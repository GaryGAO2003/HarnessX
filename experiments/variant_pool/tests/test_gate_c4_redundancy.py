# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""C4 — lock the decision that CANONICALIZE / BUILD_SMOKE_L1 are no-op by design.

Batch C4 investigated where the gate's canonicalize (``harness.py:944``) and
build-smoke (``replay.py:64``) checks should go, and concluded they are
**redundant** with the two guarantees every candidate already carries before it
reaches the gate (SPEC §7.8):

* ``meta_agent.evolve`` runs canonicalize + synthetic replay smoke internally
  (``EvolveValidator.run``) and *raises* on failure, so an evolved candidate has
  provably passed both; a forked candidate is the same evolve product; and
* the recipe's ``evaluate`` step canonicalizes every candidate through
  ``_prepare_round_config`` (covering even the round-0 baseline that skips
  ``evolve``) *before* the gate runs, then executes the full harness for real.

So stages 2 and 3 stay no-op: no built-in check is wired. These tests lock that
behaviour, the preserved five-stage structure, the still-live injection seam,
and the fact that the gate's real interception comes from three other stages.

Fully offline: no LLM, no network, no ``HarnessConfig`` load.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from variant_pool.gate import (
    GATE_SEQUENCE,
    Decision,
    GateResult,
    GateStage,
    TaskEval,
    run_gate,
)
from variant_pool.ledger import SuccessLedger
from variant_pool.manifest import ChangeManifest, check_level2_roundtrip

# The two stages C4 pinned as no-op, and the three that actually intercept.
_NOOP_STAGES = {GateStage.CANONICALIZE, GateStage.BUILD_SMOKE_L1}
_REAL_INTERCEPTORS = {
    GateStage.MANIFEST_COMPLETE,
    GateStage.ROUNDTRIP_L2,
    GateStage.SEESAW_REGRESSION,
}


# ---------------------------------------------------------------------------
# Fixture helpers (local, so this file does not import a sibling test module)
# ---------------------------------------------------------------------------


def _ledger(*solved: str) -> SuccessLedger:
    """A ledger whose ``ever_solved`` contains exactly ``solved``."""
    ledger = SuccessLedger()
    for round_idx, task_id in enumerate(solved):
        ledger.record("V0", task_id, n_pass=2, n_att=2, round_idx=round_idx)
    return ledger


def _complete_tools_manifest(**overrides) -> ChangeManifest:
    """A complete tools-bucket manifest with Level-2 evidence (mirrors C-R10-02)."""
    data = {
        "candidate_id": "C-R10-02",
        "bucket": ["tools"],
        "capability_evidence": [
            check_level2_roundtrip("x" * 10_529, lambda content: content).as_capability_evidence()
        ],
        "file_changes": [
            {"path": "wiki_text_fetch.py", "action": "create", "diff_summary": "WikiTextFetch via MediaWiki API"}
        ],
        "predicted_impact": {"tasks_will_unlock": ["db4fd70a"]},
        "attribution_signature": {"type": "tool_call", "tool_name": "WikiTextFetch", "expected_min_calls": 1},
        "target_variant": "V0",
    }
    data.update(overrides)
    return ChangeManifest.model_validate(data)


class _AlwaysRuns:
    """A canonicalize/smoke check that records it ran and returns a canned verdict.

    Used only to prove the *injection seam* still works — the production recipe
    never passes one of these.
    """

    def __init__(self, log: list[str], name: str, verdict: tuple[bool, str]) -> None:
        self.log = log
        self.name = name
        self.verdict = verdict

    def __call__(self, *args):  # noqa: ARG002 - stub ignores its inputs
        self.log.append(self.name)
        return self.verdict


# ===========================================================================
# The gate has no built-in canonicalize / smoke check (the core of "no-op")
# ===========================================================================


