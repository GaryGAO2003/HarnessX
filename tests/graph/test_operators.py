"""P3 block 18 — deterministic operator kernel.

Each operator: happy path through the full transaction + applicability
failures with concrete reasons.  Determinism: same operator + same snapshot
→ identical edits.
"""

import pytest

from harnessx.core.harness import HarnessConfig
from harnessx.graph.operators import (
    InsertProcessor,
    MutateProcessorParams,
    OperatorError,
    RemoveProcessor,
    ReplaceSameSingletonGroup,
    RewireOrdering,
    SwapBundle,
    apply_operator,
)
from harnessx.graph.snapshot import to_graph
from harnessx.graph.types import Node, NodeType

from tests.graph.fixtures import serialized_dict

PROBE_TARGET = "tests.graph.fixtures.RuntimeProbe"
ORDERED_TARGET = "tests.graph.fixtures.OrderedProbe"


def _snap():
    return to_graph(HarnessConfig(processors=[
        serialized_dict(PROBE_TARGET, hook="task_start", singleton_group="probe",
                        order=10),
        serialized_dict(ORDERED_TARGET, hook="task_start", singleton_group="ordered",
                        order=20),
    ]))


# ── MutateProcessorParams ───────────────────────────────────────────────────


def test_mutate_params_commits():
    snap = _snap()
    result, report = apply_operator(
        snap, MutateProcessorParams("proc:runtime_probe", {"depth": 3}),
        materialize=False)
    assert report.passed, report.reason()
    assert result.nodes["proc:runtime_probe"].metadata["_ctor_kwargs_"] == {"depth": 3}
    assert "_ctor_kwargs_" not in snap.nodes["proc:runtime_probe"].metadata  # parent untouched


def test_mutate_params_rejects_metadata_keys():
    result, report = apply_operator(
        _snap(), MutateProcessorParams("proc:runtime_probe", {"_order_": 5}),
        materialize=False)
    assert result is None
    assert "metadata keys" in report.reason()


def test_mutate_params_rejects_missing_node():
    result, report = apply_operator(
        _snap(), MutateProcessorParams("proc:ghost", {"a": 1}), materialize=False)
    assert result is None
    assert report.issues[0].error_type == "operator_precondition"


# ── InsertProcessor ─────────────────────────────────────────────────────────


def test_insert_processor_commits():
    snap = _snap()
    result, report = apply_operator(snap, InsertProcessor({
        "_target_": "x.NewProc", "_hooks_": ["step_end"],
        "_singleton_group_": "fresh",
    }), materialize=False)
    assert report.passed, report.reason()
    assert "proc:new_proc" in result.nodes
    assert "proc:new_proc" not in snap.nodes


def test_insert_processor_rejects_taken_group():
    result, report = apply_operator(_snap(), InsertProcessor({
        "_target_": "x.NewProc", "_singleton_group_": "probe",
    }), materialize=False)
    assert result is None
    assert "already claimed" in report.reason()


def test_insert_processor_rejects_missing_target():
    result, report = apply_operator(_snap(), InsertProcessor({}), materialize=False)
    assert result is None
    assert "_target_" in report.reason()


# ── RemoveProcessor ─────────────────────────────────────────────────────────


def test_remove_processor_commits():
    snap = _snap()
    result, report = apply_operator(
        snap, RemoveProcessor("proc:runtime_probe"), materialize=False)
    assert report.passed, report.reason()
    assert "proc:runtime_probe" not in result.nodes
    assert all("proc:runtime_probe" not in (e.source_id, e.target_id)
               for e in result.edges + result.runtime_edges)
    assert "proc:runtime_probe" in snap.nodes  # parent untouched


def test_remove_processor_rejects_slot_node():
    result, report = apply_operator(
        _snap(), RemoveProcessor("slot:memory"), materialize=False)
    assert result is None
    assert "not a processor" in report.reason()


# ── ReplaceSameSingletonGroup ───────────────────────────────────────────────


