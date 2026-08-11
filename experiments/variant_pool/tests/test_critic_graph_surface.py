# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""v6 M8 — the Critic's overlap surface, made graph-shaped.

The three deterministic portfolio rules are unchanged and covered elsewhere
(``test_candidate_pipeline`` / ``test_regression_accountability``). This suite
covers only what ``mutation_surface`` / ``overlapping_candidates`` are computed
*over*: an exact graph surface when a candidate carries graph edits, the
path-string surface otherwise, and an honest record of which one ran.

The no-ship contract (kill, never ship) is asserted directly here too — it is
the module's whole reason for existing, so a test that never checks it is a
gate nobody watches fail.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from experiments.variant_pool.critic import (
    CandidateVerdict,
    CriticContext,
    CriticReview,
    DeterministicCritic,
)
from experiments.variant_pool.evidence import EvidenceStore
from experiments.variant_pool.manifest import CandidateArtifact, ChangeManifest
from harnessx.graph.edit import GraphEdit, GraphEditType


# ---------------------------------------------------------------------------
# builders
# ---------------------------------------------------------------------------


def _node_edit(node_id: str) -> GraphEdit:
    """A phenotype-safe param mutation whose sole touched element is ``node_id``."""
    return GraphEdit(edit_type=GraphEditType.MUTATE_INACTIVE, target_node_id=node_id)


def _candidate(
    candidate_id: str,
    *,
    paths: tuple[str, ...],
    edits: tuple[GraphEdit, ...] | None = None,
    unlock: tuple[str, ...] = ("t1",),
    target: str = "V0",
) -> CandidateArtifact:
    manifest = ChangeManifest.model_validate(
        {
            "candidate_id": candidate_id,
            "bucket": ["config"],
            "file_changes": [{"path": p, "action": "modify", "diff_summary": "x"} for p in paths],
            "predicted_impact": {"tasks_will_unlock": list(unlock)},
            "target_variant": target,
        }
    )
    return CandidateArtifact(
        config_path=Path(f"/nonexistent/{candidate_id}.yaml"),
        manifest=manifest,
        target_variant=target,
        graph_edits=edits,
    )


def _review(tmp_path: Path, candidates: list[CandidateArtifact]) -> CriticReview:
    critic = DeterministicCritic(EvidenceStore(tmp_path / "run"))
    return asyncio.run(
        critic.review(
            context=CriticContext(round_idx=1, target_variant="V0"),
            candidates=candidates,
        )
    )


def _by_id(review: CriticReview) -> dict[str, CandidateVerdict]:
    return {v.candidate_id: v for v in review.verdicts}


# ---------------------------------------------------------------------------
# (2) precision gain — same file, different nodes: path overlaps, graph does not
# ---------------------------------------------------------------------------


def test_path_surface_overlaps_where_graph_surface_does_not(tmp_path: Path) -> None:
    same_file = ("variants/k.yaml",)

    # Path surface: both edit the same file -> reported as overlapping.
    path_review = _review(
        tmp_path,
        [
            _candidate("C-R1-01", paths=same_file),
            _candidate("C-R1-02", paths=same_file),
        ],
    )
    path_v = _by_id(path_review)
    assert path_v["C-R1-01"].surface_kind == "path"
    assert path_v["C-R1-01"].overlapping_candidates == ("C-R1-02",)
    assert path_v["C-R1-02"].overlapping_candidates == ("C-R1-01",)

    # Graph surface: same file, but disjoint nodes -> NOT overlapping.
    graph_review = _review(
        tmp_path,
        [
            _candidate("C-R1-01", paths=same_file, edits=(_node_edit("proc:foo"),)),
            _candidate("C-R1-02", paths=same_file, edits=(_node_edit("proc:bar"),)),
        ],
    )
    graph_v = _by_id(graph_review)
    assert graph_v["C-R1-01"].surface_kind == "graph"
    assert graph_v["C-R1-01"].mutation_surface == ("proc:foo",)
    assert graph_v["C-R1-02"].mutation_surface == ("proc:bar",)
    assert graph_v["C-R1-01"].overlapping_candidates == ()
    assert graph_v["C-R1-02"].overlapping_candidates == ()


# ---------------------------------------------------------------------------
# (3) true overlap still caught — same node genuinely overlaps
# ---------------------------------------------------------------------------


