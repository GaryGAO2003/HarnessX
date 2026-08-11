"""Graph export — convert a HarnessConfig into a typed GraphSnapshot.

``to_graph(config)`` is the primary entry point.  It walks the config's
flat processor list and produces nodes + typed edges without changing any
existing behaviour (pure read).
"""

from __future__ import annotations

import logging
from copy import deepcopy
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..core.processor import PROCESSOR_HOOK_NAMES
from ..core.runtime import RuntimeReg, SerializedReg, coerce_runtime_reg
from .types import (
    SKELETON_HOOK_NAMES,
    Edge,
    EdgeType,
    GraphSnapshot,
    Node,
    NodeType,
)

_log = logging.getLogger(__name__)

if TYPE_CHECKING:
    from harnessx.core.harness import HarnessConfig


# ── skeleton hooks ──────────────────────────────────────────────────────────


def _make_skeleton_nodes() -> dict[str, Node]:
    """Create the 10 fixed runloop hook nodes."""
    nodes: dict[str, Node] = {}
    for name in SKELETON_HOOK_NAMES:
        node_id = f"hook:{name}"
        nodes[node_id] = Node(
            node_id=node_id,
            node_type=NodeType.SKELETON_HOOK,
            label=name,
            metadata={"hook_name": name},
        )
    return nodes


# ── loop-back edge ──────────────────────────────────────────────────────────


def _make_loop_back_edge() -> Edge:
    """The single back-edge that makes the hook skeleton a cyclic graph."""
    return Edge(
        source_id="hook:task_end",
        target_id="hook:step_start",
        edge_type=EdgeType.LOOP_BACK,
        metadata={},
    )


# ── processor extraction ───────────────────────────────────────────────────


def _compute_slug(target: str) -> str:
    """CamelCase → snake_case, without any prefix (L4.0).

    Persistent nodes use ``f"proc:{_compute_slug(...)}"``; runtime nodes use
    ``f"rt:{_compute_slug(...)}"`` — both share this slug computation.
    """
    short = target.rsplit(".", 1)[-1] if "." in target else target
    slug = ""
    for i, ch in enumerate(short):
        if ch.isupper() and i > 0 and (short[i - 1].islower() or (i + 1 < len(short) and short[i + 1].islower())):
            slug += "_"
        slug += ch.lower()
    return slug


@dataclass(frozen=True)
class ProcNodeIds:
    """Result of the single PROCESSOR node-id authority (both schemes).

    - ``persistent`` — ``(dict_ref, "proc:…")`` for every serialized processor
      dict, in ``config.processors`` order with the per-target disambiguation
      counter (count-all: every dict with a non-empty ``_target_``).
    - ``runtime`` — ``(proc, "rt:…")`` for every runtime-overlay processor,
      canonical ``_rt_procs`` first then instance-plugin procs (id-deduped).
    """

    persistent: list
    runtime: list


def assign_processor_node_ids(config: "HarnessConfig") -> ProcNodeIds:
    """THE authority for PROCESSOR node-id assignment (L4.0 / L5.1).

    This is the ONLY place either the persistent ``proc:`` scheme or the
    runtime-overlay ``rt:`` scheme is formed.  ``to_graph``,
    ``_add_runtime_overlay``, ``_add_executes_before_edges`` and
    ``build_node_binding`` all derive their ids from here — there is no second
    per-target walk kept in sync by comment.
    """
    # ── persistent proc: ids (config order, count-all per target) ───────────
    persistent: list = []
    seen_targets: dict[str, int] = {}
    for proc_dict in config.processors or []:
        if not isinstance(proc_dict, dict):
            continue
        target = proc_dict.get("_target_", "")
        if not target:
            continue
        seen_targets[target] = seen_targets.get(target, 0) + 1
        index = seen_targets[target]
        node_id = f"proc:{_compute_slug(target)}"
        if index > 1:
            node_id = f"{node_id}__{index}"
        persistent.append((proc_dict, node_id))

    # ── runtime-overlay rt: ids (canonical _rt_procs, then plugin procs) ─────
    # rt: ids can only collide with other rt: ids — the prefix is disjoint from
    # proc:/hook:/slot:, so seeding the disambiguation set with the persistent
    # nodes (as the overlay's node_id_seen does) never affects an rt: id.  An
    # empty set reproduces the overlay's ids exactly.
    runtime: list = []
    rt_seen: set[str] = set()
    canonical_ids: set[int] = set()

    def _rt_id(proc: object) -> str:
        cls = type(proc)
        target = getattr(proc, "__hx_target__", "") or f"{cls.__module__}.{cls.__qualname__}"
        base_id = f"rt:{_compute_slug(target)}"
        node_id = base_id
        suffix = 0
        while node_id in rt_seen:
            suffix += 1
            node_id = f"{base_id}__rt{suffix}"
        rt_seen.add(node_id)
        return node_id

    for reg in (coerce_runtime_reg(p) for p in config._rt_procs):
        if reg is None:
            continue  # defensive: dict should not appear in _rt_procs
        canonical_ids.add(id(reg.proc))
        runtime.append((reg.proc, _rt_id(reg.proc)))

    for plugin in config.plugins or []:
        if isinstance(plugin, dict):
            continue  # dict plugin (YAML): not enumerable on pure read (L4.5)
        for proc in getattr(plugin, "processors", []) or []:
            if id(proc) in canonical_ids:
                continue  # same instance already in _rt_procs
            reg = coerce_runtime_reg(proc)
            if reg is None:
                continue
            runtime.append((reg.proc, _rt_id(reg.proc)))

    return ProcNodeIds(persistent=persistent, runtime=runtime)


