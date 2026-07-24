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
from math import comb
from typing import Any

#: Pool events the report knows how to lay on a time axis (SPEC §6.7).
POOL_EVENT_KINDS = ("fork", "retire")

#: Variant label used when a run records no variant at all (M0: one harness,
#: no pool). Keeps the per-variant views defined for baseline runs.
IMPLICIT_VARIANT = "H0"


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

    def __post_init__(self) -> None:
        if self.n_att < 0 or self.n_pass < 0 or self.infra_failures < 0:
            raise ValueError(f"{self.task_id}: negative counts in {self}")
        if self.n_pass > self.n_att:
            raise ValueError(f"{self.task_id}: n_pass={self.n_pass} exceeds n_att={self.n_att}")
        if self.infra_failures > self.n_att - self.n_pass:
            raise ValueError(
                f"{self.task_id}: {self.infra_failures} infra failures cannot coexist with "
                f"{self.n_pass} passes in {self.n_att} attempts — a failed attempt is still an attempt (A.3)"
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

    results: list[TaskResult] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)
    run_name: str = ""
    lock_sha256: str | None = None
    #: Main metric of the run. pass@2 is the paper's (§6.1 p.15).
    k: int = 2

    # ------------------------------------------------------------------
    # building
    # ------------------------------------------------------------------

    def add(self, result: TaskResult) -> None:
        self.results.append(result)

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
            "k": k,
            "rounds": rounds,
            "tasks": len({result.task_id for result in self.results}),
            "attempts": sum(result.n_att for result in self.results),
            "infra_failures": self.infra_failure_count(),
            "infra_failure_rate": self.infra_failure_rate(),
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
            "## Headline",
            "",
            "| metric | value |",
            "|---|---|",
            f"| final pass@{k} (round {rounds[-1]}) | {self.final(k):.4f} |",
            f"| peak pass@{k} (round {peak_round}) | {peak_score:.4f} |",
            f"| drift (final - peak) | {self.drift(k):+.4f} |",
            f"| final pass@1 | {self.pass_at_1():.4f} |",
            f"| final per-attempt rate | {self.per_attempt_rate():.4f} |",
            f"| pass@{k} - pass@1 (masking gap, §7.1) | {self.masking_gap(k=k):.4f} |",
            f"| attempts / infra failures | {sum(r.n_att for r in self.results)} / {self.infra_failure_count()} |",
            "",
            "> The peak is selected on the same task set the run evolved on, with",
            "> no held-out split (§7.7). It is reported next to the final score,",
            f"> never instead of it. pass@{k} can mask a per-attempt decline (§7.1),",
            "> which is what the pass@1 row is for.",
            "",
            "## Per-round curve",
            "",
            f"| round | pass@{k} | pass@1 | per-attempt | variants |",
            "|---|---|---|---|---|",
        ]
        counts = self.variant_count_curve()
        for index, round_idx in enumerate(rounds):
            lines.append(
                f"| {round_idx} | {self.pass_at_k(k, round_idx=round_idx):.4f} "
                f"| {self.pass_at_1(round_idx=round_idx):.4f} "
                f"| {self.per_attempt_rate(round_idx=round_idx):.4f} "
                f"| {counts[index]} |"
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
            )
        )
    return report
