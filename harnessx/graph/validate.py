"""P3 — fail-closed graph validation (S0–S4 layers).

Layered checks, every failing layer REJECTS (candidate never proceeds):

    S0  schema / types / field presence / ID format
    S1  endpoint existence, edge-kind ↔ node-kind legality
    S2  hook coverage, singleton uniqueness, order/after consistency
    S3  slot & event-field interface signatures
    S4  transactional apply: roundtrip, build, hash stability

S5 (smoke / held-out runtime evaluation) is NOT here — it only contributes
to a candidate's score, never to admission.

The "owner" facet of S2 is enforced at Harness construction by the runtime
owner registry (``claim_owners``) — a snapshot carries no ownership
information, so this module does not re-check it.

Derived-edge caveat: ``EXECUTES_BEFORE`` chains on an ``apply_edits`` result
are stale until the next ``to_graph()`` (L5.5); S4's re-graph step is where
fresh chains are recomputed and checked for conflicts.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..core.processor import PROCESSOR_HOOK_NAMES
from ..core.runtime import (
    HarnessConflictError,
    RoutingEnvelope,
    RuntimeReg,
    stable_topological_sort,
)
from .edit import GraphEdit, GraphEditError, GraphEditType, apply_edits
from .types import EdgeType, GraphSnapshot, NodeType

# ── report types ────────────────────────────────────────────────────────────


@dataclass
class ValidationIssue:
    """One validation finding, tagged with its layer."""

    layer: str  # "S0" … "S4"
    error_type: str
    message: str
    node_ids: list[str] = field(default_factory=list)
    edge_ids: list[str] = field(default_factory=list)


@dataclass
class ValidationReport:
    """Outcome of a validation pass.  ``issues`` reject; ``warnings`` don't."""

    passed: bool
    issues: list[ValidationIssue] = field(default_factory=list)
    warnings: list[ValidationIssue] = field(default_factory=list)
    # S4 fixed point: the re-graph snapshot of the built config (build → to_graph).
    # Populated ONLY when a materialize pass runs clean; None otherwise (including
    # materialize=False).  Carries the candidate's canonical build-time identity —
    # fresh EXECUTES_BEFORE chains + full builder metadata — as opposed to the
    # apply_edits result, whose derived chains are stale (L5.5) and whose inserted
    # nodes hold only partial metadata.  Intentionally excluded from ``reason()``
    # and from any serialization of issues/warnings.
    fixed_point: GraphSnapshot | None = None

    def reason(self) -> str:
        return "; ".join(f"[{i.layer}:{i.error_type}] {i.message}" for i in self.issues)


def _report(issues: list, warnings: list, *,
            fixed_point: "GraphSnapshot | None" = None) -> ValidationReport:
    return ValidationReport(passed=not issues, issues=issues, warnings=warnings,
                            fixed_point=fixed_point)


def _edge_key(edge) -> str:
    return f"{edge.source_id}->{edge.target_id}:{edge.edge_type.value}"


# ── S1 edge-kind ↔ node-kind legality matrix ────────────────────────────────

_P = NodeType.PROCESSOR
_H = NodeType.SKELETON_HOOK
_S = NodeType.SLOT
_B = NodeType.BUNDLE
_OBSERVED_ENDS = {_P, _H, _S}

_EDGE_KIND_MATRIX: "dict[EdgeType, tuple[set, set]]" = {
    EdgeType.ATTACHED_TO: ({_P}, {_H}),
    EdgeType.AFTER: ({_P}, {_P}),
    EdgeType.WRITES_TO: ({_P}, {_S}),
    EdgeType.READS_FROM: ({_P}, {_S}),
    EdgeType.COMPOSES_WITH: ({_P, _B}, {_P, _B}),
    EdgeType.CONFLICTS_WITH: ({_P}, {_P}),
    EdgeType.SPECIALIZES: ({NodeType.SKILL, NodeType.TOOL},
                           {NodeType.SKILL, NodeType.TOOL}),
    EdgeType.LOOP_BACK: ({_H}, {_H}),
    EdgeType.EXECUTES_BEFORE: ({_P}, {_P}),
    # observed edges are trace evidence — shapes vary, keep permissive
    EdgeType.OBSERVED_CONTROL: (_OBSERVED_ENDS, _OBSERVED_ENDS),
    EdgeType.OBSERVED_DATA: (_OBSERVED_ENDS, _OBSERVED_ENDS),
}

