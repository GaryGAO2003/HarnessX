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
import hashlib
import json
import logging
import math
import os
import re
import shutil
import subprocess
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

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
from harnessx.meta_harness import MetaAgent  # noqa: F401 - documented base of VariantPoolMetaAgent

# Recipe-layer subclass that injects our candidate contract into TASK.md without
# modifying ``harnessx/`` (the variant pool is additive only).
from .variant_pool_meta_agent import VariantPoolMetaAgent

from benchmarks.gaia.evaluator import GAIAPipelineEvaluator
from benchmarks.gaia.harness import make_gaia_builder_gpt5
from benchmarks.gaia.task import GAIATask, load_gaia_tasks, load_gaia_tasks_from_json

# The C1 engine and its offline components (SPEC stage A/C1).
from experiments.variant_pool.engine import (
    DEFAULT_MIN_FORK,
    DEFAULT_PATIENCE,
    SHIP_POLICIES,
    RoundResult,
    VariantPoolEngine,
)
from experiments.variant_pool.candidate_pipeline import (
    CandidateBrief,
    CandidatePipeline,
    CandidateSlot,
    DigesterRoundArtifact,
    DigesterStage,
    IsolatedEvolverAdapter,
    OURS_ACTIONABILITY_THRESHOLD_PROVENANCE,
    OURS_DEFAULT_ACTIONABILITY_THRESHOLD,
    outward_candidate_id,
    PipelineContext,
    PipelineResult,
    PlannerStage,
    PlanningArtifact,
)
from experiments.variant_pool.critic import (
    CandidateVerdict,
    CriticContext,
    CriticRejection,
    CriticReview,
    CriticStage,
    DeterministicCritic,
    RevisionRequest,
    demoted_regression_concern,
    regressions_for_gate,
)
from experiments.variant_pool.evidence import EvidenceStore, RejectedCandidate, TaskDigest
from experiments.variant_pool.experiment_lock import (
    DatasetSpec,
    EnvSpec,
    ExperimentLock,
    H0Freeze,
    Hyperparams,
    ModelSpec,
    OFFICIAL_DEFAULT_ENDPOINT,
    UNRESOLVED,
    sha256_file,
)
from experiments.variant_pool.gate import (
    DEFAULT_REGRESSION_BASELINE,
    REGRESSION_BASELINE_GLOBAL,
    REGRESSION_BASELINE_MODES,
    Decision,
    GateResult,
    TaskEval,
    _declared_level2,
    run_gate,
)
from experiments.variant_pool.ledger import SuccessLedger
from experiments.variant_pool.manifest import (
    CandidateArtifact,
    ChangeManifest,
    DEFAULT_LEVEL2_LABEL,
    RepoJournalFormatError,
    adapt_repo_journal_manifest,
    check_level2_roundtrip,
)
from experiments.variant_pool.pool import VariantPool
from experiments.variant_pool.reporting import CandidateTaskResult, RunReport, TaskResult
from experiments.variant_pool.resume import (
    annotate_resume_provenance,
    apply_resume_state,
    load_resume_state,
    lock_blocking_diffs,
    plan_resume,
    rebuild_pool,
    replay_ledger,
)
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

#: Values the lab LiteLLM endpoint accepts for ``reasoning_effort``, probed
#: directly against it on 2026-08-01 for both deepseek-v4-flash and
#: deepseek-v4-pro. "very_high"/"ultra" and anything else return HTTP 400
#: ``literal_error``. Note that the *measured* effect is binary, not graded:
#: unset and "none" produce zero reasoning_content, every other value turns
#: thinking on (~4x completion tokens), and reasoning length shows no monotone
#: ordering across the enabled levels (n=1 per cell).
REASONING_EFFORT_CHOICES = ("none", "minimal", "low", "medium", "high", "xhigh", "max")

# W28 — manifest contract mode (--manifest-mode). ``repo`` accepts the open
# repo meta-agent's own journal vocabulary and adapts it; ``paper`` injects the
# Table 9 schema + a filled example and requires strict paper-shaped output.
# ``repo`` is the default because it is the contract the open-source meta-agent
# was actually trained to produce (runs/forkprobe_11 evidence).
MANIFEST_MODES = ("repo", "paper")
DEFAULT_MANIFEST_MODE = "repo"
#: One Critic-style revision is allowed on a no-config outcome (paper §4.3).
DEFAULT_EVOLVE_RETRY = 1

#: --search-backend (W1). ``chain`` (default) keeps the built-in ``WebSearch``
#: fallback chain byte-identical; ``serper`` swaps a Serper-first drop-in
#: (``harnessx.tools.contrib.serper_search``) into the deployed H0 registry.
SEARCH_BACKENDS = ("chain", "serper")
DEFAULT_SEARCH_BACKEND = "chain"

#: --evolve-commit-bounce (W1/F-A). ``off`` (default) is byte-identical; ``on``
#: lets a no-config slot get ONE short, tiny-budget "commit a decision" bounce
#: before it is failed (treats the meta-agent's empty-handed early ``end_turn``).
EVOLVE_COMMIT_BOUNCE_MODES = ("off", "on")
DEFAULT_EVOLVE_COMMIT_BOUNCE = "off"
#: The bounce runs a strictly bounded continuation so it is cheap: <= 15 steps.
_BOUNCE_MAX_STEPS = 15

#: --regression-accountability (F-B). ``strict`` (default) is byte-identical: any
#: active regression can trigger the whole-round no-op veto. ``shipped_only`` only
#: hard-gates regressions a shipped APPLY/FORK config change actually caused;
#: rejected-candidate gate regressions and zero-ship inter-round variance are
#: demoted to (visible, non-blocking) ``strategy_concerns``.
REGRESSION_ACCOUNTABILITY_MODES = ("strict", "shipped_only")
DEFAULT_REGRESSION_ACCOUNTABILITY = "strict"

#: --force-gate — TEMPORARY plumbing-probe modes. ``off`` is the default and
#: keeps the deterministic gate byte-identical; ``apply``/``fork`` override the
#: gate's final decision so the settlement chain runs on real data. See
#: :func:`_forced_gate`.
FORCE_GATE_MODES = ("off", "apply", "fork")

#: --l2-cert — L2 (ROUNDTRIP_L2) evidence mechanism (SPEC §7.11, "乙+甲").
#: ``auto`` (default) machine-certifies undeclared tool-bucket Level-2 evidence
#: from a candidate's own eval trajectories under ``--manifest-mode repo``;
#: ``off`` keeps today's built-in stage-4 behaviour byte-identical. See
#: :func:`_l2_certifying_gate`.
L2_CERT_MODES = ("auto", "off")
DEFAULT_L2_CERT = "auto"

#: --ship-policy — round-settlement policy (M-17, SPEC §7.14). ``first_wins``
#: (default) is the Algorithm-1 reading and byte-identical to the pre-M-17
#: engine; ``bucket_disjoint`` is the App B.1 (p.34) ranked multi-ship arm. The
#: allowed set is owned by :data:`experiments.variant_pool.engine.SHIP_POLICIES`.
DEFAULT_SHIP_POLICY = "first_wins"

#: The pass-marker replay.py writes as the first line of ``REPLAY.md`` on a
#: passing smoke (``harnessx/meta_harness/replay.py`` ``render_report_md`` ->
#: "# Replay gate passed"; the fail path titles it "# Replay gate failed").
#: Pinned from real artifacts under ``recipe/gaia_evolver/runs/a1pilot2`` — the
#: OURS-v2 processor-bucket certification reads it (see :func:`_make_l2_certifier`).
_REPLAY_PASS_MARKER = "# Replay gate passed"

#: --aegis-digester — Phase A1 of the LLM-AEGIS reconstruction
#: (experiments/docs/REPRO-COMPLETION-PLAN.md). The paper's Digester (§4.3) is
#: LLM-driven; our current adapter is a deterministic approximation.
#: ``deterministic`` (default) keeps the byte-identical :class:`_EvidenceDigester`
#: fallback; ``llm`` routes every FAILED task through one meta-model
#: interpretation call and derives round-level actionability from a further call.
#: Planner and Critic stay deterministic until A2/A3, so the audit's
#: ``llm_aegis_reproduction`` flag stays ``False`` in both modes — only the
#: per-role Digester name flips.
AEGIS_DIGESTER_MODES = ("deterministic", "llm")
DEFAULT_AEGIS_DIGESTER = "deterministic"

#: --aegis-planner — Phase A2 of the LLM-AEGIS reconstruction
#: (experiments/docs/REPRO-COMPLETION-PLAN.md). The paper's Planner (§4.3) is
#: LLM-driven: it builds the mutation landscape ("who fails / what was tried /
#: which edit classes are untried") from the round's digests + prior-ship
#: history and emits the K_t candidate briefs. Our current adapter is a
#: deterministic failure-cluster grouping. ``deterministic`` (default) keeps the
#: byte-identical :class:`_DeterministicPlanner`; ``llm`` routes the round's
#: evidence through one meta-model call (plus one parse-retry) that emits the
#: briefs — an empty landscape short-circuits the round. The Digester can be LLM
#: independently; the Critic stays deterministic, so the audit's
#: ``llm_aegis_reproduction`` flag stays ``False`` in both modes — only the
#: per-role Planner name flips.
AEGIS_PLANNER_MODES = ("deterministic", "llm")
DEFAULT_AEGIS_PLANNER = "deterministic"

#: --aegis-critic — Phase A3 of the LLM-AEGIS reconstruction
#: (experiments/docs/REPRO-COMPLETION-PLAN.md). The paper's Critic (§4.3) is
#: LLM-driven: it audits the Evolver's structured candidate portfolio, emits a
#: ship_ranking for the deterministic gate to inspect, and may request AT MOST
#: ONE revision. Our current adapter is the deterministic portfolio-audit
#: fallback (:class:`experiments.variant_pool.critic.DeterministicCritic`).
#: ``deterministic`` (default) keeps the byte-identical fallback; ``llm`` routes
#: the round's candidates + digests through one meta-model call (plus one
#: parse-retry) that ranks/rejects/requests. The Critic never ships — the
#: deterministic gate stays the sole shipping authority — so ``llm`` only changes
#: the inspection order and the (at most one) revision. Only when Digester,
#: Planner AND Critic are all ``llm`` does the audit's ``llm_aegis_reproduction``
#: flag flip to ``True``; any deterministic role keeps it ``False``.
AEGIS_CRITIC_MODES = ("deterministic", "llm")
DEFAULT_AEGIS_CRITIC = "deterministic"


def _resolve_actionability_threshold(raw: float | None, aegis_digester: str) -> float:
    """Algorithm 1's alpha, defaulted per Digester mode when not given explicitly.

    The recipe's historical implicit default was 1.0 — calibrated for the
    BINARY deterministic fallback digester (a_t ∈ {0.0, 1.0}; "equality
    continues" makes 1.0 the never-skip legacy behavior). An LLM Digester
    emits real-valued a_t, and runs/a1smoke showed a_t=0.9 < 1.0 silently
    skipping every round under the legacy default. Auto default: 1.0 in
    deterministic mode (byte-identical), the library's OURS default (0.5)
    in llm mode; an explicit ``--actionability-threshold`` always wins.
    """
    if raw is not None:
        return float(raw)
    if aegis_digester == "llm":
        return float(OURS_DEFAULT_ACTIONABILITY_THRESHOLD)
    return 1.0

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

#: M-38. The Evolver writes ``file://`` targets for the tools and processors it
#: authors, and the loader in ``harnessx/core`` strips a fixed seven characters
#: (``len("file://")``). An RFC-style third slash therefore leaves a leading
#: ``/`` on a Windows path, which resolves against the current drive and yields
#: ``D:\D:\...`` — the target fails to open.
#:
#: Our own load path repairs this (``_resolve_tool_targets`` /
#: ``_normalise_artefact_node``), so the gate and the active pool are unaffected.
#: What is NOT repaired is the Evolver's *own* smoke test inside
#: ``pipeline/candidates/``: that load goes through the vendored ``Harness``
#: directly. And the failure is silent — ``harness.py`` catches the exception,
#: logs a warning nowhere the meta-agent can see, and returns a registry without
#: the tool. So the Evolver evaluates its candidate without the capability that
#: candidate declares, and cannot know it.
#:
#: That interacts badly with the Level-2 requirement immediately above: a tool
#: candidate must show its output survived serialisation into the next model
#: message. A tool that never registered can never produce that observation, so
#: the tool lever is close to unusable until the Evolver spells the target in the
#: form the loader accepts. This brief is ours, not the paper's published prompt
#: (that is ``PAPER_MANIFEST_SCHEMA_BRIEF``), and it already carries several
#: runtime contracts learned from failed runs; this is one more.
_FILE_TARGET_SPELLING_BRIEF = (
    "FILE TARGET SPELLING (hard requirement): write every `file:` target — for "
    "tools in `tool_registry.custom`, for processor `_target_`, and for any "
    "`template_path` — as `file://<absolute path>::<symbol>`, with EXACTLY TWO "
    "slashes after `file:`. Do not write the RFC three-slash form "
    "`file:///C:/...`. The loader removes a fixed seven characters, so a third "
    "slash leaves a leading `/` that resolves to a doubled drive letter and the "
    "target silently fails to open: the tool will be ABSENT from your own smoke "
    "test, with no error you can observe, and you will be judging your candidate "
    "without the capability it declares — and unable to produce the Level-2 "
    "evidence the gate demands for it. "
)

# --manifest-mode paper — inject the Table 9 (p.36) schema + the C-R10-02 worked
# instance (report §3.3) so a repo-trained meta-agent writes manifest.yaml in
# the paper shape instead of its journal vocabulary. Recipe-layer injection
# only; nothing under ``harnessx/`` is modified.
PAPER_MANIFEST_SCHEMA_BRIEF = (
    "WRITE ORDER (hard requirement): FIRST write `output_dir/config.yaml` and "
    "any `tools/`/`processors/`/`templates/` files your change declares; ONLY "
    "THEN write `_meta_scratch/manifest.yaml` and `candidates.md`. A manifest "
    "that describes a config.yaml you have not actually written is an "
    "automatic failed round (runs/paper1: four attempts died exactly this "
    "way — analysis artefacts complete, config.yaml missing).\n"
    "Write `_meta_scratch/manifest.yaml` as a bare YAML mapping in the paper's "
    "Table 9 schema. Types are STRICT: `capability_evidence` and `file_changes` "
    "are LISTS OF MAPPINGS and `predicted_impact` is a MAPPING — never prose "
    "strings. Do NOT use journal vocabulary (no `levers`/`lens`/`lever`/`intent`/"
    "`predicted_affected` keys; unknown keys are rejected). Required keys/types:\n"
    "  candidate_id: str  # exactly the suggested id\n"
    "  bucket: list[str]  # subset of [prompt, tools, config, processor]\n"
    "  iterates_from: str|null\n"
    "  capability_evidence: list of {type, claim, evidence}  # [] for a pure prompt edit; type MUST be one of [python_package, http_endpoint, builtin_tool, filesystem, other] — any other value is rejected (runs/paper3 died on invented types); use 'other' when unsure\n"
    "  file_changes: list of {path, action(create|modify|delete), diff_summary}\n"
    "  predicted_impact: {tasks_will_unlock: [...], tasks_will_stabilize: [...], tasks_at_risk: [...]}\n"
    "  attribution_signature: {type(tool_call|processor_invocation|prompt_feature), tool_name, expected_min_calls}  # null ONLY when bucket == [prompt]; REQUIRED for any other bucket incl. [prompt, config] (W19 hard gate — runs/paper2 died on this)\n"
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
    "For tools/processor buckets, at least ONE capability_evidence claim MUST "
    "contain the phrase 'Level 2' asserting the tool return survives provider "
    "serialization (the example's second entry is the reference shape) — the "
    "deterministic gate rejects code candidates without it at ROUNDTRIP_L2. "
    "A candidate that ADDS a tool MUST also modify the system prompt template (or "
    "an instruction the worker sees) telling the agent WHEN to use the new tool, "
    "and `predicted_impact.tasks_will_unlock` must list tasks where that trigger "
    "fires — a registered-but-never-invoked tool cannot produce capability evidence "
    "and is rejected at the gate (runs/a1pilot2: two candidates died exactly this "
    "way; the paper's C-R10-02 ships tools+prompt+config together). "
    # M-38 applies to both manifest modes: it is a platform contract about how a
    # path is spelled, not a claim about either schema.
    + _FILE_TARGET_SPELLING_BRIEF
)

# --manifest-mode repo — the caller adapts the meta-agent's repo-native products
# (config diff + journal), so it does not have to write a separate manifest at
# all. This is the default because it hands the meta-agent one artefact to
# produce (config.yaml + its journal entry) instead of two; runs/smoke_fix2
# showed it frequently fails to finish even one (DECISION_REQUIRED.md), so
# demanding a second manifest.yaml on top only compounded the failure.
REPO_MANIFEST_SCHEMA_BRIEF = (
    "You do NOT need to write `_meta_scratch/manifest.yaml`. The caller adapts its "
    "internal manifest from your repo-native products: the `config.yaml` diff and "
    "your journal vocabulary directly — `levers` (subset of configuration/control/"
    "action/instruction), `predicted_affected` (task ids you expect to flip), and "
    "`hypothesis_id`. For prompt/config-only edits, just write `config.yaml` and "
    "your usual journal entry; no `capability_evidence` or paper "
    "`attribution_signature` is needed. EXCEPTION — if your edit adds or modifies "
    "a TOOL or PROCESSOR, the deterministic gate requires declared Level-2 "
    "round-trip evidence and will otherwise reject it at ROUNDTRIP_L2 (this is "
    "how runs/smoke_calib6's tool candidate died). In that case include in your "
    "journal entry (or a manifest.yaml) a structured `capability_evidence:` list "
    "of {type, claim, evidence} mappings with: (1) one entry showing the "
    "capability works (the endpoint/command output you actually verified), and "
    "(2) one entry whose claim contains 'Level 2' asserting the tool return "
    "survives provider serialization to the model, with the observed evidence "
    "(e.g. \"tool output of N chars appeared intact in the next model message\"). "
    "A candidate that ADDS a tool MUST also modify the system prompt template (or "
    "an instruction the worker sees) telling the agent WHEN to use the new tool, "
    "and its `predicted_affected` tasks must be ones where that trigger fires. A "
    "registered-but-never-invoked tool cannot produce capability evidence and is "
    "rejected at the gate (runs/a1pilot2: two candidates died exactly this way; the "
    "paper's C-R10-02 ships tools+prompt+config together). "
    + _FILE_TARGET_SPELLING_BRIEF +
    "Never fabricate: only claim what you observed in this session."
)

# --- Paper App B.1 published prompt text (verbatim; extraction: pypdf, ligature loss possible) ---
#
# P1-1 (OPTIMIZATION-PLAN): the paper (docs/assets/paper/HarnessX_Tech_Report.pdf,
# = arXiv 2606.14249) publishes the AEGIS meta-agent role prompts in Appendix B.1.
# Extracted per-page with pypdf (poppler unavailable). The prompt blocks render in a
# monospace/code font, so NO ligatures were dropped: a scan for ligature codepoints
# (fb00-fb06) and for dropped-ligature words found NONE across all three prompts, so
# there are ZERO ligature repairs to mark. Preserved verbatim from the pypdf slice:
# pagination lines (bare page numbers) were stripped, and pypdf's spacing artifacts
# (e.g. "round ' s", "` code `", "{ % if %}") are kept UN-edited so the constant is
# a faithful copy of the extraction rather than a hand-cleaned paraphrase.
#
# Coverage (paper Appendix B.1, pp.30-34): Planner = FULL; Evolver = published ~60%
# with 3 author truncations; Critic = published ~70% with 2 author truncations.

#: Planner system_prompt.md, verbatim, FULL (paper App B.1, pp.30-31).
_PAPER_APP_B1_PLANNER = """\
# Planner -- Round {{ round }}
Your goal: write a single ` landscape.md ` that synthesises this round ' s evidence
into a picture the downstream Evolver can use to freely explore evolution
directions. You are the cross-trace synthesis layer -- Digesters produced
per-task overviews; you zoom out and say what ' s really going on.
## What the landscape should convey
- Recurring failure modes across this round ' s digests -- your own grouping, not
forced by exact-string matching.
- What was tried in previous rounds (journal.md, data/ship_outcomes.json,
data/rejected_candidates.jsonl, archive/) and which outcomes held up.
- Tasks that persistently failed across rounds (data/task_history.jsonl) and
theories about them that have NOT been tried yet.
- Whether last round ' s ship caused regressions. Read R{{ round }}/regressions.md
first: it is the deterministic, k-aware list of tasks whose pass-state
worsened versus the previous round, with the joint-suspect ships from
R{{ round_minus_1 }} attached. The hit-rate in ship_outcomes.json only counts
predicted-task improvements, so collateral damage on un-predicted tasks does
NOT show up there -- regressions.md is the only place it surfaces. If the file
lists regressions, put them at the top of the landscape (a dedicated
"## Regressions to address" section), name the responsible ship ' s bucket(s),
and flag each in unattempted_directions so the Evolver treats them as
first-class targets.
- What the reputation signal says about which mutation layers have historically
yielded (proposed -> shipped, window): {{ reputation_summary }}
- What scoreboard.json and ship_outcomes.json say about per-bucket hit rates. If
one bucket has shipped 3+ rounds running with flat or declining hit rate while
another has never been tried AND the digests point at failures it could
address, say so: name the neglected bucket and the cluster it would target.
Do not prescribe WHICH mutation to pick -- point at the evidence and let the
Evolver decide the shape.
{ % if round >= 2 %}- Prior Critic ' s strategy_concern, if any. Read
R{{ round_minus_1 }}/decision.md; if its frontmatter has a non-empty
strategy_concern, surface it at the TOP of the landscape, quoted verbatim,
then note whether this round ' s evidence still supports it. The Critic writes
strategy_concern to reach next round ' s Evolver, but the Evolver only reads
landscape.md -- you are the relay.
{ % endif %}
Be evidence-anchored. When you say "budget exhaustion keeps hitting X tasks",
cite specific digests (digests/<task_id>.md) or trajectory anchors
(trajectories/<task>_r0.jsonl#step_N). The Evolver will read what you cite.
Be selective, not exhaustive. Three coherent directions -> list three. One
overwhelming signal -> say so. Your reader decides how many candidates to build;
it benefits from clarity, not volume.
## Where evidence lives
Run root has INDEX.md, a catalog. Typical sources: overview.md (this round ' s
digests + patterns); digests/<task_id>.md (per-task analysis with anchors);
journal.md (prior memos); data/*.jsonl and data/ship_outcomes.json (cross-round
ledgers); archive/ (non-shipped manifests). No required reading list -- pull
what supports the synthesis.
## Output
One file via ` write_tool ` . The body is open-ended markdown; the only structural
expectation is a short YAML frontmatter so the Evolver can find your key
conclusions:
---
round: {{ round }}
top_themes: # your synthesis, free-text tags
- <theme-1>
persistent_failures: # task_ids failed across >=2 rounds
- <task_id>
unattempted_directions: # approaches not tried yet per ship_outcomes
- <short description>
---
## Landscape
<Open narrative. Evidence citations throughout.>"""

#: Evolver system_prompt.md, verbatim, published ~60% (paper App B.1, pp.31-33).
#: Carries the paper's own 3 truncation markers ([... ... truncated ...]).
_PAPER_APP_B1_EVOLVER = """\
# Evolver -- Round {{ round }}
Your goal: produce concrete evolution candidates whose shipping will raise next
round ' s benchmark pass rate. You decide how many candidates (K >= 1) -- one
high-value candidate beats three speculative ones, but if two genuinely
different directions both have strong evidence, produce both. Every candidate
must be evidence-driven with citations to raw traces or digests.
## Your stance
This role is research, not maintenance. Your value is in creative, rigorous,
breakthrough-level thinking -- not in iterating on the bucket the pipeline has
shipped most recently. When evidence points to a structural lever the harness
has never touched -- a new tool, a runloop parameter, a different processor-hook
time point -- propose it, even when the bucket has an empty reputation. Do not
let bucket history, gate-rejection fear, or implementation discomfort narrow
your search. Follow the evidence.
[... strategy-concern relay and revert/improve-prior-ship rules truncated ...]
## Action space is what you can verify exists
The mutation space is bounded by the runtime, the reachable web, and the
harness ' s current capability set. When a direction depends on something beyond
these -- a package, an API endpoint, a tool you assume is installed -- the
system treats unverified dependencies as hallucinations. You have ` bash ` ,
` web_search ` , and ` web_fetch ` to confirm a capability exists before writing code
against it; record the confirmation in ` capability_evidence ` .
## Build -> verify -> iterate (mandatory for code candidates)
For any candidate that introduces new executable code, you MUST complete this
loop IN YOUR SESSION before writing the manifest:
1. Write the code to your scratch dir.
2. Verify by actually running it -- not by reasoning about it. Two levels:
- Level 1 -- unit call works: instantiate the processor/tool, drive the
async hook, assert the expected state mutation happened.
- Level 2 -- round-trip reaches the model: a unit call that returns does not
prove the agent sees the return. Simulate the path from your code to the
model ' s next input and assert the content survives it (provider serializer
for tools; the next pipeline stage for processors).
3. Iterate if verification fails -- fix the bug, or pivot if the environment
does not support what you assumed. Do NOT hide the failure in a try/except.
4. Attach the verifying output as ` capability_evidence ` . "I believe this will
work" is not acceptable; paste the actual command and its output.
A candidate whose new code has not been observed to work will burn a round ' s
ship slot for zero flips. Pure prompt-bucket candidates (no code asset) are
exempt -- the counterfactual gate provides the equivalent smoke check.
[... reading list and write locations truncated ...]
## Manifest shape
Per candidate, emit a manifest at ` {{ candidates_dir }}/C-R{{ round }}-<NN>.md `
and a scratch dir with the applied ` config.yaml ` .
---
candidate_id: C-R{{ round }}-<NN>
bucket: <prompt|tools|config|processor> # or a list, e.g. [prompt, processor]
iterates_from: <prior_ship_id> # OPTIONAL -- set for a revert/improve
capability_evidence: # REQUIRED -- may be empty []
- type: <python_package|http_endpoint|builtin_tool|filesystem|other>
claim: "<the capability this candidate depends on>"
evidence: "<something you OBSERVED this session: command + output snippet>"
file_changes:
- {path: <under scratch dir>, action: <create|modify|delete>, diff_summary: "<one line>"}
predicted_impact:
tasks_will_unlock: [<ALL_FAIL -> expect >=1 rollout to pass>]
tasks_will_stabilize: [<PARTIAL_PASS -> expect all rollouts to pass>]
tasks_at_risk: [<currently >=1 pass -> might regress>]
attribution_signature: # recommended for tools/processor/config
type: <tool_call|processor_invocation>
tool_name: <PascalCase name as registered>
expected_min_calls: 1
---
## Failure Evidence
At least one trajectory or digest anchor per candidate, e.g.
` trajectories/abc123_r0.jsonl#step_5 -- what went wrong here ` .
## Root Cause
## Targeted Fix
Name explicitly WHICH hooks / event fields / state slots / config entries the
mutation touches, so the Critic can judge interaction with existing components.
## Why this won ' t break tasks_at_risk
[... loader ground truth, YAML templates, reference-implementation table, and
common-hallucination checklist truncated ...]"""

#: Critic system_prompt.md, verbatim, published ~70% (paper App B.1, pp.33-34).
#: Carries the paper's own 2 truncation markers ([... ... truncated ...]).
_PAPER_APP_B1_CRITIC = """\
# Critic -- Round {{ round }}
Your goal has two parts, and both matter.
## Part 1 -- Per-candidate verdict
Pick the single candidate (or multiple bucket-disjoint candidates) whose
shipping is most likely to raise next round ' s pass rate without hurting it. If
none qualifies, no-op -- shipping a bad candidate is worse than nothing.
Every verdict MUST explicitly address candidate-vs-config interaction. Read the
candidate ' s ` ## Targeted Fix ` (which hooks / event fields / state slots it
touches) AND the current HarnessConfig. Answer in your verdict: does this
candidate ' s mutation surface overlap with any processor, tool, prompt clause, or
config kwarg already in the parent config? If yes, argue whether the overlap is
(a) intentional and safe (the new component supersedes the old one, which the
candidate ' s applied YAML has removed) or (b) an accidental collision and grounds
for rejection. A verdict that does not address this is incomplete and counts as
ask-more.
[... round-trip (Level-2) evidence check for tool/processor candidates truncated ...]
## Part 2 -- Portfolio audit
Even when every individual candidate is acceptable, step back and look at the
pattern across rounds (scoreboard.json, data/ship_outcomes.json):
- For any lever item shipped in >=2 of the last 3 rounds with cumulative
hit_rate < 0.4, do NOT ship a candidate touching that lever again; flag it as
strategy_concern. A single-round miss is likely k-sampling noise; only
persistence across rounds is signal.
- Is there a bucket or cluster the Evolver has never touched, while a failure
pattern in digests/ suggests it is the right lever? Flag it.
- Did this round ' s regressions.md list any regressed task? The Evolver was
required either to ship a candidate addressing each regression or to write a
"## Why this regression is acceptable" section. Reject the round (no-op) if
neither path was taken, citing the missed task IDs.
Record strategy_concern in decision.md ' s frontmatter only when the evidence is
concrete: name the bucket, the round range, the hit rate, the failing tasks.
Next round ' s Planner relays it to the Evolver. This is how you challenge the
Evolver ' s strategy, not just its candidates.
[... independence rule, available-to-read guide, ask_evolver, and loader ground
truth truncated ...]
## Output
For each candidate, write ` verdicts/V-<candidate_id>.md ` :
---
candidate_id: <C-R{{ round }}-NN>
verdict: <accept|reject|ask-more>
evidence_anchors:
- trajectories/<file>#step_N
---
## Reasoning
<Why this verdict. Cite the anchors. 2-4 short paragraphs.>
After all verdicts, write ` decision.md ` :
---
round: {{ round }}
decision_type: <ship|no_op>
ship_ranking: # candidates to ship, in priority order
- candidate_id: <C-R{{ round }}-NN>
strategy_concern: | # OPTIONAL -- fill only when the audit surfaces one
<one concrete paragraph; cite ship_outcomes / task_history anchors>
---
## Reasoning
<3-6 bullets, one per verdict file, plus one bullet for any strategy_concern.>
Multi-ship: Stage 4 ships every listed candidate in order but skips any whose
bucket was already claimed by an earlier-ranked ship, so bucket-disjoint
candidates attacking orthogonal failure modes can ship together. Nothing ships
unless decision.md parses cleanly."""

