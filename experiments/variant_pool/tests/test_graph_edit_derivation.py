# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""v6 — the Evolver's config change becomes exact graph edits (M8 surface + M7 handle).

The Evolver already writes a new config file and the parent config is known, so a
candidate's graph edits are DERIVED (``to_graph`` of both configs, then
``diff_graphs``) rather than guessed from prose. This suite pins:

* the derived edits equal the real parent->child graph delta, and their node ids
  are the ones ``assign_processor_node_ids`` mints (not a parallel scheme);
* the flag is default OFF — ``graph_edits`` stays ``None`` and no audit key is
  added, byte-identical to today;
* the per-candidate provenance says WHICH path ran and WHY (the 4e0810f rule);
* a populated ``graph_edits`` makes the Critic's overlap surface graph-shaped
  (reusing M8's existing surface behaviour);
* a Digester-named node id survives into the Planner's input, and a
  Planner-named node survives into the brief the Evolver receives.

Fully offline: no provider, no network. Configs are built with the real builder
and round-tripped through YAML on disk, so ``to_graph`` runs for real.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from recipe.gaia_evolver import run_variant_pool as rvp  # noqa: E402
from experiments.variant_pool.candidate_pipeline import (  # noqa: E402
    CandidateBrief,
    PipelineContext,
)
from experiments.variant_pool.critic import CriticContext, DeterministicCritic  # noqa: E402
from experiments.variant_pool.evidence import EvidenceStore, TaskDigest  # noqa: E402
from experiments.variant_pool.graph_manifest import derive_graph_edits  # noqa: E402
from experiments.variant_pool.manifest import CandidateArtifact, ChangeManifest  # noqa: E402
from harnessx.bundles import context as context_bundle  # noqa: E402
from harnessx.core.builder import HarnessBuilder  # noqa: E402
from harnessx.core.harness import HarnessConfig  # noqa: E402
from harnessx.graph.edit import diff_graphs  # noqa: E402
from harnessx.graph.snapshot import assign_processor_node_ids, to_graph  # noqa: E402


# ---------------------------------------------------------------------------
# builders
# ---------------------------------------------------------------------------


def _config_pair(tmp_path: Path):
    """Write parent.yaml + a child.yaml that retunes ONE processor's ctor kwarg.

    Returns ``(parent_path, child_path)``. The child adds a single ctor kwarg to
    the first dict processor — a genuine config change that ``to_graph`` records
    as a node-metadata delta (``_ctor_kwargs_``), so the diff is exactly one
    node-targeted ``MUTATE_INACTIVE`` edit.
    """
    base = (HarnessBuilder() | context_bundle).build()
    parent_path = tmp_path / "parent.yaml"
    base.to_yaml_file(parent_path)

    parent_cfg = HarnessConfig.from_yaml_file(parent_path).canonicalize()
    retuned = False
    new_procs = []
    for proc in parent_cfg.processors:
        if isinstance(proc, dict) and not retuned:
            proc = {**proc, "ghx_test_param": 4242}
            retuned = True
        new_procs.append(proc)
    assert retuned, "context bundle must contribute at least one dict processor"

    child_cfg = parent_cfg.copy(processors=new_procs)
    child_path = tmp_path / "child.yaml"
    child_cfg.to_yaml_file(child_path)
    return parent_path, child_path


def _candidate(candidate_id: str, *, edits, target: str = "V0") -> CandidateArtifact:
    manifest = ChangeManifest.model_validate(
        {
            "candidate_id": candidate_id,
            "bucket": ["config"],
            "file_changes": [{"path": "variants/k.yaml", "action": "modify", "diff_summary": "x"}],
            "predicted_impact": {"tasks_will_unlock": ["t1"]},
            "target_variant": target,
        }
    )
    return CandidateArtifact(
        config_path=Path(f"/nonexistent/{candidate_id}.yaml"),
        manifest=manifest,
        target_variant=target,
        graph_edits=edits,
    )


def _ctx(tmp_path: Path) -> PipelineContext:
    return PipelineContext(
        round_idx=1,
        target_variant="V0",
        current_config_path=tmp_path / "current.yaml",
        trajectories_dir=tmp_path,
        output_root=tmp_path,
    )


# ---------------------------------------------------------------------------
# (1) the derived edits ARE the graph delta
# ---------------------------------------------------------------------------


def test_derived_graph_edits_match_the_config_delta(tmp_path: Path) -> None:
    parent_path, child_path = _config_pair(tmp_path)

    d = derive_graph_edits(parent_path, child_path)
    assert d.source == "graph"
    assert d.edits, "a real config change must imply a real graph delta"

    before = HarnessConfig.from_yaml_file(parent_path).canonicalize()
    after = HarnessConfig.from_yaml_file(child_path).canonicalize()
    expected = {e.target_node_id for e in diff_graphs(to_graph(before), to_graph(after)) if e.target_node_id}

    got = {e.target_node_id for e in d.edits if e.target_node_id}
    assert got, "the retune must produce a node-targeted edit"
    assert got == expected


# ---------------------------------------------------------------------------
# (2) the node ids come from the assignment authority, not a parallel scheme
# ---------------------------------------------------------------------------


def test_derived_node_ids_come_from_the_assignment_authority(tmp_path: Path) -> None:
    parent_path, child_path = _config_pair(tmp_path)
    child_cfg = HarnessConfig.from_yaml_file(child_path).canonicalize()

    authority = {node_id for _, node_id in assign_processor_node_ids(child_cfg).persistent}
    got = {e.target_node_id for e in derive_graph_edits(parent_path, child_path).edits if e.target_node_id}

    assert got
    assert got <= authority
    assert all(node_id.startswith("proc:") for node_id in got)


# ---------------------------------------------------------------------------
# (3) flag OFF (default): no graph edits, no audit key, byte-identical
# ---------------------------------------------------------------------------


def test_flag_off_yields_no_graph_edits(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HARNESSX_GHX_EVOLVER_GRAPH_EDITS", raising=False)
    parent_path, child_path = _config_pair(tmp_path)

    assert rvp._evolver_graph_edits_enabled() is False
    edits, provenance = rvp._evolver_graph_edits(parent_path, child_path)
    assert edits is None
    assert provenance == {}


# ---------------------------------------------------------------------------
# (4) provenance records which path ran, with a reason when it did not
# ---------------------------------------------------------------------------


def test_provenance_records_the_graph_path_when_flag_on(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HARNESSX_GHX_EVOLVER_GRAPH_EDITS", "1")
    parent_path, child_path = _config_pair(tmp_path)

    edits, provenance = rvp._evolver_graph_edits(parent_path, child_path)
    assert edits is not None
    assert provenance["graph_edits_source"] == "graph"
    assert "config diff" in provenance["graph_edits_reason"]
    assert provenance["graph_edits_count"] == len(edits)


def test_provenance_records_unavailable_with_a_reason(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HARNESSX_GHX_EVOLVER_GRAPH_EDITS", "1")
    edits, provenance = rvp._evolver_graph_edits(tmp_path / "missing_parent.yaml", tmp_path / "missing_child.yaml")
    assert edits is None
    assert provenance["graph_edits_source"] == "unavailable"
    assert provenance["graph_edits_reason"].strip()
    assert provenance["graph_edits_count"] == 0


# ---------------------------------------------------------------------------
# (5) end-to-end: populated graph_edits => graph-shaped Critic surface
# ---------------------------------------------------------------------------


def test_populated_graph_edits_make_the_critic_surface_graph_shaped(tmp_path: Path) -> None:
    parent_path, child_path = _config_pair(tmp_path)
    d = derive_graph_edits(parent_path, child_path)
    assert d.edits

    critic = DeterministicCritic(EvidenceStore(tmp_path / "run"))
    review = asyncio.run(
        critic.review(
            context=CriticContext(round_idx=1, target_variant="V0"),
            candidates=[_candidate("C-R1-01", edits=d.edits)],
        )
    )
    verdict = review.verdicts[0]
    assert verdict.surface_kind == "graph"

    node_ids = {e.target_node_id for e in d.edits if e.target_node_id}
    assert node_ids
    assert node_ids <= set(verdict.mutation_surface)


def test_no_graph_edits_keeps_the_path_surface(tmp_path: Path) -> None:
    critic = DeterministicCritic(EvidenceStore(tmp_path / "run"))
    review = asyncio.run(
        critic.review(
            context=CriticContext(round_idx=1, target_variant="V0"),
            candidates=[_candidate("C-R1-01", edits=None)],
        )
    )
    assert review.verdicts[0].surface_kind == "path"


# ---------------------------------------------------------------------------
# (6a) a Digester-named node id survives into the Planner's input
# ---------------------------------------------------------------------------


def test_digester_node_id_survives_into_planner_input(tmp_path: Path) -> None:
    planner = rvp._LLMPlanner(provider=object(), k_t=2, fallback=rvp._DeterministicPlanner(k_t=2))
    named = TaskDigest(
        task_id="t1",
        round_idx=1,
        variant_id="V0",
        outcome=(0, 2),
        failure_category="tool_output_dropped",
        implicated_nodes=["proc:cost_guard_processor"],
    )
    body = planner._compose_input(_ctx(tmp_path), (named,), max_anchors=None)
    assert "proc:cost_guard_processor" in body
    assert "nodes=[" in body


def test_text_path_digest_adds_no_node_line_to_planner_input(tmp_path: Path) -> None:
    planner = rvp._LLMPlanner(provider=object(), k_t=2, fallback=rvp._DeterministicPlanner(k_t=2))
    plain = TaskDigest(task_id="t2", round_idx=1, variant_id="V0", outcome=(0, 2))
    body = planner._compose_input(_ctx(tmp_path), (plain,), max_anchors=None)
    assert "nodes=[" not in body


# ---------------------------------------------------------------------------
# (6b) a Planner-named node survives into the brief the Evolver receives
# ---------------------------------------------------------------------------


def test_planner_named_node_survives_into_the_evolver_brief() -> None:
    brief = CandidateBrief(
        brief_id="P-R1-01",
        buckets=("config",),
        task_ids=("t1",),
        rationale="retune the guard",
        implicated_node="proc:cost_guard_processor",
    )
    contract_brief = rvp._planner_brief_for_contract(brief)
    assert contract_brief["implicated_node"] == "proc:cost_guard_processor"

    merged = rvp._planner_brief_with_regressions(contract_brief, ())
    assert merged["implicated_node"] == "proc:cost_guard_processor"


def test_empty_implicated_node_is_stripped_for_a_byte_identical_contract() -> None:
    brief = CandidateBrief(brief_id="P-R1-01", buckets=("config",), task_ids=("t1",), rationale="x")
    assert "implicated_node" not in rvp._planner_brief_for_contract(brief)
