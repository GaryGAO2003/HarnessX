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
