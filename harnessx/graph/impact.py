"""S5 — Impact analysis: forward slice, danger edge set, influence cone.

When a graph edit is proposed, these functions determine which edges
and tasks could be affected — the basis for selective retest decisions.

Layers
------
1. **reach** — structural, definitive, free.
   Forward slice through declared edges from the changed node.
   Answers: "what could be affected?"

2. **immune** — structural negative, exchangeable for budget.
   Nodes provably unreachable from the change.

3. **magnitude** — needs edge weights + historical evidence.
   Graph only provides attachment points for this layer.
"""

from __future__ import annotations

from collections import deque

from .edit import GraphEdit
from .types import EdgeType, GraphSnapshot


# ── forward slice ───────────────────────────────────────────────────────────


# Edges that propagate *impact* (not just placement).
# ATTACHED_TO is WHERE a processor runs, not WHAT it affects.
# AFTER, WRITES_TO, READS_FROM carry semantic dependency.
_IMPACT_EDGE_TYPES = frozenset({
    EdgeType.AFTER,
    EdgeType.WRITES_TO,
    EdgeType.READS_FROM,
    EdgeType.LOOP_BACK,
})


def forward_slice(
    snapshot: GraphSnapshot,
    start_node_ids: set[str],
) -> set[str]:
    """Compute the forward-reachable impact cone from start nodes.

    Only traverses *semantic* edges (AFTER, WRITES_TO, READS_FROM).
    ATTACHED_TO edges are structural placement — they say where a
    processor is installed, not what it affects downstream.

    Args:
        snapshot: The declared graph.
        start_node_ids: Nodes whose forward reachable cone to compute.

    Returns:
        Node_ids reachable via semantic edges from any start node.
    """
    reachable: set[str] = set(start_node_ids)
    queue = deque(start_node_ids)

    # Build adjacency list — semantic edges only
    adj: dict[str, list[str]] = {}
    for edge in snapshot.edges:
        if edge.edge_type.value.startswith("observed_"):
            continue
        if edge.edge_type in _IMPACT_EDGE_TYPES:
            adj.setdefault(edge.source_id, []).append(edge.target_id)

    while queue:
        current = queue.popleft()
        for neighbor in adj.get(current, []):
            if neighbor not in reachable:
                reachable.add(neighbor)
                queue.append(neighbor)

    return reachable


# ── danger edge set ─────────────────────────────────────────────────────────


def danger_edge_set(
    edits: list[GraphEdit],
    snapshot: GraphSnapshot,
) -> tuple[set[str], set[str]]:
    """Compute the set of nodes and edges that could change behaviour.

    For each edit, the danger set includes:
    - The directly affected node
    - All nodes forward-reachable from the affected node
    - All edges incident on any affected node

    Args:
        edits: The proposed graph edits.
        snapshot: The parent graph before edits.

    Returns:
        (danger_node_ids, danger_edge_keys) — the union across all edits.
    """
    affected_nodes: set[str] = set()

    for edit in edits:
        affected_nodes |= edit.affected_node_ids()

    # Forward reachable from affected nodes
    reachable = forward_slice(snapshot, affected_nodes)
    all_danger_nodes = affected_nodes | reachable

    # All edges incident on danger nodes
    danger_edges: set[str] = set()
    for edge in snapshot.edges:
        if edge.edge_type.value.startswith("observed_"):
            continue
        if edge.source_id in all_danger_nodes or edge.target_id in all_danger_nodes:
            danger_edges.add(f"{edge.source_id}→{edge.target_id}")

    return all_danger_nodes, danger_edges


# ── influence cone ──────────────────────────────────────────────────────────


def influence_cone(
    snapshot: GraphSnapshot,
    node_id: str,
) -> dict[str, set[str]]:
    """Compute the three-tier influence cone for a single node.

    Args:
        snapshot: The declared graph.
        node_id: The node whose influence to analyze.

    Returns:
        Dict with keys:
        - "scope": node_ids definitely reachable (forward slice)
        - "immune": node_ids provably unaffected (complement of scope)
        - "source": the starting node_id
    """
    scope = forward_slice(snapshot, {node_id})
    all_nodes = set(snapshot.nodes)
    immune = all_nodes - scope

    return {
        "source": {node_id},
        "scope": scope,
        "immune": immune,
    }


# ── footprint intersection ──────────────────────────────────────────────────


def intersects_footprint(
    danger_nodes: set[str],
    danger_edges: set[str],
    footprint_nodes: set[str],
    footprint_edges: set[str],
) -> bool:
    """Check whether a danger set intersects a task footprint.

    True → the task may be affected by the edit → retest needed.
    False → the task is provably unaffected → inherit parent score.

    This is the core decision function for selective retest (S5).
    """
    if danger_nodes & footprint_nodes:
        return True
    if danger_edges & footprint_edges:
        return True
    return False
