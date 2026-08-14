# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""The unfolded graph U — the per-invocation record of what actually ran (v6 M4).

The static graph G has exactly one cycle: the ``LOOP_BACK`` edge from
``hook:task_end`` to ``hook:step_start``.  U materialises G as the run proceeds,
one node per **processor invocation**, and the cycle is broken because every
invocation is a distinct node.  U is therefore always a DAG — which is what makes
the later "which processors contributed to this failure" ancestors query
well-defined.

Invocation identity (THE TRAP).  ``{node_id}@t{round}`` is only a valid identity
if each distinct invocation gets a distinct id.  Taking ``round == State.step``
does NOT achieve that: a ``"*"``-registered processor fires on several hooks
within one step; ``after_tool`` dispatches at two sites; a step may hold several
tool calls, so the tool-side hooks fire repeatedly.  Collapsing those into one
node is exactly how a cycle silently reappears.  So the *round* tag here is a
**monotonically increasing invocation ordinal**, not the step; the step travels
as node metadata.  This keeps :func:`~harnessx.graph.types.unfolded_id`'s
digits-only contract intact (the ordinal is a non-negative int) while making
every invocation distinct, and — because the ordinal totally orders invocations —
every edge points from a lower ordinal to a higher one, so U is a DAG by
construction.

  1. two distinct invocations are never the same node (unique ordinal);
  2. the step is recoverable from the node (``UnfoldedNode.step``);
  3. :func:`unfolded_id` / :func:`parse_unfolded_id` stay the only id scheme.

Edges.  U carries only observed edges:

  * ``OBSERVED_CONTROL`` — the actual execution order within one hook firing
    (consecutive invocations, ``prev -> cur``);
  * ``OBSERVED_DATA``   — reaching-definitions data flow taken from
    ``State.slot_provenance``: each slot read is linked from the most-recent
    prior write of the same key (a ``delete`` kills the current definition).
    Because writes/reads are logged in execution (ordinal) order, the writer's
    ordinal is always strictly below the reader's — a data edge can never point
    backwards.  ``LOOP_BACK`` never appears in U; :func:`UnfoldRecorder.finalize`
    asserts it.

v6 M5.  Tools join U too.  Each ``tool_registry.execute`` at the runloop's tool
site mints one node (``tool:<name>``, hook ``"tool"``) from the SAME global
ordinal, so it interleaves with the processors around it; the ``before_tool`` →
``tool`` and ``tool`` → ``after_tool`` control edges are the observed execution
order of one tool call, and — like every U edge — point from lower ordinal to
higher.  ``spawn_subagent`` additionally carries an inter-layer ``INVOKES`` edge
from its tool node to the child run id (:class:`UnfoldedInvokes`), kept in a
separate collection because its target is a child run, not a node in this U.

v6 M12.  The message plane.  ``OBSERVED_DATA`` originally derived only from
``State.slot_provenance``.  On a stack that carries everything through the message
list that channel is empty, so U had a control plane and no data plane at all —
and because control edges never cross a hook firing, every causal cone collapsed
to the firing holding its anchor.  Messages are now a **second access plane in the
same reaching-definitions log**:

  * a processor invocation that adds a message to the event's tuple *writes* it,
    and one that drops a message *reads* it (a rewrite is both);
  * the model invocation *reads* the exact list handed to the provider and
    *writes* the assistant reply;
  * a tool invocation *writes* its result message.

Message identity is the message object's own identity: pass-through preserves it,
a rewrite allocates a new object — which is exactly a new definition. The recorder
pins every message it has keyed so an ``id`` can never be recycled onto a different
object.  Message accesses skip the ``slot_provenance`` cross-check (a message has
no provenance record); slot accesses keep it.

v6 M12 also mints one node per model call (``model:<name>``, hook ``"model"``) from
the same global ordinal, for the same reason tools got one in M5: the model is a
real consumer and producer on the data plane, and it belongs to none of the
processors around it.

