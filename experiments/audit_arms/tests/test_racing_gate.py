import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # audit_arms
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))                    # tests

import racing_gate as rg  # noqa: E402


# --------------------------------------------------------------------------- #
# SPRT                                                                         #
# --------------------------------------------------------------------------- #
def test_boundaries_numeric():
    cfg = rg.SPRTConfig(alpha=0.05, beta=0.20)
    assert math.isclose(cfg.upper, math.log(16.0), rel_tol=1e-12)          # ln((1-.2)/.05)
    assert math.isclose(cfg.lower, math.log(0.2 / 0.95), rel_tol=1e-12)
    assert cfg.upper > 0 > cfg.lower


def test_all_concordant_continues():
    cfg = rg.SPRTConfig(p1=0.75)
    pairs = [(0, 0), (1, 1)] * 25
    res = rg.decide(pairs, cfg)
    assert res.decision == "CONTINUE"
    assert res.n_discordant == 0
    assert res.llr == 0.0
    assert res.n_used == len(pairs)   # whole stream consumed, none informative


def test_extreme_p1_accepts_fast():
    cfg = rg.SPRTConfig(p1=0.75)
    # candidate wins every discordant pair -> LLR climbs by ln(1.5) each step.
    pairs = [(0, 1)] * 20
    res = rg.decide(pairs, cfg)
    assert res.decision == "ACCEPT"
    # ceil(upper / ln(1.5)) discordant pairs needed
    need = math.ceil(cfg.upper / math.log(1.5))
    assert res.n_discordant == need
    assert res.n_used == need


def test_reject_symmetry():
    cfg = rg.SPRTConfig(p1=0.75)
    # base wins every discordant pair -> LLR falls by ln(0.5) each step.
    pairs = [(1, 0)] * 20
    res = rg.decide(pairs, cfg)
    assert res.decision == "REJECT"
    need = math.ceil(cfg.lower / math.log(0.5))   # both negative -> positive count
    assert res.n_discordant == need


def test_concordant_counted_in_n_used_not_discordant():
    cfg = rg.SPRTConfig(p1=0.75)
    # two concordant, then enough cand-wins to accept
    win_need = math.ceil(cfg.upper / math.log(1.5))
    pairs = [(0, 0), (1, 1)] + [(0, 1)] * win_need
    res = rg.decide(pairs, cfg)
    assert res.decision == "ACCEPT"
    assert res.n_discordant == win_need
    assert res.n_used == win_need + 2          # concordant pairs consumed too


def test_implied_uplift_monotone_in_p1():
    d = 0.30
    u55 = rg.SPRTConfig(p1=0.55).implied_uplift(d)
    u75 = rg.SPRTConfig(p1=0.75).implied_uplift(d)
    assert u55 < u75
    assert math.isclose(u75, d * (2 * 0.75 - 1))


# --------------------------------------------------------------------------- #
# Hoeffding                                                                    #
# --------------------------------------------------------------------------- #
def test_hoeffding_halfwidth_monotone():
    assert rg.hoeffding_halfwidth(10) > rg.hoeffding_halfwidth(100) > rg.hoeffding_halfwidth(1000)
    assert math.isinf(rg.hoeffding_halfwidth(0))
    # narrower alpha (more confidence) => wider interval
    assert rg.hoeffding_halfwidth(100, alpha=0.01) > rg.hoeffding_halfwidth(100, alpha=0.05)


def test_hoeffding_ci_narrows_with_n():
    _, lo10, hi10, _ = rg.hoeffding_uplift_ci([1, -1] * 5)
    _, lo100, hi100, _ = rg.hoeffding_uplift_ci([1, -1] * 50)
    assert (hi10 - lo10) > (hi100 - lo100)


def test_hoeffding_race_directions():
    up = rg.hoeffding_race([(0, 1)] * 5000)
    assert up.decision == "ACCEPT" and up.mean > 0
    down = rg.hoeffding_race([(1, 0)] * 5000)
    assert down.decision == "REJECT" and down.mean < 0
    flat = rg.hoeffding_race([(0, 0), (1, 1)] * 50)
    assert flat.decision == "CONTINUE"


def test_fixed_n_reference_positive():
    n = rg.fixed_n_for_mde(rg.SPRTConfig(p1=0.60))
    assert n >= 1
