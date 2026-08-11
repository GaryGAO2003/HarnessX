"""GHX-AEGIS Block A — graph-native Digester (transport-free).

Role
====
The Digester is the first stage of the B-arm meta-evolution loop.  It reads one
evaluation round's raw trajectories and compresses each **FAILED** task into a
typed, graph-grounded :class:`TaskDigest` that names the processor nodes
implicated in the failure (on-graph, in ``implicated_nodes``) alongside the
off-graph attribution families that have no node (in ``implicated_aspects``).
It is the graph-native re-expression of the AEGIS
Digester (HX reference — read-only:
``recipe/gaia_evolver/run_variant_pool.py::_LLMDigester``): every input and
output is re-cast onto the processor graph — the parent is handed to the model
as a :func:`harnessx.graph.bom.graph_bom` bill of materials, and the model's
implicated-node claims are validated against ``snapshot.nodes``.

Like :mod:`experiments.variant_pool.proposer`, this module is **transport-free**:
the caller injects a synchronous ``llm_call: Callable[[str], str]`` (LiteLLM,
Anthropic, a stub, a cassette) and this module only ever

    1. windows the trajectory + summarizes the parent graph into a prompt,
    2. hands that prompt to the injected ``llm_call``,
    3. robustly extracts a typed :class:`TaskDigest` from the reply.

No provider dependency, no gating, no scoring re-derivation — the harness'
pass/fail verdict is ground truth the model interprets, never re-judges.

Polarity — READ THIS BEFORE EDITING ``extract_digest``
======================================================
Extraction is **filter-and-keep**, not reject-on-suspicion.  A structurally
usable reply (parseable JSON object, all five contract keys present, ``task_id``
echoed back) is always KEPT; only genuinely unusable replies (parse failure,
non-object, a missing key, a ``task_id`` mismatch) are dropped to an error row.
Within a kept digest the model's *claims* are sanitized rather than trusted
wholesale:

    * ``implicated_nodes`` is filtered against ``snapshot.nodes`` — any id not on
      the parent graph is a hallucination: it is dropped and recorded in
      ``warnings``.  If **every** id is hallucinated the digest still survives
      with ``implicated_nodes=()`` (the failure_category + evidence are still
      signal).
    * ``implicated_aspects`` carries the OFF-GRAPH attribution families
      (``tool:<name>``, ``prompt:<section>``, ``environment``,
      ``model_capability`` — the four HX ``implicated_components`` families that
      have no graph node).  Tokens outside that vocabulary, or a prefixed family
      with an empty suffix, are dropped into ``warnings``; the HX slash
      spellings (``tools/x``, ``prompt/x``) are normalized to the colon form;
      and a stray ``processor/<name>`` that resolves to a parent ``proc:`` node
      is MOVED onto ``implicated_nodes`` (recorded in ``warnings``) rather than
      dropped.
    * ``evidence`` is truncated (at most 6 items, each ≤ 200 chars); ``notes``
      is one short free-form string (stripped, ≤ 200 chars).

The per-task digest carries NO actionability — that signal is round-level (the
paper's Algorithm 1 a_t), produced separately by
:func:`run_round_actionability` over the round's digests and never re-judged per
task.

Rationale: a digest carrying one bad node reference still carries a real
category and real evidence the meta-loop needs; discarding it wholesale would
throw away that signal.  Every function never raises — a transport exception or
a malformed reply degrades to an ``errors`` row (or a ``(None, error)`` pair for
the round signal), never a dead round.

Anti-contamination
==================
The grader's gold answer must never enter the meta-evolution loop.
:func:`build_digester_prompt` surfaces only neutral execution-outcome fields and
deliberately OMITS the record's ``expected`` (gold answer) and ``reason``
(grader verdict, which quotes the gold answer) — see the inline note there.

This module does NOT wire itself into rehearsal (that is Block D); it is a pure
library of transport-free functions.
"""

from __future__ import annotations

import json
import math
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from harnessx.graph.bom import graph_bom
from harnessx.graph.snapshot import _compute_slug
from harnessx.graph.types import GraphSnapshot

