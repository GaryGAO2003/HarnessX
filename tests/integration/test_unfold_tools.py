# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""v6 M5 — tools on the unfolded graph U.

These run real harnesses with ``HARNESSX_GHX_UNFOLD`` on, read U back from disk,
and pin the M5 properties the downstream tool-relation query depends on:

  * a tool execution is one U node (``tool:<name>``, hook ``"tool"``), interleaved
    with the processors around it by the shared global ordinal, bridged by real
    ``before_tool → tool`` and ``tool → after_tool`` control edges;
  * U stays a DAG once tools are present, across multi-tool-call steps;
  * node ids stay unique when the same tool recurs across steps;
  * a ``spawn_subagent`` call adds exactly one inter-layer ``INVOKES`` edge that
    names the child by its actual run id;
  * the tool-relation projection emits only relations the trace supports — a
    tempting temporal sequence with no slot bridge yields nothing;
  * recording stays off by default.
"""

from __future__ import annotations

import os
import sys
from collections import Counter, defaultdict
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from fixtures.mock_provider import MockProvider  # noqa: E402
from fixtures.mock_tools import add_tool, echo_tool, make_registry  # noqa: E402

from harnessx import (  # noqa: E402
    BaseTask,
    HarnessConfig,
    ModelConfig,
    MultiHookProcessor,
)
from harnessx.graph.tool_relations import (  # noqa: E402
    project_tool_relation_names,
    project_tool_relations,
)
from harnessx.graph.types import EdgeType, parse_unfolded_id  # noqa: E402
from harnessx.graph.unfold import load_unfolded, unfolded_path  # noqa: E402
from harnessx.tools.spawn_subagent import spawn_subagent_tool  # noqa: E402
from harnessx.tracing.journal import HarnessJournal  # noqa: E402

_CTRL = EdgeType.OBSERVED_CONTROL.value
_DATA = EdgeType.OBSERVED_DATA.value


def _tool_turn(tag: str, name: str, inp: dict) -> dict:
    return {"content": tag, "tool_calls": [{"id": tag, "name": name, "input": inp}]}


def _two_tool_turn(tag: str) -> dict:
    return {
        "content": tag,
        "tool_calls": [
            {"id": f"{tag}1", "name": "add", "input": {"a": 1, "b": 1}},
            {"id": f"{tag}2", "name": "add", "input": {"a": 2, "b": 2}},
        ],
    }


# ── instrumentation ─────────────────────────────────────────────────────────


class StarPassthrough(MultiHookProcessor):
    """Fires on every hook as a pass-through — records a node on before/after_tool."""


class BridgeProc(MultiHookProcessor):
    """Writes a slot in ``after_tool`` and reads it in ``before_tool``.

    This is the ONLY shape that connects two tool calls on U: a slot written
    downstream of tool A is read upstream of tool B, so ``project_tool_relations``
    can trace A → B.  ``State`` is captured on task_start (later hook events carry
    no ``state``).
    """

    def __init__(self, key: str = "bridge"):
        super().__init__()
        self._key = key
        self._state = None

    async def on_task_start(self, event):
        self._state = event.state
        yield event

    async def on_before_tool(self, event):
        if self._state is not None:
            self._state.get_slot(self._key)
        yield event

    async def on_after_tool(self, event):
        if self._state is not None:
            self._state.set_slot(self._key, "u_test", event.result)
        yield event


class StepSlotIO(MultiHookProcessor):
    """Writes a slot on step_start, reads it on step_end — data edges that never
    touch the tool nodes (a tempting-but-unsupported source of tool relations)."""

    def __init__(self, key: str = "unrelated"):
        super().__init__()
        self._key = key
        self._state = None

    async def on_task_start(self, event):
        self._state = event.state
        yield event

    async def on_step_start(self, event):
        if self._state is not None:
            self._state.set_slot(self._key, "u_test", event.step_id)
        yield event

    async def on_step_end(self, event):
        if self._state is not None:
            self._state.get_slot(self._key)
        yield event


# ── helpers ─────────────────────────────────────────────────────────────────


async def _run_and_load(tmp_path, processors, responses, *, tools=None, session_id="usess"):
    journal = HarnessJournal(base_dir=str(tmp_path), export_jsonl=True, session_id=session_id, silent=True)
    config = HarnessConfig(
        tool_registry=make_registry(*(tools or [])),
        tracer=journal,
        processors=processors,
    )
    mc = ModelConfig(main=MockProvider(responses=responses))
    result = await mc.agentic(config).run(BaseTask(description="q", max_steps=10))
    path = unfolded_path(str(tmp_path), session_id, result.run_id)
    assert path.exists(), f"expected U at {path}"
    return result, load_unfolded(path)


def _find_cycle_node(graph):
    """Kahn detector over U's OBSERVED edges (INVOKES is inter-layer, excluded)."""
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


