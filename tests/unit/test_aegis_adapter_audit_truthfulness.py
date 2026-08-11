# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""The AEGIS pipeline audit must state what EXECUTED, not what was CONFIGURED.

Every LLM AEGIS adapter (``_LLMDigester`` / ``_LLMPlanner`` / ``_LLMCritic``)
silently reverts to its deterministic fallback on any error, so a configured-
``llm`` role can still run wholly (or partly) deterministic. These tests pin the
recording added to each adapter and the four recipe properties that read it, so a
degraded round can no longer masquerade as a genuine three-role LLM round.

No network / API keys: the completion path is monkeypatched and stub deterministic
fallbacks stand in for the real ones.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import recipe.gaia_evolver.run_variant_pool as rvp  # noqa: E402
from experiments.variant_pool.candidate_pipeline import (  # noqa: E402
    DigesterRoundArtifact,
    PlanningArtifact,
)
from experiments.variant_pool.critic import CriticReview  # noqa: E402
from experiments.variant_pool.evidence import TaskDigest  # noqa: E402
from recipe.gaia_evolver.run_variant_pool import (  # noqa: E402
    VariantPoolRecipe,
    _AdapterExecution,
    _LLMCritic,
    _LLMDigester,
    _LLMPlanner,
)


# ─── stub deterministic fallbacks (return canned artifacts, no side effects) ──


class _StubDigesterFallback:
    def __init__(self, artifact: DigesterRoundArtifact) -> None:
        self._artifact = artifact

    async def digest(self, *, context):  # noqa: ANN001 - test stub
        return self._artifact


class _StubPlannerFallback:
    def __init__(self, artifact: PlanningArtifact) -> None:
        self._artifact = artifact

    async def plan(self, *, context, digests):  # noqa: ANN001 - test stub
        return self._artifact


class _StubCriticFallback:
    def __init__(self, review: CriticReview) -> None:
        self._review = review

    async def review(self, *, context, candidates):  # noqa: ANN001 - test stub
        return self._review


# ─── helpers ─────────────────────────────────────────────────────────────────


def _failed_digest(task_id: str) -> TaskDigest:
    # outcome (0, 2): zero passes over two rollouts -> TaskDigest.solved is False.
    return TaskDigest(task_id=task_id, round_idx=1, variant_id="v1", outcome=(0, 2))


def _make_digester() -> _LLMDigester:
    return _LLMDigester(
        evidence=None,
        pool=None,
        provider=None,
        tasks_by_id={},
        run_dir=Path("."),
        fallback=_StubDigesterFallback(
            DigesterRoundArtifact(digests=(), actionability=0.0, rationale="stub deterministic")
        ),
    )


def _bare_recipe(
    *,
    digester_cfg: str,
    planner_cfg: str,
    critic_cfg: str,
    digester_exec: _AdapterExecution | None = None,
    planner_exec: _AdapterExecution | None = None,
    critic_exec: _AdapterExecution | None = None,
) -> VariantPoolRecipe:
    """A recipe carrying only the attributes the audit properties read."""

    recipe = VariantPoolRecipe.__new__(VariantPoolRecipe)
    recipe.aegis_digester = digester_cfg
    recipe.aegis_planner = planner_cfg
    recipe.aegis_critic = critic_cfg
    recipe._active_digester_execution = digester_exec
    recipe._active_planner_execution = planner_exec
    recipe._active_critic_execution = critic_exec
    return recipe


# ─── 1. Critic LLM path raises -> deterministic name, reproduction False ──────


