"""GHX-AEGIS Block A — graph-native Digester.

Pure-function tests for :mod:`experiments.variant_pool.aegis_digester`.  The
polarity under test: extraction is *filter-and-keep* — a structurally usable
reply is always kept, and only its unusable *claims* (hallucinated node ids,
out-of-vocabulary aspects, overlong evidence/notes) are sanitized; genuinely
unusable replies (parse failure, non-object, missing key, task_id mismatch)
drop to an error.  Coverage spans the on-graph ``implicated_nodes`` column, the
off-graph ``implicated_aspects`` vocabulary (with slash-form normalization and
``processor/<name>`` → node routing), ``notes`` truncation, the round-level
actionability role, and the anti-contamination guarantee (no gold answer in the
prompt).
"""

import json
import re

import pytest

from experiments.variant_pool.aegis_digester import (
    TRAJ_FRONTMATTER_CAP,
    TRAJ_HEAD_CAP,
    TRAJ_TAIL_CAP,
    DigesterResult,
    RoundActionability,
    TaskDigest,
    _trajectory_window,
    build_digester_prompt,
    build_round_actionability_prompt,
    extract_digest,
    extract_round_actionability,
    run_digester,
    run_round_actionability,
    write_digests_jsonl,
)
from harnessx.core.harness import HarnessConfig
from harnessx.graph.snapshot import to_graph

COST_GUARD = "harnessx.processors.control.cost_guard.CostGuardProcessor"
LOOP_DET = "harnessx.processors.control.loop_detection.LoopDetectionProcessor"

COST_NODE = "proc:cost_guard_processor"
LOOP_NODE = "proc:loop_detection_processor"


def _snapshot():
    return to_graph(HarnessConfig(processors=[
        {"_target_": LOOP_DET, "_hook_": "step_end", "threshold": 25},
        {"_target_": COST_GUARD, "_hook_": "before_model", "max_usd": 40.0},
    ]))


def _record(task_id="t1", passed=False, **overrides):
    rec = {
        "task_id": task_id,
        "passed": passed,
        "level": 1,
        "exit_reason": "budget_exceeded",
        "steps": 20,
        "cost_usd": 0.55,
        "total_tokens": 166907,
        "n_pass": 0 if not passed else 1,
        "n_att": 1,
        "question": "What species of bird is featured in the video?",
        "trajectory_file": f"trajectories/{task_id}.md",
    }
    rec.update(overrides)
    return rec


def _digest_json(task_id="t1", category="reasoning_error", nodes=None,
                 aspects=None, evidence=None, notes=""):
    """A valid six-key per-task digest reply (no per-task actionability)."""
    return json.dumps({
        "task_id": task_id,
        "failure_category": category,
        "implicated_nodes": nodes if nodes is not None else [],
        "implicated_aspects": aspects if aspects is not None else [],
        "evidence": evidence if evidence is not None else ["step 1: search"],
        "notes": notes,
    })


def _digest(task_id="t1", category="reasoning_error", nodes=(), aspects=(),
            evidence=("step 1: search",), notes="", warnings=()):
    """Construct a :class:`TaskDigest` directly (for round / writer tests)."""
    return TaskDigest(
        task_id=task_id,
        failure_category=category,
        implicated_nodes=tuple(nodes),
        implicated_aspects=tuple(aspects),
        evidence=tuple(evidence),
        notes=notes,
        warnings=tuple(warnings),
    )


# ── setup sanity: the snapshot really carries the referenced nodes ───────────


def test_snapshot_carries_referenced_nodes():
    snap = _snapshot()
    assert COST_NODE in snap.nodes
    assert LOOP_NODE in snap.nodes


# ── _trajectory_window ───────────────────────────────────────────────────────


def test_window_extracts_frontmatter_and_body():
    text = '---\ntask_id: "x"\nsteps: 5\n---\n\n# Trajectory\nbody here'
    fm, head, tail = _trajectory_window(text)
    assert fm.startswith("---") and fm.rstrip().endswith("---")
    assert "task_id" in fm and "steps: 5" in fm
    assert "body here" in head
    assert "body here" not in fm      # frontmatter must not spill into the body
    assert tail == ""