# ── 1. a tool is a node, interleaved and bridged by control edges ────────────


@pytest.mark.asyncio
async def test_tool_node_interleaves_and_is_bridged(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESSX_GHX_UNFOLD", "1")
    _r, graph = await _run_and_load(
        tmp_path,
        processors=[StarPassthrough()],
        responses=[_tool_turn("c", "add", {"a": 1, "b": 1}), "done"],
        tools=[add_tool],
    )
    tool_nodes = [n for n in graph.nodes if n.static_node_id == "tool:add"]
    assert len(tool_nodes) == 1
    t = tool_nodes[0]
    assert t.hook == "tool" and t.label == "add" and t.graphed
    # its id parses as an unfolded id whose ordinal is the tool node's ordinal
    assert parse_unfolded_id(t.id) == ("tool:add", t.ordinal)

    node_by_id = {n.id: n for n in graph.nodes}
    into = [e for e in graph.edges if e.edge_type == _CTRL and e.target == t.id]
    out = [e for e in graph.edges if e.edge_type == _CTRL and e.source == t.id]
    assert into, "no before_tool → tool control edge"
    assert out, "no tool → after_tool control edge"
    # the predecessor is a before_tool invocation with a STRICTLY LOWER ordinal…
    for e in into:
        pred = node_by_id[e.source]
        assert pred.hook == "before_tool"
        assert pred.ordinal < t.ordinal
    # …and the successor is an after_tool invocation with a strictly higher ordinal.
    for e in out:
        succ = node_by_id[e.target]
        assert succ.hook == "after_tool"
        assert succ.ordinal > t.ordinal


# ── 2. U is a DAG with tools present, over multi-tool-call steps ─────────────


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "responses",
    [
        [_tool_turn("c", "add", {"a": 1, "b": 1}), "done"],
        [_two_tool_turn("c"), "done"],
        [_two_tool_turn("a"), _two_tool_turn("b"), "done"],
    ],
)
async def test_dag_with_tools(tmp_path, monkeypatch, responses):
    monkeypatch.setenv("HARNESSX_GHX_UNFOLD", "1")
    _r, graph = await _run_and_load(tmp_path, processors=[BridgeProc()], responses=responses, tools=[add_tool])
    assert [n for n in graph.nodes if n.static_node_id == "tool:add"], "no tool node recorded"
    assert _find_cycle_node(graph) is None, "U contains a cycle with tools present"


# ── 3. node ids stay unique when the same tool recurs across steps ───────────


