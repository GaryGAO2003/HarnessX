# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""Offline tests for the M1 subtask pipeline (``subtask_pipeline``).

Fully offline: every live seam (session runner, meta ``complete``, task-level
``scorer``) is a scripted async stub — never a provider, never a network call,
never a real rollout. Covers the schema/DAG validator, the three routing modes
(+ ledger cold-start + deterministic tie-break), the observational credit
ledger's Laplace math, both decomposition sources (llm repair/fallback + file
oracle), the per-attempt executor (happy path, fallback, verify gate), and the
B3 pool-profile briefing.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from experiments.variant_pool.subtask_pipeline import (  # noqa: E402
    CONTEXT_HEADER,
    PROFILE_HEADER,
    ROUTING_MODES,
    SUBTASK_TYPES,
    WHOLE_TASK_ID,
    WHOLE_TASK_TYPE,
    Decomposer,
    DecompParseError,
    DecompPlan,
    DecompUnavailable,
    DecompValidationError,
    FileDecomposer,
    LlmDecomposer,
    PipelineExecutor,
    SessionResult,
    SubtaskRouter,
    SubtaskSpec,
    Synthesizer,
    TypeCreditLedger,
    VerifyGate,
    build_pool_profile,
    validate_plan,
)


# ---------------------------------------------------------------------------
# helpers / async stubs
# ---------------------------------------------------------------------------
def _run(coro):
    return asyncio.run(coro)


def _rec(sid, stype="search", instruction=None, dep=None):
    rec = {"id": sid, "type": stype, "instruction": instruction or f"do {sid}"}
    if dep is not None:
        rec["dep"] = dep
    return rec


