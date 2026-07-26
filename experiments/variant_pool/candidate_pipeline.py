# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""Deterministic, injectable AEGIS-style candidate batch orchestration.

This is a reconstruction boundary, not a claim that one existing meta-agent is
the paper's four roles.  Digester, Planner, Evolver and Critic are independent
``Protocol`` stages so real model-backed adapters can be supplied later and
offline tests can use scripts.  The orchestration itself is deterministic:
structured-candidate validation, de-duplication, candidate-id ordering, the
paper's ``K_t=4`` cap, and at most one Critic revision cycle.

The result is a *gate queue*.  It never applies a candidate and deliberately
does not import :mod:`.gate`; the deterministic gate remains the only shipping
authority.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, Sequence

from .critic import (
    CriticContext,
    CriticReview,
    CriticStage,
    RevisionRequest,
)
from .evidence import TaskDigest
from .manifest import CandidateArtifact

DEFAULT_K_T = 4
MAX_K_T = 4
# [OURS] Algorithm 1 names alpha but does not publish one unique operational
# value.  Keep the reconstruction default explicit, configurable and auditable.
OURS_DEFAULT_ACTIONABILITY_THRESHOLD = 0.5
OURS_ACTIONABILITY_THRESHOLD_PROVENANCE = (
    "OURS: configurable reconstruction parameter; the paper does not specify "
    "one unique operational alpha"
)
LEGACY_ACTIONABILITY_ASSUMPTION = 1.0


@dataclass(frozen=True)
class PipelineContext:
    round_idx: int
    target_variant: str
    current_config_path: Path
    trajectories_dir: Path
    output_root: Path
    memo_path: Path | None = None
    regressions: tuple[str, ...] = ()
    failure_buckets: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "current_config_path", Path(self.current_config_path))
        object.__setattr__(self, "trajectories_dir", Path(self.trajectories_dir))
        object.__setattr__(self, "output_root", Path(self.output_root))
        if self.memo_path is not None:
            object.__setattr__(self, "memo_path", Path(self.memo_path))


@dataclass(frozen=True)
class CandidateBrief:
    """One Planner recommendation for the Evolver."""

    brief_id: str
    buckets: tuple[str, ...]
    task_ids: tuple[str, ...]
    rationale: str = ""


@dataclass(frozen=True)
class PlanningArtifact:
    """Structured Planner output; details beyond these fields are adapter-owned."""

    target_variant: str
    briefs: tuple[CandidateBrief, ...] = ()
    notes: tuple[str, ...] = ()
    #: [A2] Set ``True`` ONLY by an LLM Planner that itself returned zero briefs
    #: (an explicitly empty mutation landscape, paper §4.3) — the second half of
    #: EXP-E07's real short-circuit. The deterministic Planner and every fallback
    #: leave this ``False``, so the legacy empty-briefs path (``short_circuit=
    #: "empty_landscape"``) stays byte-identical. Backward-compatible default.
    empty_landscape: bool = False


@dataclass(frozen=True)
class DigesterRoundArtifact:
    """Auditable round-level Digester output used by selective invocation.

    ``contract_mode`` is ``"structured"`` for the Algorithm 1-shaped
    contract.  ``"legacy_sequence_deviation"`` is reserved for the explicit
    compatibility adapter used when an older Digester returns only a sequence
    of :class:`TaskDigest` objects.
    """

    digests: tuple[TaskDigest, ...]
    actionability: float
    rationale: str
    contract_mode: str = "structured"

    def __post_init__(self) -> None:
        digests = tuple(self.digests)
        if any(not isinstance(digest, TaskDigest) for digest in digests):
            raise TypeError("digests must contain only TaskDigest objects")
        object.__setattr__(
            self,
            "digests",
            tuple(
                sorted(
                    digests,
                    key=lambda digest: (
                        digest.round_idx,
                        digest.task_id,
                        digest.variant_id,
                    ),
                )
            ),
        )

        actionability = float(self.actionability)
        if not math.isfinite(actionability) or not 0.0 <= actionability <= 1.0:
            raise ValueError(
                "actionability must be a finite float in [0, 1], "
                f"got {self.actionability!r}"
            )
        object.__setattr__(self, "actionability", actionability)

        rationale = self.rationale.strip()
        if not rationale:
            raise ValueError("Digester round rationale must be non-empty")
        object.__setattr__(self, "rationale", rationale)
        if self.contract_mode not in {
            "structured",
            "legacy_sequence_deviation",
        }:
            raise ValueError(f"unsupported Digester contract_mode: {self.contract_mode!r}")


