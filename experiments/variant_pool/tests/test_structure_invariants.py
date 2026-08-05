# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""Tests for ``--structure-invariants`` (batch-4c Item 1).

Two layers: (a) the pure ported invariants in
``experiments.variant_pool.structure_invariants`` (IV-3 body / IV-8 slot_type
dispatch / IV-9 bucket-file / IV-11 exploration / IV-12 iterates_from — one
pass + reject per invariant, plus the explorer/legacy dispatch); (b) the recipe
wiring — ``_apply_structure_invariants`` on/off pins, the manifest-body sourcing
from ``_meta_scratch/manifest.yaml``, the flag default, and the provenance.

Faithful to ``upstream/feat/aegis:harnessx/aegis/gates/structure.py`` (the
net-new invariants our ``ChangeManifest.validate_complete`` does not already
enforce). Fully offline (no LLM, no network).
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from experiments.variant_pool.candidate_pipeline import AuditRecord  # noqa: E402
from experiments.variant_pool.manifest import CandidateArtifact, ChangeManifest  # noqa: E402
from experiments.variant_pool.structure_invariants import (  # noqa: E402
    _check_exploration_response,
    _check_iterates_from,
    validate_candidate_structure,
)
from recipe.gaia_evolver import run_variant_pool as rvp  # noqa: E402

_ANCHOR = rvp._parse_digest_anchors
_EVIDENCE = "## Failure Evidence\ntrajectories/abc.jsonl#step_1 -- what went wrong\n"


def _manifest(
    *,
    bucket=("config",),
    files=(("x.yaml", "modify"),),
    iterates_from: str | None = None,
) -> ChangeManifest:
    return ChangeManifest.model_validate(
        {
            "candidate_id": "C-R2-01",
            "bucket": list(bucket),
            "iterates_from": iterates_from,
            "file_changes": [
                {"path": p, "action": a, "diff_summary": "d"} for p, a in files
            ],
            "predicted_impact": {"tasks_will_unlock": ["t1"]},
            "target_variant": "V0",
        }
    )


# ===========================================================================
# Layer (a): the pure ported invariants.
# ===========================================================================

# -- IV-3 (body): ## Failure Evidence + >=1 anchor -------------------------
def test_iv3_body_passes_with_section_and_anchor() -> None:
    r = validate_candidate_structure(_manifest(), _EVIDENCE, anchor_parser=_ANCHOR)
    assert r.ok is True


def test_iv3_body_rejects_missing_failure_evidence_section() -> None:
    r = validate_candidate_structure(
        _manifest(), "## Root Cause\njust prose, no evidence header", anchor_parser=_ANCHOR
    )
    assert r.ok is False
    assert "IV-3 missing Failure Evidence section" in r.reason


def test_iv3_body_rejects_section_without_anchor() -> None:
    r = validate_candidate_structure(
        _manifest(), "## Failure Evidence\nthe model just gave up", anchor_parser=_ANCHOR
    )
    assert r.ok is False
    assert "zero evidence anchors" in r.reason


def test_iv3_body_absent_is_na_not_a_reject() -> None:
    # No manifest-body carrier (repo-journal candidate): body checks are N/A, not
    # a reject — documented deviation. IV-9 (manifest-level) still passes here.
    r = validate_candidate_structure(_manifest(), "", anchor_parser=_ANCHOR)
    assert r.ok is True


# -- IV-8 (slot_type dispatch) ---------------------------------------------
def test_explorer_slot_bypasses_all_evidence_checks() -> None:
    # A manifest that WOULD fail IV-9 (config bucket + .py file) and has no body
    # passes as an explorer slot (official: OK right after required keys).
    bad = _manifest(bucket=("config",), files=(("x.py", "modify"),))
    r = validate_candidate_structure(bad, "", slot_type="explorer", anchor_parser=_ANCHOR)
    assert r.ok is True


def test_legacy_slot_relaxes_iv9_but_keeps_body_check() -> None:
    bad = _manifest(bucket=("config",), files=(("x.py", "modify"),))
    # legacy: IV-9 relaxed (the .py under config no longer rejects) ...
    assert validate_candidate_structure(bad, _EVIDENCE, slot_type="legacy", anchor_parser=_ANCHOR).ok
    # ... but the body check still applies.
    r = validate_candidate_structure(bad, "no header", slot_type="legacy", anchor_parser=_ANCHOR)
    assert r.ok is False and "IV-3" in r.reason


