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

    The hash covers node identity and declared edges only — it is a pure
    function of the config structure, independent of any runtime traces.

    Returns:
        Hex-encoded SHA-256 digest.
    """
    canonical = _canonical_form(snapshot, include_observed=False)
    return _sha256(canonical)


def phenotype_hash(snapshot: GraphSnapshot) -> str:
    """Compute the phenotype (observation-aware) hash.

    In addition to the genotype form, this includes observation edges
    from runtime traces (S4).  Used for early cutoff.

    Returns:
        Hex-encoded SHA-256 digest, or empty string if no observation
        edges are present (phenotype not yet determined).
    """
    canonical = _canonical_form(snapshot, include_observed=True)
    return _sha256(canonical)


# ── internals ───────────────────────────────────────────────────────────────


def _sha256(canonical: str) -> str:
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _canonical_form(snapshot: GraphSnapshot, *, include_observed: bool = False) -> str:
    """Produce a stable, sort-order-normalized JSON string for hashing.

    Args:
        snapshot: The graph to canonicalize.
        include_observed: If False, OBSERVED_CONTROL and OBSERVED_DATA
            edges are excluded.  Set True for phenotype hashing.
    """
    nodes = []
    for node_id in sorted(snapshot.nodes):
        node = snapshot.nodes[node_id]
        nodes.append(_node_canonical(node))

    edges = []
    for edge in snapshot.edges:
        if not include_observed and edge.edge_type.value.startswith("observed_"):
            continue
        edges.append(_edge_canonical(edge))

    # Sort edges for deterministic output
    edges.sort()

    payload = {
        "nodes": nodes,
        "edges": edges,
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


def _sort_dict(d: dict) -> dict:
    """Recursively sort a dict for deterministic serialization."""
    result = {}
    for key in sorted(d):
        val = d[key]
        if isinstance(val, dict):
            result[key] = _sort_dict(val)
        elif isinstance(val, (list, tuple)):
            if val and isinstance(val[0], dict):
                result[key] = [_sort_dict(v) if isinstance(v, dict) else v for v in val]
            else:
                # Sort lists of primitives for determinism
                try:
                    result[key] = sorted(val, key=str)
                except TypeError:
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
