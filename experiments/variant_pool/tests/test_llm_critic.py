# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""Offline tests for the A3 LLM Critic (``recipe.gaia_evolver.run_variant_pool``).

Fully offline: the meta provider is a scripted fake with a ``complete`` coroutine
(never litellm, never a network call). What is exercised is the ``_LLMCritic``
adapter's contract — one review call, the JSON ship_ranking/verdict/rejection/
revision schema, unknown-id dropping, the one-revision cap, ranked-permutation
repair, the deterministic whole-round regression veto that the model cannot
bypass, the parse retry and the wholesale deterministic fallback, the ~30k input
cap — plus the recipe wiring (which adapter is chosen, the truthful pipeline-audit
name, the three-role ``llm_aegis_reproduction`` flag), the wired revision path
driven end-to-end through ``CandidatePipeline.run`` (Part 2), and the composed
three-role provenance string (Part 3).
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

# The recipe lives under ``recipe/``; put the repo root on the path so it and its
# ``experiments.variant_pool`` / ``harnessx`` imports resolve (conftest only adds
# ``experiments/``).
_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from recipe.gaia_evolver import run_variant_pool as rvp  # noqa: E402
from experiments.variant_pool.candidate_pipeline import (  # noqa: E402
    CandidateBrief,
    CandidatePipeline,
    DigesterRoundArtifact,
    IsolatedEvolverAdapter,
    PipelineContext,
    PipelineResult,
    PlanningArtifact,
)
from experiments.variant_pool.critic import (  # noqa: E402
    CriticContext,
    CriticReview,
    DeterministicCritic,
    RevisionRequest,
)
from experiments.variant_pool.evidence import EvidenceStore, TaskDigest  # noqa: E402
from experiments.variant_pool.manifest import CandidateArtifact, ChangeManifest  # noqa: E402


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _ScriptedProvider:
    """Scripted ``complete``: a FIFO queue of response strings.

    The Critic makes ONE call per review (plus at most one parse-retry), so a
    single ordered queue is enough. Every prompt is recorded so tests can assert
    the schema / retry text / that the candidate manifests reached the model.
    """

    def __init__(self, responses) -> None:
        self.responses = list(responses)
        self.prompts: list[str] = []
        self.calls = 0

    async def complete(self, messages, tools, stream_callback=None):
        self.calls += 1
        self.prompts.append(messages[0].content)
        if not self.responses:
            raise AssertionError("no scripted critic response left")
        return SimpleNamespace(content=self.responses.pop(0))


class _RaisingProvider:
    def __init__(self) -> None:
        self.calls = 0

    async def complete(self, messages, tools, stream_callback=None):
        self.calls += 1
        raise RuntimeError("boom")


class _FakeTask:
    def __init__(self, task_id: str, question: str = "") -> None:
        self.task_id = task_id
        self.question = question


# The recipe reads ``t.level`` for its level map; give the task stand-ins one.
_FakeTask.level = 1  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def _context(
    root: Path,
    *,
    round_idx: int = 3,
    target: str = "V0",
    digests=(),
    regressions=(),
    failure_buckets=(),
) -> CriticContext:
    return CriticContext(
        round_idx=round_idx,
        target_variant=target,
        digests=tuple(digests),
        regressions=tuple(regressions),
        failure_buckets=tuple(failure_buckets),
    )


def _artifact(
    root: Path,
    candidate_id: str,
    *,
    target: str = "V0",
    bucket=("prompt",),
    unlock=("t-fail",),
    at_risk=(),
    iterates_from=None,
) -> CandidateArtifact:
    output_dir = root / "artifacts" / candidate_id
    output_dir.mkdir(parents=True, exist_ok=True)
    config = output_dir / "config.yaml"
    config.write_text(f"candidate: {candidate_id}\n", encoding="utf-8")
    code = bool({"tools", "processor"} & set(bucket))
    manifest = ChangeManifest.model_validate(
        {
            "candidate_id": candidate_id,
            "bucket": list(bucket),
            "iterates_from": iterates_from,
            "capability_evidence": (
                [{"type": "other", "claim": "Level 2 roundtrip", "evidence": "survived"}]
                if code
                else []
            ),
            "file_changes": [
                {
                    "path": f"harness/{candidate_id}.txt",
                    "action": "modify",
                    "diff_summary": "candidate mutation",
                }
            ],
            "predicted_impact": {
                "tasks_will_unlock": list(unlock),
                "tasks_at_risk": list(at_risk),
            },
            "attribution_signature": (
                {"type": "tool_call", "tool_name": "Lookup", "expected_min_calls": 1}
                if code
                else None
            ),
            "target_variant": target,
        }
    )
    return CandidateArtifact(config_path=config, manifest=manifest, target_variant=target)


