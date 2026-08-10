"""B-arm proposer adapter — parent graph snapshot → LLM prompt → typed proposals.

This module is the *transport-free* bridge between an LLM and the shadow
evolution kernel.  It has **zero API / provider dependency**: the caller
injects a synchronous ``llm_call: Callable[[str], str]`` (any transport —
LiteLLM, Anthropic, a stub, a cassette) and this module only ever

    1. summarizes the parent graph + operator vocabulary into a prompt,
    2. hands that prompt to the injected ``llm_call``,
    3. robustly extracts a JSON array of proposals from the reply.

The extracted ``proposals`` are the exact input shape
:func:`experiments.variant_pool.shadow_evolution.run_shadow_round` expects —
each proposal is ``{"operator": <snake_case name>, "params": {...},
"rationale": "..."}``.  This adapter does **no gating**: the legality of any
single proposal is decided downstream by ``parse_proposal`` /
``run_shadow_round`` (typed-operator-only, forbidden-key rejection, S0–S4 +
Δ8 hard gate).  The extraction layer's sole job is "find the array"; a
malformed *element* is passed through verbatim so the kernel can reject it
with a concrete reason.

The prompt's stated output contract is bound to the parser's actual contract
by importing the same vocabulary (``PROPOSAL_OPERATORS``) and key sets
(``_ALLOWED_KEYS`` / ``_FORBIDDEN_KEYS``) the kernel enforces, so the prompt
can never advertise a shape the gate would not accept.  The operator params
schema is derived by *introspecting* the operator dataclasses, so adding or
changing an operator field updates the vocabulary automatically (single
source of truth).
"""

from __future__ import annotations

import json
from dataclasses import MISSING, dataclass, field, fields
from typing import Any, Callable

from harnessx.graph.bom import graph_bom
from harnessx.graph.types import GraphSnapshot

# The proposer's output contract IS the kernel's input contract: bind to the
# same vocabulary + key sets the gate enforces (read-only imports).
from experiments.variant_pool.shadow_evolution import (
    _ALLOWED_KEYS,
    _FORBIDDEN_KEYS,
    PROPOSAL_OPERATORS,
)

__all__ = [
    "ExtractionResult",
    "operator_vocabulary",
    "build_proposer_prompt",
    "extract_proposals",
    "llm_propose",
]


# ── operator vocabulary (introspected, single source) ────────────────────────

#: Hand-written, per-operator usage note keyed by the SAME snake_case names as
#: ``PROPOSAL_OPERATORS``.  Field names/types/defaults come from introspection;
#: only the human "what/why" of each operator lives here.
_OPERATOR_USAGE: "dict[str, str]" = {
    "mutate_processor_params": (
        "Change constructor kwargs of ONE existing processor node. "
        "node_id: id of a persistent PROCESSOR node from the BOM; "
        "param_changes: {ctor_kwarg: new_value} (no keys starting with '_' — "
        "those are ordering/metadata, use rewire_ordering)."
    ),
    "insert_processor": (
        "Insert a new serialized processor node. spec: a full serialized dict "
        "with a non-empty '_target_'; an optional '_singleton_group_' must not "
        "collide with a group already present in the BOM (use "
        "replace_same_singleton_group for that)."
    ),
    "remove_processor": (
        "Remove ONE existing persistent processor node (its incident edges go "
        "with it). node_id: id of a PROCESSOR node from the BOM."
    ),
    "replace_same_singleton_group": (
        "Replace a processor with an alternative from the SAME singleton group. "
        "node_id: existing PROCESSOR node; new_spec: serialized dict whose "
        "'_singleton_group_' equals the old node's group (ordering continuity)."
    ),
    "rewire_ordering": (
        "Change a processor's ordering metadata only. node_id: existing "
        "PROCESSOR node; order: new integer '_order_'; after: list of node "
        "ids/groups this must run after. Supply at least one of order/after."
    ),
    "swap_bundle": (
        "Swap a bundle's child subgraph for one with an IDENTICAL boundary "
        "signature. bundle_node_id: existing BUNDLE node; replacement_bundle_id: "
        "id of the replacement bundle; replacement_signature: must equal the "
        "current bundle's interface_signature."
    ),
}


def _type_str(annotation: Any) -> str:
    """Normalize a dataclass field annotation to a readable type string.

    Under ``from __future__ import annotations`` the operator fields carry
    string annotations; fields written as string literals (``"int | None"``)
    arrive double-quoted, so strip one layer of surrounding quotes.
    """
    if not isinstance(annotation, str):
        return getattr(annotation, "__name__", str(annotation))
    s = annotation.strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in ("'", '"'):
        s = s[1:-1]
    return s


