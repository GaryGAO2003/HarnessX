# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""Was the cap-40 vs cap-20 contrast ever a two-sample comparison?

One run, s1k8b103, opened its first round at a 40-step cap and then ran its
fourteen remaining rounds -- and every round of every other run -- at 20. An
earlier note read the gap between those two regimes as evidence that a larger
step budget buys accuracy and put a number on it: +24.6pp at z=4.7, and then a
task-paired bootstrap of +24.2pp. This file asks whether that comparison was
ever admissible, and the answer turns out to be a bookkeeping question before it
is a statistical one.

The bookkeeping comes first. The step cap is a property of the (run, round), not
of the run: reading it off the run alone tags all fourteen cap-20 rounds of
s1k8b103 as cap-40 and drops them from the control regime they belong to.
Section 1 resolves the cap per (run, round) -- the largest step at which a
budget_exceeded attempt was cut off -- and states, as a correction to
q_budget_censoring.caps_per_run, how many attempts the coarser per-run rule
mis-files and which block (s1k8b103 R2..R15) it wrongly excludes.

The statistics come second. The cap-40 regime is a single (run, round) cell.
Every attempt in it shares that cell's model, prompt, seed schedule and round
position, so the attempts are not independent draws from a "cap-40 population":
there is one cluster, and n is 1 in the unit that matters. Section 2 counts the
clusters on each arm, measures how strong the within-cluster dependence is on the
cap-20 side through an intra-cluster correlation and the design effect it
implies, and runs a cluster bootstrap that resamples whole (run, round) cells.
On the treated arm that bootstrap returns the identical arm every draw, so its
interval has zero width by construction -- shown numerically, not asserted. The
+24.6pp and the paired interval are retracted here in the voice the sibling
Kaplan-Meier section uses, and must not be reinstated.

