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
Report §5.1 finds three readings that the paper uses interchangeably and never
bridges. The default remains **(a) the routing-induced partition**:
``cluster(x)`` is the variant that carried ``x`` in the previous frozen
partition. Unlike the original stage-A implementation, routing now actually
aggregates every task in that cluster and scores a true
``(variant, cluster)`` cell.

Callers can inject an authoritative ``task_id -> cluster_id`` mapping. This is
the stable seam for failure-mode or level/domain clusters; an unmapped task is
an *unknown cluster* and takes deterministic cold start rather than silently
falling back to task-level evidence. Without a mapping, ``cluster_mode='routed'``
is explicitly the routing-induced engineering choice. Other cluster modes need
the mapping because the paper publishes no clustering algorithm.

The old per-task tournament remains available as
``routing_mode='task_tournament'``. It is a compatibility/ablation arm, not the
paper-faithful default: calling a task a cluster without aggregation was the
bug this module now avoids.

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

#: Cluster readings of report §5.1. ``failure`` and ``level`` are usable when
#: the caller injects assignments; the paper does not publish their algorithm.
CLUSTER_MODES = ("routed", "failure", "level")

#: Estimator scope. ``cluster`` implements the paper's formula;
#: ``task_tournament`` preserves the former per-task behavior for compatibility.
ROUTING_MODES = ("cluster", "task_tournament")

