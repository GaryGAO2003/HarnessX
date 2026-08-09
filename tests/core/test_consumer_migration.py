"""Block 16 — L2.3a consumer migration: canonical-preserving rebuilds.

The regression class: any ``copy(processors=...)`` fed from the dict VIEW
silently drops RuntimeReg entries.  All remaining call sites now feed from
``_processor_regs``.
"""

from harnessx.core.harness import HarnessConfig
from harnessx.core.processor import MultiHookProcessor
from harnessx.core.runtime import RuntimeReg, unwrap_runtime_proc


class _RtP(MultiHookProcessor):
    async def on_task_start(self, event):
        yield event


def _mixed_config():
    return HarnessConfig(processors=[
        {"_target_": "x.ProcA", "_hook_": "task_start"},
        RuntimeReg(proc=_RtP(), hook="task_start"),
        {"_target_": "x.ProcB", "_hook_": "task_end"},
    ])


def test_mount_plugin_preserves_runtime_regs():
    from harnessx.cli import _mount_plugin

    class _Plug:
        name = "p"
        processors: list = []

    cfg = _mixed_config()
    out = _mount_plugin(cfg, _Plug())
    kinds = [type(r).__name__ for r in out._processor_regs]
    assert kinds == ["SerializedReg", "RuntimeReg", "SerializedReg"]  # interleave kept
    assert out.plugins and out.plugins[-1].name == "p"


def test_prepend_append_around_canonical_keeps_regs():
    cfg = _mixed_config()
    head, tail = _RtP(), _RtP()
    out = cfg.copy(processors=[head, *cfg._processor_regs, tail])
    regs = out._processor_regs
    assert len(regs) == 5
    assert unwrap_runtime_proc(regs[0]) is head
    assert unwrap_runtime_proc(regs[-1]) is tail
    assert type(regs[2]).__name__ == "RuntimeReg"  # original middle reg intact


def test_add_runtime_reg_dedup_via_unwrap():
    cfg = _mixed_config()
    already = any(isinstance(unwrap_runtime_proc(p), _RtP)
                  for p in cfg._rt_procs)
    assert already  # unwrap sees through the record (tau2 pattern)
    n = len(cfg._processor_regs)
    if not already:
        cfg.add_runtime_reg(_RtP())
    assert len(cfg._processor_regs) == n  # no duplicate appended
