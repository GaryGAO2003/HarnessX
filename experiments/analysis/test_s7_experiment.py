"""Tests for S7 controlled experiment module."""

import pytest

from experiments.analysis.s7_experiment import (
    ArmResult,
    ExperimentConfig,
    ExperimentResult,
    compare_arms,
    compute_statistical_significance,
)


class TestArmResult:
    def test_default_text_mode(self):
        arm = ArmResult(mode="text")
        assert arm.mode == "text"
        assert arm.illegal_rejected == 0  # graph-specific fields zero

    def test_graph_mode_has_extra_fields(self):
        arm = ArmResult(
            mode="graph",
            illegal_rejected=3,
            duplicate_skipped=1,
            retest_inherited_tasks=40,
            retest_total_tasks=100,
        )
        assert arm.illegal_rejected == 3
        assert arm.duplicate_skipped == 1
        assert arm.retest_inherited_tasks == 40


class TestExperimentResult:
    def test_efficiency_delta(self):
        r = ExperimentResult(
            text=ArmResult(mode="text", total_candidates=10, shipped_candidates=5),
            graph=ArmResult(mode="graph", total_candidates=10, shipped_candidates=7),
        )
        assert r.efficiency_delta() == pytest.approx(0.2)

    def test_pass_delta(self):
        r = ExperimentResult(
            text=ArmResult(mode="text", final_pass_at_k=0.6),
            graph=ArmResult(mode="graph", final_pass_at_k=0.7),
        )
        assert r.pass_delta() == pytest.approx(0.1)

    def test_cost_delta(self):
        r = ExperimentResult(
            text=ArmResult(mode="text", total_cost_usd=50.0),
            graph=ArmResult(mode="graph", total_cost_usd=40.0),
        )
        assert r.cost_delta() == -10.0  # exact

    def test_retest_savings_pct(self):
        r = ExperimentResult(
            graph=ArmResult(
                mode="graph",
                retest_inherited_tasks=30,
                retest_total_tasks=100,
            ),
        )
        assert r.retest_savings_pct() == 0.3

    def test_summary(self):
        r = ExperimentResult(
            text=ArmResult(mode="text", final_pass_at_k=0.5, total_cost_usd=100, total_candidates=20, shipped_candidates=10),
            graph=ArmResult(mode="graph", final_pass_at_k=0.55, total_cost_usd=80, total_candidates=20, shipped_candidates=12,
                          illegal_rejected=2, duplicate_skipped=1, retest_inherited_tasks=30, retest_total_tasks=100),
        )
        s = r.summary()
        assert "S7 Controlled Experiment" in s
        assert "Text arm:" in s
        assert "Graph arm:" in s
        assert "Pass delta" in s


class TestExperimentConfig:
    def test_defaults(self):
        config = ExperimentConfig(task_ids=["t1", "t2"])
        assert config.rounds == 10
        assert config.seed == 42
        assert config.pool_capacity == 8

    def test_to_dict(self):
        config = ExperimentConfig(task_ids=["a", "b", "c"], rounds=5)
        d = config.to_dict()
        assert d["task_count"] == 3
        assert d["rounds"] == 5


class TestCompareArms:
    def test_compare_arms(self):
        text = ArmResult(mode="text", final_pass_at_k=0.5)
        graph = ArmResult(mode="graph", final_pass_at_k=0.6)
        result = compare_arms(text, graph)
        assert result.text.mode == "text"
        assert result.graph.mode == "graph"


class TestStatisticalSignificance:
    def test_bootstrap_ci(self):
        text_data = [{"pass_count": 5}, {"pass_count": 6}, {"pass_count": 4}]
        graph_data = [{"pass_count": 7}, {"pass_count": 8}, {"pass_count": 6}]
        stats = compute_statistical_significance(text_data, graph_data)
        assert stats["n_rounds"] == 3
        assert "mean_diff" in stats
        assert "ci_low" in stats
        assert "ci_high" in stats

    def test_single_round(self):
        text_data = [{"pass_count": 3}]
        graph_data = [{"pass_count": 5}]
        stats = compute_statistical_significance(text_data, graph_data)
        assert stats["n_rounds"] == 1
        # With n=1 rounds, cannot compute meaningful bootstrap
