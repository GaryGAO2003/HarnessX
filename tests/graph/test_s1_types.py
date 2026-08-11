"""Tests for harnessx.graph types module — S1: graph IR data structures."""

import pytest

from harnessx.graph.types import (
    SKELETON_HOOK_NAMES,
    Edge,
    EdgeType,
    GraphSnapshot,
    Node,
    NodeType,
    parse_unfolded_id,
    unfolded_id,
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
        assert EdgeType.INVOKES == "invokes"

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


class TestUnfoldedId:
    # Every node-id shape snapshot.py actually constructs: hook:, proc:,
    # proc:…__2, slot:, dotted slot key, rt:, rt:…__rt1, rt:slot:. All contain
    # ':' and one contains a literal '@t' — the parse must survive each.
    _ID_SHAPES = [
        "hook:before_tool",
        "proc:sample",
        "proc:sample__2",
        "slot:memory",
        "slot:model.route",
        "rt:cost_guard",
        "rt:cost_guard__rt1",
        "rt:slot:memory",
        "weird@tname",  # a static id that itself contains '@t'
    ]

    def test_round_trip_all_id_shapes(self):
        for node_id in self._ID_SHAPES:
            for round_index in (0, 1, 7, 42):
                uid = unfolded_id(node_id, round_index)
                assert parse_unfolded_id(uid) == (node_id, round_index)

    def test_unfolded_id_format(self):
        assert unfolded_id("hook:before_tool", 3) == "hook:before_tool@t3"

    def test_negative_round_rejected(self):
        with pytest.raises(ValueError):
            unfolded_id("proc:sample", -1)

    def test_parse_rejects_ids_without_round_tag(self):
        for bad in [
            "hook:before_tool",
            "proc:sample@t",
            "proc:sample@tbar",
            "proc:sample@t-1",
            "proc:sample@t1x",
            "",
        ]:
            with pytest.raises(ValueError):
                parse_unfolded_id(bad)

    def test_parse_splits_on_last_tag(self):
        # A static id containing '@t' must still round-trip: split on the LAST.
        assert parse_unfolded_id("weird@tname@t5") == ("weird@tname", 5)
