import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import cold_start as cs  # noqa: E402


def test_shrink_limit_m_zero_is_mle():
    assert cs.shrink(3, 4, parent_p=0.7, m=0) == 3 / 4


def test_shrink_limit_m_infty_is_parent():
    assert math.isclose(cs.shrink(3, 4, parent_p=0.7, m=1_000_000), 0.7, rel_tol=1e-4)


def test_shrink_empty_falls_back_to_parent():
    assert cs.shrink(0, 0, parent_p=0.42, m=0) == 0.42               # n+m == 0
    assert math.isclose(cs.shrink(0, 0, parent_p=0.42, m=5), 0.42)   # pure prior


def test_shrink_monotone_interpolation():
    mle = 1.0        # s=2, n=2
    parent = 0.3
    vals = [cs.shrink(2, 2, parent, m) for m in (0, 1, 2, 4, 8, 64, 4096)]
    # strictly decreasing from MLE toward parent, staying within [parent, mle]
    for a, b in zip(vals, vals[1:]):
        assert a >= b
    assert vals[0] == mle
    assert parent <= vals[-1] < mle
    assert math.isclose(vals[-1], parent, abs_tol=1e-2)


def test_from_cells_aggregates_levels():
    recs = [
        {"variant": "V0", "cluster": "t1", "s": 1, "n": 2},
        {"variant": "V0", "cluster": "t1", "s": 1, "n": 1},   # same cell -> merges
        {"variant": "V0", "cluster": "t2", "s": 0, "n": 3},
        {"variant": "V1", "cluster": "t1", "s": 2, "n": 2},
    ]
    counts = cs.from_cells(recs)
    assert counts.cell("V0", "t1") == (2, 3)
    assert counts.variant("V0") == (2, 6)
    assert counts.total == (4, 8)
    assert math.isclose(counts.pool_p(), 0.5)


def test_from_cells_passed_adapter():
    recs = [
        {"variant": "V0", "cluster": "t1", "passed": True, "n_pass": 1, "n_att": 2},
        {"variant": "V0", "cluster": "t1", "passed": False, "n_pass": 0, "n_att": 2},
    ]
    counts = cs.from_cells(recs)
    assert counts.cell("V0", "t1") == (1, 4)


def test_two_level_chain_consistency():
    recs = [
        {"variant": "V0", "cluster": "t1", "s": 4, "n": 8},   # cell has data
        {"variant": "V0", "cluster": "t2", "s": 2, "n": 4},
        {"variant": "V1", "cluster": "t1", "s": 9, "n": 10},
    ]
    counts = cs.from_cells(recs)
    # m1=m2=0 with populated cell -> raw cell MLE
    assert math.isclose(cs.estimate(counts, "V0", "t1", m1=0, m2=0), 4 / 8)
    # empty cell, m1=0 -> variant-level estimate (which with m2=0 is the variant MLE)
    empty = cs.estimate(counts, "V0", "t_missing", m1=0, m2=0)
    assert math.isclose(empty, counts.variant("V0")[0] / counts.variant("V0")[1])
    # variant estimate with huge m2 -> pool global mean
    pool = counts.pool_p()
    assert math.isclose(cs.variant_estimate(counts, "V0", m2=1e9), pool, rel_tol=1e-6)


def test_estimate_between_cell_and_parent():
    recs = [{"variant": "V0", "cluster": "t1", "s": 1, "n": 1},
            {"variant": "V0", "cluster": "t2", "s": 0, "n": 9}]
    counts = cs.from_cells(recs)
    parent = cs.variant_estimate(counts, "V0", m2=2)
    est = cs.estimate(counts, "V0", "t1", m1=4, m2=2)   # cell MLE=1.0, parent low
    assert parent <= est <= 1.0


def test_mle_empty_conventions():
    counts = cs.from_cells([{"variant": "V0", "cluster": "t1", "s": 1, "n": 2}])
    assert cs.mle(counts, "V0", "t_missing", empty=0.5) == 0.5
    assert cs.mle(counts, "V0", "t_missing", empty=0.7) == 0.7
    assert cs.mle(counts, "V0", "t1", empty=0.5) == 0.5   # 1/2, empty ignored
