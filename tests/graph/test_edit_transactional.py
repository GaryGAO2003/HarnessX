"""L5.5 — apply_edits transactionality (VM11).

Three-hash clearing, runtime-edge endpoint validation (fail-closed),
REMOVE_NODE filtering of runtime_edges, INSERT_NODE slug migration,
CHANGE_DEPENDENCY edge_type fidelity.
"""

import pytest

from harnessx.core.harness import HarnessConfig
from harnessx.core.runtime import RuntimeReg
from harnessx.graph.edit import GraphEdit, GraphEditError, GraphEditType, apply_edits
from harnessx.graph.identity import deployment_hash, genotype_hash, phenotype_hash
from harnessx.graph.snapshot import to_graph
from harnessx.graph.types import Edge, EdgeType, GraphSnapshot, Node, NodeType

from .test_runtime_overlay import _RtProc


def _mixed_snapshot():
    """Serialized + runtime proc in the same bucket → mixed chain in runtime_edges."""
    cfg = HarnessConfig(processors=[
        {"_target_": "x.ProcA", "_hook_": "*", "_order_": 0},
        RuntimeReg(proc=_RtProc(), hook="*", order=0),
    ])
    return to_graph(cfg)


# ── VM11: hash cache lifecycle ──────────────────────────────────────────────


def test_apply_edits_clears_all_three_hashes():
    snap = _mixed_snapshot()
    genotype_hash(snap)
    deployment_hash(snap)
    phenotype_hash(snap)
    assert snap.genotype_hash and snap.deployment_hash and snap.phenotype_hash

    edit = GraphEdit(edit_type=GraphEditType.MUTATE_INACTIVE,
                     target_node_id="proc:proc_a", node_changes={"_order_": 9})
    result = apply_edits(snap, [edit])
    assert result.genotype_hash == ""
    assert result.deployment_hash == ""
    assert result.phenotype_hash == ""


def test_apply_edits_leaves_original_untouched():
    snap = _mixed_snapshot()
    g, d, p = genotype_hash(snap), deployment_hash(snap), phenotype_hash(snap)
    edit = GraphEdit(edit_type=GraphEditType.REMOVE_NODE, target_node_id="proc:proc_a")
    apply_edits(snap, [edit])
    # original snapshot: hashes and structure both intact
    assert snap.genotype_hash == g
    assert snap.deployment_hash == d
    assert snap.phenotype_hash == p
    assert "proc:proc_a" in snap.nodes


def test_chained_apply_edits_new_object_each_time():
    snap = _mixed_snapshot()
    e1 = GraphEdit(edit_type=GraphEditType.MUTATE_INACTIVE,
                   target_node_id="proc:proc_a", node_changes={"_order_": 1})
    r1 = apply_edits(snap, [e1])
    e2 = GraphEdit(edit_type=GraphEditType.MUTATE_INACTIVE,
                   target_node_id="proc:proc_a", node_changes={"_order_": 2})
    r2 = apply_edits(r1, [e2])
    assert r2 is not r1
    assert r2.deployment_hash == ""
    assert r1.nodes["proc:proc_a"].metadata["_order_"] == 1  # r1 unchanged
    assert r2.nodes["proc:proc_a"].metadata["_order_"] == 2


# ── L5.5 endpoint validation (fail-closed) ──────────────────────────────────


def test_dangling_runtime_edge_source_rejected():
    snap = GraphSnapshot()
    snap.nodes["hook:task_start"] = Node(
        node_id="hook:task_start", node_type=NodeType.SKELETON_HOOK, label="task_start")
    snap.runtime_edges.append(Edge(
        source_id="rt:ghost", target_id="hook:task_start",
        edge_type=EdgeType.ATTACHED_TO, metadata={}))
    with pytest.raises(GraphEditError, match="runtime edge source missing"):
        apply_edits(snap, [])


def test_dangling_runtime_edge_target_rejected():
    snap = GraphSnapshot()
    snap.runtime_nodes["rt:x"] = Node(
        node_id="rt:x", node_type=NodeType.PROCESSOR, label="x")
    snap.runtime_edges.append(Edge(
        source_id="rt:x", target_id="hook:ghost",
        edge_type=EdgeType.ATTACHED_TO, metadata={}))
    with pytest.raises(GraphEditError, match="runtime edge target missing"):
        apply_edits(snap, [])


def test_valid_runtime_edges_pass_validation():
    snap = _mixed_snapshot()  # to_graph output: all endpoints exist
    result = apply_edits(snap, [])
    assert result.deployment_hash == ""


# ── REMOVE_NODE also filters runtime_edges ──────────────────────────────────


def test_remove_node_drops_runtime_edges_referencing_it():
    snap = _mixed_snapshot()
    # precondition: the mixed chain references the persistent node
    assert any(e.source_id == "proc:proc_a" or e.target_id == "proc:proc_a"
               for e in snap.runtime_edges)
    edit = GraphEdit(edit_type=GraphEditType.REMOVE_NODE, target_node_id="proc:proc_a")
    result = apply_edits(snap, [edit])  # endpoint validation must pass
    assert all(e.source_id != "proc:proc_a" and e.target_id != "proc:proc_a"
               for e in result.runtime_edges)
    # runtime node itself survives — only incident edges are dropped
    assert any(n.startswith("rt:") for n in result.runtime_nodes)


# ── INSERT_NODE uses _compute_slug ──────────────────────────────────────────


def test_insert_node_uses_compute_slug():
    snap = GraphSnapshot()
    edit = GraphEdit(edit_type=GraphEditType.INSERT_NODE,
                     node_spec={"_target_": "mod.SlidingWindowMemory"})
    result = apply_edits(snap, [edit])
    assert "proc:sliding_window_memory" in result.nodes


# ── CHANGE_DEPENDENCY add respects edge_type ────────────────────────────────


def test_change_dependency_add_uses_declared_edge_type():
    snap = GraphSnapshot()
    snap.nodes["proc:a"] = Node(node_id="proc:a", node_type=NodeType.PROCESSOR, label="a")
    snap.nodes["proc:b"] = Node(node_id="proc:b", node_type=NodeType.PROCESSOR, label="b")
    edit = GraphEdit(edit_type=GraphEditType.CHANGE_DEPENDENCY,
                     edge_source_id="proc:a", edge_target_id="proc:b",
                     edge_type=EdgeType.AFTER, add_edge=True)
    result = apply_edits(snap, [edit])
    added = [e for e in result.edges
             if e.source_id == "proc:a" and e.target_id == "proc:b"]
    assert len(added) == 1
    assert added[0].edge_type == EdgeType.AFTER  # not hardcoded ATTACHED_TO
