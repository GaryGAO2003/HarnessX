"""#33 — envelope-aware reachability gate.

Pure-function tests for :mod:`experiments.variant_pool.envelope_gate` plus
wiring tests that drive the gate through ``run_shadow_round`` (the B-arm hard
gate).  The polarity under test: only *provably dead* edits are rejected — an
edit is dead iff BOTH endpoints are unreachable in the envelope; anything with
a binding endpoint, an unknown processor/param, a missing parent value, or no
envelope passes.
"""

import pytest

from experiments.variant_pool.envelope_gate import (
    Envelope,
    judge_candidate,
    judge_param_edit,
)
from experiments.variant_pool.shadow_evolution import (
    ShadowLedger,
    run_shadow_round,
)
from harnessx.core.harness import HarnessConfig
from harnessx.graph.snapshot import to_graph

CAP1 = Envelope(max_cost_usd=1.0, max_steps=20)

COST_GUARD = "harnessx.processors.control.cost_guard.CostGuardProcessor"
LOOP_DET = "harnessx.processors.control.loop_detection.LoopDetectionProcessor"


# ── judge_param_edit: cost_guard.max_usd ─────────────────────────────────────


def test_cost_max_usd_dead_both_above_cap():
    # 40 -> 50 under a $1 cap: the task cap pre-empts the attempt at $1, so
    # neither $40 nor $50 CostGuard threshold is ever reached -> runtime no-op.
    r = judge_param_edit("cost_guard_processor", "max_usd", 40.0, 50.0, CAP1)
    assert r is not None
    assert "cost_guard.max_usd=50.0 unreachable under max_cost_usd=1.0" in r
    assert "old=40.0 also unreachable" in r


def test_cost_max_usd_binding_new_below_cap():
    # 40 -> 0.8: the new threshold fires at $0.80, a stopping behavior the
    # parent (dead at $40) never had -> binding, must pass.
    assert judge_param_edit("cost_guard_processor", "max_usd", 40.0, 0.8, CAP1) is None


def test_cost_max_usd_binding_both_below_cap():
    # 0.9 -> 0.8: both fire, at different costs -> binding, must pass.
    assert judge_param_edit("cost_guard_processor", "max_usd", 0.9, 0.8, CAP1) is None


def test_cost_max_usd_old_missing_passes():
    # parent value unreadable -> insufficient evidence -> pass.
    assert judge_param_edit("cost_guard_processor", "max_usd", None, 50.0, CAP1) is None


# ── judge_param_edit: cost_guard.warning_threshold ───────────────────────────


def test_warning_threshold_dead():
    # max_usd=40 (unchanged), warn ratio 0.8 -> 0.7: warn fires at 40*ratio,
    # i.e. $28 (new) / $32 (old), both far past the $1 cap -> never logs.
    ctx = {"max_usd": 40.0}
    r = judge_param_edit(
        "cost_guard_processor", "warning_threshold", 0.8, 0.7, CAP1, ctx)
    assert r is not None
    assert "cost_guard.warning_threshold=0.7 unreachable under max_cost_usd=1.0" in r
    assert "old=0.8 also unreachable" in r


def test_warning_threshold_binding_product_below_cap():
    # max_usd=1.0, warn 0.9 -> 0.8: warn point 1.0*0.8=$0.80 < $1 -> reachable.
    ctx = {"max_usd": 1.0}
    assert judge_param_edit(
        "cost_guard_processor", "warning_threshold", 0.9, 0.8, CAP1, ctx) is None


def test_warning_threshold_passes_without_max_usd_context():
    # max_usd unresolvable -> cannot decide the product -> pass.
    assert judge_param_edit(
        "cost_guard_processor", "warning_threshold", 0.8, 0.7, CAP1, {}) is None


# ── judge_param_edit: loop_detection count thresholds ────────────────────────


