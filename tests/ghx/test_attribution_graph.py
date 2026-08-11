# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""G2 piece 1 — graph-backed signature check.

The vendored attributor answers direct/orphan/joint by regex-parsing digest text;
this backend answers the identical question by counting ``tool:<name>`` nodes in a
run's U. Tests 1 + 2: a fired signature is ``direct`` and an absent one is the
refusal vocabulary (``orphan``) with ``min_calls`` respected; a signature the graph
cannot represent (prompt/config bucket, processor_invocation, unknown type) is an
honest "no graph representation" (``backend='none'``), never a guess.
"""

from __future__ import annotations

from harnessx.ghx.attribution_graph import (
    BACKEND_GRAPH,
    BACKEND_NONE,
    DIRECT,
    JOINT,
    ORPHAN,
    check_signature_in_u,
    count_tool_invocations,
    infer_signature,
)
from harnessx.graph.types import unfolded_id
from harnessx.graph.unfold import UnfoldedGraph, UnfoldedNode


def _tool_node(name: str, ordinal: int) -> UnfoldedNode:
    return UnfoldedNode(
        id=unfolded_id(f"tool:{name}", ordinal),
        static_node_id=f"tool:{name}",
        graphed=True,
        hook="tool",
        step=ordinal,
        ordinal=ordinal,
        label=name,
    )


def _proc_node(base: str, ordinal: int, hook: str = "before_model", label: str = "") -> UnfoldedNode:
    return UnfoldedNode(
        id=unfolded_id(base, ordinal),
        static_node_id=base,
        graphed=True,
        hook=hook,
        step=ordinal,
        ordinal=ordinal,
        label=label or base,
    )


def _u_with(*nodes: UnfoldedNode) -> UnfoldedGraph:
    return UnfoldedGraph(run_id="r", session_id="s", nodes=list(nodes))


# ── test 1: fired → direct; absent → orphan; min_calls respected ──────────────


def test_tool_signature_fired_is_direct():
    u = _u_with(_proc_node("Sys", 0), _tool_node("SmartFetch", 1), _tool_node("SmartFetch", 2))
    res = check_signature_in_u({"type": "tool_call", "tool_name": "SmartFetch", "expected_min_calls": 1}, u)
    assert res.label == DIRECT
    assert res.backend == BACKEND_GRAPH
    assert res.answered_by_graph is True
    assert res.fired is True
    assert res.count == 2
    assert res.min_calls == 1


def test_tool_signature_absent_is_orphan():
    # A tool node exists, but NOT the one the signature names.
    u = _u_with(_tool_node("Bash", 0), _tool_node("Bash", 1))
    res = check_signature_in_u({"type": "tool_call", "tool_name": "SmartFetch"}, u)
    assert res.label == ORPHAN
    assert res.backend == BACKEND_GRAPH
    assert res.fired is False
    assert res.count == 0


def test_min_calls_floor_respected():
    u = _u_with(_tool_node("SmartFetch", 0), _tool_node("SmartFetch", 1))
    # 2 invocations, floor 2 → direct.
    assert (
        check_signature_in_u({"type": "tool_call", "tool_name": "SmartFetch", "expected_min_calls": 2}, u).label
        == DIRECT
    )
    # 2 invocations, floor 3 → orphan (edited/present but under the credit floor).
    below = check_signature_in_u({"type": "tool_call", "tool_name": "SmartFetch", "expected_min_calls": 3}, u)
    assert below.label == ORPHAN
    assert below.fired is False
    assert below.count == 2 and below.min_calls == 3


def test_min_calls_alias_accepted():
    u = _u_with(_tool_node("SmartFetch", 0))
    # The official field is ``expected_min_calls``; ``min_calls`` is accepted as an alias.
    res = check_signature_in_u({"type": "tool_call", "tool_name": "SmartFetch", "min_calls": 2}, u)
    assert res.label == ORPHAN
    assert res.min_calls == 2


def test_count_tool_invocations_counts_only_matching_nodes():
    u = _u_with(
        _proc_node("proc:cost_guard", 0),
        _tool_node("SmartFetch", 1),
        _tool_node("Bash", 2),
        _tool_node("SmartFetch", 3),
    )
    assert count_tool_invocations(u, "SmartFetch") == 2
    assert count_tool_invocations(u, "Bash") == 1
    assert count_tool_invocations(u, "Nonexistent") == 0


# ── test 2: no graph representation → honest, never a guess ────────────────────


def test_no_signature_is_joint_by_definition():
    u = _u_with(_tool_node("SmartFetch", 0))
    res = check_signature_in_u(None, u)
    assert res.label == JOINT
    assert res.backend == BACKEND_NONE
    assert res.answered_by_graph is False
    assert res.fired is None
    assert "prompt/config" in res.reason


def test_processor_invocation_has_no_graph_representation():
    # Even though a matching tool node is present, a processor_invocation signature
    # is NOT answered by counting tool nodes — the graph abstains honestly.
    u = _u_with(_tool_node("SmartFetch", 0))
    res = check_signature_in_u({"type": "processor_invocation", "class_name": "FooProcessor"}, u)
    assert res.label == JOINT
    assert res.backend == BACKEND_NONE
    assert res.fired is None
    assert "no tool-node representation" in res.reason


def test_unknown_signature_type_is_not_guessed():
    u = _u_with(_tool_node("SmartFetch", 0))
    res = check_signature_in_u({"type": "spooky_new_type", "tool_name": "SmartFetch"}, u)
    assert res.label == JOINT
    assert res.backend == BACKEND_NONE
    assert res.fired is None


def test_tool_call_missing_name_abstains():
    u = _u_with(_tool_node("SmartFetch", 0))
    res = check_signature_in_u({"type": "tool_call"}, u)
    assert res.backend == BACKEND_NONE
    assert res.label == JOINT


# ── inference mirrors the vendored attributor ─────────────────────────────────


def test_infer_signature_prefers_explicit():
    explicit = {"type": "tool_call", "tool_name": "X", "expected_min_calls": 1}
    assert infer_signature("tools", {"bucket": "tools"}, explicit) is explicit


def test_infer_signature_prompt_and_config_are_none():
    assert infer_signature("prompt", {"bucket": "prompt"}) is None
    assert infer_signature("config", {"bucket": "config"}) is None


def test_infer_signature_tools_bucket_from_file_changes():
    manifest = {"bucket": "tools", "file_changes": [{"path": "harnessx/tools/smart_fetch.py", "action": "create"}]}
    sig = infer_signature("tools", manifest)
    assert sig == {"type": "tool_call", "tool_name": "SmartFetch", "expected_min_calls": 1}
