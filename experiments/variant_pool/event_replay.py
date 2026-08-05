# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""Adapter: OUR landed session JSONL  →  the counterfactual gate's ``kind`` rows.

Why this file exists
--------------------
The official AEGIS counterfactual gate
(``harnessx/aegis/gates/counterfactual.py`` on ``upstream/feat/aegis``) dispatches
on a ``kind`` field (``after_model`` / ``after_tool`` / ``task_end``) and reads a
flat ``final_output``.  **No producer in this codebase emits that schema.**  Our
journal (``harnessx/tracing/journal.py``) writes *type*-tagged rows
(``raw_assistant`` / ``raw_tool`` / ``episode_end``); the ``kind`` schema exists
only inside the gate's own unit-test fixture.  Fed real data the gate matches
zero events and silently returns ``ok=True`` — a latent no-op.

This adapter fulfils the gate's data contract from the artifacts we actually
land, so the ported+hardened gate (``counterfactual_gate.py``) can replay a
candidate's processor chain over real trajectories.

Ground-truth schema (verified on a real 30-record session,
``runs/e_pervar3/R9/active_pool/V0/sessions/R9-V0-active-0383a3ee-.../<run>.jsonl``):

* record ``type`` values: ``session_start``, ``tools``, ``system``, ``raw_user``,
  ``raw_assistant``, ``raw_tool``, ``episode_end``.
* ``raw_assistant.message = {role, content, tool_calls:[{id,name,input}]}``
* ``raw_tool.message      = {role:"tool", content, tool_call_id, name}``
* ``episode_end           = {exit_reason, total_steps, reward, passed, message:null}``
  — has ``exit_reason``, **lacks** ``final_output`` (until the journal Task-C fix
  lands; see ``final_output`` precedence below).

Mapping produced by :func:`adapt_records`
-----------------------------------------
========================  =========================  =========================================
gate ``kind`` (hook)      ← our ``type``             flattening
========================  =========================  =========================================
``after_model``           ``raw_assistant``          ``content`` ← message.content;
 (on_after_model)                                     ``tool_calls`` ← message.tool_calls
``after_tool``            ``raw_tool``               ``tool_name`` ← message.name;
 (on_after_tool)                                      ``result`` ← message.content;
                                                      ``tool_call_id`` ← message.tool_call_id
``task_end``              ``episode_end``            ``exit_reason`` direct;
 (on_task_end)                                        ``final_output`` **injected** (see below)
========================  =========================  =========================================

``final_output`` injection precedence (highest wins):
  1. caller override for this task (``final_output_override``);
  2. the ``episode_end`` record's own ``final_output`` key, if present
     (future runs, after the journal Task-C fix — gate-ready with zero
     inference);
  3. **self-contained default** — the ``content`` of the last ``raw_assistant``
     seen *before* the ``episode_end`` in the stream.

Rows that already carry a ``kind`` key are passed through **verbatim**, so
official-format streams (and hand-built test fixtures) replay unchanged.
Records whose ``type`` is not one of the three hook sources
(``session_start`` / ``tools`` / ``system`` / ``raw_user`` …) are dropped: they
are not hook-dispatchable.

Fidelity caveats (carried into the gate docstring too)
------------------------------------------------------
The flattened rows carry only ``content`` / ``tool_calls`` / ``result`` /
``exit_reason`` / ``final_output``.  A processor that reads richer model output —
``thinking`` / ``thinking_blocks`` / ``usage`` / ``content_blocks`` / per-step
token counts — sees defaults on replay, so its behaviour degrades or no-ops.
Large ``raw_tool`` results are off-loaded by the journal to
``tool_results/<id>_raw.txt`` sidecars (``meta.content_ref``) and the inline
``message.content`` is then empty; replay sees ``""`` for those tool results
(the sidecar is not re-inlined here).  Both are acceptable for the gate's
purpose (detecting a candidate processor that *rewrites* ``final_output`` /
``exit_reason``) but mean the replay is a lower-fidelity shadow of a live run.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


# our journal ``type``  →  gate ``kind``
_TYPE_TO_KIND = {
    "raw_assistant": "after_model",
    "raw_tool": "after_tool",
    "episode_end": "task_end",
}

# gate ``kind`` values that a processor hook can be dispatched for.
HOOK_KINDS = ("after_model", "after_tool", "task_end")


def _adapt_one(record: dict, *, last_assistant_content: str,
               final_output_override: str | None) -> "dict | None":
    """Flatten a single OUR session record into one gate ``kind`` row.

    Returns ``None`` for records that are not hook-dispatchable.
    Pass-through: a record that already carries a ``kind`` key is returned
    unchanged (official-format / hand-built rows).
    """
    # Already in kind-format → pass through verbatim.
    if "kind" in record:
        return record

    rtype = record.get("type")
    kind = _TYPE_TO_KIND.get(rtype)
    if kind is None:
        return None

    msg = record.get("message") or {}
    step = record.get("step", 0)

    if kind == "after_model":
        return {
            "kind": "after_model",
            "step": step,
            "content": msg.get("content", "") or "",
            "tool_calls": list(msg.get("tool_calls") or ()),
        }
    if kind == "after_tool":
        return {
            "kind": "after_tool",
            "step": step,
            "tool_name": msg.get("name", "") or "",
            "result": msg.get("content", "") or "",
            "tool_call_id": msg.get("tool_call_id", "") or "",
        }
    # kind == "task_end"
    if final_output_override is not None:
        final_output = final_output_override
    elif record.get("final_output") is not None:
        # Future runs: journal Task-C fix writes final_output onto episode_end.
        final_output = record.get("final_output")
    else:
        final_output = last_assistant_content
    return {
        "kind": "task_end",
        "step": step,
        "exit_reason": record.get("exit_reason", "done"),
        "final_output": final_output,
        "total_steps": record.get("total_steps", 0),
    }


