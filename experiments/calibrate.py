# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""W30 — the cost-calibration run: measure small, project to M0.

SPEC §6.8 (Codex critique 10) replaces the earlier "6 tasks to measure tokens"
plan with a **level-stratified** sample, because six tasks taken off the top of
a task list measure token volume and nothing else. Three things have to come
out of this run before M0 is budgeted (checklist §6):

1. the real context-cache hit rate — the one large lever on the bill;
2. the real billed tokens per attempt;
3. whether DeepSeek V4-flash clears the **capability floor** on GAIA. Appendix
   D.5 is the warning: a too-weak inner-loop model (Qwen3.5-9B on SWE) collapses
   to a 0.05 hit rate and "evolution cannot accumulate", which would make an M0
   null result a false negative about the *pool*, not about the model.

Scope of this batch
-------------------
Everything here is offline: stratified sampling, the accounting, the projection
arithmetic and the rendering. The path that actually drives a harness is a
single injected ``runner`` callable and :func:`harness_runner` raises
``NotImplementedError`` — wiring it to the real evaluation loop is
``TODO(batch-B)``. Nothing imports ``run.py`` (SPEC §5).

Sample sizes
------------
SPEC §6.8 names two: 2/3/1 = 6 and 8/12/4 = 24. The second is *not* the paper's
39/52/12 ratio (that would be 9/12/3 at n=24); it over-weights level 3, which is
the stratum where a proportional sample is too small to say anything — twelve
tasks in the paper's own set. Both named quotas are used verbatim; any other
``--size`` falls back to the paper's ratio by largest remainder.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

EXPERIMENTS_DIR = Path(__file__).resolve().parent
if str(EXPERIMENTS_DIR) not in sys.path:  # `experiments` is not a package
    sys.path.insert(0, str(EXPERIMENTS_DIR))

from variant_pool.accounting import DEFAULT_PRICING, AttemptCost, CostLedger, Pricing  # noqa: E402
from variant_pool.experiment_lock import PAPER_LEVEL_DISTRIBUTION, DatasetSpec  # noqa: E402
from variant_pool.reporting import RunReport, TaskResult  # noqa: E402

REPO_ROOT = EXPERIMENTS_DIR.parent
DEFAULT_DATASET = REPO_ROOT / "recipe/gaia_evolver/data/webthinker_gaia_dev.json"

#: The two sample sizes SPEC §6.8 names explicitly. Not proportional to
#: A.2's 39/52/12 — see the module docstring.
CALIBRATION_QUOTAS: dict[int, dict[int, int]] = {
    6: {1: 2, 2: 3, 3: 1},
    24: {1: 8, 2: 12, 3: 4},
}

#: Full-scale M0: 3 lineages x 103 tasks x 15 rounds x pass@2 (checklist §6).
M0_SCALE = {"lineages": 3, "tasks": 103, "rounds": 15, "k": 2}


# ---------------------------------------------------------------------------
# Stratified sampling
# ---------------------------------------------------------------------------


def level_quota(size: int) -> dict[int, int]:
    """Tasks per GAIA level for a sample of ``size``.

    The two sizes SPEC §6.8 names are returned verbatim. Anything else is the
    paper's 39/52/12 ratio allocated by largest remainder, which keeps the sum
    exactly equal to ``size`` — plain rounding does not (it gives 5 at n=6).
    """
    if size < 1:
        raise ValueError(f"size must be >= 1, got {size}")
    if size in CALIBRATION_QUOTAS:
        return dict(CALIBRATION_QUOTAS[size])

    total = sum(PAPER_LEVEL_DISTRIBUTION.values())
    exact = {level: share * size / total for level, share in PAPER_LEVEL_DISTRIBUTION.items()}
    quota = {level: int(value) for level, value in exact.items()}
    remaining = size - sum(quota.values())
    # largest fractional part first; ties go to the larger stratum, then to the
    # lower level, so the allocation is deterministic
    order = sorted(
        exact,
        key=lambda level: (-(exact[level] - quota[level]), -PAPER_LEVEL_DISTRIBUTION[level], level),
    )
    for level in order[:remaining]:
        quota[level] += 1
    return dict(sorted(quota.items()))


