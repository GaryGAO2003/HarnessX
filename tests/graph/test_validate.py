"""P3 block 17 — fail-closed validator (S0–S4) + transaction protocol.

Baseline: every to_graph() export of a real config passes S0–S3.  Each layer
then gets a targeted violation that must REJECT; the transaction protocol
must return the original snapshot untouched on any failure.
"""

import pytest

from harnessx.core.builder import HarnessBuilder
from harnessx.core.harness import HarnessConfig
from harnessx.core.runtime import RuntimeReg
from harnessx.graph.edit import GraphEdit, GraphEditType
from harnessx.graph.identity import genotype_hash
from harnessx.graph.snapshot import to_graph
from harnessx.graph.types import Edge, EdgeType, GraphSnapshot, Node, NodeType
from harnessx.graph.validate import (
    transactional_apply,
    validate_edit_preconditions,
    validate_snapshot,
)

from tests.graph.fixtures import RuntimeProbe, config_srsr, serialized_dict

PROBE_TARGET = "tests.graph.fixtures.RuntimeProbe"


def _layers(report):
    return {(i.layer, i.error_type) for i in report.issues}


# ── baseline: real exports pass ─────────────────────────────────────────────


def test_context_bundle_snapshot_validates():
    from harnessx.bundles import context

    snap = to_graph((HarnessBuilder() | context).build())
    report = validate_snapshot(snap)
    assert report.passed, report.reason()


def test_mixed_srsr_snapshot_validates():
    report = validate_snapshot(to_graph(config_srsr()))
    assert report.passed, report.reason()


def test_empty_config_snapshot_validates():
    report = validate_snapshot(to_graph(HarnessConfig(processors=[])))
    assert report.passed, report.reason()


# ── S0: schema / id format / field types ────────────────────────────────────


def test_s0_id_key_mismatch_rejected():
    snap = to_graph(HarnessConfig(processors=[]))
    snap.nodes["proc:wrong"] = Node(
        node_id="proc:other", node_type=NodeType.PROCESSOR, label="x",
        metadata={"_target_": "m.X"})
    assert ("S0", "id_key_mismatch") in _layers(validate_snapshot(snap))


def test_s0_bad_prefix_rejected():
    snap = to_graph(HarnessConfig(processors=[]))
    snap.nodes["widget:x"] = Node(
        node_id="widget:x", node_type=NodeType.PROCESSOR, label="x",
        metadata={"_target_": "m.X"})
    assert ("S0", "bad_id_prefix") in _layers(validate_snapshot(snap))


def test_s0_bad_metadata_types_rejected():
    snap = to_graph(HarnessConfig(processors=[]))
    snap.nodes["proc:x"] = Node(
        node_id="proc:x", node_type=NodeType.PROCESSOR, label="x",
        metadata={"_target_": "m.X", "_hooks_": "notalist", "_order_": "nan"})
    layers = _layers(validate_snapshot(snap))
    assert ("S0", "bad_field_type") in layers


def test_s0_missing_target_rejected():
    snap = to_graph(HarnessConfig(processors=[]))
    snap.nodes["proc:x"] = Node(
        node_id="proc:x", node_type=NodeType.PROCESSOR, label="x", metadata={})
    assert ("S0", "missing_target") in _layers(validate_snapshot(snap))


# ── S1: endpoints + kind legality ───────────────────────────────────────────


def test_s1_dangling_endpoint_rejected():
    snap = to_graph(HarnessConfig(processors=[]))
    snap.edges.append(Edge(source_id="proc:ghost", target_id="hook:task_start",
                           edge_type=EdgeType.ATTACHED_TO, metadata={}))
    assert ("S1", "dangling_endpoint") in _layers(validate_snapshot(snap))


def test_s1_illegal_edge_kind_rejected():
    snap = to_graph(HarnessConfig(processors=[]))
    # hook → hook ATTACHED_TO is kind-illegal (processor → hook only)
    snap.edges.append(Edge(source_id="hook:task_start", target_id="hook:task_end",
                           edge_type=EdgeType.ATTACHED_TO, metadata={}))
    assert ("S1", "illegal_edge_kind") in _layers(validate_snapshot(snap))


