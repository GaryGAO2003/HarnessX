# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""Offline tests for W1 — the Serper (serper.dev) WebSearch backend.

No network: ``httpx.AsyncClient`` is monkeypatched with a scripted fake and the
built-in fallback is monkeypatched to a sentinel, so what is exercised is the
Serper->built-in fallback contract, the title/link/snippet mapping (+300-char
cap), and the recipe-layer registry swap / flag / provenance — all without any
paid API call.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

# The recipe lives under ``recipe/``; put the repo root on the path (conftest only
# adds ``experiments/``).
_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import harnessx.tools.contrib.serper_search as serper  # noqa: E402
from harnessx.tools.builtin.web_search import web_search_tool  # noqa: E402
from harnessx.tools.inmemory import InMemoryToolRegistry  # noqa: E402
from recipe.gaia_evolver import run_variant_pool as rvp  # noqa: E402


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


def _fake_httpx_client(*, organic=None, raise_post=False, raise_status=False):
    """A fake ``httpx.AsyncClient`` class returning a scripted Serper JSON body."""

    class _Resp:
        status_code = 200

        def raise_for_status(self):
            if raise_status:
                raise RuntimeError("serper http 500")

        def json(self):
            return {"organic": list(organic or []), "credits": 1}

    class _Client:
        def __init__(self, *a, **k):
            self.posts: list[dict] = []

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, headers=None, json=None):
            if raise_post:
                raise RuntimeError("serper connection refused")
            _Client.last = {"url": url, "headers": headers, "json": json}
            return _Resp()

    return _Client


def _sentinel_builtin(marker="BUILTIN_CHAIN"):
    """A fake built-in WebSearch Tool whose ``fn`` records its call and returns a marker."""
    calls: list[tuple] = []

    async def _fn(query, max_results=5):
        calls.append((query, max_results))
        return marker

    return SimpleNamespace(fn=_fn), calls


# ===========================================================================
# mapping: Serper organic[] -> [{title, url, snippet}] with a 300-char cap
# ===========================================================================


@pytest.mark.asyncio
async def test_serper_result_mapping_and_snippet_cap(monkeypatch):
    organic = [
        {"title": "First", "link": "https://a.example", "snippet": "hello"},
        {"title": "Second", "link": "https://b.example", "snippet": "x" * 400},
        {"title": "Third", "link": "https://c.example"},  # missing snippet -> ""
    ]
    monkeypatch.setattr(serper.httpx, "AsyncClient", _fake_httpx_client(organic=organic))

    results = await serper._search_serper("who won", 5, "test-key")

    assert results == [
        {"title": "First", "url": "https://a.example", "snippet": "hello"},
        {"title": "Second", "url": "https://b.example", "snippet": "x" * 300},
        {"title": "Third", "url": "https://c.example", "snippet": ""},
    ]
    # Request shape matches the spec: POST body {"q", "num"} + X-API-KEY header.
    sent = serper.httpx.AsyncClient.last  # type: ignore[attr-defined]
    assert sent["json"] == {"q": "who won", "num": 5}
    assert sent["headers"]["X-API-KEY"] == "test-key"
    assert sent["url"] == "https://google.serper.dev/search"


@pytest.mark.asyncio
async def test_serper_num_caps_results(monkeypatch):
    organic = [{"title": f"T{i}", "link": f"https://{i}.example", "snippet": "s"} for i in range(10)]
    monkeypatch.setattr(serper.httpx, "AsyncClient", _fake_httpx_client(organic=organic))
    results = await serper._search_serper("q", 3, "k")
    assert len(results) == 3  # sliced to max_results like the built-in SerpAPI backend


# ===========================================================================
# fallback: no key / empty / error all fall through to the built-in chain
# ===========================================================================


@pytest.mark.asyncio
async def test_no_key_falls_back_to_builtin(monkeypatch):
    monkeypatch.delenv("SERPER_API_KEY", raising=False)
    fake, calls = _sentinel_builtin()
    monkeypatch.setattr(serper, "_builtin_web_search", fake)

    out = await serper._serper_web_search("q", 3)

    assert out == "BUILTIN_CHAIN"
    assert calls == [("q", 3)]  # delegated verbatim, so the circuit breaker keeps working


@pytest.mark.asyncio
async def test_serper_error_falls_back_to_builtin(monkeypatch):
    monkeypatch.setenv("SERPER_API_KEY", "test-key")
    monkeypatch.setattr(serper.httpx, "AsyncClient", _fake_httpx_client(raise_post=True))
    fake, calls = _sentinel_builtin()
    monkeypatch.setattr(serper, "_builtin_web_search", fake)

    out = await serper._serper_web_search("q", 4)

    assert out == "BUILTIN_CHAIN"
    assert calls == [("q", 4)]


@pytest.mark.asyncio
async def test_empty_serper_result_falls_back_to_builtin(monkeypatch):
    monkeypatch.setenv("SERPER_API_KEY", "test-key")
    monkeypatch.setattr(serper.httpx, "AsyncClient", _fake_httpx_client(organic=[]))
    fake, calls = _sentinel_builtin()
    monkeypatch.setattr(serper, "_builtin_web_search", fake)

    out = await serper._serper_web_search("q", 5)

    assert out == "BUILTIN_CHAIN"
    assert calls == [("q", 5)]


