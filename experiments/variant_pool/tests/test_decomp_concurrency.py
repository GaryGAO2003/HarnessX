"""``--decomp-concurrency`` (M-35): parallel evaluation, minus the modes it breaks.

The evaluation path was fully serial -- ``for task ... for attempt ... await`` --
while the evolution loop saturates the endpoint at ``--concurrency 10``. One
evaluation arm therefore used roughly a tenth of the available throughput.

Two properties have to hold and are what these tests pin:

1. **The default is the old path.** Concurrency 1 must run and record one task at
   a time, so the JSONL streams exactly as before.
2. **Ledger routing is refused above 1.** ``SubtaskRouter.route`` reads
   ``TypeCreditLedger.rate()`` that concurrent tasks are writing, so overlapping
   tasks would make routing depend on which rollouts finished first. That arm
   would not be reproducible from its own frozen inputs.

``single`` and ``round_robin`` are pure functions of
``(task_id, attempt, subtask_index)`` -- verified here, since the refusal rests
on that being true.
"""
import zlib

import pytest

from experiments.variant_pool.subtask_pipeline import (
    DEFAULT_LEDGER_MIN_OBS,
    SubtaskRouter,
    TypeCreditLedger,
)
from recipe.gaia_evolver.run_variant_pool import build_arg_parser

VARIANTS = ("V0", "V1", "V2", "V3")


def _route(router, task_id="t1", attempt=0, index=0, stype="search", choice="V0"):
    return router.route(
        task_id=task_id,
        attempt=attempt,
        subtask_index=index,
        subtask_type=stype,
        task_level_choice=choice,
    )


# --- why the two safe modes are safe --------------------------------------


def test_single_routing_ignores_shared_state_entirely():
    router = SubtaskRouter("single", variant_ids=VARIANTS)
    assert _route(router, choice="V2") == "V2"
    assert _route(router, choice="V2", index=7, attempt=3) == "V2"


def test_round_robin_is_a_pure_function_of_task_attempt_and_index():
    router = SubtaskRouter("round_robin", variant_ids=VARIANTS)
    for task_id in ("alpha", "beta", "gamma"):
        for attempt in range(3):
            for index in range(4):
                expected = VARIANTS[
                    (zlib.crc32(task_id.encode()) + attempt + index) % len(VARIANTS)
                ]
                assert _route(router, task_id, attempt, index) == expected


def test_round_robin_is_unaffected_by_ledger_writes():
    ledger = TypeCreditLedger()
    router = SubtaskRouter("round_robin", variant_ids=VARIANTS, ledger=ledger)
    before = _route(router, "task-x", 0, 0)
    for _ in range(20):
        ledger.record(pairs=[("V3", "search")], passed=True)
    assert _route(router, "task-x", 0, 0) == before


def test_ledger_routing_does_change_when_other_tasks_write():
    # The concurrency refusal exists because of exactly this: if a concurrent
    # task lands these observations first, the route flips.
    ledger = TypeCreditLedger()
    router = SubtaskRouter(
        "ledger", variant_ids=VARIANTS, ledger=ledger, min_obs=DEFAULT_LEDGER_MIN_OBS
    )
    before = _route(router, stype="search", choice="V0")
    for _ in range(10):
        ledger.record(pairs=[("V2", "search")], passed=True)
    after = _route(router, stype="search", choice="V0")
    assert before != after, "ledger routing is supposed to be evidence-sensitive"
    assert after == "V2"


# --- the CLI contract -----------------------------------------------------


def test_default_is_serial():
    a = build_arg_parser().parse_args(["--provider-id", "deepseek", "--decomp-eval"])
    assert a.decomp_concurrency == 1


@pytest.mark.parametrize("n", ["2", "8", "16"])
def test_concurrency_is_accepted_with_a_safe_routing_mode(n):
    a = build_arg_parser().parse_args(
        ["--provider-id", "deepseek", "--decomp-eval",
         "--decomp-routing", "round_robin", "--decomp-concurrency", n]
    )
    assert a.decomp_concurrency == int(n)
    assert a.decomp_routing == "round_robin"


def test_ledger_plus_concurrency_is_rejected_by_the_run_path(monkeypatch, tmp_path):
    from recipe.gaia_evolver import run_variant_pool as rvp

    args = build_arg_parser().parse_args(
        ["--provider-id", "deepseek", "--decomp-eval",
         "--decomp-routing", "ledger", "--decomp-concurrency", "4"]
    )
    with pytest.raises(SystemExit, match="refused with --decomp-routing ledger"):
        rvp._run_decomp_eval(args, tmp_path, {})


def test_ledger_at_concurrency_one_is_not_rejected_for_that_reason(tmp_path):
    from recipe.gaia_evolver import run_variant_pool as rvp

    args = build_arg_parser().parse_args(
        ["--provider-id", "deepseek", "--decomp-eval",
         "--decomp-routing", "ledger", "--decomp-concurrency", "1"]
    )
    with pytest.raises(Exception) as excinfo:
        rvp._run_decomp_eval(args, tmp_path, {})
    # It still fails -- deps is empty -- but never for the concurrency reason.
    assert "refused with --decomp-routing ledger" not in str(excinfo.value)


@pytest.mark.parametrize("n", ["0", "-1"])
def test_non_positive_concurrency_is_refused(n, tmp_path):
    from recipe.gaia_evolver import run_variant_pool as rvp

    args = build_arg_parser().parse_args(
        ["--provider-id", "deepseek", "--decomp-eval", "--decomp-concurrency", n]
    )
    with pytest.raises(SystemExit, match="must be >= 1"):
        rvp._run_decomp_eval(args, tmp_path, {})
