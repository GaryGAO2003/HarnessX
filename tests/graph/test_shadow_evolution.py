"""P5 block 20 — shadow evolution kernel (上线门禁 enforcement).

Typed-proposal-only parsing, ≤4 candidates per round (excess explicitly
rejected), hard gate before critic exposure, runtime overlay never a
mutation input, append-only ledger, APPLY/FORK only on measured evidence.
"""

import pytest

from experiments.variant_pool.shadow_evolution import (
    MAX_CANDIDATES_PER_ROUND,
    ShadowLedger,
    ledger_metrics,
    parse_proposal,
    record_evaluation,
    run_shadow_round,
)
from harnessx.core.harness import HarnessConfig
from harnessx.graph.identity import genotype_hash
from harnessx.graph.operators import RewireOrdering
from harnessx.graph.snapshot import to_graph

from tests.graph.fixtures import serialized_dict

PROBE_TARGET = "tests.graph.fixtures.RuntimeProbe"


def _parent():
    return to_graph(HarnessConfig(processors=[
        serialized_dict(PROBE_TARGET, hook="task_start",
                        singleton_group="probe", order=10),
    ]))


def _rewire(order=42, rationale=""):
    return {"operator": "rewire_ordering",
            "params": {"node_id": "proc:runtime_probe", "order": order},
            "rationale": rationale}


# ── proposal normalization ──────────────────────────────────────────────────


def test_parse_valid_proposal():
    op, err = parse_proposal(_rewire())
    assert err == ""
    assert isinstance(op, RewireOrdering)
    assert op.order == 42


def test_parse_rejects_non_dict():
    op, err = parse_proposal("rewire_ordering(order=42)")
    assert op is None and "must be a dict" in err


@pytest.mark.parametrize("key", ["python", "yaml", "mermaid", "code", "patch"])
def test_parse_rejects_payload_smuggling(key):
    op, err = parse_proposal({"operator": "rewire_ordering",
                              "params": {}, key: "..."})
    assert op is None
    assert "format violation" in err


def test_parse_rejects_unknown_operator():
    op, err = parse_proposal({"operator": "write_python", "params": {}})
    assert op is None and "unknown operator" in err


def test_parse_rejects_bad_params():
    op, err = parse_proposal({"operator": "rewire_ordering",
                              "params": {"nonexistent_kw": 1}})
    assert op is None and "bad params" in err


def test_parse_rejects_extra_keys():
    op, err = parse_proposal({"operator": "rewire_ordering", "params": {},
                              "confidence": 0.9})
    assert op is None and "unknown proposal keys" in err


# ── round runner ────────────────────────────────────────────────────────────


def test_round_gates_and_records(tmp_path):
    ledger = ShadowLedger(tmp_path / "shadow.jsonl")
    parent = _parent()
    g_before = genotype_hash(parent)

    result = run_shadow_round(parent, [
        _rewire(order=42, rationale="advisory text"),
        "free-form python please",                                   # parse fail
        {"operator": "remove_processor",
         "params": {"node_id": "proc:ghost"}},                       # precondition fail
    ], ledger, round_id="r1", materialize=False)

    assert [r.decision for r in result.records] == ["GATED", "REJECT", "REJECT"]
    gated = result.records[0]
    assert gated.parent_genotype == g_before
    assert gated.operator == "rewire_ordering"
    assert gated.operator_params == {"node_id": "proc:runtime_probe", "order": 42}
    assert gated.genotype_hash and gated.deployment_hash
    assert gated.rationale == "advisory text"
    assert result.records[1].validation_issues[0]["error_type"] == "parse_error"
    assert result.records[2].validation_issues[0]["error_type"] == "operator_precondition"

    # metrics
    assert result.metrics["proposed"] == 3
    assert result.metrics["parse_rate"] == pytest.approx(2 / 3)
    assert result.metrics["gate_pass_rate"] == pytest.approx(1 / 3)
    assert result.metrics["unique_genotypes"] == 1

    # shadow only: parent untouched, ledger persisted
    assert genotype_hash(parent) == g_before
    assert parent.nodes["proc:runtime_probe"].metadata["_order_"] == 10
    assert len(ledger.records()) == 3


def test_round_capacity_rejects_excess_explicitly(tmp_path):
    ledger = ShadowLedger(tmp_path / "shadow.jsonl")
    proposals = [_rewire(order=i) for i in range(6)]
    result = run_shadow_round(_parent(), proposals, ledger,
                              round_id="r1", materialize=False)
    assert len(result.records) == 6                     # nothing silently dropped
    decisions = [r.decision for r in result.records]
    assert decisions[:MAX_CANDIDATES_PER_ROUND] == ["GATED"] * 4
    assert decisions[4:] == ["REJECT", "REJECT"]
    for r in result.records[4:]:
        assert r.validation_issues[0]["error_type"] == "round_capacity"


