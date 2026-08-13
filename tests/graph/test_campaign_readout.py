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


def test_peak_inside_a_replicate_group_is_debiased_to_the_group_mean(tmp_path):
    """Taking the best of k draws of one configuration inflates the peak, so the
    drop measured from it overstates. The group mean is what gets quoted."""
    run = tmp_path / "run"
    _mk_curves(run, [(0, 76, "ok"), (1, 74, "noop"), (2, 80, "noop"), (3, 73, "ok")])
    for n in (0, 1, 2):
        _mk_round(run, n)  # one config across R0-R2
    _mk_round(run, 3, cap=99)  # R3 is a genuinely different config
    arm = read_arm(run)
    e = endpoint(arm["rounds"], replicate_groups(arm["rounds"]))
    assert e["peak_round"] == 2 and e["peak_passed"] == 80
    assert e["drop_tasks"] == 7  # the naive reading
    assert e["peak_is_max_of_k_draws"] == 3
    assert e["peak_group_mean"] == 76.67
    assert e["drop_vs_group_mean_tasks"] == 3.67  # what is actually defensible
    assert e["net_tasks_vs_first"] == -3


def test_endpoint_omits_debiasing_when_the_peak_stands_alone(tmp_path):
    run = tmp_path / "run"
    _mk_curves(run, [(0, 60, "ok"), (1, 80, "ok"), (2, 70, "ok")])
    _mk_round(run, 0)
    _mk_round(run, 1, cap=50)
    _mk_round(run, 2, cap=99)
    arm = read_arm(run)
    e = endpoint(arm["rounds"], replicate_groups(arm["rounds"]))
    assert e["peak_inside_replicate_group"] is None
    assert "peak_group_mean" not in e
    assert e["net_tasks_vs_first"] == 10


def test_pooled_noise_uses_within_group_deviations_only(tmp_path):
    """Between-group differences are configuration effects and must not inflate
    the noise estimate — only deviation from each group's own mean counts."""
    from experiments.analysis.campaign_readout import pooled_noise

    # Two groups, each deviating +-1 from its own mean, but means 30 apart.
    # Pooled variance = (2 + 2) / 2 = 2, so SD = sqrt(2) -- the 30-task gap
    # between groups must not enter at all.
    groups = [{"passed": [60, 62]}, {"passed": [90, 92]}]
    n = pooled_noise(groups, 103)
    assert n["degrees_of_freedom"] == 2
    assert n["single_round_sd_tasks"] == round(2**0.5, 2)
    assert n["round_difference_sd_tasks"] == 2.0


def test_pooled_noise_is_empty_without_replicates(tmp_path):
    from experiments.analysis.campaign_readout import pooled_noise

    assert pooled_noise([], 103) == {}


def test_cross_arm_compares_finals_and_verifies_the_shared_baseline(tmp_path):
    """Net-gain-vs-net-gain rewards whichever arm drew a low baseline, so the
    comparison is final vs final — licensed only if R0's config really matches."""
    a, b = tmp_path / "a", tmp_path / "b"
    _mk_curves(a, [(0, 64, "baseline"), (1, 73, "ok")])
    _mk_curves(b, [(0, 61, "baseline"), (1, 77, "ok")])
    for run, suffix in ((a, ""), (b, "-baseline")):
        _mk_round(run, 0, suffix=suffix)  # same stack, different checkout
        _mk_round(run, 1, cap=99, suffix=suffix)
    x = build({"L0": a, "L2": b}, {})["cross_arm"]
    assert x["baseline_config_shared"] is True
    assert x["baseline_passed"] == [64, 61]
    assert x["final_difference_tasks"] == -4  # 73 - 77, not (+9) - (+16)
    assert x["both_complete"] is True


def test_cross_arm_flags_a_baseline_that_is_not_actually_shared(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    _mk_curves(a, [(0, 64, "baseline")])
    _mk_curves(b, [(0, 61, "baseline")])
    _mk_round(a, 0)
    _mk_round(b, 0, cap=99)  # genuinely different starting configuration
    assert build({"L0": a, "L2": b}, {})["cross_arm"]["baseline_config_shared"] is False


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
