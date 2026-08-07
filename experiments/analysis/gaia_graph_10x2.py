"""GAIA 10-task × 2-round graph-aware evaluation (AEGIS pipeline integration).

Loads 10 real GAIA tasks, sets up a minimal VariantPoolEngine, and
exercises the graph pipeline (S1-S6) against real task configs.
Zero LLM API calls — uses deterministic simulated evolver.

Outputs a graph-aware run report in JSON format.
"""

from __future__ import annotations

import json
import tempfile
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from benchmarks.gaia.harness import make_gaia_builder_gpt5
from benchmarks.gaia.task import load_gaia_tasks_from_json
from harnessx.core.builder import HarnessBuilder
from harnessx.core.model_config import ModelConfig
from harnessx.graph import (
    DedupRegistry,
    FootprintStore,
    GraphEdit,
    GraphEditType,
    apply_edits,
    backfill_declarations,
    compute_footprint,
    danger_edge_set,
    genotype_hash,
    reconcile,
    to_graph,
)
from harnessx.graph.declaration import WELL_KNOWN_DECLARATIONS
from harnessx.graph.fault import classify_edge_faults
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
from experiments.variant_pool.engine import VariantPoolEngine
from experiments.variant_pool.pool import Variant, VariantPool
from experiments.variant_pool.ledger import SuccessLedger
from experiments.variant_pool.router import Router
from experiments.variant_pool.gate import Decision, GateResult, GateStage, run_gate
from experiments.variant_pool.selective_retest import (
    SelectiveRetestEngine,
    RetestReport,
)
from experiments.analysis.s2_precheck import PreCheckMetrics, decide_mode


# ── config ──────────────────────────────────────────────────────────────────

DATA_DIR = Path("recipe/gaia_evolver/data")
TASK_FILE = DATA_DIR / "smoke10.json"  # 10 webthinker-style GAIA tasks
OUTPUT_DIR = Path("experiments/analysis/output")
ROUNDS = 2
POOL_K = 1  # single variant for simplicity

MODEL_NAME = "claude-sonnet-4-6"  # declared, not called


@dataclass
class GraphRunReport:
    """Per-round and aggregate graph metrics for a run."""

    run_id: str = ""
    task_ids: list[str] = field(default_factory=list)
    rounds: int = 0

    # S1
    genotypes_seen: int = 0
    duplicates_caught: int = 0

    # S2
    precheck: dict = field(default_factory=dict)

    # S3
    known_declarations: int = 0
    total_targets: int = 0

    # S4
    footprints_stored: int = 0

    # S5
    retest_reports: list[dict] = field(default_factory=list)

    # S6
    skill_count: int = 0
    edge_faults_detected: int = 0

    per_round: list[dict] = field(default_factory=list)


# ── main ────────────────────────────────────────────────────────────────────


