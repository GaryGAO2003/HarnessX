# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""Data-plane attribution (v6 M3).

Slot writes and reads become attributable to the processor performing them.
The runloop dispatches processors through :class:`~harnessx.core.processor.ProcessorChain`,
which sets the *current actor* around each ``processor.process(...)`` call; the
State slot API (:mod:`harnessx.core.state`) reads that current actor when it
records a slot write / read / delete.

The actor is the processor's **graph node id** where one exists — resolved via
the runtime's ``(node_id, bucket, proc)`` binding
(``_HarnessRuntime.proc_node_binding``), the SAME binding the graph executor
consumes (v6 M2b).  No second id authority is introduced: node ids flow in
through that binding, and processors with no graph node (``extra_processors``,
dict-plugins) already carry the :data:`~harnessx.graph.executor.UNGRAPHED`
marker in it.  Anything the binding cannot name — a processor absent from the
map, or a run with no binding installed — is recorded as ``UNGRAPHED`` too,
never a fabricated id and never blank.

Recording is free when unused: with nothing installed, :func:`current_actor`
returns ``None`` (no processor is acting), so the legacy path — and any caller
that dispatches processors without a harness — keeps working unchanged.
"""

from __future__ import annotations

import contextvars

# id(proc) -> actor (a graph node id, or the UNGRAPHED marker).  Installed per
# run by the harness from proc_node_binding.  ``None`` = no binding installed.
_actor_resolver: contextvars.ContextVar = contextvars.ContextVar("ghx_actor_resolver", default=None)

# The resolved identity of the processor currently executing, or ``None`` when
# no processor is on the stack.  Set/reset around each ProcessorChain call.
_current_actor: contextvars.ContextVar = contextvars.ContextVar("ghx_current_actor", default=None)

# v6 M4: the unfolded-graph recorder for this run, or ``None`` when U is not being
# recorded (the default — recording is opt-in and free when unused).  Duck-typed
# (record_invocation / log_slot_access) so core never hard-imports the graph pkg.
_unfold_recorder: contextvars.ContextVar = contextvars.ContextVar("ghx_unfold_recorder", default=None)

# The unfolded id of the invocation currently executing, or ``None`` outside any
# recorded invocation.  Set/reset alongside ``_current_actor`` so slot accesses a
# processor performs are attributable to its exact invocation (not just its node).
_current_invocation: contextvars.ContextVar = contextvars.ContextVar("ghx_current_invocation", default=None)

# Cache the UNGRAPHED singleton (imported lazily to avoid a graph<->core import
# cycle at module load).  Both the binding and this fallback reference the ONE
# marker object, so every ungraphed actor compares ``is UNGRAPHED``.
_UNGRAPHED_CACHE: list = []


def _ungraphed():
    if not _UNGRAPHED_CACHE:
        from ..graph.executor import UNGRAPHED

        _UNGRAPHED_CACHE.append(UNGRAPHED)
    return _UNGRAPHED_CACHE[0]


def install_actor_resolver(binding):
    """Install an ``id(proc) -> actor`` map from a ``(node_id, bucket, proc)`` binding.

    Returns a token for :func:`reset_actor_resolver`.  ``binding`` may be
    ``None`` (legacy callers); the map is then empty and every acting processor
    resolves as ungraphed.
    """
    mapping: dict = {}
    if binding is not None:
        for node_id, _bucket, proc in binding:
            mapping[id(proc)] = node_id
    return _actor_resolver.set(mapping)


def reset_actor_resolver(token) -> None:
    _actor_resolver.reset(token)


def _resolve(processor):
    mapping = _actor_resolver.get()
    if not mapping:
        return _ungraphed()
    return mapping.get(id(processor), _ungraphed())


def enter_actor(processor):
    """Mark ``processor`` as the current actor; returns a token for :func:`exit_actor`."""
    return _current_actor.set(_resolve(processor))


def exit_actor(token) -> None:
    _current_actor.reset(token)


def current_actor():
    """Actor of the processor currently executing, or ``None`` outside any processor."""
    return _current_actor.get()


# ── v6 M4: unfolded-graph recording ─────────────────────────────────────────
#
# ``ProcessorChain.process`` (the sole processor-invocation funnel, where the
# actor context above is already established) drives these; ``State`` reports
# slot accesses through :func:`note_slot_access`.  All three no-op when no
# recorder is installed, so the legacy path pays only one context-var read.


def install_unfold_recorder(recorder):
    """Install the per-run unfolded-graph recorder; returns a reset token."""
    return _unfold_recorder.set(recorder)


def reset_unfold_recorder(token) -> None:
    _unfold_recorder.reset(token)


def current_unfold_recorder():
    """The active unfolded-graph recorder, or ``None`` when U is not being recorded."""
    return _unfold_recorder.get()


def enter_invocation(processor, hook: str, step: int, prev_in_firing):
    """Record ``processor``'s invocation and mark it current; returns ``(id, token)``.

    ``prev_in_firing`` is the previous invocation's id within the same hook firing
    (``None`` for the first).  Returns ``(None, None)`` when no recorder is active.
    The caller passes the token to :func:`exit_invocation` in a ``finally``.
    """
    recorder = _unfold_recorder.get()
    if recorder is None:
        return None, None
    inv_id = recorder.record_invocation(_current_actor.get(), processor, hook, step, prev_in_firing)
    token = _current_invocation.set(inv_id)
    return inv_id, token


def exit_invocation(token) -> None:
    if token is not None:
        _current_invocation.reset(token)


def current_invocation():
    """Unfolded id of the invocation currently executing, or ``None`` outside one."""
    return _current_invocation.get()


def enter_tool_invocation(tool_name: str, step: int, prev_in_firing):
    """Record a tool-execution invocation and mark it current; returns ``(id, token)``.

    The runloop's tool site is not a processor dispatch, so a tool has no actor to
    resolve — the recorder mints the node from ``tool_name`` instead.  Symmetric
    with :func:`enter_invocation`: ``prev_in_firing`` is the last ``before_tool``
    invocation's id (``None`` when none ran), the returned token goes to
    :func:`exit_invocation` in a ``finally``, and ``(None, None)`` comes back when
    no recorder is active (U off — the runloop pays nothing).  Marking the tool
    current lets slot accesses it performs, and any ``spawn_subagent`` it drives,
    attribute to this exact invocation.
    """
    recorder = _unfold_recorder.get()
    if recorder is None:
        return None, None
    inv_id = recorder.record_tool_invocation(tool_name, step, prev_in_firing)
    token = _current_invocation.set(inv_id)
    return inv_id, token


def note_slot_access(slot_key: str, kind: str, step: int) -> None:
    """Report a slot access (from ``State``) to the active recorder, if any."""
    recorder = _unfold_recorder.get()
    if recorder is None:
        return
    inv_id = _current_invocation.get()
    if inv_id is None:
        return  # access outside any recorded invocation — no invocation to attribute
    recorder.log_slot_access(slot_key, kind, inv_id, step)
