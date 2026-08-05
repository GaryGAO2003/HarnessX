# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""Where should the step budget go, given that the pool's output is a vote?

Two constants have never been touched by the evolution loop: every attempt gets
20 steps, and every (variant, task) gets exactly 2 attempts. Both are load-
bearing and neither was ever chosen.

The classical answer to "62.6% of losses are timeouts" is capping and restarts:
under a heavy-tailed runtime distribution, several short fresh runs beat one long
one, because a fresh draw escapes the tail that a longer horizon only extends.
That prescription needs three numbers, all of which are on disk:

  1. WHERE SUCCESSES LIVE. The distribution of steps used by attempts that
     passed. A cap below the tail of that distribution is nearly free; the steps
     above it are being spent almost exclusively on attempts that will not pass.

  2. WHAT A CAP COSTS AND BUYS. For every cap k, the share of current successes
     that still finish in time, and the steps reclaimed per attempt. That trade
     is exact -- lowering a cap is a truncation of logged behaviour, so no
     counterfactual generation is involved.

  3. WHAT A RESTART IS WORTH. P(a fresh attempt passes | the previous attempt on
     the same task, same config, same round failed), split by how it failed.
     This is the price the reclaimed steps buy, and it is directly observable in
     the second attempt of every cell whose first attempt lost.

Raising a cap is NOT testable here and no number below should be read that way:
nothing was ever logged past step 20, so the value of a longer horizon is
unmeasured, and the aggregate evidence (P(pass | budget_exceeded) = 2.4%) points
away from it rather than toward it.
"""
from __future__ import annotations

import collections
import pathlib
import statistics as st
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import n_answer_selection as N  # noqa: E402


def collect(runs):
    cells = list(N.load_cells(runs))
    by = collections.defaultdict(list)
    for c in cells:
        for a in c["atts"]:
            if a["steps"] is None:
                continue
            kind = ("pass" if a["passed"]
                    else "timeout" if a["exit"] == "budget_exceeded"
                    else "wrong" if a["exit"] == "done"
                    else "error")
            by[kind].append(int(a["steps"]))
    return cells, by


def main(runs):
    cells, by = collect(runs)
    tot = sum(len(v) for v in by.values())
    print(f"attempts with a step count: {tot}   cells: {len(cells)}\n")

    print("=" * 72)
    print("1. WHERE SUCCESSES LIVE")
    print("=" * 72)
    print(f"  {'outcome':<12}{'n':>7}{'mean':>8}{'p50':>6}{'p75':>6}"
          f"{'p90':>6}{'p95':>6}{'max':>6}")
    for k in ("pass", "wrong", "timeout", "error"):
        v = sorted(by.get(k, []))
        if not v:
            continue
        q = lambda p: v[min(int(p * len(v)), len(v) - 1)]  # noqa: E731
        print(f"  {k:<12}{len(v):>7}{st.mean(v):8.1f}{q(.50):6d}{q(.75):6d}"
              f"{q(.90):6d}{q(.95):6d}{max(v):6d}")

    passes = sorted(by.get("pass", []))
    print("\n" + "=" * 72)
    print("2. WHAT A CAP COSTS AND BUYS")
    print("=" * 72)
    every = [s for v in by.values() for s in v]
    print(f"  {'cap':>5}{'successes kept':>17}{'successes lost':>16}"
          f"{'steps spent/attempt':>21}{'reclaimed':>11}")
    base_cost = st.mean(every)
    for cap in (6, 8, 10, 12, 14, 16, 18, 20):
        kept = sum(1 for s in passes if s <= cap)
        cost = st.mean([min(s, cap) for s in every])
        print(f"  {cap:>5}{100*kept/len(passes):16.1f}%"
              f"{len(passes)-kept:16d}{cost:21.1f}"
              f"{100*(base_cost-cost)/base_cost:10.1f}%")
    print("\n  'successes lost' counts attempts that DID pass but needed more")
    print("  steps than the cap. Truncation of logged behaviour, so exact.")

    print("\n" + "=" * 72)
    print("3. WHAT A RESTART IS WORTH")
    print("=" * 72)
    # second attempt of a cell, conditioned on how the first one lost
    tab = collections.defaultdict(lambda: [0, 0])
    for c in cells:
        a1, a2 = c["atts"][0], c["atts"][1]
        if a1["passed"]:
            continue
        key = ("first hit the step ceiling" if a1["exit"] == "budget_exceeded"
               else "first answered wrong" if a1["exit"] == "done"
               else "first errored")
        tab[key][1] += 1
        tab[key][0] += a2["passed"]
    print(f"  {'given the first attempt...':<32}{'n':>7}{'P(fresh attempt passes)':>26}")
    grand = [0, 0]
    for k in sorted(tab):
        w, n = tab[k]
        grand[0] += w
        grand[1] += n
        print(f"  {k:<32}{n:>7}{100*w/max(n,1):25.1f}%")
    print(f"  {'ANY failed first attempt':<32}{grand[1]:>7}"
          f"{100*grand[0]/max(grand[1],1):25.1f}%")
    base = sum(1 for c in cells for a in c["atts"] if a["passed"]) / \
        sum(len(c["atts"]) for c in cells)
    print(f"\n  unconditional P(an attempt passes) = {100*base:.1f}%")
    print("  a restart on a failed task is worth less than a fresh task, but it")
    print("  is the price the reclaimed steps buy -- compare it against 0.")

    print("\n" + "=" * 72)
    print("4. THE TRADE, AS ARITHMETIC")
    print("=" * 72)
    ceil_rate = 100 * tab["first hit the step ceiling"][0] / \
        max(tab["first hit the step ceiling"][1], 1)
    for cap in (10, 12, 14):
        cost = st.mean([min(s, cap) for s in every])
        lost = sum(1 for s in passes if s > cap)
        extra = (base_cost - cost) / cap
        print(f"  cap {cap:>2}: reclaims {base_cost-cost:4.1f} steps/attempt "
              f"= {extra:.2f} extra attempts, costs {lost} logged successes "
              f"({100*lost/len(passes):.1f}% of them)")
    print(f"\n  each extra attempt on a stuck task is worth ~{ceil_rate:.0f}% "
          f"(row 3, ceiling case).")
    print("  a cap pays iff  extra_attempts x that rate  >  the successes it cuts.")
    print("  NOTE: this arithmetic assumes a restart is independent of the run it")
    print("  replaces. That is the one premise here NOT established by these logs.")


if __name__ == "__main__":
    main([p.name for p in sorted(N.RUNS.iterdir())
          if (p / "comparison.json").exists()])
