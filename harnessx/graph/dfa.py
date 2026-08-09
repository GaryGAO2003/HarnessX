"""Δ8 — lifecycle DFA structural pass (typed graph × hook automaton).

The 8-hook processor lifecycle is a deterministic automaton; this pass runs
the product of a candidate snapshot against it and reports WITNESSES for
"structurally legal but protocol-violating" configurations — the class of
defect the S0–S4 validator cannot see (its checks are per-layer static; this
one reasons about firing order and bucket enforcement).

Hand-written lifecycle transition table (the one-off "hook 时序规约"):

    task_start → step_start → before_model → [model] → after_model
      → (before_tool → [tool] → after_tool)* → step_end
      → step_start (next step) | task_end (accept)

Six checks, each producing witnesses on failure:

    1. alphabet_violation    — coverage / attachment outside the 8 DFA states
    2. dead_processor        — no firing state at all (never executes)
    3. after_never_enforced  — ``_after_`` resolves to a DIFFERENT registration
                               bucket: the runtime sorts per bucket, so the
                               constraint silently never applies
    4. temporal_contradiction— the ``after`` intent is impossible within a
                               task pass (task_start-only declarer, or
                               task_end-only target) — stronger than #3
    5. skeleton_broken       — missing skeleton hook nodes / broken LOOP_BACK
    6. chain_state_mismatch  — an EXECUTES_BEFORE edge whose bucket disagrees
                               with an endpoint's registration bucket
                               (stale or hand-corrupted derived chain)

This pass is deliberately INDEPENDENT of ``validate.py`` (S0–S4): the V−
ablation arm toggles them separately, and the zero-cost replay reports
"passed S0–S4 + Δ8" as distinct gates.  Some overlap with S2 (e.g. attach
targets) is intended — each pass must stand alone.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..core.processor import PROCESSOR_HOOK_NAMES
from .types import EdgeType, GraphSnapshot, NodeType

#: Hand-written lifecycle automaton — states are the 8 processor hooks;
#: model/tool calls happen between before_/after_ pairs and are not
#: processor-attachable states.
LIFECYCLE_TRANSITIONS: "dict[str, frozenset[str]]" = {
    "task_start": frozenset({"step_start"}),
    "step_start": frozenset({"before_model"}),
    "before_model": frozenset({"after_model"}),
    "after_model": frozenset({"before_tool", "step_end"}),
    "before_tool": frozenset({"after_tool"}),
    "after_tool": frozenset({"before_tool", "step_end"}),
    "step_end": frozenset({"step_start", "task_end"}),
    "task_end": frozenset(),  # accept state
}

_DFA_STATES = frozenset(PROCESSOR_HOOK_NAMES)


@dataclass
class DfaWitness:
    """One protocol violation with its graph coordinates."""

    check: str
    message: str
    node_ids: list = field(default_factory=list)
    hook: str = ""


@dataclass
class DfaReport:
    passed: bool
    witnesses: "list[DfaWitness]" = field(default_factory=list)

    def reason(self) -> str:
        return "; ".join(f"[Δ8:{w.check}] {w.message}" for w in self.witnesses)


def lifecycle_dfa_check(snapshot: GraphSnapshot) -> DfaReport:
    """Run the six lifecycle-DFA checks over a snapshot (main + overlay)."""
    witnesses: list[DfaWitness] = []

    _check_skeleton(snapshot, witnesses)

    all_nodes = {**snapshot.nodes, **snapshot.runtime_nodes}
    attach: "dict[str, set[str]]" = {}
    for edges in (snapshot.edges, snapshot.runtime_edges):
        for edge in edges:
            if edge.edge_type is EdgeType.ATTACHED_TO:
                attach.setdefault(edge.source_id, set()).add(
                    edge.target_id.removeprefix("hook:"))

    processors = {nid: node for nid, node in all_nodes.items()
                  if node.node_type is NodeType.PROCESSOR}

    # 1 + 2: alphabet + dead processors (firing states per processor)
    firing: "dict[str, set[str]]" = {}
    for nid, node in processors.items():
        states: set[str] = set()
        for h in node.metadata.get("_hooks_", []) or []:
            if h == "*":
                states |= _DFA_STATES
            elif h in _DFA_STATES:
                states.add(h)
            else:
                witnesses.append(DfaWitness(
                    "alphabet_violation",
                    f"{nid}: coverage entry {h!r} is not a lifecycle state",
                    [nid], hook=str(h)))
        for h in attach.get(nid, ()):
            if h in _DFA_STATES:
                states.add(h)
            else:
                witnesses.append(DfaWitness(
                    "alphabet_violation",
                    f"{nid}: attached to non-lifecycle state {h!r} "
                    "(model/tool are not processor states)",
                    [nid], hook=str(h)))
        firing[nid] = states
        if not states:
            witnesses.append(DfaWitness(
                "dead_processor",
                f"{nid}: no firing state — this processor can never execute",
                [nid]))

    # 3 + 4: after enforcement vs registration buckets / temporal possibility
    bucket: "dict[str, str]" = {}
    group_nodes: "dict[str, list[str]]" = {}
    for nid, node in processors.items():
        b = node.metadata.get("_hook_")
        if isinstance(b, str) and b:
            bucket[nid] = b
        sg = node.metadata.get("_singleton_group_")
        if isinstance(sg, str) and sg:
            group_nodes.setdefault(sg, []).append(nid)

    for nid, node in processors.items():
        for ref in node.metadata.get("_after_", []) or []:
            targets = group_nodes.get(ref)
            if not targets:
                continue  # unresolved: soft dep — S2 already warns
            decl_bucket = bucket.get(nid)
            for tid in targets:
                tgt_bucket = bucket.get(tid)
                if decl_bucket is None or tgt_bucket is None:
                    continue  # bucket unknown → no claim
                if decl_bucket == tgt_bucket:
                    continue  # same bucket: the router enforces it
                # temporal impossibility beats the weaker "never enforced"
                decl_fire = firing.get(nid, set())
                tgt_fire = firing.get(tid, set())
                if decl_fire == {"task_start"} and "task_start" not in tgt_fire:
                    witnesses.append(DfaWitness(
                        "temporal_contradiction",
                        f"{nid} (fires only at task_start) declares after "
                        f"{ref!r} ({tid}), which can only fire later — the "
                        "constraint is unsatisfiable within a task pass",
                        [nid, tid], hook="task_start"))
                elif tgt_fire == {"task_end"} and "task_end" not in decl_fire:
                    witnesses.append(DfaWitness(
                        "temporal_contradiction",
                        f"{nid} declares after {ref!r} ({tid}), which fires "
                        "only at task_end — nothing after task_end can "
                        "precede this processor's states",
                        [nid, tid], hook="task_end"))
                else:
                    witnesses.append(DfaWitness(
                        "after_never_enforced",
                        f"{nid} (bucket {decl_bucket!r}) declares after "
                        f"{ref!r} ({tid}, bucket {tgt_bucket!r}) — the router "
                        "sorts per registration bucket, so this ordering is "
                        "silently never enforced",
                        [nid, tid], hook=decl_bucket))

    # 6: derived-chain state consistency
    for container, edges in (("edges", snapshot.edges),
                             ("runtime_edges", snapshot.runtime_edges)):
        for edge in edges:
            if edge.edge_type is not EdgeType.EXECUTES_BEFORE:
                continue
            chain_bucket = edge.metadata.get("hook", "")
            for endpoint in (edge.source_id, edge.target_id):
                b = bucket.get(endpoint)
                if b is not None and b != chain_bucket:
                    witnesses.append(DfaWitness(
                        "chain_state_mismatch",
                        f"{container}: EXECUTES_BEFORE chain for bucket "
                        f"{chain_bucket!r} touches {endpoint} whose "
                        f"registration bucket is {b!r} — stale or corrupted "
                        "derived chain",
                        [endpoint], hook=chain_bucket))

    return DfaReport(passed=not witnesses, witnesses=witnesses)


def _check_skeleton(snapshot: GraphSnapshot, witnesses: list) -> None:
    """Check 5: the hook skeleton must be intact for the automaton to exist."""
    from .types import SKELETON_HOOK_NAMES

    for name in SKELETON_HOOK_NAMES:
        if f"hook:{name}" not in snapshot.nodes:
            witnesses.append(DfaWitness(
                "skeleton_broken",
                f"skeleton hook node hook:{name} is missing",
                [f"hook:{name}"], hook=name))

    loop_backs = [e for e in snapshot.edges
                  if e.edge_type is EdgeType.LOOP_BACK]
    if len(loop_backs) != 1:
        witnesses.append(DfaWitness(
            "skeleton_broken",
            f"expected exactly one LOOP_BACK edge, found {len(loop_backs)}"))
    elif (loop_backs[0].source_id, loop_backs[0].target_id) != \
            ("hook:task_end", "hook:step_start"):
        witnesses.append(DfaWitness(
            "skeleton_broken",
            f"LOOP_BACK must run task_end → step_start, found "
            f"{loop_backs[0].source_id} → {loop_backs[0].target_id}"))
