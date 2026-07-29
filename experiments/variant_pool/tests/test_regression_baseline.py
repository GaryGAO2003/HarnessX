# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""Offline tests for M-23 — ``--regression-baseline {global, per_variant}``.

Fully offline. Exercises (1) the gate's seesaw regression predicate under both
baselines, including the double-blind NEW-1 asymmetry (a task solved only by a
*different* variant is a regression under ``global`` but not under
``per_variant``), (2) the engine forwarding the flag to its gate only when it is
non-default so a ``global`` run's gate call stays byte-identical, and (3) the
recipe flag / provenance / resume guardrail. ``global`` (default) is
byte-identical throughout.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from variant_pool.engine import VariantPoolEngine
from variant_pool.gate import (
    DEFAULT_REGRESSION_BASELINE,
    REGRESSION_BASELINE_GLOBAL,
    REGRESSION_BASELINE_MODES,
    REGRESSION_BASELINE_PER_VARIANT,
    Decision,
    GateResult,
    TaskEval,
    _classify,
    _seesaw_three_way,
    run_gate,
)
from variant_pool.ledger import SuccessLedger
from variant_pool.pool import VariantPool
from variant_pool.router import Router

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from recipe.gaia_evolver import run_variant_pool as rvp  # noqa: E402
from experiments.variant_pool.experiment_lock import ExperimentLock  # noqa: E402
from experiments.variant_pool.resume import lock_blocking_diffs  # noqa: E402


# ---------------------------------------------------------------------------
# The M-23 signature fixture (double-blind audit NEW-1)
# ---------------------------------------------------------------------------
#
# K>=2. Task ``T`` was solved by variant j (V0) and is in the *global*
# ever_solved set, but the candidate under audit targets variant k (V1) which
# has never solved ``T`` — so its ``before`` is ``(0, 2)`` (exactly what the
# engine's ``_task_eval`` derives from V1's per-variant cell). ``T`` fails
# (``after == 0``); ``U`` is a fresh improvement.


def _v0_solved_T_ledger() -> SuccessLedger:
    """A ledger whose global ever_solved is {T}: V0 (variant j) solved T."""
    ledger = SuccessLedger()
    ledger.record("V0", "T", n_pass=2, n_att=2, round_idx=0)
    return ledger


def _tk_for_variant_k() -> list[TaskEval]:
    return [
        TaskEval("U", before=(0, 2), after=(2, 2)),  # fresh improvement
        TaskEval("T", before=(0, 2), after=(0, 2)),  # k never solved T; now fails
    ]


# ===========================================================================
# 1. gate seesaw — the load-bearing behaviour
# ===========================================================================


def test_default_baseline_is_global_and_byte_identical() -> None:
    assert DEFAULT_REGRESSION_BASELINE == REGRESSION_BASELINE_GLOBAL == "global"
    ledger = _v0_solved_T_ledger()
    tk = _tk_for_variant_k()
    # omitting the kwarg is the same code path as passing "global": both read
    # the global ever_solved set.
    assert _classify(tk, ledger) == _classify(tk, ledger, regression_baseline="global")
    improved, regressed = _classify(tk, ledger)
    assert improved == {"U"}
    assert regressed == {"T"}  # T is globally ever-solved (by V0)


def test_per_variant_baseline_ignores_a_sibling_variants_solve() -> None:
    """NEW-1: T solved only by V0 is *not* V1's regression under per_variant."""
    ledger = _v0_solved_T_ledger()
    tk = _tk_for_variant_k()

    g_improved, g_regressed = _classify(tk, ledger, regression_baseline="global")
    pv_improved, pv_regressed = _classify(tk, ledger, regression_baseline="per_variant")

    assert g_improved == pv_improved == {"U"}
    assert g_regressed == {"T"}  # global: any variant's solve counts
    assert pv_regressed == set()  # per_variant: V1 never solved T, so no regression