def test_window_no_frontmatter_is_all_body():
    fm, head, tail = _trajectory_window("plain body, no yaml block")
    assert fm == ""
    assert head == "plain body, no yaml block"
    assert tail == ""


def test_window_short_body_no_duplication():
    body = "SHORT BODY CONTENT"
    fm, head, tail = _trajectory_window(f"---\na: 1\n---\n\n{body}")
    assert head == body        # whole body rides in head …
    assert tail == ""          # … and tail is empty (no overlap, no repeat)


def test_window_long_body_splits_head_tail():
    body = "H" * 13_000 + "M" * 30_000 + "T" * 25_000
    fm, head, tail = _trajectory_window(f"---\na: 1\n---\n\n{body}")
    assert len(head) == TRAJ_HEAD_CAP
    assert len(tail) == TRAJ_TAIL_CAP
    assert head == body[:TRAJ_HEAD_CAP]
    assert tail == body[-TRAJ_TAIL_CAP:]


def test_window_frontmatter_truncated_to_cap():
    text = f"---\n{'x' * 5_000}\n---\n\nbody"
    fm, _head, _tail = _trajectory_window(text)
    assert len(fm) == TRAJ_FRONTMATTER_CAP


def test_window_non_string_is_safe():
    assert _trajectory_window(None) == ("", "", "")


# ── build_digester_prompt ────────────────────────────────────────────────────


def test_prompt_has_bom_sections_and_six_key_contract():
    snap = _snapshot()
    prompt = build_digester_prompt(
        _record(), "---\nk: v\n---\n\ntrajectory body", snap)
    for header in (
        "## Parent graph (bill of materials)",
        "## Task outcome",
        "## Task question",
        "## Trajectory frontmatter",
        "## Trajectory head",
        "## Trajectory tail",
        "## Output contract (hard requirements)",
    ):
        assert header in prompt, header
    # the BOM section really carries the parent nodes
    assert COST_NODE in prompt and LOOP_NODE in prompt
    # the contract names all six keys — and NOT the retired per-task actionability
    for key in ("task_id", "failure_category", "implicated_nodes",
                "implicated_aspects", "evidence", "notes"):
        assert key in prompt
    # the aspect vocabulary is spelled out for the model
    assert "model_capability" in prompt and "environment" in prompt
    # no correction section unless asked
    assert "## Correction" not in prompt


def test_prompt_never_leaks_expected_or_reason():
    # The record carries the gold answer in expected/reason; the prompt must
    # surface NEITHER (both feed the meta-loop → contamination).
    snap = _snapshot()
    secret = "ZQ_GOLD_ANSWER_9137"
    rec = _record(
        task_id="t-leak",
        question="What is the capital of France?",   # no secret in the question
        expected=secret,
        reason=f"no_match: expected='{secret}'",
    )
    prompt = build_digester_prompt(rec, "clean trajectory, no gold answer", snap)
    assert secret not in prompt
    # but the neutral outcome fields DO appear
    assert "t-leak" in prompt
    assert "budget_exceeded" in prompt


def test_prompt_includes_retry_correction_section():
    snap = _snapshot()
    prompt = build_digester_prompt(
        _record(), "body", snap, retry_error="json.loads failed: boom")
    assert "## Correction" in prompt
    assert "json.loads failed: boom" in prompt


# ── extract_digest: happy paths ──────────────────────────────────────────────


def test_extract_valid_object():
    snap = _snapshot()
    raw = _digest_json(
        task_id="t1", category="blocked_source", nodes=[COST_NODE],
        aspects=["tool:WebSearch", "environment"],
        evidence=["step 5: WebSearch empty", "exit_reason budget_exceeded"],
        notes="agent gave up early")
    digest, err = extract_digest(raw, expected_task_id="t1", snapshot=snap)
    assert err == ""
    assert isinstance(digest, TaskDigest)
    assert digest.task_id == "t1"
    assert digest.failure_category == "blocked_source"
    assert digest.implicated_nodes == (COST_NODE,)
    assert digest.implicated_aspects == ("tool:WebSearch", "environment")
    assert digest.evidence == ("step 5: WebSearch empty", "exit_reason budget_exceeded")
    assert digest.notes == "agent gave up early"
    assert digest.warnings == ()


