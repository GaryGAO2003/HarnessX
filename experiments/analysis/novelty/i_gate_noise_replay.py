# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""Analysis I -- noise-aware gate replay: how many gate decisions are driven by
measurement noise rather than real edit effects?

Premise (verified in code, printed at run time from the lock):
  The variant-pool seesaw classifies a task as *improved* when ``before_passes==0
  and after_passes>=1`` (``gate.py:227``) and *regressed* when ``after_passes==0``
  and it was ever solved / the variant solved it before (``gate.py:229-254``).
  Both are HARD single-shot rules on 2 binary samples (pass@2). There is **no
  noise threshold** on the per-task decision in this gate path: the lock records
  ``noise_threshold=None`` while the paper's planned +/-5% sits unused in
  ``planned_noise_threshold=0.05`` (experiment_lock.py:250,254). The only place a
  noise guard is applied is the K=1 single-config kernel ``_score_and_gate``
  (``run.py:1577`` -> applied at ``run.py:1619-1620``), a round-level aggregate
  count delta -- a different code path that never touches the variant pool.

This bed flips ~23% of tasks under byte-identical re-measurement (SD 4.59pp), so a
2-sample per-task rule can flag "regressions"/"improvements" that are pure noise.
This script quantifies, offline, how many of the actual gate decisions would move
under a noise-aware test, and in which direction.

Statistical test (stated here, not hidden in code)
  H0 for a task: its true per-attempt success rate equals the POOLED estimate
  ``p_hat`` from every usable non-reuse attempt of that task across ALL rounds and
  variants (median 32 attempts for a carried task). The gate keys on pass@2, so the
  after-state is the binary event {0 passes} or {>=1 pass} in n=2 attempts. One-sided
  exact-binomial p-values (n=2, exact, no approximation):
    regressed task (observed after = 0/2): p = P(X=0 | n,p_hat) = (1-p_hat)^n
    improved  task (observed after >=1/2): p = P(X>=1| n,p_hat) = 1-(1-p_hat)^n
  A flag is a SIGNIFICANT (real) effect iff its p-value <= alpha. With n=2 and
  alpha=0.05 a regression is significant only if p_hat >= 1-sqrt(0.05) = 0.776, and
  an improvement only if p_hat <= 1-sqrt(0.95) = 0.025. alpha=0.05 primary; 0.10 shown
  as sensitivity.

Reuse (do not re-derive): the merged, reuse-excluded (round,variant,task) table and
its ``active_score_source == candidate_reuse`` exclusion come verbatim from
``h_pool_headroom.build_table`` (see 07-FORK-REUSE-CORRECTION.md). The imputed
fork-reuse-corrected R0->final gain in section (5) comes from
``f_fork_reuse_correction.main`` (captured, not reimplemented).

