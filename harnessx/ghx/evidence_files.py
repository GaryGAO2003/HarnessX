# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""Materialise graph evidence as workspace files for the official AEGIS roles.

**The load-bearing idea.**  In the agentic-pull world a cone file is a *MAP for
the reader, not a payload*.  The official Digester is itself an agent that reads
trajectories; so a cone file does not copy trajectory content into itself — it
tells the Digester *which steps causally mattered* (by trajectory step number)
so its own reads go to the right places.  Everything here is a pointer, never a
transcript.

Two kinds of file are written under ``<run_dir>/R{n}/graph_evidence/``:

1. **Cone maps**, one per *failing* task, at ``cones/<task_id>.md``.  Each renders
   the causal cone (:func:`harnessx.graph.causal.causal_cone`) anchored by
   :func:`_cone_anchors` — the run's terminal invocation
   (:func:`~harnessx.graph.causal.terminal_node`) together with its last model call
   (v6 M12), because ``TaskEndEvent`` carries no messages and the terminal anchor
   alone cannot reach the message plane.  The file holds the cone's invocations in
   ordinal order (``t{ordinal}: hook label [step N]``), the data-flow section
   (``key: t{writer} -> t{reader}`` over the cone's ``OBSERVED_DATA`` edges, both
   the slot and the message plane), the ``INVOKES`` frontier when the cone reached a
   subagent boundary, and the set of trajectory step numbers the cone touched —
   the pointers the reader opens.

2. **Cross-task facts** at ``facts.md``.  From the cones just computed: the static
   nodes appearing in the cone of MORE THAN ONE failing task (emitted only when
   non-empty — an absent section, never an empty header).  Node edit history is
   written as a single honest line: the official ship ledger does not record
   graph node ids yet, so this is *not yet recorded*, which is distinct from
   *never edited* — absence here means unknown, not none.

This module is pure: it reads U files through a caller-supplied ``resolver`` and
writes evidence files.  It does not read the flag, does not re-derive pass/fail
(the caller passes the failing-task list — the orchestrator's Stage P already
knows outcomes), and imports nothing from ``harnessx.aegis``.
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from ..graph.causal import DATA, causal_cone, invokes_frontier, select_nodes, terminal_node
from ..graph.unfold import UnfoldedGraph, load_unfolded, unfolded_path

# A static node counts as "shared" only when it appears in the cone of at least
# this many DISTINCT failing tasks.  Two is the whole point: a node in exactly
# one task's cone is task-local, not a cross-task fact.  Isolated here because it
# is the one line a broken gate would get wrong.
_SHARED_MIN_TASKS = 2


# ── resolvers (kept separate from the pure materialiser) ─────────────────────


def core_layout_resolver(base_dir, session_id: str, run_id_by_task: dict):
    """A resolver for the U-file layout :func:`harnessx.graph.unfold.write_unfolded`
    writes: ``{base_dir}/{session_id}/{run_id}_unfolded.jsonl``.

    ``run_id_by_task`` maps a task id to the run id whose U file holds that task's
    run.  The returned callable takes a task id and returns a loaded
    :class:`~harnessx.graph.unfold.UnfoldedGraph`, or ``None`` when the task has no
    known run id or its U file is absent (so a missing file reads as *unavailable*,
    never as an empty graph).  The recipe wires its own resolver when the layout
    differs; the materialiser accepts any ``task_id -> (UnfoldedGraph | path | None)``.
    """

    def resolve(task_id: str):
        run_id = run_id_by_task.get(task_id)
        if not run_id:
            return None
        path = unfolded_path(str(base_dir), session_id, run_id)
        if not path.exists():
            return None
        return load_unfolded(path)

    return resolve


# ── U normalisation ──────────────────────────────────────────────────────────


def _load_u(resolved) -> UnfoldedGraph | None:
    """Normalise a resolver result (``UnfoldedGraph`` | path-like | ``None``)."""
    if resolved is None:
        return None
    if isinstance(resolved, UnfoldedGraph):
        return resolved
    path = Path(resolved)
    if not path.exists():
        return None
    return load_unfolded(path)


