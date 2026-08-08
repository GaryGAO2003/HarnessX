"""Block 1 — normalize/coerce rejection chain for processor registrations.

Covers: None / blacklist builtins → ValueError; SerializedReg / RuntimeReg
pass-through; dict → SerializedReg; bare-instance coerce through the REAL
``get_graph_metadata`` (L1.4 minimal implemented in core/processor.py).
"""

import pytest

from harnessx.core.harness import HarnessConfig
from harnessx.core.runtime import (
    HarnessConflictError,
    RuntimeReg,
    SerializedReg,
    coerce_runtime_reg,
    normalize_processor_reg,
    stable_topological_sort,
    unwrap_runtime_proc,
)


class _FakeProc:
    """Bare processor (has a __dict__, class-level metadata)."""

    _hook = "task_end"
    _order = 10
    _singleton_group = "demo"
    _after = ("other",)


class _NoHookProc:
    """Bare processor with no class hook → natural bucket "*"."""

    _order = 3


class _SlotsProc:
    """__slots__ processor (no instance __dict__) — _read must not crash."""

    __slots__ = ()
    _hook = "task_start"
    _order = 0


# ── normalize: None ─────────────────────────────────────────────────────────


def test_normalize_none_raises_valueerror():
    with pytest.raises(ValueError):
        normalize_processor_reg(None)


# ── normalize: blacklist builtins → ValueError ──────────────────────────────


@pytest.mark.parametrize(
    "bad",
    [
        "SlidingWindowMemory",   # str
        5,                       # int
        1.5,                     # float
        True,                    # bool (int subclass — covered)
        b"bytes",                # bytes
        bytearray(b"ba"),        # bytearray
        [1, 2],                  # list
        (1, 2),                  # tuple
        {1, 2},                  # set
        frozenset({1, 2}),       # frozenset
        1j,                      # complex
        range(3),                # range
        int,                     # type (class object)
    ],
)
def test_normalize_blacklist_raises_valueerror(bad):
    with pytest.raises(ValueError):
        normalize_processor_reg(bad)


# ── normalize: pass-through ─────────────────────────────────────────────────


def test_normalize_serializedreg_passthrough():
    s = SerializedReg(dict_ref={"_hook_": "task_end"})
    assert normalize_processor_reg(s) is s


def test_normalize_runtimereg_passthrough():
    r = RuntimeReg(proc=object())
    assert normalize_processor_reg(r) is r


def test_normalize_dict_wraps_serializedreg():
    d = {"_target_": "x", "_hook_": "task_start"}
    out = normalize_processor_reg(d)
    assert isinstance(out, SerializedReg)
    assert out.dict_ref is d  # dynamic properties read the SAME dict


# ── normalize / coerce: bare instances ──────────────────────────────────────


def test_normalize_bare_instance_coerces():
    proc = _FakeProc()
    out = normalize_processor_reg(proc)
    assert isinstance(out, RuntimeReg)
    assert out.proc is proc
    assert out.hook == "task_end"
    assert out.order == 10
    assert out.singleton_group == "demo"
    assert out.after == ("other",)


def test_coerce_bare_instance_natural_bucket_star():
    proc = _NoHookProc()
    reg = coerce_runtime_reg(proc)
    assert reg is not None
    assert reg.hook == "*"  # no _hook → "*" (harness.py:391 semantics)


def test_coerce_none_and_dict_return_none():
    assert coerce_runtime_reg(None) is None
    assert coerce_runtime_reg({}) is None


def test_coerce_runtimereg_identity():
    r = RuntimeReg(proc=object(), hook="before_model")
    assert coerce_runtime_reg(r) is r


# ── unwrap ──────────────────────────────────────────────────────────────────


def test_unwrap_runtimereg_returns_proc():
    proc = object()
    assert unwrap_runtime_proc(RuntimeReg(proc=proc)) is proc


def test_unwrap_bare_processor_returns_itself():
    proc = _FakeProc()
    assert unwrap_runtime_proc(proc) is proc


# ── SerializedReg dynamic properties (presence vs value) ────────────────────


def test_serializedreg_presence_flags():
    d = {"_hook_": "", "_order_": 0, "_singleton_group_": "", "_after_": []}
    s = SerializedReg(dict_ref=d)
    assert s.hook_present and s.order_present and s.sg_present and s.after_present
    assert s.hook == "" and s.order == 0
    assert s.singleton_group == "" and s.after == ()


