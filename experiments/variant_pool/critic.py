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


def _graph_surface(candidate: CandidateArtifact) -> frozenset[str] | None:
    """The touched node/edge identifiers of a candidate's graph edits, or ``None``.

    v6 M8. ``None`` means the candidate carries no graph-edit information, so the
    path-string surface (file-change paths) applies unchanged — today's default.
    Otherwise the surface is the union, over every edit, of the node ids the edit
    touches (``edit.affected_node_ids()`` — whose ids come from the M2a node-id
    authority; none are minted here) plus a ``src→tgt`` identifier for each
    dependency edit. Duck-typed on the edit so this module keeps its dependency-free
    import surface and never imports the graph package at load time.

    Because the surface is exact graph elements rather than shared file paths, two
    candidates editing *different* nodes of the *same* file no longer read as
    overlapping, while two candidates touching the *same* node still do.
    """
    edits = getattr(candidate, "graph_edits", None)
    if not edits:
        return None
    surface: set[str] = set()
    for edit in edits:
        surface |= set(edit.affected_node_ids())
        source = getattr(edit, "edge_source_id", "")
        target = getattr(edit, "edge_target_id", "")
        if source and target:
            surface.add(f"{source}→{target}")
    return frozenset(surface)


@dataclass(frozen=True)
class CriticContext:
    """Inputs needed by the portfolio audit for one target variant."""

    round_idx: int
    target_variant: str
    digests: tuple[TaskDigest, ...] = ()
    regressions: tuple[str, ...] = ()
    failure_buckets: tuple[str, ...] = ()
    #: F-B (--regression-accountability). The subset of ``regressions`` a shipped
    #: APPLY/FORK config change actually caused. ``None`` (default) = strict
    #: accountability: EVERY regression may trigger the whole-round no-op veto
    #: (byte-identical legacy behaviour). A tuple = shipped_only accountability:
    #: only these hard-gate; the rest are demoted to ``strategy_concerns``.
    shipped_regressions: tuple[str, ...] | None = None


def regressions_for_gate(
    context: "CriticContext",
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Split ``context.regressions`` into (hard-gating, demoted-to-concerns).

    F-B. Strict accountability (``context.shipped_regressions is None``): every
    regression may hard-gate and nothing is demoted, so the whole-round veto is
    byte-identical to the legacy behaviour. shipped_only accountability
    (``shipped_regressions`` is a tuple): only regressions a shipped APPLY/FORK
    change caused may hard-gate; the rest are demoted to visible, non-blocking
    ``strategy_concerns``. Order within ``context.regressions`` is preserved.
    """
    if context.shipped_regressions is None:
        return tuple(context.regressions), ()
    shipped = set(context.shipped_regressions)
    gating = tuple(task_id for task_id in context.regressions if task_id in shipped)
    demoted = tuple(task_id for task_id in context.regressions if task_id not in shipped)
    return gating, demoted


def demoted_regression_concern(task_id: str) -> str:
    """The ``strategy_concern`` recorded for a regression that was NOT hard-gated (F-B)."""
    return (
        f"regression {task_id} not hard-gated (regression-accountability=shipped_only): "
        "no shipped APPLY/FORK config change caused it (rejected-candidate gate "
        "regression or zero-ship inter-round variance); surfaced for visibility, "
        "not blocking the round"
    )


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
    #: v6 M8 — which surface produced ``mutation_surface`` / ``overlapping_candidates``:
    #: ``"graph"`` (exact touched node/edge ids) or ``"path"`` (file-change paths).
    #: Recorded, never inferred: a verdict must not claim the precise graph surface
    #: while it actually ran the imprecise path one (the 4e0810f failure mode).
    surface_kind: str = "path"
    #: v6 M8 — why that surface was used, in the shape of M6b/M7's executed-path
    #: provenance notes.
    surface_provenance: str = "path surface: file-change paths; no graph-edit information on this candidate"


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

        # F-B: only shipped-caused regressions hard-gate under shipped_only
        # accountability; the rest are demoted to concerns. Strict accountability
        # (shipped_regressions is None) puts every regression in ``gate_regressions``
        # and leaves ``demoted_regressions`` empty, so this is byte-identical.
        gate_regressions, demoted_regressions = regressions_for_gate(context)
        for task_id in self._unresolved_regressions(demoted_regressions, eligible):
            concerns.add(demoted_regression_concern(task_id))
        unresolved_regressions = self._unresolved_regressions(
            gate_regressions,
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
        # M8: a candidate carrying graph edits gets an exact node/edge surface;
        # otherwise the path-string surface runs unchanged (the default). Which
        # one executed is recorded on the verdict, not left to be inferred.
        surfaces: dict[str, frozenset[str]] = {}
        surface_kinds: dict[str, str] = {}
        surface_notes: dict[str, str] = {}
        for candidate in ranked:
            graph_surface = _graph_surface(candidate)
            if graph_surface is not None:
                surfaces[candidate.candidate_id] = graph_surface
                surface_kinds[candidate.candidate_id] = "graph"
                surface_notes[candidate.candidate_id] = (
                    f"graph surface: {len(graph_surface)} touched node/edge id(s) "
                    f"from {len(candidate.graph_edits or ())} graph edit(s)"
                )
            else:
                surfaces[candidate.candidate_id] = frozenset(
                    str(change.get("path", ""))
                    for change in candidate.manifest.file_changes
                    if str(change.get("path", "")).strip()
                )
                surface_kinds[candidate.candidate_id] = "path"
                surface_notes[candidate.candidate_id] = (
                    "path surface: file-change paths; no graph-edit information on this candidate"
                )
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
                    surface_kind=surface_kinds[candidate.candidate_id],
                    surface_provenance=surface_notes[candidate.candidate_id],
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
        # M8: with a graph surface, rank by the touched-element count; otherwise the
        # file-change count, exactly as before. Ordering semantics are unchanged —
        # only the source of ``surface_size`` follows whichever surface applies.
        graph_surface = _graph_surface(candidate)
        surface_size = len(graph_surface) if graph_surface is not None else len(candidate.manifest.file_changes)
        return (-failure_coverage, -regression_coverage, surface_size, candidate.candidate_id)
