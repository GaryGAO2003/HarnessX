# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""Ship recording — the recipe writes ``ship_outcomes.json`` when a candidate ships.

``EvidenceStore.append_ship`` / ``ship_outcome_from_manifest`` existed and were
unit-tested, but nothing in ``recipe/`` ever called them, so the Critic's
banned-lever rule never fired, the Planner's reputation signal was empty, and the
graph node-edit history was permanently "unknown". These tests pin the wiring:
``VariantPoolRecipe._record_round_ships`` records each APPLY/FORK ship at ship
time (unrealised), ``_realize_prior_ships`` scores it one round later, and the
real :class:`DeterministicCritic` then bans a repeatedly ineffective lever.

The two recording methods are exercised through a thin ``_Runner`` shim that
borrows them unbound from the real recipe (the pattern used by
``test_record_gate_complement``), so the tests never stand up a full run.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from experiments.variant_pool.critic import CriticContext, DeterministicCritic
from experiments.variant_pool.evidence import EvidenceStore, ShipOutcome
from experiments.variant_pool.gate import Decision
from experiments.variant_pool.manifest import CandidateArtifact, ChangeManifest


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


def _artifact(
    root: Path,
    candidate_id: str,
    *,
    target: str = "V0",
    bucket: tuple[str, ...] = ("prompt",),
    unlock: tuple[str, ...] = ("new-task",),
    at_risk: tuple[str, ...] = (),
    graph_edits: tuple[object, ...] | None = None,
) -> CandidateArtifact:
    output_dir = root / "artifacts" / candidate_id
    output_dir.mkdir(parents=True, exist_ok=True)
    config = output_dir / "config.yaml"
    config.write_text(f"candidate: {candidate_id}\n", encoding="utf-8")
    code = bool({"tools", "processor"} & set(bucket))
    manifest = ChangeManifest.model_validate(
        {
            "candidate_id": candidate_id,
            "bucket": list(bucket),
            "capability_evidence": (
                [{"type": "other", "claim": "Level 2 roundtrip", "evidence": "survived"}] if code else []
            ),
            "file_changes": [
                {
                    "path": f"harness/{candidate_id}.txt",
                    "action": "modify",
                    "diff_summary": "candidate mutation",
                }
            ],
            "predicted_impact": {
                "tasks_will_unlock": list(unlock),
                "tasks_at_risk": list(at_risk),
            },
            "attribution_signature": (
                {"type": "tool_call", "tool_name": "Lookup", "expected_min_calls": 1} if code else None
            ),
            "target_variant": target,
        }
    )
    return CandidateArtifact(
        config_path=config,
        manifest=manifest,
        target_variant=target,
        graph_edits=graph_edits,
    )


class _FakeEdit:
    """Duck-typed graph edit: only ``affected_node_ids()`` is read by the recipe."""

    def __init__(self, nodes: list[str]) -> None:
        self._nodes = tuple(nodes)

    def affected_node_ids(self) -> tuple[str, ...]:
        return self._nodes


class _Result:
    """Just the two attributes ``_record_round_ships`` reads off a RoundResult."""

    def __init__(self, decisions: dict[str, Decision], selected: dict[str, str]) -> None:
        self.decisions = dict(decisions)
        self.selected_candidate_ids = dict(selected)


class _Runner:
    """Enough surface to exercise the recording methods unbound from the runner."""

    from recipe.gaia_evolver.run_variant_pool import (  # noqa: E402
        VariantPoolRecipe as _Real,
    )

    _record_round_ships = _Real._record_round_ships
    _realize_prior_ships = _Real._realize_prior_ships

    def __init__(self, evidence, *, candidates=None, active_pass=None) -> None:
        self.evidence = evidence
        self._round_candidates = dict(candidates or {})
        self._active_round_pass = dict(active_pass or {})


def _realised(candidate_id, round_idx, levers, predicted, realized) -> ShipOutcome:
    return ShipOutcome(
        candidate_id=candidate_id,
        round_idx=round_idx,
        variant_id="V0",
        levers=list(levers),
        predicted_flips=list(predicted),
        realized_flips=list(realized),
    )


