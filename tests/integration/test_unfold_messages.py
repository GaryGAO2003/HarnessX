# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""v6 M12 — the message plane on the unfolded graph U.

Before M12, ``OBSERVED_DATA`` derived only from ``State.slot_provenance``.  A stack
that carries everything through the message list writes no slots, so U had a control
plane and no data plane; control edges never cross a hook firing, so every causal
cone collapsed to the firing holding its anchor.  These run real harnesses with
``HARNESSX_GHX_UNFOLD`` on, read U back from disk, and pin what M12 adds:

  * a model call is one U node (``model:<name>``, hook ``"model"``), bridged by real
    ``before_model → model`` and ``model → after_model`` control edges;
  * message data edges exist at all in a run that touches no slot — the fact the
    whole milestone turns on;
  * the two edges that carry a task's actual causality: ``tool → model`` (the tool's
    result message, read by the model that saw it) and ``model → model`` (a reply
    read back on the next step);
  * data edges CROSS steps and firings, which control edges by construction cannot;
  * a context assembler that re-materialises history is not credited as its author;
  * U stays a DAG with model nodes present;
  * the cone anchored by ``_cone_anchors`` reaches tool and model nodes, where the
    terminal-only anchor reaches neither;
  * recording stays off by default.
"""

from __future__ import annotations

import dataclasses
import os
import sys
from collections import defaultdict

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from fixtures.mock_provider import MockProvider  # noqa: E402
from fixtures.mock_tools import add_tool, make_registry  # noqa: E402

from harnessx import (  # noqa: E402
    BaseTask,
    HarnessConfig,
    ModelConfig,
    MultiHookProcessor,
)
from harnessx.core.events import Message  # noqa: E402
from harnessx.ghx.evidence_files import _cone_anchors  # noqa: E402
from harnessx.graph.causal import causal_cone, terminal_node  # noqa: E402
from harnessx.graph.types import EdgeType  # noqa: E402
from harnessx.graph.unfold import load_unfolded, unfolded_path  # noqa: E402
from harnessx.tracing.journal import HarnessJournal  # noqa: E402

_CTRL = EdgeType.OBSERVED_CONTROL.value
_DATA = EdgeType.OBSERVED_DATA.value


def _tool_turn(tag: str) -> dict:
    return {"content": tag, "tool_calls": [{"id": tag, "name": "add", "input": {"a": 1, "b": 1}}]}


class StarPassthrough(MultiHookProcessor):
    """Fires on every hook as a pass-through — one node per firing, mutating nothing."""


class Reassembler(MultiHookProcessor):
    """Materialises ``State.messages`` into the step_start event, creating nothing.

    This is the shape that would steal authorship if a re-appearance counted as a
    write: to the dispatcher's diff its output tuple is entirely 'new'.
    """

    def __init__(self):
        super().__init__()
        self._state = None

    async def on_task_start(self, event):
        self._state = event.state
        yield event

    async def on_step_start(self, event):
        if self._state is None:
            yield event
            return
        yield dataclasses.replace(event, messages=tuple(event.messages) or tuple(self._state.messages))


class NoteInjector(MultiHookProcessor):
    """Appends one genuinely new message on step_start — a real message-plane write."""

    def __init__(self):
        super().__init__()
        self._state = None

    async def on_task_start(self, event):
        self._state = event.state
        yield event

    async def on_step_start(self, event):
        base = tuple(event.messages) or (tuple(self._state.messages) if self._state else ())
        yield dataclasses.replace(event, messages=base + (Message(role="user", content="note"),))


async def _run_and_load(tmp_path, processors, responses, *, tools=None, session_id="msess"):
    journal = HarnessJournal(base_dir=str(tmp_path), export_jsonl=True, session_id=session_id, silent=True)
    config = HarnessConfig(
        tool_registry=make_registry(*(tools or [])),
        tracer=journal,
        processors=processors,
    )
    mc = ModelConfig(main=MockProvider(responses=responses))
    result = await mc.agentic(config).run(BaseTask(description="q", max_steps=10))
    # A segment boundary can move ``state.run_id`` away from the id U was filed
    # under, so take the session's U files and use the one that recorded the run.
    written = sorted((tmp_path / session_id).glob("*_unfolded.jsonl"))
    assert written, f"expected a U under {tmp_path / session_id}"
    return result, max((load_unfolded(p) for p in written), key=lambda g: len(g.nodes))


def _by_id(graph):
    return {n.id: n for n in graph.nodes}


def _data_edges(graph, plane="message"):
    return [e for e in graph.edges if e.edge_type == _DATA and (e.metadata or {}).get("plane") == plane]


def _find_cycle_node(graph):
    ids = graph.node_ids()
    adj = defaultdict(list)
    indeg = {nid: 0 for nid in ids}
    for e in graph.edges:
        assert e.source in ids, f"edge source {e.source} is not a node"
        assert e.target in ids, f"edge target {e.target} is not a node"
        assert e.source != e.target, f"self-loop on {e.source}"
        adj[e.source].append(e.target)
        indeg[e.target] += 1
    queue = [n for n in ids if indeg[n] == 0]
    processed = 0
    while queue:
        n = queue.pop()
        processed += 1
        for m in adj[n]:
            indeg[m] -= 1
            if indeg[m] == 0:
                queue.append(m)
    if processed != len(ids):
        return next(n for n in ids if indeg[n] > 0)
    return None


# ── 1. the model is a node, bridged into the firings around it ───────────────


@pytest.mark.asyncio
async def test_model_node_exists_and_is_bridged(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESSX_GHX_UNFOLD", "1")
    _r, graph = await _run_and_load(
        tmp_path,
        processors=[StarPassthrough()],
        responses=[_tool_turn("c"), "done"],
        tools=[add_tool],
    )
    model_nodes = [n for n in graph.nodes if n.hook == "model"]
    assert len(model_nodes) == 2, "one node per non-skipped provider call"
    assert all(n.static_node_id == "model:MockProvider" for n in model_nodes)
    assert all(n.graphed for n in model_nodes), "a model call is a named node, not UNGRAPHED"
    # Distinct invocations are distinct nodes.
    assert len({n.id for n in model_nodes}) == 2

    by_id = _by_id(graph)
    ctrl = [e for e in graph.edges if e.edge_type == _CTRL]
    incoming = [e for e in ctrl if by_id[e.target].hook == "model"]
    outgoing = [e for e in ctrl if by_id[e.source].hook == "model"]
    assert incoming and all(by_id[e.source].hook == "before_model" for e in incoming)
    assert outgoing and all(by_id[e.target].hook == "after_model" for e in outgoing)
    # Every edge points forward — the DAG property the ordinal buys.
    for e in graph.edges:
        assert by_id[e.source].ordinal < by_id[e.target].ordinal


# ── 2. the data plane exists on a run that touches no slot ───────────────────


@pytest.mark.asyncio
async def test_message_data_edges_exist_without_any_slot(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESSX_GHX_UNFOLD", "1")
    _r, graph = await _run_and_load(
        tmp_path,
        processors=[StarPassthrough()],
        responses=[_tool_turn("c"), "done"],
        tools=[add_tool],
    )
    assert _data_edges(graph, "slot") == [], "this run writes no slot — the old plane is empty"
    msg_edges = _data_edges(graph, "message")
    assert msg_edges, "message plane produced no data edges"
    assert all((e.metadata or {}).get("slot_key", "").startswith("msg:") for e in msg_edges)
    assert _find_cycle_node(graph) is None


# ── 3. the two edges that carry a task's causality ───────────────────────────


@pytest.mark.asyncio
async def test_tool_to_model_and_model_to_model_edges(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESSX_GHX_UNFOLD", "1")
    _r, graph = await _run_and_load(
        tmp_path,
        processors=[StarPassthrough()],
        responses=[_tool_turn("c"), "done"],
        tools=[add_tool],
    )
    by_id = _by_id(graph)
    pairs = {(by_id[e.source].hook, by_id[e.target].hook) for e in _data_edges(graph)}
    assert ("tool", "model") in pairs, "the tool's result message must reach the model that read it"
    assert ("model", "model") in pairs, "a reply must reach the model that read it back"

    # The tool→model edge carries the tool result message, by role.
    tool_to_model = [
        e for e in _data_edges(graph) if by_id[e.source].hook == "tool" and by_id[e.target].hook == "model"
    ]
    assert all(":tool" in (e.metadata or {}).get("slot_key", "") for e in tool_to_model)


# ── 4. data edges cross steps; control edges cannot ──────────────────────────


@pytest.mark.asyncio
async def test_data_edges_cross_steps(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESSX_GHX_UNFOLD", "1")
    _r, graph = await _run_and_load(
        tmp_path,
        processors=[StarPassthrough()],
        responses=[_tool_turn("c"), _tool_turn("d"), "done"],
        tools=[add_tool],
    )
    by_id = _by_id(graph)
    crossing = [e for e in _data_edges(graph) if by_id[e.source].step != by_id[e.target].step]
    assert crossing, "the message plane must reach across steps — this is what the cone needs"
    ctrl_crossing = [
        e for e in graph.edges if e.edge_type == _CTRL and by_id[e.source].hook != by_id[e.target].hook
    ]
    # Control edges only ever bridge adjacent firings of one call (before_tool→tool,
    # tool→after_tool, before_model→model, model→after_model) — never a step apart.
    assert all(by_id[e.source].step == by_id[e.target].step for e in ctrl_crossing)


# ── 5. re-assembly is carriage, not authorship ───────────────────────────────


@pytest.mark.asyncio
async def test_reassembler_does_not_steal_authorship(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESSX_GHX_UNFOLD", "1")
    _r, graph = await _run_and_load(
        tmp_path,
        processors=[Reassembler()],
        responses=[_tool_turn("c"), "done"],
        tools=[add_tool],
    )
    by_id = _by_id(graph)
    writers = {by_id[e.source].hook for e in _data_edges(graph)}
    assert "step_start" not in writers, "an assembler that creates nothing must author nothing"
    assert {"tool", "model"} & writers, "the real authors keep their edges"


@pytest.mark.asyncio
async def test_injected_message_is_authored_by_its_processor(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESSX_GHX_UNFOLD", "1")
    _r, graph = await _run_and_load(
        tmp_path,
        processors=[NoteInjector()],
        responses=["done"],
    )
    by_id = _by_id(graph)
    authored = [
        e
        for e in _data_edges(graph)
        if by_id[e.source].hook == "step_start" and by_id[e.target].hook == "model"
    ]
    assert authored, "a processor that creates a message must own the edge to its reader"
    assert all(":user" in (e.metadata or {}).get("slot_key", "") for e in authored)


# ── 6. the cone stops being degenerate ───────────────────────────────────────


@pytest.mark.asyncio
async def test_cone_reaches_tool_and_model_nodes(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESSX_GHX_UNFOLD", "1")
    _r, graph = await _run_and_load(
        tmp_path,
        processors=[StarPassthrough()],
        responses=[_tool_turn("c"), _tool_turn("d"), "done"],
        tools=[add_tool],
    )
    by_id = _by_id(graph)

    terminal_only = causal_cone(graph, terminal_node(graph), include_anchors=True)
    assert not any(by_id[n].hook in ("tool", "model") for n in terminal_only), (
        "TaskEndEvent carries no messages, so the terminal anchor alone still reaches nothing"
    )

    cone = causal_cone(graph, _cone_anchors(graph), include_anchors=True)
    hooks = {by_id[n].hook for n in cone}
    assert "tool" in hooks and "model" in hooks
    assert cone > terminal_only, "the model anchor only ever adds to the terminal cone"
    # It reaches back across steps, not just into its own firing.
    assert len({by_id[n].step for n in cone}) > 1


# ── 7. off by default ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_no_recording_without_the_flag(tmp_path, monkeypatch):
    monkeypatch.delenv("HARNESSX_GHX_UNFOLD", raising=False)
    journal = HarnessJournal(base_dir=str(tmp_path), export_jsonl=True, session_id="offsess", silent=True)
    config = HarnessConfig(
        tool_registry=make_registry(add_tool),
        tracer=journal,
        processors=[StarPassthrough()],
    )
    mc = ModelConfig(main=MockProvider(responses=[_tool_turn("c"), "done"]))
    result = await mc.agentic(config).run(BaseTask(description="q", max_steps=10))
    assert not unfolded_path(str(tmp_path), "offsess", result.run_id).exists()
    assert result.final_output
