"""Contract matrix — one test per frozen invariant I1-I10 (v5.3 P0).

Each test's docstring first line is the invariant's canonical statement
(plan §0.1 / ``docs/graph-hardening-v5.3-engineering-plan.md`` §2).  Real
assertions lock semantics verifiable on today's tree; ``@pytest.mark.skip``
tests name the P1/P2/P3 work item that must land before the invariant is
fully checkable.  ``docs/graph-hardening-v5.3-P0-freeze.md`` records the full
disposition and the code realities behind each choice.

Disposition (2026-08-09, updated after block 15 landed):
    REAL  — I1, I2, I3, I4, I5, I6, I7, I8, I10
    SKIP  — I9 (P3 fail-closed gate)

I5/I7 were unblocked by the block-15 rewiring (_instantiate_runtime consumes
_processor_regs once; _route_processors buckets envelopes via the shared
stable_topological_sort) — their former skip reasons are preserved in git
history.
"""

from __future__ import annotations

import pytest

from harnessx.core import processor as core_processor
from harnessx.core.harness import HarnessConfig
from harnessx.core.processor import PROCESSOR_HOOK_NAMES
from harnessx.core.runtime import RuntimeReg, SerializedReg
from harnessx.graph import snapshot as graph_snapshot
from harnessx.graph.edit import GraphEdit, GraphEditError, GraphEditType, apply_edits
from harnessx.graph.identity import genotype_hash, phenotype_hash
from harnessx.graph.snapshot import to_graph
from harnessx.graph.types import SKELETON_HOOK_NAMES, Edge, EdgeType

from tests.graph.fixtures import (
    OrderedProbe,
    RuntimeProbe,
    StubPlugin,
    config_srsr,
    serialized_dict,
)

# ── I1 — canonical hook tuple single source (REAL) ───────────────────────────


def test_i1_processor_hook_names_single_canonical_source():
    """PROCESSOR_HOOK_NAMES 是 core 中唯一 canonical 8-hook tuple.

    Value + order are frozen, and every consumer references the SAME tuple
    object (identity), never a hardcoded copy.  The 10-hook graph skeleton is
    a distinct, larger constant and must not masquerade as this canonical.
    """
    # value + order — canonical 8, exact
    assert PROCESSOR_HOOK_NAMES == (
        "task_start", "step_start", "before_model", "after_model",
        "before_tool", "after_tool", "step_end", "task_end",
    )
    assert len(PROCESSOR_HOOK_NAMES) == 8

    # two names, one object inside core (L1.1a alias)
    assert core_processor.PROCESSOR_HOOK_NAMES is core_processor._HOOK_LIFECYCLE_ORDER

    # graph side references the SAME object (snapshot.py imports from core), not
    # a divergent hardcoded copy
    assert graph_snapshot.PROCESSOR_HOOK_NAMES is core_processor.PROCESSOR_HOOK_NAMES

    # migration guard: the 8 processor hooks are the 10-hook skeleton minus
    # {model, tool} (reconciled by processor.py:961).  Locks that no future
    # "old 10-hook" artifact can attach a processor to model/tool.
    assert len(SKELETON_HOOK_NAMES) == 10
    assert set(PROCESSOR_HOOK_NAMES) < set(SKELETON_HOOK_NAMES)
    assert set(SKELETON_HOOK_NAMES) - set(PROCESSOR_HOOK_NAMES) == {"model", "tool"}


# ── I2 — single writable registration sequence (REAL) ────────────────────────


def test_i2_processor_regs_single_writable_sequence():
    """_processor_regs 是唯一可写注册序列；processors 与 _rt_procs 只是视图.

    A mixed S-R-S-R config keeps all four registrations in ``_processor_regs``
    (positional); ``processors`` exposes only SerializedReg dict_refs and
    ``_rt_procs`` only RuntimeRegs.  Both write APIs flow through the canonical.
    """
    cfg = config_srsr()  # [S, R, S, R]
    assert len(cfg._processor_regs) == 4
    kinds = [type(r).__name__ for r in cfg._processor_regs]
    assert kinds == ["SerializedReg", "RuntimeReg", "SerializedReg", "RuntimeReg"]

    # processors view: only the two SerializedReg dict_refs
    assert len(cfg.processors) == 2
    assert all(isinstance(p, dict) for p in cfg.processors)

    # _rt_procs view: only the two RuntimeRegs, as a tuple (a list view's
    # .append() would silently no-op; a tuple forces writes through the API)
    assert isinstance(cfg._rt_procs, tuple)
    assert len(cfg._rt_procs) == 2
    assert all(isinstance(r, RuntimeReg) for r in cfg._rt_procs)

    # write API 1: append through the canonical (serialized view unchanged)
    cfg.add_runtime_reg(RuntimeProbe())
    assert len(cfg._processor_regs) == 5
    assert len(cfg._rt_procs) == 3
    assert len(cfg.processors) == 2

    # write API 2: replace the full mixed sequence
    cfg.replace_processor_regs([serialized_dict("x.Y", singleton_group="g")])
    assert len(cfg._processor_regs) == 1
    assert isinstance(cfg._processor_regs[0], SerializedReg)
    assert len(cfg._rt_procs) == 0
    assert len(cfg.processors) == 1


