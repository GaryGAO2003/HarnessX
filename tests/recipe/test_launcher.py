# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""Tests for the GHX ladder launcher ``recipe/gaia_evolver/run_meta_aegis_ghx.py``.

Covers: ``--help`` surfaces ``--ghx-level``; the level→flag table; explicit env beating
the level; L0 delegating to the vendored round loop untouched; the level≥2 evidence
wiring and the level-4 gate pass-through (checked=False with no real replay U); the
run_round seam installs/restores and its re-entry guard reaches the original exactly
once; and the vendored-integrity pin staying green across import + wiring.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

import recipe.gaia_evolver.run_meta_aegis as _pilot
import recipe.gaia_evolver.run_meta_aegis_ghx as ghx
from harnessx import aegis as _aegis_pkg
from harnessx.aegis.orchestrator import AegisOrchestrator
from harnessx.graph.causal import CONTROL
from harnessx.graph.types import unfolded_id
from harnessx.graph.unfold import UnfoldedEdge, UnfoldedGraph, UnfoldedNode, write_unfolded

_UNFOLD = ghx._UNFOLD
_IDENTITY = ghx._IDENTITY
_EVIDENCE = ghx._EVIDENCE
_GATE = ghx._GATE
_ALL_FLAGS = (_UNFOLD, _IDENTITY, _EVIDENCE, _GATE, "HARNESSX_GHX_RUNTIME")


# ── fixtures shared with the G1/G2 test style ─────────────────────────────────


def _small_u(run_id: str, session_id: str) -> UnfoldedGraph:
    """A minimal two-node U (before_model → task_end) that yields a non-empty cone."""
    a = UnfoldedNode(
        id=unfolded_id("Sys", 0),
        static_node_id="Sys",
        graphed=True,
        hook="before_model",
        step=0,
        ordinal=0,
        label="Sys",
    )
    b = UnfoldedNode(
        id=unfolded_id("End", 1), static_node_id="End", graphed=True, hook="task_end", step=1, ordinal=1, label="End"
    )
    return UnfoldedGraph(
        run_id=run_id,
        session_id=session_id,
        nodes=[a, b],
        edges=[UnfoldedEdge(source=a.id, target=b.id, edge_type=CONTROL)],
    )


def _gate_manifest(tmp_path: Path, cid: str) -> Path:
    p = tmp_path / f"{cid}.md"
    p.write_text(
        "---\n"
        "bucket: tools\n"
        "attribution_signature:\n"
        "  type: tool_call\n"
        "  tool_name: SmartFetch\n"
        "  expected_min_calls: 1\n"
        "---\n\n"
        "A tool candidate.\n",
        encoding="utf-8",
    )
    return p


class _StubOrch:
    """Minimal orchestrator stand-in: exposes ``run_dir`` and records run_round calls."""

    def __init__(self, run_dir: Path) -> None:
        self.run_dir = Path(run_dir)
        self.calls: list[dict] = []

    async def run_round(self, **kwargs):
        self.calls.append(kwargs)
        return {"shipped_cids": []}


# ── test 1: --help exits 0 and shows --ghx-level ──────────────────────────────


def test_help_exits_zero_and_shows_ghx_level(capsys):
    parser = ghx.build_parser()
    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "--ghx-level" in out


def test_ghx_level_default_and_choices():
    parser = ghx.build_parser()
    assert parser.parse_args([]).ghx_level == 0
    assert parser.parse_args(["--ghx-level", "5"]).ghx_level == 5
    with pytest.raises(SystemExit):
        parser.parse_args(["--ghx-level", "6"])  # out of the 0..5 ladder


# ── test 2: level → flag mapping (exactly the documented set, nothing extra) ───


@pytest.mark.parametrize(
    "level,expected",
    [
        (0, set()),
        (1, {_UNFOLD, _IDENTITY}),
        (2, {_UNFOLD, _IDENTITY, _EVIDENCE}),
        (3, {_UNFOLD, _IDENTITY, _EVIDENCE}),
        (4, {_UNFOLD, _IDENTITY, _EVIDENCE, _GATE}),
    ],
)
def test_level_flag_mapping(level, expected):
    env: dict[str, str] = {}
    applied = ghx.apply_level_flags(level, env)
    assert set(applied) == expected
    # Exactly these flags and no others — in particular RUNTIME is never on the ladder.
    assert set(env) == expected
    assert all(env[f] == "1" for f in expected)
    assert "HARNESSX_GHX_RUNTIME" not in env


