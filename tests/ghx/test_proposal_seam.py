# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""L5 seam tests — wiring :mod:`harnessx.ghx.graph_proposals` into the vendored Evolver.

Mirrors the precedent :mod:`tests.ghx.test_brief_pointers` and
:mod:`tests.recipe.test_launcher` already established:

* Double-rebind coverage (both call sites; simulated lazy import mirrors
  ``orchestrator.py``'s ``evolver_harness_factory``) + install/restore identity.
* Flag off -> byte-for-byte passthrough; flag on -> tools + prompt appended, vendored
  text kept as an untouched prefix.
* Preflight failures propagate rather than get swallowed into a silent downgrade.
* The launcher's level table gets a level 5, ``setdefault`` precedence intact.

Every test here is offline — no model calls, no network. ``build_evolver_harness``
is documented pure ("no file I/O at call time"), so only tests that actually
construct a ``ProposalSession`` (flag on, non-ask-more) need a real parent YAML on
disk; the small fixture is reused from ``test_graph_proposals.py`` rather than
re-derived (either is fine per the L5 build brief; importing keeps one source of
truth for the probe processor classes' dotted _target_ paths).
"""

from __future__ import annotations

from pathlib import Path

import pytest

import recipe.gaia_evolver.run_meta_aegis_ghx as ghx
from harnessx.aegis.agents.evolver import EvolverInputs, build_evolver_harness
from harnessx.ghx import graph_proposals
from harnessx.ghx.graph_proposals import ProposalPreflightError
from harnessx.ghx.proposal_seam import install_graph_proposals
from tests.ghx.test_graph_proposals import _BAD_PARENT_YAML, _PARENT_YAML


def _prompt_text(cfg) -> str:
    for p in cfg.processors:
        if isinstance(p, dict) and "SystemPromptProcessor" in p.get("_target_", ""):
            return p["system_builder"]["text"]
    raise AssertionError("no SystemPromptProcessor entry found in config")


def _make_inputs(tmp_path: Path, **overrides) -> EvolverInputs:
    defaults = dict(
        round=5,
        current_config_path=tmp_path / "parent.yaml",
        landscape_path=tmp_path / "landscape.md",
        digests_dir=tmp_path / "digests",
        trajectories_dir=tmp_path / "trajectories",
        candidates_dir=tmp_path / "candidates",
        applied_root=tmp_path / "applied",
    )
    defaults.update(overrides)
    return EvolverInputs(**defaults)


# ── 1. double rebind: propose.py's own name + a simulated lazy import ─────────────


def test_double_rebind_covers_propose_module_and_simulated_lazy_import():
    import harnessx.aegis.agents.evolver as evolver_mod
    import harnessx.aegis.stages.propose as propose_mod

    orig = evolver_mod.build_evolver_harness
    assert propose_mod.build_evolver_harness is orig  # sanity: same object pre-install

    with install_graph_proposals():
        # (a) propose.py's own module-level-imported copy of the name is rebound.
        assert propose_mod.build_evolver_harness is not orig
        wrapped_a = propose_mod.build_evolver_harness

        # (b) orchestrator.py:454's shape: a local import inside a function body,
        # re-executed every call -- resolves the SAME wrapper, live, off the
        # defining module's rebound attribute.
        def _lazy_import_call_site():
            from harnessx.aegis.agents.evolver import build_evolver_harness

            return build_evolver_harness

        wrapped_b = _lazy_import_call_site()
        assert wrapped_b is wrapped_a
        assert wrapped_b is not orig

    # Restored on exit -- both call sites see the true original again.
    assert propose_mod.build_evolver_harness is orig
    assert evolver_mod.build_evolver_harness is orig


# ── 2. flag off: wrapper is a pure passthrough ─────────────────────────────────────


async def test_flag_off_wrapper_is_passthrough(tmp_path, monkeypatch):
    monkeypatch.delenv(graph_proposals.FLAG, raising=False)
    import harnessx.aegis.stages.propose as propose_mod

    inputs = _make_inputs(tmp_path)  # current_config_path need not exist -- flag is off
    baseline = build_evolver_harness(inputs)

    with install_graph_proposals():
        wrapped = propose_mod.build_evolver_harness(inputs)

    assert len(wrapped.processors) == len(baseline.processors)
    assert _prompt_text(wrapped) == _prompt_text(baseline)
    assert wrapped.tool_registry.list_names() == baseline.tool_registry.list_names()


# ── 3. flag on, normal mode: 4 tools + prompt additions, vendored text untouched ──


async def test_flag_on_normal_mode_registers_tools_and_appends_prompt(tmp_path, monkeypatch):
    monkeypatch.setenv(graph_proposals.FLAG, "1")
    import harnessx.aegis.stages.propose as propose_mod

    parent = tmp_path / "parent.yaml"
    parent.write_text(_PARENT_YAML, encoding="utf-8")
    inputs = _make_inputs(tmp_path, current_config_path=parent)
    baseline = build_evolver_harness(inputs)

    with install_graph_proposals():
        cfg = propose_mod.build_evolver_harness(inputs)

    new_names = set(cfg.tool_registry.list_names()) - set(baseline.tool_registry.list_names())
    assert new_names == {
        "GraphProposalOpen", "GraphProposalEdit", "GraphProposalManifest", "GraphProposalStatus",
    }

    baseline_text = _prompt_text(baseline)
    text = _prompt_text(cfg)
    assert text.startswith(baseline_text)  # vendored original text is an untouched PREFIX
    injected = text[len(baseline_text):]
    assert "Do NOT hand-write" in injected  # P-1 suppression
    assert "processor node(s)" in injected  # node inventory
    assert "trajectories/, sessions/, and digests/" in injected  # anchor contract
    assert "_hook_/_order_/_singleton_group_/" in injected  # metadata materialization trap
    assert "IV-9" in injected  # F3: bucket/extension gate warning
    assert "Bucket is DERIVED" in injected  # 08-13: bucket comes from the diff, not the model
    assert "never looks at graph edges" in injected  # 08-13: landability rule
    assert "REPLACES the entire" in injected  # failure_evidence full-replace warning


# ── 4. flag on, ask-more mode: NO wiring at all -- cfg is orig(inputs), untouched ──


def _ask_more_inputs(tmp_path: Path, **overrides) -> EvolverInputs:
    candidates_dir = tmp_path / "candidates"
    scratch = tmp_path / "askmore_scratch" / "C-R5-01_deadbeef.md"
    base = dict(
        candidates_dir=candidates_dir,
        ask_more_brief_path=candidates_dir / "C-R5-01.md",
        ask_more_candidate_id="C-R5-01",
        ask_more_candidate_path=scratch,
    )
    base.update(overrides)
    return _make_inputs(tmp_path, **base)


async def test_flag_on_ask_more_mode_returns_cfg_unmodified(tmp_path, monkeypatch):
    """F1: the answer channel for ask-more is final_output (evolver.md L1-9 tells the
    model so directly; judge.py:30 is the only place the answer is read back), and
    ask_more_candidate_path is a defensive write-scope release valve nothing
    downstream reads. Wiring a Manifest/Status pair onto it -- as an earlier revision
    of this module did -- gives the model a tool that LOOKS authoritative and drains
    the answer into a file nobody reads, starving final_output. So in ask-more mode
    the wrapper must be a pure passthrough: no new tools, no prompt text appended, no
    file ever written to ask_more_candidate_path."""
    monkeypatch.setenv(graph_proposals.FLAG, "1")
    import harnessx.aegis.stages.propose as propose_mod

    inputs = _ask_more_inputs(tmp_path)
    baseline = build_evolver_harness(inputs)

    with install_graph_proposals():
        cfg = propose_mod.build_evolver_harness(inputs)

    assert cfg.tool_registry.list_names() == baseline.tool_registry.list_names()
    assert _prompt_text(cfg) == _prompt_text(baseline)
    assert not inputs.ask_more_candidate_path.exists()


# ── 5. preflight failure is loud, not swallowed ────────────────────────────────────


async def test_flag_on_bad_parent_raises_preflight_error(tmp_path, monkeypatch):
    monkeypatch.setenv(graph_proposals.FLAG, "1")
    import harnessx.aegis.stages.propose as propose_mod

    parent = tmp_path / "parent.yaml"
    parent.write_text(_BAD_PARENT_YAML, encoding="utf-8")
    inputs = _make_inputs(tmp_path, current_config_path=parent)

    with install_graph_proposals():
        with pytest.raises(ProposalPreflightError):
            propose_mod.build_evolver_harness(inputs)


# ── 6. launcher: level 5 = level 4 ∪ {GRAPH_PROPOSALS}, setdefault precedence ──────


def test_launcher_level5_flags_equal_level4_union_proposals():
    env: dict = {}
    applied = ghx.apply_level_flags(5, env)
    assert set(applied) == {ghx._UNFOLD, ghx._IDENTITY, ghx._EVIDENCE, ghx._GATE, ghx._PROPOSALS}
    assert env[ghx._PROPOSALS] == "1"


def test_launcher_level5_explicit_env_zero_survives():
    env = {ghx._PROPOSALS: "0"}
    ghx.apply_level_flags(5, env)
    assert env[ghx._PROPOSALS] == "0"  # explicit env always wins over the level


@pytest.mark.parametrize("level", [0, 1, 2, 3, 4])
def test_launcher_level_le4_sets_no_proposals_flag(level):
    env: dict = {}
    ghx.apply_level_flags(level, env)
    assert ghx._PROPOSALS not in env


def test_launcher_proposals_flag_matches_graph_proposals_module():
    assert ghx._PROPOSALS == graph_proposals.FLAG


# ── 7. launcher wiring: unconditional install + the "any level" dispatch gap ──────


def test_ghx_round_wiring_installs_proposals_rebind_unconditionally(monkeypatch):
    monkeypatch.delenv(ghx._PROPOSALS, raising=False)
    import harnessx.aegis.stages.propose as propose_mod

    orig = propose_mod.build_evolver_harness
    with ghx._ghx_round_wiring():
        assert propose_mod.build_evolver_harness is not orig
    assert propose_mod.build_evolver_harness is orig


async def test_dispatch_wires_seam_when_only_proposals_flag_is_set(monkeypatch):
    """HARNESSX_GHX_GRAPH_PROPOSALS=1 alone (no evidence/gate, --ghx-level 0) must
    still wire the seam -- same "explicit env wins at any level" contract the other
    GHX flags already have (see _dispatch's OR condition)."""
    monkeypatch.setenv(ghx._PROPOSALS, "1")
    monkeypatch.delenv(ghx._EVIDENCE, raising=False)
    monkeypatch.delenv(ghx._GATE, raising=False)

    import harnessx.aegis.stages.propose as propose_mod

    orig = propose_mod.build_evolver_harness
    seen: dict = {}

    async def _spy_run_pilot(args):
        seen["wired"] = propose_mod.build_evolver_harness is not orig

    monkeypatch.setattr(ghx._pilot, "run_pilot", _spy_run_pilot)
    args = ghx.build_parser().parse_args(["--ghx-level", "0"])

    await ghx._dispatch(args)

    assert seen.get("wired") is True
    assert propose_mod.build_evolver_harness is orig  # restored once dispatch returns
