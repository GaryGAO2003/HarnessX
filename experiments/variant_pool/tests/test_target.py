# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""Offline unit tests for ``variant_pool.target`` (W14)."""

from __future__ import annotations

from pathlib import Path

import pytest

from variant_pool.ledger import SuccessLedger
from variant_pool.pool import VariantPool
from variant_pool.target import STRATEGIES, select_target_variant


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _pool(tmp_path: Path, n_variants: int = 3) -> VariantPool:
    pool = VariantPool()
    root = pool.add_root(tmp_path / "V0.yaml", tmp_path / "learnings_V0.md")
    for _ in range(n_variants - 1):
        pool.fork(root.variant_id, set(), at_round=1)
    return pool


# ---------------------------------------------------------------------------
# worst_first — our default (SPEC §6.6)
# ---------------------------------------------------------------------------


def test_default_strategy_targets_the_weakest_variant(tmp_path: Path) -> None:
    """Explicit test of an *our-default* knob: worst_first."""
    pool = _pool(tmp_path, n_variants=3)
    ledger = SuccessLedger()
    ledger.record("V0", "a", n_pass=2, n_att=2, round_idx=0)  # rollup 1.0
    ledger.record("V1", "b", n_pass=1, n_att=2, round_idx=0)  # rollup 0.5
    ledger.record("V2", "c", n_pass=0, n_att=2, round_idx=0)  # rollup 0.0

    assert select_target_variant(pool, ledger) == "V2"
    assert select_target_variant(pool, ledger, strategy="worst_first") == "V2"


def test_worst_first_breaks_ties_on_the_lowest_id(tmp_path: Path) -> None:
    pool = _pool(tmp_path, n_variants=3)
    ledger = SuccessLedger()
    ledger.record("V1", "b", n_pass=0, n_att=2, round_idx=0)
    ledger.record("V2", "c", n_pass=0, n_att=2, round_idx=0)

    assert select_target_variant(pool, ledger) == "V1"


def test_a_fresh_variant_scores_the_stale_prior(tmp_path: Path) -> None:
    """No history is neither the worst nor the best — it sits at the prior."""
    pool = _pool(tmp_path, n_variants=2)
    ledger = SuccessLedger()

    # V0 clearly worse than an untried V1
    ledger.record("V0", "a", n_pass=0, n_att=2, round_idx=0)
    assert select_target_variant(pool, ledger) == "V0"

    # V0 clearly better than an untried V1
    fresh = SuccessLedger()
    fresh.record("V0", "a", n_pass=2, n_att=2, round_idx=0)
    assert select_target_variant(pool, fresh) == "V1"


def test_single_variant_pool_targets_v0(tmp_path: Path) -> None:
    pool = _pool(tmp_path, n_variants=1)
    assert select_target_variant(pool, SuccessLedger()) == "V0"


# ---------------------------------------------------------------------------
# Ablation arms
# ---------------------------------------------------------------------------


def test_round_robin_cycles_by_id(tmp_path: Path) -> None:
    pool = _pool(tmp_path, n_variants=3)
    ledger = SuccessLedger()
    picks = [select_target_variant(pool, ledger, strategy="round_robin", round_idx=r) for r in range(7)]
    assert picks == ["V0", "V1", "V2", "V0", "V1", "V2", "V0"]


def test_round_robin_refuses_to_guess_the_round(tmp_path: Path) -> None:
    pool = _pool(tmp_path, n_variants=2)
    with pytest.raises(ValueError, match="round_robin requires round_idx"):
        select_target_variant(pool, SuccessLedger(), strategy="round_robin")


def test_failure_density_targets_the_largest_unsolved_mass(tmp_path: Path) -> None:
    pool = _pool(tmp_path, n_variants=2)
    ledger = SuccessLedger()

    # V0 carries 3 tasks, 2 of them unsolved; V1 carries 1 task, all unsolved.
    # A per-task *rate* would pick V1 (1.0 > 0.67); failure mass picks V0.
    pool.variants["V0"].routed_tasks = {"a", "b", "c"}
    pool.variants["V1"].routed_tasks = {"z"}
    ledger.record("V0", "a", n_pass=2, n_att=2, round_idx=0)
    ledger.record("V0", "b", n_pass=0, n_att=2, round_idx=0)
    ledger.record("V1", "z", n_pass=0, n_att=2, round_idx=0)

    assert select_target_variant(pool, ledger, strategy="failure_density") == "V0"


def test_failure_density_counts_never_attempted_tasks_as_unsolved(tmp_path: Path) -> None:
    pool = _pool(tmp_path, n_variants=2)
    ledger = SuccessLedger()
    pool.variants["V0"].routed_tasks = {"a", "b"}  # never attempted at all
    pool.variants["V1"].routed_tasks = {"z"}
    ledger.record("V1", "z", n_pass=0, n_att=2, round_idx=0)

    assert select_target_variant(pool, ledger, strategy="failure_density") == "V0"


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def test_unknown_strategy_is_rejected(tmp_path: Path) -> None:
    pool = _pool(tmp_path, n_variants=2)
    assert set(STRATEGIES) == {"worst_first", "round_robin", "failure_density"}
    with pytest.raises(ValueError):
        select_target_variant(pool, SuccessLedger(), strategy="best_first")


def test_empty_pool_is_rejected() -> None:
    with pytest.raises(RuntimeError, match="empty pool"):
        select_target_variant(VariantPool(), SuccessLedger())
