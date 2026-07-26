# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""Offline unit tests for ``variant_pool.router`` (W2 + W7 + routing freeze).

The routing-freeze cases are the load-bearing ones: they are what separates a
real online router from a disguised oracle (SPEC §6.2).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from variant_pool.ledger import SuccessLedger
from variant_pool.pool import VariantPool
from variant_pool.router import CLUSTER_MODES, ROUTING_MODES, Router, RoutingFreezeError


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _pool(tmp_path: Path, n_variants: int = 2, tasks: set[str] | None = None) -> VariantPool:
    """A pool of ``n_variants`` with placeholder paths (no files needed)."""
    pool = VariantPool()
    root = pool.add_root(tmp_path / "V0.yaml", tmp_path / "learnings_V0.md", tasks=tasks)
    for _ in range(n_variants - 1):
        pool.fork(root.variant_id, set(), at_round=1)
    return pool


def _task_router(**kwargs) -> Router:
    """The legacy per-task tournament, now an explicit compatibility arm."""
    return Router(routing_mode="task_tournament", **kwargs)


# ===========================================================================
# Routing freeze (SPEC §6.2) — the correctness core
# ===========================================================================


def test_freeze_routing_uses_prior_rounds_only_and_rejects_this_round(tmp_path: Path) -> None:
    """The fixture is built so that this round's results *would* flip routing.

    Prior evidence (round 0) favours V0. Round 1's own rollouts favour V1. A
    frozen routing pass for round 1 must return V0, and once round 1 is in the
    ledger it must refuse to re-freeze round 1 at all.
    """
    pool = _pool(tmp_path, n_variants=2)
    ledger = SuccessLedger()
    router = Router(task_to_cluster={"t": "c"})

    # --- prior round only -------------------------------------------------
    ledger.record("V0", "t", n_pass=2, n_att=2, round_idx=0)  # V0 = 3/4
    frozen = router.freeze_routing(["t"], pool, ledger, round_idx=1)
    assert frozen["t"] == "V0"

    # --- this round's rollouts land in the ledger --------------------------
    ledger.record("V1", "t", n_pass=2, n_att=2, round_idx=1)
    ledger.record("V0", "t", n_pass=0, n_att=2, round_idx=1)

    # the fixture really would flip: an unfrozen argmax now prefers V1
    assert router.route("t", pool, ledger, before_round=2) == "V1"

    # ... but the frozen map handed out before the round is untouched ...
    assert frozen["t"] == "V0"

    # ... and re-freezing round 1 is refused outright, not silently answered
    with pytest.raises(RoutingFreezeError, match="routing freeze violated"):
        router.freeze_routing(["t"], pool, ledger, round_idx=1)


def test_route_ignores_cells_written_in_the_frozen_round(tmp_path: Path) -> None:
    """Second line of defence: even reached directly, ``route`` cannot see it.

    V0 holds strong prior evidence; V1 holds an even stronger result from the
    round being frozen. At ``before_round=1`` the V1 cell is invisible, so V0
    wins; at ``before_round=2`` the same cell is prior evidence and V1 wins.
    """
    pool = _pool(tmp_path, n_variants=2)
    ledger = SuccessLedger()
    router = Router(task_to_cluster={"t": "c"})

    ledger.record("V0", "t", n_pass=2, n_att=2, round_idx=0)  # prior: 3/4
    ledger.record("V1", "t", n_pass=4, n_att=4, round_idx=1)  # this round: 5/6

    assert router.route("t", pool, ledger, before_round=1) == "V0"
    assert router.route("t", pool, ledger, before_round=2) == "V1"


def test_freeze_routing_returns_an_immutable_map(tmp_path: Path) -> None:
    """The round's routing is fixed for the whole round."""
    pool = _pool(tmp_path, n_variants=2)
    ledger = SuccessLedger()
    frozen = Router().freeze_routing(["t1", "t2"], pool, ledger, round_idx=0)

    assert dict(frozen) == {"t1": "V0", "t2": "V0"}
    with pytest.raises(TypeError):
        frozen["t1"] = "V1"  # type: ignore[index]
    with pytest.raises(AttributeError):
        frozen.clear()  # type: ignore[attr-defined]


