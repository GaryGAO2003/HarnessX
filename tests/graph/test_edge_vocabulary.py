"""Δ7 block 25 — edge vocabulary: families, data channels, BOM.

Edge-family totality, state-channel tagging on slot edges, S3 rejection of
unknown channels, deterministic BOM.
"""

from harnessx.core.harness import HarnessConfig
from harnessx.core.runtime import RuntimeReg
from harnessx.graph.bom import graph_bom
from harnessx.graph.snapshot import to_graph
from harnessx.graph.types import (
    DataChannel,
    Edge,
    EdgeFamily,
    EdgeType,
    edge_family,
)
from harnessx.graph.validate import validate_snapshot

from tests.graph.fixtures import SlotProbe, serialized_dict

MR = "harnessx.processors.multi_model.model_router.ModelRouterProcessor"


# ── family classification ───────────────────────────────────────────────────


def test_every_edge_type_has_a_family():
    for et in EdgeType:
        assert isinstance(edge_family(et), EdgeFamily)


def test_family_assignments():
    assert edge_family(EdgeType.ATTACHED_TO) is EdgeFamily.CONTROL_FLOW
    assert edge_family(EdgeType.EXECUTES_BEFORE) is EdgeFamily.CONTROL_FLOW
    assert edge_family(EdgeType.LOOP_BACK) is EdgeFamily.CONTROL_FLOW
    assert edge_family(EdgeType.AFTER) is EdgeFamily.CONTROL_DEP
    assert edge_family(EdgeType.CONFLICTS_WITH) is EdgeFamily.CONTROL_DEP
    assert edge_family(EdgeType.WRITES_TO) is EdgeFamily.DATA_FLOW
    assert edge_family(EdgeType.READS_FROM) is EdgeFamily.DATA_FLOW
    assert edge_family(EdgeType.OBSERVED_DATA) is EdgeFamily.DATA_FLOW
    assert edge_family(EdgeType.COMPOSES_WITH) is EdgeFamily.STRUCTURAL


def test_five_data_channels():
    assert {c.value for c in DataChannel} == {
        "prompt", "argument", "return", "message", "state"}


# ── slot edges carry the state channel by construction ──────────────────────


def test_declared_slot_edges_tagged_state():
    snap = to_graph(HarnessConfig(processors=[{"_target_": MR}]))
    slot_edges = [e for e in snap.edges
                  if e.edge_type in (EdgeType.WRITES_TO, EdgeType.READS_FROM)]
    assert slot_edges
    assert all(e.metadata.get("data_channel") == "state" for e in slot_edges)


def test_runtime_slot_edges_tagged_state():
    snap = to_graph(HarnessConfig(processors=[RuntimeReg(proc=SlotProbe())]))
    slot_edges = [e for e in snap.runtime_edges
                  if e.edge_type in (EdgeType.WRITES_TO, EdgeType.READS_FROM)]
    assert slot_edges
    assert all(e.metadata.get("data_channel") == "state" for e in slot_edges)


def test_control_edges_carry_no_channel():
    snap = to_graph(HarnessConfig(processors=[
        serialized_dict("x.A", hook="task_start", order=0),
        serialized_dict("x.B", hook="task_start", order=50),
    ]))
    for e in snap.edges:
        if edge_family(e.edge_type) is not EdgeFamily.DATA_FLOW:
            assert "data_channel" not in e.metadata


# ── S3: unknown channel rejects ─────────────────────────────────────────────


def test_unknown_data_channel_rejected():
    snap = to_graph(HarnessConfig(processors=[
        {"_target_": "x.P", "_hooks_": ["task_start"],
         "_writes_slots_": ["memory"]}]))
    edge = next(e for e in snap.edges if e.edge_type is EdgeType.WRITES_TO)
    edge.metadata["data_channel"] = "telepathy"
    report = validate_snapshot(snap)
    assert any(i.error_type == "bad_data_channel" for i in report.issues)


def test_valid_channels_pass():
    snap = to_graph(HarnessConfig(processors=[
        {"_target_": "x.P", "_hooks_": ["task_start"],
         "_writes_slots_": ["memory"]}]))
    report = validate_snapshot(snap)
    assert report.passed, report.reason()


# ── BOM ─────────────────────────────────────────────────────────────────────


def test_bom_deterministic_and_complete():
    cfg = HarnessConfig(processors=[
        serialized_dict("x.ProcB", hook="task_start", singleton_group="b", order=50),
        serialized_dict("x.ProcA", hook="task_start", singleton_group="a", order=0),
        RuntimeReg(proc=SlotProbe(), hook="step_end"),
    ])
    bom1 = graph_bom(to_graph(cfg))
    bom2 = graph_bom(to_graph(cfg))
    assert bom1 == bom2                              # deterministic

    targets = [p["target"] for p in bom1["processors"]]
    assert targets == sorted(targets)                # sorted by node id
    assert {"x.ProcA", "x.ProcB"} <= set(targets)
    assert len(bom1["runtime_processors"]) == 1
    assert bom1["runtime_processors"][0]["bucket"] == "step_end"
    assert "memory" in bom1["slots"]                 # baseline slots present
    assert "probe.slot" in bom1["runtime_slots"]

    fams = bom1["edges_by_family"]
    assert fams.get("control_flow", 0) > 0           # ATTACHED_TO + chain + loop
    assert fams.get("data_flow", 0) >= 1             # SlotProbe write
    assert bom1["data_channels"].get("state", 0) >= 1


def test_bom_empty_config():
    bom = graph_bom(to_graph(HarnessConfig(processors=[])))
    assert bom["processors"] == []
    assert bom["runtime_processors"] == []
    assert len(bom["slots"]) == 6                    # baseline slots
    assert bom["edges_by_family"].get("control_flow", 0) == 1  # loop back only
