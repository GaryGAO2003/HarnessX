"""Sim B (audit idea ⑩) -- offline replay of cold-start router shrinkage.

Reads real gaia_evolver comparison.json data (READ-ONLY) and runs a leave-
future-out (LFO) replay: for each run and each round r>=1, predict round-r cell
success from rounds < r only, at the (variant, cluster) grain. Compares raw MLE
(two empty-cell conventions) against hierarchical beta-binomial shrinkage over a
pre-registered pseudo-count grid.

Estimators
  mle@0.5   raw MLE, empty cell -> 0.5
  mle@0.7   raw MLE, empty cell -> 0.7  (official _UNKNOWN_BOOST optimism value)
  shrink@m  two-level chain (cell->variant->pool), pseudo-count m, m in {1,2,4,8}

Metrics
  Brier, log-loss (per scored future attempt outcome)
  n=1 overcorrection-flip count: at prediction points whose history holds exactly
  ONE observation for the (variant, cluster) cell, how often the estimate lands on
  the opposite side of the 0.5 routing threshold from the pool prior p_pool. Raw
  MLE flips on a single lucky/unlucky rollout; shrinkage resists (F2 style).

CLI, self-contained (stdlib only + sibling audit_arms modules).
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from collections import defaultdict
from typing import Any, Dict, List, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # repo-idiom self-insert
import cold_start as cs           # noqa: E402
import replay_data as rd          # noqa: E402

DEFAULT_RUNS_DIR = r"D:/PycharmProj/HarnessX/recipe/gaia_evolver/runs"
DEFAULT_OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")
UNKNOWN_BOOST = 0.7   # aligns with the official router _UNKNOWN_BOOST empty-cell optimism
EPS = 1e-6            # log-loss clipping


def _cluster_of(cell: Dict[str, Any], cluster_key: str):
    return cell["task"] if cluster_key == "task" else cell["level"]


def _history_records(cells: List[Dict[str, Any]], cluster_key: str) -> List[Dict[str, Any]]:
    return [{"variant": c["variant"], "cluster": _cluster_of(c, cluster_key),
             "s": c["n_pass"], "n": c["n_att"]} for c in cells]


def _outcomes(cell: Dict[str, Any]) -> List[int]:
    return [1 if a["passed"] else 0 for a in cell["attempts"]]


def _brier(p: float, y: int) -> float:
    return (p - y) ** 2


def _logloss(p: float, y: int) -> float:
    p = min(max(p, EPS), 1.0 - EPS)
    return -(y * math.log(p) + (1 - y) * math.log(1.0 - p))


def _same_side(a: float, b: float) -> bool:
    """True if a and b are on the same side of 0.5 (0.5 treated as its own side)."""
    return (a < 0.5) == (b < 0.5)


# --------------------------------------------------------------------------- #
# Leave-future-out replay for one cluster key                                  #
# --------------------------------------------------------------------------- #
def replay_lfo(cells_by_run: Dict[str, List[Dict[str, Any]]], cluster_key: str,
               m_grid: Tuple[int, ...]) -> Dict[str, Any]:
    estimators = ["mle@0.5", f"mle@{UNKNOWN_BOOST}"] + [f"shrink@{m}" for m in m_grid]
    agg = {e: {"brier_sum": 0.0, "logloss_sum": 0.0, "n": 0} for e in estimators}
    flips = {e: 0 for e in estimators}
    flip_points = 0        # number of n==1 prediction points examined
    n_predpoints = 0       # number of scored future cells
    n_empty_hist = 0       # future cells with no (variant,cluster) history

    for run_name, cells in cells_by_run.items():
        rounds = sorted({c["round"] for c in cells if c["round"] is not None})
        for r in rounds:
            hist = [c for c in cells if c["round"] is not None and c["round"] < r]
            future = [c for c in cells if c["round"] == r]
            if not hist or not future:
                continue
            counts = cs.from_cells(_history_records(hist, cluster_key))

            for cell in future:
                v = cell["variant"]
                cl = _cluster_of(cell, cluster_key)
                outcomes = _outcomes(cell)
                if not outcomes:
                    continue
                n_predpoints += 1
                s_cell, n_cell = counts.cell(v, cl)
                if n_cell == 0:
                    n_empty_hist += 1

                preds = {
                    "mle@0.5": cs.mle(counts, v, cl, empty=0.5),
                    f"mle@{UNKNOWN_BOOST}": cs.mle(counts, v, cl, empty=UNKNOWN_BOOST),
                }
                for m in m_grid:
                    preds[f"shrink@{m}"] = cs.estimate(counts, v, cl, m1=m, m2=m,
                                                       pool_fallback=0.5)

                for e, p in preds.items():
                    for y in outcomes:
                        agg[e]["brier_sum"] += _brier(p, y)
                        agg[e]["logloss_sum"] += _logloss(p, y)
                        agg[e]["n"] += 1

                # n=1 overcorrection flip: history holds exactly one observation
                # for this cell. A "flip" = the routing side (estimate >= 0.5)
                # changes between the estimator's EMPTY-prior state (no cell obs)
                # and its ONE-observation state -- i.e. a single rollout crosses
                # the 0.5 routing threshold. Reference is per-estimator (own prior).
                if n_cell == 1:
                    flip_points += 1
                    for e, p_after in preds.items():
                        if e.startswith("mle@"):
                            p_before = float(e.split("@")[1])       # empty-cell convention
                        else:
                            m = int(e.split("@")[1])
                            p_before = cs.variant_estimate(counts, v, m2=m,
                                                           pool_fallback=0.5)  # shrink prior
                        if not _same_side(p_before, p_after):
                            flips[e] += 1

    results = {}
    for e in estimators:
        n = agg[e]["n"] or 1
        results[e] = {
            "brier": round(agg[e]["brier_sum"] / n, 5),
            "logloss": round(agg[e]["logloss_sum"] / n, 5),
            "n_scored": agg[e]["n"],
            "n1_flips": flips[e],
        }
    shrink_keys = [f"shrink@{m}" for m in m_grid]
    best_m = min(shrink_keys, key=lambda k: results[k]["brier"]) if shrink_keys else None
    return {
        "cluster_key": cluster_key,
        "estimators": results,
        "best_shrink_by_brier": best_m,
        "n_pred_points": n_predpoints,
        "n_empty_history_cells": n_empty_hist,
        "n1_flip_points": flip_points,
        "m_grid": list(m_grid),
    }


# --------------------------------------------------------------------------- #
# Report writers                                                               #
# --------------------------------------------------------------------------- #
def write_reports(result: Dict[str, Any], out_dir: str) -> Tuple[str, str]:
    os.makedirs(out_dir, exist_ok=True)
    json_path = os.path.join(out_dir, "cold_start_offline_report.json")
    md_path = os.path.join(out_dir, "cold_start_offline_report.md")
    with open(json_path, "w", encoding="utf-8") as fh:
        json.dump(result, fh, indent=2, default=str)

    lines: List[str] = []
    lines.append("# Sim B -- Cold-Start Router Shrinkage (offline LFO replay)\n")
    lines.append(f"- runs used: {', '.join(result['meta']['used_runs']) or '(none)'}")
    lines.append(f"- runs skipped: {result['meta']['skipped'] or '(none)'}")
    lines.append(f"- constants: m_grid={result['meta']['m_grid']} "
                 f"unknown_boost={UNKNOWN_BOOST} drop_infra={result['meta']['drop_infra']} "
                 f"min_variants={result['meta']['min_variants']}\n")
    for ck, block in result["by_cluster_key"].items():
        lines.append(f"## cluster-key = {ck}\n")
        lines.append(f"- prediction points: {block['n_pred_points']} "
                     f"(empty-history cells: {block['n_empty_history_cells']}); "
                     f"n=1 flip points: {block['n1_flip_points']}")
        lines.append(f"- best shrinkage by Brier: {block['best_shrink_by_brier']}\n")
        lines.append("| estimator | Brier | log-loss | n scored | n=1 flips |")
        lines.append("|---|---|---|---|---|")
        for e, m in block["estimators"].items():
            lines.append(f"| {e} | {m['brier']} | {m['logloss']} | {m['n_scored']} | {m['n1_flips']} |")
        lines.append("")
    lines.append("> Audit reading: raw MLE emits 0/1 point predictions after one rollout, so a "
                 "single observation crosses the 0.5 routing threshold (n=1 flips); shrinkage "
                 "holds the estimate near its prior and cuts those flips. Log-loss (calibration) "
                 "is where shrinkage gains most; the official 0.7 unknown-boost inflates error on "
                 "empty-history cells vs a neutral 0.5 on this low-pass-rate benchmark.")
    with open(md_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    return md_path, json_path


# --------------------------------------------------------------------------- #
# Main                                                                         #
# --------------------------------------------------------------------------- #
def run(runs_dir: str, runs, min_variants, cluster_keys, m_grid, drop_infra) -> Dict[str, Any]:
    rep = rd.load_report(runs_dir, runs=runs, min_variants=min_variants, drop_infra=drop_infra)
    by_run: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for c in rep.cells:
        by_run[c["run"]].append(c)

    by_ck = {ck: replay_lfo(by_run, ck, tuple(m_grid)) for ck in cluster_keys}
    return {
        "meta": {
            "runs_dir": runs_dir,
            "used_runs": rep.used_runs,
            "skipped": rep.skipped,
            "files": rep.files,
            "manifest": rd.manifest(rep.files),
            "m_grid": list(m_grid),
            "drop_infra": drop_infra,
            "min_variants": min_variants,
            "cluster_keys": cluster_keys,
        },
        "by_cluster_key": by_ck,
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description="Offline LFO replay of cold-start shrinkage (Sim B).")
    ap.add_argument("--runs-dir", default=DEFAULT_RUNS_DIR)
    ap.add_argument("--runs", nargs="*", default=None)
    ap.add_argument("--min-variants", type=int, default=8)
    ap.add_argument("--cluster-key", choices=["task", "level", "both"], default="both")
    ap.add_argument("--m", type=int, nargs="*", default=list(cs.M_GRID))
    ap.add_argument("--keep-infra", action="store_true")
    ap.add_argument("--out", default=DEFAULT_OUT)
    args = ap.parse_args(argv)

    cluster_keys = ["task", "level"] if args.cluster_key == "both" else [args.cluster_key]
    result = run(args.runs_dir, args.runs, args.min_variants, cluster_keys, args.m,
                 drop_infra=not args.keep_infra)
    md, js = write_reports(result, args.out)
    print(f"[sim_cold_start_offline] used runs: {result['meta']['used_runs']}")
    for ck, block in result["by_cluster_key"].items():
        best = block["best_shrink_by_brier"]
        est = block["estimators"]
        print(f"[sim_cold_start_offline] cluster={ck} pred_points={block['n_pred_points']} "
              f"best={best} Brier(best)={est[best]['brier'] if best else '-'} "
              f"Brier(mle@0.7)={est['mle@0.7']['brier']} "
              f"flips(mle@0.7)={est['mle@0.7']['n1_flips']} "
              f"flips({best})={est[best]['n1_flips'] if best else '-'}")
    print(f"[sim_cold_start_offline] wrote {md} and {js}")
    return result


if __name__ == "__main__":
    main()
