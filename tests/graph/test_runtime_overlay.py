"""Block 6 — runtime overlay (L5.1 / L5.1b / L5.3).

Runtime-only processors → runtime_nodes; instance plugin processors → runtime
nodes (id-deduped vs canonical); runtime slots never pollute snapshot.nodes;
ATTACHED_TO edges point at the main-graph hook skeleton.
"""

import pytest

from harnessx.core.harness import HarnessConfig
from harnessx.core.processor import MultiHookProcessor
from harnessx.core.runtime import RuntimeReg
from harnessx.graph.snapshot import to_graph
from harnessx.graph.types import EdgeType, NodeType
from harnessx.plugins.base import HarnessPlugin


class _RtProc(MultiHookProcessor):
    """Runtime-only processor: not serializable, no class hook."""

    _order = 7

    async def on_task_start(self, event):
        yield event


class _SlotProc(MultiHookProcessor):
    """Runtime processor with a dynamic slot write."""

    _writes_slot_keys = ("custom.slot",)

    async def on_step_end(self, event):
        yield event


class _StubPlugin(HarnessPlugin):
    name = "stub"
    version = "0.1.0"
    description = "stub"

    def __init__(self, proc):
        super().__init__()
        self.processors = [proc]


def _config_with(procs, plugins=None):
    return HarnessConfig(processors=procs, plugins=plugins or [])


def test_runtime_only_processor_creates_rt_node():
    proc = _RtProc()
    cfg = _config_with([RuntimeReg(proc=proc)])
    snap = to_graph(cfg)
    # rt node exists in runtime_nodes (not nodes)
    rt_ids = [n for n in snap.runtime_nodes if n.startswith("rt:")]
    assert len(rt_ids) == 1
    assert all(n.startswith("rt:") for n in snap.runtime_nodes)
    assert all(n.startswith("proc:") or n.startswith("hook:") or n.startswith("slot:")
               for n in snap.nodes)
    # genotype isolated: no runtime node in nodes
    assert "rt:" not in "".join(snap.nodes)


def test_runtime_attached_to_hook_skeleton():
    proc = _RtProc()
    cfg = _config_with([RuntimeReg(proc=proc, hook="task_start")])
    snap = to_graph(cfg)
    rt_id = next(n for n in snap.runtime_nodes if n.startswith("rt:"))
    attached = [e for e in snap.runtime_edges
                if e.edge_type == EdgeType.ATTACHED_TO and e.source_id == rt_id]
    assert len(attached) == 1
    assert attached[0].target_id == "hook:task_start"  # cross to main-graph skeleton


def test_runtime_slot_never_pollutes_nodes():
    proc = _SlotProc()
    cfg = _config_with([RuntimeReg(proc=proc)])
    snap = to_graph(cfg)
    # dynamic slot lives in runtime_nodes, not nodes
    assert "rt:slot:custom.slot" in snap.runtime_nodes
    assert "slot:custom.slot" not in snap.nodes
    # WRITES_TO edge from rt proc → rt slot
    rt_id = next(n for n in snap.runtime_nodes if n.startswith("rt:"))
    writes = [e for e in snap.runtime_edges
              if e.edge_type == EdgeType.WRITES_TO and e.source_id == rt_id]
    assert len(writes) == 1
    assert writes[0].target_id == "rt:slot:custom.slot"


def test_plugin_processor_creates_rt_node():
    proc = _RtProc()
    plugin = _StubPlugin(proc)
    cfg = _config_with([], plugins=[plugin])
    snap = to_graph(cfg)
    rt_ids = [n for n in snap.runtime_nodes if n.startswith("rt:")]
    assert len(rt_ids) == 1  # plugin processor appears


def test_plugin_processor_id_deduped_against_canonical():
    proc = _RtProc()
    plugin = _StubPlugin(proc)
    # same instance in both canonical RuntimeReg AND plugin.processors
    cfg = _config_with([RuntimeReg(proc=proc)], plugins=[plugin])
    snap = to_graph(cfg)
    rt_ids = [n for n in snap.runtime_nodes if n.startswith("rt:")]
    assert len(rt_ids) == 1  # deduped, not doubled


def test_dict_plugin_warns_and_skips():
    import logging
    from unittest.mock import patch

    cfg = _config_with([], plugins=[{"_target_": "some.Plugin"}])
    with patch("harnessx.graph.snapshot._log.warning") as mock_warn:
        snap = to_graph(cfg)
    mock_warn.assert_called_once()  # contract narrowing warning
    assert len(snap.runtime_nodes) == 0  # no enumerable processors


def test_runtime_node_id_stable():
    proc = _RtProc()
    cfg = _config_with([RuntimeReg(proc=proc)])
    snap1 = to_graph(cfg)
    snap2 = to_graph(cfg)
    assert set(snap1.runtime_nodes) == set(snap2.runtime_nodes)  # deterministic


def test_runtime_duplicate_target_gets_suffix():
    p1, p2 = _RtProc(), _RtProc()  # two instances, same class
    cfg = _config_with([RuntimeReg(proc=p1), RuntimeReg(proc=p2)])
    snap = to_graph(cfg)
    rt_ids = sorted(n for n in snap.runtime_nodes if n.startswith("rt:"))
    assert len(rt_ids) == 2
    # class _RtProc → slug "_rt_proc"; node ids "rt:__rt_proc" and "rt:__rt_proc__rt1"
    assert rt_ids[0].startswith("rt:__rt_proc")
    assert rt_ids[1].startswith("rt:__rt_proc__rt")  # suffix disambiguates


