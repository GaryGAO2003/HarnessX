"""S4 — Three-way reconciliation of declared vs observed edges.

Extends software reflexion models (Murphy, Notkin & Sullivan, FSE 1995)
with a third "absence" category:

    convergence  — declared edge confirmed by observation
    divergence   — observation edge with no declared counterpart
                   (reveals hidden coupling)
    absence      — declared edge never observed
                   (dead declaration, potentially removable)

These three categories feed into S3 (backfill verification), S5
(danger-edge precision), and S6 (edge-fault classification).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from .types import EdgeType, GraphSnapshot


class ReconciliationCategory(str, Enum):
    CONVERGENCE = "convergence"  # declared + observed
    DIVERGENCE = "divergence"  # observed only — hidden coupling
    ABSENCE = "absence"  # declared only — dead declaration


@dataclass
class ReconciledEdge:
    """One edge after three-way reconciliation."""

    source_id: str
    target_id: str
    edge_type: EdgeType
    category: ReconciliationCategory
    declared: bool = False
    observed_count: int = 0
    observation_steps: list[int] = field(default_factory=list)


@dataclass
class ConvergenceReport:
    """Result of reconciling declared and observed edges."""

    edges: list[ReconciledEdge] = field(default_factory=list)

    @property
    def convergence_count(self) -> int:
        return sum(1 for e in self.edges if e.category == ReconciliationCategory.CONVERGENCE)

    @property
    def divergence_count(self) -> int:
        return sum(1 for e in self.edges if e.category == ReconciliationCategory.DIVERGENCE)

    @property
    def absence_count(self) -> int:
        return sum(1 for e in self.edges if e.category == ReconciliationCategory.ABSENCE)

    @property
    def total_declared(self) -> int:
        return sum(1 for e in self.edges if e.declared)

    @property
    def total_observed(self) -> int:
        return sum(1 for e in self.edges if e.observed_count > 0)

    def divergence_rate(self) -> float:
        """Rate of observed-but-undeclared edges — hidden coupling density."""
        total = len(self.edges)
        return self.divergence_count / total if total > 0 else 0.0

    def erosion_rate(self) -> float:
        """Rate of declared-but-never-observed edges — architecture erosion."""
        declared = self.total_declared
        return self.absence_count / declared if declared > 0 else 0.0

    def summary(self) -> str:
        return (
            f"ConvergenceReport("
            f"convergence={self.convergence_count}, "
            f"divergence={self.divergence_count}, "
            f"absence={self.absence_count}"
            f")"
        )


def reconcile(
    snapshot: GraphSnapshot,
    observed_edge_keys: set[str],
    observed_node_ids: set[str],
) -> ConvergenceReport:
    """Reconcile declared edges (from the graph) with observed edges (from traces).

    Args:
        snapshot: The declared graph with typed edges.
        observed_edge_keys: Edge keys observed at runtime (format: "src→tgt").
        observed_node_ids: Node ids observed at runtime.

    Returns:
        ConvergenceReport categorizing every relevant edge.
    """
    report = ConvergenceReport()
    seen_declared_keys: set[str] = set()

    for edge in snapshot.edges:
        # Skip observation edges — they are the input, not the baseline
        if edge.edge_type.value.startswith("observed_"):
            continue

        edge_key = f"{edge.source_id}→{edge.target_id}"
        seen_declared_keys.add(edge_key)

        reconciled = ReconciledEdge(
            source_id=edge.source_id,
            target_id=edge.target_id,
            edge_type=edge.edge_type,
            category=ReconciliationCategory.ABSENCE,  # default — assume unobserved
            declared=True,
            observed_count=0,
        )

        if edge_key in observed_edge_keys:
            reconciled.category = ReconciliationCategory.CONVERGENCE
            reconciled.observed_count = 1

        report.edges.append(reconciled)

    # Divergence: observed edges with no declared counterpart
    for edge_key in sorted(observed_edge_keys):
        if edge_key in seen_declared_keys:
            continue
        parts = edge_key.split("→", 1)
        src = parts[0] if len(parts) > 0 else ""
        tgt = parts[1] if len(parts) > 1 else ""
        report.edges.append(ReconciledEdge(
            source_id=src,
            target_id=tgt,
            edge_type=EdgeType.OBSERVED_DATA,  # best guess for untyped observation
            category=ReconciliationCategory.DIVERGENCE,
            declared=False,
            observed_count=1,
        ))

    return report
