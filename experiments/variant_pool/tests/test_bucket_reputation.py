# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""Tests for ``--bucket-reputation`` (batch-4b Item 1).

Two layers: (a) the ported data module ``experiments.variant_pool.reputation``
(Reputation moving average + unknown-bucket boost + deque window; ShipRecord
hit_rate; Scoreboard idempotent add + per-bucket rollup); (b) the recipe wiring
— flip computation (a predicted task unsolved in the previous settled round and
solved this round), the rendered table, the Planner/Critic prompt injection
on/off pins, and the flag-gated pool_state.json fields. Fully offline.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from recipe.gaia_evolver import run_variant_pool as rvp  # noqa: E402
from experiments.variant_pool.critic import DeterministicCritic  # noqa: E402
from experiments.variant_pool.engine import RoundResult  # noqa: E402
from experiments.variant_pool.evidence import EvidenceStore  # noqa: E402
from experiments.variant_pool.gate import Decision  # noqa: E402
from experiments.variant_pool.manifest import ChangeManifest  # noqa: E402
from experiments.variant_pool.reputation import (  # noqa: E402
    BUCKETS,
    Reputation,
    Scoreboard,
    ShipRecord,
)


# ===========================================================================
# Layer (a): the ported data module.
# ===========================================================================
def test_unknown_bucket_gets_boost() -> None:
    rep = Reputation()
    assert rep.score("prompt") == 0.7  # never tried ⇒ _UNKNOWN_BOOST
    assert rep.score("does-not-exist") == 0.7


def test_reputation_moving_average() -> None:
    rep = Reputation()
    rep.record("tools", True)
    rep.record("tools", False)
    assert rep.score("tools") == 0.5


def test_deque_window_is_five() -> None:
    rep = Reputation(window=5)
    # [T,T,T,F,F] ⇒ 3/5
    for hit in (True, True, True, False, False):
        rep.record("tools", hit)
    assert rep.score("tools") == 0.6
    # A sixth record evicts the OLDEST (a True) ⇒ [T,T,F,F,F] ⇒ 2/5.
    rep.record("tools", False)
    assert rep.score("tools") == 0.4


def test_downweight_all_drops_a_true() -> None:
    rep = Reputation()
    rep.record("tools", True)
    rep.record("tools", True)
    assert rep.score("tools") == 1.0
    rep.downweight_all()
    assert rep.score("tools") < 1.0


def test_reputation_roundtrip() -> None:
    rep = Reputation()
    rep.record("tools", True)
    rep.record("config", False)
    back = Reputation.from_dict(rep.to_dict())
    assert back.score("tools") == 1.0
    assert back.score("config") == 0.0
    assert set(rep.to_dict()) == set(BUCKETS)


def test_ship_record_hit_rate() -> None:
    rec = ShipRecord(
        cid="c", round=1, bucket="tools",
        predicted_tasks=("a", "b", "c", "d"), flipped_in_ship_round=("a", "b", "c"),
    )
    assert rec.hit_rate() == 0.75
    empty = ShipRecord(cid="c2", round=1, bucket="tools", predicted_tasks=(), flipped_in_ship_round=())
    assert empty.hit_rate() == 0.0


def test_scoreboard_add_ship_idempotent_and_rollup() -> None:
    sb = Scoreboard()
    sb.add_ship(ShipRecord(cid="c", round=1, bucket="tools", predicted_tasks=("a", "b"), flipped_in_ship_round=("a",)))
    # Same cid ⇒ replaces (idempotent on re-run), not appended.
    sb.add_ship(ShipRecord(cid="c", round=1, bucket="tools", predicted_tasks=("a", "b", "x"), flipped_in_ship_round=("a", "x")))
    assert len(sb.ships) == 1
    roll = sb.to_dict()["by_bucket"]["tools"]
    assert roll["ships"] == 1 and roll["predicted"] == 3 and roll["flipped"] == 2
    assert roll["hit_rate"] == round(2 / 3, 4)


# ===========================================================================
# Layer (b): recipe wiring.
# ===========================================================================
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
    tmp_path.mkdir(parents=True, exist_ok=True)
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


def _manifest(candidate_id="C-R1-01", *, bucket=("tools",), unlock=(), stabilize=()) -> ChangeManifest:
    return ChangeManifest.model_validate(
        {
            "candidate_id": candidate_id,
            "bucket": list(bucket),
            "file_changes": [{"path": "p.txt", "action": "modify", "diff_summary": "x"}],
            "predicted_impact": {
                "tasks_will_unlock": list(unlock),
                "tasks_will_stabilize": list(stabilize),
            },
            "target_variant": "V0",
        }
    )


def test_ship_bucket_takes_first_of_the_list() -> None:
    assert rvp.VariantPoolRecipe._ship_bucket(_manifest(bucket=("tools", "prompt"))) == "tools"
    assert rvp.VariantPoolRecipe._ship_bucket(_manifest(bucket=())) == "unknown"


