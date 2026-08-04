# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""Analysis A -- selective-invocation bottleneck (which stage dries a round up).

Reads (per round, read-only):
  * pool_state.json      : no_candidate, shipped, paper_target_variant, routing,
                           candidate_accounting.requested_slots / actual_candidates
  * R*/V*/pipeline_audit.json (when present): actionability, threshold,
                           provenance, short_circuit

Stage taxonomy (verified against candidate_pipeline.py / engine.py):
  bootstrap          round 0, no target, no candidate pipeline by design
  routing_starvation UPSTREAM: worst_first target carries 0 routed tasks =>
                     the pipeline has no failing-task evidence and never
                     requests a slot (requested_slots == 0, no audit written)
  digester_below_a   short_circuit == 'actionability_below_threshold'
  planner_empty      short_circuit in {empty_landscape, planner_empty_landscape}
  evolver_no_valid   short_circuit == 'no_valid_candidates'
  critic_or_gate     DOWNSTREAM: pipeline ran (requested_slots>0) but nothing
                     shipped (all candidates rejected at critic/gate)
  shipped            an edit shipped

Counterfactual (indicative): for the digester actionability gate only, how many
rounds would *not* be short-circuited at a given alpha. Only rounds whose
actionability is persisted (the 9 that ran the pipeline) can be scored; drought
rounds persist no actionability because the pipeline never ran, so alpha cannot
apply to them. Labelled INDICATIVE -- it counts "would-the-digester-gate-fire",
never "would-a-candidate-ship".

