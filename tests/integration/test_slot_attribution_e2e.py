# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""v6 M3 end-to-end: the real harness path attributes slot writes to graph nodes.

Confirms the resolver installed by ``Harness.run`` around ``run_loop`` lets the
State slot API record the acting processor's REAL graph node id (from the M2a
binding), and that an ``extra_processors`` injection records as ``UNGRAPHED``.
"""

from __future__ import annotations

import os
import sys

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
from harnessx.graph.executor import UNGRAPHED  # noqa: E402


@pytest.fixture(autouse=True)
def _no_owner_leak():
    from harnessx.core.runtime import _OWNER_LOCK, _OWNERS

    with _OWNER_LOCK:
        before = set(_OWNERS.keys())
    yield
    with _OWNER_LOCK:
        for key in [k for k in _OWNERS if k not in before]:
            del _OWNERS[key]


class _SlotWriter(MultiHookProcessor):
    def __init__(self):
        self.captured = []

    async def on_task_start(self, event):
        event.state.set_slot("witness", "test", "v")
        self.captured.append(event.state)
        yield event


async def test_graph_member_write_records_its_real_node_id():
    writer = _SlotWriter()
    cfg = HarnessConfig(tool_registry=make_registry(add_tool), processors=[writer])
    harness = ModelConfig(main=MockProvider(responses=["done"])).agentic(cfg)

    node_id = next(nid for nid, _, proc in harness._rt.proc_node_binding if proc is writer)
    assert node_id is not UNGRAPHED  # a config processor is a graph member

    await harness.run(BaseTask(description="hi", max_steps=3))

    state = writer.captured[-1]
    (access,) = state.slot_provenance["witness"].writers
    assert access.actor == node_id  # the processor's REAL graph node id
    assert access.step == 0  # written at task_start


async def test_extra_processor_write_records_ungraphed():
    writer = _SlotWriter()
    cfg = HarnessConfig(tool_registry=make_registry(add_tool), processors=[])
    harness = Harness(
        ModelConfig(main=MockProvider(responses=["done"])),
        cfg,
        extra_processors={"*": [writer]},
    )
    assert any(nid is UNGRAPHED for nid, _, proc in harness._rt.proc_node_binding if proc is writer)

    await harness.run(BaseTask(description="hi", max_steps=3))

    state = writer.captured[-1]
    (access,) = state.slot_provenance["witness"].writers
    assert access.actor is UNGRAPHED
