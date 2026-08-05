# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""Layer A -- mechanical trace-fact extraction (batch-4a Item 1).

Ported from ``upstream/feat/aegis:harnessx/aegis/stages/trace_facts.py`` and
adapted to OUR landed session JSONL schema (record ``type`` values
``raw_assistant`` / ``raw_tool`` / ``episode_end``; message shapes documented in
:mod:`experiments.variant_pool.event_replay`). The extractor reads a task's
session segments and returns structured, deterministic facts (tool-call shape
table, exit summary, repeated-call runs, tool-burst heuristic, tool-effect
shortlist). The Digester LLM receives :meth:`TraceFacts.to_markdown` verbatim
and is told NOT to rewrite it (Layer A); the model's interpretation (Layer C)
builds on top.

Reuse (not re-derivation): parsing goes through
:func:`event_replay.load_session_records` and the assistant/tool linkage through
:func:`event_replay.adapt_records` (the ``after_model`` / ``after_tool`` kind
rows), so this module inherits event_replay's single source of truth for how
OUR records flatten. Only the ``episode_end`` exit fields (``exit_reason`` /
``total_steps`` / ``passed``) are read from the raw records, because
``adapt_records`` intentionally drops the non-gate ``passed`` field from its
``task_end`` row.

Degradations vs the official extractor (honest, documented):

* **No multimodal class.** The official ``return_type`` includes ``multimodal``
  (structured image/file content blocks) and ``multimodal_coerced``. OUR stream
  carries ``message.content`` as a plain string with no ``content_blocks`` (see
  event_replay's schema notes), so ``multimodal`` can never be detected and is
  dropped; the short marker-only text case the official labels
  ``multimodal_coerced`` is reported here as ``short_marker``. The emitted
  ``return_type`` set is therefore ``{text, short_marker, empty, error,
  missing}``.
* **Off-loaded tool outputs read as empty.** The journal externalizes large
  ``raw_tool`` results to ``tool_results/<id>.txt`` sidecars and leaves the
  inline ``message.content`` empty; event_replay does not re-inline them, so a
  large tool result appears here with ``return_type=empty`` and
  ``return_len=0`` (and ``next_uses_result=None`` -- too short to tell). This
  mirrors event_replay's stated fidelity caveat.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

from experiments.variant_pool.event_replay import adapt_records, load_session_records

# Short marker strings the official uses to spot content that has been coerced
# down to a placeholder. Kept for parity; matched case-insensitively.
_MULTIMODAL_MARKERS = (
    "[image displayed below]",
    "image displayed below",
    "[content omitted",
    "content omitted for length",
    "<image>",
    "<file>",
)
_MULTIMODAL_MARKERS_LOWER = tuple(m.lower() for m in _MULTIMODAL_MARKERS)

# Thresholds chosen so a healthy multi-tool trajectory (typically <= 5-10 calls
# of any one tool, <= 3 in any single step) never trips, but the observed
# loop-trap shape (e.g. 75 SmartFetch in 19 steps with peak ~30 in step 1)
# reliably does. Kept identical to the official extractor.
_BURST_TOTAL_MIN = 20
_BURST_PEAK_MIN = 10


def _rollout_tag(path: Path) -> str:
    """Extract 'rN' from a cleaned session filename like '<task>_r3.jsonl'.

    OUR landed fixtures are not named with the official ``_rN`` suffix, so this
    falls back to the file stem (one rollout per file), which is a stable,
    unique tag for the tables/anchors.
    """
    stem = path.name.replace(".jsonl", "")
    if "_r" in stem:
        return "r" + stem.rsplit("_r", 1)[1]
    return stem


def _args_sha(args: object) -> str:
    try:
        blob = json.dumps(args, sort_keys=True, ensure_ascii=False, default=str)
    except Exception:  # noqa: BLE001 - repr fallback keeps the sha deterministic
        blob = repr(args)
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:8]


def _content_to_text(content: object) -> str:
    """Flatten assistant/tool content to a plain string.

    OUR stream carries ``content`` as a string; the list-of-blocks branch is
    kept defensively (and mirrors the official flattener) so a future stream
    that carried structured blocks would degrade to ``<image>`` / ``<file>``
    placeholder text rather than crash.
    """
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for blk in content:
            if isinstance(blk, dict):
                if blk.get("type") in ("image", "file", "image_url", "input_image"):
                    parts.append(f"<{blk.get('type')}>")
                else:
                    parts.append(str(blk.get("text") or blk.get("content") or ""))
            else:
                parts.append(str(blk))
        return "\n".join(parts)
    return str(content)


