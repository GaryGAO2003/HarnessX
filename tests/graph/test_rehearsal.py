"""Shadow-evolution rehearsal runner — B / F0 arms, K=1 Global selection.

Zero-API throughout: a ``StubTaskBed`` / small path-keyed beds stand in for the
GAIA task bed and :func:`stub_proposer` stands in for the LLM proposer, so the
whole propose→gate→evaluate→select→advance loop is exercised without a model
call.  Same directory / style as ``tests/graph/test_eval_bridge.py``.
"""

import asyncio

import pytest

from experiments.variant_pool.eval_bridge import StubTaskBed, TaskBedResult
from experiments.variant_pool.proposer import ExtractionResult
from experiments.variant_pool.rehearsal import run_rehearsal, stub_proposer
from experiments.variant_pool.shadow_evolution import ShadowLedger
from harnessx.core.harness import HarnessConfig

from tests.graph.fixtures import serialized_dict

PROBE_TARGET = "tests.graph.fixtures.RuntimeProbe"


def _write_parent(tmp_path):
    """Write a legal single-processor parent config.yaml (order=10)."""
    config = HarnessConfig(processors=[
        serialized_dict(PROBE_TARGET, hook="task_start",
                        singleton_group="probe", order=10),
    ])
    path = tmp_path / "parent.yaml"
    config.to_yaml_file(path)
    return path


class _PathBed:
    """pass_rate keyed on whether the config is a materialized candidate.

    Materialized candidates live under ``.../rounds/rN/...``; the parent
    baseline config does not — that separation lets one bed hand the parent one
    score and every challenger another, deterministically.
    """

    def __init__(self, *, parent_pr, cand_pr):
        self._parent_pr = parent_pr
        self._cand_pr = cand_pr

    async def evaluate(self, config_path, task_ids=None):
        is_candidate = "/rounds/" in str(config_path).replace("\\", "/")
        pr = self._cand_pr if is_candidate else self._parent_pr
        return TaskBedResult(
            per_task={"t1": {"passed": pr > 0.5, "cost_usd": 0.01, "tokens": 100}},
            pass_rate=pr, total_cost_usd=0.01, total_tokens=100,
        )


class _InfraFailCandidateBed:
    """Parent baseline loads; every materialized candidate is infra_failed."""

    async def evaluate(self, config_path, task_ids=None):
        if "/rounds/" in str(config_path).replace("\\", "/"):
            return TaskBedResult(per_task={}, pass_rate=0.0,
                                 infra_failed=True, error="boom")
        return TaskBedResult(per_task={"t1": {"passed": True}}, pass_rate=0.4)


# ── B arm end-to-end ─────────────────────────────────────────────────────────


def test_three_round_dry_run_b_arm(tmp_path):
    parent = _write_parent(tmp_path)
    ledger = ShadowLedger(tmp_path / "shadow.jsonl")

    report = asyncio.run(run_rehearsal(
        parent, rounds=3, task_bed=StubTaskBed(pass_rate=0.5),
        proposer=stub_proposer, ledger=ledger, out_dir=tmp_path / "out"))

    assert len(report.round_reports) == 3
    # ledger carries both gate rows and (measured) evaluation rows
    kinds = [r.record_kind for r in ledger.records()]
    assert "gate" in kinds and "evaluation" in kinds
    # round gate never exceeds the kernel's ≤4 candidate cap; all parsed
    for rr in report.round_reports:
        assert rr.parse_ok
        assert 1 <= rr.n_gated <= 4
        assert rr.n_evaluated == rr.n_gated
        assert rr.baseline_measured["pass_rate"] == pytest.approx(0.5)
    # raw facts flushed to disk
    assert (tmp_path / "out" / "report.json").exists()


def test_proposer_defaults_to_stub_when_none(tmp_path):
    parent = _write_parent(tmp_path)
    ledger = ShadowLedger(tmp_path / "shadow.jsonl")
    report = asyncio.run(run_rehearsal(
        parent, rounds=1, task_bed=StubTaskBed(pass_rate=0.5),
        proposer=None, ledger=ledger, out_dir=tmp_path / "out"))
    assert report.round_reports[0].n_gated >= 1


# ── selection: promote / no-promote lineage ──────────────────────────────────


def test_winner_promotes_and_advances_lineage(tmp_path):
    parent = _write_parent(tmp_path)
    ledger = ShadowLedger(tmp_path / "shadow.jsonl")

    report = asyncio.run(run_rehearsal(
        parent, rounds=1, task_bed=_PathBed(parent_pr=0.3, cand_pr=0.9),
        proposer=stub_proposer, ledger=ledger, out_dir=tmp_path / "out"))

    rr = report.round_reports[0]
    assert rr.winner  # a challenger was promoted
    applied = [c for c in rr.candidates if c.decision == "APPLY"]
    assert len(applied) == 1
    assert applied[0].candidate_id == rr.winner
    # the next parent is the winner's materialized config, not the seed file
    assert report.final_parent_config == applied[0].config_path
    assert report.final_parent_config != str(parent)
    # APPLY is written as a measured evaluation row (上线门禁)
    apply_rows = [r for r in ledger.records()
                  if r.record_kind == "evaluation" and r.decision == "APPLY"]
    assert len(apply_rows) == 1
    assert apply_rows[0].measured["pass_rate"] == pytest.approx(0.9)


