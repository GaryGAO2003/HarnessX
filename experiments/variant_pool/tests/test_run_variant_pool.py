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
import json
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
from experiments.variant_pool.gate import Decision, GateResult  # noqa: E402


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


def _make_recipe(
    tmp_path,
    *,
    args,
    tasks,
    meta,
    baseline_bytes=b"baseline: true\n",
    active_pool_evaluator=None,
    min_fork=(1, 1),
):
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
        active_pool_evaluator=active_pool_evaluator,
        min_fork=min_fork,
    )


# ===========================================================================
# (a) --pool-k 1 reproduces a single lineage and never forks
# ===========================================================================


def test_pool_k1_single_lineage_never_forks(tmp_path, monkeypatch):
    tasks = [_Task("a"), _Task("b"), _Task("c")]
    # solve one more task each round, then plateau.
    fake_pass = FakePassK(
        {
            "a": [(2, 2), (2, 2), (2, 2), (2, 2), (2, 2)],
            "b": [(0, 2), (2, 2), (2, 2), (2, 2), (2, 2)],
            "c": [(0, 2), (0, 2), (2, 2), (2, 2), (2, 2)],
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
    # Candidate measurements are safely reused for the three deployed APPLY
    # rounds. The rejected R3 candidate is not reusable, so the still-active
    # baseline config receives one separate full score (3 extra task calls).
    assert len(fake_pass.seen) == 15
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
    assert recipe._last_traj_dir["V0"] == (
        tmp_path
        / "run"
        / "R1"
        / "V0"
        / "candidate_gate"
        / "C-R1-V0"
        / "trajectories"
    )


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
    assert recipe._round_candidates == {}
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


# ===========================================================================
# (e) candidate gate and settled active-pool measurement are separate
# ===========================================================================


def _forced(decision: Decision, *, improved=(), regressed=()):
    return lambda *args, **kwargs: GateResult(
        passed=decision is not Decision.REJECT,
        failed_stage=None,
        decision=decision,
        archive_reason=f"forced {decision.value}",
        improved=frozenset(improved),
        regressed=frozenset(regressed),
    )


def test_rejected_candidate_cannot_move_final_or_pass_at_1(tmp_path, monkeypatch):
    tasks = [_Task("a")]
    monkeypatch.setattr(rvp, "_run_task_pass_k", FakePassK({"a": [(2, 2)]}))
    active_calls = []

    def active(variant, task_ids, round_idx):
        active_calls.append((variant.variant_id, set(task_ids), round_idx))
        return {"a": (0, 2)}

    recipe = _make_recipe(
        tmp_path,
        args=_Args(num_rounds=1),
        tasks=tasks,
        meta=FakeMeta(),
        active_pool_evaluator=active,
    )
    recipe.engine.gate = _forced(Decision.REJECT)
    try:
        recipe.run()
    finally:
        recipe.close()

    assert active_calls == [("V0", {"a"}, 0)]
    assert recipe.report.final() == 0.0
    assert recipe.report.pass_at_1() == 0.0
    candidate = recipe.report.candidate_diagnostics()
    assert candidate["by_round"][0]["pass_at_2"] == 1.0
    assert candidate["results"][0]["decision"] == "reject"


def test_no_candidate_round_still_scores_the_fixed_full_task_set(tmp_path, monkeypatch):
    tasks = [_Task("a"), _Task("b")]
    monkeypatch.setattr(
        rvp,
        "_run_task_pass_k",
        FakePassK({"a": [(0, 2)], "b": [(0, 2)]}),
    )
    calls = []

    def active(variant, task_ids, round_idx):
        calls.append((variant.variant_id, set(task_ids), round_idx))
        return {task_id: ((2, 2) if task_id == "a" else (0, 2)) for task_id in task_ids}

    recipe = _make_recipe(
        tmp_path,
        args=_Args(num_rounds=2),
        tasks=tasks,
        meta=FakeMeta(mode="noop"),
        active_pool_evaluator=active,
    )
    try:
        recipe.run()
    finally:
        recipe.close()

    assert calls == [("V0", {"a", "b"}, 0), ("V0", {"a", "b"}, 1)]
    diagnostics = recipe.report.round_diagnostics()
    assert diagnostics[1]["no_candidate"] is True
    assert diagnostics[1]["evaluated_tasks"] == diagnostics[1]["evaluated_task_denominator"] == 2
    assert len(recipe.report.results_in(1)) == 2


def test_fork_scores_parent_and_child_under_their_deployed_ids(tmp_path, monkeypatch):
    tasks = [_Task("a"), _Task("b")]
    monkeypatch.setattr(
        rvp,
        "_run_task_pass_k",
        FakePassK(
            {
                "a": [(2, 2), (0, 2)],
                "b": [(0, 2), (2, 2)],
            }
        ),
    )
    calls = []

    def active(variant, task_ids, round_idx):
        calls.append((variant.variant_id, set(task_ids), round_idx))
        return {task_id: (2, 2) for task_id in task_ids}

    recipe = _make_recipe(
        tmp_path,
        args=_Args(pool_k=2, num_rounds=2),
        tasks=tasks,
        meta=FakeMeta(mode="change"),
        active_pool_evaluator=active,
        min_fork=(1, 1),
    )
    try:
        results = recipe.run()
    finally:
        recipe.close()

    assert results[1].decisions["V0"] is Decision.FORK
    assert ("V0", {"a"}, 1) in calls
    assert ("V1", {"b"}, 1) in calls
    carriers = {row.task_id: row.variant_id for row in recipe.report.results_in(1)}
    assert carriers == {"a": "V0", "b": "V1"}


def test_reconcile_apply_then_retire_is_explicit_and_keyerror_free(tmp_path):
    recipe = _make_recipe(
        tmp_path,
        args=_Args(pool_k=2),
        tasks=[_Task("a"), _Task("b")],
        meta=FakeMeta(),
        active_pool_evaluator=lambda variant, tasks, round_idx: {
            task: (0, 2) for task in tasks
        },
    )
    child = recipe.pool.fork("V0", {"b"}, at_round=0)
    candidate_path = tmp_path / "candidate.yaml"
    candidate_path.write_text("changed: true\n", encoding="utf-8")
    recipe._round_candidates = {
        "C-R1-V0": rvp.PoolCandidate("C-R1-V0", "V0", candidate_path),
    }
    traj_dir = tmp_path / "candidate-trajectories"
    traj_dir.mkdir()
    recipe._round_traj_dir = {"C-R1-V0": traj_dir}
    recipe.pool.retire("V0")
    result = RoundResult(
        round_idx=1,
        variant_count=1,
        decisions={"V0": Decision.APPLY},
        selected_candidate_ids={"V0": "C-R1-V0"},
        retired=["V0"],
    )

    try:
        recipe._reconcile(result)
    finally:
        recipe.close()

    assert child.variant_id in recipe.pool.variants
    assert recipe._reconcile_status["V0"] == "applied_then_retired"
    assert "V0" not in recipe._last_traj_dir


def test_variant_recipe_cli_defaults_match_the_paper_run(monkeypatch) -> None:
    monkeypatch.delenv("HARNESSX_PROVIDER_ID", raising=False)
    args = rvp.build_arg_parser().parse_args([])
    assert (args.pass_k, args.num_rounds, args.patience, args.concurrency) == (2, 15, 3, 10)
    assert args.routing_mode == "cluster"
    assert args.provider_id is None  # an unresolved provider is never silently runnable


def test_lock_records_runtime_values_and_resolved_provenance(tmp_path):
    baseline = tmp_path / "baseline.yaml"
    baseline.write_text("harness: frozen\n", encoding="utf-8")
    prompt = tmp_path / "prompt.j2"
    prompt.write_text("You are the deployed GAIA agent.", encoding="utf-8")
    data = tmp_path / "tasks.json"
    data.write_text(
        json.dumps([{"task_id": "a", "Question": "q", "answer": "a", "Level": 2}]),
        encoding="utf-8",
    )

    class _Registry:
        @staticmethod
        def list_names():
            return ["WebFetch", "WebSearch"]

    class _Base:
        tool_registry = _Registry()
        processors = [
            {
                "_target_": "harnessx.processors.context.system_prompt.SystemPromptProcessor",
                "system_builder": {"template_path": str(prompt)},
            }
        ]

    args = _Args(
        pool_k=3,
        pass_k=2,
        num_rounds=7,
        concurrency=6,
        patience=4,
        seed=19,
        provider_id="provider-live",
        api_base=None,
        data_path=str(data),
        routing_mode="cluster",
        routing_window=5,
        retirement_metric="cluster_macro",
    )
    lock = rvp._build_experiment_lock(
        args=args,
        run_tag="runtime-lock",
        baseline_config_path=baseline,
        original_base=_Base(),
    )

    assert lock.git_sha not in ("", "unresolved")
    assert lock.h0.system_prompt_sha256 == rvp.sha256_file(prompt)
    assert lock.models.provider == "provider-live"
    assert lock.models.api_base == "unresolved"
    assert lock.env.seed == 19
    assert lock.hyperparams.candidates_per_round == "one_per_active_variant"
    assert lock.hyperparams.target_strategy == "all_active_variants"
    assert lock.hyperparams.routing_mode == "cluster"
    assert lock.hyperparams.routing_window == 5
    assert lock.hyperparams.retirement_metric == "cluster_macro"
    assert lock.hyperparams.planned_seeds == (0, 1, 2)
    assert "models.api_base unresolved" in lock.provenance_warnings
    rendered = lock.to_json()
    assert '""' not in rendered
    assert "YOUR_PROVIDER_ID" not in rendered


# --- M-17: --ship-policy wiring (arg parsing + byte-safe lock provenance) ----


def test_ship_policy_cli_defaults_to_first_wins_and_validates_choices() -> None:
    parser = rvp.build_arg_parser()
    assert parser.parse_args([]).ship_policy == "first_wins"
    assert parser.parse_args(["--ship-policy", "bucket_disjoint"]).ship_policy == "bucket_disjoint"
    with pytest.raises(SystemExit):
        parser.parse_args(["--ship-policy", "nonsense"])


def _ship_policy_lock_fixture(tmp_path, **overrides):
    baseline = tmp_path / "baseline.yaml"
    baseline.write_text("harness: frozen\n", encoding="utf-8")
    prompt = tmp_path / "prompt.j2"
    prompt.write_text("You are the deployed GAIA agent.", encoding="utf-8")
    data = tmp_path / "tasks.json"
    data.write_text(
        json.dumps([{"task_id": "a", "Question": "q", "answer": "a", "Level": 2}]),
        encoding="utf-8",
    )

    class _Registry:
        @staticmethod
        def list_names():
            return ["WebFetch"]

    class _Base:
        tool_registry = _Registry()
        processors = [
            {
                "_target_": "harnessx.processors.context.system_prompt.SystemPromptProcessor",
                "system_builder": {"template_path": str(prompt)},
            }
        ]

    args = _Args(data_path=str(data), **overrides)
    return args, baseline, _Base()


def test_lock_leaves_a_default_ship_policy_unrecorded(tmp_path) -> None:
    """first_wins (default) emits no ship_policy provenance — the lock of a
    default run stays byte-identical (the force-gate byte-safe pattern)."""
    args, baseline, base = _ship_policy_lock_fixture(tmp_path)
    lock = rvp._build_experiment_lock(
        args=args, run_tag="t", baseline_config_path=baseline, original_base=base
    )
    assert not any("ship_policy" in warning for warning in lock.provenance_warnings)


def test_lock_notes_a_non_default_ship_policy_as_auditable_provenance(tmp_path) -> None:
    """bucket_disjoint records exactly one auditable provenance note (M-17)."""
    args, baseline, base = _ship_policy_lock_fixture(tmp_path, ship_policy="bucket_disjoint")
    lock = rvp._build_experiment_lock(
        args=args, run_tag="t", baseline_config_path=baseline, original_base=base
    )
    notes = [w for w in lock.provenance_warnings if "ship_policy=bucket_disjoint" in w]
    assert len(notes) == 1
    assert "M-17" in notes[0]
    assert "App B.1" in notes[0]


# ===========================================================================
# (d) target eligibility: a fork child with no trajectories is not targeted
# ===========================================================================


def test_paper_target_skips_a_fork_child_without_trajectories(tmp_path):
    """runs/forceprobe2 P2: a freshly forked child that has no settled
    trajectories dir must not be chosen as the paper-mode target, because the
    evolve step cannot service it. The recipe wires
    ``_variants_with_settled_trajectories`` as the selection eligibility.
    """
    args = _Args(candidate_mode="paper", target_strategy="worst_first", pool_k=2)
    recipe = _make_recipe(
        tmp_path,
        args=args,
        tasks=[_Task("a"), _Task("z")],
        meta=FakeMeta("noop"),
    )

    # Fork a child V1 that has never been evaluated -> absent from _last_traj_dir.
    recipe.pool.fork("V0", set(), at_round=1)
    v0_traj = tmp_path / "v0_traj"
    v0_traj.mkdir()
    recipe._last_traj_dir = {"V0": v0_traj}

    # Make V1 the worst_first winner, so eligibility (not the score) is what
    # excludes it.
    recipe.ledger.record("V0", "a", n_pass=2, n_att=2, round_idx=0)  # rollup high
    recipe.ledger.record("V1", "z", n_pass=0, n_att=2, round_idx=0)  # rollup low

    eligible = recipe._variants_with_settled_trajectories()
    assert eligible == {"V0"}  # V1 excluded: no settled trajectories dir on disk

    # Unfiltered selection would starve the round on V1; the wired eligibility
    # deterministically targets V0 instead.
    assert rvp.select_target_variant(recipe.pool, recipe.ledger, strategy="worst_first") == "V1"
    assert (
        rvp.select_target_variant(
            recipe.pool, recipe.ledger, strategy="worst_first", eligible=eligible
        )
        == "V0"
    )


# ===========================================================================
# (d) target eligibility, cont.: a variant WITH a settled trajectories dir but
#     ZERO routed tasks is still not targeted (the routed-tasks half of the
#     tightened predicate; runs/forceprobe2 R2)
# ===========================================================================


def test_paper_target_skips_a_variant_with_trajectories_but_no_routed_tasks(tmp_path):
    """runs/forceprobe2 R2: a variant that still owns a settled trajectories dir
    on disk but currently holds ZERO routed tasks must not be chosen as the
    paper-mode target. The engine skips empty-cluster variants
    (engine.py:262-263), so a round aimed at such a variant evolves nothing —
    the exact R2 starvation the trajectory-only eligibility rule allowed.
    ``_variants_with_settled_trajectories`` therefore also requires routed
    tasks, not merely a trajectories dir.
    """
    args = _Args(candidate_mode="paper", target_strategy="worst_first", pool_k=2)
    recipe = _make_recipe(
        tmp_path,
        args=args,
        tasks=[_Task("a"), _Task("z")],
        meta=FakeMeta("noop"),
    )

    # Fork a child V1 with no inherited tasks -> V1.routed_tasks is empty while
    # V0 keeps both tasks. Unlike test (d), give V1 a real trajectories dir too,
    # so ONLY the routed-tasks half of the predicate can exclude it.
    recipe.pool.fork("V0", set(), at_round=1)
    v0_traj = tmp_path / "v0_traj"
    v0_traj.mkdir()
    v1_traj = tmp_path / "v1_traj"
    v1_traj.mkdir()
    recipe._last_traj_dir = {"V0": v0_traj, "V1": v1_traj}

    # Make V1 the worst_first winner, so eligibility (not the score) is what
    # excludes it.
    recipe.ledger.record("V0", "a", n_pass=2, n_att=2, round_idx=0)  # rollup high
    recipe.ledger.record("V1", "z", n_pass=0, n_att=2, round_idx=0)  # rollup low

    # V1 has a settled trajectories dir on disk (the trajectory-only rule alone
    # would include it) but holds zero routed tasks (the tightened rule does not).
    assert recipe._last_traj_dir["V1"].is_dir()
    assert not recipe.pool.variants["V1"].routed_tasks
    assert recipe.pool.variants["V0"].routed_tasks

    eligible = recipe._variants_with_settled_trajectories()
    assert eligible == {"V0"}  # V1 excluded: zero routed tasks despite its dir

    # Unfiltered selection would starve the round on the empty-cluster V1; the
    # wired eligibility deterministically targets V0 instead.
    assert rvp.select_target_variant(recipe.pool, recipe.ledger, strategy="worst_first") == "V1"
    assert (
        rvp.select_target_variant(
            recipe.pool, recipe.ledger, strategy="worst_first", eligible=eligible
        )
        == "V0"
    )