def _extract_declaration(proc_dict: dict, target: str) -> "ComponentDecl":
    """dict metadata → ComponentDecl (L3.1). Fallback chain: dict → WKD → infer.

    Every field resolves independently via key presence (P2).  All fields are
    accumulated first and the ComponentDecl is constructed ONCE at the end, so
    ``__post_init__`` (hook↔hooks sync + lifecycle sort) fires correctly —
    never construct-then-mutate.
    """
    from .declaration import ComponentDecl, DeclarationSource, WELL_KNOWN_DECLARATIONS

    hooks: tuple = ()
    order = 50  # sentinel: "unknown" (L3.3)
    singleton_group = ""
    after: tuple = ()
    writes_to: tuple = ()
    reads_from: tuple = ()
    reads_event_fields: tuple = ()
    writes_event_fields: tuple = ()
    source = DeclarationSource.UNKNOWN
    confidence = 0.0

    # ── step 1: accumulate from the dict ──
    hooks_present = "_hooks_" in proc_dict or "_hook_" in proc_dict

    hooks_raw: tuple = ()
    if "_hooks_" in proc_dict:
        v = proc_dict["_hooks_"]
        hooks_raw = tuple(v) if isinstance(v, (list, tuple)) else ()
    elif "_hook_" in proc_dict:
        hooks_raw = (proc_dict["_hook_"],)
    # drop empty strings / non-str: legacy `_hook_=""` must yield hooks=()
    # (hooks_present stays True → WKD fallback and inference both forbidden →
    # 0 ATTACHED_TO edges).  "*" is truthy and survives (L4.1 expands it).
    hooks = tuple(h for h in hooks_raw if isinstance(h, str) and h)

    if "_order_" in proc_dict:
        try:
            order = int(proc_dict["_order_"])
        except (TypeError, ValueError):
            pass  # malformed hand-written YAML → stays "unknown", no crash

    if "_singleton_group_" in proc_dict:
        v = proc_dict["_singleton_group_"]
        # non-str (incl. None) → "": str(None) would mint a phantom "None"
        # singleton group (two such processors would CONFLICT with each other)
        singleton_group = v if isinstance(v, str) else ""

    if "_after_" in proc_dict:
        v = proc_dict["_after_"]
        # isinstance guard (same as SerializedReg.after): bare truthiness would
        # TypeError on `_after_: 7` and char-split on `_after_: "ab"`
        after = tuple(v) if isinstance(v, (list, tuple)) else ()

    if "_writes_slots_" in proc_dict:
        v = proc_dict["_writes_slots_"]
        writes_to = tuple(v) if isinstance(v, (list, tuple)) else ()
    if "_reads_slots_" in proc_dict:
        v = proc_dict["_reads_slots_"]
        reads_from = tuple(v) if isinstance(v, (list, tuple)) else ()
    if "_reads_event_fields_" in proc_dict:
        v = proc_dict["_reads_event_fields_"]
        reads_event_fields = tuple(v) if isinstance(v, (list, tuple)) else ()
    if "_writes_event_fields_" in proc_dict:
        v = proc_dict["_writes_event_fields_"]
        writes_event_fields = tuple(v) if isinstance(v, (list, tuple)) else ()

    # ── step 2: WKD fallback (only for keys entirely missing from the dict) ──
    wkd = WELL_KNOWN_DECLARATIONS.get(target)
    if wkd is not None:
        if not hooks_present and wkd.hooks:
            # key-presence, not truthiness: `_hooks_=[]` is explicit-empty
            hooks = wkd.hooks
        if "_order_" not in proc_dict:
            order = wkd.order
        if "_singleton_group_" not in proc_dict and wkd.singleton_group:
            singleton_group = wkd.singleton_group
        if "_after_" not in proc_dict and wkd.after:
            after = wkd.after
        if "_writes_slots_" not in proc_dict and wkd.writes_to:
            writes_to = wkd.writes_to
        if "_reads_slots_" not in proc_dict and wkd.reads_from:
            reads_from = wkd.reads_from
        if "_reads_event_fields_" not in proc_dict and wkd.reads_event_fields:
            reads_event_fields = wkd.reads_event_fields
        if "_writes_event_fields_" not in proc_dict and wkd.writes_event_fields:
            writes_event_fields = wkd.writes_event_fields
        if source == DeclarationSource.UNKNOWN:
            source = wkd.source
        if confidence < wkd.confidence:
            confidence = wkd.confidence

    # ── step 3: string inference (only when hooks are entirely undeclared) ──
    if not hooks_present and not hooks:
        inferred = _infer_hook_from_target(target)
        if inferred and inferred != "unknown":
            hooks = (inferred,)

    # ── step 4: construct once (__post_init__ syncs hook↔hooks + sorts) ──
    # Bucket read-back (VM5 "*" recognition): same chain as `_bucket`
    # (dict `_hook_` → WKD.hook).  Only "*" needs explicit passing (L3.2
    # exemption keeps it); a concrete bucket == hooks[0] derives naturally.
    # Explicit-empty coverage suppresses the read-back: passing hook="*" with
    # hooks=() would trip __post_init__ rule 1 and expand coverage to ("*",),
    # breaking VM14's "explicit `_hooks_=[]` → 0 edges".
    bucket = ""
    if "_hook_" in proc_dict:
        v = proc_dict["_hook_"]
        bucket = v if isinstance(v, str) else ""
    elif wkd is not None and wkd.hook:
        bucket = wkd.hook
    explicit_empty_coverage = hooks_present and not hooks
    return ComponentDecl(
        target=target,
        hook=("*" if bucket == "*" and not explicit_empty_coverage else ""),
        hooks=hooks,
        order=order,
        singleton_group=singleton_group,
        after=after,
        writes_to=writes_to,
        reads_from=reads_from,
        reads_event_fields=reads_event_fields,
        writes_event_fields=writes_event_fields,
        source=source,
        confidence=confidence,
    )


