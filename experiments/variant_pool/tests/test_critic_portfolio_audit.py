# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""Tests for ``--critic-portfolio-audit`` (batch-4c Item 2).

The official Critic Part-2 portfolio audit + the strategy_concern relay loop,
faithful to ``upstream/feat/aegis:harnessx/aegis/templates/critic.md`` Part 2 +
``templates/planner.md`` L38-45, adapted to OUR bare ``_LLMCritic`` JSON
completion: the Critic MAY emit a ``strategy_concern`` ({bucket, reason} or str)
and a ``decision_type: no_op``; the concern is written to a sink, persisted in
pool_state.json, and relayed verbatim into the next round's Planner prompt +
Evolver TASK.md brief. Off ⇒ byte-identical prompt, no field parsed, no
pool_state field. Fully offline (scripted FIFO provider — never a network call).
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from experiments.variant_pool.critic import CriticContext, DeterministicCritic  # noqa: E402
from experiments.variant_pool.engine import RoundResult  # noqa: E402
from experiments.variant_pool.evidence import EvidenceStore  # noqa: E402
from experiments.variant_pool.manifest import CandidateArtifact, ChangeManifest  # noqa: E402
from recipe.gaia_evolver import run_variant_pool as rvp  # noqa: E402
from recipe.gaia_evolver.variant_pool_meta_agent import VariantPoolMetaAgent  # noqa: E402


class _ScriptedProvider:
    def __init__(self, responses) -> None:
        self.responses = list(responses)
        self.prompts: list[str] = []
        self.calls = 0

    async def complete(self, messages, tools, stream_callback=None):  # noqa: ANN001, ARG002
        self.calls += 1
        self.prompts.append(messages[0].content)
        if not self.responses:
            raise AssertionError("no scripted response left")
        return SimpleNamespace(content=self.responses.pop(0))


def _artifact(root: Path, candidate_id: str = "C-R3-01") -> CandidateArtifact:
    out = root / candidate_id
    out.mkdir(parents=True, exist_ok=True)
    config = out / "config.yaml"
    config.write_text("processors: []\n", encoding="utf-8")
    manifest = ChangeManifest.model_validate(
        {
            "candidate_id": candidate_id,
            "bucket": ["prompt"],
            "file_changes": [{"path": "p.md", "action": "modify", "diff_summary": "x"}],
            "predicted_impact": {"tasks_will_unlock": ["t-fail"]},
            "target_variant": "V0",
        }
    )
    return CandidateArtifact(config_path=config, manifest=manifest, target_variant="V0")


def _context() -> CriticContext:
    return CriticContext(round_idx=3, target_variant="V0", digests=(), regressions=(), failure_buckets=())


def _critic(root: Path, provider, *, portfolio_audit=False, sink=None, reputation_md="") -> rvp._LLMCritic:
    return rvp._LLMCritic(
        provider=provider,
        fallback=DeterministicCritic(EvidenceStore(root / "ev")),
        portfolio_audit=portfolio_audit,
        concern_sink=sink,
        reputation_md=reputation_md,
    )


def _verdict(*, extra: dict | None = None) -> str:
    obj = {
        "ranked_candidate_ids": ["C-R3-01"],
        "verdicts": [{"candidate_id": "C-R3-01", "rank": 1, "reasons": ["ok"]}],
        "rejections": [],
        "revision_requests": [],
        "no_op": False,
        "no_op_reasons": [],
    }
    if extra:
        obj.update(extra)
    return json.dumps(obj)


def _run(critic, candidates):
    return asyncio.run(critic.review(context=_context(), candidates=candidates))


# ---------------------------------------------------------------------------
# 1. prompt: Part-2 contract present on / absent off + scoreboard dependency
# ---------------------------------------------------------------------------
def test_part2_contract_present_when_on_absent_when_off(tmp_path: Path) -> None:
    on = _critic(tmp_path, None, portfolio_audit=True, reputation_md="MY-REP")
    prompt_on = on._build_prompt("SUMMARY", truncation=(), retry_error=None)
    assert "Part 2 — Portfolio audit" in prompt_on
    assert '"strategy_concern"' in prompt_on
    assert "decision_type" in prompt_on
    # with a scoreboard injected, the audit references it (not the unavailable note).
    assert "table above" in prompt_on and "unavailable" not in prompt_on

    off = _critic(tmp_path, None, portfolio_audit=False)
    prompt_off = off._build_prompt("SUMMARY", truncation=(), retry_error=None)
    assert "Part 2 — Portfolio audit" not in prompt_off