def _failed(task_id: str, *, round_idx: int = 2, category: str = "gaia_level_1", anchors=()) -> TaskDigest:
    return TaskDigest(
        task_id=task_id,
        round_idx=round_idx,
        variant_id="V0",
        outcome=(0, 2),
        failure_category=category,
        evidence_anchors=list(anchors),
    )


def _critic(root: Path, provider, *, sink=None) -> rvp._LLMCritic:
    return rvp._LLMCritic(
        provider=provider,
        fallback=DeterministicCritic(EvidenceStore(root / "evstore")),
        revision_sink=sink,
    )


# ---------------------------------------------------------------------------
# recipe wiring: _Args / _make_recipe (mirrors A1/A2)
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
        self.manifest_mode = "repo"
        self.aegis_digester = "deterministic"
        self.aegis_planner = "deterministic"
        self.aegis_critic = "deterministic"
        self.__dict__.update(overrides)


def _make_recipe(tmp_path, *, meta=None, **overrides):
    baseline = tmp_path / "baseline.yaml"
    baseline.write_bytes(b"baseline: true\n")
    return rvp.VariantPoolRecipe(
        args=_Args(**overrides),
        tasks=[_FakeTask("a"), _FakeTask("z")],
        model_config=None,
        meta_agent=meta if meta is not None else SimpleNamespace(),
        pipeline_eval=None,
        run_dir=tmp_path / "run",
        baseline_config_path=baseline,
    )


# ---------------------------------------------------------------------------
# (0) flag default identity: default -> DeterministicCritic; llm -> _LLMCritic
# ---------------------------------------------------------------------------


def test_default_flag_selects_deterministic_critic(tmp_path):
    recipe = _make_recipe(tmp_path, aegis_critic="deterministic")
    try:
        critic = recipe._make_critic()
        assert isinstance(critic, DeterministicCritic)
        assert not isinstance(critic, rvp._LLMCritic)
        assert recipe._critic_adapter_name == "deterministic_portfolio_fallback"
    finally:
        recipe.close()


def test_llm_flag_selects_llm_critic_with_meta_provider(tmp_path):
    provider = object()
    meta = SimpleNamespace(inner_model=SimpleNamespace(get=lambda key: provider))
    recipe = _make_recipe(tmp_path, aegis_critic="llm", meta=meta)
    try:
        critic = recipe._make_critic()
        assert isinstance(critic, rvp._LLMCritic)
        assert critic.provider is provider
        assert isinstance(critic.fallback, DeterministicCritic)
        # The critic is handed the recipe's round revision sink (same object).
        assert critic.revision_sink is recipe._round_revision_requests
        assert recipe._critic_adapter_name == "MetaModel_llm_critic"
    finally:
        recipe.close()


def test_invalid_aegis_critic_mode_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="aegis_critic"):
        _make_recipe(tmp_path, aegis_critic="bogus")


# ---------------------------------------------------------------------------
# (1) llm happy path: 2 candidates -> ranked + verdicts mapped
# ---------------------------------------------------------------------------


def test_llm_happy_path_two_candidates_ranked_and_verdicts(tmp_path):
    response = json.dumps(
        {
            "ranked_candidate_ids": ["C-R3-02", "C-R3-01"],
            "verdicts": [
                {"candidate_id": "C-R3-02", "rank": 1, "reasons": ["stronger tool evidence"]},
                {"candidate_id": "C-R3-01", "rank": 2, "reasons": ["prompt-only, lower payoff"]},
            ],
            "rejections": [],
            "revision_requests": [],
            "no_op": False,
            "no_op_reasons": [],
            "strategy_concerns": ["both target the same failure cluster"],
        }
    )
    provider = _ScriptedProvider([response])
    critic = _critic(tmp_path, provider)
    c1 = _artifact(tmp_path, "C-R3-01", bucket=("prompt",))
    c2 = _artifact(tmp_path, "C-R3-02", bucket=("tools",))
    digests = (_failed("t-fail", anchors=["R2/t.md"]),)

    review = asyncio.run(
        critic.review(context=_context(tmp_path, digests=digests), candidates=[c1, c2])
    )

    assert provider.calls == 1  # exactly one review call
    assert review.ranked_candidate_ids == ("C-R3-02", "C-R3-01")
    assert not review.no_op
    assert review.rejections == ()
    assert {v.candidate_id: v.rank for v in review.verdicts} == {"C-R3-02": 1, "C-R3-01": 2}
    # mutation_surface is computed from each candidate's manifest file_changes.
    surfaces = {v.candidate_id: v.mutation_surface for v in review.verdicts}
    assert surfaces["C-R3-01"] == ("harness/C-R3-01.txt",)
    assert "both target the same failure cluster" in review.strategy_concerns
    # The Critic only ranks; the deterministic gate stays the shipping authority.
    assert review.requires_deterministic_gate is True
    assert not hasattr(review, "approved")

    # The prompt carried the candidate manifests, the digests, and the "no ship"
    # framing (the deterministic gate is the shipping authority).
    prompt = provider.prompts[0]
    assert "C-R3-01" in prompt and "C-R3-02" in prompt
    assert "predicted_flips" in prompt
    assert "ONLY shipping authority" in prompt
    assert "t-fail" in prompt  # digest surfaced


