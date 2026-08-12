# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""L5 graph-native candidate surface -- session-scoped Evolver tools.

Every mutation goes through the real ``transactional_apply(materialize=True)``
transaction, and the two files a candidate produces (manifest ``.md``,
``config.yaml``) are always machine-rewritten. The killer tests (6/7) feed the
module's own output back through the REAL vendored parser/gates
(``parse_candidate_manifest``, ``validate_candidate_manifest``,
``validate_applied_config``) -- proof this module's artifacts are not just
internally self-consistent but actually acceptable to the official pipeline.

Fixture note: ``build_from_config`` (the S4 materialize build) never forwards a
``_target_`` dict's ``_hook_``/``_order_``/``_singleton_group_``/``_after_`` fields
to ``HarnessBuilder.add()`` -- only a processor CLASS's own ``_hook``/``_order``/
``_singleton_group``/``_after`` attributes (no trailing underscore) survive a
materialize round-trip; constructor kwargs do survive (verified empirically against
``harnessx/core/builder.py`` before writing this fixture). So the probes below
declare those as class attributes -- matching what the YAML also declares, for
first-pass (pre-materialize) ``to_graph`` consistency -- and expose one real
constructor kwarg (``tag``) as the thing REPLACE_SAME_GROUP actually changes.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from harnessx.aegis.agents.evolver import parse_candidate_manifest
from harnessx.aegis.apply import validate_applied_config
from harnessx.aegis.gates.structure import validate_candidate_manifest
from harnessx.core.harness import HarnessConfig
from harnessx.core.processor import MultiHookProcessor
from harnessx.ghx.graph_proposals import (
    FLAG,
    ProposalPreflightError,
    ProposalSession,
    graph_proposals_enabled,
)
from harnessx.graph.identity import genotype_hash
from harnessx.graph.snapshot import to_graph


# ── real, importable fixture processors ────────────────────────────────────


class _EchoProbe(MultiHookProcessor):
    _order = 10
    _singleton_group = "probe_sg"

    def __init__(self, tag: str = "v1") -> None:
        self.tag = tag

    async def on_task_start(self, event):
        yield event


class _OrderedProbe(MultiHookProcessor):
    _order = 20
    _singleton_group = "ordered_sg"
    _after = ("probe_sg",)

    async def on_task_start(self, event):
        yield event


class _SlotProbe(MultiHookProcessor):
    _order = 30
    _singleton_group = "slot_sg"

    async def on_step_end(self, event):
        yield event


_ECHO_TARGET = "tests.ghx.test_graph_proposals._EchoProbe"
_ORDERED_TARGET = "tests.ghx.test_graph_proposals._OrderedProbe"
_SLOT_TARGET = "tests.ghx.test_graph_proposals._SlotProbe"

# 3 processors, singleton_group on every node, _after_ on the second. A dict
# "plugins" entry proves non-processors top-level keys survive the merge
# untouched (I3 / test 7) -- it is never imported (canonicalize doesn't
# instantiate plugins, only the runtime build step would).
_PARENT_YAML = f"""processors:
  - _target_: {_ECHO_TARGET}
    _hook_: "*"
    _singleton_group_: probe_sg
    _order_: 10
    tag: v1
  - _target_: {_ORDERED_TARGET}
    _hook_: "*"
    _singleton_group_: ordered_sg
    _order_: 20
    _after_: [probe_sg]
  - _target_: {_SLOT_TARGET}
    _hook_: "*"
    _singleton_group_: slot_sg
    _order_: 30
plugins:
  - _target_: tests.ghx.fixtures_stub.FakePlugin
    note: keep-me-untouched
"""

_BAD_PARENT_YAML = """processors:
  - _target_: totally.bogus.module.NotARealClass
    _hook_: task_start
    _singleton_group_: bogus_sg
"""


def _write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8", newline="\n")
    return path


def _make_session(tmp_path: Path, *, round_n: int = 7, parent_yaml: str = _PARENT_YAML) -> ProposalSession:
    parent = _write(tmp_path / "parent.yaml", parent_yaml)
    return ProposalSession(
        parent_config_path=parent,
        candidates_dir=tmp_path / "candidates",
        applied_root=tmp_path / "applied",
        round_n=round_n,
    )


