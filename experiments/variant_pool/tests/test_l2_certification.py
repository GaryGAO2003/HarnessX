# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""Offline tests for ``--l2-cert`` L2 machine self-certification (SPEC §7.11 乙+甲).

repo-mode tool candidates die at gate stage 4 (ROUNDTRIP_L2) when the meta-agent
declines to declare ``capability_evidence``. ``--l2-cert auto`` machine-certifies
the missing Level-2 evidence from the candidate's OWN evaluation trajectories: it
finds the new tool's real recorded output and runs it through the provider's real
serializer. These tests are fully offline — no network, no rollouts, no provider.
A fake serializer factory is injected so the production serializer (which imports
provider internals lazily) is never constructed here.

Imports go through the ``experiments.variant_pool.*`` path — the same one the
recipe uses — so ``run_gate`` is the exact object the recipe threads (importing
the top-level ``variant_pool.*`` alias would give a distinct module and break
``is`` identity, mirroring ``test_force_gate.py``).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# The recipe lives under ``recipe/``; put the repo root on the path so it and its
# ``experiments.variant_pool`` imports resolve (conftest only adds ``experiments/``).
_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from recipe.gaia_evolver import run_variant_pool as rvp  # noqa: E402
from experiments.variant_pool.gate import (  # noqa: E402
    Decision,
    GateStage,
    TaskEval,
    run_gate,
)
from experiments.variant_pool.ledger import SuccessLedger  # noqa: E402
from experiments.variant_pool.manifest import (  # noqa: E402
    CandidateArtifact,
    ChangeManifest,
)


# ---------------------------------------------------------------------------
# Stubs & fixtures
# ---------------------------------------------------------------------------


class _StubRecipe:
    """Minimal recipe state the L2 gate wrapper reads (item 2 threading).

    Only the attributes ``_l2_certifying_gate`` and the certifier actually touch:
    the three mode flags, ``run_dir``, ``_candidate_meta`` (audit sink) and
    ``_round_traj_dir`` (locates the candidate's ``sessions/`` dir).
    """

    def __init__(
        self,
        tmp_path: Path,
        *,
        l2_cert: str = "auto",
        manifest_mode: str = "repo",
        candidate_mode: str = "paper",
    ) -> None:
        self.l2_cert = l2_cert
        self.manifest_mode = manifest_mode
        self.candidate_mode = candidate_mode
        self.run_dir = tmp_path / "run"
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self._candidate_meta: dict = {}
        self._round_traj_dir: dict = {}


class _SpyFactory:
    """A serializer factory that counts how often the serializer is constructed."""

    def __init__(self) -> None:
        self.calls = 0

    def __call__(self):
        self.calls += 1
        return lambda s: s


def _surviving_factory():
    """Real-shaped serializer: content survives verbatim (identity)."""
    return lambda s: s


def _dropping_factory():
    """A serializer that drops the content entirely (survival must fail)."""
    return lambda s: ""


def _manifest(
    *,
    candidate_id: str = "C-R1-01",
    target_variant: str = "V0",
    buckets: tuple[str, ...] = ("tools",),
    tool_path: str | None = "tools/MyTool",
    declared: bool = False,
    extra_file_changes: tuple[dict, ...] = (),
) -> ChangeManifest:
    """A repo-journal manifest that passes gate stage 1 (completeness).

    ``provenance='repo_journal'`` relaxes the paper-only capability_evidence /
    attribution requirements, exactly like a real repo-mode candidate, so stage 1
    passes and the candidate reaches stage 4 undeclared.
    """
    capability_evidence: list[dict] = []
    if declared:
        capability_evidence = [
            {
                "type": "other",
                "claim": "tool return survives provider serialization to the model (Level 2)",
                "evidence": "observed intact in the next model message",
            }
        ]
    file_changes: list[dict] = []
    if tool_path:
        file_changes.append(
            {"path": tool_path, "action": "create", "diff_summary": "add tool"}
        )
    file_changes.extend(extra_file_changes)
    if not file_changes:
        file_changes = [{"path": "config.yaml", "action": "modify", "diff_summary": "cfg"}]
    return ChangeManifest.model_validate(
        {
            "candidate_id": candidate_id,
            "bucket": list(buckets),
            "capability_evidence": capability_evidence,
            "file_changes": file_changes,
            "predicted_impact": {"tasks_will_unlock": ["t1"]},
            "attribution_signature": None,
            "target_variant": target_variant,
            "provenance": "repo_journal",
        }
    )