# ---------------------------------------------------------------------------
# (2) unknown-id drop + note in strategy_concerns
# ---------------------------------------------------------------------------


def test_unknown_ids_are_dropped_with_a_note(tmp_path):
    response = json.dumps(
        {
            "ranked_candidate_ids": ["C-R3-01", "C-GHOST-99"],
            "verdicts": [{"candidate_id": "C-GHOST-99", "rank": 1, "reasons": ["hallucinated"]}],
            "rejections": [{"candidate_id": "C-PHANTOM", "reason": "not real"}],
            "revision_requests": [],
            "no_op": False,
            "no_op_reasons": [],
            "strategy_concerns": [],
        }
    )
    provider = _ScriptedProvider([response])
    critic = _critic(tmp_path, provider)
    c1 = _artifact(tmp_path, "C-R3-01")

    review = asyncio.run(critic.review(context=_context(tmp_path), candidates=[c1]))

    # Only the real candidate survives ranking; verdict/rejection for unknown ids
    # are dropped, each noted in strategy_concerns.
    assert review.ranked_candidate_ids == ("C-R3-01",)
    assert [v.candidate_id for v in review.verdicts] == []
    assert review.rejections == ()
    joined = " || ".join(review.strategy_concerns)
    assert "unknown candidate id 'C-GHOST-99'" in joined
    assert "unknown candidate id 'C-PHANTOM'" in joined


# ---------------------------------------------------------------------------
# (3) more than one revision request -> first kept, rest dropped with note
# ---------------------------------------------------------------------------


def test_more_than_one_revision_request_keeps_the_first(tmp_path):
    response = json.dumps(
        {
            "ranked_candidate_ids": ["C-R3-01", "C-R3-02"],
            "verdicts": [],
            "rejections": [],
            "revision_requests": [
                {"candidate_id": "C-R3-01", "reason": "vague prompt", "instructions": "add a budget"},
                {"candidate_id": "C-R3-02", "reason": "second", "instructions": "ignored"},
            ],
            "no_op": False,
            "no_op_reasons": [],
            "strategy_concerns": [],
        }
    )
    provider = _ScriptedProvider([response])
    sink: dict = {}
    critic = _critic(tmp_path, provider, sink=sink)
    c1 = _artifact(tmp_path, "C-R3-01")
    c2 = _artifact(tmp_path, "C-R3-02")

    review = asyncio.run(critic.review(context=_context(tmp_path), candidates=[c1, c2]))

    assert len(review.revision_requests) == 1  # paper §4.3 cap
    assert review.revision_requests[0].candidate_id == "C-R3-01"
    assert any("only one revision is allowed" in note for note in review.strategy_concerns)
    # The single surviving request's reason+instructions are recorded in the sink.
    assert sink == {"C-R3-01": {"reason": "vague prompt", "instructions": "add a budget"}}


# ---------------------------------------------------------------------------
# (4) ranked-permutation repair: missing non-rejected ids appended in id order
# ---------------------------------------------------------------------------


def test_ranking_is_repaired_to_a_permutation_of_non_rejected(tmp_path):
    response = json.dumps(
        {
            # Only ranks C-R3-03; omits C-R3-01; ranks the rejected C-R3-02.
            "ranked_candidate_ids": ["C-R3-03", "C-R3-02"],
            "verdicts": [],
            "rejections": [{"candidate_id": "C-R3-02", "reason": "banned lever"}],
            "revision_requests": [],
            "no_op": False,
            "no_op_reasons": [],
            "strategy_concerns": [],
        }
    )
    provider = _ScriptedProvider([response])
    critic = _critic(tmp_path, provider)
    candidates = [
        _artifact(tmp_path, "C-R3-01"),
        _artifact(tmp_path, "C-R3-02"),
        _artifact(tmp_path, "C-R3-03"),
    ]

    review = asyncio.run(critic.review(context=_context(tmp_path), candidates=candidates))

    # C-R3-02 rejected -> dropped from ranking; C-R3-01 was missing -> appended in
    # candidate-id order. Result is exactly the non-rejected set {C-R3-01,C-R3-03}.
    assert review.ranked_candidate_ids == ("C-R3-03", "C-R3-01")
    assert {r.candidate_id for r in review.rejections} == {"C-R3-02"}
    joined = " || ".join(review.strategy_concerns)
    assert "also rejected" in joined  # C-R3-02 drop noted
    assert "ranking repaired" in joined and "C-R3-01" in joined  # missing appended


