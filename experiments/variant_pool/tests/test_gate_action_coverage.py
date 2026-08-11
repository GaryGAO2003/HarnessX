# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""The invariant, made mechanical: every Evolver action is covered by a gate check.

The standing rule for the graph-native Evolver (v6) is that *every expansion of
the action set requires a matching expansion of the deterministic gate*. In prose
that rule rots: a seventh operator ships, nobody adds a gate check, and the
anti-reward-hacking guarantee quietly weakens. This test is the enforcer.

The action set is **derived from code**, never hand-listed:

* the graph-edit vocabulary is ``harnessx.graph.edit.GraphEditType`` (the enum);
* the operator surface is discovered by introspecting
  ``harnessx.graph.operators`` for the frozen dataclasses that expose ``edits``.

Against each, a coverage table names the deterministic gate check that governs a
candidate born from that action. The coverage tables are the *only* hand-kept
part, and the tests assert the derived action set and the covered set are equal —
so adding a seventh operator or edit type without a coverage entry fails this
suite, exactly as the invariant demands. The mapped checks are themselves real
``GateStage`` members of the live ``GATE_SEQUENCE``, so a table pointing at a
removed or invented check fails too.
"""

from __future__ import annotations

import dataclasses
import inspect

from harnessx.graph import operators as operators_module
from harnessx.graph.edit import GraphEditType
from variant_pool.gate import GATE_SEQUENCE, GateStage

# ---------------------------------------------------------------------------
# The action set — derived from code
# ---------------------------------------------------------------------------


def _discover_operators() -> dict[str, type]:
    """Every Evolver operator defined in ``harnessx.graph.operators``.

    An operator is a frozen dataclass *defined in that module* exposing a
    callable ``edits`` (the P3 contract: ``operator.edits(snapshot) -> [GraphEdit]``).
    Discovering by that shape, rather than a hand-written list, is what makes a
    newly added seventh operator show up here automatically — and therefore fail
    coverage until a gate check is mapped to it.
    """
    found: dict[str, type] = {}
    for name, obj in inspect.getmembers(operators_module, inspect.isclass):
        if getattr(obj, "__module__", None) != operators_module.__name__:
            continue
        if not dataclasses.is_dataclass(obj):
            continue
        if callable(getattr(obj, "edits", None)):
            found[name] = obj
    return found


# ---------------------------------------------------------------------------
# The coverage tables — the one hand-kept part, forced in sync by the tests below
# ---------------------------------------------------------------------------

#: Each graph-edit type -> the deterministic gate check that governs a candidate
#: it produces. The split is principled, not lazy:
#:
#: * actions that INTRODUCE a new executable node are covered by
#:   ``MANIFEST_COMPLETE`` — whose W19 attribution check is now a *graph existence
#:   query* (:func:`variant_pool.manifest.check_attribution_in_graph`): an
#:   inserted/replaced node that never executes in U is refused;
#: * actions that only REARRANGE or REMOVE existing nodes cannot be judged by
#:   "did it run" and are covered by the outcome gate ``SEESAW_REGRESSION`` — a
#:   removal or rewire that breaks an ever-solved task is a regression.
EDIT_TYPE_GATE_COVERAGE: dict[GraphEditType, GateStage] = {
    GraphEditType.INSERT_NODE: GateStage.MANIFEST_COMPLETE,
    GraphEditType.REPLACE_SAME_GROUP: GateStage.MANIFEST_COMPLETE,
    GraphEditType.MUTATE_INACTIVE: GateStage.SEESAW_REGRESSION,
    GraphEditType.REMOVE_NODE: GateStage.SEESAW_REGRESSION,
    GraphEditType.CHANGE_DEPENDENCY: GateStage.SEESAW_REGRESSION,
    GraphEditType.SWAP_SUBGRAPH: GateStage.SEESAW_REGRESSION,
}

#: Each operator (by class name) -> its governing gate check, on the same split.
#: ``InsertProcessor`` / ``ReplaceSameSingletonGroup`` add a node whose execution
#: the W19 graph query verifies; the rest rearrange or remove and are judged by
#: the seesaw.
OPERATOR_GATE_COVERAGE: dict[str, GateStage] = {
    "InsertProcessor": GateStage.MANIFEST_COMPLETE,
    "ReplaceSameSingletonGroup": GateStage.MANIFEST_COMPLETE,
    "MutateProcessorParams": GateStage.SEESAW_REGRESSION,
    "RewireOrdering": GateStage.SEESAW_REGRESSION,
    "RemoveProcessor": GateStage.SEESAW_REGRESSION,
    "SwapBundle": GateStage.SEESAW_REGRESSION,
}


# ---------------------------------------------------------------------------
# The invariant
# ---------------------------------------------------------------------------


def test_every_graph_edit_type_has_a_gate_check() -> None:
    """The enum is the action vocabulary; coverage must equal it exactly.

    A seventh ``GraphEditType`` member added without a coverage entry makes the
    derived set larger than the covered set and fails here.
    """
    derived = set(GraphEditType)
    covered = set(EDIT_TYPE_GATE_COVERAGE)
    assert covered == derived, (
        "GraphEditType coverage is out of sync with the enum. "
        f"uncovered (add a gate check): {sorted(e.name for e in derived - covered)}; "
        f"stale (edit type removed): {sorted(e.name for e in covered - derived)}"
    )


def test_every_operator_has_a_gate_check() -> None:
    """The operator registry is discovered from code; coverage must equal it.

    A seventh operator dataclass added to ``harnessx.graph.operators`` without a
    coverage entry is discovered here and fails until a gate check is mapped.
    """
    derived = set(_discover_operators())
    covered = set(OPERATOR_GATE_COVERAGE)
    assert covered == derived, (
        "Operator coverage is out of sync with harnessx.graph.operators. "
        f"uncovered (add a gate check): {sorted(derived - covered)}; "
        f"stale (operator removed): {sorted(covered - derived)}"
    )


def test_coverage_points_only_at_real_wired_gate_checks() -> None:
    """Every mapped check must be a real gate stage in the live ``GATE_SEQUENCE``.

    Coverage cannot be satisfied by pointing an action at a stage that was
    removed from the gate or never wired — the gate check has to actually exist.
    """
    live = set(GATE_SEQUENCE)
    for action, stage in {**EDIT_TYPE_GATE_COVERAGE, **OPERATOR_GATE_COVERAGE}.items():
        assert isinstance(stage, GateStage), f"{action}: coverage value is not a GateStage"
        assert stage in live, f"{action}: mapped to {stage.name}, which is not in GATE_SEQUENCE"


def test_operator_discovery_finds_the_known_first_batch() -> None:
    """Guard the discovery itself: the plan §7 first batch must be found.

    If introspection silently stopped finding operators, the coverage equality
    above would pass vacuously against an empty set. Anchoring the known six
    keeps that failure visible (the lesson of the DAG/count tests that survived a
    broken identity: a check nobody has watched fail is not a check).
    """
    discovered = set(_discover_operators())
    first_batch = {
        "MutateProcessorParams",
        "InsertProcessor",
        "RemoveProcessor",
        "ReplaceSameSingletonGroup",
        "RewireOrdering",
        "SwapBundle",
    }
    assert first_batch <= discovered, f"discovery lost operators: {sorted(first_batch - discovered)}"