def _artifact(manifest: ChangeManifest, tmp_path: Path) -> CandidateArtifact:
    """Wrap a manifest as the structured candidate the gate unwraps at stage 4."""
    return CandidateArtifact(
        config_path=tmp_path / "candidate.yaml",  # never created; gate does not load it
        manifest=manifest,
        target_variant=manifest.target_variant,
    )


def _write_tool_session(
    sessions_dir: Path,
    tool_name: str,
    output: str,
    *,
    external: bool = False,
    session_id: str = "sess0",
    run_id: str = "run0",
) -> None:
    """Write a HarnessJournal-shaped session JSONL with one tool result.

    Mirrors the real on-disk format (journal.py): a ``raw_tool`` record whose
    ``message`` is ``{role: tool, content, tool_call_id, name}``. Large outputs
    are externalized to ``tool_results/{id}.txt`` and referenced via
    ``meta.content_ref`` (journal INLINE_LIMIT); small ones stay inline. A
    ``*_trace.jsonl`` sibling is written to prove the parser skips it.
    """
    sdir = Path(sessions_dir) / session_id
    sdir.mkdir(parents=True, exist_ok=True)
    if external:
        (sdir / "tool_results").mkdir(exist_ok=True)
        (sdir / "tool_results" / "tc1.txt").write_text(output, encoding="utf-8")
        rec = {
            "type": "raw_tool",
            "step": 1,
            "message": {"role": "tool", "content": "", "tool_call_id": "tc1", "name": tool_name},
            "meta": {"content_ref": "tool_results/tc1.txt", "content_size": len(output.encode())},
        }
    else:
        rec = {
            "type": "raw_tool",
            "step": 1,
            "message": {"role": "tool", "content": output, "tool_call_id": "tc1", "name": tool_name},
        }
    (sdir / f"{run_id}.jsonl").write_text(json.dumps(rec) + "\n", encoding="utf-8")
    (sdir / f"{run_id}_trace.jsonl").write_text(
        json.dumps({"event_type": "tool_result", "tool_name": tool_name}) + "\n",
        encoding="utf-8",
    )


def _wire_candidate_dir(stub: _StubRecipe, candidate_id: str, tmp_path: Path) -> Path:
    """Create the candidate_gate dir and point ``_round_traj_dir`` at it.

    Returns the ``sessions/`` dir (sibling of ``trajectories/``), where session
    fixtures should be written.
    """
    cg = tmp_path / "cg" / candidate_id
    stub._round_traj_dir[candidate_id] = cg / "trajectories"
    return cg / "sessions"


def _run(stub, candidate, *, parent_config, tk_results, serializer_factory):
    """Invoke the L2-certifying gate on one candidate (helper for the logic tests)."""
    gate = rvp._l2_certifying_gate(stub, run_gate, serializer_factory=serializer_factory)
    return gate(candidate, parent_config, SuccessLedger(), tk_results)


_IMPROVE = [TaskEval("t1", before=(0, 2), after=(2, 2))]  # stage 5 -> APPLY on pass
_FLAT = [TaskEval("t1", before=(0, 2), after=(0, 2))]  # only reached if stage 4 passes


# ===========================================================================
# Recipe wiring / mode identity (item 1)
# ===========================================================================


class _Args:
    """Minimal CLI namespace the recipe __init__ reads (paper-mode capable)."""

    def __init__(self, **overrides) -> None:
        self.pool_k = 1
        self.num_rounds = 1
        self.pass_k = 2
        self.max_cost = 5.0
        self.concurrency = 2
        self.no_judge = True
        self.run_tag = "test-l2-cert"
        self.model = "task-model"
        self.meta_model = "meta-model"
        self.max_tasks = 0
        self.max_steps = 20
        self.seed = 0
        self.estimator = "laplace"
        self.cluster_mode = "routed"
        self.routing_mode = "cluster"
        self.routing_window = None
        self.retirement_metric = "task_macro"
        self.candidate_mode = "paper"
        self.candidates_per_round = 1
        self.actionability_threshold = 1.0
        self.target_strategy = "worst_first"
        self.manifest_mode = "repo"
        self.patience = 3
        self.evolve_retry = 1
        self.__dict__.update(overrides)


