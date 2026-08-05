# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""Offline tests for ``--candidate-load-feedback`` (SPEC §7.37).

No network, no real meta-agent. ``slot_agent.evolve`` is a scripted stub that
writes REAL candidate configs (a loadable ``processors: []`` or a broken one whose
``template_path`` points at a missing ``file://`` artefact) plus the session
``*_state.json`` the continuity ledger reads, so the in-slot validation runs the
SAME fail-closed net the evaluator uses. What is exercised:

PART 1 (in-slot, ``_evolve_candidate_with_continuity``): a shipped config that does
not load feeds the load error into the next attempt's contract (the same channel
DECISION_REQUIRED uses) ALONGSIDE the prior working notes; a corrected config on the
next attempt proceeds; exhausting the retries on load failures ends in the terminal
auto-abstain with the load-failure text as its reason; a validation failure costs an
attempt slot but NO step budget; and ``off`` never validates (byte-identical).

PART 2 (eval-time, ``_run_config_evaluation``): a CANDIDATE config that does not load
is downgraded to the existing infra-failure lane (all tasks scored all-infra) so the
run continues; the ACTIVE-POOL path still raises loudly, flag or no flag.
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


# ---------------------------------------------------------------------------
# config fixtures — a loadable config and one that fails the fail-closed net
# ---------------------------------------------------------------------------
#: Loads and resolves clean (no artefacts to verify).
_GOOD_CONFIG = "processors: []\n"


def _bad_config_text(
    missing: str = "file:///D:/nonexistent_dir_7p37/gaia_agent_evolved.j2",
) -> str:
    """A config that LOADS + canonicalizes but fails ``_resolve_artefact_paths``:
    its system-prompt ``template_path`` names a ``file://`` artefact that is absent,
    which the fail-closed net converts into a ``FileNotFoundError`` naming the target.
    """
    return (
        "processors:\n"
        "- _target_: harnessx.processors.context.system_prompt.SystemPromptProcessor\n"
        "  system_builder:\n"
        "    _target_: harnessx.processors.context.strategies.system_prompt.template."
        "TemplateSystemPromptBuilder\n"
        f'    template_path: "{missing}"\n'
    )


def _write_bad_config(tmp_path: Path, name: str = "bad.yaml") -> Path:
    path = tmp_path / name
    path.write_text(_bad_config_text(), encoding="utf-8")
    return path


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


def _current_config(tmp_path: Path, body: bytes = b"processors: []\n") -> Path:
    path = tmp_path / "current.yaml"
    path.write_bytes(body)
    return path


class _LoadFeedbackMeta:
    """Scripted ``evolve`` that ships REAL configs and lays down the ledger state.

    ``script[i]`` is ``"ship_bad"`` / ``"ship_good"`` / ``"no_config"`` /
    ``"raw_error"``. Per call it optionally writes ``steps_per_attempt[i]`` into a
    ``*_state.json`` (the ledger source) and ``notes[i]`` into ``NOTES.md`` (so the
    "feedback ALONGSIDE working notes" path is observable). ``max_steps`` is a real
    mutable attribute so the ledger's grant per attempt is visible.
    """

    def __init__(
        self,
        script,
        *,
        steps_per_attempt=None,
        notes=None,
        max_steps: int = 100,
    ) -> None:
        self.script = script
        self.steps_per_attempt = steps_per_attempt or []
        self.notes = notes or []
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
        action = self.script[idx] if idx < len(self.script) else "ship_good"
        if action == "no_config":
            (scratch / "DECISION_REQUIRED.md").write_text(
                "# Missing config.yaml\nanalysis but no final decision.\n",
                encoding="utf-8",
            )
            raise RuntimeError(f"did not produce {out / 'config.yaml'}")
        if action == "raw_error":
            raise RuntimeError("replay smoke timed out")
        cfg = out / "config.yaml"
        cfg.write_text(
            _bad_config_text() if action == "ship_bad" else _GOOD_CONFIG,
            encoding="utf-8",
        )
        return cfg


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
        candidate_load_feedback=True,
    )
    kwargs.update(overrides)
    return await rvp._evolve_candidate_with_retry(**kwargs)


