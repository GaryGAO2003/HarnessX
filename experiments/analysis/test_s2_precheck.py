"""Tests for S2 offline pre-check module."""

import json
import tempfile
from pathlib import Path

import pytest

from experiments.analysis.s2_precheck import (
    PreCheckMetrics,
    compute_duplicate_rate,
    compute_footprint_sparsity,
    compute_footprint_stability,
    compute_illegal_rate,
    decide_mode,
    run_precheck,
)


# ── synthetic helpers ──────────────────────────────────────────────────────


def make_config_dict(processors=None, **kwargs):
    """Create a minimal HarnessConfig-like dict."""
    d = {"processors": processors or []}
    d.update(kwargs)
    return d


def make_processor_dict(target, hook="*", **kwargs):
    """Create a minimal _target_ dict for a processor."""
    d = {"_target_": target, "_hook_": hook}
    d.update(kwargs)
    return d


def make_run_dir(files: dict[str, str]) -> Path:
    """Create a temp run directory with given file path → content mapping."""
    tmp = Path(tempfile.mkdtemp(prefix="s2_test_"))
    for relpath, content in files.items():
        full = tmp / relpath
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content, encoding="utf-8")
    return tmp


# ── ① illegal rate ────────────────────────────────────────────────────────


class TestIllegalRate:
    def test_all_valid(self):
        configs = [
            make_config_dict([
                make_processor_dict("mod.A"),
                make_processor_dict("mod.B"),
            ]),
            make_config_dict([
                make_processor_dict("mod.C"),
            ]),
        ]
        total, illegal, errors = compute_illegal_rate(configs)
        assert total == 2
        assert illegal == 0
        assert errors == {}

    def test_singleton_conflict(self):
        configs = [
            make_config_dict([
                make_processor_dict("mod.A", _singleton_group_="mem"),
                make_processor_dict("mod.B", _singleton_group_="mem"),
            ]),
        ]
        total, illegal, errors = compute_illegal_rate(configs)
        assert total == 1
        assert illegal == 1
        assert any("singleton_conflict" in k for k in errors)

    def test_unresolved_after(self):
        configs = [
            make_config_dict([
                make_processor_dict("mod.A", _after_="nonexistent"),
            ]),
        ]
        total, illegal, errors = compute_illegal_rate(configs)
        assert total == 1
        assert illegal == 1
        assert any("unresolved_after" in k for k in errors)

    def test_with_declarations(self):
        """Backfilled declarations can resolve after deps."""
        configs = [
            make_config_dict([
                make_processor_dict("mod.A", _after_="mem_ctrl"),
            ]),
        ]
        declarations = {
            "mod.B": {"_singleton_group_": "mem_ctrl"},
        }
        total, illegal, errors = compute_illegal_rate(configs, declarations=declarations)
        assert total == 1
        # "mem_ctrl" not in sg_set because B is not in the config itself
        assert illegal == 1

    def test_invalid_processors_field(self):
        configs = [{"processors": "not_a_list"}]
        total, illegal, errors = compute_illegal_rate(configs)
        assert total == 1
        assert illegal == 1


# ── ② footprint sparsity ──────────────────────────────────────────────────


class TestFootprintSparsity:
    def test_disjoint_footprints(self):
        fps = {
            "task_a": {"e1", "e2"},
            "task_b": {"e3", "e4"},
        }
        mean, median, pairs = compute_footprint_sparsity(fps)
        # Jaccard = 0/4 = 0, distance = 1.0
        assert mean == 1.0
        assert median == 1.0
        assert pairs == 1

    def test_identical_footprints(self):
        fps = {
            "task_a": {"e1", "e2"},
            "task_b": {"e1", "e2"},
        }
        mean, median, pairs = compute_footprint_sparsity(fps)
        # Jaccard = 2/2 = 1.0, distance = 0.0
        assert mean == 0.0
        assert median == 0.0

    def test_empty_input(self):
        mean, median, pairs = compute_footprint_sparsity({})
        assert mean == 0.0
        assert median == 0.0
        assert pairs == 0

    def test_partial_overlap(self):
        fps = {
            "task_a": {"e1", "e2", "e3"},
            "task_b": {"e2", "e3", "e4"},
        }
        mean, median, pairs = compute_footprint_sparsity(fps)
        # intersection=2, union=4, Jaccard=0.5, distance=0.5
        assert mean == 0.5
        assert median == 0.5


# ── ③ duplicate rate ──────────────────────────────────────────────────────


