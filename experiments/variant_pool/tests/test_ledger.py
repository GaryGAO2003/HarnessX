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


def test_record_merges_repeated_writes_in_the_same_round() -> None:
    ledger = SuccessLedger()
    ledger.record("V0", "t1", n_pass=1, n_att=2, round_idx=4)
    ledger.record("V0", "t1", n_pass=1, n_att=2, round_idx=4)

    assert ledger.cell("V0", "t1") == CellStats(passes=2, attempts=4, last_round=4)
    assert ledger.aggregate_counts("V0", {"t1"}, before_round=5, window=1) == (2, 4)


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


def test_cluster_estimator_uses_one_aggregate_denominator() -> None:
    ledger = SuccessLedger()
    ledger.record("V0", "short", n_pass=2, n_att=2, round_idx=0)
    ledger.record("V0", "long", n_pass=0, n_att=6, round_idx=0)

    # One cluster cell: (2 + 1) / (2 + 6 + 2) = 3/10. Averaging two
    # independently smoothed task cells would be 7/16 and is intentionally not
    # the estimator used by Router.
    assert ledger.aggregate_counts("V0", {"short", "long"}) == (2, 8)
    assert ledger.estimate_cluster("V0", {"short", "long"}) == pytest.approx(3 / 10)


def test_cluster_estimator_preserves_before_round_and_window() -> None:
    ledger = SuccessLedger()
    ledger.record("V0", "old", n_pass=2, n_att=2, round_idx=1)
    ledger.record("V0", "fresh", n_pass=2, n_att=2, round_idx=8)

    assert ledger.estimate_cluster("V0", {"old", "fresh"}, before_round=8) == pytest.approx(3 / 4)
    assert ledger.estimate_cluster("V0", {"old", "fresh"}, before_round=9) == pytest.approx(5 / 6)
    assert ledger.estimate_cluster(
        "V0", {"old", "fresh"}, before_round=10, window=5
    ) == pytest.approx(3 / 4)


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


def test_before_round_strictly_excludes_current_but_keeps_older_rounds() -> None:
    """A current-round write cannot hide valid history or leak into routing."""
    ledger = SuccessLedger()
    ledger.record("V0", "t1", n_pass=2, n_att=2, round_idx=1)
    assert ledger.estimate("V0", "t1", before_round=3) == pytest.approx(3 / 4)

    ledger.record("V0", "t1", n_pass=0, n_att=2, round_idx=3)
    assert ledger.aggregate_counts("V0", {"t1"}, before_round=3) == (2, 2)
    assert ledger.estimate("V0", "t1", before_round=3) == pytest.approx(3 / 4)
    assert ledger.aggregate_counts("V0", {"t1"}, before_round=4) == (2, 4)
    assert ledger.estimate("V0", "t1", before_round=4) == pytest.approx(3 / 6)


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


def test_window_excludes_old_counts_inside_a_recently_touched_cell() -> None:
    """The OURS window is a true round interval, not a last-write cell filter."""
    ledger = SuccessLedger()
    ledger.record("V0", "mixed", n_pass=2, n_att=2, round_idx=1)
    ledger.record("V0", "mixed", n_pass=0, n_att=2, round_idx=8)

    # The cumulative compatibility view still contains both rounds.
    assert ledger.cell("V0", "mixed") == CellStats(passes=2, attempts=4, last_round=8)
    assert ledger.aggregate_counts("V0", {"mixed"}) == (2, 4)

    # [5, 10) retains R8 but excludes R1.
    assert ledger.aggregate_counts("V0", {"mixed"}, before_round=10, window=5) == (0, 2)
    assert ledger.estimate("V0", "mixed", before_round=10, window=5) == pytest.approx(1 / 4)


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
    # Equal attempt counts make task-macro and raw agree here.
    assert ledger.variant_rollup("V0") == pytest.approx(0.5)