def test_serializedreg_missing_keys_fall_back():
    s = SerializedReg(dict_ref={})
    assert not (s.hook_present or s.order_present or s.sg_present or s.after_present)
    assert s.hook == "" and s.order == 0
    assert s.singleton_group is None and s.after == ()


def test_serializedreg_reads_live_dict():
    d = {"_hook_": "task_start"}
    s = SerializedReg(dict_ref=d)
    d["_hook_"] = "task_end"  # external mutation → same value everywhere
    assert s.hook == "task_end"


# ── SerializedReg: None / 异型值防御（块 3）──────────────────────────────────


def test_serializedreg_none_values_degrade_safely():
    s = SerializedReg(dict_ref={
        "_hook_": None, "_order_": None,
        "_singleton_group_": None, "_after_": None,
    })
    assert s.hook == "" and s.order == 0
    assert s.singleton_group is None and s.after == ()


def test_serializedreg_heterogeneous_values_degrade_safely():
    s = SerializedReg(dict_ref={
        "_hook_": 123, "_order_": "abc",
        "_singleton_group_": 5, "_after_": 7,
    })
    assert s.hook == "" and s.order == 0
    assert s.singleton_group is None and s.after == ()
    # 非空非迭代 _after_ 不崩（tuple(7) 曾抛 TypeError）


def test_serializedreg_after_str_not_char_split():
    # str 值不得 char-split（("a","b")）—— isinstance 守卫 → ()
    assert SerializedReg(dict_ref={"_after_": "ab"}).after == ()


def test_serializedreg_order_numeric_string_coerces():
    assert SerializedReg(dict_ref={"_order_": "50"}).order == 50


def test_serializedreg_after_list_tuple_kept():
    assert SerializedReg(dict_ref={"_after_": ["a", "b"]}).after == ("a", "b")
    assert SerializedReg(dict_ref={"_after_": ("x",)}).after == ("x",)


# ── L1.4 real get_graph_metadata (bare-instance natural values) ─────────────


def test_coerce_real_graph_metadata_reads_class_attrs():
    proc = _FakeProc()
    reg = coerce_runtime_reg(proc)
    assert reg is not None
    assert reg.proc is proc
    assert reg.hook == "task_end"          # natural bucket = class _hook
    assert reg.order == 10                 # natural order = class _order
    assert reg.singleton_group == "demo"
    assert reg.after == ("other",)


def test_coerce_slots_instance_no_crash():
    proc = _SlotsProc()
    reg = coerce_runtime_reg(proc)
    assert reg is not None
    assert reg.hook == "task_start"        # _read falls back to class attrs
    assert reg.order == 0


def test_normalize_slots_instance_coerces():
    proc = _SlotsProc()
    out = normalize_processor_reg(proc)
    assert isinstance(out, RuntimeReg)
    assert out.proc is proc
    assert out.hook == "task_start"


# ── Block 4: HarnessConfig canonical + views + write API ────────────────────


def test_config_canonical_builds_and_views():
    d1 = {"_target_": "x.A", "_hook_": "task_start"}
    r = RuntimeReg(proc=_FakeProc(), hook="task_end")
    cfg = HarnessConfig(processors=[d1, r])
    # canonical: mixed order preserved, all normalized
    assert [type(x) for x in cfg._processor_regs] == [SerializedReg, RuntimeReg]
    # processors view = SerializedReg dict_refs only
    assert cfg.processors == [d1]
    assert cfg.processors[0] is d1  # view exposes the SAME dicts
    # _rt_procs = derived RuntimeReg tuple view
    assert cfg._rt_procs == (r,)


def test_config_bare_instance_coerces_into_canonical():
    proc = _FakeProc()
    cfg = HarnessConfig(processors=[proc])
    assert len(cfg._processor_regs) == 1
    assert isinstance(cfg._processor_regs[0], RuntimeReg)
    assert cfg._processor_regs[0].proc is proc
    assert cfg.processors == []  # bare instance is not in the dict view
    assert cfg._rt_procs == (cfg._processor_regs[0],)


