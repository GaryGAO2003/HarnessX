# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""Offline tests for P3 — ``--proposal-repair-retry``.

Fully offline. Exercises the producer-exit schema check (``bucket`` + non-empty
``predicted_impact``, reusing ChangeManifest's own field semantics and error
strings), the single targeted repair evolve (which names the offending field in
its contract and, under --evolve-continuity on, draws the same per-slot step
ledger), the success path, the second-failure "archive as today" path, the
no-op exemption, and byte-identical ``0`` behaviour.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from recipe.gaia_evolver import run_variant_pool as rvp  # noqa: E402
from experiments.variant_pool.candidate_pipeline import (  # noqa: E402
    CandidateArtifact,
    CandidateSlot,
    PipelineContext,
    PlanningArtifact,
)
from experiments.variant_pool.manifest import BUCKETS, ChangeManifest  # noqa: E402


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


def _ctx(root: Path, *, current: Path | None = None) -> PipelineContext:
    if current is None:
        current = root / "cur.yaml"
        current.write_text("x: 1\n", encoding="utf-8")
    traj = root / "traj"
    traj.mkdir(parents=True, exist_ok=True)
    return PipelineContext(
        round_idx=1,
        target_variant="V0",
        current_config_path=current,
        trajectories_dir=traj,
        output_root=root,
    )


def _slot(root: Path, candidate_id: str = "C-R1-01") -> CandidateSlot:
    output_dir = root / "candidates" / candidate_id
    output_dir.mkdir(parents=True, exist_ok=True)
    memo = root / f"{candidate_id}.md"
    memo.write_text("", encoding="utf-8")
    return CandidateSlot(suggested_candidate_id=candidate_id, output_dir=output_dir, memo_path=memo)


def _manifest(*, bucket=("prompt",), unlock=("t-fail",)) -> ChangeManifest:
    return ChangeManifest.model_validate(
        {
            "candidate_id": "C-R1-01",
            "bucket": list(bucket),
            "capability_evidence": [],
            "file_changes": [{"path": "h.txt", "action": "modify", "diff_summary": "m"}],
            "predicted_impact": {"tasks_will_unlock": list(unlock)},
            "target_variant": "V0",
        }
    )


_VALID_MANIFEST_YAML = """\
candidate_id: C-R1-01
bucket:
  - prompt
target_variant: V0
predicted_impact:
  tasks_will_unlock:
    - t-fail
file_changes:
  - path: h.txt
    action: modify
    diff_summary: m
"""

_INVALID_MANIFEST_YAML = """\
candidate_id: C-R1-01
target_variant: V0
predicted_impact:
  tasks_will_unlock:
    - t-fail
file_changes:
  - path: h.txt
    action: modify
    diff_summary: m
"""  # no ``bucket`` -> bucket: missing


class _PaperMeta:
    """Copyable meta stub: writes config.yaml + a per-call manifest.yaml (paper mode)."""

    def __init__(self, manifests, *, max_steps: int = 100) -> None:
        self.manifests = manifests
        self.calls: list[dict] = []
        self._contract = None
        self.max_steps = max_steps
        self.memo_path = None

    def set_candidate_contract(self, contract) -> None:
        self._contract = contract

    async def evolve(self, *, output_dir, current_config=None, **kwargs):
        idx = len(self.calls)
        self.calls.append({"output_dir": Path(output_dir), "contract": self._contract})
        out = Path(output_dir)
        scratch = out / "_meta_scratch"
        scratch.mkdir(parents=True, exist_ok=True)
        yaml_text = self.manifests[min(idx, len(self.manifests) - 1)]
        (scratch / "manifest.yaml").write_text(yaml_text, encoding="utf-8")
        cfg = out / "config.yaml"
        cfg.write_text(f"evolved: {idx}\n", encoding="utf-8")  # distinct from parent
        return cfg


class _RepairMeta:
    """Scripted meta stub for _run_proposal_repair (writes config + state file)."""

    def __init__(self, script, *, steps=None, max_steps: int = 100) -> None:
        self.script = script
        self.steps = steps or []
        self.max_steps = max_steps
        self.calls: list[dict] = []
        self._contract = None

    def set_candidate_contract(self, contract) -> None:
        self._contract = contract

    async def evolve(self, *, output_dir, current_config=None, **kwargs):
        idx = len(self.calls)
        self.calls.append(
            {"output_dir": Path(output_dir), "contract": self._contract, "max_steps": self.max_steps}
        )
        out = Path(output_dir)
        scratch = out / "_meta_scratch"
        scratch.mkdir(parents=True, exist_ok=True)
        sessions = out / "meta_workspace" / "sessions"
        sessions.mkdir(parents=True, exist_ok=True)
        if idx < len(self.steps):
            (sessions / f"{idx}_state.json").write_text(
                json.dumps({"step": self.steps[idx]}), encoding="utf-8"
            )
        if (self.script[idx] if idx < len(self.script) else "ship") == "no_config":
            (scratch / "DECISION_REQUIRED.md").write_text("no decision", encoding="utf-8")
            raise RuntimeError(f"did not produce {out / 'config.yaml'}")
        cfg = out / "config.yaml"
        cfg.write_text(f"repaired: {idx}\n", encoding="utf-8")
        return cfg


