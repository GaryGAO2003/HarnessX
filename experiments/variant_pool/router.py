# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""W2 + W7 — task router, and the routing freeze that keeps M1 honest.

The paper's rule is one line (§4.5 p.11)::

    route(x) = argmax_k S_hat(k, cluster(x))

Everything operational around it is ours (report §5, gaps 2/3/8): cold start,
tie-break, exploration rate, and which of the three readings of *cluster* is
the main arm.

Cluster (W7)
------------
Report §5.1 adjudicates three readings that the paper uses interchangeably and
never bridges. We take **(a) the routing-induced partition** as the main arm:
``cluster(x)`` is simply the variant currently carrying ``x``, which is the
literal mechanism of "tested only against tasks routed to k" (§4.5 p.11) and
costs nothing extra. Reading (b) (meta-agent failure-mode grouping) stays as a
``cluster_mode`` hook for a batch-C ablation; reading (c) (semantic/domain
clustering) is falsifiably excluded by p.18, which lists domain-aware
clustering as a *different* pilot strategy. Because the partition is
self-bootstrapping under (a), :meth:`Router.route` scores the ``(variant, task)``
cell directly rather than averaging over a separately-defined cluster.

Routing freeze (SPEC §6.2) — not a design choice
------------------------------------------------
A round's routing is frozen **before** that round's rollouts and may read only
rounds ``< round_idx``. If routing could see the round it is routing, "send the
task to whoever solves it" becomes a disguised oracle and the M1-vs-M0
comparison is self-deceiving. Two independent mechanisms enforce it:

1. :meth:`Router.freeze_routing` refuses to run at all when the ledger already
   contains a write at or after ``round_idx`` (:class:`RoutingFreezeError`).
2. :meth:`Router.route` takes ``before_round`` as a *required* keyword and
   hands it to every ``ledger.estimate`` call, so a cell from the frozen round
   is invisible even if one slips in.

