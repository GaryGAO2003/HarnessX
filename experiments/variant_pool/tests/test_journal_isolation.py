# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""C3 / W9 — offline tests for per-variant meta-agent journal isolation.

The paper's novelty gate runs *inside* ``meta_agent.evolve``
(``agent.py`` -> ``EvolveValidator(memo_path=self.memo_path)`` -> ``check_novelty``),
reading the memo the ``MetaAgent`` was constructed with. ``MetaAgent`` binds
``memo_path`` at construction time and ``evolve`` takes no per-call override, so
the recipe gives every variant its own meta-agent, cloned from the template and
bound to ``variant.journal_path`` (SPEC §2.1 / §C3 W9). Without this, two
sibling variants share one global ``learnings.md`` and a legitimate retry of a
cluster another sibling reverted trips ``reverted_signature_reused``.

No network, no real rollouts, no model calls: ``meta_agent.evolve`` is a
recording stub. These tests drive ``_evolve`` / ``fork`` / ``_meta_agent_for``
directly, so ``_run_task_pass_k`` and the ``HarnessConfig`` seams are never hit.
"""

from __future__ import annotations

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
from experiments.variant_pool.engine import RoundResult  # noqa: E402


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


class RecordingMeta:
    """Scripted ``meta_agent`` that records the memo each evolve ran against.

    Serves as the clone *template*: :func:`_make_variant_meta_agent` shallow-
    copies it and overrides ``memo_path`` per variant. The shared ``seen`` list
    survives the shallow copy (like the real clones sharing read-only config),
    so every variant's clone appends to the same log. ``evolve`` writes a config
    that always differs from the baseline, so the recipe treats it as a real
    candidate rather than the byte-identical no-op.
    """

    def __init__(self, seen: list[tuple[str, str]]) -> None:
        self.memo_path = None  # overridden on each per-variant clone
        self.seen = seen

    async def evolve(self, *, current_config, trajectories_dir, output_dir, replay_model, replay_max_cost_usd):
        out = Path(output_dir) / "config.yaml"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(f"evolved: {output_dir}\n", encoding="utf-8")
        self.seen.append((str(self.memo_path), str(Path(output_dir))))
        return out


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


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


def _seed_traj(recipe, vid: str) -> Path:
    """Give ``vid`` a prior evaluation so ``_evolve`` takes the meta-agent branch."""
    seed = recipe.run_dir / "R0" / vid / "trajectories"
    seed.mkdir(parents=True, exist_ok=True)
    recipe._last_traj_dir[vid] = seed
    return seed


# ===========================================================================
# (c) K=1 still uses a single memo — equivalent to run.py's single lineage
# ===========================================================================


def test_k1_reuses_the_single_learnings_memo(tmp_path):
    seen: list[tuple[str, str]] = []
    recipe = _make_recipe(tmp_path, args=_Args(pool_k=1, num_rounds=3), tasks=[_Task("a")], meta=RecordingMeta(seen))
    v0 = recipe.pool.variants["V0"]
    _seed_traj(recipe, "V0")

    try:
        recipe._evolve(v0, 1)
        recipe._evolve(v0, 2)
    finally:
        recipe.close()

    # V0's journal is run_dir/learnings.md — the same single memo run.py binds.
    expected = str((tmp_path / "run" / "learnings.md").resolve())
    assert v0.journal_path == tmp_path / "run" / "learnings.md"
    used = {memo for memo, _ in seen}
    assert used == {expected}  # one memo across all rounds, never a per-round file
    assert Path(recipe._meta_agent_for(v0).memo_path) == Path(expected)


# ===========================================================================
# (a) each variant evolves against its OWN memo — siblings never share one
# ===========================================================================


def test_each_variant_evolves_against_its_own_memo(tmp_path):
    seen: list[tuple[str, str]] = []
    recipe = _make_recipe(
        tmp_path, args=_Args(pool_k=8, num_rounds=3), tasks=[_Task("a"), _Task("b")], meta=RecordingMeta(seen)
    )
    v0 = recipe.pool.variants["V0"]
    # V0 must have a journal on disk for the fork to inherit it (SPEC §2.1).
    v0.journal_path.parent.mkdir(parents=True, exist_ok=True)
    v0.journal_path.write_text("# V0 journal\n", encoding="utf-8")

    child = recipe.pool.fork("V0", improved_tasks={"b"}, at_round=1)
    assert child.variant_id == "V1"
    assert child.journal_path.name == "learnings_V1.md"
    assert child.journal_path != v0.journal_path

    _seed_traj(recipe, "V0")
    _seed_traj(recipe, "V1")

    try:
        recipe._evolve(v0, 1)
        recipe._evolve(child, 1)
    finally:
        recipe.close()

    # Two evolves, two DISTINCT memos — not one shared global learnings.md.
    used = {memo for memo, _ in seen}
    assert used == {str(v0.journal_path.resolve()), str(child.journal_path.resolve())}
    assert len(used) == 2
    # Each variant's agent is bound to exactly its own journal.
    assert Path(recipe._meta_agent_for(v0).memo_path) == v0.journal_path.resolve()
    assert Path(recipe._meta_agent_for(child).memo_path) == child.journal_path.resolve()


# ===========================================================================
# (b) a forked child inherits its parent's journal, then diverges
# ===========================================================================


def test_forked_child_inherits_parent_journal_then_diverges(tmp_path):
    seen: list[tuple[str, str]] = []
    recipe = _make_recipe(tmp_path, args=_Args(pool_k=8), tasks=[_Task("a")], meta=RecordingMeta(seen))
    v0 = recipe.pool.variants["V0"]

    parent_history = "# V0 learnings\n\n## Round 1\nreverted: tried lever L on cluster C\n"
    v0.journal_path.parent.mkdir(parents=True, exist_ok=True)
    v0.journal_path.write_text(parent_history, encoding="utf-8")

    child = recipe.pool.fork("V0", improved_tasks={"a"}, at_round=1)

    # Inherit-then-diverge: the child's journal is a copy of the parent's history
    # on a separate file, so its novelty check starts with the parent's context.
    assert child.journal_path.exists()
    assert child.journal_path.read_text(encoding="utf-8") == parent_history
    assert child.journal_path != v0.journal_path

    # The child's meta-agent binds to the inherited journal, not the parent's.
    child_agent = recipe._meta_agent_for(child)
    assert Path(child_agent.memo_path) == child.journal_path.resolve()

    # And an actual evolve of the child records that inherited memo.
    _seed_traj(recipe, "V1")
    try:
        recipe._evolve(child, 2)
    finally:
        recipe.close()
    assert seen[-1][0] == str(child.journal_path.resolve())


# ===========================================================================
# caching + cleanup
# ===========================================================================


def test_meta_agent_cached_per_variant_and_distinct(tmp_path):
    recipe = _make_recipe(tmp_path, args=_Args(pool_k=8), tasks=[_Task("a")], meta=RecordingMeta([]))
    v0 = recipe.pool.variants["V0"]
    v0.journal_path.parent.mkdir(parents=True, exist_ok=True)
    v0.journal_path.write_text("# V0\n", encoding="utf-8")
    child = recipe.pool.fork("V0", improved_tasks={"a"}, at_round=1)

    try:
        # Same variant -> same cached instance; different variant -> different one.
        assert recipe._meta_agent_for(v0) is recipe._meta_agent_for(v0)
        assert recipe._meta_agent_for(child) is recipe._meta_agent_for(child)
        assert recipe._meta_agent_for(v0) is not recipe._meta_agent_for(child)
        # The clones are real per-variant instances, distinct from the template.
        assert recipe._meta_agent_for(v0) is not recipe.meta_agent
    finally:
        recipe.close()


def test_retired_variant_agent_is_pruned(tmp_path):
    recipe = _make_recipe(tmp_path, args=_Args(pool_k=8), tasks=[_Task("a")], meta=RecordingMeta([]))
    v0 = recipe.pool.variants["V0"]
    v0.journal_path.parent.mkdir(parents=True, exist_ok=True)
    v0.journal_path.write_text("# V0\n", encoding="utf-8")
    child = recipe.pool.fork("V0", improved_tasks={"a"}, at_round=1)

    try:
        recipe._meta_agent_for(child)  # populate the cache
        assert "V1" in recipe._meta_agents
        # Reconcile a round that retired V1 -> its cached agent is dropped.
        recipe._reconcile(RoundResult(round_idx=1, variant_count=1, retired=["V1"]))
        assert "V1" not in recipe._meta_agents
    finally:
        recipe.close()
