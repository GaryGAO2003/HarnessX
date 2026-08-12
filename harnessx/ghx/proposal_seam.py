# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""L5 — wire the graph-native candidate surface into the vendored Evolver (task #5).

:mod:`harnessx.ghx.graph_proposals` builds the capability (``ProposalSession``, the
four ``GraphProposal*`` tools) but deliberately stops short of connecting it to
anything the vendored pilot actually calls — see that module's own docstring:
"Out of scope for this module (task #5's job): rebinding the vendored Evolver
builder, prompt injection text, and the ask-more subcall path." This module is that
job.

**The double rebind.** ``harnessx.aegis.agents.evolver.build_evolver_harness`` is
looked up as a bare name at TWO call sites, and Python resolves a bare name against
the CALLING function's own module namespace — the same fact
:mod:`harnessx.ghx.brief_pointers` documents for the Digester/Planner seam:

* ``harnessx/aegis/stages/propose.py`` imports the name at module top level
  (``from harnessx.aegis.agents.evolver import (..., build_evolver_harness, ...)``,
  line 19) and calls it at line 55 — Stage 2's own copy of the name, in
  ``propose.py``'s module globals, is what ``run_stage_2`` actually calls.
* ``harnessx/aegis/orchestrator.py``'s ``evolver_harness_factory`` (closure built
  inside ``run_round``, used for Critic ask-more subcalls) does
  ``from .agents.evolver import build_evolver_harness, EvolverInputs`` INSIDE the
  function body (line 454) — a local import, re-executed every time the closure
  runs, so it re-resolves against the CURRENT state of
  ``harnessx.aegis.agents.evolver`` on every ask-more subcall.

