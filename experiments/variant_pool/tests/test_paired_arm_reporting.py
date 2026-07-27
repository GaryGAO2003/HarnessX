# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""P1-2 — paired-arm CI + drift reporting (mirror paper Table 5).

Fully offline. Pins:

* ``binomial_ci`` against hand-computed Wilson score intervals (and the closed
  form for a range of (passes, n), including the p=0 / p=1 clamps and fractional
  effective counts), with the degenerate n=0 made safe;
* ``paired_arm_summary`` rendering the final / peak(+round) / drift columns of
  Table 5, the 95% CI at each arm's bed size, the between-arm delta, and the
  CI-overlap note — plus safe degradation on an empty arm.
"""

from __future__ import annotations

from math import sqrt

import pytest

from variant_pool.reporting import (
    RunReport,
    TaskResult,
    binomial_ci,
    paired_arm_summary,
)


def _wilson(passes: float, n: int, z: float = 1.96) -> tuple[float, float]:
    """Independent reference implementation of the Wilson score interval."""
    p = passes / n
    z2 = z * z
    denom = 1.0 + z2 / n
    center = (p + z2 / (2.0 * n)) / denom
    margin = (z / denom) * sqrt(p * (1.0 - p) / n + z2 / (4.0 * n * n))
    return (max(0.0, center - margin), min(1.0, center + margin))


def _report(name: str, rounds_rows: dict[int, list[tuple[str, int]]], k: int = 2) -> RunReport:
    report = RunReport(run_name=name, k=k)
    for round_idx, rows in rounds_rows.items():
        for task_id, n_pass in rows:
            report.add(TaskResult(task_id=task_id, round_idx=round_idx, n_att=2, n_pass=n_pass))
    return report


# ---------------------------------------------------------------------------
# binomial_ci — pinned against hand-computed Wilson values
# ---------------------------------------------------------------------------


def test_binomial_ci_hand_computed_wilson():
    # passes=8, n=12 -> hand-computed Wilson 95% interval.
    lo, hi = binomial_ci(8, 12)
    assert lo == pytest.approx(0.3906, abs=1e-4)
    assert hi == pytest.approx(0.8619, abs=1e-4)


def test_binomial_ci_symmetric_at_half():
    # p=0.5 -> the interval is symmetric around 0.5.
    lo, hi = binomial_ci(1, 2)
    assert lo == pytest.approx(0.0945, abs=1e-4)
    assert hi == pytest.approx(0.9055, abs=1e-4)
    assert (lo + hi) == pytest.approx(1.0, abs=1e-9)


@pytest.mark.parametrize(
    "passes, n",
    [(0, 4), (4, 4), (1, 4), (5, 20), (90, 103), (103, 103), (12, 12)],
)
def test_binomial_ci_matches_reference_and_is_bounded(passes, n):
    lo, hi = binomial_ci(passes, n)
    exp_lo, exp_hi = _wilson(passes, n)
    assert lo == pytest.approx(exp_lo, abs=1e-12)
    assert hi == pytest.approx(exp_hi, abs=1e-12)
    # Wilson never leaves [0, 1] even at the p=0 / p=1 extremes.
    assert 0.0 <= lo <= hi <= 1.0


def test_binomial_ci_fractional_passes_matches_reference():
    # A macro-averaged pass@2 of 0.495 over 103 tasks has no integer success
    # count; the effective count 0.495*103 must reproduce the proportion exactly.
    passes = 0.495 * 103
    lo, hi = binomial_ci(passes, 103)
    exp_lo, exp_hi = _wilson(passes, 103)
    assert lo == pytest.approx(exp_lo, abs=1e-12)
    assert hi == pytest.approx(exp_hi, abs=1e-12)


def test_binomial_ci_custom_z():
    # A wider z widens the interval symmetrically for p=0.5.
    lo90, hi90 = binomial_ci(1, 2, z=1.645)
    lo95, hi95 = binomial_ci(1, 2, z=1.96)
    assert lo90 > lo95 and hi90 < hi95


def test_binomial_ci_degenerate_n_zero_is_safe():
    assert binomial_ci(0, 0) == (0.0, 1.0)
    # even a nonsensical passes with n=0 must not raise and must stay ordered
    lo, hi = binomial_ci(5, 0)
    assert lo <= hi


def test_binomial_ci_clamps_passes_out_of_range():
    # passes > n is clamped to n (p=1); passes < 0 is clamped to 0 (p=0).
    assert binomial_ci(9, 4) == binomial_ci(4, 4)
    assert binomial_ci(-3, 4) == binomial_ci(0, 4)


# ---------------------------------------------------------------------------
# paired_arm_summary — the Table 5 block
# ---------------------------------------------------------------------------


def _paper_shaped_arms():
    # global peaks at round 0 (0.75) then drifts to 0.25 (mirrors the paper's
    # Global-arm late collapse); ensemble holds 1.0 (peak round 0, no drift).
    glob = _report(
        "global",
        {
            0: [("t1", 2), ("t2", 2), ("t3", 2), ("t4", 0)],  # pass@2 = 0.75
            1: [("t1", 2), ("t2", 0), ("t3", 0), ("t4", 0)],  # pass@2 = 0.25
        },
    )
    ens = _report(
        "ensemble",
        {
            0: [("t1", 2), ("t2", 2), ("t3", 2), ("t4", 2)],  # 1.0
            1: [("t1", 2), ("t2", 2), ("t3", 2), ("t4", 2)],  # 1.0 (peak round 0)
        },
    )
    return glob, ens


def test_paired_arm_summary_renders_table5_columns_and_numbers():
    glob, ens = _paper_shaped_arms()
    block = paired_arm_summary(glob, ens)

    # Table-5 column headers (final / peak(round) / drift) + CI column.
    assert "final pass@2" in block
    assert "peak pass@2 (round)" in block
    assert "drift (final-peak)" in block
    assert "95% CI (Wilson)" in block

    # Per-arm final / peak(+round) / drift cells.
    assert "| global | 0.2500 | 0.7500 (r0) | -0.5000 |" in block
    assert "| ensemble | 1.0000 | 1.0000 (r0) | +0.0000 |" in block

    # 95% CI at bed size 4 for each arm (self-consistent with binomial_ci).
    lo_g, hi_g = binomial_ci(0.25 * 4, 4)
    lo_e, hi_e = binomial_ci(1.0 * 4, 4)
    assert f"[{lo_g:.4f}, {hi_g:.4f}] (n=4)" in block
    assert f"[{lo_e:.4f}, {hi_e:.4f}] (n=4)" in block


def test_paired_arm_summary_delta_and_overlap_note():
    glob, ens = _paper_shaped_arms()
    block = paired_arm_summary(glob, ens)
    # between-arm delta (ensemble - global) on final pass@2 = +0.75.
    assert "between-arm delta (final pass@2, ensemble - global): +0.7500" in block
    # at bed size 4 the two CIs overlap, so the delta is not resolved.
    lo_g, hi_g = binomial_ci(0.25 * 4, 4)
    lo_e, hi_e = binomial_ci(1.0 * 4, 4)
    overlap = lo_g <= hi_e and lo_e <= hi_g
    assert overlap  # sanity: this fixture is the overlapping regime
    assert "CI overlap:" in block
    assert "CIs overlap" in block


def test_paired_arm_summary_disjoint_note_when_ci_separate():
    # Large, well-separated beds -> disjoint CIs -> resolved delta.
    glob = _report("global", {0: [(f"t{i}", 0) for i in range(40)]})  # 0.0 over 40
    ens = _report("ensemble", {0: [(f"t{i}", 2) for i in range(40)]})  # 1.0 over 40
    block = paired_arm_summary(glob, ens)
    lo_g, hi_g = binomial_ci(0.0, 40)
    lo_e, hi_e = binomial_ci(40, 40)
    assert not (lo_g <= hi_e and lo_e <= hi_g)  # sanity: disjoint
    assert "CIs disjoint" in block
    assert "ensemble's CI lies entirely above" in block


def test_paired_arm_summary_custom_labels():
    glob, ens = _paper_shaped_arms()
    block = paired_arm_summary(glob, ens, label_a="k1", label_b="k8")
    assert "| k1 |" in block and "| k8 |" in block
    assert "k8 - k1" in block


def test_paired_arm_summary_empty_arm_is_safe():
    empty = RunReport(run_name="global", k=2)
    ens = _report("ensemble", {0: [("t1", 2), ("t2", 2)]})
    block = paired_arm_summary(empty, ens)
    assert "_no results_" in block  # the empty arm's row
    # delta / overlap line degrades safely rather than raising.
    assert "n/a (missing results for global)" in block


def test_paired_arm_summary_tokens_column_optional():
    glob, ens = _paper_shaped_arms()
    # no tokens attribute -> no tokens column
    assert "tokens" not in paired_arm_summary(glob, ens)
    # a truthy tokens attribute -> tokens column appears
    ens.tokens = 1_234_567  # type: ignore[attr-defined]
    block = paired_arm_summary(glob, ens)
    assert "tokens" in block
    assert "1234567" in block