def _params_schema(cls: type) -> "dict[str, dict]":
    """Introspect an operator dataclass into a JSON-ready params schema."""
    schema: "dict[str, dict]" = {}
    for f in fields(cls):
        entry: "dict[str, Any]" = {"type": _type_str(f.type)}
        if f.default is not MISSING:
            entry["required"] = False
            entry["default"] = f.default
        elif f.default_factory is not MISSING:      # type: ignore[misc]
            entry["required"] = False
            entry["default"] = f.default_factory()  # type: ignore[misc]
        else:
            entry["required"] = True
        schema[f.name] = entry
    return schema


def operator_vocabulary() -> "list[dict]":
    """Programmatically derived operator vocabulary (single source of truth).

    Field names/types/defaults are introspected from the operator dataclasses
    registered in ``PROPOSAL_OPERATORS`` — changing an operator updates the
    vocabulary automatically.  Each entry is
    ``{"operator": name, "params_schema": {...}, "usage": "..."}``.
    """
    return [
        {
            "operator": name,
            "params_schema": _params_schema(cls),
            "usage": _OPERATOR_USAGE.get(name, ""),
        }
        for name, cls in PROPOSAL_OPERATORS.items()
    ]


# ── prompt assembly ──────────────────────────────────────────────────────────


def build_proposer_prompt(
    snapshot: GraphSnapshot,
    *,
    max_candidates: int = 4,
    extra_context: str = "",
) -> str:
    """Assemble the proposer prompt from a parent snapshot.

    Sections: a compact-JSON parent BOM (``graph_bom``), the introspected
    operator vocabulary, and a hard output contract (JSON-array only, ≤
    ``max_candidates`` objects, keys restricted to operator/params/rationale,
    rationale must cite a BOM node id, forbidden keys / code payloads listed).
    ``extra_context`` (e.g. prior-round results injected by the runner) is
    appended verbatim.
    """
    bom_json = json.dumps(
        graph_bom(snapshot), ensure_ascii=False, separators=(",", ":")
    )

    # Tunable ctor params per processor node — without this the model cannot
    # ground mutate_processor_params and emits empty param_changes (observed
    # live: the only smoke proposal died at S0 "param_changes is empty").
    # Long values (prompt strings…) are truncated: the model needs the key
    # and the value's shape, not the full text.
    def _short(v: Any) -> Any:
        s = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False, default=str)
        return (s[:80] + "…") if len(s) > 80 else v

    tunable = {}
    for nid in sorted(snapshot.nodes):
        kwargs = snapshot.nodes[nid].metadata.get("_ctor_kwargs_") or {}
        if isinstance(kwargs, dict) and kwargs:
            tunable[nid] = {k: _short(v) for k, v in sorted(kwargs.items())}
    tunable_json = json.dumps(tunable, ensure_ascii=False, separators=(",", ":"))

    vocab = operator_vocabulary()
    op_lines = []
    for entry in vocab:
        schema_json = json.dumps(
            entry["params_schema"], ensure_ascii=False, separators=(",", ":")
        )
        op_lines.append(f"- {entry['operator']}: {entry['usage']}\n    params: {schema_json}")
    op_block = "\n".join(op_lines)

    allowed = ", ".join(sorted(_ALLOWED_KEYS))
    forbidden = ", ".join(sorted(_FORBIDDEN_KEYS))

    prompt = (
        "You propose deterministic edits to an LLM-agent processor graph. You "
        "are given the parent graph as a bill of materials (BOM) and a fixed "
        "vocabulary of typed operators. Propose graph edits ONLY as typed "
        "operator specifications.\n\n"
        "## Parent graph (bill of materials)\n"
        f"{bom_json}\n\n"
        "## Tunable constructor params per processor node (current values)\n"
        f"{tunable_json}\n\n"
        "## Operator vocabulary (the ONLY admissible proposals)\n"
        f"{op_block}\n\n"
        "## Output contract (hard requirements)\n"
        f"Return ONLY a single JSON array of AT MOST {max_candidates} objects. "
        f"Each object has EXACTLY these keys: {allowed}.\n"
        "  - \"operator\": one operator name from the vocabulary above.\n"
        "  - \"params\": an object matching that operator's params schema; every "
        "node id MUST be an id that appears in the BOM above. For "
        "mutate_processor_params, param_changes MUST be non-empty and only use "
        "keys listed for that node in the tunable-params section.\n"
        "  - \"rationale\": one short sentence that MUST reference at least one "
        "node id present in the BOM above.\n"
        f"Never emit the keys {{{forbidden}}}, and never emit Python / YAML / "
        "config / Mermaid / diagram / diff / patch payloads — proposals are "
        "typed operator specs, never code or config. Output no prose, no code "
        "fences, and nothing outside the JSON array."
    )

    if extra_context:
        prompt += "\n\n## Additional context\n" + extra_context
    return prompt