def _tools(session: ProposalSession) -> dict:
    return {t.name: t for t in session.make_tools()}


def _node_ids(session: ProposalSession) -> dict:
    """target -> node id, read off the session's own preflighted parent snapshot
    (avoids hardcoding the M2a slug algorithm's exact output)."""
    out = {}
    for nid, node in session._parent_snapshot.nodes.items():
        t = node.metadata.get("_target_")
        if t:
            out[t] = nid
    return out


def _echo_node_spec(tag: str) -> dict:
    return {"_target_": _ECHO_TARGET, "_hook_": "*", "_singleton_group_": "probe_sg",
            "_order_": 10, "tag": tag}


# ── 1. preflight ──────────────────────────────────────────────────────────────


def test_preflight_good_parent_succeeds(tmp_path: Path):
    session = _make_session(tmp_path)
    assert session._parent_hashes["genotype"]
    assert "3 processor node(s)" in session.node_inventory_text()


def test_preflight_bad_parent_raises_preflight_error(tmp_path: Path):
    parent = _write(tmp_path / "parent.yaml", _BAD_PARENT_YAML)
    with pytest.raises(ProposalPreflightError):
        ProposalSession(
            parent_config_path=parent,
            candidates_dir=tmp_path / "candidates",
            applied_root=tmp_path / "applied",
            round_n=7,
        )


# ── 2. Open: cid format lock + skeleton passes the real parser ────────────────


async def test_open_rejects_malformed_candidate_id(tmp_path: Path):
    session = _make_session(tmp_path, round_n=7)
    tools = _tools(session)

    bad_shape = await tools["GraphProposalOpen"].fn(candidate_id="C-R7-1", bucket="config")
    assert bad_shape["ok"] is False
    wrong_round = await tools["GraphProposalOpen"].fn(candidate_id="C-R8-01", bucket="config")
    assert wrong_round["ok"] is False
    # Nothing was written for either rejected attempt.
    assert not (session.candidates_dir / "C-R7-1.md").exists()
    assert not (session.candidates_dir / "C-R8-01.md").exists()


async def test_open_accepts_correct_shape_and_writes_skeleton(tmp_path: Path):
    session = _make_session(tmp_path, round_n=7)
    tools = _tools(session)

    res = await tools["GraphProposalOpen"].fn(candidate_id="C-R7-01", bucket="config")
    assert res["ok"] is True, res

    manifest_path = session.candidates_dir / "C-R7-01.md"
    config_path = session.applied_root / "C-R7-01" / "config.yaml"
    assert manifest_path.exists()
    assert config_path.exists()

    # Real vendored parser must eat the skeleton without raising.
    text = manifest_path.read_text(encoding="utf-8")
    fm, body = parse_candidate_manifest(text)
    assert fm["candidate_id"] == "C-R7-01"
    assert fm["bucket"] == "config"
    assert fm["capability_evidence"] == []  # required key present, legitimately empty
    assert fm["file_changes"]  # never empty -- config.yaml entry always present

    # The skeleton fails the real gate on CONTENT (no evidence anchor yet), never
    # on the carrier (YAML parses, required keys are all present -- asserted above).
    gate = validate_candidate_manifest(fm, body)
    assert gate.ok is False
    assert "zero evidence anchors" in gate.reason


# ── 3. Edit success path: REPLACE_SAME_GROUP changes a parameter ──────────────


async def test_edit_replace_same_group_success_writes_through_and_reconciles(tmp_path: Path):
    session = _make_session(tmp_path, round_n=7)
    tools = _tools(session)
    await tools["GraphProposalOpen"].fn(candidate_id="C-R7-01", bucket="config")

    rp_id = _node_ids(session)[_ECHO_TARGET]
    edits = [{"edit_type": "replace_same_group", "target_node_id": rp_id,
              "node_spec": _echo_node_spec("v2")}]
    res = await tools["GraphProposalEdit"].fn(candidate_id="C-R7-01", edits=edits, reason="bump tag")
    assert res["ok"] is True, res
    assert res["genotype"]
    assert "tag='v2'" in res["node_inventory"]

    jsonl_path = session.applied_root / "C-R7-01" / "graph_edits.jsonl"
    lines = jsonl_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["edit_type"] == "replace_same_group"
    assert rec["reason"] == "bump tag"

    # Post-write reload genotype must match what the tool reported.
    config_path = session.applied_root / "C-R7-01" / "config.yaml"
    reloaded = HarnessConfig.from_yaml_file(str(config_path)).canonicalize()
    assert genotype_hash(to_graph(reloaded)) == res["genotype"]


