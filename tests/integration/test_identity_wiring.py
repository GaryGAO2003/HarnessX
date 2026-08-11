# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""v6 M9 — run identity wired to its three moments, end to end.

These run real harnesses with ``HARNESSX_GHX_IDENTITY`` on, read the persisted
identity JSON back, and pin: it is free/off by default; genotype + deployment are
recorded and match a fresh ``to_graph`` of the config; the phenotype is a real
hash equal to an independent projection of the persisted U when unfold is on, and
is recorded as absent-with-reason when unfold is off; the digests are stable
across a re-run of the same config.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from fixtures.mock_provider import MockProvider  # noqa: E402
from fixtures.mock_tools import add_tool, make_registry  # noqa: E402

from harnessx import BaseTask, HarnessConfig, ModelConfig, MultiHookProcessor  # noqa: E402
from harnessx.graph.identity import deployment_hash, genotype_hash, phenotype_hash  # noqa: E402
from harnessx.graph.identity_record import (  # noqa: E402
    identity_path,
    load_identity,
    project_observed_edges,
)
from harnessx.graph.snapshot import to_graph  # noqa: E402
from harnessx.graph.unfold import load_unfolded, unfolded_path  # noqa: E402
from harnessx.tracing.journal import HarnessJournal  # noqa: E402


def _two_tool_turn(tag):
    return {
        "content": tag,
        "tool_calls": [
            {"id": f"{tag}1", "name": "add", "input": {"a": 1, "b": 1}},
            {"id": f"{tag}2", "name": "add", "input": {"a": 2, "b": 2}},
        ],
    }


_TWO_TOOLS_THEN_DONE = [_two_tool_turn("c"), "done"]


class MultiHookSlotIO(MultiHookProcessor):
    """Writes a slot on step_start, reads it on step_end → a cross-firing data edge."""

    def __init__(self, key="flow"):
        super().__init__()
        self._key = key
        self._state = None

    async def on_task_start(self, event):
        self._state = event.state
        yield event

    async def on_step_start(self, event):
        if self._state is not None:
            self._state.set_slot(self._key, "u_test", event.step_id)
        yield event

    async def on_step_end(self, event):
        if self._state is not None:
            self._state.get_slot(self._key)
        yield event


class WriterTS(MultiHookProcessor):
    """Writes a slot within the task_start firing (same-firing data edge source)."""

    def __init__(self, key="shared"):
        super().__init__()
        self._key = key

    async def on_task_start(self, event):
        event.state.set_slot(self._key, "u_test", 42)
        yield event


class ReaderTS(MultiHookProcessor):
    """Reads a slot within the task_start firing (same-firing data edge target)."""

    def __init__(self, key="shared"):
        super().__init__()
        self._key = key

    async def on_task_start(self, event):
        event.state.get_slot(self._key)
        yield event


def _make(tmp_path, session_id, processors, responses, tools=None):
    journal = HarnessJournal(base_dir=str(tmp_path), export_jsonl=True, session_id=session_id, silent=True)
    config = HarnessConfig(
        tool_registry=make_registry(*(tools or [])),
        tracer=journal,
        processors=processors,
    )
    mc = ModelConfig(main=MockProvider(responses=responses))
    return config, mc.agentic(config)


# ── off by default: no file, no cost ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_identity_off_by_default(tmp_path, monkeypatch):
    monkeypatch.delenv("HARNESSX_GHX_IDENTITY", raising=False)
    _cfg, harness = _make(tmp_path, "off", [MultiHookSlotIO()], ["done"])
    result = await harness.run(BaseTask(description="q"))
    assert not identity_path(str(tmp_path), "off", result.run_id).exists()


# ── identity on, unfold on: all three recorded, phenotype is a real projection ─


@pytest.mark.asyncio
async def test_all_three_recorded_with_unfold(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESSX_GHX_IDENTITY", "1")
    monkeypatch.setenv("HARNESSX_GHX_UNFOLD", "1")
    cfg, harness = _make(
        tmp_path, "on", [WriterTS(), ReaderTS(), MultiHookSlotIO()], _TWO_TOOLS_THEN_DONE, tools=[add_tool]
    )
    result = await harness.run(BaseTask(description="q", max_steps=10))

    ipath = identity_path(str(tmp_path), "on", result.run_id)
    assert ipath.exists(), f"expected identity at {ipath}"
    ident = load_identity(ipath)

    # genotype + deployment match a fresh graph of the config (moments 1 & 2)
    snap = to_graph(cfg)
    assert ident.genotype == genotype_hash(snap)
    assert ident.deployment == deployment_hash(snap)

    # phenotype is a real hash (not absent) and equals an INDEPENDENT projection
    # of the persisted U onto a fresh snapshot (moment 3).
    assert ident.phenotype is not None
    assert ident.phenotype_absent_reason is None
    u = load_unfolded(unfolded_path(str(tmp_path), "on", result.run_id))
    snap2 = to_graph(cfg)
    project_observed_edges(snap2, u)
    assert ident.phenotype == phenotype_hash(snap2)
    assert ident.projected_edge_count >= 1, "this run should have observed at least one data edge"


# ── identity on, unfold off: phenotype absent-with-reason, not a hash ─────────


@pytest.mark.asyncio
async def test_phenotype_absent_when_unfold_off(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESSX_GHX_IDENTITY", "1")
    monkeypatch.delenv("HARNESSX_GHX_UNFOLD", raising=False)
    cfg, harness = _make(
        tmp_path, "noU", [WriterTS(), ReaderTS(), MultiHookSlotIO()], _TWO_TOOLS_THEN_DONE, tools=[add_tool]
    )
    result = await harness.run(BaseTask(description="q", max_steps=10))

    # U itself was not recorded ...
    assert not unfolded_path(str(tmp_path), "noU", result.run_id).exists()
    # ... but the identity IS, with genotype/deployment present and phenotype absent.
    ident = load_identity(identity_path(str(tmp_path), "noU", result.run_id))
    assert ident.genotype == genotype_hash(to_graph(cfg))
    assert ident.deployment == deployment_hash(to_graph(cfg))
    assert ident.phenotype is None, "phenotype must be absent, not a hash over an empty observed set"
    assert ident.phenotype_absent_reason == "unfold_disabled"


# ── stability: same config → same genotype/deployment across a re-run ─────────


@pytest.mark.asyncio
async def test_digests_stable_across_reruns(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESSX_GHX_IDENTITY", "1")
    monkeypatch.delenv("HARNESSX_GHX_UNFOLD", raising=False)

    _cfg1, h1 = _make(tmp_path, "s1", [MultiHookSlotIO()], ["done"])
    r1 = await h1.run(BaseTask(description="q"))
    _cfg2, h2 = _make(tmp_path, "s2", [MultiHookSlotIO()], ["done"])
    r2 = await h2.run(BaseTask(description="q"))

    i1 = load_identity(identity_path(str(tmp_path), "s1", r1.run_id))
    i2 = load_identity(identity_path(str(tmp_path), "s2", r2.run_id))
    assert i1.genotype == i2.genotype
    assert i1.deployment == i2.deployment
