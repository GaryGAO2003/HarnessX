# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""Can the pool's answers be harvested WITHOUT time travel?

`n_answer_selection` showed the pool emits ~16 answers per task across a run and
that a majority vote over all of them scores 80.24% against 59.67% for a single
attempt. That number pools answers from every round, including rounds that had
not happened yet at the moment the answer was needed -- it is a ceiling, not a
protocol.

The protocol this file tests is causal:

    at round R, task T's shipped answer = majority over every normally-terminated
    answer produced for T by any variant in rounds <= R, ties broken by recency.

No future information. No extra rollouts -- the answers were already paid for by
the evolution loop, which discards them. If a real deployment cached them, this
is free.

This is the kill switch for the whole ensemble-harvest direction. If prefix
voting buys nothing over shipping the current round's best-terminating attempt,
there is no delivery channel and the direction has to be restructured.

Validity gates, checked before any headline number is printed:

  G1  answer->verdict consistency. The grader must give the same verdict to the
      same answer string on the same task. If it does not, "the majority answer
      is correct" is not even well defined, and every number below is noise.
  G2  a shuffled-history control. Re-run the protocol with each task's history
      replaced by another task's. Any rule that still gains is reading something
      other than the task's own answer distribution.

Rule set is deliberately austere -- exit_reason, answer strings, round order.
No LLM, no learned scorer, nothing that costs a token to evaluate.
"""
from __future__ import annotations

import collections
import pathlib
import random
import re
import statistics as st
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import n_answer_selection as NA  # noqa: E402

RUNS = NA.RUNS
_NORM = re.compile(r"[^a-z0-9 ]")


def norm(s: str) -> str:
    return _NORM.sub("", s.strip().lower()).strip()


# ------------------------------------------------------------------ loading
def build_history(runs):
    """(run, task) -> [(round, [attempt, ...]), ...] ordered by round."""
    by = collections.defaultdict(lambda: collections.defaultdict(list))
    for c in NA.load_cells(runs):
        r = c["round"]
        if r is None:
            continue
        by[(c["run"], c["task"])][int(r)].extend(c["atts"])
    return {k: sorted(v.items()) for k, v in by.items()}


def verdict_map(hist):
    """(run,task) -> {normalised answer: (n_pass, n_fail)} over the whole run."""
    out = {}
    for k, rounds in hist.items():
        m = collections.defaultdict(lambda: [0, 0])
        for _, atts in rounds:
            for a in atts:
                n = norm(a["answer"])
                if n:
                    m[n][0 if a["passed"] else 1] += 1
        out[k] = m
    return out


def gate_consistency(vmap):
    """G1: does the grader ever give one answer string both verdicts?"""
    tot = amb = 0
    for m in vmap.values():
        for _, (p, f) in m.items():
            tot += 1
            amb += p > 0 and f > 0
    return tot, amb


# ------------------------------------------------------------------- rules
def _majority(pool, vmap_task):
    """pool = [(round, attempt)] ordered oldest-first. Returns True if the
    modal answer is a correct one. Ties broken by most recent occurrence."""
    if not pool:
        return None
    cnt = collections.Counter()
    last = {}
    for i, (_, a) in enumerate(pool):
        n = norm(a["answer"])
        if not n:
            continue
        cnt[n] += 1
        last[n] = i
    if not cnt:
        return None
    top = max(cnt, key=lambda n: (cnt[n], last[n]))
    p, f = vmap_task.get(top, (0, 0))
    return p > f


def evaluate(hist, vmap, window=None, shuffle_seed=None):
    """Walk every (task, round) decision point and score each rule.

    window=None      -> prefix is the whole history up to and including R
    window=w         -> only the last w rounds
    shuffle_seed     -> G2 control: the prefix comes from a DIFFERENT task
    """
    keys = list(hist)
    donor = dict(zip(keys, keys))
    if shuffle_seed is not None:
        rng = random.Random(shuffle_seed)
        shuffled = keys[:]
        rng.shuffle(shuffled)
        donor = dict(zip(keys, shuffled))

    per_task = collections.defaultdict(lambda: collections.Counter())
    depth_hit = collections.defaultdict(lambda: collections.Counter())

    for k, rounds in hist.items():
        dk = donor[k]
        drounds = hist[dk]
        vt = vmap[k]
        for idx, (R, atts) in enumerate(rounds):
            c = per_task[k]
            c["n"] += 1
            # --- rules that see only the current round -----------------
            c["R0_first"] += atts[0]["passed"]
            done_now = [a for a in atts if a["exit"] == "done"]
            c["R1_done"] += (done_now[0] if done_now else atts[0])["passed"]
            c["oracle_round"] += any(a["passed"] for a in atts)
            # --- prefix pool -------------------------------------------
            src = drounds if shuffle_seed is not None else rounds
            lo = 0 if window is None else max(0, idx + 1 - window)
            hi = min(idx + 1, len(src))
            pool = [(r, a) for r, sub in src[lo:hi] for a in sub
                    if a["exit"] == "done" and norm(a["answer"])]
            if shuffle_seed is None:
                depth = len({r for r, _ in pool})
            else:
                depth = idx + 1
            got = _majority(pool, vt)
            if got is None:                       # nothing terminated normally
                got = (done_now[0] if done_now else atts[0])["passed"]
            c["R2_prefix"] += bool(got)
            depth_hit[min(depth, 8)]["n"] += 1
            depth_hit[min(depth, 8)]["R0"] += atts[0]["passed"]
            depth_hit[min(depth, 8)]["R1"] += (done_now[0] if done_now
                                               else atts[0])["passed"]
            depth_hit[min(depth, 8)]["R2"] += bool(got)
    return per_task, depth_hit


def rate(per_task, field):
    n = sum(c["n"] for c in per_task.values())
    return sum(c[field] for c in per_task.values()) / n


def boot_ci(per_task, a, b, reps=2000, seed=0):
    """Bootstrap the paired difference a-b, resampling TASKS (the unit of
    independence -- rounds within a task are not independent)."""
    keys = list(per_task)
    rng = random.Random(seed)
    out = []
    for _ in range(reps):
        s = [per_task[keys[rng.randrange(len(keys))]] for _ in keys]
        n = sum(c["n"] for c in s) or 1
        out.append((sum(c[a] for c in s) - sum(c[b] for c in s)) / n)
    out.sort()
    return out[int(.025 * reps)], out[int(.975 * reps)]


# ------------------------------------------------------------------- main
def main(runs):
    hist = build_history(runs)
    vmap = verdict_map(hist)
    tot, amb = gate_consistency(vmap)

    print("=" * 76)
    print("G1  GRADER CONSISTENCY (must pass before anything below means anything)")
    print("=" * 76)
    print(f"  distinct (task, answer) pairs: {tot}")
    print(f"  graded BOTH pass and fail    : {amb}  ({100*amb/max(tot,1):.2f}%)")
    if amb / max(tot, 1) > 0.02:
        print("  -> FAIL: 'the majority answer is correct' is not well defined.")
    else:
        print("  -> pass")

    ntask = len(hist)
    ndec = sum(len(v) for v in hist.values())
    print(f"\n  (run,task) series: {ntask}   decision points: {ndec}   "
          f"mean rounds/task: {ndec/max(ntask,1):.1f}")

    per, depth = evaluate(hist, vmap)
    print("\n" + "=" * 76)
    print("PREFIX-ONLY PROTOCOL  (no future information, no extra rollouts)")
    print("=" * 76)
    r0, r1, r2 = (rate(per, k) for k in ("R0_first", "R1_done", "R2_prefix"))
    orc = rate(per, "oracle_round")
    print(f"  {'rule':<40}{'acc':>9}{'vs R0':>10}{'95% CI':>18}")
    print(f"  {'-'*40}{'-'*9}{'-'*10}{'-'*18}")
    print(f"  {'R0  ship attempt 0 (what runs today)':<40}{100*r0:8.2f}%"
          f"{0.0:+9.2f}pp{'--':>18}")
    for lbl, key, val in (("R1  prefer exit=done, this round", "R1_done", r1),
                          ("R2  majority over prefix history", "R2_prefix", r2)):
        lo, hi = boot_ci(per, key, "R0_first")
        print(f"  {lbl:<40}{100*val:8.2f}%{100*(val-r0):+9.2f}pp"
              f"   [{100*lo:+.2f},{100*hi:+.2f}]")
    lo, hi = boot_ci(per, "R2_prefix", "R1_done")
    print(f"  {'      ... R2 over R1 (the real question)':<40}{'':>9}"
          f"{100*(r2-r1):+9.2f}pp   [{100*lo:+.2f},{100*hi:+.2f}]")
    print(f"  {'oracle  any attempt this round':<40}{100*orc:8.2f}%"
          f"{100*(orc-r0):+9.2f}pp{'--':>18}")

    print("\n" + "=" * 76)
    print("HISTORY DEPTH  -- does the protocol need a long cache to work?")
    print("=" * 76)
    print(f"  {'rounds of history':<20}{'n':>7}{'R0':>9}{'R1':>9}{'R2':>9}{'R2-R1':>9}")
    for d in sorted(depth):
        c = depth[d]
        n = c["n"]
        f = lambda k: 100 * c[k] / n
        print(f"  {('>=8' if d==8 else str(d)):<20}{n:7d}{f('R0'):8.1f}%"
              f"{f('R1'):8.1f}%{f('R2'):8.1f}%{f('R2')-f('R1'):+8.1f}pp")

    print("\n" + "=" * 76)
    print("WINDOWED PREFIX -- is stale history hurting?")
    print("=" * 76)
    print(f"  {'window (rounds)':<20}{'R2 acc':>10}{'vs full':>10}")
    for w in (2, 3, 4, 6, None):
        p, _ = evaluate(hist, vmap, window=w)
        v = rate(p, "R2_prefix")
        print(f"  {(str(w) if w else 'all'):<20}{100*v:9.2f}%{100*(v-r2):+9.2f}pp")

    print("\n" + "=" * 76)
    print("G2  SHUFFLED-HISTORY CONTROL  (prefix taken from a DIFFERENT task)")
    print("=" * 76)
    ctrl = []
    for s in range(5):
        p, _ = evaluate(hist, vmap, shuffle_seed=s)
        ctrl.append(rate(p, "R2_prefix"))
    m = st.mean(ctrl)
    print(f"  R2 on shuffled history: {100*m:.2f}%  (5 seeds, sd "
          f"{100*st.stdev(ctrl):.2f}pp)")
    print(f"  R2 on true history    : {100*r2:.2f}%")
    print(f"  true - shuffled       : {100*(r2-m):+.2f}pp")
    print("  -> a real effect must show a large gap here. A small gap means the")
    print("     rule is exploiting the answer prior, not the task's own history.")


if __name__ == "__main__":
    argv = sys.argv[1:]
    main(argv or [p.name for p in sorted(RUNS.iterdir())
                  if (p / "comparison.json").exists()])
