"""Block 11 — L4 snapshot rebuild (decl-driven nodes + edges).

VM2 (exact ATTACHED_TO counts), VM13 (pure read), VM14 (WKD fallback +
phantom-dep removal + explicit-empty), VM7 graph side (model.route slot),
L4.3 dynamic slots, L4.4 ctor-kwargs deepcopy (VM12g), metadata
no-pinning rules (P2), WKD drift warning (约束 #6).
"""

from unittest.mock import patch

import pytest

from harnessx.core.harness import HarnessConfig
from harnessx.graph.snapshot import _WKD_DRIFT_SEEN, _extract_declaration, to_graph
from harnessx.graph.types import EdgeType, NodeType

CG = "harnessx.processors.control.cost_guard.CostGuardProcessor"
MR = "harnessx.processors.multi_model.model_router.ModelRouterProcessor"


@pytest.fixture(autouse=True)
def _reset_wkd_drift_dedup():
    """The WKD-drift dedup set is per-process; clear it so each test's
    warning/info assertions see a clean slate regardless of run order."""
    _WKD_DRIFT_SEEN.clear()
    yield
    _WKD_DRIFT_SEEN.clear()


def _cfg(procs):
    return HarnessConfig(processors=procs)


def _attached_from(snap, src):
    return [e for e in snap.edges
            if e.edge_type == EdgeType.ATTACHED_TO and e.source_id == src]


# ── VM2: exact ATTACHED_TO edge counts ──────────────────────────────────────


@pytest.mark.parametrize("hooks, expected", [
    (["task_start", "task_end"], 2),
    (["before_model"], 1),
    (["*"], 8),
    ([], 0),
])
def test_attached_edge_counts(hooks, expected):
    snap = to_graph(_cfg([{"_target_": "x.ProcA", "_hooks_": hooks}]))
    assert len(_attached_from(snap, "proc:proc_a")) == expected


def test_wildcard_never_attaches_model_or_tool():
    snap = to_graph(_cfg([{"_target_": "x.ProcA", "_hooks_": ["*"]}]))
    targets = {e.target_id for e in _attached_from(snap, "proc:proc_a")}
    assert "hook:model" not in targets and "hook:tool" not in targets


# ── VM13: pure read — nonexistent target still exports ──────────────────────


def test_nonexistent_target_pure_read():
    snap = to_graph(_cfg([{
        "_target_": "nonexistent.module.Class",
        "_hooks_": ["task_start"],
        "_order_": 5,
    }]))
    assert "proc:class" in snap.nodes
    assert len(_attached_from(snap, "proc:class")) == 1
    assert snap.nodes["proc:class"].metadata["_order_"] == 5


# ── VM14: legacy WKD fallback + phantom dep + explicit empty ────────────────


def test_legacy_dict_wkd_fallback():
    snap = to_graph(_cfg([{"_target_": CG}]))  # only _target_
    node = snap.nodes["proc:cost_guard_processor"]
    assert node.metadata["_hooks_"] == ["before_model"]
    assert node.metadata["_order_"] == 10
    assert node.metadata["_singleton_group_"] == "cost_guard"
    assert node.metadata["_hook_"] == "*"  # natural bucket from WKD
    assert len(_attached_from(snap, "proc:cost_guard_processor")) == 1


def test_costguard_phantom_slot_dep_gone():
    snap = to_graph(_cfg([{"_target_": CG}]))
    reads = [e for e in snap.edges
             if e.edge_type == EdgeType.READS_FROM
             and e.source_id == "proc:cost_guard_processor"]
    assert reads == []  # no READS_FROM slot:cost (phantom removed)
    node_meta = snap.nodes["proc:cost_guard_processor"].metadata
    assert node_meta["_reads_event_fields_"] == ["BeforeModelEvent.cumulative_cost_usd"]
    assert "_reads_slots_" not in node_meta  # never resolved → not written


def test_explicit_empty_hooks_blocks_wkd_fallback():
    snap = to_graph(_cfg([{"_target_": CG, "_hooks_": []}]))
    node = snap.nodes["proc:cost_guard_processor"]
    assert node.metadata["_hooks_"] == []  # explicit empty preserved
    assert _attached_from(snap, "proc:cost_guard_processor") == []


