# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""Offline unit tests for pass@k in the GAIA evolver runner (W17).

No network, no model, no ``runs/`` directory: every rollout is a stub, which is
what lets these tests assert the two properties a live run can never show
cheaply.

* **Independence.** §6.1 p.15 says "two *independent* attempts per round". Each
  attempt therefore has to get its own harness instance, its own task copy and
  its own session id; sharing any of them would let attempt 2 inherit attempt
  1's state and correlate the samples the estimator assumes are independent.
* **Concurrency.** Table 8 fixes task concurrency at 10. Raising k must not
  raise the number of harnesses in flight, so the semaphore is acquired per
  attempt and the fake rollout below counts overlaps to prove it.

The rest is arithmetic that has to be exactly right because the seesaw gate and
the manifest's three ``predicted_impact`` categories are defined on it:
(0,2)/(1,2)/(2,2) → solved False/True/True, infrastructure failures kept inside
the denominator (A.3 p.29), and a round score that is the unbiased estimator
rather than a solved-task ratio.

One thing is deliberately *not* tested, because it must not be implemented:
§7.1 p.21 notes pass@2 lets a task whose success probability has degraded still
register as "solved". :func:`test_pass_2_masks_a_two_of_two_to_one_of_two_drift`
pins that masking in place — it is the mechanism behind the paper's Global-arm
collapse and reproducing it is the point.
"""

from __future__ import annotations

import asyncio
import contextlib
import io
from types import SimpleNamespace

import pytest

from benchmarks.gaia.task import GAIATask
from recipe.gaia_evolver.run import (
    _is_infra_failure,
    _merge_attempt_records,
    _rollout_once,
    _round_pass_rate,
    _run_task,
    _run_task_pass_k,
    print_multiround_comparison,
)
from variant_pool.reporting import pass_at_k

#: Everything pass@k adds to a record. Every other key must survive untouched
#: at ``--pass-k 1`` — that is the whole backward-compatibility claim.
NEW_RECORD_KEYS = {"n_pass", "n_att", "attempts", "infra_failures", "primary_attempt"}


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _task(task_id: str = "t1", *, level: int = 1) -> GAIATask:
    return GAIATask(
        description="",  # __post_init__ mirrors `question` into it
        task_id=task_id,
        question=f"question for {task_id}",
        level=level,
        final_answer="42",
    )


def _attempt_record(
    task_id: str = "t1",
    *,
    attempt: int = 0,
    passed: bool = False,
    exit_reason: str = "done",
    **extra,
) -> dict:
    """A per-attempt record shaped like the one ``_run_task`` returns."""
    record = {
        "task_id": task_id,
        "attempt": attempt,
        "level": 1,
        "question": "question for t1",
        "expected": "42",
        "output": "FINAL ANSWER: 42" if passed else "FINAL ANSWER: 41",
        "passed": passed,
        "score": 1.0 if passed else 0.0,
        "reason": "match" if passed else "mismatch",
        "steps": 5,
        "total_tokens": 100,
        "cost_usd": 0.25,
        "elapsed_s": 3.0,
        "exit_reason": exit_reason,
    }
    record.update(extra)
    return record


def _stub_rollout(*outcomes: bool, exit_reasons: tuple[str, ...] | None = None):
    """Rollout stub whose attempt *i* passes iff ``outcomes[i]``."""

    async def _rollout(task: GAIATask, attempt_idx: int) -> dict:
        return _attempt_record(
            task.task_id,
            attempt=attempt_idx,
            passed=outcomes[attempt_idx],
            exit_reason=(exit_reasons[attempt_idx] if exit_reasons else "done"),
        )

    return _rollout


class _CountingModelConfig:
    """Stands in for ``ModelConfig``; hands out a fresh harness per call."""

    def __init__(self) -> None:
        self.built: list[SimpleNamespace] = []

    def agentic(self, config) -> SimpleNamespace:  # noqa: ANN001
        harness = SimpleNamespace(config=config, index=len(self.built), _rt=None)
        self.built.append(harness)
        return harness


class _ConcurrencyTracker:
    """Counts overlapping rollouts so the semaphore's bound can be asserted."""

    def __init__(self, *, dwell: float = 0.005) -> None:
        self.live = 0
        self.peak = 0
        self.total = 0
        self._dwell = dwell

    async def rollout(self, task: GAIATask, attempt_idx: int) -> dict:
        self.live += 1
        self.total += 1
        self.peak = max(self.peak, self.live)
        await asyncio.sleep(self._dwell)
        self.live -= 1
        return _attempt_record(task.task_id, attempt=attempt_idx)