# ── I3 — SerializedReg dynamic dict_ref reads (REAL, unit slice) ─────────────


def test_i3_serialized_reg_dynamic_dict_ref():
    """SerializedReg presence/value 是 dict_ref 动态读取.

    Values are re-read from the shared dict on every access (never cached), so
    a later mutation is reflected immediately; presence flags track key
    existence.  Scope: the SerializedReg primitive (landed early in
    runtime.py).  The end-to-end graph/runtime co-read (VM20b) is exercised in
    P2 once the graph snapshot consumes _processor_regs via dict_ref.
    """
    d = {"_target_": "x.Y"}
    reg = SerializedReg(dict_ref=d)

    # missing key → presence False, natural-fallback value
    assert reg.hook_present is False and reg.hook == ""
    assert reg.order_present is False and reg.order == 0

    # dynamic: mutate the shared dict → property re-reads the NEW value
    d["_hook_"] = "before_model"
    assert reg.hook_present is True and reg.hook == "before_model"
    d["_hook_"] = "after_model"
    assert reg.hook == "after_model"  # not cached

    # explicit-empty hook: present but empty (empty bucket, never executes)
    d["_hook_"] = ""
    assert reg.hook_present is True and reg.hook == ""


# ── I4 — RuntimeReg never mutates the shared instance (REAL) ──────────────────


def test_i4_runtime_reg_does_not_mutate_instance():
    """RuntimeReg 不修改共享 processor 实例.

    Registration metadata lives on the frozen RuntimeReg record, never written
    onto the wrapped processor.  (Instance-state binding is a separate
    Harness-construction concern, L2.3b, not registration.)
    """
    proc = RuntimeProbe()
    before = dict(proc.__dict__)

    reg = RuntimeReg(proc=proc, hook="before_model", order=9, singleton_group="g", after=("h",))
    assert proc.__dict__ == before  # registration did not touch the instance
    assert reg.proc is proc

    # frozen record — metadata cannot be rewritten in place
    with pytest.raises(AttributeError):
        reg.hook = "after_model"  # type: ignore[misc]


# ── I5 — runloop executes only bare processors (REAL since block 15) ─────────


def test_i5_runloop_executes_only_bare_processors():
    """runloop 只执行裸 processor，不执行 RuntimeReg/RoutingEnvelope.

    The routed ``_HarnessRuntime.processors`` dict IS the runloop's execution
    surface (hooks iterate its buckets directly).  After the block-15 rewiring
    every bucket holds bare processor instances only — records and envelopes
    are unwrapped by ``_route_processors``; the registered runtime instance
    itself (identity) lands in the bucket its record names.
    """
    from harnessx.core.harness import _instantiate_runtime
    from harnessx.core.runtime import RoutingEnvelope

    probe = RuntimeProbe()
    cfg = HarnessConfig(
        processors=[
            serialized_dict("tests.graph.fixtures.RuntimeProbe", hook="task_start"),
            RuntimeReg(proc=probe, hook="before_model", order=3),
        ],
        plugins=[StubPlugin([OrderedProbe()])],
    )
    pd = _instantiate_runtime(cfg).processors

    all_procs = [p for procs in pd.values() for p in procs]
    assert len(all_procs) == 3  # serialized + runtime + plugin tail
    for p in all_procs:
        assert not isinstance(p, (RuntimeReg, RoutingEnvelope, SerializedReg, dict)), (
            f"record/envelope reached the execution surface: {type(p).__name__}"
        )

    # the record's proc is executed by identity, in the record's bucket
    assert probe in pd.get("before_model", [])
    # the serialized entry instantiated a FRESH instance (never the record's)
    assert all(p is not probe for p in pd.get("task_start", []))


# ── I6 — runtime overlay excluded from genotype hash (REAL) ──────────────────


def test_i6_runtime_overlay_excluded_from_genotype_hash():
    """nodes/edges 只表示 genotype；runtime overlay 不进 genotype hash.

    Two configs identical except for a runtime-only registration produce the
    SAME genotype hash; the runtime proc surfaces in runtime_nodes only.
    """
    snap_bare = to_graph(HarnessConfig(processors=[]))
    snap_rt = to_graph(HarnessConfig(processors=[
        RuntimeReg(proc=RuntimeProbe(), hook="task_start"),
    ]))

    # the runtime proc is represented — but only in the overlay
    assert any(n.startswith("rt:") for n in snap_rt.runtime_nodes)
    assert not any(n.startswith("rt:") for n in snap_bare.runtime_nodes)

    # genotype hash is invariant to the runtime overlay
    assert genotype_hash(snap_rt) == genotype_hash(snap_bare)


# ── I7 — EXECUTES_BEFORE shares the routing sort (REAL since block 15) ────────


