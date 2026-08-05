# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""Task-C forward fix: the journal's ``episode_end`` record carries ``final_output``.

Before the fix, ``HarnessJournal`` held ``event.final_output`` on the
``TaskEndEvent`` but dropped it from the emitted ``episode_end`` session record
(``message: None`` and no ``final_output`` key).  The counterfactual replay gate
therefore had to *infer* the terminal output from the last assistant message.
Adding the one key makes future runs gate-ready with zero adapter inference.

This test serialises a ``TaskEndEvent`` through the real journal writer and
asserts the persisted ``episode_end`` record now includes ``final_output`` (and
that the neighbouring keys are untouched).
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from harnessx.core.events import TaskEndEvent
from harnessx.tracing.journal import HarnessJournal


def test_episode_end_record_carries_final_output(tmp_path: Path):
    session_id = "sess-final-output"
    run_id = "run-final-output"
    journal = HarnessJournal(
        base_dir=str(tmp_path / "sessions"),
        export_jsonl=True,
        silent=True,
        session_id=session_id,
    )
    journal._open_files(run_id)  # mirrors TaskStartEvent side effects
    session_dir = journal._session_dir  # captured before on_event closes files

    final_output = "FINAL ANSWER: rockhopper penguin"
    end_event = TaskEndEvent(
        run_id=run_id,
        step_id=2,
        exit_reason="done",
        final_output=final_output,
        total_steps=2,
        total_tokens=500,
    )
    asyncio.run(journal.on_event(end_event))

    segment = Path(session_dir) / f"{run_id}.jsonl"
    records = [
        json.loads(line)
        for line in segment.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    episode_ends = [r for r in records if r.get("type") == "episode_end"]
    assert len(episode_ends) == 1
    ep = episode_ends[0]

    # The Task-C key.
    assert "final_output" in ep, "episode_end must carry final_output (Task C)"
    assert ep["final_output"] == final_output

    # Neighbouring keys untouched.
    assert ep["exit_reason"] == "done"
    assert ep["total_steps"] == 2
    assert ep["message"] is None