__all__ = [
    "TRAJ_FRONTMATTER_CAP",
    "TRAJ_HEAD_CAP",
    "TRAJ_TAIL_CAP",
    "TaskDigest",
    "RoundActionability",
    "DigesterResult",
    "build_digester_prompt",
    "extract_digest",
    "run_digester",
    "build_round_actionability_prompt",
    "extract_round_actionability",
    "run_round_actionability",
    "write_digests_jsonl",
]


# ── trajectory windowing (HX window experience values) ───────────────────────

#: Frontmatter / head / tail char caps, carried over from the HX ``_LLMDigester``
#: window so long trajectories are compressed the same way.
TRAJ_FRONTMATTER_CAP = 4_000
TRAJ_HEAD_CAP = 12_000
TRAJ_TAIL_CAP = 20_000

#: Per-item and count caps on the extracted ``evidence`` list.
_EVIDENCE_CHAR_CAP = 200
_EVIDENCE_MAX_ITEMS = 6

#: Char cap on the free-form ``notes`` string.
_NOTES_CHAR_CAP = 200

#: The exact keys the per-task digest model reply must return (order-independent).
_REQUIRED_KEYS = (
    "task_id",
    "failure_category",
    "implicated_nodes",
    "implicated_aspects",
    "evidence",
    "notes",
)

#: OFF-GRAPH attribution vocabulary for ``implicated_aspects`` — the HX
#: ``implicated_components`` families that have no graph node.  ``environment``
#: and ``model_capability`` are bare words; ``tool:`` and ``prompt:`` require a
#: non-empty suffix after the colon.  ``processor/<name>`` is deliberately NOT
#: here: it belongs on the graph and is routed to ``implicated_nodes`` instead.
_BARE_ASPECTS = frozenset({"environment", "model_capability"})
_PREFIX_ASPECTS = ("tool:", "prompt:")

#: A trajectory ``.md`` opens with a ``---``-delimited YAML frontmatter block
#: (recipe.gaia_evolver.run._render_trajectory_frontmatter). Non-greedy so it
#: stops at the FIRST closing ``---``.
_FRONTMATTER_RE = re.compile(r"^---\n.*?\n---\n?", re.DOTALL)

_MISSING = object()


def _trajectory_window(text: Any) -> "tuple[str, str, str]":
    """Split a trajectory ``.md`` into ``(frontmatter, head, tail)`` windows.

    ``frontmatter`` is the leading ``---``…``---`` block (truncated to
    :data:`TRAJ_FRONTMATTER_CAP`); ``head``/``tail`` are the first
    :data:`TRAJ_HEAD_CAP` / last :data:`TRAJ_TAIL_CAP` chars of the body.  When
    the body is short enough to fit whole (``len ≤ head_cap + tail_cap``) it is
    returned as ``head`` and ``tail`` is empty — head and tail NEVER overlap, so
    no body content is ever duplicated.  Never raises.
    """
    if not isinstance(text, str):
        return "", "", ""
    match = _FRONTMATTER_RE.match(text)
    if match is None:
        frontmatter, body = "", text
    else:
        frontmatter = text[: match.end()].rstrip("\n")
        body = text[match.end() :].lstrip("\n")
    frontmatter = frontmatter[:TRAJ_FRONTMATTER_CAP]
    if len(body) <= TRAJ_HEAD_CAP + TRAJ_TAIL_CAP:
        return frontmatter, body, ""
    return frontmatter, body[:TRAJ_HEAD_CAP], body[-TRAJ_TAIL_CAP:]


# ── prompt assembly ──────────────────────────────────────────────────────────


