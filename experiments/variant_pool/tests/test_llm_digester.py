# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""Offline tests for the A1 LLM Digester (``recipe.gaia_evolver.run_variant_pool``).

Fully offline: the meta provider is a scripted fake with a ``complete`` coroutine
(never litellm, never a network call). What is exercised is the ``_LLMDigester``
adapter's contract — split-by-outcome, per-task interpretation calls, round-level
actionability, trajectory windowing, the parse/validation retries and the two
fallback tiers — plus the recipe wiring (which adapter is chosen and the truthful
pipeline-audit name).
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

# The recipe lives under ``recipe/``; put the repo root on the path so it and its
# ``experiments.variant_pool`` / ``harnessx`` imports resolve (conftest only adds
# ``experiments/``).
_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from recipe.gaia_evolver import run_variant_pool as rvp  # noqa: E402
from experiments.variant_pool.candidate_pipeline import (  # noqa: E402
    DigesterRoundArtifact,
    PipelineContext,
    PipelineResult,
)
from experiments.variant_pool.critic import CriticReview  # noqa: E402
from experiments.variant_pool.evidence import EvidenceStore, TaskDigest  # noqa: E402


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeVariant:
    def __init__(self, routed) -> None:
        self.routed_tasks = set(routed)


class _FakePool:
    """Only ``.variants[target].routed_tasks`` is read by the digesters."""

    def __init__(self, variants) -> None:
        self.variants = dict(variants)


class _FakeTask:
    def __init__(self, task_id: str, question: str = "") -> None:
        self.task_id = task_id
        self.question = question


def _key_of(prompt: str) -> str:
    """Dispatch key: 'round' for the round-level prompt, else the failed task id."""
    if "ROUND-LEVEL" in prompt:
        return "round"
    match = re.search(r"TASK ID: (\S+)", prompt)
    return match.group(1) if match else "?"


class _ScriptedProvider:
    """Scripted ``complete``: a per-key queue of response strings.

    ``by_key["t1"]`` is consumed in call order for task ``t1``; ``by_key["round"]``
    for the round-level call. Records every prompt so tests can assert windowing /
    retry text / which tasks were sent to the model.
    """

    def __init__(self, by_key) -> None:
        self.by_key = {k: list(v) for k, v in by_key.items()}
        self.prompts: list[str] = []
        self.calls = 0

    async def complete(self, messages, tools, stream_callback=None):
        self.calls += 1
        prompt = messages[0].content
        self.prompts.append(prompt)
        key = _key_of(prompt)
        queue = self.by_key.get(key)
        if not queue:
            raise AssertionError(f"no scripted response left for key {key!r}")
        return SimpleNamespace(content=queue.pop(0))

    def prompts_for(self, key: str) -> list[str]:
        return [p for p in self.prompts if _key_of(p) == key]


class _RaisingProvider:
    def __init__(self) -> None:
        self.calls = 0

    async def complete(self, messages, tools, stream_callback=None):
        self.calls += 1
        raise RuntimeError("boom")


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def _context(run_dir: Path, *, round_idx: int = 5, target: str = "V0") -> PipelineContext:
    cfg = run_dir / "cur.yaml"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text("x: 1\n", encoding="utf-8")
    traj = run_dir / "traj"
    traj.mkdir(parents=True, exist_ok=True)
    return PipelineContext(
        round_idx=round_idx,
        target_variant=target,
        current_config_path=cfg,
        trajectories_dir=traj,
        output_root=run_dir,
    )


def _write_traj(
    run_dir: Path,
    rel: str,
    *,
    frontmatter: str = "task_id: t\neval_passed: false",
    body: str = "the agent tried to fetch the page and then failed at the last step",
) -> str:
    """Write a ``<frontmatter>\\n\\n<body>`` trajectory .md; return its run-relative path."""
    path = run_dir / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\n{frontmatter}\n---\n\n{body}", encoding="utf-8")
    return rel


def _build(run_dir: Path, provider, *, digests, routed, tasks=(), target="V0", round_idx=5):
    evidence = EvidenceStore(run_dir)
    for digest in digests:
        evidence.append_digest(digest)
    pool = _FakePool({target: _FakeVariant(routed)})
    fallback = rvp._EvidenceDigester(evidence, pool)
    digester = rvp._LLMDigester(
        evidence=evidence,
        pool=pool,
        provider=provider,
        tasks_by_id={t.task_id: t for t in tasks},
        run_dir=run_dir,
        fallback=fallback,
    )
    return digester, _context(run_dir, round_idx=round_idx, target=target)


