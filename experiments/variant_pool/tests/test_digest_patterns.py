# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""Offline tests for batch-2a Item 3 — ``--digest-patterns {fail_only, all}``.

Prompt-assembly level (no LLM, no network). ``fail_only`` (default) keeps today's
byte-identical per-task prompt and digests only failures. ``all`` selects the
pattern-conditional templates ported from
``upstream/feat/aegis:harnessx/aegis/templates/digester_*.md`` and embeds the
Layer-B 9-class taxonomy in the failure-bearing templates.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from variant_pool.evidence import TaskDigest

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from recipe.gaia_evolver import run_variant_pool as rvp  # noqa: E402

_ONE_TAXONOMY_CLASS = "tool_effect_missing"


def _digest(task_id: str, outcome: tuple[int, int]) -> TaskDigest:
    return TaskDigest(task_id=task_id, round_idx=1, variant_id="V0", outcome=outcome)


def _digester(**overrides) -> rvp._LLMDigester:
    kwargs = dict(
        evidence=None,
        pool=None,
        provider=None,
        tasks_by_id={},
        run_dir=Path("."),
        fallback=None,
    )
    kwargs.update(overrides)
    return rvp._LLMDigester(**kwargs)


# ---------------------------------------------------------------------------
# template files: taxonomy present, provenance stripped
# ---------------------------------------------------------------------------


def test_all_templates_load_and_strip_provenance() -> None:
    for pattern in ("ALL_FAIL", "ALL_PASS", "PARTIAL_PASS"):
        body = rvp._load_digester_template(pattern)
        assert not body.lstrip().startswith("<!--")  # provenance header stripped
        assert "PROVENANCE" not in body
        assert "Digester" in body  # real body present


def test_failure_templates_embed_the_9_class_taxonomy() -> None:
    for pattern in ("ALL_FAIL", "PARTIAL_PASS"):
        body = rvp._load_digester_template(pattern)
        for cls in rvp._LAYER_B_CLASSES:
            assert cls in body, (pattern, cls)
    assert len(rvp._LAYER_B_CLASSES) == 9


def test_all_pass_template_carries_the_fragility_marker() -> None:
    body = rvp._load_digester_template("ALL_PASS")
    assert rvp._FRAGILITY_CATEGORY in body  # latent_fragility
    assert "none" in body
    assert "strategy" in body.lower() and "fragility" in body.lower()


def test_partial_template_demands_both_rollouts() -> None:
    body = rvp._load_digester_template("PARTIAL_PASS").lower()
    assert "both" in body and "diverg" in body


# ---------------------------------------------------------------------------
# leading-instruction selection
# ---------------------------------------------------------------------------


def test_fail_only_leading_instruction_is_the_legacy_constant_byte_for_byte() -> None:
    dig = _digester(digest_patterns="fail_only")
    # every pattern resolves to today's constant under fail_only (byte-identical).
    for pattern in ("ALL_FAIL", "ALL_PASS", "PARTIAL_PASS"):
        assert dig._leading_instruction(pattern) == rvp._LLM_DIGESTER_TASK_PROMPT


def test_all_mode_leading_instruction_selects_the_right_template() -> None:
    dig = _digester(digest_patterns="all")
    assert dig._leading_instruction("ALL_FAIL") == rvp._load_digester_template("ALL_FAIL")
    assert dig._leading_instruction("ALL_PASS") == rvp._load_digester_template("ALL_PASS")
    assert dig._leading_instruction("PARTIAL_PASS") == rvp._load_digester_template("PARTIAL_PASS")
    # and they are genuinely distinct texts
    assert dig._leading_instruction("ALL_FAIL") != dig._leading_instruction("ALL_PASS")


# ---------------------------------------------------------------------------
# full prompt assembly — fail_only byte-identical, all-mode taxonomy present
# ---------------------------------------------------------------------------


