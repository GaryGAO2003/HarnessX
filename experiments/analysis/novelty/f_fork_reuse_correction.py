"""F. Fork-round candidate_reuse correction.

At every fork round the newborn variant's slice is scored from `candidate_reuse`
(the candidate-gate measurements that *defined* the fork's task set T_k), so that
slice passes 100% by construction. This inflates the round's reported pass@2.

Produces three curves:
  reported : as-is (what pool_report.curve contains)
  fresh    : reused slice dropped (denominator shrinks -- NOT cross-round comparable)
  imputed  : reused slice replaced by the SAME task_ids' fresh outcome in the NEXT
             round (unbiased when the newborn is re-measured fresh and its config
             was not edited in between); denominator stays at the full bed.

Read-only. Zero API cost.  Usage:  python f_fork_reuse_correction.py [run_tag]
"""
import json
import os
import re
import sys
import glob

RUNS = os.path.join(os.path.dirname(__file__), "..", "..", "..", "recipe", "gaia_evolver", "runs")
RUNS = os.path.abspath(RUNS)


def load_rounds(run_dir):
    out = {}
    for p in glob.glob(os.path.join(run_dir, "R*", "pool_state.json")):
        idx = int(re.findall(r"R(\d+)", os.path.basename(os.path.dirname(p)))[0])
        with open(p, encoding="utf-8") as fh:
            out[idx] = json.load(fh)
    return dict(sorted(out.items()))


def passed(cell):
    """pass@2 for one task: at least one of the attempts succeeded."""
    return cell[0] >= 1


def main(tag="s1k8b103"):
    run_dir = os.path.join(RUNS, tag)
    rounds = load_rounds(run_dir)
    if not rounds:
        print(f"no rounds under {run_dir}")
        return

    # index: round -> variant -> {task: cell}, and round -> variant -> source
    meas = {r: (d.get("active_pool_measurements") or {}) for r, d in rounds.items()}
    src = {r: (d.get("active_score_source") or {}) for r, d in rounds.items()}

    print(f"=== {tag} : fork-round candidate_reuse correction ===\n")
    header = (f"{'R':>3} {'reported':>9} {'reuse_n':>8} {'fresh':>9} {'fresh_n':>8} "
              f"{'imputed':>9} {'imp_n':>7} {'unimp':>6}  reuse_variants")
    print(header)
    print("-" * len(header))

    curves = {"reported": {}, "fresh": {}, "imputed": {}}
    detail = []

    for r in sorted(meas):
        cells = meas[r]
        if not cells:
            continue
        rep_p = rep_n = fr_p = fr_n = 0
        reuse_slice = []          # (variant, task_id, cell)
        reuse_vars = []
        for v, tasks in cells.items():
            source = src[r].get(v, "?")
            for t, c in tasks.items():
                rep_n += 1
                rep_p += passed(c)
                if source == "candidate_reuse":
                    reuse_slice.append((v, t, c))
                else:
                    fr_n += 1
                    fr_p += passed(c)
            if source == "candidate_reuse" and tasks:
                reuse_vars.append(f"{v}({len(tasks)})")

        # impute: same task_id measured fresh in the NEXT round
        imp_p, imp_n, unimp = fr_p, fr_n, 0
        nxt = meas.get(r + 1, {})
        nxt_src = src.get(r + 1, {})
        for v, t, _c in reuse_slice:
            found = None
            # prefer the same variant, then any variant measured fresh next round
            for cand_v in [v] + [x for x in nxt if x != v]:
                if nxt_src.get(cand_v) != "fresh_rollout":
                    continue
                cell = (nxt.get(cand_v) or {}).get(t)
                if cell is not None:
                    found = cell
                    break
            if found is None:
                unimp += 1
            else:
                imp_n += 1
                imp_p += passed(found)

        rep = rep_p / rep_n if rep_n else float("nan")
        fresh = fr_p / fr_n if fr_n else float("nan")
        imp = imp_p / imp_n if imp_n else float("nan")
        curves["reported"][r] = rep
        curves["fresh"][r] = fresh
        curves["imputed"][r] = imp
        detail.append((r, len(reuse_slice), unimp))

        print(f"{r:>3} {rep:>9.4f} {len(reuse_slice):>8} {fresh:>9.4f} {fr_n:>8} "
              f"{imp:>9.4f} {imp_n:>7} {unimp:>6}  {','.join(reuse_vars) or '-'}")

    # summary statistics on the evolution rounds (R1..), matching pool_report.curve
    print()
    evo = [r for r in sorted(curves["reported"]) if r >= 1]
    for name in ("reported", "fresh", "imputed"):
        vals = [curves[name][r] for r in evo]
        peak = max(vals)
        peak_r = evo[vals.index(peak)]
        final = vals[-1]
        print(f"{name:>9}:  peak={peak:.4f} @R{peak_r}   final={final:.4f}   "
              f"peak-final={peak - final:+.4f}")

    if 0 in curves["reported"]:
        print(f"\nR0 (bootstrap, all fresh) = {curves['reported'][0]:.4f}")
        for name in ("reported", "imputed"):
            final = curves[name][evo[-1]]
            print(f"  gain over R0, {name:>8} final: {final - curves['reported'][0]:+.4f}")

    tot_reuse = sum(d[1] for d in detail)
    tot_unimp = sum(d[2] for d in detail)
    print(f"\nreused cells total = {tot_reuse}   (unimputable = {tot_unimp})")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "s1k8b103")
