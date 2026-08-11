# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""G1 test 3 — the flag-gated wrapper is a pure pass-through when off.

A spy orchestrator records that ``run_round`` was delegated to, without doing any
real work. Flag off: nothing is written and the delegate is still called. Flag
on: the evidence files appear AND the delegate is still called. The wrapper never
subclasses or overrides the vendored orchestrator.
"""

from __future__ import annotations

from pathlib import Path

from harnessx.ghx.overlay import aegis_evidence_enabled, run_round_with_graph_evidence
from harnessx.graph.causal import CONTROL
from harnessx.graph.types import unfolded_id
from harnessx.graph.unfold import UnfoldedEdge, UnfoldedGraph, UnfoldedNode


def _u() -> UnfoldedGraph:
    a = UnfoldedNode(
        id=unfolded_id("Sys", 0),
        static_node_id="Sys",
        graphed=True,
        hook="before_model",
        step=0,
        ordinal=0,
        label="Sys",
    )
    b = UnfoldedNode(
        id=unfolded_id("End", 1), static_node_id="End", graphed=True, hook="task_end", step=1, ordinal=1, label="End"
    )
    return UnfoldedGraph(
        run_id="r",
        session_id="s",
        nodes=[a, b],
        edges=[UnfoldedEdge(source=a.id, target=b.id, edge_type=CONTROL)],
    )


def _resolver(task: str):
    return _u() if task == "alpha" else None


class _SpyOrch:
    """Minimal stand-in: has a run_dir and an async run_round that records calls."""

    def __init__(self, run_dir: Path) -> None:
        self.run_dir = Path(run_dir)
        self.calls: list[dict] = []

    async def run_round(self, **kwargs):
        self.calls.append(kwargs)
        return {"ok": True}


def _run_round_kwargs(tmp_path: Path) -> dict:
    return {
        "round_n": 1,
        "raw_sessions_dir": tmp_path / "sessions",
        "pass_flags_by_task": {},
        "current_config_path": tmp_path / "config.yaml",
    }


async def test_flag_off_writes_nothing_but_delegates(tmp_path: Path):
    orch = _SpyOrch(tmp_path / "run")
    res = await run_round_with_graph_evidence(
        orch,
        failed_task_ids=["alpha"],
        resolver=_resolver,
        evidence_enabled=False,
        **_run_round_kwargs(tmp_path),
    )
    # Delegate called, verbatim kwargs forwarded.
    assert res == {"ok": True}
    assert len(orch.calls) == 1
    assert orch.calls[0]["round_n"] == 1
    assert "failed_task_ids" not in orch.calls[0]  # our params are NOT forwarded
    assert "resolver" not in orch.calls[0]
    # Nothing written.
    assert not (orch.run_dir / "R1" / "graph_evidence").exists()


async def test_flag_on_writes_and_delegates(tmp_path: Path):
    orch = _SpyOrch(tmp_path / "run")
    await run_round_with_graph_evidence(
        orch,
        failed_task_ids=["alpha"],
        resolver=_resolver,
        evidence_enabled=True,
        **_run_round_kwargs(tmp_path),
    )
    assert len(orch.calls) == 1
    ev = orch.run_dir / "R1" / "graph_evidence"
    assert (ev / "facts.md").exists()
    assert (ev / "cones" / "alpha.md").exists()


def test_flag_default_off(monkeypatch):
    monkeypatch.delenv("HARNESSX_GHX_AEGIS_EVIDENCE", raising=False)
    assert aegis_evidence_enabled() is False
    monkeypatch.setenv("HARNESSX_GHX_AEGIS_EVIDENCE", "1")
    assert aegis_evidence_enabled() is True
    monkeypatch.setenv("HARNESSX_GHX_AEGIS_EVIDENCE", "off")
    assert aegis_evidence_enabled() is False
