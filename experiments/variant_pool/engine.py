# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""C1 — the variant-pool evolution engine.

This is the first module that makes the pool *move*: it drives one round of the
adaptation loop of Algorithm 1 (report §3.2, paper p.9) under variant isolation
(§4.5 p.11), and chains rounds into a run with early stopping. Batch A built the
static parts — pool, ledger, router, gate — and tested each in isolation; this
module is where routing evolves with the scoreboard, forks branch a conflicted
edit, a full pool retires its weakest variant, and a run stops when it goes idle.

The two callbacks stay injected
-------------------------------
Real rollouts and real ``meta_agent.evolve`` are batch C. Here ``evaluate`` and
``evolve`` are constructor callbacks so the whole engine is exercised offline
with deterministic stubs (SPEC §8.3/§8.4):

* ``evolve(variant, round_idx) -> candidate | None`` — the round's proposed edit
  for one variant, carrying its own ``target_variant`` when it is a
  :class:`.manifest.ChangeManifest`; ``None`` means "no candidate this round".
* ``evaluate(candidate, T_k, round_idx) -> {task: (n_pass, n_att)}`` — the
  candidate's pass@2 outcome on **only** the tasks routed to its variant
  (§4.5 "tested only against tasks routed to k").

One round, in order (SPEC §8.4)
-------------------------------
1. ``router.freeze_routing`` — freeze the routing from *prior-round* evidence
   only, **before** any rollout. This is the correctness core (SPEC §6.2): if
   routing could see the round it is routing, "send the task to whoever solves
   it" would be a disguised oracle and M1-vs-M0 would be self-deceiving. The
   engine enforces it structurally — freeze is the first thing it does and the
   ledger is written only at step 5 — and the router enforces it again by
   refusing to run when the ledger already holds this round.
2. For each variant with a non-empty ``T_k``: ``evolve`` it, and if a candidate
   comes back, ``evaluate`` that candidate on ``T_k`` (the narrowed evaluation).
3. ``gate`` the candidate: APPLY / FORK / REJECT.
4. Apply the decision to the pool: APPLY merges into the variant; FORK branches a
   new variant (retiring the weakest first if the pool is full, then re-routing
   the orphans); REJECT leaves the variant and archives the reason.
5. Record this round's kept results into the ledger — for the *next* round only.
6. Update the global idle counter; a run stops once it reaches ``patience``.
7. Emit a :class:`RoundResult`, and a per-task digest if an evidence store was
   given.

K = 1 is a single lineage
-------------------------
With one variant the frozen routing is always ``V0``, ``T_k`` is the whole task
set, and the round is exactly "evolve V0 once, evaluate once, let the gate
apply or reject". A FORK can never happen: forking a full one-variant pool would
have to retire ``V0`` to branch a child of ``V0``, which is degenerate, so the
engine downgrades that FORK to REJECT (there is no other variant to retire). The
K=1 regression test checks this equivalence against a hand-computed single
lineage, and that :meth:`VariantPool.fork` is never called.

What "before" means here, and one C1 simplification
---------------------------------------------------
The gate classifies a task as *improved* only if the variant did not already
solve it. That before-state is read from the ledger's running totals: a variant
that has ever recorded a pass on a task is treated as already solving it. This
is the same whole-cell coarseness the ledger documents (running totals keep no
per-round history), and it is recorded here as ours. Regression is judged
against the full-history solved set inside the gate (W21), independent of this
before-state. Real config merging on APPLY is batch C; C1 records the outcome
and marks the ship without touching harness files.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable

from .gate import Decision, GateResult, TaskEval, run_gate

if TYPE_CHECKING:  # pragma: no cover - typing only, no runtime import cycle
    from .evidence import EvidenceStore
    from .ledger import SuccessLedger
    from .pool import VariantPool
    from .router import Router

#: Default early-stop patience P (Table 8 / §6.1: 15 rounds, patience 3).
DEFAULT_PATIENCE = 3

#: Default fork minimum ``(improved, regressed)`` sizes (SPEC §6.6), mirroring
#: :data:`.gate.DEFAULT_MIN_FORK`. Kept as its own constant so the engine's
#: default is visible without importing the gate's.
DEFAULT_MIN_FORK = (2, 2)

