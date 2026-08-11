# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""G1 test 4 — evidence files are readable under the official roles' read scope.

Build a Digester harness via the vendored builder (unmodified) and prove its
ReadScopeGateProcessor does not block the graph_evidence/ paths — both through
the serialised gate contract (blocked_roots/allowed_files, the same shape the
existing aegis read-scope tests assert against) AND by driving a live
ToolCallEvent through the gate's on_before_tool hook. harnessx source must stay
blocked (no regression). The gate itself is never modified.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from harnessx.aegis._paths import HARNESSX_SRC_ROOT
from harnessx.aegis.agents.digester import DigesterInputs, build_digester_harness
from harnessx.core.events import ToolCallEvent
from harnessx.ghx.evidence_files import materialize_graph_evidence
from harnessx.graph.causal import CONTROL
from harnessx.graph.types import unfolded_id
from harnessx.graph.unfold import UnfoldedEdge, UnfoldedGraph, UnfoldedNode
from harnessx.meta_harness.processors.read_scope_gate import ReadScopeGateProcessor


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
        id=unfolded_id("tool:Bash", 1),
        static_node_id="tool:Bash",
        graphed=True,
        hook="tool",
        step=0,
        ordinal=1,
        label="Bash",
    )
    c = UnfoldedNode(
        id=unfolded_id("End", 2), static_node_id="End", graphed=True, hook="task_end", step=1, ordinal=2, label="End"
    )
    return UnfoldedGraph(
        run_id="alpha-run",
        session_id="s",
        nodes=[a, b, c],
        edges=[
            UnfoldedEdge(source=a.id, target=b.id, edge_type=CONTROL),
            UnfoldedEdge(source=b.id, target=c.id, edge_type=CONTROL),
        ],
    )


def _read_gate_dict(cfg) -> dict:
    """Pull the read-scope gate's serialised entry out of a HarnessConfig — the
    same extraction the existing aegis read-scope tests use."""
    for p in cfg.processors:
        if isinstance(p, dict) and "ReadScopeGateProcessor" in p.get("_target_", ""):
            return p
    raise AssertionError("no ReadScopeGateProcessor entry found in config")


def _is_blocked(gate: dict, path) -> bool:
    resolved = Path(path).resolve()
    allowed = [Path(x).resolve() for x in gate.get("allowed_files", [])]
    blocked = [Path(x).resolve() for x in gate.get("blocked_roots", [])]
    if any(resolved == a for a in allowed):
        return False
    for root in blocked:
        try:
            resolved.relative_to(root)
            return True
        except ValueError:
            continue
    return False


def _drive_gate(gate: dict, path: str) -> ToolCallEvent:
    """Run a real Read ToolCallEvent through a live gate built from the serialised
    args, returning the (possibly replaced) event the gate yields."""
    live = ReadScopeGateProcessor(
        blocked_roots=tuple(gate.get("blocked_roots", ())),
        allowed_files=tuple(gate.get("allowed_files") or ()),
    )
    ev = ToolCallEvent(run_id="r", step_id=0, tool_name="Read", tool_input={"file_path": path})

    async def _first():
        async for out in live.on_before_tool(ev):
            return out
        return None

    return asyncio.run(_first())


def test_graph_evidence_readable_under_digester_gate(tmp_path: Path):
    run_dir = tmp_path / "run"
    summary = materialize_graph_evidence(run_dir, 1, ["alpha"], lambda t: _u())
    ev = Path(summary["evidence_dir"])
    cone_file = ev / "cones" / "alpha.md"
    facts_file = ev / "facts.md"
    assert cone_file.exists() and facts_file.exists()

    # Build the Digester harness via the vendored builder, unmodified.
    traj_dir = run_dir / "R1" / "trajectories"
    traj_dir.mkdir(parents=True, exist_ok=True)
    traj = traj_dir / "alpha_r0.jsonl"
    traj.write_text("{}\n", encoding="utf-8")
    inputs = DigesterInputs(
        task_id="alpha",
        pattern="ALL_FAIL",
        trajectory_paths=[traj],
        digest_out_path=run_dir / "R1" / "digests" / "alpha.md",
    )
    cfg = build_digester_harness(inputs)
    gate = _read_gate_dict(cfg)

    # Contract: graph_evidence/ is NOT under a blocked root, so it is readable.
    assert not _is_blocked(gate, cone_file)
    assert not _is_blocked(gate, facts_file)
    assert not _is_blocked(gate, ev / "cones")  # Grep/Glob dir path

    # Live gate agrees: the event passes through un-replaced (approved, no synthetic).
    out = _drive_gate(gate, str(cone_file))
    assert out.approved is True
    assert out.synthetic_result is None
    out_facts = _drive_gate(gate, str(facts_file))
    assert out_facts.approved is True

    # No regression: harnessx source stays blocked, live gate blocks it too.
    src = HARNESSX_SRC_ROOT / "core" / "processor.py"
    assert _is_blocked(gate, src)
    blocked = _drive_gate(gate, str(src))
    assert blocked.approved is False
