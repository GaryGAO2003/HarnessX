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
from harnessx.graph.identity import deployment_hash, genotype_hash
from harnessx.graph.snapshot import to_graph
from harnessx.graph.transform import graph_to_config_dict
from harnessx.graph.types import EdgeType, Node, NodeType

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


def _chain(snap) -> "list[tuple[str, str]]":
    return [(e.source_id, e.target_id) for e in snap.edges
            if e.edge_type == EdgeType.EXECUTES_BEFORE]


def test_rewire_proc_ref_with_group_translates_to_group_name():
    # two procs at the SAME order in one hook — the `after` breaks the tie.
    snap = to_graph(HarnessConfig(processors=[
        serialized_dict(PROBE_TARGET, hook="task_start", singleton_group="probe", order=10),
        serialized_dict(ORDERED_TARGET, hook="task_start", singleton_group="ordered", order=10),
    ]))
    # reference the target by proc:id — must translate to its singleton_group.
    result, report = apply_operator(snap, RewireOrdering(
        "proc:runtime_probe", after=("proc:ordered_probe",)), materialize=False)
    assert report.passed, report.reason()
    assert result.nodes["proc:runtime_probe"].metadata["_after_"] == ["ordered"]
    # (i) no unresolved_after warning — the reference resolves in-graph
    assert not [w for w in report.warnings if w.error_type == "unresolved_after"]
    # (i) the re-graphed EXECUTES_BEFORE chain reflects the new order (ordered→probe,
    # flipping the seq-tie default of probe→ordered)
    regraphed = to_graph(HarnessConfig(**graph_to_config_dict(result)))
    assert ("proc:ordered_probe", "proc:runtime_probe") in _chain(regraphed)


def test_rewire_proc_ref_without_group_uses_order_arithmetic():
    # target carries NO singleton_group → `after` cannot be a group name; the
    # operator lifts the edited node's _order_ past the target's effective order.
    snap = to_graph(HarnessConfig(processors=[
        serialized_dict(PROBE_TARGET, hook="task_start", singleton_group="probe", order=10),
        serialized_dict(ORDERED_TARGET, hook="task_start", order=10),   # no group
    ]))
    result, report = apply_operator(snap, RewireOrdering(
        "proc:runtime_probe", after=("proc:ordered_probe",)), materialize=False)
    assert report.passed, report.reason()
    meta = result.nodes["proc:runtime_probe"].metadata
    assert meta["_order_"] == 11                       # target order 10 + 1
    assert not meta.get("_after_")                     # no group → no _after_ written
    regraphed = to_graph(HarnessConfig(**graph_to_config_dict(result)))
    assert ("proc:ordered_probe", "proc:runtime_probe") in _chain(regraphed)


def test_rewire_proc_ref_missing_target_fails_closed():
    # target absent from the graph → operator rejects, no candidate produced.
    result, report = apply_operator(_snap(), RewireOrdering(
        "proc:runtime_probe", after=("proc:does_not_exist",)), materialize=False)
    assert result is None
    assert any(i.error_type == "operator_precondition" for i in report.issues)
    assert "resolves to no node" in report.reason()


def test_rewire_bare_group_missing_fails_closed():
    # a bare group-name reference that names no group in the graph → reject
    # (would otherwise land as a silently-dropped unresolved soft dep).
    result, report = apply_operator(_snap(), RewireOrdering(
        "proc:runtime_probe", after=("nonexistent_group",)), materialize=False)
    assert result is None
    assert any(i.error_type == "operator_precondition" for i in report.issues)
    assert "not a singleton_group" in report.reason()


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


def test_materialize_returns_build_fixed_point_not_edited_snapshot(tmp_path):
    """Candidate identity under ``materialize=True`` is the S4 build fixed point,
    not the ``apply_edits`` result.

    On a multi-processor parent, a RewireOrdering that flips the run order leaves
    a STALE derived EXECUTES_BEFORE chain on the edited snapshot (L5.5), so its
    genotype diverges from the build fixed point.  ``apply_operator`` must hand
    back the fixed point, whose genotype equals what a persisted-then-reloaded
    config re-graphs to — the exact invariant eval_bridge roundtrips on (this is
    the divergence that drove rehearsal roundtrip_rate below 1.0 before the fix).
    """
    snap = _snap()  # two importable processors in task_start, orders 10 & 20
    op = RewireOrdering("proc:runtime_probe", order=99)

    edited, r_edit = apply_operator(snap, op, materialize=False)
    fixed, r_fixed = apply_operator(snap, op, materialize=True)

    # materialize=False runs no build → no fixed point; the edited result stands
    assert r_edit.fixed_point is None
    assert edited is not None
    # materialize=True → returned snapshot IS the build fixed point
    assert r_fixed.fixed_point is not None
    assert fixed is r_fixed.fixed_point

    # the stale chain makes the edited snapshot a genuinely different genotype —
    # returning it (the old behavior) is what broke the roundtrip
    assert genotype_hash(edited) != genotype_hash(fixed)

    # fixed point re-graphs to itself through a persisted config (mirrors
    # eval_bridge.materialize_candidate: graph→config→yaml→reload→re-graph)
    cfg = HarnessConfig(processors=graph_to_config_dict(fixed).get("processors", []))
    cfg_path = tmp_path / "candidate" / "config.yaml"
    cfg.to_yaml_file(cfg_path)
    reloaded = to_graph(HarnessConfig.from_yaml_file(cfg_path))
    assert genotype_hash(reloaded) == genotype_hash(fixed)
    assert deployment_hash(reloaded) == deployment_hash(fixed)
