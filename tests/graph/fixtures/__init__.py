"""P0 contract fixtures — reusable registration/config constructors (v5.3).

Shared by ``tests/graph/test_contract_matrix.py`` (I1-I10) and later P1-P4
tests.  The factories cover the registration-item shapes the v5.3 freeze
pins (see ``docs/graph-hardening-v5.3-P0-freeze.md`` §1-§2):

* pure serialized  — ``_target_`` dicts → ``SerializedReg``;
* pure runtime     — bare processor / ``RuntimeReg`` → ``RuntimeReg``;
* S-R-S-R mixed    — order-sensitive sequence (VM19 / VM20a);
* dict plugin      — unenumerable, contract-narrowed;
* explicit-empty hook (``_hook_=""``) — empty bucket, never executes (VM14);
* missing fields   — presence flags False → natural fallback.

Construction idioms follow ``tests/graph/test_runtime_overlay.py`` and
``tests/graph/test_adapter.py``: plain factory functions returning fresh
objects.  Runtime-only processors are single-owner (L2.3b), so each call
yields a new instance to keep callers isolated.
"""

from __future__ import annotations

from harnessx.core.harness import HarnessConfig
from harnessx.core.processor import MultiHookProcessor
from harnessx.core.runtime import RuntimeReg
from harnessx.plugins.base import HarnessPlugin

# ── probe processors ─────────────────────────────────────────────────────────


class RuntimeProbe(MultiHookProcessor):
    """Runtime-only probe: not serializable, no class hook (natural bucket "*")."""

    async def on_task_start(self, event):
        yield event


class OrderedProbe(MultiHookProcessor):
    """Runtime probe carrying registration-time class metadata (order/hook)."""

    _order = 5

    async def on_before_model(self, event):
        yield event


class SlotProbe(MultiHookProcessor):
    """Runtime probe with a dynamic slot write (→ runtime_nodes, never nodes)."""

    _writes_slot_keys = ("probe.slot",)

    async def on_step_end(self, event):
        yield event


class StubPlugin(HarnessPlugin):
    """Minimal plugin carrying instance processors (id-deduped vs canonical)."""

    name = "stub"
    version = "0.1.0"
    description = "stub"

    def __init__(self, procs):
        super().__init__()
        self.processors = list(procs)


# ── serialized (dict → SerializedReg) building blocks ────────────────────────

# Arbitrary dotted targets: ``to_graph`` reads ``_target_`` as a string and
# never imports it (verified 2026-08-09 — harnessx/graph/snapshot.py has no
# importlib/import_module call), so these route through to_graph safely without
# a real importable class.
ALPHA_TARGET = "tests.graph.fixtures.probes.AlphaProcessor"
BETA_TARGET = "tests.graph.fixtures.probes.BetaProcessor"


def serialized_dict(target, *, hook=None, singleton_group=None, order=None, after=None):
    """Build a serialized ``_target_`` dict with ONLY the present keys.

    Omitting a keyword leaves the corresponding ``_*_`` key absent, so the
    resulting SerializedReg reports ``*_present=False`` (natural fallback).
    Passing ``hook=""`` writes an explicit-empty hook (``hook_present=True``,
    empty bucket that never executes — VM14).
    """
    d = {"_target_": target}
    if hook is not None:
        d["_hook_"] = hook
    if singleton_group is not None:
        d["_singleton_group_"] = singleton_group
    if order is not None:
        d["_order_"] = order
    if after is not None:
        d["_after_"] = list(after)
    return d


# ── config factories ─────────────────────────────────────────────────────────


def config_serialized_only():
    """Pure serialized registration — every entry normalizes to SerializedReg."""
    return HarnessConfig(processors=[
        serialized_dict(ALPHA_TARGET, hook="before_model", singleton_group="alpha", order=10),
        serialized_dict(BETA_TARGET, hook="after_model", singleton_group="beta", order=20),
    ])


def config_runtime_only():
    """Pure runtime registration — bare processor + RuntimeReg (both → RuntimeReg)."""
    return HarnessConfig(processors=[
        RuntimeProbe(),                                          # bare → coerced RuntimeReg
        RuntimeReg(proc=OrderedProbe(), hook="before_model", order=5),
    ])


def config_srsr():
    """Order-sensitive S-R-S-R mixed sequence (VM19 / VM20a).

    Canonical ``_processor_regs`` preserves [S, R, S, R] positional order;
    ``processors`` exposes only the two SerializedRegs, ``_rt_procs`` only the
    two RuntimeRegs.
    """
    return HarnessConfig(processors=[
        serialized_dict(ALPHA_TARGET, hook="before_model", singleton_group="alpha", order=10),
        RuntimeReg(proc=RuntimeProbe(), hook="task_start"),
        serialized_dict(BETA_TARGET, hook="after_model", singleton_group="beta", order=20),
        RuntimeReg(proc=OrderedProbe(), hook="before_model", order=5),
    ])


def config_with_dict_plugin():
    """Config carrying a dict plugin (unenumerable → contract-narrowed + warn)."""
    return HarnessConfig(processors=[], plugins=[{"_target_": "some.Plugin"}])


def config_instance_plugin():
    """Config carrying an instance plugin whose processors overlay at runtime."""
    return HarnessConfig(processors=[], plugins=[StubPlugin([RuntimeProbe()])])


def config_explicit_empty_hook():
    """Serialized entry with explicit ``_hook_=""`` — empty bucket, never runs (VM14)."""
    return HarnessConfig(processors=[
        serialized_dict(ALPHA_TARGET, hook="", singleton_group="alpha"),
    ])


def config_missing_fields():
    """Serialized entry with ONLY ``_target_`` — all presence flags False (natural fallback)."""
    return HarnessConfig(processors=[
        serialized_dict(ALPHA_TARGET),
    ])