def _failed(task_id: str, anchor: str, *, round_idx: int = 1) -> TaskDigest:
    return TaskDigest(
        task_id=task_id,
        round_idx=round_idx,
        variant_id="V0",
        outcome=(0, 2),
        failure_category="gaia_level_1",
        evidence_anchors=[anchor],
    )


def _passed(task_id: str, *, round_idx: int = 1) -> TaskDigest:
    return TaskDigest(
        task_id=task_id,
        round_idx=round_idx,
        variant_id="V0",
        outcome=(2, 2),
    )


# ---------------------------------------------------------------------------
# (0) deterministic arm stays byte-stable through the shared enumeration helper
# ---------------------------------------------------------------------------


def test_deterministic_digester_output_is_unchanged(tmp_path):
    """The refactor to ``_latest_settled_digests`` must not move the deterministic
    arm's bytes: the exact rationale string and binary actionability are locked."""
    evidence = EvidenceStore(tmp_path)
    evidence.append_digest(_failed("t1", "R1/t1.md"))
    evidence.append_digest(_passed("t2"))
    pool = _FakePool({"V0": _FakeVariant({"t1", "t2"})})
    det = rvp._EvidenceDigester(evidence, pool)

    art = asyncio.run(det.digest(context=_context(tmp_path, round_idx=5)))
    assert art.contract_mode == "structured"
    assert art.actionability == 1.0
    assert art.rationale == (
        "deterministic fallback actionability is binary: 1.0 when at least one "
        "settled task is unsolved (count=1), else 0.0; threshold is OURS"
    )
    assert [d.task_id for d in art.digests] == ["t1", "t2"]


# ---------------------------------------------------------------------------
# (1) default flag -> _EvidenceDigester (identity); llm flag -> _LLMDigester
# ---------------------------------------------------------------------------


class _Args:
    def __init__(self, **overrides) -> None:
        self.pool_k = 1
        self.num_rounds = 4
        self.pass_k = 2
        self.max_cost = 5.0
        self.concurrency = 2
        self.no_judge = True
        self.run_tag = "test"
        self.model = "task-model"
        self.meta_model = "meta-model"
        self.max_tasks = 0
        self.max_steps = 20
        self.provider_id = "provider"
        self.api_base = None
        self.data_path = None
        self.seed = 0
        self.estimator = "laplace"
        self.cluster_mode = "routed"
        self.routing_mode = "cluster"
        self.routing_window = None
        self.retirement_metric = "task_macro"
        self.candidate_mode = "legacy_single"
        self.candidates_per_round = 4
        self.actionability_threshold = 1.0
        self.target_strategy = "all_active_variants"
        self.patience = 3
        self.planned_seeds = (0, 1, 2)
        self.evolve_steps = 200
        self.aegis_digester = "deterministic"
        self.__dict__.update(overrides)


def _make_recipe(tmp_path, *, aegis_digester="deterministic", meta=None):
    baseline = tmp_path / "baseline.yaml"
    baseline.write_bytes(b"baseline: true\n")
    return rvp.VariantPoolRecipe(
        args=_Args(aegis_digester=aegis_digester),
        tasks=[_FakeTask("a"), _FakeTask("z")],
        model_config=None,
        meta_agent=meta if meta is not None else SimpleNamespace(),
        pipeline_eval=None,
        run_dir=tmp_path / "run",
        baseline_config_path=baseline,
    )


# The recipe reads ``t.level`` for its level map; give the task stand-ins one.
_FakeTask.level = 1  # type: ignore[attr-defined]


def test_default_flag_selects_evidence_digester(tmp_path):
    recipe = _make_recipe(tmp_path, aegis_digester="deterministic")
    try:
        digester = recipe._make_digester()
        assert isinstance(digester, rvp._EvidenceDigester)
        assert not isinstance(digester, rvp._LLMDigester)
        assert recipe._digester_adapter_name == "deterministic_evidence_store_fallback"
    finally:
        recipe.close()


def test_llm_flag_selects_llm_digester_with_meta_provider(tmp_path):
    provider = object()
    meta = SimpleNamespace(inner_model=SimpleNamespace(get=lambda key: provider))
    recipe = _make_recipe(tmp_path, aegis_digester="llm", meta=meta)
    try:
        digester = recipe._make_digester()
        assert isinstance(digester, rvp._LLMDigester)
        assert digester.provider is provider
        assert isinstance(digester.fallback, rvp._EvidenceDigester)
        assert recipe._digester_adapter_name == "MetaModel_llm_digester"
    finally:
        recipe.close()


