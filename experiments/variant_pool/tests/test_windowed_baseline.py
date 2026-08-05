# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""Offline tests for batch-2a Item 1 — ``--regression-baseline windowed``.

Fully offline (no rollouts, no LLM, no network). ``windowed`` is the official
adjacent-round watchlist semantics ported from
``upstream/feat/aegis:harnessx/aegis/data/regressions.py`` (compare round N-1 vs
N only, no accumulated solved set). These tests pin:

1. the gate seesaw predicate under ``windowed`` versus ``global`` / ``per_variant``
   — the load-bearing behaviour: a stale one-off solve blocks under ``global``
   (and ``per_variant``) but not under ``windowed``;
2. the honest variant-agnostic divergence from the official "same variant" clause
   (the gate is never handed the target ``variant_id``);
3. the recipe flag choice + provenance.

``global`` (default) and ``per_variant`` (M-23) stay byte-identical throughout.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from variant_pool.gate import (
    DEFAULT_REGRESSION_BASELINE,
    REGRESSION_BASELINE_GLOBAL,
    REGRESSION_BASELINE_MODES,
    REGRESSION_BASELINE_PER_VARIANT,
    REGRESSION_BASELINE_WINDOWED,
    Decision,
    TaskEval,
    _classify,
    _seesaw_three_way,
    _solved_in_previous_settled_round,
    run_gate,
)
from variant_pool.ledger import SuccessLedger

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from recipe.gaia_evolver import run_variant_pool as rvp  # noqa: E402


# ---------------------------------------------------------------------------
# constants
# ---------------------------------------------------------------------------


def test_windowed_is_a_third_mode_and_default_is_still_global() -> None:
    assert REGRESSION_BASELINE_WINDOWED == "windowed"
    assert REGRESSION_BASELINE_WINDOWED in REGRESSION_BASELINE_MODES
    assert REGRESSION_BASELINE_MODES == (
        REGRESSION_BASELINE_GLOBAL,
        REGRESSION_BASELINE_PER_VARIANT,
        REGRESSION_BASELINE_WINDOWED,
    )
    assert DEFAULT_REGRESSION_BASELINE == REGRESSION_BASELINE_GLOBAL


# ---------------------------------------------------------------------------
# the load-bearing case: a stale one-off solve
# ---------------------------------------------------------------------------


def _stale_solve_ledger() -> SuccessLedger:
    """V1 solved T once in R2 (luck), then failed it every round R3..R9."""
    ledger = SuccessLedger()
    ledger.record("V1", "T", n_pass=2, n_att=2, round_idx=2)
    for rnd in range(3, 10):
        ledger.record("V1", "T", n_pass=0, n_att=2, round_idx=rnd)
    # U is a fresh, still-unsolved task the R10 candidate improves.
    ledger.record("V1", "U", n_pass=0, n_att=2, round_idx=9)
    return ledger


def _tk_r10() -> list[TaskEval]:
    # before mirrors engine._task_eval: V1's accumulated cell.passes>=1 on T
    # (from R2) makes before=(2,2); U never solved -> (0,2). T now fails.
    return [
        TaskEval("U", before=(0, 2), after=(2, 2)),
        TaskEval("T", before=(2, 2), after=(0, 2)),
    ]


def test_windowed_ignores_a_stale_solve_that_global_and_per_variant_block() -> None:
    """R2-by-luck, failed since: global/per_variant block in R10, windowed does not."""
    ledger = _stale_solve_ledger()
    tk = _tk_r10()

    g_improved, g_regressed = _classify(tk, ledger, regression_baseline="global")
    pv_improved, pv_regressed = _classify(tk, ledger, regression_baseline="per_variant")
    w_improved, w_regressed = _classify(tk, ledger, regression_baseline="windowed")

    assert g_improved == pv_improved == w_improved == {"U"}
    # global: T is in the cross-variant ever_solved set (solved in R2).
    assert g_regressed == {"T"}
    # per_variant: V1's accumulated cell still shows a pass (R2) -> regression.
    assert pv_regressed == {"T"}
    # windowed: T was NOT solved in the immediately previous round (R9 failed).
    assert w_regressed == set()


