# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""Offline tests for P1 — ``--evolve-continuity``.

No network, no real meta-agent: ``slot_agent.evolve`` is a scripted stub that
reproduces ``agent.py``'s no-config behaviour (write ``DECISION_REQUIRED.md`` and
raise) and also lays down the session artefacts continuity reads — a
``*_state.json`` with a cumulative ``step`` field (the per-slot step ledger), an
optional ``_meta_scratch/NOTES.md`` (working notes) and an optional session
``*.jsonl`` transcript (the notes fallback). What is exercised: notes/transcript
injection on retry, the descending per-attempt ``max_steps`` ledger, the sub-floor
auto-abstain, the exhausted-retries auto-abstain (no RuntimeError death path), the
save/restore of the agent's ``max_steps``, the argparse-time validations, and
byte-identical ``off`` behaviour.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

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


class _ContinuityMeta:
    """Scripted ``evolve`` that also lays down the session artefacts P1 reads.

    ``script[i]`` is ``"no_config"`` / ``"ship"`` / ``"raw_error"``. Per call it
    optionally writes ``steps_per_attempt[i]`` into a ``*_state.json`` (the ledger
    source), ``notes[i]`` into ``NOTES.md``, and ``transcript[i]`` (a list of
    assistant strings) into a session ``*.jsonl`` (the notes fallback). ``max_steps``
    is a real mutable attribute so the ledger's descent and its restoration are
    observable, and the budget in force at each call is recorded.
    """

    def __init__(
        self,
        script,
        *,
        steps_per_attempt=None,
        notes=None,
        transcript=None,
        max_steps: int = 100,
    ) -> None:
        self.script = script
        self.steps_per_attempt = steps_per_attempt or []
        self.notes = notes or []
        self.transcript = transcript or []
        self.max_steps = max_steps
        self.calls: list[dict] = []
        self._contract = None

    def set_candidate_contract(self, contract) -> None:
        self._contract = contract

    async def evolve(self, *, output_dir, **kwargs):
        idx = len(self.calls)
        self.calls.append(
            {
                "output_dir": Path(output_dir),
                "contract": self._contract,
                "max_steps": self.max_steps,
            }
        )
        out = Path(output_dir)
        scratch = out / "_meta_scratch"
        scratch.mkdir(parents=True, exist_ok=True)
        sessions = out / "meta_workspace" / "sessions"
        sessions.mkdir(parents=True, exist_ok=True)
        if idx < len(self.steps_per_attempt):
            (sessions / f"{idx}_state.json").write_text(
                json.dumps({"step": self.steps_per_attempt[idx]}), encoding="utf-8"
            )
        if idx < len(self.notes) and self.notes[idx] is not None:
            (scratch / "NOTES.md").write_text(self.notes[idx], encoding="utf-8")
        if idx < len(self.transcript) and self.transcript[idx]:
            lines = [
                json.dumps({"type": "assistant", "message": {"content": m}})
                for m in self.transcript[idx]
            ]
            (sessions / f"{idx}.jsonl").write_text("\n".join(lines), encoding="utf-8")
        action = self.script[idx] if idx < len(self.script) else "ship"
        if action == "no_config":
            (scratch / "DECISION_REQUIRED.md").write_text(
                "# Missing config.yaml\nit ended with analysis but did not commit "
                "to a final decision.\n",
                encoding="utf-8",
            )
            raise RuntimeError(f"did not produce {out / 'config.yaml'}")
        if action == "raw_error":
            raise RuntimeError("replay smoke timed out")
        cfg = out / "config.yaml"
        cfg.write_text(f"shipped: {idx}\n", encoding="utf-8")
        return cfg


def _current_config(tmp_path: Path, body: bytes = b"root: parent\n") -> Path:
    path = tmp_path / "current.yaml"
    path.write_bytes(body)
    return path


async def _run(meta, slot, current, **overrides):
    kwargs = dict(
        slot_agent=meta,
        slot=slot,
        manifest_mode="repo",
        target_variant="V0",
        planner_brief={"rationale": "x"},
        base_evolve_kwargs={"current_config": current},
        max_retries=1,
        continuity="on",
        abstain="outcome",
    )
    kwargs.update(overrides)
    return await rvp._evolve_candidate_with_retry(**kwargs)


