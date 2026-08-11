# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""G1 test 2 — facts.md shared-node section and the honest edit-history line.

The shared-node section is a cross-task fact: a static node in the cone of MORE
THAN ONE failing task. Present when >=2 tasks share a node; absent (no header,
not an empty header) when the cones are disjoint. The edit-history line is the
honest 'not yet recorded' text — the distinction between unknown and never that
has bitten this codebase repeatedly.
"""

from __future__ import annotations

from pathlib import Path

from harnessx.ghx.evidence_files import materialize_graph_evidence
from harnessx.graph.causal import CONTROL
from harnessx.graph.types import unfolded_id
from harnessx.graph.unfold import UnfoldedEdge, UnfoldedGraph, UnfoldedNode


def _linear_u(run_id: str, bases: list[tuple[str, str, int]]) -> UnfoldedGraph:
    """A control chain t0->t1->...->tN; the terminal-anchored cone is every node,
    so the cone's static-node set is exactly the ``bases`` provided."""
    nodes = [
        UnfoldedNode(
            id=unfolded_id(base, i),
            static_node_id=base,
            graphed=True,
            hook=hook,
            step=step,
            ordinal=i,
            label=base,
        )
        for i, (base, hook, step) in enumerate(bases)
    ]
    edges = [UnfoldedEdge(source=nodes[i].id, target=nodes[i + 1].id, edge_type=CONTROL) for i in range(len(nodes) - 1)]
    return UnfoldedGraph(run_id=run_id, session_id="s", nodes=nodes, edges=edges)


_EDIT_HISTORY_MARK = "not yet recorded"


def test_shared_section_present_when_two_tasks_share_a_node(tmp_path: Path):
    run_dir = tmp_path / "run"

    def resolver(task: str):
        if task == "alpha":
            return _linear_u("a", [("Sys", "before_model", 0), ("tool:Bash", "tool", 1), ("End", "task_end", 2)])
        if task == "beta":
            return _linear_u("b", [("SysB", "before_model", 0), ("tool:Bash", "tool", 1), ("EndB", "task_end", 2)])
        return None

    summary = materialize_graph_evidence(run_dir, 1, ["alpha", "beta"], resolver)
    facts = Path(summary["facts_path"]).read_text(encoding="utf-8")

    # Only tool:Bash is shared; Sys/End vs SysB/EndB are task-local.
    assert summary["shared_nodes"] == {"tool:Bash": ["alpha", "beta"]}
    assert "## Shared cone nodes" in facts
    assert "`tool:Bash` — tasks: alpha, beta" in facts

    # Edit-history line is always present and honest.
    assert "## Node edit history" in facts
    assert _EDIT_HISTORY_MARK in facts


def test_shared_section_absent_when_cones_disjoint(tmp_path: Path):
    run_dir = tmp_path / "run"

    def resolver(task: str):
        if task == "alpha":
            return _linear_u("a", [("SysA", "before_model", 0), ("tool:Read", "tool", 1), ("EndA", "task_end", 2)])
        if task == "beta":
            return _linear_u("b", [("SysB", "before_model", 0), ("tool:Write", "tool", 1), ("EndB", "task_end", 2)])
        return None

    summary = materialize_graph_evidence(run_dir, 1, ["alpha", "beta"], resolver)
    facts = Path(summary["facts_path"]).read_text(encoding="utf-8")

    # No node is shared: the section header is ABSENT, not an empty header.
    assert summary["shared_nodes"] == {}
    assert "## Shared cone nodes" not in facts

    # The edit-history line is still there — honesty does not depend on sharing.
    assert "## Node edit history" in facts
    assert _EDIT_HISTORY_MARK in facts
