# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""Offline unit tests for ``recipe/gaia_evolver/oracle_ceiling.py`` (W0/M0).

All fixtures are tiny hand-written ``comparison.json`` files dropped into
``tmp_path``; nothing here touches the network, an LLM, or real GAIA data.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# Make the repo root importable when this file is run directly (pytest's
# pythonpath=["."] already covers the normal invocation).
_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from recipe.gaia_evolver.oracle_ceiling import (  # noqa: E402
    build_level_map,
    build_lineages,
    compute_ceiling,
    jaccard,
    load_comparison,
    run,
)


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _rec(task_id: str, passed: bool, *, level: int = 1, cost: float | None = 0.1, **extra) -> dict:
    d: dict = {"task_id": task_id, "passed": passed, "level": level}
    if cost is not None:
        d["cost_usd"] = cost
    d.update(extra)
    return d


def _write_run(run_dir: Path, rounds: list[list[dict]]) -> Path:
    run_dir.mkdir(parents=True, exist_ok=True)
    payload = {"rounds": rounds, "round_summaries": [], "run_config": {}}
    (run_dir / "comparison.json").write_text(json.dumps(payload), encoding="utf-8")
    return run_dir


def _analyse(run_dirs: list[Path], *, last_k=None, tasks=None) -> dict:
    warnings: list[str] = []
    lineages = build_lineages([Path(d) for d in run_dirs], last_k, warnings)
    raw = [load_comparison(lin.run_dir) for lin in lineages]
    level_map = build_level_map(raw, tasks, warnings)
    return compute_ceiling(lineages, level_map, warnings)


# ---------------------------------------------------------------------------
# Union math + best single + headroom
# ---------------------------------------------------------------------------


def test_union_math_and_headroom(tmp_path: Path) -> None:
    a = _write_run(
        tmp_path / "linA",
        [[_rec("t1", True), _rec("t2", True), _rec("t3", False)]],
    )
    b = _write_run(
        tmp_path / "linB",
        [[_rec("t1", False), _rec("t2", False), _rec("t3", True)]],
    )
    res = _analyse([a, b])

    assert res["n_tasks"] == 3
    assert res["n_variants"] == 2
    # Best single = lineage A, which solves 2 of 3.
    assert res["best_single"]["n_solved"] == 2
    assert res["best_single"]["id"] == "linA/R0"
    assert res["best_single"]["pass_rate"] == pytest.approx(2 / 3)
    # Oracle union covers all three tasks.
    assert res["oracle"]["n_solved"] == 3
    assert res["oracle"]["pass_rate"] == pytest.approx(1.0)
    assert sorted(res["oracle"]["solved_task_ids"]) == ["t1", "t2", "t3"]
    # headroom = 1.0 - 2/3 = 33.33pp absolute, +50% relative.
    assert res["headroom"]["abs_pp"] == pytest.approx(100 * (1.0 - 2 / 3))
    assert res["headroom"]["rel"] == pytest.approx(0.5)


def test_best_single_tie_breaks_on_cost(tmp_path: Path) -> None:
    # Both variants solve exactly one task; the cheaper one must win.
    expensive = _write_run(
        tmp_path / "linExp",
        [[_rec("t1", True, cost=0.5), _rec("t2", False, cost=0.5)]],
    )
    cheap = _write_run(
        tmp_path / "linCheap",
        [[_rec("t1", False, cost=0.05), _rec("t2", True, cost=0.05)]],
    )
    res = _analyse([expensive, cheap])

    assert res["best_single"]["n_solved"] == 1
    assert res["best_single"]["id"] == "linCheap/R0"


# ---------------------------------------------------------------------------
# Cross-lineage task-set inconsistency (absent = unsolved)
# ---------------------------------------------------------------------------


def test_absent_tasks_counted_unsolved_and_warned(tmp_path: Path) -> None:
    a = _write_run(tmp_path / "linA", [[_rec("t1", True), _rec("t2", True)]])
    b = _write_run(tmp_path / "linB", [[_rec("t1", True), _rec("t3", True)]])
    res = _analyse([a, b])

    # Universe is the union {t1, t2, t3}; each lineage attempted only 2 tasks.
    assert res["n_tasks"] == 3
    # A solves {t1,t2}; t3 is absent from A and counts as unsolved -> 2/3.
    assert res["best_single"]["n_solved"] == 2
    assert res["oracle"]["n_solved"] == 3
    # The mismatch must surface as a warning, not a silent miscount.
    assert any("different task sets" in w for w in res["warnings"])


# ---------------------------------------------------------------------------
# --last-k truncation
# ---------------------------------------------------------------------------


def _three_round_run(tmp_path: Path) -> Path:
    return _write_run(
        tmp_path / "linK",
        [
            [_rec("t1", True), _rec("t2", False)],  # R0
            [_rec("t1", False), _rec("t2", False)],  # R1
            [_rec("t1", True), _rec("t2", True)],  # R2
        ],
    )


