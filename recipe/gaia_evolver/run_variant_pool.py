# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""C2 — the variant pool's first contact with the real GAIA loop.

This is a **parallel recipe** to :mod:`recipe.gaia_evolver.run`: it does not
rewrite ``run.py``'s inline main loop (SPEC §8.2 — the baseline still runs and
pass@2 was just changed there). It imports ``run.py``'s verified parts
(``_run_task_pass_k``, ``_rollout_once`` and the trajectory helpers), rebuilds
the ``rollout`` / ``finalize`` closures ``run.py``'s ``main`` constructs inline,
and lets the C1 :class:`~experiments.variant_pool.engine.VariantPoolEngine`
orchestrate them against real ``meta_agent.evolve`` evolutions.

What C1 left as injected callbacks, this fills in
------------------------------------------------
* ``evolve(variant, round_idx) -> candidate | None`` — round 0 (or a variant's
  first appearance) returns a *baseline candidate* wrapping the variant's own
  config, with **no** meta-agent call, because ``meta_agent.evolve`` needs a
  populated ``trajectories_dir`` that does not exist yet. Every later round calls
  ``meta_agent.evolve(current_config=<held config>, trajectories_dir=<the
  variant's last evaluation>, ...)`` and wraps the returned YAML in a candidate
  carrying ``target_variant``. A byte-identical output is the meta-agent's
  explicit no-op idiom (``run.py`` line ~1304) and returns ``None`` so the
  variant contributes only to the idle counter.
* ``evaluate(candidate, T_k, round_idx) -> {task: (n_pass, n_att)}`` — loads the
  candidate's config into a harness (``model_config.agentic(round_config)``),
  runs ``_run_task_pass_k`` for every task in ``T_k`` under a fresh per-round
  semaphore, and reads ``n_pass`` / ``n_att`` straight off each merged record.

Config lineage across rounds (the K=1 single-lineage geometry, SPEC §8.1)
-------------------------------------------------------------------------
The engine's ``_apply_candidate`` is a no-op in C1 (real config merging was
explicitly deferred to batch C). Rather than reopen C1, this recipe reconciles
each round *after* :meth:`VariantPoolEngine.run_round` returns, using the
:class:`~experiments.variant_pool.engine.RoundResult`:

* **APPLY** — the variant now embodies the candidate, so its ``config_path`` and
  its "last evaluated trajectories" advance to the candidate's.
* **FORK** — the new child embodies the candidate (the improved edit); the
  parent keeps its old config. ``pool.fork`` cloned the *parent's* config into
  the child's slot, so the child's ``config_path`` is repointed at the
  candidate's YAML here.
* **REJECT** — the variant is untouched: neither its config nor its held
  trajectories move (the engine already declined to record the rejected results
  in the ledger).
* **baseline** — the round-0 baseline "adopts" the variant's own config, so the
  config never moves; its trajectories are seeded regardless of the gate, which
  is what lets round 1 evolve even if the baseline solved nothing.

With ``--pool-k 1`` the pool holds only V0, routing is always V0, no fork can
ever happen (the engine downgrades an impossible fork of a full one-variant pool
to REJECT), and each round is exactly "evolve V0 once from its last run, evaluate
once on all tasks, gate". That is the paper's Global arm; ``--pool-k 8`` is the
Ensemble arm, same code path, one variable changed (SPEC §8.1).