# ---------------------------------------------------------------------------
# (5) whole-round regression veto: an LLM review that ignores an active
#     regression still no_ops the round (parity with DeterministicCritic)
# ---------------------------------------------------------------------------


def test_llm_review_ignoring_a_regression_still_no_ops_the_round(tmp_path):
    # The model happily ranks the candidate and never touches the regression;
    # the deterministic veto is re-applied and forces a whole-round no-op.
    response = json.dumps(
        {
            "ranked_candidate_ids": ["C-R3-01"],
            "verdicts": [{"candidate_id": "C-R3-01", "rank": 1, "reasons": ["looks fine"]}],
            "rejections": [],
            "revision_requests": [],
            "no_op": False,
            "no_op_reasons": [],
            "strategy_concerns": [],
        }
    )
    provider = _ScriptedProvider([response])
    critic = _critic(tmp_path, provider)
    # tasks_at_risk is empty and there is no regression explanation -> "old-task"
    # is an unhandled active regression.
    c1 = _artifact(tmp_path, "C-R3-01", at_risk=())

    review = asyncio.run(
        critic.review(
            context=_context(tmp_path, regressions=("old-task",)),
            candidates=[c1],
        )
    )

    assert review.no_op is True
    assert review.ranked_candidate_ids == ()
    assert "old-task" in review.no_op_reasons[0]
    assert "neither handled in tasks_at_risk nor explained" in review.no_op_reasons[0]


def test_llm_review_that_handles_the_regression_is_not_vetoed(tmp_path):
    # The candidate lists the regressed task in tasks_at_risk -> no veto, ranking
    # stands. Confirms the veto is genuinely conditional, not always-on.
    response = json.dumps(
        {
            "ranked_candidate_ids": ["C-R3-01"],
            "verdicts": [],
            "rejections": [],
            "revision_requests": [],
            "no_op": False,
            "no_op_reasons": [],
            "strategy_concerns": [],
        }
    )
    provider = _ScriptedProvider([response])
    critic = _critic(tmp_path, provider)
    c1 = _artifact(tmp_path, "C-R3-01", at_risk=("old-task",))

    review = asyncio.run(
        critic.review(context=_context(tmp_path, regressions=("old-task",)), candidates=[c1])
    )

    assert review.no_op is False
    assert review.ranked_candidate_ids == ("C-R3-01",)


# ---------------------------------------------------------------------------
# (6) explicit model no_op stops the round without ranking
# ---------------------------------------------------------------------------


def test_model_no_op_stops_the_round(tmp_path):
    response = json.dumps(
        {
            "ranked_candidate_ids": ["C-R3-01"],  # ignored under no_op
            "verdicts": [],
            "rejections": [{"candidate_id": "C-R3-01", "reason": "redundant"}],
            "revision_requests": [],
            "no_op": True,
            "no_op_reasons": ["nothing in this batch is worth a ship slot"],
            "strategy_concerns": [],
        }
    )
    provider = _ScriptedProvider([response])
    critic = _critic(tmp_path, provider)
    c1 = _artifact(tmp_path, "C-R3-01")

    review = asyncio.run(critic.review(context=_context(tmp_path), candidates=[c1]))

    assert review.no_op is True
    assert review.ranked_candidate_ids == ()  # a no-op review cannot also rank
    assert review.no_op_reasons == ("nothing in this batch is worth a ship slot",)
    assert {r.candidate_id for r in review.rejections} == {"C-R3-01"}


# ---------------------------------------------------------------------------
# (7) parse retry: garbage then valid -> used; exactly 2 calls
# ---------------------------------------------------------------------------


def test_parse_retry_uses_second_response(tmp_path):
    valid = json.dumps(
        {
            "ranked_candidate_ids": ["C-R3-01"],
            "verdicts": [],
            "rejections": [],
            "revision_requests": [],
            "no_op": False,
            "no_op_reasons": [],
            "strategy_concerns": [],
        }
    )
    provider = _ScriptedProvider(["not json at all {oops", valid])
    critic = _critic(tmp_path, provider)
    c1 = _artifact(tmp_path, "C-R3-01")

    review = asyncio.run(critic.review(context=_context(tmp_path), candidates=[c1]))

    assert provider.calls == 2
    assert review.ranked_candidate_ids == ("C-R3-01",)
    # The retry prompt fed the parse error back.
    assert "was rejected" in provider.prompts[1]