def test_replace_same_group_commits():
    snap = _snap()
    result, report = apply_operator(snap, ReplaceSameSingletonGroup(
        "proc:runtime_probe",
        {"_target_": "x.ProbeV2", "_hooks_": ["task_start"],
         "_singleton_group_": "probe"},
    ), materialize=False)
    assert report.passed, report.reason()
    assert result.nodes["proc:runtime_probe"].metadata["_target_"] == "x.ProbeV2"


def test_replace_same_group_rejects_group_change():
    result, report = apply_operator(_snap(), ReplaceSameSingletonGroup(
        "proc:runtime_probe",
        {"_target_": "x.ProbeV2", "_singleton_group_": "other"},
    ), materialize=False)
    assert result is None
    assert "group mismatch" in report.reason()


# ── RewireOrdering ──────────────────────────────────────────────────────────


def test_rewire_ordering_commits():
    snap = _snap()
    result, report = apply_operator(snap, RewireOrdering(
        "proc:runtime_probe", order=99, after=("ordered",)), materialize=False)
    assert report.passed, report.reason()
    meta = result.nodes["proc:runtime_probe"].metadata
    assert meta["_order_"] == 99
    assert meta["_after_"] == ["ordered"]


def test_rewire_ordering_rejects_noop():
    result, report = apply_operator(
        _snap(), RewireOrdering("proc:runtime_probe"), materialize=False)
    assert result is None
    assert "nothing to change" in report.reason()


def test_rewire_creating_cross_order_conflict_fails_closed():
    # probe(order 10) declares after ordered(order 20) → S2 order_conflict
    result, report = apply_operator(_snap(), RewireOrdering(
        "proc:runtime_probe", after=("ordered",)), materialize=False)
    assert result is None
    assert any(i.error_type == "order_conflict" for i in report.issues)


# ── SwapBundle ──────────────────────────────────────────────────────────────


def _snap_with_bundle(signature="sig-a"):
    snap = _snap()
    meta = {"child_graph_id": "bundle-old"}
    if signature:
        meta["interface_signature"] = signature
    snap.nodes["bundle:ctx"] = Node(
        node_id="bundle:ctx", node_type=NodeType.BUNDLE, label="ctx", metadata=meta)
    return snap


def test_swap_bundle_same_signature_commits():
    snap = _snap_with_bundle("sig-a")
    result, report = apply_operator(snap, SwapBundle(
        "bundle:ctx", "bundle-new", replacement_signature="sig-a"),
        materialize=False)
    assert report.passed, report.reason()
    assert result.nodes["bundle:ctx"].metadata["child_graph_id"] == "bundle-new"
    assert snap.nodes["bundle:ctx"].metadata["child_graph_id"] == "bundle-old"


def test_swap_bundle_signature_mismatch_rejected():
    result, report = apply_operator(_snap_with_bundle("sig-a"), SwapBundle(
        "bundle:ctx", "bundle-new", replacement_signature="sig-b"),
        materialize=False)
    assert result is None
    assert "signature mismatch" in report.reason()


def test_swap_bundle_missing_signature_rejected():
    result, report = apply_operator(_snap_with_bundle(signature=""), SwapBundle(
        "bundle:ctx", "bundle-new", replacement_signature="sig-a"),
        materialize=False)
    assert result is None
    assert "interface_signature" in report.reason()


# ── determinism + full materialize path ─────────────────────────────────────


def test_operator_edits_deterministic():
    snap = _snap()
    op = RewireOrdering("proc:runtime_probe", order=42)
    assert op.edits(snap) == op.edits(snap)


def test_operator_full_materialize_with_real_target():
    snap = to_graph(HarnessConfig(processors=[
        serialized_dict(PROBE_TARGET, hook="task_start"),
    ]))
    result, report = apply_operator(snap, RewireOrdering(
        "proc:runtime_probe", order=3), materialize=True)
    assert report.passed, report.reason()
    assert result is not None