class _FakeHarness:
    """Minimal ``harness.run`` that records the session id it was handed."""

    def __init__(self, *, boom: bool = False) -> None:
        self.sessions: list[str] = []
        self._boom = boom

    async def run(self, task, session_id: str = ""):  # noqa: ANN001
        self.sessions.append(session_id)
        if self._boom:
            raise RuntimeError("sandbox refused to start")
        return SimpleNamespace(
            final_output="FINAL ANSWER: 42",
            total_steps=3,
            total_tokens=120,
            total_cost_usd=0.5,
            exit_reason="done",
            run_id="run-1",
            task_end=SimpleNamespace(state_snapshot={"slots": {}}),
        )


class _FakeEvaluator:
    def __init__(self, *, passed: bool = True) -> None:
        self._passed = passed

    async def evaluate_answer(self, output: str, expected: str):  # noqa: ANN001
        return SimpleNamespace(
            passed=self._passed,
            score=1.0 if self._passed else 0.0,
            reason="stub",
        )


def _render(rounds: list[list[dict]], **kwargs) -> str:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        print_multiround_comparison(rounds, **kwargs)
    return buf.getvalue()


# ===========================================================================
# k = 1 — regression protection
# ===========================================================================


async def test_pass_k_1_leaves_every_pre_existing_field_untouched() -> None:
    original = _attempt_record(passed=True)

    async def _rollout(task: GAIATask, attempt_idx: int) -> dict:
        return dict(original)

    merged = await _run_task_pass_k(
        _task(),
        pass_k=1,
        sem=asyncio.Semaphore(1),
        rollout=_rollout,
    )

    assert set(merged) - set(original) == NEW_RECORD_KEYS
    for key, value in original.items():
        assert merged[key] == value, f"pass@1 changed {key!r}"
    assert merged["n_pass"] == 1
    assert merged["n_att"] == 1
    assert merged["primary_attempt"] == 0
    assert merged["infra_failures"] == 0


async def test_pass_k_1_runs_exactly_one_rollout() -> None:
    tracker = _ConcurrencyTracker(dwell=0)
    merged = await _run_task_pass_k(
        _task(),
        pass_k=1,
        sem=asyncio.Semaphore(4),
        rollout=tracker.rollout,
        finalize=None,
    )
    assert tracker.total == 1
    assert merged["n_att"] == 1


def test_pass_k_1_round_rate_equals_the_solved_task_ratio() -> None:
    records = [
        {"task_id": "a", "n_att": 1, "n_pass": 1},
        {"task_id": "b", "n_att": 1, "n_pass": 0},
        {"task_id": "c", "n_att": 1, "n_pass": 1},
    ]
    naive = sum(1 for r in records if r["n_pass"]) / len(records)
    assert _round_pass_rate(records, 1) == pytest.approx(naive)
    assert _round_pass_rate(records, 1) == pytest.approx(2 / 3)


def test_single_attempt_records_render_the_historical_table() -> None:
    """Legacy records carry no n_att; the table must look exactly as before."""
    rounds = [
        [{"task_id": "t1", "passed": False, "steps": 5, "cost_usd": 0.2, "total_tokens": 500}],
        [{"task_id": "t1", "passed": True, "steps": 4, "cost_usd": 0.1, "total_tokens": 400}],
    ]
    out = _render(rounds)
    assert "rollouts" not in out
    assert "pass@" not in out
    assert "PASS" in out and "FAIL" in out
    # …and the per-task cells stay bare PASS/FAIL, with no attempt suffix.
    assert "PASS 1/1" not in out
    assert "FAIL 0/1" not in out


# ===========================================================================
# k = 2 — independence (§6.1 p.15 "two independent attempts")
# ===========================================================================


