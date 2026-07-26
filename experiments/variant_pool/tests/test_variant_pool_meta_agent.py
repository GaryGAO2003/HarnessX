# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""Offline tests for :class:`VariantPoolMetaAgent` and the repo-native manifest.

These pin the architecture invariant that replaced the (reverted) upstream
``candidate_contract`` change: the candidate contract is injected by a
recipe-layer subclass, never by ``harnessx/``. They assert

1. ``candidate_contract=None`` renders byte-for-byte the same ``TASK.md`` as a
   native :class:`MetaAgent` (no upstream behaviour change leaks in);
2. paper mode reproduces the exact wording of the reverted upstream prototype;
3. repo mode (default) drops the ``manifest.yaml`` requirement;
4. the repo-native journal fallback builds a complete manifest without any
   ``manifest.yaml`` from the meta-agent.

No network, no model calls: everything is a pure render / adapt.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# The subclass lives under ``recipe/``; put the repo root on the path (conftest
# only adds ``experiments/``).
_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from harnessx.meta_harness import MetaAgent  # noqa: E402
from harnessx.meta_harness.journal import JournalEntrySpec, append_entry  # noqa: E402

from recipe.gaia_evolver import run_variant_pool as rvp  # noqa: E402
from recipe.gaia_evolver.variant_pool_meta_agent import VariantPoolMetaAgent  # noqa: E402
from variant_pool.manifest import REPO_JOURNAL_PROVENANCE  # noqa: E402


def _brief_kwargs() -> dict:
    return {
        "current_config_path": Path("/tmp/current.yaml"),
        "trajectories_dir": Path("/tmp/traj"),
        "output_dir": Path("/tmp/out"),
        "context_path": None,
    }


# ===========================================================================
# 1. candidate_contract=None == native MetaAgent
# ===========================================================================


def test_none_contract_renders_identically_to_native_metaagent() -> None:
    native = MetaAgent(inner_model=object())  # type: ignore[arg-type]
    ours = VariantPoolMetaAgent(inner_model=object())  # type: ignore[arg-type]

    assert ours._candidate_contract is None  # default is inert
    assert ours._render_task_brief(**_brief_kwargs()) == native._render_task_brief(**_brief_kwargs())
    # The upstream contract section must be absent when no contract is set.
    assert "Structured candidate contract" not in ours._render_task_brief(**_brief_kwargs())
    assert "manifest.yaml" not in ours._render_task_brief(**_brief_kwargs())


def test_clearing_the_contract_restores_native_output() -> None:
    native = MetaAgent(inner_model=object())  # type: ignore[arg-type]
    ours = VariantPoolMetaAgent(inner_model=object())  # type: ignore[arg-type]

    ours.set_candidate_contract(
        {
            "suggested_candidate_id": "C-R1-01",
            "target_variant": "V0",
            "planner_brief": {"manifest_mode": "repo"},
        }
    )
    assert "Structured candidate contract" in ours._render_task_brief(**_brief_kwargs())

    ours.set_candidate_contract(None)
    assert ours._render_task_brief(**_brief_kwargs()) == native._render_task_brief(**_brief_kwargs())


# ===========================================================================
# 2. paper mode == the reverted upstream wording (equivalence to the backup patch)
# ===========================================================================


