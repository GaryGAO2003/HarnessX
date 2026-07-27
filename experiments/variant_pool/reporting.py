# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""W29 — the evaluation output contract: never report the peak alone.

SPEC §6.7 (Codex critique 9) exists because the paper convicts itself twice.

§7.7, verbatim on its own limitations: the reported figure is a *peak* over
rounds selected on the same task set that was evolved on, with **no held-out
split** — "selection bias" and "overfitting" in its own words. §7.1 adds the
second: pass@2 "can mask a decline in per-attempt success probability", which is
the mechanism behind the Global arm's late collapse. A report that prints one
number is therefore not a weaker report, it is a *misleading* one.

So :class:`RunReport` refuses to serve the peak by itself. Every rendering puts
``final`` and ``peak`` side by side, the per-round curve underneath, and the
pass@1 / per-attempt rates that show whether pass@2 is hiding a drift.

What the paper specifies, and what is ours
------------------------------------------
Specified: the pass@k estimator (A.3 formula 6, the standard unbiased
``1 - C(n-c, k) / C(n, k)`` averaged over tasks — copied, not chosen) and the
rule that infrastructure failures "count as failures" and are not dropped
(A.3). :class:`TaskResult` can mark them, and marking one changes nothing about
the arithmetic; the mark exists so the run can be *audited*, not so the failures
can be excused.

Ours: the whole variant-pool block. §6.6 of the paper reports no per-variant
process data at all — no variant count over time, no routing hit rate, no
per-variant coverage, no fork/retire axis — so :meth:`RunReport.variant_count_curve`
and its neighbours are the instrumentation we add (SPEC §6.7 last bullet).

Level stratification
--------------------
GAIA levels are 39/52/12 in the paper's fixed set (A.2 p.28), and level 3 is
twelve tasks: a single level-3 flip moves that stratum by 8.3 points. Reporting
by level is what keeps a headline delta from being read as uniform progress.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from math import comb, sqrt
from typing import Any

#: Pool events the report knows how to lay on a time axis (SPEC §6.7).
POOL_EVENT_KINDS = ("fork", "retire")

#: Variant label used when a run records no variant at all (M0: one harness,
#: no pool). Keeps the per-variant views defined for baseline runs.
IMPLICIT_VARIANT = "H0"


def _validate_attempt_counts(
    *,
    label: str,
    n_att: int,
    n_pass: int,
    infra_failures: int,
    budget_exhaustions: int,
) -> None:
    """Validate mutually exclusive attempt outcomes.

    Infrastructure errors and runtime budget exhaustion are different failure
    modes.  Both remain inside ``n_att`` (and therefore score as failures), but
    an attempt cannot be counted in both buckets or also be a pass.
    """
    if min(n_att, n_pass, infra_failures, budget_exhaustions) < 0:
        raise ValueError(f"{label}: negative counts")
    if n_pass > n_att:
        raise ValueError(f"{label}: n_pass={n_pass} exceeds n_att={n_att}")
    classified_failures = infra_failures + budget_exhaustions
    if classified_failures > n_att - n_pass:
        raise ValueError(
            f"{label}: {classified_failures} classified failures cannot coexist with "
            f"{n_pass} passes in {n_att} attempts — a failed attempt is still an attempt"
        )


@dataclass
class TaskResult:
    """One task's rollouts in one round: ``n_att`` attempts, ``n_pass`` passes.

    Under pass@2 this is ``n_att == 2`` and ``n_pass in {0, 1, 2}`` (§6.1 p.15),
    but ``n_att`` is kept free because the pass@k estimator is only unbiased
    when ``n`` may exceed ``k`` (A.3 formula 6).

    ``infra_failures`` counts attempts that died on infrastructure — a timeout,
    a provider error, a sandbox that would not start. A.3 verbatim: they "count
    as failures" and are not excluded, so they are *already inside* ``n_att``
    and are recorded only to make the rate auditable. The constructor enforces
    the consequence: an attempt cannot both pass and be an infrastructure
    failure, so ``infra_failures <= n_att - n_pass``.
    """

    task_id: str
    round_idx: int
    n_att: int
    n_pass: int
    variant_id: str | None = None
    infra_failures: int = 0
    budget_exhaustions: int = 0

    def __post_init__(self) -> None:
        _validate_attempt_counts(
            label=self.task_id,
            n_att=self.n_att,
            n_pass=self.n_pass,
            infra_failures=self.infra_failures,
            budget_exhaustions=self.budget_exhaustions,
        )

    @property
    def solved(self) -> bool:
        """pass@k semantics: one passing rollout is enough."""
        return self.n_pass >= 1

    @property
    def variant(self) -> str:
        """Variant label, or :data:`IMPLICIT_VARIANT` for an unrouted run."""
        return self.variant_id if self.variant_id is not None else IMPLICIT_VARIANT

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "round_idx": self.round_idx,
            "n_att": self.n_att,
            "n_pass": self.n_pass,
            "variant_id": self.variant_id,
            "infra_failures": self.infra_failures,
            "budget_exhaustions": self.budget_exhaustions,
        }