def test_last_k_one_keeps_only_final_round(tmp_path: Path) -> None:
    d = _three_round_run(tmp_path)
    res = _analyse([d], last_k=1)

    assert res["n_variants"] == 1
    v = res["variants"][0]
    assert v["round"] == 2  # original round number preserved
    assert v["id"] == "linK/R2"
    # R2 solves both tasks -> ceiling == best == 100%.
    assert res["oracle"]["n_solved"] == 2
    assert res["best_single"]["n_solved"] == 2


def test_last_k_two_keeps_final_two_rounds(tmp_path: Path) -> None:
    d = _three_round_run(tmp_path)
    res = _analyse([d], last_k=2)

    assert res["n_variants"] == 2
    ids = {v["id"] for v in res["variants"]}
    assert ids == {"linK/R1", "linK/R2"}


def test_no_last_k_keeps_all_rounds(tmp_path: Path) -> None:
    d = _three_round_run(tmp_path)
    res = _analyse([d])
    assert res["n_variants"] == 3


def test_last_k_larger_than_rounds_uses_all_with_warning(tmp_path: Path) -> None:
    d = _three_round_run(tmp_path)
    warnings: list[str] = []
    lineages = build_lineages([d], 99, warnings)
    assert len(lineages[0].variants) == 3
    assert any("--last-k 99" in w for w in warnings)


# ---------------------------------------------------------------------------
# 0-solved (unsolvable) task statistics + coverage histogram
# ---------------------------------------------------------------------------


def test_unsolvable_tasks_and_histogram(tmp_path: Path) -> None:
    a = _write_run(
        tmp_path / "linA",
        [[_rec("t1", True), _rec("t2", False), _rec("t3", False)]],
    )
    b = _write_run(
        tmp_path / "linB",
        [[_rec("t1", True), _rec("t2", False), _rec("t3", False)]],
    )
    res = _analyse([a, b])

    # t2 and t3 are solved by nobody -> unsolvable.
    assert res["n_unsolvable"] == 2
    assert res["unsolvable_task_ids"] == ["t2", "t3"]
    # Histogram: two tasks at 0 variants, one task (t1) at 2 variants.
    assert res["coverage_histogram"]["0"] == 2
    assert res["coverage_histogram"]["2"] == 1
    assert res["coverage_histogram"]["1"] == 0
    assert res["n_uniquely_solved"] == 0
    assert res["n_solved_by_all"] == 1


def test_uniquely_solved_counts_headroom_sources(tmp_path: Path) -> None:
    a = _write_run(tmp_path / "linA", [[_rec("t1", True), _rec("t2", False)]])
    b = _write_run(tmp_path / "linB", [[_rec("t1", False), _rec("t2", True)]])
    res = _analyse([a, b])
    # Each task solved by exactly one variant.
    assert res["n_uniquely_solved"] == 2
    assert res["coverage_histogram"]["1"] == 2


# ---------------------------------------------------------------------------
# Last-round union diagnostic
# ---------------------------------------------------------------------------


def test_last_round_union_distinct_from_full_oracle(tmp_path: Path) -> None:
    # R0 uniquely solves t1; the final round R1 does not -> full oracle beats
    # the last-round-only union.
    d = _write_run(
        tmp_path / "linX",
        [
            [_rec("t1", True), _rec("t2", False)],  # R0 solves t1
            [_rec("t1", False), _rec("t2", True)],  # R1 (last) solves t2
        ],
    )
    res = _analyse([d])
    assert res["oracle"]["n_solved"] == 2  # {t1, t2}
    assert res["last_round_union"]["n_solved"] == 1  # only {t2}
    assert res["last_round_union"]["gap_abs_pp_below_oracle"] == pytest.approx(50.0)


# ---------------------------------------------------------------------------
# Level buckets (records by default; --tasks override)
# ---------------------------------------------------------------------------


def test_level_buckets_from_records(tmp_path: Path) -> None:
    a = _write_run(
        tmp_path / "linA",
        [[_rec("t1", True, level=1), _rec("t2", True, level=2), _rec("t3", False, level=2)]],
    )
    b = _write_run(
        tmp_path / "linB",
        [[_rec("t1", True, level=1), _rec("t2", False, level=2), _rec("t3", True, level=2)]],
    )
    res = _analyse([a, b])

    assert res["levels"] is not None
    buckets = res["levels"]["buckets"]
    # Level 1 = {t1}: everyone solves it, no headroom.
    assert buckets["1"]["n_tasks"] == 1
    assert buckets["1"]["headroom_abs_pp"] == pytest.approx(0.0)
    # Level 2 = {t2, t3}: best single solves 1/2, oracle 2/2 -> 50pp headroom.
    assert buckets["2"]["n_tasks"] == 2
    assert buckets["2"]["best_single"]["pass_rate"] == pytest.approx(0.5)
    assert buckets["2"]["oracle"]["pass_rate"] == pytest.approx(1.0)
    assert buckets["2"]["headroom_abs_pp"] == pytest.approx(50.0)