#: --aegis-prompts. paper (default, paper-first house rule) drives the LLM roles
#: with the paper's published App B.1 prompts (runtime-adapted only where our
#: JSON/manifest contract requires); ours keeps the byte-identical OURS constants
#: as the ablation arm. Only affects llm-role modes and the always-LLM Evolver.
AEGIS_PROMPTS_MODES = ("paper", "ours")
DEFAULT_AEGIS_PROMPTS = "paper"

#: Marker inserted where a paper truncation is filled by OURS bridging text.
_PAPER_TRUNCATION_BRIDGE_TAG = "[...paper truncation — OURS bridge]"


def _apply_truncation_bridges(text: str, bridges: tuple) -> str:
    """Replace each verbatim paper truncation marker with a marked OURS bridge.

    ``bridges`` is a tuple of ``(paper_marker, ours_text)``; each marker is the
    paper's own ``[... ... truncated ...]`` line and is replaced by
    ``_PAPER_TRUNCATION_BRIDGE_TAG`` + the OURS text that covers the same ground.
    A marker absent from ``text`` is a no-op (guarded by the P1-1 tests, which pin
    the resulting bridge count and the absence of residual truncation markers).
    """
    for marker, ours in bridges:
        text = text.replace(marker, f"{_PAPER_TRUNCATION_BRIDGE_TAG} {ours}")
    return text


#: Evolver truncation bridges (3), each OURS text covering the truncated section.
_PAPER_EVOLVER_BRIDGES = (
    ('[... strategy-concern relay and revert/improve-prior-ship rules truncated ...]', "The Planner relays any prior-round Critic strategy_concern to you through the planner brief below; when it names a lever with a weak cumulative hit rate, do not re-ship that lever. To revert or improve a prior ship, set iterates_from to that ship's candidate id and say why this round's evidence changes the call."),
    ('[... reading list and write locations truncated ...]', "This runtime hands you the round's evidence directly in the injected planner brief (per-task digests, prior-ship history, and any active regressions); you do not fetch a separate reading list. Write the applied config.yaml (and any tools/processors/templates it declares) into your output_dir scratch exactly as the manifest instructions in this brief require."),
    ('[... loader ground truth, YAML templates, reference-implementation table, and\ncommon-hallucination checklist truncated ...]', "The machine-readable manifest shape, the loader's accepted keys, and this runtime's anti-hallucination rules are pinned in the manifest_instructions and decision_contract_requirement fields of this brief; follow those exactly, verify every capability you depend on, and never claim a capability you did not observe."),
)

#: Critic truncation bridges (2).
_PAPER_CRITIC_BRIDGES = (
    ('[... round-trip (Level-2) evidence check for tool/processor candidates truncated ...]', "For any tools or processor candidate, require declared Level-2 round-trip evidence — proof that the tool/processor return survives provider serialization and actually reaches the model. The deterministic gate rejects a code candidate lacking it at ROUNDTRIP_L2, so rank such a candidate last or reject it and say the Level-2 evidence is missing."),
    ('[... independence rule, available-to-read guide, ask_evolver, and loader ground\ntruth truncated ...]', "Judge each candidate independently on its own manifest and the shared round evidence, not on the other candidates' fate. You may request AT MOST ONE revision (see the output contract below). Base every judgement only on the manifests and evidence provided in this runtime."),
)

#: OURS runtime output-format tails. The paper prompts tell the model to WRITE
#: files (landscape.md / verdicts+decision.md); our runtime instead parses one
#: JSON object, so paper mode appends this machine-readable contract. The paper's
#: own output wording is kept above and explicitly takes precedence; the tail only
#: pins the JSON shape our parser (_parse_plan_json / _parse_review_json) requires.
_PAPER_PLANNER_OUTPUT_CONTRACT_TAIL = (
    '\n'
    '\n'
    '## OUTPUT FORMAT (OURS runtime contract)\n'
    "The paper's own output instructions above take precedence in wording and intent. This runtime cannot read a landscape.md file written via write_tool; it parses a SINGLE JSON object from your reply that encodes the same synthesis as up to K_t candidate briefs. So instead of writing a file, return ONLY this JSON object (no prose, no markdown fences):\n"
    '{\n'
    '  "briefs": [\n'
    '    {\n'
    '      "buckets": ["<1+ of: prompt, tools, config, processor>"],\n'
    '      "task_ids": ["<task ids taken ONLY from this round\'s summaries>"],\n'
    '      "rationale": "<one evidence-anchored hypothesis for this single candidate; must be non-empty>"\n'
    '    }\n'
    '  ],\n'
    '  "landscape_notes": "<short synthesis of what was tried and which edit classes remain untried>"\n'
    '}\n'
    'Emit between 0 and K_t briefs; an empty "briefs": [] is legitimate when nothing is addressable by a harness edit this round. Never invent task ids or edit classes. Return the JSON object and nothing else.'
)

_PAPER_CRITIC_OUTPUT_CONTRACT_TAIL = (
    '\n'
    '\n'
    '## OUTPUT FORMAT (OURS runtime contract)\n'
    "The paper's own output instructions above (verdicts/*.md and decision.md) take precedence in wording and intent. This runtime cannot read files you write; it parses a SINGLE JSON object from your reply that is the machine-readable equivalent of that decision. So in ADDITION to the reasoning described above, return ONLY this JSON object (no prose, no markdown fences):\n"
    '{\n'
    '  "ranked_candidate_ids": ["<candidate ids best-first; a permutation of the candidates you did NOT reject>"],\n'
    '  "verdicts": [{"candidate_id": "<id>", "rank": <1-based integer>, "reasons": ["<short justification>"]}],\n'
    '  "rejections": [{"candidate_id": "<id>", "reason": "<why it must not be ranked>"}],\n'
    '  "revision_requests": [{"candidate_id": "<id>", "reason": "<what is wrong>", "instructions": "<concrete fix for the Evolver>"}],\n'
    '  "no_op": <true to stop the whole round, else false>,\n'
    '  "no_op_reasons": ["<non-empty when no_op is true>"],\n'
    '  "strategy_concerns": ["<0+ portfolio-level observations>"]\n'
    '}\n'
    'Use ONLY the candidate ids shown; never invent one. Emit AT MOST ONE revision request (paper section 4.3). '
    'In repo manifest mode, Level-2 round-trip evidence for tool/processor candidates is MACHINE-CERTIFIED downstream at the deterministic gate from the candidate\'s real evaluation trajectories (deviation M-22); do NOT reject a candidate solely because declared Level-2 evidence is missing — record it as a strategy concern instead. '
    'Return the JSON object and nothing else.'
)

#: Assembled paper-mode prompts actually fed to the roles in --aegis-prompts paper.
#: Planner = full verbatim body + OURS JSON tail; Critic = bridged body + OURS JSON
#: tail; Evolver guidance = bridged body (merged into the candidate contract brief,
#: where our existing OURS manifest/decision text already covers output shape).
_PAPER_PLANNER_PROMPT = _PAPER_APP_B1_PLANNER + _PAPER_PLANNER_OUTPUT_CONTRACT_TAIL
_PAPER_CRITIC_PROMPT = (
    _apply_truncation_bridges(_PAPER_APP_B1_CRITIC, _PAPER_CRITIC_BRIDGES)
    + _PAPER_CRITIC_OUTPUT_CONTRACT_TAIL
)
_PAPER_EVOLVER_GUIDANCE = _apply_truncation_bridges(
    _PAPER_APP_B1_EVOLVER, _PAPER_EVOLVER_BRIDGES
)


# ---------------------------------------------------------------------------
# --force-gate plumbing probe (TEMPORARY, default-off)
# ---------------------------------------------------------------------------
#
# The deterministic gate's APPLY/FORK settlement branches (engine._settle_round)
# had never been exercised by real data: organic candidates so far only produced
# REJECT or no-op. This switch overrides the gate's *final* decision so the whole
# downstream settlement chain (pool.fork / journal inheritance / next-round
# multi-variant routing / reporting) runs end-to-end on a real run. It is a
# plumbing probe, NOT a measurement: a forced APPLY/FORK ships a candidate the
# gate did not actually clear, so any score from a forced run is tainted — and
# every artefact says so (pool_report.md banner + experiment.lock.json warning).
# The gate callable is injected into the engine (VariantPoolEngine(..., gate=...)),
# so this lives entirely in the recipe layer; nothing under experiments/ or
# harnessx/ is modified.


def _forced_gate(
    mode: str,
    real_gate: Callable[..., GateResult] = run_gate,
) -> Callable[..., GateResult]:
    """Wrap ``run_gate`` so its final decision can be forced to APPLY/FORK.

    ``mode="off"`` returns ``real_gate`` **unchanged** (identity, not a copy), so
    a normal run's gate path is byte-identical. For ``apply``/``fork`` the
    returned callable mirrors
    :func:`experiments.variant_pool.gate.run_gate`'s signature exactly, always
    runs the real gate first (keeping its full result for the audit), and
    overrides the outcome **only** when the real gate reached stage 5
    (``real.decision is not None``). A candidate that died at stages 1-4
    (manifest incompleteness, canonicalize/smoke/round-trip) is returned
    untouched: a probe must never ship a candidate that failed an integrity
    check.
    """
    if mode not in FORCE_GATE_MODES:
        raise ValueError(f"force_gate mode must be one of {FORCE_GATE_MODES}, got {mode!r}")
    if mode == "off":
        return real_gate

    def _gate(
        candidate: Any,
        parent_config: Any,
        ledger: Any,
        tk_results: Iterable[TaskEval],
        *,
        min_fork: tuple[int, int] = DEFAULT_MIN_FORK,
        regression_baseline: str = REGRESSION_BASELINE_GLOBAL,
        check_manifest: Callable[[Any], list[str]] | None = None,
        check_canonicalize: Callable[[Any, Any], tuple[bool, str]] | None = None,
        check_smoke: Callable[[Any], tuple[bool, str]] | None = None,
        check_roundtrip: Callable[[Any], tuple[bool, str]] | None = None,
    ) -> GateResult:
        # tk_results may be a one-shot iterable; materialise once so the real
        # gate and any synthesis see the same task evals.
        evals = list(tk_results)
        real = real_gate(
            candidate,
            parent_config,
            ledger,
            evals,
            min_fork=min_fork,
            regression_baseline=regression_baseline,
            check_manifest=check_manifest,
            check_canonicalize=check_canonicalize,
            check_smoke=check_smoke,
            check_roundtrip=check_roundtrip,
        )
        # Stages 1-4 failed (integrity check) -> never override, never ship.
        if real.decision is None:
            return real
        if mode == "fork":
            return _force_to_fork(real, evals)
        return _force_to_apply(real)

    return _gate


def _force_to_fork(real: GateResult, evals: list[TaskEval]) -> GateResult:
    """Rewrite a stage-5 result into FORK, synthesising ``improved`` if empty.

    The engine feeds ``gate_result.improved`` to ``pool.fork(parent, improved,
    round)`` as the forked child's task assignment (engine.py:480-487), so a
    forced FORK with an empty ``improved`` would spawn a task-less child. When
    the real gate found no improved task, synthesise the assignment from the
    tasks whose after-state is failed (``n_pass == 0``); if none are failed,
    fall back to all evaluated task ids. The synthesised set is echoed in
    ``archive_reason`` so the fabrication is auditable. ``regressed`` is kept
    from the real result.
    """
    if real.decision is Decision.FORK:
        return real
    if real.improved:
        improved = frozenset(real.improved)
        synthesized: list[str] | None = None
    else:
        failed = {ev.task_id for ev in evals if ev.after[0] == 0}
        if not failed:
            failed = {ev.task_id for ev in evals}
        improved = frozenset(failed)
        synthesized = sorted(improved)
    archive_reason = (
        f"FORCED_GATE(fork): real_decision={real.decision.value}; "
        f"synthesized_improved={synthesized or None}; {real.archive_reason}"
    )
    return GateResult(
        passed=True,
        failed_stage=None,
        decision=Decision.FORK,
        archive_reason=archive_reason,
        improved=improved,
        regressed=frozenset(real.regressed),
    )


def _force_to_apply(real: GateResult) -> GateResult:
    """Rewrite a stage-5 result into APPLY, keeping the real improved/regressed."""
    if real.decision is Decision.APPLY:
        return real
    archive_reason = (
        f"FORCED_GATE(apply): real_decision={real.decision.value}; {real.archive_reason}"
    )
    return GateResult(
        passed=True,
        failed_stage=None,
        decision=Decision.APPLY,
        archive_reason=archive_reason,
        improved=frozenset(real.improved),
        regressed=frozenset(real.regressed),
    )


def _forced_gate_banner(mode: str) -> str:
    """One-line pool_report.md taint banner; empty string when ``off``.

    Empty when off so a normal run's report is byte-identical.
    """
    if mode == "off":
        return ""
    return (
        f"> ⚠ FORCED GATE MODE: {mode} — plumbing probe; decisions are "
        "overridden, results are NOT measurements."
    )


# ---------------------------------------------------------------------------
# --l2-cert L2 machine self-certification (SPEC §7.11 "乙+甲"; default auto)
# ---------------------------------------------------------------------------
#
# repo-mode tool candidates die at gate stage 4 (ROUNDTRIP_L2) because the open
# repo meta-agent will not write ``capability_evidence`` even when the contract
# demands it (forkprobe1 R1; TASK.md:89, an n=1 non-compliance). SPEC §7.11's
# ruling: 甲 — a meta-declared Level-2 entry always wins (and its rate is itself a
# paper data point); 乙 — when the meta did NOT declare it, the recipe certifies
# the capability *mechanically* from the candidate's OWN evaluation trajectories:
# it takes the new tool's REAL recorded output and runs it through the provider's
# REAL serializer (``manifest.check_level2_roundtrip`` + the litellm tool-message
# path), and stage 4 passes/fails on that measurement — never on assertion.
#
# This is measurement, not a softened gate (the 墓碑 old road): an honest reject
# stands when the tool was never invoked (no evidence possible) or the real
# output does not survive serialization (a C-R10-02-class catch, more informative
# than "not declared"). Meta-declared evidence and the non-code exemption keep the
# built-in ``_declared_level2`` behaviour. Injected only for ``--manifest-mode
# repo`` paper-candidate runs; the paper manifest arm is untouched (faithful arm),
# and legacy opaque candidates have no manifest-backed stage 4 to certify. Recorded
# as deviation M-22. The gate callable is injected through the engine's existing
# seam (``VariantPoolEngine(..., gate=...)`` -> ``run_gate(..., check_roundtrip=...)``,
# gate.py:384-410); nothing under experiments/ or harnessx/ is modified.
#
# OURS-v2 (pending morning review): the processor bucket was declared-only in v1
# (a1pilot2: two processor candidates died ``ROUNDTRIP_L2: no Level-2 round-trip
# evidence for bucket=['processor']``). A processor adds no model-visible return to
# serialize, so v1's probe has nothing to certify. v2 certifies a PROCESSOR-only,
# undeclared candidate from REPLAY-EXECUTION evidence instead: reaching the gate
# means evolve's internal replay smoke PASSED with this config, and every
# registered processor is an unconditional run-loop hook (harnessx/core/processor.py),
# so it executed inside the real run. We verify the artifact — the candidate's
# ``_meta_scratch/REPLAY.md`` (sibling of the winning attempt's config, located as
# ``_finalize_slot`` locates manifest.yaml) exists and carries the pass marker. This
# is weaker than the tools serialization probe (real execution evidence, not
# serialization survival). Mixed tools+processor keeps the tools rule: a tool that
# was added still takes the v1 probe and honest-rejects if it was never invoked —
# the processor path never rescues it.


def _real_tool_serializer() -> Callable[[str], Any]:
    """The production Level-2 serializer: the litellm provider's real tool path.

    Mirrors ``LiteLLMProvider.complete()``'s per-message construction for a
    ``role="tool"`` result (``harnessx/providers/litellm_provider.py``) and
    returns the ``content`` field the model would actually read.
    ``to_openai_content`` is the provider's own utility — there is no stub in the
    production path (SPEC §7.11). Imported lazily so importing this recipe (and
    its offline tests) never pulls the provider stack; tests inject a fake
    serializer and never reach here.
    """
    from harnessx.providers._utils import to_openai_content

    def _serialize(tool_output: str) -> Any:
        content = to_openai_content(tool_output)
        tool_msg: dict[str, Any] = {
            "role": "tool",
            "content": "" if content is None else content,
            "tool_call_id": "l2_probe",
            "name": "l2_probe",
        }
        return tool_msg["content"]

    return _serialize


def _l2_target_tool_names(
    manifest: ChangeManifest,
    parent_config: Any,
    candidate_config: Any,
) -> set[str]:
    """Tool names this candidate adds/changes (the set stage 4 must certify).

    Union of two honest sources: manifest ``file_changes`` paths under ``tools/``
    (basename, ``.py`` stripped) and — authoritative for the *registered* name a
    tool is invoked under — ``compute_changeset(parent, candidate).tools_added``
    (the same diff the recipe already uses in :func:`_repo_journal_file_changes`).
    A config that fails to load degrades to the ``file_changes`` names rather than
    raising.
    """
    names: set[str] = set()
    for change in getattr(manifest, "file_changes", None) or []:
        path = str((change or {}).get("path", "")).strip().replace("\\", "/")
        if path.startswith("tools/"):
            leaf = path[len("tools/") :].strip().strip("/").rsplit("/", 1)[-1]
            if leaf.endswith(".py"):
                leaf = leaf[:-3]
            if leaf:
                names.add(leaf)
    if parent_config and candidate_config:
        try:
            from harnessx.core.harness import HarnessConfig
            from harnessx.meta_harness.agent import compute_changeset

            before = HarnessConfig.from_yaml_file(Path(parent_config)).canonicalize()
            after = HarnessConfig.from_yaml_file(Path(candidate_config)).canonicalize()
            for name in compute_changeset(before, after).get("tools_added", []):
                if str(name).strip():
                    names.add(str(name).strip())
        except Exception:  # noqa: BLE001 - degrade to file_changes-derived names
            pass
    return names


def _l2_candidate_sessions_dir(
    recipe: Any,
    candidate_id: str,
    target_variant: str,
) -> Path | None:
    """The candidate's evaluation ``sessions/`` dir, where tool outputs are traced.

    The recipe records ``_round_traj_dir[candidate_id] = <cg>/trajectories`` during
    evaluation (which runs before the gate, engine.py:295 vs 308), and the journal
    writes tool traces to the sibling ``<cg>/sessions``. That lookup is primary; a
    deterministic reconstruction from ``run_dir`` + the round parsed off the
    ``C-R<round>-<NN>`` id is the fallback when the map is unavailable (tests).
    """
    traj = getattr(recipe, "_round_traj_dir", {}).get(candidate_id)
    if traj is not None:
        return Path(traj).parent / "sessions"
    run_dir = getattr(recipe, "run_dir", None)
    match = re.match(r"C-R(\d+)-", str(candidate_id or ""))
    if run_dir is None or match is None or not target_variant:
        return None
    return (
        Path(run_dir)
        / f"R{match.group(1)}"
        / target_variant
        / "candidate_gate"
        / str(candidate_id)
        / "sessions"
    )


def _l2_resolve_tool_content(msg: dict, rec: dict, session_dir: Path) -> str:
    """The tool result text, resolving the journal's large-output externalization.

    Inline results live in ``message.content``; results over the journal's
    ``INLINE_LIMIT`` are written to ``tool_results/{id}.txt`` and referenced by
    ``meta.content_ref`` (journal.py:_prepare_tool_result). Non-string content
    (multimodal) resolves to ``""`` so the probe reports a non-survivable return.
    """
    content = msg.get("content")
    if isinstance(content, str) and content:
        return content
    meta = rec.get("meta")
    if isinstance(meta, dict):
        ref = meta.get("content_ref")
        if ref:
            try:
                return Path(session_dir, ref).read_text(encoding="utf-8")
            except Exception:  # noqa: BLE001 - a missing sidecar is not this tool's output
                pass
    return content if isinstance(content, str) else ""


def _l2_find_tool_output(
    sessions_dir: Path | None,
    targets: set[str],
) -> tuple[str, str] | None:
    """Longest REAL recorded output of any ``targets`` tool, or ``None`` if never called.

    Parses the HarnessJournal session JSONL (``sessions/*/<run>.jsonl``, skipping
    ``*_trace.jsonl``). Tool results are ``raw_tool`` (pre-processor, always
    written) / ``tool`` (post-processor delta) records; the effective delta is
    preferred per ``tool_call_id``. Returns ``(tool_name, output)`` for the
    longest output (the strongest capability evidence; an all-empty tool yields
    ``("name", "")`` which the probe then honestly rejects). ``None`` means no
    invocation was recorded at all.
    """
    if sessions_dir is None or not Path(sessions_dir).is_dir():
        return None
    best_len = -1
    best_name: str | None = None
    best_output: str | None = None
    for jsonl in sorted(Path(sessions_dir).glob("*/*.jsonl")):
        if jsonl.name.endswith("_trace.jsonl"):
            continue
        try:
            text = jsonl.read_text(encoding="utf-8")
        except Exception:  # noqa: BLE001 - unreadable segment is skipped
            continue
        per_call: dict[str, tuple[int, str, str]] = {}
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception:  # noqa: BLE001 - a malformed line is not a tool result
                continue
            if not isinstance(rec, dict) or rec.get("type") not in ("raw_tool", "tool"):
                continue
            msg = rec.get("message")
            if not isinstance(msg, dict) or msg.get("role") != "tool":
                continue
            name = msg.get("name")
            if name not in targets:
                continue
            output = _l2_resolve_tool_content(msg, rec, jsonl.parent)
            call_id = str(msg.get("tool_call_id") or f"__pos_{len(per_call)}")
            priority = 1 if rec.get("type") == "tool" else 0
            prev = per_call.get(call_id)
            if prev is None or priority >= prev[0]:
                per_call[call_id] = (priority, str(name), output)
        for _, name_str, output in per_call.values():
            if len(output) > best_len:
                best_len, best_name, best_output = len(output), name_str, output
    if best_name is None or best_output is None:
        return None
    return best_name, best_output


def _l2_record(
    recipe: Any,
    candidate_id: str,
    *,
    outcome: str,
    tool: str | None,
    output_chars: int | None,
    note: str,
    passed: bool,
    provenance: str = "OURS_machine_certified",
) -> tuple[bool, str]:
    """Write the machine-certification audit into the recipe's candidate meta.

    Records under ``_candidate_meta[candidate_id]["l2_certification"]`` and returns
    the ``(passed, reason)`` pair stage 4 expects. A PASS reason is tagged as ours;
    a FAIL reason is the bare note so the archived ``ROUNDTRIP_L2: <note>`` reads as
    the specific catch (empty return / dropped content / never invoked / no target).
    ``provenance`` defaults to the v1 tag; the OURS-v2 processor path passes
    ``OURS_machine_certified_v2``.
    """
    record = {
        "outcome": outcome,
        "tool": tool,
        "output_chars": output_chars,
        "note": note,
        "provenance": provenance,
    }
    try:
        recipe._candidate_meta.setdefault(candidate_id, {})["l2_certification"] = record
    except Exception:  # noqa: BLE001 - auditing must never break the gate decision
        pass
    if passed:
        return True, f"OURS machine-certified ({outcome}): {note}"
    return False, note


def _l2_certify_processor_replay(
    recipe: Any,
    candidate: Any,
    candidate_id: str,
    manifest: ChangeManifest,
) -> tuple[bool, str]:
    """Stage-4 certification for a PROCESSOR-only candidate (OURS-v2, pending morning review).

    v1's probe certifies a tool's model-visible return by re-running it through the
    provider serializer; a processor adds no such return (it is an unconditional
    run-loop hook, ``harnessx/core/processor.py``), so there is nothing to
    serialize. Instead this certifies from REPLAY-EXECUTION evidence: the candidate
    only reaches this gate because evolve's internal replay smoke PASSED with this
    config, and a passing replay drives the real run loop end to end -> every
    registered processor executed. We verify that artifact: the candidate's
    ``_meta_scratch/REPLAY.md`` — sibling of the winning attempt's ``config.yaml``,
    located exactly as :meth:`VariantPoolRecipe._finalize_slot` locates
    ``manifest.yaml`` (``config_path.parent / "_meta_scratch" / <file>``) — exists
    and carries :data:`_REPLAY_PASS_MARKER`.

    This is weaker than the tools serialization probe (real execution evidence, not
    serialization survival). A missing ``REPLAY.md`` or one without the pass marker
    (e.g. REPLAY_FAIL-style "# Replay gate failed" content) keeps the built-in FAIL
    unchanged (:func:`_declared_level2`) and records nothing, so the archived
    ``ROUNDTRIP_L2`` reason is byte-identical to today's processor rejection.
    """
    config_path = getattr(candidate, "config_path", None)
    if config_path is not None:
        replay_path = Path(config_path).parent / "_meta_scratch" / "REPLAY.md"
        if replay_path.is_file():
            try:
                text = replay_path.read_text(encoding="utf-8")
            except Exception:  # noqa: BLE001 - an unreadable artifact is treated as absent
                text = ""
            if _REPLAY_PASS_MARKER in text:
                return _l2_record(
                    recipe,
                    candidate_id,
                    outcome="certified_processor_replay",
                    tool=None,
                    output_chars=None,
                    note=(
                        "processor executed in passing replay smoke (OURS-v2; weaker "
                        "than the tools serialization probe — real execution "
                        "evidence, not serialization survival)"
                    ),
                    passed=True,
                    provenance="OURS_machine_certified_v2",
                )
    # No passing replay artifact: unchanged built-in FAIL, recording nothing.
    return _declared_level2(manifest)


def _make_l2_certifier(
    recipe: Any,
    candidate: Any,
    parent_config: Any,
    serializer_factory: Callable[[], Callable[[str], Any]],
) -> Callable[[Any], tuple[bool, str]]:
    """Build the stage-4 ``check_roundtrip`` for one candidate (SPEC §7.11).

    Receives the manifest the gate unwraps (gate.py:409). Cases 甲/exempt/opaque
    defer to the built-in :func:`_declared_level2` (meta wins / no code). A
    tool-bucket manifest with no declared Level-2 entry is machine-certified from
    the candidate's real eval-trajectory tool output (v1); a PROCESSOR-only,
    undeclared candidate is certified from replay-execution evidence
    (:func:`_l2_certify_processor_replay`, OURS-v2, pending morning review).
    """

    def _check(manifest: Any) -> tuple[bool, str]:
        # 甲 (declared) / non-code exempt / opaque: keep the built-in stage-4
        # behaviour exactly.
        if (
            not isinstance(manifest, ChangeManifest)
            or manifest.level2_evidence() is not None
            or not manifest.needs_code_verification()
        ):
            return _declared_level2(manifest)

        candidate_id = manifest.candidate_id or str(getattr(candidate, "candidate_id", "") or "")
        target_variant = str(getattr(candidate, "target_variant", "") or manifest.target_variant or "")
        candidate_config = getattr(candidate, "config_path", None)

        # Distinguish tools involvement from processor-only via the same
        # target-derivation logic the tools path uses below.
        targets = _l2_target_tool_names(manifest, parent_config, candidate_config)

        # PROCESSOR-only (OURS-v2, pending morning review): a code candidate that
        # adds/changes NO tool and does not claim the tools bucket. v1's tool-output
        # serialization probe has nothing to probe, so certify from REPLAY-EXECUTION
        # evidence. Mixed tools+processor keeps the tools rule (targets non-empty, or
        # the tools bucket, falls through to the v1 probe; a never-invoked tool still
        # honest-rejects there — the processor path never rescues it).
        if not targets and "tools" not in set(manifest.bucket):
            return _l2_certify_processor_replay(recipe, candidate, candidate_id, manifest)

        # 乙: tools bucket, no declared evidence -> certify from the real run.
        if not targets:
            return _l2_record(
                recipe,
                candidate_id,
                outcome="no_target",
                tool=None,
                output_chars=None,
                note="cannot identify which tool to certify; declare capability_evidence explicitly",
                passed=False,
            )

        sessions_dir = _l2_candidate_sessions_dir(recipe, candidate_id, target_variant)
        found = _l2_find_tool_output(sessions_dir, targets)
        if found is None:
            return _l2_record(
                recipe,
                candidate_id,
                outcome="no_invocation",
                tool=None,
                output_chars=None,
                note="new tool was never invoked during candidate evaluation; no capability evidence possible",
                passed=False,
            )

        tool_name, tool_output = found
        serializer = serializer_factory()
        evidence = check_level2_roundtrip(tool_output, serializer, label=DEFAULT_LEVEL2_LABEL)
        return _l2_record(
            recipe,
            candidate_id,
            outcome="certified" if evidence.survived else "failed_probe",
            tool=tool_name,
            output_chars=len(tool_output),
            note=evidence.note,
            passed=evidence.survived,
        )

    return _check


def _l2_certifying_gate(
    recipe: Any,
    real_gate: Callable[..., GateResult] = run_gate,
    *,
    serializer_factory: Callable[[], Callable[[str], Any]] | None = None,
) -> Callable[..., GateResult]:
    """Wrap ``run_gate`` so undeclared tool-bucket Level-2 evidence is machine-certified.

    Returns ``real_gate`` **unchanged** (identity) unless certification is active —
    ``l2_cert == "auto"`` and ``manifest_mode == "repo"`` and the paper candidate
    mode (legacy opaque candidates never reach a manifest-backed stage 4, so there
    is nothing to certify and the off-mode identity is preserved). When active, the
    returned callable mirrors :func:`run_gate`'s signature and, for a candidate that
    carries a :class:`ChangeManifest` and has no caller-supplied ``check_roundtrip``,
    injects the certifier as stage 4's check (it *replaces* the built-in
    ``_declared_level2``, gate.py:384-410). Opaque candidates and an explicit
    ``check_roundtrip`` pass through untouched.
    """
    active = (
        getattr(recipe, "l2_cert", DEFAULT_L2_CERT) == "auto"
        and getattr(recipe, "manifest_mode", "") == "repo"
        and getattr(recipe, "candidate_mode", "") == "paper"
    )
    if not active:
        return real_gate
    factory = serializer_factory or _real_tool_serializer

    def _gate(
        candidate: Any,
        parent_config: Any,
        ledger: Any,
        tk_results: Iterable[TaskEval],
        *,
        min_fork: tuple[int, int] = DEFAULT_MIN_FORK,
        regression_baseline: str = REGRESSION_BASELINE_GLOBAL,
        check_manifest: Callable[[Any], list[str]] | None = None,
        check_canonicalize: Callable[[Any, Any], tuple[bool, str]] | None = None,
        check_smoke: Callable[[Any], tuple[bool, str]] | None = None,
        check_roundtrip: Callable[[Any], tuple[bool, str]] | None = None,
    ) -> GateResult:
        manifest = candidate if isinstance(candidate, ChangeManifest) else getattr(candidate, "manifest", None)
        if check_roundtrip is None and isinstance(manifest, ChangeManifest):
            check_roundtrip = _make_l2_certifier(recipe, candidate, parent_config, factory)
        return real_gate(
            candidate,
            parent_config,
            ledger,
            tk_results,
            min_fork=min_fork,
            regression_baseline=regression_baseline,
            check_manifest=check_manifest,
            check_canonicalize=check_canonicalize,
            check_smoke=check_smoke,
            check_roundtrip=check_roundtrip,
        )

    return _gate


