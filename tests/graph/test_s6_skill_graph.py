"""Tests for S6 skill graph, invariants, and edge-fault classification."""

import pytest

from harnessx.graph.fault import (
    EdgeFault,
    EdgeFaultType,
    audit_skill_graph,
    classify_edge_faults,
)
from harnessx.graph.skill_graph import (
    SkillCategory,
    SkillEdge,
    SkillEdgeType,
    SkillGraph,
    SkillNode,
)
from harnessx.graph.skill_invariants import (
    InvariantViolation,
    SkillGraphEdit,
    check_acyclicity,
    check_append_only_reversible,
    check_non_contradiction,
)


# ── helpers ─────────────────────────────────────────────────────────────────

def make_skill_graph() -> SkillGraph:
    g = SkillGraph()
    g.add_skill(SkillNode("A", "SkillA", SkillCategory.PROCESSOR))
    g.add_skill(SkillNode("B", "SkillB", SkillCategory.TOOL))
    g.add_skill(SkillNode("C", "SkillC", SkillCategory.PROMPT))
    return g


# ── skill graph ─────────────────────────────────────────────────────────────


class TestSkillGraph:
    def test_add_skill(self):
        g = SkillGraph()
        g.add_skill(SkillNode("s1", "Search"))
        assert "s1" in g
        assert g.get_skill("s1").skill_name == "Search"

    def test_add_edge(self):
        g = make_skill_graph()
        g.add_edge(SkillEdge("A", "B", SkillEdgeType.DEPENDS_ON))
        deps = g.edges_by_type(SkillEdgeType.DEPENDS_ON)
        assert len(deps) == 1
        assert deps[0].source_id == "A"

    def test_remove_edge(self):
        g = make_skill_graph()
        g.add_edge(SkillEdge("A", "B", SkillEdgeType.DEPENDS_ON))
        assert g.remove_edge("A", "B", SkillEdgeType.DEPENDS_ON)
        assert len(g.edges) == 0

    def test_get_dependencies(self):
        g = make_skill_graph()
        g.add_edge(SkillEdge("A", "B", SkillEdgeType.DEPENDS_ON))
        g.add_edge(SkillEdge("B", "C", SkillEdgeType.DEPENDS_ON))
        deps = g.get_dependencies("A")
        assert "B" in deps
        assert "C" in deps  # transitive

    def test_get_conflicts(self):
        g = make_skill_graph()
        g.add_edge(SkillEdge("A", "B", SkillEdgeType.CONFLICTS_WITH))
        assert "B" in g.get_conflicts("A")
        assert "A" in g.get_conflicts("B")  # undirected

    def test_get_similar(self):
        g = make_skill_graph()
        g.add_edge(SkillEdge("A", "C", SkillEdgeType.SIMILAR_TO))
        assert "C" in g.get_similar("A")
        assert "A" in g.get_similar("C")  # undirected


# ── invariants ──────────────────────────────────────────────────────────────


class TestAcyclicity:
    def test_dag_passes(self):
        g = make_skill_graph()
        g.add_edge(SkillEdge("A", "B", SkillEdgeType.DEPENDS_ON))
        g.add_edge(SkillEdge("B", "C", SkillEdgeType.DEPENDS_ON))
        violations = check_acyclicity(g)
        assert len(violations) == 0

    def test_cycle_detected(self):
        g = make_skill_graph()
        g.add_edge(SkillEdge("A", "B", SkillEdgeType.DEPENDS_ON))
        g.add_edge(SkillEdge("B", "C", SkillEdgeType.DEPENDS_ON))
        g.add_edge(SkillEdge("C", "A", SkillEdgeType.DEPENDS_ON))
        violations = check_acyclicity(g)
        assert len(violations) >= 1
        assert violations[0].rule == "acyclicity"

    def test_composes_with_in_cycle_check(self):
        g = make_skill_graph()
        g.add_edge(SkillEdge("A", "B", SkillEdgeType.COMPOSES_WITH))
        g.add_edge(SkillEdge("B", "A", SkillEdgeType.COMPOSES_WITH))
        violations = check_acyclicity(g)
        assert len(violations) >= 1

    def test_conflicts_with_ignored(self):
        g = make_skill_graph()
        # conflicts form a cycle but shouldn't be detected
        g.add_edge(SkillEdge("A", "B", SkillEdgeType.CONFLICTS_WITH))
        g.add_edge(SkillEdge("B", "A", SkillEdgeType.CONFLICTS_WITH))
        violations = check_acyclicity(g)
        assert len(violations) == 0