def build_digester_prompt(
    record: dict,
    trajectory_text: str,
    snapshot: GraphSnapshot,
    *,
    retry_error: "str | None" = None,
) -> str:
    """Assemble the per-task Digester prompt from one failed record + trajectory.

    Sections mirror :func:`experiments.variant_pool.proposer.build_proposer_prompt`:
    a compact-JSON parent BOM (:func:`graph_bom`), the neutral task outcome, the
    task question, the three trajectory windows, and a hard output contract
    (single JSON object, EXACTLY the six contract keys, ``implicated_nodes``
    drawn from the BOM proc: ids, ``implicated_aspects`` drawn from the
    off-graph vocabulary, evidence citing concrete facts, notes a short
    string).  ``retry_error`` appends a correction section instructing a clean
    re-emit.
    """
    bom_json = json.dumps(
        graph_bom(snapshot), ensure_ascii=False, separators=(",", ":")
    )
    frontmatter, head, tail = _trajectory_window(trajectory_text)

    # ANTI-CONTAMINATION — DO NOT surface record["expected"] or record["reason"]:
    # both carry the grader's GOLD ANSWER, and this digest feeds the meta-loop;
    # leaking the answer into that loop contaminates every downstream candidate.
    # Only the neutral execution-outcome fields below may enter the prompt.
    outcome = {
        "task_id": record.get("task_id", ""),
        "level": record.get("level"),
        "exit_reason": record.get("exit_reason", ""),
        "steps": record.get("steps"),
        "cost_usd": record.get("cost_usd"),
        "n_pass": record.get("n_pass"),
        "n_att": record.get("n_att"),
    }
    outcome_json = json.dumps(outcome, ensure_ascii=False, separators=(",", ":"))
    question = str(record.get("question", "") or "")
    task_id = str(record.get("task_id", ""))

    prompt = (
        "You are the Digester in a processor-graph agent harness. A benchmark "
        "task was executed by the agent and the harness scored it as FAILED. "
        "That pass/fail outcome is GROUND TRUTH — do NOT re-judge it. Your job "
        "is to attribute this one failed run to concrete processor nodes on the "
        "parent graph and compress the trajectory evidence into a structured "
        "digest.\n\n"
        "## Parent graph (bill of materials)\n"
        f"{bom_json}\n\n"
        "## Task outcome\n"
        f"{outcome_json}\n\n"
        "## Task question\n"
        f"{question}\n\n"
        "## Trajectory frontmatter\n"
        f"{frontmatter}\n\n"
        f"## Trajectory head (first {TRAJ_HEAD_CAP} chars of body)\n"
        f"{head}\n\n"
        f"## Trajectory tail (last {TRAJ_TAIL_CAP} chars of body)\n"
        f"{tail}\n\n"
        "## Output contract (hard requirements)\n"
        "Return ONLY a single JSON object with EXACTLY these keys: "
        "task_id, failure_category, implicated_nodes, implicated_aspects, "
        "evidence, notes.\n"
        f"  - \"task_id\": copy back verbatim: {task_id!r}.\n"
        "  - \"failure_category\": ONE short snake_case label (e.g. "
        "blocked_source, reasoning_error, tool_output_dropped, "
        "budget_exhausted).\n"
        "  - \"implicated_nodes\": a list (possibly empty) of processor node ids "
        "that appear as \"proc:\"-prefixed node_ids in the bill of materials "
        "above; every id MUST be one present there.\n"
        "  - \"implicated_aspects\": a list (possibly empty) of OFF-GRAPH failure "
        "attributions, each from EXACTLY this vocabulary: \"environment\" "
        "(environment / network / sandbox friction), \"model_capability\" (a "
        "ceiling of the model's own ability, not fixable by editing the "
        "harness), \"tool:<name>\" (a specific tool's behavior), or "
        "\"prompt:<section>\" (a specific prompt section). Do NOT put processor "
        "nodes here — those belong in implicated_nodes.\n"
        "  - \"evidence\": a list of AT MOST 6 short strings, each citing a "
        "concrete fact from the trajectory or the task outcome (a step, a tool "
        "result, an exit reason). Never invent quotes.\n"
        "  - \"notes\": one short sentence of extra context, or an empty "
        "string.\n"
        "Output no prose, no markdown, no code fences, and nothing outside the "
        "JSON object."
    )

    if retry_error:
        prompt += (
            "\n\n## Correction (your previous output failed to parse)\n"
            f"{retry_error}\n"
            "Return ONLY a single valid JSON object with exactly the six keys "
            "above and nothing else."
        )
    return prompt


# ── typed digest + extraction (never-raise, filter-and-keep) ──────────────────