_PROC_LIST_KEYS = (
    "_hooks_", "_after_", "_writes_slots_", "_reads_slots_",
    "_reads_event_fields_", "_writes_event_fields_",
)


# ── S0 on the edit list (preconditions, before anything mutates) ────────────


def validate_edit_preconditions(
    snapshot: GraphSnapshot, edits: "list[GraphEdit]",
) -> ValidationReport:
    """S0 over the edit list itself — reject malformed edits before apply."""
    issues: list[ValidationIssue] = []

    def _bad(i: int, error_type: str, msg: str) -> None:
        issues.append(ValidationIssue("S0", error_type, f"edit[{i}]: {msg}"))

    for i, edit in enumerate(edits):
        if not isinstance(edit, GraphEdit):
            _bad(i, "not_an_edit", f"got {type(edit).__name__}")
            continue
        if not isinstance(edit.edit_type, GraphEditType):
            _bad(i, "bad_edit_type", f"edit_type={edit.edit_type!r}")
            continue
        et = edit.edit_type
        if et in (GraphEditType.INSERT_NODE, GraphEditType.REPLACE_SAME_GROUP):
            if not isinstance(edit.node_spec, dict):
                _bad(i, "missing_node_spec", f"{et.value} requires a node_spec dict")
            else:
                target = edit.node_spec.get("_target_", "")
                if not isinstance(target, str) or not target:
                    _bad(i, "missing_target", f"{et.value}: node_spec._target_ must be a non-empty str")
        if et in (GraphEditType.REMOVE_NODE, GraphEditType.REPLACE_SAME_GROUP,
                  GraphEditType.SWAP_SUBGRAPH, GraphEditType.MUTATE_INACTIVE):
            if not isinstance(edit.target_node_id, str) or not edit.target_node_id:
                _bad(i, "missing_target_node", f"{et.value} requires target_node_id")
        if et is GraphEditType.MUTATE_INACTIVE and not isinstance(edit.node_changes, dict):
            _bad(i, "missing_node_changes", "MUTATE_INACTIVE requires node_changes dict")
        if et is GraphEditType.SWAP_SUBGRAPH and not edit.replacement_bundle_id:
            _bad(i, "missing_bundle_id", "SWAP_SUBGRAPH requires replacement_bundle_id")
        if et is GraphEditType.CHANGE_DEPENDENCY:
            if not edit.edge_source_id or not edit.edge_target_id:
                _bad(i, "missing_endpoints", "CHANGE_DEPENDENCY requires both endpoints")
            if not isinstance(edit.edge_type, EdgeType):
                _bad(i, "bad_edge_type", f"edge_type={edit.edge_type!r}")
    return _report(issues, [])


# ── S0–S3 static snapshot validation ────────────────────────────────────────


def validate_snapshot(snapshot: GraphSnapshot) -> ValidationReport:
    """Static fail-closed validation of a snapshot (layers S0–S3)."""
    issues: list[ValidationIssue] = []
    warnings: list[ValidationIssue] = []

    _s0_nodes(snapshot, issues)
    _s1_edges(snapshot, issues)
    _s2_hooks_groups_order(snapshot, issues, warnings)
    _s3_interfaces(snapshot, issues, warnings)
    return _report(issues, warnings)