def test_loop_threshold_dead_both_above_steps():
    # both 25 and 30 exceed the 20-step budget -> the detector can never
    # accumulate that many repeats either way -> no-op.
    r = judge_param_edit("loop_detection_processor", "threshold", 25, 30, CAP1)
    assert r is not None
    assert "loop_detection.threshold=30 unreachable under max_steps=20" in r
    assert "old=25 also unreachable" in r


def test_loop_threshold_binding_new_reachable():
    # 25 -> 5: 5 <= 20 -> the detector can now trip -> binding, must pass.
    assert judge_param_edit("loop_detection_processor", "threshold", 25, 5, CAP1) is None


def test_loop_threshold_disabling_edit_is_binding():
    # 5 -> 25: old reachable (detector works), new not (detector disabled) —
    # NOT both dead, so this real behavior change must pass (the gate kills
    # no-ops, not unwise edits).
    assert judge_param_edit("loop_detection_processor", "threshold", 5, 25, CAP1) is None


@pytest.mark.parametrize("param", [
    "warn_threshold", "name_warn_threshold", "compaction_drop_threshold",
])
def test_loop_other_count_params_covered(param):
    r = judge_param_edit("loop_detection_processor", param, 25, 30, CAP1)
    assert r is not None and f"loop_detection.{param}=30" in r


# ── judge_param_edit: default-pass paths (never over-reject) ──────────────────


def test_unknown_param_passes():
    assert judge_param_edit("cost_guard_processor", "mystery", 40.0, 50.0, CAP1) is None


def test_unknown_processor_passes():
    assert judge_param_edit("mystery_processor", "max_usd", 40.0, 50.0, CAP1) is None


def test_no_envelope_passes():
    assert judge_param_edit("cost_guard_processor", "max_usd", 40.0, 50.0, None) is None


def test_missing_envelope_dimension_passes():
    env = Envelope(max_cost_usd=1.0, max_steps=None)
    assert judge_param_edit("loop_detection_processor", "threshold", 25, 30, env) is None


def test_never_raises_on_non_numeric():
    assert judge_param_edit("cost_guard_processor", "max_usd", "abc", "def", CAP1) is None


# ── judge_candidate ──────────────────────────────────────────────────────────


def _reader(parent_params):
    return lambda node_id, param: parent_params.get(param)


def test_candidate_all_dead_rejected():
    reasons = judge_candidate(
        "mutate_processor_params",
        {"node_id": "proc:cost_guard_processor",
         "param_changes": {"max_usd": 50.0}},
        _reader({"max_usd": 40.0}), CAP1)
    assert reasons and "cost_guard.max_usd=50.0" in reasons[0]


def test_candidate_mixed_binding_param_keeps_it_alive():
    # max_usd->50 is dead, but retry_budget is not in the rule table (unknown
    # -> pass), so the candidate survives — one live lever is enough.
    reasons = judge_candidate(
        "mutate_processor_params",
        {"node_id": "proc:cost_guard_processor",
         "param_changes": {"max_usd": 50.0, "retry_budget": 3}},
        _reader({"max_usd": 40.0}), CAP1)
    assert reasons == []


def test_candidate_warning_uses_parent_max_usd_context():
    reasons = judge_candidate(
        "mutate_processor_params",
        {"node_id": "proc:cost_guard_processor",
         "param_changes": {"warning_threshold": 0.7}},
        _reader({"max_usd": 40.0, "warning_threshold": 0.8}), CAP1)
    assert reasons and "warning_threshold=0.7" in reasons[0]


def test_candidate_non_param_operator_passes():
    reasons = judge_candidate(
        "rewire_ordering",
        {"node_id": "proc:cost_guard_processor", "order": 5},
        _reader({}), CAP1)
    assert reasons == []


def test_candidate_no_envelope_passes():
    reasons = judge_candidate(
        "mutate_processor_params",
        {"node_id": "proc:cost_guard_processor",
         "param_changes": {"max_usd": 50.0}},
        _reader({"max_usd": 40.0}), None)
    assert reasons == []