class _NoopMeta:
    """Copyable meta stub whose config is byte-identical (an explicit no-op)."""

    def __init__(self) -> None:
        self.calls: list[Path] = []
        self._contract = None
        self.max_steps = 100
        self.memo_path = None

    def set_candidate_contract(self, contract) -> None:
        self._contract = contract

    async def evolve(self, *, output_dir, current_config, **kwargs):
        self.calls.append(Path(output_dir))
        out = Path(output_dir)
        (out / "config.yaml").write_bytes(Path(current_config).read_bytes())
        return out / "config.yaml"


# ===========================================================================
# schema check unit
# ===========================================================================


def test_schema_problems_valid_manifest_is_clean() -> None:
    assert rvp._proposal_schema_problems(_manifest()) == []


def test_schema_problems_bucket_missing() -> None:
    problems = rvp._proposal_schema_problems(_manifest(bucket=()))
    assert problems == ["bucket: missing"]


def test_schema_problems_bucket_unknown() -> None:
    problems = rvp._proposal_schema_problems(_manifest(bucket=("banana",)))
    assert len(problems) == 1
    assert "unknown edit types" in problems[0]
    assert "banana" in problems[0]


def test_schema_problems_predicted_impact_empty() -> None:
    problems = rvp._proposal_schema_problems(_manifest(unlock=()))
    assert problems == [
        "predicted_impact: no predicted flip "
        "(tasks_will_unlock and tasks_will_stabilize are both empty)"
    ]


def test_repair_instruction_names_the_field_and_legal_values() -> None:
    instruction = rvp._proposal_repair_instruction(["bucket: missing"])
    assert "bucket: missing" in instruction
    assert str(list(BUCKETS)) in instruction
    assert "tasks_will_unlock" in instruction


# ===========================================================================
# _run_proposal_repair
# ===========================================================================


def test_repair_runs_and_names_the_field(tmp_path: Path) -> None:
    current = tmp_path / "cur.yaml"
    current.write_text("x: 1\n", encoding="utf-8")
    meta = _RepairMeta(["ship"], max_steps=100)
    slot = _slot(tmp_path)
    new_yaml, steps = asyncio.run(
        rvp._run_proposal_repair(
            slot_agent=meta,
            slot=slot,
            manifest_mode="repo",
            target_variant="V0",
            planner_brief={"rationale": "x"},
            base_evolve_kwargs={"current_config": current},
            problems=["bucket: missing"],
            paper_evolver_guidance=None,
            continuity="off",
            abstain="error",
            total_budget=None,
            steps_used=0,
        )
    )
    assert new_yaml is not None and new_yaml.name == "config.yaml"
    assert meta.calls[0]["output_dir"] == slot.output_dir / "repair"
    brief = meta.calls[0]["contract"]["planner_brief"]
    assert "proposal_repair_instruction" in brief
    assert "bucket: missing" in brief["proposal_repair_instruction"]
    assert steps == 0  # no ledger when continuity is off
    assert meta.max_steps == 100  # untouched


def test_repair_draws_the_shared_ledger_under_continuity(tmp_path: Path) -> None:
    current = tmp_path / "cur.yaml"
    current.write_text("x: 1\n", encoding="utf-8")
    meta = _RepairMeta(["ship"], steps=[20], max_steps=100)
    new_yaml, steps = asyncio.run(
        rvp._run_proposal_repair(
            slot_agent=meta,
            slot=_slot(tmp_path),
            manifest_mode="repo",
            target_variant="V0",
            planner_brief={"rationale": "x"},
            base_evolve_kwargs={"current_config": current},
            problems=["bucket: missing"],
            paper_evolver_guidance=None,
            continuity="on",
            abstain="outcome",
            total_budget=100,
            steps_used=30,
        )
    )
    assert new_yaml is not None
    assert meta.calls[0]["max_steps"] == 70  # granted = total(100) - steps_used(30)
    assert steps == 20  # read from the repair session state
    assert meta.max_steps == 100  # restored after the ledgered attempt


def test_repair_skipped_when_ledger_below_floor(tmp_path: Path) -> None:
    current = tmp_path / "cur.yaml"
    current.write_text("x: 1\n", encoding="utf-8")
    meta = _RepairMeta(["ship"], max_steps=100)
    new_yaml, steps = asyncio.run(
        rvp._run_proposal_repair(
            slot_agent=meta,
            slot=_slot(tmp_path),
            manifest_mode="repo",
            target_variant="V0",
            planner_brief={},
            base_evolve_kwargs={"current_config": current},
            problems=["bucket: missing"],
            paper_evolver_guidance=None,
            continuity="on",
            abstain="outcome",
            total_budget=100,
            steps_used=98,  # remaining 2 < floor(5)
        )
    )
    assert new_yaml is None and steps == 0
    assert meta.calls == []  # never launched


