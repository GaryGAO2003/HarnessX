# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""G1b tests — materialised graph evidence gets DISCOVERED, not just readable.

Mirrors the precedent tests/ghx/ already established for G1/G2:

* ``test_read_gate.py`` — proves the vendored read-scope gate admits evidence paths
  by building a real role harness and driving a live ``ToolCallEvent`` through it.
  Test 6 here follows that exact technique for the pointer TARGET paths, but with the
  brief-pointer rebind actually installed (not just the raw evidence-file existence).
* ``test_overlay_flag.py`` / ``tests/recipe/test_launcher.py`` — spy/stub orchestrator
  + flag on/off + install/restore identity + a full vendored-integrity pin cycle.
  Tests 1/2/4/5 here follow that shape for the NEW rebind
  (``harnessx.ghx.brief_pointers.install_brief_pointers``).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path

import pytest

from harnessx import aegis as _aegis_pkg
from harnessx.aegis._paths import HARNESSX_SRC_ROOT
from harnessx.core.events import ToolCallEvent
from harnessx.ghx.brief_pointers import (
    digester_evidence_paths,
    install_brief_pointers,
    planner_evidence_paths,
)
from harnessx.ghx.evidence_files import materialize_graph_evidence
from harnessx.ghx.overlay import run_round_with_graph_evidence
from harnessx.graph.causal import CONTROL
from harnessx.graph.types import unfolded_id
from harnessx.graph.unfold import UnfoldedEdge, UnfoldedGraph, UnfoldedNode
from harnessx.meta_harness.processors.read_scope_gate import ReadScopeGateProcessor

# Duplicated (not imported) from harnessx.ghx.brief_pointers on purpose: this is a
# contract test on the rendered wording, so it must not import the private render
# helper it is checking against.
_EXPECTED_HEADER = "## Graph evidence (GHX)"
_EXPECTED_INTRO = (
    "Causal-cone map and cross-task facts for this round's failures, derived from the\n"
    "recorded execution graph (not from trace text). Read them before the raw trace:"
)


def _expected_section(paths: "list[str]") -> str:
    lines = "\n".join(f"- {p}" for p in paths)
    return f"\n\n{_EXPECTED_HEADER}\n\n{_EXPECTED_INTRO}\n\n{lines}\n"


def _prompt_text(cfg) -> str:
    for p in cfg.processors:
        if isinstance(p, dict) and "SystemPromptProcessor" in p.get("_target_", ""):
            return p["system_builder"]["text"]
    raise AssertionError("no SystemPromptProcessor entry found in config")


def _u() -> UnfoldedGraph:
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
        run_id="alpha-run",
        session_id="s",
        nodes=[a, b],
        edges=[UnfoldedEdge(source=a.id, target=b.id, edge_type=CONTROL)],
    )


def _resolver(task: str):
    return _u() if task == "alpha" else None


class _StubOrch:
    """Minimal orchestrator stand-in (same shape as tests/recipe/test_launcher.py's
    _StubOrch): exposes ``run_dir``; its ``run_round`` drives the REAL per-task
    Digester dispatch and the REAL Stage-1 Planner dispatch through the exact
    bare-name calls ``orchestrator.py`` / ``plan.py`` make at runtime — the calls
    ``install_brief_pointers`` rebinds."""

    def __init__(self, run_dir: Path) -> None:
        self.run_dir = Path(run_dir)

    async def run_round(self, *, round_n, tasks, **_ignored):
        import harnessx.aegis.agents.digester as dig_mod
        import harnessx.aegis.stages.plan as plan_mod
        from harnessx.aegis.agents.digester import DigesterInputs
        from harnessx.aegis.agents.planner import PlannerInputs

        digester_prompts = {}
        for task_id, pattern in tasks:
            inputs = DigesterInputs(
                task_id=task_id,
                pattern=pattern,
                trajectory_paths=[self.run_dir / f"R{round_n}" / "trajectories" / f"{task_id}_r0.jsonl"],
                digest_out_path=self.run_dir / f"R{round_n}" / "digests" / f"{task_id}.md",
            )
            cfg = dig_mod.build_digester_harness(inputs)  # the exact orchestrator.py call
            digester_prompts[task_id] = _prompt_text(cfg)

        planner_inputs = PlannerInputs(
            round=round_n,
            overview_path=self.run_dir / f"R{round_n}" / "summary.md",
            journal_path=self.run_dir / "journal.md",
            archive_dir=self.run_dir / "archive",
            current_config_path=self.run_dir / "config.yaml",
            landscape_path=self.run_dir / f"R{round_n}" / "landscape.md",
            digests_dir=self.run_dir / f"R{round_n}" / "digests",
            reputation_summary={},
        )
        planner_cfg = plan_mod.build_planner_harness(planner_inputs)  # the exact plan.py call
        return {"digester_prompts": digester_prompts, "planner_prompt": _prompt_text(planner_cfg)}


