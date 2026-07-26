# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""Critic contracts and a deterministic portfolio-audit fallback.

The Critic never ships a candidate.  It may only rank structured candidates,
request a revision, or stop the round.  Every ranked candidate must still pass
``gate.run_gate``; this module intentionally has no dependency on ``gate`` and
no ``approve``/``apply`` API.

The paper publishes the Critic prompt but not an executable implementation.
``CriticStage`` therefore admits an injected model-backed adapter, while
``DeterministicCritic`` implements the three portfolio rules that can be
reconstructed without an LLM (SPEC §6.5):

* ban a lever shipped in at least two of the previous three rounds when its
  cumulative hit-rate is below 0.4;
* identify failure clusters/buckets the current portfolio never touches;
* stop the whole round when known regressions are neither handled nor
  explicitly explained.

The fallback's numerical ranking is an engineering choice, not a claim that a
single deterministic scorer reproduces the paper's four-role AEGIS dialogue.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, Sequence

from .evidence import EvidenceStore, TaskDigest
from .manifest import CandidateArtifact


@dataclass(frozen=True)
class CriticContext:
    """Inputs needed by the portfolio audit for one target variant."""

    round_idx: int
    target_variant: str
    digests: tuple[TaskDigest, ...] = ()
    regressions: tuple[str, ...] = ()
    failure_buckets: tuple[str, ...] = ()


@dataclass(frozen=True)
class CandidateVerdict:
    """An auditable rank explanation, not a shipping decision."""

    candidate_id: str
    rank: int | None
    mutation_surface: tuple[str, ...]
    overlapping_candidates: tuple[str, ...] = ()
    reasons: tuple[str, ...] = ()
    # The paper asks whether overlap with the parent was intentional.  The
    # deterministic fallback only sees manifests, not semantic parent-config
    # intent; a model-backed Critic should replace this explicit deviation.
    parent_overlap_assessment: str = (
        "not assessed by deterministic fallback; inject a semantic Critic for parent-intent review"
    )


@dataclass(frozen=True)
class CriticRejection:
    candidate_id: str
    reason: str


@dataclass(frozen=True)
class RevisionRequest:
    candidate_id: str
    reason: str


@dataclass(frozen=True)
class CriticReview:
    """The complete Critic result.

    There is deliberately no ``approved`` field.  ``ranked_candidate_ids`` is
    merely the order in which the deterministic gate should inspect candidates.
    """

    ranked_candidate_ids: tuple[str, ...] = ()
    verdicts: tuple[CandidateVerdict, ...] = ()
    rejections: tuple[CriticRejection, ...] = ()
    revision_requests: tuple[RevisionRequest, ...] = ()
    no_op: bool = False
    no_op_reasons: tuple[str, ...] = ()
    strategy_concerns: tuple[str, ...] = ()
    unexplored_failure_clusters: tuple[str, ...] = ()
    unexplored_failure_buckets: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.no_op and self.ranked_candidate_ids:
            raise ValueError("a no-op Critic review cannot also rank candidates")

    @property
    def requires_deterministic_gate(self) -> bool:
        """Always true: Critic ranking never authorizes shipping."""

        return True


class CriticStage(Protocol):
    """Injectable Critic interface (LLM-backed or deterministic)."""

    async def review(
        self,
        *,
        context: CriticContext,
        candidates: Sequence[CandidateArtifact],
    ) -> CriticReview: ...


