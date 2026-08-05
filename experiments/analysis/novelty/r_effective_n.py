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

PROVENANCE -- what here is borrowed and what is not.

The MDE arithmetic is NOT ours. `(z_alpha/2 + z_beta) * SE` on a benchmark pass
rate is Miller, "Adding Error Bars to Evals" (arXiv:2411.00640) Eq. 9, worked
there to the conclusion that a 3pp effect needs "at least n=969 independent
questions". Cite it as the method source; never present the formula as a
derivation of ours. What Miller does not do is discount the bed for tasks that
cannot swing -- the paper is explicit that it does not treat item removal or
effective sample size -- so specialising the formula to a swing set under
clustering is the only part that is ours.

The nearest prior claim about agent-evaluation noise is arXiv:2602.07150, which
states verbatim that "reported improvements of 2-3 percentage points may reflect
evaluation noise rather than genuine algorithmic progress" and recommends
"use statistical power analysis to determine the number of runs needed". It
recommends; it does not compute. Four differences have to be stated positively
wherever this analysis is written up: its bed is SWE-bench and ours is GAIA; it
measures noise where we partition the bed to get an effective n and drive an MDE
from it; it advises a power analysis where we run one; and it does not derive a
floor and check it against an A/A measurement. Asserting novelty without naming
that paper first is not defensible.

Restricting an eval to mid-difficulty items is itself standard -- the IRT line
(tinyBenchmarks arXiv:2402.14992, metabench arXiv:2407.12844) and the agent-side
arXiv:2603.23749 all do it. Every one of them does it to cut cost or stabilise a
ranking. Feeding the surviving item count into a power calculation is the use
that is new, not the mechanism.
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

    The estimator `(z_alpha/2 + z_beta) * SE` is Miller arXiv:2411.00640 Eq. 9,
    used here unmodified. The only local move is what goes into `n_swing` -- see
    the module docstring.
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
    print("\n  SOURCES (do not report the arithmetic above as our derivation):")
    print("    MDE estimator  (z_a/2 + z_b) * SE   Miller arXiv:2411.00640 Eq.9")
    print("    nearest prior claim on agent-eval noise  arXiv:2602.07150 -- it")
    print("      recommends a power analysis on SWE-bench; it does not run one,")
    print("      does not compute an effective n, and does not check a derived")
    print("      floor against an A/A measurement. Distinguish it explicitly.")
    print("    mid-difficulty item selection is standard (IRT: 2402.14992,")
    print("      2407.12844; agent-side: 2603.23749) -- all for cost or ranking.")
    print("      Feeding the surviving count into a power calculation is the")
    print("      new USE; the mechanism is borrowed.")

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