async def test_critic_fallback_reports_deterministic_name_and_reason(monkeypatch) -> None:
    critic = _LLMCritic(provider=None, fallback=_StubCriticFallback(CriticReview()))

    async def _boom(context, candidates):  # noqa: ANN001 - test stub
        raise RuntimeError("provider exploded")

    monkeypatch.setattr(critic, "_review_llm", _boom)
    review = await critic.review(context=None, candidates=())

    # Behaviour unchanged: the round still receives a deterministic review.
    assert isinstance(review, CriticReview)
    assert critic.execution.llm == 0
    assert critic.execution.fallback == 1
    assert critic.execution.reasons == ["RuntimeError: provider exploded"]

    recipe = _bare_recipe(
        digester_cfg="llm",
        planner_cfg="llm",
        critic_cfg="llm",
        digester_exec=_AdapterExecution(llm=1),
        planner_exec=_AdapterExecution(llm=1),
        critic_exec=critic.execution,
    )
    assert recipe._critic_adapter_name == "deterministic_portfolio_fallback"
    assert recipe._llm_aegis_reproduction is False
    block = recipe._adapter_fallbacks_block()
    assert block["critic"]["fallback"] == 1
    assert "RuntimeError: provider exploded" in block["critic"]["reasons"]


# ─── 2. All three LLM roles succeed -> MetaModel_* names, reproduction True ────


async def test_all_three_llm_success_names_and_reproduction(monkeypatch) -> None:
    digester = _make_digester()
    monkeypatch.setattr(rvp, "_latest_settled_digests", lambda *a, **k: (_failed_digest("t1"),))

    async def _interp(context, digest):  # noqa: ANN001 - test stub
        return digest, None

    async def _actionability(context, out_digests):  # noqa: ANN001 - test stub
        return 0.5, "actionable this round"

    monkeypatch.setattr(digester, "_interpret_failed_task", _interp)
    monkeypatch.setattr(digester, "_round_actionability", _actionability)
    await digester.digest(context=None)
    assert digester.execution.llm == 1
    assert digester.execution.fallback == 0

    planner = _LLMPlanner(
        provider=None,
        k_t=3,
        fallback=_StubPlannerFallback(PlanningArtifact(target_variant="v1")),
    )

    async def _plan_ok(context, digests):  # noqa: ANN001 - test stub
        return PlanningArtifact(target_variant="v1", empty_landscape=False)

    monkeypatch.setattr(planner, "_plan_llm", _plan_ok)
    await planner.plan(context=None, digests=())
    assert planner.execution.llm == 1
    assert planner.execution.fallback == 0

    critic = _LLMCritic(provider=None, fallback=_StubCriticFallback(CriticReview()))

    async def _review_ok(context, candidates):  # noqa: ANN001 - test stub
        return CriticReview()

    monkeypatch.setattr(critic, "_review_llm", _review_ok)
    await critic.review(context=None, candidates=())
    assert critic.execution.llm == 1
    assert critic.execution.fallback == 0

    recipe = _bare_recipe(
        digester_cfg="llm",
        planner_cfg="llm",
        critic_cfg="llm",
        digester_exec=digester.execution,
        planner_exec=planner.execution,
        critic_exec=critic.execution,
    )
    assert recipe._digester_adapter_name == "MetaModel_llm_digester"
    assert recipe._planner_adapter_name == "MetaModel_llm_planner"
    assert recipe._critic_adapter_name == "MetaModel_llm_critic"
    assert recipe._llm_aegis_reproduction is True
    block = recipe._adapter_fallbacks_block()
    assert block["digester"]["fallback"] == 0
    assert block["planner"]["fallback"] == 0
    assert block["critic"]["fallback"] == 0


# ─── 3. Digester: exactly one of several tasks falls back -> partial split ─────


