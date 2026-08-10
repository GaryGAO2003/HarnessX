"""P3 — deterministic graph-edit operators (first batch, no crossover).

Each operator is a frozen parameter record whose ``edits(snapshot)`` method
deterministically produces the ``GraphEdit`` list for ONE parent snapshot —
same operator + same snapshot → same edits.  Applicability is checked
against the parent and failures raise :class:`OperatorError` with a concrete
reason ("legal edits materialize, illegal ones explain themselves" — P3 exit
criterion).  Operators never mutate the snapshot; all mutation flows through
:func:`harnessx.graph.validate.transactional_apply`.

First batch (plan §7): MutateProcessorParams, InsertProcessor,
RemoveProcessor, ReplaceSameSingletonGroup, RewireOrdering, SwapBundle
(same boundary signature only).  Crossover is intentionally absent — the
operator kernel runs single-parent shadow mode first.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field

from .declaration import WELL_KNOWN_DECLARATIONS
from .edit import GraphEdit, GraphEditType
from .types import GraphSnapshot, NodeType
from .validate import ValidationIssue, ValidationReport, transactional_apply


class OperatorError(Exception):
    """Operator not applicable to this snapshot — message states why."""


def _persistent_processor(snapshot: GraphSnapshot, node_id: str, op: str):
    node = snapshot.nodes.get(node_id)
    if node is None:
        raise OperatorError(f"{op}: node {node_id!r} not in snapshot.nodes "
                            "(runtime overlay nodes are not editable)")
    if node.node_type is not NodeType.PROCESSOR:
        raise OperatorError(f"{op}: node {node_id!r} is {node.node_type.value}, "
                            "not a processor")
    return node


def _effective_order(node) -> int:
    """A processor's effective ``_order_`` under the SAME chain the graph sorts by.

    Fallback chain mirrors ``snapshot._order_parse`` (L4.6): explicit dict
    ``_order_`` → the well-known default for its ``_target_`` (never the 50
    "unknown" sentinel) → 0.  Keeping this identical to the sorter is what lets
    order-arithmetic below provably reproduce a claimed ``after`` in the
    EXECUTES_BEFORE chain.
    """
    meta = node.metadata
    if "_order_" in meta:
        try:
            return int(meta["_order_"])
        except (TypeError, ValueError):
            pass
    target = meta.get("_target_")
    if isinstance(target, str):
        wkd = WELL_KNOWN_DECLARATIONS.get(target)
        if wkd is not None and wkd.order != 50:
            return wkd.order
    return 0


def _known_singleton_groups(snapshot: GraphSnapshot) -> "dict[str, str]":
    """Map each *explicit* ``_singleton_group_`` → a representative processor id,
    over persistent AND runtime processors.

    Mirrors the ``known_sgs`` set the S2 validator builds (metadata only, no WKD
    fallback), so any group name written into ``_after_`` here resolves there
    with no ``unresolved_after`` warning.
    """
    groups: "dict[str, str]" = {}
    for nodes in (snapshot.nodes, snapshot.runtime_nodes):
        for nid, node in nodes.items():
            if node.node_type is not NodeType.PROCESSOR:
                continue
            sg = node.metadata.get("_singleton_group_")
            if isinstance(sg, str) and sg:
                groups.setdefault(sg, nid)
    return groups


# ── operators ───────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class MutateProcessorParams:
    """Change constructor kwargs of one processor (metadata keys untouched)."""

    node_id: str
    param_changes: dict = field(default_factory=dict)

    def edits(self, snapshot: GraphSnapshot) -> "list[GraphEdit]":
        node = _persistent_processor(snapshot, self.node_id, "MutateProcessorParams")
        if not self.param_changes:
            raise OperatorError("MutateProcessorParams: param_changes is empty")
        bad = [k for k in self.param_changes if not isinstance(k, str) or k.startswith("_")]
        if bad:
            raise OperatorError(
                f"MutateProcessorParams: {bad} are metadata keys, not ctor "
                "params — use RewireOrdering for ordering metadata")
        current = node.metadata.get("_ctor_kwargs_")
        merged = {**(current if isinstance(current, dict) else {}),
                  **deepcopy(self.param_changes)}
        return [GraphEdit(
            edit_type=GraphEditType.MUTATE_INACTIVE,
            target_node_id=self.node_id,
            node_changes={"_ctor_kwargs_": merged},
            reason=f"mutate params {sorted(self.param_changes)}",
        )]


@dataclass(frozen=True)
class InsertProcessor:
    """Insert a new serialized processor node from a full spec dict."""

    spec: dict = field(default_factory=dict)

    def edits(self, snapshot: GraphSnapshot) -> "list[GraphEdit]":
        target = self.spec.get("_target_", "")
        if not isinstance(target, str) or not target:
            raise OperatorError("InsertProcessor: spec._target_ must be a non-empty str")
        sg = self.spec.get("_singleton_group_", "")
        if isinstance(sg, str) and sg:
            taken = {n.metadata.get("_singleton_group_")
                     for n in snapshot.nodes.values()
                     if n.node_type is NodeType.PROCESSOR}
            if sg in taken:
                raise OperatorError(
                    f"InsertProcessor: singleton_group {sg!r} already claimed — "
                    "use ReplaceSameSingletonGroup instead")
        return [GraphEdit(
            edit_type=GraphEditType.INSERT_NODE,
            node_spec=deepcopy(self.spec),
            reason=f"insert {target}",
        )]


@dataclass(frozen=True)
class RemoveProcessor:
    """Remove one persistent processor node (incident edges go with it)."""

    node_id: str

    def edits(self, snapshot: GraphSnapshot) -> "list[GraphEdit]":
        _persistent_processor(snapshot, self.node_id, "RemoveProcessor")
        return [GraphEdit(
            edit_type=GraphEditType.REMOVE_NODE,
            target_node_id=self.node_id,
            reason=f"remove {self.node_id}",
        )]


@dataclass(frozen=True)
class ReplaceSameSingletonGroup:
    """Replace a processor with an alternative from the SAME singleton group."""

    node_id: str
    new_spec: dict = field(default_factory=dict)

    def edits(self, snapshot: GraphSnapshot) -> "list[GraphEdit]":
        node = _persistent_processor(snapshot, self.node_id, "ReplaceSameSingletonGroup")
        target = self.new_spec.get("_target_", "")
        if not isinstance(target, str) or not target:
            raise OperatorError("ReplaceSameSingletonGroup: new_spec._target_ required")
        old_sg = node.metadata.get("_singleton_group_", "")
        new_sg = self.new_spec.get("_singleton_group_", "")
        if new_sg != old_sg:
            raise OperatorError(
                f"ReplaceSameSingletonGroup: group mismatch "
                f"({new_sg!r} != {old_sg!r}) — ordering continuity requires the "
                "same singleton_group")
        return [GraphEdit(
            edit_type=GraphEditType.REPLACE_SAME_GROUP,
            target_node_id=self.node_id,
            node_spec=deepcopy(self.new_spec),
            reason=f"replace {self.node_id} within group {old_sg!r}",
        )]


@dataclass(frozen=True)
class RewireOrdering:
    """Change a processor's ``_order_`` and/or ``_after_`` metadata.

    Invariant (P0 fix): a claimed ``X after Y`` must be *provably* reflected in
    the effective ordering or no edit is produced.  Each ``after`` reference is
    resolved against the parent graph — a ``proc:<id>`` whose target carries a
    singleton_group is translated to that group name (the only form the graph
    resolves ``_after_`` by); a target with no group is expressed arithmetically
    (edited ``_order_`` = target's effective order + 1); a target absent from the
    graph is fail-closed.  A bare group-name reference must already name a
    singleton_group present in the graph.  This prevents a ``proc:``-namespaced
    reference from being silently ignored as an unresolved soft dep (a no-op
    edit that the ledger would otherwise bank as a real candidate).
    """

    node_id: str
    order: "int | None" = None
    after: "tuple[str, ...] | None" = None

    def edits(self, snapshot: GraphSnapshot) -> "list[GraphEdit]":
        _persistent_processor(snapshot, self.node_id, "RewireOrdering")
        if self.order is None and self.after is None:
            raise OperatorError("RewireOrdering: nothing to change (order and after both None)")
        changes: dict = {}

        order_floor: "int | None" = None
        if self.after is not None:
            if not all(isinstance(a, str) and a for a in self.after):
                raise OperatorError("RewireOrdering: after entries must be non-empty strs")
            known_groups = _known_singleton_groups(snapshot)
            resolved_after: "list[str]" = []
            for ref in self.after:
                if ref.startswith("proc:"):
                    target = snapshot.nodes.get(ref) or snapshot.runtime_nodes.get(ref)
                    if target is None:
                        raise OperatorError(
                            f"RewireOrdering: _after_ reference {ref!r} resolves to no "
                            "node in the graph — the claimed ordering cannot hold")
                    sg = target.metadata.get("_singleton_group_")
                    if isinstance(sg, str) and sg:
                        # graph resolves _after_ by group name → translate the id
                        if sg not in resolved_after:
                            resolved_after.append(sg)
                    else:
                        # no singleton_group → _after_ could never name it; make the
                        # ordering hold arithmetically (order strictly past the target)
                        floor = _effective_order(target) + 1
                        order_floor = floor if order_floor is None else max(order_floor, floor)
                else:
                    # bare group name — must already exist or the graph drops it as
                    # an unresolved soft dep (the exact silent no-op we fail closed on)
                    if ref not in known_groups:
                        raise OperatorError(
                            f"RewireOrdering: _after_ reference {ref!r} is not a "
                            "singleton_group present in the graph")
                    if ref not in resolved_after:
                        resolved_after.append(ref)
            if resolved_after:
                changes["_after_"] = resolved_after

        if self.order is not None:
            if not isinstance(self.order, int):
                raise OperatorError(f"RewireOrdering: order must be int, got {self.order!r}")
            changes["_order_"] = self.order
        if order_floor is not None:
            # order arithmetic must clear every no-group target (and any explicit
            # order the caller also gave) so the claimed after actually holds
            changes["_order_"] = max(order_floor, changes.get("_order_", order_floor))

        if not changes:
            raise OperatorError(
                "RewireOrdering: nothing to change (references produced no effective edit)")

        return [GraphEdit(
            edit_type=GraphEditType.MUTATE_INACTIVE,
            target_node_id=self.node_id,
            node_changes=changes,
            reason=f"rewire ordering of {self.node_id}",
        )]


@dataclass(frozen=True)
class SwapBundle:
    """Swap a bundle's child graph — accepted ONLY for an identical boundary
    signature (plan §7); a missing signature on either side is a reject."""

    bundle_node_id: str
    replacement_bundle_id: str
    replacement_signature: str = ""

    def edits(self, snapshot: GraphSnapshot) -> "list[GraphEdit]":
        node = snapshot.nodes.get(self.bundle_node_id)
        if node is None:
            raise OperatorError(f"SwapBundle: node {self.bundle_node_id!r} not in snapshot")
        if node.node_type is not NodeType.BUNDLE:
            raise OperatorError(
                f"SwapBundle: node {self.bundle_node_id!r} is "
                f"{node.node_type.value}, not a bundle")
        if not self.replacement_bundle_id:
            raise OperatorError("SwapBundle: replacement_bundle_id required")
        current_sig = node.metadata.get("interface_signature", "")
        if not current_sig or not self.replacement_signature:
            raise OperatorError(
                "SwapBundle: both bundles must declare interface_signature — "
                "boundary compatibility cannot be verified")
        if current_sig != self.replacement_signature:
            raise OperatorError(
                f"SwapBundle: boundary signature mismatch "
                f"({self.replacement_signature!r} != {current_sig!r})")
        return [GraphEdit(
            edit_type=GraphEditType.SWAP_SUBGRAPH,
            target_node_id=self.bundle_node_id,
            replacement_bundle_id=self.replacement_bundle_id,
            reason=f"swap bundle {self.bundle_node_id} -> {self.replacement_bundle_id}",
        )]


# ── one-call kernel entry ───────────────────────────────────────────────────


def apply_operator(
    snapshot: GraphSnapshot,
    operator,
    *,
    materialize: bool = True,
) -> "tuple[GraphSnapshot | None, ValidationReport]":
    """Run one operator through the full P3 transaction.

    Operator applicability failures come back as a rejected report
    (``operator_precondition``) instead of an exception, so shadow-mode
    callers have a single fail-closed entrypoint.

    Candidate identity (``materialize=True``): the returned snapshot is the S4
    build **fixed point** (``report.fixed_point`` — the config re-graphed via
    ``build_from_config → to_graph``), NOT the raw ``apply_edits`` result.  The
    edited result carries stale derived ``EXECUTES_BEFORE`` chains (L5.5) and
    inserted nodes with only partial metadata, so its genotype/deployment hash
    would not match the identity that a persisted-then-reloaded config re-graphs
    to.  Returning the fixed point makes ``genotype_hash``/``deployment_hash`` of
    this snapshot equal what materialization writes to disk and re-hashes — and,
    as a side effect, two textually different edits that build to the same graph
    collapse onto one genotype (natural dedup).  ``materialize=False`` skips the
    build entirely and returns the ``apply_edits`` result unchanged (for callers
    whose targets are not importable and who re-graph in a later stage).
    """
    try:
        edits = operator.edits(snapshot)
    except OperatorError as exc:
        return None, ValidationReport(passed=False, issues=[ValidationIssue(
            "S0", "operator_precondition", str(exc))])
    result, report = transactional_apply(snapshot, edits, materialize=materialize)
    if materialize and report.fixed_point is not None:
        return report.fixed_point, report
    return result, report
