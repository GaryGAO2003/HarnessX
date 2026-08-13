# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""L5 — the graph-native candidate surface: an in-session tool set for the Evolver.

Today the official Evolver writes two artifacts by hand: a manifest (frontmatter
``.md``) and an applied ``config.yaml``.  Both are free-text — the model composes the
YAML itself, so a candidate's real edit is whatever survived its own transcription.

This module gives the Evolver a session-scoped tool set (:class:`ProposalSession`)
where every mutation is a typed :class:`~harnessx.graph.edit.GraphEdit` validated
through the same S0-S4 transaction the rest of L5 uses
(:func:`harnessx.graph.validate.transactional_apply`), and the two artifacts are
**always machine-generated**: the model supplies content (evidence, predicted impact,
failure citations), the module supplies layout, and both files are rewritten in full
on every successful call.  Nothing here changes what the official pipeline reads —
``parse_candidate_manifest``, the five gates, the Critic, G2 — all keep reading a
frontmatter ``.md`` and a ``config.yaml`` exactly as before; this module only changes
how those bytes get produced.

Compilation is a merge, not a re-serialization: ``HarnessConfig.to_yaml()`` is total
(processors, plugins, tool_registry, workspace, tracer), so writing a candidate's
config is ``yaml.safe_load(parent_yaml)`` as the base dict with only its
``processors`` key replaced by ``graph_to_config_dict(snapshot)["processors"]``.
Every non-processors top-level key the parent carried survives untouched (I3).

Candidate identity follows the L5 convention used by
:func:`harnessx.graph.operators.apply_operator`: after a successful edit group, the
session's in-memory snapshot advances to the S4 build **fixed point**
(``report.fixed_point``), not the raw ``apply_edits`` result — that is the identity a
persisted-then-reloaded config re-graphs to, and every write is verified against it
(write, reload, re-graph, compare genotype hashes; mismatch rolls the write back).

Honesty contracts carried over from the rest of GHX:

* A derivation/validation failure is always recorded, never silently swallowed
  (the ``candidate_surface.py`` ``derivation_failed`` house rule) — a rejected edit
  group returns structured issues and leaves the candidate's snapshot and files
  untouched; a hand-written change to a machine file is detected and logged into
  lineage before being mechanically overwritten, never adopted silently.
* ``MUTATE_INACTIVE`` does not actually check whether its target is on an active
  path (see :mod:`harnessx.graph.edit`) — the tool description says so plainly
  rather than promising a safety property this module cannot back up.

Flag ``HARNESSX_GHX_GRAPH_PROPOSALS`` is read at call time (never cached at import),
default off — same convention as every other GHX flag
(:func:`harnessx.ghx.overlay.aegis_evidence_enabled`).