# ---------------------------------------------------------------------------
# (8) wholesale fallback: provider error / double parse failure -> deterministic
# ---------------------------------------------------------------------------


def test_wholesale_provider_failure_reverts_to_deterministic(tmp_path):
    provider = _RaisingProvider()
    critic = _critic(tmp_path, provider)
    c1 = _artifact(tmp_path, "C-R3-01")
    context = _context(tmp_path)

    review = asyncio.run(critic.review(context=context, candidates=[c1]))

    assert provider.calls == 1  # raised on the first call
    # Exactly the DeterministicCritic result underneath the prefixed concern.
    det = asyncio.run(DeterministicCritic(EvidenceStore(tmp_path / "ev2")).review(context=context, candidates=[c1]))
    assert review.ranked_candidate_ids == det.ranked_candidate_ids
    assert review.strategy_concerns[0].startswith("llm_critic_fell_back: RuntimeError: boom;")


def test_double_parse_failure_reverts_to_deterministic(tmp_path):
    provider = _ScriptedProvider(["nope", "still nope"])
    critic = _critic(tmp_path, provider)
    c1 = _artifact(tmp_path, "C-R3-01")

    review = asyncio.run(critic.review(context=_context(tmp_path), candidates=[c1]))

    assert provider.calls == 2
    assert review.ranked_candidate_ids == ("C-R3-01",)  # deterministic ranking
    assert review.strategy_concerns[0].startswith(
        "llm_critic_fell_back: critic JSON failed twice"
    )


# ---------------------------------------------------------------------------
# (9) input cap: oversized digests -> input under cap with truncation note
# ---------------------------------------------------------------------------


def test_input_is_capped_and_truncation_is_noted(tmp_path):
    big_anchor = "x" * 4000
    digests = tuple(_failed(f"t{i}", anchors=[big_anchor] * 5) for i in range(6))
    critic = _critic(tmp_path, _ScriptedProvider([]))
    candidates = tuple(_artifact(tmp_path, f"C-R3-{i:02d}") for i in range(1, 3))
    context = _context(tmp_path, digests=digests)

    body, truncation = critic._build_input(context, candidates)

    assert len(body) <= rvp._LLM_CRITIC_INPUT_CAP
    assert truncation  # evidence_anchors were trimmed
    assert "evidence_anchors" in truncation[0]
    # Candidate ids and digest task ids are NEVER dropped, only anchors.
    for i in range(6):
        assert f"t{i}" in body
    assert "C-R3-01" in body and "C-R3-02" in body


# ---------------------------------------------------------------------------
# (10) adapter-name truth + three-role llm_aegis_reproduction flag
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
        digester_artifact=DigesterRoundArtifact(digests=(), actionability=0.0, rationale="test"),
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
        ("deterministic", "deterministic_portfolio_fallback"),
        ("llm", "MetaModel_llm_critic"),
    ],
)
def test_pipeline_audit_critic_name_is_truthful(tmp_path, mode, expected_name):
    provider = object()
    meta = SimpleNamespace(inner_model=SimpleNamespace(get=lambda key: provider))
    recipe = _make_recipe(tmp_path, aegis_critic=mode, meta=meta)
    try:
        recipe._persist_pipeline_audit("V0", 3, _minimal_pipeline_result())
        payload = json.loads(
            (recipe.run_dir / "R3" / "V0" / "pipeline_audit.json").read_text(encoding="utf-8")
        )
        assert payload["adapter"]["critic"] == expected_name
        # Only the Critic is LLM here (Digester+Planner deterministic): the
        # AEGIS-complete flag stays False.
        assert payload["adapter"]["llm_aegis_reproduction"] is False
    finally:
        recipe.close()


@pytest.mark.parametrize(
    "digester, planner, critic, expected",
    [
        ("deterministic", "deterministic", "deterministic", False),
        ("llm", "deterministic", "deterministic", False),
        ("llm", "llm", "deterministic", False),
        ("deterministic", "llm", "llm", False),
        ("llm", "llm", "llm", True),
    ],
)
def test_llm_aegis_reproduction_true_only_when_all_three_llm(
    tmp_path, digester, planner, critic, expected
):
    provider = object()
    meta = SimpleNamespace(inner_model=SimpleNamespace(get=lambda key: provider))
    recipe = _make_recipe(
        tmp_path,
        meta=meta,
        aegis_digester=digester,
        aegis_planner=planner,
        aegis_critic=critic,
    )
    try:
        assert recipe._llm_aegis_reproduction is expected
        recipe._persist_pipeline_audit("V0", 3, _minimal_pipeline_result())
        payload = json.loads(
            (recipe.run_dir / "R3" / "V0" / "pipeline_audit.json").read_text(encoding="utf-8")
        )
        assert payload["adapter"]["llm_aegis_reproduction"] is expected
    finally:
        recipe.close()


