# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""Adapter tests — OUR landed session JSONL → gate ``kind`` rows.

Anti-recurrence: the official counterfactual gate died of "fixture schema !=
producer schema".  The core test here runs the adapter over a **verbatim copy of
real journal output** (see ``fixtures/PROVENANCE.md``), not a hand-authored mock,
so a future schema drift in the journal breaks this test loudly.
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from variant_pool import event_replay
from variant_pool.event_replay import HOOK_KINDS

_FIXTURES = Path(__file__).resolve().parent / "fixtures"
_REAL_SESSION = _FIXTURES / "real_session_R9_V0_0383a3ee.jsonl"

# Hard-coded from the companion trajectory md's ``## Result > final_output``
# (== the full final assistant message).  Provenance:
#   recipe/gaia_evolver/runs/e_pervar3/R9/active_pool/V0/trajectories/
#     0383a3ee-47a7-41a4-b493-519bdefe0488.md   (extracted_answer: "rockhopper penguin")
# Copied 2026-08-05; verified byte-for-byte against the fixture's last
# raw_assistant content.
_EXPECTED_FINAL_OUTPUT = (
    "Based on the transcript and summary of the BBC Earth YouTube video "
    '"Top 5 Silliest Animal Moments!", the first segment (the bird segment) '
    "features rockhopper penguins climbing a steep cliff. The transcript "
    'explicitly identifies them as "rockhoppers" (rockhopper penguins).'
    "\n\nFINAL ANSWER: rockhopper penguin"
)
_EXTRACTED_ANSWER = "rockhopper penguin"  # trajectory md: extracted_answer


# ── Real-producer fixture (Task D.1) ──────────────────────────────────────────


def test_real_fixture_yields_at_least_one_of_each_kind():
    rows = event_replay.adapt_jsonl_file(_REAL_SESSION)
    kinds = Counter(r["kind"] for r in rows)
    assert kinds["after_model"] > 0
    assert kinds["after_tool"] > 0
    assert kinds["task_end"] > 0
    # Every emitted row is hook-dispatchable.
    assert all(r["kind"] in HOOK_KINDS for r in rows)