def _classify_return(text: str) -> str:
    """``text`` / ``short_marker`` / ``empty`` / ``error`` (see module docstring).

    The official ``multimodal`` / ``multimodal_coerced`` classes collapse here:
    with no content blocks in OUR stream ``multimodal`` cannot occur, and the
    short marker-only case is reported as ``short_marker``.
    """
    if not text.strip():
        return "empty"
    low = text.strip().lower()
    if low.startswith("error") or "traceback" in low[:200]:
        return "error"
    if len(text) < 120 and any(m in low for m in _MULTIMODAL_MARKERS_LOWER):
        return "short_marker"
    return "text"


@dataclass
class ToolCallFact:
    rollout: str
    step: int
    tool: str
    args_sha: str
    args_preview: str  # short JSON-preview of args, <= 120 chars
    return_type: str  # text | short_marker | empty | error | missing
    return_len: int
    next_uses_result: bool | None  # heuristic; None if no next assistant step

    @property
    def anchor(self) -> str:
        return f"trajectories/<file_for_{self.rollout}>#step_{self.step}"


@dataclass
class ExitFact:
    rollout: str
    exit_reason: str
    total_steps: int
    passed: bool | None
    terminal_snippet: str  # last 200 chars of last assistant content


@dataclass
class RepeatRun:
    rollout: str
    tool: str
    args_sha: str
    steps: list[int]


@dataclass
class ToolBurst:
    """A single tool was hammered in one rollout -- likely a loop trap.

    ``RepeatRun`` only fires when args are byte-identical between calls (and no
    other tool intervenes). That misses the common case where a tool is called
    many times with *different* args (e.g. SmartFetch 75x across 30 URLs).
    Bursts are detected from aggregate counts so that pattern is surfaced.
    """

    rollout: str
    tool: str
    total_calls: int  # whole-rollout total
    max_calls_in_one_step: int  # peak iterated count within one step
    peak_step: int  # step where the peak happened
    severity: str  # "high" if total >= 30 or peak >= 15, else "medium"


