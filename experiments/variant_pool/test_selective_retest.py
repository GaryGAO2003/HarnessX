"""Tests for S5 selective retest engine."""

import tempfile
from pathlib import Path

import pytest

from experiments.variant_pool.selective_retest import (
    RetestDecision,
    RetestReport,
    SelectiveRetestEngine,
)
from harnessx.graph.edit import GraphEdit, GraphEditType
from harnessx.graph.footprint import CoverageFootprint, FootprintStore
from harnessx.graph.types import Edge, EdgeType, GraphSnapshot, Node, NodeType


def make_graph() -> GraphSnapshot:
    """A → B → C."""
    snapshot = GraphSnapshot()
    for label, nid in [("A", "proc:a"), ("B", "proc:b"), ("C", "proc:c")]:
        snapshot.nodes[nid] = Node(node_id=nid, node_type=NodeType.PROCESSOR, label=label)
    snapshot.nodes["hook:task_start"] = Node(
        node_id="hook:task_start", node_type=NodeType.SKELETON_HOOK, label="task_start",
    )
    snapshot.edges = [
        Edge(source_id="proc:a", target_id="proc:b", edge_type=EdgeType.AFTER),
        Edge(source_id="proc:b", target_id="proc:c", edge_type=EdgeType.AFTER),
        Edge(source_id="proc:a", target_id="hook:task_start", edge_type=EdgeType.ATTACHED_TO),
    ]
    snapshot.genotype_hash = "abc123"
    return snapshot


class TestRetestDecision:
    def test_enum_values(self):
        assert RetestDecision.MUST_RETEST == "must_retest"
        assert RetestDecision.CAN_INHERIT == "can_inherit"
        assert RetestDecision.UNCERTAIN == "uncertain"


class TestRetestReport:
    def test_records_decisions(self):
        r = RetestReport()
        r.record("t1", RetestDecision.MUST_RETEST)
        r.record("t2", RetestDecision.CAN_INHERIT)
        r.record("t3", RetestDecision.CAN_INHERIT)
        r.finalize()
        assert r.total_tasks == 3
        assert r.must_retest == 1
        assert r.can_inherit == 2
        assert r.budget_saved_pct == 2 / 3


class TestSelectiveRetestEngine:
    def test_cold_start_must_retest(self):
        engine = SelectiveRetestEngine(mode="safe")
        decision = engine.should_retest({"n1"}, {"e1"}, None, "abc")
        assert decision == RetestDecision.MUST_RETEST

    def test_footprint_miss_can_inherit(self):
        engine = SelectiveRetestEngine(mode="safe")
        fp = CoverageFootprint(
            task_id="t1",
            touched_node_ids={"proc:b", "proc:c"},
            observed_edge_keys={"proc:b→proc:c"},
            genotype_hash="abc",
        )
        # Danger is only proc:a — footprint covers b and c only → no intersection
        decision = engine.should_retest({"proc:a"}, set(), fp, "abc")
        assert decision == RetestDecision.CAN_INHERIT

    def test_footprint_hit_must_retest(self):
        engine = SelectiveRetestEngine(mode="safe")
        fp = CoverageFootprint(
            task_id="t1",
            touched_node_ids={"proc:a", "proc:b"},
            observed_edge_keys={"proc:a→hook:task_start"},
            genotype_hash="abc",
        )
        decision = engine.should_retest({"proc:a"}, set(), fp, "abc")
        assert decision == RetestDecision.MUST_RETEST

    def test_genotype_mismatch_uncertain(self):
        engine = SelectiveRetestEngine(mode="safe")
        fp = CoverageFootprint(
            task_id="t1",
            touched_node_ids={"proc:b"},
            genotype_hash="old_hash",
        )
        decision = engine.should_retest({"proc:a"}, set(), fp, "new_hash")
        assert decision == RetestDecision.UNCERTAIN

    def test_heuristic_mode_skips_genotype_check(self):
        engine = SelectiveRetestEngine(mode="heuristic")
        fp = CoverageFootprint(
            task_id="t1",
            touched_node_ids={"proc:b"},
            genotype_hash="old_hash",
        )
        # Heuristic ignores genotype mismatch for intersection check
        decision = engine.should_retest({"proc:a"}, set(), fp, "new_hash")
        assert decision == RetestDecision.CAN_INHERIT  # no intersection

    def test_decide_with_footprint_store(self):
        base = Path(tempfile.mkdtemp(prefix="s5_retest_"))
        store = FootprintStore(base)
        # Footprint covers only "proc:isolated" — outside the danger cone of proc:a
        store.put(CoverageFootprint(
            task_id="t1", variant_id="V0",
            touched_node_ids={"proc:isolated"}, genotype_hash="abc123",
        ))

        engine = SelectiveRetestEngine(mode="safe", footprint_store=store)
        g = make_graph()
        edit = GraphEdit(edit_type=GraphEditType.MUTATE_INACTIVE, target_node_id="proc:a", node_changes={"x": 1})

        report = engine.decide([edit], g, ["t1"], "V0")
        assert report.total_tasks == 1
        # Footprint covers isolated node outside danger cone → CAN_INHERIT
        assert report.can_inherit == 1