#: pass@2 attempt count used to encode the ledger-derived before-state of a task
#: (SPEC §6.1 p.15). ``(2, 2)`` = the variant already solves it, ``(0, 2)`` = it
#: does not; only ``before_passes == 0`` matters to the gate's improved check.
_PASS_AT = 2


@dataclass
class RoundResult:
    """What one round of :meth:`VariantPoolEngine.run_round` did.

    ``per_variant_pass`` is keyed by the *evolved* variant (the one the round's
    candidate targeted) and holds that candidate's pass@2 outcome on ``T_k`` —
    the round's measurement, recorded whatever the gate decided, so a rejected
    round still has a curve point. ``decisions`` carries only the variants a
    candidate was actually gated for; a variant skipped for an empty ``T_k`` or
    a ``None`` candidate has no entry. ``forked`` / ``retired`` are the pool
    mutations, ``shipped`` is true iff any variant applied or forked, and
    ``idle`` is the global counter *after* this round.
    """

    round_idx: int
    variant_count: int
    decisions: dict[str, Decision] = field(default_factory=dict)
    per_variant_pass: dict[str, dict[str, tuple[int, int]]] = field(default_factory=dict)
    forked: list[str] = field(default_factory=list)
    retired: list[str] = field(default_factory=list)
    shipped: bool = False
    idle: int = 0


