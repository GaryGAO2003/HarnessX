"""Evaluation bridge — GATED shadow candidate → materialized config → measured.

Covers the materialization restore-chain (the bridge re-derives the gate's own
genotype, never a fresh one), the fail-closed contract, and the import
discipline that keeps the recipe's heavy module off the zero-API test path.
"""

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from experiments.variant_pool.eval_bridge import (
    GaiaTaskBed,
    StubTaskBed,
    TaskBedResult,
    materialize_candidate,
    measured_from_result,
)
from experiments.variant_pool.shadow_evolution import (
    ShadowLedger,
    record_evaluation,
    run_shadow_round,
)
from harnessx.core.harness import HarnessConfig
from harnessx.graph.identity import genotype_hash
from harnessx.graph.snapshot import to_graph

from tests.graph.fixtures import serialized_dict

PROBE_TARGET = "tests.graph.fixtures.RuntimeProbe"
REPO_ROOT = Path(__file__).resolve().parents[2]


def _parent():
    return to_graph(HarnessConfig(processors=[
        serialized_dict(PROBE_TARGET, hook="task_start",
                        singleton_group="probe", order=10),
    ]))


def _rewire(order=42):
    return {"operator": "rewire_ordering",
            "params": {"node_id": "proc:runtime_probe", "order": order}}


def _gated(tmp_path):
    """Run one shadow round that yields exactly one GATED candidate."""
    ledger = ShadowLedger(tmp_path / "shadow.jsonl")
    result = run_shadow_round(_parent(), [_rewire(order=42)], ledger,
                              round_id="r1", materialize=True)
    return ledger, result.records[0]


# ── materialization ──────────────────────────────────────────────────────────


def test_materialize_roundtrips_gate_genotype(tmp_path):
    parent = _parent()
    ledger = ShadowLedger(tmp_path / "shadow.jsonl")
    rec = run_shadow_round(parent, [_rewire(order=42)], ledger,
                           round_id="r1", materialize=True).records[0]
    assert rec.decision == "GATED"
    assert rec.candidate_id == "r1/c0"          # contains '/' — must be sanitized

    config_path = materialize_candidate(parent, rec, tmp_path / "out")

    # written where expected, with the id path-sanitized
    assert config_path.exists()
    assert config_path.name == "config.yaml"
    assert config_path.parent.name == "r1_c0"

    # the reconstructed config re-graphs to the gate's OWN genotype hash — the
    # bridge re-derives the blessed genotype, it does not invent a new one
    reloaded = HarnessConfig.from_yaml_file(config_path)
    assert genotype_hash(to_graph(reloaded)) == rec.genotype_hash


def test_materialize_rejects_non_gated(tmp_path):
    ledger = ShadowLedger(tmp_path / "shadow.jsonl")
    # remove_processor on a non-existent node → hard REJECT (precondition fail)
    rec = run_shadow_round(_parent(), [
        {"operator": "remove_processor", "params": {"node_id": "proc:ghost"}},
    ], ledger, round_id="r1", materialize=False).records[0]
    assert rec.decision == "REJECT"

    with pytest.raises(ValueError, match="GATED"):
        materialize_candidate(_parent(), rec, tmp_path / "out")


# ── stub bed + measured evidence ─────────────────────────────────────────────


def test_stub_bed_aggregates_and_subsets():
    bed = StubTaskBed(results={
        "t1": {"passed": True, "n_pass": 1, "n_att": 1,
               "cost_usd": 0.02, "tokens": 1500},
        "t2": {"passed": False, "n_pass": 0, "n_att": 1,
               "cost_usd": 0.03, "tokens": 2000},
    })

    result = asyncio.run(bed.evaluate(Path("unused.yaml")))
    assert result.pass_rate == pytest.approx(0.5)
    assert result.total_cost_usd == pytest.approx(0.05)
    assert result.total_tokens == 3500
    assert not result.infra_failed

    # task_ids subsets both per_task and the aggregate
    only_t1 = asyncio.run(bed.evaluate(Path("unused.yaml"), task_ids=["t1"]))
    assert set(only_t1.per_task) == {"t1"}
    assert only_t1.pass_rate == pytest.approx(1.0)