# ===========================================================================
# working notes / transcript injection on retry
# ===========================================================================


@pytest.mark.asyncio
async def test_notes_are_injected_into_the_retry_contract(tmp_path: Path) -> None:
    meta = _ContinuityMeta(
        ["no_config", "ship"],
        steps_per_attempt=[10, 10],
        notes=["I ruled out the prompt lever; leaning on a tool.", None],
    )
    outcome = await _run(meta, _slot(tmp_path), _current_config(tmp_path))

    assert len(meta.calls) == 2
    retry_brief = meta.calls[1]["contract"]["planner_brief"]
    # Both the working-notes directive (present every attempt) AND the prior notes.
    assert "working_notes_requirement" in retry_brief
    assert "prior_working_notes" in retry_brief
    assert "ruled out the prompt lever" in retry_brief["prior_working_notes"]
    assert "NOTES.md" in retry_brief["prior_working_notes"]
    # DECISION_REQUIRED feedback is carried ALONGSIDE the notes.
    assert "prior_decision_required_feedback" in retry_brief
    # First attempt has no prior notes but does carry the directive.
    assert "working_notes_requirement" in meta.calls[0]["contract"]["planner_brief"]
    assert "prior_working_notes" not in meta.calls[0]["contract"]["planner_brief"]
    assert outcome.attempts == 2 and not outcome.auto_abstain


@pytest.mark.asyncio
async def test_transcript_tail_is_the_fallback_when_notes_absent(tmp_path: Path) -> None:
    meta = _ContinuityMeta(
        ["no_config", "ship"],
        steps_per_attempt=[10, 10],
        notes=[None, None],  # no NOTES.md -> fall back to the transcript
        transcript=[["first thought", "the failing task needs a retry tool"], None],
    )
    await _run(meta, _slot(tmp_path), _current_config(tmp_path))

    retry_brief = meta.calls[1]["contract"]["planner_brief"]
    assert "prior_working_notes" in retry_brief
    injected = retry_brief["prior_working_notes"]
    assert "closing analysis" in injected
    assert "needs a retry tool" in injected  # the last assistant message


# ===========================================================================
# per-slot step ledger
# ===========================================================================


@pytest.mark.asyncio
async def test_per_attempt_max_steps_descends_with_the_ledger(tmp_path: Path) -> None:
    meta = _ContinuityMeta(
        ["no_config", "no_config", "ship"],
        steps_per_attempt=[30, 40, 5],
        max_steps=100,
    )
    outcome = await _run(meta, _slot(tmp_path), _current_config(tmp_path), max_retries=2)

    # total=100; attempt0 gets 100 (uses 30) -> attempt1 gets 70 (uses 40) ->
    # attempt2 gets 30.
    assert [c["max_steps"] for c in meta.calls] == [100, 70, 30]
    assert outcome.steps_used == 75  # 30 + 40 + 5
    # The agent's max_steps is restored to the original after the ledger ran.
    assert meta.max_steps == 100


@pytest.mark.asyncio
async def test_sub_floor_budget_auto_abstains_without_launching(tmp_path: Path) -> None:
    current = _current_config(tmp_path)
    meta = _ContinuityMeta(
        ["no_config"],
        steps_per_attempt=[97],  # leaves 3 < floor(5)
        max_steps=100,
    )
    outcome = await _run(meta, _slot(tmp_path), current, max_retries=3)

    assert len(meta.calls) == 1  # the floor blocked attempt 1
    assert outcome.auto_abstain is True
    assert "exhausted without decision" in outcome.abstain_reason
    # The terminal outcome is a real config, byte-identical to the parent.
    assert outcome.config_path.read_bytes() == current.read_bytes()
    assert meta.max_steps == 100  # restored


