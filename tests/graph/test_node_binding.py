"""M2a — graph node id ⇄ live processor binding.

``build_node_binding(config)`` maps every PROCESSOR node id in ``to_graph`` to
the live instance the runtime executes for it, covering both the persistent
(``proc:``) and runtime-overlay (``rt:``) schemes.  Node ids come from the
single authority ``assign_processor_node_ids`` — the sole place either scheme is
formed.  These tests pin the bijection, the exact ids (golden), repeated
registration, config-order-vs-execution-order divergence, and that ``to_graph``
and the binding read the same authority.
"""

from harnessx.core.harness import HarnessConfig, _instantiate_runtime
from harnessx.core.processor import MultiHookProcessor
from harnessx.core.runtime import RuntimeReg
from harnessx.graph.binding import build_node_binding
from harnessx.graph.snapshot import assign_processor_node_ids, to_graph
from harnessx.graph.types import NodeType

MOD = __name__  # instantiable _target_ prefix (this module is importable)


# ── instantiable test processors ────────────────────────────────────────────


class _AlphaProc(MultiHookProcessor):
    def __init__(self, tag=""):
        super().__init__()
        self.tag = tag

    async def on_task_start(self, event):
        yield event


class _BetaProc(MultiHookProcessor):
    def __init__(self, tag=""):
        super().__init__()
        self.tag = tag

    async def on_step_end(self, event):
        yield event


class _RtOnly(MultiHookProcessor):
    def __init__(self, tag=""):
        super().__init__()
        self.tag = tag

    async def on_task_start(self, event):
        yield event


def _t(cls, **kw):
    return {"_target_": f"{MOD}.{cls}", **kw}


def _graph_proc_ids(cfg):
    snap = to_graph(cfg)
    return {nid for nid, n in {**snap.nodes, **snap.runtime_nodes}.items() if n.node_type == NodeType.PROCESSOR}


# ── 1. bijection ─────────────────────────────────────────────────────────────


def test_bijection_persistent_only():
    cfg = HarnessConfig(
        processors=[
            _t("_AlphaProc", _hook_="task_start", tag="a1"),
            _t("_BetaProc", _hook_="step_end", tag="b1"),
            _t("_AlphaProc", _hook_="task_start", tag="a2"),
        ]
    )
    binding = build_node_binding(cfg)
    assert set(binding) == _graph_proc_ids(cfg)


def test_bijection_runtime_overlay():
    cfg = HarnessConfig(
        processors=[
            _t("_AlphaProc", _hook_="task_start", tag="a1"),
            RuntimeReg(proc=_RtOnly(tag="r1"), hook="task_start"),
            RuntimeReg(proc=_RtOnly(tag="r2"), hook="task_start"),
        ]
    )
    binding = build_node_binding(cfg)
    graph_ids = _graph_proc_ids(cfg)
    assert set(binding) == graph_ids
    # both schemes are covered
    assert any(k.startswith("proc:") for k in binding)
    assert any(k.startswith("rt:") for k in binding)


def test_bijection_mixed_interleaved():
    cfg = HarnessConfig(
        processors=[
            RuntimeReg(proc=_RtOnly(tag="r1"), hook="task_start"),
            _t("_AlphaProc", _hook_="task_start", tag="a1"),
            RuntimeReg(proc=_RtOnly(tag="r2"), hook="task_start"),
            _t("_BetaProc", _hook_="step_end", tag="b1"),
        ]
    )
    binding = build_node_binding(cfg)
    assert set(binding) == _graph_proc_ids(cfg)


def test_bijection_empty_config():
    cfg = HarnessConfig(processors=[])
    binding = build_node_binding(cfg)
    assert binding == {}
    assert _graph_proc_ids(cfg) == set()


def test_binding_has_no_id_outside_graph():
    cfg = HarnessConfig(
        processors=[
            _t("_AlphaProc", _hook_="task_start", tag="a1"),
            RuntimeReg(proc=_RtOnly(tag="r1")),
        ]
    )
    binding = build_node_binding(cfg)
    # nothing in the binding is absent from the graph, and vice versa
    assert set(binding) <= _graph_proc_ids(cfg)
    assert _graph_proc_ids(cfg) <= set(binding)


# ── 2. golden ids (pinned by running the code — no id may change) ────────────


def test_golden_ids_persistent():
    cfg = HarnessConfig(
        processors=[
            _t("_AlphaProc", _hook_="task_start", tag="a1"),
            _t("_BetaProc", _hook_="step_end", tag="b1"),
            _t("_AlphaProc", _hook_="task_start", tag="a2"),
        ]
    )
    # graph node ids
    assert _graph_proc_ids(cfg) == {
        "proc:__alpha_proc",
        "proc:__alpha_proc__2",
        "proc:__beta_proc",
    }
    # authority persistent order (config order, count-all per target)
    ids = assign_processor_node_ids(cfg)
    assert [nid for _, nid in ids.persistent] == [
        "proc:__alpha_proc",
        "proc:__beta_proc",
        "proc:__alpha_proc__2",
    ]
    assert ids.runtime == []
    # binding maps each id to the right instance (by ctor-injected tag)
    binding = build_node_binding(cfg)
    assert binding["proc:__alpha_proc"].tag == "a1"
    assert binding["proc:__alpha_proc__2"].tag == "a2"
    assert binding["proc:__beta_proc"].tag == "b1"


