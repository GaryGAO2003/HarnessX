"""Δ7 side-product — deterministic bill of materials for a snapshot.

A small, sorted manifest of what a candidate is made of: persistent and
runtime processors (with identity-relevant metadata), slots, and edge
counts by Δ7 family and data channel.  Deterministic by construction so it
can ride along in shadow-ledger records and diff cleanly between parents
and candidates.
"""

from __future__ import annotations

from .types import GraphSnapshot, NodeType, edge_family


def graph_bom(snapshot: GraphSnapshot) -> dict:
    """Build the bill of materials (sorted, JSON-ready)."""

    def _procs(nodes: dict) -> list:
        out = []
        for nid in sorted(nodes):
            node = nodes[nid]
            if node.node_type is not NodeType.PROCESSOR:
                continue
            meta = node.metadata
            out.append({
                "node_id": nid,
                "target": meta.get("_target_", ""),
                "code_hash": meta.get("_code_hash", ""),
                "singleton_group": meta.get("_singleton_group_", ""),
                "order": meta.get("_order_"),
                "bucket": meta.get("_hook_", ""),
            })
        return out

    def _slots(nodes: dict) -> list:
        return sorted(
            node.metadata.get("slot_name", node.label)
            for node in nodes.values()
            if node.node_type is NodeType.SLOT
        )

    edges_by_family: dict = {}
    data_channels: dict = {}
    for edge in [*snapshot.edges, *snapshot.runtime_edges]:
        fam = edge_family(edge.edge_type).value
        edges_by_family[fam] = edges_by_family.get(fam, 0) + 1
        channel = edge.metadata.get("data_channel")
        if channel:
            data_channels[channel] = data_channels.get(channel, 0) + 1

    return {
        "processors": _procs(snapshot.nodes),
        "runtime_processors": _procs(snapshot.runtime_nodes),
        "slots": _slots(snapshot.nodes),
        "runtime_slots": _slots(snapshot.runtime_nodes),
        "bundles": sorted(
            nid for nid, n in snapshot.nodes.items()
            if n.node_type is NodeType.BUNDLE
        ),
        "edges_by_family": dict(sorted(edges_by_family.items())),
        "data_channels": dict(sorted(data_channels.items())),
    }
