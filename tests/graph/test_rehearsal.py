"""Shadow-evolution rehearsal runner — B / F0 arms, K=1 Global selection.

Zero-API throughout: a ``StubTaskBed`` / small path-keyed beds stand in for the
GAIA task bed and :func:`stub_proposer` stands in for the LLM proposer, so the
whole propose→gate→evaluate→select→advance loop is exercised without a model
call.  Same directory / style as ``tests/graph/test_eval_bridge.py``.
"""

import asyncio
import json
import logging

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


class _HoldoutStub:
    """Holdout bed with distinct per_task maps for the outgoing parent (before)
    vs the promoted winner (after), keyed on the ``/rounds/`` path split like
    ``_PathBed``.  ``calls`` counts evaluate() so a test can prove it was — or
    was not — touched (cost discipline)."""

    def __init__(self, *, before, after):
        self._before = before
        self._after = after
        self.calls = 0

    async def evaluate(self, config_path, task_ids=None):
        self.calls += 1
        is_candidate = "/rounds/" in str(config_path).replace("\\", "/")
        mapping = self._after if is_candidate else self._before
        per_task = {t: {"passed": p, "cost_usd": 0.0, "tokens": 10}
                    for t, p in mapping.items()}
        n = len(mapping)
        pass_rate = (sum(1 for p in mapping.values() if p) / n) if n else 0.0
        return TaskBedResult(per_task=per_task, pass_rate=pass_rate)


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
        proposer=stub_proposer, ledger=ledger, out_dir=tmp_path / "out",
        normalize=False))

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


# ── fragility veto (block 2): a fragile top scorer cannot promote ────────────


class _FragilityBed:
    """Parent baseline is clean (no truncation); candidate c0 is the top scorer
    but may truncate (exit_reason budget_exceeded), candidate c1 scores lower.
    Keyed on the candidate-id path segment the runner writes (``.../c0/...``)."""

    def __init__(self, *, c0_trunc, c1_trunc):
        self._c0_trunc = c0_trunc
        self._c1_trunc = c1_trunc

    @staticmethod
    def _bed(pass_rate, truncated):
        er = "budget_exceeded" if truncated else "done"
        return TaskBedResult(
            per_task={"t1": {"passed": True, "exit_reason": er},
                      "t2": {"passed": False, "exit_reason": er}},
            pass_rate=pass_rate)

    async def evaluate(self, config_path, task_ids=None):
        p = str(config_path).replace("\\", "/")
        if "/rounds/" not in p:
            return self._bed(0.5, truncated=False)          # parent: clean
        # key on the candidate segment ONLY (the tmp_path prefix can itself
        # contain "c0" via pytest's dir-name truncation, so never match on it)
        seg = p.split("/rounds/", 1)[-1]
        if "c0" in seg:
            return self._bed(0.9, truncated=self._c0_trunc)  # top scorer
        return self._bed(0.7, truncated=self._c1_trunc)      # runner-up


def _two_reorders(snapshot):
    return ExtractionResult(proposals=[
        {"operator": "rewire_ordering",
         "params": {"node_id": "proc:runtime_probe", "order": 20},
         "rationale": "c0"},
        {"operator": "rewire_ordering",
         "params": {"node_id": "proc:runtime_probe", "order": 30},
         "rationale": "c1"},
    ], error="")