# ---------------------------------------------------------------------------
# Injectable seams (module level so tests can monkeypatch them offline)
# ---------------------------------------------------------------------------


def _make_journal(sessions_dir: Path):
    """The per-(variant, round) tracer, isolated like ``run.py``'s round journal."""
    from harnessx.tracing.journal import HarnessJournal

    return HarnessJournal(base_dir=str(sessions_dir), export_jsonl=True)


#: Builder keys that name a file the harness must actually be able to open.
#: The Evolver writes these itself, so nothing guarantees they are well-formed.
_ARTEFACT_PATH_KEYS = ("template_path",)


def _as_local_path(value: str) -> Path:
    """Coerce a config path value to a local filesystem path.

    The Evolver has been observed writing ``file:///D:/...`` URIs into
    ``template_path``. Nothing validated them, the URI never opened, and the
    resulting ``OSError`` was swallowed by the processor-crash handler -- so the
    agent silently ran with an *empty* system prompt instead of its evolved one.

    All four Windows spellings have been seen in real configs and all must
    resolve to the same file -- ``file:///D:\\x``, ``file:///D:/x`` (both written
    by the Evolver), plus the two-slash ``file://D:\\x`` / ``file://D:/x`` that
    :func:`_resolve_tool_targets` emits and ``to_yaml_file`` then persists for a
    later round to read back. ``urlparse`` cannot do this: on the two-slash form
    it reads the drive letter as a netloc and drops it.
    """
    if not value.startswith("file:"):
        return Path(value)
    from urllib.parse import unquote

    rest = unquote(value[len("file:") :])
    body = rest.lstrip("/")
    if re.match(r"^[A-Za-z]:", body):
        return Path(body)  # windows absolute, whatever the slash count
    return Path("/" + body) if body else Path(rest)


def _assert_processor_instantiates(spec: Mapping, raw_target: str, config_path: Path) -> None:
    """Fail closed unless an evolved processor really does build.

    A readable file is *not* the invariant we care about. The invariant is that
    the processor ends up in the pipeline, and there are two ways for it not to:
    the path fails to resolve, or the config passes kwargs the referenced class
    version does not accept. s1k8b103 R10/V4 is the second kind -- it names
    ``CommitNudgeProcessor`` from C-R6-01 while passing a ``nudge=`` argument
    that build does not take, so instantiation raises ``TypeError``.

    Either way ``_instantiate_proc`` catches it with a bare ``except: return
    None`` and logs nothing at all, so the variant runs the stock stack while
    its config claims otherwise. Checking with the *same call the runtime makes*
    is the only check that covers both causes.
    """
    from harnessx.core.builder import _instantiate

    try:
        _instantiate(dict(spec))
    except Exception as exc:  # noqa: BLE001 - re-raised with provenance below
        raise RuntimeError(
            f"{config_path}: evolved processor {raw_target!r} does not instantiate "
            f"({type(exc).__name__}: {exc}). The runtime drops such processors silently "
            "(_instantiate_proc swallows the exception and returns None), so the variant "
            "would run the stock processor stack while its config claims otherwise."
        ) from exc


def _normalise_artefact_node(node: Any, config_path: Path) -> tuple[Any, bool]:
    """Recursively fix the artefact references in one processor spec.

    Two kinds, both of which the Evolver writes as ``file://`` URIs:

    * ``template_path`` -- the prompt file a ``SystemPromptProcessor`` renders.
      Rewritten to a plain local path, which is what the builder wants.
    * ``_target_`` -- the *processor class itself*, loaded from an evolved .py.
      Rewritten to ``file://<abs path>::<ClassName>``, because
      ``harnessx.core.builder._parse_file_target`` strips the scheme with a bare
      ``_target[len("file://"):]`` and an RFC-style third slash leaves ``/D:\\x``
      in front of the drive, which Windows resolves as ``D:\\D:\\x``.

    Recurses because ``builder._instantiate`` instantiates nested specs too, so
    a ``_target_`` can sit below the top level of a processor entry.

    Fail-closed on both. Returns ``(node, changed)`` and never mutates the input.
    """
    if isinstance(node, list):
        out, changed = [], False
        for item in node:
            fixed, item_changed = _normalise_artefact_node(item, config_path)
            out.append(fixed)
            changed |= item_changed
        return (out, True) if changed else (node, False)

    if not isinstance(node, Mapping):
        return node, False

    patched: dict | None = None

    def _set(key: str, value: Any) -> None:
        nonlocal patched
        patched = patched if patched is not None else dict(node)
        patched[key] = value

    for key, raw in node.items():
        if key in _ARTEFACT_PATH_KEYS and raw:
            path = _as_local_path(str(raw))
            if not path.is_file():
                raise FileNotFoundError(
                    f"{config_path}: {key}={raw!r} does not resolve to a readable file "
                    f"(tried {path}). The evolved artefact this variant is defined by is "
                    "missing, so the run would silently fall back to an empty system prompt."
                )
            if str(path) != str(raw):
                _set(key, str(path))
            continue

        if key == "_target_" and isinstance(raw, str) and raw.startswith("file:"):
            uri_part, sep, symbol = raw.rpartition("::")
            if not sep or not symbol.strip() or not uri_part.strip():
                raise ValueError(
                    f"{config_path}: processor target {raw!r} is malformed "
                    "(expected 'file://<abs path>.py::ClassName')."
                )
            path = _as_local_path(uri_part)
            if not path.is_file():
                raise FileNotFoundError(
                    f"{config_path}: processor target {raw!r} does not resolve to a "
                    f"readable file (tried {path}). The evolved processor this variant "
                    "is defined by is missing, and the runtime drops such processors "
                    "silently -- see _instantiate_proc's bare 'except: return None'."
                )
            fixed = f"file://{path}::{symbol.strip()}"
            if fixed != raw:
                _set(key, fixed)
            _assert_processor_instantiates(
                {**(patched if patched is not None else dict(node)), key: fixed}, raw, config_path
            )
            continue

        if isinstance(raw, (Mapping, list)):
            child, child_changed = _normalise_artefact_node(raw, config_path)
            if child_changed:
                _set(key, child)

    return (patched, True) if patched is not None else (node, False)


def _resolve_artefact_paths(processors: Any, config_path: Path) -> list:
    """Normalise, then **verify**, every evolved artefact a config points at.

    Fail-closed by design. A config naming an artefact that cannot be opened is
    a broken variant, not a variant that quietly falls back to a default: the
    evolved prompt *is* the variant. Raising here converts an invisible
    degradation into a loud one at load time.

    The ``_target_`` case is the one that most needs this. A prompt that fails
    to open at least logs a processor crash, and a tool that fails to load logs
    a warning; an evolved *processor* whose file cannot be opened is swallowed
    by ``_instantiate_proc``'s bare ``except: return None`` and leaves **no
    trace at all**. Measured in s1k8b103: 19 active-pool configs declared an
    evolved processor that was simply absent at runtime, with the instantiated
    set byte-identical to the stock baseline.
    """
    resolved = []
    for processor in processors or []:
        fixed, _ = _normalise_artefact_node(processor, config_path)
        resolved.append(fixed)
    return resolved


def _resolve_tool_targets(tool_registry: Any, config_path: Path) -> Any:
    """Rewrite ``tool_registry.custom`` file targets into the only form that loads.

    Same failure as :func:`_resolve_artefact_paths`, on the other delivery path.
    The Evolver writes evolved tools as ``file://`` targets, and the loader in
    ``harnessx.core.harness`` parses them with a bare
    ``target[len("file://"):]``. On Windows that leaves the leading slash of an
    RFC-style URI in front of the drive -- ``/D:\\x`` -- which resolves against
    the current drive as ``D:\\D:\\x`` and raises ``[Errno 22]``. The loader logs
    that at WARNING and continues, so the variant runs *without* the tool its
    evolution added.

    Measured before the fix: 466 such failures in s1k8b103 (including
    ``R2``/``R4`` active-pool configs for V1, i.e. the pool itself, not just
    candidates), 40 in s2k8b50 and 14 in b_smoke -- the latter being every one
    of V1's 14 sessions.

    ``harnessx/`` is vendored and not ours to patch, so the rewrite happens
    here: emit ``file://<abs path>::<symbol>``, the two-slash spelling that
    survives the bare prefix strip. Verified empirically against the vendored
    parser; the three-slash spellings fail for both slash directions.

    Fail-closed for the same reason as the prompt path: a tool the variant is
    defined by that cannot be opened makes it a broken variant, not a quieter
    one. Dotted module targets are returned untouched.
    """
    custom = getattr(tool_registry, "custom", None)
    if not custom:
        return tool_registry

    patched: list = []
    changed = False
    for target in custom:
        if not isinstance(target, str) or not target.startswith("file:"):
            patched.append(target)
            continue
        uri_part, sep, symbol = target.rpartition("::")
        if not sep or not symbol.strip() or not uri_part.strip():
            raise ValueError(
                f"{config_path}: tool target {target!r} is malformed "
                "(expected 'file://<abs path>.py::symbol')."
            )
        path = _as_local_path(uri_part)
        if not path.is_file():
            raise FileNotFoundError(
                f"{config_path}: tool target {target!r} does not resolve to a readable "
                f"file (tried {path}). The evolved tool this variant is defined by is "
                "missing, so the run would silently hand the model an incomplete tool set."
            )
        fixed = f"file://{path}::{symbol.strip()}"
        changed |= fixed != target
        patched.append(fixed)

    if not changed:
        return tool_registry
    import dataclasses

    return dataclasses.replace(tool_registry, custom=patched)


