"""S6 — Edge-fault classification (thesis contribution).

When a skill retrieval fails — the agent calls a tool that doesn't
exist, or a skill dependency is broken — the failure is classified
as one of three edge-fault types in the skill graph:

    MISSING — an edge should exist (observed at runtime) but is not declared.
    WRONG   — a declared edge has the wrong type (observation contradicts).
    STALE   — a declared edge was once observed but has not been seen
              in recent rounds (the relationship has decayed).

This classification fills a literature gap: SkillDAG (arXiv:2606.03056)
governs edges with invariants but does not diagnose *why* retrieval
failed by typing the faulty edge.

Classification rules
--------------------
MISSING
    An observation edge exists (two skills were co-used or data flowed
    between them) but no declared edge connects them.

WRONG
    A declared edge of type T₁ exists, but observation shows type T₂
    behaviour.  Example: declared DEPENDS_ON but tool calls show the
    tools are never co-invoked, suggesting CONFLICTS_WITH.

STALE
    A declared edge was confirmed by observation in rounds [R₀ … Rₖ₋₁]
    but has not been observed in the last N rounds.  The relationship
    has likely decayed due to prompt/config evolution.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from .skill_graph import SkillEdge, SkillEdgeType, SkillGraph


class EdgeFaultType(str, Enum):
    MISSING = "missing"  # observed but undeclared
    WRONG = "wrong"  # declared type contradicts observation
    STALE = "stale"  # declared but not recently observed


@dataclass
class EdgeFault:
    """One classified edge fault in the skill graph."""

    source_skill_id: str
    target_skill_id: str
    fault_type: EdgeFaultType

    # What was declared (may be None for MISSING)
    declared_edge_type: SkillEdgeType | None = None
    # What was observed (may be None for STALE)
    observed_edge_type: SkillEdgeType | None = None

    # Evidence
    evidence: str = ""
    detected_at_round: int = 0
    observation_count: int = 0  # how many times the observation edge appeared

    # Severity
    severity: str = "warning"  # "critical" | "warning" | "info"


# ── classification ──────────────────────────────────────────────────────────


def classify_edge_faults(
    declared_graph: SkillGraph,
    observed_edges: list[tuple[str, str, str]],  # (src, tgt, observed_edge_type)
    *,
    stale_threshold_rounds: int = 3,
    current_round: int = 0,
) -> list[EdgeFault]:
    """Classify edge faults by comparing declared vs observed skill edges.

    Args:
        declared_graph: The declared skill graph.
        observed_edges: Runtime observations as (source_skill, target_skill,
            edge_type_string) tuples.
        stale_threshold_rounds: Number of rounds without observation before
            a declared edge is considered STALE.
        current_round: Current evolution round for staleness comparison.

    Returns:
        List of EdgeFault objects, one per detected discrepancy.
    """
    faults: list[EdgeFault] = []

    # Build lookup: (src, tgt) → declared edge
    declared_map: dict[tuple[str, str], SkillEdge] = {}
    for e in declared_graph.edges:
        key = (e.source_id, e.target_id)
        declared_map[key] = e

    # Build lookup: (src, tgt) → observed type + count
    observed_map: dict[tuple[str, str], tuple[str, int]] = {}
    for src, tgt, etype in observed_edges:
        key = (src, tgt)
        prev = observed_map.get(key)
        count = (prev[1] + 1) if prev else 1
        observed_map[key] = (etype, count)

    # ── MISSING: observed but no declaration ──────────────────────────
    for (src, tgt), (obs_type, count) in observed_map.items():
        if (src, tgt) not in declared_map:
            try:
                obs_skill_type = SkillEdgeType(obs_type)
            except ValueError:
                obs_skill_type = None
            faults.append(EdgeFault(
                source_skill_id=src,
                target_skill_id=tgt,
                fault_type=EdgeFaultType.MISSING,
                observed_edge_type=obs_skill_type,
                evidence=f"Observed {count} time(s) at runtime, no declaration",
                observation_count=count,
                severity="critical",
            ))

    # ── WRONG: declared type ≠ observed type ────────────────────────
    for (src, tgt), decl_edge in declared_map.items():
        if (src, tgt) in observed_map:
            obs_type_str, count = observed_map[(src, tgt)]
            try:
                obs_type = SkillEdgeType(obs_type_str)
            except ValueError:
                continue
            if obs_type != decl_edge.edge_type:
                faults.append(EdgeFault(
                    source_skill_id=src,
                    target_skill_id=tgt,
                    fault_type=EdgeFaultType.WRONG,
                    declared_edge_type=decl_edge.edge_type,
                    observed_edge_type=obs_type,
                    evidence=(
                        f"Declared {decl_edge.edge_type.value}, "
                        f"observed {obs_type.value} ({count} time(s))"
                    ),
                    observation_count=count,
                    severity="warning",
                ))

    # ── STALE: declared but not recently observed ───────────────────
    for (src, tgt), decl_edge in declared_map.items():
        if (src, tgt) not in observed_map:
            rounds_since = current_round - decl_edge.round_added
            if rounds_since >= stale_threshold_rounds:
                faults.append(EdgeFault(
                    source_skill_id=src,
                    target_skill_id=tgt,
                    fault_type=EdgeFaultType.STALE,
                    declared_edge_type=decl_edge.edge_type,
                    evidence=(
                        f"Declared at round {decl_edge.round_added}, "
                        f"not observed in last {stale_threshold_rounds}+ rounds"
                    ),
                    severity="info",
                ))

    return faults


def audit_skill_graph(
    declared_graph: SkillGraph,
    observed_edges: list[tuple[str, str, str]],
    *,
    current_round: int = 0,
) -> dict[str, list[EdgeFault]]:
    """Full audit: classify faults and group by severity.

    Returns:
        ``{"critical": [...], "warning": [...], "info": [...]}``
    """
    faults = classify_edge_faults(
        declared_graph,
        observed_edges,
        stale_threshold_rounds=3,
        current_round=current_round,
    )

    by_severity: dict[str, list[EdgeFault]] = {
        "critical": [], "warning": [], "info": [],
    }
    for f in faults:
        by_severity.setdefault(f.severity, []).append(f)

    return by_severity