@pytest.mark.asyncio
async def test_exhausted_retries_auto_abstain_instead_of_raising(tmp_path: Path) -> None:
    current = _current_config(tmp_path)
    meta = _ContinuityMeta(
        ["no_config", "no_config"],
        steps_per_attempt=[10, 10],
        max_steps=100,
    )
    outcome = await _run(meta, _slot(tmp_path), current, max_retries=1)

    assert len(meta.calls) == 2  # both attempts ran; no RuntimeError
    assert outcome.auto_abstain is True
    assert "after 2 attempts / 20 steps" in outcome.abstain_reason
    assert outcome.config_path.read_bytes() == current.read_bytes()
    # Written beside the last launched (retry_01) attempt.
    assert outcome.config_path == _slot(tmp_path).output_dir / "retry_01" / "config.yaml"


@pytest.mark.asyncio
async def test_a_non_no_config_error_still_raises_under_continuity(tmp_path: Path) -> None:
    meta = _ContinuityMeta(["raw_error"], steps_per_attempt=[5], max_steps=100)
    with pytest.raises(RuntimeError, match="replay smoke timed out"):
        await _run(meta, _slot(tmp_path), _current_config(tmp_path), max_retries=2)
    assert len(meta.calls) == 1  # not retried, not auto-abstained
    assert meta.max_steps == 100  # still restored on the raising path


@pytest.mark.asyncio
async def test_unreadable_state_charges_the_full_grant(tmp_path: Path) -> None:
    """No ``*_state.json`` -> the granted budget is charged in full (conservative)."""
    current = _current_config(tmp_path)
    meta = _ContinuityMeta(
        ["no_config", "no_config"],
        steps_per_attempt=[],  # no state files written at all
        max_steps=100,
    )
    outcome = await _run(meta, _slot(tmp_path), current, max_retries=3)

    # attempt0 granted 100, no state -> charged 100 -> remaining 0 < floor -> abstain.
    assert len(meta.calls) == 1
    assert outcome.auto_abstain is True
    assert outcome.steps_used == 100


# ===========================================================================
# continuity helper units
# ===========================================================================


def test_agent_total_budget_reads_max_steps_else_default() -> None:
    assert rvp._agent_total_budget(SimpleNamespace(max_steps=42)) == 42
    assert rvp._agent_total_budget(SimpleNamespace(max_steps=0)) == rvp.EVOLVE_MAX_STEPS
    assert rvp._agent_total_budget(SimpleNamespace()) == rvp.EVOLVE_MAX_STEPS


def test_continuity_steps_used_reads_step_field(tmp_path: Path) -> None:
    sessions = tmp_path / "meta_workspace" / "sessions"
    sessions.mkdir(parents=True, exist_ok=True)
    (sessions / "abc_state.json").write_text(json.dumps({"step": 17}), encoding="utf-8")
    assert rvp._continuity_steps_used(tmp_path, granted=50) == 17
    # missing -> charge the grant
    assert rvp._continuity_steps_used(tmp_path / "empty", granted=50) == 50


def test_head_truncate_keeps_the_tail_with_a_marker() -> None:
    text = "abcdefghij" * 1000  # 10k chars
    out = rvp._head_truncate(text, 6000)
    assert len(out) <= 6000 + 80  # ~cap plus the one-line marker
    assert out.endswith("j")  # the most recent tail is kept
    assert "head-truncated" in out
    assert rvp._head_truncate("short", 6000) == "short"  # under cap untouched


def test_continuity_prior_context_prefers_notes_then_transcript(tmp_path: Path) -> None:
    attempt = tmp_path / "retry_01"
    scratch = attempt / "_meta_scratch"
    scratch.mkdir(parents=True, exist_ok=True)
    (scratch / "NOTES.md").write_text("my running notes", encoding="utf-8")
    got = rvp._continuity_prior_context(attempt)
    assert got is not None and "my running notes" in got and "NOTES.md" in got

    # No NOTES.md -> transcript fallback.
    attempt2 = tmp_path / "retry_02"
    sessions = attempt2 / "meta_workspace" / "sessions"
    sessions.mkdir(parents=True, exist_ok=True)
    (sessions / "s.jsonl").write_text(
        json.dumps({"type": "assistant", "message": {"content": "closing thought"}}),
        encoding="utf-8",
    )
    got2 = rvp._continuity_prior_context(attempt2)
    assert got2 is not None and "closing thought" in got2 and "closing analysis" in got2

    # Neither -> None.
    assert rvp._continuity_prior_context(tmp_path / "nope") is None