def stratified_sample(tasks: Sequence[Mapping[str, Any]], size: int) -> list[dict[str, Any]]:
    """Take ``size`` tasks holding the level mix of :func:`level_quota`.

    Deterministic and RNG-free, the same rule as
    ``experiments/build_gaia_subset.py``: within a level, sort by ``task_id``
    and take from the front. A level with too few tasks is topped up from the
    remaining ones (largest stratum first) so the sample still has ``size``
    entries — and :func:`level_counts` will show that the mix slipped.
    """
    quota = level_quota(size)
    by_level: dict[int, list[dict[str, Any]]] = {}
    for task in tasks:
        by_level.setdefault(int(task["Level"]), []).append(dict(task))
    for rows in by_level.values():
        rows.sort(key=lambda row: str(row["task_id"]))

    picked: list[dict[str, Any]] = []
    leftovers: list[dict[str, Any]] = []
    for level in sorted(set(quota) | set(by_level)):
        pool = by_level.get(level, [])
        want = quota.get(level, 0)
        picked.extend(pool[:want])
        leftovers.extend(pool[want:])
    if len(picked) < size:
        leftovers.sort(key=lambda row: (-PAPER_LEVEL_DISTRIBUTION.get(int(row["Level"]), 0), str(row["task_id"])))
        picked.extend(leftovers[: size - len(picked)])
    return picked[:size]


def level_counts(tasks: Sequence[Mapping[str, Any]]) -> dict[int, int]:
    counts: dict[int, int] = {}
    for task in tasks:
        level = int(task["Level"])
        counts[level] = counts.get(level, 0) + 1
    return dict(sorted(counts.items()))


