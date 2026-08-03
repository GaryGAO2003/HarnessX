# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""K -- cross-run lock comparability (P1).

P1 says two runs cannot be compared directly, but says it as a verdict rather
than a procedure: nothing in the repo answers "given these two runs, what
exactly may I compare?". This does.

It diffs ``experiment.lock.json`` across runs and sorts every difference into
three kinds:

* **BLOCKING** -- the field sits in a section ``resume`` treats as identity
  (imported from :data:`resume._LOCK_BLOCKING_SECTIONS`, so this tool cannot
  drift from the engine). Any hit here means the two runs are different
  experiments and no direct score comparison is licensed.
* **SCHEMA** -- the key exists in one lock and not the other. This is *not* the
  same as a value difference and must not be reported as one: an absent key
  means the field predates that run, so nothing was captured either way. The
  ``reasoning_effort`` / ``meta_reasoning_effort`` pair between s1k8b103
  (2026-07-30) and e_pervar3 (2026-08-03) is exactly this case, and calling it
  "both are null" hides a provenance gap.
* **INFO** -- differs but does not block (timestamps, ids, warning lists).

Zero API cost: reads persisted lock files only.

Known gap, deliberately not fixed here: ``models.api_base`` records
``unresolved`` because ``run_variant_pool.py:6400`` only reads ``--api-base``
while the endpoint is actually resolvable from the environment the same way
``EnvSpec.deepseek_api_base`` resolves it (``:6491``). That is a one-line fix,
but ``models`` is a BLOCKING section, so landing it while a run is in flight
would make that run unresumable. Do it once no run is live.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "experiments"))

from _common import RUNS_ROOT, eprint, experiment_lock  # noqa: E402

try:  # keep in lockstep with the engine rather than restating the lists
    from variant_pool.resume import _LOCK_BLOCKING_SECTIONS, _LOCK_BLOCKING_TOP_FIELDS
except Exception:  # pragma: no cover - fall back only if the import path shifts
    _LOCK_BLOCKING_SECTIONS = ("h0", "models", "dataset", "hyperparams", "env")
    _LOCK_BLOCKING_TOP_FIELDS = ("provenance_warnings",)

MISSING = object()


def flatten(obj, prefix: str = "") -> dict[str, object]:
    out: dict[str, object] = {}
    if isinstance(obj, dict):
        for key, value in obj.items():
            out.update(flatten(value, f"{prefix}.{key}" if prefix else str(key)))
    elif isinstance(obj, list):
        out[prefix] = tuple(map(str, obj))
    else:
        out[prefix] = obj
    return out


def classify(path: str, left, right) -> str:
    if left is MISSING or right is MISSING:
        return "SCHEMA"
    root = path.split(".")[0]
    # ``provenance_warnings`` is a top-level *field*, not a section, but it blocks:
    # the frozen-Hyperparams taints (--regression-baseline, --force-gate,
    # --ship-policy, ...) are recorded there rather than as hyperparams fields, so
    # skipping it would classify the single most consequential difference between
    # s1k8b103 and e_pervar3 -- global vs per_variant regression baseline (M-23) --
    # as merely informational.
    if root in _LOCK_BLOCKING_SECTIONS or root in _LOCK_BLOCKING_TOP_FIELDS:
        return "BLOCKING"
    return "INFO"


def compare(run_a: str, run_b: str) -> dict[str, list]:
    a = flatten(experiment_lock(run_a))
    b = flatten(experiment_lock(run_b))
    buckets: dict[str, list] = {"BLOCKING": [], "SCHEMA": [], "INFO": []}
    for path in sorted(set(a) | set(b)):
        left = a.get(path, MISSING)
        right = b.get(path, MISSING)
        if left is not MISSING and right is not MISSING and left == right:
            continue
        buckets[classify(path, left, right)].append((path, left, right))
    return buckets


def show(value) -> str:
    if value is MISSING:
        return "<key absent>"
    text = str(value)
    return text if len(text) <= 46 else text[:43] + "..."


def main(argv: list[str]) -> int:
    runs = argv[1:]
    if len(runs) < 2:
        available = sorted(d.name for d in RUNS_ROOT.iterdir()
                           if (d / "experiment.lock.json").is_file())
        eprint("usage: k_lock_comparability.py <run_a> <run_b> [run_c ...]")
        eprint(f"runs with a lock: {', '.join(available)}")
        return 2

    base, *others = runs
    worst = 0
    for other in others:
        buckets = compare(base, other)
        eprint("")
        eprint("=" * 78)
        eprint(f"{base}  vs  {other}")
        eprint("=" * 78)
        for kind in ("BLOCKING", "SCHEMA", "INFO"):
            rows = buckets[kind]
            if not rows:
                continue
            eprint(f"\n[{kind}]  {len(rows)} field(s)")
            for path, left, right in rows:
                eprint(f"   {path}")
                eprint(f"      {base:>18s}: {show(left)}")
                eprint(f"      {other:>18s}: {show(right)}")

        n_block, n_schema = len(buckets["BLOCKING"]), len(buckets["SCHEMA"])
        eprint("")
        if n_block:
            worst = max(worst, 2)
            eprint(f"VERDICT: NOT directly comparable -- {n_block} blocking field(s). "
                   "Scores from these two runs may not be differenced.")
        elif n_schema:
            worst = max(worst, 1)
            eprint(f"VERDICT: comparable, but {n_schema} field(s) exist in only one lock. "
                   "Absent != null; state the gap rather than asserting equality.")
        else:
            eprint("VERDICT: comparable -- no blocking or schema differences.")
    return worst


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