def test_fragility_veto_promotes_clean_runner_up(tmp_path):
    parent = _write_parent(tmp_path)
    ledger = ShadowLedger(tmp_path / "shadow.jsonl")
    # c0 has the highest pass_rate (0.9) but truncates; the clean c1 (0.7) wins.
    report = asyncio.run(run_rehearsal(
        parent, rounds=1, task_bed=_FragilityBed(c0_trunc=True, c1_trunc=False),
        proposer=_two_reorders, ledger=ledger, out_dir=tmp_path / "out"))

    rr = report.round_reports[0]
    applied = [c for c in rr.candidates if c.decision == "APPLY"]
    assert len(applied) == 1
    assert applied[0].measured["pass_rate"] == pytest.approx(0.7)   # runner-up
    # the fragile top scorer is REJECTed with a FRAGILITY_VETO rationale naming
    # both truncation rates
    veto = [r for r in ledger.records()
            if r.record_kind == "evaluation" and "FRAGILITY_VETO" in r.rationale]
    assert len(veto) == 1
    assert veto[0].measured["pass_rate"] == pytest.approx(0.9)
    assert veto[0].measured["truncation_rate"] == pytest.approx(1.0)
    assert "1.000" in veto[0].rationale and "0.000" in veto[0].rationale


def test_fragility_veto_all_fragile_no_winner(tmp_path):
    parent = _write_parent(tmp_path)
    ledger = ShadowLedger(tmp_path / "shadow.jsonl")
    # both candidates truncate above the clean parent baseline → none eligible
    report = asyncio.run(run_rehearsal(
        parent, rounds=1, task_bed=_FragilityBed(c0_trunc=True, c1_trunc=True),
        proposer=_two_reorders, ledger=ledger, out_dir=tmp_path / "out"))

    rr = report.round_reports[0]
    assert rr.winner == ""
    assert rr.candidates and all(c.decision == "REJECT" for c in rr.candidates)
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
        mode="f0", normalize=False))

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
        min_delta=0.2, normalize=False))
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
        proposer=stub_proposer, ledger=ledger, out_dir=tmp_path / "out",
        normalize=False))
    assert report.round_reports[0].winner == ""
    assert report.final_parent_config == str(parent)


# ── R arm wiring ─────────────────────────────────────────────────────────────


def test_r_arm_mode_runs_with_random_proposer(tmp_path):
    import random

    from experiments.variant_pool.random_proposer import random_proposer

    parent = _write_parent(tmp_path)
    ledger = ShadowLedger(tmp_path / "shadow.jsonl")
    rng = random.Random(7)

    report = asyncio.run(run_rehearsal(
        parent, rounds=2, task_bed=StubTaskBed(pass_rate=0.5),
        proposer=lambda s: random_proposer(s, rng),
        ledger=ledger, out_dir=tmp_path / "out", mode="r"))

    assert report.mode == "r"
    assert len(report.round_reports) == 2
    for rr in report.round_reports:
        assert rr.mode == "r"
        assert rr.parse_ok            # random source never parse-fails
        assert rr.n_proposals >= 1


def test_cli_dry_run_r_mode(tmp_path):
    from experiments.variant_pool.rehearsal import main

    parent = _write_parent(tmp_path)
    out = tmp_path / "cli_out"
    rc = main(["--parent", str(parent), "--rounds", "1", "--mode", "r",
               "--dry-run", "--seed", "3", "--out-dir", str(out)])
    assert rc == 0

    import json
    report = json.loads((out / "report.json").read_text(encoding="utf-8"))
    assert report["mode"] == "r"
    assert len(report["round_reports"]) == 1


# ── SERPER_API_KEY blind-spot guard (search stack parity) ────────────────────

_ARM_PARITY_FRAGMENT = "arm parity at risk"


def test_warn_if_no_serper_key_warns_and_returns_false(monkeypatch, caplog):
    from experiments.variant_pool.rehearsal import _warn_if_no_serper_key

    monkeypatch.delenv("SERPER_API_KEY", raising=False)
    with caplog.at_level(
        logging.WARNING, logger="experiments.variant_pool.rehearsal"
    ):
        present = _warn_if_no_serper_key()

    assert present is False
    hits = [r for r in caplog.records if _ARM_PARITY_FRAGMENT in r.getMessage()]
    assert len(hits) == 1
    assert hits[0].levelno == logging.WARNING


