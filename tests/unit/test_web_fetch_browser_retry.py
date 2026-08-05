# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""Tests for ``_is_short_enough_to_retry_via_browser`` in web_fetch.

The static path returns non-empty error/terminal markers on failure. A short
binary or error response is a terminal answer and must NOT fall through to
Playwright: retrying a PDF URL through ``page.inner_text("body")`` was the
observed cause of indefinite worker hangs (the GAIA runs of 2026-05-13). Only a
genuine short JS-rendered shell (below ``_JS_THRESHOLD``, no terminal marker)
should be retried through the browser.
"""

from __future__ import annotations

from harnessx.tools.builtin.web_fetch import (
    _JS_THRESHOLD,
    _is_short_enough_to_retry_via_browser,
)


def test_binary_marker_is_not_retried_via_browser() -> None:
    # The headline case: a short binary response (e.g. a PDF) is terminal and
    # must not fall through to Playwright.
    assert _is_short_enough_to_retry_via_browser("[binary content: application/pdf]") is False


def test_static_error_markers_are_not_retried() -> None:
    for terminal in (
        "[fetch failed: HTTP 404 for https://example.com]",
        "[fetch failed after retries: TimeoutException]",
        "[error: html2text not installed]",
    ):
        assert _is_short_enough_to_retry_via_browser(terminal) is False


def test_long_static_content_is_not_retried() -> None:
    # >= _JS_THRESHOLD chars already looks like a real page.
    assert _is_short_enough_to_retry_via_browser("x" * _JS_THRESHOLD) is False


def test_short_js_shell_is_retried_via_browser() -> None:
    # A short shell with no terminal marker is exactly the case the browser
    # fallback exists for.
    assert _is_short_enough_to_retry_via_browser('<div id="root"></div>') is True