def level_map(tasks: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    """``task_id -> level``, the mapping :meth:`RunReport.by_level` needs."""
    return {str(task["task_id"]): int(task["Level"]) for task in tasks}


def load_tasks(path: str | Path) -> list[dict[str, Any]]:
    """Read a webthinker-schema task file (the format build_gaia_subset writes)."""
    rows = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(rows, list):
        raise ValueError(f"{path} must hold a JSON array of tasks")
    return rows


# ---------------------------------------------------------------------------
# The evaluation seam
# ---------------------------------------------------------------------------


@dataclass
class AttemptOutcome:
    """One rollout: did it pass, and what did it consume."""

    passed: bool
    cost: AttemptCost
    infra_failure: bool = False


#: What batch B has to supply: run ``task`` once and report the outcome.
AttemptRunner = Callable[..., AttemptOutcome]


def harness_runner(*args: Any, **kwargs: Any) -> AttemptOutcome:
    """The real evaluation path — not wired in this batch.

    TODO(batch-B): drive the GAIA harness for one attempt and build the
    :class:`AttemptCost` from the provider ``Usage`` via
    :meth:`AttemptCost.from_usage` (which subtracts the cached prefix out of
    ``prompt_tokens``). Kept as a stub so the calibration pipeline is complete
    and testable without touching ``run.py`` (SPEC §5).
    """
    raise NotImplementedError("TODO(batch-B): wire the GAIA harness attempt path into calibrate.py")


@dataclass
class Calibration:
    """The offline product of a calibration run."""

    report: RunReport
    ledger: CostLedger
    tasks: list[dict[str, Any]] = field(default_factory=list)
    dataset: DatasetSpec | None = None
    synthetic: bool = False

    def level_map(self) -> dict[str, int]:
        return level_map(self.tasks)


def run_calibration(
    tasks: Sequence[Mapping[str, Any]],
    runner: AttemptRunner,
    *,
    k: int = 2,
    round_idx: int = 0,
    pricing: Pricing | Mapping[str, Pricing] | None = None,
    run_name: str = "calibration",
) -> Calibration:
    """Run ``k`` attempts per task, recording every attempt in a :class:`CostLedger`.

    One round: calibration measures the *cost of a round*, and the meta agent's
    share is whatever the runner books under ``role="meta_agent"``. Infrastructure
    failures are counted as failures on both sides (A.3) — the attempt is still
    billed and still in the denominator.
    """
    if k < 1:
        raise ValueError(f"k must be >= 1, got {k}")
    ledger = CostLedger(pricing=pricing if pricing is not None else DEFAULT_PRICING)
    report = RunReport(run_name=run_name, k=k)

    for task in tasks:
        task_id = str(task["task_id"])
        passes = 0
        infra = 0
        for attempt_idx in range(k):
            outcome = runner(task, attempt_idx=attempt_idx, round_idx=round_idx)
            ledger.record(outcome.cost)
            passes += 1 if outcome.passed else 0
            infra += 1 if (outcome.infra_failure or outcome.cost.infra_failure) else 0
        report.add(
            TaskResult(
                task_id=task_id,
                round_idx=round_idx,
                n_att=k,
                n_pass=passes,
                infra_failures=min(infra, k - passes),
            )
        )
    return Calibration(report=report, ledger=ledger, tasks=[dict(task) for task in tasks])


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def render(
    calibration: Calibration,
    *,
    scale: Mapping[str, int] | None = None,
    floor: float | None = None,
) -> str:
    """Markdown: sample, cache, per-attempt bill, level pass rates, projection."""
    scale = dict(scale) if scale is not None else dict(M0_SCALE)
    report, ledger = calibration.report, calibration.ledger
    lines: list[str] = ["# Cost calibration (W30)", ""]
    if calibration.synthetic:
        lines += [
            "> **DRY RUN — synthetic outcomes.** The pass rates below are made up by a",
            "> stub runner and are not evidence about any model. Only the plumbing is real.",
            "",
        ]

    lines += ["## Sample", "", f"- tasks: {len(calibration.tasks)}", f"- by level: {level_counts(calibration.tasks)}"]
    if calibration.dataset is not None:
        lines += [
            f"- dataset: `{calibration.dataset.path}`",
            f"- sha256: `{calibration.dataset.sha256}`",
            f"- full-set level mix: {calibration.dataset.level_distribution}",
        ]
    lines.append("")

    by_role = ledger.by_role()
    lines += ["## Tokens and cache", "", "| role | attempts | miss in | cached in | out | cache hit rate |", "|---|---|---|---|---|---|"]
    for role, totals in by_role.items():
        rate = totals["cache_hit_rate"]
        lines.append(
            f"| {role} | {totals['attempts']} | {totals['input_tokens']} | {totals['cached_input_tokens']} "
            f"| {totals['output_tokens']} | {'n/a' if rate is None else f'{rate:.3f}'} |"
        )
    overall = ledger.cache_hit_rate()
    lines += [
        "",
        f"- overall cache hit rate: {'n/a' if overall is None else f'{overall:.3f}'} "
        "(the dominant term in the M0 budget, checklist §6)",
    ]

    billed = ledger.total_billed()
    per_attempt = ledger.per_attempt_billed()
    task_attempts = ledger.attempts("task_agent")
    if task_attempts:
        totals = by_role["task_agent"]
        lines += [
            f"- billed tokens per task-agent attempt: "
            f"{(totals['input_tokens'] + totals['cached_input_tokens']) / task_attempts:.0f} in "
            f"({totals['cached_input_tokens'] / task_attempts:.0f} cached) / "
            f"{totals['output_tokens'] / task_attempts:.0f} out",
        ]
    lines += [
        f"- measured spend: task ${billed['task_agent']:.4f} + meta ${billed['meta_agent']:.4f} "
        f"= ${billed['total']:.4f}",
        f"- per task-agent attempt: {'n/a' if per_attempt is None else f'${per_attempt:.6f}'}",
        "",
    ]

    lines += ["## Capability floor", ""]
    if report.rounds():
        mapping = calibration.level_map()
        by_level = report.by_level(mapping)
        counts = report.level_counts(mapping)
        lines += [f"| level | tasks | pass@{report.k} |", "|---|---|---|"]
        lines += [f"| {level} | {counts[level]} | {score:.4f} |" for level, score in by_level.items()]
        overall_pass = report.pass_at_k()
        lines += [
            "",
            f"- overall pass@{report.k}: {overall_pass:.4f}; pass@1: {report.pass_at_1():.4f}",
        ]
        if floor is None:
            lines += [
                "- floor check: **not asserted**. Pass `--floor` with the threshold this run",
                "  must clear; D.5 warns that a too-weak inner-loop model makes an M0 null",
                "  result a false negative about the pool rather than about the model.",
            ]
        else:
            verdict = "clears" if overall_pass > floor else "**below**"
            lines += [f"- floor check: {overall_pass:.4f} {verdict} the {floor:.4f} floor."]
    else:
        lines += ["_no attempts recorded_"]
    lines.append("")

    projection = ledger.project(**scale)
    lines += [
        "## Projection to full M0",
        "",
        f"- scale: {scale['lineages']} lineages x {scale['tasks']} tasks x {scale['rounds']} rounds "
        f"x pass@{scale['k']} = {projection['attempts']} attempts, {projection['meta_cycles']} meta cycles",
        f"- task agent: ${projection['task_agent_usd']:.2f}",
        f"- meta agent: ${projection['meta_agent_usd']:.2f}"
        + ("" if projection["basis"]["meta_agent_measured"] else "  ⚠️ **not measured in this sample**"),
        f"- **budget range: ${projection['total_usd']:.2f} (at the measured cache hit rate) "
        f"– ${projection['total_usd_no_cache']:.2f} (cache cold)**",
        f"- basis: {projection['basis']['sample_attempts']} sampled attempts, "
        f"{projection['basis']['sample_meta_rounds']} meta round(s); "
        f"pricing {projection['basis']['pricing']}",
        "",
        "> Re-run this after every large harness change (SPEC §6.8): the Digester swallows",
        "> ~10M raw trace tokens per round, so the meta-agent term moves when the loop does.",
        "",
    ]
    return "\n".join(lines)


def summary(calibration: Calibration, *, scale: Mapping[str, int] | None = None) -> dict[str, Any]:
    """Machine-readable companion to :func:`render`."""
    scale = dict(scale) if scale is not None else dict(M0_SCALE)
    return {
        "synthetic": calibration.synthetic,
        "sample_size": len(calibration.tasks),
        "sample_by_level": {str(level): n for level, n in level_counts(calibration.tasks).items()},
        "cache_hit_rate": calibration.ledger.cache_hit_rate(),
        "by_role": calibration.ledger.by_role(),
        "billed": calibration.ledger.total_billed(),
        "per_attempt_usd": calibration.ledger.per_attempt_billed(),
        "projection": calibration.ledger.project(**scale),
        "report": calibration.report.to_dict(level_map=calibration.level_map()),
    }


# ---------------------------------------------------------------------------
# Dry-run stub
# ---------------------------------------------------------------------------


def synthetic_runner(task: Mapping[str, Any], *, attempt_idx: int, round_idx: int) -> AttemptOutcome:
    """Deterministic fake attempt, for exercising the pipeline with no API.

    Outcomes are a hash of the task id, not a model: any pass rate this produces
    is meaningless and every rendering built from it carries a DRY RUN banner.
    """
    task_id = str(task["task_id"])
    digest = sum(ord(char) for char in task_id)
    passed = (digest + attempt_idx) % 3 != 0
    prompt = 8000 + digest % 2000
    cached = int(prompt * 0.6) if attempt_idx else 0
    return AttemptOutcome(
        passed=passed,
        cost=AttemptCost(
            task_id=task_id,
            round_idx=round_idx,
            variant_id=None,
            role="task_agent",
            input_tokens=prompt - cached,
            cached_input_tokens=cached,
            output_tokens=1200 + digest % 500,
            tool_calls=3,
        ),
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET, help="webthinker-schema task file")
    parser.add_argument("--size", type=int, default=6, help="sample size; 6 = 2/3/1, 24 = 8/12/4 (SPEC §6.8)")
    parser.add_argument("--k", type=int, default=2, help="rollouts per task (pass@k, §6.1)")
    parser.add_argument("--task-model", default=DEFAULT_PRICING["task_agent"].model)
    parser.add_argument("--meta-model", default=DEFAULT_PRICING["meta_agent"].model)
    parser.add_argument("--pricing-from-litellm", action="store_true", help="price from litellm's cost map")
    parser.add_argument("--lineages", type=int, default=M0_SCALE["lineages"])
    parser.add_argument("--tasks", type=int, default=M0_SCALE["tasks"])
    parser.add_argument("--rounds", type=int, default=M0_SCALE["rounds"])
    parser.add_argument("--floor", type=float, default=None, help="pass@k the run must clear (D.5 capability floor)")
    parser.add_argument("--out", type=Path, default=None, help="write the markdown report here")
    parser.add_argument("--json-out", type=Path, default=None, help="write the machine-readable summary here")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="use the synthetic runner: exercises the pipeline, produces no evidence",
    )
    return parser