def test_baseline_flips_the_seesaw_decision() -> None:
    """The same candidate FORKs under global but APPLYs under per_variant."""
    ledger = _v0_solved_T_ledger()
    tk = _tk_for_variant_k()
    # global: improves U, regresses T -> conflict -> FORK.
    assert _seesaw_three_way(tk, ledger, regression_baseline="global") is Decision.FORK
    # per_variant: improves U, regresses nothing -> APPLY.
    assert _seesaw_three_way(tk, ledger, regression_baseline="per_variant") is Decision.APPLY


def test_run_gate_threads_the_baseline_end_to_end() -> None:
    """An opaque candidate through the full gate: decision follows the baseline."""
    ledger = _v0_solved_T_ledger()
    candidate = SimpleNamespace(candidate_id="C0")  # opaque: no .manifest -> seesaw only

    g = run_gate(candidate, "cfg", ledger, _tk_for_variant_k(), regression_baseline="global")
    pv = run_gate(candidate, "cfg", ledger, _tk_for_variant_k(), regression_baseline="per_variant")

    assert g.decision is Decision.FORK
    assert g.regressed == frozenset({"T"})
    assert pv.decision is Decision.APPLY
    assert pv.regressed == frozenset()


@pytest.mark.parametrize("mode", ["", "GLOBAL", "per-variant", "shipped_only", None])
def test_invalid_baseline_is_rejected(mode) -> None:
    ledger = SuccessLedger()
    tk = [TaskEval("a", before=(0, 2), after=(2, 2))]
    with pytest.raises(ValueError):
        _seesaw_three_way(tk, ledger, regression_baseline=mode)
    with pytest.raises(ValueError):
        run_gate(SimpleNamespace(), "cfg", ledger, tk, regression_baseline=mode)


# ===========================================================================
# 2. engine forwarding — non-default only, so global stays byte-identical
# ===========================================================================


class _OpaqueCand:
    def __init__(self, candidate_id: str, target_variant: str) -> None:
        self.candidate_id = candidate_id
        self.target_variant = target_variant


def _one_variant_engine(gate, regression_baseline: str) -> VariantPoolEngine:
    pool = VariantPool(K=4)
    pool.add_root("cfg/V0.yaml", "j/V0.md", tasks={"a"})

    def _evolve(variant, round_idx):  # noqa: ARG001 - scripted
        return _OpaqueCand("C0", variant.variant_id)

    def _evaluate(candidate, t_k, round_idx):  # noqa: ARG001 - scripted
        return {task: (2, 2) for task in t_k}

    return VariantPoolEngine(
        pool,
        SuccessLedger(),
        Router(routing_mode="task_tournament"),
        evaluate=_evaluate,
        evolve=_evolve,
        gate=gate,
        regression_baseline=regression_baseline,
    )


def test_engine_rejects_unknown_regression_baseline() -> None:
    with pytest.raises(ValueError):
        _one_variant_engine(run_gate, "bogus")


def test_engine_forwards_baseline_to_gate_only_when_non_default() -> None:
    captured: list[dict] = []

    def _spy_gate(candidate, parent_config, ledger, tk_results, **kwargs):  # noqa: ANN001, ARG001
        captured.append(dict(kwargs))
        return GateResult(
            passed=True, failed_stage=None, decision=Decision.REJECT, archive_reason="spy"
        )

    _one_variant_engine(_spy_gate, REGRESSION_BASELINE_GLOBAL).run(tasks={"a"}, num_rounds=1)
    assert captured, "gate was never called"
    # global: the call is byte-identical to the pre-M-23 recipe (kwarg omitted).
    assert "regression_baseline" not in captured[0]

    captured.clear()
    _one_variant_engine(_spy_gate, REGRESSION_BASELINE_PER_VARIANT).run(tasks={"a"}, num_rounds=1)
    assert captured, "gate was never called"
    assert captured[0]["regression_baseline"] == REGRESSION_BASELINE_PER_VARIANT


# ===========================================================================
# 3. recipe flag / provenance / resume guardrail
# ===========================================================================


def test_regression_baseline_flag_default_and_choices() -> None:
    parser = rvp.build_arg_parser()
    assert parser.parse_args([]).regression_baseline == "global"
    assert (
        parser.parse_args(["--regression-baseline", "per_variant"]).regression_baseline
        == "per_variant"
    )
    with pytest.raises(SystemExit):
        parser.parse_args(["--regression-baseline", "per-variant"])