def test_flip_is_unsolved_prev_and_solved_now(tmp_path) -> None:
    recipe = _make_recipe(tmp_path)
    try:
        # Round 0: t1 unsolved, t2 solved. Round 1: both solved (t1 flips).
        recipe.ledger.record("Vx", "t1", 0, 2, 0)
        recipe.ledger.record("Vx", "t2", 2, 2, 0)
        recipe.ledger.record("Vx", "t1", 2, 2, 1)
        recipe.ledger.record("Vx", "t2", 2, 2, 1)
        manifest = _manifest(bucket=("tools",), unlock=("t1", "t2"))
        recipe._round_candidates = {"C-R1-01": SimpleNamespace(manifest=manifest)}
        result = SimpleNamespace(
            selected_candidate_ids={"Vx": "C-R1-01"},
            decisions={"Vx": Decision.APPLY},
        )
        recipe._update_reputation(result, 1)

        assert len(recipe._scoreboard.ships) == 1
        ship = recipe._scoreboard.ships[0]
        assert ship.predicted_tasks == ("t1", "t2")
        assert ship.flipped_in_ship_round == ("t1",)  # t2 was already solved in R0
        assert ship.hit_rate() == 0.5
        # hit_rate >= 0.5 ⇒ a True reputation bit for the tools bucket.
        assert recipe._reputation.score("tools") == 1.0
    finally:
        recipe.close()


def test_no_flip_records_a_miss(tmp_path) -> None:
    recipe = _make_recipe(tmp_path)
    try:
        # t3 attempted but never solved in either round ⇒ no flip.
        recipe.ledger.record("Vx", "t3", 0, 2, 0)
        recipe.ledger.record("Vx", "t3", 0, 2, 1)
        manifest = _manifest(bucket=("config",), unlock=("t3",))
        recipe._round_candidates = {"C-R1-01": SimpleNamespace(manifest=manifest)}
        result = SimpleNamespace(
            selected_candidate_ids={"Vx": "C-R1-01"},
            decisions={"Vx": Decision.APPLY},
        )
        recipe._update_reputation(result, 1)
        ship = recipe._scoreboard.ships[0]
        assert ship.flipped_in_ship_round == ()
        assert ship.hit_rate() == 0.0
        assert recipe._reputation.score("config") == 0.0  # a False bit
    finally:
        recipe.close()


def test_rejected_candidate_is_not_scored(tmp_path) -> None:
    recipe = _make_recipe(tmp_path)
    try:
        manifest = _manifest(unlock=("t1",))
        recipe._round_candidates = {"C-R1-01": SimpleNamespace(manifest=manifest)}
        result = SimpleNamespace(
            selected_candidate_ids={"Vx": "C-R1-01"},
            decisions={"Vx": Decision.REJECT},  # not APPLY/FORK
        )
        recipe._update_reputation(result, 1)
        assert recipe._scoreboard.ships == []
    finally:
        recipe.close()


def test_render_table_shape_and_values(tmp_path) -> None:
    recipe = _make_recipe(tmp_path)
    try:
        recipe._reputation.record("tools", True)
        recipe._scoreboard.add_ship(
            ShipRecord(cid="C1", round=1, bucket="tools", predicted_tasks=("t1", "t2"), flipped_in_ship_round=("t1",))
        )
        table = recipe._render_reputation_table()
        assert "| bucket | reputation | ships | hit_rate |" in table
        # tools: reputation 1.00, ships 1, hit_rate 1/2 = 0.50.
        assert "| tools | 1.00 | 1 | 0.50 |" in table
        # An untried bucket shows the 0.70 boost, 0 ships, 0.00 hit_rate.
        assert "| prompt | 0.70 | 0 | 0.00 |" in table
    finally:
        recipe.close()


def test_planner_prompt_injection_on_off(tmp_path) -> None:
    on = rvp._LLMPlanner(
        provider=None, k_t=1, fallback=rvp._DeterministicPlanner(1), reputation_md="MY-REP-TABLE"
    )
    prompt_on = on._build_prompt("SUMMARY", truncation=(), retry_error=None)
    assert "MY-REP-TABLE" in prompt_on
    assert "Bucket reputation & ship scoreboard (read before planning)" in prompt_on

    off = rvp._LLMPlanner(provider=None, k_t=1, fallback=rvp._DeterministicPlanner(1))
    prompt_off = off._build_prompt("SUMMARY", truncation=(), retry_error=None)
    assert "Bucket reputation" not in prompt_off


def test_critic_prompt_injection_on_off(tmp_path) -> None:
    fallback = DeterministicCritic(EvidenceStore(tmp_path / "ev"))
    on = rvp._LLMCritic(provider=None, fallback=fallback, reputation_md="MY-REP-TABLE")
    prompt_on = on._build_prompt("SUMMARY", truncation=(), retry_error=None)
    assert "MY-REP-TABLE" in prompt_on
    assert "Bucket reputation & ship scoreboard (read before reviewing)" in prompt_on

    off = rvp._LLMCritic(provider=None, fallback=fallback)
    prompt_off = off._build_prompt("SUMMARY", truncation=(), retry_error=None)
    assert "Bucket reputation" not in prompt_off


def test_pool_state_fields_flag_gated(tmp_path) -> None:
    # ON: the two fields are written into pool_state.json.
    on = _make_recipe(tmp_path / "on", bucket_reputation=True)
    try:
        on._dump_round(RoundResult(round_idx=1, variant_count=1), 1)
        state = on.pool_states[-1]
        assert "bucket_reputation" in state
        assert "ship_scoreboard" in state
    finally:
        on.close()

    # OFF (default): neither field is present — the byte-identical pin.
    off = _make_recipe(tmp_path / "off")
    try:
        off._dump_round(RoundResult(round_idx=1, variant_count=1), 1)
        state = off.pool_states[-1]
        assert "bucket_reputation" not in state
        assert "ship_scoreboard" not in state
    finally:
        off.close()
