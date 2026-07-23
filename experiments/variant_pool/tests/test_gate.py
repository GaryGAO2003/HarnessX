# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""Offline unit tests for ``variant_pool.gate`` (W5 + W21 + W27).

Stages 2 and 3 are exercised with injected stubs (batch A wires no repo
checks); stages 1 and 4 are tested both as stubs and against their real
:mod:`variant_pool.manifest` implementations; stage 5, the three-way seesaw, is
tested in full.
"""

from __future__ import annotations

import pytest

from variant_pool.gate import (
    DEFAULT_MIN_FORK,
    GATE_SEQUENCE,
    Decision,
    GateStage,
    TaskEval,
    _classify,
    _seesaw_three_way,
    level2_roundtrip_check,
    run_gate,
)
from variant_pool.ledger import SuccessLedger
from variant_pool.manifest import ChangeManifest, check_level2_roundtrip


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _ledger(*solved: str) -> SuccessLedger:
    """A ledger whose ``ever_solved`` contains exactly ``solved``."""
    ledger = SuccessLedger()
    for round_idx, task_id in enumerate(solved):
        ledger.record("V0", task_id, n_pass=2, n_att=2, round_idx=round_idx)
    return ledger


class _LastRoundLedger:
    """Wrong baseline on purpose: "solved" means "solved last round".

    Used to show that the two baselines genuinely disagree on the timeline
    fixture, so the full-history test cannot pass by accident.
    """

    def __init__(self, solved_last_round: set[str]) -> None:
        self._solved = solved_last_round

    def is_ever_solved(self, task_id: str) -> bool:
        return task_id in self._solved


class _Spy:
    """Records that a stage ran, and answers with a canned verdict."""

    def __init__(self, log: list[str], name: str, result):
        self.log = log
        self.name = name
        self.result = result

    def __call__(self, *args):  # noqa: ARG002 - stubs ignore their inputs
        self.log.append(self.name)
        return self.result


# ===========================================================================
# Stage 5 — the three-way exit (§4.5 p.11)
# ===========================================================================


def test_apply_when_something_improves_and_nothing_regresses() -> None:
    """Verbatim: improves some tasks without regressing any -> applied to its variant."""
    ledger = _ledger("kept")
    tk = [
        TaskEval("unlocked", before=(0, 2), after=(2, 2)),
        TaskEval("kept", before=(2, 2), after=(1, 2)),  # still solved -> not a regression
    ]
    improved, regressed = _classify(tk, ledger)
    assert improved == {"unlocked"}
    assert regressed == set()
    assert _seesaw_three_way(tk, ledger) is Decision.APPLY


def test_fork_when_it_improves_a_subset_while_regressing_others() -> None:
    """"improves a subset while regressing others" -> fork, do not reject."""
    ledger = _ledger("old1", "old2")
    tk = [
        TaskEval("new1", before=(0, 2), after=(1, 2)),
        TaskEval("new2", before=(0, 2), after=(2, 2)),
        TaskEval("old1", before=(2, 2), after=(0, 2)),
        TaskEval("old2", before=(1, 2), after=(0, 2)),
    ]
    improved, regressed = _classify(tk, ledger)
    assert improved == {"new1", "new2"}
    assert regressed == {"old1", "old2"}
    assert _seesaw_three_way(tk, ledger) is Decision.FORK


def test_reject_when_nothing_improves() -> None:
    ledger = _ledger("old1")
    tk = [
        TaskEval("old1", before=(2, 2), after=(0, 2)),
        TaskEval("flat", before=(0, 2), after=(0, 2)),
    ]
    improved, regressed = _classify(tk, ledger)
    assert improved == set()
    assert regressed == {"old1"}
    assert _seesaw_three_way(tk, ledger) is Decision.REJECT


def test_reject_a_completely_flat_candidate() -> None:
    ledger = _ledger()
    tk = [TaskEval("a", before=(1, 2), after=(1, 2)), TaskEval("b", before=(2, 2), after=(2, 2))]
    assert _seesaw_three_way(tk, ledger) is Decision.REJECT


def test_a_single_passing_rollout_counts_as_solved() -> None:
    """pass@2: "solved" is n_pass >= 1, not 2/2."""
    ledger = _ledger()
    tk = [TaskEval("t", before=(0, 2), after=(1, 2))]
    assert _classify(tk, ledger) == ({"t"}, set())


def test_improved_and_regressed_are_mutually_exclusive() -> None:
    ledger = _ledger("t")
    tk = [TaskEval("t", before=(0, 2), after=(1, 2))]
    improved, regressed = _classify(tk, ledger)
    assert improved == {"t"}
    assert regressed == set()


# ---------------------------------------------------------------------------
# W21 — the baseline is the full history, not the previous round
# ---------------------------------------------------------------------------


def test_regression_is_measured_against_the_full_history() -> None:
    """R3 solves it, R5 quietly breaks it, an R6 candidate leaves it broken.

    The candidate never *caused* the R5 breakage, yet the seesaw constraint of
    §4.1 p.8 is about the whole trace store: shipping a config under which an
    ever-solved task sits at 0/2 is a regression. A last-round baseline would
    wave this candidate through, which is the strictly weaker constraint W21
    exists to avoid.
    """
    ledger = SuccessLedger()
    ledger.record("V0", "t_flaky", n_pass=2, n_att=2, round_idx=3)  # R3: solved
    ledger.record("V0", "t_flaky", n_pass=0, n_att=2, round_idx=5)  # R5: quietly broken
    assert ledger.is_ever_solved("t_flaky")

    tk = [
        TaskEval("t_flaky", before=(0, 2), after=(0, 2)),  # already broken before the candidate
        TaskEval("t_new", before=(0, 2), after=(2, 2)),
    ]

    improved, regressed = _classify(tk, ledger)
    assert improved == {"t_new"}
    assert regressed == {"t_flaky"}
    assert _seesaw_three_way(tk, ledger, min_fork=(1, 1)) is Decision.FORK

    # the same fixture under a last-round baseline: t_flaky was 0/2 in R5 too,
    # so it would not register as a regression and the candidate would ship.
    wrong_baseline = _LastRoundLedger(solved_last_round=set())
    assert _classify(tk, wrong_baseline) == ({"t_new"}, set())
    assert _seesaw_three_way(tk, wrong_baseline, min_fork=(1, 1)) is Decision.APPLY


def test_baseline_spans_variants() -> None:
    """Ever-solved by *another* variant still counts against this candidate."""
    ledger = SuccessLedger()
    ledger.record("V1", "shared", n_pass=1, n_att=2, round_idx=0)

    tk = [TaskEval("shared", before=(0, 2), after=(0, 2)), TaskEval("x", before=(0, 2), after=(2, 2))]
    _, regressed = _classify(tk, ledger)
    assert regressed == {"shared"}


def test_a_task_never_solved_by_anyone_is_not_a_regression() -> None:
    ledger = _ledger()
    tk = [TaskEval("never", before=(0, 2), after=(0, 2)), TaskEval("x", before=(0, 2), after=(2, 2))]
    improved, regressed = _classify(tk, ledger)
    assert improved == {"x"}
    assert regressed == set()
    assert _seesaw_three_way(tk, ledger) is Decision.APPLY


# ---------------------------------------------------------------------------
# Minimum fork size (ours — SPEC §2.4/§6.6)
# ---------------------------------------------------------------------------


def test_default_min_fork_is_two_two() -> None:
    """Explicit test of an *our-default* knob."""
    assert DEFAULT_MIN_FORK == (2, 2)


def test_a_one_one_conflict_does_not_earn_a_fork() -> None:
    """One improvement against one regression is pass@2 noise, not a cluster.

    The candidate is rejected rather than forked: a variant slot is not spent,
    and the improvement cannot be applied either, because the seesaw forbids
    regressing an ever-solved task.
    """
    ledger = _ledger("old")
    tk = [
        TaskEval("new", before=(0, 2), after=(2, 2)),
        TaskEval("old", before=(2, 2), after=(0, 2)),
    ]
    improved, regressed = _classify(tk, ledger)
    assert (len(improved), len(regressed)) == (1, 1)

    assert _seesaw_three_way(tk, ledger) is Decision.REJECT
    assert _seesaw_three_way(tk, ledger, min_fork=(1, 1)) is Decision.FORK


def test_the_threshold_needs_both_sides() -> None:
    """2 improvements against 1 regression still misses ``min_fork=(2, 2)``."""
    ledger = _ledger("old")
    tk = [
        TaskEval("new1", before=(0, 2), after=(2, 2)),
        TaskEval("new2", before=(0, 2), after=(1, 2)),
        TaskEval("old", before=(2, 2), after=(0, 2)),
    ]
    assert _seesaw_three_way(tk, ledger) is Decision.REJECT
    assert _seesaw_three_way(tk, ledger, min_fork=(2, 1)) is Decision.FORK


def test_min_fork_is_validated() -> None:
    ledger = _ledger()
    tk = [TaskEval("t", before=(0, 2), after=(2, 2))]
    with pytest.raises(ValueError):
        _seesaw_three_way(tk, ledger, min_fork=(0, 2))
    with pytest.raises(ValueError):
        run_gate(None, None, ledger, tk, min_fork=(2, 0))


# ===========================================================================
# Stages 1-4 — ordered, halt on first failure, archive the reason (§4.3 p.10)
# ===========================================================================


def _passing_checks(log: list[str]) -> dict:
    return {
        "check_manifest": _Spy(log, "manifest", []),
        "check_canonicalize": _Spy(log, "canonicalize", (True, "ok")),
        "check_smoke": _Spy(log, "smoke", (True, "ok")),
        "check_roundtrip": _Spy(log, "roundtrip", (True, "ok")),
    }


def test_all_stages_run_in_order_then_the_seesaw_decides() -> None:
    log: list[str] = []
    ledger = _ledger("kept")
    tk = [TaskEval("unlocked", before=(0, 2), after=(2, 2)), TaskEval("kept", before=(2, 2), after=(2, 2))]

    result = run_gate("cand", "parent", ledger, tk, **_passing_checks(log))

    assert log == ["manifest", "canonicalize", "smoke", "roundtrip"]
    assert result.passed is True
    assert result.failed_stage is None
    assert result.decision is Decision.APPLY
    assert result.improved == frozenset({"unlocked"})


def test_gate_sequence_matches_the_paper_order() -> None:
    assert GATE_SEQUENCE == (
        GateStage.MANIFEST_COMPLETE,
        GateStage.CANONICALIZE,
        GateStage.BUILD_SMOKE_L1,
        GateStage.ROUNDTRIP_L2,
        GateStage.SEESAW_REGRESSION,
    )


def test_incomplete_manifest_halts_before_anything_else() -> None:
    log: list[str] = []
    checks = _passing_checks(log)
    checks["check_manifest"] = _Spy(log, "manifest", ["target_variant", "capability_evidence"])

    result = run_gate("cand", "parent", _ledger(), [], **checks)

    assert log == ["manifest"]  # nothing downstream ran
    assert result.passed is False
    assert result.failed_stage is GateStage.MANIFEST_COMPLETE
    assert result.decision is None
    assert "capability_evidence" in result.archive_reason
    assert "target_variant" in result.archive_reason


def test_canonicalize_failure_halts_the_gate() -> None:
    log: list[str] = []
    checks = _passing_checks(log)
    checks["check_canonicalize"] = _Spy(log, "canonicalize", (False, "singleton group violated"))

    result = run_gate("cand", "parent", _ledger(), [], **checks)

    assert log == ["manifest", "canonicalize"]
    assert result.failed_stage is GateStage.CANONICALIZE
    assert result.archive_reason == "CANONICALIZE: singleton group violated"


def test_smoke_failure_halts_the_gate() -> None:
    log: list[str] = []
    checks = _passing_checks(log)
    checks["check_smoke"] = _Spy(log, "smoke", (False, "processor raised on hook drive"))

    result = run_gate("cand", "parent", _ledger(), [], **checks)

    assert log == ["manifest", "canonicalize", "smoke"]
    assert result.failed_stage is GateStage.BUILD_SMOKE_L1
    assert "processor raised" in result.archive_reason


def test_level2_roundtrip_failure_halts_the_gate() -> None:
    """"a unit call that returns does not prove the agent sees the return"."""
    log: list[str] = []
    checks = _passing_checks(log)
    checks["check_roundtrip"] = _Spy(log, "roundtrip", (False, "tool output dropped by _prepare_messages"))

    result = run_gate("cand", "parent", _ledger(), [], **checks)

    assert log == ["manifest", "canonicalize", "smoke", "roundtrip"]
    assert result.failed_stage is GateStage.ROUNDTRIP_L2
    assert result.decision is None


def test_omitted_checks_pass_so_stage_a_can_run_offline() -> None:
    ledger = _ledger()
    tk = [TaskEval("t", before=(0, 2), after=(2, 2))]
    result = run_gate(None, None, ledger, tk)
    assert result.passed is True
    assert result.decision is Decision.APPLY


# ---------------------------------------------------------------------------
# GateResult payload
# ---------------------------------------------------------------------------


def test_a_fork_result_carries_the_tasks_the_new_variant_serves() -> None:
    ledger = _ledger("old1", "old2")
    tk = [
        TaskEval("new1", before=(0, 2), after=(2, 2)),
        TaskEval("new2", before=(0, 2), after=(2, 2)),
        TaskEval("old1", before=(2, 2), after=(0, 2)),
        TaskEval("old2", before=(2, 2), after=(0, 2)),
    ]
    result = run_gate("cand", "parent", ledger, tk)

    assert result.passed is True
    assert result.decision is Decision.FORK
    assert result.improved == frozenset({"new1", "new2"})
    assert result.regressed == frozenset({"old1", "old2"})


def test_a_rejected_candidate_is_archived_with_a_reason() -> None:
    ledger = _ledger("old")
    tk = [TaskEval("old", before=(2, 2), after=(0, 2))]
    result = run_gate("cand", "parent", ledger, tk)

    assert result.passed is False
    assert result.decision is Decision.REJECT
    assert result.failed_stage is GateStage.SEESAW_REGRESSION
    assert "no task improved" in result.archive_reason
    assert "old" in result.archive_reason


def test_a_below_threshold_rejection_says_so() -> None:
    ledger = _ledger("old")
    tk = [
        TaskEval("new", before=(0, 2), after=(2, 2)),
        TaskEval("old", before=(2, 2), after=(0, 2)),
    ]
    result = run_gate("cand", "parent", ledger, tk)

    assert result.decision is Decision.REJECT
    assert "below fork threshold" in result.archive_reason
    assert "min_fork=(2, 2)" in result.archive_reason


# ===========================================================================
# Stages 1 and 4 wired to the real manifest contract (W13 + W24)
# ===========================================================================


def _manifest(**overrides) -> ChangeManifest:
    """A complete tools-bucket manifest, Level-2 evidence included."""
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


def test_a_complete_manifest_passes_stage_one_without_a_stub() -> None:
    ledger = _ledger()
    tk = [TaskEval("db4fd70a", before=(0, 2), after=(2, 2))]

    result = run_gate(_manifest(), "parent", ledger, tk)

    assert result.passed is True
    assert result.decision is Decision.APPLY


def test_an_incomplete_manifest_fails_stage_one_for_real() -> None:
    """Gate stage 1 is ``validate_complete`` — here, our target_variant field."""
    result = run_gate(_manifest(target_variant=""), "parent", _ledger(), [])

    assert result.passed is False
    assert result.failed_stage is GateStage.MANIFEST_COMPLETE
    assert result.decision is None
    assert "target_variant" in result.archive_reason


def test_an_injected_manifest_check_still_wins() -> None:
    """Batch C may substitute its own completeness check; injection takes precedence."""
    log: list[str] = []
    checks = _passing_checks(log)
    result = run_gate(_manifest(target_variant=""), "parent", _ledger(), [], **checks)

    assert result.failed_stage is not GateStage.MANIFEST_COMPLETE
    assert log == ["manifest", "canonicalize", "smoke", "roundtrip"]


def test_a_tools_candidate_without_level2_evidence_fails_stage_four() -> None:
    """p.37: the Critic verified Level-2 evidence before accepting a tools candidate."""
    manifest = _manifest(
        capability_evidence=[
            {"type": "http_endpoint", "claim": "MediaWiki API returns text", "evidence": "10,529 chars"}
        ]
    )
    result = run_gate(manifest, "parent", _ledger(), [])

    assert result.passed is False
    assert result.failed_stage is GateStage.ROUNDTRIP_L2
    assert "Level-2" in result.archive_reason


def test_a_pure_prompt_candidate_is_exempt_from_stage_four() -> None:
    """p.32: "Pure prompt-bucket candidates (no code asset) are exempt"."""
    manifest = _manifest(
        bucket=["prompt"],
        capability_evidence=[],
        attribution_signature=None,
        file_changes=[{"path": "gaia_agent.md", "action": "modify", "diff_summary": "one line"}],
    )
    result = run_gate(manifest, "parent", _ledger(), [TaskEval("db4fd70a", before=(0, 2), after=(2, 2))])

    assert result.passed is True
    assert result.decision is Decision.APPLY


def test_stage_one_halts_before_stage_four_on_a_manifest() -> None:
    """Ordering survives the real implementations: the first failing check halts."""
    manifest = _manifest(candidate_id="", capability_evidence=[])
    result = run_gate(manifest, "parent", _ledger(), [])

    assert result.failed_stage is GateStage.MANIFEST_COMPLETE
    assert "candidate_id" in result.archive_reason


def test_a_live_roundtrip_check_can_be_injected() -> None:
    """Batch C wires the provider serializer through ``level2_roundtrip_check``."""
    truncating = level2_roundtrip_check(lambda content: content[:200], "x" * 10_529)
    result = run_gate(_manifest(), "parent", _ledger(), [], check_roundtrip=truncating)

    assert result.failed_stage is GateStage.ROUNDTRIP_L2
    assert "10,529 chars in" in result.archive_reason

    surviving = level2_roundtrip_check(lambda content: content, "x" * 10_529)
    ok = run_gate(_manifest(), "parent", _ledger(), [TaskEval("t", before=(0, 2), after=(2, 2))],
                  check_roundtrip=surviving)
    assert ok.passed is True


def test_an_opaque_candidate_keeps_the_stage_a_stub_behaviour() -> None:
    """Nothing changes for callers that do not hand the gate a manifest."""
    result = run_gate("cand", "parent", _ledger(), [TaskEval("t", before=(0, 2), after=(2, 2))])
    assert result.passed is True
    assert result.decision is Decision.APPLY


# ---------------------------------------------------------------------------
# TaskEval validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "before,after",
    [((3, 2), (0, 2)), ((0, 2), (3, 2)), ((-1, 2), (0, 2))],
)
def test_task_eval_rejects_impossible_outcomes(before, after) -> None:
    with pytest.raises(ValueError):
        TaskEval("t", before=before, after=after)
