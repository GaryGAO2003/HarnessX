"""Δ11 (half) — graph element ↔ journal JSONL line back-links.

Pure infrastructure for debuggability and GS-D targeting: when a diagnosis
says "this node/edge is suspicious", the back-link resolves it to the exact
journal lines it came from — and a journal line resolves back to the graph
elements active at that moment.  It never changes any reading.

The chain::

    graph node id  ←→  HookObservation (Δ11 anchor: journal_uuid)
                   ←→  {run_id}.jsonl line (uuid field → line number)

Node-id derivation reuses the snapshot slug rule (``_compute_slug``), so the
ids here are the SAME ids ``to_graph()`` allocates — no parallel naming.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .observer import HookObservation, TaskTrace
from .snapshot import _compute_slug


def observation_node_id(obs: HookObservation) -> str:
    """Graph node id for one observation (same slugs as ``to_graph``).

    - a qualified processor target → its ``proc:{slug}`` node;
    - model / tool / bare hook observations → the ``hook:{name}`` skeleton
      node (the composition layer does not graph model/tool behaviour —
      Δ14 owner-test boundary).
    """
    if obs.processor_target:
        return f"proc:{_compute_slug(obs.processor_target)}"
    label = obs.processor_label or ""
    if label and label not in ("model",) and not label.startswith("tool:") \
            and label != obs.hook_name:
        # a concrete processor label without a qualified target
        return f"proc:{_compute_slug(label)}"
    return f"hook:{obs.hook_name}"


@dataclass
class BacklinkRef:
    """One resolved link between a graph element and a journal line."""

    node_id: str
    run_id: str
    step_id: int
    hook_name: str
    journal_uuid: str


class BacklinkIndex:
    """Bidirectional node ↔ journal-uuid index built from task traces."""

    def __init__(self) -> None:
        self._by_node: "dict[str, list[BacklinkRef]]" = {}
        self._by_uuid: "dict[str, list[BacklinkRef]]" = {}

    @classmethod
    def from_trace(cls, trace: TaskTrace) -> "BacklinkIndex":
        idx = cls()
        idx.add_trace(trace)
        return idx

    def add_trace(self, trace: TaskTrace) -> None:
        for obs in trace.observations:
            ref = BacklinkRef(
                node_id=observation_node_id(obs),
                run_id=trace.run_id,
                step_id=obs.step_id,
                hook_name=obs.hook_name,
                journal_uuid=obs.journal_uuid,
            )
            self._by_node.setdefault(ref.node_id, []).append(ref)
            if ref.journal_uuid:
                self._by_uuid.setdefault(ref.journal_uuid, []).append(ref)

    def refs_for_node(self, node_id: str) -> "list[BacklinkRef]":
        """Graph → journal: every observed firing of this node."""
        return list(self._by_node.get(node_id, []))

    def nodes_for_uuid(self, journal_uuid: str) -> "list[str]":
        """Journal → graph: node ids active at (or right after) this line."""
        return [r.node_id for r in self._by_uuid.get(journal_uuid, [])]


# ── literal line-number resolution (the 行号 half of the back-link) ──────────


def resolve_journal_line(
    jsonl_path: "str | Path", journal_uuid: str,
) -> "tuple[int, dict] | None":
    """Find the 1-based line number + record of a uuid in a session JSONL."""
    path = Path(jsonl_path)
    if not journal_uuid or not path.exists():
        return None
    with path.open("r", encoding="utf-8") as fh:
        for line_no, line in enumerate(fh, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if record.get("uuid") == journal_uuid:
                return line_no, record
    return None


def uuid_at_line(jsonl_path: "str | Path", line_no: int) -> str:
    """Reverse direction: the uuid recorded at a 1-based JSONL line."""
    path = Path(jsonl_path)
    if line_no < 1 or not path.exists():
        return ""
    with path.open("r", encoding="utf-8") as fh:
        for i, line in enumerate(fh, start=1):
            if i == line_no:
                try:
                    return str(json.loads(line).get("uuid", "") or "")
                except json.JSONDecodeError:
                    return ""
    return ""
