# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""The seesaw ratchet is enforced against a signal that cannot support it.

HarnessX §4.1 states the acceptance rule as a monotone ratchet: a shipped
candidate must not lose any task already recorded as solved. The gate implements
this by comparing pass@2 counts before and after -- two Bernoulli draws per
(variant, task).

Two draws is not enough to tell "this configuration broke the task" from "this
task is a coin flip and both flips came up tails". A task whose true per-attempt
success probability is 0.6 shows zero passes out of two on 16% of rounds while
nothing whatsoever has changed. The ratchet fires on those rounds.

This file asks how much of the observed ratchet violation is explainable by
sampling alone. The null is deliberately generous to the gate: each task gets its
own success probability, estimated leave-this-round-out so the round being judged
never contributes to its own expectation, and the expectation is computed only
over rounds that actually occurred.

    observed  = rounds where the task was solved earlier and passes zero times now
    expected  = sum over the same rounds of (1 - p_hat_loo)^n_attempts

If observed / expected is near 1, the ratchet is measuring noise. That does not
mean regressions never happen -- it means the instrument cannot see them, which
is the stronger claim because it is a property of the design, not of this bed.

The exit_reason split is reported alongside because it separates two very
different stories: zero passes because every attempt ran out of steps (a resource
accident the configuration may have nothing to do with) versus zero passes with
every attempt terminating normally (the only case where "the configuration broke
it" is even a candidate explanation).
"""
from __future__ import annotations

import collections
import pathlib
import random
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import n_answer_selection as NA  # noqa: E402
import o_prefix_voting as O  # noqa: E402


def analyse(runs):
    hist = O.build_history(runs)

    obs = exp = viol_rounds = 0
    why = collections.Counter()
    per_run = collections.defaultdict(lambda: [0.0, 0.0])
    solved_before_rounds = 0
    depth_bucket = collections.defaultdict(lambda: [0.0, 0.0])

    for (run, task), rounds in hist.items():
        flat = [(r, a) for r, atts in rounds for a in atts]
        n_all = len(flat)
        n_pass_all = sum(a["passed"] for _, a in flat)

        solved = False
        for idx, (R, atts) in enumerate(rounds):
            n = len(atts)
            k = sum(a["passed"] for a in atts)
            if solved:
                solved_before_rounds += 1
                # leave-this-round-out success probability for THIS task
                den = n_all - n
                p = (n_pass_all - k) / den if den > 0 else 0.0
                e = (1.0 - p) ** n
                exp += e
                per_run[run][1] += e
                depth_bucket[min(idx, 8)][1] += e
                if k == 0:
                    obs += 1
                    per_run[run][0] += 1
                    depth_bucket[min(idx, 8)][0] += 1
                    ex = {a["exit"] for a in atts}
                    why["all hit the step budget" if ex == {"budget_exceeded"}
                        else "all terminated normally (wrong answer)"
                        if ex == {"done"}
                        else "infra error present" if "error" in ex
                        else "mixed budget/normal"] += 1
            solved = solved or k > 0
        viol_rounds += 0

    return hist, obs, exp, why, per_run, solved_before_rounds, depth_bucket


def parametric_ci(hist, reps=400, seed=0):
    """Simulate the null directly: redraw every attempt from its task's own
    leave-round-out rate, then recount violations. Gives the null's spread."""
    rng = random.Random(seed)
    out = []
    tasks = []
    for (_, _), rounds in hist.items():
        flat = [a for _, atts in rounds for a in atts]
        n_all, n_pass = len(flat), sum(a["passed"] for a in flat)
        tasks.append([(len(atts), sum(a["passed"] for a in atts))
                      for _, atts in rounds] + [(n_all, n_pass)])
    for _ in range(reps):
        tot = 0
        for t in tasks:
            n_all, n_pass = t[-1]
            solved = False
            for n, k in t[:-1]:
                den = n_all - n
                p = (n_pass - k) / den if den > 0 else 0.0
                kk = sum(1 for _ in range(n) if rng.random() < p)
                if solved and kk == 0:
                    tot += 1
                solved = solved or kk > 0
        out.append(tot)
    out.sort()
    return out[int(.025 * reps)], out[int(.975 * reps)]


def main(runs):
    hist, obs, exp, why, per_run, elig, depth = analyse(runs)
    lo, hi = parametric_ci(hist)

    print("=" * 76)
    print("RATCHET VIOLATIONS  vs  WHAT SAMPLING ALONE PREDICTS")
    print("=" * 76)
    print(f"  eligible rounds (task already solved earlier) : {elig}")
    print(f"  observed violations (zero passes this round)  : {obs}"
          f"   ({100*obs/max(elig,1):.2f}% of eligible)")
    print(f"  expected under per-task sampling null         : {exp:.1f}"
          f"   95% null band [{lo}, {hi}]")
    r = obs / exp if exp else float("nan")
    print(f"  observed / expected                           : {r:.2f}")
    verdict = ("the ratchet is firing on NOISE -- observed sits inside the null"
               if lo <= obs <= hi else
               "observed EXCEEDS the sampling null -- some violations are real")
    print(f"  -> {verdict}")
    if obs > hi:
        print(f"     excess over the null's upper edge: {obs-hi} rounds "
              f"({100*(obs-hi)/max(elig,1):.2f}% of eligible)")

    print("\n" + "=" * 76)
    print("WHAT DID THE FAILING ROUND LOOK LIKE?")
    print("=" * 76)
    tot = sum(why.values()) or 1
    for k, v in why.most_common():
        print(f"  {k:<40}{v:6d}{100*v/tot:8.1f}%")
    print("\n  only the 'all terminated normally' row can even be a configuration")
    print("  regression; a budget-exhausted round tells you nothing about the edit.")

    print("\n" + "=" * 76)
    print("DOES IT GET BETTER AS THE RUN MATURES?")
    print("=" * 76)
    print(f"  {'round index':<14}{'observed':>10}{'expected':>10}{'obs/exp':>10}")
    for d in sorted(depth):
        o_, e_ = depth[d]
        print(f"  {('>=8' if d == 8 else str(d)):<14}{int(o_):10d}{e_:10.1f}"
              f"{(o_/e_ if e_ else float('nan')):10.2f}")

    print("\n" + "=" * 76)
    print("PER RUN")
    print("=" * 76)
    print(f"  {'run':<16}{'observed':>10}{'expected':>10}{'obs/exp':>10}")
    for run in sorted(per_run):
        o_, e_ = per_run[run]
        if e_ < 3:
            continue
        print(f"  {run:<16}{int(o_):10d}{e_:10.1f}{o_/e_:10.2f}")


if __name__ == "__main__":
    argv = sys.argv[1:]
    main(argv or [p.name for p in sorted(NA.RUNS.iterdir())
                  if (p / "comparison.json").exists()])
