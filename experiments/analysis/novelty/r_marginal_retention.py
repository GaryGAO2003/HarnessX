# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""Which variants should a pool keep, when the pool's output is a vote?

The gate keeps a variant because the variant got better. But the thing that
gets shipped is not a variant -- it is whatever the pool's answers vote for. A
variant that is individually mediocre yet reaches answers nobody else reaches is
worth more to that vote than a strong variant that only repeats the consensus,
and the current rule cannot see the difference because it never looks past the
variant's own pass column.

So: hold the number of retained variants fixed and change only the rule that
picks them.

  ACCURACY   top-k by mean pass rate.                     what the gate does now
  PARETO     top-k by how many tasks the variant is on the per-instance Pareto
             frontier for (GEPA 2507.19457). Rewards being uniquely good
             somewhere rather than good on average.
  MARGINAL   greedy: repeatedly add whichever remaining variant most raises the
             pool's ORACLE coverage. Rewards reaching answers the kept set
             cannot reach. No published training-free agent-config system uses
             this as a retention objective.
  MARGINAL-V same greedy, but the thing being raised is the pool's PLURALITY
             score -- i.e. optimise the rule you actually ship, not its oracle.
  RANDOM     control. Any rule that cannot beat this is not a rule.

Scored on the two quantities that matter downstream: what the retained pool's
answers VOTE to (deployable) and what they could reach at best (ORACLE).