def test_legacy_empty_hook_string_blocks_fallback_and_inference():
    snap = to_graph(_cfg([{"_target_": CG, "_hook_": ""}]))
    node = snap.nodes["proc:cost_guard_processor"]
    assert node.metadata["_hooks_"] == []
    assert node.metadata["_hook_"] == ""
    assert _attached_from(snap, "proc:cost_guard_processor") == []


def test_string_inference_for_unknown_target():
    # not a WKD key, but the class-name pattern matches the inference table
    snap = to_graph(_cfg([{"_target_": "other.pkg.LoopDetectionProcessor"}]))
    node = snap.nodes["proc:loop_detection_processor"]
    assert node.metadata["_hooks_"] == ["step_end"]  # inferred
    assert len(_attached_from(snap, "proc:loop_detection_processor")) == 1


# ── VM7 graph side + L4.3 dynamic slots ─────────────────────────────────────


def test_model_router_slot_node_and_edge():
    snap = to_graph(_cfg([{"_target_": MR}]))
    assert "slot:model.route" in snap.nodes  # dynamic slot created
    assert snap.nodes["slot:model.route"].metadata["slot_type"] == "dynamic"
    writes = [e for e in snap.edges
              if e.edge_type == EdgeType.WRITES_TO
              and e.source_id == "proc:model_router_processor"]
    assert len(writes) == 1
    assert writes[0].target_id == "slot:model.route"  # dot preserved


def test_dynamic_slot_from_dict_declaration():
    snap = to_graph(_cfg([{
        "_target_": "x.ProcA", "_hooks_": ["task_start"],
        "_writes_slots_": ["custom.key"], "_reads_slots_": ["memory"],
    }]))
    assert "slot:custom.key" in snap.nodes
    writes = [e for e in snap.edges if e.edge_type == EdgeType.WRITES_TO
              and e.source_id == "proc:proc_a"]
    reads = [e for e in snap.edges if e.edge_type == EdgeType.READS_FROM
             and e.source_id == "proc:proc_a"]
    assert [e.target_id for e in writes] == ["slot:custom.key"]
    assert [e.target_id for e in reads] == ["slot:memory"]  # baseline slot reused


def test_baseline_slots_always_present():
    snap = to_graph(_cfg([]))
    for name in ("memory", "plan", "cost", "tool_registry", "workspace", "sandbox"):
        assert f"slot:{name}" in snap.nodes


# ── L4.4: ctor kwargs deepcopy (VM12g one direction) ────────────────────────


def test_ctor_kwargs_saved_and_isolated():
    src = {"_target_": "x.ProcA", "_hooks_": ["task_start"],
           "cfg": {"depth": [1, 2]}, "name": ""}
    snap = to_graph(_cfg([src]))
    meta = snap.nodes["proc:proc_a"].metadata
    assert meta["_ctor_kwargs_"] == {"cfg": {"depth": [1, 2]}, "name": ""}
    src["cfg"]["depth"].append(99)  # mutate source AFTER export
    assert meta["_ctor_kwargs_"]["cfg"]["depth"] == [1, 2]  # deepcopy isolation


# ── metadata no-pinning rules (P2) ──────────────────────────────────────────


def test_unknown_target_writes_no_sentinel_keys():
    snap = to_graph(_cfg([{"_target_": "zz.TotallyUnknown"}]))
    meta = snap.nodes["proc:totally_unknown"].metadata
    for key in ("_hook_", "_hooks_", "_order_", "_singleton_group_", "_after_",
                "_writes_slots_", "_reads_slots_",
                "_reads_event_fields_", "_writes_event_fields_"):
        assert key not in meta, f"{key} pinned for an undeclared processor"


def test_explicit_falsey_values_survive():
    snap = to_graph(_cfg([{
        "_target_": "x.ProcA", "_hooks_": ["task_start"],
        "_order_": 0, "_singleton_group_": "", "_after_": [],
    }]))
    meta = snap.nodes["proc:proc_a"].metadata
    assert meta["_order_"] == 0
    assert meta["_singleton_group_"] == ""
    assert meta["_after_"] == []