@dataclass(frozen=True)
class TaskDigest:
    """A structured, graph-grounded interpretation of one failed task run.

    ``implicated_nodes`` are node ids proven to exist in the parent
    ``snapshot.nodes`` (the on-graph attribution); ``implicated_aspects`` are
    the off-graph attribution families (``tool:<name>``, ``prompt:<section>``,
    ``environment``, ``model_capability``).  ``notes`` is one short free-form
    string.  ``warnings`` records every claim that was sanitized away (e.g. a
    hallucinated node id dropped, an out-of-vocabulary aspect dropped, or a
    ``processor/<name>`` aspect moved onto ``implicated_nodes``).

    There is deliberately NO per-task ``actionability`` field: actionability is
    a round-level signal (see :class:`RoundActionability`).
    """

    task_id: str
    failure_category: str
    implicated_nodes: "tuple[str, ...]"
    implicated_aspects: "tuple[str, ...]"
    evidence: "tuple[str, ...]"
    notes: str = ""
    warnings: "tuple[str, ...]" = ()


def _first_json_object(text: str) -> "str | None":
    """The first balanced ``{...}`` block in ``text`` (string-aware), or ``None``.

    A brace scanner rather than a greedy first-``{``/last-``}`` slice, so an
    object embedded in prose or wrapped in a ```json fence is recovered and
    trailing text does not defeat the strict ``json.loads`` the caller runs.
    """
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    in_str = False
    escape = False
    for index in range(start, len(text)):
        char = text[index]
        if in_str:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_str = False
        elif char == '"':
            in_str = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    return None


def _load_object(raw: str) -> "tuple[dict | None, str]":
    """Recover a single JSON object from an LLM reply, or ``(None, error)``.

    Tries the whole reply first so a top-level array / scalar can be named as a
    non-object error; otherwise falls back to the first balanced ``{...}`` block
    (handles fenced objects and objects embedded in prose).
    """
    text = raw.strip()
    try:
        whole = json.loads(text)
    except (ValueError, TypeError):
        whole = _MISSING
    if whole is not _MISSING:
        if isinstance(whole, dict):
            return whole, ""
        return None, f"top-level JSON value is {type(whole).__name__}, not an object"

    block = _first_json_object(text)
    if block is None:
        return None, "no JSON object found in digest output"
    try:
        parsed = json.loads(block)
    except (ValueError, TypeError) as exc:
        return None, f"json.loads failed: {exc}"
    if not isinstance(parsed, dict):
        return None, f"top-level JSON value is {type(parsed).__name__}, not an object"
    return parsed, ""


def _sanitize_aspects(
    raw_aspects: Any, valid_nodes: "set[str]"
) -> "tuple[list[str], list[str], list[str]]":
    """Split model aspect claims into ``(aspects, nodes_to_move, warnings)``.

    Legal off-graph families are ``tool:<name>``, ``prompt:<section>``,
    ``environment`` and ``model_capability`` (:data:`_BARE_ASPECTS` /
    :data:`_PREFIX_ASPECTS`).  Filter-and-keep polarity: every rejected token is
    recorded in ``warnings`` and the digest is still kept.  Three graph-native
    normalizations are applied first:

      * the HX slash spellings ``tools/<name>`` / ``prompt/<section>`` a model
        may echo from the reference prompt are rewritten to the colon form;
      * a ``processor/<name>`` claim belongs on the graph, not here — ``name``
        is slugged with :func:`_compute_slug` (so both a class name like
        ``CostGuardProcessor`` and the snake id ``cost_guard_processor`` resolve)
        and, when ``proc:<slug>`` is a real node on the parent, it is MOVED into
        ``nodes_to_move`` (a warning explains the move), otherwise dropped;
      * a prefixed family with an empty suffix (``tool:`` / ``prompt:``) is
        dropped.
    """
    aspects: "list[str]" = []
    moved_nodes: "list[str]" = []
    warnings: "list[str]" = []
    if not isinstance(raw_aspects, list):
        raw_aspects = []
    for item in raw_aspects:
        token = str(item).strip()
        if not token:
            continue
        # ``processor/<name>`` is on-graph: route it to implicated_nodes if it
        # names a real proc: node, else drop it.
        if token.startswith("processor/"):
            name = token[len("processor/") :].strip()
            core = name[len("proc:") :].strip() if name.startswith("proc:") else name
            candidate = f"proc:{_compute_slug(core)}" if core else ""
            if candidate and candidate in valid_nodes:
                if candidate not in moved_nodes:
                    moved_nodes.append(candidate)
                warnings.append(
                    f"moved processor aspect onto implicated_nodes: "
                    f"{token} -> {candidate}"
                )
            else:
                warnings.append(f"dropped unresolved processor aspect: {token}")
            continue
        # Normalize the HX slash spellings to the graph's colon form.
        if token.startswith("tools/"):
            token = "tool:" + token[len("tools/") :]
        elif token.startswith("prompt/"):
            token = "prompt:" + token[len("prompt/") :]
        if token in _BARE_ASPECTS:
            if token not in aspects:
                aspects.append(token)
            continue
        matched_prefix = next(
            (p for p in _PREFIX_ASPECTS if token.startswith(p)), None
        )
        if matched_prefix is None:
            warnings.append(f"dropped aspect not in vocabulary: {token}")
        elif token[len(matched_prefix) :].strip():
            if token not in aspects:
                aspects.append(token)
        else:
            warnings.append(f"dropped aspect with empty suffix: {token}")
    return aspects, moved_nodes, warnings


