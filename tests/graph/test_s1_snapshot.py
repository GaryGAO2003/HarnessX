"""Tests for harnessx.graph.snapshot — S1: to_graph export."""

import pytest

from harnessx.core.builder import HarnessBuilder
from harnessx.bundles import context, coding
from harnessx.graph.snapshot import to_graph
from harnessx.graph.types import EdgeType, NodeType, SKELETON_HOOK_NAMES


@pytest.fixture
def simple_config():
    return (HarnessBuilder() | context).build()


@pytest.fixture
def full_config():
    return (HarnessBuilder() | context | coding).build()


class TestToGraph:
    def test_returns_graph_snapshot(self, simple_config):
        snapshot = to_graph(simple_config)
        assert snapshot.nodes
        assert snapshot.edges

    def test_has_all_skeleton_hooks(self, simple_config):
        snapshot = to_graph(simple_config)
        for name in SKELETON_HOOK_NAMES:
            hook_id = f"hook:{name}"
            assert hook_id in snapshot.nodes
            assert snapshot.nodes[hook_id].node_type == NodeType.SKELETON_HOOK

    def test_has_loop_back_edge(self, simple_config):
        snapshot = to_graph(simple_config)
        loop_edges = snapshot.edges_by_type(EdgeType.LOOP_BACK)
        assert len(loop_edges) == 1
        assert loop_edges[0].source_id == "hook:task_end"
        assert loop_edges[0].target_id == "hook:step_start"

    def test_has_processor_nodes(self, simple_config):
        snapshot = to_graph(simple_config)
        processor_nodes = [
            n for n in snapshot.nodes.values() if n.node_type == NodeType.PROCESSOR
        ]
        assert len(processor_nodes) > 0, "Should have at least one processor"

    def test_processors_have_attached_to_edges(self, simple_config):
        snapshot = to_graph(simple_config)
        attached = snapshot.edges_by_type(EdgeType.ATTACHED_TO)
        assert len(attached) > 0, "Should have ATTACHED_TO edges"

    def test_has_slot_nodes(self, simple_config):
        snapshot = to_graph(simple_config)
        slot_nodes = [
            n for n in snapshot.nodes.values() if n.node_type == NodeType.SLOT
        ]
        assert len(slot_nodes) >= 3  # memory, plan, cost at minimum

    def test_different_configs_produce_different_graphs(self):
        c1 = (HarnessBuilder() | context).build()
        c2 = (HarnessBuilder() | context | coding).build()
        s1 = to_graph(c1)
        s2 = to_graph(c2)
        # Different number of processors
        proc1 = [n for n in s1.nodes.values() if n.node_type == NodeType.PROCESSOR]
        proc2 = [n for n in s2.nodes.values() if n.node_type == NodeType.PROCESSOR]
        assert len(proc1) != len(proc2)

    def test_idempotent(self, simple_config):
        s1 = to_graph(simple_config)
        s2 = to_graph(simple_config)
        assert len(s1.nodes) == len(s2.nodes)
        assert len(s1.edges) == len(s2.edges)

    def test_wildcard_hook_creates_edges_to_all_hooks(self, simple_config):
        """Processors with hook='*' should have ATTACHED_TO edges to all 10 hooks."""
        snapshot = to_graph(simple_config)
        # Count edges per wildcard processor
        attached = snapshot.edges_by_type(EdgeType.ATTACHED_TO)
        # Each wildcard processor should have 10 edges (one per hook)
        edge_counts: dict[str, int] = {}
        for e in attached:
            edge_counts[e.source_id] = edge_counts.get(e.source_id, 0) + 1
        # At least some processors should have 10 edges
        assert any(c == 10 for c in edge_counts.values()), (
            f"Expected some processors with 10 ATTACHED_TO edges, got counts: {edge_counts}"
        )
