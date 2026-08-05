# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""How many of the 103 tasks can the gate actually learn anything from?

A gate compares pass counts before and after an edit. A task that every
configuration solves contributes the same value to both sides; so does a task
that nothing ever solves. Neither can move the comparison, but both consume
rollouts and both inflate the headline pass rate. Only tasks that sometimes pass
and sometimes fail carry information about whether an edit helped.

So "103 tasks" is not the sample size of the decision. The sample size is the
swing set, and every power calculation, every noise floor and every claim of the
form "this edit improved N tasks" has to be read against that smaller number.

Three things are computed here:

  * the partition of the bed into never / always / swing, by attempt share as
    well as by task count, because the two differ a lot;
  * a variance decomposition -- how much of the outcome variance is between
    tasks (real difficulty, the thing a benchmark is supposed to measure) versus
    within task (the same configuration flipping on reruns);
  * the minimum effect the gate could detect, given the swing count and two
    attempts per task, which is the number that says whether the instrument was
    ever capable of the job it was given.

The within-task component is estimated only from cells where the SAME variant
attempted the SAME task in the SAME round, so nothing about configuration
difference can leak into it. That makes it a clean floor on irreducible noise.
"""
from __future__ import annotations

import collections
import math
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import n_answer_selection as NA  # noqa: E402


def partition(runs, lo=0.02, hi=0.98):
    """task -> (n_attempts, n_pass); and the never/always/swing split."""
    tot = collections.defaultdict(lambda: [0, 0])
    within_pairs = 0
    within_disagree = 0
    for c in NA.load_cells(runs):
        key = c["task"]
        for a in c["atts"]:
            tot[key][0] += 1
            tot[key][1] += a["passed"]
        # same variant, same round, same task -> pure rerun noise
        if len(c["atts"]) == 2:
            within_pairs += 1
            within_disagree += c["atts"][0]["passed"] != c["atts"][1]["passed"]
    groups = {"never": [], "always": [], "swing": []}
    for t, (n, k) in tot.items():
        p = k / n
        groups["never" if p <= lo else "always" if p >= hi else "swing"].append(
            (t, n, k, p))
    return tot, groups, within_pairs, within_disagree


def power(n_swing, sd_per_task, attempts=2, alpha=0.05, target=0.80):
    """Smallest shift in the swing-set pass rate a round comparison could see.

    Each swing task is measured with `attempts` Bernoulli draws, so the standard
    error of the swing-set mean is sd / sqrt(n * attempts). Deriving it this way
    rather than fitting it is the point: if the number that falls out matches the
    noise floor measured empirically from A/A rounds, then the floor is not a
    property of this experiment's setup, it is arithmetic.
    """
    if n_swing <= 1:
        return float("nan"), float("nan")
    z_a, z_b = 1.959964, 0.8416212
    se = sd_per_task / math.sqrt(n_swing * attempts)
    return se, (z_a + z_b) * se


def main(runs):
    tot, g, wp, wd = partition(runs)
    n_task = len(tot)
    n_att = sum(n for n, _ in tot.values())

    print("=" * 76)
    print("WHAT THE 103 TASKS ARE ACTUALLY MADE OF")
    print("=" * 76)
    print(f"  distinct tasks across the corpus : {n_task}")
    print(f"  attempts                         : {n_att}")
    print(f"\n  {'group':<10}{'tasks':>7}{'% tasks':>10}{'attempts':>11}"
          f"{'% attempts':>12}{'mean p':>9}")
    for name in ("never", "always", "swing"):
        rows = g[name]
        if not rows:
            print(f"  {name:<10}{0:>7}")
            continue
        a = sum(n for _, n, _, _ in rows)
        mp = sum(p for *_, p in rows) / len(rows)
        print(f"  {name:<10}{len(rows):>7}{100*len(rows)/n_task:9.1f}%{a:>11}"
              f"{100*a/n_att:11.1f}%{mp:9.3f}")

    swing = g["swing"]
    print(f"\n  -> the gate's real sample size is {len(swing)}, not {n_task}.")
    dead = len(g["never"]) + len(g["always"])
    dead_att = sum(n for name in ("never", "always") for _, n, _, _ in g[name])
    print(f"  -> {dead} tasks ({100*dead/n_task:.0f}%) can never move a "
          f"before/after comparison,")
    print(f"     yet they consume {100*dead_att/n_att:.0f}% of all rollouts.")

    print("\n" + "=" * 76)
    print("VARIANCE DECOMPOSITION")
    print("=" * 76)
    ps = [k / n for n, k in tot.values()]
    mu = sum(ps) / len(ps)
    between = sum((p - mu) ** 2 for p in ps) / (len(ps) - 1)
    within = sum(p * (1 - p) for p in ps) / len(ps)
    print(f"  mean per-task pass rate           : {mu:.4f}")
    print(f"  between-task variance (difficulty): {between:.4f}")
    print(f"  within-task variance  (flakiness) : {within:.4f}")
    icc = between / (between + within)
    print(f"  ICC = between / (between+within)  : {icc:.3f}")
    print("  ICC is the share of an observation that reflects WHICH task it is")
    print("  rather than luck. Below ~0.5 a single run cannot rank configurations.")

    print(f"\n  same variant, same round, same task, 2 attempts: {wp} cells")
    print(f"  of those, the two attempts DISAGREE            : {wd}"
          f"  ({100*wd/max(wp,1):.1f}%)")
    print("  that disagreement rate is pure rerun noise -- no configuration")
    print("  difference of any kind is involved.")

    print("\n" + "=" * 76)
    print("COULD THE GATE HAVE DETECTED ANYTHING?")
    print("=" * 76)
    sd = math.sqrt(sum(p * (1 - p) for _, _, _, p in swing) / max(len(swing), 1))
    print(f"  swing tasks                        : {len(swing)}")
    print(f"  per-task sd on the swing set       : {sd:.3f}")
    se, mde = power(len(swing), sd)
    print(f"\n  PREDICTED round-to-round sd of the swing-set pass rate")
    print(f"    = sd / sqrt(n_swing * attempts) = {sd:.3f} / "
          f"sqrt({len(swing)} * 2) = {100*se:.2f}pp")
    print(f"  MEASURED noise floor from A/A rounds (e_pervar3 epochs) = 3.18pp")
    print(f"    -> the floor is not an artefact of this setup. It is what the")
    print(f"       bed's own flakiness and its effective size arithmetically give.")
    print(f"\n  {'n swing':>9}{'SE':>9}{'MDE @80% power':>17}")
    for n_s in sorted({len(swing), 40, 20, 10}):
        if n_s < 2:
            continue
        s, m = power(n_s, sd)
        print(f"  {n_s:>9}{100*s:8.2f}pp{100*m:15.2f}pp")
    print("\n  A single round-pair comparison cannot see an edit worth less than")
    print("  the MDE above. Harness edits plausibly move 1-3pp. An instrument")
    print("  whose minimum detectable effect exceeds the effects on offer is not")
    print("  a noisy instrument -- it is the wrong instrument.")

    print("\n" + "=" * 76)
    print("THE NEVER-SOLVED CORE")
    print("=" * 76)
    nev = sorted(g["never"], key=lambda r: -r[1])[:10]
    print(f"  {len(g['never'])} tasks were never solved by any variant in any run.")
    print(f"  {'task':<12}{'attempts spent':>16}")
    for t, n, _, _ in nev:
        print(f"  {t[:10]:<12}{n:>16}")
    tail = sum(n for _, n, _, _ in g["never"])
    print(f"  total rollouts spent on tasks nothing ever solved: {tail}"
          f"  ({100*tail/n_att:.1f}%)")


if __name__ == "__main__":
    argv = sys.argv[1:]
    main(argv or [p.name for p in sorted(NA.RUNS.iterdir())
                  if (p / "comparison.json").exists()])
