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
    CandidateTaskResult,
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


def test_budget_exhaustion_is_not_reported_as_infrastructure() -> None:
    report = _report(
        _result("infra", 0, 0, infra_failures=1),
        _result("budget", 0, 0, budget_exhaustions=1),
    )
    assert report.infra_failure_count() == 1
    assert report.budget_exhaustion_count() == 1
    assert report.infra_failure_rate() == pytest.approx(1 / 4)
    assert report.budget_exhaustion_rate() == pytest.approx(1 / 4)
    payload = report.to_dict()
    assert payload["infra_failures"] == 1
    assert payload["budget_exhaustions"] == 1


def test_headline_counts_failures_as_run_totals_across_rounds() -> None:
    """The headline's attempts / infra / budget row is a run total over every
    settled round, not just the final one.

    runs/smoke_calib6 exposed the bug: exhaustions in R0 and R1 were under-counted
    because ``attempts`` summed all rounds while infra/budget summed only the last
    (the ``round_idx=None`` default of the count helpers). All three now share the
    run-total scope.
    """
    report = _report(
        _result("a", 0, 0, budget_exhaustions=2),
        _result("b", 0, 0, infra_failures=1),
        _result("a", 1, 0, budget_exhaustions=1),
        _result("b", 1, 1, infra_failures=1),
    )
    # The per-round helpers keep their last-round default (unchanged API): R1 by
    # itself has 1 infra + 1 budget — exactly what a last-round headline showed.
    assert report.budget_exhaustion_count(round_idx=0) == 2
    assert report.budget_exhaustion_count() == 1  # last round only
    assert report.infra_failure_count(round_idx=0) == 1
    assert report.infra_failure_count() == 1  # last round only

    text = report.to_markdown()
    # Run totals: attempts 8, infra 1+1=2, budget 2+1=3 — summed over both rounds.
    assert "| 8 / 2 / 3 |" in text
    # The last-round-only figures must NOT be what the headline prints.
    assert "| 8 / 1 / 1 |" not in text


def test_to_json_serialises_to_dict_verbatim_including_run_totals() -> None:
    """EXP-E08 closure: ``to_json`` is a pure serialisation of ``to_dict`` and
    adds no keys of its own, so the run-total triple lands in the JSON unchanged
    and the two outputs cannot drift."""
    report = _report(
        _result("a", 0, 0, budget_exhaustions=2),
        _result("b", 0, 0, infra_failures=1),
        _result("a", 1, 0, budget_exhaustions=1),
        _result("b", 1, 1, infra_failures=1),
    )
    payload = report.to_dict()
    # to_dict carries the run-total triple (summed over every settled round).
    assert payload["attempts_run_total"] == 8
    assert payload["infra_failures_run_total"] == 2
    assert payload["budget_exhaustions_run_total"] == 3
    # to_json is exactly json.dumps(to_dict, ...): the two cannot drift.
    assert report.to_json() == json.dumps(
        payload, ensure_ascii=False, indent=2, sort_keys=True
    )
    # and the run-total keys are present, unchanged, in the parsed JSON.
    from_json = json.loads(report.to_json())
    for key in (
        "attempts_run_total",
        "infra_failures_run_total",
        "budget_exhaustions_run_total",
    ):
        assert from_json[key] == payload[key]


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


def test_rejected_candidate_is_diagnostics_only() -> None:
    report = _report(_result("a", 0, 0, variant_id="V0"))
    report.add_candidate(
        CandidateTaskResult(
            task_id="a",
            round_idx=0,
            candidate_id="C-R0-V0",
            target_variant_id="V0",
            n_att=2,
            n_pass=2,
            decision="reject",
            failed_stage="SEESAW_REGRESSION",
            archive_reason="regressed a previously solved task",
        )
    )
    report.record_round(
        round_idx=0,
        candidate_count=1,
        evaluated_task_denominator=1,
        active_variant_count=1,
    )

    assert report.final() == 0.0
    assert report.pass_at_1() == 0.0
    assert report.routing_hit_rate() == 0.0
    payload = report.to_dict()
    diagnostics = payload["candidate_diagnostics"]
    assert diagnostics["by_round"][0]["pass_at_2"] == 1.0
    assert diagnostics["attempted_candidate_count"] == 1
    assert diagnostics["evaluated_candidate_count"] == 1
    assert diagnostics["rejected_candidate_count"] == 1
    assert diagnostics["skipped_candidate_count"] == 0
    assert diagnostics["candidate_denominator"] == 1
    assert diagnostics["candidates"][0]["failed_stage"] == "SEESAW_REGRESSION"
    assert diagnostics["candidates"][0]["archive_reason"] == "regressed a previously solved task"
    assert payload["final_pass_at_2"] == 0.0