class TestDuplicateRate:
    def test_no_duplicates(self):
        """Two different configs → unique hashes."""
        configs = [
            make_config_dict([make_processor_dict("mod.A")]),
            make_config_dict([make_processor_dict("mod.B")]),
        ]
        total, unique, rate = compute_duplicate_rate(configs)
        assert total == 2
        assert unique == 2
        assert rate == 0.0

    def test_all_duplicates(self):
        configs = [
            make_config_dict([make_processor_dict("mod.A")]),
            make_config_dict([make_processor_dict("mod.A")]),
        ]
        total, unique, rate = compute_duplicate_rate(configs)
        assert total == 2
        assert unique == 1
        assert rate == 0.5


# ── ④ footprint stability ─────────────────────────────────────────────────


class TestFootprintStability:
    def test_perfect_stability(self):
        repeated = {
            "task_a": [{"e1", "e2"}, {"e1", "e2"}, {"e1", "e2"}],
        }
        mean, std, tasks = compute_footprint_stability(repeated)
        assert mean == 1.0
        assert std == 0.0
        assert tasks == 1

    def test_no_stability(self):
        repeated = {
            "task_a": [{"e1"}, {"e2"}],
        }
        mean, std, tasks = compute_footprint_stability(repeated)
        assert mean == 0.0
        assert std == 0.0
        assert tasks == 1

    def test_only_one_run_skipped(self):
        repeated = {
            "task_a": [{"e1"}],
        }
        mean, std, tasks = compute_footprint_stability(repeated)
        assert mean == 0.0
        assert tasks == 0


# ── decisions ──────────────────────────────────────────────────────────────


class TestDecideMode:
    def test_high_stability_safe(self):
        metrics = PreCheckMetrics(
            footprint_stability_mean=0.85,
            footprint_sparsity_median=0.8,
            tasks_with_repeats=10,
            duplicate_rate=0.3,
            illegal_rate=0.15,
        )
        d = decide_mode(metrics)
        assert d["retest_mode"] == "safe"
        assert d["selective_retest_value"] == "high"
        assert d["dedup_investment"] == "worthwhile"
        assert d["build_validation_value"] == "high"

    def test_low_stability_heuristic(self):
        metrics = PreCheckMetrics(
            footprint_stability_mean=0.3,
            footprint_sparsity_median=0.2,
            tasks_with_repeats=5,
            duplicate_rate=0.05,
            illegal_rate=0.02,
        )
        d = decide_mode(metrics)
        assert d["retest_mode"] == "heuristic"
        assert d["selective_retest_value"] == "low"
        assert d["dedup_investment"] == "marginal"
        assert d["build_validation_value"] == "moderate_or_low"

    def test_no_data_defaults(self):
        metrics = PreCheckMetrics()
        d = decide_mode(metrics)
        assert d["retest_mode"] == "heuristic"
        assert "retest_mode_reason" in d


# ── run_precheck integration ──────────────────────────────────────────────


class TestRunPrecheck:
    def test_empty_dir(self):
        tmp = make_run_dir({})
        metrics = run_precheck(tmp)
        assert metrics.total_candidates == 0
        assert len(metrics.bias_declarations) == 4  # all four quantities

    def test_with_candidates(self):
        import yaml

        tmp = make_run_dir({
            "data/candidates/c001.yaml": yaml.dump({
                "processors": [
                    {"_target_": "mod.A", "_hook_": "before_model"},
                ],
            }),
            "data/candidates/c002.yaml": yaml.dump({
                "processors": [
                    {"_target_": "mod.B", "_hook_": "after_model"},
                ],
            }),
        })
        metrics = run_precheck(tmp)
        assert metrics.total_candidates == 2
        assert metrics.illegal_rate == 0.0  # both valid
        assert metrics.unique_genotypes == 2
        assert metrics.duplicate_rate == 0.0

    def test_with_footprints(self):
        tmp = make_run_dir({
            "data/task_footprints/task_a.json": json.dumps({
                "task_id": "task_a",
                "observed_edge_keys": ["e1", "e2", "e3"],
            }),
            "data/task_footprints/task_b.json": json.dumps({
                "task_id": "task_b",
                "observed_edge_keys": ["e2", "e3", "e4"],
            }),
        })
        metrics = run_precheck(tmp)
        assert metrics.task_pair_count == 1
        # partial overlap: intersection=2, union=4, distance=0.5
        assert metrics.footprint_sparsity_mean == 0.5
