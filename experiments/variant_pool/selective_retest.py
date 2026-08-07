"""S5 — Selective retest engine with full-vs-selective toggle.

Replaces full-task-bed measurement with danger-edge ∩ footprint
intersection checks.  Tasks whose footprint does not touch any
danger edge inherit the parent variant's score — saving budget.

Three retest modes (toggle via ``mode`` parameter)
--------------------------------------------------
**full**      Original HarnessX behaviour — every task re-evaluated.
              Zero risk of missing a regression.  Zero budget saved.
              Default when no graph IR or no footprints available.

**safe**      Deterministic-replay selective retest.  Only skips when
              the footprint was computed under the same genotype hash
              AND the danger-edge intersection is provably empty.

**heuristic** Footprint-union selective retest.  Uses union of
              historical footprints as baseline.  Wider → fewer skips
              → safer but less budget saved.

Decision table (from S2 pre-check)
-----------------------------------
============ ===== ==================================================
S2 ④         ≥0.7  safe mode
S2 ④         <0.5  heuristic mode  (default when no data)
============ ===== ==================================================

The engine is designed as a drop-in: when mode="full" or
footprint_store is None, behaviour is byte-identical to the
original HarnessX full-retest path.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from harnessx.graph.footprint import CoverageFootprint, FootprintStore
from harnessx.graph.impact import danger_edge_set, intersects_footprint
from harnessx.graph.edit import GraphEdit
from harnessx.graph.types import GraphSnapshot


class RetestMode(str, Enum):
    """Global retest policy toggle."""
    FULL = "full"            # always retest (original HarnessX)
    SAFE = "safe"            # genotype-matched footprint skip
    HEURISTIC = "heuristic"  # footprint-union skip


class RetestDecision(str, Enum):
    MUST_RETEST = "must_retest"
    CAN_INHERIT = "can_inherit"
    UNCERTAIN = "uncertain"  # stale footprint or cold start — force retest


@dataclass
class RetestReport:
    """Per-candidate retest decision summary."""

    mode: str = ""
    total_tasks: int = 0
    must_retest: int = 0
    can_inherit: int = 0
    uncertain: int = 0
    budget_saved_pct: float = 0.0
    per_task: dict[str, RetestDecision] = field(default_factory=dict)

    def record(self, task_id: str, decision: RetestDecision) -> None:
        self.total_tasks += 1
        task_decisions = {
            RetestDecision.MUST_RETEST: "must_retest",
            RetestDecision.CAN_INHERIT: "can_inherit",
            RetestDecision.UNCERTAIN: "uncertain",
        }
        key = task_decisions[decision]
        setattr(self, key, getattr(self, key) + 1)
        self.per_task[task_id] = decision

    def finalize(self) -> None:
        if self.total_tasks > 0:
            self.budget_saved_pct = self.can_inherit / self.total_tasks


class SelectiveRetestEngine:
    """Decides which tasks need re-evaluation for a graph edit candidate.

    Drop-in for VariantPoolEngine.evaluate(): when mode="full" or no
    footprint_store, every task gets MUST_RETEST — identical to the
    original full-measurement path.

    Usage::

        # Full retest (original HarnessX behaviour)
        engine = SelectiveRetestEngine(mode=RetestMode.FULL)

        # Selective retest (graph-aware)
        engine = SelectiveRetestEngine(mode=RetestMode.HEURISTIC,
                                       footprint_store=store)

        report = engine.decide(edits, graph, task_ids, variant_id)
        must_retest = [t for t, d in report.per_task.items()
                        if d != RetestDecision.CAN_INHERIT]
    """

    def __init__(
        self,
        mode: RetestMode | str = RetestMode.FULL,
        footprint_store: FootprintStore | None = None,
        max_footprint_age: int = 3,
    ):
        if isinstance(mode, str):
            mode = RetestMode(mode)
        self.mode: RetestMode = mode
        self.footprint_store = footprint_store
        self.max_footprint_age = max_footprint_age

    @property
    def is_active(self) -> bool:
        """True when selective retest is actually in use."""
        return (self.mode != RetestMode.FULL
                and self.footprint_store is not None)

    def should_retest(
        self,
        danger_nodes: set[str],
        danger_edges: set[str],
        footprint: CoverageFootprint | None,
        current_genotype: str = "",
    ) -> RetestDecision:
        """Decide whether one task needs re-evaluation.

        When ``footprint`` is None, always returns MUST_RETEST (cold start).
        Otherwise applies the active mode's logic.
        """
        # Full mode: always retest (original HarnessX)
        if self.mode == RetestMode.FULL:
            return RetestDecision.MUST_RETEST

        # No footprint → must retest (cold start)
        if footprint is None:
            return RetestDecision.MUST_RETEST

        # Safe mode: genotype must match
        if self.mode == RetestMode.SAFE and footprint.genotype_hash != current_genotype:
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
        """Decide retest for all tasks in a variant's T_k."""
        report = RetestReport(mode=self.mode.value)

        if not self.is_active or self.mode == RetestMode.FULL:
            # Full mode or no store: every task must retest — fast path
            for task_id in task_ids:
                report.record(task_id, RetestDecision.MUST_RETEST)
            report.finalize()
            return report

        # Selective modes with store available
        danger_nodes, danger_edges = danger_edge_set(edits, graph)

        for task_id in task_ids:
            fp = self.footprint_store.get(variant_id, task_id)  # type: ignore[union-attr]
            decision = self.should_retest(
                danger_nodes, danger_edges, fp, graph.genotype_hash,
            )
            report.record(task_id, decision)

        report.finalize()
        return report