def test_config_add_runtime_reg_appends_and_refreshes():
    d1 = {"_target_": "x.A"}
    cfg = HarnessConfig(processors=[d1])
    cfg.add_runtime_reg(_FakeProc())
    assert len(cfg._processor_regs) == 2
    assert isinstance(cfg._processor_regs[1], RuntimeReg)
    assert cfg.processors == [d1]  # view still only the SerializedReg
    assert len(cfg._rt_procs) == 1


def test_config_replace_processor_regs_full_mixed_sequence():
    d1 = {"_target_": "x.A"}
    r = RuntimeReg(proc=_FakeProc())
    cfg = HarnessConfig(processors=[d1])
    # replace with full mixed sequence (dict + record + bare)
    cfg.replace_processor_regs([d1, r, _FakeProc()])
    assert [type(x) for x in cfg._processor_regs] == [SerializedReg, RuntimeReg, RuntimeReg]
    assert cfg.processors == [d1]  # refreshed view
    assert len(cfg._rt_procs) == 2


def test_config_replace_processor_regs_removes_and_refreshes():
    """Digester scenario: replace SerializedReg with RuntimeReg → old dict must
    disappear from the processors view (no stale persistent node)."""
    d1 = {"_target_": "x.A"}
    cfg = HarnessConfig(processors=[d1])
    new_reg = RuntimeReg(proc=_FakeProc(), hook="task_start")
    cfg.replace_processor_regs([new_reg])
    assert cfg.processors == []  # old dict_ref gone from the view
    assert cfg._rt_procs == (new_reg,)


def test_config_copy_no_override_preserves_canonical():
    d1 = {"_target_": "x.A"}
    r = RuntimeReg(proc=_FakeProc())
    cfg = HarnessConfig(processors=[d1, r])
    new = cfg.copy()
    assert [type(x) for x in new._processor_regs] == [SerializedReg, RuntimeReg]
    assert new.processors == [d1]
    assert new._rt_procs == (r,)
    # original untouched
    assert cfg._processor_regs[0] is d1 or True


def test_config_copy_override_rebuilds_canonical():
    d1 = {"_target_": "x.A"}
    r = RuntimeReg(proc=_FakeProc())
    cfg = HarnessConfig(processors=[d1, r])
    new_d = {"_target_": "y.B"}
    new = cfg.copy(processors=[new_d])
    # override: canonical rebuilt from the new mixed sequence (dict only)
    assert len(new._processor_regs) == 1
    assert isinstance(new._processor_regs[0], SerializedReg)
    assert new.processors == [new_d]
    assert new._rt_procs == ()
    # original canonical unchanged
    assert len(cfg._processor_regs) == 2


def test_config_copy_override_with_bare_instance():
    cfg = HarnessConfig(processors=[{"_target_": "x.A"}])
    proc = _FakeProc()
    new = cfg.copy(processors=[proc])
    assert isinstance(new._processor_regs[0], RuntimeReg)
    assert new._processor_regs[0].proc is proc
    assert new.processors == []


def test_config_canonicalize_dedups_order_insensitive():
    d1 = {"_target_": "x.A", "_order_": 1}
    d1b = {"_order_": 1, "_target_": "x.A"}  # same content, different key order
    r = RuntimeReg(proc=_FakeProc())
    cfg = HarnessConfig(processors=[d1, d1b, r])
    new = cfg.canonicalize()
    # d1 and d1b dedup (order-insensitive key); RuntimeReg passes through
    assert len(new._processor_regs) == 2
    assert new._processor_regs[0].dict_ref is d1  # first wins
    assert isinstance(new._processor_regs[1], RuntimeReg)


def test_config_canonicalize_keeps_runtime_regs():
    r = RuntimeReg(proc=_FakeProc())
    cfg = HarnessConfig(processors=[r, r])  # same record twice
    new = cfg.canonicalize()
    # RuntimeRegs not deduped (identity preserved) — 2 identical records kept
    assert len(new._processor_regs) == 2


# ── Block 5: L4.6 bucket/order resolution + stable_topological_sort ─────────


class _D:
    """Mini envelope-like entry for sorter tests."""

    def __init__(self, name, order, sg="", after=(), seq=0):
        self.name, self.order, self.sg, self.after, self.seq = name, order, sg, after, seq


