"""Tests for S4 reconciliation module."""

import pytest

from harnessx.graph.reconciliation import (
    ConvergenceReport,
    ReconciliationCategory,
    ReconciledEdge,
    reconcile,
)
from harnessx.graph.types import Edge, EdgeType, GraphSnapshot, Node, NodeType
from harnessx.graph.snapshot import to_graph
from harnessx.core.builder import HarnessBuilder
from harnessx.bundles import context


def make_simple_snapshot() -> GraphSnapshot:
    """Create a minimal graph with known edges for testing reconciliation."""
    snapshot = GraphSnapshot()
    snapshot.nodes["hook:task_start"] = Node(
        node_id="hook:task_start", node_type=NodeType.SKELETON_HOOK, label="task_start",
    )
    snapshot.nodes["hook:step_start"] = Node(
        node_id="hook:step_start", node_type=NodeType.SKELETON_HOOK, label="step_start",
    )
    snapshot.nodes["proc:a"] = Node(
        node_id="proc:a", node_type=NodeType.PROCESSOR, label="ProcessorA",
        metadata={"_target_": "mod.A", "_hook_": "task_start"},
    )
    snapshot.nodes["proc:b"] = Node(
        node_id="proc:b", node_type=NodeType.PROCESSOR, label="ProcessorB",
        metadata={"_target_": "mod.B", "_hook_": "step_start"},
    )
    snapshot.edges = [
        Edge(source_id="proc:a", target_id="hook:task_start", edge_type=EdgeType.ATTACHED_TO),
        Edge(source_id="proc:b", target_id="hook:step_start", edge_type=EdgeType.ATTACHED_TO),
        Edge(source_id="hook:task_end", target_id="hook:step_start", edge_type=EdgeType.LOOP_BACK),
    ]
    return snapshot


class TestReconciliationCategory:
    def test_enum_values(self):
        assert ReconciliationCategory.CONVERGENCE == "convergence"
        assert ReconciliationCategory.DIVERGENCE == "divergence"
        assert ReconciliationCategory.ABSENCE == "absence"


class TestReconciledEdge:
    def test_convergence_edge(self):
        e = ReconciledEdge(
            source_id="a", target_id="b",
            edge_type=EdgeType.ATTACHED_TO,
            category=ReconciliationCategory.CONVERGENCE,
            declared=True, observed_count=1,
        )
        assert e.category == ReconciliationCategory.CONVERGENCE
        assert e.declared
        assert e.observed_count == 1


class TestReconcile:
    def test_all_converged(self):
        """All declared edges observed."""
        snapshot = make_simple_snapshot()
        observed_edges = {
            "proc:a→hook:task_start",
            "proc:b→hook:step_start",
            "hook:task_end→hook:step_start",
        }
        report = reconcile(snapshot, observed_edges, {"proc:a", "proc:b"})
        assert report.convergence_count == 3
        assert report.divergence_count == 0
        assert report.absence_count == 0

    def test_absence(self):
        """Declared edge never observed."""
        snapshot = make_simple_snapshot()
        observed_edges = {
            "proc:a→hook:task_start",
        }
        report = reconcile(snapshot, observed_edges, {"proc:a"})
        assert report.convergence_count == 1
        assert report.absence_count == 2  # loop_back + proc:b→step_start
        assert report.divergence_count == 0

    def test_divergence(self):
        """Observed edge with no declared counterpart — hidden coupling."""
        snapshot = make_simple_snapshot()
        observed_edges = {
            "proc:a→hook:task_start",
            "proc:b→hook:step_start",
            "hook:task_end→hook:step_start",
            "proc:c→slot:memory",  # undeclared!
        }
        report = reconcile(snapshot, observed_edges, {"proc:a", "proc:b", "proc:c"})
        assert report.divergence_count == 1
        assert report.convergence_count == 3

    def test_erosion_rate(self):
        """Absence / declared = erosion rate."""
        snapshot = make_simple_snapshot()
        observed_edges = set()
        report = reconcile(snapshot, observed_edges, set())
        assert report.absence_count == 3
        assert report.total_declared == 3
        assert report.erosion_rate() == 1.0

    def test_divergence_rate(self):
        snapshot = make_simple_snapshot()
        observed_edges = {
            "proc:c→hook:step_start",  # entirely undeclared
        }
        report = reconcile(snapshot, observed_edges, {"proc:c"})
        assert report.divergence_count == 1

    def test_with_real_config(self):
        """Reconciliation should work with a real HarnessConfig graph."""
        config = (HarnessBuilder() | context).build()
        snapshot = to_graph(config)
        # Simulate some observed edges
        observed = {"proc:system_prompt_processor→hook:task_start"}
        report = reconcile(snapshot, observed, set())
        # Should have at least some declared edges
        assert report.total_declared > 0
        assert report.convergence_count + report.absence_count == report.total_declared


class TestConvergenceReport:
    def test_summary(self):
        report = ConvergenceReport(edges=[
            ReconciledEdge("a", "b", EdgeType.ATTACHED_TO, ReconciliationCategory.CONVERGENCE,
                          declared=True, observed_count=1),
            ReconciledEdge("c", "d", EdgeType.OBSERVED_DATA, ReconciliationCategory.DIVERGENCE,
                          declared=False, observed_count=1),
        ])
        s = report.summary()
        assert "convergence=1" in s
        assert "divergence=1" in s
        assert "absence=0" in s
