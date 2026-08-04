# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""Offline tests for P2 — ``--evolve-abstain``.

Fully offline. Exercises the first-class abstain across its layers: the
``AbstainProposal`` plumbing through the isolated adapter + ``_normalise_candidates``
(PIPELINE_ABSTAIN, not PIPELINE_PROPOSAL), the recipe's ``_maybe_abstain``
classifier and ABSTAIN.md reason capture, the ledger row and pool_state counter,
the LLM-planner prior-abstain feedback (present only when non-empty), and
byte-identical ``error`` behaviour.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from recipe.gaia_evolver import run_variant_pool as rvp  # noqa: E402
from experiments.variant_pool.candidate_pipeline import (  # noqa: E402
    AbstainProposal,
    AuditRecord,
    CandidatePipeline,
    DigesterRoundArtifact,
    IsolatedEvolverAdapter,
    PipelineContext,
    PipelineResult,
    PlanningArtifact,
    ProposalFailure,
)
from experiments.variant_pool.critic import CriticReview  # noqa: E402
from experiments.variant_pool.evidence import TaskDigest  # noqa: E402


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


class _FakeTask:
    level = 1

    def __init__(self, task_id: str) -> None:
        self.task_id = task_id


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


def _make_recipe(tmp_path, *, meta=None, **arg_overrides):
    tmp_path = Path(tmp_path)
    tmp_path.mkdir(parents=True, exist_ok=True)
    baseline = tmp_path / "baseline.yaml"
    baseline.write_bytes(b"baseline: true\n")
    return rvp.VariantPoolRecipe(
        args=_Args(**arg_overrides),
        tasks=[_FakeTask("a"), _FakeTask("z")],
        model_config=None,
        meta_agent=meta if meta is not None else SimpleNamespace(),
        pipeline_eval=None,
        run_dir=tmp_path / "run",
        baseline_config_path=baseline,
    )


def _ctx(root: Path, *, current: Path | None = None, round_idx: int = 4, **overrides) -> PipelineContext:
    if current is None:
        current = root / "cur.yaml"
        current.write_text("x: 1\n", encoding="utf-8")
    traj = root / "traj"
    traj.mkdir(parents=True, exist_ok=True)
    return PipelineContext(
        round_idx=round_idx,
        target_variant="V0",
        current_config_path=current,
        trajectories_dir=traj,
        output_root=root,
        **overrides,
    )


def _slot(root: Path, candidate_id: str = "C-R1-01"):
    from experiments.variant_pool.candidate_pipeline import CandidateSlot

    output_dir = root / "candidates" / candidate_id
    output_dir.mkdir(parents=True, exist_ok=True)
    memo = root / f"{candidate_id}.md"
    memo.write_text("", encoding="utf-8")
    return CandidateSlot(suggested_candidate_id=candidate_id, output_dir=output_dir, memo_path=memo)


def _minimal_pipeline_result(*, audit=()) -> PipelineResult:
    return PipelineResult(
        digests=(),
        plan=None,
        considered_candidates=(),
        ranked_for_gate=(),
        critic_review=CriticReview(no_op=True, no_op_reasons=("test",)),
        audit=tuple(audit),
        revision_count=0,
        digester_artifact=DigesterRoundArtifact(digests=(), actionability=0.0, rationale="t"),
        actionability=0.0,
        actionability_threshold=1.0,
        actionability_threshold_provenance="t",
        short_circuit=None,
        no_op=True,
        no_op_reasons=("test",),
    )