async def test_digester_partial_fallback_splits_counts(monkeypatch) -> None:
    digester = _make_digester()
    monkeypatch.setattr(
        rvp,
        "_latest_settled_digests",
        lambda *a, **k: (_failed_digest("t1"), _failed_digest("t2"), _failed_digest("t3")),
    )

    note = "t3: LLM interpretation failed twice (bad json); kept deterministic digest"

    async def _interp(context, digest):  # noqa: ANN001 - test stub
        if digest.task_id == "t3":
            return digest, note
        return digest, None

    async def _actionability(context, out_digests):  # noqa: ANN001 - test stub
        return 0.5, "actionable this round"

    monkeypatch.setattr(digester, "_interpret_failed_task", _interp)
    monkeypatch.setattr(digester, "_round_actionability", _actionability)
    await digester.digest(context=None)

    assert digester.execution.llm == 2
    assert digester.execution.fallback == 1
    assert digester.execution.no_target == 0
    assert digester.execution.reasons == [note]

    recipe = _bare_recipe(
        digester_cfg="llm",
        planner_cfg="llm",
        critic_cfg="llm",
        digester_exec=digester.execution,
        planner_exec=_AdapterExecution(llm=1),
        critic_exec=_AdapterExecution(llm=1),
    )
    # Any fallback in the round forfeits the pure-LLM digester name.
    assert recipe._digester_adapter_name == "deterministic_evidence_store_fallback"
    assert recipe._llm_aegis_reproduction is False
    block = recipe._adapter_fallbacks_block()
    assert block["digester"] == {
        "llm": 2,
        "fallback": 1,
        "no_target": 0,
        "reasons": [note],
    }


# ─── 4. Digester "target gone" -> distinct no_target state, NOT a fallback ─────


async def test_digester_no_target_is_distinct_state(monkeypatch) -> None:
    det_artifact = DigesterRoundArtifact(digests=(), actionability=0.0, rationale="selected target is no longer active")
    digester = _LLMDigester(
        evidence=None,
        pool=None,
        provider=None,
        tasks_by_id={},
        run_dir=Path("."),
        fallback=_StubDigesterFallback(det_artifact),
    )
    monkeypatch.setattr(rvp, "_latest_settled_digests", lambda *a, **k: None)

    result = await digester.digest(context=None)

    # Behaviour unchanged: the deterministic result is returned unprefixed.
    assert result is det_artifact
    assert digester.execution.no_target == 1
    assert digester.execution.fallback == 0
    assert digester.execution.llm == 0

    recipe = _bare_recipe(
        digester_cfg="llm",
        planner_cfg="llm",
        critic_cfg="llm",
        digester_exec=digester.execution,
        planner_exec=_AdapterExecution(llm=1),
        critic_exec=_AdapterExecution(llm=1),
    )
    # no_target is not a fallback: the LLM name and reproduction flag stand.
    assert recipe._digester_adapter_name == "MetaModel_llm_digester"
    assert recipe._llm_aegis_reproduction is True
    block = recipe._adapter_fallbacks_block()
    assert block["digester"]["no_target"] == 1
    assert block["digester"]["fallback"] == 0


# ─── 5. Regression: all roles deterministic -> today's names, no fallbacks key ─


def test_all_deterministic_names_and_no_fallbacks_block() -> None:
    recipe = _bare_recipe(
        digester_cfg="deterministic",
        planner_cfg="deterministic",
        critic_cfg="deterministic",
    )
    assert recipe._digester_adapter_name == "deterministic_evidence_store_fallback"
    assert recipe._planner_adapter_name == "deterministic_failure_cluster_fallback"
    assert recipe._critic_adapter_name == "deterministic_portfolio_fallback"
    assert recipe._llm_aegis_reproduction is False
    assert recipe._llm_aegis_reproduction_configured is False
    # Empty -> _persist_pipeline_audit omits the key -> payload byte-identical.
    assert recipe._adapter_fallbacks_block() == {}


# ─── 6. run_config keeps CONFIG intent even when a round degraded ─────────────


def test_run_config_reproduction_is_config_intent_not_execution() -> None:
    recipe = _bare_recipe(
        digester_cfg="llm",
        planner_cfg="llm",
        critic_cfg="llm",
        digester_exec=_AdapterExecution(llm=1),
        planner_exec=_AdapterExecution(llm=1),
        critic_exec=_AdapterExecution(fallback=1, reasons=["RuntimeError: boom"]),
    )
    # run_config records how the run was CONFIGURED -> stays True.
    assert recipe._llm_aegis_reproduction_configured is True
    # The per-round pipeline audit records what EXECUTED -> False.
    assert recipe._llm_aegis_reproduction is False
    assert recipe._critic_adapter_name == "deterministic_portfolio_fallback"
