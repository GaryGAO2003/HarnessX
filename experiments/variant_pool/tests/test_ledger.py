# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""Offline unit tests for ``variant_pool.ledger`` (W3 + W21).

Pure in-memory arithmetic: no LLM, no network, no files.
"""

from __future__ import annotations

import pytest

from variant_pool.ledger import DEFAULT_STALE_PRIOR, CellStats, SuccessLedger


# ---------------------------------------------------------------------------
# record()
# ---------------------------------------------------------------------------


def test_record_accumulates_pass_at_2_rollouts() -> None:
    ledger = SuccessLedger()
    ledger.record("V0", "t1", n_pass=1, n_att=2, round_idx=0)
    ledger.record("V0", "t1", n_pass=2, n_att=2, round_idx=1)

    cell = ledger.cell("V0", "t1")
    assert cell == CellStats(passes=3, attempts=4, last_round=1)


def test_record_keeps_the_highest_round_seen() -> None:
    """Out-of-order writes must not walk ``last_round`` backwards."""
    ledger = SuccessLedger()
    ledger.record("V0", "t1", n_pass=0, n_att=2, round_idx=5)
    ledger.record("V0", "t1", n_pass=0, n_att=2, round_idx=3)
    assert ledger.cell("V0", "t1").last_round == 5


def test_record_rejects_impossible_counts() -> None:
    ledger = SuccessLedger()
    with pytest.raises(ValueError):
        ledger.record("V0", "t1", n_pass=3, n_att=2, round_idx=0)
    with pytest.raises(ValueError):
        ledger.record("V0", "t1", n_pass=-1, n_att=2, round_idx=0)


def test_cells_are_isolated_per_variant_and_task() -> None:
    ledger = SuccessLedger()
    ledger.record("V0", "t1", n_pass=2, n_att=2, round_idx=0)
    assert ledger.cell("V1", "t1") is None
    assert ledger.cell("V0", "t2") is None
    assert ledger.tasks_of("V0") == {"t1"}
    assert ledger.variants() == {"V0"}


# ---------------------------------------------------------------------------
# estimate() — our smoothing default (SPEC §6.6)
# ---------------------------------------------------------------------------


def test_estimate_uses_laplace_smoothing_by_default() -> None:
    """Explicit test of an *our-default* knob: S_hat = (p + 1) / (a + 2)."""
    ledger = SuccessLedger()
    ledger.record("V0", "t1", n_pass=2, n_att=2, round_idx=0)
    assert ledger.estimate("V0", "t1") == pytest.approx(3 / 4)

    ledger.record("V0", "t1", n_pass=0, n_att=2, round_idx=1)
    assert ledger.estimate("V0", "t1") == pytest.approx(3 / 6)


def test_estimate_returns_the_prior_for_a_never_evaluated_cell() -> None:
    ledger = SuccessLedger()
    assert DEFAULT_STALE_PRIOR == 0.5
    assert ledger.estimate("V0", "t1") == 0.5
    assert ledger.estimate("V9", "nope") == 0.5


def test_stale_prior_is_configurable() -> None:
    """Ablation {0, 0.5, ...} of SPEC §6.6."""
    pessimistic = SuccessLedger(stale_prior=0.0)
    assert pessimistic.estimate("V0", "t1") == 0.0
    with pytest.raises(ValueError):
        SuccessLedger(stale_prior=1.5)


def test_raw_rate_estimator_is_available_as_an_ablation() -> None:
    ledger = SuccessLedger(laplace=False)
    ledger.record("V0", "t1", n_pass=1, n_att=2, round_idx=0)
    assert ledger.estimate("V0", "t1") == pytest.approx(0.5)
    ledger.record("V0", "t2", n_pass=2, n_att=2, round_idx=0)
    assert ledger.estimate("V0", "t2") == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# estimate(before_round=...) — the ledger half of the routing freeze (SPEC §6.2)
# ---------------------------------------------------------------------------


def test_before_round_hides_cells_written_in_that_round() -> None:
    ledger = SuccessLedger()
    ledger.record("V0", "t1", n_pass=2, n_att=2, round_idx=5)

    # unrestricted: the cell is visible
    assert ledger.estimate("V0", "t1") == pytest.approx(3 / 4)
    # frozen at round 5: a round-5 write is not prior-round evidence
    assert ledger.estimate("V0", "t1", before_round=5) == 0.5
    # frozen at round 6: round 5 *is* prior now
    assert ledger.estimate("V0", "t1", before_round=6) == pytest.approx(3 / 4)


def test_before_round_hides_future_rounds_too() -> None:
    ledger = SuccessLedger()
    ledger.record("V0", "t1", n_pass=2, n_att=2, round_idx=9)
    assert ledger.estimate("V0", "t1", before_round=4) == 0.5


def test_before_round_drops_the_whole_cell_not_just_the_new_rollouts() -> None:
    """Documented coarseness: running totals cannot be un-mixed (SPEC §2.2).

    A cell touched in the frozen round falls back to the prior even though it
    also holds older rounds. Under correct operation this never fires, because
    at freeze time no cell can carry the current round yet — and when it does,
    dropping it is the safe direction (it can never leak this round's result).
    """
    ledger = SuccessLedger()
    ledger.record("V0", "t1", n_pass=2, n_att=2, round_idx=1)
    assert ledger.estimate("V0", "t1", before_round=3) == pytest.approx(3 / 4)

    ledger.record("V0", "t1", n_pass=0, n_att=2, round_idx=3)
    assert ledger.estimate("V0", "t1", before_round=3) == 0.5


def test_max_last_round_is_the_freeze_guard() -> None:
    ledger = SuccessLedger()
    assert ledger.max_last_round() == -1

    ledger.record("V0", "t1", n_pass=1, n_att=2, round_idx=0)
    ledger.record("V1", "t2", n_pass=0, n_att=2, round_idx=4)
    ledger.record("V0", "t3", n_pass=2, n_att=2, round_idx=2)
    assert ledger.max_last_round() == 4


# ---------------------------------------------------------------------------
# estimate(window=...) — recency ablation
# ---------------------------------------------------------------------------


def test_window_drops_cells_older_than_the_recency_horizon() -> None:
    ledger = SuccessLedger()
    ledger.record("V0", "fresh", n_pass=2, n_att=2, round_idx=8)
    ledger.record("V0", "stale", n_pass=2, n_att=2, round_idx=1)

    # full history (default): both visible
    assert ledger.estimate("V0", "stale") == pytest.approx(3 / 4)
    # last 5 rounds relative to round 10: round 1 is out, round 8 is in
    assert ledger.estimate("V0", "stale", before_round=10, window=5) == 0.5
    assert ledger.estimate("V0", "fresh", before_round=10, window=5) == pytest.approx(3 / 4)


def test_window_must_be_positive() -> None:
    ledger = SuccessLedger()
    ledger.record("V0", "t1", n_pass=2, n_att=2, round_idx=1)
    with pytest.raises(ValueError):
        ledger.estimate("V0", "t1", window=0)


# ---------------------------------------------------------------------------
# W21 — ever_solved is the full-history baseline
# ---------------------------------------------------------------------------


def test_a_single_passing_rollout_marks_the_task_solved_forever() -> None:
    ledger = SuccessLedger()
    ledger.record("V0", "t1", n_pass=1, n_att=2, round_idx=0)
    assert ledger.is_ever_solved("t1")

    ledger.record("V0", "t2", n_pass=0, n_att=2, round_idx=0)
    assert not ledger.is_ever_solved("t2")


def test_ever_solved_survives_a_later_regression() -> None:
    """R3 solves it, R5 quietly breaks it — the baseline must not forget.

    This is the whole point of W21: the seesaw baseline is "any previously
    solved task recorded in T_t" (§4.1 p.8), i.e. full history, not last round.
    """
    ledger = SuccessLedger()
    ledger.record("V0", "t1", n_pass=2, n_att=2, round_idx=3)
    assert ledger.is_ever_solved("t1")

    ledger.record("V0", "t1", n_pass=0, n_att=2, round_idx=5)
    assert ledger.is_ever_solved("t1")
    # ... even though the *current* estimate has collapsed
    assert ledger.estimate("V0", "t1") == pytest.approx(3 / 6)


def test_ever_solved_spans_variants() -> None:
    """Solved by *any* variant counts — the baseline is pool-wide."""
    ledger = SuccessLedger()
    ledger.record("V1", "t1", n_pass=1, n_att=2, round_idx=2)
    assert ledger.is_ever_solved("t1")
    assert ledger.cell("V0", "t1") is None
    assert ledger.ever_solved == {"t1"}


# ---------------------------------------------------------------------------
# variant_rollup + attempts_on
# ---------------------------------------------------------------------------


def test_variant_rollup_pools_every_cell_of_the_variant() -> None:
    ledger = SuccessLedger()
    ledger.record("V0", "t1", n_pass=2, n_att=2, round_idx=0)
    ledger.record("V0", "t2", n_pass=0, n_att=2, round_idx=0)
    ledger.record("V0", "t3", n_pass=1, n_att=2, round_idx=0)
    # raw pooled rate, not Laplace: 3 passes / 6 attempts
    assert ledger.variant_rollup("V0") == pytest.approx(0.5)


def test_variant_rollup_of_an_untried_variant_is_the_prior() -> None:
    ledger = SuccessLedger()
    assert ledger.variant_rollup("V7") == 0.5
    assert SuccessLedger(stale_prior=0.0).variant_rollup("V7") == 0.0


def test_attempts_on_feeds_the_tie_break() -> None:
    ledger = SuccessLedger()
    ledger.record("V0", "t1", n_pass=1, n_att=2, round_idx=0)
    ledger.record("V0", "t1", n_pass=0, n_att=2, round_idx=1)
    assert ledger.attempts_on("V0", "t1") == 4
    assert ledger.attempts_on("V1", "t1") == 0