# ===========================================================================
# argparse-time validations + recipe __init__ guard
# ===========================================================================


def _args_ns(**overrides) -> SimpleNamespace:
    ns = SimpleNamespace(
        evolve_continuity="off",
        evolve_abstain="error",
        evolve_commit_bounce="off",
    )
    for key, value in overrides.items():
        setattr(ns, key, value)
    return ns


def test_continuity_requires_outcome_abstain() -> None:
    with pytest.raises(SystemExit, match="requires --evolve-abstain outcome"):
        rvp._validate_evolve_continuity_args(_args_ns(evolve_continuity="on"))


def test_continuity_excludes_commit_bounce() -> None:
    with pytest.raises(SystemExit, match="mutually exclusive with"):
        rvp._validate_evolve_continuity_args(
            _args_ns(evolve_continuity="on", evolve_abstain="outcome", evolve_commit_bounce="on")
        )


def test_continuity_valid_combination_passes() -> None:
    # No raise.
    rvp._validate_evolve_continuity_args(
        _args_ns(evolve_continuity="on", evolve_abstain="outcome", evolve_commit_bounce="off")
    )
    rvp._validate_evolve_continuity_args(_args_ns())  # off = no-op


def test_flag_default_and_choices() -> None:
    parser = rvp.build_arg_parser()
    args = parser.parse_args([])
    assert args.evolve_continuity == "off"
    assert parser.parse_args(["--evolve-continuity", "on"]).evolve_continuity == "on"
    with pytest.raises(SystemExit):
        parser.parse_args(["--evolve-continuity", "maybe"])


def test_provenance_is_none_for_default() -> None:
    assert rvp._evolve_continuity_provenance("off") is None
    warn = rvp._evolve_continuity_provenance("on")
    assert warn is not None and "evolve_continuity" in warn


# ===========================================================================
# off = byte-identical to the pre-P1 loop
# ===========================================================================


def test_build_contract_defaults_are_byte_identical() -> None:
    """All four P1/P2/P3 contract flags at their defaults reproduce the pre-change
    contract exactly (no new brief keys)."""
    common = dict(
        manifest_mode="repo",
        suggested_candidate_id="C-R1-01",
        target_variant="V0",
        planner_brief={"rationale": "x"},
    )
    baseline = rvp._build_candidate_contract(**common)
    with_defaults = rvp._build_candidate_contract(
        **common,
        working_notes_directive=False,
        prior_working_notes=None,
        abstain_reason_directive=False,
        repair_instruction=None,
    )
    assert with_defaults == baseline
    brief = baseline["planner_brief"]
    for key in (
        "working_notes_requirement",
        "prior_working_notes",
        "abstain_reason_requirement",
        "proposal_repair_instruction",
    ):
        assert key not in brief


@pytest.mark.asyncio
async def test_off_is_byte_identical_no_continuity_keys(tmp_path: Path) -> None:
    """continuity=off (default) never touches max_steps / notes and keeps the
    pre-P1 retry contract (no continuity keys)."""
    meta = _ContinuityMeta(["no_config", "ship"], steps_per_attempt=[10, 10], max_steps=100)
    outcome = await rvp._evolve_candidate_with_retry(
        slot_agent=meta,
        slot=_slot(tmp_path),
        manifest_mode="repo",
        target_variant="V0",
        planner_brief={"rationale": "x"},
        base_evolve_kwargs={"current_config": _current_config(tmp_path)},
        max_retries=1,
        # continuity defaults to "off", abstain to "error"
    )
    assert outcome.attempts == 2 and not outcome.auto_abstain
    for call in meta.calls:
        brief = call["contract"]["planner_brief"]
        assert "working_notes_requirement" not in brief
        assert "prior_working_notes" not in brief
        assert "abstain_reason_requirement" not in brief
        assert call["max_steps"] == 100  # never re-budgeted