# ── 4. Edit failure path: illegal target_node_id ───────────────────────────────


async def test_edit_illegal_target_node_id_is_structured_rejection(tmp_path: Path):
    session = _make_session(tmp_path, round_n=7)
    tools = _tools(session)
    await tools["GraphProposalOpen"].fn(candidate_id="C-R7-01", bucket="config")
    before_genotype = genotype_hash(session._candidates["C-R7-01"].snapshot)

    edits = [{
        "edit_type": "replace_same_group",
        "target_node_id": "proc:does_not_exist",
        "node_spec": _echo_node_spec("v9"),
    }]
    res = await tools["GraphProposalEdit"].fn(candidate_id="C-R7-01", edits=edits)

    assert res["ok"] is False
    assert res["issues"]
    for issue in res["issues"]:
        assert set(issue) >= {"layer", "error_type", "message"}

    assert genotype_hash(session._candidates["C-R7-01"].snapshot) == before_genotype
    assert not (session.applied_root / "C-R7-01" / "graph_edits.jsonl").exists()


async def test_edit_bad_edit_type_is_structured_rejection_not_a_raise(tmp_path: Path):
    session = _make_session(tmp_path, round_n=7)
    tools = _tools(session)
    await tools["GraphProposalOpen"].fn(candidate_id="C-R7-01", bucket="config")

    res = await tools["GraphProposalEdit"].fn(
        candidate_id="C-R7-01", edits=[{"edit_type": "not_a_real_edit_type"}],
    )
    assert res["ok"] is False
    assert res["issues"][0]["layer"] == "parse"


# ── 5. Atomic group: second edit illegal -> whole group rejected ──────────────


async def test_edit_group_is_atomic_second_bad_edit_rejects_the_whole_group(tmp_path: Path):
    session = _make_session(tmp_path, round_n=7)
    tools = _tools(session)
    await tools["GraphProposalOpen"].fn(candidate_id="C-R7-01", bucket="config")
    rp_id = _node_ids(session)[_ECHO_TARGET]
    before_genotype = genotype_hash(session._candidates["C-R7-01"].snapshot)

    bad_spec = _echo_node_spec("v3")
    bad_spec["_singleton_group_"] = "WRONG_GROUP"
    edits = [
        {"edit_type": "replace_same_group", "target_node_id": rp_id, "node_spec": _echo_node_spec("v2")},
        # Same node again, but a mismatched singleton_group -> GraphEditError
        # inside apply_edits, which aborts the ENTIRE group.
        {"edit_type": "replace_same_group", "target_node_id": rp_id, "node_spec": bad_spec},
    ]
    res = await tools["GraphProposalEdit"].fn(candidate_id="C-R7-01", edits=edits)

    assert res["ok"] is False
    assert genotype_hash(session._candidates["C-R7-01"].snapshot) == before_genotype
    assert not (session.applied_root / "C-R7-01" / "graph_edits.jsonl").exists()


# ── 6. KILLER TEST: machine-produced manifest passes the real structure gate ──


