# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""Tests for v2 trajectory frontmatter in recipe/gaia_evolver/run.py."""

from __future__ import annotations

from recipe.gaia_evolver.run import (  # noqa: E402
    _count_trajectory_failure_signals,
    _render_trajectory_frontmatter,
    _write_task_trajectory,
)


def _record(**overrides) -> dict:
    base = {
        "task_id": "gaia-001",
        "exit_reason": "done",
        "steps": 12,
        "cost_usd": 0.456,
        "total_tokens": 18432,
        "output": "Paris is the capital.",
        "tool_call_counts": {"web_search": 5, "read_file": 3},
        "tool_error_counts": {"web_search": 2},
        "extracted_answer": "Paris",
        "llm_judge_verdict": {
            "verdict": "plausible",
            "confidence": 0.75,
            "cause": "triangulated 3 sources",
            "missing": "",
            "lesson": "triangulation resolves ambiguous names",
        },
    }
    base.update(overrides)
    return base


def test_v2_frontmatter_contains_required_measurements():
    fm = _render_trajectory_frontmatter(_record())
    assert 'task_id: "gaia-001"' in fm or "task_id: gaia-001" in fm
    assert "exit_reason:" in fm
    assert "steps: 12" in fm
    assert "cost_usd: 0.456" in fm
    assert "tool_call_counts:" in fm
    assert "tool_error_counts:" in fm
    assert "final_output_length:" in fm


def test_v2_frontmatter_contains_judge_fields_when_present():
    fm = _render_trajectory_frontmatter(_record())
    assert "extracted_answer:" in fm
    assert "Paris" in fm
    assert "judge_verdict:" in fm
    assert "plausible" in fm
    assert "judge_confidence: 0.75" in fm
    assert "judge_cause:" in fm
    assert "triangulated 3 sources" in fm
    assert "judge_missing:" in fm
    assert "judge_lesson:" in fm


def test_v2_frontmatter_omits_all_judge_fields_when_disabled():
    # When record has no llm_judge_verdict (e.g. --no-judge path),
    # judge_* and extracted_answer are omitted entirely.
    r = _record(llm_judge_verdict={}, extracted_answer="")
    r.pop("extracted_answer")
    r["llm_judge_verdict"] = {}
    fm = _render_trajectory_frontmatter(r)
    for forbidden in [
        "extracted_answer:",
        "judge_verdict:",
        "judge_confidence:",
        "judge_cause:",
        "judge_missing:",
        "judge_lesson:",
    ]:
        assert forbidden not in fm, f"expected {forbidden!r} to be absent"


def test_v2_frontmatter_has_no_legacy_fields():
    fm = _render_trajectory_frontmatter(_record())
    for legacy in ["output_band:", "output_flags:", "suspicious_tool_outputs:"]:
        assert legacy not in fm, f"legacy field {legacy!r} must not appear"


def test_final_output_length_is_stripped_char_count():
    r = _record(output="   padded spaces  \n")
    fm = _render_trajectory_frontmatter(r)
    # "padded spaces" stripped = 13 chars
    assert "final_output_length: 13" in fm


def test_final_output_length_prefers_output_key_over_legacy_final_output():
    r = _record(output="   canonical output  ", final_output="")
    fm = _render_trajectory_frontmatter(r)
    assert "final_output_length: 16" in fm


def test_frontmatter_emits_empty_end_turn_diagnostics():
    r = _record(model_empty_end_turn=True, empty_end_turn_recovered=False)
    fm = _render_trajectory_frontmatter(r)
    assert "model_empty_end_turn: true" in fm
    assert "empty_end_turn_recovered: false" in fm


def test_v2_frontmatter_emits_total_tokens_when_present():
    r = _record()
    assert r.get("total_tokens") == 18432
    fm = _render_trajectory_frontmatter(r)
    assert "total_tokens: 18432" in fm


def test_v2_frontmatter_omits_total_tokens_when_absent():
    r = _record()
    r.pop("total_tokens", None)
    fm = _render_trajectory_frontmatter(r)
    assert "total_tokens:" not in fm


def test_record_key_uses_total_tokens_not_tokens():
    """Regression guard: _run_task must store 'total_tokens', not 'tokens'.

    Asserts no bare ``"tokens": ...`` assignment (old key) remains in run.py.
    This is a static-analysis guard — if the rename is reverted, this test
    catches it without needing a live task run.
    """
    import pathlib

    run_py = pathlib.Path(__file__).parent.parent.parent / "recipe" / "gaia_evolver" / "run.py"
    source = run_py.read_text(encoding="utf-8")
    # Match dict-literal assignment ``"tokens": <value>`` that is NOT inside
    # a totals-aggregation dict (i.e., not the local `totals` dict builder).
    # The simplest guard: assert the old per-task record key assignment is gone.
    # We look for the pattern used in _run_task: ``"tokens": result.``
    assert '"tokens": result.' not in source, "Found old record key 'tokens' in _run_task — must be 'total_tokens'"