def test_regression_baseline_provenance_is_none_for_default() -> None:
    assert rvp._regression_baseline_provenance("global") is None
    warn = rvp._regression_baseline_provenance("per_variant")
    assert warn is not None
    assert "regression_baseline=per_variant" in warn
    assert "byte-for-byte" in warn


class _LockArgs:
    """Minimal CLI namespace covering the fields ``_build_experiment_lock`` reads."""

    def __init__(self, **overrides) -> None:
        self.pool_k = 2
        self.pass_k = 2
        self.num_rounds = 1
        self.max_steps = 20
        self.concurrency = 2
        self.evolve_steps = 200
        self.patience = 1
        self.seed = 0
        self.estimator = "laplace"
        self.candidate_mode = "legacy_single"
        self.candidates_per_round = 4
        self.model = "task-model"
        self.meta_model = "meta-model"
        self.provider_id = "provider"
        self.api_base = None
        self.data_path = None
        self.planned_seeds = (0, 1, 2)
        self.__dict__.update(overrides)


def _make_lock(tmp_path: Path, **overrides) -> ExperimentLock:
    baseline = tmp_path / "V0" / "config.yaml"
    baseline.parent.mkdir(parents=True, exist_ok=True)
    baseline.write_text("baseline: true\n", encoding="utf-8")
    fake_base = SimpleNamespace(processors=[], tool_registry=None)
    return rvp._build_experiment_lock(
        args=_LockArgs(**overrides),
        run_tag="test-regression-baseline",
        baseline_config_path=baseline,
        original_base=fake_base,
    )


def test_lock_records_regression_baseline_only_when_per_variant(tmp_path) -> None:
    off_lock = _make_lock(tmp_path)  # default -> global
    assert not any("regression_baseline" in w for w in off_lock.provenance_warnings)

    on_lock = _make_lock(tmp_path, regression_baseline="per_variant")
    hits = [w for w in on_lock.provenance_warnings if "regression_baseline=per_variant" in w]
    assert len(hits) == 1

    # the record survives the lock's own JSON round-trip
    back = ExperimentLock.from_json(on_lock.to_json())
    assert any("regression_baseline=per_variant" in w for w in back.provenance_warnings)


def test_resume_guardrail_blocks_a_baseline_swap(tmp_path) -> None:
    """Resuming a global run under per_variant is blocked (change-baseline continuation)."""
    existing = _make_lock(tmp_path)  # global
    new = _make_lock(tmp_path, regression_baseline="per_variant")
    blocking = lock_blocking_diffs(new, existing)
    assert any(entry.startswith("provenance_warnings") for entry in blocking)
    # and the reverse direction is equally blocked
    assert any(entry.startswith("provenance_warnings") for entry in lock_blocking_diffs(existing, new))


def test_recipe_wrappers_forward_the_baseline_to_the_real_gate() -> None:
    """Both recipe-layer gate wrappers thread regression_baseline through."""
    captured: list[dict] = []

    def _spy_real_gate(candidate, parent_config, ledger, tk_results, **kwargs):  # noqa: ANN001, ARG001
        captured.append(dict(kwargs))
        return GateResult(
            passed=True, failed_stage=None, decision=Decision.APPLY, archive_reason="spy"
        )

    evals = [TaskEval("a", before=(0, 2), after=(2, 2))]

    # _forced_gate (active mode) wraps and must forward.
    forced = rvp._forced_gate("apply", _spy_real_gate)
    forced(SimpleNamespace(), "cfg", SuccessLedger(), evals, regression_baseline="per_variant")
    assert captured[-1]["regression_baseline"] == "per_variant"

    # _l2_certifying_gate (active recipe) wraps and must forward.
    recipe = SimpleNamespace(l2_cert="auto", manifest_mode="repo", candidate_mode="paper")
    certifying = rvp._l2_certifying_gate(recipe, _spy_real_gate)
    certifying(SimpleNamespace(), "cfg", SuccessLedger(), evals, regression_baseline="per_variant")
    assert captured[-1]["regression_baseline"] == "per_variant"