def adapt_records(records: "list[dict]", *,
                  final_output_override: str | None = None) -> "list[dict]":
    """Adapt an ordered list of OUR session records into gate ``kind`` rows.

    ``records`` is the parsed JSONL stream (one dict per line), in file order.
    ``final_output_override`` — if given, every ``task_end`` row uses this exact
    value for ``final_output`` (precedence 1 above); otherwise the last
    ``raw_assistant.content`` seen before each ``episode_end`` is injected.

    Non-dispatchable records are dropped.  Rows already in ``kind`` form pass
    through verbatim.
    """
    out: list[dict] = []
    last_assistant_content = ""
    for record in records:
        # Track the most recent assistant content so an episode_end can inject
        # it as its final_output (self-contained default).
        if record.get("type") == "raw_assistant":
            content = (record.get("message") or {}).get("content", "")
            if content:
                last_assistant_content = content
        row = _adapt_one(
            record,
            last_assistant_content=last_assistant_content,
            final_output_override=final_output_override,
        )
        if row is not None:
            out.append(row)
    return out


def load_session_records(paths: "list[Path]") -> "list[dict]":
    """Read and concatenate JSONL session files in the given order.

    Blank lines and malformed JSON lines are skipped (mirrors the official
    gate's lenient ``_load_trajectory_events``).  Callers are responsible for
    ordering ``paths`` (see :func:`run_jsonls_in_dir`); this only concatenates.
    """
    rows: list[dict] = []
    for path in paths:
        try:
            with Path(path).open(encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rows.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        except OSError:
            continue
    return rows


def run_jsonls_in_dir(session_dir: Path) -> "list[Path]":
    """Return the ``<run_id>.jsonl`` segment files in a session dir, ordered.

    A single session dir may hold multiple ``<run_id>.jsonl`` files when the
    run rotated segments (compaction / handoff).  ``*_trace.jsonl`` (execution
    metadata) and ``*_state.json`` are excluded.  Ordered by write time then
    name, which follows the append/rotation order for the common case; segment
    order is a best-effort reconstruction from the filesystem (the authoritative
    order lives in the session index, not consulted here).
    """
    session_dir = Path(session_dir)
    files = [
        p for p in session_dir.glob("*.jsonl")
        if not p.name.endswith("_trace.jsonl")
    ]

    def _key(p: Path):
        try:
            mtime = p.stat().st_mtime_ns
        except OSError:
            mtime = 0
        return (mtime, p.name)

    return sorted(files, key=_key)


def session_dirs_for_task(sessions_root: Path, task_id: str) -> "list[Path]":
    """Find the session dir(s) for ``task_id`` under a ``sessions/`` root.

    Session dirs are named ``*-active-<task_id>``; the pass@2 second attempt
    lives in a sibling ``*-active-<task_id>-a2`` dir.  The primary attempt is
    returned before the ``-a2`` sibling.
    """
    sessions_root = Path(sessions_root)
    if not sessions_root.is_dir():
        return []
    tid = re.escape(task_id)
    # active-<task_id>  (primary)  |  active-<task_id>-a2  (pass@2)
    pat = re.compile(rf"active-{tid}(?P<a2>-a2)?$")
    primary: list[Path] = []
    a2: list[Path] = []
    for child in sessions_root.iterdir():
        if not child.is_dir():
            continue
        m = pat.search(child.name)
        if not m:
            continue
        (a2 if m.group("a2") else primary).append(child)
    return sorted(primary) + sorted(a2)


def adapt_task(sessions_root: Path, task_id: str, *,
               final_output_overrides: "dict[str, str] | None" = None,
               ) -> "list[dict]":
    """High-level: resolve ``task_id`` → session dir(s) → concatenated kind rows.

    Concatenates every rotated segment across the primary and pass@2 dirs, in
    order, then adapts.  ``final_output_overrides`` maps ``task_id`` → exact
    ``final_output`` string (precedence 1).  Returns ``[]`` when no session dir
    is found (the gate treats an empty task as zero coverage).
    """
    override = None
    if final_output_overrides:
        override = final_output_overrides.get(task_id)

    paths: list[Path] = []
    for sdir in session_dirs_for_task(sessions_root, task_id):
        paths.extend(run_jsonls_in_dir(sdir))
    records = load_session_records(paths)
    return adapt_records(records, final_output_override=override)


def adapt_jsonl_file(path: Path, *,
                     final_output_override: str | None = None) -> "list[dict]":
    """Convenience: adapt a single session JSONL file into kind rows.

    Used by tests that pin a copied real-producer fixture; not on the gate's
    hot path.
    """
    records = load_session_records([Path(path)])
    return adapt_records(records, final_output_override=final_output_override)


def make_session_rows_loader(sessions_root: Path, *,
                             final_output_overrides: "dict[str, str] | None" = None):
    """Return a ``rows_for_task(task_id) -> list[dict]`` closure over a root.

    This is the seam the gate consumes: :func:`counterfactual_gate.
    check_counterfactual_replay` accepts any ``rows_for_task`` callable, so tests
    can inject hand-built rows while production wiring passes this loader.
    """
    root = Path(sessions_root)

    def rows_for_task(task_id: str) -> "list[dict]":
        return adapt_task(root, task_id,
                          final_output_overrides=final_output_overrides)

    return rows_for_task