def _s0_nodes(snapshot: GraphSnapshot, issues: list) -> None:
    """S0: id format, key consistency, metadata field types."""
    containers = (
        ("nodes", snapshot.nodes, {
            NodeType.SKELETON_HOOK: ("hook:",),
            NodeType.PROCESSOR: ("proc:",),
            NodeType.SLOT: ("slot:",),
        }),
        ("runtime_nodes", snapshot.runtime_nodes, {
            NodeType.PROCESSOR: ("rt:",),
            NodeType.SLOT: ("rt:slot:",),
        }),
    )
    for cname, nodes, prefixes in containers:
        for nid, node in nodes.items():
            if not isinstance(nid, str) or not nid:
                issues.append(ValidationIssue(
                    "S0", "bad_node_id", f"{cname}: empty/non-str node id {nid!r}"))
                continue
            if node.node_id != nid:
                issues.append(ValidationIssue(
                    "S0", "id_key_mismatch",
                    f"{cname}[{nid}].node_id == {node.node_id!r}", [nid]))
            if not isinstance(node.node_type, NodeType):
                issues.append(ValidationIssue(
                    "S0", "bad_node_type",
                    f"{cname}[{nid}]: node_type={node.node_type!r}", [nid]))
                continue
            expected = prefixes.get(node.node_type)
            if expected is not None and not nid.startswith(expected):
                issues.append(ValidationIssue(
                    "S0", "bad_id_prefix",
                    f"{cname}[{nid}]: {node.node_type.value} id must start with "
                    f"{' or '.join(expected)}", [nid]))
            if node.node_type is NodeType.PROCESSOR:
                _s0_processor_metadata(cname, nid, node.metadata, issues)


def _s0_processor_metadata(cname: str, nid: str, meta: dict, issues: list) -> None:
    target = meta.get("_target_", "")
    if not isinstance(target, str) or not target:
        issues.append(ValidationIssue(
            "S0", "missing_target", f"{cname}[{nid}]: _target_ must be a non-empty str", [nid]))
    for key in _PROC_LIST_KEYS:
        if key in meta and not isinstance(meta[key], (list, tuple)):
            issues.append(ValidationIssue(
                "S0", "bad_field_type", f"{cname}[{nid}]: {key} must be a list", [nid]))
    if "_order_" in meta and not isinstance(meta["_order_"], int):
        issues.append(ValidationIssue(
            "S0", "bad_field_type", f"{cname}[{nid}]: _order_ must be an int", [nid]))
    for key in ("_hook_", "_singleton_group_"):
        if key in meta and not isinstance(meta[key], str):
            issues.append(ValidationIssue(
                "S0", "bad_field_type", f"{cname}[{nid}]: {key} must be a str", [nid]))


def _s1_edges(snapshot: GraphSnapshot, issues: list) -> None:
    """S1: endpoint existence + edge-kind/node-kind legality."""
    all_nodes: dict = {**snapshot.nodes, **snapshot.runtime_nodes}
    for container, edges in (("edges", snapshot.edges),
                             ("runtime_edges", snapshot.runtime_edges)):
        for edge in edges:
            if not isinstance(edge.edge_type, EdgeType):
                issues.append(ValidationIssue(
                    "S1", "bad_edge_type",
                    f"{container}: edge_type={edge.edge_type!r}",
                    edge_ids=[f"{edge.source_id}->{edge.target_id}"]))
                continue
            src = all_nodes.get(edge.source_id)
            tgt = all_nodes.get(edge.target_id)
            if src is None or tgt is None:
                missing = edge.source_id if src is None else edge.target_id
                issues.append(ValidationIssue(
                    "S1", "dangling_endpoint",
                    f"{container}: {_edge_key(edge)} references missing node {missing!r}",
                    node_ids=[missing], edge_ids=[_edge_key(edge)]))
                continue
            legal = _EDGE_KIND_MATRIX.get(edge.edge_type)
            if legal is None:
                continue  # unknown-but-typed edge kinds pass S1 shape checks
            src_kinds, tgt_kinds = legal
            if src.node_type not in src_kinds or tgt.node_type not in tgt_kinds:
                issues.append(ValidationIssue(
                    "S1", "illegal_edge_kind",
                    f"{container}: {edge.edge_type.value} may not connect "
                    f"{src.node_type.value} -> {tgt.node_type.value}",
                    node_ids=[edge.source_id, edge.target_id],
                    edge_ids=[_edge_key(edge)]))