def test_extract_valid_object_has_no_actionability_attr():
    # Per-task actionability was retired to the round level.
    snap = _snapshot()
    digest, err = extract_digest(_digest_json(), expected_task_id="t1", snapshot=snap)
    assert err == "" and digest is not None
    assert not hasattr(digest, "actionability")


def test_extract_fenced_object():
    snap = _snapshot()
    raw = "Here is my analysis:\n```json\n" + _digest_json() + "\n```\nDone."
    digest, err = extract_digest(raw, expected_task_id="t1", snapshot=snap)
    assert err == ""
    assert digest is not None and digest.task_id == "t1"


def test_extract_object_embedded_in_prose():
    snap = _snapshot()
    raw = "I think the failure is: " + _digest_json() + " -- that's my read."
    digest, err = extract_digest(raw, expected_task_id="t1", snapshot=snap)
    assert err == "" and digest is not None


# ── extract_digest: reject paths ─────────────────────────────────────────────


def test_extract_garbage_text_errors():
    snap = _snapshot()
    digest, err = extract_digest(
        "Sorry, I could not analyze this one.", expected_task_id="t1", snapshot=snap)
    assert digest is None
    assert err


def test_extract_empty_errors():
    snap = _snapshot()
    digest, err = extract_digest("   ", expected_task_id="t1", snapshot=snap)
    assert digest is None and err


def test_extract_non_dict_top_level_errors():
    snap = _snapshot()
    digest, err = extract_digest(
        json.dumps([1, 2, 3]), expected_task_id="t1", snapshot=snap)
    assert digest is None
    assert "not an object" in err


def test_extract_missing_key_errors():
    snap = _snapshot()
    raw = json.dumps({          # no "notes"
        "task_id": "t1", "failure_category": "x",
        "implicated_nodes": [], "implicated_aspects": [], "evidence": [],
    })
    digest, err = extract_digest(raw, expected_task_id="t1", snapshot=snap)
    assert digest is None
    assert "notes" in err


def test_extract_task_id_mismatch_errors():
    snap = _snapshot()
    raw = _digest_json(task_id="OTHER")
    digest, err = extract_digest(raw, expected_task_id="t1", snapshot=snap)
    assert digest is None
    assert "mismatch" in err


# ── extract_digest: implicated_nodes sanitation (filter-and-keep) ─────────────


def test_extract_filters_hallucinated_node_with_warning():
    snap = _snapshot()
    raw = _digest_json(nodes=[COST_NODE, "proc:ghost_node"])
    digest, err = extract_digest(raw, expected_task_id="t1", snapshot=snap)
    assert err == ""
    assert digest.implicated_nodes == (COST_NODE,)      # real node kept
    assert any("ghost_node" in w for w in digest.warnings)


def test_extract_all_nodes_hallucinated_keeps_empty_tuple():
    snap = _snapshot()
    raw = _digest_json(nodes=["proc:ghost_a", "proc:ghost_b"])
    digest, err = extract_digest(raw, expected_task_id="t1", snapshot=snap)
    assert err == ""                     # digest is KEPT, not rejected
    assert digest.implicated_nodes == ()
    assert len(digest.warnings) == 2


def test_extract_non_list_implicated_nodes_coerced_empty():
    snap = _snapshot()
    raw = json.dumps({
        "task_id": "t1", "failure_category": "x",
        "implicated_nodes": "proc:cost_guard_processor",   # a bare string
        "implicated_aspects": [], "evidence": [], "notes": "",
    })
    digest, err = extract_digest(raw, expected_task_id="t1", snapshot=snap)
    assert err == ""
    assert digest.implicated_nodes == ()