async def test_each_attempt_builds_its_own_harness_and_task_copy() -> None:
    task = _task()
    model_config = _CountingModelConfig()
    seen: list[dict] = []

    async def _run_task_stub(harness, task, label, *, pipeline_eval, harness_config=None, attempt_idx=0):  # noqa: ANN001
        # Keep the objects alive, not their ids: a freed copy's id can be
        # handed straight back to the next allocation, which would make an
        # id-based identity check pass or fail at random.
        seen.append(
            {
                "harness": harness,
                "attempt_idx": attempt_idx,
                "task_obj": task,
                "max_cost": task.max_cost_usd,
                "label": label,
            }
        )
        return _attempt_record(task.task_id, attempt=attempt_idx, passed=attempt_idx == 1)

    async def _rollout(t: GAIATask, i: int) -> dict:
        return await _rollout_once(
            t,
            i,
            label="R0",
            model_config=model_config,
            round_config=object(),
            pipeline_eval=None,
            max_cost=1.5,
            run_task=_run_task_stub,
        )

    merged = await _run_task_pass_k(task, pass_k=2, sem=asyncio.Semaphore(10), rollout=_rollout)

    # Two rollouts, two harnesses, and they are not the same object.
    assert len(model_config.built) == 2
    assert model_config.built[0] is not model_config.built[1]
    assert {s["attempt_idx"] for s in seen} == {0, 1}
    assert seen[0]["harness"] is not seen[1]["harness"]
    assert {id(s["harness"]) for s in seen} == {id(h) for h in model_config.built}

    # Each attempt got its own task copy carrying the per-attempt cost cap —
    # the original task object is never handed to the harness.
    assert seen[0]["task_obj"] is not seen[1]["task_obj"]
    assert all(s["task_obj"] is not task for s in seen)
    assert all(s["max_cost"] == 1.5 for s in seen)

    assert merged["n_att"] == 2
    assert merged["n_pass"] == 1


async def test_each_attempt_gets_its_own_session_id() -> None:
    harness = _FakeHarness()
    task = _task("abc")
    for attempt_idx in (0, 1, 2):
        await _run_task(
            harness,
            task,
            "R3",
            pipeline_eval=_FakeEvaluator(),
            attempt_idx=attempt_idx,
        )
    # Attempt 0 keeps the pre-pass@k session id verbatim; later attempts are
    # suffixed, so two rollouts of one task cannot overwrite each other's log.
    assert harness.sessions == ["R3-abc", "R3-abc-a2", "R3-abc-a3"]
    assert len(set(harness.sessions)) == 3


async def test_attempt_index_is_recorded_on_every_attempt() -> None:
    harness = _FakeHarness()
    record = await _run_task(
        harness,
        _task("abc"),
        "R0",
        pipeline_eval=_FakeEvaluator(),
        attempt_idx=1,
    )
    assert record["attempt"] == 1


async def test_a_crashed_attempt_still_yields_a_record_with_its_index() -> None:
    record = await _run_task(
        _FakeHarness(boom=True),
        _task("abc"),
        "R0",
        pipeline_eval=_FakeEvaluator(),
        attempt_idx=1,
    )
    assert record["attempt"] == 1
    assert record["exit_reason"] == "error"
    assert record["passed"] is False
    assert _is_infra_failure(record)


# ===========================================================================
# k = 2 — outcome arithmetic
# ===========================================================================


@pytest.mark.parametrize(
    ("outcomes", "expected_passed", "expected_n_pass"),
    [
        ((False, False), False, 0),  # 0/2 — ALL_FAIL
        ((False, True), True, 1),  # 1/2 — PARTIAL_PASS
        ((True, False), True, 1),  # 1/2 — PARTIAL_PASS, other order
        ((True, True), True, 2),  # 2/2
    ],
)
async def test_two_attempts_are_solved_if_either_succeeds(outcomes, expected_passed, expected_n_pass) -> None:
    merged = await _run_task_pass_k(
        _task(),
        pass_k=2,
        sem=asyncio.Semaphore(4),
        rollout=_stub_rollout(*outcomes),
    )
    assert merged["passed"] is expected_passed
    assert merged["n_pass"] == expected_n_pass
    assert merged["n_att"] == 2
    assert [a["passed"] for a in merged["attempts"]] == list(outcomes)
    assert [a["attempt"] for a in merged["attempts"]] == [0, 1]


async def test_exactly_k_rollouts_run_per_task() -> None:
    tracker = _ConcurrencyTracker(dwell=0)
    merged = await _run_task_pass_k(
        _task(),
        pass_k=2,
        sem=asyncio.Semaphore(4),
        rollout=tracker.rollout,
    )
    assert tracker.total == 2
    assert merged["n_att"] == 2
    assert len(merged["attempts"]) == 2


async def test_flat_fields_describe_the_attempt_that_passed() -> None:
    """`passed: true` must not sit next to a failing score/output."""
    merged = await _run_task_pass_k(
        _task(),
        pass_k=2,
        sem=asyncio.Semaphore(4),
        rollout=_stub_rollout(False, True),
    )
    assert merged["primary_attempt"] == 1
    assert merged["passed"] is True
    assert merged["score"] == 1.0
    assert merged["reason"] == "match"


async def test_resource_fields_sum_over_attempts() -> None:
    """The round really did pay for both rollouts, so the record must say so."""
    merged = await _run_task_pass_k(
        _task(),
        pass_k=2,
        sem=asyncio.Semaphore(4),
        rollout=_stub_rollout(False, True),
    )
    assert merged["cost_usd"] == pytest.approx(0.5)  # 2 x 0.25
    assert merged["total_tokens"] == 200
    assert merged["steps"] == 10
    assert [a["cost_usd"] for a in merged["attempts"]] == [0.25, 0.25]