def extract_digest(
    raw: Any,
    *,
    expected_task_id: str,
    snapshot: GraphSnapshot,
) -> "tuple[TaskDigest | None, str]":
    """Extract a :class:`TaskDigest` from an LLM reply — never raises.

    Drops to ``(None, error)`` only for structurally unusable replies (empty /
    non-string, no JSON object, a non-object top-level value, a missing contract
    key, or a ``task_id`` that does not echo ``expected_task_id``).  Everything
    else is KEPT and sanitized per the module's filter-and-keep polarity:
    hallucinated ``implicated_nodes`` (ids absent from ``snapshot.nodes``) are
    dropped into ``warnings``, ``implicated_aspects`` are filtered to the
    off-graph vocabulary (a ``processor/<name>`` that resolves to a parent node
    is moved onto ``implicated_nodes``), ``evidence`` is capped, and ``notes``
    is stripped and truncated.
    """
    if not isinstance(raw, str) or not raw.strip():
        return None, "empty or non-string digest output"

    obj, error = _load_object(raw)
    if obj is None:
        return None, error

    missing = [key for key in _REQUIRED_KEYS if key not in obj]
    if missing:
        return None, f"missing required keys: {', '.join(missing)}"

    task_id = str(obj.get("task_id", "")).strip()
    if task_id != str(expected_task_id).strip():
        return None, (
            f"task_id mismatch: got {task_id!r}, expected {str(expected_task_id)!r}"
        )

    failure_category = (
        str(obj.get("failure_category") or "").strip() or "uncategorized_failure"
    )

    # Filter-and-keep: only ids proven to exist on the parent graph survive;
    # every hallucinated id is dropped into ``warnings`` (the digest is kept).
    valid_nodes = set(getattr(snapshot, "nodes", {}) or {})
    implicated: "list[str]" = []
    warnings: "list[str]" = []
    raw_nodes = obj.get("implicated_nodes")
    if not isinstance(raw_nodes, list):
        raw_nodes = []
    for item in raw_nodes:
        nid = str(item).strip()
        if not nid:
            continue
        if nid in valid_nodes:
            if nid not in implicated:
                implicated.append(nid)
        else:
            warnings.append(f"dropped hallucinated node not in graph: {nid}")

    # Off-graph aspects: filter to the vocabulary, and route any on-graph
    # ``processor/<name>`` claim onto ``implicated_nodes`` when it resolves.
    aspects, moved_nodes, aspect_warnings = _sanitize_aspects(
        obj.get("implicated_aspects"), valid_nodes
    )
    for nid in moved_nodes:
        if nid not in implicated:
            implicated.append(nid)
    warnings.extend(aspect_warnings)

    raw_evidence = obj.get("evidence")
    if not isinstance(raw_evidence, list):
        raw_evidence = []
    evidence: "list[str]" = []
    for item in raw_evidence:
        piece = str(item).strip()[:_EVIDENCE_CHAR_CAP]
        # Order-preserving dedup BEFORE the count cap (HX parity, ``_map_task_digest``
        # ``if anchor not in anchors``): duplicates never consume a cap slot.
        if not piece or piece in evidence:
            continue
        evidence.append(piece)
        if len(evidence) >= _EVIDENCE_MAX_ITEMS:
            break

    notes = str(obj.get("notes") or "").strip()[:_NOTES_CHAR_CAP]

    digest = TaskDigest(
        task_id=str(expected_task_id),
        failure_category=failure_category,
        implicated_nodes=tuple(implicated),
        implicated_aspects=tuple(aspects),
        evidence=tuple(evidence),
        notes=notes,
        warnings=tuple(warnings),
    )
    return digest, ""


