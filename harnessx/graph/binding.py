"""Graph node ⇄ live processor binding (M2a).

``build_node_binding(config)`` maps every PROCESSOR node id in
``to_graph(config)`` — persistent ``proc:`` and runtime-overlay ``rt:`` alike —
to the live processor instance the runtime would execute for that node.

Node ids come from the single authority (``assign_processor_node_ids``), so the
binding cannot drift from the graph.  Persistent instances are built through the
same ``_instantiate_proc`` seam the run path uses (``harness.py`` →
``_instantiate_runtime``), keyed by the config record each id was derived from —
NOT by the per-hook topological execution order, which diverges from config
order whenever ``_order`` / ``_after`` reshuffle a bucket.  Runtime-overlay
instances already live on the config, so they are bound directly.

A processor whose spec cannot be instantiated is absent from the binding, which
matches the runtime: ``_instantiate_runtime`` drops the same spec (harness.py).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .snapshot import assign_processor_node_ids

if TYPE_CHECKING:
    from harnessx.core.harness import HarnessConfig


def build_node_binding(config: "HarnessConfig") -> "dict[str, object]":
    """Bind PROCESSOR node ids to live processor instances.

    Returns ``{node_id: processor}`` covering both the persistent (``proc:``)
    and runtime-overlay (``rt:``) processors of ``to_graph(config)``.  Node ids
    are derived from :func:`assign_processor_node_ids`; each id maps to the
    instance built from (persistent) or carried by (runtime) the exact config
    record the id was assigned to, independent of per-hook execution order.
    """
    from ..core.harness import _instantiate_proc

    ids = assign_processor_node_ids(config)
    binding: "dict[str, object]" = {}

    # Persistent processors: instantiate the SerializedReg dict its node id was
    # derived from — the same seam _instantiate_runtime uses (harness.py).  A
    # dropped/unbuildable spec is absent here exactly as it is at runtime.
    for dict_ref, node_id in ids.persistent:
        inst = _instantiate_proc(dict_ref)
        if inst is None:
            continue
        binding[node_id] = inst

    # Runtime-overlay processors: the live instance already exists on the config.
    for proc, node_id in ids.runtime:
        binding[node_id] = proc

    return binding