# Per-process dedup: (target, frozenset(reported keys)) already logged once.
# WKD↔class consistency is a static property, so re-reporting the same target
# with the same key set on every to_graph() call is pure noise — one line per
# distinct diagnosis for the life of the process.  Tests clear this to isolate.
_WKD_DRIFT_SEEN: set = set()


def _warn_wkd_drift(proc_dict: dict, target: str, wkd) -> None:
    """Diagnose dict metadata vs WELL_KNOWN_DECLARATIONS (约束 #6).

    Two distinct, separately-worded diagnoses — a serialized key can either
    *contradict* the WKD or be *absent* from the dict, and only the first is a
    real drift signal:

    - **value drift** (key present, value ≠ WKD) → WARNING.  Builder-serialized
      values are class ground truth at build time, so a live mismatch usually
      means the class changed and the WKD table was not updated; stale WKD
      silently diverges the L4.6/L5.6 chain order from the runtime execution
      order (I7).  Explicit registration overrides also land here.

    - **legacy absence** (key missing) → INFO.  Pre-v5.3 configs serialized only
      ``_target_`` / ``_hook_``; the v5.3 builder now writes the full metadata
      block (``builder.py`` L2.1, "key presence == declared").  A missing key is
      therefore an old on-disk shape, *not* a class↔WKD mismatch — that
      consistency is enforced separately (test_declaration_l3), so absence is the
      norm for historical corpora and must never spam WARNING.

    ``_hook_`` is never inspected — the builder writes it precisely on
    registration overrides, which are legitimate.  Both diagnoses dedup per
    process on ``(target, frozenset(keys))`` (their key sets are disjoint, so a
    warning never masks an info for the same target).
    """
    value_drift: list[str] = []
    legacy_missing: list[str] = []

    if "_hooks_" not in proc_dict:
        legacy_missing.append("_hooks_")
    elif isinstance(proc_dict["_hooks_"], (list, tuple)) \
            and tuple(proc_dict["_hooks_"]) != wkd.hooks:
        value_drift.append("_hooks_")

    if "_order_" not in proc_dict:
        legacy_missing.append("_order_")
    else:
        try:
            if int(proc_dict["_order_"]) != wkd.order:
                value_drift.append("_order_")
        except (TypeError, ValueError):
            pass

    if "_singleton_group_" not in proc_dict:
        legacy_missing.append("_singleton_group_")
    elif isinstance(proc_dict["_singleton_group_"], str) \
            and proc_dict["_singleton_group_"] != wkd.singleton_group:
        value_drift.append("_singleton_group_")

    if value_drift:
        key = (target, frozenset(value_drift))
        if key not in _WKD_DRIFT_SEEN:
            _WKD_DRIFT_SEEN.add(key)
            _log.warning(
                "WKD drift for %s: dict keys %s differ from WELL_KNOWN_DECLARATIONS "
                "(class metadata may have changed without a WKD update)",
                target, value_drift,
            )
    if legacy_missing:
        key = (target, frozenset(legacy_missing))
        if key not in _WKD_DRIFT_SEEN:
            _WKD_DRIFT_SEEN.add(key)
            _log.info(
                "WKD note for %s: dict keys %s absent - serialized dict predates "
                "v5.3 metadata (legacy config); class<->WKD consistency is enforced "
                "separately",
                target, legacy_missing,
            )


# ── runtime overlay (L5.1 / L5.1b / L5.3) ────────────────────────────────────


