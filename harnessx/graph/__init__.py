"""HarnessX Graph IR — typed graph representation of agent harness configurations.

The graph IR sits between evolution and execution: evolution reads graph
snapshots, produces typed graph edits, validates via ``build()``, and
uses selective retest (danger-edges ∩ task-footprint) instead of
full-task-bed measurement.

Core API
--------
.. code-block:: python

    from harnessx.graph import to_graph, genotype_hash, GraphSnapshot, Node, Edge

    snapshot = to_graph(harness_config)
    ghash = genotype_hash(snapshot)
"""

from .declaration import (
    ComponentDecl,
    DeclarationSource,
    WELL_KNOWN_DECLARATIONS,
    backfill_declarations,
    merge_declarations,
    validate_declarations,
)
from .edit import GraphEdit, GraphEditType, apply_edits, diff_graphs
from .footprint import CoverageFootprint, FootprintStore, compute_footprint
from .identity import DedupRegistry, genotype_hash, phenotype_hash
from .impact import danger_edge_set, forward_slice, influence_cone, intersects_footprint
from .observer import HookObservation, ObservationProcessor, TaskTrace
from .reconciliation import (
    ConvergenceReport,
    ReconciliationCategory,
    ReconciledEdge,
    reconcile,
)
from .snapshot import to_graph
from .transform import graph_to_config_dict
from .types import (
    SKELETON_HOOK_NAMES,
    Edge,
    EdgeType,
    GraphSnapshot,
    Node,
    NodeType,
)

__all__ = [
    # types
    "NodeType",
    "EdgeType",
    "Node",
    "Edge",
    "GraphSnapshot",
    "SKELETON_HOOK_NAMES",
    # snapshot
    "to_graph",
    # identity
    "genotype_hash",
    "phenotype_hash",
    "DedupRegistry",
    # declaration (S3)
    "ComponentDecl",
    "DeclarationSource",
    "WELL_KNOWN_DECLARATIONS",
    "backfill_declarations",
    "merge_declarations",
    "validate_declarations",
    # observer (S4)
    "ObservationProcessor",
    "HookObservation",
    "TaskTrace",
    # footprint (S4)
    "CoverageFootprint",
    "FootprintStore",
    "compute_footprint",
    # reconciliation (S4)
    "ConvergenceReport",
    "ReconciliationCategory",
    "ReconciledEdge",
    "reconcile",
    # edit (S5)
    "GraphEdit",
    "GraphEditType",
    "apply_edits",
    "diff_graphs",
    # impact (S5)
    "forward_slice",
    "danger_edge_set",
    "influence_cone",
    "intersects_footprint",
    # transform (S5)
    "graph_to_config_dict",
]