async def test_killer_machine_manifest_passes_real_structure_gate(tmp_path: Path):
    session = _make_session(tmp_path, round_n=7)
    tools = _tools(session)
    await tools["GraphProposalOpen"].fn(candidate_id="C-R7-01", bucket="config")

    rp_id = _node_ids(session)[_ECHO_TARGET]
    edits = [{"edit_type": "replace_same_group", "target_node_id": rp_id,
              "node_spec": _echo_node_spec("v2")}]
    edit_res = await tools["GraphProposalEdit"].fn(candidate_id="C-R7-01", edits=edits, reason="tune tag")
    assert edit_res["ok"] is True, edit_res

    manifest_res = await tools["GraphProposalManifest"].fn(
        candidate_id="C-R7-01",
        capability_evidence=[{
            "type": "builtin_tool", "claim": "uses only stdlib", "evidence": "no new imports added",
        }],
        predicted_impact={
            "tasks_will_unlock": ["task_a"], "tasks_will_stabilize": [],
            "tasks_at_risk": [], "tasks_will_pass": ["task_a"],
        },
        failure_evidence=(
            "Observed a scheduling timeout in "
            "`trajectories/abc123_r0.jsonl#step_5` -- raised probe tag to fix it."
        ),
        attribution_signature={
            "type": "processor_invocation", "tool_name": "EchoProbe", "expected_min_calls": 1,
        },
    )
    assert manifest_res["ok"] is True, manifest_res

    # Feed the WRITTEN FILE back through the real vendored parser + gate.
    manifest_path = session.candidates_dir / "C-R7-01.md"
    text = manifest_path.read_bytes().decode("utf-8")
    fm, body = parse_candidate_manifest(text)
    gate = validate_candidate_manifest(fm, body)
    assert gate.ok, gate.reason


# ── 7. machine config passes the official applied-config validator ────────────


async def test_config_passes_official_validate_applied_config_and_keeps_extra_keys(tmp_path: Path):
    session = _make_session(tmp_path, round_n=7)
    tools = _tools(session)
    await tools["GraphProposalOpen"].fn(candidate_id="C-R7-01", bucket="config")

    rp_id = _node_ids(session)[_ECHO_TARGET]
    edits = [{"edit_type": "replace_same_group", "target_node_id": rp_id,
              "node_spec": _echo_node_spec("v2")}]
    res = await tools["GraphProposalEdit"].fn(candidate_id="C-R7-01", edits=edits)
    assert res["ok"] is True, res

    config_path = session.applied_root / "C-R7-01" / "config.yaml"
    result = validate_applied_config(config_path, expected_bucket="config")
    assert result.canonicalized is True

    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert raw["plugins"] == [{"_target_": "tests.ghx.fixtures_stub.FakePlugin", "note": "keep-me-untouched"}]


# ── 8. hand-written detection ──────────────────────────────────────────────────


async def test_hand_written_config_detected_provenance_flips_and_diff_recorded(tmp_path: Path):
    session = _make_session(tmp_path, round_n=7)
    tools = _tools(session)
    await tools["GraphProposalOpen"].fn(candidate_id="C-R7-01", bucket="config")

    config_path = session.applied_root / "C-R7-01" / "config.yaml"
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    for entry in raw["processors"]:
        if entry.get("_target_") == _ECHO_TARGET:
            entry["tag"] = "hand-edited"
    hand_text = yaml.safe_dump(raw, allow_unicode=True, sort_keys=False)
    _write(config_path, hand_text)

    rp_id = _node_ids(session)[_ECHO_TARGET]
    edits = [{"edit_type": "replace_same_group", "target_node_id": rp_id,
              "node_spec": _echo_node_spec("v2")}]
    res = await tools["GraphProposalEdit"].fn(candidate_id="C-R7-01", edits=edits)
    assert res["ok"] is True, res

    candidate = session._candidates["C-R7-01"]
    assert candidate.provenance == "hand_written_detected"
    assert candidate.hand_written_events

    lineage = json.loads((session.applied_root / "C-R7-01" / "graph_lineage.json").read_text(encoding="utf-8"))
    assert lineage["provenance"] == "hand_written_detected"
    assert lineage["hand_written_events"]
    ev = lineage["hand_written_events"][0]
    assert ev["kind"] == "config"
    assert "derived_edits" in ev  # diff_graphs succeeded against the parseable hand edit


# ── 9. zero-edit candidate ─────────────────────────────────────────────────────


async def test_zero_edit_candidate_marks_lineage_and_manifest(tmp_path: Path):
    session = _make_session(tmp_path, round_n=7)
    tools = _tools(session)
    await tools["GraphProposalOpen"].fn(candidate_id="C-R7-01", bucket="config")

    manifest_text = (session.candidates_dir / "C-R7-01.md").read_text(encoding="utf-8")
    assert "ZERO CONFIG DELTA" in manifest_text

    lineage = json.loads((session.applied_root / "C-R7-01" / "graph_lineage.json").read_text(encoding="utf-8"))
    assert lineage["zero_edit"] is True


# ── 10. flag ────────────────────────────────────────────────────────────────────