# ===========================================================================
# PART 1 — in-slot proposal-time validation
# ===========================================================================


@pytest.mark.asyncio
async def test_load_failure_feeds_error_forward_then_a_fixed_config_proceeds(tmp_path: Path) -> None:
    """(a) A non-loading ship feeds the load error into the next attempt (alongside
    the working notes); a corrected config on attempt 2 is accepted normally."""
    meta = _LoadFeedbackMeta(
        ["ship_bad", "ship_good"],
        steps_per_attempt=[30, 40],
        notes=["ruled out the tool lever; trying a prompt edit.", None],
        max_steps=100,
    )
    outcome = await _run(meta, _slot(tmp_path), _current_config(tmp_path), max_retries=1)

    assert len(meta.calls) == 2
    retry_brief = meta.calls[1]["contract"]["planner_brief"]
    # The load error rides the SAME channel DECISION_REQUIRED uses.
    assert "prior_decision_required_feedback" in retry_brief
    feedback = retry_brief["prior_decision_required_feedback"]
    assert "LOAD FAILURE" in feedback
    assert "template_path" in feedback  # the real net named the offending target
    # ...fed ALONGSIDE the prior working notes.
    assert "prior_working_notes" in retry_brief
    assert "ruled out the tool lever" in retry_brief["prior_working_notes"]
    # The fixed config on attempt 2 becomes the outcome (not an abstain).
    assert not outcome.auto_abstain
    assert outcome.attempts == 2 and outcome.retries == 1
    assert outcome.config_path.read_text(encoding="utf-8") == _GOOD_CONFIG
    # Validation charged NO step budget: attempt 1 was granted the FULL budget (the
    # bad attempt's 30 steps were not charged) and only the good attempt's 40 count.
    assert meta.calls[1]["max_steps"] == 100
    assert outcome.steps_used == 40


@pytest.mark.asyncio
async def test_all_load_failures_end_in_abstain_carrying_the_load_error(tmp_path: Path) -> None:
    """(b) Every attempt ships a non-loading config -> terminal auto-abstain whose
    reason carries the load-failure text (no RuntimeError death path)."""
    current = _current_config(tmp_path)
    meta = _LoadFeedbackMeta(["ship_bad", "ship_bad"], steps_per_attempt=[10, 10], max_steps=100)
    outcome = await _run(meta, _slot(tmp_path), current, max_retries=1)

    assert len(meta.calls) == 2  # both attempts ran; no raise
    assert outcome.auto_abstain is True
    assert "load failure" in outcome.abstain_reason.lower()
    assert "template_path" in outcome.abstain_reason  # the real net's target name
    assert "after 2 attempts" in outcome.abstain_reason
    # Terminal config is byte-identical to the parent (the auto-abstain copy).
    assert outcome.config_path.read_bytes() == current.read_bytes()
    # Two validation failures charged zero step budget.
    assert outcome.steps_used == 0
    assert meta.max_steps == 100  # restored


@pytest.mark.asyncio
async def test_validation_failure_consumes_no_step_budget(tmp_path: Path) -> None:
    """A validation failure costs only an attempt slot: the next attempt is granted
    the FULL budget and ``steps_used`` counts only the accepted attempt."""
    meta = _LoadFeedbackMeta(["ship_bad", "ship_good"], steps_per_attempt=[55, 20], max_steps=100)
    outcome = await _run(meta, _slot(tmp_path), _current_config(tmp_path), max_retries=1)

    assert [c["max_steps"] for c in meta.calls] == [100, 100]  # bad attempt charged 0
    assert outcome.steps_used == 20  # only the accepted attempt's steps


@pytest.mark.asyncio
async def test_a_no_config_after_a_load_failure_reverts_the_terminal_reason(tmp_path: Path) -> None:
    """A no-config as the LAST terminal cause keeps the generic abstain reason even
    when an earlier attempt was a load failure (the load text is not stale)."""
    current = _current_config(tmp_path)
    meta = _LoadFeedbackMeta(["ship_bad", "no_config"], steps_per_attempt=[10, 10], max_steps=100)
    outcome = await _run(meta, _slot(tmp_path), current, max_retries=1)

    assert outcome.auto_abstain is True
    assert "exhausted without decision" in outcome.abstain_reason
    assert "load failure" not in outcome.abstain_reason.lower()