@dataclass
class TraceFacts:
    task_id: str
    rollouts: list[str] = field(default_factory=list)
    tool_calls: list[ToolCallFact] = field(default_factory=list)
    exits: list[ExitFact] = field(default_factory=list)
    repeats: list[RepeatRun] = field(default_factory=list)
    bursts: list[ToolBurst] = field(default_factory=list)
    trajectory_file_by_rollout: dict[str, str] = field(default_factory=dict)

    def anchor(self, rollout: str, step: int) -> str:
        fname = self.trajectory_file_by_rollout.get(rollout, f"<{rollout}>")
        return f"trajectories/{fname}#step_{step}"

    def to_markdown(self) -> str:
        """Render as a self-contained markdown section (official structure)."""
        lines: list[str] = []
        lines.append("## Trace Facts (Layer A — mechanical; do not rewrite)")
        lines.append("")
        lines.append(
            "> These facts were extracted deterministically from trajectory jsonl. "
            "Layer B (Pathology signals) and Layer C (Diagnosis) below should treat "
            "them as ground truth evidence."
        )
        lines.append("")

        # --- Exits ---
        lines.append("### Exits")
        lines.append("")
        lines.append("| rollout | exit_reason | steps | passed | terminal |")
        lines.append("|---|---|---|---|---|")
        for e in self.exits:
            passed_str = "—" if e.passed is None else ("yes" if e.passed else "no")
            snip = e.terminal_snippet.replace("|", "/").replace("\n", " ")[:160]
            if len(e.terminal_snippet) > 160:
                snip += "…"
            lines.append(
                f"| {e.rollout} | {e.exit_reason} | {e.total_steps} | {passed_str} | {snip} |"
            )
        if not self.exits:
            lines.append("| (no episode_end events) |  |  |  |  |")
        lines.append("")

        # --- Tool calls per rollout ---
        lines.append("### Tool calls")
        lines.append("")
        for rollout in self.rollouts:
            calls = [c for c in self.tool_calls if c.rollout == rollout]
            if not calls:
                lines.append(f"**{rollout}** — no tool calls.")
                lines.append("")
                continue
            lines.append(
                f"**{rollout}** → `trajectories/{self.trajectory_file_by_rollout.get(rollout, '')}`"
            )
            lines.append("")
            lines.append(
                "| step | tool | args_sha | args_preview | return_type | return_len | next_uses_result |"
            )
            lines.append("|---|---|---|---|---|---|---|")
            for c in calls:
                nu = "—" if c.next_uses_result is None else ("yes" if c.next_uses_result else "**NO**")
                prev = c.args_preview.replace("|", "/").replace("\n", " ")
                lines.append(
                    f"| {c.step} | `{c.tool}` | {c.args_sha} | {prev} | {c.return_type} | {c.return_len} | {nu} |"
                )
            lines.append("")

        # --- Repeated runs ---
        lines.append("### Repeated tool calls (same args, no new tool between)")
        lines.append("")
        if not self.repeats:
            lines.append("_None detected._")
        else:
            for r in self.repeats:
                anchor = self.anchor(r.rollout, r.steps[0])
                lines.append(
                    f"- **{r.rollout}** `{r.tool}` args_sha={r.args_sha} at steps {r.steps} — {anchor}"
                )
        lines.append("")

        # --- Tool bursts (suspected loop trap) ---
        lines.append("### Tool burst (suspected loop trap — same tool hammered)")
        lines.append("")
        if not self.bursts:
            lines.append("_None detected._")
        else:
            lines.append(
                "_Heuristic: a single tool called >=20 times in a rollout OR "
                ">=10 times concentrated in one step. The args may differ "
                "between calls (e.g. iterating URLs) so this is the loop "
                "pattern the consecutive-same-args repeat detector misses. "
                "If a newly-shipped tool appears here, treat it as evidence "
                "the candidate enabled budget burn rather than progress._"
            )
            lines.append("")
            for b in self.bursts:
                anchor = self.anchor(b.rollout, b.peak_step)
                lines.append(
                    f"- **{b.rollout}** `{b.tool}` total={b.total_calls}, "
                    f"peak={b.max_calls_in_one_step} at step {b.peak_step} "
                    f"(severity={b.severity}) — {anchor}"
                )
        lines.append("")

        # --- Tool-effect missing shortlist ---
        effect_missing = [c for c in self.tool_calls if c.next_uses_result is False]
        lines.append("### Tool calls whose output the next step did NOT reference")
        lines.append("")
        if not effect_missing:
            lines.append("_None detected._")
        else:
            lines.append(
                "_Heuristic: assistant message after the tool call does not contain "
                "any ≥20-char substring from the tool output. This is the single most "
                "common way a tool call 'fires but fails to affect the model' — "
                "e.g. when protocol flattening drops multimodal content._"
            )
            lines.append("")
            for c in effect_missing:
                lines.append(
                    f"- **{c.rollout}** step {c.step} `{c.tool}` "
                    f"(return_type={c.return_type}, len={c.return_len}) — "
                    f"{self.anchor(c.rollout, c.step)}"
                )
        lines.append("")

        return "\n".join(lines)


def _shares_substring(a: str, b: str, min_len: int = 20) -> bool:
    """True iff some length-``min_len`` substring of the shorter string appears
    in the longer one. Step=1 so no window is skipped."""
    if len(a) < min_len or len(b) < min_len:
        return False
    src, tgt = (a, b) if len(a) <= len(b) else (b, a)
    for i in range(len(src) - min_len + 1):
        if src[i : i + min_len] in tgt:
            return True
    return False