#: Tie-break policies for equal estimates (SPEC §6.6). Default: fewest attempts
#: in that cluster, which spends ties on information rather than incumbency.
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
        routing_mode: str = "cluster",
        task_to_cluster: Mapping[str, str | int] | None = None,
        tie_break: str = "fewest_attempts",
        epsilon: float = 0.0,
        seed: int = 0,
        window: int | None = None,
    ) -> None:
        if cluster_mode not in CLUSTER_MODES:
            raise ValueError(f"cluster_mode must be one of {CLUSTER_MODES}, got {cluster_mode!r}")
        if routing_mode not in ROUTING_MODES:
            raise ValueError(f"routing_mode must be one of {ROUTING_MODES}, got {routing_mode!r}")
        if tie_break not in TIE_BREAKS:
            raise ValueError(f"tie_break must be one of {TIE_BREAKS}, got {tie_break!r}")
        if not 0.0 <= epsilon <= 1.0:
            raise ValueError(f"epsilon must be in [0, 1], got {epsilon}")
        if window is not None and window < 1:
            raise ValueError(f"window must be >= 1, got {window}")
        assignments = (
            None
            if task_to_cluster is None
            else {task_id: str(cluster_id) for task_id, cluster_id in task_to_cluster.items()}
        )
        if assignments is not None:
            invalid = sorted(
                repr(task_id)
                for task_id, cluster_id in assignments.items()
                if not isinstance(task_id, str)
                or not task_id
                or not cluster_id
            )
            if invalid:
                raise ValueError(f"task_to_cluster requires non-empty string ids; invalid tasks: {invalid}")
        self.cluster_mode = cluster_mode
        self.routing_mode = routing_mode
        self.task_to_cluster = None if assignments is None else MappingProxyType(assignments)
        self.tie_break = tie_break
        self.epsilon = epsilon
        self.window = window
        self._rng = random.Random(seed)

    # ------------------------------------------------------------------
    # W7 — cluster
    # ------------------------------------------------------------------

    def cluster_of(self, task_id: str, pool: VariantPool) -> str | None:
        """Return the stable cluster id, or ``None`` when it is unknown.

        An injected mapping is authoritative: missing entries do not borrow a
        carrier or masquerade as singleton clusters. Without a mapping, the
        only implemented interpretation is the explicitly named
        routing-induced partition.
        """
        if self.task_to_cluster is not None:
            return self.task_to_cluster.get(task_id)
        if self.cluster_mode != "routed":
            raise NotImplementedError(
                f"cluster_mode {self.cluster_mode!r} requires an injected "
                "task_to_cluster mapping; the paper publishes no clustering algorithm"
            )
        return pool.carrier_of(task_id)

    def tasks_in_cluster(self, cluster_id: str, pool: VariantPool) -> frozenset[str]:
        """Tasks belonging to ``cluster_id`` under the configured assignment."""
        if self.task_to_cluster is not None:
            return frozenset(
                task_id for task_id, assigned in self.task_to_cluster.items() if assigned == cluster_id
            )
        if self.cluster_mode == "routed" and cluster_id in pool.variants:
            return frozenset(pool.variants[cluster_id].routed_tasks)
        return frozenset()

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
        # Sorting makes seeded exploration/random tie-breaks reproducible even
        # when the caller supplies a set.
        frozen = {
            task_id: self.route(task_id, pool, ledger, before_round=round_idx)
            for task_id in sorted(set(tasks))
        }
        return MappingProxyType(frozen)

    # ------------------------------------------------------------------
    # W2 — argmax routing
    # ------------------------------------------------------------------

    def route(self, task_id: str, pool: VariantPool, ledger: SuccessLedger, *, before_round: int) -> str:
        """Route on evidence strictly before ``before_round``.

        The default is the paper's
        ``argmax_v S_hat(v, cluster(task))``. ``task_tournament`` instead uses
        the legacy singleton ``{task}``. ``before_round`` is required, not
        optional: an unfrozen estimate is never the right thing to route on.
        """
        variant_ids = sorted(pool.variants)
        if not variant_ids:
            raise RuntimeError("cannot route on an empty pool")

        if self.routing_mode == "task_tournament":
            evidence_tasks = frozenset({task_id})
        else:
            cluster_id = self.cluster_of(task_id, pool)
            if cluster_id is None:
                return self.cold_start(task_id, pool, ledger, before_round=before_round)
            evidence_tasks = self.tasks_in_cluster(cluster_id, pool)
            if not evidence_tasks:
                return self.cold_start(task_id, pool, ledger, before_round=before_round)

        if not self._has_prior_evidence(evidence_tasks, pool, ledger, before_round):
            return self.cold_start(task_id, pool, ledger, before_round=before_round)

        explored = self.explore(variant_ids)
        if explored is not None:
            return explored

        scores = {
            vid: ledger.estimate_cluster(
                vid,
                evidence_tasks,
                before_round=before_round,
                window=self.window,
            )
            for vid in variant_ids
        }
        best = max(scores.values())
        tied = [vid for vid in variant_ids if scores[vid] == best]
        if len(tied) == 1:
            return tied[0]
        return self._break_tie(tied, evidence_tasks, ledger, before_round)

    def cold_start(
        self,
        task_id: str,
        pool: VariantPool,
        ledger: SuccessLedger | None = None,
        *,
        before_round: int | None = None,
    ) -> str:
        """Route a task with no usable prior evidence.

        Ours (report §5 gap 3 — the paper gives no round-0 rule). With a single
        variant the answer is V0 by construction; otherwise send it to the
        variant with the highest overall success rate, which is the rule the
        paper does give for unseen tasks at deployment time (§7.5 p.22,
        "highest overall success rate on the evolution set"). Ties go to the
        lowest id so the choice is reproducible.

        ``ledger`` extends the SPEC signature so the rollup is available; the
        bare two-argument call (as in :meth:`VariantPool.reassign`) falls back
        to V0. ``before_round`` also makes direct calls freeze-safe: current or
        future cells cannot leak through this fallback branch.
        """
        variant_ids = sorted(pool.variants)
        if not variant_ids:
            raise RuntimeError("cannot cold-start route on an empty pool")
        if len(variant_ids) == 1 or ledger is None:
            return variant_ids[0]
        return max(
            variant_ids,
            key=lambda variant_id: ledger.variant_rollup(
                variant_id,
                before_round=before_round,
                window=self.window,
            ),
        )

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

    def _has_prior_evidence(
        self,
        task_ids: Iterable[str],
        pool: VariantPool,
        ledger: SuccessLedger,
        before_round: int,
    ) -> bool:
        """Does any variant hold usable evidence for this cluster/task?

        Checked from aggregate denominators rather than by comparing estimates
        to the prior, so a genuine 0.5 estimate is not mistaken for absence.
        """
        for variant_id in pool.variants:
            if ledger.attempts_on_cluster(
                variant_id,
                task_ids,
                before_round=before_round,
                window=self.window,
            ):
                return True
        return False

    def _break_tie(
        self,
        tied: list[str],
        task_ids: Iterable[str],
        ledger: SuccessLedger,
        before_round: int,
    ) -> str:
        if self.tie_break == "fewest_attempts":
            return min(
                tied,
                key=lambda vid: (
                    ledger.attempts_on_cluster(
                        vid,
                        task_ids,
                        before_round=before_round,
                        window=self.window,
                    ),
                    vid,
                ),
            )
        if self.tie_break == "smallest_id":
            return min(tied)
        return self._rng.choice(sorted(tied))
