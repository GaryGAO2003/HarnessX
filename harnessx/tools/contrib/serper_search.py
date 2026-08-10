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

``serper_only_web_search_tool`` is the same drop-in with the fallback removed
entirely: per a project ruling the native chain is unusable and must NEVER serve
a query. It is boundary-identical to the built-in ``WebSearch`` (same
name/description/schema/tags/execution_target) but tries Serper *only*, retrying
up to 3 total attempts with a short backoff on transport errors. A missing
``SERPER_API_KEY`` at call time logs an ERROR and returns an explicit
"unavailable" tool result (the recipe additionally fails fast at launch when the
key is absent); an empty Serper result returns the built-in ``web_search``
empty-result wording verbatim — an honest empty answer, NOT a fallback; an
exhausted retry budget returns a ``"Web search failed (Serper): ..."`` tool
result. It never imports or calls the built-in chain's ``fn`` on any path (the
``_builtin_web_search`` reference is reused only for name/schema parity).

On "subclassing": the built-in ``WebSearch`` is a ``Tool`` *dataclass instance*
produced by the ``@tool`` decorator, not a class, so there is nothing to
subclass. Copying its schema verbatim and delegating its fallback to the
built-in function is the faithful, additive realisation of "a WebSearch tool
that prefers Serper".
"""

from __future__ import annotations

import asyncio
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

#: Same round-trip contract for the fallback-free variant: the recipe swaps this
#: in under ``--search-backend serper_only`` and it must serialise under
#: ``tool_registry.custom`` (its import path), not the built-in ``WebSearch`` name.
_SERPER_ONLY_TOOL_TARGET = (
    "harnessx.tools.contrib.serper_search.serper_only_web_search_tool"
)

#: serper_only retry budget: 3 total Serper attempts (no native fallback exists),
#: with a short backoff BETWEEN attempts (2s then 5s). Any transport/HTTP error
#: is retried; there is no path into the built-in chain on exhaustion.
_SERPER_ONLY_MAX_ATTEMPTS = 3
_SERPER_ONLY_BACKOFF_S = (2, 5)


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


#: Set once the missing-key warning has fired, so the silent fall-through to the
#: built-in scrape chain is flagged exactly ONCE per process instead of on every
#: search. Without this, a run with no ``SERPER_API_KEY`` uses Serper for nothing
#: and leaves no trace at all — the blind spot this guards.
_warned_no_key = False


def _warn_missing_key_once() -> None:
    """Warn (once per process) that ``SERPER_API_KEY`` is unset, so ``WebSearch``
    silently runs on the built-in scrape chain and Serper is never used.

    Behaviour is unchanged — the caller still falls back — this only leaves the
    one log line that the silent path was previously missing.
    """
    global _warned_no_key
    if not _warned_no_key:
        _warned_no_key = True
        logger.warning(
            "SERPER_API_KEY not set: WebSearch runs on the built-in scrape "
            "chain (Serper never used)"
        )


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
    else:
        # No key at all: the fall-through below is otherwise completely silent.
        _warn_missing_key_once()

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


def _builtin_empty_result_message(query: str) -> str:
    """The built-in ``web_search`` message for an empty result set, verbatim.

    ``web_search_tool`` builds this string (its local ``_unavailable_msg``) and
    returns it whenever the merged result set is empty. We reproduce the exact
    wording here so a ``serper_only`` empty answer is indistinguishable from the
    built-in's own empty answer — reusing the *wording*, NOT the fallback chain
    (the built-in ``fn`` is never called on this path). Kept byte-for-byte in sync
    with ``harnessx/tools/builtin/web_search.py``'s ``_unavailable_msg``.
    """
    return (
        f"[SEARCH UNAVAILABLE] All search providers failed for query: {query}\n"
        "Web search is temporarily not accessible. You MUST still provide a concrete "
        "answer based on your training knowledge. Do NOT answer with 'unavailable', "
        "'unknown', or 'unable to determine' — give your best factual answer. "
        "Try WebFetch to access specific URLs directly if you know the relevant page."
    )


async def _serper_only_web_search(query: str, max_results: int = 5) -> str:
    """Serper-only ``WebSearch``. NEVER falls back to the built-in chain.

    Project ruling: the native fallback chain is unusable and must never serve a
    query, so this backend has no path into ``_builtin_web_search.fn``. Behaviour:

    * Missing ``SERPER_API_KEY`` at call time -> log an ERROR and return an
      explicit unavailable tool result. The built-in chain is not touched.
    * Serper transport/HTTP error -> retry up to ``_SERPER_ONLY_MAX_ATTEMPTS``
      total attempts, sleeping ``_SERPER_ONLY_BACKOFF_S`` seconds BETWEEN attempts;
      each failed attempt logs a WARNING that names the backend.
    * Successful call with empty ``organic`` results -> return the built-in
      ``web_search`` empty-result wording verbatim. This is an honest empty
      answer, NOT a fallback (the built-in ``fn`` is never called).
    * All attempts failed -> log a WARNING and return
      ``"Web search failed (Serper): <last error>."``. Never raises.
    """
    query = str(query)  # guard against non-string (mirrors the built-in)
    try:
        max_results = int(max_results)
    except (TypeError, ValueError):
        max_results = 5

    api_key = os.environ.get("SERPER_API_KEY", "")
    if not api_key:
        logger.error(
            "serper_only WebSearch: SERPER_API_KEY is not set; returning an "
            "unavailable tool result (no native fallback)."
        )
        return (
            "Web search unavailable: SERPER_API_KEY is not set "
            "(serper_only backend, no fallback)."
        )

    last_error: Exception | None = None
    for attempt in range(1, _SERPER_ONLY_MAX_ATTEMPTS + 1):
        try:
            results = await _search_serper(query, max_results, api_key)
        except Exception as e:  # noqa: BLE001 - retried; NEVER falls back to built-in
            last_error = e
            logger.warning(
                "Serper (serper_only) attempt %d/%d failed: %s",
                attempt,
                _SERPER_ONLY_MAX_ATTEMPTS,
                e,
            )
            if attempt < _SERPER_ONLY_MAX_ATTEMPTS:
                await asyncio.sleep(_SERPER_ONLY_BACKOFF_S[attempt - 1])
            continue
        # Successful call. Empty organic -> honest empty answer reusing the
        # built-in's own empty-result wording (NOT a fallback into its chain).
        if not results:
            return _builtin_empty_result_message(query)
        return _format_results(results)

    logger.warning(
        "Serper (serper_only) failed after %d attempts, no native fallback: %s",
        _SERPER_ONLY_MAX_ATTEMPTS,
        last_error,
    )
    return f"Web search failed (Serper): {last_error}."


#: A ``Tool`` boundary-identical to the built-in ``WebSearch`` (same name /
#: description / input_schema / tags / execution_target) with a Serper-ONLY
#: ``fn`` that never falls back to the built-in chain.
serper_only_web_search_tool = Tool(
    name=_builtin_web_search.name,
    description=_builtin_web_search.description,
    input_schema=_builtin_web_search.input_schema,
    fn=_serper_only_web_search,
    tags=list(_builtin_web_search.tags),
    execution_target=_builtin_web_search.execution_target,
)
serper_only_web_search_tool.__hx_target__ = _SERPER_ONLY_TOOL_TARGET