Read-only. stdlib only. Zero API cost.
Usage:  python experiments/analysis/novelty/i_gate_noise_replay.py [run]
"""

from __future__ import annotations

import ast
import contextlib
import io
import re
import sys
from collections import Counter, defaultdict

import _common as C
import f_fork_reuse_correction as F
import h_pool_headroom as H

ALPHA = 0.05          # primary confidence level for the exact-binomial test
ALPHA2 = 0.10         # sensitivity level
SETTLED_HI = 0.85     # p_hat >= -> settled-pass
SETTLED_LO = 0.15     # p_hat <= -> settled-fail
LOW_P = 0.30          # "entered ever_solved by luck" threshold (part 4)

# regression significant iff (1-p)^n <= alpha  <=>  p >= 1-alpha^(1/n)
def reg_sig_threshold(n: int = C.PASS_K, alpha: float = ALPHA) -> float:
    return 1.0 - alpha ** (1.0 / n)


# improvement significant iff 1-(1-p)^n <= alpha  <=>  p <= 1-(1-alpha)^(1/n)
def imp_sig_threshold(n: int = C.PASS_K, alpha: float = ALPHA) -> float:
    return 1.0 - (1.0 - alpha) ** (1.0 / n)


def p_after_zero(p_hat: float, n: int) -> float:
    """Exact P(0 passes in n attempts | rate p_hat) -- the regressed-flag p-value."""
    return (1.0 - p_hat) ** n


def p_after_pass(p_hat: float, n: int) -> float:
    """Exact P(>=1 pass in n attempts | rate p_hat) -- the improved-flag p-value."""
    return 1.0 - (1.0 - p_hat) ** n


# --------------------------------------------------------------------------- #
# pooled per-task p_hat (reuses the blessed reuse-excluded table)
# --------------------------------------------------------------------------- #
def pooled_phat(states):
    """Return (phat, att, pool) pooling every usable non-reuse attempt per task.

    ``pool[t] = [succ, att]`` summed over the merged (round,variant,task) table
    from ``h_pool_headroom.build_table`` -- i.e. the same reuse-cell exclusion and
    same-variant de-dup used by analysis H. ``phat[t] = succ/att``.
    """
    table, prov = H.build_table(states)
    pool = defaultdict(lambda: [0, 0])
    for r in table:
        for t, vmap in table[r].items():
            for _v, (_src, succ, at) in vmap.items():
                pool[t][0] += succ
                pool[t][1] += at
    phat = {t: (sa[0] / sa[1] if sa[1] else 0.0) for t, sa in pool.items()}
    att = {t: sa[1] for t, sa in pool.items()}
    return phat, att, dict(pool), prov


# --------------------------------------------------------------------------- #
# reconstruct the seesaw decisions verbatim from candidate_diagnostics
# --------------------------------------------------------------------------- #
_LIST_RE = {
    "improved": re.compile(r"improved=(\[[^\]]*\])"),
    "regressed": re.compile(r"regressed=(\[[^\]]*\])"),
}


def _parse_list(reason: str, key: str):
    m = _LIST_RE[key].search(reason or "")
    if not m:
        return []
    try:
        return list(ast.literal_eval(m.group(1)))
    except (ValueError, SyntaxError):
        return []


def seesaw_decisions(states):
    """Every candidate that reached the seesaw, with its verbatim improved/regressed.

    A candidate reached the seesaw iff its ``archive_reason`` carries an
    ``improved=`` list (FORK/APPLY/SEESAW_REGRESSION all do; pre-seesaw stage
    failures -- ROUNDTRIP_L2 etc. -- do not). ``after[t] = (succ, att)`` is read
    from the candidate's own gate ``evaluation`` block.
    Returns list of dicts sorted by round.
    """
    out = []
    for r in sorted(states):
        st = states[r]
        cd = st.get("candidate_diagnostics") or {}
        for cid, info in cd.items():
            reason = info.get("archive_reason") or ""
            if "improved=" not in reason:
                continue  # never reached the seesaw
            decision = (info.get("decision") or "").lower()
            evaluation = info.get("evaluation") or {}
            after = {t: (int(v[0]), int(v[1])) for t, v in evaluation.items()
                     if isinstance(v, (list, tuple)) and len(v) == 2}
            out.append({
                "round": r,
                "cid": cid,
                "variant": info.get("variant_id"),
                "decision": decision,
                "improved": _parse_list(reason, "improved"),
                "regressed": _parse_list(reason, "regressed"),
                "after": after,
            })
    return out


def _n_after(dec, task):
    sa = dec["after"].get(task)
    n = sa[1] if sa else C.PASS_K
    return n if n else C.PASS_K


# --------------------------------------------------------------------------- #
# the three gate rules (stated explicitly)
# --------------------------------------------------------------------------- #
def rule_H(imp, reg):
    """Current hard seesaw with min_fork=(1,1)."""
    if not imp:
        return "REJECT"
    if not reg:
        return "APPLY"
    return "FORK"


def rule_N(imp, reg_sig):
    """Noise-tolerant seesaw: a task counts as regressed only if its 0/2 after-state
    is significant under H0 (exact binomial, p<=alpha). Improved side unchanged."""
    if not imp:
        return "REJECT"
    if not reg_sig:
        return "APPLY"
    return "FORK"


def rule_S(imp, reg_raw, reg_sig):
    """Net-effect: ship if estimated net task delta (|imp|-|reg_raw|, the gate's own
    tallies) is positive AND no task regresses beyond the confidence bound. A real
    (significant) regression with improvements forks; otherwise reject."""
    if reg_sig:
        return "FORK" if imp else "REJECT"
    return "APPLY" if (len(imp) - len(reg_raw)) > 0 else "REJECT"


def classify_regressions(dec, phat, alpha):
    """Split a decision's regressed set into significant (real) vs noise."""
    sig, noise, missing = [], [], []
    for t in dec["regressed"]:
        if t not in phat:
            missing.append(t)
            continue
        n = _n_after(dec, t)
        (sig if p_after_zero(phat[t], n) <= alpha else noise).append(t)
    return sig, noise, missing