# ---------------------------------------------------------------------------
# (2) llm happy path: 2 failed + 1 passed
# ---------------------------------------------------------------------------


def test_llm_happy_path_maps_failures_and_skips_passed(tmp_path):
    t1_json = json.dumps(
        {
            "failure_category": "blocked_source",
            "implicated_components": ["tools/WebFetch", "environment"],
            "evidence_anchors": ["step 4: 403 Forbidden"],
            "notes": "site blocked the fetch",
        }
    )
    t2_json = json.dumps(
        {
            "failure_category": "reasoning_error",
            "implicated_components": ["model_capability"],
            "evidence_anchors": ["step 9: wrong unit"],
            "notes": "",
        }
    )
    round_json = json.dumps(
        {"actionability": 0.75, "rationale": "t1 has a concrete addressable tool fix"}
    )
    provider = _ScriptedProvider({"t1": [t1_json], "t2": [t2_json], "round": [round_json]})

    a1 = _write_traj(tmp_path, "R1/t1.md")
    a2 = _write_traj(tmp_path, "R1/t2.md")
    digester, ctx = _build(
        tmp_path,
        provider,
        digests=[_failed("t1", a1), _failed("t2", a2), _passed("t3")],
        routed={"t1", "t2", "t3"},
        tasks=[_FakeTask("t1", "Q1"), _FakeTask("t2", "Q2"), _FakeTask("t3", "Q3")],
    )

    art = asyncio.run(digester.digest(context=ctx))
    assert isinstance(art, DigesterRoundArtifact)
    assert art.contract_mode == "structured"

    # F+1 calls: 2 per-task interpretations + 1 round-level actionability.
    assert provider.calls == 3
    # The PASSED task never went to the model.
    assert provider.prompts_for("t3") == []

    by_id = {d.task_id: d for d in art.digests}
    assert [d.task_id for d in art.digests] == ["t1", "t2", "t3"]  # sorted

    t1 = by_id["t1"]
    assert t1.failure_category == "blocked_source"
    assert set(t1.implicated_components) == {"tools/WebFetch", "environment"}
    assert t1.outcome == (0, 2)  # harness outcome preserved, not re-derived
    assert a1 in t1.evidence_anchors  # trajectory path kept like today
    assert "step 4: 403 Forbidden" in t1.evidence_anchors
    assert "note: site blocked the fetch" in t1.evidence_anchors

    t2 = by_id["t2"]
    assert t2.failure_category == "reasoning_error"
    assert t2.implicated_components == ["model_capability"]
    assert not any(a.startswith("note:") for a in t2.evidence_anchors)  # empty note

    t3 = by_id["t3"]
    assert t3.solved and t3.failure_category is None  # deterministic, untouched

    assert art.actionability == 0.75
    assert "t1 has a concrete addressable tool fix" in art.rationale
    assert "llm_interpreted=2" in art.rationale
    assert "per_task_fallbacks=0" in art.rationale


def test_llm_no_failures_makes_no_calls(tmp_path):
    provider = _ScriptedProvider({})
    digester, ctx = _build(
        tmp_path,
        provider,
        digests=[_passed("t1"), _passed("t2")],
        routed={"t1", "t2"},
        tasks=[_FakeTask("t1"), _FakeTask("t2")],
    )
    art = asyncio.run(digester.digest(context=ctx))
    assert provider.calls == 0
    assert art.actionability == 0.0
    assert "no addressable failure" in art.rationale


# ---------------------------------------------------------------------------
# (3) parse-retry: first response garbage, second valid -> used; exactly 2 calls
# ---------------------------------------------------------------------------


def test_per_task_parse_retry_uses_second_response(tmp_path):
    valid = json.dumps(
        {"failure_category": "reasoning_error", "implicated_components": [], "evidence_anchors": [], "notes": ""}
    )
    provider = _ScriptedProvider({"t1": ["not json at all {oops", valid]})
    a1 = _write_traj(tmp_path, "R1/t1.md")
    digester, ctx = _build(
        tmp_path,
        provider,
        digests=[_failed("t1", a1)],
        routed={"t1"},
        tasks=[_FakeTask("t1", "Q1")],
    )

    # Focused on the per-task interpretation path: exactly two provider calls.
    new_digest, note = asyncio.run(digester._interpret_failed_task(ctx, _failed("t1", a1)))
    assert provider.calls == 2
    assert note is None
    assert new_digest.failure_category == "reasoning_error"
    # The retry prompt fed the parse error back.
    assert "was rejected" in provider.prompts[1]


