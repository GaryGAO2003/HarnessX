"""Nine process metrics — computed off a real dry-run rehearsal (zero API).

Each test drives ``run_rehearsal`` (stub proposer + a stub / path-keyed task
bed) to produce genuine ``report.json`` + ``shadow.jsonl`` inputs, then asserts
the metrics against the known plot.  Same directory / style as
``tests/graph/test_rehearsal.py``; no model call anywhere.
"""

import asyncio
import json
from pathlib import Path

import pytest

from experiments.variant_pool.eval_bridge import StubTaskBed, TaskBedResult
from experiments.variant_pool.process_metrics import (
    SELECTIVE_NO_FOOTPRINTS_REASON,
    _load_footprints,
    compute_process_metrics,
    render_text,
)
from experiments.variant_pool.proposer import ExtractionResult
from experiments.variant_pool.rehearsal import run_rehearsal, stub_proposer
from experiments.variant_pool.shadow_evolution import ShadowLedger
from harnessx.core.harness import HarnessConfig
from harnessx.graph import CoverageFootprint, NodeType, to_graph

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


class _HoldoutStub:
    """Holdout bed: distinct per_task maps for the outgoing parent (before) vs
    the promoted winner (after), keyed on the ``/rounds/`` path split."""

    def __init__(self, *, before, after):
        self._before = before
        self._after = after

    async def evaluate(self, config_path, task_ids=None):
        is_candidate = "/rounds/" in str(config_path).replace("\\", "/")
        mapping = self._after if is_candidate else self._before
        per_task = {t: {"passed": p} for t, p in mapping.items()}
        n = len(mapping)
        pass_rate = (sum(1 for p in mapping.values() if p) / n) if n else 0.0
        return TaskBedResult(per_task=per_task, pass_rate=pass_rate)


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


# ── metric 8: holdout_regression_rate — real compute + both null branches ────


def test_holdout_regression_rate_computed(tmp_path):
    """A promotion that breaks one holdout task the parent had passed reads a
    regression rate of 1/2 (parent passed t1,t2; winner broke t2) and reports
    the freshly-unlocked task (t3) alongside."""
    parent = _write_parent(tmp_path)
    out, ledger_path = tmp_path / "out", tmp_path / "shadow.jsonl"
    holdout = _HoldoutStub(before={"t1": True, "t2": True, "t3": False},
                           after={"t1": True, "t2": False, "t3": True})

    _run(parent, rounds=1, bed=_PathBed(parent_pr=0.3, cand_pr=0.9),
         proposer=stub_proposer, ledger_path=ledger_path, out_dir=out,
         holdout_bed=holdout)

    m = compute_process_metrics(out / "report.json", ledger_path)
    ho = m["holdout_regression_rate"]
    assert ho["value"] == pytest.approx(0.5)      # 1 regressed / 2 parent-passed
    assert ho["regressed"] == 1
    assert ho["baseline_passed"] == 2
    assert ho["per_apply"][0]["regressed_tasks"] == ["t2"]
    assert ho["per_apply"][0]["unlocked_tasks"] == ["t3"]
    # value branch renders without crashing (ASCII table)
    assert isinstance(render_text(m), str)


def test_holdout_null_reason_no_apply(tmp_path):
    """No promotion at all → null with the 'no APPLY rounds' reason even when a
    holdout bed is wired (it is simply never paid)."""
    parent = _write_parent(tmp_path)
    out, ledger_path = tmp_path / "out", tmp_path / "shadow.jsonl"

    _run(parent, rounds=1, bed=StubTaskBed(pass_rate=0.5),
         proposer=stub_proposer, ledger_path=ledger_path, out_dir=out,
         holdout_bed=_HoldoutStub(before={"t1": True}, after={"t1": True}))

    ho = compute_process_metrics(out / "report.json", ledger_path)["holdout_regression_rate"]
    assert ho["value"] is None
    assert ho["reason"] == "no APPLY rounds"


def test_holdout_null_reason_not_wired_when_apply_without_holdout(tmp_path):
    """A promotion with no holdout bed wired → the second null branch: APPLY
    happened but the round carries no holdout data (a --no-holdout run)."""
    parent = _write_parent(tmp_path)
    out, ledger_path = tmp_path / "out", tmp_path / "shadow.jsonl"

    _run(parent, rounds=1, bed=_PathBed(parent_pr=0.3, cand_pr=0.9),
         proposer=stub_proposer, ledger_path=ledger_path, out_dir=out)

    ho = compute_process_metrics(out / "report.json", ledger_path)["holdout_regression_rate"]
    assert ho["value"] is None
    assert ho["reason"] == "holdout not wired for this run"


# ── metric 9: selective_retest_savings — real compute + not-collected null ───


def _proc_node_id(parent):
    """The single processor node_id in the parent config's snapshot."""
    snap = to_graph(HarnessConfig.from_yaml_file(parent))
    return next(nid for nid, n in snap.nodes.items()
                if n.node_type is NodeType.PROCESSOR)