def _recipe(tmp_path, **overrides):
    run_dir = tmp_path / "run"
    run_dir.mkdir(parents=True, exist_ok=True)
    baseline = tmp_path / "V0" / "config.yaml"
    baseline.parent.mkdir(parents=True, exist_ok=True)
    baseline.write_text("baseline: true\n", encoding="utf-8")
    return rvp.VariantPoolRecipe(
        args=_Args(**overrides),
        tasks=[type("_T", (), {"task_id": "t1", "level": 1})()],
        model_config=object(),
        meta_agent=object(),
        pipeline_eval=object(),
        run_dir=run_dir,
        baseline_config_path=baseline,
    )


def test_off_flag_keeps_the_gate_byte_identical_to_run_gate(tmp_path) -> None:
    """``--l2-cert off`` (force-gate off) leaves the engine gate as ``run_gate``."""
    recipe = _recipe(tmp_path, l2_cert="off")
    assert recipe.l2_cert == "off"
    assert recipe.engine.gate is run_gate


def test_paper_manifest_mode_auto_does_not_inject(tmp_path) -> None:
    """The faithful arm (manifest-mode paper) never machine-certifies."""
    recipe = _recipe(tmp_path, manifest_mode="paper", l2_cert="auto")
    assert recipe.engine.gate is run_gate


def test_auto_repo_paper_injects_the_certifying_wrapper(tmp_path) -> None:
    recipe = _recipe(tmp_path, manifest_mode="repo", l2_cert="auto", candidate_mode="paper")
    assert recipe.engine.gate is not run_gate


def test_legacy_candidate_mode_stays_identity_even_under_auto_repo(tmp_path) -> None:
    """Legacy opaque candidates have no manifest-backed stage 4 to certify."""
    recipe = _recipe(
        tmp_path,
        candidate_mode="legacy_single",
        target_strategy="all_active_variants",
        manifest_mode="repo",
        l2_cert="auto",
    )
    assert recipe.engine.gate is run_gate


def test_recipe_rejects_an_unknown_l2_cert_mode(tmp_path) -> None:
    with pytest.raises(ValueError):
        _recipe(tmp_path, l2_cert="bogus")


# ===========================================================================
# Certifier logic (item 3) — via the wrapper on structured candidates
# ===========================================================================


def test_declared_evidence_wins_and_the_probe_is_never_called(tmp_path) -> None:
    """甲: a meta-declared Level-2 entry defers to the built-in; no probe runs."""
    stub = _StubRecipe(tmp_path)
    manifest = _manifest(declared=True)
    art = _artifact(manifest, tmp_path)
    spy = _SpyFactory()

    result = _run(
        stub, art, parent_config=tmp_path / "p.yaml", tk_results=_IMPROVE, serializer_factory=spy
    )

    assert result.decision is Decision.APPLY  # stage 4 passed on the declaration
    assert result.passed is True
    assert spy.calls == 0  # the serializer was never even constructed
    assert "l2_certification" not in stub._candidate_meta.get("C-R1-01", {})


def test_tools_bucket_certified_from_real_external_output(tmp_path) -> None:
    """乙: undeclared tool output (externalized) survives -> stage 4 passes, recorded."""
    stub = _StubRecipe(tmp_path)
    manifest = _manifest()  # bucket=[tools], tools/MyTool, undeclared
    art = _artifact(manifest, tmp_path)
    sessions = _wire_candidate_dir(stub, "C-R1-01", tmp_path)
    big = "WIKI-EXTRACT " * 500  # > journal INLINE_LIMIT -> externalized fixture
    _write_tool_session(sessions, "MyTool", big, external=True)

    result = _run(
        stub, art, parent_config=tmp_path / "p.yaml", tk_results=_IMPROVE,
        serializer_factory=_surviving_factory,
    )

    assert result.passed is True
    assert result.decision is Decision.APPLY
    cert = stub._candidate_meta["C-R1-01"]["l2_certification"]
    assert cert["outcome"] == "certified"
    assert cert["tool"] == "MyTool"
    assert cert["output_chars"] == len(big)
    assert cert["provenance"] == "OURS_machine_certified"