def test_hooks_only_dict_gets_no_fabricated_bucket():
    snap = to_graph(_cfg([{"_target_": "x.ProcA",
                           "_hooks_": ["step_start", "step_end"]}]))
    meta = snap.nodes["proc:proc_a"].metadata
    assert "_hook_" not in meta  # bucket unknown — never fabricated from coverage[0]
    assert meta["_hooks_"] == ["step_start", "step_end"]


# ── bucket read-back in _extract_declaration (VM5) ──────────────────────────


def test_extract_star_bucket_readback():
    decl = _extract_declaration({"_target_": "x.P", "_hook_": "*"}, "x.P")
    assert decl.hook == "*"
    assert decl.hooks == ("*",)


def test_extract_wkd_natural_bucket():
    decl = _extract_declaration({}, CG)
    assert decl.hook == "*"                      # WKD natural bucket
    assert decl.hooks == ("before_model",)       # WKD coverage


def test_extract_malformed_values_degrade_safely():
    decl = _extract_declaration({
        "_hooks_": "notalist", "_order_": "garbage",
        "_singleton_group_": None, "_after_": 7,
    }, "zz.Unknown")
    assert decl.hooks == ()
    assert decl.order == 50
    assert decl.singleton_group == ""
    assert decl.after == ()


# ── WKD drift warning (约束 #6) ─────────────────────────────────────────────


def test_wkd_drift_warns():
    # _order_ present but ≠ WKD → real value drift → WARNING (retained).
    with patch("harnessx.graph.snapshot._log.warning") as mock_warn:
        to_graph(_cfg([{"_target_": CG, "_order_": 999}]))
    assert mock_warn.call_count == 1
    assert "_order_" in str(mock_warn.call_args)


def test_wkd_matching_values_no_warning():
    with patch("harnessx.graph.snapshot._log.warning") as mock_warn:
        to_graph(_cfg([{"_target_": CG, "_order_": 10,
                        "_singleton_group_": "cost_guard"}]))
    mock_warn.assert_not_called()


def test_wkd_drift_deduped_across_calls():
    # Same (target, key set) across two to_graph() calls → a single WARNING.
    cfg = _cfg([{"_target_": CG, "_order_": 999}])
    with patch("harnessx.graph.snapshot._log.warning") as mock_warn:
        to_graph(cfg)
        to_graph(cfg)
    assert mock_warn.call_count == 1


def test_wkd_legacy_absent_keys_are_info_not_warning():
    # Pre-v5.3 shape: _hooks_/_order_/_singleton_group_ all absent → INFO only,
    # never WARNING, with the legacy-config wording.
    with patch("harnessx.graph.snapshot._log.warning") as mock_warn, \
            patch("harnessx.graph.snapshot._log.info") as mock_info:
        to_graph(_cfg([{"_target_": CG, "_hook_": "*"}]))
    mock_warn.assert_not_called()
    assert mock_info.call_count == 1
    msg = str(mock_info.call_args)
    assert "predates" in msg and "legacy config" in msg
    for k in ("_hooks_", "_order_", "_singleton_group_"):
        assert k in msg


def test_wkd_legacy_info_also_deduped_across_calls():
    cfg = _cfg([{"_target_": CG, "_hook_": "*"}])
    with patch("harnessx.graph.snapshot._log.info") as mock_info:
        to_graph(cfg)
        to_graph(cfg)
    assert mock_info.call_count == 1


def test_wkd_mixed_drift_and_absence_split_by_level():
    # _order_ present-and-wrong (drift → WARNING); _hooks_/_singleton_group_
    # absent (legacy → INFO).  Disjoint key sets → both fire, one each.
    with patch("harnessx.graph.snapshot._log.warning") as mock_warn, \
            patch("harnessx.graph.snapshot._log.info") as mock_info:
        to_graph(_cfg([{"_target_": CG, "_order_": 999}]))
    assert mock_warn.call_count == 1
    assert "_order_" in str(mock_warn.call_args)
    assert mock_info.call_count == 1
    assert "_hooks_" in str(mock_info.call_args)
