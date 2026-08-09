"""Δ11 block 22 — graph ↔ journal back-links.

Anchor capture at observation time, node-id derivation via the shared slug
rule, bidirectional index, and literal line-number resolution.
"""

import asyncio
import json
from types import SimpleNamespace

from harnessx.graph.backlink import (
    BacklinkIndex,
    observation_node_id,
    resolve_journal_line,
    uuid_at_line,
)
from harnessx.graph.observer import HookObservation, ObservationProcessor, TaskTrace


def _drive(agen):
    async def _run():
        async for _ in agen:
            pass
    asyncio.run(_run())


# ── node-id derivation (same slugs as to_graph) ─────────────────────────────


def test_qualified_target_maps_to_proc_node():
    obs = HookObservation(step_id=1, hook_name="before_model",
                         processor_label="CostGuardProcessor",
                         processor_target="x.y.CostGuardProcessor")
    assert observation_node_id(obs) == "proc:cost_guard_processor"


def test_bare_hook_observation_maps_to_hook_node():
    obs = HookObservation(step_id=1, hook_name="step_end",
                         processor_label="step_end")
    assert observation_node_id(obs) == "hook:step_end"


def test_model_and_tool_observations_map_to_hook_nodes():
    model = HookObservation(step_id=1, hook_name="after_model",
                            processor_label="model")
    tool = HookObservation(step_id=1, hook_name="before_tool",
                           processor_label="tool:Bash")
    assert observation_node_id(model) == "hook:after_model"
    assert observation_node_id(tool) == "hook:before_tool"


def test_unqualified_processor_label_slugs_to_proc_node():
    obs = HookObservation(step_id=1, hook_name="task_start",
                          processor_label="SlidingWindowMemory")
    assert observation_node_id(obs) == "proc:sliding_window_memory"


# ── Δ11 anchor capture at observation time ──────────────────────────────────


class _FakeJournal:
    def __init__(self):
        self.last_uuid = None


def _bound_observer(journal):
    obs = ObservationProcessor(task_id="t1")
    obs._bind_runtime(SimpleNamespace(tracer=journal))
    return obs


def test_observer_captures_current_journal_uuid():
    journal = _FakeJournal()
    obs = _bound_observer(journal)

    journal.last_uuid = "uuid-line-1"
    _drive(obs.on_before_model(SimpleNamespace()))
    journal.last_uuid = "uuid-line-2"
    _drive(obs.on_step_end(SimpleNamespace()))

    uuids = [o.journal_uuid for o in obs.trace.observations]
    assert uuids == ["uuid-line-1", "uuid-line-2"]  # per-observation anchor


def test_unbound_observer_records_empty_anchor():
    obs = ObservationProcessor(task_id="t1")  # no runtime bound
    _drive(obs.on_step_end(SimpleNamespace()))
    assert obs.trace.observations[0].journal_uuid == ""


def test_null_tracer_records_empty_anchor():
    obs = _bound_observer(SimpleNamespace())  # tracer without last_uuid
    obs._bind_runtime(SimpleNamespace(tracer=object()))
    _drive(obs.on_step_end(SimpleNamespace()))
    assert obs.trace.observations[0].journal_uuid == ""


# ── bidirectional index ─────────────────────────────────────────────────────


def _trace_with_anchors():
    trace = TaskTrace(task_id="t1", run_id="r1")
    trace.record(HookObservation(step_id=1, hook_name="before_model",
                                 processor_label="model",
                                 journal_uuid="u1"))
    trace.record(HookObservation(step_id=1, hook_name="task_start",
                                 processor_label="Probe",
                                 processor_target="m.Probe",
                                 journal_uuid="u1"))
    trace.record(HookObservation(step_id=2, hook_name="step_end",
                                 processor_label="step_end",
                                 journal_uuid="u2"))
    return trace


def test_index_graph_to_journal():
    idx = BacklinkIndex.from_trace(_trace_with_anchors())
    refs = idx.refs_for_node("proc:probe")
    assert len(refs) == 1
    assert refs[0].journal_uuid == "u1"
    assert refs[0].run_id == "r1"
    assert refs[0].step_id == 1


def test_index_journal_to_graph():
    idx = BacklinkIndex.from_trace(_trace_with_anchors())
    assert set(idx.nodes_for_uuid("u1")) == {"hook:before_model", "proc:probe"}
    assert idx.nodes_for_uuid("u2") == ["hook:step_end"]
    assert idx.nodes_for_uuid("missing") == []


# ── literal line-number resolution ──────────────────────────────────────────


def _write_jsonl(tmp_path):
    path = tmp_path / "run.jsonl"
    lines = [
        {"uuid": "u1", "parent_uuid": None, "type": "user"},
        {"uuid": "u2", "parent_uuid": "u1", "type": "assistant"},
        {"uuid": "u3", "parent_uuid": "u2", "type": "tool"},
    ]
    path.write_text("\n".join(json.dumps(x) for x in lines) + "\n",
                    encoding="utf-8")
    return path


def test_resolve_uuid_to_line_number(tmp_path):
    path = _write_jsonl(tmp_path)
    line_no, record = resolve_journal_line(path, "u2")
    assert line_no == 2
    assert record["type"] == "assistant"


def test_resolve_missing_uuid(tmp_path):
    assert resolve_journal_line(_write_jsonl(tmp_path), "nope") is None
    assert resolve_journal_line(tmp_path / "ghost.jsonl", "u1") is None


def test_line_number_back_to_uuid(tmp_path):
    path = _write_jsonl(tmp_path)
    assert uuid_at_line(path, 3) == "u3"
    assert uuid_at_line(path, 99) == ""


def test_round_trip_node_to_line_and_back(tmp_path):
    path = _write_jsonl(tmp_path)
    idx = BacklinkIndex.from_trace(_trace_with_anchors())
    # graph → journal line
    ref = idx.refs_for_node("proc:probe")[0]
    line_no, _ = resolve_journal_line(path, ref.journal_uuid)
    assert line_no == 1
    # journal line → graph
    assert "proc:probe" in idx.nodes_for_uuid(uuid_at_line(path, line_no))
