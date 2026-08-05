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

* ``evolve(variant, round_idx) -> candidate | Sequence[candidate] | None``
  returns one proposed edit or a Critic-ranked gate queue for that variant.
  ``None`` or an empty sequence means "no candidate this round". Queue order is
  preserved; under the default ``first_wins`` policy the first deterministic
  APPLY/FORK wins, while under ``bucket_disjoint`` (M-17, App B.1) the queue is
  scanned on and every bucket-disjoint ship is kept.
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
2. For each variant with a non-empty ``T_k``: ``evolve`` it, and evaluate its
   ranked candidates on ``T_k`` in the supplied order (the narrowed evaluation).
3. ``gate`` each attempted candidate: a REJECT is archived and advances the
   queue. Under ``first_wins`` the first APPLY/FORK is selected and the rest are
   skipped; under ``bucket_disjoint`` every APPLY/FORK whose edit bucket is not
   already claimed this round is selected (a second APPLY to an already-applied
   variant is skipped as an unreconstructable whole-config merge, M-17).
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

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable

from .gate import (
    REGRESSION_BASELINE_GLOBAL,
    REGRESSION_BASELINE_MODES,
    Decision,
    GateResult,
    GateStage,
    TaskEval,
    _classify,
    _decide,
    run_gate,
)
from .ledger import ROLLUP_MODES

if TYPE_CHECKING:  # pragma: no cover - typing only, no runtime import cycle
    from .evidence import EvidenceStore
    from .ledger import SuccessLedger
    from .pool import VariantPool
    from .router import Router

#: Default early-stop patience P (Table 8 / §6.1: 15 rounds, patience 3).
DEFAULT_PATIENCE = 3

#: Paper-faithful fork minimum, mirroring :data:`.gate.DEFAULT_MIN_FORK`.
#: ``(2, 2)`` is still accepted as an explicit anti-noise ablation.
DEFAULT_MIN_FORK = (1, 1)

#: Defensive bound for one ``evolve`` return value. This is deliberately not
#: the paper's global per-round K_t: CandidatePipeline/recipe owns that cap.
#: The engine bound only prevents a custom callback from returning an
#: unexpectedly large per-variant queue.
DEFAULT_MAX_CANDIDATES = 4

#: Round-settlement ship policies (M-17, SPEC §7.14).
#:
#: * ``first_wins`` (default) — Algorithm 1 (paper p.9): the first deterministic
#:   APPLY/FORK in a variant's ranked queue ships and the rest are skipped. This
#:   is byte-identical to the pre-M-17 engine.
#: * ``bucket_disjoint`` — App B.1 (paper p.34): ship every ranked candidate in
#:   order, skipping any whose edit *bucket* was already claimed by an
#:   earlier-ranked ship this round, so bucket-disjoint candidates on different
#:   settlement targets can ship together. A second APPLY to a variant already
#:   applied this round is skipped as unreconstructable (see
#:   :meth:`VariantPoolEngine._multiship_skip_reason`): our candidates are whole
#:   ``config.yaml`` files, not diffs, and no sound field-level merge of two
#:   whole configs exists in the repo (M-17 Phase-1 finding).
SHIP_POLICIES = ("first_wins", "bucket_disjoint")

#: --ship-confirmation modes (the pre-ship full-bed gate).
#:
#: The problem this fixes: candidates are gated on the *routed window* ``T_k``
#: (~12 of 103 tasks in e_pervar3), so an APPLY/FORK the window renders is blind
#: to what the edit does to the ~90 tasks routed elsewhere. Improvements or
#: regressions off-window are structurally invisible at decision time.
#:
#: * ``off`` (default) — byte-identical: the window seesaw's APPLY/FORK/REJECT is
#:   enacted directly, exactly as before this feature. No full-bed evaluation, no
#:   extra counters, no config/lock/report change. This is a hard guarantee,
#:   mirrored on every emitted field (nothing new is written when ``off``).
#: * ``full_bed`` — the window seesaw becomes a *cheap pre-filter*: any candidate
#:   about to APPLY or FORK is first re-evaluated on the **whole** task bed (the
#:   run's complete task list, same pass-k structure and the same
#:   candidate-evaluation path the window used, only parameterised by task list),
#:   re-classified with :func:`gate._classify` against the same
#:   ledger/regression-baseline, and re-decided with :func:`gate._decide` under
#:   the same ``min_fork``. **The full-bed decision REPLACES the window
#:   decision**: APPLY/FORK on the full bed is enacted; REJECT on the full bed
#:   overturns the window ship and the candidate is archived under
#:   :attr:`GateStage.SHIP_CONFIRM`. A candidate the window already REJECTS is
#:   *never* confirmed — rejects stay window-only, so no extra cost is spent on
#:   anything that was not about to ship. The window is the pre-filter; the full
#:   bed is the verdict.
#:
#: Modes ``next_round`` and ``eprocess`` are reserved by STATPOOL-DESIGN.md
#: §6.2/§8 for a *post*-ship probation mechanism (provisional ship -> next
#: round's fresh measurement -> rollback if a PROTECTED task drops) and are NOT
#: implemented here; ``full_bed`` is the pre-ship, candidate-scope confirmation.
SHIP_CONFIRMATION_OFF = "off"
SHIP_CONFIRMATION_FULL_BED = "full_bed"
SHIP_CONFIRMATION_MODES = (SHIP_CONFIRMATION_OFF, SHIP_CONFIRMATION_FULL_BED)
DEFAULT_SHIP_CONFIRMATION = SHIP_CONFIRMATION_OFF

