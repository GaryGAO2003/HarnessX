# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""Offline core of the HarnessX variant pool (SPEC stage A).

Pure-Python, dependency-light re-implementation of the variant-isolation
machinery of HarnessX §4.5. Nothing in this package imports ``run.py`` /
``agent.py`` or touches the network; see ``SPEC.md`` for the module contracts
and for which defaults are ours rather than the paper's.
"""

from __future__ import annotations

from .accounting import (
    DEFAULT_PRICING,
    MTOK,
    ROLES,
    AttemptCost,
    CostLedger,
    Pricing,
)
from .engine import DEFAULT_PATIENCE, RoundResult, VariantPoolEngine
from .evidence import (
    LEVER_BAN_HIT_RATE,
    LEVER_BAN_MIN_SHIPS,
    LEVER_BAN_WINDOW,
    EvidenceStore,
    RejectedCandidate,
    ShipOutcome,
    TaskDigest,
    ship_outcome_from_manifest,
)
from .experiment_lock import (
    FAMILY_SECTIONS,
    LOCK_FILENAME,
    PAPER_LEVEL_DISTRIBUTION,
    SPEC_VERSION,
    DatasetSpec,
    EnvSpec,
    ExperimentFamilyError,
    ExperimentLock,
    H0Freeze,
    Hyperparams,
    ModelSpec,
    sha256_file,
    sha256_text,
)
from .gate import (
    DEFAULT_MIN_FORK,
    DEFAULT_REGRESSION_BASELINE,
    GATE_SEQUENCE,
    REGRESSION_BASELINE_MODES,
    Decision,
    GateResult,
    GateStage,
    TaskEval,
    level2_roundtrip_check,
    run_gate,
)
from .ledger import DEFAULT_STALE_PRIOR, CellStats, SuccessLedger
from .manifest import (
    BUCKETS,
    LEVEL2_CLAIM,
    AttributionGraphResult,
    AttributionSignature,
    ChangeManifest,
    ImpactCategory,
    Level2Evidence,
    PredictedImpact,
    check_attribution_in_graph,
    check_level2_roundtrip,
    impact_category,
)
from .pool import DEFAULT_K, Variant, VariantPool
from .reporting import (
    IMPLICIT_VARIANT,
    POOL_EVENT_KINDS,
    RunReport,
    TaskResult,
    pass_at_k,
    report_from_rows,
)
from .router import CLUSTER_MODES, TIE_BREAKS, Router, RoutingFreezeError
from .target import STRATEGIES, select_target_variant

__all__ = [
    "BUCKETS",
    "CLUSTER_MODES",
    "DEFAULT_K",
    "DEFAULT_MIN_FORK",
    "DEFAULT_PATIENCE",
    "DEFAULT_PRICING",
    "DEFAULT_REGRESSION_BASELINE",
    "DEFAULT_STALE_PRIOR",
    "FAMILY_SECTIONS",
    "GATE_SEQUENCE",
    "IMPLICIT_VARIANT",
    "LEVEL2_CLAIM",
    "LEVER_BAN_HIT_RATE",
    "LEVER_BAN_MIN_SHIPS",
    "LEVER_BAN_WINDOW",
    "LOCK_FILENAME",
    "MTOK",
    "PAPER_LEVEL_DISTRIBUTION",
    "POOL_EVENT_KINDS",
    "REGRESSION_BASELINE_MODES",
    "ROLES",
    "SPEC_VERSION",
    "STRATEGIES",
    "TIE_BREAKS",
    "AttemptCost",
    "AttributionGraphResult",
    "AttributionSignature",
    "CellStats",
    "ChangeManifest",
    "CostLedger",
    "DatasetSpec",
    "Decision",
    "EnvSpec",
    "EvidenceStore",
    "ExperimentFamilyError",
    "ExperimentLock",
    "GateResult",
    "GateStage",
    "H0Freeze",
    "Hyperparams",
    "ImpactCategory",
    "Level2Evidence",
    "ModelSpec",
    "PredictedImpact",
    "Pricing",
    "RejectedCandidate",
    "RoundResult",
    "Router",
    "RoutingFreezeError",
    "RunReport",
    "ShipOutcome",
    "SuccessLedger",
    "TaskDigest",
    "TaskEval",
    "TaskResult",
    "Variant",
    "VariantPool",
    "VariantPoolEngine",
    "check_attribution_in_graph",
    "check_level2_roundtrip",
    "impact_category",
    "level2_roundtrip_check",
    "pass_at_k",
    "report_from_rows",
    "run_gate",
    "select_target_variant",
    "sha256_file",
    "sha256_text",
    "ship_outcome_from_manifest",
]