async def test_pass_k_below_one_is_rejected() -> None:
    with pytest.raises(ValueError, match="pass_k must be >= 1"):
        await _run_task_pass_k(_task(), pass_k=0, sem=asyncio.Semaphore(1), rollout=_stub_rollout())


def test_merging_zero_attempts_is_an_error_not_a_zero() -> None:
    with pytest.raises(ValueError, match="at least one attempt"):
        _merge_attempt_records([])


# ===========================================================================
# Concurrency — k must not multiply the harnesses in flight (Table 8)
# ===========================================================================


@pytest.mark.parametrize("pass_k", [1, 2, 3])
async def test_concurrency_is_bounded_by_the_semaphore_whatever_k_is(pass_k) -> None:
    limit = 2
    n_tasks = 4
    sem = asyncio.Semaphore(limit)
    tracker = _ConcurrencyTracker()

    await asyncio.gather(
        *(
            _run_task_pass_k(_task(f"t{i}"), pass_k=pass_k, sem=sem, rollout=tracker.rollout)
            for i in range(n_tasks)
        )
    )

    assert tracker.total == n_tasks * pass_k  # every attempt really ran
    assert tracker.peak <= limit  # …and never more than `limit` at once
    assert tracker.peak == limit  # the bound is reached, so the test has teeth


async def test_raising_k_does_not_raise_the_peak() -> None:
    """The failure this guards: acquiring the semaphore per *task* instead of
    per *attempt*, which would let k=3 run 3x the configured concurrency."""
    limit = 3
    peaks = []
    for pass_k in (1, 3):
        tracker = _ConcurrencyTracker()
        sem = asyncio.Semaphore(limit)
        await asyncio.gather(
            *(
                _run_task_pass_k(_task(f"t{i}"), pass_k=pass_k, sem=sem, rollout=tracker.rollout)
                for i in range(4)
            )
        )
        peaks.append(tracker.peak)
    assert peaks == [limit, limit]


async def test_post_processing_runs_outside_the_semaphore() -> None:
    """Judge lookup and trajectory writes are cheap and must not hold a slot.

    ``finalize`` is invoked after the semaphore is released, so it can overlap
    freely; with a 1-slot semaphore the finalizers of different tasks still
    interleave, which they could not do from inside the critical section.
    """
    sem = asyncio.Semaphore(1)
    live_finalizers = 0
    peak_finalizers = 0

    async def _rollout(task: GAIATask, attempt_idx: int) -> dict:
        await asyncio.sleep(0)
        return _attempt_record(task.task_id, attempt=attempt_idx)

    async def _finalize(task: GAIATask, attempt_idx: int, record: dict) -> dict:
        nonlocal live_finalizers, peak_finalizers
        live_finalizers += 1
        peak_finalizers = max(peak_finalizers, live_finalizers)
        await asyncio.sleep(0.005)
        live_finalizers -= 1
        record["finalized"] = True
        return record

    merged = await asyncio.gather(
        *(
            _run_task_pass_k(_task(f"t{i}"), pass_k=2, sem=sem, rollout=_rollout, finalize=_finalize)
            for i in range(3)
        )
    )
    assert peak_finalizers > 1
    assert all(r["finalized"] for r in merged)


# ===========================================================================
# Infrastructure failures count as failures (A.3 p.29)
# ===========================================================================


async def test_infra_failure_counts_in_the_denominator_not_the_numerator() -> None:
    merged = await _run_task_pass_k(
        _task(),
        pass_k=2,
        sem=asyncio.Semaphore(4),
        rollout=_stub_rollout(False, True, exit_reasons=("error", "done")),
    )
    assert merged["n_att"] == 2  # the crash is NOT dropped
    assert merged["n_pass"] == 1
    assert merged["infra_failures"] == 1
    assert merged["attempts"][0]["infra_failure"] is True
    assert merged["attempts"][1]["infra_failure"] is False
    assert merged["passed"] is True


async def test_two_infra_failures_score_as_a_failed_task() -> None:
    merged = await _run_task_pass_k(
        _task(),
        pass_k=2,
        sem=asyncio.Semaphore(4),
        rollout=_stub_rollout(False, False, exit_reasons=("error", "error")),
    )
    assert merged["n_att"] == 2
    assert merged["n_pass"] == 0
    assert merged["infra_failures"] == 2
    assert merged["passed"] is False
    # And it drags the round score down rather than vanishing from it.
    assert _round_pass_rate([merged], 2) == pytest.approx(0.0)