@dataclass
class CandidateTaskResult:
    """One candidate-gate measurement, never a deployed-pool score.

    ``target_variant_id`` identifies the variant the edit was proposed for. It
    is deliberately not called ``variant_id``: after a FORK the measured edit
    is deployed by the child, after a REJECT it is deployed by nobody, and
    conflating either case with the active carrier corrupts routing and final
    score diagnostics.
    """

    task_id: str
    round_idx: int
    candidate_id: str
    target_variant_id: str
    n_att: int
    n_pass: int
    decision: str | None = None
    infra_failures: int = 0
    budget_exhaustions: int = 0
    failed_stage: str | None = None
    archive_reason: str = ""
    skipped_reason: str | None = None
    evaluated: bool = True

    def __post_init__(self) -> None:
        _validate_attempt_counts(
            label=f"{self.candidate_id}:{self.task_id}",
            n_att=self.n_att,
            n_pass=self.n_pass,
            infra_failures=self.infra_failures,
            budget_exhaustions=self.budget_exhaustions,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "round_idx": self.round_idx,
            "candidate_id": self.candidate_id,
            "target_variant_id": self.target_variant_id,
            "n_att": self.n_att,
            "n_pass": self.n_pass,
            "decision": self.decision,
            "infra_failures": self.infra_failures,
            "budget_exhaustions": self.budget_exhaustions,
            "failed_stage": self.failed_stage,
            "archive_reason": self.archive_reason,
            "skipped_reason": self.skipped_reason,
            "evaluated": self.evaluated,
        }


def pass_at_k(n: int, c: int, k: int) -> float:
    """A.3 formula 6 — the unbiased estimator for one task.

    ``1 - C(n - c, k) / C(n, k)``: the probability that a random draw of *k* of
    the *n* attempts contains at least one of the *c* that passed. This is not
    the same as "did any attempt pass": with ``n = 4, c = 1, k = 2`` the naive
    reading says 1.0, the estimator says 0.5, and the estimator is the one that
    survives ``n > k``.

    ``n < k`` raises rather than guessing. Under pass@2 with two rollouts it
    cannot happen; if it does, the task was under-sampled and any convention
    (clamping to n, or scoring it as a plain "any pass") would bias the report
    in a direction nobody chose.
    """
    if k < 1:
        raise ValueError(f"k must be >= 1, got {k}")
    if n < k:
        raise ValueError(f"pass@{k} needs at least {k} attempts, got n={n}")
    if c < 0 or c > n:
        raise ValueError(f"passes out of range: c={c}, n={n}")
    if n - c < k:
        return 1.0
    return 1.0 - comb(n - c, k) / comb(n, k)