@dataclass
class DeterministicCritic:
    """Offline implementation of the reconstructable portfolio rules."""

    evidence_store: EvidenceStore
    ranking_note: str = field(
        default=(
            "engineering fallback ranks failure-task coverage first, regression coverage second, "
            "then smaller mutation surfaces and candidate id"
        )
    )

    async def review(
        self,
        *,
        context: CriticContext,
        candidates: Sequence[CandidateArtifact],
    ) -> CriticReview:
        ordered = sorted(candidates, key=lambda candidate: candidate.candidate_id)
        rejections: list[CriticRejection] = []
        concerns: set[str] = set()
        eligible: list[CandidateArtifact] = []

        for candidate in ordered:
            banned = [
                lever
                for lever in sorted(set(candidate.manifest.bucket))
                if self.evidence_store.lever_is_banned(lever, context.round_idx)
            ]
            if banned:
                for lever in banned:
                    rate = self.evidence_store.hit_rate(lever)
                    rate_text = "undefined" if rate is None else f"{rate:.3f}"
                    concerns.add(
                        f"lever {lever!r} is banned: >=2 ships in the previous 3 rounds "
                        f"and cumulative hit_rate={rate_text}<0.4"
                    )
                rejections.append(
                    CriticRejection(
                        candidate.candidate_id,
                        f"candidate uses banned lever(s): {', '.join(banned)}",
                    )
                )
                continue
            eligible.append(candidate)

        cluster_tasks = self._cluster_tasks(context.digests)
        mentioned_tasks = {
            task_id
            for candidate in eligible
            for task_id in (
                *candidate.manifest.predicted_impact.predicted_flips(),
                *candidate.manifest.predicted_impact.tasks_at_risk,
            )
        }
        unexplored_clusters = tuple(
            cluster
            for cluster, task_ids in sorted(cluster_tasks.items())
            if task_ids.isdisjoint(mentioned_tasks)
        )

        used_buckets = {
            bucket for candidate in eligible for bucket in candidate.manifest.bucket
        }
        unexplored_buckets = tuple(sorted(set(context.failure_buckets) - used_buckets))

        unresolved_regressions = self._unresolved_regressions(
            context.regressions,
            eligible,
        )
        if unresolved_regressions:
            reason = (
                "whole-round no-op: regressions were neither handled in tasks_at_risk "
                f"nor explained: {', '.join(unresolved_regressions)}"
            )
            return CriticReview(
                rejections=tuple(rejections),
                no_op=True,
                no_op_reasons=(reason,),
                strategy_concerns=tuple(sorted(concerns)),
                unexplored_failure_clusters=unexplored_clusters,
                unexplored_failure_buckets=unexplored_buckets,
            )

        if not eligible:
            return CriticReview(
                rejections=tuple(rejections),
                no_op=True,
                no_op_reasons=("whole-round no-op: no candidates remain after portfolio audit",),
                strategy_concerns=tuple(sorted(concerns)),
                unexplored_failure_clusters=unexplored_clusters,
                unexplored_failure_buckets=unexplored_buckets,
            )

        failing_tasks = {
            digest.task_id
            for digest in context.digests
            if not digest.solved
        }
        regressions = set(context.regressions)
        ranked = sorted(
            eligible,
            key=lambda candidate: self._ranking_key(candidate, failing_tasks, regressions),
        )
        surfaces = {
            candidate.candidate_id: frozenset(
                str(change.get("path", ""))
                for change in candidate.manifest.file_changes
                if str(change.get("path", "")).strip()
            )
            for candidate in ranked
        }
        verdicts: list[CandidateVerdict] = []
        for rank, candidate in enumerate(ranked, start=1):
            overlaps = tuple(
                other_id
                for other_id, other_surface in sorted(surfaces.items())
                if other_id != candidate.candidate_id
                and not surfaces[candidate.candidate_id].isdisjoint(other_surface)
            )
            verdicts.append(
                CandidateVerdict(
                    candidate_id=candidate.candidate_id,
                    rank=rank,
                    mutation_surface=tuple(sorted(surfaces[candidate.candidate_id])),
                    overlapping_candidates=overlaps,
                    reasons=(self.ranking_note,),
                )
            )

        return CriticReview(
            ranked_candidate_ids=tuple(candidate.candidate_id for candidate in ranked),
            verdicts=tuple(verdicts),
            rejections=tuple(rejections),
            strategy_concerns=tuple(sorted(concerns)),
            unexplored_failure_clusters=unexplored_clusters,
            unexplored_failure_buckets=unexplored_buckets,
        )

    @staticmethod
    def _cluster_tasks(digests: Sequence[TaskDigest]) -> dict[str, set[str]]:
        clusters: dict[str, set[str]] = {}
        for digest in digests:
            if digest.solved or not digest.failure_category:
                continue
            clusters.setdefault(digest.failure_category, set()).add(digest.task_id)
        return clusters

    @staticmethod
    def _unresolved_regressions(
        regressions: Sequence[str],
        candidates: Sequence[CandidateArtifact],
    ) -> tuple[str, ...]:
        handled = {
            task_id
            for candidate in candidates
            for task_id in candidate.manifest.predicted_impact.tasks_at_risk
        }
        explained = {
            task_id
            for candidate in candidates
            for task_id in candidate.explained_regressions
        }
        return tuple(sorted(set(regressions) - handled - explained))

    @staticmethod
    def _ranking_key(
        candidate: CandidateArtifact,
        failing_tasks: set[str],
        regressions: set[str],
    ) -> tuple[int, int, int, str]:
        impact = candidate.manifest.predicted_impact
        failure_coverage = len(set(impact.predicted_flips()) & failing_tasks)
        regression_coverage = len(set(impact.tasks_at_risk) & regressions)
        surface_size = len(candidate.manifest.file_changes)
        return (-failure_coverage, -regression_coverage, surface_size, candidate.candidate_id)