def _add_runtime_overlay(
    snapshot: GraphSnapshot, config: "HarnessConfig",
) -> "dict[int, str]":
    """Add runtime-only processor nodes + plugin processors + runtime slots.

    Runtime nodes live in ``snapshot.runtime_nodes`` (never ``nodes``) so the
    genotype hash stays isolated from the runtime overlay (I6).  ATTACHED_TO
    edges point at the main-graph hook skeleton (cross runtime_nodes → nodes).

    Returns ``{id(proc): node_id}`` — the L5.6 EXECUTES_BEFORE chain uses this
    to reference the SAME node ids the overlay allocated.
    """
    from ..core.processor import MultiHookProcessor
    from ..core.processor import get_graph_metadata

    # rt: node ids come from the single authority (assign_processor_node_ids) —
    # keyed by proc identity, in the same canonical-then-plugin order this
    # overlay walks below.  No id is formed here.
    rt_ids = {id(proc): nid for proc, nid in assign_processor_node_ids(config).runtime}
    canonical_ids: set[int] = set()
    proc_id_to_node: "dict[int, str]" = {}

    def _add_runtime_node(reg: RuntimeReg) -> None:
        proc = reg.proc
        meta = get_graph_metadata(proc)  # class-level metadata (slots, events, dispatch)
        cls = type(proc)
        target = getattr(proc, "__hx_target__", "") or f"{cls.__module__}.{cls.__qualname__}"

        # RuntimeReg registration values override class defaults (no instance mutation)
        meta["_hook_"] = reg.hook
        meta["_order_"] = reg.order
        meta["_singleton_group_"] = reg.singleton_group or ""
        meta["_after_"] = list(reg.after) if reg.after else []
        # _hooks_ four-state coverage (L2.2 / runloop "*" bucket closure):
        if reg.hook == "":
            meta["_hooks_"] = []                      # empty bucket: never executes
        elif reg.hook and reg.hook != "*":
            meta["_hooks_"] = [reg.hook]              # concrete hook → single
        elif reg.hook == "*" and not isinstance(proc, MultiHookProcessor):
            meta["_hooks_"] = list(PROCESSOR_HOOK_NAMES)  # bare "*" → all 8
        # else: keep dispatch-derived _hooks_ (MHP + "*")

        node_id = rt_ids[id(reg.proc)]

        extra = dict(meta)
        extra["_runtime_only"] = True
        extra["_target_"] = target
        node = Node(
            node_id=node_id, node_type=NodeType.PROCESSOR,
            label=target.rsplit(".", 1)[-1], metadata=extra,
        )
        snapshot.runtime_nodes[node_id] = node
        proc_id_to_node[id(reg.proc)] = node_id

        for hook_name in meta["_hooks_"]:
            if hook_name in SKELETON_HOOK_NAMES:
                snapshot.runtime_edges.append(Edge(
                    source_id=node_id, target_id=f"hook:{hook_name}",
                    edge_type=EdgeType.ATTACHED_TO,
                    metadata={"provenance": "runtime_only"},
                ))

    # L5.1: canonical RuntimeRegs
    for reg in (coerce_runtime_reg(p) for p in config._rt_procs):
        if reg is None:
            continue  # defensive: dict should not appear in _rt_procs
        canonical_ids.add(id(reg.proc))
        _add_runtime_node(reg)

    # L5.1b: instance plugins' processors (id-dedup vs canonical)
    for plugin in config.plugins or []:
        if isinstance(plugin, dict):
            # dict plugin (YAML): pure read can't enumerate its processors (L4.5)
            _log.warning(
                "dict 插件不进 deployment 表示（纯读取不可枚举）— %s",
                str(plugin)[:80],
            )
            continue
        for proc in getattr(plugin, "processors", []) or []:
            if id(proc) in canonical_ids:
                continue  # same instance also in _rt_procs (harness.py:700-706 semantics)
            reg = coerce_runtime_reg(proc)
            if reg is None:
                continue
            _add_runtime_node(reg)

    # L5.3: runtime-only slot nodes + WRITES_TO / READS_FROM edges
    _add_runtime_slots(snapshot)
    return proc_id_to_node


def _add_runtime_slots(snapshot: GraphSnapshot) -> None:
    """Runtime slot nodes (never in snapshot.nodes) + read/write edges.

    If a same-named persistent slot already exists, the edge points at the
    main-graph node; otherwise a ``rt:slot:{key}`` node is created in
    runtime_nodes (genotype stays isolated).
    """
    rt_slot_keys: set[str] = set()
    for node_id, node in snapshot.runtime_nodes.items():
        if node.node_type != NodeType.PROCESSOR:
            continue
        for slot_name in node.metadata.get("_writes_slots_", []):
            rt_slot_keys.add(slot_name)
        for slot_name in node.metadata.get("_reads_slots_", []):
            rt_slot_keys.add(slot_name)

    for key in sorted(rt_slot_keys):
        persistent_id = f"slot:{key}"
        rt_slot_id = f"rt:slot:{key}"
        if persistent_id not in snapshot.nodes and rt_slot_id not in snapshot.runtime_nodes:
            snapshot.runtime_nodes[rt_slot_id] = Node(
                node_id=rt_slot_id, node_type=NodeType.SLOT,
                label=key,
                metadata={"slot_name": key, "slot_type": "dynamic", "_runtime_only": True},
            )

    for node_id, node in snapshot.runtime_nodes.items():
        if node.node_type != NodeType.PROCESSOR:
            continue
        for slot_name in node.metadata.get("_writes_slots_", []):
            target = f"slot:{slot_name}" if f"slot:{slot_name}" in snapshot.nodes else f"rt:slot:{slot_name}"
            snapshot.runtime_edges.append(Edge(
                source_id=node_id, target_id=target,
                edge_type=EdgeType.WRITES_TO,
                metadata={"provenance": "runtime_only", "data_channel": "state"},
            ))
        for slot_name in node.metadata.get("_reads_slots_", []):
            target = f"slot:{slot_name}" if f"slot:{slot_name}" in snapshot.nodes else f"rt:slot:{slot_name}"
            snapshot.runtime_edges.append(Edge(
                source_id=node_id, target_id=target,
                edge_type=EdgeType.READS_FROM,
                metadata={"provenance": "runtime_only", "data_channel": "state"},
            ))


