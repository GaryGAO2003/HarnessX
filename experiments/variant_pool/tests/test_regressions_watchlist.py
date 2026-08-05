# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""batch-4a Item 3 -- per-round regressions watchlist + prompt/brief injection."""

from __future__ import annotations

import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from recipe.gaia_evolver import run_variant_pool as rvp  # noqa: E402
from recipe.gaia_evolver.variant_pool_meta_agent import VariantPoolMetaAgent  # noqa: E402
from experiments.variant_pool import regressions as R  # noqa: E402
from experiments.variant_pool.ledger import SuccessLedger  # noqa: E402


def _two_round_ledger() -> SuccessLedger:
    ledger = SuccessLedger()
    # luck task: solved R0 (ALL_PASS) then failing R1 (ALL_FAIL) -> regressed_hard
    ledger.record("V0", "t_luck", 2, 2, 0)
    ledger.record("V0", "t_luck", 0, 2, 1)
    # ALL_PASS -> PARTIAL -> regressed_soft
    ledger.record("V0", "t_soft", 2, 2, 0)
    ledger.record("V0", "t_soft", 1, 2, 1)
    # PARTIAL(0.5) -> ALL_FAIL -> regressed_partial
    ledger.record("V0", "t_part", 1, 2, 0)
    ledger.record("V0", "t_part", 0, 2, 1)
    # stable pass: no regression
    ledger.record("V0", "t_ok", 2, 2, 0)
    ledger.record("V0", "t_ok", 2, 2, 1)
    return ledger


# ---------------------------------------------------------------------------
# grades on synthetic two-round ledgers
# ---------------------------------------------------------------------------


def test_grades_including_luck_task_hard():
    regs = {r["task_id"]: r["grade"] for r in R.detect_regressions(_two_round_ledger(), 1)}
    assert regs["t_luck"] == "regressed_hard"  # solved-once-then-failing flags hard
    assert regs["t_soft"] == "regressed_soft"
    assert regs["t_part"] == "regressed_partial"
    assert "t_ok" not in regs  # stable task is not a regression


def test_no_regressions_at_round_zero():
    assert R.detect_regressions(_two_round_ledger(), 0) == []


def test_grade_helper_matches_official_thresholds():
    assert R._grade([True, True], [False, False]) == "regressed_hard"
    assert R._grade([True, True], [True, False]) == "regressed_soft"
    assert R._grade([True, False], [False, False]) == "regressed_partial"
    assert R._grade([True, False], [True, False]) is None  # unchanged partial
    assert R._grade(None, [False, False]) is None  # no prior data
    assert R._grade([False, False], [False, False]) is None  # never solved


def test_variant_agnostic_prior_round_solve_counts():
    # t solved by V1 in R0, failing under V0 in R1 -> still a hard regression
    # because the windowed comparison is variant-agnostic.
    ledger = SuccessLedger()
    ledger.record("V1", "t", 2, 2, 0)
    ledger.record("V0", "t", 0, 2, 1)
    regs = R.detect_regressions(ledger, 1)
    assert len(regs) == 1 and regs[0]["grade"] == "regressed_hard"


# ---------------------------------------------------------------------------
# file rendering
# ---------------------------------------------------------------------------


def test_write_regressions_md_file_and_content(tmp_path):
    ledger = _two_round_ledger()
    path, rendered = R.write_regressions_md(
        tmp_path, ledger, compare_round=1, for_evolve_round_n=2,
        joint_suspect_ships=[{"ship_id": "V0", "bucket": "processor"}],
    )
    assert path == tmp_path / "R2" / "regressions.md"
    assert path.is_file()
    assert path.read_text(encoding="utf-8") == rendered
    assert "regressed_hard" in rendered
    assert "## Per-task detail" in rendered
    assert "Joint-suspect ships" in rendered


def test_render_empty_when_no_regressions():
    md = R.render_regressions_md(1, [], for_evolve_round_n=2)
    assert "_No regressions detected this round._" in md


# ---------------------------------------------------------------------------
# injection present/absent by flag -- Planner + Critic prompts
# ---------------------------------------------------------------------------


def test_planner_prompt_injects_watchlist_only_when_set():
    off = rvp._LLMPlanner(provider=None, k_t=1, fallback=None)
    assert off.regressions_md == ""  # default off
    base = off._build_prompt("SUMMARY", truncation=(), retry_error=None)
    assert "Regressions watchlist" not in base

    on = rvp._LLMPlanner(provider=None, k_t=1, fallback=None, regressions_md="WL-BODY-123")
    injected = on._build_prompt("SUMMARY", truncation=(), retry_error=None)
    assert "## Regressions watchlist (read before planning)" in injected
    assert "WL-BODY-123" in injected


def test_critic_prompt_injects_watchlist_only_when_set():
    off = rvp._LLMCritic(provider=None, fallback=None)
    assert off.regressions_md == ""
    base = off._build_prompt("SUMMARY", truncation=(), retry_error=None)
    assert "Regressions watchlist" not in base

    on = rvp._LLMCritic(provider=None, fallback=None, regressions_md="WL-BODY-456")
    injected = on._build_prompt("SUMMARY", truncation=(), retry_error=None)
    assert "## Regressions watchlist (read before reviewing)" in injected
    assert "WL-BODY-456" in injected


# ---------------------------------------------------------------------------
# injection present/absent by flag -- Evolver TASK.md brief render
# ---------------------------------------------------------------------------


def _contract(with_watchlist: bool) -> dict:
    brief = {"manifest_mode": "repo", "buckets": ["prompt"]}
    if with_watchlist:
        brief["regressions_watchlist"] = "WATCHLIST-EVOLVER-789"
    return {"suggested_candidate_id": "C-1", "target_variant": "V0", "planner_brief": brief}


def test_evolver_brief_renders_watchlist_section_when_present():
    section = VariantPoolMetaAgent._render_candidate_contract(_contract(True))
    assert "### Regressions watchlist (read before proposing)" in section
    assert "WATCHLIST-EVOLVER-789" in section
    # lifted out of the JSON contract (not duplicated inside planner_contract_json).
    json_line = [ln for ln in section.splitlines() if "planner_contract_json" in ln][0]
    assert "regressions_watchlist" not in json_line


def test_evolver_brief_byte_identical_when_absent():
    with_key = VariantPoolMetaAgent._render_candidate_contract(_contract(True))
    without = VariantPoolMetaAgent._render_candidate_contract(_contract(False))
    assert "Regressions watchlist" not in without
    assert without != with_key
    # and the no-watchlist brief matches a brief that never carried the key.
    plain = {"suggested_candidate_id": "C-1", "target_variant": "V0",
             "planner_brief": {"manifest_mode": "repo", "buckets": ["prompt"]}}
    assert without == VariantPoolMetaAgent._render_candidate_contract(plain)
