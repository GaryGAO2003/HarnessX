# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""Offline unit tests for ``variant_pool.manifest`` (W13 + W24).

The reference fixture is the paper's own worked example, C-R10-02 (appendix C.1,
p.36-37): the GAIA / Sonnet 4.6 round-10 composite edit that added the
``WikiTextFetch`` tool. Its manifest is the only complete manifest instance in
the paper, so it is what the round-trip has to survive verbatim.
"""

from __future__ import annotations

import pytest
import yaml

from variant_pool.manifest import (
    BUCKETS,
    CANDIDATE_ID_RE,
    DEFAULT_LEVEL2_LABEL,
    LEVEL2_CLAIM,
    AttributionSignature,
    ChangeManifest,
    ImpactCategory,
    Level2Evidence,
    PredictedImpact,
    check_level2_roundtrip,
    impact_category,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

#: C-R10-02 verbatim (p.37), plus the ``target_variant`` field the paper's
#: schema lacks and variant isolation requires (ours, SPEC §2.6).
C_R10_02_YAML = """
candidate_id: C-R10-02
bucket: [tools, prompt, config]
capability_evidence:
  - type: http_endpoint
    claim: "MediaWiki API returns full plain-text extract where WebFetch returns 0 chars"
    evidence: "GET .../w/api.php?...&explaintext=true -> 10,529 chars for Franklin/Foxboro_Line"
  - type: other
    claim: "tool return survives provider serialization to the model (Level 2)"
    evidence: "_prepare_messages([tool_msg]) keeps content as 10,529-char string"
file_changes:
  - {path: R10/applied/C-R10-02/wiki_text_fetch.py, action: create, diff_summary: "WikiTextFetch via MediaWiki API"}
  - {path: R10/applied/C-R10-02/gaia_agent.md, action: create, diff_summary: "R8 prompt + one WikiTextFetch line"}
  - {path: R10/applied/C-R10-02/config.yaml, action: create, diff_summary: "register tool; restore R8; drop budget processor"}
predicted_impact:
  tasks_will_unlock: [db4fd70a, f0f46385, 983bba7c, 08f3a05f, 5e2a91b0]
  tasks_will_stabilize: [4b6bb5f7, 42d4198c]
  tasks_at_risk: []
attribution_signature:
  type: tool_call
  tool_name: WikiTextFetch
  expected_min_calls: 1
target_variant: V0
"""


@pytest.fixture
def c_r10_02() -> ChangeManifest:
    return ChangeManifest.from_yaml(C_R10_02_YAML)


def _minimal(**overrides) -> ChangeManifest:
    """A complete prompt-bucket manifest, so tests can break one thing at a time."""
    data = {
        "candidate_id": "C-R3-01",
        "bucket": ["prompt"],
        "capability_evidence": [],
        "file_changes": [{"path": "gaia_agent.md", "action": "modify", "diff_summary": "one line"}],
        "predicted_impact": {"tasks_will_unlock": ["t1"]},
        "target_variant": "V0",
    }
    data.update(overrides)
    return ChangeManifest.model_validate(data)


# ===========================================================================
# YAML round-trip on the paper's own instance
# ===========================================================================


def test_c_r10_02_parses_with_every_paper_field(c_r10_02: ChangeManifest) -> None:
    assert c_r10_02.candidate_id == "C-R10-02"
    assert c_r10_02.bucket == ["tools", "prompt", "config"]
    assert len(c_r10_02.capability_evidence) == 2
    assert len(c_r10_02.file_changes) == 3
    assert c_r10_02.predicted_impact.tasks_will_unlock == [
        "db4fd70a",
        "f0f46385",
        "983bba7c",
        "08f3a05f",
        "5e2a91b0",
    ]
    assert c_r10_02.predicted_impact.tasks_will_stabilize == ["4b6bb5f7", "42d4198c"]
    assert c_r10_02.predicted_impact.tasks_at_risk == []
    assert c_r10_02.attribution_signature == AttributionSignature(
        type="tool_call", tool_name="WikiTextFetch", expected_min_calls=1
    )
    assert c_r10_02.target_variant == "V0"


def test_c_r10_02_survives_a_yaml_round_trip(c_r10_02: ChangeManifest) -> None:
    """to_yaml -> from_yaml loses nothing."""
    again = ChangeManifest.from_yaml(c_r10_02.to_yaml())
    assert again == c_r10_02
    assert again.model_dump() == c_r10_02.model_dump()


def test_the_emitted_yaml_keeps_the_paper_field_order(c_r10_02: ChangeManifest) -> None:
    """The template (p.32) fixes the order; the logged manifest should match it."""
    emitted = c_r10_02.to_yaml()
    keys = list(yaml.safe_load(emitted))
    assert keys == [
        "candidate_id",
        "bucket",
        "iterates_from",
        "capability_evidence",
        "file_changes",
        "predicted_impact",
        "attribution_signature",
        "target_variant",
    ]


def test_iterates_from_round_trips_even_though_table_9_omits_it() -> None:
    """H7: ``iterates_from`` lives only in the p.32 template, and is the lineage hook."""
    manifest = _minimal(candidate_id="C-R11-01", iterates_from="C-R10-02")
    assert ChangeManifest.from_yaml(manifest.to_yaml()).iterates_from == "C-R10-02"
    assert _minimal().iterates_from is None


def test_front_matter_form_is_accepted() -> None:
    """The on-disk manifest is YAML front matter inside a ``.md`` (p.32/p.37)."""
    text = f"---\n{_minimal().to_yaml()}---\n## Failure Evidence\ntrajectories/abc_r0.jsonl#step_5\n"
    parsed = ChangeManifest.from_yaml(text)
    assert parsed.candidate_id == "C-R3-01"
    assert parsed.target_variant == "V0"


def test_a_scalar_bucket_is_accepted() -> None:
    """The template writes ``bucket: <prompt|tools|...> # or a list`` (p.32)."""
    assert ChangeManifest.from_yaml("candidate_id: C-R1-01\nbucket: prompt\n").bucket == ["prompt"]


