"""Graph export — convert a HarnessConfig into a typed GraphSnapshot.

``to_graph(config)`` is the primary entry point.  It walks the config's
flat processor list and produces nodes + typed edges without changing any
existing behaviour (pure read).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .types import (
    SKELETON_HOOK_NAMES,
    Edge,
    EdgeType,
    GraphSnapshot,
    Node,
    NodeType,
)

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


def _slug_from_target(target: str, index: int) -> str:
    """Derive a stable, human-readable node id from a _target_ class path."""
    # e.g. "harnessx.processors.memory.strategies.sliding_window.SlidingWindowMemory"
    # → "proc:sliding_window_memory"
    short = target.rsplit(".", 1)[-1] if "." in target else target
    # CamelCase → snake_case
    slug = ""
    for i, ch in enumerate(short):
        if ch.isupper() and i > 0 and (short[i - 1].islower() or (i + 1 < len(short) and short[i + 1].islower())):
            slug += "_"
        slug += ch.lower()
    return f"proc:{slug}"


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
            if decl.hooks and decl.hooks != ("*",) and "_hook_" not in extra:
                extra["_hook_"] = decl.hooks[0] if len(decl.hooks) == 1 else "*"
            # Slot deps + event-field reads for edge creation later
            if decl.writes_to:
                extra["_writes_slots_"] = list(decl.writes_to)
            if decl.reads_from:
                extra["_reads_slots_"] = list(decl.reads_from)
            if decl.reads_event_fields:
                extra["_reads_event_fields_"] = list(decl.reads_event_fields)

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
