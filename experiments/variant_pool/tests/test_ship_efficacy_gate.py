# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""Tests for the ``--ship-efficacy-gate`` pre-flight (SPEC §7.24).

Run s1k8b103 shipped seven edits, five of which were runtime no-ops -- dead
``file://`` targets that the loader drops silently, and empty prompt templates
-- yet each passed every gate stage and consumed a full candidate-evaluation
batch. The gate rejects such candidates read-only (no rollouts, no LLM) before
the engine spends a batch on them, wiring the rejection through the existing
pipeline-audit accounting.

The four checks are exercised through :func:`_ship_efficacy_reason`, and the
flag on/off behaviour through :func:`_apply_ship_efficacy_gate`, which operates
on the pipeline's ``(ranked_for_gate, audit)`` pair. Fully offline.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from experiments.variant_pool.candidate_pipeline import AuditRecord  # noqa: E402
from experiments.variant_pool.manifest import CandidateArtifact, ChangeManifest  # noqa: E402
from recipe.gaia_evolver.run_variant_pool import (  # noqa: E402
    _apply_ship_efficacy_gate,
    _ship_efficacy_reason,
)

#: A processor whose ``__init__`` takes exactly one keyword, so an unexpected
#: kwarg is expressible (the s1k8b103 R10/V4 ``nudge=`` fault).
PROCESSOR_SOURCE = '''
class NudgeProcessor:
    def __init__(self, warn_at_remaining: int = 3):
        self.warn_at_remaining = warn_at_remaining
'''


def _system_prompt_proc(template_path: str) -> dict:
    return {
        "_target_": "harnessx.processors.context.system_prompt.SystemPromptProcessor",
        "system_builder": {"template_path": template_path},
    }


def _write_config(path: Path, processors: list[dict]) -> Path:
    path.write_text(yaml.safe_dump({"processors": processors}), encoding="utf-8")
    return path


def _candidate(
    config_path: Path,
    *,
    candidate_id: str = "C-R1-01",
    target: str = "V0",
    bucket: tuple[str, ...] = ("prompt",),
) -> CandidateArtifact:
    manifest = ChangeManifest.model_validate(
        {"candidate_id": candidate_id, "bucket": list(bucket), "target_variant": target}
    )
    return CandidateArtifact(config_path=config_path, manifest=manifest, target_variant=target)