# ── round-level actionability (paper Algorithm 1 a_t) ────────────────────────
#
# A SECOND, distinct Digester role: after the per-task digests are produced, one
# more model call over their summaries emits the round-level actionability a_t —
# the selective-invocation signal ("is there at least one evidence-backed,
# harness-editable failure this round?"). It is transport-free like everything
# else here and is NOT auto-wired into ``run_digester``; the meta-loop (Block D)
# composes it. The extraction mirrors the HX reference ``_parse_round_json``
# HARD-REJECT contract verbatim: a bad / out-of-range actionability or a blank
# rationale yields ``(None, error)`` — never a clamped or defaulted value.


@dataclass(frozen=True)
class RoundActionability:
    """The round-level a_t signal: a clamped score plus a non-empty rationale."""

    actionability: float
    rationale: str


def _round_summary_line(digest: TaskDigest) -> str:
    """One graph-native summary line for a failed task's digest.

    Splits the HX ``components=[...]`` column into the graph-native
    ``nodes=[...]`` (on-graph) and ``aspects=[...]`` (off-graph) columns.
    """
    nodes = ", ".join(digest.implicated_nodes) or "none"
    aspects = ", ".join(digest.implicated_aspects) or "none"
    has_evidence = "yes" if digest.evidence else "no"
    return (
        f"- {digest.task_id}: category={digest.failure_category or 'unknown'}; "
        f"nodes=[{nodes}]; aspects=[{aspects}]; has_evidence={has_evidence}"
    )


def build_round_actionability_prompt(
    digests: "list[TaskDigest]",
    *,
    retry_error: "str | None" = None,
) -> str:
    """Assemble the round-level actionability prompt from this round's digests.

    Each digest is rendered as one :func:`_round_summary_line`; an empty round
    renders as ``(no unsolved tasks)``.  The contract demands EXACTLY two keys
    ``{actionability, rationale}`` with a non-empty rationale.  ``retry_error``
    appends a correction section instructing a clean re-emit.
    """
    if digests:
        summary = "\n".join(_round_summary_line(digest) for digest in digests)
    else:
        summary = "(no unsolved tasks)"

    prompt = (
        "You are the Digester in a processor-graph agent harness, now emitting "
        "the ROUND-LEVEL actionability signal a_t used for selective invocation "
        "(paper Algorithm 1). Below are the per-task failure summaries you "
        "produced for this round.\n\n"
        "Decide a_t in [0, 1]: is there AT LEAST ONE failure this round that has "
        "usable evidence AND points at a harness edit a future round could "
        "plausibly make (a processor node, a tool, a prompt section, or an "
        "environment fix)? Score HIGH when yes. Score LOW or zero when every "
        "failure is unaddressable by a harness edit — a pure model_capability "
        "ceiling, or a failure with no usable evidence — or when there is "
        "nothing to fix.\n\n"
        "## Per-task failure summaries this round\n"
        f"{summary}\n\n"
        "## Output contract (hard requirements)\n"
        "Return ONLY a single JSON object with EXACTLY these two keys: "
        "actionability, rationale.\n"
        "  - \"actionability\": a number in [0, 1].\n"
        "  - \"rationale\": one or two sentences justifying the value; it MUST "
        "be non-empty.\n"
        "Output no prose, no markdown, no code fences, and nothing outside the "
        "JSON object."
    )

    if retry_error:
        prompt += (
            "\n\n## Correction (your previous output failed to parse)\n"
            f"{retry_error}\n"
            "Return ONLY a single valid JSON object with exactly the two keys "
            "above and nothing else."
        )
    return prompt


