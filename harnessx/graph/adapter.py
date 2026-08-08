"""GraphAdapter — single entry-point for all AEGIS-created components.

Every new processor, tool, or config change produced by the Evolver
MUST pass through this adapter to be registered in the graph IR.
Without graph registration, the component is invisible to selective
retest, dedup, and impact analysis.

Contract
--------
Any component created by the AEGIS pipeline must declare:

    Processor
        _target_           (qualified class path)
        _hook_             (single hook or "*")
        _singleton_group_  (unique within the config)
        _order_            (within-hook priority)
        _after_            (optional: ordering deps)
        _writes_slots_     (optional: slot names written)
        _reads_slots_      (optional: slot names read)

    Tool
        tool_name          (PascalCase: "WebSearch", "Bash", ...)
        tool_type          ("builtin" | "custom")
        skill_deps         (optional: skill-level dependencies)

    Template / Prompt
        template_path      (path to .j2 template)
        template_type      ("system_prompt" | "user_wrapper" | "tool_prompt")
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


class ComponentKind(str, Enum):
    PROCESSOR = "processor"
    TOOL = "tool"
    TEMPLATE = "template"
    CONFIG = "config"


# ── metadata specs ──────────────────────────────────────────────────────────


@dataclass
class ProcessorSpec:
    """Metadata a new processor MUST provide for graph registration.

    Separates two data channels:
      - ``writes_slots`` / ``reads_slots`` — State slot keys (kv store).
      - ``reads_event_fields`` — event-channel data the processor
        reads from lifecycle events (e.g. ``cumulative_cost_usd``).

    The ``hook`` field accepts ``\"*\"`` for wildcard.  Single-hook
    processors should set it to a specific hook name.
    """

    target: str
    hook: str = "*"
    singleton_group: str = ""
    order: int = 50
    after: tuple[str, ...] = ()
    writes_slots: tuple[str, ...] = ()       # State slot keys
    reads_slots: tuple[str, ...] = ()        # State slot keys
    reads_event_fields: tuple[str, ...] = ()  # Event-channel fields

    def validate(self) -> list[str]:
        """Return list of missing required fields."""
        missing = []
        if not self.target:
            missing.append("target (qualified class path)")
        if not self.singleton_group:
            missing.append("singleton_group (unique within config)")
        return missing

    def to_node_spec(self) -> dict[str, Any]:
        """Convert to the format GraphEdit.INSERT_NODE expects."""
        spec: dict[str, Any] = {
            "_target_": self.target,
            "_hook_": self.hook,
            "_singleton_group_": self.singleton_group,
            "_order_": self.order,
        }
        if self.after:
            spec["_after_"] = list(self.after)
        if self.writes_slots:
            spec["_writes_slots_"] = list(self.writes_slots)
        if self.reads_slots:
            spec["_reads_slots_"] = list(self.reads_slots)
        if self.reads_event_fields:
            spec["_reads_event_fields_"] = list(self.reads_event_fields)
        return spec


@dataclass
class ToolSpec:
    """Metadata a new tool MUST provide for graph registration."""

    tool_name: str        # PascalCase
    tool_type: str = "custom"     # "builtin" | "custom"
    description: str = ""
    skill_deps: tuple[str, ...] = ()  # skill-level dependency IDs

    def validate(self) -> list[str]:
        missing = []
        if not self.tool_name:
            missing.append("tool_name")
        elif not self.tool_name[0].isupper():
            missing.append("tool_name must be PascalCase")
        return missing

    def to_node_spec(self) -> dict[str, Any]:
        return {
            "tool_name": self.tool_name,
            "tool_type": self.tool_type,
            "description": self.description,
            "skill_deps": list(self.skill_deps),
        }


@dataclass
class TemplateSpec:
    """Metadata for a new prompt template."""

    template_path: str
    template_type: str = "system_prompt"

    def validate(self) -> list[str]:
        missing = []
        if not self.template_path:
            missing.append("template_path")
        return missing


# ── adapter ─────────────────────────────────────────────────────────────────


@dataclass
class GraphAdaptationReport:
    """Result of adapting AEGIS output through the graph layer."""

    processors_added: list[ProcessorSpec] = field(default_factory=list)
    tools_added: list[ToolSpec] = field(default_factory=list)
    templates_changed: list[TemplateSpec] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return len(self.errors) == 0

    @property
    def total_new_components(self) -> int:
        return len(self.processors_added) + len(self.tools_added) + len(self.templates_changed)


class GraphAdapter:
    """Adapt AEGIS Evolver output to graph-compatible format.

    Usage in the AEGIS pipeline::

        adapter = GraphAdapter()
        report = adapter.adapt_evolver_output(evolver_result)
        if not report.ok:
            reject_candidate(report.errors)
        # proceed with graph edit + validation
    """

    # Required metadata fields for different component types
    PROCESSOR_REQUIRED = {"_target_", "_singleton_group_"}
    PROCESSOR_RECOMMENDED = {"_hook_", "_order_", "_after_", "_writes_slots_", "_reads_slots_"}

    def adapt_processor(self, spec: ProcessorSpec) -> GraphAdaptationReport:
        """Validate and register a new processor spec."""
        report = GraphAdaptationReport()
        missing = spec.validate()
        if missing:
            report.errors.append(
                f"Processor '{spec.target}' missing: {', '.join(missing)}"
            )
        else:
            report.processors_added.append(spec)
            # Check recommended fields
            missing_rec = []
            if spec.hook == "*":
                missing_rec.append("hook='*' (wildcard) — narrow to single hook if possible")
            if not spec.writes_slots and not spec.reads_slots:
                missing_rec.append("no slot deps — impact analysis will be broad")
            if missing_rec:
                report.warnings.extend(
                    f"Processor '{spec.target}': {m}" for m in missing_rec
                )
        return report

    def adapt_tool(self, spec: ToolSpec) -> GraphAdaptationReport:
        """Validate and register a new tool spec."""
        report = GraphAdaptationReport()
        missing = spec.validate()
        if missing:
            report.errors.append(
                f"Tool '{spec.tool_name}' missing: {', '.join(missing)}"
            )
        else:
            report.tools_added.append(spec)
        return report

    def adapt_template(self, spec: TemplateSpec) -> GraphAdaptationReport:
        """Register a template change."""
        report = GraphAdaptationReport()
        missing = spec.validate()
        if missing:
            report.errors.append(f"Template missing: {', '.join(missing)}")
        else:
            report.templates_changed.append(spec)
        return report

    def adapt_all(
        self,
        processors: list[ProcessorSpec],
        tools: list[ToolSpec],
        templates: list[TemplateSpec],
    ) -> GraphAdaptationReport:
        """Adapt a batch of AEGIS output components."""
        report = GraphAdaptationReport()
        for p in processors:
            r = self.adapt_processor(p)
            report.processors_added.extend(r.processors_added)
            report.errors.extend(r.errors)
            report.warnings.extend(r.warnings)
        for t in tools:
            r = self.adapt_tool(t)
            report.tools_added.extend(r.tools_added)
            report.errors.extend(r.errors)
            report.warnings.extend(r.warnings)
        for t in templates:
            r = self.adapt_template(t)
            report.templates_changed.extend(r.templates_changed)
            report.errors.extend(r.errors)
            report.warnings.extend(r.warnings)
        return report


# ── AEGIS integration helpers ───────────────────────────────────────────────


def validate_evolver_output(
    candidate_dir: Path,
) -> GraphAdaptationReport:
    """Scan an Evolver output directory and validate all new components.

    Called by the graph gate before measurement.
    """
    report = GraphAdaptationReport()

    # 1. Check config.yaml for new processors
    config_path = candidate_dir / "config.yaml"
    if config_path.exists():
        try:
            import yaml
            with open(config_path, encoding="utf-8") as f:
                data = yaml.safe_load(f)
            processors = data.get("processors", []) if isinstance(data, dict) else []
            for p in processors:
                if not isinstance(p, dict):
                    continue
                target = p.get("_target_", "")
                if not target:
                    continue
                spec = ProcessorSpec(
                    target=target,
                    hook=p.get("_hook_", "*"),
                    singleton_group=p.get("_singleton_group_", ""),
                    order=p.get("_order_", 50),
                    after=tuple(p.get("_after_", []) or []),
                    writes_slots=tuple(p.get("_writes_slots_", []) or []),
                    reads_slots=tuple(p.get("_reads_slots_", []) or []),
                )
                r = GraphAdapter().adapt_processor(spec)
                report.processors_added.extend(r.processors_added)
                report.errors.extend(r.errors)
                report.warnings.extend(r.warnings)
        except Exception as e:
            report.errors.append(f"Cannot parse config.yaml: {e}")

    # 2. Check for new tool definitions (if tools dir exists)
    tools_dir = candidate_dir / "tools"
    if tools_dir.is_dir():
        for py_file in tools_dir.glob("*.py"):
            if py_file.name.startswith("_"):
                continue
            # Extract tool name from filename
            tool_name = py_file.stem
            # PascalCase check
            spec = ToolSpec(tool_name=tool_name, tool_type="custom")
            r = GraphAdapter().adapt_tool(spec)
            report.tools_added.extend(r.tools_added)
            report.errors.extend(r.errors)

    # 3. Check for new templates
    templates_dir = candidate_dir / "templates"
    if templates_dir.is_dir():
        for j2_file in templates_dir.glob("*.j2"):
            spec = TemplateSpec(
                template_path=str(j2_file),
                template_type="system_prompt",
            )
            report.templates_changed.append(spec)

    return report
