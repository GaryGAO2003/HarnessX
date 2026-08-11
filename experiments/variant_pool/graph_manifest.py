"""S5 — GraphEditManifest: extends ChangeManifest with graph edit fields.

The standard ChangeManifest carries predicted impacts at the task level
(task_id lists).  GraphEditManifest adds graph edit operations and a
danger edge summary so the selective retest engine can compute task
footprint intersections without re-parsing the manifest text.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from harnessx.graph.edit import GraphEdit, GraphEditType


@dataclass
class GraphEditManifest:
    """Graph-aware extension of change manifest.

    Carries the graph edits alongside the standard ChangeManifest fields
    so that danger-edge computation (S5) and selective retest decisions
    can be made deterministically.
    """

    candidate_id: str = ""
    edits: list[GraphEdit] = field(default_factory=list)
    parent_genotype_hash: str = ""
    child_genotype_hash: str = ""

    # Predicted impact at graph level
    affected_node_ids: set[str] = field(default_factory=set)
    affected_edge_keys: set[str] = field(default_factory=set)

    # Standard ChangeManifest fields (mirrored for gate compatibility)
    target_variant: str = ""
    bucket: list[str] = field(default_factory=list)
    predicted_impact_task_ids: list[str] = field(default_factory=list)

    @property
    def edit_count(self) -> int:
        return len(self.edits)

    @property
    def edit_types(self) -> set[GraphEditType]:
        return {e.edit_type for e in self.edits}

    def summary(self) -> str:
        types = ",".join(sorted(et.value for et in self.edit_types))
        return (
            f"GraphEditManifest({self.candidate_id}, "
            f"{self.edit_count} edits [{types}], "
            f"affected={len(self.affected_node_ids)} nodes)"
        )

    @classmethod
    def from_edits(
        cls,
        candidate_id: str,
        edits: list[GraphEdit],
        parent_genotype: str = "",
        child_genotype: str = "",
    ) -> GraphEditManifest:
        """Create from a list of GraphEdits and hashes."""
        affected_nodes: set[str] = set()
        for edit in edits:
            affected_nodes |= edit.affected_node_ids()

        return cls(
            candidate_id=candidate_id,
            edits=list(edits),
            parent_genotype_hash=parent_genotype,
            child_genotype_hash=child_genotype,
            affected_node_ids=affected_nodes,
        )

    def to_dict(self) -> dict:
        return {
            "candidate_id": self.candidate_id,
            "edits": [
                {
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
                for e in self.edits
            ],
            "parent_genotype_hash": self.parent_genotype_hash,
            "child_genotype_hash": self.child_genotype_hash,
            "target_variant": self.target_variant,
            "bucket": self.bucket,
        }


@dataclass(frozen=True)
class GraphEditDerivation:
    """The result of deriving graph edits from a parent->child config change.

    ``source`` records WHICH path produced ``edits`` — never inferred from
    configuration (the 4e0810f discipline): ``"graph"`` means both configs
    loaded and the edits are the exact ``diff_graphs`` delta (possibly empty
    when the change implies no graph delta, e.g. a prompt-text-only edit);
    ``"unavailable"`` means the derivation could not run and ``edits`` is
    ``None`` so the caller degrades to today's path-string surface. ``reason``
    always explains what happened.
    """

    edits: tuple[GraphEdit, ...] | None
    source: str
    reason: str


def derive_graph_edits(parent_config_path, child_config_path) -> GraphEditDerivation:
    """Derive the exact :class:`GraphEdit`s implied by a config change.

    A config diff genuinely IMPLIES a graph delta, so the edits are computed —
    not inferred from prose — by loading both configs, exporting each with
    :func:`harnessx.graph.snapshot.to_graph`, and taking
    :func:`harnessx.graph.edit.diff_graphs` of the two snapshots. Every node id
    on the returned edits therefore comes from ``to_graph``'s sole authority,
    :func:`harnessx.graph.snapshot.assign_processor_node_ids`; none is minted
    here and no second differ is written.

    Never raises: any load/parse failure degrades to
    ``GraphEditDerivation(None, "unavailable", <reason>)`` so a candidate keeps
    the path-string Critic surface rather than losing the round.
    """
    try:
        from harnessx.core.harness import HarnessConfig
        from harnessx.graph.edit import diff_graphs
        from harnessx.graph.snapshot import to_graph

        before = HarnessConfig.from_yaml_file(Path(parent_config_path)).canonicalize()
        after = HarnessConfig.from_yaml_file(Path(child_config_path)).canonicalize()
        edits = tuple(diff_graphs(to_graph(before), to_graph(after)))
    except Exception as exc:  # noqa: BLE001 - degrade to the path-string surface
        return GraphEditDerivation(
            None,
            "unavailable",
            f"graph-edit derivation unavailable: {type(exc).__name__}: {exc}",
        )
    return GraphEditDerivation(
        edits,
        "graph",
        f"graph-derived from parent->child config diff: {len(edits)} edit(s)",
    )
