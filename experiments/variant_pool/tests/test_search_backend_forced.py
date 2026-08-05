"""Forced-serper contract tests for the ``--search-backend`` seam (W1).

Born from a live incident (2026-08-05): the chval30 validation launcher omitted
``--search-backend serper``, so the whole bed ran on the dead free fallback
ladder (Wikipedia 403 / Bing walled / DDG failing) while the paid Serper key
sat untouched — caught by the user noticing the Serper balance never moved.
The lock's ``h0.tool_registry`` told the truth the whole time.

These tests pin the seam so the wiring itself can never silently rot:
``serper`` must actually swap the live registry entry, ``chain`` must be a
byte-identical no-op, the no-WebSearch soft path must at least warn loudly,
and the provenance must record the swap. The launcher side additionally
refuses to ignite without a live Serper probe (runs/, gitignored).
"""
import logging
from types import SimpleNamespace

import pytest

from recipe.gaia_evolver.run_variant_pool import (
    DEFAULT_SEARCH_BACKEND,
    SEARCH_BACKENDS,
    _maybe_use_serper_backend,
    _search_backend_provenance,
)


class _FakeRegistry:
    """Duck-typed twin of the registry surface the seam actually touches.

    ``_maybe_use_serper_backend`` reads ``registry._tools`` (dict) and calls
    ``registry.register(tool, replace=True)`` — nothing else. The swapped-in
    tool object is the REAL serper drop-in (imported inside the seam), so the
    load-bearing half of the contract runs against production code.
    """

    def __init__(self, tools):
        self._tools = dict(tools)

    def register(self, tool, replace=False):
        name = getattr(tool, "name", None)
        if name in self._tools and not replace:
            raise ValueError(f"duplicate tool {name!r}")
        self._tools[name] = tool


def _config_with(tools):
    return SimpleNamespace(tool_registry=_FakeRegistry(tools))


def test_defaults_and_choices_are_stable():
    assert DEFAULT_SEARCH_BACKEND == "chain"
    assert SEARCH_BACKENDS == ("chain", "serper")


def test_chain_is_a_byte_identical_noop():
    stock = SimpleNamespace(name="WebSearch")
    cfg = _config_with({"WebSearch": stock})
    out = _maybe_use_serper_backend(cfg, "chain")
    assert out is cfg
    assert cfg.tool_registry._tools["WebSearch"] is stock


def test_serper_swaps_the_live_websearch_entry():
    stock = SimpleNamespace(name="WebSearch")
    cfg = _config_with({"WebSearch": stock})
    out = _maybe_use_serper_backend(cfg, "serper")
    assert out is cfg
    swapped = cfg.tool_registry._tools["WebSearch"]
    assert swapped is not stock, "serper mode must replace the WebSearch entry"
    assert getattr(swapped, "name", None) == "WebSearch", (
        "drop-in must keep the tool name so the worker and lock name-list are unchanged"
    )
    target = str(getattr(swapped, "__hx_target__", ""))
    assert "serper_search" in target, (
        "swapped tool must carry the serper_search __hx_target__ so V0/config.yaml "
        "round-trips it as a tool_registry.custom import path"
    )


def test_serper_without_websearch_warns_and_leaves_unchanged(caplog):
    cfg = _config_with({"Bash": SimpleNamespace(name="Bash")})
    with caplog.at_level(logging.WARNING):
        _maybe_use_serper_backend(cfg, "serper")
    assert "no 'WebSearch' tool" in caplog.text, (
        "the soft path must at least warn loudly (documented soft spot: it does "
        "not hard-fail; the launcher-side live probe is the hard enforcement)"
    )
    assert set(cfg.tool_registry._tools) == {"Bash"}


def test_invalid_backend_raises():
    with pytest.raises(ValueError):
        _maybe_use_serper_backend(_config_with({}), "google")


def test_provenance_records_the_swap_and_only_the_swap():
    assert _search_backend_provenance("chain") is None
    prov = _search_backend_provenance("serper")
    assert prov and "search_backend=serper" in prov and "serper_search" in prov