# ── test 3: explicit env beats the level (both directions) ────────────────────


def test_explicit_env_disables_flag_the_level_would_set():
    env = {_EVIDENCE: "0"}  # user turned evidence OFF explicitly
    ghx.apply_level_flags(2, env)  # level 2 would turn it on
    assert env[_EVIDENCE] == "0"  # explicit value survives
    assert env[_UNFOLD] == "1"  # untouched flags still default on


def test_explicit_env_keeps_flag_a_lower_level_omits():
    env = {_GATE: "1"}  # user turned the gate ON explicitly
    ghx.apply_level_flags(1, env)  # level 1 does not include the gate
    assert env[_GATE] == "1"  # not cleared — explicit env wins


# ── test 3b: meta model — explicit flag > GAIA_META_MODEL env > follows --model ─
#
# The vendored default is an Anthropic model captured at import time; with only one
# key (LiteLLM/DeepSeek) that crashes the first meta phase, so the launcher never
# falls back to it.


def test_meta_model_follows_main_model_when_unspecified(monkeypatch):
    monkeypatch.delenv("GAIA_META_MODEL", raising=False)
    args = ghx.build_parser().parse_args(["--model", "deepseek/deepseek-chat"])
    ghx._resolve_meta_model(args)
    assert args.meta_model == "deepseek/deepseek-chat"


def test_meta_model_env_beats_follow(monkeypatch):
    monkeypatch.setenv("GAIA_META_MODEL", "deepseek/deepseek-reasoner")
    args = ghx.build_parser().parse_args(["--model", "deepseek/deepseek-chat"])
    ghx._resolve_meta_model(args)
    assert args.meta_model == "deepseek/deepseek-reasoner"


def test_meta_model_explicit_flag_beats_env_and_follow(monkeypatch):
    monkeypatch.setenv("GAIA_META_MODEL", "deepseek/deepseek-reasoner")
    args = ghx.build_parser().parse_args(["--model", "deepseek/deepseek-chat", "--meta-model", "openai/gpt-4o"])
    ghx._resolve_meta_model(args)
    assert args.meta_model == "openai/gpt-4o"


# ── test 4: L0 delegates to the vendored round loop untouched, sets no flags ───


async def test_level0_delegates_to_vendored_pilot_and_sets_no_flags(monkeypatch):
    import os

    for flag in _ALL_FLAGS:
        monkeypatch.delenv(flag, raising=False)
    seen: list = []

    async def _spy_run_pilot(args):
        seen.append(args)

    monkeypatch.setattr(_pilot, "run_pilot", _spy_run_pilot)
    orig_run_round = AegisOrchestrator.run_round

    await ghx.main(["--ghx-level", "0", "--max-tasks", "1"])

    # The vendored round loop was invoked exactly once, with the parsed args.
    assert len(seen) == 1
    assert seen[0].ghx_level == 0
    assert seen[0].max_tasks == 1
    # Level 0 set no GHX flags.
    for flag in _ALL_FLAGS:
        assert not os.environ.get(flag)
    # No overlay seam was installed (or it was fully restored).
    assert AegisOrchestrator.run_round is orig_run_round


# ── test 5a: level ≥2 evidence wiring materialises cones for FAILING tasks ─────


