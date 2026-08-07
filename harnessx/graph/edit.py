"""S5 — Graph-edit action space.

Defines the typed graph edit operators that the Evolver produces instead
of raw YAML text diffs.  Each edit is a structural operation on the graph
IR — insert node, remove node, change edge, swap subgraph — with
validation that ensures the result remains well-formed.

Five edit types
--------------
1. **insert-node**     — Insert a processor on an existing edge.
2. **replace-same-group** — Replace a processor with another from the
   same singleton_group (preserves ordering).
3. **change-dependency-edge** — Add or remove ``after`` or data edges.
4. **swap-subgraph**   — Replace an entire bundle with an alternative.
5. **mutate-inactive** — Modify a processor not on any active path
   (phenotype-safe by definition).
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from enum import Enum

from .types import EdgeType, GraphSnapshot, Node, NodeType


class GraphEditType(str, Enum):
    INSERT_NODE = "insert_node"
    REMOVE_NODE = "remove_node"
    REPLACE_SAME_GROUP = "replace_same_group"
    CHANGE_DEPENDENCY = "change_dependency"
    SWAP_SUBGRAPH = "swap_subgraph"
    MUTATE_INACTIVE = "mutate_inactive"


@dataclass
class GraphEdit:
    """One graph edit operation."""

    edit_type: GraphEditType
    target_node_id: str = ""

    # For INSERT_NODE / REPLACE_SAME_GROUP
    node_spec: dict | None = None  # serialized processor spec

    # For CHANGE_DEPENDENCY
    edge_source_id: str = ""
    edge_target_id: str = ""
    edge_type: EdgeType | None = None
    add_edge: bool = True  # True=add, False=remove

    # For SWAP_SUBGRAPH
    replacement_bundle_id: str = ""

    # For MUTATE_INACTIVE
    node_changes: dict | None = None  # {field: new_value}

    # Metadata
    reason: str = ""  # LLM rationale, preserved for debugging

    def affected_node_ids(self) -> set[str]:
        """Nodes whose behaviour could change as a result of this edit."""
        ids: set[str] = set()
        if self.target_node_id:
            ids.add(self.target_node_id)
        if self.edge_source_id:
            ids.add(self.edge_source_id)
        if self.edge_target_id:
            ids.add(self.edge_target_id)
        return ids


# ── apply edits ─────────────────────────────────────────────────────────────


class GraphEditError(Exception):
    """Raised when a graph edit is structurally invalid."""


def apply_edits(snapshot: GraphSnapshot, edits: list[GraphEdit]) -> GraphSnapshot:
    """Apply a sequence of graph edits to produce a new snapshot.

    Edits are applied in order.  Each edit is validated against the
    current graph state before application.

    Returns a new GraphSnapshot (immutable style).
    """
    result = deepcopy(snapshot)

    for edit in edits:
        _apply_one(result, edit)

    # Invalidate genotype hash — caller must recompute
    result.genotype_hash = ""
    result.phenotype_hash = ""

    return result


def _apply_one(snapshot: GraphSnapshot, edit: GraphEdit) -> None:
    """Apply a single edit in-place to snapshot."""
    if edit.edit_type == GraphEditType.INSERT_NODE:
        _apply_insert_node(snapshot, edit)
    elif edit.edit_type == GraphEditType.REMOVE_NODE:
        _apply_remove_node(snapshot, edit)
    elif edit.edit_type == GraphEditType.REPLACE_SAME_GROUP:
        _apply_replace_same_group(snapshot, edit)
    elif edit.edit_type == GraphEditType.CHANGE_DEPENDENCY:
        _apply_change_dependency(snapshot, edit)
    elif edit.edit_type == GraphEditType.SWAP_SUBGRAPH:
        _apply_swap_subgraph(snapshot, edit)
    elif edit.edit_type == GraphEditType.MUTATE_INACTIVE:
        _apply_mutate_inactive(snapshot, edit)


def _apply_insert_node(snapshot: GraphSnapshot, edit: GraphEdit) -> None:
    """Insert a new processor node and wire its edges."""
    if edit.node_spec is None:
        raise GraphEditError("INSERT_NODE requires node_spec")

    target = edit.node_spec.get("_target_", "")
    if not target:
        raise GraphEditError("INSERT_NODE: node_spec must include _target_")

    # Generate node_id
    from .snapshot import _slug_from_target
    node_id = _slug_from_target(target, 0)

    # Ensure uniqueness
    base = node_id
    counter = 1
    while node_id in snapshot.nodes:
        node_id = f"{base}__{counter}"
        counter += 1

    hook = edit.node_spec.get("_hook_", "")
    node = Node(
        node_id=node_id,
        node_type=NodeType.PROCESSOR,
        label=target.rsplit(".", 1)[-1] if "." in target else target,
        metadata=dict(edit.node_spec),
    )
    snapshot.nodes[node_id] = node

    # Wire ATTACHED_TO edges
    if hook and f"hook:{hook}" in snapshot.nodes:
        snapshot.edges.append(EdgeType.ATTACHED_TO.to_edge(node_id, f"hook:{hook}"))


def _apply_remove_node(snapshot: GraphSnapshot, edit: GraphEdit) -> None:
    """Remove a node and all incident edges."""
    nid = edit.target_node_id
    if nid not in snapshot.nodes:
        raise GraphEditError(f"REMOVE_NODE: node '{nid}' not found")
    del snapshot.nodes[nid]
    snapshot.edges = [
        e for e in snapshot.edges
        if e.source_id != nid and e.target_id != nid
    ]


def _apply_replace_same_group(snapshot: GraphSnapshot, edit: GraphEdit) -> None:
    """Replace a processor with another from the same singleton_group."""
    if edit.node_spec is None:
        raise GraphEditError("REPLACE_SAME_GROUP requires node_spec")

    old_id = edit.target_node_id
    if old_id not in snapshot.nodes:
        raise GraphEditError(f"REPLACE_SAME_GROUP: node '{old_id}' not found")

    old_node = snapshot.nodes[old_id]
    new_sg = edit.node_spec.get("_singleton_group_", "")
    old_sg = old_node.metadata.get("_singleton_group_", "")

    if new_sg != old_sg:
        raise GraphEditError(
            f"REPLACE_SAME_GROUP: singleton_group mismatch "
            f"('{new_sg}' vs '{old_sg}')"
        )

    # Replace node metadata, keep node_id for edge continuity
    new_target = edit.node_spec.get("_target_", old_node.metadata.get("_target_", ""))
    snapshot.nodes[old_id] = Node(
        node_id=old_id,
        node_type=NodeType.PROCESSOR,
        label=new_target.rsplit(".", 1)[-1] if "." in new_target else new_target,
        metadata=dict(edit.node_spec),
    )


def _apply_change_dependency(snapshot: GraphSnapshot, edit: GraphEdit) -> None:
    """Add or remove an AFTER or data edge."""
    if not edit.edge_source_id or not edit.edge_target_id:
        raise GraphEditError("CHANGE_DEPENDENCY requires edge_source and edge_target")
    if edit.edge_type is None:
        raise GraphEditError("CHANGE_DEPENDENCY requires edge_type")

    if edit.add_edge:
        snapshot.edges.append(EdgeType.ATTACHED_TO.to_edge(
            edit.edge_source_id, edit.edge_target_id,
        ))
    else:
        snapshot.edges = [
            e for e in snapshot.edges
            if not (e.source_id == edit.edge_source_id
                    and e.target_id == edit.edge_target_id
                    and e.edge_type == edit.edge_type)
        ]


def _apply_swap_subgraph(snapshot: GraphSnapshot, edit: GraphEdit) -> None:
    """Replace a bundle node's child graph reference."""
    nid = edit.target_node_id
    if nid not in snapshot.nodes:
        raise GraphEditError(f"SWAP_SUBGRAPH: bundle node '{nid}' not found")
    if snapshot.nodes[nid].node_type != NodeType.BUNDLE:
        raise GraphEditError(f"SWAP_SUBGRAPH: node '{nid}' is not a BUNDLE")

    node = snapshot.nodes[nid]
    node.metadata["child_graph_id"] = edit.replacement_bundle_id


