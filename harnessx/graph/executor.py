# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""Graph-native processor dispatch (v6 M2b).

``GraphProcessorExecutor`` answers "which processors run at hook H, in what
order" by READING the graph — the ``EXECUTES_BEFORE`` chains ``to_graph``
already emits per bucket — instead of re-running the runtime router's
``stable_topological_sort``.  Those chains were themselves built by that same
sort (I7), so the graph already encodes the execution order; the executor only
reconstructs the linear order from the persisted edges and applies the
``"*"``-bucket concatenation rule ``runloop.get_procs`` uses (``"*"`` first,
then the hook-specific bucket).

Instances come from the node-id→instance binding the runtime records AS it
instantiates (``_instantiate_runtime``).  The executor NEVER instantiates a
processor — doing so would mint a second, divergent set of stateful instances
(the hazard ``build_node_binding`` carries); it only maps ids the runtime
already bound.

Ungraphed dispatch (``UNGRAPHED``): some processors are dispatched by the
runloop yet have NO graph node — ``extra_processors`` injected after
``_instantiate_runtime``, and dict-plugin processors the pure-read graph cannot
enumerate.  Legacy ``get_procs`` appends them to the END of their bucket (in
registration order); the graph has nothing to say about their order.  They are
recorded in the binding under the explicit ``UNGRAPHED`` marker (never a fake
node id), tagged with their bucket, and dispatched after the graph-derived
members — so they stay visible as ungraphed rather than silently vanishing.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .types import EdgeType

if TYPE_CHECKING:
    from .types import GraphSnapshot


# The two skeleton hooks declared but never dispatched: SKELETON_HOOK_NAMES has
# 10 entries, PROCESSOR_HOOK_NAMES the 8 real ones.  A processor can only reach
# these via an ATTACHED_TO edge minted from an explicit ``hooks=["model"]``
# declaration that slips past the wildcard seal — the runloop dispatches
# neither, so the executor refuses them rather than become the seam that makes
# them live.
_NON_DISPATCH_HOOKS: frozenset = frozenset({"model", "tool"})

_STAR_BUCKET = "*"


class _Ungraphed:
    """Marker for a binding entry that is dispatched but has no graph node."""

    __slots__ = ()

    def __repr__(self) -> str:
        return "UNGRAPHED"


# Singleton marker — producers (harness.py) and this consumer compare it by
# identity, so both sides must reference this one object.
UNGRAPHED = _Ungraphed()


class NonDispatchHookError(ValueError):
    """Raised when the executor is asked for the ``model`` or ``tool`` hook."""


class GraphProcessorExecutor:
    """Resolve a hook's processor list from the graph + the runtime binding.

    ``binding`` is an iterable of ``(node_id, bucket, proc)`` triples the
    runtime records as it instantiates — the same instances the router placed
    in ``proc_dict``.  ``bucket`` is the processor's registration bucket
    (``env.reg.hook``); order within a bucket comes purely from the graph.  A
    triple whose ``node_id`` is :data:`UNGRAPHED` is a dispatched-but-ungraphed
    processor, ordered after the graph-derived members in registration order.
    """

    def __init__(self, snapshot: "GraphSnapshot", binding) -> None:
        self._proc_by_node: dict = {}
        self._graph_members: dict = {}  # bucket -> [node_id] (registration order)
        self._ungraphed: dict = {}  # bucket -> [proc] (registration order)
        for node_id, bucket, proc in binding:
            if node_id is UNGRAPHED:
                self._ungraphed.setdefault(bucket, []).append(proc)
            else:
                self._proc_by_node[node_id] = proc
                self._graph_members.setdefault(bucket, []).append(node_id)

        # EXECUTES_BEFORE successor edges per bucket, straight from the graph's
        # mixed (deployment) chain in runtime_edges.  metadata["hook"] is the
        # bucket the chain was emitted for; a single-node bucket emits no edge.
        self._succ_by_bucket: dict = {}
        for edge in snapshot.runtime_edges:
            if edge.edge_type is not EdgeType.EXECUTES_BEFORE:
                continue
            bucket = edge.metadata.get("hook")
            self._succ_by_bucket.setdefault(bucket, {})[edge.source_id] = edge.target_id

        self._order_cache: dict = {}

    def _ordered_nodes(self, bucket: str) -> list:
        cached = self._order_cache.get(bucket)
        if cached is not None:
            return cached
        members = self._graph_members.get(bucket, [])
        if len(members) <= 1:
            self._order_cache[bucket] = list(members)
            return self._order_cache[bucket]

        member_set = set(members)
        raw_succ = self._succ_by_bucket.get(bucket, {})
        succ = {a: b for a, b in raw_succ.items() if a in member_set and b in member_set}
        pred_set = set(succ.values())

        # A linear chain has exactly one in-degree-0 head; iterate members in
        # registration order so any pathological multi-head / detached case
        # degrades to a stable, deterministic order (and surfaces in parity,
        # rather than silently mis-ordering).
        ordered: list = []
        seen: set = set()
        for head in (n for n in members if n not in pred_set):
            cur = head
            while cur is not None and cur not in seen:
                ordered.append(cur)
                seen.add(cur)
                cur = succ.get(cur)
        for node_id in members:  # defensive: reach nodes a broken chain skipped
            if node_id not in seen:
                ordered.append(node_id)
                seen.add(node_id)

        self._order_cache[bucket] = ordered
        return ordered

    def _bucket_procs(self, bucket: str) -> list:
        procs = [self._proc_by_node[n] for n in self._ordered_nodes(bucket)]
        procs.extend(self._ungraphed.get(bucket, []))
        return procs

    def procs_for(self, hook: str) -> list:
        """Ordered processor instances for ``hook`` (``"*"`` bucket first).

        Refuses the ``model`` / ``tool`` hooks, which the runloop never
        dispatches.
        """
        if hook in _NON_DISPATCH_HOOKS:
            raise NonDispatchHookError(
                f"hook {hook!r} is never dispatched by the runloop; "
                "the graph executor refuses to resolve processors for it"
            )
        return self._bucket_procs(_STAR_BUCKET) + self._bucket_procs(hook)


def build_graph_executor(config, binding) -> GraphProcessorExecutor:
    """Build an executor from a HarnessConfig and the runtime's node→instance binding.

    ``to_graph`` is a pure read — it walks serialized dicts and already-live
    runtime instances and never instantiates a processor — so building the
    snapshot here cannot create a second set of instances.
    """
    from .snapshot import to_graph

    return GraphProcessorExecutor(to_graph(config), binding)
