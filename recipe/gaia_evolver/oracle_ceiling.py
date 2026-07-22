# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""W0 — offline oracle-ceiling aggregation for the variant-pool lab (M0).

This is a *post-hoc, offline* analysis. It reads the fallen-out data of one or
more already-completed single-lineage evolver runs and computes the performance
ceiling of a hypothetical **perfect router** — the pass rate you would reach if,
for every task, an oracle picked the one variant that happens to solve it.

The M0 decision (`experiments/README.md`, `docs/HARNESSX-IMPL-CHECKLIST.md`):

    headroom = oracle-union pass_rate − best single-variant pass_rate

If headroom ≈ 0, a variant pool / router cannot beat the best single lineage and
the whole online-routing effort (M1) is not worth building. If headroom is large,
routing has room to win and M1 is justified.

Terminology
-----------
* **lineage**  — one evolver run directory, i.e. one ``comparison.json``. Each
  lineage is a single-lineage MetaHarness hill-climb (upstream's "Global" arm).
* **variant**  — one *(lineage, round)* checkpoint. Round ``R{j}`` of a lineage
  is the config compiled by that round; its executed config is dumped at
  ``{run_dir}/R{j}/config.yaml`` (see ``run.py:707-708``) and its per-task
  outcomes are the ``j``-th inner list of ``comparison.json``'s ``rounds`` field
  (written at ``run.py:978``). So K lineages × R rounds = K·R variants.

Input schema (``comparison.json``, produced by ``recipe/gaia_evolver/run.py``)
------------------------------------------------------------------------------
Top level is a dict with three keys; we only read ``rounds``::

    {
      "rounds": [                     # one inner list per round (run.py:982)
        [ {record}, {record}, ... ],  # round 0
        ...
      ],
      "round_summaries": [...],
      "run_config": {...}
    }

Each ``record`` on the success path (``run.py:198-215``) carries at least::

    {"task_id": str, "level": int, "passed": bool, "cost_usd": float,
     "total_tokens": int, "steps": int, ...}

The error path (``run.py:239-252``) omits ``cost_usd``/``total_tokens``/``steps``
and always sets ``passed = False``; this script defends against those absences.

Everything here is pure stdlib, offline, and side-effect free apart from the
optional ``--out`` JSON dump.
"""

from __future__ import annotations

import argparse
import json
import logging
import statistics
import sys
from dataclasses import dataclass, field
from itertools import combinations
from pathlib import Path
from typing import Any

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-5s %(name)s: %(message)s",
)
logger = logging.getLogger("oracle_ceiling")

# Print the full pairwise Jaccard matrix only when the variant count is small
# enough for it to stay readable; above this we emit summary stats only.
_MAX_MATRIX_VARIANTS = 8


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Variant:
    """One *(lineage, round)* checkpoint's solved-task footprint.

    ``solved`` and ``appeared`` are frozensets of ``task_id`` strings.
    ``solved`` ⊆ ``appeared`` — a task is "solved" iff a record for it exists
    with ``passed == True``. Any universe task *not* in ``appeared`` is treated
    as unsolved by this variant ("缺席任务视为未解").
    """

    variant_id: str
    lineage_label: str
    lineage_idx: int
    round_idx: int  # original round number within the lineage
    solved: frozenset[str]
    appeared: frozenset[str]
    cost_usd: float
    is_last_round: bool


@dataclass
class Lineage:
    """One run directory's rounds after ``--last-k`` slicing."""

    label: str
    run_dir: Path
    rounds_total: int
    variants: list[Variant] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Loading + validation
# ---------------------------------------------------------------------------


def _fail(msg: str) -> "SystemExit":
    """Return a SystemExit carrying a clear, prefixed error message."""
    return SystemExit(f"oracle_ceiling: error: {msg}")


def load_comparison(run_dir: Path) -> list[list[dict]]:
    """Read ``{run_dir}/comparison.json`` and return its ``rounds`` list.

    Raises ``SystemExit`` (not a silent skip) on a missing directory, missing
    file, unparseable JSON, or a structurally-invalid ``rounds`` field.
    """
    if not run_dir.exists():
        raise _fail(f"run directory does not exist: {run_dir}")
    if not run_dir.is_dir():
        raise _fail(f"not a directory: {run_dir}")
    cmp_path = run_dir / "comparison.json"
    if not cmp_path.is_file():
        raise _fail(f"no comparison.json in {run_dir}")
    try:
        data = json.loads(cmp_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise _fail(f"could not parse {cmp_path}: {exc}") from exc
    if not isinstance(data, dict) or "rounds" not in data:
        raise _fail(f"{cmp_path} has no top-level 'rounds' key")
    rounds = data["rounds"]
    if not isinstance(rounds, list):
        raise _fail(f"{cmp_path} 'rounds' is not a list")
    if not rounds:
        raise _fail(f"{cmp_path} has an empty 'rounds' list — no variants to analyse")
    for j, rd in enumerate(rounds):
        if not isinstance(rd, list):
            raise _fail(f"{cmp_path} rounds[{j}] is not a list of records")
    return rounds


def _solved_and_appeared(
    records: list[dict],
    warnings: list[str],
    where: str,
) -> tuple[set[str], set[str]]:
    """Return (solved, appeared) task-id sets for one round's records.

    Records with a missing/blank ``task_id`` cannot be attributed and are
    skipped with a warning. A ``task_id`` that appears more than once in a
    single round is OR-folded on ``passed`` (defensive; run.py writes one
    record per task) and warned about.
    """
    solved: set[str] = set()
    appeared: set[str] = set()
    seen: set[str] = set()
    for rec in records:
        if not isinstance(rec, dict):
            _warn(warnings, f"{where}: skipping non-dict record {rec!r}")
            continue
        tid = rec.get("task_id")
        if not tid or not isinstance(tid, str):
            _warn(warnings, f"{where}: record with missing/blank task_id skipped")
            continue
        if tid in seen:
            _warn(warnings, f"{where}: duplicate task_id {tid!r} in one round (OR-folding passed)")
        seen.add(tid)
        appeared.add(tid)
        if bool(rec.get("passed")):
            solved.add(tid)
    return solved, appeared


def _round_cost(records: list[dict]) -> float:
    """Sum ``cost_usd`` over a round; missing on the error path (run.py:239)."""
    return sum(float(r.get("cost_usd") or 0.0) for r in records if isinstance(r, dict))


def _dedupe_labels(names: list[str]) -> list[str]:
    """Make lineage labels unique by appending ``#i`` to any collisions."""
    counts: dict[str, int] = {}
    for n in names:
        counts[n] = counts.get(n, 0) + 1
    seen: dict[str, int] = {}
    out: list[str] = []
    for n in names:
        if counts[n] == 1:
            out.append(n)
        else:
            k = seen.get(n, 0)
            seen[n] = k + 1
            out.append(f"{n}#{k}")
    return out


def build_lineages(
    run_dirs: list[Path],
    last_k: int | None,
    warnings: list[str],
) -> list[Lineage]:
    """Load each run dir and materialise its variants after ``--last-k`` slicing."""
    labels = _dedupe_labels([d.name or str(d) for d in run_dirs])
    lineages: list[Lineage] = []
    for lin_idx, (run_dir, label) in enumerate(zip(run_dirs, labels)):
        rounds = load_comparison(run_dir)
        rounds_total = len(rounds)
        # Slice to the last K rounds; original round numbers are preserved in
        # the variant labels so "R4" still means round 4 of the full run.
        if last_k is not None and last_k > 0:
            if last_k > rounds_total:
                _warn(
                    warnings,
                    f"{label}: --last-k {last_k} > {rounds_total} rounds available; using all",
                )
            start = max(0, rounds_total - last_k)
        else:
            start = 0
        sliced = list(enumerate(rounds))[start:]
        lineage = Lineage(label=label, run_dir=run_dir, rounds_total=rounds_total)
        n_used = len(sliced)
        for local_i, (orig_round, records) in enumerate(sliced):
            where = f"{label}/R{orig_round}"
            if not records:
                _warn(warnings, f"{where}: empty round (no records) — skipped")
                continue
            solved, appeared = _solved_and_appeared(records, warnings, where)
            if not appeared:
                _warn(warnings, f"{where}: no attributable tasks — skipped")
                continue
            variant = Variant(
                variant_id=f"{label}/R{orig_round}",
                lineage_label=label,
                lineage_idx=lin_idx,
                round_idx=orig_round,
                solved=frozenset(solved),
                appeared=frozenset(appeared),
                cost_usd=_round_cost(records),
                is_last_round=(local_i == n_used - 1),
            )
            lineage.variants.append(variant)
        if not lineage.variants:
            _warn(warnings, f"{label}: no usable variants after filtering")
        lineages.append(lineage)
    return lineages


def build_level_map(
    run_dirs_rounds: list[list[list[dict]]],
    tasks_path: str | None,
    warnings: list[str],
) -> dict[str, int]:
    """Map ``task_id -> level``.

    Primary source is the per-record ``level`` field, which every record
    carries (``run.py:200``), so level bucketing works out of the box. If
    ``--tasks`` is given, the GAIA task JSON (webthinker/HF schema, capitalised
    ``"Level"`` key — see ``task.py:286-348``) *overrides* the record levels and
    fills any gaps. Conflicting levels for the same ``task_id`` are warned about.

    NOTE (deviation from the literal spec): the spec scoped level bucketing to
    the ``--tasks`` path only. Because the fallen-out records already carry an
    authoritative ``level``, this uses them as the default source so bucketing is
    available without ``--tasks``; ``--tasks`` remains supported as an override.
    """
    level_map: dict[str, int] = {}

    def _set(tid: str, lvl: int, src: str) -> None:
        if tid in level_map and level_map[tid] != lvl:
            _warn(
                warnings,
                f"level conflict for {tid!r}: {level_map[tid]} vs {lvl} ({src} wins)",
            )
        level_map[tid] = lvl

    # Records first (lower priority than --tasks).
    for rounds in run_dirs_rounds:
        for rd in rounds:
            for rec in rd:
                if not isinstance(rec, dict):
                    continue
                tid = rec.get("task_id")
                lvl = rec.get("level")
                if isinstance(tid, str) and tid and isinstance(lvl, int):
                    if tid not in level_map:
                        level_map[tid] = lvl

    # --tasks overrides / supplements.
    if tasks_path:
        p = Path(tasks_path)
        if not p.is_file():
            raise _fail(f"--tasks file not found: {p}")
        try:
            rows = json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise _fail(f"could not parse --tasks {p}: {exc}") from exc
        if not isinstance(rows, list):
            raise _fail(f"--tasks {p} is not a JSON list of task objects")
        found = 0
        for raw in rows:
            if not isinstance(raw, dict):
                continue
            tid = raw.get("task_id") or raw.get("id")
            lvl = raw.get("Level", raw.get("level"))
            if isinstance(tid, str) and tid and lvl is not None:
                try:
                    _set(tid, int(lvl), "--tasks")
                    found += 1
                except (TypeError, ValueError):
                    _warn(warnings, f"--tasks: non-integer level for {tid!r}: {lvl!r}")
        if found == 0:
            _warn(warnings, f"--tasks {p}: no usable task_id/level pairs found")

    return level_map


# ---------------------------------------------------------------------------
# Core computation
# ---------------------------------------------------------------------------


def _warn(warnings: list[str], msg: str) -> None:
    logger.warning(msg)
    warnings.append(msg)


def _pass_rate(n_solved: int, n_total: int) -> float:
    return (n_solved / n_total) if n_total else 0.0


def best_single_variant(
    variants: list[Variant],
    universe: frozenset[str],
    restrict: frozenset[str] | None = None,
) -> tuple[Variant | None, int]:
    """Pick the variant solving the most tasks (within ``restrict`` if given).

    Tie-break mirrors ``run.py``'s best-round selection (``run.py:287-290``):
    prefer more solved, then lower cost, then earlier variant index.
    Returns ``(variant, n_solved)``; ``(None, 0)`` if there are no variants.
    """
    if not variants:
        return None, 0
    scope = restrict if restrict is not None else universe

    def _score(iv: tuple[int, Variant]) -> tuple[int, float, int]:
        idx, v = iv
        n = len(v.solved & scope)
        return (-n, v.cost_usd, idx)

    idx, best = min(enumerate(variants), key=_score)
    return best, len(best.solved & scope)


def oracle_union(variants: list[Variant], restrict: frozenset[str] | None = None) -> frozenset[str]:
    """Union of all variants' solved sets (a perfect per-task router)."""
    u: set[str] = set()
    for v in variants:
        u |= v.solved
    if restrict is not None:
        u &= restrict
    return frozenset(u)


def jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    """Jaccard similarity of two solved sets.

    Convention: two empty solved sets have similarity 0.0 (they carry no
    overlap evidence and, for a diversity read, contribute no signal).
    """
    if not a and not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


def compute_ceiling(
    lineages: list[Lineage],
    level_map: dict[str, int],
    warnings: list[str],
) -> dict[str, Any]:
    """Aggregate everything the report and JSON need."""
    variants: list[Variant] = [v for lin in lineages for v in lin.variants]
    if not variants:
        raise _fail("no usable variants across all run directories")

    # Universe = union of every task any variant attempted ("全任务并集").
    universe: frozenset[str] = frozenset().union(*(v.appeared for v in variants))
    n_tasks = len(universe)
    if n_tasks == 0:
        raise _fail("no tasks appeared in any variant")

    # Warn on cross-lineage task-set inconsistency.
    _warn_task_set_inconsistency(variants, universe, warnings)

    # ── best single variant + oracle union (over full universe) ──────────────
    best_v, best_n = best_single_variant(variants, universe)
    best_rate = _pass_rate(best_n, n_tasks)

    oracle = oracle_union(variants)
    oracle_n = len(oracle)
    oracle_rate = _pass_rate(oracle_n, n_tasks)

    headroom_abs = oracle_rate - best_rate
    headroom_rel = (headroom_abs / best_rate) if best_rate > 0 else None

    # ── last-round-only union (distinct from all-round oracle) ───────────────
    last_round_variants = [v for v in variants if v.is_last_round]
    lr_union = oracle_union(last_round_variants) if last_round_variants else frozenset()
    lr_n = len(lr_union)
    lr_rate = _pass_rate(lr_n, n_tasks)

    # ── coverage histogram: #variants solving each task ──────────────────────
    coverage: dict[str, int] = {t: 0 for t in universe}
    for v in variants:
        for t in v.solved:
            coverage[t] += 1
    n_variants = len(variants)
    histogram = {k: 0 for k in range(n_variants + 1)}
    for c in coverage.values():
        histogram[c] += 1
    unsolvable = sorted(t for t, c in coverage.items() if c == 0)
    uniquely_solved = histogram.get(1, 0)
    solved_by_all = histogram.get(n_variants, 0)

    # ── pairwise Jaccard overlap ─────────────────────────────────────────────
    overlap = _overlap_stats(variants)

    # ── per-level buckets ────────────────────────────────────────────────────
    levels = _level_buckets(variants, universe, level_map, warnings)

    return {
        "n_lineages": len(lineages),
        "n_variants": n_variants,
        "n_tasks": n_tasks,
        "lineages": [
            {
                "label": lin.label,
                "run_dir": str(lin.run_dir),
                "rounds_total": lin.rounds_total,
                "variants_used": len(lin.variants),
            }
            for lin in lineages
        ],
        "variants": [
            {
                "id": v.variant_id,
                "lineage": v.lineage_label,
                "round": v.round_idx,
                "n_solved": len(v.solved),
                "n_appeared": len(v.appeared),
                "pass_rate": _pass_rate(len(v.solved), n_tasks),
                "cost_usd": round(v.cost_usd, 4),
                "is_last_round": v.is_last_round,
            }
            for v in variants
        ],
        "best_single": {
            "id": best_v.variant_id if best_v else None,
            "n_solved": best_n,
            "pass_rate": best_rate,
        },
        "oracle": {
            "n_solved": oracle_n,
            "pass_rate": oracle_rate,
            "solved_task_ids": sorted(oracle),
        },
        "headroom": {
            "abs_pp": headroom_abs * 100.0,
            "rel": headroom_rel,
        },
        "last_round_union": {
            "n_variants": len(last_round_variants),
            "n_solved": lr_n,
            "pass_rate": lr_rate,
            "headroom_abs_pp_vs_best": (lr_rate - best_rate) * 100.0,
            "gap_abs_pp_below_oracle": (oracle_rate - lr_rate) * 100.0,
        },
        "coverage_histogram": {str(k): histogram[k] for k in sorted(histogram)},
        "unsolvable_task_ids": unsolvable,
        "n_unsolvable": len(unsolvable),
        "n_uniquely_solved": uniquely_solved,
        "n_solved_by_all": solved_by_all,
        "overlap": overlap,
        "levels": levels,
        "warnings": warnings,
    }


def _warn_task_set_inconsistency(
    variants: list[Variant],
    universe: frozenset[str],
    warnings: list[str],
) -> None:
    """Warn if variants attempted different task sets (absent = unsolved).

    Triggers on membership, not just cardinality: two variants of equal size
    that attempted *different* tasks are still inconsistent.
    """
    if any(v.appeared != universe for v in variants):
        missing_examples = []
        for v in variants:
            miss = universe - v.appeared
            if miss:
                missing_examples.append(f"{v.variant_id} missing {len(miss)}")
        _warn(
            warnings,
            "variants attempted different task sets (absent tasks counted unsolved): "
            + "; ".join(missing_examples[:6])
            + (" ..." if len(missing_examples) > 6 else ""),
        )


def _overlap_stats(variants: list[Variant]) -> dict[str, Any]:
    """Pairwise Jaccard summary; full matrix only when few variants."""
    n = len(variants)
    pairs = list(combinations(range(n), 2))
    if not pairs:
        return {"n_pairs": 0}
    vals = [jaccard(variants[i].solved, variants[j].solved) for i, j in pairs]
    stats = {
        "n_pairs": len(pairs),
        "jaccard_min": min(vals),
        "jaccard_median": statistics.median(vals),
        "jaccard_mean": statistics.fmean(vals),
        "jaccard_max": max(vals),
    }
    if n <= _MAX_MATRIX_VARIANTS:
        matrix = {}
        for (i, j), v in zip(pairs, vals):
            key = f"{variants[i].variant_id} | {variants[j].variant_id}"
            matrix[key] = round(v, 4)
        stats["matrix"] = matrix
    return stats


def _level_buckets(
    variants: list[Variant],
    universe: frozenset[str],
    level_map: dict[str, int],
    warnings: list[str],
) -> dict[str, Any] | None:
    """Per-level headroom. Returns None when no level info is available."""
    if not level_map:
        return None
    # Partition the universe by level.
    by_level: dict[int, set[str]] = {}
    n_unknown = 0
    for t in universe:
        lvl = level_map.get(t)
        if lvl is None:
            n_unknown += 1
            continue
        by_level.setdefault(lvl, set()).add(t)
    if not by_level:
        _warn(warnings, "level map covered none of the universe tasks — skipping buckets")
        return None
    if n_unknown:
        _warn(warnings, f"{n_unknown} universe task(s) have no known level — excluded from buckets")

    out: dict[str, Any] = {"n_tasks_without_level": n_unknown, "buckets": {}}
    for lvl in sorted(by_level):
        bucket = frozenset(by_level[lvl])
        n_bucket = len(bucket)
        best_v, best_n = best_single_variant(variants, universe, restrict=bucket)
        best_rate = _pass_rate(best_n, n_bucket)
        orc = oracle_union(variants, restrict=bucket)
        orc_rate = _pass_rate(len(orc), n_bucket)
        out["buckets"][str(lvl)] = {
            "n_tasks": n_bucket,
            "best_single": {
                "id": best_v.variant_id if best_v else None,
                "n_solved": best_n,
                "pass_rate": best_rate,
            },
            "oracle": {"n_solved": len(orc), "pass_rate": orc_rate},
            "headroom_abs_pp": (orc_rate - best_rate) * 100.0,
        }
    return out


# ---------------------------------------------------------------------------
# Human-readable report
# ---------------------------------------------------------------------------


def _pct(frac: float) -> str:
    return f"{100 * frac:.1f}%"


def format_report(result: dict[str, Any]) -> str:
    """Render the aggregate as an aligned, plain-text report for stdout."""
    lines: list[str] = []
    n_lin = result["n_lineages"]
    n_var = result["n_variants"]
    n_tasks = result["n_tasks"]
    lin_word = "lineage" if n_lin == 1 else "lineages"
    var_word = "variant" if n_var == 1 else "variants"
    task_word = "task" if n_tasks == 1 else "tasks"
    lines.append(f"Oracle Ceiling: {n_lin} {lin_word} x {n_var} {var_word} over {n_tasks} {task_word} (union)")
    lines.append("")

    # ── Lineages ─────────────────────────────────────────────────────────────
    lines.append("Lineages")
    for lin in result["lineages"]:
        lines.append(
            f"  {lin['label']:<16} rounds_total={lin['rounds_total']:<3} "
            f"variants_used={lin['variants_used']:<3} ({lin['run_dir']})"
        )
    lines.append("")

    # ── Per-variant table ────────────────────────────────────────────────────
    ID_W = max(12, max(len(v["id"]) for v in result["variants"]))
    lines.append("Per-variant pass rate (denominator = full task union)")
    lines.append(f"  {'variant':<{ID_W}} | {'solved':>10} | {'pass_rate':>9} | {'cost_usd':>9}")
    lines.append(f"  {'-' * ID_W}-+-{'-' * 10}-+-{'-' * 9}-+-{'-' * 9}")
    best_id = result["best_single"]["id"]
    for v in result["variants"]:
        mark = "  <- best" if v["id"] == best_id else ""
        solved_cell = f"{v['n_solved']}/{n_tasks}"
        lines.append(
            f"  {v['id']:<{ID_W}} | {solved_cell:>10} | {_pct(v['pass_rate']):>9} | ${v['cost_usd']:>8.2f}{mark}"
        )
    lines.append("")

    # ── Headline ─────────────────────────────────────────────────────────────
    best = result["best_single"]
    orc = result["oracle"]
    hr = result["headroom"]
    rel = hr["rel"]
    rel_str = f"{100 * rel:+.1f}%" if rel is not None else "n/a"
    lines.append("Ceiling")
    lines.append(f"  best single variant : {best['n_solved']}/{n_tasks}  ({_pct(best['pass_rate'])})  [{best['id']}]")
    lines.append(f"  oracle union        : {orc['n_solved']}/{n_tasks}  ({_pct(orc['pass_rate'])})")
    lines.append(f"  >>> HEADROOM        : {hr['abs_pp']:+.1f}pp absolute   ({rel_str} relative)")
    lines.append("")

    # ── Last-round-only union ────────────────────────────────────────────────
    lr = result["last_round_union"]
    lines.append("Last-round union (each lineage's final round only)")
    lines.append(
        f"  {lr['n_variants']} final-round variant(s): {lr['n_solved']}/{n_tasks}  "
        f"({_pct(_pass_rate(lr['n_solved'], n_tasks))})"
    )
    lines.append(
        f"  vs best single : {lr['headroom_abs_pp_vs_best']:+.1f}pp    "
        f"below full oracle : {lr['gap_abs_pp_below_oracle']:+.1f}pp"
    )
    lines.append("")

    # ── Coverage histogram ───────────────────────────────────────────────────
    lines.append("Task coverage (# variants solving each task)")
    hist = result["coverage_histogram"]
    max_count = max(result["coverage_histogram"].values()) or 1
    for k in sorted(hist, key=int):
        cnt = hist[k]
        bar = "#" * int(round(40 * cnt / max_count)) if cnt else ""
        tag = ""
        if k == "0":
            tag = "  (unsolvable - no variant solves)"
        elif k == "1":
            tag = "  (uniquely solved - headroom source)"
        lines.append(f"  {k:>3} variants: {cnt:>4}  {bar}{tag}")
    lines.append(
        f"  unsolvable={result['n_unsolvable']}  uniquely_solved={result['n_uniquely_solved']}  "
        f"solved_by_all={result['n_solved_by_all']}"
    )
    lines.append("")

    # ── Overlap ──────────────────────────────────────────────────────────────
    ov = result["overlap"]
    if ov.get("n_pairs"):
        lines.append("Pairwise solved-set overlap (Jaccard; lower = more diverse)")
        lines.append(
            f"  pairs={ov['n_pairs']}  min={ov['jaccard_min']:.3f}  "
            f"median={ov['jaccard_median']:.3f}  mean={ov['jaccard_mean']:.3f}  "
            f"max={ov['jaccard_max']:.3f}"
        )
        if "matrix" in ov:
            for key, val in ov["matrix"].items():
                lines.append(f"    {key}: {val:.3f}")
        lines.append("")

    # ── Level buckets ────────────────────────────────────────────────────────
    levels = result["levels"]
    if levels:
        lines.append("Per-level headroom")
        lines.append(f"  {'level':>6} | {'tasks':>6} | {'best_single':>12} | {'oracle':>10} | {'headroom':>10}")
        lines.append(f"  {'-' * 6}-+-{'-' * 6}-+-{'-' * 12}-+-{'-' * 10}-+-{'-' * 10}")
        for lvl, b in levels["buckets"].items():
            lines.append(
                f"  {lvl:>6} | {b['n_tasks']:>6} | {_pct(b['best_single']['pass_rate']):>12} | "
                f"{_pct(b['oracle']['pass_rate']):>10} | {b['headroom_abs_pp']:>+8.1f}pp"
            )
        if levels["n_tasks_without_level"]:
            lines.append(f"  ({levels['n_tasks_without_level']} task(s) had no known level, excluded)")
        lines.append("")

    # ── Warnings ─────────────────────────────────────────────────────────────
    if result["warnings"]:
        lines.append(f"Warnings ({len(result['warnings'])})")
        for w in result["warnings"]:
            lines.append(f"  ! {w}")
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="oracle_ceiling.py",
        description=(
            "W0 (M0): offline oracle-ceiling aggregation. Given one or more "
            "completed evolver run directories (each a single lineage with a "
            "comparison.json), treat every (lineage, round) checkpoint as a "
            "routable variant and compute the perfect-router pass-rate ceiling "
            "and its headroom over the best single variant."
        ),
    )
    parser.add_argument(
        "run_dirs",
        nargs="+",
        type=Path,
        help="One or more evolver run directories, each containing comparison.json. Each = one lineage.",
    )
    parser.add_argument(
        "--last-k",
        type=int,
        default=None,
        help="Use only the last N rounds of each lineage as variants. Default: all rounds.",
    )
    parser.add_argument(
        "--tasks",
        default=None,
        help=(
            "Optional GAIA task JSON (webthinker/HF schema, 'Level' key). If given, "
            "its levels override the per-record levels for the per-level headroom "
            "buckets. Gracefully skipped if absent or level-free."
        ),
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Optional directory to write machine-readable oracle_ceiling.json into.",
    )
    return parser


def run(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.last_k is not None and args.last_k <= 0:
        raise _fail("--last-k must be a positive integer")

    warnings: list[str] = []
    lineages = build_lineages(list(args.run_dirs), args.last_k, warnings)
    # Reload raw rounds for the level map (records carry authoritative levels).
    raw_rounds = [load_comparison(lin.run_dir) for lin in lineages]
    level_map = build_level_map(raw_rounds, args.tasks, warnings)

    result = compute_ceiling(lineages, level_map, warnings)

    print(format_report(result))

    if args.out is not None:
        out_dir: Path = args.out
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / "oracle_ceiling.json"
        out_path.write_text(
            json.dumps(result, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        logger.info("Wrote %s", out_path)

    return 0


def main() -> None:
    try:
        raise SystemExit(run())
    except SystemExit as exc:
        # argparse and _fail() both raise SystemExit; surface non-int messages.
        if isinstance(exc.code, str):
            print(exc.code, file=sys.stderr)
            raise SystemExit(2) from None
        raise


if __name__ == "__main__":
    main()
