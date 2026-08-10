# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""Anti-contamination L1 — query blocklist matcher + the three search-tool gates."""
import os

import pytest

from harnessx.tools.query_blocklist import (
    QUERY_BLOCKED_MESSAGE,
    QUERY_BLOCKLIST_ENV_VAR,
    is_query_blocked,
    load_query_rules,
)


class TestLoadQueryRules:
    def test_missing_env_is_empty(self):
        assert load_query_rules(env={}) == ()

    def test_blank_env_is_empty(self):
        assert load_query_rules(env={QUERY_BLOCKLIST_ENV_VAR: "   "}) == ()

    def test_parses_multi_term_rules(self):
        rules = load_query_rules(
            env={QUERY_BLOCKLIST_ENV_VAR: "gaia+benchmark,gaia+answer"}
        )
        assert rules == (("gaia", "benchmark"), ("gaia", "answer"))

    def test_single_term_rule(self):
        assert load_query_rules(env={QUERY_BLOCKLIST_ENV_VAR: "leaderboard"}) == (
            ("leaderboard",),
        )

    def test_whitespace_tolerated_and_lowercased(self):
        rules = load_query_rules(
            env={QUERY_BLOCKLIST_ENV_VAR: "  GAIA + Benchmark , FOO  "}
        )
        assert rules == (("gaia", "benchmark"), ("foo",))

    def test_skips_blank_terms_within_rule(self):
        # Stray / duplicate '+' produces empty terms that are dropped.
        assert load_query_rules(
            env={QUERY_BLOCKLIST_ENV_VAR: "gaia++benchmark+"}
        ) == (("gaia", "benchmark"),)

    def test_skips_whole_blank_rules(self):
        # Stray / duplicate ',' produces empty rules that are dropped.
        assert load_query_rules(
            env={QUERY_BLOCKLIST_ENV_VAR: "gaia+answer,, + ,  ,foo"}
        ) == (("gaia", "answer"), ("foo",))


class TestIsQueryBlocked:
    def test_env_unset_blocks_nothing(self, monkeypatch):
        monkeypatch.delenv(QUERY_BLOCKLIST_ENV_VAR, raising=False)
        assert is_query_blocked("gaia benchmark answer key") is False

    def test_empty_rules_arg_blocks_nothing(self):
        assert is_query_blocked("gaia benchmark answer", ()) is False

    def test_all_terms_present_hits(self):
        rules = (("gaia", "benchmark"),)
        assert is_query_blocked("the gaia benchmark leaderboard", rules) is True

    def test_missing_one_term_misses(self):
        # 'gaia' present, 'benchmark' absent -> co-occurrence requirement fails.
        rules = (("gaia", "benchmark"),)
        assert is_query_blocked("gaia telescope parallax", rules) is False

    def test_single_term_rule_hits(self):
        rules = (("leaderboard",),)
        assert is_query_blocked("GAIA leaderboard page", rules) is True

    def test_case_insensitive(self):
        rules = load_query_rules(env={QUERY_BLOCKLIST_ENV_VAR: "gaia+benchmark"})
        assert is_query_blocked("GAIA Benchmark Results", rules) is True

    def test_substring_semantics(self):
        # Substring (not word-boundary) matching — documents the chosen semantics.
        rules = (("gaia", "answer"),)
        assert is_query_blocked("gaian answers dataset", rules) is True

    def test_any_rule_matches(self):
        rules = (("gaia", "benchmark"), ("gaia", "answer"))
        assert is_query_blocked("gaia answer key", rules) is True

    def test_reads_env_when_rules_omitted(self, monkeypatch):
        monkeypatch.setenv(QUERY_BLOCKLIST_ENV_VAR, "gaia+benchmark")
        assert is_query_blocked("gaia benchmark task 1") is True

    def test_non_string_query_no_raise(self):
        # Non-string inputs must never raise -> treated as not blocked.
        rules = (("gaia",),)
        assert is_query_blocked(42, rules) is False
        assert is_query_blocked(None, rules) is False

    def test_empty_query_not_blocked(self):
        assert is_query_blocked("", (("gaia",),)) is False


class _NetworkTouched(BaseException):
    """Sentinel raised by the stubbed network functions below.

    A ``BaseException`` (deliberately NOT ``Exception``) so that if a query gate
    fails to short-circuit, the stub propagates past the search entry points'
    ``except Exception`` provider-fallback handlers and fails the test loudly,
    instead of being swallowed and masked as an ordinary 'all providers failed'
    result.
    """


async def _boom(*a, **k):
    raise _NetworkTouched("network must not be touched for a blocked query")


