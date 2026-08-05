# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""Tests for ``--refuted-signature-gate`` (batch-2b Item 2).

Port of upstream/feat/aegis novelty.py + signatures.py. A candidate whose
file_changes signature was refuted by the gate stack in a prior round is dropped
before evaluation, so the meta-agent cannot re-spend a batch re-proposing a dead
edit (s1k8b103 R4 budget_floor=30 -> R6 budget_floor=55).

Divergence from the official signature (documented in run_variant_pool): our
manifest has no ``diff_sha_after``, so the signature hashes each declared file's
post-write CONTENT (basename-keyed for cross-round stability), falling back to a
hash of the canonical file_change dict when the file is unreadable. Fully offline.
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from experiments.variant_pool.candidate_pipeline import AuditRecord  # noqa: E402
from experiments.variant_pool.manifest import CandidateArtifact, ChangeManifest  # noqa: E402
from recipe.gaia_evolver.run_variant_pool import (  # noqa: E402
    _apply_refuted_signature_gate,
    compute_candidate_signature,
)


def _candidate(
    tmp_dir: Path,
    *,
    proc_body: str = "AAA",
    candidate_id: str = "C-R1-01",
    path: str = "commit_nudge.py",
) -> CandidateArtifact:
    """A candidate whose config dir carries a declared file with ``proc_body``."""
    tmp_dir.mkdir(parents=True, exist_ok=True)
    (tmp_dir / path).write_text(proc_body, encoding="utf-8")
    config = tmp_dir / "config.yaml"
    config.write_text("processors: []\n", encoding="utf-8")
    manifest = ChangeManifest.model_validate(
        {
            "candidate_id": candidate_id,
            "bucket": ["processor"],
            "target_variant": "V0",
            "file_changes": [{"path": path, "action": "modify", "diff_summary": "x"}],
        }
    )
    return CandidateArtifact(config_path=config, manifest=manifest, target_variant="V0")


# ---------------------------------------------------------------------------
# 1. Signature is stable across (per-round) dirs for identical content, and
#    differs when the written content differs.
# ---------------------------------------------------------------------------
def test_signature_stable_for_identical_content(tmp_path: Path) -> None:
    a = _candidate(tmp_path / "r1", proc_body="SAME")
    b = _candidate(tmp_path / "r2", proc_body="SAME")  # different dir, same content
    c = _candidate(tmp_path / "r3", proc_body="DIFFERENT")
    sig_a = compute_candidate_signature(a.manifest, base_dir=a.config_path.parent)
    sig_b = compute_candidate_signature(b.manifest, base_dir=b.config_path.parent)
    sig_c = compute_candidate_signature(c.manifest, base_dir=c.config_path.parent)
    assert sig_a == sig_b  # cross-round dedup: basename + content, not abs path
    assert sig_a != sig_c


def test_signature_dict_fallback_when_file_absent(tmp_path: Path) -> None:
    # No file written for the declared path -> canonical-dict fallback, still
    # deterministic and equal for identical file_changes.
    m = ChangeManifest.model_validate(
        {
            "candidate_id": "C-R1-01",
            "bucket": ["config"],
            "target_variant": "V0",
            "file_changes": [{"path": "gone.py", "action": "modify", "diff_summary": "y"}],
        }
    )
    s1 = compute_candidate_signature(m, base_dir=tmp_path / "nope")
    s2 = compute_candidate_signature(m, base_dir=tmp_path / "also_nope")
    assert s1 == s2 and len(s1) == 64


# ---------------------------------------------------------------------------
# 2. Same signature re-proposed next round -> rejected pre-evaluation.
# ---------------------------------------------------------------------------
def test_refuted_signature_is_rejected(tmp_path: Path) -> None:
    refuted_cand = _candidate(tmp_path / "prev", proc_body="DEAD")
    sig = compute_candidate_signature(
        refuted_cand.manifest, base_dir=refuted_cand.config_path.parent
    )
    # A NEW candidate (new dir) re-proposing the identical edit next round.
    reproposal = _candidate(tmp_path / "next", proc_body="DEAD", candidate_id="C-R2-01")

    ranked, audit = _apply_refuted_signature_gate(
        (reproposal,), (), refuted={sig}, enabled=True
    )
    assert ranked == ()
    assert len(audit) == 1
    assert audit[0].phase == "novelty"
    assert audit[0].disposition == "rejected"
    assert audit[0].candidate_id == "C-R2-01"
    assert audit[0].reason.startswith("novelty: signature previously refuted")


# ---------------------------------------------------------------------------
# 3. A different edit (different content) passes.
# ---------------------------------------------------------------------------
def test_different_file_changes_pass(tmp_path: Path) -> None:
    refuted_cand = _candidate(tmp_path / "prev", proc_body="DEAD")
    sig = compute_candidate_signature(
        refuted_cand.manifest, base_dir=refuted_cand.config_path.parent
    )
    fresh = _candidate(tmp_path / "next", proc_body="ALIVE", candidate_id="C-R2-01")

    ranked, audit = _apply_refuted_signature_gate(
        (fresh,), (), refuted={sig}, enabled=True
    )
    assert ranked == (fresh,)
    assert audit == ()


# ---------------------------------------------------------------------------
# 4. Flag off -> byte-identical pass-through even for a refuted candidate.
# ---------------------------------------------------------------------------
def test_flag_off_is_a_noop_passthrough(tmp_path: Path) -> None:
    cand = _candidate(tmp_path / "prev", proc_body="DEAD")
    sig = compute_candidate_signature(cand.manifest, base_dir=cand.config_path.parent)
    existing = (
        AuditRecord(phase="proposal", disposition="artifact", reason="ok", candidate_id="C-R1-01"),
    )
    ranked, audit = _apply_refuted_signature_gate(
        (cand,), existing, refuted={sig}, enabled=False
    )
    assert ranked == (cand,)
    assert audit == existing
    assert not any(r.phase == "novelty" for r in audit)