# ── test 1: wired round — FAILED task gets a pointer, PASSED task does not ─────────


async def test_wired_round_pointer_only_for_failed_tasks_with_evidence(tmp_path):
    run_dir = tmp_path / "run"
    stub = _StubOrch(run_dir)

    res = await run_round_with_graph_evidence(
        stub,
        failed_task_ids=["alpha"],
        resolver=_resolver,
        evidence_enabled=True,
        round_n=1,
        tasks=[("alpha", "ALL_FAIL"), ("beta", "ALL_PASS")],
    )

    cone_alpha = (run_dir / "R1" / "graph_evidence" / "cones" / "alpha.md").resolve()
    facts = (run_dir / "R1" / "graph_evidence" / "facts.md").resolve()
    assert cone_alpha.exists() and facts.exists()
    assert not (run_dir / "R1" / "graph_evidence" / "cones" / "beta.md").exists()  # passing task → no cone

    alpha_prompt = res["digester_prompts"]["alpha"]
    beta_prompt = res["digester_prompts"]["beta"]
    planner_prompt = res["planner_prompt"]

    # FAILED task: brief ENDS WITH the pointer section, listing only files that exist.
    assert alpha_prompt.endswith(_expected_section([str(cone_alpha), str(facts)]))

    # PASSED task: no pointer section anywhere — byte-identical to the vendored
    # builder called directly (Stage P dispatches a Digester for every task, passed
    # included, but only FAILED tasks are evidence-relevant — see
    # install_brief_pointers' docstring).
    assert _EXPECTED_HEADER not in beta_prompt
    from harnessx.aegis.agents.digester import DigesterInputs
    from harnessx.aegis.agents.digester import build_digester_harness as _orig_digester_builder

    beta_inputs = DigesterInputs(
        task_id="beta",
        pattern="ALL_PASS",
        trajectory_paths=[run_dir / "R1" / "trajectories" / "beta_r0.jsonl"],
        digest_out_path=run_dir / "R1" / "digests" / "beta.md",
    )
    assert beta_prompt == _prompt_text(_orig_digester_builder(beta_inputs))

    # Planner: pointer section lists facts.md + alpha's cone (only alpha — beta has none).
    assert planner_prompt.endswith(_expected_section([str(facts), str(cone_alpha)]))

    # The injection manifest is the on-disk artifact the Digester's unpersisted
    # session cannot leave: exactly one digester record (alpha, never beta) and one
    # planner record, each naming the exact injected paths.
    manifest = json.loads((run_dir / "R1" / "graph_evidence" / "injections.json").read_text(encoding="utf-8"))
    assert {(r["role"], r["task_id"]) for r in manifest} == {("digester", "alpha"), ("planner", None)}
    by_role = {r["role"]: r for r in manifest}
    assert by_role["digester"]["paths"] == [str(cone_alpha), str(facts)]
    assert by_role["planner"]["paths"] == [str(facts), str(cone_alpha)]


# ── test 2: flag off → prompt bytes identical to the unpatched builder ─────────────


async def test_flag_off_prompt_bytes_identical(tmp_path):
    run_dir = tmp_path / "run"
    stub = _StubOrch(run_dir)

    res = await run_round_with_graph_evidence(
        stub,
        failed_task_ids=["alpha"],
        resolver=_resolver,
        evidence_enabled=False,
        round_n=1,
        tasks=[("alpha", "ALL_FAIL")],
    )

    assert not (run_dir / "R1" / "graph_evidence").exists()  # G1 materialiser never ran either

    from harnessx.aegis.agents.digester import DigesterInputs
    from harnessx.aegis.agents.digester import build_digester_harness as _orig_digester_builder

    inputs = DigesterInputs(
        task_id="alpha",
        pattern="ALL_FAIL",
        trajectory_paths=[run_dir / "R1" / "trajectories" / "alpha_r0.jsonl"],
        digest_out_path=run_dir / "R1" / "digests" / "alpha.md",
    )
    assert res["digester_prompts"]["alpha"] == _prompt_text(_orig_digester_builder(inputs))