def test_flag_default_off_and_reads_env_live(monkeypatch):
    monkeypatch.delenv(FLAG, raising=False)
    assert graph_proposals_enabled() is False
    monkeypatch.setenv(FLAG, "1")
    assert graph_proposals_enabled() is True
    monkeypatch.setenv(FLAG, "off")
    assert graph_proposals_enabled() is False


# ── 11. Windows: no CRLF in any emitted artifact ───────────────────────────────


async def test_no_crlf_in_any_emitted_artifact(tmp_path: Path):
    session = _make_session(tmp_path, round_n=7)
    tools = _tools(session)
    await tools["GraphProposalOpen"].fn(candidate_id="C-R7-01", bucket="config")

    rp_id = _node_ids(session)[_ECHO_TARGET]
    edits = [{"edit_type": "replace_same_group", "target_node_id": rp_id,
              "node_spec": _echo_node_spec("v2")}]
    await tools["GraphProposalEdit"].fn(candidate_id="C-R7-01", edits=edits, reason="crlf check")

    scratch = session.applied_root / "C-R7-01"
    paths = [
        session.candidates_dir / "C-R7-01.md",
        scratch / "config.yaml",
        scratch / "graph_edits.jsonl",
        scratch / "graph_lineage.json",
        scratch / "graph_lineage.md",
    ]
    for p in paths:
        assert b"\r\n" not in p.read_bytes(), p


# ── 12. reopen after crash: refuses, does not overwrite; jsonl accumulates ────


async def test_reopen_existing_candidate_refuses_and_does_not_overwrite(tmp_path: Path):
    session = _make_session(tmp_path, round_n=7)
    tools = _tools(session)
    await tools["GraphProposalOpen"].fn(candidate_id="C-R7-01", bucket="config")
    manifest_path = session.candidates_dir / "C-R7-01.md"
    before = manifest_path.read_bytes()

    # A fresh ProposalSession over the same directories simulates a restarted
    # process picking the round back up after a crash.
    session2 = _make_session(tmp_path, round_n=7)
    tools2 = _tools(session2)
    res = await tools2["GraphProposalOpen"].fn(candidate_id="C-R7-01", bucket="config")

    assert res["ok"] is False
    assert manifest_path.read_bytes() == before  # untouched, not overwritten

    status = await tools2["GraphProposalStatus"].fn(candidate_id="")
    assert status["ok"] is True


async def test_jsonl_accumulates_incrementally_across_edit_calls(tmp_path: Path):
    session = _make_session(tmp_path, round_n=7)
    tools = _tools(session)
    await tools["GraphProposalOpen"].fn(candidate_id="C-R7-01", bucket="config")
    rp_id = _node_ids(session)[_ECHO_TARGET]

    for tag in ("v2", "v3"):
        edits = [{"edit_type": "replace_same_group", "target_node_id": rp_id,
                  "node_spec": _echo_node_spec(tag)}]
        res = await tools["GraphProposalEdit"].fn(candidate_id="C-R7-01", edits=edits, reason=f"tag->{tag}")
        assert res["ok"] is True, res

    jsonl_path = session.applied_root / "C-R7-01" / "graph_edits.jsonl"
    lines = jsonl_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["node_spec"]["tag"] == "v2"
    assert json.loads(lines[1])["node_spec"]["tag"] == "v3"


# ── extra: GraphProposalStatus (4th tool, not otherwise exercised above) ──────


async def test_status_reports_checklist_missing_fields_and_gate(tmp_path: Path):
    session = _make_session(tmp_path, round_n=7)
    tools = _tools(session)
    await tools["GraphProposalOpen"].fn(candidate_id="C-R7-01", bucket="config")

    status = await tools["GraphProposalStatus"].fn(candidate_id="C-R7-01")
    assert status["ok"] is True
    assert status["edit_count"] == 0
    assert status["structure_gate_ok"] is False
    assert "capability_evidence" in status["manifest_missing_fields"]
    assert status["checklist"]

    all_status = await tools["GraphProposalStatus"].fn(candidate_id="")
    assert "C-R7-01" in all_status["candidates"]

    missing = await tools["GraphProposalStatus"].fn(candidate_id="C-R9-99")
    assert missing["ok"] is False