def test_extract_evidence_truncated_and_capped():
    snap = _snapshot()
    long_item = "x" * 500
    raw = _digest_json(evidence=[long_item] + [f"e{i}" for i in range(10)])
    digest, err = extract_digest(raw, expected_task_id="t1", snapshot=snap)
    assert err == ""
    assert len(digest.evidence) == 6                 # capped
    assert len(digest.evidence[0]) == 200            # per-item truncation


def test_extract_evidence_deduped_order_preserving():
    snap = _snapshot()
    raw = _digest_json(evidence=["step 1", "step 1", "step 2", "step 1", "step 3"])
    digest, err = extract_digest(raw, expected_task_id="t1", snapshot=snap)
    assert err == ""
    assert digest.evidence == ("step 1", "step 2", "step 3")   # dups gone, order kept


def test_extract_evidence_dedup_does_not_consume_cap():
    # Six unique values (each duplicated) all survive; the 7th unique is dropped
    # by the count cap, not by a duplicate stealing its slot.
    snap = _snapshot()
    raw = _digest_json(evidence=[
        "a", "a", "b", "b", "c", "c", "d", "d", "e", "e", "f", "f", "g"])
    digest, err = extract_digest(raw, expected_task_id="t1", snapshot=snap)
    assert err == ""
    assert digest.evidence == ("a", "b", "c", "d", "e", "f")


# ── extract_digest: implicated_aspects vocabulary (off-graph column) ──────────


def test_extract_aspect_four_families_kept():
    snap = _snapshot()
    raw = _digest_json(
        aspects=["environment", "model_capability", "tool:Bash", "prompt:system"])
    digest, err = extract_digest(raw, expected_task_id="t1", snapshot=snap)
    assert err == ""
    assert digest.implicated_aspects == (
        "environment", "model_capability", "tool:Bash", "prompt:system")
    assert digest.warnings == ()


def test_extract_aspect_illegal_family_dropped_with_warning():
    snap = _snapshot()
    raw = _digest_json(aspects=["tool:Bash", "hardware_fault", "model/gpt"])
    digest, err = extract_digest(raw, expected_task_id="t1", snapshot=snap)
    assert err == ""                                     # digest kept (polarity)
    assert digest.implicated_aspects == ("tool:Bash",)   # only the legal family
    assert any("hardware_fault" in w for w in digest.warnings)
    assert any("model/gpt" in w for w in digest.warnings)


def test_extract_aspect_empty_suffix_dropped():
    snap = _snapshot()
    raw = _digest_json(aspects=["tool:", "prompt:  ", "tool:RealTool"])
    digest, err = extract_digest(raw, expected_task_id="t1", snapshot=snap)
    assert err == ""
    assert digest.implicated_aspects == ("tool:RealTool",)
    assert sum("empty suffix" in w for w in digest.warnings) == 2


def test_extract_aspect_slash_forms_normalized():
    # A model may echo the HX slash spelling; we accept it into the colon form.
    snap = _snapshot()
    raw = _digest_json(aspects=["tools/WebSearch", "prompt/system_header"])
    digest, err = extract_digest(raw, expected_task_id="t1", snapshot=snap)
    assert err == ""
    assert digest.implicated_aspects == ("tool:WebSearch", "prompt:system_header")


def test_extract_non_list_aspects_coerced_empty():
    snap = _snapshot()
    raw = json.dumps({
        "task_id": "t1", "failure_category": "x",
        "implicated_nodes": [], "implicated_aspects": "environment",  # bare string
        "evidence": [], "notes": "",
    })
    digest, err = extract_digest(raw, expected_task_id="t1", snapshot=snap)
    assert err == ""
    assert digest.implicated_aspects == ()


def test_extract_processor_aspect_moved_to_nodes():
    # ``processor/<name>`` belongs on the graph: if it resolves it is MOVED.
    snap = _snapshot()
    raw = _digest_json(nodes=[], aspects=["processor/cost_guard_processor"])
    digest, err = extract_digest(raw, expected_task_id="t1", snapshot=snap)
    assert err == ""
    assert COST_NODE in digest.implicated_nodes       # moved onto the graph
    assert digest.implicated_aspects == ()            # not left as an aspect
    assert any("moved processor aspect" in w for w in digest.warnings)