# ── rendering ────────────────────────────────────────────────────────────────


def _cone_anchors(u: UnfoldedGraph) -> list:
    """The anchors a failure cone is rooted at: the terminal invocation, plus the run's
    LAST model call when there was one (v6 M12).

    The terminal invocation is the run's final state, but ``TaskEndEvent`` carries no
    ``messages`` field, so a ``task_end`` processor can never touch the message plane and
    its cone reaches nothing the run actually did.  The last model call is the invocation
    that emitted the final answer and read the whole history that produced it — on the
    message plane that is where the run's causal chain is anchored.  Both are kept: the
    union only ever adds to what the terminal anchor already gave.
    """
    anchors: list = []
    term = terminal_node(u)
    if term is not None:
        anchors.append(term)
    model_nodes = select_nodes(u, lambda n: n.hook == "model")
    if model_nodes and model_nodes[-1] not in anchors:
        anchors.append(model_nodes[-1])
    return anchors


def _render_cone(task_id: str, u: UnfoldedGraph, anchors: list, cone: set) -> str:
    node_by_id = {n.id: n for n in u.nodes}
    cone_nodes = sorted(
        (node_by_id[nid] for nid in cone if nid in node_by_id),
        key=lambda n: n.ordinal,
    )

    def _desc(nid: str) -> str:
        n = node_by_id.get(nid)
        return f"t{n.ordinal}: {n.hook} {n.label}" if n else nid

    anchor_desc = "; ".join(_desc(a) for a in anchors)

    lines: list[str] = [
        f"# Causal cone — {task_id}",
        "",
        (
            f"Anchored at {anchor_desc}. The invocations "
            "below are the causal cone — every invocation that could have contributed to "
            "how this run ended — in ordinal order. This file is a MAP, not a payload: each "
            "`[step N]` points into this task's trajectory; read those steps yourself, no "
            "trajectory content is copied here."
        ),
        "",
        "## Invocations (ordinal order)",
    ]
    for n in cone_nodes:
        lines.append(f"- t{n.ordinal}: {n.hook} {n.label} [step {n.step}]")
    lines.append("")

    # Slot data-flow: OBSERVED_DATA edges with both endpoints inside the cone.
    data_rows: list[tuple[int, int, str]] = []
    for e in u.edges:
        if e.edge_type != DATA or e.source not in cone or e.target not in cone:
            continue
        w = node_by_id.get(e.source)
        r = node_by_id.get(e.target)
        if w is None or r is None:
            continue
        slot = (e.metadata or {}).get("slot_key", "?")
        data_rows.append((w.ordinal, r.ordinal, slot))
    if data_rows:
        lines.append("## Data-flow (writer -> reader)")
        for w_ord, r_ord, slot in sorted(data_rows):
            lines.append(f"- {slot}: t{w_ord} -> t{r_ord}")
        lines.append("")

    # INVOKES frontier: subagent boundaries the cone reached but did not descend.
    frontier = invokes_frontier(u, cone)
    if frontier:
        lines.append("## INVOKES frontier")
        for iv in sorted(
            frontier, key=lambda x: (node_by_id[x.source].ordinal if x.source in node_by_id else -1, x.child_run_id)
        ):
            src = node_by_id.get(iv.source)
            src_desc = f"t{src.ordinal} ({src.label})" if src else iv.source
            lines.append(f"- {src_desc} invokes child run {iv.child_run_id}")
        lines.append("")

    # Trajectory step pointers — the map the reader opens.
    steps = sorted({n.step for n in cone_nodes})
    lines.append("## Trajectory steps that causally mattered")
    lines.append(
        "Open these step numbers in this task's `trajectories/` file(s) — the cone above "
        "names which invocations mattered; these are the steps to read:"
    )
    lines.append(", ".join(str(s) for s in steps) if steps else "(none)")
    lines.append("")
    return "\n".join(lines)


