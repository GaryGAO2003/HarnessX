# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""The replay-smoke hard cap is env-tunable but byte-identical by default.

``MetaAgent.evolve`` historically clamped ``replay_timeout_s`` to 20s no
matter what the caller passed (agent.py: ``min(replay_timeout_s, 20.0)``),
which turns the replay gate into a latency lottery for slower providers
(runs/paper2: a fully-formed candidate died at exactly 20.0s). The cap is
now read from ``HARNESSX_REPLAY_TIMEOUT_CAP_S`` with the historical 20s as
the untouched default.
"""

from __future__ import annotations

import pytest

from harnessx.meta_harness.agent import _replay_timeout_cap_s


def test_default_cap_is_the_historical_20s(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HARNESSX_REPLAY_TIMEOUT_CAP_S", raising=False)
    assert _replay_timeout_cap_s() == 20.0


def test_env_var_raises_the_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HARNESSX_REPLAY_TIMEOUT_CAP_S", "60")
    assert _replay_timeout_cap_s() == 60.0


@pytest.mark.parametrize("bad", ["", "garbage", "0", "-5"])
def test_invalid_or_nonpositive_values_fall_back_to_20s(
    monkeypatch: pytest.MonkeyPatch, bad: str
) -> None:
    monkeypatch.setenv("HARNESSX_REPLAY_TIMEOUT_CAP_S", bad)
    assert _replay_timeout_cap_s() == 20.0
