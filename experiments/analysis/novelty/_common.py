# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""Shared read-only helpers for the offline novelty analyses (A-E).

Zero API cost: every function here only *reads* already-persisted run
artefacts under ``recipe/gaia_evolver/runs/<run>/``. Nothing in this package
imports, mutates, or re-invokes the runner (``run_variant_pool.py``) or any
module under ``experiments/variant_pool/`` -- the analyses are pure post-hoc
readers of ``pool_state.json`` / ``pipeline_audit.json`` / ``pool_report.json``
/ the dataset json.

Field semantics used here were verified against the code and the data before
use (see ``experiments/docs/novelty/05-OFFLINE-RESULTS.md`` for the audit):

* ``active_pool_measurements[variant][task] = [n_pass, n_att]`` is a **per-round
  pass@2 snapshot** (``n_att == pass_k`` every round, never cumulative) -- the
  settled deployed-pool score, source flagged in ``active_score_source``.
* ``candidate_gate_measurements[variant][task] = [n_pass, n_att]`` is the
  candidate gate rollout (``per_variant_pass``, scope ``candidate_gate``): the
  *target* variant's shipped-candidate rollout on its evaluated tasks.
* the gate's internal ``before`` is binarised to ``(pass_k, pass_k)`` /
  ``(0, pass_k)`` (engine ``_task_eval``), which is *why* the any-k seesaw is
  blind to a ``2/2 -> 1/2`` soft regression -- see analysis B.
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
RUNS_ROOT = REPO_ROOT / "recipe" / "gaia_evolver" / "runs"
DEFAULT_RUN = "s1k8b103"
PASS_K = 2  # pass@2 throughout these runs (run_config.pass_k / lock.hyperparams)


def load_json(path: Path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def run_path(run: str) -> Path:
    p = RUNS_ROOT / run
    if not p.is_dir():
        raise FileNotFoundError(f"run directory not found: {p}")
    return p


def round_indices(run: str) -> list[int]:
    """Round indices that have a persisted pool_state.json.

    An incomplete trailing round (e.g. e_pervar3 R7, which has no pool_state.json)
    is skipped so the analyses read only settled rounds.
    """
    root = run_path(run)
    idx = [
        int(d.name[1:])
        for d in root.iterdir()
        if d.is_dir()
        and d.name.startswith("R")
        and d.name[1:].isdigit()
        and (d / "pool_state.json").is_file()
    ]
    return sorted(idx)


def pool_state(run: str, round_idx: int) -> dict:
    return load_json(run_path(run) / f"R{round_idx}" / "pool_state.json")


def all_states(run: str) -> dict[int, dict]:
    return {r: pool_state(run, r) for r in round_indices(run)}


def pool_report(run: str) -> dict:
    return load_json(run_path(run) / "pool_report.json")


def experiment_lock(run: str) -> dict:
    p = run_path(run) / "experiment.lock.json"
    return load_json(p) if p.exists() else {}


def run_config(run: str) -> dict:
    """Merge comparison.run_config + lock.hyperparams (whichever is present)."""
    cfg: dict = {}
    comp = run_path(run) / "comparison.json"
    if comp.exists():
        cfg.update(load_json(comp).get("run_config", {}) or {})
    hp = experiment_lock(run).get("hyperparams", {}) or {}
    for key in ("cluster_source", "cluster_mode", "routing_mode"):
        cfg.setdefault(key, hp.get(key))
    return cfg


def level_map(run: str) -> dict[str, str]:
    """task_id -> GAIA level (as str). The clustering key when
    ``cluster_source == gaia_level`` (3 clusters: levels 1/2/3).

    Read from the pinned dataset json named in ``experiment.lock.json``
    (``dataset.path``, repo-root relative); falls back to the standard path.
    """
    lock = experiment_lock(run)
    rel = (lock.get("dataset", {}) or {}).get("path")
    candidates = []
    if rel:
        candidates.append(REPO_ROOT / rel.replace("\\", "/"))
    candidates.append(REPO_ROOT / "recipe" / "gaia_evolver" / "data" / "webthinker_gaia_dev.json")
    for path in candidates:
        if path.exists():
            data = load_json(path)
            rows = data if isinstance(data, list) else list(data.values())
            out: dict[str, str] = {}
            for row in rows:
                tid = row.get("task_id") or row.get("id")
                lvl = row.get("Level", row.get("level"))
                if tid is not None and lvl is not None:
                    out[str(tid)] = str(lvl)
            return out
    raise FileNotFoundError(f"dataset for level map not found for run {run!r}")


def carriers(state: dict) -> dict[str, int]:
    """variant_id -> number of tasks routed to it this round."""
    return {vid: len(tasks) for vid, tasks in state.get("routing", {}).items()}


def pool_task_scores(state: dict) -> dict[str, tuple[int, int]]:
    """task_id -> (n_pass, n_att) merged across all carriers this round.

    Routing partitions the task set, so each task is measured by exactly one
    carrier; the merge is the settled deployed-pool score per task for the round.
    """
    out: dict[str, tuple[int, int]] = {}
    for _vid, tasks in state.get("active_pool_measurements", {}).items():
        for tid, sa in tasks.items():
            out[tid] = (int(sa[0]), int(sa[1]))
    return out


def ship_rounds(states: dict[int, dict]) -> list[int]:
    return [r for r, s in sorted(states.items()) if s.get("shipped")]


def drought_rounds(states: dict[int, dict]) -> list[int]:
    return [r for r, s in sorted(states.items()) if s.get("no_candidate")]


def find_pipeline_audit(run: str, round_idx: int) -> tuple[str, dict] | None:
    """Return (variant_dir, audit_dict) for the round's pipeline_audit.json, if any.

    Drought rounds have none (the pipeline never reached the audit-writing
    point); those return ``None``.
    """
    rdir = run_path(run) / f"R{round_idx}"
    if not rdir.is_dir():
        return None
    for sub in sorted(rdir.iterdir()):
        audit = sub / "pipeline_audit.json"
        if audit.is_file():
            return sub.name, load_json(audit)
    return None


def mean(values) -> float | None:
    values = list(values)
    return sum(values) / len(values) if values else None


def cumulative_cluster_counts(
    states: dict[int, dict], upto_round: int, levels: dict[str, str], measurement: str = "active_pool_measurements"
) -> dict[tuple[str, str], list[int]]:
    """Sum (pass, att) per (variant, level) over rounds 0..upto_round inclusive.

    Mirrors ``SuccessLedger.estimate_cluster`` aggregation basis (sum passes and
    attempts across the cluster's tasks, then smooth once). Reconstructed from
    the per-round settled ``active_pool_measurements`` snapshots.
    """
    agg: dict[tuple[str, str], list[int]] = defaultdict(lambda: [0, 0])
    for r in range(0, upto_round + 1):
        state = states.get(r)
        if not state:
            continue
        for vid, tasks in state.get(measurement, {}).items():
            for tid, (np_, na_) in tasks.items():
                lvl = levels.get(tid)
                if lvl is None:
                    continue
                cell = agg[(vid, lvl)]
                cell[0] += int(np_)
                cell[1] += int(na_)
    return agg


def eprint(*args, **kwargs):
    print(*args, file=sys.stdout, **kwargs)