# --------------------------------------------------------------------------- #
def bar(n, scale=1):
    return "#" * int(round(n * scale))


def main(tag: str = "s1k8b103") -> None:
    try:
        states = C.all_states(tag)
    except FileNotFoundError as exc:
        print(f"[{tag}] cannot load: {exc}")
        return
    if not states:
        print(f"[{tag}] no rounds with pool_state.json -- skip")
        return

    print("=" * 80)
    print(f"I. NOISE-AWARE GATE REPLAY  --  run={tag}   rounds={sorted(states)}")
    print("=" * 80)

    # ---- (0) preliminary code fact, grounded in the run's own lock -----------
    lock = C.experiment_lock(tag)
    hp = lock.get("hyperparams", {}) or {}
    print("\n(0) PRELIMINARY -- is a noise threshold applied to the per-task gate "
          "decision in the variant-pool path?")
    print("    ANSWER: NO.")
    print("    - gate.py:227  improved  <- before==0 and after>=1        (hard, no threshold)")
    print("    - gate.py:229-254 regressed <- after==0 and ever/never-solved (hard, no threshold)")
    print("    - _decide()/min_fork gate.py:257-269 thresholds the COUNT of improved/regressed")
    print("      tasks (min_fork=(1,1) here), never the per-task pass-count delta.")
    print(f"    - experiment_lock.py:250 noise_threshold = None            (lock says: {hp.get('noise_threshold')!r})")
    print(f"    - experiment_lock.py:254 planned_noise_threshold = 0.05    (lock says: {hp.get('planned_noise_threshold')!r})")
    print("      i.e. the paper's +/-5% (Table 8, A.4) was PLANNED but NOT wired into this gate.")
    print("    - The only noise guard that IS applied lives in the K=1 kernel _score_and_gate")
    print("      (run.py:1577 default 3 -> applied run.py:1619-1620) on the ROUND-LEVEL aggregate")
    print("      passed-count delta; it never runs in the variant-pool path.")
    print(f"    [lock regression_baseline={hp.get('regression_baseline')!r} (None -> engine default "
          f"'global'/ever_solved); min_fork={hp.get('min_fork')}]")

    phat, att, pool, prov = pooled_phat(states)
    decs = seesaw_decisions(states)
    thr_r = reg_sig_threshold(alpha=ALPHA)
    thr_i = imp_sig_threshold(alpha=ALPHA)
    print(f"\n[test constants] n={C.PASS_K}  alpha={ALPHA}  ->  regression significant iff "
          f"p_hat>={thr_r:.4f};  improvement significant iff p_hat<={thr_i:.4f}")
    print(f"[bias control] reuse cells excluded via h_pool_headroom.build_table: "
          f"active_reuse={prov['excluded_reuse_active']} gate_reuse={prov['excluded_reuse_gate']} "
          f"(non-reuse cells pooled for p_hat)")

    _section1(phat, att, decs)
    _section2(decs, phat)
    _section3(decs, phat)
    _section4(decs, phat, pool)
    _section5()