# ── test 3: evidence file absent → its line absent; nothing on disk → no section ───


def test_digester_evidence_paths_only_lists_existing_files(tmp_path):
    run_dir = tmp_path / "run"

    # Nothing materialised at all.
    assert digester_evidence_paths(run_dir, 1, "alpha") == []

    # facts.md only (no cones/ dir at all yet) → just facts, in absolute+resolved form.
    ev = run_dir / "R1" / "graph_evidence"
    ev.mkdir(parents=True)
    facts = ev / "facts.md"
    facts.write_text("shared facts", encoding="utf-8")
    assert digester_evidence_paths(run_dir, 1, "alpha") == [str(facts.resolve())]

    # cones/ dir exists but not for THIS task → still just facts.
    (ev / "cones").mkdir()
    (ev / "cones" / "someone_else.md").write_text("x", encoding="utf-8")
    assert digester_evidence_paths(run_dir, 1, "alpha") == [str(facts.resolve())]

    # this task's cone appears → both, cone first then facts.
    cone = ev / "cones" / "alpha.md"
    cone.write_text("x", encoding="utf-8")
    assert digester_evidence_paths(run_dir, 1, "alpha") == [str(cone.resolve()), str(facts.resolve())]


def test_planner_evidence_paths_globs_only_existing_cones(tmp_path):
    run_dir = tmp_path / "run"
    assert planner_evidence_paths(run_dir, 1) == []

    ev = run_dir / "R1" / "graph_evidence"
    (ev / "cones").mkdir(parents=True)
    facts = ev / "facts.md"
    facts.write_text("x", encoding="utf-8")
    (ev / "cones" / "alpha.md").write_text("x", encoding="utf-8")
    (ev / "cones" / "gamma.md").write_text("x", encoding="utf-8")

    paths = planner_evidence_paths(run_dir, 1)
    # facts.md first, then cones in sorted-by-filename order (alpha before gamma).
    assert paths == [
        str(facts.resolve()),
        str((ev / "cones" / "alpha.md").resolve()),
        str((ev / "cones" / "gamma.md").resolve()),
    ]


async def test_no_evidence_dir_at_all_leaves_prompt_untouched(tmp_path):
    run_dir = tmp_path / "run"  # nothing ever materialised under this run_dir
    from harnessx.aegis.agents.digester import DigesterInputs
    from harnessx.aegis.agents.digester import build_digester_harness as _orig_digester_builder

    inputs = DigesterInputs(
        task_id="alpha",
        pattern="ALL_FAIL",
        trajectory_paths=[run_dir / "t.jsonl"],
        digest_out_path=run_dir / "d.md",
    )
    with install_brief_pointers(run_dir, 1, ["alpha"]):
        import harnessx.aegis.agents.digester as dig_mod

        wrapped_text = _prompt_text(dig_mod.build_digester_harness(inputs))

    assert _EXPECTED_HEADER not in wrapped_text
    assert wrapped_text == _prompt_text(_orig_digester_builder(inputs))
    # No injection happened → no manifest record was written anywhere.
    assert not (run_dir / "R1" / "graph_evidence" / "injections.json").exists()


# ── test 4: rebind installs and restores, including across an exception ────────────


def test_rebind_installs_and_restores_identity(tmp_path):
    import harnessx.aegis.agents.digester as dig_mod
    import harnessx.aegis.stages.plan as plan_mod

    orig_digester = dig_mod.build_digester_harness
    orig_planner = plan_mod.build_planner_harness

    with install_brief_pointers(tmp_path, 1, ["alpha"]):
        assert dig_mod.build_digester_harness is not orig_digester
        assert plan_mod.build_planner_harness is not orig_planner

    assert dig_mod.build_digester_harness is orig_digester
    assert plan_mod.build_planner_harness is orig_planner


