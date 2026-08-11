# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""P1-1 — adopt the paper's published App B.1 role prompts (--aegis-prompts).

Fully offline. Pins the paper-fidelity contract:

* the paper's App B.1 prompt text is carried VERBATIM (distinctive sentences must
  survive, so drift is caught), with the Planner FULL and the Evolver/Critic
  carrying the paper's own truncation markers;
* the assembled paper-mode prompts = verbatim body + a marked OURS output tail
  (Planner/Critic) or verbatim body + marked OURS truncation bridges (Evolver);
* ``ours`` mode is byte-identical to today's OURS constants;
* the flag defaults to ``paper``; an invalid value is rejected;
* the recipe wires the selected prompt into the LLM Planner/Critic and the
  always-LLM Evolver's candidate contract.

The provenance-string rules (``,prompts=<mode>`` only when a role is llm) are
pinned in ``test_llm_critic.py`` alongside the other composed-adapter cases.
"""

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from recipe.gaia_evolver import run_variant_pool as rvp  # noqa: E402
from experiments.variant_pool.critic import DeterministicCritic  # noqa: E402


# ---------------------------------------------------------------------------
# Minimal offline recipe builder (mirrors test_llm_critic.py's helpers)
# ---------------------------------------------------------------------------


class _FakeTask:
    level = 1

    def __init__(self, task_id: str) -> None:
        self.task_id = task_id
        self.question = ""


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
        self.candidate_mode = "paper"
        self.candidates_per_round = 4
        self.actionability_threshold = 1.0
        self.target_strategy = "worst_first"
        self.patience = 3
        self.planned_seeds = (0, 1, 2)
        self.evolve_steps = 200
        self.manifest_mode = "repo"
        self.aegis_digester = "deterministic"
        self.aegis_planner = "deterministic"
        self.aegis_critic = "deterministic"
        self.__dict__.update(overrides)


def _make_recipe(tmp_path, *, meta=None, **overrides):
    baseline = tmp_path / "baseline.yaml"
    baseline.write_bytes(b"baseline: true\n")
    return rvp.VariantPoolRecipe(
        args=_Args(**overrides),
        tasks=[_FakeTask("a"), _FakeTask("z")],
        model_config=None,
        meta_agent=meta if meta is not None else SimpleNamespace(),
        pipeline_eval=None,
        run_dir=tmp_path / "run",
        baseline_config_path=baseline,
    )


def _meta_with_provider(provider=None):
    provider = provider if provider is not None else object()
    return SimpleNamespace(inner_model=SimpleNamespace(get=lambda key: provider))


# ---------------------------------------------------------------------------
# (1) verbatim fidelity — distinctive sentences + paper's own truncations
# ---------------------------------------------------------------------------

# Distinctive sentences copied verbatim from App B.1 (pp.30-34). If the constant
# ever drifts from the published text, one of these will fail.
_PLANNER_VERBATIM = [
    "Your goal: write a single ` landscape.md ` that synthesises this round ' s evidence",
    "You are the cross-trace synthesis layer -- Digesters produced",
    "Be selective, not exhaustive. Three coherent directions -> list three.",
]
_EVOLVER_VERBATIM = [
    "This role is research, not maintenance.",
    "## Build -> verify -> iterate (mandatory for code candidates)",
    "Level 2 -- round-trip reaches the model: a unit call that returns does not",
]
_CRITIC_VERBATIM = [
    "Your goal has two parts, and both matter.",
    "## Part 1 -- Per-candidate verdict",
    "shipping a bad candidate is worse than nothing.",
]


def test_planner_verbatim_distinctive_sentences_present():
    for sentence in _PLANNER_VERBATIM:
        assert sentence in rvp._PAPER_APP_B1_PLANNER
        # they survive into the assembled paper-mode prompt too
        assert sentence in rvp._PAPER_PLANNER_PROMPT


def test_evolver_and_critic_verbatim_distinctive_sentences_present():
    for sentence in _EVOLVER_VERBATIM:
        assert sentence in rvp._PAPER_APP_B1_EVOLVER
        assert sentence in rvp._PAPER_EVOLVER_GUIDANCE
    for sentence in _CRITIC_VERBATIM:
        assert sentence in rvp._PAPER_APP_B1_CRITIC
        assert sentence in rvp._PAPER_CRITIC_PROMPT


def test_verbatim_constants_carry_the_papers_own_truncation_markers():
    # Planner is published FULL: no author truncation markers.
    assert "truncated ...]" not in rvp._PAPER_APP_B1_PLANNER
    # Evolver ~60%: 3 markers; Critic ~70%: 2 markers (the paper's own).
    assert rvp._PAPER_APP_B1_EVOLVER.count("truncated ...]") == 3
    assert rvp._PAPER_APP_B1_CRITIC.count("truncated ...]") == 2


# ---------------------------------------------------------------------------
# (2) truncation bridges — merged texts fill each gap, none left raw
# ---------------------------------------------------------------------------


def test_evolver_guidance_bridges_all_three_truncations():
    tag = rvp._PAPER_TRUNCATION_BRIDGE_TAG
    assert rvp._PAPER_EVOLVER_GUIDANCE.count(tag) == 3
    # every raw paper marker is gone, replaced by a marked OURS bridge
    assert "truncated ...]" not in rvp._PAPER_EVOLVER_GUIDANCE


def test_critic_prompt_bridges_both_truncations():
    tag = rvp._PAPER_TRUNCATION_BRIDGE_TAG
    assert rvp._PAPER_CRITIC_PROMPT.count(tag) == 2
    assert "truncated ...]" not in rvp._PAPER_CRITIC_PROMPT


def test_bridge_tag_is_the_spec_marker():
    assert rvp._PAPER_TRUNCATION_BRIDGE_TAG == "[...paper truncation — OURS bridge]"


# ---------------------------------------------------------------------------
# (3) runtime-adaptation tails — verbatim body + OURS JSON output contract
# ---------------------------------------------------------------------------


def test_paper_planner_prompt_is_verbatim_body_plus_ours_output_tail():
    assert rvp._PAPER_PLANNER_PROMPT.startswith(rvp._PAPER_APP_B1_PLANNER)
    tail = rvp._PAPER_PLANNER_PROMPT[len(rvp._PAPER_APP_B1_PLANNER):]
    assert "## OUTPUT FORMAT (OURS runtime contract)" in tail
    # the paper's own output wording is declared to take precedence
    assert "take precedence" in tail
    # the tail pins the machine-readable JSON shape our parser needs
    assert '"briefs"' in tail and '"landscape_notes"' in tail


def test_paper_critic_prompt_is_bridged_body_plus_ours_output_tail():
    assert "## OUTPUT FORMAT (OURS runtime contract)" in rvp._PAPER_CRITIC_PROMPT
    assert "take precedence" in rvp._PAPER_CRITIC_PROMPT
    assert '"ranked_candidate_ids"' in rvp._PAPER_CRITIC_PROMPT
    # the verbatim critic body (before any tail) is a prefix of the bridged prompt
    bridged_body = rvp._apply_truncation_bridges(
        rvp._PAPER_APP_B1_CRITIC, rvp._PAPER_CRITIC_BRIDGES
    )
    assert rvp._PAPER_CRITIC_PROMPT.startswith(bridged_body)


# ---------------------------------------------------------------------------
# (4) ours-mode byte-identity with today's constants
# ---------------------------------------------------------------------------


def test_llm_role_prompt_field_defaults_to_ours_constants():
    planner = rvp._LLMPlanner(provider=object(), k_t=1, fallback=object())
    critic = rvp._LLMCritic(provider=object(), fallback=object())
    assert planner.prompt == rvp._LLM_PLANNER_PROMPT
    assert critic.prompt == rvp._LLM_CRITIC_PROMPT


def test_paper_prompts_differ_from_ours_prompts():
    assert rvp._PAPER_PLANNER_PROMPT != rvp._LLM_PLANNER_PROMPT
    assert rvp._PAPER_CRITIC_PROMPT != rvp._LLM_CRITIC_PROMPT


def test_build_prompt_uses_the_configured_prompt():
    ours = rvp._LLMPlanner(provider=object(), k_t=1, fallback=object())
    paper = rvp._LLMPlanner(
        provider=object(), k_t=1, fallback=object(), prompt=rvp._PAPER_PLANNER_PROMPT
    )
    ours_prompt = ours._build_prompt("EVID", round_idx=1, truncation=(), retry_error=None)
    paper_prompt = paper._build_prompt("EVID", round_idx=1, truncation=(), retry_error=None)
    assert ours_prompt.startswith(rvp._LLM_PLANNER_PROMPT)
    assert "cross-trace synthesis layer" not in ours_prompt  # OURS text, not paper
    assert "cross-trace synthesis layer" in paper_prompt  # paper verbatim
    assert "ROUND EVIDENCE:\nEVID" in paper_prompt  # runtime evidence still appended


# ---------------------------------------------------------------------------
# (5) --aegis-prompts flag: default paper, validation, recipe wiring
# ---------------------------------------------------------------------------


def test_flag_constants_and_argparse_default():
    assert rvp.AEGIS_PROMPTS_MODES == ("paper", "ours")
    assert rvp.DEFAULT_AEGIS_PROMPTS == "paper"
    parser = rvp.build_arg_parser()
    ns = parser.parse_args(["--run-tag", "x"])
    assert ns.aegis_prompts == "paper"
    ns_ours = parser.parse_args(["--run-tag", "x", "--aegis-prompts", "ours"])
    assert ns_ours.aegis_prompts == "ours"


def test_recipe_default_prompts_mode_is_paper(tmp_path):
    recipe = _make_recipe(tmp_path)
    try:
        assert recipe.aegis_prompts == "paper"
    finally:
        recipe.close()


def test_invalid_aegis_prompts_mode_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="aegis_prompts"):
        _make_recipe(tmp_path, aegis_prompts="bogus")


def test_paper_mode_wires_paper_prompts_into_llm_roles(tmp_path):
    recipe = _make_recipe(
        tmp_path,
        meta=_meta_with_provider(),
        aegis_prompts="paper",
        aegis_planner="llm",
        aegis_critic="llm",
    )
    try:
        planner = recipe._make_planner()
        critic = recipe._make_critic()
        assert isinstance(planner, rvp._LLMPlanner)
        assert isinstance(critic, rvp._LLMCritic)
        assert planner.prompt == rvp._PAPER_PLANNER_PROMPT
        assert critic.prompt == rvp._PAPER_CRITIC_PROMPT
    finally:
        recipe.close()


def test_ours_mode_wires_ours_prompts_into_llm_roles(tmp_path):
    recipe = _make_recipe(
        tmp_path,
        meta=_meta_with_provider(),
        aegis_prompts="ours",
        aegis_planner="llm",
        aegis_critic="llm",
    )
    try:
        planner = recipe._make_planner()
        critic = recipe._make_critic()
        assert planner.prompt == rvp._LLM_PLANNER_PROMPT
        assert critic.prompt == rvp._LLM_CRITIC_PROMPT
    finally:
        recipe.close()


def test_deterministic_roles_ignore_prompts_mode(tmp_path):
    # With deterministic roles the prompt selection is irrelevant: the roles are
    # the byte-identical deterministic fallbacks regardless of --aegis-prompts.
    recipe = _make_recipe(tmp_path, aegis_prompts="paper")
    try:
        assert isinstance(recipe._make_critic(), DeterministicCritic)
        assert isinstance(recipe._make_planner(), rvp._DeterministicPlanner)
    finally:
        recipe.close()


# ---------------------------------------------------------------------------
# (6) Evolver guidance injected into the candidate contract only in paper mode
# ---------------------------------------------------------------------------


def test_contract_injects_evolver_guidance_when_provided():
    contract = rvp._build_candidate_contract(
        manifest_mode="repo",
        suggested_candidate_id="C-R1-01",
        target_variant="V0",
        planner_brief={"rationale": "x"},
        paper_evolver_guidance=rvp._PAPER_EVOLVER_GUIDANCE,
    )
    brief = contract["planner_brief"]
    assert brief["paper_evolver_guidance"] == rvp._PAPER_EVOLVER_GUIDANCE
    assert "This role is research, not maintenance." in brief["paper_evolver_guidance"]


def test_contract_byte_identical_without_guidance():
    # ours mode passes None -> the brief is byte-identical to the pre-P1-1 output
    # (which never had a paper_evolver_guidance key).
    with_none = rvp._build_candidate_contract(
        manifest_mode="repo",
        suggested_candidate_id="C-R1-01",
        target_variant="V0",
        planner_brief={"rationale": "x"},
        paper_evolver_guidance=None,
    )
    without_param = rvp._build_candidate_contract(
        manifest_mode="repo",
        suggested_candidate_id="C-R1-01",
        target_variant="V0",
        planner_brief={"rationale": "x"},
    )
    assert with_none == without_param
    assert "paper_evolver_guidance" not in with_none["planner_brief"]