def test_stub_bed_fixed_pass_rate():
    bed = StubTaskBed(pass_rate=0.75)
    result = asyncio.run(bed.evaluate(Path("unused.yaml")))
    assert result.pass_rate == pytest.approx(0.75)
    assert result.per_task == {}


def test_measured_from_result_is_all_numeric():
    result = TaskBedResult(
        per_task={"t1": {"passed": True}, "t2": {"passed": False}},
        pass_rate=0.5, total_cost_usd=0.05, total_tokens=3500,
    )
    measured = measured_from_result(result)
    assert measured == {"pass_rate": 0.5, "n_tasks": 2, "cost_usd": 0.05,
                        "tokens": 3500, "truncation_rate": 0.0}
    # every value clears the record_evaluation numeric gate (non-bool number)
    assert all(isinstance(v, (int, float)) and not isinstance(v, bool)
               for v in measured.values())


def test_measured_from_result_scores_truncation_rate():
    # exit_reason=="budget_exceeded" records over the total → the fragility signal
    result = TaskBedResult(
        per_task={
            "t1": {"passed": True, "exit_reason": "done"},
            "t2": {"passed": False, "exit_reason": "budget_exceeded"},
            "t3": {"passed": False, "exit_reason": "budget_exceeded"},
            "t4": {"passed": False, "exit_reason": "loop_detected"},
        },
        pass_rate=0.25,
    )
    assert measured_from_result(result)["truncation_rate"] == pytest.approx(0.5)


def test_measured_from_result_flags_infra_failure():
    measured = measured_from_result(
        TaskBedResult(per_task={}, infra_failed=True, error="boom"))
    assert measured["infra_failed"] == 1
    assert measured["n_tasks"] == 0


def test_measured_evidence_records_an_evaluation(tmp_path):
    """End-to-end: stub bed → measured_from_result → record_evaluation."""
    ledger, gate = _gated(tmp_path)
    bed = StubTaskBed(results={
        "t1": {"passed": False, "cost_usd": 0.01, "tokens": 900},
    })
    measured = measured_from_result(asyncio.run(bed.evaluate(Path("x.yaml"))))

    rec = record_evaluation(ledger, gate.candidate_id,
                            decision="REJECT", measured=measured)
    assert rec.record_kind == "evaluation"
    assert rec.decision == "REJECT"
    rows = [r for r in ledger.records() if r.candidate_id == gate.candidate_id]
    assert [r.record_kind for r in rows] == ["gate", "evaluation"]


def test_non_numeric_measured_is_rejected_by_kernel(tmp_path):
    """Sanity: the kernel gate still rejects advisory-only evidence."""
    ledger, gate = _gated(tmp_path)
    with pytest.raises(ValueError, match="numeric metric"):
        record_evaluation(ledger, gate.candidate_id,
                          decision="REJECT", measured={"note": "looks fine"})


# ── import discipline ────────────────────────────────────────────────────────


def test_import_eval_bridge_does_not_pull_recipe():
    """Importing the bridge must not drag in the 9000-line recipe module.

    Checked in a fresh interpreter: within this pytest process a sibling test
    may already have imported ``recipe.*``, so ``sys.modules`` here is not a
    clean witness. The subprocess proves the bridge's *own* top-level imports
    stay recipe-free.
    """
    code = (
        "import sys\n"
        "from pathlib import Path\n"
        "import experiments.variant_pool.eval_bridge as eb\n"
        "leaked = sorted(m for m in sys.modules if m.startswith('recipe'))\n"
        "assert not leaked, ('import', leaked)\n"
        # constructing the real bed must stay recipe-free too (all recipe
        # imports are deferred to evaluate())
        "eb.GaiaTaskBed('m', 'mm', 'p', Path('x.json'),\n"
        "               max_cost=0.5, max_steps=20, out_dir=Path('o'))\n"
        "leaked2 = sorted(m for m in sys.modules if m.startswith('recipe'))\n"
        "assert not leaked2, ('construct', leaked2)\n"
        "print('OK')\n"
    )
    env = {**os.environ, "PYTHONPATH": str(REPO_ROOT), "PYTHONUTF8": "1"}
    proc = subprocess.run([sys.executable, "-c", code],
                          capture_output=True, text=True,
                          cwd=str(REPO_ROOT), env=env)
    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    assert "OK" in proc.stdout


