# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""Spawn: re-instantiate a child processor when deepcopy of the parent's bound
instance fails, instead of silently dropping it (found by the v6 full-chain smoke).

Background: at spawn time the parent Harness has already bound each processor to
its runtime (``_bind_harness_config`` / ``_bind_runtime``), so the instance now
reaches an unpicklable object transitively (a journal's ``TextIOWrapper``).  The
old code caught the ``copy.deepcopy`` failure, warned, and ran the child WITHOUT
the processor — a pipeline different from the configured one, silently.

The fix rebuilds a FRESH instance from the processor's serialized form (the same
``_serialize_processor`` → ``_instantiate_proc`` machinery the config layer uses),
which is exactly what the child would have constructed from config and cannot
carry the parent's runtime bindings.  When no honest serialized form exists
(runtime-only marker / locally-defined class), it still degrades — but loudly,
naming the class and stating that re-instantiation was impossible.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from harnessx import HarnessConfig, MultiHookProcessor
from harnessx.core.runtime import unwrap_runtime_proc
from harnessx.tools.spawn_subagent import _default_child_config
from harnessx.tracing.null_tracer import NullTracer
from harnessx.tools.inmemory import InMemoryToolRegistry


# ═══════════════════════════════════════════════════════════════════════════════
# Helpers (mirrors test_spawn_config_inheritance.py)
# ═══════════════════════════════════════════════════════════════════════════════


def _proc_instances(config, cls=None):
    all_instances = [unwrap_runtime_proc(p) for p in (config._rt_procs or ())]
    if cls is None:
        return all_instances
    return [p for p in all_instances if isinstance(p, cls)]


def _make_overrides() -> dict:
    return {"model": "", "system_prompt": "", "tools": []}


def _parent_config(**kwargs) -> HarnessConfig:
    defaults = dict(tool_registry=InMemoryToolRegistry(), tracer=NullTracer())
    defaults.update(kwargs)
    return HarnessConfig(**defaults)


def _child_of(parent) -> HarnessConfig:
    return _default_child_config(parent, _make_overrides(), child_depth=1, max_depth=3)


# ── Module-level processors (importable → have a serialized form) ────────────


class HeadProc(MultiHookProcessor):
    """Ordinary picklable processor — clones via deepcopy (position guard)."""

    async def on_task_start(self, event):
        yield event


class TailProc(MultiHookProcessor):
    """Ordinary picklable processor — clones via deepcopy (position guard)."""

    async def on_task_start(self, event):
        yield event


class ReinstConfigProc(MultiHookProcessor):
    """A config processor: constructor state is serializable, but an unpicklable
    attribute attached post-init defeats deepcopy (stands in for the bound
    journal reference the smoke hit)."""

    def __init__(self, tag: str = "cfg") -> None:
        super().__init__()
        self._tag = tag

    async def on_task_start(self, event):
        yield event


class ClonableStatefulProc(MultiHookProcessor):
    """Picklable processor carrying RUNTIME state (not a constructor param) — used
    to prove the happy path CLONES (state survives) rather than re-instantiates
    (state would reset to the constructor default)."""

    def __init__(self, tag: str = "s") -> None:
        super().__init__()
        self._tag = tag
        self._runtime_counter = 0  # never a constructor arg → reinit would zero it

    async def on_task_start(self, event):
        yield event


# ═══════════════════════════════════════════════════════════════════════════════
# Tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestSpawnReinstantiate:
    # ── 1. unpicklable config processor reaches the child as a FRESH instance ──
    def test_unpicklable_config_processor_reinstantiated_in_position(self, tmp_path):
        middle = ReinstConfigProc(tag="cfg")
        fh = open(tmp_path / "handle.txt", "w")  # unpicklable post-init attr
        middle._fh = fh
        head, tail = HeadProc(), TailProc()
        parent = _parent_config(processors=[head, middle, tail])

        try:
            # No warning should fire — the middle processor is re-instantiated.
            import warnings as _w

            with _w.catch_warnings():
                _w.simplefilter("error")  # any UserWarning would raise here
                child = _child_of(parent)
        finally:
            fh.close()

        procs = _proc_instances(child)
        # position preserved: head, re-instantiated middle, tail — not appended last
        assert [type(p).__name__ for p in procs] == ["HeadProc", "ReinstConfigProc", "TailProc"]

        child_middle = _proc_instances(child, ReinstConfigProc)[0]
        assert child_middle is not middle  # a fresh instance, not the parent's object
        assert child_middle._tag == "cfg"  # constructor state reconstructed
        assert not hasattr(child_middle, "_fh")  # the unpicklable post-init attr is gone

    # ── 2. runtime-only / unimportable processor still degrades, but loudly ────
    def test_runtime_only_processor_degrades_with_named_warning(self, tmp_path):
        # A locally-defined class (<locals> qualname): no target string can import
        # it, so there is genuinely no serialized form to rebuild from.
        class LocalRuntimeOnlyProc(MultiHookProcessor):
            async def on_task_start(self, event):
                yield event

        proc = LocalRuntimeOnlyProc()
        fh = open(tmp_path / "handle2.txt", "w")
        proc._fh = fh  # unpicklable → deepcopy fails
        parent = _parent_config(processors=[proc])

        try:
            with pytest.warns(UserWarning) as record:
                child = _child_of(parent)
        finally:
            fh.close()

        # degraded: the processor is absent from the child pipeline
        assert _proc_instances(child, LocalRuntimeOnlyProc) == []
        msgs = [str(w.message) for w in record]
        # names the class, states it ran WITHOUT it, and that re-instantiation failed
        assert any("LocalRuntimeOnlyProc" in m for m in msgs), msgs
        assert any("WITHOUT it" in m for m in msgs), msgs
        assert any("re-instantiate" in m or "serializable form" in m for m in msgs), msgs

    # ── 3. happy path: picklable processor is CLONED, not re-instantiated ──────
    def test_picklable_processor_is_cloned_not_reinstantiated(self):
        proc = ClonableStatefulProc(tag="s")
        proc._runtime_counter = 99  # runtime mutation deepcopy would carry
        parent = _parent_config(processors=[proc])

        child = _child_of(parent)

        child_procs = _proc_instances(child, ClonableStatefulProc)
        assert len(child_procs) == 1
        clone = child_procs[0]
        assert clone is not proc  # own copy (single-owner rule)
        # deepcopy carried the runtime counter; re-instantiation would have reset
        # it to the constructor default (0) — so 99 proves the clone path was taken.
        assert clone._runtime_counter == 99
