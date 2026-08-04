# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""E1 -- is there any SPECIALISM structure in the variant x task matrix?

The variant-pool thesis needs variants whose *relative* ranking flips across
tasks. This tests exactly that, on the real matrices, offline, zero API.

Models (logit space, binomial likelihood on (passes, attempts) cells):

    M0   logit p = mu                          everything the same
    M1   logit p = mu + a_v + b_t              <- THE NULL HYPOTHESIS.
                                               Each variant has one overall
                                               ability, each task one overall
                                               difficulty, NO interaction. Under
                                               M1 argmax_v p(v,t) does not depend
                                               on t, so VBS == SBS and routing is
                                               worth exactly zero.
    M2   M1 + u_v . w_t   (rank 1)             one specialism axis
    M3   M1 + rank 2                           two specialism axes

So the whole question is: does M2 beat M1 out of sample, by more than pure
sampling noise can fake?

Held-out: attempts inside each cell are split train/test by a hypergeometric
draw (the correct split for aggregated binomial counts), so we score the
ability to predict *new attempts on cells the model has seen* -- which is what
routing actually needs.

Noise floor: a parametric bootstrap under M1. Synthetic matrices are generated
from the fitted M1 with the REAL attempt structure, so by construction they hold
no specialism at all; refitting M1 vs M2 on them shows how much M2 wins on noise
alone. The observed win must clear that distribution's 95th percentile.

Readouts
    1. held-out per-attempt NLL for M0..M3 + the M1 bootstrap noise floor
    2. VBS - SBS from empirical p_hat / M1 fit / M2 fit  (de-noised headroom)
    3. correlation of the M2 task loading w_t with GAIA level -- tells us whether
       any specialism that exists is ALIGNED with the routing partition.
       sim_overlap.py showed those two cases need completely different fixes.