# ---------------------------------------------------------------------------
# (4) per-task double failure -> that task deterministic, others stay LLM
# ---------------------------------------------------------------------------


def test_per_task_double_failure_falls_back_only_that_task(tmp_path):
    t2_json = json.dumps(
        {"failure_category": "reasoning_error", "implicated_components": ["model_capability"], "evidence_anchors": [], "notes": ""}
    )
    round_json = json.dumps({"actionability": 0.5, "rationale": "one addressable failure remains"})
    provider = _ScriptedProvider(
        {"t1": ["garbage one", "garbage two"], "t2": [t2_json], "round": [round_json]}
    )
    a1 = _write_traj(tmp_path, "R1/t1.md")
    a2 = _write_traj(tmp_path, "R1/t2.md")
    digester, ctx = _build(
        tmp_path,
        provider,
        digests=[_failed("t1", a1), _failed("t2", a2)],
        routed={"t1", "t2"},
        tasks=[_FakeTask("t1", "Q1"), _FakeTask("t2", "Q2")],
    )

    art = asyncio.run(digester.digest(context=ctx))
    # t1 tried twice (both garbage), t2 once, round once.
    assert provider.calls == 4

    by_id = {d.task_id: d for d in art.digests}
    # t1 reverted to the deterministic digest (unchanged category, no LLM anchors).
    assert by_id["t1"].failure_category == "gaia_level_1"
    assert by_id["t1"].evidence_anchors == [a1]
    # t2 stayed LLM-interpreted.
    assert by_id["t2"].failure_category == "reasoning_error"

    # The per-task fallback is recorded in the rationale (audit route: the stage
    # cannot push into the pipeline's AuditRecord list).
    assert "digester_fallback(disposition=fallback): t1:" in art.rationale
    assert "per_task_fallbacks=1" in art.rationale
    assert "llm_interpreted=1" in art.rationale


def test_missing_trajectory_falls_back_that_task(tmp_path):
    provider = _ScriptedProvider({"round": [json.dumps({"actionability": 0.2, "rationale": "no evidence"})]})
    digester, ctx = _build(
        tmp_path,
        provider,
        digests=[_failed("t1", "R1/does_not_exist.md")],
        routed={"t1"},
        tasks=[_FakeTask("t1", "Q1")],
    )
    art = asyncio.run(digester.digest(context=ctx))
    # No per-task call was made (nothing to read); only the round call.
    assert provider.prompts_for("t1") == []
    assert provider.calls == 1
    assert "no readable trajectory" in art.rationale
    assert {d.task_id for d in art.digests} == {"t1"}


# ---------------------------------------------------------------------------
# (5) wholesale provider failure -> full deterministic fallback with prefix
# ---------------------------------------------------------------------------


def test_wholesale_provider_failure_reverts_to_deterministic(tmp_path):
    provider = _RaisingProvider()
    a1 = _write_traj(tmp_path, "R1/t1.md")
    digester, ctx = _build(
        tmp_path,
        provider,
        digests=[_failed("t1", a1)],
        routed={"t1"},
        tasks=[_FakeTask("t1", "Q1")],
    )
    art = asyncio.run(digester.digest(context=ctx))
    assert provider.calls == 1  # raised on the first call
    assert art.rationale.startswith("llm_digester_fell_back: RuntimeError: boom; ")
    # Exactly the deterministic result underneath the prefix.
    assert "deterministic fallback actionability is binary" in art.rationale
    assert art.actionability == 1.0
    by_id = {d.task_id: d for d in art.digests}
    assert by_id["t1"].failure_category == "gaia_level_1"  # untouched
    assert by_id["t1"].evidence_anchors == [a1]


def test_round_double_failure_reverts_to_deterministic(tmp_path):
    valid_task = json.dumps(
        {"failure_category": "blocked_source", "implicated_components": [], "evidence_anchors": [], "notes": ""}
    )
    # Round call returns invalid JSON twice -> wholesale fallback (ruling 4).
    provider = _ScriptedProvider({"t1": [valid_task], "round": ["nope", "still nope"]})
    a1 = _write_traj(tmp_path, "R1/t1.md")
    digester, ctx = _build(
        tmp_path,
        provider,
        digests=[_failed("t1", a1)],
        routed={"t1"},
        tasks=[_FakeTask("t1", "Q1")],
    )
    art = asyncio.run(digester.digest(context=ctx))
    assert art.rationale.startswith("llm_digester_fell_back: round actionability failed twice")
    assert art.actionability == 1.0
    assert {d.task_id for d in art.digests} == {"t1"}


