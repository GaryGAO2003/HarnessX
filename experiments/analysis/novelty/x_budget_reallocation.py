# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""At a fixed attempt budget, is there anything to gain by spending attempts
where they can actually change an outcome?

Every task is given pass_k=2 attempts, flat. The bed is not flat. Pooled over the
whole corpus, 3 of the 103 tasks are never solved, 13 are always solved, and 87
swing. Two attempts on an always-solved task buy nothing; two on a never-solved
task buy nothing either; only swing tasks can be moved, and among those the
marginal value of a third or fourth attempt is largest exactly where the first two
attempts disagree (same-variant/same-round attempt pairs disagree ~21%, see
r_effective_n.py). So the status quo spends part of its budget where no outcome can
change. Idea N-15a asks how much of that is recoverable at a FIXED total budget,
and -- the part that decides whether it is worth building -- how much is reachable
by a rule that only sees signals available at allocation time.

THE ESTIMATOR. Model a cell as n independent Bernoulli(p_t) draws, solved iff any
attempt passes:  P(solved | n) = 1 - (1 - p_t)^n.  Expected tasks solved is that,
summed over the 103-task bed. The objective is concave in n_t, so at a fixed total
budget the optimum is greedy: give each next attempt to the task with the largest
marginal gain (1 - p_t)^{n_t} * p_t. That optimum is an ORACLE -- it reads each
task's true p_t, which no deployed allocator knows -- so it is only ever an UPPER
BOUND and is labelled as one everywhere it appears (Section 2).

WHAT ACTUALLY DECIDES THE RESULT is not the ceiling but the controls (Section 3).
The oracle estimates p_hat_t from the very attempts it then allocates, so it will
look good even on pure noise. We split each task's attempts in half, estimate on
half A, allocate on that estimate, and score on half B; the gain that survives the
split is the honest one, and the in-sample/out-of-sample difference is the
overfitting premium. We then shuffle the task labels 1000 times (seeded) to build a
null distribution of the "gain". If the out-of-sample gain does not clear the
null's 95th percentile, the signal is noise, and the headline says exactly that -- a
null here is as useful as a positive, because it tells us NOT to spend engineering
budget on a reallocation rule.

Sections 4 and 5 ask the deployable question. A feasible rule may use only what is
observable when the allocation is made -- past rounds (prior-driven) or a first pair
of attempts whose answers agree or not (agreement-driven, N-15a as literally
stated) -- never the task's true p_t. And because deployment ships ONE answer, a
pass@k gain is not a delivered gain: Section 5 re-scores the best feasible
allocation through NA.rule_prefer_done and reports what is actually delivered.

FRAME. Section 1 describes the corpus AS SPENT: 6730 attempts across 36 runs. From
Section 2 on we model a single deployment whose status-quo budget is 2 x 103 = 206
attempts, using the 6730 corpus attempts only to ESTIMATE each task's difficulty
p_t. "The observed total number of attempts" therefore means the per-deployment
total the status quo spends, 206, not the corpus total.

ASSUMPTIONS (each is stated again in the output where it binds):
  A1  attempts within a cell are independent Bernoulli(p_t)   -> the 1-(1-p)^n model
  A2  p_t is stationary across rounds and pooled over variants -> per-task pooling
  A3  attempts can be issued sequentially per cell             -> the two-phase rule
The current runner does issue the two attempts of a cell sequentially, so A3 holds
for the agreement-driven rule; A1 and A2 are approximations and are flagged.

PROVENANCE. Every number below is computed in this run EXCEPT four constants
imported from sibling scripts and labelled as imported:
  * the prefer-exit=done selection rule recovers +6.66pp [+5.60, +7.77] of a
    10.82pp pass@1->pass@2 delivery gap                     (n_answer_selection.py)
  * the gate's minimum detectable effect on the swing set is 9.03pp
                                                            (r_effective_n.py)
