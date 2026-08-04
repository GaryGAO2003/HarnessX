"""Monte-Carlo simulation of the DECISION layer only, calibrated on e_pervar3.

Ground truth: each task has a true pass@2 probability p, initialised from the
task's pooled empirical frequency in the real run. Task classes:
  dead   p <= 0.05  -> capability-bound: edits fix with prob P_FIX_DEAD only
  flaky  0.05<p<0.90 -> harness-sensitive: edits fix with prob 0.6 (p -> +0.35)
  stable p >= 0.90
This "edits mostly work on flaky, rarely on dead" is the ONE modelling
assumption; a sensitivity grid varies P_FIX_DEAD at the end.

Three policies share the SAME candidate generator and measurement model; only
the decision rules differ:
  BASE   current mechanism: worst_first on unwindowed cumulative rollup (frozen
         cells included), single target, improved = never-passed-ever,
         regressed = ever-passed, fork seeds child routing with the in-sample
         improved slice, Laplace cluster argmax, no planner retry.
  FALL   F-ALL only: slots to every carrier (prop. to carry), everything else
         as BASE. Tests "parallelism alone doesn't fix the ledger".
  NEW    full method: matrix layer (epoch Wilson, PROT/A/B/C), slots prop. to
         |A+B|, gate improved = flips non-PROTECTED, regressed = fails
         PROTECTED, 1-round probation with rollback, routing Wilson-LB +
         hysteresis + full-gate seeding + pin regressed to parent 3 rounds,
         planner retry.

Headline metric: deployed true skill = mean over the bed of the routed
variant's true p (noise-free, only the simulator can see it).
"""
import collections
import json
import math
import pathlib
import random
import statistics
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = pathlib.Path(__file__).resolve().parents[3]
RUN = ROOT / "recipe/gaia_evolver/runs/e_pervar3"

# ---------------- calibration ------------------------------------------------
st = {}
for d in sorted(RUN.iterdir()):
    p = d / "pool_state.json"
    if d.is_dir() and d.name.startswith("R") and d.name[1:].isdigit() and p.is_file():
        st[int(d.name[1:])] = json.loads(p.read_text(encoding="utf-8"))

freq = collections.defaultdict(lambda: [0, 0])
for r, s in st.items():
    src = s.get("active_score_source") or {}
    for v, tasks in (s.get("active_pool_measurements") or {}).items():
        if src.get(v) == "candidate_reuse":
            continue
        for t, c in tasks.items():
            freq[t][0] += 1 if c[0] >= 1 else 0
            freq[t][1] += 1

ds = json.loads((ROOT / "recipe/gaia_evolver/data/webthinker_gaia_dev.json").read_text(encoding="utf-8"))
rows = ds if isinstance(ds, list) else list(ds.values())
LVL = {str(r.get("task_id") or r.get("id")): str(r.get("Level", r.get("level"))) for r in rows}

TASKS = sorted(freq)
P0, KIND = {}, {}
for t in TASKS:
    k, n = freq[t]
    p = k / n
    if p <= 0.05:
        P0[t], KIND[t] = 0.02, "dead"
    elif p >= 0.90:
        P0[t], KIND[t] = min(p, 0.97), "stable"
    else:
        P0[t], KIND[t] = p, "flaky"
CLUSTER = {t: LVL.get(t, "?") for t in TASKS}
CLUSTERS = sorted(set(CLUSTER.values()))

N_ROUNDS = 16
K_SLOTS = 4
TARGETS_PER_CAND = 3
P_DUD = 0.30
P_FIX_FLAKY = 0.60
FIX_GAIN = 0.35
P_COLLATERAL = 0.25
COLLATERAL = 0.30
P_PLANNER_BLANK = 0.06
MAX_VARIANTS = 8
Z = 1.28
DELTA = 0.05
PIN_ROUNDS = 3


def wilson_lb(k, n, z=Z):
    if n == 0:
        return 0.0
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    w = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (c - w) / d