# ---------------------------------------------------------------------------
# (6) windowing: a 200k-char trajectory -> head+tail slices, under the cap
# ---------------------------------------------------------------------------


def test_trajectory_windowing_head_tail_under_cap(tmp_path):
    body = "HEADSTART_MARKER" + ("x" * 100_000) + "MIDDLE_MARKER" + ("y" * 100_000) + "TAILEND_MARKER"
    assert len(body) > 200_000
    a1 = _write_traj(tmp_path, "R1/big.md", frontmatter="task_id: t1\neval_passed: false", body=body)
    valid = json.dumps(
        {"failure_category": "reasoning_error", "implicated_components": [], "evidence_anchors": [], "notes": ""}
    )
    provider = _ScriptedProvider({"t1": [valid]})
    digester, ctx = _build(
        tmp_path,
        provider,
        digests=[_failed("t1", a1)],
        routed={"t1"},
        tasks=[_FakeTask("t1", "Q1")],
    )

    asyncio.run(digester._interpret_failed_task(ctx, _failed("t1", a1)))
    assert provider.calls == 1
    prompt = provider.prompts[0]

    assert "HEADSTART_MARKER" in prompt          # head slice present
    assert "TAILEND_MARKER" in prompt            # tail slice present
    assert "MIDDLE_MARKER" not in prompt         # middle dropped
    assert "eval_passed" in prompt               # frontmatter block included
    assert len(prompt) < 40_000                  # under the ~40k cap


# ---------------------------------------------------------------------------
# (7) actionability clamp: out-of-range -> retry -> valid
# ---------------------------------------------------------------------------


def test_round_actionability_out_of_range_retries(tmp_path):
    valid_task = json.dumps(
        {"failure_category": "blocked_source", "implicated_components": [], "evidence_anchors": [], "notes": ""}
    )
    provider = _ScriptedProvider(
        {
            "t1": [valid_task],
            "round": [
                json.dumps({"actionability": 1.5, "rationale": "too high"}),
                json.dumps({"actionability": 0.4, "rationale": "second try valid"}),
            ],
        }
    )
    a1 = _write_traj(tmp_path, "R1/t1.md")
    digester, ctx = _build(
        tmp_path,
        provider,
        digests=[_failed("t1", a1)],
        routed={"t1"},
        tasks=[_FakeTask("t1", "Q1")],
    )
    art = asyncio.run(digester.digest(context=ctx))
    assert art.actionability == 0.4
    assert "second try valid" in art.rationale
    round_prompts = provider.prompts_for("round")
    assert len(round_prompts) == 2
    assert "out of range" in round_prompts[1]  # validation error fed back


# ---------------------------------------------------------------------------
# (8) adapter-name truth: pipeline_audit dict shows the active mode's name
# ---------------------------------------------------------------------------


def _minimal_pipeline_result() -> PipelineResult:
    return PipelineResult(
        digests=(),
        plan=None,
        considered_candidates=(),
        ranked_for_gate=(),
        critic_review=CriticReview(no_op=True, no_op_reasons=("test",)),
        audit=(),
        revision_count=0,
        digester_artifact=DigesterRoundArtifact(
            digests=(), actionability=0.0, rationale="test"
        ),
        actionability=0.0,
        actionability_threshold=1.0,
        actionability_threshold_provenance="test",
        short_circuit=None,
        no_op=True,
        no_op_reasons=("test",),
    )


@pytest.mark.parametrize(
    "mode, expected_name",
    [
        ("deterministic", "deterministic_evidence_store_fallback"),
        ("llm", "MetaModel_llm_digester"),
    ],
)
def test_pipeline_audit_adapter_name_is_truthful(tmp_path, mode, expected_name):
    provider = object()
    meta = SimpleNamespace(inner_model=SimpleNamespace(get=lambda key: provider))
    recipe = _make_recipe(tmp_path, aegis_digester=mode, meta=meta)
    try:
        recipe._persist_pipeline_audit("V0", 3, _minimal_pipeline_result())
        payload = json.loads(
            (recipe.run_dir / "R3" / "V0" / "pipeline_audit.json").read_text(encoding="utf-8")
        )
        assert payload["adapter"]["digester"] == expected_name
        # Only the Digester is LLM in A1; the AEGIS-complete flag stays False.
        assert payload["adapter"]["llm_aegis_reproduction"] is False
    finally:
        recipe.close()
