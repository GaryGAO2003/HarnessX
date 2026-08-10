"""Build the rehearsal holdout set — stratified, disjoint from every bed file.

The nine-process-metrics ``holdout_regression_rate`` needs a task set the
lineage never evolved on.  This builder samples 2×L1 + 3×L2 + 1×L3 (the
calib6 ratio, ≈ the paper's 39/52/12 stratification) from
``webthinker_gaia_dev.json``, EXCLUDING every task id already present in any
bed file (calib6 ⊂ pilot12, smoke10) — evolution beds and the holdout must
stay disjoint or the regression rate is circular.

Deterministic: candidates are sorted by task_id and sampled with a fixed
seed, so re-running reproduces the same file byte-for-byte.

Usage::

    python -m experiments.build_holdout_set   # writes data/holdout6.json
"""

from __future__ import annotations

import json
import random
from pathlib import Path

DATA = Path("recipe/gaia_evolver/data")
POOL = DATA / "webthinker_gaia_dev.json"
EXCLUDE_FILES = ("calib6.json", "pilot12.json", "smoke10.json")
OUT = DATA / "holdout6.json"
PER_LEVEL = {"1": 2, "2": 3, "3": 1}
SEED = 0


def build() -> list[dict]:
    pool = json.loads(POOL.read_text(encoding="utf-8"))
    used: set[str] = set()
    for name in EXCLUDE_FILES:
        used |= {t["task_id"] for t in
                 json.loads((DATA / name).read_text(encoding="utf-8"))}

    rng = random.Random(SEED)
    picked: list[dict] = []
    for level, k in PER_LEVEL.items():
        cands = sorted(
            (t for t in pool
             if str(t.get("Level")) == level and t["task_id"] not in used),
            key=lambda t: t["task_id"],
        )
        if len(cands) < k:
            raise SystemExit(f"level {level}: only {len(cands)} unused tasks, need {k}")
        picked.extend(rng.sample(cands, k))
    return picked


def main() -> int:
    tasks = build()
    OUT.write_text(json.dumps(tasks, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    lv = {}
    for t in tasks:
        lv[str(t.get("Level"))] = lv.get(str(t.get("Level")), 0) + 1
    print(f"wrote {OUT} n={len(tasks)} levels={dict(sorted(lv.items()))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
