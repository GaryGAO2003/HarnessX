# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""Anti-contamination L1 — URL blocklist matcher, search/fetch/browser gates."""
import pytest

from harnessx.tools.url_blocklist import (
    BLOCKLIST_ENV_VAR,
    filter_results,
    is_blocked,
    load_blocklist,
    url_host,
)


class TestLoadBlocklist:
    def test_missing_env_is_empty(self):
        assert load_blocklist(env={}) == ()

    def test_blank_env_is_empty(self):
        assert load_blocklist(env={BLOCKLIST_ENV_VAR: "   "}) == ()

    def test_parses_entries_and_strips_whitespace(self):
        bl = load_blocklist(env={BLOCKLIST_ENV_VAR: " a.com , b.org/foo "})
        assert bl == (("a.com", ""), ("b.org", "/foo"))

    def test_skips_empty_tokens(self):
        bl = load_blocklist(env={BLOCKLIST_ENV_VAR: "a.com,,, ,b.org"})
        assert bl == (("a.com", ""), ("b.org", ""))

    def test_tolerates_scheme_and_stray_dots_slashes(self):
        bl = load_blocklist(env={BLOCKLIST_ENV_VAR: "https://.Example.COM./Path/"})
        assert bl == (("example.com", "/path"),)


class TestIsBlocked:
    def test_env_unset_blocks_nothing(self, monkeypatch):
        monkeypatch.delenv(BLOCKLIST_ENV_VAR, raising=False)
        assert is_blocked("https://hal.cs.princeton.edu/gaia/task/42") is False

    def test_empty_blocklist_arg_blocks_nothing(self):
        assert is_blocked("https://hal.cs.princeton.edu/x", ()) is False

    def test_exact_domain_hit(self):
        bl = (("harbor-index.org", ""),)
        assert is_blocked("https://harbor-index.org/leaderboard", bl) is True

    def test_subdomain_hit(self):
        bl = (("neurometric.ai", ""),)
        assert is_blocked("https://leaderboard.neurometric.ai/x", bl) is True

    def test_unrelated_domain_miss(self):
        bl = (("neurometric.ai", ""),)
        assert is_blocked("https://en.wikipedia.org/wiki/GAIA", bl) is False

    def test_suffix_lookalike_not_matched(self):
        # "evilneurometric.ai" must NOT match "neurometric.ai" (no dot boundary).
        bl = (("neurometric.ai", ""),)
        assert is_blocked("https://evilneurometric.ai/x", bl) is False

    def test_path_prefix_present_side_hits(self):
        bl = load_blocklist(
            env={BLOCKLIST_ENV_VAR: "huggingface.co/datasets/gaia-benchmark"}
        )
        assert (
            is_blocked(
                "https://huggingface.co/datasets/gaia-benchmark/tree/main", bl
            )
            is True
        )

    def test_path_prefix_absent_side_misses(self):
        bl = load_blocklist(
            env={BLOCKLIST_ENV_VAR: "huggingface.co/datasets/gaia-benchmark"}
        )
        assert is_blocked("https://huggingface.co/datasets/squad", bl) is False

    def test_case_insensitive_host_and_path(self):
        bl = load_blocklist(
            env={BLOCKLIST_ENV_VAR: "huggingface.co/datasets/gaia-benchmark"}
        )
        assert (
            is_blocked("HTTPS://HuggingFace.CO/DataSets/GAIA-Benchmark/x", bl)
            is True
        )

    def test_scheme_less_url_hit(self):
        bl = (("hal.cs.princeton.edu", ""),)
        assert is_blocked("hal.cs.princeton.edu/gaia", bl) is True

    def test_host_with_port_hit(self):
        bl = (("harbor-index.org", ""),)
        assert is_blocked("http://harbor-index.org:8080/x", bl) is True

    def test_garbage_url_not_blocked(self):
        bl = (("harbor-index.org", ""),)
        assert is_blocked("::not a url::", bl) is False

    def test_reads_env_when_blocklist_omitted(self, monkeypatch):
        monkeypatch.setenv(BLOCKLIST_ENV_VAR, "neurometric.ai")
        assert is_blocked("https://leaderboard.neurometric.ai/t/1") is True


class TestUrlHost:
    def test_strips_scheme_port_and_lowercases(self):
        assert url_host("https://Leaderboard.Neurometric.AI:8080/x") == (
            "leaderboard.neurometric.ai"
        )

    def test_scheme_less(self):
        assert url_host("harbor-index.org/x") == "harbor-index.org"

    def test_empty(self):
        assert url_host("") == ""


class TestFilterResults:
    def test_empty_blocklist_keeps_all(self):
        results = [{"url": "https://a.com", "title": "A", "snippet": ""}]
        kept, dropped = filter_results(results, blocklist=())
        assert kept == results
        assert dropped == 0

    def test_drops_blocked_entries_only(self):
        bl = (("neurometric.ai", ""),)
        results = [
            {
                "url": "https://leaderboard.neurometric.ai/t/1",
                "title": "leak",
                "snippet": "Expected answer: 42",
            },
            {"url": "https://en.wikipedia.org/wiki/X", "title": "ok", "snippet": ""},
        ]
        kept, dropped = filter_results(results, blocklist=bl)
        assert dropped == 1
        assert [r["url"] for r in kept] == ["https://en.wikipedia.org/wiki/X"]