def test_real_fixture_injects_last_assistant_content_as_final_output():
    rows = event_replay.adapt_jsonl_file(_REAL_SESSION)
    task_end = [r for r in rows if r["kind"] == "task_end"]
    assert len(task_end) == 1
    injected = task_end[0]["final_output"]

    # Hard-coded expected string (from the trajectory md ## Result).
    assert injected == _EXPECTED_FINAL_OUTPUT
    # And it carries the recorded extracted_answer.
    assert _EXTRACTED_ANSWER in injected
    assert injected.endswith("FINAL ANSWER: rockhopper penguin")

    # Independent cross-check: injection == the last raw_assistant content in
    # the raw stream (the self-contained default the adapter promises).
    recs = [
        json.loads(line)
        for line in _REAL_SESSION.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    last_assistant = ""
    for r in recs:
        if r.get("type") == "raw_assistant":
            c = (r.get("message") or {}).get("content", "")
            if c:
                last_assistant = c
    assert injected == last_assistant


def test_real_fixture_drops_non_dispatchable_records():
    rows = event_replay.adapt_jsonl_file(_REAL_SESSION)
    # session_start / tools / system / raw_user must not survive as rows.
    assert all(r["kind"] in HOOK_KINDS for r in rows)
    # 9 raw_assistant + 8 raw_tool + 1 episode_end == 18 dispatchable rows.
    assert len(rows) == 18


def test_real_fixture_after_model_flattening():
    rows = event_replay.adapt_jsonl_file(_REAL_SESSION)
    after_model = [r for r in rows if r["kind"] == "after_model"]
    # The first assistant turn issued a WebSearch tool call.
    first = after_model[0]
    assert "content" in first
    assert isinstance(first["tool_calls"], list)
    assert first["tool_calls"][0]["name"] == "WebSearch"
    assert "id" in first["tool_calls"][0]
    assert "input" in first["tool_calls"][0]


def test_real_fixture_after_tool_flattening():
    rows = event_replay.adapt_jsonl_file(_REAL_SESSION)
    after_tool = [r for r in rows if r["kind"] == "after_tool"]
    r0 = after_tool[0]
    assert set(("tool_name", "result", "tool_call_id")).issubset(r0)
    assert r0["tool_name"]  # non-empty tool name
    assert r0["tool_call_id"].startswith("chatcmpl-tool-")


def test_real_fixture_task_end_exit_reason_direct():
    rows = event_replay.adapt_jsonl_file(_REAL_SESSION)
    task_end = [r for r in rows if r["kind"] == "task_end"][0]
    assert task_end["exit_reason"] == "done"


# ── Pass-through, precedence, helpers ─────────────────────────────────────────


def test_kind_format_rows_pass_through_verbatim():
    official = [
        {"kind": "step_start", "step_id": 0},
        {"kind": "after_model", "step_id": 0, "content": "The answer is 42.",
         "tool_calls": []},
        {"kind": "task_end", "final_output": "FINAL ANSWER: 42",
         "exit_reason": "done"},
    ]
    out = event_replay.adapt_records(official)
    assert out == official  # returned unchanged


def test_final_output_override_wins_over_injection():
    recs = [
        {"type": "raw_assistant", "step": 0,
         "message": {"role": "assistant", "content": "penultimate"}},
        {"type": "episode_end", "step": 1, "exit_reason": "done",
         "message": None},
    ]
    rows = event_replay.adapt_records(recs, final_output_override="OVERRIDE VALUE")
    task_end = [r for r in rows if r["kind"] == "task_end"][0]
    assert task_end["final_output"] == "OVERRIDE VALUE"


def test_episode_end_own_final_output_used_when_present():
    # Future runs (after the journal Task-C fix) carry final_output on the
    # episode_end record; the adapter must prefer it over inference.
    recs = [
        {"type": "raw_assistant", "step": 0,
         "message": {"role": "assistant", "content": "stale assistant text"}},
        {"type": "episode_end", "step": 1, "exit_reason": "done",
         "final_output": "AUTHORITATIVE FROM RECORD", "message": None},
    ]
    rows = event_replay.adapt_records(recs)
    task_end = [r for r in rows if r["kind"] == "task_end"][0]
    assert task_end["final_output"] == "AUTHORITATIVE FROM RECORD"


def test_run_jsonls_excludes_trace_and_orders(tmp_path: Path):
    d = tmp_path / "R9-V0-active-tid"
    d.mkdir()
    (d / "seg1.jsonl").write_text("{}\n", encoding="utf-8")
    (d / "seg1_trace.jsonl").write_text("{}\n", encoding="utf-8")
    (d / "seg1_state.json").write_text("{}\n", encoding="utf-8")
    names = [p.name for p in event_replay.run_jsonls_in_dir(d)]
    assert names == ["seg1.jsonl"]  # trace + state excluded


def test_session_dirs_for_task_primary_before_a2(tmp_path: Path):
    tid = "0383a3ee-47a7-41a4-b493-519bdefe0488"
    root = tmp_path / "sessions"
    root.mkdir()
    (root / f"R9-V0-active-{tid}").mkdir()
    (root / f"R9-V0-active-{tid}-a2").mkdir()
    (root / f"R9-V0-active-somethingelse").mkdir()
    dirs = event_replay.session_dirs_for_task(root, tid)
    names = [p.name for p in dirs]
    assert names == [f"R9-V0-active-{tid}", f"R9-V0-active-{tid}-a2"]


def test_adapt_task_end_to_end_over_real_dir_layout(tmp_path: Path):
    tid = "0383a3ee-47a7-41a4-b493-519bdefe0488"
    root = tmp_path / "sessions"
    sdir = root / f"R9-V0-active-{tid}"
    sdir.mkdir(parents=True)
    # Drop the real fixture in as this task's single segment.
    (sdir / "run0.jsonl").write_text(
        _REAL_SESSION.read_text(encoding="utf-8"), encoding="utf-8"
    )
    rows = event_replay.adapt_task(root, tid)
    kinds = Counter(r["kind"] for r in rows)
    assert kinds["after_model"] and kinds["after_tool"] and kinds["task_end"]
    task_end = [r for r in rows if r["kind"] == "task_end"][0]
    assert task_end["final_output"] == _EXPECTED_FINAL_OUTPUT