def _prepare_round_config(config_path: Path, journal: Any):
    """Load a config YAML and attach this round's tracer (``run.py`` idiom).

    Mirrors ``run.py``: ``HarnessConfig.from_yaml_file(...).canonicalize()`` then
    ``.copy(tracer=...)``. Kept as a module-level function so the unit tests can
    replace it without a real ``HarnessConfig`` (they never run a rollout).

    Every artefact path the config names is normalised and verified here -- see
    :func:`_resolve_artefact_paths` (system prompts) and
    :func:`_resolve_tool_targets` (custom tools) for why both are fail-closed.
    """
    from harnessx.core.harness import HarnessConfig

    cfg = HarnessConfig.from_yaml_file(config_path).canonicalize()
    return cfg.copy(
        tracer=journal,
        processors=_resolve_artefact_paths(cfg.processors, config_path),
        tool_registry=_resolve_tool_targets(cfg.tool_registry, config_path),
    )


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
    paper_evolver_guidance: str | None = None,
) -> dict[str, Any]:
    """Assemble the ``candidate_contract`` our recipe injects into ``TASK.md``.

    P1-1: in ``--aegis-prompts paper`` the recipe passes ``paper_evolver_guidance``
    — the paper's published App B.1 Evolver prompt, merged with OURS bridges over
    its truncations — which is added to the brief under ``paper_evolver_guidance``.
    It is ``None`` in ``ours`` mode, and when ``None`` the brief is byte-identical
    to the pre-P1-1 output.

    All injection is recipe-layer: the contract is carried on
    :class:`~recipe.gaia_evolver.variant_pool_meta_agent.VariantPoolMetaAgent`
    (via ``set_candidate_contract``) and rendered by its
    ``_render_candidate_contract`` override, which appends the section to the
    base brief and serialises this ``planner_brief`` verbatim as JSON. Nothing
    under ``harnessx/`` is touched. Carries (1) the mode-specific manifest
    instructions (paper Table 9 schema + C-R10-02 example, or the repo-native
    "no manifest needed" note) for fault (1), and (2) the B4 decision-contract
    emphasis for fault (2). On a retry it also carries the prior
    ``DECISION_REQUIRED.md`` text.
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
    # P1-1 paper mode: merge the paper's App B.1 Evolver guidance into the brief
    # the meta-agent (the always-LLM Evolver) reads. Absent in ours mode, keeping
    # the brief byte-identical to the pre-P1-1 output.
    if paper_evolver_guidance:
        brief["paper_evolver_guidance"] = paper_evolver_guidance
    return {
        # The id the meta-agent must use verbatim is the repo-gate-safe ALIAS
        # of our paper-shape slot id: the repo's own validate_workflow scans
        # candidates.md for `C-\d+` (digits only) and rejected `C-R1-01`
        # outright (runs/smoke_hard). Everything on our side stays keyed by
        # the slot id; the manifest intake maps an echoed alias back.
        "suggested_candidate_id": outward_candidate_id(suggested_candidate_id),
        "target_variant": target_variant,
        "planner_brief": brief,
    }


def _planner_brief_with_regressions(
    brief: Mapping[str, Any],
    regressions: Sequence[str],
) -> dict[str, Any]:
    """Surface the active pool's regressions in the meta-agent's planner brief.

    ``PipelineContext.regressions`` already reaches the DeterministicCritic, but
    nothing tells the meta-agent which previously-solved tasks regressed, so its
    journal/manifest cannot name them and the Critic vetoes the whole round
    (runs/forceprobe1 R2: "regressions were neither handled in tasks_at_risk nor
    explained"). This merges that list into the brief the meta-agent actually
    reads — ``_build_candidate_contract`` renders ``planner_brief`` verbatim into
    ``TASK.md`` — so the meta-agent can list each regressed task in
    ``tasks_at_risk`` or explain it and clear the Critic's whole-round veto.

    Byte-stable when empty: with no regressions the result is exactly
    ``dict(brief)`` (no new keys), so regression-free rounds keep their previous
    brief verbatim.
    """
    merged = dict(brief)
    if not regressions:
        return merged
    tasks = list(regressions)
    merged["active_regressions"] = tasks
    merged["regression_requirement"] = (
        "These previously-solved tasks regressed in the last settled round: "
        f"{tasks}; your journal/manifest MUST either list each of them in "
        "tasks_at_risk / predicted_impact.tasks_at_risk or give an explicit "
        "regression explanation, because the Critic rejects the whole round "
        'otherwise ("regressions were neither handled in tasks_at_risk nor '
        'explained").'
    )
    return merged


def _planner_brief_with_revision(
    brief: Mapping[str, Any],
    revision: Mapping[str, str] | None,
) -> dict[str, Any]:
    """Surface a Critic revision request in the meta-agent's planner brief (A3).

    When the Critic (paper §4.3) asks for the single allowed revision, the
    meta-agent must be told *what* to fix. This merges the Critic's ``reason``
    and ``instructions`` into the brief ``_build_candidate_contract`` renders
    verbatim into ``TASK.md`` (key ``critic_revision_request``), exactly the
    merge pattern :func:`_planner_brief_with_regressions` uses.

    Byte-stable when absent: with no revision (``None`` or empty) the result is
    exactly ``dict(brief)`` (no new key), so an ordinary proposal keeps its brief
    verbatim and the non-revision path stays byte-identical.
    """
    merged = dict(brief)
    if not revision:
        return merged
    merged["critic_revision_request"] = {
        "reason": str(revision.get("reason", "") or ""),
        "instructions": str(revision.get("instructions", "") or ""),
    }
    return merged


#: A revision slot id (``C-R1-01-revision-01``, allocated by
#: :meth:`experiments.variant_pool.candidate_pipeline.IsolatedEvolverAdapter.revise`).
#: Kept as an OWN recipe constant rather than reaching into candidate_pipeline's
#: private ``_PAPER_SLOT_ID``.
_REVISION_SLOT_ID = re.compile(r"^C-R(\d+)-(\d+)-revision-(\d+)$")


def _revision_manifest_candidate_id(slot: "CandidateSlot") -> str:
    """A gate-valid paper-shape candidate id for one revised candidate.

    The revision slot id ``C-R1-01-revision-01`` does NOT match the manifest's
    ``^C-R\\d+-\\d{2,}$`` id contract (``manifest.CANDIDATE_ID_RE``), so a revised
    manifest keyed by the raw slot id would be rejected at gate stage 1
    (``validate_complete``) and the pipeline's revision path could never actually
    settle. Pack the parent slot ordinal and the revision ordinal into one valid
    ``NN``: parent ``C-R1-01`` revision ``01`` -> ``C-R1-0101`` — it matches the
    id regex, keeps the round prefix the pipeline checks
    (``expected_round``), and is DISTINCT from the parent so the revised
    manifest's ``iterates_from`` can point at the parent without a self-loop.
    Degrades to the raw slot id when the slot is not a revision slot.
    """
    match = _REVISION_SLOT_ID.match(slot.suggested_candidate_id)
    if match is None:
        return slot.suggested_candidate_id
    round_idx = int(match.group(1))
    parent_slot = int(match.group(2))
    revision = int(match.group(3))
    return f"C-R{round_idx}-{parent_slot:02d}{revision:02d}"


def _composed_pipeline_adapter(
    digester_mode: str,
    planner_mode: str,
    critic_mode: str,
    *,
    all_deterministic_literal: str,
    prompts_mode: str,
) -> str:
    """One truthful ``candidate_pipeline_adapter`` string across all three roles.

    A3 unifies the digester-only-aware provenance flagged in commit a4eeae7. When
    Digester, Planner and Critic are ALL deterministic the caller's
    ``all_deterministic_literal`` is returned UNCHANGED so the pre-A1 string stays
    byte-identical (the composed ``digester=...,planner=...,critic=...`` form is
    not byte-identical to that literal, so the ruling is: keep the literal for the
    all-deterministic case, use the composed form only when a role is ``llm``).
    The Evolver is always the LLM MetaAgent, echoed as the ``+llm_metaagent_evolver``
    suffix exactly like the old literal. P1-1: when ANY of the three roles is
    ``llm`` the active ``--aegis-prompts`` mode is appended as
    ``,prompts=<paper|ours>``. The all-deterministic literal is left UNCHANGED
    (no prompts suffix): its byte-identity is preserved regardless of the prompts
    flag — the always-LLM Evolver's prompt mode is still recorded, but in the
    audit dict's dedicated ``aegis_prompts`` field rather than in this literal.
    """
    modes = (digester_mode, planner_mode, critic_mode)
    if all(mode == "deterministic" for mode in modes):
        return all_deterministic_literal
    return (
        f"digester={digester_mode},planner={planner_mode},"
        f"critic={critic_mode}+llm_metaagent_evolver,prompts={prompts_mode}"
    )


def _digest_prior_ship_history(digest: TaskDigest) -> str:
    """This task's prior outcomes + shipped candidate ids per round, from the digest.

    Reads ``digest.prior_history`` — the cross-round continuity the EvidenceStore
    attaches at write time (``ships`` = what was already tried). This is the
    "prior-ship history the recipe can already reach"; no store is queried.
    Shared by the A2 Planner and the A3 Critic so both read the same source.
    """
    entries: list[str] = []
    for entry in digest.prior_history:
        if not isinstance(entry, dict):
            continue
        round_idx = entry.get("round_idx")
        solved = entry.get("solved")
        category = entry.get("failure_category")
        ships = [str(ship) for ship in (entry.get("ships") or [])]
        ship_str = ",".join(ships) if ships else "no_ship"
        outcome = "solved" if solved else "failed"
        category_str = f"/{category}" if category else ""
        entries.append(f"R{round_idx}:{outcome}{category_str} ships=[{ship_str}]")
    return "; ".join(entries)


def _repo_journal_file_changes(
    current_config_path: Path,
    new_config_path: Path,
) -> list[dict[str, str]]:
    """Honest ``file_changes`` for a manifest adapted without a manifest.yaml.

    ``config.yaml`` is always listed: the caller already rejected byte-identical
    output, so the config provably changed. When both configs load as
    ``HarnessConfig``s the structural changeset (added/removed tools, processors
    and templates) is appended so the record is faithful; any load failure
    degrades to the ``config.yaml``-only entry rather than fabricating changes.
    """
    changes: list[dict[str, str]] = [
        {
            "path": "config.yaml",
            "action": "modify",
            "diff_summary": (
                "config.yaml changed vs current_config (repo-native manifest adaptation)"
            ),
        }
    ]
    try:
        from harnessx.core.harness import HarnessConfig
        from harnessx.meta_harness.agent import compute_changeset

        before = HarnessConfig.from_yaml_file(Path(current_config_path)).canonicalize()
        after = HarnessConfig.from_yaml_file(Path(new_config_path)).canonicalize()
        diff = compute_changeset(before, after)
    except Exception:  # noqa: BLE001 - degrade to the config.yaml-only record
        return changes

    for name in diff.get("tools_added", []):
        changes.append(
            {"path": f"tools/{name}", "action": "create", "diff_summary": f"add tool {name}"}
        )
    for name in diff.get("tools_removed", []):
        changes.append(
            {"path": f"tools/{name}", "action": "delete", "diff_summary": f"remove tool {name}"}
        )
    for name in diff.get("processors_added", []):
        changes.append(
            {"path": f"processors/{name}", "action": "create", "diff_summary": f"add processor {name}"}
        )
    for name in diff.get("processors_removed", []):
        changes.append(
            {"path": f"processors/{name}", "action": "delete", "diff_summary": f"remove processor {name}"}
        )
    for name in diff.get("processors_config_changed", []):
        changes.append(
            {"path": f"processors/{name}", "action": "modify", "diff_summary": f"retune processor {name}"}
        )
    for name in diff.get("templates_added", []):
        changes.append(
            {"path": f"templates/{name}", "action": "create", "diff_summary": f"add template {name}"}
        )
    for name in diff.get("templates_changed", []):
        changes.append(
            {"path": f"templates/{name}", "action": "modify", "diff_summary": f"edit template {name}"}
        )
    for name in diff.get("templates_removed", []):
        changes.append(
            {"path": f"templates/{name}", "action": "delete", "diff_summary": f"remove template {name}"}
        )
    return changes


def _repo_manifest_from_journal(
    *,
    memo_path: Path,
    current_config_path: Path,
    new_config_path: Path,
    candidate_id: str,
    target_variant: str,
) -> ChangeManifest:
    """Adapt a repo-native manifest when the meta-agent wrote no manifest.yaml.

    Repo mode does not require ``manifest.yaml``; the meta-agent only has to
    write ``config.yaml`` and its usual journal entry (both already required by
    the base brief). This assembles the same journal vocabulary the
    ``--manifest-mode repo`` adapter accepts, drawn from the two repo-native
    products:

    * the latest journal entry at ``memo_path`` -> ``levers`` (edit buckets),
      ``predicted_affected`` (the flip claim), and ``hypothesis_id``;
    * the config diff -> ``file_changes`` (see :func:`_repo_journal_file_changes`).

    The result carries ``provenance="repo_journal"`` exactly like the
    manifest.yaml path, so the deterministic seesaw stays the shipping authority
    and the same paper-only fields are relaxed. Nothing is fabricated: absent
    journal fields simply stay empty and surface at ``validate_complete``.
    """
    from harnessx.meta_harness.journal import latest_entry

    mapping: dict[str, Any] = {
        "candidate_id": candidate_id,
        "target_variant": target_variant,
        "file_changes": _repo_journal_file_changes(current_config_path, new_config_path),
    }
    entry = latest_entry(Path(memo_path))
    if entry is not None:
        if entry.levers:
            mapping["levers"] = list(entry.levers)
        if entry.predicted_affected:
            mapping["predicted_affected"] = list(entry.predicted_affected)
        if entry.hypothesis_id:
            mapping["hypothesis_id"] = entry.hypothesis_id
    return adapt_repo_journal_manifest(
        yaml.safe_dump(mapping, sort_keys=False, allow_unicode=True),
        fallback_candidate_id=candidate_id,
        fallback_target_variant=target_variant,
    )


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
    paper_evolver_guidance: str | None = None,
    commit_bounce: str = "off",
    bounce_max_steps: int = _BOUNCE_MAX_STEPS,
    bounce_audit: "dict[str, Any] | None" = None,
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
            paper_evolver_guidance=paper_evolver_guidance,
        )
        # ``evolve``'s signature is upstream and cannot take the contract, so we
        # set it on the (subclass) agent immediately before the call. Each
        # attempt updates it (retries carry the prior DECISION_REQUIRED text).
        slot_agent.set_candidate_contract(contract)
        try:
            new_yaml = await slot_agent.evolve(
                output_dir=attempt_dir,
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
            # F-A (--evolve-commit-bounce on): retries are exhausted and the slot
            # STILL produced no config.yaml. Before failing, give it exactly one
            # short, tiny-budget "commit a decision now" bounce. Only a genuine
            # no-config outcome (DECISION_REQUIRED.md present) is eligible — a raw
            # error (timeout, validator failure) is never bounced. When
            # commit_bounce == "off" this whole block is skipped and the original
            # ``raise`` runs verbatim, so the default path is byte-identical.
            if commit_bounce == "on" and decision_path.is_file():
                bounce_feedback = decision_path.read_text(encoding="utf-8")
                bounce_yaml = await _run_commit_bounce(
                    slot_agent=slot_agent,
                    slot=slot,
                    manifest_mode=manifest_mode,
                    target_variant=target_variant,
                    planner_brief=planner_brief,
                    base_evolve_kwargs=base_evolve_kwargs,
                    decision_feedback=bounce_feedback,
                    paper_evolver_guidance=paper_evolver_guidance,
                    bounce_max_steps=bounce_max_steps,
                )
                if bounce_audit is not None:
                    bounce_audit["bounce_used"] = True
                    bounce_audit["bounce_outcome"] = (
                        "shipped_via_bounce" if bounce_yaml is not None else "still_missing"
                    )
                if bounce_yaml is not None:
                    return _EvolveOutcome(
                        config_path=Path(bounce_yaml),
                        attempts=attempt + 2,  # + the one bounce invocation
                        retries=attempt,
                        decision_required_history=(*decision_history, bounce_feedback),
                    )
            raise
        return _EvolveOutcome(
            config_path=Path(new_yaml),
            attempts=attempt + 1,
            retries=attempt,
            decision_required_history=tuple(decision_history),
        )
    raise AssertionError("unreachable: retry loop exited without return/raise")


#: The bounce directive injected into the meta-agent's ``TASK.md`` (F-A). Its
#: wording deliberately reuses the ``DECISION_REQUIRED.md`` generator's two-valid-
#: endings phrasing (``harnessx/meta_harness/agent.py`` ``_write_missing_config_findings``:
#: "Choose exactly one ... Either choice is valid; missing config.yaml is not").
_COMMIT_BOUNCE_DIRECTIVE = (
    "FINAL COMMIT STEP (bounce). This is a short, strictly bounded continuation "
    f"(<= {_BOUNCE_MAX_STEPS} steps). Do NOT start new analysis or research. Your "
    "previous session ended with analysis but never wrote `config.yaml`. End THIS "
    "turn by choosing exactly one valid ending: (1) SHIP — write "
    "`output_dir/config.yaml` with your best change; or (2) EXPLICIT NO-OP — copy "
    "the current config byte-for-byte with `cp <current_config> "
    "output_dir/config.yaml`. Either choice is valid; a missing `config.yaml` is not."
)


async def _run_commit_bounce(
    *,
    slot_agent: Any,
    slot: "CandidateSlot",
    manifest_mode: str,
    target_variant: str,
    planner_brief: Mapping[str, Any],
    base_evolve_kwargs: Mapping[str, Any],
    decision_feedback: str,
    paper_evolver_guidance: str | None,
    bounce_max_steps: int,
) -> "Path | None":
    """One short, tiny-budget continuation asking the meta-agent to commit a decision.

    F-A. ``MetaAgent.evolve`` runs a *fresh* session each call and exposes no
    resume seam, so the mechanically-reliable "continuation" is a fresh evolve
    that (a) carries the prior session's ``DECISION_REQUIRED.md`` text — which
    includes the meta-agent's own last-assistant excerpt, i.e. where it stalled —
    back through the existing ``prior_decision_required_feedback`` contract seam,
    (b) adds the explicit two-valid-endings :data:`_COMMIT_BOUNCE_DIRECTIVE`, and
    (c) runs under a shrunken step budget (``<= bounce_max_steps``). It writes to
    an isolated ``<slot>/bounce`` dir so a partial write cannot contaminate the
    base slot. Returns the produced ``config.yaml`` path, or ``None`` if the
    bounce also failed to commit (the caller then takes the normal failure path).
    Never raises — a failed bounce must not become a new failure mode.
    """
    bounce_dir = Path(slot.output_dir) / "bounce"
    bounce_dir.mkdir(parents=True, exist_ok=True)
    bounce_brief = dict(planner_brief)
    bounce_brief["commit_bounce_directive"] = _COMMIT_BOUNCE_DIRECTIVE
    contract = _build_candidate_contract(
        manifest_mode=manifest_mode,
        suggested_candidate_id=slot.suggested_candidate_id,
        target_variant=target_variant,
        planner_brief=bounce_brief,
        decision_feedback=decision_feedback,
        paper_evolver_guidance=paper_evolver_guidance,
    )
    slot_agent.set_candidate_contract(contract)

    # Shrink the step budget for the bounce (best-effort: a stub agent may not
    # expose ``max_steps``). Restored in ``finally`` so the slot agent is left as
    # it was found.
    prev_steps = getattr(slot_agent, "max_steps", None)
    if isinstance(prev_steps, int) and prev_steps > 0:
        try:
            slot_agent.max_steps = min(int(bounce_max_steps), prev_steps)
        except Exception:  # noqa: BLE001 - budget shrink is best-effort
            pass
    try:
        new_yaml = await slot_agent.evolve(
            output_dir=bounce_dir,
            **dict(base_evolve_kwargs),
        )
    except Exception as exc:  # noqa: BLE001 - a failed bounce is not fatal
        logger.warning(
            "[%s] commit bounce did not produce config.yaml: %s",
            slot.suggested_candidate_id,
            exc,
        )
        return None
    finally:
        if isinstance(prev_steps, int):
            try:
                slot_agent.max_steps = prev_steps
            except Exception:  # noqa: BLE001
                pass
    return Path(new_yaml) if new_yaml is not None else None


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


def _latest_settled_digests(
    evidence: EvidenceStore,
    pool: VariantPool,
    context: PipelineContext,
) -> tuple[TaskDigest, ...] | None:
    """The latest settled per-task digest for the target's routed tasks.

    Shared enumeration behind BOTH Digester adapters. Reusing it is what makes
    the ``llm`` arm read exactly the tasks and (n_pass, n_att) outcomes the
    deterministic :class:`_EvidenceDigester` does: the LLM only *interprets*
    failures, it never re-derives the outcome (which comes from the harness).
    Returns ``None`` when the selected target is no longer in the pool (the same
    condition the deterministic adapter reports as "no longer active").
    """
    variant = pool.variants.get(context.target_variant)
    if variant is None:
        return None
    routed = set(variant.routed_tasks)
    latest: dict[str, TaskDigest] = {}
    for digest in evidence.iter_digests():
        if digest.round_idx >= context.round_idx or digest.task_id not in routed:
            continue
        previous = latest.get(digest.task_id)
        if previous is None or (digest.round_idx, digest.variant_id) > (
            previous.round_idx,
            previous.variant_id,
        ):
            latest[digest.task_id] = digest
    return tuple(latest[task_id] for task_id in sorted(latest))


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
        digests = _latest_settled_digests(self.evidence, self.pool, context)
        if digests is None:
            return DigesterRoundArtifact(
                digests=(),
                actionability=0.0,
                rationale="selected target is no longer active",
            )
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


# ---------------------------------------------------------------------------
# A1 — LLM Digester (first LLM-AEGIS role; paper §4.3)
# ---------------------------------------------------------------------------
#
# The paper's Digester "compresses each task's traces into a structured per-task
# summary: binary outcome, failure category (if any), implicated component
# identifiers, and supporting evidence excerpts" (§4.3 p.10). This adapter makes
# that step model-backed while keeping the deterministic gate and engine
# untouched (REPRO-COMPLETION-PLAN Phase A; SPEC §4.3 principle: the shipping
# authority stays deterministic). The prompts are OURS — a reconstruction of the
# withheld §4.3/appendix F.1 role description, recorded as such.

#: [OURS] Trajectory windowing. A single GAIA trajectory can reach ~350k tokens;
#: failures concentrate at the end, so the window is tail-heavy. We send the
#: frontmatter block (capped), the first HEAD chars and the last TAIL chars of
#: the trajectory body — never the whole file — keeping every per-task call well
#: under ~40k chars.
_LLM_DIGESTER_HEAD_CHARS = 12_000
_LLM_DIGESTER_TAIL_CHARS = 20_000
_LLM_DIGESTER_FRONTMATTER_CHARS = 4_000

#: [OURS] Digester per-task prompt. Frozen/audited later, so it is a module
#: constant carrying the instruction + inline JSON schema; the per-task evidence
#: is appended at call time. The outcome is handed in as ground truth — the model
#: interprets, it does not re-judge pass/fail.
_LLM_DIGESTER_TASK_PROMPT = (
    "You are the Digester in a self-improving agent harness (the AEGIS Digester "
    "role, paper section 4.3). A GAIA task was executed by the agent and the "
    "harness scored it as FAILED. That pass/fail outcome is GROUND TRUTH given to "
    "you below — do NOT re-judge whether the task passed. Your job is to INTERPRET "
    "the failure from the trajectory evidence and compress it into a structured "
    "per-task summary.\n"
    "\n"
    "Output ONLY a JSON object (no prose, no markdown fences, no code block) with "
    "EXACTLY these four keys:\n"
    "{\n"
    '  "failure_category": "<short free-form label, e.g. blocked_source, '
    'reasoning_error, tool_output_dropped, scope_ambiguity>",\n'
    '  "implicated_components": ["<0+ identifiers from THIS vocabulary ONLY: '
    'tools/<name>, processor/<name>, prompt/<section>, environment, '
    'model_capability>"],\n'
    '  "evidence_anchors": ["<0+ SHORT verbatim quotes or step references copied '
    'from the trajectory that justify the category>"],\n'
    '  "notes": "<one short sentence of extra context, or empty string>"\n'
    "}\n"
    "\n"
    "Rules: base every field ONLY on the evidence shown; never invent tool names, "
    "steps, or quotes; keep each quote to a line or less; keep the whole object "
    "small. Return the JSON object and nothing else."
)

#: [OURS] Digester round-level prompt. Produces Algorithm 1's actionability a_t
#: from the per-task summaries. The deterministic fallback made a_t binary; the
#: LLM version is the real semantics ("is there at least one addressable failure
#: with usable evidence").
_LLM_DIGESTER_ROUND_PROMPT = (
    "You are the Digester in a self-improving agent harness (the AEGIS Digester "
    "role, paper section 4.3), now emitting the ROUND-LEVEL actionability signal "
    "a_t for Algorithm 1's selective invocation. Below are the per-task failure "
    "summaries you just produced for this round's target variant.\n"
    "\n"
    "Decide a_t in [0, 1]: is there AT LEAST ONE addressable failure with usable "
    "evidence that a harness edit could plausibly fix this round? Score high when "
    "yes; score low or zero when the failures are unaddressable (e.g. pure "
    "model_capability limits, or no usable evidence) or there is nothing to fix.\n"
    "\n"
    "Output ONLY a JSON object (no prose, no markdown fences, no code block) with "
    "EXACTLY these two keys:\n"
    "{\n"
    '  "actionability": <a number between 0 and 1 inclusive>,\n'
    '  "rationale": "<one or two sentences justifying the value; must be '
    'non-empty>"\n'
    "}\n"
    "\n"
    "Return the JSON object and nothing else."
)


class _DigesterWholesaleFallback(Exception):
    """Signal that the whole LLM Digester round must revert to deterministic.

    Carries the human-readable ``reason`` that is prefixed onto the returned
    rationale (fallback policy, task ruling 4).
    """


def _first_json_object(text: str) -> str | None:
    """The first balanced ``{...}`` block in ``text`` (string-aware), or ``None``.

    A brace scanner rather than a greedy first-``{``/last-``}`` slice so trailing
    prose after a valid object (a common LLM habit) does not defeat the strict
    ``json.loads`` the caller then runs on the returned block.
    """
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    in_str = False
    escape = False
    for index in range(start, len(text)):
        char = text[index]
        if in_str:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_str = False
        elif char == '"':
            in_str = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    return None


_TRAJECTORY_FRONTMATTER_RE = re.compile(r"^---\n.*?\n---\n?", re.DOTALL)


def _split_trajectory_frontmatter(text: str) -> tuple[str, str]:
    """Split a trajectory ``.md`` into ``(frontmatter, body)``.

    The recipe writes ``<frontmatter>\\n\\n<body>`` where the frontmatter is the
    ``---``-delimited YAML block (:func:`recipe.gaia_evolver.run.
    _render_trajectory_frontmatter`). When no frontmatter is present the whole
    text is the body.
    """
    match = _TRAJECTORY_FRONTMATTER_RE.match(text)
    if match is None:
        return "", text
    frontmatter = text[: match.end()].rstrip("\n")
    body = text[match.end() :].lstrip("\n")
    return frontmatter, body


@dataclass
class _LLMDigester:
    """Model-backed Digester (paper §4.3), first of the three LLM-AEGIS roles.

    Reads exactly the tasks/outcomes the deterministic :class:`_EvidenceDigester`
    does (:func:`_latest_settled_digests`) and splits them by outcome:

    * **PASSED tasks** keep the compact deterministic digest verbatim with **no
      LLM call**. [OURS] declared cost choice: a passing task carries no failure
      to interpret, so spending a meta-model call on it buys nothing; only
      failures are worth the tokens. The call model per round is therefore
      ``F failed tasks -> F + 1`` calls (one interpretation per failure, plus one
      round-level actionability call when ``F >= 1``; ``F == 0`` makes zero calls
      and a_t = 0.0).
    * **FAILED tasks** each get ONE meta-model call returning structured JSON
      (failure_category / implicated_components / evidence_anchors / notes) which
      is mapped onto :class:`TaskDigest`. The harness-decided ``outcome`` and the
      cross-round ``prior_history`` are preserved untouched.

    Robustness (task rulings 3-4): a per-task parse/validation failure retries
    once with the error fed back, then falls back to the deterministic digest for
    THAT task only; a provider error or a round-level double-failure reverts the
    WHOLE round to what :class:`_EvidenceDigester` would return, with the
    rationale prefixed ``llm_digester_fell_back:``. A round never dies here.

    Audit route (task ruling 3): the pipeline builds its ``AuditRecord`` list from
    the returned :class:`DigesterRoundArtifact` alone — a stage cannot push into
    that list — so per-task and wholesale fallbacks are recorded inside the round
    ``rationale`` string (phrased ``digester_fallback(disposition=fallback): ...``)
    rather than as separate audit records. The active-mode adapter name is set
    truthfully by the recipe (``MetaModel_llm_digester``); ``llm_aegis_reproduction``
    stays ``False`` until the Planner and Critic are LLM too.
    """

    evidence: EvidenceStore
    pool: VariantPool
    provider: Any
    tasks_by_id: Mapping[str, Any]
    run_dir: Path
    fallback: _EvidenceDigester

    async def digest(self, *, context: PipelineContext) -> DigesterRoundArtifact:
        base = _latest_settled_digests(self.evidence, self.pool, context)
        if base is None:
            # Target gone from the pool: identical to the deterministic result,
            # not a fallback — return it unprefixed.
            return await self.fallback.digest(context=context)
        try:
            return await self._digest_llm(context, base)
        except _DigesterWholesaleFallback as exc:
            return await self._wholesale_fallback(context, str(exc))
        except Exception as exc:  # noqa: BLE001 - provider/other error must not kill the round
            return await self._wholesale_fallback(
                context, f"{type(exc).__name__}: {exc}"
            )

    async def _digest_llm(
        self,
        context: PipelineContext,
        base: tuple[TaskDigest, ...],
    ) -> DigesterRoundArtifact:
        passed = [digest for digest in base if digest.solved]
        failed = [digest for digest in base if not digest.solved]

        out_digests: list[TaskDigest] = list(passed)
        fallback_notes: list[str] = []
        interpreted = 0
        for digest in failed:
            new_digest, note = await self._interpret_failed_task(context, digest)
            out_digests.append(new_digest)
            if note is None:
                interpreted += 1
            else:
                fallback_notes.append(note)

        if not failed:
            actionability = 0.0
            core = (
                "llm_digester: no settled routed task is unsolved; no addressable "
                "failure this round, so a_t=0.0 (derived locally, no LLM call)"
            )
        else:
            actionability, round_rationale = await self._round_actionability(
                context, out_digests
            )
            core = f"llm_digester: {round_rationale}"

        rationale = self._compose_rationale(
            core=core,
            n_passed=len(passed),
            n_failed=len(failed),
            interpreted=interpreted,
            fallback_notes=fallback_notes,
        )
        return DigesterRoundArtifact(
            digests=tuple(out_digests),
            actionability=actionability,
            rationale=rationale,
        )

    async def _interpret_failed_task(
        self,
        context: PipelineContext,
        digest: TaskDigest,
    ) -> tuple[TaskDigest, str | None]:
        """One failed task -> (mapped TaskDigest, None) or (deterministic, note).

        Provider exceptions propagate (they are wholesale). Only parse/validation
        failures are handled here: retry once with the error appended, then fall
        back to the deterministic digest for this task and return an audit note.
        """
        window = self._trajectory_window(digest)
        if window is None:
            return digest, (
                f"{digest.task_id}: no readable trajectory to interpret; kept "
                "deterministic digest"
            )
        frontmatter, head, tail = window
        question = self._question_for(digest.task_id)

        error: str | None = None
        for _attempt in range(2):
            prompt = self._build_task_prompt(
                digest=digest,
                question=question,
                frontmatter=frontmatter,
                head=head,
                tail=tail,
                retry_error=error,
            )
            text = await self._complete(prompt)
            parsed, error = self._parse_task_json(text)
            if parsed is not None:
                return self._map_task_digest(digest, parsed), None
        return digest, (
            f"{digest.task_id}: LLM interpretation failed twice ({error}); kept "
            "deterministic digest"
        )

    async def _round_actionability(
        self,
        context: PipelineContext,
        out_digests: Sequence[TaskDigest],
    ) -> tuple[float, str]:
        summary = self._round_summary(out_digests)
        error: str | None = None
        for _attempt in range(2):
            prompt = self._build_round_prompt(summary, retry_error=error)
            text = await self._complete(prompt)
            parsed, error = self._parse_round_json(text)
            if parsed is not None:
                return parsed
        raise _DigesterWholesaleFallback(
            f"round actionability failed twice ({error})"
        )

    # -- prompt assembly ------------------------------------------------------

    def _build_task_prompt(
        self,
        *,
        digest: TaskDigest,
        question: str,
        frontmatter: str,
        head: str,
        tail: str,
        retry_error: str | None,
    ) -> str:
        n_pass, n_att = digest.outcome
        parts = [
            _LLM_DIGESTER_TASK_PROMPT,
            f"\n\nTASK ID: {digest.task_id}",
            f"\nTASK OUTCOME (harness ground truth, do not re-derive): FAILED "
            f"(n_pass={n_pass} of n_att={n_att})",
            f"\n\nTASK QUESTION:\n{question}" if question else "",
            f"\n\n--- TRAJECTORY FRONTMATTER ---\n{frontmatter}" if frontmatter else "",
            f"\n\n--- TRAJECTORY HEAD (first {_LLM_DIGESTER_HEAD_CHARS} chars) ---\n{head}",
            f"\n\n--- TRAJECTORY TAIL (last {_LLM_DIGESTER_TAIL_CHARS} chars) ---\n{tail}"
            if tail
            else "",
        ]
        if retry_error:
            parts.append(
                "\n\nYour previous response was rejected: "
                f"{retry_error}. Return ONLY a single valid JSON object with the "
                "four required keys and nothing else."
            )
        return "".join(parts)

    def _build_round_prompt(self, summary: str, *, retry_error: str | None) -> str:
        parts = [
            _LLM_DIGESTER_ROUND_PROMPT,
            f"\n\nPER-TASK FAILURE SUMMARIES THIS ROUND:\n{summary}",
        ]
        if retry_error:
            parts.append(
                "\n\nYour previous response was rejected: "
                f"{retry_error}. Return ONLY a single valid JSON object with the "
                "two required keys and nothing else."
            )
        return "".join(parts)

    @staticmethod
    def _round_summary(out_digests: Sequence[TaskDigest]) -> str:
        lines: list[str] = []
        for digest in out_digests:
            if digest.solved:
                continue
            components = ", ".join(digest.implicated_components) or "none"
            has_evidence = "yes" if digest.evidence_anchors else "no"
            lines.append(
                f"- {digest.task_id}: category={digest.failure_category or 'unknown'}; "
                f"components=[{components}]; has_evidence={has_evidence}"
            )
        return "\n".join(lines) if lines else "(no unsolved tasks)"

    # -- parsing / validation -------------------------------------------------

    def _parse_task_json(self, text: str) -> tuple[dict | None, str | None]:
        block = _first_json_object(text)
        if block is None:
            return None, "no JSON object found in response"
        try:
            obj = json.loads(block)
        except (ValueError, TypeError) as exc:
            return None, f"json.loads failed: {exc}"
        if not isinstance(obj, dict):
            return None, "top-level JSON value is not an object"
        category = obj.get("failure_category")
        if not isinstance(category, str) or not category.strip():
            return None, "failure_category must be a non-empty string"
        return obj, None

    def _parse_round_json(
        self, text: str
    ) -> tuple[tuple[float, str] | None, str | None]:
        block = _first_json_object(text)
        if block is None:
            return None, "no JSON object found in response"
        try:
            obj = json.loads(block)
        except (ValueError, TypeError) as exc:
            return None, f"json.loads failed: {exc}"
        if not isinstance(obj, dict):
            return None, "top-level JSON value is not an object"
        raw = obj.get("actionability")
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            return None, "actionability must be a number in [0, 1]"
        value = float(raw)
        if not math.isfinite(value) or not 0.0 <= value <= 1.0:
            return None, f"actionability {raw!r} is out of range [0, 1]"
        rationale = obj.get("rationale")
        if not isinstance(rationale, str) or not rationale.strip():
            return None, "rationale must be a non-empty string"
        return (value, rationale.strip()), None

    def _map_task_digest(self, digest: TaskDigest, obj: Mapping[str, Any]) -> TaskDigest:
        category = str(obj.get("failure_category") or "").strip() or (
            digest.failure_category or "uncategorized_failure"
        )
        components = [
            str(item).strip()
            for item in (obj.get("implicated_components") or [])
            if str(item).strip()
        ]
        model_anchors = [
            str(item).strip()
            for item in (obj.get("evidence_anchors") or [])
            if str(item).strip()
        ]
        # Keep the trajectory path anchor like the deterministic digest does,
        # then the model's anchors, then the free-form ``notes`` (TaskDigest has
        # no notes field; folding it into evidence_anchors keeps the LLM's
        # interpretation auditable — [OURS] mapping choice).
        anchors: list[str] = list(digest.evidence_anchors)
        for anchor in model_anchors:
            if anchor not in anchors:
                anchors.append(anchor)
        notes = str(obj.get("notes") or "").strip()
        if notes:
            anchors.append(f"note: {notes}")
        return TaskDigest(
            task_id=digest.task_id,
            round_idx=digest.round_idx,
            variant_id=digest.variant_id,
            outcome=digest.outcome,
            failure_category=category,
            implicated_components=components,
            evidence_anchors=anchors,
            prior_history=list(digest.prior_history),
        )

    # -- trajectory windowing -------------------------------------------------

    def _trajectory_window(self, digest: TaskDigest) -> tuple[str, str, str] | None:
        """``(frontmatter, head, tail)`` for a failed task, or ``None`` if unreadable."""
        text = self._trajectory_text(digest)
        if text is None:
            return None
        frontmatter, body = _split_trajectory_frontmatter(text)
        frontmatter = frontmatter[:_LLM_DIGESTER_FRONTMATTER_CHARS]
        if len(body) <= _LLM_DIGESTER_HEAD_CHARS + _LLM_DIGESTER_TAIL_CHARS:
            # Short enough to send whole (no overlap between head and tail).
            return frontmatter, body, ""
        head = body[:_LLM_DIGESTER_HEAD_CHARS]
        tail = body[-_LLM_DIGESTER_TAIL_CHARS:]
        return frontmatter, head, tail

    def _trajectory_text(self, digest: TaskDigest) -> str | None:
        for anchor in digest.evidence_anchors:
            path_part = str(anchor).split("#", 1)[0].strip()
            if not path_part:
                continue
            path = Path(path_part)
            if not path.is_absolute():
                path = self.run_dir / path_part
            if path.is_file():
                try:
                    return path.read_text(encoding="utf-8")
                except OSError:
                    continue
        return None

    def _question_for(self, task_id: str) -> str:
        task = self.tasks_by_id.get(task_id)
        if task is None:
            return ""
        return str(getattr(task, "question", "") or getattr(task, "description", "") or "")

    # -- LLM plumbing / fallback ---------------------------------------------

    async def _complete(self, prompt: str) -> str:
        # Plain async completion on the recipe's meta provider — NOT
        # MetaAgent.evolve. Temperature is left at the provider default.
        from harnessx.core.events import Message

        response = await self.provider.complete(
            [Message(role="user", content=prompt)], []
        )
        return str(getattr(response, "content", "") or "")

    @staticmethod
    def _compose_rationale(
        *,
        core: str,
        n_passed: int,
        n_failed: int,
        interpreted: int,
        fallback_notes: Sequence[str],
    ) -> str:
        parts = [
            core,
            (
                f" [llm_digester bookkeeping: passed={n_passed} kept deterministic "
                f"(no LLM), failed={n_failed}, llm_interpreted={interpreted}, "
                f"per_task_fallbacks={len(fallback_notes)}]"
            ),
        ]
        for note in fallback_notes:
            parts.append(f" digester_fallback(disposition=fallback): {note};")
        return "".join(parts)

    async def _wholesale_fallback(
        self, context: PipelineContext, reason: str
    ) -> DigesterRoundArtifact:
        base = await self.fallback.digest(context=context)
        return DigesterRoundArtifact(
            digests=base.digests,
            actionability=base.actionability,
            rationale=f"llm_digester_fell_back: {reason}; " + base.rationale,
            contract_mode=base.contract_mode,
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
# A2 — LLM Planner (second LLM-AEGIS role; paper §4.3)
# ---------------------------------------------------------------------------
#
# The paper's Planner "builds the mutation landscape — which tasks still fail,
# what edits were already tried on them, and which of the four edit classes are
# untried — and emits up to K_t candidate briefs, one falsifiable edit
# hypothesis each" (§4.3 p.10). This adapter makes that step model-backed while
# keeping the deterministic gate and engine untouched (REPRO-COMPLETION-PLAN
# Phase A2; SPEC §4.3 principle: the shipping authority stays deterministic). The
# prompt is OURS — a reconstruction of the withheld §4.3 role description.

#: The four paper edit classes (Table 9 ``bucket``; PAPER_MANIFEST_SCHEMA_BRIEF
#: "subset of [prompt, tools, config, processor]"). In ``--aegis-planner llm`` a
#: brief's ``buckets`` are SUGGESTED edit classes drawn from this set (paper
#: semantics), unlike the deterministic Planner whose ``buckets`` carry
#: failure-cluster labels; see :class:`_LLMPlanner`.
_PAPER_EDIT_CLASSES = ("prompt", "tools", "config", "processor")

#: [OURS] Serialized-input budget for one Planner call. A round can carry many
#: failed tasks with long evidence_anchor lists; the input is capped here and
#: evidence_anchors are truncated first (the task ids / categories / prior-ship
#: history are never dropped), keeping the call well under the meta model's
#: context.
_LLM_PLANNER_INPUT_CAP = 30_000

#: [OURS] Planner prompt. Frozen/audited later, so it is a module constant
#: carrying the instruction + inline JSON schema; the round's evidence is
#: appended at call time. The Planner does NOT re-derive task outcomes — it reads
#: the Digester's structured per-task summaries and proposes the mutation
#: landscape.
_LLM_PLANNER_PROMPT = (
    "You are the Planner in a self-improving agent harness (AEGIS Planner role, "
    "paper section 4.3). The Digester has already compressed this round's traces "
    "into the per-task failure summaries given below (their pass/fail outcomes "
    "are GROUND TRUTH — do NOT re-judge them). Your job is to build the MUTATION "
    "LANDSCAPE for the target harness variant — which tasks still fail, what was "
    "already tried on them (from the prior-ship history), and which of the four "
    "edit classes are still untried — and to emit up to K_t candidate briefs, one "
    "falsifiable edit hypothesis each.\n"
    "\n"
    "The four edit classes (paper Table 9 buckets) are EXACTLY: prompt, tools, "
    "config, processor. Each brief's `buckets` is your SUGGESTED edit class(es) "
    "for that one candidate (one or more of those four literals) — it is NOT a "
    "failure label. When several distinct failure clusters exist and you emit "
    "more than one brief, make the briefs differ in their bucket mix so the batch "
    "explores the landscape instead of repeating a single edit.\n"
    "\n"
    "Output ONLY a JSON object (no prose, no markdown fences, no code block) with "
    "EXACTLY these two keys:\n"
    "{\n"
    '  "briefs": [\n'
    "    {\n"
    '      "buckets": ["<1+ of: prompt, tools, config, processor>"],\n'
    '      "task_ids": ["<task ids taken ONLY from this round\'s summaries below>"],\n'
    '      "rationale": "<one evidence-anchored hypothesis for this single '
    'candidate; must be non-empty>"\n'
    "    }\n"
    "  ],\n"
    '  "landscape_notes": "<short summary of what has been tried and which edit '
    'classes remain untried>"\n'
    "}\n"
    "\n"
    "Emit between 0 and K_t briefs. Returning `\"briefs\": []` is a LEGITIMATE "
    "outcome when nothing in the evidence is addressable by a harness edit this "
    "round — do NOT invent a brief to fill the batch. Rules: never invent task "
    "ids, tool names, or edit classes; use ONLY the ids and evidence shown; keep "
    "the whole object small. Return the JSON object and nothing else."
)


class _PlannerWholesaleFallback(Exception):
    """Signal that the whole LLM Planner round must revert to deterministic.

    Carries the human-readable ``reason`` prefixed onto the returned plan's notes
    (fallback policy, mirrors :class:`_DigesterWholesaleFallback`).
    """


@dataclass
class _LLMPlanner:
    """Model-backed Planner (paper §4.3), second of the three LLM-AEGIS roles.

    Consumes exactly the digests the pipeline hands every Planner (this round's
    per-task summaries, carrying the LLM-Digester output when ``--aegis-digester
    llm`` is also on) and, in ONE meta-model call (plus at most one parse-retry),
    emits between 0 and ``k_t`` :class:`~experiments.variant_pool.candidate_pipeline.CandidateBrief`
    objects plus a landscape summary. The input assembled into the prompt is the
    round's digests (task_id / outcome / failure_category / implicated_components
    / evidence_anchors), ``context.regressions``, ``context.failure_buckets`` and
    the prior-ship history the recipe can already reach — each digest's
    ``prior_history`` (its ``ships`` per prior round), which the EvidenceStore
    attached at write time; no new store is queried. The serialized input is
    capped at ~30k chars, truncating evidence_anchors first.

    Buckets semantics — a deliberate SHIFT from the deterministic arm. The
    deterministic :class:`_DeterministicPlanner` puts *failure-cluster labels*
    (e.g. ``gaia_level_1``) in a brief's ``buckets``. In ``llm`` mode ``buckets``
    are instead *suggested edit classes* — a subset of the four paper edit
    classes (``prompt``/``tools``/``config``/``processor``, :data:`_PAPER_EDIT_CLASSES`).
    This is safe because no consumer treats ``CandidateBrief.buckets`` as the
    manifest ``bucket`` enum: a brief is only rendered verbatim into the meta
    contract (``asdict(brief)`` -> ``planner_brief`` JSON in
    ``VariantPoolMetaAgent._render_candidate_contract``); ``_brief_for_slot`` and
    the contract renderer never parse ``buckets``. The deterministic arm already
    ships non-enum labels through that same path, so both semantics coexist.

    Validation (task ruling): each brief's ``buckets`` is filtered to the four
    edit classes (a brief with none left is dropped), ``task_ids`` is filtered to
    this round's digest ids (unknown ids dropped, noted), and an empty
    ``rationale`` drops the brief; all drops are recorded in the plan notes.
    Briefs are renumbered ``P-R{round}-{NN}`` exactly like the deterministic
    Planner. Robustness (mirrors A1): a JSON parse/validation failure retries once
    with the error fed back; a provider error or a double parse failure reverts
    the WHOLE round to what :class:`_DeterministicPlanner` returns, with the notes
    prefixed ``llm_planner_fell_back:``. A round never dies here.

    Empty landscape: when the model itself returns ``"briefs": []`` the plan sets
    :attr:`~experiments.variant_pool.candidate_pipeline.PlanningArtifact.empty_landscape`,
    which the pipeline short-circuits (``short_circuit="planner_empty_landscape"``).
    Fallbacks NEVER set the flag; a response whose briefs were all dropped as
    invalid leaves it ``False`` and takes the legacy empty-briefs path.

    The active adapter name is set truthfully by the recipe
    (``MetaModel_llm_planner``); ``llm_aegis_reproduction`` stays ``False`` until
    the Critic is LLM too.
    """

    provider: Any
    k_t: int
    fallback: _DeterministicPlanner
    #: P1-1 --aegis-prompts: this role's system prompt. Defaults to the OURS
    #: constant so every existing construction stays byte-identical; the recipe
    #: passes ``_PAPER_PLANNER_PROMPT`` in ``paper`` mode.
    prompt: str = _LLM_PLANNER_PROMPT

    async def plan(
        self,
        *,
        context: PipelineContext,
        digests: Sequence[TaskDigest],
    ) -> PlanningArtifact:
        digests = tuple(digests)
        try:
            return await self._plan_llm(context, digests)
        except _PlannerWholesaleFallback as exc:
            return await self._wholesale_fallback(context, digests, str(exc))
        except Exception as exc:  # noqa: BLE001 - provider/other error must not kill the round
            return await self._wholesale_fallback(
                context, digests, f"{type(exc).__name__}: {exc}"
            )

    async def _plan_llm(
        self,
        context: PipelineContext,
        digests: tuple[TaskDigest, ...],
    ) -> PlanningArtifact:
        summary, truncation = self._build_input(context, digests)
        valid_ids = {digest.task_id for digest in digests}
        error: str | None = None
        for _attempt in range(2):
            prompt = self._build_prompt(summary, truncation=truncation, retry_error=error)
            text = await self._complete(prompt)
            parsed, error = self._parse_plan_json(text)
            if parsed is not None:
                return self._map_plan(context, parsed, valid_ids, truncation)
        raise _PlannerWholesaleFallback(f"planner JSON failed twice ({error})")

    # -- input assembly (capped) ---------------------------------------------

    def _build_input(
        self,
        context: PipelineContext,
        digests: tuple[TaskDigest, ...],
    ) -> tuple[str, tuple[str, ...]]:
        """``(serialized_input, truncation_notes)`` under :data:`_LLM_PLANNER_INPUT_CAP`.

        evidence_anchors are the only field trimmed (task ids, categories,
        components and prior-ship history are always kept); the note records how
        far they were trimmed so the audit shows the input was capped.
        """
        for max_anchors in (None, 3, 1, 0):
            body = self._compose_input(context, digests, max_anchors=max_anchors)
            if len(body) <= _LLM_PLANNER_INPUT_CAP:
                if max_anchors is None:
                    return body, ()
                if max_anchors == 0:
                    note = (
                        "evidence_anchors dropped entirely to fit the "
                        f"~{_LLM_PLANNER_INPUT_CAP}-char input cap"
                    )
                else:
                    note = (
                        f"evidence_anchors truncated to <= {max_anchors} per task to fit "
                        f"the ~{_LLM_PLANNER_INPUT_CAP}-char input cap"
                    )
                return body, (note,)
        body = self._compose_input(context, digests, max_anchors=0)[:_LLM_PLANNER_INPUT_CAP]
        return body, (
            f"input hard-truncated to {_LLM_PLANNER_INPUT_CAP} chars",
        )

    def _compose_input(
        self,
        context: PipelineContext,
        digests: tuple[TaskDigest, ...],
        *,
        max_anchors: int | None,
    ) -> str:
        regressions = ", ".join(context.regressions) or "none"
        failure_buckets = ", ".join(context.failure_buckets) or "none"
        lines = [
            f"TARGET VARIANT: {context.target_variant}",
            f"ROUND: {context.round_idx}",
            f"K_t (max briefs to emit this round): {self.k_t}",
            f"ACTIVE REGRESSIONS (previously solved, now failing): {regressions}",
            f"SETTLED FAILURE CATEGORIES: {failure_buckets}",
            "",
            "PER-TASK SUMMARIES THIS ROUND:",
        ]
        for digest in digests:
            n_pass, n_att = digest.outcome
            status = "SOLVED" if digest.solved else "FAILED"
            components = ", ".join(digest.implicated_components) or "none"
            anchors = list(digest.evidence_anchors)
            if max_anchors is not None:
                anchors = anchors[:max_anchors]
            anchor_str = " | ".join(anchors) if anchors else "none"
            lines.append(
                f"- {digest.task_id}: {status} ({n_pass}/{n_att}); "
                f"category={digest.failure_category or 'unknown'}; "
                f"components=[{components}]; evidence=[{anchor_str}]"
            )
            prior = self._prior_ship_history(digest)
            if prior:
                lines.append(f"    prior_history: {prior}")
        return "\n".join(lines)

    @staticmethod
    def _prior_ship_history(digest: TaskDigest) -> str:
        """This task's prior outcomes + the candidate ids shipped for it, per round.

        Delegates to the module-level :func:`_digest_prior_ship_history` so the A2
        Planner and the A3 Critic read the "prior-ship history the recipe can
        already reach" from the same source. Output is byte-identical to the
        previous inline body.
        """
        return _digest_prior_ship_history(digest)

    # -- prompt assembly ------------------------------------------------------

    def _build_prompt(
        self,
        summary: str,
        *,
        truncation: tuple[str, ...],
        retry_error: str | None,
    ) -> str:
        parts = [
            self.prompt,
            f"\n\nROUND EVIDENCE:\n{summary}",
        ]
        if truncation:
            parts.append("\n\nINPUT NOTES: " + "; ".join(truncation))
        if retry_error:
            parts.append(
                "\n\nYour previous response was rejected: "
                f"{retry_error}. Return ONLY a single valid JSON object with the "
                "two required keys ('briefs' and 'landscape_notes') and nothing else."
            )
        return "".join(parts)

    # -- parsing / validation / mapping --------------------------------------

    def _parse_plan_json(self, text: str) -> tuple[dict | None, str | None]:
        block = _first_json_object(text)
        if block is None:
            return None, "no JSON object found in response"
        try:
            obj = json.loads(block)
        except (ValueError, TypeError) as exc:
            return None, f"json.loads failed: {exc}"
        if not isinstance(obj, dict):
            return None, "top-level JSON value is not an object"
        briefs = obj.get("briefs")
        if not isinstance(briefs, list):
            return None, "'briefs' must be a JSON array (possibly empty)"
        notes = obj.get("landscape_notes", "")
        if notes is not None and not isinstance(notes, str):
            return None, "'landscape_notes' must be a string"
        return obj, None

    def _map_plan(
        self,
        context: PipelineContext,
        obj: Mapping[str, Any],
        valid_ids: set[str],
        truncation: tuple[str, ...],
    ) -> PlanningArtifact:
        raw_briefs = list(obj.get("briefs") or [])
        # The flag is set ONLY when the model itself returned an empty list — not
        # when every brief was dropped as invalid (that keeps the legacy path).
        model_returned_zero = len(raw_briefs) == 0
        landscape_notes = str(obj.get("landscape_notes") or "").strip()

        briefs: list[CandidateBrief] = []
        drop_notes: list[str] = []
        for index, raw in enumerate(raw_briefs, start=1):
            if len(briefs) >= self.k_t:
                drop_notes.append(f"dropped brief #{index}: K_t={self.k_t} cap reached")
                continue
            brief, note = self._map_brief(
                context, raw, valid_ids, position=len(briefs) + 1
            )
            if brief is None:
                drop_notes.append(note or f"dropped brief #{index}: invalid")
                continue
            briefs.append(brief)
            if note:
                drop_notes.append(note)

        notes: list[str] = ["llm_planner (paper §4.3 AEGIS Planner role; prompt OURS)"]
        if landscape_notes:
            notes.append(landscape_notes)
        notes.extend(truncation)
        notes.extend(drop_notes)
        if len(notes) == 1:  # only the provenance tag
            notes.append(
                "llm_planner: model reported an empty mutation landscape (briefs=0)"
                if model_returned_zero
                else "llm_planner: mutation landscape emitted"
            )
        return PlanningArtifact(
            target_variant=context.target_variant,
            briefs=tuple(briefs),
            notes=tuple(notes),
            empty_landscape=model_returned_zero,
        )

    def _map_brief(
        self,
        context: PipelineContext,
        raw: Any,
        valid_ids: set[str],
        *,
        position: int,
    ) -> tuple[CandidateBrief | None, str | None]:
        if not isinstance(raw, Mapping):
            return None, f"dropped brief #{position}: not a JSON object"
        raw_buckets = raw.get("buckets") or []
        if isinstance(raw_buckets, str):
            raw_buckets = [raw_buckets]
        buckets: list[str] = []
        for item in raw_buckets:
            value = str(item).strip().lower()
            if value in _PAPER_EDIT_CLASSES and value not in buckets:
                buckets.append(value)
        if not buckets:
            return None, (
                f"dropped brief #{position}: no valid edit class in "
                f"buckets={raw.get('buckets')!r} (allowed: {list(_PAPER_EDIT_CLASSES)})"
            )
        rationale = str(raw.get("rationale") or "").strip()
        if not rationale:
            return None, f"dropped brief #{position}: empty rationale"
        raw_ids = raw.get("task_ids") or []
        if isinstance(raw_ids, str):
            raw_ids = [raw_ids]
        kept_ids: list[str] = []
        unknown_ids: list[str] = []
        for item in raw_ids:
            value = str(item).strip()
            if not value:
                continue
            if value in valid_ids:
                if value not in kept_ids:
                    kept_ids.append(value)
            elif value not in unknown_ids:
                unknown_ids.append(value)
        brief_id = f"P-R{context.round_idx}-{position:02d}"
        brief = CandidateBrief(
            brief_id=brief_id,
            buckets=tuple(buckets),
            task_ids=tuple(kept_ids),
            rationale=rationale,
        )
        note = None
        if unknown_ids:
            note = (
                f"{brief_id}: dropped unknown task_ids {unknown_ids} "
                "(not in this round's digests)"
            )
        return brief, note

    # -- LLM plumbing / fallback ---------------------------------------------

    async def _complete(self, prompt: str) -> str:
        # Plain async completion on the recipe's meta provider — the same seam A1
        # uses. Temperature is left at the provider default.
        from harnessx.core.events import Message

        response = await self.provider.complete(
            [Message(role="user", content=prompt)], []
        )
        return str(getattr(response, "content", "") or "")

    async def _wholesale_fallback(
        self,
        context: PipelineContext,
        digests: tuple[TaskDigest, ...],
        reason: str,
    ) -> PlanningArtifact:
        base = await self.fallback.plan(context=context, digests=digests)
        return PlanningArtifact(
            target_variant=base.target_variant,
            briefs=base.briefs,
            notes=(f"llm_planner_fell_back: {reason}; ", *base.notes),
            # A fallback is NOT an empty landscape: leave the flag False so the
            # deterministic empty-briefs path (short_circuit="empty_landscape")
            # stays byte-identical to the deterministic arm.
            empty_landscape=False,
        )


# ---------------------------------------------------------------------------
# A3 — LLM Critic (third and final LLM-AEGIS role; paper §4.3)
# ---------------------------------------------------------------------------
#
# The paper's Critic audits the Evolver's structured candidate portfolio, emits a
# ship_ranking for the deterministic gate to inspect, and may request AT MOST ONE
# revision (§4.3 p.10). This adapter makes that step model-backed while keeping
# the deterministic gate and engine untouched (REPRO-COMPLETION-PLAN Phase A3;
# SPEC §4.3 principle: the shipping authority stays deterministic). The prompt is
# OURS — a reconstruction of the withheld §4.3 role description.

#: [OURS] Serialized-input budget for one Critic call (mirrors the A2 Planner).
#: Candidate manifests + digests can be long; the input is capped here and the
#: digest evidence_anchors are truncated FIRST (candidate ids / buckets /
#: predicted_impact / file paths and the digest task ids / categories are never
#: dropped), keeping the call well under the meta model's context.
_LLM_CRITIC_INPUT_CAP = 30_000

#: [OURS] Critic prompt. Frozen/audited later, so it is a module constant carrying
#: the instruction + inline JSON schema; the round's candidates + evidence are
#: appended at call time. The Critic does NOT re-derive task outcomes and cannot
#: ship — it audits the structured candidates and hands the deterministic gate a
#: ranked queue.
_LLM_CRITIC_PROMPT = (
    "You are the Critic in a self-improving agent harness (AEGIS Critic role, "
    "paper section 4.3). The Evolver has proposed a batch of structured candidates "
    "for ONE target harness variant; each candidate carries a change manifest "
    "(candidate_id, bucket, predicted_impact, capability_evidence, file_changes, "
    "target_variant). The Digester's per-task failure summaries for this round are "
    "given too (their pass/fail outcomes are GROUND TRUTH — do NOT re-judge "
    "them).\n"
    "\n"
    "Your job is a PORTFOLIO AUDIT: (1) produce a ship_ranking — the order in which "
    "the gate should inspect the candidates, best first; (2) reject any candidate "
    "that is unsound or redundant; (3) raise portfolio-level strategy concerns; and "
    "(4) for AT MOST ONE candidate, request a single revision with concrete "
    "instructions for the Evolver.\n"
    "\n"
    "You do NOT ship, approve, or apply anything. A separate DETERMINISTIC gate "
    "re-evaluates every ranked candidate on real tasks and is the ONLY shipping "
    "authority: your ranking is merely the inspection order, and a rejection only "
    "removes a candidate from that queue. You cannot make a candidate ship.\n"
    "\n"
    "Output ONLY a JSON object (no prose, no markdown fences, no code block) with "
    "EXACTLY these keys:\n"
    "{\n"
    '  "ranked_candidate_ids": ["<candidate ids best-first; a permutation of the '
    'candidates you did NOT reject>"],\n'
    '  "verdicts": [{"candidate_id": "<id>", "rank": <1-based integer>, "reasons": '
    '["<short justification>"]}],\n'
    '  "rejections": [{"candidate_id": "<id>", "reason": "<why it must not be '
    'ranked>"}],\n'
    '  "revision_requests": [{"candidate_id": "<id>", "reason": "<what is wrong>", '
    '"instructions": "<concrete fix for the Evolver>"}],\n'
    '  "no_op": <true to stop the whole round, else false>,\n'
    '  "no_op_reasons": ["<non-empty when no_op is true>"],\n'
    '  "strategy_concerns": ["<0+ portfolio-level observations>"]\n'
    "}\n"
    "\n"
    "Rules: use ONLY the candidate ids shown below; never invent an id. Emit AT "
    "MOST ONE revision request (paper section 4.3). `ranked_candidate_ids` must be "
    "a permutation of the candidates you did not reject (rank every non-rejected "
    "candidate, none twice). Base every judgement only on the manifests and "
    "evidence shown. In repo manifest mode, Level-2 round-trip evidence for "
    "tool/processor candidates is MACHINE-CERTIFIED downstream at the "
    "deterministic gate from the candidate's real evaluation trajectories "
    "(deviation M-22); do NOT reject a candidate solely because declared Level-2 "
    "evidence is missing — record it as a strategy concern instead. "
    "Return the JSON object and nothing else."
)


class _CriticWholesaleFallback(Exception):
    """Signal that the whole LLM Critic call must revert to deterministic.

    Carries the human-readable ``reason`` prefixed onto the fallback review's
    strategy_concerns (fallback policy; mirrors :class:`_PlannerWholesaleFallback`).
    """


@dataclass
class _LLMCritic:
    """Model-backed Critic (paper §4.3), third and last of the LLM-AEGIS roles.

    Implements the ``CriticStage`` protocol the pipeline calls
    (``review(*, context, candidates)``). In ONE meta-model call (plus at most one
    parse-retry) it audits the Evolver's structured candidates and returns a
    :class:`~experiments.variant_pool.critic.CriticReview` — a ship_ranking, per
    candidate verdicts, rejections, at most one revision request, and strategy
    concerns.

    Shipping authority — unchanged. The Critic NEVER ships or un-ships: the
    pipeline consumes ``ranked_for_gate`` as a *gate queue*
    (``CandidatePipeline._resolve_ranking`` feeds the deterministic ``run_gate``),
    and ``CriticReview.requires_deterministic_gate`` is always True. Ranking only
    orders the gate's inspection; a rejection only drops a candidate from the
    queue. So an ``llm`` Critic cannot make a candidate ship that the
    deterministic seesaw would reject, nor keep one shipping beyond removing it
    from this round's queue — the deterministic gate stays the sole shipping
    authority (SPEC §4.3).

    Validation (task ruling): every id the model emits must be an ACTUAL candidate
    id (unknowns are dropped, each noted in strategy_concerns); AT MOST ONE
    revision request survives (extras dropped, noted — paper §4.3); and
    ``ranked_candidate_ids`` is repaired to a permutation of the non-rejected ids
    (rejected/unknown/duplicate ids removed, missing ids appended in candidate-id
    order, the repair noted). Robustness (mirrors A1/A2): a JSON parse/validation
    failure retries once with the error fed back; a provider error or a double
    parse failure reverts the WHOLE review to what
    :class:`~experiments.variant_pool.critic.DeterministicCritic` returns, with the
    strategy_concerns prefixed ``llm_critic_fell_back:``. A round never dies here.

    Whole-round regression veto — kept. The DeterministicCritic's OUR-side safety
    rule (no_op the round when an active regression is neither handled in a
    surviving candidate's ``tasks_at_risk`` nor explained) is NOT bypassed by the
    model: after mapping the LLM review, the same deterministic check
    (``DeterministicCritic._unresolved_regressions``, reused DIRECTLY — it is a
    stateless staticmethod, so calling it duplicates no logic and leaves critic.py
    untouched) runs over the surviving candidates and forces the round to no_op if
    a regression is unhandled, whatever the model ranked.

    Revision plumbing (A3 Part 2). When the review carries the single revision
    request, ``(reason, instructions)`` is recorded into ``revision_sink`` keyed by
    the target candidate id, so the recipe's revision producer — which only
    receives ``slot.revision_of`` — can inject the instructions into the revised
    candidate's contract. ``RevisionRequest`` itself has only ``candidate_id`` and
    ``reason`` (critic.py is untouched), so the free-form ``instructions`` live in
    the sink.

    The active adapter name is set truthfully by the recipe
    (``MetaModel_llm_critic``); ``llm_aegis_reproduction`` flips to ``True`` only
    when the Digester and Planner are LLM too.
    """

    provider: Any
    fallback: DeterministicCritic
    revision_sink: dict[str, dict[str, str]] | None = None
    #: P1-1 --aegis-prompts: this role's system prompt. Defaults to the OURS
    #: constant (byte-identical construction); the recipe passes
    #: ``_PAPER_CRITIC_PROMPT`` in ``paper`` mode.
    prompt: str = _LLM_CRITIC_PROMPT

    async def review(
        self,
        *,
        context: CriticContext,
        candidates: Sequence[CandidateArtifact],
    ) -> CriticReview:
        candidates = tuple(candidates)
        try:
            return await self._review_llm(context, candidates)
        except _CriticWholesaleFallback as exc:
            return await self._wholesale_fallback(context, candidates, str(exc))
        except Exception as exc:  # noqa: BLE001 - provider/other error must not kill the round
            return await self._wholesale_fallback(
                context, candidates, f"{type(exc).__name__}: {exc}"
            )

    async def _review_llm(
        self,
        context: CriticContext,
        candidates: tuple[CandidateArtifact, ...],
    ) -> CriticReview:
        summary, truncation = self._build_input(context, candidates)
        error: str | None = None
        for _attempt in range(2):
            prompt = self._build_prompt(summary, truncation=truncation, retry_error=error)
            text = await self._complete(prompt)
            parsed, error = self._parse_review_json(text)
            if parsed is not None:
                return self._map_review(context, candidates, parsed)
        raise _CriticWholesaleFallback(f"critic JSON failed twice ({error})")

    # -- input assembly (capped) ---------------------------------------------

    def _build_input(
        self,
        context: CriticContext,
        candidates: tuple[CandidateArtifact, ...],
    ) -> tuple[str, tuple[str, ...]]:
        """``(serialized_input, truncation_notes)`` under :data:`_LLM_CRITIC_INPUT_CAP`.

        digest evidence_anchors are the only field trimmed (candidate ids,
        buckets, predicted_impact, file paths and the digest task ids / categories
        are always kept); the note records how far they were trimmed so the audit
        shows the input was capped. Mirrors :meth:`_LLMPlanner._build_input`.
        """
        for max_anchors in (None, 3, 1, 0):
            body = self._compose_input(context, candidates, max_anchors=max_anchors)
            if len(body) <= _LLM_CRITIC_INPUT_CAP:
                if max_anchors is None:
                    return body, ()
                if max_anchors == 0:
                    note = (
                        "evidence_anchors dropped entirely to fit the "
                        f"~{_LLM_CRITIC_INPUT_CAP}-char input cap"
                    )
                else:
                    note = (
                        f"evidence_anchors truncated to <= {max_anchors} per task to fit "
                        f"the ~{_LLM_CRITIC_INPUT_CAP}-char input cap"
                    )
                return body, (note,)
        body = self._compose_input(context, candidates, max_anchors=0)[:_LLM_CRITIC_INPUT_CAP]
        return body, (f"input hard-truncated to {_LLM_CRITIC_INPUT_CAP} chars",)

    def _compose_input(
        self,
        context: CriticContext,
        candidates: tuple[CandidateArtifact, ...],
        *,
        max_anchors: int | None,
    ) -> str:
        regressions = ", ".join(context.regressions) or "none"
        failure_buckets = ", ".join(context.failure_buckets) or "none"
        lines = [
            f"TARGET VARIANT: {context.target_variant}",
            f"ROUND: {context.round_idx}",
            f"ACTIVE REGRESSIONS (previously solved, now failing): {regressions}",
            f"SETTLED FAILURE CATEGORIES: {failure_buckets}",
            "",
            "CANDIDATES TO AUDIT:",
        ]
        for candidate in candidates:
            manifest = candidate.manifest
            impact = manifest.predicted_impact
            flips = ", ".join(impact.predicted_flips()) or "none"
            at_risk = ", ".join(impact.tasks_at_risk) or "none"
            claims = (
                "; ".join(
                    str(entry.get("claim", "")).strip()
                    for entry in manifest.capability_evidence
                    if str(entry.get("claim", "")).strip()
                )
                or "none"
            )
            paths = (
                ", ".join(
                    str(change.get("path", "")).strip()
                    for change in manifest.file_changes
                    if str(change.get("path", "")).strip()
                )
                or "none"
            )
            lines.append(
                f"- {candidate.candidate_id}: bucket={list(manifest.bucket)}; "
                f"target={manifest.target_variant}; predicted_flips=[{flips}]; "
                f"tasks_at_risk=[{at_risk}]; file_changes=[{paths}]; "
                f"capability_evidence=[{claims}]"
            )
        lines.append("")
        lines.append("PER-TASK SUMMARIES THIS ROUND:")
        for digest in context.digests:
            n_pass, n_att = digest.outcome
            status = "SOLVED" if digest.solved else "FAILED"
            components = ", ".join(digest.implicated_components) or "none"
            anchors = list(digest.evidence_anchors)
            if max_anchors is not None:
                anchors = anchors[:max_anchors]
            anchor_str = " | ".join(anchors) if anchors else "none"
            lines.append(
                f"- {digest.task_id}: {status} ({n_pass}/{n_att}); "
                f"category={digest.failure_category or 'unknown'}; "
                f"components=[{components}]; evidence=[{anchor_str}]"
            )
            prior = _digest_prior_ship_history(digest)
            if prior:
                lines.append(f"    prior_history: {prior}")
        return "\n".join(lines)

    # -- prompt assembly ------------------------------------------------------

    def _build_prompt(
        self,
        summary: str,
        *,
        truncation: tuple[str, ...],
        retry_error: str | None,
    ) -> str:
        parts = [
            self.prompt,
            f"\n\nROUND PORTFOLIO + EVIDENCE:\n{summary}",
        ]
        if truncation:
            parts.append("\n\nINPUT NOTES: " + "; ".join(truncation))
        if retry_error:
            parts.append(
                "\n\nYour previous response was rejected: "
                f"{retry_error}. Return ONLY a single valid JSON object with the "
                "required keys and nothing else."
            )
        return "".join(parts)

    # -- parsing / validation / mapping --------------------------------------

    def _parse_review_json(self, text: str) -> tuple[dict | None, str | None]:
        block = _first_json_object(text)
        if block is None:
            return None, "no JSON object found in response"
        try:
            obj = json.loads(block)
        except (ValueError, TypeError) as exc:
            return None, f"json.loads failed: {exc}"
        if not isinstance(obj, dict):
            return None, "top-level JSON value is not an object"
        for key in ("ranked_candidate_ids", "verdicts", "rejections", "revision_requests"):
            value = obj.get(key)
            if value is not None and not isinstance(value, list):
                return None, f"'{key}' must be a JSON array"
        if "no_op" in obj and not isinstance(obj.get("no_op"), bool):
            return None, "'no_op' must be a boolean"
        return obj, None

    def _map_review(
        self,
        context: CriticContext,
        candidates: tuple[CandidateArtifact, ...],
        obj: Mapping[str, Any],
    ) -> CriticReview:
        actual_ids = [candidate.candidate_id for candidate in candidates]
        id_set = set(actual_ids)
        by_id = {candidate.candidate_id: candidate for candidate in candidates}
        notes: list[str] = [
            str(concern).strip()
            for concern in (obj.get("strategy_concerns") or [])
            if str(concern).strip()
        ]

        # -- rejections: known candidate ids only ----------------------------
        rejections: list[CriticRejection] = []
        rejected_ids: set[str] = set()
        for raw in obj.get("rejections") or []:
            if not isinstance(raw, Mapping):
                continue
            cid = str(raw.get("candidate_id", "")).strip()
            if cid not in id_set:
                notes.append(f"dropped rejection of unknown candidate id {cid!r}")
                continue
            if cid in rejected_ids:
                continue
            rejected_ids.add(cid)
            rejections.append(
                CriticRejection(cid, str(raw.get("reason", "")).strip() or "rejected by Critic")
            )

        # -- revision requests: known ids, AT MOST ONE (paper §4.3) ----------
        revision_requests: list[RevisionRequest] = []
        revision_target: str | None = None
        revision_details: dict[str, str] | None = None
        for raw in obj.get("revision_requests") or []:
            if not isinstance(raw, Mapping):
                continue
            cid = str(raw.get("candidate_id", "")).strip()
            reason = str(raw.get("reason", "")).strip()
            instructions = str(raw.get("instructions", "")).strip()
            if cid not in id_set:
                notes.append(f"dropped revision request for unknown candidate id {cid!r}")
                continue
            if revision_requests:
                notes.append(
                    f"dropped extra revision request for {cid!r}; only one revision "
                    "is allowed (paper §4.3)"
                )
                continue
            revision_requests.append(
                RevisionRequest(cid, reason or "revision requested by Critic")
            )
            revision_target = cid
            revision_details = {"reason": reason, "instructions": instructions}

        no_op_reasons = tuple(
            str(reason).strip()
            for reason in (obj.get("no_op_reasons") or [])
            if str(reason).strip()
        )
        if bool(obj.get("no_op")):
            # A no-op review cannot also rank candidates (CriticReview guard).
            return CriticReview(
                rejections=tuple(rejections),
                no_op=True,
                no_op_reasons=no_op_reasons
                or ("llm_critic requested a whole-round no-op (no reason given)",),
                strategy_concerns=tuple(notes),
            )

        # -- verdicts: known candidate ids only ------------------------------
        verdicts: list[CandidateVerdict] = []
        for raw in obj.get("verdicts") or []:
            if not isinstance(raw, Mapping):
                continue
            cid = str(raw.get("candidate_id", "")).strip()
            if cid not in id_set:
                notes.append(f"dropped verdict for unknown candidate id {cid!r}")
                continue
            rank_raw = raw.get("rank")
            try:
                rank = int(rank_raw) if rank_raw is not None else None
            except (TypeError, ValueError):
                rank = None
            verdicts.append(
                CandidateVerdict(
                    candidate_id=cid,
                    rank=rank,
                    mutation_surface=self._surface(by_id[cid]),
                    reasons=tuple(
                        str(item).strip()
                        for item in (raw.get("reasons") or [])
                        if str(item).strip()
                    ),
                )
            )

        # -- ship_ranking: repair to a permutation of the non-rejected ids ---
        non_rejected = [cid for cid in actual_ids if cid not in rejected_ids]
        ranked, repair_notes = self._repair_ranking(
            obj.get("ranked_candidate_ids") or [], non_rejected, rejected_ids, id_set
        )
        notes.extend(repair_notes)

        # -- whole-round regression veto (parity with DeterministicCritic) ---
        # F-B: demoted (non-shipped) regressions become visible, non-blocking
        # concerns; strict accountability returns none, so ``notes`` is unchanged.
        veto, demoted_concerns = self._regression_veto(context, candidates, rejected_ids)
        notes.extend(demoted_concerns)
        if veto is not None:
            return CriticReview(
                rejections=tuple(rejections),
                no_op=True,
                no_op_reasons=(veto,),
                strategy_concerns=tuple(notes),
            )

        # Record the single surviving revision request so the recipe's revision
        # producer (which only sees slot.revision_of) can inject the instructions.
        if (
            self.revision_sink is not None
            and revision_target is not None
            and revision_details is not None
        ):
            self.revision_sink[revision_target] = revision_details

        return CriticReview(
            ranked_candidate_ids=tuple(ranked),
            verdicts=tuple(verdicts),
            rejections=tuple(rejections),
            revision_requests=tuple(revision_requests),
            strategy_concerns=tuple(notes),
        )

    @staticmethod
    def _surface(candidate: CandidateArtifact) -> tuple[str, ...]:
        return tuple(
            sorted(
                {
                    str(change.get("path", "")).strip()
                    for change in candidate.manifest.file_changes
                    if str(change.get("path", "")).strip()
                }
            )
        )

    @staticmethod
    def _repair_ranking(
        raw_ranked: Sequence[Any],
        non_rejected: Sequence[str],
        rejected_ids: set[str],
        id_set: set[str],
    ) -> tuple[list[str], list[str]]:
        """Deterministically repair the model's ranking into a permutation.

        Drops unknown/rejected/duplicate ids (each noted) and appends any
        non-rejected id missing from the ranking in candidate-id order. The result
        is exactly the non-rejected set, ordered by the model where it was valid.
        """
        notes: list[str] = []
        ranked: list[str] = []
        seen: set[str] = set()
        for raw in raw_ranked:
            cid = str(raw).strip()
            if cid not in id_set:
                notes.append(f"dropped ranked unknown candidate id {cid!r}")
                continue
            if cid in rejected_ids:
                notes.append(f"dropped ranked candidate {cid!r} that was also rejected")
                continue
            if cid in seen:
                notes.append(f"dropped duplicate ranked candidate id {cid!r}")
                continue
            seen.add(cid)
            ranked.append(cid)
        missing = sorted(cid for cid in non_rejected if cid not in seen)
        if missing:
            notes.append(
                "ranking repaired: appended non-rejected candidates missing from "
                f"ship_ranking in candidate-id order: {missing}"
            )
            ranked.extend(missing)
        return ranked, notes

    def _regression_veto(
        self,
        context: CriticContext,
        candidates: tuple[CandidateArtifact, ...],
        rejected_ids: set[str],
    ) -> "tuple[str | None, tuple[str, ...]]":
        """The DeterministicCritic whole-round veto, reused so the LLM cannot bypass it.

        ``DeterministicCritic._unresolved_regressions`` is a stateless staticmethod;
        calling it directly reuses the exact deterministic logic without
        duplicating it and without touching critic.py. Computed over the surviving
        (non-rejected) candidates, matching how the deterministic Critic computes it
        over its post-audit eligible set.

        Returns ``(veto_reason_or_None, demoted_concerns)``. F-B: under shipped_only
        accountability only shipped-caused regressions can produce the veto; the
        rest are returned as demoted ``strategy_concerns``. Strict accountability
        (``context.shipped_regressions is None``) puts every regression in the gate
        set and returns no demoted concerns, so the veto is byte-identical.
        """
        surviving = [
            candidate for candidate in candidates if candidate.candidate_id not in rejected_ids
        ]
        gate_regressions, demoted_regressions = regressions_for_gate(context)
        demoted_concerns = tuple(
            demoted_regression_concern(task_id)
            for task_id in DeterministicCritic._unresolved_regressions(
                demoted_regressions, surviving
            )
        )
        unresolved = DeterministicCritic._unresolved_regressions(
            gate_regressions, surviving
        )
        if not unresolved:
            return None, demoted_concerns
        veto = (
            "whole-round no-op: regressions were neither handled in tasks_at_risk "
            f"nor explained: {', '.join(unresolved)}"
        )
        return veto, demoted_concerns

    # -- LLM plumbing / fallback ---------------------------------------------

    async def _complete(self, prompt: str) -> str:
        # Plain async completion on the recipe's meta provider — the same seam A1
        # and A2 use. Temperature is left at the provider default.
        from harnessx.core.events import Message

        response = await self.provider.complete(
            [Message(role="user", content=prompt)], []
        )
        return str(getattr(response, "content", "") or "")

    async def _wholesale_fallback(
        self,
        context: CriticContext,
        candidates: tuple[CandidateArtifact, ...],
        reason: str,
    ) -> CriticReview:
        base = await self.fallback.review(context=context, candidates=candidates)
        return CriticReview(
            ranked_candidate_ids=base.ranked_candidate_ids,
            verdicts=base.verdicts,
            rejections=base.rejections,
            revision_requests=base.revision_requests,
            no_op=base.no_op,
            no_op_reasons=base.no_op_reasons,
            strategy_concerns=(f"llm_critic_fell_back: {reason};", *base.strategy_concerns),
            unexplored_failure_clusters=base.unexplored_failure_clusters,
            unexplored_failure_buckets=base.unexplored_failure_buckets,
        )


CLUSTER_SOURCES: tuple[str, ...] = ("gaia_level", "capability")
DEFAULT_CLUSTER_SOURCE = "gaia_level"
DEFAULT_CLUSTER_MIN_SIZE = 8


def _merge_small_clusters(
    assignment: dict[str, str], min_size: int
) -> tuple[dict[str, str], dict[str, str]]:
    """Fold clusters below ``min_size`` into their most similar large cluster.

    Routing is argmax *per cluster* and the seesaw tests a candidate only on the
    tasks routed to its variant (SS4.5 p.11), so a cluster of two tasks gives the
    gate almost no power -- the measured noise floor is already SD 4.57pp at
    n=103. Similarity is Jaccard over the ``+``-separated capability sets, so a
    small cluster joins the large one it most resembles rather than a catch-all
    bucket: ``browse+search+verify`` lands in ``browse+compute+search+verify``,
    not in a shapeless "misc".

    Ties break on the larger target, then on the lexicographically smaller id, so
    the merge is deterministic and reproducible from the frozen map alone.
    """
    sizes: dict[str, int] = {}
    for cluster_id in assignment.values():
        sizes[cluster_id] = sizes.get(cluster_id, 0) + 1
    big = sorted(cid for cid, n in sizes.items() if n >= min_size)
    small = sorted(cid for cid, n in sizes.items() if n < min_size)
    if not big:
        # Everything is small: merging would collapse the partition to nothing.
        # Better to route on the raw partition and report it than to invent one.
        return dict(assignment), {}

    def jaccard(a: str, b: str) -> float:
        sa, sb = set(a.split("+")), set(b.split("+"))
        return len(sa & sb) / len(sa | sb) if (sa | sb) else 0.0

    merged_into = {
        cid: max(big, key=lambda t: (jaccard(cid, t), sizes[t], [-ord(c) for c in t]))
        for cid in small
    }
    return (
        {task: merged_into.get(cid, cid) for task, cid in assignment.items()},
        merged_into,
    )


def _resolve_task_clusters(args: Any, level_map: Mapping[str, int]) -> tuple[dict[str, str], dict]:
    """Build the task -> cluster assignment and the provenance to record with it.

    SS4.5 routes each task to ``argmax_v S_hat(v, cluster(task))`` but never
    defines ``cluster``; ``router.cluster_of`` says as much in its own
    NotImplementedError. The choice is therefore ours and it is structural, not
    cosmetic: routing is argmax per cluster, so **at most ``min(K, n_clusters)``
    variants can ever carry tasks**. Under ``gaia_level`` there are three
    clusters, which caps an eight-variant pool at three loaded variants
    regardless of the gate.

    ``gaia_level`` (default) reproduces the previous hardcoded behaviour
    byte-for-byte. ``capability`` reads a frozen map keyed by the SET of D1-lite
    subtask types a task needs, computed from the task text alone before any
    attempt, so it cannot encode an outcome.
    """
    source = str(getattr(args, "cluster_source", DEFAULT_CLUSTER_SOURCE))
    if source not in CLUSTER_SOURCES:
        raise SystemExit(f"--cluster-source must be one of {CLUSTER_SOURCES}, got {source!r}")

    if source == "gaia_level":
        return (
            {task_id: f"gaia_level_{level}" for task_id, level in level_map.items()},
            {"cluster_source": "gaia_level", "cluster_map_path": None, "cluster_map_sha256": None},
        )

    raw_path = getattr(args, "cluster_map", None)
    if not raw_path:
        raise SystemExit("--cluster-source capability requires --cluster-map <path>")
    path = Path(raw_path)
    if not path.is_file():
        raise SystemExit(f"--cluster-map not found: {path}")
    blob = path.read_bytes()
    payload = json.loads(blob.decode("utf-8"))
    table = payload.get("clusters") if isinstance(payload, dict) else None
    if not isinstance(table, dict) or not table:
        raise SystemExit(f"--cluster-map {path}: no non-empty 'clusters' object")

    # Fail closed on partial coverage. Silently letting an unmapped task fall
    # through to cold-start would mean the run routes on a partition the frozen
    # map does not describe, and nothing downstream could tell.
    missing = sorted(set(level_map) - set(table))
    if missing:
        raise SystemExit(
            f"--cluster-map {path} is missing {len(missing)} of {len(level_map)} bed tasks "
            f"(e.g. {missing[:3]}); rebuild it against this bed"
        )

    assignment = {task_id: str(table[task_id]) for task_id in level_map}
    min_size = int(getattr(args, "cluster_min_size", DEFAULT_CLUSTER_MIN_SIZE))
    merged, merged_into = _merge_small_clusters(assignment, min_size) if min_size > 1 else (assignment, {})
    sizes: dict[str, int] = {}
    for cluster_id in merged.values():
        sizes[cluster_id] = sizes.get(cluster_id, 0) + 1
    return merged, {
        "cluster_source": "capability",
        "cluster_map_path": str(path),
        "cluster_map_sha256": hashlib.sha256(blob).hexdigest(),
        "cluster_min_size": min_size,
        "cluster_count_raw": len(set(assignment.values())),
        "cluster_count": len(sizes),
        "cluster_sizes": dict(sorted(sizes.items(), key=lambda kv: (-kv[1], kv[0]))),
        "cluster_merged_into": merged_into,
    }


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
        # --evolve-commit-bounce (F-A). ``off`` (default) is byte-identical.
        self.evolve_commit_bounce = str(
            getattr(args, "evolve_commit_bounce", DEFAULT_EVOLVE_COMMIT_BOUNCE)
        )
        if self.evolve_commit_bounce not in EVOLVE_COMMIT_BOUNCE_MODES:
            raise ValueError(
                f"evolve_commit_bounce must be one of {EVOLVE_COMMIT_BOUNCE_MODES}, "
                f"got {self.evolve_commit_bounce!r}"
            )
        # --regression-accountability (F-B). ``strict`` (default) is byte-identical.
        self.regression_accountability = str(
            getattr(args, "regression_accountability", DEFAULT_REGRESSION_ACCOUNTABILITY)
        )
        if self.regression_accountability not in REGRESSION_ACCOUNTABILITY_MODES:
            raise ValueError(
                f"regression_accountability must be one of {REGRESSION_ACCOUNTABILITY_MODES}, "
                f"got {self.regression_accountability!r}"
            )
        # --regression-baseline (M-23). ``global`` (default) is byte-identical: the
        # seesaw regression side keeps anchoring on the global cross-variant
        # ever_solved set. ``per_variant`` anchors each candidate on its own
        # variant's solve history (forwarded to the gate by the engine).
        self.regression_baseline = str(
            getattr(args, "regression_baseline", DEFAULT_REGRESSION_BASELINE)
        )
        if self.regression_baseline not in REGRESSION_BASELINE_MODES:
            raise ValueError(
                f"regression_baseline must be one of {REGRESSION_BASELINE_MODES}, "
                f"got {self.regression_baseline!r}"
            )
        #: F-B: rounds in which each variant's deployed config changed via an
        #: APPLY/FORK ship. Accumulated across rounds in ``_reconcile`` (never reset
        #: per round) and read by ``_shipped_caused_regressions`` to classify which
        #: regressions a shipped change actually caused. Only consulted under
        #: ``regression_accountability == 'shipped_only'``; in ``strict`` mode it is
        #: written but never read, so ``strict`` runs stay byte-identical.
        self._config_change_rounds: dict[str, list[int]] = {}
        # --force-gate: TEMPORARY plumbing probe. ``off`` keeps the gate
        # byte-identical (the engine receives the real ``run_gate``); ``apply``/
        # ``fork`` override the final decision to exercise the settlement chain.
        self.force_gate = str(getattr(args, "force_gate", "off"))
        if self.force_gate not in FORCE_GATE_MODES:
            raise ValueError(
                f"force_gate must be one of {FORCE_GATE_MODES}, got {self.force_gate!r}"
            )
        # --l2-cert: L2 evidence mechanism (SPEC §7.11). ``auto`` machine-certifies
        # undeclared tool-bucket Level-2 evidence from a candidate's own eval
        # trajectories under repo manifest-mode; ``off`` keeps stage 4 byte-identical.
        self.l2_cert = str(getattr(args, "l2_cert", DEFAULT_L2_CERT))
        if self.l2_cert not in L2_CERT_MODES:
            raise ValueError(
                f"l2_cert must be one of {L2_CERT_MODES}, got {self.l2_cert!r}"
            )
        # --ship-policy: round settlement (M-17, SPEC §7.14). ``first_wins``
        # (default) keeps the Algorithm-1 first-wins break byte-identical;
        # ``bucket_disjoint`` enables App B.1 ranked multi-ship in the engine.
        self.ship_policy = str(getattr(args, "ship_policy", DEFAULT_SHIP_POLICY))
        if self.ship_policy not in SHIP_POLICIES:
            raise ValueError(
                f"ship_policy must be one of {SHIP_POLICIES}, got {self.ship_policy!r}"
            )
        # --aegis-digester: Phase A1. ``deterministic`` (default) keeps the
        # byte-identical _EvidenceDigester; ``llm`` model-backs the Digester role.
        self.aegis_digester = str(getattr(args, "aegis_digester", DEFAULT_AEGIS_DIGESTER))
        if self.aegis_digester not in AEGIS_DIGESTER_MODES:
            raise ValueError(
                f"aegis_digester must be one of {AEGIS_DIGESTER_MODES}, "
                f"got {self.aegis_digester!r}"
            )
        # --aegis-planner: Phase A2. ``deterministic`` (default) keeps the
        # byte-identical _DeterministicPlanner; ``llm`` model-backs the Planner role.
        self.aegis_planner = str(getattr(args, "aegis_planner", DEFAULT_AEGIS_PLANNER))
        if self.aegis_planner not in AEGIS_PLANNER_MODES:
            raise ValueError(
                f"aegis_planner must be one of {AEGIS_PLANNER_MODES}, "
                f"got {self.aegis_planner!r}"
            )
        # --aegis-critic: Phase A3. ``deterministic`` (default) keeps the
        # byte-identical DeterministicCritic; ``llm`` model-backs the Critic role.
        self.aegis_critic = str(getattr(args, "aegis_critic", DEFAULT_AEGIS_CRITIC))
        if self.aegis_critic not in AEGIS_CRITIC_MODES:
            raise ValueError(
                f"aegis_critic must be one of {AEGIS_CRITIC_MODES}, "
                f"got {self.aegis_critic!r}"
            )
        # --aegis-prompts (P1-1): ``paper`` (default, paper-first house rule)
        # drives the LLM Planner/Critic and the always-LLM Evolver with the paper's
        # published App B.1 prompts (runtime-adapted only where our JSON/manifest
        # contract requires); ``ours`` keeps the byte-identical OURS constants as
        # the ablation arm.
        self.aegis_prompts = str(getattr(args, "aegis_prompts", DEFAULT_AEGIS_PROMPTS))
        if self.aegis_prompts not in AEGIS_PROMPTS_MODES:
            raise ValueError(
                f"aegis_prompts must be one of {AEGIS_PROMPTS_MODES}, "
                f"got {self.aegis_prompts!r}"
            )
        # Algorithm 1's alpha: explicit flag wins; otherwise mode-dependent
        # default (deterministic digester -> legacy 1.0, llm -> OURS 0.5).
        self.actionability_threshold = _resolve_actionability_threshold(
            getattr(args, "actionability_threshold", None), self.aegis_digester
        )
        self.retarget_after_freeze = bool(getattr(args, "retarget_after_freeze", False))
        if self.retarget_after_freeze:
            # A preview freeze is only sound while ``freeze_routing`` consumes no
            # RNG. Two paths in ``Router`` draw: epsilon-greedy exploration
            # (router.py:296, unreachable at epsilon<=0 thanks to the early return
            # at :291) and the random tie-break (:348). Under either, the preview
            # would advance the generator and the *real* freeze would then return
            # a different partition than it does today -- the flag would silently
            # change routing rather than only change which variant is targeted.
            epsilon = float(getattr(args, "epsilon", 0.0) or 0.0)
            tie_break = str(getattr(args, "tie_break", "fewest_attempts"))
            if epsilon > 0.0 or tie_break == "random":
                raise ValueError(
                    "--retarget-after-freeze needs a deterministic router: it previews "
                    "the freeze before picking a target, and a preview that draws from "
                    "the RNG would change the real freeze. Got "
                    f"epsilon={epsilon}, tie_break={tie_break!r}. Use epsilon=0 with a "
                    "deterministic tie-break, or drop the flag."
                )
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
        # A3 — Critic revision requests this round, keyed by the target candidate
        # id ({candidate_id: {"reason", "instructions"}}). The LLM Critic writes it
        # during review(); the revision producer reads it by ``slot.revision_of``.
        self._round_revision_requests: dict[str, dict[str, str]] = {}
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
        # SS4.5 never defines cluster(). The choice caps the pool: routing is
        # argmax per cluster, so at most min(K, n_clusters) variants can ever
        # carry tasks. Default reproduces the previous hardcoded gaia_level.
        task_clusters, self.cluster_provenance = _resolve_task_clusters(args, self.level_map)
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
            # Router has always accepted epsilon; nothing ever passed it, so
            # explore() was dead code at 0.0. Its own docstring states the
            # purpose: "stop a variant that lost early from being frozen out by
            # argmax forever". Default 0.0 keeps every prior run reproducible.
            epsilon=float(getattr(args, "epsilon", 0.0)),
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
            # Two recipe-layer wrappers over the real ``run_gate``, composed so L2
            # certification happens *inside* the real-gate call that force-gate
            # audits. ``_l2_certifying_gate`` returns ``run_gate`` unchanged unless
            # SPEC §7.11 certification is active (auto + repo manifest-mode + paper
            # candidates), and ``_forced_gate`` returns its argument unchanged when
            # off — so with both features off the engine still receives ``run_gate``
            # itself (byte-identical gate path).
            gate=_forced_gate(self.force_gate, _l2_certifying_gate(self, run_gate)),
            evidence=self.evidence,
            patience=patience,
            min_fork=min_fork,
            retirement_metric=retirement_metric,
            max_candidates_per_variant=self.candidates_per_round,
            record_selected_results=self.candidate_mode != "paper",
            ship_policy=self.ship_policy,
            regression_baseline=self.regression_baseline,
        )

    # ------------------------------------------------------------------
    # driver
    # ------------------------------------------------------------------

    def run(self, start_round: int = 0) -> list[RoundResult]:
        """Run up to ``num_rounds`` rounds, reconciling config lineage between them.

        ``start_round`` (default 0, byte-identical to the original loop) lets
        ``--resume`` continue from the round after the last settled one; the
        engine/pool/ledger must already be rehydrated (see
        :func:`experiments.variant_pool.resume.apply_resume_state`) before the
        first resumed round.
        """
        all_ids = {t.task_id for t in self.tasks if t.task_id}
        results: list[RoundResult] = []
        for round_idx in range(int(start_round), int(self.args.num_rounds)):
            self._round_candidates = {}
            self._round_traj_dir = {}
            self._round_records = {}
            self._round_candidate_memos = {}
            self._round_revision_requests = {}
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
                        eligible=self._retarget_eligible(round_idx, all_ids),
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

    def _retarget_eligible(self, round_idx: int, all_ids) -> set[str]:
        """Eligible evolve targets, optionally re-derived from a preview freeze.

        The target is picked here, in the recipe, *before* ``engine.run_round``
        freezes this round's routing (``engine.py:297``). Eligibility therefore
        reads ``variant.routed_tasks``, which still holds the **previous** round's
        partition. When the freeze then reassigns that variant's cluster to a
        stronger sibling, the target enters the round carrying nothing, the engine
        skips it as an empty cluster (``engine.py:316-319``), and the round yields
        no candidate at all. That is 6 of 15 rounds on s1k8b103.

        With ``--retarget-after-freeze`` we run ``freeze_routing`` once as a
        preview and keep only variants that will actually carry tasks. The preview
        is exact rather than an estimate: at ``epsilon=0`` with a deterministic
        tie-break the call reads the ledger and pool without mutating either and
        draws no RNG, so it returns precisely the partition the real freeze will
        return moments later (the constructor rejects the configurations where
        that stops holding). ``freeze_routing``'s own leak check is a ledger read,
        so running it twice is not a second write.

        Default off: the flag returns the unchanged eligible set, so routing,
        targets and the lock all stay byte-identical, and a resume of a run
        started before this flag existed still matches.
        """
        settled = self._variants_with_settled_trajectories()
        if not self.retarget_after_freeze:
            return settled
        preview = self.engine.router.freeze_routing(
            set(all_ids), self.pool, self.ledger, round_idx
        )
        carrying = set(preview.values())
        # Falling back to ``settled`` keeps the old behaviour in the corner where
        # no settled variant carries anything, rather than handing the selector an
        # empty set and turning a recoverable round into a hard failure.
        return {vid for vid in settled if vid in carrying} or settled

    def _variants_with_settled_trajectories(self) -> set[str]:
        """Variant ids eligible to be this round's evolve target.

        A variant qualifies only when it satisfies BOTH conditions:

        * it currently **holds routed tasks** (``variant.routed_tasks`` is
          non-empty), and
        * its last **settled trajectories dir exists** on disk
          (``_last_traj_dir``).

        Ruling (runs/forceprobe2 R2 starvation). The engine evaluates *before*
        it evolves, so any variant holding routed tasks this round necessarily
        has fresh settled trajectories by evolve time — the routed-tasks
        condition subsumes the trajectories condition in the organic flow. The
        trajectories check therefore stays only as a belt for reuse / edge paths
        (e.g. ``candidate_reuse``, where a variant can retain a stale
        trajectories dir after its tasks moved off it). Requiring routed tasks
        closes the actual forceprobe2 R2 bug: V1 was eligible under the
        trajectory-only rule (its dir survived via candidate_reuse) yet held
        zero routed tasks, so the engine — which skips empty-cluster variants
        (engine.py:262-263) — evolved nothing when a round was aimed at it.

        The trajectories half is still exactly the precondition
        :meth:`_run_paper_candidate_pipeline` raises on — a target with no
        settled trajectories dir cannot be evolved (runs/forceprobe2 P2).
        Passing this intersection as target-selection eligibility keeps a round
        from being aimed at a variant the evolve step cannot service or the
        engine will skip. Empty (e.g. before the R0 baseline seeds V0) makes
        selection fall back to the whole pool.
        """
        with_trajectories = {
            vid
            for vid, traj in self._last_traj_dir.items()
            if traj is not None and Path(traj).is_dir()
        }
        with_routed_tasks = {
            vid
            for vid, variant in self.pool.variants.items()
            if variant.routed_tasks
        }
        return with_trajectories & with_routed_tasks

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

    def _make_digester(self) -> DigesterStage:
        """The active Digester adapter (--aegis-digester).

        ``deterministic`` (default) returns the byte-identical
        :class:`_EvidenceDigester`. ``llm`` returns the model-backed
        :class:`_LLMDigester`, sharing the recipe's meta provider
        (``meta_agent.inner_model``'s ``main`` role — the same provider the
        meta calls use) and keeping the deterministic adapter as its fallback so
        a wholesale revert is byte-identical to the deterministic arm.
        """
        deterministic = _EvidenceDigester(self.evidence, self.pool)
        if self.aegis_digester != "llm":
            return deterministic
        provider = self.meta_agent.inner_model.get("main")
        return _LLMDigester(
            evidence=self.evidence,
            pool=self.pool,
            provider=provider,
            tasks_by_id=self.tasks_by_id,
            run_dir=self.run_dir,
            fallback=deterministic,
        )

    @property
    def _digester_adapter_name(self) -> str:
        """Truthful pipeline-audit name for the active Digester role."""
        return (
            "MetaModel_llm_digester"
            if self.aegis_digester == "llm"
            else "deterministic_evidence_store_fallback"
        )

    def _make_planner(self) -> PlannerStage:
        """The active Planner adapter (--aegis-planner).

        ``deterministic`` (default) returns the byte-identical
        :class:`_DeterministicPlanner`. ``llm`` returns the model-backed
        :class:`_LLMPlanner`, sharing the recipe's meta provider
        (``meta_agent.inner_model``'s ``main`` role — the same provider the
        Digester and evolve calls use) and keeping the deterministic adapter as
        its wholesale fallback so a revert is byte-identical to the deterministic
        arm.
        """
        deterministic = _DeterministicPlanner(self.candidates_per_round)
        if self.aegis_planner != "llm":
            return deterministic
        provider = self.meta_agent.inner_model.get("main")
        return _LLMPlanner(
            provider=provider,
            k_t=self.candidates_per_round,
            fallback=deterministic,
            prompt=(
                _PAPER_PLANNER_PROMPT
                if self.aegis_prompts == "paper"
                else _LLM_PLANNER_PROMPT
            ),
        )

    @property
    def _planner_adapter_name(self) -> str:
        """Truthful pipeline-audit name for the active Planner role."""
        return (
            "MetaModel_llm_planner"
            if self.aegis_planner == "llm"
            else "deterministic_failure_cluster_fallback"
        )

    def _make_critic(self) -> CriticStage:
        """The active Critic adapter (--aegis-critic).

        ``deterministic`` (default) returns the byte-identical
        :class:`~experiments.variant_pool.critic.DeterministicCritic`. ``llm``
        returns the model-backed :class:`_LLMCritic`, sharing the recipe's meta
        provider (``meta_agent.inner_model``'s ``main`` role — the same provider
        the Digester/Planner/evolve calls use), keeping the deterministic Critic as
        its wholesale fallback (so a revert is byte-identical to the deterministic
        arm), and given the round's revision sink so the revision producer can
        reach the Critic's instructions by ``slot.revision_of``.
        """
        deterministic = DeterministicCritic(self.evidence)
        if self.aegis_critic != "llm":
            return deterministic
        provider = self.meta_agent.inner_model.get("main")
        return _LLMCritic(
            provider=provider,
            fallback=deterministic,
            revision_sink=self._round_revision_requests,
            prompt=(
                _PAPER_CRITIC_PROMPT
                if self.aegis_prompts == "paper"
                else _LLM_CRITIC_PROMPT
            ),
        )

    @property
    def _critic_adapter_name(self) -> str:
        """Truthful pipeline-audit name for the active Critic role."""
        return (
            "MetaModel_llm_critic"
            if self.aegis_critic == "llm"
            else "deterministic_portfolio_fallback"
        )

    @property
    def _llm_aegis_reproduction(self) -> bool:
        """True ONLY when Digester, Planner AND Critic are all LLM (A1+A2+A3).

        This is the moment the pipeline-audit flag was reserved for: the full
        AEGIS three-role dialogue is model-backed (prompts are OURS
        reconstructions). Any deterministic role keeps it False.
        """
        return (
            self.aegis_digester == "llm"
            and self.aegis_planner == "llm"
            and self.aegis_critic == "llm"
        )

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
        digester = self._make_digester()
        planner = self._make_planner()

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

        async def _revision_producer(
            *,
            context: PipelineContext,
            plan: PlanningArtifact,
            slot: CandidateSlot,
        ) -> CandidateArtifact | None:
            # A3 Part 2. The adapter hands us the revision slot only
            # (``slot.revision_of`` = the parent candidate id); the Critic's
            # ``reason``+``instructions`` are looked up from the round revision
            # sink the LLM Critic populated, and injected into the revised
            # candidate's contract by ``_produce_paper_candidate``.
            revision = self._round_revision_requests.get(slot.revision_of or "")
            return await self._produce_paper_candidate(
                variant=variant,
                context=context,
                plan=plan,
                slot=slot,
                revision=revision,
            )

        critic = self._make_critic()
        pipeline = CandidatePipeline(
            digester=digester,
            planner=planner,
            evolver=IsolatedEvolverAdapter(
                producer=_producer,
                revision_producer=_revision_producer,
            ),
            critic=critic,
            k_t=self.candidates_per_round,
            actionability_threshold=float(
                self.actionability_threshold
            ),
        )
        _regressions = self._recent_regressions(variant)
        context = PipelineContext(
            round_idx=round_idx,
            target_variant=vid,
            current_config_path=Path(variant.config_path),
            trajectories_dir=trajectories_dir,
            output_root=output_root,
            memo_path=Path(variant.journal_path),
            regressions=_regressions,
            failure_buckets=self._failure_buckets(variant),
            # F-B: only classify shipped-caused regressions under shipped_only;
            # ``None`` keeps the veto byte-identical in the default strict mode.
            shipped_regressions=(
                self._shipped_caused_regressions(variant, _regressions)
                if self.regression_accountability == "shipped_only"
                else None
            ),
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
        revision: Mapping[str, str] | None = None,
    ) -> CandidateArtifact | None:
        """Run MetaAgent in one isolated slot and require a valid manifest.

        Two faults from ``runs/forkprobe_11`` are fixed here in the recipe layer:
        (1) manifest format via ``--manifest-mode`` (repo adapter vs paper
        schema injection) and (2) no-config outcomes via ``--evolve-retry``.
        Neither touches ``harnessx/``.

        A3 Part 2 — revision. When ``revision`` is given (the Critic's
        ``{"reason", "instructions"}`` for the parent candidate), this runs in the
        revision slot ``<parent>-revision-01`` allocated by the adapter, injects
        the request into the meta contract (``critic_revision_request`` in the
        planner brief), and finalizes the revised manifest with a gate-valid
        paper-shape candidate id (:func:`_revision_manifest_candidate_id`) whose
        ``iterates_from`` points at the parent — the two things the pipeline's
        revision path checks before it lets the revised candidate rejoin the
        batch. ``revision=None`` keeps the ordinary proposal byte-identical.
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
        # F-A: the bounce writes bounce_used / bounce_outcome here when
        # --evolve-commit-bounce is on; it stays ``{}`` (and the ``**`` merges add
        # nothing) when off, so the default audit is byte-identical.
        bounce_audit: dict[str, Any] = {}
        try:
            outcome = await _evolve_candidate_with_retry(
                slot_agent=slot_agent,
                slot=slot,
                manifest_mode=self.manifest_mode,
                target_variant=context.target_variant,
                planner_brief=_planner_brief_with_revision(
                    _planner_brief_with_regressions(
                        asdict(brief), context.regressions
                    ),
                    revision,
                ),
                base_evolve_kwargs=base_kwargs,
                max_retries=self.evolve_retry,
                paper_evolver_guidance=(
                    _PAPER_EVOLVER_GUIDANCE if self.aegis_prompts == "paper" else None
                ),
                commit_bounce=self.evolve_commit_bounce,
                bounce_max_steps=_BOUNCE_MAX_STEPS,
                bounce_audit=bounce_audit,
            )
        except Exception as exc:  # noqa: BLE001 - adapter converts to ProposalFailure
            self._candidate_meta[slot_id] = {
                "manifest_mode": self.manifest_mode,
                "provenance": None,
                "attempts": self.evolve_retry + 1,
                "retries": self.evolve_retry,
                "parse_status": "no_config_after_retry",
                "paper_only_gaps": [],
                **bounce_audit,
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
            **bounce_audit,
        }
        self._candidate_meta[slot_id] = meta

        if context.current_config_path.read_bytes() == config_path.read_bytes():
            meta["parse_status"] = "explicit_noop"
            raise ValueError(f"{slot_id}: byte-identical explicit no-op")

        # Manifest sourcing. Retries write to isolated subdirs, so the manifest
        # (if any) lives beside the config the winning attempt returned.
        #
        # ``paper`` mode requires manifest.yaml (paper fidelity). ``repo`` mode
        # (default) makes it OPTIONAL: the meta-agent's burden is one artefact
        # (config.yaml + its journal entry), not two. When it wrote no
        # manifest.yaml the caller adapts one from repo-native products (the
        # config diff + the journal's levers/predicted_affected); a
        # journal-vocabulary manifest.yaml, if it still wrote one, is honoured.
        manifest_path = config_path.parent / "_meta_scratch" / "manifest.yaml"

        if self.manifest_mode == "paper":
            if not manifest_path.is_file():
                meta["parse_status"] = "manifest_missing"
                raise FileNotFoundError(
                    f"{slot_id}: required manifest missing: {manifest_path}"
                )
            try:
                manifest = ChangeManifest.from_yaml(
                    manifest_path.read_text(encoding="utf-8")
                )
            except Exception as exc:  # noqa: BLE001 - preserve parser detail in audit
                meta["parse_status"] = "format_mismatch"
                raise ValueError(
                    f"{slot_id}: invalid manifest.yaml (paper schema mismatch): {exc}"
                ) from exc
        elif manifest_path.is_file():
            # repo mode, manifest.yaml present: accept the journal vocabulary.
            meta["manifest_source"] = "manifest_yaml"
            try:
                manifest = adapt_repo_journal_manifest(
                    manifest_path.read_text(encoding="utf-8"),
                    fallback_candidate_id=slot_id,
                    fallback_target_variant=context.target_variant,
                )
            except RepoJournalFormatError as exc:
                meta["parse_status"] = "format_mismatch"
                raise ValueError(
                    f"{slot_id}: repo manifest format mismatch: {exc}"
                ) from exc
        else:
            # repo mode, no manifest.yaml: adapt from repo-native products.
            meta["manifest_source"] = "repo_journal_entry"
            manifest = _repo_manifest_from_journal(
                memo_path=slot.memo_path,
                current_config_path=context.current_config_path,
                new_config_path=config_path,
                candidate_id=slot_id,
                target_variant=context.target_variant,
            )

        # The contract hands the meta-agent the repo-gate-safe ALIAS of the
        # slot id, so a manifest echoing that alias is OUR candidate under its
        # outward name, not a disagreement. Map it back so every internal
        # artefact (gate, ledger, reports) keys on the paper-shape slot id;
        # the alias itself stays in the audit record.
        alias = outward_candidate_id(slot_id)
        if manifest.candidate_id == alias and alias != slot_id:
            meta["repo_candidate_id"] = alias
            manifest = manifest.model_copy(update={"candidate_id": slot_id})

        if revision is not None:
            # A3 Part 2. The revision slot id (``C-R1-01-revision-01``) is not a
            # gate-valid manifest candidate id, so finalize a paper-shape one and
            # point iterates_from at the parent — both required for the pipeline to
            # accept the revised candidate back into the batch.
            revised_id = _revision_manifest_candidate_id(slot)
            manifest = manifest.model_copy(
                update={"candidate_id": revised_id, "iterates_from": slot.revision_of}
            )
            meta["revision_of"] = slot.revision_of

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

    def _shipped_caused_regressions(
        self, variant: Any, regressions: Sequence[str]
    ) -> tuple[str, ...]:
        """The subset of ``regressions`` a shipped APPLY/FORK config change caused (F-B).

        A regression is a task whose last two settled digests went solved ->
        unsolved (the same rule as :meth:`_recent_regressions`). It is
        *shipped-caused* when a config change (APPLY/FORK, tracked in
        ``self._config_change_rounds``) landed on the failing carrier lineage
        strictly after the last passing round and at or before the failing round —
        i.e. the failing eval ran under a newly-shipped config, not the one the
        task last passed under. With zero ships (``self._config_change_rounds``
        empty) nothing is shipped-caused, so every flagged regression is classified
        as a rejected-candidate gate regression or zero-ship inter-round variance
        (runs/a1big4 R2/R3). Only consulted under ``shipped_only`` accountability.
        """
        regression_set = set(regressions)
        if not regression_set:
            return ()
        # Exact fast path: no config has ever changed -> nothing is shipped-caused.
        if not any(self._config_change_rounds.values()):
            return ()
        routed = set(variant.routed_tasks)
        history: dict[str, list[TaskDigest]] = {}
        for digest in self.evidence.iter_digests():
            if digest.task_id in routed:
                history.setdefault(digest.task_id, []).append(digest)
        shipped: list[str] = []
        for task_id in regression_set:
            digests = history.get(task_id)
            if not digests or len(digests) < 2:
                continue
            ordered = sorted(digests, key=lambda item: (item.round_idx, item.variant_id))
            prev, last = ordered[-2], ordered[-1]
            if not (prev.solved and not last.solved):
                continue  # defensive: only classify genuine solved->unsolved flips
            low, high = prev.round_idx, last.round_idx
            carriers = {prev.variant_id, last.variant_id}
            if any(
                low < change_round <= high
                for carrier in carriers
                for change_round in self._config_change_rounds.get(carrier, ())
            ):
                shipped.append(task_id)
        return tuple(sorted(shipped))

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
                "digester": self._digester_adapter_name,
                "planner": self._planner_adapter_name,
                "evolver": "MetaAgent_isolated_slots",
                "critic": self._critic_adapter_name,
                # True ONLY when Digester, Planner AND Critic are all LLM (A1+A2+A3
                # complete); any deterministic role keeps it False.
                "llm_aegis_reproduction": self._llm_aegis_reproduction,
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
                # F-B: this variant's deployed config changed this round (APPLY).
                self._config_change_rounds.setdefault(vid, []).append(result.round_idx)
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
            # F-B: the fork child is deployed with a newly-shipped config this round.
            self._config_change_rounds.setdefault(child_id, []).append(result.round_idx)

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
        l2_cert_counts: dict[str, int] = {}
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
            cert = record.get("l2_certification")
            if isinstance(cert, dict):
                outcome = str(cert.get("outcome"))
                l2_cert_counts[outcome] = l2_cert_counts.get(outcome, 0) + 1
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
        # SPEC §7.11 (M-22): one line only when the recipe machine-certified L2
        # evidence for at least one candidate this run (乙 fallback fired).
        if l2_cert_counts:
            lines.insert(
                len(lines) - 1,
                f"- L2 machine-certification (OURS 乙, M-22): {l2_cert_counts}",
            )
        return "\n".join(lines)

    def _dump_final(self) -> None:
        """Write the RunReport (final+peak+curve+by-level), pool axis, comparison.json."""
        try:
            md = self.report.to_markdown(level_map=self.level_map)
            banner = _forced_gate_banner(self.force_gate)
            if banner:
                md = f"{banner}\n\n{md}"
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
                    # A3 — one composed string across all three LLM-AEGIS roles;
                    # byte-identical to the pre-A1 literal when all deterministic.
                    # P1-1: ``,prompts=<mode>`` is appended only when a role is llm.
                    _composed_pipeline_adapter(
                        self.aegis_digester,
                        self.aegis_planner,
                        self.aegis_critic,
                        all_deterministic_literal=(
                            "deterministic Digester/Planner/Critic fallbacks + MetaAgent Evolver"
                        ),
                        prompts_mode=self.aegis_prompts,
                    )
                    if self.candidate_mode == "paper"
                    else "legacy MetaAgent single proposal"
                ),
                # P1-1: prompts mode is ALSO recorded as a dedicated field so it is
                # visible even in all-deterministic runs (where the always-LLM
                # Evolver still uses the selected prompt but the composed string
                # above keeps its byte-identical literal).
                "aegis_prompts": (
                    self.aegis_prompts if self.candidate_mode == "paper" else None
                ),
                "aegis_prompts_provenance": (
                    "paper: LLM roles + Evolver use the paper's App B.1 published "
                    "prompts (runtime-adapted only for our JSON/manifest contract); "
                    "ours: byte-identical OURS constants (P1-1)"
                    if self.candidate_mode == "paper"
                    else None
                ),
                "llm_aegis_reproduction": self._llm_aegis_reproduction,
                "actionability_threshold": (
                    float(self.actionability_threshold)
                    if self.candidate_mode == "paper"
                    else None
                ),
                "actionability_threshold_provenance": (
                    OURS_ACTIONABILITY_THRESHOLD_PROVENANCE
                    if self.candidate_mode == "paper"
                    else None
                ),
                "cluster_mode": str(getattr(self.args, "cluster_mode", "routed")),
                # Was hardcoded "gaia_level" while the flag existed nowhere; now
                # that cluster(task) is selectable, a literal here would let the
                # record claim a partition the run did not use. The provenance
                # dict also carries the map digest and the resulting sizes,
                # without which "n_clusters" is unverifiable after the fact.
                **self.cluster_provenance,
                "epsilon": float(getattr(self.args, "epsilon", 0.0)),
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