def test_freeze_routing_rejects_a_future_round_too(tmp_path: Path) -> None:
    pool = _pool(tmp_path, n_variants=2)
    ledger = SuccessLedger()
    ledger.record("V0", "t", n_pass=1, n_att=2, round_idx=7)

    with pytest.raises(RoutingFreezeError):
        Router().freeze_routing(["t"], pool, ledger, round_idx=3)


def test_freeze_routing_accepts_a_ledger_that_stops_one_round_short(tmp_path: Path) -> None:
    """The normal case: newest cell is ``round_idx - 1``."""
    pool = _pool(tmp_path, n_variants=2)
    ledger = SuccessLedger()
    ledger.record("V1", "t", n_pass=2, n_att=2, round_idx=4)

    frozen = Router(task_to_cluster={"t": "c"}).freeze_routing(["t"], pool, ledger, round_idx=5)
    assert frozen["t"] == "V1"


def test_freeze_routing_covers_every_task(tmp_path: Path) -> None:
    """Tasks with evidence follow it; the rest take the cold-start rule."""
    pool = _pool(tmp_path, n_variants=2)
    ledger = SuccessLedger()
    ledger.record("V1", "t2", n_pass=2, n_att=2, round_idx=0)
    ledger.record("V0", "elsewhere", n_pass=2, n_att=2, round_idx=0)
    # equal rollups, so cold start falls to the lowest id
    assert ledger.variant_rollup("V0") == ledger.variant_rollup("V1") == 1.0

    frozen = _task_router().freeze_routing(["t1", "t2", "t3"], pool, ledger, round_idx=1)
    assert set(frozen) == {"t1", "t2", "t3"}
    assert frozen["t2"] == "V1"  # only t2 has task-level evidence
    assert frozen["t1"] == frozen["t3"] == "V0"  # cold start


def test_round_zero_freezes_everything_onto_v0(tmp_path: Path) -> None:
    pool = _pool(tmp_path, n_variants=1, tasks={"t1", "t2"})
    frozen = Router().freeze_routing(["t1", "t2"], pool, SuccessLedger(), round_idx=0)
    assert dict(frozen) == {"t1": "V0", "t2": "V0"}


# ===========================================================================
# W2 — argmax routing
# ===========================================================================


def test_route_picks_the_highest_estimate(tmp_path: Path) -> None:
    pool = _pool(tmp_path, n_variants=3)
    ledger = SuccessLedger()
    ledger.record("V0", "t", n_pass=0, n_att=2, round_idx=0)
    ledger.record("V1", "t", n_pass=2, n_att=2, round_idx=0)
    ledger.record("V2", "t", n_pass=1, n_att=2, round_idx=0)

    assert Router(task_to_cluster={"t": "c"}).route("t", pool, ledger, before_round=1) == "V1"


def test_route_requires_before_round(tmp_path: Path) -> None:
    """An unfrozen estimate is never the right thing to route on."""
    pool = _pool(tmp_path, n_variants=2)
    with pytest.raises(TypeError):
        Router().route("t", pool, SuccessLedger())  # type: ignore[call-arg]


def test_route_on_an_empty_pool_raises() -> None:
    pool = VariantPool()
    with pytest.raises(RuntimeError, match="empty pool"):
        Router().route("t", pool, SuccessLedger(), before_round=0)


def test_a_cell_estimating_exactly_at_the_prior_is_not_treated_as_absent(tmp_path: Path) -> None:
    """Cold start is decided on the cells, not by comparing scores to 0.5.

    V1's 1/2 record smooths to exactly the 0.5 prior; it must still count as
    evidence, so routing goes through the tie-break rather than cold start.
    """
    pool = _pool(tmp_path, n_variants=2)
    ledger = SuccessLedger()
    ledger.record("V1", "t", n_pass=1, n_att=2, round_idx=0)
    ledger.record("V1", "elsewhere", n_pass=2, n_att=2, round_idx=0)
    assert ledger.estimate("V1", "t", before_round=1) == 0.5

    # tie at 0.5 with untried V0 -> fewest attempts wins -> V0 (0 attempts)
    assert _task_router().route("t", pool, ledger, before_round=1) == "V0"
    # ... whereas cold start would have consulted the rollup and picked V1
    assert ledger.variant_rollup("V1") > ledger.variant_rollup("V0")
    assert Router().cold_start("t", pool, ledger) == "V1"


# ---------------------------------------------------------------------------
# Cold start (ours — report §5 gap 3)
# ---------------------------------------------------------------------------


