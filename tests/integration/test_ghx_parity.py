# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""GHX v6 M2b parity gate.

The graph executor (``harnessx.graph.executor``) answers "which processors run
at hook H, in what order" by reading the graph's ``EXECUTES_BEFORE`` chains plus
the ``"*"``-bucket concatenation rule.  This module is the gate for the whole v6
effort: for a range of configs it asserts the executor's ordered list is
identical **by instance identity** to the runloop's legacy ``get_procs`` for all
8 dispatch hooks, and that an end-to-end run produces a byte-identical
hook-fire sequence with the flag off vs on.
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
from harnessx.bundles import coding, context, control  # noqa: E402
from harnessx.core.builder import HarnessBuilder  # noqa: E402
from harnessx.core.processor import PROCESSOR_HOOK_NAMES, before_model, on_step_end  # noqa: E402
from harnessx.graph.executor import (  # noqa: E402
    UNGRAPHED,
    NonDispatchHookError,
    build_graph_executor,
)

_FLAG = "HARNESSX_GHX_RUNTIME"


@pytest.fixture(autouse=True)
def _no_owner_leak():
    """Release owner-registry claims this module's MultiHookProcessors make.

    Building a Harness claims every MHP in the global single-owner registry;
    these tests never call ``cleanup()``, so without this the claims would
    linger and surface as cross-test state in whatever runs next.
    """
    from harnessx.core.runtime import _OWNER_LOCK, _OWNERS

    with _OWNER_LOCK:
        before = set(_OWNERS.keys())
    yield
    with _OWNER_LOCK:
        for key in [k for k in _OWNERS if k not in before]:
            del _OWNERS[key]


# ── legacy get_procs replica (runloop.py:154-158, flag OFF) ──────────────────


def _legacy_get_procs(processors: dict, key: str) -> list:
    star = processors.get("*", [])
    specific = processors.get(key)
    return star + specific if specific else star


def _assert_identical(legacy: list, graph: list, hook: str, label: str) -> None:
    assert len(legacy) == len(graph), (
        f"[{label}] hook={hook}: length {len(graph)} != legacy {len(legacy)} "
        f"(graph={[type(p).__name__ for p in graph]}, "
        f"legacy={[type(p).__name__ for p in legacy]})"
    )
    for i, (a, b) in enumerate(zip(legacy, graph)):
        assert a is b, (
            f"[{label}] hook={hook} pos={i}: {type(b).__name__} is not the "
            f"same instance the runloop dispatches ({type(a).__name__})"
        )


# ── explicit processors that exercise the three divergence points ────────────


class _AfterFirst(MultiHookProcessor):
    """step_end, singleton_group 'first', order 0."""

    _hook = "step_end"
    _singleton_group = "first"
    _order = 0

    async def on_step_end(self, event):
        yield event


class _AfterSecond(MultiHookProcessor):
    """step_end, must run AFTER 'first' even though registered before it."""

    _hook = "step_end"
    _singleton_group = "second"
    _order = 0
    _after = ("first",)

    async def on_step_end(self, event):
        yield event


class _SameGroupLo(MultiHookProcessor):
    _hook = "before_model"
    _singleton_group = "shared"
    _order = 10

    async def on_before_model(self, event):
        yield event


class _SameGroupHi(MultiHookProcessor):
    _hook = "before_model"
    _singleton_group = "shared"
    _order = 20

    async def on_before_model(self, event):
        yield event


class _StarSpy(MultiHookProcessor):
    """No ``_hook`` → bucket ``"*"``; fires on every hook it overrides."""

    async def on_step_end(self, event):
        yield event

    async def on_before_model(self, event):
        yield event


class _StepEndOnly(MultiHookProcessor):
    """``_hook`` set → genuinely lives in the ``step_end`` bucket, not ``"*"``."""

    _hook = "step_end"

    async def on_step_end(self, event):
        yield event


# ── config factories (each returns a fresh HarnessConfig) ────────────────────


def _cfg_context():
    return (HarnessBuilder() | context).build()


def _cfg_context_coding():
    return (HarnessBuilder() | context | coding).build()


def _cfg_context_control():
    return (HarnessBuilder() | context | control).build()


def _cfg_coding():
    return (HarnessBuilder() | coding).build()


def _cfg_control():
    return (HarnessBuilder() | control).build()


def _cfg_after_ordering():
    # Registered [_AfterSecond, _AfterFirst]; _after forces execution [_First, _Second].
    return HarnessConfig(
        tool_registry=make_registry(add_tool),
        processors=[_AfterSecond(), _AfterFirst()],
    )


def _cfg_same_singleton_group():
    return HarnessConfig(
        tool_registry=make_registry(add_tool),
        processors=[_SameGroupHi(), _SameGroupLo()],
    )


def _cfg_star_concatenation():
    # _StarSpy → "*" bucket; _StepEndOnly → step_end bucket.  get_procs("step_end")
    # must be [ *-bucket ..., step_end-bucket ... ] — star FIRST.
    return HarnessConfig(
        tool_registry=make_registry(add_tool),
        processors=[_StepEndOnly(), _StarSpy()],
    )