def _maybe_add_step_countdown(config: Any, mode: str) -> Any:
    """Append a ``StepCountdownProcessor`` to the deployed H0 config when ``mode == 'on'``.

    Config-level addition only — ``benchmarks/`` is never touched and H0 stays
    byte-identical when off (the *same* object is returned unchanged). When on, the
    processor is serialized to a ``_target_`` dict and appended to
    ``config.processors`` so candidates authored FROM the deployed config inherit
    it with no special handling (each candidate is a full config built from this
    one).
    """
    if mode == "off":
        return config
    if mode != "on":
        raise ValueError(f"--step-countdown must be 'off' or 'on', got {mode!r}")
    import dataclasses as _dcs

    from harnessx.core.harness import _serialize_processor
    from harnessx.processors.control.step_countdown import StepCountdownProcessor

    proc_dict = _serialize_processor(StepCountdownProcessor())
    if not proc_dict:
        return config
    return _dcs.replace(config, processors=[*config.processors, proc_dict])


def _step_countdown_provenance(mode: str) -> "str | None":
    """Byte-safe lock record for ``--step-countdown``.

    Same pattern as ``--force-gate`` / ``--ship-policy``: ``Hyperparams`` is a
    frozen dataclass and ``experiment_lock.py`` must not be modified, so a
    non-default mode is recorded as a provenance warning. Returns ``None`` for
    ``off`` (a default run's lock stays byte-identical); returns the warning
    string for ``on``.
    """
    if mode == "off":
        return None
    return (
        f"step_countdown={mode} ENABLED (run_variant_pool composition-native step "
        "budget render, mirrors smolagents' per-turn remaining-step count): a "
        "StepCountdownProcessor was appended to the deployed H0 config, so the frozen "
        "h0.config_sha256 reflects the added processor. The H0 processor DEFAULTS are "
        "unchanged and an off run's config + lock stay byte-identical"
    )


