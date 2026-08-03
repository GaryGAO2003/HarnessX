# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""Analysis H -- pool headroom: is there UNUSED score in the evolved variant pool?

Question: on tasks where >=2 *distinct* variants were measured, how much higher
would the round's pass@2 be if each task went to its best measured variant
(routing headroom) or to the union of all measured variants (portfolio headroom)
-- **after subtracting the headroom you would measure under pure noise**.

Why a noise control is mandatory: this bed flips ~23% of tasks under
byte-identical re-measurement (SD 4.59pp). "max over m variants" is therefore
inflated by selection *even if every variant were identical*. The decisive
numbers here are ``excess_R`` / ``excess_U`` = observed headroom minus the
headroom the same "max-over-m" operation produces under H0 "all m variants share
this task's pooled pass rate".

Data plumbing (verified, see _common.py docstring + 07-FORK-REUSE-CORRECTION.md):
  * ``active_pool_measurements[v][t] = [succ, att]`` -- per-round settled snapshot,
    routing-partitioned so each task has exactly ONE active carrier per round.
  * ``candidate_gate_measurements[v][t] = [succ, att]`` -- the gate rollout of that
    round's ``paper_target_variant`` on its evaluated cluster. This is the ONLY
    source of a *second* distinct variant on a task within a round; without it the
    active partition gives zero overlap.
  * ``att == 2`` throughout (pass@2 = succ>=1).

CRITICAL BIAS CONTROL. Cells whose ``active_score_source[v] == candidate_reuse``
are circularly scored (100% pass by construction: the fork's task set T_k is
*defined* as the tasks the newborn passed). They are excluded from EVERY headroom
computation. Because the reuse cells are literal copies of the gate rollout in
some runs (e_pervar3 R8, a1big5 R2 -- verified byte-identical), the exclusion is
keyed on ``(round, variant)`` and applied to BOTH sources, so a dropped reuse
cell cannot sneak back in through ``candidate_gate_measurements``.

