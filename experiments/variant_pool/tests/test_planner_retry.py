# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""Offline tests for F4 — ``--planner-retry``.

Fully offline. Exercises the LLM Planner's empty-landscape retry: a blank meta
response (``briefs: []``) is re-issued up to ``planner_retry`` extra times before
the round falls through to the existing empty-landscape short-circuit. Default
``0`` is byte-identical (one call, no retry, empty landscape returned unchanged).

Measured motivation: e_pervar3 saw 2 of 34 planner meta calls return blank, so
e_pervar3 ran 16 rounds on a degenerate 2-variant pool. --planner-retry N re-asks
the model before conceding the empty landscape.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from recipe.gaia_evolver import run_variant_pool as rvp  # noqa: E402
from experiments.variant_pool.candidate_pipeline import PipelineContext  # noqa: E402


_LOGGER_NAME = "gaia_evolver.variant_pool"
_BLANK = json.dumps({"briefs": [], "landscape_notes": "nothing to change"})


def _full_landscape(k: int) -> str:
    """A well-formed non-empty landscape: ``k`` briefs that survive validation."""
    return json.dumps(
        {
            "briefs": [
                {"buckets": ["prompt"], "rationale": f"tighten prompt {i}", "task_ids": []}
                for i in range(k)
            ],
            "landscape_notes": "recovered",
        }
    )


class _Resp:
    def __init__(self, content: str) -> None:
        self.content = content


class _ScriptedProvider:
    """Returns queued meta responses in order; repeats the last once exhausted.

    Mirrors the ``provider.complete(messages, tools)`` seam ``_LLMPlanner._complete``
    calls. ``calls`` counts how many meta calls the planner actually made.
    """

    def __init__(self, *responses: str) -> None:
        self._responses = list(responses)
        self.calls = 0

    async def complete(self, messages, tools):
        idx = min(self.calls, len(self._responses) - 1)
        self.calls += 1
        return _Resp(self._responses[idx])


def _planner(provider, *, k_t: int, planner_retry: int) -> "rvp._LLMPlanner":
    return rvp._LLMPlanner(
        provider=provider,
        k_t=k_t,
        fallback=rvp._DeterministicPlanner(k_t=k_t),
        planner_retry=planner_retry,
    )


def _context(tmp_path: Path) -> PipelineContext:
    # _plan_llm reads target_variant / round_idx / regressions / failure_buckets
    # and the digests; it never opens current_config_path, so tmp stubs suffice.
    return PipelineContext(
        round_idx=1,
        target_variant="V0",
        current_config_path=tmp_path / "config.yaml",
        trajectories_dir=tmp_path,
        output_root=tmp_path,
    )


def _empty_landscape_warnings(caplog) -> list[str]:
    return [
        r.getMessage()
        for r in caplog.records
        if "planner returned empty landscape" in r.getMessage()
    ]


def test_blank_then_valid_retry_one_recovers_full_pool(tmp_path, caplog):
    """(a) blank-then-valid with retry=1 -> full-width pool, exactly one WARNING."""
    provider = _ScriptedProvider(_BLANK, _full_landscape(3))
    planner = _planner(provider, k_t=3, planner_retry=1)

    with caplog.at_level(logging.WARNING, logger=_LOGGER_NAME):
        artifact = asyncio.run(planner.plan(context=_context(tmp_path), digests=()))

    assert not artifact.empty_landscape
    assert len(artifact.briefs) == 3  # full-width landscape recovered on the retry
    assert provider.calls == 2  # one base call + one retry

    warnings = _empty_landscape_warnings(caplog)
    assert len(warnings) == 1
    assert "retry 1/1" in warnings[0]


def test_all_blank_retry_two_falls_through_and_counts_attempts(tmp_path, caplog):
    """(b) all-blank with retry=2 -> existing empty_landscape path; attempts counted."""
    provider = _ScriptedProvider(_BLANK)  # always blank (last response repeats)
    planner = _planner(provider, k_t=3, planner_retry=2)

    with caplog.at_level(logging.WARNING, logger=_LOGGER_NAME):
        artifact = asyncio.run(planner.plan(context=_context(tmp_path), digests=()))

    assert artifact.empty_landscape  # unchanged empty-landscape behaviour
    assert artifact.briefs == ()
    assert provider.calls == 3  # base + 2 retries, all blank

    assert _empty_landscape_warnings(caplog) == [
        "planner returned empty landscape, retry 1/2",
        "planner returned empty landscape, retry 2/2",
    ]


def test_default_retry_zero_single_call_no_retry(tmp_path, caplog):
    """(c) default retry=0 -> one call even on blank; no retry, byte-identical path.

    A valid second response is queued but MUST NOT be consumed: retry=0 makes a
    single meta call and returns the empty landscape exactly as today.
    """
    provider = _ScriptedProvider(_BLANK, _full_landscape(3))
    planner = _planner(provider, k_t=3, planner_retry=0)

    with caplog.at_level(logging.WARNING, logger=_LOGGER_NAME):
        artifact = asyncio.run(planner.plan(context=_context(tmp_path), digests=()))

    assert artifact.empty_landscape  # blank short-circuits the round, as today
    assert provider.calls == 1  # single meta call, no retry attempted
    assert _empty_landscape_warnings(caplog) == []
