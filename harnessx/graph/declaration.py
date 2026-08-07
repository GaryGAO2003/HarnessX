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

    Maps to ``_ProcEntry`` fields plus data dependency declarations.
    """

    target: str  # qualified class path (_target_)
    hook: str = ""  # which hook it fires on, "*" for MultiHook
    order: int = 50  # NORMAL; PRE=0, POST=100
    singleton_group: str = ""  # empty = no conflict group
    after: tuple[str, ...] = ()  # singleton_groups that must precede this
    writes_to: tuple[str, ...] = ()  # slot names written
    reads_from: tuple[str, ...] = ()  # slot names read

    source: DeclarationSource = DeclarationSource.UNKNOWN
    confidence: float = 0.0
    llm_evidence: str = ""  # free-text rationale from LLM drafting

    def is_trusted(self) -> bool:
        return self.confidence >= 0.9

    def is_usable(self) -> bool:
        return self.confidence >= 0.5


# ── defaults for well-known components ──────────────────────────────────────

# Built-in processors whose hook/order/singleton_group are known from
# code inspection.  These serve as the seed for declaration backfill.
WELL_KNOWN_DECLARATIONS: dict[str, ComponentDecl] = {
    # ── context bundle ──
    "harnessx.processors.context.system_prompt.SystemPromptProcessor": ComponentDecl(
        target="harnessx.processors.context.system_prompt.SystemPromptProcessor",
        hook="task_start",
        singleton_group="system_prompt",
        source=DeclarationSource.CODE_INTROSPECTION,
        confidence=1.0,
    ),
    "harnessx.processors.context.user_wrapper.UserWrapperProcessor": ComponentDecl(
        target="harnessx.processors.context.user_wrapper.UserWrapperProcessor",
        hook="step_start",
        singleton_group="user_wrapper",
        source=DeclarationSource.CODE_INTROSPECTION,
        confidence=1.0,
    ),
    # ── control bundle ──
    "harnessx.processors.control.loop_detection.LoopDetectionProcessor": ComponentDecl(
        target="harnessx.processors.control.loop_detection.LoopDetectionProcessor",
        hook="step_end",
        singleton_group="loop_detection",
        source=DeclarationSource.CODE_INTROSPECTION,
        confidence=1.0,
    ),
    "harnessx.processors.control.compaction.CompactionProcessor": ComponentDecl(
        target="harnessx.processors.control.compaction.CompactionProcessor",
        hook="step_end",
        singleton_group="compaction",
        source=DeclarationSource.CODE_INTROSPECTION,
        confidence=1.0,
    ),
    "harnessx.processors.control.cost_guard.CostGuardProcessor": ComponentDecl(
        target="harnessx.processors.control.cost_guard.CostGuardProcessor",
        hook="before_model",
        order=100,  # POST — runs last before model call
        singleton_group="cost_guard",
        reads_from=("cost",),
        source=DeclarationSource.CODE_INTROSPECTION,
        confidence=1.0,
    ),
    "harnessx.processors.control.parse_retry.ParseRetryProcessor": ComponentDecl(
        target="harnessx.processors.control.parse_retry.ParseRetryProcessor",
        hook="after_model",
        singleton_group="parse_retry",
        source=DeclarationSource.CODE_INTROSPECTION,
        confidence=1.0,
    ),
    "harnessx.processors.control.tool_call_correction.ToolCallCorrectionLayer": ComponentDecl(
        target="harnessx.processors.control.tool_call_correction.ToolCallCorrectionLayer",
        hook="after_model",
        singleton_group="tool_call_correction",
        source=DeclarationSource.CODE_INTROSPECTION,
        confidence=1.0,
    ),
    # ── tool bundle ──
    "harnessx.processors.tools.skill_loader.ProgressiveSkillLoader": ComponentDecl(
        target="harnessx.processors.tools.skill_loader.ProgressiveSkillLoader",
        hook="step_start",
        singleton_group="skill_loader",
        source=DeclarationSource.CODE_INTROSPECTION,
        confidence=1.0,
    ),
    "harnessx.processors.control.self_verify.SelfVerifyProcessor": ComponentDecl(
        target="harnessx.processors.control.self_verify.SelfVerifyProcessor",
        hook="after_tool",
        singleton_group="self_verify",
        source=DeclarationSource.CODE_INTROSPECTION,
        confidence=1.0,
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

    Args:
        targets: List of ``_target_`` class paths.
        hints: Optional LLM-drafted declarations keyed by target.

    Returns:
        ``{target: ComponentDecl}`` — every target gets at least an UNKNOWN entry.
    """
    result: dict[str, ComponentDecl] = {}

    for target in targets:
        # 1. Check well-known declarations (code introspection)
        known = WELL_KNOWN_DECLARATIONS.get(target)
        if known is not None:
            result[target] = known
            continue

        # 2. Check provided hints (LLM draft)
        hint = (hints or {}).get(target)
        if hint is not None:
            result[target] = hint
            continue

        # 3. Unknown — opaque to graph validation
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
    """Merge declared and observed metadata, resolving divergences.

    Observation wins for ``hook``, ``writes_to``, ``reads_from`` (runtime truth).
    Declaration wins for ``singleton_group``, ``after`` (design intent).
    """
    result: dict[str, ComponentDecl] = {}

    for target in set(declared) | set(observed):
        dec = declared.get(target)
        obs = observed.get(target)

        if dec is None:
            result[target] = obs  # type: ignore[assignment]
            continue
        if obs is None:
            result[target] = dec
            continue

        # Merge: observed hook/data deps, declared ordering
        merged = ComponentDecl(
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
        result[target] = merged

    return result


def validate_declarations(
    declarations: dict[str, ComponentDecl],
) -> list[str]:
    """Check declarations for internal contradictions.

    Returns a list of error messages (empty = valid).
    """
    errors: list[str] = []

    sg_targets: dict[str, str] = {}  # singleton_group → first target

    for target, decl in declarations.items():
        sg = decl.singleton_group
        if not sg:
            continue

        # Singleton conflict: two components with same singleton_group
        if sg in sg_targets and sg_targets[sg] != target:
            errors.append(
                f"singleton_conflict: {target} and {sg_targets[sg]} "
                f"both claim singleton_group='{sg}'"
            )
        else:
            sg_targets[sg] = target

        # After-dependency resolution
        for after_sg in decl.after:
            if after_sg not in sg_targets:
                # The after target might not be in this config — soft dep
                pass

    return errors
