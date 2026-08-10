"""B-arm proposer adapter — snapshot → prompt → typed proposals (zero API).

Covers the introspected operator vocabulary (single source of truth), prompt
assembly (BOM node ids, operator names, contract), robust non-gating JSON
extraction, and an end-to-end stub → llm_propose → run_shadow_round path whose
gate decision lands on GATED.
"""

import dataclasses as dc

import pytest

from experiments.variant_pool.proposer import (
    ExtractionResult,
    build_proposer_prompt,
    extract_proposals,
    llm_propose,
    operator_vocabulary,
)
from experiments.variant_pool.shadow_evolution import (
    PROPOSAL_OPERATORS,
    ShadowLedger,
    run_shadow_round,
)
from harnessx.core.harness import HarnessConfig
from harnessx.graph.operators import RewireOrdering
from harnessx.graph.snapshot import to_graph

from tests.graph.fixtures import serialized_dict

PROBE_TARGET = "tests.graph.fixtures.RuntimeProbe"
PROBE_NODE_ID = "proc:runtime_probe"


def _parent():
    return to_graph(HarnessConfig(processors=[
        serialized_dict(PROBE_TARGET, hook="task_start",
                        singleton_group="probe", order=10),
    ]))


def _rewire_json(order=42):
    return (
        '[{"operator":"rewire_ordering",'
        f'"params":{{"node_id":"{PROBE_NODE_ID}","order":{order}}},'
        '"rationale":"reorder ' + PROBE_NODE_ID + '"}]'
    )


# ── operator vocabulary (introspection is the single source) ─────────────────


def test_vocabulary_covers_all_six_operators():
    vocab = operator_vocabulary()
    assert {e["operator"] for e in vocab} == set(PROPOSAL_OPERATORS)
    assert len(vocab) == 6


def test_vocabulary_schema_matches_dataclass_fields():
    # introspection-vs-introspection self-consistency: schema keys must be
    # exactly the operator dataclass field names.
    for entry in operator_vocabulary():
        cls = PROPOSAL_OPERATORS[entry["operator"]]
        assert set(entry["params_schema"]) == {f.name for f in dc.fields(cls)}


def test_vocabulary_rewire_ordering_fields():
    entry = next(e for e in operator_vocabulary()
                 if e["operator"] == "rewire_ordering")
    schema = entry["params_schema"]
    assert set(schema) == {"node_id", "order", "after"}
    assert schema["node_id"]["required"] is True
    assert schema["order"]["required"] is False
    assert schema["order"]["default"] is None
    assert schema["after"]["default"] is None
    assert entry["usage"]                                   # non-empty note


def test_vocabulary_default_factory_field_is_json_ready():
    # dict-typed fields (default_factory=dict) surface an empty-dict default.
    entry = next(e for e in operator_vocabulary()
                 if e["operator"] == "mutate_processor_params")
    assert entry["params_schema"]["param_changes"]["default"] == {}
    assert entry["params_schema"]["node_id"]["required"] is True


# ── prompt assembly ──────────────────────────────────────────────────────────


def test_prompt_contains_all_operator_names():
    prompt = build_proposer_prompt(_parent())
    for name in PROPOSAL_OPERATORS:
        assert name in prompt


def test_prompt_contains_real_bom_node_id():
    prompt = build_proposer_prompt(_parent())
    assert PROBE_NODE_ID in prompt


def test_prompt_contains_max_candidates_and_forbidden_keys():
    prompt = build_proposer_prompt(_parent(), max_candidates=3)
    assert "3" in prompt
    # forbidden-key hint (mirrors the kernel's _FORBIDDEN_KEYS)
    for key in ("python", "yaml", "mermaid", "diff", "patch"):
        assert key in prompt
    # allowed keys named in the contract
    for key in ("operator", "params", "rationale"):
        assert key in prompt


def test_prompt_appends_extra_context_verbatim():
    marker = "PRIOR-ROUND: candidate c0 GATED, c1 REJECT(parse)"
    prompt = build_proposer_prompt(_parent(), extra_context=marker)
    assert marker in prompt


# ── extraction (robust, non-gating) ──────────────────────────────────────────


def test_extract_bare_array():
    res = extract_proposals(_rewire_json())
    assert res.error == ""
    assert len(res.proposals) == 1
    assert res.proposals[0]["operator"] == "rewire_ordering"


