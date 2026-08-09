"""Block 12 — L7 transform (graph_to_config_dict).

VM12 round-trip sub-items: falsey preservation (12a/12b/12f), unresolved
_after_ (12c), nested/ordered ctor kwargs (12d/12e), bidirectional deepcopy
isolation (12g); plus _runtime_only skip (L7.4) and _after_ metadata priority
over AFTER edges (L7.3).
"""

from harnessx.core.harness import HarnessConfig
from harnessx.graph.snapshot import to_graph
from harnessx.graph.transform import graph_to_config_dict, verify_roundtrip
from harnessx.graph.types import Edge, EdgeType, GraphSnapshot, Node, NodeType


def _roundtrip_one(proc_dict):
    snap = to_graph(HarnessConfig(processors=[proc_dict]))
    out = graph_to_config_dict(snap)
    assert len(out["processors"]) == 1
    return out["processors"][0]


# ── VM12a/b/f: falsey survives ──────────────────────────────────────────────


def test_order_zero_survives():
    out = _roundtrip_one({"_target_": "x.ProcA", "_hooks_": ["task_start"],
                          "_order_": 0})
    assert out["_order_"] == 0


def test_empty_singleton_group_survives():
    out = _roundtrip_one({"_target_": "x.ProcA", "_hooks_": ["task_start"],
                          "_singleton_group_": ""})
    assert out["_singleton_group_"] == ""


def test_falsey_ctor_params_survive():
    out = _roundtrip_one({"_target_": "x.ProcA", "_hooks_": ["task_start"],
                          "enabled": False, "count": 0, "name": ""})
    assert out["enabled"] is False
    assert out["count"] == 0
    assert out["name"] == ""


# ── VM12c: unresolved _after_ survives verbatim ─────────────────────────────


def test_unresolved_after_survives():
    out = _roundtrip_one({"_target_": "x.ProcA", "_hooks_": ["task_start"],
                          "_after_": ["unknown_sg"]})
    assert out["_after_"] == ["unknown_sg"]


def test_explicit_empty_after_survives():
    out = _roundtrip_one({"_target_": "x.ProcA", "_hooks_": ["task_start"],
                          "_after_": []})
    assert out["_after_"] == []


# ── VM12d/e: ctor kwargs structure + order ──────────────────────────────────


def test_nested_ctor_dict_roundtrips():
    out = _roundtrip_one({"_target_": "x.ProcA", "_hooks_": ["task_start"],
                          "nested": {"a": 1, "b": {"c": [2, 3]}}})
    assert out["nested"] == {"a": 1, "b": {"c": [2, 3]}}


def test_ordered_ctor_list_keeps_order():
    out = _roundtrip_one({"_target_": "x.ProcA", "_hooks_": ["task_start"],
                          "tags": ["c", "a", "b"]})
    assert out["tags"] == ["c", "a", "b"]


# ── VM12g: bidirectional deepcopy isolation ─────────────────────────────────


def test_mutating_output_does_not_touch_graph():
    snap = to_graph(HarnessConfig(processors=[
        {"_target_": "x.ProcA", "_hooks_": ["task_start"],
         "cfg": {"depth": [1, 2]}},
    ]))
    out = graph_to_config_dict(snap)
    out["processors"][0]["cfg"]["depth"].append(99)
    node = snap.nodes["proc:proc_a"]
    assert node.metadata["_ctor_kwargs_"]["cfg"]["depth"] == [1, 2]


def test_mutating_graph_does_not_touch_output():
    snap = to_graph(HarnessConfig(processors=[
        {"_target_": "x.ProcA", "_hooks_": ["task_start"],
         "cfg": {"depth": [1, 2]}},
    ]))
    out = graph_to_config_dict(snap)
    snap.nodes["proc:proc_a"].metadata["_ctor_kwargs_"]["cfg"]["depth"].append(99)
    assert out["processors"][0]["cfg"]["depth"] == [1, 2]


# ── L7.1: full key set carried through ──────────────────────────────────────


def test_all_declared_metadata_keys_carried():
    out = _roundtrip_one({
        "_target_": "x.ProcA",
        "_hook_": "*",
        "_hooks_": ["task_start", "task_end"],
        "_order_": 7,
        "_singleton_group_": "sg",
        "_writes_slots_": ["s.w"],
        "_reads_slots_": [],
        "_reads_event_fields_": ["Ev.f"],
        "_writes_event_fields_": [],
    })
    assert out["_hook_"] == "*"
    assert out["_hooks_"] == ["task_start", "task_end"]
    assert out["_order_"] == 7
    assert out["_singleton_group_"] == "sg"
    assert out["_writes_slots_"] == ["s.w"]
    assert out["_reads_slots_"] == []          # explicit empty kept
    assert out["_reads_event_fields_"] == ["Ev.f"]
    assert out["_writes_event_fields_"] == []


# ── L7.3: metadata _after_ beats AFTER-edge derivation ──────────────────────


def test_after_metadata_priority_over_edges():
    snap = GraphSnapshot()
    snap.nodes["proc:a"] = Node(
        node_id="proc:a", node_type=NodeType.PROCESSOR, label="A",
        metadata={"_target_": "m.A", "_after_": ["declared_sg"]})
    snap.nodes["proc:b"] = Node(
        node_id="proc:b", node_type=NodeType.PROCESSOR, label="B",
        metadata={"_target_": "m.B", "_singleton_group_": "edge_sg"})
    snap.edges.append(Edge(source_id="proc:a", target_id="proc:b",
                           edge_type=EdgeType.AFTER, metadata={}))
    out = graph_to_config_dict(snap)
    a = next(p for p in out["processors"] if p["_target_"] == "m.A")
    assert a["_after_"] == ["declared_sg"]  # metadata wins over edge derivation


def test_after_edge_fallback_without_metadata():
    snap = GraphSnapshot()
    snap.nodes["proc:a"] = Node(
        node_id="proc:a", node_type=NodeType.PROCESSOR, label="A",
        metadata={"_target_": "m.A"})
    snap.nodes["proc:b"] = Node(
        node_id="proc:b", node_type=NodeType.PROCESSOR, label="B",
        metadata={"_target_": "m.B", "_singleton_group_": "edge_sg"})
    snap.edges.append(Edge(source_id="proc:a", target_id="proc:b",
                           edge_type=EdgeType.AFTER, metadata={}))
    out = graph_to_config_dict(snap)
    a = next(p for p in out["processors"] if p["_target_"] == "m.A")
    assert a["_after_"] == ["edge_sg"]  # no metadata key → edge fallback


# ── L7.4: runtime-only nodes skipped ────────────────────────────────────────


def test_runtime_only_node_skipped():
    snap = GraphSnapshot()
    snap.nodes["proc:a"] = Node(
        node_id="proc:a", node_type=NodeType.PROCESSOR, label="A",
        metadata={"_target_": "m.A"})
    snap.nodes["proc:rtish"] = Node(
        node_id="proc:rtish", node_type=NodeType.PROCESSOR, label="R",
        metadata={"_target_": "m.R", "_runtime_only": True})
    out = graph_to_config_dict(snap)
    targets = [p["_target_"] for p in out["processors"]]
    assert targets == ["m.A"]


# ── round-trip on a real builder config ─────────────────────────────────────


def test_verify_roundtrip_context_bundle():
    from harnessx.core.builder import HarnessBuilder
    from harnessx.bundles import context

    config = (HarnessBuilder() | context).build()
    assert verify_roundtrip(to_graph(config))