def test_tools_bucket_inline_output_is_also_certified(tmp_path) -> None:
    """A small inline tool output is read straight from the JSONL and certified."""
    stub = _StubRecipe(tmp_path)
    art = _artifact(_manifest(), tmp_path)
    sessions = _wire_candidate_dir(stub, "C-R1-01", tmp_path)
    _write_tool_session(sessions, "MyTool", "hello world", external=False)

    result = _run(
        stub, art, parent_config=tmp_path / "p.yaml", tk_results=_IMPROVE,
        serializer_factory=_surviving_factory,
    )
    assert result.passed is True
    assert stub._candidate_meta["C-R1-01"]["l2_certification"]["outcome"] == "certified"
    assert stub._candidate_meta["C-R1-01"]["l2_certification"]["output_chars"] == len("hello world")


def test_tools_bucket_failed_probe_when_serializer_drops_content(tmp_path) -> None:
    """乙: a real output that does not survive fails stage 4 with the probe note."""
    stub = _StubRecipe(tmp_path)
    art = _artifact(_manifest(), tmp_path)
    sessions = _wire_candidate_dir(stub, "C-R1-01", tmp_path)
    _write_tool_session(sessions, "MyTool", "payload-that-gets-dropped", external=False)

    result = _run(
        stub, art, parent_config=tmp_path / "p.yaml", tk_results=_FLAT,
        serializer_factory=_dropping_factory,
    )

    assert result.passed is False
    assert result.decision is None  # halted at stage 4
    assert result.failed_stage is GateStage.ROUNDTRIP_L2
    cert = stub._candidate_meta["C-R1-01"]["l2_certification"]
    assert cert["outcome"] == "failed_probe"
    assert cert["tool"] == "MyTool"
    # the probe's own (more informative) note is surfaced in the archive reason
    assert cert["note"] in result.archive_reason
    assert "did not preserve" in cert["note"]


def test_tools_bucket_no_invocation_is_an_honest_reject(tmp_path) -> None:
    """乙 boundary: the tool was never invoked -> honest reject (SPEC §7.11)."""
    stub = _StubRecipe(tmp_path)
    art = _artifact(_manifest(), tmp_path)
    _wire_candidate_dir(stub, "C-R1-01", tmp_path)  # no session files written

    result = _run(
        stub, art, parent_config=tmp_path / "p.yaml", tk_results=_FLAT,
        serializer_factory=_surviving_factory,
    )

    assert result.failed_stage is GateStage.ROUNDTRIP_L2
    assert result.decision is None
    cert = stub._candidate_meta["C-R1-01"]["l2_certification"]
    assert cert["outcome"] == "no_invocation"
    assert cert["tool"] is None
    assert "never invoked" in result.archive_reason


def test_tools_bucket_wrong_tool_invoked_is_no_invocation(tmp_path) -> None:
    """A trajectory that only invokes some *other* tool yields no target evidence."""
    stub = _StubRecipe(tmp_path)
    art = _artifact(_manifest(), tmp_path)  # target tool = MyTool
    sessions = _wire_candidate_dir(stub, "C-R1-01", tmp_path)
    _write_tool_session(sessions, "SomeOtherTool", "irrelevant output", external=False)

    result = _run(
        stub, art, parent_config=tmp_path / "p.yaml", tk_results=_FLAT,
        serializer_factory=_surviving_factory,
    )
    assert result.failed_stage is GateStage.ROUNDTRIP_L2
    assert stub._candidate_meta["C-R1-01"]["l2_certification"]["outcome"] == "no_invocation"


def test_no_identifiable_target_tool_is_a_reject(tmp_path) -> None:
    """A tools-bucket manifest with no tools/ change and unloadable configs -> no_target."""
    stub = _StubRecipe(tmp_path)
    manifest = _manifest(tool_path=None)  # only a config.yaml file change
    art = _artifact(manifest, tmp_path)

    result = _run(
        stub, art, parent_config=tmp_path / "p.yaml", tk_results=_FLAT,
        serializer_factory=_surviving_factory,
    )

    assert result.failed_stage is GateStage.ROUNDTRIP_L2
    assert result.decision is None
    cert = stub._candidate_meta["C-R1-01"]["l2_certification"]
    assert cert["outcome"] == "no_target"
    assert "cannot identify which tool" in result.archive_reason