def repo_candidate_id(round_idx: int, slot: int) -> str:
    """Digits-only candidate id the repo's own evidence gate accepts.

    ``validate_workflow._evidence`` scans ``candidates.md`` for
    ``^##\\s+Candidate\\s+(C-\\d+)\\b`` — **digits only** after ``C-`` — so our
    paper-shape slot ids (``C-R1-01``, Table 9 p.36) are rejected on sight
    (the ``R``). Slot ids therefore stay paper-shape everywhere on our side
    (allocator, manifest, gate, reports) and are translated to this outward
    alias only at the repo boundary, via :func:`outward_candidate_id`.
    Packing round and slot into fixed-width digits keeps the alias decodable:
    ``C-0103`` = round 1, slot 3.
    """
    return f"C-{round_idx:02d}{slot:02d}"


_PAPER_SLOT_ID = re.compile(r"^C-R(\d+)-(\d+)(?:-revision-(\d+))?$")


def outward_candidate_id(candidate_id: str) -> str:
    """Repo-gate-safe alias for one of our paper-shape candidate ids.

    Pure string mapping used at exactly two seams: the candidate contract
    handed to the meta-agent (which is told to use the alias verbatim, so its
    ``candidates.md`` heading passes the repo's ``C-\\d+`` regex) and the
    recipe's manifest intake, which maps an echoed alias back to the slot id.
    A revision slot gets its parent's alias plus the two-digit revision
    ordinal (``C-R1-01-revision-01`` -> ``C-010101``) so a per-variant journal
    never keys two entries the same. Ids that are not paper-shape (already
    outward, or legacy) pass through on their digits.
    """
    match = _PAPER_SLOT_ID.match(candidate_id)
    if match is None:
        digits = "".join(ch for ch in candidate_id if ch.isdigit())
        return f"C-{digits or '0'}"
    alias = repo_candidate_id(int(match.group(1)), int(match.group(2)))
    if match.group(3) is not None:
        alias = f"{alias}{int(match.group(3)):02d}"
    return alias


@dataclass(frozen=True)
class CandidateSlot:
    """Isolated filesystem allocation for one proposal."""

    suggested_candidate_id: str
    output_dir: Path
    memo_path: Path
    revision_of: str | None = None


@dataclass(frozen=True)
class ProposalFailure:
    """A producer-side failure retained in the audit instead of becoming opaque."""

    candidate_id: str
    reason: str


@dataclass(frozen=True)
class AuditRecord:
    phase: str
    disposition: str
    reason: str
    candidate_id: str | None = None


@dataclass(frozen=True)
class PipelineResult:
    digests: tuple[TaskDigest, ...]
    plan: PlanningArtifact | None
    considered_candidates: tuple[CandidateArtifact, ...]
    ranked_for_gate: tuple[CandidateArtifact, ...]
    critic_review: CriticReview
    audit: tuple[AuditRecord, ...]
    revision_count: int
    digester_artifact: DigesterRoundArtifact
    actionability: float
    actionability_threshold: float
    actionability_threshold_provenance: str
    short_circuit: str | None
    no_op: bool
    no_op_reasons: tuple[str, ...] = ()

    @property
    def requires_deterministic_gate(self) -> bool:
        """Always true; ``ranked_for_gate`` is not an approval list."""

        return True

    @property
    def threshold(self) -> float:
        """Short alias for call sites that report ``a_t`` against ``alpha``."""

        return self.actionability_threshold


class DigesterStage(Protocol):
    async def digest(
        self,
        *,
        context: PipelineContext,
    ) -> DigesterRoundArtifact | Sequence[TaskDigest]: ...


class PlannerStage(Protocol):
    async def plan(
        self,
        *,
        context: PipelineContext,
        digests: Sequence[TaskDigest],
    ) -> PlanningArtifact: ...


class EvolverStage(Protocol):
    async def propose(
        self,
        *,
        context: PipelineContext,
        plan: PlanningArtifact,
        limit: int,
    ) -> Sequence[object]: ...


class RevisingEvolverStage(EvolverStage, Protocol):
    async def revise(
        self,
        *,
        context: PipelineContext,
        plan: PlanningArtifact,
        candidate: CandidateArtifact,
        request: RevisionRequest,
    ) -> object: ...