#: The evaluate-callback ``phase`` the engine passes on a full-bed confirmation
#: call (and never on a window call). It lets a recipe route confirmation
#: rollouts into their own accounting bucket without forking a second evaluator.
SHIP_CONFIRM_PHASE = "confirm"

#: pass@2 attempt count used to encode the ledger-derived before-state of a task
#: (SPEC §6.1 p.15). ``(2, 2)`` = the variant already solves it, ``(0, 2)`` = it
#: does not; only ``before_passes == 0`` matters to the gate's improved check.
_PASS_AT = 2


@dataclass
class CandidateDiagnostic:
    """Per-candidate audit emitted by :class:`RoundResult`.

    ``decision`` is the deterministic gate decision (or ``None`` when an
    earlier gate stage failed / the candidate was skipped). ``evaluation`` is
    always scoped to the target variant's frozen ``T_k``.
    """

    candidate_id: str
    variant_id: str
    evaluation: dict[str, tuple[int, int]] = field(default_factory=dict)
    decision: Decision | None = None
    failed_stage: GateStage | None = None
    archive_reason: str = ""
    skipped_reason: str | None = None
    #: Set only under ``--ship-confirmation full_bed`` when a window APPLY/FORK
    #: was re-classified on the whole bed: ``{mode, window_decision,
    #: full_decision, window_improved, window_regressed, full_improved,
    #: full_regressed}``. ``None`` (the default) whenever confirmation did not run
    #: — so an ``off`` run leaves this field untouched and every reader that only
    #: emits it when non-``None`` stays byte-identical. ``evaluation`` always
    #: stays the window ``T_k`` measurement; the confirmation's own rollouts are
    #: accounted separately by the recipe, never folded in here.
    ship_confirm: dict[str, Any] | None = None


@dataclass
class RoundResult:
    """What one round of :meth:`VariantPoolEngine.run_round` did.

    ``per_variant_pass`` preserves the legacy curve API: it holds the selected
    candidate's scoped pass@2 outcome, or the final attempted outcome when the
    whole queue rejects. ``candidate_diagnostics`` is the lossless audit keyed
    by candidate id, including gate failures and skip reasons.
    ``selected_candidate_ids`` names only candidates admitted to settlement;
    ``no_candidate_variants`` distinguishes an explicit no-candidate result
    from an empty routing cluster. ``decisions`` remains variant-keyed for
    compatibility. ``forked`` / ``retired`` are pool mutations, ``shipped`` is
    true iff any variant applied or forked, and ``idle`` is the global counter
    after this round.
    """

    round_idx: int
    variant_count: int
    decisions: dict[str, Decision] = field(default_factory=dict)
    per_variant_pass: dict[str, dict[str, tuple[int, int]]] = field(default_factory=dict)
    candidate_diagnostics: dict[str, CandidateDiagnostic] = field(default_factory=dict)
    selected_candidate_ids: dict[str, str] = field(default_factory=dict)
    no_candidate_variants: list[str] = field(default_factory=list)
    forked: list[str] = field(default_factory=list)
    retired: list[str] = field(default_factory=list)
    shipped: bool = False
    idle: int = 0


