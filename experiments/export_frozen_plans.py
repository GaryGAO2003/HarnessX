"""Export the frozen decompositions as a replayable oracle file.

Why this exists. The CH4 arms differ only in where subtask work is sent. If each
arm generated its own decomposition, an arm difference would confound "the plans
differed" with "the routing differed" -- decomposition is a model call and does
not repeat. So the plans are generated once and replayed.

They already exist: ``build_task_clusters.py`` decomposed all 103 tasks to derive
the capability partition, and kept the plans. This turns that side product into
the input ``--decomp-source file:`` expects, so the arms replay the *same* plans
that CH3's partition was derived from rather than a second, differently-sampled
set.

Output is digest-stamped. All four arms must read one file and the manifests must
agree on its hash, or the comparison is not what it claims to be.
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
from pathlib import Path

REQUIRED = ("id", "type", "instruction", "dep")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--clusters", required=True, help="task_clusters.json from build_task_clusters.py")
    ap.add_argument("--out", required=True, help="Oracle plan file for --decomp-source file:<path>")
    args = ap.parse_args()

    src = Path(args.clusters)
    payload = json.loads(src.read_text(encoding="utf-8"))
    plans = payload.get("plans") or {}
    if not plans:
        raise SystemExit(f"{src}: no 'plans' object — rebuild with build_task_clusters.py")

    # The cluster builder kept only {id, type} per subtask, because the partition
    # needs the type set and nothing else. A replayable plan needs the
    # instruction and the dependency edges too, so a partial record has to fail
    # loudly rather than be silently padded: a fabricated instruction would make
    # every arm replay a plan the decomposer never produced.
    incomplete = [
        tid for tid, subs in plans.items()
        if not all(all(k in s for k in REQUIRED) for s in subs)
    ]
    if incomplete:
        raise SystemExit(
            f"{src}: {len(incomplete)}/{len(plans)} plans lack one of {REQUIRED} "
            f"(e.g. {incomplete[:2]}). The cluster builder stores only id+type; "
            "re-run it with full-record export before freezing plans for replay."
        )

    body = json.dumps(plans, indent=2, ensure_ascii=False, sort_keys=True)
    Path(args.out).write_text(body, encoding="utf-8")

    sizes = collections.Counter(len(s) for s in plans.values())
    print(f"tasks: {len(plans)}")
    print("subtasks per task:", dict(sorted(sizes.items())))
    total = sum(len(s) for s in plans.values())
    print(f"mean {total / len(plans):.2f}")
    print(f"\nwrote {args.out}")
    print(f"sha256 {hashlib.sha256(body.encode()).hexdigest()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
