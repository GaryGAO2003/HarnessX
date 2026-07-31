"""Unit tests for the T1 deep metrics (A-H) in :mod:`deep_metrics`.

Uses tiny synthetic ``pool_state.json`` fixtures written to ``tmp_path`` — no
dependency on the real ``runs/**``. Run explicitly (the repo's pytest testpaths is
``tests/`` only)::

    .venv312\\Scripts\\python.exe -m pytest experiments/analysis/test_deep_metrics.py -q
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))  # make deep_metrics / _poolscan importable
import deep_metrics as dm
import _poolscan as ps


@pytest.fixture(autouse=True)
def _clear_state_cache():
    dm._STATE_CACHE.clear()
    yield
    dm._STATE_CACHE.clear()


def write_round(run_dir: Path, rnd: int, variant_count: int, shipped: bool,
                routing: dict, measurements: dict, *, decisions=None, diagnostics=None,
                denom: int = 4, forked=None, retired=None) -> None:
    rd = Path(run_dir) / f"R{rnd}"
    rd.mkdir(parents=True, exist_ok=True)
    state = {
        "round": rnd,
        "variant_count": variant_count,
        "evaluated_task_denominator": denom,
        "shipped": shipped,
        "forked": forked or [],
        "retired": retired or [],
        "idle": 0,
        "routing": routing,
        "active_pool_measurements": measurements,
        "decisions": decisions or {},
        "candidate_diagnostics": diagnostics or {},
    }
    (rd / "pool_state.json").write_text(json.dumps(state), encoding="utf-8")


@pytest.fixture
def multi_run(tmp_path) -> Path:
    """4-task, 4-round, 2-variant synthetic pool (denominator=4)."""
    run = tmp_path / "synthmulti"
    # R0: baseline single variant, no ship. pool_pass=2 (t1,t3) -> 50%
    write_round(run, 0, 1, False,
                {"V0": ["t1", "t2", "t3", "t4"]},
                {"V0": {"t1": [1, 2], "t2": [0, 2], "t3": [1, 2], "t4": [0, 2]}})
    # R1: fork V1 (ship). pool_pass=3 -> 75%. cross-variant regression on t2.
    write_round(run, 1, 2, True,
                {"V0": ["t1", "t2"], "V1": ["t3", "t4"]},
                {"V0": {"t1": [1, 2], "t2": [1, 2]}, "V1": {"t3": [1, 2], "t4": [0, 2]}},
                decisions={"V1": "fork"}, forked=["V1"],
                diagnostics={"C-R1": {"archive_reason": "FORK: improved=['t3'] regressed=['t2']",
                                      "failed_stage": None, "variant_id": "V1"}})
    # R2: no ship, same routing. pool_pass=3 -> 75%  (Δ vs R1 = 0.0, noise)
    write_round(run, 2, 2, False,
                {"V0": ["t1", "t2"], "V1": ["t3", "t4"]},
                {"V0": {"t1": [1, 2], "t2": [0, 2]}, "V1": {"t3": [1, 2], "t4": [1, 2]}})
    # R3: no ship, t3 re-routed to V0. pool_pass=2 -> 50%  (Δ vs R2 = -25.0, noise)
    write_round(run, 3, 2, False,
                {"V0": ["t1", "t2", "t3"], "V1": ["t4"]},
                {"V0": {"t1": [0, 2], "t2": [0, 2], "t3": [1, 2]}, "V1": {"t4": [1, 2]}},
                decisions={"V0": "reject"},
                diagnostics={"C-R3": {"archive_reason": "SEESAW_REGRESSION: improved=[] regressed=['t1','t2']",
                                      "failed_stage": "SEESAW_REGRESSION", "variant_id": "V0"}})
    return run


@pytest.fixture
def single_run(tmp_path) -> Path:
    """2-round single-variant pool (mirrors a zero-fork run)."""
    run = tmp_path / "synthsingle"
    write_round(run, 0, 1, False, {"V0": ["t1", "t2", "t3", "t4"]},
                {"V0": {"t1": [1, 2], "t2": [0, 2], "t3": [1, 2], "t4": [0, 2]}})
    # apply-in-place ship, still K=1, with an (in-variant) diagnostic carrying a regressed list
    write_round(run, 1, 1, True, {"V0": ["t1", "t2", "t3", "t4"]},
                {"V0": {"t1": [1, 2], "t2": [1, 2], "t3": [1, 2], "t4": [0, 2]}},
                decisions={"V0": "apply"},
                diagnostics={"C-R1": {"archive_reason": "APPLY: improved=['t2'] regressed=['t4']",
                                      "failed_stage": None, "variant_id": "V0"}})
    return run


LEVELS = {"t1": 1, "t2": 1, "t3": 2, "t4": 3}


# --------------------------------------------------------------------------- A
def test_A_noise_floor(multi_run):
    res = dm.noise_floor(ps.scan_run(multi_run))
    assert res["n"] == 2  # R2 and R3 are the no-ship rounds with a predecessor
    assert [s["round"] for s in res["samples"]] == [2, 3]
    assert res["samples"][0]["delta"] == pytest.approx(0.0)
    assert res["samples"][1]["delta"] == pytest.approx(-25.0)
    assert res["mean"] == pytest.approx(-12.5)
    assert res["sd_pop"] == pytest.approx(12.5)          # ddof=0 (hand-calc convention)
    assert res["sd_sample"] == pytest.approx(17.67767, rel=1e-4)  # ddof=1
    assert res["abs_mean"] == pytest.approx(12.5)
    assert res["abs_max"] == pytest.approx(25.0)


def test_A_max_round_truncation(multi_run):
    # Excluding R3 leaves a single no-ship sample (R2).
    res = dm.noise_floor(ps.scan_run(multi_run), max_round=2)
    assert res["n"] == 1 and res["samples"][0]["round"] == 2


# --------------------------------------------------------------------------- B
def test_B_best_of_pool(multi_run):
    res = dm.best_of_pool(ps.scan_run(multi_run))
    assert res["union_solved"] == 4 and res["denominator"] == 4
    assert res["union_pct"] == pytest.approx(100.0)
    assert res["best_single_round"] == 1 and res["best_single_pass"] == 3
    assert res["best_single_pct"] == pytest.approx(75.0)
    assert res["union_equiv_pass_at"] == 2 * 4  # pass@(2 x settled_rounds)


# --------------------------------------------------------------------------- C
def test_C_posthoc(multi_run):
    res = dm.posthoc_optimal_routing(ps.scan_run(multi_run))
    # best rates: t1=.75, t2=.25, t3=1.0, t4=.667 -> sum 2.6667 / 4 = 66.7%
    assert res["tasks_with_history"] == 4
    assert res["posthoc_score"] == pytest.approx(0.75 + 0.25 + 1.0 + 2 / 3)
    assert res["posthoc_pct"] == pytest.approx(100.0 * (0.75 + 0.25 + 1.0 + 2 / 3) / 4)


# --------------------------------------------------------------------------- D
def test_D_protocol_stops(multi_run):
    res = dm.protocol_stops(ps.scan_run(multi_run), patiences=(1, 2, 3))
    s = res["stops"]
    assert s[1]["triggered"] and s[1]["stop_round"] == 0 and s[1]["stop_pct"] == pytest.approx(50.0)
    assert s[2]["triggered"] and s[2]["stop_round"] == 3 and s[2]["stop_pct"] == pytest.approx(50.0)
    assert not s[3]["triggered"] and s[3]["terminal_round"] == 3


# --------------------------------------------------------------------------- E
def test_E_dynamics(multi_run):
    res = dm.variant_dynamics(ps.scan_run(multi_run))
    assert res["single_variant"] is False
    assert set(res["territory_holders"]) == {"V0", "V1"}
    bl = res["birth_vs_latest"]
    assert bl["V0"]["birth_round"] == 0 and bl["V0"]["birth_pct"] == pytest.approx(50.0)
    assert bl["V0"]["latest_round"] == 3 and bl["V0"]["latest_pct"] == pytest.approx(100 / 3)
    assert bl["V1"]["birth_pct"] == pytest.approx(50.0) and bl["V1"]["latest_pct"] == pytest.approx(100.0)
    assert bl["V1"]["overconfidence_gap"] == pytest.approx(-50.0)
    assert res["churn"][1]["changed"] == 2  # t3,t4 leave V0
    assert res["churn"][2]["changed"] == 0  # routing unchanged R1->R2
    assert res["churn"][3]["changed"] == 1  # t3 moves V1->V0
    assert res["survival"]["V0"]["alive_rounds"] == 4 and res["survival"]["V0"]["currently_alive"]
    assert res["survival"]["V1"]["alive_rounds"] == 3


def test_E_single_variant_graceful(single_run):
    res = dm.variant_dynamics(ps.scan_run(single_run))
    assert res["single_variant"] is True
    assert res["territory_holders"] == ["V0"]
    # churn is 0 (V0 owns everything every round)
    assert all(c["changed"] == 0 for c in res["churn"].values())


# --------------------------------------------------------------------------- F
def test_F_m23(multi_run):
    res = dm.m23_manifestation(ps.scan_run(multi_run))
    assert res["data_available"] is True and res["single_variant_run"] is False
    assert res["cross_variant_rounds"] == [1, 3]
    assert res["manifestation_rate"] == pytest.approx(1.0)
    assert res["total_regressed_task_instances"] == 3  # 1 (R1) + 2 (R3)
    assert res["fields_queried"]  # documents which fields were inspected


def test_F_single_variant_cannot_manifest(single_run):
    res = dm.m23_manifestation(ps.scan_run(single_run))
    assert res["single_variant_run"] is True
    # a K=1 diagnostic with a regressed list is NOT a cross-variant manifestation
    assert res["cross_variant_round_count"] == 0
    assert all(m["cross_variant"] is False for m in res["manifest"])


def test_F_no_data_honest(tmp_path):
    run = tmp_path / "nodiag"
    write_round(run, 0, 1, False, {"V0": ["t1"]}, {"V0": {"t1": [1, 2]}}, denom=1)
    res = dm.m23_manifestation(ps.scan_run(run))
    assert res["data_available"] is False  # reported, not invented


# --------------------------------------------------------------------------- G
def test_G_difficulty(multi_run):
    res = dm.difficulty_strata(ps.scan_run(multi_run), LEVELS)
    assert res["available"] and res["level_counts"] == {1: 2, 2: 1, 3: 1}
    r0 = next(pr for pr in res["per_round"] if pr["round"] == 0)["by_level"]
    assert r0[1]["pass"] == 1 and r0[1]["den"] == 2   # L1: t1 pass, t2 fail
    assert r0[2]["pass"] == 1 and r0[2]["den"] == 1   # L2: t3 pass
    assert r0[3]["pass"] == 0 and r0[3]["den"] == 1   # L3: t4 fail
    assert res["unmapped_total"] == 0


def test_G_unavailable(multi_run):
    res = dm.difficulty_strata(ps.scan_run(multi_run), {})
    assert res["available"] is False


# --------------------------------------------------------------------------- H
def test_H_parse_cost_lines():
    text = (
        "15:00:00 [INFO ] recipe.gaia_evolver.run - [R0-V0-active] t1 PASS - steps=5 cost=$0.10 time=10.0s\n"
        "15:00:01 [INFO ] recipe.gaia_evolver.run - [R0-V0-active] t2 FAIL - steps=5 cost=$0.20 time=20.0s\n"
        "15:00:02 [INFO ] recipe.gaia_evolver.run - [R1-V1-active] t3 PASS - steps=5 cost=$0.30 time=30.0s\n"
    )
    recs = dm.parse_cost_lines(text)
    assert len(recs) == 3
    assert recs[0] == {"round": 0, "variant": "V0", "phase": "active", "task": "t1",
                       "verdict": "PASS", "cost": 0.10, "time": 10.0}


def test_H_cost_account(tmp_path, multi_run):
    log = tmp_path / "synth.console.log"
    log.write_text(
        "LAUNCHER_START 2026/07/30 x 15:00:00.00\n"
        "15:00:00 [INFO ] recipe.gaia_evolver.run - [R0-V0-active] t1 PASS - steps=5 cost=$0.10 time=10.0s\n"
        "15:00:00 [INFO ] recipe.gaia_evolver.run - [R0-V0-active] t1 PASS - steps=5 cost=$0.10 time=10.0s\n"  # dup
        "15:00:01 [INFO ] recipe.gaia_evolver.run - [R0-V0-active] t2 FAIL - steps=5 cost=$0.20 time=20.0s\n"
        "15:00:02 [INFO ] recipe.gaia_evolver.run - [R1-V1-active] t3 PASS - steps=5 cost=$0.30 time=30.0s\n"
        "LAUNCHER_EXIT code=0 2026/07/30 x 15:10:00.00\n",
        encoding="utf-8")
    res = dm.cost_account([log], run_dir=multi_run)
    assert res["cost_line_count"] == 3  # duplicate physical line de-duped
    assert res["total_cost"] == pytest.approx(0.60)
    assert res["total_tasks"] == 3
    by_round = {r["round"]: r for r in res["per_round"]}
    assert by_round[0]["cost"] == pytest.approx(0.30) and by_round[0]["tasks"] == 2 and by_round[0]["passes"] == 1
    assert by_round[1]["cost"] == pytest.approx(0.30) and by_round[1]["passes"] == 1
    assert res["wall_clock_seconds"] == pytest.approx(600.0)
    assert res["exit_code"] == 0


def test_H_no_log(multi_run):
    res = dm.cost_account([Path("does-not-exist.console.log")], run_dir=multi_run)
    assert res["console_logs_read"] == [] and res["cost_line_count"] == 0


# ------------------------------------------------------------------- LaTeX / render
def test_latex_tables(multi_run):
    cta = dm.latex_curve_table(ps.scan_run(multi_run))
    assert r"\begin{tabular}" in cta and "R3 &" in cta
    nt = dm.latex_noise_table(dm.noise_floor(ps.scan_run(multi_run)))
    assert "samples $n$ & 2" in nt
    tt = dm.latex_territory_table(dm.variant_dynamics(ps.scan_run(multi_run)))
    assert "V0 &" in tt and "V1 &" in tt


def test_latex_single_variant_note(single_run):
    tt = dm.latex_territory_table(dm.variant_dynamics(ps.scan_run(single_run)))
    assert "single-variant" in tt


def test_render_all_no_crash_on_pending(tmp_path):
    """An in-flight run (a pending/unreadable round) must not crash any section."""
    run = tmp_path / "inflight"
    write_round(run, 0, 1, False, {"V0": ["t1"]}, {"V0": {"t1": [1, 2]}}, denom=1)
    (run / "R1").mkdir()
    (run / "R1" / "pool_state.json").write_text("{ this is not valid json", encoding="utf-8")
    rounds = ps.scan_run(run)
    lines = dm.build_deep_sections(rounds, run, [], {})
    assert any("A. Noise floor" in ln for ln in lines)  # produced sections, no exception