Out of scope for this module (task #5's job): rebinding the vendored Evolver builder,
prompt injection text, and the ask-more subcall path.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from ..graph.edit import GraphEdit, GraphEditType, diff_graphs
from ..graph.identity import deployment_hash, genotype_hash, phenotype_hash
from ..graph.impact import danger_edge_set
from ..graph.snapshot import to_graph
from ..graph.transform import graph_to_config_dict
from ..graph.types import EdgeType, GraphSnapshot, NodeType

_ENABLE_VALUES = frozenset({"1", "true", "on", "yes"})
FLAG = "HARNESSX_GHX_GRAPH_PROPOSALS"


def graph_proposals_enabled() -> bool:
    """True when the graph-native candidate surface should be offered.

    Read at call time (never cached at import), default OFF, so a run pays nothing
    unless ``HARNESSX_GHX_GRAPH_PROPOSALS`` is explicitly set — same convention as
    the other GHX flags (``aegis_evidence_enabled``, ``graph_gate_enabled``).
    """
    return os.environ.get(FLAG, "").strip().lower() in _ENABLE_VALUES


class ProposalPreflightError(RuntimeError):
    """Raised by :meth:`ProposalSession.__init__` when the parent config cannot
    round-trip cleanly (``from_yaml_file -> canonicalize -> to_graph ->
    transactional_apply(materialize=True)``).  Loud by design — the caller (#5)
    must not swallow this into a silent non-graph fallback."""


# ── bookkeeping / defaults ───────────────────────────────────────────────────

# Files this module itself writes into a candidate's scratch dir. Excluded from the
# file_changes asset scan — that scan is for files the MODEL wrote by hand (e.g. a
# prompt-bucket template asset), not our own ledger.
_BOOKKEEPING_NAMES = frozenset({
    "config.yaml", "graph_edits.jsonl", "graph_lineage.json", "graph_lineage.md",
})

# Interpreter droppings, not authored assets. The workflow actively produces these:
# the injected prompt tells the model a .py helper is legal, and the moment it
# *imports* one to test it, CPython writes __pycache__/<mod>.cpython-3XX.pyc beside
# it. Declaring that .pyc in file_changes fails IV-9 (no bucket whitelists .pyc) and
# kills an otherwise-good candidate at the structure gate -- which is exactly what
# no-op'd L5_holdout6x3_v3 R2. The vendored gate's own guard only skips paths ENDING
# in "__pycache__" (structure.py:180), i.e. the directory, never the files inside it.
_ARTEFACT_DIRS = frozenset({"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"})
_ARTEFACT_SUFFIXES = (".pyc", ".pyo", ".pyd")


def _is_build_artefact(path: Path, scratch_dir: Path) -> bool:
    if path.suffix in _ARTEFACT_SUFFIXES:
        return True
    try:
        parts = path.relative_to(scratch_dir).parts[:-1]
    except ValueError:
        parts = path.parts[:-1]
    return any(p in _ARTEFACT_DIRS for p in parts)

_PREDICTED_IMPACT_KEYS = (
    "tasks_will_unlock", "tasks_will_stabilize", "tasks_at_risk", "tasks_will_pass",
)

# Manifest fields the MODEL is expected to supply via GraphProposalManifest — used
# by GraphProposalStatus to report what has not been given yet (distinct from what
# is structurally present: file_changes/capability_evidence/predicted_impact are
# always structurally present because this module fills defaults for them).
_MODEL_SUPPLIED_FIELDS = (
    "capability_evidence", "predicted_impact", "failure_evidence", "attribution_signature",
)


@dataclass
class _CandidateState:
    """Per-candidate session state. One instance per ``GraphProposalOpen`` call."""

    candidate_id: str
    bucket: Any  # original str | list[str] form as given, preserved verbatim in frontmatter
    iterates_from: str
    snapshot: GraphSnapshot
    manifest_path: Path
    config_path: Path
    edits_path: Path
    lineage_json_path: Path
    lineage_md_path: Path
    opened_at: str
    manifest_fields: dict = field(default_factory=dict)  # model-supplied frontmatter fields
    fields_set: set = field(default_factory=set)  # which _MODEL_SUPPLIED_FIELDS have been given
    failure_evidence_body: str = ""
    edits_log: list = field(default_factory=list)
    reports: list = field(default_factory=list)
    danger_nodes: set = field(default_factory=set)
    danger_edges: set = field(default_factory=set)
    provenance: str = "tool_path"
    derived_fields: set = field(default_factory=set)
    hand_written_events: list = field(default_factory=list)
    last_config_hash: str = ""
    last_manifest_hash: str = ""


# ── edit dict <-> GraphEdit conversion (structural rejection, never raises out) ──


class _EditConversionError(ValueError):
    """A JSON edit entry could not become a typed GraphEdit. Caught at the tool
    boundary and turned into a structured ``issues`` entry — never raised out of
    a tool call."""


def _edit_from_dict(raw: Any, group_reason: str) -> GraphEdit:
    if not isinstance(raw, dict):
        raise _EditConversionError(f"edit entry must be a JSON object, got {type(raw).__name__}")

    et_raw = raw.get("edit_type")
    try:
        edit_type = GraphEditType(et_raw)
    except ValueError:
        raise _EditConversionError(
            f"unknown edit_type {et_raw!r}; must be one of "
            f"{sorted(t.value for t in GraphEditType)}"
        ) from None

    edge_type_raw = raw.get("edge_type")
    edge_type: EdgeType | None = None
    if edge_type_raw:
        try:
            edge_type = EdgeType(edge_type_raw)
        except ValueError:
            raise _EditConversionError(
                f"unknown edge_type {edge_type_raw!r}; must be one of "
                f"{sorted(t.value for t in EdgeType)}"
            ) from None

    node_spec = raw.get("node_spec")
    if node_spec is not None and not isinstance(node_spec, dict):
        raise _EditConversionError("node_spec must be a JSON object or null")
    node_changes = raw.get("node_changes")
    if node_changes is not None and not isinstance(node_changes, dict):
        raise _EditConversionError("node_changes must be a JSON object or null")

    return GraphEdit(
        edit_type=edit_type,
        target_node_id=str(raw.get("target_node_id") or ""),
        node_spec=node_spec,
        edge_source_id=str(raw.get("edge_source_id") or ""),
        edge_target_id=str(raw.get("edge_target_id") or ""),
        edge_type=edge_type,
        add_edge=bool(raw.get("add_edge", True)),
        replacement_bundle_id=str(raw.get("replacement_bundle_id") or ""),
        node_changes=node_changes,
        reason=group_reason,
    )


def _edit_to_jsonable(e: GraphEdit) -> dict:
    return {
        "edit_type": e.edit_type.value,
        "target_node_id": e.target_node_id,
        "node_spec": e.node_spec,
        "edge_source_id": e.edge_source_id,
        "edge_target_id": e.edge_target_id,
        "edge_type": e.edge_type.value if e.edge_type else None,
        "add_edge": e.add_edge,
        "replacement_bundle_id": e.replacement_bundle_id,
        "node_changes": e.node_changes,
        "reason": e.reason,
    }


# ── bucket derivation (what AEGIS compose can actually carry) ────────────────

_PROMPT_TARGET_MARK = "SystemPromptProcessor"


def _procs_by_target(processors: Any) -> dict[str, dict]:
    """Index a config-dict processor list by ``_target_`` — compose's own key."""
    out: dict[str, dict] = {}
    for p in processors or []:
        if isinstance(p, dict):
            tgt = p.get("_target_")
            if tgt:
                out[str(tgt)] = p
    return out


def _prompt_change_is_template_only(parent_p: dict, cand_p: dict) -> bool:
    """True when the only difference is ``system_builder.template_path``.

    ``_apply_prompt`` copies *only* that one field when it finds it
    (``compose.py:58-63``), so any other change to the prompt node needs the
    ``config`` applier instead or it is silently dropped.
    """
    a, b = copy.deepcopy(parent_p), copy.deepcopy(cand_p)
    for d in (a, b):
        sb = d.get("system_builder")
        if isinstance(sb, dict):
            sb.pop("template_path", None)
    return a == b


def derive_landing_buckets(
    parent_snapshot: GraphSnapshot,
    candidate_snapshot: GraphSnapshot,
    *,
    parent_tool_registry: Any = None,
    candidate_tool_registry: Any = None,
) -> list[str]:
    """Buckets whose ``aegis.compose`` applier can actually carry this diff.

    AEGIS composes a shipped candidate onto the round base by dispatching on the
    manifest's ``bucket``, and each applier understands exactly one kind of change
    (``harnessx/aegis/compose.py:173``). A candidate declaring a bucket whose
    applier cannot express its edits passes all five gates and then lands
    *nothing* — the round silently re-runs the parent config and the flat read is
    mistaken for "the intervention didn't help". So the bucket is derived from the
    realised diff rather than taken from the model.

    Mapping, mirroring the appliers:

    * added / removed ``_target_``       -> ``processor`` (``_apply_processor``)
    * kwargs changed, prompt node only,
      and only its ``template_path``     -> ``prompt``    (``_apply_prompt``)
    * kwargs changed, anything else      -> ``config``    (``_apply_config``,
      which matches by ``_target_`` and so subsumes the prompt node too)
    * ``tool_registry`` changed          -> ``tools``     (``_apply_tools``)

    An empty result means no applier can carry the change. An edge-only edit
    (``CHANGE_DEPENDENCY``) is the canonical case: compose never looks at edges.
    Callers must refuse to finalize such a candidate rather than let it ship.
    """
    parent = _procs_by_target(graph_to_config_dict(parent_snapshot).get("processors"))
    cand = _procs_by_target(graph_to_config_dict(candidate_snapshot).get("processors"))

    buckets: list[str] = []
    if set(cand) - set(parent) or set(parent) - set(cand):
        buckets.append("processor")

    changed = [t for t in set(parent) & set(cand) if parent[t] != cand[t]]
    if changed:
        prompt_only = all(
            _PROMPT_TARGET_MARK in t and _prompt_change_is_template_only(parent[t], cand[t])
            for t in changed
        )
        buckets.append("prompt" if prompt_only else "config")

    if (parent_tool_registry or None) != (candidate_tool_registry or None):
        buckets.append("tools")

    return buckets


def _issue_to_dict(i: Any) -> dict:
    return {"layer": i.layer, "error_type": i.error_type, "message": i.message}


def _append_jsonl(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(obj, ensure_ascii=False) + "\n")


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _render_lineage_md(payload: dict) -> str:
    """Human-readable mirror of the lineage JSON (candidate_surface.py convention:
    markdown summary + a fenced JSON block carrying the exact same payload)."""
    lines = [f"# Graph lineage -- {payload['candidate_id']}", ""]
    lines += [
        f"Round {payload['round']}. Provenance: `{payload['provenance']}`. "
        f"Zero-edit: {payload['zero_edit']}.",
        "",
        "## Hashes",
        f"- parent genotype: `{payload['parent_hashes'].get('genotype', '')}`",
        f"- child genotype: `{payload['child_hashes'].get('genotype', '')}`",
        f"- child deployment: `{payload['child_hashes'].get('deployment', '')}`",
        f"- child phenotype: `{payload['child_hashes'].get('phenotype', '')}`",
        "",
        f"## Edits ({len(payload['edits'])})",
    ]
    if payload["edits"]:
        for e in payload["edits"]:
            anchor = e.get("target_node_id") or e.get("edge_source_id") or "(none)"
            lines.append(f"- {e.get('edit_type')} on `{anchor}` -- {e.get('reason') or '(no reason given)'}")
    else:
        lines.append("(none)")
    lines += [
        "",
        f"## Danger cone ({payload['danger_cone']['node_count']} nodes, "
        f"{payload['danger_cone']['edge_count']} edges)",
        "",
        f"## Hand-written events ({len(payload['hand_written_events'])})",
    ]
    if payload["hand_written_events"]:
        for ev in payload["hand_written_events"]:
            lines.append(f"- {ev.get('kind')} detected at {ev.get('detected_at')}")
    else:
        lines.append("(none)")
    lines += [
        "",
        "## Machine-readable",
        "```json",
        json.dumps(payload, ensure_ascii=False, indent=2),
        "```",
        "",
    ]
    return "\n".join(lines)


# ── tool schemas + descriptions ──────────────────────────────────────────────

_OPEN_DESC = (
    "Open a new candidate inside this Evolver session. Writes the candidate's manifest "
    "frontmatter (all required keys present, capability_evidence=[]) and its applied "
    "config.yaml (byte-for-byte the round parent's processors) immediately. "
    "candidate_id must match C-R{round}-NN (exactly two digits) for this session's round. "
    "Refuses -- without overwriting anything -- if this candidate_id already has a "
    "manifest or scratch directory on disk; call GraphProposalStatus to inspect existing "
    "work instead."
)
_SCHEMA_OPEN = {
    "type": "object",
    "properties": {
        "candidate_id": {"type": "string", "description": "Must match C-R{round}-NN (two digits)."},
        "bucket": {
            "type": ["string", "array"],
            "items": {"type": "string"},
            "description": (
                "Your INTENT: one of prompt|tools|config|processor, or a list. Advisory only -- "
                "the bucket written into the manifest is derived from the edits you actually "
                "apply, because AEGIS composes a ship by dispatching on bucket and each applier "
                "carries exactly one kind of change. Declaring a bucket whose applier cannot "
                "express your edits would ship through all five gates and land nothing. "
                "GraphProposalStatus reports declared_bucket vs landing_buckets."
            ),
        },
        "iterates_from": {
            "type": "string",
            "description": (
                "Optional prior ship id this candidate reverts/improves (e.g. C-R5-01). "
                "Omit for a brand-new candidate."
            ),
        },
    },
    "required": ["candidate_id", "bucket"],
}

_EDIT_DESC = (
    "Apply one atomic group of typed graph edits to a candidate's config -- every edit "
    "in the group commits together, or the whole group is rejected and nothing changes "
    "(snapshot and files untouched). Six edit_type values: insert_node, remove_node, "
    "replace_same_group, change_dependency, swap_subgraph, mutate_inactive. "
    "To change an ACTIVE processor's parameters, use replace_same_group with a node_spec "
    "that keeps the same _target_ and _singleton_group_ but different other fields -- "
    "mutate_inactive does NOT check whether its target is actually inactive despite its "
    "name, so do not rely on it for phenotype-safety. "
    "IMPORTANT: only node_spec keys WITHOUT a leading underscore (constructor kwargs) are "
    "guaranteed to survive -- _hook_/_order_/_singleton_group_/_after_ only affect the "
    "pre-build graph and are silently replaced by the target class's own _hook/_order/"
    "_singleton_group/_after attributes once this candidate's snapshot advances to its S4 "
    "build fixed point, unless your new value already matches what the class declares. "
    "On success: ok=true, the child genotype hash, an updated node inventory, and the "
    "files rewritten. On failure: ok=false, issues=[{layer, error_type, message}], and "
    "the genotype UNCHANGED -- feed the issues back to the model and retry."
)
_EDIT_ITEM_SCHEMA = {
    "type": "object",
    "properties": {
        "edit_type": {"type": "string", "enum": sorted(t.value for t in GraphEditType)},
        "target_node_id": {"type": "string"},
        "node_spec": {"type": "object", "description": "Serialized processor spec (_target_, _hook_, ...)."},
        "edge_source_id": {"type": "string"},
        "edge_target_id": {"type": "string"},
        "edge_type": {"type": "string", "enum": sorted(t.value for t in EdgeType)},
        "add_edge": {"type": "boolean", "description": "True=add the edge, False=remove it. Default true."},
        "replacement_bundle_id": {"type": "string"},
        "node_changes": {"type": "object", "description": "{field: new_value} for mutate_inactive."},
    },
    "required": ["edit_type"],
}
_SCHEMA_EDIT = {
    "type": "object",
    "properties": {
        "candidate_id": {"type": "string"},
        "edits": {
            "type": "array",
            "items": _EDIT_ITEM_SCHEMA,
            "description": "One atomic group of typed graph edits -- all commit or all are rejected.",
        },
        "reason": {"type": "string", "description": "Rationale applied to every edit in this group."},
    },
    "required": ["candidate_id", "edits"],
}

_MANIFEST_DESC = (
    "Update this candidate's model-authored manifest fields. Content is yours; layout "
    "is always machine-generated and the manifest is rewritten in full on every call. "
    "Only the fields you pass (non-null) are changed -- omit a field to keep its previous "
    "value. failure_evidence is the markdown body for the '## Failure Evidence' section: "
    "cite at least one real anchor yourself, formatted as `trajectories/<file>#<locator>`, "
    "`sessions/<file>#<locator>`, or `digests/<file>` (backtick- or bracket-wrapped). ONLY "
    "trajectories/, sessions/, and digests/ prefixes are recognized -- applied/ and "
    "meta_sessions/ are NOT legal anchor prefixes and will not count as evidence."
)
_SCHEMA_MANIFEST = {
    "type": "object",
    "properties": {
        "candidate_id": {"type": "string"},
        "capability_evidence": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "type": {"type": "string"},
                    "claim": {"type": "string"},
                    "evidence": {"type": "string"},
                },
                "required": ["type", "claim", "evidence"],
            },
        },
        "predicted_impact": {
            "type": "object",
            "properties": {k: {"type": "array", "items": {"type": "string"}} for k in _PREDICTED_IMPACT_KEYS},
        },
        "failure_evidence": {"type": "string", "description": "Markdown body for '## Failure Evidence'."},
        "attribution_signature": {
            "type": "object",
            "properties": {
                "type": {"type": "string", "enum": ["tool_call", "processor_invocation"]},
                "tool_name": {"type": "string"},
                "expected_min_calls": {"type": "integer"},
            },
        },
        "notes": {"type": "string"},
    },
    "required": ["candidate_id"],
}