@dataclass(frozen=True)
class _PendingCandidate:
    """A fully evaluated/gated candidate awaiting round settlement."""

    variant_id: str
    candidate_id: str
    candidate: Any
    gate_result: GateResult
    tk_eval: Mapping[str, tuple[int, int]]


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
        # A full-bed confirmation reuses this same callback, only parameterised by
        # task list and marked with ``phase=SHIP_CONFIRM_PHASE`` (a keyword the
        # window call never passes), so the callback stays a single evaluator.
        evaluate: Callable[..., Mapping[str, tuple[int, int]]],
        evolve: Callable[[Any, int], Any],
        gate: Callable[..., GateResult] = run_gate,
        evidence: EvidenceStore | None = None,
        patience: int = DEFAULT_PATIENCE,
        min_fork: tuple[int, int] = DEFAULT_MIN_FORK,
        retirement_metric: str = "task_macro",
        max_candidates_per_variant: int = DEFAULT_MAX_CANDIDATES,
        record_selected_results: bool = True,
        ship_policy: str = "first_wins",
        regression_baseline: str = REGRESSION_BASELINE_GLOBAL,
        ship_confirmation: str = DEFAULT_SHIP_CONFIRMATION,
    ) -> None:
        if patience < 1:
            raise ValueError(f"patience must be >= 1, got {patience}")
        if len(min_fork) != 2 or min_fork[0] < 1 or min_fork[1] < 1:
            raise ValueError(f"min_fork must be a pair of positive ints, got {min_fork!r}")
        if retirement_metric not in ROLLUP_MODES:
            raise ValueError(
                f"retirement_metric must be one of {ROLLUP_MODES}, got {retirement_metric!r}"
            )
        if max_candidates_per_variant < 1:
            raise ValueError(
                "max_candidates_per_variant must be >= 1, "
                f"got {max_candidates_per_variant}"
            )
        if ship_policy not in SHIP_POLICIES:
            raise ValueError(
                f"ship_policy must be one of {SHIP_POLICIES}, got {ship_policy!r}"
            )
        if regression_baseline not in REGRESSION_BASELINE_MODES:
            raise ValueError(
                f"regression_baseline must be one of {REGRESSION_BASELINE_MODES}, "
                f"got {regression_baseline!r}"
            )
        if ship_confirmation not in SHIP_CONFIRMATION_MODES:
            raise ValueError(
                f"ship_confirmation must be one of {SHIP_CONFIRMATION_MODES}, "
                f"got {ship_confirmation!r}"
            )
        self.pool = pool
        self.ledger = ledger
        self.router = router
        self.evaluate = evaluate
        self.evolve = evolve
        self.gate = gate
        self.evidence = evidence
        self.patience = patience
        self.min_fork = tuple(min_fork)
        self.retirement_metric = retirement_metric
        self.max_candidates_per_variant = max_candidates_per_variant
        # Paper-mode recipes score the settled active pool on the full task set
        # and therefore suppress these narrower gate measurements. The default
        # preserves the standalone engine's historical ledger behaviour.
        self.record_selected_results = record_selected_results
        #: Round-settlement policy (M-17, SPEC §7.14). ``first_wins`` is the
        #: Algorithm-1 reading and the byte-identical default; ``bucket_disjoint``
        #: is the App B.1 ranked multi-ship arm.
        self.ship_policy = ship_policy
        #: Seesaw regression baseline (M-23). ``global`` (default) anchors on the
        #: cross-variant ever_solved set; ``per_variant`` anchors each candidate on
        #: its own variant history. Forwarded to the gate only when non-default, so
        #: a ``global`` run's gate call stays byte-identical (see :meth:`run_round`).
        self.regression_baseline = regression_baseline
        #: Pre-ship full-bed confirmation (the routed-window blindness fix).
        #: ``off`` (default) is byte-identical — the window seesaw ships directly.
        #: ``full_bed`` re-classifies every window APPLY/FORK on the whole task bed
        #: before it is enacted (see :data:`SHIP_CONFIRMATION_MODES`). Rejects are
        #: never confirmed, so a window REJECT never triggers a full-bed rollout.
        self.ship_confirmation = ship_confirmation
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
        pending: list[_PendingCandidate] = []
        # Round-global multi-ship bookkeeping (ship_policy == "bucket_disjoint").
        # ``claimed_buckets`` holds the edit buckets already taken by a shipped
        # candidate this round; ``applied_variants`` holds variants that already
        # received an APPLY this round (a second APPLY would need a whole-config
        # merge that is not soundly reconstructable — M-17 Phase-1). Both stay
        # empty and unread under the default ``first_wins`` policy.
        claimed_buckets: set[str] = set()
        applied_variants: set[str] = set()

        # Phase 1 — evaluate and gate every freeze-time variant against exactly
        # the same pool/ledger snapshot. No APPLY/FORK/retire/record mutation is
        # allowed here, so variant-id order cannot contaminate another gate.
        for variant_id in self._variants_at_freeze(frozen):
            t_k = {task for task, carrier in frozen.items() if carrier == variant_id}
            if not t_k:
                continue  # 2. empty cluster -> nothing to evolve or evaluate

            variant = self.pool.variants[variant_id]
            evolved = self.evolve(variant, round_idx)
            queue, overflow = self._candidate_queue(evolved)
            if not queue and not overflow:
                result.no_candidate_variants.append(variant_id)
                continue

            indexed = [
                (self._candidate_id(candidate, variant_id, round_idx, index), candidate)
                for index, candidate in enumerate((*queue, *overflow), start=1)
            ]
            admitted = indexed[: len(queue)]
            overflow_indexed = indexed[len(queue) :]
            for candidate_id, _ in overflow_indexed:
                self._add_diagnostic(
                    result,
                    CandidateDiagnostic(
                        candidate_id=candidate_id,
                        variant_id=variant_id,
                        skipped_reason=(
                            "engine defensive candidate limit exceeded: "
                            f"max_candidates_per_variant={self.max_candidates_per_variant}"
                        ),
                    ),
                )

            last_rejected_eval: dict[str, tuple[int, int]] | None = None
            for position, (candidate_id, candidate) in enumerate(admitted):
                # 2. narrowed evaluation: each ranked candidate is measured on
                # the same frozen T_k, in the Critic-provided order.
                tk_eval_raw = self.evaluate(candidate, set(t_k), round_idx)
                self._require_full_coverage(variant_id, t_k, tk_eval_raw)
                tk_eval = {
                    task: tuple(tk_eval_raw[task])
                    for task in sorted(t_k)
                }

                # 3. Critic ranking is only a queue: every candidate still runs
                # the complete deterministic gate.
                tk_results = [
                    self._task_eval(variant_id, task, tk_eval[task])
                    for task in sorted(t_k)
                ]
                gate_kwargs: dict[str, Any] = {"min_fork": self.min_fork}
                # M-23: forward the regression baseline only when it is non-default,
                # so a ``global`` run's gate call — and any injected gate's
                # signature — stays byte-identical to the pre-M-23 recipe.
                if self.regression_baseline != REGRESSION_BASELINE_GLOBAL:
                    gate_kwargs["regression_baseline"] = self.regression_baseline
                gate_result = self.gate(
                    candidate,
                    variant.config_path,
                    self.ledger,
                    tk_results,
                    **gate_kwargs,
                )
                self._add_diagnostic(
                    result,
                    CandidateDiagnostic(
                        candidate_id=candidate_id,
                        variant_id=variant_id,
                        evaluation=tk_eval,
                        decision=gate_result.decision,
                        failed_stage=gate_result.failed_stage,
                        archive_reason=gate_result.archive_reason,
                    ),
                )

                if gate_result.decision in (Decision.APPLY, Decision.FORK):
                    # Under bucket_disjoint, a gate-approved candidate may still be
                    # skipped by the ranked multi-ship rule (App B.1 p.34). Under
                    # first_wins this always returns None, so the block below is the
                    # unchanged Algorithm-1 first-wins settlement.
                    skip_reason = self._multiship_skip_reason(
                        gate_result.decision,
                        variant_id,
                        candidate,
                        claimed_buckets,
                        applied_variants,
                    )
                    if skip_reason is not None:
                        # The gate approved it; the policy did not ship it. Record
                        # the audit reason on the diagnostic already added above and
                        # keep scanning the ranked queue for a later, still-shippable
                        # candidate on an unclaimed bucket.
                        result.candidate_diagnostics[candidate_id].skipped_reason = skip_reason
                        continue

                    # --ship-confirmation full_bed: the window gate is a cheap
                    # pre-filter; nothing ships until the full task bed agrees. Only
                    # candidates that reach here (window APPLY/FORK, not multiship-
                    # skipped) are confirmed — a window REJECT never gets this far, so
                    # rejects stay window-only. ``off`` skips the whole block, which is
                    # why an ``off`` run is byte-identical.
                    if self.ship_confirmation == SHIP_CONFIRMATION_FULL_BED:
                        confirm_result, confirm_meta = self._run_ship_confirmation(
                            candidate, variant_id, tasks, round_idx, gate_result
                        )
                        diagnostic = result.candidate_diagnostics[candidate_id]
                        diagnostic.ship_confirm = confirm_meta
                        if confirm_result.decision is Decision.REJECT:
                            # Full bed overturns the window ship: archive under
                            # SHIP_CONFIRM with both verdicts, enact nothing, and keep
                            # scanning the queue exactly as a plain REJECT would.
                            diagnostic.decision = Decision.REJECT
                            diagnostic.failed_stage = GateStage.SHIP_CONFIRM
                            diagnostic.archive_reason = confirm_result.archive_reason
                            last_rejected_eval = tk_eval
                            result.decisions[variant_id] = Decision.REJECT
                            self._archive_rejection(
                                variant_id,
                                candidate,
                                confirm_result,
                                round_idx,
                                downgraded=False,
                                candidate_id=candidate_id,
                            )
                            continue
                        # Full bed confirms (or flips) the ship: its decision and its
                        # improved/regressed sets REPLACE the window's for settlement.
                        diagnostic.decision = confirm_result.decision
                        diagnostic.archive_reason = confirm_result.archive_reason
                        gate_result = confirm_result

                    if variant_id not in result.selected_candidate_ids:
                        result.per_variant_pass[variant_id] = dict(tk_eval)
                        result.selected_candidate_ids[variant_id] = candidate_id
                    pending.append(
                        _PendingCandidate(
                            variant_id=variant_id,
                            candidate_id=candidate_id,
                            candidate=candidate,
                            gate_result=gate_result,
                            tk_eval=tk_eval,
                        )
                    )
                    # A shipped candidate claims its buckets round-globally; a
                    # shipped APPLY also claims its target variant against a second
                    # (unmergeable) APPLY this round.
                    claimed_buckets.update(self._candidate_buckets(candidate))
                    if gate_result.decision is Decision.APPLY:
                        applied_variants.add(variant_id)
                    if self.ship_policy != "bucket_disjoint":
                        for skipped_id, _ in admitted[position + 1 :]:
                            self._add_diagnostic(
                                result,
                                CandidateDiagnostic(
                                    candidate_id=skipped_id,
                                    variant_id=variant_id,
                                    skipped_reason=(
                                        f"earlier candidate {candidate_id} selected "
                                        f"{gate_result.decision.value}"
                                    ),
                                ),
                            )
                        break
                    # bucket_disjoint: keep scanning the ranked queue so a
                    # bucket-disjoint later candidate can also ship this round.
                    continue

                # A rejected candidate is fully archived, then the next ranked
                # candidate gets its own scoped evaluation and gate.
                last_rejected_eval = tk_eval
                result.decisions[variant_id] = Decision.REJECT
                self._archive_rejection(
                    variant_id,
                    candidate,
                    gate_result,
                    round_idx,
                    downgraded=False,
                    candidate_id=candidate_id,
                )
            else:
                # Preserve the legacy single-candidate curve point: when every
                # candidate rejects, expose the final attempted candidate here.
                # Under bucket_disjoint the loop always completes (no first-wins
                # break), so guard against clobbering a curve point already set by
                # a shipped candidate for this variant.
                if last_rejected_eval is not None and variant_id not in result.selected_candidate_ids:
                    result.per_variant_pass[variant_id] = dict(last_rejected_eval)

        # Phase 2 — compute one deterministic capacity/retirement plan and only
        # then mutate the pool and ledger. In particular, a full-pool fork can
        # never retire an object that a later pending action still needs.
        self._settle_round(pending, round_idx, result)

        # 6. idle: reset on any ship, otherwise advance one.
        self._idle = 0 if result.shipped else self._idle + 1
        result.idle = self._idle
        result.variant_count = len(self.pool)
        return result

    # ------------------------------------------------------------------
    # ship confirmation (--ship-confirmation full_bed)
    # ------------------------------------------------------------------

    def _run_ship_confirmation(
        self,
        candidate: Any,
        variant_id: str,
        tasks: set[str],
        round_idx: int,
        window_result: GateResult,
    ) -> tuple[GateResult, dict[str, Any]]:
        """Re-decide a window APPLY/FORK on the whole task bed (the verdict).

        The candidate is re-evaluated on ``tasks`` — the run's complete task list,
        not the routed window — through the *same* ``evaluate`` callback, marked
        ``phase=SHIP_CONFIRM_PHASE`` so the caller can bill the extra rollouts to
        their own bucket. The full-bed outcomes go through the same before/after
        classification the window used (:func:`gate._classify`) against the same
        ledger and regression baseline, and the same :func:`gate._decide` under
        the same ``min_fork``.

        Returns the full-bed :class:`GateResult` (whose ``decision`` and
        ``improved``/``regressed`` sets REPLACE the window's for settlement, and
        whose ``failed_stage`` is :attr:`GateStage.SHIP_CONFIRM` on an overturn)
        plus the per-candidate ``ship_confirm`` meta. This never runs the manifest
        / L2 / smoke stages again: those do not depend on the task set and already
        passed in the window gate — only the seesaw is task-set-dependent.
        """
        full_eval_raw = self.evaluate(
            candidate, set(tasks), round_idx, phase=SHIP_CONFIRM_PHASE
        )
        self._require_full_coverage(variant_id, tasks, full_eval_raw)
        full_results = [
            self._task_eval(variant_id, task, full_eval_raw[task])
            for task in sorted(tasks)
        ]
        full_improved, full_regressed = _classify(
            full_results, self.ledger, regression_baseline=self.regression_baseline
        )
        full_decision = _decide(full_improved, full_regressed, self.min_fork)

        window_summary = (
            f"improved={sorted(window_result.improved)} "
            f"regressed={sorted(window_result.regressed)}"
        )
        full_summary = f"improved={sorted(full_improved)} regressed={sorted(full_regressed)}"
        window_decision = window_result.decision

        if full_decision is Decision.REJECT:
            archive_reason = (
                f"{GateStage.SHIP_CONFIRM.name}: window said "
                f"{window_decision.value} ({window_summary}) but full bed said "
                f"reject ({full_summary})"
            )
            confirm_result = GateResult(
                passed=False,
                failed_stage=GateStage.SHIP_CONFIRM,
                decision=Decision.REJECT,
                archive_reason=archive_reason,
                improved=frozenset(full_improved),
                regressed=frozenset(full_regressed),
            )
        else:
            archive_reason = (
                f"{full_decision.name}: {full_summary} "
                f"(SHIP_CONFIRM full_bed confirmed; window said "
                f"{window_decision.value} {window_summary})"
            )
            confirm_result = GateResult(
                passed=True,
                failed_stage=None,
                decision=full_decision,
                archive_reason=archive_reason,
                improved=frozenset(full_improved),
                regressed=frozenset(full_regressed),
            )

        meta = {
            "mode": self.ship_confirmation,
            "window_decision": window_decision.value,
            "full_decision": full_decision.value,
            "window_improved": len(window_result.improved),
            "window_regressed": len(window_result.regressed),
            "full_improved": len(full_improved),
            "full_regressed": len(full_regressed),
        }
        return confirm_result, meta

    # ------------------------------------------------------------------
    # decision handling
    # ------------------------------------------------------------------

    def _settle_round(
        self,
        pending: Iterable[_PendingCandidate],
        round_idx: int,
        result: RoundResult,
    ) -> None:
        """Settle a round transaction after all candidates have been gated.

        The paper specifies per-candidate APPLY/FORK/REJECT but says nothing
        about simultaneous ships or capacity conflicts. Our deterministic
        engineering policy is:

        * APPLY parents are protected and settle first;
        * fork proposals are considered by parent id;
        * only a feasible subset whose parents can remain beside their children
          is admitted;
        * required retirees are chosen once, by ``retirement_metric`` then id;
        * all retirees leave and all orphans are reassigned before any child is
          created.

        This removes the former order pollution where the first fork could
        retire a later variant before that variant was evaluated.
        """
        proposals = sorted(pending, key=lambda item: item.variant_id)
        applies = [item for item in proposals if item.gate_result.decision is Decision.APPLY]
        forks = [item for item in proposals if item.gate_result.decision is Decision.FORK]
        rejected = [
            item
            for item in proposals
            if item.gate_result.decision not in (Decision.APPLY, Decision.FORK)
        ]

        fork_winners, fork_losers, retirees = self._plan_forks(applies, forks)

        # Gate rejects never mutate the ledger: their candidate is not embodied
        # by any live variant.
        for item in rejected:
            result.decisions[item.variant_id] = Decision.REJECT
            self._archive_rejection(
                item.variant_id,
                item.candidate,
                item.gate_result,
                round_idx,
                downgraded=False,
                candidate_id=item.candidate_id,
            )

        # Capacity-downgraded forks are also candidate-only measurements.
        for item in fork_losers:
            result.decisions[item.variant_id] = Decision.REJECT
            diagnostic = result.candidate_diagnostics[item.candidate_id]
            diagnostic.decision = Decision.REJECT
            diagnostic.failed_stage = GateStage.SEESAW_REGRESSION
            diagnostic.archive_reason = (
                "FORK downgraded: round settlement could not preserve both "
                f"parent and child within K={self.pool.K}; "
                f"{item.gate_result.archive_reason}"
            )
            self._archive_rejection(
                item.variant_id,
                item.candidate,
                item.gate_result,
                round_idx,
                downgraded=True,
                candidate_id=item.candidate_id,
                downgrade_reason=(
                    "round settlement could not preserve both parent and child "
                    f"within K={self.pool.K}"
                ),
            )

        # Retire in one planned batch. Reassigning only after all removals keeps
        # an orphan from being sent to another variant that is about to retire.
        # This also happens before *any* current-round ledger write, so even a
        # cold-start reassignment cannot leak an APPLY result into its own round.
        orphans: set[str] = set()
        for variant_id in retirees:
            orphans.update(self.pool.retire(variant_id))
            result.retired.append(variant_id)
        if orphans:
            self.pool.reassign(
                sorted(orphans),
                self.router,
                self.ledger,
                before_round=round_idx,
            )

        # APPLY parents were protected by the plan and therefore still exist.
        for item in applies:
            if self.record_selected_results:
                self._record(item.variant_id, item.tk_eval, round_idx)
            self._apply_candidate(self.pool.variants[item.variant_id], item.candidate)
            result.decisions[item.variant_id] = Decision.APPLY

        for item in fork_winners:
            child = self.pool.fork(
                item.variant_id,
                item.gate_result.improved,
                round_idx,
            )
            result.forked.append(child.variant_id)
            # Under bucket_disjoint a variant may both APPLY (mutating itself) and
            # spawn a FORK child the same round; the in-place APPLY is that
            # variant's outcome, so do not overwrite it with FORK. The child is
            # recorded in ``result.forked`` either way. Under first_wins a variant
            # never has both, so this is byte-identical there.
            if result.decisions.get(item.variant_id) is not Decision.APPLY:
                result.decisions[item.variant_id] = Decision.FORK

            # The child embodies the candidate evaluated on the entire T_k.
            # Record successes *and failures* from that whole scoped evaluation.
            # Recording only improved tasks gives a child an optimistic prior and
            # inflates both routing and retirement rollups. The unchanged parent
            # intentionally receives none of the candidate's measurements.
            if self.record_selected_results:
                self._record(child.variant_id, item.tk_eval, round_idx)
            self._apply_candidate(child, item.candidate)

        result.shipped = bool(applies or fork_winners)

    def _plan_forks(
        self,
        applies: Iterable[_PendingCandidate],
        forks: Iterable[_PendingCandidate],
    ) -> tuple[list[_PendingCandidate], list[_PendingCandidate], list[str]]:
        """Choose feasible forks and their retirees from the pre-settle state."""
        apply_ids = {item.variant_id for item in applies}
        active_ids = set(self.pool.variants)
        active_count = len(active_ids)
        winners: list[_PendingCandidate] = []
        losers: list[_PendingCandidate] = []

        for item in sorted(forks, key=lambda proposal: proposal.variant_id):
            trial = [*winners, item]
            winner_ids = {proposal.variant_id for proposal in trial}
            needed = max(0, active_count + len(trial) - self.pool.K)
            retireable = active_ids - apply_ids - winner_ids
            if len(retireable) >= needed:
                winners.append(item)
            else:
                losers.append(item)

        winner_ids = {item.variant_id for item in winners}
        needed = max(0, active_count + len(winners) - self.pool.K)
        retireable = active_ids - apply_ids - winner_ids
        retirees = sorted(
            retireable,
            key=lambda variant_id: (self._retirement_score(variant_id), variant_id),
        )[:needed]
        return winners, losers, retirees

    def _retirement_score(self, variant_id: str) -> float:
        """Score a retirement candidate under the configured macro/raw arm."""
        assignments = getattr(self.router, "task_to_cluster", None)
        if self.retirement_metric == "cluster_macro" and assignments is None:
            # No external cluster map: use the frozen routing-induced partition.
            # The paper does not define cluster-macro retirement; this fallback
            # is a deterministic engineering choice rather than a paper claim.
            assignments = {
                task_id: carrier
                for carrier, variant in self.pool.variants.items()
                for task_id in variant.routed_tasks
            }
        return self.ledger.variant_rollup(
            variant_id,
            mode=self.retirement_metric,
            task_clusters=assignments,
        )

    def _weakest_other(self, keep_id: str) -> str | None:
        """Lowest-ranked variant other than ``keep_id`` (ties: lowest id)."""
        others = sorted(vid for vid in self.pool.variants if vid != keep_id)
        if not others:
            return None
        return min(others, key=lambda variant_id: (self._retirement_score(variant_id), variant_id))

    def _multiship_skip_reason(
        self,
        decision: Decision,
        variant_id: str,
        candidate: Any,
        claimed_buckets: set[str],
        applied_variants: set[str],
    ) -> str | None:
        """Return an audit reason to skip a gate-approved candidate, or ``None``.

        Only the ``bucket_disjoint`` policy (App B.1 p.34) can skip a candidate
        the gate approved; ``first_wins`` always returns ``None`` here (its own
        first-wins break, not this method, ends the queue). Two skip reasons,
        checked in the paper's order:

        1. **Bucket already claimed** — the candidate carries an edit bucket a
           higher-ranked ship already took this round. This is App B.1's verbatim
           rule ("skipping any whose bucket is already claimed this round").
        2. **Unreconstructable same-variant APPLY** — the candidate is a second
           APPLY to a variant already applied this round. Our candidates are
           whole ``config.yaml`` files authored against the round-start config,
           not diffs, and the repo has no sound field-level merge of two whole
           configs (M-17 Phase-1: ``compute_changeset`` is a diff *producer*
           only; ``HarnessConfig`` has no prompt/template field — templates live
           inside the ``processors`` list — so per-bucket field ownership is not
           separable, and ``compute_changeset`` cannot even see config-scalar
           edits). Shipping the second APPLY would last-write-wins clobber the
           first, so we skip it and record the ambiguity honestly rather than
           silently discard a candidate the paper's prose implies could ship.
        """
        if self.ship_policy != "bucket_disjoint":
            return None
        overlap = self._candidate_buckets(candidate) & claimed_buckets
        if overlap:
            return (
                "ship_skipped: bucket-disjoint policy — bucket(s) "
                f"{sorted(overlap)} already claimed by an earlier-ranked ship "
                "this round (App B.1 p.34)"
            )
        if decision is Decision.APPLY and variant_id in applied_variants:
            return (
                "ship_skipped: bucket-disjoint same-variant apply requires config "
                "merge (unreconstructable from whole-config candidates)"
            )
        return None

    @staticmethod
    def _candidate_buckets(candidate: Any) -> frozenset[str]:
        """The candidate's declared edit buckets (Table 9 p.36), or empty.

        Reads ``candidate.manifest.bucket`` (a :class:`CandidateArtifact`) or
        ``candidate.bucket`` (a bare :class:`ChangeManifest`); an opaque
        candidate that declares neither claims — and collides with — nothing.
        """
        manifest = getattr(candidate, "manifest", None)
        raw = getattr(manifest, "bucket", None)
        if raw is None:
            raw = getattr(candidate, "bucket", None)
        if not raw:
            return frozenset()
        if isinstance(raw, str):
            return frozenset({raw})
        return frozenset(str(bucket) for bucket in raw)

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
        self,
        variant_id: str,
        candidate: Any,
        gate_result: GateResult,
        round_idx: int,
        *,
        downgraded: bool,
        candidate_id: str | None = None,
        downgrade_reason: str | None = None,
    ) -> None:
        if self.evidence is None:
            return
        from .evidence import RejectedCandidate  # local: keep evidence optional

        candidate_id = candidate_id or self._candidate_id(candidate, variant_id, round_idx, 1)
        failed_stage = gate_result.failed_stage.name if gate_result.failed_stage is not None else "SEESAW_REGRESSION"
        reason = gate_result.archive_reason
        if downgraded:
            detail = downgrade_reason or f"no retireable variant besides {variant_id}"
            reason = f"FORK downgraded: {detail}; {reason}"
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

    def _candidate_queue(self, evolved: Any) -> tuple[list[Any], list[Any]]:
        """Normalise one ``evolve`` result without disturbing ranked order.

        Text and bytes are legacy opaque single candidates despite implementing
        ``Sequence``. The split is an engine-safety boundary only; the recipe's
        CandidatePipeline remains responsible for the paper's global K_t cap.
        """
        if evolved is None:
            candidates: list[Any] = []
        elif isinstance(evolved, Sequence) and not isinstance(
            evolved, (str, bytes, bytearray)
        ):
            candidates = list(evolved)
        else:
            candidates = [evolved]
        limit = self.max_candidates_per_variant
        return candidates[:limit], candidates[limit:]

    @staticmethod
    def _candidate_id(
        candidate: Any,
        variant_id: str,
        round_idx: int,
        position: int,
    ) -> str:
        """Return the declared id or a stable id for legacy opaque candidates."""
        try:
            declared = getattr(candidate, "candidate_id", "")
        except Exception:  # noqa: BLE001 - diagnostics must survive bad metadata
            declared = ""
        if declared:
            return str(declared)
        return f"C-R{round_idx}-{variant_id}-{position:02d}"

    @staticmethod
    def _add_diagnostic(result: RoundResult, diagnostic: CandidateDiagnostic) -> None:
        """Insert one globally keyed diagnostic, rejecting ambiguous ids."""
        if diagnostic.candidate_id in result.candidate_diagnostics:
            previous = result.candidate_diagnostics[diagnostic.candidate_id]
            raise ValueError(
                "candidate ids must be unique within a round: "
                f"{diagnostic.candidate_id!r} appeared for "
                f"{previous.variant_id!r} and {diagnostic.variant_id!r}"
            )
        result.candidate_diagnostics[diagnostic.candidate_id] = diagnostic

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
