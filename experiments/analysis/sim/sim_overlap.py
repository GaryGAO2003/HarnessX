"""Does cross-variant OVERLAP measurement open the pool? (zero API)

Forked from ``sim_decision.run_policy``'s NEW branch. Same ground truth, same
candidate generator, same measurement model -- the ONLY thing that changes is
how much *comparable* evidence the router has when it decides who holds a
cluster.

The gap this probes
-------------------
In every policy sim_decision models, routing partitions the bed: a task is
measured by exactly one variant per round. So in the incumbent/challenger test
(sim_decision.py:160-172) a challenger's evidence on the incumbent's cluster
comes only from rounds where it *used* to hold those tasks -- or, if it never
did, ``n == 0`` and ``wilson_lb(0, 0) == 0.0``, so it can never take the
cluster. Every cross-variant comparison is therefore across time (and across
epochs, i.e. across different configs), never same-round.

OVERLAP
-------
Each round, M tasks (rotating over the bed) are measured by *every* live
variant. Those measurements go to a separate decision ledger (``xhist``) that
feeds routing only; they never touch ``ledger``/``hist``, so the gate, the
tiers and the headline are untouched. That is the "decision measurement vs
scoring measurement" split -- the same reason probation works in NEW.

Two cost models
---------------
OVL   additive: the M*(V-1) extra measurements are extra budget.
OVLB  budget-neutral: total measurement calls per round are pinned to
      len(TASKS), so overlap is paid for by measuring fewer routine tasks.
      This is the honest arm -- it answers "is comparable evidence worth more
      than the routine evidence it displaces?"

Headline ``deployed`` is the noise-free true skill of whatever variant each
task is routed to, so overlap cannot inflate it by measuring more; it can only
help by producing *better routing decisions*.

NOTE on reproducibility: sim_decision seeds with ``hash(policy) % 1000``, and
str hashing is salted per process unless PYTHONHASHSEED is set -- so its
absolute numbers are not reproducible across runs. This module uses an explicit
policy->int table instead.
"""
import collections
import pathlib
import random
import statistics
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import sim_decision as S  # noqa: E402  (path set above)

TASKS, P0, KIND, CLUSTER, CLUSTERS = S.TASKS, S.P0, S.KIND, S.CLUSTER, S.CLUSTERS
wilson_lb, gen_candidates, measure = S.wilson_lb, S.gen_candidates, S.measure
N_ROUNDS, K_SLOTS, MAX_VARIANTS = S.N_ROUNDS, S.K_SLOTS, S.MAX_VARIANTS
DELTA, PIN_ROUNDS = S.DELTA, S.PIN_ROUNDS

#: explicit, so runs are reproducible across processes (see module docstring)
POLICY_SEED = {"NEW": 11, "OVL": 22, "OVLB": 33}


def _epoch_counts(store, v, tasks, e0):
    """(k, n) for variant v over `tasks`, counting only rounds >= e0."""
    k = n = 0
    for t in tasks:
        for rr, o in store[v].get(t, {}).items():
            if rr >= e0:
                k += o
                n += 1
    return k, n


