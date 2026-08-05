# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""Tests for ``--attribution-check`` (batch-2b Item 3, report-only).

Port of upstream/feat/aegis attribution.py. After a settled round, each shipped
candidate's predicted tasks are classified direct/orphan/joint by whether its
mechanical tool_call attribution_signature fired in the settled trajectory's
``tool_call_counts`` frontmatter. prompt/config (and, by our schema, any
processor_invocation) signatures classify as joint. Fully offline; no provider.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from experiments.variant_pool.gate import Decision  # noqa: E402
from experiments.variant_pool.manifest import (  # noqa: E402
    AttributionSignature,
    CandidateArtifact,
    ChangeManifest,
)
from recipe.gaia_evolver.run_variant_pool import (  # noqa: E402
    VariantPoolRecipe,
    _classify_attribution_firing,
    _parse_tool_call_counts,
)


# ---------------------------------------------------------------------------
# Pure classifier + frontmatter parser.
# ---------------------------------------------------------------------------
def test_parse_tool_call_counts_frontmatter() -> None:
    md = '---\ntask_id: t1\ntool_call_counts: {"SmartFetch": 3, "Read": 1}\n---\n\nbody\n'
    counts = _parse_tool_call_counts(md)
    assert counts == {"SmartFetch": 3, "Read": 1}
    assert _parse_tool_call_counts("no frontmatter here") == {}


def test_classify_direct_orphan_joint() -> None:
    sig = AttributionSignature(type="tool_call", tool_name="SmartFetch", expected_min_calls=2)
    assert _classify_attribution_firing(sig, {"SmartFetch": 2}) == "direct"
    assert _classify_attribution_firing(sig, {"SmartFetch": 1}) == "orphan"
    assert _classify_attribution_firing(sig, {}) == "orphan"
    # No mechanical signature (prompt/config) -> joint.
    assert _classify_attribution_firing(None, {"SmartFetch": 9}) == "joint"
    # processor_invocation carries no class_name in our schema -> joint (divergence).
    proc_sig = AttributionSignature(type="processor_invocation", tool_name=None)
    assert _classify_attribution_firing(proc_sig, {"SmartFetch": 9}) == "joint"


# ---------------------------------------------------------------------------
# _check_ship_attribution over a synthetic settled round (stub recipe).
# ---------------------------------------------------------------------------
def _manifest(**over) -> ChangeManifest:
    data = {
        "candidate_id": "C-R1-01",
        "bucket": ["tools"],
        "target_variant": "V0",
        "file_changes": [{"path": "smart_fetch.py", "action": "create", "diff_summary": "x"}],
        "predicted_impact": {"tasks_will_unlock": ["task1"]},
        "attribution_signature": {"type": "tool_call", "tool_name": "SmartFetch", "expected_min_calls": 1},
    }
    data.update(over)
    return ChangeManifest.model_validate(data)


def _stub(tmp_path: Path, manifest: ChangeManifest, *, counts_line: str) -> tuple:
    rel = "R1/active_pool/V0/trajectories/task1.md"
    traj = tmp_path / rel
    traj.parent.mkdir(parents=True, exist_ok=True)
    # Body carries a NUL byte -> the checker must read with errors="replace".
    traj.write_bytes(
        f"---\ntask_id: task1\n{counts_line}\n---\n\nbody".encode("utf-8") + b"\x00tail\n"
    )
    (tmp_path / "config.yaml").write_text("processors: []\n", encoding="utf-8")
    candidate = CandidateArtifact(
        config_path=tmp_path / "config.yaml", manifest=manifest, target_variant="V0"
    )
    stub = types.SimpleNamespace(
        run_dir=tmp_path,
        _active_round_records={"V0": {"task1": {"trajectory_file": rel}}},
        _round_candidates={"C-R1-01": candidate},
    )
    result = types.SimpleNamespace(
        selected_candidate_ids={"V0": "C-R1-01"},
        decisions={"V0": Decision.APPLY},
    )
    return stub, result


def test_shipped_signature_fired_is_direct(tmp_path: Path) -> None:
    stub, result = _stub(tmp_path, _manifest(), counts_line='tool_call_counts: {"SmartFetch": 2}')
    out = VariantPoolRecipe._check_ship_attribution(stub, result, 1)
    assert out["C-R1-01"]["tasks"]["task1"] == "direct"
    assert out["C-R1-01"]["summary"] == {"direct": 1, "orphan": 0, "joint": 0}


def test_shipped_signature_not_fired_is_orphan(tmp_path: Path) -> None:
    stub, result = _stub(tmp_path, _manifest(), counts_line='tool_call_counts: {"Read": 5}')
    out = VariantPoolRecipe._check_ship_attribution(stub, result, 1)
    assert out["C-R1-01"]["tasks"]["task1"] == "orphan"


def test_prompt_bucket_is_joint(tmp_path: Path) -> None:
    manifest = _manifest(bucket=["prompt"], attribution_signature=None)
    stub, result = _stub(tmp_path, manifest, counts_line='tool_call_counts: {"SmartFetch": 9}')
    out = VariantPoolRecipe._check_ship_attribution(stub, result, 1)
    assert out["C-R1-01"]["tasks"]["task1"] == "joint"
    assert out["C-R1-01"]["signature"] is None


def test_no_ship_nothing_attributed(tmp_path: Path) -> None:
    # A REJECT decision means nothing shipped -> empty attribution (proxy for
    # "flag off / no ships -> nothing runs").
    stub, result = _stub(tmp_path, _manifest(), counts_line='tool_call_counts: {"SmartFetch": 2}')
    result.decisions = {"V0": Decision.REJECT}
    out = VariantPoolRecipe._check_ship_attribution(stub, result, 1)
    assert out == {}
