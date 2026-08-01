# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""Offline tests for round-boundary ``--resume`` (experiments.variant_pool.resume).

Every fixture is built under ``tmp_path`` — nothing here reads or writes the real
``runs/`` tree (a live run may be writing there). No network, no rollouts: the
tests exercise the loader, the pool/ledger rebuild, the lock guardrail and the
CLI planning in isolation, plus the recipe-glue against a lightweight stub.
"""

from __future__ import annotations

import json
import types
from pathlib import Path

import pytest

from experiments.variant_pool.experiment_lock import (
    ExperimentLock,
    Hyperparams,
    ModelSpec,
)
from experiments.variant_pool.ledger import SuccessLedger
from experiments.variant_pool.pool import VariantPool
from experiments.variant_pool.router import Router
from experiments.variant_pool.resume import (
    ResumeError,
    annotate_resume_provenance,
    apply_resume_state,
    load_resume_state,
    lock_blocking_diffs,
    plan_resume,
    rebuild_pool,
    replay_ledger,
)


# ---------------------------------------------------------------------------
# fixtures / helpers (all under tmp_path)
# ---------------------------------------------------------------------------


def _round_state(
    round_idx: int,
    routing: dict[str, list[str]],
    active_measurements: dict[str, dict[str, list[int]]],
    *,
    decisions: dict[str, str] | None = None,
    forked: list[str] | None = None,
    retired: list[str] | None = None,
    idle: int = 0,
    selected: dict[str, str] | None = None,
    per_variant_pass: dict[str, dict[str, list[int]]] | None = None,
    score_source: dict[str, str] | None = None,
) -> dict:
    """A ``pool_state.json`` dict shaped like the recipe's ``_dump_round`` output."""
    return {
        "round": round_idx,
        "variant_count": len(routing),
        "routing": {vid: sorted(tasks) for vid, tasks in routing.items()},
        "decisions": dict(decisions or {}),
        "forked": list(forked or []),
        "retired": list(retired or []),
        "shipped": bool(forked) or any(d == "apply" for d in (decisions or {}).values()),
        "idle": idle,
        "selected_candidate_ids": dict(selected or {}),
        "active_score_source": dict(score_source or {}),
        "per_variant_pass": dict(per_variant_pass or {}),
        "candidate_gate_measurements": dict(per_variant_pass or {}),
        "active_pool_measurements": {
            vid: dict(tasks) for vid, tasks in active_measurements.items()
        },
    }


def _write_lock(run_dir: Path, *, candidate_mode: str = "paper", **hp) -> ExperimentLock:
    run_dir.mkdir(parents=True, exist_ok=True)
    lock = ExperimentLock(
        experiment_id=run_dir.name,
        created_at="2026-07-28T00:00:00+00:00",
        hyperparams=Hyperparams(candidate_mode=candidate_mode, **hp),
        models=ModelSpec(
            task_agent_model="task-model",
            meta_agent_model="meta-model",
            provider="prov",
            api_base="base",
        ),
    )
    lock.save(run_dir)
    return lock


