# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""GHX ↔ AEGIS bridge (modules G1 + G2): graph evidence and enforcement.

The official AEGIS roles (Digester, Planner, Evolver, Critic) are agent
sessions that *pull* whatever evidence supports their decisions, reading the
run-dir workspace under a read-scope gate.  This package makes the graph-native
core's causal evidence available to them **without touching one byte of the
vendored** ``harnessx.aegis`` **package**: the evidence becomes ordinary
workspace files the roles can discover, not prompt injections and not a
subclass of any vendored class.

G1 — evidence:

- :func:`materialize_graph_evidence` writes cone/facts files (pure, flag-agnostic).
- :func:`core_layout_resolver` is the one convenience resolver for the U-file
  layout our core actually writes; the recipe wires its own if the layout
  differs.
- :func:`run_round_with_graph_evidence` is the flag-gated wrapper that
  materialises then delegates to ``orchestrator.run_round`` unchanged.

G2 — enforcement (graph existence vs. the vendored text-parsing attributor):

- :func:`check_signature_in_u` answers the official direct/orphan/joint question
  by counting ``tool:<name>`` nodes instead of regex-parsing digest markdown.
- :func:`materialize_candidate_surfaces` writes each candidate's exact graph delta
  (M2a node/edge ids) so overlap is a subgraph intersection, not a path collision.
- :func:`run_round_with_graph_gate` adds the sixth gate — refuse a candidate whose
  mechanical signature never fired in its replay U — flag-gated, vendored untouched.
"""

from __future__ import annotations

from .attribution_graph import (
    GraphSignatureResult,
    check_signature_in_u,
    count_tool_invocations,
    infer_signature,
)
from .candidate_surface import (
    CandidateSurface,
    derive_candidate_surface,
    materialize_candidate_surfaces,
    write_candidate_surface,
)
from .evidence_files import core_layout_resolver, materialize_graph_evidence
from .graph_gate import (
    GraphGateVerdict,
    apply_graph_gate_to_stage4,
    check_graph_gate,
    graph_gate_enabled,
    run_round_with_graph_gate,
)
from .overlay import aegis_evidence_enabled, run_round_with_graph_evidence

__all__ = [
    # G1 — evidence
    "aegis_evidence_enabled",
    "core_layout_resolver",
    "materialize_graph_evidence",
    "run_round_with_graph_evidence",
    # G2 piece 1 — graph-backed signature check
    "GraphSignatureResult",
    "check_signature_in_u",
    "count_tool_invocations",
    "infer_signature",
    # G2 piece 2 — candidate mutation surface
    "CandidateSurface",
    "derive_candidate_surface",
    "write_candidate_surface",
    "materialize_candidate_surfaces",
    # G2 piece 3 — the sixth gate
    "GraphGateVerdict",
    "check_graph_gate",
    "apply_graph_gate_to_stage4",
    "graph_gate_enabled",
    "run_round_with_graph_gate",
]