def test_graph_surface_still_catches_a_shared_node(tmp_path: Path) -> None:
    review = _review(
        tmp_path,
        [
            _candidate(
                "C-R1-01",
                paths=("variants/a.yaml",),
                edits=(_node_edit("proc:foo"), _node_edit("proc:only-a")),
            ),
            _candidate(
                "C-R1-02",
                paths=("variants/b.yaml",),
                edits=(_node_edit("proc:foo"), _node_edit("proc:only-b")),
            ),
        ],
    )
    by_id = _by_id(review)
    # Different files (path surface would call them disjoint), but they share
    # proc:foo, so the graph surface reports a real overlap.
    assert by_id["C-R1-01"].surface_kind == "graph"
    assert by_id["C-R1-01"].overlapping_candidates == ("C-R1-02",)
    assert by_id["C-R1-02"].overlapping_candidates == ("C-R1-01",)


def test_graph_surface_catches_a_shared_dependency_edge(tmp_path: Path) -> None:
    shared_edge = GraphEdit(
        edit_type=GraphEditType.CHANGE_DEPENDENCY,
        edge_source_id="proc:a",
        edge_target_id="proc:b",
        edge_type=None,
        add_edge=True,
    )
    review = _review(
        tmp_path,
        [
            _candidate("C-R1-01", paths=("x.yaml",), edits=(shared_edge,)),
            _candidate("C-R1-02", paths=("y.yaml",), edits=(shared_edge,)),
        ],
    )
    by_id = _by_id(review)
    assert "proc:a→proc:b" in by_id["C-R1-01"].mutation_surface
    assert by_id["C-R1-01"].overlapping_candidates == ("C-R1-02",)


# ---------------------------------------------------------------------------
# (4) fallback records the path surface, and why
# ---------------------------------------------------------------------------


def test_fallback_records_the_path_surface_and_why(tmp_path: Path) -> None:
    review = _review(tmp_path, [_candidate("C-R1-01", paths=("variants/k.yaml",))])
    verdict = _by_id(review)["C-R1-01"]
    assert verdict.surface_kind == "path"
    assert verdict.mutation_surface == ("variants/k.yaml",)
    assert "path surface" in verdict.surface_provenance
    assert "no graph-edit information" in verdict.surface_provenance


# ---------------------------------------------------------------------------
# (5) ranking follows the touched-element count, not the file-change count
# ---------------------------------------------------------------------------


def test_ranking_orders_by_touched_element_count(tmp_path: Path) -> None:
    # Equal coverage, one file_change each (so the path surface_size is a tie and
    # the id tiebreak would put C-R1-01 first). The graph counts differ: C-R1-02
    # touches one node, C-R1-01 touches three -> the smaller graph surface wins.
    review = _review(
        tmp_path,
        [
            _candidate(
                "C-R1-01",
                paths=("shared.yaml",),
                edits=(
                    _node_edit("proc:a"),
                    _node_edit("proc:b"),
                    _node_edit("proc:c"),
                ),
            ),
            _candidate(
                "C-R1-02",
                paths=("shared.yaml",),
                edits=(_node_edit("proc:z"),),
            ),
        ],
    )
    # File-change count is 1==1; only the touched-element count (1 < 3) can flip
    # the lexical id order, so this proves ranking used the graph surface.
    assert review.ranked_candidate_ids == ("C-R1-02", "C-R1-01")


# ---------------------------------------------------------------------------
# (6) the no-ship contract, asserted directly
# ---------------------------------------------------------------------------


def test_no_ship_contract(tmp_path: Path) -> None:
    review = _review(tmp_path, [_candidate("C-R1-01", paths=("k.yaml",))])

    # The review — and every verdict — carries no shipping decision.
    assert not hasattr(review, "approved")
    for verdict in review.verdicts:
        assert not hasattr(verdict, "approved")

    # Ranking never authorizes shipping: the deterministic gate always runs.
    assert review.requires_deterministic_gate is True

    # The Critic has no approve/apply API and no gate dependency.
    assert not hasattr(DeterministicCritic, "approve")
    assert not hasattr(DeterministicCritic, "apply")
    import experiments.variant_pool.critic as critic_mod

    assert not hasattr(critic_mod, "gate")
    assert not hasattr(critic_mod, "run_gate")

    # A no-op review cannot also rank candidates.
    with pytest.raises(ValueError):
        CriticReview(no_op=True, ranked_candidate_ids=("C-R1-01",))