def test_extract_processor_aspect_unresolved_dropped():
    snap = _snapshot()
    raw = _digest_json(aspects=["processor/ghost_proc"])
    digest, err = extract_digest(raw, expected_task_id="t1", snapshot=snap)
    assert err == ""
    assert digest.implicated_nodes == ()
    assert digest.implicated_aspects == ()
    assert any("dropped unresolved processor aspect" in w for w in digest.warnings)


def test_extract_processor_aspect_move_dedups_with_existing_node():
    snap = _snapshot()
    raw = _digest_json(nodes=[COST_NODE], aspects=["processor/cost_guard_processor"])
    digest, err = extract_digest(raw, expected_task_id="t1", snapshot=snap)
    assert err == ""
    assert digest.implicated_nodes == (COST_NODE,)    # no duplicate


def test_extract_processor_aspect_pascalcase_moved_to_nodes():
    # The HX vocabulary passes class-name labels (processor/CostGuardProcessor);
    # _compute_slug resolves the class name to the graph's proc: id.
    snap = _snapshot()
    raw = _digest_json(aspects=["processor/CostGuardProcessor"])
    digest, err = extract_digest(raw, expected_task_id="t1", snapshot=snap)
    assert err == ""
    assert COST_NODE in digest.implicated_nodes
    assert digest.implicated_aspects == ()
    assert any("moved processor aspect" in w for w in digest.warnings)


# ── extract_digest: notes ─────────────────────────────────────────────────────


def test_extract_notes_stripped_and_truncated():
    snap = _snapshot()
    raw = _digest_json(notes="  " + "n" * 300 + "  ")
    digest, err = extract_digest(raw, expected_task_id="t1", snapshot=snap)
    assert err == ""
    assert digest.notes == "n" * 200                 # stripped, then capped at 200


def test_extract_notes_none_becomes_empty():
    snap = _snapshot()
    raw = json.dumps({
        "task_id": "t1", "failure_category": "x",
        "implicated_nodes": [], "implicated_aspects": [],
        "evidence": [], "notes": None,
    })
    digest, err = extract_digest(raw, expected_task_id="t1", snapshot=snap)
    assert err == ""
    assert digest.notes == ""


# ── run_digester (stub llm_call over tmp_path trajectories) ───────────────────


def _write_traj(dirpath, task_id, body="the agent ran and produced output"):
    path = dirpath / f"{task_id}.md"
    path.write_text(
        f'---\ntask_id: "{task_id}"\nsteps: 3\n---\n\n## Task\nq\n\n## Result\n{body}\n',
        encoding="utf-8")
    return path


def _task_id_in_prompt(prompt):
    match = re.search(r'"task_id":"([^"]+)"', prompt)
    return match.group(1) if match else ""


def _echo_stub(**digest_kwargs):
    """A stub that echoes back a valid digest for whichever task it is asked about."""
    def llm(prompt):
        return _digest_json(task_id=_task_id_in_prompt(prompt), **digest_kwargs)
    return llm


def test_run_digester_only_failed_rows(tmp_path):
    snap = _snapshot()
    tdir = tmp_path / "trajectories"
    tdir.mkdir()
    _write_traj(tdir, "f1")
    _write_traj(tdir, "p1")
    records = [_record("f1", passed=False), _record("p1", passed=True)]

    calls = []

    def llm(prompt):
        calls.append(_task_id_in_prompt(prompt))
        return _digest_json(task_id="f1")

    res = run_digester(records, tdir, snap, llm)
    assert res.n_failed_tasks == 1
    assert res.n_calls == 1
    assert calls == ["f1"]              # the passed row is never digested
    assert [d.task_id for d in res.digests] == ["f1"]
    assert res.errors == []