# --------------------------------------------------------------------------- #
# (1) per-task success-rate distribution
# --------------------------------------------------------------------------- #
def _section1(phat, att, decs):
    print("\n" + "-" * 80)
    print("(1) PER-TASK SUCCESS-RATE DISTRIBUTION  (pooled over all rounds/variants)")
    print("-" * 80)
    tasks = sorted(phat)
    n = len(tasks)
    if not n:
        print("    no pooled tasks -- cannot build a distribution. SKIP.")
        return
    atts = [att[t] for t in tasks]
    atts_sorted = sorted(atts)
    med = atts_sorted[len(atts_sorted) // 2]
    print(f"    tasks pooled = {n}   attempts/task: min={min(atts)} median={med} "
          f"max={max(atts)}  (carried task ~= {max(atts)} draws)")

    # histogram in bins of 0.1
    bins = [0] * 10
    for t in tasks:
        b = min(int(phat[t] * 10), 9)
        bins[b] += 1
    print("\n    p_hat histogram (bin width 0.1):")
    for i, c in enumerate(bins):
        lo, hi = i / 10, (i + 1) / 10
        print(f"      [{lo:.1f},{hi:.1f}) {c:>4}  {bar(c)}")

    # settled / contested split (unweighted)
    sp = [t for t in tasks if phat[t] >= SETTLED_HI]
    sf = [t for t in tasks if phat[t] <= SETTLED_LO]
    con = [t for t in tasks if SETTLED_LO < phat[t] < SETTLED_HI]
    print(f"\n    UNWEIGHTED (n={n} tasks):")
    print(f"      settled-pass (p_hat>={SETTLED_HI})   : {len(sp):>4}  ({len(sp)/n*100:5.1f}%)")
    print(f"      settled-fail (p_hat<={SETTLED_LO})   : {len(sf):>4}  ({len(sf)/n*100:5.1f}%)")
    print(f"      CONTESTED    ({SETTLED_LO}<p_hat<{SETTLED_HI}) : {len(con):>4}  "
          f"({len(con)/n*100:5.1f}%)   <- the noise-story target band")

    # weighted by how often each task appears in a gate decision
    appear = Counter()
    for d in decs:
        for t in set(d["improved"]) | set(d["regressed"]):
            appear[t] += 1
    tot_w = sum(appear.values())
    if tot_w:
        w_sp = sum(appear[t] for t in sp)
        w_sf = sum(appear[t] for t in sf)
        w_con = sum(appear[t] for t in con)
        w_missing = tot_w - w_sp - w_sf - w_con  # decision tasks with no pooled p_hat
        print(f"\n    WEIGHTED by gate-decision appearances (total appearances={tot_w} "
              f"across {len(decs)} seesaw candidates):")
        print(f"      settled-pass : {w_sp:>4}  ({w_sp/tot_w*100:5.1f}%)")
        print(f"      settled-fail : {w_sf:>4}  ({w_sf/tot_w*100:5.1f}%)")
        print(f"      CONTESTED    : {w_con:>4}  ({w_con/tot_w*100:5.1f}%)   <- share of gate-touched task-slots that are contested")
        if w_missing:
            print(f"      (no pooled p_hat: {w_missing} appearances -- task never made a usable non-reuse cell)")
        distinct_touch = len(appear)
        con_touch = sum(1 for t in appear if SETTLED_LO < phat.get(t, -1) < SETTLED_HI)
        print(f"      distinct tasks ever in a decision = {distinct_touch}; of them contested = "
              f"{con_touch} ({con_touch/distinct_touch*100:.1f}%)")
    else:
        print("\n    no gate decisions with improved/regressed tasks -- weighted split N/A.")

    # verdict
    frac_con = len(con) / n
    print("\n    READ: ", end="")
    if frac_con < 0.15:
        print(f"contested band is SMALL ({frac_con*100:.1f}%) -- the noise story has a thin "
              "target; most tasks are settled. Leans toward FALSIFYING the hypothesis.")
    else:
        print(f"contested band is LARGE ({frac_con*100:.1f}% of tasks; see weighted share above) "
              "-- enough noise-sensitive tasks for gate decisions to be noise-driven.")


# --------------------------------------------------------------------------- #
# (2) gate-decision noise audit
# --------------------------------------------------------------------------- #
def _section2(decs, phat):
    print("\n" + "-" * 80)
    print("(2) GATE-DECISION NOISE AUDIT  (expected pure-noise flags per round)")
    print("-" * 80)
    print("    Source of improved/regressed sets: VERBATIM from candidate_diagnostics")
    print("    [cid].archive_reason ('improved=[...] regressed=[...]'); after-state from")
    print("    [cid].evaluation. Expected pure-noise count uses the CONDITIONAL false-alarm")
    print("    rate = each flag's exact-binomial p-value (regressed: (1-p_hat)^n; improved:")
    print("    1-(1-p_hat)^n). (Joint symmetric prob of the whole before->after pattern =")
    print("    (1-p_hat)^n * (1-(1-p_hat)^n), reported as E_noise_joint for reference.)")
    if not decs:
        print("    no seesaw candidates in this run -- SKIP.")
        return
    print(f"\n    {'R':>3} {'cid':>12} {'dec':>7} {'|imp|':>5} {'|reg|':>5} "
          f"{'E_noiseImp':>10} {'E_noiseReg':>10} {'regSig':>7} {'E_jointReg':>10}")
    tot = defaultdict(float)
    for d in decs:
        r, cid, dec = d["round"], d["cid"], d["decision"]
        imp, reg = d["improved"], d["regressed"]
        e_imp = e_reg = e_joint = 0.0
        nsig = 0
        for t in imp:
            if t in phat:
                e_imp += p_after_pass(phat[t], _n_after(d, t))
        for t in reg:
            if t in phat:
                n = _n_after(d, t)
                pv = p_after_zero(phat[t], n)
                e_reg += pv
                e_joint += pv * p_after_pass(phat[t], n)
                if pv <= ALPHA:
                    nsig += 1
        tot["imp"] += len(imp); tot["reg"] += len(reg)
        tot["e_imp"] += e_imp; tot["e_reg"] += e_reg; tot["e_joint"] += e_joint
        tot["regsig"] += nsig
        print(f"    {r:>3} {cid:>12} {dec:>7} {len(imp):>5} {len(reg):>5} "
              f"{e_imp:>10.2f} {e_reg:>10.2f} {nsig:>7} {e_joint:>10.2f}")
    print(f"\n    AGGREGATE over {len(decs)} candidates:")
    print(f"      total improved flags = {int(tot['imp'])}; expected pure noise = "
          f"{tot['e_imp']:.1f}  ({tot['e_imp']/tot['imp']*100:.0f}% of improved flags)"
          if tot['imp'] else "      no improved flags")
    print(f"      total regressed flags= {int(tot['reg'])}; expected pure noise = "
          f"{tot['e_reg']:.1f}  ({tot['e_reg']/tot['reg']*100:.0f}% of regressed flags)"
          if tot['reg'] else "      no regressed flags")
    print(f"      regressed flags that ARE significant (real) at alpha={ALPHA}: "
          f"{int(tot['regsig'])} / {int(tot['reg'])}"
          + (f"  ({tot['regsig']/tot['reg']*100:.0f}%)" if tot['reg'] else ""))


# --------------------------------------------------------------------------- #
# (3) counterfactual decision flips
# --------------------------------------------------------------------------- #
def _section3(decs, phat):
    print("\n" + "-" * 80)
    print("(3) COUNTERFACTUAL DECISION FLIPS  (Rule H current / N noise-tolerant / S net-effect)")
    print("-" * 80)
    print("    Rule N: regressed task counts only if (1-p_hat)^n <= alpha (exact binomial).")
    print("    Rule S: APPLY iff |imp|-|reg_raw|>0 and no significant regression; a")
    print("            significant regression with improvements FORKs, else REJECT.")
    if not decs:
        print("    no seesaw candidates -- SKIP.")
        return

    for alpha in (ALPHA, ALPHA2):
        print(f"\n    === alpha = {alpha}  (regression significant iff p_hat >= "
              f"{reg_sig_threshold(alpha=alpha):.4f}) ===")
        confN = Counter()
        confS = Counter()
        rows = []
        for d in decs:
            imp, reg = d["improved"], d["regressed"]
            reg_sig, reg_noise, reg_miss = classify_regressions(d, phat, alpha)
            # tasks with no pooled p_hat are treated as non-significant (cannot reject H0)
            aH = rule_H(imp, reg)
            aN = rule_N(imp, reg_sig)
            aS = rule_S(imp, reg, reg_sig)
            actual = d["decision"].upper()
            confN[(actual, aN)] += 1
            confS[(actual, aS)] += 1
            rows.append((d["round"], d["cid"], actual, aH, aN, aS,
                         len(imp), len(reg), len(reg_sig)))
        # per-candidate table
        print(f"    {'R':>3} {'cid':>12} {'actual':>7} {'ruleH':>6} {'ruleN':>6} "
              f"{'ruleS':>6} {'|imp|':>5} {'|reg|':>5} {'regSig':>6}")
        for r, cid, ac, aH, aN, aS, ni, nr, ns in rows:
            flag = ""
            if ac != aN:
                flag += " N:%s->%s" % (ac, aN)
            if ac != aS:
                flag += " S:%s->%s" % (ac, aS)
            print(f"    {r:>3} {cid:>12} {ac:>7} {aH:>6} {aN:>6} {aS:>6} "
                  f"{ni:>5} {nr:>5} {ns:>6}{flag}")

        # confusion matrices + headline flips
        cats = ["APPLY", "FORK", "REJECT"]
        for name, conf in (("N (noise-tolerant)", confN), ("S (net-effect)", confS)):
            print(f"\n    confusion matrix vs Rule {name}   (rows=actual, cols=counterfactual):")
            print("        " + "".join(f"{c:>8}" for c in cats))
            for a in cats:
                print(f"    {a:>6}" + "".join(f"{conf.get((a, c), 0):>8}" for c in cats))
            r2a = sum(conf.get(("REJECT", "APPLY"), 0) for _ in [0])
            f2a = conf.get(("FORK", "APPLY"), 0)
            a2r = conf.get(("APPLY", "REJECT"), 0)
            f2r = conf.get(("FORK", "REJECT"), 0)
            print(f"      >>> REJECT->APPLY (throughput rescue) = {r2a}")
            print(f"      >>> FORK->APPLY  (edit propagated pool-wide instead of quarantined) = {f2a}")
            print(f"      >>> APPLY->REJECT (null edit the current gate lets through) = {a2r}")
            if name.startswith("S"):
                print(f"      >>> FORK->REJECT (net-negative edit the current gate ships as a fork) = {f2r}")


# --------------------------------------------------------------------------- #
# (4) ever_solved veto load
# --------------------------------------------------------------------------- #
def _section4(decs, phat, pool):
    print("\n" + "-" * 80)
    print("(4) EVER_SOLVED VETO LOAD  (how much veto power is luck?)")
    print("-" * 80)
    print("    ever_solved reconstructed as {task: >=1 pooled non-reuse pass} (global")
    print("    baseline; reuse passes excluded as circular). 'entered by luck' = pooled")
    print(f"    p_hat < {LOW_P}.")
    ever = {t for t, sa in pool.items() if sa[0] >= 1}
    if not ever:
        print("    no ever-solved tasks reconstructable -- SKIP.")
        return
    lucky = {t for t in ever if phat[t] < LOW_P}
    print(f"\n    |ever_solved| = {len(ever)};  low-p_hat (<{LOW_P}) members = {len(lucky)} "
          f"({len(lucky)/len(ever)*100:.1f}%)  <- in the veto set by luck")

    # per rejected/forked candidate: how many blocking regressed tasks are low-p_hat
    print(f"\n    per candidate that was blocked/split by regressions:")
    print(f"    {'R':>3} {'cid':>12} {'dec':>7} {'|reg|':>5} {'reg_lowP':>8} "
          f"{'reg_sig':>7}  low-p_hat blocking tasks dominate?")
    tot_reg = tot_low = 0
    for d in decs:
        reg = d["regressed"]
        if not reg or d["decision"] not in ("fork", "reject"):
            continue
        low = [t for t in reg if t in phat and phat[t] < LOW_P]
        sig, _noise, _miss = classify_regressions(d, phat, ALPHA)
        tot_reg += len(reg); tot_low += len(low)
        dom = "YES" if len(low) >= max(1, len(reg) / 2) else ""
        print(f"    {d['round']:>3} {d['cid']:>12} {d['decision']:>7} {len(reg):>5} "
              f"{len(low):>8} {len(sig):>7}  {dom}")
    if tot_reg:
        print(f"\n    across blocked/split candidates: {tot_low}/{tot_reg} blocking regressed "
              f"tasks are low-p_hat luck ({tot_low/tot_reg*100:.0f}%).")
        print("    A regressed task can veto an APPLY (force FORK/REJECT) even when it entered")
        print("    ever_solved on a single lucky pass -- that is the ratchet's noise load.")


# --------------------------------------------------------------------------- #
# (5) throughput-vs-score cross-run check
# --------------------------------------------------------------------------- #
def _imputed_r0_final_gain(tag):
    """Reuse f_fork_reuse_correction.main (captured) for the imputed R0->final gain."""
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            F.main(tag)
    except Exception as exc:  # noqa: BLE001 - report and skip, never substitute
        return None, f"f_fork_reuse_correction failed: {exc}"
    txt = buf.getvalue()
    m = re.search(r"gain over R0,\s*imputed final:\s*([+-]?[0-9.]+)", txt)
    if not m:
        return None, "no R0 in run (imputed R0->final gain undefined)"
    return float(m.group(1)), None


def _run_decision_counts(tag):
    """(n_rounds, n_apply, n_fork, n_reject, n_drought) from pool_state artifacts."""
    try:
        states = C.all_states(tag)
    except FileNotFoundError:
        return None
    if not states:
        return None
    n_apply = n_fork = n_reject = n_drought = 0
    for r, st in states.items():
        if st.get("no_candidate"):
            n_drought += 1
        cd = st.get("candidate_diagnostics") or {}
        for _cid, info in cd.items():
            reason = info.get("archive_reason") or ""
            if "improved=" not in reason:
                continue
            dec = (info.get("decision") or "").lower()
            if dec == "apply":
                n_apply += 1
            elif dec == "fork":
                n_fork += 1
            elif dec == "reject":
                n_reject += 1
    return len(states), n_apply, n_fork, n_reject, n_drought


def _section5():
    print("\n" + "-" * 80)
    print("(5) THROUGHPUT-vs-SCORE CROSS-RUN CHECK  (all runs with >=3 rounds)")
    print("-" * 80)
    print("    R0->final gain uses the IMPUTED fork-reuse-corrected curve")
    print("    (f_fork_reuse_correction.main, captured -- NOT pool_report.curve).")
    runs = sorted(p.name for p in C.RUNS_ROOT.iterdir() if p.is_dir())
    print(f"\n    {'run':>16} {'rnds':>4} {'APPLY':>5} {'FORK':>4} {'REJ':>4} {'drt':>4} "
          f"{'ships':>5} {'imp_gain':>9}")
    pts = []
    for run in runs:
        counts = _run_decision_counts(run)
        if not counts:
            continue
        n_rounds, na, nf, nr, nd = counts
        if n_rounds < 3:
            continue
        gain, err = _imputed_r0_final_gain(run)
        ships = na + nf
        gstr = f"{gain:+.4f}" if gain is not None else "  NA"
        print(f"    {run:>16} {n_rounds:>4} {na:>5} {nf:>4} {nr:>4} {nd:>4} {ships:>5} {gstr:>9}"
              + ("" if gain is not None else f"   ({err})"))
        if gain is not None:
            pts.append((ships, gain, run))
    # observational correlation, honest caveat
    if len(pts) >= 3:
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        n = len(pts)
        mx, my = sum(xs) / n, sum(ys) / n
        sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
        sxx = sum((x - mx) ** 2 for x in xs)
        syy = sum((y - my) ** 2 for y in ys)
        rho = sxy / (sxx * syy) ** 0.5 if sxx > 0 and syy > 0 else float("nan")
        print(f"\n    ships (APPLY+FORK) vs imputed R0->final gain: Pearson r = {rho:.3f}  "
              f"(n={n} runs)")
        print("    CAVEAT: n is small and this is observational -- runs differ in bed, seed,")
        print("    cluster source, and length; r here is descriptive, not causal, and one")
        print("    long run can dominate. Do not read a policy claim into it.")
    else:
        print(f"\n    only {len(pts)} runs with an imputed gain -- too few to correlate.")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "s1k8b103")