# ── main export ─────────────────────────────────────────────────────────────


def to_graph(config: "HarnessConfig", *, source_hash: str = "") -> GraphSnapshot:
    """Export a HarnessConfig as a typed graph snapshot.

    Walks ``config.processors`` (list of ``_target_`` dicts) and produces
    nodes + typed edges.  No behavioural change — pure read.

    Args:
        config: A built HarnessConfig.
        source_hash: Optional hash of the source YAML / builder state.

    Returns:
        A GraphSnapshot with all nodes and declared edges populated.
        ``genotype_hash`` is left empty — call :func:`harnessx.graph.identity.genotype_hash`
        to compute it.
    """
    snapshot = GraphSnapshot(source_config_hash=source_hash or "")

    # 1. skeleton hooks
    snapshot.nodes.update(_make_skeleton_nodes())

    # 2. loop-back
    snapshot.edges.append(_make_loop_back_edge())

    # 3. processor nodes + ATTACHED_TO edges (decl-driven — L3.1 / L4.1)
    from .declaration import WELL_KNOWN_DECLARATIONS

    # Persistent proc: node ids come from the single authority — no second
    # per-target walk here (L4.0).  Keyed by dict identity; both this loop and
    # the authority skip the same non-dict / empty-target entries, so every
    # proc_dict that reaches node creation has an entry.
    persistent_ids = {id(d): nid for d, nid in assign_processor_node_ids(config).persistent}
    proc_node_ids: list[str] = []

    for proc_dict in config.processors:
        # proc_dict may be a _target_ dict or a runtime-only instance —
        # instances are represented by the runtime overlay, not here.
        if not isinstance(proc_dict, dict):
            continue

        target = proc_dict.get("_target_", "")
        if not target:
            continue

        wkd = WELL_KNOWN_DECLARATIONS.get(target)
        decl = _extract_declaration(proc_dict, target)
        if wkd is not None:
            _warn_wkd_drift(proc_dict, target, wkd)

        # Node metadata carries RESOLVED decl values, but a key is written only
        # when the extraction actually resolved it (dict key / WKD / inference).
        # The 50/""/() "unknown" sentinels are never pinned into the graph, so
        # a round-trip cannot turn "undeclared" into "explicitly declared" (P2)
        # — pinning `_order_=50` or `_singleton_group_=""` would replace the
        # runtime's natural class-default fallback with a wrong explicit value.
        meta: dict[str, object] = {"_target_": target}
        if "_code_hash" in proc_dict:
            meta["_code_hash"] = proc_dict["_code_hash"]

        hooks_present = "_hooks_" in proc_dict or "_hook_" in proc_dict
        if "_hook_" in proc_dict:
            v = proc_dict["_hook_"]
            meta["_hook_"] = v if isinstance(v, str) else ""  # bucket: dict wins
        elif wkd is not None and wkd.hook:
            meta["_hook_"] = wkd.hook                          # natural bucket
        elif not hooks_present and decl.hooks:
            meta["_hook_"] = decl.hooks[0]                     # inference hit
        # `_hooks_`-only dicts get no `_hook_`: fabricating a bucket from
        # coverage[0] would reroute the processor on round-trip (natural "*"
        # bucket → concrete hook).
        if hooks_present or wkd is not None or decl.hooks:
            meta["_hooks_"] = list(decl.hooks)
        if "_order_" in proc_dict or wkd is not None:
            meta["_order_"] = decl.order
        if "_singleton_group_" in proc_dict or (wkd is not None and wkd.singleton_group):
            meta["_singleton_group_"] = decl.singleton_group
        if "_after_" in proc_dict or (wkd is not None and wkd.after):
            meta["_after_"] = list(decl.after)
        if "_writes_slots_" in proc_dict or (wkd is not None and wkd.writes_to):
            meta["_writes_slots_"] = list(decl.writes_to)
        if "_reads_slots_" in proc_dict or (wkd is not None and wkd.reads_from):
            meta["_reads_slots_"] = list(decl.reads_from)
        if "_reads_event_fields_" in proc_dict or (wkd is not None and wkd.reads_event_fields):
            meta["_reads_event_fields_"] = list(decl.reads_event_fields)
        if "_writes_event_fields_" in proc_dict or (wkd is not None and wkd.writes_event_fields):
            meta["_writes_event_fields_"] = list(decl.writes_event_fields)

        # L4.4: constructor kwargs (non-underscore keys), deepcopied so later
        # mutation of the source dict never leaks into the graph (VM12g)
        ctor_kwargs = deepcopy({k: v for k, v in proc_dict.items()
                                if not k.startswith("_")})
        if ctor_kwargs:
            meta["_ctor_kwargs_"] = ctor_kwargs

        node_id = persistent_ids[id(proc_dict)]

        snapshot.nodes[node_id] = Node(
            node_id=node_id,
            node_type=NodeType.PROCESSOR,
            label=target.rsplit(".", 1)[-1] if "." in target else target,
            metadata=meta,
        )
        proc_node_ids.append(node_id)

        # L4.1: ATTACHED_TO edges from decl.hooks. "*" expands to the 8
        # processor hooks (PROCESSOR_HOOK_NAMES) — never model/tool.
        for hook_name in decl.hooks:
            if hook_name == "*":
                target_hooks = list(PROCESSOR_HOOK_NAMES)
            elif hook_name in SKELETON_HOOK_NAMES:
                target_hooks = [hook_name]
            else:
                target_hooks = []
            for t in target_hooks:
                hook_node_id = f"hook:{t}"
                if hook_node_id in snapshot.nodes:
                    snapshot.edges.append(Edge(
                        source_id=node_id,
                        target_id=hook_node_id,
                        edge_type=EdgeType.ATTACHED_TO,
                        metadata={},
                    ))

    # 4. AFTER edges (soft ordering dependencies within same hook)
    # Build group → node_id mapping from declared singleton_groups
    group_to_node: dict[str, str] = {}
    for node_id in proc_node_ids:
        node = snapshot.nodes[node_id]
        sg = node.metadata.get("_singleton_group_")
        if sg:
            group_to_node[sg] = node_id

    for node_id in proc_node_ids:
        node = snapshot.nodes[node_id]
        after_groups_raw = node.metadata.get("_after_")
        if after_groups_raw is None:
            continue
        after_groups = _normalize_after(after_groups_raw)
        for after_group in after_groups:
            if after_group in group_to_node:
                target_id = group_to_node[after_group]
                snapshot.edges.append(Edge(
                    source_id=node_id,
                    target_id=target_id,
                    edge_type=EdgeType.AFTER,
                    metadata={"via_singleton_group": after_group},
                ))

    # 5. CONFLICTS_WITH edges (singleton_group mutual exclusion)
    # Two processors sharing the same singleton_group → conflict
    sg_to_nodes: dict[str, list[str]] = {}
    for node_id in proc_node_ids:
        sg = snapshot.nodes[node_id].metadata.get("_singleton_group_")
        if sg:
            sg_to_nodes.setdefault(sg, []).append(node_id)
    for sg, nids in sg_to_nodes.items():
        if len(nids) < 2:
            continue
        # Every pair in the same group conflicts
        for i in range(len(nids)):
            for j in range(i + 1, len(nids)):
                snapshot.edges.append(Edge(
                    source_id=nids[i],
                    target_id=nids[j],
                    edge_type=EdgeType.CONFLICTS_WITH,
                    metadata={"singleton_group": sg},
                ))

    # 6. Slot nodes + read/write edges
    _add_slot_nodes(snapshot)

    # 7. WRITES_TO / READS_FROM edges from declaration metadata.
    # Slot edges are ADFG "state"-channel data flow by construction (Δ7).
    for node_id in proc_node_ids:
        node = snapshot.nodes[node_id]
        for slot_name in node.metadata.get("_writes_slots_", []):
            slot_node_id = f"slot:{slot_name}"
            if slot_node_id in snapshot.nodes:
                snapshot.edges.append(Edge(
                    source_id=node_id,
                    target_id=slot_node_id,
                    edge_type=EdgeType.WRITES_TO,
                    metadata={"provenance": "declared", "data_channel": "state"},
                ))
        for slot_name in node.metadata.get("_reads_slots_", []):
            slot_node_id = f"slot:{slot_name}"
            if slot_node_id in snapshot.nodes:
                snapshot.edges.append(Edge(
                    source_id=node_id,
                    target_id=slot_node_id,
                    edge_type=EdgeType.READS_FROM,
                    metadata={"provenance": "declared", "data_channel": "state"},
                ))

    # ── Runtime overlay (L5.1 / L5.1b / L5.3) ──────────────────────────────
    proc_id_to_node = _add_runtime_overlay(snapshot, config)

    # ── EXECUTES_BEFORE chains (L4.6 persistent → main edges; L5.6 mixed → runtime) ──
    _add_executes_before_edges(snapshot, config, proc_id_to_node)

    return snapshot