class _AbstainMeta:
    """Copyable meta stub whose ``evolve`` writes a byte-identical config + ABSTAIN.md."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        self.calls: list[Path] = []
        self._contract = None
        self.max_steps = 50
        self.memo_path = None

    def set_candidate_contract(self, contract) -> None:
        self._contract = contract

    async def evolve(self, *, output_dir, current_config, **kwargs):
        self.calls.append(Path(output_dir))
        out = Path(output_dir)
        scratch = out / "_meta_scratch"
        scratch.mkdir(parents=True, exist_ok=True)
        (scratch / "ABSTAIN.md").write_text(self.reason, encoding="utf-8")
        cfg = out / "config.yaml"
        cfg.write_bytes(Path(current_config).read_bytes())  # byte-identical no-op
        return cfg


# ===========================================================================
# AbstainProposal plumbing (candidate_pipeline)
# ===========================================================================


def test_adapter_passes_abstain_through(tmp_path: Path) -> None:
    async def producer(*, context, plan, slot):
        return AbstainProposal(slot.suggested_candidate_id, "declined")

    adapter = IsolatedEvolverAdapter(producer=producer)
    results = asyncio.run(
        adapter.propose(context=_ctx(tmp_path), plan=PlanningArtifact(target_variant="V0"), limit=1)
    )
    assert isinstance(results[0], AbstainProposal)
    assert results[0].reason == "declined"


def test_normalise_maps_abstain_to_pipeline_abstain_phase(tmp_path: Path) -> None:
    pipeline = CandidatePipeline(
        digester=object(), planner=object(), evolver=object(), critic=object(), k_t=1
    )
    artifacts, audit = pipeline._normalise_candidates(
        _ctx(tmp_path),
        [AbstainProposal("C-R1-01", "not worth a round")],
        phase="proposal",
        limit=1,
    )
    assert artifacts == ()  # an abstain is not a candidate
    assert len(audit) == 1
    record = audit[0]
    assert record.phase == "abstain"  # -> failed_stage PIPELINE_ABSTAIN
    assert record.disposition == "rejected"  # consumes the slot like a rejection
    assert record.candidate_id == "C-R1-01"
    assert record.reason == "ABSTAIN: not worth a round"


def test_normalise_leaves_proposal_failure_unchanged(tmp_path: Path) -> None:
    pipeline = CandidatePipeline(
        digester=object(), planner=object(), evolver=object(), critic=object(), k_t=1
    )
    _artifacts, audit = pipeline._normalise_candidates(
        _ctx(tmp_path),
        [ProposalFailure("C-R1-01", "producer raised RuntimeError: boom")],
        phase="proposal",
        limit=1,
    )
    assert audit[0].phase == "proposal"  # -> PIPELINE_PROPOSAL, byte-identical
    assert audit[0].reason == "producer raised RuntimeError: boom"


# ===========================================================================
# reason capture + record helpers
# ===========================================================================


def test_read_abstain_reason_caps_and_falls_back(tmp_path: Path) -> None:
    attempt = tmp_path / "att"
    scratch = attempt / "_meta_scratch"
    scratch.mkdir(parents=True, exist_ok=True)
    (scratch / "ABSTAIN.md").write_text("  the parent already fixes this  ", encoding="utf-8")
    assert rvp._read_abstain_reason(attempt) == "the parent already fixes this"

    (scratch / "ABSTAIN.md").write_text("z" * 999, encoding="utf-8")
    assert len(rvp._read_abstain_reason(attempt)) == rvp._ABSTAIN_REASON_CAP

    (scratch / "ABSTAIN.md").write_text("   ", encoding="utf-8")
    assert rvp._read_abstain_reason(attempt) == "(no reason given)"
    assert rvp._read_abstain_reason(tmp_path / "missing") == "(no reason given)"


def test_round_abstain_records_extracts_pairs() -> None:
    assert rvp._strip_abstain_prefix("ABSTAIN: foo") == "foo"
    assert rvp._strip_abstain_prefix("foo") == "foo"
    pr = _minimal_pipeline_result(
        audit=(
            AuditRecord(phase="abstain", disposition="rejected", candidate_id="C-R1-01", reason="ABSTAIN: a"),
            AuditRecord(phase="proposal", disposition="rejected", candidate_id="C-R1-02", reason="bucket: missing"),
            AuditRecord(phase="abstain", disposition="rejected", candidate_id=None, reason="ABSTAIN: skip"),
        )
    )
    assert rvp._round_abstain_records(pr) == (("C-R1-01", "a"),)


# ===========================================================================
# _maybe_abstain classifier (recipe)
# ===========================================================================


def test_maybe_abstain_real_change_is_none(tmp_path: Path) -> None:
    recipe = _make_recipe(tmp_path, evolve_abstain="outcome")
    try:
        current = tmp_path / "cur.yaml"
        current.write_bytes(b"a\n")
        changed = tmp_path / "changed.yaml"
        changed.write_bytes(b"b\n")
        ctx = _ctx(tmp_path, current=current)
        assert recipe._maybe_abstain(slot_id="C", config_path=changed, context=ctx, meta={}) is None
    finally:
        recipe.close()


def test_maybe_abstain_outcome_returns_proposal_with_reason(tmp_path: Path) -> None:
    recipe = _make_recipe(tmp_path, evolve_abstain="outcome")
    try:
        current = tmp_path / "cur.yaml"
        current.write_bytes(b"a\n")
        attempt = tmp_path / "att"
        (attempt / "_meta_scratch").mkdir(parents=True, exist_ok=True)
        (attempt / "_meta_scratch" / "ABSTAIN.md").write_text("no better move", encoding="utf-8")
        cfg = attempt / "config.yaml"
        cfg.write_bytes(b"a\n")  # byte-identical
        meta: dict = {}
        result = recipe._maybe_abstain(
            slot_id="C-R1-01", config_path=cfg, context=_ctx(tmp_path, current=current), meta=meta
        )
        assert isinstance(result, AbstainProposal)
        assert result.reason == "no better move"
        assert meta["parse_status"] == "explicit_noop"
        assert meta["abstain_reason"] == "no better move"
    finally:
        recipe.close()


def test_maybe_abstain_auto_reason_overrides_file(tmp_path: Path) -> None:
    recipe = _make_recipe(tmp_path, evolve_abstain="outcome")
    try:
        current = tmp_path / "cur.yaml"
        current.write_bytes(b"a\n")
        attempt = tmp_path / "att"
        attempt.mkdir(parents=True, exist_ok=True)
        cfg = attempt / "config.yaml"
        cfg.write_bytes(b"a\n")
        result = recipe._maybe_abstain(
            slot_id="C",
            config_path=cfg,
            context=_ctx(tmp_path, current=current),
            meta={},
            auto_abstain_reason="exhausted without decision after 2 attempts / 20 steps",
        )
        assert isinstance(result, AbstainProposal)
        assert "exhausted without decision" in result.reason
    finally:
        recipe.close()


def test_maybe_abstain_error_mode_raises(tmp_path: Path) -> None:
    recipe = _make_recipe(tmp_path, evolve_abstain="error")
    try:
        current = tmp_path / "cur.yaml"
        current.write_bytes(b"a\n")
        attempt = tmp_path / "att"
        attempt.mkdir(parents=True, exist_ok=True)
        cfg = attempt / "config.yaml"
        cfg.write_bytes(b"a\n")
        with pytest.raises(ValueError, match="byte-identical explicit no-op"):
            recipe._maybe_abstain(
                slot_id="C", config_path=cfg, context=_ctx(tmp_path, current=current), meta={}
            )
    finally:
        recipe.close()


# ===========================================================================
# _produce_paper_candidate end-to-end abstain
# ===========================================================================


def test_producer_outcome_returns_abstain_with_captured_reason(tmp_path: Path) -> None:
    meta = _AbstainMeta("the parent config already handles this")
    recipe = _make_recipe(tmp_path, meta=meta, evolve_abstain="outcome")
    try:
        current = tmp_path / "cur.yaml"
        current.write_text("x: 1\n", encoding="utf-8")
        ctx = _ctx(tmp_path, current=current)
        result = asyncio.run(
            recipe._produce_paper_candidate(
                variant=SimpleNamespace(),
                context=ctx,
                plan=PlanningArtifact(target_variant="V0"),
                slot=_slot(tmp_path),
            )
        )
        assert isinstance(result, AbstainProposal)
        assert result.reason == "the parent config already handles this"
        assert recipe._candidate_meta["C-R1-01"]["parse_status"] == "explicit_noop"
        assert recipe._candidate_meta["C-R1-01"]["abstain_reason"] == result.reason
    finally:
        recipe.close()


def test_producer_error_mode_raises_byte_identically(tmp_path: Path) -> None:
    meta = _AbstainMeta("would-be reason")
    recipe = _make_recipe(tmp_path, meta=meta, evolve_abstain="error")
    try:
        current = tmp_path / "cur.yaml"
        current.write_text("x: 1\n", encoding="utf-8")
        with pytest.raises(ValueError, match="byte-identical explicit no-op"):
            asyncio.run(
                recipe._produce_paper_candidate(
                    variant=SimpleNamespace(),
                    context=_ctx(tmp_path, current=current),
                    plan=PlanningArtifact(target_variant="V0"),
                    slot=_slot(tmp_path),
                )
            )
    finally:
        recipe.close()


# ===========================================================================
# ledger row (PIPELINE_ABSTAIN) + pool_state counter
# ===========================================================================


def test_pipeline_abstain_row_is_archived(tmp_path: Path) -> None:
    recipe = _make_recipe(tmp_path, evolve_abstain="outcome")
    try:
        rec = AuditRecord(
            phase="abstain", disposition="rejected", candidate_id="C-R2-01", reason="ABSTAIN: declined"
        )
        recipe._persist_pipeline_audit("V0", 2, _minimal_pipeline_result(audit=(rec,)))
        rows = recipe.evidence.rejected_candidates()
        row = next(r for r in rows if r["candidate_id"] == "C-R2-01")
        assert row["failed_stage"] == "PIPELINE_ABSTAIN"
        assert row["archive_reason"] == "ABSTAIN: declined"
        assert row["round_idx"] == 2 and row["variant_id"] == "V0"
    finally:
        recipe.close()


def test_pool_state_abstains_counter_outcome_only(tmp_path: Path) -> None:
    rec = AuditRecord(
        phase="abstain", disposition="rejected", candidate_id="C-R1-01", reason="ABSTAIN: nope"
    )
    result = SimpleNamespace(candidate_diagnostics={}, selected_candidate_ids={})

    recipe = _make_recipe(tmp_path / "a", evolve_abstain="outcome")
    try:
        recipe._pipeline_results = {"V0": _minimal_pipeline_result(audit=(rec,))}
        acc = recipe._candidate_accounting(result)
        assert acc["abstains"] == 1
        assert acc["abstain_records"] == [{"candidate_id": "C-R1-01", "reason": "nope"}]
        # An abstain counts as a produced proposal (slot arithmetic unchanged).
        assert acc["actual_candidates"] == 1
        assert acc["producer_or_pipeline_rejected"] == 1
    finally:
        recipe.close()

    recipe_err = _make_recipe(tmp_path / "b", evolve_abstain="error")
    try:
        recipe_err._pipeline_results = {"V0": _minimal_pipeline_result(audit=(rec,))}
        acc_err = recipe_err._candidate_accounting(result)
        assert "abstains" not in acc_err  # gated: default accounting is byte-identical
        assert "abstain_records" not in acc_err
    finally:
        recipe_err.close()


# ===========================================================================
# planner feedback (prior abstains) — non-empty only
# ===========================================================================


def _planner() -> rvp._LLMPlanner:
    return rvp._LLMPlanner(provider=object(), k_t=2, fallback=rvp._DeterministicPlanner(2))


def test_prior_abstains_section_present_only_when_nonempty(tmp_path: Path) -> None:
    planner = _planner()
    digests = (TaskDigest(task_id="t0", round_idx=4, variant_id="V0", outcome=(0, 2)),)

    body_default = planner._compose_input(_ctx(tmp_path), digests, max_anchors=None)
    body_empty = planner._compose_input(
        _ctx(tmp_path, prior_abstains=()), digests, max_anchors=None
    )
    body_ab = planner._compose_input(
        _ctx(tmp_path, prior_abstains=(("C-R3-01", "already tried the retry tool"),)),
        digests,
        max_anchors=None,
    )

    assert "PRIOR ABSTAINS" not in body_default
    # An explicit empty tuple is byte-identical to the default (no section).
    assert body_empty == body_default
    assert "PRIOR ABSTAINS (last round):" in body_ab
    assert "C-R3-01: already tried the retry tool" in body_ab


def test_pipeline_context_defaults_prior_abstains_empty(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    assert ctx.prior_abstains == ()


# ===========================================================================
# flag / provenance
# ===========================================================================


def test_abstain_flag_default_and_choices() -> None:
    parser = rvp.build_arg_parser()
    assert parser.parse_args([]).evolve_abstain == "error"
    assert parser.parse_args(["--evolve-abstain", "outcome"]).evolve_abstain == "outcome"
    with pytest.raises(SystemExit):
        parser.parse_args(["--evolve-abstain", "maybe"])


def test_abstain_provenance_is_none_for_default() -> None:
    assert rvp._evolve_abstain_provenance("error") is None
    warn = rvp._evolve_abstain_provenance("outcome")
    assert warn is not None and "evolve_abstain" in warn