def test_repair_that_fails_returns_none(tmp_path: Path) -> None:
    current = tmp_path / "cur.yaml"
    current.write_text("x: 1\n", encoding="utf-8")
    meta = _RepairMeta(["no_config"], max_steps=100)
    new_yaml, _steps = asyncio.run(
        rvp._run_proposal_repair(
            slot_agent=meta,
            slot=_slot(tmp_path),
            manifest_mode="repo",
            target_variant="V0",
            planner_brief={},
            base_evolve_kwargs={"current_config": current},
            problems=["bucket: missing"],
            paper_evolver_guidance=None,
            continuity="off",
            abstain="error",
            total_budget=None,
            steps_used=0,
        )
    )
    assert new_yaml is None  # a failed repair is not fatal


# ===========================================================================
# _produce_paper_candidate schema-repair flow (paper mode)
# ===========================================================================


def test_producer_repairs_a_missing_bucket_then_succeeds(tmp_path: Path) -> None:
    meta = _PaperMeta([_INVALID_MANIFEST_YAML, _VALID_MANIFEST_YAML])
    recipe = _make_recipe(tmp_path, meta=meta, manifest_mode="paper", proposal_repair_retry=1)
    try:
        result = asyncio.run(
            recipe._produce_paper_candidate(
                variant=SimpleNamespace(),
                context=_ctx(tmp_path),
                plan=PlanningArtifact(target_variant="V0"),
                slot=_slot(tmp_path),
            )
        )
        assert isinstance(result, CandidateArtifact)
        assert len(meta.calls) == 2  # initial + ONE repair
        # The repair contract named the offending field.
        repair_brief = meta.calls[1]["contract"]["planner_brief"]
        assert "bucket: missing" in repair_brief["proposal_repair_instruction"]
        # The adopted manifest is now schema-clean.
        assert rvp._proposal_schema_problems(result.manifest) == []
        assert recipe._candidate_meta["C-R1-01"]["proposal_repair_attempted"] is True
    finally:
        recipe.close()


def test_producer_double_failure_archives_as_today(tmp_path: Path) -> None:
    meta = _PaperMeta([_INVALID_MANIFEST_YAML, _INVALID_MANIFEST_YAML])
    recipe = _make_recipe(tmp_path, meta=meta, manifest_mode="paper", proposal_repair_retry=1)
    try:
        result = asyncio.run(
            recipe._produce_paper_candidate(
                variant=SimpleNamespace(),
                context=_ctx(tmp_path),
                plan=PlanningArtifact(target_variant="V0"),
                slot=_slot(tmp_path),
            )
        )
        # P3 does not mask a still-invalid proposal: it returns the artifact so the
        # pipeline archives it exactly as today (bucket still missing).
        assert isinstance(result, CandidateArtifact)
        assert len(meta.calls) == 2  # at most ONE repair
        assert "bucket: missing" in result.manifest.validate_complete()
        assert recipe._candidate_meta["C-R1-01"]["proposal_repair_attempted"] is True
    finally:
        recipe.close()


def test_producer_repair_off_does_not_touch_a_bad_manifest(tmp_path: Path) -> None:
    """repair_retry=0 (default): no repair attempt; byte-identical to today."""
    meta = _PaperMeta([_INVALID_MANIFEST_YAML])
    recipe = _make_recipe(tmp_path, meta=meta, manifest_mode="paper", proposal_repair_retry=0)
    try:
        result = asyncio.run(
            recipe._produce_paper_candidate(
                variant=SimpleNamespace(),
                context=_ctx(tmp_path),
                plan=PlanningArtifact(target_variant="V0"),
                slot=_slot(tmp_path),
            )
        )
        assert isinstance(result, CandidateArtifact)
        assert len(meta.calls) == 1  # never repaired
        assert "proposal_repair_attempted" not in recipe._candidate_meta["C-R1-01"]
    finally:
        recipe.close()


def test_noop_is_exempt_from_schema_checks(tmp_path: Path) -> None:
    """A byte-identical no-op exits before schema validation, so no repair runs."""
    meta = _NoopMeta()
    recipe = _make_recipe(tmp_path, meta=meta, manifest_mode="repo", proposal_repair_retry=1)
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
        assert len(meta.calls) == 1  # no repair evolve for a no-op
        assert not (_slot(tmp_path).output_dir / "repair").exists()
    finally:
        recipe.close()


# ===========================================================================
# flag / provenance
# ===========================================================================


def test_repair_flag_default_and_choices() -> None:
    parser = rvp.build_arg_parser()
    assert parser.parse_args([]).proposal_repair_retry == 0
    assert parser.parse_args(["--proposal-repair-retry", "1"]).proposal_repair_retry == 1
    with pytest.raises(SystemExit):
        parser.parse_args(["--proposal-repair-retry", "2"])


def test_repair_provenance_is_none_for_default() -> None:
    assert rvp._proposal_repair_retry_provenance(0) is None
    warn = rvp._proposal_repair_retry_provenance(1)
    assert warn is not None and "proposal_repair_retry" in warn