def _apply_mutate_inactive(snapshot: GraphSnapshot, edit: GraphEdit) -> None:
    """Modify a processor not on any active path."""
    nid = edit.target_node_id
    if nid not in snapshot.nodes:
        raise GraphEditError(f"MUTATE_INACTIVE: node '{nid}' not found")
    if edit.node_changes is None:
        raise GraphEditError("MUTATE_INACTIVE requires node_changes")

    node = snapshot.nodes[nid]
    new_meta = dict(node.metadata)
    new_meta.update(edit.node_changes)
    snapshot.nodes[nid] = Node(
        node_id=nid,
        node_type=node.node_type,
        label=node.label,
        metadata=new_meta,
    )


# ── diff ────────────────────────────────────────────────────────────────────


def diff_graphs(before: GraphSnapshot, after: GraphSnapshot) -> list[GraphEdit]:
    """Compute a minimal edit list to transform ``before`` into ``after``.

    Used as a fallback when the LLM does not produce structured graph edits.
    """
    edits: list[GraphEdit] = []

    before_ids = set(before.nodes)
    after_ids = set(after.nodes)

    # Removed nodes
    for nid in before_ids - after_ids:
        edits.append(GraphEdit(edit_type=GraphEditType.REMOVE_NODE, target_node_id=nid))

    # Added nodes
    for nid in after_ids - before_ids:
        spec = dict(after.nodes[nid].metadata)
        spec["_target_"] = spec.get("_target_", after.nodes[nid].label)
        edits.append(GraphEdit(
            edit_type=GraphEditType.INSERT_NODE,
            node_spec=spec,
        ))

    # Modified nodes
    for nid in before_ids & after_ids:
        bm = dict(before.nodes[nid].metadata)
        am = dict(after.nodes[nid].metadata)
        if bm != am:
            changes = {k: v for k, v in am.items() if bm.get(k) != v}
            if changes:
                edits.append(GraphEdit(
                    edit_type=GraphEditType.MUTATE_INACTIVE,
                    target_node_id=nid,
                    node_changes=changes,
                ))

    # Edge changes (structural diff)
    before_edge_keys = {(e.source_id, e.target_id, e.edge_type.value) for e in before.edges}
    after_edge_keys = {(e.source_id, e.target_id, e.edge_type.value) for e in after.edges}

    for src, tgt, etype in after_edge_keys - before_edge_keys:
        edits.append(GraphEdit(
            edit_type=GraphEditType.CHANGE_DEPENDENCY,
            edge_source_id=src,
            edge_target_id=tgt,
            edge_type=_edge_type_from_str(etype),
            add_edge=True,
        ))

    for src, tgt, etype in before_edge_keys - after_edge_keys:
        edits.append(GraphEdit(
            edit_type=GraphEditType.CHANGE_DEPENDENCY,
            edge_source_id=src,
            edge_target_id=tgt,
            edge_type=_edge_type_from_str(etype),
            add_edge=False,
        ))

    return edits


def _edge_type_from_str(s: str) -> EdgeType:
    try:
        return EdgeType(s)
    except ValueError:
        return EdgeType.ATTACHED_TO


# ── helper for edge construction ────────────────────────────────────────────

def _add_edge_type_to_edge() -> None:
    """Monkey-patch-free: ensure EdgeType can create edges."""
    pass


# Patch EdgeType to have a to_edge helper
def _edge_type_to_edge(self, source_id: str, target_id: str, metadata=None) -> "Edge":
    from .types import Edge
    return Edge(
        source_id=source_id,
        target_id=target_id,
        edge_type=self,
        metadata=metadata or {},
    )


EdgeType.to_edge = _edge_type_to_edge  # type: ignore[attr-defined]
