# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""v6 M6a — the causal cone of a real run is materially smaller than the whole U.

This is the property that makes the query layer worth building: instead of serialising
a whole run and truncating it to a fixed character cap (the AEGIS Digester's
``_LLM_CRITIC_INPUT_CAP = 30_000``), a consumer asks for the causal cone of an anchor
and serialises only that.  Run a real multi-step harness with ``HARNESSX_GHX_UNFOLD``
on, read U back from disk, and compare the serialised size of an attribution cone
against the whole U — the cone must be much smaller, and well under 30,000 characters.
"""

from __future__ import annotations

import json
import os
import sys

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
from harnessx.graph.causal import (  # noqa: E402
    DATA,
    causal_cone,
    induced_subgraph,
)
from harnessx.graph.unfold import load_unfolded, unfolded_path, unfolded_records  # noqa: E402
from harnessx.tracing.journal import HarnessJournal  # noqa: E402

_CAP = 30_000  # the AEGIS Digester input cap the cone must stay well under


def _two_tool_turn(tag: str) -> dict:
    return {
        "content": tag,
        "tool_calls": [
            {"id": f"{tag}1", "name": "add", "input": {"a": 1, "b": 1}},
            {"id": f"{tag}2", "name": "add", "input": {"a": 2, "b": 2}},
        ],
    }


class MultiHookSlotIO(MultiHookProcessor):
    """Writes slot 'flow' on step_start, reads it on step_end — cross-step data flow
    on a DIFFERENT slot than the accumulator, i.e. noise the accumulator cone must
    exclude."""

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


class AccumProc(MultiHookProcessor):
    """Reads and rewrites slot 'acc' on every step_end → a reaching-definition chain
    that spans every step, so the cone of the last accumulation follows a real,
    multi-step data path rather than a degenerate single-firing chain."""

    def __init__(self, key: str = "acc"):
        super().__init__()
        self._key = key
        self._state = None

    async def on_task_start(self, event):
        self._state = event.state
        self._state.set_slot(self._key, "u_test", 0)  # the chain's first definition
        yield event

    async def on_step_end(self, event):
        if self._state is not None:
            prev = self._state.get_slot(self._key)  # read → links to the prior write
            self._state.set_slot(self._key, "u_test", prev)  # write → the next definition
        yield event


def _serialized_chars(graph) -> int:
    return sum(len(json.dumps(rec, ensure_ascii=False)) for rec in unfolded_records(graph))


@pytest.mark.asyncio
async def test_causal_cone_materially_smaller_than_whole_u(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESSX_GHX_UNFOLD", "1")
    # twelve two-tool-call steps → a whole U that itself exceeds the 30k cap, i.e. a
    # run the AEGIS Digester would have to truncate.
    responses = [_two_tool_turn(f"s{i}") for i in range(12)] + ["done"]
    journal = HarnessJournal(base_dir=str(tmp_path), export_jsonl=True, session_id="cone", silent=True)
    config = HarnessConfig(
        tool_registry=make_registry(add_tool),
        tracer=journal,
        processors=[MultiHookSlotIO(), AccumProc()],
    )
    result = (
        await ModelConfig(main=MockProvider(responses=responses))
        .agentic(config)
        .run(BaseTask(description="q", max_steps=30))
    )
    whole = load_unfolded(unfolded_path(str(tmp_path), "cone", result.run_id))
    assert len(whole.nodes) > 50, f"run too small to be a fair test: {len(whole.nodes)} nodes"

    # anchor on the last accumulation — its cone follows the 'acc' reaching-def chain
    # back across every step, a genuine multi-node attribution slice.
    accum_nodes = [n for n in whole.nodes if n.label == "AccumProc" and n.hook == "step_end"]
    assert accum_nodes, "accumulator recorded no step_end invocation"
    anchor = max(accum_nodes, key=lambda n: n.ordinal).id

    cone = causal_cone(whole, anchor)  # both observed edge kinds
    cone_u = induced_subgraph(whole, cone)
    data_cone = causal_cone(whole, anchor, edge_types=DATA)  # attribution cone only
    data_cone_u = induced_subgraph(whole, data_cone)

    whole_chars = _serialized_chars(whole)
    cone_chars = _serialized_chars(cone_u)
    data_chars = _serialized_chars(data_cone_u)

    # report the real numbers
    print(
        f"\n[causal-cone] whole U: {len(whole.nodes)} nodes / {whole_chars} chars | "
        f"full cone: {len(cone_u.nodes)} nodes / {cone_chars} chars ({cone_chars / whole_chars:.3f}) | "
        f"data cone: {len(data_cone_u.nodes)} nodes / {data_chars} chars ({data_chars / whole_chars:.3f})"
    )
    # the data (attribution) cone is a subset of the full cone, and smaller still
    assert data_cone <= cone
    assert data_chars <= cone_chars

    # the whole U really would be truncated; the cone comfortably fits
    assert whole_chars > _CAP, f"whole U ({whole_chars}) should exceed the cap for a fair test"
    assert cone_chars < _CAP, f"cone {cone_chars} chars exceeds the {_CAP} cap"
    # … a strict, material reduction, not the trace relabelled
    assert cone_chars < whole_chars * 0.5, (
        f"cone not materially smaller: {cone_chars} vs {whole_chars} ({cone_chars / whole_chars:.2%})"
    )
    assert 1 < len(cone_u.nodes) < len(whole.nodes), "cone should be a non-trivial proper subset"
    assert set(cone) < whole.node_ids()
