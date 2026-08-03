"""G. ever_solved ratchet: does the no-regression constraint set outgrow what any
single round can actually pass?

The paper's seesaw rule (S4.1): "the candidate must not regress any previously
solved task recorded in T_t".  T_t is monotone -- it never forgets.  Combined with
a bed whose identical-config re-measurement flips ~23% of tasks, a task that passes
once by luck becomes a permanent constraint.

Measures, per round:
  round_pass   : tasks passing this round (pass@2, any variant)
  ever_global  : union of round_pass over all rounds <= t      <- the global baseline
  gap          : ever_global - round_pass  (constraints that this round cannot meet)

Plus the "luck" distribution: for each task in the final ever_solved set, in how
many of the measured rounds did it actually pass.

Read-only. Zero API cost.  Usage:  python g_ever_solved_ratchet.py [run_tag]
"""
import json
import os
import re
import sys
import glob
from collections import Counter, defaultdict

RUNS = os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "recipe", "gaia_evolver", "runs"))


def load_rounds(run_dir):
    out = {}
    for p in glob.glob(os.path.join(run_dir, "R*", "pool_state.json")):
        idx = int(re.findall(r"R(\d+)", os.path.basename(os.path.dirname(p)))[0])
        with open(p, encoding="utf-8") as fh:
            out[idx] = json.load(fh)
    return dict(sorted(out.items()))


def main(tag="s1k8b103"):
    run_dir = os.path.join(RUNS, tag)
    rounds = load_rounds(run_dir)
    if not rounds:
        print(f"no rounds under {run_dir}")
        return

    per_round_pass = {}            # round -> set(task)
    per_variant_ever = defaultdict(set)
    task_pass_rounds = Counter()   # task -> how many rounds it passed in
    measured_rounds = 0

    for r, d in rounds.items():
        apm = d.get("active_pool_measurements") or {}
        if not apm:
            continue
        measured_rounds += 1
        passing = set()
        for v, cells in apm.items():
            for t, c in cells.items():
                if c[0] >= 1:
                    passing.add(t)
                    per_variant_ever[v].add(t)
        per_round_pass[r] = passing
        for t in passing:
            task_pass_rounds[t] += 1

    print(f"=== {tag} : ever_solved ratchet ===\n")
    print(f"{'R':>3} {'round_pass':>11} {'ever_global':>12} {'gap':>6} {'new_this_round':>15}")
    print("-" * 52)
    ever = set()
    for r in sorted(per_round_pass):
        p = per_round_pass[r]
        new = p - ever
        ever |= p
        print(f"{r:>3} {len(p):>11} {len(ever):>12} {len(ever) - len(p):>6} {len(new):>15}")

    sizes = [len(p) for p in per_round_pass.values()]
    print()
    print(f"bed size (distinct tasks measured)  = "
          f"{len(set().union(*per_round_pass.values())) if per_round_pass else 0}")
    print(f"max  single-round pass              = {max(sizes)}")
    print(f"mean single-round pass              = {sum(sizes)/len(sizes):.1f}")
    print(f"FINAL ever_solved (global)          = {len(ever)}")
    print(f"  -> exceeds max single round by    = {len(ever) - max(sizes)}  "
          f"({(len(ever) - max(sizes)) / max(sizes) * 100:+.1f}%)")

    print("\n--- 'luck' distribution: how many rounds did each ever_solved task pass in? ---")
    dist = Counter(task_pass_rounds.values())
    cum = 0
    for k in sorted(dist):
        cum += dist[k]
        print(f"  passed in {k:>2}/{measured_rounds} rounds : {dist[k]:>3} tasks   "
              f"(cumulative {cum:>3} = {cum/len(ever)*100:5.1f}% of ever_solved)")
    lucky = sum(v for k, v in dist.items() if k <= 2)
    print(f"\n  tasks that passed in <=2 of {measured_rounds} rounds : {lucky} "
          f"({lucky/len(ever)*100:.1f}% of the constraint set)")

    print("\n--- per-variant ever_solved sizes ---")
    for v in sorted(per_variant_ever):
        print(f"  {v}: {len(per_variant_ever[v])}")

    # gate outcome peek on the rounds that produced candidates but did not ship
    print("\n--- rounds with candidates but no ship ---")
    for r, d in rounds.items():
        if d.get("candidate_count") and not d.get("shipped"):
            diag = d.get("candidate_diagnostics")
            dec = d.get("decisions")
            print(f"  R{r}: candidates={d.get('candidate_count')} target={d.get('paper_target_variant')}")
            for label, blob in (("decisions", dec), ("diagnostics", diag)):
                if blob:
                    s = json.dumps(blob, ensure_ascii=False)
                    print(f"    {label}: {s[:600]}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "s1k8b103")