def _s2_hooks_groups_order(snapshot: GraphSnapshot, issues: list, warnings: list) -> None:
    """S2: hook coverage validity, singleton uniqueness, order/after consistency."""
    valid_hooks = set(PROCESSOR_HOOK_NAMES) | {"*"}
    all_nodes: dict = {**snapshot.nodes, **snapshot.runtime_nodes}

    # hook coverage entries must be processor hooks (or "*")
    for nid, node in all_nodes.items():
        if node.node_type is not NodeType.PROCESSOR:
            continue
        for h in node.metadata.get("_hooks_", []) or []:
            if h not in valid_hooks:
                issues.append(ValidationIssue(
                    "S2", "invalid_hook",
                    f"{nid}: _hooks_ entry {h!r} is not a processor hook", [nid]))

    # ATTACHED_TO must land on the 8 processor hooks — never hook:model/tool
    allowed_targets = {f"hook:{h}" for h in PROCESSOR_HOOK_NAMES}
    for edges in (snapshot.edges, snapshot.runtime_edges):
        for edge in edges:
            if edge.edge_type is EdgeType.ATTACHED_TO \
                    and edge.target_id.startswith("hook:") \
                    and edge.target_id not in allowed_targets:
                issues.append(ValidationIssue(
                    "S2", "non_processor_hook",
                    f"{_edge_key(edge)}: processors never attach to "
                    f"{edge.target_id}", [edge.source_id], [_edge_key(edge)]))

    # singleton uniqueness: duplicates among persistent processors reject;
    # a persistent/runtime overlap is only a warning (overlay may legitimately
    # mirror a group while a replacement is staged)
    def _groups(nodes) -> "dict[str, list[str]]":
        out: dict[str, list[str]] = {}
        for nid, node in nodes.items():
            if node.node_type is not NodeType.PROCESSOR:
                continue
            sg = node.metadata.get("_singleton_group_")
            if isinstance(sg, str) and sg:
                out.setdefault(sg, []).append(nid)
        return out

    persistent_groups = _groups(snapshot.nodes)
    runtime_groups = _groups(snapshot.runtime_nodes)
    for sg, nids in persistent_groups.items():
        if len(nids) > 1:
            issues.append(ValidationIssue(
                "S2", "singleton_conflict",
                f"singleton_group {sg!r} claimed by {len(nids)} persistent processors",
                node_ids=list(nids)))
    for sg in set(persistent_groups) & set(runtime_groups):
        warnings.append(ValidationIssue(
            "S2", "singleton_overlay_overlap",
            f"singleton_group {sg!r} exists in both the persistent graph and the runtime overlay",
            node_ids=persistent_groups[sg] + runtime_groups[sg]))

    # after references: unresolved is a soft dep (builder semantics) → warning
    known_sgs = set(persistent_groups) | set(runtime_groups)
    for nid, node in all_nodes.items():
        if node.node_type is not NodeType.PROCESSOR:
            continue
        for after_sg in node.metadata.get("_after_", []) or []:
            if after_sg not in known_sgs:
                warnings.append(ValidationIssue(
                    "S2", "unresolved_after",
                    f"{nid}: _after_ reference {after_sg!r} not in graph", [nid]))

    # order/after consistency: dry-run the SHARED sorter per bucket over
    # persistent processors (cross-order contradiction / same-order cycle)
    buckets: "dict[str, list[RoutingEnvelope]]" = {}
    for i, (nid, node) in enumerate(snapshot.nodes.items()):
        if node.node_type is not NodeType.PROCESSOR:
            continue
        meta = node.metadata
        hook = meta.get("_hook_")
        if not isinstance(hook, str) or not hook:
            continue  # bucket unknown → no order constraint to check
        order = meta.get("_order_", 0)
        if not isinstance(order, int):
            order = 0
        sg = meta.get("_singleton_group_")
        after = meta.get("_after_", ()) or ()
        buckets.setdefault(hook, []).append(RoutingEnvelope(RuntimeReg(
            proc=None,
            hook=hook,
            order=order,
            singleton_group=sg if isinstance(sg, str) and sg else None,
            after=tuple(a for a in after if isinstance(a, str)),
        ), i))
    for hook, envs in buckets.items():
        try:
            stable_topological_sort(
                envs,
                order_key=lambda e: e.reg.order,
                after_key=lambda e: e.reg.after,
                group_key=lambda e: e.reg.singleton_group or "",
                seq_key=lambda e: e.seq,
            )
        except HarnessConflictError as exc:
            issues.append(ValidationIssue(
                "S2", "order_conflict",
                f"bucket {hook!r}: {exc}"))