def test_no_candidate_round_still_has_a_complete_active_denominator() -> None:
    report = _report(_result("a", 3, 2, variant_id="V0"), _result("b", 3, 0, variant_id="V0"))
    report.record_round(
        round_idx=3,
        candidate_count=0,
        evaluated_task_denominator=2,
        active_variant_count=1,
    )
    diagnostic = report.to_dict()["round_diagnostics"][0]
    assert diagnostic == {
        "round": 3,
        "candidate_count": 0,
        "no_candidate": True,
        "active_variant_count": 1,
        "evaluated_tasks": 2,
        "evaluated_task_denominator": 2,
        "complete": True,
    }


def test_candidate_result_old_construction_defaults_remain_compatible() -> None:
    # All pre-extension positional fields remain in their original order.
    result = CandidateTaskResult("a", 0, "C0", "V0", 2, 1, "apply", 0, 0)
    assert result.failed_stage is None
    assert result.archive_reason == ""
    assert result.skipped_reason is None
    assert result.evaluated is True


def test_candidate_result_serializes_gate_and_skip_diagnostics() -> None:
    result = CandidateTaskResult(
        task_id="a",
        round_idx=4,
        candidate_id="C4",
        target_variant_id="V2",
        n_att=0,
        n_pass=0,
        failed_stage="DIGESTER_ACTIONABILITY",
        archive_reason="",
        skipped_reason="actionability 0.2 below threshold 0.5",
        evaluated=False,
    )
    assert result.to_dict() == {
        "task_id": "a",
        "round_idx": 4,
        "candidate_id": "C4",
        "target_variant_id": "V2",
        "n_att": 0,
        "n_pass": 0,
        "decision": None,
        "infra_failures": 0,
        "budget_exhaustions": 0,
        "failed_stage": "DIGESTER_ACTIONABILITY",
        "archive_reason": "",
        "skipped_reason": "actionability 0.2 below threshold 0.5",
        "evaluated": False,
    }


def test_skipped_candidate_does_not_pollute_any_active_metric() -> None:
    report = _report(
        _result("a", 0, 2, variant_id="V0"),
        _result("b", 0, 0, variant_id="V0"),
    )
    baseline = {
        "final": report.final(),
        "peak": report.peak(),
        "curve": report.curve(),
        "drift": report.drift(),
        "hit": report.routing_hit_rate(),
    }
    report.add_candidate(
        CandidateTaskResult(
            task_id="__candidate__",
            round_idx=0,
            candidate_id="C-skipped",
            target_variant_id="V0",
            n_att=0,
            n_pass=0,
            skipped_reason="planner returned an empty landscape",
            evaluated=False,
        )
    )

    assert report.final() == baseline["final"]
    assert report.peak() == baseline["peak"]
    assert report.curve() == baseline["curve"]
    assert report.drift() == baseline["drift"]
    assert report.routing_hit_rate() == baseline["hit"]

    diagnostics = report.to_dict()["candidate_diagnostics"]
    assert diagnostics["attempted_candidate_count"] == 1
    assert diagnostics["evaluated_candidate_count"] == 0
    assert diagnostics["rejected_candidate_count"] == 0
    assert diagnostics["skipped_candidate_count"] == 1
    assert diagnostics["evaluation_denominator"] == 1
    assert diagnostics["evaluated_tasks"] == 0
    assert diagnostics["pass_at_2"] is None
    assert diagnostics["candidates"][0]["skipped_reason"] == "planner returned an empty landscape"


def test_manifest_gate_failure_is_rejected_even_without_a_decision() -> None:
    report = _report(_result("a", 0, 2, variant_id="V0"))
    report.add_candidate(
        CandidateTaskResult(
            task_id="__candidate__",
            round_idx=0,
            candidate_id="C-invalid",
            target_variant_id="V0",
            n_att=0,
            n_pass=0,
            decision=None,
            failed_stage="MANIFEST_COMPLETE",
            archive_reason="manifest is missing change_summary",
            skipped_reason="candidate failed before rollout evaluation",
            evaluated=False,
        )
    )

    diagnostics = report.to_dict()["candidate_diagnostics"]
    assert diagnostics["attempted_candidate_count"] == 1
    assert diagnostics["evaluated_candidate_count"] == 0
    assert diagnostics["rejected_candidate_count"] == 1
    assert diagnostics["skipped_candidate_count"] == 1
    assert diagnostics["candidates"][0]["rejected"] is True
    assert diagnostics["candidates"][0]["skipped"] is True
    assert report.final() == 1.0
    assert report.curve() == [1.0]


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


# ===========================================================================
# Candidate-gate outcome classification — runs/forceprobe2 P1
# ===========================================================================


def _candidate(candidate_id: str, decision: str | None, **kwargs) -> CandidateTaskResult:
    return CandidateTaskResult(
        task_id="t",
        round_idx=0,
        candidate_id=candidate_id,
        target_variant_id="V0",
        n_att=2,
        n_pass=kwargs.pop("n_pass", 2),
        decision=decision,
        **kwargs,
    )


