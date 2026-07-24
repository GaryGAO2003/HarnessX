# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""Offline unit tests for ``experiments/calibrate.py`` (W30's execution skeleton).

The pieces that can be wrong without an API are the ones tested: the level
quotas (SPEC §6.8 names 2/3/1 and 8/12/4, and neither is what plain rounding of
the paper's 39/52/12 gives), the sampling determinism, the ledger wiring, and
the projection arithmetic that turns a six-task sample into an M0 budget.

The evaluation path itself is ``TODO(batch-B)``: :func:`calibrate.harness_runner`
must still raise, and the runner used here is injected.
"""

from __future__ import annotations

import json

import pytest

import calibrate
from variant_pool.accounting import MTOK, AttemptCost, Pricing

FLASH = Pricing.deepseek_v4_flash()
PRO = Pricing.deepseek_v4_pro()

# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _tasks(counts: dict[int, int]) -> list[dict]:
    return [
        {"task_id": f"L{level}-{index:03d}", "Question": "q", "answer": "a", "Level": level}
        for level, n in sorted(counts.items())
        for index in range(n)
    ]


def _fixed_runner(*, passes: bool = True, input_tokens: int = 1000, cached: int = 0, output: int = 500):
    def runner(task, *, attempt_idx: int, round_idx: int) -> calibrate.AttemptOutcome:
        return calibrate.AttemptOutcome(
            passed=passes,
            cost=AttemptCost(
                task_id=str(task["task_id"]),
                round_idx=round_idx,
                variant_id=None,
                role="task_agent",
                input_tokens=input_tokens,
                cached_input_tokens=cached,
                output_tokens=output,
            ),
        )

    return runner


# ===========================================================================
# Level quotas — SPEC §6.8
# ===========================================================================


def test_the_two_named_sample_sizes_are_the_specs_own() -> None:
    assert calibrate.level_quota(6) == {1: 2, 2: 3, 3: 1}
    assert calibrate.level_quota(24) == {1: 8, 2: 12, 3: 4}


def test_the_named_24_quota_is_not_the_papers_ratio() -> None:
    """8/12/4 over-weights level 3; the proportional split would be 9/12/3."""
    proportional = {level: round(share * 24 / 103) for level, share in {1: 39, 2: 52, 3: 12}.items()}
    assert proportional == {1: 9, 2: 12, 3: 3}
    assert calibrate.level_quota(24) != proportional


def test_the_full_set_reproduces_the_paper_mix() -> None:
    assert calibrate.level_quota(103) == {1: 39, 2: 52, 3: 12}


@pytest.mark.parametrize("size", [1, 2, 5, 7, 12, 30, 50, 103])
def test_a_quota_always_sums_to_the_requested_size(size) -> None:
    """Largest remainder, not rounding: plain rounding gives 5 at size 6."""
    assert sum(calibrate.level_quota(size).values()) == size


def test_a_quota_is_deterministic() -> None:
    assert calibrate.level_quota(37) == calibrate.level_quota(37)


def test_a_zero_sized_sample_is_rejected() -> None:
    with pytest.raises(ValueError):
        calibrate.level_quota(0)


# ===========================================================================
# Stratified sampling
# ===========================================================================


def test_the_default_sample_is_two_three_one() -> None:
    sample = calibrate.stratified_sample(_tasks({1: 39, 2: 52, 3: 12}), 6)
    assert len(sample) == 6
    assert calibrate.level_counts(sample) == {1: 2, 2: 3, 3: 1}


def test_the_large_sample_is_eight_twelve_four() -> None:
    sample = calibrate.stratified_sample(_tasks({1: 39, 2: 52, 3: 12}), 24)
    assert calibrate.level_counts(sample) == {1: 8, 2: 12, 3: 4}


def test_sampling_is_deterministic_and_takes_the_first_ids() -> None:
    tasks = _tasks({1: 39, 2: 52, 3: 12})
    first = calibrate.stratified_sample(tasks, 6)
    assert first == calibrate.stratified_sample(list(reversed(tasks)), 6)
    assert [row["task_id"] for row in first if row["Level"] == 1] == ["L1-000", "L1-001"]


def test_a_short_level_is_topped_up_and_the_slip_is_visible() -> None:
    """The sample still has `size` rows, and level_counts shows the mix moved."""
    sample = calibrate.stratified_sample(_tasks({1: 39, 2: 52, 3: 0}), 6)
    assert len(sample) == 6
    counts = calibrate.level_counts(sample)
    assert 3 not in counts
    assert sum(counts.values()) == 6


def test_level_map_feeds_the_report() -> None:
    sample = calibrate.stratified_sample(_tasks({1: 3, 2: 3, 3: 3}), 6)
    mapping = calibrate.level_map(sample)
    assert set(mapping) == {row["task_id"] for row in sample}
    assert set(mapping.values()) <= {1, 2, 3}


def test_load_tasks_round_trips_a_written_file(tmp_path) -> None:
    path = tmp_path / "tasks.json"
    path.write_text(json.dumps(_tasks({1: 2, 2: 1})), encoding="utf-8")
    assert len(calibrate.load_tasks(path)) == 3

    bad = tmp_path / "bad.json"
    bad.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="JSON array"):
        calibrate.load_tasks(bad)


# ===========================================================================
# The evaluation seam stays unwired in this batch
# ===========================================================================


def test_the_real_runner_is_still_a_todo() -> None:
    with pytest.raises(NotImplementedError, match="batch-B"):
        calibrate.harness_runner()


# ===========================================================================
# run_calibration — every attempt lands in the ledger
# ===========================================================================


def test_each_task_is_attempted_k_times_and_billed_k_times() -> None:
    sample = calibrate.stratified_sample(_tasks({1: 39, 2: 52, 3: 12}), 6)
    result = calibrate.run_calibration(sample, _fixed_runner(), k=2)

    assert result.ledger.attempts("task_agent") == 12
    assert result.report.pass_at_k() == 1.0
    assert result.report.rounds() == [0]
    assert len(result.tasks) == 6


def test_a_failing_run_scores_zero_but_is_still_billed() -> None:
    sample = calibrate.stratified_sample(_tasks({1: 3, 2: 3, 3: 3}), 6)
    result = calibrate.run_calibration(sample, _fixed_runner(passes=False), k=2)

    assert result.report.pass_at_k() == 0.0
    assert result.ledger.total_billed()["total"] > 0


def test_infra_failures_reach_the_report(tmp_path) -> None:
    """A.3: an attempt lost to infrastructure is a failure, not a deleted attempt."""

    def runner(task, *, attempt_idx, round_idx):
        outcome = _fixed_runner(passes=False)(task, attempt_idx=attempt_idx, round_idx=round_idx)
        outcome.infra_failure = True
        return outcome

    result = calibrate.run_calibration(_tasks({1: 2}), runner, k=2)
    assert result.report.infra_failure_count() == 4
    assert result.report.per_attempt_rate() == 0.0


def test_a_zero_k_calibration_is_rejected() -> None:
    with pytest.raises(ValueError):
        calibrate.run_calibration(_tasks({1: 1}), _fixed_runner(), k=0)


# ===========================================================================
# Projection arithmetic
# ===========================================================================


def test_the_sample_extrapolates_to_the_m0_scale() -> None:
    sample = calibrate.stratified_sample(_tasks({1: 39, 2: 52, 3: 12}), 6)
    result = calibrate.run_calibration(sample, _fixed_runner(input_tokens=1000, output=500), k=2)

    projection = result.ledger.project(**calibrate.M0_SCALE)
    per_attempt = (1000 * 0.14 + 500 * 0.28) / MTOK

    assert calibrate.M0_SCALE == {"lineages": 3, "tasks": 103, "rounds": 15, "k": 2}
    assert projection["attempts"] == 9270
    assert projection["per_attempt_usd"] == pytest.approx(per_attempt)
    assert projection["total_usd"] == pytest.approx(per_attempt * 9270)


def test_the_cache_hit_rate_moves_the_projected_budget() -> None:
    """The budget range is the spread between the measured rate and a cold cache."""
    sample = _tasks({1: 2})
    cold = calibrate.run_calibration(sample, _fixed_runner(input_tokens=1000, cached=0), k=2)
    warm = calibrate.run_calibration(sample, _fixed_runner(input_tokens=200, cached=800), k=2)

    cold_projection = cold.ledger.project(**calibrate.M0_SCALE)
    warm_projection = warm.ledger.project(**calibrate.M0_SCALE)

    assert warm.ledger.cache_hit_rate() == pytest.approx(0.8)
    assert warm_projection["total_usd"] < cold_projection["total_usd"]
    assert warm_projection["total_usd_no_cache"] == pytest.approx(cold_projection["total_usd"])


# ===========================================================================
# Rendering
# ===========================================================================


def test_render_reports_the_three_calibration_targets() -> None:
    """Checklist §6: cache hit rate, per-attempt tokens, per-level pass rate."""
    sample = calibrate.stratified_sample(_tasks({1: 39, 2: 52, 3: 12}), 6)
    result = calibrate.run_calibration(sample, _fixed_runner(input_tokens=400, cached=600), k=2)
    text = calibrate.render(result)

    assert "overall cache hit rate: 0.600" in text
    assert "billed tokens per task-agent attempt" in text
    assert "## Capability floor" in text
    assert "| level | tasks | pass@2 |" in text
    assert "budget range" in text
    assert "not asserted" in text  # no --floor given


def test_render_states_the_floor_verdict_when_asked() -> None:
    result = calibrate.run_calibration(_tasks({1: 2}), _fixed_runner(), k=2)
    assert "clears the 0.5000 floor" in calibrate.render(result, floor=0.5)
    assert "**below**" in calibrate.render(result, floor=1.5)


def test_an_unmeasured_meta_agent_is_flagged_not_hidden() -> None:
    result = calibrate.run_calibration(_tasks({1: 2}), _fixed_runner(), k=2)
    text = calibrate.render(result)
    assert "not measured in this sample" in text


def test_summary_is_json_serialisable() -> None:
    result = calibrate.run_calibration(_tasks({1: 2, 2: 1}), _fixed_runner(), k=2)
    payload = json.loads(json.dumps(calibrate.summary(result)))
    assert payload["sample_size"] == 3
    assert payload["projection"]["attempts"] == 9270
    assert payload["report"]["final_pass_at_2"] == 1.0
    assert payload["sample_by_level"] == {"1": 2, "2": 1}


# ===========================================================================
# CLI
# ===========================================================================


def test_dry_run_writes_a_banner_and_both_artifacts(tmp_path, capsys) -> None:
    dataset = tmp_path / "tasks.json"
    dataset.write_text(json.dumps(_tasks({1: 39, 2: 52, 3: 12})), encoding="utf-8")
    out = tmp_path / "out" / "calibration.md"
    json_out = tmp_path / "out" / "calibration.json"

    code = calibrate.main(
        [
            "--dataset", str(dataset),
            "--size", "6",
            "--dry-run",
            "--out", str(out),
            "--json-out", str(json_out),
        ]
    )
    assert code == 0

    text = out.read_text(encoding="utf-8")
    assert "DRY RUN" in text
    assert "not evidence" in text
    payload = json.loads(json_out.read_text(encoding="utf-8"))
    assert payload["synthetic"] is True
    assert payload["sample_by_level"] == {"1": 2, "2": 3, "3": 1}
    assert "sampled 6 of 103 tasks" in capsys.readouterr().out


def test_the_cli_defaults_to_the_paper_scale() -> None:
    args = calibrate.build_parser().parse_args([])
    assert (args.lineages, args.tasks, args.rounds, args.k) == (3, 103, 15, 2)
    assert args.size == 6
    assert args.floor is None
    assert args.dry_run is False


def test_built_in_pricing_covers_the_default_models() -> None:
    args = calibrate.build_parser().parse_args([])
    pricing = calibrate.resolve_pricing(args)
    assert pricing["task_agent"].model.endswith("flash")
    assert pricing["meta_agent"].model.endswith("pro")


def test_an_unknown_model_asks_for_litellm() -> None:
    args = calibrate.build_parser().parse_args(["--task-model", "openai/gpt-5.4"])
    with pytest.raises(SystemExit, match="litellm"):
        calibrate.resolve_pricing(args)


def test_without_dry_run_the_cli_hits_the_batch_b_todo(tmp_path) -> None:
    dataset = tmp_path / "tasks.json"
    dataset.write_text(json.dumps(_tasks({1: 2, 2: 3, 3: 1})), encoding="utf-8")
    with pytest.raises(NotImplementedError, match="batch-B"):
        calibrate.main(["--dataset", str(dataset), "--size", "6"])
