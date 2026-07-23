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

The ``before_round`` cut (routing freeze)
-----------------------------------------
:meth:`SuccessLedger.estimate` takes ``before_round``: with it, a cell whose
``last_round >= before_round`` is not counted and falls back to the prior. This
is the ledger-side half of the routing freeze (SPEC §6.2) — the round's routing
must be computed from *prior* rounds only, or online routing degenerates into a
disguised oracle and the M1-vs-M0 comparison is self-deceiving. The other half
is :meth:`Router.freeze_routing`, which refuses outright to run when the ledger
already contains this round's results.

The cut is deliberately whole-cell: :class:`CellStats` keeps running totals
(SPEC §2.2), so a cell touched at or after ``before_round`` cannot have that
round's contribution subtracted out and is dropped entirely. Under correct
operation this is exactly equivalent to "prior rounds only", because at freeze
time for round *r* no cell can have ``last_round >= r`` yet — and if one does,
that *is* the leak we want to catch.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Prior for a cell we have never evaluated. Ours (SPEC §6.6); the ablation
#: range is {0, 0.5, inherit-from-parent}. 0.5 is the uninformative choice:
#: it neither blocks a fresh variant from being tried nor pretends it works.
DEFAULT_STALE_PRIOR = 0.5


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


class SuccessLedger:
    """``S[variant_id][task_id] -> CellStats`` plus the full-history solved set."""

    def __init__(self, *, stale_prior: float = DEFAULT_STALE_PRIOR, laplace: bool = True) -> None:
        if not 0.0 <= stale_prior <= 1.0:
            raise ValueError(f"stale_prior must be in [0, 1], got {stale_prior}")
        self._cells: dict[str, dict[str, CellStats]] = {}
        #: W21 — every task ever solved by *any* variant, across all rounds.
        self.ever_solved: set[str] = set()
        self.stale_prior = stale_prior
        self.laplace = laplace

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

        ``before_round`` enforces the routing freeze: a cell last written at or
        after that round is not prior-round evidence and is dropped to the
        prior (see the module docstring).

        ``window`` is the recency ablation of SPEC §6.6 (default: full
        history). Because cells keep running totals rather than per-round
        history, it is applied at cell granularity: a cell whose last write is
        older than ``window`` rounds before the reference round is treated as
        stale. That is coarser than a rolling per-rollout window and is
        recorded as such; the default (``None``) is unaffected.
        """
        cell = self._cells.get(variant_id, {}).get(task_id)
        if cell is None or cell.attempts == 0:
            return self.stale_prior
        if before_round is not None and cell.last_round >= before_round:
            # not evidence from a prior round -> invisible to a frozen routing pass
            return self.stale_prior
        if window is not None:
            if window < 1:
                raise ValueError(f"window must be >= 1, got {window}")
            reference = before_round if before_round is not None else self.max_last_round() + 1
            if cell.last_round < reference - window:
                return self.stale_prior
        if self.laplace:
            return (cell.passes + 1) / (cell.attempts + 2)
        return cell.passes / cell.attempts

    def is_ever_solved(self, task_id: str) -> bool:
        """W21 — was this task ever solved, by any variant, in any round?"""
        return task_id in self.ever_solved

    def variant_rollup(self, variant_id: str) -> float:
        """Overall success rate of a variant across all the cells it owns.

        Used to rank variants — retirement ("lowest-performing", §4.5 p.11),
        cold-start routing, and deployment-time collapse to a single variant
        ("highest overall success rate on the evolution set", §7.5 p.22). This
        is the raw pooled rate, not the Laplace estimate: smoothing exists to
        keep a *single sparse cell* from swinging routing, whereas a rollup
        already aggregates many cells and should not be pulled toward 0.5.
        Returns the stale prior for a variant with no attempts at all.
        """
        cells = self._cells.get(variant_id, {})
        total_passes = sum(c.passes for c in cells.values())
        total_attempts = sum(c.attempts for c in cells.values())
        if total_attempts == 0:
            return self.stale_prior
        return total_passes / total_attempts

    def attempts_on(self, variant_id: str, task_id: str) -> int:
        """Rollouts this variant has spent on this task (0 if never tried).

        Feeds the default routing tie-break (fewest attempts first, SPEC §6.6).
        """
        cell = self._cells.get(variant_id, {}).get(task_id)
        return cell.attempts if cell is not None else 0

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