def run(policy, seed, p_fix_dead=0.05, m_overlap=12, hetero=0.0, spec_frac=0.15, spec_mode='random'):
    """NEW decision layer, optionally with cross-variant overlap measurement.

    ``hetero`` injects REAL complementarity the simulator otherwise lacks. In
    sim_decision a child differs from its parent only on the ~4 tasks an edit
    touched, so variants are ~96% identical by construction -- under that ground
    truth no routing rule can win, because there is nothing to route *to*. With
    hetero > 0 a fork also becomes genuinely better on a random ``spec_frac``
    slice of the bed, i.e. a real specialist. Sweeping it separates "overlap is
    useless" from "this simulator has no complementarity for overlap to find".
    """
    rng = random.Random(seed * 7919 + POLICY_SEED[policy])
    overlap = policy in ("OVL", "OVLB")
    budget_neutral = policy == "OVLB"

    variants = {"V0": dict(P0)}
    routed = {"V0": set(TASKS)}
    ledger = collections.defaultdict(lambda: collections.defaultdict(lambda: [0, 0]))
    hist = collections.defaultdict(dict)      # scoring+gate evidence
    xhist = collections.defaultdict(dict)     # decision-only overlap evidence
    epoch_start = {"V0": 0}
    prev_prot = collections.defaultdict(set)
    ever_any = set()
    pins = {}
    probation = {}
    m = dict(ships=0, forks=0, rejects=0, deadlock=0, starved=0, wasted=0,
             targeted=0, good_rejected=0, blank=0, meas_calls=0,
             challenger_wins=0, blind_cells=0)

    for r in range(N_ROUNDS):
        live = sorted(variants)

        # ---- pick this round's overlap set (rotating window over the bed) ---
        if overlap and m_overlap > 0 and len(live) > 1:
            start = (r * m_overlap) % len(TASKS)
            ovl = [TASKS[(start + i) % len(TASKS)] for i in range(m_overlap)]
        else:
            ovl = []

        # ---- routing freeze -------------------------------------------------
        if r > 0:
            new_routed = {v: set() for v in variants}
            for c in CLUSTERS:
                ctasks = [t for t in TASKS if CLUSTER[t] == c]
                holder = max(live, key=lambda v: sum(1 for t in ctasks if t in routed.get(v, ())))
                inc_k, inc_n = _epoch_counts(hist, holder, ctasks, epoch_start.get(holder, 0))
                if overlap:
                    xk, xn = _epoch_counts(xhist, holder, ctasks, epoch_start.get(holder, 0))
                    inc_k, inc_n = inc_k + xk, inc_n + xn
                inc_hat = inc_k / inc_n if inc_n else 0.5

                best, best_lb = holder, None
                for v in live:
                    if v == holder:
                        continue
                    e0 = epoch_start.get(v, 0)
                    k, n = _epoch_counts(hist, v, ctasks, e0)
                    if overlap:
                        xk, xn = _epoch_counts(xhist, v, ctasks, e0)
                        k, n = k + xk, n + xn
                    if n == 0:
                        m["blind_cells"] += 1   # challenger cannot be judged at all
                    lb = wilson_lb(k, n)
                    if lb > inc_hat + DELTA and (best_lb is None or lb > best_lb):
                        best, best_lb = v, lb
                if best != holder:
                    m["challenger_wins"] += 1
                for t in ctasks:
                    new_routed[best].add(t)

            for t, (h, exp) in list(pins.items()):
                if r < exp and h in variants:
                    for v in new_routed:
                        new_routed[v].discard(t)
                    new_routed[h].add(t)
                else:
                    del pins[t]
            routed = new_routed

        # ---- measurement ----------------------------------------------------
        # Budget-neutral: overlap displaces routine measurement, so the total
        # number of measure() calls stays at len(TASKS) as in the baseline.
        skip = set()
        if budget_neutral and ovl:
            extra = len(ovl) * (len(live) - 1)
            pool_for_skip = [t for t in TASKS if t not in set(ovl)]
            rng.shuffle(pool_for_skip)
            skip = set(pool_for_skip[:min(extra, len(pool_for_skip))])

        meas = {}
        for v, ts in routed.items():
            todo = [t for t in ts if t not in skip]
            meas[v] = measure(rng, variants[v], todo)
            m["meas_calls"] += len(todo)
            for t, o in meas[v].items():
                le = ledger[v][t]
                le[0] += o
                le[1] += 1
                hist[v].setdefault(t, {})[r] = o
                if o:
                    ever_any.add(t)

        # ---- overlap pass: every live variant measures the same tasks -------
        # Decision-only: goes to xhist, never to ledger/hist/ever_any, so the
        # gate, the tiers and the headline are byte-identical to NEW.
        for v in live:
            for t in ovl:
                if t in routed.get(v, ()) and t in meas.get(v, {}):
                    xhist[v].setdefault(t, {})[r] = meas[v][t]
                    continue
                o = 1 if rng.random() < variants[v][t] else 0
                xhist[v].setdefault(t, {})[r] = o
                m["meas_calls"] += 1

        # ---- probation ------------------------------------------------------
        for v in list(probation):
            pr = probation[v]
            pm = [meas[v][t] for t in pr["protected"] if t in meas.get(v, {})]
            rate = sum(pm) / len(pm) if pm else 1.0
            if rate < pr["base_rate"] - 0.15:
                variants[v] = pr["pre_p"]
                m["rejects"] += 1
            else:
                m["ships"] += 1
                prev_prot[v] = set(pr["protected"])
                epoch_start[v] = pr["ship_round"]
            del probation[v]

        # ---- matrix layer ---------------------------------------------------
        def tiers(v):
            out = {}
            e0 = epoch_start.get(v, 0)
            bridge = prev_prot[v] if (r - e0) < 2 else set()
            for t in routed.get(v, ()):
                cur = [o for rr, o in hist[v].get(t, {}).items() if rr >= e0]
                k, n = sum(cur), len(cur)
                if wilson_lb(k, n) >= 0.5 or t in bridge:
                    out[t] = "PROT"
                elif k > 0:
                    out[t] = "A"
                elif t in ever_any:
                    out[t] = "B"
                else:
                    out[t] = "C"
            return out

        tv = {v: tiers(v) for v in variants}
        s = {v: sum(1 for x in tv[v].values() if x in "AB") for v in variants}
        tot = sum(s.values())
        alloc = {}
        if tot:
            qs = {v: K_SLOTS * s[v] / tot for v in sorted(variants)}
            alloc = {v: int(qs[v]) for v in qs}
            for v, _ in sorted(qs.items(), key=lambda kv: -(kv[1] - int(kv[1]))):
                if sum(alloc.values()) >= K_SLOTS:
                    break
                alloc[v] += 1
        for v in probation:
            alloc[v] = 0
        for v in variants:
            if r - epoch_start.get(v, 0) + 1 < 2:
                alloc[v] = 0

        for v in variants:
            if len(routed.get(v, ())) >= 20 and alloc.get(v, 0) == 0 and v not in probation:
                m["starved"] += 1

        # ---- per-variant pipeline -------------------------------------------
        any_pool = False
        for v in sorted(variants):
            n_slots = alloc.get(v, 0)
            if n_slots <= 0:
                continue
            tk = sorted(routed[v])
            tv_ = tv[v]
            pool = [t for t in tk if tv_.get(t) in ("A", "B")]
            prot = {t for t in tk if tv_.get(t) == "PROT"}
            if pool:
                any_pool = True
            cands = gen_candidates(rng, pool, n_slots, variants[v], p_fix_dead)
            cands = [c if c is not None else gen_candidates(rng, pool, 1, variants[v], p_fix_dead)[0]
                     for c in cands]
            shipped = False
            for cand in cands:
                if cand is None:
                    m["blank"] += 1
                    continue
                m["targeted"] += len(cand["targets"])
                m["wasted"] += sum(1 for t in cand["targets"] if KIND[t] == "dead")
                if shipped:
                    continue
                cand_p = dict(variants[v])
                cand_p.update(cand["eff"])
                gm = measure(rng, cand_p, tk)
                m["meas_calls"] += len(tk)
                improved = {t for t in tk if gm[t] and t not in prot}
                regressed = {t for t in tk if not gm[t] and t in prot}
                true_gain = sum(cand_p.get(t, P0[t]) - variants[v].get(t, P0[t]) for t in TASKS)
                if not improved:
                    m["rejects"] += 1
                    if true_gain > 0.3:
                        m["good_rejected"] += 1
                    continue
                if regressed and len(regressed) <= 2 + 0.05 * len(prot):
                    regressed = set()
                if not regressed:
                    base = [t for t in prot if t in meas.get(v, {})]
                    probation[v] = dict(
                        pre_p=dict(variants[v]), protected=set(prot), ship_round=r,
                        base_rate=(sum(meas[v][t] for t in base) / max(1, len(base))))
                    variants[v] = cand_p
                    shipped = True
                elif len(variants) < MAX_VARIANTS:
                    child = f"V{len(variants)}"
                    if hetero > 0:
                        if spec_mode == "cluster":
                            # specialism ALIGNED with the routing partition
                            c = rng.choice(CLUSTERS)
                            spec = [t for t in TASKS if CLUSTER[t] == c]
                        else:
                            # specialism scattered across the bed, ignoring clusters
                            spec = rng.sample(TASKS, int(len(TASKS) * spec_frac))
                        cand_p = dict(cand_p)
                        for t in spec:
                            cand_p[t] = min(0.95, cand_p[t] + hetero)
                    variants[child] = cand_p
                    routed[child] = set(improved)
                    routed[v] -= improved
                    epoch_start[child] = r
                    prev_prot[child] = set(prot)
                    for t, o in gm.items():
                        hist[child].setdefault(t, {})[r] = o
                        le = ledger[child][t]
                        le[0] += o
                        le[1] += 1
                    for t in regressed:
                        pins[t] = (v, r + 1 + PIN_ROUNDS)
                    m["ships"] += 1
                    m["forks"] += 1
                    shipped = True
                else:
                    m["rejects"] += 1
        if not any_pool:
            m["deadlock"] += 1

    m["deployed"] = sum(variants[v][t] for v, ts in routed.items() for t in ts) / len(TASKS)
    m["variants"] = len(variants)
    m["carriers"] = sum(1 for v in routed if routed[v])
    # VBS / SBS on the FINAL pool, in true skill (only the simulator sees these).
    # vbs = route every task to its best variant; sbs = the single best variant
    # for the whole bed. vbs - sbs is the entire prize any router can win; if it
    # is ~0 the pool has no complementarity to route to, whatever the rule.
    m["vbs"] = sum(max(variants[v][t] for v in variants) for t in TASKS) / len(TASKS)
    m["sbs"] = max(sum(variants[v][t] for t in TASKS) for v in variants) / len(TASKS)
    return m


