"""Module 2 (audit idea ⑩) -- cold-start hierarchical shrinkage for the router.

Beta-binomial empirical-Bayes shrinkage of per-cell success rates toward a
parent, so a freshly-seen (variant, cluster) cell is not routed on the strength
of one lucky/unlucky rollout. Two-level chain:

    cell (variant, cluster)  --shrink toward-->  variant rollup
    variant rollup           --shrink toward-->  pool global mean

Primitive:  shrink(s, n, parent_p, m) = (s + m*parent_p) / (n + m)
Limits:     m -> 0    == raw MLE (s / n)
            m -> inf  == parent_p
            monotone interpolation between the two as m grows.

Pure, stdlib only. No dependency on experiments.variant_pool. The empty-cell
optimism constant used by the *baselines* (0.7) is documented in the sim; this
module only implements the shrinkage estimator itself.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Hashable, Iterable, Mapping, Tuple

# Pre-registered shrinkage pseudo-count grid (ARMS-SPEC v0.1).
M_GRID = (1, 2, 4, 8)

Cluster = Hashable
Variant = Hashable
SN = Tuple[int, int]   # (successes, trials)


def shrink(s: float, n: float, parent_p: float, m: float) -> float:
    """Beta-binomial empirical-Bayes shrinkage primitive.

    (s + m*parent_p) / (n + m). With m the strength (in pseudo-observations) of
    the prior toward parent_p. m==0 gives the raw MLE s/n; m->inf gives parent_p.
    If there is nothing to go on (n + m == 0) we fall back to the parent.
    """
    denom = n + m
    if denom == 0:
        return parent_p
    return (s + m * parent_p) / denom


@dataclass
class Counts:
    """Self-contained aggregated success/trial counts at three levels.

    cells:    (variant, cluster) -> (s, n)
    variants: variant            -> (s, n)
    total:    (s, n) pooled over everything
    All plain dicts / tuples -- serialisable, no hidden state.
    """
    cells: Dict[Tuple[Variant, Cluster], SN] = field(default_factory=dict)
    variants: Dict[Variant, SN] = field(default_factory=dict)
    total: SN = (0, 0)

    def cell(self, variant: Variant, cluster: Cluster) -> SN:
        return self.cells.get((variant, cluster), (0, 0))

    def variant(self, variant: Variant) -> SN:
        return self.variants.get(variant, (0, 0))

    def pool_p(self, fallback: float = 0.5) -> float:
        s, n = self.total
        return s / n if n > 0 else fallback


def _add(d: Dict[Any, SN], key: Any, s: int, n: int) -> None:
    ps, pn = d.get(key, (0, 0))
    d[key] = (ps + s, pn + n)


def from_cells(records: Iterable[Mapping[str, Any]]) -> Counts:
    """Adapter: build a Counts roll-up from an iterable of cell records.

    Each record must expose 'variant', 'cluster', and either ('s','n') counts
    or an integer/bool 'passed' plus optional 'n_att'. Records with n<=0 are
    skipped. Returns a Counts with cell / variant / pool aggregates.
    """
    cells: Dict[Tuple[Variant, Cluster], SN] = {}
    variants: Dict[Variant, SN] = {}
    tot_s = tot_n = 0
    for rec in records:
        v = rec["variant"]
        cl = rec["cluster"]
        if "s" in rec and "n" in rec:
            s, n = int(rec["s"]), int(rec["n"])
        else:
            n = int(rec.get("n_att", 1))
            s = int(rec.get("n_pass", 1 if rec.get("passed") else 0))
        if n <= 0:
            continue
        _add(cells, (v, cl), s, n)
        _add(variants, v, s, n)
        tot_s += s
        tot_n += n
    return Counts(cells=cells, variants=variants, total=(tot_s, tot_n))


def variant_estimate(counts: Counts, variant: Variant, m2: float,
                     pool_fallback: float = 0.5) -> float:
    """Level-2 estimate: variant rollup shrunk toward the pool global mean."""
    s_var, n_var = counts.variant(variant)
    return shrink(s_var, n_var, counts.pool_p(pool_fallback), m2)


def estimate(counts: Counts, variant: Variant, cluster: Cluster,
             m1: float, m2: float, pool_fallback: float = 0.5) -> float:
    """Two-level chain estimate for a (variant, cluster) cell.

    cell shrinks toward the variant rollup (strength m1); the variant rollup
    shrinks toward the pool global mean (strength m2). Consistency limits:
      m1 == 0 and cell has data -> raw cell MLE
      cell empty                -> variant_estimate (which itself -> pool as m2->inf)
    """
    var_est = variant_estimate(counts, variant, m2, pool_fallback)
    s_cell, n_cell = counts.cell(variant, cluster)
    return shrink(s_cell, n_cell, var_est, m1)


def mle(counts: Counts, variant: Variant, cluster: Cluster, empty: float) -> float:
    """Raw per-cell MLE with an explicit empty-cell convention (0.5 or 0.7)."""
    s, n = counts.cell(variant, cluster)
    if n <= 0:
        return empty
    return s / n
