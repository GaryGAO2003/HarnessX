"""S6 — Skill/tool heterogeneous graph layer.

Extends the structural harness graph with a skill dimension: skills and
tools become first-class graph nodes with semantically-typed edges
adopted from SkillDAG (arXiv:2606.03056).

Five typed skill edges
-----------------------
1. **depends_on**     — Skill A requires skill B to function.
2. **specializes**    — Skill A refines / extends skill B.
3. **composes_with**  — Skills A and B are composable as a bundle.
4. **similar_to**     — Soft semantic similarity (for retrieval, not validation).
5. **conflicts_with** — Skills A and B are mutually incompatible.

Three governance invariants (enforced by :mod:`.skill_invariants`):
  - acyclicity (DEPENDS_ON + COMPOSES_WITH + SPECIALIZES edges)
  - non-contradiction (no edge pair that asserts opposite semantics)
  - append-only-reversible (removals must be documented reversals)

This is the thesis contribution layer — the skill graph sits on top of
the structural harness graph (S1) and is populated from observation
edges (S4) and LLM declarations.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class SkillEdgeType(str, Enum):
    """Typed semantic edges between skill/tool nodes (from SkillDAG)."""

    DEPENDS_ON = "depends_on"
    SPECIALIZES = "specializes"
    COMPOSES_WITH = "composes_with"
    SIMILAR_TO = "similar_to"
    CONFLICTS_WITH = "conflicts_with"


class SkillCategory(str, Enum):
    TOOL = "tool"
    PROCESSOR = "processor"
    PROMPT = "prompt"
    COMPOSITE = "composite"


@dataclass
class SkillNode:
    """A skill or tool node in the heterogeneous skill graph."""

    skill_id: str
    skill_name: str
    category: SkillCategory = SkillCategory.COMPOSITE
    description: str = ""

    # Binding to structural harness graph
    bound_entity: str = ""  # _target_ path for processor, tool name for tool
    version: str = ""  # for tracking evolution of the skill itself

    # Provenance
    source: str = "declared"  # "declared" | "observed" | "llm_draft"


@dataclass
class SkillEdge:
    """A typed semantic edge between two skill nodes."""

    source_id: str
    target_id: str
    edge_type: SkillEdgeType

    confidence: float = 1.0  # 1.0 = declared; <1.0 = inferred/LLM-drafted
    inference_source: str = "declared"  # "declared" | "observed" | "llm_draft"
    round_added: int = 0  # the evolution round when this edge was added


@dataclass
class SkillGraph:
    """A heterogeneous skill graph with typed semantic edges.

    This is a separate layer from the structural HarnessGraph, though
    skill nodes may reference processor/tool nodes in the structural graph
    via ``bound_entity``.
    """

    nodes: dict[str, SkillNode] = field(default_factory=dict)
    edges: list[SkillEdge] = field(default_factory=list)

    # ── node operations ─────────────────────────────────────────────────

    def add_skill(self, node: SkillNode) -> None:
        self.nodes[node.skill_id] = node

    def get_skill(self, skill_id: str) -> SkillNode | None:
        return self.nodes.get(skill_id)

    def remove_skill(self, skill_id: str) -> None:
        self.nodes.pop(skill_id, None)

    # ── edge operations ─────────────────────────────────────────────────

    def add_edge(self, edge: SkillEdge) -> None:
        self.edges.append(edge)

    def remove_edge(
        self, source_id: str, target_id: str, edge_type: SkillEdgeType,
    ) -> bool:
        """Remove a specific edge.  Returns True if found and removed."""
        for i, e in enumerate(self.edges):
            if (e.source_id == source_id
                    and e.target_id == target_id
                    and e.edge_type == edge_type):
                self.edges.pop(i)
                return True
        return False

    def edges_by_type(self, edge_type: SkillEdgeType) -> list[SkillEdge]:
        return [e for e in self.edges if e.edge_type == edge_type]

    # ── queries ─────────────────────────────────────────────────────────

    def get_dependencies(self, skill_id: str) -> set[str]:
        """Transitive DEPENDS_ON + COMPOSES_WITH closure."""
        result: set[str] = set()
        queue = [skill_id]
        while queue:
            current = queue.pop()
            for e in self.edges:
                if e.source_id == current and e.edge_type in (
                    SkillEdgeType.DEPENDS_ON, SkillEdgeType.COMPOSES_WITH,
                ):
                    if e.target_id not in result:
                        result.add(e.target_id)
                        queue.append(e.target_id)
        return result

    def get_specializations(self, skill_id: str) -> set[str]:
        """All transitive SPECIALIZES targets."""
        result: set[str] = set()
        queue = [skill_id]
        while queue:
            current = queue.pop()
            for e in self.edges:
                if e.source_id == current and e.edge_type == SkillEdgeType.SPECIALIZES:
                    if e.target_id not in result:
                        result.add(e.target_id)
                        queue.append(e.target_id)
        return result

    def get_conflicts(self, skill_id: str) -> set[str]:
        """Direct CONFLICTS_WITH targets."""
        return {
            e.target_id for e in self.edges
            if e.source_id == skill_id and e.edge_type == SkillEdgeType.CONFLICTS_WITH
        } | {
            e.source_id for e in self.edges
            if e.target_id == skill_id and e.edge_type == SkillEdgeType.CONFLICTS_WITH
        }

    def get_similar(self, skill_id: str) -> set[str]:
        """SIMILAR_TO targets (undirected)."""
        return {
            e.target_id for e in self.edges
            if e.source_id == skill_id and e.edge_type == SkillEdgeType.SIMILAR_TO
        } | {
            e.source_id for e in self.edges
            if e.target_id == skill_id and e.edge_type == SkillEdgeType.SIMILAR_TO
        }

    def successors(self, skill_id: str) -> set[str]:
        """All direct edge targets."""
        return {e.target_id for e in self.edges if e.source_id == skill_id}

    def predecessors(self, skill_id: str) -> set[str]:
        """All direct edge sources."""
        return {e.source_id for e in self.edges if e.target_id == skill_id}

    def __len__(self) -> int:
        return len(self.nodes)

    def __contains__(self, skill_id: str) -> bool:
        return skill_id in self.nodes