@pytest.mark.asyncio
async def test_tool_ids_unique_across_steps(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESSX_GHX_UNFOLD", "1")
    _r, graph = await _run_and_load(
        tmp_path,
        processors=[StarPassthrough()],
        responses=[_two_tool_turn("a"), _two_tool_turn("b"), "done"],
        tools=[add_tool],
    )
    tool_nodes = [n for n in graph.nodes if n.static_node_id == "tool:add"]
    # 2 steps × 2 add calls = 4 distinct invocations of the SAME tool node id base.
    assert len(tool_nodes) == 4, f"expected 4 tool invocations, got {len(tool_nodes)}"
    # the collapse trap is genuinely exercised: (tool:add, step) recurs.
    per_step = Counter((n.static_node_id, n.step) for n in tool_nodes)
    assert any(c > 1 for c in per_step.values()), "trap not exercised — no tool recurred within a step"
    ids = [n.id for n in graph.nodes]
    dupes = [i for i, c in Counter(ids).items() if c > 1]
    assert not dupes, f"nodes share an id, e.g. {dupes[:3]}"


# ── 4. spawn_subagent adds exactly one INVOKES edge naming the child ─────────


@pytest.mark.asyncio
async def test_spawn_adds_one_invokes_edge(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESSX_GHX_UNFOLD", "1")
    responses = [
        {"tool_calls": [{"id": "p1", "name": "spawn_subagent", "input": {"task": "echo", "wait": True}}]},
        {"tool_calls": [{"id": "c1", "name": "echo", "input": {"message": "hi-child"}}]},
        "child done",
        "parent done",
    ]
    journal = HarnessJournal(base_dir=str(tmp_path), export_jsonl=True, session_id="spawnsess", silent=True)
    registry = make_registry(echo_tool)
    registry.register(spawn_subagent_tool)
    config = HarnessConfig(tool_registry=registry, tracer=journal, processors={})
    config.init_workspace = False
    result = (
        await ModelConfig(main=MockProvider(responses=responses))
        .agentic(config)
        .run(BaseTask(description="q", max_steps=10))
    )
    assert result.exit_reason == "done"

    parent = load_unfolded(unfolded_path(str(tmp_path), "spawnsess", result.run_id))
    # exactly one inter-layer edge…
    assert len(parent.invokes) == 1
    iv = parent.invokes[0]
    # …sourced at the parent's spawn_subagent TOOL node…
    src = {n.id: n for n in parent.nodes}.get(iv.source)
    assert src is not None and src.static_node_id == "tool:spawn_subagent"
    # …and it names the child by a run id that is NOT a node in this U, ≠ parent…
    assert iv.child_run_id and iv.child_run_id != result.run_id
    assert iv.child_run_id not in parent.node_ids()
    # …which resolves to a REAL child run: a child U exists with exactly that id.
    child_files = list(Path(tmp_path).rglob(f"{iv.child_run_id}_unfolded.jsonl"))
    assert child_files, f"INVOKES target {iv.child_run_id} has no child U file"
    assert load_unfolded(child_files[0]).run_id == iv.child_run_id


# ── 5. the projection emits only trace-supported relations ───────────────────


@pytest.mark.asyncio
async def test_projection_emits_data_mediated_relation(tmp_path, monkeypatch):
    """A slot written after tool A and read before tool B → A → B is derivable."""
    monkeypatch.setenv("HARNESSX_GHX_UNFOLD", "1")
    _r, graph = await _run_and_load(
        tmp_path,
        processors=[BridgeProc("bridge")],
        responses=[
            _tool_turn("s0", "add", {"a": 1, "b": 1}),  # step 0: add, after_tool writes slot
            _tool_turn("s1", "echo", {"message": "x"}),  # step 1: before_tool reads it, then echo
            "done",
        ],
        tools=[add_tool, echo_tool],
    )
    rels = project_tool_relations(graph)
    names = {(r.source_tool, r.target_tool) for r in rels}
    assert ("add", "echo") in names, f"expected add → echo, got {names}"
    assert ("echo", "add") not in names, "data flows forward only; echo → add is unsupported"
    add_echo = next(r for r in rels if (r.source_tool, r.target_tool) == ("add", "echo"))
    assert "bridge" in add_echo.via_slots, f"witness should cross slot 'bridge', got {add_echo.via_slots}"


@pytest.mark.asyncio
async def test_projection_refuses_unsupported_relation(tmp_path, monkeypatch):
    """Two tools in temporal sequence, but the only data flow is step_start→step_end
    (never touching the tools).  The tempting 'A ran before B, so A feeds B' is NOT
    emitted — the trace does not support it."""
    monkeypatch.setenv("HARNESSX_GHX_UNFOLD", "1")
    _r, graph = await _run_and_load(
        tmp_path,
        processors=[StepSlotIO("unrelated")],
        responses=[
            _tool_turn("s0", "add", {"a": 1, "b": 1}),
            _tool_turn("s1", "echo", {"message": "x"}),
            "done",
        ],
        tools=[add_tool, echo_tool],
    )
    # both tools are present, and real data edges exist — the trap is set…
    tool_nodes = [n for n in graph.nodes if n.static_node_id.startswith("tool:")]
    assert len(tool_nodes) == 2
    assert graph.edges_of_type(_DATA), "expected step_start→step_end data edges to exist"
    # …but none of them bridge the tools, so no tool relation is projected.
    assert project_tool_relations(graph) == [], f"invented: {project_tool_relation_names(graph)}"


# ── 6. recording is off by default ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_disabled_by_default(tmp_path, monkeypatch):
    monkeypatch.delenv("HARNESSX_GHX_UNFOLD", raising=False)
    journal = HarnessJournal(base_dir=str(tmp_path), export_jsonl=True, session_id="off", silent=True)
    config = HarnessConfig(tool_registry=make_registry(add_tool), tracer=journal, processors=[StarPassthrough()])
    result = (
        await ModelConfig(main=MockProvider(responses=[_tool_turn("c", "add", {"a": 1, "b": 1}), "done"]))
        .agentic(config)
        .run(BaseTask(description="q", max_steps=10))
    )
    assert not unfolded_path(str(tmp_path), "off", result.run_id).exists()
