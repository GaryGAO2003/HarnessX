"""Block 13 — L1.3 compute_effective_hooks R0–R7 (VM1) + L1.2 class attrs."""

from harnessx.core.events import BeforeModelEvent, StepEndEvent
from harnessx.core.processor import (
    MultiHookProcessor,
    PROCESSOR_HOOK_NAMES,
    compute_effective_hooks,
    get_graph_metadata,
    on,
)


class _Parent(MultiHookProcessor):
    async def on_step_end(self, event):
        yield event


class _Child(_Parent):
    async def on_before_model(self, event):
        yield event


class _GrandChild(_Child):
    @on(StepEndEvent)
    async def custom_step_end(self, event):
        yield event


class _Hooked(MultiHookProcessor):
    _hook = "task_end"

    async def on_step_start(self, event):  # coverage irrelevant: R1 short-circuits
        yield event


class _HookedChildOptsOut(_Hooked):
    _hook = None  # explicit opt-out: derive from dispatch, don't inherit parent hook


class _Bare(MultiHookProcessor):
    pass


class _OnDecorated(MultiHookProcessor):
    @on(BeforeModelEvent)
    async def guard(self, event):
        yield event


class _OnDecoratedChild(_OnDecorated):
    pass


# ── VM1: MRO inheritance ────────────────────────────────────────────────────


def test_child_inherits_parent_handler():
    assert compute_effective_hooks(_Parent) == ("step_end",)
    assert "step_end" in compute_effective_hooks(_Child)


def test_child_adds_own_handler_lifecycle_ordered():
    # before_model precedes step_end in lifecycle order (never alphabetical)
    assert compute_effective_hooks(_Child) == ("before_model", "step_end")


def test_grandchild_on_override_no_duplicates():
    hooks = compute_effective_hooks(_GrandChild)
    assert hooks == ("before_model", "step_end")  # set-dedup, single step_end


def test_inherited_on_decorated_handler():
    # @on() in the parent, child does not override → still covered via MRO
    assert compute_effective_hooks(_OnDecoratedChild) == ("before_model",)


# ── R1 short-circuit + explicit opt-out ─────────────────────────────────────


def test_class_hook_short_circuits():
    assert compute_effective_hooks(_Hooked) == ("task_end",)


def test_explicit_none_hook_derives_from_dispatch():
    # subclass _hook=None stops the MRO walk — parent hook NOT inherited
    assert compute_effective_hooks(_HookedChildOptsOut) == ("step_start",)
    assert get_graph_metadata(_HookedChildOptsOut)["_hook_"] == "*"


# ── R4: bare subclass inherits full coverage ────────────────────────────────


def test_bare_subclass_covers_all_eight():
    assert compute_effective_hooks(_Bare) == PROCESSOR_HOOK_NAMES


def test_base_class_attrs_do_not_leak_hooks():
    # L1.2 attrs on MultiHookProcessor itself must not affect derivation
    meta = get_graph_metadata(_Bare)
    assert meta["_hook_"] == "*"
    assert meta["_order_"] == 0
    assert meta["_singleton_group_"] == ""
    assert meta["_after_"] == []
    assert meta["_writes_slots_"] == []


# ── R6/R7 ───────────────────────────────────────────────────────────────────


def test_lifecycle_order_not_alphabetical():
    hooks = compute_effective_hooks(_Bare)
    # alphabetical would put after_model first; lifecycle starts at task_start
    assert hooks[0] == "task_start"
    assert hooks[-1] == "task_end"


def test_model_tool_never_present():
    for cls in (_Parent, _Child, _GrandChild, _Bare, _Hooked):
        assert not set(compute_effective_hooks(cls)) & {"model", "tool"}
