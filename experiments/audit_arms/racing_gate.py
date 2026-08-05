"""Module 1 (audit idea ⑧) -- statistical acceptance gate for ship decisions.

Two pure readings of a paired base-vs-candidate stream, no I/O:

  1. SPRT on discordant pairs (sequential McNemar). Concordant pairs carry no
     information and never move the log-likelihood ratio (LLR). Wald boundaries
       A = ln((1 - beta) / alpha)      (upper -> ACCEPT H1: candidate better)
       B = ln(beta / (1 - alpha))      (lower -> REJECT  H0: no improvement)
     H0: P(candidate wins | discordant) = 0.5
     H1: P(candidate wins | discordant) = p1
  2. Hoeffding race on the paired uplift. Two-sided Hoeffding CI at level alpha;
     the gate fires the first time the CI excludes 0.

Everything here is a pure function or a frozen dataclass. Self-contained, stdlib
only. No dependency on experiments.variant_pool or any branch analysis script.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List, Sequence, Tuple

# Pre-registered constants (ARMS-SPEC v0.1). Overridable per call, but these are
# the pre-registration defaults; any deviation must be logged in ARMS-LEDGER.md.
ALPHA = 0.05
BETA = 0.20
P0 = 0.5                       # McNemar null: fair coin on discordant direction
P1_GRID = (0.55, 0.60, 0.75)   # pre-registered H1 grid

Pair = Tuple[int, int]         # (base_pass, cand_pass), each 0/1


# --------------------------------------------------------------------------- #
# SPRT (sequential McNemar on discordant pairs)                               #
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class SPRTConfig:
    """Configuration for the sequential McNemar SPRT."""
    alpha: float = ALPHA
    beta: float = BETA
    p0: float = P0
    p1: float = 0.60

    @property
    def upper(self) -> float:
        """A = ln((1 - beta) / alpha) -- cross upward => ACCEPT H1."""
        return math.log((1.0 - self.beta) / self.alpha)

    @property
    def lower(self) -> float:
        """B = ln(beta / (1 - alpha)) -- cross downward => REJECT H0."""
        return math.log(self.beta / (1.0 - self.alpha))

    @property
    def llr_win(self) -> float:
        """LLR increment when the candidate wins a discordant pair."""
        return math.log(self.p1 / self.p0)

    @property
    def llr_loss(self) -> float:
        """LLR increment when the base wins a discordant pair."""
        return math.log((1.0 - self.p1) / (1.0 - self.p0))

    def implied_uplift(self, discordant_rate: float) -> float:
        """theta_hat = d_hat * (2*p1 - 1): implied per-task uplift at this p1."""
        return discordant_rate * (2.0 * self.p1 - 1.0)


@dataclass
class SPRTResult:
    decision: str                 # 'ACCEPT' | 'REJECT' | 'CONTINUE'
    n_used: int                   # total pairs consumed from the stream at decision
    n_discordant: int             # discordant pairs consumed (the informative ones)
    llr: float                    # final cumulative log-likelihood ratio
    trace: List[Tuple[int, int, int, bool, float]] = field(default_factory=list)
    # trace rows: (index, base_pass, cand_pass, is_discordant, llr_after)


def _discordant_direction(base_pass: int, cand_pass: int):
    """Return +1 if candidate wins the discordant pair, -1 if base wins, 0 if concordant."""
    if cand_pass == base_pass:
        return 0
    return 1 if (cand_pass == 1 and base_pass == 0) else -1


def decide(pairs: Sequence[Pair], config: SPRTConfig | None = None) -> SPRTResult:
    """Run the sequential McNemar SPRT over a stream of (base_pass, cand_pass) pairs.

    Concordant pairs are consumed (counted in n_used) but do not update the LLR.
    Stops the first time the LLR crosses a Wald boundary; otherwise CONTINUE with
    the whole stream consumed.
    """
    cfg = config or SPRTConfig()
    llr = 0.0
    n_disc = 0
    trace: List[Tuple[int, int, int, bool, float]] = []
    for i, (base_pass, cand_pass) in enumerate(pairs, start=1):
        b, c = int(base_pass), int(cand_pass)
        d = _discordant_direction(b, c)
        is_disc = d != 0
        if is_disc:
            n_disc += 1
            llr += cfg.llr_win if d > 0 else cfg.llr_loss
        trace.append((i, b, c, is_disc, llr))
        if llr >= cfg.upper:
            return SPRTResult("ACCEPT", i, n_disc, llr, trace)
        if llr <= cfg.lower:
            return SPRTResult("REJECT", i, n_disc, llr, trace)
    return SPRTResult("CONTINUE", len(trace), n_disc, llr, trace)


def fixed_n_for_mde(config: SPRTConfig | None = None) -> int:
    """Wald's average-sample-number style guide for the number of *discordant*
    pairs a fixed-sample test would nominally need at H1 -- used only as a
    reference denominator for the budget-reallocation readout (not a boundary).
    """
    cfg = config or SPRTConfig()
    # Expected discordant sample size under H1 (Wald ASN, H1 true):
    num = (1.0 - cfg.beta) * cfg.upper + cfg.beta * cfg.lower
    den = cfg.p1 * cfg.llr_win + (1.0 - cfg.p1) * cfg.llr_loss
    if den == 0:
        return 0
    return max(1, int(math.ceil(num / den)))


# --------------------------------------------------------------------------- #
# Hoeffding race on the paired uplift                                         #
# --------------------------------------------------------------------------- #
def hoeffding_halfwidth(n: int, alpha: float = ALPHA, value_range: float = 2.0) -> float:
    """Two-sided Hoeffding CI half-width for a mean of n i.i.d. bounded values.

    Paired diffs live in [-1, 1] so value_range = 2 by default. Half-width is
    monotonically decreasing in n and increasing as alpha shrinks.
    """
    if n <= 0:
        return float("inf")
    return value_range * math.sqrt(math.log(2.0 / alpha) / (2.0 * n))


def hoeffding_uplift_ci(diffs: Sequence[int], alpha: float = ALPHA):
    """CI on the paired uplift mean(cand - base). diffs are in {-1, 0, 1}.

    Returns (mean, lo, hi, decision) where decision is:
      'ACCEPT'  if lo > 0  (candidate uplift, CI excludes 0 above)
      'REJECT'  if hi < 0  (candidate regression, CI excludes 0 below)
      'CONTINUE' otherwise (CI still straddles 0)
    """
    n = len(diffs)
    if n == 0:
        return (0.0, float("-inf"), float("inf"), "CONTINUE")
    mean = sum(diffs) / n
    h = hoeffding_halfwidth(n, alpha=alpha, value_range=2.0)
    lo, hi = mean - h, mean + h
    if lo > 0:
        decision = "ACCEPT"
    elif hi < 0:
        decision = "REJECT"
    else:
        decision = "CONTINUE"
    return (mean, lo, hi, decision)


@dataclass
class HoeffdingResult:
    decision: str
    n_used: int
    mean: float
    lo: float
    hi: float


def hoeffding_race(pairs: Sequence[Pair], alpha: float = ALPHA) -> HoeffdingResult:
    """Stream paired diffs; fire the gate the first step the Hoeffding CI excludes 0."""
    diffs: List[int] = []
    for i, (base_pass, cand_pass) in enumerate(pairs, start=1):
        diffs.append(int(cand_pass) - int(base_pass))
        mean, lo, hi, decision = hoeffding_uplift_ci(diffs, alpha=alpha)
        if decision != "CONTINUE":
            return HoeffdingResult(decision, i, mean, lo, hi)
    mean, lo, hi, decision = hoeffding_uplift_ci(diffs, alpha=alpha)
    return HoeffdingResult(decision, len(diffs), mean, lo, hi)