def _maybe_use_serper_backend(config: Any, backend: str) -> Any:
    """Swap the ``WebSearch`` tool for the Serper-first drop-in when ``backend == 'serper'``.

    W1. ``chain`` (default) returns the *same* config object unchanged, so H0 and
    every candidate derived from it stay byte-identical and ``harnessx/tools/contrib``
    is never even imported. ``serper`` replaces the live H0 tool_registry's
    ``WebSearch`` entry in place with ``serper_web_search_tool`` (identical
    name/description/schema, Serper-first fn). Because the swapped-in tool carries
    ``__hx_target__``, when the H0 config is serialised to ``V0/config.yaml`` the
    entry round-trips as a ``tool_registry.custom`` import path, so every candidate
    authored FROM H0 resolves ``WebSearch`` to the same Serper backend. Only the
    recipe layer is touched; ``harnessx/`` and ``benchmarks/`` are not.
    """
    if backend == "chain":
        return config
    if backend != "serper":
        raise ValueError(f"--search-backend must be one of {SEARCH_BACKENDS}, got {backend!r}")
    from harnessx.tools.contrib.serper_search import serper_web_search_tool

    registry = getattr(config, "tool_registry", None)
    tools = getattr(registry, "_tools", None)
    if isinstance(tools, dict) and "WebSearch" in tools:
        # In-place, replace=True: keeps the tool name "WebSearch" so the worker is
        # unaware and the lock's tool_registry name list is unchanged.
        registry.register(serper_web_search_tool, replace=True)
    else:
        logger.warning(
            "--search-backend serper: no 'WebSearch' tool in the H0 registry to swap; "
            "leaving the tool set unchanged"
        )
    return config