def _write_footprints(eval_dir, rows):
    """Write footprints.jsonl exactly as the bridge does ({attempt, **to_dict})."""
    eval_dir.mkdir(parents=True, exist_ok=True)
    with (eval_dir / "footprints.jsonl").open("w", encoding="utf-8") as fh:
        for attempt, fp in rows:
            fh.write(json.dumps({"attempt": attempt, **fp.to_dict()}) + "\n")


def _write_parent_on(tmp_path, hook):
    """A legal single-processor parent whose proc attaches to ``hook`` ('*' = all 8)."""
    config = HarnessConfig(processors=[
        serialized_dict(PROBE_TARGET, hook=hook, singleton_group="probe", order=10),
    ])
    path = tmp_path / "parent.yaml"
    config.to_yaml_file(path)
    return path


def _report_ledger_one_mutate(tmp_path, parent, proc_id, eval_dir):
    """report.json + shadow.jsonl for one GATED mutate_processor_params candidate
    on ``proc_id``, with the round's baseline footprints under ``eval_dir``."""
    report = {
        "mode": "b", "rounds": 1,
        "initial_parent_config": str(parent), "final_parent_config": str(parent),
        "round_reports": [{
            "round_id": "r0", "mode": "b", "parent_config": str(parent),
            "baseline_eval_dir": str(eval_dir),
            "parent_pass_rate": 0.0, "baseline_measured": {}, "candidates": [],
            "n_proposals": 1, "n_gated": 1, "n_evaluated": 1, "wall_clock_s": 0.0,
        }],
    }
    report_path = tmp_path / "report.json"
    report_path.write_text(json.dumps(report), encoding="utf-8")
    ledger_path = tmp_path / "shadow.jsonl"
    ledger_path.write_text(json.dumps({
        "record_kind": "gate", "candidate_id": "r0/c0", "round_id": "r0",
        "parent_genotype": "", "operator": "mutate_processor_params",
        "operator_params": {"node_id": proc_id, "param_changes": {"foo": 1}},
        "decision": "GATED", "genotype_hash": "abc",
    }) + "\n", encoding="utf-8")
    return report_path, ledger_path


def test_selective_retest_savings_computed(tmp_path):
    """Two bed tasks, one whose parent footprint touches the candidate's edit and
    one that does not → exactly half the bed needs a retest → saving of 0.5, with
    the touched task named in the retest set."""
    parent = _write_parent(tmp_path)
    proc_id = _proc_node_id(parent)

    # t_hit's footprint touches the mutated node (∈ danger set); t_miss touches a
    # node absent from the graph, so it can never intersect any danger set.
    eval_dir = tmp_path / "out" / "eval_0000"
    _write_footprints(eval_dir, [
        (0, CoverageFootprint(task_id="t_hit", touched_node_ids={proc_id})),
        (0, CoverageFootprint(task_id="t_miss", touched_node_ids={"proc:__absent__"})),
    ])

    report = {
        "mode": "b", "rounds": 1,
        "initial_parent_config": str(parent), "final_parent_config": str(parent),
        "round_reports": [{
            "round_id": "r0", "mode": "b", "parent_config": str(parent),
            "baseline_eval_dir": str(eval_dir),
            "parent_pass_rate": 0.0, "baseline_measured": {}, "candidates": [],
            "n_proposals": 1, "n_gated": 1, "n_evaluated": 1, "wall_clock_s": 0.0,
        }],
    }
    report_path = tmp_path / "report.json"
    report_path.write_text(json.dumps(report), encoding="utf-8")

    ledger_path = tmp_path / "shadow.jsonl"
    ledger_path.write_text(json.dumps({
        "record_kind": "gate", "candidate_id": "r0/c0", "round_id": "r0",
        "parent_genotype": "", "operator": "mutate_processor_params",
        "operator_params": {"node_id": proc_id, "param_changes": {"foo": 1}},
        "decision": "GATED", "genotype_hash": "abc",
    }) + "\n", encoding="utf-8")

    m = compute_process_metrics(report_path, ledger_path)
    sr = m["selective_retest_savings"]
    assert sr["value"] == pytest.approx(0.5)         # 1 − 1/2 bed retested
    assert sr["candidates_scored"] == 1
    cand = sr["per_candidate"][0]
    assert cand["candidate_id"] == "r0/c0"
    assert cand["bed_tasks"] == 2
    assert cand["retest_tasks"] == ["t_hit"]         # only the intersecting task
    assert cand["savings"] == pytest.approx(0.5)
    # footprints name proc:* nodes → exact processor-level path, not projection
    assert cand["granularity"] == "processor"
    assert "note" not in sr                          # no conservative caveat needed
    # value branch renders without crashing (ASCII table)
    assert isinstance(render_text(m), str)