def test_s1_writes_to_hook_rejected():
    snap = to_graph(HarnessConfig(processors=[
        {"_target_": "x.P", "_hooks_": ["task_start"]}]))
    snap.edges.append(Edge(source_id="proc:p", target_id="hook:task_start",
                           edge_type=EdgeType.WRITES_TO, metadata={}))
    assert ("S1", "illegal_edge_kind") in _layers(validate_snapshot(snap))


# ── S2: hooks / singleton / order-after ─────────────────────────────────────


def test_s2_invalid_hook_name_rejected():
    snap = to_graph(HarnessConfig(processors=[
        {"_target_": "x.P", "_hooks_": ["task_start"]}]))
    snap.nodes["proc:p"].metadata["_hooks_"] = ["not_a_hook"]
    assert ("S2", "invalid_hook") in _layers(validate_snapshot(snap))


def test_s2_attach_to_model_hook_rejected():
    snap = to_graph(HarnessConfig(processors=[
        {"_target_": "x.P", "_hooks_": ["task_start"]}]))
    snap.edges.append(Edge(source_id="proc:p", target_id="hook:model",
                           edge_type=EdgeType.ATTACHED_TO, metadata={}))
    assert ("S2", "non_processor_hook") in _layers(validate_snapshot(snap))


def test_s2_singleton_conflict_rejected():
    snap = to_graph(HarnessConfig(processors=[
        serialized_dict("x.A", hook="task_start", singleton_group="dup"),
        serialized_dict("x.B", hook="task_start", singleton_group="dup"),
    ]))
    assert ("S2", "singleton_conflict") in _layers(validate_snapshot(snap))


def test_s2_unresolved_after_is_warning_not_reject():
    snap = to_graph(HarnessConfig(processors=[
        serialized_dict("x.A", hook="task_start", singleton_group="a",
                        after=("missing_group",)),
    ]))
    report = validate_snapshot(snap)
    assert report.passed  # soft dep — builder semantics
    assert any(w.error_type == "unresolved_after" for w in report.warnings)


def test_s2_after_cycle_rejected():
    # a after b, b after a — same order, same bucket → cycle.  to_graph would
    # itself raise while chaining, so build the metadata cycle post-export.
    snap = to_graph(HarnessConfig(processors=[
        serialized_dict("x.A", hook="task_start", singleton_group="a", order=0),
        serialized_dict("x.B", hook="task_start", singleton_group="b", order=0,
                        after=("a",)),
    ]))
    snap.nodes["proc:a"].metadata["_after_"] = ["b"]  # close the cycle
    assert ("S2", "order_conflict") in _layers(validate_snapshot(snap))


def test_s2_cross_order_after_rejected():
    snap = to_graph(HarnessConfig(processors=[
        serialized_dict("x.A", hook="task_start", singleton_group="a", order=0),
        serialized_dict("x.B", hook="task_start", singleton_group="b", order=50),
    ]))
    # a (order 0) declares after b (order 50) → never satisfiable
    snap.nodes["proc:a"].metadata["_after_"] = ["b"]
    assert ("S2", "order_conflict") in _layers(validate_snapshot(snap))


# ── S3: interface signatures ────────────────────────────────────────────────


def test_s3_undeclared_slot_edge_rejected():
    snap = to_graph(HarnessConfig(processors=[
        {"_target_": "x.P", "_hooks_": ["task_start"]}]))
    snap.edges.append(Edge(source_id="proc:p", target_id="slot:memory",
                           edge_type=EdgeType.WRITES_TO, metadata={}))
    assert ("S3", "undeclared_slot_edge") in _layers(validate_snapshot(snap))


def test_s3_declared_slot_edge_passes():
    snap = to_graph(HarnessConfig(processors=[
        {"_target_": "x.P", "_hooks_": ["task_start"],
         "_writes_slots_": ["memory"]}]))
    report = validate_snapshot(snap)
    assert report.passed, report.reason()


def test_s3_malformed_event_field_rejected():
    snap = to_graph(HarnessConfig(processors=[
        {"_target_": "x.P", "_hooks_": ["task_start"],
         "_reads_event_fields_": ["nodothere"]}]))
    assert ("S3", "bad_event_field") in _layers(validate_snapshot(snap))


# ── S0 edit preconditions ───────────────────────────────────────────────────