def test_an_unknown_key_is_a_parse_error_not_an_incompleteness() -> None:
    with pytest.raises(Exception):  # pydantic ValidationError; extra="forbid"
        ChangeManifest.from_yaml("candidate_id: C-R1-01\nvariant_id: V1\n")


def test_an_empty_manifest_parses_so_the_gate_can_judge_it() -> None:
    """Parsing must not pre-empt the deterministic gate (module docstring)."""
    assert ChangeManifest.from_yaml("").candidate_id == ""


# ===========================================================================
# validate_complete — gate stage 1 (§4.3 p.10)
# ===========================================================================


def test_the_paper_instance_is_complete(c_r10_02: ChangeManifest) -> None:
    assert c_r10_02.validate_complete() == []


def test_a_missing_target_variant_is_detected(c_r10_02: ChangeManifest) -> None:
    """Ours: the paper's schema has no variant field, variant isolation needs one."""
    c_r10_02.target_variant = ""
    problems = c_r10_02.validate_complete()
    assert any(p.startswith("target_variant") for p in problems)


@pytest.mark.parametrize(
    "field,value,prefix",
    [
        ("candidate_id", "", "candidate_id"),
        ("candidate_id", "R10-02", "candidate_id"),
        ("bucket", [], "bucket"),
        ("bucket", ["memory"], "bucket"),
        ("file_changes", [], "file_changes"),
    ],
)
def test_missing_or_malformed_fields_are_reported(field, value, prefix) -> None:
    manifest = _minimal(**{field: value})
    problems = manifest.validate_complete()
    assert any(p.startswith(prefix) for p in problems), problems


def test_a_code_candidate_needs_capability_evidence() -> None:
    """p.32: "I believe this will work" is not acceptable."""
    assert any(p.startswith("capability_evidence") for p in _minimal(bucket=["tools"]).validate_complete())
    # ... while a pure prompt candidate is exempt (counterfactual gate, p.32/M18)
    assert _minimal(bucket=["prompt"]).validate_complete() == []


def test_malformed_evidence_and_file_change_entries_are_reported() -> None:
    manifest = _minimal(
        capability_evidence=[{"type": "other", "claim": "x"}],
        file_changes=[{"path": "a.py", "action": "rewrite", "diff_summary": "x"}],
    )
    problems = manifest.validate_complete()
    assert any("capability_evidence[0]" in p and "evidence" in p for p in problems), problems
    assert any("file_changes[0]" in p and "rewrite" in p for p in problems), problems


