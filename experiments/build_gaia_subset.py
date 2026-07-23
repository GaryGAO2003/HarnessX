"""Build a fixed, reproducible GAIA task subset in webthinker JSON schema.

The upstream repo defaults to ``data/webthinker_gaia_dev.json`` but does not
ship it. This script materialises that file from the HuggingFace GAIA
validation split, restricted to text-only tasks (no attachments) so runs stay
platform-independent and cheap.

**Stratified by difficulty to match the paper.** HarnessX (arXiv 2606.14249)
Appendix A.2 p.28: "GAIA uses a fixed 103-task set drawn across the three
difficulty levels (39/52/12)". Taking tasks in task_id order would skew the
level mix and break comparability, so we sample per level to hit that ratio.

Deterministic: within each level, tasks are sorted by task_id and taken in
order. No RNG, no seed to track.

Usage:
    python experiments/build_gaia_subset.py                 # paper set: 39/52/12
    python experiments/build_gaia_subset.py --size 50       # same ratio, scaled
    python experiments/build_gaia_subset.py --size 0        # all text-only tasks
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))  # `benchmarks` is not an installed package

from benchmarks.gaia.task import load_gaia_tasks  # noqa: E402

DEFAULT_OUT = REPO_ROOT / "recipe/gaia_evolver/data/webthinker_gaia_dev.json"


# Paper's fixed set: HarnessX Appendix A.2 p.28 — 103 tasks total.
PAPER_QUOTA = {1: 39, 2: 52, 3: 12}


def stratified(text_only: list, size: int) -> list:
    """Take tasks per level, preserving the paper's 39/52/12 difficulty mix.

    ``size <= 0`` returns everything; ``size == 103`` reproduces the paper's
    quota exactly; other sizes scale the same ratio and top up any shortfall
    (a level can run out) from the remaining tasks, largest level first.
    """
    by_level: dict[int, list] = {}
    for t in text_only:
        by_level.setdefault(t.level, []).append(t)
    for lvl in by_level:
        by_level[lvl].sort(key=lambda t: t.task_id)  # deterministic

    if size <= 0:
        return [t for lvl in sorted(by_level) for t in by_level[lvl]]

    total = sum(PAPER_QUOTA.values())
    picked, leftovers = [], []
    for lvl, quota in sorted(PAPER_QUOTA.items()):
        want = round(quota * size / total)
        pool = by_level.get(lvl, [])
        picked.extend(pool[:want])
        leftovers.extend(pool[want:])
        if len(pool) < want:
            print(f"  warning: level {lvl} has {len(pool)} tasks, wanted {want}")

    # Rounding or an exhausted level can leave us short/over of `size`.
    if len(picked) < size:
        picked.extend(leftovers[: size - len(picked)])
    return picked[:size]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--size", type=int, default=103,
                    help="103 = paper's set (39/52/12); 0 = all text-only tasks")
    ap.add_argument("--split", default="validation")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = ap.parse_args()

    tasks = load_gaia_tasks(split=args.split)
    print(f"loaded {len(tasks)} tasks from HF split={args.split}")

    # Text-only: attachment tasks need file plumbing we don't need for M0.
    text_only = [t for t in tasks if not t.file_name]
    print(f"text-only: {len(text_only)}")

    subset = stratified(text_only, args.size)

    rows = [
        {
            "task_id": t.task_id,
            "Question": t.question,
            "answer": t.final_answer,
            "Level": t.level,
            "Annotator_Metadata": t.annotator_metadata or {},
        }
        for t in subset
    ]

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")

    levels = Counter(r["Level"] for r in rows)
    print(f"wrote {len(rows)} tasks -> {args.out}")
    print(f"by level: {dict(sorted(levels.items()))}")


if __name__ == "__main__":
    main()
