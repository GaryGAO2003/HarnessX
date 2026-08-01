"""Build the frozen 10-task smoke subset for the decomposition pipeline.

B-FREEZE.md §5 requires the sampling rule to be hard-coded here *before* the
smoke runs, and forbids changing it afterwards. Everything below is therefore
literal and seeded -- rerunning this script reproduces the identical subset.

Sampling rule (frozen 2026-08-01)
---------------------------------
source   : recipe/gaia_evolver/data/webthinker_gaia_dev.json  (GAIA-Text-103)
strata   : GAIA Level, proportional to the bed, rounded to sum to 10
             L1  39/103 * 10 = 3.79 -> 4
             L2  52/103 * 10 = 5.05 -> 5
             L3  12/103 * 10 = 1.17 -> 1
selection: within each stratum, sort by task_id (stable, independent of the
           source file's ordering), then random.Random(SEED).sample
seed     : 20260801
output   : recipe/gaia_evolver/data/smoke10.json  (same schema as the source)

The smoke measures pipeline behaviour, not accuracy, so the subset is not
held out from anything and must never be reported as an evaluation result.
"""

from __future__ import annotations

import json
import random
from collections import Counter
from pathlib import Path

SEED = 20260801
STRATA = {"1": 4, "2": 5, "3": 1}

REPO = Path(__file__).resolve().parents[1]
SOURCE = REPO / "recipe" / "gaia_evolver" / "data" / "webthinker_gaia_dev.json"
DEST = REPO / "recipe" / "gaia_evolver" / "data" / "smoke10.json"


def build() -> list[dict]:
    tasks = json.loads(SOURCE.read_text(encoding="utf-8"))
    picked: list[dict] = []
    for level, n in sorted(STRATA.items()):
        stratum = sorted((t for t in tasks if str(t["Level"]) == level),
                         key=lambda t: t["task_id"])
        if len(stratum) < n:
            raise SystemExit(f"level {level}: need {n}, bed has {len(stratum)}")
        # A fresh Random per stratum keeps each stratum's draw independent of
        # how many items the previous stratum consumed from the stream.
        picked.extend(random.Random(SEED).sample(stratum, n))
    return picked


def main() -> None:
    picked = build()
    DEST.write_text(json.dumps(picked, ensure_ascii=False, indent=2), encoding="utf-8")

    counts = Counter(str(t["Level"]) for t in picked)
    print(f"source : {SOURCE.name}  n={len(json.loads(SOURCE.read_text(encoding='utf-8')))}")
    print(f"dest   : {DEST}")
    print(f"seed   : {SEED}")
    print(f"levels : {dict(sorted(counts.items()))}  total={len(picked)}")
    print("tasks  :")
    for t in picked:
        q = t["Question"].replace("\n", " ")[:64]
        print(f"  L{t['Level']}  {t['task_id']}  {q}")


if __name__ == "__main__":
    main()