def _search_backend_provenance(mode: str) -> "str | None":
    """Byte-safe lock record for ``--search-backend`` (same pattern as ``--step-countdown``).

    Returns ``None`` for the default ``chain`` (the lock stays byte-identical);
    a provenance warning otherwise.
    """
    if mode == DEFAULT_SEARCH_BACKEND:
        return None
    return (
        f"search_backend={mode} ENABLED (W1): the deployed H0 WebSearch tool was "
        "replaced by the Serper-first drop-in (harnessx.tools.contrib.serper_search), "
        "so the frozen h0.config_sha256 reflects WebSearch moving from tool_registry."
        "builtin to tool_registry.custom. The tool name/description/schema are "
        "unchanged and a 'chain' run's config + lock stay byte-identical"
    )


def _evolve_commit_bounce_provenance(mode: str) -> "str | None":
    """Byte-safe lock record for ``--evolve-commit-bounce`` (F-A).

    Returns ``None`` for the default ``off`` (the lock stays byte-identical); a
    provenance warning otherwise.
    """
    if mode == DEFAULT_EVOLVE_COMMIT_BOUNCE:
        return None
    return (
        f"evolve_commit_bounce={mode} ENABLED (F-A): a no-config meta slot gets one "
        f"short (<= {_BOUNCE_MAX_STEPS} steps) 'commit a decision' bounce before it is "
        "failed. This adds meta-agent invocations (audited as bounce_used / "
        "bounce_outcome) and can turn an otherwise-failed attempt into a settled "
        "config.yaml, so it is not comparable byte-for-byte with an 'off' run"
    )


def _regression_accountability_provenance(mode: str) -> "str | None":
    """Byte-safe lock record for ``--regression-accountability`` (F-B).

    Returns ``None`` for the default ``strict`` (the lock stays byte-identical); a
    provenance warning otherwise.
    """
    if mode == DEFAULT_REGRESSION_ACCOUNTABILITY:
        return None
    return (
        f"regression_accountability={mode} ENABLED (F-B): the whole-round no-op veto "
        "hard-gates only regressions a shipped APPLY/FORK config change caused; "
        "rejected-candidate gate regressions and zero-ship inter-round variance are "
        "demoted to non-blocking strategy_concerns. This changes which rounds no-op, "
        "so it is not comparable byte-for-byte with a 'strict' run"
    )


def _cluster_source_provenance(args: Any) -> "str | None":
    """Byte-safe lock record for ``--cluster-source`` / ``--cluster-min-size``.

    ``Hyperparams.cluster_source`` carries the mode, but the frozen dataclass has
    nowhere for the map digest -- and without it "capability, 5 clusters" is an
    unverifiable claim, since the partition lives in a file outside the repo.
    Returns ``None`` for the default so a ``gaia_level`` lock stays byte-identical.
    """
    source = str(getattr(args, "cluster_source", DEFAULT_CLUSTER_SOURCE))
    if source == DEFAULT_CLUSTER_SOURCE:
        return None
    raw_path = getattr(args, "cluster_map", None)
    digest = "unresolved"
    if raw_path and Path(raw_path).is_file():
        digest = hashlib.sha256(Path(raw_path).read_bytes()).hexdigest()
    return (
        f"cluster_source={source} ENABLED: cluster(task) is the SET of D1-lite "
        f"subtask types the task needs, from map {raw_path!r} sha256={digest}, "
        f"merged below --cluster-min-size="
        f"{int(getattr(args, 'cluster_min_size', DEFAULT_CLUSTER_MIN_SIZE))}. The paper "
        "does not define cluster(); routing is argmax per cluster, so this changes "
        "min(K, n_clusters) -- the ceiling on how many variants can carry tasks at "
        "all. Not comparable byte-for-byte with a 'gaia_level' run"
    )


def _retarget_after_freeze_provenance(enabled: bool) -> "str | None":
    """Byte-safe lock record for ``--retarget-after-freeze``; ``None`` when off."""
    if not enabled:
        return None
    return (
        "retarget_after_freeze=on ENABLED (OURS): the evolve target is picked from a "
        "preview of this round's routing freeze instead of from the previous round's "
        "partition, so a variant whose cluster is about to be reassigned can no longer "
        "be selected and then enter the round carrying nothing. The paper does not "
        "define target selection at all (PAPER-METHODOLOGY-DEVIATIONS M-16), so both "
        "the old and new orderings are OURS; this one removes the zero-candidate rounds "
        "the old one produced (6 of 15 on s1k8b103). It changes which variant is "
        "evolved on rounds where the freeze moves a cluster, so it is not comparable "
        "byte-for-byte with an 'off' run"
    )


def _epsilon_provenance(epsilon: float) -> "str | None":
    """Byte-safe lock record for ``--epsilon``; ``None`` at the 0.0 default."""
    if epsilon <= 0.0:
        return None
    return (
        f"epsilon={epsilon} ENABLED (SPEC SS6.6): each task has this probability of "
        "being routed to a uniformly random variant instead of the cluster argmax. "
        "This buys measurements on (variant, cluster) cells that argmax would never "
        "sample -- an unmeasured cell sits at the Laplace prior and cannot win -- at "
        "an accuracy cost proportional to epsilon. Routing is no longer a "
        "deterministic function of the ledger, so per-round load is not comparable "
        "with an epsilon=0 run"
    )


def _regression_baseline_provenance(mode: str) -> "str | None":
    """Byte-safe lock record for ``--regression-baseline`` (M-23).

    Returns ``None`` for the default ``global`` (the lock stays byte-identical); a
    provenance warning otherwise.
    """
    if mode == DEFAULT_REGRESSION_BASELINE:
        return None
    return (
        f"regression_baseline={mode} ENABLED (M-23): the gate's seesaw judges a "
        "candidate's regressions against its OWN variant's solve history instead of "
        "the global cross-variant ever_solved set, so a task only ever solved by a "
        "different variant no longer counts as a regression for this candidate. This "
        "changes which candidates apply/fork/reject, so it is not comparable "
        "byte-for-byte with a 'global' run"
    )


def _task_reasoning_effort(args: Any) -> str | None:
    """Effective reasoning effort for the task (inner) agent, or ``None`` to omit.

    ``getattr`` with a default keeps callers/tests that never set the flag (an
    ``args`` object without the attribute) byte-identical: no flag => ``None`` =>
    no ``reasoning_effort`` key is sent.
    """
    return getattr(args, "reasoning_effort", None)


