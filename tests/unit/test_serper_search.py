# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""Silent-fallback guard for the Serper ``WebSearch`` backend.

When ``SERPER_API_KEY`` is unset, ``_serper_web_search`` still falls through to
the built-in scrape chain — previously with no trace at all. These tests pin the
once-per-process WARNING that now marks that path, and its absence when a key is
present. No network: the built-in fallback ``fn`` and Serper call are stubbed.
"""
import logging

import pytest

from harnessx.tools.contrib import serper_search

_WARN_FRAGMENT = "SERPER_API_KEY not set"


async def _stub_builtin(query, max_results=5):
    # Stands in for the built-in WebSearch fallback fn so the missing-key path
    # never touches the network.
    return "STUB-BUILTIN-RESULT"


@pytest.fixture
def _stub_fallback(monkeypatch):
    monkeypatch.setattr(serper_search._builtin_web_search, "fn", _stub_builtin)
    # Reset the once-per-process flag so the warning count is independent of the
    # order tests happen to run in.
    monkeypatch.setattr(serper_search, "_warned_no_key", False)


async def test_missing_key_warns_exactly_once_across_two_searches(
    monkeypatch, caplog, _stub_fallback
):
    monkeypatch.delenv("SERPER_API_KEY", raising=False)
    with caplog.at_level(
        logging.WARNING, logger="harnessx.tools.contrib.serper_search"
    ):
        r1 = await serper_search._serper_web_search("q1")
        r2 = await serper_search._serper_web_search("q2")

    assert r1 == "STUB-BUILTIN-RESULT"
    assert r2 == "STUB-BUILTIN-RESULT"
    hits = [r for r in caplog.records if _WARN_FRAGMENT in r.getMessage()]
    assert len(hits) == 1, f"expected one missing-key warning, got {len(hits)}"
    assert hits[0].levelno == logging.WARNING


async def test_key_present_does_not_warn(monkeypatch, caplog):
    monkeypatch.setenv("SERPER_API_KEY", "test-key")
    monkeypatch.setattr(serper_search, "_warned_no_key", False)

    async def _fake_serper(query, max_results, api_key):
        # Serper returns a hit -> _serper_web_search formats it and never reaches
        # the missing-key branch (also keeps the test off the network).
        return [{"title": "t", "url": "u", "snippet": "s"}]

    monkeypatch.setattr(serper_search, "_search_serper", _fake_serper)
    with caplog.at_level(
        logging.WARNING, logger="harnessx.tools.contrib.serper_search"
    ):
        out = await serper_search._serper_web_search("q")

    assert _WARN_FRAGMENT not in out
    assert [r for r in caplog.records if _WARN_FRAGMENT in r.getMessage()] == []
