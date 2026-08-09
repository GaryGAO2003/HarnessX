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

from harnessx.core.processor import PROCESSOR_HOOK_NAMES


class DeclarationSource(str, Enum):
    CODE_INTROSPECTION = "code_introspection"
    OBSERVATION_VERIFIED = "observation_verified"
    LLM_DRAFT = "llm_draft"
    UNKNOWN = "unknown"


@dataclass
class ComponentDecl:
    """Declared metadata for one processor component.

    ``hook`` vs ``hooks`` (mirrors ``_hook_`` / ``_hooks_`` serialization):
      - ``hook`` = registration bucket; ``"*"`` for a MultiHookProcessor with
        no class ``_hook``.  Legacy single-hook callers read it as coverage[0].
      - ``hooks`` = handler coverage (lifecycle-ordered); drives ATTACHED_TO
        edges (L4.1).
    """

    target: str
    hook: str = ""                          # registration bucket / legacy view
    hooks: tuple[str, ...] = ()             # handler coverage, lifecycle order
    order: int = 50                          # 50 = "unknown" sentinel (L3.3)
    singleton_group: str = ""
    after: tuple[str, ...] = ()
    writes_to: tuple[str, ...] = ()          # state slot keys
    reads_from: tuple[str, ...] = ()         # state slot keys
    reads_event_fields: tuple[str, ...] = ()     # "EventClass.field"
    writes_event_fields: tuple[str, ...] = ()    # "EventClass.field"

    source: DeclarationSource = DeclarationSource.UNKNOWN
    confidence: float = 0.0
    llm_evidence: str = ""

    # Δ17 provenance: field name → "path/to/file.py:line" citation(s).
    # Filled by harnessx.graph.bootstrap for code-introspected declarations;
    # only EXPLICITLY DECLARED fields get citations (derived defaults, e.g.
    # the "*" bucket of a hookless MHP, are not declarations and need none).
    citations: dict = field(default_factory=dict)

    def __post_init__(self):
        """hook↔hooks sync + lifecycle normalization (L3.2).

        - ``hook=`` given, ``hooks=`` not → ``hooks = (hook,)`` (legacy
          single-hook compat; ``"*"`` also lands in hooks — L4.1 expands it).
        - ``hook="*"`` is exempt from coverage sync: ``"*"`` is the
          registration bucket, not coverage.  Overwriting it with ``hooks[0]``
          would make ``_bucket`` diverge from the runtime natural bucket (I7).
          Concrete-hook buckets have coverage == [class_hook], so the sync
          never conflicts there.
        - Both given (hook != "*") → ``hooks`` wins, ``hook`` derives.
        - ``hooks`` always sorted in lifecycle order.
        """
        if self.hook and not self.hooks:
            object.__setattr__(self, "hooks", (self.hook,))
        if self.hooks and self.hook != "*" and (
            not self.hook or self.hook != self.hooks[0]
        ):
            object.__setattr__(self, "hook", self.hooks[0])
        if self.hooks and len(self.hooks) > 1:
            order = {name: i for i, name in enumerate(PROCESSOR_HOOK_NAMES)}
            sorted_hooks = tuple(sorted(self.hooks, key=lambda h: order.get(h, 999)))
            if sorted_hooks != self.hooks:
                object.__setattr__(self, "hooks", sorted_hooks)
                if self.hook != "*":
                    object.__setattr__(self, "hook", sorted_hooks[0])

    def is_trusted(self) -> bool:
        return self.confidence >= 0.9

    def is_usable(self) -> bool:
        return self.confidence >= 0.5


# ── built-in declarations (source: class introspection audit 2026-08-09) ────
#
# Audit invariant (实施约束 #6): every entry's hook / hooks / order /
# singleton_group / slot / event fields must equal what the class itself
# reports via ``get_graph_metadata(cls)`` — asserted by
# tests/graph/test_declaration_l3.py.  All 19 classes are MultiHookProcessors
# without a class ``_hook`` → registration bucket ``hook="*"`` for every entry;
# ``hooks`` is the dispatch-derived handler coverage.

