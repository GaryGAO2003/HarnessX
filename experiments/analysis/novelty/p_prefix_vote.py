# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""Does the pool's answer history pay when you are only allowed to look backwards?

Plurality over every answer a run ever produced for a task is worth +20.6pp over
shipping one attempt. That number is not a protocol -- it reads round 14 to
decide round 3. The deployable version keeps an answer cache and, at round R,
votes over rounds < R plus the two attempts just made. Nothing from the future.

If the prefix version collapses to nothing, the whole "harvest the pool" line has
no payoff channel and should be abandoned tonight rather than after a paid run.

Four rules on the same cells, so the comparison is paired throughout:

  SINGLE   ship attempt 0. What the system effectively does.
  DONE     ship the attempt that terminated normally. One line, no cache.
  PREFIX   plurality over cache(rounds < R) + the two new attempts.  <- the test
  FULL     plurality over every round, including later ones. Time travel; kept
           only to show how much of the ceiling the honest version reaches.

ORACLE2 (pass@2 over the two current attempts) is reported alongside because it
is the paper's headline metric, and the distance from it to SINGLE is the slack
every rule here is competing for.

Uncertainty is a cluster bootstrap over (run, task): rounds of the same task
share a cache and an answer distribution, so resampling cells would treat one
task's luck as many independent observations.
"""
from __future__ import annotations

import collections
import pathlib
import random
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import n_answer_selection as N  # noqa: E402

_norm = lambda s: re.sub(r"[^a-z0-9 ]", "", s.strip().lower())  # noqa: E731


def _plurality_ok(atts):
    """Majority over normally-terminated answers; correct iff the winner passed."""
    ballot = [a for a in atts if a["exit"] == "done" and _norm(a["answer"])]
    if not ballot:
        return False
    votes = collections.Counter(_norm(a["answer"]) for a in ballot)
    top = votes.most_common(1)[0][0]
    return any(a["passed"] for a in ballot if _norm(a["answer"]) == top)


def _done_ok(atts):
    for a in atts:
        if a["exit"] == "done":
            return a["passed"]
    return atts[0]["passed"]


def build(runs):
    """(run, task) -> rounds in order, each with its own attempts."""
    by = collections.defaultdict(list)
    for c in N.load_cells(runs):
        r = c["round"]
        by[(c["run"], c["task"])].append((r if r is not None else 0, c["atts"]))
    out = {}
    for k, v in by.items():
        v.sort(key=lambda t: t[0])
        if len(v) >= 2:            # a cache needs at least one earlier round
            out[k] = v
    return out


def score(tasks):
    """One row per (run, task, round). Rules are evaluated on identical cells."""
    rows = []
    for key, seq in tasks.items():
        every = [a for _, atts in seq for a in atts]
        cache = []
        for rnd, atts in seq:
            rows.append({
                "key": key,
                "round": rnd,
                "cache": len(cache),
                "single": atts[0]["passed"],
                "done": _done_ok(atts),
                "oracle2": any(a["passed"] for a in atts),
                "prefix": _plurality_ok(cache + atts),
                "full": _plurality_ok(every),
            })
            cache = cache + atts
    return rows


def _boot(rows, a, b, reps=2000, seed=0):
    """Paired cluster bootstrap of mean(a) - mean(b), clustered on (run, task)."""
    rng = random.Random(seed)
    groups = collections.defaultdict(list)
    for r in rows:
        groups[r["key"]].append(r)
    keys = list(groups)
    diffs = []
    for _ in range(reps):
        pick = [groups[keys[rng.randrange(len(keys))]] for _ in keys]
        flat = [r for g in pick for r in g]
        n = len(flat)
        diffs.append(sum(r[a] for r in flat) / n - sum(r[b] for r in flat) / n)
    diffs.sort()
    return diffs[int(.025 * reps)], diffs[int(.975 * reps)]


def main(runs):
    tasks = build(runs)
    rows = score(tasks)
    n = len(rows)
    if not n:
        print("no eligible cells")
        return
    acc = {k: sum(r[k] for r in rows) / n
           for k in ("single", "done", "prefix", "full", "oracle2")}
    print(f"cells: {n}   tasks: {len(tasks)}   runs: "
          f"{len({k[0] for k in tasks})}")
    print(f"mean rounds per task: {n/len(tasks):.1f}\n")

    print("=" * 76)
    print("RULES (all on the same cells; CI = cluster bootstrap on (run,task))")
    print("=" * 76)
    print(f"  {'rule':<34}{'acc':>9}{'vs SINGLE':>12}{'95% CI':>20}")
    print(f"  {'-'*34}{'-'*9}{'-'*12}{'-'*20}")
    print(f"  {'SINGLE   ship attempt 0':<34}{100*acc['single']:8.2f}%"
          f"{0.0:+11.2f}pp{'--':>20}")
    for k, lab in (("done", "DONE     prefer clean exit"),
                   ("prefix", "PREFIX   cache(<R) + now   <<<"),
                   ("full", "FULL     all rounds (time travel)"),
                   ("oracle2", "ORACLE2  pass@2 on the 2 attempts")):
        lo, hi = _boot(rows, k, "single")
        print(f"  {lab:<34}{100*acc[k]:8.2f}%{100*(acc[k]-acc['single']):+11.2f}pp"
              f"   [{100*lo:+6.2f},{100*hi:+6.2f}]")

    lo, hi = _boot(rows, "prefix", "done")
    print(f"\n  what the CACHE adds on top of the one-line rule:"
          f"  {100*(acc['prefix']-acc['done']):+.2f}pp   "
          f"95% CI [{100*lo:+.2f}, {100*hi:+.2f}]")
    lo, hi = _boot(rows, "full", "prefix")
    print(f"  price of refusing time travel:                  "
          f"{100*(acc['full']-acc['prefix']):+.2f}pp   "
          f"95% CI [{100*lo:+.2f}, {100*hi:+.2f}]")

    print("\n" + "=" * 76)
    print("DOES THE CACHE WARM UP? (prefix - done, by how much history exists)")
    print("=" * 76)
    bands = [(0, 0), (1, 4), (5, 12), (13, 28), (29, 10**6)]
    print(f"  {'cache size':<16}{'cells':>7}{'SINGLE':>9}{'DONE':>9}"
          f"{'PREFIX':>9}{'PREFIX-DONE':>13}")
    for lo_, hi_ in bands:
        sub = [r for r in rows if lo_ <= r["cache"] <= hi_]
        if len(sub) < 20:
            continue
        m = len(sub)
        s = sum(r["single"] for r in sub) / m
        d = sum(r["done"] for r in sub) / m
        p = sum(r["prefix"] for r in sub) / m
        lab = f"{lo_}" if lo_ == hi_ else (f"{lo_}-{hi_}" if hi_ < 10**6 else f"{lo_}+")
        print(f"  {lab:<16}{m:7d}{100*s:8.1f}%{100*d:8.1f}%{100*p:8.1f}%"
              f"{100*(p-d):+12.2f}pp")
    print("\n  a cache of 0 is the first round a task appears; PREFIX there is just")
    print("  plurality over the two current attempts, so it should sit near DONE.")
    print("  a rising last column is the mechanism actually earning its keep.")


if __name__ == "__main__":
    main([p.name for p in sorted(N.RUNS.iterdir())
          if (p / "comparison.json").exists()])
