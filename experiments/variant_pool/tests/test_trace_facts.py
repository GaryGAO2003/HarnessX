# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""batch-4a Item 1 -- Layer-A mechanical trace-fact extraction + injection."""

from __future__ import annotations

import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from recipe.gaia_evolver import run_variant_pool as rvp  # noqa: E402
from experiments.variant_pool.evidence import EvidenceStore, TaskDigest  # noqa: E402
from experiments.variant_pool.trace_facts import extract_trace_facts  # noqa: E402

_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "real_session_R9_V0_0383a3ee.jsonl"


# ---------------------------------------------------------------------------
# (1) extraction on the REAL landed session fixture
# ---------------------------------------------------------------------------


def test_extract_nonzero_facts_on_real_fixture():
    facts = extract_trace_facts("t_bbc", [_FIXTURE])
    # nonzero facts: the fixture has 8 tool calls (steps 0-7) and one exit.
    assert len(facts.tool_calls) == 8
    assert len(facts.exits) == 1
    assert facts.rollouts == ["real_session_R9_V0_0383a3ee"]


def test_next_uses_result_has_positive_and_negative_cases():
    facts = extract_trace_facts("t_bbc", [_FIXTURE])
    positives = [c for c in facts.tool_calls if c.next_uses_result is True]
    negatives = [c for c in facts.tool_calls if c.next_uses_result is False]
    nones = [c for c in facts.tool_calls if c.next_uses_result is None]
    # The final WebSearch (step 7) output is referenced by the final answer;
    # several mid-run tool outputs are not; empty results are undecidable (None).
    assert positives, "expected at least one next_uses_result=True"
    assert negatives, "expected at least one next_uses_result=False"
    assert nones, "empty tool results should be undecidable (None)"
    # empty returns are classified 'empty' and never counted as used.
    assert all(c.next_uses_result is None for c in facts.tool_calls if c.return_type == "empty")


def test_exit_fact_snippet_and_fields():
    facts = extract_trace_facts("t_bbc", [_FIXTURE])
    exit_fact = facts.exits[0]
    assert exit_fact.exit_reason == "done"
    assert exit_fact.total_steps == 9
    # terminal_snippet is the last <=200 chars of the last assistant content.
    assert 0 < len(exit_fact.terminal_snippet) <= 200
    recs = [json.loads(l) for l in _FIXTURE.read_text(encoding="utf-8").splitlines() if l.strip()]
    last_asst = [
        r for r in recs
        if r.get("type") == "raw_assistant" and (r.get("message") or {}).get("content")
    ][-1]
    assert exit_fact.terminal_snippet == str(last_asst["message"]["content"])[-200:]


# ---------------------------------------------------------------------------
# (2) rendering -- official section titles
# ---------------------------------------------------------------------------


def test_render_has_official_section_titles():
    md = extract_trace_facts("t_bbc", [_FIXTURE]).to_markdown()
    for title in (
        "## Trace Facts (Layer A — mechanical; do not rewrite)",
        "### Exits",
        "### Tool calls",
        "### Repeated tool calls (same args, no new tool between)",
        "### Tool burst (suspected loop trap — same tool hammered)",
        "### Tool calls whose output the next step did NOT reference",
    ):
        assert title in md, f"missing section: {title!r}"
    # the tool-effect shortlist surfaces the fixture's un-referenced outputs.
    assert "next step did NOT reference" in md


# ---------------------------------------------------------------------------
# (3) burst threshold boundaries (synthetic sessions)
# ---------------------------------------------------------------------------


def _write_session(path: Path, calls: list[tuple[int, list[str]]]) -> None:
    """Write a synthetic session jsonl. ``calls`` = [(step, [tool_names]), ...];
    each assistant event carries those tool_calls at ``step`` with a matching
    raw_tool result per call."""
    rows: list[dict] = [{"type": "session_start", "step": 0, "message": None}]
    tid = 0
    for step, tools in calls:
        tcs = []
        results = []
        for name in tools:
            cid = f"c{tid}"
            tid += 1
            tcs.append({"id": cid, "name": name, "input": {"i": cid}})
            results.append({
                "type": "raw_tool", "step": step,
                "message": {"role": "tool", "content": f"ok {cid}", "tool_call_id": cid, "name": name},
            })
        rows.append({
            "type": "raw_assistant", "step": step,
            "message": {"role": "assistant", "content": f"a{step}", "tool_calls": tcs},
        })
        rows.extend(results)
    rows.append({
        "type": "episode_end", "step": 999, "exit_reason": "done",
        "total_steps": len(calls), "passed": True, "message": None,
    })
    path.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")