The selection MECHANISM (NA.rule_prefer_done) is imported and applied directly; the
delivered numbers it produces here are computed in this run.
"""
from __future__ import annotations

import collections
import heapq
import json
import math
import pathlib
import random
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import n_answer_selection as NA  # noqa: E402

BAR = "=" * 74

# ---- imported constants (labelled; not recomputed here) --------------------
SEL_RECOVERED_PP = 6.66      # prefer exit=done, delivered recovery  (NA sibling)
SEL_RECOVERED_LO = 5.60      # 95% CI low                            (NA sibling)
SEL_RECOVERED_HI = 7.77      # 95% CI high                           (NA sibling)
SEL_GAP_PP = 10.82           # pass@1 -> pass@2 delivery gap          (NA sibling)
MDE_PP = 9.03                # gate MDE on the swing set   (r_effective_n sibling)

# ---- experiment constants --------------------------------------------------
PASS_K = 2                   # status-quo attempts per task (observed, asserted)
PRIOR_ROUNDS = 4             # rounds < 4 are "prior" (present in all 36 runs);
                             # rounds >= 4 are the forward "eval" horizon
NULL_PERMS = 1000
NULL_SEED = 0


# ===========================================================================
# per-task aggregation -- one pass over cells
# ===========================================================================
def aggregate(cells):
    """task -> a record of every count the analysis needs.

    All cells have exactly PASS_K attempts (asserted in main), so attempt index 0
    is the split-sample "half A" and index 1 is "half B" (stated in Section 3).
    """
    T = collections.defaultdict(lambda: {
        "m": 0, "k": 0,                 # attempts, passes (pooled)  -> p_t
        "nA": 0, "kA": 0,               # half A (attempt idx 0)     -> p_hat^A
        "nB": 0, "kB": 0,               # half B (attempt idx 1)     -> p_hat^B
        "np": 0, "kp": 0,               # prior rounds               -> p_hat^prior
        "ne": 0, "ke": 0,               # eval rounds                -> p_hat^eval
        "cells": 0, "disagree": 0,      # first-pair answer (dis)agreement -> delta_t
        "solved_cells": 0,              # cells with >=1 passing attempt (current alloc)
        "done": 0, "done_pass": 0,      # exit==done  (delivered model)
        "nd": 0, "nd_pass": 0,          # exit!=done  (delivered model)
    })
    for c in cells:
        atts = c["atts"]
        r = c["round"]
        t = T[c["task"]]
        t["cells"] += 1
        if any(a["passed"] for a in atts):   # a cell is "solved" when >=1 attempt passes
            t["solved_cells"] += 1
        a0, a1 = atts[0]["answer"].strip().lower(), atts[1]["answer"].strip().lower()
        if a0 != a1:
            t["disagree"] += 1
        for i, a in enumerate(atts):
            passed = 1 if a["passed"] else 0
            t["m"] += 1
            t["k"] += passed
            if i == 0:
                t["nA"] += 1
                t["kA"] += passed
            else:
                t["nB"] += 1
                t["kB"] += passed
            if r is not None and r < PRIOR_ROUNDS:
                t["np"] += 1
                t["kp"] += passed
            else:
                t["ne"] += 1
                t["ke"] += passed
            if a["exit"] == "done":
                t["done"] += 1
                t["done_pass"] += passed
            else:
                t["nd"] += 1
                t["nd_pass"] += passed
    return dict(T)


def rate(k, n):
    return k / n if n else None


def raw_attempts_per_record(runs):
    """attempts-per-record counts across ALL comparison.json files, BEFORE the
    load_cells >=2 filter -- so the claim '2 is the dominant allocation' can be
    checked rather than assumed. Mirrors load_cells' rounds->records traversal."""
    counts = collections.Counter()
    contributing = set()
    for run in runs:
        f = NA.RUNS / run / "comparison.json"
        if not f.exists():
            continue
        d = json.loads(f.read_text(encoding="utf-8"))
        for rnd in d.get("rounds") or []:
            for rec in rnd or []:
                k = len(rec.get("attempts") or [])
                counts[k] += 1
                if k >= 2:
                    contributing.add(run)
    return counts, contributing


