"""Offline tests for the structured AEGIS candidate batch."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from experiments.variant_pool.candidate_pipeline import (
    AuditRecord,
    CandidateBrief,
    CandidatePipeline,
    CandidateSlot,
    DigesterRoundArtifact,
    IsolatedEvolverAdapter,
    PipelineContext,
    PlanningArtifact,
    ProposalFailure,
)
from experiments.variant_pool.critic import (
    CriticContext,
    CriticReview,
    DeterministicCritic,
    RevisionRequest,
)
from experiments.variant_pool.evidence import EvidenceStore, ShipOutcome, TaskDigest
from experiments.variant_pool.manifest import CandidateArtifact, ChangeManifest


def _context(
    root: Path,
    *,
    round_idx: int = 3,
    target: str = "V0",
    regressions: tuple[str, ...] = (),
    memo_path: Path | None = None,
) -> PipelineContext:
    current = root / "current.yaml"
    current.parent.mkdir(parents=True, exist_ok=True)
    current.write_text("root: current\n", encoding="utf-8")
    trajectories = root / "trajectories"
    trajectories.mkdir(exist_ok=True)
    return PipelineContext(
        round_idx=round_idx,
        target_variant=target,
        current_config_path=current,
        trajectories_dir=trajectories,
        output_root=root,
        memo_path=memo_path,
        regressions=regressions,
    )


def _artifact(
    root: Path,
    candidate_id: str,
    *,
    target: str = "V0",
    bucket: tuple[str, ...] = ("prompt",),
    unlock: tuple[str, ...] = ("new-task",),
    at_risk: tuple[str, ...] = (),
    iterates_from: str | None = None,
    regression_explanations: tuple[tuple[str, str], ...] = (),
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
                {
                    "type": "tool_call",
                    "tool_name": "Lookup",
                    "expected_min_calls": 1,
                }
                if code
                else None
            ),
            "target_variant": target,
        }
    )
    return CandidateArtifact(
        config_path=config,
        manifest=manifest,
        target_variant=target,
        regression_explanations=regression_explanations,
    )


class _Digester:
    def __init__(
        self,
        digests=(),
        *,
        actionability: float = 1.0,
        legacy: bool = False,
    ):
        self.digests = tuple(digests)
        self.actionability = actionability
        self.legacy = legacy
        self.calls = 0

    async def digest(self, *, context):
        self.calls += 1
        if self.legacy:
            return self.digests
        return DigesterRoundArtifact(
            digests=self.digests,
            actionability=self.actionability,
            rationale="offline test round evidence is actionable",
        )


class _Planner:
    def __init__(self, briefs=None, *, empty_landscape=False):
        self.briefs = (
            (
                CandidateBrief(
                    brief_id="brief-1",
                    buckets=("prompt",),
                    task_ids=("new-task",),
                    rationale="offline test actionable landscape",
                ),
            )
            if briefs is None
            else tuple(briefs)
        )
        self.empty_landscape = empty_landscape
        self.calls = 0

    async def plan(self, *, context, digests):
        self.calls += 1
        return PlanningArtifact(
            target_variant=context.target_variant,
            briefs=self.briefs,
            empty_landscape=self.empty_landscape,
        )


class _Evolver:
    def __init__(self, proposals):
        self.proposals = list(proposals)
        self.limit_seen = None
        self.calls = 0

    async def propose(self, *, context, plan, limit):
        self.calls += 1
        self.limit_seen = limit
        return self.proposals


class _RankingCritic:
    def __init__(self):
        self.calls = 0
        self.candidate_ids_seen: list[tuple[str, ...]] = []

    async def review(self, *, context, candidates):
        self.calls += 1
        self.candidate_ids_seen.append(
            tuple(candidate.candidate_id for candidate in candidates)
        )
        return CriticReview(
            ranked_candidate_ids=tuple(
                candidate.candidate_id
                for candidate in sorted(candidates, key=lambda item: item.candidate_id)
            )
        )


@pytest.mark.parametrize(
    ("actionability", "expected_downstream_calls", "expected_short_circuit"),
    [
        (0.49, 0, "actionability_below_threshold"),
        (0.50, 1, None),
        (0.51, 1, None),
    ],
)
@pytest.mark.asyncio
async def test_actionability_threshold_selectively_invokes_downstream_stages(
    tmp_path: Path,
    actionability: float,
    expected_downstream_calls: int,
    expected_short_circuit: str | None,
) -> None:
    context = _context(tmp_path, round_idx=3)
    candidate = _artifact(tmp_path, "C-R3-01")
    digester = _Digester(actionability=actionability)
    planner = _Planner()
    evolver = _Evolver([candidate])
    critic = _RankingCritic()
    pipeline = CandidatePipeline(
        digester,
        planner,
        evolver,
        critic,
        actionability_threshold=0.5,
    )

    result = await pipeline.run(context)

    assert digester.calls == 1
    assert planner.calls == expected_downstream_calls
    assert evolver.calls == expected_downstream_calls
    assert critic.calls == expected_downstream_calls
    assert result.actionability == actionability
    assert result.actionability_threshold == 0.5
    assert result.threshold == 0.5
    assert "OURS" in result.actionability_threshold_provenance
    assert result.short_circuit == expected_short_circuit
    if actionability < 0.5:
        assert result.no_op
        assert result.plan is None
        assert "a_t=0.49 < alpha=0.5" in result.no_op_reasons[0]
        assert any(
            record.phase == "selective_invocation"
            and record.disposition == "no_op"
            and "a_t=0.49 < alpha=0.5" in record.reason
            for record in result.audit
        )
    else:
        assert not result.no_op
        assert result.ranked_for_gate == (candidate,)
        assert any(
            record.phase == "selective_invocation"
            and record.disposition == "continue"
            and f"a_t={actionability:g} >= alpha=0.5" in record.reason
            for record in result.audit
        )


@pytest.mark.asyncio
async def test_empty_planner_landscape_short_circuits_evolver_and_critic(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path, round_idx=3)
    digester = _Digester(actionability=0.8)
    planner = _Planner(briefs=())
    evolver = _Evolver([_artifact(tmp_path, "C-R3-01")])
    critic = _RankingCritic()
    pipeline = CandidatePipeline(digester, planner, evolver, critic)

    result = await pipeline.run(context)

    assert digester.calls == 1
    assert planner.calls == 1
    assert evolver.calls == 0
    assert critic.calls == 0
    assert result.no_op
    assert result.short_circuit == "empty_landscape"
    assert result.plan == PlanningArtifact(target_variant="V0")
    assert "briefs=0" in result.no_op_reasons[0]
    assert any(
        record.phase == "planner"
        and record.disposition == "no_op"
        and "empty actionable landscape" in record.reason
        for record in result.audit
    )


@pytest.mark.asyncio
async def test_planner_empty_landscape_flag_short_circuits_before_evolver(
    tmp_path: Path,
) -> None:
    # [A2/EXP-E07] A Planner that itself declares the mutation landscape empty
    # (empty_landscape=True + zero briefs) short-circuits with the new reason and
    # a dedicated planner short_circuit audit record, never touching Evolver/Critic.
    context = _context(tmp_path, round_idx=3)
    digester = _Digester(actionability=0.8)
    planner = _Planner(
        briefs=(),
        empty_landscape=True,
    )
    evolver = _Evolver([_artifact(tmp_path, "C-R3-01")])
    critic = _RankingCritic()
    pipeline = CandidatePipeline(digester, planner, evolver, critic)

    result = await pipeline.run(context)

    assert planner.calls == 1
    assert evolver.calls == 0
    assert critic.calls == 0
    assert result.no_op
    assert result.short_circuit == "planner_empty_landscape"
    assert any(
        record.phase == "planner" and record.disposition == "short_circuit"
        for record in result.audit
    )


@pytest.mark.asyncio
async def test_empty_briefs_without_flag_keeps_legacy_short_circuit(
    tmp_path: Path,
) -> None:
    # Regression: empty briefs WITHOUT the empty_landscape flag (the deterministic
    # Planner / any fallback) must keep the byte-identical legacy short-circuit.
    context = _context(tmp_path, round_idx=3)
    evolver = _Evolver([_artifact(tmp_path, "C-R3-01")])
    critic = _RankingCritic()
    pipeline = CandidatePipeline(_Digester(actionability=0.8), _Planner(briefs=()), evolver, critic)

    result = await pipeline.run(context)

    assert evolver.calls == 0
    assert critic.calls == 0
    assert result.plan is not None
    assert result.plan.empty_landscape is False
    assert result.short_circuit == "empty_landscape"
    assert not any(
        record.disposition == "short_circuit" for record in result.audit
    )


@pytest.mark.asyncio
async def test_legacy_digester_sequence_is_supported_as_audited_deviation(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path, round_idx=3)
    digest = TaskDigest(
        task_id="legacy-task",
        round_idx=3,
        variant_id="V0",
        outcome=(0, 2),
        failure_category="reasoning",
    )
    candidate = _artifact(tmp_path, "C-R3-01")
    pipeline = CandidatePipeline(
        _Digester((digest,), legacy=True),
        _Planner(),
        _Evolver([candidate]),
        _RankingCritic(),
    )

    result = await pipeline.run(context)

    assert result.digests == (digest,)
    assert result.digester_artifact.digests == result.digests
    assert result.digester_artifact.actionability == 1.0
    assert result.digester_artifact.contract_mode == "legacy_sequence_deviation"
    assert any(
        record.phase == "digester"
        and record.disposition == "deviation"
        and "legacy/deviation" in record.reason
        for record in result.audit
    )


@pytest.mark.asyncio
async def test_critic_is_called_only_with_valid_surviving_candidates(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path, round_idx=3)
    invalid = _artifact(tmp_path, "C-R3-01")
    invalid.manifest.file_changes = []
    survivor = _artifact(tmp_path, "C-R3-02")
    critic = _RankingCritic()
    pipeline = CandidatePipeline(
        _Digester(),
        _Planner(),
        _Evolver([invalid, survivor, object()]),
        critic,
    )

    result = await pipeline.run(context)

    assert critic.calls == 1
    assert critic.candidate_ids_seen == [("C-R3-02",)]
    assert result.considered_candidates == (survivor,)
    assert result.ranked_for_gate == (survivor,)


@pytest.mark.asyncio
async def test_k_t_defaults_to_four_and_batch_is_sorted_and_truncated(tmp_path: Path) -> None:
    context = _context(tmp_path, round_idx=3)
    proposals = [
        _artifact(tmp_path, f"C-R3-{index:02d}", unlock=(f"task-{index}",))
        for index in (6, 2, 5, 1, 4, 3)
    ]
    evolver = _Evolver(proposals)
    pipeline = CandidatePipeline(_Digester(), _Planner(), evolver, _RankingCritic())

    result = await pipeline.run(context)

    assert evolver.limit_seen == 4
    assert [candidate.candidate_id for candidate in result.ranked_for_gate] == [
        "C-R3-01",
        "C-R3-02",
        "C-R3-03",
        "C-R3-04",
    ]
    assert {
        record.candidate_id
        for record in result.audit
        if "K_t=4 limit exceeded" in record.reason
    } == {"C-R3-05", "C-R3-06"}
    with pytest.raises(ValueError, match="k_t"):
        CandidatePipeline(_Digester(), _Planner(), evolver, _RankingCritic(), k_t=5)


@pytest.mark.asyncio
async def test_duplicate_candidate_ids_are_deterministically_archived(tmp_path: Path) -> None:
    context = _context(tmp_path, round_idx=3)
    first = _artifact(tmp_path, "C-R3-01", unlock=("first",))
    duplicate = CandidateArtifact(
        config_path=first.config_path,
        manifest=first.manifest.model_copy(deep=True),
        target_variant="V0",
    )
    pipeline = CandidatePipeline(
        _Digester(),
        _Planner(),
        _Evolver([duplicate, first]),
        _RankingCritic(),
    )

    result = await pipeline.run(context)

    assert [candidate.candidate_id for candidate in result.ranked_for_gate] == ["C-R3-01"]
    assert any(
        record.candidate_id == "C-R3-01" and "duplicate" in record.reason
        for record in result.audit
    )


@pytest.mark.asyncio
async def test_opaque_candidate_and_incomplete_manifest_are_rejected_before_critic(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path, round_idx=3)
    incomplete = _artifact(tmp_path, "C-R3-01")
    incomplete.manifest.file_changes = []
    critic = _RankingCritic()
    pipeline = CandidatePipeline(
        _Digester(),
        _Planner(),
        _Evolver([incomplete, incomplete.config_path]),
        critic,
    )

    result = await pipeline.run(context)

    assert result.no_op
    assert critic.calls == 0
    reasons = [record.reason for record in result.audit]
    assert any("file_changes: missing" in reason for reason in reasons)
    assert any("opaque candidate type" in reason for reason in reasons)


@pytest.mark.asyncio
async def test_target_mismatch_is_machine_rejected(tmp_path: Path) -> None:
    context = _context(tmp_path, round_idx=3, target="V0")
    candidate = _artifact(tmp_path, "C-R3-01", target="V1")
    pipeline = CandidatePipeline(
        _Digester(),
        _Planner(),
        _Evolver([candidate]),
        _RankingCritic(),
    )

    result = await pipeline.run(context)

    assert result.no_op
    assert any("expected 'V0'" in record.reason for record in result.audit)


@pytest.mark.asyncio
async def test_critic_ranking_is_only_a_queue_for_the_deterministic_gate(tmp_path: Path) -> None:
    context = _context(tmp_path, round_idx=3)
    candidate = _artifact(tmp_path, "C-R3-01")
    pipeline = CandidatePipeline(
        _Digester(),
        _Planner(),
        _Evolver([candidate]),
        _RankingCritic(),
    )

    result = await pipeline.run(context)

    assert result.ranked_for_gate == (candidate,)
    assert result.requires_deterministic_gate
    assert result.critic_review.requires_deterministic_gate
    assert not hasattr(result.critic_review, "approved")


@pytest.mark.asyncio
async def test_critic_bans_a_recently_repeated_low_hit_lever(tmp_path: Path) -> None:
    store = EvidenceStore(tmp_path / "run")
    for round_idx in (1, 2):
        store.append_ship(
            ShipOutcome(
                candidate_id=f"C-R{round_idx}-01",
                round_idx=round_idx,
                variant_id="V0",
                levers=["tools"],
                predicted_flips=[f"task-{round_idx}"],
                realized_flips=[],
            )
        )
    critic = DeterministicCritic(store)
    tools = _artifact(tmp_path, "C-R3-01", bucket=("tools",))
    prompt = _artifact(tmp_path, "C-R3-02", bucket=("prompt",))

    review = await critic.review(
        context=CriticContext(round_idx=3, target_variant="V0"),
        candidates=[tools, prompt],
    )

    assert review.ranked_candidate_ids == ("C-R3-02",)
    assert review.rejections[0].candidate_id == "C-R3-01"
    assert "banned lever" in review.rejections[0].reason
    assert any("hit_rate=0.000<0.4" in concern for concern in review.strategy_concerns)


@pytest.mark.asyncio
async def test_unhandled_regression_forces_a_whole_round_no_op(tmp_path: Path) -> None:
    critic = DeterministicCritic(EvidenceStore(tmp_path / "run"))
    candidate = _artifact(tmp_path, "C-R3-01", unlock=("new-task",))
    digest = TaskDigest(
        task_id="untouched-task",
        round_idx=3,
        variant_id="V0",
        outcome=(0, 2),
        failure_category="reasoning",
    )

    review = await critic.review(
        context=CriticContext(
            round_idx=3,
            target_variant="V0",
            digests=(digest,),
            regressions=("old-task",),
            failure_buckets=("tools",),
        ),
        candidates=[candidate],
    )

    assert review.no_op
    assert review.ranked_candidate_ids == ()
    assert "old-task" in review.no_op_reasons[0]
    assert review.unexplored_failure_clusters == ("reasoning",)
    assert review.unexplored_failure_buckets == ("tools",)


class _RevisionEvolver(_Evolver):
    def __init__(self, proposals, root: Path):
        super().__init__(proposals)
        self.root = root
        self.revise_calls = 0

    async def revise(self, *, context, plan, candidate, request):
        self.revise_calls += 1
        return _artifact(
            self.root,
            "C-R3-03",
            unlock=("revised-task",),
            iterates_from=request.candidate_id,
        )


class _RevisionCritic:
    def __init__(self):
        self.calls = 0

    async def review(self, *, context, candidates):
        self.calls += 1
        ranked = tuple(candidate.candidate_id for candidate in candidates)
        if self.calls == 1:
            return CriticReview(
                ranked_candidate_ids=ranked,
                revision_requests=(
                    RevisionRequest("C-R3-02", "second request"),
                    RevisionRequest("C-R3-01", "first deterministic request"),
                ),
            )
        return CriticReview(
            ranked_candidate_ids=ranked,
            revision_requests=(RevisionRequest("C-R3-03", "try revising again"),),
        )


@pytest.mark.asyncio
async def test_pipeline_allows_at_most_one_revision_cycle(tmp_path: Path) -> None:
    context = _context(tmp_path, round_idx=3)
    evolver = _RevisionEvolver(
        [
            _artifact(tmp_path, "C-R3-01", unlock=("a",)),
            _artifact(tmp_path, "C-R3-02", unlock=("b",)),
        ],
        tmp_path,
    )
    critic = _RevisionCritic()
    pipeline = CandidatePipeline(_Digester(), _Planner(), evolver, critic)

    result = await pipeline.run(context)

    assert evolver.revise_calls == 1
    assert critic.calls == 2
    assert result.revision_count == 1
    suppressed = [
        record
        for record in result.audit
        if record.disposition == "revision_suppressed"
    ]
    assert {record.candidate_id for record in suppressed} == {"C-R3-02", "C-R3-03"}


@pytest.mark.asyncio
async def test_isolated_adapter_runs_producers_concurrently_but_returns_slot_order(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path, round_idx=3)
    expected_ids = tuple(f"C-R3-{index:02d}" for index in range(1, 5))
    releases = {candidate_id: asyncio.Event() for candidate_id in expected_ids}
    completed = {candidate_id: asyncio.Event() for candidate_id in expected_ids}
    all_started = asyncio.Event()
    active = 0
    max_active = 0
    completion_order: list[str] = []

    async def producer(*, context, plan, slot: CandidateSlot):
        nonlocal active, max_active
        candidate_id = slot.suggested_candidate_id
        active += 1
        max_active = max(max_active, active)
        if active == len(expected_ids):
            all_started.set()

        await releases[candidate_id].wait()
        completion_order.append(candidate_id)
        active -= 1
        completed[candidate_id].set()
        if candidate_id == "C-R3-03":
            raise RuntimeError("scripted isolated failure")

        config = slot.output_dir / "config.yaml"
        config.write_text(f"candidate: {candidate_id}\n", encoding="utf-8")
        manifest = ChangeManifest.model_validate(
            {
                "candidate_id": candidate_id,
                "bucket": ["prompt"],
                "file_changes": [
                    {
                        "path": f"{candidate_id}.md",
                        "action": "modify",
                        "diff_summary": "concurrent isolated proposal",
                    }
                ],
                "predicted_impact": {
                    "tasks_will_unlock": [candidate_id],
                },
                "target_variant": context.target_variant,
            }
        )
        return CandidateArtifact(config, manifest, context.target_variant)

    adapter = IsolatedEvolverAdapter(producer)
    proposal_task = asyncio.create_task(
        adapter.propose(
            context=context,
            plan=PlanningArtifact(target_variant="V0"),
            limit=4,
        )
    )
    await asyncio.wait_for(all_started.wait(), timeout=1)
    for candidate_id in reversed(expected_ids):
        releases[candidate_id].set()
        await asyncio.wait_for(completed[candidate_id].wait(), timeout=1)
    proposals = await proposal_task

    assert max_active == 4
    assert completion_order == list(reversed(expected_ids))
    assert tuple(proposal.candidate_id for proposal in proposals) == expected_ids
    assert isinstance(proposals[2], ProposalFailure)
    assert "scripted isolated failure" in proposals[2].reason


@pytest.mark.asyncio
async def test_isolated_adapter_gives_each_candidate_private_output_and_memo(
    tmp_path: Path,
) -> None:
    base_memo = tmp_path / "learnings.md"
    base_memo.write_text("shared baseline\n", encoding="utf-8")
    context = _context(tmp_path, round_idx=3, memo_path=base_memo)
    observations: list[tuple[Path, Path, str]] = []

    async def producer(*, context, plan, slot: CandidateSlot):
        before = slot.memo_path.read_text(encoding="utf-8")
        observations.append((slot.output_dir, slot.memo_path, before))
        slot.memo_path.write_text(
            before + f"changed by {slot.suggested_candidate_id}\n",
            encoding="utf-8",
        )
        config = slot.output_dir / "config.yaml"
        config.write_text(
            f"candidate: {slot.suggested_candidate_id}\n",
            encoding="utf-8",
        )
        manifest = ChangeManifest.model_validate(
            {
                "candidate_id": slot.suggested_candidate_id,
                "bucket": ["prompt"],
                "file_changes": [
                    {
                        "path": f"{slot.suggested_candidate_id}.md",
                        "action": "modify",
                        "diff_summary": "isolated",
                    }
                ],
                "predicted_impact": {
                    "tasks_will_unlock": [slot.suggested_candidate_id]
                },
                "target_variant": context.target_variant,
            }
        )
        return CandidateArtifact(config, manifest, context.target_variant)

    adapter = IsolatedEvolverAdapter(producer)
    proposals = await adapter.propose(
        context=context,
        plan=PlanningArtifact(target_variant="V0"),
        limit=4,
    )

    assert len(proposals) == 4
    assert len({output for output, _, _ in observations}) == 4
    assert len({memo for _, memo, _ in observations}) == 4
    assert {before for _, _, before in observations} == {"shared baseline\n"}
    assert base_memo.read_text(encoding="utf-8") == "shared baseline\n"
    assert all(
        artifact.config_path.parent == observations[index][0]
        for index, artifact in enumerate(proposals)
        if isinstance(artifact, CandidateArtifact)
    )