def extract_round_actionability(
    raw: Any,
) -> "tuple[RoundActionability | None, str]":
    """Extract a :class:`RoundActionability` from an LLM reply — never raises.

    Reuses :func:`_load_object`, then applies the HX reference's HARD-REJECT
    contract verbatim (``_parse_round_json``): ``actionability`` must be a real
    (non-``bool``) number in ``[0, 1]`` — a missing / ``bool`` / non-numeric
    value is ``(None, "actionability must be a number in [0, 1]")`` and a
    non-finite or out-of-range value is
    ``(None, f"actionability {raw!r} is out of range [0, 1]")``.  It is NEVER
    clamped and NEVER defaulted.  ``rationale`` must be a non-empty string.  The
    checks run actionability-then-rationale, matching the reference order.
    """
    if not isinstance(raw, str) or not raw.strip():
        return None, "empty or non-string round actionability output"

    obj, error = _load_object(raw)
    if obj is None:
        return None, error

    value_raw = obj.get("actionability")
    if isinstance(value_raw, bool) or not isinstance(value_raw, (int, float)):
        return None, "actionability must be a number in [0, 1]"
    value = float(value_raw)
    if not math.isfinite(value) or not 0.0 <= value <= 1.0:
        return None, f"actionability {value_raw!r} is out of range [0, 1]"

    rationale = obj.get("rationale")
    if not isinstance(rationale, str) or not rationale.strip():
        return None, "rationale must be a non-empty string"

    return RoundActionability(actionability=value, rationale=rationale.strip()), ""


def run_round_actionability(
    digests: "list[TaskDigest]",
    llm_call: "Callable[[str], str]",
) -> "tuple[RoundActionability | None, str, int]":
    """Drive the round-level actionability call — returns ``(result, error, n)``.

    One ``llm_call``; a parse failure retries ONCE with the parse error fed
    back.  Returns ``(RoundActionability, "", n_calls)`` on success,
    ``(None, last_error, n_calls)`` on a second parse failure, and
    ``(None, "llm_call raised: ...", n_calls)`` if the transport raises (never
    propagated).  ``n`` counts every invocation, including one that raised.
    """
    last_error = ""
    n_calls = 0
    for attempt in range(2):
        retry_error = last_error if attempt else None
        prompt = build_round_actionability_prompt(digests, retry_error=retry_error)
        n_calls += 1
        try:
            raw = llm_call(prompt)
        except Exception as exc:  # noqa: BLE001 — transport must not kill the round
            return None, f"llm_call raised: {type(exc).__name__}: {exc}", n_calls
        result, last_error = extract_round_actionability(raw)
        if result is not None:
            return result, "", n_calls
    return None, last_error, n_calls


# ── round driver ─────────────────────────────────────────────────────────────


@dataclass
class DigesterResult:
    """Outcome of digesting one evaluation round.

    ``n_failed_tasks`` is the total number of FAILED rows in ``records`` (BEFORE
    any ``max_tasks`` cap) — a truthful round statistic independent of how many
    were actually digested.  ``n_calls`` counts every ``llm_call`` invocation
    (including ones that raised).  ``errors`` rows are ``{"task_id", "error"}``.
    """

    digests: "list[TaskDigest]" = field(default_factory=list)
    errors: "list[dict]" = field(default_factory=list)
    n_failed_tasks: int = 0
    n_calls: int = 0


def _load_trajectory(
    trajectories_dir: Path, record: dict, task_id: str
) -> "str | None":
    """Locate + read a task's trajectory ``.md``, or ``None`` if unreadable.

    Tries the basename of ``record["trajectory_file"]`` under
    ``trajectories_dir`` first, then falls back to ``{task_id}.md``.
    """
    candidates: "list[Path]" = []
    traj_file = record.get("trajectory_file")
    if isinstance(traj_file, str) and traj_file.strip():
        candidates.append(trajectories_dir / Path(traj_file).name)
    if task_id:
        fallback = trajectories_dir / f"{task_id}.md"
        if fallback not in candidates:
            candidates.append(fallback)
    for path in candidates:
        if path.is_file():
            try:
                return path.read_text(encoding="utf-8")
            except OSError:
                continue
    return None