def test_gaia_bed_constructs_and_stores_params():
    """GaiaTaskBed.__init__ is pure attribute storage (no imports, no IO).

    The recipe-free guarantee is proven in a clean interpreter by
    ``test_import_eval_bridge_does_not_pull_recipe``; here we only pin the
    constructor's stored state.
    """
    bed = GaiaTaskBed(
        model="anthropic/claude-sonnet-4-6",
        meta_model="anthropic/claude-opus-4-1",
        provider_id="test",
        data_path=Path("recipe/gaia_evolver/data/calib6.json"),
        max_cost=0.5,
        max_steps=20,
        out_dir=Path("unused"),
    )
    assert bed._pass_k == 1
    assert bed._concurrency == 2
    assert bed._max_steps == 20
    # footprint collection is opt-in: off unless explicitly requested, so the
    # default eval path never attaches an ObservationProcessor or pays for it.
    assert bed._collect_footprints is False
    on = GaiaTaskBed(
        model="m", meta_model="mm", provider_id="test",
        data_path=Path("x.json"), max_cost=0.5, max_steps=20,
        out_dir=Path("unused"), collect_footprints=True,
    )
    assert on._collect_footprints is True


def test_materialize_grafts_base_config_fields(tmp_path):
    """A materialized candidate must carry the parent's non-graph fields
    (tool_registry etc.) - the graph only owns the composition layer."""
    import yaml

    from experiments.variant_pool.eval_bridge import graft_base_fields

    base = {"tool_registry": {"builtin": ["Read"]},
            "workspace": {"root": "r"},
            "processors": [{"_target_": "old.Gone"}]}
    projected = {"processors": [{"_target_": "new.Proc"}]}
    out = graft_base_fields(projected, base)
    assert out["tool_registry"] == {"builtin": ["Read"]}
    assert out["workspace"] == {"root": "r"}
    assert out["processors"] == [{"_target_": "new.Proc"}]   # projection wins

    base_path = tmp_path / "base.yaml"
    base_path.write_text(yaml.safe_dump(base), encoding="utf-8")
    out2 = graft_base_fields(projected, base_path)
    assert out2["tool_registry"] == {"builtin": ["Read"]}
    assert graft_base_fields(projected, None) == projected


def test_graft_excludes_tracer(tmp_path):
    """tracer is run infrastructure, never grafted - a shared journal session
    accumulates cost across evaluate() calls (observed live: candidate costs
    formed an arithmetic progression)."""
    from experiments.variant_pool.eval_bridge import graft_base_fields

    base = {"tool_registry": {"builtin": ["Read"]},
            "tracer": {"_target_": "harnessx.tracing.journal.HarnessJournal",
                       "base_dir": "old_runs/session"},
            "processors": []}
    out = graft_base_fields({"processors": [{"_target_": "x.P"}]}, base)
    assert "tracer" not in out
    assert out["tool_registry"] == {"builtin": ["Read"]}


# ── per-evaluate isolation (the contamination fix) ───────────────────────────


