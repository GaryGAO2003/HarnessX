# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""Offline unit tests for ``variant_pool.reporting`` (W29).

Two claims are worth testing here, because both are ways a run can lie:

* the pass@k estimator is A.3 formula 6, not "did any attempt pass" — the two
  agree at ``n == k`` and diverge as soon as ``n > k``, so the reference values
  below are hand-computed from ``1 - C(n-c, k) / C(n, k)``;
* the report cannot serve a peak without a final (§7.7) and cannot serve pass@2
  without pass@1 (§7.1). The masking fixture is the paper's own failure mode:
  every task drifts 2/2 -> 1/2, pass@2 does not move, pass@1 halves.
"""

from __future__ import annotations

import json

import pytest

from variant_pool.reporting import (
    IMPLICIT_VARIANT,
    RunReport,
    TaskResult,
    pass_at_k,
    report_from_rows,
)

# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _result(task_id: str, round_idx: int, n_pass: int, n_att: int = 2, **kwargs) -> TaskResult:
    return TaskResult(task_id=task_id, round_idx=round_idx, n_att=n_att, n_pass=n_pass, **kwargs)


def _report(*results: TaskResult, **kwargs) -> RunReport:
    return RunReport(results=list(results), **kwargs)


# ===========================================================================
# A.3 formula 6 — the unbiased estimator
# ===========================================================================


@pytest.mark.parametrize(
    ("n", "c", "expected"),
    [
        (2, 0, 0.0),  # C(2,2)/C(2,2) = 1
        (2, 1, 1.0),  # C(1,2) = 0
        (2, 2, 1.0),
    ],
)
def test_pass_at_2_with_two_rollouts_matches_hand_calculation(n, c, expected) -> None:
    assert pass_at_k(n, c, 2) == pytest.approx(expected)


@pytest.mark.parametrize(
    ("n", "c", "expected"),
    [
        (4, 0, 0.0),  # 1 - C(4,2)/C(4,2)
        (4, 1, 0.5),  # 1 - C(3,2)/C(4,2) = 1 - 3/6
        (4, 2, 5 / 6),  # 1 - C(2,2)/C(4,2) = 1 - 1/6
        (4, 3, 1.0),  # C(1,2) = 0
        (4, 4, 1.0),
    ],
)
def test_pass_at_2_with_four_rollouts_matches_hand_calculation(n, c, expected) -> None:
    assert pass_at_k(n, c, 2) == pytest.approx(expected)


def test_the_estimator_is_not_did_any_attempt_pass() -> None:
    """One pass in four attempts is pass@2 = 0.5, not 1.0 — that is the whole point."""
    naive = 1.0  # "at least one rollout passed"
    assert pass_at_k(4, 1, 2) == pytest.approx(0.5)
    assert pass_at_k(4, 1, 2) != naive


def test_pass_at_1_is_the_raw_success_probability() -> None:
    assert pass_at_k(4, 1, 1) == pytest.approx(0.25)
    assert pass_at_k(2, 1, 1) == pytest.approx(0.5)


def test_too_few_attempts_is_an_error_not_a_convention() -> None:
    with pytest.raises(ValueError, match="at least 2 attempts"):
        pass_at_k(1, 1, 2)


@pytest.mark.parametrize(("n", "c", "k"), [(2, 3, 2), (2, -1, 2), (2, 1, 0)])
def test_impossible_estimator_arguments_are_rejected(n, c, k) -> None:
    with pytest.raises(ValueError):
        pass_at_k(n, c, k)


# ===========================================================================
# final and peak — SPEC §6.7 / paper §7.7
# ===========================================================================


def _rise_and_fall() -> RunReport:
    """Peak at round 1, decline afterwards — the shape §7.1 describes."""
    return _report(
        _result("a", 0, 2), _result("b", 0, 0), _result("c", 0, 0), _result("d", 0, 0),
        _result("a", 1, 2), _result("b", 1, 2), _result("c", 1, 1), _result("d", 1, 0),
        _result("a", 2, 2), _result("b", 2, 0), _result("c", 2, 0), _result("d", 2, 0),
    )


def test_final_and_peak_are_different_numbers() -> None:
    report = _rise_and_fall()
    assert report.curve() == pytest.approx([0.25, 0.75, 0.25])
    assert report.peak() == (1, pytest.approx(0.75))
    assert report.final() == pytest.approx(0.25)
    assert report.drift() == pytest.approx(-0.5)


def test_the_peak_is_the_earliest_best_round() -> None:
    report = _report(_result("a", 0, 2), _result("a", 1, 2), _result("a", 2, 0))
    assert report.peak() == (0, 1.0)


def test_markdown_never_shows_a_peak_without_a_final() -> None:
    text = _rise_and_fall().to_markdown()
    assert "final pass@2" in text
    assert "peak pass@2" in text
    # both live in the same table, above anything else
    assert text.index("final pass@2") < text.index("peak pass@2") < text.index("## Per-round curve")
    assert "no held-out split" in text  # §7.7 is stated, not implied


def test_markdown_carries_the_curve_and_the_lock() -> None:
    report = _rise_and_fall()
    report.run_name = "M0-global-s0"
    report.lock_sha256 = "deadbeef"
    text = report.to_markdown()
    assert "M0-global-s0" in text
    assert "deadbeef" in text
    assert text.count("| 0 |") >= 1 and "| 2 |" in text  # one row per round


def test_an_empty_report_renders_without_a_peak() -> None:
    report = RunReport()
    assert report.curve() == []
    assert "_no results recorded_" in report.to_markdown()
    with pytest.raises(ValueError, match="empty report"):
        report.peak()


# ===========================================================================
# pass@2 masking a per-attempt decline — paper §7.1
# ===========================================================================


def test_pass_at_2_hides_the_drift_that_pass_at_1_shows() -> None:
    """Every task goes 2/2 -> 1/2: pass@2 stays 1.0, pass@1 halves."""
    report = _report(
        *[_result(t, 0, 2) for t in "abcd"],
        *[_result(t, 5, 1) for t in "abcd"],
    )

    assert report.pass_at_k(round_idx=0) == 1.0
    assert report.pass_at_k(round_idx=5) == 1.0
    assert report.drift() == 0.0  # the headline metric reports no problem at all

    assert report.pass_at_1(round_idx=0) == 1.0
    assert report.pass_at_1(round_idx=5) == 0.5
    assert report.per_attempt_rate(round_idx=5) == 0.5
    assert report.masking_gap(round_idx=5) == pytest.approx(0.5)


def test_pass_at_1_and_per_attempt_rate_diverge_on_uneven_sampling() -> None:
    """Macro (per task) vs micro (per rollout); a gap means uneven attempt counts."""
    report = _report(_result("a", 0, 1, n_att=2), _result("b", 0, 1, n_att=4))
    assert report.pass_at_1() == pytest.approx((0.5 + 0.25) / 2)
    assert report.per_attempt_rate() == pytest.approx(2 / 6)


# ===========================================================================
# Level stratification — A.2 p.28 (39/52/12)
# ===========================================================================


def test_by_level_splits_the_headline() -> None:
    level_map = {"a": 1, "b": 1, "c": 2, "d": 3}
    report = _report(_result("a", 0, 2), _result("b", 0, 2), _result("c", 0, 1), _result("d", 0, 0))

    assert report.pass_at_k() == pytest.approx(0.75)
    assert report.by_level(level_map) == {1: 1.0, 2: 1.0, 3: 0.0}
    assert report.level_counts(level_map) == {1: 2, 2: 1, 3: 1}


def test_by_level_uses_the_scored_round_only() -> None:
    level_map = {"a": 1, "b": 3}
    report = _report(_result("a", 0, 0), _result("b", 0, 0), _result("a", 1, 2), _result("b", 1, 2))
    assert report.by_level(level_map, round_idx=0) == {1: 0.0, 3: 0.0}
    assert report.by_level(level_map) == {1: 1.0, 3: 1.0}


def test_a_task_missing_from_the_level_map_is_an_error() -> None:
    report = _report(_result("a", 0, 2), _result("ghost", 0, 0))
    with pytest.raises(ValueError, match="ghost"):
        report.by_level({"a": 1})


def test_markdown_level_table_is_opt_in() -> None:
    report = _report(_result("a", 0, 2), _result("b", 0, 0))
    assert "By GAIA level" not in report.to_markdown()
    assert "By GAIA level" in report.to_markdown(level_map={"a": 1, "b": 3})


# ===========================================================================
# Infrastructure failures — A.3 verbatim: counted as failures, not dropped
# ===========================================================================


def test_infra_failures_stay_in_the_denominator() -> None:
    clean = _report(_result("a", 0, 2), _result("b", 0, 0))
    marked = _report(_result("a", 0, 2), _result("b", 0, 0, infra_failures=2))

    assert marked.pass_at_k() == clean.pass_at_k() == 0.5  # marking changes no arithmetic
    assert marked.per_attempt_rate() == pytest.approx(0.5)
    assert marked.infra_failure_count() == 2
    assert marked.infra_failure_rate() == pytest.approx(2 / 4)


def test_a_passing_attempt_cannot_also_be_an_infra_failure() -> None:
    with pytest.raises(ValueError, match="still an attempt"):
        _result("a", 0, 2, infra_failures=1)


def test_infra_failure_rate_is_none_without_attempts() -> None:
    assert RunReport().infra_failure_rate() is None


@pytest.mark.parametrize("kwargs", [{"n_pass": 3, "n_att": 2}, {"n_pass": -1, "n_att": 2}])
def test_impossible_results_are_rejected(kwargs) -> None:
    with pytest.raises(ValueError):
        TaskResult(task_id="a", round_idx=0, **kwargs)


# ===========================================================================
# Variant-pool process data — ours; the paper's §6.6 reports none
# ===========================================================================


def test_a_baseline_run_counts_as_one_implicit_variant() -> None:
    report = _report(_result("a", 0, 2), _result("b", 0, 0), _result("a", 1, 2))
    assert report.variant_count_curve() == [1, 1]
    assert report.coverage_per_variant() == {IMPLICIT_VARIANT: 2}
    assert report.routing_hit_rate() is None  # nothing was routed; not 0.0


def test_variant_count_curve_follows_the_pool() -> None:
    report = _report(
        _result("a", 0, 2, variant_id="V0"),
        _result("b", 0, 0, variant_id="V0"),
        _result("a", 1, 2, variant_id="V0"),
        _result("b", 1, 1, variant_id="V1"),
    )
    assert report.variant_count_curve() == [1, 2]
    assert report.coverage_per_variant() == {"V0": 2, "V1": 1}


def test_routing_hit_rate_counts_solved_routed_evaluations() -> None:
    report = _report(
        _result("a", 1, 2, variant_id="V0"),
        _result("b", 1, 0, variant_id="V1"),
        _result("c", 1, 1, variant_id="V1"),
    )
    assert report.routing_hit_rate() == pytest.approx(2 / 3)


def test_fork_and_retire_events_lie_on_a_round_axis() -> None:
    report = _report(_result("a", 0, 2))
    report.add_event(round_idx=5, kind="retire", variant_id="V2", reason="lowest rollup")
    report.add_event(round_idx=3, kind="fork", variant_id="V1", parent_id="V0")

    events = report.fork_retire_events()
    assert [(e["round_idx"], e["kind"]) for e in events] == [(3, "fork"), (5, "retire")]
    assert events[0]["parent_id"] == "V0"

    with pytest.raises(ValueError):
        report.add_event(round_idx=1, kind="explode", variant_id="V0")


# ===========================================================================
# Serialisation
# ===========================================================================


def test_to_dict_pairs_final_with_peak() -> None:
    payload = _rise_and_fall().to_dict()
    assert payload["final_pass_at_2"] == pytest.approx(0.25)
    assert payload["peak_pass_at_2"] == pytest.approx(0.75)
    assert payload["peak_round"] == 1
    assert payload["curve"] == pytest.approx([0.25, 0.75, 0.25])
    assert payload["final_pass_at_1"] == pytest.approx(0.25)
    assert payload["curve_pass_at_1"] == pytest.approx([0.25, 0.625, 0.25])


def test_to_json_is_parseable_and_carries_the_level_table() -> None:
    report = _report(_result("a", 0, 2), _result("b", 0, 0))
    payload = json.loads(report.to_json(level_map={"a": 1, "b": 3}))
    assert payload["by_level"] == {"1": 1.0, "3": 0.0}
    assert payload["level_counts"] == {"1": 1, "3": 1}
    assert payload["attempts"] == 4
    assert payload["tasks"] == 2


def test_report_from_rows_reads_a_flat_feed() -> None:
    report = report_from_rows(
        [
            {"task_id": "a", "round_idx": 0, "n_att": 2, "n_pass": 1, "variant_id": "V0"},
            {"task_id": "b", "round_idx": 0, "n_att": 2, "n_pass": 0, "infra_failures": 1},
        ],
        run_name="calibration",
    )
    assert report.run_name == "calibration"
    assert report.pass_at_k() == 0.5
    assert report.infra_failure_count() == 1