def _s3_interfaces(snapshot: GraphSnapshot, issues: list, warnings: list) -> None:
    """S3: slot edges must be declaration-backed; event fields well-formed.

    Family-differential treatment (Δ7): only DATA_FLOW edges carry interface
    obligations — control edges are checked by topology/ordering layers
    (S1/S2), never by declaration backing.  A ``data_channel`` tag, when
    present, must be one of the five ADFG channels.
    """
    from .types import DataChannel

    all_nodes: dict = {**snapshot.nodes, **snapshot.runtime_nodes}
    valid_channels = {c.value for c in DataChannel}

    for container, edges in (("edges", snapshot.edges),
                             ("runtime_edges", snapshot.runtime_edges)):
        for edge in edges:
            channel = edge.metadata.get("data_channel")
            if channel is not None and channel not in valid_channels:
                issues.append(ValidationIssue(
                    "S3", "bad_data_channel",
                    f"{container}: {_edge_key(edge)} carries unknown "
                    f"data_channel {channel!r} (ADFG five-way: "
                    f"{sorted(valid_channels)})",
                    edge_ids=[_edge_key(edge)]))
            if edge.edge_type not in (EdgeType.WRITES_TO, EdgeType.READS_FROM):
                continue
            src = all_nodes.get(edge.source_id)
            tgt = all_nodes.get(edge.target_id)
            if src is None or tgt is None:
                continue  # S1 already rejected the dangling endpoint
            slot_key = tgt.metadata.get("slot_name", tgt.label)
            decl_key = ("_writes_slots_" if edge.edge_type is EdgeType.WRITES_TO
                        else "_reads_slots_")
            declared = src.metadata.get(decl_key, []) or []
            if slot_key not in declared:
                issues.append(ValidationIssue(
                    "S3", "undeclared_slot_edge",
                    f"{container}: {_edge_key(edge)} has no backing {decl_key} "
                    f"declaration for slot {slot_key!r}",
                    node_ids=[edge.source_id], edge_ids=[_edge_key(edge)]))

    for nid, node in all_nodes.items():
        if node.node_type is NodeType.PROCESSOR:
            for key in ("_reads_event_fields_", "_writes_event_fields_"):
                for entry in node.metadata.get(key, []) or []:
                    if not isinstance(entry, str) or "." not in entry:
                        issues.append(ValidationIssue(
                            "S3", "bad_event_field",
                            f"{nid}: {key} entry {entry!r} is not "
                            f"'EventClass.field'", [nid]))
        elif node.node_type is NodeType.BUNDLE:
            if not node.metadata.get("child_graph_id"):
                warnings.append(ValidationIssue(
                    "S3", "bundle_missing_child",
                    f"{nid}: bundle has no child_graph_id", [nid]))


# ── S4 transactional apply ──────────────────────────────────────────────────


