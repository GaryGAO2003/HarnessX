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
import copy
import json
import logging
import os
import shutil
import subprocess
import time
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Importing run.py runs its module-level setup (project root onto sys.path,
# .env load, LiteLLM quieting) and gives us its verified, unchanged parts.
from .defaults import (
    DEFAULT_META_MODEL,
    DEFAULT_MODEL,
    EVOLVE_COST_CAP_USD,
    EVOLVE_MAX_STEPS,
    EVOLVE_WALL_CLOCK_S,
    MAX_COST_USD,
    MAX_STEPS,
    MAX_TASKS,
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
from experiments.variant_pool.candidate_pipeline import (
    CandidateBrief,
    CandidatePipeline,
    CandidateSlot,
    DigesterRoundArtifact,
    IsolatedEvolverAdapter,
    OURS_ACTIONABILITY_THRESHOLD_PROVENANCE,
    PipelineContext,
    PipelineResult,
    PlanningArtifact,
)
from experiments.variant_pool.critic import DeterministicCritic
from experiments.variant_pool.evidence import EvidenceStore, RejectedCandidate, TaskDigest
from experiments.variant_pool.experiment_lock import (
    DatasetSpec,
    EnvSpec,
    ExperimentLock,
    H0Freeze,
    Hyperparams,
    ModelSpec,
    UNRESOLVED,
    sha256_file,
)
from experiments.variant_pool.gate import Decision
from experiments.variant_pool.ledger import SuccessLedger
from experiments.variant_pool.manifest import (
    CandidateArtifact,
    ChangeManifest,
    RepoJournalFormatError,
    adapt_repo_journal_manifest,
)
from experiments.variant_pool.pool import VariantPool
from experiments.variant_pool.reporting import CandidateTaskResult, RunReport, TaskResult
from experiments.variant_pool.router import Router
from experiments.variant_pool.target import STRATEGIES as TARGET_STRATEGIES
from experiments.variant_pool.target import select_target_variant

logger = logging.getLogger("gaia_evolver.variant_pool")

# Paper recipe defaults. Shared ``defaults.py`` intentionally stays tuned for
# the small development recipe; this parallel variant-pool entry point is the
# paper-aligned one and every value remains overrideable on its CLI.
PAPER_NUM_ROUNDS = 15
PAPER_PASS_K = 2
PAPER_PATIENCE = 3
PAPER_CONCURRENCY = 10
PAPER_PLANNED_SEEDS = (0, 1, 2)
PAPER_CANDIDATES_PER_ROUND = 4
CANDIDATE_MODES = ("paper", "legacy_single")

# W28 — manifest contract mode (--manifest-mode). ``repo`` accepts the open
# repo meta-agent's own journal vocabulary and adapts it; ``paper`` injects the
# Table 9 schema + a filled example and requires strict paper-shaped output.
# ``repo`` is the default because it is the contract the open-source meta-agent
# was actually trained to produce (runs/forkprobe_11 evidence).
MANIFEST_MODES = ("repo", "paper")
DEFAULT_MANIFEST_MODE = "repo"
#: One Critic-style revision is allowed on a no-config outcome (paper §4.3).
DEFAULT_EVOLVE_RETRY = 1

# B4 — the repo's own hard requirement, quoted from ``agent.py`` L926-937's
# DECISION_REQUIRED notice, front-loaded into our injected brief so the
# meta-agent commits to a decision instead of stopping after analysis.
DECISION_CONTRACT_EMPHASIS = (
    "HARD REQUIREMENT — you MUST end this turn by committing to a final "
    "decision, exactly one of:\n"
    "1. Ship change: write `output_dir/config.yaml` (a real change), or\n"
    "2. Explicit no-op: copy `current_config` byte-for-byte to "
    "`output_dir/config.yaml` (e.g. `cp <current_config> <output_dir>/config.yaml`).\n"
    "Ending with analysis but no written `config.yaml` is INVALID and fails the "
    "round: \"it ended with analysis but did not commit to a final decision.\" "
    "A missing `config.yaml` is not a valid outcome."
)

# --manifest-mode paper — inject the Table 9 (p.36) schema + the C-R10-02 worked
# instance (report §3.3) so a repo-trained meta-agent writes manifest.yaml in
# the paper shape instead of its journal vocabulary. Recipe-layer injection
# only; nothing under ``harnessx/`` is modified.
PAPER_MANIFEST_SCHEMA_BRIEF = (
    "Write `_meta_scratch/manifest.yaml` as a bare YAML mapping in the paper's "
    "Table 9 schema. Types are STRICT: `capability_evidence` and `file_changes` "
    "are LISTS OF MAPPINGS and `predicted_impact` is a MAPPING — never prose "
    "strings. Do NOT use journal vocabulary (no `levers`/`lens`/`lever`/`intent`/"
    "`predicted_affected` keys; unknown keys are rejected). Required keys/types:\n"
    "  candidate_id: str  # exactly the suggested id\n"
    "  bucket: list[str]  # subset of [prompt, tools, config, processor]\n"
    "  iterates_from: str|null\n"
    "  capability_evidence: list of {type, claim, evidence}  # [] for a pure prompt edit\n"
    "  file_changes: list of {path, action(create|modify|delete), diff_summary}\n"
    "  predicted_impact: {tasks_will_unlock: [...], tasks_will_stabilize: [...], tasks_at_risk: [...]}\n"
    "  attribution_signature: {type(tool_call|processor_invocation|prompt_feature), tool_name, expected_min_calls}  # null for a pure prompt edit\n"
    "  target_variant: str  # exactly the given target\n"
    "Filled example (paper C-R10-02, appendix C.1 p.37):\n"
    "  candidate_id: C-R10-02\n"
    "  bucket: [tools, prompt, config]\n"
    "  capability_evidence:\n"
    "    - {type: http_endpoint, claim: \"MediaWiki API returns full plain-text extract where WebFetch returns 0 chars\", evidence: \"GET .../w/api.php?...&explaintext=true -> 10,529 chars\"}\n"
    "    - {type: other, claim: \"tool return survives provider serialization to the model (Level 2)\", evidence: \"_prepare_messages([tool_msg]) keeps content as 10,529-char string\"}\n"
    "  file_changes:\n"
    "    - {path: wiki_text_fetch.py, action: create, diff_summary: \"WikiTextFetch via MediaWiki API\"}\n"
    "    - {path: config.yaml, action: create, diff_summary: \"register tool; restore R8 prompt; drop budget processor\"}\n"
    "  predicted_impact: {tasks_will_unlock: [db4fd70a, f0f46385], tasks_will_stabilize: [4b6bb5f7], tasks_at_risk: []}\n"
    "  attribution_signature: {type: tool_call, tool_name: WikiTextFetch, expected_min_calls: 1}\n"
    "  target_variant: V0\n"
)

# --manifest-mode repo — the caller adapts the meta-agent's natural journal
# vocabulary, so it does not have to reshape into the paper schema.
REPO_MANIFEST_SCHEMA_BRIEF = (
    "Write `_meta_scratch/manifest.yaml` as a bare YAML mapping. You MAY use your "
    "journal vocabulary directly: `levers` (subset of configuration/control/"
    "action/instruction), `predicted_affected` (task ids you expect to flip), and "
    "`hypothesis_id`, alongside `candidate_id`, `target_variant`, and a "
    "`file_changes` list (prose bullets or {path, action, diff_summary} entries). "
    "The caller maps this to the internal manifest; you do not need to supply "
    "`capability_evidence` or a paper `attribution_signature`."
)


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


def _make_variant_meta_agent(template: Any, journal_path: Path) -> Any:
    """W9 — a per-variant meta-agent bound to that variant's own journal.

    The paper's gates (canonicalize -> replay smoke -> novelty -> evidence) run
    *inside* ``meta_agent.evolve``, not in the C1 gate: ``agent.py`` builds an
    ``EvolveValidator(memo_path=self.memo_path)`` which calls ``check_novelty``
    on that memo. ``MetaAgent`` binds ``memo_path`` at construction time and
    ``evolve`` takes no per-call memo override, so isolating the novelty check
    per variant means one meta-agent per variant, each bound to
    ``variant.journal_path`` (SPEC §2.1 / §C3 W9). Sharing one global memo would
    let variant B's legitimate retry of a cluster variant A reverted trip
    ``reverted_signature_reused`` and be falsely killed.

    A shallow copy shares the template's read-only config (model, budgets, skill
    dirs, write roots) and swaps only the memo; ``MetaAgent`` keeps no
    per-evolve mutable state — its tracer/harness/scratch are all built locally
    inside each ``evolve`` call — so the copy is safe. ``.resolve()`` mirrors
    ``MetaAgent.__init__`` so V0's journal (``run_dir/learnings.md``) reproduces
    the single-lineage memo byte-for-byte under K=1. Module level so the offline
    W9 test can observe / replace it.
    """
    clone = copy.copy(template)
    clone.memo_path = Path(journal_path).resolve()
    return clone


def _build_candidate_contract(
    *,
    manifest_mode: str,
    suggested_candidate_id: str,
    target_variant: str,
    planner_brief: Mapping[str, Any],
    decision_feedback: str | None = None,
) -> dict[str, Any]:
    """Assemble the ``candidate_contract`` our recipe injects into ``TASK.md``.

    All injection is recipe-layer: it augments the ``planner_brief`` that
    ``agent.py:_render_candidate_contract`` renders verbatim as JSON. Nothing
    under ``harnessx/`` is touched. Carries (1) the mode-specific manifest
    instructions (paper Table 9 schema + C-R10-02 example, or the repo-journal
    acceptance note) for fault (1), and (2) the B4 decision-contract emphasis for
    fault (2). On a retry it also carries the prior ``DECISION_REQUIRED.md`` text.
    """
    manifest_instructions = (
        PAPER_MANIFEST_SCHEMA_BRIEF
        if manifest_mode == "paper"
        else REPO_MANIFEST_SCHEMA_BRIEF
    )
    brief: dict[str, Any] = {
        **dict(planner_brief),
        "manifest_mode": manifest_mode,
        "manifest_instructions": manifest_instructions,
        "decision_contract_requirement": DECISION_CONTRACT_EMPHASIS,
    }
    if decision_feedback:
        brief["prior_decision_required_feedback"] = (
            "Your previous attempt ended without writing `config.yaml` and was "
            "rejected. The orchestrator's DECISION_REQUIRED notice follows; this "
            "time you MUST end by writing `config.yaml` (a real change) or an "
            "explicit `cp` no-op.\n\n" + decision_feedback
        )
    return {
        "suggested_candidate_id": suggested_candidate_id,
        "target_variant": target_variant,
        "planner_brief": brief,
    }


@dataclass
class _EvolveOutcome:
    """Result of one candidate's (possibly retried) evolve."""

    config_path: Path
    attempts: int  # total evolve invocations (>= 1)
    retries: int  # attempts - 1
    decision_required_history: tuple[str, ...]


async def _evolve_candidate_with_retry(
    *,
    slot_agent: Any,
    slot: "CandidateSlot",
    manifest_mode: str,
    target_variant: str,
    planner_brief: Mapping[str, Any],
    base_evolve_kwargs: Mapping[str, Any],
    max_retries: int,
) -> _EvolveOutcome:
    """Run ``slot_agent.evolve``; on a *no-config* outcome, retry with feedback.

    Fault (2): DeepSeek pro often finishes with analysis but no ``config.yaml``,
    which makes ``agent.py`` write ``_meta_scratch/DECISION_REQUIRED.md`` and
    raise. We detect that specific case by the presence of that file, feed its
    text back into the next attempt's brief, and retry up to ``max_retries``
    times (paper §4.3 allows one Critic revision). Every attempt writes to an
    isolated subdirectory so a partial write never contaminates the next one.

    Only the no-config case is retried: a timeout, a validator failure, or any
    other exception (no DECISION_REQUIRED.md) re-raises immediately, and a
    byte-identical explicit no-op is a *successful* evolve handled by the caller
    — it is never silently converted into a retry or a no-op fallback.
    """
    decision_history: list[str] = []
    for attempt in range(max_retries + 1):
        attempt_dir = (
            Path(slot.output_dir)
            if attempt == 0
            else Path(slot.output_dir) / f"retry_{attempt:02d}"
        )
        attempt_dir.mkdir(parents=True, exist_ok=True)
        contract = _build_candidate_contract(
            manifest_mode=manifest_mode,
            suggested_candidate_id=slot.suggested_candidate_id,
            target_variant=target_variant,
            planner_brief=planner_brief,
            decision_feedback=decision_history[-1] if decision_history else None,
        )
        try:
            new_yaml = await slot_agent.evolve(
                output_dir=attempt_dir,
                candidate_contract=contract,
                **dict(base_evolve_kwargs),
            )
        except Exception as exc:  # noqa: BLE001 - classify no-config vs other
            decision_path = attempt_dir / "_meta_scratch" / "DECISION_REQUIRED.md"
            if decision_path.is_file() and attempt < max_retries:
                decision_history.append(decision_path.read_text(encoding="utf-8"))
                logger.warning(
                    "[%s] no config.yaml (attempt %d/%d): %s; retrying with "
                    "DECISION_REQUIRED feedback",
                    slot.suggested_candidate_id,
                    attempt + 1,
                    max_retries + 1,
                    exc,
                )
                continue
            raise
        return _EvolveOutcome(
            config_path=Path(new_yaml),
            attempts=attempt + 1,
            retries=attempt,
            decision_required_history=tuple(decision_history),
        )
    raise AssertionError("unreachable: retry loop exited without return/raise")


@dataclass
class PoolCandidate:
    """One round's proposed config for one variant.

    Deliberately **not** a :class:`~experiments.variant_pool.manifest.ChangeManifest`
    so the C1 gate treats it opaquely and the decision is the pure seesaw on
    ``T_k`` (SPEC §8.4 step 3). Batch C4 investigated the gate's canonicalize /
    smoke stages and found them redundant with ``meta_agent.evolve``'s internal
    gate plus the evaluate-time ``_prepare_round_config`` canonicalize, so they
    stay no-op by design (SPEC §7.8); no per-candidate manifest is wired here.
    Carries ``target_variant`` (the variant this
    candidate is for) and ``candidate_id`` (read by the engine when archiving a
    rejection). ``is_baseline`` marks a round-0 "adopt my own config" candidate,
    which produced no meta-agent call.
    """

    candidate_id: str
    target_variant: str
    config_path: Path
    is_baseline: bool = False


@dataclass
class _EvidenceDigester:
    """Deterministic EvidenceStore adapter used by paper candidate mode.

    This is a runnable fallback, not the paper's unpublished LLM Digester.  It
    returns the latest settled digest for each task currently routed to the
    target, preserving the cross-round history already attached on ingestion.
    """

    evidence: EvidenceStore
    pool: VariantPool

    async def digest(self, *, context: PipelineContext) -> DigesterRoundArtifact:
        variant = self.pool.variants.get(context.target_variant)
        if variant is None:
            return DigesterRoundArtifact(
                digests=(),
                actionability=0.0,
                rationale="selected target is no longer active",
            )
        routed = set(variant.routed_tasks)
        latest: dict[str, TaskDigest] = {}
        for digest in self.evidence.iter_digests():
            if digest.round_idx >= context.round_idx or digest.task_id not in routed:
                continue
            previous = latest.get(digest.task_id)
            if previous is None or (digest.round_idx, digest.variant_id) > (
                previous.round_idx,
                previous.variant_id,
            ):
                latest[digest.task_id] = digest
        digests = tuple(latest[task_id] for task_id in sorted(latest))
        unsolved = sum(1 for digest in digests if not digest.solved)
        return DigesterRoundArtifact(
            digests=digests,
            actionability=1.0 if unsolved else 0.0,
            rationale=(
                "deterministic fallback actionability is binary: 1.0 when at "
                f"least one settled task is unsolved (count={unsolved}), else 0.0; "
                "threshold is OURS"
            ),
        )


@dataclass
class _DeterministicPlanner:
    """Evidence-only Planner fallback with stable cluster/task briefs."""

    k_t: int

    async def plan(
        self,
        *,
        context: PipelineContext,
        digests: tuple[TaskDigest, ...],
    ) -> PlanningArtifact:
        failing = [digest for digest in digests if not digest.solved]
        grouped: dict[str, list[str]] = {}
        for digest in failing:
            bucket = digest.failure_category or "unclassified_failure"
            grouped.setdefault(bucket, []).append(digest.task_id)

        briefs: list[CandidateBrief] = []
        for index, (bucket, task_ids) in enumerate(sorted(grouped.items()), start=1):
            briefs.append(
                CandidateBrief(
                    brief_id=f"P-R{context.round_idx}-{index:02d}",
                    buckets=(bucket,),
                    task_ids=tuple(sorted(task_ids)),
                    rationale=(
                        "deterministic fallback: propose one falsifiable edit for "
                        f"the settled failure cluster {bucket!r}"
                    ),
                )
            )
            if len(briefs) >= self.k_t:
                break
        return PlanningArtifact(
            target_variant=context.target_variant,
            briefs=tuple(briefs),
            notes=(
                "Deterministic EvidenceStore Planner fallback; not the paper's LLM Planner.",
            ),
        )


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
        active_pool_evaluator: (
            Callable[[Any, set[str], int], Mapping[str, tuple[int, int]]] | None
        ) = None,
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
        self.candidate_mode = str(getattr(args, "candidate_mode", "paper"))
        if self.candidate_mode not in CANDIDATE_MODES:
            raise ValueError(
                f"candidate_mode must be one of {CANDIDATE_MODES}, got {self.candidate_mode!r}"
            )
        self.candidates_per_round = int(
            getattr(args, "candidates_per_round", PAPER_CANDIDATES_PER_ROUND)
        )
        if not 1 <= self.candidates_per_round <= PAPER_CANDIDATES_PER_ROUND:
            raise ValueError(
                "candidates_per_round must be in [1, 4]; "
                f"got {self.candidates_per_round}"
            )
        self.manifest_mode = str(getattr(args, "manifest_mode", DEFAULT_MANIFEST_MODE))
        if self.manifest_mode not in MANIFEST_MODES:
            raise ValueError(
                f"manifest_mode must be one of {MANIFEST_MODES}, got {self.manifest_mode!r}"
            )
        self.evolve_retry = int(getattr(args, "evolve_retry", DEFAULT_EVOLVE_RETRY))
        if self.evolve_retry < 0:
            raise ValueError(f"evolve_retry must be >= 0, got {self.evolve_retry}")
        self.target_strategy = str(
            getattr(
                args,
                "target_strategy",
                "worst_first" if self.candidate_mode == "paper" else "all_active_variants",
            )
        )
        if self.candidate_mode == "paper" and self.target_strategy not in TARGET_STRATEGIES:
            raise ValueError(
                "paper candidate mode selects exactly one global target and requires "
                f"one of {TARGET_STRATEGIES}; got {self.target_strategy!r}"
            )
        if (
            self.candidate_mode == "legacy_single"
            and self.target_strategy != "all_active_variants"
        ):
            raise ValueError(
                "legacy_single is an explicit all_active_variants ablation; pass "
                "--target-strategy all_active_variants"
            )

        # Per-variant lineage: the config a variant currently holds is its
        # pool ``config_path``; ``_last_traj_dir`` is where that config was last
        # evaluated (what the next evolve reads).
        self._last_traj_dir: dict[str, Path] = {}
        # W9 — ``self.meta_agent`` is the *template*; each variant evolves through
        # its own clone bound to ``variant.journal_path`` so the novelty gate
        # (run inside ``evolve``) only sees that variant's lineage. Cached by
        # variant_id (journal_path is immutable after add_root/fork).
        self._meta_agents: dict[str, Any] = {}
        # Reset each round; candidate-id keys prevent a K_t batch from
        # overwriting another candidate for the same target variant.
        self._round_candidates: dict[str, PoolCandidate | CandidateArtifact] = {}
        self._round_traj_dir: dict[str, Path] = {}
        # Candidate-gate records and settled active-pool records are physically
        # separate. Only the latter are admitted to RunReport.results.
        self._round_records: dict[str, dict[str, dict]] = {}
        self._round_candidate_memos: dict[str, Path] = {}
        # W28 — per-candidate manifest-mode/retry provenance for the report:
        # {candidate_id_or_slot: {"provenance", "retries", "attempts",
        #  "parse_status", "paper_only_gaps"}}. Never folded into headline scores.
        self._candidate_meta: dict[str, dict[str, Any]] = {}
        self._paper_target_variant: str | None = None
        self._pipeline_results: dict[str, PipelineResult] = {}
        self._pipeline_audit_paths: dict[str, str] = {}
        self._active_round_pass: dict[str, dict[str, tuple[int, int]]] = {}
        self._active_round_records: dict[str, dict[str, dict]] = {}
        self._active_round_traj_dir: dict[str, Path] = {}
        self._active_score_source: dict[str, str] = {}
        self._reconcile_status: dict[str, str] = {}
        self._external_active_pool_evaluator = active_pool_evaluator
        self._active_pool_evaluator = active_pool_evaluator or self._evaluate_active_variant

        all_ids = {t.task_id for t in self.tasks if t.task_id}
        self._all_task_ids = frozenset(all_ids)
        self.level_map: dict[str, int] = {t.task_id: int(t.level) for t in self.tasks if t.task_id}
        task_clusters = {
            task_id: f"gaia_level_{level}"
            for task_id, level in self.level_map.items()
        }
        retirement_metric = str(getattr(args, "retirement_metric", "task_macro"))

        self.pool = VariantPool(K=int(args.pool_k))
        self.pool.add_root(self.baseline_config_path, self.run_dir / "learnings.md", tasks=all_ids)
        estimator = str(getattr(args, "estimator", "laplace"))
        self.ledger = SuccessLedger(
            laplace=estimator == "laplace",
            rollup_mode=retirement_metric,
            task_clusters=task_clusters,
        )
        self.router = Router(
            cluster_mode=str(getattr(args, "cluster_mode", "routed")),
            routing_mode=str(getattr(args, "routing_mode", "cluster")),
            task_to_cluster=task_clusters,
            seed=int(getattr(args, "seed", 0)),
            window=getattr(args, "routing_window", None),
        )
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
            retirement_metric=retirement_metric,
            max_candidates_per_variant=self.candidates_per_round,
            record_selected_results=self.candidate_mode != "paper",
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
            self._round_candidate_memos = {}
            self._pipeline_results = {}
            self._pipeline_audit_paths = {}
            self._paper_target_variant = None
            self._active_round_pass = {}
            self._active_round_records = {}
            self._active_round_traj_dir = {}
            self._active_score_source = {}
            self._reconcile_status = {}

            if self.candidate_mode == "paper" and round_idx == 0:
                # R0 is the settled H0 measurement. It is neither a proposal nor
                # a gate attempt and therefore must not advance Algorithm 1's
                # idle counter. T includes this baseline round; evolution starts
                # at R1.
                result = RoundResult(
                    round_idx=0,
                    variant_count=len(self.pool),
                    idle=self.engine.idle,
                )
                self._reconcile_status["V0"] = "baseline_active"
            else:
                if self.candidate_mode == "paper":
                    self._paper_target_variant = select_target_variant(
                        self.pool,
                        self.ledger,
                        strategy=self.target_strategy,
                        round_idx=round_idx,
                    )
                result = self.engine.run_round(round_idx, set(all_ids))
                self._reconcile(result)
            self._score_active_portfolio(result, round_idx, set(all_ids))
            if self.candidate_mode == "paper":
                self._record_settled_active_outcomes(round_idx)
            self._ingest_report(result, round_idx)
            self._dump_round(result, round_idx)
            results.append(result)

            if (
                not (self.candidate_mode == "paper" and round_idx == 0)
                and self.engine.idle >= self.engine.patience
            ):
                logger.info("[R%d] idle=%d >= patience=%d — early stop", round_idx, self.engine.idle, self.engine.patience)
                break

        self._dump_final()
        return results

    # ------------------------------------------------------------------
    # callback: evolve
    # ------------------------------------------------------------------

    def _meta_agent_for(self, variant: Any) -> Any:
        """The per-variant meta-agent (W9), lazily cloned from the template.

        Built once per ``variant_id`` and cached: a variant's ``journal_path``
        never changes after ``add_root`` / ``fork``. A forked child's journal
        was already copied from its parent by :meth:`VariantPool.fork`
        (inherit-then-diverge, SPEC §2.1), so the child's first evolve reads the
        parent's history and then accumulates on its own file. Under K=1 only V0
        exists and its journal is ``run_dir/learnings.md`` — the same memo the
        single-lineage ``run.py`` uses — so behaviour is unchanged.
        """
        vid = variant.variant_id
        agent = self._meta_agents.get(vid)
        if agent is None:
            agent = _make_variant_meta_agent(self.meta_agent, variant.journal_path)
            self._meta_agents[vid] = agent
        return agent

    def _evolve(
        self,
        variant: Any,
        round_idx: int,
    ) -> PoolCandidate | tuple[CandidateArtifact, ...] | None:
        """Produce a legacy candidate or the paper mode's Critic-ranked queue."""

        if self.candidate_mode == "paper":
            if variant.variant_id != self._paper_target_variant:
                return None
            return self._await(self._run_paper_candidate_pipeline(variant, round_idx))
        return self._evolve_legacy(variant, round_idx)

    def _evolve_legacy(self, variant: Any, round_idx: int) -> PoolCandidate | None:
        """Compatibility arm: evolve every active routed variant once."""

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
            self._round_candidates[cand.candidate_id] = cand
            return cand

        evolve_dir = self.run_dir / f"R{round_idx}" / vid / "evolve"
        evolve_dir.mkdir(parents=True, exist_ok=True)
        logger.info("[R%d] %s evolve -> %s", round_idx, vid, evolve_dir)

        # meta_agent.evolve runs the paper's own gates internally (canonicalize
        # -> replay smoke -> novelty -> evidence; the "Gates check replay/novelty"
        # of Figure 6). A rejected candidate raises rather than returns — e.g. a
        # replay-smoke timeout, which DeepSeek hits where the paper's stronger
        # models did not. run.py wraps this same call in a broad except (~1316):
        # a crashed/rejected evolve just means "no shipped candidate this round,
        # reuse the current config". We mirror that: the variant contributes to
        # idle and its config is untouched. Swallowing here (not in the engine)
        # keeps the failure attributable to one variant's evolve, not the round.
        try:
            new_yaml = self._await(
                self._meta_agent_for(variant).evolve(
                    current_config=Path(variant.config_path),
                    trajectories_dir=self._last_traj_dir[vid],
                    output_dir=evolve_dir,
                    replay_model=self.model_config,
                    replay_max_cost_usd=min(0.5, float(self.args.max_cost)),
                )
            )
        except Exception as exc:  # noqa: BLE001 — mirrors run.py's evolve guard
            logger.warning("[R%d] %s evolve rejected/crashed -> no candidate: %s", round_idx, vid, exc)
            return None
        new_yaml = Path(new_yaml)

        # Byte-identical output == the meta-agent's explicit no-op idiom
        # (run.py ~line 1304). No candidate this round -> idle only.
        if Path(variant.config_path).read_bytes() == new_yaml.read_bytes():
            logger.info("[R%d] %s evolve = no-op (byte-identical)", round_idx, vid)
            return None

        cand = PoolCandidate(
            candidate_id=f"C-R{round_idx}-{vid}",
            target_variant=vid,
            config_path=new_yaml,
            is_baseline=False,
        )
        self._round_candidates[cand.candidate_id] = cand
        return cand

    async def _run_paper_candidate_pipeline(
        self,
        variant: Any,
        round_idx: int,
    ) -> tuple[CandidateArtifact, ...]:
        """Run one global K_t batch against the selected target variant."""

        vid = variant.variant_id
        trajectories_dir = self._last_traj_dir.get(vid)
        if trajectories_dir is None or not trajectories_dir.is_dir():
            raise RuntimeError(
                f"paper candidate round R{round_idx} has no settled trajectories for {vid}"
            )
        output_root = self.run_dir / f"R{round_idx}" / vid / "pipeline"
        digester = _EvidenceDigester(self.evidence, self.pool)
        planner = _DeterministicPlanner(self.candidates_per_round)

        async def _producer(
            *,
            context: PipelineContext,
            plan: PlanningArtifact,
            slot: CandidateSlot,
        ) -> CandidateArtifact | None:
            return await self._produce_paper_candidate(
                variant=variant,
                context=context,
                plan=plan,
                slot=slot,
            )

        pipeline = CandidatePipeline(
            digester=digester,
            planner=planner,
            evolver=IsolatedEvolverAdapter(producer=_producer),
            critic=DeterministicCritic(self.evidence),
            k_t=self.candidates_per_round,
            actionability_threshold=float(
                getattr(self.args, "actionability_threshold", 1.0)
            ),
        )
        context = PipelineContext(
            round_idx=round_idx,
            target_variant=vid,
            current_config_path=Path(variant.config_path),
            trajectories_dir=trajectories_dir,
            output_root=output_root,
            memo_path=Path(variant.journal_path),
            regressions=self._recent_regressions(variant),
            failure_buckets=self._failure_buckets(variant),
        )
        pipeline_result = await pipeline.run(context)
        self._pipeline_results[vid] = pipeline_result
        self._persist_pipeline_audit(vid, round_idx, pipeline_result)
        for candidate in pipeline_result.considered_candidates:
            self._round_candidates[candidate.candidate_id] = candidate
        # The Critic only ranks. The engine's deterministic gate remains the
        # sole shipping authority and therefore receives only this queue.
        return pipeline_result.ranked_for_gate

    async def _produce_paper_candidate(
        self,
        *,
        variant: Any,
        context: PipelineContext,
        plan: PlanningArtifact,
        slot: CandidateSlot,
    ) -> CandidateArtifact | None:
        """Run MetaAgent in one isolated slot and require a valid manifest.

        Two faults from ``runs/forkprobe_11`` are fixed here in the recipe layer:
        (1) manifest format via ``--manifest-mode`` (repo adapter vs paper
        schema injection) and (2) no-config outcomes via ``--evolve-retry``.
        Neither touches ``harnessx/``.
        """

        brief = self._brief_for_slot(plan, slot)
        slot_agent = _make_variant_meta_agent(self.meta_agent, slot.memo_path)
        slot_id = slot.suggested_candidate_id
        base_kwargs = {
            "current_config": context.current_config_path,
            "trajectories_dir": context.trajectories_dir,
            "replay_model": self.model_config,
            "replay_max_cost_usd": min(0.5, float(self.args.max_cost)),
        }
        try:
            outcome = await _evolve_candidate_with_retry(
                slot_agent=slot_agent,
                slot=slot,
                manifest_mode=self.manifest_mode,
                target_variant=context.target_variant,
                planner_brief=asdict(brief),
                base_evolve_kwargs=base_kwargs,
                max_retries=self.evolve_retry,
            )
        except Exception as exc:  # noqa: BLE001 - adapter converts to ProposalFailure
            self._candidate_meta[slot_id] = {
                "manifest_mode": self.manifest_mode,
                "provenance": None,
                "attempts": self.evolve_retry + 1,
                "retries": self.evolve_retry,
                "parse_status": "no_config_after_retry",
                "paper_only_gaps": [],
            }
            raise RuntimeError(
                f"MetaAgent proposal failed for {slot_id}: {exc}"
            ) from exc

        config_path = outcome.config_path
        meta: dict[str, Any] = {
            "manifest_mode": self.manifest_mode,
            "provenance": None,
            "attempts": outcome.attempts,
            "retries": outcome.retries,
            "parse_status": "ok",
            "paper_only_gaps": [],
        }
        self._candidate_meta[slot_id] = meta

        if context.current_config_path.read_bytes() == config_path.read_bytes():
            meta["parse_status"] = "explicit_noop"
            raise ValueError(f"{slot_id}: byte-identical explicit no-op")

        # Retries write to isolated subdirs, so the manifest lives beside the
        # config the winning attempt actually returned, not the base slot dir.
        manifest_path = config_path.parent / "_meta_scratch" / "manifest.yaml"
        if not manifest_path.is_file():
            meta["parse_status"] = "manifest_missing"
            raise FileNotFoundError(
                f"{slot_id}: required manifest missing: {manifest_path}"
            )
        manifest_text = manifest_path.read_text(encoding="utf-8")

        if self.manifest_mode == "paper":
            try:
                manifest = ChangeManifest.from_yaml(manifest_text)
            except Exception as exc:  # noqa: BLE001 - preserve parser detail in audit
                meta["parse_status"] = "format_mismatch"
                raise ValueError(
                    f"{slot_id}: invalid manifest.yaml (paper schema mismatch): {exc}"
                ) from exc
        else:  # repo: accept the meta-agent's journal vocabulary
            try:
                manifest = adapt_repo_journal_manifest(
                    manifest_text,
                    fallback_candidate_id=slot_id,
                    fallback_target_variant=context.target_variant,
                )
            except RepoJournalFormatError as exc:
                meta["parse_status"] = "format_mismatch"
                raise ValueError(
                    f"{slot_id}: repo manifest format mismatch: {exc}"
                ) from exc

        meta["provenance"] = manifest.provenance
        meta["paper_only_gaps"] = list(manifest.paper_only_gaps())

        artifact = CandidateArtifact(
            config_path=config_path,
            manifest=manifest,
            target_variant=context.target_variant,
        )
        # Re-key the metadata under the manifest's own candidate_id (the slot id
        # and manifest id normally agree, but the manifest is authoritative).
        if artifact.candidate_id and artifact.candidate_id != slot_id:
            self._candidate_meta[artifact.candidate_id] = meta
        self._round_candidate_memos[artifact.candidate_id] = slot.memo_path
        return artifact

    @staticmethod
    def _brief_for_slot(plan: PlanningArtifact, slot: CandidateSlot) -> CandidateBrief:
        if not plan.briefs:
            return CandidateBrief(
                brief_id=slot.suggested_candidate_id,
                buckets=(),
                task_ids=(),
                rationale="no deterministic Planner brief was available",
            )
        try:
            position = max(0, int(slot.suggested_candidate_id.rsplit("-", 1)[-1]) - 1)
        except ValueError:
            position = 0
        return plan.briefs[position % len(plan.briefs)]

    def _recent_regressions(self, variant: Any) -> tuple[str, ...]:
        """Tasks whose last two settled outcomes changed solved -> unsolved."""

        routed = set(variant.routed_tasks)
        history: dict[str, list[TaskDigest]] = {}
        for digest in self.evidence.iter_digests():
            if digest.task_id in routed:
                history.setdefault(digest.task_id, []).append(digest)
        regressions: list[str] = []
        for task_id, digests in history.items():
            ordered = sorted(
                digests,
                key=lambda item: (item.round_idx, item.variant_id),
            )
            if len(ordered) >= 2 and ordered[-2].solved and not ordered[-1].solved:
                regressions.append(task_id)
        return tuple(sorted(regressions))

    def _failure_buckets(self, variant: Any) -> tuple[str, ...]:
        """Latest settled failure categories for the target's routed tasks."""

        routed = set(variant.routed_tasks)
        latest: dict[str, TaskDigest] = {}
        for digest in self.evidence.iter_digests():
            if digest.task_id not in routed:
                continue
            previous = latest.get(digest.task_id)
            if previous is None or (digest.round_idx, digest.variant_id) > (
                previous.round_idx,
                previous.variant_id,
            ):
                latest[digest.task_id] = digest
        return tuple(
            sorted(
                {
                    digest.failure_category
                    for digest in latest.values()
                    if not digest.solved and digest.failure_category
                }
            )
        )

    def _persist_pipeline_audit(
        self,
        target_variant: str,
        round_idx: int,
        pipeline_result: PipelineResult,
    ) -> None:
        """Persist every pre-gate disposition and mirror rejections to evidence."""

        audit_path = (
            self.run_dir
            / f"R{round_idx}"
            / target_variant
            / "pipeline_audit.json"
        )
        audit_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "round": round_idx,
            "target_variant": target_variant,
            "adapter": {
                "digester": "deterministic_evidence_store_fallback",
                "planner": "deterministic_failure_cluster_fallback",
                "evolver": "MetaAgent_isolated_slots",
                "critic": "deterministic_portfolio_fallback",
                "llm_aegis_reproduction": False,
            },
            "digests": [digest.to_dict() for digest in pipeline_result.digests],
            "plan": (
                asdict(pipeline_result.plan)
                if pipeline_result.plan is not None
                else None
            ),
            "considered_candidate_ids": [
                candidate.candidate_id
                for candidate in pipeline_result.considered_candidates
            ],
            "ranked_for_gate": [
                candidate.candidate_id
                for candidate in pipeline_result.ranked_for_gate
            ],
            "critic_review": asdict(pipeline_result.critic_review),
            "revision_count": pipeline_result.revision_count,
            "no_op": pipeline_result.no_op,
            "no_op_reasons": list(pipeline_result.no_op_reasons),
            "actionability": pipeline_result.actionability,
            "actionability_threshold": pipeline_result.actionability_threshold,
            "actionability_threshold_provenance": (
                pipeline_result.actionability_threshold_provenance
            ),
            "short_circuit": pipeline_result.short_circuit,
            "audit": [asdict(record) for record in pipeline_result.audit],
        }
        audit_path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        self._pipeline_audit_paths[target_variant] = str(
            audit_path.relative_to(self.run_dir)
        )

        for index, record in enumerate(pipeline_result.audit, start=1):
            if record.disposition != "rejected":
                continue
            candidate_id = record.candidate_id or (
                f"PIPELINE-R{round_idx}-{index:02d}"
            )
            self.evidence.append_rejected(
                RejectedCandidate(
                    candidate_id=candidate_id,
                    round_idx=round_idx,
                    variant_id=target_variant,
                    failed_stage=f"PIPELINE_{record.phase.upper()}",
                    archive_reason=record.reason,
                )
            )

    # ------------------------------------------------------------------
    # callback: evaluate
    # ------------------------------------------------------------------

    def _evaluate(self, candidate: Any, t_k: set[str], round_idx: int) -> dict[str, tuple[int, int]]:
        """Evaluate a pre-settlement candidate on ``T_k`` for the gate."""
        return self._await(self._run_evaluation(candidate, set(t_k), round_idx))

    async def _run_evaluation(self, candidate: Any, t_k: set[str], round_idx: int) -> dict[str, tuple[int, int]]:
        vid = candidate.target_variant
        candidate_id = str(candidate.candidate_id)
        vround_dir = (
            self.run_dir
            / f"R{round_idx}"
            / vid
            / "candidate_gate"
            / candidate_id
        )
        outcomes, cleaned, traj_dir = await self._run_config_evaluation(
            config_path=Path(candidate.config_path),
            variant_id=vid,
            task_ids=set(t_k),
            round_idx=round_idx,
            vround_dir=vround_dir,
            label=f"R{round_idx}-{vid}-{candidate_id}",
            trajectory_rel_dir=(
                f"R{round_idx}/{vid}/candidate_gate/{candidate_id}/trajectories"
            ),
            measurement_scope="candidate_gate",
        )
        self._round_traj_dir[candidate_id] = traj_dir
        self._round_records[candidate_id] = cleaned
        return outcomes

    def _evaluate_active_variant(
        self,
        variant: Any,
        task_ids: set[str],
        round_idx: int,
    ) -> dict[str, tuple[int, int]]:
        """Default real scorer for one deployed active variant.

        The constructor's ``active_pool_evaluator`` seam replaces this method
        in offline tests, so full post-settlement scoring needs no network.
        """
        return self._await(self._run_active_evaluation(variant, set(task_ids), round_idx))

    async def _run_active_evaluation(
        self,
        variant: Any,
        task_ids: set[str],
        round_idx: int,
    ) -> dict[str, tuple[int, int]]:
        vid = variant.variant_id
        vround_dir = self.run_dir / f"R{round_idx}" / "active_pool" / vid
        outcomes, cleaned, traj_dir = await self._run_config_evaluation(
            config_path=Path(variant.config_path),
            variant_id=vid,
            task_ids=set(task_ids),
            round_idx=round_idx,
            vround_dir=vround_dir,
            label=f"R{round_idx}-{vid}-active",
            trajectory_rel_dir=f"R{round_idx}/active_pool/{vid}/trajectories",
            measurement_scope="settled_active_pool",
        )
        self._active_round_records[vid] = cleaned
        self._active_round_traj_dir[vid] = traj_dir
        return outcomes

    async def _run_config_evaluation(
        self,
        *,
        config_path: Path,
        variant_id: str,
        task_ids: set[str],
        round_idx: int,
        vround_dir: Path,
        label: str,
        trajectory_rel_dir: str,
        measurement_scope: str,
    ) -> tuple[dict[str, tuple[int, int]], dict[str, dict], Path]:
        """Run pass@k for one config and return outcomes, records and artefacts."""
        traj_dir = vround_dir / "trajectories"
        traj_dir.mkdir(parents=True, exist_ok=True)
        sessions_dir = vround_dir / "sessions"

        journal = _make_journal(sessions_dir)
        round_config = _prepare_round_config(config_path, journal)
        try:
            round_config.to_yaml_file(vround_dir / "config.yaml")
        except Exception as exc:  # noqa: BLE001 - best-effort reproducibility dump
            logger.debug("[R%d] %s config dump skipped: %s", round_idx, variant_id, exc)

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
                variant_id=variant_id,
                trajectory_rel_dir=trajectory_rel_dir,
            )

        ordered = sorted(task_ids)
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

        outcomes: dict[str, tuple[int, int]] = {}
        cleaned: dict[str, dict] = {}
        for tid, record in zip(ordered, records):
            n_pass = int(record.get("n_pass") or 0)
            n_att = int(record.get("n_att") or 0)
            outcomes[tid] = (n_pass, n_att)
            clean = self._clean_record(record, variant_id=variant_id, round_idx=round_idx)
            clean["measurement_scope"] = measurement_scope
            cleaned[tid] = clean
        return outcomes, cleaned, traj_dir

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
        trajectory_rel_dir: str,
    ) -> dict:
        """Mirror ``run.py``'s inline ``_finalize_attempt`` closure per variant.

        Same behaviour as ``run.py`` (judge verdict lookup, trajectory write),
        only the trajectory path is namespaced under the variant's subdir.
        """
        harness = record.pop("_harness", None)
        raw = record.get("_result")
        tid = record.get("task_id") or "unknown"
        traj_name = f"{tid}.md" if attempt_idx == 0 else f"{tid}.a{attempt_idx + 1}.md"
        record["trajectory_file"] = f"{trajectory_rel_dir}/{traj_name}"

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
        retired = set(result.retired)
        for retired_id in result.retired:
            self._last_traj_dir.pop(retired_id, None)
            self._meta_agents.pop(retired_id, None)  # W9: drop the retired variant's agent
            self._reconcile_status[retired_id] = "retired"

        for vid in result.no_candidate_variants:
            self._reconcile_status.setdefault(vid, "no_candidate")

        # The compatibility arm's baseline is evaluated by the engine but is
        # useful as trajectory seed even if its gate decision rejects.
        for candidate_id, candidate in self._round_candidates.items():
            if not isinstance(candidate, PoolCandidate) or not candidate.is_baseline:
                continue
            traj_dir = self._round_traj_dir.get(candidate_id)
            variant = self.pool.variants.get(candidate.target_variant)
            if traj_dir is not None and variant is not None:
                self._last_traj_dir[candidate.target_variant] = traj_dir
                self._reconcile_status[candidate.target_variant] = "baseline_active"

        for vid, candidate_id in sorted(result.selected_candidate_ids.items()):
            candidate = self._round_candidates.get(candidate_id)
            if candidate is None:
                raise RuntimeError(
                    f"selected candidate {candidate_id!r} for {vid!r} is absent from recipe state"
                )
            traj_dir = self._round_traj_dir.get(candidate_id)
            if traj_dir is None:
                raise RuntimeError(
                    f"selected candidate {candidate_id!r} has no evaluation trajectories"
                )
            decision = result.decisions.get(vid)
            variant = self.pool.variants.get(vid)
            if variant is None:
                # A variant can APPLY early in the engine loop and be selected
                # as the retiree by a later same-round FORK. Its ledger event is
                # historical fact, but there is no deployed carrier to mutate.
                if vid in retired and decision is Decision.APPLY:
                    self._reconcile_status[vid] = "applied_then_retired"
                elif vid in retired:
                    self._reconcile_status[vid] = "candidate_target_retired"
                else:
                    self._reconcile_status[vid] = "missing_after_settlement"
                continue
            if isinstance(candidate, PoolCandidate) and candidate.is_baseline:
                # Baseline adopts the variant's own config: seed trajectories so
                # the next round can evolve, whatever the gate said.
                self._last_traj_dir[vid] = traj_dir
                self._reconcile_status[vid] = "baseline_active"
            elif decision is Decision.APPLY:
                variant.config_path = Path(candidate.config_path)
                self._last_traj_dir[vid] = traj_dir
                self._adopt_candidate_memo(candidate_id, variant.journal_path)
                self._reconcile_status[vid] = "applied"
            elif decision is Decision.FORK:
                self._reconcile_status[vid] = "fork_parent_unchanged"

        for vid, decision in result.decisions.items():
            if decision is Decision.REJECT:
                self._reconcile_status.setdefault(vid, "rejected_unchanged")

        for child_id in result.forked:
            child = self.pool.variants.get(child_id)
            if child is None:
                self._reconcile_status[child_id] = (
                    "forked_then_retired" if child_id in retired else "fork_child_missing"
                )
                continue
            parent_id = child.parent_id
            candidate_id = (
                result.selected_candidate_ids.get(parent_id)
                if parent_id is not None
                else None
            )
            candidate = (
                self._round_candidates.get(candidate_id)
                if candidate_id is not None
                else None
            )
            traj_dir = (
                self._round_traj_dir.get(candidate_id)
                if candidate_id is not None
                else None
            )
            if candidate is not None:
                # The child embodies the improved edit, not the parent's config
                # (pool.fork cloned the parent's).
                child.config_path = Path(candidate.config_path)
            if traj_dir is not None:
                self._last_traj_dir[child_id] = traj_dir
            if candidate_id is not None:
                self._adopt_candidate_memo(candidate_id, child.journal_path)
            self._reconcile_status[child_id] = "fork_child_active"

    def _adopt_candidate_memo(self, candidate_id: str, journal_path: Path) -> None:
        """Promote only the selected slot's private memo into its live lineage."""

        memo_path = self._round_candidate_memos.get(candidate_id)
        if memo_path is None or not memo_path.is_file():
            return
        destination = Path(journal_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(memo_path, destination)

    # ------------------------------------------------------------------
    # settled active-pool measurement
    # ------------------------------------------------------------------

    def _score_active_portfolio(
        self,
        result: RoundResult,
        round_idx: int,
        all_task_ids: set[str],
    ) -> None:
        """Score the complete deployed portfolio after settlement/reconcile.

        Every task has exactly one *current* carrier. Candidate-gate outcomes
        may be reused only when the whole routed subset was run by the exact
        config now deployed under that carrier. REJECT is never reusable.
        Supplying an external evaluator disables reuse so offline tests observe
        the post-settlement carrier directly.
        """
        routing: dict[str, str] = {}
        for vid, variant in sorted(self.pool.variants.items()):
            for task_id in sorted(set(variant.routed_tasks) & all_task_ids):
                previous = routing.setdefault(task_id, vid)
                if previous != vid:
                    raise RuntimeError(
                        f"active-pool routing is not a partition: {task_id!r} is carried by "
                        f"{previous!r} and {vid!r}"
                    )
        missing = sorted(all_task_ids - set(routing))
        if missing:
            raise RuntimeError(
                f"active-pool score missing deployed carriers for {missing}; "
                "every round must cover the fixed full task set"
            )

        for vid, variant in sorted(self.pool.variants.items()):
            task_ids = {task for task, carrier in routing.items() if carrier == vid}
            if not task_ids:
                continue
            reused = None
            if self._external_active_pool_evaluator is None:
                reused = self._safe_candidate_reuse(variant, task_ids, result, round_idx)

            if reused is not None:
                outcomes, records, traj_dir = reused
                self._active_round_records[vid] = records
                self._active_round_traj_dir[vid] = traj_dir
                self._active_score_source[vid] = "candidate_reuse"
            else:
                raw = self._active_pool_evaluator(variant, set(task_ids), round_idx)
                outcomes = {task: tuple(value) for task, value in raw.items()}
                self._require_full_active_coverage(vid, task_ids, outcomes)
                if self._external_active_pool_evaluator is not None:
                    # A plain injected mapping has no rollout artefacts. Keep a
                    # truthful minimal record rather than manufacturing infra=1.
                    self._active_round_records[vid] = {
                        task_id: {
                            "task_id": task_id,
                            "variant_id": vid,
                            "round": round_idx,
                            "n_pass": int(outcomes[task_id][0]),
                            "n_att": int(outcomes[task_id][1]),
                            "measurement_scope": "settled_active_pool",
                            "measurement_source": "injected",
                        }
                        for task_id in sorted(task_ids)
                    }
                self._active_score_source[vid] = (
                    "injected" if self._external_active_pool_evaluator is not None else "fresh_rollout"
                )

            self._require_full_active_coverage(vid, task_ids, outcomes)
            self._active_round_pass[vid] = {
                task_id: (int(outcomes[task_id][0]), int(outcomes[task_id][1]))
                for task_id in sorted(task_ids)
            }
            traj_dir = self._active_round_traj_dir.get(vid)
            if traj_dir is None:
                # Injected offline evaluators have no rollout artefacts. Keep a
                # stable empty directory so the next paper Evolver slot still
                # receives an isolated, existing trajectory root.
                traj_dir = (
                    self.run_dir
                    / f"R{round_idx}"
                    / "active_pool"
                    / vid
                    / "trajectories"
                )
                traj_dir.mkdir(parents=True, exist_ok=True)
                self._active_round_traj_dir[vid] = traj_dir
            if traj_dir is not None:
                self._last_traj_dir[vid] = traj_dir

        measured = {
            task_id
            for outcomes in self._active_round_pass.values()
            for task_id in outcomes
        }
        if measured != all_task_ids:
            raise RuntimeError(
                "active-pool score did not cover the fixed full task set: "
                f"missing={sorted(all_task_ids - measured)}, extra={sorted(measured - all_task_ids)}"
            )

    def _record_settled_active_outcomes(self, round_idx: int) -> None:
        """Write each deployed carrier/task outcome once for next-round use."""

        seen_tasks: set[str] = set()
        for variant_id, outcomes in sorted(self._active_round_pass.items()):
            records = self._active_round_records.get(variant_id, {})
            for task_id, (n_pass, n_att) in sorted(outcomes.items()):
                if task_id in seen_tasks:
                    raise RuntimeError(
                        f"settled active ledger would double-record {task_id!r} in R{round_idx}"
                    )
                seen_tasks.add(task_id)
                self.ledger.record(
                    variant_id,
                    task_id,
                    int(n_pass),
                    int(n_att),
                    round_idx,
                )
                record = records.get(task_id, {})
                trajectory = str(record.get("trajectory_file") or "").strip()
                digest = TaskDigest(
                    task_id=task_id,
                    round_idx=round_idx,
                    variant_id=variant_id,
                    outcome=(int(n_pass), int(n_att)),
                    failure_category=(
                        None
                        if int(n_pass) >= 1
                        else f"gaia_level_{self.level_map.get(task_id, 0)}"
                    ),
                    evidence_anchors=[trajectory] if trajectory else [],
                )
                self.evidence.append_digest(
                    self.evidence.attach_prior_history(digest)
                )
        if seen_tasks != set(self._all_task_ids):
            raise RuntimeError(
                "settled active ledger did not receive exactly the fixed task set: "
                f"missing={sorted(set(self._all_task_ids) - seen_tasks)}, "
                f"extra={sorted(seen_tasks - set(self._all_task_ids))}"
            )

    def _safe_candidate_reuse(
        self,
        variant: Any,
        task_ids: set[str],
        result: RoundResult,
        round_idx: int,
    ) -> tuple[dict[str, tuple[int, int]], dict[str, dict], Path] | None:
        """Return a whole-subset reuse only for an actually deployed candidate."""
        vid = variant.variant_id
        candidates: list[tuple[str, PoolCandidate | CandidateArtifact]] = []
        for target_id, candidate_id in sorted(result.selected_candidate_ids.items()):
            candidate = self._round_candidates.get(candidate_id)
            if candidate is None:
                continue
            decision = result.decisions.get(target_id)
            deployed = (
                (
                    isinstance(candidate, PoolCandidate)
                    and candidate.is_baseline
                    and target_id == vid
                    and target_id in self.pool.variants
                )
                or (decision is Decision.APPLY and target_id == vid)
                or (
                    decision is Decision.FORK
                    and vid in result.forked
                    and getattr(variant, "parent_id", None) == target_id
                )
            )
            if deployed:
                candidates.append((target_id, candidate))

        for target_id, candidate in candidates:
            candidate_id = candidate.candidate_id
            candidate_outcomes = result.per_variant_pass.get(target_id, {})
            if not task_ids <= set(candidate_outcomes):
                continue
            if not self._same_config(Path(candidate.config_path), Path(variant.config_path)):
                continue
            source_records = self._round_records.get(candidate_id, {})
            if not task_ids <= set(source_records):
                continue
            traj_dir = self._round_traj_dir.get(candidate_id)
            if traj_dir is None:
                continue
            records: dict[str, dict] = {}
            for task_id in sorted(task_ids):
                record = dict(source_records[task_id])
                record["variant_id"] = vid  # actual deployed carrier
                record["round"] = round_idx
                record["measurement_scope"] = "settled_active_pool"
                record["measurement_source"] = "candidate_reuse"
                record["source_candidate_id"] = candidate.candidate_id
                record["source_candidate_target_variant_id"] = target_id
                records[task_id] = record
            outcomes = {
                task_id: (
                    int(candidate_outcomes[task_id][0]),
                    int(candidate_outcomes[task_id][1]),
                )
                for task_id in sorted(task_ids)
            }
            return outcomes, records, traj_dir
        return None

    @staticmethod
    def _same_config(left: Path, right: Path) -> bool:
        try:
            return left.resolve() == right.resolve() or left.read_bytes() == right.read_bytes()
        except OSError:
            return left == right

    @staticmethod
    def _require_full_active_coverage(
        variant_id: str,
        task_ids: set[str],
        outcomes: Mapping[str, tuple[int, int]],
    ) -> None:
        missing = sorted(task_ids - set(outcomes))
        extra = sorted(set(outcomes) - task_ids)
        if missing or extra:
            raise ValueError(
                f"active_pool_evaluator for {variant_id!r} must return exactly its routed subset; "
                f"missing={missing}, extra={extra}"
            )

    # ------------------------------------------------------------------
    # reporting / artefacts
    # ------------------------------------------------------------------

    def _ingest_report(self, result: RoundResult, round_idx: int) -> None:
        """Publish candidate diagnostics and the separate active-pool score."""
        # Candidate gate stream: useful for explaining decisions, never used by
        # final/peak/drift/routing-hit metrics.
        engine_candidate_ids = set(result.candidate_diagnostics)
        for candidate_id, diagnostic in sorted(result.candidate_diagnostics.items()):
            records = self._round_records.get(candidate_id, {})
            decision = (
                diagnostic.decision.value
                if diagnostic.decision is not None
                else None
            )
            failed_stage = (
                diagnostic.failed_stage.name
                if diagnostic.failed_stage is not None
                else None
            )
            if not diagnostic.evaluation:
                self.report.add_candidate(
                    CandidateTaskResult(
                        task_id="__candidate__",
                        round_idx=round_idx,
                        candidate_id=candidate_id,
                        target_variant_id=diagnostic.variant_id,
                        n_att=0,
                        n_pass=0,
                        decision=decision,
                        failed_stage=failed_stage,
                        archive_reason=diagnostic.archive_reason,
                        skipped_reason=diagnostic.skipped_reason,
                        evaluated=False,
                    )
                )
                continue
            for task_id, (n_pass, n_att) in diagnostic.evaluation.items():
                infra, budget = self._failure_counts(records.get(task_id, {}), n_pass, n_att)
                self.report.add_candidate(
                    CandidateTaskResult(
                        task_id=task_id,
                        round_idx=round_idx,
                        candidate_id=candidate_id,
                        target_variant_id=diagnostic.variant_id,
                        n_att=n_att,
                        n_pass=n_pass,
                        decision=decision,
                        failed_stage=failed_stage,
                        archive_reason=diagnostic.archive_reason,
                        skipped_reason=diagnostic.skipped_reason,
                        evaluated=True,
                        infra_failures=infra,
                        budget_exhaustions=budget,
                    )
                )

        # Producer/normalisation/Critic failures never enter the engine queue,
        # so publish one zero-attempt diagnostic row for each such candidate.
        for target_id, pipeline_result in sorted(self._pipeline_results.items()):
            rejected: dict[str, list[Any]] = {}
            for audit in pipeline_result.audit:
                if audit.candidate_id and audit.disposition == "rejected":
                    rejected.setdefault(audit.candidate_id, []).append(audit)
            for candidate_id, records in sorted(rejected.items()):
                if candidate_id in engine_candidate_ids:
                    continue
                self.report.add_candidate(
                    CandidateTaskResult(
                        task_id="__pipeline__",
                        round_idx=round_idx,
                        candidate_id=candidate_id,
                        target_variant_id=target_id,
                        n_att=0,
                        n_pass=0,
                        decision=None,
                        failed_stage=f"PIPELINE_{records[0].phase.upper()}",
                        archive_reason="; ".join(record.reason for record in records),
                        skipped_reason="rejected before deterministic gate evaluation",
                        evaluated=False,
                    )
                )

        # Settled active-pool stream: one row per fixed task, carrying the
        # variant that actually serves it after APPLY/FORK/RETIRE.
        comp_records: list[dict] = []
        for vid, outcomes in self._active_round_pass.items():
            records = self._active_round_records.get(vid, {})
            for task_id, (n_pass, n_att) in outcomes.items():
                infra, budget = self._failure_counts(records.get(task_id, {}), n_pass, n_att)
                self.report.add(
                    TaskResult(
                        task_id=task_id,
                        round_idx=round_idx,
                        n_att=n_att,
                        n_pass=n_pass,
                        variant_id=vid,
                        infra_failures=infra,
                        budget_exhaustions=budget,
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
                rec["infra_failures"] = infra
                rec["budget_exhaustions"] = budget
                rec["measurement_scope"] = "settled_active_pool"
                comp_records.append(rec)
        self._comparison_rounds[round_idx] = comp_records
        for child_id in result.forked:
            child = self.pool.variants.get(child_id)
            self.report.add_event(
                round_idx=round_idx,
                kind="fork",
                variant_id=child_id,
                parent_id=getattr(child, "parent_id", None),
            )
        for retired_id in result.retired:
            self.report.add_event(round_idx=round_idx, kind="retire", variant_id=retired_id)

        candidate_accounting = self._candidate_accounting(result)
        candidate_count = candidate_accounting["actual_candidates"]
        n_tasks = sum(len(outcomes) for outcomes in self._active_round_pass.values())
        passed = sum(
            1
            for outcomes in self._active_round_pass.values()
            for (n_pass, _) in outcomes.values()
            if n_pass >= 1
        )
        self.report.record_round(
            round_idx=round_idx,
            candidate_count=candidate_count,
            evaluated_task_denominator=len(self._all_task_ids),
            active_variant_count=len(self.pool),
        )
        self.round_summaries.append(
            {
                "round": round_idx,
                "variant_count": result.variant_count,
                "evaluated_tasks": n_tasks,
                "evaluated_task_denominator": len(self._all_task_ids),
                "candidate_count": candidate_count,
                "candidate_evaluated_tasks": candidate_accounting["evaluated_tasks"],
                "candidate_accounting": candidate_accounting,
                "pipeline_audit_paths": dict(sorted(self._pipeline_audit_paths.items())),
                "paper_target_variant": self._paper_target_variant,
                "no_candidate": candidate_count == 0,
                "passed": passed,
                "shipped": result.shipped,
                "idle": result.idle,
                "decisions": {vid: dec.value for vid, dec in result.decisions.items()},
                "reconcile_status": dict(sorted(self._reconcile_status.items())),
                "active_score_source": dict(sorted(self._active_score_source.items())),
                "forked": list(result.forked),
                "retired": list(result.retired),
            }
        )

    def _candidate_accounting(self, result: RoundResult) -> dict[str, int]:
        """Actual runtime proposal/evaluation counts, separate from planned K_t."""

        actual_ids = set(result.candidate_diagnostics)
        proposal_ids: set[str] = set()
        valid_ids: set[str] = set()
        ranked_ids: set[str] = set()
        pipeline_rejected_ids: set[str] = set()
        for pipeline_result in self._pipeline_results.values():
            valid_ids.update(
                candidate.candidate_id
                for candidate in pipeline_result.considered_candidates
            )
            ranked_ids.update(
                candidate.candidate_id
                for candidate in pipeline_result.ranked_for_gate
            )
            for audit in pipeline_result.audit:
                if audit.candidate_id and audit.phase == "proposal":
                    proposal_ids.add(audit.candidate_id)
                if audit.candidate_id and audit.disposition == "rejected":
                    pipeline_rejected_ids.add(audit.candidate_id)
            proposal_ids.update(
                candidate.candidate_id
                for candidate in pipeline_result.considered_candidates
            )
        actual_ids.update(proposal_ids)
        evaluated = {
            candidate_id
            for candidate_id, diagnostic in result.candidate_diagnostics.items()
            if diagnostic.evaluation
        }
        skipped = {
            candidate_id
            for candidate_id, diagnostic in result.candidate_diagnostics.items()
            if diagnostic.skipped_reason is not None
        }
        skipped.update(pipeline_rejected_ids - evaluated)
        gate_rejected = {
            candidate_id
            for candidate_id, diagnostic in result.candidate_diagnostics.items()
            if diagnostic.decision is Decision.REJECT
            or diagnostic.failed_stage is not None
        }
        return {
            "requested_slots": (
                self.candidates_per_round if self._pipeline_results else 0
            ),
            "actual_candidates": len(actual_ids),
            "producer_or_pipeline_rejected": len(pipeline_rejected_ids),
            "valid_considered": len(valid_ids),
            "ranked_for_gate": len(ranked_ids),
            "evaluated": len(evaluated),
            "evaluated_tasks": sum(
                len(diagnostic.evaluation)
                for diagnostic in result.candidate_diagnostics.values()
            ),
            "gate_rejected": len(gate_rejected),
            "skipped": len(skipped),
            "selected": len(result.selected_candidate_ids),
        }

    @staticmethod
    def _failure_counts(record: Mapping[str, Any], n_pass: int, n_att: int) -> tuple[int, int]:
        """Return disjoint ``(infra, budget)`` counts from a merged record."""
        failures = max(0, int(n_att) - int(n_pass))
        infra = max(0, int(record.get("infra_failures") or 0))
        attempts = record.get("attempts") or []
        budget = sum(
            1
            for attempt in attempts
            if str(attempt.get("exit_reason") or "") == "budget_exceeded"
        )
        if not attempts and str(record.get("exit_reason") or "") == "budget_exceeded":
            budget = 1
        # Old producers may have only an aggregate. New producers may provide
        # it explicitly; take the larger truthful count, then keep categories
        # disjoint and bounded by failed attempts.
        budget = max(budget, max(0, int(record.get("budget_exhaustions") or 0)))
        infra = min(infra, failures)
        budget = min(budget, failures - infra)
        return infra, budget

    def _dump_round(self, result: RoundResult, round_idx: int) -> None:
        """Per-round variant-pool snapshot (routing partition + events)."""
        candidate_accounting = self._candidate_accounting(result)
        state = {
            "round": round_idx,
            "variant_count": result.variant_count,
            "routing": {vid: sorted(v.routed_tasks) for vid, v in sorted(self.pool.variants.items())},
            "decisions": {vid: dec.value for vid, dec in result.decisions.items()},
            "forked": list(result.forked),
            "retired": list(result.retired),
            "shipped": result.shipped,
            "idle": result.idle,
            "candidate_count": candidate_accounting["actual_candidates"],
            "candidate_accounting": candidate_accounting,
            "no_candidate": candidate_accounting["actual_candidates"] == 0,
            "selected_candidate_ids": dict(sorted(result.selected_candidate_ids.items())),
            "candidate_diagnostics": {
                candidate_id: {
                    "variant_id": diagnostic.variant_id,
                    "decision": (
                        diagnostic.decision.value
                        if diagnostic.decision is not None
                        else None
                    ),
                    "failed_stage": (
                        diagnostic.failed_stage.name
                        if diagnostic.failed_stage is not None
                        else None
                    ),
                    "archive_reason": diagnostic.archive_reason,
                    "skipped_reason": diagnostic.skipped_reason,
                    "evaluated": bool(diagnostic.evaluation),
                    "evaluation": {
                        task_id: [outcome[0], outcome[1]]
                        for task_id, outcome in sorted(diagnostic.evaluation.items())
                    },
                }
                for candidate_id, diagnostic in sorted(
                    result.candidate_diagnostics.items()
                )
            },
            "pipeline_audit_paths": dict(sorted(self._pipeline_audit_paths.items())),
            "paper_target_variant": self._paper_target_variant,
            "evaluated_task_denominator": len(self._all_task_ids),
            "reconcile_status": dict(sorted(self._reconcile_status.items())),
            "active_score_source": dict(sorted(self._active_score_source.items())),
            # Backward-compatible alias, now explicitly labelled as candidate
            # gate data so it cannot be mistaken for a deployed score.
            "per_variant_pass_scope": "candidate_gate",
            "per_variant_pass": {
                vid: {task: [np_, na_] for task, (np_, na_) in outcomes.items()}
                for vid, outcomes in result.per_variant_pass.items()
            },
            "candidate_gate_measurements": {
                vid: {task: [np_, na_] for task, (np_, na_) in outcomes.items()}
                for vid, outcomes in result.per_variant_pass.items()
            },
            "active_pool_measurements": {
                vid: {task: [np_, na_] for task, (np_, na_) in outcomes.items()}
                for vid, outcomes in self._active_round_pass.items()
            },
        }
        self.pool_states.append(state)
        round_dir = self.run_dir / f"R{round_idx}"
        round_dir.mkdir(parents=True, exist_ok=True)
        (round_dir / "pool_state.json").write_text(
            json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    def _manifest_mode_report_section(self) -> str:
        """W28 — a pool_report.md section on manifest mode, retries, and gaps.

        Appended to the report so the fixes are auditable next to the
        Candidate-gate diagnostics. Pre-settlement diagnostics only; never folded
        into headline scores.
        """
        meta = self._candidate_meta
        total = len(meta)
        provenance_counts: dict[str, int] = {}
        parse_counts: dict[str, int] = {}
        total_retries = 0
        candidates_needing_retry = 0
        gap_counts: dict[str, int] = {}
        for record in meta.values():
            prov = str(record.get("provenance"))
            provenance_counts[prov] = provenance_counts.get(prov, 0) + 1
            status = str(record.get("parse_status"))
            parse_counts[status] = parse_counts.get(status, 0) + 1
            retries = int(record.get("retries") or 0)
            total_retries += retries
            if retries > 0:
                candidates_needing_retry += 1
            for gap in record.get("paper_only_gaps") or ():
                gap_counts[gap] = gap_counts.get(gap, 0) + 1
        lines = [
            "## Manifest-mode diagnostics (W28)",
            "",
            "> Recipe-layer fixes for runs/forkprobe_11 (manifest format + "
            "no-config retry). Pre-settlement diagnostics; not part of any score.",
            "",
            f"- manifest mode: {self.manifest_mode}",
            f"- candidates per round (K_t): {self.candidates_per_round} "
            "(N independent meta sessions; see M-16/M-20)",
            f"- evolve-retry budget: {self.evolve_retry}",
            f"- candidate slots tracked: {total}",
            f"- provenance: {provenance_counts or 'none'}",
            f"- parse status: {parse_counts or 'none'}",
            f"- total retries used: {total_retries} "
            f"across {candidates_needing_retry} candidate(s)",
            f"- repo paper-only gaps marked (not fabricated): {gap_counts or 'none'}",
            "",
        ]
        return "\n".join(lines)

    def _dump_final(self) -> None:
        """Write the RunReport (final+peak+curve+by-level), pool axis, comparison.json."""
        try:
            md = self.report.to_markdown(level_map=self.level_map)
            md += "\n" + self._manifest_mode_report_section()
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
                "patience": int(getattr(self.args, "patience", PAPER_PATIENCE)),
                "seed": int(getattr(self.args, "seed", 0)),
                "candidate_mode": self.candidate_mode,
                "candidates_per_round": (
                    self.candidates_per_round
                    if self.candidate_mode == "paper"
                    else "one_per_active_variant"
                ),
                "candidates_per_round_provenance": (
                    "OURS/deviation: N independent meta-agent sessions per round, "
                    "not Algorithm 1 L15's single multi-candidate Evolver stage "
                    "(PAPER-METHODOLOGY-DEVIATIONS M-16/M-20)"
                ),
                "manifest_mode": self.manifest_mode,
                "manifest_mode_provenance": (
                    "repo: adapt repo journal vocabulary (paper Table 9 contract "
                    "absent from open repo); paper: inject Table 9 schema + "
                    "C-R10-02 example (PAPER-METHODOLOGY-DEVIATIONS M-19)"
                ),
                "evolve_retry": self.evolve_retry,
                "evolve_retry_provenance": (
                    "OURS: one revision on a no-config outcome, feeding back "
                    "DECISION_REQUIRED.md (paper §4.3 one-revision allowance; "
                    "PAPER-METHODOLOGY-DEVIATIONS M-21)"
                ),
                "target_strategy": self.target_strategy,
                "target_strategy_provenance": (
                    "OURS: paper does not specify how target variant k is selected"
                    if self.candidate_mode == "paper"
                    else "legacy ablation: all active routed variants"
                ),
                "candidate_pipeline_adapter": (
                    "deterministic Digester/Planner/Critic fallbacks + MetaAgent Evolver"
                    if self.candidate_mode == "paper"
                    else "legacy MetaAgent single proposal"
                ),
                "llm_aegis_reproduction": False,
                "actionability_threshold": (
                    float(getattr(self.args, "actionability_threshold", 1.0))
                    if self.candidate_mode == "paper"
                    else None
                ),
                "actionability_threshold_provenance": (
                    OURS_ACTIONABILITY_THRESHOLD_PROVENANCE
                    if self.candidate_mode == "paper"
                    else None
                ),
                "cluster_mode": str(getattr(self.args, "cluster_mode", "routed")),
                "cluster_source": "gaia_level",
                "routing_mode": str(getattr(self.args, "routing_mode", "cluster")),
                "routing_window": getattr(self.args, "routing_window", None),
                "retirement_metric": str(
                    getattr(self.args, "retirement_metric", "task_macro")
                ),
                "estimator": str(getattr(self.args, "estimator", "laplace")),
                "score_measurement_scope": "settled_active_pool",
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
        cleaned = {k: v for k, v in record.items() if not k.startswith("_")}
        cleaned["variant_id"] = variant_id
        cleaned["round"] = round_idx
        return cleaned

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
    """Build a lock from enabled runtime behaviour and available provenance."""
    warnings: list[str] = []
    try:
        config_sha = sha256_file(baseline_config_path)
    except Exception as exc:  # noqa: BLE001
        config_sha = UNRESOLVED
        warnings.append(f"h0.config_sha256 unresolved: {exc}")
    tool_names: tuple[str, ...] = ()
    try:
        registry = getattr(original_base, "tool_registry", None)
        if registry is not None and hasattr(registry, "list_names"):
            tool_names = tuple(sorted(registry.list_names()))
    except Exception as exc:  # noqa: BLE001
        tool_names = ()
        warnings.append(f"h0.tool_registry unresolved: {exc}")
    if not tool_names:
        warnings.append("h0.tool_registry unresolved")

    prompt_sha = _system_prompt_sha(original_base, warnings)

    data_path = getattr(args, "data_path", None)
    if data_path:
        try:
            dataset = DatasetSpec.from_file(data_path)
        except Exception as exc:  # noqa: BLE001
            dataset = DatasetSpec(path=str(data_path), sha256=UNRESOLVED, size=0)
            warnings.append(f"dataset.sha256 unresolved: {exc}")
    else:
        dataset = DatasetSpec(path="huggingface:gaia", sha256=UNRESOLVED, size=0)
        warnings.append("dataset.sha256 unresolved for live Hugging Face load")

    oracle_path = Path(__file__).resolve().parent / "oracle_ceiling.py"
    try:
        oracle_sha = sha256_file(oracle_path)
    except Exception as exc:  # noqa: BLE001
        oracle_sha = UNRESOLVED
        warnings.append(f"env.oracle_ceiling_sha unresolved: {exc}")

    planned_seeds = tuple(getattr(args, "planned_seeds", PAPER_PLANNED_SEEDS))
    candidate_mode = str(getattr(args, "candidate_mode", "paper"))
    candidate_limit = int(
        getattr(args, "candidates_per_round", PAPER_CANDIDATES_PER_ROUND)
    )
    paper_mode = candidate_mode == "paper"
    target_strategy = str(
        getattr(
            args,
            "target_strategy",
            "worst_first" if paper_mode else "all_active_variants",
        )
    )

    return ExperimentLock(
        experiment_id=run_tag,
        created_at=datetime.now(timezone.utc).isoformat(),
        git_sha=_git_sha(warnings),
        h0=H0Freeze(
            config_sha256=config_sha,
            system_prompt_sha256=prompt_sha,
            tool_registry=tool_names,
        ),
        models=ModelSpec(
            task_agent_model=getattr(args, "model", UNRESOLVED),
            meta_agent_model=getattr(args, "meta_model", UNRESOLVED),
            api_base=getattr(args, "api_base", None) or UNRESOLVED,
            provider=getattr(args, "provider_id", None) or UNRESOLVED,
        ),
        dataset=dataset,
        hyperparams=Hyperparams(
            K=int(args.pool_k),
            estimator=str(getattr(args, "estimator", "laplace")),
            target_strategy=target_strategy,
            target_strategy_provenance=(
                "OURS: paper leaves target selection undefined"
                if paper_mode
                else "legacy ablation evolves all active routed variants"
            ),
            cluster_mode=str(getattr(args, "cluster_mode", "routed")),
            cluster_source="gaia_level",
            routing_mode=str(getattr(args, "routing_mode", "cluster")),
            window=getattr(args, "routing_window", None),
            routing_window=getattr(args, "routing_window", None),
            retirement_metric=str(getattr(args, "retirement_metric", "task_macro")),
            candidate_mode=candidate_mode,
            candidates_per_round=(
                f"global_up_to_{candidate_limit}"
                if paper_mode
                else "one_per_active_variant"
            ),
            candidate_limit=candidate_limit if paper_mode else 1,
            candidate_pipeline_adapter=(
                "deterministic_evidence_digester_planner_critic+llm_metaagent_evolver"
                if paper_mode
                else "legacy_metaagent_single_proposal_per_active_variant"
            ),
            candidate_pipeline_semantics=(
                "runnable_fallback_not_full_llm_aegis_reproduction"
                if paper_mode
                else "legacy_ablation_no_structured_candidate_pipeline"
            ),
            actionability_threshold=(
                float(getattr(args, "actionability_threshold", 1.0))
                if paper_mode
                else 0.0
            ),
            actionability_threshold_provenance=(
                OURS_ACTIONABILITY_THRESHOLD_PROVENANCE
                if paper_mode
                else "not enabled in legacy candidate mode"
            ),
            baseline_round_policy=(
                "R0_settled_active_only_evolution_starts_R1"
                if paper_mode
                else "legacy_baseline_candidate_runs_through_gate"
            ),
            pass_at_k=int(args.pass_k),
            T=int(args.num_rounds),
            P=int(getattr(args, "patience", PAPER_PATIENCE)),
            max_steps=int(args.max_steps),
            concurrency=int(args.concurrency),
            meta_concurrency=min(candidate_limit, PAPER_CANDIDATES_PER_ROUND) if paper_mode else 1,
            meta_max_steps=int(getattr(args, "evolve_steps", EVOLVE_MAX_STEPS)),
            planned_candidates_per_round=PAPER_CANDIDATES_PER_ROUND,
            planned_meta_concurrency=PAPER_CANDIDATES_PER_ROUND,
            planned_seeds=planned_seeds,
        ),
        env=EnvSpec(
            seed=int(getattr(args, "seed", 0)),
            oracle_ceiling_sha=oracle_sha,
        ),
        provenance_warnings=tuple(warnings),
    )


def _system_prompt_sha(original_base: Any, warnings: list[str]) -> str:
    """Hash the prompt template actually referenced by the frozen harness."""
    for processor in getattr(original_base, "processors", ()) or ():
        if not isinstance(processor, Mapping):
            continue
        if not str(processor.get("_target_", "")).endswith("SystemPromptProcessor"):
            continue
        builder = processor.get("system_builder") or {}
        if not isinstance(builder, Mapping):
            continue
        template_path = builder.get("template_path")
        if template_path:
            try:
                return sha256_file(Path(str(template_path)))
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"h0.system_prompt_sha256 unresolved: {exc}")
                return UNRESOLVED
    warnings.append("h0.system_prompt_sha256 unresolved: no template_path in frozen harness")
    return UNRESOLVED


def _git_sha(warnings: list[str]) -> str:
    """Resolve the checked-out commit without accepting dirty/blank stand-ins."""
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=Path(__file__).resolve().parents[2],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
        value = completed.stdout.strip()
        if value:
            return value
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"git_sha unresolved: {exc}")
        return UNRESOLVED
    warnings.append("git_sha unresolved: git returned an empty revision")
    return UNRESOLVED


# ---------------------------------------------------------------------------
# setup + CLI + main
# ---------------------------------------------------------------------------


def build_arg_parser() -> argparse.ArgumentParser:
    """CLI mirroring ``run.py`` plus ``--pool-k`` (SPEC §8.1: K=1 Global, K=8 Ensemble)."""
    parser = argparse.ArgumentParser(description="GAIA Variant-Pool Evolver (parallel recipe to run.py)")
    parser.add_argument("--max-tasks", type=int, default=MAX_TASKS)
    parser.add_argument("--max-cost", type=float, default=MAX_COST_USD)
    parser.add_argument("--num-rounds", type=int, default=PAPER_NUM_ROUNDS)
    parser.add_argument("--model", default=DEFAULT_MODEL, help="Inner (task-doing) agent model.")
    parser.add_argument("--meta-model", default=DEFAULT_META_MODEL, help="Meta-agent (evolve) model.")
    parser.add_argument(
        "--provider-id",
        default=os.getenv("HARNESSX_PROVIDER_ID"),
        help="Concrete provider id (or HARNESSX_PROVIDER_ID); placeholder ids are rejected.",
    )
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
    parser.add_argument(
        "--concurrency",
        type=int,
        default=PAPER_CONCURRENCY,
        help="Max concurrent trajectories (paper default: 10).",
    )
    parser.add_argument(
        "--pass-k",
        type=int,
        default=PAPER_PASS_K,
        help="Independent rollouts per task per round (paper §6.1: pass@2).",
    )
    parser.add_argument("--patience", type=int, default=PAPER_PATIENCE, help="Early-stop idle rounds P.")
    parser.add_argument("--seed", type=int, default=0, help="This run's routing RNG seed.")
    parser.add_argument(
        "--planned-seeds",
        type=_parse_seed_list,
        default=PAPER_PLANNED_SEEDS,
        help="Experiment-plan lineage seeds, comma-separated; not this run's --seed.",
    )
    parser.add_argument("--estimator", choices=("laplace", "raw"), default="laplace")
    parser.add_argument("--cluster-mode", choices=("routed",), default="routed")
    parser.add_argument(
        "--routing-mode",
        choices=("cluster", "task_tournament"),
        default="cluster",
        help="Cluster routing uses the reproducible GAIA-level task mapping.",
    )
    parser.add_argument(
        "--routing-window",
        type=int,
        default=None,
        help="Optional prior-round recency window; default is full history.",
    )
    parser.add_argument(
        "--retirement-metric",
        choices=("task_macro", "cluster_macro", "raw"),
        default="task_macro",
    )
    parser.add_argument(
        "--candidate-mode",
        choices=CANDIDATE_MODES,
        default="paper",
        help=(
            "paper = one global target and structured K_t candidate pipeline; "
            "legacy_single = one opaque proposal per active routed variant"
        ),
    )
    parser.add_argument(
        "--candidates-per-round",
        type=int,
        choices=range(1, PAPER_CANDIDATES_PER_ROUND + 1),
        default=1,
        help=(
            "Global paper-mode K_t cap. Default 1 = repo-native single-candidate "
            "mode (run this first); 4 aligns with the paper's Table 8 K_t=4. "
            "KNOWN DEVIATION: Algorithm 1 L15's {H~^k} reads as ONE Evolver stage "
            "emitting multiple candidates, but this implementation runs N "
            "independent meta-agent sessions (one per slot), which multiplies "
            "per-round cost and failure rate by N. Recorded in "
            "PAPER-METHODOLOGY-DEVIATIONS.md (M-16/M-20)."
        ),
    )
    parser.add_argument(
        "--manifest-mode",
        choices=MANIFEST_MODES,
        default=DEFAULT_MANIFEST_MODE,
        help=(
            "How the candidate manifest is obtained (fixes forkprobe_11 fault 1). "
            "repo (default) = adapt the open repo meta-agent's own journal "
            "vocabulary (levers/predicted_affected/hypothesis_id) to our manifest "
            "semantics, marking paper-only fields (e.g. capability_evidence) as "
            "missing rather than fabricating them; paper = inject the Table 9 "
            "schema + a filled C-R10-02 example into our recipe brief and require "
            "strict paper-shaped manifest.yaml. Neither modifies harnessx/."
        ),
    )
    parser.add_argument(
        "--evolve-retry",
        type=int,
        default=DEFAULT_EVOLVE_RETRY,
        help=(
            "Retries when a meta-agent slot finishes with analysis but no "
            "config.yaml (forkprobe_11 fault 2). Feeds the repo's "
            "DECISION_REQUIRED.md back and re-runs the slot up to N times (paper "
            "§4.3 allows one Critic revision; default 1). A no-config outcome is "
            "never silently treated as a no-op. Retry counts are reported."
        ),
    )
    parser.add_argument(
        "--actionability-threshold",
        type=float,
        default=1.0,
        help=(
            "OURS: threshold on binary any-unsolved settled evidence (0 or 1) "
            "required to run Planner/Evolver; the paper does not publish it."
        ),
    )
    parser.add_argument(
        "--target-strategy",
        choices=(*TARGET_STRATEGIES, "all_active_variants"),
        default="worst_first",
        help=(
            "Paper mode selects one target with this OURS policy. "
            "all_active_variants is only for legacy_single ablation mode."
        ),
    )
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


def _parse_seed_list(value: str) -> tuple[int, ...]:
    seeds = tuple(int(part.strip()) for part in value.split(",") if part.strip())
    if not seeds:
        raise argparse.ArgumentTypeError("planned seed list must not be empty")
    return seeds


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
    if args.patience < 1:
        raise SystemExit(f"--patience must be >= 1, got {args.patience}")
    if args.routing_window is not None and args.routing_window < 1:
        raise SystemExit(f"--routing-window must be >= 1, got {args.routing_window}")
    for flag, value in (
        ("--provider-id", args.provider_id),
        ("--model", args.model),
        ("--meta-model", args.meta_model),
    ):
        if not value or "YOUR_PROVIDER" in str(value).upper():
            raise SystemExit(f"{flag} must be a concrete runtime value; unresolved placeholders are not runnable")

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
        patience=args.patience,
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