def gen_candidates(rng, pool, n_slots, p_vec, p_fix_dead):
    """Shared generator. pool = list of target tasks the policy exposes."""
    cands = []
    for _ in range(n_slots):
        if not pool or rng.random() < P_PLANNER_BLANK:
            cands.append(None)          # planner blank for this slot
            continue
        targets = rng.sample(pool, min(TARGETS_PER_CAND, len(pool)))
        eff = {}
        if rng.random() >= P_DUD:
            for t in targets:
                kind = KIND[t]
                if kind == "flaky" and rng.random() < P_FIX_FLAKY:
                    eff[t] = min(0.95, p_vec[t] + FIX_GAIN)
                elif kind == "dead" and rng.random() < p_fix_dead:
                    eff[t] = 0.55
        if rng.random() < P_COLLATERAL:
            vict = rng.choice(TASKS)
            eff[vict] = max(0.02, p_vec.get(vict, P0[vict]) - COLLATERAL)
        cands.append({"targets": targets, "eff": eff})
    return cands


def measure(rng, p_vec, tasks):
    return {t: 1 if rng.random() < p_vec[t] else 0 for t in tasks}


def run_policy(policy, seed, p_fix_dead=0.05):
    rng = random.Random(seed * 7919 + hash(policy) % 1000)
    variants = {"V0": dict(P0)}                      # vid -> true p vector
    routed = {"V0": set(TASKS)}
    ledger = collections.defaultdict(lambda: collections.defaultdict(lambda: [0, 0]))
    hist = collections.defaultdict(dict)             # (v) -> t -> {r: 0/1}
    epoch_start = {"V0": 0}
    prev_prot = collections.defaultdict(set)         # bridge rule: last epoch's PROTECTED
    ever_any = set()
    pins = {}                                        # t -> (holder, expires_round)
    probation = {}                                   # vid -> dict(pre_p, protected, base_rate)
    m = dict(ships=0, forks=0, rejects=0, deadlock=0, starved=0,
             wasted=0, targeted=0, good_rejected=0, blank=0)

    for r in range(N_ROUNDS):
        # ---- routing freeze -------------------------------------------------
        if r > 0:
            new_routed = {v: set() for v in variants}
            for c in CLUSTERS:
                ctasks = [t for t in TASKS if CLUSTER[t] == c]
                holder = max(sorted(variants),
                             key=lambda v: sum(1 for t in ctasks if t in routed.get(v, ())))
                if policy == "NEW":
                    best, best_lb = holder, None
                    inc_k = inc_n = 0
                    for t in ctasks:
                        h = hist[holder].get(t, {})
                        for rr, o in h.items():
                            if rr >= epoch_start.get(holder, 0):
                                inc_k += o
                                inc_n += 1
                    inc_hat = inc_k / inc_n if inc_n else 0.5
                    for v in sorted(variants):
                        if v == holder:
                            continue
                        k = n = 0
                        for t in ctasks:
                            h = hist[v].get(t, {})
                            for rr, o in h.items():
                                if rr >= epoch_start.get(v, 0):
                                    k += o
                                    n += 1
                        lb = wilson_lb(k, n)
                        if lb > inc_hat + DELTA and (best_lb is None or lb > best_lb):
                            best, best_lb = v, lb
                    winner = best
                else:
                    best, best_s = None, -1
                    for v in sorted(variants):
                        k = n = 0
                        for t in ctasks:
                            kk, nn = ledger[v].get(t, (0, 0))
                            k += kk
                            n += nn
                        s_ = (k + 1) / (n + 2)
                        if s_ > best_s:
                            best, best_s = v, s_
                    winner = best
                for t in ctasks:
                    new_routed[winner].add(t)
            if policy == "NEW":
                for t, (holder, exp) in list(pins.items()):
                    if r < exp and holder in variants:
                        for v in new_routed:
                            new_routed[v].discard(t)
                        new_routed[holder].add(t)
                    else:
                        del pins[t]
            routed = new_routed

        # ---- evaluate active pool ------------------------------------------
        meas = {}
        for v, ts in routed.items():
            meas[v] = measure(rng, variants[v], ts)
            for t, o in meas[v].items():
                le = ledger[v][t]
                le[0] += o
                le[1] += 1
                hist[v].setdefault(t, {})[r] = o
                if o:
                    ever_any.add(t)

        # ---- probation check (NEW) -----------------------------------------
        if policy == "NEW":
            for v in list(probation):
                pr = probation[v]
                prot_meas = [meas[v][t] for t in pr["protected"] if t in meas[v]]
                rate = sum(prot_meas) / len(prot_meas) if prot_meas else 1.0
                if rate < pr["base_rate"] - 0.15:
                    variants[v] = pr["pre_p"]
                    m["rejects"] += 1
                else:
                    m["ships"] += 1
                    prev_prot[v] = set(pr["protected"])
                    epoch_start[v] = pr["ship_round"]   # probation round counts
                del probation[v]

        # ---- matrix layer / pools ------------------------------------------
        def tiers(v):
            out = {}
            e0 = epoch_start.get(v, 0)
            bridge = prev_prot[v] if (r - e0) < 2 else set()
            for t in routed.get(v, ()):
                h = hist[v].get(t, {})
                cur = [o for rr, o in h.items() if rr >= e0]
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

        # ---- slot allocation ------------------------------------------------
        if policy == "BASE":
            def rollup(v):
                cells = ledger[v]
                vals = [k / n for k, n in cells.values() if n]
                return sum(vals) / len(vals) if vals else 0.5
            tgt = min(sorted(variants), key=rollup)
            alloc = {tgt: K_SLOTS}
        elif policy == "FALL":
            tot = sum(len(routed[v]) for v in variants) or 1
            alloc = {v: round(K_SLOTS * len(routed[v]) / tot) for v in sorted(variants)}
        else:
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
            # cooldown: an epoch needs >=2 fresh rounds before its variant may
            # be evolved again (intensification: incumbents accrue evidence)
            for v in variants:
                if r - epoch_start.get(v, 0) + 1 < 2:
                    alloc[v] = 0

        for v in variants:
            if len(routed.get(v, ())) >= 20 and alloc.get(v, 0) == 0 and v not in probation:
                m["starved"] += 1

        # ---- per-variant pipeline ------------------------------------------
        any_pool = False
        for v in sorted(variants):
            n_slots = alloc.get(v, 0)
            if n_slots <= 0:
                continue
            tk = sorted(routed[v])
            if policy == "NEW":
                tv_ = tiers(v)
                pool = [t for t in tk if tv_[t] in "AB"]
                prot = {t for t in tk if tv_[t] == "PROT"}
            else:
                never = {t for t in tk if ledger[v].get(t, (0, 0))[0] == 0}
                pool = sorted(never) or [t for t in tk if meas[v].get(t) == 0]
                prot = {t for t in tk if ledger[v].get(t, (0, 0))[0] > 0}
            if pool:
                any_pool = True
            cands = gen_candidates(rng, pool, n_slots, variants[v], p_fix_dead)
            if policy == "NEW":
                cands = [c if c is not None else
                         (gen_candidates(rng, pool, 1, variants[v], p_fix_dead)[0])
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
                m["gate_meas"] = m.get("gate_meas", 0) + len(tk)
                if policy == "NEW":
                    improved = {t for t in tk if gm[t] and t not in prot}
                    regressed = {t for t in tk if not gm[t] and t in prot}
                else:
                    never = {t for t in tk if ledger[v].get(t, (0, 0))[0] == 0}
                    improved = {t for t in never if gm[t]}
                    regressed = {t for t in tk if not gm[t] and t in prot}
                true_gain = sum(cand_p.get(t, P0[t]) - variants[v].get(t, P0[t]) for t in TASKS)
                if not improved:
                    m["rejects"] += 1
                    if true_gain > 0.3:
                        m["good_rejected"] += 1
                    continue
                if policy == "NEW" and regressed:
                    # probation makes APPLY reversible, so fork only on conflict
                    # clearly above single-measurement noise on the PROT set
                    noise_bar = 2 + 0.05 * len(prot)
                    if len(regressed) <= noise_bar:
                        regressed = set()
                if not regressed:
                    if policy == "NEW":
                        probation[v] = dict(pre_p=dict(variants[v]), protected=set(prot),
                                            ship_round=r,
                                            base_rate=(sum(meas[v][t] for t in prot if t in meas[v])
                                                       / max(1, len([t for t in prot if t in meas[v]]))))
                        variants[v] = cand_p
                    else:
                        variants[v] = cand_p
                        m["ships"] += 1
                    shipped = True
                elif len(variants) < MAX_VARIANTS:
                    child = f"V{len(variants)}"
                    variants[child] = cand_p
                    routed[child] = set(improved)
                    routed[v] -= improved
                    epoch_start[child] = r      # its seed measurement counts
                    if policy == "NEW":
                        prev_prot[child] = set(prot)
                        for t, o in gm.items():
                            hist[child].setdefault(t, {})[r] = o
                            le = ledger[child][t]
                            le[0] += o
                            le[1] += 1
                        for t in regressed:
                            pins[t] = (v, r + 1 + PIN_ROUNDS)
                    else:
                        for t in improved:
                            le = ledger[child][t]
                            le[0] += 1
                            le[1] += 1
                    m["ships"] += 1
                    m["forks"] += 1
                    shipped = True
                else:
                    m["rejects"] += 1
        if not any_pool:
            m["deadlock"] += 1
        m.setdefault("traj", []).append(
            sum(variants[v][t] for v, ts in routed.items() for t in ts) / len(TASKS))

    deployed = sum(variants[v][t] for v, ts in routed.items() for t in ts) / len(TASKS)
    m["deployed"] = deployed
    m["variants"] = len(variants)
    return m


def bench(policies, seeds, p_fix_dead=0.05, label=""):
    print(f"\n=== {label or ('p_fix_dead=' + str(p_fix_dead))}   seeds={seeds} ===")
    base_dep = None
    hdr = (f"{'policy':<6}{'部署真值':>10}{'±':>7}{'ship':>7}{'fork':>6}{'变体':>6}"
           f"{'饿轮':>6}{'旱轮':>6}{'空白':>6}{'好编辑被拒':>11}{'死题瞄准%':>10}")
    print(hdr)
    for pol in policies:
        rs = [run_policy(pol, s, p_fix_dead) for s in range(seeds)]
        dep = [x["deployed"] for x in rs]
        mu, sd = statistics.mean(dep), statistics.pstdev(dep)
        if base_dep is None:
            base_dep = mu
        avg = lambda k: statistics.mean(x[k] for x in rs)
        waste = 100 * sum(x["wasted"] for x in rs) / max(1, sum(x["targeted"] for x in rs))
        print(f"{pol:<6}{mu:>10.4f}{sd:>7.3f}{avg('ships'):>7.2f}{avg('forks'):>6.2f}"
              f"{avg('variants'):>6.2f}{avg('starved'):>6.1f}{avg('deadlock'):>6.1f}"
              f"{avg('blank'):>6.1f}{avg('good_rejected'):>11.2f}{waste:>10.1f}")
        traj = [statistics.mean(x["traj"][i] for x in rs) for i in range(N_ROUNDS)]
        print("      轨迹 " + " ".join(f"{x:.3f}" for x in traj))
    print(f"  起点(全床初始真值均值) = {sum(P0.values())/len(TASKS):.4f}")


if __name__ == "__main__":
    print(f"床校准: {len(TASKS)} 题  dead {sum(1 for k in KIND.values() if k=='dead')} / "
          f"flaky {sum(1 for k in KIND.values() if k=='flaky')} / "
          f"stable {sum(1 for k in KIND.values() if k=='stable')}")

    bench(["BASE", "FALL", "NEW"], seeds=300, p_fix_dead=0.05, label="主设定 p_fix_dead=0.05")
    for pfd in (0.30, 0.60):
        bench(["BASE", "NEW"], seeds=300, p_fix_dead=pfd)
