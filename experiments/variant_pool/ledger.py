# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""W3 + W21 — per-(variant, task) success ledger with a full-history seesaw baseline.

Two jobs, both load-bearing:

**W3 — the routing estimate.** The paper's routing rule is
``route(x) = argmax_k S_hat(k, cluster(x))`` and its *entire* description of
``S_hat`` is one phrase: "highest estimated success rate on that task's cluster
across prior rounds" (§4.5 p.11). No estimator, no window, no smoothing, no
confidence bound — and the wording drifts across the paper ("estimated ...
across prior rounds" p.11 / "prior" p.17 / "overall" p.22). Report §5 gap 2.
So the estimator here is **ours** and is an explicit experiment choice
(SPEC §6.6): Laplace ``(passes + 1) / (attempts + 2)``, prior ``0.5`` for a
cell that has never been evaluated.

**W21 — the seesaw baseline.** The seesaw constraint (§4.1 p.8) is, verbatim,
that a candidate "must not regress any previously solved task recorded in
``T_t``". ``T_t`` is the whole accumulated trace store, so the baseline is the
**full history** of everything ever solved by *any* variant — not last round's
result. :attr:`SuccessLedger.ever_solved` is that set: it only ever grows, so a
task solved in R3 and quietly broken in R5 is still a regression when an R6
candidate leaves it at 0/2.

The ``before_round`` cut and rolling window
-------------------------------------------
:meth:`SuccessLedger.estimate` takes ``before_round``: only observations with
``round_idx < before_round`` are eligible. This is the ledger-side half of the
routing freeze (SPEC §6.2) — the round's routing must be computed from *prior*
rounds only, or online routing degenerates into a disguised oracle and the
M1-vs-M0 comparison is self-deceiving. The other half is
:meth:`Router.freeze_routing`, which refuses outright to run when the ledger
already contains this round's results.

``window`` is **OURS** because the paper specifies no estimator horizon. The
ledger retains per-round counts so ``window=w`` is a genuine rolling interval:
the eligible rounds are ``[reference - w, reference)``, where ``reference`` is
``before_round`` when supplied and otherwise one past the latest recorded
round. :meth:`cell` and reads without either cut retain the original cumulative
full-history behaviour.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from statistics import fmean

#: Prior for a cell we have never evaluated. Ours (SPEC §6.6); the ablation
#: range is {0, 0.5, inherit-from-parent}. 0.5 is the uninformative choice:
#: it neither blocks a fresh variant from being tried nor pretends it works.
DEFAULT_STALE_PRIOR = 0.5

#: Variant-level ranking estimators. ``task_macro`` is the engineering default
#: because it gives every evaluated task one vote instead of letting tasks with
#: more rollouts dominate retirement. The paper says only "lowest-performing"
#: and specifies neither a denominator nor a task/cluster weighting (§4.5).
ROLLUP_MODES = ("task_macro", "cluster_macro", "raw")


@dataclass
class CellStats:
    """Running totals for one ``(variant, task)`` cell.

    Under pass@2 each evaluated round contributes ``attempts += 2`` and
    ``passes += n_pass`` with ``n_pass in {0, 1, 2}`` (§6.1 p.15).
    ``last_round`` is the most recent round that wrote this cell; ``-1`` means
    "never evaluated".
    """

    passes: int = 0
    attempts: int = 0
    last_round: int = -1


@dataclass
class _RoundStats:
    """Counts recorded for one exact ``(variant, task, round)`` bucket."""

    passes: int = 0
    attempts: int = 0


class SuccessLedger:
    """``S[variant_id][task_id] -> CellStats`` plus the full-history solved set."""

    def __init__(
        self,
        *,
        stale_prior: float = DEFAULT_STALE_PRIOR,
        laplace: bool = True,
        rollup_mode: str = "task_macro",
        task_clusters: Mapping[str, str] | None = None,
    ) -> None:
        if not 0.0 <= stale_prior <= 1.0:
            raise ValueError(f"stale_prior must be in [0, 1], got {stale_prior}")
        if rollup_mode not in ROLLUP_MODES:
            raise ValueError(f"rollup_mode must be one of {ROLLUP_MODES}, got {rollup_mode!r}")
        self._cells: dict[str, dict[str, CellStats]] = {}
        self._rounds: dict[str, dict[str, dict[int, _RoundStats]]] = {}
        #: W21 — every task ever solved by *any* variant, across all rounds.
        self.ever_solved: set[str] = set()
        self.stale_prior = stale_prior
        self.laplace = laplace
        self.rollup_mode = rollup_mode
        self.task_clusters = dict(task_clusters) if task_clusters is not None else None

    # ------------------------------------------------------------------
    # writes
    # ------------------------------------------------------------------

    def record(self, variant_id: str, task_id: str, n_pass: int, n_att: int, round_idx: int) -> CellStats:
        """Fold one round's rollouts for ``(variant, task)`` into the ledger.

        A task counts as solved the moment a single rollout passes (pass@2
        semantics), and once solved it stays in :attr:`ever_solved` forever —
        that monotonicity *is* the W21 baseline.
        """
        if n_att < 0 or n_pass < 0:
            raise ValueError(f"negative rollout counts: n_pass={n_pass}, n_att={n_att}")
        if n_pass > n_att:
            raise ValueError(f"n_pass={n_pass} exceeds n_att={n_att}")
        cell = self._cells.setdefault(variant_id, {}).setdefault(task_id, CellStats())
        cell.passes += n_pass
        cell.attempts += n_att
        cell.last_round = max(cell.last_round, round_idx)
        round_stats = (
            self._rounds.setdefault(variant_id, {})
            .setdefault(task_id, {})
            .setdefault(round_idx, _RoundStats())
        )
        round_stats.passes += n_pass
        round_stats.attempts += n_att
        if n_pass >= 1:
            self.ever_solved.add(task_id)
        return cell

    # ------------------------------------------------------------------
    # reads
    # ------------------------------------------------------------------

    def cell(self, variant_id: str, task_id: str) -> CellStats | None:
        """The raw cell, or ``None`` if never evaluated."""
        return self._cells.get(variant_id, {}).get(task_id)

    def estimate(
        self,
        variant_id: str,
        task_id: str,
        *,
        before_round: int | None = None,
        window: int | None = None,
    ) -> float:
        """S_hat for one cell — Laplace-smoothed, prior ``0.5`` when stale.

        ``before_round`` enforces the routing freeze by excluding that round
        and every later round. ``window`` is the OURS recency ablation (default:
        full history) and selects exact per-round buckets in the interval
        described in the module docstring.
        """
        passes, attempts = self.aggregate_counts(
            variant_id,
            (task_id,),
            before_round=before_round,
            window=window,
        )
        return self._rate(passes, attempts)

    def estimate_cluster(
        self,
        variant_id: str,
        task_ids: Iterable[str],
        *,
        before_round: int | None = None,
        window: int | None = None,
    ) -> float:
        """Estimate one true ``(variant, cluster)`` cell.

        The cluster denominator is the sum of attempts for every eligible task
        in the cluster. Laplace smoothing is applied **once to that aggregate**:
        ``(Σpasses + 1) / (Σattempts + 2)``. Applying a prior independently to
        every task and then averaging would make the answer depend on how finely
        a cluster happened to be enumerated, which the paper never licenses.

        ``before_round`` and ``window`` use the same per-round eligibility as
        :meth:`estimate`; consequently a frozen round cannot leak through an
        aggregate while older observations of the same task remain usable.
        """
        passes, attempts = self.aggregate_counts(
            variant_id,
            task_ids,
            before_round=before_round,
            window=window,
        )
        return self._rate(passes, attempts)

    def aggregate_counts(
        self,
        variant_id: str,
        task_ids: Iterable[str],
        *,
        before_round: int | None = None,
        window: int | None = None,
    ) -> tuple[int, int]:
        """Return ``(passes, attempts)`` over eligible, de-duplicated tasks.

        This is public so the router can distinguish a genuine 0.5 estimate
        from an empty cluster without comparing the estimate to the prior.
        """
        self._validate_window(window)
        passes = 0
        attempts = 0
        for task_id in set(task_ids):
            task_passes, task_attempts = self._task_counts(
                variant_id,
                task_id,
                before_round=before_round,
                window=window,
            )
            passes += task_passes
            attempts += task_attempts
        return passes, attempts

    def _task_counts(
        self,
        variant_id: str,
        task_id: str,
        *,
        before_round: int | None,
        window: int | None,
    ) -> tuple[int, int]:
        """Counts for one task after applying an exact round interval."""
        cell = self._cells.get(variant_id, {}).get(task_id)
        if cell is None or cell.attempts == 0:
            return 0, 0
        if before_round is None and window is None:
            return cell.passes, cell.attempts

        reference = before_round if before_round is not None else self.max_last_round() + 1
        lower = reference - window if window is not None else None
        passes = 0
        attempts = 0
        for round_idx, stats in self._rounds.get(variant_id, {}).get(task_id, {}).items():
            if round_idx >= reference:
                continue
            if lower is not None and round_idx < lower:
                continue
            passes += stats.passes
            attempts += stats.attempts
        return passes, attempts

    @staticmethod
    def _validate_window(window: int | None) -> None:
        if window is not None and window < 1:
            raise ValueError(f"window must be >= 1, got {window}")

    def _rate(self, passes: int, attempts: int) -> float:
        if attempts == 0:
            return self.stale_prior
        if self.laplace:
            return (passes + 1) / (attempts + 2)
        return passes / attempts

    def is_ever_solved(self, task_id: str) -> bool:
        """W21 — was this task ever solved, by any variant, in any round?"""
        return task_id in self.ever_solved

    def variant_rollup(
        self,
        variant_id: str,
        *,
        mode: str | None = None,
        task_clusters: Mapping[str, str] | None = None,
        before_round: int | None = None,
        window: int | None = None,
    ) -> float:
        """Overall variant score for retirement/cold-start ranking.

        ``task_macro`` (default)
            Mean of per-task raw success rates. Every task has equal weight,
            regardless of how many times it was attempted.
        ``cluster_macro``
            Pool attempts within each injected cluster, then give every cluster
            equal weight. A mapping is required; a task missing from a partial
            mapping receives its own deterministic singleton cluster.
        ``raw``
            The legacy ``Σpasses / Σattempts`` rollup. It remains available for
            compatibility, but it can rank a variant mostly by where repeated
            attempts happened rather than by breadth of competence.

        The paper only says "lowest-performing variant" and gives no metric,
        denominator, window, or tie rule. Choosing task-macro by default is
        therefore an explicitly marked engineering decision, not a paper claim.
        """
        selected = self.rollup_mode if mode is None else mode
        if selected not in ROLLUP_MODES:
            raise ValueError(f"mode must be one of {ROLLUP_MODES}, got {selected!r}")

        self._validate_window(window)
        attempted: dict[str, tuple[int, int]] = {}
        for task_id in self._cells.get(variant_id, {}):
            passes, attempts = self._task_counts(
                variant_id,
                task_id,
                before_round=before_round,
                window=window,
            )
            if attempts:
                attempted[task_id] = (passes, attempts)
        if not attempted:
            return self.stale_prior

        if selected == "raw":
            total_passes = sum(passes for passes, _ in attempted.values())
            total_attempts = sum(attempts for _, attempts in attempted.values())
            return total_passes / total_attempts

        if selected == "task_macro":
            return fmean(passes / attempts for passes, attempts in attempted.values())

        clusters = task_clusters if task_clusters is not None else self.task_clusters
        if clusters is None:
            raise ValueError("cluster_macro rollup requires a task_id -> cluster_id mapping")
        grouped: dict[str, list[int]] = defaultdict(lambda: [0, 0])
        for task_id, (passes, attempts) in attempted.items():
            # The paper gives no unknown-cluster policy. A singleton fallback is
            # deterministic and, unlike dropping the task, keeps its evidence in
            # the ranking while making the missing assignment auditable.
            cluster_id = clusters.get(task_id, f"__unclustered__:{task_id}")
            grouped[cluster_id][0] += passes
            grouped[cluster_id][1] += attempts
        return fmean(passes / attempts for passes, attempts in grouped.values())

    def attempts_on(self, variant_id: str, task_id: str) -> int:
        """Rollouts this variant has spent on this task (0 if never tried).

        Feeds the default routing tie-break (fewest attempts first, SPEC §6.6).
        """
        cell = self._cells.get(variant_id, {}).get(task_id)
        return cell.attempts if cell is not None else 0

    def attempts_on_cluster(
        self,
        variant_id: str,
        task_ids: Iterable[str],
        *,
        before_round: int | None = None,
        window: int | None = None,
    ) -> int:
        """Eligible attempts in a cluster, used by routing tie-breaks."""
        return self.aggregate_counts(
            variant_id,
            task_ids,
            before_round=before_round,
            window=window,
        )[1]

    def max_last_round(self) -> int:
        """Highest ``last_round`` anywhere in the ledger (``-1`` when empty).

        The routing freeze guard: before freezing round *r* this must be
        ``< r``, otherwise this round's results are already visible.
        """
        return max(
            (cell.last_round for tasks in self._cells.values() for cell in tasks.values()),
            default=-1,
        )

    def tasks_of(self, variant_id: str) -> set[str]:
        """Tasks this variant has any history for."""
        return set(self._cells.get(variant_id, {}))

    def variants(self) -> set[str]:
        """Variants with any recorded history (including retired ones)."""
        return set(self._cells)
