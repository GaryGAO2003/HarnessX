"""Evaluation bridge — GATED shadow candidate → materialized config → measured.

Covers the materialization restore-chain (the bridge re-derives the gate's own
genotype, never a fresh one), the fail-closed contract, and the import
discipline that keeps the recipe's heavy module off the zero-API test path.
"""

import asyncio
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
    assert measured == {"pass_rate": 0.5, "n_tasks": 2,
                        "cost_usd": 0.05, "tokens": 3500}
    # every value clears the record_evaluation numeric gate (non-bool number)
    assert all(isinstance(v, (int, float)) and not isinstance(v, bool)
               for v in measured.values())


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