def test_cold_start_with_one_variant_is_v0(tmp_path: Path) -> None:
    pool = _pool(tmp_path, n_variants=1)
    assert Router().cold_start("t", pool, SuccessLedger()) == "V0"


def test_cold_start_prefers_the_highest_rollup(tmp_path: Path) -> None:
    pool = _pool(tmp_path, n_variants=3)
    ledger = SuccessLedger()
    ledger.record("V0", "a", n_pass=0, n_att=2, round_idx=0)
    ledger.record("V1", "b", n_pass=1, n_att=2, round_idx=0)
    ledger.record("V2", "c", n_pass=2, n_att=2, round_idx=0)

    assert Router().cold_start("brand-new", pool, ledger) == "V2"


def test_cold_start_ties_go_to_the_lowest_id(tmp_path: Path) -> None:
    pool = _pool(tmp_path, n_variants=3)
    ledger = SuccessLedger()
    ledger.record("V1", "b", n_pass=2, n_att=2, round_idx=0)
    ledger.record("V2", "c", n_pass=2, n_att=2, round_idx=0)

    assert Router().cold_start("brand-new", pool, ledger) == "V1"


def test_cold_start_without_a_ledger_falls_back_to_v0(tmp_path: Path) -> None:
    """The bare SPEC signature, as used by ``VariantPool.reassign``."""
    pool = _pool(tmp_path, n_variants=3)
    assert Router().cold_start("t", pool) == "V0"


def test_route_cold_starts_a_task_nobody_has_evidence_for(tmp_path: Path) -> None:
    pool = _pool(tmp_path, n_variants=2)
    ledger = SuccessLedger()
    ledger.record("V1", "other", n_pass=2, n_att=2, round_idx=0)

    assert Router().route("unseen", pool, ledger, before_round=1) == "V1"


# ---------------------------------------------------------------------------
# Tie-break (ours — SPEC §6.6)
# ---------------------------------------------------------------------------


def test_default_tie_break_is_fewest_attempts(tmp_path: Path) -> None:
    """Explicit test of an *our-default* knob: ties are spent on exploration."""
    router = _task_router()
    assert router.tie_break == "fewest_attempts"

    pool = _pool(tmp_path, n_variants=2)
    ledger = SuccessLedger()
    # equal estimates (both 1/2 -> 0.5), unequal experience
    ledger.record("V0", "t", n_pass=1, n_att=2, round_idx=0)
    ledger.record("V0", "t", n_pass=1, n_att=2, round_idx=1)
    ledger.record("V1", "t", n_pass=1, n_att=2, round_idx=1)
    assert ledger.estimate("V0", "t", before_round=2) == ledger.estimate("V1", "t", before_round=2)
    assert ledger.attempts_on("V0", "t") == 4
    assert ledger.attempts_on("V1", "t") == 2

    assert router.route("t", pool, ledger, before_round=2) == "V1"


def test_smallest_id_tie_break(tmp_path: Path) -> None:
    pool = _pool(tmp_path, n_variants=2)
    ledger = SuccessLedger()
    ledger.record("V0", "t", n_pass=1, n_att=2, round_idx=0)
    ledger.record("V0", "t", n_pass=1, n_att=2, round_idx=1)
    ledger.record("V1", "t", n_pass=1, n_att=2, round_idx=1)

    assert _task_router(tie_break="smallest_id").route("t", pool, ledger, before_round=2) == "V0"


def test_random_tie_break_is_seeded_and_reproducible(tmp_path: Path) -> None:
    pool = _pool(tmp_path, n_variants=2)
    ledger = SuccessLedger()
    ledger.record("V0", "t", n_pass=1, n_att=2, round_idx=0)
    ledger.record("V1", "t", n_pass=1, n_att=2, round_idx=0)

    first = [_task_router(tie_break="random", seed=7).route("t", pool, ledger, before_round=1) for _ in range(5)]
    second = [_task_router(tie_break="random", seed=7).route("t", pool, ledger, before_round=1) for _ in range(5)]
    assert first == second
    assert set(first) <= {"V0", "V1"}


# ---------------------------------------------------------------------------
# Exploration (ours — SPEC §6.6, default off)
# ---------------------------------------------------------------------------