Rebinding only the defining module (``harnessx.aegis.agents.evolver``) would miss
Stage 2's own bound copy (``propose.py`` already holds its own reference from the
moment it was first imported); rebinding only ``propose.py`` would miss the
ask-more path (the lazy import re-reads the defining module, not ``propose.py``).
Both names must be swapped to the SAME wrapper for this seam to cover both call
sites — :func:`install_graph_proposals` does exactly that, restored in a
``finally``, no vendored file's bytes ever touched (this is a runtime attribute
monkeypatch, not a file edit — ``harnessx/aegis/**`` stays byte-identical, same
technique :mod:`harnessx.ghx.graph_gate` uses on ``run_stage_4`` and
:mod:`harnessx.ghx.brief_pointers` uses on the Digester/Planner builders).

**Flag-gated at call time, not at install time.** The rebind itself installs
unconditionally (no ``if graph_proposals_enabled(): ...`` around the assignment) —
the wrapper re-reads :func:`~harnessx.ghx.graph_proposals.graph_proposals_enabled`
every time IT is called and is a byte-for-byte passthrough when the flag is off.
This is what lets ``HARNESSX_GHX_GRAPH_PROPOSALS`` be flipped independently of
whatever ``--ghx-level`` the launcher was given, exactly like every other GHX flag.

**The ask-more path does not use ProposalSession.** ``EvolverInputs.ask_more_candidate_path``
is a scratch file the orchestrator allocates FRESH per subcall
(``askmore_scratch/{cid}_{uuid4().hex[:8]}.md`` — never pre-existing, never the
original candidate's real manifest) and the vendored pipeline never reads it back:
``make_evolver_runner`` (``harnessx/aegis/stages/judge.py``) takes the Evolver's
answer from ``result.final_output`` only. The write-scope gate allows exactly this
one path as a defensive release valve, not because anything downstream consumes it.
``ProposalSession._open`` hard-codes ``candidates_dir / f"{candidate_id}.md"``, so it
cannot target this random-suffixed filename, and even if it could, ``_manifest()``
always writes FOUR files (manifest, config.yaml, two lineage files) where the
ask-more write-scope permits exactly one. So ask-more mode gets its own small,
standalone ``GraphProposalManifest``/``GraphProposalStatus`` pair (:func:`_wire_ask_more`)
that writes only ``ask_more_candidate_path`` and nothing else — same tool names,
descriptions, and JSON schemas as normal mode (imported from
:mod:`harnessx.ghx.graph_proposals`, not re-derived) for a consistent model-facing
contract, but a from-scratch implementation underneath. ``GraphProposalOpen`` and
``GraphProposalEdit`` are simply never registered in this mode — a tool that does
not exist is a structural refusal that needs no runtime check.
"""

from __future__ import annotations

import contextlib
from pathlib import Path
from typing import Any

import yaml

from .brief_pointers import _append_pointer_to_config
from .graph_proposals import (
    _MANIFEST_DESC,
    _MODEL_SUPPLIED_FIELDS,
    _SCHEMA_MANIFEST,
    _SCHEMA_STATUS,
    _STATUS_DESC,
    ProposalSession,
    graph_proposals_enabled,
)

_PROMPT_HEADER = "## Graph-native candidate tools (GHX)"
_ASK_MORE_PROMPT_HEADER = "## Graph-native candidate tools (GHX) -- ask-more"

# Verbatim per L5 build-report follow-up #1: P-1 (a prior patch) taught the vendored
# prompt to write manifests/config.yaml by hand; this session must say the opposite
# just as explicitly, or the two instructions fight silently in-context.
_P1_SUPPRESSION = (
    "In THIS session, manifests and applied config.yaml are EMITTED BY THE "
    "GraphProposal* TOOLS. Do NOT hand-write `candidates/*.md` or "
    "`applied/<cid>/config.yaml` via Write -- hand-written bytes are overwritten "
    "and logged as hand_written provenance. Use Write only for NEW asset files "
    "(new processor/tool source, templates)."
)

_ANCHOR_CONTRACT = (
    "Anchor contract: only trajectories/, sessions/, and digests/ prefixes are "
    "legal evidence anchors for failure_evidence citations. applied/ and "
    "meta_sessions/ are real files in this run but are NOT legal anchor prefixes "
    "and will not satisfy the evidence-anchor gate."
)

# Verbatim per L5 build-report follow-up #2: GraphProposalManifest rewrites the
# '## Failure Evidence' body in full on every call -- it is not addressed anywhere
# else in the prompt (the tool description covers layout, not this replace-vs-append
# distinction), so a model that assumes append-semantics silently loses prior
# citations the next time it calls the tool.
_FAILURE_EVIDENCE_REPLACE_WARNING = (
    "GraphProposalManifest's failure_evidence REPLACES the entire '## Failure "
    "Evidence' body on every call -- it does not append. Send the complete "
    "failure-evidence text every time you call it, not just what changed since "
    "the last call."
)

_METADATA_TRAP_WARNING = (
    "Metadata trap: only node_spec keys WITHOUT a leading underscore (constructor "
    "kwargs) are guaranteed to survive an edit. _hook_/_order_/_singleton_group_/"
    "_after_ do NOT survive this candidate's S4 rebuild by themselves -- only the "
    "TARGET CLASS's own _hook/_order/_singleton_group/_after class attributes do. "
    "Changing one of these four fields requires the target class itself to declare "
    "the new value -- setting it in node_spec alone returns ok=true and looks like "
    "it worked, then silently reverts once the candidate advances to its build "
    "fixed point."
)

_INCREMENTAL_DISCIPLINE = (
    "Incremental discipline: a successful GraphProposalEdit or GraphProposalManifest "
    "call already IS the delivery -- there is no separate finalize/submit step and "
    "no closing ceremony required."
)


def _register_tools(cfg, tools) -> None:
    """Register ``tools`` (``@tool``-decorated objects) into ``cfg``'s tool registry.

    ``HarnessConfig.tool_registry`` at this point is the SAME live
    ``InMemoryToolRegistry`` instance ``build_evolver_harness`` built (see
    ``harnessx/aegis/agents/evolver.py``: ``builder = HarnessBuilder().slot(tool_registry=tool_reg)``)
    -- ``HarnessBuilder.build()`` only serialises ``processors`` to ``list[dict]``;
    a tool registry passed in via ``.slot()`` is returned as-is (verified against
    ``harnessx/core/builder.py``'s ``build()``). So this is a plain ``.register()``
    call, not a config-dict edit.
    """
    for t in tools:
        cfg.tool_registry.register(t)


def _render_prompt_section(session: ProposalSession) -> str:
    lines = [
        "",
        "",
        _PROMPT_HEADER,
        "",
        "This session's candidates are produced through four tools, not hand-written",
        "files: GraphProposalOpen (start a candidate) -> GraphProposalEdit (one atomic",
        "group of typed graph edits per call; call it as many times as you need) ->",
        "GraphProposalManifest (supply the model-authored fields: capability_evidence,",
        "predicted_impact, failure_evidence, attribution_signature, notes) ->",
        "GraphProposalStatus (read-only; call any time to see what is still",
        "outstanding). Six edit_type values: insert_node, remove_node,",
        "replace_same_group, change_dependency, swap_subgraph, mutate_inactive. To",
        "change an ACTIVE processor's parameters, use replace_same_group with a",
        "node_spec that keeps the same _target_ and _singleton_group_ but different",
        "other fields -- mutate_inactive does not check whether its target is",
        "actually inactive despite its name.",
        "",
        _METADATA_TRAP_WARNING,
        "",
        _FAILURE_EVIDENCE_REPLACE_WARNING,
        "",
        _ANCHOR_CONTRACT,
        "",
        _P1_SUPPRESSION,
        "",
        _INCREMENTAL_DISCIPLINE,
        "",
        "### Round parent node inventory",
        "",
        session.node_inventory_text(),
        "",
    ]
    return "\n".join(lines)


def _append_prompt(cfg, session: ProposalSession) -> None:
    _append_pointer_to_config(cfg, _render_prompt_section(session))


def _render_ask_more_prompt_section(candidate_id: str, candidate_path: Path) -> str:
    lines = [
        "",
        "",
        _ASK_MORE_PROMPT_HEADER,
        "",
        f"This ask-more session has ONE tool pair, scoped to candidate {candidate_id}:",
        "GraphProposalManifest (writes your answer's structured fields to",
        f"{candidate_path} -- layout is machine-generated, content is yours; the file",
        "is rewritten in full on every call) and GraphProposalStatus (read-only).",
        "GraphProposalOpen and GraphProposalEdit do not exist in this session -- graph",
        "edits are not available for a clarifying answer.",
        "",
        _FAILURE_EVIDENCE_REPLACE_WARNING,
        "",
        _ANCHOR_CONTRACT,
        "",
    ]
    return "\n".join(lines)


def _wire_ask_more(cfg, inputs):
    """Ask-more subcall wiring: a standalone Manifest/Status pair, single-file scope.

    Does not construct a :class:`ProposalSession` (see module docstring for why) --
    writes ONLY ``inputs.ask_more_candidate_path``, matching the WriteScope's
    single-file allowance (``evolver.py:170-171``) exactly, never a companion
    config.yaml or lineage file the way the normal-mode Manifest tool would.
    """
    if inputs.ask_more_candidate_path is None or inputs.ask_more_candidate_id is None:
        return cfg  # nothing to bind to -- leave the vendored config untouched

    from ..tools.base import tool

    path = Path(inputs.ask_more_candidate_path)
    bound_cid = inputs.ask_more_candidate_id
    state: dict = {"fields": {}, "fields_set": set(), "failure_evidence": ""}

    def _render() -> str:
        fm: dict = {"candidate_id": bound_cid, "ask_more": True}
        for key in ("capability_evidence", "predicted_impact", "attribution_signature"):
            if key in state["fields_set"]:
                fm[key] = state["fields"][key]
        if "notes" in state["fields"]:
            fm["notes"] = state["fields"]["notes"]
        fm_yaml = yaml.safe_dump(fm, allow_unicode=True, sort_keys=False)
        body_text = state["failure_evidence"].strip() or "(no answer given yet)"
        return f"---\n{fm_yaml}---\n## Failure Evidence\n\n{body_text}\n"

    async def graph_proposal_manifest(
        candidate_id: str,
        capability_evidence: Any = None,
        predicted_impact: Any = None,
        failure_evidence: "str | None" = None,
        attribution_signature: Any = None,
        notes: "str | None" = None,
    ) -> dict:
        if candidate_id != bound_cid:
            return {
                "ok": False,
                "error": f"this ask-more session only has {bound_cid!r} open, not {candidate_id!r}",
            }
        updated: list[str] = []
        if capability_evidence is not None:
            state["fields"]["capability_evidence"] = capability_evidence
            state["fields_set"].add("capability_evidence")
            updated.append("capability_evidence")
        if predicted_impact is not None:
            state["fields"]["predicted_impact"] = predicted_impact
            state["fields_set"].add("predicted_impact")
            updated.append("predicted_impact")
        if attribution_signature is not None:
            state["fields"]["attribution_signature"] = attribution_signature
            state["fields_set"].add("attribution_signature")
            updated.append("attribution_signature")
        if notes is not None:
            state["fields"]["notes"] = notes
            updated.append("notes")
        if failure_evidence is not None:
            state["failure_evidence"] = failure_evidence
            state["fields_set"].add("failure_evidence")
            updated.append("failure_evidence")
        if not updated:
            return {
                "ok": False,
                "error": (
                    "no fields given -- pass at least one of capability_evidence/"
                    "predicted_impact/failure_evidence/attribution_signature/notes"
                ),
            }
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_render(), encoding="utf-8", newline="\n")
        return {"ok": True, "candidate_id": bound_cid, "updated_fields": updated, "files": [str(path)]}

    async def graph_proposal_status(candidate_id: str = "") -> dict:
        missing = [f for f in _MODEL_SUPPLIED_FIELDS if f not in state["fields_set"]]
        checklist: list[str] = []
        if not path.exists():
            checklist.append("no GraphProposalManifest call yet -- nothing written")
        if missing:
            checklist.append(f"GraphProposalManifest not yet called for: {', '.join(missing)}")
        if not checklist:
            checklist.append("every model-supplied field has been given")
        return {
            "ok": True,
            "candidate_id": bound_cid,
            "ask_more": True,
            "file_written": path.exists(),
            "manifest_missing_fields": missing,
            "checklist": checklist,
        }

    manifest_tool = tool(
        name="GraphProposalManifest", description=_MANIFEST_DESC, input_schema=_SCHEMA_MANIFEST
    )(graph_proposal_manifest)
    status_tool = tool(
        name="GraphProposalStatus", description=_STATUS_DESC, input_schema=_SCHEMA_STATUS
    )(graph_proposal_status)

    _register_tools(cfg, [manifest_tool, status_tool])
    _append_pointer_to_config(cfg, _render_ask_more_prompt_section(bound_cid, path))
    return cfg


@contextlib.contextmanager
def install_graph_proposals():
    """Rebind the Evolver's builder at both call sites for the duration of the block.

    Installs unconditionally (see module docstring) -- the flag check happens inside
    the wrapper, at call time, so ``HARNESSX_GHX_GRAPH_PROPOSALS`` can be flipped
    independently of whatever else is active, the same "explicit env wins" semantics
    every other GHX flag has. Restored in a ``finally``, including when the wrapped
    body raises (a :class:`~harnessx.ghx.graph_proposals.ProposalPreflightError`
    from a bad parent config propagates straight through -- never swallowed here).
    """
    import harnessx.aegis.agents.evolver as _evolver_mod
    import harnessx.aegis.stages.propose as _propose_mod

    orig = _evolver_mod.build_evolver_harness

    def _wrapped(inputs):
        cfg = orig(inputs)
        if not graph_proposals_enabled():
            return cfg
        if inputs.ask_more_brief_path is not None:
            return _wire_ask_more(cfg, inputs)
        session = ProposalSession(
            parent_config_path=inputs.current_config_path,
            candidates_dir=inputs.candidates_dir,
            applied_root=inputs.applied_root,
            round_n=inputs.round,
        )
        _register_tools(cfg, session.make_tools())
        _append_prompt(cfg, session)
        return cfg

    _propose_mod.build_evolver_harness = _wrapped
    _evolver_mod.build_evolver_harness = _wrapped
    try:
        yield
    finally:
        _propose_mod.build_evolver_harness = orig
        _evolver_mod.build_evolver_harness = orig


__all__ = ["install_graph_proposals"]