def _cfg_star_over_bundle():
    cfg = (HarnessBuilder() | context | coding).build()
    cfg.add_runtime_reg(_StarSpy())
    return cfg


_CONFIG_FACTORIES = [
    ("context", _cfg_context),
    ("context|coding", _cfg_context_coding),
    ("context|control", _cfg_context_control),
    ("coding", _cfg_coding),
    ("control", _cfg_control),
    ("after_ordering", _cfg_after_ordering),
    ("same_singleton_group", _cfg_same_singleton_group),
    ("star_concatenation", _cfg_star_concatenation),
    ("star_over_bundle", _cfg_star_over_bundle),
]


# ── parity: identical by instance identity across all 8 dispatch hooks ───────


@pytest.mark.parametrize("label,factory", _CONFIG_FACTORIES, ids=[c[0] for c in _CONFIG_FACTORIES])
def test_executor_matches_get_procs_by_identity(label, factory):
    harness = ModelConfig(main=MockProvider(responses=["done"])).agentic(factory())
    binding = harness._rt.proc_node_binding
    assert binding, f"[{label}] runtime recorded an empty node→instance binding"

    executor = build_graph_executor(harness.config, binding)
    for hook in PROCESSOR_HOOK_NAMES:
        legacy = _legacy_get_procs(harness._rt.processors, hook)
        graph = executor.procs_for(hook)
        _assert_identical(legacy, graph, hook, label)


def test_after_ordering_comes_from_the_graph():
    """_after must reorder a bucket against registration order — via the graph."""
    harness = ModelConfig(main=MockProvider(responses=["done"])).agentic(_cfg_after_ordering())
    executor = build_graph_executor(harness.config, harness._rt.proc_node_binding)

    names = [type(p).__name__ for p in executor.procs_for("step_end")]
    assert names == ["_AfterFirst", "_AfterSecond"], names  # NOT registration order
    _assert_identical(
        _legacy_get_procs(harness._rt.processors, "step_end"),
        executor.procs_for("step_end"),
        "step_end",
        "after_ordering",
    )


def test_star_bucket_is_concatenated_first():
    """The ``"*"`` bucket is prepended to the hook-specific bucket, in that order."""
    harness = ModelConfig(main=MockProvider(responses=["done"])).agentic(_cfg_star_concatenation())
    executor = build_graph_executor(harness.config, harness._rt.proc_node_binding)

    step_end = executor.procs_for("step_end")
    step_end_names = [type(p).__name__ for p in step_end]
    assert step_end_names == ["_StarSpy", "_StepEndOnly"], step_end_names
    # before_model has only the star processor (the step_end-bucket one is absent).
    before_model = executor.procs_for("before_model")
    assert [type(p).__name__ for p in before_model] == ["_StarSpy"]
    _assert_identical(
        _legacy_get_procs(harness._rt.processors, "step_end"),
        step_end,
        "step_end",
        "star_concatenation",
    )


# ── extra_processors: dispatched but ungraphed, appended to bucket end ───────


def _make_marker_proc():
    @on_step_end
    async def _extra(event):
        yield event

    return _extra


def _make_star_marker_proc():
    @before_model
    async def _star_extra(event):
        yield event

    return _star_extra


def test_extra_processors_match_get_procs_by_identity():
    """extra_processors (``"*"`` + a specific hook, coexisting with graph members)."""
    star_extra = _make_star_marker_proc()
    step_end_extra = _make_marker_proc()
    # _StarSpy -> "*" graph member; _StepEndOnly -> step_end graph member.  The
    # extras coexist with both on the same buckets.
    cfg = HarnessConfig(
        tool_registry=make_registry(add_tool),
        processors=[_StepEndOnly(), _StarSpy()],
    )
    extras = {"*": [star_extra], "step_end": [step_end_extra]}
    harness = Harness(ModelConfig(main=MockProvider(responses=["done"])), cfg, extra_processors=extras)

    # The extras must be present in the binding, marked as ungraphed.
    ungraphed = [proc for nid, _, proc in harness._rt.proc_node_binding if nid is UNGRAPHED]
    assert star_extra in ungraphed and step_end_extra in ungraphed

    executor = build_graph_executor(harness.config, harness._rt.proc_node_binding)
    for hook in PROCESSOR_HOOK_NAMES:
        legacy = _legacy_get_procs(harness._rt.processors, hook)
        _assert_identical(legacy, executor.procs_for(hook), hook, "extra_processors")

    # Explicit shape: star bucket (graph then extra) THEN step_end bucket (graph then extra).
    names = [type(p).__name__ for p in executor.procs_for("step_end")]
    assert names == ["_StarSpy", "_star_extra", "_StepEndOnly", "_extra"], names


