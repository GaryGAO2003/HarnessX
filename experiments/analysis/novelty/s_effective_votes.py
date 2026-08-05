# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""How many independent variants is the pool actually worth?

Two questions that close out the diversity line, both answerable from the error
matrix alone.

**Effective votes.** For correlated Bernoulli voters the variance of the vote
count is N*p(1-p)*[1 + (N-1)*rho], so the pool of N variants carries the
information of

    N_eff = N / (1 + (N-1) * rho)

independent voters, where rho is the mean pairwise correlation of their ERROR
indicators. As rho approaches 1, N_eff approaches 1 no matter how many variants
were bred. This is the design-effect form of the same statement Tumer & Ghosh
(1996) proved for the added error of a combiner: E_add_ens = E_add*(1+d(N-1))/N,
which goes to E_add as d goes to 1. It is also directly comparable to a
published result on LLM judge panels, where nine frontier judges from seven
families were found to be worth about two independent votes.

**Pool-relative credit.** The gate credits a candidate for tasks IT newly
solves. For a pool that is going to be consumed as an ensemble, solving a task
the rest of the pool already solves is worth nothing. So: of the tasks the gate
actually counted as improvements, how many were already covered by the other
variants? That number is the size of the accounting error, if there is one.
"""
from __future__ import annotations

import collections
import json
import math
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import n_answer_selection as NA  # noqa: E402

RUNS = NA.RUNS
_IMP = re.compile(r"improved=\[(.*?)\]", re.S)
_ID = re.compile(r"'([^']+)'")


def error_matrix(run, min_cov=40):
    """variant -> {task: error rate}, restricted to tasks all of them attempted."""
    cell = collections.defaultdict(lambda: collections.defaultdict(lambda: [0, 0]))
    for c in NA.load_cells([run]):
        d = cell[c["variant"]][c["task"]]
        for a in c["atts"]:
            d[0] += 1
            d[1] += a["passed"]
    keep = {v: td for v, td in cell.items() if len(td) >= min_cov}
    if len(keep) < 2:
        return None, None
    common = sorted(set.intersection(*(set(td) for td in keep.values())))
    if len(common) < 20:
        return None, None
    vs = sorted(keep)
    E = [[1.0 - keep[v][t][1] / keep[v][t][0] for t in common] for v in vs]
    return vs, E


def mean_pairwise_corr(E):
    n = len(E)
    m = len(E[0])
    mus = [sum(r) / m for r in E]
    sds = [math.sqrt(max(sum((x - mu) ** 2 for x in r) / m, 1e-12))
           for r, mu in zip(E, mus)]
    tot = cnt = 0.0
    for i in range(n):
        for j in range(i + 1, n):
            cov = sum((E[i][k] - mus[i]) * (E[j][k] - mus[j])
                      for k in range(m)) / m
            tot += cov / (sds[i] * sds[j])
            cnt += 1
    return tot / cnt if cnt else float("nan")


def pool_relative_credit(run):
    """Of the tasks the gate counted as improvements, how many were already
    solved by the OTHER variants in the pool at that point?"""
    pr = RUNS / run / "pool_report.json"
    if not pr.exists():
        return None
    d = json.loads(pr.read_text(encoding="utf-8"))
    cands = (d.get("candidate_diagnostics") or {}).get("candidates") or []
    # who solves what, per variant, over the whole run
    solved = collections.defaultdict(set)
    for c in NA.load_cells([run]):
        if any(a["passed"] for a in c["atts"]):
            solved[c["variant"]].add(c["task"])
    out = []
    for c in cands:
        if c.get("decision") not in ("apply", "fork"):
            continue
        m = _IMP.search(c.get("archive_reason") or "")
        if not m:
            continue
        ids = [str(x) for x in _ID.findall(m.group(1))]
        if not ids:
            continue
        tgt = c.get("target_variant_id")
        others = set()
        for v, s in solved.items():
            if v != tgt:
                others |= s
        covered = sum(1 for t in ids if t in others)
        out.append((c.get("round"), tgt, len(ids), covered))
    return out


def main(runs):
    print("=" * 76)
    print("EFFECTIVE NUMBER OF INDEPENDENT VARIANTS")
    print("=" * 76)
    print(f"  {'run':<14}{'N':>4}{'mean err rho':>14}{'N_eff':>8}"
          f"{'N_eff/N':>10}")
    print("  " + "-" * 48)
    for run in runs:
        vs, E = error_matrix(run)
        if not vs:
            continue
        rho = mean_pairwise_corr(E)
        n = len(vs)
        neff = n / (1 + (n - 1) * rho) if (1 + (n - 1) * rho) > 0 else float("inf")
        print(f"  {run:<14}{n:>4}{rho:>14.3f}{neff:>8.2f}{neff/n:>10.2f}")
    print("\n  rho is the mean pairwise correlation of the variants' ERROR")
    print("  vectors. N_eff = N / (1 + (N-1)rho) is how many independent voters")
    print("  that pool is worth. Published comparison: nine frontier LLM judges")
    print("  from seven model families were measured at about two effective votes.")

    print("\n" + "=" * 76)
    print("POOL-RELATIVE CREDIT: were the gate's 'improvements' already covered?")
    print("=" * 76)
    print(f"  {'run':<14}{'rnd':>4}{'target':>8}{'|improved|':>12}"
          f"{'already in pool':>17}{'share':>8}")
    print("  " + "-" * 63)
    tot_i = tot_c = 0
    for run in runs:
        rows = pool_relative_credit(run)
        if not rows:
            continue
        for rnd, tgt, n_i, cov in rows:
            tot_i += n_i
            tot_c += cov
            print(f"  {run:<14}{str(rnd):>4}{str(tgt):>8}{n_i:>12}{cov:>17}"
                  f"{100*cov/max(n_i,1):>7.0f}%")
    if tot_i:
        print("  " + "-" * 63)
        print(f"  {'TOTAL':<14}{'':>4}{'':>8}{tot_i:>12}{tot_c:>17}"
              f"{100*tot_c/tot_i:>7.0f}%")
        print("\n  A task the rest of the pool already solves adds nothing to an")
        print("  ensemble, but the gate credits it exactly like a task nobody")
        print("  else can do. That share is the size of the mis-accounting --")
        print("  though note it only matters if the pool is consumed as an")
        print("  ensemble, and this one never is: routing sends each task to")
        print("  exactly one variant per round.")


if __name__ == "__main__":
    argv = sys.argv[1:]
    main(argv or [p.name for p in sorted(RUNS.iterdir())
                  if (p / "comparison.json").exists()])