"""
from __future__ import annotations

import collections
import json
import math
import pathlib
import sys

import numpy as np

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = pathlib.Path(__file__).resolve().parents[3]
RUNS = ROOT / "recipe/gaia_evolver/runs"


# --------------------------------------------------------------------------
# data
# --------------------------------------------------------------------------
def load_cells(run: str, drop_reuse: bool = True):
    """(variant, task) -> [passes, attempts], pooled over rounds."""
    d = RUNS / run
    cells = collections.defaultdict(lambda: collections.defaultdict(lambda: [0, 0]))
    for p in sorted(d.glob("R*/pool_state.json")):
        st = json.loads(p.read_text(encoding="utf-8"))
        src = st.get("active_score_source") or {}
        for v, tasks in (st.get("active_pool_measurements") or {}).items():
            if drop_reuse and src.get(v) == "candidate_reuse":
                continue  # in-sample gate copies are circular
            for t, rec in tasks.items():
                if isinstance(rec, dict):
                    s, n = rec.get("succ"), rec.get("n_att")
                else:
                    s, n = rec[0], rec[1]
                if n:
                    cells[v][str(t)][0] += s
                    cells[v][str(t)][1] += n
    return cells


def load_levels():
    ds = json.loads((ROOT / "recipe/gaia_evolver/data/webthinker_gaia_dev.json").read_text("utf-8"))
    rows = ds if isinstance(ds, list) else list(ds.values())
    return {str(r.get("task_id") or r.get("id")): str(r.get("Level", r.get("level"))) for r in rows}


def build(runs):
    """Stack several runs. Same bed, but different H0 -> V0 of run A is NOT
    V0 of run B, so variants are namespaced by run."""
    vi, ti, K, N, vname = [], [], [], [], []
    tasks = {}
    vmap = {}
    for run in runs:
        for v, td in sorted(load_cells(run).items()):
            key = f"{run}:{v}"
            if key not in vmap:
                vmap[key] = len(vmap)
                vname.append(key)
            for t, (k, n) in td.items():
                if t not in tasks:
                    tasks[t] = len(tasks)
                vi.append(vmap[key]); ti.append(tasks[t]); K.append(k); N.append(n)
    tname = [None] * len(tasks)
    for t, i in tasks.items():
        tname[i] = t
    return (np.array(vi), np.array(ti), np.array(K, float), np.array(N, float),
            vname, tname)


# --------------------------------------------------------------------------
# model
# --------------------------------------------------------------------------
def _sig(x):
    return 1.0 / (1.0 + np.exp(-np.clip(x, -30, 30)))


def fit(vi, ti, K, N, V, T, rank, l2=1e-2, steps=3000, lr=0.05, seed=0, quiet=True):
    """Binomial logistic factor model, fitted with Adam (no scipy here)."""
    rng = np.random.default_rng(seed)
    mu = np.array(0.0)
    al = np.zeros(V)
    be = np.zeros(T)
    U = rng.normal(0, 0.01, (V, rank)) if rank else np.zeros((V, 0))
    W = rng.normal(0, 0.01, (T, rank)) if rank else np.zeros((T, 0))
    params = [mu, al, be, U, W]
    ms = [np.zeros_like(p) for p in params]
    vs = [np.zeros_like(p) for p in params]
    b1, b2, eps = 0.9, 0.999, 1e-8

    for step in range(1, steps + 1):
        inter = np.einsum("ir,ir->i", U[vi], W[ti]) if rank else 0.0
        eta = mu + al[vi] + be[ti] + inter
        p = _sig(eta)
        g_eta = (N * p - K)                      # d NLL / d eta
        g_mu = g_eta.sum()
        g_al = np.bincount(vi, g_eta, minlength=V)
        g_be = np.bincount(ti, g_eta, minlength=T)
        if rank:
            g_U = np.zeros((V, rank)); g_W = np.zeros((T, rank))
            for r in range(rank):
                g_U[:, r] = np.bincount(vi, g_eta * W[ti, r], minlength=V)
                g_W[:, r] = np.bincount(ti, g_eta * U[vi, r], minlength=T)
            g_U += l2 * U; g_W += l2 * W
        else:
            g_U = np.zeros((V, 0)); g_W = np.zeros((T, 0))
        # weak ridge on main effects too, purely for identifiability
        g_al = g_al + 1e-4 * al
        g_be = g_be + 1e-4 * be

        for i, (pm, gr) in enumerate(zip(params, [g_mu, g_al, g_be, g_U, g_W])):
            ms[i] = b1 * ms[i] + (1 - b1) * gr
            vs[i] = b2 * vs[i] + (1 - b2) * gr * gr
            mh = ms[i] / (1 - b1 ** step)
            vh = vs[i] / (1 - b2 ** step)
            upd = lr * mh / (np.sqrt(vh) + eps)
            if i == 0:
                mu = mu - upd
                params[0] = mu
            else:
                pm -= upd
        al, be, U, W = params[1], params[2], params[3], params[4]
    return mu, al, be, U, W


def predict(mu, al, be, U, W, vi, ti):
    inter = np.einsum("ir,ir->i", U[vi], W[ti]) if U.shape[1] else 0.0
    return _sig(mu + al[vi] + be[ti] + inter)


def nll_per_attempt(p, K, N):
    p = np.clip(p, 1e-9, 1 - 1e-9)
    return -(K * np.log(p) + (N - K) * np.log(1 - p)).sum() / N.sum()


def full_matrix(mu, al, be, U, W, V, T):
    inter = U @ W.T if U.shape[1] else 0.0
    return _sig(mu + al[:, None] + be[None, :] + inter)


def vbs_sbs(P):
    """P is V x T of per-attempt pass probability."""
    return P.max(axis=0).mean(), P.mean(axis=1).max()


# --------------------------------------------------------------------------
# experiment
# --------------------------------------------------------------------------
def split(K, N, frac, rng):
    n_te = np.floor(N * frac).astype(int)
    k_te = np.array([rng.hypergeometric(int(k), int(n - k), int(m)) if m > 0 and n > 0 else 0
                     for k, n, m in zip(K, N, n_te)], float)
    return K - k_te, N - n_te.astype(float), k_te, n_te.astype(float)


def run(runs, boots=200, seed=0):
    vi, ti, K, N, vname, tname = build(runs)
    V, T = len(vname), len(tname)
    LVL = load_levels()
    print(f"runs={runs}  variants={V}  tasks={T}  cells={len(K)}  "
          f"attempts={int(N.sum())}  density={len(K)/(V*T):.1%}")
    print("  per-variant  cells / attempts / mean p:")
    for i, nm in enumerate(vname):
        s = vi == i
        print(f"    {nm:<22} {s.sum():>4}  {int(N[s].sum()):>6}  {K[s].sum()/max(1,N[s].sum()):.4f}")

    rng = np.random.default_rng(seed)
    Ktr, Ntr, Kte, Nte = split(K, N, 0.30, rng)
    ok = Nte > 0
    print(f"\nheld-out: {int(Nte.sum())} attempts over {ok.sum()} cells "
          f"({int(Ntr.sum())} train)")

    print(f"\n{'model':<6}{'rank':>5}{'train NLL':>12}{'held-out NLL':>14}{'vs M1':>10}")
    fits, hos = {}, {}
    for name, rank in (("M0", None), ("M1", 0), ("M2", 1), ("M3", 2)):
        if rank is None:
            p_tr = np.full(len(K), Ktr.sum() / max(1e-9, Ntr.sum()))
            tr = nll_per_attempt(p_tr, Ktr, Ntr)
            ho = nll_per_attempt(p_tr[ok], Kte[ok], Nte[ok])
            fits[name] = None
        else:
            f = fit(vi, ti, Ktr, Ntr, V, T, rank, seed=seed)
            fits[name] = f
            tr = nll_per_attempt(predict(*f, vi, ti), Ktr, Ntr)
            ho = nll_per_attempt(predict(*f, vi[ok], ti[ok]), Kte[ok], Nte[ok])
        hos[name] = ho
        d = "" if name == "M1" else f"{(hos.get('M1', ho) - ho)*1000:+9.3f}"
        print(f"{name:<6}{str(rank):>5}{tr:>12.5f}{ho:>14.5f}{d:>10}")
    obs_gain = (hos["M1"] - hos["M2"]) * 1000
    print(f"\n  observed M2 gain over M1 = {obs_gain:+.3f} milli-nats/attempt")

    # ---- parametric bootstrap under M1 (no specialism by construction) -----
    print(f"\n  parametric bootstrap under M1 ({boots} draws)...", flush=True)
    mu1, al1, be1, U1, W1 = fits["M1"]
    p1 = predict(mu1, al1, be1, U1, W1, vi, ti)
    null = []
    brng = np.random.default_rng(seed + 1)
    for b in range(boots):
        Kb = brng.binomial(N.astype(int), p1).astype(float)
        Kbtr, Nbtr, Kbte, Nbte = split(Kb, N, 0.30, brng)
        okb = Nbte > 0
        # SAME optimiser budget as the real fit, or the null is not comparable
        f1 = fit(vi, ti, Kbtr, Nbtr, V, T, 0, seed=b)
        f2 = fit(vi, ti, Kbtr, Nbtr, V, T, 1, seed=b)
        h1 = nll_per_attempt(predict(*f1, vi[okb], ti[okb]), Kbte[okb], Nbte[okb])
        h2 = nll_per_attempt(predict(*f2, vi[okb], ti[okb]), Kbte[okb], Nbte[okb])
        null.append((h1 - h2) * 1000)
    null = np.array(null)
    q95 = np.percentile(null, 95)
    print(f"  noise floor: mean {null.mean():+.3f}  95th pct {q95:+.3f}  "
          f"max {null.max():+.3f}  (milli-nats)")
    if obs_gain <= 0:
        verdict = ("NO SPECIALISM DETECTED -- M2 does not even beat M1 on the real "
                   "data (gain <= 0); the noise floor is not the binding question")
    elif obs_gain > q95:
        verdict = "CLEARS the noise floor -- specialism structure is real"
    else:
        verdict = "DOES NOT clear the noise floor -- the M2 gain is consistent with noise"
    print(f"  >>> observed {obs_gain:+.3f} vs 95th pct {q95:+.3f}")
    print(f"  >>> {verdict}")
    print(f"  >>> empirical p-value = {(null >= obs_gain).mean():.3f}")

    # ---- readout 2: VBS - SBS, empirical vs de-noised ----------------------
    print("\n  VBS - SBS (per-attempt pass rate, whole bed):")
    Pemp = np.full((V, T), np.nan)
    Pemp[vi, ti] = K / N
    col = ~np.isnan(Pemp).all(axis=0)
    Pe = np.where(np.isnan(Pemp), np.nanmean(Pemp, axis=0)[None, :], Pemp)[:, col]
    for label, P in (("empirical p_hat", Pe),
                     ("M1 fit (null)", full_matrix(*fits["M1"], V, T)),
                     ("M2 fit (de-noised)", full_matrix(*fits["M2"], V, T))):
        vbs, sbs = vbs_sbs(P)
        print(f"    {label:<22} VBS {vbs:.4f}  SBS {sbs:.4f}  gap {(vbs-sbs)*100:+6.2f}pp")

    # ---- readout 3: is the specialism aligned with the routing partition? --
    _, _, _, U2, W2 = fits["M2"]
    w = W2[:, 0]
    lv = np.array([LVL.get(t, "?") for t in tname])
    print("\n  M2 task loading w_t by GAIA level (routing partition):")
    for L in sorted(set(lv)):
        s = lv == L
        print(f"    L{L}: n={s.sum():>3}  mean {w[s].mean():+.4f}  sd {w[s].std():.4f}")
    grand = w.mean()
    ss_tot = ((w - grand) ** 2).sum()
    ss_bet = sum((lv == L).sum() * (w[lv == L].mean() - grand) ** 2 for L in set(lv))
    print(f"    variance of w_t explained by level (eta^2) = {ss_bet/max(1e-12,ss_tot):.4f}")
    print("      ~0 => specialism is scattered; whole-cluster routing CANNOT capture it")
    print("      high => specialism aligns with clusters; current routing can capture it")

    print("\n  M2 variant loadings u_v (lineage redundancy check):")
    for i, nm in enumerate(vname):
        print(f"    {nm:<22} {U2[i,0]:+.4f}")


if __name__ == "__main__":
    boots = int(sys.argv[1]) if len(sys.argv) > 1 else 200
    print("=" * 74)
    print("E1a  s1k8b103 only (8 variants, the deepest pool we have)")
    print("=" * 74)
    run(["s1k8b103"], boots=boots)
    print("\n" + "=" * 74)
    print("E1b  s1k8b103 + e_pervar3 (10 variants; same bed sha, different H0)")
    print("=" * 74)
    run(["s1k8b103", "e_pervar3"], boots=boots)