def transactional_apply(
    snapshot: GraphSnapshot,
    edits: "list[GraphEdit]",
    *,
    materialize: bool = True,
) -> "tuple[GraphSnapshot | None, ValidationReport]":
    """The P3 transaction protocol — all-or-nothing, original untouched.

    deepcopy(parent) → validate edit preconditions → apply all edits →
    validate full snapshot → (hashes cleared by apply_edits) → materialize
    config → build → re-graph → compare invariants → commit or discard.

    Returns ``(result, report)``; ``result`` is ``None`` whenever
    ``report.passed`` is False.  ``materialize=False`` stops after the static
    snapshot validation (S0–S3) — for callers whose targets are not importable
    in the current environment and who run build in a later stage.
    """
    pre = validate_edit_preconditions(snapshot, edits)
    if not pre.passed:
        return None, pre

    try:
        result = apply_edits(snapshot, edits)
    except (GraphEditError, HarnessConflictError) as exc:
        return None, _report([ValidationIssue(
            "S4", "apply_failed", str(exc))], [])
    except Exception as exc:  # noqa: BLE001 — fail closed, never propagate
        return None, _report([ValidationIssue(
            "S4", "apply_error", f"{type(exc).__name__}: {exc}")], [])

    full = validate_snapshot(result)
    if not full.passed:
        return None, full

    fixed_point: GraphSnapshot | None = None
    if materialize:
        issues, fixed_point = _s4_materialize(result)
        if issues:
            return None, _report(issues, full.warnings)

    return result, _report([], full.warnings, fixed_point=fixed_point)


def _s4_materialize(
    result: GraphSnapshot,
) -> "tuple[list[ValidationIssue], GraphSnapshot | None]":
    """S4: graph → config dict → build → re-graph, all fail-closed.

    Returns ``(issues, fixed_point)``.  ``fixed_point`` is the ``re1`` re-graph
    snapshot — the build output ``to_graph(build_from_config(...))`` — and is
    returned ONLY when the pass is clean (no issues); on any failure or drift it
    is ``None``.  This snapshot is the candidate's canonical build-time identity:
    the same one that a persisted config would re-graph to, with fresh derived
    chains and full builder metadata.
    """
    from harnessx.core.builder import build_from_config
    from .identity import genotype_hash
    from .snapshot import to_graph
    from .transform import graph_to_config_dict

    issues: list[ValidationIssue] = []

    try:
        config_dict = graph_to_config_dict(result)
    except Exception as exc:  # noqa: BLE001
        return [ValidationIssue(
            "S4", "materialize_failed", f"{type(exc).__name__}: {exc}")], None

    try:
        config = build_from_config(config_dict)
    except HarnessConflictError as exc:
        conflicts = getattr(exc, "conflicts", None) or [str(exc)]
        return [ValidationIssue("S4", "build_conflict", str(c))
                for c in conflicts], None
    except Exception as exc:  # noqa: BLE001 — ImportError included: FAIL-CLOSED
        return [ValidationIssue(
            "S4", "build_failed", f"{type(exc).__name__}: {exc}")], None

    try:
        re1 = to_graph(config)
        re2 = to_graph(config)
        if genotype_hash(re1) != genotype_hash(re2):
            issues.append(ValidationIssue(
                "S4", "hash_unstable",
                "re-graph genotype hash differs across two exports"))
    except HarnessConflictError as exc:
        return [ValidationIssue("S4", "regraph_conflict", str(exc))], None
    except Exception as exc:  # noqa: BLE001
        return [ValidationIssue(
            "S4", "regraph_failed", f"{type(exc).__name__}: {exc}")], None

    # invariant: the persistent processor target multiset survives the
    # materialize→build round-trip (nothing silently dropped or fabricated)
    def _targets(snap: GraphSnapshot) -> "list[str]":
        return sorted(
            n.metadata.get("_target_", "")
            for n in snap.nodes.values()
            if n.node_type is NodeType.PROCESSOR
        )

    if _targets(result) != _targets(re1):
        issues.append(ValidationIssue(
            "S4", "materialize_drift",
            f"processor targets changed across materialize/build: "
            f"{_targets(result)} != {_targets(re1)}"))
    # clean pass → hand back the build fixed point as the candidate identity;
    # any late-appended issue (hash_unstable / materialize_drift) suppresses it
    return issues, (None if issues else re1)