async def test_level2_wired_round_materialises_cones_from_capture(tmp_path, monkeypatch):
    monkeypatch.setenv(_EVIDENCE, "1")
    monkeypatch.delenv(_GATE, raising=False)

    import harnessx.aegis.orchestrator as orch_mod

    orig_calls = {"n": 0}

    async def _fake_orig(self, **kwargs):
        orig_calls["n"] += 1
        return {"shipped_cids": []}

    monkeypatch.setattr(orch_mod.AegisOrchestrator, "run_round", _fake_orig)

    run_dir = tmp_path / "run"
    # The failing task's U lives under R{round_n-1}=R0's sessions (rollouts that ran).
    base_dir = run_dir / "R0" / "sessions"
    write_unfolded(_small_u("run-a", "aegis/R0-alpha"), base_dir=str(base_dir))

    with ghx._ghx_round_wiring() as capture:
        capture[0] = {"alpha": ("aegis/R0-alpha", "run-a")}
        orch = object.__new__(orch_mod.AegisOrchestrator)
        orch.run_dir = run_dir
        res = await orch.run_round(
            round_n=1,
            raw_sessions_dir=tmp_path,
            pass_flags_by_task={"alpha": [False], "beta": [True]},
            current_config_path=tmp_path / "c.yaml",
        )

    assert res == {"shipped_cids": []}
    assert orig_calls["n"] == 1  # delegate reached exactly once (guard held)
    ev = run_dir / "R1" / "graph_evidence"  # evidence goes under the PLANNED round
    assert (ev / "cones" / "alpha.md").exists()  # failing task → cone from its U
    assert not (ev / "cones" / "beta.md").exists()  # passing task → nothing
    assert (ev / "facts.md").exists()


async def test_level2_evidence_overlay_called_with_failed_ids_and_resolved_u(tmp_path, monkeypatch):
    """Direct check on the composition helper: non-empty failed_task_ids + a resolver
    that resolves a written U → the evidence overlay writes that task's cone."""
    monkeypatch.setenv(_EVIDENCE, "1")
    monkeypatch.delenv(_GATE, raising=False)

    run_dir = tmp_path / "run"
    base_dir = run_dir / "R0" / "sessions"
    write_unfolded(_small_u("run-a", "aegis/R0-alpha"), base_dir=str(base_dir))
    resolver = ghx._make_evidence_resolver(base_dir, {"alpha": ("aegis/R0-alpha", "run-a")})
    stub = _StubOrch(run_dir)

    res = await ghx._ghx_run_round(
        stub,
        failed_task_ids=["alpha"],
        evidence_resolver=resolver,
        gate_u_resolver=ghx._gate_u_resolver,
        parent_config_path=tmp_path / "c.yaml",
        round_n=1,
        raw_sessions_dir=tmp_path,
        pass_flags_by_task={"alpha": [False]},
        current_config_path=tmp_path / "c.yaml",
    )

    assert res == {"shipped_cids": []}
    assert len(stub.calls) == 1  # delegate still called
    assert (run_dir / "R1" / "graph_evidence" / "cones" / "alpha.md").exists()


# ── test 5b: level 4 gate engaged; no real replay U → pass-through checked=False ─


async def test_level4_gate_passes_through_when_no_replay_u(tmp_path, monkeypatch):
    monkeypatch.setenv(_GATE, "1")
    monkeypatch.delenv(_EVIDENCE, raising=False)  # isolate the gate

    import harnessx.aegis.orchestrator as orch_mod

    async def _fake_stage_4(**kwargs):
        return {
            "shipped_cid": "c1",
            "shipped_cids": ["c1"],
            "gate_results": {"c1": {}},
            "reason": None,
            "candidate_signatures": {},
        }

    monkeypatch.setattr(orch_mod, "run_stage_4", _fake_stage_4)
    sentinel = orch_mod.run_stage_4

    candidates_info = {"c1": (_gate_manifest(tmp_path, "c1"), tmp_path / "c1.yaml")}
    s4_kwargs = {"round_n": 1, "candidates_info": candidates_info}

    class _RebindStub:
        """Stands in for the vendored run_round: calls the (gate-wrapped) module-level
        ``run_stage_4`` and returns its shipped set — the seam the gate filters through."""

        def __init__(self, run_dir: Path) -> None:
            self.run_dir = Path(run_dir)

        async def run_round(self, **kwargs):
            import harnessx.aegis.orchestrator as m

            stage_4 = await m.run_stage_4(**s4_kwargs)
            return {"shipped_cids": stage_4.get("shipped_cids")}

    stub = _RebindStub(tmp_path / "run")

    res = await ghx._ghx_run_round(
        stub,
        failed_task_ids=[],
        evidence_resolver=lambda tid: None,
        gate_u_resolver=ghx._gate_u_resolver,
        parent_config_path=tmp_path / "c1.yaml",
        round_n=1,
        raw_sessions_dir=tmp_path,
        pass_flags_by_task={},
        current_config_path=tmp_path / "c1.yaml",
    )

    # No real replay U → the gate could not check → the candidate is kept (pass-through).
    assert res["shipped_cids"] == ["c1"]
    # The module global run_stage_4 is restored after the gate-wrapped round.
    assert orch_mod.run_stage_4 is sentinel
    # And the honest evidence records checked=False ("not checked").
    gate_md = (stub.run_dir / "R1" / "graph_evidence" / "gate" / "c1.md").read_text(encoding="utf-8")
    assert "not checked" in gate_md.lower()