def _write_run(
    tmp_path: Path,
    rounds: list[dict],
    *,
    tag: str = "resume_run",
    candidate_mode: str = "paper",
    snapshots: bool = True,
    write_lock: bool = True,
    baseline: bool = True,
) -> Path:
    """Materialise a settled run dir: per-round state + snapshots + journals."""
    run_dir = tmp_path / tag
    run_dir.mkdir(parents=True, exist_ok=True)
    if baseline:
        (run_dir / "V0").mkdir(parents=True, exist_ok=True)
        (run_dir / "V0" / "config.yaml").write_text("baseline: v0\n", encoding="utf-8")
    (run_dir / "learnings.md").write_text("# V0 journal\n", encoding="utf-8")
    if write_lock:
        _write_lock(run_dir, candidate_mode=candidate_mode)

    for state in rounds:
        n = state["round"]
        rdir = run_dir / f"R{n}"
        rdir.mkdir(parents=True, exist_ok=True)
        (rdir / "pool_state.json").write_text(
            json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        if snapshots:
            for vid in state["routing"]:
                vdir = rdir / "active_pool" / vid
                (vdir / "trajectories").mkdir(parents=True, exist_ok=True)
                (vdir / "config.yaml").write_text(f"variant: {vid}\nround: {n}\n", encoding="utf-8")
    return run_dir


class _Args:
    """Minimal CLI namespace for ``plan_resume`` (only reads resume/clean/run_tag)."""

    def __init__(self, **overrides) -> None:
        self.resume = None
        self.clean = False
        self.run_tag = None
        self.__dict__.update(overrides)


def _recipe_stub() -> types.SimpleNamespace:
    """A duck-typed recipe holding just what ``apply_resume_state`` mutates."""
    pool = VariantPool(K=8)
    pool.add_root("V0/config.yaml", "learnings.md", tasks={"seed"})
    return types.SimpleNamespace(
        pool=pool,
        ledger=SuccessLedger(),
        engine=types.SimpleNamespace(_idle=0, patience=3),
        router=Router(),
        _last_traj_dir={},
        _config_change_rounds={},
        pool_states=[],
    )


# ---------------------------------------------------------------------------
# 1. two settled rounds -> resume from R2
# ---------------------------------------------------------------------------


def test_resume_from_two_settled_rounds(tmp_path: Path) -> None:
    rounds = [
        _round_state(0, {"V0": ["t1", "t2"]}, {"V0": {"t1": [1, 2], "t2": [0, 2]}}, idle=0),
        _round_state(
            1,
            {"V0": ["t1", "t2"]},
            {"V0": {"t1": [1, 2], "t2": [1, 2]}},
            decisions={"V0": "reject"},
            idle=1,
        ),
    ]
    run_dir = _write_run(tmp_path, rounds)

    state = load_resume_state(run_dir)

    assert state.settled_rounds == (0, 1)
    assert state.last_settled_round == 1
    assert state.next_round == 2
    assert state.idle == 1
    assert state.next_id == 1
    assert state.ever_solved == {"t1", "t2"}
    (v0,) = state.variants
    assert v0.variant_id == "V0"
    assert v0.routed_tasks == ("t1", "t2")
    assert v0.config_source == "active_pool_snapshot@R1"
    assert v0.config_path == run_dir / "R1" / "active_pool" / "V0" / "config.yaml"
    assert v0.last_traj_dir == run_dir / "R1" / "active_pool" / "V0" / "trajectories"

    # Rebuilt pool + ledger let round 2 freeze legally (newest cell < 2).
    pool = rebuild_pool(state, K=8)
    assert set(pool.variants) == {"V0"}
    assert pool.variants["V0"].routed_tasks == {"t1", "t2"}
    assert pool.next_id == 1

    ledger = replay_ledger(state, SuccessLedger())
    assert ledger.ever_solved == {"t1", "t2"}
    assert ledger.max_last_round() == 1  # freeze for round 2 is valid
    # t2 recorded 0/2 in R0 and 1/2 in R1 -> cumulative 1/4.
    cell = ledger.cell("V0", "t2")
    assert (cell.passes, cell.attempts) == (1, 4)
    # per-round buckets survive for the routing freeze window.
    assert ledger.aggregate_counts("V0", ["t2"], before_round=1) == (0, 2)


# ---------------------------------------------------------------------------
# 2. only R0 -> resume from R1
# ---------------------------------------------------------------------------


def test_resume_from_only_r0(tmp_path: Path) -> None:
    rounds = [_round_state(0, {"V0": ["t1"]}, {"V0": {"t1": [2, 2]}}, idle=0)]
    run_dir = _write_run(tmp_path, rounds)

    state = load_resume_state(run_dir)

    assert state.settled_rounds == (0,)
    assert state.next_round == 1
    assert state.idle == 0
    assert state.ever_solved == {"t1"}
    assert len(state.variants) == 1


# ---------------------------------------------------------------------------
# 3. finished run (pool_report.md) -> refuse
# ---------------------------------------------------------------------------


def test_resume_refuses_finished_run(tmp_path: Path) -> None:
    rounds = [_round_state(0, {"V0": ["t1"]}, {"V0": {"t1": [1, 2]}})]
    run_dir = _write_run(tmp_path, rounds)
    (run_dir / "pool_report.md").write_text("# done\n", encoding="utf-8")

    with pytest.raises(ResumeError) as excinfo:
        load_resume_state(run_dir)
    assert "pool_report.md" in str(excinfo.value)


def test_allow_finished_loads_a_completed_run(tmp_path: Path) -> None:
    """--decomp-pool-from reads a finished run's frozen pool, it never resumes it.

    Without this escape hatch the B1/B2 arms cannot load s1k8b103 at all: every
    completed evolution run has written pool_report.md by definition.
    """
    rounds = [_round_state(0, {"V0": ["t1"]}, {"V0": {"t1": [1, 2]}})]
    run_dir = _write_run(tmp_path, rounds)
    (run_dir / "pool_report.md").write_text("# done\n", encoding="utf-8")

    state = load_resume_state(run_dir, allow_finished=True)
    assert len(state.variants) == 1

    # and the default is unchanged for everyone else
    with pytest.raises(ResumeError):
        load_resume_state(run_dir, allow_finished=False)


def test_resume_refuses_run_with_no_settled_round(tmp_path: Path) -> None:
    run_dir = tmp_path / "empty"
    run_dir.mkdir()
    _write_lock(run_dir)
    with pytest.raises(ResumeError) as excinfo:
        load_resume_state(run_dir)
    assert "no settled round" in str(excinfo.value)


# ---------------------------------------------------------------------------
# 4. lock mismatch -> hard reject + itemized diff
# ---------------------------------------------------------------------------


def test_lock_blocking_diffs_flags_hyperparam_mismatch(tmp_path: Path) -> None:
    existing = _write_lock(tmp_path / "run", candidate_mode="paper", K=8, P=3)
    # Same family (h0/models/dataset), but a different ablation hyperparam.
    new = ExperimentLock(
        experiment_id="run",
        created_at="2026-07-28T10:10:10+00:00",
        hyperparams=Hyperparams(candidate_mode="paper", K=4, P=5),
        models=existing.models,
    )
    blocking = lock_blocking_diffs(new, existing)
    assert blocking, "a K/P change must block resume"
    joined = "\n".join(blocking)
    assert "hyperparams.K:" in joined
    assert "hyperparams.P:" in joined
    # created_at differs too, but must never be a blocking diff.
    assert not any(entry.startswith("created_at") for entry in blocking)


def test_lock_blocking_diffs_ignores_timestamp_only(tmp_path: Path) -> None:
    existing = _write_lock(tmp_path / "run", candidate_mode="paper", K=8)
    new = ExperimentLock(
        experiment_id="run",
        created_at="2099-01-01T00:00:00+00:00",  # different wall clock only
        hyperparams=existing.hyperparams,
        models=existing.models,
    )
    assert lock_blocking_diffs(new, existing) == []


def test_lock_blocking_diffs_flags_taint_provenance(tmp_path: Path) -> None:
    # Taints like --force-gate / --ship-policy live in provenance_warnings (not
    # Hyperparams fields), so flipping one on resume must still block.
    existing = _write_lock(tmp_path / "run", candidate_mode="paper")
    new = ExperimentLock(
        experiment_id="run",
        created_at=existing.created_at,
        hyperparams=existing.hyperparams,
        models=existing.models,
        provenance_warnings=("force_gate=on ENABLED (plumbing probe)",),
    )
    blocking = lock_blocking_diffs(new, existing)
    assert any(entry.startswith("provenance_warnings") for entry in blocking)


def test_lock_blocking_diffs_flags_model_swap(tmp_path: Path) -> None:
    existing = _write_lock(tmp_path / "run", candidate_mode="paper")
    new = ExperimentLock(
        experiment_id="run",
        created_at=existing.created_at,
        hyperparams=existing.hyperparams,
        models=ModelSpec(task_agent_model="OTHER", meta_agent_model="meta-model", provider="prov", api_base="base"),
    )
    blocking = lock_blocking_diffs(new, existing)
    assert any(entry.startswith("models.task_agent_model:") for entry in blocking)


# ---------------------------------------------------------------------------
# 5. --resume + --clean -> error
# ---------------------------------------------------------------------------


def test_plan_resume_rejects_clean(tmp_path: Path) -> None:
    run_dir = _write_run(tmp_path, [_round_state(0, {"V0": ["t1"]}, {"V0": {"t1": [1, 2]}})])
    with pytest.raises(SystemExit) as excinfo:
        plan_resume(_Args(resume=str(run_dir), clean=True), tmp_path)
    assert "mutually exclusive" in str(excinfo.value)


def test_plan_resume_derives_run_tag(tmp_path: Path) -> None:
    run_dir = _write_run(tmp_path, [_round_state(0, {"V0": ["t1"]}, {"V0": {"t1": [1, 2]}})], tag="a1big9")
    args = _Args(resume=str(run_dir))
    resolved = plan_resume(args, tmp_path)
    assert resolved == run_dir.resolve()
    assert args.run_tag == "a1big9"  # derived from the dir


def test_plan_resume_by_tag_under_runs_dir(tmp_path: Path) -> None:
    run_dir = _write_run(tmp_path, [_round_state(0, {"V0": ["t1"]}, {"V0": {"t1": [1, 2]}})], tag="bytag")
    args = _Args(resume="bytag")
    resolved = plan_resume(args, tmp_path)
    assert resolved == run_dir.resolve()


def test_plan_resume_conflicting_run_tag(tmp_path: Path) -> None:
    run_dir = _write_run(tmp_path, [_round_state(0, {"V0": ["t1"]}, {"V0": {"t1": [1, 2]}})], tag="real")
    with pytest.raises(SystemExit) as excinfo:
        plan_resume(_Args(resume=str(run_dir), run_tag="different"), tmp_path)
    assert "disagrees" in str(excinfo.value)


def test_plan_resume_missing_target(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as excinfo:
        plan_resume(_Args(resume="nope"), tmp_path)
    assert "not found" in str(excinfo.value)


# ---------------------------------------------------------------------------
# 6. fork children (V0 + V1) rebuild
# ---------------------------------------------------------------------------


def test_resume_rebuilds_fork_children(tmp_path: Path) -> None:
    rounds = [
        _round_state(0, {"V0": ["t1", "t2", "t3"]}, {"V0": {"t1": [1, 2], "t2": [0, 2], "t3": [0, 2]}}, idle=0),
        _round_state(
            1,
            {"V0": ["t1", "t2"], "V1": ["t3"]},
            {"V0": {"t1": [1, 2], "t2": [1, 2]}, "V1": {"t3": [2, 2]}},
            decisions={"V0": "fork"},
            forked=["V1"],
            selected={"V0": "C-R1-01"},
            idle=0,
        ),
    ]
    run_dir = _write_run(tmp_path, rounds)

    state = load_resume_state(run_dir)

    assert {v.variant_id for v in state.variants} == {"V0", "V1"}
    assert state.next_id == 2
    v0 = state.variant("V0")
    v1 = state.variant("V1")
    assert v0.routed_tasks == ("t1", "t2")
    assert v1.routed_tasks == ("t3",)
    assert v1.created_round == 1
    assert v1.parent_id == "V0"  # single fork that round -> unambiguous
    # Each variant carries its own config snapshot + journal path.
    assert v0.config_path == run_dir / "R1" / "active_pool" / "V0" / "config.yaml"
    assert v1.config_path == run_dir / "R1" / "active_pool" / "V1" / "config.yaml"
    assert state.variant("V1").journal_path == run_dir / "learnings_V1.md"

    pool = rebuild_pool(state, K=8)
    assert set(pool.variants) == {"V0", "V1"}
    assert pool.variants["V1"].config_path == run_dir / "R1" / "active_pool" / "V1" / "config.yaml"
    assert pool.variants["V0"].config_path != pool.variants["V1"].config_path

    ledger = replay_ledger(state, SuccessLedger())
    assert ledger.ever_solved == {"t1", "t2", "t3"}
    assert ledger.cell("V1", "t3").passes == 2


# ---------------------------------------------------------------------------
# 7. no --resume -> zero behaviour difference
# ---------------------------------------------------------------------------


def test_plan_resume_noop_without_flag(tmp_path: Path) -> None:
    args = _Args(resume=None, run_tag="keep")
    assert plan_resume(args, tmp_path) is None
    assert args.run_tag == "keep"  # untouched
    assert args.clean is False


def test_default_arg_parser_resume_is_none() -> None:
    import sys

    _ROOT = Path(__file__).resolve().parents[3]
    if str(_ROOT) not in sys.path:
        sys.path.insert(0, str(_ROOT))
    from recipe.gaia_evolver import run_variant_pool as rvp

    ns = rvp.build_arg_parser().parse_args([])
    assert ns.resume is None
    assert ns.clean is False


# ---------------------------------------------------------------------------
# lineage / next_id edge cases + provenance + glue
# ---------------------------------------------------------------------------


def test_next_id_counts_retired_variants(tmp_path: Path) -> None:
    # V2 is forked in R1 then retired in R2; its id must never be reused.
    rounds = [
        _round_state(0, {"V0": ["t1", "t2"]}, {"V0": {"t1": [1, 2], "t2": [0, 2]}}, idle=0),
        _round_state(
            1,
            {"V0": ["t1"], "V2": ["t2"]},
            {"V0": {"t1": [1, 2]}, "V2": {"t2": [1, 2]}},
            decisions={"V0": "fork"},
            forked=["V2"],
            idle=0,
        ),
        _round_state(
            2,
            {"V0": ["t1", "t2"]},
            {"V0": {"t1": [1, 2], "t2": [1, 2]}},
            retired=["V2"],
            idle=1,
        ),
    ]
    run_dir = _write_run(tmp_path, rounds)
    state = load_resume_state(run_dir)
    assert {v.variant_id for v in state.variants} == {"V0"}
    assert state.next_id == 3  # V2 counted even though retired


def test_stale_config_after_final_round_ship_is_reported(tmp_path: Path) -> None:
    # V0 ships (APPLY) in the last settled round but that round wrote no fresh
    # snapshot (reuse path) -> the loader must fall back to R0 and report it.
    r0 = _round_state(0, {"V0": ["t1"]}, {"V0": {"t1": [0, 2]}}, idle=0)
    r1 = _round_state(
        1,
        {"V0": ["t1"]},
        {"V0": {"t1": [1, 2]}},
        decisions={"V0": "apply"},
        selected={"V0": "C-R1-01"},
        score_source={"V0": "candidate_reuse"},
        idle=0,
    )
    run_dir = _write_run(tmp_path, [r0], snapshots=True)  # only R0 gets a snapshot
    # add R1 state WITHOUT an active_pool snapshot for V0
    (run_dir / "R1").mkdir(parents=True, exist_ok=True)
    (run_dir / "R1" / "pool_state.json").write_text(json.dumps(r1), encoding="utf-8")

    state = load_resume_state(run_dir)
    v0 = state.variant("V0")
    assert v0.config_source == "active_pool_snapshot@R0(stale:R1_ship)"
    assert v0.config_path == run_dir / "R0" / "active_pool" / "V0" / "config.yaml"
    assert any("shipped in R1" in w for w in state.warnings)


def test_annotate_resume_provenance_preserves_comparability(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    existing = _write_lock(run_dir, candidate_mode="paper", K=8)
    prior_sha = existing.sha256()

    entry = annotate_resume_provenance(run_dir, existing, resumed_at_round=2)
    assert entry["resumed_at_round"] == 2
    assert entry["prior_lock_sha256"] == prior_sha
    assert "resumed_at" not in entry  # omitted timestamp

    data = json.loads((run_dir / "experiment.lock.json").read_text(encoding="utf-8"))
    assert data["resume_provenance"] == [entry]
    # The reloaded lock ignores the sidecar key -> identity/comparability intact.
    reloaded = ExperimentLock.load(run_dir)
    assert reloaded.sha256() == prior_sha

    # A second resume accumulates rather than replaces.
    annotate_resume_provenance(run_dir, ExperimentLock.load(run_dir), resumed_at_round=3, resumed_at="2026-07-28T01:00:00Z")
    data2 = json.loads((run_dir / "experiment.lock.json").read_text(encoding="utf-8"))
    assert len(data2["resume_provenance"]) == 2
    assert data2["resume_provenance"][1]["resumed_at"] == "2026-07-28T01:00:00Z"


def test_apply_resume_state_rehydrates_recipe(tmp_path: Path) -> None:
    rounds = [
        _round_state(0, {"V0": ["t1", "t2", "t3"]}, {"V0": {"t1": [1, 2], "t2": [0, 2], "t3": [0, 2]}}, idle=0),
        _round_state(
            1,
            {"V0": ["t1", "t2"], "V1": ["t3"]},
            {"V0": {"t1": [1, 2], "t2": [1, 2]}, "V1": {"t3": [2, 2]}},
            decisions={"V0": "fork"},
            forked=["V1"],
            selected={"V0": "C-R1-01"},
            idle=2,
        ),
    ]
    run_dir = _write_run(tmp_path, rounds)
    state = load_resume_state(run_dir)

    recipe = _recipe_stub()
    apply_resume_state(recipe, state)

    # pool membership + partition
    assert set(recipe.pool.variants) == {"V0", "V1"}
    assert recipe.pool.variants["V0"].routed_tasks == {"t1", "t2"}
    assert recipe.pool.variants["V1"].routed_tasks == {"t3"}
    assert recipe.pool.next_id == 2
    # ledger replayed
    assert recipe.ledger.ever_solved == {"t1", "t2", "t3"}
    assert recipe.ledger.max_last_round() == 1
    # idle restored
    assert recipe.engine._idle == 2
    # trajectories + config-change lineage
    assert recipe._last_traj_dir["V0"] == run_dir / "R1" / "active_pool" / "V0" / "trajectories"
    assert recipe._config_change_rounds["V1"] == [1]
    # pre-resume pool_states preloaded so the final aggregate is complete
    assert [s["round"] for s in recipe.pool_states] == [0, 1]


def test_apply_resume_state_warns_on_stochastic_router(tmp_path: Path) -> None:
    rounds = [_round_state(0, {"V0": ["t1"]}, {"V0": {"t1": [1, 2]}}, idle=0)]
    run_dir = _write_run(tmp_path, rounds)
    state = load_resume_state(run_dir)

    recipe = _recipe_stub()
    recipe.router = Router(epsilon=0.2)  # stochastic arm -> RNG position unrecoverable
    apply_resume_state(recipe, state)
    assert any("RNG" in w for w in getattr(recipe, "_resume_warnings", []))


def test_legacy_mode_ledger_from_selected_candidates(tmp_path: Path) -> None:
    # legacy_single: the ledger is fed by the selected candidate (APPLY->variant),
    # not the settled active pool.
    rounds = [
        _round_state(
            0,
            {"V0": ["t1", "t2"]},
            {"V0": {"t1": [0, 2], "t2": [0, 2]}},  # active pool (NOT the ledger source here)
            decisions={"V0": "apply"},
            selected={"V0": "C-R0-01"},
            per_variant_pass={"V0": {"t1": [1, 2], "t2": [0, 2]}},
            idle=0,
        ),
    ]
    run_dir = _write_run(tmp_path, rounds, candidate_mode="legacy_single")
    state = load_resume_state(run_dir)
    assert state.candidate_mode == "legacy_single"
    ledger = replay_ledger(state, SuccessLedger())
    # t1 solved via the selected candidate; active-pool 0/2 must NOT be the source.
    assert ledger.cell("V0", "t1").passes == 1
    assert ledger.ever_solved == {"t1"}
