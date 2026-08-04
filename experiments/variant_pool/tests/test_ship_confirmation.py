# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""Offline tests for the pre-ship full-bed gate (``--ship-confirmation full_bed``).

The problem: candidates are gated on their routed window ``T_k`` (~12 of 103
tasks in e_pervar3), so a window APPLY/FORK is blind to what the edit does to the
~90 tasks routed elsewhere. ``full_bed`` makes the window a cheap pre-filter and
the whole task bed the verdict: any candidate about to ship is re-evaluated on
the full bed, re-classified, and re-decided; that verdict REPLACES the window
decision, and a full-bed REJECT archives the candidate under
``GateStage.SHIP_CONFIRM``. Window REJECTs are never confirmed.

These tests drive the engine with a fixed routing map (so the window is a strict
subset of the bed) and a scripted evaluator that answers window and confirm
calls independently — exactly the divergence the feature exists to catch.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from variant_pool.engine import DEFAULT_MIN_FORK, VariantPoolEngine
from variant_pool.gate import Decision, GateStage
from variant_pool.ledger import SuccessLedger
from variant_pool.pool import VariantPool
from variant_pool.reporting import CandidateTaskResult
from variant_pool.router import Router

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from recipe.gaia_evolver import run_variant_pool as rvp  # noqa: E402


# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------


class _Cand:
    """A candidate the gate treats opaquely (not a ChangeManifest)."""

    def __init__(self, candidate_id: str, target_variant: str) -> None:
        self.candidate_id = candidate_id
        self.target_variant = target_variant


class _FixedRouter(Router):
    """Return a predetermined frozen routing map (narrows the window at will)."""

    def __init__(self, routing: dict[str, str], **kwargs) -> None:
        kwargs.setdefault("routing_mode", "task_tournament")
        super().__init__(**kwargs)
        self._routing = dict(routing)

    def freeze_routing(self, tasks, pool, ledger, round_idx):  # noqa: ARG002 - fixed map
        return {task: self._routing[task] for task in tasks if task in self._routing}


class _Driver:
    """Scripted ``evolve``/``evaluate`` that answers window vs confirm separately.

    ``evolve`` proposes the one candidate for ``target`` only. ``evaluate``
    returns ``window`` outcomes for a window (default-phase) call and ``full``
    outcomes for a ``phase='confirm'`` call, logging the two call kinds apart so a
    test can assert confirmation ran (or did not) and was counted separately.
    """

    def __init__(
        self,
        *,
        target: str,
        window: dict[str, tuple[int, int]],
        full: dict[str, tuple[int, int]],
        candidate_id: str = "C-conf",
    ) -> None:
        self.target = target
        self.window = window
        self.full = full
        self.cand = _Cand(candidate_id, target)
        self.window_calls: list[frozenset[str]] = []
        self.confirm_calls: list[frozenset[str]] = []

    def evolve(self, variant, round_idx):  # noqa: ARG002 - scripted round
        return self.cand if variant.variant_id == self.target else None

    def evaluate(self, candidate, t_k, round_idx, phase="window"):  # noqa: ARG002
        if phase == rvp.SHIP_CONFIRM_PHASE:
            self.confirm_calls.append(frozenset(t_k))
            return {task: self.full[task] for task in t_k}
        self.window_calls.append(frozenset(t_k))
        return {task: self.window[task] for task in t_k}


def _engine(
    routing: dict[str, str],
    driver: _Driver,
    *,
    ship_confirmation: str,
    min_fork: tuple[int, int] = DEFAULT_MIN_FORK,
    seed_solved: tuple[tuple[str, str], ...] = (),
    K: int = 8,
) -> tuple[VariantPoolEngine, VariantPool, SuccessLedger]:
    """Build a two-variant pool + engine; ``seed_solved`` marks ever-solved cells."""
    ledger = SuccessLedger()
    for variant_id, task_id in seed_solved:
        ledger.record(variant_id, task_id, 2, 2, 0)  # prior-round ever-solved seed
    router = _FixedRouter(routing)
    pool = VariantPool(K=K)
    pool.add_root("cfg/V0.yaml", "j/V0.md", tasks=set())
    pool.fork("V0", set(), 0)  # bring V1 into being so the bed can route off-window
    engine = VariantPoolEngine(
        pool,
        ledger,
        router,
        evaluate=driver.evaluate,
        evolve=driver.evolve,
        min_fork=min_fork,
        ship_confirmation=ship_confirmation,
    )
    return engine, pool, ledger


