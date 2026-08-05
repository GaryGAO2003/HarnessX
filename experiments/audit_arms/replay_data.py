"""Module 3 -- independent read-only loader for gaia_evolver comparison.json.

Parses the comparison.json schema directly (no import of any branch analysis
script or experiments.variant_pool). Data is treated as strictly read-only.

comparison.json schema (relevant slice):
  {rounds: [ [ {round, variant_id, task_id, level,
                passed, exit_reason, infra_failures, primary_attempt, n_pass, n_att,
                attempts: [{passed, exit_reason, steps, infra_failure, reason}]} ] ]}

A "cell" is (run, round, variant, task). Loader flattens rounds into cell dicts,
optionally dropping infra-failure attempts.
"""

from __future__ import annotations

import glob
import hashlib
import json
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple


# --------------------------------------------------------------------------- #
# Manifest                                                                     #
# --------------------------------------------------------------------------- #
def sha1_8(path: str) -> str:
    """First 8 hex chars of the SHA-1 of a file's bytes."""
    h = hashlib.sha1()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:8]


def manifest(files: Sequence[str]) -> Dict[str, str]:
    """{path: sha1[:8]} over the given files (skips paths that cannot be read)."""
    out: Dict[str, str] = {}
    for p in files:
        try:
            out[p] = sha1_8(p)
        except OSError:
            out[p] = "MISSING"
    return out


# --------------------------------------------------------------------------- #
# Discovery                                                                    #
# --------------------------------------------------------------------------- #
def discover_run_files(runs_dir: str, runs: Optional[Sequence[str]] = None
                       ) -> List[Tuple[str, str]]:
    """Return [(run_name, comparison.json path)] for the requested runs.

    If `runs` is None, discover every <run>/comparison.json under runs_dir.
    """
    pairs: List[Tuple[str, str]] = []
    if runs is not None:
        for r in runs:
            p = os.path.join(runs_dir, r, "comparison.json")
            pairs.append((r, p))
        return pairs
    for p in sorted(glob.glob(os.path.join(runs_dir, "*", "comparison.json"))):
        pairs.append((os.path.basename(os.path.dirname(p)), p))
    return pairs


def _distinct_variants(rounds: Any) -> int:
    vids = set()
    if isinstance(rounds, list):
        for rnd in rounds:
            if isinstance(rnd, list):
                for rec in rnd:
                    vids.add(rec.get("variant_id"))
    return len(vids)


# --------------------------------------------------------------------------- #
# Cell extraction                                                             #
# --------------------------------------------------------------------------- #
def _cell_from_record(run: str, rec: Dict[str, Any], drop_infra: bool
                      ) -> Optional[Dict[str, Any]]:
    """Turn one comparison.json record into a cell dict, applying drop_infra.

    Returns None if, after dropping infra-failure attempts, nothing usable
    remains for this cell.
    """
    raw_attempts = rec.get("attempts") or []
    attempts: List[Dict[str, Any]] = []
    if raw_attempts:
        for a in raw_attempts:
            attempts.append({
                "passed": bool(a.get("passed")),
                "exit_reason": a.get("exit_reason"),
                "steps": a.get("steps"),
                "infra_failure": bool(a.get("infra_failure")),
                "reason": a.get("reason"),
            })
    else:
        # Some runs record no per-attempt list; synthesise one from record fields.
        attempts.append({
            "passed": bool(rec.get("passed")),
            "exit_reason": rec.get("exit_reason"),
            "steps": rec.get("steps"),
            "infra_failure": bool(rec.get("infra_failures", 0)),
            "reason": rec.get("reason"),
        })

    kept = [a for a in attempts if not a["infra_failure"]] if drop_infra else attempts
    if not kept:
        return None

    n_att = len(kept)
    n_pass = sum(1 for a in kept if a["passed"])
    # cell-level pass: best-of the kept attempts (matches "cell solved" semantics).
    passed = n_pass > 0
    return {
        "run": run,
        "round": rec.get("round"),
        "variant": rec.get("variant_id"),
        "task": rec.get("task_id"),
        "level": rec.get("level"),
        "passed": passed,
        "n_pass": n_pass,
        "n_att": n_att,
        "exit_reason": rec.get("exit_reason"),
        "infra_failure": bool(rec.get("infra_failures", 0)),
        "attempts": kept,
    }


@dataclass
class LoadReport:
    cells: List[Dict[str, Any]] = field(default_factory=list)
    used_runs: List[str] = field(default_factory=list)
    skipped: List[Tuple[str, str]] = field(default_factory=list)  # (run, reason)
    files: List[str] = field(default_factory=list)                # used files


def load_report(runs_dir: str, runs: Optional[Sequence[str]] = None,
                min_variants: Optional[int] = None, drop_infra: bool = True
                ) -> LoadReport:
    """Full loader: returns cells plus provenance (used runs, skipped, files)."""
    rep = LoadReport()
    for run, path in discover_run_files(runs_dir, runs):
        if not os.path.exists(path):
            rep.skipped.append((run, "missing comparison.json"))
            continue
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (json.JSONDecodeError, OSError, UnicodeDecodeError) as exc:
            rep.skipped.append((run, f"malformed: {type(exc).__name__}: {exc}"))
            continue
        rounds = data.get("rounds") if isinstance(data, dict) else None
        if not isinstance(rounds, list):
            rep.skipped.append((run, "no 'rounds' list"))
            continue
        if min_variants is not None and _distinct_variants(rounds) < min_variants:
            rep.skipped.append((run, f"variants<{min_variants}"))
            continue
        n_before = len(rep.cells)
        for rnd in rounds:
            if not isinstance(rnd, list):
                continue
            for rec in rnd:
                if not isinstance(rec, dict):
                    continue
                cell = _cell_from_record(run, rec, drop_infra)
                if cell is not None:
                    rep.cells.append(cell)
        if len(rep.cells) > n_before:
            rep.used_runs.append(run)
            rep.files.append(path)
        else:
            rep.skipped.append((run, "no usable cells after filtering"))
    return rep


def load_cells(runs_dir: str, runs: Optional[Sequence[str]] = None,
               min_variants: Optional[int] = None, drop_infra: bool = True
               ) -> List[Dict[str, Any]]:
    """Convenience wrapper returning just the flat list of cell dicts."""
    return load_report(runs_dir, runs, min_variants, drop_infra).cells
