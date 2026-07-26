# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""Offline tests for the ``--force-gate`` plumbing probe in ``run_variant_pool``.

The probe overrides the deterministic gate's final APPLY/FORK decision so the
settlement chain runs end-to-end on data that organically only ever produced
REJECT. All tests here are fully offline: no network, no rollouts, no LLM. They
reuse the engine's scripted-stub style (``test_engine.py``) and the recipe
module's path bootstrap (``test_run_variant_pool.py``).

Everything is imported through the ``experiments.variant_pool.*`` path — the same
one the recipe uses — so ``run_gate`` / ``Decision`` / ``GateResult`` are the
exact objects the recipe's ``_forced_gate`` produces (importing the top-level
``variant_pool.*`` alias would give distinct module objects and break ``is``).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# The recipe lives under ``recipe/``; put the repo root on the path so it and its
# ``experiments.variant_pool`` imports resolve (conftest only adds ``experiments/``).
_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from recipe.gaia_evolver import run_variant_pool as rvp  # noqa: E402
from experiments.variant_pool.engine import VariantPoolEngine  # noqa: E402
from experiments.variant_pool.gate import (  # noqa: E402
    Decision,
    GateResult,
    GateStage,
    TaskEval,
    run_gate,
)
from experiments.variant_pool.ledger import SuccessLedger  # noqa: E402
from experiments.variant_pool.pool import VariantPool  # noqa: E402
from experiments.variant_pool.router import Router  # noqa: E402


# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------


class _Cand:
    """An opaque candidate the real gate treats as a pure seesaw (no manifest)."""

    def __init__(
        self,
        candidate_id: str = "C-R0-V0",
        target_variant: str = "V0",
        *,
        config_path: Path | None = None,
    ) -> None:
        self.candidate_id = candidate_id
        self.target_variant = target_variant
        if config_path is not None:
            self.config_path = config_path


class _BadManifestCand:
    """A structured candidate whose ``.manifest`` is not a ChangeManifest.

    ``run_gate`` returns a stage-1 MANIFEST_COMPLETE failure (``decision is
    None``) for this — the integrity failure a probe must never override.
    """

    candidate_id = "C-R0-bad"
    target_variant = "V0"
    manifest = "not-a-change-manifest"


class _ScriptedOne:
    """``evolve`` returns one candidate; ``evaluate`` returns a fixed outcome map."""

    def __init__(self, candidate, outcomes: dict[str, tuple[int, int]]) -> None:
        self.candidate = candidate
        self.outcomes = outcomes

    def evolve(self, variant, round_idx):  # noqa: ARG002 - scripted single round
        return self.candidate

    def evaluate(self, candidate, t_k, round_idx):  # noqa: ARG002 - scripted single round
        return {task: self.outcomes[task] for task in t_k}


# ---------------------------------------------------------------------------
# Recipe construction (threading, item 3) — offline, no model calls
# ---------------------------------------------------------------------------


class _Task:
    def __init__(self, task_id: str, level: int = 1) -> None:
        self.task_id = task_id
        self.level = level


class _Args:
    """Minimal CLI namespace the recipe __init__ reads (no ``force_gate`` by default)."""

    def __init__(self, **overrides) -> None:
        self.pool_k = 2
        self.num_rounds = 1
        self.pass_k = 2
        self.max_cost = 5.0
        self.concurrency = 2
        self.no_judge = True
        self.run_tag = "test-force-gate"
        self.model = "task-model"
        self.meta_model = "meta-model"
        self.max_tasks = 0
        self.max_steps = 20
        self.provider_id = "provider"
        self.api_base = None
        self.data_path = None
        self.seed = 0
        self.estimator = "laplace"
        self.cluster_mode = "routed"
        self.routing_mode = "cluster"
        self.routing_window = None
        self.retirement_metric = "task_macro"
        self.candidate_mode = "legacy_single"
        self.candidates_per_round = 4
        self.actionability_threshold = 1.0
        self.target_strategy = "all_active_variants"
        self.patience = 3
        self.planned_seeds = (0, 1, 2)
        self.evolve_steps = 200
        self.__dict__.update(overrides)