def test_candidate_old_missing_passes():
    reasons = judge_candidate(
        "mutate_processor_params",
        {"node_id": "proc:cost_guard_processor",
         "param_changes": {"max_usd": 50.0}},
        _reader({}), CAP1)
    assert reasons == []


# ── run_shadow_round wiring (B-arm hard gate) ────────────────────────────────


def _parent_snapshot():
    return to_graph(HarnessConfig(processors=[
        {"_target_": LOOP_DET, "_hook_": "step_end", "threshold": 25},
        {"_target_": COST_GUARD, "_hook_": "before_model",
         "max_usd": 40.0, "warning_threshold": 0.8},
    ]))


def _mutate(node_id, changes, rationale=""):
    return {"operator": "mutate_processor_params",
            "params": {"node_id": node_id, "param_changes": changes},
            "rationale": rationale}


def _round(tmp_path, proposal, envelope=CAP1):
    ledger = ShadowLedger(tmp_path / "shadow.jsonl")
    result = run_shadow_round(
        _parent_snapshot(), [proposal], ledger,
        round_id="r1", materialize=False, envelope=envelope)
    return result.records[0]


def test_round_rejects_dead_cost_edit(tmp_path):
    rec = _round(tmp_path, _mutate("proc:cost_guard_processor", {"max_usd": 50.0},
                                   rationale="raise the budget"))
    assert rec.decision == "REJECT"
    assert rec.decided_by == "gate"
    assert rec.validation_issues[0]["error_type"] == "envelope_dead"
    assert rec.validation_issues[0]["layer"] == "envelope"
    assert rec.rationale.startswith("ENVELOPE_DEAD: ")
    assert ("cost_guard.max_usd=50.0 unreachable under max_cost_usd=1.0 "
            "(old=40.0 also unreachable)") in rec.rationale
    # advisory rationale is preserved after the machine reason
    assert "raise the budget" in rec.rationale


def test_round_gates_binding_cost_edit(tmp_path):
    rec = _round(tmp_path, _mutate("proc:cost_guard_processor", {"max_usd": 0.8}))
    assert rec.decision == "GATED"


def test_round_gates_binding_both_below_cap(tmp_path):
    # parent max_usd=40 -> 0.9 would be binding; here 40 -> 0.8 is binding too.
    rec = _round(tmp_path, _mutate("proc:cost_guard_processor", {"max_usd": 0.8}))
    assert rec.decision == "GATED"


def test_round_rejects_dead_warning_edit(tmp_path):
    rec = _round(tmp_path, _mutate("proc:cost_guard_processor",
                                   {"warning_threshold": 0.7}))
    assert rec.decision == "REJECT"
    assert "cost_guard.warning_threshold=0.7" in rec.rationale


def test_round_rejects_dead_loop_edit(tmp_path):
    rec = _round(tmp_path, _mutate("proc:loop_detection_processor", {"threshold": 30}))
    assert rec.decision == "REJECT"
    assert "loop_detection.threshold=30 unreachable under max_steps=20" in rec.rationale


def test_round_gates_binding_loop_edit(tmp_path):
    rec = _round(tmp_path, _mutate("proc:loop_detection_processor", {"threshold": 5}))
    assert rec.decision == "GATED"


def test_round_gates_mixed_param_edit(tmp_path):
    # max_usd->50 dead + retry_budget unknown -> whole candidate lives.
    rec = _round(tmp_path, _mutate("proc:cost_guard_processor",
                                   {"max_usd": 50.0, "retry_budget": 3}))
    assert rec.decision == "GATED"


def test_round_no_envelope_gates_dead_edit(tmp_path):
    # backward compat: without an envelope the pre-#33 gate is byte-identical,
    # so the same dead edit that REJECTs above now GATES.
    rec = _round(tmp_path, _mutate("proc:cost_guard_processor", {"max_usd": 50.0}),
                 envelope=None)
    assert rec.decision == "GATED"
