# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""Env knobs on the OpenAI provider transport layer.

Both default to the historical behavior when unset (1 req/s global throttle,
SDK-default 600 s HTTP timeout) so existing runs are byte-identical; formal
ladder runs on the self-hosted gateway set them to lift the wall-clock caps.
"""
from __future__ import annotations

from harnessx.providers.openai_provider import _client_timeout, _min_request_interval


def test_interval_default_unchanged(monkeypatch):
    monkeypatch.delenv("HARNESSX_MIN_REQUEST_INTERVAL", raising=False)
    assert _min_request_interval() == 1.0


def test_interval_env_override(monkeypatch):
    monkeypatch.setenv("HARNESSX_MIN_REQUEST_INTERVAL", "0.25")
    assert _min_request_interval() == 0.25


def test_interval_negative_clamped_to_zero(monkeypatch):
    monkeypatch.setenv("HARNESSX_MIN_REQUEST_INTERVAL", "-3")
    assert _min_request_interval() == 0.0


def test_interval_garbage_falls_back(monkeypatch):
    monkeypatch.setenv("HARNESSX_MIN_REQUEST_INTERVAL", "fast")
    assert _min_request_interval() == 1.0


def test_timeout_default_is_sdk_default(monkeypatch):
    monkeypatch.delenv("HARNESSX_HTTP_TIMEOUT", raising=False)
    assert _client_timeout() is None


def test_timeout_env_override(monkeypatch):
    monkeypatch.setenv("HARNESSX_HTTP_TIMEOUT", "300")
    assert _client_timeout() == 300.0


def test_timeout_garbage_falls_back(monkeypatch):
    monkeypatch.setenv("HARNESSX_HTTP_TIMEOUT", "forever")
    assert _client_timeout() is None
