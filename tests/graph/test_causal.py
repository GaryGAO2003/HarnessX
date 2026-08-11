# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""v6 M6a — causal queries over the unfolded graph U (:mod:`harnessx.graph.causal`).

Pure query layer, so these run on hand-built U's whose right answer is obvious by
inspection: two disjoint ancestor paths, a node with none, an unrelated branch that
must stay OUT of the cone, edge-type-restricted cones, a wide fan-in for termination,
a deliberately CYCLIC U to show the visited-set guard, cross-layer descent through an
INVOKES edge, and JSONL round-trip parity.
"""

from __future__ import annotations

import pytest

from harnessx.graph.causal import (
    CONTROL,
    DATA,
    LayeredCone,
    ancestors,
    causal_cone,
    causal_cone_across,
    descendants,
    induced_subgraph,
    invokes_frontier,
    select_nodes,
    terminal_node,
    tool_invocations,
)
from harnessx.graph.types import unfolded_id
from harnessx.graph.unfold import (
    UnfoldedEdge,
    UnfoldedGraph,
    UnfoldedInvokes,
    UnfoldedNode,
    load_unfolded,
    write_unfolded,
)


# ── builders ─────────────────────────────────────────────────────────────────


def _node(base: str, ordinal: int, *, hook: str = "before_model", label: str = "") -> UnfoldedNode:
    return UnfoldedNode(
        id=unfolded_id(base, ordinal),
        static_node_id=base,
        graphed=True,
        hook=hook,
        step=0,
        ordinal=ordinal,
        label=label or base,
    )


def _edge(src: UnfoldedNode, tgt: UnfoldedNode, edge_type: str) -> UnfoldedEdge:
    return UnfoldedEdge(source=src.id, target=tgt.id, edge_type=edge_type)


def _diamond():
    """A U where D has two DISJOINT ancestor paths and E has none.

        A@t0 --control--> B@t1 --control--> D@t3
        C@t2 --data----------------------->  D@t3
        E@t4  (isolated)

    ancestors(D) = {A, B, C}; the control path {A,B} and the data path {C} share no
    node.  ancestors(E) = {} and ancestors(A) = {}.
    """
    a = _node("A", 0)
    b = _node("B", 1)
    c = _node("C", 2)
    d = _node("D", 3)
    e = _node("E", 4)
    edges = [
        _edge(a, b, CONTROL),
        _edge(b, d, CONTROL),
        _edge(c, d, DATA),
    ]
    g = UnfoldedGraph(run_id="diamond", session_id="s", nodes=[a, b, c, d, e], edges=edges)
    return g, {"A": a.id, "B": b.id, "C": c.id, "D": d.id, "E": e.id}


# ── 1. correctness: disjoint paths, and a node with no ancestors ─────────────


def test_ancestors_two_disjoint_paths():
    g, n = _diamond()
    assert ancestors(g, n["D"]) == {n["A"], n["B"], n["C"]}
    # the two paths are genuinely disjoint: control side {A,B}, data side {C}
    assert ancestors(g, n["D"], edge_types=CONTROL) == {n["A"], n["B"]}
    assert ancestors(g, n["D"], edge_types=DATA) == {n["C"]}


def test_ancestors_none():
    g, n = _diamond()
    assert ancestors(g, n["E"]) == set()  # isolated node
    assert ancestors(g, n["A"]) == set()  # a source


def test_descendants_forward_cone():
    g, n = _diamond()
    assert descendants(g, n["A"]) == {n["B"], n["D"]}
    assert descendants(g, n["C"]) == {n["D"]}
    assert descendants(g, n["D"]) == set()  # a sink


def test_ancestors_is_not_descendants():
    """The off-by-one guard: for a node with ancestors but no descendants (a sink),
    the two directions must not coincide.  ancestors(D) is non-empty; descendants(D)
    is empty — a traversal that confused the direction would fail here."""
    g, n = _diamond()
    assert ancestors(g, n["D"]) == {n["A"], n["B"], n["C"]}
    assert descendants(g, n["D"]) == set()
    assert ancestors(g, n["D"]) != descendants(g, n["D"])


def test_causal_cone_union_and_anchors():
    g, n = _diamond()
    # union of ancestors over {B, C}, anchors included by default
    assert causal_cone(g, [n["B"], n["C"]]) == {n["A"], n["B"], n["C"]}
    # include_anchors=False drops the seeds themselves
    assert causal_cone(g, [n["B"], n["C"]], include_anchors=False) == {n["A"]}
    # a bare string is accepted as a single anchor
    assert causal_cone(g, n["D"]) == {n["A"], n["B"], n["C"], n["D"]}
    # data-only cone of D: just the data-side ancestor plus D
    assert causal_cone(g, n["D"], edge_types=DATA) == {n["C"], n["D"]}


def test_unknown_anchor_raises():
    g, _ = _diamond()
    with pytest.raises(ValueError):
        ancestors(g, "Z@t99")
    with pytest.raises(ValueError):
        causal_cone(g, ["A@t0", "Z@t99"])


# ── 2. cone excludes the irrelevant ──────────────────────────────────────────


def test_cone_excludes_unrelated_branch():
    """A cone that returns everything is just the trace again.  Build a failure cone
    and a clearly unrelated branch that never reaches it; assert the branch is absent."""
    # failure side: R@t0 -> M@t1 -> F@t3 (F is the anchor)
    r = _node("R", 0)
    m = _node("M", 1)
    f = _node("F", 3)
    # unrelated side: U1@t2 -> U2@t4 -> U3@t5, feeding a DIFFERENT sink, never F
    u1 = _node("U1", 2)
    u2 = _node("U2", 4)
    u3 = _node("U3", 5)
    edges = [
        _edge(r, m, CONTROL),
        _edge(m, f, DATA),
        _edge(u1, u2, CONTROL),
        _edge(u2, u3, DATA),
    ]
    g = UnfoldedGraph(run_id="excl", session_id="s", nodes=[r, m, f, u1, u2, u3], edges=edges)

    cone = causal_cone(g, f.id)
    assert cone == {r.id, m.id, f.id}
    unrelated = {u1.id, u2.id, u3.id}
    assert cone & unrelated == set(), f"cone leaked unrelated nodes: {cone & unrelated}"


# ── 3. size bound: see tests/integration/test_causal_cone.py (needs a real run) ─


# ── 4. loaded-from-JSONL parity ──────────────────────────────────────────────


def test_jsonl_parity(tmp_path):
    g, n = _diamond()
    # add an INVOKES edge so the round-trip covers invokes too
    g.invokes.append(UnfoldedInvokes(source=n["D"], child_run_id="child-run"))
    write_unfolded(g, base_dir=str(tmp_path))
    reloaded = load_unfolded(tmp_path / "s" / "diamond_unfolded.jsonl")

    for probe in (n["D"], n["E"], n["A"]):
        assert ancestors(reloaded, probe) == ancestors(g, probe)
        assert descendants(reloaded, probe) == descendants(g, probe)
    assert causal_cone(reloaded, [n["D"]]) == causal_cone(g, [n["D"]])
    assert causal_cone(reloaded, n["D"], edge_types=DATA) == causal_cone(g, n["D"], edge_types=DATA)
    # frontier survives the round trip
    assert [iv.child_run_id for iv in invokes_frontier(reloaded, causal_cone(reloaded, n["D"]))] == ["child-run"]


# ── 5. termination: wide fan-in, and the visited-set guard on a cyclic U ──────


def test_wide_fan_in_terminates():
    """Many sources into one sink — a real fan-in, traversal must terminate and
    return every source exactly once (set semantics)."""
    sink = _node("SINK", 100, hook="task_end")
    sources = [_node(f"SRC{i}", i) for i in range(50)]
    edges = [_edge(s, sink, DATA) for s in sources]
    g = UnfoldedGraph(run_id="fan", session_id="s", nodes=[*sources, sink], edges=edges)
    anc = ancestors(g, sink.id)
    assert anc == {s.id for s in sources}
    assert len(anc) == 50


def test_visited_set_guards_a_cycle():
    """U is a DAG by construction, so this cannot arise from the recorder — but the
    query must not loop forever if handed a cyclic U.  Build one directly (P<->Q) and
    assert the traversal terminates with the finite reachable set.  A traversal without
    a visited set would hang here (the test would never return)."""
    p = _node("P", 0)
    q = _node("Q", 1)
    edges = [_edge(p, q, CONTROL), _edge(q, p, CONTROL)]  # back edge: a real cycle
    g = UnfoldedGraph(run_id="cyc", session_id="s", nodes=[p, q], edges=edges)
    # terminates (does not hang) and returns the other node
    assert ancestors(g, q.id) == {p.id}
    assert descendants(g, q.id) == {p.id}


# ── 6. cross-layer descent through INVOKES ───────────────────────────────────


def _parent_child():
    """Parent U that spawns a child; the child is a separate U with ordinals from 0.

    parent:  ts@t0 -> tool:spawn_subagent@t1 -> te@t2   (INVOKES -> 'child1')
    child :  ts@t0 -> te@t1
    """
    p_ts = _node("hook:task_start", 0, hook="task_start")
    p_tool = _node("tool:spawn_subagent", 1, hook="tool", label="spawn_subagent")
    p_te = _node("hook:task_end", 2, hook="task_end")
    parent = UnfoldedGraph(
        run_id="parent",
        session_id="s",
        nodes=[p_ts, p_tool, p_te],
        edges=[_edge(p_ts, p_tool, CONTROL), _edge(p_tool, p_te, CONTROL)],
        invokes=[UnfoldedInvokes(source=p_tool.id, child_run_id="child1")],
    )
    c_ts = _node("hook:task_start", 0, hook="task_start")
    c_te = _node("hook:task_end", 1, hook="task_end")
    child = UnfoldedGraph(
        run_id="child1",
        session_id="s",
        nodes=[c_ts, c_te],
        edges=[_edge(c_ts, c_te, CONTROL)],
    )
    return parent, child, {"p_tool": p_tool.id, "p_te": p_te.id, "p_ts": p_ts.id, "c_ts": c_ts.id, "c_te": c_te.id}


def test_invokes_frontier_is_observable_without_a_resolver():
    """Decision 2: ancestors does not silently cross INVOKES; the boundary is reported.
    Anchored at the terminal, the cone reaches the spawn tool node, so the frontier
    names the child — even with no resolver, the caller can tell the cone ended there."""
    parent, _child, n = _parent_child()
    cone = causal_cone(parent, terminal_node(parent))
    assert cone == {n["p_ts"], n["p_tool"], n["p_te"]}
    frontier = invokes_frontier(parent, cone)
    assert [iv.child_run_id for iv in frontier] == ["child1"]

    # causal_cone_across with resolve=None: frontier present, no descent
    layered = causal_cone_across(parent, [n["p_te"]], resolve=None)
    assert isinstance(layered, LayeredCone)
    assert layered.children == {}
    assert [iv.child_run_id for iv in layered.invokes] == ["child1"]


def test_cross_layer_descends_with_a_resolver():
    parent, child, n = _parent_child()
    resolve = {"child1": child}.get

    layered = causal_cone_across(parent, [n["p_te"]], resolve=resolve)
    assert set(layered.nodes) == {n["p_ts"], n["p_tool"], n["p_te"]}
    assert set(layered.children) == {"child1"}
    child_cone = layered.children["child1"]
    # the child's cone is anchored at the child's terminal invocation
    assert set(child_cone.nodes) == {n["c_ts"], n["c_te"]}
    assert layered.run_ids() == {"parent", "child1"}
    assert layered.total_nodes() == 5


def test_cross_layer_gated_by_ancestry():
    """A child is pulled in only because its spawn node is a U-backed ancestor of the
    anchor.  Anchor on task_start (upstream of the spawn): the spawn node is NOT in the
    cone, so nothing descends even with a resolver present."""
    parent, child, n = _parent_child()
    resolve = {"child1": child}.get
    layered = causal_cone_across(parent, [n["p_ts"]], resolve=resolve)
    assert layered.nodes == {n["p_ts"]}
    assert layered.invokes == []
    assert layered.children == {}


def test_cross_layer_unresolved_child_stays_on_frontier():
    """A resolver that cannot find the child leaves it on the frontier, not in children —
    the boundary remains visible rather than silently vanishing."""
    parent, _child, n = _parent_child()
    layered = causal_cone_across(parent, [n["p_te"]], resolve=lambda _rid: None)
    assert [iv.child_run_id for iv in layered.invokes] == ["child1"]
    assert layered.children == {}


# ── anchor selection helpers ─────────────────────────────────────────────────


def test_anchor_helpers():
    parent, _child, n = _parent_child()
    assert terminal_node(parent) == n["p_te"]
    assert terminal_node(UnfoldedGraph(run_id="empty", session_id="s")) is None
    assert tool_invocations(parent) == [n["p_tool"]]
    assert tool_invocations(parent, name="spawn_subagent") == [n["p_tool"]]
    assert tool_invocations(parent, name="nonesuch") == []
    # the general mechanism: any predicate over nodes
    assert set(select_nodes(parent, lambda nd: nd.hook == "task_end")) == {n["p_te"]}


# ── induced subgraph (what a consumer serialises) ────────────────────────────


def test_induced_subgraph_has_no_dangling_edges():
    g, n = _diamond()
    g.invokes.append(UnfoldedInvokes(source=n["D"], child_run_id="child1"))
    cone = causal_cone(g, n["D"])  # {A, B, C, D}, excludes E
    sub = induced_subgraph(g, cone)
    assert {nd.id for nd in sub.nodes} == cone
    # every retained edge has both endpoints inside the cone (induced) …
    for e in sub.edges:
        assert e.source in cone and e.target in cone
    # … and E's node is gone
    assert n["E"] not in {nd.id for nd in sub.nodes}
    # the C--data-->D edge is kept (both endpoints in the cone)
    assert any(e.source == n["C"] and e.target == n["D"] for e in sub.edges)
    # invokes sourced inside the cone are carried
    assert [iv.child_run_id for iv in sub.invokes] == ["child1"]
