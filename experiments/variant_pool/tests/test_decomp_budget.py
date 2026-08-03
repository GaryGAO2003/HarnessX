"""``--decomp-budget`` (M-39): equal budget by construction, not by footnote.

CH4 claims the decomposed arm is budget-matched to the undivided one. That claim
was false when written: each subtask received the whole-task cap, so a decomposed
attempt spent about 2.2x the steps of an undivided one. Under that arrangement a
positive result is answerable with "you spent twice the compute" and a null one
says nothing at all.

A flat --decomp-subtask-max-steps cannot fix it. Over the frozen 103-task plans
the subtask count runs 1..12 (mean 4.38, median 4), so a cap of 4 still lets 25%
of tasks exceed the undivided budget and a cap of 5 lets 41%. The division has to
be per-task because the plan length is.
"""
import pytest

from experiments.variant_pool.subtask_pipeline import (
    BUDGET_MODES,
    DEFAULT_BUDGET_MODE,
    PipelineExecutor,
    SessionResult,
    SubtaskRouter,
    Synthesizer,
    validate_plan,
)
from recipe.gaia_evolver.run_variant_pool import build_arg_parser

CAP = 20


def _plan_of(n):
    return [
        {"id": f"s{i}", "type": "search", "instruction": f"step {i}",
         "dep": [f"s{i-1}"] if i else []}
        for i in range(n)
    ]


def _executor(n_subtasks, mode, cap=CAP):
    seen = []

    class _D:
        async def decompose(self, task_id, task_text, *, profile=None):
            return validate_plan(_plan_of(n_subtasks))

    async def runner(*, instruction, variant_id, max_steps, subtask_id, subtask_type):
        seen.append(max_steps)
        return SessionResult(output="x", steps=1, cost_usd=0.0)

    async def scorer(output, gt):
        return True

    async def complete(prompt):
        return "FINAL ANSWER: 42"

    return PipelineExecutor(
        runner=runner,
        scorer=scorer,
        decomposer=_D(),
        router=SubtaskRouter("single", variant_ids=("V0",)),
        synthesizer=Synthesizer(complete),
        budget_mode=mode,
        subtask_max_steps=cap,
    ), seen


async def _run(ex):
    return await ex.run_attempt(
        task_id="t", task_text="TASK", ground_truth="42", attempt=0, task_level_choice="V0"
    )


# --- the default must not move -------------------------------------------


@pytest.mark.asyncio
async def test_default_still_gives_every_subtask_the_full_cap():
    ex, seen = _executor(5, DEFAULT_BUDGET_MODE)
    await _run(ex)
    assert seen == [CAP] * 5


@pytest.mark.asyncio
async def test_default_is_what_makes_the_decomposed_arm_cost_more():
    # The defect, asserted rather than described: five subtasks may spend five
    # times the undivided budget.
    ex, seen = _executor(5, DEFAULT_BUDGET_MODE)
    await _run(ex)
    assert sum(seen) == 5 * CAP > CAP


# --- shared budget --------------------------------------------------------


@pytest.mark.parametrize("n,expected", [(1, 20), (2, 10), (3, 6), (4, 5), (5, 4), (6, 3), (12, 1)])
@pytest.mark.asyncio
async def test_shared_divides_the_chain_budget_by_plan_length(n, expected):
    ex, seen = _executor(n, "shared")
    await _run(ex)
    assert seen == [expected] * n


@pytest.mark.parametrize("n", [1, 2, 3, 4, 5, 6, 7, 8, 12])
@pytest.mark.asyncio
async def test_shared_never_exceeds_the_undivided_budget(n):
    ex, seen = _executor(n, "shared")
    await _run(ex)
    assert sum(seen) <= CAP, "this is the whole point of the mode"


@pytest.mark.asyncio
async def test_a_plan_longer_than_the_budget_gets_one_step_each_not_zero():
    # Floor division alone would hand out 0 steps whenever the plan is longer
    # than the budget, and the chain could never run. 20 subtasks is the schema
    # maximum, so the case is reached with a smaller cap rather than a longer
    # plan -- a 25-subtask plan is rejected by validate_plan and would fall back
    # to a whole-task rollout, testing nothing.
    ex, seen = _executor(20, "shared", cap=10)
    await _run(ex)
    assert seen == [1] * 20
    assert sum(seen) > 10, "the floor is the one place the chain may exceed its budget"


@pytest.mark.asyncio
async def test_convergence_credit_scores_against_the_shared_cap_not_the_full_one():
    # Otherwise a subtask given 4 steps would be judged against 20 and always
    # count as converged, silently disabling the M-36 signal under this mode.
    from experiments.variant_pool.subtask_pipeline import TypeCreditLedger

    ledger = TypeCreditLedger()

    class _D:
        async def decompose(self, task_id, task_text, *, profile=None):
            return validate_plan(_plan_of(5))

    async def runner(*, instruction, variant_id, max_steps, subtask_id, subtask_type):
        return SessionResult(output="x", steps=max_steps, cost_usd=0.0)  # exactly at cap

    async def scorer(output, gt):
        return True

    async def complete(prompt):
        return "FINAL ANSWER: 42"

    ex = PipelineExecutor(
        runner=runner, scorer=scorer, decomposer=_D(),
        router=SubtaskRouter("single", variant_ids=("V0",)),
        synthesizer=Synthesizer(complete), credit_mode="subtask_convergence",
        budget_mode="shared", credit_ledger=ledger, subtask_max_steps=CAP,
    )
    await _run(ex)
    assert ledger.passes("V0", "search") == 0, "at the shared cap = not converged"
    assert ledger.attempts("V0", "search") == 5


# --- contract -------------------------------------------------------------


def test_unknown_budget_mode_is_rejected():
    with pytest.raises(ValueError, match="budget_mode must be one of"):
        _executor(3, "proportional")


def test_declared_modes_match_the_cli():
    assert BUDGET_MODES == ("per_subtask", "shared")


def test_cli_default_is_the_pre_flag_behaviour():
    a = build_arg_parser().parse_args(["--provider-id", "deepseek", "--decomp-eval"])
    assert a.decomp_budget == "per_subtask"


def test_cli_accepts_shared():
    a = build_arg_parser().parse_args(
        ["--provider-id", "deepseek", "--decomp-eval", "--decomp-budget", "shared"]
    )
    assert a.decomp_budget == "shared"


def test_the_fallback_path_keeps_the_whole_task_budget():
    # A decomposition failure degrades to one whole-task rollout, which IS the
    # undivided arm; dividing its budget would penalise a task for its planner
    # failing rather than for being decomposed.
    import inspect

    from experiments.variant_pool import subtask_pipeline

    src = inspect.getsource(subtask_pipeline.PipelineExecutor._fallback)
    assert "max_steps=self.subtask_max_steps" in src