def test_opaque_candidate_sails_through_both_no_op_stages() -> None:
    """An opaque candidate (like ``PoolCandidate``) is never stopped at stage 2 or 3."""
    ledger = _ledger()
    tk = [TaskEval("t", before=(0, 2), after=(2, 2))]

    result = run_gate("opaque-candidate", "parent-config", ledger, tk)

    assert result.passed is True
    assert result.decision is Decision.APPLY
    assert result.failed_stage not in _NOOP_STAGES


def test_complete_manifest_is_not_stopped_at_canonicalize_or_smoke() -> None:
    """Even a manifest — which *does* trigger built-in stage 1/4 — has no built-in 2/3.

    Stages 2 and 3 carry no default implementation at all: only stages 1
    (manifest) and 4 (roundtrip) get a built-in when the candidate is a
    ``ChangeManifest``. So a complete manifest reaches the seesaw without ever
    being checked for canonicalize/smoke.
    """
    ledger = _ledger()
    tk = [TaskEval("db4fd70a", before=(0, 2), after=(2, 2))]

    result = run_gate(_complete_tools_manifest(), "parent-config", ledger, tk)

    assert result.passed is True
    assert result.decision is Decision.APPLY
    assert result.failed_stage not in _NOOP_STAGES


# ===========================================================================
# For an opaque candidate, the seesaw is the ONLY interceptor
# ===========================================================================


@pytest.mark.parametrize(
    "solved, tk, expect_pass, expect_stage, expect_decision",
    [
        # improves one, regresses nothing -> APPLY, no halt
        ((), [TaskEval("a", before=(0, 2), after=(2, 2))], True, None, Decision.APPLY),
        # improves two, regresses two -> FORK, no halt
        (
            ("old1", "old2"),
            [
                TaskEval("new1", before=(0, 2), after=(2, 2)),
                TaskEval("new2", before=(0, 2), after=(2, 2)),
                TaskEval("old1", before=(2, 2), after=(0, 2)),
                TaskEval("old2", before=(2, 2), after=(0, 2)),
            ],
            True,
            None,
            Decision.FORK,
        ),
        # nothing improves, one regresses -> REJECT, halted at the seesaw
        (
            ("old",),
            [TaskEval("old", before=(2, 2), after=(0, 2))],
            False,
            GateStage.SEESAW_REGRESSION,
            Decision.REJECT,
        ),
    ],
)
def test_only_the_seesaw_governs_an_opaque_candidate(
    solved, tk, expect_pass, expect_stage, expect_decision
) -> None:
    """No opaque-candidate outcome is ever attributed to stage 2 or 3."""
    result = run_gate("opaque-candidate", "parent-config", _ledger(*solved), tk)

    assert result.passed is expect_pass
    assert result.failed_stage is expect_stage
    assert result.decision is expect_decision
    # The load-bearing invariant: an opaque candidate can only ever be halted by
    # the seesaw, never by the no-op canonicalize/smoke stages.
    assert result.failed_stage not in _NOOP_STAGES


# ===========================================================================
# The real interception comes from manifest / L2 / seesaw — not 2/3
# ===========================================================================


def test_real_interception_is_manifest_then_l2_then_seesaw() -> None:
    """Enumerate every way the gate halts and confirm none is a no-op stage."""
    ledger = _ledger()

    # (1) MANIFEST_COMPLETE — an incomplete manifest halts at stage 1.
    incomplete = run_gate(_complete_tools_manifest(target_variant=""), "p", ledger, [])
    assert incomplete.failed_stage is GateStage.MANIFEST_COMPLETE

    # (4) ROUNDTRIP_L2 — a tools manifest without Level-2 evidence halts at stage 4.
    no_l2 = run_gate(
        _complete_tools_manifest(
            capability_evidence=[
                {"type": "http_endpoint", "claim": "MediaWiki API returns text", "evidence": "10,529 chars"}
            ]
        ),
        "p",
        ledger,
        [],
    )
    assert no_l2.failed_stage is GateStage.ROUNDTRIP_L2

    # (5) SEESAW_REGRESSION — a purely harmful candidate halts at stage 5.
    seesaw = run_gate("opaque-candidate", "p", _ledger("old"), [TaskEval("old", before=(2, 2), after=(0, 2))])
    assert seesaw.failed_stage is GateStage.SEESAW_REGRESSION

    halting_stages = {incomplete.failed_stage, no_l2.failed_stage, seesaw.failed_stage}
    assert halting_stages == _REAL_INTERCEPTORS
    assert halting_stages.isdisjoint(_NOOP_STAGES)