def test_run_digester_retries_once_then_succeeds(tmp_path):
    snap = _snapshot()
    tdir = tmp_path / "trajectories"
    tdir.mkdir()
    _write_traj(tdir, "f1")

    prompts = []

    def llm(prompt):
        prompts.append(prompt)
        if len(prompts) == 1:
            return "not json at all"
        return _digest_json(task_id="f1")

    res = run_digester([_record("f1")], tdir, snap, llm)
    assert [d.task_id for d in res.digests] == ["f1"]
    assert res.errors == []
    assert res.n_calls == 2
    assert "## Correction" in prompts[1]      # retry fed the parse error back


def test_run_digester_parse_fails_twice_records_error(tmp_path):
    snap = _snapshot()
    tdir = tmp_path / "trajectories"
    tdir.mkdir()
    _write_traj(tdir, "f1")

    res = run_digester([_record("f1")], tdir, snap, lambda _p: "still not json")
    assert res.digests == []
    assert res.n_calls == 2
    assert len(res.errors) == 1
    assert res.errors[0]["task_id"] == "f1"
    assert "parse failed" in res.errors[0]["error"]


def test_run_digester_transport_exception_continues(tmp_path):
    snap = _snapshot()
    tdir = tmp_path / "trajectories"
    tdir.mkdir()
    _write_traj(tdir, "f1")
    _write_traj(tdir, "f2")

    def llm(prompt):
        if _task_id_in_prompt(prompt) == "f1":
            raise RuntimeError("boom transport")
        return _digest_json(task_id="f2")

    res = run_digester([_record("f1"), _record("f2")], tdir, snap, llm)
    assert [d.task_id for d in res.digests] == ["f2"]      # round survived f1's failure
    assert len(res.errors) == 1
    assert res.errors[0]["task_id"] == "f1"
    assert "boom transport" in res.errors[0]["error"]
    assert res.n_calls == 2                                # f1 (raised) + f2 (ok)


def test_run_digester_missing_trajectory_records_error(tmp_path):
    snap = _snapshot()
    tdir = tmp_path / "trajectories"
    tdir.mkdir()          # deliberately empty

    def llm(_prompt):
        raise AssertionError("llm_call must not run when the trajectory is missing")

    res = run_digester([_record("f1")], tdir, snap, llm)
    assert res.digests == []
    assert res.n_calls == 0
    assert res.errors == [{"task_id": "f1", "error": "trajectory file not found"}]


def test_run_digester_falls_back_to_task_id_md(tmp_path):
    # trajectory_file points at a stale/missing basename; the {task_id}.md
    # fallback under trajectories_dir resolves it.
    snap = _snapshot()
    tdir = tmp_path / "trajectories"
    tdir.mkdir()
    _write_traj(tdir, "f2")
    rec = _record("f2", trajectory_file="stale/dir/does_not_exist.md")

    res = run_digester([rec], tdir, snap, _echo_stub())
    assert [d.task_id for d in res.digests] == ["f2"]
    assert res.errors == []


def test_run_digester_max_tasks_caps_processing(tmp_path):
    snap = _snapshot()
    tdir = tmp_path / "trajectories"
    tdir.mkdir()
    for tid in ("f1", "f2", "f3"):
        _write_traj(tdir, tid)
    records = [_record("f1"), _record("f2"), _record("f3")]

    res = run_digester(records, tdir, snap, _echo_stub(), max_tasks=2)
    assert res.n_failed_tasks == 3                          # pre-cap total is truthful
    assert len(res.digests) + len(res.errors) == 2         # only two were digested
    assert res.n_calls == 2
    assert [d.task_id for d in res.digests] == ["f1", "f2"]


# ── build_round_actionability_prompt ─────────────────────────────────────────


def test_round_prompt_line_structure_and_contract():
    digests = [
        _digest("t1", category="blocked_source", nodes=(COST_NODE,),
                aspects=("tool:WebSearch",), evidence=("step 5",)),
        _digest("t2", category="reasoning_error", nodes=(), aspects=(),
                evidence=()),
    ]
    prompt = build_round_actionability_prompt(digests)
    assert (
        f"- t1: category=blocked_source; nodes=[{COST_NODE}]; "
        "aspects=[tool:WebSearch]; has_evidence=yes"
    ) in prompt
    assert (
        "- t2: category=reasoning_error; nodes=[none]; aspects=[none]; "
        "has_evidence=no"
    ) in prompt
    # two-key round contract
    assert "actionability" in prompt and "rationale" in prompt
    assert "## Correction" not in prompt


