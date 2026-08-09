"""Block 15 — L2.3c rule 4 (envelope routing) + L2.3b (owner claim).

VM16: records bucket by reg.hook, proc_dict holds bare processors only,
empty bucket routed but inert, defensive bare-instance path.
VM18: canonical consumed once, order → after-topo → seq stability,
plugin tail envelopes with id-dedup.
VM17: single-owner fail-fast, idempotent same-token claim, cleanup release,
construction-failure rollback; spawn-style per-child clone passes.
"""

import asyncio

import pytest

from harnessx.core.harness import Harness, HarnessConfig, _instantiate_runtime
from harnessx.core.model_config import ModelConfig
from harnessx.core.processor import MultiHookProcessor
from harnessx.core.runtime import _OWNERS, RuntimeReg
from harnessx.providers.unconfigured import UnConfiguredProvider


class RProcA(MultiHookProcessor):
    _order = 0

    async def on_task_start(self, event):
        yield event


class RProcB(MultiHookProcessor):
    _order = 50

    async def on_task_start(self, event):
        yield event


class RProcAfter(MultiHookProcessor):
    _order = 50
    _singleton_group = "r.after"
    _after = ("r.b",)

    async def on_task_start(self, event):
        yield event


class _ExplodingBind(MultiHookProcessor):
    async def on_task_start(self, event):
        yield event

    def _bind_sub_harnesses(self, subs):
        raise RuntimeError("boom")


def _procs_of(config):
    return _instantiate_runtime(config).processors


def _mk(config):
    return Harness(ModelConfig(main=UnConfiguredProvider()), config)


# ── VM16: record routing ────────────────────────────────────────────────────


def test_record_routes_to_reg_hook_bucket():
    proc = RProcA()
    cfg = HarnessConfig(processors=[RuntimeReg(proc=proc, hook="task_end")])
    pd = _procs_of(cfg)
    assert pd.get("task_end") == [proc]           # bare instance, reg's bucket
    assert all(proc not in v for k, v in pd.items() if k != "task_end")


def test_proc_dict_values_are_bare_processors():
    cfg = HarnessConfig(processors=[RuntimeReg(proc=RProcA())])
    for procs in _procs_of(cfg).values():
        for p in procs:
            assert not isinstance(p, RuntimeReg)  # records never enter (I5)


def test_explicit_empty_hook_routes_to_inert_bucket():
    tgt = f"{RProcA.__module__}.{RProcA.__qualname__}"
    cfg = HarnessConfig(processors=[{"_target_": tgt, "_hook_": ""}])
    pd = _procs_of(cfg)
    assert len(pd.get("", [])) == 1               # routed to "" (runloop skips)
    assert not pd.get("*")                        # never leaks into "*"


def test_missing_hook_key_falls_back_to_natural():
    tgt = f"{RProcA.__module__}.{RProcA.__qualname__}"
    cfg = HarnessConfig(processors=[{"_target_": tgt}])
    pd = _procs_of(cfg)
    assert len(pd.get("*", [])) == 1              # MHP natural bucket


def test_bare_instance_defensive_path_with_override():
    from harnessx.core.harness import _route_processors

    proc = RProcA()
    proc.__hx_hook_override__ = "step_end"
    pd = _route_processors([proc])
    assert pd.get("step_end") == [proc]


# ── VM18: canonical once + ordering ─────────────────────────────────────────


def test_canonical_consumed_once_no_duplicates():
    proc = RProcA()
    cfg = HarnessConfig(processors=[RuntimeReg(proc=proc, hook="task_start")])
    pd = _procs_of(cfg)
    total = sum(1 for procs in pd.values() for p in procs if p is proc)
    assert total == 1                              # never routed twice


def test_bucket_order_by_order_then_seq():
    a, b = RProcA(), RProcB()
    cfg = HarnessConfig(processors=[
        RuntimeReg(proc=b, hook="task_start", order=50),   # listed first
        RuntimeReg(proc=a, hook="task_start", order=0),
    ])
    assert _procs_of(cfg)["task_start"] == [a, b]  # order beats registration seq