def test_forked_winner_is_counted_as_forked_not_rejected() -> None:
    """A FORK winner carries a forced-gate ``archive_reason``; it must be counted
    as forked, not rejected (runs/forceprobe2 said "candidates rejected: 2" while
    one of the two had FORKED). applied / forked / rejected are the three mutually
    exclusive shipping outcomes and, with no pure skips, sum to the denominator.
    """
    report = _report(_result("t", 0, 2, variant_id="V0"))
    report.add_candidate(_candidate("C-apply", "apply", archive_reason="applied edit"))
    report.add_candidate(
        _candidate(
            "C-fork",
            "fork",
            n_pass=1,
            archive_reason="FORCED_GATE(fork): real_decision=reject; synthesized_improved=[x, y]",
        )
    )
    report.add_candidate(
        _candidate(
            "C-reject",
            "reject",
            n_pass=0,
            failed_stage="SEESAW_REGRESSION",
            archive_reason="regressed a previously solved task",
        )
    )

    diagnostics = report.candidate_diagnostics()
    assert diagnostics["attempted_candidate_count"] == 3
    assert diagnostics["applied_candidate_count"] == 1
    assert diagnostics["forked_candidate_count"] == 1
    assert diagnostics["rejected_candidate_count"] == 1
    assert diagnostics["skipped_candidate_count"] == 0
    # mutually exclusive, and (no pure skips) summing to the denominator
    assert (
        diagnostics["applied_candidate_count"]
        + diagnostics["forked_candidate_count"]
        + diagnostics["rejected_candidate_count"]
        == diagnostics["attempted_candidate_count"]
    )

    by_id = {row["candidate_id"]: row for row in diagnostics["candidates"]}
    assert by_id["C-fork"]["forked"] is True and by_id["C-fork"]["rejected"] is False
    assert by_id["C-apply"]["applied"] is True and by_id["C-apply"]["rejected"] is False
    assert by_id["C-reject"]["rejected"] is True
    assert by_id["C-reject"]["applied"] is False and by_id["C-reject"]["forked"] is False

    # The JSON representation mirrors the same counters.
    payload = report.to_dict()["candidate_diagnostics"]
    assert payload["applied_candidate_count"] == 1
    assert payload["forked_candidate_count"] == 1
    assert payload["rejected_candidate_count"] == 1

    # The markdown pins the rendered lines and counts.
    text = report.to_markdown()
    assert "- candidates applied: 1" in text
    assert "- candidates forked: 1" in text
    assert "- candidates rejected: 1" in text
    assert "- candidates skipped: 0" in text


def test_pre_gate_failure_is_rejected_and_skipped_but_never_forked() -> None:
    """A pre-gate failure (no decision, a failed stage, unevaluated) stays in the
    ``rejected`` bucket and the orthogonal ``skipped`` bucket, and is in neither
    ``applied`` nor ``forked``.
    """
    report = _report(_result("t", 0, 2, variant_id="V0"))
    report.add_candidate(
        CandidateTaskResult(
            task_id="__candidate__",
            round_idx=0,
            candidate_id="C-pre",
            target_variant_id="V0",
            n_att=0,
            n_pass=0,
            decision=None,
            failed_stage="ROUNDTRIP_L2",
            archive_reason="no Level-2 round-trip evidence",
            evaluated=False,
        )
    )
    diagnostics = report.candidate_diagnostics()
    assert diagnostics["applied_candidate_count"] == 0
    assert diagnostics["forked_candidate_count"] == 0
    assert diagnostics["rejected_candidate_count"] == 1
    assert diagnostics["skipped_candidate_count"] == 1
    row = diagnostics["candidates"][0]
    assert row["applied"] is False and row["forked"] is False
    assert row["rejected"] is True and row["skipped"] is True


# ===========================================================================
# to_dict scope: run-total vs last-round infra/budget — deferred Fix (2e78468)
# ===========================================================================


def test_to_dict_exposes_run_total_and_last_round_infra_budget_scopes() -> None:
    """``infra_failures`` / ``budget_exhaustions`` keep their LAST-ROUND scope
    (paired with the last-round rates), and the new ``*_run_total`` keys are the
    whole-run sums that match the markdown headline.
    """
    report = _report(
        _result("a", 0, 0, budget_exhaustions=2),
        _result("b", 0, 0, infra_failures=1),
        _result("a", 1, 0, budget_exhaustions=1),
        _result("b", 1, 1, infra_failures=1),
    )
    payload = report.to_dict()

    # Old keys keep last-round values (round 1: 1 infra, 1 budget) — unchanged API.
    assert payload["infra_failures"] == 1
    assert payload["budget_exhaustions"] == 1
    # New keys are run totals over both rounds.
    assert payload["infra_failures_run_total"] == 2
    assert payload["budget_exhaustions_run_total"] == 3
    assert payload["attempts_run_total"] == 8
    # ``attempts`` was already a run total and is unchanged.
    assert payload["attempts"] == 8
    # The run-total triple matches the markdown headline row.
    assert "| 8 / 2 / 3 |" in report.to_markdown()