Usage:  python experiments/analysis/novelty/a_selective_invocation.py [run]
"""

from __future__ import annotations

import sys

import _common as C

SHORT_CIRCUIT_STAGE = {
    "actionability_below_threshold": "digester_below_a",
    "empty_landscape": "planner_empty",
    "planner_empty_landscape": "planner_empty",
    "no_valid_candidates": "evolver_no_valid",
}


def classify(run: str, r: int, state: dict) -> dict:
    ptgt = state.get("paper_target_variant")
    routing = state.get("routing", {})
    tgt_carry = len(routing.get(ptgt, [])) if ptgt else None
    acct = state.get("candidate_accounting", {}) or {}
    req = acct.get("requested_slots")
    actual = acct.get("actual_candidates")
    audit = C.find_pipeline_audit(run, r)
    act = thr = prov = sc = None
    if audit is not None:
        _, pa = audit
        act = pa.get("actionability")
        thr = pa.get("actionability_threshold")
        prov = pa.get("actionability_threshold_provenance")
        sc = pa.get("short_circuit")

    if r == 0 and ptgt is None:
        stage = "bootstrap"
    elif sc in SHORT_CIRCUIT_STAGE:
        stage = SHORT_CIRCUIT_STAGE[sc]
    elif state.get("no_candidate"):
        # requested_slots==0 and target carries no tasks => routing starvation.
        stage = "routing_starvation" if (req == 0 and tgt_carry == 0) else "upstream_no_candidate"
    elif state.get("shipped"):
        stage = "shipped"
    else:
        stage = "critic_or_gate"  # pipeline ran, candidates rejected downstream

    return {
        "round": r,
        "no_candidate": state.get("no_candidate"),
        "shipped": state.get("shipped"),
        "target": ptgt,
        "tgt_carry": tgt_carry,
        "requested_slots": req,
        "actual_candidates": actual,
        "has_audit": audit is not None,
        "actionability": act,
        "threshold": thr,
        "provenance": prov,
        "short_circuit": sc,
        "stage": stage,
    }


def main(run: str = C.DEFAULT_RUN) -> None:
    states = C.all_states(run)
    rows = [classify(run, r, states[r]) for r in sorted(states)]

    print(f"# Analysis A -- selective-invocation bottleneck  (run={run})\n")
    hdr = ("R", "nocand", "ship", "tgt", "carry", "req", "act#", "audit", "a_t", "alpha", "stage")
    print("{:>3} {:>6} {:>5} {:>4} {:>5} {:>4} {:>4} {:>5} {:>5} {:>5}  {}".format(*hdr))
    for row in rows:
        print("{:>3} {:>6} {:>5} {:>4} {:>5} {:>4} {:>4} {:>5} {:>5} {:>5}  {}".format(
            row["round"],
            str(row["no_candidate"]),
            str(row["shipped"]),
            str(row["target"]),
            str(row["tgt_carry"]),
            str(row["requested_slots"]),
            str(row["actual_candidates"]),
            "Y" if row["has_audit"] else "-",
            str(row["actionability"]),
            str(row["threshold"]),
            row["stage"],
        ))

    # provenance (single value across the run)
    prov = next((r["provenance"] for r in rows if r["provenance"]), None)
    print(f"\nactionability_threshold_provenance: {prov}")

    # stage tally
    from collections import Counter
    tally = Counter(r["stage"] for r in rows)
    print("\nstage tally:", dict(tally))

    droughts = [r["round"] for r in rows if r["no_candidate"]]
    starved = [r["round"] for r in rows if r["stage"] == "routing_starvation"]
    downstream = [r["round"] for r in rows if r["stage"] in ("critic_or_gate",)]
    print(f"drought rounds (no_candidate)     : {droughts}")
    print(f"  -> routing_starvation (upstream): {starved}")
    print(f"  -> bootstrap                    : {[r['round'] for r in rows if r['stage']=='bootstrap']}")
    print(f"downstream critic/gate rejections : {downstream}")

    # --- alpha counterfactual (INDICATIVE, re-scoring; digester gate only) ---
    scored = [r for r in rows if r["actionability"] is not None]
    alphas = [0.0, 0.3, 0.5, 0.7]
    print("\n[INDICATIVE / re-scoring] digester actionability-gate counterfactual")
    print("  (only the {} rounds that ran the pipeline persist an actionability;".format(len(scored)))
    print("   drought rounds persist none -> alpha cannot rescue them, they are")
    print("   routing-starved upstream of the digester.)")
    print("  actionability values on scored rounds:",
          {r["round"]: r["actionability"] for r in scored})
    print("  {:>6}  {:>18}  {:>22}".format("alpha", "scored-rounds-continue", "scored-rounds-shortcircuit"))
    for a in alphas:
        # boundary: a_t >= alpha continues ('equality continues', pipeline code).
        cont = [r["round"] for r in scored if float(r["actionability"]) >= a]
        cut = [r["round"] for r in scored if float(r["actionability"]) < a]
        tag = "  (actual alpha)" if a == 0.5 else ""
        print("  {:>6}  {:>18}  {:>22}{}".format(a, len(cont), len(cut), tag))
    minact = min(float(r["actionability"]) for r in scored)
    maxact = max(float(r["actionability"]) for r in scored)
    cut_at = {a: sum(1 for r in scored if float(r["actionability"]) < a) for a in alphas}
    if len(set(cut_at.values())) == 1:
        print(f"  min persisted actionability = {minact}; every alpha in {alphas} cuts the "
              f"same {next(iter(cut_at.values()))} of {len(scored)} scored rounds "
              f"-> alpha is inert in the observed range.")
    else:
        print(f"  min persisted actionability = {minact}; the cut count MOVES with alpha "
              f"({', '.join(f'{a}->{n}' for a, n in cut_at.items())} of {len(scored)}) "
              f"-> alpha is NOT inert in the observed range.")

    # --- conclusion, derived from this run's own rows (never hard-coded) ---
    print("\nCONCLUSION:")
    n_starve = len(starved)
    n_boot = sum(1 for r in rows if r["stage"] == "bootstrap")
    n_dig = sum(1 for r in rows if r["stage"] == "digester_below_a")
    n_plan = sum(1 for r in rows if r["stage"] == "planner_empty")
    n_down = len(downstream)
    cut_actual = cut_at[0.5]
    parts = []
    if n_starve:
        parts.append(f"{n_starve} routing_starvation (target carried 0 routed tasks)")
    if n_dig:
        parts.append(f"{n_dig} digester_below_alpha")
    if n_plan:
        parts.append(f"{n_plan} planner_empty")
    if n_boot:
        parts.append(f"{n_boot} bootstrap")
    print(f"  {len(droughts)} drought rounds break down as: {'; '.join(parts) or 'none'}.")
    if n_dig:
        print(f"  The digester gate DOES bite: actionability fell below alpha=0.5 in "
              f"{cut_actual} of {len(scored)} scored rounds (range {minact}-{maxact}).")
        print(f"  Whether alpha is the lever depends on the counterfactual table above,")
        print(f"  not on this line -- read the cut counts, they are the evidence.")
    else:
        print(f"  The digester gate never bit: actionability was {minact}-{maxact}, always "
              f">= alpha=0.5, so the actionability threshold is not the lever here.")
    print(f"  {n_down} non-drought rounds ({downstream}) failed downstream "
          f"(all candidates rejected at critic/gate).")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else C.DEFAULT_RUN)
