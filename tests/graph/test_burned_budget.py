"""Burned-budget accounting — per-candidate attribution and run totals."""

import json

from experiments.analysis.burned_budget import compute


def _state(path, cost, tokens):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"cumulative_cost_usd": cost,
                                "cumulative_tokens": tokens}),
                    encoding="utf-8")


def _replay_report(path, run_name, intercepted, passed):
    per = {f"{run_name}/{c}": {"verdict": "intercepted_build_other",
                               "evaluated": True} for c in intercepted}
    per.update({f"{run_name}/{c}": {"verdict": "passed", "evaluated": True}
                for c in passed})
    path.write_text(json.dumps({"root": "", "per_candidate": per}),
                    encoding="utf-8")
    return path


def test_attribution_and_totals(tmp_path):
    run = tmp_path / "runx"
    # intercepted candidate: two attempt states
    _state(run / "R1" / "C-R1-01" / "sessions" / "s1" / "a_state.json", 2.0, 1000)
    _state(run / "R1" / "C-R1-01" / "sessions" / "s2" / "b_state.json", 3.0, 2000)
    # passed candidate: spends but is not burned budget
    _state(run / "R1" / "C-R1-02" / "sessions" / "s3" / "c_state.json", 5.0, 4000)
    # non-candidate spend (active pool): run total only
    _state(run / "R2" / "active_pool" / "V0" / "d_state.json", 10.0, 8000)

    report = _replay_report(tmp_path / "replay.json", "runx",
                            intercepted=["C-R1-01"], passed=["C-R1-02"])
    result = compute(report, [run])

    row = result["runs"]["runx"]
    assert row["intercepted"]["C-R1-01"] == {
        "usd": 5.0, "tokens": 3000, "has_recorded_spend": True}
    assert row["burned_usd"] == 5.0
    assert row["run_total_usd"] == 20.0
    assert row["attempt_states"] == 4
    assert row["burned_share_of_run_cost"] == 0.25
    assert result["totals"]["burned_usd"] == 5.0
    assert result["totals"]["burned_share_of_corpus_cost"] == 0.25


def test_intercepted_without_spend_is_flagged(tmp_path):
    run = tmp_path / "runy"
    _state(run / "R1" / "C-R1-05" / "sessions" / "s" / "x_state.json", 1.0, 10)
    report = _replay_report(tmp_path / "replay.json", "runy",
                            intercepted=["C-R1-05", "C-R2-09"], passed=[])
    result = compute(report, [run])
    rows = result["runs"]["runy"]["intercepted"]
    assert rows["C-R2-09"] == {"usd": 0.0, "tokens": 0,
                               "has_recorded_spend": False}
    assert result["runs"]["runy"]["burned_usd"] == 1.0


def test_corrupt_state_skipped(tmp_path):
    run = tmp_path / "runz"
    bad = run / "R1" / "C-R1-01" / "sessions" / "s" / "bad_state.json"
    bad.parent.mkdir(parents=True)
    bad.write_text("{not json", encoding="utf-8")
    _state(run / "R1" / "C-R1-01" / "sessions" / "s" / "ok_state.json", 2.0, 100)
    report = _replay_report(tmp_path / "replay.json", "runz",
                            intercepted=["C-R1-01"], passed=[])
    result = compute(report, [run])
    assert result["runs"]["runz"]["burned_usd"] == 2.0
    assert result["runs"]["runz"]["attempt_states"] == 1
