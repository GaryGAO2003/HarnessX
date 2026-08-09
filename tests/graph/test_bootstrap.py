"""Δ17 block 23 — declaration bootstrapping with file:line citations.

Deterministic re-runnable extraction; values == get_graph_metadata (single
truth); citations point at the exact declaring lines (MRO-nearest wins);
derived defaults carry no citation; the WKD snapshot is pinned to the
bootstrap output (anti-rot).
"""

import pytest

from harnessx.core.processor import MultiHookProcessor, get_graph_metadata
from harnessx.graph.bootstrap import bootstrap_declaration, bootstrap_well_known
from harnessx.graph.declaration import WELL_KNOWN_DECLARATIONS

CG = "harnessx.processors.control.cost_guard.CostGuardProcessor"
MR = "harnessx.processors.multi_model.model_router.ModelRouterProcessor"
SELF = "tests.graph.test_bootstrap"


class CitedProbe(MultiHookProcessor):
    _hook = "task_end"
    _order = 7
    _singleton_group = "cited.probe"
    _after = ("other",)

    async def on_task_end(self, event):
        yield event


class CitedChild(CitedProbe):
    """Inherits everything — citations must point at the PARENT's lines."""


# ── values == single source of truth ────────────────────────────────────────


def test_values_match_get_graph_metadata():
    decl = bootstrap_declaration(f"{SELF}.CitedProbe")
    meta = get_graph_metadata(CitedProbe)
    assert decl.hook == meta["_hook_"] == "task_end"
    assert decl.hooks == tuple(meta["_hooks_"])
    assert decl.order == meta["_order_"] == 7
    assert decl.singleton_group == "cited.probe"
    assert decl.after == ("other",)
    assert decl.confidence == 1.0


# ── citations: exact declaring lines ────────────────────────────────────────


def test_citations_point_at_declaring_lines():
    import inspect

    decl = bootstrap_declaration(f"{SELF}.CitedProbe")
    src, class_line = inspect.getsourcelines(CitedProbe)
    # attribute lines are offsets within the class body
    expected = {}
    for i, line in enumerate(src):
        for attr, field_name in (("_hook", "hook"), ("_order", "order"),
                                 ("_singleton_group", "singleton_group"),
                                 ("_after", "after")):
            if line.strip().startswith(f"{attr} ="):
                expected[field_name] = class_line + i
    for field_name, line_no in expected.items():
        cite = decl.citations[field_name]
        assert cite.endswith(f":{line_no}"), f"{field_name}: {cite} != :{line_no}"
        assert "test_bootstrap.py" in cite


def test_handler_coverage_cited():
    decl = bootstrap_declaration(f"{SELF}.CitedProbe")
    assert "hooks" in decl.citations
    assert "test_bootstrap.py" in decl.citations["hooks"]


def test_inherited_attrs_cite_parent_lines():
    child = bootstrap_declaration(f"{SELF}.CitedChild")
    parent = bootstrap_declaration(f"{SELF}.CitedProbe")
    # child declares nothing itself — every citation resolves to the parent's
    for field_name in ("hook", "order", "singleton_group", "after"):
        assert child.citations[field_name] == parent.citations[field_name]
    assert child.hook == "task_end"  # value inherited too


def test_derived_defaults_carry_no_citation():
    # WKD classes have no class _hook → bucket "*" is DERIVED, not declared
    decl = bootstrap_declaration(CG)
    assert decl.hook == "*"
    assert "hook" not in decl.citations          # nothing to cite
    assert "singleton_group" in decl.citations   # explicitly declared
    assert "order" in decl.citations


def test_channel_declarations_cited():
    cg = bootstrap_declaration(CG)
    assert cg.reads_event_fields == ("BeforeModelEvent.cumulative_cost_usd",)
    assert "reads_event_fields" in cg.citations
    assert "cost_guard.py" in cg.citations["reads_event_fields"]

    mr = bootstrap_declaration(MR)
    assert mr.writes_to == ("model.route",)
    assert "writes_to" in mr.citations
    assert "model_router.py" in mr.citations["writes_to"]


# ── determinism + anti-rot pin ──────────────────────────────────────────────


def test_bootstrap_deterministic():
    a = bootstrap_declaration(CG)
    b = bootstrap_declaration(CG)
    assert a == b


@pytest.mark.parametrize("target", sorted(WELL_KNOWN_DECLARATIONS))
def test_wkd_snapshot_pinned_to_bootstrap(target):
    """The static snapshot must equal a fresh bootstrap (citations aside)."""
    boot = bootstrap_declaration(target)
    wkd = WELL_KNOWN_DECLARATIONS[target]
    assert wkd.hook == boot.hook, f"{target}: hook rotted"
    assert wkd.hooks == boot.hooks, f"{target}: hooks rotted"
    assert wkd.order == boot.order, f"{target}: order rotted"
    assert wkd.singleton_group == boot.singleton_group, f"{target}: sg rotted"
    assert wkd.after == boot.after, f"{target}: after rotted"
    assert wkd.writes_to == boot.writes_to, f"{target}: writes_to rotted"
    assert wkd.reads_from == boot.reads_from, f"{target}: reads_from rotted"
    assert wkd.reads_event_fields == boot.reads_event_fields
    assert wkd.writes_event_fields == boot.writes_event_fields


def test_bootstrap_well_known_covers_all_targets():
    table = bootstrap_well_known()
    assert set(table) == set(WELL_KNOWN_DECLARATIONS)
    # every regenerated entry carries at least one citation (Δ9 precondition:
    # all 19 declare singleton_group/order in source)
    for target, decl in table.items():
        assert decl.citations, f"{target}: no citations extracted"


def test_unimportable_target_raises():
    with pytest.raises(Exception):
        bootstrap_declaration("nonexistent.module.Klass")
