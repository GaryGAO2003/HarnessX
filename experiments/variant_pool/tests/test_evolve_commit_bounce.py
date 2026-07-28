# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""Offline tests for W2 / F-A — ``--evolve-commit-bounce``.

No network, no real meta-agent: ``slot_agent.evolve`` is a scripted stub that
reproduces ``agent.py``'s no-config behaviour (write ``DECISION_REQUIRED.md``,
then raise). What is exercised is the bounce trigger (only after retries are
exhausted, only on a genuine no-config outcome, at most once), the shrunken step
budget, the bounce contract (two-endings directive + prior DECISION_REQUIRED
feedback), the success/failure audit, and byte-identical ``off`` behaviour.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from recipe.gaia_evolver import run_variant_pool as rvp  # noqa: E402
from experiments.variant_pool.candidate_pipeline import CandidateSlot  # noqa: E402


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
    """A meta-agent stub whose ``evolve`` follows a per-call script.

    ``script[i]`` is ``"no_config"`` (write DECISION_REQUIRED.md like agent.py and
    raise), ``"ship"`` (write config.yaml and return its path), or ``"raw_error"``
    (raise WITHOUT DECISION_REQUIRED.md — never bounced/retried). ``max_steps`` is
    a real mutable attribute so the budget-shrink can be observed and its
    restoration asserted.
    """

    def __init__(self, script: list[str], max_steps: int = 260) -> None:
        self.script = script
        self.calls: list[dict] = []
        self._contract = None
        self.max_steps = max_steps

    def set_candidate_contract(self, contract) -> None:
        self._contract = contract

    async def evolve(self, *, output_dir, **kwargs):
        idx = len(self.calls)
        self.calls.append(
            {
                "output_dir": Path(output_dir),
                "contract": self._contract,
                "max_steps": self.max_steps,  # budget in force at THIS call
            }
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
        config.write_text(f"shipped_at_call: {idx}\n", encoding="utf-8")
        return config


# ===========================================================================
# off = byte-identical to the pre-F-A failure path
# ===========================================================================


@pytest.mark.asyncio
async def test_bounce_off_is_byte_identical(tmp_path: Path) -> None:
    meta = _ScriptedMeta(["no_config"])
    slot = _slot(tmp_path)
    audit: dict = {}
    with pytest.raises(RuntimeError, match="did not produce"):
        await rvp._evolve_candidate_with_retry(
            slot_agent=meta,
            slot=slot,
            manifest_mode="repo",
            target_variant="V0",
            planner_brief={},
            base_evolve_kwargs={},
            max_retries=0,
            commit_bounce="off",
            bounce_audit=audit,
        )
    assert len(meta.calls) == 1  # the base attempt only — no bounce
    assert audit == {}  # off records nothing (keeps the audit byte-identical)


@pytest.mark.asyncio
async def test_default_params_never_bounce(tmp_path: Path) -> None:
    """The new params default to off/None, so existing callers are unaffected."""
    meta = _ScriptedMeta(["no_config"])
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
    assert len(meta.calls) == 1


# ===========================================================================
# on: the bounce recovers a no-config slot
# ===========================================================================


@pytest.mark.asyncio
async def test_bounce_on_recovers_a_no_config(tmp_path: Path) -> None:
    meta = _ScriptedMeta(["no_config", "ship"])  # base no_config, bounce ships
    slot = _slot(tmp_path)
    audit: dict = {}

    outcome = await rvp._evolve_candidate_with_retry(
        slot_agent=meta,
        slot=slot,
        manifest_mode="repo",
        target_variant="V0",
        planner_brief={"rationale": "x"},
        base_evolve_kwargs={},
        max_retries=0,
        commit_bounce="on",
        bounce_audit=audit,
    )

    assert len(meta.calls) == 2  # base attempt + exactly one bounce
    assert meta.calls[1]["output_dir"] == slot.output_dir / "bounce"  # isolated dir
    assert audit == {"bounce_used": True, "bounce_outcome": "shipped_via_bounce"}
    assert outcome.config_path == slot.output_dir / "bounce" / "config.yaml"

    # The bounce contract carries the two-endings directive + prior feedback.
    bounce_brief = meta.calls[1]["contract"]["planner_brief"]
    assert "commit_bounce_directive" in bounce_brief
    assert "cp" in bounce_brief["commit_bounce_directive"]  # the explicit no-op option
    assert "prior_decision_required_feedback" in bounce_brief
    assert "did not commit to a final decision" in bounce_brief["prior_decision_required_feedback"]

    # The bounce ran under a shrunken step budget, restored afterwards.
    assert meta.calls[1]["max_steps"] <= rvp._BOUNCE_MAX_STEPS
    assert meta.max_steps == 260  # restored to the original


@pytest.mark.asyncio
async def test_bounce_fires_only_after_retries_and_at_most_once(tmp_path: Path) -> None:
    # max_retries=1: attempt0 no_config -> retry_01 no_config -> ONE bounce ships.
    meta = _ScriptedMeta(["no_config", "no_config", "ship"])
    slot = _slot(tmp_path)
    audit: dict = {}

    outcome = await rvp._evolve_candidate_with_retry(
        slot_agent=meta,
        slot=slot,
        manifest_mode="repo",
        target_variant="V0",
        planner_brief={},
        base_evolve_kwargs={},
        max_retries=1,
        commit_bounce="on",
        bounce_audit=audit,
    )

    assert [c["output_dir"] for c in meta.calls] == [
        slot.output_dir,
        slot.output_dir / "retry_01",
        slot.output_dir / "bounce",
    ]
    assert audit == {"bounce_used": True, "bounce_outcome": "shipped_via_bounce"}
    assert outcome.retries == 1


# ===========================================================================
# on: a bounce that still fails to commit -> normal failure path + audit
# ===========================================================================


@pytest.mark.asyncio
async def test_bounce_on_still_missing_fails_with_audit(tmp_path: Path) -> None:
    meta = _ScriptedMeta(["no_config", "no_config"])  # base + bounce both no_config
    slot = _slot(tmp_path)
    audit: dict = {}

    with pytest.raises(RuntimeError, match="did not produce"):
        await rvp._evolve_candidate_with_retry(
            slot_agent=meta,
            slot=slot,
            manifest_mode="repo",
            target_variant="V0",
            planner_brief={},
            base_evolve_kwargs={},
            max_retries=0,
            commit_bounce="on",
            bounce_audit=audit,
        )

    assert len(meta.calls) == 2  # base + one bounce, then it gives up
    assert audit == {"bounce_used": True, "bounce_outcome": "still_missing"}


@pytest.mark.asyncio
async def test_raw_error_never_bounces(tmp_path: Path) -> None:
    """A failure without DECISION_REQUIRED.md is never bounced, even when on."""
    meta = _ScriptedMeta(["raw_error"])
    slot = _slot(tmp_path)
    audit: dict = {}
    with pytest.raises(RuntimeError, match="replay smoke timed out"):
        await rvp._evolve_candidate_with_retry(
            slot_agent=meta,
            slot=slot,
            manifest_mode="repo",
            target_variant="V0",
            planner_brief={},
            base_evolve_kwargs={},
            max_retries=0,
            commit_bounce="on",
            bounce_audit=audit,
        )
    assert len(meta.calls) == 1  # no bounce for a non-no-config error
    assert audit == {}


@pytest.mark.asyncio
async def test_bounce_on_but_first_attempt_ships_is_never_triggered(tmp_path: Path) -> None:
    meta = _ScriptedMeta(["ship"])
    slot = _slot(tmp_path)
    audit: dict = {}
    outcome = await rvp._evolve_candidate_with_retry(
        slot_agent=meta,
        slot=slot,
        manifest_mode="repo",
        target_variant="V0",
        planner_brief={},
        base_evolve_kwargs={},
        max_retries=1,
        commit_bounce="on",
        bounce_audit=audit,
    )
    assert len(meta.calls) == 1
    assert audit == {}  # bounce not triggered when the slot commits on its own
    assert outcome.attempts == 1


# ===========================================================================
# flag / provenance
# ===========================================================================


def test_evolve_commit_bounce_flag_default_and_choices() -> None:
    parser = rvp.build_arg_parser()
    assert parser.parse_args([]).evolve_commit_bounce == "off"
    assert parser.parse_args(["--evolve-commit-bounce", "on"]).evolve_commit_bounce == "on"
    with pytest.raises(SystemExit):
        parser.parse_args(["--evolve-commit-bounce", "maybe"])


def test_evolve_commit_bounce_provenance_is_none_for_default() -> None:
    assert rvp._evolve_commit_bounce_provenance("off") is None
    warn = rvp._evolve_commit_bounce_provenance("on")
    assert warn is not None and "evolve_commit_bounce" in warn