def test_a_manifest_that_predicts_nothing_is_incomplete() -> None:
    """p.35: the manifest exists to be falsifiable; no prediction, nothing to falsify."""
    problems = _minimal(predicted_impact={}).validate_complete()
    assert any(p.startswith("predicted_impact") for p in problems), problems


def test_contradictory_predictions_are_reported() -> None:
    manifest = _minimal(
        predicted_impact={
            "tasks_will_unlock": ["t1", "t2"],
            "tasks_will_stabilize": ["t2"],
            "tasks_at_risk": ["t1"],
        }
    )
    problems = manifest.validate_complete()
    assert any("both ALL_FAIL and PARTIAL_PASS" in p for p in problems), problems
    assert any("at risk while currently at 0 passes" in p for p in problems), problems


def test_attribution_signature_is_a_hard_gate_for_non_prompt_candidates() -> None:
    """W19 / SPEC §6.4: the paper says "recommended", we require it."""
    problems = _minimal(bucket=["config"], attribution_signature=None).validate_complete()
    assert any(p.startswith("attribution_signature") for p in problems), problems


def test_a_tool_call_signature_needs_a_tool_name() -> None:
    manifest = _minimal(
        bucket=["config"],
        attribution_signature={"type": "tool_call", "expected_min_calls": 1},
    )
    assert any("needs tool_name" in p for p in manifest.validate_complete())


def test_expected_min_calls_must_be_at_least_one() -> None:
    manifest = _minimal(
        bucket=["config"],
        attribution_signature={"type": "prompt_feature", "expected_min_calls": 0},
    )
    assert any("expected_min_calls" in p for p in manifest.validate_complete())


def test_candidate_id_pattern_matches_the_paper_examples() -> None:
    assert CANDIDATE_ID_RE.match("C-R3-01")
    assert CANDIDATE_ID_RE.match("C-R10-02")
    assert not CANDIDATE_ID_RE.match("C-R10-2")


# ===========================================================================
# M17 — the three categories are pass@2 categories
# ===========================================================================


@pytest.mark.parametrize(
    "n_pass,n_att,expected",
    [
        (0, 2, ImpactCategory.WILL_UNLOCK),  # ALL_FAIL
        (1, 2, ImpactCategory.WILL_STABILIZE),  # PARTIAL_PASS
        (2, 2, ImpactCategory.AT_RISK),  # currently passing
    ],
)
def test_pass_at_2_outcomes_map_to_the_three_manifest_categories(n_pass, n_att, expected) -> None:
    assert impact_category(n_pass, n_att) is expected
    assert expected.value in PredictedImpact.model_fields


def test_stabilize_is_unreachable_without_pass_at_k(k=1) -> None:
    """M17: PARTIAL_PASS cannot occur under single-attempt evaluation.

    With one rollout the only outcomes are 0/1 and 1/1, so
    ``tasks_will_stabilize`` is permanently empty and the manifest contract is
    semantically incomplete — the third independent argument for pass@2 (W17).
    """
    categories = {impact_category(n_pass, k) for n_pass in range(k + 1)}
    assert ImpactCategory.WILL_STABILIZE not in categories
    assert categories == {ImpactCategory.WILL_UNLOCK, ImpactCategory.AT_RISK}
    # under pass@2 it appears
    assert impact_category(1, 2) is ImpactCategory.WILL_STABILIZE


@pytest.mark.parametrize("n_pass,n_att", [(3, 2), (-1, 2), (0, 0)])
def test_impossible_outcomes_are_rejected(n_pass, n_att) -> None:
    with pytest.raises(ValueError):
        impact_category(n_pass, n_att)


def test_predicted_flips_is_unlock_plus_stabilize(c_r10_02: ChangeManifest) -> None:
    """p.37 arithmetic: 5 unlock + 2 stabilize = the seven predicted tasks."""
    flips = c_r10_02.predicted_impact.predicted_flips()
    assert len(flips) == 7
    assert flips[:5] == c_r10_02.predicted_impact.tasks_will_unlock
    assert flips[5:] == c_r10_02.predicted_impact.tasks_will_stabilize