def bench(policies, seeds, p_fix_dead=0.05, m_overlap=12, hetero=0.0, spec_mode='random', label=""):
    print(f"\n=== {label}   seeds={seeds}  M={m_overlap}  hetero={hetero}  p_fix_dead={p_fix_dead} ===")
    print(f"{'policy':<7}{'部署真值':>10}{'±':>7}{'SBS':>8}{'VBS':>8}{'VBS-SBS':>9}"
          f"{'已捕获%':>9}{'带题变体':>9}{'挑战成功':>9}{'盲格':>7}{'测量次数':>10}")
    out = {}
    for pol in policies:
        rs = [run(pol, s, p_fix_dead, m_overlap, hetero, 0.15, spec_mode) for s in range(seeds)]
        dep = [x["deployed"] for x in rs]
        mu, sd = statistics.mean(dep), statistics.pstdev(dep)
        avg = lambda k: statistics.mean(x[k] for x in rs)  # noqa: E731
        out[pol] = (mu, sd, dep, rs)
        sbs, vbs = avg("sbs"), avg("vbs")
        gap = vbs - sbs
        capt = 100 * (mu - sbs) / gap if gap > 1e-9 else float("nan")
        print(f"{pol:<7}{mu:>10.4f}{sd:>7.3f}{sbs:>8.4f}{vbs:>8.4f}{gap*100:>8.2f}pp"
              f"{capt:>8.1f}%{avg('carriers'):>9.2f}{avg('challenger_wins'):>9.2f}"
              f"{avg('blind_cells'):>7.1f}{avg('meas_calls'):>10.0f}")
    if "NEW" in out:
        b = out["NEW"][2]
        for pol in policies:
            if pol == "NEW":
                continue
            d = out[pol][2]
            diff = [x - y for x, y in zip(d, b)]
            wins = 100 * sum(1 for x in diff if x > 0) / len(diff)
            mu = statistics.mean(diff)
            se = statistics.pstdev(diff) / (len(diff) ** 0.5)
            print(f"  {pol} - NEW: {mu*100:+.2f}pp  SE {se*100:.2f}  "
                  f"逐种子胜率 {wins:.1f}%  95%CI [{(mu-1.96*se)*100:+.2f}, {(mu+1.96*se)*100:+.2f}]")
    return out


if __name__ == "__main__":
    seeds = int(sys.argv[1]) if len(sys.argv) > 1 else 120
    print(f"床: {len(TASKS)} 题")
    print("\n##### C. 专精粒度 vs 路由粒度 (hetero=0.2, M=12) #####")
    for mode in ("random", "cluster"):
        bench(["NEW", "OVL", "OVLB"], seeds=seeds, m_overlap=12, hetero=0.2,
              spec_mode=mode, label=f"spec_mode={mode}")
