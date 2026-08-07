"""End-to-end smoke test: exercises the full graph pipeline S1-S7.

10 simulated tasks × 2 rounds using real HarnessConfigs and the
graph IR layer — no LLM API calls.

Exercises:
  S1 — to_graph() export + genotype/phenotype hashing + dedup
  S2 — pre-check metrics on simulated candidate data
  S3 — declaration backfill + graph build validation
  S4 — ObservationProcessor + footprint + reconciliation
  S5 — graph edit + danger edges + selective retest
  S6 — SkillGraph + invariants + edge-fault classification
  S7 — experiment comparison (text vs graph arms)
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from harnessx.core.builder import HarnessBuilder
from harnessx.bundles import context, coding, control
from harnessx.graph import (
    CoverageFootprint,
    DedupRegistry,
    FootprintStore,
    GraphEdit,
    GraphEditType,
    GraphSnapshot,
    apply_edits,
    backfill_declarations,
    compute_footprint,
    danger_edge_set,
    genotype_hash,
    graph_to_config_dict,
    intersects_footprint,
    phenotype_hash,
    reconcile,
    to_graph,
)
from harnessx.graph.declaration import WELL_KNOWN_DECLARATIONS
from harnessx.graph.edit import diff_graphs
from harnessx.graph.identity import DedupRegistry as Dedup
from harnessx.graph.observer import HookObservation, TaskTrace
from harnessx.graph.skill_graph import (
    SkillCategory,
    SkillEdge,
    SkillEdgeType,
    SkillGraph,
    SkillNode,
)
from harnessx.graph.skill_invariants import (
    check_acyclicity,
    check_non_contradiction,
)
from harnessx.graph.fault import classify_edge_faults, EdgeFaultType
from experiments.analysis.s2_precheck import (
    PreCheckMetrics,
    compute_duplicate_rate,
    compute_illegal_rate,
    compute_footprint_sparsity,
    decide_mode,
)
from experiments.analysis.s7_experiment import (
    ArmResult,
    ExperimentResult,
    compare_arms,
)
from experiments.variant_pool.selective_retest import (
    RetestDecision,
    SelectiveRetestEngine,
)


# ── helpers ─────────────────────────────────────────────────────────────────

def make_base_config():
    """Build the baseline harness config (V0)."""
    return (HarnessBuilder() | context | coding).build()


def make_variant_config():
    """Build a variant config (slightly different — context only)."""
    return (HarnessBuilder() | context).build()


def banner(msg: str) -> None:
    print(f"\n{'='*60}")
    print(f"  {msg}")
    print(f"{'='*60}")


# ── 10 simulated tasks ─────────────────────────────────────────────────────

TASK_IDS = [f"gaia_{i:03d}" for i in range(1, 11)]


# ── main smoke test ─────────────────────────────────────────────────────────

def run_smoke_test() -> dict:
    """Run the full end-to-end smoke test. Returns a results dict."""
    results: dict = {}

    # ══════════════════════════════════════════════════════════════════
    banner("S1: Graph export + genotype/phenotype hashing")
    # ══════════════════════════════════════════════════════════════════

    base_config = make_base_config()
    variant_config = make_variant_config()

    base_snapshot = to_graph(base_config)
    variant_snapshot = to_graph(variant_config)

    gh_base = genotype_hash(base_snapshot)
    gh_variant = genotype_hash(variant_snapshot)
    ph_base = phenotype_hash(base_snapshot)

    print(f"  Base config: {len(base_snapshot.nodes)} nodes, {len(base_snapshot.edges)} edges")
    print(f"  Variant config: {len(variant_snapshot.nodes)} nodes, {len(variant_snapshot.edges)} edges")
    print(f"  Genotype hash (base):    {gh_base[:16]}...")
    print(f"  Genotype hash (variant): {gh_variant[:16]}...")
    print(f"  Phenotype hash (base):   {ph_base[:16]}...")
    print(f"  Hash equality: {gh_base == gh_variant} (expected: {len(base_config.processors) == len(variant_config.processors)})")

    assert gh_base != gh_variant, "Different configs must have different hashes"
    results["s1_nodes"] = len(base_snapshot.nodes)
    results["s1_edges"] = len(base_snapshot.edges)

    # ══════════════════════════════════════════════════════════════════
    banner("S1b: Dedup registry")
    # ══════════════════════════════════════════════════════════════════

    dedup = DedupRegistry()
    assert dedup.register(gh_base, "C-R0-01")
    assert not dedup.register(gh_base, "C-R0-02")  # duplicate
    assert len(dedup) == 1
    print(f"  Dedup: {len(dedup)} unique among 2 submissions")
    results["s1_dedup_ok"] = True

    # ══════════════════════════════════════════════════════════════════
    banner("S2: Pre-check metrics")
    # ══════════════════════════════════════════════════════════════════

    # Simulate 10 candidate configs
    candidate_configs = [
        {"processors": list(base_config.processors)},
        {"processors": list(variant_config.processors)},
    ] * 5  # 10 configs, 5 pairs

    _, _, illegal_errors = compute_illegal_rate(candidate_configs)
    _, unique, dup_rate = compute_duplicate_rate(candidate_configs)

    # Simulated footprints
    footprints: dict[str, set[str]] = {
        tid: {f"hook:h{i}" for i in range(j % 5 + 2)}
        for j, tid in enumerate(TASK_IDS)
    }

    mean_sparsity, median_sparsity, pairs = compute_footprint_sparsity(footprints)

    metrics = PreCheckMetrics(
        total_candidates=10,
        illegal_count=len(illegal_errors),
        illegal_rate=len(illegal_errors) / 10,
        footprint_sparsity_mean=mean_sparsity,
        footprint_sparsity_median=median_sparsity,
        task_pair_count=pairs,
        duplicate_rate=dup_rate,
        unique_genotypes=unique,
    )

    decisions = decide_mode(metrics)
    print(f"  Illegal rate:     {metrics.illegal_rate:.2%}")
    print(f"  Duplicate rate:   {metrics.duplicate_rate:.2%}")
    print(f"  Sparsity (mean):  {metrics.footprint_sparsity_mean:.2%}")
    print(f"  Sparsity (median):{metrics.footprint_sparsity_median:.2%}")
    print(f"  Retest mode:      {decisions['retest_mode']}")
    print(f"  Dedup investment: {decisions['dedup_investment']}")
    results["s2_retest_mode"] = decisions["retest_mode"]

    # ══════════════════════════════════════════════════════════════════
    banner("S3: Declaration backfill + graph build validation")
    # ══════════════════════════════════════════════════════════════════

    targets = [p["_target_"] for p in base_config.processors if isinstance(p, dict)]
    decls = backfill_declarations(targets[:10])

    known_count = sum(1 for d in decls.values() if d.is_trusted())
    total_count = len(decls)
    print(f"  Targets: {total_count}, known (trusted): {known_count}")

    # Validate declarations — should have no conflicts
    from harnessx.graph.declaration import validate_declarations
    decl_errors = validate_declarations(decls)
    print(f"  Declaration errors: {len(decl_errors)}")
    assert len(decl_errors) == 0, f"Unexpected declaration errors: {decl_errors}"
    results["s3_known_decls"] = known_count

    # ══════════════════════════════════════════════════════════════════
    banner("S4: Observation + footprint + reconciliation")
    # ══════════════════════════════════════════════════════════════════

    # Simulate a task trace for 10 tasks
    traces: dict[str, TaskTrace] = {}
    for tid in TASK_IDS:
        trace = TaskTrace(task_id=tid, variant_id="V0")
        # Simulate observations: each task touches a subset of hooks
        for step in range(5):
            trace.record(HookObservation(
                step_id=step,
                hook_name="before_model",
                processor_label="SystemPromptProcessor",
            ))
            trace.record(HookObservation(
                step_id=step,
                hook_name="after_model",
                processor_label="model",
                tools_called=["Bash"],
            ))
        traces[tid] = trace

    # Compute footprints
    base = Path(tempfile.mkdtemp(prefix="s4_smoke_"))
    store = FootprintStore(base)

    for tid, trace in traces.items():
        fp = compute_footprint(trace, base_snapshot)
        fp.variant_id = "V0"
        store.put(fp)

    # Verify persistence
    loaded = store.get_all("V0")
    print(f"  Footprints stored: {len(loaded)}/{len(TASK_IDS)}")

    # Reconciliation
    all_observed_edges: set[str] = set()
    for tid in TASK_IDS:
        fp = store.get("V0", tid)
        if fp:
            all_observed_edges |= fp.observed_edge_keys

    report = reconcile(base_snapshot, all_observed_edges, set())
    print(f"  Convergence: {report.convergence_count}")
    print(f"  Divergence:  {report.divergence_count}")
    print(f"  Absence:     {report.absence_count}")
    print(f"  Erosion rate:{report.erosion_rate():.1%}")
    results["s4_footprints"] = len(loaded)
    results["s4_erosion_rate"] = report.erosion_rate()

    # ══════════════════════════════════════════════════════════════════
    banner("S5: Graph edits + danger edges + selective retest")
    # ══════════════════════════════════════════════════════════════════

    # Build a graph edit: insert a retry processor
    edit = GraphEdit(
        edit_type=GraphEditType.INSERT_NODE,
        node_spec={
            "_target_": "harnessx.processors.control.tool_failure_guard.ToolFailureGuard",
            "_hook_": "after_tool",
            "_singleton_group_": "retry_policy",
        },
        reason="Add retry logic for flaky tools",
    )

    # Apply edit to get new graph
    edited_snapshot = apply_edits(base_snapshot, [edit])
    print(f"  Edit type: {edit.edit_type.value}")
    print(f"  Nodes before: {len(base_snapshot.nodes)}, after: {len(edited_snapshot.nodes)}")

    # Danger edges
    danger_nodes, danger_edges = danger_edge_set([edit], base_snapshot)
    print(f"  Danger nodes: {len(danger_nodes)}, edges: {len(danger_edges)}")

    # Selective retest — 10 tasks
    engine = SelectiveRetestEngine(mode="heuristic", footprint_store=store)
    retest_report = engine.decide([edit], base_snapshot, TASK_IDS, "V0")
    print(f"  Retest: must={retest_report.must_retest}, "
          f"can_inherit={retest_report.can_inherit}, "
          f"uncertain={retest_report.uncertain}")
    print(f"  Budget saved: {retest_report.budget_saved_pct:.0%}")

    # Graph → config round-trip
    config_dict = graph_to_config_dict(base_snapshot)
    assert "processors" in config_dict
    print(f"  Round-trip: {len(config_dict['processors'])} processors")

    # Diff graphs
    diffs = diff_graphs(base_snapshot, edited_snapshot)
    print(f"  Diff: {len(diffs)} edit(s)")
    results["s5_danger_nodes"] = len(danger_nodes)
    results["s5_retest_saved"] = retest_report.can_inherit

    # ══════════════════════════════════════════════════════════════════
    banner("S6: SkillDAG + invariants + edge-fault classification")
    # ══════════════════════════════════════════════════════════════════

    # Build a skill graph with known tools
    sg = SkillGraph()
    for sid, name, cat in [
        ("search", "WebSearch", SkillCategory.TOOL),
        ("fetch", "WebFetch", SkillCategory.TOOL),
        ("bash", "Bash", SkillCategory.TOOL),
        ("read", "Read", SkillCategory.TOOL),
        ("write", "Write", SkillCategory.TOOL),
        ("code_agent", "CodeAgent", SkillCategory.COMPOSITE),
        ("research_agent", "ResearchAgent", SkillCategory.COMPOSITE),
    ]:
        sg.add_skill(SkillNode(sid, name, cat))

    # Add typed edges
    sg.add_edge(SkillEdge("research_agent", "search", SkillEdgeType.DEPENDS_ON))
    sg.add_edge(SkillEdge("research_agent", "fetch", SkillEdgeType.DEPENDS_ON))
    sg.add_edge(SkillEdge("code_agent", "bash", SkillEdgeType.DEPENDS_ON))
    sg.add_edge(SkillEdge("code_agent", "read", SkillEdgeType.DEPENDS_ON))
    sg.add_edge(SkillEdge("code_agent", "write", SkillEdgeType.DEPENDS_ON))
    sg.add_edge(SkillEdge("search", "fetch", SkillEdgeType.SIMILAR_TO))
    sg.add_edge(SkillEdge("research_agent", "code_agent", SkillEdgeType.COMPOSES_WITH))

    print(f"  Skills: {len(sg.nodes)}, Edges: {len(sg.edges)}")

    # Invariants
    acyc_violations = check_acyclicity(sg)
    contra_violations = check_non_contradiction(sg)
    print(f"  Acyclicity violations:   {len(acyc_violations)}")
    print(f"  Contradiction violations:{len(contra_violations)}")
    assert len(acyc_violations) == 0, f"Unexpected cycles: {acyc_violations}"
    assert len(contra_violations) == 0, f"Unexpected contradictions: {contra_violations}"

    # Edge-fault classification
    observed_edges = [
        ("research_agent", "search", "depends_on"),
        ("research_agent", "fetch", "depends_on"),
        ("code_agent", "bash", "depends_on"),
        # "code_agent"→"read" is MISSING (not observed)
        # "search"→"fetch" is WRONG (declared similar_to, but observed as depends_on at runtime)
        ("search", "fetch", "depends_on"),
    ]
    faults = classify_edge_faults(sg, observed_edges, current_round=2)

    by_type: dict[str, int] = {}
    for f in faults:
        by_type[f.fault_type.value] = by_type.get(f.fault_type.value, 0) + 1
    print(f"  Edge faults: {by_type}")

    # Detect transitive dependencies
    deps = sg.get_dependencies("research_agent")
    conflicts = sg.get_conflicts("research_agent")
    print(f"  research_agent deps: {deps}")
    print(f"  research_agent conflicts: {conflicts}")

    results["s6_skills"] = len(sg.nodes)
    results["s6_faults"] = len(faults)

    # ══════════════════════════════════════════════════════════════════
    banner("S7: Experiment comparison (text vs graph)")
    # ══════════════════════════════════════════════════════════════════

    text_arm = ArmResult(
        mode="text",
        total_candidates=10,
        shipped_candidates=4,
        final_pass_at_k=0.45,
        total_cost_usd=12.50,
    )
    graph_arm = ArmResult(
        mode="graph",
        total_candidates=10,
        shipped_candidates=6,
        illegal_rejected=2,
        duplicate_skipped=1,
        retest_inherited_tasks=30,
        retest_total_tasks=100,
        final_pass_at_k=0.50,
        total_cost_usd=9.20,
    )

    result = compare_arms(text_arm, graph_arm)
    print(result.summary())
    results["s7_pass_delta"] = result.pass_delta()
    results["s7_cost_delta"] = result.cost_delta()

    # ══════════════════════════════════════════════════════════════════
    banner("RESULTS SUMMARY")
    # ══════════════════════════════════════════════════════════════════

    print(f"\n  S1: {results['s1_nodes']} nodes, {results['s1_edges']} edges in graph snapshot")
    print(f"  S2: retest_mode={results['s2_retest_mode']}")
    print(f"  S3: {results['s3_known_decls']} known declarations")
    print(f"  S4: {results['s4_footprints']} footprints, erosion={results['s4_erosion_rate']:.0%}")
    print(f"  S5: {results['s5_danger_nodes']} danger nodes, {results['s5_retest_saved']} tasks saved")
    print(f"  S6: {results['s6_skills']} skills, {results['s6_faults']} edge faults")
    print(f"  S7: pass Δ={results['s7_pass_delta']:+.2%}, cost Δ=${results['s7_cost_delta']:+.2f}")
    print()

    return results


if __name__ == "__main__":
    run_smoke_test()