class CandidateProducer(Protocol):
    async def __call__(
        self,
        *,
        context: PipelineContext,
        plan: PlanningArtifact,
        slot: CandidateSlot,
    ) -> CandidateArtifact | None: ...


@dataclass
class IsolatedEvolverAdapter:
    """Run an injected one-candidate producer in isolated output/memo slots.

    This is the adapter seam for ``MetaAgent.evolve``.  Each proposal receives
    a distinct directory and a private copy of the memo, so a proposal cannot
    mutate the context seen by the next proposal.  The producer must return a
    :class:`CandidateArtifact`; ``None`` and exceptions become auditable
    ``ProposalFailure`` records rather than opaque candidates.
    """

    producer: CandidateProducer
    revision_producer: CandidateProducer | None = None

    async def propose(
        self,
        *,
        context: PipelineContext,
        plan: PlanningArtifact,
        limit: int,
    ) -> Sequence[object]:
        slots: list[CandidateSlot] = []
        for index in range(1, limit + 1):
            candidate_id = f"C-R{context.round_idx}-{index:02d}"
            slots.append(self._allocate_slot(context, candidate_id))
        # ``asyncio.gather`` runs the isolated producers concurrently while
        # preserving this deterministic slot/Candidate-ID result order.  The
        # pipeline caps ``limit`` at K_t <= 4; Critic and gate ordering remain
        # outside this adapter and therefore sequential.
        return await asyncio.gather(
            *(
                self._produce(context, plan, slot, self.producer)
                for slot in slots
            )
        )

    async def revise(
        self,
        *,
        context: PipelineContext,
        plan: PlanningArtifact,
        candidate: CandidateArtifact,
        request: RevisionRequest,
    ) -> object:
        if self.revision_producer is None:
            return ProposalFailure(
                request.candidate_id,
                "Critic requested revision but this Evolver adapter has no revision producer",
            )
        slot = self._allocate_slot(
            context,
            f"{request.candidate_id}-revision-01",
            revision_of=request.candidate_id,
        )
        return await self._produce(context, plan, slot, self.revision_producer)

    @staticmethod
    async def _produce(
        context: PipelineContext,
        plan: PlanningArtifact,
        slot: CandidateSlot,
        producer: CandidateProducer,
    ) -> object:
        try:
            artifact = await producer(context=context, plan=plan, slot=slot)
        except Exception as exc:  # noqa: BLE001 - stage failure belongs in the audit
            return ProposalFailure(
                slot.suggested_candidate_id,
                f"producer raised {type(exc).__name__}: {exc}",
            )
        if artifact is None:
            return ProposalFailure(
                slot.suggested_candidate_id,
                "producer returned no CandidateArtifact (opaque/no-op proposal)",
            )
        if not isinstance(artifact, CandidateArtifact):
            return ProposalFailure(
                slot.suggested_candidate_id,
                f"producer returned opaque {type(artifact).__name__}, expected CandidateArtifact",
            )
        try:
            artifact.config_path.resolve().relative_to(slot.output_dir.resolve())
        except ValueError:
            return ProposalFailure(
                artifact.candidate_id,
                f"config_path {artifact.config_path} escapes isolated slot {slot.output_dir}",
            )
        return artifact

    @staticmethod
    def _allocate_slot(
        context: PipelineContext,
        slot_name: str,
        *,
        revision_of: str | None = None,
    ) -> CandidateSlot:
        output_dir = context.output_root / "candidates" / slot_name
        memo_path = context.output_root / "_candidate_memos" / f"{slot_name}.md"
        output_dir.mkdir(parents=True, exist_ok=True)
        memo_path.parent.mkdir(parents=True, exist_ok=True)
        memo_text = ""
        if context.memo_path is not None and context.memo_path.is_file():
            memo_text = context.memo_path.read_text(encoding="utf-8")
        memo_path.write_text(memo_text, encoding="utf-8")
        return CandidateSlot(
            suggested_candidate_id=slot_name,
            output_dir=output_dir,
            memo_path=memo_path,
            revision_of=revision_of,
        )


