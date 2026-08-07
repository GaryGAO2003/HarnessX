"""S5 — Selective retest engine.

Replaces full-task-bed measurement with danger-edge ∩ footprint
intersection checks.  Tasks whose footprint does not touch any
danger edge inherit the parent variant's score — saving budget.

Modes
-----
**safe** (deterministic replay)
    Only skip if the footprint was computed under the same genotype
    hash AND the danger-edge intersection is provably empty.

**heuristic** (footprint union)
    Use the union of all historical footprints for a task as the
    baseline.  Wider → fewer skips → safer but less budget saved.

Decision table (from S2 pre-check)
-----------------------------------
============ ===== ==================================================
④ stability  ≥0.7  safe mode
④ stability  <0.5  heuristic mode (default when no data)
============ ===== ==================================================
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from harnessx.graph.footprint import CoverageFootprint, FootprintStore
from harnessx.graph.impact import danger_edge_set, intersects_footprint
from harnessx.graph.edit import GraphEdit
from harnessx.graph.types import GraphSnapshot


class RetestDecision(str, Enum):
    MUST_RETEST = "must_retest"
    CAN_INHERIT = "can_inherit"
    UNCERTAIN = "uncertain"  # stale footprint, force retest


@dataclass
class RetestReport:
    """Per-candidate retest decision summary."""

    total_tasks: int = 0
    must_retest: int = 0
    can_inherit: int = 0
    uncertain: int = 0
    budget_saved_pct: float = 0.0
    per_task: dict[str, RetestDecision] = field(default_factory=dict)

    def record(self, task_id: str, decision: RetestDecision) -> None:
        self.total_tasks += 1
        if decision == RetestDecision.MUST_RETEST:
            self.must_retest += 1
        elif decision == RetestDecision.CAN_INHERIT:
            self.can_inherit += 1
        else:
            self.uncertain += 1
        self.per_task[task_id] = decision

    def finalize(self) -> None:
        if self.total_tasks > 0:
            self.budget_saved_pct = self.can_inherit / self.total_tasks


class SelectiveRetestEngine:
    """Decides which tasks need re-evaluation for a graph edit candidate.

    Usage::

        engine = SelectiveRetestEngine(mode="heuristic", footprint_store=store)
        report = engine.decide(edits, graph, task_ids, variant_id)
        # → report.can_inherit tasks can skip measurement
    """

    def __init__(
        self,
        mode: str = "heuristic",
        footprint_store: FootprintStore | None = None,
        max_footprint_age: int = 3,
    ):
        self.mode = mode  # "safe" | "heuristic"
        self.footprint_store = footprint_store
        self.max_footprint_age = max_footprint_age

    def should_retest(
        self,
        danger_nodes: set[str],
        danger_edges: set[str],
        footprint: CoverageFootprint | None,
        current_genotype: str = "",
    ) -> RetestDecision:
        """Decide whether one task needs re-evaluation.

        Args:
            danger_nodes: Nodes affected by the edit set.
            danger_edges: Edge keys affected by the edit set.
            footprint: The task's last coverage footprint (may be None).
            current_genotype: The current config's genotype hash.

        Returns:
            RetestDecision — MUST_RETEST, CAN_INHERIT, or UNCERTAIN.
        """
        # No footprint → must retest (cold start)
        if footprint is None:
            return RetestDecision.MUST_RETEST

        # Footprint was computed under a different config → uncertain
        if self.mode == "safe" and footprint.genotype_hash != current_genotype:
            return RetestDecision.UNCERTAIN

        # Check intersection
        if intersects_footprint(
            danger_nodes, danger_edges,
            footprint.touched_node_ids, footprint.observed_edge_keys,
        ):
            return RetestDecision.MUST_RETEST

        return RetestDecision.CAN_INHERIT

    def decide(
        self,
        edits: list[GraphEdit],
        graph: GraphSnapshot,
        task_ids: list[str],
        variant_id: str,
    ) -> RetestReport:
        """Decide retest for all tasks in a variant's T_k.

        Args:
            edits: The proposed graph edits.
            graph: The parent graph snapshot.
            task_ids: Tasks routed to this variant (T_k).
            variant_id: The variant being evolved.

        Returns:
            RetestReport with per-task decisions.
        """
        danger_nodes, danger_edges = danger_edge_set(edits, graph)
        report = RetestReport()

        for task_id in task_ids:
            fp = None
            if self.footprint_store is not None:
                fp = self.footprint_store.get(variant_id, task_id)
            decision = self.should_retest(
                danger_nodes, danger_edges, fp, graph.genotype_hash,
            )
            report.record(task_id, decision)

        report.finalize()
        return report