# ---------------------------------------------------------------------------
# (11) Part 3 — composed three-role provenance string
# ---------------------------------------------------------------------------


def test_composed_pipeline_adapter_all_deterministic_is_byte_identical():
    # Lock literal (machine form) and comparison.json literal (human form) are
    # each returned UNCHANGED when all three roles are deterministic. P1-1: this
    # holds regardless of --aegis-prompts (the prompts suffix is appended only in
    # the composed/any-llm form), so both paper and ours keep the byte-identical
    # literal.
    lock_literal = "deterministic_evidence_digester_planner_critic+llm_metaagent_evolver"
    cmp_literal = "deterministic Digester/Planner/Critic fallbacks + MetaAgent Evolver"
    for prompts_mode in ("paper", "ours"):
        assert (
            rvp._composed_pipeline_adapter(
                "deterministic", "deterministic", "deterministic",
                all_deterministic_literal=lock_literal,
                prompts_mode=prompts_mode,
            )
            == lock_literal
        )
        assert (
            rvp._composed_pipeline_adapter(
                "deterministic", "deterministic", "deterministic",
                all_deterministic_literal=cmp_literal,
                prompts_mode=prompts_mode,
            )
            == cmp_literal
        )


@pytest.mark.parametrize(
    "digester, planner, critic, prompts_mode, expected",
    [
        ("llm", "deterministic", "deterministic", "paper", "digester=llm,planner=deterministic,critic=deterministic+llm_metaagent_evolver,prompts=paper"),
        ("deterministic", "deterministic", "llm", "ours", "digester=deterministic,planner=deterministic,critic=llm+llm_metaagent_evolver,prompts=ours"),
        ("llm", "llm", "llm", "paper", "digester=llm,planner=llm,critic=llm+llm_metaagent_evolver,prompts=paper"),
        ("llm", "llm", "llm", "ours", "digester=llm,planner=llm,critic=llm+llm_metaagent_evolver,prompts=ours"),
    ],
)
def test_composed_pipeline_adapter_uses_composed_form_when_any_role_is_llm(
    digester, planner, critic, prompts_mode, expected
):
    # The literal is IGNORED once any role is llm — the composed form is used, and
    # P1-1 appends the active --aegis-prompts mode as ',prompts=<mode>'.
    assert (
        rvp._composed_pipeline_adapter(
            digester, planner, critic, all_deterministic_literal="IGNORED",
            prompts_mode=prompts_mode,
        )
        == expected
    )


def _lock_base(tmp_path):
    baseline = tmp_path / "baseline.yaml"
    baseline.write_text("harness: frozen\n", encoding="utf-8")

    class _Registry:
        @staticmethod
        def list_names():
            return ["WebFetch", "WebSearch"]

    class _Base:
        tool_registry = _Registry()
        processors: list = []

    return baseline, _Base()


def test_experiment_lock_adapter_all_deterministic_matches_pre_a1_literal(tmp_path):
    baseline, base = _lock_base(tmp_path)
    args = _Args(candidate_mode="paper", target_strategy="worst_first")
    lock = rvp._build_experiment_lock(
        args=args, run_tag="t", baseline_config_path=baseline, original_base=base
    )
    assert (
        lock.hyperparams.candidate_pipeline_adapter
        == "deterministic_evidence_digester_planner_critic+llm_metaagent_evolver"
    )


@pytest.mark.parametrize(
    "digester, planner, critic, prompts, expected",
    [
        # aegis_prompts defaults to paper (paper-first house rule), so the composed
        # lock string carries ,prompts=paper unless overridden to ours (P1-1).
        ("deterministic", "deterministic", "llm", "paper", "digester=deterministic,planner=deterministic,critic=llm+llm_metaagent_evolver,prompts=paper"),
        ("llm", "llm", "llm", "paper", "digester=llm,planner=llm,critic=llm+llm_metaagent_evolver,prompts=paper"),
        ("deterministic", "deterministic", "llm", "ours", "digester=deterministic,planner=deterministic,critic=llm+llm_metaagent_evolver,prompts=ours"),
    ],
)
def test_experiment_lock_adapter_is_composed_when_a_role_is_llm(
    tmp_path, digester, planner, critic, prompts, expected
):
    baseline, base = _lock_base(tmp_path)
    args = _Args(
        candidate_mode="paper",
        target_strategy="worst_first",
        aegis_digester=digester,
        aegis_planner=planner,
        aegis_critic=critic,
        aegis_prompts=prompts,
    )
    lock = rvp._build_experiment_lock(
        args=args, run_tag="t", baseline_config_path=baseline, original_base=base
    )
    assert lock.hyperparams.candidate_pipeline_adapter == expected


