# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""Censored failures at the evolution gate: how many APPLY / FORK / REJECT
verdicts are artefacts of counting budget-exhausted attempts as capability
failures, and how many survive once those observations are imputed instead?

A ``budget_exceeded`` attempt is right-censored -- the harness stopped it, the
agent did not fail it. The variant-pool gate (``experiments/variant_pool/gate.py``)
never sees that distinction: its seesaw keys on a pass@2 after-state
``(n_pass, n_att)`` with no exit reasons, so a task the agent was merely not given
room to finish is counted identically to one it cannot do. Hutter et al.
(arXiv:1310.1947) name this "treat-as-uncensored" and show it is biased in one
direction while dropping the censored points is biased in the other; the standard
repair in algorithm configuration is model-based imputation of the censored
outcome (Schmee & Hahn 1979). This script ports that repair to the gate's
improved/regressed arithmetic and asks how many recorded verdicts move.

THE DATA LIMITATION -- stated once, honoured throughout.
The gate's own rollout is NOT persisted with exit reasons; its ``evaluation``
block carries aggregate ``(n_pass, n_att)`` only. The only per-attempt
``exit_reason`` on disk is the settled deployed-pool measurement in
``comparison.json`` (``measurement_scope == settled_active_pool``) -- a DIFFERENT
rollout of the same task in the same round. Every censoring quantity below is
therefore read from the settled pool and used as a PROXY for the gate rollout.
Section 2 validates that proxy before Section 3 leans on it; Section 1 is
explicitly an association between two rollouts, not a measurement of the gate's
own attempts.

THE ESTIMATOR. Per task ``t``, from settled-pool attempts in cap-20 cells,
``P(censored | failed)_t`` is the share of failed attempts that hit the budget.
A failed attempt is imputed to a latent pass with probability
``P(censored | failed)_t * r``, where ``r = P(an attempt still running at the cap
would eventually pass)``. A cell's after-state becomes a latent pass if any of its
attempts latently passes. A regression whose after-state turns into a latent pass
is CANCELLED (the conservative variant only ever removes regressions, because the
per-variant ``before`` is not recoverable from disk); reported separately, a
never-solved task so imputed becomes an IMPROVEMENT (the less conservative
variant). The two variants are reported side by side and never averaged.

THE IDENTIFYING ASSUMPTION. ``r`` is a counterfactual and is only weakly
identified here. The sole attempts in the whole corpus allowed to run past the
binding step-20 cap are the 49 in ``s1k8b103`` R1 (which ran at cap 40), 25 of
which passed, giving ``r_hat = 25/49``. Those 49 share one ``(run, round)`` so the
true uncertainty is wider than a Wilson interval admits, and the cap is confounded
with the round. ``r`` is treated as a sensitivity parameter throughout (Section 4),
never as a settled point value.

Read-only. stdlib only. Zero API cost. Every number is computed in this run except
the bed's 9.03pp minimum detectable effect, imported from ``r_effective_n.py`` and
labelled as such.