@pytest.mark.asyncio
async def test_flag_off_ships_a_non_loading_config_without_validating(tmp_path: Path, monkeypatch) -> None:
    """(c) ``off`` (default) never validates: a non-loading ship is accepted as-is,
    with no retry and no abstain — byte-identical to the pre-flag loop."""
    seen = {"n": 0}
    real = rvp._candidate_config_load_error

    def _spy(path):
        seen["n"] += 1
        return real(path)

    monkeypatch.setattr(rvp, "_candidate_config_load_error", _spy)
    meta = _LoadFeedbackMeta(["ship_bad"], steps_per_attempt=[10], max_steps=100)
    outcome = await _run(
        meta,
        _slot(tmp_path),
        _current_config(tmp_path),
        max_retries=1,
        candidate_load_feedback=False,
    )

    assert seen["n"] == 0  # validation was never called
    assert len(meta.calls) == 1  # shipped and accepted, no retry
    assert not outcome.auto_abstain
    assert outcome.config_path.read_text(encoding="utf-8") == _bad_config_text()


# ===========================================================================
# PART 2 — eval-time raise downgraded for candidates only
# ===========================================================================


def _fake_eval_self(candidate_load_feedback: bool):
    return SimpleNamespace(
        candidate_load_feedback=candidate_load_feedback,
        args=SimpleNamespace(pass_k=2, concurrency=2),
    )


async def _run_config_eval(fake_self, *, config_path, scope, vround_dir, task_ids):
    return await rvp.VariantPoolRecipe._run_config_evaluation(
        fake_self,
        config_path=config_path,
        variant_id="V0",
        task_ids=set(task_ids),
        round_idx=0,
        vround_dir=vround_dir,
        label="R0-V0-C",
        trajectory_rel_dir="R0/V0/x/trajectories",
        measurement_scope=scope,
    )


@pytest.mark.asyncio
async def test_eval_time_candidate_downgraded_active_and_off_still_raise(tmp_path: Path, monkeypatch) -> None:
    """(d) flag on: a candidate config-load failure is downgraded to the infra lane
    (run continues); the active-pool path — and any run with the flag off — still
    raises loudly, exactly as today."""
    monkeypatch.setattr(rvp, "_make_journal", lambda sessions_dir: None)
    bad = _write_bad_config(tmp_path)
    on = _fake_eval_self(candidate_load_feedback=True)

    # Candidate window gate: downgraded to all-infra, no raise.
    outcomes, cleaned, _ = await _run_config_eval(
        on, config_path=bad, scope="candidate_gate", vround_dir=tmp_path / "cand", task_ids={"t1", "t2"}
    )
    assert outcomes == {"t1": (0, 2), "t2": (0, 2)}  # all-infra, gate will reject
    assert all(rec["infra_failures"] == 2 for rec in cleaned.values())
    assert all("candidate_load_failure" in rec for rec in cleaned.values())
    assert all(rec["measurement_scope"] == "candidate_gate" for rec in cleaned.values())

    # Ship-confirm is candidate-side too: also downgraded.
    o2, _c2, _ = await _run_config_eval(
        on, config_path=bad, scope="ship_confirm", vround_dir=tmp_path / "sc", task_ids={"t1"}
    )
    assert o2 == {"t1": (0, 2)}

    # Active pool: a non-loading deployed variant is a run-integrity event — loud.
    with pytest.raises(FileNotFoundError):
        await _run_config_eval(
            on, config_path=bad, scope="settled_active_pool", vround_dir=tmp_path / "act", task_ids={"t1"}
        )

    # Flag off + candidate scope: byte-identical to today — still raises.
    off = _fake_eval_self(candidate_load_feedback=False)
    with pytest.raises(FileNotFoundError):
        await _run_config_eval(
            off, config_path=bad, scope="candidate_gate", vround_dir=tmp_path / "off", task_ids={"t1"}
        )