def test_preconditions_reject_malformed_edits():
    snap = to_graph(HarnessConfig(processors=[]))
    report = validate_edit_preconditions(snap, [
        GraphEdit(edit_type=GraphEditType.INSERT_NODE),                # no spec
        GraphEdit(edit_type=GraphEditType.REMOVE_NODE),                # no target
        GraphEdit(edit_type=GraphEditType.CHANGE_DEPENDENCY,
                  edge_source_id="a", edge_target_id="b"),             # no edge_type
        GraphEdit(edit_type=GraphEditType.MUTATE_INACTIVE,
                  target_node_id="proc:x"),                            # no changes
    ])
    kinds = {i.error_type for i in report.issues}
    assert not report.passed
    assert {"missing_node_spec", "missing_target_node",
            "bad_edge_type", "missing_node_changes"} <= kinds


# ── S4 transaction protocol ─────────────────────────────────────────────────


def _probe_snapshot():
    return to_graph(HarnessConfig(processors=[
        serialized_dict(PROBE_TARGET, hook="task_start"),
    ]))


def test_transaction_commit_on_legal_edit():
    snap = _probe_snapshot()
    g_before = genotype_hash(snap)
    result, report = transactional_apply(snap, [GraphEdit(
        edit_type=GraphEditType.MUTATE_INACTIVE,
        target_node_id="proc:runtime_probe",
        node_changes={"_order_": 7},
    )])
    assert report.passed, report.reason()
    assert result is not None
    assert result.nodes["proc:runtime_probe"].metadata["_order_"] == 7
    assert genotype_hash(snap) == g_before  # original untouched


def test_transaction_discard_on_precondition_failure():
    snap = _probe_snapshot()
    result, report = transactional_apply(snap, [GraphEdit(
        edit_type=GraphEditType.INSERT_NODE)])
    assert result is None
    assert not report.passed
    assert report.issues[0].layer == "S0"


def test_transaction_discard_on_apply_failure():
    snap = _probe_snapshot()
    nodes_before = set(snap.nodes)
    result, report = transactional_apply(snap, [GraphEdit(
        edit_type=GraphEditType.REMOVE_NODE, target_node_id="proc:nonexistent")])
    assert result is None
    assert ("S4", "apply_failed") in _layers(report)
    assert set(snap.nodes) == nodes_before  # original untouched


def test_transaction_discard_on_post_apply_validation():
    snap = _probe_snapshot()
    # a dependency edge to a node that exists but with an illegal kind pairing
    result, report = transactional_apply(snap, [GraphEdit(
        edit_type=GraphEditType.CHANGE_DEPENDENCY,
        edge_source_id="hook:task_start", edge_target_id="hook:task_end",
        edge_type=EdgeType.ATTACHED_TO, add_edge=True)])
    assert result is None
    assert ("S1", "illegal_edge_kind") in _layers(report)


def test_transaction_build_failure_fail_closed():
    # unimportable target: static layers pass, S4 build must REJECT
    snap = to_graph(HarnessConfig(processors=[
        serialized_dict("nonexistent.module.Klass", hook="task_start"),
    ]))
    result, report = transactional_apply(snap, [GraphEdit(
        edit_type=GraphEditType.MUTATE_INACTIVE,
        target_node_id="proc:klass", node_changes={"_order_": 1},
    )])
    assert result is None
    assert ("S4", "build_failed") in _layers(report)


def test_transaction_materialize_false_skips_build():
    snap = to_graph(HarnessConfig(processors=[
        serialized_dict("nonexistent.module.Klass", hook="task_start"),
    ]))
    result, report = transactional_apply(snap, [GraphEdit(
        edit_type=GraphEditType.MUTATE_INACTIVE,
        target_node_id="proc:klass", node_changes={"_order_": 1},
    )], materialize=False)
    assert report.passed, report.reason()
    assert result is not None


def test_transaction_materialize_real_target_roundtrips():
    snap = _probe_snapshot()
    result, report = transactional_apply(snap, [GraphEdit(
        edit_type=GraphEditType.MUTATE_INACTIVE,
        target_node_id="proc:runtime_probe",
        node_changes={"_order_": 3},
    )], materialize=True)
    assert report.passed, report.reason()
    assert result is not None
