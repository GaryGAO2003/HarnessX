# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""GHX ↔ AEGIS bridge (module G1): graph evidence as workspace files.

The official AEGIS roles (Digester, Planner, Evolver, Critic) are agent
sessions that *pull* whatever evidence supports their decisions, reading the
run-dir workspace under a read-scope gate.  This package makes the graph-native
core's causal evidence available to them **without touching one byte of the
vendored** ``harnessx.aegis`` **package**: the evidence becomes ordinary
workspace files the roles can discover, not prompt injections and not a
subclass of any vendored class.

- :func:`materialize_graph_evidence` writes the files (pure, flag-agnostic).
- :func:`core_layout_resolver` is the one convenience resolver for the U-file
  layout our core actually writes; the recipe wires its own if the layout
  differs.
- :func:`run_round_with_graph_evidence` is the flag-gated wrapper that
  materialises then delegates to ``orchestrator.run_round`` unchanged.
"""

from __future__ import annotations

from .evidence_files import core_layout_resolver, materialize_graph_evidence
from .overlay import aegis_evidence_enabled, run_round_with_graph_evidence

__all__ = [
    "aegis_evidence_enabled",
    "core_layout_resolver",
    "materialize_graph_evidence",
    "run_round_with_graph_evidence",
]