@dataclass
class RunReport:
    """Everything one run must publish (SPEC §6.7).

    Built incrementally with :meth:`add`, or handed a list of
    :class:`TaskResult` up front. ``events`` carries the pool's fork/retire
    axis as plain dicts with at least ``round_idx``, ``kind`` and
    ``variant_id``; ``lock_sha256`` ties the numbers to the
    :class:`.experiment_lock.ExperimentLock` that produced them, so a report
    can never be quoted without its configuration.
    """

    #: Settled, deployed active-pool measurements. All headline/process metrics
    #: consume this stream and no other.
    results: list[TaskResult] = field(default_factory=list)
    #: Pre-settlement candidate gate measurements, diagnostics only.
    candidate_results: list[CandidateTaskResult] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)
    #: Per-round measurement contract (candidate count, fixed denominator,
    #: completeness). Kept separate from results so no-candidate rounds remain
    #: explicit even though they still have a full active-pool score.
    round_metadata: dict[int, dict[str, Any]] = field(default_factory=dict)
    run_name: str = ""
    lock_sha256: str | None = None
    #: Main metric of the run. pass@2 is the paper's (§6.1 p.15).
    k: int = 2

    # ------------------------------------------------------------------
    # building
    # ------------------------------------------------------------------

    def add(self, result: TaskResult) -> None:
        self.results.append(result)

    def add_candidate(self, result: CandidateTaskResult) -> None:
        """Add a gate measurement without exposing it to headline metrics."""
        self.candidate_results.append(result)

    def record_round(
        self,
        *,
        round_idx: int,
        candidate_count: int,
        evaluated_task_denominator: int,
        active_variant_count: int,
    ) -> None:
        """Record the fixed active-pool measurement contract for one round."""
        if min(round_idx, candidate_count, evaluated_task_denominator, active_variant_count) < 0:
            raise ValueError("round metadata counts must be non-negative")
        rows = self.results_in(round_idx)
        task_ids = {row.task_id for row in rows}
        self.round_metadata[round_idx] = {
            "round": round_idx,
            "candidate_count": candidate_count,
            "no_candidate": candidate_count == 0,
            "active_variant_count": active_variant_count,
            "evaluated_tasks": len(task_ids),
            "evaluated_task_denominator": evaluated_task_denominator,
            "complete": len(rows) == len(task_ids) == evaluated_task_denominator,
        }

    def add_event(self, *, round_idx: int, kind: str, variant_id: str, **extra: Any) -> None:
        """Record a fork or a retirement on the pool's time axis."""
        if kind not in POOL_EVENT_KINDS:
            raise ValueError(f"kind must be one of {POOL_EVENT_KINDS}, got {kind!r}")
        self.events.append({"round_idx": round_idx, "kind": kind, "variant_id": variant_id, **extra})

    def rounds(self) -> list[int]:
        """Rounds with at least one result, ascending."""
        return sorted({result.round_idx for result in self.results})

    def results_in(self, round_idx: int) -> list[TaskResult]:
        return [result for result in self.results if result.round_idx == round_idx]

    # ------------------------------------------------------------------
    # headline metrics
    # ------------------------------------------------------------------

    def pass_at_k(self, k: int | None = None, *, round_idx: int | None = None) -> float:
        """Mean of the per-task unbiased estimator (A.3 formula 6).

        ``round_idx`` defaults to the last round, so the bare call is the run's
        final score rather than a pooled average over the whole trajectory —
        pooling rounds would mix a harness with its own earlier versions.
        """
        return self._mean_estimate(self._scope(round_idx), k if k is not None else self.k)

    def pass_at_1(self, *, round_idx: int | None = None) -> float:
        """pass@1 — the per-task success *probability*, macro-averaged.

        This is the number §7.1 warns pass@2 can mask: a harness whose tasks
        drift from 2/2 to 1/2 holds pass@2 at 1.0 while pass@1 falls from 1.0 to
        0.5. Report both or report neither.
        """
        return self._mean_estimate(self._scope(round_idx), 1)

    def per_attempt_rate(self, *, round_idx: int | None = None) -> float:
        """Pooled passes over attempts — the micro-average companion to pass@1.

        Differs from :meth:`pass_at_1` only when tasks carry different attempt
        counts, where pass@1 weighs every task equally and this weighs every
        rollout equally. Both are reported because a divergence between them is
        itself a finding (uneven sampling across tasks).
        """
        scope = self._scope(round_idx)
        attempts = sum(result.n_att for result in scope)
        if attempts == 0:
            return 0.0
        return sum(result.n_pass for result in scope) / attempts

    def curve(self, k: int | None = None) -> list[float]:
        """pass@k per round, in round order (SPEC §6.7)."""
        return [self.pass_at_k(k, round_idx=r) for r in self.rounds()]

    def final(self, k: int | None = None) -> float:
        """The last round's score — the honest end state."""
        return self.pass_at_k(k)

    def peak(self, k: int | None = None) -> tuple[int, float]:
        """``(round_idx, score)`` of the best round, earliest best on a tie.

        Never to be published alone: §7.7 concedes the peak is selected on the
        same tasks it was evolved on, with no held-out split. :meth:`to_markdown`
        enforces the pairing.
        """
        rounds = self.rounds()
        if not rounds:
            raise ValueError("cannot take a peak of an empty report")
        scores = self.curve(k)
        best = max(range(len(scores)), key=lambda i: scores[i])
        return rounds[best], scores[best]

    def drift(self, k: int | None = None) -> float:
        """``final - peak``: how much of the peak did not survive to the end.

        Zero or negative by construction (the peak is a maximum). The paper's
        Global arm ends far below its own peak (§7.1), which is exactly what a
        single reported number hides.
        """
        return self.final(k) - self.peak(k)[1]

    def masking_gap(self, *, round_idx: int | None = None, k: int | None = None) -> float:
        """``pass@k - pass@1`` — the size of what pass@k is covering up (§7.1)."""
        return self.pass_at_k(k, round_idx=round_idx) - self.pass_at_1(round_idx=round_idx)

    def by_level(
        self,
        level_map: Mapping[str, int],
        *,
        k: int | None = None,
        round_idx: int | None = None,
    ) -> dict[int, float]:
        """pass@k per GAIA level (A.2 p.28: 39/52/12).

        Raises if a scored task is missing from ``level_map``: silently dropping
        it would change the denominator of a stratum without saying so, and with
        twelve level-3 tasks that is a visible fraction of the result.
        """
        scope = self._scope(round_idx)
        missing = sorted({result.task_id for result in scope} - set(level_map))
        if missing:
            raise ValueError(f"level_map is missing {len(missing)} scored task(s): {missing[:5]}")
        by_level: dict[int, list[TaskResult]] = {}
        for result in scope:
            by_level.setdefault(int(level_map[result.task_id]), []).append(result)
        return {
            level: self._mean_estimate(rows, k if k is not None else self.k)
            for level, rows in sorted(by_level.items())
        }

    def level_counts(self, level_map: Mapping[str, int], *, round_idx: int | None = None) -> dict[int, int]:
        """Tasks scored per level — the denominators behind :meth:`by_level`."""
        counts: dict[int, int] = {}
        for result in self._scope(round_idx):
            level = int(level_map[result.task_id])
            counts[level] = counts.get(level, 0) + 1
        return dict(sorted(counts.items()))

    # ------------------------------------------------------------------
    # infrastructure failures (A.3)
    # ------------------------------------------------------------------

    def infra_failure_count(self, *, round_idx: int | None = None) -> int:
        return sum(result.infra_failures for result in self._scope(round_idx))

    def infra_failure_rate(self, *, round_idx: int | None = None) -> float | None:
        """Share of attempts lost to infrastructure; ``None`` with no attempts.

        The rate is a *health* signal, not a correction: A.3 counts these
        attempts as failures and nothing here removes them from a denominator.
        A run where it climbs is a run whose scores are partly an infrastructure
        measurement, which is a threat to report, not a number to adjust.
        """
        scope = self._scope(round_idx)
        attempts = sum(result.n_att for result in scope)
        if attempts == 0:
            return None
        return sum(result.infra_failures for result in scope) / attempts

    def budget_exhaustion_count(self, *, round_idx: int | None = None) -> int:
        """Attempts stopped by the runtime cost/token budget (not infra)."""
        return sum(result.budget_exhaustions for result in self._scope(round_idx))

    def budget_exhaustion_rate(self, *, round_idx: int | None = None) -> float | None:
        scope = self._scope(round_idx)
        attempts = sum(result.n_att for result in scope)
        if attempts == 0:
            return None
        return sum(result.budget_exhaustions for result in scope) / attempts

    # ------------------------------------------------------------------
    # variant-pool process data — ours (SPEC §6.7); the paper reports none
    # ------------------------------------------------------------------

    def variant_count_curve(self) -> list[int]:
        """Distinct variants carrying tasks in each round, in round order.

        A run with no ``variant_id`` anywhere (M0) reports 1 per round: there
        is one harness, and calling that 0 would make the M0 and M1 curves
        incomparable at round 0.
        """
        return [len({result.variant for result in self.results_in(r)}) for r in self.rounds()]

    def routing_hit_rate(self) -> float | None:
        """Share of routed evaluations where the routed variant solved the task.

        ``None`` when nothing was routed — M0 has no router, and reporting 0.0
        would read as "routing never worked" for a run that never routed. This
        is the online counterpart to the ledger's estimate: it says whether
        ``argmax S_hat`` sent tasks to variants that could actually do them.
        """
        routed = [result for result in self.results if result.variant_id is not None]
        if not routed:
            return None
        return sum(1 for result in routed if result.solved) / len(routed)

    def coverage_per_variant(self) -> dict[str, int]:
        """Distinct tasks each variant was ever routed to (SPEC §6.7)."""
        coverage: dict[str, set[str]] = {}
        for result in self.results:
            coverage.setdefault(result.variant, set()).add(result.task_id)
        return {variant: len(tasks) for variant, tasks in sorted(coverage.items())}

    def fork_retire_events(self) -> list[dict[str, Any]]:
        """The pool's fork/retire axis, in round order (SPEC §6.7)."""
        selected = [event for event in self.events if event.get("kind") in POOL_EVENT_KINDS]
        return sorted(selected, key=lambda event: (event["round_idx"], event["kind"], event.get("variant_id", "")))

    def round_diagnostics(self) -> list[dict[str, Any]]:
        """Active-pool denominators and no-candidate status, round by round."""
        diagnostics: list[dict[str, Any]] = []
        for round_idx in self.rounds():
            explicit = self.round_metadata.get(round_idx)
            if explicit is not None:
                diagnostics.append(dict(explicit))
                continue
            rows = self.results_in(round_idx)
            task_ids = {row.task_id for row in rows}
            candidate_ids = {
                row.candidate_id for row in self.candidate_results if row.round_idx == round_idx
            }
            diagnostics.append(
                {
                    "round": round_idx,
                    "candidate_count": len(candidate_ids),
                    "no_candidate": not candidate_ids,
                    "active_variant_count": len({row.variant for row in rows}),
                    "evaluated_tasks": len(task_ids),
                    "evaluated_task_denominator": len(task_ids),
                    "complete": len(rows) == len(task_ids),
                }
            )
        return diagnostics

    def candidate_diagnostics(self, *, k: int | None = None) -> dict[str, Any]:
        """Candidate-only audit stream; never folded into final/peak/curve."""
        k = self.k if k is None else k
        by_round: list[dict[str, Any]] = []
        candidate_rounds = sorted({row.round_idx for row in self.candidate_results})
        for round_idx in candidate_rounds:
            rows = [row for row in self.candidate_results if row.round_idx == round_idx]
            by_round.append({"round": round_idx, **self._candidate_summary(rows, k)})
        summary = self._candidate_summary(self.candidate_results, k)
        return {
            "measurement_scope": "candidate_gate",
            **summary,
            "by_round": by_round,
            "results": [row.to_dict() for row in self.candidate_results],
        }

    def _candidate_summary(
        self,
        rows: Sequence[CandidateTaskResult],
        k: int,
    ) -> dict[str, Any]:
        """Candidate-level counts plus the evaluated task denominator.

        Counts are over ``(round, candidate_id)`` so a replayed identifier in a
        later round cannot collapse two attempts into one. The shipping outcome
        is classified on the candidate's single *final* decision, so ``applied``,
        ``forked`` and ``rejected`` are mutually exclusive: an APPLY/FORK winner
        is never miscounted as ``rejected`` merely because it carries an
        ``archive_reason`` (e.g. a forced-gate audit note). ``rejected`` is the
        terminal non-ship — decision REJECT, or a pre-gate failure (a failed
        stage / archive reason with no APPLY/FORK). ``skipped`` is the orthogonal
        axis (no evaluated task rows) and may overlap ``rejected`` when a
        candidate fails a pre-gate check before it is ever scored. The three
        outcome buckets plus the pure skips (skipped and not rejected) partition
        the attempted denominator.
        """
        grouped: dict[tuple[int, str], list[CandidateTaskResult]] = {}
        for row in rows:
            grouped.setdefault((row.round_idx, row.candidate_id), []).append(row)

        candidate_summaries: list[dict[str, Any]] = []
        evaluated_keys: set[tuple[int, str]] = set()
        applied_keys: set[tuple[int, str]] = set()
        forked_keys: set[tuple[int, str]] = set()
        rejected_keys: set[tuple[int, str]] = set()
        skipped_keys: set[tuple[int, str]] = set()
        evaluated_rows = [row for row in rows if row.evaluated]

        for key in sorted(grouped):
            candidate_rows = grouped[key]
            scored_rows = [row for row in candidate_rows if row.evaluated]
            evaluated = bool(scored_rows)
            # Classify on the one settled gate outcome, not on any archived
            # note: a FORK/APPLY winner that carries an archive_reason must not
            # be counted as rejected (runs/forceprobe2 P1).
            final_decision = next(
                (row.decision for row in candidate_rows if row.decision is not None),
                None,
            )
            applied = final_decision == "apply"
            forked = final_decision == "fork"
            rejected = (
                not applied
                and not forked
                and any(
                    row.decision == "reject"
                    or row.failed_stage is not None
                    or bool(row.archive_reason)
                    for row in candidate_rows
                )
            )
            skipped = not evaluated
            if evaluated:
                evaluated_keys.add(key)
            if applied:
                applied_keys.add(key)
            if forked:
                forked_keys.add(key)
            if rejected:
                rejected_keys.add(key)
            if skipped:
                skipped_keys.add(key)

            candidate_summaries.append(
                {
                    "round": key[0],
                    "candidate_id": key[1],
                    "target_variant_id": candidate_rows[0].target_variant_id,
                    "evaluated": evaluated,
                    "applied": applied,
                    "forked": forked,
                    "rejected": rejected,
                    "skipped": skipped,
                    "decision": final_decision,
                    "failed_stage": next(
                        (row.failed_stage for row in candidate_rows if row.failed_stage is not None),
                        None,
                    ),
                    "archive_reason": next(
                        (row.archive_reason for row in candidate_rows if row.archive_reason),
                        "",
                    ),
                    "skipped_reason": next(
                        (row.skipped_reason for row in candidate_rows if row.skipped_reason is not None),
                        None,
                    ),
                    "evaluated_task_denominator": len(scored_rows),
                    "attempts": sum(row.n_att for row in scored_rows),
                    f"pass_at_{k}": self._mean_estimate(scored_rows, k) if scored_rows else None,
                }
            )

        attempted = len(grouped)
        evaluated = len(evaluated_keys)
        return {
            # ``candidate_count`` / ``evaluated_tasks`` are retained for older
            # report readers. The explicit names remove their old ambiguity.
            "candidate_count": attempted,
            "attempted_candidate_count": attempted,
            "evaluated_candidate_count": evaluated,
            "applied_candidate_count": len(applied_keys),
            "forked_candidate_count": len(forked_keys),
            "rejected_candidate_count": len(rejected_keys),
            "skipped_candidate_count": len(skipped_keys),
            "candidate_denominator": attempted,
            "evaluation_denominator": attempted,
            "evaluated_candidate_rate": evaluated / attempted if attempted else None,
            "evaluated_tasks": len(evaluated_rows),
            "evaluated_task_denominator": len(evaluated_rows),
            "attempts": sum(row.n_att for row in evaluated_rows),
            f"pass_at_{k}": self._mean_estimate(evaluated_rows, k) if evaluated_rows else None,
            "infra_failures": sum(row.infra_failures for row in evaluated_rows),
            "budget_exhaustions": sum(row.budget_exhaustions for row in evaluated_rows),
            "candidates": candidate_summaries,
        }

    # ------------------------------------------------------------------
    # rendering
    # ------------------------------------------------------------------

    def to_dict(self, *, level_map: Mapping[str, int] | None = None, k: int | None = None) -> dict[str, Any]:
        """The full contract as a mapping. Never a peak without a final."""
        k = k if k is not None else self.k
        rounds = self.rounds()
        payload: dict[str, Any] = {
            "run_name": self.run_name,
            "lock_sha256": self.lock_sha256,
            "measurement_scope": "settled_active_pool",
            "k": k,
            "rounds": rounds,
            "tasks": len({result.task_id for result in self.results}),
            "attempts": sum(result.n_att for result in self.results),
            # ``infra_failures`` / ``budget_exhaustions`` and their ``*_rate``
            # companions are LAST-ROUND scoped (the ``round_idx=None`` default of
            # the count/rate helpers), so each count is paired with the
            # last-round rate beside it. This differs from ``attempts`` above,
            # which is a run total. The keys are kept last-round for backward
            # compatibility; the run-total triple that matches the markdown
            # headline is exposed separately as the ``*_run_total`` keys below.
            "infra_failures": self.infra_failure_count(),
            "infra_failure_rate": self.infra_failure_rate(),
            "budget_exhaustions": self.budget_exhaustion_count(),
            "budget_exhaustion_rate": self.budget_exhaustion_rate(),
            "attempts_run_total": sum(result.n_att for result in self.results),
            "infra_failures_run_total": sum(result.infra_failures for result in self.results),
            "budget_exhaustions_run_total": sum(
                result.budget_exhaustions for result in self.results
            ),
            "round_diagnostics": self.round_diagnostics(),
            "candidate_diagnostics": self.candidate_diagnostics(k=k),
            "variant_count_curve": self.variant_count_curve(),
            "routing_hit_rate": self.routing_hit_rate(),
            "coverage_per_variant": self.coverage_per_variant(),
            "fork_retire_events": self.fork_retire_events(),
        }
        if rounds:
            peak_round, peak_score = self.peak(k)
            payload.update(
                {
                    f"final_pass_at_{k}": self.final(k),
                    "peak_round": peak_round,
                    f"peak_pass_at_{k}": peak_score,
                    "drift_from_peak": self.drift(k),
                    "final_pass_at_1": self.pass_at_1(),
                    "final_per_attempt_rate": self.per_attempt_rate(),
                    "masking_gap": self.masking_gap(k=k),
                    "curve": self.curve(k),
                    "curve_pass_at_1": [self.pass_at_1(round_idx=r) for r in rounds],
                }
            )
        if level_map is not None:
            payload["by_level"] = {str(level): score for level, score in self.by_level(level_map, k=k).items()}
            payload["level_counts"] = {str(level): n for level, n in self.level_counts(level_map).items()}
        return payload

    def to_json(self, *, level_map: Mapping[str, int] | None = None, k: int | None = None) -> str:
        """Serialise :meth:`to_dict` verbatim (JSON).

        EXP-E08 closure: ``to_json`` is a pure serialisation of ``to_dict`` and
        adds no keys of its own, so the run-total triple (``attempts_run_total`` /
        ``infra_failures_run_total`` / ``budget_exhaustions_run_total``) and its
        last-round companions land in the JSON exactly as ``to_dict`` exposes
        them — the two outputs cannot drift. ``test_reporting`` pins this
        (``to_json`` parses back equal to ``to_dict``, run-total keys present).
        """
        return json.dumps(self.to_dict(level_map=level_map, k=k), ensure_ascii=False, indent=2, sort_keys=True)

    def to_markdown(self, *, level_map: Mapping[str, int] | None = None, k: int | None = None) -> str:
        """Human-readable report. ``final`` and ``peak`` share one table, always.

        ``level_map`` and ``k`` extend the SPEC signature as keyword arguments
        with defaults, so the zero-argument call in SPEC §6.7 still works; the
        level table simply needs the mapping the report does not carry.
        """
        k = k if k is not None else self.k
        rounds = self.rounds()
        lines: list[str] = [f"# Run report{f': {self.run_name}' if self.run_name else ''}", ""]
        if self.lock_sha256:
            lines += [f"lock: `{self.lock_sha256}`", ""]
        if not rounds:
            return "\n".join(lines + ["_no results recorded_", ""])

        peak_round, peak_score = self.peak(k)
        lines += [
            "## Active-pool headline",
            "",
            "| metric | value |",
            "|---|---|",
            f"| final pass@{k} (round {rounds[-1]}) | {self.final(k):.4f} |",
            f"| peak pass@{k} (round {peak_round}) | {peak_score:.4f} |",
            f"| drift (final - peak) | {self.drift(k):+.4f} |",
            f"| final pass@1 | {self.pass_at_1():.4f} |",
            f"| final per-attempt rate | {self.per_attempt_rate():.4f} |",
            f"| pass@{k} - pass@1 (masking gap, §7.1) | {self.masking_gap(k=k):.4f} |",
            # Run-total health counts over the whole settled active pool, unlike
            # the per-round score rows above. All three share one scope: summing
            # ``self.results`` (every round) keeps them consistent. Using the
            # ``infra_failure_count`` / ``budget_exhaustion_count`` helpers here
            # would scope only the final round (their ``round_idx=None`` default),
            # so a run with exhaustions in earlier rounds would under-report them.
            f"| attempts / infra failures / budget exhaustion (run total) | "
            f"{sum(r.n_att for r in self.results)} / "
            f"{sum(r.infra_failures for r in self.results)} / "
            f"{sum(r.budget_exhaustions for r in self.results)} |",
            "",
            "> The peak is selected on the same task set the run evolved on, with",
            "> no held-out split (§7.7). It is reported next to the final score,",
            f"> never instead of it. pass@{k} can mask a per-attempt decline (§7.1),",
            "> which is what the pass@1 row is for.",
            "",
            "## Per-round curve",
            "",
            f"| round | pass@{k} | pass@1 | per-attempt | tasks/denominator | variants | candidate |",
            "|---|---|---|---|---|---|---|",
        ]
        counts = self.variant_count_curve()
        diagnostics = {row["round"]: row for row in self.round_diagnostics()}
        for index, round_idx in enumerate(rounds):
            diag = diagnostics[round_idx]
            lines.append(
                f"| {round_idx} | {self.pass_at_k(k, round_idx=round_idx):.4f} "
                f"| {self.pass_at_1(round_idx=round_idx):.4f} "
                f"| {self.per_attempt_rate(round_idx=round_idx):.4f} "
                f"| {diag['evaluated_tasks']}/{diag['evaluated_task_denominator']} "
                f"| {counts[index]} "
                f"| {'none' if diag['no_candidate'] else diag['candidate_count']} |"
            )
        lines.append("")

        if level_map is not None:
            by_level = self.by_level(level_map, k=k)
            level_counts = self.level_counts(level_map)
            lines += ["## By GAIA level", "", f"| level | tasks | pass@{k} |", "|---|---|---|"]
            lines += [f"| {level} | {level_counts[level]} | {score:.4f} |" for level, score in by_level.items()]
            lines.append("")

        coverage = self.coverage_per_variant()
        hit_rate = self.routing_hit_rate()
        lines += [
            "## Variant pool",
            "",
            f"- variant count by round: {counts}",
            f"- routing hit rate: {'n/a (nothing routed)' if hit_rate is None else f'{hit_rate:.4f}'}",
            f"- coverage per variant: {coverage}",
        ]
        events = self.fork_retire_events()
        if events:
            lines += ["- fork/retire events:"]
            lines += [
                f"  - r{event['round_idx']} {event['kind']} {event.get('variant_id', '')}".rstrip()
                for event in events
            ]
        else:
            lines += ["- fork/retire events: none"]
        lines.append("")

        candidate = self.candidate_diagnostics(k=k)
        lines += [
            "## Candidate-gate diagnostics",
            "",
            "> Candidate measurements are pre-settlement diagnostics. Rejected",
            "> candidates never contribute to the active-pool headline or curve.",
            "",
            f"- candidates attempted (denominator): {candidate['attempted_candidate_count']}",
            f"- candidates evaluated: {candidate['evaluated_candidate_count']}",
            f"- candidates applied: {candidate['applied_candidate_count']}",
            f"- candidates forked: {candidate['forked_candidate_count']}",
            f"- candidates rejected: {candidate['rejected_candidate_count']}",
            f"- candidates skipped: {candidate['skipped_candidate_count']}",
            f"- candidate task evaluations: {candidate['evaluated_tasks']}",
            f"- candidate infra failures: {candidate['infra_failures']}",
            f"- candidate budget exhaustions: {candidate['budget_exhaustions']}",
            "",
        ]
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------

    def _scope(self, round_idx: int | None) -> list[TaskResult]:
        if round_idx is None:
            rounds = self.rounds()
            if not rounds:
                return []
            round_idx = rounds[-1]
        return self.results_in(round_idx)

    @staticmethod
    def _mean_estimate(rows: Sequence[TaskResult], k: int) -> float:
        if not rows:
            return 0.0
        return sum(pass_at_k(row.n_att, row.n_pass, k) for row in rows) / len(rows)