Usage:  python experiments/analysis/novelty/t_censored_gate.py [run ...]
"""
from __future__ import annotations

import json
import math
import random
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import _common as C  # noqa: E402
import i_gate_noise_replay as I  # noqa: E402
import n_answer_selection as NA  # noqa: E402

# --------------------------------------------------------------------------- #
# constants (only MDE_PP is imported; everything else is computed in this run)
# --------------------------------------------------------------------------- #
Z95 = 1.959964            # 95% normal quantile, for Wilson score intervals
MIN_FORK = (1, 1)         # gate.py DEFAULT_MIN_FORK -- printed and assumed below
CAP_BIND = 20             # the step cap binding every (run,round) except s1k8b103 R1
MIN_FAIL = 5              # per-task P(cens|fail) needs >=5 failed obs, else pooled
M = 2000                  # multiple-imputation Bernoulli replicates
SEED = 0                  # all randomness is seeded from random.Random(SEED)
BED = 103                 # GAIA bed size the final-score bound is taken against
MDE_PP = 9.03             # IMPORTED from r_effective_n.py (MDE @80% power, 77-task swing set)


def banner(title: str) -> None:
    print("=" * 74)
    print(title)
    print("=" * 74)


# --------------------------------------------------------------------------- #
# gate.py::_decide, reproduced verbatim (see experiments/variant_pool/gate.py)
# --------------------------------------------------------------------------- #
def decide(improved, regressed, min_fork=MIN_FORK) -> str:
    """APPLY / FORK / REJECT exactly as ``gate.py::_decide`` (returns a str).

    REJECT if improved empty; APPLY if improved non-empty and regressed empty;
    FORK if ``len(improved) >= min_improve and len(regressed) >= min_regress``;
    else REJECT. With ``min_fork=(1,1)`` the final REJECT branch is unreachable.
    """
    min_improve, min_regress = min_fork
    if not improved:
        return "REJECT"
    if not regressed:
        return "APPLY"
    if len(improved) >= min_improve and len(regressed) >= min_regress:
        return "FORK"
    return "REJECT"


def wilson(k: int, n: int, z: float = Z95):
    """(p_hat, lo, hi) Wilson score interval; (nan, nan, nan) when n == 0."""
    if n <= 0:
        return (float("nan"), float("nan"), float("nan"))
    p = k / n
    denom = 1.0 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = (z / denom) * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (p, center - half, center + half)


def pearson(xs, ys) -> float:
    n = len(xs)
    if n < 2:
        return float("nan")
    mx, my = sum(xs) / n, sum(ys) / n
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    return sxy / math.sqrt(sxx * syy) if sxx > 0 and syy > 0 else float("nan")


def pct(sorted_vals, q: float) -> float:
    if not sorted_vals:
        return float("nan")
    idx = min(int(q * len(sorted_vals)), len(sorted_vals) - 1)
    return sorted_vals[idx]


# --------------------------------------------------------------------------- #
# settled deployed-pool censoring (the proxy) -- comparison.json, one pass/run
# --------------------------------------------------------------------------- #
def load_settled(runs):
    """Read every settled-pool record once.

    Returns ``(cells, caps)`` where
      ``cells[(run, rnd, task)] = {atts:[(exit,passed,steps)], cens_any, cens_all,
      budget_exhaustions}`` and
      ``caps[(run, rnd)] = max steps among that cell's budget_exceeded attempts``.
    The cap is a property of ``(run, round)``, not of the run.
    """
    cells: dict = {}
    caps: dict = {}
    scopes = Counter()
    for run in runs:
        f = NA.RUNS / run / "comparison.json"
        if not f.exists():
            continue
        d = json.loads(f.read_text(encoding="utf-8"))
        for rnd_list in d.get("rounds") or []:
            for rec in (rnd_list or []):
                scopes[rec.get("measurement_scope")] += 1
                rnd = rec.get("round")
                task = str(rec.get("task_id"))
                atts = []
                for a in (rec.get("attempts") or []):
                    ex = a.get("exit_reason")
                    steps = a.get("steps")
                    atts.append((ex, bool(a.get("passed")), steps))
                    if ex == "budget_exceeded" and steps is not None:
                        ck = (run, rnd)
                        caps[ck] = max(caps.get(ck, 0), int(steps))
                if not atts:
                    continue
                slot = cells.setdefault(
                    (run, rnd, task),
                    {"atts": [], "budget_exhaustions": rec.get("budget_exhaustions")},
                )
                slot["atts"].extend(atts)
    for slot in cells.values():
        exits = [e for (e, _p, _s) in slot["atts"]]
        slot["cens_any"] = any(e == "budget_exceeded" for e in exits)
        slot["cens_all"] = bool(exits) and all(e == "budget_exceeded" for e in exits)
    return cells, caps, scopes


def per_task_censoring(cells, caps):
    """P(censored | failed) per task from cap-20 cells, plus the pooled fallback.

    A *failed* attempt is ``passed == False`` (a budget_exceeded attempt that was
    nonetheless graded pass is a pass, not a censored failure). A *censored
    failure* is ``exit == budget_exceeded and not passed``.
    """
    per: dict = defaultdict(lambda: {"n": 0, "kpass": 0, "kcens": 0})
    pooled = {"failed": 0, "kcens": 0}
    for (run, rnd, _task), slot in cells.items():
        if caps.get((run, rnd)) != CAP_BIND:
            continue
        d = per[_task]
        for (ex, passed, _s) in slot["atts"]:
            d["n"] += 1
            if passed:
                d["kpass"] += 1
            else:
                pooled["failed"] += 1
                if ex == "budget_exceeded":
                    d["kcens"] += 1
                    pooled["kcens"] += 1
    pooled_rate = pooled["kcens"] / pooled["failed"] if pooled["failed"] else 0.0
    return dict(per), pooled_rate, pooled


def pcf(task, per, pooled_rate):
    """Base ``P(censored | failed)_t`` (unscaled by r) and its provenance tag."""
    d = per.get(task)
    if d is None:
        return pooled_rate, "pooled:no-cap20-cell"
    failed = d["n"] - d["kpass"]
    if failed < MIN_FAIL:
        return pooled_rate, f"pooled:failed={failed}<{MIN_FAIL}"
    return d["kcens"] / failed, f"pertask:failed={failed}"


# --------------------------------------------------------------------------- #
# gather every recorded seesaw decision (reuse I.seesaw_decisions verbatim)
# --------------------------------------------------------------------------- #
def gather(runs):
    """All seesaw candidates across ``runs``; annotate run, forced flag, recon.

    ``forced`` marks a FORCED_GATE probe whose recorded decision overrides the
    seesaw arithmetic (``archive_reason`` carries ``FORCED_GATE(...)`` and a
    ``real_decision=``). Such a candidate's improved/regressed are synthesized, so
    it is excluded from every counterfactual below; it is reported, not hidden.
    """
    decisions = []
    states_cache = {}
    for run in runs:
        try:
            states = C.all_states(run)
        except FileNotFoundError:
            continue
        if not states:
            continue
        states_cache[run] = states
        reasons = {}
        for _r, st in states.items():
            for cid, info in (st.get("candidate_diagnostics") or {}).items():
                reasons[cid] = info.get("archive_reason") or ""
        for d in I.seesaw_decisions(states):
            reason = reasons.get(d["cid"], "")
            d["run"] = run
            d["key"] = (run, d["round"], d["cid"])
            d["forced"] = "FORCED_GATE" in reason
            m = re.search(r"real_decision=(\w+)", reason)
            d["real_decision"] = m.group(1).upper() if m else None
            d["recorded"] = (d["decision"] or "").upper()
            d["recon"] = decide(d["improved"], d["regressed"])
            decisions.append(d)
    return decisions, states_cache


def prep_candidate(dec, per, pooled_rate):
    """Attach imputation scaffolding: base improved/regressed sets and, for every
    after-0 task in T_k, its (task, n_att, base P(cens|fail), in_regressed)."""
    reg = set(dec["regressed"])
    imp = set(dec["improved"])
    after0 = []
    for t, (npass, natt) in dec["after"].items():
        if npass == 0:
            q0, _src = pcf(t, per, pooled_rate)
            after0.append((t, int(natt) if natt else C.PASS_K, q0, t in reg))
    dec["_imp"] = imp
    dec["_reg"] = reg
    dec["_after0"] = after0
    dec["_O"] = [t for (t, _na, _q, inreg) in after0 if not inreg]  # never-in-reg after0


# --------------------------------------------------------------------------- #
# (1) OBSERVABLE DIAGNOSIS -- no model
# --------------------------------------------------------------------------- #
def section1(analysis, forced, cells):
    banner("(1) OBSERVABLE DIAGNOSIS -- gate verdicts vs recorded censoring")
    total = len(analysis) + len(forced)
    print(f"  seesaw candidates in corpus         : {total}   <-- SMALL n; carried to the verdict")
    print(f"  usable (seesaw-arithmetic) candidates: {len(analysis)}")
    print(f"  excluded FORCED_GATE probes         : {len(forced)}"
          + (f"  ({', '.join('%s/%s(real=%s)' % (d['run'], d['cid'], d['real_decision']) for d in forced)})"
             if forced else ""))
    runs_in = sorted({d["run"] for d in analysis})
    print(f"  runs contributing usable candidates : {len(runs_in)}  {runs_in}")
    print("\n  ASSUMPTION (binds here): the settled deployed-pool rollout is used as a")
    print("  PROXY for the gate's own rollout. This section is an ASSOCIATION between two")
    print("  different rollouts of the same task in the same (run,round) -- NOT a")
    print("  measurement of the gate's own attempts, which carry no exit reasons on disk.")
    print("  'censored' here = >=1 settled attempt exited budget_exceeded (pass or fail).")

    groups = ("regressed", "improved", "other")
    look = {g: 0 for g in groups}
    cany = {g: 0 for g in groups}
    call = {g: 0 for g in groups}
    nomatch = {g: 0 for g in groups}
    for d in analysis:
        run, rnd = d["run"], d["round"]
        Tk = set(d["after"].keys())
        Rset = set(d["regressed"]) & Tk
        Iset = set(d["improved"]) & Tk
        Oset = Tk - Rset - Iset
        for g, tasks in (("regressed", Rset), ("improved", Iset), ("other", Oset)):
            for t in tasks:
                slot = cells.get((run, rnd, str(t)))
                if slot is None:
                    nomatch[g] += 1
                    continue
                look[g] += 1
                cany[g] += 1 if slot["cens_any"] else 0
                call[g] += 1 if slot["cens_all"] else 0

    print(f"\n  {'group':<10}{'tasks':>7}{'w/ measure':>11}{'>=1 cens':>10}{'rate':>8}"
          f"{'  95% Wilson':>18}{'all cens':>10}{'rate':>8}")
    rate_any = {}
    for g in groups:
        n = look[g]
        pa, la, ha = wilson(cany[g], n)
        pl, ll, hl = wilson(call[g], n)
        rate_any[g] = (pa, la, ha, n)
        tot_g = n + nomatch[g]
        print(f"  {g:<10}{tot_g:>7}{n:>11}{cany[g]:>10}"
              f"{(pa*100 if n else float('nan')):>7.1f}%"
              f"   [{la*100:5.1f},{ha*100:5.1f}]"
              f"{call[g]:>10}{(pl*100 if n else float('nan')):>7.1f}%")
    if any(nomatch[g] for g in groups):
        print("  (no same-round settled measurement -> excluded from the rates above: "
              + ", ".join(f"{g}={nomatch[g]}" for g in groups if nomatch[g]) + ")")

    pr, prl, prh, prn = rate_any["regressed"]
    br, brl, brh, brn = rate_any["other"]
    print("\n  DIFFERENTIAL (>=1-censored rate):")
    diff_pp = None
    non_overlap = False
    if prn and brn:
        diff_pp = (pr - br) * 100
        print(f"    regressed {pr*100:.1f}% [{prl*100:.1f},{prh*100:.1f}]  minus  "
              f"base/other {br*100:.1f}% [{brl*100:.1f},{brh*100:.1f}]  =  {diff_pp:+.1f}pp")
        non_overlap = (prl > brh or brl > prh)
        print("    the two Wilson intervals " + ("do NOT overlap" if non_overlap else "OVERLAP")
              + " -- the association is "
              + ("supported at 95%" if non_overlap else "not resolved at 95%")
              + " on this small n.")
    else:
        print("    insufficient measured tasks in one arm -- differential undefined.")
    print("  READ: regressed tasks being more often censored in the parallel settled")
    print("  rollout is suggestive that the gate's own regressed flags include censored")
    print("  failures; it is an association only, and Section 3 is where a decision moves.")
    return diff_pp, non_overlap


# --------------------------------------------------------------------------- #
# (2) THE CENSORING MODEL
# --------------------------------------------------------------------------- #
def section2(analysis, cells, caps, per, pooled_rate, pooled, r_hat_pack):
    banner("(2) THE CENSORING MODEL  P(censored|failed)_t  and the residual r")
    ncap20 = sum(1 for k, s in cells.items() if caps.get((k[0], k[1])) == CAP_BIND)
    print(f"  fitted on settled cap-{CAP_BIND} cells only: {ncap20} cells, "
          f"{pooled['failed']} failed attempts, {pooled['kcens']} of them censored.")
    print(f"  pooled fallback P(censored|failed) = {pooled['kcens']}/{pooled['failed']} "
          f"= {pooled_rate:.3f}")
    print(f"  per-task estimate used when a task has >= {MIN_FAIL} failed attempts, else the")
    print(f"  pooled rate. Rationale: with < {MIN_FAIL} failures the ratio's standard error")
    print("  exceeds ~0.22 (one attempt moves it >=0.2), larger than the between-task")
    print("  spread, so pooling is the lower-MSE choice.")
    # distribution of per-task rates (tasks meeting the per-task threshold)
    strong = [(d["kcens"] / (d["n"] - d["kpass"]))
              for d in per.values() if (d["n"] - d["kpass"]) >= MIN_FAIL]
    if strong:
        strong.sort()
        print(f"  per-task rate (>= {MIN_FAIL} failures, n={len(strong)} tasks): "
              f"min={strong[0]:.2f} median={strong[len(strong)//2]:.2f} max={strong[-1]:.2f}")

    # ---- proxy validation ------------------------------------------------- #
    print("\n  PROXY VALIDATION -- settled-pool failure rate vs the gate's own after-state")
    print("  failure rate, over tasks present in both (the model is fitted on the settled")
    print("  rollout but applied to the gate rollout, so this must be checked, not assumed):")
    settled_rate = {t: 1.0 - d["kpass"] / d["n"] for t, d in per.items() if d["n"] > 0}
    gate_fa = defaultdict(lambda: [0, 0])
    for d in analysis:
        for t, (npass, natt) in d["after"].items():
            gate_fa[str(t)][0] += (natt - npass)
            gate_fa[str(t)][1] += natt
    gate_rate = {t: fa[0] / fa[1] for t, fa in gate_fa.items() if fa[1] > 0}
    common = sorted(set(settled_rate) & set(gate_rate))
    xs = [settled_rate[t] for t in common]
    ys = [gate_rate[t] for t in common]
    r = pearson(xs, ys)
    mad = sum(abs(a - b) for a, b in zip(xs, ys)) / len(common) if common else float("nan")
    print(f"    tasks in both rollouts: {len(common)}   Pearson r = {r:.3f}   "
          f"mean |settled - gate| = {mad:.3f}")
    holds = (not math.isnan(r)) and r >= 0.5 and mad <= 0.15
    if holds:
        print("    PROXY HOLDS (r>=0.5 and MAD<=0.15): the settled rollout tracks the gate")
        print("    rollout closely enough to carry the censoring model. Section 3 proceeds.")
    else:
        print("    ** THREAT TO VALIDITY: the proxy is WEAK by the pre-registered bar")
        print("       (r>=0.5 and MAD<=0.15). The settled rollout is a coarse stand-in for")
        print("       the gate rollout (gate after-state is pass@2, a 3-level rate), so the")
        print("       imputation in Section 3 inherits this slack. Carried to the verdict;")
        print("       the results below are NOT presented as settled. **")

    # ---- the residual r --------------------------------------------------- #
    past_n, past_pass, past_cells = r_hat_pack
    p, lo, hi = wilson(past_pass, past_n)
    print(f"\n  RESIDUAL r = P(an attempt still running at the cap would eventually pass).")
    print(f"    identifying observation: attempts that ran PAST step {CAP_BIND} across the whole")
    print(f"    corpus = {past_n}, of which {past_pass} passed  ->  r_hat = {past_pass}/{past_n} "
          f"= {p:.4f}")
    print(f"    Wilson 95% on r_hat: [{lo:.4f}, {hi:.4f}]")
    print(f"    ALL {past_n} come from a single (run,round) = {sorted(past_cells)} -- the true")
    print("    uncertainty is WIDER than Wilson admits, and the cap (40) is confounded with")
    print("    the round (R1) here. ASSUMPTION (binds Section 4): r is identified from one")
    print("    (run,round). r is a SENSITIVITY parameter, never a settled point value.")
    return p, lo, hi


# --------------------------------------------------------------------------- #
# (3) THREE ESTIMATORS, SAME DECISIONS
# --------------------------------------------------------------------------- #
def impute_variants(dec, latent):
    """(decA, decB) for one candidate given a latent-pass map over its after-0 tasks.

    A (conservative): regressed loses any latent-pass task; improved unchanged.
    B (less conservative): additionally, a never-in-regressed after-0 task that
    latently passes cannot have been ever-solved, so its before is 0 and it becomes
    an improvement.
    """
    regA = {t for t in dec["_reg"] if not latent.get(t, False)}
    decA = decide(dec["_imp"], regA)
    impB = set(dec["_imp"]) | {t for t in dec["_O"] if latent.get(t, False)}
    decB = decide(impB, regA)
    return decA, decB


def section3(analysis, cells, per, pooled_rate, r_hat):
    banner("(3) THREE ESTIMATORS, SAME DECISIONS")
    print("  Each estimator re-runs gate.py::_decide on every recorded seesaw decision.")
    print("  min_fork assumed = (1, 1)  [gate.py DEFAULT_MIN_FORK]; with it, REJECT<=>improved")
    print("  empty, APPLY<=>improved non-empty & regressed empty, FORK<=>both non-empty.")

    # ---- estimator 1: treat-as-uncensored (status quo) -------------------- #
    print("\n  [1] TREAT-AS-UNCENSORED (status quo). Reconstructing gate.py::_decide on the")
    print("      recorded improved/regressed must reproduce the recorded verdict exactly.")
    bad = [d for d in analysis if d["recon"] != d["recorded"]]
    for d in bad:
        print(f"      !! {d['run']}/{d['cid']} recon={d['recon']} recorded={d['recorded']}")
    assert not bad, "status-quo reconstruction does not match recorded decisions -- broken"
    print(f"      OK: all {len(analysis)} usable candidates reproduce "
          f"({Counter(d['recorded'] for d in analysis)}).")

    # ---- estimator 2: listwise deletion ----------------------------------- #
    print("\n  [2] LISTWISE DELETION. A task whose settled proxy cell is FULLY censored is")
    print("      dropped as unevaluated -> removed from BOTH improved and regressed, then")
    print("      re-decided. (Applied at task granularity via the proxy, because the gate")
    print("      rollout has no per-attempt exit reasons to drop.)")
    confLD = Counter()
    ld_rows = []
    for d in analysis:
        drop = set()
        for t in set(d["improved"]) | set(d["regressed"]):
            slot = cells.get((d["run"], d["round"], str(t)))
            if slot is not None and slot["cens_all"]:
                drop.add(t)
        impLD = set(d["improved"]) - drop
        regLD = set(d["regressed"]) - drop
        decLD = decide(impLD, regLD)
        confLD[(d["recorded"], decLD)] += 1
        if decLD != d["recorded"]:
            ld_rows.append((d["run"], d["cid"], d["recorded"], decLD, len(drop)))
    nchg = sum(v for (a, b), v in confLD.items() if a != b)
    print(f"      RESULT: {nchg} of {len(analysis)} candidates change decision under listwise")
    print("      deletion (deterministic -- no r, no draws).")
    for run, cid, rec, new, ndrop in ld_rows:
        print(f"        {run}/{cid}: {rec} -> {new}  ({ndrop} fully-censored task(s) dropped)")
    if nchg:
        print("      directions: "
              + ", ".join(f"{a}->{b}={confLD[(a, b)]}"
                          for (a, b) in sorted(confLD) if a != b))
    print("      Hutter et al.: treat-as-uncensored [1] and listwise deletion are biased in")
    print("      OPPOSITE directions. At the gate the drop is not monotone -- dropping an all-")
    print("      censored REGRESSION lifts a veto (-> APPLY) while dropping an all-censored")
    print("      IMPROVEMENT removes a gain (-> REJECT); both pull against the [1] counts.")

    # ---- estimator 3: Schmee-Hahn imputation (multiple imputation) -------- #
    print("\n  [3] SCHMEE-HAHN IMPUTATION (multiple imputation). Reported below as two")
    print("      variants, side by side, never averaged.")
    print("      ASSUMPTION (binds here): per-attempt independence within a cell.")
    print(f"      M={M} seeded Bernoulli replicates, random.Random({SEED}), at r_hat={r_hat:.4f}.")
    print("      Every after-0 attempt latently passes with probability")
    print("      P(censored|failed)_t * r, drawn independently; a cell is a latent pass if any")
    print("      of its attempts is. Variant A cancels regressions only; variant B additionally")
    print("      promotes an imputed never-solved task to an improvement.")


def run_mi(analysis, r_hat):
    """M seeded replicates. Returns per-candidate posteriors and per-replicate,
    per-direction flip-count series for both variants."""
    rng = random.Random(SEED)
    postA = {d["key"]: Counter() for d in analysis}
    postB = {d["key"]: Counter() for d in analysis}
    changedA = [0] * M
    changedB = [0] * M
    dirA = defaultdict(lambda: [0] * M)
    dirB = defaultdict(lambda: [0] * M)
    for m in range(M):
        cA = Counter()
        cB = Counter()
        for d in analysis:
            latent = {}
            for (t, natt, q0, _inreg) in d["_after0"]:
                q = q0 * r_hat
                lp = False
                for _ in range(natt):
                    if rng.random() < q:
                        lp = True
                latent[t] = lp
            decA, decB = impute_variants(d, latent)
            rec = d["recorded"]
            postA[d["key"]][decA] += 1
            postB[d["key"]][decB] += 1
            if decA != rec:
                changedA[m] += 1
                cA[(rec, decA)] += 1
            if decB != rec:
                changedB[m] += 1
                cB[(rec, decB)] += 1
        for k, v in cA.items():
            dirA[k][m] = v
        for k, v in cB.items():
            dirB[k][m] = v
    return postA, postB, changedA, changedB, dict(dirA), dict(dirB)


def report_mi(analysis, postA, postB, changedA, changedB, dirA, dirB):
    # per-candidate posteriors, only for candidates that can move under either variant
    movers = [d for d in analysis
              if len(postA[d["key"]]) > 1 or len(postB[d["key"]]) > 1]
    print(f"\n  per-candidate posterior decision share over M={M} replicates "
          f"(only the {len(movers)} candidates that ever move; the other "
          f"{len(analysis) - len(movers)} are fixed):")
    if movers:
        print(f"    {'run':>10} {'R':>2} {'cid':>11} {'rec':>6} "
              f"{'A:APP/FRK/REJ':>16} {'B:APP/FRK/REJ':>16}")
        for d in sorted(movers, key=lambda x: (x["run"], x["round"])):
            a, b = postA[d["key"]], postB[d["key"]]

            def sh(c):
                return "%.2f/%.2f/%.2f" % (c.get("APPLY", 0) / M, c.get("FORK", 0) / M,
                                           c.get("REJECT", 0) / M)
            print(f"    {d['run']:>10} {d['round']:>2} {d['cid']:>11} {d['recorded']:>6} "
                  f"{sh(a):>16} {sh(b):>16}")
    else:
        print("    none -- no candidate moves under either variant at this r.")

    for name, changed, dirs in (("A (conservative: cancels regressions)", changedA, dirA),
                                ("B (less conservative: + never-solved promotions)", changedB, dirB)):
        cs = sorted(changed)
        print(f"\n  VARIANT {name}")
        print(f"    candidates changing decision per replicate: "
              f"mean={sum(changed)/M:.2f}  median={pct(cs,0.5):.0f}  "
              f"95% pctile [{pct(cs,0.025):.0f}, {pct(cs,0.975):.0f}]")
        if dirs:
            for direction in sorted(dirs, key=lambda k: -sum(dirs[k])):
                s = sorted(dirs[direction])
                frm, to = direction
                print(f"      {frm}->{to:<7} mean={sum(s)/M:.2f}  "
                      f"95% pctile [{pct(s,0.025):.0f}, {pct(s,0.975):.0f}]")
        else:
            print("      no direction ever fires -- zero flips in every replicate.")


# --------------------------------------------------------------------------- #
# (4) SENSITIVITY IN r
# --------------------------------------------------------------------------- #
def expected_flips(analysis, per, pooled_rate, r):
    """Analytic expected number of flipped decisions at residual r, for both
    variants (independent-cell arithmetic; matches the MI in expectation)."""
    EA = 0.0
    EB = 0.0
    for d in analysis:
        pmap = {}
        for (t, natt, q0, _inreg) in d["_after0"]:
            q = q0 * r
            pmap[t] = 1.0 - (1.0 - q) ** natt  # cell latent-pass probability
        rec = d["recorded"]
        if rec == "FORK":
            pclear = 1.0
            for t in d["_reg"]:
                pclear *= pmap.get(t, 0.0)
            EA += pclear          # FORK -> APPLY when all regressions cancel
            EB += pclear
        elif rec == "REJECT":
            pno = 1.0
            for t in d["_O"]:
                pno *= (1.0 - pmap.get(t, 0.0))
            EB += (1.0 - pno)     # REJECT -> APPLY/FORK when any never-solved task is promoted
        # APPLY contributes 0 to both (regressed stays empty; improved stays non-empty)
    return EA, EB


def section4(analysis, per, pooled_rate, r_hat, r_lo, r_hi):
    banner("(4) SENSITIVITY IN r  (expected flipped decisions vs r)")
    e0 = expected_flips(analysis, per, pooled_rate, 0.0)
    assert abs(e0[0]) < 1e-12 and abs(e0[1]) < 1e-12, "r=0 must give zero flips"
    print("  sanity check PASSED: r=0 gives exactly 0 expected flips for both variants.")
    grid = [i / 20 for i in range(21)]
    marks = {round(r_hat, 4): "r_hat", round(r_lo, 4): "r_lo(Wilson)", round(r_hi, 4): "r_hi(Wilson)"}
    rows = sorted(set(grid) | set(marks))
    maxE = max(max(expected_flips(analysis, per, pooled_rate, r)) for r in rows) or 1.0
    print(f"\n  {'r':>6} {'E[flip]A':>9} {'E[flip]B':>9}  {'bar (B; scaled to max)':<34} mark")
    first_A = first_B = None
    for r in rows:
        ea, eb = expected_flips(analysis, per, pooled_rate, r)
        if first_A is None and ea >= 1.0:
            first_A = r
        if first_B is None and eb >= 1.0:
            first_B = r
        bar = "#" * int(round(eb / maxE * 32))
        mk = marks.get(round(r, 4), "")
        print(f"  {r:>6.4f} {ea:>9.3f} {eb:>9.3f}  {bar:<34} {mk}")
    print("\n  'first flip' = smallest r whose EXPECTED flip count reaches 1.0.")
    print(f"    variant A (conservative)     : "
          + (f"r = {first_A:.4f}" if first_A is not None else "NEVER up to r=1.0"))
    print(f"    variant B (less conservative): "
          + (f"r = {first_B:.4f}" if first_B is not None else "NEVER up to r=1.0"))
    ea_hat, eb_hat = expected_flips(analysis, per, pooled_rate, r_hat)
    print(f"    at r_hat={r_hat:.4f}: E[flip]A = {ea_hat:.3f},  E[flip]B = {eb_hat:.3f}")
    if first_A is None:
        print("\n  HEADLINE: under the conservative variant NO value of r up to 1.0 is expected")
        print("  to flip a single decision. Censoring cannot cancel enough of any FORK's")
        print("  regressed set (they are too large) to turn it into an APPLY.")
    else:
        print("\n  Note: below these r the flips still occur in a minority of imputations")
        print("  (E>0 for all r>0); the threshold above is where a flip becomes EXPECTED.")
    return ea_hat, eb_hat, first_A, first_B


# --------------------------------------------------------------------------- #
# (5) SO WHAT
# --------------------------------------------------------------------------- #
def ever_recovered_later(states, task, after_round):
    for r in sorted(states):
        if r <= after_round:
            continue
        for _vid, tasks in (states[r].get("active_pool_measurements") or {}).items():
            sa = tasks.get(task)
            if sa and int(sa[0]) >= 1:
                return True
    return False


def section5(analysis, states_cache, postA, postB):
    banner("(5) SO WHAT")
    # REJECT -> APPLY/FORK flips (variant B only; variant A never grows improved)
    rej_flips = [d for d in analysis
                 if d["recorded"] == "REJECT"
                 and (postB[d["key"]].get("APPLY", 0) + postB[d["key"]].get("FORK", 0)) > 0]
    fork_flips_A = [d for d in analysis
                    if d["recorded"] == "FORK" and postA[d["key"]].get("APPLY", 0) > 0]

    print("  Variant A (conservative) produces only FORK->APPLY moves; it never turns a")
    print("  REJECT into an APPLY/FORK, because it cannot manufacture an improvement -- so")
    print("  it discards NO improved task on account of censoring.")
    print(f"    FORK->APPLY candidates with any posterior mass under A: {len(fork_flips_A)}")
    for d in fork_flips_A:
        p = postA[d["key"]].get("APPLY", 0) / M
        print(f"      {d['run']}/{d['cid']}  P(FORK->APPLY)={p:.3f}  "
              f"(edit would ship pool-wide instead of being quarantined to a fork; no")
        print("         improvement is discarded either way)")

    print(f"\n  REJECT->APPLY/FORK flips (variant B, via imputed never-solved tasks): "
          f"{len(rej_flips)}")
    if not rej_flips:
        print("    none.")
    by_run_never = defaultdict(int)
    by_run_imp = defaultdict(int)
    for d in rej_flips:
        run = d["run"]
        states = states_cache[run]
        pflip = (postB[d["key"]].get("APPLY", 0) + postB[d["key"]].get("FORK", 0)) / M
        # the imputed improvements at risk = this candidate's never-in-reg after-0 tasks
        imputed = list(d["_O"])
        never = [t for t in imputed if not ever_recovered_later(states, str(t), d["round"])]
        by_run_never[run] += len(never)
        by_run_imp[run] += len(imputed)
        print(f"    {run}/{d['cid']} (R{d['round']}): P(flip)={pflip:.3f}  "
              f"imputed improvements={len(imputed)}  never recovered later={len(never)}")
        print("       NOTE: these are IMPUTED from censored never-solved tasks (the")
        print("       aggressive variant); they are not observed passes the gate discarded.")

    print("\n  BOUND on a run's final score on the 103-task bed (per run -- a cross-run sum")
    print("  would not be any one run's score):")
    cons_bound = 0.0
    print(f"    CONSERVATIVE (variant A): 0 improvements discarded on any run -> {cons_bound:.2f}pp,")
    print(f"    trivially BELOW the {MDE_PP:.2f}pp MDE [IMPORTED from r_effective_n.py].")
    agg_bound = 0.0
    agg_run = None
    if by_run_never:
        print("    AGGRESSIVE (variant B; rests on imputing never-solved tasks):")
        print(f"      {'run':>12} {'never-recov':>12} {'bound pp':>9}   (bound = never-recov / 103)")
        for run in sorted(by_run_never, key=lambda x: -by_run_never[x]):
            b = 100.0 * by_run_never[run] / BED
            if b > agg_bound:
                agg_bound, agg_run = b, run
            print(f"      {run:>12} {by_run_never[run]:>12} {b:>8.2f}pp")
        state = "ABOVE" if agg_bound >= MDE_PP else "BELOW"
        print(f"    largest single-run bound = {agg_bound:.2f}pp ({agg_run}) -- {state} the "
              f"{MDE_PP:.2f}pp MDE, but it")
        print("    is an upper bound on a COUNTERFACTUAL: it counts never-solved tasks the model")
        print("    imputes might have passed with more budget, not passes the gate observed.")

    print("\n  READING:")
    if not rej_flips and not fork_flips_A:
        print("    The gate is INERT with respect to censoring on this corpus. This is a")
        print("    NEGATIVE result: the censoring bias is real and measurable at the")
        print("    observation level (Section 1) but does NOT propagate to the decision level.")
    else:
        print("    Censoring DOES reach the decision level, but under the conservative repair")
        print("    only as SCOPE: the FORK->APPLY moves above ship an edit pool-wide instead of")
        print("    quarantining it, and discard no improvement -- foregone throughput is 0.00pp.")
        print("    A REJECTED improvement is rescued only under the aggressive never-solved")
        print("    imputation, whose credibility is exactly what the sensitivity parameter r")
        print("    governs; its bound is reported against the bed's MDE above, not asserted.")
    return cons_bound, agg_bound, agg_run, len(rej_flips), len(fork_flips_A)


# --------------------------------------------------------------------------- #
# (6) VERDICT
# --------------------------------------------------------------------------- #
def section6(n_analysis, diff_pp, non_overlap, ea_hat, eb_hat, first_A,
             cons_bound, agg_bound, agg_run, proxy_note):
    banner("(6) VERDICT")
    ov = "non-overlapping" if non_overlap else "overlapping"
    dtxt = f"{diff_pp:+.0f}pp" if diff_pp is not None else "n/a"
    ftxt = f"first EXPECTED at r={first_A:.2f}" if first_A is not None else "never expected up to r=1"
    lines = [
        f"On {n_analysis} usable seesaw candidates (tens, not thousands -- the caveat on every claim),",
        "right-censoring is real where it is measurable: a task the gate flags 'regressed' is",
        f"censored in the parallel settled rollout {dtxt} more than the base rate, Wilson intervals",
        f"{ov} (Section 1). It reaches the decision level only as SCOPE -- the conservative repair",
        f"flips ~{ea_hat:.1f} verdicts per imputation at r_hat ({ftxt}), all FORK->APPLY, rescuing no",
        f"rejected improvement, so its foregone-throughput bound is {cons_bound:.2f}pp, below the "
        f"{MDE_PP:.2f}pp",
        f"MDE [imported from r_effective_n.py]. A rejected improvement moves only under the aggressive",
        f"variant that imputes never-solved tasks (~{eb_hat:.0f}/imputation, up to {agg_bound:.1f}pp "
        f"in {agg_run}),",
        f"which rests on r from a single (run,round) and a settled proxy that is {proxy_note}. Sections",
        "1 (the bias exists) and 3-4 (it moves scope, not throughput) carry the argument. PORT: Schmee",
        "& Hahn 1979 for the imputation estimator, Hutter et al. arXiv:1310.1947 for the treat-as-",
        "uncensored bias; the one original move is applying it to an evolution gate's improved/",
        "regressed arithmetic rather than to runtime capping.",
    ]
    for ln in lines:
        print("  " + ln)


# --------------------------------------------------------------------------- #
def main(runs) -> None:
    banner("T. CENSORED-FAILURE AUDIT OF THE EVOLUTION GATE")
    print(f"  runs scanned (pool_report.json present): {len(runs)}")
    print(f"  seed = {SEED} (all Bernoulli imputation draws); M = {M}; min_fork = {MIN_FORK}")

    decisions, states_cache = gather(runs)
    forced = [d for d in decisions if d["forced"]]
    analysis = [d for d in decisions if not d["forced"]]

    cells, caps, scopes = load_settled(runs)
    assert set(scopes) <= {"settled_active_pool", None}, \
        f"unexpected measurement_scope values: {dict(scopes)}"
    print(f"  settled measurement_scope values seen : {dict(scopes)}  "
          f"(only settled_active_pool/absent -- the gate's own rollout is NOT here)")

    per, pooled_rate, pooled = per_task_censoring(cells, caps)
    for d in analysis:
        prep_candidate(d, per, pooled_rate)

    # identifying observation for r, computed in-run
    past_n = past_pass = 0
    past_cells = set()
    for (run, rnd, _t), slot in cells.items():
        for (_ex, passed, steps) in slot["atts"]:
            if steps is not None and int(steps) > CAP_BIND:
                past_n += 1
                past_pass += 1 if passed else 0
                past_cells.add((run, rnd))
    if past_n == 0:
        print(f"\n  NOTE: no attempt in the selected runs ran past the binding cap {CAP_BIND}; the")
        print("  residual r is identified ONLY from s1k8b103 R1, which is not in scope. The")
        print("  observable diagnosis (Section 1) still holds; the r-dependent Sections 2-6")
        print("  require that run. Re-run with the default corpus or include s1k8b103.")
        print()
        section1(analysis, forced, cells)
        return
    assert len(past_cells) == 1, \
        f"expected a single identifying (run,round) past cap {CAP_BIND}, got {sorted(past_cells)}"

    print()
    diff_pp, non_overlap = section1(analysis, forced, cells)
    print()
    r_hat, r_lo, r_hi = section2(analysis, cells, caps, per, pooled_rate, pooled,
                                 (past_n, past_pass, past_cells))
    # recompute proxy note for the verdict
    settled_rate = {t: 1.0 - d["kpass"] / d["n"] for t, d in per.items() if d["n"] > 0}
    gate_fa = defaultdict(lambda: [0, 0])
    for d in analysis:
        for t, (npass, natt) in d["after"].items():
            gate_fa[str(t)][0] += (natt - npass)
            gate_fa[str(t)][1] += natt
    gate_rate = {t: fa[0] / fa[1] for t, fa in gate_fa.items() if fa[1] > 0}
    common = sorted(set(settled_rate) & set(gate_rate))
    r_proxy = pearson([settled_rate[t] for t in common], [gate_rate[t] for t in common])
    mad_proxy = (sum(abs(settled_rate[t] - gate_rate[t]) for t in common) / len(common)
                 if common else float("nan"))
    proxy_note = (f"weak (r={r_proxy:.2f}, MAD={mad_proxy:.2f})"
                  if not ((not math.isnan(r_proxy)) and r_proxy >= 0.5 and mad_proxy <= 0.15)
                  else f"adequate (r={r_proxy:.2f}, MAD={mad_proxy:.2f})")

    print()
    section3(analysis, cells, per, pooled_rate, r_hat)
    postA, postB, changedA, changedB, dirA, dirB = run_mi(analysis, r_hat)
    report_mi(analysis, postA, postB, changedA, changedB, dirA, dirB)
    print()
    ea_hat, eb_hat, first_A, _first_B = section4(analysis, per, pooled_rate, r_hat, r_lo, r_hi)
    print()
    cons_bound, agg_bound, agg_run, _nrej, _nfork = section5(
        analysis, states_cache, postA, postB)
    print()
    section6(len(analysis), diff_pp, non_overlap, ea_hat, eb_hat, first_A,
             cons_bound, agg_bound, agg_run, proxy_note)


if __name__ == "__main__":
    argv = sys.argv[1:]
    runs = argv or [p.name for p in sorted(NA.RUNS.iterdir())
                    if (p / "pool_report.json").exists()]
    main(runs)