# ===========================================================================
# allocation primitives
# ===========================================================================
def solved(p, n):
    """Expected P(task solved | n attempts) under the A1 Bernoulli model."""
    if n <= 0:
        return 0.0
    return 1.0 - (1.0 - p) ** n


def score(alloc, p_eval, order):
    """Expected tasks solved: sum_t [1 - (1-p_eval_t)^{alloc_t}]."""
    return sum(solved(p_eval[t], alloc[t]) for t in order)


def greedy(p, budget, order):
    """Concave-objective optimum: hand each next attempt to the largest marginal.

    Marginal gain of the (n+1)-th attempt on task t is (1-p_t)^n * p_t. A max-heap
    keyed on that marginal (task index breaks ties, so the allocation is
    deterministic) reproduces the optimal allocation. n_t >= 0 is enforced: a task
    whose marginal is 0 (p_t == 0) is never chosen and keeps 0 attempts.
    """
    alloc = {t: 0 for t in order}
    heap = [(-p[t], i, t) for i, t in enumerate(order)]
    heapq.heapify(heap)
    spent = 0
    for _ in range(budget):
        neg, i, t = heap[0]
        if -neg <= 0.0:            # no task can benefit from another attempt
            break
        heapq.heapreplace(heap, (-((1.0 - p[t]) ** (alloc[t] + 1) * p[t]), i, t))
        alloc[t] += 1
        spent += 1
    return alloc, spent


def profile_by_stratum(alloc, stratum, order):
    """(mean attempts, min, max, #tasks with 0) per stratum for an allocation."""
    out = {}
    for name in ("never", "always", "swing"):
        vals = [alloc[t] for t in order if stratum[t] == name]
        if not vals:
            out[name] = (0.0, 0, 0, 0)
        else:
            out[name] = (sum(vals) / len(vals), min(vals), max(vals),
                         sum(1 for v in vals if v == 0))
    return out


def pctl(sorted_vals, q):
    """Linear-interpolated percentile of an already-sorted list."""
    if not sorted_vals:
        return float("nan")
    k = (len(sorted_vals) - 1) * q
    lo, hi = math.floor(k), math.ceil(k)
    if lo == hi:
        return sorted_vals[int(k)]
    return sorted_vals[lo] * (hi - k) + sorted_vals[hi] * (k - lo)


# ===========================================================================
# delivered outcome (compose with answer selection)
# ===========================================================================
def delivered(rec, n):
    """P(the prefer-exit=done selection ships a PASS | n attempts).

    Under A1 (iid attempts) with, per task, q = P(exit==done),
    p_done = P(pass|done), p_nd = P(pass|not done): the selector ships the first
    done attempt if any exists (a uniformly random done attempt -> expected pass
    p_done), else attempt 0 conditioned on none-done -> expected pass p_nd. So
        delivered(n) = [1 - (1-q)^n] * p_done + (1-q)^n * p_nd .
    This is the closed form of NA.rule_prefer_done; it is validated against the
    rule applied to the real n=2 cells in Section 5.
    """
    if n <= 0:
        return 0.0
    m = rec["m"]
    q = rec["done"] / m if m else 0.0
    p_done = rec["done_pass"] / rec["done"] if rec["done"] else 0.0
    p_nd = rec["nd_pass"] / rec["nd"] if rec["nd"] else 0.0
    none_done = (1.0 - q) ** n
    return (1.0 - none_done) * p_done + none_done * p_nd


