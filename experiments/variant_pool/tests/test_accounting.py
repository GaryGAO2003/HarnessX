# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""Offline unit tests for ``variant_pool.accounting`` (W30).

Every dollar figure below is hand-computed from the published DeepSeek V4
prices, because the point of the module is that the repo's own estimate is
wrong for this provider (``runloop.py:947-949`` hard-codes Claude Sonnet's
$3/$15) and nothing here may depend on it.

The two arithmetic traps under test:

* ``input_tokens`` counts cache **misses**. Providers report the opposite
  (``prompt_tokens`` includes the cached prefix), so :meth:`AttemptCost.from_usage`
  must subtract, or the prefix gets billed twice at 50x the right rate;
* the task agent and the meta agent scale differently — attempts vs rounds —
  so :meth:`CostLedger.project` extrapolates them on different units.
"""

from __future__ import annotations

import pytest

from variant_pool.accounting import (
    DEFAULT_PRICING,
    MTOK,
    ROLES,
    AttemptCost,
    CostLedger,
    Pricing,
)

FLASH = Pricing.deepseek_v4_flash()
PRO = Pricing.deepseek_v4_pro()

# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _cost(role: str = "task_agent", *, task_id: str = "t", round_idx: int = 0, **kwargs) -> AttemptCost:
    return AttemptCost(task_id=task_id, round_idx=round_idx, variant_id=None, role=role, **kwargs)


class _Usage:
    """A repo ``Usage``-shaped stub: ``input_tokens`` is the full prompt."""

    def __init__(self, input_tokens: int, output_tokens: int, cache_read_tokens: int = 0, cache_write_tokens: int = 0):
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.cache_read_tokens = cache_read_tokens
        self.cache_write_tokens = cache_write_tokens


# ===========================================================================
# Pricing
# ===========================================================================


def test_the_published_deepseek_v4_prices() -> None:
    assert (FLASH.input_per_mtok, FLASH.cached_input_per_mtok, FLASH.output_per_mtok) == (0.14, 0.0028, 0.28)
    assert (PRO.input_per_mtok, PRO.cached_input_per_mtok, PRO.output_per_mtok) == (0.435, 0.003625, 0.87)
    assert FLASH.cache_write_per_mtok == PRO.cache_write_per_mtok == 0.0  # writes are free


def test_the_cache_discount_is_the_whole_budget_lever() -> None:
    assert FLASH.cache_discount == pytest.approx(50.0)
    assert PRO.cache_discount == pytest.approx(120.0)


def test_cost_applies_the_cache_discount() -> None:
    """100k misses + 900k hits + 50k out, at flash prices."""
    billed = FLASH.cost(input_tokens=100_000, cached_input_tokens=900_000, output_tokens=50_000)
    assert billed == pytest.approx(0.014 + 0.00252 + 0.014)

    cold = FLASH.cost(input_tokens=1_000_000, output_tokens=50_000)
    assert cold == pytest.approx(0.14 + 0.014)
    assert cold / billed == pytest.approx(5.046, abs=0.001)


def test_a_million_tokens_costs_exactly_the_quoted_price() -> None:
    assert FLASH.cost(input_tokens=MTOK) == pytest.approx(0.14)
    assert FLASH.cost(cached_input_tokens=MTOK) == pytest.approx(0.0028)
    assert FLASH.cost(output_tokens=MTOK) == pytest.approx(0.28)
    assert FLASH.cost(cache_write_tokens=MTOK) == 0.0


def test_the_repo_estimate_would_be_off_by_an_order_of_magnitude() -> None:
    """``runloop.py:947-949`` prices everything at Claude Sonnet's $3/$15."""
    repo_style = (900_000 * 3.0 + 100_000 * 15.0) / MTOK
    real = FLASH.cost(input_tokens=900_000, output_tokens=100_000)
    assert repo_style == pytest.approx(4.2)
    assert real == pytest.approx(0.154)
    assert repo_style / real == pytest.approx(27.3, abs=0.1)  # checklist §5: "about 27x"


def test_negative_prices_are_rejected() -> None:
    with pytest.raises(ValueError):
        Pricing(model="x", input_per_mtok=-1, cached_input_per_mtok=0, output_per_mtok=0)


def test_pricing_from_litellm_matches_the_hand_written_table() -> None:
    """litellm is where batch B will read prices from; the two must agree."""
    pytest.importorskip("litellm")
    from_map = Pricing.from_litellm("deepseek/deepseek-v4-flash")
    assert from_map.input_per_mtok == pytest.approx(FLASH.input_per_mtok)
    assert from_map.cached_input_per_mtok == pytest.approx(FLASH.cached_input_per_mtok)
    assert from_map.output_per_mtok == pytest.approx(FLASH.output_per_mtok)


def test_default_pricing_splits_flash_and_pro() -> None:
    assert DEFAULT_PRICING["task_agent"].model.endswith("flash")
    assert DEFAULT_PRICING["meta_agent"].model.endswith("pro")