# -- IV-9: bucket <-> file-extension consistency ---------------------------
def test_iv9_passes_when_extension_matches_bucket() -> None:
    r = validate_candidate_structure(
        _manifest(bucket=("processor",), files=(("p.py", "create"),)),
        _EVIDENCE,
        anchor_parser=_ANCHOR,
    )
    assert r.ok is True


def test_iv9_rejects_extension_outside_bucket_allowlist() -> None:
    r = validate_candidate_structure(
        _manifest(bucket=("config",), files=(("p.py", "create"),)),
        _EVIDENCE,
        anchor_parser=_ANCHOR,
    )
    assert r.ok is False
    assert "IV-9" in r.reason and "p.py" in r.reason


def test_iv9_multi_bucket_unions_allowlists() -> None:
    # bucket=[prompt, processor] permits .md + .py + .yaml (union) — the 4b
    # list-bucket deviation. A .md and a .py together pass.
    r = validate_candidate_structure(
        _manifest(bucket=("prompt", "processor"), files=(("a.md", "modify"), ("b.py", "create"))),
        _EVIDENCE,
        anchor_parser=_ANCHOR,
    )
    assert r.ok is True


# -- IV-11: prior strategy_concern exploration enforcement -----------------
def test_iv11_passes_when_candidate_targets_the_flagged_bucket() -> None:
    assert _check_exploration_response(("config",), "## Root Cause\nx", {"config"}) is None


def test_iv11_passes_with_a_substantive_infeasibility_section() -> None:
    body = (
        "## Why flagged direction is infeasible\n"
        "The tools bucket cannot help: web_search returns 403 on every attempt, "
        "as the bash output above shows across all five tasks.\n"
    )
    assert _check_exploration_response(("config",), body, {"tools"}) is None


def test_iv11_rejects_untargeted_flag_without_justification() -> None:
    reason = _check_exploration_response(("config",), "## Root Cause\nx", {"tools"})
    assert reason is not None and reason.startswith("IV-11")


def test_iv11_rejects_too_short_justification() -> None:
    body = "## Why flagged direction is infeasible\ntoo short\n"
    reason = _check_exploration_response(("config",), body, {"tools"})
    assert reason is not None and "too short" in reason


def test_iv11_noop_when_nothing_flagged() -> None:
    assert _check_exploration_response(("config",), "", None) is None
    assert _check_exploration_response(("config",), "", set()) is None


# -- IV-12: iterates_from lineage ------------------------------------------
_PRIOR = {"C-R1-01": {"round": 1, "superseded_by": None}}


def test_iv12_passes_for_valid_earlier_cited_target() -> None:
    body = "improves on C-R1-01 whose hit_rate held up"
    assert _check_iterates_from("C-R1-01", body, _PRIOR, current_round=2) is None


def test_iv12_rejects_unknown_target() -> None:
    reason = _check_iterates_from("C-R9-99", "cites C-R9-99", _PRIOR, current_round=2)
    assert reason is not None and "not found in ship ledger" in reason


def test_iv12_rejects_same_or_later_round_target() -> None:
    reason = _check_iterates_from("C-R1-01", "cites C-R1-01", _PRIOR, current_round=1)
    assert reason is not None and "must be < current round" in reason


def test_iv12_rejects_superseded_target() -> None:
    prior = {"C-R1-01": {"round": 1, "superseded_by": "C-R1-02"}}
    reason = _check_iterates_from("C-R1-01", "cites C-R1-01", prior, current_round=3)
    assert reason is not None and "superseded by" in reason


def test_iv12_rejects_uncited_target_in_body() -> None:
    reason = _check_iterates_from("C-R1-01", "body never names it", _PRIOR, current_round=2)
    assert reason is not None and "cite the target ship id" in reason


def test_iv12_noop_when_absent_or_no_ledger() -> None:
    # Absent field: check-only-when-present.
    assert _check_iterates_from(None, "", _PRIOR, current_round=2) is None
    # No ledger (the production path): validated as a non-empty string only.
    assert _check_iterates_from("C-R1-01", "", None, current_round=2) is None
    assert _check_iterates_from("   ", "", None, current_round=2) == "IV-12 iterates_from must be a non-empty string"