WELL_KNOWN_DECLARATIONS: dict[str, ComponentDecl] = {
    # ═══ context bundle ═══
    "harnessx.processors.context.system_prompt.SystemPromptProcessor": ComponentDecl(
        target="harnessx.processors.context.system_prompt.SystemPromptProcessor",
        hook="*", hooks=("task_start",),
        order=1, singleton_group="context.system",
        source=DeclarationSource.CODE_INTROSPECTION, confidence=1.0,
    ),
    "harnessx.processors.context.user_wrapper.UserWrapperProcessor": ComponentDecl(
        target="harnessx.processors.context.user_wrapper.UserWrapperProcessor",
        hook="*", hooks=("before_model",),
        order=5, singleton_group="context.user_wrapper",
        source=DeclarationSource.CODE_INTROSPECTION, confidence=1.0,
    ),
    # ═══ memory bundle ═══
    "harnessx.processors.memory.memory_extraction.MemoryExtractionProcessor": ComponentDecl(
        target="harnessx.processors.memory.memory_extraction.MemoryExtractionProcessor",
        hook="*", hooks=("step_start",),
        order=3, singleton_group="memory.extraction",
        source=DeclarationSource.CODE_INTROSPECTION, confidence=1.0,
    ),
    "harnessx.processors.memory.memory_retrieval.MemoryRetrievalProcessor": ComponentDecl(
        target="harnessx.processors.memory.memory_retrieval.MemoryRetrievalProcessor",
        hook="*", hooks=("step_start", "step_end"),
        order=3, singleton_group="memory.retrieval",
        source=DeclarationSource.CODE_INTROSPECTION, confidence=1.0,
    ),
    # ═══ control bundle ═══
    "harnessx.processors.control.loop_detection.LoopDetectionProcessor": ComponentDecl(
        target="harnessx.processors.control.loop_detection.LoopDetectionProcessor",
        hook="*", hooks=("task_start", "step_start", "before_tool", "after_tool", "task_end"),
        order=20, singleton_group="loop_detection",
        source=DeclarationSource.CODE_INTROSPECTION, confidence=1.0,
    ),
    "harnessx.processors.control.compaction.CompactionProcessor": ComponentDecl(
        target="harnessx.processors.control.compaction.CompactionProcessor",
        hook="*", hooks=("task_start", "step_start", "before_model", "task_end"),
        order=8, singleton_group="compaction",
        source=DeclarationSource.CODE_INTROSPECTION, confidence=1.0,
    ),
    "harnessx.processors.control.cost_guard.CostGuardProcessor": ComponentDecl(
        target="harnessx.processors.control.cost_guard.CostGuardProcessor",
        hook="*", hooks=("before_model",),
        order=10, singleton_group="cost_guard",
        reads_event_fields=("BeforeModelEvent.cumulative_cost_usd",),
        source=DeclarationSource.CODE_INTROSPECTION, confidence=1.0,
    ),
    "harnessx.processors.control.parse_retry.ParseRetryProcessor": ComponentDecl(
        target="harnessx.processors.control.parse_retry.ParseRetryProcessor",
        hook="*", hooks=("after_model", "task_end"),
        order=10, singleton_group="parse_retry",
        source=DeclarationSource.CODE_INTROSPECTION, confidence=1.0,
    ),
    "harnessx.processors.control.self_verify.SelfVerifyProcessor": ComponentDecl(
        target="harnessx.processors.control.self_verify.SelfVerifyProcessor",
        hook="*", hooks=("task_start", "before_model", "after_model",
                         "before_tool", "after_tool", "task_end"),
        order=90, singleton_group="self_verify",
        source=DeclarationSource.CODE_INTROSPECTION, confidence=1.0,
    ),
    "harnessx.processors.control.token_budget.TokenBudgetProcessor": ComponentDecl(
        target="harnessx.processors.control.token_budget.TokenBudgetProcessor",
        hook="*", hooks=("step_start",),
        order=10, singleton_group="token_budget",
        source=DeclarationSource.CODE_INTROSPECTION, confidence=1.0,
    ),
    "harnessx.processors.control.step_countdown.StepCountdownProcessor": ComponentDecl(
        target="harnessx.processors.control.step_countdown.StepCountdownProcessor",
        hook="*", hooks=("task_start", "step_start", "before_model", "task_end"),
        order=40, singleton_group="step_countdown",
        source=DeclarationSource.CODE_INTROSPECTION, confidence=1.0,
    ),
    # ═══ tool bundle ═══
    "harnessx.processors.tools.skill_loader.ProgressiveSkillLoader": ComponentDecl(
        target="harnessx.processors.tools.skill_loader.ProgressiveSkillLoader",
        hook="*", hooks=("task_start", "step_start", "before_model", "task_end"),
        order=12, singleton_group="progressive_skill_loader",
        source=DeclarationSource.CODE_INTROSPECTION, confidence=1.0,
    ),
    "harnessx.processors.tools.tool_filter.ToolFilterProcessor": ComponentDecl(
        target="harnessx.processors.tools.tool_filter.ToolFilterProcessor",
        hook="*", hooks=("step_start",),
        order=6, singleton_group="tools.filter",
        source=DeclarationSource.CODE_INTROSPECTION, confidence=1.0,
    ),
    "harnessx.processors.tools.tool_whitelist.ToolWhitelistProcessor": ComponentDecl(
        target="harnessx.processors.tools.tool_whitelist.ToolWhitelistProcessor",
        hook="*", hooks=("before_tool",),
        order=10, singleton_group="tool_whitelist",
        source=DeclarationSource.CODE_INTROSPECTION, confidence=1.0,
    ),
    # ═══ observability ═══
    "harnessx.processors.observability.otel_proc.OTelProcessor": ComponentDecl(
        target="harnessx.processors.observability.otel_proc.OTelProcessor",
        hook="*", hooks=("step_end", "task_end"),
        order=30, singleton_group="otel",
        source=DeclarationSource.CODE_INTROSPECTION, confidence=1.0,
    ),
    "harnessx.processors.observability.checkpoint.CheckpointProcessor": ComponentDecl(
        target="harnessx.processors.observability.checkpoint.CheckpointProcessor",
        hook="*", hooks=("step_end",),
        order=10, singleton_group="checkpoint",
        source=DeclarationSource.CODE_INTROSPECTION, confidence=1.0,
    ),
    # ═══ evaluation ═══
    "harnessx.processors.evaluation.evaluation.EvaluationProcessor": ComponentDecl(
        target="harnessx.processors.evaluation.evaluation.EvaluationProcessor",
        hook="*", hooks=("task_end",),
        order=0, singleton_group="evaluation",
        source=DeclarationSource.CODE_INTROSPECTION, confidence=1.0,
    ),
    "harnessx.processors.evaluation.llm_judge.LLMJudgeProcessor": ComponentDecl(
        target="harnessx.processors.evaluation.llm_judge.LLMJudgeProcessor",
        hook="*", hooks=("task_end",),
        order=0, singleton_group="llm_judge",
        source=DeclarationSource.CODE_INTROSPECTION, confidence=1.0,
    ),
    # ═══ multi-model ═══
    "harnessx.processors.multi_model.model_router.ModelRouterProcessor": ComponentDecl(
        target="harnessx.processors.multi_model.model_router.ModelRouterProcessor",
        hook="*", hooks=("task_start", "task_end"),
        order=20, singleton_group="model_router",
        writes_to=("model.route",),
        source=DeclarationSource.CODE_INTROSPECTION, confidence=1.0,
    ),
}