def _build(dig: rvp._LLMDigester, digest: TaskDigest, pattern: str) -> str:
    return dig._build_task_prompt(
        digest=digest,
        question="Q1",
        frontmatter="FM",
        head="HEAD",
        tail="TAIL",
        retry_error=None,
        pattern=pattern,
    )


def test_fail_only_failed_prompt_is_byte_identical_today() -> None:
    dig = _digester(digest_patterns="fail_only")
    prompt = _build(dig, _digest("t1", (0, 2)), "ALL_FAIL")
    # leading instruction is exactly today's constant (no taxonomy leaked in).
    assert prompt.startswith(rvp._LLM_DIGESTER_TASK_PROMPT)
    assert _ONE_TAXONOMY_CLASS not in prompt
    # the legacy FAILED outcome label, verbatim.
    assert (
        "TASK OUTCOME (harness ground truth, do not re-derive): FAILED "
        "(n_pass=0 of n_att=2)"
    ) in prompt
    assert "TASK ID: t1" in prompt


def test_all_mode_all_fail_prompt_uses_template_and_taxonomy() -> None:
    dig = _digester(digest_patterns="all")
    prompt = _build(dig, _digest("t1", (0, 2)), "ALL_FAIL")
    assert prompt.startswith(rvp._load_digester_template("ALL_FAIL"))
    assert _ONE_TAXONOMY_CLASS in prompt  # taxonomy text present
    assert "FAILED (n_pass=0 of n_att=2)" in prompt


def test_all_mode_all_pass_prompt_uses_pass_label_and_template() -> None:
    dig = _digester(digest_patterns="all")
    prompt = _build(dig, _digest("t1", (2, 2)), "ALL_PASS")
    assert prompt.startswith(rvp._load_digester_template("ALL_PASS"))
    assert "ALL_PASS (n_pass=2 of n_att=2)" in prompt
    assert rvp._FRAGILITY_CATEGORY in prompt


def test_all_mode_partial_prompt_uses_partial_label() -> None:
    dig = _digester(digest_patterns="all")
    prompt = _build(dig, _digest("t1", (1, 2)), "PARTIAL_PASS")
    assert prompt.startswith(rvp._load_digester_template("PARTIAL_PASS"))
    assert "PARTIAL_PASS (n_pass=1 of n_att=2)" in prompt


# ---------------------------------------------------------------------------
# flag / provenance
# ---------------------------------------------------------------------------


def test_digest_patterns_flag_choice_and_default() -> None:
    parser = rvp.build_arg_parser()
    assert parser.parse_args([]).digest_patterns == "fail_only"
    assert parser.parse_args(["--digest-patterns", "all"]).digest_patterns == "all"
    with pytest.raises(SystemExit):
        parser.parse_args(["--digest-patterns", "some"])


def test_digest_patterns_provenance_none_at_default() -> None:
    assert rvp._digest_patterns_provenance("fail_only") is None
    warn = rvp._digest_patterns_provenance("all")
    assert warn is not None
    assert "digest_patterns=all" in warn
    assert "byte-for-byte" in warn


def test_compose_rationale_fail_only_bookkeeping_is_byte_identical() -> None:
    # the legacy string, character for character (default digest_patterns).
    legacy = (
        "core [llm_digester bookkeeping: passed=3 kept deterministic (no LLM), "
        "failed=2, llm_interpreted=2, per_task_fallbacks=0]"
    )
    got = rvp._LLMDigester._compose_rationale(
        core="core", n_passed=3, n_failed=2, interpreted=2, fallback_notes=[]
    )
    assert got == legacy


def test_compose_rationale_all_mode_reports_passes_as_llm_digested() -> None:
    got = rvp._LLMDigester._compose_rationale(
        core="core",
        n_passed=3,
        n_failed=2,
        interpreted=5,
        fallback_notes=[],
        digest_patterns="all",
    )
    assert "passed=3 llm-digested" in got
    assert "kept deterministic (no LLM)" not in got