# ── Block 7: EXECUTES_BEFORE chains (L4.6 persistent / L5.6 mixed) ───────────


def _chain_edges(snap, in_runtime=False):
    """Return EXECUTES_BEFORE edges (main or runtime)."""
    edges = snap.runtime_edges if in_runtime else snap.edges
    return [e for e in edges if e.edge_type == EdgeType.EXECUTES_BEFORE]


def test_l46_persistent_chain_in_main_edges():
    # two serialized processors, same bucket, distinct orders
    cfg = HarnessConfig(processors=[
        {"_target_": "x.ProcA", "_hook_": "task_start", "_order_": 0},
        {"_target_": "x.ProcB", "_hook_": "task_start", "_order_": 50},
    ])
    snap = to_graph(cfg)
    chains = _chain_edges(snap)
    assert len(chains) == 1  # A → B (order 0 before 50)
    assert chains[0].source_id == "proc:proc_a"
    assert chains[0].target_id == "proc:proc_b"
    assert chains[0].metadata.get("hook") == "task_start"


def test_l46_chain_respects_after_topo():
    # A after B (same order) → B executes before A, despite A listed first
    cfg = HarnessConfig(processors=[
        {"_target_": "x.ProcA", "_hook_": "task_start", "_order_": 0,
         "_singleton_group_": "a", "_after_": ["b"]},
        {"_target_": "x.ProcB", "_hook_": "task_start", "_order_": 0,
         "_singleton_group_": "b"},
    ])
    snap = to_graph(cfg)
    chains = _chain_edges(snap)
    assert len(chains) == 1
    assert chains[0].source_id == "proc:proc_b"  # B first
    assert chains[0].target_id == "proc:proc_a"


def test_l46_empty_bucket_no_chain():
    cfg = HarnessConfig(processors=[
        {"_target_": "x.ProcA", "_hook_": "", "_order_": 0},
        {"_target_": "x.ProcB", "_hook_": "task_start", "_order_": 0},
    ])
    snap = to_graph(cfg)
    # empty-bucket ProcA excluded; ProcB alone → no chain
    assert _chain_edges(snap) == []


def test_l56_mixed_chain_in_runtime_edges():
    # serialized (S) + runtime (R) same bucket → chain spans both
    proc = _RtProc()  # no class hook → "*"
    cfg = HarnessConfig(processors=[
        {"_target_": "x.ProcA", "_hook_": "*", "_order_": 0},
        RuntimeReg(proc=proc, hook="*", order=0),
    ])
    snap = to_graph(cfg)
    chains = _chain_edges(snap, in_runtime=True)
    assert len(chains) == 1
    # endpoints: persistent proc: node + rt: node
    src, tgt = chains[0].source_id, chains[0].target_id
    assert src.startswith("proc:") and tgt.startswith("rt:")


def test_l56_sr_vs_rs_order_differs():
    """VM19: S,R vs R,S same bucket → deployment chain reversed."""
    def build(rt_first):
        proc = _RtProc()
        s = {"_target_": "x.ProcA", "_hook_": "*", "_order_": 0}
        r = RuntimeReg(proc=proc, hook="*", order=0)
        procs = [r, s] if rt_first else [s, r]
        return to_graph(HarnessConfig(processors=procs))

    snap_sr = build(rt_first=False)
    snap_rs = build(rt_first=True)
    chain_sr = _chain_edges(snap_sr, in_runtime=True)
    chain_rs = _chain_edges(snap_rs, in_runtime=True)
    assert len(chain_sr) == 1 and len(chain_rs) == 1
    # seq differs → chain direction differs (S→R vs R→S)
    assert (chain_sr[0].source_id, chain_sr[0].target_id) != (
        chain_rs[0].source_id, chain_rs[0].target_id)


def test_l56_genotype_isolated_from_mixed_chain():
    """VM10/19: runtime chain doesn't affect main edges (genotype)."""
    proc = _RtProc()
    cfg = HarnessConfig(processors=[
        {"_target_": "x.ProcA", "_hook_": "*", "_order_": 0},
        RuntimeReg(proc=proc, hook="*", order=0),
    ])
    snap = to_graph(cfg)
    assert _chain_edges(snap) == []  # main edges: no EXECUTES_BEFORE
    assert len(_chain_edges(snap, in_runtime=True)) == 1  # runtime has it


def test_l56_plugin_processor_in_chain():
    proc = _RtProc()
    plugin = _StubPlugin(proc)
    cfg = HarnessConfig(processors=[], plugins=[plugin])
    snap = to_graph(cfg)
    # single plugin processor → no chain (needs ≥2 in a bucket)
    # but with a canonical S in the same bucket:
    cfg2 = HarnessConfig(processors=[
        {"_target_": "x.ProcA", "_hook_": "*", "_order_": 0},
    ], plugins=[plugin])
    snap2 = to_graph(cfg2)
    chains = _chain_edges(snap2, in_runtime=True)
    assert len(chains) == 1
    src, tgt = chains[0].source_id, chains[0].target_id
    assert (src.startswith("proc:") and tgt.startswith("rt:")) or \
           (src.startswith("rt:") and tgt.startswith("proc:"))