Recording is free when nothing consumes it: the recorder is installed per run
only when :func:`unfold_enabled` is true (env ``HARNESSX_GHX_UNFOLD``).  With it
absent, ``ProcessorChain.process`` does a single context-var read and moves on.
"""

from __future__ import annotations

import datetime
import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from .types import EdgeType, parse_unfolded_id, unfolded_id

# String the static-node-id slot carries for a dispatched-but-ungraphed
# invocation (``extra_processors``, dict-plugins).  Matches ``repr(UNGRAPHED)``;
# no real graph node is ever the literal string ``"UNGRAPHED"``, so an unfolded
# id like ``UNGRAPHED@t7`` cannot collide with a graphed node's id.
_UNGRAPHED_BASE = "UNGRAPHED"

_OBSERVED_CONTROL = EdgeType.OBSERVED_CONTROL.value
_OBSERVED_DATA = EdgeType.OBSERVED_DATA.value
# v6 M5: the inter-layer edge from a parent tool node to a nested child run.  It
# is NOT a U observed edge — its target is a child run id, not a node in this U —
# so it lives in its own collection, apart from the OBSERVED_* edges that the
# DAG/finalize invariant governs.
_INVOKES = EdgeType.INVOKES.value

SCHEMA = "ghx-unfolded-v1"

_ENABLE_VALUES = frozenset({"1", "true", "on", "yes"})


def unfold_enabled() -> bool:
    """True when the unfolded graph should be recorded and persisted for a run.

    Read at call time (never cached at import), default OFF, so a run pays
    nothing unless ``HARNESSX_GHX_UNFOLD`` is explicitly set.
    """
    return os.environ.get("HARNESSX_GHX_UNFOLD", "").strip().lower() in _ENABLE_VALUES


# ── records ─────────────────────────────────────────────────────────────────


@dataclass
class UnfoldedNode:
    """One processor invocation in U.

    ``id`` is ``{static_node_id}@t{ordinal}`` (an :func:`unfolded_id`).  ``step``
    is the hook's ``event.step_id`` — the round the tracer attributes the firing
    to; it may sit one below ``State.step`` at ``step_end`` (the loop increments
    the step before dispatching that hook).  Data-edge endpoints carry their own
    ``State.step`` in edge metadata, so this field is metadata for recoverability,
    not the key data edges are validated against.
    """

    id: str
    static_node_id: str  # a graph node id, or ``"UNGRAPHED"``
    graphed: bool
    hook: str
    step: int
    ordinal: int
    label: str = ""


@dataclass
class UnfoldedEdge:
    """One observed edge in U (``observed_control`` or ``observed_data``)."""

    source: str
    target: str
    edge_type: str
    metadata: dict = field(default_factory=dict)


@dataclass
class UnfoldedInvokes:
    """One inter-layer ``INVOKES`` edge: a parent tool node → a nested child run.

    ``source`` is the parent's ``spawn_subagent`` tool invocation id (an
    :func:`unfolded_id` in THIS U); ``child_run_id`` is the child run's id — the
    only handle stable across layers, because each layer's U owns its own ordinal
    counter (both start at 0), so an ordinal cannot name a child.  The target is
    therefore a run id, not a node in this U, which is exactly why this edge is
    kept out of :attr:`UnfoldedGraph.edges` (the DAG/observed-edge set).
    """

    source: str
    child_run_id: str
    metadata: dict = field(default_factory=dict)


@dataclass
class UnfoldedGraph:
    """A loaded / finalized U: self-describing, streamable as JSONL."""

    run_id: str
    session_id: str
    nodes: list = field(default_factory=list)  # list[UnfoldedNode]
    edges: list = field(default_factory=list)  # list[UnfoldedEdge]
    invokes: list = field(default_factory=list)  # list[UnfoldedInvokes] (inter-layer)

    def node_ids(self) -> set:
        return {n.id for n in self.nodes}

    def edges_of_type(self, edge_type: str) -> list:
        return [e for e in self.edges if e.edge_type == edge_type]


# ── recorder ────────────────────────────────────────────────────────────────


def _actor_to_base(actor) -> tuple[str, bool]:
    """Map a resolved actor (node-id str / UNGRAPHED marker / None) to (base, graphed)."""
    if isinstance(actor, str):
        return actor, True
    return _UNGRAPHED_BASE, False


def _prov_actor_key(actor):
    """Normalise a ``SlotAccess.actor`` for comparison against a static node id."""
    if isinstance(actor, str):
        return actor
    if actor is None:
        return None
    return _UNGRAPHED_BASE  # the UNGRAPHED marker


class UnfoldRecorder:
    """Per-run recorder for the unfolded graph U.

    Duck-typed methods (:meth:`record_invocation`, :meth:`log_slot_access`) are
    driven from :mod:`harnessx.core.attribution` so the core never hard-imports
    the graph package.  One instance lives for the length of one ``Harness.run``.
    """

    def __init__(self, run_id: str, session_id: str) -> None:
        self.run_id = run_id
        self.session_id = session_id
        self._ordinal = 0
        self._nodes: list = []
        self._control_edges: list = []
        # inter-layer INVOKES edges (parent tool node → child run id), kept apart
        # from the observed control/data edges above.
        self._invokes: list = []
        # access log entries: {"ordinal", "kind", "slot_key", "step", "inv_id",
        # "plane"}, appended in execution order (== ordinal order across
        # invocations).  Both planes share this log: ``_build_data_edges`` is
        # reaching-definitions over ``slot_key`` and does not care what a key names.
        self._accesses: list = []
        self._base_by_ordinal: dict = {}
        # v6 M12: message identity.  ``id(message) -> key``, with every keyed
        # message pinned in ``_msg_pin`` so CPython can never recycle an id onto a
        # different object and fabricate a data edge between unrelated messages.
        self._msg_keys: dict = {}
        self._msg_pin: list = []

    # -- recording (called around each processor invocation) -----------------

    def record_invocation(self, actor, processor, hook: str, step: int, prev_in_firing) -> str:
        """Record one invocation node; link ``prev_in_firing`` with OBSERVED_CONTROL.

        ``prev_in_firing`` is the previous invocation's id *within the same hook
        firing* (``None`` for the first), so control edges never cross firing
        boundaries.  Returns the new invocation's unfolded id.
        """
        base, graphed = _actor_to_base(actor)
        ordinal = self._ordinal
        self._ordinal += 1
        uid = unfolded_id(base, ordinal)
        self._nodes.append(
            UnfoldedNode(
                id=uid,
                static_node_id=base,
                graphed=graphed,
                hook=hook,
                step=int(step),
                ordinal=ordinal,
                label=type(processor).__name__ if processor is not None else "",
            )
        )
        self._base_by_ordinal[ordinal] = base
        if prev_in_firing is not None:
            self._control_edges.append(
                UnfoldedEdge(
                    source=prev_in_firing,
                    target=uid,
                    edge_type=_OBSERVED_CONTROL,
                    metadata={"hook": hook},
                )
            )
        return uid

    def record_tool_invocation(self, tool_name: str, step: int, prev_in_firing) -> str:
        """Record one tool-execution node in U; link ``prev_in_firing`` with OBSERVED_CONTROL.

        A tool is not a processor: it runs at the runloop's tool site, between the
        ``before_tool`` and ``after_tool`` firings of one tool call.  It carries the
        canonical ``tool:<name>`` node id (the same id :mod:`~harnessx.graph.footprint`
        and :mod:`~harnessx.graph.observer` use for tools), and it draws the SAME
        global ordinal as processor invocations, so it interleaves with them and —
        because the ordinal only grows — every edge to or from it points forward and
        U stays a DAG.  ``prev_in_firing`` is the last ``before_tool`` invocation's
        id (``None`` when no ``before_tool`` processor ran, in which case no incoming
        control edge is invented); the ``tool → first after_tool`` edge is added
        afterwards by :meth:`link_control`.
        """
        base = f"tool:{tool_name}"
        ordinal = self._ordinal
        self._ordinal += 1
        uid = unfolded_id(base, ordinal)
        # graphed=True: a tool carries a structured ``tool:`` node id, NOT the
        # UNGRAPHED sentinel — even though tools are not (yet) emitted as static
        # nodes in G.  The flag distinguishes named nodes from the dispatched-but-
        # unnamed catch-all; a tool is named.
        self._nodes.append(
            UnfoldedNode(
                id=uid,
                static_node_id=base,
                graphed=True,
                hook="tool",
                step=int(step),
                ordinal=ordinal,
                label=tool_name,
            )
        )
        self._base_by_ordinal[ordinal] = base
        if prev_in_firing is not None:
            self._control_edges.append(
                UnfoldedEdge(
                    source=prev_in_firing,
                    target=uid,
                    edge_type=_OBSERVED_CONTROL,
                    metadata={"hook": "before_tool"},
                )
            )
        return uid

    def record_model_invocation(self, model_name: str, step: int, prev_in_firing) -> str:
        """Record one model-call node in U; link ``prev_in_firing`` with OBSERVED_CONTROL.

        Exactly the shape of :meth:`record_tool_invocation`, for the same reason: the
        provider call is not a processor dispatch, it sits between the ``before_model``
        and ``after_model`` firings, and it is the one place the whole assembled message
        list is consumed.  Node id is ``model:<name>``, hook ``"model"``, drawn from the
        SAME global ordinal so it interleaves with the processors around it and every
        edge still points forward.  ``prev_in_firing`` is the last ``before_model``
        invocation's id (``None`` when none ran — then no incoming edge is invented);
        the ``model → first after_model`` edge is added afterwards by :meth:`link_control`.
        """
        base = f"model:{model_name}" if model_name else "model:unknown"
        ordinal = self._ordinal
        self._ordinal += 1
        uid = unfolded_id(base, ordinal)
        self._nodes.append(
            UnfoldedNode(
                id=uid,
                static_node_id=base,
                graphed=True,
                hook="model",
                step=int(step),
                ordinal=ordinal,
                label=model_name or "unknown",
            )
        )
        self._base_by_ordinal[ordinal] = base
        if prev_in_firing is not None:
            self._control_edges.append(
                UnfoldedEdge(
                    source=prev_in_firing,
                    target=uid,
                    edge_type=_OBSERVED_CONTROL,
                    metadata={"hook": "before_model"},
                )
            )
        return uid

    def link_control(self, source_id, target_id, hook: str) -> None:
        """Add an OBSERVED_CONTROL edge ``source_id → target_id`` between two nodes.

        Used only for the ``tool → first after_tool`` bridge; both endpoints are
        already recorded nodes, and the tool's ordinal is below the after_tool
        node's, so the edge points forward.  Self-links / missing endpoints are
        dropped rather than emitted.
        """
        if source_id is None or target_id is None or source_id == target_id:
            return
        self._control_edges.append(
            UnfoldedEdge(
                source=source_id,
                target=target_id,
                edge_type=_OBSERVED_CONTROL,
                metadata={"hook": hook},
            )
        )

    def node_count(self) -> int:
        """Number of invocation nodes recorded so far (a boundary marker for the runloop)."""
        return len(self._nodes)

    def node_id_at(self, index: int):
        """Id of the node recorded at ``index`` in append order, or ``None`` if out of range."""
        if 0 <= index < len(self._nodes):
            return self._nodes[index].id
        return None

    def record_invokes(self, source_id: str, child_run_id: str, metadata=None) -> None:
        """Record an inter-layer INVOKES edge: parent tool node → child run id.

        ``source_id`` is the parent ``spawn_subagent`` tool invocation's id in this
        U; ``child_run_id`` is the child run's actual id (its own U file's key).
        This edge is held apart from the observed control/data edges: its target is
        not a node in this U, so it never enters the DAG check nor :meth:`finalize`'s
        observed-edge assertion.
        """
        if source_id is None or not child_run_id:
            return
        self._invokes.append(
            UnfoldedInvokes(
                source=source_id,
                child_run_id=child_run_id,
                metadata=dict(metadata) if metadata else {},
            )
        )

    def log_slot_access(self, slot_key: str, kind: str, invocation_id: str, step: int) -> None:
        """Log a slot access performed by the currently-executing invocation."""
        self._log_access(slot_key, kind, invocation_id, step, "slot")

    def message_key(self, message) -> str:
        """Stable per-run key naming one message object (v6 M12).

        Keyed on object identity, so a message passed through unchanged keeps its key
        while a rewritten one gets a fresh key — the reaching-definitions engine then
        reads a rewrite as a new definition, which is what it is.  The message is
        pinned for the recorder's lifetime: an unpinned object could be collected and
        its ``id`` handed to an unrelated message, silently welding two definitions
        together.
        """
        mid = id(message)
        key = self._msg_keys.get(mid)
        if key is None:
            key = f"msg:{len(self._msg_keys)}:{getattr(message, 'role', '?')}"
            self._msg_keys[mid] = key
            self._msg_pin.append(message)
        return key

    def log_message_access(self, message, kind: str, invocation_id, step: int) -> None:
        """Log a message-plane access by ``invocation_id`` (v6 M12); no-op if it is ``None``.

        Callers pass the invocation id explicitly rather than relying on the current-
        invocation context var, because the two message producers outside the processor
        dispatcher — the model call and the tool call — append their messages after their
        own invocation scope has closed.

        **A write is the FIRST sight of a message object, and only that.**  A context
        assembler that pulls history out of ``State`` into an empty event tuple looks,
        to the dispatcher's diff, exactly like a processor that produced all of it; were
        that recorded, last-write-wins would hand the assembler authorship of every
        message each step and erase the tool → model and model → model edges that are
        the whole point.  Identity is the definition: an object is defined once, when it
        first exists, and every later re-appearance is carriage, not authorship.
        """
        if invocation_id is None:
            return
        if kind == "write" and id(message) in self._msg_keys:
            return
        self._log_access(self.message_key(message), kind, invocation_id, step, "message")

    def _log_access(self, key: str, kind: str, invocation_id: str, step: int, plane: str) -> None:
        _, ordinal = parse_unfolded_id(invocation_id)
        self._accesses.append(
            {
                "ordinal": ordinal,
                "kind": kind,
                "slot_key": key,
                "step": int(step),
                "inv_id": invocation_id,
                "plane": plane,
            }
        )

    # -- finalisation --------------------------------------------------------

    def finalize(self, state=None) -> UnfoldedGraph:
        """Build the OBSERVED_DATA edges and return the complete U.

        ``state`` (when given) supplies ``slot_provenance`` for a cross-check:
        every data edge must correspond to a real writer/reader pair there, or it
        is dropped rather than invented.
        """
        data_edges = self._build_data_edges(state)
        edges = list(self._control_edges) + data_edges
        # U is behaviour, not structure: only the two observed edge types ever
        # appear.  LOOP_BACK (the sole cycle in G) must never leak in — assert it.
        assert all(e.edge_type in (_OBSERVED_CONTROL, _OBSERVED_DATA) for e in edges), (
            "unfolded graph U must contain only observed edges"
        )
        return UnfoldedGraph(
            run_id=self.run_id,
            session_id=self.session_id,
            nodes=list(self._nodes),
            edges=edges,
            invokes=list(self._invokes),
        )

    def _build_data_edges(self, state) -> list:
        prov = getattr(state, "slot_provenance", None) or {} if state is not None else {}
        by_key: dict = {}
        for a in self._accesses:
            by_key.setdefault(a["slot_key"], []).append(a)

        edges: list = []
        seen: set = set()
        for key, accs in by_key.items():
            last_write = None  # most-recent prior write access (a dict), or None
            for a in accs:
                kind = a["kind"]
                if kind == "write":
                    last_write = a
                elif kind == "delete":
                    last_write = None  # the definition is gone; later reads see None
                elif kind == "read":
                    if last_write is None:
                        continue
                    # Same-invocation write→read is intra-invocation, not a data
                    # edge between nodes (and would be a self-loop); require a
                    # strictly-earlier ordinal.
                    if last_write["ordinal"] >= a["ordinal"]:
                        continue
                    edge_key = (last_write["inv_id"], a["inv_id"], key)
                    if edge_key in seen:
                        continue
                    # The slot_provenance cross-check applies to the slot plane only:
                    # a message has no provenance record to be corroborated against,
                    # and the dispatcher/runloop sites that log message accesses are
                    # themselves the primary observation (v6 M12).
                    plane = a.get("plane", "slot")
                    if plane == "slot" and state is not None and not self._provenance_supports(prov, key, last_write, a):
                        continue
                    seen.add(edge_key)
                    edges.append(
                        UnfoldedEdge(
                            source=last_write["inv_id"],
                            target=a["inv_id"],
                            edge_type=_OBSERVED_DATA,
                            metadata={
                                "plane": plane,
                                "slot_key": key,
                                "writer": {
                                    "static_node_id": self._base_by_ordinal[last_write["ordinal"]],
                                    "step": last_write["step"],
                                },
                                "reader": {
                                    "static_node_id": self._base_by_ordinal[a["ordinal"]],
                                    "step": a["step"],
                                },
                            },
                        )
                    )
        return edges

    def _provenance_supports(self, prov, key, writer, reader) -> bool:
        p = prov.get(key)
        if p is None:
            return False
        w_static = self._base_by_ordinal[writer["ordinal"]]
        r_static = self._base_by_ordinal[reader["ordinal"]]
        return _access_present(p.writers, w_static, writer["step"]) and _access_present(
            p.readers, r_static, reader["step"]
        )


def _access_present(access_list, static_node_id, step) -> bool:
    for acc in access_list:
        if _prov_actor_key(acc.actor) == static_node_id and acc.step == step:
            return True
    return False


# ── persistence ─────────────────────────────────────────────────────────────


def _iso_now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _node_record(n: UnfoldedNode) -> dict:
    return {
        "kind": "node",
        "id": n.id,
        "static_node_id": n.static_node_id,
        "graphed": n.graphed,
        "hook": n.hook,
        "step": n.step,
        "ordinal": n.ordinal,
        "label": n.label,
    }


def _edge_record(e: UnfoldedEdge) -> dict:
    return {
        "kind": "edge",
        "edge_type": e.edge_type,
        "source": e.source,
        "target": e.target,
        "metadata": e.metadata,
    }


def _invokes_record(iv: UnfoldedInvokes) -> dict:
    return {
        "kind": "invokes",
        "edge_type": _INVOKES,
        "source": iv.source,
        "child_run_id": iv.child_run_id,
        "metadata": iv.metadata,
    }


def unfolded_records(graph: UnfoldedGraph):
    """Yield the JSONL records for ``graph`` (meta, then nodes, edges, invokes)."""
    yield {
        "kind": "meta",
        "schema": SCHEMA,
        "run_id": graph.run_id,
        "session_id": graph.session_id,
        "created": _iso_now(),
        "node_count": len(graph.nodes),
        "edge_count": len(graph.edges),
        "invokes_count": len(graph.invokes),
    }
    for n in graph.nodes:
        yield _node_record(n)
    for e in graph.edges:
        yield _edge_record(e)
    for iv in graph.invokes:
        yield _invokes_record(iv)


def unfolded_path(base_dir: str, session_id: str, run_id: str) -> Path:
    """Path U is written to: ``{base_dir}/{session_id}/{run_id}_unfolded.jsonl``."""
    return Path(base_dir) / session_id / f"{run_id}_unfolded.jsonl"


def write_unfolded(graph: UnfoldedGraph, base_dir: str = "sessions") -> Path:
    """Write ``graph`` as JSONL under the HarnessJournal-style session layout."""
    path = unfolded_path(base_dir, graph.session_id, graph.run_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for rec in unfolded_records(graph):
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return path


def load_unfolded(path) -> UnfoldedGraph:
    """Read a U JSONL file back into an :class:`UnfoldedGraph` (round-trip)."""
    run_id = ""
    session_id = ""
    nodes: list = []
    edges: list = []
    invokes: list = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            kind = rec.get("kind")
            if kind == "meta":
                run_id = rec.get("run_id", "")
                session_id = rec.get("session_id", "")
            elif kind == "invokes":
                invokes.append(
                    UnfoldedInvokes(
                        source=rec["source"],
                        child_run_id=rec["child_run_id"],
                        metadata=rec.get("metadata", {}),
                    )
                )
            elif kind == "node":
                nodes.append(
                    UnfoldedNode(
                        id=rec["id"],
                        static_node_id=rec["static_node_id"],
                        graphed=rec["graphed"],
                        hook=rec["hook"],
                        step=rec["step"],
                        ordinal=rec["ordinal"],
                        label=rec.get("label", ""),
                    )
                )
            elif kind == "edge":
                edges.append(
                    UnfoldedEdge(
                        source=rec["source"],
                        target=rec["target"],
                        edge_type=rec["edge_type"],
                        metadata=rec.get("metadata", {}),
                    )
                )
    return UnfoldedGraph(run_id=run_id, session_id=session_id, nodes=nodes, edges=edges, invokes=invokes)