def resolve_pricing(args: argparse.Namespace) -> dict[str, Pricing]:
    if args.pricing_from_litellm:
        return {
            "task_agent": Pricing.from_litellm(args.task_model),
            "meta_agent": Pricing.from_litellm(args.meta_model),
        }
    table = {price.model: price for price in (Pricing.deepseek_v4_flash(), Pricing.deepseek_v4_pro())}
    missing = [model for model in (args.task_model, args.meta_model) if model not in table]
    if missing:
        raise SystemExit(f"no built-in price for {missing}; pass --pricing-from-litellm")
    return {"task_agent": table[args.task_model], "meta_agent": table[args.meta_model]}


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    pricing = resolve_pricing(args)

    tasks = load_tasks(args.dataset)
    sample = stratified_sample(tasks, args.size)
    print(f"sampled {len(sample)} of {len(tasks)} tasks; by level {level_counts(sample)}")

    runner: AttemptRunner = synthetic_runner if args.dry_run else harness_runner
    calibration = run_calibration(sample, runner, k=args.k, pricing=pricing)
    calibration.dataset = DatasetSpec.from_file(args.dataset)
    calibration.synthetic = args.dry_run

    scale = {"lineages": args.lineages, "tasks": args.tasks, "rounds": args.rounds, "k": args.k}
    text = render(calibration, scale=scale, floor=args.floor)
    print(text)

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(
            json.dumps(summary(calibration, scale=scale), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
