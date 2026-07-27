# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
from __future__ import annotations

import dataclasses

from ...core.events import (
    BeforeModelEvent,
    Message,
    StepStartEvent,
    TaskEndEvent,
    TaskStartEvent,
)
from ...core.processor import MultiHookProcessor

#: Stable marker prefixing every injected line — lets the model (and tests)
#: recognise the countdown unambiguously and keeps it to a single refreshed line.
COUNTDOWN_MARKER = "[step-countdown]"

_COUNTDOWN_TEMPLATE = (
    "[step-countdown] step {x} of {n} used; you MUST output your FINAL ANSWER before step {n}."
)

_ESCALATION_TEMPLATE = (
    "[step-countdown] step {x} of {n} used — STOP researching NOW: output your FINAL "
    "ANSWER with your current best answer this turn."
)


class StepCountdownProcessor(MultiHookProcessor):
    """Inject a refreshed, model-visible step-countdown line before each model call.

    Mirrors what ``smolagents`` renders every turn — the remaining-step count —
    so the worker can honour the guarded prompt's "output FINAL ANSWER before
    step N" rule instead of dying budget-exceeded with no answer over a long
    trajectory (RUN-LOG F2: 20/21 budget-exceeded rollouts produced no answer
    because the worker could not self-count steps).

    Injection seam — ``on_before_model``
        A single ``user`` message carrying the countdown line is added to the
        model-input messages (the same seam :class:`EnvironmentContextInjector`
        uses for its TimeBudget warning). The RunLoop calls the model with these
        messages but only persists the assistant response and tool results back
        to ``state``; the injected line is therefore **ephemeral** — freshly
        recomputed and *replaced, not accumulated* every step — and the model
        demonstrably sees it because it is in the messages handed to
        ``provider.complete()``. When the last message is a plain-text ``user``
        turn the line is appended to its content (hook contract: only the last
        user's content may change); otherwise a fresh ``user`` message is
        appended (hook contract: +1 ``user`` allowed when the tail is not user).

    N (max steps)
        Read per task at ``on_step_start`` from ``event.task.max_steps`` — the
        run-loop's own budget source (``State.max_steps`` is copied from it and
        ``state.budget_exceeded()`` halts the loop at ``step >= max_steps``).
        ``BeforeModelEvent`` carries no task, so N is cached at ``step_start``
        (which always fires immediately before ``before_model``). Pass
        ``max_steps=`` to pin N explicitly instead of auto-discovering it.

    Step counter
        The processor's own per-task invocation count — 1-indexed, incremented
        once per model call and reset on every task boundary
        (``on_task_start`` / ``on_task_end`` / a changed ``run_id`` at
        ``on_step_start``), following :class:`LoopDetectionProcessor`'s
        per-task reset idiom.

    Escalation
        From step ``N - escalate_within`` onward (default the trailing steps
        beginning at N-2) the line switches to a STOP-now directive while still
        reporting the step count.

    Args:
        max_steps:       Pin N explicitly. ``None`` (default) auto-discovers N
                         per task from ``StepStartEvent.task.max_steps``. When N
                         is unknown (no pin, no task budget) nothing is injected.
        escalate_within: Number of trailing steps that emit the escalation line
                         (default 2 → escalation begins at step ``N-2``).
    """

    _singleton_group = "step_countdown"
    _order = 40  # after the token_budget (10) / loop_detection (20) guards; a soft nudge

    def __init__(self, max_steps: int | None = None, escalate_within: int = 2):
        self.max_steps = max_steps
        self.escalate_within = escalate_within
        # Effective N in force this task: the pin when provided, else discovered.
        self._n: int | None = max_steps
        self._count: int = 0
        self._current_run_id: str = ""

    # ------------------------------------------------------------------
    # Per-task reset (LoopDetectionProcessor idiom)
    # ------------------------------------------------------------------

    def _reset(self) -> None:
        self._count = 0
        # A constructor-pinned N survives reset; an auto-discovered N is dropped
        # so the next task re-reads its own budget.
        self._n = self.max_steps

    async def on_task_start(self, event: TaskStartEvent):
        self._reset()
        self._current_run_id = event.run_id
        yield event

    async def on_step_start(self, event: StepStartEvent):
        if event.run_id != self._current_run_id:
            self._reset()
            self._current_run_id = event.run_id
        # Real runtime seam for N: the task carries the per-task step budget the
        # run loop enforces. Only auto-discover when N was not pinned.
        if self.max_steps is None:
            task = getattr(event, "task", None)
            n = getattr(task, "max_steps", None)
            if isinstance(n, int) and n > 0:
                self._n = n
        yield event

    async def on_before_model(self, event: BeforeModelEvent):
        self._count += 1
        line = self._render_line()
        if line is None:
            yield event
            return

        msgs = event.messages
        if msgs and msgs[-1].role == "user" and isinstance(msgs[-1].content, str):
            # Tail is a plain-text user turn (e.g. the task question, or a runloop
            # continuation nudge) → edit its content. History window is untouched,
            # and because the whole edit is ephemeral it does not accumulate.
            last = msgs[-1]
            merged = f"{last.content}\n\n{line}" if last.content else line
            new_last = dataclasses.replace(last, content=merged)
            yield dataclasses.replace(event, messages=msgs[:-1] + (new_last,))
        else:
            # Tail is a tool/assistant turn (the common agentic case) → append
            # exactly one fresh user message with the countdown line.
            yield dataclasses.replace(event, messages=msgs + (Message(role="user", content=line),))

    async def on_task_end(self, event: TaskEndEvent):
        self._reset()
        yield event

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------

    def _render_line(self) -> "str | None":
        """The single countdown line for the current step, or ``None`` when N is
        unknown (fail-safe: never inject a misleading count)."""
        n = self._n
        if not isinstance(n, int) or n <= 0:
            return None
        x = self._count
        if x >= n - self.escalate_within:
            return _ESCALATION_TEMPLATE.format(x=x, n=n)
        return _COUNTDOWN_TEMPLATE.format(x=x, n=n)