def test_gate_resolver_is_honestly_none():
    """The launcher's gate resolver must return None (no real replay U exists yet).

    This is the mutation-verify anchor: if it returned the parent task's U instead,
    ``test_level4_gate_passes_through_when_no_replay_u`` would refuse c1 and fail.
    """
    assert ghx._gate_u_resolver("any-candidate") is None


# ── test: the run_round seam installs, guards re-entry, and restores ───────────


def test_wiring_installs_and_restores():
    orig = AegisOrchestrator.run_round
    with ghx._ghx_round_wiring() as capture:
        assert AegisOrchestrator.run_round is not orig
        assert isinstance(capture, dict)
    assert AegisOrchestrator.run_round is orig


async def test_run_task_capture_records_session_and_run_id(monkeypatch):
    """The run-id map the resolver needs is captured at the rollout call site by
    wrapping the module-global ``run_meta_aegis._run_task`` — the same global the
    vendored ``run_pilot`` resolves at call time."""

    class _Res:
        run_id = "run-xyz"

    class _Task:
        task_id = "alpha"

    async def _orig(harness, task, label, **kw):
        return {"task_id": task.task_id, "_result": _Res()}

    monkeypatch.setattr(_pilot, "_run_task", _orig)

    with ghx._ghx_round_wiring() as capture:
        rec = await _pilot._run_task(object(), _Task(), "aegis/R0")
        assert rec["_result"].run_id == "run-xyz"  # original record passes through
        # session_id mirrors exactly what _run_task hands harness.run(): f"{label}-{tid}".
        assert capture == {0: {"alpha": ("aegis/R0-alpha", "run-xyz")}}


async def test_wiring_reentry_reaches_original_exactly_once(tmp_path, monkeypatch):
    monkeypatch.delenv(_EVIDENCE, raising=False)
    monkeypatch.delenv(_GATE, raising=False)

    import harnessx.aegis.orchestrator as orch_mod

    calls = {"orig": 0}

    async def _fake_orig(self, **kwargs):
        calls["orig"] += 1
        return {"shipped_cids": []}

    monkeypatch.setattr(orch_mod.AegisOrchestrator, "run_round", _fake_orig)

    with ghx._ghx_round_wiring():
        orch = object.__new__(orch_mod.AegisOrchestrator)
        orch.run_dir = tmp_path
        res = await orch.run_round(
            round_n=1,
            raw_sessions_dir=tmp_path,
            pass_flags_by_task={},
            current_config_path=tmp_path / "c.yaml",
        )

    assert res == {"shipped_cids": []}
    assert calls["orig"] == 1  # overlay routed, inner re-entry hit the original once


# ── test 6: vendored integrity pin stays green across import + wiring ──────────


def _hash_aegis_tree() -> dict[str, str]:
    # EOL-normalized, matching tests/ghx/test_vendored_integrity.py: the pilot's own
    # snapshot/restore rewrites files in text mode on Windows (LF→CRLF), so raw-byte
    # hashing false-alarms after every real run.
    root = Path(_aegis_pkg.__file__).parent
    out: dict[str, str] = {}
    for p in sorted(root.rglob("*")):
        if p.is_dir():
            continue
        parts = p.relative_to(root).parts
        if "__pycache__" in parts or p.suffix == ".pyc":
            continue
        out[p.relative_to(root).as_posix()] = hashlib.sha256(p.read_bytes().replace(b"\r\n", b"\n")).hexdigest()
    return out


def test_launcher_import_and_wiring_keep_vendored_bytes():
    manifest_path = Path(__file__).resolve().parents[2] / "tests" / "ghx" / "vendored_aegis_manifest.json"
    expected = json.loads(manifest_path.read_text(encoding="utf-8"))
    with ghx._ghx_round_wiring():
        pass  # a full patch/restore cycle must not alter any vendored file's bytes
    assert _hash_aegis_tree() == expected