def test_burst_total_boundary_fires_at_20(tmp_path):
    # 20 distinct steps, one 'T' call each: total==20 (>= _BURST_TOTAL_MIN) fires.
    p = tmp_path / "s_r0.jsonl"
    _write_session(p, [(i, ["T"]) for i in range(20)])
    bursts = extract_trace_facts("t", [p]).bursts
    assert len(bursts) == 1
    assert bursts[0].total_calls == 20
    assert bursts[0].max_calls_in_one_step == 1
    assert bursts[0].severity == "medium"


def test_burst_total_boundary_silent_at_19(tmp_path):
    p = tmp_path / "s_r0.jsonl"
    _write_session(p, [(i, ["T"]) for i in range(19)])
    assert extract_trace_facts("t", [p]).bursts == []


def test_burst_peak_boundary_fires_at_10_in_one_step(tmp_path):
    # 10 'T' calls in a single step: peak==10 (>= _BURST_PEAK_MIN) fires even
    # though total (10) is under the total threshold.
    p = tmp_path / "s_r0.jsonl"
    _write_session(p, [(0, ["T"] * 10)])
    bursts = extract_trace_facts("t", [p]).bursts
    assert len(bursts) == 1
    assert bursts[0].max_calls_in_one_step == 10
    assert bursts[0].peak_step == 0
    assert bursts[0].severity == "medium"


def test_burst_high_severity_at_peak_15(tmp_path):
    p = tmp_path / "s_r0.jsonl"
    _write_session(p, [(0, ["T"] * 15)])
    bursts = extract_trace_facts("t", [p]).bursts
    assert len(bursts) == 1
    assert bursts[0].severity == "high"  # peak >= 15


def test_repeat_run_detects_consecutive_identical_args(tmp_path):
    # Two consecutive identical (tool,args) calls with nothing between -> RepeatRun.
    p = tmp_path / "s_r0.jsonl"
    rows: list[dict] = [{"type": "session_start", "step": 0, "message": None}]
    for step in (0, 1):
        cid = f"c{step}"
        rows.append({
            "type": "raw_assistant", "step": step,
            "message": {"role": "assistant", "content": f"a{step}",
                        "tool_calls": [{"id": cid, "name": "Fetch", "input": {"url": "u"}}]},
        })
        rows.append({
            "type": "raw_tool", "step": step,
            "message": {"role": "tool", "content": "same", "tool_call_id": cid, "name": "Fetch"},
        })
    rows.append({"type": "episode_end", "exit_reason": "done", "total_steps": 2, "passed": False, "message": None})
    p.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    facts = extract_trace_facts("t", [p])
    assert len(facts.repeats) == 1
    assert facts.repeats[0].tool == "Fetch"
    assert facts.repeats[0].steps == [0, 1]


# ---------------------------------------------------------------------------
# (4) flag-off => digester per-task prompt byte-identical
# ---------------------------------------------------------------------------


class _FakeVariant:
    def __init__(self, routed):
        self.routed_tasks = set(routed)


class _FakePool:
    def __init__(self, variants):
        self.variants = dict(variants)


def _digester(run_dir: Path, **kw) -> "rvp._LLMDigester":
    evidence = EvidenceStore(run_dir)
    pool = _FakePool({"V0": _FakeVariant(())})
    return rvp._LLMDigester(
        evidence=evidence,
        pool=pool,
        provider=None,
        tasks_by_id={},
        run_dir=run_dir,
        fallback=rvp._EvidenceDigester(evidence, pool),
        **kw,
    )


def _digest() -> TaskDigest:
    return TaskDigest(
        task_id="t",
        round_idx=1,
        variant_id="V0",
        outcome=(0, 2),
        failure_category="x",
        evidence_anchors=["trajectories/t.md"],
    )


def test_trace_facts_flag_defaults_off():
    assert _digester(Path(".")).trace_facts is False


def test_prompt_byte_identical_when_trace_facts_absent(tmp_path):
    digester = _digester(tmp_path)
    digest = _digest()
    kw = dict(
        digest=digest, question="Q", frontmatter="FM", head="HEAD", tail="TAIL",
        retry_error=None,
    )
    base = digester._build_task_prompt(**kw)
    explicit_none = digester._build_task_prompt(**kw, trace_facts_md=None)
    assert base == explicit_none
    assert "Trace Facts" not in base


def test_prompt_injects_block_verbatim_when_present(tmp_path):
    digester = _digester(tmp_path)
    digest = _digest()
    kw = dict(
        digest=digest, question="Q", frontmatter="FM", head="HEAD", tail="TAIL",
        retry_error=None,
    )
    block = "## Trace Facts (Layer A — mechanical; do not rewrite)\n\nBODY-XYZ"
    prompt = digester._build_task_prompt(**kw, trace_facts_md=block)
    assert prompt != digester._build_task_prompt(**kw)
    assert block in prompt  # verbatim
    assert rvp._TRACE_FACTS_LEAD in prompt