# ===========================================================================
# Layer (b): the recipe wiring.
# ===========================================================================
def _candidate_with_body(tmp_path: Path, manifest: ChangeManifest, body: str | None) -> CandidateArtifact:
    """A CandidateArtifact whose config sits next to a _meta_scratch/manifest.yaml.

    ``body`` is written as the prose AFTER the manifest front matter (the real
    on-disk form); ``None`` writes no manifest file (the repo-journal case).
    """
    cand_dir = tmp_path / manifest.candidate_id
    cand_dir.mkdir(parents=True, exist_ok=True)
    config = cand_dir / "config.yaml"
    config.write_text("processors: []\n", encoding="utf-8")
    if body is not None:
        scratch = cand_dir / "_meta_scratch"
        scratch.mkdir(parents=True, exist_ok=True)
        (scratch / "manifest.yaml").write_text(
            f"---\n{manifest.to_yaml()}---\n{body}", encoding="utf-8"
        )
    return CandidateArtifact(config_path=config, manifest=manifest, target_variant="V0")


def test_manifest_body_reads_prose_after_front_matter(tmp_path: Path) -> None:
    cand = _candidate_with_body(tmp_path, _manifest(), _EVIDENCE)
    assert "## Failure Evidence" in rvp._candidate_manifest_body(cand)
    # No manifest file ⇒ empty body (the repo-journal case).
    cand2 = _candidate_with_body(tmp_path / "nofile", _manifest(), None)
    assert rvp._candidate_manifest_body(cand2) == ""


def test_apply_gate_rejects_with_structure_audit_record(tmp_path: Path) -> None:
    # IV-9 violation: config bucket carrying a .py file.
    bad = _manifest(bucket=("config",), files=(("x.py", "modify"),))
    cand = _candidate_with_body(tmp_path, bad, _EVIDENCE)
    ranked, audit = rvp._apply_structure_invariants(
        (cand,), (), strategy_concern_flagged=set(), enabled=True
    )
    assert ranked == ()
    assert len(audit) == 1
    assert audit[0].phase == "structure"
    assert audit[0].disposition == "rejected"
    assert audit[0].candidate_id == "C-R2-01"
    assert audit[0].reason.startswith("structure: IV-9")


def test_apply_gate_consumes_prior_strategy_concern_iv11(tmp_path: Path) -> None:
    # A config candidate that ignores a flagged 'tools' bucket without a
    # justification section is dropped by IV-11.
    cand = _candidate_with_body(tmp_path, _manifest(bucket=("config",)), "## Root Cause\nx")
    ranked, audit = rvp._apply_structure_invariants(
        (cand,), (), strategy_concern_flagged={"tools"}, enabled=True
    )
    assert ranked == () and audit[-1].reason.startswith("structure: IV-11")


def test_apply_gate_keeps_a_clean_candidate(tmp_path: Path) -> None:
    cand = _candidate_with_body(tmp_path, _manifest(), _EVIDENCE)
    existing = (AuditRecord(phase="proposal", disposition="artifact", reason="ok", candidate_id="C-R2-01"),)
    ranked, audit = rvp._apply_structure_invariants(
        (cand,), existing, strategy_concern_flagged=set(), enabled=True
    )
    assert ranked == (cand,)
    assert audit == existing


def test_apply_gate_off_is_byte_identical_passthrough(tmp_path: Path) -> None:
    # A candidate the gate WOULD reject passes through untouched when off.
    bad = _manifest(bucket=("config",), files=(("x.py", "modify"),))
    cand = _candidate_with_body(tmp_path, bad, _EVIDENCE)
    existing = (AuditRecord(phase="proposal", disposition="artifact", reason="ok", candidate_id="C-R2-01"),)
    ranked, audit = rvp._apply_structure_invariants(
        (cand,), existing, strategy_concern_flagged={"tools"}, enabled=False
    )
    assert ranked == (cand,)
    assert audit == existing
    assert not any(r.phase == "structure" for r in audit)


# ===========================================================================
# flag / provenance.
# ===========================================================================
def test_flag_defaults_off() -> None:
    parser = rvp.build_arg_parser()
    assert parser.parse_args([]).structure_invariants is False
    assert parser.parse_args(["--structure-invariants"]).structure_invariants is True


def test_provenance_none_when_off() -> None:
    assert rvp._structure_invariants_provenance(False) is None
    warn = rvp._structure_invariants_provenance(True)
    assert warn is not None
    assert "structure_invariants=on" in warn
    assert "byte-for-byte" in warn