# ===========================================================================
# AttemptCost
# ===========================================================================


def test_input_tokens_are_misses_and_prompt_is_their_sum() -> None:
    cost = _cost(input_tokens=200, cached_input_tokens=800)
    assert cost.prompt_tokens == 1000


def test_from_usage_subtracts_the_cached_prefix() -> None:
    """The provider's ``prompt_tokens`` includes the cache hit; ours must not."""
    cost = AttemptCost.from_usage(
        _Usage(input_tokens=1410, output_tokens=200, cache_read_tokens=1408),
        task_id="db4fd70a",
        round_idx=3,
        role="task_agent",
        variant_id="V1",
    )
    assert (cost.input_tokens, cost.cached_input_tokens) == (2, 1408)
    assert cost.prompt_tokens == 1410
    assert cost.variant_id == "V1"

    # billing the raw prompt as a miss would cost ~40x more
    naive = FLASH.cost(input_tokens=1410, cached_input_tokens=1408, output_tokens=200)
    assert naive > cost.billed(FLASH) * 3


def test_from_usage_rejects_more_cache_than_prompt() -> None:
    with pytest.raises(ValueError, match="exceeds prompt tokens"):
        AttemptCost.from_usage(
            _Usage(input_tokens=100, output_tokens=0, cache_read_tokens=200),
            task_id="t",
            round_idx=0,
            role="task_agent",
        )


def test_an_unknown_role_is_rejected() -> None:
    with pytest.raises(ValueError, match="role must be one of"):
        _cost(role="critic")


def test_negative_token_counts_are_rejected() -> None:
    with pytest.raises(ValueError):
        _cost(input_tokens=-1)


# ===========================================================================
# CostLedger — the role split (SPEC §6.8)
# ===========================================================================


def _mixed_ledger() -> CostLedger:
    ledger = CostLedger(pricing=DEFAULT_PRICING)
    ledger.record(_cost("task_agent", input_tokens=1000, output_tokens=1000, tool_calls=3))
    ledger.record(_cost("task_agent", input_tokens=1000, output_tokens=1000, retries=1, infra_failure=True))
    ledger.record(_cost("meta_agent", input_tokens=10_000, output_tokens=2000, round_idx=0))
    return ledger


def test_the_two_loops_are_never_merged() -> None:
    by_role = _mixed_ledger().by_role()
    assert set(by_role) == set(ROLES)
    assert by_role["task_agent"]["attempts"] == 2
    assert by_role["task_agent"]["tool_calls"] == 3
    assert by_role["task_agent"]["retries"] == 1
    assert by_role["task_agent"]["infra_failures"] == 1
    assert by_role["meta_agent"]["attempts"] == 1
    assert by_role["meta_agent"]["input_tokens"] == 10_000


def test_a_silent_role_is_present_and_empty_not_missing() -> None:
    ledger = CostLedger()
    ledger.record(_cost("task_agent", input_tokens=10))
    assert ledger.by_role()["meta_agent"]["attempts"] == 0
    assert ledger.by_role()["meta_agent"]["cache_hit_rate"] is None


def test_billing_uses_each_roles_own_price_list() -> None:
    billed = _mixed_ledger().total_billed()
    task = 2 * (1000 * 0.14 + 1000 * 0.28) / MTOK
    meta = (10_000 * 0.435 + 2000 * 0.87) / MTOK
    assert billed["task_agent"] == pytest.approx(task)
    assert billed["meta_agent"] == pytest.approx(meta)
    assert billed["total"] == pytest.approx(task + meta)


def test_one_pricing_object_covers_every_role() -> None:
    ledger = _mixed_ledger()
    flat = ledger.total_billed(FLASH)
    assert flat["meta_agent"] == pytest.approx((10_000 * 0.14 + 2000 * 0.28) / MTOK)


def test_incomplete_pricing_map_is_rejected() -> None:
    with pytest.raises(ValueError, match="missing role"):
        _mixed_ledger().total_billed({"task_agent": FLASH})


def test_per_attempt_billed_defaults_to_the_task_agent() -> None:
    ledger = _mixed_ledger()
    assert ledger.per_attempt_billed() == pytest.approx((1000 * 0.14 + 1000 * 0.28) / MTOK)
    assert ledger.per_attempt_billed(role="meta_agent") == pytest.approx((10_000 * 0.435 + 2000 * 0.87) / MTOK)


def test_per_attempt_billed_is_none_without_records() -> None:
    assert CostLedger().per_attempt_billed() is None


# ===========================================================================
# Cache hit rate — the dominant budget term
# ===========================================================================


def test_cache_hit_rate_is_cached_over_prompt() -> None:
    ledger = CostLedger()
    ledger.record(_cost(input_tokens=200, cached_input_tokens=800))
    ledger.record(_cost(input_tokens=800, cached_input_tokens=200))
    assert ledger.cache_hit_rate() == pytest.approx(0.5)