@pytest.mark.asyncio
async def test_serper_success_does_not_touch_the_builtin_chain(monkeypatch):
    monkeypatch.setenv("SERPER_API_KEY", "test-key")
    monkeypatch.setattr(
        serper.httpx,
        "AsyncClient",
        _fake_httpx_client(organic=[{"title": "Hit", "link": "https://h.example", "snippet": "snip"}]),
    )

    def _boom(*a, **k):
        raise AssertionError("built-in chain must not be called on Serper success")

    monkeypatch.setattr(serper, "_builtin_web_search", SimpleNamespace(fn=_boom))

    out = await serper._serper_web_search("q", 5)

    assert "Hit" in out and "https://h.example" in out and "snip" in out


# ===========================================================================
# tool identity: indistinguishable from the built-in WebSearch at the boundary
# ===========================================================================


def test_serper_tool_matches_builtin_schema_and_is_round_trippable():
    assert serper.serper_web_search_tool.name == web_search_tool.name == "WebSearch"
    assert serper.serper_web_search_tool.description == web_search_tool.description
    assert serper.serper_web_search_tool.input_schema == web_search_tool.input_schema
    assert serper.serper_web_search_tool.tags == web_search_tool.tags
    assert serper.serper_web_search_tool.execution_target == web_search_tool.execution_target
    # __hx_target__ makes a YAML round-trip serialize it as tool_registry.custom
    # (its import path) instead of reverting to the built-in ``WebSearch`` name.
    assert (
        serper.serper_web_search_tool.__hx_target__
        == "harnessx.tools.contrib.serper_search.serper_web_search_tool"
    )


# ===========================================================================
# recipe wiring: --search-backend swap / default byte-equivalence / provenance
# ===========================================================================


def test_maybe_use_serper_backend_chain_is_a_noop():
    registry = InMemoryToolRegistry()
    registry.register(web_search_tool)
    config = SimpleNamespace(tool_registry=registry)

    out = rvp._maybe_use_serper_backend(config, "chain")

    assert out is config  # same object
    assert registry._tools["WebSearch"] is web_search_tool  # untouched built-in


def test_maybe_use_serper_backend_serper_swaps_websearch_in_place():
    registry = InMemoryToolRegistry()
    registry.register(web_search_tool)
    other = registry.list_names()
    config = SimpleNamespace(tool_registry=registry)

    rvp._maybe_use_serper_backend(config, "serper")

    swapped = registry._tools["WebSearch"]
    assert swapped is serper.serper_web_search_tool
    assert swapped.name == "WebSearch"  # worker sees the same tool name
    assert swapped.__hx_target__ == "harnessx.tools.contrib.serper_search.serper_web_search_tool"
    # no other tool names appeared/disappeared
    assert set(registry.list_names()) == set(other)


def test_maybe_use_serper_backend_without_websearch_is_safe():
    registry = InMemoryToolRegistry()  # empty, no WebSearch
    config = SimpleNamespace(tool_registry=registry)
    out = rvp._maybe_use_serper_backend(config, "serper")
    assert out is config
    assert registry.list_names() == []


def test_search_backend_flag_default_and_choices():
    parser = rvp.build_arg_parser()
    assert parser.parse_args([]).search_backend == "chain"
    assert parser.parse_args(["--search-backend", "serper"]).search_backend == "serper"
    with pytest.raises(SystemExit):
        parser.parse_args(["--search-backend", "bogus"])


def test_search_backend_provenance_is_none_for_default():
    assert rvp._search_backend_provenance("chain") is None
    warn = rvp._search_backend_provenance("serper")
    assert warn is not None and "serper" in warn


def test_invalid_backend_raises():
    with pytest.raises(ValueError):
        rvp._maybe_use_serper_backend(SimpleNamespace(tool_registry=None), "bogus")


def test_serper_swap_round_trips_so_candidates_inherit_it():
    """The swapped tool must serialise as tool_registry.custom (its import path),
    NOT the built-in ``WebSearch`` name — else candidates authored from H0 would
    silently revert to the built-in backend on the next YAML load."""
    from harnessx.core.harness import (
        _build_tool_registry_from_config,
        _runtime_registry_to_config,
    )

    registry = InMemoryToolRegistry()
    registry.register(web_search_tool)
    registry.register(serper.serper_web_search_tool, replace=True)  # the recipe swap

    cfg = _runtime_registry_to_config(registry)
    assert "WebSearch" not in cfg.builtin  # would revert to built-in if it were
    assert "harnessx.tools.contrib.serper_search.serper_web_search_tool" in cfg.custom

    # What a candidate config load does — it must resolve WebSearch -> Serper.
    rebuilt = _build_tool_registry_from_config(cfg)
    assert rebuilt._tools["WebSearch"].fn.__module__ == "harnessx.tools.contrib.serper_search"


def test_default_chain_registry_serialises_websearch_as_builtin():
    """The unswapped (chain) registry keeps WebSearch under builtin -> byte-identical."""
    from harnessx.core.harness import _runtime_registry_to_config

    registry = InMemoryToolRegistry()
    registry.register(web_search_tool)
    cfg = _runtime_registry_to_config(registry)
    assert "WebSearch" in cfg.builtin
    assert not cfg.custom
