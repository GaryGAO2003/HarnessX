# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""Make materialised graph evidence DISCOVERED, not merely readable (module G1b).

G1 (:mod:`harnessx.ghx.evidence_files`) writes causal-cone maps and cross-task facts
into the round workspace, and ``tests/ghx/test_read_gate.py`` proves the vendored
read-scope gate admits those paths for the Digester role. But readable is not read:
the four AEGIS role agents are driven entirely by a per-invocation system prompt
rendered from a vendored Jinja2 template
(:func:`harnessx.aegis._prompt.render_template`) and handed to the harness as a plain
in-memory string (:class:`harnessx.aegis._prompt.StaticSystemPromptBuilder`). That
string is assembled fresh by ``build_digester_harness`` / ``build_planner_harness``
each time a role session is dispatched, is NEVER written to disk before the session
launches, and never mentions ``graph_evidence/`` — so a role that was never told the
evidence exists has no reason to go looking for it.

**Survey finding (read this before assuming a "brief file" exists on disk).** There is
no persisted per-task "brief" file in the current vendored AEGIS: the old
briefs-directory dispatch model (``R<n>/briefs/B-R<n>-NN.md``, still referenced by
``harnessx/aegis/data/ledger.py::backfill_rejected_revivals`` for backward
compatibility) was retired — see ``orchestrator.py``'s own comment, "No brief-to-
candidate alignment any more ... there are no separate briefs". What each role reads
as its operating instructions is exactly the ``system_builder.text`` string inside the
``HarnessConfig`` object ``build_digester_harness`` / ``build_planner_harness``
returns. That string IS the runtime "brief" this module injects a pointer into — a
string, not a file — and it becomes the actual first message of the role's session
before any tool call happens, which is the only place a pointer can land and still be
guaranteed *seen* rather than *available if the model thinks to look*.

**The seam.** Both builders are looked up as a bare name at their call site, exactly
like ``harnessx.ghx.graph_gate`` rebinds ``harnessx.aegis.orchestrator.run_stage_4``:
Python resolves a bare name against the CALLING function's enclosing module namespace,
so monkeypatching the name there (not necessarily where the function is *defined*)
is what changes what that call site sees.

* Digester — ``orchestrator.py``'s ``run_round`` builds a ``digester_factory`` closure
  that does ``from .agents.digester import build_digester_harness`` INSIDE the
  function body (a local import, re-executed on every call — once per task, since
  Stage P dispatches one Digester per task). A local import re-resolves the name
  against the CURRENT state of ``harnessx.aegis.agents.digester`` every time it runs,
  so rebinding ``harnessx.aegis.agents.digester.build_digester_harness`` is live for
  every subsequent per-task call, for the lifetime of the rebind.
* Planner — ``harnessx/aegis/stages/plan.py`` imports ``build_planner_harness`` at
  module top level (``from harnessx.aegis.agents.planner import build_planner_harness,
  PlannerInputs``), so ``plan.py`` holds its OWN bound copy in its own module globals
  from the moment ``plan.py`` is first imported. Rebinding
  ``harnessx.aegis.agents.planner.build_planner_harness`` (the DEFINING module) would
  NOT reach ``run_stage_1``'s call site — verified empirically: after such a patch,
  ``harnessx.aegis.stages.plan.build_planner_harness is
  harnessx.aegis.agents.planner.build_planner_harness`` is ``False``, the ``plan``
  module still holds the original. The seam that actually intercepts Stage 1's call is
  ``harnessx.aegis.stages.plan.build_planner_harness`` — the CALLING module's copy of
  the name, not the defining module's.

Evolver and Critic are out of scope. G1's evidence is failure-diagnosis material
(cones anchored at a failing run's terminal invocation; facts shared across FAILING
tasks) — relevant to the roles that read failures fresh, not to the roles that read
already-written candidates/digests.

