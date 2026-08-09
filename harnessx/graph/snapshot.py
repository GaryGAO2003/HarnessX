"""Graph export — convert a HarnessConfig into a typed GraphSnapshot.

``to_graph(config)`` is the primary entry point.  It walks the config's
flat processor list and produces nodes + typed edges without changing any
existing behaviour (pure read).
"""

from __future__ import annotations

import logging
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


def _slug_from_target(target: str, index: int) -> str:
    """Derive a stable, human-readable node id from a _target_ class path."""
    # e.g. "harnessx.processors.memory.strategies.sliding_window.SlidingWindowMemory"
    # → "proc:sliding_window_memory"
    return f"proc:{_compute_slug(target)}"


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

    node_id_seen: set[str] = set(snapshot.nodes) | set(snapshot.runtime_nodes)
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

        node_id = f"rt:{_compute_slug(target)}"
        suffix = 0
        base_id = node_id
        while node_id in node_id_seen:
            suffix += 1
            node_id = f"{base_id}__rt{suffix}"
        node_id_seen.add(node_id)

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
                metadata={"provenance": "runtime_only"},
            ))
        for slot_name in node.metadata.get("_reads_slots_", []):
            target = f"slot:{slot_name}" if f"slot:{slot_name}" in snapshot.nodes else f"rt:slot:{slot_name}"
            snapshot.runtime_edges.append(Edge(
                source_id=node_id, target_id=target,
                edge_type=EdgeType.READS_FROM,
                metadata={"provenance": "runtime_only"},
            ))


def _make_processor_node(
    target: str,
    hook: str,
    index: int,
    extra: dict | None = None,
) -> Node:
    """Create a processor node from its _target_ dict."""
    node_id = _slug_from_target(target, index)
    metadata: dict = {
        "_target_": target,
        "_hook_": hook,
    }
    if extra:
        metadata.update(extra)
    return Node(
        node_id=node_id,
        node_type=NodeType.PROCESSOR,
        label=target.rsplit(".", 1)[-1] if "." in target else target,
        metadata=metadata,
    )


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

    # 3. processor nodes + ATTACHED_TO edges
    seen_targets: dict[str, int] = {}  # target → count for disambiguation
    proc_node_ids: list[str] = []

    for i, proc_dict in enumerate(config.processors):
        # proc_dict may be a _target_ dict or a runtime-only instance (MultiHookProcessor etc.)
        # Runtime-only instances are not dicts — skip them for graph export.
        if not isinstance(proc_dict, dict):
            continue

        target = proc_dict.get("_target_", "")
        if not target:
            continue

        # Determine hook
        hook = proc_dict.get("_hook_", "")
        if not hook:
            # Try to infer from the target class's _hook attribute
            hook = _infer_hook_from_target(target)

        if not hook:
            hook = "unknown"

        extra: dict[str, object] = {}
        for key in ("_order_", "_singleton_group_", "_code_hash"):
            if key in proc_dict:
                extra[key] = proc_dict[key]
        for key in ("_after_",):
            val = proc_dict.get(key)
            if val is not None:
                extra[key] = val

        # Inject declaration metadata from well-known processor classes
        from .declaration import WELL_KNOWN_DECLARATIONS
        decl = WELL_KNOWN_DECLARATIONS.get(target)
        if decl is not None:
            if decl.singleton_group and "_singleton_group_" not in extra:
                extra["_singleton_group_"] = decl.singleton_group
            if decl.order and "_order_" not in extra:
                extra["_order_"] = decl.order
            if decl.after and "_after_" not in extra:
                extra["_after_"] = decl.after
            # Store slot deps for edge creation later
            if decl.writes_to:
                extra["_writes_slots_"] = list(decl.writes_to)
            if decl.reads_from:
                extra["_reads_slots_"] = list(decl.reads_from)

        # Disambiguate duplicate targets (same class used multiple times)
        seen_targets[target] = seen_targets.get(target, 0) + 1
        index = seen_targets[target]
        node = _make_processor_node(target, hook, index, extra if extra else None)
        node_id = node.node_id
        if index > 1:
            node_id = f"{node_id}__{index}"

        # Replace with disambiguated id if needed
        if node_id != node.node_id:
            node = Node(
                node_id=node_id,
                node_type=node.node_type,
                label=node.label,
                metadata=dict(node.metadata),
            )

        snapshot.nodes[node_id] = node
        proc_node_ids.append(node_id)

        # ATTACHED_TO edge: processor → its hook(s)
        # "*" means MultiHookProcessor — fires on every hook
        if hook == "*":
            target_hooks = SKELETON_HOOK_NAMES
        else:
            target_hooks = [hook] if hook in SKELETON_HOOK_NAMES else []
        for hook_name in target_hooks:
            hook_node_id = f"hook:{hook_name}"
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

    # 7. WRITES_TO / READS_FROM edges from declaration metadata
    for node_id in proc_node_ids:
        node = snapshot.nodes[node_id]
        for slot_name in node.metadata.get("_writes_slots_", []):
            slot_node_id = f"slot:{slot_name}"
            if slot_node_id in snapshot.nodes:
                snapshot.edges.append(Edge(
                    source_id=node_id,
                    target_id=slot_node_id,
                    edge_type=EdgeType.WRITES_TO,
                    metadata={"provenance": "declared"},
                ))
        for slot_name in node.metadata.get("_reads_slots_", []):
            slot_node_id = f"slot:{slot_name}"
            if slot_node_id in snapshot.nodes:
                snapshot.edges.append(Edge(
                    source_id=node_id,
                    target_id=slot_node_id,
                    edge_type=EdgeType.READS_FROM,
                    metadata={"provenance": "declared"},
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
    """Add slot nodes for known data channels and any WRITES_TO/READS_FROM edges.

    Slot nodes represent typed data channels that processors read from or
    write to.  The three canonical slots are memory, plan, and cost.
    Additional slots (tool_registry, workspace, sandbox) are added as
    infrastructure slots.
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
    # config.processors is the SerializedReg dict_ref view (same order as the
    # canonical SerializedRegs), so walking it reproduces the main loop's
    # per-target index and thus the proc: node ids.
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
        index = seen_targets[target]
        node_id = f"proc:{_compute_slug(target)}"
        if index > 1:
            node_id = f"{node_id}__{index}"
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
