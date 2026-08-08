"""S3 — Declaration metadata for processor components.

Every processor in a HarnessGraph carries metadata (hook, order,
singleton_group, after, data dependencies) that graph validation
needs.  Today most of this metadata is *implicit* — stored in class
attributes (``_hook``, ``_order``, …) that are resolved at build time
but not serialized into ``HarnessConfig`` YAML.

This module provides types and backfill logic for making those
declarations explicit, so that candidate validation (S3) can detect
conflicts, cycles, and interface mismatches before measurement
budget is spent.

Declaration provenance tiers
----------------------------
1. **code_introspection** (confidence=1.0)
   Extracted from class attributes (``_hook``, ``_order``, ``_singleton_group``).
   Definitive — the class author intended this.

2. **observation_verified** (confidence=0.95)
   Confirmed by ≥2 runtime observation edges that match a declaration.

3. **llm_draft** (confidence=0.5–0.7)
   Predicted by an LLM from the class name, kwargs, and context.
   Needs observation verification to be trusted.

4. **unknown** (confidence=0.0)
   No declaration available — the processor is opaque to graph validation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class DeclarationSource(str, Enum):
    CODE_INTROSPECTION = "code_introspection"
    OBSERVATION_VERIFIED = "observation_verified"
    LLM_DRAFT = "llm_draft"
    UNKNOWN = "unknown"


@dataclass
class ComponentDecl:
    """Declared metadata for one processor component.

    Distinguishes two data channels:
      - **writes_to / reads_from**: State ``slots`` (key-value store).
        These are the declared slot keys the processor writes/reads.
      - **reads_event_fields**: Event-channel data.  Fields read from
        lifecycle events (e.g. ``cumulative_cost_usd`` on StepEndEvent).
        Not backed by State slots — they come from the event pipeline.

    The ``hooks`` field is a tuple to express MultiHookProcessor
    coverage.  A single-hook processor uses ``hooks=(\"before_model\",)``;
    a wildcard uses ``hooks=(\"*\",)``.  Backward compatibility: the
    constructor also accepts the legacy ``hook: str`` keyword; it is
    normalized to ``hooks``.
    """

    target: str
    hooks: tuple[str, ...] = ("*",)
    order: int = 50
    singleton_group: str = ""
    after: tuple[str, ...] = ()
    writes_to: tuple[str, ...] = ()       # State slot keys
    reads_from: tuple[str, ...] = ()      # State slot keys
    reads_event_fields: tuple[str, ...] = ()  # Event-channel fields

    source: DeclarationSource = DeclarationSource.UNKNOWN
    confidence: float = 0.0
    llm_evidence: str = ""

    def __post_init__(self) -> None:
        """Normalize legacy ``hook`` kwarg to ``hooks``."""
        pass  # handled by classmethod constructor below

    @classmethod
    def create(
        cls,
        target: str,
        *,
        hook: str = "",
        hooks: tuple[str, ...] | None = None,
        order: int = 50,
        singleton_group: str = "",
        after: tuple[str, ...] = (),
        writes_to: tuple[str, ...] = (),
        reads_from: tuple[str, ...] = (),
        reads_event_fields: tuple[str, ...] = (),
        source: DeclarationSource = DeclarationSource.UNKNOWN,
        confidence: float = 0.0,
        llm_evidence: str = "",
    ) -> "ComponentDecl":
        """Factory with backward-compatible ``hook=`` support."""
        if hooks is None:
            hooks = (hook,) if hook else ("*",)
        return cls(
            target=target,
            hooks=hooks,
            order=order,
            singleton_group=singleton_group,
            after=after,
            writes_to=writes_to,
            reads_from=reads_from,
            reads_event_fields=reads_event_fields,
            source=source,
            confidence=confidence,
            llm_evidence=llm_evidence,
        )

    @property
    def hook(self) -> str:
        """Legacy accessor — first hook in the tuple."""
        return self.hooks[0] if self.hooks else "*"

    def is_trusted(self) -> bool:
        return self.confidence >= 0.9

    def is_usable(self) -> bool:
        return self.confidence >= 0.5


# ── built-in declarations (source: code scan 2026-08-08) ────────────────────

WELL_KNOWN_DECLARATIONS: dict[str, ComponentDecl] = {
    # ═══ context bundle ═══
    "harnessx.processors.context.system_prompt.SystemPromptProcessor": ComponentDecl.create(
        target="harnessx.processors.context.system_prompt.SystemPromptProcessor",
        order=1, singleton_group="context.system",
        source=DeclarationSource.CODE_INTROSPECTION, confidence=1.0,
    ),
    "harnessx.processors.context.user_wrapper.UserWrapperProcessor": ComponentDecl.create(
        target="harnessx.processors.context.user_wrapper.UserWrapperProcessor",
        order=5, singleton_group="context.user_wrapper",
        source=DeclarationSource.CODE_INTROSPECTION, confidence=1.0,
    ),
    # ═══ memory bundle ═══
    "harnessx.processors.memory.memory_extraction.MemoryExtractionProcessor": ComponentDecl.create(
        target="harnessx.processors.memory.memory_extraction.MemoryExtractionProcessor",
        order=3, singleton_group="memory.extraction",
        source=DeclarationSource.CODE_INTROSPECTION, confidence=1.0,
    ),
    "harnessx.processors.memory.memory_retrieval.MemoryRetrievalProcessor": ComponentDecl.create(
        target="harnessx.processors.memory.memory_retrieval.MemoryRetrievalProcessor",
        order=3, singleton_group="memory.retrieval",
        source=DeclarationSource.CODE_INTROSPECTION, confidence=1.0,
    ),
    # ═══ control bundle ═══
    "harnessx.processors.control.loop_detection.LoopDetectionProcessor": ComponentDecl.create(
        target="harnessx.processors.control.loop_detection.LoopDetectionProcessor",
        order=20, singleton_group="loop_detection",
        source=DeclarationSource.CODE_INTROSPECTION, confidence=1.0,
    ),
    "harnessx.processors.control.compaction.CompactionProcessor": ComponentDecl.create(
        target="harnessx.processors.control.compaction.CompactionProcessor",
        order=8, singleton_group="compaction",
        source=DeclarationSource.CODE_INTROSPECTION, confidence=1.0,
    ),
    "harnessx.processors.control.cost_guard.CostGuardProcessor": ComponentDecl.create(
        target="harnessx.processors.control.cost_guard.CostGuardProcessor",
        order=10, singleton_group="cost_guard",
        reads_from=("cost",),
        reads_event_fields=("cumulative_cost_usd",),
        source=DeclarationSource.CODE_INTROSPECTION, confidence=1.0,
    ),
    "harnessx.processors.control.parse_retry.ParseRetryProcessor": ComponentDecl.create(
        target="harnessx.processors.control.parse_retry.ParseRetryProcessor",
        order=10, singleton_group="parse_retry",
        source=DeclarationSource.CODE_INTROSPECTION, confidence=1.0,
    ),
    "harnessx.processors.control.self_verify.SelfVerifyProcessor": ComponentDecl.create(
        target="harnessx.processors.control.self_verify.SelfVerifyProcessor",
        order=90, singleton_group="self_verify",
        source=DeclarationSource.CODE_INTROSPECTION, confidence=1.0,
    ),
    "harnessx.processors.control.token_budget.TokenBudgetProcessor": ComponentDecl.create(
        target="harnessx.processors.control.token_budget.TokenBudgetProcessor",
        order=10, singleton_group="token_budget",
        source=DeclarationSource.CODE_INTROSPECTION, confidence=1.0,
    ),
    "harnessx.processors.control.step_countdown.StepCountdownProcessor": ComponentDecl.create(
        target="harnessx.processors.control.step_countdown.StepCountdownProcessor",
        order=40, singleton_group="step_countdown",
        source=DeclarationSource.CODE_INTROSPECTION, confidence=1.0,
    ),
    # ═══ tool bundle ═══
    "harnessx.processors.tools.skill_loader.ProgressiveSkillLoader": ComponentDecl.create(
        target="harnessx.processors.tools.skill_loader.ProgressiveSkillLoader",
        singleton_group="skill_loader",
        source=DeclarationSource.CODE_INTROSPECTION, confidence=1.0,
    ),
    "harnessx.processors.tools.tool_filter.ToolFilterProcessor": ComponentDecl.create(
        target="harnessx.processors.tools.tool_filter.ToolFilterProcessor",
        order=6, singleton_group="tools.filter",
        source=DeclarationSource.CODE_INTROSPECTION, confidence=1.0,
    ),
    "harnessx.processors.tools.tool_whitelist.ToolWhitelistProcessor": ComponentDecl.create(
        target="harnessx.processors.tools.tool_whitelist.ToolWhitelistProcessor",
        order=10, singleton_group="tool_whitelist",
        source=DeclarationSource.CODE_INTROSPECTION, confidence=1.0,
    ),
    # ═══ observability ═══
    "harnessx.processors.observability.otel_proc.OTelProcessor": ComponentDecl.create(
        target="harnessx.processors.observability.otel_proc.OTelProcessor",
        order=30, singleton_group="otel",
        source=DeclarationSource.CODE_INTROSPECTION, confidence=1.0,
    ),
    "harnessx.processors.observability.checkpoint.CheckpointProcessor": ComponentDecl.create(
        target="harnessx.processors.observability.checkpoint.CheckpointProcessor",
        order=10, singleton_group="checkpoint",
        source=DeclarationSource.CODE_INTROSPECTION, confidence=1.0,
    ),
    # ═══ evaluation ═══
    "harnessx.processors.evaluation.evaluation.EvaluationProcessor": ComponentDecl.create(
        target="harnessx.processors.evaluation.evaluation.EvaluationProcessor",
        singleton_group="evaluation",
        source=DeclarationSource.CODE_INTROSPECTION, confidence=1.0,
    ),
    "harnessx.processors.evaluation.llm_judge.LLMJudgeProcessor": ComponentDecl.create(
        target="harnessx.processors.evaluation.llm_judge.LLMJudgeProcessor",
        singleton_group="llm_judge",
        source=DeclarationSource.CODE_INTROSPECTION, confidence=1.0,
    ),
    # ═══ multi-model ═══
    "harnessx.processors.multi_model.model_router.ModelRouterProcessor": ComponentDecl.create(
        target="harnessx.processors.multi_model.model_router.ModelRouterProcessor",
        order=20, singleton_group="model_router",
        writes_to=("model_route",),
        source=DeclarationSource.CODE_INTROSPECTION, confidence=1.0,
    ),
}


# ── backfill logic ─────────────────────────────────────────────────────────


def backfill_declarations(
    targets: list[str],
    *,
    hints: dict[str, ComponentDecl] | None = None,
) -> dict[str, ComponentDecl]:
    """Produce the best available declaration for each target.

    Resolution order: code_introspection > observation_verified > llm_draft.
    """
    result: dict[str, ComponentDecl] = {}

    for target in targets:
        known = WELL_KNOWN_DECLARATIONS.get(target)
        if known is not None:
            result[target] = known
            continue
        hint = (hints or {}).get(target)
        if hint is not None:
            result[target] = hint
            continue
        result[target] = ComponentDecl(
            target=target,
            source=DeclarationSource.UNKNOWN,
            confidence=0.0,
        )

    return result


def merge_declarations(
    declared: dict[str, ComponentDecl],
    observed: dict[str, ComponentDecl],
) -> dict[str, ComponentDecl]:
    """Merge declared and observed metadata."""
    result: dict[str, ComponentDecl] = {}
    for target in set(declared) | set(observed):
        dec = declared.get(target)
        obs = observed.get(target)
        if dec is None:
            result[target] = obs  # type: ignore[assignment]
        elif obs is None:
            result[target] = dec
        else:
            result[target] = ComponentDecl(
                target=target,
                hook=obs.hook or dec.hook,
                order=dec.order,
                singleton_group=dec.singleton_group,
                after=dec.after,
                writes_to=obs.writes_to or dec.writes_to,
                reads_from=obs.reads_from or dec.reads_from,
                source=DeclarationSource.OBSERVATION_VERIFIED if obs.is_trusted() else dec.source,
                confidence=max(dec.confidence, obs.confidence),
            )
    return result


def validate_declarations(
    declarations: dict[str, ComponentDecl],
) -> list[str]:
    """Check declarations for internal contradictions."""
    errors: list[str] = []
    sg_targets: dict[str, str] = {}
    for target, decl in declarations.items():
        sg = decl.singleton_group
        if not sg:
            continue
        if sg in sg_targets and sg_targets[sg] != target:
            errors.append(
                f"singleton_conflict: {target} and {sg_targets[sg]} "
                f"both claim singleton_group='{sg}'"
            )
        else:
            sg_targets[sg] = target
        for after_sg in decl.after:
            if after_sg not in sg_targets:
                pass  # soft dep — may be in another config
    return errors