# ---------------------------------------------------------------------------
# 1 — a shipped candidate becomes a ShipOutcome
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("decision", [Decision.APPLY, Decision.FORK])
def test_a_shipped_candidate_is_recorded_with_its_manifest_flips_and_levers(tmp_path: Path, decision: Decision) -> None:
    store = EvidenceStore(tmp_path / "run")
    candidate = _artifact(tmp_path, "C-R1-01", bucket=("prompt",), unlock=("t1", "t2"))
    runner = _Runner(store, candidates={"C-R1-01": candidate})

    runner._record_round_ships(_Result({"V0": decision}, {"V0": "C-R1-01"}), 1)

    ships = list(store.iter_ships())
    assert len(ships) == 1
    ship = ships[0]
    assert ship.candidate_id == "C-R1-01"
    assert ship.round_idx == 1
    assert ship.variant_id == "V0"
    assert ship.levers == ["prompt"]
    assert ship.predicted_flips == ["t1", "t2"]
    # ship time only knows the prediction, not the outcome.
    assert ship.realized is False


def test_rejected_and_opaque_candidates_do_not_ship(tmp_path: Path) -> None:
    store = EvidenceStore(tmp_path / "run")
    candidate = _artifact(tmp_path, "C-R1-01", unlock=("t1",))
    runner = _Runner(store, candidates={"C-R1-01": candidate})

    # REJECT never ships; a selected id with no manifest (opaque C1 candidate) is
    # skipped rather than crashing.
    runner._record_round_ships(
        _Result({"V0": Decision.REJECT, "V1": Decision.APPLY}, {"V0": "C-R1-01", "V1": "missing"}),
        1,
    )
    assert list(store.iter_ships()) == []


# ---------------------------------------------------------------------------
# 2 — idempotence: a re-run round does not double-record
# ---------------------------------------------------------------------------


def test_re_running_the_same_round_does_not_double_record(tmp_path: Path) -> None:
    store = EvidenceStore(tmp_path / "run")
    candidate = _artifact(tmp_path, "C-R1-01", unlock=("t1",))
    runner = _Runner(store, candidates={"C-R1-01": candidate})
    result = _Result({"V0": Decision.APPLY}, {"V0": "C-R1-01"})

    runner._record_round_ships(result, 1)
    runner._record_round_ships(result, 1)  # resume / replay of the same round

    assert [ship.candidate_id for ship in store.iter_ships()] == ["C-R1-01"]


# ---------------------------------------------------------------------------
# 3 — the pre-realisation window carries no rate (not a false zero)
# ---------------------------------------------------------------------------


def test_hit_rate_is_undefined_until_the_ship_is_realised(tmp_path: Path) -> None:
    """A ship recorded at ship time predicts flips but has realised nothing yet.

    Its ``realized_flips`` are empty because the following round has not run, not
    because the lever failed — so the rate is ``None`` (undefined), never 0.0.
    Recording the ship as though it were realised (the mutation) turns this into
    a real zero and breaks the assertions below.
    """
    store = EvidenceStore(tmp_path / "run")
    candidate = _artifact(tmp_path, "C-R1-01", bucket=("tools",), unlock=("t1", "t2"))
    runner = _Runner(store, candidates={"C-R1-01": candidate})

    runner._record_round_ships(_Result({"V0": Decision.APPLY}, {"V0": "C-R1-01"}), 1)

    ship = next(iter(store.iter_ships()))
    assert ship.realized is False
    assert ship.predicted_flips == ["t1", "t2"]
    assert ship.hit_rate is None
    assert store.hit_rate("tools") is None
    assert store.hit_rate() is None


def test_realising_a_prior_ship_scores_its_flips_against_the_next_round(tmp_path: Path) -> None:
    store = EvidenceStore(tmp_path / "run")
    candidate = _artifact(tmp_path, "C-R1-01", bucket=("tools",), unlock=("t1", "t2"), at_risk=("t3",))
    runner = _Runner(store, candidates={"C-R1-01": candidate})
    runner._record_round_ships(_Result({"V0": Decision.APPLY}, {"V0": "C-R1-01"}), 1)

    # Round 2's settled pass: t1 flipped (solved), t2 did not, t3 (at risk) failed.
    runner._active_round_pass = {"V0": {"t1": (1, 2), "t2": (0, 2), "t3": (0, 2)}}
    runner._realize_prior_ships(2)

    ship = next(iter(store.iter_ships()))
    assert ship.realized is True
    assert ship.realized_flips == ["t1"]
    assert ship.realized_regressions == ["t3"]
    assert ship.hit_rate == pytest.approx(0.5)
    # a second realise pass changes nothing (idempotent).
    runner._active_round_pass = {"V0": {"t1": (0, 2), "t2": (0, 2), "t3": (2, 2)}}
    runner._realize_prior_ships(2)
    assert next(iter(store.iter_ships())).realized_flips == ["t1"]


# ---------------------------------------------------------------------------
# 4 — the banned-lever rule comes back to life (store level)
# ---------------------------------------------------------------------------


