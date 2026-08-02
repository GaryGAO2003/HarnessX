"""Build the frozen 50-task bed for the s2 pool re-evolution.

Why a smaller bed at all: ``s1k8b103`` cost 26h29m for 103 tasks x 16 rounds
(~1.76 h/round). The Aug-02 artefact fix invalidated that run's *selection
history* -- three variants were evaluated while silently running an empty
system prompt -- so the pool has to be re-evolved under honest signal. Pool
*depth* is bought with rounds, not with bed size, so the bed is cut and the
round count is kept.

What this costs, stated plainly: at n=50 the per-round noise floor widens from
the measured SD 4.57pp (n=103, B-FREEZE 4.1) to roughly 4.57 * sqrt(103/50)
~= 6.6pp. Gate decisions are therefore noisier than in s1k8b103 and this run's
accuracy curve is **not** comparable to it. It exists to produce a
differentiated pool, not an accuracy result.

Sampling rule (frozen 2026-08-02)
---------------------------------
source   : recipe/gaia_evolver/data/webthinker_gaia_dev.json  (GAIA-Text-103)
strata   : GAIA Level, proportional to the bed, rounded to sum to 50
             L1  39/103 * 50 = 18.93 -> 19
             L2  52/103 * 50 = 25.24 -> 25
             L3  12/103 * 50 =  5.83 ->  6
selection: within each stratum, sort by task_id (stable, independent of the
           source file's ordering), then random.Random(SEED).sample
seed     : 20260801  (same seed as build_smoke_set.py; the draws are
           independent because k differs)
output   : recipe/gaia_evolver/data/pool_bed50.json  (same schema as source)

Identical rule to build_smoke_set.py -- only the strata sizes differ.
"""

from __future__ import annotations

import json
import random
from collections import Counter
from pathlib import Path

SEED = 20260801
STRATA = {"1": 19, "2": 25, "3": 6}

REPO = Path(__file__).resolve().parents[1]
SOURCE = REPO / "recipe" / "gaia_evolver" / "data" / "webthinker_gaia_dev.json"
DEST = REPO / "recipe" / "gaia_evolver" / "data" / "pool_bed50.json"


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
    ids = sorted(t["task_id"] for t in picked)
    digest = __import__("hashlib").sha256("\n".join(ids).encode()).hexdigest()[:16]
    print(f"source : {SOURCE.name}")
    print(f"dest   : {DEST}")
    print(f"seed   : {SEED}")
    print(f"levels : {dict(sorted(counts.items()))}  total={len(picked)}")
    print(f"id digest (sha256 of sorted ids, first 16): {digest}")


if __name__ == "__main__":
    main()