def test_processor_only_bucket_matches_the_builtin_declared_only(tmp_path) -> None:
    """Item 3d: processor bucket without tools stays declared-only (built-in FAIL)."""
    stub = _StubRecipe(tmp_path)
    manifest = _manifest(buckets=("processor",), tool_path=None)
    art = _artifact(manifest, tmp_path)

    certified = _run(
        stub, art, parent_config=tmp_path / "p.yaml", tk_results=_FLAT,
        serializer_factory=_surviving_factory,
    )
    builtin = run_gate(art, tmp_path / "p.yaml", SuccessLedger(), _FLAT)

    assert certified.failed_stage is GateStage.ROUNDTRIP_L2
    assert certified.decision is None
    # byte-identical to the built-in declared-only path
    assert certified.archive_reason == builtin.archive_reason
    # a delegated (non-certified) case records nothing under l2_certification
    assert "l2_certification" not in stub._candidate_meta.get("C-R1-01", {})


def test_non_code_prompt_bucket_is_exempt_like_the_builtin(tmp_path) -> None:
    """A pure prompt candidate is exempt at stage 4 (case b -> _declared_level2)."""
    stub = _StubRecipe(tmp_path)
    manifest = _manifest(buckets=("prompt",), tool_path=None)
    art = _artifact(manifest, tmp_path)

    result = _run(
        stub, art, parent_config=tmp_path / "p.yaml", tk_results=_IMPROVE,
        serializer_factory=_surviving_factory,
    )
    assert result.passed is True
    assert result.decision is Decision.APPLY
    assert "l2_certification" not in stub._candidate_meta.get("C-R1-01", {})


# ===========================================================================
# Composition with _forced_gate (item: force-gate must not override an L2 fail)
# ===========================================================================


def test_forced_gate_does_not_override_an_l2_stage4_failure(tmp_path) -> None:
    """A stage-4 L2 failure keeps decision=None, so force-gate leaves it untouched."""
    stub = _StubRecipe(tmp_path)
    art = _artifact(_manifest(), tmp_path)
    sessions = _wire_candidate_dir(stub, "C-R1-01", tmp_path)
    _write_tool_session(sessions, "MyTool", "dropped-payload", external=False)

    # Compose exactly as the recipe does: _forced_gate(mode, _l2_certifying_gate(...)).
    inner = rvp._l2_certifying_gate(stub, run_gate, serializer_factory=_dropping_factory)
    composed = rvp._forced_gate("apply", inner)
    result = composed(art, tmp_path / "p.yaml", SuccessLedger(), _FLAT)

    assert result.decision is None  # integrity failure, never force-promoted
    assert result.failed_stage is GateStage.ROUNDTRIP_L2
    assert "FORCED_GATE" not in result.archive_reason
    assert stub._candidate_meta["C-R1-01"]["l2_certification"]["outcome"] == "failed_probe"


def test_forced_gate_still_promotes_a_certified_candidate(tmp_path) -> None:
    """A certified (stage-4 pass) candidate reaches stage 5 and force-gate can act."""
    stub = _StubRecipe(tmp_path)
    art = _artifact(_manifest(), tmp_path)
    sessions = _wire_candidate_dir(stub, "C-R1-01", tmp_path)
    _write_tool_session(sessions, "MyTool", "surviving-output", external=False)

    inner = rvp._l2_certifying_gate(stub, run_gate, serializer_factory=_surviving_factory)
    composed = rvp._forced_gate("fork", inner)
    # A flat (no-improve) eval is a real stage-5 REJECT; force-fork overrides that.
    result = composed(art, tmp_path / "p.yaml", SuccessLedger(), _FLAT)

    assert result.decision is Decision.FORK
    assert "FORCED_GATE(fork)" in result.archive_reason
    assert stub._candidate_meta["C-R1-01"]["l2_certification"]["outcome"] == "certified"
