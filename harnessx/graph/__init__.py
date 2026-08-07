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

from .identity import DedupRegistry, genotype_hash, phenotype_hash
from .snapshot import to_graph
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
]