def test_exploration_is_off_by_default(tmp_path: Path) -> None:
    """Explicit test of an *our-default* knob: epsilon = 0, pure argmax."""
    router = _task_router()
    assert router.epsilon == 0.0
    assert router.explore(["V0", "V1"]) is None

    pool = _pool(tmp_path, n_variants=2)
    ledger = SuccessLedger()
    ledger.record("V1", "t", n_pass=2, n_att=2, round_idx=0)
    assert all(router.route("t", pool, ledger, before_round=1) == "V1" for _ in range(20))


def test_full_exploration_always_draws_a_variant() -> None:
    router = Router(epsilon=1.0, seed=3)
    draws = {router.explore(["V0", "V1", "V2"]) for _ in range(30)}
    assert None not in draws
    assert draws <= {"V0", "V1", "V2"}


def test_exploration_can_override_argmax(tmp_path: Path) -> None:
    pool = _pool(tmp_path, n_variants=2)
    ledger = SuccessLedger()
    ledger.record("V0", "t", n_pass=2, n_att=2, round_idx=0)
    ledger.record("V1", "t", n_pass=0, n_att=2, round_idx=0)

    router = _task_router(epsilon=1.0, seed=1)
    picks = {router.route("t", pool, ledger, before_round=1) for _ in range(30)}
    assert "V1" in picks  # the argmax loser is reachable


# ---------------------------------------------------------------------------
# W7 — cluster mode
# ---------------------------------------------------------------------------


def test_cluster_of_is_the_carrying_variant(tmp_path: Path) -> None:
    pool = _pool(tmp_path, n_variants=1, tasks={"t1", "t2"})
    child = pool.fork("V0", {"t1"}, at_round=2)

    router = Router()
    assert router.cluster_of("t1", pool) == child.variant_id
    assert router.cluster_of("t2", pool) == "V0"


def test_cluster_of_marks_an_uncarried_task_unknown(tmp_path: Path) -> None:
    pool = _pool(tmp_path, n_variants=2)
    assert Router().cluster_of("orphan", pool) is None


def test_default_routed_mode_aggregates_the_whole_carrier_cluster(tmp_path: Path) -> None:
    """The default is no longer a task tournament disguised as a cluster."""
    pool = _pool(tmp_path, n_variants=2, tasks={"a", "b"})
    ledger = SuccessLedger()
    ledger.record("V0", "a", 2, 2, 0)
    ledger.record("V0", "b", 0, 2, 0)
    ledger.record("V1", "a", 0, 2, 0)
    ledger.record("V1", "b", 1, 2, 0)

    # On b alone V1 wins (0.5 > 0.25), but over routed cluster {a,b}
    # V0 wins (3/6 > 2/6). Both tasks therefore receive the cluster winner.
    assert _task_router().route("b", pool, ledger, before_round=1) == "V1"
    router = Router()
    assert router.route("a", pool, ledger, before_round=1) == "V0"
    assert router.route("b", pool, ledger, before_round=1) == "V0"


def test_injected_mapping_drives_variant_by_cluster_routing(tmp_path: Path) -> None:
    pool = _pool(tmp_path, n_variants=2)
    mapping = {"a": "reasoning", "b": "reasoning"}
    ledger = SuccessLedger()
    ledger.record("V0", "a", 2, 2, 0)
    ledger.record("V0", "b", 0, 2, 0)
    ledger.record("V1", "a", 0, 2, 0)
    ledger.record("V1", "b", 1, 2, 0)

    router = Router(task_to_cluster=mapping)
    assert router.cluster_of("b", pool) == "reasoning"
    assert router.tasks_in_cluster("reasoning", pool) == frozenset({"a", "b"})
    assert router.route("b", pool, ledger, before_round=1) == "V0"


def test_cluster_tie_uses_the_aggregate_attempt_denominator(tmp_path: Path) -> None:
    pool = _pool(tmp_path, n_variants=2)
    ledger = SuccessLedger()
    ledger.record("V0", "a", 1, 2, 0)  # aggregate estimate 2/4, 2 attempts
    ledger.record("V1", "a", 1, 2, 0)
    ledger.record("V1", "b", 1, 2, 0)  # aggregate estimate 3/6, 4 attempts

    router = Router(task_to_cluster={"a": "c", "b": "c"})
    assert ledger.estimate_cluster("V0", {"a", "b"}, before_round=1) == 0.5
    assert ledger.estimate_cluster("V1", {"a", "b"}, before_round=1) == 0.5
    assert router.route("b", pool, ledger, before_round=1) == "V0"


