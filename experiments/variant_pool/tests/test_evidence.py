# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""Offline unit tests for ``variant_pool.evidence`` (W25).

Everything lands under ``tmp_path``; nothing here compresses a trace, because
the Digester prompt is withheld by the paper (appendix F.1) and the compression
step is batch C. What is tested is the contract the rest of the loop reads:
digest persistence, cross-round continuity, and the hit-rate the Critic's
portfolio audit runs on.

The hit-rate reference is the paper's own worked example: C-R10-02 predicted
five unlocks plus two stabilises and five of those seven flipped, "hit rate
0.71" (p.37).
"""

from __future__ import annotations

import json

import pytest

from variant_pool.evidence import (
    LEVER_BAN_HIT_RATE,
    LEVER_BAN_MIN_SHIPS,
    LEVER_BAN_WINDOW,
    EvidenceStore,
    RejectedCandidate,
    ShipOutcome,
    TaskDigest,
    ship_outcome_from_manifest,
)
from variant_pool.manifest import ChangeManifest

# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _digest(task_id: str, round_idx: int, variant_id: str = "V0", n_pass: int = 0, **kwargs) -> TaskDigest:
    return TaskDigest(
        task_id=task_id,
        round_idx=round_idx,
        variant_id=variant_id,
        outcome=(n_pass, 2),
        **kwargs,
    )


def _ship(candidate_id: str, round_idx: int, *, levers, predicted, realized, variant_id="V0") -> ShipOutcome:
    return ShipOutcome(
        candidate_id=candidate_id,
        round_idx=round_idx,
        variant_id=variant_id,
        levers=list(levers),
        predicted_flips=list(predicted),
        realized_flips=list(realized),
    )


@pytest.fixture
def store(tmp_path) -> EvidenceStore:
    return EvidenceStore(tmp_path / "runs" / "m1")


# ===========================================================================
# TaskDigest
# ===========================================================================


def test_a_digest_carries_the_paper_fields_plus_the_variant(store: EvidenceStore) -> None:
    """§4.3 p.10 fields; ``variant_id`` is ours (report §3.5: the layout has none)."""
    digest = _digest(
        "db4fd70a",
        10,
        variant_id="V1",
        failure_category="blocked-source",
        implicated_components=["WebFetch"],
        evidence_anchors=["trajectories/db4fd70a_r0.jsonl#step_0"],
    )
    assert digest.solved is False
    assert digest.variant_id == "V1"
    assert digest.failure_category == "blocked-source"


def test_pass_at_2_solved_means_one_passing_rollout() -> None:
    assert _digest("t", 0, n_pass=1).solved is True
    assert _digest("t", 0, n_pass=0).solved is False


@pytest.mark.parametrize("outcome", [(3, 2), (-1, 2)])
def test_impossible_outcomes_are_rejected(outcome) -> None:
    with pytest.raises(ValueError):
        TaskDigest(task_id="t", round_idx=0, variant_id="V0", outcome=outcome)


def test_digest_dict_round_trip_keeps_the_outcome_a_tuple() -> None:
    digest = _digest("t", 3, n_pass=1, implicated_components=["BudgetProcessor"])
    again = TaskDigest.from_dict(json.loads(json.dumps(digest.to_dict())))
    assert again == digest
    assert isinstance(again.outcome, tuple)


# ===========================================================================
# EvidenceStore — JSONL persistence (§E.1 p.43)
# ===========================================================================


def test_digests_round_trip_through_task_history_jsonl(store: EvidenceStore) -> None:
    written = [_digest("a", 0), _digest("b", 0, n_pass=2), _digest("a", 1, n_pass=1)]
    for digest in written:
        store.append_digest(digest)

    assert store.task_history_path.name == "task_history.jsonl"
    assert store.task_history_path.parent.name == "data"
    assert list(store.iter_digests()) == written


def test_one_line_per_round_task_variant(store: EvidenceStore) -> None:
    """Verbatim "one line per (round, task)" (p.43), plus our variant dimension."""
    store.append_digest(_digest("a", 0, variant_id="V0"))
    store.append_digest(_digest("a", 0, variant_id="V1"))
    lines = store.task_history_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    assert {json.loads(line)["variant_id"] for line in lines} == {"V0", "V1"}


def test_an_empty_store_reads_as_empty(store: EvidenceStore) -> None:
    assert list(store.iter_digests()) == []
    assert store.ship_outcomes() == []
    assert store.rejected_candidates() == []
    assert store.cross_round_history("nobody") == []
    assert store.hit_rate() is None


def test_rejected_candidates_are_archived_with_their_reason(store: EvidenceStore) -> None:
    """§4.3 p.10: failing candidates are "archived with their rejection reason"."""
    store.append_rejected(
        RejectedCandidate(
            candidate_id="C-R10-01",
            round_idx=10,
            variant_id="V0",
            failed_stage="ROUNDTRIP_L2",
            archive_reason="ROUNDTRIP_L2: tool output dropped by _prepare_messages",
        )
    )
    archived = store.rejected_candidates()
    assert len(archived) == 1
    assert archived[0]["failed_stage"] == "ROUNDTRIP_L2"
    assert "dropped" in archived[0]["archive_reason"]


def test_ship_outcomes_is_a_json_array_not_jsonl(store: EvidenceStore) -> None:
    """p.43 names it ``ship_outcomes.json``, so it holds one array."""
    store.append_ship(_ship("C-R1-01", 1, levers=["prompt"], predicted=["a"], realized=["a"]))
    store.append_ship(_ship("C-R2-01", 2, levers=["config"], predicted=["b"], realized=[]))

    parsed = json.loads(store.ship_outcomes_path.read_text(encoding="utf-8"))
    assert isinstance(parsed, list)
    assert [entry["candidate_id"] for entry in parsed] == ["C-R1-01", "C-R2-01"]


# ===========================================================================
# Cross-round continuity (§4.3 p.10)
# ===========================================================================


def test_cross_round_history_is_ordered_and_variant_scoped(store: EvidenceStore) -> None:
    store.append_digest(_digest("t", 2, variant_id="V0", n_pass=0))
    store.append_digest(_digest("t", 4, variant_id="V0", n_pass=2))
    store.append_digest(_digest("t", 3, variant_id="V1", n_pass=1))
    store.append_digest(_digest("other", 2, variant_id="V0"))

    v0 = store.cross_round_history("t", "V0")
    assert [entry["round_idx"] for entry in v0] == [2, 4]
    assert [entry["solved"] for entry in v0] == [False, True]

    # ours: no variant filter spans variants, which a task moved by routing needs
    everywhere = store.cross_round_history("t")
    assert [(e["round_idx"], e["variant_id"]) for e in everywhere] == [(2, "V0"), (3, "V1"), (4, "V0")]


def test_history_links_the_ships_that_named_the_task(store: EvidenceStore) -> None:
    """"links to its history of prior outcomes and shipped edits" (p.10)."""
    store.append_digest(_digest("db4fd70a", 10, n_pass=2))
    store.append_ship(
        _ship("C-R10-02", 10, levers=["tools"], predicted=["db4fd70a", "f0f46385"], realized=["db4fd70a"])
    )
    store.append_ship(_ship("C-R10-03", 10, levers=["prompt"], predicted=["unrelated"], realized=[]))

    entry = store.cross_round_history("db4fd70a", "V0")[0]
    assert entry["ships"] == ["C-R10-02"]


def test_prior_history_stops_before_the_digests_own_round(store: EvidenceStore) -> None:
    for round_idx in (0, 1, 2):
        store.append_digest(_digest("t", round_idx))

    current = _digest("t", 2)
    linked = store.attach_prior_history(current)

    assert [entry["round_idx"] for entry in linked.prior_history] == [0, 1]
    assert current.prior_history == []  # the original is not mutated


# ===========================================================================
# Hit-rate — the Critic portfolio audit input (SPEC §6.5)
# ===========================================================================


def test_the_paper_worked_example_scores_071(store: EvidenceStore) -> None:
    """C-R10-02: five of the seven predicted tasks flipped -> 0.71 (p.37)."""
    ship = _ship(
        "C-R10-02",
        10,
        levers=["tools", "prompt", "config"],
        predicted=["db4fd70a", "f0f46385", "983bba7c", "08f3a05f", "5e2a91b0", "4b6bb5f7", "42d4198c"],
        realized=["db4fd70a", "f0f46385", "983bba7c", "08f3a05f", "5e2a91b0"],
    )
    assert len(ship.predicted_flips) == 7
    assert ship.hit_rate == pytest.approx(0.71, abs=0.005)

    store.append_ship(ship)
    assert store.hit_rate() == pytest.approx(5 / 7)
    # a composite ship counts towards each of its buckets (ours)
    for lever in ("tools", "prompt", "config"):
        assert store.hit_rate(lever) == pytest.approx(5 / 7)
    assert store.hit_rate("processor") is None


def test_unpredicted_flips_do_not_count_as_hits(store: EvidenceStore) -> None:
    """"tasks flipped / predicted" (Figure 12c p.43): the denominator is the prediction."""
    ship = _ship("C-R1-01", 1, levers=["prompt"], predicted=["a", "b"], realized=["a", "lucky"])
    assert ship.hits == ["a"]
    assert ship.hit_rate == 0.5


def test_hit_rate_is_pooled_across_ships_not_averaged(store: EvidenceStore) -> None:
    """Ours: sum the numerators and denominators, so a big ship weighs more."""
    store.append_ship(_ship("C-R1-01", 1, levers=["tools"], predicted=list("abcd"), realized=[]))
    store.append_ship(_ship("C-R2-01", 2, levers=["tools"], predicted=["e"], realized=["e"]))

    assert store.hit_rate("tools") == pytest.approx(1 / 5)  # pooled
    # the average of the per-ship rates would be 0.5, which is the number we reject
    rates = [ship.hit_rate for ship in store.iter_ships()]
    assert sum(rates) / len(rates) == pytest.approx(0.5)


def test_an_empty_prediction_leaves_the_hit_rate_undefined(store: EvidenceStore) -> None:
    """Denominator 0 is ``None``, never 0.0 — nothing was claimed, so nothing failed."""
    ship = _ship("C-R1-01", 1, levers=["prompt"], predicted=[], realized=["a"])
    assert ship.hit_rate is None

    store.append_ship(ship)
    assert store.hit_rate() is None
    assert store.hit_rate("prompt") is None


def test_hit_rate_filters(store: EvidenceStore) -> None:
    store.append_ship(_ship("C-R1-01", 1, levers=["prompt"], predicted=["a", "b"], realized=["a"]))
    store.append_ship(
        _ship("C-R5-01", 5, levers=["tools"], predicted=["c"], realized=["c"], variant_id="V1")
    )

    assert store.hit_rate() == pytest.approx(2 / 3)
    assert store.hit_rate(since_round=5) == 1.0
    assert store.hit_rate(variant_id="V1") == 1.0
    assert store.hit_rate("prompt") == 0.5


# ---------------------------------------------------------------------------
# Lever ban (Critic portfolio audit rule 1)
# ---------------------------------------------------------------------------


def test_the_ban_thresholds_match_the_paper(store: EvidenceStore) -> None:
    assert (LEVER_BAN_WINDOW, LEVER_BAN_MIN_SHIPS, LEVER_BAN_HIT_RATE) == (3, 2, 0.4)


def test_a_repeatedly_ineffective_lever_is_banned(store: EvidenceStore) -> None:
    """>=2 ships in the last 3 rounds and a cumulative hit-rate below 0.4."""
    store.append_ship(_ship("C-R7-01", 7, levers=["prompt"], predicted=list("abcde"), realized=["a"]))
    store.append_ship(_ship("C-R8-01", 8, levers=["prompt"], predicted=list("fghij"), realized=["f"]))

    assert store.hit_rate("prompt") == pytest.approx(0.2)
    assert store.lever_is_banned("prompt", round_idx=9) is True


def test_one_recent_ship_is_not_enough_to_ban(store: EvidenceStore) -> None:
    store.append_ship(_ship("C-R1-01", 1, levers=["prompt"], predicted=list("abcde"), realized=[]))
    store.append_ship(_ship("C-R8-01", 8, levers=["prompt"], predicted=list("fghij"), realized=[]))

    assert store.hit_rate("prompt") == 0.0
    assert store.lever_is_banned("prompt", round_idx=9) is False  # only R8 is inside the window


def test_an_effective_lever_survives_repeated_use(store: EvidenceStore) -> None:
    store.append_ship(_ship("C-R7-01", 7, levers=["tools"], predicted=list("abcde"), realized=list("abc")))
    store.append_ship(_ship("C-R8-01", 8, levers=["tools"], predicted=list("fg"), realized=["f"]))

    assert store.hit_rate("tools") == pytest.approx(4 / 7)
    assert store.lever_is_banned("tools", round_idx=9) is False


def test_a_lever_with_no_prediction_is_never_banned(store: EvidenceStore) -> None:
    """Denominator 0: an undefined rate is not evidence of ineffectiveness."""
    store.append_ship(_ship("C-R7-01", 7, levers=["config"], predicted=[], realized=[]))
    store.append_ship(_ship("C-R8-01", 8, levers=["config"], predicted=[], realized=[]))

    assert store.hit_rate("config") is None
    assert store.lever_is_banned("config", round_idx=9) is False


def test_the_current_round_is_outside_the_recency_window(store: EvidenceStore) -> None:
    store.append_ship(_ship("C-R8-01", 8, levers=["prompt"], predicted=["a"], realized=[]))
    store.append_ship(_ship("C-R9-01", 9, levers=["prompt"], predicted=["b"], realized=[]))

    assert store.lever_ship_rounds("prompt") == [8, 9]
    assert store.lever_is_banned("prompt", round_idx=9) is False  # only R8 counts as prior
    assert store.lever_is_banned("prompt", round_idx=10) is True


def test_window_is_validated(store: EvidenceStore) -> None:
    with pytest.raises(ValueError):
        store.lever_is_banned("prompt", round_idx=3, window=0)


# ---------------------------------------------------------------------------
# manifest -> ship outcome
# ---------------------------------------------------------------------------


def test_a_shipped_manifest_becomes_a_scorable_outcome(store: EvidenceStore) -> None:
    manifest = ChangeManifest.model_validate(
        {
            "candidate_id": "C-R10-02",
            "bucket": ["tools", "prompt", "config"],
            "predicted_impact": {
                "tasks_will_unlock": ["db4fd70a", "f0f46385"],
                "tasks_will_stabilize": ["4b6bb5f7"],
                "tasks_at_risk": ["risky"],
            },
            "target_variant": "V1",
        }
    )
    outcome = ship_outcome_from_manifest(
        manifest, round_idx=10, realized_flips=["db4fd70a"], attribution_satisfied=True
    )

    assert outcome.variant_id == "V1"
    assert outcome.levers == ["tools", "prompt", "config"]
    assert outcome.predicted_flips == ["db4fd70a", "f0f46385", "4b6bb5f7"]
    assert outcome.predicted_at_risk == ["risky"]  # a risk disclosure is not a claimed flip
    assert outcome.hit_rate == pytest.approx(1 / 3)
    assert outcome.attribution_satisfied is True

    store.append_ship(outcome)
    assert store.ship_outcomes()[0]["hit_rate"] == pytest.approx(1 / 3)


def test_ship_outcome_dict_round_trip(store: EvidenceStore) -> None:
    ship = _ship("C-R1-01", 1, levers=["prompt"], predicted=["a", "b"], realized=["a"])
    ship.realized_regressions = ["c"]
    store.append_ship(ship)
    assert list(store.iter_ships()) == [ship]


# ---------------------------------------------------------------------------
# v6 — recorded ship-node touches reach the Planner via cross_round_history
# ---------------------------------------------------------------------------


def test_cross_round_history_surfaces_recorded_ship_nodes(store: EvidenceStore) -> None:
    store.append_digest(_digest("t", 0))
    store.append_digest(_digest("t", 1))
    ship = _ship("C-R0-01", 0, levers=["config"], predicted=["t"], realized=[])
    ship.graph_nodes_touched = ["proc:cost_guard_processor"]
    store.append_ship(ship)

    entry = store.cross_round_history("t", "V0")[0]
    assert entry["round_idx"] == 0
    assert entry["ship_nodes"] == ["proc:cost_guard_processor"]


def test_cross_round_history_omits_ship_nodes_when_unrecorded(store: EvidenceStore) -> None:
    store.append_digest(_digest("t", 0))
    # graph_nodes_touched left None (recording did not run): the key is ABSENT,
    # which the Planner reads as "unknown", never as "touched nothing".
    ship = _ship("C-R0-01", 0, levers=["config"], predicted=["t"], realized=[])
    store.append_ship(ship)

    entry = store.cross_round_history("t", "V0")[0]
    assert "ship_nodes" not in entry


def test_recorded_but_empty_ship_nodes_is_a_fact_not_unknown(store: EvidenceStore) -> None:
    store.append_digest(_digest("t", 0))
    # a prompt-text-only ship: recording ran, touched no graph node -> key present
    # with an empty list, which is "recorded, never edited" not "unknown".
    ship = _ship("C-R0-01", 0, levers=["prompt"], predicted=["t"], realized=[])
    ship.graph_nodes_touched = []
    store.append_ship(ship)

    entry = store.cross_round_history("t", "V0")[0]
    assert entry["ship_nodes"] == []
