# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""v6 M3: data-plane attribution — slot writes/reads carry their acting processor.

Covers the current-actor context (set/reset around each ProcessorChain call),
write/read/delete provenance recorded by the State slot API, the UNGRAPHED and
no-actor cases, exception-path non-leakage, the anti-bypass guard, and the
unchanged behaviour of the per-step slot diff.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

import harnessx
from harnessx.core.attribution import (
    current_actor,
    install_actor_resolver,
    reset_actor_resolver,
)
from harnessx.core.processor import ProcessorChain
from harnessx.core.state import SlotAccess, State
from harnessx.core.trajectory import FullStateSnapshot
from harnessx.graph.executor import UNGRAPHED


# ── helper processors (call the captured State's slot API, then pass through) ──


class _Writer:
    def __init__(self, state, key, value):
        self._state, self._key, self._value = state, key, value

    async def process(self, event):
        self._state.set_slot(self._key, "test", self._value)
        yield event


class _Reader:
    def __init__(self, state, key):
        self._state, self._key = state, key

    async def process(self, event):
        self._state.get_slot(self._key)
        yield event


class _Deleter:
    def __init__(self, state, key):
        self._state, self._key = state, key

    async def process(self, event):
        self._state.delete_slot(self._key)
        yield event


class _Raiser:
    async def process(self, event):
        raise RuntimeError("boom")
        yield event  # pragma: no cover — makes this an async generator


def _install(binding):
    """Install a resolver from ``binding`` and return the reset token."""
    return install_actor_resolver(binding)


async def _run(chain_procs, event=None):
    chain = ProcessorChain(*chain_procs)
    out = None
    async for out in chain.process(event if event is not None else object()):
        pass
    return out


# ── 1. write inside a processor is attributed; write outside is a distinct None ──


async def test_write_inside_processor_records_that_processor():
    state = State(run_id="r1")
    state.step = 4
    w = _Writer(state, "k", 1)
    token = _install([("node.writer", "step_end", w)])
    try:
        await _run([w])
    finally:
        reset_actor_resolver(token)

    # actor AND the step it happened in are both recorded.
    assert state.slot_provenance["k"].writers == [SlotAccess("node.writer", 4)]


async def test_write_outside_any_processor_records_absence_as_none():
    state = State(run_id="r1")
    token = _install([])  # resolver present but no actor is executing
    try:
        state.set_slot("k", "test", 1)
    finally:
        reset_actor_resolver(token)

    # Absence of an actor is recorded as None — NOT a fabricated / ungraphed id.
    assert state.slot_provenance["k"].writers == [SlotAccess(None, 0)]
    assert None is not UNGRAPHED


async def test_writes_in_different_steps_are_distinguishable():
    state = State(run_id="r1")
    w = _Writer(state, "k", 1)
    token = _install([("node.writer", "step_end", w)])
    try:
        state.step = 1
        await _run([w])
        state.step = 3
        await _run([w])
    finally:
        reset_actor_resolver(token)

    # Same actor, two rounds → two distinct facts carrying their step.
    assert state.slot_provenance["k"].writers == [
        SlotAccess("node.writer", 1),
        SlotAccess("node.writer", 3),
    ]


# ── 2. two processors writing the same slot in one step stay distinct ──────────


async def test_two_writers_same_slot_both_recorded_in_order():
    state = State(run_id="r1")
    w1 = _Writer(state, "k", 1)
    w2 = _Writer(state, "k", 2)
    token = _install([("node.w1", "step_end", w1), ("node.w2", "step_end", w2)])
    try:
        await _run([w1, w2])  # one chain == one step
    finally:
        reset_actor_resolver(token)

    # Both writers preserved in order — the last writer is not the only record.
    assert state.slot_provenance["k"].writers == [
        SlotAccess("node.w1", 0),
        SlotAccess("node.w2", 0),
    ]
    # And the slot value is the last write (existing semantics unchanged).
    assert state.get_slot("k").content == 2


# ── 3. a read is traced to the reading processor ───────────────────────────────


async def test_read_is_traced_to_reading_processor():
    state = State(run_id="r1")
    state.set_slot("k", "test", 1)  # seed (attributed to None; outside a processor)
    r = _Reader(state, "k")
    token = _install([("node.reader", "before_model", r)])
    try:
        await _run([r])
    finally:
        reset_actor_resolver(token)

    assert SlotAccess("node.reader", 0) in state.slot_provenance["k"].readers


async def test_reader_is_deduplicated_within_a_step():
    state = State(run_id="r1")
    r = _Reader(state, "k")
    token = _install([("node.reader", "before_model", r)])
    try:
        await _run([r])
        await _run([r])  # same reader, SAME step (state.step unchanged)
    finally:
        reset_actor_resolver(token)

    # Repeat read in the same round adds nothing.
    assert state.slot_provenance["k"].readers == [SlotAccess("node.reader", 0)]


async def test_reader_across_steps_is_two_facts():
    state = State(run_id="r1")
    r = _Reader(state, "k")
    token = _install([("node.reader", "before_model", r)])
    try:
        state.step = 2
        await _run([r])
        state.step = 5
        await _run([r])
    finally:
        reset_actor_resolver(token)

    # Same reader, two rounds → two data-dependency facts, not one.
    assert state.slot_provenance["k"].readers == [
        SlotAccess("node.reader", 2),
        SlotAccess("node.reader", 5),
    ]


