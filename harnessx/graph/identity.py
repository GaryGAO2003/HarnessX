"""Content-addressed graph identity — genotype and phenotype hashing.

Genotype hash (structural, Nix-style)
    SHA-256 over a canonical JSON form of nodes + declared edges.
    Two configs with the same genotype are structurally identical and
    interchangeable — used for duplicate detection.

Phenotype hash (observation-aware, Build-Systems-à-la-Carte early cutoff)
    Incorporates observation edges from runtime traces.  Two configs with
    the same phenotype produce the same observable behaviour — used for
    selective retest inheritance (S5).
"""

from __future__ import annotations

import hashlib
import json

from .types import GraphSnapshot, Node, Edge


def genotype_hash(snapshot: GraphSnapshot) -> str:
    """Compute the genotype (structural) hash of a graph snapshot.

    Covers ONLY ``snapshot.nodes`` / ``snapshot.edges`` — the persistent graph.
    The runtime overlay (runtime_nodes / runtime_edges) is invisible here (I6),
    so changing runtime-only processors never moves the genotype.

    Returns:
        Hex-encoded SHA-256 digest (also cached on the snapshot).
    """
    h = _hash_nodes_edges(snapshot.nodes, snapshot.edges, include_observed=False)
    snapshot.genotype_hash = h
    return h


def deployment_hash(snapshot: GraphSnapshot) -> str:
    """Compute the deployment hash — genotype + runtime overlay (L6.3).

    Merges the main graph with the runtime overlay (runtime nodes/edges,
    including the L5.6 mixed EXECUTES_BEFORE chain) but EXCLUDES observed
    edges: those are runtime trace data, not deployment configuration.

    Returns:
        Hex-encoded SHA-256 digest (also cached on the snapshot).
    """
    all_nodes = {**snapshot.nodes, **snapshot.runtime_nodes}
    all_edges = snapshot.edges + snapshot.runtime_edges
    h = _hash_nodes_edges(all_nodes, all_edges, include_observed=False)
    snapshot.deployment_hash = h
    return h


def phenotype_hash(snapshot: GraphSnapshot) -> str:
    """Compute the phenotype hash — deployment + observed edges (L6.5).

    Layering: genotype ⊆ deployment ⊆ phenotype (deployment = genotype +
    runtime overlay; phenotype = deployment + observed edges from S4 traces).

    Returns:
        Hex-encoded SHA-256 digest (also cached on the snapshot).
    """
    all_nodes = {**snapshot.nodes, **snapshot.runtime_nodes}
    all_edges = snapshot.edges + snapshot.runtime_edges
    h = _hash_nodes_edges(all_nodes, all_edges, include_observed=True)
    snapshot.phenotype_hash = h
    return h


# ── internals ───────────────────────────────────────────────────────────────


def _sha256(canonical: str) -> str:
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _hash_nodes_edges(nodes: dict, edges: list, *, include_observed: bool) -> str:
    """Shared hashing kernel over explicit nodes/edges (L6.6).

    All three public hashes go through this — genotype passes the main graph,
    deployment/phenotype pass the merged main + runtime overlay.
    """
    return _sha256(_canonical_form(nodes, edges, include_observed=include_observed))


def _canonical_form(nodes: dict, edges: list, *, include_observed: bool = False) -> str:
    """Produce a stable, sort-order-normalized JSON string for hashing.

    Args:
        nodes: node_id → Node mapping to canonicalize.
        edges: edges to canonicalize.
        include_observed: If False, OBSERVED_CONTROL and OBSERVED_DATA
            edges are excluded.  Set True for phenotype hashing.
    """
    canonical_nodes = []
    for node_id in sorted(nodes):
        canonical_nodes.append(_node_canonical(nodes[node_id]))

    canonical_edges = []
    for edge in edges:
        if not include_observed and edge.edge_type.value.startswith("observed_"):
            continue
        canonical_edges.append(_edge_canonical(edge))

    # Sort edges for deterministic output
    canonical_edges.sort()

    payload = {
        "nodes": canonical_nodes,
        "edges": canonical_edges,
    }

    return json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _node_canonical(node: Node) -> dict:
    """Canonical representation of one node for hashing."""
    meta = dict(node.metadata)
    # Strip non-deterministic / runtime fields
    meta.pop("_code_hash", None)
    return {
        "node_id": node.node_id,
        "node_type": node.node_type.value,
        "label": node.label,
        "metadata": _sort_dict(meta),
    }


def _edge_canonical(edge: Edge) -> list:
    """Canonical representation of one edge for hashing.

    Returns a list so edges can be sorted lexicographically.
    """
    return [
        edge.source_id,
        edge.target_id,
        edge.edge_type.value,
        _sort_dict(dict(edge.metadata)),
    ]


_ORDER_INDEPENDENT_LIST_KEYS: frozenset = frozenset({
    "_after_",
    "_writes_slots_",
    "_reads_slots_",
    "_reads_event_fields_",
    "_writes_event_fields_",
    "_hooks_",
})


def _sort_dict(d: dict, *, at_root: bool = True) -> dict:
    """Recursively sort a dict for deterministic serialization (L6.2).

    - ``at_root=True`` (node.metadata top level): keys in the
      order-independent whitelist get their primitive lists sorted; every
      other list keeps its order.
    - ``at_root=False`` (ANY nested dict, not just ``_ctor_kwargs_``): all
      lists/tuples keep their order strictly — even a key that happens to be
      named ``_after_``.  Order carries meaning inside constructor kwargs.
    """
    result = {}
    for key in sorted(d):
        val = d[key]
        if isinstance(val, dict):
            result[key] = _sort_dict(val, at_root=False)
        elif isinstance(val, (list, tuple)):
            if not at_root:
                result[key] = list(val)  # nested: order is significant
            elif key in _ORDER_INDEPENDENT_LIST_KEYS:
                try:
                    result[key] = sorted(val, key=str)
                except TypeError:
                    result[key] = list(val)
            else:
                result[key] = list(val)
        else:
            result[key] = val
    return result


# ── dedup utilities ─────────────────────────────────────────────────────────


class DedupRegistry:
    """A hash → node_id registry for duplicate detection.

    Two candidates with the same genotype hash are structurally identical
    and one can be skipped.
    """

    def __init__(self) -> None:
        self._seen: dict[str, str] = {}  # hash → first candidate_id

    def register(self, genotype: str, candidate_id: str) -> bool:
        """Register a candidate.  Returns True if this hash is new."""
        if genotype in self._seen:
            return False
        self._seen[genotype] = candidate_id
        return True

    def is_duplicate(self, genotype: str) -> bool:
        return genotype in self._seen

    def first_seen(self, genotype: str) -> str | None:
        return self._seen.get(genotype)

    def __len__(self) -> int:
        return len(self._seen)

    def __contains__(self, genotype: str) -> bool:
        return genotype in self._seen
