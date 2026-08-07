"""Tests for S5 impact module."""

import pytest

from harnessx.graph.edit import GraphEdit, GraphEditType
from harnessx.graph.impact import (
    danger_edge_set,
    forward_slice,
    influence_cone,
    intersects_footprint,
)
from harnessx.graph.types import Edge, EdgeType, GraphSnapshot, Node, NodeType


def make_chain_graph() -> GraphSnapshot:
    """A → B → C → slot:memory chain."""
    snapshot = GraphSnapshot()
    for label, nid in [("A", "proc:a"), ("B", "proc:b"), ("C", "proc:c"), ("mem", "slot:memory")]:
        nt = NodeType.PROCESSOR if nid.startswith("proc") else NodeType.SLOT
        snapshot.nodes[nid] = Node(node_id=nid, node_type=nt, label=label)
    snapshot.nodes["hook:task_start"] = Node(
        node_id="hook:task_start", node_type=NodeType.SKELETON_HOOK, label="task_start",
    )
    snapshot.edges = [
        Edge(source_id="proc:a", target_id="proc:b", edge_type=EdgeType.AFTER),
        Edge(source_id="proc:b", target_id="proc:c", edge_type=EdgeType.AFTER),
        Edge(source_id="proc:c", target_id="slot:memory", edge_type=EdgeType.WRITES_TO),
        Edge(source_id="proc:a", target_id="hook:task_start", edge_type=EdgeType.ATTACHED_TO),
    ]
    return snapshot


class TestForwardSlice:
    def test_single_start(self):
        g = make_chain_graph()
        reachable = forward_slice(g, {"proc:a"})
        assert "proc:a" in reachable
        assert "proc:b" in reachable
        assert "proc:c" in reachable
        assert "slot:memory" in reachable

    def test_mid_chain(self):
        g = make_chain_graph()
        reachable = forward_slice(g, {"proc:b"})
        assert "proc:a" not in reachable  # upstream
        assert "proc:b" in reachable
        assert "proc:c" in reachable
        assert "slot:memory" in reachable

    def test_leaf_node(self):
        g = make_chain_graph()
        reachable = forward_slice(g, {"slot:memory"})
        assert reachable == {"slot:memory"}

    def test_empty_start(self):
        g = make_chain_graph()
        reachable = forward_slice(g, set())
        assert reachable == set()

    def test_ignores_observed_edges(self):
        g = make_chain_graph()
        g.edges.append(Edge(
            source_id="proc:c", target_id="proc:a",
            edge_type=EdgeType.OBSERVED_CONTROL,
        ))
        # observed edge should not add proc:a to reachable set
        reachable = forward_slice(g, {"proc:c"})
        assert "proc:a" not in reachable


class TestDangerEdgeSet:
    def test_edit_produces_danger_set(self):
        g = make_chain_graph()
        edit = GraphEdit(
            edit_type=GraphEditType.MUTATE_INACTIVE,
            target_node_id="proc:b",
            node_changes={"_order_": 99},
        )
        danger_nodes, danger_edges = danger_edge_set([edit], g)
        # proc:b + forward reachable (proc:c, slot:memory)
        assert "proc:b" in danger_nodes
        assert "proc:c" in danger_nodes
        assert "slot:memory" in danger_nodes
        # proc:a is upstream, not in danger set (only if reachable from edit)
        # edges incident on danger nodes
        assert len(danger_edges) >= 2

    def test_multiple_edits(self):
        g = make_chain_graph()
        edits = [
            GraphEdit(edit_type=GraphEditType.MUTATE_INACTIVE, target_node_id="proc:a", node_changes={"x": 1}),
            GraphEdit(edit_type=GraphEditType.MUTATE_INACTIVE, target_node_id="proc:c", node_changes={"y": 2}),
        ]
        danger_nodes, danger_edges = danger_edge_set(edits, g)
        # Both change points + their forward reachable sets
        assert "proc:a" in danger_nodes
        assert "proc:c" in danger_nodes


class TestInfluenceCone:
    def test_scope_and_immune(self):
        g = make_chain_graph()
        cone = influence_cone(g, "proc:b")
        assert "proc:b" in cone["scope"]
        assert "proc:c" in cone["scope"]
        assert "slot:memory" in cone["scope"]
        assert "proc:a" in cone["immune"]

    def test_total_coverage(self):
        g = make_chain_graph()
        cone = influence_cone(g, "proc:a")
        assert cone["scope"] | cone["immune"] == set(g.nodes)


class TestIntersectsFootprint:
    def test_hit_on_node(self):
        assert intersects_footprint({"n1"}, set(), {"n1", "n2"}, set())

    def test_hit_on_edge(self):
        assert intersects_footprint(set(), {"e1"}, set(), {"e1", "e2"})

    def test_miss(self):
        assert not intersects_footprint({"n3"}, {"e3"}, {"n1", "n2"}, {"e1", "e2"})

    def test_empty_footprint(self):
        assert not intersects_footprint({"n1"}, {"e1"}, set(), set())

    def test_empty_danger(self):
        assert not intersects_footprint(set(), set(), {"n1"}, {"e1"})