def _compute_shared_nodes(per_task_static_nodes: dict) -> dict:
    """Map each static node id to the sorted list of failing tasks whose cone
    contains it, keeping only nodes shared by ``>= _SHARED_MIN_TASKS`` distinct tasks."""
    node_to_tasks: dict[str, set] = defaultdict(set)
    for task_id, statics in per_task_static_nodes.items():
        for s in statics:
            node_to_tasks[s].add(task_id)
    shared = {node: sorted(tasks) for node, tasks in node_to_tasks.items() if len(tasks) >= _SHARED_MIN_TASKS}
    return dict(sorted(shared.items()))


_EDIT_HISTORY_LINE = (
    "Node-level edit history is not yet recorded: the ship ledger does not track "
    "graph node ids (that arrives with a later module). This is *not yet recorded*, "
    "NOT *never edited* — absence here means unknown, not none."
)


def _render_facts(round_n: int, shared: dict) -> str:
    lines: list[str] = [f"# Cross-task graph facts — R{round_n}", ""]
    if shared:
        lines.append("## Shared cone nodes")
        lines.append("Static nodes appearing in the causal cone of more than one failing task:")
        for node, tasks in shared.items():
            lines.append(f"- `{node}` — tasks: {', '.join(tasks)}")
        lines.append("")
    lines.append("## Node edit history")
    lines.append(_EDIT_HISTORY_LINE)
    lines.append("")
    return "\n".join(lines)


# ── the pure materialiser ────────────────────────────────────────────────────


def materialize_graph_evidence(
    run_dir,
    round_n: int,
    failed_task_ids,
    resolver,
) -> dict:
    """Write cone maps + ``facts.md`` under ``<run_dir>/R{round_n}/graph_evidence/``.

    ``failed_task_ids`` is the caller's failing-task list — this function never
    re-derives pass/fail.  ``resolver`` maps a task id to its U
    (:class:`~harnessx.graph.unfold.UnfoldedGraph`), a path to a U JSONL file, or
    ``None`` when unavailable; a task whose U is unavailable or empty gets **no**
    cone file (its absence is unambiguous) and is reported under ``missing_u``.
    Solved tasks are simply not in ``failed_task_ids`` and get nothing.

    Returns a summary dict: ``cones_written`` (task ids), ``missing_u`` (task ids
    with no usable U), ``shared_nodes`` (the facts.md shared map), and the two
    written directories/paths — for the caller's audit trail and for tests.
    """
    ev_dir = Path(run_dir) / f"R{round_n}" / "graph_evidence"
    cones_dir = ev_dir / "cones"
    ev_dir.mkdir(parents=True, exist_ok=True)

    per_task_static_nodes: dict[str, set] = {}
    cones_written: list[str] = []
    missing_u: list[str] = []

    for task_id in failed_task_ids:
        u = _load_u(resolver(task_id))
        if u is None or not u.nodes:
            missing_u.append(task_id)
            continue
        anchors = _cone_anchors(u)
        if not anchors:
            missing_u.append(task_id)
            continue
        cone = causal_cone(u, anchors, include_anchors=True)
        cones_dir.mkdir(parents=True, exist_ok=True)
        (cones_dir / f"{task_id}.md").write_text(_render_cone(task_id, u, anchors, cone), encoding="utf-8")
        cones_written.append(task_id)
        node_by_id = {n.id: n for n in u.nodes}
        per_task_static_nodes[task_id] = {node_by_id[nid].static_node_id for nid in cone if nid in node_by_id}

    shared = _compute_shared_nodes(per_task_static_nodes)
    facts_path = ev_dir / "facts.md"
    facts_path.write_text(_render_facts(round_n, shared), encoding="utf-8")

    return {
        "evidence_dir": str(ev_dir),
        "cones_dir": str(cones_dir),
        "facts_path": str(facts_path),
        "cones_written": cones_written,
        "missing_u": missing_u,
        "shared_nodes": shared,
    }
