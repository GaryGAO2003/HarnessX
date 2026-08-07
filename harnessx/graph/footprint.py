"""S4 — Coverage footprints: per-task graph activity records.

A coverage footprint records which nodes and edges in a HarnessGraph
were on the active execution path during one task run.  It is the
basis for selective retest (S5: danger-edges ∩ footprint → retest
decision) and for edge-fault classification (S6: declared vs observed).

Storage format: JSON per task per variant.
  ``{variant_id}/{task_id}.footprint.json``
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .types import GraphSnapshot


@dataclass
class CoverageFootprint:
    """Per-task coverage of a harness graph."""

    task_id: str
    variant_id: str = ""
    run_id: str = ""

    # Set of node_ids that were on the active execution path.
    touched_node_ids: set[str] = field(default_factory=set)
    # Set of edge keys (source_id + target_id + edge_type) observed.
    observed_edge_keys: set[str] = field(default_factory=set)

    # Metadata
    step_count: int = 0
    exit_reason: str = ""
    genotype_hash: str = ""  # config genotype this footprint was computed against

    # ── serialization ──────────────────────────────────────────────────

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "variant_id": self.variant_id,
            "run_id": self.run_id,
            "touched_node_ids": sorted(self.touched_node_ids),
            "observed_edge_keys": sorted(self.observed_edge_keys),
            "step_count": self.step_count,
            "exit_reason": self.exit_reason,
            "genotype_hash": self.genotype_hash,
        }

    @classmethod
    def from_dict(cls, data: dict) -> CoverageFootprint:
        return cls(
            task_id=data.get("task_id", ""),
            variant_id=data.get("variant_id", ""),
            run_id=data.get("run_id", ""),
            touched_node_ids=set(data.get("touched_node_ids", [])),
            observed_edge_keys=set(data.get("observed_edge_keys", [])),
            step_count=data.get("step_count", 0),
            exit_reason=data.get("exit_reason", ""),
            genotype_hash=data.get("genotype_hash", ""),
        )

    # ── intersection ───────────────────────────────────────────────────

    def intersects(self, danger_node_ids: set[str], danger_edge_keys: set[str]) -> bool:
        """Does this footprint intersect a set of danger nodes/edges?

        Used by S5 selective retest: if the intersection is empty,
        the task can inherit its parent's score.
        """
        if self.touched_node_ids & danger_node_ids:
            return True
        if self.observed_edge_keys & danger_edge_keys:
            return True
        return False


# ── persistence ─────────────────────────────────────────────────────────────


class FootprintStore:
    """Persistent store for coverage footprints.

    Layout::

        {base_dir}/{variant_id}/{task_id}.footprint.json
    """

    def __init__(self, base_dir: Path):
        self.base_dir = Path(base_dir)

    def _path(self, variant_id: str, task_id: str) -> Path:
        return self.base_dir / variant_id / f"{task_id}.footprint.json"

    def put(self, footprint: CoverageFootprint) -> None:
        path = self._path(footprint.variant_id, footprint.task_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(footprint.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def get(self, variant_id: str, task_id: str) -> CoverageFootprint | None:
        path = self._path(variant_id, task_id)
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return CoverageFootprint.from_dict(data)
        except Exception:
            return None

    def get_all(self, variant_id: str) -> dict[str, CoverageFootprint]:
        """Load all footprints for a variant."""
        footprints: dict[str, CoverageFootprint] = {}
        variant_dir = self.base_dir / variant_id
        if not variant_dir.is_dir():
            return footprints
        for fp_file in sorted(variant_dir.glob("*.footprint.json")):
            fp = self.get(variant_id, fp_file.stem.replace(".footprint", ""))
            if fp is not None:
                footprints[fp.task_id] = fp
        return footprints

    def footprint_union(
        self, variant_id: str, task_ids: list[str]
    ) -> tuple[set[str], set[str]]:
        """Union of node and edge sets across multiple task footprints.

        Used by heuristic-mode selective retest (S5).
        """
        all_nodes: set[str] = set()
        all_edges: set[str] = set()
        for tid in task_ids:
            fp = self.get(variant_id, tid)
            if fp is not None:
                all_nodes |= fp.touched_node_ids
                all_edges |= fp.observed_edge_keys
        return all_nodes, all_edges


# ── computation ─────────────────────────────────────────────────────────────


def compute_footprint(trace, snapshot: GraphSnapshot) -> CoverageFootprint:
    """Compute a coverage footprint from a TaskTrace and a GraphSnapshot.

    Maps observation records back to graph node/edge identities.

    Args:
        trace: A ``TaskTrace`` from ``ObservationProcessor.flush()``.
        snapshot: The graph snapshot the task was running against.

    Returns:
        CoverageFootprint with touched nodes and observed edges.
    """
    from .observer import TaskTrace as _TaskTrace

    touched_nodes: set[str] = set()
    observed_edges: set[str] = set()

    # Build lookup: processor label → node_id
    label_to_node: dict[str, str] = {}
    for node_id, node in snapshot.nodes.items():
        label_to_node[node.label] = node_id
        # Also index by target tail
        target = node.metadata.get("_target_", "")
        if target:
            tail = target.rsplit(".", 1)[-1] if "." in target else target
            label_to_node[tail] = node_id

    for obs in trace.observations:
        # Map processor to graph node
        node_id = label_to_node.get(obs.processor_label, "")
        if not node_id and obs.processor_target:
            tail = obs.processor_target.rsplit(".", 1)[-1] if "." in obs.processor_target else obs.processor_target
            node_id = label_to_node.get(tail, "")

        if node_id:
            touched_nodes.add(node_id)

        # Map hook to skeleton node
        hook_node_id = f"hook:{obs.hook_name}"
        if hook_node_id in snapshot.nodes:
            touched_nodes.add(hook_node_id)
            if node_id:
                edge_key = f"{node_id}→{hook_node_id}"
                observed_edges.add(edge_key)

        # Map tools to slot writes
        for tool_name in obs.tools_called:
            # Tool invocation → write to slot:tool_registry
            slot_id = "slot:tool_registry"
            if slot_id in snapshot.nodes:
                touched_nodes.add(slot_id)
                if node_id:
                    edge_key = f"{node_id}→{slot_id}"
                    observed_edges.add(edge_key)
            # Also mark the tool processor itself
            tool_node_id = label_to_node.get(f"tool:{tool_name}", "")
            if tool_node_id:
                touched_nodes.add(tool_node_id)

    return CoverageFootprint(
        task_id=trace.task_id,
        variant_id=trace.variant_id,
        run_id=trace.run_id,
        touched_node_ids=touched_nodes,
        observed_edge_keys=observed_edges,
        step_count=len(trace.observations),
        exit_reason="",
        genotype_hash=snapshot.genotype_hash,
    )
