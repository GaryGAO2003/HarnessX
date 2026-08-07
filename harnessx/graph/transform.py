"""S5 — Graph-to-config transform.

Converts a GraphSnapshot back to a HarnessConfig dict so that
graph-edit proposals can be materialized as runnable YAML configs.

The runtime consumes YAML, not graph IR — this module is the bridge.
"""

from __future__ import annotations

from typing import Any

from .types import EdgeType, GraphSnapshot, NodeType


def graph_to_config_dict(snapshot: GraphSnapshot) -> dict[str, Any]:
    """Convert a GraphSnapshot to a dict suitable for HarnessConfig.

    The output dict has the same shape as a YAML-serialized config:
        {processors: [{_target_: ..., _hook_: ..., ...}, ...]}

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

    # Sort by hook → order within each hook
    hook_order: dict[str, int] = {}
    for nid, node in proc_nodes:
        hook = node.metadata.get("_hook_", "*")
        order = node.metadata.get("_order_", 50)
        hook_order[nid] = (hook, order)

    proc_nodes.sort(key=lambda x: hook_order.get(x[0], ("*", 50)))

    for nid, node in proc_nodes:
        proc_dict: dict[str, Any] = {"_target_": node.metadata.get("_target_", node.label)}
        for key in ("_hook_", "_order_", "_singleton_group_", "_code_hash"):
            if key in node.metadata and node.metadata[key]:
                proc_dict[key] = node.metadata[key]
        # AFTER edges → _after_ list
        after_targets = _get_after_targets(nid, snapshot)
        if after_targets:
            proc_dict["_after_"] = after_targets
        # Other kwargs from metadata
        for key, val in node.metadata.items():
            if key.startswith("_") or key == "_target_":
                continue
            proc_dict[key] = val
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