# ---------------------------------------------------------------------------
# (12) Part 2 — the revision path, end-to-end through CandidatePipeline.run
# ---------------------------------------------------------------------------


class _ScriptedRevisionMeta:
    """A meta-agent stub that ships a config + a paper manifest.yaml per evolve.

    Shallow-copied per slot by ``_make_variant_meta_agent`` (which shares the
    mutable ``contracts``/``_counter`` state across every slot agent), it records
    the contract handed to each evolve so the test can assert the revision slot's
    contract carried the Critic's instructions. Also exposes ``inner_model`` so a
    recipe built with ``--aegis-critic llm`` could reach a provider (unused here —
    this test uses a scripted critic to drive the revision precisely).
    """

    def __init__(self, provider=None) -> None:
        self.inner_model = SimpleNamespace(get=lambda key: provider)
        self.contracts: list = []  # shared across shallow copies
        self._counter = [0]  # shared across shallow copies
        self._contract = None
        self.memo_path = None

    def set_candidate_contract(self, contract) -> None:
        self._contract = contract

    async def evolve(self, *, output_dir, current_config, trajectories_dir, replay_model, replay_max_cost_usd):
        contract = self._contract
        self.contracts.append(contract)
        suggested = contract["suggested_candidate_id"]
        target = contract["target_variant"]
        out = Path(output_dir)
        (out / "_meta_scratch").mkdir(parents=True, exist_ok=True)
        self._counter[0] += 1
        cfg = out / "config.yaml"
        cfg.write_text(f"evolved: {self._counter[0]}\nfor: {suggested}\n", encoding="utf-8")
        manifest = {
            "candidate_id": suggested,
            "bucket": ["prompt"],
            "capability_evidence": [],
            "file_changes": [
                {"path": "config.yaml", "action": "modify", "diff_summary": "edit prompt"}
            ],
            "predicted_impact": {"tasks_will_unlock": ["t-fail"]},
            "attribution_signature": None,
            "target_variant": target,
        }
        (out / "_meta_scratch" / "manifest.yaml").write_text(
            yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8"
        )
        return cfg


class _ScriptedRevisionCritic:
    """First review requests exactly one revision (recording it to the sink the
    way ``_LLMCritic`` does); the second ranks the surviving candidates."""

    def __init__(self, sink, *, target, reason, instructions) -> None:
        self.sink = sink
        self.target = target
        self.reason = reason
        self.instructions = instructions
        self.calls = 0
        self.candidate_ids_seen: list[tuple[str, ...]] = []

    async def review(self, *, context, candidates):
        self.calls += 1
        ids = tuple(candidate.candidate_id for candidate in candidates)
        self.candidate_ids_seen.append(ids)
        if self.calls == 1:
            self.sink[self.target] = {"reason": self.reason, "instructions": self.instructions}
            return CriticReview(
                ranked_candidate_ids=ids,
                revision_requests=(RevisionRequest(self.target, self.reason),),
            )
        return CriticReview(ranked_candidate_ids=tuple(sorted(ids)))


class _FakeDigester:
    def __init__(self, digests, *, actionability: float = 1.0) -> None:
        self.digests = tuple(digests)
        self.actionability = actionability

    async def digest(self, *, context):
        return DigesterRoundArtifact(
            digests=self.digests, actionability=self.actionability, rationale="actionable"
        )


class _FakePlanner:
    async def plan(self, *, context, digests):
        return PlanningArtifact(
            target_variant=context.target_variant,
            briefs=(
                CandidateBrief(
                    brief_id="P-R3-01",
                    buckets=("prompt",),
                    task_ids=("t-fail",),
                    rationale="tighten the prompt",
                ),
            ),
        )


