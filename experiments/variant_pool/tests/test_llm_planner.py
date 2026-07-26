# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""Offline tests for the A2 LLM Planner (``recipe.gaia_evolver.run_variant_pool``).

Fully offline: the meta provider is a scripted fake with a ``complete`` coroutine
(never litellm, never a network call). What is exercised is the ``_LLMPlanner``
adapter's contract — one round-level call, the JSON brief schema, bucket/task-id
validation with dropping, brief renumbering, the parse retry and the wholesale
fallback, the ~30k input cap, and the empty-landscape short-circuit wired through
a real ``CandidatePipeline`` — plus the recipe wiring (which adapter is chosen
and the truthful pipeline-audit name).
"""

from __future__ import annotations

import asyncio
import json
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
    CandidatePipeline,
    DigesterRoundArtifact,
    PipelineContext,
    PipelineResult,
    PlanningArtifact,
)
from experiments.variant_pool.critic import CriticReview  # noqa: E402
from experiments.variant_pool.evidence import TaskDigest  # noqa: E402


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _ScriptedProvider:
    """Scripted ``complete``: a FIFO queue of response strings.

    The Planner makes ONE call per round (plus at most one parse-retry), so a
    single ordered queue is enough. Every prompt is recorded so tests can assert
    schema / retry text / that prior-ship history reached the model.
    """

    def __init__(self, responses) -> None:
        self.responses = list(responses)
        self.prompts: list[str] = []
        self.calls = 0

    async def complete(self, messages, tools, stream_callback=None):
        self.calls += 1
        self.prompts.append(messages[0].content)
        if not self.responses:
            raise AssertionError("no scripted planner response left")
        return SimpleNamespace(content=self.responses.pop(0))


class _RaisingProvider:
    def __init__(self) -> None:
        self.calls = 0

    async def complete(self, messages, tools, stream_callback=None):
        self.calls += 1
        raise RuntimeError("boom")


class _FakeDigester:
    """Round digester stub returning a fixed structured artifact."""

    def __init__(self, digests, *, actionability: float = 1.0) -> None:
        self.digests = tuple(digests)
        self.actionability = actionability
        self.calls = 0

    async def digest(self, *, context):
        self.calls += 1
        return DigesterRoundArtifact(
            digests=self.digests,
            actionability=self.actionability,
            rationale="offline test round evidence is actionable",
        )


class _SpyEvolver:
    def __init__(self) -> None:
        self.calls = 0

    async def propose(self, *, context, plan, limit):
        self.calls += 1
        return ()


class _SpyCritic:
    def __init__(self) -> None:
        self.calls = 0

    async def review(self, *, context, candidates):
        self.calls += 1
        return CriticReview(no_op=True, no_op_reasons=("spy critic",))


class _FakeTask:
    def __init__(self, task_id: str, question: str = "") -> None:
        self.task_id = task_id
        self.question = question


# The recipe reads ``t.level`` for its level map; give the task stand-ins one.
_FakeTask.level = 1  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def _context(root: Path, *, round_idx: int = 5, target: str = "V0", **overrides) -> PipelineContext:
    cfg = root / "cur.yaml"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text("x: 1\n", encoding="utf-8")
    traj = root / "traj"
    traj.mkdir(parents=True, exist_ok=True)
    return PipelineContext(
        round_idx=round_idx,
        target_variant=target,
        current_config_path=cfg,
        trajectories_dir=traj,
        output_root=root,
        **overrides,
    )


def _failed(
    task_id: str,
    *,
    round_idx: int = 5,
    category: str = "blocked_source",
    components=(),
    anchors=(),
    prior=(),
) -> TaskDigest:
    return TaskDigest(
        task_id=task_id,
        round_idx=round_idx,
        variant_id="V0",
        outcome=(0, 2),
        failure_category=category,
        implicated_components=list(components),
        evidence_anchors=list(anchors),
        prior_history=list(prior),
    )


def _passed(task_id: str, *, round_idx: int = 5) -> TaskDigest:
    return TaskDigest(task_id=task_id, round_idx=round_idx, variant_id="V0", outcome=(2, 2))


def _planner(provider, *, k_t: int = 2) -> rvp._LLMPlanner:
    return rvp._LLMPlanner(
        provider=provider,
        k_t=k_t,
        fallback=rvp._DeterministicPlanner(k_t),
    )


# ---------------------------------------------------------------------------
# (0) recipe wiring: default -> deterministic; llm -> _LLMPlanner
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
        self.aegis_planner = "deterministic"
        self.__dict__.update(overrides)


def _make_recipe(tmp_path, *, aegis_planner="deterministic", meta=None):
    baseline = tmp_path / "baseline.yaml"
    baseline.write_bytes(b"baseline: true\n")
    return rvp.VariantPoolRecipe(
        args=_Args(aegis_planner=aegis_planner),
        tasks=[_FakeTask("a"), _FakeTask("z")],
        model_config=None,
        meta_agent=meta if meta is not None else SimpleNamespace(),
        pipeline_eval=None,
        run_dir=tmp_path / "run",
        baseline_config_path=baseline,
    )


def test_default_flag_selects_deterministic_planner(tmp_path):
    recipe = _make_recipe(tmp_path, aegis_planner="deterministic")
    try:
        planner = recipe._make_planner()
        assert isinstance(planner, rvp._DeterministicPlanner)
        assert not isinstance(planner, rvp._LLMPlanner)
        assert recipe._planner_adapter_name == "deterministic_failure_cluster_fallback"
    finally:
        recipe.close()


def test_llm_flag_selects_llm_planner_with_meta_provider(tmp_path):
    provider = object()
    meta = SimpleNamespace(inner_model=SimpleNamespace(get=lambda key: provider))
    recipe = _make_recipe(tmp_path, aegis_planner="llm", meta=meta)
    try:
        planner = recipe._make_planner()
        assert isinstance(planner, rvp._LLMPlanner)
        assert planner.provider is provider
        assert isinstance(planner.fallback, rvp._DeterministicPlanner)
        assert planner.k_t == recipe.candidates_per_round
        assert recipe._planner_adapter_name == "MetaModel_llm_planner"
    finally:
        recipe.close()


def test_invalid_aegis_planner_mode_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="aegis_planner"):
        _make_recipe(tmp_path, aegis_planner="bogus")


# ---------------------------------------------------------------------------
# (1) llm happy path: 2 failure clusters, K_t=2 -> 2 renumbered briefs
# ---------------------------------------------------------------------------


def test_llm_happy_path_two_clusters_two_briefs(tmp_path):
    response = json.dumps(
        {
            "briefs": [
                {
                    "buckets": ["tools"],
                    "task_ids": ["t1"],
                    "rationale": "add a MediaWiki text tool for the blocked source",
                },
                {
                    "buckets": ["prompt", "config"],
                    "task_ids": ["t2", "ghost"],
                    "rationale": "tighten the scope prompt and raise the step budget",
                },
            ],
            "landscape_notes": "tools untried for blocked_source; prompt tried once",
        }
    )
    provider = _ScriptedProvider([response])
    planner = _planner(provider, k_t=2)
    context = _context(tmp_path, round_idx=5)
    digests = (
        _failed(
            "t1",
            category="blocked_source",
            components=["tools/WebFetch"],
            prior=[
                {
                    "round_idx": 3,
                    "variant_id": "V0",
                    "outcome": [0, 2],
                    "solved": False,
                    "failure_category": "blocked_source",
                    "ships": ["C-R3-01"],
                }
            ],
        ),
        _failed("t2", category="scope_ambiguity"),
        _passed("t3"),
    )

    plan = asyncio.run(planner.plan(context=context, digests=digests))

    assert provider.calls == 1  # exactly one round-level Planner call
    assert plan.target_variant == "V0"
    assert plan.empty_landscape is False
    assert isinstance(plan, PlanningArtifact)

    assert [brief.brief_id for brief in plan.briefs] == ["P-R5-01", "P-R5-02"]
    first, second = plan.briefs
    assert first.buckets == ("tools",)
    assert first.task_ids == ("t1",)
    assert second.buckets == ("prompt", "config")
    # The unknown "ghost" id is dropped; only this round's ids survive.
    assert second.task_ids == ("t2",)
    assert all(
        set(brief.buckets) <= set(rvp._PAPER_EDIT_CLASSES) for brief in plan.briefs
    )

    joined_notes = " || ".join(plan.notes)
    assert "dropped unknown task_ids" in joined_notes and "ghost" in joined_notes
    assert "tools untried for blocked_source" in joined_notes

    # The prompt carried the round evidence, the edit-class vocabulary, the
    # diversity nudge, and the prior-ship history (what was already tried).
    prompt = provider.prompts[0]
    assert "prompt, tools, config, processor" in prompt  # the four edit classes listed
    assert "differ in their bucket mix" in prompt
    assert "C-R3-01" in prompt  # prior ship surfaced from digest.prior_history
    assert "t3" in prompt  # passed task still summarised for landscape context


def test_llm_brief_with_no_valid_edit_class_is_dropped(tmp_path):
    response = json.dumps(
        {
            "briefs": [
                {
                    "buckets": ["gaia_level_1"],  # a failure label, not an edit class
                    "task_ids": ["t1"],
                    "rationale": "this brief must be dropped",
                },
                {
                    "buckets": ["prompt"],
                    "task_ids": ["t1"],
                    "rationale": "keep this one",
                },
            ],
            "landscape_notes": "one brief had an invalid bucket",
        }
    )
    provider = _ScriptedProvider([response])
    planner = _planner(provider, k_t=4)
    plan = asyncio.run(
        planner.plan(context=_context(tmp_path, round_idx=5), digests=(_failed("t1"),))
    )

    assert [brief.brief_id for brief in plan.briefs] == ["P-R5-01"]
    assert plan.briefs[0].buckets == ("prompt",)
    assert any("no valid edit class" in note for note in plan.notes)


def test_llm_more_than_k_t_briefs_are_capped(tmp_path):
    response = json.dumps(
        {
            "briefs": [
                {"buckets": ["prompt"], "task_ids": ["t1"], "rationale": "a"},
                {"buckets": ["tools"], "task_ids": ["t1"], "rationale": "b"},
                {"buckets": ["config"], "task_ids": ["t1"], "rationale": "c"},
            ],
            "landscape_notes": "three proposed",
        }
    )
    provider = _ScriptedProvider([response])
    planner = _planner(provider, k_t=2)
    plan = asyncio.run(
        planner.plan(context=_context(tmp_path, round_idx=5), digests=(_failed("t1"),))
    )

    assert len(plan.briefs) == 2
    assert [brief.brief_id for brief in plan.briefs] == ["P-R5-01", "P-R5-02"]
    assert any("K_t=2 cap reached" in note for note in plan.notes)


# ---------------------------------------------------------------------------
# (2) parse retry: garbage then valid -> used; exactly 2 calls
# ---------------------------------------------------------------------------


def test_parse_retry_uses_second_response(tmp_path):
    valid = json.dumps(
        {
            "briefs": [{"buckets": ["prompt"], "task_ids": ["t1"], "rationale": "ok"}],
            "landscape_notes": "recovered",
        }
    )
    provider = _ScriptedProvider(["not json at all {oops", valid])
    planner = _planner(provider, k_t=2)
    plan = asyncio.run(
        planner.plan(context=_context(tmp_path, round_idx=5), digests=(_failed("t1"),))
    )

    assert provider.calls == 2
    assert [brief.brief_id for brief in plan.briefs] == ["P-R5-01"]
    assert plan.empty_landscape is False
    # The retry prompt fed the parse error back.
    assert "was rejected" in provider.prompts[1]


# ---------------------------------------------------------------------------
# (3) wholesale failure -> deterministic fallback with prefixed notes
# ---------------------------------------------------------------------------


def test_wholesale_provider_failure_reverts_to_deterministic(tmp_path):
    provider = _RaisingProvider()
    planner = _planner(provider, k_t=2)
    context = _context(tmp_path, round_idx=5)
    digests = (_failed("t1", category="blocked_source"), _failed("t2", category="reasoning"))

    plan = asyncio.run(planner.plan(context=context, digests=digests))

    assert provider.calls == 1  # raised on the first call
    # Exactly the deterministic Planner's briefs underneath the prefix, and the
    # flag is never set by a fallback.
    assert plan.empty_landscape is False
    assert plan.notes[0].startswith("llm_planner_fell_back: RuntimeError: boom; ")
    det = asyncio.run(
        rvp._DeterministicPlanner(2).plan(context=context, digests=digests)
    )
    assert [brief.brief_id for brief in plan.briefs] == [
        brief.brief_id for brief in det.briefs
    ]
    # Deterministic buckets are failure-cluster labels, not the edit-class enum.
    assert plan.briefs[0].buckets == ("blocked_source",)


def test_double_parse_failure_reverts_to_deterministic(tmp_path):
    provider = _ScriptedProvider(["nope", "still nope"])
    planner = _planner(provider, k_t=2)
    context = _context(tmp_path, round_idx=5)
    plan = asyncio.run(planner.plan(context=context, digests=(_failed("t1"),)))

    assert provider.calls == 2
    assert plan.empty_landscape is False
    assert plan.notes[0].startswith(
        "llm_planner_fell_back: planner JSON failed twice"
    )
    assert len(plan.briefs) == 1  # the deterministic cluster brief


# ---------------------------------------------------------------------------
# (4) empty landscape: briefs:[] -> flag -> pipeline short-circuit, no evolver
# ---------------------------------------------------------------------------


def test_empty_landscape_flag_is_set_only_on_model_zero(tmp_path):
    provider = _ScriptedProvider(
        [json.dumps({"briefs": [], "landscape_notes": "nothing addressable this round"})]
    )
    planner = _planner(provider, k_t=2)
    plan = asyncio.run(
        planner.plan(context=_context(tmp_path, round_idx=5), digests=(_failed("t1"),))
    )
    assert plan.briefs == ()
    assert plan.empty_landscape is True
    assert any("nothing addressable this round" in note for note in plan.notes)


def test_empty_landscape_short_circuits_pipeline_without_evolver(tmp_path):
    provider = _ScriptedProvider(
        [json.dumps({"briefs": [], "landscape_notes": "all failures are model_capability limits"})]
    )
    planner = _planner(provider, k_t=2)
    digester = _FakeDigester((_failed("t1"),), actionability=1.0)
    evolver = _SpyEvolver()
    critic = _SpyCritic()
    pipeline = CandidatePipeline(digester, planner, evolver, critic)

    result = asyncio.run(pipeline.run(_context(tmp_path, round_idx=5)))

    assert provider.calls == 1
    assert evolver.calls == 0  # Evolver never ran
    assert critic.calls == 0  # Critic never ran
    assert result.no_op
    assert result.short_circuit == "planner_empty_landscape"
    assert result.plan is not None and result.plan.empty_landscape is True
    assert any(
        record.phase == "planner" and record.disposition == "short_circuit"
        for record in result.audit
    )
    assert any(
        "all failures are model_capability limits" in record.reason
        for record in result.audit
    )


def test_all_invalid_briefs_take_legacy_path_not_empty_landscape(tmp_path):
    # The model returned briefs (non-empty list) that were all dropped as invalid.
    # empty_landscape must stay False so the pipeline uses the LEGACY short-circuit.
    provider = _ScriptedProvider(
        [json.dumps({"briefs": [{"buckets": ["nonsense"], "task_ids": [], "rationale": ""}], "landscape_notes": "x"})]
    )
    planner = _planner(provider, k_t=2)
    plan = asyncio.run(
        planner.plan(context=_context(tmp_path, round_idx=5), digests=(_failed("t1"),))
    )
    assert plan.briefs == ()
    assert plan.empty_landscape is False


def test_deterministic_planner_never_sets_empty_landscape(tmp_path):
    det = rvp._DeterministicPlanner(2)
    # No failing digests -> zero briefs, but the flag stays False (legacy path).
    plan = asyncio.run(
        det.plan(context=_context(tmp_path, round_idx=5), digests=(_passed("t1"),))
    )
    assert plan.briefs == ()
    assert plan.empty_landscape is False


# ---------------------------------------------------------------------------
# (5) input cap: oversized digests -> input under cap with truncation note
# ---------------------------------------------------------------------------


def test_input_is_capped_and_truncation_is_noted(tmp_path):
    big_anchor = "x" * 4000
    digests = tuple(
        _failed(f"t{i}", anchors=[big_anchor] * 5) for i in range(6)
    )
    planner = _planner(_ScriptedProvider([]), k_t=4)
    body, truncation = planner._build_input(_context(tmp_path, round_idx=5), digests)

    assert len(body) <= rvp._LLM_PLANNER_INPUT_CAP
    assert truncation  # evidence_anchors were trimmed
    assert "evidence_anchors" in truncation[0]
    # Task ids / structure are NEVER dropped, only anchors.
    for i in range(6):
        assert f"t{i}" in body


def test_input_cap_note_surfaces_in_plan_notes(tmp_path):
    big_anchor = "y" * 4000
    digests = tuple(_failed(f"t{i}", anchors=[big_anchor] * 5) for i in range(6))
    valid = json.dumps(
        {"briefs": [{"buckets": ["prompt"], "task_ids": ["t0"], "rationale": "ok"}], "landscape_notes": "capped"}
    )
    provider = _ScriptedProvider([valid])
    planner = _planner(provider, k_t=2)
    plan = asyncio.run(
        planner.plan(context=_context(tmp_path, round_idx=5), digests=digests)
    )
    assert any("evidence_anchors" in note for note in plan.notes)
    assert "INPUT NOTES:" in provider.prompts[0]


# ---------------------------------------------------------------------------
# (6) adapter-name truth: pipeline_audit dict shows the active mode's name
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
        ("deterministic", "deterministic_failure_cluster_fallback"),
        ("llm", "MetaModel_llm_planner"),
    ],
)
def test_pipeline_audit_planner_name_is_truthful(tmp_path, mode, expected_name):
    provider = object()
    meta = SimpleNamespace(inner_model=SimpleNamespace(get=lambda key: provider))
    recipe = _make_recipe(tmp_path, aegis_planner=mode, meta=meta)
    try:
        recipe._persist_pipeline_audit("V0", 3, _minimal_pipeline_result())
        payload = json.loads(
            (recipe.run_dir / "R3" / "V0" / "pipeline_audit.json").read_text(encoding="utf-8")
        )
        assert payload["adapter"]["planner"] == expected_name
        # Only Digester+Planner may be LLM in A1/A2; the AEGIS-complete flag stays False.
        assert payload["adapter"]["llm_aegis_reproduction"] is False
    finally:
        recipe.close()
