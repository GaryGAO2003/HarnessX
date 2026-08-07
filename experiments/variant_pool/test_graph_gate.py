"""Tests for S3 — graph gate validation."""

import tempfile
from pathlib import Path

import pytest

from experiments.variant_pool.graph_gate import (
    GraphGateStage,
    GraphValidationError,
    GraphValidationReport,
    validate_candidate_graph,
)


def make_config_yaml(content: str) -> Path:
    """Write a config YAML to a temp file and return the path."""
    tmp = Path(tempfile.mkdtemp(prefix="s3_test_"))
    path = tmp / "config.yaml"
    path.write_text(content, encoding="utf-8")
    return path


class TestGraphGateStage:
    def test_stages_exist(self):
        assert GraphGateStage.GRAPH_BUILD == "graph_build"
        assert GraphGateStage.GRAPH_DEDUP == "graph_dedup"


class TestGraphValidationReport:
    def test_passed_report(self):
        r = GraphValidationReport(passed=True, genotype_hash="abc123")
        assert r.passed
        assert r.rejection_reason() == ""
        assert r.genotype_hash == "abc123"

    def test_failed_report(self):
        r = GraphValidationReport(
            passed=False,
            errors=[GraphValidationError(error_type="cycle", message="Found cycle")],
        )
        assert not r.passed
        assert "cycle" in r.rejection_reason()
        assert r.genotype_hash == ""

    def test_multiple_errors(self):
        r = GraphValidationReport(
            passed=False,
            errors=[
                GraphValidationError(error_type="cycle", message="Cycle A→B→A"),
                GraphValidationError(
                    error_type="singleton_conflict",
                    message="Duplicate mem_ctrl",
                ),
            ],
        )
        reason = r.rejection_reason()
        assert "cycle" in reason
        assert "singleton" in reason


class TestValidateCandidateGraph:
    def test_valid_config(self):
        yaml_content = """\
processors:
  - _target_: harnessx.processors.control.loop_detection.LoopDetectionProcessor
    _hook_: step_end
  - _target_: harnessx.processors.control.cost_guard.CostGuardProcessor
    _hook_: before_model
"""
        path = make_config_yaml(yaml_content)
        report = validate_candidate_graph(path)
        assert report.passed
        assert report.genotype_hash

    def test_missing_file(self):
        path = Path("/nonexistent/config.yaml")
        report = validate_candidate_graph(path)
        assert not report.passed
        assert any("Cannot load" in e.message for e in report.errors)

    def test_empty_config(self):
        yaml_content = "processors: []"
        path = make_config_yaml(yaml_content)
        report = validate_candidate_graph(path)
        assert report.passed  # empty is valid

    def test_singleton_conflict(self):
        """Two processors claiming the same singleton_group → conflict."""
        yaml_content = """\
processors:
  - _target_: harnessx.processors.control.loop_detection.LoopDetectionProcessor
    _hook_: step_end
    _singleton_group_: loop_detection
  - _target_: harnessx.processors.control.cost_guard.CostGuardProcessor
    _hook_: step_end
    _singleton_group_: loop_detection
"""
        path = make_config_yaml(yaml_content)
        report = validate_candidate_graph(path)
        assert not report.passed
        assert any("singleton_conflict" in e.error_type for e in report.errors)

    def test_genotype_hash_stable(self):
        """Same config → same genotype hash."""
        yaml_content = """\
processors:
  - _target_: harnessx.processors.control.loop_detection.LoopDetectionProcessor
    _hook_: step_end
"""
        p1 = make_config_yaml(yaml_content)
        p2 = make_config_yaml(yaml_content)
        r1 = validate_candidate_graph(p1)
        r2 = validate_candidate_graph(p2)
        assert r1.passed and r2.passed
        assert r1.genotype_hash == r2.genotype_hash


class TestGraphMetadataGate:
    """New processors without metadata should trigger warnings."""

    def test_new_processor_without_sg_warns(self):
        yaml_content = """\
processors:
  - _target_: harnessx.processors.control.loop_detection.LoopDetectionProcessor
    _hook_: step_end
  - _target_: totally.new.CustomProcessor
    _hook_: before_model
"""
        path = make_config_yaml(yaml_content)
        report = validate_candidate_graph(path)
        # Passes (import error is a warning, metadata missing is a warning)
        assert report.passed
        assert any("missing_metadata" in w.error_type for w in report.warnings)
        assert any("import_warning" in w.error_type for w in report.warnings)

    def test_known_processor_no_warning(self):
        yaml_content = """\
processors:
  - _target_: harnessx.processors.control.loop_detection.LoopDetectionProcessor
    _hook_: step_end
"""
        path = make_config_yaml(yaml_content)
        report = validate_candidate_graph(path)
        assert report.passed
        missing = [w for w in report.warnings if w.error_type == "missing_metadata"]
        assert len(missing) == 0

    def test_new_processor_with_sg_passes(self):
        yaml_content = """\
processors:
  - _target_: my.DeclaredProcessor
    _hook_: step_end
    _singleton_group_: my_group
"""
        path = make_config_yaml(yaml_content)
        report = validate_candidate_graph(path)
        # Passes (import error is a warning, not a block)
        assert report.passed
        missing = [w for w in report.warnings if w.error_type == "missing_metadata"]
        assert len(missing) == 0  # sg declared, no metadata warning


class TestGraphValidationError:
    def test_create_with_node_refs(self):
        e = GraphValidationError(
            error_type="cycle",
            message="Found cycle A→B→A",
            node_ids=["proc:a", "proc:b"],
        )
        assert e.error_type == "cycle"
        assert "A→B→A" in e.message
        assert len(e.node_ids) == 2
