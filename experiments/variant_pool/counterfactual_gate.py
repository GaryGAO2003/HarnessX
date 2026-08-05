# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""Counterfactual replay gate — ported from the official AEGIS gate, then FIXED.

Samples K previously-passing tasks and replays the NEW candidate's processor
chain over their recorded events (no LLM, no tools, no network).  Fails the
candidate if any sampled task's ``final_output`` or ``exit_reason`` changes
relative to what the original passing run emitted.  This catches the class of
regression a smoke replay can't: "the new processor rewrites the output of a
task the old config already got right".

Provenance & the bug this fixes
-------------------------------
The official gate (``git show upstream/feat/aegis:harnessx/aegis/gates/
counterfactual.py``) is a **latent no-op**.  It dispatches on a ``kind`` field
and reads a flat ``final_output`` — a schema no producer in this repo emits (the
journal writes type-tagged ``raw_assistant`` / ``raw_tool`` / ``episode_end``
rows; the ``kind`` schema lives only in the gate's own test fixture).  Fed real
data it matched zero events and returned ``ok=True`` unconditionally.  Two more
weaknesses compounded it: zero coverage was indistinguishable from "no
regression", and a crashing processor was silently skipped (fail-open) with no
record.

The fix is three layers: (A) ``event_replay.py`` adapts our landed artifacts to
the gate's data contract; (B) **this** module ports the replay logic and hardens
it; (C) a one-line journal fix makes future ``episode_end`` rows carry
``final_output`` so no adapter inference is needed.

Deliberate divergences from the official gate
---------------------------------------------
1. **Zero-coverage fails closed.**  If the adapter yields 0 hook-dispatchable
   events for the sampled tasks, or 0 processors were instantiated from the
   candidate config, the gate returns NOT-ok with reason
   ``counterfactual: zero replay coverage (<detail>)``.  The official bug class
   (nothing replayed → silent pass) becomes a loud error.  Consequence: an
   *empty* processor chain (``processors: []``) — which the official
   ``test_gate_ok_for_identity_processor_chain`` accepted — is now rejected,
   because a chain that replays nothing cannot certify the absence of a
   regression.  Real candidates always declare processors; an empty chain is not
   a shippable candidate.

2. **Observability + honest exception handling.**  The result object carries
   ``events_dispatched`` / ``processors_replayed`` / ``tasks_replayed`` /
   ``swallowed_exceptions``.  The official bare ``try/except`` that skipped a
   crashing processor becomes: the exception is **always recorded**
   ``(processor, repr(exc))``; with ``strict=False`` (official-parity default)
   replay continues fail-open; with ``strict=True`` any replay/instantiation
   exception fails the gate.

3. **Real frozen events, captured yields.**  Our events are
   ``@dataclass(frozen=True)``; the official gate's ``SimpleNamespace`` +
   in-place mutation model cannot work here (a real processor yields a *new*
   event via ``dataclasses.replace``, it cannot assign ``event.final_output``).
   This port builds the real ``ModelResponseEvent`` / ``ToolResultEvent`` /
   ``TaskEndEvent`` and **captures the yielded event** (the codebase's own
   "last same-type yield" primary-diff convention), then compares the replayed
   terminal against the original.  Dispatch also routes through each processor's
   real ``_DISPATCH`` table (so ``@on``-decorated free-named handlers are found),
   improving on the official hardcoded method-name map.

Fidelity caveats
----------------
Replay is a lower-fidelity shadow of a live run.  The adapter carries only
``content`` / ``tool_calls`` / ``result`` / ``exit_reason`` / ``final_output``;
a processor that reads ``thinking`` / ``thinking_blocks`` / ``usage`` /
``content_blocks`` / per-step token counts sees dataclass defaults, so it
degrades or no-ops on replay (and may raise → recorded in
``swallowed_exceptions``).  Processors that depend on live binding
(``_harness_config`` / ``_runtime`` / tool registry) may also raise; those are
recorded, not fatal (unless ``strict``).  The gate is sound for its purpose —
catching a candidate processor that *rewrites* the terminal output — not a full
re-execution.

WIRING (deferred)
-----------------
This module intentionally wires **no** CLI flag — ``run_variant_pool.py`` is
owned by another change in flight.  Intended integration, to be added there:

* Flag: ``--counterfactual-gate`` (``action="store_true"``, **default off**),
  mirroring ``--ship-efficacy-gate``.
* Provenance: a ``_counterfactual_gate_provenance(args) -> str | None`` helper
  following the ``_epsilon_provenance`` / ``_ship_efficacy_provenance`` pattern
  (returns ``None`` when off so the experiment lock stays byte-identical;
  otherwise a lock record noting that enabling it changes which candidates
  apply/fork/reject and so is not byte-comparable with an "off" run), threaded
  into the same provenance list as ``_epsilon_provenance(...)``.
* Call site: pre-ship, alongside the deterministic *seesaw* stage — after the
  Evolver writes the candidate config and before a full candidate-evaluation
  batch is spent.  Build ``rows_for_task`` via
  ``event_replay.make_session_rows_loader(active_pool_sessions_dir,
  final_output_overrides=...)`` and pass the previous round's passing task ids.
  A NOT-ok result rejects the candidate with a ``counterfactual:`` reason
  before evaluation.
"""
from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import yaml

from harnessx.core.events import (
    Event,
    ModelResponseEvent,
    TaskEndEvent,
    ToolCall,
    ToolResultEvent,
)
from harnessx.core.processor import MultiHookProcessor

from . import event_replay


# gate ``kind`` → the real (frozen) event class it replays through.
_KIND_TO_EVENT = {
    "after_model": ModelResponseEvent,
    "after_tool": ToolResultEvent,
    "task_end": TaskEndEvent,
}


@dataclass
class CounterfactualResult:
    """Gate verdict + replay observability.

    ``ok`` / ``reason`` are duck-compatible with ``aegis.gates.structure.
    GateResult`` (which is not landed in this worktree), so this can be adapted
    to a ``GateResult`` at the wiring site once that package lands.
    """

    ok: bool
    reason: str = ""
    events_dispatched: int = 0
    processors_replayed: int = 0
    tasks_replayed: int = 0
    swallowed_exceptions: list[tuple[str, str]] = field(default_factory=list)
    regressions: list[str] = field(default_factory=list)


class _StrictReplayError(Exception):
    """Raised internally when ``strict=True`` and a processor/instantiation raises."""

    def __init__(self, where: str, exc: BaseException) -> None:
        super().__init__(f"{where}: {exc!r}")
        self.where = where
        self.exc = exc


def _to_tool_call(tc: Any) -> Any:
    """Coerce a ``{id,name,input}`` dict into a real ``ToolCall`` (best effort)."""
    if isinstance(tc, ToolCall):
        return tc
    if isinstance(tc, dict):
        return ToolCall(
            id=str(tc.get("id", "")),
            name=str(tc.get("name", "")),
            input=tc.get("input") or {},
        )
    return tc


def _build_event(row: dict, run_id: str) -> "Event | None":
    """Build the real frozen event for a gate ``kind`` row, or ``None``."""
    kind = row.get("kind")
    try:
        step = int(row.get("step", 0) or 0)
    except (TypeError, ValueError):
        step = 0

    if kind == "after_model":
        tool_calls = tuple(_to_tool_call(tc) for tc in (row.get("tool_calls") or ()))
        return ModelResponseEvent(
            run_id=run_id,
            step_id=step,
            content=row.get("content", "") or "",
            tool_calls=tool_calls,
        )
    if kind == "after_tool":
        return ToolResultEvent(
            run_id=run_id,
            step_id=step,
            tool_name=row.get("tool_name", "") or "",
            tool_call_id=row.get("tool_call_id", "") or "",
            result=row.get("result", "") or "",
        )
    if kind == "task_end":
        try:
            total_steps = int(row.get("total_steps", 0) or 0)
        except (TypeError, ValueError):
            total_steps = 0
        return TaskEndEvent(
            run_id=run_id,
            step_id=step,
            final_output=row.get("final_output", "") or "",
            exit_reason=row.get("exit_reason", "done") or "done",
            total_steps=total_steps,
        )
    return None


def _instantiate_processors(
    cfg: dict, *, strict: bool, swallowed: list[tuple[str, str]]
) -> list:
    """Instantiate the candidate config's ``processors:`` via OUR builder.

    Uses ``harnessx.core.builder._instantiate`` so every ``file://`` /
    ``::ClassName`` target resolves exactly as the real run resolves it (M-41).
    Instantiation failures are recorded in ``swallowed`` (and, when ``strict``,
    raised as ``_StrictReplayError``) instead of being silently dropped.
    """
    from harnessx.core.builder import _instantiate

    procs_raw = cfg.get("processors") or []
    out: list = []
    for entry in procs_raw:
        if not isinstance(entry, dict):
            continue
        if "_target_" not in entry and "type" not in entry:
            continue
        target = entry.get("_target_") or entry.get("type") or "<unknown>"
        try:
            obj = _instantiate(entry)
        except Exception as exc:  # noqa: BLE001 — recorded, optionally fatal
            swallowed.append((f"<instantiate {target}>", repr(exc)))
            if strict:
                raise _StrictReplayError(f"instantiate {target}", exc)
            continue
        if obj is not None:
            out.append(obj)
    return out


async def _dispatch_one(proc, event: Event) -> "tuple[list[Event] | None, BaseException | None]":
    """Dispatch ``event`` to a single processor.

    Returns ``(yielded_events, exc)``.  ``yielded_events is None`` means the
    processor has no handler for this event type (not dispatched).  Otherwise
    ``yielded_events`` is every event the handler yielded and ``exc`` is the
    exception it raised (or ``None``).

    MultiHookProcessors are dispatched through their resolved ``on_*`` hook
    directly (NOT via ``process()``) so exceptions surface here for accounting —
    ``process()`` would swallow them internally.  Bare ``Processor``-protocol
    objects are dispatched through ``process()``.
    """
    handler = None
    if isinstance(proc, MultiHookProcessor):
        method_name = type(proc)._DISPATCH.get(type(event))
        if method_name:
            handler = getattr(proc, method_name, None)
    else:
        handler = getattr(proc, "process", None)

    if handler is None:
        return None, None

    outs: list[Event] = []
    try:
        async for out in handler(event):
            outs.append(out)
    except asyncio.CancelledError:
        raise
    except BaseException as exc:  # noqa: BLE001 — recorded by caller
        return outs, exc
    return outs, None


async def _thread_event(
    event: Event,
    processors: list,
    *,
    strict: bool,
    swallowed: list[tuple[str, str]],
) -> Event:
    """Thread ``event`` through the processor chain, returning the final event.

    Mirrors ``pipe`` / ``_diff_primary``: after each processor the "primary"
    event is the last yielded event of the same type; that is fed to the next
    processor.  A processor that intercepts (yields nothing of that type) leaves
    the event unchanged for the rest of the chain.  Exceptions are recorded;
    under ``strict`` they abort via ``_StrictReplayError``.
    """
    current = event
    etype = type(event)
    for proc in processors:
        yields, exc = await _dispatch_one(proc, current)
        if yields is None:
            continue  # this processor does not handle this event type
        if exc is not None:
            pname = type(proc).__name__
            swallowed.append((pname, repr(exc)))
            if strict:
                raise _StrictReplayError(pname, exc)
            continue  # fail-open: event passes through unchanged
        same = [e for e in yields if isinstance(e, etype)]
        if same:
            current = same[-1]
    return current


async def check_counterfactual_replay(
    *,
    new_config_yaml_text: "str | None",
    passing_task_ids: "list[str]",
    rows_for_task: "Callable[[str], list[dict]] | None" = None,
    sessions_root: "Path | None" = None,
    final_output_overrides: "dict[str, str] | None" = None,
    k_samples: int = 3,
    rng_seed: "int | None" = 0,
    strict: bool = False,
) -> CounterfactualResult:
    """Replay the candidate's processor chain over K sampled passing tasks.

    Args:
        new_config_yaml_text: The candidate harness config YAML (with a
            ``processors:`` list).  ``None`` → skip (ok).
        passing_task_ids: Task ids the previous round already passed.  Empty →
            skip (ok).
        rows_for_task: ``task_id -> list[kind-row dict]`` seam.  Tests inject
            hand-built rows here; production passes the adapter loader.  If
            ``None``, one is built from ``sessions_root`` via
            :func:`event_replay.make_session_rows_loader`.
        sessions_root: ``sessions/`` dir used to build the default
            ``rows_for_task`` when it is not supplied.
        final_output_overrides: ``task_id -> exact final_output`` map forwarded
            to the default adapter loader.
        k_samples: Number of tasks to sample (deterministically) for replay.
        rng_seed: Seed for the sampling RNG (deterministic; no unseeded random).
        strict: ``False`` (default) = official-parity fail-open on replay
            exceptions; ``True`` = any replay/instantiation exception fails.

    Returns:
        :class:`CounterfactualResult` — ``ok`` plus replay observability.
    """
    # Legitimate skips (nothing to check) — distinct from zero-coverage.
    if new_config_yaml_text is None:
        return CounterfactualResult(ok=True, reason="skipped: no candidate cfg supplied")
    if not passing_task_ids:
        return CounterfactualResult(
            ok=True, reason="skipped: no previously-passing tasks to sample"
        )

    if rows_for_task is None:
        if sessions_root is None:
            return CounterfactualResult(
                ok=False,
                reason=(
                    "counterfactual: no trajectory source "
                    "(pass rows_for_task or sessions_root)"
                ),
            )
        rows_for_task = event_replay.make_session_rows_loader(
            sessions_root, final_output_overrides=final_output_overrides
        )

    try:
        cfg = yaml.safe_load(new_config_yaml_text) or {}
    except yaml.YAMLError as exc:
        return CounterfactualResult(
            ok=False, reason=f"counterfactual: new config YAML invalid: {exc!r}"
        )
    if not isinstance(cfg, dict):
        return CounterfactualResult(
            ok=False, reason="counterfactual: new config YAML is not a mapping"
        )

    swallowed: list[tuple[str, str]] = []
    try:
        processors = _instantiate_processors(cfg, strict=strict, swallowed=swallowed)
    except _StrictReplayError as sre:
        return CounterfactualResult(
            ok=False,
            reason=f"counterfactual: strict replay exception ({sre.where}): {sre.exc!r}",
            swallowed_exceptions=swallowed,
        )

    rng = random.Random(rng_seed)
    sampled = (
        list(passing_task_ids)
        if len(passing_task_ids) <= k_samples
        else rng.sample(list(passing_task_ids), k_samples)
    )

    events_dispatched = 0
    tasks_replayed = 0
    regressions: list[str] = []

    try:
        for tid in sampled:
            try:
                rows = rows_for_task(tid) or []
            except Exception as exc:  # noqa: BLE001 — a loader failure is coverage loss
                swallowed.append((f"<load {tid}>", repr(exc)))
                if strict:
                    raise _StrictReplayError(f"load {tid}", exc)
                continue

            run_id = f"replay-{tid}"
            task_had_event = False
            for row in rows:
                if row.get("kind") not in _KIND_TO_EVENT:
                    continue
                ev = _build_event(row, run_id)
                if ev is None:
                    continue
                events_dispatched += 1
                task_had_event = True
                ev_after = await _thread_event(
                    ev, processors, strict=strict, swallowed=swallowed
                )
                if isinstance(ev, TaskEndEvent):
                    if ev_after.final_output != ev.final_output:
                        regressions.append(
                            f"{tid}: final_output changed "
                            f"{ev.final_output!r} -> {ev_after.final_output!r}"
                        )
                    if ev_after.exit_reason != ev.exit_reason:
                        regressions.append(
                            f"{tid}: exit_reason changed "
                            f"{ev.exit_reason!r} -> {ev_after.exit_reason!r}"
                        )
            if task_had_event:
                tasks_replayed += 1
    except _StrictReplayError as sre:
        return CounterfactualResult(
            ok=False,
            reason=f"counterfactual: strict replay exception ({sre.where}): {sre.exc!r}",
            events_dispatched=events_dispatched,
            processors_replayed=len(processors),
            tasks_replayed=tasks_replayed,
            swallowed_exceptions=swallowed,
        )

    processors_replayed = len(processors)

    # Divergence #1 — zero coverage fails closed (the official latent no-op).
    if events_dispatched == 0 or processors_replayed == 0:
        detail_parts = []
        if processors_replayed == 0:
            detail_parts.append("0 processors instantiated from candidate config")
        if events_dispatched == 0:
            detail_parts.append(
                f"0 hook-dispatchable events across {len(sampled)} sampled task(s)"
            )
        return CounterfactualResult(
            ok=False,
            reason=f"counterfactual: zero replay coverage ({'; '.join(detail_parts)})",
            events_dispatched=events_dispatched,
            processors_replayed=processors_replayed,
            tasks_replayed=tasks_replayed,
            swallowed_exceptions=swallowed,
        )

    if regressions:
        return CounterfactualResult(
            ok=False,
            reason=(
                "counterfactual replay flagged regressions: "
                + "; ".join(regressions[:5])
            ),
            events_dispatched=events_dispatched,
            processors_replayed=processors_replayed,
            tasks_replayed=tasks_replayed,
            swallowed_exceptions=swallowed,
            regressions=regressions,
        )

    return CounterfactualResult(
        ok=True,
        reason="counterfactual: no terminal-output regression detected",
        events_dispatched=events_dispatched,
        processors_replayed=processors_replayed,
        tasks_replayed=tasks_replayed,
        swallowed_exceptions=swallowed,
    )