def test_infra_failure_is_keyed_on_exit_reason_error() -> None:
    assert _is_infra_failure({"exit_reason": "error"}) is True
    assert _is_infra_failure({"exit_reason": "done"}) is False
    assert _is_infra_failure({}) is False


# ===========================================================================
# Round score = the unbiased estimator (A.3 p.29, formula 6)
# ===========================================================================


def test_round_rate_under_pass_2_is_the_mean_of_the_estimator() -> None:
    records = [
        {"task_id": "a", "n_att": 2, "n_pass": 0},
        {"task_id": "b", "n_att": 2, "n_pass": 1},
        {"task_id": "c", "n_att": 2, "n_pass": 2},
    ]
    expected = sum(pass_at_k(2, c, 2) for c in (0, 1, 2)) / 3
    assert _round_pass_rate(records, 2) == pytest.approx(expected)
    assert _round_pass_rate(records, 2) == pytest.approx(2 / 3)


def test_round_rate_is_the_estimator_not_an_any_pass_ratio() -> None:
    """The two only diverge when n > k — and there the estimator must win.

    Four rollouts with one pass: "did any attempt pass" says 1.0, formula 6
    says 1 - C(3,2)/C(4,2) = 0.5.
    """
    records = [{"task_id": "a", "n_att": 4, "n_pass": 1}]
    any_pass = sum(1 for r in records if r["n_pass"]) / len(records)
    assert any_pass == 1.0
    assert _round_pass_rate(records, 2) == pytest.approx(0.5)


def test_at_n_equals_k_the_estimator_and_any_pass_coincide() -> None:
    """Why the gate is unaffected by the switch: at pass@2 with two rollouts
    the estimator *is* the solved-task ratio, so round_pass_rate keeps feeding
    ``_score_and_gate`` the same number it always did."""
    records = [
        {"task_id": "a", "n_att": 2, "n_pass": 0},
        {"task_id": "b", "n_att": 2, "n_pass": 1},
        {"task_id": "c", "n_att": 2, "n_pass": 2},
        {"task_id": "d", "n_att": 2, "n_pass": 0},
    ]
    any_pass = sum(1 for r in records if r["n_pass"]) / len(records)
    assert _round_pass_rate(records, 2) == pytest.approx(any_pass)


def test_round_rate_of_an_empty_round_is_zero() -> None:
    assert _round_pass_rate([], 2) == 0.0


def test_an_undersampled_task_is_scored_at_the_attempts_it_has() -> None:
    """k_eff = n_att rather than a guess; it degrades to "any attempt passed"."""
    records = [{"task_id": "a", "n_att": 1, "n_pass": 1}]
    assert _round_pass_rate(records, 2) == pytest.approx(1.0)


# ===========================================================================
# Reporting (SPEC §6.7) — n_pass/n_att reach the table
# ===========================================================================


def _round(*specs: tuple[str, int, int]) -> list[dict]:
    return [
        {
            "task_id": tid,
            "passed": n_pass > 0,
            "n_pass": n_pass,
            "n_att": n_att,
            "steps": 5,
            "cost_usd": 0.2,
            "total_tokens": 500,
        }
        for tid, n_pass, n_att in specs
    ]


def test_comparison_table_reports_attempt_counts_under_pass_k() -> None:
    out = _render([_round(("t1", 2, 2), ("t2", 1, 2), ("t3", 0, 2))], pass_k=2)
    assert "[pass@2]" in out
    assert "PASS 2/2" in out
    assert "PASS 1/2" in out
    assert "FAIL 0/2" in out
    assert "rollouts" in out
    assert "(3/6)" in out  # pooled per-attempt rate


def test_pass_2_masks_a_two_of_two_to_one_of_two_drift() -> None:
    """§7.1 p.21, reproduced on purpose, not fixed.

    Every task drifts 2/2 -> 1/2. pass@2 does not move a point; the pooled
    rollout rate halves. A report that printed only the first number would call
    this round unchanged, which is exactly how the paper's Global arm collapsed
    without tripping the seesaw constraint.
    """
    rounds = [
        _round(("t1", 2, 2), ("t2", 2, 2)),
        _round(("t1", 1, 2), ("t2", 1, 2)),
    ]
    out = _render(rounds, pass_k=2)
    assert _round_pass_rate(rounds[0], 2) == _round_pass_rate(rounds[1], 2) == 1.0
    assert "+0.0pp" in out  # pass@2 delta: nothing to see
    assert "-50.0pp" in out  # rollout rate: half the attempts stopped passing