def test_cache_hit_rate_is_per_role() -> None:
    ledger = CostLedger()
    ledger.record(_cost("task_agent", input_tokens=100, cached_input_tokens=900))
    ledger.record(_cost("meta_agent", input_tokens=1000))
    assert ledger.cache_hit_rate("task_agent") == pytest.approx(0.9)
    assert ledger.cache_hit_rate("meta_agent") == 0.0
    assert ledger.cache_hit_rate() == pytest.approx(0.45)


def test_no_prompt_tokens_leaves_the_hit_rate_undefined() -> None:
    """``None``, not 0.0 — nothing was measured, so nothing missed the cache."""
    assert CostLedger().cache_hit_rate() is None
    ledger = CostLedger()
    ledger.record(_cost(output_tokens=50))
    assert ledger.cache_hit_rate() is None


# ===========================================================================
# Projection to full M0 scale
# ===========================================================================


def test_projection_scales_the_two_loops_on_different_units() -> None:
    """Task agent: lineages x tasks x rounds x k. Meta agent: lineages x rounds."""
    projection = _mixed_ledger().project(lineages=3, tasks=103, rounds=15, k=2)

    per_attempt = (1000 * 0.14 + 1000 * 0.28) / MTOK
    per_cycle = (10_000 * 0.435 + 2000 * 0.87) / MTOK

    assert projection["attempts"] == 3 * 103 * 15 * 2 == 9270
    assert projection["meta_cycles"] == 45
    assert projection["per_attempt_usd"] == pytest.approx(per_attempt)
    assert projection["task_agent_usd"] == pytest.approx(per_attempt * 9270)
    assert projection["meta_agent_usd"] == pytest.approx(per_cycle * 45)
    assert projection["total_usd"] == pytest.approx(per_attempt * 9270 + per_cycle * 45)
    assert projection["basis"]["meta_agent_measured"] is True
    assert projection["basis"]["sample_attempts"] == 2


def test_projection_reports_the_no_cache_upper_bound() -> None:
    """Repricing every hit as a miss is what the bill becomes if the prefix stops sticking."""
    ledger = CostLedger(pricing=DEFAULT_PRICING)
    ledger.record(_cost("task_agent", input_tokens=100, cached_input_tokens=900, output_tokens=100))

    projection = ledger.project(lineages=1, tasks=10, rounds=1, k=2)
    point = (100 * 0.14 + 900 * 0.0028 + 100 * 0.28) / MTOK
    cold = (1000 * 0.14 + 100 * 0.28) / MTOK

    assert projection["per_attempt_usd"] == pytest.approx(point)
    assert projection["total_usd"] == pytest.approx(point * 20)
    assert projection["total_usd_no_cache"] == pytest.approx(cold * 20)
    assert projection["total_usd_no_cache"] > projection["total_usd"]
    assert projection["cache_hit_rate"] == pytest.approx(0.9)


def test_an_unmeasured_meta_agent_projects_to_zero_and_says_so() -> None:
    ledger = CostLedger(pricing=DEFAULT_PRICING)
    ledger.record(_cost("task_agent", input_tokens=1000, output_tokens=1000))

    projection = ledger.project(lineages=3, tasks=103, rounds=15, k=2)
    assert projection["meta_agent_usd"] == 0.0
    assert projection["basis"]["meta_agent_measured"] is False
    assert projection["basis"]["sample_meta_rounds"] == 0


def test_meta_agent_unit_is_the_round_not_the_call() -> None:
    """Two meta-agent calls in one round are one cycle, not two."""
    ledger = CostLedger(pricing=DEFAULT_PRICING)
    ledger.record(_cost("meta_agent", input_tokens=10_000, round_idx=0))
    ledger.record(_cost("meta_agent", input_tokens=10_000, round_idx=0))

    projection = ledger.project(lineages=1, tasks=1, rounds=1, k=1)
    assert projection["basis"]["sample_meta_rounds"] == 1
    assert projection["per_meta_cycle_usd"] == pytest.approx(2 * 10_000 * 0.435 / MTOK)


def test_projection_records_which_price_list_it_used() -> None:
    projection = _mixed_ledger().project(lineages=1, tasks=1, rounds=1, k=2)
    assert projection["basis"]["pricing"]["task_agent"].endswith("flash")
    assert projection["basis"]["pricing"]["meta_agent"].endswith("pro")


@pytest.mark.parametrize(
    "kwargs",
    [
        {"lineages": 0, "tasks": 1, "rounds": 1, "k": 1},
        {"lineages": 1, "tasks": 0, "rounds": 1, "k": 1},
        {"lineages": 1, "tasks": 1, "rounds": 0, "k": 1},
        {"lineages": 1, "tasks": 1, "rounds": 1, "k": 0},
    ],
)
def test_projection_arguments_are_validated(kwargs) -> None:
    with pytest.raises(ValueError):
        _mixed_ledger().project(**kwargs)
