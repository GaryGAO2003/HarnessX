"""Freeze a capability-profile cluster map for the GAIA bed.

Why this exists
---------------
``§4.5`` routes each task to ``argmax_v S_hat(v, cluster(task))`` but the paper
never defines ``cluster``. Our reconstruction used the GAIA difficulty level, so
there are exactly **three** clusters -- and routing is argmax *per cluster*, so
at most three variants can ever carry tasks. With ``K=8`` that leaves five
variants structurally idle: arithmetic, not dynamics. Measured on ``s1k8b103``:
Gini 0.22 (R4) -> 0.78 (R14), final load ``[52, 39, 12, 0, 0, 0, 0, 0]``.

This script builds a finer partition from the *capability profile* of a task --
the SET of D1-lite subtask types its decomposition needs. On the ten-task smoke
the set-of-types key gave six groups where the dominant-type key gave one (every
GAIA task is search-dominant), so the set is the only usable form.

Honesty properties that make this admissible
--------------------------------------------
* The label is computed from the task TEXT ONLY, before any attempt -- it cannot
  encode the outcome, so routing on it is not leakage.
* It is computed ONCE and frozen to disk with a digest, so every arm routes on
  byte-identical labels and the map is a lock-recordable artefact.
* It reuses the pipeline's own ``LlmDecomposer`` + ``DECOMPOSE_PROMPT_TEMPLATE``
  + ``validate_plan``, so labels agree with what the decomposition arms produce
  rather than coming from a parallel prompt that could drift.

Runs SERIALLY on purpose: a live evolution run is already saturating the
endpoint at concurrency 10, and this must not steal its rate-limit budget.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from experiments.variant_pool.subtask_pipeline import (  # noqa: E402
    DEFAULT_MAX_SUBTASKS,
    LlmDecomposer,
)
from recipe.gaia_evolver.run import _make_provider  # noqa: E402


def _task_text(rec: dict) -> str:
    for key in ("Question", "question", "task", "text", "prompt"):
        value = rec.get(key)
        if isinstance(value, str) and value.strip():
            return value
    raise KeyError(f"no question field in record with keys {sorted(rec)}")


def _task_id(rec: dict) -> str:
    return rec.get("task_id") or rec.get("id")


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data-path", required=True, help="GAIA JSON (webthinker schema).")
    ap.add_argument("--out", required=True, help="Where to write the frozen cluster map.")
    ap.add_argument("--model", default="deepseek/deepseek-v4-pro")
    ap.add_argument("--provider-id", default="deepseek")
    ap.add_argument("--api-base", default=None)
    ap.add_argument("--max-subtasks", type=int, default=DEFAULT_MAX_SUBTASKS)
    ap.add_argument("--limit", type=int, default=0, help="Debug: stop after N tasks.")
    args = ap.parse_args()

    records = json.loads(Path(args.data_path).read_text(encoding="utf-8"))
    if args.limit:
        records = records[: args.limit]
    print(f"tasks: {len(records)}  model: {args.model}  (serial, one call per task)")

    provider = _make_provider(
        args.model,
        args.provider_id,
        api_base=args.api_base or os.environ.get("DEEPSEEK_API_BASE"),
        api_key=os.environ.get("DEEPSEEK_API_KEY"),
    )

    async def complete(prompt: str) -> str:
        from harnessx.core.events import Message

        response = await provider.complete([Message(role="user", content=prompt)], [])
        return str(getattr(response, "content", "") or "")

    decomposer = LlmDecomposer(complete, max_subtasks=args.max_subtasks)

    clusters: dict[str, str] = {}
    plans: dict[str, list[dict]] = {}
    failures: list[str] = []

    for i, rec in enumerate(records, 1):
        tid = _task_id(rec)
        try:
            plan = await decomposer.decompose(tid, _task_text(rec))
            subtasks = list(getattr(plan, "subtasks", None) or plan)
            types = sorted({str(getattr(s, "type", None) or s["type"]) for s in subtasks})
            if not types:
                raise ValueError("plan carried no subtask types")
            clusters[tid] = "+".join(types)
            plans[tid] = [
                {
                    "id": str(getattr(s, "id", None) or s["id"]),
                    "type": str(getattr(s, "type", None) or s["type"]),
                }
                for s in subtasks
            ]
            print(f"  [{i:>3}/{len(records)}] {tid[:8]}  {clusters[tid]}")
        except Exception as exc:  # noqa: BLE001 - a failed label must not be invented
            failures.append(tid)
            print(f"  [{i:>3}/{len(records)}] {tid[:8]}  FAILED: {type(exc).__name__}: {exc}")

    payload = {
        "schema": "task_clusters/v1",
        "source": "d1lite_subtask_type_set",
        "model": args.model,
        "max_subtasks": args.max_subtasks,
        "data_path": str(args.data_path),
        "data_sha256": hashlib.sha256(Path(args.data_path).read_bytes()).hexdigest(),
        "clusters": clusters,
        "plans": plans,
        "failed_task_ids": failures,
    }
    body = json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True)
    digest = hashlib.sha256(body.encode()).hexdigest()
    Path(args.out).write_text(body, encoding="utf-8")

    counts = Counter(clusters.values())
    print(f"\n{'=' * 66}")
    print(f"labelled {len(clusters)}/{len(records)}   failed {len(failures)}")
    print(f"distinct clusters: {len(counts)}")
    for key, n in counts.most_common():
        print(f"  {n:>4}  {key}")
    sizes = sorted(counts.values(), reverse=True)
    if sizes:
        total = sum(sizes)
        gini = sum((2 * i - len(sizes) + 1) * x for i, x in enumerate(sorted(sizes))) / (
            len(sizes) * total
        )
        print(f"\nlargest cluster {sizes[0]}/{total} ({sizes[0] / total:.0%})   size-Gini {gini:.2f}")
        print(f"clusters with >= 8 tasks: {sum(1 for s in sizes if s >= 8)}")
    print(f"\nwrote {args.out}\nmap sha256 {digest}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
