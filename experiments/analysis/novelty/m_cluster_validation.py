# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""Is there a partition of the bed that whole-cluster routing could actually use?

sim_overlap showed the fork in the road: when specialism lines up with the
routing partition, overlap measurement buys +1.53pp; when it is scattered, it
buys nothing. l_rank_ablation showed GAIA level explains only 6.9-10.6% of the
(overfit) specialism axis. So the question is whether some OTHER partition does
better -- and the natural candidate is "how does this task fail", not "how hard
is it".

Why not difficulty: GAIA level is a proxy for fitness, and QD's standing rule is
that a behaviour descriptor must not be a fitness axis, or the archive collapses.
Failure mode is a behaviour axis: two tasks that both fail, one by running out of
steps and one by answering wrong, need different fixes.

Scored by what the CURRENT router could actually extract:

    cluster_gap(C) = sum_c (|c|/T) * max_v mean_{t in c} p(v,t)   <- whole-cluster
                   - max_v mean_t p(v,t)                          <- single best

i.e. "assign each cluster wholesale to its best variant" minus "run the single
best variant on everything". That is exactly the ceiling of argmax-per-cluster.

The number is meaningless without a null: more clusters ALWAYS raises it, purely
by winner's curse over noisier per-cluster means. So every partition is compared
against random partitions with the SAME cluster sizes.

Features are conditioned on failure (fraction of FAILED attempts of each kind),
so task difficulty is divided out and only the failure's character remains.
"""
from __future__ import annotations

import collections
import json
import pathlib
import sys

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import l_rank_ablation as L  # noqa: E402

ROOT = L.ROOT
RUNS = L.RUNS


# --------------------------------------------------------------------------
def per_task_features(run: str):
    """Behaviour features per task, conditioned on FAILURE so that difficulty
    (= how often it fails) is divided out and only the character remains."""
    d = json.loads((RUNS / run / "comparison.json").read_text(encoding="utf-8"))
    fail = collections.defaultdict(lambda: collections.Counter())
    steps_f = collections.defaultdict(list)
    tools = collections.defaultdict(collections.Counter)
    n_att = collections.Counter()
    for recs in d.get("rounds") or []:
        for rec in recs or []:
            t = str(rec.get("task_id"))
            tc = rec.get("tool_call_counts") or {}
            if isinstance(tc, dict):
                for k, v in tc.items():
                    tools[t][k] += v
            for a in rec.get("attempts") or []:
                n_att[t] += 1
                if a.get("passed"):
                    continue
                er = a.get("exit_reason")
                kind = ("budget" if er == "budget_exceeded"
                        else "infra" if er == "error" else "wrong")
                fail[t][kind] += 1
                if a.get("steps") is not None:
                    steps_f[t].append(a["steps"])
    return fail, steps_f, tools, n_att


def build_feature_matrix(tasks, fail, steps_f, tools, mode):
    rows = []
    tool_names = sorted({k for t in tasks for k in tools[t]})
    for t in tasks:
        f = fail[t]
        tot = sum(f.values())
        if mode == "failmode":
            v = ([f["budget"] / tot, f["infra"] / tot, f["wrong"] / tot] if tot
                 else [1 / 3, 1 / 3, 1 / 3])
            s = steps_f[t]
            v.append((np.mean(s) / 20.0) if s else 0.5)   # steps are capped at 20
        else:  # behaviour: which tools this task drives the agent to use
            tc = tools[t]
            s_ = sum(tc.values()) or 1
            v = [tc.get(k, 0) / s_ for k in tool_names]
        rows.append(v)
    X = np.array(rows, float)
    sd = X.std(axis=0)
    keep = sd > 1e-9
    X = (X[:, keep] - X[:, keep].mean(axis=0)) / sd[keep]
    return X


def kmeans(X, k, seed=0, iters=100):
    rng = np.random.default_rng(seed)
    C = X[rng.choice(len(X), k, replace=False)].copy()
    lab = np.zeros(len(X), int)
    for _ in range(iters):
        d = ((X[:, None, :] - C[None, :, :]) ** 2).sum(-1)
        new = d.argmin(1)
        if (new == lab).all():
            break
        lab = new
        for j in range(k):
            if (lab == j).any():
                C[j] = X[lab == j].mean(0)
    return lab


# --------------------------------------------------------------------------
def cluster_gap(P, lab):
    """Whole-cluster oracle minus single-best-variant, in pass-rate points."""
    T = P.shape[1]
    tot = 0.0
    for c in np.unique(lab):
        m = lab == c
        tot += m.sum() * P[:, m].mean(axis=1).max()
    return tot / T - P.mean(axis=1).max()


def null_gaps(P, lab, reps=400, seed=0):
    """Same cluster SIZES, random membership. Carries the same winner's curse."""
    rng = np.random.default_rng(seed)
    out = []
    for _ in range(reps):
        out.append(cluster_gap(P, rng.permutation(lab)))
    return np.array(out)


def evaluate(P, lab, name, reps=400):
    g = cluster_gap(P, lab)
    null = null_gaps(P, lab, reps)
    q95 = np.percentile(null, 95)
    sizes = sorted(collections.Counter(lab).values(), reverse=True)
    excess = g - null.mean()
    verdict = "BEATS null" if g > q95 else "within null"
    print(f"  {name:<26} k={len(set(lab))}  sizes={str(sizes):<18} "
          f"gap {g*100:+6.2f}pp   null mean {null.mean()*100:+6.2f} "
          f"p95 {q95*100:+6.2f}   excess {excess*100:+6.2f}pp   {verdict}")
    return g, null


def main(run="s1k8b103", min_cov=60):
    cells = L.load_cells(run)
    cov = {v: set(td) for v, td in cells.items() if len(td) >= min_cov}
    tasks = sorted(set.intersection(*cov.values()))
    vs = sorted(cov)
    P = np.array([[cells[v][t][0] / cells[v][t][1] for t in tasks] for v in vs])
    lvl = L.load_levels()
    print(f"run={run}  dense sub-matrix: {len(vs)} variants x {len(tasks)} tasks "
          f"(100% filled)")
    print(f"  variants: {vs}")
    print(f"  level mix: {dict(collections.Counter(lvl.get(t,'?') for t in tasks))}")
    print(f"  single best variant (SBS) = {P.mean(axis=1).max():.4f}"
          f"   per-task best (VBS) = {P.max(axis=0).mean():.4f}"
          f"   full gap {100*(P.max(axis=0).mean()-P.mean(axis=1).max()):+.2f}pp")
    print("\n  (VBS here is per-TASK routing -- the unreachable ceiling. Below is what")
    print("   whole-CLUSTER routing, which is what the system actually does, could get.)\n")

    fail, steps_f, tools, n_att = per_task_features(run)

    print("partition                    k  sizes              cluster gap   vs null")
    lab_lvl = np.array([lvl.get(t, "?") for t in tasks])
    _, _ = evaluate(P, lab_lvl, "gaia_level (current)")

    for mode in ("failmode", "behaviour"):
        X = build_feature_matrix(tasks, fail, steps_f, tools, mode)
        if X.shape[1] == 0:
            print(f"  {mode}: no usable features")
            continue
        for k in (2, 3, 4):
            lab = kmeans(X, k, seed=0)
            if len(set(lab)) < k:
                continue
            evaluate(P, lab, f"{mode} k={k} ({X.shape[1]}d)")

    print("\n  READ: 'excess' is the gap above what a random partition of the same")
    print("  shape already gets. Only a partition that BEATS null carries routable")
    print("  structure; anything 'within null' is winner's curse over noisy means.")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "s1k8b103")
