"""Build a capability partition that survives being sampled again.

Why. Two independent decomposition passes over the same 103 tasks agree on the
exact type set for only **42.7%** of them (Jaccard 0.781, Rand 0.779). A
partition read off one pass is therefore substantially an artefact of that
sampling, and routing on it would inherit the noise.

What is stable is the *marginal*: how often each capability is mentioned at all
barely moves between passes (search 96/95, browse 64/57, compute 81/76, verify
63/65). So the consensus is taken **per capability, not per set**: a type joins a
task's label if at least ``--min-votes`` of the passes named it. Requiring whole
sets to match would throw away most of the agreement that exists.

The stability figures this fixes are themselves worth reporting -- an LLM
decomposer is a noisy instrument, and any work routing on its output owes the
reader that number.
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import itertools
import json
import statistics
from pathlib import Path


def _load(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _agreement(a: dict, b: dict) -> dict:
    common = sorted(set(a) & set(b))
    exact = sum(1 for t in common if a[t] == b[t])
    jac = []
    for t in common:
        x, y = set(a[t].split("+")), set(b[t].split("+"))
        jac.append(len(x & y) / len(x | y) if (x | y) else 1.0)
    agree = tot = 0
    for p, q in itertools.combinations(common, 2):
        tot += 1
        agree += (a[p] == a[q]) == (b[p] == b[q])
    return {
        "n": len(common),
        "exact": round(exact / len(common), 3),
        "jaccard": round(statistics.mean(jac), 3),
        "rand": round(agree / tot, 3) if tot else 1.0,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--passes", nargs="+", required=True, help="task_clusters*.json files")
    ap.add_argument("--out", required=True)
    ap.add_argument("--min-votes", type=int, default=2)
    args = ap.parse_args()

    payloads = [_load(p) for p in args.passes]
    tables = [p["clusters"] for p in payloads]
    if len(tables) < 2:
        raise SystemExit("consensus needs at least two passes")

    print(f"passes: {len(tables)}   min-votes: {args.min_votes}")
    print("\n=== pairwise stability of the raw passes ===")
    for i, j in itertools.combinations(range(len(tables)), 2):
        st = _agreement(tables[i], tables[j])
        print(f"  pass{i+1} vs pass{j+1}: exact {st['exact']:.1%}  "
              f"jaccard {st['jaccard']:.3f}  rand {st['rand']:.3f}  (n={st['n']})")

    # UNION, not intersection. A pass can drop a task to an infrastructure
    # error -- v3 lost one to a server disconnect -- and intersecting would
    # silently shrink the partition below the bench, which the router's
    # fail-closed coverage check would then reject. Each task is instead judged
    # against the passes that actually have it.
    tasks = sorted(set().union(*(set(t) for t in tables)))
    votes = {t: collections.Counter() for t in tasks}
    present = collections.Counter()
    for table in tables:
        for t in tasks:
            if t in table:
                present[t] += 1
                votes[t].update(set(table[t].split("+")))

    partial = sorted(t for t in tasks if present[t] < len(tables))
    if partial:
        print(f"\n=== tasks missing from some pass: {len(partial)} ===")
        for t in partial:
            print(f"  {t[:8]}  present in {present[t]}/{len(tables)} passes")

    consensus, empties = {}, []
    for t in tasks:
        # Never demand more votes than there are passes carrying the task.
        needed = min(args.min_votes, present[t])
        kept = sorted(c for c, n in votes[t].items() if n >= needed)
        if not kept:
            # Every capability was named by only one pass. Falling back to the
            # union keeps the task in the partition rather than dropping it,
            # and is recorded so the count is visible.
            kept = sorted(votes[t])
            empties.append(t)
        consensus[t] = "+".join(kept)

    print("\n=== consensus vs each pass ===")
    for i, table in enumerate(tables):
        st = _agreement(consensus, table)
        print(f"  consensus vs pass{i+1}: exact {st['exact']:.1%}  jaccard {st['jaccard']:.3f}")

    sizes = collections.Counter(consensus.values())
    print(f"\n=== consensus partition ===\nclusters: {len(sizes)}")
    for k, n in sizes.most_common():
        print(f"  {n:>4}  {k}")
    print(f"\n  >= 8 tasks: {sum(1 for n in sizes.values() if n >= 8)}")
    if empties:
        print(f"  no-majority tasks (fell back to union): {len(empties)}")

    body = json.dumps(
        {
            "schema": "task_clusters/consensus-v1",
            "source": "d1lite_subtask_type_set, per-capability majority",
            "passes": [str(p) for p in args.passes],
            "pass_digests": [
                hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in args.passes
            ],
            "min_votes": args.min_votes,
        "passes_per_task": {t: present[t] for t in tasks if present[t] < len(tables)},
            "no_majority_tasks": empties,
            "clusters": consensus,
            # Plans come from the FIRST pass, so the arms replay a real
            # decomposition rather than a synthesised consensus that no
            # decomposer ever produced.
            "plans": payloads[0].get("plans", {}),
            "plans_from": str(args.passes[0]),
        },
        indent=2, ensure_ascii=False, sort_keys=True,
    )
    Path(args.out).write_text(body, encoding="utf-8")
    print(f"\nwrote {args.out}\nsha256 {hashlib.sha256(body.encode()).hexdigest()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