Async bridging
--------------
``run.py``'s ``main`` is a coroutine driven by one event loop. The C1 engine is
synchronous and calls ``evolve`` / ``evaluate`` synchronously, so this recipe
owns one persistent loop and bridges every coroutine (``meta_agent.evolve`` and
the per-round ``asyncio.gather`` of rollouts) through ``run_until_complete``. The
loop is never running when a callback fires, so the bridge is safe.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Importing run.py runs its module-level setup (project root onto sys.path,
# .env load, LiteLLM quieting) and gives us its verified, unchanged parts.
from .defaults import (
    DEFAULT_CONCURRENCY,
    DEFAULT_META_MODEL,
    DEFAULT_MODEL,
    DEFAULT_PROVIDER_ID,
    EVOLVE_COST_CAP_USD,
    EVOLVE_MAX_STEPS,
    EVOLVE_WALL_CLOCK_S,
    MAX_COST_USD,
    MAX_STEPS,
    MAX_TASKS,
    NUM_ROUNDS,
)
from .run import (
    RUNS_DIR,
    _GAIA_SKILLS_DIR,
    _build_trajectory_text,
    _compute_tool_counts,
    _compute_tool_stats,
    _make_provider,
    _pick_pivotal_tool,
    _rollout_once,
    _run_task_pass_k,
    _write_task_trajectory,
)

# Repo parts (same sources run.py imports from).
from harnessx.core.model_config import ModelConfig
from harnessx.meta_harness import MetaAgent

from benchmarks.gaia.evaluator import GAIAPipelineEvaluator
from benchmarks.gaia.harness import make_gaia_builder_gpt5
from benchmarks.gaia.task import GAIATask, load_gaia_tasks, load_gaia_tasks_from_json

# The C1 engine and its offline components (SPEC stage A/C1).
from experiments.variant_pool.engine import DEFAULT_MIN_FORK, DEFAULT_PATIENCE, RoundResult, VariantPoolEngine
from experiments.variant_pool.evidence import EvidenceStore
from experiments.variant_pool.experiment_lock import (
    DatasetSpec,
    EnvSpec,
    ExperimentLock,
    H0Freeze,
    Hyperparams,
    ModelSpec,
    sha256_file,
)
from experiments.variant_pool.gate import Decision
from experiments.variant_pool.ledger import SuccessLedger
from experiments.variant_pool.pool import VariantPool
from experiments.variant_pool.reporting import RunReport, TaskResult
from experiments.variant_pool.router import Router

logger = logging.getLogger("gaia_evolver.variant_pool")


# ---------------------------------------------------------------------------
# Injectable seams (module level so tests can monkeypatch them offline)
# ---------------------------------------------------------------------------


def _make_journal(sessions_dir: Path):
    """The per-(variant, round) tracer, isolated like ``run.py``'s round journal."""
    from harnessx.tracing.journal import HarnessJournal

    return HarnessJournal(base_dir=str(sessions_dir), export_jsonl=True)


def _prepare_round_config(config_path: Path, journal: Any):
    """Load a config YAML and attach this round's tracer (``run.py`` idiom).

    Mirrors ``run.py``: ``HarnessConfig.from_yaml_file(...).canonicalize()`` then
    ``.copy(tracer=...)``. Kept as a module-level function so the unit tests can
    replace it without a real ``HarnessConfig`` (they never run a rollout).
    """
    from harnessx.core.harness import HarnessConfig

    cfg = HarnessConfig.from_yaml_file(config_path).canonicalize()
    return cfg.copy(tracer=journal)


@dataclass
class PoolCandidate:
    """One round's proposed config for one variant.

    Deliberately **not** a :class:`~experiments.variant_pool.manifest.ChangeManifest`
    so the C1 gate treats it opaquely and the decision is the pure seesaw on
    ``T_k`` (SPEC §8.4 step 3); wiring the manifest / canonicalize / smoke stages
    into the gate is batch C4. Carries ``target_variant`` (the variant this
    candidate is for) and ``candidate_id`` (read by the engine when archiving a
    rejection). ``is_baseline`` marks a round-0 "adopt my own config" candidate,
    which produced no meta-agent call.
    """

    candidate_id: str
    target_variant: str
    config_path: Path
    is_baseline: bool = False


# ---------------------------------------------------------------------------
# The recipe
# ---------------------------------------------------------------------------


class VariantPoolRecipe:
    """Drives the C1 engine against real rollouts and real ``meta_agent.evolve``.

    The engine owns the loop shape (route -> evolve -> evaluate -> gate ->
    fork/apply/retire -> record); this class owns the two callbacks, the config
    lineage across rounds, and the on-disk artefacts.
    """

    def __init__(
        self,
        *,
        args: Any,
        tasks: list[GAIATask],
        model_config: Any,
        meta_agent: Any,
        pipeline_eval: Any,
        run_dir: str | Path,
        baseline_config_path: str | Path,
        patience: int = DEFAULT_PATIENCE,
        min_fork: tuple[int, int] = DEFAULT_MIN_FORK,
        loop: asyncio.AbstractEventLoop | None = None,
    ) -> None:
        self.args = args
        self.tasks = list(tasks)
        self.tasks_by_id: dict[str, GAIATask] = {t.task_id: t for t in tasks if t.task_id}
        self.model_config = model_config
        self.meta_agent = meta_agent
        self.pipeline_eval = pipeline_eval
        self.run_dir = Path(run_dir)
        self.baseline_config_path = Path(baseline_config_path)
        self._owns_loop = loop is None
        self._loop = loop or asyncio.new_event_loop()

        # Per-variant lineage: the config a variant currently holds is its
        # pool ``config_path``; ``_last_traj_dir`` is where that config was last
        # evaluated (what the next evolve reads).
        self._last_traj_dir: dict[str, Path] = {}
        # Reset each round; populated by the callbacks, consumed by reconcile.
        self._round_candidates: dict[str, PoolCandidate | None] = {}
        self._round_traj_dir: dict[str, Path] = {}
        self._round_records: dict[str, dict[str, dict]] = {}

        all_ids = {t.task_id for t in self.tasks if t.task_id}
        self.level_map: dict[str, int] = {t.task_id: int(t.level) for t in self.tasks if t.task_id}

        self.pool = VariantPool(K=int(args.pool_k))
        self.pool.add_root(self.baseline_config_path, self.run_dir / "learnings.md", tasks=all_ids)
        self.ledger = SuccessLedger()
        self.router = Router(cluster_mode="routed")
        self.evidence = EvidenceStore(self.run_dir)
        self.report = RunReport(run_name=str(getattr(args, "run_tag", "") or ""), k=int(args.pass_k))
        self.round_summaries: list[dict] = []
        self.pool_states: list[dict] = []
        # round_idx -> run.py-compatible per-task records (for comparison.json).
        self._comparison_rounds: dict[int, list[dict]] = {}

        self.engine = VariantPoolEngine(
            self.pool,
            self.ledger,
            self.router,
            evaluate=self._evaluate,
            evolve=self._evolve,
            evidence=self.evidence,
            patience=patience,
            min_fork=min_fork,
        )

    # ------------------------------------------------------------------
    # driver
    # ------------------------------------------------------------------

    def run(self) -> list[RoundResult]:
        """Run up to ``num_rounds`` rounds, reconciling config lineage between them."""
        all_ids = {t.task_id for t in self.tasks if t.task_id}
        results: list[RoundResult] = []
        for round_idx in range(int(self.args.num_rounds)):
            self._round_candidates = {}
            self._round_traj_dir = {}
            self._round_records = {}

            result = self.engine.run_round(round_idx, set(all_ids))
            self._reconcile(result)
            self._ingest_report(result, round_idx)
            self._dump_round(result, round_idx)
            results.append(result)

            if self.engine.idle >= self.engine.patience:
                logger.info("[R%d] idle=%d >= patience=%d — early stop", round_idx, self.engine.idle, self.engine.patience)
                break

        self._dump_final()
        return results

    # ------------------------------------------------------------------
    # callback: evolve
    # ------------------------------------------------------------------

    def _evolve(self, variant: Any, round_idx: int) -> PoolCandidate | None:
        """Produce this round's candidate config for ``variant`` (or ``None``)."""
        vid = variant.variant_id

        # A variant with no prior evaluation cannot be evolved (meta_agent.evolve
        # needs trajectories). Adopt its own config as the round's candidate.
        if vid not in self._last_traj_dir:
            cand = PoolCandidate(
                candidate_id=f"C-R{round_idx}-{vid}",
                target_variant=vid,
                config_path=Path(variant.config_path),
                is_baseline=True,
            )
            self._round_candidates[vid] = cand
            return cand

        evolve_dir = self.run_dir / f"R{round_idx}" / vid / "evolve"
        evolve_dir.mkdir(parents=True, exist_ok=True)
        logger.info("[R%d] %s evolve -> %s", round_idx, vid, evolve_dir)

        new_yaml = self._await(
            self.meta_agent.evolve(
                current_config=Path(variant.config_path),
                trajectories_dir=self._last_traj_dir[vid],
                output_dir=evolve_dir,
                replay_model=self.model_config,
                replay_max_cost_usd=min(0.5, float(self.args.max_cost)),
            )
        )
        new_yaml = Path(new_yaml)

        # Byte-identical output == the meta-agent's explicit no-op idiom
        # (run.py ~line 1304). No candidate this round -> idle only.
        if Path(variant.config_path).read_bytes() == new_yaml.read_bytes():
            logger.info("[R%d] %s evolve = no-op (byte-identical)", round_idx, vid)
            self._round_candidates[vid] = None
            return None

        cand = PoolCandidate(
            candidate_id=f"C-R{round_idx}-{vid}",
            target_variant=vid,
            config_path=new_yaml,
            is_baseline=False,
        )
        self._round_candidates[vid] = cand
        return cand

    # ------------------------------------------------------------------
    # callback: evaluate
    # ------------------------------------------------------------------

    def _evaluate(self, candidate: Any, t_k: set[str], round_idx: int) -> dict[str, tuple[int, int]]:
        """Evaluate the candidate on ``T_k`` (§4.5 narrowed evaluation)."""
        return self._await(self._run_evaluation(candidate, set(t_k), round_idx))

    async def _run_evaluation(self, candidate: Any, t_k: set[str], round_idx: int) -> dict[str, tuple[int, int]]:
        vid = candidate.target_variant
        vround_dir = self.run_dir / f"R{round_idx}" / vid
        traj_dir = vround_dir / "trajectories"
        traj_dir.mkdir(parents=True, exist_ok=True)
        sessions_dir = vround_dir / "sessions"

        journal = _make_journal(sessions_dir)
        round_config = _prepare_round_config(Path(candidate.config_path), journal)
        try:
            round_config.to_yaml_file(vround_dir / "config.yaml")
        except Exception as exc:  # noqa: BLE001 - best-effort reproducibility dump
            logger.debug("[R%d] %s config dump skipped: %s", round_idx, vid, exc)

        label = f"R{round_idx}-{vid}"
        sem = asyncio.Semaphore(max(1, int(self.args.concurrency)))

        async def _rollout(task: GAIATask, attempt_idx: int) -> dict:
            return await _rollout_once(
                task,
                attempt_idx,
                label=label,
                model_config=self.model_config,
                round_config=round_config,
                pipeline_eval=self.pipeline_eval,
                max_cost=float(self.args.max_cost),
            )

        async def _finalize(task: GAIATask, attempt_idx: int, record: dict) -> dict:
            return await self._finalize_attempt(
                task,
                attempt_idx,
                record,
                traj_dir=traj_dir,
                round_config=round_config,
                round_idx=round_idx,
                variant_id=vid,
            )

        ordered = sorted(t_k)
        records = list(
            await asyncio.gather(
                *(
                    _run_task_pass_k(
                        self.tasks_by_id[tid],
                        pass_k=int(self.args.pass_k),
                        sem=sem,
                        rollout=_rollout,
                        finalize=_finalize,
                    )
                    for tid in ordered
                )
            )
        )

        self._round_traj_dir[vid] = traj_dir
        outcomes: dict[str, tuple[int, int]] = {}
        cleaned: dict[str, dict] = {}
        for tid, record in zip(ordered, records):
            n_pass = int(record.get("n_pass") or 0)
            n_att = int(record.get("n_att") or 0)
            outcomes[tid] = (n_pass, n_att)
            cleaned[tid] = self._clean_record(record, variant_id=vid, round_idx=round_idx)
        self._round_records[vid] = cleaned
        return outcomes

    async def _finalize_attempt(
        self,
        task: GAIATask,
        attempt_idx: int,
        record: dict,
        *,
        traj_dir: Path,
        round_config: Any,
        round_idx: int,
        variant_id: str,
    ) -> dict:
        """Mirror ``run.py``'s inline ``_finalize_attempt`` closure per variant.

        Same behaviour as ``run.py`` (judge verdict lookup, trajectory write),
        only the trajectory path is namespaced under the variant's subdir.
        """
        harness = record.pop("_harness", None)
        raw = record.get("_result")
        tid = record.get("task_id") or "unknown"
        traj_name = f"{tid}.md" if attempt_idx == 0 else f"{tid}.a{attempt_idx + 1}.md"
        record["trajectory_file"] = f"R{round_idx}/{variant_id}/trajectories/{traj_name}"

        judge_entry: dict = {}
        if not getattr(self.args, "no_judge", False) and harness is not None:
            from harnessx.processors.evaluation.llm_judge import LLMJudgeProcessor as _LJP

            run_id = getattr(raw, "run_id", "") or "" if raw is not None else ""
            for _proc in harness._rt.processors.get("*", []):
                if isinstance(_proc, _LJP):
                    judge_entry = _proc.get_verdict(run_id) or {}
                    break

        record["extracted_answer"] = judge_entry.get("extracted_answer") or ""
        record["llm_judge_verdict"] = judge_entry.get("verdict") or {}

        if raw is not None:
            record["pivotal_tool"] = _pick_pivotal_tool(raw)
            call_counts, error_counts = _compute_tool_counts(raw)
            record["tool_call_counts"] = call_counts
            record["tool_error_counts"] = error_counts
            _, err_count = _compute_tool_stats(raw)
            record["error_count"] = err_count
            traj_text = _build_trajectory_text(task, raw, harness_config=round_config)
            _write_task_trajectory(traj_dir, task, traj_text, record=record, filename=traj_name)
        return record

    # ------------------------------------------------------------------
    # config lineage reconciliation (post-round)
    # ------------------------------------------------------------------

    def _reconcile(self, result: RoundResult) -> None:
        """Advance each variant's held config after the gate decided (see module doc)."""
        for retired_id in result.retired:
            self._last_traj_dir.pop(retired_id, None)

        for vid, cand in self._round_candidates.items():
            if cand is None:
                continue
            traj_dir = self._round_traj_dir.get(vid)
            if traj_dir is None:
                continue
            decision = result.decisions.get(vid)
            if cand.is_baseline:
                # Baseline adopts the variant's own config: seed trajectories so
                # the next round can evolve, whatever the gate said.
                self._last_traj_dir[vid] = traj_dir
            elif decision is Decision.APPLY:
                self.pool.variants[vid].config_path = Path(cand.config_path)
                self._last_traj_dir[vid] = traj_dir
            # FORK -> handled below; REJECT / None -> the variant is untouched.

        for child_id in result.forked:
            child = self.pool.variants.get(child_id)
            if child is None:
                continue
            parent_id = child.parent_id
            cand = self._round_candidates.get(parent_id) if parent_id else None
            traj_dir = self._round_traj_dir.get(parent_id) if parent_id else None
            if cand is not None:
                # The child embodies the improved edit, not the parent's config
                # (pool.fork cloned the parent's).
                child.config_path = Path(cand.config_path)
            if traj_dir is not None:
                self._last_traj_dir[child_id] = traj_dir

    # ------------------------------------------------------------------
    # reporting / artefacts
    # ------------------------------------------------------------------

    def _ingest_report(self, result: RoundResult, round_idx: int) -> None:
        """Fold this round's measured outcomes and pool events into the report."""
        comp_records: list[dict] = []
        for vid, outcomes in result.per_variant_pass.items():
            records = self._round_records.get(vid, {})
            for task_id, (n_pass, n_att) in outcomes.items():
                infra = int((records.get(task_id) or {}).get("infra_failures") or 0)
                infra = max(0, min(infra, n_att - n_pass))
                self.report.add(
                    TaskResult(
                        task_id=task_id,
                        round_idx=round_idx,
                        n_att=n_att,
                        n_pass=n_pass,
                        variant_id=vid,
                        infra_failures=infra,
                    )
                )
                # run.py-compatible comparison record: keep the cost/token/step
                # fields oracle_ceiling.py reads, add the variant/pass counts.
                rec = dict(records.get(task_id, {}))
                rec.setdefault("task_id", task_id)
                rec.setdefault("level", self.level_map.get(task_id, 0))
                rec["variant_id"] = vid
                rec["round"] = round_idx
                rec["n_pass"] = n_pass
                rec["n_att"] = n_att
                rec["passed"] = n_pass >= 1
                comp_records.append(rec)
        self._comparison_rounds[round_idx] = comp_records
        for child_id in result.forked:
            self.report.add_event(round_idx=round_idx, kind="fork", variant_id=child_id)
        for retired_id in result.retired:
            self.report.add_event(round_idx=round_idx, kind="retire", variant_id=retired_id)

        passed = sum(1 for outcomes in result.per_variant_pass.values() for (np_, _) in outcomes.values() if np_ >= 1)
        n_tasks = sum(len(outcomes) for outcomes in result.per_variant_pass.values())
        self.round_summaries.append(
            {
                "round": round_idx,
                "variant_count": result.variant_count,
                "evaluated_tasks": n_tasks,
                "passed": passed,
                "shipped": result.shipped,
                "idle": result.idle,
                "decisions": {vid: dec.value for vid, dec in result.decisions.items()},
                "forked": list(result.forked),
                "retired": list(result.retired),
            }
        )

    def _dump_round(self, result: RoundResult, round_idx: int) -> None:
        """Per-round variant-pool snapshot (routing partition + events)."""
        state = {
            "round": round_idx,
            "variant_count": result.variant_count,
            "routing": {vid: sorted(v.routed_tasks) for vid, v in sorted(self.pool.variants.items())},
            "decisions": {vid: dec.value for vid, dec in result.decisions.items()},
            "forked": list(result.forked),
            "retired": list(result.retired),
            "shipped": result.shipped,
            "idle": result.idle,
            "per_variant_pass": {
                vid: {task: [np_, na_] for task, (np_, na_) in outcomes.items()}
                for vid, outcomes in result.per_variant_pass.items()
            },
        }
        self.pool_states.append(state)
        round_dir = self.run_dir / f"R{round_idx}"
        round_dir.mkdir(parents=True, exist_ok=True)
        (round_dir / "pool_state.json").write_text(
            json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    def _dump_final(self) -> None:
        """Write the RunReport (final+peak+curve+by-level), pool axis, comparison.json."""
        try:
            md = self.report.to_markdown(level_map=self.level_map)
            (self.run_dir / "pool_report.md").write_text(md, encoding="utf-8")
            (self.run_dir / "pool_report.json").write_text(
                self.report.to_json(level_map=self.level_map), encoding="utf-8"
            )
        except Exception as exc:  # noqa: BLE001 - a report failure must not lose the raw data
            logger.warning("pool report render failed (non-fatal): %s", exc)

        (self.run_dir / "pool_states.json").write_text(
            json.dumps(self.pool_states, indent=2, ensure_ascii=False), encoding="utf-8"
        )

        # comparison.json — run.py-compatible shape so analyze_run.py /
        # oracle_ceiling.py consume it unchanged: rounds[j] = one round's records.
        rounds_records = [self._comparison_rounds.get(s["round"], []) for s in self.pool_states]
        comparison = {
            "rounds": rounds_records,
            "round_summaries": self.round_summaries,
            "run_config": {
                "recipe": "variant_pool",
                "pool_k": int(self.args.pool_k),
                "model": getattr(self.args, "model", ""),
                "meta_model": getattr(self.args, "meta_model", ""),
                "max_tasks": getattr(self.args, "max_tasks", 0),
                "max_cost_usd": getattr(self.args, "max_cost", 0.0),
                "num_rounds": int(self.args.num_rounds),
                "pass_k": int(self.args.pass_k),
                "concurrency": int(self.args.concurrency),
                "run_dir": str(self.run_dir),
            },
        }
        (self.run_dir / "comparison.json").write_text(
            json.dumps(comparison, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        logger.info("Results -> %s", self.run_dir / "comparison.json")

    @staticmethod
    def _clean_record(record: dict, *, variant_id: str, round_idx: int) -> dict:
        """Strip private (``_``-prefixed) keys so the record is JSON-serialisable."""
        return {k: v for k, v in record.items() if not k.startswith("_")}

    # ------------------------------------------------------------------
    # async bridge / lifecycle
    # ------------------------------------------------------------------

    def _await(self, coro: Any) -> Any:
        return self._loop.run_until_complete(coro)

    def close(self) -> None:
        if self._owns_loop and not self._loop.is_closed():
            try:
                self._loop.run_until_complete(self._loop.shutdown_asyncgens())
            except Exception:  # noqa: BLE001 - shutdown best-effort
                pass
            self._loop.close()


# ---------------------------------------------------------------------------
# experiment lock (W26)
# ---------------------------------------------------------------------------


def _build_experiment_lock(
    *,
    args: Any,
    run_tag: str,
    baseline_config_path: Path,
    original_base: Any,
) -> ExperimentLock:
    """Best-effort ``experiment.lock.json`` recording what this run froze."""
    try:
        config_sha = sha256_file(baseline_config_path)
    except Exception:  # noqa: BLE001
        config_sha = ""
    tool_names: tuple[str, ...] = ()
    try:
        registry = getattr(original_base, "tool_registry", None)
        if registry is not None and hasattr(registry, "list_names"):
            tool_names = tuple(sorted(registry.list_names()))
    except Exception:  # noqa: BLE001
        tool_names = ()

    if args.data_path:
        try:
            dataset = DatasetSpec.from_file(args.data_path)
        except Exception:  # noqa: BLE001
            dataset = DatasetSpec(path=str(args.data_path), sha256="", size=0)
    else:
        dataset = DatasetSpec(path="", sha256="", size=0)

    return ExperimentLock(
        experiment_id=run_tag,
        created_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
        h0=H0Freeze(config_sha256=config_sha, system_prompt_sha256="", tool_registry=tool_names),
        models=ModelSpec(
            task_agent_model=args.model,
            meta_agent_model=args.meta_model,
            api_base=args.api_base or "",
            provider=args.provider_id,
        ),
        dataset=dataset,
        hyperparams=Hyperparams(
            K=int(args.pool_k),
            pass_at_k=int(args.pass_k),
            T=int(args.num_rounds),
            max_steps=int(args.max_steps),
            concurrency=int(args.concurrency),
        ),
        env=EnvSpec(),
    )


# ---------------------------------------------------------------------------
# setup + CLI + main
# ---------------------------------------------------------------------------


def build_arg_parser() -> argparse.ArgumentParser:
    """CLI mirroring ``run.py`` plus ``--pool-k`` (SPEC §8.1: K=1 Global, K=8 Ensemble)."""
    parser = argparse.ArgumentParser(description="GAIA Variant-Pool Evolver (parallel recipe to run.py)")
    parser.add_argument("--max-tasks", type=int, default=MAX_TASKS)
    parser.add_argument("--max-cost", type=float, default=MAX_COST_USD)
    parser.add_argument("--num-rounds", type=int, default=NUM_ROUNDS)
    parser.add_argument("--model", default=DEFAULT_MODEL, help="Inner (task-doing) agent model.")
    parser.add_argument("--meta-model", default=DEFAULT_META_MODEL, help="Meta-agent (evolve) model.")
    parser.add_argument("--provider-id", default=DEFAULT_PROVIDER_ID)
    parser.add_argument("--api-base", default=None, help="OpenAI-compatible endpoint for --model.")
    parser.add_argument("--api-key", default=None, help="API key paired with --api-base.")
    parser.add_argument("--clean", action="store_true", help="Wipe runs/<tag>/ before starting.")
    parser.add_argument("--no-judge", action="store_true", help="Disable LLMJudgeProcessor.")
    parser.add_argument("--evolve-cost", type=float, default=EVOLVE_COST_CAP_USD)
    parser.add_argument("--evolve-steps", type=int, default=EVOLVE_MAX_STEPS)
    parser.add_argument("--evolve-wall-clock", type=int, default=EVOLVE_WALL_CLOCK_S)
    parser.add_argument("--run-tag", default=None, help="Label for this run's runs/<tag>/ dir.")
    _default_data = str(Path(__file__).resolve().parent / "data" / "webthinker_gaia_dev.json")
    parser.add_argument("--data-path", default=_default_data, help="Local GAIA JSON (webthinker schema); '' = HF download.")
    parser.add_argument("--attachments-dir", default=None, help="Dir of per-task attachment files.")
    parser.add_argument("--level", type=int, default=0, help="GAIA level (1/2/3); 0 = all.")
    parser.add_argument("--max-steps", type=int, default=MAX_STEPS, help=f"Per-task step cap. Default: {MAX_STEPS}.")
    parser.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY, help="Max concurrent trajectories.")
    parser.add_argument("--pass-k", type=int, default=1, help="Independent rollouts per task per round (paper §6.1: pass@2).")
    parser.add_argument(
        "--pool-k",
        type=int,
        default=1,
        help=(
            "Variant-pool capacity K (SPEC §8.1). 1 = single lineage = paper's "
            "Global arm (never forks); 8 = Ensemble arm. Same code path, one "
            "variable. Default: 1."
        ),
    )
    return parser


def setup(args: Any, run_dir: Path) -> dict[str, Any]:
    """Build providers/models/evaluator/meta-agent/tasks + the frozen baseline config.

    Reuses ``run.py``'s construction sequence verbatim (SPEC §8.2). Returns the
    dependencies the recipe needs plus the ``ExperimentLock``.
    """
    import dataclasses as _dcs

    from harnessx.core.harness import _serialize_processor

    provider = _make_provider(args.model, args.provider_id, api_base=args.api_base, api_key=args.api_key)
    model_config = ModelConfig(main=provider)

    judge_provider = _make_provider(args.meta_model, args.provider_id)
    pipeline_eval = GAIAPipelineEvaluator(judge_provider=judge_provider)

    meta_provider = _make_provider(
        args.meta_model,
        args.provider_id,
        extended_thinking=True,
        thinking_budget_tokens=32_000,
        max_tokens=40_000,
    )
    meta_model = ModelConfig(main=meta_provider)

    level_filter = None if args.level == 0 else args.level
    max_tasks_filter = None if args.max_tasks <= 0 else args.max_tasks
    logger.info("Loading GAIA tasks (level=%s, max_tasks=%s)...", level_filter or "all", max_tasks_filter or "all")
    if args.data_path:
        tasks = load_gaia_tasks_from_json(
            args.data_path, level=level_filter, max_tasks=max_tasks_filter, attachments_dir=args.attachments_dir
        )
    else:
        tasks = load_gaia_tasks(level=level_filter, max_tasks=max_tasks_filter)
    if not tasks:
        raise SystemExit("No tasks loaded!")
    for t in tasks:
        t.max_steps = args.max_steps
    logger.info("Loaded %d tasks (max_steps=%d)", len(tasks), args.max_steps)

    original_base = make_gaia_builder_gpt5(max_cost_usd=args.max_cost).build()
    if not args.no_judge:
        from harnessx.processors.evaluation.llm_judge import LLMJudgeProcessor

        _judge_dict = _serialize_processor(LLMJudgeProcessor(judge_model=args.meta_model))
        if _judge_dict:
            original_base = _dcs.replace(original_base, processors=[*original_base.processors, _judge_dict])

    # Freeze the baseline (H0) to V0/config.yaml — the root variant's config.
    v0_dir = run_dir / "V0"
    v0_dir.mkdir(parents=True, exist_ok=True)
    baseline_config_path = v0_dir / "config.yaml"
    original_base.to_yaml_file(baseline_config_path)

    meta_agent = MetaAgent(
        inner_model=meta_model,
        memo_path=run_dir / "learnings.md",
        extra_skills_dirs=([_GAIA_SKILLS_DIR] if _GAIA_SKILLS_DIR.is_dir() else None),
        max_cost_usd=args.evolve_cost,
        wall_clock_s=float(args.evolve_wall_clock),
        max_steps=args.evolve_steps,
    )

    return {
        "model_config": model_config,
        "meta_agent": meta_agent,
        "pipeline_eval": pipeline_eval,
        "tasks": tasks,
        "baseline_config_path": baseline_config_path,
        "original_base": original_base,
    }


def main() -> None:
    args = build_arg_parser().parse_args()
    if args.pass_k < 1:
        raise SystemExit(f"--pass-k must be >= 1, got {args.pass_k}")
    if args.pool_k < 1:
        raise SystemExit(f"--pool-k must be >= 1, got {args.pool_k}")

    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    run_tag = args.run_tag or time.strftime("pool_%Y%m%d-%H%M%S")
    run_dir = RUNS_DIR / run_tag
    if args.clean and run_dir.exists():
        shutil.rmtree(run_dir)
        logger.info("Cleaned %s", run_dir)
    elif run_dir.exists() and any(run_dir.iterdir()):
        logger.warning("--run-tag %r already exists and is non-empty; output will interleave. Use --clean.", run_tag)
    run_dir.mkdir(parents=True, exist_ok=True)
    logger.info("Run outputs -> %s (pool_k=%d)", run_dir, args.pool_k)

    deps = setup(args, run_dir)

    lock = _build_experiment_lock(
        args=args,
        run_tag=run_tag,
        baseline_config_path=deps["baseline_config_path"],
        original_base=deps["original_base"],
    )
    lock.save(run_dir)

    recipe = VariantPoolRecipe(
        args=args,
        tasks=deps["tasks"],
        model_config=deps["model_config"],
        meta_agent=deps["meta_agent"],
        pipeline_eval=deps["pipeline_eval"],
        run_dir=run_dir,
        baseline_config_path=deps["baseline_config_path"],
    )
    recipe.report.lock_sha256 = lock.sha256()
    try:
        recipe.run()
    finally:
        recipe.close()


if __name__ == "__main__":
    import warnings

    warnings.filterwarnings("ignore", message=".*Event loop is closed.*")
    main()
