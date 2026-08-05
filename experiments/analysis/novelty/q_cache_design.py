# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""Design knobs for the answer cache, settled on logged rollouts before building it.

Prefix voting over the pool's own history is worth +12.6pp over the free
exit_reason rule. That establishes the mechanism; it does not say how to build
it. Four knobs decide the implementation and each has a defensible argument on
both sides, so each gets measured rather than assumed:

  WINDOW      all history, or only the last k rounds? If the configs genuinely
              improve, old answers are stale and a window should win. If the
              gain is just sample count, more is better and a window throws
              away votes. This is the one knob that also tests whether the
              evolution is doing anything for the ensemble.
  WEIGHTING   uniform, or lean on recent rounds / short trajectories / variants
              with a good record? Weighting can only help if the weight
              correlates with correctness, which is exactly what gets checked.
  BALLOT      clean exits only, or let budget-exceeded attempts vote too? They
              pass 2.4% of the time, so they should be noise -- but they are
              28% of the attempts and discarding them costs votes.
  DECAY       geometric recency decay as a continuous version of WINDOW.

Also reported: the good/bad diversity split (Brown & Kuncheva 2010). Disagreement
only pays when a member breaks a WRONG majority and is right; when it breaks a
RIGHT majority it costs. Splitting the pool's disagreement into those two halves
says whether the measured +9.0pp cross-variant disagreement is an asset or a
liability, which no aggregate disagreement number can tell you.

Everything here replays answers already on disk. No API.
"""
from __future__ import annotations

import collections
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import n_answer_selection as N  # noqa: E402
import p_prefix_vote as P  # noqa: E402

_norm = P._norm


def vote(entries, weight=None):
    """entries = [(answer, attempt, age_in_rounds)]. Returns the winning string."""
    tally = collections.defaultdict(float)
    for ans, att, age in entries:
        tally[ans] += 1.0 if weight is None else weight(att, age)
    if not tally:
        return None
    best = max(tally.values())
    return sorted(k for k, v in tally.items() if v >= best - 1e-9)[0]


def run(tasks, window=None, weight=None, ballot="done"):
    """One policy over every cell. Correctness is judged inside the ballot only."""
    hit = tot = 0
    for seq in tasks.values():
        hist = []                      # [(round, attempt)]
        for rnd, atts in seq:
            pool = hist + [(rnd, a) for a in atts]
            if window is not None:
                pool = [(r, a) for r, a in pool if rnd - r <= window]
            ent = []
            passing = set()
            for r, a in pool:
                if ballot == "done" and a["exit"] != "done":
                    continue
                s = _norm(a["answer"])
                if not s:
                    continue
                ent.append((s, a, rnd - r))
                if a["passed"]:
                    passing.add(s)
            w = vote(ent, weight)
            hit += w is not None and w in passing
            tot += 1
            hist += [(rnd, a) for a in atts]
    return hit / tot, tot


def good_bad_diversity(tasks):
    """Brown & Kuncheva: is a dissenting vote an asset or a liability?

    For every cell, take the pool's plurality before this round's attempts and
    ask what the new attempts do to it. A dissent that overturns a wrong
    plurality is GOOD; one that breaks a right plurality is BAD.
    """
    c = collections.Counter()
    for seq in tasks.values():
        hist = []
        for rnd, atts in seq:
            prior = [a for a in hist if a["exit"] == "done" and _norm(a["answer"])]
            new = [a for a in atts if a["exit"] == "done" and _norm(a["answer"])]
            if prior and new:
                pv = collections.Counter(_norm(a["answer"]) for a in prior)
                ptop = sorted(k for k, v in pv.items() if v == max(pv.values()))[0]
                prior_ok = any(a["passed"] for a in prior if _norm(a["answer"]) == ptop)
                both = prior + new
                bv = collections.Counter(_norm(a["answer"]) for a in both)
                btop = sorted(k for k, v in bv.items() if v == max(bv.values()))[0]
                after_ok = any(a["passed"] for a in both if _norm(a["answer"]) == btop)
                if not prior_ok and after_ok:
                    c["GOOD  wrong plurality overturned"] += 1
                elif prior_ok and not after_ok:
                    c["BAD   right plurality broken"] += 1
                elif prior_ok:
                    c["held right"] += 1
                else:
                    c["held wrong"] += 1
            hist += atts
    return c


def main(runs):
    tasks = P.build(runs)
    base, n = run(tasks)
    print(f"cells {n}   tasks {len(tasks)}\n")

    print("=" * 70)
    print("WINDOW -- how far back should the cache reach?")
    print("=" * 70)
    print(f"  {'window':<26}{'acc':>9}{'vs all-history':>16}")
    for w in (0, 1, 2, 3, 5, 8, None):
        acc, _ = run(tasks, window=w)
        lab = "this round only" if w == 0 else (
            "all history" if w is None else f"last {w} round(s)")
        print(f"  {lab:<26}{100*acc:8.2f}%{100*(acc-base):+15.2f}pp")
    print("\n  a window that BEATS all-history means old answers are stale, i.e.")
    print("  the evolution is genuinely improving the configs. a window that")
    print("  loses means the gain is sample count and nothing more.")

    print("\n" + "=" * 70)
    print("WEIGHTING -- does any cheap weight beat one-attempt-one-vote?")
    print("=" * 70)
    schemes = [
        ("uniform", None),
        ("recency 0.8^age", lambda a, age: 0.8 ** age),
        ("recency 0.5^age", lambda a, age: 0.5 ** age),
        ("short trajectories", lambda a, age: 1.0 / (1 + (a["steps"] or 20) / 20)),
        ("recency x short", lambda a, age: (0.8 ** age) / (1 + (a["steps"] or 20) / 20)),
    ]
    print(f"  {'scheme':<26}{'acc':>9}{'vs uniform':>14}")
    for lab, f in schemes:
        acc, _ = run(tasks, weight=f)
        print(f"  {lab:<26}{100*acc:8.2f}%{100*(acc-base):+13.2f}pp")

    print("\n" + "=" * 70)
    print("BALLOT -- should attempts that ran out of steps get a vote?")
    print("=" * 70)
    for b in ("done", "all"):
        acc, _ = run(tasks, ballot=b)
        lab = "clean exits only" if b == "done" else "every attempt votes"
        print(f"  {lab:<26}{100*acc:8.2f}%{100*(acc-base):+13.2f}pp")

    print("\n" + "=" * 70)
    print("GOOD vs BAD DIVERSITY (Brown & Kuncheva 2010)")
    print("=" * 70)
    c = good_bad_diversity(tasks)
    tot = sum(c.values())
    for k in sorted(c):
        print(f"  {k:<38}{c[k]:6d}  {100*c[k]/tot:5.1f}%")
    g = c["GOOD  wrong plurality overturned"]
    b = c["BAD   right plurality broken"]
    print(f"\n  good:bad ratio = {g}:{b}"
          f"   net = {g-b:+d} cells  ({100*(g-b)/tot:+.2f}pp of all cells)")
    print("  a ratio near 1 means the pool's disagreement is churn, not signal.")


if __name__ == "__main__":
    main([p.name for p in sorted(N.RUNS.iterdir())
          if (p / "comparison.json").exists()])
