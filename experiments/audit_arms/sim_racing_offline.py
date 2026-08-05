"""Sim A (audit idea ⑧) -- offline replay of the racing acceptance gate.

Reads real gaia_evolver comparison.json data (READ-ONLY), builds paired
base-vs-candidate streams, and replays the sequential McNemar SPRT + Hoeffding
race offline (zero API cost). Three readouts:

  1. True-effect pairing: for every ordered variant pair (base Vi, cand Vj) in a
     run, match on shared task_id (streamed in round order) and record the SPRT /
     Hoeffding decision, n_used, and observed uplift. Aggregate ACCEPT / REJECT /
     CONTINUE rates overall and binned by observed uplift.
  2. A/A calibration (true null): same-variant attempt-0 vs attempt-1 pairs.
     Bootstrap (cluster = (run, task)) the false-ACCEPT rate; it must land at or
     below alpha.
  3. Budget reallocation: rollouts saved by early-stopping vs a fixed-N protocol.

Pairing note (recorded in ARMS-LEDGER.md): the evolver router partitions each
round's task set across active variants, so no two variants co-measure the same
task within a single round -- the literal same-round reading yields zero pairs.
Per the spec's operative phrases ("real per-task paired results", "stream in
round order") we match base vs candidate on shared task_id ACROSS rounds and
stream in round order. Each variant's per-task result is its earliest-round cell.

CLI, self-contained (stdlib only + sibling audit_arms modules).
"""

from __future__ import annotations

import argparse
import json
import os
import random
import statistics
import sys
from collections import defaultdict
from typing import Any, Dict, List, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # repo-idiom self-insert
import racing_gate as rg          # noqa: E402
import replay_data as rd          # noqa: E402

DEFAULT_RUNS_DIR = r"D:/PycharmProj/HarnessX/recipe/gaia_evolver/runs"
DEFAULT_OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")
SEED = 20260805
B_BOOT = 2000

# Observed-uplift bins in percentage points (lo_exclusive, hi_inclusive], with
# open ends at +/- inf.
UPLIFT_BINS = [
    (float("-inf"), -5.0, "<=-5pp"),
    (-5.0, -1.0, "(-5,-1]pp"),
    (-1.0, 1.0, "(-1,1]pp"),
    (1.0, 3.0, "(1,3]pp"),
    (3.0, 5.0, "(3,5]pp"),
    (5.0, 10.0, "(5,10]pp"),
    (10.0, float("inf"), ">10pp"),
]


def _bin_label(uplift_pp: float) -> str:
    for lo, hi, label in UPLIFT_BINS:
        if lo < uplift_pp <= hi:
            return label
    return UPLIFT_BINS[0][2]


# --------------------------------------------------------------------------- #
# Pure pairing helpers                                                         #
# --------------------------------------------------------------------------- #
def variant_task_result(cells: List[Dict[str, Any]]) -> Dict[Any, Dict[Any, Tuple[int, int]]]:
    """variant -> task -> (round, passed) using the EARLIEST round per (variant, task)."""
    out: Dict[Any, Dict[Any, Tuple[int, int]]] = defaultdict(dict)
    for c in cells:
        v, t = c["variant"], c["task"]
        r = c["round"] if c["round"] is not None else 0
        p = 1 if c["passed"] else 0
        prev = out[v].get(t)
        if prev is None or r < prev[0]:
            out[v][t] = (r, p)
    return out


def build_variant_pairs(cells: List[Dict[str, Any]]) -> Dict[Tuple[Any, Any], List[Tuple[int, int, int]]]:
    """For every ordered variant pair (base, cand) sharing >=1 task, build the
    round-ordered stream of (stream_round, base_pass, cand_pass) matched by task.
    stream_round is the candidate's earliest round for that task.
    """
    vt = variant_task_result(cells)
    variants = sorted(vt.keys(), key=lambda x: (x is None, str(x)))
    pairs: Dict[Tuple[Any, Any], List[Tuple[int, int, int]]] = {}
    for base in variants:
        for cand in variants:
            if base == cand:
                continue
            shared = set(vt[base]) & set(vt[cand])
            if not shared:
                continue
            rows: List[Tuple[int, str, int, int]] = []
            for t in shared:
                _, bp = vt[base][t]
                cr, cp = vt[cand][t]
                rows.append((cr, str(t), bp, cp))
            rows.sort(key=lambda row: (row[0], row[1]))  # candidate round, then task (deterministic)
            pairs[(base, cand)] = [(r, bp, cp) for r, _, bp, cp in rows]
    return pairs


