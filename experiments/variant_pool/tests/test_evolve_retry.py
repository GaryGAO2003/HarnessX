# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""Offline tests for the forkprobe_11 fault-2 fix (--evolve-retry) and the
recipe-layer manifest brief injection / K_t candidate count.

No network, no real meta-agent: ``slot_agent.evolve`` is a scripted stub that
reproduces ``agent.py``'s no-config behaviour (write ``DECISION_REQUIRED.md``,
then raise) so the retry classification and feedback loop are exercised offline.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# The recipe lives under ``recipe/``; put the repo root on the path (conftest
# only adds ``experiments/``).
_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from recipe.gaia_evolver import run_variant_pool as rvp  # noqa: E402
from experiments.variant_pool.candidate_pipeline import (  # noqa: E402
    CandidateSlot,
    IsolatedEvolverAdapter,
    PipelineContext,
    PlanningArtifact,
    ProposalFailure,
)


def _slot(tmp_path: Path, candidate_id: str = "C-R1-01") -> CandidateSlot:
    output_dir = tmp_path / candidate_id
    output_dir.mkdir(parents=True, exist_ok=True)
    memo = tmp_path / f"{candidate_id}.md"
    memo.write_text("", encoding="utf-8")
    return CandidateSlot(
        suggested_candidate_id=candidate_id,
        output_dir=output_dir,
        memo_path=memo,
    )


class _ScriptedMeta:
    """A meta-agent stub whose ``evolve`` follows a per-attempt script.

    ``script[i]`` is either ``"no_config"`` (write DECISION_REQUIRED.md like
    agent.py and raise) or ``"ship"`` (write config.yaml and return its path).
    ``"raw_error"`` raises without writing DECISION_REQUIRED.md (a non-no-config
    failure that must never be retried).
    """

    def __init__(self, script: list[str]) -> None:
        self.script = script
        self.calls: list[dict] = []
        self._contract = None

    def set_candidate_contract(self, contract) -> None:
        # Mirrors VariantPoolMetaAgent: ``evolve``'s signature is upstream, so the
        # recipe sets the per-call contract on the agent right before each call.
        self._contract = contract

    async def evolve(self, *, output_dir, **kwargs):
        idx = len(self.calls)
        self.calls.append(
            {"output_dir": Path(output_dir), "contract": self._contract}
        )
        action = self.script[idx] if idx < len(self.script) else "ship"
        scratch = Path(output_dir) / "_meta_scratch"
        scratch.mkdir(parents=True, exist_ok=True)
        if action == "no_config":
            (scratch / "DECISION_REQUIRED.md").write_text(
                "# Missing config.yaml\nit ended with analysis but did not commit "
                "to a final decision.\n",
                encoding="utf-8",
            )
            raise RuntimeError(
                f"meta-agent finished after 300.0s but did not produce "
                f"{Path(output_dir) / 'config.yaml'}."
            )
        if action == "raw_error":
            raise RuntimeError("replay smoke timed out")
        config = Path(output_dir) / "config.yaml"
        config.write_text(f"shipped_at_attempt: {idx}\n", encoding="utf-8")
        return config


# ===========================================================================
# --evolve-retry
# ===========================================================================


@pytest.mark.asyncio
async def test_retry_recovers_a_first_attempt_no_config(tmp_path: Path) -> None:
    """No config first, config on retry: retry fires and the count is recorded."""
    meta = _ScriptedMeta(["no_config", "ship"])
    slot = _slot(tmp_path)

    outcome = await rvp._evolve_candidate_with_retry(
        slot_agent=meta,
        slot=slot,
        manifest_mode="repo",
        target_variant="V0",
        planner_brief={"brief_id": "b", "buckets": (), "task_ids": (), "rationale": ""},
        base_evolve_kwargs={"current_config": tmp_path / "cur.yaml"},
        max_retries=1,
    )

    assert meta.calls[0]["output_dir"] == slot.output_dir  # attempt 0 = base slot
    assert meta.calls[1]["output_dir"] == slot.output_dir / "retry_01"  # isolated
    assert outcome.attempts == 2
    assert outcome.retries == 1
    assert len(outcome.decision_required_history) == 1
    assert "did not commit to a final decision" in outcome.decision_required_history[0]
    # The retry brief carries the DECISION_REQUIRED text back to the meta-agent.
    retry_brief = meta.calls[1]["contract"]["planner_brief"]
    assert "prior_decision_required_feedback" in retry_brief
    assert "did not commit to a final decision" in retry_brief["prior_decision_required_feedback"]


