# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""Offline tests for batch-2a Item 2 — ``--actionability {llm, mechanical}``.

Fully offline (no rollouts, no LLM, no network). ``mechanical`` ports the official
pattern scorer from ``upstream/feat/aegis:harnessx/aegis/stages/preprocess.py``
``_compute_actionability`` (L110-129), adapted to our pass@2 ``TaskDigest`` outcome.
``llm`` (default) stays byte-identical: a_t still comes from the round-level LLM call.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from variant_pool.evidence import TaskDigest

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from recipe.gaia_evolver import run_variant_pool as rvp  # noqa: E402


def _digest(task_id: str, outcome: tuple[int, int], *, category: str | None = None) -> TaskDigest:
    return TaskDigest(
        task_id=task_id,
        round_idx=1,
        variant_id="V0",
        outcome=outcome,
        failure_category=category,
    )


# ---------------------------------------------------------------------------
# pattern classifier + fragility marker
# ---------------------------------------------------------------------------


def test_digest_pattern_of_three_way() -> None:
    assert rvp._digest_pattern_of(_digest("a", (0, 2))) == "ALL_FAIL"
    assert rvp._digest_pattern_of(_digest("b", (1, 2))) == "PARTIAL_PASS"
    assert rvp._digest_pattern_of(_digest("c", (2, 2))) == "ALL_PASS"


def test_reports_fragility_marker() -> None:
    assert rvp._reports_fragility(_digest("a", (2, 2), category="latent_fragility"))
    assert not rvp._reports_fragility(_digest("b", (2, 2), category="none"))
    assert not rvp._reports_fragility(_digest("c", (2, 2), category=None))


# ---------------------------------------------------------------------------
# the tiers (official _compute_actionability, adapted)
# ---------------------------------------------------------------------------


def test_any_all_fail_scores_one_point_zero() -> None:
    # e_pervar3-style: 3 ALL_FAIL among 39 -> 1.0 (proceeds), where the recorded
    # llm run scored 0.2 (drought).
    digests = [_digest(f"f{i}", (0, 2)) for i in range(3)]
    digests += [_digest(f"p{i}", (2, 2)) for i in range(36)]
    score, reason = rvp._mechanical_actionability(digests, digest_patterns="fail_only")
    assert score == 1.0
    assert "ALL_FAIL" in reason


def test_partial_only_scores_zero_point_eight() -> None:
    digests = [_digest("a", (1, 2)), _digest("b", (2, 2))]  # partial + pass, no all-fail
    score, _reason = rvp._mechanical_actionability(digests, digest_patterns="fail_only")
    assert score == 0.8


def test_pure_all_pass_scores_zero_and_fragility_tier_is_inert_in_fail_only() -> None:
    # Pure all-pass -> 0.0. A latent-fragility marker is INERT under fail_only,
    # because fail_only never produces an ALL_PASS digest (documented).
    digests = [_digest(f"p{i}", (2, 2)) for i in range(3)]
    assert rvp._mechanical_actionability(digests, digest_patterns="fail_only") == (
        0.0,
        "mechanical a_t: ALL_PASS only, no fragility surfaced -> no-op",
    )
    fragile = digests + [_digest("frag", (2, 2), category="latent_fragility")]
    score_fail_only, _ = rvp._mechanical_actionability(fragile, digest_patterns="fail_only")
    assert score_fail_only == 0.0  # inert: the marker is ignored under fail_only


def test_fragility_tier_reachable_only_under_all_mode() -> None:
    digests = [
        _digest("clean", (2, 2), category="none"),
        _digest("frag", (2, 2), category="latent_fragility"),
    ]
    score, reason = rvp._mechanical_actionability(digests, digest_patterns="all")
    assert score == 0.3
    assert "fragility" in reason


def test_all_fail_beats_fragility_and_partial_beats_fragility() -> None:
    # priority order: ALL_FAIL (1.0) > PARTIAL (0.8) > fragility (0.3) > no-op (0.0)
    with_fail = [
        _digest("f", (0, 2)),
        _digest("frag", (2, 2), category="latent_fragility"),
    ]
    assert rvp._mechanical_actionability(with_fail, digest_patterns="all")[0] == 1.0
    with_partial = [
        _digest("p", (1, 2)),
        _digest("frag", (2, 2), category="latent_fragility"),
    ]
    assert rvp._mechanical_actionability(with_partial, digest_patterns="all")[0] == 0.8


def test_empty_scores_zero() -> None:
    assert rvp._mechanical_actionability([], digest_patterns="all")[0] == 0.0


# ---------------------------------------------------------------------------
# mechanical mode makes NO round-level LLM call
# ---------------------------------------------------------------------------


class _RaisingProvider:
    async def complete(self, messages, tools):  # noqa: ANN001, ARG002
        raise AssertionError("mechanical a_t must not make a round-level LLM call")


def _bare_digester(tmp_path: Path, **overrides) -> rvp._LLMDigester:
    kwargs = dict(
        evidence=None,
        pool=None,
        provider=_RaisingProvider(),
        tasks_by_id={},
        run_dir=tmp_path,
        fallback=None,
    )
    kwargs.update(overrides)
    return rvp._LLMDigester(**kwargs)


def test_mechanical_resolve_round_actionability_bypasses_llm(tmp_path) -> None:
    dig = _bare_digester(tmp_path, actionability_mode="mechanical")
    failed = [_digest("f", (0, 2))]
    score, core = asyncio.run(dig._resolve_round_actionability(None, failed, failed))
    assert score == 1.0
    assert core.startswith("llm_digester: mechanical a_t")


def test_llm_default_no_failure_shortcut_is_unchanged(tmp_path) -> None:
    # llm mode + zero failures -> the byte-identical a_t=0.0 shortcut, no LLM call.
    dig = _bare_digester(tmp_path, actionability_mode="llm")
    passes = [_digest("p", (2, 2))]
    score, core = asyncio.run(dig._resolve_round_actionability(None, passes, []))
    assert score == 0.0
    assert "no addressable" in core and "no LLM call" in core


# ---------------------------------------------------------------------------
# flag / provenance
# ---------------------------------------------------------------------------


def test_actionability_flag_choice_and_default() -> None:
    parser = rvp.build_arg_parser()
    assert parser.parse_args([]).actionability == "llm"
    assert parser.parse_args(["--actionability", "mechanical"]).actionability == "mechanical"
    with pytest.raises(SystemExit):
        parser.parse_args(["--actionability", "bogus"])


def test_actionability_provenance_none_at_default() -> None:
    assert rvp._actionability_mode_provenance("llm") is None
    warn = rvp._actionability_mode_provenance("mechanical")
    assert warn is not None
    assert "actionability=mechanical" in warn
    assert "byte-for-byte" in warn