def test_golden_ids_runtime_overlay():
    cfg = HarnessConfig(
        processors=[
            _t("_AlphaProc", _hook_="task_start", tag="a1"),
            RuntimeReg(proc=_RtOnly(tag="r1"), hook="task_start"),
            RuntimeReg(proc=_RtOnly(tag="r2"), hook="task_start"),
        ]
    )
    assert _graph_proc_ids(cfg) == {
        "proc:__alpha_proc",
        "rt:__rt_only",
        "rt:__rt_only__rt1",
    }
    ids = assign_processor_node_ids(cfg)
    assert [nid for _, nid in ids.persistent] == ["proc:__alpha_proc"]
    assert [nid for _, nid in ids.runtime] == ["rt:__rt_only", "rt:__rt_only__rt1"]
    binding = build_node_binding(cfg)
    assert binding["rt:__rt_only"].tag == "r1"
    assert binding["rt:__rt_only__rt1"].tag == "r2"


# ── 3. repeated registration → distinct ids, distinct instances ──────────────


def test_repeated_persistent_distinct_instances():
    cfg = HarnessConfig(
        processors=[
            _t("_AlphaProc", _hook_="task_start", tag="a1"),
            _t("_AlphaProc", _hook_="task_start", tag="a2"),
            _t("_AlphaProc", _hook_="task_start", tag="a3"),
        ]
    )
    binding = build_node_binding(cfg)
    ids = ["proc:__alpha_proc", "proc:__alpha_proc__2", "proc:__alpha_proc__3"]
    assert set(binding) == set(ids)
    insts = [binding[i] for i in ids]
    # three distinct live instances (not just distinct ids)
    assert len({id(p) for p in insts}) == 3
    assert [p.tag for p in insts] == ["a1", "a2", "a3"]


def test_repeated_runtime_distinct_instances():
    p1, p2 = _RtOnly(tag="r1"), _RtOnly(tag="r2")
    cfg = HarnessConfig(
        processors=[
            RuntimeReg(proc=p1, hook="task_start"),
            RuntimeReg(proc=p2, hook="task_start"),
        ]
    )
    binding = build_node_binding(cfg)
    ids = ["rt:__rt_only", "rt:__rt_only__rt1"]
    assert set(binding) == set(ids)
    assert id(binding[ids[0]]) != id(binding[ids[1]])
    # runtime overlay binds the ACTUAL config instances (no re-instantiation)
    assert binding[ids[0]] is p1
    assert binding[ids[1]] is p2


# ── 4. config order vs per-hook execution order genuinely diverge ────────────


def test_order_divergence_binding_follows_config_not_execution():
    # Same class twice on the same hook; _order reverses the execution order
    # relative to config order.  Node ids are config-order; execution order is
    # the per-hook topological sort — these deliberately disagree here.
    cfg = HarnessConfig(
        processors=[
            _t("_AlphaProc", _hook_="task_start", _order_=10, tag="first"),
            _t("_AlphaProc", _hook_="task_start", _order_=1, tag="second"),
        ]
    )

    # Runtime execution order (topological): order=1 before order=10.
    rt = _instantiate_runtime(cfg)
    exec_tags = [p.tag for p in rt.processors["task_start"]]
    assert exec_tags == ["second", "first"]  # divergence is real

    # Node-id order is config order: proc:__alpha_proc = first registration.
    ids = assign_processor_node_ids(cfg)
    assert [nid for _, nid in ids.persistent] == [
        "proc:__alpha_proc",
        "proc:__alpha_proc__2",
    ]

    # The binding maps each id to the instance of the record its id was derived
    # from — config order — NOT the (reversed) execution order.
    binding = build_node_binding(cfg)
    assert binding["proc:__alpha_proc"].tag == "first"
    assert binding["proc:__alpha_proc__2"].tag == "second"


# ── 5. single authority: to_graph and the binding share one id source ────────


def test_single_authority_shared_id_source():
    cfg = HarnessConfig(
        processors=[
            _t("_AlphaProc", _hook_="task_start", tag="a1"),
            _t("_AlphaProc", _hook_="task_start", tag="a2"),
            RuntimeReg(proc=_RtOnly(tag="r1"), hook="task_start"),
            RuntimeReg(proc=_RtOnly(tag="r2"), hook="task_start"),
            _t("_BetaProc", _hook_="step_end", tag="b1"),
        ]
    )
    ids = assign_processor_node_ids(cfg)
    authority_ids = {nid for _, nid in ids.persistent} | {nid for _, nid in ids.runtime}

    # to_graph's PROCESSOR node ids must equal the authority's ids exactly:
    # a second, divergent id walk anywhere would break this.
    assert _graph_proc_ids(cfg) == authority_ids

    # build_node_binding derives its keys from the SAME authority (all targets
    # here are instantiable, so coverage is total).
    assert set(build_node_binding(cfg)) == authority_ids