def test_rebind_restores_on_exception_inside_round(tmp_path):
    import harnessx.aegis.agents.digester as dig_mod
    import harnessx.aegis.stages.plan as plan_mod

    orig_digester = dig_mod.build_digester_harness
    orig_planner = plan_mod.build_planner_harness

    class _Boom(RuntimeError):
        pass

    with pytest.raises(_Boom):
        with install_brief_pointers(tmp_path, 1, ["alpha"]):
            assert dig_mod.build_digester_harness is not orig_digester
            raise _Boom("kaboom mid-round")

    assert dig_mod.build_digester_harness is orig_digester
    assert plan_mod.build_planner_harness is orig_planner


# ── test 5: vendored integrity pin stays green across a full install/restore cycle ─


def _hash_aegis_tree() -> "dict[str, str]":
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


def test_vendored_integrity_stays_green_across_install_cycle(tmp_path):
    manifest_path = Path(__file__).parent / "vendored_aegis_manifest.json"
    expected = json.loads(manifest_path.read_text(encoding="utf-8"))
    with install_brief_pointers(tmp_path, 1, ["alpha"]):
        pass  # a full patch/restore cycle must not alter any vendored file's bytes
    assert _hash_aegis_tree() == expected


# ── test 6: pointer target paths are admissible under the Digester's read gate ─────


def _read_gate_dict(cfg) -> dict:
    """Pull the read-scope gate's serialised entry out of a HarnessConfig — the
    same extraction tests/ghx/test_read_gate.py uses."""
    for p in cfg.processors:
        if isinstance(p, dict) and "ReadScopeGateProcessor" in p.get("_target_", ""):
            return p
    raise AssertionError("no ReadScopeGateProcessor entry found in config")


def _is_blocked(gate: dict, path) -> bool:
    resolved = Path(path).resolve()
    allowed = [Path(x).resolve() for x in gate.get("allowed_files", [])]
    blocked = [Path(x).resolve() for x in gate.get("blocked_roots", [])]
    if any(resolved == a for a in allowed):
        return False
    for root in blocked:
        try:
            resolved.relative_to(root)
            return True
        except ValueError:
            continue
    return False


def _drive_gate(gate: dict, path: str) -> ToolCallEvent:
    """Run a real Read ToolCallEvent through a live gate built from the serialised
    args, returning the (possibly replaced) event the gate yields."""
    live = ReadScopeGateProcessor(
        blocked_roots=tuple(gate.get("blocked_roots", ())),
        allowed_files=tuple(gate.get("allowed_files") or ()),
    )
    ev = ToolCallEvent(run_id="r", step_id=0, tool_name="Read", tool_input={"file_path": path})

    async def _first():
        async for out in live.on_before_tool(ev):
            return out
        return None

    return asyncio.run(_first())


def test_pointer_targets_admissible_under_digester_read_gate(tmp_path):
    run_dir = tmp_path / "run"
    summary = materialize_graph_evidence(run_dir, 1, ["alpha"], lambda t: _u())
    ev = Path(summary["evidence_dir"])
    cone_file = ev / "cones" / "alpha.md"
    facts_file = ev / "facts.md"
    assert cone_file.exists() and facts_file.exists()

    with install_brief_pointers(run_dir, 1, ["alpha"]):
        import harnessx.aegis.agents.digester as dig_mod
        from harnessx.aegis.agents.digester import DigesterInputs

        inputs = DigesterInputs(
            task_id="alpha",
            pattern="ALL_FAIL",
            trajectory_paths=[run_dir / "R1" / "trajectories" / "alpha_r0.jsonl"],
            digest_out_path=run_dir / "R1" / "digests" / "alpha.md",
        )
        cfg = dig_mod.build_digester_harness(inputs)

    # The pointer actually names these exact paths — discoverable AND admissible,
    # not merely one or the other.
    text = _prompt_text(cfg)
    assert str(cone_file.resolve()) in text
    assert str(facts_file.resolve()) in text

    gate = _read_gate_dict(cfg)
    assert not _is_blocked(gate, cone_file)
    assert not _is_blocked(gate, facts_file)

    out = _drive_gate(gate, str(cone_file))
    assert out.approved is True
    assert out.synthetic_result is None
    out_facts = _drive_gate(gate, str(facts_file))
    assert out_facts.approved is True

    # No regression: harnessx source stays blocked, live gate blocks it too.
    src = HARNESSX_SRC_ROOT / "core" / "processor.py"
    assert _is_blocked(gate, src)
    blocked = _drive_gate(gate, str(src))
    assert blocked.approved is False