# ===========================================================================
# refactor + helper units
# ===========================================================================


def test_prepare_round_config_still_loads_validates_and_attaches_the_journal(tmp_path: Path) -> None:
    """The ``_validate_candidate_config`` extraction keeps ``_prepare_round_config``
    behaviour: a good config yields a config carrying the journal; a bad one raises
    through the same fail-closed net (the eval path is unchanged)."""
    good = tmp_path / "good.yaml"
    good.write_text(_GOOD_CONFIG, encoding="utf-8")
    round_config = rvp._prepare_round_config(good, journal="JOURNAL-SENTINEL")
    assert round_config.tracer == "JOURNAL-SENTINEL"

    with pytest.raises(FileNotFoundError):
        rvp._prepare_round_config(_write_bad_config(tmp_path), journal=None)


def test_validate_candidate_config_is_journal_free(tmp_path: Path) -> None:
    good = tmp_path / "good.yaml"
    good.write_text(_GOOD_CONFIG, encoding="utf-8")
    cfg, processors, tool_registry = rvp._validate_candidate_config(good)
    assert processors == []
    assert tool_registry is None
    # No tracer attached by validation (side-effect-free).
    assert getattr(cfg, "tracer", None) is None


def test_candidate_config_load_error_none_for_good_text_for_bad(tmp_path: Path) -> None:
    good = tmp_path / "good.yaml"
    good.write_text(_GOOD_CONFIG, encoding="utf-8")
    assert rvp._candidate_config_load_error(good) is None
    err = rvp._candidate_config_load_error(_write_bad_config(tmp_path))
    assert err is not None
    assert err.startswith("FileNotFoundError:")
    assert "template_path" in err


def test_candidate_load_failure_evaluation_marks_every_task_all_infra(tmp_path: Path) -> None:
    outcomes, cleaned, traj_dir = rvp._candidate_load_failure_evaluation(
        task_ids={"t1", "t2"},
        variant_id="V0",
        round_idx=3,
        measurement_scope="candidate_gate",
        pass_k=2,
        traj_dir=tmp_path,
        reason="RuntimeError: boom",
    )
    assert outcomes == {"t1": (0, 2), "t2": (0, 2)}
    for tid, rec in cleaned.items():
        # _failure_counts reads infra_failures directly, capped at n_att - n_pass.
        assert rec["infra_failures"] == 2 and rec["n_att"] == 2 and rec["n_pass"] == 0
        assert rec["candidate_load_failure"] == "RuntimeError: boom"
        assert rec["variant_id"] == "V0" and rec["round"] == 3
    assert traj_dir == tmp_path


# ===========================================================================
# argparse + provenance
# ===========================================================================


def test_flag_default_off_and_store_true() -> None:
    parser = rvp.build_arg_parser()
    assert parser.parse_args([]).candidate_load_feedback is False
    assert parser.parse_args(["--candidate-load-feedback"]).candidate_load_feedback is True


def test_provenance_is_none_for_default_warning_when_on() -> None:
    assert rvp._candidate_load_feedback_provenance(False) is None
    warn = rvp._candidate_load_feedback_provenance(True)
    assert warn is not None
    assert "candidate_load_feedback=on" in warn


def test_lock_records_provenance_only_when_enabled(tmp_path: Path) -> None:
    baseline = tmp_path / "baseline.yaml"
    baseline.write_text(_GOOD_CONFIG, encoding="utf-8")

    def _lock(enabled: bool):
        args = SimpleNamespace(
            candidate_load_feedback=enabled,
            pool_k=1,
            pass_k=2,
            num_rounds=1,
            max_steps=20,
            concurrency=2,
            patience=3,
        )
        return rvp._build_experiment_lock(
            args=args, run_tag="t", baseline_config_path=baseline, original_base=None
        )

    off_warnings = _lock(False).provenance_warnings
    on_warnings = _lock(True).provenance_warnings
    assert not any("candidate_load_feedback" in w for w in off_warnings)
    assert any("candidate_load_feedback=on" in w for w in on_warnings)
