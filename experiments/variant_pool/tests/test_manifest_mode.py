# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""Offline tests for ``--manifest-mode`` (W28): the repo-journal adapter.

The reference fixture is a *real* failure sample. ``runs/forkprobe_11`` (a $3
run) archived C-R1-02 in ``data/rejected_candidates.jsonl`` with eight pydantic
validation errors: the repo meta-agent filled our Table-9 keys with journal
vocabulary (``lens`` / ``lever`` / ``intent`` / ``predicted_affected`` inside
``attribution_signature``) and with natural-language strings where the schema
wants lists/dicts. ``C_R1_02_REPO_YAML`` reconstructs that manifest from the
recorded ``input_value`` fragments so the adapter is tested against what the
meta-agent actually wrote, not a synthetic sample.
"""

from __future__ import annotations

import pytest
import yaml
from pydantic import ValidationError

from variant_pool.manifest import (
    PAPER_MANIFEST_PROVENANCE,
    REPO_JOURNAL_PROVENANCE,
    ChangeManifest,
    RepoJournalFormatError,
    adapt_repo_journal_manifest,
)

# The eight-error C-R1-02 manifest reconstructed from forkprobe_11's
# rejected_candidates.jsonl input_value fragments: string capability_evidence,
# string file_changes bullets, string predicted_impact, and journal
# lens/lever/intent/predicted_affected inside attribution_signature.
C_R1_02_REPO_YAML = """
candidate_id: C-R1-02
bucket: prompt
capability_evidence: "3 web research tasks (05407167, 08c0b6e2, 23dd90aa) share a pattern; needs clearer escalation guidance."
file_changes:
  - "config.yaml (template_path updated)"
  - "templates/gaia_agent_v2.j2 (added source recognition guidance)"
predicted_impact: "Expected to flip 08c0b6e2 and 05407167; verification remains a deeper gap."
attribution_signature:
  lens: failure
  lever: instruction
  intent: corrective
  hypothesis_id: h_source_escalation_v1
  predicted_affected:
    - 05407167-39ec-4d3a-a234-000000000000
    - 23dd90aa-4616-b6dd-dd6534e4825b
target_variant: V0
"""

# A control/processor edit: the journal levers map to a *code* bucket, so the
# paper-only fields (capability_evidence, attribution_signature) become real
# gaps that must be marked, not fabricated.
C_R3_03_REPO_YAML = """
candidate_id: C-R3-03
levers: [instruction, control]
predicted_affected: [08f3a05f-0000-0000-0000-000000000000]
hypothesis_id: h_early_commit_v1
file_changes:
  - "processors/early_commit_ladder.py (forces answer when remaining_steps <= 3)"
  - "config.yaml (register EarlyCommit processor)"