# ── Δ9 citation gate ────────────────────────────────────────────────────────


def is_cited(decl: ComponentDecl) -> bool:
    """Δ9: does this declaration carry usable provenance?

    - code introspection WITH file:line citations → cited;
    - observation-verified → cited (the observations are the provenance);
    - LLM drafts are NEVER validator-eligible — ``llm_evidence`` is a
      rationale, not a source location; they stay retrieval-only until
      observation verifies them (污染扩散 prevention);
    - unknown / uncited code claims → not cited.
    """
    if decl.source is DeclarationSource.CODE_INTROSPECTION:
        return bool(decl.citations)
    if decl.source is DeclarationSource.OBSERVATION_VERIFIED:
        return True
    return False


def citation_gate(
    decls: "dict[str, ComponentDecl]",
) -> "tuple[dict[str, ComponentDecl], dict[str, ComponentDecl]]":
    """Split declarations into (validator_eligible, retrieval_only) — Δ9.

    Uncited declarations are not discarded — they remain available for
    retrieval/reconciliation — but the validator never consumes them.
    """
    eligible: dict[str, ComponentDecl] = {}
    retrieval: dict[str, ComponentDecl] = {}
    for target, decl in decls.items():
        (eligible if is_cited(decl) else retrieval)[target] = decl
    return eligible, retrieval


