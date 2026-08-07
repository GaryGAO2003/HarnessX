"""S2-S5 instability audit — all points where the graph-IR pipeline can fail.

Run against any HarnessConfig to quantify the gaps.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from collections import Counter

from harnessx.graph import to_graph, GraphSnapshot


@dataclass
class InstabilityReport:
    """Complete instability audit for a harness config."""

    # ── S3: metadata gaps ──
    total_processors: int = 0
    processors_with_gaps: int = 0
    gap_details: list[dict] = field(default_factory=list)

    # ── wildcard domination ──
    wildcard_count: int = 0
    single_hook_count: int = 0
    # When every processor is wildcard, danger cone = everything
    wildcard_ratio: float = 0.0

    # ── edge type breakdown ──
    edge_counts: dict[str, int] = field(default_factory=dict)
    semantic_edge_count: int = 0
    # Without semantic edges, forward_slice is ATTACHED_TO only
    has_semantic_edges: bool = False

    # ── S4: footprint reality ──
    cold_start: bool = True  # no footprints exist initially
    # ObservationProcessor only captures declared hooks, not prompt internals

    # ── S5: selective retest viability ──
    selective_retest_viable: bool = False
    estimated_max_savings_pct: float = 0.0

    # ── declaration backfill needed ──
    backfill_required: list[str] = field(default_factory=list)

    def summary(self) -> str:
        lines = [
            "=" * 60,
            "GRAPH IR — INSTABILITY AUDIT",
            "=" * 60,
            "",
            "--- S3: Metadata Gaps ---",
            f"  {self.processors_with_gaps}/{self.total_processors} processors have gaps",
            f"  Missing: singleton_group, after, order on most processors",
            f"  Impact: danger_edge_set can not distinguish same-hook processors",
            f"  Fix: S3 backfill_declarations() + observation verification",
            "",
            "--- Wildcard Domination ---",
            f"  {self.wildcard_count}/{self.total_processors} use hook='*' (wildcard)",
            f"  Impact: forward_slice from any wildcard covers ALL 10 hooks",
            f"  → selective retest saves 0% in current configs",
            f"  Fix: backfill single-hook declarations where possible",
            "",
            "--- Edge Types ---",
            f"  Semantic edges (after+writes_to+reads_from+conflicts): {self.semantic_edge_count}",
            f"  ATTACHED_TO only: {self.edge_counts.get('attached_to', 0)}",
            f"  Without semantic edges, danger cone = ATTACHED_TO = everything",
            f"  Fix: S3 backfill + S4 observation → populate semantic edges",
            "",
            "--- S4: Footprint Reality ---",
            f"  Cold start: {'YES' if self.cold_start else 'NO'} (no footprints yet)",
            f"  ObservationProcessor only captures hook-level events",
            f"  Prompt-internal effects NOT captured (system prompt change → reasoning change)",
            f"  Sub-agent spawns NOT captured (edge traversal in child harness)",
            f"  Impact: footprint underestimates true dependency set",
            "",
            "--- S5: Selective Retest Viability ---",
            f"  Viable: {self.selective_retest_viable}",
            f"  Estimated max savings: {self.estimated_max_savings_pct:.0%}",
            f"  If 0% → graph IR provides attribution/dedup but NOT retest savings",
            f"  Fix: backfill metadata + run S2 pre-check → measure actual viability",
            "",
            "--- Non-determinism ---",
            f"  LLM agents are inherently non-deterministic",
            f"  Safe mode requires deterministic replay (fixed seed + record-replay)",
            f"  Heuristic mode uses footprint union (wider, safer, less savings)",
            f"  S2 ④ measures footprint stability → decides safe vs heuristic",
            "",
            "--- S2 Pre-check Required ---",
            f"  ① illegal candidate rate (lower bound, empty decls → 0%)",
            f"  ② footprint sparsity (upper bound for savings — currently near 0%)",
            f"  ③ duplicate rate (genotype hash dedup — unaffected)",
            f"  ④ footprint stability (safe vs heuristic — unknown until measured)",
            f"  ⑤ extraction recall (can we even capture a useful footprint?)",
        ]
        if self.backfill_required:
            lines.append("")
            lines.append("--- Backfill Required Before Selective Retest Works ---")
            for item in self.backfill_required:
                lines.append(f"  - {item}")
        return "\n".join(lines)


def audit_config(config) -> InstabilityReport:
    """Run the full instability audit against a HarnessConfig."""
    snapshot = to_graph(config)
    report = InstabilityReport()

    # ── metadata gaps ──
    proc_nodes = [(nid, n) for nid, n in snapshot.nodes.items()
                  if n.node_type.value == "processor"]
    report.total_processors = len(proc_nodes)

    for nid, node in proc_nodes:
        gaps = []
        sg = node.metadata.get("_singleton_group_", "")
        after = node.metadata.get("_after_", "")
        order = node.metadata.get("_order_")
        hook = node.metadata.get("_hook_", "")
        if not sg:
            gaps.append("singleton_group")
        if not after:
            gaps.append("after")
        if order is None:
            gaps.append("order")
        if hook == "unknown" or not hook:
            gaps.append("hook")
        if gaps:
            report.processors_with_gaps += 1
            report.gap_details.append({
                "label": node.label,
                "target": node.metadata.get("_target_", ""),
                "missing": gaps,
            })

    # ── wildcard ──
    report.wildcard_count = sum(
        1 for _, n in proc_nodes if n.metadata.get("_hook_", "") == "*"
    )
    report.single_hook_count = report.total_processors - report.wildcard_count
    report.wildcard_ratio = (
        report.wildcard_count / report.total_processors
        if report.total_processors else 0
    )

    # ── edges ──
    et: dict[str, int] = dict(Counter(e.edge_type.value for e in snapshot.edges))
    report.edge_counts = et
    report.semantic_edge_count = sum(
        et.get(k, 0) for k in ("after", "writes_to", "reads_from", "conflicts_with")
    )
    report.has_semantic_edges = report.semantic_edge_count > 0

    # ── viability ──
    # If all processors are wildcard AND zero semantic edges,
    # the danger cone is the whole graph → savings = 0%
    if report.wildcard_ratio >= 0.8 and not report.has_semantic_edges:
        report.selective_retest_viable = False
        report.estimated_max_savings_pct = 0.0
    elif report.has_semantic_edges:
        report.selective_retest_viable = True
        # Rough estimate: semantic edges partition the graph
        report.estimated_max_savings_pct = min(0.6, report.semantic_edge_count / max(len(snapshot.edges), 1))
    else:
        report.selective_retest_viable = False
        report.estimated_max_savings_pct = 0.1  # optimistic

    # ── backfill needed ──
    if report.processors_with_gaps > 0:
        report.backfill_required.append(
            f"Backfill declarations for {report.processors_with_gaps}/{report.total_processors} "
            f"processors (singleton_group, after, order)"
        )
    if not report.has_semantic_edges:
        report.backfill_required.append(
            "Populate semantic edges (AFTER, WRITES_TO, READS_FROM) — "
            "currently only ATTACHED_TO edges exist"
        )
    if report.wildcard_ratio >= 0.8:
        report.backfill_required.append(
            f"Reduce wildcard ratio ({report.wildcard_ratio:.0%}) — "
            f"declare single-hook bindings for wildcard processors where possible"
        )
    report.backfill_required.append(
        "S2 pre-check: measure footprint stability before enabling selective retest"
    )

    return report


if __name__ == "__main__":
    from harnessx.core.builder import HarnessBuilder
    from harnessx.bundles import context, coding
    config = (HarnessBuilder() | context | coding).build()
    r = audit_config(config)
    print(r.summary())