def test_selective_hook_projection_conservative(tmp_path):
    """Hook-level footprints (no proc:* attribution) + a proc-node edit: the danger
    set is projected through the proc's ATTACHED_TO hook.  One task touches that
    hook, one does not → saving 0.5, flagged hook-projected(conservative)."""
    parent = _write_parent_on(tmp_path, "before_model")
    proc_id = _proc_node_id(parent)                  # attaches to hook:before_model

    eval_dir = tmp_path / "out" / "eval_0000"
    _write_footprints(eval_dir, [
        (0, CoverageFootprint(task_id="t_hit", touched_node_ids={"hook:before_model"})),
        (0, CoverageFootprint(task_id="t_miss", touched_node_ids={"hook:task_end"})),
    ])
    report_path, ledger_path = _report_ledger_one_mutate(tmp_path, parent, proc_id, eval_dir)

    m = compute_process_metrics(report_path, ledger_path)
    sr = m["selective_retest_savings"]
    assert sr["value"] == pytest.approx(0.5)          # 1 − 1/2 bed retested
    assert sr["note"]                                 # projection flagged at top level
    cand = sr["per_candidate"][0]
    assert cand["granularity"] == "hook-projected(conservative)"
    assert cand["retest_tasks"] == ["t_hit"]          # only the task touching the hook
    assert cand["savings"] == pytest.approx(0.5)
    assert isinstance(render_text(m), str)            # projection branch renders


def test_selective_hook_projection_wildcard_zero(tmp_path):
    """A wildcard ('*') processor attaches to all 8 processor hooks → every task's
    footprint intersects the projected hook set → the whole bed retests → saving
    0.0.  Correct at hook granularity: a whole-lifecycle change must retest all."""
    parent = _write_parent_on(tmp_path, "*")
    proc_id = _proc_node_id(parent)                  # attaches to all 8 hooks

    eval_dir = tmp_path / "out" / "eval_0000"
    _write_footprints(eval_dir, [
        (0, CoverageFootprint(task_id="t1", touched_node_ids={"hook:before_model"})),
        (0, CoverageFootprint(task_id="t2", touched_node_ids={"hook:task_end"})),
    ])
    report_path, ledger_path = _report_ledger_one_mutate(tmp_path, parent, proc_id, eval_dir)

    sr = compute_process_metrics(report_path, ledger_path)["selective_retest_savings"]
    assert sr["value"] == pytest.approx(0.0)         # nothing can be skipped
    cand = sr["per_candidate"][0]
    assert cand["granularity"] == "hook-projected(conservative)"
    assert sorted(cand["retest_tasks"]) == ["t1", "t2"]   # every task retests
    assert cand["savings"] == pytest.approx(0.0)


_REHEARSAL_B2 = Path(__file__).resolve().parents[2] / "recipe/gaia_evolver/runs/rehearsal_b2"


@pytest.mark.skipif(not (_REHEARSAL_B2 / "report.json").is_file(),
                    reason="rehearsal_b2 artefacts not present")
def test_selective_real_rehearsal_b2_not_optimistic():
    """Real rehearsal smoke: hook-level footprints (0 proc:* nodes) + 8 gated
    mutate_processor_params candidates.  Before the projection this read a blanket
    saving=1.000; the hook projection drops it below 1.0 and flags every candidate
    hook-projected (procs on before_model/'*' are touched by every task → full
    retest; only a step_start-only proc, never observed, keeps its saving)."""
    m = compute_process_metrics(_REHEARSAL_B2 / "report.json",
                                _REHEARSAL_B2 / "shadow.jsonl")
    sr = m["selective_retest_savings"]
    assert sr["value"] is not None
    assert sr["value"] < 1.0                         # no longer the optimistic artefact
    assert sr["note"]                                # projection flagged
    assert sr["candidates_scored"] == 8
    assert all(c["granularity"] == "hook-projected(conservative)"
               for c in sr["per_candidate"])
    full_retest = [c for c in sr["per_candidate"] if c["savings"] == 0.0]
    assert len(full_retest) >= 6                     # before_model/'*' procs, every task hits
    assert isinstance(render_text(m), str)


def test_selective_null_reason_when_not_collected(tmp_path):
    """A real gated candidate but no footprints on disk (the bed was run without
    --collect-footprints) → honest null with the 'not collected' reason."""
    parent = _write_parent(tmp_path)
    out, ledger_path = tmp_path / "out", tmp_path / "shadow.jsonl"
    _run(parent, rounds=1, bed=StubTaskBed(pass_rate=0.5),
         proposer=stub_proposer, ledger_path=ledger_path, out_dir=out)

    sr = compute_process_metrics(out / "report.json", ledger_path)["selective_retest_savings"]
    assert sr["value"] is None
    assert sr["reason"] == SELECTIVE_NO_FOOTPRINTS_REASON


def test_load_footprints_unions_attempts_and_missing(tmp_path):
    """_load_footprints unions per-attempt rows per task and returns {} when the
    file is absent (the not-collected case)."""
    assert _load_footprints(str(tmp_path / "nope")) == {}

    eval_dir = tmp_path / "eval"
    _write_footprints(eval_dir, [
        (0, CoverageFootprint(task_id="t1", touched_node_ids={"a"},
                              observed_edge_keys={"a→b"})),
        (1, CoverageFootprint(task_id="t1", touched_node_ids={"c"})),
        (0, CoverageFootprint(task_id="t2", touched_node_ids={"d"})),
    ])
    fps = _load_footprints(str(eval_dir))
    assert fps["t1"] == ({"a", "c"}, {"a→b"})        # unioned across attempts
    assert fps["t2"] == ({"d"}, set())


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
