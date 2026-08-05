import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import replay_data as rd                 # noqa: E402
import sim_racing_offline as sa           # noqa: E402
import sim_cold_start_offline as sb       # noqa: E402
from helpers import make_comparison_json  # noqa: E402


def _cell(run, rnd, v, t, passed, atts=None, level=1):
    attempts = atts if atts is not None else [{"passed": passed, "infra_failure": False,
                                               "exit_reason": "done", "steps": 1, "reason": "x"}]
    return {"run": run, "round": rnd, "variant": v, "task": t, "level": level,
            "passed": passed, "n_pass": sum(1 for a in attempts if a["passed"]),
            "n_att": len(attempts), "exit_reason": "done", "infra_failure": False,
            "attempts": attempts}


# --------------------------------------------------------------------------- #
# Sim A pairing construction                                                   #
# --------------------------------------------------------------------------- #
def test_build_variant_pairs_task_matched_round_order():
    # V0 measures t1@r0, t2@r0 ; V1 measures t1@r1, t2@r2. Shared tasks t1,t2.
    cells = [
        _cell("R", 0, "V0", "t1", True),
        _cell("R", 0, "V0", "t2", False),
        _cell("R", 1, "V1", "t1", False),   # candidate round 1 for t1
        _cell("R", 2, "V1", "t2", True),    # candidate round 2 for t2
    ]
    pairs = sa.build_variant_pairs(cells)
    stream = pairs[("V0", "V1")]
    # ordered by candidate round: t1 (r1) then t2 (r2)
    assert stream == [(1, 1, 0), (2, 0, 1)]     # (round, base_pass, cand_pass)
    # reverse pair also present
    assert ("V1", "V0") in pairs


def test_build_variant_pairs_uses_earliest_round_per_task():
    cells = [
        _cell("R", 0, "V0", "t1", True),
        _cell("R", 3, "V0", "t1", False),   # later round ignored (earliest wins)
        _cell("R", 1, "V1", "t1", True),
    ]
    stream = sa.build_variant_pairs(cells)[("V0", "V1")]
    assert stream == [(1, 1, 1)]            # base_pass from r0 (True), not r3


def test_build_aa_pairs_attempt0_vs_attempt1():
    atts = [{"passed": True, "infra_failure": False, "exit_reason": "done", "steps": 1, "reason": "x"},
            {"passed": False, "infra_failure": False, "exit_reason": "b", "steps": 1, "reason": "y"}]
    cells = [_cell("R", 0, "V0", "t1", True, atts=atts),
             _cell("R", 0, "V0", "t2", True)]           # single attempt -> skipped
    aa = sa.build_aa_pairs(cells)
    assert len(aa) == 1
    assert aa[0]["base_pass"] == 1 and aa[0]["cand_pass"] == 0
    assert aa[0]["run"] == "R" and aa[0]["task"] == "t1"


def test_replay_true_effect_small_uplift_rarely_accepts():
    # 100 tasks, candidate wins only ~2pp more -> gate should not ACCEPT.
    cells = []
    for i in range(100):
        base = i < 50                 # base passes half
        cand = i < 51                 # candidate passes one more (+2pp, +1 discordant)
        cells.append(_cell("R", 0, "V0", f"t{i}", base))
        cells.append(_cell("R", 1, "V1", f"t{i}", cand))
    pairs = sa.build_variant_pairs(cells)
    res = sa.replay_true_effect({("V0", "V1"): pairs[("V0", "V1")]}, (0.6,), 0.05, 0.20)
    blk = res["per_p1"]["p1=0.6"]
    assert blk["sprt_decisions"]["ACCEPT"] == 0


# --------------------------------------------------------------------------- #
# Sim B leave-future-out split                                                 #
# --------------------------------------------------------------------------- #
def test_replay_lfo_split_excludes_future():
    # round 0 establishes history; round 1 is predicted. Only round>=1 scored.
    cells = [
        _cell("R", 0, "V0", "t1", True),
        _cell("R", 0, "V0", "t1", True),     # more history on same cell
        _cell("R", 1, "V0", "t1", False),    # predicted from history only
    ]
    by_run = {"R": cells}
    out = sb.replay_lfo(by_run, "task", (1, 2, 4, 8))
    assert out["n_pred_points"] == 1         # only the round-1 cell is a prediction point
    # mle@0.5 predicts from history (all passes) -> Brier vs the failing outcome is high
    assert out["estimators"]["mle@0.5"]["n_scored"] == 1


def test_replay_lfo_shrinkage_reduces_n1_flips():
    # GAIA-like: low pass rate, fresh cells whose single history observation is a
    # FAILURE. Raw MLE (prior >= 0.5) flips below 0.5 on that one failure;
    # shrinkage (prior already < 0.5) stays put and resists the flip.
    cells = []
    # pool prior below 0.5: 16 pass / 24 fail on throwaway seed tasks
    for i in range(40):
        cells.append(_cell("R", 0, "V0", f"seed{i}", i < 16))
    # round 1: one failure observation per fresh task
    for i in range(20):
        cells.append(_cell("R", 1, "V0", f"c{i}", False))
    # round 2: predict those fresh cells (history now has exactly the 1 failure)
    for i in range(20):
        cells.append(_cell("R", 2, "V0", f"c{i}", False))
    out = sb.replay_lfo({"R": cells}, "task", (1, 2, 4, 8))
    flips = out["estimators"]
    assert out["n1_flip_points"] == 20
    # MLE crosses 0.5 on the single failure; shrinkage (prior < 0.5) does not.
    assert flips["mle@0.7"]["n1_flips"] == 20
    assert flips["mle@0.5"]["n1_flips"] == 20
    assert flips["shrink@8"]["n1_flips"] == 0
    assert flips["shrink@8"]["n1_flips"] <= flips["shrink@1"]["n1_flips"]


def test_run_end_to_end_on_synthetic(tmp_path):
    # exercise the full CLI run() path (not just pure helpers) on synthetic data.
    make_comparison_json(tmp_path / "runX" / "comparison.json", rounds=[
        [{"round": 0, "variant": v, "task": f"t{t}", "level": 1,
          "attempts": [(t % 2 == 0, False), ((t + 1) % 2 == 0, False)]}
         for v in ("V0", "V1") for t in range(6)],
        [{"round": 1, "variant": v, "task": f"t{t}", "level": 1,
          "attempts": [(t % 3 == 0, False), (False, False)]}
         for v in ("V0", "V1") for t in range(6)],
    ])
    a = sa.run(str(tmp_path), None, 2, 0.05, 0.20, (0.55, 0.6, 0.75), 50, 20260805, True)
    assert a["meta"]["used_runs"] == ["runX"]
    assert "p1=0.6" in a["aa_calibration"]["per_p1"]
    b = sb.run(str(tmp_path), None, 2, ["task", "level"], (1, 2, 4, 8), True)
    assert set(b["by_cluster_key"]) == {"task", "level"}
    assert b["by_cluster_key"]["task"]["n_pred_points"] > 0