class VariantPoolEngine:
    """Drives the variant pool one round at a time (Algorithm 1 + §4.5).

    The engine owns no success data of its own: routing reads the ledger,
    ranking retirement uses :meth:`SuccessLedger.variant_rollup`, and the gate
    owns the ship decision. It owns only the *sequencing* — the order that keeps
    the routing freeze honest — and the global idle counter.
    """

    def __init__(
        self,
        pool: VariantPool,
        ledger: SuccessLedger,
        router: Router,
        *,
        evaluate: Callable[[Any, set[str], int], Mapping[str, tuple[int, int]]],
        evolve: Callable[[Any, int], Any],
        gate: Callable[..., GateResult] = run_gate,
        evidence: EvidenceStore | None = None,
        patience: int = DEFAULT_PATIENCE,
        min_fork: tuple[int, int] = DEFAULT_MIN_FORK,
    ) -> None:
        if patience < 1:
            raise ValueError(f"patience must be >= 1, got {patience}")
        if len(min_fork) != 2 or min_fork[0] < 1 or min_fork[1] < 1:
            raise ValueError(f"min_fork must be a pair of positive ints, got {min_fork!r}")
        self.pool = pool
        self.ledger = ledger
        self.router = router
        self.evaluate = evaluate
        self.evolve = evolve
        self.gate = gate
        self.evidence = evidence
        self.patience = patience
        self.min_fork = tuple(min_fork)
        #: Global idle counter (SPEC §6.6: single idle, aligned with Algorithm 1).
        self._idle = 0

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------

    @property
    def idle(self) -> int:
        """Consecutive rounds since the last ship (reset to 0 on any ship)."""
        return self._idle

    def run(self, tasks: set[str], num_rounds: int) -> list[RoundResult]:
        """Run up to ``num_rounds`` rounds, stopping early on ``idle >= patience``.

        Rounds are numbered from 0 so the round index and the ledger's
        ``last_round`` stay in step, which is what the routing-freeze guard
        checks. The loop stops the moment the idle counter reaches ``patience``
        (Algorithm 1's ``idle >= P -> break``); the returned list holds every
        round that actually ran.
        """
        if num_rounds < 0:
            raise ValueError(f"num_rounds must be >= 0, got {num_rounds}")
        results: list[RoundResult] = []
        for round_idx in range(num_rounds):
            results.append(self.run_round(round_idx, set(tasks)))
            if self._idle >= self.patience:
                break
        return results

    def run_round(self, round_idx: int, tasks: set[str]) -> RoundResult:
        """Execute one round of the loop (SPEC §8.4), in the fixed order above."""
        # 1. Freeze the routing before any rollout, from prior-round evidence
        #    only. freeze_routing raises RoutingFreezeError if the ledger already
        #    holds round_idx, so a leak is caught rather than silently oracling.
        frozen = self.router.freeze_routing(tasks, self.pool, self.ledger, round_idx)
        # adopt the frozen partition so fork/retire/reassign see this round's
        # routing (a pool write, never a ledger write -> freeze stays intact).
        self.pool.apply_routing(dict(frozen))

        result = RoundResult(round_idx=round_idx, variant_count=len(self.pool))
        # snapshot the variants present at freeze time: a variant forked or
        # retired mid-round is not itself evolved this round.
        for variant_id in self._variants_at_freeze(frozen):
            if variant_id not in self.pool.variants:
                continue  # retired earlier in this same round
            t_k = {task for task, carrier in frozen.items() if carrier == variant_id}
            if not t_k:
                continue  # 2. empty cluster -> nothing to evolve or evaluate

            variant = self.pool.variants[variant_id]
            candidate = self.evolve(variant, round_idx)
            if candidate is None:
                continue  # no candidate this round -> contributes to idle only

            # 2. narrowed evaluation: the candidate is measured on T_k alone.
            tk_eval = self.evaluate(candidate, set(t_k), round_idx)
            self._require_full_coverage(variant_id, t_k, tk_eval)
            result.per_variant_pass[variant_id] = {task: tuple(tk_eval[task]) for task in t_k}

            # 3. gate the candidate on T_k.
            tk_results = [self._task_eval(variant_id, task, tk_eval[task]) for task in sorted(t_k)]
            gate_result = self.gate(
                candidate,
                variant.config_path,
                self.ledger,
                tk_results,
                min_fork=self.min_fork,
            )

            # 4. apply the decision to the pool.
            decision = self._settle(variant_id, candidate, gate_result, tk_eval, round_idx, result)
            result.decisions[variant_id] = decision
            if decision in (Decision.APPLY, Decision.FORK):
                result.shipped = True

        # 6. idle: reset on any ship, otherwise advance one.
        self._idle = 0 if result.shipped else self._idle + 1
        result.idle = self._idle
        result.variant_count = len(self.pool)
        return result

    # ------------------------------------------------------------------
    # decision handling
    # ------------------------------------------------------------------

    def _settle(
        self,
        variant_id: str,
        candidate: Any,
        gate_result: GateResult,
        tk_eval: Mapping[str, tuple[int, int]],
        round_idx: int,
        result: RoundResult,
    ) -> Decision:
        """Apply a gate outcome to the pool and ledger; return the final decision.

        The returned decision can differ from ``gate_result.decision`` only in
        one case: a FORK the pool cannot honour (no variant other than the fork
        parent to retire) is downgraded to REJECT, which is what makes a K=1 pool
        provably fork-free.
        """
        decision = gate_result.decision

        if decision is Decision.APPLY:
            # 5. the variant now embodies the candidate -> record its results.
            self._record(variant_id, tk_eval, round_idx)
            self._apply_candidate(self.pool.variants[variant_id], candidate)
            return Decision.APPLY

        if decision is Decision.FORK:
            forked = self._fork(variant_id, gate_result, tk_eval, round_idx, result)
            if forked is not None:
                return Decision.FORK
            # could not fork -> fall through and archive as a rejection.
            self._archive_rejection(variant_id, candidate, gate_result, round_idx, downgraded=True)
            return Decision.REJECT

        # REJECT: the variant is untouched; nothing is recorded (the ledger must
        # keep reflecting the variant's real, unshipped state), only archived.
        self._archive_rejection(variant_id, candidate, gate_result, round_idx, downgraded=False)
        return Decision.REJECT

    def _fork(
        self,
        parent_id: str,
        gate_result: GateResult,
        tk_eval: Mapping[str, tuple[int, int]],
        round_idx: int,
        result: RoundResult,
    ) -> str | None:
        """Branch a new variant for the improved tasks; ``None`` if impossible.

        A full pool first retires its weakest variant *other than the parent*
        (paper §4.5: "retiring the lowest-performing variant if the pool is
        full") and re-routes that variant's orphaned tasks under the routing
        freeze. If the parent is the only variant (a full K=1 pool), there is
        nothing to retire and the fork cannot proceed.
        """
        if self.pool.is_full():
            retiree = self._weakest_other(parent_id)
            if retiree is None:
                return None
            orphans = self.pool.retire(retiree)
            result.retired.append(retiree)
            if orphans:
                self.pool.reassign(orphans, self.router, self.ledger, before_round=round_idx)

        child = self.pool.fork(parent_id, gate_result.improved, round_idx)
        result.forked.append(child.variant_id)
        # 5. the new variant serves exactly the tasks it improved -> record those
        #    outcomes under it. The parent is unchanged, so its tasks are not
        #    re-recorded (that would credit the parent with the candidate's run).
        improved_eval = {task: tk_eval[task] for task in gate_result.improved if task in tk_eval}
        self._record(child.variant_id, improved_eval, round_idx)
        return child.variant_id

    def _weakest_other(self, keep_id: str) -> str | None:
        """Lowest-rollup variant that is not ``keep_id`` (ties: lowest id)."""
        others = sorted(vid for vid in self.pool.variants if vid != keep_id)
        if not others:
            return None
        return min(others, key=self.ledger.variant_rollup)

    def _apply_candidate(self, variant: Any, candidate: Any) -> None:
        """Merge an applied candidate into its variant (C1: outcome only).

        Real config/asset merging is batch C, which owns HarnessConfig
        serialisation (SPEC §5). C1 keeps the placeholder ``config_path`` and
        leaves the on-disk harness untouched; the ship is already recorded in the
        ledger and reported in the :class:`RoundResult`. Kept as a seam so batch
        C can fill it in without changing the control flow.
        """
        return None

    # ------------------------------------------------------------------
    # ledger + evidence
    # ------------------------------------------------------------------

    def _record(self, variant_id: str, tk_eval: Mapping[str, tuple[int, int]], round_idx: int) -> None:
        """Fold this round's kept results into the ledger, for the next round."""
        for task_id, (n_pass, n_att) in tk_eval.items():
            self.ledger.record(variant_id, task_id, n_pass, n_att, round_idx)
            self._write_digest(variant_id, task_id, (n_pass, n_att), round_idx)

    def _write_digest(
        self, variant_id: str, task_id: str, outcome: tuple[int, int], round_idx: int
    ) -> None:
        if self.evidence is None:
            return
        from .evidence import TaskDigest  # local: keep evidence optional

        digest = TaskDigest(
            task_id=task_id,
            round_idx=round_idx,
            variant_id=variant_id,
            outcome=(int(outcome[0]), int(outcome[1])),
        )
        self.evidence.append_digest(self.evidence.attach_prior_history(digest))

    def _archive_rejection(
        self, variant_id: str, candidate: Any, gate_result: GateResult, round_idx: int, *, downgraded: bool
    ) -> None:
        if self.evidence is None:
            return
        from .evidence import RejectedCandidate  # local: keep evidence optional

        candidate_id = getattr(candidate, "candidate_id", "") or f"C-R{round_idx}-{variant_id}"
        failed_stage = gate_result.failed_stage.name if gate_result.failed_stage is not None else "SEESAW_REGRESSION"
        reason = gate_result.archive_reason
        if downgraded:
            reason = f"FORK downgraded: no retireable variant besides {variant_id}; {reason}"
        self.evidence.append_rejected(
            RejectedCandidate(
                candidate_id=str(candidate_id),
                round_idx=round_idx,
                variant_id=variant_id,
                failed_stage=failed_stage,
                archive_reason=reason,
            )
        )

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    def _task_eval(self, variant_id: str, task_id: str, after: tuple[int, int]) -> TaskEval:
        """Build the gate's per-task before/after pair.

        The before-state is the variant's ledger view entering this round: a
        variant that has ever recorded a pass on the task is treated as already
        solving it (before = ``(2, 2)``), otherwise not (``(0, 2)``). Only
        ``before_passes == 0`` matters to the gate's improved check; regression
        is judged separately against the full-history solved set (W21).
        """
        cell = self.ledger.cell(variant_id, task_id)
        before_solved = cell is not None and cell.passes >= 1
        before = (_PASS_AT, _PASS_AT) if before_solved else (0, _PASS_AT)
        return TaskEval(task_id, before=before, after=(int(after[0]), int(after[1])))

    @staticmethod
    def _variants_at_freeze(frozen: Mapping[str, str]) -> list[str]:
        """Distinct variants that carry at least one task in the frozen routing."""
        return sorted(set(frozen.values()))

    @staticmethod
    def _require_full_coverage(
        variant_id: str, t_k: Iterable[str], tk_eval: Mapping[str, tuple[int, int]]
    ) -> None:
        missing = sorted(task for task in t_k if task not in tk_eval)
        if missing:
            raise ValueError(
                f"evaluate() for variant {variant_id!r} did not return outcomes for {missing}; "
                "the narrowed evaluation must cover all of T_k"
            )