def test_windowed_default_kwarg_is_global_and_unchanged() -> None:
    ledger = _stale_solve_ledger()
    tk = _tk_r10()
    # omitting the kwarg is the same code path as passing "global".
    assert _classify(tk, ledger) == _classify(tk, ledger, regression_baseline="global")


def test_windowed_flips_the_seesaw_decision() -> None:
    """Same candidate: FORK under global (improve U, regress T) but APPLY under windowed."""
    ledger = _stale_solve_ledger()
    tk = _tk_r10()
    assert _seesaw_three_way(tk, ledger, regression_baseline="global") is Decision.FORK
    assert _seesaw_three_way(tk, ledger, regression_baseline="windowed") is Decision.APPLY


def test_windowed_blocks_when_the_task_was_solved_in_the_previous_round() -> None:
    """A solve in the immediately previous round (R9) IS a windowed regression."""
    ledger = SuccessLedger()
    ledger.record("V1", "T", n_pass=2, n_att=2, round_idx=9)  # solved last round
    ledger.record("V1", "U", n_pass=0, n_att=2, round_idx=9)
    tk = [
        TaskEval("U", before=(0, 2), after=(2, 2)),
        TaskEval("T", before=(2, 2), after=(0, 2)),
    ]
    for mode in ("global", "per_variant", "windowed"):
        _improved, regressed = _classify(tk, ledger, regression_baseline=mode)
        assert regressed == {"T"}, mode


def test_windowed_is_variant_agnostic_on_the_previous_round() -> None:
    """Documented divergence from official 'same variant': a prior-round solve by a
    *sibling* variant still blocks under windowed (the gate is not passed variant_id),
    where per_variant would not. windowed matches global's verdict here."""
    ledger = SuccessLedger()
    ledger.record("V0", "T", n_pass=2, n_att=2, round_idx=9)  # sibling solved T last round
    ledger.record("V1", "U", n_pass=0, n_att=2, round_idx=9)  # target V1; never solved T
    tk = [
        TaskEval("U", before=(0, 2), after=(2, 2)),
        TaskEval("T", before=(0, 2), after=(0, 2)),  # V1 never solved T -> before (0,2)
    ]
    _gi, g_regressed = _classify(tk, ledger, regression_baseline="global")
    _pi, pv_regressed = _classify(tk, ledger, regression_baseline="per_variant")
    _wi, w_regressed = _classify(tk, ledger, regression_baseline="windowed")
    assert g_regressed == {"T"}  # any variant ever solved it
    assert pv_regressed == set()  # V1 itself never solved it
    assert w_regressed == {"T"}  # any variant solved it *last round*


def test_solved_in_previous_settled_round_helper_edges() -> None:
    empty = SuccessLedger()
    assert _solved_in_previous_settled_round("T", empty) is False  # no previous round

    ledger = SuccessLedger()
    ledger.record("V1", "T", n_pass=1, n_att=2, round_idx=5)  # partial pass last round
    assert _solved_in_previous_settled_round("T", ledger) is True  # n_pass>=1 counts
    assert _solved_in_previous_settled_round("MISSING", ledger) is False


def test_windowed_run_gate_end_to_end() -> None:
    ledger = _stale_solve_ledger()
    candidate = SimpleNamespace(candidate_id="C0")  # opaque -> seesaw only
    result = run_gate(
        candidate, "cfg", ledger, _tk_r10(), regression_baseline="windowed"
    )
    assert result.decision is Decision.APPLY
    assert result.regressed == frozenset()


# ---------------------------------------------------------------------------
# recipe flag / provenance
# ---------------------------------------------------------------------------


def test_windowed_flag_choice_is_accepted() -> None:
    parser = rvp.build_arg_parser()
    assert parser.parse_args([]).regression_baseline == "global"
    assert (
        parser.parse_args(["--regression-baseline", "windowed"]).regression_baseline
        == "windowed"
    )
    with pytest.raises(SystemExit):
        parser.parse_args(["--regression-baseline", "window"])


def test_windowed_provenance_records_and_default_is_none() -> None:
    assert rvp._regression_baseline_provenance("global") is None
    warn = rvp._regression_baseline_provenance("windowed")
    assert warn is not None
    assert "regression_baseline=windowed" in warn
    assert "byte-for-byte" in warn
    # distinct from per_variant's text (adjacent-round vs own-variant framing).
    assert "immediately previous settled round" in warn
