"""Block 10 — L3 declaration layer.

VM3 (ComponentDecl hook↔hooks compat + "*" bucket exemption), VM4
(merge_declarations multi-hook), WKD-vs-class consistency (实施约束 #6),
VM7 slot-key bridge (ModelRouter / CostGuard class attrs).
"""

import importlib

import pytest

from harnessx.core.processor import get_graph_metadata
from harnessx.graph.declaration import (
    ComponentDecl,
    DeclarationSource,
    WELL_KNOWN_DECLARATIONS,
    merge_declarations,
)


# ── VM3: ComponentDecl hook/hooks compat ────────────────────────────────────


def test_hook_only_expands_to_hooks():
    d = ComponentDecl(target="x", hook="task_start")
    assert d.hooks == ("task_start",)
    assert d.hook == "task_start"


def test_hooks_only_derives_hook():
    d = ComponentDecl(target="x", hooks=("task_end",))
    assert d.hook == "task_end"


def test_both_given_hooks_wins():
    d = ComponentDecl(target="x", hook="task_start", hooks=("task_end",))
    assert d.hooks == ("task_end",)
    assert d.hook == "task_end"


def test_star_bucket_exempt_from_sync():
    d = ComponentDecl(target="x", hook="*", hooks=("task_end", "task_start"))
    assert d.hook == "*"  # registration bucket survives
    assert d.hooks == ("task_start", "task_end")  # lifecycle sorted


def test_star_hook_alone_lands_in_hooks():
    d = ComponentDecl(target="x", hook="*")
    assert d.hooks == ("*",)  # L4.1 expands the wildcard to 8 edges
    assert d.hook == "*"


def test_hooks_lifecycle_sorted():
    d = ComponentDecl(target="x", hooks=("task_end", "before_model", "task_start"))
    assert d.hooks == ("task_start", "before_model", "task_end")
    assert d.hook == "task_start"


def test_unknown_hook_names_sort_last_stably():
    d = ComponentDecl(target="x", hooks=("zeta", "task_start", "alpha"))
    assert d.hooks == ("task_start", "zeta", "alpha")  # unknowns keep order


# ── VM4: merge_declarations multi-hook + new fields ─────────────────────────


def _decl(**kw):
    kw.setdefault("target", "x")
    return ComponentDecl(**kw)


def test_merge_observed_hooks_win_when_nonempty():
    dec = {"x": _decl(hooks=("task_start",))}
    obs = {"x": _decl(hooks=("task_start", "task_end"), confidence=0.95)}
    merged = merge_declarations(dec, obs)["x"]
    assert merged.hooks == ("task_start", "task_end")


def test_merge_empty_observed_hooks_fall_back():
    dec = {"x": _decl(hooks=("step_end",))}
    obs = {"x": _decl(confidence=0.95)}
    merged = merge_declarations(dec, obs)["x"]
    assert merged.hooks == ("step_end",)


def test_merge_star_bucket_survives():
    dec = {"x": _decl(hook="*", hooks=("task_start",))}
    obs = {"x": _decl(hooks=("task_start", "task_end"), confidence=0.95)}
    merged = merge_declarations(dec, obs)["x"]
    assert merged.hook == "*"  # observation never changes the bucket
    assert merged.hooks == ("task_start", "task_end")


def test_merge_event_fields_observed_wins():
    dec = {"x": _decl(reads_event_fields=("Ev.a",), writes_event_fields=("Ev.w",))}
    obs = {"x": _decl(reads_event_fields=("Ev.b",), confidence=0.95)}
    merged = merge_declarations(dec, obs)["x"]
    assert merged.reads_event_fields == ("Ev.b",)      # obs wins
    assert merged.writes_event_fields == ("Ev.w",)     # obs empty → dec


def test_merge_one_sided_passthrough():
    dec = {"a": _decl(target="a")}
    obs = {"b": _decl(target="b")}
    merged = merge_declarations(dec, obs)
    assert merged["a"].target == "a"
    assert merged["b"].target == "b"


# ── 实施约束 #6: WKD == class introspection ─────────────────────────────────


@pytest.mark.parametrize("target", sorted(WELL_KNOWN_DECLARATIONS))
def test_wkd_matches_class_introspection(target):
    decl = WELL_KNOWN_DECLARATIONS[target]
    mod, cls_name = target.rsplit(".", 1)
    cls = getattr(importlib.import_module(mod), cls_name)
    meta = get_graph_metadata(cls)

    assert decl.hook == meta["_hook_"], f"{target}: hook (bucket) drifted"
    assert decl.hooks == tuple(meta["_hooks_"]), f"{target}: hooks (coverage) drifted"
    assert decl.order == meta["_order_"], f"{target}: order drifted"
    assert decl.singleton_group == meta["_singleton_group_"], f"{target}: sg drifted"
    assert decl.after == tuple(meta["_after_"]), f"{target}: after drifted"
    assert decl.writes_to == tuple(meta["_writes_slots_"]), f"{target}: writes_to drifted"
    assert decl.reads_from == tuple(meta["_reads_slots_"]), f"{target}: reads_from drifted"
    assert decl.reads_event_fields == tuple(meta["_reads_event_fields_"]), \
        f"{target}: reads_event_fields drifted"
    assert decl.writes_event_fields == tuple(meta["_writes_event_fields_"]), \
        f"{target}: writes_event_fields drifted"
    assert decl.source == DeclarationSource.CODE_INTROSPECTION


def test_costguard_has_no_phantom_slot_dep():
    decl = WELL_KNOWN_DECLARATIONS[
        "harnessx.processors.control.cost_guard.CostGuardProcessor"]
    assert decl.reads_from == ()  # phantom ("cost",) removed (VM14)
    assert decl.reads_event_fields == ("BeforeModelEvent.cumulative_cost_usd",)


# ── VM7: instance-level slot-key bridge ─────────────────────────────────────


def test_model_router_default_slot_key():
    from harnessx.processors.multi_model.model_router import ModelRouterProcessor
    meta = get_graph_metadata(ModelRouterProcessor())
    assert meta["_writes_slots_"] == ["model.route"]


def test_model_router_custom_slot_key_visible_to_graph():
    from harnessx.processors.multi_model.model_router import ModelRouterProcessor
    meta = get_graph_metadata(ModelRouterProcessor(slot_key="custom.route"))
    assert meta["_writes_slots_"] == ["custom.route"]  # dot preserved, not truncated