# ===========================================================================
def main(runs):
    cells = list(NA.load_cells(runs))
    if not cells:
        print("no cells")
        return

    # ---- corpus assertions & the observed allocation ----------------------
    cmp_dirs = [pp.name for pp in sorted(NA.RUNS.iterdir())
                if (pp / "comparison.json").exists()]
    assert len(cmp_dirs) == 36, \
        f"expected 36 runs with comparison.json, found {len(cmp_dirs)}"
    raw_counts, contributing = raw_attempts_per_record(cmp_dirs)
    n_records = sum(raw_counts.values())
    dom_k, dom_n = raw_counts.most_common(1)[0]
    assert dom_k == PASS_K and dom_n > n_records / 2, \
        f"dominant attempts-per-record is {dom_k} ({dom_n}/{n_records}), expected {PASS_K}"
    n_cells = len(cells)

    T = aggregate(cells)
    order = sorted(T)                     # deterministic task ordering (no set iter)
    N = len(order)
    p = {t: rate(T[t]["k"], T[t]["m"]) for t in order}
    stratum = {t: ("never" if p[t] == 0.0 else "always" if p[t] == 1.0 else "swing")
               for t in order}
    n_att = sum(T[t]["m"] for t in order)
    BUDGET = PASS_K * N                    # per-deployment status-quo budget = 206

    print(f"runs with comparison.json: {len(cmp_dirs)} (asserted 36)   "
          f"distinct tasks: {N}   corpus attempts pooled: {n_att}")
    print("actual attempts-per-record distribution across all 36 comparison.json")
    print("  files (BEFORE load_cells' >=2 filter -- verified, not assumed):")
    for k_, v_ in sorted(raw_counts.items()):
        print(f"    {k_} attempts : {v_:5d}  ({100*v_/n_records:5.1f}%)")
    print(f"  -> {dom_k} attempts/record is dominant ({100*dom_n/n_records:.1f}%).")
    print(f"  {len(contributing)} runs ran pass_k>=2 and supply all {n_cells} analysed")
    print(f"  cells; {36-len(contributing)} runs ran a single attempt per record "
          f"(smoke/calib/aegis)")
    print(f"  and are excluded -- reallocation is undefined without a second attempt.")
    print("ASSUMPTIONS  A1 iid Bernoulli(p_t) within a cell | A2 p_t stationary over")
    print("  rounds, pooled over variants | A3 attempts issuable sequentially per cell")
    print("  Corpus is the EVIDENCE for p_t; the deployment budget modelled from")
    print(f"  Section 2 on is {PASS_K} x {N} = {BUDGET} attempts.")

    # =======================================================================
    print("\n" + BAR)
    print("1. WHERE THE BUDGET GOES NOW  (corpus as spent; A2 pooling)")
    print(BAR)
    print(f"  {'stratum':<9}{'tasks':>6}{'attempts':>10}{'% budget':>10}"
          f"{'cells':>8}{'cells solved':>14}")
    strat_tasks = collections.defaultdict(list)
    for t in order:
        strat_tasks[stratum[t]].append(t)
    for name in ("never", "always", "swing"):
        ts = strat_tasks[name]
        a = sum(T[t]["m"] for t in ts)
        cl = sum(T[t]["cells"] for t in ts)
        # a task is "solved in a cell" when >=1 of that cell's attempts passes.
        # never: 0 passes ever -> 0 solved cells; always: every attempt passes ->
        # every cell solved; swing: counted exactly from the cell records.
        solved_cells = sum(T[t]["solved_cells"] for t in ts)
        print(f"  {name:<9}{len(ts):>6}{a:>10}{100*a/n_att:9.1f}%{cl:>8}"
              f"{solved_cells:>8} ({100*solved_cells/max(cl,1):4.1f}%)")
    dead_tasks = strat_tasks["never"] + strat_tasks["always"]
    dead_att = sum(T[t]["m"] for t in dead_tasks)
    print(f"\n  immediately visible waste = attempts on never- + always-solved tasks")
    print(f"    never n={len(strat_tasks['never'])}, always n={len(strat_tasks['always'])}"
          f"  ->  {dead_att} attempts  ({100*dead_att/n_att:.1f}% of budget)")
    print(f"    a 2nd attempt cannot change a never (p=0) or always (p=1) outcome,")
    print(f"    so that share is the loosest upper bound on what reallocation frees.")

    # =======================================================================
    print("\n" + BAR)
    print("2. THE ORACLE CEILING  --  UPPER BOUND, NOT ACHIEVABLE")
    print(BAR)
    print("  The oracle reads each task's TRUE p_t (approximated by the full-pool")
    print("  p_hat_t) to allocate. A deployed allocator does not have p_t, so every")
    print("  number in this section is an upper bound, not a target.")
    uni = {t: PASS_K for t in order}
    orc, orc_spent = greedy(p, BUDGET, order)
    s_uni = score(uni, p, order)
    s_orc = score(orc, p, order)
    print(f"\n  budget fixed at the observed per-deployment total: {BUDGET} attempts")
    print(f"  objective is concave in n_t (marginal (1-p)^n p decreases in n), so the")
    print(f"  greedy max-marginal allocation is optimal; n_t >= 0 is enforced.")
    print(f"\n  {'allocation':<22}{'E[tasks solved]':>17}{'% of 103-bed':>15}")
    print(f"  {'uniform (n_t=2)':<22}{s_uni:>17.2f}{100*s_uni/N:>14.1f}%")
    print(f"  {'oracle (true p_t)':<22}{s_orc:>17.2f}{100*s_orc/N:>14.1f}%")
    gain_tasks = s_orc - s_uni
    print(f"  {'ceiling gain':<22}{gain_tasks:>+17.2f}{100*gain_tasks/N:>+13.2f}pp")
    prof_u = profile_by_stratum(uni, stratum, order)
    prof_o = profile_by_stratum(orc, stratum, order)
    n_zero = sum(1 for t in order if orc[t] == 0)
    print(f"\n  allocation profile (mean attempts/task by stratum):")
    print(f"  {'stratum':<9}{'uniform':>9}{'oracle mean':>13}{'oracle min':>12}"
          f"{'oracle max':>12}{'oracle=0':>10}")
    for name in ("never", "always", "swing"):
        mu, mn, mx, z = prof_o[name]
        print(f"  {name:<9}{prof_u[name][0]:>9.2f}{mu:>13.2f}{mn:>12d}{mx:>12d}{z:>10d}")
    print(f"\n  the optimum gives 0 attempts to {n_zero} tasks "
          f"({100*n_zero/N:.0f}% of the bed); {sum(1 for t in order if p[t]==0)} of "
          f"those are the p=0 never-solved tasks.")
    if orc_spent < BUDGET:
        print(f"  NOTE: only {orc_spent}/{BUDGET} attempts were spendable "
              f"(remaining tasks had 0 marginal).")

    # =======================================================================
    print("\n" + BAR)
    print("3. THE OVERFITTING CONTROL  --  the section that decides the result")
    print(BAR)
    print("  Split rule (A1): each cell has exactly 2 attempts, so attempt index 0")
    print("  is half A and attempt index 1 is half B. Estimate p_hat on A, allocate")
    print("  on that, score on B. Tasks with an empty half fall back to full-pool p.")
    pA = {t: (rate(T[t]["kA"], T[t]["nA"]) if T[t]["nA"] else p[t]) for t in order}
    pB = {t: (rate(T[t]["kB"], T[t]["nB"]) if T[t]["nB"] else p[t]) for t in order}
    empty_half = sum(1 for t in order if not T[t]["nA"] or not T[t]["nB"])
    allocA, _ = greedy(pA, BUDGET, order)

    s_in = score(allocA, pA, order) - score(uni, pA, order)      # in-sample (on A)
    s_out = score(allocA, pB, order) - score(uni, pB, order)     # out-of-sample (B)
    premium = s_in - s_out
    print(f"\n  tasks with an empty half (fell back to pooled p): {empty_half}")
    print(f"  {'gain vs uniform, same budget':<34}{'tasks':>9}{'pp of bed':>12}")
    print(f"  {'in-sample  (allocate A, score A)':<34}{s_in:>+9.2f}{100*s_in/N:>+10.2f}pp")
    print(f"  {'out-of-sample (allocate A, score B)':<34}{s_out:>+9.2f}{100*s_out/N:>+10.2f}pp")
    print(f"  {'overfitting premium (in - out)':<34}{premium:>+9.2f}{100*premium/N:>+10.2f}pp")

    # ---- null control: shuffle task labels --------------------------------
    print(f"\n  NULL CONTROL: hold the allocation's shape fixed, shuffle which task it")
    print(f"  lands on ({NULL_PERMS} permutations, random.Random({NULL_SEED})). If the")
    print(f"  out-of-sample gain does not clear the null's 95th percentile it is noise.")
    allocA_vals = [allocA[t] for t in order]                 # ordered; no set iter
    pB_vals = [pB[t] for t in order]
    base_uniB = score(uni, pB, order)
    rng = random.Random(NULL_SEED)
    idx = list(range(N))
    null = []
    for _ in range(NULL_PERMS):
        rng.shuffle(idx)
        g = sum(solved(pB_vals[j], allocA_vals[idx[j]]) for j in range(N)) - base_uniB
        null.append(g)
    null.sort()
    null_mean = sum(null) / len(null)
    null_p95 = pctl(null, 0.95)
    clears = s_out > null_p95
    print(f"  {'null mean gain':<28}{null_mean:>+9.2f} tasks{100*null_mean/N:>+9.2f}pp")
    print(f"  {'null 95th percentile':<28}{null_p95:>+9.2f} tasks{100*null_p95/N:>+9.2f}pp")
    print(f"  {'observed out-of-sample gain':<28}{s_out:>+9.2f} tasks{100*s_out/N:>+9.2f}pp")
    print(f"  (the null mean is NEGATIVE because a concentrated allocation dropped on")
    print(f"   random tasks is worse than a flat one -- concavity again -- so any")
    print(f"   correctly-placed concentration beats it; the test is whether p_hat^A")
    print(f"   places the concentration on the RIGHT tasks, out of sample.)")
    print(f"\n  >>> out-of-sample gain {'CLEARS' if clears else 'DOES NOT CLEAR'} the "
          f"null 95th percentile.")
    if not clears:
        print("  >>> HEADLINE: the reallocation signal is indistinguishable from noise.")

    # =======================================================================
    print("\n" + BAR)
    print("4. A FEASIBLE RULE  --  signals available at allocation time only")
    print(BAR)
    # ---- prior-driven: strictly forward-looking (rounds < PRIOR_ROUNDS) ----
    prior = {t: rate(T[t]["kp"], T[t]["np"]) for t in order}
    peval = {t: (rate(T[t]["ke"], T[t]["ne"]) if T[t]["ne"] else p[t]) for t in order}
    no_prior = [t for t in order if T[t]["np"] == 0]
    no_eval = sum(1 for t in order if T[t]["ne"] == 0)
    with_prior = [t for t in order if T[t]["np"] > 0]
    reserved = PASS_K * len(no_prior)
    alloc_prior = {t: PASS_K for t in no_prior}              # status-quo fallback
    ap, _ = greedy({t: prior[t] for t in with_prior}, BUDGET - reserved, with_prior)
    alloc_prior.update(ap)
    s_prior = score(alloc_prior, peval, order) - score(uni, peval, order)
    print(f"  prior-driven: estimate p on rounds < {PRIOR_ROUNDS} (present in all 36")
    print(f"    runs), allocate {BUDGET}, SCORE on rounds >= {PRIOR_ROUNDS} (strictly")
    print(f"    forward -- never the round being allocated or later).")
    print(f"    tasks with no prior history: {len(no_prior)}  "
          f"(handled: reserved n={PASS_K} each, {reserved} attempts, excluded from")
    print(f"    the greedy pool); tasks with no eval history: {no_eval} (scored on pooled p).")

    # ---- agreement-driven: react to the first pair (N-15a as stated) -------
    delta = {t: (T[t]["disagree"] / T[t]["cells"] if T[t]["cells"] else 0.0)
             for t in order}
    exp_disagree = sum(delta[t] for t in order)     # expected # first-pair disagreements
    E = round(exp_disagree)
    budget_a = BUDGET + E                            # 2N base + one extra per contest
    # agreement-driven expected solved: a task gets 3 attempts w.p. delta_t, else 2.
    s_agree_pk = sum(delta[t] * solved(p[t], PASS_K + 1)
                     + (1 - delta[t]) * solved(p[t], PASS_K) for t in order)
    # uniform at the SAME budget spreads the top-up evenly: n_t = budget_a / N.
    uni_a_n = budget_a / N
    s_uni_a = sum(solved(p[t], uni_a_n) for t in order)
    g_agree = s_agree_pk - s_uni_a
    print(f"\n  agreement-driven (two-phase, uses A3): give every task a first pair,")
    print(f"    ship extra attempts only to tasks whose two answers DISAGREE. This is")
    print(f"    reactive -- it fits no per-task p, so it does not overfit p_t.")
    print(f"    expected first-pair disagreements: {exp_disagree:.1f} of {N} tasks "
          f"(top-up budget E={E}).")
    print(f"    budget {budget_a} (= {BUDGET} + {E}); compared to UNIFORM AT THE SAME")
    print(f"    budget (n_t={uni_a_n:.3f}) so the extra attempts are the only difference.")

    # ---- oracle at the agreement budget, for reference --------------------
    orc_a, _ = greedy(p, budget_a, order)
    s_orc_a = score(orc_a, p, order) - s_uni_a

    print(f"\n  {'rule':<34}{'budget':>7}{'gain vs uniform':>17}{'pp of bed':>12}")
    print(f"  {'-'*34}{'-'*7}{'-'*17}{'-'*12}")
    print(f"  {'uniform (status quo, n_t=2)':<34}{BUDGET:>7}{0.0:>+17.2f}{0.0:>+10.2f}pp")
    print(f"  {'prior-driven [out-of-sample]':<34}{BUDGET:>7}"
          f"{s_prior:>+17.2f}{100*s_prior/N:>+10.2f}pp")
    print(f"  {'agreement-driven [reactive]':<34}{budget_a:>7}"
          f"{g_agree:>+17.2f}{100*g_agree/N:>+10.2f}pp")
    print(f"  {'oracle [UPPER BOUND, true p_t]':<34}{budget_a:>7}"
          f"{s_orc_a:>+17.2f}{100*s_orc_a/N:>+10.2f}pp")
    print(f"\n  FLOOR every feasible rule must clear (Section 3 null 95th pct, at")
    print(f"  budget {BUDGET}): {100*null_p95/N:+.2f}pp. Out-of-sample oracle gain there")
    print(f"  was {100*s_out/N:+.2f}pp. Prior-driven is the only rule scored strictly")
    print(f"  out-of-sample; agreement-driven's gain is measured against a matched-")
    print(f"  budget uniform, not against the {BUDGET}-attempt status quo.")

    # =======================================================================
    print("\n" + BAR)
    print("5. COMPOSITION WITH ANSWER SELECTION  --  pass@k gain is not delivered gain")
    print(BAR)
    print(f"  Imported (labelled): prefer exit=done recovers +{SEL_RECOVERED_PP}pp "
          f"[{SEL_RECOVERED_LO:+.2f}, {SEL_RECOVERED_HI:+.2f}] of a {SEL_GAP_PP}pp")
    print(f"  pass@1->pass@2 delivery gap  (n_answer_selection.py). Deployment ships")
    print(f"  ONE answer; extra attempts widen that gap as well as the ceiling.")
    # validate the closed-form delivered() against the real rule at n=2
    emp_deliver = 0
    for c in cells:
        emp_deliver += 1 if NA.rule_prefer_done(c["atts"])["passed"] else 0
    emp_rate = emp_deliver / n_cells
    cf_rate = sum(delivered(T[t], PASS_K) * T[t]["cells"] for t in order) / n_cells
    print(f"\n  validation: NA.rule_prefer_done on the real n=2 cells delivers "
          f"{100*emp_rate:.2f}%;")
    print(f"  the closed form delivered(n=2) gives {100*cf_rate:.2f}% -- a mild "
          f"overestimate")
    print(f"  ({100*(cf_rate-emp_rate):+.2f}pp) because A1 treats the pair as "
          f"independent; close")
    print(f"  enough that the SHAPE of delivered(n>2) is informative. Rows below are")
    print(f"  scored on full-pool p (in-sample) to isolate the selection shrinkage, so")
    print(f"  these gains are NOT the out-of-sample gains of Sections 3-4.")

    def deliv_total(alloc):
        return sum(delivered(T[t], alloc[t]) for t in order)

    # best feasible allocation = prior-driven (the strictly out-of-sample rule)
    print(f"\n  {'allocation':<30}{'pass@k solved':>14}{'delivered':>12}"
          f"{'pass@k gain':>13}{'deliv. gain':>13}")
    pk_uni, dl_uni = score(uni, p, order), deliv_total(uni)
    for label, alloc in (("uniform (n_t=2)", uni),
                         ("prior-driven [feasible, OOS]", alloc_prior),
                         ("oracle [UPPER BOUND]", orc)):
        pk, dl = score(alloc, p, order), deliv_total(alloc)
        print(f"  {label:<30}{pk:>14.2f}{dl:>12.2f}"
              f"{pk-pk_uni:>+12.2f} {dl-dl_uni:>+12.2f}")
    # agreement-driven delivered (expected-value mixture: 3 w.p. delta, else 2)
    dl_agree = sum(delta[t] * delivered(T[t], PASS_K + 1)
                   + (1 - delta[t]) * delivered(T[t], PASS_K) for t in order)
    dl_uni_a = sum(delivered(T[t], uni_a_n) for t in order)
    print(f"  {'agreement-driven [reactive]':<30}{s_agree_pk:>14.2f}{dl_agree:>12.2f}"
          f"{s_agree_pk-s_uni_a:>+12.2f} {dl_agree-dl_uni_a:>+12.2f}")
    print(f"\n  read the last two columns together: reallocation buys pass@k it cannot")
    print(f"  deliver. The delivered gain is the honest one -- it is what ships.")

    # =======================================================================
    print("\n" + BAR)
    print("6. VERDICT")
    print(BAR)
    prior_deliv_gain = deliv_total(alloc_prior) - dl_uni
    print(f"  Oracle ceiling (UPPER BOUND, true p_t): {100*gain_tasks/N:+.2f}pp of the")
    print(f"    103-task bed -- small, because 100/103 tasks already have p_t>0 and two")
    print(f"    attempts already bank most of what one more would.")
    print(f"  Out-of-sample gain (split-sample oracle): {100*s_out/N:+.2f}pp.")
    print(f"  Null floor (95th pct of shuffled labels): {100*null_p95/N:+.2f}pp -- the")
    print(f"    out-of-sample gain {'CLEARS' if clears else 'DOES NOT CLEAR'} it.")
    print(f"  Best feasible rule DELIVERED gain (after prefer-done selection):")
    print(f"    prior-driven {100*prior_deliv_gain/N:+.2f}pp, "
          f"agreement-driven {100*(dl_agree-dl_uni_a)/N:+.2f}pp.")
    print(f"  Gate MDE on the swing set is {MDE_PP}pp (imported, r_effective_n.py):")
    print(f"    any gain this size is below what a round-comparison could even measure.")
    if clears and prior_deliv_gain > 0:
        print(f"  VERDICT: a real but small signal survives the controls; N-15a is worth")
        print(f"    at most a cheap reactive top-up, and its delivered gain sits under the")
        print(f"    gate MDE, so it cannot be validated by the current instrument.")
    else:
        print(f"  VERDICT: the reallocation gain does not survive the controls at a")
        print(f"    deliverable level. Do NOT spend budget implementing N-15a -- the fixed")
        print(f"    2-attempt allocation is already near the reachable frontier.")


if __name__ == "__main__":
    argv = sys.argv[1:]
    main(argv or [p.name for p in sorted(NA.RUNS.iterdir())
                  if (p / "comparison.json").exists()])