The returned map is a read-only ``MappingProxyType``: the round's routing is
fixed for the whole round, and the ledger is updated afterwards for the *next*
round only.
"""

from __future__ import annotations

import random
from collections.abc import Iterable, Mapping
from types import MappingProxyType
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only, no runtime import cycle
    from .ledger import SuccessLedger
    from .pool import VariantPool

#: Cluster readings of report §5.1. Only "routed" (the main arm) is implemented
#: in stage A; the other two are batch-C ablation hooks (SPEC §5).
CLUSTER_MODES = ("routed", "failure", "level")

#: Tie-break policies for equal estimates (SPEC §6.6). Default: fewest attempts
#: on that task, which spends ties on information rather than on incumbency.
TIE_BREAKS = ("fewest_attempts", "smallest_id", "random")


class RoutingFreezeError(RuntimeError):
    """Raised when routing is asked to look at the round it is routing.

    Not a recoverable condition: seeing it means the engine recorded this
    round's rollouts before freezing its routing, which would turn online
    routing into an oracle.
    """


class Router:
    """Routes tasks to variants from prior-round evidence only."""

    def __init__(
        self,
        *,
        cluster_mode: str = "routed",
        tie_break: str = "fewest_attempts",
        epsilon: float = 0.0,
        seed: int = 0,
    ) -> None:
        if cluster_mode not in CLUSTER_MODES:
            raise ValueError(f"cluster_mode must be one of {CLUSTER_MODES}, got {cluster_mode!r}")
        if tie_break not in TIE_BREAKS:
            raise ValueError(f"tie_break must be one of {TIE_BREAKS}, got {tie_break!r}")
        if not 0.0 <= epsilon <= 1.0:
            raise ValueError(f"epsilon must be in [0, 1], got {epsilon}")
        self.cluster_mode = cluster_mode
        self.tie_break = tie_break
        self.epsilon = epsilon
        self._rng = random.Random(seed)

    # ------------------------------------------------------------------
    # W7 — cluster
    # ------------------------------------------------------------------

    def cluster_of(self, task_id: str, pool: VariantPool) -> str:
        """Main arm: the cluster of a task is the variant carrying it.

        Falls back to :meth:`cold_start` for a task nobody carries yet (round 0,
        or a task whose carrier was just retired).
        """
        if self.cluster_mode != "routed":
            raise NotImplementedError(
                f"cluster_mode {self.cluster_mode!r} is a batch-C ablation "
                "(report §5.1 readings (b)/(c)); only 'routed' is implemented in stage A"
            )
        carrier = pool.carrier_of(task_id)
        if carrier is not None:
            return carrier
        return self.cold_start(task_id, pool)

    # ------------------------------------------------------------------
    # routing freeze
    # ------------------------------------------------------------------

    def freeze_routing(
        self,
        tasks: Iterable[str],
        pool: VariantPool,
        ledger: SuccessLedger,
        round_idx: int,
    ) -> Mapping[str, str]:
        """Freeze this round's routing before any of its rollouts run.

        Raises :class:`RoutingFreezeError` if the ledger already holds a write
        from ``round_idx`` or later — under correct operation the newest cell at
        this point is ``round_idx - 1``, so anything newer is this round's
        result leaking backwards into its own routing.

        Returns a read-only map that is the routing for the entire round.
        """
        newest = ledger.max_last_round()
        if newest >= round_idx:
            raise RoutingFreezeError(
                f"routing freeze violated: ledger already holds round {newest} "
                f"while freezing routing for round {round_idx}. Routing must be frozen "
                "before this round's rollouts; the ledger may only be updated afterwards, "
                "for the next round."
            )
        frozen = {task_id: self.route(task_id, pool, ledger, before_round=round_idx) for task_id in tasks}
        return MappingProxyType(frozen)

    # ------------------------------------------------------------------
    # W2 — argmax routing
    # ------------------------------------------------------------------

    def route(self, task_id: str, pool: VariantPool, ledger: SuccessLedger, *, before_round: int) -> str:
        """``argmax_v S_hat(v, task)`` over evidence strictly before ``before_round``.

        ``before_round`` is required, not optional: an unfrozen estimate is
        never the right thing to route on.
        """
        variant_ids = sorted(pool.variants)
        if not variant_ids:
            raise RuntimeError("cannot route on an empty pool")

        explored = self.explore(variant_ids)
        if explored is not None:
            return explored

        if not self._has_prior_evidence(task_id, pool, ledger, before_round):
            return self.cold_start(task_id, pool, ledger)

        scores = {vid: ledger.estimate(vid, task_id, before_round=before_round) for vid in variant_ids}
        best = max(scores.values())
        tied = [vid for vid in variant_ids if scores[vid] == best]
        if len(tied) == 1:
            return tied[0]
        return self._break_tie(tied, task_id, ledger)

    def cold_start(self, task_id: str, pool: VariantPool, ledger: SuccessLedger | None = None) -> str:
        """Route a task with no usable prior evidence.

        Ours (report §5 gap 3 — the paper gives no round-0 rule). With a single
        variant the answer is V0 by construction; otherwise send it to the
        variant with the highest overall success rate, which is the rule the
        paper does give for unseen tasks at deployment time (§7.5 p.22,
        "highest overall success rate on the evolution set"). Ties go to the
        lowest id so the choice is reproducible.

        ``ledger`` extends the SPEC signature so the rollup is available; the
        bare two-argument call (as in :meth:`VariantPool.reassign`) falls back
        to V0. Reading the rollup is freeze-safe: during a freeze the ledger
        provably holds nothing from the current round.
        """
        variant_ids = sorted(pool.variants)
        if not variant_ids:
            raise RuntimeError("cannot cold-start route on an empty pool")
        if len(variant_ids) == 1 or ledger is None:
            return variant_ids[0]
        return max(variant_ids, key=ledger.variant_rollup)

    def explore(self, variant_ids: Iterable[str]) -> str | None:
        """Optional epsilon-greedy escape hatch; ``None`` means "use argmax".

        Ours, default off (``epsilon = 0``, SPEC §6.6). Its purpose is to stop a
        variant that lost early from being frozen out by argmax forever. The
        draw comes from a seeded RNG and never consults the ledger, so turning
        it on does not weaken the routing freeze.
        """
        if self.epsilon <= 0.0:
            return None
        candidates = sorted(variant_ids)
        if not candidates:
            return None
        if self._rng.random() < self.epsilon:
            return self._rng.choice(candidates)
        return None

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------

    @staticmethod
    def _has_prior_evidence(
        task_id: str,
        pool: VariantPool,
        ledger: SuccessLedger,
        before_round: int | None,
    ) -> bool:
        """Does any variant hold usable pre-``before_round`` evidence for this task?

        Checked on the cells themselves rather than by comparing estimates to
        the prior, so a cell that genuinely estimates to 0.5 is not mistaken
        for an absent one.
        """
        for variant_id in pool.variants:
            cell = ledger.cell(variant_id, task_id)
            if cell is None or cell.attempts == 0:
                continue
            if before_round is None or cell.last_round < before_round:
                return True
        return False

    def _break_tie(self, tied: list[str], task_id: str, ledger: SuccessLedger) -> str:
        if self.tie_break == "fewest_attempts":
            return min(tied, key=lambda vid: (ledger.attempts_on(vid, task_id), vid))
        if self.tie_break == "smallest_id":
            return min(tied)
        return self._rng.choice(sorted(tied))
