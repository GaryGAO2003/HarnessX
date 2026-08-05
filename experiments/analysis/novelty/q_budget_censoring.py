# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""Is a budget-exhausted run slow, or stuck?

A `budget_exceeded` attempt is a right-censored observation: the run stopped
because the harness stopped it, not because the agent finished. Treating it as a
failure -- which the ledger, the gate and every reported score do -- conflates
"this configuration cannot do the task" with "this configuration was not given
room to finish". Those want opposite repairs.

The discriminator is the empirical hazard of success:

    h(t) = (attempts that succeed on exactly step t) / (attempts still running at t)

If h(t) is still meaningfully above zero as t approaches the cap, the cap is
binding and censored runs contain unrealised successes. If h(t) has decayed to
nothing well before the cap, the censored runs were not going to finish anyway
and the honest reading is that they were stuck, not slow.

The complementary evidence is paired: cells where one attempt hit the budget and
its twin -- same task, same round, same configuration -- passed. Those prove the
configuration CAN do the task, so the censored twin is bad luck rather than
incapacity, and the twin's step count says how close to the cap a success runs.

Kaplan-Meier is reported for the survival curve because that is the standard
estimator for censored data, but the assumption it needs -- censoring
independent of the event -- is exactly what is in doubt here. So the KM number
is given as an UPPER bound on what raising the cap could buy, never as a point
prediction, and the hazard curve is what carries the argument.
"""
from __future__ import annotations

import collections
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import n_answer_selection as NA  # noqa: E402


def collect(runs):
    """Per-attempt (steps, exit, passed, run) plus the paired budget/pass twins.

    The run is carried because the step cap is NOT global. One run's first round
    used a 40-step cap while every other round of every run used 20, so taking
    the cap as the maximum step observed anywhere overstates it for 99% of the
    corpus and makes every "distance from the cap" statement wrong. The cap is
    resolved per run below, and twins are recorded with their own run's cap.
    """
    att = []
    twins = []
    for c in NA.load_cells(runs):
        for a in c["atts"]:
            if a["steps"] is not None:
                att.append((int(a["steps"]), a["exit"], a["passed"], c["run"]))
        be = [a for a in c["atts"] if a["exit"] == "budget_exceeded"]
        pa = [a for a in c["atts"] if a["passed"] and a["exit"] == "done"]
        if be and pa:
            for p in pa:
                if p["steps"] is not None:
                    twins.append((int(p["steps"]), c["run"]))
    return att, twins


def caps_per_run(att):
    """The operative cap for each run: the step at which budget_exceeded fires.

    Using the max budget_exceeded step rather than the max step of any attempt
    keeps a single long successful run from redefining the cap for the rest.
    """
    caps = collections.defaultdict(int)
    for s, e, _, run in att:
        if e == "budget_exceeded":
            caps[run] = max(caps[run], s)
    for s, _, _, run in att:                      # runs with no cap hit at all
        caps.setdefault(run, 0)
        caps[run] = max(caps[run], 0) or max(caps[run], s)
    return dict(caps)


def hazard(att, cap):
    """h(t) over the integer step grid, plus the risk set at each t."""
    succ = collections.Counter(s for s, _, p, _ in att if p)
    # an attempt is "still running at t" if its recorded final step >= t
    ends = collections.Counter(s for s, _, _, _ in att)
    running = len(att)
    rows = []
    for t in range(1, cap + 1):
        rows.append((t, running, succ.get(t, 0),
                     succ.get(t, 0) / running if running else 0.0))
        running -= ends.get(t, 0)
    return rows


def main(runs):
    att, twins = collect(runs)
    caps = caps_per_run(att)
    # the dominant regime -- almost the whole corpus shares one cap
    regime = collections.Counter(caps[r] for _, _, _, r in att)
    cap = regime.most_common(1)[0][0]
    main_att = [a for a in att if caps[a[3]] == cap]
    n = len(att)
    by_exit = collections.Counter(e for _, e, _, _ in att)

    print("=" * 74)
    print("SETUP")
    print("=" * 74)
    print(f"  attempts with a recorded step count : {n}")
    print(f"  step cap by run                     : "
          f"{dict(sorted(collections.Counter(caps.values()).items()))}"
          f"   (cap -> how many runs)")
    print(f"  dominant regime                     : cap={cap},"
          f" {len(main_att)} attempts ({100*len(main_att)/n:.1f}%)")
    print(f"  exit reasons                        : {dict(by_exit)}")
    passes = [s for s, _, p, _ in main_att if p]
    print(f"  passing attempts in that regime     : {len(passes)}"
          f"   median {sorted(passes)[len(passes)//2]} steps")
    print("\n  NOTE: the cap is NOT global. Every statement below about distance")
    print("  from the cap is computed inside the dominant regime only.")

    print("\n" + "=" * 74)
    print(f"WHERE DO SUCCESSES LAND RELATIVE TO THE CAP? (cap = {cap})")
    print("=" * 74)
    for q in (0.5, 0.75, 0.9, 0.95, 0.99):
        v = sorted(passes)[min(int(q * len(passes)), len(passes) - 1)]
        print(f"  {int(q*100):>3}% of successes finish by step {v:>3}"
              f"   ({100*v/cap:.0f}% of the cap)")
    tail = sum(1 for s in passes if s >= cap - 2)
    print(f"\n  successes at step >= cap-2 ({cap-2}) : {tail}"
          f"  ({100*tail/len(passes):.2f}% of successes)")
    print("  a cap that binds would show a pile-up here; a cap that does not")
    print("  bind shows successes finishing far below it.")
    att = main_att

    print("\n" + "=" * 74)
    print("EMPIRICAL HAZARD OF SUCCESS  h(t) = successes at t / still running at t")
    print("=" * 74)
    rows = hazard(att, cap)
    print(f"  {'step':>5}{'at risk':>10}{'successes':>11}{'hazard':>10}")
    for t, risk, s, h in rows:
        bar = "#" * int(h * 200)
        print(f"  {t:>5}{risk:>10}{s:>11}{100*h:>9.2f}%  {bar}")

    late = [h for t, _, _, h in rows if t >= cap - 4]
    early = [h for t, _, _, h in rows if 3 <= t <= 7]
    print(f"\n  mean hazard, steps 3-7        : {100*sum(early)/len(early):.2f}%")
    print(f"  mean hazard, last 5 steps     : {100*sum(late)/len(late):.2f}%")
    ratio = (sum(late) / len(late)) / (sum(early) / len(early) or 1e-9)
    print(f"  late / early                  : {ratio:.3f}")
    print("  -> near zero means the cap is not where the successes were coming")
    print("     from; censored runs were not about to finish.")

    print("\n" + "=" * 74)
    print("KAPLAN-MEIER  --  WITHDRAWN, DO NOT REINSTATE")
    print("=" * 74)
    print("  An earlier version of this file reported a KM estimate of the")
    print("  uncensored success rate and an implied headroom. That number is")
    print("  retracted. Kaplan-Meier assumes censoring independent of the event,")
    print("  and here the cap fires precisely on the runs that are going badly --")
    print("  censoring is informative by construction. KM estimates are also")
    print("  undefined beyond the largest uncensored observation, which is the")
    print("  region the extrapolation lived in. The algorithm-configuration")
    print("  literature routes around KM for exactly this reason and imputes")
    print("  censored values under a model instead (Schmee & Hahn 1979, EM;")
    print("  Hutter et al. arXiv:1310.1947, which measures that both dropping")
    print("  censored points and treating them as finished are biased).")
    print("  Any successor to this analysis must use model-based imputation.")

    print("\n" + "=" * 74)
    print("PAIRED TWINS  (one attempt hit the budget, its same-config twin passed)")
    print("=" * 74)
    tw = [s for s, r in twins if caps.get(r) == cap]
    if tw:
        t = sorted(tw)
        print(f"  such pairs (dominant regime only): {len(tw)}")
        print(f"  the PASSING twin's step count: median {t[len(t)//2]}, "
              f"p90 {t[min(int(.9*len(t)), len(t)-1)]}, max {t[-1]}")
        for d in (0, 2, 4):
            near = sum(1 for s in tw if s >= cap - d)
            print(f"  passing twins finishing at step >= {cap-d:>2} "
                  f"(cap-{d}): {near:>4}  ({100*near/len(tw):.1f}%)")
        print("\n  the configuration demonstrably CAN do these tasks, so its")
        print("  censored twin is variance rather than incapacity. A pile-up at")
        print("  the cap means successes were still arriving when the cap fired.")
    else:
        print("  none found")


if __name__ == "__main__":
    argv = sys.argv[1:]
    main(argv or [p.name for p in sorted(NA.RUNS.iterdir())
                  if (p / "comparison.json").exists()])
