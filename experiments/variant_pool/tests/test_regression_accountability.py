# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""Offline tests for F-B — ``--regression-accountability``.

Fully offline. Exercises: (1) the ``regressions_for_gate`` partition, (2) both
critics' veto (DeterministicCritic + the LLM Critic's re-applied deterministic
veto) demoting non-shipped regressions under ``shipped_only`` while still hard-
gating a genuinely shipped-caused one, (3) the recipe's ``_shipped_caused_
regressions`` journal classifier (zero-ship variance vs a config change that
lands in the failing interval), and (4) the flag / provenance. Strict mode is
byte-identical throughout.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from recipe.gaia_evolver import run_variant_pool as rvp  # noqa: E402
from experiments.variant_pool.critic import (  # noqa: E402
    CriticContext,
    DeterministicCritic,
    demoted_regression_concern,
    regressions_for_gate,
)
from experiments.variant_pool.evidence import EvidenceStore, TaskDigest  # noqa: E402
from experiments.variant_pool.manifest import CandidateArtifact, ChangeManifest  # noqa: E402


def _artifact(root: Path, candidate_id: str, *, at_risk=(), target="V0") -> CandidateArtifact:
    output_dir = root / "artifacts" / candidate_id
    output_dir.mkdir(parents=True, exist_ok=True)
    config = output_dir / "config.yaml"
    config.write_text(f"candidate: {candidate_id}\n", encoding="utf-8")
    manifest = ChangeManifest.model_validate(
        {
            "candidate_id": candidate_id,
            "bucket": ["prompt"],
            "iterates_from": None,
            "capability_evidence": [],
            "file_changes": [
                {"path": f"harness/{candidate_id}.txt", "action": "modify", "diff_summary": "m"}
            ],
            "predicted_impact": {"tasks_will_unlock": ["t-fail"], "tasks_at_risk": list(at_risk)},
            "attribution_signature": None,
            "target_variant": target,
        }
    )
    return CandidateArtifact(config_path=config, manifest=manifest, target_variant=target)


# ===========================================================================
# regressions_for_gate / demoted_regression_concern
# ===========================================================================


def test_regressions_for_gate_strict_gates_everything() -> None:
    ctx = CriticContext(round_idx=1, target_variant="V0", regressions=("a", "b"))
    assert ctx.shipped_regressions is None  # default = strict
    assert regressions_for_gate(ctx) == (("a", "b"), ())


def test_regressions_for_gate_shipped_only_splits() -> None:
    ctx = CriticContext(
        round_idx=1, target_variant="V0", regressions=("a", "b", "c"), shipped_regressions=("b",)
    )
    gate, demoted = regressions_for_gate(ctx)
    assert gate == ("b",)
    assert demoted == ("a", "c")


def test_demoted_concern_names_the_task_and_is_non_blocking() -> None:
    msg = demoted_regression_concern("task-x")
    assert "task-x" in msg and "shipped_only" in msg and "not blocking" in msg


# ===========================================================================
# DeterministicCritic veto: strict vs shipped_only
# ===========================================================================


def test_deterministic_strict_hard_gates_an_unhandled_regression(tmp_path: Path) -> None:
    critic = DeterministicCritic(EvidenceStore(tmp_path / "ev"))
    c1 = _artifact(tmp_path, "C-R3-01", at_risk=())  # does NOT handle the regression
    ctx = CriticContext(round_idx=3, target_variant="V0", regressions=("old-task",))  # strict
    review = asyncio.run(critic.review(context=ctx, candidates=[c1]))
    assert review.no_op is True
    assert "old-task" in review.no_op_reasons[0]


def test_deterministic_shipped_only_demotes_a_non_shipped_regression(tmp_path: Path) -> None:
    critic = DeterministicCritic(EvidenceStore(tmp_path / "ev"))
    c1 = _artifact(tmp_path, "C-R3-01", at_risk=())
    # shipped_only, but nothing was shipped-caused -> empty shipped set.
    ctx = CriticContext(
        round_idx=3, target_variant="V0", regressions=("old-task",), shipped_regressions=()
    )
    review = asyncio.run(critic.review(context=ctx, candidates=[c1]))
    assert review.no_op is False  # NOT hard-gated
    assert review.ranked_candidate_ids == ("C-R3-01",)  # the round proceeds
    assert any("old-task" in concern for concern in review.strategy_concerns)  # visible


def test_deterministic_shipped_only_still_gates_a_shipped_regression(tmp_path: Path) -> None:
    critic = DeterministicCritic(EvidenceStore(tmp_path / "ev"))
    c1 = _artifact(tmp_path, "C-R3-01", at_risk=())
    ctx = CriticContext(
        round_idx=3,
        target_variant="V0",
        regressions=("old-task",),
        shipped_regressions=("old-task",),  # a shipped change caused it
    )
    review = asyncio.run(critic.review(context=ctx, candidates=[c1]))
    assert review.no_op is True  # still hard-gated in both modes
    assert "old-task" in review.no_op_reasons[0]


# ===========================================================================
# The LLM Critic re-applies the SAME deterministic veto -> same F-B behaviour
# ===========================================================================


def _llm_critic(tmp_path: Path):
    return rvp._LLMCritic(
        provider=SimpleNamespace(),  # unused by _regression_veto
        fallback=DeterministicCritic(EvidenceStore(tmp_path / "ev")),
    )


