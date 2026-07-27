# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""runs/a1pilot postmortem: the paper-prompted LLM Critic vetoed 4/6 candidates
at PIPELINE_CRITIC_INITIAL for missing declared Level-2 evidence — upstream of
the gate where M-22 machine-certification lives (evaluation trajectories do not
exist yet at Critic time, so the machine evidence CANNOT exist that early).
Both critic prompts must carry the routing directive: enforcement stays at the
deterministic gate with real evidence; the Critic records a concern instead."""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from recipe.gaia_evolver import run_variant_pool as rvp  # noqa: E402

_DIRECTIVE_MARK = "MACHINE-CERTIFIED downstream at the deterministic gate"
_NO_REJECT_MARK = "do NOT reject a candidate solely because declared Level-2"


def test_paper_critic_tail_carries_the_m22_routing_directive() -> None:
    assert _DIRECTIVE_MARK in rvp._PAPER_CRITIC_OUTPUT_CONTRACT_TAIL
    assert _NO_REJECT_MARK in rvp._PAPER_CRITIC_OUTPUT_CONTRACT_TAIL
    assert _DIRECTIVE_MARK in rvp._PAPER_CRITIC_PROMPT


def test_ours_critic_prompt_carries_the_m22_routing_directive() -> None:
    assert _DIRECTIVE_MARK in rvp._LLM_CRITIC_PROMPT
    assert _NO_REJECT_MARK in rvp._LLM_CRITIC_PROMPT
