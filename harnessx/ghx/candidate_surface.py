# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""Per-candidate graph mutation surface (module G2, piece 2).

The official Evolver writes each candidate's applied config to a scratch
``config.yaml``.  Two candidates that touch the same processor are a *collision*,
but the ship ledger only sees their file paths — and two edits to the same
``processors:`` list live in the same file, so a path comparison cannot tell an
overlap from an adjacency.

This module derives each candidate's **exact graph delta** against the round's
parent config and writes it as workspace evidence.  The delta is a set of node/
edge identifiers (the same ``proc:``/``hook:`` ids the M2a authority
:func:`harnessx.graph.snapshot.assign_processor_node_ids` assigns), so "do these
two candidates overlap?" becomes a subgraph intersection over identifiers rather
than a file-path guess.

Derivation is the canonical pipeline: ``HarnessConfig.from_yaml_file(...)
.canonicalize()`` for parent and candidate, ``to_graph`` both, ``diff_graphs``.
The node ids come straight from the graphs' node keys (M2a ids); ``diff_graphs``
supplies the mutated-node and edge deltas.  A candidate whose config will not load
or diff is recorded as ``derivation_failed`` with the reason — never a fabricated
empty surface, because "could not derive" is not "touches nothing".

Each candidate file at ``R{n}/graph_evidence/candidates/<candidate_id>.md`` carries
a human-readable surface plus a fenced JSON block the gate and any reader parse.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from ..graph.edit import GraphEditType, diff_graphs
from ..graph.snapshot import to_graph

_STATUS_OK = "ok"
_STATUS_FAILED = "derivation_failed"


@dataclass
class CandidateSurface:
    """The graph delta one candidate introduces over the parent config.

    ``status`` is ``ok`` or ``derivation_failed``.  On failure the ``reason`` is set
    and every id list is empty *because the delta is unknown*, which the renderer
    states explicitly — an empty surface here is never presented as "touches nothing".
    ``nodes_added``/``nodes_removed`` are M2a node ids (graph node keys);
    ``nodes_mutated`` are ids whose params changed; ``edges_added``/``edges_removed``
    are ``(source_id, target_id, edge_type)`` triples.
    """

    candidate_id: str
    status: str = _STATUS_OK
    reason: str = ""
    nodes_added: list[str] = field(default_factory=list)
    nodes_removed: list[str] = field(default_factory=list)
    nodes_mutated: list[str] = field(default_factory=list)
    edges_added: list[tuple[str, str, str]] = field(default_factory=list)
    edges_removed: list[tuple[str, str, str]] = field(default_factory=list)
    edit_count: int = 0

    @property
    def ok(self) -> bool:
        return self.status == _STATUS_OK

    def touched_node_ids(self) -> set[str]:
        """Every node id in this candidate's mutation surface — the set two
        candidates intersect to detect a real overlap."""
        return set(self.nodes_added) | set(self.nodes_removed) | set(self.nodes_mutated)

    def to_json_obj(self) -> dict:
        return {
            "candidate_id": self.candidate_id,
            "status": self.status,
            "reason": self.reason,
            "nodes_added": list(self.nodes_added),
            "nodes_removed": list(self.nodes_removed),
            "nodes_mutated": list(self.nodes_mutated),
            "edges_added": [list(e) for e in self.edges_added],
            "edges_removed": [list(e) for e in self.edges_removed],
            "edit_count": self.edit_count,
        }


def derive_candidate_surface(
    candidate_id: str,
    parent_config_path,
    candidate_config_path,
) -> CandidateSurface:
    """Compute the candidate's graph delta against the parent config.

    Canonical pipeline: load + ``canonicalize`` both configs, ``to_graph`` both,
    ``diff_graphs``.  Added/removed node ids are the symmetric difference of the two
    graphs' node keys (guaranteed to be the M2a
    :func:`assign_processor_node_ids` ids, since ``to_graph`` derives keys from that
    authority); mutated nodes and edge deltas come from the ``diff_graphs`` edit
    list.  Any exception (missing file, unparseable YAML, canonicalize/graph error)
    degrades to a ``derivation_failed`` surface carrying the reason.
    """
    from ..core.harness import HarnessConfig

    try:
        parent_cfg = HarnessConfig.from_yaml_file(str(parent_config_path)).canonicalize()
        candidate_cfg = HarnessConfig.from_yaml_file(str(candidate_config_path)).canonicalize()
        parent_graph = to_graph(parent_cfg)
        candidate_graph = to_graph(candidate_cfg)
        edits = diff_graphs(parent_graph, candidate_graph)
    except Exception as exc:  # noqa: BLE001 — any derivation failure is recorded, not raised
        return CandidateSurface(
            candidate_id=candidate_id,
            status=_STATUS_FAILED,
            reason=f"{type(exc).__name__}: {exc}",
        )

    parent_ids = set(parent_graph.nodes)
    candidate_ids = set(candidate_graph.nodes)
    nodes_added = sorted(candidate_ids - parent_ids)
    nodes_removed = sorted(parent_ids - candidate_ids)
    nodes_mutated = sorted(
        {e.target_node_id for e in edits if e.edit_type == GraphEditType.MUTATE_INACTIVE and e.target_node_id}
    )
    edges_added = sorted(
        (e.edge_source_id, e.edge_target_id, e.edge_type.value if e.edge_type else "")
        for e in edits
        if e.edit_type == GraphEditType.CHANGE_DEPENDENCY and e.add_edge
    )
    edges_removed = sorted(
        (e.edge_source_id, e.edge_target_id, e.edge_type.value if e.edge_type else "")
        for e in edits
        if e.edit_type == GraphEditType.CHANGE_DEPENDENCY and not e.add_edge
    )
    return CandidateSurface(
        candidate_id=candidate_id,
        status=_STATUS_OK,
        nodes_added=nodes_added,
        nodes_removed=nodes_removed,
        nodes_mutated=nodes_mutated,
        edges_added=edges_added,
        edges_removed=edges_removed,
        edit_count=len(edits),
    )


