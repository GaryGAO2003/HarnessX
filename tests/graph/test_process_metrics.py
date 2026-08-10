"""Nine process metrics — computed off a real dry-run rehearsal (zero API).

Each test drives ``run_rehearsal`` (stub proposer + a stub / path-keyed task
bed) to produce genuine ``report.json`` + ``shadow.jsonl`` inputs, then asserts
the metrics against the known plot.  Same directory / style as
``tests/graph/test_rehearsal.py``; no model call anywhere.
"""

import asyncio
from pathlib import Path

import pytest

from experiments.variant_pool.eval_bridge import StubTaskBed, TaskBedResult
from experiments.variant_pool.process_metrics import compute_process_metrics, render_text
from experiments.variant_pool.proposer import ExtractionResult
from experiments.variant_pool.rehearsal import run_rehearsal, stub_proposer
from experiments.variant_pool.shadow_evolution import ShadowLedger
from harnessx.core.harness import HarnessConfig

from tests.graph.fixtures import serialized_dict

PROBE_TARGET = "tests.graph.fixtures.RuntimeProbe"


def _write_parent(tmp_path):
    """A legal single-processor parent config.yaml (order=10)."""
    config = HarnessConfig(processors=[
        serialized_dict(PROBE_TARGET, hook="task_start", singleton_group="probe", order=10),
    ])
    path = tmp_path / "parent.yaml"
    config.to_yaml_file(path)
    return path