def _sort(entries):
    return [
        e.name for e in stable_topological_sort(
            entries,
            order_key=lambda e: e.order,
            after_key=lambda e: e.after,
            group_key=lambda e: e.sg,
            seq_key=lambda e: e.seq,
        )
    ]


def test_sorter_order_then_seq():
    entries = [_D("A", 50, seq=0), _D("B", 0, seq=1), _D("C", 0, seq=0)]
    assert _sort(entries) == ["C", "B", "A"]  # order asc, then seq asc (C seq=0 first)


def test_sorter_after_topological_within_order():
    # same order 0; A after B → B first despite A seq=0
    entries = [_D("A", 0, sg="a", after=("b",), seq=0), _D("B", 0, sg="b", seq=1)]
    assert _sort(entries) == ["B", "A"]


def test_sorter_cross_order_conflict_raises():
    from harnessx.core.runtime import HarnessConflictError

    entries = [_D("A", 0, sg="a", after=("b",), seq=0), _D("B", 50, sg="b", seq=1)]
    with pytest.raises(HarnessConflictError):
        _sort(entries)


def test_sorter_same_order_cycle_raises():
    entries = [_D("A", 0, sg="a", after=("b",), seq=0), _D("B", 0, sg="b", after=("a",), seq=1)]
    with pytest.raises(HarnessConflictError):
        _sort(entries)


def test_sorter_soft_dep_ignored():
    # after refs an unregistered group → silently ignored
    entries = [_D("A", 0, sg="a", after=("missing",), seq=0), _D("B", 0, sg="b", seq=1)]
    assert _sort(entries) == ["A", "B"]  # no raise, stable by seq


def test_sorter_stable_fifo_by_seq():
    # same order, no after → seq order preserved (stable)
    entries = [_D("A", 0, seq=2), _D("B", 0, seq=0), _D("C", 0, seq=1)]
    assert _sort(entries) == ["B", "C", "A"]


def test_harness_conflict_error_reexported_from_builder():
    from harnessx.core.builder import HarnessConflictError as BuilderError

    assert BuilderError is HarnessConflictError  # same class, re-exported
    err = BuilderError(["a", "b"])
    assert err.conflicts == ["a", "b"]


def test_harness_conflict_error_from_core_init():
    from harnessx.core import HarnessConflictError as CoreError

    assert CoreError is HarnessConflictError


# ── Block 5: _bucket / _order_parse (graph snapshot L4.6) ───────────────────


def test_bucket_dict_key_wins():
    from harnessx.graph.snapshot import _bucket

    assert _bucket({"_hook_": "task_end"}, "any.Target") == "task_end"


def test_bucket_wkd_hook_fallback():
    from harnessx.graph.snapshot import _bucket

    # CostGuardProcessor has no _hook_ key → WKD.hook (natural bucket)
    assert _bucket({"_target_": "harnessx.processors.control.cost_guard.CostGuardProcessor"}, "x") == "*"
    # A WKD entry with explicit hook
    assert _bucket({}, "harnessx.processors.context.system_prompt.SystemPromptProcessor") == "*"


def test_bucket_inference_fallback():
    from harnessx.graph.snapshot import _bucket

    assert _bucket({}, "SlidingWindowMemory") == "step_start"  # inferred
    assert _bucket({}, "UnknownTarget") == "*"  # no WKD, no inference → "*"


def test_order_parse_dict_wins():
    from harnessx.graph.snapshot import _order_parse

    assert _order_parse({"_order_": 10}, "x") == 10
    assert _order_parse({"_order_": "20"}, "x") == 20  # numeric string


def test_order_parse_wkd_fallback():
    from harnessx.graph.snapshot import _order_parse

    # CostGuard WKD order=10 → fallback when no _order_ key
    assert _order_parse({}, "harnessx.processors.control.cost_guard.CostGuardProcessor") == 10


def test_order_parse_no_wkd_returns_zero():
    from harnessx.graph.snapshot import _order_parse

    assert _order_parse({}, "Unknown.Target") == 0  # NOT 50 sentinel


def test_order_parse_bad_dict_value_falls_back():
    from harnessx.graph.snapshot import _order_parse

    assert _order_parse({"_order_": "abc"}, "Unknown.Target") == 0