def report_from_rows(rows: Iterable[Mapping[str, Any]], **kwargs: Any) -> RunReport:
    """Build a report from plain mappings (a ``comparison.json``-shaped feed)."""
    report = RunReport(**kwargs)
    for row in rows:
        report.add(
            TaskResult(
                task_id=str(row["task_id"]),
                round_idx=int(row["round_idx"]),
                n_att=int(row["n_att"]),
                n_pass=int(row["n_pass"]),
                variant_id=row.get("variant_id"),
                infra_failures=int(row.get("infra_failures", 0)),
                budget_exhaustions=int(row.get("budget_exhaustions", 0)),
            )
        )
    return report


# ---------------------------------------------------------------------------
# P1-2 — paired-arm CI + drift reporting (mirror paper Table 5)
# ---------------------------------------------------------------------------
#
# OPTIMIZATION-PLAN P1-2 / PAPER-GAP-AUDIT §0: the paper's headline (Table 5) is a
# LONG-HORIZON, LARGE-n contrast — Ensemble stays up while Global peaks early and
# collapses — and its visibility depends on the CI at the bed (task-set) size
# (§7.7 reports ±8.5% at n=103, ±28% at n=12). These pure helpers put the two arms
# side by side with the final/peak/drift columns of Table 5 and a CI at each arm's
# bed size, so a run can never quote one arm's peak without the paired context.


