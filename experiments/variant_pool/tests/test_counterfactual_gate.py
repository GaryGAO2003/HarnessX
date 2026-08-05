# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""Counterfactual gate tests — ported+hardened replay.

A gate that cannot catch a known positive must never ship, so the anchor tests
here are: a planted-regression processor is CAUGHT, a pass-through is OK, and
zero coverage FAILS closed (the official latent-no-op condition).  Candidate
processors are instantiated through the real builder path (``file://…::Class``),
exercising M-41 resolution end-to-end.

Note on the frozen-event divergence: our events are ``@dataclass(frozen=True)``,
so a processor mutates output by yielding ``dataclasses.replace(event, …)`` — the
official gate's ``SimpleNamespace`` in-place ``event.final_output = …`` model
raises ``FrozenInstanceError`` here (see
``test_inplace_mutation_is_recorded_not_silent``).
"""
from __future__ import annotations

from pathlib import Path

from variant_pool import counterfactual_gate, event_replay
from variant_pool.counterfactual_gate import check_counterfactual_replay

_FIXTURES = Path(__file__).resolve().parent / "fixtures"
_REAL_SESSION = _FIXTURES / "real_session_R9_V0_0383a3ee.jsonl"
_REAL_TASK_ID = "0383a3ee-47a7-41a4-b493-519bdefe0488"


# ── processor module bodies (written to temp files, loaded via file://) ───────

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
_EXIT_FLIPPER = (
    "import dataclasses\n"
    "from harnessx.core.processor import MultiHookProcessor\n\n"
    "class ExitFlipper(MultiHookProcessor):\n"
    "    async def on_task_end(self, event):\n"
    "        yield dataclasses.replace(event, exit_reason='error')\n"
)
_AFTER_TOOL_RAISER = (
    "from harnessx.core.processor import MultiHookProcessor\n\n"
    "class AfterToolRaiser(MultiHookProcessor):\n"
    "    async def on_after_tool(self, event):\n"
    "        raise RuntimeError('boom in after_tool')\n"
    "        yield event  # unreachable; makes this an async generator\n"
)
_INPLACE_MUTATOR = (
    "from harnessx.core.processor import MultiHookProcessor\n\n"
    "class InplaceMutator(MultiHookProcessor):\n"
    "    async def on_task_end(self, event):\n"
    "        event.final_output = 'FINAL ANSWER: mutated'  # frozen → raises\n"
    "        yield event\n"
)


def _cfg(tmp_path: Path, filename: str, body: str, cls: str) -> str:
    """Write a processor module and return a one-processor config YAML."""
    p = tmp_path / filename
    p.write_text(body, encoding="utf-8")
    target = "file://" + p.as_posix()
    return f'processors:\n  - _target_: "{target}::{cls}"\n'


def _simple_rows(_tid: str) -> "list[dict]":
    return [
        {"kind": "after_model", "step": 0, "content": "hi", "tool_calls": []},
        {"kind": "after_tool", "step": 0, "tool_name": "WebSearch",
         "result": "r", "tool_call_id": "t1"},
        {"kind": "task_end", "step": 1, "exit_reason": "done",
         "final_output": "FINAL ANSWER: 42"},
    ]


# ── Task D.2 — positive control: planted regression MUST be caught ────────────


async def test_planted_final_output_regression_is_caught(tmp_path: Path):
    cfg = _cfg(tmp_path, "rw.py", _REWRITER, "FinalOutputRewriter")
    r = await check_counterfactual_replay(
        new_config_yaml_text=cfg,
        passing_task_ids=["task_a"],
        rows_for_task=_simple_rows,
        k_samples=1,
    )
    assert not r.ok
    assert "task_a" in r.reason
    assert "final_output" in r.reason
    assert r.regressions


async def test_planted_exit_reason_regression_is_caught(tmp_path: Path):
    cfg = _cfg(tmp_path, "flip.py", _EXIT_FLIPPER, "ExitFlipper")
    r = await check_counterfactual_replay(
        new_config_yaml_text=cfg,
        passing_task_ids=["task_a"],
        rows_for_task=_simple_rows,
        k_samples=1,
    )
    assert not r.ok
    assert "exit_reason" in r.reason


# ── Task D.3 — neutral control: pass-through is OK ────────────────────────────


async def test_pass_through_processor_is_ok(tmp_path: Path):
    cfg = _cfg(tmp_path, "pt.py", _PASS_THROUGH, "PassThrough")
    r = await check_counterfactual_replay(
        new_config_yaml_text=cfg,
        passing_task_ids=["task_a"],
        rows_for_task=_simple_rows,
        k_samples=1,
    )
    assert r.ok, r.reason
    assert r.events_dispatched > 0
    assert r.processors_replayed == 1
    assert r.tasks_replayed == 1
    assert r.swallowed_exceptions == []


# ── Task D.4 — zero coverage fails closed ─────────────────────────────────────


async def test_unknown_event_types_fail_closed(tmp_path: Path):
    cfg = _cfg(tmp_path, "pt.py", _PASS_THROUGH, "PassThrough")

    def only_unknown(_tid: str):
        return [{"kind": "step_start", "step": 0}, {"kind": "mystery", "step": 1}]

    r = await check_counterfactual_replay(
        new_config_yaml_text=cfg,
        passing_task_ids=["task_a"],
        rows_for_task=only_unknown,
        k_samples=1,
    )
    assert not r.ok
    assert "coverage" in r.reason.lower()
    assert r.events_dispatched == 0


async def test_empty_processor_chain_is_zero_coverage(tmp_path: Path):
    # Divergence #1: the official gate accepted `processors: []`; we reject it,
    # because a chain that replays nothing cannot certify no-regression.
    r = await check_counterfactual_replay(
        new_config_yaml_text="processors: []\n",
        passing_task_ids=["task_a"],
        rows_for_task=_simple_rows,
        k_samples=1,
    )
    assert not r.ok
    assert "coverage" in r.reason.lower()
    assert r.processors_replayed == 0


# ── Task D.5 — official-fixture compat (hand-built kind rows) ──────────────────


async def test_official_kind_format_rows_still_replay(tmp_path: Path):
    # Mirrors the official gate's own unit-test fixture shape.
    def official_rows(_tid: str):
        return [
            {"kind": "step_start", "step_id": 0},
            {"kind": "after_model", "step_id": 0, "content": "The answer is 42.",
             "tool_calls": []},
            {"kind": "task_end", "final_output": "FINAL ANSWER: 42",
             "exit_reason": "done"},
        ]

    cfg = _cfg(tmp_path, "pt.py", _PASS_THROUGH, "PassThrough")
    r = await check_counterfactual_replay(
        new_config_yaml_text=cfg,
        passing_task_ids=["task_a"],
        rows_for_task=official_rows,
        k_samples=1,
    )
    assert r.ok, r.reason
    assert r.events_dispatched > 0  # after_model + task_end dispatched


# ── Task D.6 — exception accounting (default fail-open vs strict) ──────────────


async def test_after_tool_raiser_default_ok_but_recorded(tmp_path: Path):
    cfg = _cfg(tmp_path, "raise.py", _AFTER_TOOL_RAISER, "AfterToolRaiser")
    r = await check_counterfactual_replay(
        new_config_yaml_text=cfg,
        passing_task_ids=["task_a"],
        rows_for_task=_simple_rows,
        k_samples=1,
    )
    assert r.ok, r.reason  # final_output unchanged → no regression
    assert r.events_dispatched > 0
    assert r.swallowed_exceptions
    assert any("AfterToolRaiser" in name for name, _ in r.swallowed_exceptions)


async def test_after_tool_raiser_strict_fails(tmp_path: Path):
    cfg = _cfg(tmp_path, "raise.py", _AFTER_TOOL_RAISER, "AfterToolRaiser")
    r = await check_counterfactual_replay(
        new_config_yaml_text=cfg,
        passing_task_ids=["task_a"],
        rows_for_task=_simple_rows,
        k_samples=1,
        strict=True,
    )
    assert not r.ok
    assert "strict" in r.reason.lower()
    assert r.swallowed_exceptions


async def test_inplace_mutation_is_recorded_not_silent(tmp_path: Path):
    # The official gate's model (event.final_output = ...) hits a frozen event
    # here: it raises FrozenInstanceError, which is RECORDED, and (fail-open)
    # the output is unchanged → ok. It must never silently corrupt.
    cfg = _cfg(tmp_path, "mut.py", _INPLACE_MUTATOR, "InplaceMutator")
    r = await check_counterfactual_replay(
        new_config_yaml_text=cfg,
        passing_task_ids=["task_a"],
        rows_for_task=_simple_rows,
        k_samples=1,
    )
    assert r.ok, r.reason
    assert r.swallowed_exceptions
    assert any("InplaceMutator" in name for name, _ in r.swallowed_exceptions)


# ── Official-parity skips + malformed config ──────────────────────────────────


async def test_skip_when_no_candidate_cfg():
    r = await check_counterfactual_replay(
        new_config_yaml_text=None,
        passing_task_ids=["task_a"],
        rows_for_task=_simple_rows,
    )
    assert r.ok and "skipped" in r.reason.lower()


async def test_skip_when_no_passing_tasks(tmp_path: Path):
    cfg = _cfg(tmp_path, "pt.py", _PASS_THROUGH, "PassThrough")
    r = await check_counterfactual_replay(
        new_config_yaml_text=cfg,
        passing_task_ids=[],
        rows_for_task=_simple_rows,
    )
    assert r.ok and "skipped" in r.reason.lower()


async def test_invalid_yaml_fails(tmp_path: Path):
    r = await check_counterfactual_replay(
        new_config_yaml_text="processors: [unclosed\n",
        passing_task_ids=["task_a"],
        rows_for_task=_simple_rows,
    )
    assert not r.ok
    assert "invalid" in r.reason.lower()


# ── End-to-end on the REAL producer fixture (strong anti-recurrence) ──────────


async def test_real_fixture_pass_through_is_ok(tmp_path: Path):
    cfg = _cfg(tmp_path, "pt.py", _PASS_THROUGH, "PassThrough")

    def real_rows(_tid: str):
        return event_replay.adapt_jsonl_file(_REAL_SESSION)

    r = await check_counterfactual_replay(
        new_config_yaml_text=cfg,
        passing_task_ids=[_REAL_TASK_ID],
        rows_for_task=real_rows,
        k_samples=1,
    )
    assert r.ok, r.reason
    assert r.tasks_replayed == 1
    assert r.events_dispatched == 18  # 9 after_model + 8 after_tool + 1 task_end


async def test_real_fixture_regression_is_caught(tmp_path: Path):
    cfg = _cfg(tmp_path, "rw.py", _REWRITER, "FinalOutputRewriter")

    def real_rows(_tid: str):
        return event_replay.adapt_jsonl_file(_REAL_SESSION)

    r = await check_counterfactual_replay(
        new_config_yaml_text=cfg,
        passing_task_ids=[_REAL_TASK_ID],
        rows_for_task=real_rows,
        k_samples=1,
    )
    assert not r.ok
    assert _REAL_TASK_ID in r.reason
    assert "final_output" in r.reason