def test_extra_processors_only_match_get_procs_by_identity():
    """extras with an EMPTY config (the test_custom_hook_injected shape)."""
    step_end_extra = _make_marker_proc()
    cfg = HarnessConfig(tool_registry=make_registry(add_tool), processors=[])
    harness = Harness(
        ModelConfig(main=MockProvider(responses=["done"])),
        cfg,
        extra_processors={"step_end": [step_end_extra]},
    )
    executor = build_graph_executor(harness.config, harness._rt.proc_node_binding)
    for hook in PROCESSOR_HOOK_NAMES:
        legacy = _legacy_get_procs(harness._rt.processors, hook)
        _assert_identical(legacy, executor.procs_for(hook), hook, "extra_only")
    assert executor.procs_for("step_end") == [step_end_extra]


# ── refusal: model / tool hooks are never dispatched ─────────────────────────


def test_refuses_model_and_tool_hooks():
    harness = ModelConfig(main=MockProvider(responses=["done"])).agentic(_cfg_context_coding())
    executor = build_graph_executor(harness.config, harness._rt.proc_node_binding)
    for hook in ("model", "tool"):
        with pytest.raises(NonDispatchHookError):
            executor.procs_for(hook)


# ── end-to-end: hook-fire sequence identical with the flag off vs on ─────────


def _make_sequence_spy(log: list) -> MultiHookProcessor:
    class _SequenceSpy(MultiHookProcessor):
        async def on_task_start(self, event):
            log.append("task_start")
            yield event

        async def on_step_start(self, event):
            log.append("step_start")
            yield event

        async def on_before_model(self, event):
            log.append("before_model")
            yield event

        async def on_after_model(self, event):
            log.append("after_model")
            yield event

        async def on_before_tool(self, event):
            log.append("before_tool")
            yield event

        async def on_after_tool(self, event):
            log.append("after_tool")
            yield event

        async def on_step_end(self, event):
            log.append("step_end")
            yield event

        async def on_task_end(self, event):
            log.append("task_end")
            yield event

    return _SequenceSpy()


_TOOL_STEP_RESPONSES = [
    {"content": "add", "tool_calls": [{"id": "c1", "name": "add", "input": {"a": 2, "b": 2}}]},
    "done",
]


async def _run_and_record(flag_on: bool) -> list:
    prev = os.environ.get(_FLAG)
    if flag_on:
        os.environ[_FLAG] = "1"
    else:
        os.environ.pop(_FLAG, None)
    try:
        log: list = []
        cfg = HarnessConfig(
            tool_registry=make_registry(add_tool),
            processors=[_make_sequence_spy(log)],
        )
        harness = ModelConfig(main=MockProvider(responses=list(_TOOL_STEP_RESPONSES))).agentic(cfg)
        assert harness._rt.proc_node_binding, "flag-on run would fall back: binding is empty"
        await harness.run(BaseTask(description="what is 2+2?", max_steps=5))
        return log
    finally:
        if prev is None:
            os.environ.pop(_FLAG, None)
        else:
            os.environ[_FLAG] = prev


async def _run_and_record_extras(flag_on: bool) -> list:
    """Same as _run_and_record but the spy arrives via extra_processors (ungraphed)."""
    prev = os.environ.get(_FLAG)
    if flag_on:
        os.environ[_FLAG] = "1"
    else:
        os.environ.pop(_FLAG, None)
    try:
        log: list = []
        cfg = HarnessConfig(tool_registry=make_registry(add_tool), processors=[])
        harness = Harness(
            ModelConfig(main=MockProvider(responses=list(_TOOL_STEP_RESPONSES))),
            cfg,
            extra_processors={"*": [_make_sequence_spy(log)]},
        )
        assert any(nid is UNGRAPHED for nid, _, _ in harness._rt.proc_node_binding), (
            "extra spy not recorded as ungraphed in the binding"
        )
        await harness.run(BaseTask(description="what is 2+2?", max_steps=5))
        return log
    finally:
        if prev is None:
            os.environ.pop(_FLAG, None)
        else:
            os.environ[_FLAG] = prev


@pytest.mark.asyncio
async def test_hook_fire_sequence_identical_flag_off_vs_on():
    off = await _run_and_record(flag_on=False)
    on = await _run_and_record(flag_on=True)

    assert off == on, f"flag flipped the hook-fire sequence:\n off={off}\n on ={on}"
    # The forced tool step must actually have exercised the tool hooks.
    assert "before_tool" in on and "after_tool" in on, on
    assert on[0] == "task_start" and on[-1] == "task_end", on


@pytest.mark.asyncio
async def test_extra_processors_hook_fire_sequence_flag_off_vs_on():
    off = await _run_and_record_extras(flag_on=False)
    on = await _run_and_record_extras(flag_on=True)

    assert off == on, f"flag flipped the extra_processors sequence:\n off={off}\n on ={on}"
    assert "before_tool" in on and "after_tool" in on, on
    assert on[0] == "task_start" and on[-1] == "task_end", on