def test_scoreboard_dependency_note_when_reputation_absent(tmp_path: Path) -> None:
    on = _critic(tmp_path, None, portfolio_audit=True, reputation_md="")
    prompt = on._build_prompt("SUMMARY", truncation=(), retry_error=None)
    assert "--bucket-reputation" in prompt and "unavailable" in prompt


# ---------------------------------------------------------------------------
# 2. strategy_concern parsed + written to the sink
# ---------------------------------------------------------------------------
def test_strategy_concern_parsed_and_written_to_sink(tmp_path: Path) -> None:
    sink: dict = {}
    concern = {"bucket": "config", "reason": "config shipped R1+R2, hit_rate 0.2, tasks a/b unflipped"}
    provider = _ScriptedProvider([_verdict(extra={"strategy_concern": concern})])
    critic = _critic(tmp_path, provider, portfolio_audit=True, sink=sink)
    review = _run(critic, (_artifact(tmp_path),))
    assert sink["strategy_concern"] == concern
    # also surfaced in the review's strategy_concerns for visibility.
    assert any("strategy_concern:" in n for n in review.strategy_concerns)
    assert "C-R3-01" in review.ranked_candidate_ids  # a concern does NOT no_op by itself


def test_bare_string_strategy_concern_is_accepted(tmp_path: Path) -> None:
    sink: dict = {}
    provider = _ScriptedProvider([_verdict(extra={"strategy_concern": "prompt bucket is saturated"})])
    critic = _critic(tmp_path, provider, portfolio_audit=True, sink=sink)
    _run(critic, (_artifact(tmp_path),))
    assert sink["strategy_concern"] == "prompt bucket is saturated"


def test_parse_strategy_concern_shapes() -> None:
    p = rvp._LLMCritic._parse_strategy_concern
    assert p({"bucket": "config", "reason": "r"}) == {"bucket": "config", "reason": "r"}
    assert p({"reason": "r"}) == {"reason": "r"}
    assert p("plain") == "plain"
    assert p({}) is None
    assert p("") is None
    assert p(None) is None


# ---------------------------------------------------------------------------
# 3. decision_type: no_op ships nothing (reuses the existing no_op)
# ---------------------------------------------------------------------------
def test_no_op_decision_ships_nothing(tmp_path: Path) -> None:
    provider = _ScriptedProvider([_verdict(extra={"decision_type": "no_op"})])
    critic = _critic(tmp_path, provider, portfolio_audit=True, sink={})
    review = _run(critic, (_artifact(tmp_path),))
    assert review.no_op is True
    assert review.ranked_candidate_ids == ()
    assert any("decision_type=no_op" in r for r in review.no_op_reasons)


def test_no_op_still_records_a_concern_for_next_round(tmp_path: Path) -> None:
    # A no_op round can still carry a strategy_concern forward.
    sink: dict = {}
    provider = _ScriptedProvider(
        [_verdict(extra={"decision_type": "no_op", "strategy_concern": {"bucket": "tools", "reason": "r"}})]
    )
    critic = _critic(tmp_path, provider, portfolio_audit=True, sink=sink)
    review = _run(critic, (_artifact(tmp_path),))
    assert review.no_op is True
    assert sink["strategy_concern"] == {"bucket": "tools", "reason": "r"}


# ---------------------------------------------------------------------------
# 4. off ⇒ byte-identical: the two new fields are IGNORED, sink untouched
# ---------------------------------------------------------------------------
def test_off_ignores_new_fields(tmp_path: Path) -> None:
    sink: dict = {}
    provider = _ScriptedProvider(
        [_verdict(extra={"strategy_concern": {"bucket": "config", "reason": "r"}, "decision_type": "no_op"})]
    )
    critic = _critic(tmp_path, provider, portfolio_audit=False, sink=sink)
    review = _run(critic, (_artifact(tmp_path),))
    # decision_type ignored ⇒ NOT a no_op; strategy_concern ignored ⇒ sink empty.
    assert review.no_op is False
    assert "C-R3-01" in review.ranked_candidate_ids
    assert sink == {}


