"""S5 — Graph-to-config transform.

Converts a GraphSnapshot back to a HarnessConfig dict so that
graph-edit proposals can be materialized as runnable YAML configs.

The runtime consumes YAML, not graph IR — this module is the bridge.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from .types import EdgeType, GraphSnapshot, NodeType

#: Serialization metadata keys carried back into the config dict (L7.1).
#: ``_after_`` is listed for completeness but handled separately (L7.3).
_METADATA_KEYS_TO_PRESERVE: tuple[str, ...] = (
    "_hook_", "_hooks_", "_order_", "_singleton_group_", "_code_hash",
    "_writes_slots_", "_reads_slots_",
    "_reads_event_fields_", "_writes_event_fields_",
    "_after_",
)


def graph_to_config_dict(snapshot: GraphSnapshot) -> dict[str, Any]:
    """Convert a GraphSnapshot to a dict suitable for HarnessConfig.

    The output dict has the same shape as a YAML-serialized config:
        {processors: [{_target_: ..., _hook_: ..., ...}, ...]}

    Key-presence semantics (L7.5): a metadata key present with a falsey value
    (``0`` / ``""`` / ``[]``) is still emitted — only missing keys and ``None``
    values are skipped.  Truthiness filtering here would silently turn
    "explicitly declared empty" back into "undeclared".

    Args:
        snapshot: A graph snapshot (typically after apply_edits).

    Returns:
        A dict with ``processors`` and other HarnessConfig fields.
    """
    processors: list[dict[str, Any]] = []

    # Collect processor nodes in hook+order order
    proc_nodes = [
        (nid, node) for nid, node in snapshot.nodes.items()
        if node.node_type == NodeType.PROCESSOR
    ]

    # Sort by hook → order within each hook (stable: equal keys keep their
    # insertion order, so same-bucket/same-order chains are not reshuffled)
    hook_order: dict[str, tuple] = {}
    for nid, node in proc_nodes:
        hook = node.metadata.get("_hook_", "*") or "*"
        order = node.metadata.get("_order_", 50)
        hook_order[nid] = (hook, order)

    proc_nodes.sort(key=lambda x: hook_order.get(x[0], ("*", 50)))

    for nid, node in proc_nodes:
        meta = node.metadata
        # L7.4: runtime-only nodes never materialize into the processors list
        if meta.get("_runtime_only"):
            continue

        proc_dict: dict[str, Any] = {"_target_": meta.get("_target_", node.label)}

        # L7.2: restore constructor kwargs (deepcopy — later mutation of the
        # output must not leak back into the graph, VM12g)
        ctor_kwargs = meta.get("_ctor_kwargs_")
        if isinstance(ctor_kwargs, dict):
            for k, v in deepcopy(ctor_kwargs).items():
                proc_dict[k] = v

        # legacy passthrough: pre-_ctor_kwargs_ snapshots stored ctor kwargs
        # as bare non-underscore metadata keys
        for key, val in meta.items():
            if not key.startswith("_"):
                proc_dict[key] = deepcopy(val)

        # L7.1 + L7.5: preserve declared metadata, falsey values included
        for key in _METADATA_KEYS_TO_PRESERVE:
            if key == "_after_":
                continue  # handled below (L7.3)
            if key in meta and meta[key] is not None:
                proc_dict[key] = deepcopy(meta[key])

        # L7.3: metadata ``_after_`` wins (it is the normalized singleton-group
        # list, and an explicit ``[]`` must survive); AFTER edges are only a
        # fallback for snapshots without the metadata key
        if "_after_" in meta and meta["_after_"] is not None:
            proc_dict["_after_"] = list(meta["_after_"])
        else:
            after_targets = _get_after_targets(nid, snapshot)
            if after_targets:
                proc_dict["_after_"] = after_targets

        processors.append(proc_dict)

    config_dict: dict[str, Any] = {"processors": processors}
    return config_dict


def _get_after_targets(node_id: str, snapshot: GraphSnapshot) -> list[str]:
    """Extract AFTER edge targets for a processor, returning singleton_group names."""
    targets: list[str] = []
    for edge in snapshot.edges:
        if edge.edge_type == EdgeType.AFTER and edge.source_id == node_id:
            target_node = snapshot.nodes.get(edge.target_id)
            if target_node is not None:
                sg = target_node.metadata.get("_singleton_group_", "")
                if sg:
                    targets.append(sg)
                else:
                    targets.append(edge.target_id)
    return targets


# ── round-trip verification ─────────────────────────────────────────────────


def verify_roundtrip(original_snapshot: GraphSnapshot) -> bool:
    """Check that graph → config → graph produces the same genotype hash."""
    from .identity import genotype_hash
    from .snapshot import to_graph

    config_dict = graph_to_config_dict(original_snapshot)
    try:
        from harnessx.core.harness import HarnessConfig
        config = HarnessConfig(**{k: v for k, v in config_dict.items()
                                  if k in ("processors",)})
        roundtrip = to_graph(config)
        return genotype_hash(original_snapshot) == genotype_hash(roundtrip)
    except Exception:
        return False
