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

**The ask-more path gets no wiring at all — on purpose.** ``EvolverInputs.ask_more_candidate_path``
is a scratch file the orchestrator allocates FRESH per subcall
(``askmore_scratch/{cid}_{uuid4().hex[:8]}.md`` — never pre-existing, never the
original candidate's real manifest), and nothing downstream ever reads it back. The
vendored ask-more prompt (``harnessx/aegis/templates/evolver.md``, lines 1-9) tells
the model in plain language to "answer in `final_output`" and that "your answer is
appended to the candidate manifest by the Critic" — and that is exactly what
``make_evolver_runner`` (``harnessx/aegis/stages/judge.py``, line 30) does:
``result.final_output or "(no answer)"`` is the ENTIRE read path for an ask-more
subcall's answer. ``ask_more_candidate_path`` exists only as a defensive write-scope
release valve for a model that ignores the prompt and tries to write a file anyway
(the write-scope gate allows exactly this one path) — it is not a channel anything
downstream consumes.

An earlier revision of this module wired a standalone ``GraphProposalManifest``/
``GraphProposalStatus`` pair onto that path. That was actively wrong: handing the
model a tool that LOOKS authoritative and answering the question by calling it
instead of writing to ``final_output`` routes the answer straight into a file
nobody reads, starving the one channel the Critic actually collects. So the
wrapper below does the only thing that cannot misdirect the model in ask-more
mode — it returns ``orig(inputs)`` completely unmodified: no new tools registered,
no prompt text appended, ``GraphProposalOpen``/``GraphProposalEdit``/
``GraphProposalManifest``/``GraphProposalStatus`` all absent, same as if this seam
were never installed. The lazy import at ``orchestrator.py``'s
``evolver_harness_factory`` still resolves to this wrapper on every ask-more
subcall (see "The double rebind" above) — it just passes the call straight through.
"""

from __future__ import annotations

import contextlib

from .brief_pointers import _append_pointer_to_config
from .graph_proposals import ProposalSession, graph_proposals_enabled

_PROMPT_HEADER = "## Graph-native candidate tools (GHX)"

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

# F3: IV-9 (harnessx/aegis/gates/structure.py) rejects a candidate whose file_changes
# include an extension outside its declared bucket's whitelist -- and file_changes is
# assembled by scanning what the model actually wrote (see _file_changes_for), not by
# trusting the bucket field, so a mismatched bucket is unrecoverable after the fact.
_BUCKET_EXTENSION_WARNING = (
    "Bucket/extension gate (IV-9): bucket=config allows only .yaml/.yml; "
    "bucket=prompt allows .md/.yaml/.yml; bucket=processor and bucket=tools each "
    "allow .py/.yaml/.yml. file_changes is assembled by scanning every file you "
    "actually wrote in this candidate's scratch directory -- you cannot hide a .py "
    "asset under bucket=config, IV-9 checks by extension against what is really on "
    "disk. If a candidate legitimately touches more than one bucket's file types, "
    "pass bucket as a LIST (e.g. [\"prompt\", \"processor\"]) instead of a single "
    "string. Before finishing, call GraphProposalStatus to run the real structure "
    "gate against your candidate and catch a bucket/extension mismatch yourself."
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
        _BUCKET_EXTENSION_WARNING,
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
            # F1: ask-more gets no wiring at all -- see module docstring. cfg is
            # orig(inputs) untouched: no new tools, no injected prompt text.
            return cfg
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