def _meta_reasoning_effort(args: Any) -> str | None:
    """Effective reasoning effort for the meta agent (Digester/Planner/Evolver/Critic).

    Falls back to the task value when ``--meta-reasoning-effort`` is unset; when
    both are unset the result is ``None`` (omit). A literal ``"none"`` is truthy,
    so ``--meta-reasoning-effort none`` overrides the fallback with ``"none"``.
    """
    return getattr(args, "meta_reasoning_effort", None) or getattr(args, "reasoning_effort", None)


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

    # --force-gate taint. Hyperparams is a frozen dataclass with no free-form
    # field and experiment_lock.py must not be modified, so the honest,
    # least-invasive record is a provenance warning (persisted in the lock and
    # surfaced by every reader). Only emitted when the probe is on, so an
    # ``off`` run's lock stays byte-identical.
    force_gate_mode = str(getattr(args, "force_gate", "off"))
    if force_gate_mode != "off":
        warnings.append(
            f"force_gate={force_gate_mode} ENABLED (run_variant_pool plumbing probe): "
            "the deterministic gate's final APPLY/FORK decision was overridden, so the "
            "settlement chain ran on real data but the resulting scores are NOT "
            "measurements"
        )

    # --ship-policy provenance (M-17, SPEC §7.14). Like --force-gate, Hyperparams
    # is a frozen dataclass we must not extend, so the byte-safe record for a
    # non-default policy is a provenance note (persisted + surfaced by readers).
    # first_wins (default) emits nothing, so a default run's lock stays
    # byte-identical. Unlike force_gate this is NOT a taint — bucket_disjoint is a
    # faithful App B.1 arm — but it must be recorded because it changes settlement
    # semantics and comparability with default-policy runs.
    ship_policy_mode = str(getattr(args, "ship_policy", DEFAULT_SHIP_POLICY))
    if ship_policy_mode != DEFAULT_SHIP_POLICY:
        warnings.append(
            f"ship_policy={ship_policy_mode} (M-17, App B.1 p.34 ranked multi-ship): "
            "round settlement ships every bucket-disjoint candidate in ranked order "
            "instead of the default Algorithm-1 first-wins single ship; a second "
            "same-variant APPLY is skipped as an unreconstructable whole-config merge "
            "and recorded in the audit. Not a taint, but not comparable byte-for-byte "
            "with a first_wins run"
        )

    # --step-countdown provenance. Same byte-safe pattern as --force-gate /
    # --ship-policy: recorded as a provenance warning only when enabled, so an
    # ``off`` run's lock stays byte-identical.
    _sc_warn = _step_countdown_provenance(str(getattr(args, "step_countdown", "off")))
    if _sc_warn:
        warnings.append(_sc_warn)

    # --search-backend / --evolve-commit-bounce / --regression-accountability /
    # --regression-baseline provenance. Same byte-safe pattern: each records a
    # warning ONLY when the flag is non-default, so a fully-default run's lock
    # stays byte-identical.
    for _flag_warn in (
        _search_backend_provenance(str(getattr(args, "search_backend", DEFAULT_SEARCH_BACKEND))),
        _evolve_commit_bounce_provenance(
            str(getattr(args, "evolve_commit_bounce", DEFAULT_EVOLVE_COMMIT_BOUNCE))
        ),
        _regression_accountability_provenance(
            str(getattr(args, "regression_accountability", DEFAULT_REGRESSION_ACCOUNTABILITY))
        ),
        _regression_baseline_provenance(
            str(getattr(args, "regression_baseline", DEFAULT_REGRESSION_BASELINE))
        ),
        _cluster_source_provenance(args),
        _epsilon_provenance(float(getattr(args, "epsilon", 0.0))),
        _retarget_after_freeze_provenance(bool(getattr(args, "retarget_after_freeze", False))),
    ):
        if _flag_warn:
            warnings.append(_flag_warn)

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
            # Effective (post-fallback) reasoning efforts; None when unset so a
            # pre-flag lock and an unset run compare equal (resume stays open).
            reasoning_effort=_task_reasoning_effort(args),
            meta_reasoning_effort=_meta_reasoning_effort(args),
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
            # Hyperparams is a frozen dataclass with a cluster_source field, so
            # this one records directly rather than via a provenance warning.
            cluster_source=str(getattr(args, "cluster_source", DEFAULT_CLUSTER_SOURCE)),
            epsilon=float(getattr(args, "epsilon", 0.0)),
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
                # A3 — one composed string across all three LLM-AEGIS roles. When
                # all deterministic it is byte-identical to the pre-A1 literal
                # (the Hyperparams default); the composed form is used only when a
                # role is llm (see :func:`_composed_pipeline_adapter`).
                _composed_pipeline_adapter(
                    str(getattr(args, "aegis_digester", DEFAULT_AEGIS_DIGESTER)),
                    str(getattr(args, "aegis_planner", DEFAULT_AEGIS_PLANNER)),
                    str(getattr(args, "aegis_critic", DEFAULT_AEGIS_CRITIC)),
                    all_deterministic_literal=(
                        "deterministic_evidence_digester_planner_critic+llm_metaagent_evolver"
                    ),
                    prompts_mode=str(getattr(args, "aegis_prompts", DEFAULT_AEGIS_PROMPTS)),
                )
                if paper_mode
                else "legacy_metaagent_single_proposal_per_active_variant"
            ),
            candidate_pipeline_semantics=(
                "runnable_fallback_not_full_llm_aegis_reproduction"
                if paper_mode
                else "legacy_ablation_no_structured_candidate_pipeline"
            ),
            actionability_threshold=(
                _resolve_actionability_threshold(
                    getattr(args, "actionability_threshold", None),
                    str(getattr(args, "aegis_digester", DEFAULT_AEGIS_DIGESTER)),
                )
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
            # labsmoke1 provenance gap: capture WHICH endpoint epoch this run used.
            # Only the DEEPSEEK_API_BASE URL is recorded (never the paired key);
            # unset => official/default endpoint sentinel. This enters the lock sha
            # and blocks a cross-epoch resume (env is a lock-blocking section).
            deepseek_api_base=os.environ.get("DEEPSEEK_API_BASE") or OFFICIAL_DEFAULT_ENDPOINT,
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
    # Local import: the pipeline module is the single source of truth for the
    # subtask cap, and importing it at module scope would be a cycle.
    from experiments.variant_pool.subtask_pipeline import DEFAULT_MAX_SUBTASKS

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
    parser.add_argument(
        "--reasoning-effort",
        choices=REASONING_EFFORT_CHOICES,
        default=None,
        help=(
            "Reasoning effort forwarded to the LiteLLM/vLLM endpoint for the task "
            "(inner) agent. Default (unset) sends no reasoning_effort key, keeping "
            "the request byte-identical to a pre-flag run. Measured behaviour is "
            "binary rather than graded: 'none' disables thinking, every other value "
            "enables it, with no monotone effect on reasoning length above that."
        ),
    )
    parser.add_argument(
        "--meta-reasoning-effort",
        choices=REASONING_EFFORT_CHOICES,
        default=None,
        help=(
            "Reasoning effort for the meta agent (Digester/Planner/Evolver/Critic). "
            "Unset falls back to --reasoning-effort; if both are unset, no "
            "reasoning_effort key is sent for either."
        ),
    )
    parser.add_argument("--clean", action="store_true", help="Wipe runs/<tag>/ before starting.")
    parser.add_argument("--no-judge", action="store_true", help="Disable LLMJudgeProcessor.")
    parser.add_argument("--evolve-cost", type=float, default=EVOLVE_COST_CAP_USD)
    parser.add_argument("--evolve-steps", type=int, default=EVOLVE_MAX_STEPS)
    parser.add_argument("--evolve-wall-clock", type=int, default=EVOLVE_WALL_CLOCK_S)
    parser.add_argument("--run-tag", default=None, help="Label for this run's runs/<tag>/ dir.")
    parser.add_argument(
        "--resume",
        default=None,
        help=(
            "Resume an interrupted run: a run tag under runs/ or a path to its dir. "
            "Rebuilds engine state from the last settled round and continues to --num-rounds "
            "or early stop. Mutually exclusive with --clean; --run-tag is derived from the dir."
        ),
    )
    _default_data = str(Path(__file__).resolve().parent / "data" / "webthinker_gaia_dev.json")
    parser.add_argument("--data-path", default=_default_data, help="Local GAIA JSON (webthinker schema); '' = HF download.")
    parser.add_argument("--attachments-dir", default=None, help="Dir of per-task attachment files.")
    parser.add_argument("--level", type=int, default=0, help="GAIA level (1/2/3); 0 = all.")
    parser.add_argument("--max-steps", type=int, default=MAX_STEPS, help=f"Per-task step cap. Default: {MAX_STEPS}.")
    parser.add_argument(
        "--step-countdown",
        choices=("off", "on"),
        default="off",
        help=(
            "Inject a refreshed, model-visible step-countdown line before each model "
            "call (smolagents-style remaining-step render) so the worker outputs a "
            "FINAL ANSWER before the step budget is exhausted. 'off' (default) keeps H0 "
            "byte-identical; 'on' appends a StepCountdownProcessor to the deployed config "
            "(candidates inherit it) and records a provenance warning in the lock."
        ),
    )
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
    parser.add_argument(
        "--cluster-source",
        choices=CLUSTER_SOURCES,
        default=DEFAULT_CLUSTER_SOURCE,
        help=(
            "What cluster(task) means -- the function SS4.5 uses but never defines. "
            "Routing is argmax per cluster, so at most min(K, n_clusters) variants "
            "can ever carry tasks: 'gaia_level' (default, byte-identical to the "
            "pre-flag hardcoding) gives 3 clusters and therefore caps a K=8 pool at "
            "3 loaded variants. 'capability' keys on the SET of D1-lite subtask "
            "types a task needs, read from --cluster-map."
        ),
    )
    parser.add_argument(
        "--cluster-map",
        default=None,
        help=(
            "Frozen task->capability-profile map (experiments/build_task_clusters.py). "
            "Required by --cluster-source capability; fail-closed if any bed task is "
            "absent. Labels come from the task text alone, before any attempt, so "
            "routing on them encodes no outcome."
        ),
    )
    parser.add_argument(
        "--cluster-min-size",
        type=int,
        default=DEFAULT_CLUSTER_MIN_SIZE,
        help=(
            "Capability clusters smaller than this fold into their most similar "
            f"large cluster (Jaccard over the type sets). Default {DEFAULT_CLUSTER_MIN_SIZE}: "
            "the seesaw tests a candidate only on tasks routed to its variant, and a "
            "two-task cluster gives that test no power. 1 disables merging. "
            "Ignored under --cluster-source gaia_level."
        ),
    )
    parser.add_argument(
        "--epsilon",
        type=float,
        default=0.0,
        help=(
            "Epsilon-greedy routing escape hatch (Router.explore, SPEC SS6.6). Default "
            "0.0 reproduces every run to date. Above 0, each task has this "
            "probability of going to a uniformly random variant instead of the "
            "cluster argmax. Purpose is measurement, not fairness: an unmeasured "
            "(variant, cluster) cell sits at the Laplace prior 0.5 and can never "
            "beat a measured one, so a variant that loses early is frozen out for "
            "good. Exploration costs accuracy in proportion to epsilon."
        ),
    )
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
        "--force-gate",
        choices=FORCE_GATE_MODES,
        default="off",
        help=(
            "TEMPORARY plumbing probe (default off). Overrides the deterministic "
            "gate's FINAL decision so the APPLY/FORK settlement chain (pool.fork / "
            "journal inheritance / next-round multi-variant routing / reporting) "
            "runs end-to-end on a real run. This TAINTS results: a forced run is "
            "NOT a measurement — the pool_report.md banner and experiment.lock.json "
            "say so. off keeps the gate byte-identical. Stage 1-4 integrity "
            "failures are never overridden. fork works best with --pool-k >= 2; at "
            "K=1 the engine legitimately downgrades an infeasible fork to REJECT "
            "(that downgrade path is itself worth exercising, so K=1 is allowed)."
        ),
    )
    parser.add_argument(
        "--l2-cert",
        choices=L2_CERT_MODES,
        default=DEFAULT_L2_CERT,
        help=(
            "L2 (ROUNDTRIP_L2) evidence mechanism (SPEC §7.11, 乙+甲). auto "
            "(default) = a meta-declared Level-2 entry always wins (甲); when a "
            "repo-mode tool-bucket candidate declares none, the recipe machine-"
            "certifies it (乙) by running the new tool's REAL output from the "
            "candidate's own eval trajectories through the provider's REAL "
            "serializer — an honest reject stands if the tool was never invoked or "
            "the output does not survive. Only fires for --manifest-mode repo paper "
            "candidates; the paper manifest arm is untouched. off = today's built-in "
            "declared-only stage 4, byte-identical. Recorded as deviation M-22."
        ),
    )
    parser.add_argument(
        "--ship-policy",
        choices=SHIP_POLICIES,
        default=DEFAULT_SHIP_POLICY,
        help=(
            "Round-settlement policy (M-17, SPEC §7.14). first_wins (default) = "
            "the Algorithm-1 reading (paper p.9): the first deterministic "
            "APPLY/FORK in a variant's ranked queue ships, the rest are skipped; "
            "byte-identical to the pre-M-17 engine. bucket_disjoint = the App B.1 "
            "reading (paper p.34): ship every ranked candidate in order, skipping "
            "any whose edit bucket (prompt/tools/config/processor) was already "
            "claimed this round, so bucket-disjoint candidates on different "
            "settlement targets (one in-place APPLY + FORK children) ship "
            "together. A second APPLY to an already-applied variant is skipped as "
            "an unreconstructable whole-config merge (our candidates are complete "
            "config.yaml files, not diffs) and that skip is recorded in the audit "
            "— the honest reconstruction of the paper's Alg.1-vs-App-B.1 double "
            "semantics. Non-default is noted in experiment.lock provenance."
        ),
    )
    parser.add_argument(
        "--aegis-digester",
        choices=AEGIS_DIGESTER_MODES,
        default=DEFAULT_AEGIS_DIGESTER,
        help=(
            "LLM-AEGIS Digester role (paper section 4.3; REPRO-COMPLETION-PLAN "
            "Phase A1). deterministic (default) = the byte-identical "
            "_EvidenceDigester fallback. llm = each FAILED task gets one meta-model "
            "interpretation call (structured failure_category/implicated_components/"
            "evidence_anchors) and round actionability a_t comes from a further "
            "call; PASSED tasks stay deterministic (no call), so a round costs "
            "F+1 meta calls for F failures. Prompts are OURS. Planner/Critic stay "
            "deterministic, so llm_aegis_reproduction stays False; only the audit's "
            "per-role Digester name flips to MetaModel_llm_digester."
        ),
    )
    parser.add_argument(
        "--aegis-planner",
        choices=AEGIS_PLANNER_MODES,
        default=DEFAULT_AEGIS_PLANNER,
        help=(
            "LLM-AEGIS Planner role (paper section 4.3; REPRO-COMPLETION-PLAN "
            "Phase A2). deterministic (default) = the byte-identical "
            "_DeterministicPlanner failure-cluster grouping. llm = one meta-model "
            "call builds the mutation landscape (who fails / what was tried / "
            "which edit classes are untried) from the round's digests + "
            "prior-ship history and emits up to K_t bucket-diversified candidate "
            "briefs; a model-declared empty landscape short-circuits the round. "
            "Prompts are OURS. Critic stays deterministic, so "
            "llm_aegis_reproduction stays False; only the audit's per-role "
            "Planner name flips to MetaModel_llm_planner."
        ),
    )
    parser.add_argument(
        "--aegis-critic",
        choices=AEGIS_CRITIC_MODES,
        default=DEFAULT_AEGIS_CRITIC,
        help=(
            "LLM-AEGIS Critic role (paper section 4.3; REPRO-COMPLETION-PLAN "
            "Phase A3). deterministic (default) = the byte-identical "
            "DeterministicCritic portfolio audit. llm = one meta-model call (plus "
            "one parse-retry) audits the Evolver's structured candidate batch and "
            "emits a ship_ranking, rejections, strategy concerns and AT MOST ONE "
            "revision request; the deterministic gate stays the sole shipping "
            "authority (the Critic only ranks/requests) and the deterministic "
            "regression veto still applies. Prompt is OURS. Only when Digester, "
            "Planner AND Critic are all llm does llm_aegis_reproduction flip to "
            "True; the audit's per-role Critic name flips to MetaModel_llm_critic."
        ),
    )
    parser.add_argument(
        "--aegis-prompts",
        choices=AEGIS_PROMPTS_MODES,
        default=DEFAULT_AEGIS_PROMPTS,
        help=(
            "Which prompt text drives the LLM meta-agent roles (P1-1; OPTIMIZATION-"
            "PLAN F4). paper (default, paper-first house rule) = the paper's own "
            "published Appendix B.1 prompts: the Planner in FULL, the Evolver "
            "(~60%% published, our bridges across its 3 truncations) merged into the "
            "candidate contract, and the Critic (~70%% published, bridges across its "
            "2 truncations); each is runtime-adapted ONLY where our JSON/manifest "
            "output contract requires (an appended OUTPUT FORMAT tail), the paper "
            "body kept verbatim. ours = today's byte-identical OURS reconstructions "
            "(the ablation arm). Only affects llm-role modes and the always-LLM "
            "Evolver; the provenance string records ',prompts=<mode>' whenever any "
            "role is llm, and the audit dict records it unconditionally."
        ),
    )
    parser.add_argument(
        "--retarget-after-freeze",
        action="store_true",
        help=(
            "Pick the evolve target from a preview of this round's routing freeze "
            "instead of from last round's partition. Default off, which keeps the "
            "lock and every routing decision byte-identical. Off, the target is "
            "chosen in the recipe before the engine freezes routing, so a variant "
            "can be selected on last round's tasks, lose its cluster to a stronger "
            "sibling at freeze time, and then be skipped as an empty cluster -- 6 of "
            "15 rounds produced no candidate that way on s1k8b103. On, freeze_routing "
            "runs once as a read-only preview and only variants that will actually "
            "carry tasks stay eligible. Requires a deterministic router (epsilon=0 and "
            "a non-random tie-break); otherwise the preview would draw from the RNG "
            "and change the real freeze, and startup fails loudly instead. The paper "
            "does not define target selection (M-16), so both orderings are OURS."
        ),
    )
    parser.add_argument(
        "--actionability-threshold",
        type=float,
        default=None,
        help=(
            "Algorithm 1's alpha for selective invocation. Default None = auto: "
            "1.0 with the deterministic Digester (its binary a_t + the paper's "
            "equality-continues boundary = legacy never-skip), 0.5 (library OURS "
            "default) with --aegis-digester llm, whose real-valued a_t made the "
            "legacy 1.0 skip whole rounds (runs/a1smoke: a_t=0.9). An explicit "
            "value always wins in either mode."
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
    parser.add_argument(
        "--search-backend",
        choices=SEARCH_BACKENDS,
        default=DEFAULT_SEARCH_BACKEND,
        help=(
            "WebSearch backend for the deployed H0 config and every candidate "
            "authored from it (W1). 'chain' (default) is byte-identical to the "
            "built-in SerpAPI->Tavily->Wikipedia+Bing->DuckDuckGo chain. 'serper' "
            "swaps in a Serper-first (serper.dev, SERPER_API_KEY) drop-in that "
            "falls back to that same chain on a missing key / empty result / error."
        ),
    )
    parser.add_argument(
        "--evolve-commit-bounce",
        choices=EVOLVE_COMMIT_BOUNCE_MODES,
        default=DEFAULT_EVOLVE_COMMIT_BOUNCE,
        help=(
            "F-A: what to do when a meta-agent slot ends its session with analysis "
            "but no config.yaml. 'off' (default) fails the attempt immediately "
            "(byte-identical). 'on' first gives the SAME slot one short, strictly "
            f"bounded (<= {_BOUNCE_MAX_STEPS} steps) 'commit a decision now' bounce "
            "(write config.yaml or an explicit cp no-op) before failing. At most "
            "one bounce per attempt; bounce_used / bounce_outcome are audited."
        ),
    )
    parser.add_argument(
        "--regression-accountability",
        choices=REGRESSION_ACCOUNTABILITY_MODES,
        default=DEFAULT_REGRESSION_ACCOUNTABILITY,
        help=(
            "F-B: which regressions may trigger the whole-round no-op veto. "
            "'strict' (default) is byte-identical: any active regression can veto "
            "the round. 'shipped_only' hard-gates only regressions an actually "
            "shipped APPLY/FORK config change caused; rejected-candidate gate "
            "regressions and zero-ship inter-round variance are demoted to "
            "non-blocking strategy_concerns."
        ),
    )
    parser.add_argument(
        "--regression-baseline",
        choices=REGRESSION_BASELINE_MODES,
        default=DEFAULT_REGRESSION_BASELINE,
        help=(
            "M-23: which solve history the gate's seesaw uses as the regression "
            "baseline. 'global' (default) is byte-identical: a candidate regresses a "
            "task if ANY variant ever solved it (cross-variant ever_solved). "
            "'per_variant' judges each candidate only against its own variant's solve "
            "history, so a task solved only by a different variant is not counted as a "
            "regression for this candidate."
        ),
    )
    # --- M1 task-decomposition x variant-division (evaluation-only) ---------
    # SPEC §7.17 / DESIGN §7-8. Every flag defaults to off / current behaviour;
    # nothing below runs unless --decomp-eval is passed, so the default arg
    # namespace and control flow stay byte-identical.
    parser.add_argument(
        "--decomp-eval",
        action="store_true",
        help=(
            "Evaluation-only M1 mode: load a frozen pool, run each task through "
            "the static D1-lite subtask pipeline pass-k times, score with the "
            "deterministic task-level gate, and write decomp_* artefacts. Does NOT "
            "enter the evolution loop (no gate/seesaw/critic). Off = default."
        ),
    )
    parser.add_argument(
        "--decomp-source",
        default="llm",
        help=(
            "Where decompositions come from. 'llm' (default) = one meta-model call "
            "per task (repair-retry once, else fall back to whole-task). "
            "'file:<path>' = oracle JSON keyed by task_id (E0 / cross-arm replay / tests)."
        ),
    )
    parser.add_argument(
        "--decomp-routing",
        choices=("single", "round_robin", "ledger"),
        default="single",
        help=(
            "Subtask->variant assignment. 'single' (default) = every subtask to the "
            "task-level routed variant. 'round_robin' = deterministic rotation over "
            "the pool. 'ledger' = observational (variant x subtask_type) Laplace "
            "win-rate; cold cells (< --decomp-ledger-min-obs) fall back to task-level."
        ),
    )
    parser.add_argument(
        "--decomp-pool-from",
        default=None,
        help=(
            "Run dir whose last settled pool_state is rebuilt as the frozen pool "
            "(B1/B2 arms). Default (unset) = pure-h0 single variant = B0 "
            "(fresh-spawn analogue)."
        ),
    )
    parser.add_argument(
        "--decomp-profile-from",
        default=None,
        help=(
            "Optional run dir; its frozen pool statistics become a compact "
            "per-variant profile injected into the decomposition prompt (B3, "
            "two-phase). Unset = static decomposition, prompt carries no profile "
            "segment."
        ),
    )
    parser.add_argument(
        "--decomp-max-subtasks",
        type=int,
        default=DEFAULT_MAX_SUBTASKS,
        help=(
            "Upper bound on subtasks per task (schema validation). A runaway guard, "
            "not a design cap -- the planner is told to emit as many subtasks as the "
            "task genuinely needs, and a capability may recur. Default "
            f"{DEFAULT_MAX_SUBTASKS}."
        ),
    )
    parser.add_argument(
        "--decomp-subtask-max-steps",
        type=int,
        default=None,
        help="Per-subtask step cap. Default (unset) inherits --max-steps.",
    )
    parser.add_argument(
        "--decomp-ledger-min-obs",
        type=int,
        default=3,
        help=(
            "Min (variant x subtask_type) observations before ledger routing trusts "
            "a cell; below it the subtask falls back to the task-level route. Default 3."
        ),
    )
    parser.add_argument(
        "--decomp-budget",
        choices=("per_subtask", "shared"),
        default="per_subtask",
        help=(
            "How the step budget is spread over a decomposed attempt (M-39). "
            "'per_subtask' (default, byte-identical) gives EACH subtask the full "
            "cap, so a decomposed attempt spends ~n_subtasks x cap against an "
            "undivided attempt's cap -- measured at 2.2x, which makes any "
            "positive result answerable with 'you spent twice the compute'. "
            "'shared' gives the CHAIN the cap and divides it per task: each "
            "subtask gets max(1, cap // n_subtasks), so A1 - B0 is an "
            "equal-budget contrast by construction. A fixed "
            "--decomp-subtask-max-steps cannot substitute: plans on this bench "
            "run 1..12 subtasks, so a flat cap of 4 still lets 25%% of tasks "
            "exceed the undivided budget."
        ),
    )
    parser.add_argument(
        "--decomp-credit",
        choices=("task", "subtask_convergence"),
        default="task",
        help=(
            "What a (variant x subtask_type) credit observation is scored on "
            "(M-36). 'task' (default, byte-identical) books the WHOLE task's "
            "pass/fail against every distinct pair on the chain, so a passing "
            "task credits searcher, calculator and verifier alike (~4.3 cells "
            "per chain) -- the ledger then measures participation in successful "
            "tasks rather than competence, which matters because ledger routing "
            "(the B2 arm) reads that table. 'subtask_convergence' books one "
            "observation per executed subtask, passing iff it finished inside "
            "its own step budget. It scores completion, NOT correctness: a "
            "subtask that stops early with a wrong answer counts as a success."
        ),
    )
    parser.add_argument(
        "--decomp-concurrency",
        type=int,
        default=1,
        help=(
            "Tasks evaluated in parallel under --decomp-eval. Default 1 is the "
            "pre-flag serial path, which uses roughly a tenth of the endpoint the "
            "evolution loop saturates at --concurrency 10. Attempts of one task "
            "stay serial regardless. REFUSED above 1 under --decomp-routing "
            "ledger: that mode reads the shared credit ledger other tasks are "
            "writing, so overlapping tasks would make routing depend on "
            "completion order. 'single' and 'round_robin' carry no shared "
            "routing state and are safe."
        ),
    )
    parser.add_argument(
        "--decomp-synth-guard",
        choices=("off", "strict"),
        default="off",
        help=(
            "Forbid the synthesiser from filling a missing subtask result out of "
            "parametric memory (M-33). 'off' (default) emits the stock synthesis "
            "prompt byte-for-byte. 'strict' appends a clause requiring the "
            "synthesiser to name any subtask that returned nothing and to state "
            "that the answer could not be obtained, instead of recalling one. "
            "Observed on b_smoke 20194330: a failed browse subtask was papered "
            "over with 'Based on the known content from the Game Grumps "
            "episode...'. Such a recall inflates the DECOMPOSED arm only, so it "
            "biases the headline contrast in this thesis's favour."
        ),
    )
    parser.add_argument(
        "--decomp-verify-gate",
        choices=("off", "on"),
        default="off",
        help=(
            "off (default) = no intermediate verification. on = after each non-verify "
            "subtask a light meta check runs; a failed check retries that subtask once."
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

    provider = _make_provider(
        args.model,
        args.provider_id,
        api_base=args.api_base,
        api_key=args.api_key,
        reasoning_effort=_task_reasoning_effort(args),
    )
    model_config = ModelConfig(main=provider)

    # The judge (answer grader) is deliberately left at its default effort: it is
    # the measurement instrument, not a Digester/Planner/Evolver/Critic, so it is
    # held constant across effort arms to avoid confounding the comparison.
    judge_provider = _make_provider(args.meta_model, args.provider_id)
    pipeline_eval = GAIAPipelineEvaluator(judge_provider=judge_provider)

    meta_provider = _make_provider(
        args.meta_model,
        args.provider_id,
        extended_thinking=True,
        thinking_budget_tokens=32_000,
        max_tokens=40_000,
        reasoning_effort=_meta_reasoning_effort(args),
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

    # --step-countdown (default off): additively append the countdown processor to
    # the deployed config so candidates authored from it inherit it. Off leaves H0
    # byte-identical; benchmarks/ is untouched.
    original_base = _maybe_add_step_countdown(original_base, getattr(args, "step_countdown", "off"))

    # --search-backend (default chain): swap WebSearch for the Serper-first drop-in
    # in the deployed config so candidates authored from it inherit it. 'chain'
    # leaves H0 byte-identical (same object, no contrib import); harnessx/ untouched.
    original_base = _maybe_use_serper_backend(
        original_base, getattr(args, "search_backend", DEFAULT_SEARCH_BACKEND)
    )

    # Freeze the baseline (H0) to V0/config.yaml — the root variant's config.
    v0_dir = run_dir / "V0"
    v0_dir.mkdir(parents=True, exist_ok=True)
    baseline_config_path = v0_dir / "config.yaml"
    original_base.to_yaml_file(baseline_config_path)

    meta_agent = VariantPoolMetaAgent(
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


def _load_decomp_pool(args: Any, deps: dict[str, Any], run_dir: Path):
    """Resolve the frozen pool for ``--decomp-eval`` (provider-free, testable).

    ``--decomp-pool-from <run_dir>`` rebuilds the last settled pool and replays
    its ledger (B1/B2). The default (unset) is a pure-h0 single variant = B0,
    the fresh-spawn analogue (DESIGN §7.1). Returns
    ``(pool, variant_ids, task_level_choice, pool_source)`` where
    ``task_level_choice(task_id) -> variant_id`` is the paper's deployment-time
    argmax route (trivially ``V0`` under a single variant).
    """
    task_ids = [t.task_id for t in deps["tasks"] if t.task_id]
    pool_from = getattr(args, "decomp_pool_from", None)
    if pool_from:
        # allow_finished: --decomp-pool-from consumes a *completed* evolution
        # run's frozen pool read-only; it never continues that run, so the
        # resume path's finished-run guard does not apply here.
        state = load_resume_state(
            Path(pool_from),
            candidate_mode=str(getattr(args, "candidate_mode", "paper")),
            allow_finished=True,
        )
        pool = rebuild_pool(state, K=max(int(state.next_id), len(state.variants), 1))
        ledger = replay_ledger(state, SuccessLedger())
        router = Router()
        before_round = int(state.next_round)
        variant_ids = sorted(pool.variants, key=lambda v: int(v[1:]) if v[1:].isdigit() else 0)
        pool_source = {"path": str(pool_from), "round": int(state.last_settled_round)}

        def task_level_choice(task_id: str) -> str:
            if len(variant_ids) == 1:
                return variant_ids[0]
            try:
                return router.route(task_id, pool, ledger, before_round=before_round)
            except Exception:  # noqa: BLE001 - unrouted task -> deployment cold-start
                return router.cold_start(task_id, pool, ledger, before_round=before_round)

        return pool, variant_ids, task_level_choice, pool_source

    # B0: pure-h0 single variant (fresh-spawn analogue).
    pool = VariantPool(K=1)
    pool.add_root(deps["baseline_config_path"], run_dir / "learnings.md", tasks=task_ids)
    variant_ids = ["V0"]
    pool_source = {"path": None, "round": None, "mode": "h0_single"}

    def task_level_choice(_task_id: str) -> str:
        return "V0"

    return pool, variant_ids, task_level_choice, pool_source


def _run_decomp_eval(args: Any, run_dir: Path, deps: dict[str, Any]) -> None:
    """Evaluation-only M1 decomposition mode (SPEC §7.17; DESIGN §7-8).

    Loads a frozen pool, runs each task through the static D1-lite subtask
    pipeline pass-k times, scores the synthesised answer with the deterministic
    task-level gate, and writes decomp_manifest.json / decomp_tasks.jsonl /
    decomp_summary.json / decomp_plans.json. Never enters the evolution loop;
    never touches gate/seesaw/critic. Wires the real seams to the pure classes
    in :mod:`experiments.variant_pool.subtask_pipeline`.
    """
    from harnessx.core.events import Message

    from experiments.variant_pool.reporting import pass_at_k
    from experiments.variant_pool.subtask_pipeline import (
        FileDecomposer,
        LlmDecomposer,
        PipelineExecutor,
        SessionResult,
        SubtaskRouter,
        Synthesizer,
        TypeCreditLedger,
        VerifyGate,
        build_pool_profile,
    )

    # --- validate decomp args (fail fast, provider-free) -------------------
    source = str(getattr(args, "decomp_source", "llm"))
    if source != "llm" and not source.startswith("file:"):
        raise SystemExit(f"--decomp-source must be 'llm' or 'file:<path>', got {source!r}")
    routing = str(getattr(args, "decomp_routing", "single"))
    subtask_max_steps = getattr(args, "decomp_subtask_max_steps", None)
    if subtask_max_steps is None:
        subtask_max_steps = int(args.max_steps)
    max_subtasks = int(getattr(args, "decomp_max_subtasks", 5))
    min_obs = int(getattr(args, "decomp_ledger_min_obs", 3))

    credit_mode = str(getattr(args, "decomp_credit", "task"))
    budget_mode = str(getattr(args, "decomp_budget", "per_subtask"))

    decomp_concurrency = int(getattr(args, "decomp_concurrency", 1))
    if decomp_concurrency < 1:
        raise SystemExit(f"--decomp-concurrency must be >= 1, got {decomp_concurrency}")
    if decomp_concurrency > 1 and routing == "ledger":
        # SubtaskRouter.route reads TypeCreditLedger.rate() while other tasks
        # write it, so with overlapping tasks the route depends on which
        # rollouts happened to finish first -- the arm would not be reproducible
        # even from its own frozen inputs. 'single' and 'round_robin' are pure
        # functions of (task_id, attempt, subtask_index) and stay safe.
        raise SystemExit(
            "--decomp-concurrency > 1 is refused with --decomp-routing ledger: "
            "ledger routing reads the credit ledger that concurrent tasks are "
            "writing, so routing would depend on completion order. Use "
            "--decomp-concurrency 1 for the ledger arm, or route with "
            "single/round_robin."
        )

    pipeline_eval = deps["pipeline_eval"]
    model_config = deps["model_config"]

    pool, variant_ids, task_level_choice, pool_source = _load_decomp_pool(args, deps, run_dir)
    variant_config = {vid: Path(v.config_path) for vid, v in pool.variants.items()}

    # --- B3 pool profile (optional; static == None) ------------------------
    profile_text: str | None = None
    profile_source: str | None = None
    prof_from = getattr(args, "decomp_profile_from", None)
    if prof_from:
        profile_text = build_pool_profile(prof_from)
        profile_source = str(prof_from)

    # --- meta completion seam (decompose / synthesis / verify) -------------
    meta_provider = _make_provider(
        args.meta_model,
        args.provider_id,
        reasoning_effort=_meta_reasoning_effort(args),
    )

    async def _complete(prompt: str) -> str:
        response = await meta_provider.complete([Message(role="user", content=prompt)], [])
        return str(getattr(response, "content", "") or "")

    # --- deterministic task-level scorer (exact-match gate) ----------------
    async def _score(final_output: str, ground_truth: str) -> bool:
        result = await pipeline_eval.evaluate_answer(final_output or "", ground_truth or "")
        return bool(result.passed)

    # --- shared components --------------------------------------------------
    credit_ledger = TypeCreditLedger()
    subtask_router = SubtaskRouter(routing, variant_ids=variant_ids, ledger=credit_ledger, min_obs=min_obs)
    synthesizer = Synthesizer(
        _complete, guard=str(getattr(args, "decomp_synth_guard", "off"))
    )
    verify_gate = (
        VerifyGate(_complete) if str(getattr(args, "decomp_verify_gate", "off")) == "on" else None
    )
    if source.startswith("file:"):
        decomposer = FileDecomposer.from_path(source[len("file:") :], max_subtasks=max_subtasks)
    else:
        decomposer = LlmDecomposer(_complete, max_subtasks=max_subtasks, profile_text=profile_text)

    sessions_root = run_dir / "decomp_sessions"
    sessions_root.mkdir(parents=True, exist_ok=True)

    def _make_runner(parent_task: GAIATask):
        async def _runner(*, instruction, variant_id, max_steps, subtask_id, subtask_type) -> "SessionResult":
            safe_tid = str(parent_task.task_id).replace("/", "_").replace("\\", "_").replace(":", "_")
            sess_dir = sessions_root / safe_tid / f"{subtask_id}-{variant_id}"
            round_config = _prepare_round_config(variant_config[variant_id], _make_journal(sess_dir))
            # Non-empty sentinel ground truth: keeps the reused rollout path from
            # firing the empty-GT LLM judge on a subtask (its pass/score is unused;
            # only the deterministic task-level gate over the synthesis is scored).
            sub_task = GAIATask(
                task_id=f"{parent_task.task_id}::{subtask_id}",
                description=instruction,  # model-facing content = the subtask instruction
                question=instruction,
                level=parent_task.level,
                final_answer="[decomp-subtask: not scored]",
                max_steps=int(max_steps),
            )
            record = await _rollout_once(
                sub_task,
                0,
                label=f"decomp-{variant_id}",
                model_config=model_config,
                round_config=round_config,
                pipeline_eval=pipeline_eval,
                max_cost=float(args.max_cost),
            )
            return SessionResult(
                output=str(record.get("final_output") or ""),
                steps=int(record.get("steps") or 0),
                cost_usd=float(record.get("cost_usd") or 0.0),
            )

        return _runner

    pass_k = int(args.pass_k)
    jsonl_path = run_dir / "decomp_tasks.jsonl"
    rows: list[dict] = []
    plans_out: dict[str, list] = {}
    totals = {"cost": 0.0, "attempts": 0, "fallbacks": 0}

    async def _amain() -> None:
        semaphore = asyncio.Semaphore(decomp_concurrency)

        async def _run_task(task) -> tuple[Any, list]:
            """All ``pass_k`` attempts for one task; attempts stay serial.

            Attempts of the same task are sequential even under concurrency:
            they are repeated measurements of one task and the pipeline writes
            credit between them, so overlapping them would change what the
            second attempt sees.
            """
            async with semaphore:
                choice = task_level_choice(task.task_id)
                executor = PipelineExecutor(
                    runner=_make_runner(task),
                    scorer=_score,
                    decomposer=decomposer,
                    router=subtask_router,
                    synthesizer=synthesizer,
                    credit_ledger=credit_ledger,
                    credit_mode=credit_mode,
                    budget_mode=budget_mode,
                    verify_gate=verify_gate,
                    subtask_max_steps=subtask_max_steps,
                )
                results = []
                for attempt in range(pass_k):
                    results.append(
                        await executor.run_attempt(
                            task_id=task.task_id,
                            task_text=task.question,
                            ground_truth=task.final_answer or "",
                            attempt=attempt,
                            task_level_choice=choice,
                            profile=profile_text,
                        )
                    )
                return task, results

        def _record(task, results: list, handle) -> None:
            """Fold one task's attempts into the shared accumulators.

            Kept out of the coroutine so every mutation of ``totals`` / ``rows``
            / ``plans_out`` and every JSONL line happens in sorted task order,
            whatever order the rollouts finished in. Otherwise a rerun at a
            different concurrency would produce a differently-ordered artefact
            for identical measurements.
            """
            n_pass = 0
            for result in results:
                totals["attempts"] += 1
                totals["cost"] += result.cost_usd
                if result.passed:
                    n_pass += 1
                if result.fallback:
                    totals["fallbacks"] += 1
                elif task.task_id not in plans_out and result.plan is not None:
                    plans_out[task.task_id] = result.plan.to_records()
                handle.write(json.dumps(result.to_json(), ensure_ascii=False) + "\n")
            rows.append(
                {"task_id": task.task_id, "round_idx": 0, "n_att": pass_k, "n_pass": n_pass}
            )

        ordered = sorted(deps["tasks"], key=lambda t: t.task_id)
        with jsonl_path.open("w", encoding="utf-8") as handle:
            if decomp_concurrency <= 1:
                # Byte-identical to the pre-flag path: run and record one task at
                # a time so the JSONL streams exactly as it did before.
                for task in ordered:
                    _record(*(await _run_task(task)), handle)
            else:
                # gather() yields in argument order, not completion order, so the
                # recording pass below is already sorted by task_id.
                for task, results in await asyncio.gather(*(_run_task(t) for t in ordered)):
                    _record(task, results, handle)

    asyncio.run(_amain())

    # pass@2 via the reporting estimator (A.3 formula 6), clamped like run.py.
    if rows:
        acc = 0.0
        for row in rows:
            k_eff = min(pass_k, row["n_att"])
            acc += pass_at_k(row["n_att"], row["n_pass"], k_eff) if row["n_att"] else 0.0
        pass_at_score = acc / len(rows)
    else:
        pass_at_score = 0.0

    manifest = {
        "mode": "decomp_eval",
        "decomp_source": source,
        "decomp_routing": routing,
        "decomp_max_subtasks": max_subtasks,
        "decomp_subtask_max_steps": subtask_max_steps,
        "decomp_ledger_min_obs": min_obs,
        "decomp_verify_gate": str(getattr(args, "decomp_verify_gate", "off")),
        # M-33. --decomp-eval returns before the experiment lock is built, so the
        # manifest is this mode's only provenance surface: an unrecorded guard
        # would make a guarded and an unguarded arm indistinguishable after the
        # fact, and the guard changes what counts as a pass.
        "decomp_synth_guard": str(getattr(args, "decomp_synth_guard", "off")),
        "decomp_concurrency": decomp_concurrency,
        "decomp_credit": credit_mode,
        "decomp_budget": budget_mode,
        "pass_k": pass_k,
        "num_tasks": len(rows),
        "max_steps": int(args.max_steps),
        "max_cost": float(args.max_cost),
        "model": args.model,
        "meta_model": args.meta_model,
        "pool_source": pool_source,
        "profile_source": profile_source,
    }
    summary = {
        "pass_at_k": pass_at_score,
        "k": pass_k,
        "num_tasks": len(rows),
        "total_attempts": totals["attempts"],
        "fallback_rate": (totals["fallbacks"] / totals["attempts"]) if totals["attempts"] else 0.0,
        "total_cost_usd": round(totals["cost"], 6),
        "credit_matrix": credit_ledger.matrix(),
        "rows": rows,
    }
    (run_dir / "decomp_manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    (run_dir / "decomp_summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    (run_dir / "decomp_plans.json").write_text(
        json.dumps(plans_out, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    logger.info(
        "[decomp-eval] pass@%d=%.3f over %d task(s); fallback_rate=%.3f; cost=$%.3f",
        pass_k,
        pass_at_score,
        len(rows),
        summary["fallback_rate"],
        totals["cost"],
    )


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

    if getattr(args, "decomp_eval", False) and args.resume:
        raise SystemExit(
            "--decomp-eval is an evaluation-only mode and cannot be combined with --resume"
        )

    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    # --resume (default off): validate + resolve the target dir and derive
    # --run-tag from it. Returns None when not resuming, so the path below is
    # byte-identical to the pre-resume flow.
    resume_dir = plan_resume(args, RUNS_DIR)
    run_tag = args.run_tag or time.strftime("pool_%Y%m%d-%H%M%S")
    run_dir = resume_dir if resume_dir is not None else RUNS_DIR / run_tag
    if args.clean and run_dir.exists():
        shutil.rmtree(run_dir)
        logger.info("Cleaned %s", run_dir)
    elif resume_dir is None and run_dir.exists() and any(run_dir.iterdir()):
        logger.warning("--run-tag %r already exists and is non-empty; output will interleave. Use --clean.", run_tag)
    run_dir.mkdir(parents=True, exist_ok=True)
    logger.info("Run outputs -> %s (pool_k=%d)", run_dir, args.pool_k)

    # setup() re-freezes V0/config.yaml unconditionally; on resume, keep the
    # frozen baseline so a lock rejection cannot leave a different-H0 config behind.
    _v0_backup: bytes | None = None
    if resume_dir is not None:
        _v0_cfg = run_dir / "V0" / "config.yaml"
        if _v0_cfg.is_file():
            _v0_backup = _v0_cfg.read_bytes()

    deps = setup(args, run_dir)

    # --decomp-eval (default off): evaluation-only M1 mode. Runs the frozen-pool
    # subtask pipeline and returns before the evolution loop / lock / recipe are
    # built. When off, this branch is skipped and the flow below is byte-identical.
    if getattr(args, "decomp_eval", False):
        _run_decomp_eval(args, run_dir, deps)
        return

    lock = _build_experiment_lock(
        args=args,
        run_tag=run_tag,
        baseline_config_path=deps["baseline_config_path"],
        original_base=deps["original_base"],
    )

    resume_state = None
    if resume_dir is not None:
        resume_state = load_resume_state(
            run_dir, candidate_mode=str(getattr(args, "candidate_mode", "paper"))
        )
        existing_lock = ExperimentLock.load(run_dir)
        blocking = lock_blocking_diffs(lock, existing_lock)
        if blocking:
            if _v0_backup is not None:
                (run_dir / "V0" / "config.yaml").write_bytes(_v0_backup)
            raise SystemExit(
                "--resume refused: experiment.lock.json is inconsistent with this process "
                "(comparability first; there is no --force bypass). Differences "
                "(this run -> on-disk lock):\n  - " + "\n  - ".join(blocking)
            )
        annotate_resume_provenance(run_dir, existing_lock, resumed_at_round=resume_state.next_round)
        lock = existing_lock  # the frozen family lock stays authoritative
        for _note in resume_state.warnings:
            logger.warning("[resume] %s", _note)
        logger.info(
            "[resume] continuing %s from R%d (%d settled round(s), idle=%d, %d variant(s))",
            run_dir,
            resume_state.next_round,
            len(resume_state.settled_rounds),
            resume_state.idle,
            len(resume_state.variants),
        )
    else:
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
        if resume_state is not None:
            apply_resume_state(recipe, resume_state)
            recipe.run(start_round=resume_state.next_round)
        else:
            recipe.run()
    finally:
        recipe.close()


if __name__ == "__main__":
    import warnings

    warnings.filterwarnings("ignore", message=".*Event loop is closed.*")
    main()
