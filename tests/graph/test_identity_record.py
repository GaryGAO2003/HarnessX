# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""v6 M9 — run identity: the three hashes at their three moments.

These pin the pure machinery (no running harness): the projection rule from U to
the phenotype (including the collapse of two invocations of one component and the
drop of tool/UNGRAPHED endpoints), stability across a write/read round trip,
per-layer sensitivity, the absent-with-reason record when U was never taken, and —
the strongest isolation form — that folding observed edges into the phenotype
never moves the genotype/deployment of any real ``examples/*`` config.
"""

from __future__ import annotations

from pathlib import Path

from harnessx.core.harness import HarnessConfig
from harnessx.core.runtime import RuntimeReg
from harnessx.graph.identity import deployment_hash, genotype_hash
from harnessx.graph.identity_record import (
    begin_run_identity,
    finalize_run_identity,
    identity_record,
    load_identity,
    project_observed_edges,
    write_identity,
)
from harnessx.graph.snapshot import to_graph
from harnessx.graph.types import GraphSnapshot, Node, NodeType
from harnessx.graph.unfold import UnfoldedEdge, UnfoldedGraph
from harnessx.graph.types import EdgeType, unfolded_id

from .test_runtime_overlay import _RtProc

_EXAMPLES = Path(__file__).resolve().parents[2] / "examples"

# Presentational keys the Lab reads but HarnessConfig has no field for.
_NON_CONFIG_KEYS = {"label", "description", "lab_visible", "workspace"}


def _load_example(cfg_path: Path) -> HarnessConfig:
    import yaml as _yaml

    raw = _yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    for k in _NON_CONFIG_KEYS:
        raw.pop(k, None)
    return HarnessConfig.from_yaml(_yaml.safe_dump(raw))


def _proc_snapshot() -> GraphSnapshot:
    """A minimal two-processor static graph — endpoints for projected edges."""
    nodes = {
        "proc:a": Node(node_id="proc:a", node_type=NodeType.PROCESSOR, label="A", metadata={"_target_": "m.A"}),
        "proc:b": Node(node_id="proc:b", node_type=NodeType.PROCESSOR, label="B", metadata={"_target_": "m.B"}),
    }
    return GraphSnapshot(nodes=nodes, edges=[])


def _u(*edges: UnfoldedEdge) -> UnfoldedGraph:
    return UnfoldedGraph(run_id="r", session_id="s", nodes=[], edges=list(edges))


def _obs(src_id: str, tgt_id: str, etype: EdgeType, meta=None) -> UnfoldedEdge:
    return UnfoldedEdge(source=src_id, target=tgt_id, edge_type=etype.value, metadata=meta or {})


# ── 5. projection rule ───────────────────────────────────────────────────────


def test_two_invocations_of_one_component_collapse():
    """A@t1→B@t2 and A@t3→B@t4 are two invocations of the SAME component pair —
    the projection collapses them to exactly one static proc:a→proc:b edge."""
    snap = _proc_snapshot()
    u = _u(
        _obs(unfolded_id("proc:a", 1), unfolded_id("proc:b", 2), EdgeType.OBSERVED_DATA, {"slot_key": "x"}),
        _obs(unfolded_id("proc:a", 3), unfolded_id("proc:b", 4), EdgeType.OBSERVED_DATA, {"slot_key": "y"}),
    )
    appended = project_observed_edges(snap, u)
    assert appended == 1, f"two invocations did not collapse: appended {appended}"
    projected = [e for e in snap.edges if e.edge_type == EdgeType.OBSERVED_DATA]
    assert len(projected) == 1
    e = projected[0]
    assert (e.source_id, e.target_id) == ("proc:a", "proc:b")
    assert e.metadata == {}, "per-invocation metadata (slot_key) must be dropped on projection"


def test_control_and_data_are_distinct_relations():
    """Same component pair, different observed families → two projected edges."""
    snap = _proc_snapshot()
    u = _u(
        _obs(unfolded_id("proc:a", 1), unfolded_id("proc:b", 2), EdgeType.OBSERVED_CONTROL),
        _obs(unfolded_id("proc:a", 3), unfolded_id("proc:b", 4), EdgeType.OBSERVED_DATA),
    )
    assert project_observed_edges(snap, u) == 2
    types = {e.edge_type for e in snap.edges}
    assert EdgeType.OBSERVED_CONTROL in types and EdgeType.OBSERVED_DATA in types


def test_tool_and_ungraphed_endpoints_dropped():
    """Observed edges touching a tool: node or the UNGRAPHED sentinel are not
    static components of G, so the projection drops them entirely."""
    snap = _proc_snapshot()
    u = _u(
        _obs(unfolded_id("proc:a", 1), unfolded_id("tool:Bash", 2), EdgeType.OBSERVED_CONTROL),
        _obs(unfolded_id("UNGRAPHED", 3), unfolded_id("proc:b", 4), EdgeType.OBSERVED_DATA),
        _obs(unfolded_id("proc:a", 5), unfolded_id("proc:b", 6), EdgeType.OBSERVED_DATA),
    )
    assert project_observed_edges(snap, u) == 1, "only the proc→proc edge should survive"
    survivor = [e for e in snap.edges if e.edge_type == EdgeType.OBSERVED_DATA][0]
    assert (survivor.source_id, survivor.target_id) == ("proc:a", "proc:b")


def test_runtime_node_endpoints_survive():
    """A projected endpoint that is a runtime-overlay node (rt:) is a deployed
    component and must survive — the membership set is nodes ∪ runtime_nodes."""
    snap = _proc_snapshot()
    snap.runtime_nodes["rt:proc:c"] = Node(
        node_id="rt:proc:c", node_type=NodeType.PROCESSOR, label="C", metadata={"_target_": "m.C"}
    )
    u = _u(_obs(unfolded_id("proc:a", 1), unfolded_id("rt:proc:c", 2), EdgeType.OBSERVED_DATA))
    assert project_observed_edges(snap, u) == 1
    assert any(e.target_id == "rt:proc:c" for e in snap.edges)


# ── 2. sensitivity: each layer moves on its own input, and only its own ──────


def _cfg(procs):
    return HarnessConfig(processors=procs)


def test_sensitivity_each_layer_independent():
    base = _cfg([{"_target_": "x.ProcA", "_hook_": "task_start"}])
    id_base = begin_run_identity(base, run_id="r", session_id="s")

    # structural change → genotype moves (and deployment, which contains it)
    structural = _cfg([{"_target_": "x.ProcA", "_hook_": "task_start"}, {"_target_": "x.ProcB", "_hook_": "step_end"}])
    id_struct = begin_run_identity(structural, run_id="r", session_id="s")
    assert id_struct.genotype != id_base.genotype

    # runtime-overlay change → deployment moves, genotype does NOT
    with_rt = _cfg([{"_target_": "x.ProcA", "_hook_": "task_start"}, RuntimeReg(proc=_RtProc(), hook="task_start")])
    id_rt = begin_run_identity(with_rt, run_id="r", session_id="s")
    assert id_rt.genotype == id_base.genotype, "runtime overlay must not move the genotype"
    assert id_rt.deployment != id_base.deployment, "runtime overlay must move the deployment"

    # observed-edge change → phenotype moves, genotype and deployment do NOT
    g0, d0 = id_base.genotype, id_base.deployment
    finalize_run_identity(
        id_base,
        _u(
            _obs(
                unfolded_id("proc:proc_a", 1),
                unfolded_id("hook:task_start", 2),
                EdgeType.OBSERVED_CONTROL,
            )
        ),
    )
    assert genotype_hash(id_base.snapshot) == g0, "observed edge moved the genotype"
    assert deployment_hash(id_base.snapshot) == d0, "observed edge moved the deployment"
    assert id_base.phenotype is not None and id_base.phenotype != d0, "observed edge did not move the phenotype"


# ── 1. stability across a write/read round trip ──────────────────────────────


def test_stability_round_trip(tmp_path):
    cfg = _cfg([{"_target_": "x.ProcA", "_hook_": "task_start"}, RuntimeReg(proc=_RtProc(), hook="task_start")])
    ident = begin_run_identity(cfg, run_id="run1", session_id="sess1")
    finalize_run_identity(
        ident,
        _u(
            _obs(
                unfolded_id("proc:proc_a", 1),
                unfolded_id("hook:task_start", 2),
                EdgeType.OBSERVED_DATA,
            )
        ),
    )
    path = write_identity(ident, base_dir=str(tmp_path))
    reloaded = load_identity(path)
    assert reloaded.genotype == ident.genotype
    assert reloaded.deployment == ident.deployment
    assert reloaded.phenotype == ident.phenotype
    assert reloaded.phenotype_absent_reason is None

    # deterministic: same inputs recompute to the same digests
    ident2 = begin_run_identity(cfg, run_id="run1", session_id="sess1")
    finalize_run_identity(
        ident2,
        _u(
            _obs(
                unfolded_id("proc:proc_a", 1),
                unfolded_id("hook:task_start", 2),
                EdgeType.OBSERVED_DATA,
            )
        ),
    )
    assert (ident2.genotype, ident2.deployment, ident2.phenotype) == (ident.genotype, ident.deployment, ident.phenotype)


# ── 4. absence: U not recorded → absent-with-reason, not a hash over nothing ─


def test_absent_phenotype_when_u_missing(tmp_path):
    cfg = _cfg([{"_target_": "x.ProcA", "_hook_": "task_start"}])
    ident = begin_run_identity(cfg, run_id="run2", session_id="sess2")
    finalize_run_identity(ident, None)  # U was never taken (unfold off)
    assert ident.phenotype is None, "phenotype must be absent, not a hash over an empty observed set"
    assert ident.phenotype_absent_reason == "unfold_disabled"
    assert ident.projected_edge_count == 0

    rec = identity_record(ident)
    assert rec["phenotype"] == {"absent": True, "reason": "unfold_disabled"}
    assert "hash" not in rec["phenotype"]

    reloaded = load_identity(write_identity(ident, base_dir=str(tmp_path)))
    assert reloaded.phenotype is None
    assert reloaded.phenotype_absent_reason == "unfold_disabled"


def test_empty_u_is_a_real_phenotype_not_absence():
    """Observation ON but nothing observed is a DIFFERENT claim from absence: a
    real phenotype hash (equal to the deployment, no observed edges) — never None."""
    cfg = _cfg([{"_target_": "x.ProcA", "_hook_": "task_start"}])
    ident = begin_run_identity(cfg, run_id="run3", session_id="sess3")
    finalize_run_identity(ident, _u())  # U recorded, but empty
    assert ident.phenotype is not None, "empty U must still yield a real phenotype"
    assert ident.phenotype_absent_reason is None
    assert ident.phenotype == ident.deployment, "no observed edges → phenotype == deployment"


# ── 3. isolation: the phenotype machinery never moves an existing genotype ───


def test_example_configs_isolated_from_projection():
    """Strongest isolation form: for every real examples/* config, folding
    observed edges in for the phenotype leaves genotype and deployment identical."""
    configs = sorted(_EXAMPLES.glob("*/harness_config.yaml"))
    assert configs, "no example configs found"
    for cfg_path in configs:
        cfg = _load_example(cfg_path)
        snap = to_graph(cfg)
        g0, d0 = genotype_hash(snap), deployment_hash(snap)
        # project observed edges between two real nodes of THIS config
        node_ids = list(snap.nodes)
        assert len(node_ids) >= 2, f"{cfg_path} has too few nodes to probe"
        a, b = node_ids[0], node_ids[1]
        u = _u(_obs(unfolded_id(a, 1), unfolded_id(b, 2), EdgeType.OBSERVED_CONTROL))
        project_observed_edges(snap, u)
        assert genotype_hash(snap) == g0, f"{cfg_path.parent.name}: projection moved the genotype"
        assert deployment_hash(snap) == d0, f"{cfg_path.parent.name}: projection moved the deployment"