target_variant: V0
"""


# ===========================================================================
# repo mode parses the real failure sample; paper mode still rejects it
# ===========================================================================


def test_paper_mode_still_strictly_rejects_the_repo_sample() -> None:
    """The strict Table-9 parser must keep failing on the repo-vocabulary manifest."""
    with pytest.raises(ValidationError):
        ChangeManifest.from_yaml(C_R1_02_REPO_YAML)


def test_repo_mode_parses_the_real_c_r1_02_failure_sample() -> None:
    manifest = adapt_repo_journal_manifest(C_R1_02_REPO_YAML)

    assert manifest.candidate_id == "C-R1-02"
    assert manifest.provenance == REPO_JOURNAL_PROVENANCE
    # lever "instruction" -> bucket "prompt"
    assert manifest.bucket == ["prompt"]
    # predicted_affected -> predicted_impact.tasks_will_unlock
    assert manifest.predicted_impact.tasks_will_unlock == [
        "05407167-39ec-4d3a-a234-000000000000",
        "23dd90aa-4616-b6dd-dd6534e4825b",
    ]
    # hypothesis_id retained
    assert manifest.source_hypothesis_id == "h_source_escalation_v1"
    # prose file-change bullets structured, content preserved verbatim
    assert manifest.file_changes[0]["path"] == "config.yaml"
    assert manifest.file_changes[0]["action"] == "modify"
    assert "template_path updated" in manifest.file_changes[0]["diff_summary"]
    assert manifest.file_changes[1]["path"] == "templates/gaia_agent_v2.j2"
    # a prompt-bucket candidate is complete and reaches the gate
    assert manifest.validate_complete() == []


def test_repo_mode_marks_capability_evidence_missing_never_fabricates() -> None:
    """The string capability_evidence prose is dropped, not coerced into a triple."""
    manifest = adapt_repo_journal_manifest(C_R1_02_REPO_YAML)
    # No fabricated evidence entry.
    assert manifest.capability_evidence == []
    # The journal lens/lever/intent tags are not a paper attribution signature.
    assert manifest.attribution_signature is None
    # A prompt candidate needs neither, so nothing is flagged as a gap.
    assert manifest.paper_only_gaps() == []


def test_repo_mode_code_bucket_marks_paper_only_gaps_but_still_gates() -> None:
    """A processor edit maps to a code bucket: gaps are marked, not fabricated."""
    manifest = adapt_repo_journal_manifest(C_R3_03_REPO_YAML)

    assert manifest.provenance == REPO_JOURNAL_PROVENANCE
    assert set(manifest.bucket) == {"prompt", "processor"}  # instruction + control
    assert manifest.source_hypothesis_id == "h_early_commit_v1"
    # The paper-only fields are genuinely absent and honestly recorded.
    assert manifest.capability_evidence == []
    assert manifest.attribution_signature is None
    assert manifest.paper_only_gaps() == ["capability_evidence", "attribution_signature"]
    # repo provenance relaxes exactly those two so the seesaw can still judge it.
    assert manifest.validate_complete() == []


def test_repo_provenance_relaxation_does_not_leak_into_paper_manifests() -> None:
    """A paper-provenance code candidate with no evidence still hard-fails stage 1."""
    strict = ChangeManifest.model_validate(
        {
            "candidate_id": "C-R3-03",
            "bucket": ["processor"],
            "file_changes": [
                {"path": "processors/x.py", "action": "create", "diff_summary": "x"}
            ],
            "predicted_impact": {"tasks_will_unlock": ["t1"]},
            "target_variant": "V0",
        }
    )
    assert strict.provenance == PAPER_MANIFEST_PROVENANCE
    problems = strict.validate_complete()
    assert any("capability_evidence" in p for p in problems)
    assert any("attribution_signature" in p for p in problems)
    assert strict.paper_only_gaps() == []


# ===========================================================================
# format mismatch vs missing field
# ===========================================================================


def test_a_non_mapping_manifest_is_a_format_mismatch() -> None:
    """Pure prose / a bare list is a format mismatch, distinct from a missing field."""
    with pytest.raises(RepoJournalFormatError):
        adapt_repo_journal_manifest("- just\n- a\n- list\n")
    with pytest.raises(RepoJournalFormatError):
        adapt_repo_journal_manifest("the meta-agent wrote only prose and no mapping")


def test_a_parsed_but_incomplete_manifest_is_a_missing_field_not_a_format_error() -> None:
    """An empty mapping parses (no format error); its gaps surface at validate_complete."""
    manifest = adapt_repo_journal_manifest("candidate_id: C-R1-01\n")
    assert manifest.provenance == REPO_JOURNAL_PROVENANCE
    problems = manifest.validate_complete()
    assert any("bucket" in p for p in problems)
    assert any("file_changes" in p for p in problems)
    assert any("predicted_impact" in p for p in problems)


def test_fallback_ids_fill_a_manifest_that_omits_them() -> None:
    manifest = adapt_repo_journal_manifest(
        "levers: [instruction]\npredicted_affected: [t1]\n"
        'file_changes: ["gaia_agent.md (tweak)"]\n',
        fallback_candidate_id="C-R2-01",
        fallback_target_variant="V0",
    )
    assert manifest.candidate_id == "C-R2-01"
    assert manifest.target_variant == "V0"
    assert manifest.validate_complete() == []


# ===========================================================================
# provenance stays out of the logged YAML
# ===========================================================================


def test_provenance_fields_are_excluded_from_the_logged_yaml() -> None:
    manifest = adapt_repo_journal_manifest(C_R1_02_REPO_YAML)
    keys = list(yaml.safe_load(manifest.to_yaml()))
    assert "provenance" not in keys
    assert "source_hypothesis_id" not in keys
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
