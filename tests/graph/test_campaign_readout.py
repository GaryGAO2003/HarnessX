# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""Campaign readout: config normalisation, replicate grouping, landed-ship
attribution, endpoint framing.  Pure filesystem fixtures — no runs required."""
from __future__ import annotations

import json

from experiments.analysis.campaign_readout import (
    build,
    config_group,
    endpoint,
    normalised_config,
    read_arm,
    replicate_groups,
    ship_ranking,
)

_CFG = """\
tracer:
  base_dir: D:\\PycharmProj\\HarnessX{suffix}\\runs\\TAG\\R{n}\\sessions
processors:
- _target_: harnessx.processors.control.cost_guard.CostGuardProcessor
  max_usd: {cap}
"""


def _mk_round(run_dir, n, *, cap=10, suffix="", decision=None):
    d = run_dir / f"R{n}"
    d.mkdir(parents=True, exist_ok=True)
    (d / "config.yaml").write_text(_CFG.format(n=n, cap=cap, suffix=suffix), encoding="utf-8")
    if decision is not None:
        (d / "decision.md").write_text(decision, encoding="utf-8")
    return d


def _mk_curves(run_dir, rows):
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "curves.json").write_text(
        json.dumps(
            [
                {
                    "round": r,
                    "passed": p,
                    "total_tasks": 103,
                    "pass_rate": p / 103,
                    "pass_pct": f"{p / 103 * 100:.1f}%",
                    "evolve_status": s,
                    "cost_usd": 1.0,
                    "total_tokens": 10,
                    "config_hash": f"h{r}",
                }
                for r, p, s in rows
            ]
        ),
        encoding="utf-8",
    )


_SHIP = """\
---
round: 3
decision_type: ship
ship_ranking:
  - candidate_id: C-R3-01
  - candidate_id: C-R3-02
strategy_concern: |-
  irrelevant
---
body
"""

_NOOP = """\
---
round: 4
decision_type: no_op
ship_ranking: []
---
body
"""


def test_normalisation_ignores_round_and_checkout_paths(tmp_path):
    """The two bookkeeping lines must not make identical stacks look different."""
    a = _mk_round(tmp_path / "a", 7)
    b = _mk_round(tmp_path / "b", 8, suffix="-baseline")
    assert config_group(a / "config.yaml") == config_group(b / "config.yaml")
    assert "base_dir: <NORM>" in normalised_config(a / "config.yaml")


def test_normalisation_still_separates_real_config_change(tmp_path):
    a = _mk_round(tmp_path / "a", 7)
    b = _mk_round(tmp_path / "b", 7, cap=99)
    assert config_group(a / "config.yaml") != config_group(b / "config.yaml")


def test_ship_ranking_reads_ship_and_rejects_noop(tmp_path):
    ship = _mk_round(tmp_path / "s", 3, decision=_SHIP)
    noop = _mk_round(tmp_path / "n", 4, decision=_NOOP)
    assert ship_ranking(ship) == ["C-R3-01", "C-R3-02"]
    assert ship_ranking(noop) == []
    assert ship_ranking(tmp_path / "s" / "R9") == []


def test_ships_do_not_count_as_landed_when_config_is_unchanged(tmp_path):
    """A ship decision whose apply step failed leaves the previous config in
    force; the readout must show intent, not a landed change."""
    run = tmp_path / "run"
    _mk_curves(run, [(0, 60, "baseline"), (1, 62, "crashed"), (2, 70, "ok")])
    _mk_round(run, 0)
    _mk_round(run, 1, decision=_SHIP)  # same config as R0 -> did not land
    _mk_round(run, 2, cap=99, decision=_SHIP)  # config changed -> landed
    rounds = read_arm(run)["rounds"]
    assert [r["ships_landed"] for r in rounds] == [False, False, True]


def test_replicate_groups_and_endpoint_flag_a_noise_side_peak(tmp_path):
    run = tmp_path / "run"
    _mk_curves(run, [(0, 60, "baseline"), (1, 66, "noop"), (2, 63, "noop")])
    for n in (0, 1, 2):
        _mk_round(run, n)  # one identical config across all three rounds
    arm = read_arm(run)
    groups = replicate_groups(arm["rounds"])
    assert len(groups) == 1
    assert groups[0]["rounds"] == [0, 1, 2]
    assert groups[0]["spread_tasks"] == 6

    e = endpoint(arm["rounds"], groups)
    assert e["peak_round"] == 1 and e["peak_passed"] == 66
    assert e["final_round"] == 2 and e["drop_tasks"] == 3
    # The peak shares a config with the final round, so it is a draw from the
    # same distribution -- the readout must say so rather than claim a drop.
    assert e["peak_inside_replicate_group"] == [0, 1, 2]
    assert e["peak_carried_new_ship"] is False


def test_envelope_is_the_worst_spread_across_arms(tmp_path):
    small, big = tmp_path / "small", tmp_path / "big"
    _mk_curves(small, [(0, 60, "baseline"), (1, 61, "noop")])
    _mk_curves(big, [(0, 70, "baseline"), (1, 77, "noop")])
    for run in (small, big):
        _mk_round(run, 0)
        _mk_round(run, 1)
    env = build({"S": small, "B": big}, {})["envelope"]
    assert env["max_same_config_spread_tasks"] == 7
    assert env["measured_on"]["arm"] == "B"
    assert env["replicate_group_count"] == 2
