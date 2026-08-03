# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""Analysis E -- cross-check the carrier-count claim on other runs.

Claim (observed on s1k8b103): with cluster routing over 3 GAIA-level clusters,
a NON-fork round has at most 3 variants carrying tasks (one argmax carrier per
cluster), while a FORK round can transiently reach 4 (the newborn child).

This script recomputes, for each run, the cluster count and the per-round number
of variants carrying >=1 routed task, split by whether a fork happened that
round, and states plainly whether the claim holds or is falsified.

Usage:  python experiments/analysis/novelty/e_cross_check_carriers.py [run ...]
        (default: s1k8 a1big5 e_pervar3 s1k8b103)
"""

from __future__ import annotations

import sys

import _common as C

DEFAULT_RUNS = ["s1k8", "a1big5", "e_pervar3", "s1k8b103"]


def check_run(run: str) -> dict:
    states = C.all_states(run)
    levels = C.level_map(run)
    cfg = C.run_config(run)
    n_clusters = len(set(levels.values()))
    rows = []
    for r in sorted(states):
        st = states[r]
        car = C.carriers(st)
        carrying = sorted(v for v, n in car.items() if n > 0)
        is_fork = bool(st.get("forked"))
        rows.append({
            "round": r,
            "n_carriers": len(carrying),
            "fork": is_fork,
            "carriers": carrying,
        })
    nonfork_max = max((row["n_carriers"] for row in rows if not row["fork"]), default=None)
    fork_max = max((row["n_carriers"] for row in rows if row["fork"]), default=None)
    holds = (nonfork_max is None or nonfork_max <= n_clusters)
    return {
        "run": run,
        "cluster_source": cfg.get("cluster_source"),
        "routing_mode": cfg.get("routing_mode"),
        "n_clusters": n_clusters,
        "rows": rows,
        "nonfork_max": nonfork_max,
        "fork_max": fork_max,
        "claim_holds": holds,
    }


def main(runs) -> None:
    print("# Analysis E -- carrier-count cross-check\n")
    print("Claim: non-fork rounds carriers <= #clusters; fork rounds may reach #clusters+1.\n")
    for run in runs:
        try:
            res = check_run(run)
        except FileNotFoundError as exc:
            print(f"## {run}: SKIP ({exc})\n")
            continue
        print(f"## {run}  (cluster_source={res['cluster_source']}, "
              f"routing_mode={res['routing_mode']}, #clusters={res['n_clusters']})")
        print("   {:>3} {:>9} {:>5}  carriers".format("R", "#carriers", "fork"))
        for row in res["rows"]:
            print("   {:>3} {:>9} {:>5}  {}".format(
                row["round"], row["n_carriers"], "FORK" if row["fork"] else "-",
                ",".join(row["carriers"])))
        print(f"   max carriers on non-fork rounds = {res['nonfork_max']}  "
              f"(<= #clusters={res['n_clusters']} ? {res['nonfork_max'] is not None and res['nonfork_max'] <= res['n_clusters']})")
        print(f"   max carriers on fork rounds     = {res['fork_max']}")
        verdict = "HOLDS" if res["claim_holds"] else "FALSIFIED"
        extra = ""
        if res["fork_max"] is not None and res["nonfork_max"] is not None and res["fork_max"] <= res["nonfork_max"]:
            extra = "  (note: no fork round exceeded the non-fork max in this run -- '4' not observed here)"
        print(f"   => claim {verdict}{extra}\n")


if __name__ == "__main__":
    args = sys.argv[1:] or DEFAULT_RUNS
    main(args)
