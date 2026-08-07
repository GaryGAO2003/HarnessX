"""Tests for harnessx.graph types module — S1: graph IR data structures."""

import pytest

from harnessx.graph.types import (
    SKELETON_HOOK_NAMES,
    Edge,
    EdgeType,
    GraphSnapshot,
    Node,
    NodeType,
)


class TestNodeType:
    def test_all_node_types_exist(self):
        assert NodeType.SKELETON_HOOK == "skeleton_hook"
        assert NodeType.PROCESSOR == "processor"
        assert NodeType.SLOT == "slot"
        assert NodeType.BUNDLE == "bundle"
        assert NodeType.SKILL == "skill"
        assert NodeType.TOOL == "tool"

    def test_node_type_is_string_enum(self):
        assert isinstance(NodeType.SKELETON_HOOK.value, str)


class TestEdgeType:
    def test_declared_edge_types_exist(self):
        assert EdgeType.ATTACHED_TO == "attached_to"
        assert EdgeType.AFTER == "after"
        assert EdgeType.WRITES_TO == "writes_to"
        assert EdgeType.READS_FROM == "reads_from"
        assert EdgeType.COMPOSES_WITH == "composes_with"
        assert EdgeType.CONFLICTS_WITH == "conflicts_with"
        assert EdgeType.SPECIALIZES == "specializes"
        assert EdgeType.LOOP_BACK == "loop_back"

    def test_observed_edge_types_exist(self):
        assert EdgeType.OBSERVED_CONTROL == "observed_control"
        assert EdgeType.OBSERVED_DATA == "observed_data"


class TestNode:
    def test_create_processor_node(self):
        node = Node(
            node_id="proc:test",
            node_type=NodeType.PROCESSOR,
            label="TestProcessor",
            metadata={"_target_": "mod.TestProcessor", "_hook_": "before_model"},
        )
        assert node.node_id == "proc:test"
        assert node.node_type == NodeType.PROCESSOR
        assert node.label == "TestProcessor"
        assert node.metadata["_target_"] == "mod.TestProcessor"

    def test_node_is_frozen(self):
        node = Node(node_id="n1", node_type=NodeType.SLOT, label="memory")
        with pytest.raises(Exception):
            node.node_id = "n2"  # type: ignore[misc]

    def test_node_equality(self):
        a = Node(node_id="x", node_type=NodeType.PROCESSOR, label="X")
        b = Node(node_id="x", node_type=NodeType.PROCESSOR, label="X")
        c = Node(node_id="y", node_type=NodeType.PROCESSOR, label="Y")
        assert a == b
        assert a != c


class TestEdge:
    def test_create_edge(self):
        edge = Edge(
            source_id="proc:a",
            target_id="hook:before_model",
            edge_type=EdgeType.ATTACHED_TO,
        )
        assert edge.source_id == "proc:a"
        assert edge.target_id == "hook:before_model"
        assert edge.edge_type == EdgeType.ATTACHED_TO

    def test_edge_is_frozen(self):
        edge = Edge(source_id="a", target_id="b", edge_type=EdgeType.AFTER)
        with pytest.raises(Exception):
            edge.source_id = "c"  # type: ignore[misc]


class TestGraphSnapshot:
    def test_empty_snapshot(self):
        g = GraphSnapshot()
        assert len(g.nodes) == 0
        assert len(g.edges) == 0
        assert g.genotype_hash == ""
        assert g.phenotype_hash == ""

    def test_node_ids(self):
        g = GraphSnapshot()
        n = Node(node_id="x", node_type=NodeType.SLOT, label="test")
        g.nodes[n.node_id] = n
        assert g.node_ids() == {"x"}

    def test_edges_by_type(self):
        g = GraphSnapshot()
        e1 = Edge(source_id="a", target_id="b", edge_type=EdgeType.AFTER)
        e2 = Edge(source_id="c", target_id="d", edge_type=EdgeType.LOOP_BACK)
        g.edges = [e1, e2]
        assert len(g.edges_by_type(EdgeType.AFTER)) == 1
        assert len(g.edges_by_type(EdgeType.LOOP_BACK)) == 1
        assert len(g.edges_by_type(EdgeType.CONFLICTS_WITH)) == 0

    def test_successors_predecessors(self):
        g = GraphSnapshot()
        n1 = Node(node_id="a", node_type=NodeType.SLOT, label="a")
        n2 = Node(node_id="b", node_type=NodeType.SLOT, label="b")
        g.nodes = {"a": n1, "b": n2}
        g.edges = [Edge(source_id="a", target_id="b", edge_type=EdgeType.AFTER)]
        assert g.successors("a") == {"b"}
        assert g.predecessors("b") == {"a"}
        assert g.successors("b") == set()


class TestSkeletonHooks:
    def test_ten_hooks(self):
        assert len(SKELETON_HOOK_NAMES) == 10

    def test_contains_key_hooks(self):
        assert "task_start" in SKELETON_HOOK_NAMES
        assert "task_end" in SKELETON_HOOK_NAMES
        assert "before_model" in SKELETON_HOOK_NAMES
        assert "after_tool" in SKELETON_HOOK_NAMES
        assert "step_end" in SKELETON_HOOK_NAMES