# ===========================================================================
# The injection seam is deliberately preserved (V1 reward-hacking, SPEC §9.5)
# ===========================================================================


def test_injected_canonicalize_check_still_halts() -> None:
    """C4 keeps the seam: an explicitly injected canonicalize check still runs + halts."""
    log: list[str] = []
    result = run_gate(
        "opaque-candidate",
        "parent-config",
        _ledger(),
        [],
        check_canonicalize=_AlwaysRuns(log, "canonicalize", (False, "singleton group violated")),
    )

    assert log == ["canonicalize"]
    assert result.passed is False
    assert result.failed_stage is GateStage.CANONICALIZE
    assert "singleton group violated" in result.archive_reason


def test_injected_smoke_check_still_halts() -> None:
    """Same for build-smoke: injection wins over the no-op default."""
    log: list[str] = []
    result = run_gate(
        "opaque-candidate",
        "parent-config",
        _ledger(),
        [],
        check_smoke=_AlwaysRuns(log, "smoke", (False, "processor raised on hook drive")),
    )

    assert log == ["smoke"]
    assert result.failed_stage is GateStage.BUILD_SMOKE_L1
    assert "processor raised" in result.archive_reason


# ===========================================================================
# The five-stage structure and GateResult shape are unchanged
# ===========================================================================


def test_gate_sequence_still_has_all_five_stages_in_paper_order() -> None:
    assert GATE_SEQUENCE == (
        GateStage.MANIFEST_COMPLETE,
        GateStage.CANONICALIZE,
        GateStage.BUILD_SMOKE_L1,
        GateStage.ROUNDTRIP_L2,
        GateStage.SEESAW_REGRESSION,
    )


def test_the_two_no_op_stages_and_three_interceptors_partition_the_gate() -> None:
    """The no-op set and the real-interceptor set together are exactly the gate."""
    assert _NOOP_STAGES.isdisjoint(_REAL_INTERCEPTORS)
    assert _NOOP_STAGES | _REAL_INTERCEPTORS == set(GateStage)
    assert set(GATE_SEQUENCE) == set(GateStage)


def test_gate_result_shape_is_unchanged() -> None:
    """No-op annotation must not have touched the GateResult contract."""
    field_names = {f.name for f in dataclasses.fields(GateResult)}
    assert field_names == {"passed", "failed_stage", "decision", "archive_reason", "improved", "regressed"}


# ===========================================================================
# Tie to reality: the production candidate type is opaque to the gate
# ===========================================================================


def test_production_pool_candidate_is_opaque_to_the_gate() -> None:
    """``PoolCandidate`` is deliberately not a manifest, so stages 1-4 stay no-op.

    Imported lazily: ``run_variant_pool`` pulls in the full harnessx/benchmarks
    stack, and only this reality-check needs it.
    """
    import sys

    # conftest only adds ``experiments/`` to the path; the recipe lives under the
    # repo root, so put it on the path before importing (mirrors test_run_variant_pool).
    _root = Path(__file__).resolve().parents[3]
    if str(_root) not in sys.path:
        sys.path.insert(0, str(_root))

    from recipe.gaia_evolver.run_variant_pool import PoolCandidate

    candidate = PoolCandidate(
        candidate_id="C-R1-V0",
        target_variant="V0",
        config_path=Path("V0/config.yaml"),
    )
    assert not isinstance(candidate, ChangeManifest)

    # Run it through the gate: only the seesaw decides, nothing else halts it.
    result = run_gate(candidate, "parent-config", _ledger(), [TaskEval("t", before=(0, 2), after=(2, 2))])
    assert result.passed is True
    assert result.decision is Decision.APPLY
    assert result.failed_stage is None
