# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""Analysis B -- all-k interception rate (soft regressions the any-k seesaw misses).

Verified field semantics (see report 05-OFFLINE-RESULTS.md, section B):
  * active_pool_measurements is a PER-ROUND pass@2 snapshot (n_att == pass_k
    every round, never cumulative) -- proved by step 1 below.
  * The gate's seesaw (gate.py _classify) is ANY-K:
        improved  = before_pass == 0 and after_pass >= 1
        regressed = after_pass == 0 (and ever-solved)
    and the gate's `before` is binarised to (2,2)/(0,2) (engine _task_eval),
    so a 2/2 -> 1/2 drop is structurally invisible to it.

Edit-attributable before/after (both pass@2, att consistent), for a ship round R
with target ptgt and shipped candidate:
  before[t] = active_pool_measurements[ptgt][t]     (parent, NO edit, fresh
              rollout this same round -- ptgt config is unchanged until the fork
              settles after the round)
  after[t]  = candidate_gate_measurements[ptgt][t]  (candidate, WITH edit)
  scope     = tasks present in both = the parent's retained tasks (the fork's
              improved tasks move to the child and are not re-measured under the
              parent; by construction they are improvements, not regressions).

  soft regression = before_pass > after_pass >= 1   (any-k blind, all-k sees)
  hard regression = before_pass >= 1 and after_pass == 0   (both see)
  improvement     = after_pass > before_pass

Item 3 (soft/hard counts) is an EXACT count of stored measurements.
Item 4 (all-k would-block + one-step forward score change) is RE-SCORING /
INDICATIVE and never mixed into the exact columns.

