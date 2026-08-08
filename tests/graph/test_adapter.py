"""Tests for GraphAdapter — AEGIS → graph adaptation layer."""

import tempfile
from pathlib import Path

import pytest

from harnessx.graph.adapter import (
    ComponentKind,
    GraphAdapter,
    GraphAdaptationReport,
    ProcessorSpec,
    TemplateSpec,
    ToolSpec,
    validate_evolver_output,
)


class TestProcessorSpec:
    def test_valid(self):
        spec = ProcessorSpec(
            target="my.NewProcessor",
            hook="before_model",
            singleton_group="my_group",
            order=15,
            reads_slots=("cost",),
        )
        assert spec.validate() == []

    def test_missing_sg(self):
        spec = ProcessorSpec(target="my.NewProcessor")
        errors = spec.validate()
        assert len(errors) == 1
        assert "singleton_group" in errors[0]

    def test_missing_target(self):
        spec = ProcessorSpec(target="")
        errors = spec.validate()
        assert any("target" in e for e in errors)

    def test_to_node_spec(self):
        spec = ProcessorSpec(
            target="my.P",
            singleton_group="g1",
            writes_slots=("log",),
            reads_slots=("cost",),
            after=("other",),
        )
        ns = spec.to_node_spec()
        assert ns["_target_"] == "my.P"
        assert ns["_singleton_group_"] == "g1"
        assert ns["_writes_slots_"] == ["log"]
        assert ns["_reads_slots_"] == ["cost"]
        assert ns["_after_"] == ["other"]


class TestToolSpec:
    def test_valid(self):
        spec = ToolSpec(tool_name="MyTool", tool_type="custom")
        assert spec.validate() == []

    def test_missing_name(self):
        spec = ToolSpec(tool_name="")
        assert len(spec.validate()) > 0

    def test_not_pascal_case(self):
        spec = ToolSpec(tool_name="my_tool")
        errors = spec.validate()
        assert any("PascalCase" in e for e in errors)


class TestGraphAdapter:
    def test_adapt_valid_processor(self):
        report = GraphAdapter().adapt_processor(
            ProcessorSpec(target="my.P", singleton_group="g1")
        )
        assert report.ok
        assert len(report.processors_added) == 1

    def test_adapt_invalid_processor(self):
        report = GraphAdapter().adapt_processor(
            ProcessorSpec(target="")
        )
        assert not report.ok

    def test_wildcard_hook_warns(self):
        report = GraphAdapter().adapt_processor(
            ProcessorSpec(target="my.P", singleton_group="g1", hook="*")
        )
        assert report.ok
        assert any("wildcard" in w for w in report.warnings)

    def test_adapt_valid_tool(self):
        report = GraphAdapter().adapt_tool(
            ToolSpec(tool_name="MyTool")
        )
        assert report.ok
        assert len(report.tools_added) == 1

    def test_adapt_all(self):
        report = GraphAdapter().adapt_all(
            processors=[ProcessorSpec(target="my.P1", singleton_group="g1")],
            tools=[ToolSpec(tool_name="MyTool")],
            templates=[TemplateSpec(template_path="templates/x.j2")],
        )
        assert report.ok
        assert report.total_new_components == 3

    def test_adapt_all_reports_errors(self):
        report = GraphAdapter().adapt_all(
            processors=[ProcessorSpec(target="my.P1", singleton_group="g1"),
                       ProcessorSpec(target="")],  # invalid
            tools=[],
            templates=[],
        )
        assert not report.ok
        assert len(report.processors_added) == 1
        assert len(report.errors) == 1


class TestValidateEvolverOutput:
    def test_empty_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = validate_evolver_output(Path(tmp))
            assert report.ok
            assert report.total_new_components == 0

    def test_with_config_processor(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "config.yaml").write_text(
                "processors:\n"
                "  - _target_: my.NewProcessor\n"
                "    _hook_: before_model\n"
                "    _singleton_group_: my_group\n"
                "    _order_: 15\n",
                encoding="utf-8",
            )
            report = validate_evolver_output(d)
            assert report.ok
            assert len(report.processors_added) == 1

    def test_config_without_sg(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "config.yaml").write_text(
                "processors:\n"
                "  - _target_: my.BadProcessor\n"
                "    _hook_: '*'\n",
                encoding="utf-8",
            )
            report = validate_evolver_output(d)
            assert not report.ok
            assert "singleton_group" in report.errors[0]

    def test_with_templates(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "templates").mkdir()
            (d / "templates" / "new.j2").write_text("hello", encoding="utf-8")
            report = validate_evolver_output(d)
            assert report.ok
            assert len(report.templates_changed) == 1