What is left is description rather than inference. Section 3 counts -- with no
estimator -- how far past step 20 the corpus ever ran and how many of those
attempts passed, then restates the cap-20 arm's success quantiles and its
paired-twin distances under the corrected regime. Section 4 isolates the one
quantity a downstream censoring model actually needs: r, the probability that an
attempt still running at step 20 would eventually pass, identified only by
s1k8b103's forty-nine survivors. It is reported with a Wilson interval and,
because all forty-nine sit in one cell, carried as a sensitivity parameter rather
than a point value.
"""
from __future__ import annotations

import collections
import math
import pathlib
import random
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import n_answer_selection as NA  # noqa: E402
import q_budget_censoring as QB  # noqa: E402

BAR = "=" * 74
Z95 = 1.959963984540054  # standard normal 0.975 quantile, for the Wilson interval
CELL_40 = ("s1k8b103", "1")  # the sole cap-40 (run, round); asserted below
BANDS = (("21-25", 21, 25), ("26-30", 26, 30), ("31-40", 31, 40))


# --------------------------------------------------------------- data helpers
def runs_with_comparison():
    """The default corpus: every run under RUNS that carries a comparison.json."""
    return [p.name for p in sorted(NA.RUNS.iterdir())
            if (p / "comparison.json").exists()]


def rr(cell):
    """The (run, round) key. Round is stringified so int/str drift cannot split it."""
    return (cell["run"], str(cell["round"]))


def resolve_caps(cells):
    """Operative cap for each (run, round): the max step of a budget_exceeded
    attempt in that cell, 0 if the cell never hit the budget.

    This is the per-(run, round) resolution. The point of the whole file is that
    it is NOT the per-run resolution: one run holds two caps across its rounds.
    """
    caps = collections.defaultdict(int)
    for c in cells:
        for a in c["atts"]:
            if a["exit"] == "budget_exceeded" and a["steps"] is not None:
                caps[rr(c)] = max(caps[rr(c)], int(a["steps"]))
    for c in cells:                                   # cells with no budget hit
        caps.setdefault(rr(c), 0)
    return dict(caps)


def twins_steps(cells, keep):
    """Passing-twin step counts for cells that `keep` accepts.

    A twin pair is a (run, round, variant, task) cell holding at least one
    budget_exceeded attempt and at least one attempt that passed with
    exit=done; one entry is emitted per passing-done attempt, matching the
    convention in q_budget_censoring.collect.
    """
    out = []
    for c in cells:
        if not keep(c):
            continue
        if not any(a["exit"] == "budget_exceeded" for a in c["atts"]):
            continue
        for a in c["atts"]:
            if a["passed"] and a["exit"] == "done" and a["steps"] is not None:
                out.append(int(a["steps"]))
    return out


def quantile(sorted_vals, q):
    """The q-quantile by the same index rule q_budget_censoring uses."""
    return sorted_vals[min(int(q * len(sorted_vals)), len(sorted_vals) - 1)]


# --------------------------------------------------------------- estimators
def wilson(k, n, z=Z95):
    """Wilson score interval for a binomial proportion. No scipy: the 0.975
    normal quantile is the only constant, and it is spelled out at module top."""
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    z2 = z * z
    denom = 1.0 + z2 / n
    centre = (p + z2 / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z2 / (4 * n * n)) / denom
    return (centre - half, centre + half)


def icc_oneway(groups):
    """One-way ANOVA intra-cluster correlation of a 0/1 outcome.

    ICC = (MSB - MSW) / (MSB + (mbar-1)*MSW), mbar the mean cluster size,
    clamped at 0. Returns the pieces plus design effect 1+(mbar-1)*ICC and the
    effective n = N / design effect.
    """
    k = len(groups)
    n_tot = sum(len(g) for g in groups)
    if k < 2 or n_tot <= k:
        return dict(k=k, n=n_tot, mbar=float(n_tot) / k if k else 0.0,
                    grand=0.0, msb=0.0, msw=0.0, icc=0.0, deff=1.0,
                    neff=float(n_tot))
    grand = sum(sum(g) for g in groups) / n_tot
    mbar = n_tot / k
    means = [sum(g) / len(g) for g in groups]
    ssb = sum(len(g) * (m - grand) ** 2 for g, m in zip(groups, means))
    ssw = sum((y - m) ** 2 for g, m in zip(groups, means) for y in g)
    msb = ssb / (k - 1)
    msw = ssw / (n_tot - k)
    icc = max(0.0, (msb - msw) / (msb + (mbar - 1) * msw)) if (msb or msw) else 0.0
    deff = 1.0 + (mbar - 1) * icc
    return dict(k=k, n=n_tot, mbar=mbar, grand=grand, msb=msb, msw=msw,
                icc=icc, deff=deff, neff=n_tot / deff if deff else float(n_tot))


def cluster_bootstrap_rate(clusters, b=2000, seed=0):
    """Resample whole clusters with replacement; return the pooled pass-rate's
    (min, max, 2.5%, 97.5%) across `b` draws. With one cluster every draw is the
    same arm, so min==max and the interval is a point -- which is the finding for
    the treated arm, not a bug."""
    keys = list(clusters)
    rng = random.Random(seed)
    rates = []
    for _ in range(b):
        pick = [rng.choice(keys) for _ in keys]
        ys = [y for key in pick for y in clusters[key]]
        rates.append(sum(ys) / len(ys))
    rates.sort()
    return rates[0], rates[-1], rates[int(0.025 * b)], rates[int(0.975 * b)]


def residual_success_estimate(runs) -> dict:
    """r = P(an attempt still running at step 20 eventually passes).

    Identified in this corpus only by the attempts that survived past step 20.
    Pure and cheap: one load pass, direct counts, a Wilson interval, no
    bootstrap. Importable so a sibling Schmee-Hahn imputation can pull the
    sensitivity parameter without re-deriving it.
    """
    n = k = 0
    cells_hit = set()
    for c in NA.load_cells(runs):
        for a in c["atts"]:
            if a["steps"] is not None and int(a["steps"]) > 20:
                n += 1
                k += 1 if a["passed"] else 0
                cells_hit.add((c["run"], str(c["round"])))
    lo, hi = wilson(k, n)
    return {"r_hat": (k / n) if n else 0.0, "wilson_lo": lo, "wilson_hi": hi,
            "n": n, "k": k, "clusters": len(cells_hit)}


# --------------------------------------------------------------- report
def main(runs):
    cells = list(NA.load_cells(runs))
    caps = resolve_caps(cells)

    # past-20 survivors (the only attempts that ever ran beyond the cap-20 line)
    past = [(int(a["steps"]), bool(a["passed"]))
            for c in cells for a in c["atts"]
            if a["steps"] is not None and int(a["steps"]) > 20]
    past_cells = {rr(c) for c in cells for a in c["atts"]
                  if a["steps"] is not None and int(a["steps"]) > 20}
    past_n = len(past)
    past_k = sum(1 for _, p in past if p)

    # per-(run, round) tallies
    fine_cells = collections.Counter(caps.values())
    fine_att = collections.Counter()
    for c in cells:
        fine_att[caps[rr(c)]] += sum(1 for a in c["atts"] if a["steps"] is not None)

    # per-run tallies, straight from the rule this file corrects
    att_qb, _ = QB.collect(runs)
    run_caps = QB.caps_per_run(att_qb)
    coarse_att = collections.Counter(run_caps[r] for *_, r in att_qb)

    # arms
    cap40_cells = [c for c in cells if caps[rr(c)] == 40]
    cap20_cells = [c for c in cells if caps[rr(c)] == 20]
    clusters40 = {rr(c) for c in cap40_cells}
    clusters20 = {rr(c) for c in cap20_cells}
    treat_out = [1 if a["passed"] else 0 for c in cap40_cells for a in c["atts"]]
    ctrl_out = [1 if a["passed"] else 0 for c in cap20_cells for a in c["atts"]]

    # the mis-assignment
    misassigned = fine_att[20] - coarse_att[20]
    s1_nonR1 = sum(1 for c in cells
                   if c["run"] == "s1k8b103" and str(c["round"]) != "1"
                   for a in c["atts"] if a["steps"] is not None)
    s1_nonR1_rounds = sorted({int(c["round"]) for c in cells
                              if c["run"] == "s1k8b103" and str(c["round"]) != "1"})

    # twins, both resolutions
    fine_twins = twins_steps(cells, lambda c: caps[rr(c)] == 20)
    coarse_twins = twins_steps(cells, lambda c: run_caps.get(c["run"], 0) == 20)

    is_full = set(runs) == set(runs_with_comparison())

    # -------- assert guards: the verified facts, so silent drift fails loud ----
    assert past_cells <= {CELL_40}, \
        f"attempts past step 20 outside {CELL_40}: {past_cells - {CELL_40}}"
    assert all(v in (0, 20) or (key == CELL_40 and v == 40)
               for key, v in caps.items()), \
        f"unexpected per-(run,round) cap: {[(k, v) for k, v in caps.items() if v not in (0, 20)]}"
    assert misassigned == s1_nonR1, (misassigned, s1_nonR1)
    if "s1k8b103" in runs:
        assert caps.get(CELL_40) == 40, caps.get(CELL_40)
        assert len(clusters40) == 1, sorted(clusters40)
        assert fine_att[40] == 206, fine_att[40]
        assert past_n == 49 and past_k == 25, (past_n, past_k)
        assert misassigned == 2884, misassigned          # s1k8b103 R2..R15
    if is_full:
        assert len(runs) == 36, len(runs)
        assert fine_att[20] == 6524, fine_att[20]
        assert coarse_att[20] == 3640, coarse_att[20]
        assert coarse_att[40] == 3090, coarse_att[40]
        assert len(coarse_twins) == 194, len(coarse_twins)

    n = len(cells)
    print(f"cells (run x round x variant x task, >=2 attempts): {n}")
    print(f"runs: {len(set(c['run'] for c in cells))}"
          f"    full corpus: {is_full}\n")

    if not cap40_cells or not cap20_cells:
        print("This file settles a cap-40 vs cap-20 comparison; the given run set")
        print(f"lacks one regime (cap-40 cells: {len(clusters40)}, "
              f"cap-20 cells: {len(clusters20)}). Run the default full corpus.")
        return

    # ---------------------------------------------------------------- Section 1
    print(BAR)
    print("SECTION 1 -- REGIME RESOLUTION (a bookkeeping correction)")
    print(BAR)
    print("  The cap is resolved per (run, round) as the largest step at which a")
    print("  budget_exceeded attempt was cut off (0 if the cell never hit it).")
    print()
    print(f"  {'cap':>5}{'(run,round) cells':>20}{'attempts':>12}")
    print(f"  {'-'*5}{'-'*20}{'-'*12}")
    for cp in sorted(fine_cells):
        print(f"  {cp:>5}{fine_cells[cp]:>20}{fine_att[cp]:>12}")
    print(f"  {'all':>5}{sum(fine_cells.values()):>20}{sum(fine_att.values()):>12}")
    print()
    print("  Correction to the per-run resolution in q_budget_censoring.caps_per_run.")
    print("  That rule reads the cap off the RUN, so s1k8b103's cap-40 first round")
    print("  sets the whole run to cap-40 and its fourteen cap-20 rounds are pushed")
    print("  out of the cap-20 regime they belong to. Attempts in the cap-20 regime:")
    print(f"    per (run, round)  [correct]  : {fine_att[20]:>5}")
    print(f"    per run           [coarser]  : {coarse_att[20]:>5}")
    print(f"    mis-filed by the per-run rule: {misassigned:>5}"
          f"   = s1k8b103 R{s1_nonR1_rounds[0]}..R{s1_nonR1_rounds[-1]}"
          f" ({len(s1_nonR1_rounds)} rounds)")
    print("  The per-run rule also inflates the cap-40 regime to"
          f" {coarse_att[40]} attempts")
    print(f"  when only {fine_att[40]} attempts ever actually ran at cap-40.")
    print("  This is a counting fix, not a new finding. Every distance-from-cap")
    print("  statement below is computed inside the per-(run, round) regime.")

    # ---------------------------------------------------------------- Section 2
    print("\n" + BAR)
    print("SECTION 2 -- THE CAP-40 CONTRAST IS SINGLE-CLUSTER")
    print(BAR)
    t_rate = sum(treat_out) / len(treat_out)
    c_rate = sum(ctrl_out) / len(ctrl_out)
    print("  Independent clusters are (run, round) cells. Per arm:")
    print(f"    cap-40 (treated) : {len(clusters40):>2} cluster(s)"
          f"   {len(treat_out):>5} attempts   pass {t_rate:.4f}")
    print(f"    cap-20 (control) : {len(clusters20):>2} clusters "
          f"   {len(ctrl_out):>5} attempts   pass {c_rate:.4f}")
    print("  ASSUMPTION MADE EXPLICIT: a two-sample test treats the arms as")
    print("  independent draws. The treated arm is ONE cell -- s1k8b103 R1 -- so")
    print("  its attempts share one model, prompt, seed schedule and round slot.")
    print("  There is no second cap-40 cluster to vary against.")

    groups20 = collections.defaultdict(list)   # (run, round) -> 0/1 pass outcomes
    for c in cap20_cells:
        groups20[rr(c)].extend(1 if a["passed"] else 0 for a in c["atts"])
    icc = icc_oneway(list(groups20.values()))
    print("\n  Within-cluster dependence on the cap-20 arm (ANOVA ICC of the binary")
    print("  pass outcome, clusters = (run, round) cells):")
    print(f"    clusters k = {icc['k']}   attempts N = {icc['n']}"
          f"   mean cluster size = {icc['mbar']:.1f}")
    print(f"    MSB = {icc['msb']:.5f}   MSW = {icc['msw']:.5f}"
          f"   ICC = {icc['icc']:.4f}")
    print(f"    design effect 1+(mbar-1)*ICC = {icc['deff']:.2f}"
          f"   effective n = {icc['neff']:.0f}  (nominal {icc['n']})")
    print("    Even the many-cluster arm carries this much variance inflation, so")
    print(f"    naive attempt-level standard errors are too small by ~{math.sqrt(icc['deff']):.2f}x.")

    cl40 = collections.defaultdict(list)
    for c in cap40_cells:
        cl40[rr(c)].extend(1 if a["passed"] else 0 for a in c["atts"])
    t_lo, t_hi, _, _ = cluster_bootstrap_rate(cl40)
    _, _, c_ci_lo, c_ci_hi = cluster_bootstrap_rate(groups20)
    print("\n  Cluster bootstrap (resample whole (run, round) cells, 2000 draws):")
    print(f"    treated arm ({len(cl40)} cluster) : rate in [{t_lo:.4f}, {t_hi:.4f}]"
          f"   width {t_hi - t_lo:.4f}")
    print("      -> UNDEFINED: with one cluster every resample returns the same")
    print("         arm, so the interval collapses to a point by construction.")
    print(f"    control arm ({len(groups20)} clusters, for contrast): "
          f"[{c_ci_lo:.4f}, {c_ci_hi:.4f}]  (non-degenerate)")

    print("\n" + BAR)
    print("CAP-40 vs CAP-20 CONTRAST  --  RETRACTED, DO NOT REINSTATE")
    print(BAR)
    print("  An earlier version of this analysis reported a cap-40 vs cap-20 gain")
    print("  of +24.6pp at z=4.7, and then a task-paired bootstrap of +24.2pp")
    print("  [+13.8, +35.0]. Both are retracted, on design grounds rather than by")
    print("  recomputation. The cap-40 arm is a single (run, round) cluster, so a")
    print("  z-test that counts its attempts as independent overstates precision")
    print("  by construction, and a cluster bootstrap of the arm has zero width")
    print("  (shown above). The task-paired bootstrap resamples tasks WITHIN that")
    print("  one cell and so assumes away the between-cluster variance that is the")
    print("  entire question; the sign test on the same pairs (p=0.4296) already")
    print("  declined to support the effect, which is the tell. There is exactly")
    print("  one cap-40 cluster in the corpus: no cap-40 vs cap-20 contrast is")
    print("  identified at the level that matters. A successor must obtain more")
    print("  than one cap-40 (run, round) before quoting any interval.")

    # ---------------------------------------------------------------- Section 3
    print("\n" + BAR)
    print("SECTION 3 -- WHAT SURVIVES WITHOUT INFERENCE (direct counts only)")
    print(BAR)
    band_stat = {name: [0, 0] for name, _, _ in BANDS}
    for s, p in past:
        for name, lo, hi in BANDS:
            if lo <= s <= hi:
                band_stat[name][0] += 1
                band_stat[name][1] += 1 if p else 0
                break
    print(f"  attempts that actually ran past step 20 (all in {CELL_40[0]} R{CELL_40[1]}):")
    print(f"  {'band':>7}{'attempts':>10}{'passed':>9}")
    for name, _, _ in BANDS:
        cnt, ps = band_stat[name]
        print(f"  {name:>7}{cnt:>10}{ps:>9}")
    print(f"  {'total':>7}{past_n:>10}{past_k:>9}")

    succ20 = sorted(int(a["steps"]) for c in cap20_cells for a in c["atts"]
                    if a["passed"] and a["steps"] is not None)
    print(f"\n  cap-20 arm, step quantiles of its {len(succ20)} successes (cap = 20):")
    for q in (0.5, 0.75, 0.9, 0.95, 0.99):
        v = quantile(succ20, q)
        print(f"    {int(q*100):>3}% finish by step {v:>2}   ({100*v/20:.0f}% of the cap)")
    near_cap = sum(1 for s in succ20 if s >= 20 - 2)
    print(f"  successes at step >= cap-2 (18): {near_cap}"
          f"  ({100*near_cap/len(succ20):.2f}% of successes)")

    print("\n  paired twins (same (run, round, variant, task): one attempt exited")
    print("  budget_exceeded, another passed with exit=done), cap-20 arm:")
    ft = sorted(fine_twins)
    print(f"    twin pairs: {len(ft)}"
          f"    (the per-run resolution gave {len(coarse_twins)};"
          f" the corrected")
    print(f"    regime adds {len(ft) - len(coarse_twins)} pairs from"
          f" s1k8b103 R{s1_nonR1_rounds[0]}..R{s1_nonR1_rounds[-1]})")
    print(f"    passing twin steps: median {ft[len(ft)//2]},"
          f" p90 {quantile(ft, 0.9)}, max {ft[-1]}")
    for lastk, d in ((1, 0), (3, 2), (5, 4)):
        near = sum(1 for s in ft if s >= 20 - d)
        print(f"    finishing within the last {lastk} step(s) (>= {20-d}): "
              f"{near:>3}  ({100*near/len(ft):.1f}%)")

    # ---------------------------------------------------------------- Section 4
    print("\n" + BAR)
    print("SECTION 4 -- RESIDUAL SUCCESS PROBABILITY  r = P(pass | running at 20)")
    print(BAR)
    est = residual_success_estimate(runs)
    print("  The quantity a Schmee-Hahn imputation downstream needs. Identified in")
    print(f"  this corpus only by {CELL_40[0]} R{CELL_40[1]}'s {est['n']} attempts"
          f" that survived past step 20.")
    print(f"    r_hat = {est['k']}/{est['n']} = {est['r_hat']:.4f}"
          f"    Wilson 95% [{est['wilson_lo']:.4f}, {est['wilson_hi']:.4f}]"
          f"    clusters = {est['clusters']}")
    print("  residual by step band (does r decay as the survivor runs longer?):")
    for name, _, _ in BANDS:
        cnt, ps = band_stat[name]
        rb = ps / cnt if cnt else float("nan")
        print(f"    {name:>7}: {ps:>2}/{cnt:<2} = {rb:.3f}")
    tail_n, tail_k = band_stat["31-40"]
    print("\n  THIS IS A WEAK ESTIMATE -- carry it as a sensitivity parameter, not a")
    print("  point value. Three reasons, all binding here:")
    print(f"   (1) all {est['n']} observations are one (run, round) cell"
          f" (clusters = {est['clusters']}), so the true")
    print("       interval is wider than Wilson's independence assumption allows;")
    print("   (2) that cell (R1) also differed from later rounds in other respects,")
    print("       so cap is confounded with round -- r may be an R1 effect, not a")
    print("       cap effect;")
    print(f"   (3) {tail_n} of the {est['n']} ran past step 30 and only {tail_k} of"
          f" those passed; the per-band")
    print(f"       residual falls from {band_stat['21-25'][1]}/{band_stat['21-25'][0]}"
          f" to {tail_k}/{tail_n}, so tail behaviour is not step-20 behaviour.")
    print("  (NOTE: the spec anticipated '6 past step 30'; the data show 6 PASSES")
    print(f"   past step 30 out of {tail_n} attempts -- the 6 is the pass count.)")


if __name__ == "__main__":
    argv = sys.argv[1:]
    default = runs_with_comparison()
    if not argv:
        assert len(default) == 36, \
            f"expected 36 runs with comparison.json, found {len(default)}"
    main(argv or default)