def test_round_prompt_empty_digests_fallback():
    prompt = build_round_actionability_prompt([])
    assert "(no unsolved tasks)" in prompt


def test_round_prompt_retry_correction():
    prompt = build_round_actionability_prompt(
        [_digest("t1")], retry_error="json.loads failed: boom")
    assert "## Correction" in prompt
    assert "json.loads failed: boom" in prompt


# ── extract_round_actionability ──────────────────────────────────────────────


def test_round_extract_valid():
    raw = json.dumps({"actionability": 0.75, "rationale": "one fixable tool failure"})
    result, err = extract_round_actionability(raw)
    assert err == ""
    assert isinstance(result, RoundActionability)
    assert result.actionability == 0.75
    assert result.rationale == "one fixable tool failure"


@pytest.mark.parametrize("value,message", [
    (1.5, "actionability 1.5 is out of range [0, 1]"),
    (-0.3, "actionability -0.3 is out of range [0, 1]"),
    (float("nan"), "actionability nan is out of range [0, 1]"),
    ("high", "actionability must be a number in [0, 1]"),
    (True, "actionability must be a number in [0, 1]"),
])
def test_round_extract_actionability_hard_rejected(value, message):
    # HX parity (_parse_round_json): out-of-range / non-numeric / bool is
    # REJECTED, never clamped — and the error string is verbatim-aligned.
    raw = json.dumps({"actionability": value, "rationale": "one fixable failure"})
    result, err = extract_round_actionability(raw)
    assert result is None
    assert err == message


@pytest.mark.parametrize("value", [0.0, 1.0, 0.5])
def test_round_extract_actionability_boundaries_accepted(value):
    raw = json.dumps({"actionability": value, "rationale": "ok"})
    result, err = extract_round_actionability(raw)
    assert err == ""
    assert result is not None and result.actionability == value


def test_round_extract_missing_actionability_errors():
    # actionability is REQUIRED and hard-rejected when absent (never defaulted).
    raw = json.dumps({"rationale": "evidence is unclear this round"})
    result, err = extract_round_actionability(raw)
    assert result is None
    assert err == "actionability must be a number in [0, 1]"


def test_round_extract_rationale_empty_errors():
    raw = json.dumps({"actionability": 0.5, "rationale": "   "})
    result, err = extract_round_actionability(raw)
    assert result is None
    assert "rationale" in err


def test_round_extract_rationale_missing_errors():
    raw = json.dumps({"actionability": 0.5})
    result, err = extract_round_actionability(raw)
    assert result is None
    assert "rationale" in err


def test_round_extract_garbage_text_errors():
    result, err = extract_round_actionability("I cannot decide, sorry.")
    assert result is None and err


def test_round_extract_empty_errors():
    result, err = extract_round_actionability("   ")
    assert result is None and err


# ── run_round_actionability ──────────────────────────────────────────────────


def test_run_round_first_try_success():
    calls = []

    def llm(prompt):
        calls.append(prompt)
        return json.dumps({"actionability": 0.6, "rationale": "fixable"})

    result, err, n = run_round_actionability([_digest("t1")], llm)
    assert isinstance(result, RoundActionability)
    assert result.actionability == 0.6
    assert err == "" and n == 1
    assert len(calls) == 1


def test_run_round_retries_once_then_succeeds():
    prompts = []

    def llm(prompt):
        prompts.append(prompt)
        if len(prompts) == 1:
            return "not json at all"
        return json.dumps({"actionability": 0.4, "rationale": "ok"})

    result, err, n = run_round_actionability([_digest("t1")], llm)
    assert result is not None and result.actionability == 0.4
    assert err == "" and n == 2
    assert "## Correction" in prompts[1]      # retry fed the parse error back