# ── helpers ─────────────────────────────────────────────────────────────────


def _normalize_after(raw: object) -> list[str]:
    """Normalize ``_after_`` value to a list of singleton_group strings."""
    if raw is None:
        return []
    if isinstance(raw, (list, tuple)):
        return [str(x) for x in raw if x is not None]
    if isinstance(raw, str):
        return [raw]
    return []


def _infer_hook_from_target(target: str) -> str:
    """Try to infer a hook from well-known processor class names.

    This is a best-effort fallback when ``_hook_`` is not in the dict.
    """
    # Map known singleton_groups / class patterns to hooks
    known: dict[str, str] = {
        "SlidingWindowMemory": "step_start",
        "SystemPromptBuilder": "task_start",
        "CostGuardProcessor": "before_model",
        "LoopDetectionProcessor": "step_end",
        "EvaluationProcessor": "task_end",
        "SandboxPolicy": "before_tool",
        "TelemetryLogger": "step_end",
        "DiffCollector": "step_end",
        "NullSystemPromptBuilder": "task_start",
    }
    for key, hook in known.items():
        if key in target:
            return hook
    return ""


def _add_slot_nodes(snapshot: GraphSnapshot) -> None:
    """Add slot nodes: 6 fixed baseline channels + dynamic declared slots (L4.3).

    Baseline slots (memory, plan, cost, tool_registry, workspace, sandbox) are
    always created.  On top, every slot key declared by a processor node's
    ``_writes_slots_`` / ``_reads_slots_`` gets a ``slot:{key}`` node so the
    WRITES_TO / READS_FROM edges have endpoints (e.g. ``slot:model.route``).
    """
    slot_names = [
        ("slot:memory", "memory", "SharedMemory"),
        ("slot:plan", "plan", "PlanSlot"),
        ("slot:cost", "cost", "CostSlot"),
        ("slot:tool_registry", "tool_registry", "ToolRegistry"),
        ("slot:workspace", "workspace", "Workspace"),
        ("slot:sandbox", "sandbox", "SandboxProvider"),
    ]
    for node_id, name, stype in slot_names:
        snapshot.nodes[node_id] = Node(
            node_id=node_id,
            node_type=NodeType.SLOT,
            label=name,
            metadata={"slot_name": name, "slot_type": stype},
        )

    # L4.3: dynamic slots collected from processor declarations
    dyn_keys: set[str] = set()
    for node in snapshot.nodes.values():
        if node.node_type != NodeType.PROCESSOR:
            continue
        for key in node.metadata.get("_writes_slots_", []):
            dyn_keys.add(key)
        for key in node.metadata.get("_reads_slots_", []):
            dyn_keys.add(key)
    for key in sorted(dyn_keys):  # sorted for determinism
        slot_id = f"slot:{key}"
        if slot_id not in snapshot.nodes:
            snapshot.nodes[slot_id] = Node(
                node_id=slot_id,
                node_type=NodeType.SLOT,
                label=key,
                metadata={"slot_name": key, "slot_type": "dynamic"},
            )