class _PathBed:
    """pass_rate keyed on whether the config is a materialized candidate.

    Materialized candidates live under ``.../rounds/rN/...``; the parent baseline
    does not — so one bed hands the parent one score and every challenger another.
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


def _run(parent, *, rounds, bed, proposer, ledger_path, out_dir, **kw):
    ledger = ShadowLedger(ledger_path)
    return asyncio.run(run_rehearsal(
        parent, rounds=rounds, task_bed=bed, proposer=proposer,
        ledger=ledger, out_dir=out_dir, **kw))


# ── no-generation plot: constant 0.5 bed, nothing ever promotes ──────────────


def test_no_generation_plot(tmp_path):
    parent = _write_parent(tmp_path)
    out, ledger_path = tmp_path / "out", tmp_path / "shadow.jsonl"
    _run(parent, rounds=2, bed=StubTaskBed(pass_rate=0.5),
         proposer=stub_proposer, ledger_path=ledger_path, out_dir=out)

    m = compute_process_metrics(out / "report.json", ledger_path)

    # all B-rounds parsed, no promotion, no reward
    assert m["parse_rate"]["value"] == 1.0
    assert m["totals"]["apply"] == 0
    assert m["reward"]["reward_total"] == 0.0
    assert m["reward"]["reward_per_evaluation"] == 0.0
    assert m["reward"]["reward_per_1k_tokens"] == 0.0
    # first-improvement never happens → null + a reason
    assert m["time_to_first_improvement"]["round"] is None
    assert m["time_to_first_improvement"]["reason"]
    # every gated candidate was materialized → its file re-hashes to record
    assert m["roundtrip_rate"]["value"] == 1.0
    assert m["roundtrip_rate"]["candidates"] >= 1
    assert m["roundtrip_rate"]["failures"] == []
    assert m["build_smoke_rate"]["value"] == 1.0
    # gate saw only GATED rows here (stub proposes one legal rewire per round)
    assert m["gate_pass_rate"]["value"] == 1.0
    assert m["gate_pass_rate"]["rejection_breakdown"] == {}
    # parent never advances → the same nudge → exactly one distinct genotype
    assert m["genotype_diversity"]["cumulative_distinct_genotypes"] == 1
    assert m["genotype_diversity"]["per_round"][0]["ratio"] == 1.0
    # render is a plain str (ASCII table, never crashes)
    assert isinstance(render_text(m), str) and "parse_rate" in render_text(m)


# ── generation plot: a challenger at 0.9 beats a 0.3 parent → APPLY ──────────


def test_generation_plot(tmp_path):
    parent = _write_parent(tmp_path)
    out, ledger_path = tmp_path / "out", tmp_path / "shadow.jsonl"
    _run(parent, rounds=1, bed=_PathBed(parent_pr=0.3, cand_pr=0.9),
         proposer=stub_proposer, ledger_path=ledger_path, out_dir=out)

    m = compute_process_metrics(out / "report.json", ledger_path)

    assert m["totals"]["apply"] == 1
    assert m["time_to_first_improvement"]["round"] == 0
    assert m["time_to_first_improvement"]["cumulative_wall_clock_s"] is not None
    # single APPLY: 0.9 candidate − 0.3 parent
    assert m["reward"]["reward_total"] == pytest.approx(0.6)
    assert m["reward"]["reward_per_evaluation"] > 0
    # one baseline eval + one candidate eval → denominator 2
    assert m["reward"]["evaluations_incl_baseline"] == 2


# ── parse-failure plot: proposer errors out every round ──────────────────────


def test_parse_failure_lowers_parse_rate(tmp_path):
    parent = _write_parent(tmp_path)
    out, ledger_path = tmp_path / "out", tmp_path / "shadow.jsonl"

    def bad(snapshot):
        return ExtractionResult(proposals=[], error="no JSON array found")

    _run(parent, rounds=2, bed=StubTaskBed(pass_rate=0.5),
         proposer=bad, ledger_path=ledger_path, out_dir=out)

    m = compute_process_metrics(out / "report.json", ledger_path)
    assert m["parse_rate"]["value"] < 1.0
    assert m["parse_rate"]["b_rounds"] == 2
    assert m["parse_rate"]["parsed_rounds"] == 0


# ── roundtrip fail branch: a materialized config is mutated post-hoc ─────────


def test_roundtrip_fail_on_mutated_config(tmp_path):
    parent = _write_parent(tmp_path)
    out, ledger_path = tmp_path / "out", tmp_path / "shadow.jsonl"
    report = _run(parent, rounds=1, bed=StubTaskBed(pass_rate=0.5),
                  proposer=stub_proposer, ledger_path=ledger_path, out_dir=out)

    # Rewrite one materialized candidate with a different _order_ so it re-graphs
    # to a genotype that no longer matches the hash the ledger recorded.
    victim = Path(report.round_reports[0].candidates[0].config_path)
    HarnessConfig(processors=[
        serialized_dict(PROBE_TARGET, hook="task_start", singleton_group="probe", order=999),
    ]).to_yaml_file(victim)

    m = compute_process_metrics(out / "report.json", ledger_path)
    assert m["roundtrip_rate"]["value"] < 1.0
    assert m["roundtrip_rate"]["failures"]
    assert "mismatch" in m["roundtrip_rate"]["failures"][0]["reason"]


# ── metrics 8 / 9: present, null, with a non-empty standing reason ───────────


def test_deferred_metrics_present_but_null(tmp_path):
    parent = _write_parent(tmp_path)
    out, ledger_path = tmp_path / "out", tmp_path / "shadow.jsonl"
    _run(parent, rounds=1, bed=StubTaskBed(pass_rate=0.5),
         proposer=stub_proposer, ledger_path=ledger_path, out_dir=out)

    m = compute_process_metrics(out / "report.json", ledger_path)
    for key in ("holdout_regression_rate", "selective_retest_savings"):
        assert key in m
        assert m[key]["value"] is None
        assert m[key]["reason"]


# ── malformed ledger line is counted, never silently dropped ─────────────────


def test_bad_ledger_line_counted_in_skipped(tmp_path):
    parent = _write_parent(tmp_path)
    out, ledger_path = tmp_path / "out", tmp_path / "shadow.jsonl"
    _run(parent, rounds=1, bed=StubTaskBed(pass_rate=0.5),
         proposer=stub_proposer, ledger_path=ledger_path, out_dir=out)

    with ledger_path.open("a", encoding="utf-8") as fh:
        fh.write("{ this is not valid json }\n")

    m = compute_process_metrics(out / "report.json", ledger_path)
    assert len(m["skipped_inputs"]) >= 1
    assert any(s.get("source") == "ledger" for s in m["skipped_inputs"])
