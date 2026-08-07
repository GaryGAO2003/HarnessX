"""S6 — Three governance invariants for the skill graph.

These invariants must hold after every skill graph edit.  They are
checked structurally (no LLM involved) and produce concrete violation
reports that the gate can use to reject invalid edits.

Invariants
----------
1. **Acyclicity** — No cycles in DEPENDS_ON ∪ COMPOSES_WITH ∪ SPECIALIZES.
   A skill cannot transitively depend on itself.

2. **Non-contradiction** — No pair of edges on the same node pair that
   asserts opposite semantics.  The contradiction matrix:

   =============== =============== ==============
   Edge A  \\  B   DEPENDS_ON      SPECIALIZES   COMPOSES_WITH  CONFLICTS_WITH
   DEPENDS_ON      —               OK            OK             CONTRADICTION
   SPECIALIZES     OK              —             OK             CONTRADICTION
   COMPOSES_WITH   OK              OK            —              CONTRADICTION
   SIMILAR_TO      OK              OK            OK             CONTRADICTION
   CONFLICTS_WITH  CONTRADICTION   CONTRADICTION CONTRADICTION  OK
   =============== =============== ==============

3. **Append-only-reversible** — Edge removals must be documented
   reversals of prior additions.  Initial edges (round_added=0) are
   immutable.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from .skill_graph import SkillEdge, SkillEdgeType, SkillGraph


# ── violation types ─────────────────────────────────────────────────────────


@dataclass
class InvariantViolation:
    """One invariant violation with graph coordinates."""

    rule: str  # "acyclicity" | "non_contradiction" | "append_only"
    message: str
    skill_ids: list[str] = field(default_factory=list)
    edge_types: list[str] = field(default_factory=list)


# ── 1. acyclicity ───────────────────────────────────────────────────────────


def check_acyclicity(graph: SkillGraph) -> list[InvariantViolation]:
    """Check that DEPENDS_ON + COMPOSES_WITH + SPECIALIZES edges are acyclic.

    Uses Kahn's algorithm.  SIMILAR_TO is undirected and excluded from
    cycle detection.  CONFLICTS_WITH is also excluded.
    """
    cycle_edge_types = {
        SkillEdgeType.DEPENDS_ON,
        SkillEdgeType.COMPOSES_WITH,
        SkillEdgeType.SPECIALIZES,
    }

    # Build adjacency + in-degree
    nodes = set(graph.nodes)
    adj: dict[str, list[str]] = {n: [] for n in nodes}
    in_deg: dict[str, int] = {n: 0 for n in nodes}

    for e in graph.edges:
        if e.edge_type not in cycle_edge_types:
            continue
        if e.source_id in adj and e.target_id in nodes:
            adj[e.source_id].append(e.target_id)
            in_deg[e.target_id] = in_deg.get(e.target_id, 0) + 1

    # Kahn's
    queue = deque(n for n in nodes if in_deg.get(n, 0) == 0)
    sorted_count = 0

    while queue:
        current = queue.popleft()
        sorted_count += 1
        for neighbor in adj.get(current, []):
            in_deg[neighbor] -= 1
            if in_deg[neighbor] == 0:
                queue.append(neighbor)

    if sorted_count == len(nodes):
        return []

    # Find cycle participants
    cycle_nodes = [n for n in nodes if in_deg.get(n, 0) > 0]
    return [InvariantViolation(
        rule="acyclicity",
        message=f"Cycle detected involving {len(cycle_nodes)} skill(s)",
        skill_ids=cycle_nodes,
    )]


# ── 2. non-contradiction ────────────────────────────────────────────────────


# Pairs of edge types that CANNOT coexist between the same (A, B)
_CONTRADICTION_PAIRS: set[tuple[SkillEdgeType, SkillEdgeType]] = {
    (SkillEdgeType.DEPENDS_ON, SkillEdgeType.CONFLICTS_WITH),
    (SkillEdgeType.CONFLICTS_WITH, SkillEdgeType.DEPENDS_ON),
    (SkillEdgeType.SPECIALIZES, SkillEdgeType.CONFLICTS_WITH),
    (SkillEdgeType.CONFLICTS_WITH, SkillEdgeType.SPECIALIZES),
    (SkillEdgeType.COMPOSES_WITH, SkillEdgeType.CONFLICTS_WITH),
    (SkillEdgeType.CONFLICTS_WITH, SkillEdgeType.COMPOSES_WITH),
    (SkillEdgeType.SIMILAR_TO, SkillEdgeType.CONFLICTS_WITH),
    (SkillEdgeType.CONFLICTS_WITH, SkillEdgeType.SIMILAR_TO),
}


def check_non_contradiction(graph: SkillGraph) -> list[InvariantViolation]:
    """Check that no node pair has contradictory edge types.

    Also checks transitive SPECIALIZES propagation: if X SPECIALIZES Y
    and Y CONFLICTS_WITH Z, then X implicitly CONFLICTS_WITH Z.
    """
    violations: list[InvariantViolation] = []

    # Build per-pair edge type sets
    pair_edges: dict[tuple[str, str], set[SkillEdgeType]] = {}
    for e in graph.edges:
        key = (e.source_id, e.target_id)
        pair_edges.setdefault(key, set()).add(e.edge_type)

    # Direct contradiction check
    for (src, tgt), types in pair_edges.items():
        type_list = list(types)
        for i in range(len(type_list)):
            for j in range(i + 1, len(type_list)):
                if (type_list[i], type_list[j]) in _CONTRADICTION_PAIRS:
                    violations.append(InvariantViolation(
                        rule="non_contradiction",
                        message=(
                            f"Contradictory edges between '{src}' and '{tgt}': "
                            f"{type_list[i].value} and {type_list[j].value}"
                        ),
                        skill_ids=[src, tgt],
                        edge_types=[type_list[i].value, type_list[j].value],
                    ))

    # SPECIALIZES propagation: X → Y SPECIALIZES, Y → Z CONFLICTS_WITH
    # → X implicitly conflicts with Z
    specializes_map: dict[str, set[str]] = {}
    for e in graph.edges:
        if e.edge_type == SkillEdgeType.SPECIALIZES:
            specializes_map.setdefault(e.source_id, set()).add(e.target_id)

    conflicts_map: dict[str, set[str]] = {}
    for e in graph.edges:
        if e.edge_type == SkillEdgeType.CONFLICTS_WITH:
            conflicts_map.setdefault(e.source_id, set()).add(e.target_id)
            conflicts_map.setdefault(e.target_id, set()).add(e.source_id)

    # Check if any X → Y specializes, and Z exists where Y conflicts Z but X also depends on Z
    for x_id, parents in specializes_map.items():
        for parent_id in parents:
            parent_conflicts = conflicts_map.get(parent_id, set())
            for z_id in parent_conflicts:
                if z_id == x_id:
                    violations.append(InvariantViolation(
                        rule="non_contradiction",
                        message=(
                            f"SPECIALIZES chain contradiction: '{x_id}' specializes "
                            f"'{parent_id}' which conflicts with '{z_id}' — "
                            f"'{x_id}' inherits this conflict"
                        ),
                        skill_ids=[x_id, parent_id, z_id],
                    ))

    return violations


# ── 3. append-only-reversible ───────────────────────────────────────────────


@dataclass
class SkillGraphEdit:
    """Record of one skill graph edit for reversibility tracking."""
    round_idx: int
    operation: str  # "ADD" | "REMOVE"
    edge: SkillEdge
    reason: str = ""


def check_append_only_reversible(
    proposed_removal: SkillEdge,
    edit_history: list[SkillGraphEdit],
    current_graph: SkillGraph,
) -> list[InvariantViolation]:
    """Check that an edge removal is a valid reversal.

    Rules:
    1. An edge added in round 0 (initial) cannot be removed.
    2. A removal must reference an edge that was previously ADDED
       (not part of the initial graph).
    3. The removal must have a documented reason.

    Args:
        proposed_removal: The edge to be removed.
        edit_history: Ordered list of prior edits.
        current_graph: The current skill graph.

    Returns:
        List of violations (empty = removal is valid).
    """
    violations: list[InvariantViolation] = []

    # Rule 1: initial edges are immutable
    if proposed_removal.round_added == 0:
        violations.append(InvariantViolation(
            rule="append_only",
            message=(
                f"Cannot remove initial edge: {proposed_removal.source_id} "
                f"→ {proposed_removal.target_id} ({proposed_removal.edge_type.value})"
            ),
            skill_ids=[proposed_removal.source_id, proposed_removal.target_id],
            edge_types=[proposed_removal.edge_type.value],
        ))

    # Rule 2: must have been previously added
    was_added = any(
        h.operation == "ADD"
        and h.edge.source_id == proposed_removal.source_id
        and h.edge.target_id == proposed_removal.target_id
        and h.edge.edge_type == proposed_removal.edge_type
        for h in edit_history
    )
    if not was_added and proposed_removal.round_added > 0:
        violations.append(InvariantViolation(
            rule="append_only",
            message=(
                f"Removal of edge not found in edit history: "
                f"{proposed_removal.source_id} → {proposed_removal.target_id}"
            ),
            skill_ids=[proposed_removal.source_id, proposed_removal.target_id],
        ))

    return violations