# ── EXECUTES_BEFORE bucket/order resolution (L4.6) ──────────────────────────


def _bucket(proc_dict: dict, target: str) -> str:
    """Resolve a serialized processor's registration bucket (hook).

    Fallback chain (L4.6): dict key ``_hook_`` → WKD.hook (the class's natural
    bucket) → string inference.  Never uses the declaration's ``hooks[0]``
    (coverage first element) — for multi-hook / no-``_hook`` classes the
    natural bucket differs from coverage, and using it would make the
    EXECUTES_BEFORE chain diverge from the runtime execution order (I7).
    """
    if "_hook_" in proc_dict and isinstance(proc_dict["_hook_"], str):
        return proc_dict["_hook_"]
    from .declaration import WELL_KNOWN_DECLARATIONS

    decl = WELL_KNOWN_DECLARATIONS.get(target)
    if decl is not None and decl.hook:
        return decl.hook
    inferred = _infer_hook_from_target(target)
    return inferred if inferred else "*"


def _order_parse(proc_dict: dict, target: str) -> int:
    """Resolve a serialized processor's order for EXECUTES_BEFORE sorting.

    Fallback chain (L4.6): dict key ``_order_`` → WKD.order → 0 (class default).

    Does NOT use the ComponentDecl 50 sentinel — the declaration fallback is 50
    while the runtime natural fallback is the class default 0; sorting with 50
    in a bucket mixing WKD and non-WKD classes would reverse the chain vs the
    runtime order (breaks I7).
    """
    if "_order_" in proc_dict:
        try:
            return int(proc_dict["_order_"])
        except (TypeError, ValueError):
            pass
    from .declaration import WELL_KNOWN_DECLARATIONS

    decl = WELL_KNOWN_DECLARATIONS.get(target)
    if decl is not None and decl.order != 50:
        return decl.order
    return 0


