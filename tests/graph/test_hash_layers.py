"""Block 8 — three-layer hashing (L6): genotype ⊆ deployment ⊆ phenotype.

genotype: main graph only (runtime overlay invisible — I6).
deployment: genotype + runtime overlay, no observed edges.
phenotype: deployment + observed edges.
"""

import pytest

from harnessx.core.harness import HarnessConfig
from harnessx.core.runtime import RuntimeReg
from harnessx.graph.identity import (
    _sort_dict,
    deployment_hash,
    genotype_hash,
    phenotype_hash,
)
from harnessx.graph.snapshot import to_graph
from harnessx.graph.types import Edge, EdgeType

from .test_runtime_overlay import _RtProc


def _cfg(procs=None, plugins=None):
    return HarnessConfig(processors=procs or [], plugins=plugins or [])


# ── genotype isolation from runtime overlay (I6 / VM10) ─────────────────────


def test_genotype_ignores_runtime_overlay():
    base = _cfg([{"_target_": "x.ProcA", "_hook_": "task_start"}])
    with_rt = _cfg([
        {"_target_": "x.ProcA", "_hook_": "task_start"},
        RuntimeReg(proc=_RtProc(), hook="task_start"),
    ])
    g1 = genotype_hash(to_graph(base))
    g2 = genotype_hash(to_graph(with_rt))
    assert g1 == g2  # runtime overlay invisible to genotype


def test_deployment_changes_with_runtime_overlay():
    base = _cfg([{"_target_": "x.ProcA", "_hook_": "task_start"}])
    with_rt = _cfg([
        {"_target_": "x.ProcA", "_hook_": "task_start"},
        RuntimeReg(proc=_RtProc(), hook="task_start"),
    ])
    d1 = deployment_hash(to_graph(base))
    d2 = deployment_hash(to_graph(with_rt))
    assert d1 != d2  # deployment sees the overlay


# ── observed edges: genotype/deployment exclude, phenotype includes ─────────


def test_observed_edges_excluded_from_genotype_and_deployment():
    snap = to_graph(_cfg([{"_target_": "x.ProcA", "_hook_": "task_start"}]))
    g_before, d_before = genotype_hash(snap), deployment_hash(snap)

    snap.edges.append(Edge(
        source_id="proc:proc_a", target_id="hook:task_start",
        edge_type=EdgeType.OBSERVED_CONTROL, metadata={},
    ))
    assert genotype_hash(snap) == g_before      # observed excluded
    assert deployment_hash(snap) == d_before    # observed excluded


def test_observed_edges_change_phenotype():
    snap = to_graph(_cfg([{"_target_": "x.ProcA", "_hook_": "task_start"}]))
    p_before = phenotype_hash(snap)
    snap.edges.append(Edge(
        source_id="proc:proc_a", target_id="hook:task_start",
        edge_type=EdgeType.OBSERVED_CONTROL, metadata={},
    ))
    assert phenotype_hash(snap) != p_before     # phenotype includes observed


# ── hashes are cached on the snapshot (L6.3 contract) ──────────────────────


def test_hashes_cached_on_snapshot():
    snap = to_graph(_cfg([{"_target_": "x.ProcA", "_hook_": "task_start"}]))
    assert snap.genotype_hash == "" and snap.deployment_hash == ""
    g = genotype_hash(snap)
    d = deployment_hash(snap)
    p = phenotype_hash(snap)
    assert snap.genotype_hash == g
    assert snap.deployment_hash == d
    assert snap.phenotype_hash == p


def test_hash_deterministic_across_calls():
    cfg = _cfg([
        {"_target_": "x.ProcA", "_hook_": "task_start"},
        RuntimeReg(proc=_RtProc(), hook="task_start"),
    ])
    assert deployment_hash(to_graph(cfg)) == deployment_hash(to_graph(cfg))


# ── S,R vs R,S deployment differs, genotype same (VM19/VM20e) ──────────────


def test_sr_vs_rs_deployment_differs_genotype_same():
    s = {"_target_": "x.ProcA", "_hook_": "*", "_order_": 0}

    def build(rt_first):
        r = RuntimeReg(proc=_RtProc(), hook="*", order=0)
        return to_graph(_cfg([r, s] if rt_first else [s, r]))

    snap_sr, snap_rs = build(False), build(True)
    assert genotype_hash(snap_sr) == genotype_hash(snap_rs)      # persistent identical
    assert deployment_hash(snap_sr) != deployment_hash(snap_rs)  # chain reversed


# ── _sort_dict at_root semantics (L6.2) ────────────────────────────────────


def test_sort_dict_root_whitelist_sorts():
    out = _sort_dict({"_after_": ["c", "a", "b"]})
    assert out["_after_"] == ["a", "b", "c"]  # whitelist key sorted


def test_sort_dict_root_non_whitelist_keeps_order():
    out = _sort_dict({"tags": ["c", "a", "b"]})
    assert out["tags"] == ["c", "a", "b"]  # not whitelisted → order preserved


def test_sort_dict_nested_always_preserves_order():
    out = _sort_dict({"_ctor_kwargs_": {"_after_": ["c", "a"], "tags": ["z", "y"]}})
    # nested: even a whitelisted key name keeps its order
    assert out["_ctor_kwargs_"]["_after_"] == ["c", "a"]
    assert out["_ctor_kwargs_"]["tags"] == ["z", "y"]


def test_sort_dict_nested_deep_preserves_order():
    out = _sort_dict({"a": {"b": {"_hooks_": ["z", "a"]}}})
    assert out["a"]["b"]["_hooks_"] == ["z", "a"]
