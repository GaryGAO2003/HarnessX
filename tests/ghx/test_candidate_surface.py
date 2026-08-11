# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""G2 piece 2 — per-candidate graph mutation surface.

A candidate's mutation surface is the exact set of graph node/edge ids it touches
over the parent config, so two candidates overlap iff their touched-node sets
intersect (a subgraph question), not iff they edit the same file. Test 3: a real
config pair yields the exact delta ids — and those ids are asserted to match the
M2a authority ``assign_processor_node_ids`` — while a derivation failure is a
recorded reason, never a file pretending the surface is empty.
"""

from __future__ import annotations

import json
from pathlib import Path

from harnessx.core.harness import HarnessConfig
from harnessx.ghx.candidate_surface import (
    derive_candidate_surface,
    materialize_candidate_surfaces,
    write_candidate_surface,
)
from harnessx.graph.snapshot import assign_processor_node_ids

_ALPHA = "harnessx.demo.AlphaProcessor"
_BETA = "harnessx.demo.BetaProcessor"

_PARENT_YAML = f"""processors:
  - _target_: {_ALPHA}
    _hook_: before_model
"""

_CANDIDATE_ADD_YAML = f"""processors:
  - _target_: {_ALPHA}
    _hook_: before_model
  - _target_: {_BETA}
    _hook_: after_tool
"""


def _write(tmp_path: Path, name: str, text: str) -> Path:
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


def _expected_id_for_target(cfg_path: Path, target: str) -> str:
    cfg = HarnessConfig.from_yaml_file(str(cfg_path)).canonicalize()
    ids = assign_processor_node_ids(cfg)
    matches = [nid for (d, nid) in ids.persistent if d.get("_target_") == target]
    assert len(matches) == 1, matches
    return matches[0]


def test_added_node_id_matches_m2a_authority(tmp_path: Path):
    parent = _write(tmp_path, "parent.yaml", _PARENT_YAML)
    candidate = _write(tmp_path, "cand.yaml", _CANDIDATE_ADD_YAML)

    surface = derive_candidate_surface("cand-add", parent, candidate)

    assert surface.ok
    # The added node id is exactly the M2a id for the Beta target...
    beta_id = _expected_id_for_target(candidate, _BETA)
    assert surface.nodes_added == [beta_id]
    # ...and the shared Alpha node is NOT in the surface (it is unchanged).
    alpha_id = _expected_id_for_target(candidate, _ALPHA)
    assert alpha_id not in surface.nodes_added
    assert surface.nodes_removed == []
    assert beta_id in surface.touched_node_ids()
    # An INSERT_NODE plus its ATTACHED_TO wiring means the diff was non-trivial.
    assert surface.edit_count >= 1


def test_removed_node_surface(tmp_path: Path):
    # Parent has both; candidate drops Beta → Beta is a removed node.
    parent = _write(tmp_path, "parent.yaml", _CANDIDATE_ADD_YAML)
    candidate = _write(tmp_path, "cand.yaml", _PARENT_YAML)
    beta_id = _expected_id_for_target(parent, _BETA)

    surface = derive_candidate_surface("cand-drop", parent, candidate)

    assert surface.ok
    assert surface.nodes_removed == [beta_id]
    assert surface.nodes_added == []


def test_write_candidate_surface_emits_ids_and_json(tmp_path: Path):
    parent = _write(tmp_path, "parent.yaml", _PARENT_YAML)
    candidate = _write(tmp_path, "cand.yaml", _CANDIDATE_ADD_YAML)
    run_dir = tmp_path / "run"

    res = write_candidate_surface(run_dir, 3, "cand-add", parent, candidate)

    path = Path(res["path"])
    assert path == run_dir / "R3" / "graph_evidence" / "candidates" / "cand-add.md"
    text = path.read_text(encoding="utf-8")
    beta_id = _expected_id_for_target(candidate, _BETA)
    assert beta_id in text
    # The fenced JSON block round-trips and carries the same ids.
    block = text.split("```json", 1)[1].split("```", 1)[0]
    obj = json.loads(block)
    assert obj["status"] == "ok"
    assert obj["nodes_added"] == [beta_id]


def test_derivation_failure_records_reason_not_empty_surface(tmp_path: Path):
    candidate = _write(tmp_path, "cand.yaml", _CANDIDATE_ADD_YAML)
    missing_parent = tmp_path / "does_not_exist.yaml"
    run_dir = tmp_path / "run"

    res = write_candidate_surface(run_dir, 1, "cand-broken", missing_parent, candidate)
    surface = res["surface"]

    assert not surface.ok
    assert surface.status == "derivation_failed"
    assert surface.reason  # a real reason string, not empty
    # The file exists and states the failure — it does NOT pretend an empty delta.
    text = Path(res["path"]).read_text(encoding="utf-8")
    assert "Derivation failed" in text
    assert "NOT an empty delta" in text
    obj = json.loads(text.split("```json", 1)[1].split("```", 1)[0])
    assert obj["status"] == "derivation_failed"
    assert obj["nodes_added"] == []


def test_materialize_batch_reports_written_and_failed(tmp_path: Path):
    parent = _write(tmp_path, "parent.yaml", _PARENT_YAML)
    good = _write(tmp_path, "good.yaml", _CANDIDATE_ADD_YAML)
    run_dir = tmp_path / "run"

    summary = materialize_candidate_surfaces(
        run_dir,
        2,
        parent,
        {"good": good, "broken": tmp_path / "nope.yaml"},
    )

    assert set(summary["written"]) == {"good", "broken"}
    assert summary["failed"] == ["broken"]
    assert (run_dir / "R2" / "graph_evidence" / "candidates" / "good.md").exists()
    assert (run_dir / "R2" / "graph_evidence" / "candidates" / "broken.md").exists()
