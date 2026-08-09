"""Graph IR type system — node types, edge types, and the core graph snapshot.

Node types model the four-layer view of a HarnessX agent:
  - execution layer: 10 skeleton hook nodes (the fixed runloop backbone)
  - composition layer: processor nodes attached to hooks
  - data layer: slot nodes (memory, plan, cost, etc.)
  - encapsulation layer: bundle/sub-graph nodes

Edge types carry typed semantics so downstream mechanisms (validation,
footprinting, selective retest, skill governance) operate on edges
rather than on opaque text.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field


# ── node types ──────────────────────────────────────────────────────────────


class NodeType(str, enum.Enum):
    """Typed node categories in a harness graph."""

    SKELETON_HOOK = "skeleton_hook"  # one of the 10 fixed runloop hook points
    PROCESSOR = "processor"  # a registered processor / _ProcEntry
    SLOT = "slot"  # a typed data channel (memory, plan, cost, …)
    BUNDLE = "bundle"  # a named sub-graph encapsulation
    SKILL = "skill"  # S6: skill node in the heterogeneous skill graph
    TOOL = "tool"  # S6: tool node in the heterogeneous skill graph


# ── edge types ──────────────────────────────────────────────────────────────


class EdgeType(str, enum.Enum):
    """Typed edge categories in a harness graph.

    Declared edges come from build-time metadata (``_ProcEntry`` fields,
    ``HarnessConfig`` slots).  Observed edges come from runtime traces (S4).
    """

    # structural / declared
    ATTACHED_TO = "attached_to"  # processor → hook (which hook the processor fires on)
    AFTER = "after"  # processor → processor (soft ordering dep within same hook)
    WRITES_TO = "writes_to"  # processor → slot (declared write)
    READS_FROM = "reads_from"  # processor → slot (declared read)
    COMPOSES_WITH = "composes_with"  # bundle / sub-graph membership
    CONFLICTS_WITH = "conflicts_with"  # singleton_group mutual exclusion
    SPECIALIZES = "specializes"  # S6: skill refinement / inheritance
    LOOP_BACK = "loop_back"  # task_end → step_start (the only cycle)
    EXECUTES_BEFORE = "executes_before"  # derived execution order within a bucket (L4.6/L5.6)

    # observed / runtime (S4)
    OBSERVED_CONTROL = "observed_control"  # processor A triggered → processor B triggered
    OBSERVED_DATA = "observed_data"  # runtime data flow between processors / slots


# ── node ────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Node:
    """One node in a harness graph.

    Frozen so that genotype hashing is deterministic — identity is structural.
    """

    node_id: str
    node_type: NodeType
    label: str  # human-readable (processor class name, hook name, slot name, …)
    metadata: dict = field(default_factory=dict, hash=False)
    # metadata carries type-specific fields:
    #   PROCESSOR: _target_, _hook_, _order_, _singleton_group, _after
    #   SKELETON_HOOK: hook_name
    #   SLOT: slot_name, slot_type
    #   BUNDLE: child_graph_id, interface_signature


# ── edge ────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Edge:
    """One typed edge in a harness graph."""

    source_id: str
    target_id: str
    edge_type: EdgeType
    metadata: dict = field(default_factory=dict, hash=False)
    # metadata carries type-specific fields:
    #   provenance: "declared" | "observed" | "llm_draft"
    #   observation_count: int  (for observed edges)
    #   confidence: float       (for llm-drafted edges)


# ── graph snapshot ──────────────────────────────────────────────────────────


@dataclass
class GraphSnapshot:
    """A typed, hash-addressed snapshot of a harness configuration as a graph.

    This is the central IR that sits between evolution (which reads snapshots
    and produces typed graph edits) and execution (which is gated by build()
    validation and selective retest).
    """

    nodes: dict[str, Node] = field(default_factory=dict)
    edges: list[Edge] = field(default_factory=list)
    runtime_nodes: dict[str, Node] = field(default_factory=dict)   # runtime-only processors/slots
    runtime_edges: list[Edge] = field(default_factory=list)        # runtime overlay + EXECUTES_BEFORE chains

    # content-addressed identity (S1)
    genotype_hash: str = ""
    deployment_hash: str = ""  # genotype + runtime overlay (L6.3)
    phenotype_hash: str = ""  # deployment + observed edges (L6.5)

    # source provenance
    source_config_hash: str = ""  # hash of the YAML config this was derived from

    def node_ids(self) -> set[str]:
        return set(self.nodes)

    def edges_by_type(self, edge_type: EdgeType) -> list[Edge]:
        return [e for e in self.edges if e.edge_type == edge_type]

    def successors(self, node_id: str) -> set[str]:
        return {e.target_id for e in self.edges if e.source_id == node_id}

    def predecessors(self, node_id: str) -> set[str]:
        return {e.source_id for e in self.edges if e.target_id == node_id}


# ── convenience ─────────────────────────────────────────────────────────────

# The 10 fixed skeleton hooks, in runloop execution order.
SKELETON_HOOK_NAMES: tuple[str, ...] = (
    "task_start",
    "step_start",
    "before_model",
    "model",
    "after_model",
    "before_tool",
    "tool",
    "after_tool",
    "step_end",
    "task_end",
)