def binomial_ci(passes: float, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval for a binomial proportion (95% at the default z).

    Wilson is chosen over Wald because Wald collapses to a zero-width interval at
    ``p == 0`` or ``p == 1`` and under-covers for small ``n`` — exactly this
    report's regime (GAIA level 3 is 12 tasks). For ``passes`` successes out of
    ``n`` trials with ``p = passes / n``::

        center = (p + z^2/2n) / (1 + z^2/n)
        margin = (z / (1 + z^2/n)) * sqrt( p(1-p)/n + z^2/4n^2 )

    returning ``(center - margin, center + margin)`` clamped to ``[0, 1]``.

    ``passes`` may be FRACTIONAL: a macro-averaged pass@k rate over ``n`` tasks
    has no integer success count, so :func:`paired_arm_summary` passes the
    effective count ``rate * n`` and ``p`` is recovered exactly. ``passes`` is
    clamped into ``[0, n]``. ``n <= 0`` returns ``(0.0, 1.0)`` — no data, maximal
    uncertainty — so the caller never divides by zero.
    """
    if n <= 0:
        return (0.0, 1.0)
    passes = min(max(float(passes), 0.0), float(n))
    p = passes / n
    z2 = z * z
    denom = 1.0 + z2 / n
    center = (p + z2 / (2.0 * n)) / denom
    margin = (z / denom) * sqrt(p * (1.0 - p) / n + z2 / (4.0 * n * n))
    return (max(0.0, center - margin), min(1.0, center + margin))


def _arm_stats(report: RunReport, k: int) -> dict[str, Any] | None:
    """Final/peak/drift + Wilson CI at the final-round bed size; ``None`` if empty.

    The CI is a binomial-proportion interval over the arm's final-round task set
    (its "bed"), treating the final pass@k rate as the proportion — mirroring the
    paper's own n-dependent CI framing (§7.7). ``passes`` is therefore the
    effective count ``rate * bed`` (fractional under a macro-averaged rate).
    """
    rounds = report.rounds()
    if not rounds:
        return None
    final_round = rounds[-1]
    bed = len({r.task_id for r in report.results_in(final_round)})
    final = report.final(k)
    peak_round, peak = report.peak(k)
    return {
        "final": final,
        "peak": peak,
        "peak_round": peak_round,
        "drift": report.drift(k),
        "bed": bed,
        "ci": binomial_ci(final * bed, bed),
        "final_round": final_round,
    }


def paired_arm_summary(
    report_a: RunReport,
    report_b: RunReport,
    *,
    label_a: str = "global",
    label_b: str = "ensemble",
    k: int | None = None,
) -> str:
    """Compact markdown comparing two arms, mirroring paper Table 5's columns.

    Per arm: final pass@k, peak pass@k (+round), drift (final - peak), and the
    95% Wilson CI at the arm's final-round bed size. Below the table: the
    between-arm delta (``label_b - label_a`` on final pass@k) and a one-line
    CI-overlap note (overlapping CIs mean the delta is not resolved at these bed
    sizes — the PAPER-GAP-AUDIT §0 point about small-n visibility). Table 5's
    ``tokens`` column is optional-if-available: emitted only when a report carries
    a truthy ``tokens`` attribute, since :class:`RunReport` does not track tokens.

    Consumes :class:`RunReport` instances (not ``to_dict``): the class already
    exposes final/peak/drift and the per-round task rows, so the bed size and CI
    read straight off it — the cleaner seam. An empty arm renders ``_no results_``
    and is skipped in the delta/overlap line. No :class:`RunReport` method is
    called for its side effects; this is a pure read.
    """
    k = k if k is not None else report_a.k
    a = _arm_stats(report_a, k)
    b = _arm_stats(report_b, k)
    tokens_a = getattr(report_a, "tokens", None)
    tokens_b = getattr(report_b, "tokens", None)
    show_tokens = bool(tokens_a) or bool(tokens_b)

    header = [
        "arm",
        f"final pass@{k}",
        f"peak pass@{k} (round)",
        "drift (final-peak)",
        "95% CI (Wilson)",
    ]
    if show_tokens:
        header.append("tokens")
    lines = [
        f"## Paired-arm comparison (pass@{k}, mirrors paper Table 5)",
        "",
        "| " + " | ".join(header) + " |",
        "|" + "---|" * len(header),
    ]

    def _row(label: str, stats: dict[str, Any] | None, tokens: Any) -> str:
        if stats is None:
            cells = [label, "_no results_", "_no results_", "_no results_", "_no results_"]
        else:
            lo, hi = stats["ci"]
            cells = [
                label,
                f"{stats['final']:.4f}",
                f"{stats['peak']:.4f} (r{stats['peak_round']})",
                f"{stats['drift']:+.4f}",
                f"[{lo:.4f}, {hi:.4f}] (n={stats['bed']})",
            ]
        if show_tokens:
            cells.append(str(tokens) if tokens else "n/a")
        return "| " + " | ".join(cells) + " |"

    lines.append(_row(label_a, a, tokens_a))
    lines.append(_row(label_b, b, tokens_b))
    lines.append("")

    if a is None or b is None:
        missing = label_a if a is None else label_b
        lines.append(
            f"- between-arm delta / CI overlap: n/a (missing results for {missing})"
        )
        lines.append("")
        return "\n".join(lines)

    delta = b["final"] - a["final"]
    lo_a, hi_a = a["ci"]
    lo_b, hi_b = b["ci"]
    overlap = lo_a <= hi_b and lo_b <= hi_a
    if overlap:
        overlap_note = (
            f"CIs overlap — the {delta:+.4f} delta is NOT resolved at these bed "
            f"sizes (n_{label_a}={a['bed']}, n_{label_b}={b['bed']})"
        )
    else:
        higher = label_b if lo_b > hi_a else label_a
        overlap_note = (
            f"CIs disjoint — {higher}'s CI lies entirely above the other's "
            f"(the {delta:+.4f} delta is resolved at these bed sizes)"
        )
    lines.append(
        f"- between-arm delta (final pass@{k}, {label_b} - {label_a}): {delta:+.4f}"
    )
    lines.append(f"- CI overlap: {overlap_note}")
    lines.append("")
    return "\n".join(lines)
