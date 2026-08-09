"""S3 — Graph-aware gate stage for build() validation.

Wires ``HarnessBuilder.build()`` validation into the deterministic gate
pipeline so that illegal candidates (conflicts, cycles, interface
mismatches) are intercepted *before* measurement budget is spent.

This is a new gate stage that runs between MANIFEST_COMPLETE and
SEESAW_REGRESSION.  It replaces the no-op CANONICALIZE and BUILD_SMOKE_L1
stages for graph-aware candidates.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable

from experiments.variant_pool.gate import GateStage
from experiments.variant_pool.manifest import ChangeManifest


# ── graph gate stage ───────────────────────────────────────────────────────


class GraphGateStage(str, Enum):
    """Graph-specific gate stages (extends the paper's 5-stage gate)."""

    GRAPH_BUILD = "graph_build"  # validate candidate graph via build()
    GRAPH_DEDUP = "graph_dedup"  # genotype hash duplicate check (S1)
    GRAPH_METADATA = "graph_metadata"  # new processor nodes must carry metadata
    GRAPH_DFA = "graph_dfa"  # Δ8 lifecycle-DFA protocol pass


# ── result types ────────────────────────────────────────────────────────────


@dataclass
class GraphValidationError:
    """One graph validation error, with graph coordinates."""

    error_type: str  # "singleton_conflict", "cycle", "unresolved_after", …
    message: str
    node_ids: list[str] = field(default_factory=list)
    edge_ids: list[str] = field(default_factory=list)


@dataclass
class GraphValidationReport:
    """Result of graph build validation."""

    passed: bool
    errors: list[GraphValidationError] = field(default_factory=list)
    warnings: list[GraphValidationError] = field(default_factory=list)
    genotype_hash: str = ""  # set when passed (S1)

    def rejection_reason(self) -> str:
        if self.passed:
            return ""
        lines = [f"[{e.error_type}] {e.message}" for e in self.errors]
        return "; ".join(lines)


# ── validation entry point ─────────────────────────────────────────────────


def validate_candidate_graph(
    candidate_config_path: Path,
    parent_config_path: Path | None = None,
    *,
    declarations: dict[str, "ComponentDecl"] | None = None,
) -> GraphValidationReport:
    """Validate a candidate's graph before measurement.

    1. Load the candidate's HarnessConfig from YAML.
    2. Export to GraphSnapshot via ``to_graph()`` (S1).
    3. Run structural validation: cycles, conflicts, interface mismatches.
    4. Compute genotype hash for dedup (S1).
    5. (Future S5) Validate graph edits against parent graph.

    Args:
        candidate_config_path: Path to the candidate's config.yaml.
        parent_config_path: Optional path to parent variant's config.
        declarations: Optional backfilled declaration metadata.

    Returns:
        GraphValidationReport — passed=True if the candidate is structurally valid.
    """
    from harnessx.core.builder import HarnessConflictError, build_from_config
    from harnessx.core.harness import HarnessConfig
    from harnessx.graph.declaration import backfill_declarations
    from harnessx.graph.identity import genotype_hash
    from harnessx.graph.snapshot import to_graph

    errors: list[GraphValidationError] = []
    warnings: list[GraphValidationError] = []

    # 1. Load candidate config
    try:
        config = HarnessConfig.from_yaml_file(str(candidate_config_path))
    except Exception as exc:
        errors.append(GraphValidationError(
            error_type="config_load",
            message=f"Cannot load candidate config: {exc}",
        ))
        return GraphValidationReport(passed=False, errors=errors, warnings=warnings)

    # 2. Export to graph first (pure-read, no imports needed)
    try:
        snapshot = to_graph(config)
    except Exception as exc:
        errors.append(GraphValidationError(
            error_type="graph_export",
            message=f"Cannot export to graph: {exc}",
        ))
        return GraphValidationReport(passed=False, errors=errors, warnings=warnings)

    # 2b. GRAPH_METADATA: new processor nodes must carry metadata
    for node_id, node in snapshot.nodes.items():
        if node.node_type.value != "processor":
            continue
        target = node.metadata.get("_target_", "")
        sg = node.metadata.get("_singleton_group_", "")
        from harnessx.graph.declaration import WELL_KNOWN_DECLARATIONS
        if target not in WELL_KNOWN_DECLARATIONS and not sg:
            warnings.append(GraphValidationError(
                error_type="missing_metadata",
                message=(
                    f"New processor '{node.label}' has no _singleton_group_. "
                    f"Without metadata, graph cannot model its impact — "
                    f"selective retest will treat it as wildcard."
                ),
                node_ids=[node_id],
            ))

    # 3. Static S0–S3 validation (P3 fail-closed validator) — subsumes the old
    #    ad-hoc singleton/after checks and adds schema/kind/order layers.
    from harnessx.graph.validate import validate_snapshot

    static = validate_snapshot(snapshot)
    for issue in static.issues:
        errors.append(GraphValidationError(
            error_type=issue.error_type,
            message=f"[{issue.layer}] {issue.message}",
            node_ids=list(issue.node_ids),
            edge_ids=list(issue.edge_ids),
        ))
    for w in static.warnings:
        warnings.append(GraphValidationError(
            error_type=w.error_type,
            message=f"[{w.layer}] {w.message}",
            node_ids=list(w.node_ids),
            edge_ids=list(w.edge_ids),
        ))

    # 3b. Δ8 lifecycle-DFA pass — a SEPARATE gate from S0–S3 (the V− ablation
    #     toggles them independently; the replay reports "S0–S4 + Δ8").
    from harnessx.graph.dfa import lifecycle_dfa_check

    dfa = lifecycle_dfa_check(snapshot)
    for w in dfa.witnesses:
        errors.append(GraphValidationError(
            error_type=f"dfa_{w.check}",
            message=f"[Δ8] {w.message}",
            node_ids=list(w.node_ids),
        ))

    # 4. build_from_config — FAIL-CLOSED (P3 exit criterion / I9): a candidate
    #    whose build() raises is REJECTED.  ImportError is no longer downgraded
    #    to a warning — co-located processors must be importable before gating.
    config_dict = _config_to_dict(config)
    try:
        build_from_config(config_dict)
    except HarnessConflictError as exc:
        for conflict in exc.conflicts:
            errors.append(GraphValidationError(
                error_type="build_conflict",
                message=conflict,
            ))
    except Exception as exc:  # noqa: BLE001 — ImportError included, fail closed
        errors.append(GraphValidationError(
            error_type="build_failed",
            message=f"build() raised {type(exc).__name__}: {exc}",
        ))

    if errors:
        return GraphValidationReport(passed=False, errors=errors, warnings=warnings)

    # 5. Compute genotype hash
    gh = genotype_hash(snapshot)

    passed = len(errors) == 0
    return GraphValidationReport(
        passed=passed,
        errors=errors,
        warnings=warnings,
        genotype_hash=gh if passed else "",
    )


# ── gate integration ───────────────────────────────────────────────────────


def make_graph_build_check(
    declarations: dict[str, "ComponentDecl"] | None = None,
) -> Callable[[Any, Path, Any, list[Any]], tuple[bool, str]]:
    """Create an injectable graph-build gate check.

    Returns a callable compatible with the gate's injection seam
    (``check_canonicalize`` / ``check_smoke``).  The callable receives
    ``(candidate, parent_config_path, ledger, tk_results)`` and returns
    ``(passed, reason)``.
    """

    def _check(candidate, parent_config_path, ledger, tk_results) -> tuple[bool, str]:
        # Extract config path from candidate
        config_path = _config_path_from_candidate(candidate)
        if config_path is None:
            return False, "graph_build: no config path on candidate"

        report = validate_candidate_graph(
            config_path,
            parent_config_path=parent_config_path,
            declarations=declarations,
        )

        if report.passed:
            return True, f"graph_build: OK (genotype={report.genotype_hash[:12]}…)"

        return False, report.rejection_reason()

    return _check


# ── helpers ─────────────────────────────────────────────────────────────────


def _config_to_dict(config: "HarnessConfig") -> dict[str, Any]:
    """Extract the flat dict representation from a HarnessConfig."""
    return {
        "processors": list(config.processors),
        "plugins": list(config.plugins) if config.plugins else [],
    }


def _config_path_from_candidate(candidate: Any) -> Path | None:
    """Extract config path from a candidate of unknown type."""
    # CandidateArtifact (from manifest.py)
    if hasattr(candidate, "config_path"):
        return Path(candidate.config_path)
    # ChangeManifest
    if hasattr(candidate, "candidate_id"):
        # No config path — candidate might be manifest-only
        return None
    # Raw Path
    if isinstance(candidate, (str, Path)):
        return Path(candidate)
    return None


# ── import for type hints ──────────────────────────────────────────────────
from harnessx.graph.declaration import ComponentDecl  # noqa: E402
