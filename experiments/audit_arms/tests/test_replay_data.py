import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import replay_data as rd          # noqa: E402
from helpers import make_comparison_json  # noqa: E402


def _tiny_runs(root):
    # run A: 2 variants (V0, V1), 2 rounds, with one infra-only cell and one mixed cell.
    make_comparison_json(root / "runA" / "comparison.json", rounds=[
        [  # round 0
            {"round": 0, "variant": "V0", "task": "t1", "level": 1,
             "attempts": [(True, False), (False, False)]},          # kept, passed
            {"round": 0, "variant": "V1", "task": "t2", "level": 2,
             "attempts": [(False, True)]},                          # infra-only -> dropped
        ],
        [  # round 1
            {"round": 1, "variant": "V0", "task": "t1", "level": 1,
             "attempts": [(False, True), (True, False)]},           # infra dropped, 1 kept passed
            {"round": 1, "variant": "V1", "task": "t2", "level": 2,
             "attempts": [(False, False), (False, False)]},         # kept, failed
        ],
    ])
    # run B: single variant -> filtered out by min_variants=2.
    make_comparison_json(root / "runB" / "comparison.json", rounds=[
        [{"round": 0, "variant": "V0", "task": "t9", "level": 3,
          "attempts": [(True, False)]}],
    ])
    return root


def test_load_and_filter(tmp_path):
    _tiny_runs(tmp_path)
    cells = rd.load_cells(str(tmp_path), drop_infra=True)
    # runA: t1@r0 (2 att kept), V1 t2@r0 dropped (infra-only), t1@r1 (1 kept), t2@r1 (2 kept)
    #  + runB one cell = 4 cells total
    keys = {(c["run"], c["round"], c["variant"], c["task"]) for c in cells}
    assert ("runA", 0, "V0", "t1") in keys
    assert ("runA", 0, "V1", "t2") not in keys        # infra-only cell dropped
    assert ("runB", 0, "V0", "t9") in keys

    v0_r1 = next(c for c in cells if c["run"] == "runA" and c["round"] == 1 and c["variant"] == "V0")
    assert v0_r1["n_att"] == 1 and v0_r1["passed"] is True   # infra attempt removed


def test_keep_infra(tmp_path):
    _tiny_runs(tmp_path)
    cells = rd.load_cells(str(tmp_path), drop_infra=False)
    keys = {(c["run"], c["round"], c["variant"], c["task"]) for c in cells}
    assert ("runA", 0, "V1", "t2") in keys            # infra cell retained when not dropped


def test_min_variants_filter(tmp_path):
    _tiny_runs(tmp_path)
    rep = rd.load_report(str(tmp_path), min_variants=2)
    assert "runA" in rep.used_runs
    assert "runB" not in rep.used_runs
    assert any(run == "runB" for run, _ in rep.skipped)


def test_malformed_skipped(tmp_path):
    _tiny_runs(tmp_path)
    bad = tmp_path / "runBad" / "comparison.json"
    bad.parent.mkdir(parents=True, exist_ok=True)
    bad.write_text("{ this is not valid json ", encoding="utf-8")
    rep = rd.load_report(str(tmp_path))
    assert "runBad" not in rep.used_runs
    assert any(run == "runBad" and "malformed" in reason for run, reason in rep.skipped)


def test_manifest_stable_and_short(tmp_path):
    _tiny_runs(tmp_path)
    f = str(tmp_path / "runA" / "comparison.json")
    m1 = rd.manifest([f])
    m2 = rd.manifest([f])
    assert len(m1[f]) == 8
    assert m1 == m2                                   # deterministic
    assert rd.manifest(["does_not_exist"])["does_not_exist"] == "MISSING"


def test_explicit_runs_selection(tmp_path):
    _tiny_runs(tmp_path)
    rep = rd.load_report(str(tmp_path), runs=["runB"])
    assert rep.used_runs == ["runB"]