Note the honest limit of this experiment: retention chooses among variants that
already exist, so it cannot manufacture coverage the run never produced. It
measures whether the gate is discarding coverage it already had.
"""
from __future__ import annotations

import collections
import itertools
import pathlib
import random
import statistics as st
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import n_answer_selection as N  # noqa: E402
import p_prefix_vote as P  # noqa: E402

_norm = P._norm


def load(runs, min_variants=4, min_tasks=20):
    """run -> {variant -> {task -> [attempts]}}, keeping runs big enough to rank."""
    out = collections.defaultdict(lambda: collections.defaultdict(
        lambda: collections.defaultdict(list)))
    for c in N.load_cells(runs):
        out[c["run"]][c["variant"]][c["task"]].extend(c["atts"])
    keep = {}
    for run, vs in out.items():
        tasks = set.union(*[set(t) for t in vs.values()])
        if len(vs) >= min_variants and len(tasks) >= min_tasks:
            keep[run] = vs
    return keep


def _ballot(atts):
    return [a for a in atts if a["exit"] == "done" and _norm(a["answer"])]


def pool_scores(vs, subset, tasks):
    """(plurality, oracle) for the answers the retained variants produced."""
    plur = orac = 0
    for t in tasks:
        atts = [a for v in subset for a in vs[v].get(t, [])]
        if any(a["passed"] for a in atts):
            orac += 1
        b = _ballot(atts)
        if not b:
            continue
        votes = collections.Counter(_norm(a["answer"]) for a in b)
        top = sorted(k for k, c in votes.items() if c == max(votes.values()))[0]
        plur += any(a["passed"] for a in b if _norm(a["answer"]) == top)
    return plur / len(tasks), orac / len(tasks)


def rule_accuracy(vs, tasks, k):
    def rate(v):
        atts = [a for t in tasks for a in vs[v].get(t, [])]
        return sum(a["passed"] for a in atts) / max(len(atts), 1)
    return sorted(vs, key=rate, reverse=True)[:k]


def rule_pareto(vs, tasks, k):
    """How many tasks is this variant among the best on? (ties all count.)"""
    score = collections.Counter()
    for t in tasks:
        rates = {}
        for v in vs:
            atts = vs[v].get(t, [])
            if atts:
                rates[v] = sum(a["passed"] for a in atts) / len(atts)
        if not rates:
            continue
        best = max(rates.values())
        if best == 0:
            continue                      # nobody solved it: no frontier to be on
        for v, r in rates.items():
            if r >= best:
                score[v] += 1
    return [v for v, _ in score.most_common(k)] or list(vs)[:k]


def _greedy(vs, tasks, k, which):
    chosen = []
    rest = list(vs)
    while len(chosen) < k and rest:
        best_v, best_s = None, -1.0
        for v in rest:
            s = pool_scores(vs, chosen + [v], tasks)[which]
            if s > best_s:
                best_v, best_s = v, s
        chosen.append(best_v)
        rest.remove(best_v)
    return chosen


def rule_marginal_oracle(vs, tasks, k):
    return _greedy(vs, tasks, k, 1)


def rule_marginal_vote(vs, tasks, k):
    return _greedy(vs, tasks, k, 0)


RULES = [
    ("ACCURACY   top-k mean pass", rule_accuracy),
    ("PARETO     per-task frontier", rule_pareto),
    ("MARGINAL   greedy oracle lift", rule_marginal_oracle),
    ("MARGINAL-V greedy vote lift", rule_marginal_vote),
]


def main(runs, k=3, seeds=40):
    data = load(runs)
    if not data:
        print("no run has enough variants")
        return
    print(f"runs with >={4} variants: {len(data)}   "
          f"({', '.join(sorted(data))})")
    print(f"retaining k={k} variants per run\n")

    agg = collections.defaultdict(lambda: ([], []))
    rng = random.Random(0)
    for run, vs in sorted(data.items()):
        tasks = sorted(set.union(*[set(t) for t in vs.values()]))
        for lab, fn in RULES:
            sub = fn(vs, tasks, k)
            p, o = pool_scores(vs, sub, tasks)
            agg[lab][0].append(p)
            agg[lab][1].append(o)
        rp, ro = [], []
        for _ in range(seeds):
            sub = rng.sample(list(vs), min(k, len(vs)))
            p, o = pool_scores(vs, sub, tasks)
            rp.append(p)
            ro.append(o)
        agg["RANDOM     control"][0].append(st.mean(rp))
        agg["RANDOM     control"][1].append(st.mean(ro))
        agg["ALL        keep everything"][0].append(pool_scores(vs, list(vs), tasks)[0])
        agg["ALL        keep everything"][1].append(pool_scores(vs, list(vs), tasks)[1])

    order = [r[0] for r in RULES] + ["RANDOM     control", "ALL        keep everything"]
    base_p = st.mean(agg["ACCURACY   top-k mean pass"][0])
    base_o = st.mean(agg["ACCURACY   top-k mean pass"][1])
    print(f"  {'retention rule':<30}{'PLURALITY':>11}{'vs acc':>9}"
          f"{'ORACLE':>10}{'vs acc':>9}")
    print(f"  {'-'*30}{'-'*11}{'-'*9}{'-'*10}{'-'*9}")
    for lab in order:
        p, o = agg[lab]
        print(f"  {lab:<30}{100*st.mean(p):10.2f}%{100*(st.mean(p)-base_p):+8.2f}"
              f"{100*st.mean(o):9.2f}%{100*(st.mean(o)-base_o):+8.2f}")

    print("\n  paired by run (n=%d), so each rule faces the same variants." % len(data))
    print("  MARGINAL beating ACCURACY means the gate is throwing away coverage")
    print("  it already had. MARGINAL ~ ACCURACY means the pool has no coverage")
    print("  to rescue and the retention rule is not where the score lives.")

    print(f"\n  {'rule':<30}{'wins':>7}{'ties':>7}{'losses':>8}  (plurality, per run)")
    for lab in order:
        w = t_ = l = 0
        for a, b in zip(agg[lab][0], agg["ACCURACY   top-k mean pass"][0]):
            w += a > b + 1e-9
            l += a < b - 1e-9
            t_ += abs(a - b) <= 1e-9
        print(f"  {lab:<30}{w:>7}{t_:>7}{l:>8}")


if __name__ == "__main__":
    k = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    main([p.name for p in sorted(N.RUNS.iterdir())
          if (p / "comparison.json").exists()], k=k)