# ── proposal extraction (robust, non-gating) ─────────────────────────────────


@dataclass
class ExtractionResult:
    """Outcome of extracting proposals from an LLM reply.

    ``proposals`` is the array handed straight to ``run_shadow_round`` (element
    legality is decided there, not here).  ``error`` is non-empty only when no
    JSON array/object could be recovered at all.
    """

    proposals: "list[dict]" = field(default_factory=list)
    error: str = ""


def _balanced_from(text: str, start: int, open_ch: str, close_ch: str) -> "str | None":
    """Return the balanced ``open_ch..close_ch`` substring starting at ``start``.

    String literals and backslash escapes are respected so brackets inside JSON
    strings do not affect nesting depth.  Returns ``None`` if never balanced.
    """
    depth = 0
    in_str = False
    esc = False
    for j in range(start, len(text)):
        ch = text[j]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == open_ch:
            depth += 1
        elif ch == close_ch:
            depth -= 1
            if depth == 0:
                return text[start : j + 1]
    return None


def _iter_balanced(text: str, open_ch: str, close_ch: str):
    """Yield every top-level balanced ``open_ch..close_ch`` substring, in order."""
    i = 0
    n = len(text)
    while i < n:
        if text[i] == open_ch:
            sub = _balanced_from(text, i, open_ch, close_ch)
            if sub is not None:
                yield sub
                i += len(sub)
                continue
        i += 1


def _iter_fenced(text: str):
    """Yield the inner content of each ```...``` fenced block (json tag optional)."""
    marker = "```"
    i = text.find(marker)
    while i != -1:
        j = text.find(marker, i + len(marker))
        if j == -1:
            break
        inner = text[i + len(marker) : j]
        # drop an optional language tag on the opening line (```json / ```JSON)
        newline = inner.find("\n")
        if newline != -1 and inner[:newline].strip().lower() in ("json", ""):
            inner = inner[newline + 1 :]
        yield inner
        i = text.find(marker, j + len(marker))


def extract_proposals(text: Any) -> ExtractionResult:
    """Robustly recover a proposal array from an LLM reply.

    Handles a bare JSON array, a ```json fenced array, and an array embedded in
    surrounding prose (first balanced ``[...]``); a top-level single object is
    wrapped into a one-element array.  This layer does NOT filter or validate
    individual proposals — non-object elements are preserved verbatim for the
    downstream gate to reject.  Total failure to find any JSON array/object
    returns ``proposals=[]`` with a non-empty ``error``.
    """
    if not isinstance(text, str) or not text.strip():
        return ExtractionResult(proposals=[], error="empty or non-string LLM output")

    # Candidate JSON substrings in priority order: whole reply, fenced blocks,
    # then any balanced array / object discovered inside surrounding prose.
    candidates: "list[str]" = [text.strip()]
    candidates.extend(block.strip() for block in _iter_fenced(text))
    candidates.extend(_iter_balanced(text, "[", "]"))
    candidates.extend(_iter_balanced(text, "{", "}"))

    last_err = ""
    plain_list: "list | None" = None       # a parseable array with no dict elements

    # Pass 1: prefer the first array carrying ≥1 object, or a single object.
    for cand in candidates:
        if not cand:
            continue
        try:
            parsed = json.loads(cand)
        except ValueError as exc:
            last_err = str(exc)
            continue
        if isinstance(parsed, list):
            if any(isinstance(el, dict) for el in parsed):
                return ExtractionResult(proposals=parsed, error="")
            if plain_list is None:
                plain_list = parsed
        elif isinstance(parsed, dict):
            return ExtractionResult(proposals=[parsed], error="")
        else:
            last_err = f"top-level JSON is {type(parsed).__name__}, not an array/object"

    # Pass 2: fall back to a parseable array even if it holds no objects
    # (still non-gating — the kernel rejects each element with a reason).
    if plain_list is not None:
        return ExtractionResult(proposals=plain_list, error="")

    return ExtractionResult(
        proposals=[], error=last_err or "no JSON array found in LLM output"
    )


# ── one-call adapter entry ───────────────────────────────────────────────────


def llm_propose(
    snapshot: GraphSnapshot,
    llm_call: "Callable[[str], str]",
    *,
    max_candidates: int = 4,
    extra_context: str = "",
) -> ExtractionResult:
    """Prompt an injected LLM for proposals against ``snapshot``.

    ``llm_call`` is any synchronous ``str -> str`` transport (provider-agnostic,
    zero API dependency).  Builds the prompt, calls the model, and extracts the
    proposal array — feed ``result.proposals`` straight to ``run_shadow_round``.
    """
    prompt = build_proposer_prompt(
        snapshot, max_candidates=max_candidates, extra_context=extra_context
    )
    reply = llm_call(prompt)
    return extract_proposals(reply)