def _recipe(tmp_path, **overrides):
    run_dir = tmp_path / "run"
    run_dir.mkdir(parents=True, exist_ok=True)
    baseline = tmp_path / "V0" / "config.yaml"
    baseline.parent.mkdir(parents=True, exist_ok=True)
    baseline.write_text("baseline: true\n", encoding="utf-8")
    return rvp.VariantPoolRecipe(
        args=_Args(**overrides),
        tasks=[_Task("t1", 1)],
        model_config=object(),
        meta_agent=object(),
        pipeline_eval=object(),
        run_dir=run_dir,
        baseline_config_path=baseline,
    )


# ===========================================================================
# 1. off-mode identity — the gate path stays byte-identical
# ===========================================================================


def test_off_mode_is_identity_callable_and_preserves_the_real_result() -> None:
    """``off`` returns the real gate itself and hands back its exact result."""
    reject = GateResult(
        passed=False,
        failed_stage=GateStage.SEESAW_REGRESSION,
        decision=Decision.REJECT,
        archive_reason="real reject",
    )
    applied = GateResult(
        passed=True,
        failed_stage=None,
        decision=Decision.APPLY,
        archive_reason="real apply",
    )
    for real in (reject, applied):

        def real_gate(*a, _real=real, **k):
            return _real

        wrapped = rvp._forced_gate("off", real_gate)
        # off returns the real callable itself (identity, not a wrapper/copy)
        assert wrapped is real_gate
        # and invoking it yields the exact real GateResult object, unchanged
        got = wrapped(_Cand(), None, SuccessLedger(), [TaskEval("a", (0, 2), (0, 2))])
        assert got is real


def test_off_mode_default_gate_is_the_real_run_gate() -> None:
    """The recipe passes ``_forced_gate(mode, run_gate)``; off must yield run_gate."""
    assert rvp._forced_gate("off") is run_gate
    assert rvp._forced_gate("off", run_gate) is run_gate


# ===========================================================================
# 2. fork override on a stage-5 REJECT — synthesize improved, preserve regressed
# ===========================================================================


def test_fork_forces_a_stage5_reject_and_synthesizes_improved() -> None:
    ledger = SuccessLedger()
    ledger.record("V0", "a", 2, 2, 0)  # 'a' is ever_solved
    evals = [
        TaskEval("a", before=(2, 2), after=(0, 2)),  # was solved, now fails -> regressed
        TaskEval("b", before=(0, 2), after=(0, 2)),  # never solved, still fails
    ]
    # The real gate REJECTs (nothing improved), with regressed={a}.
    real = run_gate(_Cand(), "cfg", ledger, evals)
    assert real.decision is Decision.REJECT
    assert real.improved == frozenset()
    assert real.regressed == frozenset({"a"})

    forced = rvp._forced_gate("fork", run_gate)(_Cand(), "cfg", ledger, evals)

    assert forced.decision is Decision.FORK
    assert forced.passed is True
    assert forced.failed_stage is None
    # improved synthesised from the failed (n_pass == 0) tasks
    assert forced.improved == frozenset({"a", "b"})
    # regressed preserved from the real result
    assert forced.regressed == frozenset({"a"})
    assert "FORCED_GATE(fork)" in forced.archive_reason
    assert "real_decision=reject" in forced.archive_reason
    assert "synthesized_improved=['a', 'b']" in forced.archive_reason


def test_fork_falls_back_to_all_tasks_when_none_are_failed() -> None:
    """If every after-state passes yet the decision is still forced, use all ids."""
    ledger = SuccessLedger()
    # A stage-5 result with an empty improved is contrived via a spy so we can
    # drive the "no failed task" synthesis branch deterministically.
    real = GateResult(
        passed=False,
        failed_stage=GateStage.SEESAW_REGRESSION,
        decision=Decision.REJECT,
        archive_reason="synthetic reject",
    )

    def spy(*a, **k):
        return real

    evals = [TaskEval("x", (2, 2), (2, 2)), TaskEval("y", (2, 2), (2, 2))]
    forced = rvp._forced_gate("fork", spy)(_Cand(), "cfg", ledger, evals)
    assert forced.decision is Decision.FORK
    assert forced.improved == frozenset({"x", "y"})  # fell back to all evaluated ids
    assert "synthesized_improved=['x', 'y']" in forced.archive_reason


