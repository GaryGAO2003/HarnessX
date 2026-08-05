# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""W5 + W21 + W27 — the deterministic gate: five ordered checks, three-way exit.

The gate is the **only** thing that decides what ships: §4.3 p.10, "only
deterministic checks govern shipping". The Critic produces a ``ship_ranking``
and nothing more — ranked candidates still run this gate one by one and the
first one through wins (Algorithm 1 L21-24). Nothing here consults an LLM.

The sequence (§4.3 p.10, verbatim "manifest completeness -> configuration
normalization -> build/smoke tests -> seesaw constraint", "the first failing
check halts", candidates "archived with rejection reason"):

===  ================== ===========================================
1    MANIFEST_COMPLETE  every manifest field present (W13)
2    CANONICALIZE       candidate config normalises cleanly
3    BUILD_SMOKE_L1     new processors/tools instantiate and run
4    ROUNDTRIP_L2       tool output survives provider serialisation (W24)
5    SEESAW_REGRESSION  three-way decision below
===  ================== ===========================================

Stage 4 is the paper's Level-2 check (p.32): "a unit call that returns does not
prove the agent sees the return".

Which stages are real
---------------------
Stages 1 and 4 are implemented here against :mod:`.manifest`: stage 1 is
:meth:`ChangeManifest.validate_complete` and stage 4 asks a code candidate for
the Level-2 round-trip evidence :func:`.manifest.check_level2_roundtrip`
produces. Both fire only when ``candidate`` actually *is* a
:class:`ChangeManifest`; an opaque candidate keeps the batch-A stub behaviour,
and an explicitly injected check always wins over the built-in one.

Stages 2 and 3 are **no-op by design** (SPEC §7.8, batch C4). The canonicalizer
(``harness.py:944``) and the synthetic smoke runner (``replay.py:64``) are
*not* re-run here: the C4 investigation established that every candidate which
reaches this gate has already passed both, upstream, before the seesaw sees it.

* ``meta_agent.evolve`` runs the paper's own gate internally
  (``EvolveValidator.run``, ``validate_workflow.py:890``): canonicalize ->
  contract -> replay(synthetic smoke) -> novelty -> evidence, and *raises* on
  the first failure. It returns a ``config.yaml`` only after that validator
  passes, so an evolved candidate has provably canonicalized and cleared the
  replay smoke; a rejected one raises and never becomes a candidate
  (``run_variant_pool._evolve`` catches it as "no candidate this round"). A
  *forked* candidate is the **same** evolve product — ``_reconcile`` repoints the
  fork child at the gated candidate's YAML, it does not synthesise a fresh,
  un-evolved config — so a fork never bypasses the check either.
* The recipe's ``evaluate`` step runs *before* this gate in
  :meth:`VariantPoolEngine.run_round`, and it loads every candidate's config
  through ``_prepare_round_config`` =
  ``HarnessConfig.from_yaml_file(...).canonicalize()`` (un-guarded: a failure
  raises and the candidate never reaches the gate) and then runs the full
  harness for real. That canonicalizes even the round-0 *baseline* candidate —
  the only path that skips ``evolve`` — and the real rollouts are a strictly
  stronger smoke than the synthetic one.

Re-running canonicalize/replay here would verify nothing new and would risk a
*second* replay timeout — the exact failure that surfaced on the first real run.
The two stages keep their enum slots, their position in the sequence, and their
injection seams: an explicitly injected ``check_canonicalize`` / ``check_smoke``
still wins (so a V1 reward-hacking probe, SPEC §9.5, can force a failure), but
no built-in check is wired and none should be. An absent check passes, so the
sequencing and the seesaw are also exercised offline with stubs. The gate's real
interception therefore comes from three stages: MANIFEST_COMPLETE and
ROUNDTRIP_L2 (on a :class:`ChangeManifest`) and SEESAW_REGRESSION (always).
Stage 5 is implemented in full here.

Stage 4 has two strengths, deliberately. Offline it verifies that the manifest
*declares* Level-2 evidence, which is what the Critic checks at ship time
("the Critic verified Level-2 evidence ... before accepting any tools-bucket
candidate", p.37). With a live provider, :func:`level2_roundtrip_check` builds
a check that re-runs the round trip for real; batch C injects it.

Three-way exit (§4.5 p.11, verbatim)
------------------------------------
"(1) the edit improves some tasks without regressing any, in which case it is
applied to its target variant; or (2) it improves a subset while regressing
others, in which case the system forks a new variant rather than rejecting the
edit outright"; otherwise it is rejected. This is the sharpest move in the
paper — a conflict stops being a reason to reject and becomes a reason to
branch.

Two asymmetries in the classification are deliberate:

* **improved** is judged against the candidate's own before-state on ``T_k``
  (0/2 -> >=1/2);
* **regressed** is judged against :attr:`SuccessLedger.ever_solved`, the
  full-history solved set (W21, §4.1 p.8 "any previously solved task recorded
  in ``T_t``"). A task solved in R3 and quietly broken in R5 is *still* a
  regression for an R6 candidate that leaves it at 0/2. Anchoring on last
  round instead would reproduce a strictly weaker constraint than the paper's.

Scope: ``tk_results`` must already be narrowed to ``T_k``, the tasks routed to
the candidate's target variant ("a candidate targeting variant k is tested only
against tasks routed to k", §4.5 p.11). That narrowing is what stops one
cluster's improvement from being blocked by another cluster's regression; the
caller owns it, the gate assumes it.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any, Callable

from .manifest import DEFAULT_LEVEL2_LABEL, ChangeManifest, check_level2_roundtrip

if TYPE_CHECKING:  # pragma: no cover - typing only, no runtime import cycle
    from .ledger import SuccessLedger

#: Minimum ``(improved, regressed)`` sizes before a conflict forks.
#:
#: ``(1, 1)`` is the paper-faithful default: §4.5 says an edit that improves a
#: subset while regressing others forks, without adding a minimum cardinality.
#: ``(2, 2)`` remains an explicit anti-noise ablation. The paper neither
#: specifies nor evaluates such a threshold, so making it the default would
#: silently replace the published state transition with an engineering choice.
DEFAULT_MIN_FORK = (1, 1)

#: --regression-baseline (M-23). Which solve history the seesaw's *regression*
#: side is anchored on.
#:
#: ``global`` (default, W21) keeps today's behaviour byte-identical: a candidate
#: regresses a task if **any** variant ever solved it (the full-history
#: cross-variant :attr:`SuccessLedger.ever_solved`). ``per_variant`` (M-23)
#: anchors instead on the candidate's *own* variant history, the same
#: per-variant cell scope the improved side already uses — so a task only ever
#: solved by a *different* variant is not this candidate's regression.
#:
#: ``windowed`` is the official adjacent-round watchlist semantics ported from
#: ``upstream/feat/aegis:harnessx/aegis/data/regressions.py`` (L54-66, 124-126:
#: ``detect_regressions`` compares round N-1 vs N only, keeping **no accumulated
#: solved set**). A task counts as a regression only if it was solved in the
#: *immediately previous settled round*; a one-off solve many rounds ago that has
#: since gone stale is not a regression. Both ``global`` and ``per_variant``
#: accumulate ("ever solved"); ``windowed`` deliberately does not.
#:
#: One documented divergence from the official ``for the same variant`` clause:
#: the official aegis has a single config per round, so "same variant" is trivial
#: there. In our multi-variant pool the deterministic gate is handed only the
#: ledger and the candidate's ``tk_results`` — it never receives the target
#: ``variant_id`` (the engine derives ``before`` from it in ``_task_eval`` and
#: passes neither on to :func:`run_gate`). ``windowed`` therefore judges the
#: previous round *variant-agnostically*: a prior-round solve by any variant
#: blocks. On the variant-scoping axis this places ``windowed`` between ``global``
#: and ``per_variant``; on the accumulation axis it is strictly the tightest of
#: the three. Because routing keeps a task on the same variant across adjacent
#: rounds in the common case, the two readings usually coincide; see
#: :func:`_solved_in_previous_settled_round`.
REGRESSION_BASELINE_GLOBAL = "global"
REGRESSION_BASELINE_PER_VARIANT = "per_variant"
REGRESSION_BASELINE_WINDOWED = "windowed"
REGRESSION_BASELINE_MODES = (
    REGRESSION_BASELINE_GLOBAL,
    REGRESSION_BASELINE_PER_VARIANT,
    REGRESSION_BASELINE_WINDOWED,
)
DEFAULT_REGRESSION_BASELINE = REGRESSION_BASELINE_GLOBAL


class GateStage(Enum):
    """The ordered deterministic checks of §4.3 p.10.

    The first five members are the run_gate sequence (see :data:`GATE_SEQUENCE`).
    ``SHIP_CONFIRM`` is **not** a run_gate stage: it is the engine-level pre-ship
    full-bed confirmation (``--ship-confirmation full_bed``). ``run_gate`` never
    reaches it — the window seesaw still exits at ``SEESAW_REGRESSION`` — but a
    candidate the window gate would ship can be overturned by the full-bed
    re-classification, and that archival record carries this stage. It is
    deliberately excluded from :data:`GATE_SEQUENCE` so the five-stage sequence
    (and its order tests) stay exactly the paper's.
    """

    MANIFEST_COMPLETE = "manifest_complete"
    CANONICALIZE = "canonicalize"
    BUILD_SMOKE_L1 = "build_smoke_l1"
    ROUNDTRIP_L2 = "roundtrip_l2"
    SEESAW_REGRESSION = "seesaw_regression"
    #: Engine-level pre-ship full-bed confirmation (not a run_gate check).
    SHIP_CONFIRM = "ship_confirm"


#: Evaluation order; the first failing stage halts the gate. ``SHIP_CONFIRM`` is
#: intentionally absent — it is enacted by the engine after the gate, never by
#: ``run_gate`` (see :class:`GateStage`).
GATE_SEQUENCE = (
    GateStage.MANIFEST_COMPLETE,
    GateStage.CANONICALIZE,
    GateStage.BUILD_SMOKE_L1,
    GateStage.ROUNDTRIP_L2,
    GateStage.SEESAW_REGRESSION,
)


class Decision(Enum):
    """Three-way outcome of the seesaw stage (§4.5 p.11)."""

    APPLY = "apply"
    FORK = "fork"
    REJECT = "reject"


@dataclass(frozen=True)
class TaskEval:
    """One task's pass@2 outcome before and after the candidate.

    Each tuple is ``(n_pass, n_att)``; under pass@2 ``n_att == 2`` and
    ``n_pass in {0, 1, 2}`` (§6.1 p.15). "Solved" means ``n_pass >= 1``.
    """

    task_id: str
    before: tuple[int, int]
    after: tuple[int, int]

    def __post_init__(self) -> None:
        for label, (n_pass, n_att) in (("before", self.before), ("after", self.after)):
            if n_att < 0 or n_pass < 0:
                raise ValueError(f"{self.task_id}.{label}: negative rollout counts {(n_pass, n_att)}")
            if n_pass > n_att:
                raise ValueError(f"{self.task_id}.{label}: n_pass exceeds n_att {(n_pass, n_att)}")


@dataclass
class GateResult:
    """Outcome of one candidate's trip through the gate.

    ``improved``/``regressed`` extend the SPEC fields: a FORK decision has to
    tell :meth:`VariantPool.fork` *which* tasks the new variant is being spawned
    to serve, and the archived rejection record is far more useful with the two
    task sets than without them.
    """

    passed: bool
    failed_stage: GateStage | None
    decision: Decision | None
    archive_reason: str
    improved: frozenset[str] = field(default_factory=frozenset)
    regressed: frozenset[str] = field(default_factory=frozenset)


# ---------------------------------------------------------------------------
# Stage 5 — seesaw
# ---------------------------------------------------------------------------


def _classify(
    tk_results: Iterable[TaskEval],
    ledger: SuccessLedger,
    *,
    regression_baseline: str = REGRESSION_BASELINE_GLOBAL,
) -> tuple[set[str], set[str]]:
    """Split ``T_k`` into improved and regressed tasks.

    See the module docstring for why the two sides use different baselines.
    The categories are mutually exclusive by construction (improved needs
    ``after >= 1``, regressed needs ``after == 0``). ``regression_baseline``
    selects which solve history the regressed side is judged against — see
    :func:`_is_regression`.
    """
    improved: set[str] = set()
    regressed: set[str] = set()
    for outcome in tk_results:
        before_passes = outcome.before[0]
        after_passes = outcome.after[0]
        if before_passes == 0 and after_passes >= 1:
            improved.add(outcome.task_id)
        if after_passes == 0 and _is_regression(
            outcome, before_passes, ledger, regression_baseline
        ):
            regressed.add(outcome.task_id)
    return improved, regressed


def _is_regression(
    outcome: TaskEval,
    before_passes: int,
    ledger: SuccessLedger,
    regression_baseline: str,
) -> bool:
    """Is an ``after == 0`` task a regression under the chosen baseline (M-23)?

    ``global`` (default, W21) anchors on the full-history, cross-variant
    :attr:`SuccessLedger.ever_solved` set. ``per_variant`` anchors on the
    candidate's own variant instead: ``before_passes >= 1`` is exactly "this
    variant has ever recorded a pass on the task" — the same per-variant cell
    scope the improved side uses (the engine derives ``before`` from
    ``SuccessLedger.cell(variant_id, task_id)`` in ``_task_eval``). A task solved
    only by a *different* variant therefore is not this candidate's regression.

    ``windowed`` (official adjacent-round watchlist) accumulates nothing: it asks
    only whether the task was solved in the *immediately previous settled round*.
    ``before`` cannot answer this — the engine builds ``before`` from the variant
    cell's **accumulated** ``cell.passes >= 1`` (any round, ever), so ``before[0]
    >= 1`` is already exactly what ``per_variant`` reads. Windowed instead reads
    the ledger's per-round buckets directly; see
    :func:`_solved_in_previous_settled_round` for the variant-agnostic note.
    """
    if regression_baseline == REGRESSION_BASELINE_PER_VARIANT:
        return before_passes >= 1
    if regression_baseline == REGRESSION_BASELINE_WINDOWED:
        return _solved_in_previous_settled_round(outcome.task_id, ledger)
    return ledger.is_ever_solved(outcome.task_id)


def _solved_in_previous_settled_round(task_id: str, ledger: SuccessLedger) -> bool:
    """Was ``task_id`` solved (``n_pass >= 1``) in the immediately previous round?

    The ``windowed`` baseline's per-round predicate. ``prev_round =
    ledger.max_last_round()`` is the last round anything settled into the ledger;
    at gate time the candidate's *current*-round rollouts are not yet recorded
    (``before`` is "the variant's ledger view entering this round" —
    ``engine._task_eval``), so ``max_last_round()`` is exactly round N-1, the
    "immediately previous" round the official ``detect_regressions`` compares
    against.

    Variant-agnostic by necessity: :func:`run_gate` never receives the target
    ``variant_id`` (see :data:`REGRESSION_BASELINE_WINDOWED`), so the previous
    round is read across *every* variant via the public ledger API
    (``max_last_round`` / ``variants`` / ``aggregate_counts`` with a one-round
    window). A prior-round solve by any variant counts. Returns ``False`` on an
    empty ledger (no previous round).
    """
    prev_round = ledger.max_last_round()
    if prev_round < 0:
        return False
    for variant_id in ledger.variants():
        # before_round = prev_round + 1 with window = 1 selects exactly the
        # single round-bucket ``round_idx == prev_round`` (see
        # ``SuccessLedger._task_counts``); passes there is the previous round's
        # settled pass count for this variant.
        passes, _attempts = ledger.aggregate_counts(
            variant_id, (task_id,), before_round=prev_round + 1, window=1
        )
        if passes >= 1:
            return True
    return False


def _decide(improved: set[str], regressed: set[str], min_fork: tuple[int, int]) -> Decision:
    min_improve, min_regress = min_fork
    if not improved:
        # nothing gained: either flat or purely harmful
        return Decision.REJECT
    if not regressed:
        return Decision.APPLY
    if len(improved) >= min_improve and len(regressed) >= min_regress:
        return Decision.FORK
    # a conflict too small to distinguish from pass@2 noise: do not spend a
    # variant slot on it. The improvement is dropped with it, because the
    # seesaw forbids applying an edit that regresses an ever-solved task.
    return Decision.REJECT


def _seesaw_three_way(
    tk_results: Iterable[TaskEval],
    ledger: SuccessLedger,
    *,
    min_fork: tuple[int, int] = DEFAULT_MIN_FORK,
    regression_baseline: str = REGRESSION_BASELINE_GLOBAL,
) -> Decision:
    """APPLY / FORK / REJECT for a candidate, on ``T_k`` only (§4.5 p.11)."""
    _validate_min_fork(min_fork)
    _validate_regression_baseline(regression_baseline)
    improved, regressed = _classify(tk_results, ledger, regression_baseline=regression_baseline)
    return _decide(improved, regressed, min_fork)


def _validate_min_fork(min_fork: tuple[int, int]) -> None:
    if len(min_fork) != 2:
        raise ValueError(f"min_fork must be a (min_improve, min_regress) pair, got {min_fork!r}")
    if min_fork[0] < 1 or min_fork[1] < 1:
        raise ValueError(f"min_fork entries must be >= 1, got {min_fork!r}")


def _validate_regression_baseline(regression_baseline: str) -> None:
    if regression_baseline not in REGRESSION_BASELINE_MODES:
        raise ValueError(
            f"regression_baseline must be one of {REGRESSION_BASELINE_MODES}, "
            f"got {regression_baseline!r}"
        )


# ---------------------------------------------------------------------------
# Stages 1 and 4 — the manifest contract (W13 + W24)
# ---------------------------------------------------------------------------


def _manifest_complete(candidate: ChangeManifest) -> list[str]:
    """Stage 1 — "manifest completeness" (§4.3 p.10)."""
    return candidate.validate_complete()


def _declared_level2(candidate: ChangeManifest) -> tuple[bool, str]:
    """Stage 4 offline — does the manifest carry Level-2 round-trip evidence?

    A candidate with no code asset is exempt: p.32, "Pure prompt-bucket
    candidates (no code asset) are exempt -- the counterfactual gate provides
    the equivalent smoke check". Everything in
    :data:`.manifest.CODE_BUCKETS` must show the evidence, which is what the
    Critic did before accepting C-R10-02 (p.37).
    """
    if not candidate.needs_code_verification():
        return True, f"no code asset in bucket={candidate.bucket}; exempt (p.32)"
    entry = candidate.level2_evidence()
    if entry is None:
        return False, f"no Level-2 round-trip evidence for bucket={candidate.bucket}"
    return True, f"declared: {entry.get('evidence', '')}"


def level2_roundtrip_check(
    serializer: Callable[[str], Any],
    tool_output: str,
    *,
    label: str = DEFAULT_LEVEL2_LABEL,
) -> Callable[[Any], tuple[bool, str]]:
    """Build a live stage-4 check around :func:`.manifest.check_level2_roundtrip`.

    Batch C wires the repo's real provider serializer and the candidate's own
    probe output; stage A has neither (SPEC §5), so the gate falls back to
    :func:`_declared_level2`. The returned check ignores its candidate argument
    because the closure is already candidate-specific — one probe output per
    candidate.
    """

    def _check(candidate: Any) -> tuple[bool, str]:  # noqa: ARG001 - closure is per candidate
        evidence = check_level2_roundtrip(tool_output, serializer, label=label)
        return evidence.survived, evidence.note

    return _check


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------


def run_gate(
    candidate: Any,
    parent_config: Any,
    ledger: SuccessLedger,
    tk_results: Iterable[TaskEval],
    *,
    min_fork: tuple[int, int] = DEFAULT_MIN_FORK,
    regression_baseline: str = REGRESSION_BASELINE_GLOBAL,
    check_manifest: Callable[[Any], list[str]] | None = None,
    check_canonicalize: Callable[[Any, Any], tuple[bool, str]] | None = None,
    check_smoke: Callable[[Any], tuple[bool, str]] | None = None,
    check_roundtrip: Callable[[Any], tuple[bool, str]] | None = None,
) -> GateResult:
    """Run the five ordered checks; halt and archive on the first failure.

    Parameters
    ----------
    candidate:
        A :class:`.manifest.ChangeManifest`, an artifact exposing one through
        ``.manifest``, or an opaque legacy candidate. Structured artifacts are
        unwrapped for stages 1 and 4 but otherwise remain intact for injected
        checks and later engine settlement.
    parent_config:
        The target variant's config; opaque here, forwarded to
        ``check_canonicalize``.
    tk_results:
        Per-task pass@2 before/after outcomes, already narrowed to ``T_k``.
    check_manifest:
        Returns the list of missing manifest fields (empty = complete), i.e.
        the signature of ``ChangeManifest.validate_complete``. Defaults to that
        method when ``candidate`` is, or wraps, a manifest.
    check_canonicalize, check_smoke, check_roundtrip:
        Return ``(passed, reason)``. ``check_roundtrip`` defaults to the
        manifest's declared Level-2 evidence; build a live one with
        :func:`level2_roundtrip_check`.

    An omitted check on an opaque candidate passes: batch A wires no repo
    checks, so the sequencing and the seesaw are exercised with stubs (SPEC §5).
    An explicitly injected check always takes precedence over the built-in one.
    """
    _validate_min_fork(min_fork)
    _validate_regression_baseline(regression_baseline)

    manifest_candidate: ChangeManifest | None = None
    missing_manifest = object()
    manifest_attr: Any = missing_manifest
    if isinstance(candidate, ChangeManifest):
        manifest_candidate = candidate
    else:
        try:
            manifest_attr = getattr(candidate, "manifest", manifest_attr)
        except Exception as exc:  # noqa: BLE001 - malformed structured candidate
            return GateResult(
                passed=False,
                failed_stage=GateStage.MANIFEST_COMPLETE,
                decision=None,
                archive_reason=(
                    "MANIFEST_COMPLETE: structured candidate manifest could not be read: "
                    f"{type(exc).__name__}: {exc}"
                ),
            )
        if isinstance(manifest_attr, ChangeManifest):
            manifest_candidate = manifest_attr

    # An object that advertises ``.manifest`` is structured and must never fall
    # back to the opaque/no-op gate path. CandidateArtifact is intentionally
    # duck-typed here to avoid coupling the deterministic gate to the pipeline.
    if (
        not isinstance(candidate, ChangeManifest)
        and manifest_attr is not missing_manifest
        and not isinstance(manifest_attr, ChangeManifest)
    ):
        return GateResult(
            passed=False,
            failed_stage=GateStage.MANIFEST_COMPLETE,
            decision=None,
            archive_reason=(
                "MANIFEST_COMPLETE: structured candidate .manifest must be "
                f"ChangeManifest, got {type(manifest_attr).__name__}"
            ),
        )

    if manifest_candidate is not None:
        if check_manifest is None:
            check_manifest = _manifest_complete
        if check_roundtrip is None:
            check_roundtrip = _declared_level2

    if check_manifest is not None:
        check_subject = manifest_candidate if manifest_candidate is not None else candidate
        missing = list(check_manifest(check_subject))
        if missing:
            return GateResult(
                passed=False,
                failed_stage=GateStage.MANIFEST_COMPLETE,
                decision=None,
                archive_reason=f"MANIFEST_COMPLETE: missing fields {sorted(missing)}",
            )

    # Stages 2-4. CANONICALIZE and BUILD_SMOKE_L1 have no built-in check: they are
    # no-op by design (SPEC §7.8, C4) — evolve's internal gate and the evaluate-time
    # canonicalize + full-harness rollout already guarantee both for every candidate
    # that reaches here. ROUNDTRIP_L2 keeps its manifest-backed default. For all three,
    # an explicitly injected check still runs and wins (an absent one passes).
    for stage, check, args in (
        (GateStage.CANONICALIZE, check_canonicalize, (candidate, parent_config)),
        (GateStage.BUILD_SMOKE_L1, check_smoke, (candidate,)),
        (
            GateStage.ROUNDTRIP_L2,
            check_roundtrip,
            (manifest_candidate if manifest_candidate is not None else candidate,),
        ),
    ):
        if check is None:
            continue
        ok, reason = check(*args)
        if not ok:
            return GateResult(
                passed=False,
                failed_stage=stage,
                decision=None,
                archive_reason=f"{stage.name}: {reason}",
            )

    improved, regressed = _classify(tk_results, ledger, regression_baseline=regression_baseline)
    decision = _decide(improved, regressed, min_fork)
    summary = f"improved={sorted(improved)} regressed={sorted(regressed)}"

    if decision is Decision.REJECT:
        if not improved:
            detail = "no task improved"
        else:
            detail = (
                f"conflict below fork threshold min_fork={tuple(min_fork)} "
                f"(improved={len(improved)}, regressed={len(regressed)})"
            )
        return GateResult(
            passed=False,
            failed_stage=GateStage.SEESAW_REGRESSION,
            decision=Decision.REJECT,
            archive_reason=f"{GateStage.SEESAW_REGRESSION.name}: {detail}; {summary}",
            improved=frozenset(improved),
            regressed=frozenset(regressed),
        )

    return GateResult(
        passed=True,
        failed_stage=None,
        decision=decision,
        archive_reason=f"{decision.name}: {summary}",
        improved=frozenset(improved),
        regressed=frozenset(regressed),
    )