# ── 4. the actor context does not leak across an exception ─────────────────────


async def test_actor_does_not_leak_after_processor_raises():
    state = State(run_id="r1")
    raiser = _Raiser()
    token = _install([("node.raiser", "step_end", raiser)])
    try:
        with pytest.raises(RuntimeError, match="boom"):
            await _run([raiser])
        # The finally in ProcessorChain must have reset the actor.
        assert current_actor() is None
        # A write now, outside any processor, is attributed to None — NOT to the
        # processor that just raised.
        state.set_slot("after", "test", 1)
        assert state.slot_provenance["after"].writers == [SlotAccess(None, 0)]
    finally:
        reset_actor_resolver(token)


async def test_actor_is_set_only_within_the_processor_call():
    seen = []

    class _Probe:
        async def process(self, event):
            seen.append(current_actor())
            yield event

    p = _Probe()
    token = _install([("node.probe", "step_end", p)])
    try:
        assert current_actor() is None  # before
        await _run([p])
        assert current_actor() is None  # after
    finally:
        reset_actor_resolver(token)

    assert seen == ["node.probe"]  # during


# ── 5. UNGRAPHED processors attribute as ungraphed (not blank, not fabricated) ──


async def test_ungraphed_processor_attributes_as_ungraphed():
    state = State(run_id="r1")
    w = _Writer(state, "k", 1)
    # extra_processors / dict-plugins land in the binding under the UNGRAPHED
    # marker; the resolver must carry that through verbatim.
    token = _install([(UNGRAPHED, "step_end", w)])
    try:
        await _run([w])
    finally:
        reset_actor_resolver(token)

    writer = state.slot_provenance["k"].writers[0]
    assert writer.actor is UNGRAPHED
    assert writer.actor is not None


async def test_processor_absent_from_binding_is_ungraphed_not_fabricated():
    state = State(run_id="r1")
    w = _Writer(state, "k", 1)
    # A processor the binding never names (empty map) resolves as ungraphed.
    token = _install([])
    try:
        await _run([w])
    finally:
        reset_actor_resolver(token)

    assert state.slot_provenance["k"].writers[0].actor is UNGRAPHED


async def test_delete_records_the_deleting_actor():
    state = State(run_id="r1")
    state.set_slot("k", "test", 1)
    d = _Deleter(state, "k")
    token = _install([("node.deleter", "task_end", d)])
    try:
        await _run([d])
    finally:
        reset_actor_resolver(token)

    assert state.slot_provenance["k"].deleters == [SlotAccess("node.deleter", 0)]
    assert state.get_slot("k") is None  # existing delete semantics unchanged


# ── 6. anti-bypass: no direct state.slots mutation survives outside state.py ────


def test_no_direct_slot_mutation_outside_state_py():
    """Provenance is only sound if every slot mutation goes through the API.

    Guard the whole ``harnessx`` package against ``state.slots`` being mutated
    directly (index-assign, ``del``, in-place methods, or whole-dict rebind) —
    the only sanctioned mutation site is ``core/state.py`` itself.
    """
    root = Path(harnessx.__file__).resolve().parent
    state_py = (root / "core" / "state.py").resolve()

    patterns = [
        re.compile(r"\.slots\[[^\]]*\]\s*="),  # state.slots[k] = ...
        re.compile(r"\bdel\s+[\w.]+\.slots\["),  # del state.slots[k]
        re.compile(r"\.slots\.(pop|clear|update|setdefault)\("),  # in-place mutators
        re.compile(r"\.slots\s*=(?!=)"),  # state.slots = {...}
    ]

    offenders = []
    for py in root.rglob("*.py"):
        if py.resolve() == state_py:
            continue
        text = py.read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), start=1):
            for pat in patterns:
                if pat.search(line):
                    offenders.append(f"{py.relative_to(root)}:{lineno}: {line.strip()}")

    assert not offenders, "direct state.slots mutation outside state.py:\n" + "\n".join(offenders)


# ── 7. existing per-step slot diff is unchanged by the added provenance ─────────


def test_slot_diff_still_detects_create_update_delete():
    state = State(run_id="r1")
    state.set_slot("keep", "test", "v0")
    state.set_slot("gone", "test", "x")

    before = FullStateSnapshot.from_state(state, step_id=0)

    state.set_slot("keep", "test", "v1")  # update
    state.set_slot("new", "test", "y")  # create
    state.delete_slot("gone")  # delete

    delta = before.diff(state)
    assert {op.key for op in delta.created()} == {"new"}
    assert {op.key for op in delta.updated()} == {"keep"}
    assert {op.key for op in delta.deleted()} == {"gone"}


def test_provenance_is_not_in_the_snapshot_shape():
    """Provenance is additive record-keeping — snapshot()/wake() shape is untouched."""
    state = State(run_id="r1")
    state.set_slot("k", "test", 1)  # populates slot_provenance
    snap = state.snapshot()
    assert "slot_provenance" not in snap
    assert set(snap["slots"].keys()) == {"k"}