_CITED_WKD_CACHE: "dict[str, ComponentDecl] | None" = None


def cited_well_known() -> "dict[str, ComponentDecl]":
    """The WKD table enriched with Δ17 bootstrap citations (cached).

    ``WELL_KNOWN_DECLARATIONS`` stays a static, citation-free snapshot so
    ``to_graph()`` remains pure-read (L4.5).  Gate-time consumers use THIS
    view instead — it imports the processor classes once and attaches exact
    file:line provenance, which is what keeps the Δ9 gate from starving the
    validator (the ordering hazard the mechanism assessment flagged).
    """
    global _CITED_WKD_CACHE
    if _CITED_WKD_CACHE is None:
        from .bootstrap import bootstrap_well_known

        _CITED_WKD_CACHE = bootstrap_well_known()
    return _CITED_WKD_CACHE


# ── backfill logic ─────────────────────────────────────────────────────────


def backfill_declarations(
    targets: list[str],
    *,
    hints: dict[str, ComponentDecl] | None = None,
    require_citations: bool = False,
) -> dict[str, ComponentDecl]:
    """Produce the best available declaration for each target.

    Resolution order: code_introspection > observation_verified > llm_draft.

    ``require_citations=True`` applies the Δ9 gate: well-known lookups go
    through the citation-enriched view, and uncited hints (LLM drafts,
    unverified claims) resolve to the UNKNOWN placeholder — the validator
    sees nothing it cannot prove.  Default False preserves the legacy
    retrieval behaviour.
    """
    result: dict[str, ComponentDecl] = {}
    wkd = cited_well_known() if require_citations else WELL_KNOWN_DECLARATIONS

    for target in targets:
        known = wkd.get(target)
        if known is not None and (not require_citations or is_cited(known)):
            result[target] = known
            continue
        hint = (hints or {}).get(target)
        if hint is not None and (not require_citations or is_cited(hint)):
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
    """Merge declared and observed metadata (L3.4).

    Multi-hook aware: ``hooks`` merges as a whole tuple (observed wins if
    non-empty) — never collapsed to a single hook.  All data-channel fields
    follow the same per-field ``obs.X if obs.X else dec.X`` rule.
    """
    result: dict[str, ComponentDecl] = {}
    for target in set(declared) | set(observed):
        dec = declared.get(target)
        obs = observed.get(target)
        if dec is None:
            result[target] = obs  # type: ignore[assignment]
        elif obs is None:
            result[target] = dec
        else:
            hooks = obs.hooks if obs.hooks else dec.hooks
            result[target] = ComponentDecl(
                target=target,
                hook=dec.hook if dec.hook == "*" else "",  # bucket survives; else derive from hooks
                hooks=hooks,
                order=dec.order,
                singleton_group=dec.singleton_group,
                after=dec.after,
                writes_to=obs.writes_to if obs.writes_to else dec.writes_to,
                reads_from=obs.reads_from if obs.reads_from else dec.reads_from,
                reads_event_fields=(obs.reads_event_fields if obs.reads_event_fields
                                    else dec.reads_event_fields),
                writes_event_fields=(obs.writes_event_fields if obs.writes_event_fields
                                     else dec.writes_event_fields),
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
