"""``--decomp-credit`` (M-36): what a (variant x type) observation is scored on.

This is not a side knob. B2 -- decomposition routed by the credit ledger -- is
the thesis's headline arm, and ``B2 - B1`` is the answer to RQ2. B2 reads this
ledger to decide which variant handles which kind of subtask, so if the ledger
cannot separate competence the headline contrast has no mechanism behind it.

Under ``task`` (the default, and what every run so far used) a passing task
books a pass against *every distinct pair on the chain*: the searcher, the
calculator and the verifier are all credited for one final answer. The table
therefore records participation in successful tasks, not competence at a kind of
work.

``subtask_convergence`` books one observation per executed subtask, passing iff
that subtask finished inside its own step budget. Its limit is declared rather
than hidden: it scores completion, not correctness.
"""
import pytest

from experiments.variant_pool.subtask_pipeline import (
    CREDIT_MODES,
    CREDIT_SUBTASK_CONVERGENCE,
    CREDIT_TASK,
    DecompPlan,
    PipelineExecutor,
    SessionResult,
    SubtaskRouter,
    Synthesizer,
    TypeCreditLedger,
)
from recipe.gaia_evolver.run_variant_pool import build_arg_parser

PLAN = [
    {"id": "s1", "type": "search", "instruction": "find it", "dep": []},
    {"id": "s2", "type": "compute", "instruction": "add it up", "dep": ["s1"]},
    {"id": "s3", "type": "verify", "instruction": "check it", "dep": ["s2"]},
]
CAP = 20


class _Decomposer:
    async def decompose(self, task_id, task_text, *, profile=None):
        from experiments.variant_pool.subtask_pipeline import validate_plan

        return validate_plan(PLAN)


def _executor(ledger, mode, *, steps_by_id, routing="round_robin", variants=("V0", "V1", "V2")):
    async def runner(*, instruction, variant_id, max_steps, subtask_id, subtask_type):
        return SessionResult(output=f"out-{subtask_id}", steps=steps_by_id[subtask_id], cost_usd=0.0)

    async def scorer(output, ground_truth):
        return scorer.verdict

    scorer.verdict = True

    async def complete(prompt: str) -> str:
        return "FINAL ANSWER: 42"

    return (
        PipelineExecutor(
            runner=runner,
            scorer=scorer,
            decomposer=_Decomposer(),
            router=SubtaskRouter(routing, variant_ids=variants),
            synthesizer=Synthesizer(complete),
            credit_ledger=ledger,
            credit_mode=mode,
            subtask_max_steps=CAP,
        ),
        scorer,
    )


async def _run(ex, task_id="t1"):
    return await ex.run_attempt(
        task_id=task_id, task_text="TASK", ground_truth="42", attempt=0, task_level_choice="V0"
    )


# --- the defect the default has -------------------------------------------


@pytest.mark.asyncio
async def test_task_credit_books_one_outcome_against_every_pair_on_the_chain():
    ledger = TypeCreditLedger()
    # Every subtask blows its budget, so none of them "worked" -- yet the task
    # passes, and all three get credited.
    ex, _ = _executor(ledger, CREDIT_TASK, steps_by_id={"s1": CAP, "s2": CAP, "s3": CAP})
    result = await _run(ex)
    cells = ledger.matrix()
    assert len(cells) == 3
    for rec in result.subtasks:
        assert ledger.passes(rec.variant_id, rec.type) == 1, "the whole task's pass is smeared"


@pytest.mark.asyncio
async def test_task_credit_cannot_tell_a_capped_subtask_from_a_clean_one():
    a, b = TypeCreditLedger(), TypeCreditLedger()
    ex_a, _ = _executor(a, CREDIT_TASK, steps_by_id={"s1": 1, "s2": 1, "s3": 1})
    ex_b, _ = _executor(b, CREDIT_TASK, steps_by_id={"s1": CAP, "s2": CAP, "s3": CAP})
    await _run(ex_a)
    await _run(ex_b)
    assert a.matrix() == b.matrix(), "this indistinguishability is the defect"


# --- what the convergence signal does instead ------------------------------


@pytest.mark.asyncio
async def test_convergence_credit_separates_the_capped_subtask():
    ledger = TypeCreditLedger()
    ex, _ = _executor(
        ledger, CREDIT_SUBTASK_CONVERGENCE, steps_by_id={"s1": 3, "s2": CAP, "s3": 2}
    )
    result = await _run(ex)
    by_type = {r.type: r for r in result.subtasks}
    assert ledger.passes(by_type["search"].variant_id, "search") == 1
    assert ledger.passes(by_type["compute"].variant_id, "compute") == 0
    assert ledger.attempts(by_type["compute"].variant_id, "compute") == 1
    assert ledger.passes(by_type["verify"].variant_id, "verify") == 1


@pytest.mark.asyncio
async def test_convergence_credit_ignores_the_task_outcome_entirely():
    passing, failing = TypeCreditLedger(), TypeCreditLedger()
    ex_p, sp = _executor(passing, CREDIT_SUBTASK_CONVERGENCE, steps_by_id={"s1": 3, "s2": 4, "s3": 2})
    ex_f, sf = _executor(failing, CREDIT_SUBTASK_CONVERGENCE, steps_by_id={"s1": 3, "s2": 4, "s3": 2})
    sp.verdict, sf.verdict = True, False
    await _run(ex_p)
    await _run(ex_f)
    assert passing.matrix() == failing.matrix()


@pytest.mark.asyncio
async def test_a_subtask_exactly_at_the_cap_counts_as_not_converged():
    ledger = TypeCreditLedger()
    ex, _ = _executor(ledger, CREDIT_SUBTASK_CONVERGENCE, steps_by_id={"s1": CAP, "s2": 1, "s3": 1})
    result = await _run(ex)
    search = next(r for r in result.subtasks if r.type == "search")
    assert ledger.passes(search.variant_id, "search") == 0


@pytest.mark.asyncio
async def test_a_repeated_pair_books_twice_because_they_are_two_measurements():
    ledger = TypeCreditLedger()
    ex, _ = _executor(
        ledger,
        CREDIT_SUBTASK_CONVERGENCE,
        steps_by_id={"s1": 1, "s2": 1, "s3": 1},
        routing="single",  # every subtask -> V0
        variants=("V0",),
    )
    await _run(ex)
    assert ledger.attempts("V0", "search") == 1
    assert sum(ledger.attempts("V0", t) for t in ("search", "compute", "verify")) == 3


# --- contract -------------------------------------------------------------


def test_unknown_credit_mode_is_rejected_at_construction():
    with pytest.raises(ValueError, match="credit_mode must be one of"):
        _executor(TypeCreditLedger(), "per_subtask_judge", steps_by_id={})


def test_declared_modes_match_the_cli_choices():
    assert CREDIT_MODES == ("task", "subtask_convergence")


def test_cli_default_is_the_pre_flag_behaviour():
    a = build_arg_parser().parse_args(["--provider-id", "deepseek", "--decomp-eval"])
    assert a.decomp_credit == "task"


def test_cli_accepts_the_convergence_signal():
    a = build_arg_parser().parse_args(
        ["--provider-id", "deepseek", "--decomp-eval", "--decomp-credit", "subtask_convergence"]
    )
    assert a.decomp_credit == "subtask_convergence"


def test_cli_rejects_a_signal_we_have_not_built():
    with pytest.raises(SystemExit):
        build_arg_parser().parse_args(
            ["--provider-id", "deepseek", "--decomp-credit", "counterfactual"]
        )