class TestWebSearchQueryGate:
    @pytest.mark.asyncio
    async def test_blocked_query_refused_without_network(self, monkeypatch):
        from harnessx.tools.builtin import web_search

        monkeypatch.setenv(QUERY_BLOCKLIST_ENV_VAR, "gaia+answer")
        # Stub every provider in the fallback chain: none may run for a blocked query.
        for name in (
            "_search_serpapi",
            "_search_tavily",
            "_search_wikipedia",
            "_search_bing_scrape",
            "_search_ddgs_api",
            "_search_duckduckgo",
            "_search_duckduckgo_lite",
        ):
            monkeypatch.setattr(web_search, name, _boom)

        out = await web_search.web_search_tool.fn(query="gaia answer key for task 3")
        assert out == QUERY_BLOCKED_MESSAGE

    @pytest.mark.asyncio
    async def test_unblocked_query_reaches_providers(self, monkeypatch):
        from harnessx.tools.builtin import web_search

        monkeypatch.setenv(QUERY_BLOCKLIST_ENV_VAR, "gaia+answer")

        async def _one_result(query, max_results):
            return [{"title": "ok", "url": "https://example.com/a", "snippet": "s"}]

        # A benign query must pass the gate and reach the first provider.
        monkeypatch.setattr(web_search, "_search_serpapi", _one_result)
        out = await web_search.web_search_tool.fn(query="python asyncio tutorial")
        assert out != QUERY_BLOCKED_MESSAGE
        assert "example.com" in out


class TestSerperQueryGate:
    @pytest.mark.asyncio
    async def test_serper_web_search_blocked_without_network(self, monkeypatch):
        from harnessx.tools.contrib import serper_search

        monkeypatch.setenv(QUERY_BLOCKLIST_ENV_VAR, "gaia+answer")
        monkeypatch.setenv("SERPER_API_KEY", "test-key")
        # Neither the Serper call nor the built-in fallback chain may be touched.
        monkeypatch.setattr(serper_search, "_search_serper", _boom)
        monkeypatch.setattr(serper_search._builtin_web_search, "fn", _boom)

        out = await serper_search._serper_web_search("gaia answer key", max_results=5)
        assert out == QUERY_BLOCKED_MESSAGE

    @pytest.mark.asyncio
    async def test_serper_only_blocked_without_network(self, monkeypatch):
        from harnessx.tools.contrib import serper_search

        monkeypatch.setenv(QUERY_BLOCKLIST_ENV_VAR, "gaia+answer")
        monkeypatch.setenv("SERPER_API_KEY", "test-key")
        monkeypatch.setattr(serper_search, "_search_serper", _boom)

        out = await serper_search._serper_only_web_search(
            "gaia answer key", max_results=5
        )
        assert out == QUERY_BLOCKED_MESSAGE


class TestInstallQueryBlocklistEnv:
    def test_sets_query_default_when_unset(self, monkeypatch):
        from experiments.variant_pool.anti_contamination import (
            GAIA_QUERY_RULES,
            install_blocklist_env,
        )
        from harnessx.tools.url_blocklist import BLOCKLIST_ENV_VAR

        monkeypatch.delenv(BLOCKLIST_ENV_VAR, raising=False)
        monkeypatch.delenv(QUERY_BLOCKLIST_ENV_VAR, raising=False)

        install_blocklist_env()

        assert os.environ[QUERY_BLOCKLIST_ENV_VAR] == ",".join(GAIA_QUERY_RULES)
        # Live for the matcher: an answer-hunting query is blocked...
        assert is_query_blocked("where is the gaia benchmark answer") is True
        # ...the documented ESA-telescope false positive (gaia+dataset) is accepted...
        assert is_query_blocked("Gaia DR3 dataset download") is True
        # ...while an unrelated query stays allowed.
        assert is_query_blocked("python asyncio tutorial") is False

    def test_preserves_explicit_query_override(self, monkeypatch):
        from experiments.variant_pool.anti_contamination import (
            GAIA_ANSWER_DOMAINS,
            install_blocklist_env,
        )
        from harnessx.tools.url_blocklist import BLOCKLIST_ENV_VAR

        monkeypatch.delenv(BLOCKLIST_ENV_VAR, raising=False)
        monkeypatch.setenv(QUERY_BLOCKLIST_ENV_VAR, "my+own+rule")

        # Return-value contract unchanged: still the URL blocklist string.
        assert install_blocklist_env() == ",".join(GAIA_ANSWER_DOMAINS)
        # Operator's explicit query rules preserved (setdefault did not overwrite).
        assert os.environ[QUERY_BLOCKLIST_ENV_VAR] == "my+own+rule"
