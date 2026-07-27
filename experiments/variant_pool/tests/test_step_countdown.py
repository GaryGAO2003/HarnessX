# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""Offline tests for the ``--step-countdown`` additive processor (P2-1 / audit T4).

Two layers, all fully offline (no network, no rollouts, no LLM):

* the :class:`StepCountdownProcessor` itself — per-step counting, per-task reset,
  N-2 escalation, exactly-once injection, and the ephemeral "replaced not
  accumulated" behaviour that follows from injecting at ``before_model``;
* the recipe wiring in ``run_variant_pool`` — flag-off config byte-identity,
  flag-on config carrying the processor and surviving a canonicalize + YAML
  round-trip, and the byte-safe lock/provenance record.

The recipe lives under ``recipe/``; the path bootstrap mirrors
``test_force_gate.py`` so ``recipe.gaia_evolver.run_variant_pool`` and its
``experiments.variant_pool`` imports resolve to the exact objects the recipe uses.
"""

from __future__ import annotations

import asyncio
import sys
import types
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from harnessx.core.builder import _instantiate  # noqa: E402
from harnessx.core.events import (  # noqa: E402
    BeforeModelEvent,
    Message,
    StepStartEvent,
    TaskEndEvent,
    TaskStartEvent,
)
from harnessx.core.harness import HarnessConfig  # noqa: E402
from harnessx.processors.control.step_countdown import (  # noqa: E402
    COUNTDOWN_MARKER,
    StepCountdownProcessor,
)
from recipe.gaia_evolver import run_variant_pool as rvp  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _Task:
    """Minimal task exposing the per-task step budget the run loop reads."""

    def __init__(self, max_steps: int) -> None:
        self.max_steps = max_steps


def _run(proc: StepCountdownProcessor, event):
    """Drive one event through ``proc.process`` (real _DISPATCH) and return the
    last yielded event."""

    async def _collect():
        return [ev async for ev in proc.process(event)]

    out = asyncio.run(_collect())
    return out[-1]


def _markers(msgs) -> int:
    """Total count of the countdown marker across all message contents."""
    return sum(
        (m.content.count(COUNTDOWN_MARKER) if isinstance(m.content, str) else 0) for m in msgs
    )


def _line(msgs) -> str:
    """The single injected countdown line found in *msgs* (raises if not exactly one)."""
    hits = [
        m.content for m in msgs if isinstance(m.content, str) and COUNTDOWN_MARKER in m.content
    ]
    assert len(hits) == 1, f"expected exactly one countdown line, got {len(hits)}"
    # The marker may be appended to an existing user turn; return the marker-bearing tail.
    return hits[0][hits[0].index(COUNTDOWN_MARKER):]


def _tool_tail(step: int):
    """A realistic assistant+tool tail (last role != user → append branch)."""
    return (
        Message(role="assistant", content=f"thinking {step}"),
        Message(role="tool", content=f"result {step}", tool_call_id=f"c{step}", name="web_search"),
    )


# ===========================================================================
# 1. Processor — counting, once-per-step, reset per task
# ===========================================================================


def test_counts_up_and_injects_exactly_once_per_step() -> None:
    proc = StepCountdownProcessor(max_steps=8)
    _run(proc, TaskStartEvent(run_id="r1", step_id=0))
    for k in range(5):
        _run(proc, StepStartEvent(run_id="r1", step_id=k, messages=_tool_tail(k)))
        bm = _run(proc, BeforeModelEvent(run_id="r1", step_id=k, messages=_tool_tail(k)))
        assert proc._count == k + 1  # 1-indexed per-task invocation count
        assert _markers(bm.messages) == 1  # exactly once, never accumulated
        assert f"step {k + 1} of 8 used" in _line(bm.messages)


def test_reset_on_task_end_and_on_new_run_id() -> None:
    proc = StepCountdownProcessor(max_steps=8)
    _run(proc, TaskStartEvent(run_id="r1", step_id=0))
    _run(proc, StepStartEvent(run_id="r1", step_id=0, messages=_tool_tail(0)))
    _run(proc, BeforeModelEvent(run_id="r1", step_id=0, messages=_tool_tail(0)))
    assert proc._count == 1

    # task_end zeroes the counter
    _run(proc, TaskEndEvent(run_id="r1", step_id=1))
    assert proc._count == 0

    # a step_start bearing a *different* run_id also resets (defensive path)
    _run(proc, BeforeModelEvent(run_id="r1", step_id=0, messages=_tool_tail(0)))
    assert proc._count == 1
    _run(proc, StepStartEvent(run_id="r2", step_id=0, messages=_tool_tail(0)))
    assert proc._count == 0
    bm = _run(proc, BeforeModelEvent(run_id="r2", step_id=0, messages=_tool_tail(0)))
    assert "step 1 of 8 used" in _line(bm.messages)


# ===========================================================================
# 2. Processor — N (max steps) source
# ===========================================================================


def test_auto_discovers_n_from_task_max_steps() -> None:
    proc = StepCountdownProcessor()  # unpinned → discover per task
    _run(proc, TaskStartEvent(run_id="r1", step_id=0))
    _run(proc, StepStartEvent(run_id="r1", step_id=0, task=_Task(12), messages=_tool_tail(0)))
    bm = _run(proc, BeforeModelEvent(run_id="r1", step_id=0, messages=_tool_tail(0)))
    assert "step 1 of 12 used" in _line(bm.messages)


def test_constructor_pin_overrides_task_budget() -> None:
    proc = StepCountdownProcessor(max_steps=30)  # pinned wins over task.max_steps
    _run(proc, TaskStartEvent(run_id="r1", step_id=0))
    _run(proc, StepStartEvent(run_id="r1", step_id=0, task=_Task(12), messages=_tool_tail(0)))
    bm = _run(proc, BeforeModelEvent(run_id="r1", step_id=0, messages=_tool_tail(0)))
    assert "of 30 used" in _line(bm.messages)
    assert "of 12" not in _line(bm.messages)


def test_no_injection_when_n_unknown() -> None:
    proc = StepCountdownProcessor()  # unpinned and task exposes no budget
    _run(proc, TaskStartEvent(run_id="r1", step_id=0))
    _run(proc, StepStartEvent(run_id="r1", step_id=0, task=None, messages=_tool_tail(0)))
    tail = _tool_tail(0)
    bm = _run(proc, BeforeModelEvent(run_id="r1", step_id=0, messages=tail))
    assert _markers(bm.messages) == 0  # fail-safe: no misleading count
    assert bm.messages == tail  # event passed through unchanged


# ===========================================================================
# 3. Processor — escalation threshold at N-2
# ===========================================================================


def test_escalation_begins_exactly_at_step_n_minus_2() -> None:
    n = 10
    proc = StepCountdownProcessor(max_steps=n)  # escalate_within=2 → threshold x>=n-2
    _run(proc, TaskStartEvent(run_id="r1", step_id=0))
    escalated: list[int] = []
    for k in range(n):
        _run(proc, StepStartEvent(run_id="r1", step_id=k, messages=_tool_tail(k)))
        bm = _run(proc, BeforeModelEvent(run_id="r1", step_id=k, messages=_tool_tail(k)))
        if "STOP researching NOW" in _line(bm.messages):
            escalated.append(proc._count)  # 1-indexed step number
    # "from step N-2 onward": exactly steps N-2, N-1, N escalate.
    assert escalated == [n - 2, n - 1, n]


def test_escalate_within_is_configurable() -> None:
    n = 10
    proc = StepCountdownProcessor(max_steps=n, escalate_within=1)  # only the last step
    _run(proc, TaskStartEvent(run_id="r1", step_id=0))
    escalated: list[int] = []
    for k in range(n):
        _run(proc, StepStartEvent(run_id="r1", step_id=k, messages=_tool_tail(k)))
        bm = _run(proc, BeforeModelEvent(run_id="r1", step_id=k, messages=_tool_tail(k)))
        if "STOP researching NOW" in _line(bm.messages):
            escalated.append(proc._count)
    assert escalated == [n - 1, n]


# ===========================================================================
# 4. Processor — injection seam (append vs edit) and non-accumulation
# ===========================================================================


def test_appends_user_message_when_tail_is_not_user() -> None:
    proc = StepCountdownProcessor(max_steps=8)
    _run(proc, TaskStartEvent(run_id="r1", step_id=0))
    _run(proc, StepStartEvent(run_id="r1", step_id=1, messages=_tool_tail(1)))
    tail = _tool_tail(1)
    bm = _run(proc, BeforeModelEvent(run_id="r1", step_id=1, messages=tail))
    assert len(bm.messages) == len(tail) + 1  # exactly +1 message
    assert bm.messages[-1].role == "user"
    assert COUNTDOWN_MARKER in bm.messages[-1].content


def test_edits_last_user_content_when_tail_is_user() -> None:
    proc = StepCountdownProcessor(max_steps=8)
    _run(proc, TaskStartEvent(run_id="r1", step_id=0))
    base = (Message(role="system", content="sys"), Message(role="user", content="the task?"))
    _run(proc, StepStartEvent(run_id="r1", step_id=0, messages=base))
    bm = _run(proc, BeforeModelEvent(run_id="r1", step_id=0, messages=base))
    assert len(bm.messages) == len(base)  # no new message; last-user content edited
    assert bm.messages[-1].role == "user"
    assert bm.messages[-1].content.startswith("the task?")
    assert COUNTDOWN_MARKER in bm.messages[-1].content
    assert bm.messages[:-1] == base[:-1]  # history window untouched (hook contract)


def test_replaced_not_accumulated_across_steps() -> None:
    """Injecting at before_model is ephemeral: the run loop rebuilds each step's
    context from state (which never carries the prior injection), so re-driving
    the same base yields exactly one fresh line — never a growing pile."""
    proc = StepCountdownProcessor(max_steps=8)
    _run(proc, TaskStartEvent(run_id="r1", step_id=0))
    base = _tool_tail(0)  # a fixed base, as state would re-present it each step
    for k in range(3):
        _run(proc, StepStartEvent(run_id="r1", step_id=k, messages=base))
        bm = _run(proc, BeforeModelEvent(run_id="r1", step_id=k, messages=base))
        assert _markers(bm.messages) == 1  # one line every step, not k+1
    assert _markers(base) == 0  # the caller's base was never mutated


# ===========================================================================
# 5. Recipe — flag-off config identity, flag-on append
# ===========================================================================


def _base_config() -> HarnessConfig:
    return HarnessConfig(
        processors=[
            {"_target_": "harnessx.processors.control.token_budget.TokenBudgetProcessor", "ratio": 0.8}
        ]
    )


def test_flag_off_is_config_identity() -> None:
    cfg = _base_config()
    out = rvp._maybe_add_step_countdown(cfg, "off")
    assert out is cfg  # same object → byte-identical deployed config
    assert out.to_yaml() == cfg.to_yaml()
    assert not any(
        str(p.get("_target_", "")).endswith("StepCountdownProcessor") for p in out.processors
    )


def test_flag_on_appends_processor_without_mutating_original() -> None:
    cfg = _base_config()
    out = rvp._maybe_add_step_countdown(cfg, "on")
    assert out is not cfg
    assert len(cfg.processors) == 1  # original untouched
    targets = [p.get("_target_") for p in out.processors]
    assert targets[-1].endswith("StepCountdownProcessor")
    assert targets[0].endswith("TokenBudgetProcessor")  # existing processors preserved, in order


def test_flag_on_rejects_unknown_mode() -> None:
    with pytest.raises(ValueError):
        rvp._maybe_add_step_countdown(_base_config(), "bogus")


# ===========================================================================
# 6. Recipe — flag-on config canonicalizes + serializes round-trip
# ===========================================================================


def test_flag_on_config_canonicalize_and_yaml_round_trip() -> None:
    on = rvp._maybe_add_step_countdown(_base_config(), "on")

    # canonicalize (meta-harness candidate validation path) keeps the processor
    canon = on.canonicalize()
    assert any(
        str(p.get("_target_", "")).endswith("StepCountdownProcessor") for p in canon.processors
    )

    # YAML round-trip: to_yaml -> from_yaml -> canonicalize still carries it once
    reloaded = HarnessConfig.from_yaml(on.to_yaml()).canonicalize()
    sc = [p for p in reloaded.processors if str(p.get("_target_", "")).endswith("StepCountdownProcessor")]
    assert len(sc) == 1

    # and the serialized descriptor actually re-instantiates (the repo's _target_
    # serializer knows it — no registry wiring needed)
    inst = _instantiate(sc[0])
    assert type(inst).__name__ == "StepCountdownProcessor"
    assert inst.max_steps is None and inst.escalate_within == 2


# ===========================================================================
# 7. Recipe — byte-safe lock / provenance record
# ===========================================================================


def test_provenance_off_none_on_warning() -> None:
    assert rvp._step_countdown_provenance("off") is None
    warn = rvp._step_countdown_provenance("on")
    assert warn is not None
    assert "step_countdown=on" in warn
    assert "byte-identical" in warn


class _LockArgs:
    """Minimal CLI namespace covering the fields ``_build_experiment_lock`` reads."""

    def __init__(self, **overrides) -> None:
        self.pool_k = 2
        self.pass_k = 2
        self.num_rounds = 1
        self.max_steps = 20
        self.concurrency = 2
        self.evolve_steps = 200
        self.patience = 1
        self.seed = 0
        self.estimator = "laplace"
        self.candidate_mode = "legacy_single"
        self.candidates_per_round = 4
        self.model = "task-model"
        self.meta_model = "meta-model"
        self.provider_id = "provider"
        self.api_base = None
        self.data_path = None
        self.planned_seeds = (0, 1, 2)
        self.step_countdown = "off"
        self.__dict__.update(overrides)


def _make_lock(tmp_path: Path, **overrides):
    baseline = tmp_path / "V0" / "config.yaml"
    baseline.parent.mkdir(parents=True, exist_ok=True)
    baseline.write_text("baseline: true\n", encoding="utf-8")
    fake_base = types.SimpleNamespace(processors=[], tool_registry=None)
    return rvp._build_experiment_lock(
        args=_LockArgs(**overrides),
        run_tag="test-step-countdown",
        baseline_config_path=baseline,
        original_base=fake_base,
    )


def test_lock_records_step_countdown_only_when_on(tmp_path) -> None:
    off_lock = _make_lock(tmp_path, step_countdown="off")
    assert not any("step_countdown" in w for w in off_lock.provenance_warnings)

    on_lock = _make_lock(tmp_path, step_countdown="on")
    sc = [w for w in on_lock.provenance_warnings if "step_countdown=on" in w]
    assert len(sc) == 1

    # the record survives the lock's own JSON round-trip
    from experiments.variant_pool.experiment_lock import ExperimentLock

    back = ExperimentLock.from_json(on_lock.to_json())
    assert any("step_countdown=on" in w for w in back.provenance_warnings)
