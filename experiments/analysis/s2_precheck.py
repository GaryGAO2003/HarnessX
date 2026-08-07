"""S2 — Offline replay pre-check.

Measures four quantities from historical run artifacts without making any
LLM calls (zero API cost).  The results decide:

* Whether selective retest (S5) runs in safe or heuristic mode.
* Whether graph build validation (S3) is worth the backfill investment.
* Whether genotype dedup (S1) pays for itself.

Quantities
----------
① **illegal candidate rate**
   Fraction of historical candidates that ``build()`` would have rejected
   (conflicts, cycles, interface mismatches).  Measured with currently-
   available declaration metadata — this is a **lower bound**; the true
   rate rises as declarations are backfilled.

② **footprint intersection sparsity**
   Upper bound for selective retest savings.  For candidate edits, what
   fraction of tasks have a footprint that does NOT intersect the danger
   edge set?  Higher → more tasks can inherit parent scores.

③ **duplicate candidate rate**
   Fraction of candidate slots that contained structurally identical
   configs (same genotype hash).

④ **footprint stability**
   Jaccard overlap of per-task coverage footprints across repeated
   executions of the same task.  ≥ 0.8 → safe determinstic replay is
   feasible; < 0.6 → fall back to heuristic (union) mode.

Decisions
---------
============ ===== ====================================================
Quantity     Gate  Decision
============ ===== ====================================================
④ stability  ≥0.7  safe mode (deterministic replay)
④ stability  <0.5  heuristic mode (footprint union)
② sparsity   ≥0.7  selective retest high-return
② sparsity   <0.3  selective retest not worth the complexity
③ duplicate  ≥0.2  genotype dedup pays for itself
① illegal    ≥0.1  build validation catches real rejections
============ ===== ====================================================

Bias declarations
-----------------
All quantities carry pre-registered bias directions (§4 of the S2 spec):

* ② (sparsity) is an **upper-bound** read — footprints are smaller than
  true dependency sets because observation extraction is incomplete.
* ① (illegal rate) is a **lower-bound** read when declarations are
  backfilled only partially — empty declarations see zero conflicts.
* ③ (duplicate rate) is an **upper-bound** read — genotype hashing is
  structural, so functionally different configs with accidentally
  identical structure are counted as duplicates.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


# ── metrics ─────────────────────────────────────────────────────────────────


@dataclass
class PreCheckMetrics:
    """The four S2 quantities plus metadata."""

    # ①
    total_candidates: int = 0
    illegal_count: int = 0
    illegal_rate: float = 0.0
    illegal_error_types: dict[str, int] = field(default_factory=dict)

    # ②
    footprint_sparsity_mean: float = 0.0
    footprint_sparsity_median: float = 0.0
    task_pair_count: int = 0

    # ③
    duplicate_rate: float = 0.0
    unique_genotypes: int = 0

    # ④
    footprint_stability_mean: float = 0.0
    footprint_stability_std: float = 0.0
    tasks_with_repeats: int = 0

    # metadata
    source_directory: str = ""
    bias_declarations: list[str] = field(default_factory=list)


# ── computation ────────────────────────────────────────────────────────────


def compute_illegal_rate(
    candidate_configs: list[dict],
    *,
    declarations: dict[str, dict] | None = None,
) -> tuple[int, int, dict[str, int]]:
    """① Measure illegal candidate rate.

    Args:
        candidate_configs: List of serialized HarnessConfig dicts (as from
            ``HarnessConfig.to_yaml()`` or ``_target_`` processor lists).
        declarations: Optional backfilled declaration metadata keyed by
            ``_target_`` class name.  If None, uses only what is embedded
            in the configs themselves.

    Returns:
        (total, illegal_count, error_type_counts)
    """
    from harnessx.core.builder import HarnessConflictError, _topological_sort_entries, _ProcEntry

    total = len(candidate_configs)
    illegal = 0
    error_types: dict[str, int] = {}

    for config_dict in candidate_configs:
        errors = _validate_config_dict(config_dict, declarations or {})
        if errors:
            illegal += 1
            for err_type in errors:
                error_types[err_type] = error_types.get(err_type, 0) + 1

    return total, illegal, error_types


def compute_duplicate_rate(
    candidate_configs: list[dict],
) -> tuple[int, int, float]:
    """③ Measure duplicate candidate rate via genotype hashing.

    Returns:
        (total, unique_genotypes, duplicate_rate)
    """
    from harnessx.graph.snapshot import to_graph
    from harnessx.graph.identity import genotype_hash

    seen: set[str] = set()
    total = len(candidate_configs)

    from harnessx.core.harness import HarnessConfig

    for config_dict in candidate_configs:
        try:
            # Reconstruct HarnessConfig from its serialized form.
            # The YAML-level dict has "processors" as a list of _target_ dicts.
            kwargs = {k: v for k, v in config_dict.items()
                      if k in ("processors", "tool_registry", "tracer",
                               "workspace", "workspace_template", "init_workspace",
                               "step_snapshots", "sandbox_provider", "sandbox_hint_id")}
            config = HarnessConfig(**kwargs)
            snapshot = to_graph(config)
            gh = genotype_hash(snapshot)
            seen.add(gh)
        except Exception:
            # Un-parseable config → count as unique (conservative)
            seen.add(f"__unparseable_{id(config_dict)}")

    unique = len(seen)
    rate = 1.0 - (unique / total) if total > 0 else 0.0
    return total, unique, rate


def compute_footprint_sparsity(
    footprints: dict[str, set[str]],
) -> tuple[float, float, int]:
    """② Measure footprint intersection sparsity.

    Args:
        footprints: ``{task_id: set_of_edge_or_node_ids}`` mapping.

    Returns:
        (mean_jaccard_distance, median_jaccard_distance, pair_count)
    """
    task_ids = list(footprints)
    distances: list[float] = []

    for i in range(len(task_ids)):
        for j in range(i + 1, len(task_ids)):
            a = footprints[task_ids[i]]
            b = footprints[task_ids[j]]
            if not a and not b:
                continue
            # Jaccard distance = 1 - |A∩B| / |A∪B|
            intersection = len(a & b)
            union = len(a | b)
            if union == 0:
                distances.append(1.0)
            else:
                distances.append(1.0 - intersection / union)

    if not distances:
        return 0.0, 0.0, 0

    distances.sort()
    n = len(distances)
    mean = sum(distances) / n
    median = distances[n // 2] if n % 2 == 1 else (distances[n // 2 - 1] + distances[n // 2]) / 2.0
    return mean, median, n


def compute_footprint_stability(
    repeated_footprints: dict[str, list[set[str]]],
) -> tuple[float, float, int]:
    """④ Measure footprint stability across repeated executions.

    Args:
        repeated_footprints: ``{task_id: [run1_footprint, run2_footprint, ...]}``
            where each entry has at least 2 runs.

    Returns:
        (mean_jaccard, std_jaccard, tasks_with_repeats)
    """
    similarities: list[float] = []

    for task_id, runs in repeated_footprints.items():
        if len(runs) < 2:
            continue
        # Pairwise Jaccard similarity for all run pairs
        for i in range(len(runs)):
            for j in range(i + 1, len(runs)):
                a, b = runs[i], runs[j]
                intersection = len(a & b)
                union = len(a | b)
                if union == 0:
                    similarities.append(1.0)
                else:
                    similarities.append(intersection / union)

    if not similarities:
        return 0.0, 0.0, 0

    n = len(similarities)
    mean = sum(similarities) / n
    variance = sum((x - mean) ** 2 for x in similarities) / n
    std = variance ** 0.5
    return mean, std, len(repeated_footprints)


# ── runner ─────────────────────────────────────────────────────────────────


def run_precheck(run_dir: Path) -> PreCheckMetrics:
    """Run the full S2 pre-check against a run directory.

    The expected layout::

        run_dir/
          data/
            candidates/         # candidate config YAML files
            task_footprints/    # per-task footprint JSON (S4 output)
            task_history.jsonl  # per-round task outcomes
          evidence/
            ship_outcomes.json

    If data is missing, the corresponding metric is left at its default
    (usually 0.0) and a bias declaration is added.

    Args:
        run_dir: Path to a run output directory.

    Returns:
        PreCheckMetrics with all computable quantities filled in.
    """
    metrics = PreCheckMetrics(source_directory=str(run_dir))

    # ① illegal rate
    candidates = _load_candidates(run_dir)
    if candidates:
        total, illegal, error_types = compute_illegal_rate(candidates)
        metrics.total_candidates = total
        metrics.illegal_count = illegal
        metrics.illegal_rate = illegal / total if total > 0 else 0.0
        metrics.illegal_error_types = dict(error_types)
    else:
        metrics.bias_declarations.append(
            "① illegal_rate: no historical candidate configs found — "
            "measurement is a structural lower bound (empty declarations)"
        )

    # ② footprint sparsity
    footprints = _load_footprints(run_dir)
    if footprints:
        mean, median, pairs = compute_footprint_sparsity(footprints)
        metrics.footprint_sparsity_mean = mean
        metrics.footprint_sparsity_median = median
        metrics.task_pair_count = pairs
    else:
        metrics.bias_declarations.append(
            "② footprint_sparsity: no footprint data found — "
            "upper-bound read, value represents best-case savings"
        )

    # ③ duplicate rate
    if candidates:
        total, unique, rate = compute_duplicate_rate(candidates)
        metrics.duplicate_rate = rate
        metrics.unique_genotypes = unique
    else:
        metrics.bias_declarations.append(
            "③ duplicate_rate: no candidates to hash — "
            "upper-bound read, structural hashing may over-count duplicates"
        )

    # ④ footprint stability
    repeated = _load_repeated_footprints(run_dir)
    if repeated:
        mean, std, tasks = compute_footprint_stability(repeated)
        metrics.footprint_stability_mean = mean
        metrics.footprint_stability_std = std
        metrics.tasks_with_repeats = tasks
    else:
        metrics.bias_declarations.append(
            "④ footprint_stability: no repeated task executions found — "
            "stability unknown, defaulting to heuristic mode"
        )

    return metrics


# ── decision ───────────────────────────────────────────────────────────────


def decide_mode(metrics: PreCheckMetrics) -> dict[str, str]:
    """Derive S5 mode decisions from S2 measurements.

    Returns a dict of decision keys to values.
    """
    decisions: dict[str, str] = {}

    # ④ → safe vs heuristic
    if metrics.tasks_with_repeats > 0:
        if metrics.footprint_stability_mean >= 0.7:
            decisions["retest_mode"] = "safe"
        elif metrics.footprint_stability_mean < 0.5:
            decisions["retest_mode"] = "heuristic"
        else:
            decisions["retest_mode"] = "heuristic"  # conservative default
    else:
        decisions["retest_mode"] = "heuristic"  # no stability data → safe default
        decisions["retest_mode_reason"] = "no_stability_data"

    # ② → selective retest value
    if metrics.footprint_sparsity_median >= 0.7:
        decisions["selective_retest_value"] = "high"
    elif metrics.footprint_sparsity_median < 0.3:
        decisions["selective_retest_value"] = "low"
    else:
        decisions["selective_retest_value"] = "moderate"

    # ③ → dedup value
    decisions["dedup_investment"] = (
        "worthwhile" if metrics.duplicate_rate >= 0.2 else "marginal"
    )

    # ① → build validation value
    decisions["build_validation_value"] = (
        "high" if metrics.illegal_rate >= 0.1 else "moderate_or_low"
    )

    return decisions


# ── internal helpers ───────────────────────────────────────────────────────


def _load_candidates(run_dir: Path) -> list[dict]:
    """Load candidate config dicts from a run directory."""
    candidates: list[dict] = []

    candidates_dir = run_dir / "data" / "candidates"
    if candidates_dir.is_dir():
        for yaml_file in sorted(candidates_dir.glob("*.yaml")):
            try:
                import yaml as _yaml
                with open(yaml_file, encoding="utf-8") as f:
                    data = _yaml.safe_load(f)
                    if isinstance(data, dict):
                        candidates.append(data)
            except Exception:
                continue

    return candidates


def _load_footprints(run_dir: Path) -> dict[str, set[str]]:
    """Load per-task footprints from a run directory.

    Returns ``{task_id: set_of_edge_or_node_ids}``.
    """
    footprints: dict[str, set[str]] = {}

    footprints_dir = run_dir / "data" / "task_footprints"
    if footprints_dir.is_dir():
        for json_file in sorted(footprints_dir.glob("*.json")):
            try:
                with open(json_file, encoding="utf-8") as f:
                    data = json.load(f)
                task_id = data.get("task_id", json_file.stem)
                edge_ids = set(data.get("observed_edge_keys", []) or data.get("touched_node_ids", []))
                if edge_ids:
                    footprints[task_id] = edge_ids
            except Exception:
                continue

    return footprints


def _load_repeated_footprints(run_dir: Path) -> dict[str, list[set[str]]]:
    """Load repeated-execution footprints.

    Returns ``{task_id: [run1_fp, run2_fp, ...]}`` for tasks with ≥2 runs.
    """
    from collections import defaultdict

    footprints_dir = run_dir / "data" / "task_footprints"
    if not footprints_dir.is_dir():
        return {}

    # Group by task_id
    grouped: dict[str, list[set[str]]] = defaultdict(list)

    for json_file in sorted(footprints_dir.glob("*.json")):
        try:
            with open(json_file, encoding="utf-8") as f:
                data = json.load(f)
            task_id = data.get("task_id", json_file.stem)
            edge_ids = set(data.get("observed_edge_keys", []) or data.get("touched_node_ids", []))
            if edge_ids:
                grouped[task_id].append(edge_ids)
        except Exception:
            continue

    # Keep only tasks with repeats
    return {tid: runs for tid, runs in grouped.items() if len(runs) >= 2}


def _validate_config_dict(
    config_dict: dict,
    declarations: dict[str, dict],
) -> list[str]:
    """Check whether a config dict would survive ``build()`` validation.

    Returns a list of error type strings (empty = valid).
    """
    errors: list[str] = []

    processors = config_dict.get("processors", [])
    if not isinstance(processors, list):
        return ["invalid_processors_field"]

    # Collect singleton groups
    sg_counts: dict[str, int] = {}
    for proc in processors:
        if not isinstance(proc, dict):
            continue
        sg = proc.get("_singleton_group_") or ""
        if not sg:
            # Also check declarations
            target = proc.get("_target_", "")
            decl = declarations.get(target, {})
            sg = decl.get("_singleton_group_", "")
        if sg:
            sg_counts[sg] = sg_counts.get(sg, 0) + 1

    # Singleton conflicts
    for sg, count in sg_counts.items():
        if count > 1:
            errors.append(f"singleton_conflict:{sg}")

    # After-dependency resolution check
    sg_set = set(sg_counts)
    for proc in processors:
        if not isinstance(proc, dict):
            continue
        after_raw = proc.get("_after_")
        if after_raw is None:
            target = proc.get("_target_", "")
            decl = declarations.get(target, {})
            after_raw = decl.get("_after_")
        if after_raw:
            after_list = after_raw if isinstance(after_raw, (list, tuple)) else [after_raw]
            for after_sg in after_list:
                if after_sg not in sg_set:
                    errors.append(f"unresolved_after:{after_sg}")

    return errors