def test_fork_keeps_real_improved_and_is_a_noop_on_a_real_fork() -> None:
    wrapped = rvp._forced_gate("fork", run_gate)

    # real APPLY (improved={a}, no regression) -> forced FORK keeps real improved,
    # and marks nothing synthesised.
    apply_evals = [TaskEval("a", before=(0, 2), after=(2, 2))]
    real_apply = run_gate(_Cand(), "cfg", SuccessLedger(), apply_evals)
    assert real_apply.decision is Decision.APPLY
    forced = wrapped(_Cand(), "cfg", SuccessLedger(), apply_evals)
    assert forced.decision is Decision.FORK
    assert forced.improved == frozenset({"a"})
    assert "synthesized_improved=None" in forced.archive_reason
    assert "real_decision=apply" in forced.archive_reason

    # real FORK -> forced fork returns the exact object unchanged (identity).
    # Sanity: run_gate does organically produce FORK for an improve+regress split.
    ledger = SuccessLedger()
    ledger.record("V0", "keep", 2, 2, 0)  # ever_solved
    fork_evals = [
        TaskEval("keep", before=(2, 2), after=(0, 2)),  # regressed
        TaskEval("new", before=(0, 2), after=(2, 2)),   # improved
    ]
    assert run_gate(_Cand(), "cfg", ledger, fork_evals).decision is Decision.FORK
    # Identity must be checked against a single call, so use a spy that returns a
    # fixed FORK result: the wrapper must hand back that same object untouched.
    real_fork = GateResult(
        passed=True,
        failed_stage=None,
        decision=Decision.FORK,
        archive_reason="FORK: improved=['new'] regressed=['keep']",
        improved=frozenset({"new"}),
        regressed=frozenset({"keep"}),
    )
    got = rvp._forced_gate("fork", lambda *a, **k: real_fork)(
        _Cand(), "cfg", ledger, fork_evals
    )
    assert got is real_fork


# ===========================================================================
# 3. stage 1-4 failure is never overridden (integrity guard)
# ===========================================================================


def test_stage_1_to_4_failure_is_never_overridden_real_gate() -> None:
    """A real manifest-incomplete failure (decision=None) stays REJECT/None."""
    ledger = SuccessLedger()
    evals = [TaskEval("a", (0, 2), (0, 2))]
    real = run_gate(_BadManifestCand(), "cfg", ledger, evals)
    assert real.decision is None
    assert real.failed_stage is GateStage.MANIFEST_COMPLETE

    for mode in ("fork", "apply"):
        forced = rvp._forced_gate(mode, run_gate)(_BadManifestCand(), "cfg", ledger, evals)
        assert forced.decision is None  # untouched
        assert forced.failed_stage is GateStage.MANIFEST_COMPLETE
        assert "FORCED_GATE" not in forced.archive_reason


def test_stage_1_to_4_failure_object_is_returned_identically() -> None:
    """The exact stage-1..4 result object is passed through, never rebuilt."""
    stage1 = GateResult(
        passed=False,
        failed_stage=GateStage.MANIFEST_COMPLETE,
        decision=None,
        archive_reason="MANIFEST_COMPLETE: missing fields",
    )

    def spy(*a, **k):
        return stage1

    for mode in ("fork", "apply"):
        got = rvp._forced_gate(mode, spy)(
            _Cand(), "cfg", SuccessLedger(), [TaskEval("a", (0, 2), (0, 2))]
        )
        assert got is stage1


# ===========================================================================
# 4. end-to-end settlement — forced fork drives pool.fork + child ledger
# ===========================================================================