def test_runtime_overlay_never_a_mutation_input(tmp_path):
    from harnessx.core.runtime import RuntimeReg
    from tests.graph.fixtures import RuntimeProbe

    parent = to_graph(HarnessConfig(processors=[
        serialized_dict(PROBE_TARGET, hook="task_start", singleton_group="probe"),
        RuntimeReg(proc=RuntimeProbe(), hook="task_start"),
    ]))
    rt_id = next(n for n in parent.runtime_nodes if n.startswith("rt:"))
    result = run_shadow_round(parent, [
        {"operator": "rewire_ordering", "params": {"node_id": rt_id, "order": 1}},
    ], ShadowLedger(tmp_path / "s.jsonl"), round_id="r1", materialize=False)
    assert result.records[0].decision == "REJECT"
    assert "runtime overlay" in result.records[0].validation_issues[0]["message"] \
        or "not in snapshot.nodes" in result.records[0].validation_issues[0]["message"]


def test_only_gate_survivors_exposed_for_ranking(tmp_path):
    result = run_shadow_round(_parent(), [
        _rewire(order=1),
        {"operator": "remove_processor", "params": {"node_id": "proc:ghost"}},
    ], ShadowLedger(tmp_path / "s.jsonl"), round_id="r1", materialize=False)
    assert [r.decision for r in result.gated_records] == ["GATED"]


def test_round_full_materialize_with_real_target(tmp_path):
    result = run_shadow_round(_parent(), [_rewire(order=3)],
                              ShadowLedger(tmp_path / "s.jsonl"),
                              round_id="r1", materialize=True)
    assert result.records[0].decision == "GATED"


def test_gate_rejects_candidate_adding_dangling_after(tmp_path):
    # Insert a processor whose _after_ names a group not in the graph: validation
    # only WARNS (soft dep), so the candidate would otherwise be GATED — but the
    # edit is a silent no-op, so the gate must REJECT it (fail-closed) and name
    # the dangling reference in the rationale.
    ledger = ShadowLedger(tmp_path / "s.jsonl")
    result = run_shadow_round(_parent(), [
        {"operator": "insert_processor",
         "params": {"spec": {"_target_": "x.Ghost", "_hooks_": ["step_end"],
                             "_singleton_group_": "ghost", "_after_": ["nowhere"]}},
         "rationale": "chain after a group that is not present"},
    ], ledger, round_id="r1", materialize=False)
    rec = result.records[0]
    assert rec.decision == "REJECT"
    assert rec.validation_issues[0]["error_type"] == "unresolved_after"
    assert "nowhere" in rec.validation_issues[0]["message"]
    assert "nowhere" in rec.rationale                # dangling ref named in rationale
    assert result.gated_records == []                # never a gate survivor


# ── evaluation decisions (上线门禁) ──────────────────────────────────────────


def _gated_round(tmp_path):
    ledger = ShadowLedger(tmp_path / "shadow.jsonl")
    result = run_shadow_round(_parent(), [_rewire(order=42)], ledger,
                              round_id="r1", materialize=False)
    return ledger, result.records[0].candidate_id


def test_measured_evaluation_can_apply(tmp_path):
    ledger, cid = _gated_round(tmp_path)
    rec = record_evaluation(ledger, cid, decision="APPLY",
                            measured={"pass_rate": 0.8, "tasks": 10})
    assert rec.decided_by == "evaluation"
    assert rec.measured == {"pass_rate": 0.8, "tasks": 10}
    rows = [r for r in ledger.records() if r.candidate_id == cid]
    assert [r.record_kind for r in rows] == ["gate", "evaluation"]  # append-only


def test_llm_judge_alone_cannot_promote(tmp_path):
    ledger, cid = _gated_round(tmp_path)
    with pytest.raises(ValueError, match="LLM judge alone"):
        record_evaluation(ledger, cid, decision="APPLY", measured={})


def test_advisory_text_is_not_evidence(tmp_path):
    ledger, cid = _gated_round(tmp_path)
    with pytest.raises(ValueError, match="numeric metric"):
        record_evaluation(ledger, cid, decision="APPLY",
                          measured={"judge_opinion": "looks great"})


def test_evaluation_cannot_resurrect_gate_reject(tmp_path):
    ledger = ShadowLedger(tmp_path / "shadow.jsonl")
    result = run_shadow_round(_parent(), [
        {"operator": "remove_processor", "params": {"node_id": "proc:ghost"}},
    ], ledger, round_id="r1", materialize=False)
    cid = result.records[0].candidate_id
    with pytest.raises(ValueError, match="cannot resurrect"):
        record_evaluation(ledger, cid, decision="APPLY",
                          measured={"pass_rate": 1.0})


def test_evaluation_unknown_candidate_rejected(tmp_path):
    ledger, _ = _gated_round(tmp_path)
    with pytest.raises(ValueError, match="no gate record"):
        record_evaluation(ledger, "r9/c9", decision="APPLY",
                          measured={"pass_rate": 1.0})


def test_ledger_metrics_summary(tmp_path):
    ledger, cid = _gated_round(tmp_path)
    run_shadow_round(_parent(), ["garbage"], ledger,
                     round_id="r2", materialize=False)
    record_evaluation(ledger, cid, decision="APPLY",
                      measured={"pass_rate": 0.9})
    m = ledger_metrics(ledger)
    assert m["proposals"] == 2
    assert m["parse_rate"] == pytest.approx(0.5)
    assert m["gate_pass_rate"] == pytest.approx(0.5)
    assert m["unique_genotypes"] == 1
    assert m["applied"] == 1
    assert m["forked"] == 0