def test_cluster_before_round_hides_every_same_round_member(tmp_path: Path) -> None:
    pool = _pool(tmp_path, n_variants=2)
    ledger = SuccessLedger()
    ledger.record("V0", "a", 2, 2, 0)
    ledger.record("V1", "a", 2, 2, 1)
    ledger.record("V1", "b", 2, 2, 1)
    router = Router(task_to_cluster={"a": "c", "b": "c"})

    assert router.route("b", pool, ledger, before_round=1) == "V0"
    assert router.route("b", pool, ledger, before_round=2) == "V1"


def test_cold_start_rollup_cannot_leak_the_frozen_round(tmp_path: Path) -> None:
    pool = _pool(tmp_path, n_variants=2)
    ledger = SuccessLedger()
    ledger.record("V1", "current", 2, 2, 1)
    router = Router(task_to_cluster={})

    # Unknown cluster -> cold start. The round-1 result is invisible at the
    # round-1 cut, then becomes eligible in round 2.
    assert router.route("unknown", pool, ledger, before_round=1) == "V0"
    assert router.route("unknown", pool, ledger, before_round=2) == "V1"


def test_cluster_window_is_forwarded_to_the_aggregate_estimator(tmp_path: Path) -> None:
    pool = _pool(tmp_path, n_variants=2)
    ledger = SuccessLedger()
    ledger.record("V0", "a", 4, 4, 1)
    ledger.record("V1", "b", 2, 2, 8)
    mapping = {"a": "c", "b": "c"}

    assert Router(task_to_cluster=mapping).route("b", pool, ledger, before_round=10) == "V0"
    assert Router(task_to_cluster=mapping, window=5).route("b", pool, ledger, before_round=10) == "V1"


def test_unknown_in_an_injected_map_takes_deterministic_cold_start(tmp_path: Path) -> None:
    pool = _pool(tmp_path, n_variants=2)
    ledger = SuccessLedger()
    ledger.record("V0", "target", 2, 2, 0)
    ledger.record("V0", "hard", 0, 2, 0)
    ledger.record("V1", "other", 2, 2, 0)
    router = Router(task_to_cluster={"other": "known"})

    # Target's task-level evidence is deliberately ignored: its cluster is
    # unknown, so the macro cold-start score deterministically selects V1.
    assert {router.route("target", pool, ledger, before_round=1) for _ in range(10)} == {"V1"}
    assert _task_router().route("target", pool, ledger, before_round=1) == "V0"


@pytest.mark.parametrize("mode", ["failure", "level"])
def test_non_routed_cluster_modes_require_assignments(mode: str, tmp_path: Path) -> None:
    pool = _pool(tmp_path, n_variants=1, tasks={"t1"})
    router = Router(cluster_mode=mode)
    assert router.cluster_mode in CLUSTER_MODES
    with pytest.raises(NotImplementedError, match="requires an injected"):
        router.cluster_of("t1", pool)


@pytest.mark.parametrize("mode", ["failure", "level"])
def test_non_routed_cluster_modes_work_with_injected_assignments(mode: str, tmp_path: Path) -> None:
    pool = _pool(tmp_path, n_variants=1, tasks={"t1"})
    router = Router(cluster_mode=mode, task_to_cluster={"t1": "c1"})
    assert router.cluster_of("t1", pool) == "c1"
    assert router.route("t1", pool, SuccessLedger(), before_round=0) == "V0"


def test_task_tournament_is_an_explicit_compatibility_mode() -> None:
    assert set(ROUTING_MODES) == {"cluster", "task_tournament"}
    assert Router().routing_mode == "cluster"
    assert _task_router().routing_mode == "task_tournament"


# ---------------------------------------------------------------------------
# Constructor validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "kwargs",
    [
        {"cluster_mode": "semantic"},
        {"routing_mode": "pretend_cluster"},
        {"tie_break": "coin-flip"},
        {"epsilon": -0.1},
        {"epsilon": 1.5},
        {"window": 0},
        {"task_to_cluster": {"": "cluster"}},
        {"task_to_cluster": {"task": ""}},
    ],
)
def test_router_rejects_unknown_settings(kwargs: dict) -> None:
    with pytest.raises(ValueError):
        Router(**kwargs)