def extract_trace_facts(
    task_id: str,
    trajectory_paths: list[Path],
) -> TraceFacts:
    """Extract :class:`TraceFacts` for one task from its session JSONL segments.

    Each path is treated as a single rollout. Parsing reuses
    :func:`event_replay.load_session_records`; the assistant/tool linkage reuses
    :func:`event_replay.adapt_records` (``after_model`` / ``after_tool`` rows).
    """
    facts = TraceFacts(task_id=task_id)

    for path in trajectory_paths:
        rollout = _rollout_tag(path)
        facts.rollouts.append(rollout)
        facts.trajectory_file_by_rollout[rollout] = path.name

        records = load_session_records([path])
        rows = adapt_records(records)

        assistant_rows = [r for r in rows if r.get("kind") == "after_model"]
        assistant_texts = [_content_to_text(r.get("content")) for r in assistant_rows]
        # tool_call_id -> result text, built from the after_tool rows.
        tool_result_by_id: dict[str, str] = {}
        for r in rows:
            if r.get("kind") == "after_tool":
                tid = r.get("tool_call_id")
                if tid:
                    tool_result_by_id[tid] = _content_to_text(r.get("result"))

        per_rollout_calls: list[ToolCallFact] = []
        for idx, ev in enumerate(assistant_rows):
            tool_calls = ev.get("tool_calls") or []
            if not tool_calls:
                continue
            step = int(ev.get("step", idx))
            # The "next assistant text" is shared by all tool_calls in this event
            # (they all resolve before the next assistant step).
            next_asst_text = ""
            for later_text in assistant_texts[idx + 1 :]:
                if later_text.strip():
                    next_asst_text = later_text
                    break
            for tc in tool_calls:
                if not isinstance(tc, dict):
                    continue
                tool = tc.get("name") or "?"
                args = tc.get("input") or tc.get("arguments") or {}
                sha = _args_sha(args)
                try:
                    preview = json.dumps(args, ensure_ascii=False, default=str)[:120]
                except Exception:  # noqa: BLE001
                    preview = str(args)[:120]
                tid = tc.get("id") or ""
                if tid not in tool_result_by_id:
                    return_type = "missing"
                    return_len = 0
                    next_uses: bool | None = None
                else:
                    rtext = tool_result_by_id[tid]
                    return_type = _classify_return(rtext)
                    return_len = len(rtext)
                    if not next_asst_text:
                        next_uses = None
                    elif return_len < 20:
                        next_uses = None  # too short to tell
                    else:
                        next_uses = _shares_substring(rtext, next_asst_text, min_len=20)
                per_rollout_calls.append(
                    ToolCallFact(
                        rollout=rollout,
                        step=step,
                        tool=tool,
                        args_sha=sha,
                        args_preview=preview,
                        return_type=return_type,
                        return_len=return_len,
                        next_uses_result=next_uses,
                    )
                )

        facts.tool_calls.extend(per_rollout_calls)

        # Repeated-run detection: same (tool, args_sha) consecutive with no
        # different tool in between.
        run_tool: str | None = None
        run_sha: str | None = None
        run_steps: list[int] = []
        for c in per_rollout_calls:
            if c.tool == run_tool and c.args_sha == run_sha:
                run_steps.append(c.step)
            else:
                if run_tool is not None and len(run_steps) >= 2:
                    facts.repeats.append(
                        RepeatRun(rollout=rollout, tool=run_tool, args_sha=run_sha, steps=list(run_steps))
                    )
                run_tool, run_sha, run_steps = c.tool, c.args_sha, [c.step]
        if run_tool is not None and len(run_steps) >= 2:
            facts.repeats.append(
                RepeatRun(rollout=rollout, tool=run_tool, args_sha=run_sha, steps=list(run_steps))
            )

        # Burst detection: same tool called many times in one rollout, even when
        # args differ between calls (so the consecutive-same-args repeat detector
        # misses it). A trace with >=20 calls of one tool, OR >=10 calls of one
        # tool concentrated in a single step, is reported.
        per_tool_total: dict[str, int] = {}
        per_tool_step_max: dict[str, tuple[int, int]] = {}  # (peak_count, peak_step)
        per_tool_step_running: dict[tuple[str, int], int] = {}
        for c in per_rollout_calls:
            per_tool_total[c.tool] = per_tool_total.get(c.tool, 0) + 1
            key = (c.tool, c.step)
            per_tool_step_running[key] = per_tool_step_running.get(key, 0) + 1
            n = per_tool_step_running[key]
            cur = per_tool_step_max.get(c.tool)
            if cur is None or n > cur[0]:
                per_tool_step_max[c.tool] = (n, c.step)
        for tool, total in per_tool_total.items():
            peak, peak_step = per_tool_step_max.get(tool, (0, 0))
            if total < _BURST_TOTAL_MIN and peak < _BURST_PEAK_MIN:
                continue
            severity = "high" if (total >= 30 or peak >= 15) else "medium"
            facts.bursts.append(
                ToolBurst(
                    rollout=rollout,
                    tool=tool,
                    total_calls=total,
                    max_calls_in_one_step=peak,
                    peak_step=peak_step,
                    severity=severity,
                )
            )

        # Exit summary. adapt_records drops ``passed`` from its task_end row, so
        # the exit fields are read from the raw episode_end record.
        end_ev = next((e for e in records if e.get("type") == "episode_end"), None)
        last_asst_text = ""
        for text in reversed(assistant_texts):
            if text.strip():
                last_asst_text = text[-200:]
                break
        if end_ev is not None:
            facts.exits.append(
                ExitFact(
                    rollout=rollout,
                    exit_reason=str(end_ev.get("exit_reason", "?")),
                    total_steps=int(end_ev.get("total_steps", len(assistant_rows))),
                    passed=end_ev.get("passed"),
                    terminal_snippet=last_asst_text,
                )
            )
        else:
            facts.exits.append(
                ExitFact(
                    rollout=rollout,
                    exit_reason="unknown",
                    total_steps=len(assistant_rows),
                    passed=None,
                    terminal_snippet=last_asst_text,
                )
            )

    return facts
