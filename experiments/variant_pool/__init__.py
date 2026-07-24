# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""Offline core of the HarnessX variant pool (SPEC stage A).

Pure-Python, dependency-light re-implementation of the variant-isolation
machinery of HarnessX §4.5. Nothing in this package imports ``run.py`` /
``agent.py`` or touches the network; see ``SPEC.md`` for the module contracts
and for which defaults are ours rather than the paper's.
"""

from __future__ import annotations

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
    GATE_SEQUENCE,
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
    AttributionSignature,
    ChangeManifest,
    ImpactCategory,
    Level2Evidence,
    PredictedImpact,
    check_level2_roundtrip,
    impact_category,
)
from .pool import DEFAULT_K, Variant, VariantPool
from .router import CLUSTER_MODES, TIE_BREAKS, Router, RoutingFreezeError
from .target import STRATEGIES, select_target_variant

__all__ = [
    "BUCKETS",
    "CLUSTER_MODES",
    "DEFAULT_K",
    "DEFAULT_MIN_FORK",
    "DEFAULT_STALE_PRIOR",
    "FAMILY_SECTIONS",
    "GATE_SEQUENCE",
    "LEVEL2_CLAIM",
    "LEVER_BAN_HIT_RATE",
    "LEVER_BAN_MIN_SHIPS",
    "LEVER_BAN_WINDOW",
    "LOCK_FILENAME",
    "PAPER_LEVEL_DISTRIBUTION",
    "SPEC_VERSION",
    "STRATEGIES",
    "TIE_BREAKS",
    "AttributionSignature",
    "CellStats",
    "ChangeManifest",
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
    "RejectedCandidate",
    "Router",
    "RoutingFreezeError",
    "ShipOutcome",
    "SuccessLedger",
    "TaskDigest",
    "TaskEval",
    "Variant",
    "VariantPool",
    "check_level2_roundtrip",
    "impact_category",
    "level2_roundtrip_check",
    "run_gate",
    "select_target_variant",
    "sha256_file",
    "sha256_text",
    "ship_outcome_from_manifest",
]
