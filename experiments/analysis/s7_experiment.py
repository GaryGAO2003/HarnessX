"""S7 — Controlled experiment: text evolution vs graph-IR evolution.

Runs two arms of the same harness evolution experiment under identical
conditions (same seed, same tasks, same budget), differing only in
whether the evolution loop operates on text YAML or graph IR.

Metrics collected per arm
-------------------------
* pass@k — task success rate over rounds
* proposal efficiency — fraction of proposed candidates that ship
* illegal rejection rate — candidates caught by build() validation (graph only)
* dedup savings — duplicate candidates skipped (graph only)
* selective retest savings — fraction of tasks that inherited parent scores (graph only)
* attribution precision — hit_rate of predicted_impact vs realized (both arms)
* total budget spent — API cost in USD
* rounds to convergence — when idle ≥ patience
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


# ── metric types ────────────────────────────────────────────────────────────


@dataclass
class ArmResult:
    """Results from one arm of the experiment."""

    mode: str  # "text" or "graph"
    rounds: int = 0
    total_candidates: int = 0
    shipped_candidates: int = 0
    rejected_candidates: int = 0

    # Task success
    final_pass_at_k: float = 0.0
    peak_pass_at_k: float = 0.0
    rounds_to_converge: int = 0

    # Budget
    total_cost_usd: float = 0.0
    api_calls: int = 0

    # Graph-specific (zero for text arm)
    illegal_rejected: int = 0
    duplicate_skipped: int = 0
    retest_inherited_tasks: int = 0
    retest_total_tasks: int = 0
    edge_faults_detected: int = 0

    # Attribution
    attribution_hit_rate: float = 0.0
    attribution_total_predicted: int = 0
    attribution_total_hits: int = 0

    # Per-round data
    round_data: list[dict] = field(default_factory=list)


@dataclass
class ExperimentResult:
    """Comparison of text vs graph arms."""

    text: ArmResult = field(default_factory=lambda: ArmResult(mode="text"))
    graph: ArmResult = field(default_factory=lambda: ArmResult(mode="graph"))

    def efficiency_delta(self) -> float:
        """Difference in proposal efficiency (graph - text)."""
        te = self.text.shipped_candidates / max(self.text.total_candidates, 1)
        ge = self.graph.shipped_candidates / max(self.graph.total_candidates, 1)
        return ge - te

    def pass_delta(self) -> float:
        """Difference in final pass@k."""
        return self.graph.final_pass_at_k - self.text.final_pass_at_k

    def cost_delta(self) -> float:
        """Cost savings (negative = graph cheaper)."""
        return self.graph.total_cost_usd - self.text.total_cost_usd

    def retest_savings_pct(self) -> float:
        """Fraction of tasks that inherited scores (graph only)."""
        if self.graph.retest_total_tasks == 0:
            return 0.0
        return self.graph.retest_inherited_tasks / self.graph.retest_total_tasks

    def summary(self) -> str:
        lines = [
            "=== S7 Controlled Experiment ===",
            f"Text arm:  {self.text.final_pass_at_k:.2%} pass@k, "
            f"${self.text.total_cost_usd:.2f}, "
            f"efficiency={self.text.shipped_candidates}/{self.text.total_candidates}",
            f"Graph arm: {self.graph.final_pass_at_k:.2%} pass@k, "
            f"${self.graph.total_cost_usd:.2f}, "
            f"efficiency={self.graph.shipped_candidates}/{self.graph.total_candidates}",
            "",
            f"Pass delta:     {self.pass_delta():+.2%}",
            f"Cost delta:     ${self.cost_delta():+.2f}",
            f"Efficiency Δ:   {self.efficiency_delta():+.2%}",
            f"Retest savings: {self.retest_savings_pct():.0%} (graph only)",
        ]
        if self.graph.illegal_rejected > 0:
            lines.append(f"Illegal caught: {self.graph.illegal_rejected} (graph only)")
        if self.graph.duplicate_skipped > 0:
            lines.append(f"Duplicates:     {self.graph.duplicate_skipped} (graph only)")
        if self.graph.edge_faults_detected > 0:
            lines.append(f"Edge faults:    {self.graph.edge_faults_detected} (graph only)")
        return "\n".join(lines)


# ── experiment config ───────────────────────────────────────────────────────


@dataclass
class ExperimentConfig:
    """Configuration for a controlled experiment run."""

    task_ids: list[str] = field(default_factory=list)
    rounds: int = 10
    patience: int = 3
    seed: int = 42
    pool_capacity: int = 8
    target_budget_per_round_usd: float = 2.0

    # Model config (shared across arms)
    model_name: str = "claude-sonnet-4-6"
    pass_at: int = 2  # pass@k for evaluation

    def to_dict(self) -> dict:
        return {
            "task_count": len(self.task_ids),
            "rounds": self.rounds,
            "patience": self.patience,
            "seed": self.seed,
            "pool_capacity": self.pool_capacity,
            "budget_per_round_usd": self.target_budget_per_round_usd,
            "model": self.model_name,
            "pass_at": self.pass_at,
        }


# ── comparison helpers ──────────────────────────────────────────────────────


def compare_arms(text: ArmResult, graph: ArmResult) -> ExperimentResult:
    """Produce a structured comparison of the two arms."""
    return ExperimentResult(text=text, graph=graph)


def compute_statistical_significance(
    text_round_data: list[dict],
    graph_round_data: list[dict],
) -> dict[str, float]:
    """Compute effect sizes and p-values for key metrics.

    Uses bootstrap confidence intervals for pass@k differences
    and Mann-Whitney U for cost/round comparisons.
    """
    # Simple bootstrap CI for pass@k difference
    import random
    random.seed(42)

    text_passes = [r.get("pass_count", 0) for r in text_round_data]
    graph_passes = [r.get("pass_count", 0) for r in graph_round_data]

    n_bootstrap = 1000
    diffs: list[float] = []
    n = min(len(text_passes), len(graph_passes))
    if n < 2:
        return {"mean_diff": 0.0, "ci_low": 0.0, "ci_high": 0.0, "n_rounds": n}

    for _ in range(n_bootstrap):
        indices = [random.randint(0, n - 1) for _ in range(n)]
        t = sum(text_passes[i] for i in indices) / n
        g = sum(graph_passes[i] for i in indices) / n
        diffs.append(g - t)

    diffs.sort()
    mean_diff = sum(diffs) / len(diffs)
    ci_low = diffs[int(n_bootstrap * 0.025)]
    ci_high = diffs[int(n_bootstrap * 0.975)]

    return {
        "mean_diff": mean_diff,
        "ci_low": ci_low,
        "ci_high": ci_high,
        "n_rounds": n,
        "significant": not (ci_low <= 0 <= ci_high),
    }