def test_task_macro_is_default_and_is_not_attempt_count_dominated() -> None:
    ledger = SuccessLedger()
    ledger.record("V0", "easy", n_pass=100, n_att=100, round_idx=0)
    ledger.record("V0", "hard", n_pass=0, n_att=2, round_idx=0)
    ledger.record("V1", "a", n_pass=3, n_att=4, round_idx=0)
    ledger.record("V1", "b", n_pass=3, n_att=4, round_idx=0)

    # Legacy pooled attempts make V0 look excellent; equal task weighting
    # correctly exposes that it fails half its task types.
    assert ledger.rollup_mode == "task_macro"
    assert ledger.variant_rollup("V0", mode="raw") == pytest.approx(100 / 102)
    assert ledger.variant_rollup("V1", mode="raw") == pytest.approx(0.75)
    assert ledger.variant_rollup("V0") == pytest.approx(0.5)
    assert ledger.variant_rollup("V1") == pytest.approx(0.75)


def test_cluster_macro_gives_each_cluster_one_vote() -> None:
    clusters = {"a": "large", "b": "large", "z": "small"}
    ledger = SuccessLedger(task_clusters=clusters)
    ledger.record("V0", "a", n_pass=2, n_att=2, round_idx=0)
    ledger.record("V0", "b", n_pass=2, n_att=2, round_idx=0)
    ledger.record("V0", "z", n_pass=0, n_att=2, round_idx=0)
    ledger.record("V1", "a", n_pass=1, n_att=2, round_idx=0)
    ledger.record("V1", "b", n_pass=1, n_att=2, round_idx=0)
    ledger.record("V1", "z", n_pass=2, n_att=2, round_idx=0)

    assert ledger.variant_rollup("V0", mode="cluster_macro") == pytest.approx(0.5)
    assert ledger.variant_rollup("V1", mode="cluster_macro") == pytest.approx(0.75)


def test_window_is_consistent_across_cluster_and_rollup_views() -> None:
    clusters = {"a": "large", "b": "large", "z": "small"}
    ledger = SuccessLedger(task_clusters=clusters)

    # Old results make every task's full-history rate 1/2, but must not dilute
    # the recent interval [5, 10).
    ledger.record("V0", "a", n_pass=0, n_att=2, round_idx=1)
    ledger.record("V0", "b", n_pass=2, n_att=2, round_idx=1)
    ledger.record("V0", "z", n_pass=2, n_att=2, round_idx=1)
    ledger.record("V0", "a", n_pass=2, n_att=2, round_idx=8)
    ledger.record("V0", "b", n_pass=0, n_att=2, round_idx=8)
    ledger.record("V0", "z", n_pass=0, n_att=2, round_idx=8)

    kwargs = {"before_round": 10, "window": 5}
    assert ledger.aggregate_counts("V0", clusters, **kwargs) == (2, 6)
    assert ledger.estimate("V0", "a", **kwargs) == pytest.approx(3 / 4)
    assert ledger.estimate_cluster("V0", clusters, **kwargs) == pytest.approx(3 / 8)
    assert ledger.attempts_on_cluster("V0", clusters, **kwargs) == 6
    assert ledger.variant_rollup("V0", mode="task_macro", **kwargs) == pytest.approx(1 / 3)
    assert ledger.variant_rollup("V0", mode="cluster_macro", **kwargs) == pytest.approx(1 / 4)
    assert ledger.variant_rollup("V0", mode="raw", **kwargs) == pytest.approx(1 / 3)


def test_cluster_macro_requires_assignments() -> None:
    ledger = SuccessLedger()
    ledger.record("V0", "a", n_pass=1, n_att=2, round_idx=0)
    with pytest.raises(ValueError, match="requires"):
        ledger.variant_rollup("V0", mode="cluster_macro")


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


def test_attempts_on_cluster_honours_the_freeze_cut() -> None:
    ledger = SuccessLedger()
    ledger.record("V0", "old", n_pass=1, n_att=2, round_idx=0)
    ledger.record("V0", "current", n_pass=2, n_att=4, round_idx=1)
    assert ledger.attempts_on_cluster("V0", {"old", "current"}, before_round=1) == 2
    assert ledger.attempts_on_cluster("V0", {"old", "current"}, before_round=2) == 6