def _render_id_list(header: str, ids) -> list[str]:
    lines = [f"### {header}"]
    if ids:
        lines.extend(f"- `{i}`" for i in ids)
    else:
        lines.append("(none)")
    lines.append("")
    return lines


def _render_edge_list(header: str, edges) -> list[str]:
    lines = [f"### {header}"]
    if edges:
        lines.extend(f"- {etype}: `{src}` -> `{tgt}`" for src, tgt, etype in edges)
    else:
        lines.append("(none)")
    lines.append("")
    return lines


def render_surface(surface: CandidateSurface) -> str:
    """Render a candidate surface as markdown + a fenced JSON block."""
    lines: list[str] = [f"# Graph mutation surface — {surface.candidate_id}", ""]

    if not surface.ok:
        lines += [
            f"**Derivation failed**: {surface.reason}",
            "",
            "The mutation surface could NOT be derived — the parent or candidate config "
            "did not load, or the graph diff raised. This is *derivation failure*, NOT an "
            "empty delta: absence here means unknown, not none.",
            "",
            "```json",
            json.dumps(surface.to_json_obj(), ensure_ascii=False, indent=2),
            "```",
            "",
        ]
        return "\n".join(lines)

    lines += [
        "The exact node/edge identifiers this candidate mutates over the round's parent "
        "config. Ids are the M2a `assign_processor_node_ids` node ids; overlap between two "
        "candidates is the intersection of their touched-node sets, not a file-path collision.",
        "",
        f"{surface.edit_count} graph edit(s).",
        "",
        "## Touched nodes",
    ]
    lines += _render_id_list("Added", surface.nodes_added)
    lines += _render_id_list("Removed", surface.nodes_removed)
    lines += _render_id_list("Mutated (params)", surface.nodes_mutated)
    lines.append("## Touched edges")
    lines += _render_edge_list("Added", surface.edges_added)
    lines += _render_edge_list("Removed", surface.edges_removed)
    lines += [
        "## Machine-readable",
        "```json",
        json.dumps(surface.to_json_obj(), ensure_ascii=False, indent=2),
        "```",
        "",
    ]
    return "\n".join(lines)


def _candidates_dir(run_dir, round_n: int) -> Path:
    return Path(run_dir) / f"R{round_n}" / "graph_evidence" / "candidates"


def write_candidate_surface(
    run_dir,
    round_n: int,
    candidate_id: str,
    parent_config_path,
    candidate_config_path,
) -> dict:
    """Derive and write one candidate's surface to
    ``R{n}/graph_evidence/candidates/<candidate_id>.md``.

    Returns ``{"path": str, "surface": CandidateSurface}``.  A derivation failure
    still writes a file — one that states the failure — so a missing file always
    means "this candidate was never processed", never "processed and found empty".
    """
    surface = derive_candidate_surface(candidate_id, parent_config_path, candidate_config_path)
    out_dir = _candidates_dir(run_dir, round_n)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{candidate_id}.md"
    path.write_text(render_surface(surface), encoding="utf-8")
    return {"path": str(path), "surface": surface}


def materialize_candidate_surfaces(
    run_dir,
    round_n: int,
    parent_config_path,
    candidate_config_paths: dict,
) -> dict:
    """Write a surface file for every ``candidate_id -> candidate_config_path``.

    Returns a summary dict: ``candidates_dir`` (str), ``written`` (list of
    candidate ids), ``surfaces`` (candidate_id -> CandidateSurface), and ``failed``
    (ids whose surface is a recorded derivation failure — still written).
    """
    written: list[str] = []
    surfaces: dict[str, CandidateSurface] = {}
    failed: list[str] = []
    for cid, cfg_path in (candidate_config_paths or {}).items():
        res = write_candidate_surface(run_dir, round_n, cid, parent_config_path, cfg_path)
        written.append(cid)
        surface = res["surface"]
        surfaces[cid] = surface
        if not surface.ok:
            failed.append(cid)
    return {
        "candidates_dir": str(_candidates_dir(run_dir, round_n)),
        "written": written,
        "surfaces": surfaces,
        "failed": failed,
    }


__all__ = [
    "CandidateSurface",
    "derive_candidate_surface",
    "render_surface",
    "write_candidate_surface",
    "materialize_candidate_surfaces",
]
