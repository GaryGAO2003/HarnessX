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

#: Minimum ``(improved, regressed)`` sizes before a conflict is worth a fork.
#: Ours (SPEC §2.4/§6.6): pass@2 still leaves residual noise, and a one-task
#: flip in each direction is as likely to be noise as a real cluster conflict.
#: Forking on it would fill the pool with variants that specialise on nothing.
#: Ablation {(1, 1), (2, 2)}.
DEFAULT_MIN_FORK = (2, 2)


class GateStage(Enum):
    """The ordered deterministic checks of §4.3 p.10."""

    MANIFEST_COMPLETE = "manifest_complete"
    CANONICALIZE = "canonicalize"
    BUILD_SMOKE_L1 = "build_smoke_l1"
    ROUNDTRIP_L2 = "roundtrip_l2"
    SEESAW_REGRESSION = "seesaw_regression"


#: Evaluation order; the first failing stage halts the gate.
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


def _classify(tk_results: Iterable[TaskEval], ledger: SuccessLedger) -> tuple[set[str], set[str]]:
    """Split ``T_k`` into improved and regressed tasks.

    See the module docstring for why the two sides use different baselines.
    The categories are mutually exclusive by construction (improved needs
    ``after >= 1``, regressed needs ``after == 0``).
    """
    improved: set[str] = set()
    regressed: set[str] = set()
    for outcome in tk_results:
        before_passes = outcome.before[0]
        after_passes = outcome.after[0]
        if before_passes == 0 and after_passes >= 1:
            improved.add(outcome.task_id)
        if after_passes == 0 and ledger.is_ever_solved(outcome.task_id):
            regressed.add(outcome.task_id)
    return improved, regressed


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
) -> Decision:
    """APPLY / FORK / REJECT for a candidate, on ``T_k`` only (§4.5 p.11)."""
    _validate_min_fork(min_fork)
    improved, regressed = _classify(tk_results, ledger)
    return _decide(improved, regressed, min_fork)


def _validate_min_fork(min_fork: tuple[int, int]) -> None:
    if len(min_fork) != 2:
        raise ValueError(f"min_fork must be a (min_improve, min_regress) pair, got {min_fork!r}")
    if min_fork[0] < 1 or min_fork[1] < 1:
        raise ValueError(f"min_fork entries must be >= 1, got {min_fork!r}")


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
    check_manifest: Callable[[Any], list[str]] | None = None,
    check_canonicalize: Callable[[Any, Any], tuple[bool, str]] | None = None,
    check_smoke: Callable[[Any], tuple[bool, str]] | None = None,
    check_roundtrip: Callable[[Any], tuple[bool, str]] | None = None,
) -> GateResult:
    """Run the five ordered checks; halt and archive on the first failure.

    Parameters
    ----------
    candidate:
        A :class:`.manifest.ChangeManifest`, which switches stages 1 and 4 to
        their real implementations, or anything opaque, which leaves them to
        the injected checks.
    parent_config:
        The target variant's config; opaque here, forwarded to
        ``check_canonicalize``.
    tk_results:
        Per-task pass@2 before/after outcomes, already narrowed to ``T_k``.
    check_manifest:
        Returns the list of missing manifest fields (empty = complete), i.e.
        the signature of ``ChangeManifest.validate_complete``. Defaults to that
        method when ``candidate`` is a manifest.
    check_canonicalize, check_smoke, check_roundtrip:
        Return ``(passed, reason)``. ``check_roundtrip`` defaults to the
        manifest's declared Level-2 evidence; build a live one with
        :func:`level2_roundtrip_check`.

    An omitted check on an opaque candidate passes: batch A wires no repo
    checks, so the sequencing and the seesaw are exercised with stubs (SPEC §5).
    An explicitly injected check always takes precedence over the built-in one.
    """
    _validate_min_fork(min_fork)

    if isinstance(candidate, ChangeManifest):
        if check_manifest is None:
            check_manifest = _manifest_complete
        if check_roundtrip is None:
            check_roundtrip = _declared_level2

    if check_manifest is not None:
        missing = list(check_manifest(candidate))
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
        (GateStage.ROUNDTRIP_L2, check_roundtrip, (candidate,)),
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

    improved, regressed = _classify(tk_results, ledger)
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