# ===========================================================================
# 1. off is byte-identical: the window decision ships, confirmation never runs
# ===========================================================================


def test_off_ships_the_window_decision_and_never_confirms() -> None:
    """``off`` enacts the window APPLY even though the full bed would reject it.

    Same inputs, the two modes diverge: ``off`` ships the window verdict and the
    confirm evaluator is never called; ``full_bed`` overturns it. This is the
    explicit byte-identical-off check — no confirmation, no counter, no meta.
    """
    routing = {"w1": "V0", "o1": "V1"}
    seed = (("V1", "o1"),)  # o1 is ever-solved, so an off-window failure regresses

    off = _Driver(window={"w1": (2, 2)}, full={"w1": (2, 2), "o1": (0, 2)}, target="V0")
    engine, pool, _ = _engine(
        routing, off, ship_confirmation="off", min_fork=(2, 2), seed_solved=seed
    )
    result = engine.run_round(1, {"w1", "o1"})

    assert result.decisions["V0"] is Decision.APPLY
    assert result.shipped is True
    assert off.confirm_calls == []  # confirmation never invoked when off
    assert result.candidate_diagnostics["C-conf"].ship_confirm is None
    assert len(pool) == 2

    # full_bed on identical inputs overturns the very same window ship.
    on = _Driver(window={"w1": (2, 2)}, full={"w1": (2, 2), "o1": (0, 2)}, target="V0")
    engine2, _, _ = _engine(
        routing, on, ship_confirmation="full_bed", min_fork=(2, 2), seed_solved=seed
    )
    result2 = engine2.run_round(1, {"w1", "o1"})
    assert result2.decisions["V0"] is Decision.REJECT
    assert result2.shipped is False
    assert on.confirm_calls != []


# ===========================================================================
# 2. window APPLY + full-bed APPLY -> ships, meta records both verdicts
# ===========================================================================


def test_window_apply_full_apply_ships_and_records_both_verdicts() -> None:
    routing = {"w1": "V0", "o1": "V1", "o2": "V1"}
    driver = _Driver(
        window={"w1": (2, 2)},
        full={"w1": (2, 2), "o1": (2, 2), "o2": (2, 2)},
        target="V0",
    )
    engine, pool, _ = _engine(routing, driver, ship_confirmation="full_bed")
    result = engine.run_round(1, {"w1", "o1", "o2"})

    assert result.decisions["V0"] is Decision.APPLY
    assert result.shipped is True
    # confirmation is a separate call on the whole bed, not folded into the window
    assert driver.window_calls == [frozenset({"w1"})]
    assert driver.confirm_calls == [frozenset({"w1", "o1", "o2"})]

    meta = result.candidate_diagnostics["C-conf"].ship_confirm
    assert meta == {
        "mode": "full_bed",
        "window_decision": "apply",
        "full_decision": "apply",
        "window_improved": 1,
        "window_regressed": 0,
        "full_improved": 3,
        "full_regressed": 0,
    }


# ===========================================================================
# 3. window APPLY + full-bed REJECT -> archived SHIP_CONFIRM, nothing enacted
# ===========================================================================


def test_window_apply_full_reject_archives_ship_confirm() -> None:
    routing = {"w1": "V0", "o1": "V1"}
    driver = _Driver(
        window={"w1": (2, 2)},
        full={"w1": (2, 2), "o1": (0, 2)},  # o1 (ever-solved) regresses off-window
        target="V0",
    )
    engine, pool, _ = _engine(
        routing,
        driver,
        ship_confirmation="full_bed",
        min_fork=(2, 2),  # improved=1/regressed=1 is below the fork threshold -> reject
        seed_solved=(("V1", "o1"),),
    )
    result = engine.run_round(1, {"w1", "o1"})

    diag = result.candidate_diagnostics["C-conf"]
    assert diag.decision is Decision.REJECT
    assert diag.failed_stage is GateStage.SHIP_CONFIRM
    assert "SHIP_CONFIRM" in diag.archive_reason
    assert "window said apply" in diag.archive_reason
    assert "full bed said reject" in diag.archive_reason
    assert "improved=['w1'] regressed=[]" in diag.archive_reason  # window verdict
    assert "improved=['w1'] regressed=['o1']" in diag.archive_reason  # full verdict
    assert diag.ship_confirm["window_decision"] == "apply"
    assert diag.ship_confirm["full_decision"] == "reject"

    # nothing enacted: no ship, no fork, pool unchanged
    assert result.decisions["V0"] is Decision.REJECT
    assert result.shipped is False
    assert result.forked == []
    assert len(pool) == 2
    assert "V0" not in result.selected_candidate_ids