def _processor_file(tmp_path: Path) -> Path:
    path = tmp_path / "commit_nudge.py"
    path.write_text(PROCESSOR_SOURCE, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# 1. dead file:// processor target -> check 2 (does not instantiate)
# ---------------------------------------------------------------------------
def test_dead_processor_target_is_rejected(tmp_path: Path) -> None:
    template = tmp_path / "t.j2"
    template.write_text("You are the agent.", encoding="utf-8")
    never_written = tmp_path / "never_written.py"
    config = _write_config(
        tmp_path / "c.yaml",
        [
            _system_prompt_proc(str(template)),
            {"_target_": f"file:///{never_written}::NudgeProcessor"},
        ],
    )
    candidate = _candidate(config)

    reason = _ship_efficacy_reason(candidate, None)
    assert reason is not None
    assert reason.startswith("efficacy:")
    assert "does not instantiate" in reason

    # Flag on: dropped from the queue with an ``efficacy`` rejection record.
    ranked, audit = _apply_ship_efficacy_gate((candidate,), (), None, enabled=True)
    assert ranked == ()
    assert len(audit) == 1
    assert audit[0].phase == "efficacy"
    assert audit[0].disposition == "rejected"
    assert audit[0].candidate_id == "C-R1-01"
    assert audit[0].reason.startswith("efficacy:")

    # Flag off: passes through exactly as today.
    ranked_off, audit_off = _apply_ship_efficacy_gate((candidate,), (), None, enabled=False)
    assert ranked_off == (candidate,)
    assert audit_off == ()


# ---------------------------------------------------------------------------
# 2. unexpected kwarg -> check 2 (does not instantiate)
# ---------------------------------------------------------------------------
def test_unexpected_kwarg_is_rejected(tmp_path: Path) -> None:
    template = tmp_path / "t.j2"
    template.write_text("You are the agent.", encoding="utf-8")
    proc = _processor_file(tmp_path)
    config = _write_config(
        tmp_path / "c.yaml",
        [
            _system_prompt_proc(str(template)),
            # NudgeProcessor takes ``warn_at_remaining``, not ``nudge``.
            {"_target_": f"file:///{proc}::NudgeProcessor", "nudge": 3},
        ],
    )
    candidate = _candidate(config)

    reason = _ship_efficacy_reason(candidate, None)
    assert reason is not None
    assert "does not instantiate" in reason

    ranked, _ = _apply_ship_efficacy_gate((candidate,), (), None, enabled=True)
    assert ranked == ()


# ---------------------------------------------------------------------------
# 3. empty prompt template -> check 3 (missing or empty file)
# ---------------------------------------------------------------------------
def test_empty_template_is_rejected(tmp_path: Path) -> None:
    template = tmp_path / "empty.j2"
    template.write_text("   \n", encoding="utf-8")  # whitespace-only == empty
    config = _write_config(tmp_path / "c.yaml", [_system_prompt_proc(str(template))])
    candidate = _candidate(config)

    reason = _ship_efficacy_reason(candidate, None)
    assert reason is not None
    assert "empty file" in reason

    ranked, _ = _apply_ship_efficacy_gate((candidate,), (), None, enabled=True)
    assert ranked == ()


# ---------------------------------------------------------------------------
# 4. runtime surface identical to parent while a bucket is declared -> check 4
# ---------------------------------------------------------------------------
def test_declared_change_that_does_not_land_is_rejected(tmp_path: Path) -> None:
    template = tmp_path / "shared.j2"
    template.write_text("You are the agent.", encoding="utf-8")
    # Both configs reference the SAME template + same processors: the manifest
    # says ``prompt`` changed, but nothing on the runtime surface did.
    parent = _write_config(tmp_path / "parent.yaml", [_system_prompt_proc(str(template))])
    candidate_config = _write_config(tmp_path / "cand.yaml", [_system_prompt_proc(str(template))])
    candidate = _candidate(candidate_config, bucket=("prompt",))

    reason = _ship_efficacy_reason(candidate, parent)
    assert reason == "efficacy: declared ['prompt'] but runtime surface identical to parent"

    ranked, audit = _apply_ship_efficacy_gate((candidate,), (), parent, enabled=True)
    assert ranked == ()
    assert audit[-1].reason == reason


# ---------------------------------------------------------------------------
# 5. healthy candidate (real processor, valid kwargs, changed template) -> passes
# ---------------------------------------------------------------------------
def test_healthy_candidate_passes(tmp_path: Path) -> None:
    parent_template = tmp_path / "parent.j2"
    parent_template.write_text("Old prompt.", encoding="utf-8")
    parent = _write_config(tmp_path / "parent.yaml", [_system_prompt_proc(str(parent_template))])

    proc = _processor_file(tmp_path)
    candidate_template = tmp_path / "cand.j2"
    candidate_template.write_text("New, improved prompt.", encoding="utf-8")
    candidate_config = _write_config(
        tmp_path / "cand.yaml",
        [
            _system_prompt_proc(str(candidate_template)),
            {"_target_": f"file:///{proc}::NudgeProcessor", "warn_at_remaining": 7},
        ],
    )
    candidate = _candidate(candidate_config, bucket=("prompt", "processor"))

    assert _ship_efficacy_reason(candidate, parent) is None

    # Flag on: a healthy candidate survives, no efficacy record is added, and an
    # existing (non-efficacy) audit record is preserved.
    existing = (AuditRecord(phase="proposal", disposition="artifact", reason="ok", candidate_id="C-R1-01"),)
    ranked, audit = _apply_ship_efficacy_gate((candidate,), existing, parent, enabled=True)
    assert ranked == (candidate,)
    assert audit == existing


# ---------------------------------------------------------------------------
# 6. flag off: none of the new code paths execute
# ---------------------------------------------------------------------------
def test_flag_off_is_a_noop_passthrough(tmp_path: Path) -> None:
    # A candidate the gate WOULD reject (dead processor target) is left untouched
    # when the flag is off: the queue and audit are byte-identical to today.
    template = tmp_path / "t.j2"
    template.write_text("You are the agent.", encoding="utf-8")
    never_written = tmp_path / "never_written.py"
    config = _write_config(
        tmp_path / "c.yaml",
        [
            _system_prompt_proc(str(template)),
            {"_target_": f"file:///{never_written}::NudgeProcessor"},
        ],
    )
    candidate = _candidate(config)
    existing = (AuditRecord(phase="proposal", disposition="artifact", reason="ok", candidate_id="C-R1-01"),)

    ranked, audit = _apply_ship_efficacy_gate((candidate,), existing, None, enabled=False)
    assert ranked == (candidate,)
    assert audit == existing
    assert not any(record.phase == "efficacy" for record in audit)