@pytest.mark.asyncio
async def test_no_retry_when_budget_is_zero(tmp_path: Path) -> None:
    meta = _ScriptedMeta(["no_config", "ship"])
    slot = _slot(tmp_path)
    with pytest.raises(RuntimeError, match="did not produce"):
        await rvp._evolve_candidate_with_retry(
            slot_agent=meta,
            slot=slot,
            manifest_mode="repo",
            target_variant="V0",
            planner_brief={},
            base_evolve_kwargs={},
            max_retries=0,
        )
    assert len(meta.calls) == 1  # no retry attempted


@pytest.mark.asyncio
async def test_a_non_no_config_error_is_not_retried(tmp_path: Path) -> None:
    """A failure without DECISION_REQUIRED.md (e.g. a timeout) re-raises at once."""
    meta = _ScriptedMeta(["raw_error", "ship"])
    slot = _slot(tmp_path)
    with pytest.raises(RuntimeError, match="replay smoke timed out"):
        await rvp._evolve_candidate_with_retry(
            slot_agent=meta,
            slot=slot,
            manifest_mode="repo",
            target_variant="V0",
            planner_brief={},
            base_evolve_kwargs={},
            max_retries=2,
        )
    assert len(meta.calls) == 1  # not retried despite budget


@pytest.mark.asyncio
async def test_first_attempt_success_records_zero_retries(tmp_path: Path) -> None:
    meta = _ScriptedMeta(["ship"])
    slot = _slot(tmp_path)
    outcome = await rvp._evolve_candidate_with_retry(
        slot_agent=meta,
        slot=slot,
        manifest_mode="repo",
        target_variant="V0",
        planner_brief={},
        base_evolve_kwargs={},
        max_retries=1,
    )
    assert outcome.attempts == 1
    assert outcome.retries == 0
    assert outcome.decision_required_history == ()
    assert len(meta.calls) == 1


# ===========================================================================
# manifest brief injection (fault 1 recipe side) + B4 decision emphasis
# ===========================================================================


def test_paper_mode_injects_table9_schema_and_the_c_r10_02_example() -> None:
    contract = rvp._build_candidate_contract(
        manifest_mode="paper",
        suggested_candidate_id="C-R1-01",
        target_variant="V0",
        planner_brief={"rationale": "x"},
    )
    brief = contract["planner_brief"]
    assert brief["manifest_mode"] == "paper"
    assert "Table 9" in brief["manifest_instructions"]
    assert "C-R10-02" in brief["manifest_instructions"]
    # B4 decision-contract emphasis is present in both modes.
    assert "did not commit to a final decision" in brief["decision_contract_requirement"]
    # The contract carries the repo-gate-safe alias of the slot id, never the
    # paper shape: validate_workflow's evidence gate only accepts `C-\d+`.
    assert contract["suggested_candidate_id"] == "C-0101"
    assert contract["target_variant"] == "V0"


def test_repo_mode_injects_journal_acceptance_not_the_paper_schema() -> None:
    contract = rvp._build_candidate_contract(
        manifest_mode="repo",
        suggested_candidate_id="C-R1-01",
        target_variant="V0",
        planner_brief={},
    )
    brief = contract["planner_brief"]
    assert brief["manifest_mode"] == "repo"
    assert "journal vocabulary" in brief["manifest_instructions"]
    assert "levers" in brief["manifest_instructions"]
    assert "C-R10-02" not in brief["manifest_instructions"]
    assert "did not commit to a final decision" in brief["decision_contract_requirement"]


# ===========================================================================
# --candidates-per-round controls the number of meta sessions
# ===========================================================================


def _context(tmp_path: Path) -> PipelineContext:
    current = tmp_path / "current.yaml"
    current.write_text("root: c\n", encoding="utf-8")
    trajectories = tmp_path / "traj"
    trajectories.mkdir(exist_ok=True)
    return PipelineContext(
        round_idx=1,
        target_variant="V0",
        current_config_path=current,
        trajectories_dir=trajectories,
        output_root=tmp_path / "out",
    )


@pytest.mark.parametrize("k_t", [1, 4])
@pytest.mark.asyncio
async def test_candidates_per_round_drives_one_meta_session_per_slot(
    tmp_path: Path, k_t: int
) -> None:
    """``limit`` (= candidates_per_round) meta sessions run — one per slot."""
    calls: list[str] = []

    async def _producer(*, context, plan, slot):
        calls.append(slot.suggested_candidate_id)
        return ProposalFailure(slot.suggested_candidate_id, "counting stub")

    adapter = IsolatedEvolverAdapter(producer=_producer)
    context = _context(tmp_path)
    plan = PlanningArtifact(target_variant="V0")

    results = await adapter.propose(context=context, plan=plan, limit=k_t)

    assert len(calls) == k_t
    assert len(results) == k_t
    # Distinct, deterministic candidate slots.
    assert sorted(calls) == [f"C-R1-{i:02d}" for i in range(1, k_t + 1)]