def _expected_paper_section(candidate_id: str, target_variant: str, planner_brief: dict) -> str:
    """The exact wording of the reverted upstream ``_render_candidate_contract``.

    Reconstructed independently from ``experiments/_wip_backup/
    agent_py_candidate_contract.patch`` so the assertion checks the wording, not
    the implementation under test.
    """
    contract_json = json.dumps(
        {
            "suggested_candidate_id": candidate_id,
            "target_variant": target_variant,
            "planner_brief": planner_brief,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return (
        "\n## Structured candidate contract (caller-required)\n\n"
        "This evolve call is one isolated candidate slot. In addition to the "
        "normal deliverables, write `_meta_scratch/manifest.yaml` as a bare "
        "YAML mapping. The caller rejects the proposal if this file is "
        "missing, malformed, or incomplete; it will not infer fields from "
        "`candidates.md` or silently wrap `config.yaml`.\n\n"
        f"- `suggested_candidate_id`: `{candidate_id}` (use exactly this value)\n"
        f"- `target_variant`: `{target_variant}` (use exactly this value)\n"
        f"- `planner_contract_json`: `{contract_json}`\n\n"
        "Required manifest keys: `candidate_id`, `bucket`, `iterates_from`, "
        "`capability_evidence`, `file_changes`, `predicted_impact`, "
        "`attribution_signature`, and `target_variant`. Continue to write "
        "`_meta_scratch/candidates.md` when the config changes; the manifest "
        "is an additional machine-readable contract, not a replacement.\n\n"
    )


def test_paper_mode_section_matches_the_reverted_upstream_wording() -> None:
    planner_brief = {"manifest_mode": "paper", "rationale": "x"}
    contract = {
        "suggested_candidate_id": "C-R3-02",
        "target_variant": "V7",
        "planner_brief": planner_brief,
    }
    section = VariantPoolMetaAgent._render_candidate_contract(contract)
    assert section == _expected_paper_section("C-R3-02", "V7", planner_brief)

    # And the section is appended verbatim into the rendered TASK.md.
    ours = VariantPoolMetaAgent(inner_model=object())  # type: ignore[arg-type]
    ours.set_candidate_contract(contract)
    brief = ours._render_task_brief(**_brief_kwargs())
    assert brief.endswith(section)
    assert "`_meta_scratch/manifest.yaml`" in brief


# ===========================================================================
# 3. repo mode drops the manifest.yaml requirement
# ===========================================================================


def test_repo_mode_section_drops_the_manifest_requirement() -> None:
    contract = {
        "suggested_candidate_id": "C-R3-02",
        "target_variant": "V7",
        "planner_brief": {"manifest_mode": "repo", "rationale": "x"},
    }
    section = VariantPoolMetaAgent._render_candidate_contract(contract)
    # Identity fields kept.
    assert "`C-R3-02` (use exactly this value)" in section
    assert "`V7` (use exactly this value)" in section
    assert '"manifest_mode": "repo"' in section  # planner_contract_json carries it
    # No manifest.yaml demand.
    assert "You do NOT need to write `_meta_scratch/manifest.yaml`" in section
    assert "Required manifest keys" not in section


def test_repo_mode_is_the_default_when_manifest_mode_is_absent() -> None:
    contract = {
        "suggested_candidate_id": "C-R1-01",
        "target_variant": "V0",
        "planner_brief": {"rationale": "x"},
    }
    section = VariantPoolMetaAgent._render_candidate_contract(contract)
    assert "You do NOT need to write `_meta_scratch/manifest.yaml`" in section
    assert "Required manifest keys" not in section


# ===========================================================================
# 4. repo-native journal fallback (no manifest.yaml) -> complete manifest
# ===========================================================================


def test_repo_journal_fallback_builds_a_complete_manifest(tmp_path: Path) -> None:
    journal = tmp_path / "learnings.md"
    append_entry(
        journal,
        JournalEntrySpec(
            round=1,
            label="repair blocked retrieval",
            hypothesis_id="h_source_escalation_v1",
            levers=["instruction"],
            predicted_affected=["t1", "t2"],
            prose="### Why\nsource escalation\n",
        ),
    )
    # Non-existent config paths force the changeset enrichment to degrade to the
    # honest config.yaml-only file_changes entry.
    manifest = rvp._repo_manifest_from_journal(
        memo_path=journal,
        current_config_path=tmp_path / "missing_current.yaml",
        new_config_path=tmp_path / "missing_new.yaml",
        candidate_id="C-R1-01",
        target_variant="V0",
    )

    assert manifest.provenance == REPO_JOURNAL_PROVENANCE
    assert manifest.candidate_id == "C-R1-01"
    assert manifest.target_variant == "V0"
    assert manifest.bucket == ["prompt"]  # instruction -> prompt
    assert manifest.predicted_impact.tasks_will_unlock == ["t1", "t2"]
    assert manifest.source_hypothesis_id == "h_source_escalation_v1"
    assert manifest.file_changes  # never empty
    assert manifest.file_changes[0]["path"] == "config.yaml"
    # A prompt-bucket repo-journal manifest is complete and reaches the gate.
    assert manifest.validate_complete() == []


def test_repo_journal_file_changes_degrades_to_config_only(tmp_path: Path) -> None:
    changes = rvp._repo_journal_file_changes(
        tmp_path / "nope_a.yaml", tmp_path / "nope_b.yaml"
    )
    assert changes == [
        {
            "path": "config.yaml",
            "action": "modify",
            "diff_summary": (
                "config.yaml changed vs current_config (repo-native manifest adaptation)"
            ),
        }
    ]


def test_repo_journal_fallback_without_a_journal_entry_is_incomplete_not_crashing(
    tmp_path: Path,
) -> None:
    """No journal entry -> no bucket/flip, but it parses and fails at the gate."""
    empty_journal = tmp_path / "empty.md"
    empty_journal.write_text("", encoding="utf-8")
    manifest = rvp._repo_manifest_from_journal(
        memo_path=empty_journal,
        current_config_path=tmp_path / "a.yaml",
        new_config_path=tmp_path / "b.yaml",
        candidate_id="C-R1-01",
        target_variant="V0",
    )
    assert manifest.provenance == REPO_JOURNAL_PROVENANCE
    problems = manifest.validate_complete()
    assert any("bucket" in p for p in problems)
    assert any("predicted_impact" in p for p in problems)