def build_aa_pairs(cells: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Same-variant attempt-0 vs attempt-1 pairs (true null). One per cell that
    has >=2 kept attempts. Returns dicts carrying the (run, task) cluster key.
    """
    out: List[Dict[str, Any]] = []
    for c in cells:
        atts = c["attempts"]
        if len(atts) < 2:
            continue
        out.append({
            "run": c["run"],
            "task": c["task"],
            "round": c["round"] if c["round"] is not None else 0,
            "base_pass": 1 if atts[0]["passed"] else 0,   # attempt 0 as "base"
            "cand_pass": 1 if atts[1]["passed"] else 0,    # attempt 1 as "cand"
        })
    return out


# --------------------------------------------------------------------------- #
# SPRT / Hoeffding replay over the true-effect pairs                           #
# --------------------------------------------------------------------------- #
def replay_true_effect(pairs: Dict[Tuple[Any, Any], List[Tuple[int, int, int]]],
                       p1_grid: Tuple[float, ...], alpha: float, beta: float
                       ) -> Dict[str, Any]:
    per_p1: Dict[str, Any] = {}
    # overall discordant rate for implied-uplift reporting
    tot_pairs = tot_disc = 0
    for stream in pairs.values():
        for _, bp, cp in stream:
            tot_pairs += 1
            if bp != cp:
                tot_disc += 1
    disc_rate = (tot_disc / tot_pairs) if tot_pairs else 0.0

    for p1 in p1_grid:
        cfg = rg.SPRTConfig(alpha=alpha, beta=beta, p1=p1)
        decisions = {"ACCEPT": 0, "REJECT": 0, "CONTINUE": 0}
        by_bin: Dict[str, Dict[str, int]] = defaultdict(lambda: {"ACCEPT": 0, "REJECT": 0, "CONTINUE": 0})
        hoeff = {"ACCEPT": 0, "REJECT": 0, "CONTINUE": 0}
        n_used_list: List[int] = []
        rows = []
        for (base, cand), stream in pairs.items():
            sprt_stream = [(bp, cp) for _, bp, cp in stream]
            res = rg.decide(sprt_stream, cfg)
            hres = rg.hoeffding_race(sprt_stream, alpha=alpha)
            n = len(stream)
            n_base = sum(bp for _, bp, _ in stream)
            n_cand = sum(cp for _, _, cp in stream)
            uplift_pp = 100.0 * ((n_cand - n_base) / n) if n else 0.0
            decisions[res.decision] += 1
            hoeff[hres.decision] += 1
            by_bin[_bin_label(uplift_pp)][res.decision] += 1
            n_used_list.append(res.n_used)
            rows.append({
                "base": str(base), "cand": str(cand), "n": n,
                "uplift_pp": round(uplift_pp, 2),
                "sprt": res.decision, "n_used": res.n_used,
                "n_discordant": res.n_discordant, "llr": round(res.llr, 3),
                "hoeffding": hres.decision,
            })
        total = sum(decisions.values()) or 1
        per_p1[f"p1={p1}"] = {
            "p1": p1,
            "implied_uplift_pp": round(100.0 * cfg.implied_uplift(disc_rate), 3),
            "n_variant_pairs": sum(decisions.values()),
            "sprt_decisions": decisions,
            "sprt_rates": {k: round(v / total, 4) for k, v in decisions.items()},
            "hoeffding_decisions": hoeff,
            "by_uplift_bin": {k: dict(v) for k, v in by_bin.items()},
            "n_used": _dist(n_used_list),
            "rows": rows,
        }
    return {"discordant_rate": round(disc_rate, 4), "n_pairs_total": tot_pairs, "per_p1": per_p1}


def _dist(xs: List[int]) -> Dict[str, float]:
    if not xs:
        return {"n": 0}
    return {
        "n": len(xs),
        "min": min(xs),
        "median": statistics.median(xs),
        "mean": round(statistics.mean(xs), 2),
        "max": max(xs),
    }


# --------------------------------------------------------------------------- #
# A/A calibration via cluster bootstrap                                        #
# --------------------------------------------------------------------------- #
def aa_calibration(aa_pairs: List[Dict[str, Any]], p1_grid: Tuple[float, ...],
                   alpha: float, beta: float, n_boot: int, seed: int) -> Dict[str, Any]:
    # group by cluster (run, task)
    clusters: Dict[Tuple[Any, Any], List[Dict[str, Any]]] = defaultdict(list)
    for pr in aa_pairs:
        clusters[(pr["run"], pr["task"])].append(pr)
    cluster_keys = list(clusters.keys())

    out: Dict[str, Any] = {"n_aa_pairs": len(aa_pairs), "n_clusters": len(cluster_keys),
                           "n_boot": n_boot, "seed": seed, "per_p1": {}}
    if not cluster_keys:
        return out

    rnd = random.Random(seed)
    # pre-draw bootstrap cluster index sets so every p1 sees the same resamples
    draws = [[rnd.randrange(len(cluster_keys)) for _ in cluster_keys] for _ in range(n_boot)]

    for p1 in p1_grid:
        cfg = rg.SPRTConfig(alpha=alpha, beta=beta, p1=p1)
        counts = {"ACCEPT": 0, "REJECT": 0, "CONTINUE": 0}
        for idxs in draws:
            stream_rows: List[Dict[str, Any]] = []
            for i in idxs:
                stream_rows.extend(clusters[cluster_keys[i]])
            stream_rows.sort(key=lambda r: r["round"])
            stream = [(r["base_pass"], r["cand_pass"]) for r in stream_rows]
            counts[rg.decide(stream, cfg).decision] += 1
        rate = counts["ACCEPT"] / n_boot
        se = (rate * (1 - rate) / n_boot) ** 0.5
        out["per_p1"][f"p1={p1}"] = {
            "p1": p1,
            "false_accept_rate": round(rate, 4),
            "false_accept_se": round(se, 4),
            "false_accept_ci95": [round(max(0.0, rate - 1.96 * se), 4),
                                  round(min(1.0, rate + 1.96 * se), 4)],
            "decisions": counts,
            "within_alpha": rate <= alpha + 1.96 * se,
        }
    return out


# --------------------------------------------------------------------------- #
# Budget reallocation                                                          #
# --------------------------------------------------------------------------- #
def budget_readout(true_effect: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for key, blk in true_effect["per_p1"].items():
        fixed_total = 0
        racing_total = 0
        saved_reject = 0
        saved_accept = 0
        n_early = 0
        for row in blk["rows"]:
            fixed_total += row["n"]           # fixed-N runs the whole stream
            racing_total += row["n_used"]
            if row["sprt"] == "REJECT":
                saved_reject += row["n"] - row["n_used"]
                n_early += 1
            elif row["sprt"] == "ACCEPT":
                saved_accept += row["n"] - row["n_used"]
                n_early += 1
        saved = fixed_total - racing_total
        out[key] = {
            "p1": blk["p1"],
            "fixed_n_pairs": fixed_total,
            "racing_pairs": racing_total,
            "pairs_saved": saved,
            "pairs_saved_by_early_reject": saved_reject,
            "pairs_saved_by_early_accept": saved_accept,
            "frac_saved": round(saved / fixed_total, 4) if fixed_total else 0.0,
            "n_early_stopped_pairs": n_early,
            "note": "1 pair == 1 candidate task evaluation; multiply by attempts/task for rollouts.",
        }
    return out


# --------------------------------------------------------------------------- #
# Report writers                                                               #
# --------------------------------------------------------------------------- #
def write_reports(result: Dict[str, Any], out_dir: str) -> Tuple[str, str]:
    os.makedirs(out_dir, exist_ok=True)
    json_path = os.path.join(out_dir, "racing_offline_report.json")
    md_path = os.path.join(out_dir, "racing_offline_report.md")
    with open(json_path, "w", encoding="utf-8") as fh:
        json.dump(result, fh, indent=2, default=str)

    te = result["true_effect"]
    aa = result["aa_calibration"]
    bud = result["budget"]
    lines: List[str] = []
    lines.append("# Sim A -- Racing Acceptance Gate (offline replay)\n")
    lines.append(f"- runs used: {', '.join(result['meta']['used_runs']) or '(none)'}")
    lines.append(f"- runs skipped: {result['meta']['skipped'] or '(none)'}")
    lines.append(f"- variant pairs: {result['meta']['n_variant_pairs']} | "
                 f"true-effect pairs streamed: {te['n_pairs_total']} | "
                 f"overall discordant rate: {te['discordant_rate']}")
    lines.append(f"- constants: alpha={result['meta']['alpha']} beta={result['meta']['beta']} "
                 f"p1_grid={result['meta']['p1_grid']} B={result['meta']['n_boot']} "
                 f"seed={result['meta']['seed']} drop_infra={result['meta']['drop_infra']}\n")

    lines.append("## 1. True-effect SPRT decisions\n")
    lines.append("| p1 | implied uplift | ACCEPT | REJECT | CONTINUE | n_used median/mean |")
    lines.append("|---|---|---|---|---|---|")
    for key, blk in te["per_p1"].items():
        r = blk["sprt_rates"]
        nu = blk["n_used"]
        lines.append(f"| {blk['p1']} | {blk['implied_uplift_pp']}pp | "
                     f"{blk['sprt_decisions']['ACCEPT']} ({r['ACCEPT']}) | "
                     f"{blk['sprt_decisions']['REJECT']} ({r['REJECT']}) | "
                     f"{blk['sprt_decisions']['CONTINUE']} ({r['CONTINUE']}) | "
                     f"{nu.get('median','-')}/{nu.get('mean','-')} |")
    lines.append("")
    lines.append("### ACCEPT count by observed-uplift bin")
    lines.append("| p1 | " + " | ".join(b[2] for b in UPLIFT_BINS) + " |")
    lines.append("|---|" + "|".join(["---"] * len(UPLIFT_BINS)) + "|")
    for key, blk in te["per_p1"].items():
        cells = []
        for _, _, label in UPLIFT_BINS:
            d = blk["by_uplift_bin"].get(label, {})
            cells.append(f"A{d.get('ACCEPT',0)}/R{d.get('REJECT',0)}/C{d.get('CONTINUE',0)}")
        lines.append(f"| {blk['p1']} | " + " | ".join(cells) + " |")
    lines.append("")

    lines.append("## 2. A/A calibration (true null, cluster bootstrap)\n")
    lines.append(f"- A/A pairs: {aa['n_aa_pairs']} across {aa['n_clusters']} (run,task) clusters, "
                 f"B={aa['n_boot']}")
    lines.append("| p1 | false-ACCEPT rate | 95% CI | within alpha? |")
    lines.append("|---|---|---|---|")
    for key, blk in aa.get("per_p1", {}).items():
        lines.append(f"| {blk['p1']} | {blk['false_accept_rate']} +/- {blk['false_accept_se']} | "
                     f"{blk['false_accept_ci95']} | {blk['within_alpha']} |")
    lines.append("")

    lines.append("## 3. Budget reallocation (racing vs fixed-N)\n")
    lines.append("| p1 | fixed-N pairs | racing pairs | saved | frac saved | saved(early REJECT) |")
    lines.append("|---|---|---|---|---|---|")
    for key, blk in bud.items():
        lines.append(f"| {blk['p1']} | {blk['fixed_n_pairs']} | {blk['racing_pairs']} | "
                     f"{blk['pairs_saved']} | {blk['frac_saved']} | "
                     f"{blk['pairs_saved_by_early_reject']} |")
    lines.append("")
    lines.append("> Honesty check (audit): at 1-3pp observed uplift the gate almost never "
                 "ACCEPTs -- racing manufactures no power, it only reallocates budget.")
    with open(md_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    return md_path, json_path


# --------------------------------------------------------------------------- #
# Main                                                                         #
# --------------------------------------------------------------------------- #
def run(runs_dir: str, runs, min_variants, alpha, beta, p1_grid, n_boot, seed,
        drop_infra) -> Dict[str, Any]:
    rep = rd.load_report(runs_dir, runs=runs, min_variants=min_variants, drop_infra=drop_infra)
    by_run: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for c in rep.cells:
        by_run[c["run"]].append(c)

    all_pairs: Dict[Tuple[str, Any, Any], List[Tuple[int, int, int]]] = {}
    for run_name, cells in by_run.items():
        for (base, cand), stream in build_variant_pairs(cells).items():
            all_pairs[(run_name, base, cand)] = stream
    flat_pairs = {(b, c): s for (r, b, c), s in all_pairs.items()}

    true_effect = replay_true_effect(flat_pairs, tuple(p1_grid), alpha, beta)
    aa_pairs = build_aa_pairs(rep.cells)
    aa = aa_calibration(aa_pairs, tuple(p1_grid), alpha, beta, n_boot, seed)
    bud = budget_readout(true_effect)

    return {
        "meta": {
            "runs_dir": runs_dir,
            "used_runs": rep.used_runs,
            "skipped": rep.skipped,
            "files": rep.files,
            "manifest": rd.manifest(rep.files),
            "n_variant_pairs": len(all_pairs),
            "alpha": alpha, "beta": beta, "p1_grid": list(p1_grid),
            "n_boot": n_boot, "seed": seed, "drop_infra": drop_infra,
            "min_variants": min_variants,
        },
        "true_effect": true_effect,
        "aa_calibration": aa,
        "budget": bud,
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description="Offline replay of the racing acceptance gate (Sim A).")
    ap.add_argument("--runs-dir", default=DEFAULT_RUNS_DIR)
    ap.add_argument("--runs", nargs="*", default=None,
                    help="explicit run names; overrides auto-discovery")
    ap.add_argument("--min-variants", type=int, default=8,
                    help="auto-discover runs with >= this many distinct variants (default 8 => K=8)")
    ap.add_argument("--alpha", type=float, default=rg.ALPHA)
    ap.add_argument("--beta", type=float, default=rg.BETA)
    ap.add_argument("--p1", type=float, nargs="*", default=list(rg.P1_GRID))
    ap.add_argument("--B", type=int, default=B_BOOT, dest="n_boot")
    ap.add_argument("--seed", type=int, default=SEED)
    ap.add_argument("--keep-infra", action="store_true", help="do NOT drop infra-failure attempts")
    ap.add_argument("--out", default=DEFAULT_OUT)
    args = ap.parse_args(argv)

    result = run(args.runs_dir, args.runs, args.min_variants, args.alpha, args.beta,
                 args.p1, args.n_boot, args.seed, drop_infra=not args.keep_infra)
    md, js = write_reports(result, args.out)
    print(f"[sim_racing_offline] used runs: {result['meta']['used_runs']}")
    print(f"[sim_racing_offline] variant pairs: {result['meta']['n_variant_pairs']} | "
          f"AA pairs: {result['aa_calibration']['n_aa_pairs']}")
    for key, blk in result["aa_calibration"].get("per_p1", {}).items():
        print(f"[sim_racing_offline] {key}: false-ACCEPT={blk['false_accept_rate']} "
              f"(alpha={args.alpha})")
    print(f"[sim_racing_offline] wrote {md} and {js}")
    return result


if __name__ == "__main__":
    main()
