# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""Pinned-manifest replay glue: manifest expansion, strict decision merge,
metadata-only tier.  Pure filesystem fixtures — no graph builds."""
from __future__ import annotations

import json

import pytest

from experiments.analysis.replay_corpus import (
    VP_ERA_ROOTS,
    census_drift,
    corpus_roots,
    merge_decisions,
)


def _mk_candidate(root, rnum, cid):
    d = root / f"R{rnum}" / "V0" / "pipeline" / "candidates" / cid
    d.mkdir(parents=True, exist_ok=True)
    (d / "config.yaml").write_text("tool_registry: {}\n", encoding="utf-8")


def test_corpus_roots_missing_is_loud(tmp_path):
    (tmp_path / next(iter(VP_ERA_ROOTS))).mkdir()
    with pytest.raises(FileNotFoundError, match="pinned corpus root"):
        corpus_roots(tmp_path)


def test_census_drift_reports_expected_vs_found(tmp_path):
    for name in VP_ERA_ROOTS:
        (tmp_path / name).mkdir()
    _mk_candidate(tmp_path / "s1k8", 1, "C-R1-01")  # expected 10, found 1
    drift = census_drift(tmp_path)
    assert drift["s1k8"] == (10, 1)
    assert drift["e_pervar3"] == (41, 0)


def test_merge_decisions_nonnull_beats_null_and_later_round_wins(tmp_path):
    root = tmp_path / "run"
    for rnum, decision in ((1, None), (2, "fork"), (3, "apply")):
        rdir = root / f"R{rnum}"
        rdir.mkdir(parents=True)
        (rdir / "pool_state.json").write_text(
            json.dumps(
                {
                    "candidate_diagnostics": {
                        "C-R1-01": {"decision": decision, "evaluated": bool(decision)}
                    }
                }
            ),
            encoding="utf-8",
        )
    # Final report row is null — must NOT clobber the round-state "apply".
    (root / "pool_report.json").write_text(
        json.dumps(
            {
                "candidate_diagnostics": {
                    "candidates": [
                        {"candidate_id": "C-R1-01", "decision": None},
                        {"candidate_id": "C-R9-07", "decision": "reject"},
                    ]
                }
            }
        ),
        encoding="utf-8",
    )
    merged = merge_decisions(root)
    assert merged["C-R1-01"]["decision"] == "apply"
    assert merged["C-R1-01"]["source"] == "pool_state:R3"
    # Report-only row (no config anywhere) still surfaces — the metadata tier.
    assert merged["C-R9-07"]["decision"] == "reject"
    assert merged["C-R9-07"]["source"] == "pool_report"


def test_merge_decisions_final_report_nonnull_wins(tmp_path):
    root = tmp_path / "run"
    rdir = root / "R1"
    rdir.mkdir(parents=True)
    (rdir / "pool_state.json").write_text(
        json.dumps({"candidate_diagnostics": {"C-R1-01": {"decision": None}}}),
        encoding="utf-8",
    )
    (root / "pool_report.json").write_text(
        json.dumps(
            {
                "candidate_diagnostics": {
                    "candidates": [{"candidate_id": "C-R1-01", "decision": "reject"}]
                }
            }
        ),
        encoding="utf-8",
    )
    assert merge_decisions(root)["C-R1-01"]["decision"] == "reject"
