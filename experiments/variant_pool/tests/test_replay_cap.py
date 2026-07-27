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


def test_no_hardcoded_20s_clamp_survives_anywhere_in_the_chain() -> None:
    """runs/a1pilot postmortem: a SECOND 20s clamp in replay.py silently
    re-clamped what agent.py passed through, so the env cap never reached the
    smoke (REPLAY_FAIL elapsed 20.0s under a 120s cap). Pin both modules."""
    from pathlib import Path

    import harnessx.meta_harness.agent as agent_mod
    import harnessx.meta_harness.replay as replay_mod

    agent_src = Path(agent_mod.__file__).read_text(encoding="utf-8")
    replay_src = Path(replay_mod.__file__).read_text(encoding="utf-8")
    assert "min(replay_timeout_s, 20.0)" not in agent_src
    assert "min(timeout_s, 20.0)" not in replay_src
    # Single source of truth lives in replay; agent re-exports it.
    assert replay_mod._replay_timeout_cap_s is _replay_timeout_cap_s