def test_warn_if_no_serper_key_silent_when_present(monkeypatch, caplog):
    from experiments.variant_pool.rehearsal import _warn_if_no_serper_key

    monkeypatch.setenv("SERPER_API_KEY", "test-key")
    with caplog.at_level(
        logging.WARNING, logger="experiments.variant_pool.rehearsal"
    ):
        present = _warn_if_no_serper_key()

    assert present is True
    assert [r for r in caplog.records if _ARM_PARITY_FRAGMENT in r.getMessage()] == []


def test_cli_dry_run_records_serper_key_and_never_warns(
    tmp_path, monkeypatch, capsys, caplog
):
    """--dry-run needs no key: the summary records serper_key_present for BOTH
    env states, and neither run emits the arm-parity warning (dry-run never
    reaches the real-eval guard)."""
    from experiments.variant_pool.rehearsal import main

    parent = _write_parent(tmp_path)

    def _run_dry(out_name):
        with caplog.at_level(
            logging.WARNING, logger="experiments.variant_pool.rehearsal"
        ):
            rc = main(["--parent", str(parent), "--rounds", "1", "--mode", "r",
                       "--dry-run", "--seed", "3",
                       "--out-dir", str(tmp_path / out_name)])
        assert rc == 0
        return json.loads(capsys.readouterr().out)

    # key absent -> flag False, no warning
    monkeypatch.delenv("SERPER_API_KEY", raising=False)
    caplog.clear()
    summary = _run_dry("o1")
    assert summary["serper_key_present"] is False
    assert [r for r in caplog.records if _ARM_PARITY_FRAGMENT in r.getMessage()] == []

    # key present -> flag True, still no warning under --dry-run
    monkeypatch.setenv("SERPER_API_KEY", "test-key")
    caplog.clear()
    summary = _run_dry("o2")
    assert summary["serper_key_present"] is True
    assert [r for r in caplog.records if _ARM_PARITY_FRAGMENT in r.getMessage()] == []


# ── parent normalization: lineage starts at the genotype fixed point ─────────


def test_partial_metadata_parent_normalizes_to_fixed_point(tmp_path):
    """A pre-v5.3-style parent (partial serialized metadata) drifts on its
    first graph->config cycle; normalize_parent must land it on the fixed
    point so every materialized child passes the independent genotype
    re-hash (metrics roundtrip_rate == 1.0)."""
    from experiments.variant_pool.process_metrics import compute_process_metrics
    from experiments.variant_pool.rehearsal import normalize_parent
    from harnessx.graph import genotype_hash, to_graph

    # partial metadata: only _target_ + _hook_, the pre-v5.3 on-disk shape
    parent = tmp_path / "old_parent.yaml"
    parent.write_text(
        "processors:\n"
        f"- _target_: {PROBE_TARGET}\n"
        "  _hook_: task_start\n",
        encoding="utf-8",
    )

    # normalization is idempotent (fixed point): a second cycle is a no-op
    norm = normalize_parent(parent, tmp_path / "n1")
    norm2 = normalize_parent(norm, tmp_path / "n2")
    h1 = genotype_hash(to_graph(HarnessConfig.from_yaml_file(norm)))
    h2 = genotype_hash(to_graph(HarnessConfig.from_yaml_file(norm2)))
    assert h1 == h2

    # end-to-end: rehearsal (normalize=True default) -> metrics roundtrip 1.0
    ledger = ShadowLedger(tmp_path / "shadow.jsonl")
    out = tmp_path / "out"
    asyncio.run(run_rehearsal(
        parent, rounds=1, task_bed=StubTaskBed(pass_rate=0.5),
        proposer=stub_proposer, ledger=ledger, out_dir=out))
    metrics = compute_process_metrics(out / "report.json", tmp_path / "shadow.jsonl")
    rt = metrics["roundtrip_rate"]
    assert rt["candidates"] >= 1
    assert rt["value"] == 1.0, rt


