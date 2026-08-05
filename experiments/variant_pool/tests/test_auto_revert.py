# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""Tests for ``--auto-revert`` (batch-4b Item 3).

Stage-5 adjudication ported from the tau2-pilot-enabled form of
``upstream/feat/aegis:harnessx/aegis/stages/adjudicate.py``: at round r
settlement the variant APPLY'd in r-1 is scored
(``hit_rate = |predicted ∩ solved_in_r| / |predicted|``) and, when ``< 0.5``, its
config is reverted to the captured pre-ship path for the next round, with an
audit event and (under --bucket-reputation) a reputation False bit. Empty-
predicted ships are skipped; a variant that re-shipped this round supersedes the
r-1 ship. Fully offline.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from recipe.gaia_evolver import run_variant_pool as rvp  # noqa: E402


class _FakeTask:
    def __init__(self, task_id: str) -> None:
        self.task_id = task_id


_FakeTask.level = 1  # type: ignore[attr-defined]


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
        self.evolve_steps = 200
        self.manifest_mode = "repo"
        self.aegis_digester = "deterministic"
        self.aegis_planner = "deterministic"
        self.aegis_critic = "deterministic"
        self.__dict__.update(overrides)


def _make_recipe(tmp_path, **overrides):
    baseline = tmp_path / "baseline.yaml"
    baseline.write_bytes(b"baseline: true\n")
    return rvp.VariantPoolRecipe(
        args=_Args(**overrides),
        tasks=[_FakeTask("a"), _FakeTask("z")],
        model_config=None,
        meta_agent=SimpleNamespace(),
        pipeline_eval=None,
        run_dir=tmp_path / "run",
        baseline_config_path=baseline,
    )


def _prep(recipe, *, current: Path, prior: Path, predicted, ship_round=0):
    recipe.pool.variants["Vr"] = SimpleNamespace(config_path=current)
    recipe._preship_config["Vr"] = (ship_round, str(prior), tuple(predicted), "tools")


def test_below_threshold_reverts_config(tmp_path) -> None:
    recipe = _make_recipe(tmp_path, auto_revert=True)
    try:
        prior = tmp_path / "prior_config.yaml"
        current = tmp_path / "current_config.yaml"
        # Round 1: neither predicted task solved ⇒ hit_rate 0.0 < 0.5 ⇒ revert.
        recipe.ledger.record("Vr", "t1", 0, 2, 1)
        recipe.ledger.record("Vr", "t2", 0, 2, 1)
        _prep(recipe, current=current, prior=prior, predicted=("t1", "t2"))
        recipe._adjudicate_previous_ships(None, 1)
        assert recipe.pool.variants["Vr"].config_path == prior  # reverted
        assert "Vr" not in recipe._preship_config  # adjudicated ⇒ dropped
    finally:
        recipe.close()


def test_at_or_above_threshold_keeps_config(tmp_path) -> None:
    recipe = _make_recipe(tmp_path, auto_revert=True)
    try:
        prior = tmp_path / "prior_config.yaml"
        current = tmp_path / "current_config.yaml"
        # Round 1: both predicted tasks solved ⇒ hit_rate 1.0 ⇒ keep.
        recipe.ledger.record("Vr", "t1", 2, 2, 1)
        recipe.ledger.record("Vr", "t2", 2, 2, 1)
        _prep(recipe, current=current, prior=prior, predicted=("t1", "t2"))
        recipe._adjudicate_previous_ships(None, 1)
        assert recipe.pool.variants["Vr"].config_path == current  # unchanged
        assert "Vr" not in recipe._preship_config
    finally:
        recipe.close()


def test_exactly_half_keeps(tmp_path) -> None:
    recipe = _make_recipe(tmp_path, auto_revert=True)
    try:
        prior = tmp_path / "prior_config.yaml"
        current = tmp_path / "current_config.yaml"
        # 1 of 2 solved ⇒ hit_rate 0.5, NOT < 0.5 ⇒ keep (matches should_revert).
        recipe.ledger.record("Vr", "t1", 2, 2, 1)
        recipe.ledger.record("Vr", "t2", 0, 2, 1)
        _prep(recipe, current=current, prior=prior, predicted=("t1", "t2"))
        recipe._adjudicate_previous_ships(None, 1)
        assert recipe.pool.variants["Vr"].config_path == current
    finally:
        recipe.close()


def test_empty_predicted_is_skipped(tmp_path) -> None:
    recipe = _make_recipe(tmp_path, auto_revert=True)
    try:
        prior = tmp_path / "prior_config.yaml"
        current = tmp_path / "current_config.yaml"
        _prep(recipe, current=current, prior=prior, predicted=())  # nothing predicted
        recipe._adjudicate_previous_ships(None, 1)
        assert recipe.pool.variants["Vr"].config_path == current  # no revert
        assert "Vr" not in recipe._preship_config  # still dropped
    finally:
        recipe.close()


def test_reship_this_round_supersedes_prev(tmp_path) -> None:
    recipe = _make_recipe(tmp_path, auto_revert=True)
    try:
        prior = tmp_path / "prior_config.yaml"
        current = tmp_path / "current_config.yaml"
        recipe.ledger.record("Vr", "t1", 0, 2, 1)
        # Entry shipped in round 1 (this round), so at round 1 settlement prev=0
        # does not match ⇒ not adjudicated now (adjudicated at round 2 instead).
        _prep(recipe, current=current, prior=prior, predicted=("t1",), ship_round=1)
        recipe._adjudicate_previous_ships(None, 1)
        assert recipe.pool.variants["Vr"].config_path == current
        assert "Vr" in recipe._preship_config  # retained for next round
    finally:
        recipe.close()


def test_revert_records_reputation_false_bit_and_audit(tmp_path) -> None:
    recipe = _make_recipe(tmp_path, auto_revert=True, bucket_reputation=True, audit_stream=True)
    try:
        events = []
        recipe._emit_audit = lambda *a, **k: events.append(a)  # type: ignore[assignment]
        prior = tmp_path / "prior_config.yaml"
        current = tmp_path / "current_config.yaml"
        recipe.ledger.record("Vr", "t1", 0, 2, 1)
        _prep(recipe, current=current, prior=prior, predicted=("t1",))
        recipe._adjudicate_previous_ships(None, 1)
        # A False bit went to the tools bucket (score of a single False = 0.0).
        assert recipe._reputation.score("tools") == 0.0
        # An adjudicate/revert audit event was emitted.
        assert any(e[1] == "adjudicate" and e[2] == "revert" for e in events)
    finally:
        recipe.close()


def test_off_default_has_no_capture(tmp_path) -> None:
    # The byte-identical pin: a default recipe never captures a pre-ship config,
    # so adjudication (were it called) has nothing to revert.
    recipe = _make_recipe(tmp_path)
    try:
        assert recipe.auto_revert is False
        assert recipe._preship_config == {}
        recipe._adjudicate_previous_ships(None, 1)  # no-op over empty capture
        assert recipe._preship_config == {}
    finally:
        recipe.close()