def test_llm_veto_strict_returns_veto_no_demotions(tmp_path: Path) -> None:
    critic = _llm_critic(tmp_path)
    c1 = _artifact(tmp_path, "C-R3-01", at_risk=())
    ctx = CriticContext(round_idx=3, target_variant="V0", regressions=("old-task",))
    veto, demoted = critic._regression_veto(ctx, (c1,), set())
    assert veto is not None and "old-task" in veto
    assert demoted == ()


def test_llm_veto_shipped_only_demotes_non_shipped(tmp_path: Path) -> None:
    critic = _llm_critic(tmp_path)
    c1 = _artifact(tmp_path, "C-R3-01", at_risk=())
    ctx = CriticContext(
        round_idx=3, target_variant="V0", regressions=("old-task",), shipped_regressions=()
    )
    veto, demoted = critic._regression_veto(ctx, (c1,), set())
    assert veto is None  # no hard gate
    assert len(demoted) == 1 and "old-task" in demoted[0]


def test_llm_veto_shipped_only_gates_shipped(tmp_path: Path) -> None:
    critic = _llm_critic(tmp_path)
    c1 = _artifact(tmp_path, "C-R3-01", at_risk=())
    ctx = CriticContext(
        round_idx=3,
        target_variant="V0",
        regressions=("old-task",),
        shipped_regressions=("old-task",),
    )
    veto, demoted = critic._regression_veto(ctx, (c1,), set())
    assert veto is not None and "old-task" in veto
    assert demoted == ()


# ===========================================================================
# recipe classifier: _shipped_caused_regressions (journal-fact classification)
# ===========================================================================


def _digest(task_id: str, round_idx: int, variant_id: str, solved: bool) -> TaskDigest:
    return TaskDigest(
        task_id=task_id,
        round_idx=round_idx,
        variant_id=variant_id,
        outcome=(1, 1) if solved else (0, 1),
    )


def _classify(config_change_rounds, digests, routed, regressions):
    """Call the recipe method with a lightweight fake ``self`` (no full recipe)."""
    fake_self = SimpleNamespace(
        evidence=SimpleNamespace(iter_digests=lambda: iter(digests)),
        _config_change_rounds=config_change_rounds,
    )
    variant = SimpleNamespace(routed_tasks=routed)
    return rvp.VariantPoolRecipe._shipped_caused_regressions(
        fake_self, variant, regressions
    )


def test_zero_ship_variance_is_never_shipped_caused() -> None:
    # Task passed R1, failed R2, but NOTHING ever shipped (runs/a1big4 R2/R3).
    digests = [_digest("t", 1, "V0", True), _digest("t", 2, "V0", False)]
    out = _classify({}, digests, routed={"t"}, regressions=("t",))
    assert out == ()  # demoted, not hard-gated


def test_ship_in_the_failing_interval_is_shipped_caused() -> None:
    # Passed R1, failed R2, and V0's config changed (APPLY) at R2 -> shipped-caused.
    digests = [_digest("t", 1, "V0", True), _digest("t", 2, "V0", False)]
    out = _classify({"V0": [2]}, digests, routed={"t"}, regressions=("t",))
    assert out == ("t",)


def test_ship_outside_the_interval_is_not_shipped_caused() -> None:
    # Passed R1, failed R2, but the only config change was at R5 (unrelated later).
    digests = [_digest("t", 1, "V0", True), _digest("t", 2, "V0", False)]
    out = _classify({"V0": [5]}, digests, routed={"t"}, regressions=("t",))
    assert out == ()


def test_ship_before_the_last_pass_does_not_explain_the_fail() -> None:
    # Config changed at R1, task passed at R2 (already under the new config) and
    # failed at R3 -> the change did not cause the R2->R3 fall.
    digests = [
        _digest("t", 2, "V0", True),
        _digest("t", 3, "V0", False),
    ]
    out = _classify({"V0": [1]}, digests, routed={"t"}, regressions=("t",))
    assert out == ()


def test_mixed_regressions_only_the_shipped_one_survives() -> None:
    digests = [
        _digest("shipped", 1, "V0", True),
        _digest("shipped", 2, "V0", False),
        _digest("noise", 1, "V0", True),
        _digest("noise", 2, "V0", False),
    ]
    out = _classify({"V0": [2]}, digests, routed={"shipped", "noise"}, regressions=("shipped", "noise"))
    # Both flipped in R2, but only tasks under a changed carrier config count;
    # here V0 changed at R2 so BOTH are shipped-caused (same carrier/interval).
    assert out == ("noise", "shipped")


# ===========================================================================
# flag / provenance
# ===========================================================================


def test_regression_accountability_flag_default_and_choices() -> None:
    parser = rvp.build_arg_parser()
    assert parser.parse_args([]).regression_accountability == "strict"
    assert (
        parser.parse_args(["--regression-accountability", "shipped_only"]).regression_accountability
        == "shipped_only"
    )
    with pytest.raises(SystemExit):
        parser.parse_args(["--regression-accountability", "loose"])


def test_regression_accountability_provenance_is_none_for_default() -> None:
    assert rvp._regression_accountability_provenance("strict") is None
    warn = rvp._regression_accountability_provenance("shipped_only")
    assert warn is not None and "regression_accountability" in warn
