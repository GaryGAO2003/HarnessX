# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""Offline core of the HarnessX variant pool (SPEC stage A).

Pure-Python, dependency-light re-implementation of the variant-isolation
machinery of HarnessX §4.5. Nothing in this package imports ``run.py`` /
``agent.py`` or touches the network; see ``SPEC.md`` for the module contracts
and for which defaults are ours rather than the paper's.
"""

from __future__ import annotations

from .gate import (
    DEFAULT_MIN_FORK,
    GATE_SEQUENCE,
    Decision,
    GateResult,
    GateStage,
    TaskEval,
    run_gate,
)
from .ledger import DEFAULT_STALE_PRIOR, CellStats, SuccessLedger
from .pool import DEFAULT_K, Variant, VariantPool
from .router import CLUSTER_MODES, TIE_BREAKS, Router, RoutingFreezeError
from .target import STRATEGIES, select_target_variant

__all__ = [
    "CLUSTER_MODES",
    "DEFAULT_K",
    "DEFAULT_MIN_FORK",
    "DEFAULT_STALE_PRIOR",
    "GATE_SEQUENCE",
    "STRATEGIES",
    "TIE_BREAKS",
    "CellStats",
    "Decision",
    "GateResult",
    "GateStage",
    "Router",
    "RoutingFreezeError",
    "SuccessLedger",
    "TaskEval",
    "Variant",
    "VariantPool",
    "run_gate",
    "select_target_variant",
]
