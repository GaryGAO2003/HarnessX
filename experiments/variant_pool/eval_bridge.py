"""Evaluation bridge — GATED shadow candidate → runnable config → measured数值.

The shadow kernel (:mod:`experiments.variant_pool.shadow_evolution`) writes only
a ledger: a ``GATED`` :class:`CandidateRecord` carries ``operator`` +
``operator_params`` + ``genotype_hash``, but never a config file (回滚策略: P5
只写 shadow ledger).  Nothing in the kernel can turn that record back into
something a harness can run — that materialization gap is exactly what this
bridge fills.

Restoration chain (record → runnable YAML)::

    parse_proposal({"operator": rec.operator, "params": rec.operator_params})
      → apply_operator(parent_snapshot, op, materialize=True)
      → graph_to_config_dict(snapshot)
      → HarnessConfig(processors=...).to_yaml_file(out/<cid>/config.yaml)

The reconstructed config re-graphs to the SAME ``genotype_hash`` the gate
recorded — the bridge never invents a new genotype, it re-derives the one the
gate already blessed.

Evaluation (:class:`GaiaTaskBed`) reuses the recipe's own thin rollout surface —
``_prepare_round_config`` (fail-closed config load), ``_rollout_once`` and
``_run_task_pass_k`` — WITHOUT instantiating the full recipe.  Every ``recipe.*``
import is deferred to :meth:`GaiaTaskBed.evaluate`; importing this module never
drags in the 9000-line ``run_variant_pool`` module or its ``sys.path`` / ``.env``
side effects, so zero-API tests and the graph regression never pay that cost.

Division of labor — this bridge produces *measured evidence* only.  It never
calls :func:`record_evaluation`: the APPLY / FORK / REJECT decision is a
selection-policy concern that belongs to the runner, which feeds
:func:`measured_from_result` output into ``record_evaluation`` itself.  Keeping
the promotion decision out of the bridge is what preserves the 上线门禁 boundary
(only the runner's deterministic policy writes APPLY/FORK).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from harnessx.core.harness import HarnessConfig
from harnessx.graph.operators import apply_operator
from harnessx.graph.transform import graph_to_config_dict

from experiments.variant_pool.shadow_evolution import parse_proposal

if TYPE_CHECKING:  # annotations only — never imported at runtime
    from experiments.variant_pool.shadow_evolution import CandidateRecord
    from harnessx.graph.types import GraphSnapshot


# ── path safety ──────────────────────────────────────────────────────────────

#: Characters illegal in a single Windows path component. candidate_id is
#: ``<round_id>/c<i>`` (e.g. ``r1/c0``), so '/' is the one that always bites.
_FS_UNSAFE = str.maketrans({c: "_" for c in '<>:"/\\|?*'})


def _fs_safe(name: str) -> str:
    """Make *name* usable as one path component (``r1/c0`` → ``r1_c0``)."""
    return name.translate(_FS_UNSAFE)


# ── materialization: GATED record → runnable config.yaml ─────────────────────


def materialize_candidate(
    parent_snapshot: "GraphSnapshot",
    record: "CandidateRecord",
    out_dir: Path,
) -> Path:
    """Rebuild a GATED candidate's runnable config from its ledger record.

    Re-runs the operator against *parent_snapshot* and serializes the resulting
    graph to ``out_dir/<candidate_id>/config.yaml`` (candidate_id path-sanitized).
    The written config re-graphs to ``record.genotype_hash`` — the gate's own
    genotype, not a fresh one.

    Raises ``ValueError`` if *record* is not a GATED gate row (an evaluation
    decision, a REJECT, or a never-parsed proposal cannot be materialized).
    """
    if getattr(record, "decision", "") != "GATED":
        raise ValueError(
            f"only GATED candidates materialize; {record.candidate_id!r} has "
            f"decision {record.decision!r}"
        )

    op, err = parse_proposal(
        {"operator": record.operator, "params": dict(record.operator_params)}
    )
    if op is None:
        # A GATED row was parsed once already; a failure here signals a corrupt
        # or hand-edited ledger rather than a normal path.
        raise ValueError(
            f"candidate {record.candidate_id!r} did not re-parse from its "
            f"recorded operator/params: {err}"
        )

    snapshot, report = apply_operator(parent_snapshot, op, materialize=True)
    if snapshot is None:
        reasons = "; ".join(i.message for i in report.issues) or "unknown"
        raise ValueError(
            f"candidate {record.candidate_id!r} did not re-apply against the "
            f"supplied parent snapshot: {reasons}"
        )

    config_dict = graph_to_config_dict(snapshot)
    config = HarnessConfig(processors=config_dict.get("processors", []))

    out_path = Path(out_dir) / _fs_safe(record.candidate_id) / "config.yaml"
    config.to_yaml_file(out_path)
    return out_path


# ── task-bed result + protocol ───────────────────────────────────────────────


@dataclass
class TaskBedResult:
    """Outcome of evaluating one config on a task subset.

    ``per_task`` maps task_id → ``{passed, n_pass, n_att, cost_usd, tokens}``;
    the remaining fields are the round-level aggregates.  ``infra_failed`` is
    set only when the config itself would not load (fail-closed): in that case
    ``per_task`` is empty and ``error`` carries the load failure text.
    """

    per_task: "dict[str, dict]" = field(default_factory=dict)
    pass_rate: float = 0.0
    total_cost_usd: float = 0.0
    total_tokens: int = 0
    infra_failed: bool = False
    error: str = ""


class TaskBed(Protocol):
    """A thing that scores a materialized config on a (subset of a) task set."""

    async def evaluate(
        self, config_path: Path, task_ids: "list[str] | None" = None
    ) -> TaskBedResult: ...


def _aggregate_per_task(per_task: "dict[str, dict]") -> "tuple[float, float, int]":
    """Fold per-task rows into (pass_rate, total_cost_usd, total_tokens).

    ``pass_rate`` is the fraction of tasks solved (``passed`` truthy) — the
    binary per-task signal, mean-pooled — not an unbiased pass@k estimate; the
    latter is a reporting concern the runner owns.
    """
    n = len(per_task)
    n_solved = sum(1 for r in per_task.values() if r.get("passed"))
    pass_rate = (n_solved / n) if n else 0.0
    cost = sum(float(r.get("cost_usd") or 0.0) for r in per_task.values())
    tokens = sum(int(r.get("tokens") or 0) for r in per_task.values())
    return pass_rate, cost, tokens


# ── stub bed (dry-run / tests — zero IO, zero network) ───────────────────────


class StubTaskBed:
    """A :class:`TaskBed` that returns canned numbers — no IO, no model calls.

    Give it either ``results`` (task_id → row dict, rows shaped like
    :attr:`TaskBedResult.per_task` values) for full control, or a fixed
    ``pass_rate`` when only the aggregate matters (rehearsal / dry runs).
    """

    def __init__(
        self,
        *,
        results: "dict[str, dict] | None" = None,
        pass_rate: "float | None" = None,
    ) -> None:
        if results is None and pass_rate is None:
            raise ValueError("StubTaskBed needs either results= or pass_rate=")
        self._results = results
        self._pass_rate = pass_rate

    async def evaluate(
        self, config_path: Path, task_ids: "list[str] | None" = None
    ) -> TaskBedResult:
        if self._results is not None:
            per_task = {tid: dict(row) for tid, row in self._results.items()}
            if task_ids is not None:
                per_task = {t: per_task[t] for t in task_ids if t in per_task}
            pass_rate, cost, tokens = _aggregate_per_task(per_task)
            return TaskBedResult(
                per_task=per_task,
                pass_rate=pass_rate,
                total_cost_usd=cost,
                total_tokens=tokens,
            )
        return TaskBedResult(per_task={}, pass_rate=float(self._pass_rate or 0.0))


# ── real bed (GAIA subset) ───────────────────────────────────────────────────


class GaiaTaskBed:
    """Evaluate a materialized config on a GAIA task subset via the recipe.

    All ``recipe.*`` / ``benchmarks.*`` imports happen inside
    :meth:`evaluate`, so constructing (or importing) this class never triggers
    the recipe's module-level ``sys.path`` / ``.env`` side effects.
    """

    def __init__(
        self,
        model: str,
        meta_model: str,
        provider_id: str,
        data_path: Path,
        *,
        pass_k: int = 1,
        max_cost: float,
        max_steps: int,
        concurrency: int = 2,
        out_dir: Path,
    ) -> None:
        self._model = model
        self._meta_model = meta_model
        self._provider_id = provider_id
        self._data_path = Path(data_path)
        self._pass_k = pass_k
        self._max_cost = max_cost
        self._max_steps = max_steps
        self._concurrency = concurrency
        self._out_dir = Path(out_dir)

    async def evaluate(
        self, config_path: Path, task_ids: "list[str] | None" = None
    ) -> TaskBedResult:
        # Lazy imports: the recipe surface (and its import side effects) is only
        # paid for on a real evaluation, never at module import time.
        import dataclasses

        from benchmarks.gaia.evaluator import GAIAPipelineEvaluator
        from benchmarks.gaia.task import load_gaia_tasks_from_json
        from harnessx.core.model_config import ModelConfig
        from recipe.gaia_evolver.run import (
            _make_provider,
            _rollout_once,
            _run_task_pass_k,
        )
        from recipe.gaia_evolver.run_variant_pool import _prepare_round_config

        # Fail-closed config load: a config the variant is *defined by* that will
        # not open is infra failure, not a task failure — return, do not crash.
        try:
            round_config = _prepare_round_config(Path(config_path), None)
        except (FileNotFoundError, ValueError, RuntimeError) as exc:
            return TaskBedResult(
                per_task={},
                pass_rate=0.0,
                total_cost_usd=0.0,
                total_tokens=0,
                infra_failed=True,
                error=str(exc),
            )

        provider = _make_provider(self._model, self._provider_id)
        model_config = ModelConfig(main=provider)
        # Judge runs on --meta-model, mirroring run.py. With full ground truth,
        # scoring is deterministic string matching and never calls the judge.
        judge_provider = _make_provider(self._meta_model, self._provider_id)
        pipeline_eval = GAIAPipelineEvaluator(judge_provider=judge_provider)

        tasks = load_gaia_tasks_from_json(str(self._data_path))
        if task_ids is not None:
            wanted = set(task_ids)
            tasks = [t for t in tasks if (t.task_id or "") in wanted]
        # The JSON loader assigns a per-level default max_steps; pin it to this
        # bed's value so the step budget is deterministic across the subset.
        tasks = [dataclasses.replace(t, max_steps=self._max_steps) for t in tasks]

        sem = asyncio.Semaphore(max(1, self._concurrency))

        async def _one(task: Any) -> "tuple[str, dict]":
            async def _rollout(tk: Any, attempt_idx: int) -> dict:
                return await _rollout_once(
                    tk,
                    attempt_idx,
                    label="taskbed",
                    model_config=model_config,
                    round_config=round_config,
                    pipeline_eval=pipeline_eval,
                    max_cost=self._max_cost,
                )

            merged = await _run_task_pass_k(
                task, pass_k=self._pass_k, sem=sem, rollout=_rollout
            )
            return (task.task_id or "?"), merged

        pairs = await asyncio.gather(*(_one(t) for t in tasks))

        per_task: "dict[str, dict]" = {}
        for tid, merged in pairs:
            per_task[tid] = {
                "passed": bool(merged.get("passed")),
                "n_pass": int(merged.get("n_pass") or 0),
                "n_att": int(merged.get("n_att") or 0),
                "cost_usd": float(merged.get("cost_usd") or 0.0),
                "tokens": int(merged.get("total_tokens") or 0),
            }

        pass_rate, cost, tokens = _aggregate_per_task(per_task)
        return TaskBedResult(
            per_task=per_task,
            pass_rate=pass_rate,
            total_cost_usd=cost,
            total_tokens=tokens,
        )


# ── measured evidence (fed to record_evaluation by the runner) ───────────────


def measured_from_result(result: TaskBedResult) -> dict:
    """Project a :class:`TaskBedResult` into a ``measured`` evidence dict.

    Every value is a plain numeric (``int`` / ``float``, never ``bool``), which
    is what :func:`experiments.variant_pool.shadow_evolution.record_evaluation`
    requires — advisory text alone can never promote a candidate.  ``infra_failed``
    contributes an explicit ``1`` flag so a load-failed evaluation is still a
    valid (all-zero) numeric record the runner can reject on.
    """
    measured = {
        "pass_rate": float(result.pass_rate),
        "n_tasks": int(len(result.per_task)),
        "cost_usd": float(result.total_cost_usd),
        "tokens": int(result.total_tokens),
    }
    if result.infra_failed:
        measured["infra_failed"] = 1
    return measured