# ===========================================================================
# 4. window FORK + full-bed REJECT -> same overturn under SHIP_CONFIRM
# ===========================================================================


def test_window_fork_full_reject_archives_ship_confirm() -> None:
    routing = {"a": "V0", "c": "V0", "o1": "V1"}
    driver = _Driver(
        # window: a unlocks, c (ever-solved) regresses -> FORK at (1, 1)
        window={"a": (2, 2), "c": (0, 2)},
        # full bed: a's win does not replicate, so no task improves -> REJECT
        full={"a": (0, 2), "c": (0, 2), "o1": (0, 2)},
        target="V0",
    )
    engine, pool, _ = _engine(
        routing, driver, ship_confirmation="full_bed", seed_solved=(("V1", "c"),)
    )
    result = engine.run_round(1, {"a", "c", "o1"})

    diag = result.candidate_diagnostics["C-conf"]
    assert diag.decision is Decision.REJECT
    assert diag.failed_stage is GateStage.SHIP_CONFIRM
    assert "window said fork" in diag.archive_reason
    assert "full bed said reject" in diag.archive_reason
    assert diag.ship_confirm["window_decision"] == "fork"
    assert diag.ship_confirm["full_decision"] == "reject"

    assert result.shipped is False
    assert result.forked == []
    assert len(pool) == 2  # no child spawned


# ===========================================================================
# 5. window FORK + full-bed APPLY -> the full bed replaces the decision
# ===========================================================================


def test_window_fork_full_apply_enacts_apply() -> None:
    routing = {"a": "V0", "c": "V0", "o1": "V1"}
    driver = _Driver(
        # window: a unlocks, c (ever-solved) regresses -> FORK at (1, 1)
        window={"a": (2, 2), "c": (0, 2)},
        # full bed: c is actually solved too and o1 unlocks, no regression -> APPLY
        full={"a": (2, 2), "c": (2, 2), "o1": (2, 2)},
        target="V0",
    )
    engine, pool, _ = _engine(
        routing, driver, ship_confirmation="full_bed", seed_solved=(("V1", "c"),)
    )
    result = engine.run_round(1, {"a", "c", "o1"})

    # the full-bed APPLY replaces the window FORK: apply in place, never fork
    assert result.decisions["V0"] is Decision.APPLY
    assert result.shipped is True
    assert result.forked == []
    assert len(pool) == 2

    meta = result.candidate_diagnostics["C-conf"].ship_confirm
    assert meta["window_decision"] == "fork"
    assert meta["full_decision"] == "apply"


# ===========================================================================
# 6. window REJECT -> confirmation is never invoked (no extra cost)
# ===========================================================================


def test_window_reject_never_confirms() -> None:
    routing = {"a": "V0", "o1": "V1"}
    driver = _Driver(
        window={"a": (0, 2)},  # nothing improves -> window REJECT
        full={"a": (2, 2), "o1": (2, 2)},  # would look great, but must never run
        target="V0",
    )
    engine, _, _ = _engine(routing, driver, ship_confirmation="full_bed")
    result = engine.run_round(1, {"a", "o1"})

    assert result.decisions["V0"] is Decision.REJECT
    assert driver.confirm_calls == []  # the confirm evaluator was never called
    assert result.candidate_diagnostics["C-conf"].ship_confirm is None


# ===========================================================================
# 7. constructor validation
# ===========================================================================


