# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""Causal queries over the unfolded graph U (v6 M6a).

U is a DAG of invocations (:mod:`harnessx.graph.unfold`).  This is the query layer
that replaces "read the trace" with "query the trace": ask for the causal cone of a
failure and get back only the invocations that could have contributed to it, instead
of serialising a whole run and truncating it to a fixed character cap.

Three design decisions are baked in here; each is a mechanism the caller drives, not
a policy hard-coded past their reach.

1. **Anchors.**  A task failure is not a node in U, so the caller must name the
   invocations to anchor on.  This module offers the *mechanism* — :func:`select_nodes`
   (any predicate over :class:`~harnessx.graph.unfold.UnfoldedNode`) — plus two
   convenience selectors for the obvious candidates: :func:`terminal_node` (the last
   invocation, the one that produced the run's final state) and :func:`tool_invocations`
   (tool nodes, optionally by name — how a caller anchors on a tool call it already
   knows erred, since U records that a tool RAN but not its result status).  The caller
   chooses; nothing here decides for them.

2. **Cross-layer (INVOKES).**  An ``INVOKES`` edge points from a parent ``spawn_subagent``
   tool node to a child *run id* — not a node in this U, and the child's U is a separate
   file with its own ordinal counter from zero.  :func:`ancestors` and :func:`causal_cone`
   deliberately do **not** silently cross it: ``INVOKES`` is a forward (parent→child)
   edge, so it is never an ancestor link, and U carries no data edge from a child's result
   back into a later parent node (tool results land in ``raw_messages``, not slots — the
   same limit :mod:`harnessx.graph.tool_relations` documents), so treating a child run as an
   *ancestor* of a later parent failure would over-claim.  Instead the boundary is made
   **observable**: :func:`invokes_frontier` reports every ``INVOKES`` edge whose source is
   in the cone, so a caller can always tell the cone reached a subagent boundary.  Descent
   is offered as an explicit, opt-in operation, :func:`causal_cone_across`, which takes a
   ``resolve`` callback (run id → :class:`~harnessx.graph.unfold.UnfoldedGraph`) so this
   layer does no file IO of its own, and recurses into each child's cone anchored at the
   child's terminal invocation.  Descent is gated exactly as any node is: a child appears
   only because its spawning tool node is already a U-backed ancestor of the anchor.

3. **Edge-type filtering.**  Every query takes ``edge_types``: ``None`` for both observed
   kinds, or :data:`CONTROL` / :data:`DATA` (or any iterable of edge-type strings /
   :class:`~harnessx.graph.types.EdgeType`) to restrict the cone.  The data cone is the
   one that matters for attribution — it follows reaching-definition slot flow and drops
   the merely-adjacent control chain.

Everything here is pure: no file IO, no recording, no AEGIS imports.  Traversal uses a
visited set, so it terminates on any input — U is a DAG so a cycle cannot arise, but were
U ever cyclic the visited set still guards against looping forever.
"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field

from .types import EdgeType
from .unfold import UnfoldedGraph

# The two observed edge kinds a cone can follow.  INVOKES is never in ``graph.edges``
# (its target is a child run, not a node), so it never enters this adjacency.
CONTROL = EdgeType.OBSERVED_CONTROL.value
DATA = EdgeType.OBSERVED_DATA.value


def _edge_type_value(edge_type) -> str:
    """Normalise an edge-type argument (a string or an :class:`EdgeType`) to its string."""
    return getattr(edge_type, "value", edge_type)


def _norm_edge_types(edge_types):
    """Return a frozenset of allowed edge-type strings, or ``None`` for 'all observed'."""
    if edge_types is None:
        return None
    if isinstance(edge_types, (str, EdgeType)):
        edge_types = [edge_types]
    return frozenset(_edge_type_value(et) for et in edge_types)


def _adjacency(graph: UnfoldedGraph, allowed, *, reverse: bool) -> dict:
    """Build a node → [neighbours] map over the allowed edge types.

    ``reverse`` flips edge direction so the SAME builder serves both ancestors
    (predecessors) and descendants (successors); getting this flag wrong is exactly
    the off-by-one that makes ``ancestors`` return descendants, so it is isolated to
    one place and tested directly.
    """
    adj: dict = defaultdict(list)
    for e in graph.edges:
        if allowed is not None and e.edge_type not in allowed:
            continue
        if reverse:
            adj[e.target].append(e.source)
        else:
            adj[e.source].append(e.target)
    return adj


def _reach(adj: dict, start: str) -> set:
    """All nodes reachable from ``start`` over ``adj`` (INCLUDING ``start``).

    Visited-set BFS: every node is enqueued at most once, so traversal terminates on
    any graph — a hypothetical cycle in U is bounded rather than infinite.
    """
    seen = {start}
    queue = deque([start])
    while queue:
        cur = queue.popleft()
        for nxt in adj.get(cur, ()):
            if nxt not in seen:
                seen.add(nxt)
                queue.append(nxt)
    return seen


def _require_node(node_ids: set, node_id: str) -> None:
    if node_id not in node_ids:
        raise ValueError(f"{node_id!r} is not a node in this U")


# ── core queries ─────────────────────────────────────────────────────────────


def ancestors(graph: UnfoldedGraph, node_id: str, *, edge_types=None) -> set:
    """Every node that could have contributed to ``node_id`` — the backward cone.

    Follows edges backwards (predecessors) from ``node_id``; the result excludes
    ``node_id`` itself.  ``edge_types`` restricts which edge kinds are followed
    (default: both observed kinds).  ``INVOKES`` is not an observed edge and is never
    followed here — see :func:`causal_cone_across` for cross-layer descent.
    """
    _require_node(graph.node_ids(), node_id)
    adj = _adjacency(graph, _norm_edge_types(edge_types), reverse=True)
    return _reach(adj, node_id) - {node_id}


def descendants(graph: UnfoldedGraph, node_id: str, *, edge_types=None) -> set:
    """Every node ``node_id`` could have contributed to — the forward cone.

    Follows edges forwards (successors) from ``node_id``; the result excludes
    ``node_id`` itself.
    """
    _require_node(graph.node_ids(), node_id)
    adj = _adjacency(graph, _norm_edge_types(edge_types), reverse=False)
    return _reach(adj, node_id) - {node_id}


def causal_cone(graph: UnfoldedGraph, anchors, *, edge_types=None, include_anchors: bool = True) -> set:
    """The union of :func:`ancestors` over one or more ``anchors`` — the shape a
    consumer needs to explain a failure.

    ``anchors`` is an iterable of node ids (a bare string is accepted as one anchor).
    With ``include_anchors`` (the default) the anchors themselves are in the returned
    set, so it is a self-contained subgraph seed; set it false for the strict-ancestor
    set only.  ``edge_types`` restricts which edge kinds are followed — pass
    :data:`DATA` for the attribution cone.
    """
    if isinstance(anchors, str):
        anchors = [anchors]
    anchors = list(anchors)
    node_ids = graph.node_ids()
    for a in anchors:
        _require_node(node_ids, a)
    adj = _adjacency(graph, _norm_edge_types(edge_types), reverse=True)
    cone: set = set()
    for a in anchors:
        cone |= _reach(adj, a) - {a}
    if include_anchors:
        cone |= set(anchors)
    return cone


# ── anchor selection (mechanism, not policy) ─────────────────────────────────


def select_nodes(graph: UnfoldedGraph, predicate) -> list:
    """Ids of every node for which ``predicate(node)`` is true — the general anchor
    mechanism.  ``predicate`` takes an :class:`~harnessx.graph.unfold.UnfoldedNode`.
    """
    return [n.id for n in graph.nodes if predicate(n)]


def tool_invocations(graph: UnfoldedGraph, name: str | None = None) -> list:
    """Ids of tool-execution nodes (hook ``"tool"``), optionally filtered to tool ``name``.

    How a caller anchors on a tool call it knows failed: U records that a tool RAN and
    which tool it was, so the caller pairs its own knowledge of *which* call erred (from
    the journal / trajectory) with the node id here.
    """
    return [n.id for n in graph.nodes if n.hook == "tool" and (name is None or n.label == name)]


def terminal_node(graph: UnfoldedGraph) -> str | None:
    """Id of the last invocation (highest ordinal), or ``None`` for an empty U.

    The terminal invocation produced the run's final state, so it is the natural anchor
    for "what led to how this run ended".
    """
    if not graph.nodes:
        return None
    return max(graph.nodes, key=lambda n: n.ordinal).id


# ── cross-layer (INVOKES) ────────────────────────────────────────────────────


def invokes_frontier(graph: UnfoldedGraph, cone) -> list:
    """The ``INVOKES`` edges whose source node is inside ``cone``.

    This is how a caller tells that a cone reached a subagent boundary: every returned
    :class:`~harnessx.graph.unfold.UnfoldedInvokes` names a child run the cone touched
    but did not descend into (descent is opt-in via :func:`causal_cone_across`).
    """
    cone = set(cone)
    return [iv for iv in graph.invokes if iv.source in cone]


@dataclass
class LayeredCone:
    """A causal cone that may span layers.

    ``nodes`` is the cone within ``run_id``'s U (ids unambiguous within one U — child
    ordinals restart at zero, so ids are never flattened across layers).  ``invokes`` is
    the frontier: the ``INVOKES`` edges leaving this cone.  ``children`` maps a child run
    id to its own :class:`LayeredCone` — present only for children a resolver actually
    expanded; a child left in ``invokes`` but absent from ``children`` is a boundary the
    caller can still see and choose to resolve later.
    """

    run_id: str
    nodes: set
    invokes: list
    children: dict = field(default_factory=dict)

    def run_ids(self) -> set:
        """Every run id reachable through this cone (self + expanded descendants)."""
        out = {self.run_id}
        for child in self.children.values():
            out |= child.run_ids()
        return out

    def total_nodes(self) -> int:
        """Node count across this cone and every expanded child cone."""
        return len(self.nodes) + sum(c.total_nodes() for c in self.children.values())


def causal_cone_across(
    graph: UnfoldedGraph,
    anchors,
    *,
    resolve,
    edge_types=None,
    include_anchors: bool = True,
    max_depth: int | None = None,
    _seen=None,
) -> LayeredCone:
    """The causal cone of ``anchors``, descending into invoked child runs via ``resolve``.

    ``resolve`` maps a child run id to its :class:`~harnessx.graph.unfold.UnfoldedGraph`
    (or ``None`` if unavailable) — so this layer does no file IO.  For each ``INVOKES``
    edge leaving the cone, the child's own cone is computed, anchored at the child's
    terminal invocation (what it produced for the parent), and recorded under
    :attr:`LayeredCone.children`.  A child that ``resolve`` returns ``None`` for stays on
    the frontier only.  ``max_depth`` bounds descent (``None`` = unlimited); a
    ``_seen`` run-id set breaks any cycle an adversarial resolver might introduce, so
    recursion always terminates.
    """
    if _seen is None:
        _seen = set()
    cone = causal_cone(graph, anchors, edge_types=edge_types, include_anchors=include_anchors)
    frontier = invokes_frontier(graph, cone)
    result = LayeredCone(run_id=graph.run_id, nodes=cone, invokes=frontier, children={})

    seen_next = _seen | {graph.run_id}
    if resolve is None or (max_depth is not None and max_depth <= 0):
        return result

    for iv in frontier:
        child_id = iv.child_run_id
        if child_id in seen_next or child_id in result.children:
            continue
        child_graph = resolve(child_id)
        if child_graph is None:
            continue
        child_anchor = terminal_node(child_graph)
        if child_anchor is None:
            continue
        result.children[child_id] = causal_cone_across(
            child_graph,
            [child_anchor],
            resolve=resolve,
            edge_types=edge_types,
            include_anchors=include_anchors,
            max_depth=None if max_depth is None else max_depth - 1,
            _seen=seen_next,
        )
    return result


# ── materialisation ──────────────────────────────────────────────────────────


def induced_subgraph(graph: UnfoldedGraph, node_ids) -> UnfoldedGraph:
    """The sub-U induced on ``node_ids``: those nodes, the edges with both endpoints in
    the set, and the ``INVOKES`` edges sourced inside it.

    This is what a consumer serialises instead of the whole run — feed the result to
    :func:`~harnessx.graph.unfold.unfolded_records`.  Keeping it induced (both endpoints
    in the set) means the emitted edges never dangle.
    """
    ids = set(node_ids)
    return UnfoldedGraph(
        run_id=graph.run_id,
        session_id=graph.session_id,
        nodes=[n for n in graph.nodes if n.id in ids],
        edges=[e for e in graph.edges if e.source in ids and e.target in ids],
        invokes=[iv for iv in graph.invokes if iv.source in ids],
    )