def test_make_eval_env_isolates_journal_and_workspace(tmp_path):
    """Two evaluations must not share a journal base_dir or a workspace root.

    The live contamination: a shared default journal restored a completed
    session, so each candidate's cost continued the previous one's (an
    arithmetic progression). A per-call base_dir/root is a guaranteed cold
    start. Unit-tested here (not through evaluate) because the recipe rollout
    layer is lazily imported — this keeps the check zero-API and recipe-free.
    """
    from experiments.variant_pool.eval_bridge import _make_eval_env

    j0, w0, d0 = _make_eval_env(tmp_path, 0, Path("out/r1_c0/config.yaml"))
    j1, w1, d1 = _make_eval_env(tmp_path, 1, Path("out/r1_c1/config.yaml"))

    # distinct eval dir, journal and workspace root per call
    assert d0 != d1
    assert j0.base_dir != j1.base_dir
    assert w0.root != w1.root

    # the journal never uses the shared "sessions" default that the harness
    # would re-root at the workspace — base_dir is explicit and under eval_dir
    assert j0.base_dir != "sessions"
    assert Path(j0.base_dir).resolve().parent == d0.resolve()
    assert Path(j0.base_dir).is_dir()
    # workspace root is isolated under the same eval dir
    assert w0.root.resolve().parent == d0.resolve()
    assert w0.root.is_dir()


def test_write_eval_records_strips_private_keys(tmp_path):
    """records.jsonl is the audit ledger; private (_-prefixed) keys — the live
    Harness/HarnessResult objects _run_task_pass_k leaves on the record — must
    never reach it (unserialisable, and not evidence)."""
    from experiments.variant_pool.eval_bridge import (
        _clean_merged_record,
        _write_eval_records,
    )

    merged = {
        "task_id": "t1", "passed": True, "n_pass": 1, "n_att": 1,
        "cost_usd": 0.37, "total_tokens": 1500, "steps": 4,
        "exit_reason": "done", "_harness": object(), "_result": object(),
    }
    assert _clean_merged_record(merged) == {
        "task_id": "t1", "passed": True, "n_pass": 1, "n_att": 1,
        "cost_usd": 0.37, "total_tokens": 1500, "steps": 4, "exit_reason": "done",
    }

    path = _write_eval_records(
        tmp_path / "eval" / "records.jsonl",
        [merged, {"task_id": "t2", "passed": False, "_result": object()}],
    )
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    row0 = json.loads(lines[0])
    assert row0["task_id"] == "t1"
    assert row0["cost_usd"] == 0.37
    assert row0["exit_reason"] == "done"
    assert not any(k.startswith("_") for k in row0)


# ── S5 footprint collection: serialization round-trip (metric 9 input) ───────


def test_footprint_row_roundtrips_to_intersection(tmp_path):
    """The per-attempt row the bridge dumps under --collect-footprints must be a
    JSON line that reloads into an intersectable footprint.

    Mirrors ``evaluate``'s ``{"attempt": i, **fp.to_dict()}`` shape without a
    model call: build a trace, map it to graph coordinates via ``compute_footprint``,
    write it as the bridge does, read it back, and confirm the touched nodes still
    drive ``intersects_footprint`` — the exact call metric 9 makes."""
    from harnessx.bundles import context
    from harnessx.core.builder import HarnessBuilder
    from harnessx.graph import compute_footprint, intersects_footprint
    from harnessx.graph.observer import HookObservation, TaskTrace

    snapshot = to_graph((HarnessBuilder() | context).build())
    trace = TaskTrace(task_id="t1", variant_id="V0")
    trace.record(HookObservation(step_id=1, hook_name="before_model",
                                 processor_label="model"))
    trace.record(HookObservation(step_id=2, hook_name="after_model",
                                 processor_label="model", tools_called=["Bash"]))
    fp = compute_footprint(trace, snapshot)
    assert fp.touched_node_ids  # a non-empty footprint to intersect against

    # Write exactly as the bridge does, one JSON line per (task, attempt).
    fp_path = tmp_path / "footprints.jsonl"
    fp_path.write_text(json.dumps({"attempt": 0, **fp.to_dict()}) + "\n",
                       encoding="utf-8")

    row = json.loads(fp_path.read_text(encoding="utf-8").strip())
    assert row["attempt"] == 0
    assert row["task_id"] == "t1"
    nodes = set(row["touched_node_ids"])
    edges = set(row["observed_edge_keys"])
    assert nodes == fp.touched_node_ids
    # a danger set that shares one touched node intersects; a disjoint one does not
    one = next(iter(nodes))
    assert intersects_footprint({one}, set(), nodes, edges)
    assert not intersects_footprint({"proc:__absent__"}, set(), nodes, edges)
