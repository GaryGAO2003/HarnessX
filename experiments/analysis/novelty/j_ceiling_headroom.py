# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""Analysis J -- step-ceiling headroom: is the 20-step budget a real score lever,
or is budget exhaustion concentrated on hopeless tasks?

Two questions, kept separate:

(A) NO-OP AUDIT (Task 2).  The s1k8b103 meta-agent (learnings_V7.md:233,258)
    claims its Rounds 2/4/6 ``event.state.max_steps = N`` edits never took effect
    and every rollout kept exiting ``budget_exceeded`` at step 20, blaming a
    "frozen dataclass / FrozenInstanceError".  That mechanism is false in code
    (``State`` is a plain ``@dataclass`` -- state.py:119; ``max_steps`` is a mutable
    field -- state.py:142; ``budget_exceeded`` reads ``self.max_steps`` live --
    state.py:161-168; ``run_loop`` passes the SAME state into the TaskStartEvent --
    runloop.py:201; ``ProcessorChain`` never copies event.state -- processor.py:401).
    The real reason the ceiling never moved is that the evolved CommitNudgeProcessor
    was never instantiated: its config ``_target_`` is a ``file:///`` Windows URI the
    builder mis-resolves AND it passes a ``nudge=`` kwarg the class __init__ rejects,
    so ``harness._instantiate_proc`` (harness.py:353-360) swallows the exception,
    returns None, and the variant runs the stock stack (see
    experiments/variant_pool/tests/test_processor_targets.py).  This script does NOT
    re-argue the code; it CONFIRMS the no-op empirically from landed telemetry: the
    max observed per-attempt step count per round, and whether any attempt on the
    affected variants (V3/V4) ever exceeded 20 steps.

(B) HEADROOM (Task 3).  For every ``budget_exceeded`` attempt, bucket its task by
    the pooled per-task success rate p_hat.  Budget exhaustion concentrated in the
    CONTESTED band (0.15<p_hat<0.85) = a higher ceiling could convert failures to
    passes; concentrated at p_hat==0 = the ceiling is not the binding constraint.

Data plumbing (verified against the artefacts, see _common.py):
  * ACTIVE-pool per-attempt records: comparison.json ``rounds[r]`` (r=1..15), one
    per-(round,task) record with a nested ``attempts`` list; each attempt carries
    ``exit_reason`` and ``steps`` (the PER-ATTEMPT step count).  NOTE: the record's
    top-level ``steps`` is the SUM across attempts (14+25=39) -- this script reads
    only the nested per-attempt ``attempts[].steps``.  measurement_scope =
    'settled_active_pool'.  R0 has no records.
  * ``max_steps`` (the ceiling itself) is NOT in any JSON field; it appears only as
    the denominator of ``- steps: {total_steps}/{max_steps}`` in the trajectory .md
    (trajectory.py:466).  This script spot-reads a few trajectory files per round to
    report the ceiling authoritatively.
  * CANDIDATE rollouts have NO per-attempt exit_reason/steps; only per-(candidate,
    task) COUNTS survive in pool_report.json candidate_diagnostics.results[]
    (``budget_exhaustions`` / ``n_att`` / ``infra_failures``).  So the candidate
    breakdown is at cell-count granularity, not per-attempt.

p_hat REUSED (do not re-derive): ``i_gate_noise_replay.pooled_phat`` -> pools every
usable non-reuse (round,variant,task) cell from ``h_pool_headroom.build_table``
(which drops ``active_score_source == candidate_reuse`` circular cells).