_STATUS_DESC = (
    "Read-only. With candidate_id empty, lists every candidate opened in this session. "
    "With a candidate_id, reports its edit count, which manifest fields the model has not "
    "yet supplied, whether its config differs from the round parent, its provenance "
    "(tool_path vs hand_written_detected), and a checklist of what is still outstanding "
    "before it would pass the real structure gate."
)
_SCHEMA_STATUS = {
    "type": "object",
    "properties": {
        "candidate_id": {"type": "string", "description": "Empty = report on every candidate opened this session."},
    },
    "required": [],
}


class ProposalSession:
    """One Evolver session (one round). #5 constructs this in the rebound builder.

    Owns the round's parent baseline (preflighted once at construction) and every
    candidate opened against it. Each candidate keeps its own graph snapshot,
    diverging independently from the same parent starting point.
    """

    def __init__(
        self,
        parent_config_path: "str | Path",
        candidates_dir: "str | Path",
        applied_root: "str | Path",
        round_n: int,
    ) -> None:
        from ..core.harness import HarnessConfig
        from ..graph.validate import transactional_apply

        self.parent_config_path = Path(parent_config_path)
        self.candidates_dir = Path(candidates_dir)
        self.applied_root = Path(applied_root)
        self.round_n = round_n
        self._candidates: dict[str, _CandidateState] = {}

        try:
            self._parent_yaml_text = self.parent_config_path.read_text(encoding="utf-8")
            parent_cfg = HarnessConfig.from_yaml_file(str(self.parent_config_path)).canonicalize()
            parent_snapshot = to_graph(parent_cfg)
            result, report = transactional_apply(parent_snapshot, [], materialize=True)
        except Exception as exc:  # noqa: BLE001 -- preflight must fail loud, never silently degrade
            raise ProposalPreflightError(
                f"parent config at {self.parent_config_path} failed preflight: "
                f"{type(exc).__name__}: {exc}"
            ) from exc

        if result is None or not report.passed or report.fixed_point is None:
            raise ProposalPreflightError(
                f"parent config at {self.parent_config_path} failed the S4 preflight "
                f"roundtrip: {report.reason() or 'materialize did not yield a fixed point'}"
            )

        # The S4 fixed point is the parent's canonical build-time identity — the same
        # snapshot a persisted-then-reloaded config re-graphs to (see module docstring).
        self._parent_snapshot = report.fixed_point
        self._parent_hashes = {
            "genotype": genotype_hash(self._parent_snapshot),
            "deployment": deployment_hash(self._parent_snapshot),
            "phenotype": phenotype_hash(self._parent_snapshot),
        }
        # Raw parent mapping (non-processor keys compose also reads, e.g.
        # tool_registry). Preflight already proved the text parses.
        parent_raw = yaml.safe_load(self._parent_yaml_text) or {}
        self._parent_config_dict: dict = parent_raw if isinstance(parent_raw, dict) else {}

    # ── frozen public surface (§4 interface contract) ───────────────────────

    def node_inventory_text(self) -> str:
        """Human-readable inventory of the ROUND PARENT's processor nodes — for #5 to
        inject into the session's opening prompt. Per-candidate inventories (reflecting
        each candidate's own current snapshot) are attached to tool return values via
        the same private renderer."""
        return self._inventory_lines(self._parent_snapshot)

    def make_tools(self) -> list:
        """Build the four @tool-decorated objects for this session (closures over
        ``self``). #5 registers these into the Evolver's tool registry."""
        from ..tools.base import tool

        session = self

        @tool(name="GraphProposalOpen", description=_OPEN_DESC, input_schema=_SCHEMA_OPEN)
        async def graph_proposal_open(candidate_id: str, bucket: Any, iterates_from: str = "") -> dict:
            return session._open(candidate_id, bucket, iterates_from)

        @tool(name="GraphProposalEdit", description=_EDIT_DESC, input_schema=_SCHEMA_EDIT)
        async def graph_proposal_edit(candidate_id: str, edits: list, reason: str = "") -> dict:
            return session._edit(candidate_id, edits, reason)

        @tool(name="GraphProposalManifest", description=_MANIFEST_DESC, input_schema=_SCHEMA_MANIFEST)
        async def graph_proposal_manifest(
            candidate_id: str,
            capability_evidence: Any = None,
            predicted_impact: Any = None,
            failure_evidence: "str | None" = None,
            attribution_signature: Any = None,
            notes: "str | None" = None,
        ) -> dict:
            return session._manifest(
                candidate_id,
                capability_evidence=capability_evidence,
                predicted_impact=predicted_impact,
                failure_evidence=failure_evidence,
                attribution_signature=attribution_signature,
                notes=notes,
            )

        @tool(name="GraphProposalStatus", description=_STATUS_DESC, input_schema=_SCHEMA_STATUS)
        async def graph_proposal_status(candidate_id: str = "") -> dict:
            return session._status(candidate_id)

        return [graph_proposal_open, graph_proposal_edit, graph_proposal_manifest, graph_proposal_status]

    def session_report(self) -> dict:
        """Convenience summary across every candidate opened this session — performs
        no I/O itself. The durable, on-disk source of truth is each candidate's own
        lineage file (written on every successful edit/manifest call, not only at
        session end); a caller MAY additionally persist this summary when a session
        ends, but nothing in this module requires it."""
        return {
            "round": self.round_n,
            "parent_config_path": str(self.parent_config_path),
            "parent_hashes": dict(self._parent_hashes),
            "candidates": {
                cid: {
                    "bucket": c.bucket,
                    "iterates_from": c.iterates_from,
                    "edit_count": len(c.edits_log),
                    "provenance": c.provenance,
                    "zero_edit": not c.edits_log,
                    "genotype": genotype_hash(c.snapshot),
                    "files": [
                        str(c.config_path), str(c.manifest_path), str(c.edits_path),
                        str(c.lineage_json_path), str(c.lineage_md_path),
                    ],
                }
                for cid, c in self._candidates.items()
            },
        }

    # ── tool implementations ─────────────────────────────────────────────────

    def _open(self, candidate_id: str, bucket: Any, iterates_from: str = "") -> dict:
        if not re.match(rf"^C-R{self.round_n}-\d{{2}}$", candidate_id or ""):
            return {
                "ok": False,
                "error": f"candidate_id {candidate_id!r} must match C-R{self.round_n}-NN (exactly two digits)",
            }

        bucket_norm = bucket if isinstance(bucket, list) else ([bucket] if bucket else [])
        if not bucket_norm or not all(isinstance(b, str) and b for b in bucket_norm):
            return {"ok": False, "error": "bucket must be a non-empty string or a non-empty list of strings"}

        manifest_path = self.candidates_dir / f"{candidate_id}.md"
        scratch_dir = self.applied_root / candidate_id
        if candidate_id in self._candidates or manifest_path.exists() or scratch_dir.exists():
            return {
                "ok": False,
                "error": (
                    f"{candidate_id} already has work on disk or in this session "
                    f"(manifest_exists={manifest_path.exists()}, scratch_dir_exists={scratch_dir.exists()}). "
                    "Refusing to overwrite -- call GraphProposalStatus to inspect the existing candidate."
                ),
            }

        candidate = _CandidateState(
            candidate_id=candidate_id,
            bucket=bucket,
            iterates_from=iterates_from,
            snapshot=copy.deepcopy(self._parent_snapshot),
            manifest_path=manifest_path,
            config_path=scratch_dir / "config.yaml",
            edits_path=scratch_dir / "graph_edits.jsonl",
            lineage_json_path=scratch_dir / "graph_lineage.json",
            lineage_md_path=scratch_dir / "graph_lineage.md",
            opened_at=_utcnow(),
        )

        ok, err = self._write_config_manifest_lineage(candidate)
        if not ok:
            # F9: a candidate that fails mid write-phase must not leave files behind
            # that make a same-cid retry look like it already has work in progress,
            # and must not be registered -- an unregistered cid must never become a
            # permanently-unopenable dead end (Refuse forever, Status not knowing it).
            self._cleanup_failed_open(candidate)
            return {"ok": False, "error": f"candidate creation failed while writing: {err}"}
        self._candidates[candidate_id] = candidate

        return {
            "ok": True,
            "candidate_id": candidate_id,
            "files": [str(candidate.config_path), str(candidate.manifest_path)],
            "node_inventory": self._inventory_lines(candidate.snapshot),
        }

    def _edit(self, candidate_id: str, edits: list, reason: str = "") -> dict:
        from ..graph.validate import transactional_apply

        candidate = self._candidates.get(candidate_id)
        if candidate is None:
            return {
                "ok": False,
                "genotype": "",
                "issues": [{
                    "layer": "session", "error_type": "unknown_candidate",
                    "message": f"{candidate_id!r} is not open in this session -- call GraphProposalOpen first",
                }],
            }

        self._reconcile_hand_written(candidate)
        current_genotype = genotype_hash(candidate.snapshot)

        if not edits:
            return {
                "ok": False, "genotype": current_genotype,
                "issues": [{"layer": "parse", "error_type": "empty_edit_group",
                            "message": "edits must be a non-empty JSON list"}],
            }

        group: list[GraphEdit] = []
        try:
            for raw in edits:
                group.append(_edit_from_dict(raw, reason))
        except _EditConversionError as exc:
            return {
                "ok": False, "genotype": current_genotype,
                "issues": [{"layer": "parse", "error_type": "bad_edit_shape", "message": str(exc)}],
            }

        danger_nodes, danger_edges = danger_edge_set(group, candidate.snapshot)
        result, report = transactional_apply(candidate.snapshot, group, materialize=True)

        candidate.reports.append({
            "timestamp": _utcnow(),
            "trigger": "edit",
            "passed": report.passed,
            "issues": [_issue_to_dict(i) for i in report.issues],
            "warnings": [_issue_to_dict(i) for i in report.warnings],
        })

        if result is None or not report.passed:
            issues = [_issue_to_dict(i) for i in report.issues]
            if not issues:
                issues = [{"layer": "S4", "error_type": "rejected", "message": report.reason() or "edit group rejected"}]
            return {"ok": False, "genotype": current_genotype, "issues": issues}

        # Candidate identity contract (mirrors apply_operator): advance to the S4
        # build fixed point, not the raw apply_edits result (see module docstring).
        old_snapshot = candidate.snapshot
        old_danger_nodes = set(candidate.danger_nodes)
        old_danger_edges = set(candidate.danger_edges)
        old_edits_len = len(candidate.edits_log)

        candidate.snapshot = report.fixed_point if report.fixed_point is not None else result
        candidate.danger_nodes |= danger_nodes
        candidate.danger_edges |= danger_edges

        ts = _utcnow()
        new_lines = []
        for ge in group:
            line = _edit_to_jsonable(ge)
            line["timestamp"] = ts
            new_lines.append(line)
        candidate.edits_log.extend(new_lines)

        # F2: "On failure: ... genotype UNCHANGED" (_EDIT_DESC) must hold even when
        # the FAILURE happens after the in-memory edit already applied cleanly --
        # write config+manifest+lineage first, jsonl (append-only, never rewritten
        # in place) last, and if ANY of the four write steps fails, roll the memory
        # back and re-run the first three writes against the rolled-back state so
        # disk matches memory again.
        ok, err = self._write_config_manifest_lineage(candidate)
        jsonl_err = ""
        if ok:
            try:
                for line in new_lines:
                    _append_jsonl(candidate.edits_path, line)
            except Exception as exc:  # noqa: BLE001 -- pure ledger disk fault, still rolls back
                ok = False
                jsonl_err = f"{type(exc).__name__}: {exc}"

        if not ok:
            candidate.snapshot = old_snapshot
            candidate.danger_nodes = old_danger_nodes
            candidate.danger_edges = old_danger_edges
            del candidate.edits_log[old_edits_len:]

            restore_ok, restore_err = self._write_config_manifest_lineage(candidate)
            if jsonl_err:
                # config/manifest/lineage were already rewritten with the NEW state
                # before the ledger append itself failed (pure disk fault on the
                # jsonl only) -- best-effort a compensating marker; a failure here
                # is not chased further.
                try:
                    _append_jsonl(candidate.edits_path, {
                        "rollback": True, "reason": jsonl_err, "timestamp": _utcnow(),
                    })
                except Exception:  # noqa: BLE001 -- best-effort only
                    pass

            message = jsonl_err or err
            if not restore_ok:
                message = f"{message}; disk restore to prior state ALSO failed: {restore_err}"

            candidate.reports.append({
                "timestamp": _utcnow(), "trigger": "write", "passed": False,
                "issues": [{"layer": "write", "error_type": "write_failed", "message": message}],
            })
            return {
                "ok": False, "genotype": current_genotype,
                "issues": [{"layer": "write", "error_type": "write_failed", "message": message}],
            }

        return {
            "ok": True,
            "genotype": genotype_hash(candidate.snapshot),
            "node_inventory": self._inventory_lines(candidate.snapshot),
            "files": [str(candidate.config_path), str(candidate.manifest_path)],
        }

    def _manifest(
        self,
        candidate_id: str,
        *,
        capability_evidence: Any = None,
        predicted_impact: Any = None,
        failure_evidence: "str | None" = None,
        attribution_signature: Any = None,
        notes: "str | None" = None,
    ) -> dict:
        candidate = self._candidates.get(candidate_id)
        if candidate is None:
            return {
                "ok": False,
                "error": f"{candidate_id!r} is not open in this session -- call GraphProposalOpen first",
            }

        self._reconcile_hand_written(candidate)

        # An unlandable candidate must never reach the gates. Refusing here is a
        # real stop, not advice: capability_evidence/predicted_impact can only be
        # set through this call, so a manifest that never gets them cannot pass
        # the structure gate either.
        if candidate.edits_log and not self._landing_buckets(candidate):
            return {
                "ok": False,
                "error": (
                    f"{candidate_id} has {len(candidate.edits_log)} applied edit(s) but none of "
                    "them change anything AEGIS compose can carry onto the round base. Compose "
                    "dispatches per bucket over the processor list and tool_registry only -- it "
                    "never looks at edges -- so an edge-only change (change_dependency) would "
                    "pass all five gates and then land NOTHING, and the round would silently "
                    "re-run the parent config. Express the intent as a node change "
                    "(insert_node / remove_node / replace_same_group) and call this again."
                ),
            }

        old_manifest_fields = dict(candidate.manifest_fields)
        old_fields_set = set(candidate.fields_set)
        old_failure_evidence_body = candidate.failure_evidence_body
        old_derived_fields = set(candidate.derived_fields)

        updated: list[str] = []
        if capability_evidence is not None:
            candidate.manifest_fields["capability_evidence"] = capability_evidence
            candidate.fields_set.add("capability_evidence")
            updated.append("capability_evidence")
        if predicted_impact is not None:
            candidate.manifest_fields["predicted_impact"] = predicted_impact
            candidate.fields_set.add("predicted_impact")
            updated.append("predicted_impact")
        if attribution_signature is not None:
            candidate.manifest_fields["attribution_signature"] = attribution_signature
            candidate.fields_set.add("attribution_signature")
            candidate.derived_fields.discard("attribution_signature")
            updated.append("attribution_signature")
        if notes is not None:
            candidate.manifest_fields["notes"] = notes
            updated.append("notes")
        if failure_evidence is not None:
            candidate.failure_evidence_body = failure_evidence
            candidate.fields_set.add("failure_evidence")
            updated.append("failure_evidence")

        if not updated:
            return {
                "ok": False,
                "error": (
                    "no fields given -- pass at least one of capability_evidence/"
                    "predicted_impact/failure_evidence/attribution_signature/notes"
                ),
            }

        ok, err = self._write_config_manifest_lineage(candidate)
        if not ok:
            # F2: on failure the model-supplied fields must go back UNCHANGED too --
            # a caller retrying the same call after a transient write fault should
            # not find half its previous fields already clobbered in memory.
            candidate.manifest_fields = old_manifest_fields
            candidate.fields_set = old_fields_set
            candidate.failure_evidence_body = old_failure_evidence_body
            candidate.derived_fields = old_derived_fields
            return {"ok": False, "error": f"manifest write failed, fields unchanged: {err}"}

        return {
            "ok": True,
            "candidate_id": candidate_id,
            "updated_fields": updated,
            "files": [str(candidate.manifest_path), str(candidate.config_path)],
        }

    def _status(self, candidate_id: str = "") -> dict:
        if candidate_id:
            candidate = self._candidates.get(candidate_id)
            if candidate is None:
                return {"ok": False, "error": f"unknown candidate_id {candidate_id!r}"}
            return {"ok": True, "candidate_id": candidate_id, **self._status_for(candidate)}
        return {"ok": True, "candidates": {cid: self._status_for(c) for cid, c in self._candidates.items()}}

    # ── status helpers ────────────────────────────────────────────────────────

    def _status_for(self, candidate: _CandidateState) -> dict:
        from ..aegis.gates.structure import validate_candidate_manifest

        fm = self._build_frontmatter(candidate)
        body = self._build_body(candidate)
        missing = [f for f in _MODEL_SUPPLIED_FIELDS if f not in candidate.fields_set]
        gate = validate_candidate_manifest(fm, body)

        landing = self._landing_buckets(candidate) if candidate.edits_log else []

        checklist: list[str] = []
        if not candidate.edits_log:
            checklist.append("zero-edit draft -- call GraphProposalEdit before this candidate can ship")
        elif not landing:
            checklist.append(
                "edits applied but NONE of them are carryable by AEGIS compose (it reads the "
                "processor list and tool_registry, never edges) -- this candidate would ship and "
                "land nothing; re-express it as a node change"
            )
        if missing:
            checklist.append(f"GraphProposalManifest not yet called for: {', '.join(missing)}")
        if not gate.ok:
            checklist.append(f"structure gate would currently reject: {gate.reason}")
        if not checklist:
            checklist.append("structure gate passes; nothing outstanding")

        return {
            "edit_count": len(candidate.edits_log),
            "manifest_missing_fields": missing,
            "config_differs_from_parent": genotype_hash(candidate.snapshot) != self._parent_hashes["genotype"],
            "declared_bucket": candidate.bucket,
            "landing_buckets": landing,
            "bucket_in_manifest": fm.get("bucket"),
            "provenance": candidate.provenance,
            "structure_gate_ok": gate.ok,
            "structure_gate_reason": gate.reason,
            "checklist": checklist,
        }

    # ── write-through (config.yaml, manifest.md, lineage) ───────────────────

    def _render_config_yaml(self, candidate: _CandidateState) -> str:
        base = yaml.safe_load(self._parent_yaml_text) or {}
        base = dict(base)
        base["processors"] = graph_to_config_dict(candidate.snapshot)["processors"]
        return yaml.safe_dump(base, allow_unicode=True, sort_keys=False)

    def _write_config_verified(self, candidate: _CandidateState) -> "tuple[bool, str]":
        """Write config.yaml, reload it, and require BOTH that the genotype matches
        the in-memory fixed point AND that the reloaded config still passes the same
        S0-S4 preflight this session demands of its own parent. Either failure rolls
        the write back to whatever was on disk before (or removes the file if there
        was nothing) -- never leaves an unverified file in place.

        The second check exists because the first is not sufficient. A node_spec can
        be silently completed at canonicalize time from the target CLASS's own
        attributes: a model-authored processor declaring
        ``_writes_event_fields = ("tool_input", ...)`` (bare names rather than
        ``EventClass.field``) contributes nothing at edit time, gets emitted into the
        persisted YAML by canonicalize, and survives the genotype comparison because
        the hash does not cover those fields. It then fails S3 the moment anything
        reads the file back -- which is what killed R1's whole evolve stage in
        L5_holdout6x3_v2 (see docs/ghx-v6-build-log.md, 08-13). Writing something we
        would refuse to read is the asymmetry this closes: the edit is rejected while
        the model can still fix it, instead of shipping a config that poisons the
        round parent for the next session's preflight.
        """
        from ..core.harness import HarnessConfig
        from ..graph.validate import transactional_apply

        path = candidate.config_path
        path.parent.mkdir(parents=True, exist_ok=True)
        previous = path.read_bytes() if path.exists() else None
        text = self._render_config_yaml(candidate)
        path.write_text(text, encoding="utf-8", newline="\n")
        try:
            reloaded = HarnessConfig.from_yaml_file(str(path)).canonicalize()
            got = genotype_hash(to_graph(reloaded))
            want = genotype_hash(candidate.snapshot)
            if got != want:
                raise ValueError(f"post-write genotype mismatch: in-memory={want} reread={got}")
            _result, report = transactional_apply(to_graph(reloaded), [], materialize=True)
            if not report.passed:
                raise ValueError(
                    "persisted config fails the same S0-S4 preflight a session runs on its "
                    "parent, so shipping it would break the next round's Evolver: "
                    + (report.reason() or "preflight rejected the reloaded config")
                )
        except Exception as exc:  # noqa: BLE001 -- fail closed, roll back the write
            if previous is not None:
                path.write_bytes(previous)
            else:
                path.unlink(missing_ok=True)
            return False, f"{type(exc).__name__}: {exc}"
        candidate.last_config_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        return True, ""

    def _write_config_manifest_lineage(self, candidate: _CandidateState) -> "tuple[bool, str]":
        """Write config.yaml (verified) + manifest + lineage as one failable unit --
        never raises. An exception from either of the latter two is caught and
        turned into the same ``(False, message)`` shape ``_write_config_verified``
        already uses, so callers (:meth:`_open`, :meth:`_edit`, :meth:`_manifest`)
        can treat "write everything this call touches" as a single step to roll
        back together (F2/F9)."""
        ok, err = self._write_config_verified(candidate)
        if not ok:
            return False, f"config_write_failed: {err}"
        try:
            self._write_manifest(candidate)
            self._write_lineage(candidate)
        except Exception as exc:  # noqa: BLE001 -- surfaced as a structured failure, never raised out
            return False, f"{type(exc).__name__}: {exc}"
        return True, ""

    def _cleanup_failed_open(self, candidate: _CandidateState) -> None:
        """Best-effort cleanup after :meth:`_open`'s write phase fails partway (F9)
        -- an unregistered candidate must not leave files on disk that make a
        same-cid retry look like it already has work in progress."""
        for p in (candidate.config_path, candidate.manifest_path, candidate.edits_path,
                  candidate.lineage_json_path, candidate.lineage_md_path):
            try:
                p.unlink(missing_ok=True)
            except OSError:
                pass
        scratch_dir = candidate.config_path.parent
        try:
            if scratch_dir.exists() and not any(scratch_dir.iterdir()):
                scratch_dir.rmdir()
        except OSError:
            pass

    def _file_changes_for(self, candidate: _CandidateState) -> list[dict]:
        type_counts: dict[str, int] = {}
        for e in candidate.edits_log:
            t = e.get("edit_type", "?")
            type_counts[t] = type_counts.get(t, 0) + 1
        if type_counts:
            counts_text = ", ".join(f"{k}x{v}" for k, v in sorted(type_counts.items()))
            diff_summary = f"{len(candidate.edits_log)} graph edit(s): {counts_text}"
        else:
            diff_summary = "zero-edit draft -- byte-identical to the round parent's processors"
        changes = [{"path": str(candidate.config_path), "action": "modify", "diff_summary": diff_summary}]

        scratch_dir = candidate.config_path.parent
        if scratch_dir.exists():
            for p in sorted(scratch_dir.rglob("*")):
                if not p.is_file():
                    continue
                if p.parent == scratch_dir and p.name in _BOOKKEEPING_NAMES:
                    continue  # our own ledger files at the scratch ROOT only -- a
                    # same-named file in a model-authored subdirectory is a real asset
                if _is_build_artefact(p, scratch_dir):
                    continue  # see _ARTEFACT_DIRS: byproducts of running the model's
                    # own helper, not authored assets, and fatal to IV-9 if declared
                changes.append({
                    "path": str(p), "action": "create", "diff_summary": "model-authored asset file",
                })
        return changes

    def _derive_attribution_signature(self, candidate: _CandidateState) -> "dict | None":
        """Auto-derive attribution_signature when the model hasn't given one and an
        applied edit touched a proc:/tool: node (§4.2). Marked derived=true in lineage
        via ``candidate.derived_fields`` — never overrides a model-given value."""
        # Forward note: once replay U lands, a processor_invocation signature's
        # countability must line up with U's own node-naming scheme, or the sixth
        # gate reads an honest candidate as "edited but never actually run" --
        # lineage's derived=true exists so that class can be segmented in analysis.
        for e in candidate.edits_log:
            for nid in (e.get("target_node_id"), e.get("edge_source_id"), e.get("edge_target_id")):
                if nid and (nid.startswith("proc:") or nid.startswith("tool:")):
                    spec = e.get("node_spec")
                    target = spec.get("_target_") if isinstance(spec, dict) else None
                    short = (target or nid).rsplit(".", 1)[-1].rsplit(":", 1)[-1]
                    return {"type": "processor_invocation", "tool_name": short, "expected_min_calls": 1}
        return None

    def _landing_buckets(self, candidate: _CandidateState) -> list[str]:
        """Derived bucket set — see :func:`derive_landing_buckets`.

        The candidate's ``tool_registry`` is read off disk rather than off the
        snapshot: ``_render_config_yaml`` copies that key from the parent
        verbatim, so the only way it can differ is a hand-written change, and
        those are exactly the ones a snapshot-only comparison would miss.
        """
        cand_tr = self._parent_config_dict.get("tool_registry")
        try:
            on_disk = yaml.safe_load(candidate.config_path.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError):
            on_disk = {}
        if isinstance(on_disk, dict):
            cand_tr = on_disk.get("tool_registry")
        return derive_landing_buckets(
            self._parent_snapshot,
            candidate.snapshot,
            parent_tool_registry=self._parent_config_dict.get("tool_registry"),
            candidate_tool_registry=cand_tr,
        )

    def _build_frontmatter(self, candidate: _CandidateState) -> dict:
        # The bucket AEGIS compose will obey is the one its appliers can carry,
        # not the one the model declared at Open time. Emitting the declared value
        # is what let C-R1-02 (three insert_node edits declared `config`) pass five
        # gates and land nothing -- see docs/ghx-v6-build-log.md, 08-13 autopsy.
        landing = self._landing_buckets(candidate) if candidate.edits_log else []
        if landing:
            bucket: Any = landing[0] if len(landing) == 1 else landing
        else:
            bucket = candidate.bucket
        fm: dict = {"candidate_id": candidate.candidate_id, "bucket": bucket}
        if candidate.iterates_from:
            fm["iterates_from"] = candidate.iterates_from
        fm["capability_evidence"] = candidate.manifest_fields.get("capability_evidence", [])
        fm["file_changes"] = self._file_changes_for(candidate)
        fm["predicted_impact"] = candidate.manifest_fields.get("predicted_impact") or {
            k: [] for k in _PREDICTED_IMPACT_KEYS
        }

        attribution = candidate.manifest_fields.get("attribution_signature")
        if attribution is None:
            attribution = self._derive_attribution_signature(candidate)
            if attribution is not None:
                candidate.derived_fields.add("attribution_signature")
        if attribution is not None:
            fm["attribution_signature"] = attribution

        notes = candidate.manifest_fields.get("notes")
        if notes:
            fm["notes"] = notes
        return fm

    def _build_body(self, candidate: _CandidateState) -> str:
        lines = ["## Failure Evidence", ""]
        if not candidate.edits_log:
            lines += [
                "**ZERO CONFIG DELTA -- draft.**",
                "",
                "No graph edits have been applied to this candidate yet -- config.yaml is "
                "currently byte-identical to the round parent's processors. Call "
                "GraphProposalEdit to mutate the graph, then GraphProposalManifest with "
                "failure_evidence citing a real anchor before this candidate is reviewable.",
                "",
            ]
        body_text = candidate.failure_evidence_body.strip()
        if body_text:
            lines.append(body_text)
            lines.append("")
        return "\n".join(lines)

    def _write_manifest(self, candidate: _CandidateState) -> None:
        fm = self._build_frontmatter(candidate)
        fm_yaml = yaml.safe_dump(fm, allow_unicode=True, sort_keys=False)
        body = self._build_body(candidate)
        text = f"---\n{fm_yaml}---\n{body}"
        candidate.manifest_path.parent.mkdir(parents=True, exist_ok=True)
        candidate.manifest_path.write_text(text, encoding="utf-8", newline="\n")
        candidate.last_manifest_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()

    def _lineage_dict(self, candidate: _CandidateState) -> dict:
        return {
            "candidate_id": candidate.candidate_id,
            "round": self.round_n,
            "parent_hashes": dict(self._parent_hashes),
            "child_hashes": {
                "genotype": genotype_hash(candidate.snapshot),
                "deployment": deployment_hash(candidate.snapshot),
                "phenotype": phenotype_hash(candidate.snapshot),
            },
            "edits": list(candidate.edits_log),
            "validation_reports": list(candidate.reports),
            "danger_cone": {
                "nodes": sorted(candidate.danger_nodes),
                "edges": sorted(candidate.danger_edges),
                "node_count": len(candidate.danger_nodes),
                "edge_count": len(candidate.danger_edges),
            },
            "provenance": candidate.provenance,
            "derived_fields": sorted(candidate.derived_fields),
            "hand_written_events": list(candidate.hand_written_events),
            "zero_edit": not candidate.edits_log,
            "files_emitted": [
                str(candidate.config_path), str(candidate.manifest_path), str(candidate.edits_path),
                str(candidate.lineage_json_path), str(candidate.lineage_md_path),
            ],
            "opened_at": candidate.opened_at,
            "updated_at": _utcnow(),
        }

    def _write_lineage(self, candidate: _CandidateState) -> None:
        payload = self._lineage_dict(candidate)
        candidate.lineage_json_path.parent.mkdir(parents=True, exist_ok=True)
        candidate.lineage_json_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
        candidate.lineage_md_path.write_text(
            _render_lineage_md(payload), encoding="utf-8", newline="\n")

    # ── hand-written detection ────────────────────────────────────────────────

    def _reconcile_hand_written(self, candidate: _CandidateState) -> None:
        hw = self._detect_hand_written_config(candidate)
        if hw is not None:
            candidate.hand_written_events.append(hw)
            candidate.provenance = "hand_written_detected"
        hw_m = self._detect_hand_written_manifest(candidate)
        if hw_m is not None:
            candidate.hand_written_events.append(hw_m)
            candidate.provenance = "hand_written_detected"

    def _detect_hand_written_config(self, candidate: _CandidateState) -> "dict | None":
        from ..core.harness import HarnessConfig

        path = candidate.config_path
        if not candidate.last_config_hash or not path.exists():
            return None
        disk_bytes = path.read_bytes()
        disk_hash = hashlib.sha256(disk_bytes).hexdigest()
        if disk_hash == candidate.last_config_hash:
            return None

        event: dict = {
            "kind": "config", "detected_at": _utcnow(),
            "expected_hash": candidate.last_config_hash, "disk_hash": disk_hash,
        }
        try:
            disk_cfg = HarnessConfig.from_yaml(disk_bytes.decode("utf-8")).canonicalize()
            disk_graph = to_graph(disk_cfg)
            derived = diff_graphs(candidate.snapshot, disk_graph)
            event["derived_edits"] = [_edit_to_jsonable(e) for e in derived]
        except Exception as exc:  # noqa: BLE001 -- never lose the detection itself
            event["diff_derivation_failed"] = f"{type(exc).__name__}: {exc}"
        return event

    def _detect_hand_written_manifest(self, candidate: _CandidateState) -> "dict | None":
        path = candidate.manifest_path
        if not candidate.last_manifest_hash or not path.exists():
            return None
        disk_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        if disk_hash == candidate.last_manifest_hash:
            return None
        return {
            "kind": "manifest", "detected_at": _utcnow(),
            "expected_hash": candidate.last_manifest_hash, "disk_hash": disk_hash,
        }

    # ── node inventory rendering ──────────────────────────────────────────────

    def _inventory_lines(self, snapshot: GraphSnapshot) -> str:
        proc_nodes = [(nid, n) for nid, n in snapshot.nodes.items() if n.node_type == NodeType.PROCESSOR]

        def _key(item):
            _nid, n = item
            return (n.metadata.get("_hook_", "*") or "*", n.metadata.get("_order_", 50))

        proc_nodes.sort(key=_key)
        lines = [f"{len(proc_nodes)} processor node(s):"]
        for nid, n in proc_nodes:
            meta = n.metadata
            target = meta.get("_target_", n.label)
            hook = meta.get("_hook_", "*") or "*"
            order = meta.get("_order_", 50)
            sg = meta.get("_singleton_group_", "") or "(none)"
            kwargs = meta.get("_ctor_kwargs_")
            if not isinstance(kwargs, dict):
                kwargs = {k: v for k, v in meta.items() if not k.startswith("_")}
            kw_text = ", ".join(f"{k}={v!r}" for k, v in sorted(kwargs.items())) or "(no kwargs)"
            lines.append(f"- {nid} :: {target} @ hook={hook} order={order} singleton_group={sg} :: {kw_text}")
        return "\n".join(lines)


__all__ = [
    "FLAG",
    "graph_proposals_enabled",
    "ProposalPreflightError",
    "ProposalSession",
]