def test_forced_fork_drives_the_full_settlement_chain_through_the_engine(tmp_path) -> None:
    pool = VariantPool(K=2)
    pool.add_root(tmp_path / "V0.yaml", tmp_path / "V0.md", tasks={"a", "b"})
    ledger = SuccessLedger()
    router = Router()

    cand = _Cand("C-R0-V0", "V0", config_path=tmp_path / "candidate.yaml")
    scripted = _ScriptedOne(cand, {"a": (0, 2), "b": (0, 2)})  # all fail -> real REJECT

    engine = VariantPoolEngine(
        pool,
        ledger,
        router,
        evaluate=scripted.evaluate,
        evolve=scripted.evolve,
        gate=rvp._forced_gate("fork", run_gate),
    )
    # Mirror the recipe reconcile / batch-C: the forked child embodies the
    # candidate's config. (Engine's own _apply_candidate is a C1 no-op.)
    settled: list[tuple[str, object]] = []

    def _apply(variant, candidate):
        variant.config_path = Path(candidate.config_path)
        settled.append((variant.variant_id, candidate))

    engine._apply_candidate = _apply  # type: ignore[method-assign]

    result = engine.run_round(0, {"a", "b"})

    assert result.decisions["V0"] is Decision.FORK
    assert result.shipped is True
    assert result.forked == ["V1"]
    assert len(pool) == 2

    child = pool.variants["V1"]
    assert child.config_path == Path(cand.config_path)  # child's config == candidate's
    assert settled == [("V1", cand)]
    # synthesised improved (all failed T_k) became the child's routed tasks
    assert child.routed_tasks == {"a", "b"}
    assert pool.variants["V0"].routed_tasks == set()
    # the child's ledger got the FULL tk_eval (engine.py:494-496), failures too
    assert ledger.cell("V1", "a").attempts == 2
    assert ledger.cell("V1", "a").passes == 0
    assert ledger.cell("V1", "b").attempts == 2
    assert ledger.cell("V1", "b").passes == 0
    # the unchanged parent received none of the candidate's measurements
    assert ledger.cell("V0", "a") is None
    assert ledger.cell("V0", "b") is None
    # the forced decision is auditable on the diagnostic
    diag = result.candidate_diagnostics["C-R0-V0"]
    assert diag.decision is Decision.FORK
    assert "FORCED_GATE(fork)" in diag.archive_reason


# ===========================================================================
# 5. apply override — forced apply on a stage-5 REJECT replaces the parent config
# ===========================================================================


def test_forced_apply_settles_a_reject_as_apply_through_the_engine(tmp_path) -> None:
    pool = VariantPool(K=1)
    pool.add_root(tmp_path / "V0.yaml", tmp_path / "V0.md", tasks={"a", "b"})
    ledger = SuccessLedger()
    router = Router()

    cand = _Cand("C-R0-V0", "V0", config_path=tmp_path / "candidate.yaml")
    scripted = _ScriptedOne(cand, {"a": (0, 2), "b": (0, 2)})  # all fail -> real REJECT

    engine = VariantPoolEngine(
        pool,
        ledger,
        router,
        evaluate=scripted.evaluate,
        evolve=scripted.evolve,
        gate=rvp._forced_gate("apply", run_gate),
    )

    def _apply(variant, candidate):
        variant.config_path = Path(candidate.config_path)

    engine._apply_candidate = _apply  # type: ignore[method-assign]

    result = engine.run_round(0, {"a", "b"})

    assert result.decisions["V0"] is Decision.APPLY
    assert result.shipped is True
    assert result.forked == []
    assert len(pool) == 1
    assert pool.variants["V0"].config_path == Path(cand.config_path)  # parent replaced
    # APPLY records the candidate's scoped eval onto the (same) variant
    assert ledger.cell("V0", "a").attempts == 2
    assert ledger.cell("V0", "a").passes == 0
    diag = result.candidate_diagnostics["C-R0-V0"]
    assert diag.decision is Decision.APPLY
    assert "FORCED_GATE(apply)" in diag.archive_reason
    assert "real_decision=reject" in diag.archive_reason


# ===========================================================================
# 6. report banner + validation + recipe threading
# ===========================================================================


def test_forced_gate_banner_present_when_on_absent_when_off() -> None:
    assert rvp._forced_gate_banner("off") == ""
    for mode in ("apply", "fork"):
        banner = rvp._forced_gate_banner(mode)
        assert banner
        assert "FORCED GATE MODE" in banner
        assert mode in banner
        assert "NOT measurements" in banner


def test_forced_gate_rejects_an_unknown_mode() -> None:
    with pytest.raises(ValueError):
        rvp._forced_gate("bogus")


def test_recipe_threads_the_real_run_gate_when_off(tmp_path) -> None:
    recipe = _recipe(tmp_path)  # no force_gate attr -> off
    assert recipe.force_gate == "off"
    assert recipe.engine.gate is run_gate  # byte-identical gate path


def test_recipe_threads_the_forced_wrapper_when_on(tmp_path) -> None:
    recipe = _recipe(tmp_path, force_gate="fork")
    assert recipe.force_gate == "fork"
    assert recipe.engine.gate is not run_gate  # a wrapper was injected


def test_recipe_rejects_an_unknown_force_gate(tmp_path) -> None:
    with pytest.raises(ValueError):
        _recipe(tmp_path, force_gate="bogus")
