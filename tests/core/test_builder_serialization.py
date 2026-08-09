"""Block 14 — L2.1/L2.2 builder serialization (VM5 / VM6).

Builder writes the full 9-key metadata set on every serialized dict; the
_ProcEntry resolved values overwrite unconditionally; ``_hook_`` is always
the registration bucket; ``_hooks_`` follows the three-state rule.
"""

from harnessx.core.builder import HarnessBuilder
from harnessx.core.processor import MultiHookProcessor, PROCESSOR_HOOK_NAMES

_META_KEYS = (
    "_hook_", "_hooks_", "_order_", "_singleton_group_", "_after_",
    "_writes_slots_", "_reads_slots_",
    "_reads_event_fields_", "_writes_event_fields_",
)


class SerMHP(MultiHookProcessor):
    _hook = "step_start"

    async def on_step_start(self, event):
        yield event


class SerMHPStar(MultiHookProcessor):
    async def on_task_start(self, event):
        yield event

    async def on_task_end(self, event):
        yield event


class SerPlain:
    """Non-MHP processor — serializable plain class."""

    async def __call__(self, event):
        yield event


def _only_dict(config):
    dicts = [p for p in config.processors if isinstance(p, dict)]
    assert len(dicts) == 1
    return dicts[0]


# ── VM6: all metadata keys always serialized ────────────────────────────────


def test_all_nine_keys_present_even_when_empty():
    d = _only_dict(HarnessBuilder().add(SerMHPStar()).build())
    for key in _META_KEYS:
        assert key in d, f"missing {key}"
    assert d["_writes_slots_"] == []
    assert d["_reads_slots_"] == []
    assert d["_reads_event_fields_"] == []
    assert d["_writes_event_fields_"] == []
    assert d["_after_"] == []


def test_context_bundle_dicts_all_have_keys():
    from harnessx.bundles import context

    config = (HarnessBuilder() | context).build()
    dicts = [p for p in config.processors if isinstance(p, dict)]
    assert dicts
    for d in dicts:
        for key in _META_KEYS:
            assert key in d, f"{d.get('_target_')}: missing {key}"


# ── VM5: _hook_ = entry.hook (unconditional), _hooks_ three-state ───────────


def test_explicit_hook_override_shrinks_coverage():
    d = _only_dict(HarnessBuilder().add(SerMHP(), hook="task_end").build())
    assert d["_hook_"] == "task_end"       # registration bucket, not class hook
    assert d["_hooks_"] == ["task_end"]    # concrete hook shrinks coverage


def test_natural_hook_written_unconditionally():
    d = _only_dict(HarnessBuilder().add(SerMHP()).build())
    assert d["_hook_"] == "step_start"     # == class _hook, still written
    assert d["_hooks_"] == ["step_start"]


def test_star_mhp_keeps_dispatch_coverage():
    d = _only_dict(HarnessBuilder().add(SerMHPStar()).build())
    assert d["_hook_"] == "*"
    assert d["_hooks_"] == ["task_start", "task_end"]  # dispatch, lifecycle order


def test_star_non_mhp_gets_all_eight():
    d = _only_dict(HarnessBuilder().add(SerPlain(), hook="*").build())
    assert d["_hook_"] == "*"
    assert d["_hooks_"] == list(PROCESSOR_HOOK_NAMES)  # runloop _star_procs parity


def test_entry_order_and_group_are_ground_truth():
    d = _only_dict(
        HarnessBuilder().add(SerMHPStar(), order=77, singleton_group="sg1").build()
    )
    assert d["_order_"] == 77
    assert d["_singleton_group_"] == "sg1"


def test_explicit_none_singleton_group_serializes_empty():
    d = _only_dict(
        HarnessBuilder().add(SerMHPStar(), singleton_group=None).build()
    )
    assert d["_singleton_group_"] == ""


# ── builder dicts round through the graph without WKD drift noise ───────────


def test_builder_output_produces_no_wkd_drift_warnings():
    from unittest.mock import patch

    from harnessx.bundles import context
    from harnessx.graph.snapshot import to_graph

    config = (HarnessBuilder() | context).build()
    with patch("harnessx.graph.snapshot._log.warning") as mock_warn:
        to_graph(config)
    drift_calls = [c for c in mock_warn.call_args_list
                   if "WKD drift" in str(c)]
    assert drift_calls == []  # dict values == class truth == audited WKD
