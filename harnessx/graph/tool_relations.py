# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""Tool-to-tool relations, PROJECTED from the unfolded graph U (v6 M5).

This is a *derived* artifact: it reads tool relations straight out of U's recorded
edges, so it cannot drift from what actually ran — there is no hand-written tool
dependency declaration to fall out of sync.

What the trace genuinely supports.  In U, a tool node's only edges are the control
bridges into and out of its own tool call (``before_tool`` → ``tool`` →
``after_tool``); control edges never span a hook firing or a tool call, so two
DIFFERENT tool nodes are never connected by control alone.  They can only become
connected through an ``OBSERVED_DATA`` edge — a slot a processor wrote downstream
of tool A that another processor read upstream of tool B.  So the relation this
projection emits is precisely *data-mediated reachability*: A → B iff there is a
directed path A ⇝ B in U crossing at least one data edge.  ``via_slots`` names the
slot keys on a witnessing path, and the relation is transitive (A ⇝ B ⇝ C yields
A → C).

What it does NOT claim.  U records that a processor RAN after a tool and that it
WROTE a slot; it does not record whether that write's *value* was derived from the
tool's result (there is no intra-processor data flow in the trace, and tool results
land in ``raw_messages``, not in slots).  So a data-mediated relation means "a slot
carried information across A's and B's regions", which is an over-approximation at
the value level — honestly the strongest claim the trace backs.  Two tools that run
in sequence with no shared slot flow are NOT related here, however tempting the
temporal order makes it.  Inter-layer relations (parent tool → child run) are a
different thing entirely and live in :class:`~harnessx.graph.unfold.UnfoldedInvokes`,
not in this projection.
"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field

from .types import EdgeType

_TOOL_PREFIX = "tool:"
_OBSERVED_DATA = EdgeType.OBSERVED_DATA.value


@dataclass(frozen=True)
class ToolRelation:
    """A data-mediated relation ``source_tool → target_tool``, projected from U.

    ``source_node`` / ``target_node`` are the specific tool invocation ids (an
    :func:`~harnessx.graph.types.unfolded_id` each), so two invocations of the same
    tool stay distinct.  ``via_slots`` are the slot keys crossed on one witnessing
    path — the concrete data the relation rests on.
    """

    source_tool: str
    target_tool: str
    source_node: str
    target_node: str
    via_slots: tuple = field(default_factory=tuple)


def _tool_name(static_node_id: str):
    if static_node_id.startswith(_TOOL_PREFIX):
        return static_node_id[len(_TOOL_PREFIX) :]
    return None


def _witness_slots(back, state) -> tuple:
    """Walk the BFS backpointers from ``state`` to the source, collecting data slots."""
    slots: list = []
    cur = state
    while cur is not None:
        entry = back.get(cur)
        if entry is None:
            break
        prev_state, slot = entry
        if slot is not None:
            slots.append(slot)
        cur = prev_state
    return tuple(reversed(slots))


def project_tool_relations(graph) -> list:
    """Project the data-mediated tool→tool relations recorded in ``graph`` (a U).

    Returns a list of :class:`ToolRelation`, one per (source tool node, reachable
    target tool node) pair connected by a path that crosses at least one
    ``OBSERVED_DATA`` edge.  Pure-control reachability never connects two tool
    nodes (control edges do not span firings), so every emitted relation is
    genuinely data-mediated; the requirement is enforced explicitly so the claim
    holds even if future edge kinds change that.
    """
    tool_ids = {n.id for n in graph.nodes if n.static_node_id.startswith(_TOOL_PREFIX)}
    name_by_id = {n.id: _tool_name(n.static_node_id) for n in graph.nodes if n.id in tool_ids}

    # adjacency over BOTH observed edge kinds: node -> [(target, is_data, slot_key)]
    adj: dict = defaultdict(list)
    for e in graph.edges:
        is_data = e.edge_type == _OBSERVED_DATA
        slot = e.metadata.get("slot_key") if is_data else None
        adj[e.source].append((e.target, is_data, slot))

    relations: list = []
    for src_id in sorted(tool_ids):
        # BFS over states (node, crossed_data); backpointers reconstruct a witness.
        start = (src_id, False)
        back: dict = {start: None}
        queue = deque([start])
        emitted: set = set()
        while queue:
            node, crossed = queue.popleft()
            for tgt, is_data, slot in adj.get(node, ()):  # noqa: B007 — slot used below
                ncross = crossed or is_data
                nstate = (tgt, ncross)
                if nstate in back:
                    continue
                back[nstate] = ((node, crossed), slot if is_data else None)
                queue.append(nstate)
                if ncross and tgt != src_id and tgt in tool_ids and tgt not in emitted:
                    emitted.add(tgt)
                    relations.append(
                        ToolRelation(
                            source_tool=name_by_id[src_id],
                            target_tool=name_by_id[tgt],
                            source_node=src_id,
                            target_node=tgt,
                            via_slots=_witness_slots(back, nstate),
                        )
                    )
    return relations


def project_tool_relation_names(graph) -> set:
    """Convenience: the set of ``(source_tool, target_tool)`` name pairs from U."""
    return {(r.source_tool, r.target_tool) for r in project_tool_relations(graph)}
