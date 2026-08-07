"""Tests for S5 graph edit module."""

import pytest

from harnessx.graph.edit import (
    GraphEdit,
    GraphEditError,
    GraphEditType,
    apply_edits,
    diff_graphs,
)
from harnessx.graph.types import Edge, EdgeType, GraphSnapshot, Node, NodeType


def make_minimal_graph() -> GraphSnapshot:
    """Create a minimal graph with 2 hooks and 1 processor."""
    snapshot = GraphSnapshot()
    snapshot.nodes["hook:task_start"] = Node(
        node_id="hook:task_start", node_type=NodeType.SKELETON_HOOK, label="task_start",
    )
    snapshot.nodes["hook:step_start"] = Node(
        node_id="hook:step_start", node_type=NodeType.SKELETON_HOOK, label="step_start",
    )
    snapshot.nodes["proc:a"] = Node(
        node_id="proc:a", node_type=NodeType.PROCESSOR, label="ProcessorA",
        metadata={"_target_": "mod.A", "_hook_": "task_start", "_singleton_group_": "sg_a"},
    )
    snapshot.edges = [
        Edge(source_id="proc:a", target_id="hook:task_start", edge_type=EdgeType.ATTACHED_TO),
        Edge(source_id="hook:task_end", target_id="hook:step_start", edge_type=EdgeType.LOOP_BACK),
    ]
    return snapshot


class TestGraphEditType:
    def test_all_types_exist(self):
        assert GraphEditType.INSERT_NODE == "insert_node"
        assert GraphEditType.REMOVE_NODE == "remove_node"
        assert GraphEditType.REPLACE_SAME_GROUP == "replace_same_group"
        assert GraphEditType.CHANGE_DEPENDENCY == "change_dependency"
        assert GraphEditType.SWAP_SUBGRAPH == "swap_subgraph"
        assert GraphEditType.MUTATE_INACTIVE == "mutate_inactive"


class TestGraphEdit:
    def test_create_insert(self):
        e = GraphEdit(
            edit_type=GraphEditType.INSERT_NODE,
            node_spec={"_target_": "mod.B", "_hook_": "step_start"},
            reason="Add a retry layer",
        )
        assert e.edit_type == GraphEditType.INSERT_NODE
        assert e.reason == "Add a retry layer"

    def test_affected_node_ids(self):
        e = GraphEdit(
            edit_type=GraphEditType.CHANGE_DEPENDENCY,
            edge_source_id="proc:a",
            edge_target_id="proc:b",
            edge_type=EdgeType.AFTER,
            add_edge=True,
        )
        ids = e.affected_node_ids()
        assert "proc:a" in ids
        assert "proc:b" in ids


class TestApplyEdits:
    def test_insert_node(self):
        g = make_minimal_graph()
        edit = GraphEdit(
            edit_type=GraphEditType.INSERT_NODE,
            node_spec={"_target_": "mod.B", "_hook_": "step_start"},
        )
        g2 = apply_edits(g, [edit])
        # Should have a new processor node
        proc_count = sum(1 for n in g2.nodes.values() if n.node_type == NodeType.PROCESSOR)
        assert proc_count == 2

    def test_insert_node_needs_spec(self):
        g = make_minimal_graph()
        edit = GraphEdit(edit_type=GraphEditType.INSERT_NODE)
        with pytest.raises(GraphEditError, match="node_spec"):
            apply_edits(g, [edit])

    def test_remove_node(self):
        g = make_minimal_graph()
        edit = GraphEdit(edit_type=GraphEditType.REMOVE_NODE, target_node_id="proc:a")
        g2 = apply_edits(g, [edit])
        assert "proc:a" not in g2.nodes

    def test_remove_node_removes_edges(self):
        g = make_minimal_graph()
        edit = GraphEdit(edit_type=GraphEditType.REMOVE_NODE, target_node_id="proc:a")
        g2 = apply_edits(g, [edit])
        # ATTACHED_TO edge from proc:a should be gone
        for e in g2.edges:
            assert e.source_id != "proc:a"

    def test_remove_nonexistent(self):
        g = make_minimal_graph()
        edit = GraphEdit(edit_type=GraphEditType.REMOVE_NODE, target_node_id="nonexistent")
        with pytest.raises(GraphEditError):
            apply_edits(g, [edit])

    def test_replace_same_group(self):
        g = make_minimal_graph()
        edit = GraphEdit(
            edit_type=GraphEditType.REPLACE_SAME_GROUP,
            target_node_id="proc:a",
            node_spec={"_target_": "mod.A_v2", "_hook_": "task_start", "_singleton_group_": "sg_a"},
        )
        g2 = apply_edits(g, [edit])
        replaced = g2.nodes["proc:a"]
        assert "mod.A_v2" in replaced.metadata["_target_"]

    def test_replace_sg_mismatch(self):
        g = make_minimal_graph()
        edit = GraphEdit(
            edit_type=GraphEditType.REPLACE_SAME_GROUP,
            target_node_id="proc:a",
            node_spec={"_target_": "mod.X", "_hook_": "task_start", "_singleton_group_": "sg_x"},
        )
        with pytest.raises(GraphEditError, match="singleton_group mismatch"):
            apply_edits(g, [edit])

    def test_mutate_inactive(self):
        g = make_minimal_graph()
        edit = GraphEdit(
            edit_type=GraphEditType.MUTATE_INACTIVE,
            target_node_id="proc:a",
            node_changes={"_order_": 99},
        )
        g2 = apply_edits(g, [edit])
        assert g2.nodes["proc:a"].metadata["_order_"] == 99


class TestDiffGraphs:
    def test_no_diff(self):
        g = make_minimal_graph()
        diffs = diff_graphs(g, g)
        assert len(diffs) == 0

    def test_added_node(self):
        before = make_minimal_graph()
        after = make_minimal_graph()
        after.nodes["proc:b"] = Node(
            node_id="proc:b", node_type=NodeType.PROCESSOR, label="B",
            metadata={"_target_": "mod.B", "_hook_": "step_start"},
        )
        after.edges.append(Edge(
            source_id="proc:b", target_id="hook:step_start",
            edge_type=EdgeType.ATTACHED_TO,
        ))
        diffs = diff_graphs(before, after)
        assert len(diffs) >= 1
        assert any(e.edit_type == GraphEditType.INSERT_NODE for e in diffs)