Usage:  python experiments/analysis/novelty/b_allk_interception.py [run]
"""

from __future__ import annotations

import sys

import _common as C


def verify_snapshot(states: dict[int, dict]) -> dict:
    """Step 1: snapshot vs cumulative -- compare att for the same (variant,task)
    across adjacent rounds. Cumulative would grow att; a snapshot keeps att==pass_k.
    """
    seen: dict[tuple[str, str], dict[int, list]] = {}
    for r in sorted(states):
        for vid, tasks in states[r].get("active_pool_measurements", {}).items():
            for tid, sa in tasks.items():
                seen.setdefault((vid, tid), {})[r] = sa
    atts = set()
    grew = 0
    multi = 0
    for (_vid, _tid), byr in seen.items():
        rounds = sorted(byr)
        if len(rounds) >= 2:
            multi += 1
        prev_att = None
        for r in rounds:
            att = byr[r][1]
            atts.add(att)
            if prev_att is not None and att > prev_att:
                grew += 1
            prev_att = att
    return {
        "distinct_att_values": sorted(atts),
        "pairs_tracked_multi_round": multi,
        "adjacent_att_increases": grew,
        "verdict": "SNAPSHOT" if atts <= {C.PASS_K} and grew == 0 else "CUMULATIVE/OTHER",
    }


def ship_contrast(state: dict) -> dict:
    """before/after per retained task for one ship round."""
    ptgt = state["paper_target_variant"]
    before = {t: v for t, v in state.get("active_pool_measurements", {}).get(ptgt, {}).items()}
    after = {t: v for t, v in state.get("candidate_gate_measurements", {}).get(ptgt, {}).items()}
    shared = sorted(set(before) & set(after))
    soft, hard, improved, flat = [], [], [], []
    for t in shared:
        bp = int(before[t][0])
        ap = int(after[t][0])
        if ap > bp:
            improved.append(t)
        elif bp > ap >= 1:
            soft.append(t)
        elif bp >= 1 and ap == 0:
            hard.append(t)
        else:
            flat.append(t)
    return {
        "target": ptgt,
        "child": (state.get("forked") or [None])[0],
        "n_shared": len(shared),
        "soft": soft,
        "hard": hard,
        "improved": improved,
        "n_soft": len(soft),
        "n_hard": len(hard),
        "n_improved": len(improved),
        "before": before,
        "after": after,
        "shared": shared,
    }


def next_round_delta(states: dict[int, dict], ship_round: int, child: str) -> dict | None:
    """One-step forward (indicative): on the tasks the CHILD carries in R+1, the
    mean pass@2 change from round R (pool-wide incumbent score) to round R+1.

    Every task carried in R+1 was measured by some carrier in R (routing
    partitions the fixed task set), so coverage is complete regardless of how the
    child's routing rotated across the ship boundary.
    """
    r0 = states.get(ship_round)
    r1 = states.get(ship_round + 1)
    if not r0 or not r1 or child is None:
        return None
    child_tasks = sorted(r1.get("active_pool_measurements", {}).get(child, {}))
    if not child_tasks:
        return {"n": 0, "mean_delta_pass": None}
    prev = C.pool_task_scores(r0)   # task -> (pass, att) in R
    m1 = r1["active_pool_measurements"][child]
    pairs = [(int(m1[t][0]) - prev[t][0]) for t in child_tasks if t in prev]
    return {"n": len(pairs), "mean_delta_pass": C.mean(pairs)}


def main(run: str = C.DEFAULT_RUN) -> None:
    states = C.all_states(run)
    ships = C.ship_rounds(states)

    print(f"# Analysis B -- all-k interception  (run={run})\n")

    # 1. snapshot vs cumulative
    v = verify_snapshot(states)
    print("[1] active_pool_measurements snapshot-vs-cumulative verification")
    print(f"    distinct n_att values across all rounds : {v['distinct_att_values']}")
    print(f"    (variant,task) pairs seen in >=2 rounds : {v['pairs_tracked_multi_round']}")
    print(f"    adjacent-round n_att increases          : {v['adjacent_att_increases']}")
    print(f"    VERDICT: {v['verdict']}  "
          f"(n_att is constant == pass_k every round => per-round snapshot, not cumulative)\n")

    # 2/3. per-ship exact soft/hard counts
    print("[2/3] EXACT per-ship soft/hard regression counts")
    print("      before = parent active_pool (no edit) ; after = candidate_gate (edit)")
    print("      {:>3} {:>5} {:>5} {:>7} {:>5} {:>5} {:>5}".format(
        "R", "tgt", "child", "shared", "impr", "soft", "hard"))
    contrasts = {}
    tot_soft = tot_hard = 0
    for r in ships:
        c = ship_contrast(states[r])
        contrasts[r] = c
        tot_soft += c["n_soft"]
        tot_hard += c["n_hard"]
        print("      {:>3} {:>5} {:>5} {:>7} {:>5} {:>5} {:>5}".format(
            r, str(c["target"]), str(c["child"]), c["n_shared"],
            c["n_improved"], c["n_soft"], c["n_hard"]))
    print("      {:>3} {:>5} {:>5} {:>7} {:>5} {:>5} {:>5}".format(
        "SUM", "", "", "", "", tot_soft, tot_hard))
    print(f"      => the shipped edits caused {tot_soft} soft regressions the any-k")
    print(f"         seesaw could not see, and {tot_hard} hard regressions it could.\n")

    # 4. all-k counterfactual: block if ANY succ decrease (soft or hard)
    print("[4] [RE-SCORING / INDICATIVE] all-k seesaw (require succ non-decreasing)")
    print("    A ship is BLOCKED under all-k iff it has >=1 soft-or-hard regression")
    print("    on the edit contrast above. One-step forward delta is the child's mean")
    print("    pass@2 change on its carried tasks from R to R+1 (single step, indicative).")
    blocked = []
    print("    {:>3} {:>6} {:>5} {:>5} {:>9}  {}".format(
        "R", "block?", "soft", "hard", "fwd_n", "child_mean_dpass(R->R+1)"))
    for r in ships:
        c = contrasts[r]
        is_blocked = (c["n_soft"] + c["n_hard"]) > 0
        if is_blocked:
            blocked.append(r)
        nd = next_round_delta(states, r, c["child"])
        fwd_n = nd["n"] if nd else None
        fwd_d = nd["mean_delta_pass"] if nd else None
        fwd_s = f"{fwd_d:+.3f}" if isinstance(fwd_d, float) else str(fwd_d)
        print("    {:>3} {:>6} {:>5} {:>5} {:>9}  {}".format(
            r, "BLOCK" if is_blocked else "pass", c["n_soft"], c["n_hard"],
            str(fwd_n), fwd_s))
    print(f"    => under all-k, {len(blocked)}/{len(ships)} shipped edits would be blocked: {blocked}")
    print("    (all 7 ships were FORKs; a fork by construction carries >=1 regressed")
    print("     task, so any-succ-decrease all-k blocks them all -- the interesting")
    print("     number is the SOFT count above, invisible to the shipped any-k gate.)")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else C.DEFAULT_RUN)