def run() -> GraphRunReport:
    """Run the 10-task × 2-round graph-aware evaluation."""
    report = GraphRunReport(run_id="gaia_graph_10x2")

    # 1. Load GAIA tasks
    print("Loading GAIA tasks...")
    tasks = load_gaia_tasks_from_json(
        str(TASK_FILE),
        max_tasks=10,
    )
    task_ids = [t.task_id for t in tasks]
    report.task_ids = task_ids
    report.rounds = ROUNDS
    print(f"  Loaded {len(tasks)} tasks: {task_ids[:5]}...")

    # 2. Build base harness config
    print("Building base harness config...")
    base_config = (HarnessBuilder() | make_gaia_builder_gpt5()).build()

    # 3. Export to graph (S1)
    print("Exporting to graph (S1)...")
    base_snapshot = to_graph(base_config)
    gh = genotype_hash(base_snapshot)
    report.genotypes_seen = 1
    print(f"  {len(base_snapshot.nodes)} nodes, {len(base_snapshot.edges)} edges")
    print(f"  Genotype: {gh[:16]}...")

    # 4. Declarations (S3)
    print("Backfilling declarations (S3)...")
    targets = [
        p["_target_"] for p in base_config.processors
        if isinstance(p, dict) and "_target_" in p
    ]
    decls = backfill_declarations(targets)
    report.known_declarations = sum(1 for d in decls.values() if d.is_trusted())
    report.total_targets = len(targets)
    known_pct = report.known_declarations / max(report.total_targets, 1) * 100
    print(f"  {report.known_declarations}/{report.total_targets} known ({known_pct:.0f}%)")

    # 5. Variant pool setup (AEGIS)
    print("Setting up VariantPool (AEGIS)...")
    pool_dir = Path(tempfile.mkdtemp(prefix="gaia_10x2_pool_"))
    config_path = pool_dir / "V0" / "config.yaml"
    config_path.parent.mkdir(parents=True, exist_ok=True)

    # Write base config as YAML
    import yaml
    config_path.write_text(base_config.to_yaml(), encoding="utf-8")

    pool = VariantPool(K=POOL_K)
    pool.add_root(
        config_path=config_path,
        journal_path=pool_dir / "V0" / "journal.md",
        tasks=set(task_ids),
    )
    ledger = SuccessLedger()
    router = Router(cluster_mode="routed")
    deduce = DedupRegistry()

    # Register V0 genotype
    deduce.register(gh, "V0-R0-baseline")

    # 6. Skill graph (S6)
    print("Building skill graph (S6)...")
    sg = SkillGraph()
    # Emit skill nodes from known tool names in the harness
    tool_skills = {
        "WebSearch": "web_search",
        "WebFetch": "web_fetch",
        "Bash": "bash",
        "Read": "read",
        "Write": "write",
        "Edit": "edit",
        "Glob": "glob",
        "Grep": "grep",
    }
    for tool_name, skill_id in tool_skills.items():
        sg.add_skill(SkillNode(skill_id, tool_name, SkillCategory.TOOL))

    # Cross-skill edges based on typical agent workflows
    sg.add_edge(SkillEdge("web_search", "web_fetch", SkillEdgeType.SIMILAR_TO))
    sg.add_edge(SkillEdge("read", "grep", SkillEdgeType.SIMILAR_TO))
    sg.add_edge(SkillEdge("write", "edit", SkillEdgeType.SIMILAR_TO))
    sg.add_edge(SkillEdge("bash", "read", SkillEdgeType.COMPOSES_WITH))

    report.skill_count = len(sg.nodes)

    # Check invariants
    acyc = check_acyclicity(sg)
    contra = check_non_contradiction(sg)
    assert len(acyc) == 0, f"Skill graph cycles: {acyc}"
    assert len(contra) == 0, f"Skill graph contradictions: {contra}"
    print(f"  {report.skill_count} skills, invariants OK")

    # 7. Simulated footprints (S4) — generate synthetic traces per task
    print("Generating synthetic footprints (S4)...")
    footprint_base = Path(tempfile.mkdtemp(prefix="gaia_10x2_fp_"))
    fp_store = FootprintStore(footprint_base)

    for tid in task_ids:
        trace = TaskTrace(task_id=tid, variant_id="V0")
        # Simulate a representative trace: each task touches core hooks
        for step in range(3):
            trace.record(HookObservation(
                step_id=step, hook_name="task_start",
                processor_label="SystemPromptProcessor",
            ))
            trace.record(HookObservation(
                step_id=step, hook_name="before_model",
                processor_label="UserWrapperProcessor",
            ))
            trace.record(HookObservation(
                step_id=step, hook_name="after_model",
                processor_label="model",
                tools_called=["WebSearch", "Read"],
            ))
        fp = compute_footprint(trace, base_snapshot)
        fp.variant_id = "V0"
        fp_store.put(fp)

    report.footprints_stored = len(fp_store.get_all("V0"))
    print(f"  {report.footprints_stored} footprints stored")

    # 8. Simulated evolution rounds (2 rounds)
    print(f"\nRunning {ROUNDS} simulated evolution rounds...\n")

    retest_engine = SelectiveRetestEngine(mode="heuristic", footprint_store=fp_store)
    current_snapshot = base_snapshot
    current_genotype = gh

    for round_idx in range(1, ROUNDS + 1):
        print(f"  --- Round {round_idx} ---")

        # Generate a simple graph edit (simulated evolver output)
        edit = GraphEdit(
            edit_type=GraphEditType.INSERT_NODE,
            node_spec={
                "_target_": f"harnessx.processors.control.tool_failure_guard.ToolFailureGuard",
                "_hook_": "after_tool",
                "_singleton_group_": f"retry_r{round_idx}",
            },
            reason=f"Simulated edit for round {round_idx}",
        )

        # Apply edit → new candidate graph
        candidate_snapshot = apply_edits(current_snapshot, [edit])
        candidate_gh = genotype_hash(candidate_snapshot)

        # Dedup check
        if candidate_gh in deduce:
            print(f"    [DEDUP] Duplicate skipped: {candidate_gh[:12]}...")
            report.duplicates_caught += 1
            continue
        deduce.register(candidate_gh, f"C-R{round_idx}-01")
        report.genotypes_seen += 1

        # Danger edges + selective retest
        danger_nodes, danger_edges = danger_edge_set([edit], current_snapshot)
        rt_report = retest_engine.decide([edit], current_snapshot, task_ids, "V0")
        report.retest_reports.append({
            "round": round_idx,
            "must_retest": rt_report.must_retest,
            "can_inherit": rt_report.can_inherit,
            "uncertain": rt_report.uncertain,
        })
        print(f"    Danger: {len(danger_nodes)} nodes, {len(danger_edges)} edges")
        print(f"    Retest: {rt_report.must_retest} must, "
              f"{rt_report.can_inherit} inherit, "
              f"({rt_report.budget_saved_pct:.0%} saved)")

        # Reconciliation with synthetic observed edges
        observed_edge_keys: set[str] = set()
        for tid in task_ids:
            fp = fp_store.get("V0", tid)
            if fp:
                observed_edge_keys |= fp.observed_edge_keys
        rec_report = reconcile(candidate_snapshot, observed_edge_keys, set())
        print(f"    Recon: conv={rec_report.convergence_count} "
              f"diverg={rec_report.divergence_count} "
              f"absence={rec_report.absence_count}")

        # Edge-fault classification (S6) on skill graph with synthetic data
        obs_edges = [
            ("web_search", "web_fetch", "similar_to"),
            ("bash", "read", "composes_with"),
            # Simulate a MISSING fault: web_search→read observed but undeclared
            ("web_search", "read", "depends_on"),
        ]
        faults = classify_edge_faults(sg, obs_edges, current_round=round_idx)
        report.edge_faults_detected += len(faults)
        if faults:
            print(f"    Edge faults: {Counter(f.fault_type.value for f in faults)}")

        # Advance state for next round
        current_snapshot = candidate_snapshot
        current_genotype = candidate_gh

        report.per_round.append({
            "round": round_idx,
            "genotype": candidate_gh[:12],
            "danger_nodes": len(danger_nodes),
            "danger_edges": len(danger_edges),
            "retest_saved": rt_report.can_inherit,
            "faults": len(faults),
        })

    # 9. Pre-check (S2) on the collected data
    print("\nRunning pre-check (S2)...")
    from experiments.analysis.s2_precheck import compute_illegal_rate, compute_duplicate_rate
    candidate_configs = [
        {"processors": list(base_config.processors)},
    ] * 5  # simulate 5 candidates
    _, _, illegal_errs = compute_illegal_rate(candidate_configs)
    _, unique, dup_rate = compute_duplicate_rate(candidate_configs)

    metrics = PreCheckMetrics(
        total_candidates=5,
        illegal_count=len(illegal_errs),
        illegal_rate=len(illegal_errs) / 5,
        duplicate_rate=dup_rate,
        unique_genotypes=unique,
    )
    decisions = decide_mode(metrics)
    report.precheck = {
        "retest_mode": decisions["retest_mode"],
        "dedup_investment": decisions["dedup_investment"],
        "illegal_rate": metrics.illegal_rate,
        "duplicate_rate": metrics.duplicate_rate,
    }
    print(f"  Mode: {decisions['retest_mode']}, Dedup: {decisions['dedup_investment']}")

    # 10. Summary
    print(f"\n{'='*60}")
    print("GAIA 10×2 Graph Run — Summary")
    print(f"{'='*60}")
    print(f"  Tasks:              {len(report.task_ids)}")
    print(f"  Round 1 genotypes:  1 (baseline)")
    print(f"  Round 2 genotypes:  {report.genotypes_seen - 1} new, {report.duplicates_caught} dup skipped")
    print(f"  Known declarations: {report.known_declarations}/{report.total_targets}")
    print(f"  Footprints stored:  {report.footprints_stored}")
    print(f"  Skill graph:        {report.skill_count} skills, {report.edge_faults_detected} faults")
    if report.retest_reports:
        total_saved = sum(r["can_inherit"] for r in report.retest_reports)
        total_tasks = sum(
            r["must_retest"] + r["can_inherit"] + r["uncertain"]
            for r in report.retest_reports
        )
        print(f"  Retest savings:     {total_saved}/{total_tasks} ({total_saved/max(total_tasks,1):.0%})")

    # Save report
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUTPUT_DIR / "gaia_graph_10x2_report.json"
    out_path.write_text(
        json.dumps(report.__dict__, indent=2, default=str),
        encoding="utf-8",
    )
    print(f"\n  Report saved to: {out_path}")

    return report


if __name__ == "__main__":
    run()
