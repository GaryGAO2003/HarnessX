# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""P1-1/R1 — the paper App B.1 prompts are RENDERED at build time.

The ``_PAPER_APP_B1_*`` constants carry the paper's Jinja-style placeholders
verbatim (``{{ round }}`` / ``{{ round_minus_1 }}`` / ``{{ reputation_summary }}``
/ ``{{ candidates_dir }}`` and a ``{ % if round >= 2 %} … { % endif %}`` block).
The runtime never rendered them, so the model read the raw braces. These tests pin
the fix: the *constants* still carry the braces (byte-identical), while the
*built* prompts and the injected Evolver guidance have none — the proof that
substitution happens at assembly time, not in the constants.

The reputation signal is computed from the EvidenceStore's ships (rejected
candidates carry no lever). The load-bearing invariant (commits 4e0810f,
fd04bb4): an empty store says so honestly, and a lever with ships but no realized
prediction yields "n/a", never a fabricated 0.00.

Fully offline.
"""

import re
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from recipe.gaia_evolver import run_variant_pool as rvp  # noqa: E402
from experiments.variant_pool.evidence import (  # noqa: E402
    EvidenceStore,
    RejectedCandidate,
    ShipOutcome,
)

_PLACEHOLDER = re.compile(r"\{\{.*?\}\}", re.DOTALL)
_CONDITIONAL = re.compile(r"\{ %.*?%\}", re.DOTALL)


def _built_planner(round_idx, *, evidence=None):
    planner = rvp._LLMPlanner(
        provider=object(),
        k_t=1,
        fallback=object(),
        prompt=rvp._PAPER_PLANNER_PROMPT,
        evidence=evidence,
    )
    return planner._build_prompt("EVID", round_idx=round_idx, truncation=(), retry_error=None)


def _built_critic(round_idx):
    critic = rvp._LLMCritic(provider=object(), fallback=object(), prompt=rvp._PAPER_CRITIC_PROMPT)
    return critic._build_prompt("EVID", round_idx=round_idx, truncation=(), retry_error=None)


def _built_evolver(round_idx):
    return rvp._render_paper_prompt(
        rvp._PAPER_EVOLVER_GUIDANCE,
        round_idx=round_idx,
        candidates_dir=rvp._EVOLVER_CANDIDATES_DIR,
    )


# ---------------------------------------------------------------------------
# (1) no placeholder survives into any built prompt
# ---------------------------------------------------------------------------


def test_no_placeholders_survive_in_built_prompts():
    for built in (_built_planner(3), _built_critic(3), _built_evolver(3), _built_planner(1)):
        assert _PLACEHOLDER.search(built) is None
        assert _CONDITIONAL.search(built) is None
        # strict: not even a lone brace-pair or bare marker fragment
        assert "{{" not in built and "}}" not in built
        assert "{ %" not in built and "%}" not in built


# ---------------------------------------------------------------------------
# (2) round / round_minus_1 substitute the right integers
# ---------------------------------------------------------------------------


def test_round_and_round_minus_1_substitute_correct_numbers():
    planner = _built_planner(3)
    assert "# Planner -- Round 3" in planner
    assert "R3/regressions.md" in planner  # {{ round }} in a path
    assert "R2/decision.md" in planner  # {{ round_minus_1 }} in a path
    assert "round: 3" in planner  # frontmatter {{ round }}
    assert "# Critic -- Round 7" in _built_critic(7)
    assert "# Evolver -- Round 5" in _built_evolver(5)


def test_round_minus_1_is_not_clobbered_by_round_substitution():
    # {{ round }} must not partially match inside {{ round_minus_1 }}.
    planner = _built_planner(4)
    assert "R3/decision.md" in planner  # round_minus_1 = 3
    assert "R4/regressions.md" in planner  # round = 4
    assert "_minus_1" not in planner


# ---------------------------------------------------------------------------
# (3) conditional block: kept at round >= 2, dropped below, markers gone
# ---------------------------------------------------------------------------

_BLOCK_SENTINEL = "Prior Critic ' s strategy_concern, if any."


def test_conditional_block_present_at_round_ge_2():
    planner = _built_planner(2)
    assert _BLOCK_SENTINEL in planner
    assert rvp._PLANNER_COND_OPEN not in planner
    assert rvp._PLANNER_COND_CLOSE not in planner
    # the block joins cleanly onto the preceding sentence (no marker residue)
    assert "Evolver decide the shape.\n- Prior Critic" in planner


def test_conditional_block_absent_at_round_1():
    planner = _built_planner(1)
    assert _BLOCK_SENTINEL not in planner
    assert rvp._PLANNER_COND_OPEN not in planner
    assert rvp._PLANNER_COND_CLOSE not in planner
    # the block is excised whole, leaving the surrounding text contiguous
    assert "Evolver decide the shape.\nBe evidence-anchored." in planner


# ---------------------------------------------------------------------------
# (4) reputation: honest no-data on an empty store; real per-bucket counts
# ---------------------------------------------------------------------------


def test_reputation_empty_store_is_honest_no_data(tmp_path):
    summary = rvp._reputation_summary(EvidenceStore(tmp_path), round_idx=1)
    assert summary == rvp._REPUTATION_EMPTY
    # and it flows into the built prompt in place of the placeholder
    built = _built_planner(1, evidence=EvidenceStore(tmp_path))
    assert rvp._REPUTATION_EMPTY in built
    assert "{{ reputation_summary }}" not in built


def test_reputation_carries_real_per_bucket_counts(tmp_path):
    store = EvidenceStore(tmp_path)
    # tools: predicted 2, realized 1 -> yield 0.50
    store.record_ship(
        ShipOutcome(
            candidate_id="C-R1-01",
            round_idx=1,
            variant_id="V0",
            levers=["tools"],
            predicted_flips=["t1", "t2"],
            realized_flips=["t1"],
            realized=True,
        )
    )
    # prompt: predicted 1, realized 0 -> yield 0.00 (a real zero rate)
    store.record_ship(
        ShipOutcome(
            candidate_id="C-R2-01",
            round_idx=2,
            variant_id="V0",
            levers=["prompt"],
            predicted_flips=["p1"],
            realized_flips=[],
            realized=True,
        )
    )
    store.append_rejected(
        RejectedCandidate(
            candidate_id="C-R2-09",
            round_idx=2,
            variant_id="V0",
            failed_stage="PIPELINE_GATE",
            archive_reason="x",
        )
    )
    summary = rvp._reputation_summary(store, round_idx=3)
    assert summary != rvp._REPUTATION_EMPTY
    assert "tools: 1 shipped over last 3 rounds, yield 0.50" in summary
    assert "prompt: 1 shipped over last 3 rounds, yield 0.00" in summary
    assert "+1 rejected candidate(s) this window" in summary
    # it reaches the built Planner prompt
    built = _built_planner(3, evidence=store)
    assert "tools: 1 shipped over last 3 rounds, yield 0.50" in built


def test_reputation_distinguishes_no_data_from_zero_rate(tmp_path):
    # A lever with a ship but no *realized* prediction has an undefined rate: it
    # must render "n/a", never 0.00. This is the exact distinction 4e0810f/fd04bb4
    # were about.
    store = EvidenceStore(tmp_path)
    store.record_ship(
        ShipOutcome(
            candidate_id="C-R2-02",
            round_idx=2,
            variant_id="V0",
            levers=["config"],
            predicted_flips=["c1"],
            realized_flips=[],
            realized=False,  # unrealised -> hit_rate is None, not 0.0
        )
    )
    summary = rvp._reputation_summary(store, round_idx=3)
    assert "config: 1 shipped over last 3 rounds, yield n/a (no realized predictions yet)" in summary
    assert "yield 0.00" not in summary


# ---------------------------------------------------------------------------
# (5) constants are byte-identical: they still carry the raw placeholders
# ---------------------------------------------------------------------------


def test_constants_still_contain_the_raw_placeholders():
    assert "{{ round }}" in rvp._PAPER_APP_B1_PLANNER
    assert "{{ round_minus_1 }}" in rvp._PAPER_APP_B1_PLANNER
    assert "{{ reputation_summary }}" in rvp._PAPER_APP_B1_PLANNER
    assert rvp._PLANNER_COND_OPEN in rvp._PAPER_APP_B1_PLANNER
    assert rvp._PLANNER_COND_CLOSE in rvp._PAPER_APP_B1_PLANNER
    assert "{{ round }}" in rvp._PAPER_APP_B1_EVOLVER
    assert "{{ candidates_dir }}" in rvp._PAPER_APP_B1_EVOLVER
    assert "{{ round }}" in rvp._PAPER_APP_B1_CRITIC
    # the assembled paper-mode constants (fed to the roles) also keep the braces:
    # rendering happens on a copy inside _build_prompt / the guidance injection.
    assert "{{ round }}" in rvp._PAPER_PLANNER_PROMPT
    assert "{{ reputation_summary }}" in rvp._PAPER_PLANNER_PROMPT
    assert "{{ round }}" in rvp._PAPER_CRITIC_PROMPT
    assert "{{ candidates_dir }}" in rvp._PAPER_EVOLVER_GUIDANCE


def test_candidates_dir_resolves_to_the_contract_scratch_token():
    built = _built_evolver(5)
    # our contract's real scratch-dir token, not the paper's candidates_dir/
    assert "emit a manifest at ` output_dir/C-R5-<NN>.md `" in built
    assert rvp._EVOLVER_CANDIDATES_DIR == "output_dir"
