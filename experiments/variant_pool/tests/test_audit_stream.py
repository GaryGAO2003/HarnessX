# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""batch-4a Item 4 -- append-only R{n}/audit.jsonl of lifecycle events."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from recipe.gaia_evolver import run_variant_pool as rvp  # noqa: E402
from experiments.variant_pool.audit import AuditEvent, AuditLog, _ALLOWED_KINDS  # noqa: E402


# ---------------------------------------------------------------------------
# AuditLog / AuditEvent contract
# ---------------------------------------------------------------------------


def test_no_file_until_first_append(tmp_path):
    path = tmp_path / "R1" / "audit.jsonl"
    AuditLog(path)  # constructing only makes the parent dir, not the file
    assert not path.exists()


def test_events_appended_in_order(tmp_path):
    path = tmp_path / "R1" / "audit.jsonl"
    log = AuditLog(path)
    log.append(AuditEvent(round=1, stage="preprocess", kind="preprocess", payload={"n": 3}))
    log.append(AuditEvent(round=1, stage="plan", kind="plan", payload={"b": 2}))
    log.append(AuditEvent(round=1, stage="gate", kind="gate", payload={"d": "apply"}, evidence_refs=["e"]))
    kinds = [e.kind for e in log.read_all()]
    assert kinds == ["preprocess", "plan", "gate"]
    # raw file order matches append order.
    lines = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert [r["kind"] for r in lines] == ["preprocess", "plan", "gate"]
    assert lines[2]["evidence_refs"] == ["e"]


def test_all_emitted_kinds_are_valid():
    # Every kind the recipe emits must be in the controlled vocabulary.
    for kind in ("preprocess", "plan", "propose", "propose_fail", "gate", "decision", "commit"):
        assert kind in _ALLOWED_KINDS
        AuditEvent(round=0, stage="s", kind=kind, payload={})  # constructs without error


def test_unknown_kind_rejected():
    with pytest.raises(ValueError):
        AuditEvent(round=0, stage="s", kind="not_a_kind", payload={})


def test_query_filters_by_round_and_kind(tmp_path):
    log = AuditLog(tmp_path / "audit.jsonl")
    log.append(AuditEvent(round=1, stage="s", kind="gate", payload={}))
    log.append(AuditEvent(round=2, stage="s", kind="gate", payload={}))
    log.append(AuditEvent(round=2, stage="s", kind="commit", payload={}))
    assert len(list(log.query(round=2))) == 2
    assert len(list(log.query(kind="gate"))) == 2
    assert len(list(log.query(round=2, kind="commit"))) == 1


# ---------------------------------------------------------------------------
# recipe _emit_audit gating: off => absent, on => file with the event
# ---------------------------------------------------------------------------


def test_emit_audit_off_creates_no_file(tmp_path):
    fake = SimpleNamespace(audit_stream=False, run_dir=tmp_path)
    rvp.VariantPoolRecipe._emit_audit(fake, 1, "gate", "gate", {"x": 1})
    assert not (tmp_path / "R1" / "audit.jsonl").exists()


def test_emit_audit_on_appends_event(tmp_path):
    fake = SimpleNamespace(audit_stream=True, run_dir=tmp_path)
    rvp.VariantPoolRecipe._emit_audit(fake, 1, "preprocess", "preprocess", {"n": 1})
    rvp.VariantPoolRecipe._emit_audit(
        fake, 1, "commit", "commit", {"decision": "apply"}, evidence_refs=["ref1"]
    )
    path = tmp_path / "R1" / "audit.jsonl"
    assert path.is_file()
    rows = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert [r["kind"] for r in rows] == ["preprocess", "commit"]
    assert rows[0]["round"] == 1 and rows[0]["stage"] == "preprocess"
    assert rows[1]["evidence_refs"] == ["ref1"]
