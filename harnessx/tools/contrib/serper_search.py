# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""Serper (serper.dev) web-search backend — a pure, additive drop-in.

This is a **new file** under ``harnessx/`` (the same additive convention as
``harnessx/processors/control/step_countdown.py``): it adds a capability without
editing any existing ``harnessx/`` file.

``serper_web_search_tool`` is a :class:`~harnessx.tools.base.Tool` that is
*indistinguishable from the built-in* ``WebSearch`` at the tool boundary — it
copies the built-in's ``name`` / ``description`` / ``input_schema`` /
``tags`` / ``execution_target`` verbatim, so a worker sees exactly the same tool
schema. At call time it tries Serper first
(``POST https://google.serper.dev/search`` with the ``SERPER_API_KEY`` env var)
and, on a missing key, an empty result set, or ANY exception, falls back to the
built-in ``WebSearch`` fallback chain (SerpAPI -> Tavily -> Wikipedia + Bing ->
DuckDuckGo). The fallback is the built-in tool's own function object, so the
built-in's module-level circuit-breaker state is neither duplicated nor
disturbed by this backend.

The recipe (``recipe.gaia_evolver.run_variant_pool`` under
``--search-backend serper``) swaps this tool in for the built-in ``WebSearch`` in
the deployed H0 registry, so every candidate config authored from H0 inherits it.

Serper (serper.dev) is a *different* provider from SerpAPI (serpapi.com); the
repo has no native Serper support, which is why this backend exists.

On "subclassing": the built-in ``WebSearch`` is a ``Tool`` *dataclass instance*
produced by the ``@tool`` decorator, not a class, so there is nothing to
subclass. Copying its schema verbatim and delegating its fallback to the
built-in function is the faithful, additive realisation of "a WebSearch tool
that prefers Serper".
"""

from __future__ import annotations

import logging
import os

import httpx

from ..base import Tool
from ..builtin.web_search import _format_results
from ..builtin.web_search import web_search_tool as _builtin_web_search

logger = logging.getLogger(__name__)

#: Serper JSON search endpoint (serper.dev). NOT serpapi.com.
_SERPER_ENDPOINT = "https://google.serper.dev/search"
#: Timeout in the same magnitude as the built-in SerpAPI backend
#: (``web_search._SERPAPI_TIMEOUT == 20``).
_SERPER_TIMEOUT = 20

#: The exact import target the recipe / YAML round-trip uses for this tool. Set as
#: ``__hx_target__`` below so a config serialised via
#: ``harnessx.core.harness._runtime_registry_to_config`` records this tool under
#: ``tool_registry.custom`` (its import path) instead of the built-in ``WebSearch``
#: name — otherwise the deployed config would silently revert to the built-in
#: backend on the next YAML load.
_SERPER_TOOL_TARGET = "harnessx.tools.contrib.serper_search.serper_web_search_tool"


async def _search_serper(query: str, max_results: int, api_key: str) -> list[dict]:
    """Search via Serper (serper.dev / Google). Returns ``[{title, url, snippet}]``.

    Maps the Serper ``organic`` array: ``title`` -> title, ``link`` -> url,
    ``snippet`` -> snippet (truncated to 300 chars; missing -> empty string) —
    the same record shape and 300-char snippet cap the built-in backends use.
    """
    headers = {"X-API-KEY": api_key, "Content-Type": "application/json"}
    payload = {"q": query, "num": max_results}
    async with httpx.AsyncClient(timeout=_SERPER_TIMEOUT) as client:
        resp = await client.post(_SERPER_ENDPOINT, headers=headers, json=payload)
        resp.raise_for_status()
        data = resp.json()
    results: list[dict] = []
    for r in data.get("organic", [])[:max_results]:
        results.append(
            {
                "title": r.get("title", ""),
                "url": r.get("link", ""),
                "snippet": (r.get("snippet") or "")[:300],
            }
        )
    return results


async def _serper_web_search(query: str, max_results: int = 5) -> str:
    """Serper-first ``WebSearch``. Falls back to the built-in fallback chain.

    Missing ``SERPER_API_KEY``, an empty Serper result, or any Serper error all
    delegate to the built-in ``WebSearch`` function object, so its module-level
    circuit breaker and provider ladder run unchanged and are never touched here.
    """
    query = str(query)  # guard against non-string (mirrors the built-in)
    try:
        max_results = int(max_results)
    except (TypeError, ValueError):
        max_results = 5

    api_key = os.environ.get("SERPER_API_KEY", "")
    if api_key:
        try:
            results = await _search_serper(query, max_results, api_key)
            if results:
                return _format_results(results)
        except Exception as e:  # noqa: BLE001 - any Serper failure falls back
            logger.warning("Serper search failed: %s (falling back to built-in chain)", e)

    # No key / empty / error -> the built-in WebSearch fallback chain runs with
    # its own circuit breaker + provider ladder, unchanged.
    return await _builtin_web_search.fn(query=query, max_results=max_results)


#: A ``Tool`` indistinguishable from the built-in ``WebSearch`` at the boundary:
#: identical name / description / input_schema / tags / execution_target, with a
#: Serper-first ``fn``.
serper_web_search_tool = Tool(
    name=_builtin_web_search.name,
    description=_builtin_web_search.description,
    input_schema=_builtin_web_search.input_schema,
    fn=_serper_web_search,
    tags=list(_builtin_web_search.tags),
    execution_target=_builtin_web_search.execution_target,
)
serper_web_search_tool.__hx_target__ = _SERPER_TOOL_TARGET