def test_extract_fenced_json():
    text = "```json\n" + _rewire_json() + "\n```"
    res = extract_proposals(text)
    assert res.error == ""
    assert len(res.proposals) == 1
    assert res.proposals[0]["params"]["node_id"] == PROBE_NODE_ID


def test_extract_prose_wrapped():
    text = "Sure, here are my proposals:\n" + _rewire_json() + "\nLet me know!"
    res = extract_proposals(text)
    assert res.error == ""
    assert len(res.proposals) == 1


def test_extract_single_object_is_wrapped():
    text = '{"operator":"rewire_ordering","params":{"node_id":"proc:x","order":1}}'
    res = extract_proposals(text)
    assert res.error == ""
    assert isinstance(res.proposals, list) and len(res.proposals) == 1
    assert res.proposals[0]["operator"] == "rewire_ordering"


def test_extract_pure_garbage_yields_empty_and_error():
    res = extract_proposals("I'm sorry, I cannot help with that request.")
    assert res.proposals == []
    assert res.error != ""


def test_extract_none_input():
    res = extract_proposals(None)
    assert res.proposals == []
    assert res.error != ""


def test_extract_preserves_non_object_elements():
    # the extraction layer does not filter; malformed elements pass through
    # verbatim for parse_proposal to reject downstream.
    text = ('[{"operator":"remove_processor","params":{"node_id":"proc:x"}},'
            '42,"loose-string"]')
    res = extract_proposals(text)
    assert res.error == ""
    assert len(res.proposals) == 3
    assert 42 in res.proposals
    assert "loose-string" in res.proposals
    assert res.proposals[0]["operator"] == "remove_processor"


def test_extract_skips_stray_brackets_in_prose():
    # a non-JSON "[...]" in prose must not shadow the real proposal array.
    text = "See step [1] and [2] below.\n" + _rewire_json()
    res = extract_proposals(text)
    assert res.error == ""
    assert len(res.proposals) == 1
    assert res.proposals[0]["operator"] == "rewire_ordering"


# ── end-to-end: stub llm_call → llm_propose → run_shadow_round → GATED ────────


def test_llm_propose_end_to_end_gated(tmp_path):
    parent = _parent()

    captured = {}

    def stub_llm(prompt: str) -> str:
        captured["prompt"] = prompt
        return "```json\n" + _rewire_json(order=42) + "\n```"

    res = llm_propose(parent, stub_llm)
    assert isinstance(res, ExtractionResult)
    assert res.error == ""
    assert len(res.proposals) == 1
    # the injected prompt actually carried the parent contract
    assert PROBE_NODE_ID in captured["prompt"]

    ledger = ShadowLedger(tmp_path / "shadow.jsonl")
    round_result = run_shadow_round(
        parent, res.proposals, ledger, round_id="r1", materialize=False,
    )
    assert len(round_result.records) >= 1
    rec = round_result.records[0]
    assert rec.decision in {"GATED", "REJECT"}
    # this exact rewire_ordering(order=42) shape is a verified gate survivor.
    assert rec.decision == "GATED"
    assert rec.operator == "rewire_ordering"
    assert isinstance(rec.operator_params, dict)


def test_llm_propose_operator_instance_parses_from_extracted():
    # sanity: an extracted proposal round-trips through the operator constructor
    # the kernel uses (no gating in the adapter, but the shape is constructible).
    res = extract_proposals(_rewire_json(order=7))
    op = RewireOrdering(**res.proposals[0]["params"])
    assert op.order == 7
    assert op.node_id == PROBE_NODE_ID


def test_prompt_includes_tunable_ctor_params():
    """mutate proposals must be groundable: the prompt lists each node's ctor
    kwargs with (truncated) current values - the live smoke's only proposal
    died at S0 with empty param_changes because the model had nothing to
    ground on."""
    from harnessx.core.harness import HarnessConfig
    from harnessx.graph.snapshot import to_graph

    snap = to_graph(HarnessConfig(processors=[{
        "_target_": "harnessx.processors.control.cost_guard.CostGuardProcessor",
        "_hook_": "before_model",
        "max_usd": 42.5,
        "long_note": "x" * 200,
    }]))
    prompt = build_proposer_prompt(snap)
    assert "Tunable constructor params" in prompt
    assert "max_usd" in prompt and "42.5" in prompt
    assert "x" * 81 not in prompt          # long values truncated
    assert "param_changes MUST be non-empty" in prompt