@dataclass
class CandidatePipeline:
    digester: DigesterStage
    planner: PlannerStage
    evolver: EvolverStage
    critic: CriticStage
    k_t: int = DEFAULT_K_T
    actionability_threshold: float = OURS_DEFAULT_ACTIONABILITY_THRESHOLD

    def __post_init__(self) -> None:
        if not 1 <= self.k_t <= MAX_K_T:
            raise ValueError(f"k_t must be in [1, {MAX_K_T}], got {self.k_t}")
        self.actionability_threshold = float(self.actionability_threshold)
        if (
            not math.isfinite(self.actionability_threshold)
            or not 0.0 <= self.actionability_threshold <= 1.0
        ):
            raise ValueError(
                "actionability_threshold must be a finite float in [0, 1], "
                f"got {self.actionability_threshold!r}"
            )

    async def run(self, context: PipelineContext) -> PipelineResult:
        audit: list[AuditRecord] = []
        digester_artifact, digester_audit = self._normalise_digester_output(
            await self.digester.digest(context=context)
        )
        audit.extend(digester_audit)
        digests = digester_artifact.digests
        actionability = digester_artifact.actionability

        if actionability < self.actionability_threshold:
            reason = (
                "selective invocation no-op: "
                f"actionability a_t={actionability:g} < "
                f"alpha={self.actionability_threshold:g}"
            )
            review = CriticReview(no_op=True, no_op_reasons=(reason,))
            audit.extend(self._review_audit(review, phase="selective_invocation"))
            return self._result(
                digester_artifact=digester_artifact,
                plan=None,
                candidates=(),
                review=review,
                audit=audit,
                revision_count=0,
                short_circuit="actionability_below_threshold",
            )

        audit.append(
            AuditRecord(
                phase="selective_invocation",
                disposition="continue",
                reason=(
                    f"actionability a_t={actionability:g} >= "
                    f"alpha={self.actionability_threshold:g}; "
                    "equality continues by Algorithm 1 boundary"
                ),
            )
        )
        plan = await self.planner.plan(context=context, digests=digests)
        if plan.target_variant != context.target_variant:
            raise ValueError(
                "Planner target mismatch: "
                f"{plan.target_variant!r} != {context.target_variant!r}"
            )
        if plan.empty_landscape and not plan.briefs:
            # [A2/EXP-E07] The Planner itself declared the mutation landscape
            # empty (an LLM Planner that returned zero briefs). Skip the Evolver
            # and Critic entirely and settle the round as a planner-driven no-op.
            # This is distinct from the byte-identical legacy path below (empty
            # briefs WITHOUT the flag — the deterministic Planner or a fallback),
            # which keeps ``short_circuit="empty_landscape"``.
            reason = "; ".join(note for note in plan.notes if note.strip()) or (
                "Planner reported an empty actionable mutation landscape (briefs=0)"
            )
            audit.append(
                AuditRecord(
                    phase="planner",
                    disposition="short_circuit",
                    reason=reason,
                )
            )
            review = CriticReview(no_op=True, no_op_reasons=(reason,))
            audit.extend(self._review_audit(review, phase="planner"))
            return self._result(
                digester_artifact=digester_artifact,
                plan=plan,
                candidates=(),
                review=review,
                audit=audit,
                revision_count=0,
                short_circuit="planner_empty_landscape",
            )
        if not plan.briefs:
            reason = (
                "selective invocation no-op: Planner produced an empty "
                "actionable landscape (briefs=0)"
            )
            review = CriticReview(no_op=True, no_op_reasons=(reason,))
            audit.extend(self._review_audit(review, phase="planner"))
            return self._result(
                digester_artifact=digester_artifact,
                plan=plan,
                candidates=(),
                review=review,
                audit=audit,
                revision_count=0,
                short_circuit="empty_landscape",
            )

        raw_candidates = await self.evolver.propose(
            context=context,
            plan=plan,
            limit=self.k_t,
        )
        candidates, normalise_audit = self._normalise_candidates(
            context,
            raw_candidates,
            phase="proposal",
            limit=self.k_t,
        )
        audit.extend(normalise_audit)

        if not candidates:
            review = CriticReview(
                no_op=True,
                no_op_reasons=("whole-round no-op: Evolver produced no valid structured candidates",),
            )
            audit.extend(self._review_audit(review, phase="evolver"))
            return self._result(
                digester_artifact=digester_artifact,
                plan=plan,
                candidates=(),
                review=review,
                audit=audit,
                revision_count=0,
                short_circuit="no_valid_candidates",
            )

        review = await self._review(context, digests, candidates)
        audit.extend(self._review_audit(review, phase="critic_initial"))
        revision_count = 0
        revision_excluded: set[str] = set()

        if review.revision_requests and not review.no_op:
            requests = sorted(
                review.revision_requests,
                key=lambda request: (request.candidate_id, request.reason),
            )
            selected = requests[0]
            audit.append(
                AuditRecord(
                    phase="revision",
                    disposition="revision_requested",
                    candidate_id=selected.candidate_id,
                    reason=selected.reason,
                )
            )
            for suppressed in requests[1:]:
                audit.append(
                    AuditRecord(
                        phase="revision",
                        disposition="revision_suppressed",
                        candidate_id=suppressed.candidate_id,
                        reason=(
                            f"{suppressed.reason}; only one revision cycle is allowed "
                            "(paper §4.3)"
                        ),
                    )
                )
            revision_excluded.add(selected.candidate_id)
            reviser = getattr(self.evolver, "revise", None)
            if reviser is None:
                audit.append(
                    AuditRecord(
                        phase="revision",
                        disposition="rejected",
                        candidate_id=selected.candidate_id,
                        reason="Critic requested revision but Evolver has no revise() adapter",
                    )
                )
            else:
                source = next(
                    (candidate for candidate in candidates if candidate.candidate_id == selected.candidate_id),
                    None,
                )
                if source is None:
                    audit.append(
                        AuditRecord(
                            phase="revision",
                            disposition="rejected",
                            candidate_id=selected.candidate_id,
                            reason="Critic requested revision of an unknown candidate",
                        )
                    )
                else:
                    revised_raw = await reviser(
                        context=context,
                        plan=plan,
                        candidate=source,
                        request=selected,
                    )
                    remaining = tuple(
                        candidate
                        for candidate in candidates
                        if candidate.candidate_id != selected.candidate_id
                    )
                    revised, revised_audit = self._normalise_candidates(
                        context,
                        [revised_raw],
                        phase="revision",
                        limit=1,
                        existing=remaining,
                    )
                    audit.extend(revised_audit)
                    if revised:
                        replacement = revised[0]
                        if replacement.manifest.iterates_from != selected.candidate_id:
                            audit.append(
                                AuditRecord(
                                    phase="revision",
                                    disposition="rejected",
                                    candidate_id=replacement.candidate_id,
                                    reason=(
                                        "revision manifest.iterates_from must equal "
                                        f"{selected.candidate_id!r}"
                                    ),
                                )
                            )
                        else:
                            candidates = tuple(
                                sorted((*remaining, replacement), key=lambda item: item.candidate_id)
                            )
                            revision_count = 1
                            review = await self._review(context, digests, candidates)
                            audit.extend(
                                self._review_audit(review, phase="critic_after_revision")
                            )
                            for suppressed in review.revision_requests:
                                audit.append(
                                    AuditRecord(
                                        phase="revision",
                                        disposition="revision_suppressed",
                                        candidate_id=suppressed.candidate_id,
                                        reason=(
                                            f"{suppressed.reason}; revision cycle already consumed"
                                        ),
                                    )
                                )
                            revision_excluded.update(
                                request.candidate_id for request in review.revision_requests
                            )

        ranked, ranking_audit = self._resolve_ranking(
            candidates,
            review,
            excluded=revision_excluded,
        )
        audit.extend(ranking_audit)
        return self._result(
            digester_artifact=digester_artifact,
            plan=plan,
            candidates=candidates,
            review=review,
            audit=audit,
            revision_count=revision_count,
            ranked=ranked,
        )

    @staticmethod
    def _normalise_digester_output(
        raw: DigesterRoundArtifact | Sequence[TaskDigest],
    ) -> tuple[DigesterRoundArtifact, list[AuditRecord]]:
        if isinstance(raw, DigesterRoundArtifact):
            return raw, [
                AuditRecord(
                    phase="digester",
                    disposition="artifact",
                    reason=(
                        f"structured round artifact: actionability={raw.actionability:g}; "
                        f"rationale={raw.rationale}"
                    ),
                )
            ]

        if isinstance(raw, (str, bytes)) or not isinstance(raw, Sequence):
            raise TypeError(
                "Digester must return DigesterRoundArtifact or Sequence[TaskDigest]"
            )
        artifact = DigesterRoundArtifact(
            digests=tuple(raw),
            actionability=LEGACY_ACTIONABILITY_ASSUMPTION,
            rationale=(
                "legacy/deviation compatibility: Digester returned only a "
                "Sequence[TaskDigest], so actionability was unavailable and "
                f"was assumed to be {LEGACY_ACTIONABILITY_ASSUMPTION:g}"
            ),
            contract_mode="legacy_sequence_deviation",
        )
        return artifact, [
            AuditRecord(
                phase="digester",
                disposition="deviation",
                reason=artifact.rationale,
            )
        ]

    async def _review(
        self,
        context: PipelineContext,
        digests: tuple[TaskDigest, ...],
        candidates: tuple[CandidateArtifact, ...],
    ) -> CriticReview:
        return await self.critic.review(
            context=CriticContext(
                round_idx=context.round_idx,
                target_variant=context.target_variant,
                digests=digests,
                regressions=context.regressions,
                failure_buckets=context.failure_buckets,
            ),
            candidates=candidates,
        )

    def _normalise_candidates(
        self,
        context: PipelineContext,
        raw_candidates: Sequence[object],
        *,
        phase: str,
        limit: int,
        existing: Sequence[CandidateArtifact] = (),
    ) -> tuple[tuple[CandidateArtifact, ...], list[AuditRecord]]:
        audit: list[AuditRecord] = []
        artifacts: list[CandidateArtifact] = []
        for raw in raw_candidates:
            if isinstance(raw, ProposalFailure):
                audit.append(
                    AuditRecord(
                        phase=phase,
                        disposition="rejected",
                        candidate_id=raw.candidate_id,
                        reason=raw.reason,
                    )
                )
            elif isinstance(raw, CandidateArtifact):
                artifacts.append(raw)
            else:
                audit.append(
                    AuditRecord(
                        phase=phase,
                        disposition="rejected",
                        reason=(
                            f"opaque candidate type {type(raw).__name__}; "
                            "expected CandidateArtifact"
                        ),
                    )
                )

        artifacts.sort(key=lambda item: (item.candidate_id, str(item.config_path)))
        seen_ids = {candidate.candidate_id for candidate in existing}
        seen_paths = {candidate.config_path.resolve() for candidate in existing}
        seen_dirs = {candidate.config_path.resolve().parent for candidate in existing}
        seen_fingerprints = {self._fingerprint(candidate) for candidate in existing}
        accepted: list[CandidateArtifact] = []
        root = context.output_root.resolve()

        for candidate in artifacts:
            problems = candidate.validation_errors(
                expected_target=context.target_variant,
                expected_round=context.round_idx,
            )
            resolved = candidate.config_path.resolve()
            try:
                resolved.relative_to(root)
            except ValueError:
                problems.append(
                    f"config_path: {candidate.config_path} is outside output_root {context.output_root}"
                )

            if candidate.candidate_id in seen_ids:
                problems.append(f"candidate_id: duplicate {candidate.candidate_id!r}")
            if resolved in seen_paths:
                problems.append(f"config_path: duplicate {candidate.config_path}")
            if resolved.parent in seen_dirs:
                problems.append(
                    f"config_path: output directory is shared with another candidate: {resolved.parent}"
                )

            fingerprint = self._fingerprint(candidate) if candidate.config_path.is_file() else None
            if fingerprint is not None and fingerprint in seen_fingerprints:
                problems.append("candidate: duplicate config+manifest content")

            if problems:
                for problem in problems:
                    audit.append(
                        AuditRecord(
                            phase=phase,
                            disposition="rejected",
                            candidate_id=candidate.candidate_id or None,
                            reason=problem,
                        )
                    )
                continue

            accepted.append(candidate)
            seen_ids.add(candidate.candidate_id)
            seen_paths.add(resolved)
            seen_dirs.add(resolved.parent)
            if fingerprint is not None:
                seen_fingerprints.add(fingerprint)

        if len(accepted) > limit:
            for candidate in accepted[limit:]:
                audit.append(
                    AuditRecord(
                        phase=phase,
                        disposition="rejected",
                        candidate_id=candidate.candidate_id,
                        reason=f"K_t={limit} limit exceeded after deterministic sorting",
                    )
                )
            accepted = accepted[:limit]
        return tuple(accepted), audit

    @staticmethod
    def _fingerprint(candidate: CandidateArtifact) -> str:
        manifest = candidate.manifest.model_dump(mode="json")
        manifest["candidate_id"] = ""
        manifest["iterates_from"] = None
        payload = json.dumps(manifest, sort_keys=True, ensure_ascii=False).encode("utf-8")
        config = candidate.config_path.read_bytes() if candidate.config_path.is_file() else b""
        return hashlib.sha256(payload + b"\0" + config).hexdigest()

    @staticmethod
    def _review_audit(review: CriticReview, *, phase: str) -> list[AuditRecord]:
        audit = [
            AuditRecord(
                phase=phase,
                disposition="rejected",
                candidate_id=rejection.candidate_id,
                reason=rejection.reason,
            )
            for rejection in review.rejections
        ]
        audit.extend(
            AuditRecord(
                phase=phase,
                disposition="no_op",
                reason=reason,
            )
            for reason in review.no_op_reasons
        )
        audit.extend(
            AuditRecord(
                phase=phase,
                disposition="strategy_concern",
                reason=concern,
            )
            for concern in review.strategy_concerns
        )
        audit.extend(
            AuditRecord(
                phase=phase,
                disposition="observation",
                reason=f"unexplored failure cluster: {cluster}",
            )
            for cluster in review.unexplored_failure_clusters
        )
        audit.extend(
            AuditRecord(
                phase=phase,
                disposition="observation",
                reason=f"unexplored failure bucket: {bucket}",
            )
            for bucket in review.unexplored_failure_buckets
        )
        return audit

    @staticmethod
    def _resolve_ranking(
        candidates: Sequence[CandidateArtifact],
        review: CriticReview,
        *,
        excluded: set[str],
    ) -> tuple[tuple[CandidateArtifact, ...], list[AuditRecord]]:
        if review.no_op:
            return (), []
        by_id = {candidate.candidate_id: candidate for candidate in candidates}
        rejected = {rejection.candidate_id for rejection in review.rejections} | excluded
        ranked: list[CandidateArtifact] = []
        audit: list[AuditRecord] = []
        seen: set[str] = set()
        for candidate_id in review.ranked_candidate_ids:
            if candidate_id in seen:
                audit.append(
                    AuditRecord(
                        phase="critic_ranking",
                        disposition="rejected",
                        candidate_id=candidate_id,
                        reason="Critic ranking contains duplicate candidate id",
                    )
                )
                continue
            seen.add(candidate_id)
            candidate = by_id.get(candidate_id)
            if candidate is None:
                audit.append(
                    AuditRecord(
                        phase="critic_ranking",
                        disposition="rejected",
                        candidate_id=candidate_id,
                        reason="Critic ranked an unknown candidate id",
                    )
                )
                continue
            if candidate_id in rejected:
                continue
            ranked.append(candidate)

        ranked_ids = {candidate.candidate_id for candidate in ranked}
        for candidate in candidates:
            if candidate.candidate_id in ranked_ids or candidate.candidate_id in rejected:
                continue
            audit.append(
                AuditRecord(
                    phase="critic_ranking",
                    disposition="rejected",
                    candidate_id=candidate.candidate_id,
                    reason="Critic did not include candidate in ship_ranking",
                )
            )
        return tuple(ranked), audit

    def _result(
        self,
        *,
        digester_artifact: DigesterRoundArtifact,
        plan: PlanningArtifact | None,
        candidates: Sequence[CandidateArtifact],
        review: CriticReview,
        audit: Sequence[AuditRecord],
        revision_count: int,
        ranked: Sequence[CandidateArtifact] = (),
        short_circuit: str | None = None,
    ) -> PipelineResult:
        no_op = review.no_op or not ranked
        reasons = review.no_op_reasons
        if no_op and not reasons:
            reasons = ("whole-round no-op: Critic produced no gate ranking",)
        return PipelineResult(
            digests=digester_artifact.digests,
            plan=plan,
            considered_candidates=tuple(candidates),
            ranked_for_gate=tuple(ranked),
            critic_review=review,
            audit=tuple(audit),
            revision_count=revision_count,
            digester_artifact=digester_artifact,
            actionability=digester_artifact.actionability,
            actionability_threshold=self.actionability_threshold,
            actionability_threshold_provenance=(
                OURS_ACTIONABILITY_THRESHOLD_PROVENANCE
            ),
            short_circuit=short_circuit,
            no_op=no_op,
            no_op_reasons=tuple(reasons),
        )