def run_digester(
    records: "list[dict]",
    trajectories_dir: Any,
    snapshot: GraphSnapshot,
    llm_call: "Callable[[str], str]",
    *,
    max_tasks: "int | None" = None,
) -> DigesterResult:
    """Digest every FAILED task in ``records`` into a :class:`TaskDigest`.

    One ``llm_call`` per task; a parse failure retries ONCE with the parse error
    fed back, and a second failure records an ``errors`` row.  A ``llm_call``
    that raises records an error and continues to the next task (a transport
    failure never kills the round).  A task whose trajectory cannot be read is
    recorded as an error and skipped without any model call.  ``max_tasks`` caps
    how many failed tasks are digested (the earliest in ``records`` order).
    """
    trajectories_dir = Path(trajectories_dir)
    failed = [record for record in records if not record.get("passed", False)]
    result = DigesterResult(n_failed_tasks=len(failed))

    to_digest = failed if max_tasks is None else failed[:max_tasks]
    for record in to_digest:
        task_id = str(record.get("task_id") or "")
        trajectory_text = _load_trajectory(trajectories_dir, record, task_id)
        if trajectory_text is None:
            result.errors.append(
                {"task_id": task_id, "error": "trajectory file not found"}
            )
            continue

        digest: "TaskDigest | None" = None
        last_error = ""
        transport_error: "str | None" = None
        for attempt in range(2):
            retry_error = last_error if attempt else None
            prompt = build_digester_prompt(
                record, trajectory_text, snapshot, retry_error=retry_error
            )
            result.n_calls += 1
            try:
                raw = llm_call(prompt)
            except Exception as exc:  # noqa: BLE001 — transport must not kill the round
                transport_error = f"{type(exc).__name__}: {exc}"
                break
            digest, last_error = extract_digest(
                raw, expected_task_id=task_id, snapshot=snapshot
            )
            if digest is not None:
                break

        if digest is not None:
            result.digests.append(digest)
        elif transport_error is not None:
            result.errors.append(
                {"task_id": task_id, "error": f"llm_call raised: {transport_error}"}
            )
        else:
            result.errors.append(
                {
                    "task_id": task_id,
                    "error": f"digest parse failed after retry: {last_error}",
                }
            )
    return result


# ── persistence ──────────────────────────────────────────────────────────────


def write_digests_jsonl(
    path: Any,
    round_id: str,
    digests: "list[TaskDigest]",
    errors: "list[dict]",
    model: str,
    *,
    round_actionability: "RoundActionability | None" = None,
) -> None:
    """Write ``digests`` + ``errors`` as one JSON object per line.

    Digest rows carry ``record_kind="digest"``, error rows
    ``record_kind="digest_error"``; every row is stamped with ``round_id``,
    ``model`` and ``created_at`` (wall-clock seconds).  When
    ``round_actionability`` is supplied, ONE trailing
    ``record_kind="round_actionability"`` row (the round's a_t + rationale) is
    appended after the digest and error rows.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    created_at = time.time()
    with path.open("w", encoding="utf-8") as handle:
        for digest in digests:
            row = {
                "record_kind": "digest",
                "round_id": round_id,
                "model": model,
                "created_at": created_at,
                "task_id": digest.task_id,
                "failure_category": digest.failure_category,
                "implicated_nodes": list(digest.implicated_nodes),
                "implicated_aspects": list(digest.implicated_aspects),
                "evidence": list(digest.evidence),
                "notes": digest.notes,
                "warnings": list(digest.warnings),
            }
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        for error in errors:
            row = {
                "record_kind": "digest_error",
                "round_id": round_id,
                "model": model,
                "created_at": created_at,
                "task_id": error.get("task_id", ""),
                "error": error.get("error", ""),
            }
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        if round_actionability is not None:
            row = {
                "record_kind": "round_actionability",
                "round_id": round_id,
                "model": model,
                "created_at": created_at,
                "actionability": round_actionability.actionability,
                "rationale": round_actionability.rationale,
            }
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