Read-only. stdlib only. Zero API cost.
Usage:  python experiments/analysis/novelty/h_pool_headroom.py [run]
"""

from __future__ import annotations

import random
import sys
from collections import Counter, defaultdict

import _common as C

SEED = 0
N_PERM = 2000  # >= 1000 required by the spec


# --------------------------------------------------------------------------- #
# merged usable (round, variant, task) table
# --------------------------------------------------------------------------- #
def build_table(states):
    """Merge active_pool + candidate_gate into one usable table.

    Returns
      table[r][t] = {variant: (source, succ, att)}   -- one canonical pass@2 cell
                    per variant per (round, task); active preferred, gate used only
                    when the variant has no active cell that round.
      prov         -- provenance counters (dict).
    """
    table: dict[int, dict[str, dict[str, tuple]]] = defaultdict(lambda: defaultdict(dict))
    prov = {
        "active_cells_used": 0,
        "gate_cells_used": 0,
        "excluded_reuse_active": 0,
        "excluded_reuse_gate": 0,
        "dropped_same_variant_gate_dup": 0,
    }
    for r, st in states.items():
        ass = st.get("active_score_source", {}) or {}
        reuse_vars = {v for v, s in ass.items() if s == "candidate_reuse"}

        # active source (the settled carrier score) -- reuse variants dropped whole
        for v, tasks in (st.get("active_pool_measurements", {}) or {}).items():
            if v in reuse_vars:
                prov["excluded_reuse_active"] += len(tasks)
                continue
            for t, sa in tasks.items():
                table[r][t][v] = ("active", int(sa[0]), int(sa[1]))
                prov["active_cells_used"] += 1

        # gate source -- second distinct variant; same (round,variant) reuse-flag
        for v, tasks in (st.get("candidate_gate_measurements", {}) or {}).items():
            if v in reuse_vars:  # e.g. e_pervar3 R8 / a1big5 R2: gate == reuse copy
                prov["excluded_reuse_gate"] += len(tasks)
                continue
            for t, sa in tasks.items():
                if v in table[r][t]:  # carrier == gate variant: keep the active cell
                    prov["dropped_same_variant_gate_dup"] += 1
                    continue
                table[r][t][v] = ("gate", int(sa[0]), int(sa[1]))
                prov["gate_cells_used"] += 1
    return table, prov


def bed_size(state) -> int:
    """Full task-bed for the round (for scaling to the headline curve)."""
    d = state.get("evaluated_task_denominator")
    if isinstance(d, int) and d > 0:
        return d
    tasks = set()
    for _v, ts in (state.get("routing", {}) or {}).items():
        tasks.update(ts)
    if tasks:
        return len(tasks)
    for _v, cells in (state.get("active_pool_measurements", {}) or {}).items():
        tasks.update(cells)
    return len(tasks)


# --------------------------------------------------------------------------- #
# per-unit headroom + parametric noise null
# --------------------------------------------------------------------------- #
def unit_stats(variants: dict, carrier: str):
    """One multi-measured (round, task) unit.

    variants: {variant: (source, succ, att)} with len >= 2.
    Returns (realized, oracle, e_max, q_carrier) all in [0,1].
      realized  = carrier passed?  (the score the run actually banked)
      oracle    = any measured variant passed?  (best-variant routing == union;
                  identical here because per-variant outcomes are already binary
                  pass@2 -- see module notes)
      e_max     = E[max over m variants] under H0 all share pooled p_hat
                = 1 - (1-p_hat)^(sum att)      (== pass@(sum att) at pooled rate)
      q_carrier = E[carrier passed] under the same H0 (for the alt null)
    """
    succ = sum(c[1] for c in variants.values())
    att = sum(c[2] for c in variants.values())
    p_hat = succ / att if att else 0.0
    realized = 1 if (carrier in variants and variants[carrier][1] >= 1) else 0
    oracle = 1 if any(c[1] >= 1 for c in variants.values()) else 0
    e_max = 1.0 - (1.0 - p_hat) ** att
    att_c = variants[carrier][2] if carrier in variants else 2
    q_carrier = 1.0 - (1.0 - p_hat) ** att_c
    return realized, oracle, e_max, q_carrier


def aggregate(units):
    """units: list of (realized, oracle, e_max, q_carrier). Return rate dict."""
    n = len(units)
    if n == 0:
        return None
    realized = sum(u[0] for u in units) / n
    oracle = sum(u[1] for u in units) / n
    e_max = sum(u[2] for u in units) / n
    q_car = sum(u[3] for u in units) / n
    return {
        "n": n,
        "realized": realized,
        "oracle": oracle,
        "e_max": e_max,
        "q_carrier": q_car,
        "headroom": oracle - realized,             # observed routing/union headroom
        "headroom_null": e_max - realized,          # same op under pure noise
        "excess": oracle - e_max,                   # <-- the whole point
        "excess_alt": (oracle - realized) - (e_max - q_car),  # noise realized = q_carrier
    }


# --------------------------------------------------------------------------- #
# permutation null (assumption-free): shuffle variant labels across tasks
# --------------------------------------------------------------------------- #
def permutation_null(unit_cells, n_perm=N_PERM, seed=SEED):
    """unit_cells: list of (list_of_pass_bools, carrier_index) for each multi unit.

    Global label shuffle: pool every cell's pass-bool across the whole subset,
    shuffle, deal back preserving each unit's cell count, recompute
    headroom = mean(oracle=OR) - mean(realized=carrier slot). Repeat n_perm times.
    Preserves total cells, per-unit m, and overall pass rate; destroys any real
    variant<->task complementarity. FIXED seed.
    """
    flat = []
    layout = []  # (start, m, carrier_index)
    for bools, cidx in unit_cells:
        layout.append((len(flat), len(bools), cidx))
        flat.extend(bools)
    if not flat or not layout:
        return None
    rng = random.Random(seed)
    pool = list(flat)
    n_units = len(layout)
    heads = []
    for _ in range(n_perm):
        rng.shuffle(pool)
        oracle_sum = 0
        realized_sum = 0
        for start, m, cidx in layout:
            seg = pool[start:start + m]
            if any(seg):
                oracle_sum += 1
            if seg[cidx]:
                realized_sum += 1
        heads.append((oracle_sum - realized_sum) / n_units)
    heads.sort()
    lo = heads[int(0.025 * n_perm)]
    hi = heads[int(0.975 * n_perm) - 1]
    mean = sum(heads) / len(heads)
    return {"mean": mean, "lo": lo, "hi": hi, "n_units": n_units}


# --------------------------------------------------------------------------- #
def pp(x):
    return f"{x * 100:+6.2f}pp"


def main(tag: str = "s1k8b103") -> None:
    try:
        states = C.all_states(tag)
    except FileNotFoundError as exc:
        print(f"[{tag}] cannot load: {exc}")
        return
    if not states:
        print(f"[{tag}] no rounds with pool_state.json -- skip")
        return

    table, prov = build_table(states)
    beds = {r: bed_size(st) for r, st in states.items()}
    bed = max(beds.values()) if beds else 0

    print("=" * 78)
    print(f"H. POOL HEADROOM  --  run={tag}   rounds={sorted(states)}   bed={bed}")
    print("=" * 78)

    # ---- provenance / bias-control accounting -------------------------------
    print("\n[bias control] merged usable (round,variant,task) table")
    print(f"  active cells used              : {prov['active_cells_used']}")
    print(f"  gate cells used (2nd variant)  : {prov['gate_cells_used']}")
    print(f"  EXCLUDED candidate_reuse (act) : {prov['excluded_reuse_active']}  "
          f"<- circular, dropped from headroom")
    print(f"  EXCLUDED candidate_reuse (gate): {prov['excluded_reuse_gate']}  "
          f"<- gate copies of the same reuse cells")
    print(f"  dropped same-variant gate dup  : {prov['dropped_same_variant_gate_dup']}  "
          f"(carrier re-measured by its own gate; active kept)")

    # ---- (1) coverage census ------------------------------------------------
    print("\n(1) COVERAGE CENSUS  -- tasks with >=2 DISTINCT usable variants")
    print(f"    {'R':>3} {'bed':>4} {'multi>=2':>9} {'m=2':>5} {'m>=3':>5}  "
          f"{'gate_var':>9} {'carriers':>20}")
    total_multi = 0
    multi_tasks_overall = set()
    per_round_units = {}
    per_round_cells = {}
    for r in sorted(table):
        st = states[r]
        gate_vars = list((st.get("candidate_gate_measurements", {}) or {}).keys())
        carriers = sorted((st.get("routing", {}) or {}).keys())
        units = []
        cells = []
        m2 = m3 = 0
        task_carrier = {t: v for v, ts in (st.get("routing", {}) or {}).items() for t in ts}
        for t, vmap in table[r].items():
            if len(vmap) >= 2:
                m2 += (len(vmap) == 2)
                m3 += (len(vmap) >= 3)
                carrier = task_carrier.get(t)
                units.append(unit_stats(vmap, carrier))
                bools = [c[1] >= 1 for c in vmap.values()]
                # carrier slot index within the vmap ordering
                keys = list(vmap.keys())
                cidx = keys.index(carrier) if carrier in keys else 0
                cells.append((bools, cidx))
                multi_tasks_overall.add(t)
        per_round_units[r] = units
        per_round_cells[r] = cells
        total_multi += len(units)
        if len(units) or gate_vars:
            print(f"    {r:>3} {beds[r]:>4} {len(units):>9} {m2:>5} {m3:>5}  "
                  f"{','.join(gate_vars) or '-':>9} {','.join(carriers):>20}")
    print(f"\n    overall multi-measured (round,task) units = {total_multi}")
    print(f"    distinct tasks ever multi-measured         = {len(multi_tasks_overall)}"
          f"  ({len(multi_tasks_overall) / bed * 100:.1f}% of the {bed}-task bed)" if bed else "")
    # ---- (2)/(3)/(4) headroom + nulls, PER ROUND ----------------------------
    if total_multi == 0:
        print("\n(2)/(3)/(4) PER-ROUND headroom is UNDEFINED for this run.")
        print("    >>> ZERO within-round overlap: routing partitions the bed, and the")
        print("    >>> gate only ever re-measures the SAME parent that already carries")
        print("    >>> its cluster (minus the freshly-forked newborn slice, which is the")
        print("    >>> excluded reuse). No task is measured by 2 DISTINCT usable variants")
        print("    >>> in a single round, so per-task best-variant routing cannot exist")
        print("    >>> at the round level. This is a real (structural) negative.")
    else:
        print("\n(2)/(3)/(4) PER-ROUND HEADROOM vs NOISE NULL  (routing R == union U; see note)")
        print("    on the multi-measured subset; scaled = lift / full bed")
        print(f"    {'R':>3} {'n':>4} {'realiz':>7} {'oracle':>7} {'E[max]':>7} "
              f"{'head_R':>8} {'null_R':>8} {'excess':>8} {'sc_head':>8} {'sc_exc':>8}")
        pooled_units = []
        for r in sorted(per_round_units):
            units = per_round_units[r]
            if not units:
                continue
            pooled_units.extend(units)
            agg = aggregate(units)
            scale = agg["n"] / beds[r] if beds[r] else 0.0
            print(f"    {r:>3} {agg['n']:>4} {agg['realized']:>7.3f} {agg['oracle']:>7.3f} "
                  f"{agg['e_max']:>7.3f} {pp(agg['headroom'])} {pp(agg['headroom_null'])} "
                  f"{pp(agg['excess'])} {pp(agg['headroom'] * scale)} {pp(agg['excess'] * scale)}")
        agg = aggregate(pooled_units)
        print(f"\n    OVERALL pooled: n={agg['n']}  realized={agg['realized']:.4f}  "
              f"oracle={agg['oracle']:.4f}  E[max]={agg['e_max']:.4f}")
        print(f"    headroom_R=headroom_U {pp(agg['headroom'])}   null {pp(agg['headroom_null'])}"
              f"   >>> excess {pp(agg['excess'])}  n={agg['n']}")
        all_cells = [c for r in per_round_cells for c in per_round_cells[r]]
        perm = permutation_null(all_cells)
        if perm:
            inside = perm["lo"] <= agg["headroom"] <= perm["hi"]
            print(f"    perm-null (label shuffle, {N_PERM}x, seed={SEED}): mean {pp(perm['mean'])}"
                  f" 95% [{pp(perm['lo'])},{pp(perm['hi'])}] -> "
                  f"{'INSIDE band (no real headroom)' if inside else 'OUTSIDE band'}")

    # note on R vs U identity
    print("\n    NOTE: per-variant outcomes are already binary pass@2 (succ>=1), so")
    print("    'max over measured variants' (routing R) and 'any variant solved it'")
    print("    (union U) are the SAME quantity here; excess_R == excess_U by construction.")

    # ---- CROSS-ROUND portfolio (the informative view) -----------------------
    _cross_round(table, states, bed)

    # ---- (5) pairwise complementarity ---------------------------------------
    _pairwise(table, states, bed)

    # ---- (6) cluster granularity --------------------------------------------
    _cluster_granularity(states)


def _perm_union(bit_lists, n_perm=N_PERM, seed=SEED):
    """Global bit-shuffle null for the union rate. Preserves total passes and the
    per-task variant count; destroys task<->variant association."""
    flat = [b for bl in bit_lists for b in bl]
    layout = []
    idx = 0
    for bl in bit_lists:
        layout.append((idx, len(bl)))
        idx += len(bl)
    if not flat:
        return None
    rng = random.Random(seed)
    pool = [1 if b else 0 for b in flat]
    n = len(layout)
    means = []
    for _ in range(n_perm):
        rng.shuffle(pool)
        s = sum(1 for start, m in layout if any(pool[start:start + m]))
        means.append(s / n)
    means.sort()
    return {"mean": sum(means) / len(means),
            "lo": means[int(0.025 * n_perm)],
            "hi": means[int(0.975 * n_perm) - 1]}


def _mean_ci(vals):
    """Mean and a normal 95% CI on the mean (returns mean, se, lo, hi)."""
    n = len(vals)
    if n == 0:
        return 0.0, 0.0, 0.0, 0.0
    m = sum(vals) / n
    if n < 2:
        return m, 0.0, m, m
    var = sum((x - m) ** 2 for x in vals) / (n - 1)
    se = (var / n) ** 0.5
    return m, se, m - 1.96 * se, m + 1.96 * se


def _mc_rate_null(att_p_list, n_sim=N_PERM, seed=SEED):
    """Monte-Carlo winner's-curse null for RATE-based best-variant routing.

    att_p_list: list of (att_v_list, p_hat) per multi-measured task. Under H0 every
    variant on the task shares the task's pooled per-attempt rate p_hat; each
    variant's pass@2 is *estimated* from att_v noisy Bernoulli draws, then we take
    the max over variants (selecting on a noisy estimate = winner's curse). Returns
    (overall_mean, per_task_null_list) -- E[max pass@2] under pure noise.
    """
    rng = random.Random(seed)
    per_task = []
    for att_v_list, p_hat in att_p_list:
        s = 0.0
        for _ in range(n_sim):
            best = 0.0
            for att_v in att_v_list:
                succ = sum(1 for _ in range(att_v) if rng.random() < p_hat)
                r = succ / att_v if att_v else 0.0
                pass2 = 1.0 - (1.0 - r) ** 2
                if pass2 > best:
                    best = pass2
            s += best
        per_task.append(s / n_sim)
    mean = sum(per_task) / len(per_task) if per_task else None
    return mean, per_task


def _cross_round(table, states, bed):
    print("\n" + "-" * 78)
    print("CROSS-ROUND PORTFOLIO (pool-lifetime) -- the INFORMATIVE view of headroom")
    print("-" * 78)
    print("Per-round overlap is structurally 0, so pool each task's measurements over")
    print("the whole run by DISTINCT variant (a variant 'measured' a task if it made a")
    print("usable non-reuse cell as carrier OR gate in any round). This is where any")
    print("'route each task to its best variant' score would come from.")
    print("realized = FINAL-round carrier outcome (comparable to the headline final).")

    last = max(states)
    routing_last = states[last].get("routing", {}) or {}
    carrier_last = {t: v for v, ts in routing_last.items() for t in ts}

    xr = defaultdict(lambda: defaultdict(lambda: [0, 0]))
    for r in table:
        for t, vmap in table[r].items():
            for v, (_s, succ, att) in vmap.items():
                cell = xr[t][v]
                cell[0] += succ
                cell[1] += att

    def realized_final(t):
        vm = table.get(last, {}).get(t, {})
        cv = carrier_last.get(t)
        if cv in vm:
            return 1 if vm[cv][1] >= 1 else 0
        for _v, (src, succ, _att) in vm.items():
            if src == "active":
                return 1 if succ >= 1 else 0
        return None

    variety = Counter()
    rf_l, oracle_l, emax_l = [], [], []
    orate_l, rrate_l = [], []
    union_bits, att_p = [], []
    for t, vmap in xr.items():
        variety[len(vmap)] += 1
        if len(vmap) < 2:
            continue
        rf = realized_final(t)
        if rf is None:
            continue
        succ_tot = sum(c[0] for c in vmap.values())
        att_tot = sum(c[1] for c in vmap.values())
        p_hat = succ_tot / att_tot if att_tot else 0.0
        rf_l.append(rf)
        oracle_l.append(1 if any(c[0] >= 1 for c in vmap.values()) else 0)
        emax_l.append(1.0 - (1.0 - p_hat) ** att_tot)
        orate_l.append(max(1.0 - (1.0 - (c[0] / c[1] if c[1] else 0.0)) ** 2
                           for c in vmap.values()))
        cc = vmap.get(carrier_last.get(t))
        rrate_l.append((1.0 - (1.0 - cc[0] / cc[1]) ** 2) if cc and cc[1] else float(rf))
        union_bits.append([c[0] >= 1 for c in vmap.values()])
        att_p.append(([c[1] for c in vmap.values()], p_hat))

    n = len(rf_l)
    print(f"\ndistinct-variants-per-task histogram : {dict(sorted(variety.items()))}")
    print(f"subset (>=2 distinct variants)       : n={n} / {bed} bed "
          f"({n / bed * 100:.1f}%)" if bed else f"n={n}")
    if n == 0:
        print("no cross-round overlap either -- nothing to route.")
        return

    realized = sum(rf_l) / n
    oracle = sum(oracle_l) / n
    e_max = sum(emax_l) / n
    print("\nBINARY (spec: oracle = 'any distinct variant ever solved it' == union):")
    print(f"  realized (final carrier) pass  : {realized:.4f}   n={n}")
    print(f"  oracle (best ever == union)    : {oracle:.4f}   n={n}")
    print(f"  E[max] H0 all-identical (noise): {e_max:.4f}   n={n}")
    print(f"  headroom_R = headroom_U        : {pp(oracle - realized)}")
    print(f"  headroom_null_R = _U           : {pp(e_max - realized)}")
    bin_exc = [oracle_l[i] - emax_l[i] for i in range(n)]
    _m, _se, blo, bhi = _mean_ci(bin_exc)
    print(f"  >>> excess_R = excess_U        : {pp(oracle - e_max)}   n={n}   "
          f"95% CI [{pp(blo)}, {pp(bhi)}]")
    print(f"  bed-scaled headroom (/{bed})    : {pp((oracle - realized) * n / bed)}")
    print(f"  bed-scaled EXCESS   (/{bed})    : {pp((oracle - e_max) * n / bed)}")
    print(f"      excess CI includes 0?      : {'YES -> not distinguishable from noise' if blo <= 0 <= bhi else 'no (but see plug-in bias note)'}")
    print("  NOTE: this binary excess is UPWARD biased -- E[max] uses a plug-in p_hat")
    print("  and (1-x)^n is convex, so the null under-estimates the true union (Jensen).")
    print("  The MC rate null and the permutation null below do NOT share this bias;")
    print("  trust them over a marginally-positive binary CI.")
    if e_max > 0.98:
        print("  (!) binary view is SATURATED: with many pooled attempts both oracle and")
        print("      its noise null sit at ~1.0, so the binary excess is near-blind. Use")
        print("      the rate-based view below, which is not attempt-count saturated.")

    o_rate = sum(orate_l) / n
    r_rate = sum(rrate_l) / n
    rate_null, rate_null_pt = _mc_rate_null(att_p)
    rate_exc = [orate_l[i] - rate_null_pt[i] for i in range(n)]
    _m, _se, rlo, rhi = _mean_ci(rate_exc)
    print("\nRATE-BASED (route to the variant with the best pooled pass@2; MC null):")
    print(f"  realized_rate (final carrier)  : {r_rate:.4f}   n={n}")
    print(f"  oracle_rate (best variant)     : {o_rate:.4f}   n={n}")
    print(f"  rate null E[max] H0 (winner's  : {rate_null:.4f}   n={n}")
    print(f"           curse, {N_PERM} MC, seed={SEED})")
    print(f"  rate headroom (oracle-realized): {pp(o_rate - r_rate)}")
    print(f"  rate headroom_null (null-realiz: {pp(rate_null - r_rate)}")
    print(f"  >>> rate EXCESS (oracle-null)  : {pp(o_rate - rate_null)}   n={n}   "
          f"95% CI [{pp(rlo)}, {pp(rhi)}]")
    print(f"  bed-scaled rate headroom (/{bed}): {pp((o_rate - r_rate) * n / bed)}")
    print(f"  bed-scaled rate EXCESS   (/{bed}): {pp((o_rate - rate_null) * n / bed)}")
    print(f"      excess CI includes 0?      : {'YES -> not distinguishable from noise' if rlo <= 0 <= rhi else 'no'}")

    perm = _perm_union(union_bits)
    if perm:
        print(f"\nPERMUTATION NULL on union (global bit shuffle, {N_PERM}x, seed={SEED}):")
        print(f"  observed union {oracle:.4f}  null mean {perm['mean']:.4f}  "
              f"95% [{perm['lo']:.4f},{perm['hi']:.4f}]")
        if oracle > perm["hi"]:
            v = "ABOVE band -> real positive complementarity (a portfolio would add score)"
        elif oracle < perm["lo"]:
            v = ("BELOW band -> variants are REDUNDANT/correlated (a task's variants "
                 "mostly agree; passes concentrate, so pooling adds ~nothing)")
        else:
            v = "INSIDE band -> union == base rate (no task-specific structure)"
        print(f"  -> observed is {v}")


def _pairwise(table, states, bed):
    print("\n(5) PAIRWISE COMPLEMENTARITY  -- ever-solved pooled across rounds")
    print("    (a variant 'solves' a task if it passes it in ANY round it measured it)")
    # variant -> {task -> ever_pass};  variant -> set(measured tasks); pooled (succ,att)
    ever = defaultdict(dict)
    measured = defaultdict(set)
    poolsa = defaultdict(lambda: defaultdict(lambda: [0, 0]))
    for r in table:
        for t, vmap in table[r].items():
            for v, (_src, succ, att) in vmap.items():
                measured[v].add(t)
                ever[v][t] = ever[v].get(t, False) or (succ >= 1)
                cell = poolsa[v][t]
                cell[0] += succ
                cell[1] += att
    variants = sorted(measured)
    rows = []
    for i in range(len(variants)):
        for j in range(i + 1, len(variants)):
            a, b = variants[i], variants[j]
            both = measured[a] & measured[b]
            if not both:
                continue
            nb = no = na = nn = 0
            exp_sym = 0.0  # E[symdiff] if A,B were IDENTICAL iid pass@2 at pooled rate
            for t in both:
                pa, pb = ever[a][t], ever[b][t]
                if pa and pb:
                    nb += 1
                elif pa:
                    na += 1
                elif pb:
                    no += 1
                else:
                    nn += 1
                sa, sb = poolsa[a][t], poolsa[b][t]
                tot_att = sa[1] + sb[1]
                p = (sa[0] + sb[0]) / tot_att if tot_att else 0.0
                q = 1.0 - (1.0 - p) ** 2  # pass@2 at pooled rate
                exp_sym += 2 * q * (1 - q)
            rows.append((na + no, a, b, len(both), nb, na, no, nn, exp_sym))
    if not rows:
        print("    no variant pair co-measures any task -- no portfolio structure.")
        return
    rows.sort(reverse=True)
    print(f"    {'pair':>9} {'n_both':>7} {'both_solve':>10} {'only_A':>7} "
          f"{'only_B':>7} {'neither':>7} {'symdiff':>7} {'symdiff_H0':>10}")
    for symd, a, b, n, nb, na, no, nn, exp_sym in rows[:5]:
        print(f"    {a+'/'+b:>9} {n:>7} {nb:>10} {na:>7} {no:>7} {nn:>7} {symd:>7} "
              f"{exp_sym:>10.1f}")
    print("    symdiff_H0 = E[symdiff] if the pair were IDENTICAL variants under the")
    print("    bed's own re-measurement noise. observed symdiff ~= symdiff_H0 means the")
    print("    'complementarity' is just noise, NOT real portfolio structure.")


def _cluster_granularity(states):
    print("\n(6) CLUSTER GRANULARITY  -- distinct task-sets moved as a block (routing)")
    print(f"    {'R':>3} {'n_routed':>8} {'n_blocks':>8} {'largest':>7}  block_sizes")
    block_sig = defaultdict(int)  # frozenset -> count of rounds it appears
    for r in sorted(states):
        routing = states[r].get("routing", {}) or {}
        blocks = [frozenset(ts) for ts in routing.values() if ts]
        if not blocks:
            print(f"    {r:>3} {0:>8} {0:>8} {0:>7}  --")
            continue
        distinct = set(blocks)
        sizes = sorted((len(b) for b in blocks), reverse=True)
        for b in distinct:
            block_sig[b] += 1
        print(f"    {r:>3} {sum(sizes):>8} {len(distinct):>8} {max(sizes):>7}  {sizes}")
    # recurring large blocks (the L2+3 monolith)
    recur = sorted(((cnt, len(b)) for b, cnt in block_sig.items() if cnt >= 2 and len(b) >= 2),
                   reverse=True)
    if recur:
        print("    recurring blocks (size, rounds-present):")
        for cnt, sz in recur[:6]:
            print(f"      size {sz:>3} task-set moves as ONE unit in {cnt} rounds")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "s1k8b103")