Read-only. stdlib only. Zero API cost.
Usage:  python experiments/analysis/novelty/j_ceiling_headroom.py [run]
"""

from __future__ import annotations

import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

import _common as C
import i_gate_noise_replay as I

SETTLED_HI = I.SETTLED_HI      # 0.85 -> settled-pass
SETTLED_LO = I.SETTLED_LO      # 0.15 -> settled-fail
HIGH_BE = 0.50                 # "high budget-exhaustion rate" threshold (part 3)
CEILING_HINT = 20              # the ceiling the meta-agent claims rollouts capped at
_STEPS_RE = re.compile(r"steps:\s*(\d+)\s*/\s*(\d+)")


# --------------------------------------------------------------------------- #
# loaders
# --------------------------------------------------------------------------- #
def load_comparison(tag: str) -> dict | None:
    p = C.run_path(tag) / "comparison.json"
    return C.load_json(p) if p.exists() else None


def active_attempts(comp: dict):
    """Yield one dict per ACTIVE-pool attempt (the nested per-attempt records)."""
    for r, recs in enumerate(comp.get("rounds", [])):
        if not isinstance(recs, list):
            continue
        for rec in recs:
            tid = rec.get("task_id")
            vid = rec.get("variant_id")
            atts = rec.get("attempts")
            if not isinstance(atts, list):
                continue
            for a in atts:
                yield {
                    "round": r,
                    "task": tid,
                    "variant": vid,
                    "exit_reason": a.get("exit_reason"),
                    "steps": a.get("steps"),
                    "passed": bool(a.get("passed")),
                    "infra_failure": bool(a.get("infra_failure")),
                    "traj": a.get("trajectory_file"),
                }


def classify(att: dict) -> str:
    """Mutually-exclusive per-attempt outcome by exit_reason (+ infra flag)."""
    ex = att["exit_reason"]
    if ex == "budget_exceeded":
        return "budget"
    if ex == "error" or att["infra_failure"]:
        return "infra"
    if att["passed"]:
        return "pass"
    return "wrong"  # exit_reason == 'done' but judged incorrect


def bucket(p: float | None) -> str:
    if p is None:
        return "no_phat"
    if p <= SETTLED_LO:
        return "settled-fail"
    if p >= SETTLED_HI:
        return "settled-pass"
    return "contested"


# --------------------------------------------------------------------------- #
# trajectory ceiling spot-check (max_steps is only in the .md, not the JSON)
# --------------------------------------------------------------------------- #
def _read_ceiling(run_root: Path, relpath: str):
    """Return (max_steps, total_steps) from a trajectory .md, or None."""
    if not relpath:
        return None
    p = run_root / relpath
    if not p.is_file():
        return None
    try:
        txt = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    ms = _STEPS_RE.findall(txt)
    if not ms:
        return None
    tot, cap = ms[-1]          # last "steps: T/M" line = the final render
    return int(cap), int(tot)


def sample_ceilings_by_round(tag: str, comp: dict, per_round: int = 8):
    """Spot-read up to `per_round` budget_exceeded trajectories per round; return
    {round: Counter(max_steps_value)}."""
    run_root = C.run_path(tag)
    out: dict[int, Counter] = {}
    for r, recs in enumerate(comp.get("rounds", [])):
        if not isinstance(recs, list) or not recs:
            continue
        seen = Counter()
        n = 0
        for rec in recs:
            for a in rec.get("attempts", []):
                if a.get("exit_reason") != "budget_exceeded":
                    continue
                got = _read_ceiling(run_root, a.get("trajectory_file"))
                if got:
                    seen[got[0]] += 1
                    n += 1
                if n >= per_round:
                    break
            if n >= per_round:
                break
        if seen:
            out[r] = seen
    return out


def ceiling_from_glob(tag: str, limit: int = 150):
    """Fallback for runs without comparison.json: glob trajectory .md files and
    read their max_steps denominators + max total_steps directly."""
    run_root = C.run_path(tag)
    caps = Counter()
    max_tot = 0
    over20 = 0
    n = 0
    for p in run_root.glob("R*/**/trajectories/*.md"):
        got = _read_ceiling(run_root, str(p.relative_to(run_root)))
        if not got:
            continue
        cap, tot = got
        caps[cap] += 1
        max_tot = max(max_tot, tot)
        if tot > CEILING_HINT:
            over20 += 1
        n += 1
        if n >= limit:
            break
    return caps, max_tot, over20, n


# --------------------------------------------------------------------------- #
def main(tag: str = "s1k8b103") -> None:
    try:
        states = C.all_states(tag)
    except FileNotFoundError as exc:
        print(f"[{tag}] cannot load: {exc}")
        return

    print("=" * 84)
    print(f"J. STEP-CEILING HEADROOM  --  run={tag}   rounds={sorted(states)}")
    print("=" * 84)

    comp = load_comparison(tag)
    if comp is None:
        _no_comparison_fallback(tag, states)
        return

    phat, att_pool, pool, prov = I.pooled_phat(states)
    print(f"[p_hat] pooled per-task rate from {len(phat)} tasks "
          f"(reuse cells excluded: active={prov['excluded_reuse_active']} "
          f"gate={prov['excluded_reuse_gate']}; source = i_gate_noise_replay.pooled_phat)")

    atts = list(active_attempts(comp))
    _task2(tag, comp, atts)
    _task3(tag, atts, phat)
    _task3_candidates(tag, phat)


# --------------------------------------------------------------------------- #
# TASK 2 -- empirical no-op confirmation
# --------------------------------------------------------------------------- #
def _task2(tag, comp, atts):
    print("\n" + "-" * 84)
    print("(TASK 2) PER-ROUND STEP CENSUS  --  did any max_steps edit ever move the ceiling?")
    print("-" * 84)
    print("  per-attempt steps come from rounds[r][*].attempts[*].steps (NOT the record's")
    print("  top-level 'steps', which is the SUM across attempts).  'max_steps' is absent")
    print("  from every JSON field; the CEILING column is spot-read from the trajectory .md")
    print("  ('- steps: total/max').  affected variants (carrying the CommitNudge edits) = V3 (R4-),")
    print("  V4 (R6-).")

    ceilings = sample_ceilings_by_round(tag, comp, per_round=8)

    by_round = defaultdict(list)
    for a in atts:
        by_round[a["round"]].append(a)

    print(f"\n  {'R':>3} {'attempts':>8} {'max_step':>8} {'ceiling(md)':>18} "
          f"{'budget_exc':>10} {'>20 steps':>9}  variants(>20)")
    tot_att = tot_bud = tot_over = 0
    over_rows = []
    for r in range(0, 16):
        ra = by_round.get(r, [])
        if not ra and r not in ceilings:
            continue
        steps = [a["steps"] for a in ra if isinstance(a["steps"], int)]
        mx = max(steps) if steps else None
        nb = sum(1 for a in ra if a["exit_reason"] == "budget_exceeded")
        over = [a for a in ra if isinstance(a["steps"], int) and a["steps"] > CEILING_HINT]
        cap_str = ",".join(f"{k}x{v}" for k, v in sorted(ceilings.get(r, {}).items())) or "-"
        vover = ",".join(sorted({a["variant"] for a in over})) or "-"
        print(f"  {r:>3} {len(ra):>8} {str(mx):>8} {cap_str:>18} {nb:>10} {len(over):>9}  {vover}")
        tot_att += len(ra); tot_bud += nb; tot_over += len(over)
        over_rows.extend(over)
    denom = tot_att
    print(f"\n  TOTAL active attempts = {tot_att};  budget_exceeded = {tot_bud} "
          f"({tot_bud/denom*100:.1f}%);  attempts with >20 steps = {tot_over} "
          f"({tot_over/denom*100:.1f}%)")

    # the decisive test
    print("\n  DECISIVE TEST -- any attempt exceeding 20 steps, and on which variant/round:")
    if not over_rows:
        print("    NONE.  Every active attempt in the run ran <= 20 steps.")
    else:
        vc = Counter(a["variant"] for a in over_rows)
        rc = Counter(a["round"] for a in over_rows)
        ec = Counter(a["exit_reason"] for a in over_rows)
        print(f"    {len(over_rows)} attempts > 20 steps.  by variant={dict(vc)}  "
              f"by round={dict(sorted(rc.items()))}  by exit={dict(ec)}")
        aff = [a for a in over_rows if a["variant"] in ("V3", "V4")]
        print(f"    of these, on the AFFECTED variants V3/V4 = {len(aff)}")
        mxs = max(a["steps"] for a in over_rows)
        print(f"    max steps reached by any attempt = {mxs}")

    # verdict
    aff_over = [a for a in atts if a["variant"] in ("V3", "V4")
                and isinstance(a["steps"], int) and a["steps"] > CEILING_HINT]
    aff_att = [a for a in atts if a["variant"] in ("V3", "V4")]
    aff_steps = [a["steps"] for a in aff_att if isinstance(a["steps"], int)]
    print("\n  VERDICT on the no-op claim (R4 budget_floor=30, R6 budget_floor=55):")
    print(f"    affected-variant (V3/V4) attempts = {len(aff_att)};  their max step = "
          f"{max(aff_steps) if aff_steps else 'n/a'};  any > 20 = {len(aff_over)}")
    if aff_att and not aff_over and (max(aff_steps) if aff_steps else 0) <= CEILING_HINT:
        print("    -> CONFIRMED: no V3/V4 attempt ever exceeded 20 steps.  Had the "
              "budget_floor=30/55")
        print("       writes taken effect, V3/V4 budget_exceeded attempts would exit at 30/55.")
        print("       They exit at exactly 20.  The max_steps edits were no-ops -- but NOT via")
        print("       FrozenInstanceError (see header): the evolved processor was never loaded.")
    else:
        print("    -> NOT confirmed by this slice; inspect the rows above.")


# --------------------------------------------------------------------------- #
# TASK 3 -- headroom buckets (ACTIVE pool, per-attempt)
# --------------------------------------------------------------------------- #
def _task3(tag, atts, phat):
    print("\n" + "-" * 84)
    print("(TASK 3) BUDGET-EXHAUSTION HEADROOM  --  ACTIVE pool, per-attempt")
    print("-" * 84)

    budget = [a for a in atts if a["exit_reason"] == "budget_exceeded"]
    nb = len(budget)
    print(f"  budget_exceeded active attempts = {nb} / {len(atts)} total active attempts "
          f"({nb/len(atts)*100:.1f}%)")

    # (3.1) bucket each budget attempt by its task's pooled p_hat
    buck = Counter()
    zero = 0
    for a in budget:
        p = phat.get(a["task"])
        buck[bucket(p)] += 1
        if p is not None and p == 0.0:
            zero += 1
    print("\n  (3.1) budget_exceeded ATTEMPTS bucketed by pooled task p_hat "
          f"(denominator = {nb}):")
    for b in ("settled-fail", "contested", "settled-pass", "no_phat"):
        c = buck.get(b, 0)
        extra = f"   (of which p_hat==0 exactly: {zero})" if b == "settled-fail" else ""
        print(f"      {b:14}: {c:>5}  ({c/nb*100:5.1f}%){extra}")
    contested = buck.get("contested", 0)
    print(f"\n    READ: budget exhaustion is "
          f"{'CONCENTRATED in the contested band -> real conversion potential' if contested/nb >= 0.5 else 'NOT concentrated in the contested band'}"
          f"  (contested share = {contested/nb*100:.1f}%; settled-fail share = "
          f"{buck.get('settled-fail',0)/nb*100:.1f}%).")

    # per-task aggregation
    per_task = defaultdict(lambda: Counter())
    for a in atts:
        per_task[a["task"]][classify(a)] += 1

    # (3.2) top-20 tasks by budget-exhaustion rate
    rows = []
    for t, c in per_task.items():
        n = sum(c.values())
        if not n:
            continue
        rows.append((c["budget"] / n, t, n, c, phat.get(t)))
    rows.sort(key=lambda x: (-x[0], -x[2]))
    print("\n  (3.2) TOP 20 TASKS by budget-exhaustion rate "
          "(rate = budget attempts / all attempts of that task):")
    print(f"      {'task':>10} {'n_att':>5} {'budget':>6} {'wrong':>5} {'infra':>5} "
          f"{'pass':>4} {'BE_rate':>7} {'p_hat':>6}")
    for be_rate, t, n, c, p in rows[:20]:
        ps = f"{p:.3f}" if p is not None else "  n/a"
        print(f"      {t[:10]:>10} {n:>5} {c['budget']:>6} {c['wrong']:>5} {c['infra']:>5} "
              f"{c['pass']:>4} {be_rate*100:>6.1f}% {ps:>6}")

    # (3.3) never-pass AND high budget-exhaustion tasks = optimistic conversion set
    print(f"\n  (3.3) tasks with p_hat == 0 AND budget-exhaustion rate >= {HIGH_BE:.0%} "
          "(optimistic conversion set):")
    cand = []
    for be_rate, t, n, c, p in rows:
        if p is not None and p == 0.0 and be_rate >= HIGH_BE:
            cand.append((t, n, c["budget"], be_rate))
    total_tasks = len(per_task)
    p0_tasks = sum(1 for t in per_task if phat.get(t) == 0.0)
    print(f"      count = {len(cand)} tasks   (of {p0_tasks} tasks with p_hat==0; "
          f"of {total_tasks} tasks total in the active pool)")
    for t, n, nbud, be_rate in cand[:25]:
        print(f"        {t[:12]:>12}  n_att={n:>3}  budget={nbud:>3}  BE_rate={be_rate*100:.0f}%  p_hat=0")
    print("      >>> UPPER BOUND (assumption: a higher ceiling flips EVERY budget attempt on")
    print(f"          these {len(cand)} never-pass tasks into a pass -- unrealistic; these tasks")
    print("          have produced 0 passes across ALL pooled attempts, so the ceiling is very")
    print("          unlikely to be their binding constraint).  This is a structural ceiling,")
    print("          NOT a prediction.")


# --------------------------------------------------------------------------- #
# TASK 3.4 -- candidate-evaluation rollouts (cell-count granularity)
# --------------------------------------------------------------------------- #
def _task3_candidates(tag, phat):
    print("\n" + "-" * 84)
    print("(TASK 3.4) CANDIDATE-EVALUATION rollouts  --  cell-count granularity only")
    print("-" * 84)
    try:
        pr = C.pool_report(tag)
    except FileNotFoundError:
        print("  no pool_report.json -> candidate budget breakdown UNAVAILABLE for this run.")
        return
    cd = pr.get("candidate_diagnostics", {}) or {}
    results = cd.get("results")
    if not isinstance(results, list):
        print("  candidate_diagnostics.results absent -> SKIP.")
        return
    tot_att = cd.get("attempts")
    tot_be = cd.get("budget_exhaustions")
    print(f"  candidate attempts (aggregate) = {tot_att};  candidate budget_exhaustions "
          f"(aggregate) = {tot_be};  infra = {cd.get('infra_failures')}")
    print("  NOTE: candidate records carry per-cell COUNTS (budget_exhaustions/n_att), NOT")
    print("  per-attempt exit_reason or steps.  No candidate step count is recoverable, so the")
    print("  'did any exceed 20 steps' test cannot be run on candidates from the JSON.")

    per_task_be = Counter()
    per_task_att = Counter()
    summed_be = 0
    for cell in results:
        t = cell.get("task_id")
        be = cell.get("budget_exhaustions") or 0
        na = cell.get("n_att") or 0
        if t is None:
            continue
        per_task_be[t] += be
        per_task_att[t] += na
        summed_be += be
    print(f"  sum of per-cell budget_exhaustions across results[] = {summed_be} "
          f"(cross-check vs aggregate {tot_be})")

    buck = Counter()
    for t, be in per_task_be.items():
        if be:
            buck[bucket(phat.get(t))] += be
    denom = summed_be or 1
    print(f"\n  candidate budget_exhaustions bucketed by pooled task p_hat "
          f"(denominator = {summed_be}):")
    for b in ("settled-fail", "contested", "settled-pass", "no_phat"):
        c = buck.get(b, 0)
        print(f"      {b:14}: {c:>5}  ({c/denom*100:5.1f}%)")


# --------------------------------------------------------------------------- #
# fallback for runs without comparison.json (e.g. e_pervar3)
# --------------------------------------------------------------------------- #
def _no_comparison_fallback(tag, states):
    print("\n  NO comparison.json for this run.  Per-attempt exit_reason/steps are NOT")
    print("  machine-readable here, and there is no pool_report.json budget_exhaustions_run_total")
    print("  either.  Falling back to a trajectory-file ceiling spot-check (max_steps is the")
    print("  denominator of '- steps: total/max' in each .md).")
    caps, max_tot, over20, n = ceiling_from_glob(tag)
    if n == 0:
        print("  no trajectory .md files with a 'steps: T/M' line found -> ceiling UNDETERMINED.")
        return
    print(f"\n  sampled {n} trajectory files:")
    print(f"    max_steps (ceiling) values observed: {dict(sorted(caps.items()))}")
    print(f"    max total_steps observed across sample = {max_tot}")
    print(f"    attempts with total_steps > {CEILING_HINT} = {over20} / {n} "
          f"({over20/n*100:.1f}%)")
    if set(caps) <= {CEILING_HINT}:
        print(f"    -> SAME ceiling behaviour: every sampled rollout is capped at "
              f"{CEILING_HINT} steps.")
    else:
        print(f"    -> ceiling is NOT uniformly {CEILING_HINT}; see the distribution above.")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "s1k8b103")