def test_engine_rejects_unknown_ship_confirmation_mode() -> None:
    driver = _Driver(window={"a": (2, 2)}, full={"a": (2, 2)}, target="V0")
    ledger = SuccessLedger()
    pool = VariantPool(K=2)
    pool.add_root("cfg/V0.yaml", "j/V0.md", tasks={"a"})
    with pytest.raises(ValueError, match="ship_confirmation must be one of"):
        VariantPoolEngine(
            pool,
            ledger,
            _FixedRouter({"a": "V0"}),
            evaluate=driver.evaluate,
            evolve=driver.evolve,
            ship_confirmation="next_round",  # reserved, not implemented here
        )


# ===========================================================================
# 8. reporting: confirm fields are omitted from to_dict unless confirmation ran
# ===========================================================================


def test_candidate_task_result_omits_confirm_fields_when_unset() -> None:
    off_row = CandidateTaskResult(
        task_id="t",
        round_idx=1,
        candidate_id="C",
        target_variant_id="V0",
        n_att=2,
        n_pass=2,
        decision="apply",
    ).to_dict()
    assert "confirm_attempts" not in off_row  # byte-identical off
    assert "ship_confirm" not in off_row

    on_row = CandidateTaskResult(
        task_id="t",
        round_idx=1,
        candidate_id="C",
        target_variant_id="V0",
        n_att=2,
        n_pass=2,
        decision="reject",
        failed_stage="SHIP_CONFIRM",
        confirm_attempts=6,
        ship_confirm={"mode": "full_bed", "window_decision": "apply", "full_decision": "reject"},
    ).to_dict()
    assert on_row["confirm_attempts"] == 6
    assert on_row["ship_confirm"]["full_decision"] == "reject"


def test_candidate_task_result_rejects_negative_confirm_attempts() -> None:
    with pytest.raises(ValueError, match="confirm_attempts must be >= 0"):
        CandidateTaskResult(
            task_id="t",
            round_idx=1,
            candidate_id="C",
            target_variant_id="V0",
            n_att=2,
            n_pass=2,
            confirm_attempts=-1,
        )


# ===========================================================================
# 9. recipe: byte-safe lock / provenance record
# ===========================================================================


def test_ship_confirmation_provenance_off_none_on_warning() -> None:
    assert rvp._ship_confirmation_provenance("off") is None
    warn = rvp._ship_confirmation_provenance("full_bed")
    assert warn is not None
    assert "ship_confirmation=full_bed" in warn
    assert "confirm_attempts" in warn
    assert "SHIP_CONFIRM" in warn


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
        self.ship_confirmation = "off"
        self.__dict__.update(overrides)


def _make_lock(tmp_path: Path, **overrides):
    baseline = tmp_path / "V0" / "config.yaml"
    baseline.parent.mkdir(parents=True, exist_ok=True)
    baseline.write_text("baseline: true\n", encoding="utf-8")
    import types

    fake_base = types.SimpleNamespace(processors=[], tool_registry=None)
    return rvp._build_experiment_lock(
        args=_LockArgs(**overrides),
        run_tag="test-ship-confirmation",
        baseline_config_path=baseline,
        original_base=fake_base,
    )


def test_lock_records_ship_confirmation_only_when_on(tmp_path) -> None:
    off_lock = _make_lock(tmp_path, ship_confirmation="off")
    assert not any("ship_confirmation" in w for w in off_lock.provenance_warnings)

    on_lock = _make_lock(tmp_path, ship_confirmation="full_bed")
    hits = [w for w in on_lock.provenance_warnings if "ship_confirmation=full_bed" in w]
    assert len(hits) == 1

    # the record survives the lock's own JSON round-trip
    from experiments.variant_pool.experiment_lock import ExperimentLock

    back = ExperimentLock.from_json(on_lock.to_json())
    assert any("ship_confirmation=full_bed" in w for w in back.provenance_warnings)


# ===========================================================================
# 10. argparse: the flag exists, defaults off, and rejects unknown modes
# ===========================================================================


def test_parser_ship_confirmation_default_and_choices() -> None:
    parser = rvp.build_arg_parser()
    default_args = parser.parse_args([])
    assert default_args.ship_confirmation == "off"

    on_args = parser.parse_args(["--ship-confirmation", "full_bed"])
    assert on_args.ship_confirmation == "full_bed"

    with pytest.raises(SystemExit):
        parser.parse_args(["--ship-confirmation", "next_round"])  # reserved, not a choice