Nothing here writes a byte under ``harnessx/aegis/``. Both vendored builders run
completely unmodified; this module calls straight through to them and then edits the
STRING they handed back — the same "call the vendored function, then post-process
what it returned" shape :mod:`harnessx.ghx.graph_gate` uses on ``run_stage_4``.
"""

from __future__ import annotations

import contextlib
import json
import logging
from pathlib import Path

_LOG = logging.getLogger("harnessx.ghx.brief_pointers")

_HEADER = "## Graph evidence (GHX)"
_INTRO = (
    "Causal-cone map and cross-task facts for this round's failures, derived from the\n"
    "recorded execution graph (not from trace text). Read them before the raw trace:"
)


# ── path layout (mirrors harnessx.ghx.evidence_files; not importable from there —
#    that module deliberately stays pure/independent of the aegis package and of this
#    one, and is not in this task's edit list, so the three-literal convention it
#    writes to is intentionally re-stated here rather than refactored out) ──────────


def _evidence_dir(run_dir, round_n: int) -> Path:
    return Path(run_dir) / f"R{round_n}" / "graph_evidence"


def _cone_path(run_dir, round_n: int, task_id: str) -> Path:
    return _evidence_dir(run_dir, round_n) / "cones" / f"{task_id}.md"


def _facts_path(run_dir, round_n: int) -> Path:
    return _evidence_dir(run_dir, round_n) / "facts.md"


def _cones_dir(run_dir, round_n: int) -> Path:
    return _evidence_dir(run_dir, round_n) / "cones"


# ── pointer text (only ever built from paths that exist on disk right now) ─────────


def _render_pointer_section(existing_paths: "list[str]") -> str:
    """Render the pointer section for a non-empty list of EXISTING absolute paths.

    Callers must never call this with an empty list — see ``digester_evidence_paths``/
    ``planner_evidence_paths``, which return ``[]`` (not a section) when nothing on
    disk matched. That split is what keeps "no evidence exists" indistinguishable from
    "no pointer was written" (constraint: never point at nothing).
    """
    lines = "\n".join(f"- {p}" for p in existing_paths)
    return f"\n\n{_HEADER}\n\n{_INTRO}\n\n{lines}\n"


def digester_evidence_paths(run_dir, round_n: int, task_id: str) -> "list[str]":
    """Absolute paths to THIS task's cone + the round's facts.md — only ones that
    exist on disk right now. Absolute, not workspace/run_dir-relative: neither
    ``build_digester_harness`` nor ``build_planner_harness`` configures a
    ``Workspace``/sandbox (verified — no ``harnessx/aegis/**`` file constructs one for
    any of the four roles), so ``harnessx.tools.builtin.read.read_tool`` never resolves
    a path through ``Sandbox.resolve`` and instead ``open()``s ``file_path`` exactly as
    given — a relative path would resolve against the *process* cwd at tool-call time,
    not the round dir, which is not a reliable target. The vendored Digester template
    already gives absolute paths for the same reason (see
    ``agents/digester.py``'s own comment: "trajectory_paths: absolute paths (for the
    Read tool, read-scope allowlist)") — this pointer matches that existing
    convention instead of introducing a second, unreliable one.
    """
    out: list[str] = []
    cone = _cone_path(run_dir, round_n, task_id)
    if cone.exists():
        out.append(str(cone.resolve()))
    facts = _facts_path(run_dir, round_n)
    if facts.exists():
        out.append(str(facts.resolve()))
    return out


def planner_evidence_paths(run_dir, round_n: int) -> "list[str]":
    """Absolute paths to facts.md + every cone file that exists for this round.

    The Planner is not scoped to one task (it synthesises across the whole round), so
    unlike the Digester's pointer this lists every cone G1 actually wrote — which is
    already exactly the failing-task subset, because G1 only ever materialises a cone
    for a failing task in the first place (``materialize_graph_evidence`` is called
    with ``failed_task_ids``). Globbing what is actually on disk, rather than
    threading a second copy of the failing-task list through this function, keeps this
    in lockstep with G1's own "only cones with a usable U get written" rule for free.
    """
    out: list[str] = []
    facts = _facts_path(run_dir, round_n)
    if facts.exists():
        out.append(str(facts.resolve()))
    cones_dir = _cones_dir(run_dir, round_n)
    if cones_dir.exists():
        out.extend(str(p.resolve()) for p in sorted(cones_dir.glob("*.md")))
    return out


# ── config mutation (post-processes the vendored builder's return value) ───────────


def _append_pointer_to_config(cfg, pointer_md: str) -> None:
    """Append ``pointer_md`` to the config's rendered system-prompt text, in place.

    ``HarnessConfig.processors`` is already a ``list[dict]`` — the SERIALISED form
    (``harnessx.core.harness._serialize_processor``) — by the time a builder function
    returns it, so no live processor instances need touching. Finds the
    ``SystemPromptProcessor`` entry's nested ``system_builder.text`` and extends that
    string. A processor's ``_code_hash`` metadata is a hash of the PROCESSOR CLASS's
    source (drift detection), not of instance data like ``text``, so appending to
    ``text`` does not stale it — confirmed against
    ``harnessx.core.harness._serialize_processor``'s own docstring ("Semantic code
    hash: detects logic changes ... Covers the entire class").

    No-op (leaves ``cfg`` exactly as the vendored builder produced it) if no
    ``SystemPromptProcessor`` entry is found — fails open rather than raising, matching
    this codebase's other GHX overlays (a crashing overlay must never sink a round).
    """
    for p in cfg.processors:
        if isinstance(p, dict) and "SystemPromptProcessor" in p.get("_target_", ""):
            sb = p.get("system_builder")
            if isinstance(sb, dict) and isinstance(sb.get("text"), str):
                sb["text"] = sb["text"] + pointer_md
            return
    _LOG.warning("brief_pointers: no SystemPromptProcessor entry found on config; pointer dropped")


# ── injection manifest (the production artifact the Digester cannot leave) ────────


def _record_injection(run_dir, round_n: int, role: str, task_id, paths: "list[str]") -> None:
    """Append one injection record to ``R{round_n}/graph_evidence/injections.json``.

    Why this exists: the official orchestrator persists Planner/Evolver/Critic
    sessions but NEVER the Digester's — its injected system prompt lives only in
    memory, so without this file a production run leaves no artifact proving the
    Digester pointer fired (L2 smoke finding, 2026-08-11). Recording ONLY actual
    injections (a record exists iff a pointer section was appended) turns the
    per-role, per-task injection accounting into an on-disk fact. Read-modify-write
    is safe under asyncio's single thread — these builders are sync calls with no
    await between read and write.
    """
    manifest = _evidence_dir(run_dir, round_n) / "injections.json"
    try:
        records = json.loads(manifest.read_text(encoding="utf-8")) if manifest.exists() else []
    except (OSError, ValueError):
        records = []
    records.append({"role": role, "task_id": task_id, "round": round_n, "paths": paths})
    manifest.write_text(json.dumps(records, indent=2), encoding="utf-8")


# ── the rebind ───────────────────────────────────────────────────────────────────


@contextlib.contextmanager
def install_brief_pointers(run_dir, round_n: int, failed_task_ids):
    """Rebind the Digester's and Planner's builders for the duration of one round.

    Restored in a ``finally`` — including when the wrapped body raises. Off-path (no
    evidence on disk for a given task/round) is a true no-op: the rebind is installed,
    but the wrapped builder finds an empty path list and never calls
    ``_append_pointer_to_config``, so the returned config is byte-identical to what the
    vendored builder alone would have produced.

    ``failed_task_ids`` scopes the DIGESTER pointer only: Stage P dispatches a
    Digester for every task, PASSED included (see
    ``harnessx/aegis/stages/preprocess.py::run_stage_p`` — it iterates
    ``task_to_clean_paths``, built from every raw session file, not a failing subset).
    G1 only ever materialises a cone for a FAILING task, and facts.md's content is
    itself scoped to cross-FAILURE nodes — neither is Digester-relevant evidence for a
    passing task's ALL_PASS digest, so a task outside ``failed_task_ids`` gets no
    pointer even on the (should-not-happen) chance a same-named cone file exists on
    disk. The Planner pointer is not task-scoped (see ``planner_evidence_paths``).
    """
    failed = frozenset(str(t) for t in (failed_task_ids or ()))

    import harnessx.aegis.agents.digester as _digester_mod
    import harnessx.aegis.stages.plan as _plan_mod

    orig_digester_builder = _digester_mod.build_digester_harness
    orig_planner_builder = _plan_mod.build_planner_harness

    def _wrapped_digester_builder(inputs):
        cfg = orig_digester_builder(inputs)
        try:
            task_id = getattr(inputs, "task_id", None)
            if task_id is not None and str(task_id) in failed:
                paths = digester_evidence_paths(run_dir, round_n, task_id)
                if paths:
                    _append_pointer_to_config(cfg, _render_pointer_section(paths))
                    _record_injection(run_dir, round_n, "digester", str(task_id), paths)
        except Exception as exc:  # noqa: BLE001 — pointer injection must never sink a round
            _LOG.warning("brief_pointers: digester pointer injection failed (non-fatal): %s", exc)
        return cfg

    def _wrapped_planner_builder(inputs):
        cfg = orig_planner_builder(inputs)
        try:
            paths = planner_evidence_paths(run_dir, round_n)
            if paths:
                _append_pointer_to_config(cfg, _render_pointer_section(paths))
                _record_injection(run_dir, round_n, "planner", None, paths)
        except Exception as exc:  # noqa: BLE001 — pointer injection must never sink a round
            _LOG.warning("brief_pointers: planner pointer injection failed (non-fatal): %s", exc)
        return cfg

    _digester_mod.build_digester_harness = _wrapped_digester_builder
    _plan_mod.build_planner_harness = _wrapped_planner_builder
    try:
        yield
    finally:
        _digester_mod.build_digester_harness = orig_digester_builder
        _plan_mod.build_planner_harness = orig_planner_builder


__all__ = [
    "digester_evidence_paths",
    "planner_evidence_paths",
    "install_brief_pointers",
]