def _add_executes_before_edges(
    snapshot: GraphSnapshot, config: "HarnessConfig", proc_id_to_node: dict,
) -> None:
    """EXECUTES_BEFORE chains (L4.6 persistent → main edges; L5.6 mixed → runtime).

    L4.6: within each bucket, persistent (SerializedReg) processors chain by
    (order, after topo, persistent-seq) — edges go to ``snapshot.edges``
    (genotype).  L5.6: the FULL mixed effective order (canonical + RuntimeReg +
    instance plugins, seq=max+1) chains into ``runtime_edges`` (deployment).
    Both use the SAME ``stable_topological_sort`` as the runtime router, so the
    graph chain ≡ the runtime execution order (I7).
    """
    from ..core.runtime import RoutingEnvelope, stable_topological_sort

    def _chain(envs: list, target_edges: list, bucket: str, id_map: dict) -> None:
        if not envs:
            return
        ordered = stable_topological_sort(
            envs,
            order_key=lambda e: e.reg.order,
            after_key=lambda e: e.reg.after,
            group_key=lambda e: e.reg.singleton_group or "",
            seq_key=lambda e: e.seq,
        )
        ids = [id_map[e] for e in ordered]
        for a, b in zip(ids, ids[1:]):
            target_edges.append(Edge(
                source_id=a, target_id=b,
                edge_type=EdgeType.EXECUTES_BEFORE,
                metadata={"hook": bucket},
            ))

    # ── L4.6: persistent-relative chain (main edges, genotype) ──────────────
    # proc: node ids come from the single authority (assign_processor_node_ids)
    # — this walk no longer re-derives them (that duplicate scheme is gone).
    # The per-target `index` is still computed here, but only as the L4.6
    # persistent-seq (chain tiebreak); it is not the node id.
    persistent_ids = {id(d): nid for d, nid in assign_processor_node_ids(config).persistent}
    seen_targets: dict[str, int] = {}
    persistent: list[RoutingEnvelope] = []
    persistent_id: dict = {}
    for proc_dict in config.processors or []:
        if not isinstance(proc_dict, dict):
            continue
        target = proc_dict.get("_target_", "")
        if not target:
            continue
        bucket = _bucket(proc_dict, target)
        if bucket == "":
            continue  # explicit empty bucket: no chain (never executes)
        seen_targets[target] = seen_targets.get(target, 0) + 1
        index = seen_targets[target]  # L4.6 persistent-seq (tiebreak), not the id
        node_id = persistent_ids[id(proc_dict)]
        sg = proc_dict.get("_singleton_group_")
        if not sg:
            from .declaration import WELL_KNOWN_DECLARATIONS
            decl = WELL_KNOWN_DECLARATIONS.get(target)
            sg = decl.singleton_group if decl else ""
        env = RoutingEnvelope(RuntimeReg(
            proc=None,  # pure read; L4.5 no instantiation
            hook=bucket,
            order=_order_parse(proc_dict, target),
            singleton_group=sg or None,
            after=tuple(proc_dict.get("_after_") or ()),
        ), index)  # persistent-seq = per-target index (L4.6)
        persistent.append(env)
        persistent_id[env] = node_id

    for bucket in (*PROCESSOR_HOOK_NAMES, "*"):
        _chain([e for e in persistent if e.reg.hook == bucket],
               snapshot.edges, bucket, persistent_id)

    # ── L5.6: full mixed effective order (runtime_edges, deployment) ────────
    mixed: list[RoutingEnvelope] = []
    mixed_id: dict = {}
    for seq, r in enumerate(config._processor_regs):
        if isinstance(r, SerializedReg):
            target = str(r.dict_ref.get("_target_", ""))
            bucket = _bucket(r.dict_ref, target)
            if bucket == "":
                continue
            # node id: reuse the persistent mapping (same SerializedRegs, same
            # order — config.processors is the SerializedReg dict_ref view).
            sg = r.dict_ref.get("_singleton_group_")
            if not sg:
                from .declaration import WELL_KNOWN_DECLARATIONS
                decl = WELL_KNOWN_DECLARATIONS.get(target)
                sg = decl.singleton_group if decl else ""
            env = RoutingEnvelope(RuntimeReg(
                proc=None,
                hook=bucket,
                order=_order_parse(r.dict_ref, target),
                singleton_group=sg or None,
                after=tuple(r.dict_ref.get("_after_") or ()),
            ), seq)
            # find the matching persistent env's node id by (hook, order, sg)
            match = next((e for e in persistent
                          if e.reg.hook == bucket
                          and e.reg.order == env.reg.order
                          and (e.reg.singleton_group or "") == (env.reg.singleton_group or "")),
                         None)
            if match is None:
                continue  # defensive: no matching persistent node
            mixed.append(env)
            mixed_id[env] = persistent_id[match]
        else:
            # RuntimeReg (canonical) — node id from the overlay's proc_id map
            nid = proc_id_to_node.get(id(r.proc))
            if nid is None:
                continue  # defensive: node not created (empty bucket etc.)
            mixed.append(RoutingEnvelope(r, seq))
            mixed_id[RoutingEnvelope(r, seq)] = nid

    # instance plugins (seq = max+1; id-dedup vs canonical)
    base_seq = max((e.seq for e in mixed), default=-1) + 1
    canonical_proc_ids = {id(r.proc) for r in config._rt_procs}
    for plugin in config.plugins or []:
        if isinstance(plugin, dict):
            continue
        for proc in getattr(plugin, "processors", []) or []:
            if id(proc) in canonical_proc_ids:
                continue
            reg = coerce_runtime_reg(proc)
            if reg is None:
                continue
            nid = proc_id_to_node.get(id(reg.proc))
            if nid is None:
                continue  # defensive: plugin node not created
            env = RoutingEnvelope(reg, base_seq)
            mixed.append(env)
            mixed_id[env] = nid
            base_seq += 1

    for bucket in (*PROCESSOR_HOOK_NAMES, "*"):
        _chain([e for e in mixed if e.reg.hook == bucket],
               snapshot.runtime_edges, bucket, mixed_id)
