# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""v6 M4 — the unfolded graph U (per-invocation record of what actually ran).

These run real harnesses end-to-end with ``HARNESSX_GHX_UNFOLD`` on, read the
persisted U back from disk, and pin the properties the later ancestors-query
module depends on: U is a DAG, ``LOOP_BACK`` never appears, node count equals the
number of invocations actually made (instrumented independently by spy
processors), OBSERVED_DATA edges match ``State.slot_provenance``, ungraphed
invocations survive, the genotype hash never moves, and U round-trips through
JSONL.
"""

from __future__ import annotations

import os
import sys
from collections import Counter, defaultdict

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from fixtures.mock_provider import MockProvider  # noqa: E402
from fixtures.mock_tools import add_tool, make_registry  # noqa: E402

from harnessx import (  # noqa: E402
    BaseTask,
    Harness,
    HarnessConfig,
    ModelConfig,
    MultiHookProcessor,
)
from harnessx.graph.identity import genotype_hash  # noqa: E402
from harnessx.graph.snapshot import to_graph  # noqa: E402
from harnessx.graph.types import EdgeType, parse_unfolded_id  # noqa: E402
from harnessx.graph.unfold import load_unfolded, unfolded_path  # noqa: E402
from harnessx.tracing.journal import HarnessJournal  # noqa: E402


def _two_tool_turn(tag: str) -> dict:
    return {
        "content": tag,
        "tool_calls": [
            {"id": f"{tag}1", "name": "add", "input": {"a": 1, "b": 1}},
            {"id": f"{tag}2", "name": "add", "input": {"a": 2, "b": 2}},
        ],
    }


_TWO_TOOLS_THEN_DONE = [_two_tool_turn("c"), "done"]

# Several steps, each a two-tool-call step: a "*" processor fires many times per
# step, and the same (node, step) recurs across steps — the shape that collapses
# under a naive ``ordinal = int(step)`` identity.
_MULTI_STEP_MULTI_TOOL = [_two_tool_turn("a"), _two_tool_turn("b"), "done"]


# ── instrumentation ─────────────────────────────────────────────────────────


class CountingProc(MultiHookProcessor):
    """Counts every ``process`` call — one per invocation, independent of U.

    Overriding ``process`` (not an ``on_*`` handler) counts EVERY hook firing the
    processor takes part in, exactly the granularity U records a node at.
    """

    def __init__(self, counter: list):
        super().__init__()
        self._counter = counter

    async def process(self, event):
        self._counter[0] += 1
        async for out in super().process(event):
            yield out


class MultiHookSlotIO(MultiHookProcessor):
    """Fires on several hooks; writes a slot on step_start, reads it on step_end.

    Captures the live ``State`` from the task_start event (later hook events carry
    no ``state``), then drives slot flow across DISTINCT invocations of the SAME
    node in different steps — the shape a naive ``(node, step)`` identity mangles.
    """

    def __init__(self, key: str = "flow"):
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


class WriterTS(MultiHookProcessor):
    """Writes a slot within the task_start firing (same-firing data edge source)."""

    def __init__(self, key: str = "shared"):
        super().__init__()
        self._key = key

    async def on_task_start(self, event):
        event.state.set_slot(self._key, "u_test", 42)
        yield event


class ReaderTS(MultiHookProcessor):
    """Reads a slot within the task_start firing (same-firing data edge target)."""

    def __init__(self, key: str = "shared"):
        super().__init__()
        self._key = key

    async def on_task_start(self, event):
        event.state.get_slot(self._key)
        yield event


# ── helpers ─────────────────────────────────────────────────────────────────


async def _run_and_load(
    tmp_path,
    processors,
    responses,
    *,
    tools=None,
    extra_processors=None,
    session_id="usess",
    task=None,
):
    journal = HarnessJournal(base_dir=str(tmp_path), export_jsonl=True, session_id=session_id, silent=True)
    config = HarnessConfig(
        tool_registry=make_registry(*(tools or [])),
        tracer=journal,
        processors=processors,
    )
    mc = ModelConfig(main=MockProvider(responses=responses))
    harness = Harness(mc, config, extra_processors=extra_processors) if extra_processors else mc.agentic(config)
    result = await harness.run(task or BaseTask(description="q", max_steps=10))
    path = unfolded_path(str(tmp_path), session_id, result.run_id)
    assert path.exists(), f"expected U at {path}"
    return result, load_unfolded(path)


def _find_cycle_node(graph):
    """Return a node id involved in a cycle, or None if U is acyclic (Kahn)."""
    ids = graph.node_ids()
    adj = defaultdict(list)
    indeg = {nid: 0 for nid in ids}
    for e in graph.edges:
        # every edge endpoint must be a real node
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


# ── 1. distinct invocations are never the same node (hard requirement 1) ─────


@pytest.mark.asyncio
async def test_invocation_ids_are_unique(tmp_path, monkeypatch):
    """Hard requirement 1 — the one that actually matters, and the one the DAG
    and count tests do NOT cover.

    A collapsed identity does not announce itself with a cycle: control edges
    never cross firing boundaries (so two collapsed invocations get no edge, no
    self-loop), and the node list is appended unconditionally (so the count still
    matches).  It shows up only as two distinct invocations sharing one id — which
    would silently corrupt the later ``ancestors(U, v)`` query.  So assert ids are
    pairwise distinct, over exactly the shapes that collapse under a naive
    ``ordinal = int(step)`` identity:

      * a ``"*"``-registered processor fires on every hook → several firings/step;
      * each two-tool-call step fires the tool hooks twice more;
      * the run spans several steps.

    This is the test that must fail when the ordinal is replaced by the step.
    """
    monkeypatch.setenv("HARNESSX_GHX_UNFOLD", "1")
    _result, graph = await _run_and_load(
        tmp_path,
        processors=[MultiHookSlotIO()],
        responses=_MULTI_STEP_MULTI_TOOL,
        tools=[add_tool],
    )
    # The scenario must actually exercise the trap: some static node fires more
    # than once within a single step — else the test would pass vacuously even
    # under the naive identity.
    per_node_step = Counter((n.static_node_id, n.step) for n in graph.nodes)
    assert any(c > 1 for c in per_node_step.values()), (
        "scenario fired no node more than once in a step — the collapse trap was not exercised"
    )
    ids = [n.id for n in graph.nodes]
    dupes = [i for i, c in Counter(ids).items() if c > 1]
    assert not dupes, f"{len(ids) - len(set(ids))} distinct invocations share an id, e.g. {dupes[:3]}"


# ── 2. U is a DAG (acyclicity of the EMITTED EDGES) ──────────────────────────
#
# This guards the edge-construction invariant: control edges stay within a
# firing and point forward, data edges point forward, nothing emits LOOP_BACK —
# so the emitted edge set is acyclic.  It is a real Kahn detector over several
# shapes, NOT a hand-picked example.  It deliberately does NOT prove id
# uniqueness: under a collapsed identity these shapes still produce no cycle (the
# collapsed pair shares an id but has no edge between them).  Uniqueness is
# ``test_invocation_ids_are_unique`` above; do not read this as that guarantee.


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "responses",
    [
        ["done"],
        [{"content": "x", "tool_calls": [{"id": "c1", "name": "add", "input": {"a": 1, "b": 1}}]}, "done"],
        _TWO_TOOLS_THEN_DONE,
    ],
)
async def test_unfolded_is_a_dag(tmp_path, monkeypatch, responses):
    monkeypatch.setenv("HARNESSX_GHX_UNFOLD", "1")
    _result, graph = await _run_and_load(
        tmp_path,
        processors=[MultiHookSlotIO()],
        responses=responses,
        tools=[add_tool],
    )
    assert graph.nodes, "U recorded no invocations"
    assert _find_cycle_node(graph) is None, "U contains a cycle"


# ── 2. LOOP_BACK never appears in U, however many rounds run ─────────────────


@pytest.mark.asyncio
async def test_loop_back_never_in_u(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESSX_GHX_UNFOLD", "1")
    # Force several rounds: three tool-call turns before the final answer.
    responses = [
        {"content": str(i), "tool_calls": [{"id": f"c{i}", "name": "add", "input": {"a": i, "b": i}}]} for i in range(3)
    ] + ["done"]
    _result, graph = await _run_and_load(
        tmp_path, processors=[MultiHookSlotIO()], responses=responses, tools=[add_tool]
    )
    banned = {EdgeType.LOOP_BACK.value, "loop_back"}
    assert not [e for e in graph.edges if e.edge_type in banned]
    # Only the two observed edge types are allowed to exist at all.
    assert {e.edge_type for e in graph.edges} <= {
        EdgeType.OBSERVED_CONTROL.value,
        EdgeType.OBSERVED_DATA.value,
    }


# ── 3. node count == number of invocations actually made ─────────────────────


@pytest.mark.asyncio
async def test_node_count_matches_invocations(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESSX_GHX_UNFOLD", "1")
    counter = [0]
    _result, graph = await _run_and_load(
        tmp_path,
        processors=[CountingProc(counter)],
        responses=_TWO_TOOLS_THEN_DONE,
        tools=[add_tool],
    )
    assert counter[0] > 0
    # v6 M5 / M12: U also carries a node per TOOL execution (hook == "tool") and per
    # MODEL call (hook == "model"); the spy counts only PROCESSOR invocations, so
    # split the three before comparing.
    proc_nodes = [n for n in graph.nodes if n.hook not in ("tool", "model")]
    tool_nodes = [n for n in graph.nodes if n.hook == "tool"]
    model_nodes = [n for n in graph.nodes if n.hook == "model"]
    # Two independent facts, both keyed to the same spy-measured invocation count:
    #  - processor node RECORDS == invocations  → no invocation was dropped;
    #  - distinct node IDS == records (over ALL nodes, tools included) → no two
    #    invocations collapsed onto one id (hard requirement 1).  The second is what
    #    fails under a naive ``ordinal = int(step)`` identity; the first alone would
    #    survive it, because ``_nodes`` is appended unconditionally.
    assert len(proc_nodes) == counter[0], f"U has {len(proc_nodes)} processor node records but {counter[0]} invocations"
    # _TWO_TOOLS_THEN_DONE is one step with two add calls → two tool nodes.
    assert len(tool_nodes) == 2, f"expected 2 tool nodes, got {len(tool_nodes)}"
    assert len(model_nodes) == 2, f"expected 2 model nodes (one per step), got {len(model_nodes)}"
    assert len(graph.node_ids()) == len(graph.nodes), (
        f"U has {len(graph.node_ids())} distinct ids for {len(graph.nodes)} records — invocations collapsed"
    )


# ── 4. every OBSERVED_DATA edge matches provenance; none invented ────────────


@pytest.mark.asyncio
async def test_data_edges_match_provenance(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESSX_GHX_UNFOLD", "1")
    result, graph = await _run_and_load(
        tmp_path,
        # same-firing pair (WriterTS→ReaderTS on task_start) + cross-firing
        # pair (MultiHookSlotIO step_start→step_end).
        processors=[WriterTS(), ReaderTS(), MultiHookSlotIO()],
        responses=_TWO_TOOLS_THEN_DONE,
        tools=[add_tool],
    )
    prov = result.resume_state.slot_provenance
    all_data_edges = graph.edges_of_type(EdgeType.OBSERVED_DATA.value)
    assert all_data_edges, "expected at least one OBSERVED_DATA edge"
    # v6 M12: the provenance cross-check is the SLOT plane's invariant.  Message-plane
    # edges are deliberately exempt — a message has no slot_provenance record, and the
    # dispatcher/runloop sites that log them are themselves the primary observation.
    data_edges = [e for e in all_data_edges if (e.metadata or {}).get("plane", "slot") == "slot"]
    assert data_edges, "expected at least one slot-plane OBSERVED_DATA edge"
    assert all(
        not (e.metadata or {}).get("slot_key", "").startswith("msg:") for e in data_edges
    ), "a message key leaked into the slot plane"

    node_by_id = {n.id: n for n in graph.nodes}

    def _actor_key(actor):
        if isinstance(actor, str):
            return actor
        if actor is None:
            return None
        return "UNGRAPHED"

    for e in data_edges:
        key = e.metadata["slot_key"]
        w = e.metadata["writer"]
        r = e.metadata["reader"]
        p = prov.get(key)
        assert p is not None, f"slot {key} absent from provenance"
        # writer/reader (static_node_id, step) must be REAL provenance accesses
        assert any(_actor_key(a.actor) == w["static_node_id"] and a.step == w["step"] for a in p.writers), (
            f"invented writer for {key}: {w}"
        )
        assert any(_actor_key(a.actor) == r["static_node_id"] and a.step == r["step"] for a in p.readers), (
            f"invented reader for {key}: {r}"
        )
        # endpoints are real invocation nodes, and the edge points strictly
        # forward in the invocation ordering (never backwards).
        assert e.source in node_by_id and e.target in node_by_id
        assert parse_unfolded_id(e.source)[1] < parse_unfolded_id(e.target)[1], (
            f"data edge points backwards: {e.source} -> {e.target}"
        )


# ── 5. UNGRAPHED invocations appear in U (delivered via extra_processors) ─────


@pytest.mark.asyncio
async def test_ungraphed_invocations_present(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESSX_GHX_UNFOLD", "1")
    extra = CountingProc([0])  # injected post-config → dispatched but ungraphed
    _result, graph = await _run_and_load(
        tmp_path,
        processors=[MultiHookSlotIO()],
        responses=["done"],
        extra_processors={"step_end": [extra]},
    )
    ungraphed = [n for n in graph.nodes if not n.graphed]
    assert ungraphed, "no ungraphed invocation recorded"
    assert all(n.static_node_id == "UNGRAPHED" for n in ungraphed)
    # and it was not dropped: at least one ungraphed node fired on step_end.
    assert any(n.hook == "step_end" for n in ungraphed)


# ── 6. recording U does not move the genotype hash ───────────────────────────


@pytest.mark.asyncio
async def test_genotype_hash_untouched(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESSX_GHX_UNFOLD", "1")
    journal = HarnessJournal(base_dir=str(tmp_path), export_jsonl=True, session_id="g", silent=True)
    config = HarnessConfig(
        tool_registry=make_registry(add_tool),
        tracer=journal,
        processors=[MultiHookSlotIO()],
    )
    before = genotype_hash(to_graph(config))
    mc = ModelConfig(main=MockProvider(responses=_TWO_TOOLS_THEN_DONE))
    harness = mc.agentic(config)
    await harness.run(BaseTask(description="q", max_steps=10))
    after = genotype_hash(to_graph(config))
    assert after == before


# ── 7. U round-trips through JSONL ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_u_round_trips(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESSX_GHX_UNFOLD", "1")
    _result, graph = await _run_and_load(
        tmp_path,
        processors=[WriterTS(), ReaderTS(), MultiHookSlotIO()],
        responses=_TWO_TOOLS_THEN_DONE,
        tools=[add_tool],
    )
    # Re-load the very same file: identical nodes and edges, in order.
    path = unfolded_path(str(tmp_path), "usess", _result.run_id)
    again = load_unfolded(path)
    assert again.run_id == graph.run_id
    assert again.session_id == graph.session_id
    assert again.nodes == graph.nodes
    assert again.edges == graph.edges


# ── 8. recording is off by default (no file, no cost) ────────────────────────


@pytest.mark.asyncio
async def test_disabled_by_default(tmp_path, monkeypatch):
    monkeypatch.delenv("HARNESSX_GHX_UNFOLD", raising=False)
    journal = HarnessJournal(base_dir=str(tmp_path), export_jsonl=True, session_id="off", silent=True)
    config = HarnessConfig(
        tool_registry=make_registry(add_tool),
        tracer=journal,
        processors=[MultiHookSlotIO()],
    )
    mc = ModelConfig(main=MockProvider(responses=["done"]))
    harness = mc.agentic(config)
    result = await harness.run(BaseTask(description="q"))
    assert not unfolded_path(str(tmp_path), "off", result.run_id).exists()