def test_proposer_transport_exception_survives_round(tmp_path):
    """An auth/network exception from the proposer must not abort the
    rehearsal - it is a round-level parse failure (observed live: a stale
    gateway key killed the whole run at round 0)."""
    parent = _write_parent(tmp_path)
    ledger = ShadowLedger(tmp_path / "shadow.jsonl")

    def exploding_proposer(snapshot):
        raise RuntimeError("AuthenticationError: invalid proxy token")

    report = asyncio.run(run_rehearsal(
        parent, rounds=2, task_bed=StubTaskBed(pass_rate=0.5),
        proposer=exploding_proposer, ledger=ledger, out_dir=tmp_path / "out"))

    assert len(report.round_reports) == 2
    for rr in report.round_reports:
        assert rr.parse_ok is False
        assert "proposer transport" in rr.error
        assert rr.baseline_measured
    assert ledger.records() == []


def test_normalize_grafts_non_graph_fields(tmp_path):
    """tool_registry / workspace survive normalization with original values -
    without the graft the normalized parent runs toolless (live smoke: every
    task FAILed at step 2)."""
    import yaml

    from experiments.variant_pool.rehearsal import normalize_parent

    parent = tmp_path / "parent.yaml"
    cfg = HarnessConfig(processors=[
        serialized_dict(PROBE_TARGET, hook="task_start",
                        singleton_group="probe", order=10)])
    d = yaml.safe_load(cfg.to_yaml())
    d["tool_registry"] = {"builtin": ["Read", "Bash"],
                          "custom": ["x.y.serper_tool"]}
    d["workspace"] = {"root": "D:/tmp/wsx", "agent_id": "gaia"}
    parent.write_text(yaml.safe_dump(d), encoding="utf-8")

    norm = normalize_parent(parent, tmp_path / "out")
    nd = yaml.safe_load(norm.read_text(encoding="utf-8"))
    assert nd["tool_registry"] == d["tool_registry"]
    assert nd["workspace"] == d["workspace"]
    assert nd["processors"]                      # composition layer normalized


# ── holdout regression read-out (metric 8 wiring) ────────────────────────────


def test_apply_round_records_holdout_before_after(tmp_path):
    """A promoting round evaluates the outgoing parent AND the winner on the
    holdout bed, landing both per_task read-outs on the RoundReport."""
    parent = _write_parent(tmp_path)
    ledger = ShadowLedger(tmp_path / "shadow.jsonl")
    holdout = _HoldoutStub(before={"t1": True, "t2": True, "t3": False},
                           after={"t1": True, "t2": False, "t3": True})

    report = asyncio.run(run_rehearsal(
        parent, rounds=1, task_bed=_PathBed(parent_pr=0.3, cand_pr=0.9),
        proposer=stub_proposer, ledger=ledger, out_dir=tmp_path / "out",
        holdout_bed=holdout))

    rr = report.round_reports[0]
    assert rr.winner                            # promotion happened
    assert holdout.calls == 2                    # outgoing parent + winner, once each
    # per_task detail rides inside each field (regression is scored per task)
    assert rr.holdout_before["per_task"]["t2"]["passed"] is True
    assert rr.holdout_after["per_task"]["t2"]["passed"] is False
    assert rr.holdout_after["per_task"]["t3"]["passed"] is True


def test_no_apply_round_leaves_holdout_empty(tmp_path):
    """No promotion → the holdout bed is never paid and both fields stay empty."""
    parent = _write_parent(tmp_path)
    ledger = ShadowLedger(tmp_path / "shadow.jsonl")
    holdout = _HoldoutStub(before={"t1": True}, after={"t1": True})

    report = asyncio.run(run_rehearsal(
        parent, rounds=2, task_bed=StubTaskBed(pass_rate=0.5),
        proposer=stub_proposer, ledger=ledger, out_dir=tmp_path / "out",
        holdout_bed=holdout))

    for rr in report.round_reports:
        assert rr.winner == ""
        assert rr.holdout_before == {} and rr.holdout_after == {}
    assert holdout.calls == 0                     # cost discipline: no APPLY, no pay


