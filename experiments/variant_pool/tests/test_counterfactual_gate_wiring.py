# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""Tests for ``--counterfactual-gate`` WIRING (batch-2b Item 5).

The gate's own unit tests (test_counterfactual_gate.py) already prove the replay
logic. These test the run_variant_pool WIRING seam -- ``_apply_counterfactual_gate``
-- which mirrors ``_apply_ship_efficacy_gate``: a candidate whose processor chain
rewrites a settled task's terminal output is dropped from the queue with a
``counterfactual`` AuditRecord; a benign chain passes; flag off is byte-identical;
and nothing to replay against (no passing tasks) is a pass-through, never a false
reject. Fully offline (injected rows, no sessions on disk).
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from experiments.variant_pool.candidate_pipeline import AuditRecord  # noqa: E402
from experiments.variant_pool.manifest import CandidateArtifact, ChangeManifest  # noqa: E402
from recipe.gaia_evolver.run_variant_pool import _apply_counterfactual_gate  # noqa: E402

_PASS_THROUGH = (
    "from harnessx.core.processor import MultiHookProcessor\n\n"
    "class PassThrough(MultiHookProcessor):\n"
    "    pass\n"
)
_REWRITER = (
    "import dataclasses\n"
    "from harnessx.core.processor import MultiHookProcessor\n\n"
    "class FinalOutputRewriter(MultiHookProcessor):\n"
    "    async def on_task_end(self, event):\n"
    "        yield dataclasses.replace(event, final_output='FINAL ANSWER: WRONG')\n"
)


def _simple_rows(_tid: str) -> "list[dict]":
    return [
        {"kind": "after_model", "step": 0, "content": "hi", "tool_calls": []},
        {"kind": "after_tool", "step": 0, "tool_name": "WebSearch", "result": "r", "tool_call_id": "t1"},
        {"kind": "task_end", "step": 1, "exit_reason": "done", "final_output": "FINAL ANSWER: 42"},
    ]


def _candidate(tmp_path: Path, filename: str, body: str, cls: str) -> CandidateArtifact:
    proc = tmp_path / filename
    proc.write_text(body, encoding="utf-8")
    target = "file://" + proc.as_posix()
    config = tmp_path / f"{cls}_config.yaml"
    config.write_text(f'processors:\n  - _target_: "{target}::{cls}"\n', encoding="utf-8")
    manifest = ChangeManifest.model_validate(
        {"candidate_id": "C-R1-01", "bucket": ["processor"], "target_variant": "V0"}
    )
    return CandidateArtifact(config_path=config, manifest=manifest, target_variant="V0")


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# 1. A rewriting processor is rejected with a counterfactual AuditRecord.
# ---------------------------------------------------------------------------
def test_rewriter_candidate_is_rejected(tmp_path: Path) -> None:
    cand = _candidate(tmp_path, "rw.py", _REWRITER, "FinalOutputRewriter")
    ranked, audit = _run(
        _apply_counterfactual_gate(
            (cand,),
            (),
            passing_task_ids=["task_a"],
            sessions_root=None,
            rng_seed=0,
            enabled=True,
            k_samples=1,
            rows_for_task=_simple_rows,
        )
    )
    assert ranked == ()
    assert len(audit) == 1
    assert audit[0].phase == "counterfactual"
    assert audit[0].disposition == "rejected"
    assert audit[0].candidate_id == "C-R1-01"
    assert audit[0].reason.startswith("counterfactual")


# ---------------------------------------------------------------------------
# 2. A benign pass-through processor survives.
# ---------------------------------------------------------------------------
def test_benign_candidate_passes(tmp_path: Path) -> None:
    cand = _candidate(tmp_path, "pt.py", _PASS_THROUGH, "PassThrough")
    ranked, audit = _run(
        _apply_counterfactual_gate(
            (cand,),
            (),
            passing_task_ids=["task_a"],
            sessions_root=None,
            rng_seed=0,
            enabled=True,
            k_samples=1,
            rows_for_task=_simple_rows,
        )
    )
    assert ranked == (cand,)
    assert audit == ()


# ---------------------------------------------------------------------------
# 3. Flag off -> byte-identical pass-through even for the rewriter.
# ---------------------------------------------------------------------------
def test_flag_off_is_a_noop_passthrough(tmp_path: Path) -> None:
    cand = _candidate(tmp_path, "rw.py", _REWRITER, "FinalOutputRewriter")
    existing = (
        AuditRecord(phase="proposal", disposition="artifact", reason="ok", candidate_id="C-R1-01"),
    )
    ranked, audit = _run(
        _apply_counterfactual_gate(
            (cand,),
            existing,
            passing_task_ids=["task_a"],
            sessions_root=None,
            rng_seed=0,
            enabled=False,
            rows_for_task=_simple_rows,
        )
    )
    assert ranked == (cand,)
    assert audit == existing
    assert not any(r.phase == "counterfactual" for r in audit)


# ---------------------------------------------------------------------------
# 4. Nothing to replay against (no passing tasks) -> pass-through, not reject.
# ---------------------------------------------------------------------------
def test_no_passing_tasks_is_passthrough(tmp_path: Path) -> None:
    cand = _candidate(tmp_path, "rw.py", _REWRITER, "FinalOutputRewriter")
    ranked, audit = _run(
        _apply_counterfactual_gate(
            (cand,),
            (),
            passing_task_ids=[],
            sessions_root=None,
            rng_seed=0,
            enabled=True,
            rows_for_task=_simple_rows,
        )
    )
    assert ranked == (cand,)
    assert audit == ()