def test_at_risk_tasks_are_not_predicted_flips() -> None:
    impact = PredictedImpact(tasks_will_unlock=["a"], tasks_at_risk=["b"])
    assert impact.predicted_flips() == ["a"]


def test_bucket_enumeration_matches_table_9() -> None:
    assert set(BUCKETS) == {"prompt", "tools", "config", "processor"}


# ===========================================================================
# W24 — Level-2 round-trip (p.32; C-R10-02 evidence p.37)
# ===========================================================================


def _identity(content: str) -> str:
    """A provider serializer that hands the content through untouched."""
    return f'[{{"role": "tool", "content": "{content}"}}]'


def test_content_survives_the_round_trip() -> None:
    article = "x" * 10_529  # the rail-line article of C-R10-02
    evidence = check_level2_roundtrip(article, _identity)

    assert evidence.survived is True
    assert evidence.serialized_len >= len(article)
    assert evidence.note.startswith(DEFAULT_LEVEL2_LABEL)
    assert "10,529-char string" in evidence.note


def test_the_note_reproduces_the_paper_evidence_line() -> None:
    """Verbatim from C-R10-02 (p.37)."""
    evidence = check_level2_roundtrip("x" * 10_529, lambda content: content)
    assert evidence.note == "_prepare_messages([tool_msg]) keeps content as 10,529-char string"


def test_a_truncating_serializer_fails_the_check() -> None:
    """"a unit call that returns does not prove the agent sees the return" (p.32)."""

    def truncating(content: str) -> str:
        return content[:200] + "...[truncated]"

    evidence = check_level2_roundtrip("y" * 10_529, truncating)

    assert evidence.survived is False
    assert evidence.serialized_len == 214
    assert "10,529 chars in" in evidence.note
    assert "214 chars out" in evidence.note


def test_a_zero_char_tool_return_never_survives() -> None:
    """The C-R10-02 failure mode itself: WebFetch returning 0 chars (p.36)."""
    evidence = check_level2_roundtrip("", _identity)
    assert evidence.survived is False
    assert evidence.serialized_len == 0
    assert "0 chars" in evidence.note


def test_a_raising_serializer_is_a_failed_round_trip_not_a_crash() -> None:
    def exploding(content: str) -> str:
        raise RuntimeError("content is not JSON-serialisable")

    evidence = check_level2_roundtrip("z" * 10, exploding)
    assert evidence.survived is False
    assert "RuntimeError" in evidence.note


def test_a_non_string_serializer_result_fails() -> None:
    evidence = check_level2_roundtrip("z" * 10, lambda content: {"content": content})
    assert evidence.survived is False
    assert "dict" in evidence.note


def test_a_non_string_tool_output_is_a_programming_error() -> None:
    with pytest.raises(TypeError):
        check_level2_roundtrip(b"bytes", _identity)  # type: ignore[arg-type]


def test_the_check_labels_which_stage_it_simulated() -> None:
    """Tools go through the provider serializer, processors through the next stage (p.32)."""
    evidence = check_level2_roundtrip("abc", lambda c: c, label="next pipeline stage")
    assert evidence.note.startswith("next pipeline stage")


def test_evidence_becomes_a_capability_evidence_entry(c_r10_02: ChangeManifest) -> None:
    """p.32: attach the verifying output as ``capability_evidence``."""
    evidence = check_level2_roundtrip("x" * 10_529, lambda c: c)
    entry = evidence.as_capability_evidence()

    assert entry == {
        "type": "other",
        "claim": LEVEL2_CLAIM,
        "evidence": "_prepare_messages([tool_msg]) keeps content as 10,529-char string",
    }
    # which is exactly the second capability-evidence entry of C-R10-02 (p.37)
    assert entry == c_r10_02.capability_evidence[1]


def test_level2_evidence_is_found_in_the_manifest(c_r10_02: ChangeManifest) -> None:
    found = c_r10_02.level2_evidence()
    assert found is not None
    assert "Level 2" in found["claim"]
    assert _minimal(bucket=["prompt"]).level2_evidence() is None


def test_level2_evidence_dataclass_is_frozen() -> None:
    evidence = Level2Evidence(survived=True, serialized_len=3, note="n")
    with pytest.raises(Exception):
        evidence.survived = False  # type: ignore[misc]