def test_f0_never_evaluates_holdout(tmp_path):
    """f0 never promotes, so it must never touch the holdout bed (a bed that
    raises on evaluate proves the round loop never calls it under f0)."""
    parent = _write_parent(tmp_path)
    ledger = ShadowLedger(tmp_path / "shadow.jsonl")

    class _ExplodingHoldout:
        async def evaluate(self, config_path, task_ids=None):
            raise AssertionError("f0 must never touch the holdout bed")

    report = asyncio.run(run_rehearsal(
        parent, rounds=2, task_bed=StubTaskBed(pass_rate=0.5),
        proposer=stub_proposer, ledger=ledger, out_dir=tmp_path / "out",
        mode="f0", normalize=False, holdout_bed=_ExplodingHoldout()))

    assert len(report.round_reports) == 2
    for rr in report.round_reports:
        assert rr.holdout_before == {} and rr.holdout_after == {}


def test_cli_holdout_flags_parse_and_dry_run_smoke(tmp_path):
    """--holdout-data-path / --no-holdout reach the arg layer with the right
    defaults, and a dry-run with --no-holdout still completes (dry-run builds
    no holdout bed either way — zero-API behaviour unchanged)."""
    from experiments.variant_pool.rehearsal import _build_parser, main

    args = _build_parser().parse_args(["--parent", "p", "--out-dir", "o"])
    assert args.holdout_data_path.endswith("holdout6.json")
    assert args.no_holdout is False

    parent = _write_parent(tmp_path)
    rc = main(["--parent", str(parent), "--rounds", "1", "--mode", "b",
               "--dry-run", "--no-holdout", "--out-dir", str(tmp_path / "o")])
    assert rc == 0


def test_cli_collect_footprints_flag_defaults_off_and_parses(tmp_path):
    """--collect-footprints reaches the arg layer (default off) and a dry-run with
    it set still completes — StubTaskBed ignores it, so zero-API behaviour is
    unchanged (the flag only wires the real GaiaTaskBed)."""
    from experiments.variant_pool.rehearsal import _build_parser, main

    args = _build_parser().parse_args(["--parent", "p", "--out-dir", "o"])
    assert args.collect_footprints is False
    args_on = _build_parser().parse_args(
        ["--parent", "p", "--out-dir", "o", "--collect-footprints"])
    assert args_on.collect_footprints is True

    parent = _write_parent(tmp_path)
    rc = main(["--parent", str(parent), "--rounds", "1", "--mode", "b", "--dry-run",
               "--collect-footprints", "--out-dir", str(tmp_path / "o")])
    assert rc == 0


def test_round_report_carries_baseline_eval_dir(tmp_path):
    """RoundReport records the parent baseline's eval_dir so metric 9 can locate
    its footprints.jsonl — whatever the bed returns ("" for StubTaskBed)."""

    class _DirBed:
        async def evaluate(self, config_path, task_ids=None):
            return TaskBedResult(per_task={"t1": {"passed": True}}, pass_rate=0.6,
                                 eval_dir="some/eval/dir")

    parent = _write_parent(tmp_path)
    report = asyncio.run(run_rehearsal(
        parent, rounds=1, task_bed=_DirBed(), proposer=stub_proposer,
        ledger=ShadowLedger(tmp_path / "shadow.jsonl"), out_dir=tmp_path / "out"))
    assert report.round_reports[0].baseline_eval_dir == "some/eval/dir"

    report2 = asyncio.run(run_rehearsal(
        parent, rounds=1, task_bed=StubTaskBed(pass_rate=0.5), proposer=stub_proposer,
        ledger=ShadowLedger(tmp_path / "shadow2.jsonl"), out_dir=tmp_path / "out2"))
    assert report2.round_reports[0].baseline_eval_dir == ""