def test_revision_cycle_end_to_end_injects_instructions_and_joins(tmp_path):
    meta = _ScriptedRevisionMeta()
    recipe = _make_recipe(
        tmp_path,
        meta=meta,
        candidate_mode="paper",
        manifest_mode="paper",
        target_strategy="worst_first",
    )

    output_root = tmp_path / "pipeline"
    output_root.mkdir(parents=True, exist_ok=True)
    current = tmp_path / "cur.yaml"
    current.write_text("root: current\n", encoding="utf-8")
    traj = tmp_path / "traj"
    traj.mkdir(exist_ok=True)
    memo = tmp_path / "memo.md"
    memo.write_text("", encoding="utf-8")
    context = PipelineContext(
        round_idx=3,
        target_variant="V0",
        current_config_path=current,
        trajectories_dir=traj,
        output_root=output_root,
        memo_path=memo,
    )
    variant = SimpleNamespace(
        variant_id="V0", config_path=current, journal_path=memo, routed_tasks={"t-fail"}
    )

    sink = recipe._round_revision_requests
    critic = _ScriptedRevisionCritic(
        sink,
        target="C-R3-01",
        reason="prompt too vague",
        instructions="add an explicit step budget to the system prompt",
    )

    # Wire the pipeline exactly as _run_paper_candidate_pipeline does: an initial
    # producer and a revision producer, both delegating to the recipe's real
    # _produce_paper_candidate; the revision producer looks the request up by
    # slot.revision_of, the same lookup the real closure performs.
    async def producer(*, context, plan, slot):
        return await recipe._produce_paper_candidate(
            variant=variant, context=context, plan=plan, slot=slot
        )

    async def revision_producer(*, context, plan, slot):
        return await recipe._produce_paper_candidate(
            variant=variant,
            context=context,
            plan=plan,
            slot=slot,
            revision=recipe._round_revision_requests.get(slot.revision_of or ""),
        )

    evolver = IsolatedEvolverAdapter(producer=producer, revision_producer=revision_producer)
    pipeline = CandidatePipeline(
        _FakeDigester((_failed("t-fail"),), actionability=1.0),
        _FakePlanner(),
        evolver,
        critic,
        k_t=2,
        actionability_threshold=0.5,
    )

    try:
        result = asyncio.run(pipeline.run(context))
    finally:
        recipe.close()

    # The revision cycle ran exactly once (two critic reviews, one revise).
    assert critic.calls == 2
    assert result.revision_count == 1

    considered = {candidate.candidate_id for candidate in result.considered_candidates}
    # The revised candidate JOINED under a gate-valid paper-shape id and the
    # parent it revised was REPLACED (pipeline semantics: remaining + replacement).
    assert "C-R3-0101" in considered
    assert "C-R3-01" not in considered
    assert "C-R3-02" in considered

    revised = next(c for c in result.considered_candidates if c.candidate_id == "C-R3-0101")
    assert revised.manifest.iterates_from == "C-R3-01"  # points at the parent

    # The revision slot's meta contract carried the Critic's reason + instructions.
    revision_contracts = [
        contract for contract in meta.contracts if contract["suggested_candidate_id"] == "C-030101"
    ]
    assert len(revision_contracts) == 1
    injected = revision_contracts[0]["planner_brief"]["critic_revision_request"]
    assert injected["instructions"] == "add an explicit step budget to the system prompt"
    assert injected["reason"] == "prompt too vague"
    # An ordinary (non-revision) contract never carries the revision key.
    initial_contracts = [
        contract for contract in meta.contracts if contract["suggested_candidate_id"] == "C-0301"
    ]
    assert initial_contracts and "critic_revision_request" not in initial_contracts[0]["planner_brief"]

    # The gate queue is drawn only from the surviving batch.
    assert set(result.critic_review.ranked_candidate_ids) <= {"C-R3-02", "C-R3-0101"}
    assert result.requires_deterministic_gate is True


def test_paper_pipeline_wires_a_revision_producer(tmp_path, monkeypatch):
    """The pipeline-build site now passes a non-None revision_producer, so a
    Critic revision request no longer becomes ProposalFailure('no revision
    producer'). Capture the evolver the recipe hands ``CandidatePipeline``."""
    from experiments.variant_pool.candidate_pipeline import IsolatedEvolverAdapter

    meta = _ScriptedRevisionMeta()
    recipe = _make_recipe(
        tmp_path,
        meta=meta,
        candidate_mode="paper",
        manifest_mode="paper",
        target_strategy="worst_first",
    )
    captured: dict = {}

    class _StubPipeline:
        def __init__(self, *, digester, planner, evolver, critic, k_t, actionability_threshold):
            captured["evolver"] = evolver

        async def run(self, context):
            return _minimal_pipeline_result()

    monkeypatch.setattr(rvp, "CandidatePipeline", _StubPipeline)

    traj = tmp_path / "seed_traj"
    traj.mkdir()
    recipe._last_traj_dir["V0"] = traj
    variant = recipe.pool.variants["V0"]
    try:
        recipe._await(recipe._run_paper_candidate_pipeline(variant, 3))
    finally:
        recipe.close()

    adapter = captured["evolver"]
    assert isinstance(adapter, IsolatedEvolverAdapter)
    assert adapter.producer is not None
    assert adapter.revision_producer is not None  # the A3 wiring under test