class TestNonContradiction:
    def test_depends_and_conflicts(self):
        g = make_skill_graph()
        g.add_edge(SkillEdge("A", "B", SkillEdgeType.DEPENDS_ON))
        g.add_edge(SkillEdge("A", "B", SkillEdgeType.CONFLICTS_WITH))
        violations = check_non_contradiction(g)
        assert len(violations) >= 1

    def test_specializes_conflict_propagation(self):
        g = make_skill_graph()
        g.add_edge(SkillEdge("A", "B", SkillEdgeType.SPECIALIZES))
        g.add_edge(SkillEdge("B", "A", SkillEdgeType.CONFLICTS_WITH))
        violations = check_non_contradiction(g)
        assert len(violations) >= 1
        assert any("SPECIALIZES" in v.message for v in violations)

    def test_clean_graph(self):
        g = make_skill_graph()
        g.add_edge(SkillEdge("A", "B", SkillEdgeType.DEPENDS_ON))
        g.add_edge(SkillEdge("B", "C", SkillEdgeType.COMPOSES_WITH))
        violations = check_non_contradiction(g)
        assert len(violations) == 0


class TestAppendOnly:
    def test_initial_edge_cannot_be_removed(self):
        g = make_skill_graph()
        edge = SkillEdge("A", "B", SkillEdgeType.DEPENDS_ON, round_added=0)
        violations = check_append_only_reversible(edge, [], g)
        assert len(violations) >= 1

    def test_previously_added_can_be_removed(self):
        g = make_skill_graph()
        history = [
            SkillGraphEdit(round_idx=1, operation="ADD",
                          edge=SkillEdge("A", "B", SkillEdgeType.DEPENDS_ON, round_added=1),
                          reason="A needs B"),
        ]
        removal = SkillEdge("A", "B", SkillEdgeType.DEPENDS_ON, round_added=1)
        violations = check_append_only_reversible(removal, history, g)
        assert len(violations) == 0

    def test_unadded_removal(self):
        g = make_skill_graph()
        removal = SkillEdge("X", "Y", SkillEdgeType.DEPENDS_ON, round_added=5)
        violations = check_append_only_reversible(removal, [], g)
        assert len(violations) >= 1


# ── edge-fault ──────────────────────────────────────────────────────────────


class TestEdgeFault:
    def test_create_missing(self):
        f = EdgeFault(
            source_skill_id="A", target_skill_id="B",
            fault_type=EdgeFaultType.MISSING,
            observed_edge_type=SkillEdgeType.DEPENDS_ON,
            evidence="Found in traces",
            severity="critical",
        )
        assert f.fault_type == EdgeFaultType.MISSING
        assert f.declared_edge_type is None

    def test_create_wrong(self):
        f = EdgeFault(
            source_skill_id="A", target_skill_id="B",
            fault_type=EdgeFaultType.WRONG,
            declared_edge_type=SkillEdgeType.DEPENDS_ON,
            observed_edge_type=SkillEdgeType.CONFLICTS_WITH,
        )
        assert f.fault_type == EdgeFaultType.WRONG


class TestClassifyEdgeFaults:
    def test_missing_fault(self):
        g = SkillGraph()
        observed = [("A", "B", "depends_on")]
        faults = classify_edge_faults(g, observed)
        assert len(faults) == 1
        assert faults[0].fault_type == EdgeFaultType.MISSING

    def test_wrong_fault(self):
        g = SkillGraph()
        g.add_skill(SkillNode("A", "A"))
        g.add_skill(SkillNode("B", "B"))
        g.add_edge(SkillEdge("A", "B", SkillEdgeType.DEPENDS_ON))
        observed = [("A", "B", "conflicts_with")]
        faults = classify_edge_faults(g, observed)
        assert any(f.fault_type == EdgeFaultType.WRONG for f in faults)

    def test_stale_fault(self):
        g = SkillGraph()
        g.add_skill(SkillNode("A", "A"))
        g.add_skill(SkillNode("B", "B"))
        g.add_edge(SkillEdge("A", "B", SkillEdgeType.DEPENDS_ON, round_added=0))
        faults = classify_edge_faults(
            g, [], stale_threshold_rounds=3, current_round=5,
        )
        assert any(f.fault_type == EdgeFaultType.STALE for f in faults)

    def test_no_faults(self):
        g = SkillGraph()
        g.add_skill(SkillNode("A", "A"))
        g.add_skill(SkillNode("B", "B"))
        g.add_edge(SkillEdge("A", "B", SkillEdgeType.DEPENDS_ON))
        observed = [("A", "B", "depends_on")]
        faults = classify_edge_faults(g, observed)
        assert len(faults) == 0

    def test_audit_groups_by_severity(self):
        g = SkillGraph()
        observed = [("X", "Y", "depends_on")]  # missing → critical
        result = audit_skill_graph(g, observed)
        assert len(result["critical"]) == 1
        assert len(result["warning"]) == 0