def test_run_round_parse_fails_twice_returns_error():
    result, err, n = run_round_actionability([_digest("t1")], lambda _p: "nope")
    assert result is None
    assert n == 2 and err


def test_run_round_hard_reject_flows_to_error():
    # A valid JSON object whose actionability is out of range is rejected both
    # attempts; the verbatim HX error string surfaces as the final error.
    bad = json.dumps({"actionability": 2.0, "rationale": "x"})
    result, err, n = run_round_actionability([_digest("t1")], lambda _p: bad)
    assert result is None
    assert n == 2
    assert err == "actionability 2.0 is out of range [0, 1]"


def test_run_round_transport_exception():
    def llm(_prompt):
        raise RuntimeError("boom round")

    result, err, n = run_round_actionability([_digest("t1")], llm)
    assert result is None
    assert n == 1                             # raised on the first call, no retry
    assert "boom round" in err


# ── write_digests_jsonl ──────────────────────────────────────────────────────


def test_write_digests_jsonl_row_structure(tmp_path):
    digest = TaskDigest(
        task_id="f1",
        failure_category="blocked_source",
        implicated_nodes=(COST_NODE,),
        implicated_aspects=("tool:WebSearch", "environment"),
        evidence=("step 5: empty output",),
        notes="agent stalled",
        warnings=("dropped hallucinated node not in graph: proc:ghost",),
    )
    errors = [{"task_id": "f2", "error": "trajectory file not found"}]
    out = tmp_path / "digests.jsonl"

    write_digests_jsonl(out, "r7", [digest], errors, model="flash-x")

    lines = out.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    drow, erow = json.loads(lines[0]), json.loads(lines[1])

    assert drow["record_kind"] == "digest"
    assert drow["round_id"] == "r7" and drow["model"] == "flash-x"
    assert isinstance(drow["created_at"], float)
    assert drow["task_id"] == "f1"
    assert drow["failure_category"] == "blocked_source"
    assert drow["implicated_nodes"] == [COST_NODE]
    assert drow["implicated_aspects"] == ["tool:WebSearch", "environment"]
    assert drow["evidence"] == ["step 5: empty output"]
    assert drow["notes"] == "agent stalled"
    assert drow["warnings"] == ["dropped hallucinated node not in graph: proc:ghost"]
    assert "actionability" not in drow          # per-task actionability retired

    assert erow["record_kind"] == "digest_error"
    assert erow["round_id"] == "r7" and erow["model"] == "flash-x"
    assert isinstance(erow["created_at"], float)
    assert erow["task_id"] == "f2"
    assert erow["error"] == "trajectory file not found"


def test_write_digests_jsonl_appends_round_actionability_row(tmp_path):
    digest = _digest("f1", category="blocked_source", nodes=(COST_NODE,),
                     aspects=("tool:WebSearch",), evidence=("step 5",), notes="hi")
    out = tmp_path / "digests.jsonl"

    write_digests_jsonl(
        out, "r7", [digest], [], model="flash-x",
        round_actionability=RoundActionability(0.9, "one fixable failure"))

    lines = out.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2                      # one digest + the trailing a_t row
    drow, arow = json.loads(lines[0]), json.loads(lines[1])
    assert drow["record_kind"] == "digest"
    assert arow["record_kind"] == "round_actionability"
    assert arow["round_id"] == "r7" and arow["model"] == "flash-x"
    assert isinstance(arow["created_at"], float)
    assert arow["actionability"] == 0.9
    assert arow["rationale"] == "one fixable failure"


def test_write_digests_jsonl_no_round_row_by_default(tmp_path):
    out = tmp_path / "digests.jsonl"
    write_digests_jsonl(out, "r7", [_digest("f1")], [], model="flash-x")
    lines = out.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["record_kind"] == "digest"


def test_digester_result_defaults_are_independent():
    a, b = DigesterResult(), DigesterResult()
    a.digests.append("x")
    assert b.digests == []      # no shared mutable default