def test_tasks_json_overrides_record_levels(tmp_path: Path) -> None:
    a = _write_run(
        tmp_path / "linA",
        [[_rec("t1", True, level=1), _rec("t2", True, level=2), _rec("t3", False, level=2)]],
    )
    b = _write_run(
        tmp_path / "linB",
        [[_rec("t1", True, level=1), _rec("t2", False, level=2), _rec("t3", True, level=2)]],
    )
    # Reassign t3 to level 1 via the webthinker-style JSON (capitalised "Level").
    tasks_path = tmp_path / "tasks.json"
    tasks_path.write_text(
        json.dumps(
            [
                {"task_id": "t1", "Level": 1},
                {"task_id": "t2", "Level": 2},
                {"task_id": "t3", "Level": 1},
            ]
        ),
        encoding="utf-8",
    )
    res = _analyse([a, b], tasks=str(tasks_path))
    buckets = res["levels"]["buckets"]
    assert buckets["1"]["n_tasks"] == 2  # now {t1, t3}
    assert buckets["2"]["n_tasks"] == 1  # now {t2}


def test_no_levels_available_yields_none(tmp_path: Path) -> None:
    # Records without a level field and no --tasks -> level bucketing skipped.
    a = _write_run(
        tmp_path / "linA",
        [[{"task_id": "t1", "passed": True}, {"task_id": "t2", "passed": False}]],
    )
    res = _analyse([a])
    assert res["levels"] is None


# ---------------------------------------------------------------------------
# Defensive handling of malformed / partial records
# ---------------------------------------------------------------------------


def test_missing_cost_usd_does_not_crash(tmp_path: Path) -> None:
    # Error-path records (run.py:239-252) omit cost_usd entirely.
    a = _write_run(
        tmp_path / "linA",
        [[_rec("t1", True, cost=None), _rec("t2", False, cost=None)]],
    )
    res = _analyse([a])
    assert res["variants"][0]["cost_usd"] == 0.0


def test_blank_task_id_is_skipped_with_warning(tmp_path: Path) -> None:
    a = _write_run(
        tmp_path / "linA",
        [[_rec("t1", True), {"task_id": "", "passed": True, "level": 1}]],
    )
    warnings: list[str] = []
    lineages = build_lineages([a], None, warnings)
    res = compute_ceiling(lineages, {}, warnings)
    assert res["n_tasks"] == 1  # blank task_id record dropped
    assert any("missing/blank task_id" in w for w in warnings)


# ---------------------------------------------------------------------------
# Error handling on malformed input files
# ---------------------------------------------------------------------------


def test_missing_comparison_json_raises(tmp_path: Path) -> None:
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()
    with pytest.raises(SystemExit) as exc:
        load_comparison(empty_dir)
    assert "no comparison.json" in str(exc.value)


def test_empty_rounds_raises(tmp_path: Path) -> None:
    d = tmp_path / "linEmpty"
    d.mkdir()
    (d / "comparison.json").write_text(json.dumps({"rounds": []}), encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        load_comparison(d)
    assert "empty 'rounds'" in str(exc.value)


def test_missing_rounds_key_raises(tmp_path: Path) -> None:
    d = tmp_path / "linBad"
    d.mkdir()
    (d / "comparison.json").write_text(json.dumps({"foo": 1}), encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        load_comparison(d)
    assert "no top-level 'rounds'" in str(exc.value)


def test_nonexistent_run_dir_raises(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as exc:
        load_comparison(tmp_path / "does_not_exist")
    assert "does not exist" in str(exc.value)


# ---------------------------------------------------------------------------
# Jaccard convention
# ---------------------------------------------------------------------------


def test_jaccard_conventions() -> None:
    assert jaccard(frozenset(), frozenset()) == 0.0
    assert jaccard(frozenset({"a"}), frozenset()) == 0.0
    assert jaccard(frozenset({"a", "b"}), frozenset({"a", "b"})) == 1.0
    assert jaccard(frozenset({"a", "b"}), frozenset({"b", "c"})) == pytest.approx(1 / 3)


# ---------------------------------------------------------------------------
# End-to-end run() with --out
# ---------------------------------------------------------------------------


def test_run_end_to_end_writes_json(tmp_path: Path, capsys) -> None:
    a = _write_run(tmp_path / "linA", [[_rec("t1", True), _rec("t2", False)]])
    b = _write_run(tmp_path / "linB", [[_rec("t1", False), _rec("t2", True)]])
    out_dir = tmp_path / "out"

    rc = run([str(a), str(b), "--out", str(out_dir)])
    assert rc == 0

    out_file = out_dir / "oracle_ceiling.json"
    assert out_file.is_file()
    data = json.loads(out_file.read_text(encoding="utf-8"))
    assert data["n_tasks"] == 2
    assert data["oracle"]["n_solved"] == 2
    assert data["best_single"]["n_solved"] == 1
    assert data["headroom"]["abs_pp"] == pytest.approx(50.0)

    captured = capsys.readouterr()
    assert "HEADROOM" in captured.out