def test_after_topology_within_same_order():
    early, late = RProcAfter(), RProcB()
    cfg = HarnessConfig(processors=[
        RuntimeReg(proc=early, hook="task_start", order=50,
                   singleton_group="r.after", after=("r.b",)),
        RuntimeReg(proc=late, hook="task_start", order=50,
                   singleton_group="r.b"),
    ])
    assert _procs_of(cfg)["task_start"] == [late, early]  # after target first


def test_plugin_tail_envelopes_with_id_dedup():
    from harnessx.plugins.base import HarnessPlugin

    class _P(HarnessPlugin):
        name = "p"
        version = "0"
        description = "p"

        def __init__(self, proc):
            super().__init__()
            self.processors = [proc]

    shared = RProcA()
    extra = RProcB()
    cfg = HarnessConfig(
        processors=[RuntimeReg(proc=shared, hook="task_start")],
        plugins=[_P(shared), _P(extra)],
    )
    pd = _procs_of(cfg)
    flat = [p for procs in pd.values() for p in procs]
    assert flat.count(shared) == 1                 # id-dedup vs canonical
    assert flat.count(extra) == 1                  # plugin tail joined routing


# ── VM17: single-owner claim ────────────────────────────────────────────────


def test_second_harness_same_instance_fails_fast():
    proc = RProcA()
    cfg = HarnessConfig(processors=[RuntimeReg(proc=proc)])
    h1 = _mk(cfg)
    try:
        with pytest.raises(ValueError, match="单 owner|owner"):
            _mk(cfg)
    finally:
        asyncio.run(h1.cleanup())


def test_same_instance_twice_in_one_config_is_idempotent():
    proc = RProcA()
    cfg = HarnessConfig(processors=[
        RuntimeReg(proc=proc, hook="task_start"),
        RuntimeReg(proc=proc, hook="task_end"),
    ])
    h = _mk(cfg)  # same token claims once — no error
    try:
        assert id(proc) in _OWNERS
    finally:
        asyncio.run(h.cleanup())
    assert id(proc) not in _OWNERS                 # cleanup released


def test_cleanup_release_allows_reclaim():
    proc = RProcA()
    cfg = HarnessConfig(processors=[RuntimeReg(proc=proc)])
    h1 = _mk(cfg)
    asyncio.run(h1.cleanup())
    h2 = _mk(cfg)                                  # released → claimable again
    asyncio.run(h2.cleanup())


def test_construction_failure_rolls_back_claims():
    proc = _ExplodingBind()
    cfg = HarnessConfig(processors=[RuntimeReg(proc=proc)])
    with pytest.raises(RuntimeError, match="boom"):
        _mk(cfg)
    assert id(proc) not in _OWNERS                 # this token's claims rolled back


def test_serialized_processors_never_conflict():
    tgt = f"{RProcA.__module__}.{RProcA.__qualname__}"
    cfg = HarnessConfig(processors=[{"_target_": tgt}])
    h1, h2 = _mk(cfg), _mk(cfg)                    # fresh instances each build
    asyncio.run(h1.cleanup())
    asyncio.run(h2.cleanup())


def test_spawn_style_clone_avoids_conflict():
    from harnessx.tools.spawn_subagent import _patch_processors_for_child

    proc = RProcA()
    cfg = HarnessConfig(processors=[RuntimeReg(proc=proc, hook="task_start")])
    h1 = _mk(cfg)
    try:
        child_cfg = _patch_processors_for_child(cfg, "", 1, 3)
        child_regs = [r for r in child_cfg._processor_regs
                      if isinstance(r, RuntimeReg)]
        assert len(child_regs) == 1
        assert child_regs[0].proc is not proc      # per-child clone
        assert child_regs[0].hook == "task_start"  # 4-tuple preserved
        h2 = _mk(child_cfg)                        # no owner conflict
        asyncio.run(h2.cleanup())
    finally:
        asyncio.run(h1.cleanup())
