# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""Offline unit tests for the C2 recipe ``recipe.gaia_evolver.run_variant_pool``.

No network, no real rollouts, no ``meta_agent`` model calls: ``_run_task_pass_k``
and ``meta_agent.evolve`` are replaced with deterministic stubs (SPEC §8.2/§8.3),
and the two repo-touching seams (``_make_journal`` / ``_prepare_round_config``)
are monkeypatched so no ``HarnessConfig`` is loaded. What is exercised is the
wiring the recipe adds on top of the C1 engine:

* ``--pool-k 1`` reproduces a single lineage and never forks (SPEC §8.1);
* the ``evaluate`` callback turns a merged record into ``(n_pass, n_att)``;
* the ``evolve`` callback returns ``None`` on the meta-agent's byte-identical
  no-op, and carries ``target_variant`` on a real candidate.
"""

from __future__ import annotations

import collections
import sys
from pathlib import Path

import pytest

# The recipe lives under ``recipe/``; put the repo root on the path so it and its
# ``experiments.variant_pool`` / ``harnessx`` imports resolve (conftest only adds
# ``experiments/``).
_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from recipe.gaia_evolver import run_variant_pool as rvp  # noqa: E402
from experiments.variant_pool.gate import Decision  # noqa: E402


# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------


class _Task:
    """A GAIA-task stand-in: only ``task_id`` and ``level`` are read."""

    def __init__(self, task_id: str, level: int = 1) -> None:
        self.task_id = task_id
        self.level = level


class _Args:
    """The CLI namespace the recipe reads, with test-friendly defaults."""

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
        self.__dict__.update(overrides)


class FakePassK:
    """Scripted ``_run_task_pass_k``: one merged record per (task, call).

    ``schedule[task_id]`` is a list of ``(n_pass, n_att)`` consumed in call
    order. Under K=1 each task is evaluated once per round, so index == round.
    Never touches ``rollout`` / ``finalize`` — the whole point is to skip the
    real harness.
    """

    def __init__(self, schedule: dict[str, list[tuple[int, int]]]) -> None:
        self.schedule = schedule
        self.calls: dict[str, int] = collections.defaultdict(int)
        self.seen: list[tuple[str, int]] = []

    async def __call__(self, task, *, pass_k, sem, rollout, finalize):
        tid = task.task_id
        idx = self.calls[tid]
        self.calls[tid] += 1
        n_pass, n_att = self.schedule[tid][idx]
        self.seen.append((tid, pass_k))
        return {
            "task_id": tid,
            "level": task.level,
            "n_pass": n_pass,
            "n_att": n_att,
            "passed": n_pass > 0,
            "cost_usd": 0.01,
            "total_tokens": 100,
            "steps": 3,
            "infra_failures": 0,
            "_result": None,  # private key -> stripped by _clean_record
        }


class FakeMeta:
    """Scripted ``meta_agent.evolve``: writes ``output_dir/config.yaml``.

    ``mode="change"`` writes distinct bytes each call (a real edit); ``"noop"``
    copies ``current_config`` verbatim, which is the byte-identical idiom the
    recipe reads as "no candidate this round".
    """

    def __init__(self, mode: str = "change") -> None:
        self.mode = mode
        self.calls: list[dict] = []

    async def evolve(self, *, current_config, trajectories_dir, output_dir, replay_model, replay_max_cost_usd):
        self.calls.append(
            {
                "current_config": Path(current_config),
                "trajectories_dir": Path(trajectories_dir),
                "output_dir": Path(output_dir),
                "replay_max_cost_usd": replay_max_cost_usd,
            }
        )
        out = Path(output_dir) / "config.yaml"
        out.parent.mkdir(parents=True, exist_ok=True)
        if self.mode == "noop":
            out.write_bytes(Path(current_config).read_bytes())
        else:
            out.write_text(f"evolved: {len(self.calls)}\n", encoding="utf-8")
        return out


# ---------------------------------------------------------------------------
# Fixtures / builders
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _patch_seams(monkeypatch):
    """Replace the two repo-touching seams so no HarnessConfig is loaded."""
    monkeypatch.setattr(rvp, "_make_journal", lambda sessions_dir: None)

    class _DummyConfig:
        def to_yaml_file(self, path):  # noqa: ANN001 - no-op reproducibility dump
            return None

    monkeypatch.setattr(rvp, "_prepare_round_config", lambda config_path, journal: _DummyConfig())


def _make_recipe(tmp_path, *, args, tasks, meta, baseline_bytes=b"baseline: true\n"):
    baseline = tmp_path / "baseline.yaml"
    baseline.write_bytes(baseline_bytes)
    return rvp.VariantPoolRecipe(
        args=args,
        tasks=tasks,
        model_config=None,
        meta_agent=meta,
        pipeline_eval=None,
        run_dir=tmp_path / "run",
        baseline_config_path=baseline,
    )


# ===========================================================================
# (a) --pool-k 1 reproduces a single lineage and never forks
# ===========================================================================


def test_pool_k1_single_lineage_never_forks(tmp_path, monkeypatch):
    tasks = [_Task("a"), _Task("b"), _Task("c")]
    # solve one more task each round, then plateau.
    fake_pass = FakePassK(
        {
            "a": [(2, 2), (2, 2), (2, 2), (2, 2)],
            "b": [(0, 2), (2, 2), (2, 2), (2, 2)],
            "c": [(0, 2), (0, 2), (2, 2), (2, 2)],
        }
    )
    monkeypatch.setattr(rvp, "_run_task_pass_k", fake_pass)
    meta = FakeMeta(mode="change")
    recipe = _make_recipe(tmp_path, args=_Args(pool_k=1, num_rounds=4), tasks=tasks, meta=meta)

    try:
        results = recipe.run()
    finally:
        recipe.close()

    # exactly the hand-computed single-lineage decision sequence.
    assert [r.decisions["V0"] for r in results] == [
        Decision.APPLY,
        Decision.APPLY,
        Decision.APPLY,
        Decision.REJECT,
    ]
    # never forked: a K=1 pool cannot mint a V1 (next_id stays 1, one variant).
    assert recipe.pool.next_id == 1
    assert len(recipe.pool) == 1
    assert all(r.forked == [] for r in results)
    assert all(r.retired == [] for r in results)

    # round 0 is the baseline (no meta call); the three later rounds each evolve
    # V0 once, from the config it currently holds.
    assert len(meta.calls) == 3
    assert meta.calls[0]["current_config"] == (tmp_path / "baseline.yaml")
    # and every round evaluated all three tasks (3 tasks x 4 rounds).
    assert len(fake_pass.seen) == 12
    assert {tid for tid, _ in fake_pass.seen} == {"a", "b", "c"}
    # pass_k threaded through to the evaluator.
    assert all(pk == 2 for _, pk in fake_pass.seen)

    # the measured per-round pass rates match the schedule (curve honesty).
    rates = [
        sum(1 for np_, _ in r.per_variant_pass["V0"].values() if np_ >= 1) / 3
        for r in results
    ]
    assert rates == pytest.approx([1 / 3, 2 / 3, 1.0, 1.0])


def test_pool_k1_reconciles_config_forward_on_apply(tmp_path, monkeypatch):
    """After an APPLY the variant holds the evolved config for the next evolve."""
    tasks = [_Task("a"), _Task("b")]
    fake_pass = FakePassK({"a": [(2, 2), (2, 2)], "b": [(0, 2), (2, 2)]})
    monkeypatch.setattr(rvp, "_run_task_pass_k", fake_pass)
    meta = FakeMeta(mode="change")
    recipe = _make_recipe(tmp_path, args=_Args(pool_k=1, num_rounds=2), tasks=tasks, meta=meta)

    try:
        recipe.run()
    finally:
        recipe.close()

    # R1 evolved from the baseline; the applied candidate became V0's config.
    assert meta.calls[0]["current_config"] == (tmp_path / "baseline.yaml")
    evolved = tmp_path / "run" / "R1" / "V0" / "evolve" / "config.yaml"
    assert recipe.pool.variants["V0"].config_path == evolved
    # and the trajectories the next evolve would read advanced to R1's.
    assert recipe._last_traj_dir["V0"] == tmp_path / "run" / "R1" / "V0" / "trajectories"


# ===========================================================================
# (b) the evaluate callback turns a merged record into (n_pass, n_att)
# ===========================================================================


def test_evaluate_converts_merged_records_to_pass_tuples(tmp_path, monkeypatch):
    tasks = [_Task("x"), _Task("y")]
    fake_pass = FakePassK({"x": [(1, 2)], "y": [(2, 2)]})
    monkeypatch.setattr(rvp, "_run_task_pass_k", fake_pass)
    recipe = _make_recipe(tmp_path, args=_Args(num_rounds=1), tasks=tasks, meta=FakeMeta())

    cand = rvp.PoolCandidate(
        candidate_id="C-R0-V0", target_variant="V0", config_path=tmp_path / "baseline.yaml", is_baseline=True
    )
    try:
        outcomes = recipe._evaluate(cand, {"x", "y"}, 0)
    finally:
        recipe.close()

    assert outcomes == {"x": (1, 2), "y": (2, 2)}
    # each task in T_k was evaluated exactly once.
    assert sorted(fake_pass.seen) == [("x", 2), ("y", 2)]


def test_evaluate_covers_only_tk(tmp_path, monkeypatch):
    """Only the tasks in T_k are evaluated (§4.5 narrowed evaluation)."""
    tasks = [_Task("x"), _Task("y"), _Task("z")]
    fake_pass = FakePassK({"x": [(0, 2)], "y": [(2, 2)], "z": [(2, 2)]})
    monkeypatch.setattr(rvp, "_run_task_pass_k", fake_pass)
    recipe = _make_recipe(tmp_path, args=_Args(num_rounds=1), tasks=tasks, meta=FakeMeta())

    cand = rvp.PoolCandidate(
        candidate_id="C-R0-V0", target_variant="V0", config_path=tmp_path / "baseline.yaml", is_baseline=True
    )
    try:
        outcomes = recipe._evaluate(cand, {"x", "y"}, 0)
    finally:
        recipe.close()

    assert set(outcomes) == {"x", "y"}  # z untouched
    assert {tid for tid, _ in fake_pass.seen} == {"x", "y"}


# ===========================================================================
# (c) the evolve callback returns None on a byte-identical no-op
# ===========================================================================


def test_evolve_returns_none_on_noop(tmp_path, monkeypatch):
    tasks = [_Task("a")]
    monkeypatch.setattr(rvp, "_run_task_pass_k", FakePassK({"a": [(0, 2)]}))
    meta = FakeMeta(mode="noop")
    recipe = _make_recipe(tmp_path, args=_Args(num_rounds=2), tasks=tasks, meta=meta)
    # pretend V0 was already evaluated once, so evolve takes the meta branch.
    seed = tmp_path / "run" / "R0" / "V0" / "trajectories"
    seed.mkdir(parents=True, exist_ok=True)
    recipe._last_traj_dir["V0"] = seed

    variant = recipe.pool.variants["V0"]
    try:
        candidate = recipe._evolve(variant, 1)
    finally:
        recipe.close()

    assert candidate is None
    assert recipe._round_candidates["V0"] is None
    assert len(meta.calls) == 1  # the meta-agent was called, and its output was a no-op


def test_evolve_round0_is_a_baseline_candidate_with_no_meta_call(tmp_path, monkeypatch):
    tasks = [_Task("a")]
    monkeypatch.setattr(rvp, "_run_task_pass_k", FakePassK({"a": [(2, 2)]}))
    meta = FakeMeta(mode="change")
    recipe = _make_recipe(tmp_path, args=_Args(num_rounds=1), tasks=tasks, meta=meta)

    variant = recipe.pool.variants["V0"]
    try:
        candidate = recipe._evolve(variant, 0)
    finally:
        recipe.close()

    assert candidate is not None
    assert candidate.is_baseline is True
    assert candidate.config_path == variant.config_path  # adopts its own config
    assert meta.calls == []  # no evolution on the baseline round


# ===========================================================================
# (d) target_variant is carried on the candidate
# ===========================================================================


def test_candidate_carries_target_variant(tmp_path, monkeypatch):
    tasks = [_Task("a")]
    monkeypatch.setattr(rvp, "_run_task_pass_k", FakePassK({"a": [(0, 2)]}))
    meta = FakeMeta(mode="change")
    recipe = _make_recipe(tmp_path, args=_Args(num_rounds=2), tasks=tasks, meta=meta)
    seed = tmp_path / "run" / "R0" / "V0" / "trajectories"
    seed.mkdir(parents=True, exist_ok=True)
    recipe._last_traj_dir["V0"] = seed

    variant = recipe.pool.variants["V0"]
    try:
        evolved = recipe._evolve(variant, 1)
    finally:
        recipe.close()

    assert evolved is not None
    assert evolved.target_variant == "V0"
    assert evolved.is_baseline is False
    assert evolved.candidate_id == "C-R1-V0"
    # config_path points at the freshly evolved YAML, not the parent's.
    assert evolved.config_path == tmp_path / "run" / "R1" / "V0" / "evolve" / "config.yaml"