# ---------------------------------------------------------------------------
# 5. relay render → (md, flagged buckets) for IV-11
# ---------------------------------------------------------------------------
def test_relay_render_structured_and_bare() -> None:
    md, flagged = rvp._render_strategy_concern_relay({"bucket": "config", "reason": "flat"})
    assert flagged == {"config"}
    assert "strategy_concern" in md and "flat" in md
    # bare string ⇒ no bucket ⇒ IV-11 no-ops.
    md2, flagged2 = rvp._render_strategy_concern_relay("just a note")
    assert flagged2 == set() and "just a note" in md2
    # nothing ⇒ empty.
    assert rvp._render_strategy_concern_relay(None) == ("", set())


# ---------------------------------------------------------------------------
# 6. relay injection: Planner prompt + Evolver TASK.md brief
# ---------------------------------------------------------------------------
def test_planner_prompt_relay_on_off() -> None:
    on = rvp._LLMPlanner(
        provider=None, k_t=1, fallback=rvp._DeterministicPlanner(1), strategy_concern_md="MY-CONCERN"
    )
    prompt_on = on._build_prompt("S", truncation=(), retry_error=None)
    assert "MY-CONCERN" in prompt_on and "Prior Critic strategy_concern" in prompt_on

    off = rvp._LLMPlanner(provider=None, k_t=1, fallback=rvp._DeterministicPlanner(1))
    assert "Prior Critic strategy_concern" not in off._build_prompt("S", truncation=(), retry_error=None)


def test_planner_brief_injection_is_byte_stable_when_empty() -> None:
    base = {"a": 1}
    assert rvp._planner_brief_with_strategy_concern(base, "") == base
    with_c = rvp._planner_brief_with_strategy_concern(base, "RELAY")
    assert with_c["strategy_concern"] == "RELAY"


def test_evolver_task_md_lifts_concern_to_top_section() -> None:
    contract = {
        "suggested_candidate_id": "C-1",
        "target_variant": "V0",
        "planner_brief": {"strategy_concern": "RELAY-TEXT", "manifest_mode": "repo"},
    }
    out = VariantPoolMetaAgent._render_candidate_contract(contract)
    assert "### Prior Critic strategy_concern (read first)" in out
    assert "RELAY-TEXT" in out
    # lifted out of the JSON contract (not left as a JSON key).
    assert '"strategy_concern"' not in out

    # absent ⇒ no section (byte-stable).
    contract2 = {"suggested_candidate_id": "C-1", "target_variant": "V0", "planner_brief": {"manifest_mode": "repo"}}
    assert "Prior Critic strategy_concern" not in VariantPoolMetaAgent._render_candidate_contract(contract2)


# ---------------------------------------------------------------------------
# 7. pool_state persist + carry (flag-gated)
# ---------------------------------------------------------------------------
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


def test_pool_state_persists_and_carries_when_on(tmp_path) -> None:
    recipe = _make_recipe(tmp_path / "on", critic_portfolio_audit=True)
    try:
        concern = {"bucket": "config", "reason": "flat hit rate R1+R2"}
        recipe._round_strategy_concern_sink = {"strategy_concern": concern}
        recipe._dump_round(RoundResult(round_idx=1, variant_count=1), 1)
        state = recipe.pool_states[-1]
        assert state["strategy_concern"] == concern
        # carried forward so next round relays it.
        assert recipe._strategy_concern_carry == concern
    finally:
        recipe.close()


def test_pool_state_field_absent_when_off(tmp_path) -> None:
    recipe = _make_recipe(tmp_path / "off")  # default: flag off
    try:
        recipe._round_strategy_concern_sink = {"strategy_concern": {"bucket": "config", "reason": "r"}}
        recipe._dump_round(RoundResult(round_idx=1, variant_count=1), 1)
        state = recipe.pool_states[-1]
        assert "strategy_concern" not in state
        assert recipe._strategy_concern_carry is None
    finally:
        recipe.close()


# ---------------------------------------------------------------------------
# 8. flag / provenance
# ---------------------------------------------------------------------------
def test_flag_defaults_off() -> None:
    parser = rvp.build_arg_parser()
    assert parser.parse_args([]).critic_portfolio_audit is False
    assert parser.parse_args(["--critic-portfolio-audit"]).critic_portfolio_audit is True


def test_provenance_none_when_off() -> None:
    assert rvp._critic_portfolio_audit_provenance(False) is None
    warn = rvp._critic_portfolio_audit_provenance(True)
    assert warn is not None
    assert "critic_portfolio_audit=on" in warn
    assert "byte-for-byte" in warn