class TestFormatResultsConvergencePoint:
    def test_format_results_drops_blocked(self, monkeypatch):
        from harnessx.tools.builtin import web_search

        monkeypatch.setenv(BLOCKLIST_ENV_VAR, "harbor-index.org")
        out = web_search._format_results(
            [
                {"title": "leak", "url": "https://harbor-index.org/q/5", "snippet": "a"},
                {"title": "ok", "url": "https://example.com/a", "snippet": "s"},
            ]
        )
        assert "harbor-index.org" not in out
        assert "example.com" in out


class TestSerperFiltering:
    @pytest.mark.asyncio
    async def test_serper_results_filtered(self, monkeypatch):
        from harnessx.tools.contrib import serper_search

        monkeypatch.setenv("SERPER_API_KEY", "test-key")
        monkeypatch.setenv(BLOCKLIST_ENV_VAR, "neurometric.ai")

        async def fake_search(query, max_results, api_key):
            return [
                {
                    "title": "leak",
                    "url": "https://leaderboard.neurometric.ai/t/1",
                    "snippet": "Expected answer: 42",
                },
                {
                    "title": "wiki",
                    "url": "https://en.wikipedia.org/wiki/GAIA",
                    "snippet": "ok",
                },
            ]

        monkeypatch.setattr(serper_search, "_search_serper", fake_search)
        out = await serper_search._serper_web_search("gaia task 1", max_results=5)
        assert "neurometric.ai" not in out
        assert "en.wikipedia.org" in out


class TestWebFetchBlocking:
    @pytest.mark.asyncio
    async def test_blocked_url_refused_without_request(self, monkeypatch):
        from harnessx.tools.builtin import web_fetch

        monkeypatch.setenv(BLOCKLIST_ENV_VAR, "hal.cs.princeton.edu")

        async def _boom(*a, **k):
            raise AssertionError("network must not be touched for a blocked URL")

        monkeypatch.setattr(web_fetch, "_fetch_static", _boom)
        out = await web_fetch.web_fetch_tool.fn(
            url="https://hal.cs.princeton.edu/gaia/42"
        )
        assert out.startswith("[blocked] URL blocked by policy:")
        assert "hal.cs.princeton.edu" in out

    @pytest.mark.asyncio
    async def test_unset_env_allows_fetch(self, monkeypatch):
        from harnessx.tools.builtin import web_fetch

        monkeypatch.delenv(BLOCKLIST_ENV_VAR, raising=False)

        async def _ok(url, blocklist=None):
            return "hello world " * 30  # >= 200 chars: no browser retry

        monkeypatch.setattr(web_fetch, "_fetch_static", _ok)
        out = await web_fetch.web_fetch_tool.fn(url="https://example.com/page")
        assert "hello world" in out


class TestBrowserBlocking:
    @pytest.mark.asyncio
    async def test_navigate_blocked_before_launch(self, monkeypatch):
        from harnessx.tools.builtin import browser

        monkeypatch.setenv(BLOCKLIST_ENV_VAR, "harbor-index.org")

        async def _boom(*a, **k):
            raise AssertionError("browser must not launch for a blocked URL")

        monkeypatch.setattr(browser, "_get_page", _boom)
        out = await browser.browser_tool.fn(
            action="navigate", url="https://harbor-index.org/x"
        )
        assert out.startswith("[blocked] URL blocked by policy:")
        assert "harbor-index.org" in out


class TestInstallBlocklistEnv:
    def test_sets_default_when_unset(self, monkeypatch):
        from experiments.variant_pool.anti_contamination import (
            GAIA_ANSWER_DOMAINS,
            install_blocklist_env,
        )

        monkeypatch.delenv(BLOCKLIST_ENV_VAR, raising=False)
        val = install_blocklist_env()
        assert val == ",".join(GAIA_ANSWER_DOMAINS)
        # It is now live for the matcher (subdomain of neurometric.ai).
        assert is_blocked("https://leaderboard.neurometric.ai/t/1") is True
        # Third-party GAIA mirrors on the HF dataset tree are covered (the
        # ghx_6x3_v3 leak channel), as is the dataset-viewer rows API…
        assert is_blocked("https://huggingface.co/datasets/m-ric/GAIA_annotated/viewer") is True
        assert is_blocked("https://datasets-server.huggingface.co/rows?dataset=m-ric%2Fagents") is True
        # …while the rest of the Hub stays reachable.
        assert is_blocked("https://huggingface.co/models") is False
        assert is_blocked("https://huggingface.co/m-ric") is False

    def test_preserves_explicit_override(self, monkeypatch):
        from experiments.variant_pool.anti_contamination import install_blocklist_env

        monkeypatch.setenv(BLOCKLIST_ENV_VAR, "my.own.domain")
        assert install_blocklist_env() == "my.own.domain"