class _ScriptedComplete:
    """FIFO of response strings; records every prompt it is called with."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.prompts: list[str] = []
        self.calls = 0

    async def __call__(self, prompt: str) -> str:
        self.calls += 1
        self.prompts.append(prompt)
        if not self.responses:
            raise AssertionError("no scripted complete response left")
        return self.responses.pop(0)


class _ConstComplete:
    def __init__(self, text: str):
        self.text = text
        self.prompts: list[str] = []
        self.calls = 0

    async def __call__(self, prompt: str) -> str:
        self.calls += 1
        self.prompts.append(prompt)
        return self.text


class _RecordingRunner:
    def __init__(self, outputs=None, *, default="out", cost=0.01, steps=1):
        self.outputs = outputs or {}
        self.default = default
        self.cost = cost
        self.steps = steps
        self.calls: list[dict] = []

    async def __call__(self, *, instruction, variant_id, max_steps, subtask_id, subtask_type):
        self.calls.append(
            {
                "instruction": instruction,
                "variant_id": variant_id,
                "max_steps": max_steps,
                "subtask_id": subtask_id,
                "subtask_type": subtask_type,
            }
        )
        val = self.outputs.get(subtask_id, self.default)
        if isinstance(val, SessionResult):
            return val
        return SessionResult(output=str(val), steps=self.steps, cost_usd=self.cost)


class _RecordingScorer:
    def __init__(self, result=True):
        self.result = result
        self.calls: list[tuple[str, str]] = []

    async def __call__(self, output, ground_truth):
        self.calls.append((output, ground_truth))
        return self.result


class _FixedDecomposer(Decomposer):
    def __init__(self, plan):
        self.plan = plan
        self.calls = 0

    async def decompose(self, task_id, task_text, *, profile=None):
        self.calls += 1
        return self.plan


class _RaisingDecomposer(Decomposer):
    def __init__(self, exc):
        self.exc = exc

    async def decompose(self, task_id, task_text, *, profile=None):
        raise self.exc


# ---------------------------------------------------------------------------
# schema + topological order
# ---------------------------------------------------------------------------
def test_valid_linear_plan_orders_by_dependency():
    plan = validate_plan([_rec("s1"), _rec("s2", "compute", dep=["s1"])])
    assert isinstance(plan, DecompPlan)
    assert plan.order == ("s1", "s2")
    assert [s.id for s in plan.ordered()] == ["s1", "s2"]


def test_declared_order_out_of_topo_is_repaired():
    # s2 declared first but depends on s1 -> s1 must come first.
    plan = validate_plan([_rec("s2", "compute", dep=["s1"]), _rec("s1")])
    assert plan.order == ("s1", "s2")


def test_independent_nodes_keep_declared_order():
    plan = validate_plan([_rec("a"), _rec("b", "browse"), _rec("c", "compute")])
    assert plan.order == ("a", "b", "c")


def test_diamond_dag_orders_deterministically():
    plan = validate_plan(
        [
            _rec("s1"),
            _rec("s2", "browse", dep=["s1"]),
            _rec("s3", "compute", dep=["s1"]),
            _rec("s4", "verify", dep=["s2", "s3"]),
        ]
    )
    assert plan.order == ("s1", "s2", "s3", "s4")


def test_subtask_types_are_the_four():
    assert SUBTASK_TYPES == {"search", "browse", "compute", "verify"}


def test_non_list_rejected():
    with pytest.raises(DecompValidationError):
        validate_plan({"id": "s1"})


def test_empty_rejected():
    with pytest.raises(DecompValidationError):
        validate_plan([])


def test_over_max_subtasks_rejected():
    with pytest.raises(DecompValidationError):
        validate_plan([_rec("a"), _rec("b"), _rec("c")], max_subtasks=2)


def test_invalid_type_rejected():
    with pytest.raises(DecompValidationError):
        validate_plan([_rec("s1", "translate")])


def test_empty_instruction_rejected():
    with pytest.raises(DecompValidationError):
        validate_plan([{"id": "s1", "type": "search", "instruction": "   "}])


def test_duplicate_id_rejected():
    with pytest.raises(DecompValidationError):
        validate_plan([_rec("s1"), _rec("s1", "compute")])


def test_dep_to_unknown_id_rejected():
    with pytest.raises(DecompValidationError):
        validate_plan([_rec("s1", "compute", dep=["ghost"])])


def test_self_dependency_rejected():
    with pytest.raises(DecompValidationError):
        validate_plan([_rec("s1", "compute", dep=["s1"])])


def test_cycle_rejected():
    with pytest.raises(DecompValidationError):
        validate_plan([_rec("s1", "compute", dep=["s2"]), _rec("s2", "compute", dep=["s1"])])


def test_missing_dep_defaults_to_empty():
    plan = validate_plan([_rec("s1")])  # no dep key
    assert plan.subtasks[0].dep == ()


def test_none_dep_allowed():
    plan = validate_plan([{"id": "s1", "type": "search", "instruction": "x", "dep": None}])
    assert plan.subtasks[0].dep == ()


def test_non_string_dep_rejected():
    with pytest.raises(DecompValidationError):
        validate_plan([_rec("s1"), _rec("s2", "compute", dep=[1])])


def test_plan_to_records_roundtrips():
    records = [_rec("s1"), _rec("s2", "compute", dep=["s1"])]
    plan = validate_plan(records)
    assert plan.to_records() == [
        {"id": "s1", "type": "search", "instruction": "do s1", "dep": []},
        {"id": "s2", "type": "compute", "instruction": "do s2", "dep": ["s1"]},
    ]


# ---------------------------------------------------------------------------
# routing
# ---------------------------------------------------------------------------
def _route(router, *, task_id="t", attempt=0, subtask_index=0, subtask_type="search", choice="V0"):
    return router.route(
        task_id=task_id,
        attempt=attempt,
        subtask_index=subtask_index,
        subtask_type=subtask_type,
        task_level_choice=choice,
    )


def test_router_rejects_bad_mode():
    with pytest.raises(ValueError):
        SubtaskRouter("bogus", variant_ids=["V0"])


def test_router_rejects_empty_pool():
    with pytest.raises(ValueError):
        SubtaskRouter("single", variant_ids=[])


def test_routing_modes_constant():
    assert ROUTING_MODES == ("single", "round_robin", "ledger")


def test_single_always_returns_task_level_choice():
    router = SubtaskRouter("single", variant_ids=["V0", "V1", "V2"])
    assert _route(router, choice="V2", subtask_type="compute") == "V2"
    assert _route(router, choice="V1", subtask_index=5) == "V1"


def test_round_robin_is_deterministic_and_ignores_choice():
    router = SubtaskRouter("round_robin", variant_ids=["V0", "V1", "V2"])
    a = _route(router, task_id="task-x", attempt=1, subtask_index=2, choice="V0")
    b = _route(router, task_id="task-x", attempt=1, subtask_index=2, choice="V9-different")
    assert a == b  # independent of task-level choice
    assert a == _route(router, task_id="task-x", attempt=1, subtask_index=2, choice="V0")  # stable


def test_round_robin_cycles_through_whole_pool():
    router = SubtaskRouter("round_robin", variant_ids=["V0", "V1", "V2"])
    seen = {_route(router, task_id="q", subtask_index=i) for i in range(3)}
    assert seen == {"V0", "V1", "V2"}  # three consecutive indices span the pool


def test_round_robin_attempt_and_index_are_symmetric():
    # The rotation is keyed on (crc32(task_id) + attempt + subtask_index), so
    # +1 on attempt equals +1 on subtask_index.
    router = SubtaskRouter("round_robin", variant_ids=["V0", "V1", "V2", "V3"])
    assert _route(router, task_id="z", attempt=1, subtask_index=0) == _route(
        router, task_id="z", attempt=0, subtask_index=1
    )


def test_round_robin_single_variant_pool():
    router = SubtaskRouter("round_robin", variant_ids=["V0"])
    assert _route(router, subtask_index=7) == "V0"


def test_ledger_routing_requires_ledger():
    router = SubtaskRouter("ledger", variant_ids=["V0", "V1"])
    with pytest.raises(ValueError):
        _route(router)


def test_ledger_picks_highest_warm_rate():
    ledger = TypeCreditLedger()
    for _ in range(3):
        ledger.record(pairs=[("V1", "search")], passed=True)
    ledger.record(pairs=[("V0", "search")], passed=True)
    ledger.record(pairs=[("V0", "search")], passed=False)
    ledger.record(pairs=[("V0", "search")], passed=False)
    router = SubtaskRouter("ledger", variant_ids=["V0", "V1"], ledger=ledger, min_obs=2)
    assert _route(router, subtask_type="search", choice="V0") == "V1"


def test_ledger_cold_cell_falls_back_to_task_level():
    ledger = TypeCreditLedger()
    ledger.record(pairs=[("V1", "compute")], passed=True)  # only 1 obs
    router = SubtaskRouter("ledger", variant_ids=["V0", "V1"], ledger=ledger, min_obs=3)
    assert _route(router, subtask_type="compute", choice="V0") == "V0"


def test_ledger_min_obs_boundary_is_inclusive():
    ledger = TypeCreditLedger()
    for _ in range(3):
        ledger.record(pairs=[("V1", "browse")], passed=True)
    router = SubtaskRouter("ledger", variant_ids=["V0", "V1"], ledger=ledger, min_obs=3)
    # exactly 3 observations == warm -> ledger wins over the task-level choice
    assert _route(router, subtask_type="browse", choice="V0") == "V1"


def test_ledger_ties_break_on_lowest_variant_index():
    ledger = TypeCreditLedger()
    ledger.record(pairs=[("V0", "search")], passed=True)
    ledger.record(pairs=[("V1", "search")], passed=True)
    router = SubtaskRouter("ledger", variant_ids=["V0", "V1"], ledger=ledger, min_obs=1)
    assert _route(router, subtask_type="search", choice="V1") == "V0"


# ---------------------------------------------------------------------------
# credit ledger math
# ---------------------------------------------------------------------------
def test_empty_cell_rate_is_one_half():
    ledger = TypeCreditLedger()
    assert ledger.rate("V0", "search") == pytest.approx(0.5)
    assert ledger.attempts("V0", "search") == 0
    assert ledger.passes("V0", "search") == 0


def test_record_pass_updates_passes_and_attempts():
    ledger = TypeCreditLedger()
    ledger.record(pairs=[("V0", "search")], passed=True)
    assert (ledger.passes("V0", "search"), ledger.attempts("V0", "search")) == (1, 1)
    assert ledger.rate("V0", "search") == pytest.approx((1 + 1) / (1 + 2))


def test_record_fail_updates_only_attempts():
    ledger = TypeCreditLedger()
    ledger.record(pairs=[("V0", "search")], passed=False)
    assert (ledger.passes("V0", "search"), ledger.attempts("V0", "search")) == (0, 1)
    assert ledger.rate("V0", "search") == pytest.approx(1 / 3)


def test_record_dedups_pairs_within_one_attempt():
    ledger = TypeCreditLedger()
    ledger.record(pairs=[("V0", "search"), ("V0", "search")], passed=True)
    assert ledger.attempts("V0", "search") == 1  # counted once


def test_matrix_serialization():
    ledger = TypeCreditLedger()
    ledger.record(pairs=[("V0", "search"), ("V1", "compute")], passed=True)
    matrix = ledger.matrix()
    assert matrix["V0::search"] == {
        "variant": "V0",
        "type": "search",
        "passes": 1,
        "attempts": 1,
        "rate": pytest.approx(2 / 3),
    }
    assert set(matrix) == {"V0::search", "V1::compute"}


# ---------------------------------------------------------------------------
# decomposition sources
# ---------------------------------------------------------------------------
_VALID_JSON = json.dumps([{"id": "s1", "type": "search", "instruction": "find it", "dep": []}])


def test_llm_decomposer_success_single_call():
    complete = _ScriptedComplete([_VALID_JSON])
    dec = LlmDecomposer(complete)
    plan = _run(dec.decompose("t1", "solve me"))
    assert plan.order == ("s1",)
    assert complete.calls == 1


def test_llm_decomposer_strips_code_fences():
    fenced = "```json\n" + _VALID_JSON + "\n```"
    dec = LlmDecomposer(_ScriptedComplete([fenced]))
    plan = _run(dec.decompose("t1", "x"))
    assert plan.subtasks[0].id == "s1"


def test_llm_decomposer_accepts_subtasks_wrapper():
    wrapped = json.dumps({"subtasks": [{"id": "s1", "type": "search", "instruction": "x"}]})
    dec = LlmDecomposer(_ScriptedComplete([wrapped]))
    assert _run(dec.decompose("t", "x")).order == ("s1",)


def test_llm_decomposer_repairs_once_then_succeeds():
    complete = _ScriptedComplete(["not json at all", _VALID_JSON])
    dec = LlmDecomposer(complete)
    plan = _run(dec.decompose("t1", "x"))
    assert plan.order == ("s1",)
    assert complete.calls == 2  # one repair retry
    assert dec.calls == 2


def test_llm_decomposer_fails_after_repair():
    complete = _ScriptedComplete(["garbage", "still garbage"])
    dec = LlmDecomposer(complete)
    with pytest.raises(DecompParseError):
        _run(dec.decompose("t1", "x"))
    assert complete.calls == 2


def test_llm_decomposer_validation_failure_triggers_repair():
    bad = json.dumps([{"id": "s1", "type": "translate", "instruction": "x"}])  # bad type
    complete = _ScriptedComplete([bad, _VALID_JSON])
    dec = LlmDecomposer(complete)
    plan = _run(dec.decompose("t", "x"))
    assert plan.order == ("s1",)
    assert complete.calls == 2


def test_llm_decomposer_profile_injected_when_present():
    complete = _ScriptedComplete([_VALID_JSON])
    dec = LlmDecomposer(complete, profile_text="V0 excels at search")
    _run(dec.decompose("t", "x"))
    assert PROFILE_HEADER in complete.prompts[0]
    assert "V0 excels at search" in complete.prompts[0]


def test_llm_decomposer_no_profile_segment_by_default():
    complete = _ScriptedComplete([_VALID_JSON])
    dec = LlmDecomposer(complete)  # no profile
    _run(dec.decompose("t", "x"))
    assert PROFILE_HEADER not in complete.prompts[0]


def test_llm_decomposer_per_call_profile_overrides():
    complete = _ScriptedComplete([_VALID_JSON])
    dec = LlmDecomposer(complete)
    _run(dec.decompose("t", "x", profile="dynamic brief"))
    assert "dynamic brief" in complete.prompts[0]


def test_file_decomposer_known_task(tmp_path):
    path = tmp_path / "oracle.json"
    path.write_text(
        json.dumps({"task-A": [{"id": "s1", "type": "search", "instruction": "look"}]}),
        encoding="utf-8",
    )
    dec = FileDecomposer.from_path(path)
    assert _run(dec.decompose("task-A", "q")).order == ("s1",)


def test_file_decomposer_unknown_task_raises_unavailable(tmp_path):
    path = tmp_path / "oracle.json"
    path.write_text(json.dumps({"task-A": [{"id": "s1", "type": "search", "instruction": "x"}]}), encoding="utf-8")
    dec = FileDecomposer.from_path(path)
    with pytest.raises(DecompUnavailable):
        _run(dec.decompose("task-MISSING", "q"))


def test_file_decomposer_replay_is_deterministic(tmp_path):
    path = tmp_path / "oracle.json"
    path.write_text(
        json.dumps({"t": [_rec("s1"), _rec("s2", "compute", dep=["s1"])]}), encoding="utf-8"
    )
    dec = FileDecomposer.from_path(path)
    first = _run(dec.decompose("t", "q")).to_records()
    second = _run(dec.decompose("t", "q")).to_records()
    assert first == second


def test_file_decomposer_rejects_non_object(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text(json.dumps([1, 2, 3]), encoding="utf-8")
    with pytest.raises(DecompValidationError):
        FileDecomposer.from_path(path)


# ---------------------------------------------------------------------------
# executor
# ---------------------------------------------------------------------------
def _executor(*, decomposer, runner, scorer, router, synthesizer, ledger=None, verify_gate=None, max_steps=10):
    return PipelineExecutor(
        runner=runner,
        scorer=scorer,
        decomposer=decomposer,
        router=router,
        synthesizer=synthesizer,
        credit_ledger=ledger,
        verify_gate=verify_gate,
        subtask_max_steps=max_steps,
    )


def test_executor_happy_path_runs_dag_and_records_credit():
    plan = validate_plan([_rec("s1"), _rec("s2", "compute", dep=["s1"])])
    runner = _RecordingRunner(outputs={"s1": "the-s1-output", "s2": "the-s2-output"})
    scorer = _RecordingScorer(result=True)
    synth_complete = _ConstComplete("FINAL ANSWER: 42")
    synth = Synthesizer(synth_complete)
    ledger = TypeCreditLedger()
    ex = _executor(
        decomposer=_FixedDecomposer(plan),
        runner=runner,
        scorer=scorer,
        router=SubtaskRouter("single", variant_ids=["V0"]),
        synthesizer=synth,
        ledger=ledger,
    )
    result = _run(
        ex.run_attempt(
            task_id="t", task_text="the task", ground_truth="42", attempt=0, task_level_choice="V0"
        )
    )
    assert result.fallback is False
    assert result.passed is True
    assert [c["subtask_id"] for c in runner.calls] == ["s1", "s2"]
    assert all(c["variant_id"] == "V0" for c in runner.calls)  # single routing
    assert synth_complete.calls == 1  # synthesis fired once
    assert result.final_output == "FINAL ANSWER: 42"
    assert ledger.attempts("V0", "search") == 1
    assert ledger.attempts("V0", "compute") == 1
    assert ledger.passes("V0", "search") == 1


def test_executor_passes_dependency_output_into_prompt():
    plan = validate_plan([_rec("s1"), _rec("s2", "compute", dep=["s1"])])
    runner = _RecordingRunner(outputs={"s1": "PREREQ-OUTPUT"})
    ex = _executor(
        decomposer=_FixedDecomposer(plan),
        runner=runner,
        scorer=_RecordingScorer(True),
        router=SubtaskRouter("single", variant_ids=["V0"]),
        synthesizer=Synthesizer(_ConstComplete("FINAL ANSWER: x")),
    )
    _run(ex.run_attempt(task_id="t", task_text="q", ground_truth="x", attempt=0, task_level_choice="V0"))
    s2_prompt = runner.calls[1]["instruction"]
    assert CONTEXT_HEADER in s2_prompt
    assert "PREREQ-OUTPUT" in s2_prompt
    assert "[Result of s1]" in s2_prompt
    # first subtask has no deps -> bare instruction
    assert runner.calls[0]["instruction"] == "do s1"


def test_executor_scorer_failure_is_recorded():
    plan = validate_plan([_rec("s1")])
    ledger = TypeCreditLedger()
    ex = _executor(
        decomposer=_FixedDecomposer(plan),
        runner=_RecordingRunner(),
        scorer=_RecordingScorer(result=False),
        router=SubtaskRouter("single", variant_ids=["V0"]),
        synthesizer=Synthesizer(_ConstComplete("FINAL ANSWER: nope")),
        ledger=ledger,
    )
    result = _run(ex.run_attempt(task_id="t", task_text="q", ground_truth="yes", attempt=0, task_level_choice="V0"))
    assert result.passed is False
    assert ledger.passes("V0", "search") == 0
    assert ledger.attempts("V0", "search") == 1  # a failed attempt still counts


def test_executor_fallback_on_parse_error_skips_synthesis_and_credit():
    runner = _RecordingRunner(outputs={WHOLE_TASK_ID: "whole-task-answer"})
    synth_complete = _ConstComplete("SHOULD-NOT-BE-CALLED")
    synth = Synthesizer(synth_complete)
    ledger = TypeCreditLedger()
    ex = _executor(
        decomposer=_RaisingDecomposer(DecompParseError("boom")),
        runner=runner,
        scorer=_RecordingScorer(True),
        router=SubtaskRouter("single", variant_ids=["V0"]),
        synthesizer=synth,
        ledger=ledger,
    )
    result = _run(ex.run_attempt(task_id="t", task_text="WHOLE", ground_truth="x", attempt=0, task_level_choice="V0"))
    assert result.fallback is True
    assert result.fallback_reason == "parse_failure"
    assert result.plan is None
    assert synth_complete.calls == 0  # synthesis skipped
    assert ledger.matrix() == {}  # no credit booked
    assert len(runner.calls) == 1
    assert runner.calls[0]["instruction"] == "WHOLE"
    assert runner.calls[0]["subtask_type"] == WHOLE_TASK_TYPE
    assert result.final_output == "whole-task-answer"


def test_executor_fallback_reason_oracle_missing():
    ex = _executor(
        decomposer=_RaisingDecomposer(DecompUnavailable("no entry")),
        runner=_RecordingRunner(),
        scorer=_RecordingScorer(True),
        router=SubtaskRouter("single", variant_ids=["V0"]),
        synthesizer=Synthesizer(_ConstComplete("x")),
    )
    result = _run(ex.run_attempt(task_id="t", task_text="q", ground_truth="x", attempt=0, task_level_choice="V0"))
    assert result.fallback_reason == "oracle_missing"


def test_executor_verify_gate_off_never_verifies():
    plan = validate_plan([_rec("s1")])
    runner = _RecordingRunner()
    ex = _executor(
        decomposer=_FixedDecomposer(plan),
        runner=runner,
        scorer=_RecordingScorer(True),
        router=SubtaskRouter("single", variant_ids=["V0"]),
        synthesizer=Synthesizer(_ConstComplete("FINAL ANSWER: x")),
        verify_gate=None,
    )
    result = _run(ex.run_attempt(task_id="t", task_text="q", ground_truth="x", attempt=0, task_level_choice="V0"))
    assert len(runner.calls) == 1
    assert result.subtasks[0].retried is False


def test_executor_verify_gate_retries_once_on_failure():
    plan = validate_plan([_rec("s1")])
    runner = _RecordingRunner()
    verify_complete = _ScriptedComplete(["NO"])  # first (and only) verify fails
    ex = _executor(
        decomposer=_FixedDecomposer(plan),
        runner=runner,
        scorer=_RecordingScorer(True),
        router=SubtaskRouter("single", variant_ids=["V0"]),
        synthesizer=Synthesizer(_ConstComplete("FINAL ANSWER: x")),
        verify_gate=VerifyGate(verify_complete),
    )
    result = _run(ex.run_attempt(task_id="t", task_text="q", ground_truth="x", attempt=0, task_level_choice="V0"))
    assert len(runner.calls) == 2  # original + one retry
    assert verify_complete.calls == 1  # verified once; no re-verify after retry
    assert result.subtasks[0].retried is True


def test_executor_verify_gate_no_retry_on_pass():
    plan = validate_plan([_rec("s1")])
    runner = _RecordingRunner()
    ex = _executor(
        decomposer=_FixedDecomposer(plan),
        runner=runner,
        scorer=_RecordingScorer(True),
        router=SubtaskRouter("single", variant_ids=["V0"]),
        synthesizer=Synthesizer(_ConstComplete("FINAL ANSWER: x")),
        verify_gate=VerifyGate(_ScriptedComplete(["YES"])),
    )
    result = _run(ex.run_attempt(task_id="t", task_text="q", ground_truth="x", attempt=0, task_level_choice="V0"))
    assert len(runner.calls) == 1
    assert result.subtasks[0].retried is False


def test_executor_verify_gate_skips_verify_type_subtasks():
    plan = validate_plan([_rec("s1", "verify")])
    runner = _RecordingRunner()
    verify_complete = _ScriptedComplete([])  # would raise if called
    ex = _executor(
        decomposer=_FixedDecomposer(plan),
        runner=runner,
        scorer=_RecordingScorer(True),
        router=SubtaskRouter("single", variant_ids=["V0"]),
        synthesizer=Synthesizer(_ConstComplete("FINAL ANSWER: x")),
        verify_gate=VerifyGate(verify_complete),
    )
    _run(ex.run_attempt(task_id="t", task_text="q", ground_truth="x", attempt=0, task_level_choice="V0"))
    assert verify_complete.calls == 0  # verify-type subtask is not re-verified
    assert len(runner.calls) == 1


def test_executor_ledger_routing_records_used_variant():
    plan = validate_plan([_rec("s1", "search")])
    ledger = TypeCreditLedger()
    # warm V1 on search so ledger routes s1 -> V1
    for _ in range(3):
        ledger.record(pairs=[("V1", "search")], passed=True)
    runner = _RecordingRunner()
    ex = _executor(
        decomposer=_FixedDecomposer(plan),
        runner=runner,
        scorer=_RecordingScorer(True),
        router=SubtaskRouter("ledger", variant_ids=["V0", "V1"], ledger=ledger, min_obs=3),
        synthesizer=Synthesizer(_ConstComplete("FINAL ANSWER: x")),
        ledger=ledger,
    )
    result = _run(ex.run_attempt(task_id="t", task_text="q", ground_truth="x", attempt=0, task_level_choice="V0"))
    assert runner.calls[0]["variant_id"] == "V1"
    assert result.used_pairs == (("V1", "search"),)
    assert ledger.attempts("V1", "search") == 4  # 3 seeded + this attempt


def test_attempt_result_to_json_shape():
    plan = validate_plan([_rec("s1")])
    ex = _executor(
        decomposer=_FixedDecomposer(plan),
        runner=_RecordingRunner(),
        scorer=_RecordingScorer(True),
        router=SubtaskRouter("single", variant_ids=["V0"]),
        synthesizer=Synthesizer(_ConstComplete("FINAL ANSWER: 7")),
    )
    payload = _run(
        ex.run_attempt(task_id="t", task_text="q", ground_truth="7", attempt=1, task_level_choice="V0")
    ).to_json()
    assert payload["task_id"] == "t"
    assert payload["attempt"] == 1
    assert payload["fallback"] is False
    assert payload["decomposition"] == [{"id": "s1", "type": "search", "instruction": "do s1", "dep": []}]
    assert payload["subtasks"][0]["variant_id"] == "V0"


# ---------------------------------------------------------------------------
# B3 pool profile
# ---------------------------------------------------------------------------
def _write_pool_state(run_dir: Path, round_idx: int, routing: dict) -> None:
    rdir = run_dir / f"R{round_idx}"
    rdir.mkdir(parents=True, exist_ok=True)
    (rdir / "pool_state.json").write_text(json.dumps({"routing": routing}), encoding="utf-8")


def test_build_pool_profile_reads_latest_round(tmp_path):
    _write_pool_state(tmp_path, 0, {"V0": ["t1", "t2", "t3"]})
    _write_pool_state(tmp_path, 1, {"V0": ["t1"], "V1": ["t2", "t3"]})
    profile = build_pool_profile(tmp_path)
    assert "V0" in profile and "V1" in profile
    assert "carries 1 routed task(s)" in profile  # V0 in latest (R1)
    assert "carries 2 routed task(s)" in profile  # V1 in latest (R1)


def test_build_pool_profile_no_state_raises(tmp_path):
    with pytest.raises(ValueError):
        build_pool_profile(tmp_path)


def test_build_pool_profile_empty_routing_raises(tmp_path):
    _write_pool_state(tmp_path, 0, {})
    with pytest.raises(ValueError):
        build_pool_profile(tmp_path)