def _chain_sequence(edges, bucket):
    """Reconstruct the linear node order of a bucket's EXECUTES_BEFORE chain."""
    pairs = [(e.source_id, e.target_id) for e in edges
             if e.edge_type == EdgeType.EXECUTES_BEFORE
             and e.metadata.get("hook") == bucket]
    succ = dict(pairs)
    heads = set(succ) - set(succ.values())
    assert len(heads) == 1, f"chain must be linear, got heads={heads}"
    seq = [heads.pop()]
    while seq[-1] in succ:
        seq.append(succ[seq[-1]])
    return seq


def test_i7_executes_before_shares_routing_sort():
    """EXECUTES_BEFORE 与实际 routing 排序同源（共享 stable_topological_sort）.

    The same config is fed to both sides: the graph's mixed chain (L5.6) and
    the runtime router now sort through the ONE shared function, so the chain
    node order must equal the routed bucket order — order first, then _after_
    topology within an order, then seq.
    """
    from harnessx.core.harness import _instantiate_runtime

    a, b, c = RuntimeProbe(), RuntimeProbe(), RuntimeProbe()
    cfg = HarnessConfig(processors=[
        RuntimeReg(proc=a, hook="task_start", order=50,
                   singleton_group="ga", after=("gb",)),
        RuntimeReg(proc=b, hook="task_start", order=50, singleton_group="gb"),
        RuntimeReg(proc=c, hook="task_start", order=0),
    ])

    # runtime side: order 0 first, then same-order after-topology (b before a)
    routed = _instantiate_runtime(cfg).processors["task_start"]
    assert routed == [c, b, a]

    # graph side: reconstruct the chain and compare via singleton_group labels
    snap = to_graph(cfg)
    seq = _chain_sequence(snap.runtime_edges, "task_start")
    sg_of = {nid: snap.runtime_nodes[nid].metadata.get("_singleton_group_", "")
             for nid in seq}
    assert [sg_of[n] for n in seq] == ["", "gb", "ga"]  # c → b → a, same order

    # mixed S+R same bucket: chain direction == routed order (S seq 0 first)
    probe = RuntimeProbe()
    cfg2 = HarnessConfig(processors=[
        serialized_dict("tests.graph.fixtures.RuntimeProbe",
                        hook="task_start", order=0),
        RuntimeReg(proc=probe, hook="task_start", order=0),
    ])
    routed2 = _instantiate_runtime(cfg2).processors["task_start"]
    assert len(routed2) == 2 and routed2[1] is probe  # serialized first (seq)
    seq2 = _chain_sequence(to_graph(cfg2).runtime_edges, "task_start")
    assert seq2[0].startswith("proc:") and seq2[1].startswith("rt:")


# ── I8 — apply_edits() rolls back on failure (REAL) ──────────────────────────


def test_i8_apply_edits_failure_leaves_snapshot_unchanged():
    """apply_edits() 失败时原 snapshot 完整不变.

    A failing edit raises and the ORIGINAL snapshot is untouched (apply_edits
    mutates a deepcopy; on failure nothing is returned to the caller).
    """
    snap = to_graph(HarnessConfig(processors=[]))
    nodes_before = set(snap.nodes)
    edges_before = len(snap.edges)
    geno_before = genotype_hash(snap)

    bad = GraphEdit(edit_type=GraphEditType.INSERT_NODE)  # node_spec=None → raises
    with pytest.raises(GraphEditError):
        apply_edits(snap, [bad])

    assert set(snap.nodes) == nodes_before
    assert len(snap.edges) == edges_before
    assert genotype_hash(snap) == geno_before


# ── I9 — rejected candidate never enters active (SKIP: P3 fail-closed) ────────


@pytest.mark.skip(reason=(
    "I9 blocked on P3 fail-closed gate: the graph gate currently fails OPEN — "
    "graph_gate.py:145-154 downgrades ImportError and generic build() "
    "Exceptions to warnings, so a candidate whose build() raises still passes "
    "(only HarnessConflictError is fail-closed). P3 must make build failures "
    "reject the candidate; proper check is a candidate-gate integration test."
))
def test_i9_rejected_candidate_never_enters_active():
    """rejected candidate 不得进入 executor、ledger 或 active config."""


# ── I10 — observed trace does not rewrite the genotype (REAL) ─────────────────


def test_i10_observed_trace_does_not_rewrite_genotype():
    """observed trace 只提供证据，不静默改写声明式 graph.

    Adding an OBSERVED_* edge changes only the phenotype hash; the genotype
    (declarative) hash is invariant to observed evidence.
    """
    snap = to_graph(HarnessConfig(processors=[]))
    geno_before = genotype_hash(snap)
    pheno_before = phenotype_hash(snap)

    ids = sorted(snap.nodes)
    assert len(ids) >= 2  # skeleton always provides multiple hook nodes
    snap.edges.append(Edge(
        source_id=ids[0], target_id=ids[1],
        edge_type=EdgeType.OBSERVED_CONTROL, metadata={},
    ))

    assert genotype_hash(snap) == geno_before      # declarative graph untouched
    assert phenotype_hash(snap) != pheno_before    # evidence lands in phenotype
