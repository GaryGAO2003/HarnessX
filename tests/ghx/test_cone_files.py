# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""G1 test 1 + 6 — cone maps per failing task, and stable round-scoped layout.

The materialiser is pure, so these run on hand-built U's whose cone is obvious
by inspection: a control+data chain into a terminal task_end anchor, with one
deliberately-unrelated node that must stay OUT of the cone. We assert ordinal
ordering, the slot data-flow line, the INVOKES frontier, the trajectory step
pointers, caller-driven failing-task selection (solved tasks get nothing), and
the honest missing-U path.
"""

from __future__ import annotations

from pathlib import Path

from harnessx.ghx.evidence_files import materialize_graph_evidence
from harnessx.graph.causal import CONTROL, DATA
from harnessx.graph.types import unfolded_id
from harnessx.graph.unfold import UnfoldedEdge, UnfoldedGraph, UnfoldedInvokes, UnfoldedNode


def _n(base: str, ordinal: int, hook: str, step: int) -> UnfoldedNode:
    return UnfoldedNode(
        id=unfolded_id(base, ordinal),
        static_node_id=base,
        graphed=True,
        hook=hook,
        step=step,
        ordinal=ordinal,
        label=base,
    )


def _alpha_u() -> UnfoldedGraph:
    """A U whose terminal-anchored cone is {t0, t2, t3, t4}; t1 is unrelated.

    t0 Sys        --control--> t2 tool:Bash --control--> t3 Eval --control--> t4 End
    t2 tool:Bash  --data(plan)-----------------------> t3 Eval
    t1 Unrelated  (isolated — never an ancestor of the terminal t4)
    t2 --INVOKES--> child-run-1
    """
    t0 = _n("Sys", 0, "before_model", 0)
    t1 = _n("Unrelated", 1, "before_model", 0)
    t2 = _n("tool:Bash", 2, "tool", 1)
    t3 = _n("Eval", 3, "after_tool", 1)
    t4 = _n("End", 4, "task_end", 2)
    edges = [
        UnfoldedEdge(source=t0.id, target=t2.id, edge_type=CONTROL),
        UnfoldedEdge(source=t2.id, target=t3.id, edge_type=CONTROL),
        UnfoldedEdge(source=t3.id, target=t4.id, edge_type=CONTROL),
        UnfoldedEdge(source=t2.id, target=t3.id, edge_type=DATA, metadata={"slot_key": "plan"}),
    ]
    return UnfoldedGraph(
        run_id="alpha-run",
        session_id="s",
        nodes=[t0, t1, t2, t3, t4],
        edges=edges,
        invokes=[UnfoldedInvokes(source=t2.id, child_run_id="child-run-1")],
    )


def test_cone_file_content(tmp_path: Path):
    run_dir = tmp_path / "run"
    summary = materialize_graph_evidence(
        run_dir,
        1,
        ["alpha"],
        lambda t: _alpha_u() if t == "alpha" else None,
    )
    cone = Path(summary["cones_dir"]) / "alpha.md"
    body = cone.read_text(encoding="utf-8")

    # Invocations section: ordinal order, unrelated node excluded.
    inv = body.split("## Invocations (ordinal order)")[1].split("##")[0]
    assert inv.index("t0:") < inv.index("t2:") < inv.index("t3:") < inv.index("t4:")
    assert "t1:" not in inv
    assert "Unrelated" not in body
    # Step pointers travel on each invocation line.
    assert "- t2: tool tool:Bash [step 1]" in body
    assert "- t4: task_end End [step 2]" in body

    # Slot data-flow section.
    assert "## Slot data-flow (writer -> reader)" in body
    assert "- plan: t2 -> t3" in body

    # INVOKES frontier (cone reached a subagent boundary).
    assert "## INVOKES frontier" in body
    assert "child-run-1" in body

    # Trajectory step pointers — the map the reader opens.
    steps = body.split("## Trajectory steps that causally mattered")[1]
    assert "0, 1, 2" in steps
    # It is a MAP, not a payload — say so.
    assert "MAP, not a payload" in body


def test_selection_is_caller_driven(tmp_path: Path):
    """Only tasks in the failing list get cones; solved tasks get none even when
    the resolver could produce a U for them; a failing task with no U is honestly
    reported (no empty cone file implying 'no cone')."""
    run_dir = tmp_path / "run"

    def resolver(task: str):
        if task in ("alpha", "beta"):
            return _alpha_u()
        return None  # gamma: failing but U unavailable

    summary = materialize_graph_evidence(run_dir, 1, ["alpha", "gamma"], resolver)
    cones_dir = Path(summary["cones_dir"])

    assert (cones_dir / "alpha.md").exists()
    assert not (cones_dir / "beta.md").exists()  # solved (not in failing list)
    assert not (cones_dir / "gamma.md").exists()  # failing but U unavailable
    assert summary["cones_written"] == ["alpha"]
    assert summary["missing_u"] == ["gamma"]


def test_stable_round_scoped_layout(tmp_path: Path):
    """Files land under R{n}/graph_evidence/ with stable names."""
    run_dir = tmp_path / "run"
    summary = materialize_graph_evidence(run_dir, 7, ["alpha"], lambda t: _alpha_u())

    ev = run_dir / "R7" / "graph_evidence"
    assert Path(summary["evidence_dir"]) == ev
    assert Path(summary["facts_path"]) == ev / "facts.md"
    assert Path(summary["cones_dir"]) == ev / "cones"
    assert (ev / "cones" / "alpha.md").exists()
    assert (ev / "facts.md").exists()