def test_no_winner_keeps_parent(tmp_path):
    parent = _write_parent(tmp_path)
    ledger = ShadowLedger(tmp_path / "shadow.jsonl")

    report = asyncio.run(run_rehearsal(
        parent, rounds=2, task_bed=StubTaskBed(pass_rate=0.5),
        proposer=stub_proposer, ledger=ledger, out_dir=tmp_path / "out"))

    for rr in report.round_reports:
        assert rr.winner == ""
        assert rr.candidates and all(c.decision == "REJECT" for c in rr.candidates)
    # lineage never advances — both rounds seed from the original parent file
    assert report.round_reports[1].parent_config == str(parent)
    assert report.final_parent_config == str(parent)
    # never any FORK / APPLY under K=1 with no over-the-line challenger
    decisions = {r.decision for r in ledger.records()
                 if r.record_kind == "evaluation"}
    assert decisions == {"REJECT"}


# ── parse failure does not abort the run ─────────────────────────────────────


def test_parse_failure_round_continues(tmp_path):
    parent = _write_parent(tmp_path)
    ledger = ShadowLedger(tmp_path / "shadow.jsonl")

    def bad_proposer(snapshot):
        return ExtractionResult(proposals=[], error="no JSON array found")

    report = asyncio.run(run_rehearsal(
        parent, rounds=3, task_bed=StubTaskBed(pass_rate=0.5),
        proposer=bad_proposer, ledger=ledger, out_dir=tmp_path / "out"))

    assert len(report.round_reports) == 3          # loop ran to completion
    for rr in report.round_reports:
        assert rr.parse_ok is False
        assert rr.error
        assert rr.n_proposals == 0 and rr.n_gated == 0
        assert rr.candidates == []
        assert rr.baseline_measured            # baseline still measured each round
    # no proposals ever reached the gate → ledger stays empty
    assert ledger.records() == []


# ── infra failure is a REJECT with a valid numeric measured record ───────────


def test_infra_failed_candidate_is_rejected(tmp_path):
    parent = _write_parent(tmp_path)
    ledger = ShadowLedger(tmp_path / "shadow.jsonl")

    report = asyncio.run(run_rehearsal(
        parent, rounds=1, task_bed=_InfraFailCandidateBed(),
        proposer=stub_proposer, ledger=ledger, out_dir=tmp_path / "out"))

    rr = report.round_reports[0]
    assert rr.n_gated >= 1
    cand = rr.candidates[0]
    assert cand.infra_failed is True
    assert cand.decision == "REJECT"
    assert cand.measured["infra_failed"] == 1          # numeric gate satisfied
    assert rr.winner == ""
    # record_evaluation did not raise: a REJECT evaluation row is present
    ev = [r for r in ledger.records() if r.record_kind == "evaluation"]
    assert ev and ev[0].decision == "REJECT"


# ── F0 arm: baseline only, no ledger writes ──────────────────────────────────


def test_f0_mode_baseline_only(tmp_path):
    parent = _write_parent(tmp_path)
    ledger = ShadowLedger(tmp_path / "shadow.jsonl")

    report = asyncio.run(run_rehearsal(
        parent, rounds=3, task_bed=StubTaskBed(pass_rate=0.5),
        proposer=stub_proposer, ledger=ledger, out_dir=tmp_path / "out",
        mode="f0"))

    assert len(report.round_reports) == 3
    for rr in report.round_reports:
        assert rr.mode == "f0"
        assert rr.baseline_measured["pass_rate"] == pytest.approx(0.5)
        assert rr.n_proposals == 0 and rr.n_gated == 0
        assert rr.candidates == [] and rr.winner == ""
    # F0 never gates or evaluates — the ledger stays completely empty
    assert ledger.records() == []
    assert report.final_parent_config == str(parent)


# ── min_delta band ───────────────────────────────────────────────────────────


def test_min_delta_blocks_small_gain(tmp_path):
    parent = _write_parent(tmp_path)
    ledger = ShadowLedger(tmp_path / "shadow.jsonl")
    # challenger 0.6 beats parent 0.5, but the 0.1 gain is inside the 0.2 band
    report = asyncio.run(run_rehearsal(
        parent, rounds=1, task_bed=_PathBed(parent_pr=0.5, cand_pr=0.6),
        proposer=stub_proposer, ledger=ledger, out_dir=tmp_path / "out",
        min_delta=0.2))
    rr = report.round_reports[0]
    assert rr.winner == ""
    assert all(c.decision == "REJECT" for c in rr.candidates)
    assert report.final_parent_config == str(parent)


def test_equal_scores_never_promote(tmp_path):
    parent = _write_parent(tmp_path)
    ledger = ShadowLedger(tmp_path / "shadow.jsonl")
    # strict '>' — an exactly-tied challenger does not promote (default min_delta)
    report = asyncio.run(run_rehearsal(
        parent, rounds=1, task_bed=_PathBed(parent_pr=0.5, cand_pr=0.5),
        proposer=stub_proposer, ledger=ledger, out_dir=tmp_path / "out"))
    assert report.round_reports[0].winner == ""
    assert report.final_parent_config == str(parent)