def test_fields_appear_in_stable_order_for_limit_30_reads():
    fm = _render_trajectory_frontmatter(_record())
    lines = fm.splitlines()

    # find line indices for stability
    def idx(key: str) -> int:
        for i, ln in enumerate(lines):
            if ln.startswith(key + ":"):
                return i
        return -1

    order = [
        "task_id",
        "exit_reason",
        "steps",
        "cost_usd",
        "final_output_length",
        "model_empty_end_turn",
        "empty_end_turn_recovered",
        "tools_used",
        "tool_call_counts",
        "tool_error_counts",
        "extracted_answer",
        "judge_verdict",
        "judge_confidence",
        "judge_cause",
        "judge_missing",
        "judge_lesson",
    ]
    indices = [idx(k) for k in order if idx(k) >= 0]
    assert indices == sorted(indices), f"frontmatter order unstable: {indices}"


# ---------------------------------------------------------------------------
# --traj-failure-signals: body-only failure strings lifted into frontmatter
# ---------------------------------------------------------------------------

# A rendered body carrying 2x [SEARCH UNAVAILABLE], 1x "Fetch error for ",
# 0x "No content retrieved from", and 1x [LoopDetection] (the last appended onto
# a tool result exactly as LoopDetectionProcessor writes it).
_FAILURE_BODY = (
    "## Execution Steps\n"
    "  -> web_search: [SEARCH UNAVAILABLE] All search providers failed for query: x\n"
    "  -> web_search: [SEARCH UNAVAILABLE] All search providers failed for query: y\n"
    "  -> web_fetch: Fetch error for https://example.com: read timed out\n"
    "  -> web_fetch: some real content here\n\n[LoopDetection] the same tool call was issued\n"
)

_EXPECTED_COUNTS = {
    "search_unavailable_count": 2,
    "fetch_error_count": 1,
    "fetch_empty_count": 0,
    "loop_warning_count": 1,
}


class _StubTask:
    task_id = "gaia-fs"


def test_count_trajectory_failure_signals_counts_each_marker():
    assert _count_trajectory_failure_signals(_FAILURE_BODY) == _EXPECTED_COUNTS


def test_count_trajectory_failure_signals_covers_alternate_fetch_spellings():
    # [fetch failed rolls into fetch_error_count; No content retrieved from is
    # the empty-result marker (fetch_empty_count).
    body = "[fetch failed: 503] and No content retrieved from https://x and [fetch failed after retries]"
    counts = _count_trajectory_failure_signals(body)
    assert counts["fetch_error_count"] == 2
    assert counts["fetch_empty_count"] == 1


def test_failure_signals_emitted_into_frontmatter_when_enabled():
    counts = _count_trajectory_failure_signals(_FAILURE_BODY)
    fm = _render_trajectory_frontmatter(_record(), failure_signals=counts)
    assert "search_unavailable_count: 2" in fm
    assert "fetch_error_count: 1" in fm
    assert "fetch_empty_count: 0" in fm
    assert "loop_warning_count: 1" in fm


def test_failure_signals_absent_and_byte_identical_by_default():
    # Default call must not emit any of the four keys, and must be byte-identical
    # to passing failure_signals=None explicitly.
    fm = _render_trajectory_frontmatter(_record())
    for key in _EXPECTED_COUNTS:
        assert key not in fm
    assert fm == _render_trajectory_frontmatter(_record(), failure_signals=None)


def test_write_task_trajectory_flag_off_is_byte_identical(tmp_path):
    _write_task_trajectory(tmp_path, _StubTask(), _FAILURE_BODY, record=_record(), failure_signals=False)
    off = (tmp_path / "gaia-fs.md").read_text(encoding="utf-8")
    for key in _EXPECTED_COUNTS:
        assert key not in off


def test_write_task_trajectory_flag_on_emits_counts(tmp_path):
    _write_task_trajectory(tmp_path, _StubTask(), _FAILURE_BODY, record=_record(), failure_signals=True)
    on = (tmp_path / "gaia-fs.md").read_text(encoding="utf-8")
    assert "search_unavailable_count: 2" in on
    assert "fetch_error_count: 1" in on
    assert "fetch_empty_count: 0" in on
    assert "loop_warning_count: 1" in on