def test_realised_history_bans_a_repeatedly_weak_lever(tmp_path: Path) -> None:
    """>=2 ships in the last 3 rounds and cumulative hit_rate < 0.4 -> banned."""
    store = EvidenceStore(tmp_path / "run")
    store.append_ship(_realised("C-R7-01", 7, ["tools"], list("abcde"), ["a"]))
    store.append_ship(_realised("C-R8-01", 8, ["tools"], list("fghij"), ["f"]))
    # an effective lever used just as often survives.
    store.append_ship(_realised("C-R7-02", 7, ["prompt"], list("kl"), ["k", "l"]))
    store.append_ship(_realised("C-R8-02", 8, ["prompt"], ["m"], ["m"]))

    assert store.hit_rate("tools") == pytest.approx(0.2)
    assert store.hit_rate("prompt") == pytest.approx(1.0)
    assert store.lever_is_banned("tools", round_idx=9) is True
    assert store.lever_is_banned("prompt", round_idx=9) is False


# ---------------------------------------------------------------------------
# 5 — end-to-end: the recipe's ships drive a real Critic rejection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_recipe_ships_lead_the_critic_to_reject_a_banned_lever(tmp_path: Path) -> None:
    store = EvidenceStore(tmp_path / "run")

    # R1: ship a tools candidate; realise it against R2 where its flip never lands.
    r1 = _artifact(tmp_path, "C-R1-01", bucket=("tools",), unlock=("t1",))
    runner = _Runner(store, candidates={"C-R1-01": r1})
    runner._record_round_ships(_Result({"V0": Decision.APPLY}, {"V0": "C-R1-01"}), 1)
    runner._active_round_pass = {"V0": {"t1": (0, 2)}}
    runner._realize_prior_ships(2)

    # R2: ship a second tools candidate (still unrealised at R3's Critic time).
    r2 = _artifact(tmp_path, "C-R2-01", bucket=("tools",), unlock=("t2",))
    runner._round_candidates = {"C-R2-01": r2}
    runner._record_round_ships(_Result({"V0": Decision.APPLY}, {"V0": "C-R2-01"}), 2)

    # R3: tools shipped in 2 of the last 3 rounds and its realised hit_rate is 0.0.
    critic = DeterministicCritic(store)
    tools = _artifact(tmp_path, "C-R3-01", bucket=("tools",), unlock=("t9",))
    prompt = _artifact(tmp_path, "C-R3-02", bucket=("prompt",), unlock=("t10",))

    review = await critic.review(
        context=CriticContext(round_idx=3, target_variant="V0"),
        candidates=[tools, prompt],
    )

    assert review.rejections[0].candidate_id == "C-R3-01"
    assert "banned lever" in review.rejections[0].reason
    assert review.ranked_candidate_ids == ("C-R3-02",)
    assert any(
        "'tools'" in concern and "banned" in concern and "hit_rate=0.000<0.4" in concern
        for concern in review.strategy_concerns
    )


# ---------------------------------------------------------------------------
# 6 — graph_nodes_touched: None (off) vs the touched node ids (on)
# ---------------------------------------------------------------------------


def test_graph_nodes_touched_is_none_off_and_carries_ids_on(tmp_path: Path) -> None:
    store = EvidenceStore(tmp_path / "run")
    # graph edits OFF (default): the candidate carries none -> recorded-as-unknown.
    plain = _artifact(tmp_path, "C-R1-01", bucket=("tools",), unlock=("t1",))
    # graph edits ON: the candidate carries the touched node ids.
    edited = _artifact(
        tmp_path,
        "C-R1-02",
        target="V1",
        bucket=("tools",),
        unlock=("t2",),
        graph_edits=(_FakeEdit(["tool:Lookup", "proc:cost_guard_processor"]),),
    )
    # graph edits ON but nothing touched: recorded, empty -> a fact, not unknown.
    empty = _artifact(tmp_path, "C-R1-03", target="V2", bucket=("prompt",), unlock=("t3",), graph_edits=())
    runner = _Runner(
        store,
        candidates={"C-R1-01": plain, "C-R1-02": edited, "C-R1-03": empty},
    )

    runner._record_round_ships(
        _Result(
            {"V0": Decision.APPLY, "V1": Decision.FORK, "V2": Decision.APPLY},
            {"V0": "C-R1-01", "V1": "C-R1-02", "V2": "C-R1-03"},
        ),
        1,
    )

    by_id = {ship.candidate_id: ship for ship in store.iter_ships()}
    assert by_id["C-R1-01"].graph_nodes_touched is None
    assert by_id["C-R1-02"].graph_nodes_touched == ["proc:cost_guard_processor", "tool:Lookup"]
    assert by_id["C-R1-03"].graph_nodes_touched == []
