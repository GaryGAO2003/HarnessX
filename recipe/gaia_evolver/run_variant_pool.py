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
from experiments.variant_pool.engine import DEFAULT_MIN_FORK, DEFAULT_PATIENCE, RoundResult, VariantPoolEngine
from experiments.variant_pool.candidate_pipeline import (
    CandidateBrief,
    CandidatePipeline,
    CandidateSlot,
    DigesterRoundArtifact,
    DigesterStage,
    IsolatedEvolverAdapter,
    OURS_ACTIONABILITY_THRESHOLD_PROVENANCE,
    outward_candidate_id,
    PipelineContext,
    PipelineResult,
    PlannerStage,
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
from experiments.variant_pool.gate import Decision, GateResult, TaskEval, _declared_level2, run_gate
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
    "For tools/processor buckets, at least ONE capability_evidence claim MUST "
    "contain the phrase 'Level 2' asserting the tool return survives provider "
    "serialization (the example's second entry is the reference shape) — the "
    "deterministic gate rejects code candidates without it at ROUNDTRIP_L2."
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
    "Never fabricate: only claim what you observed in this session."
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
# than "not declared"). Meta-declared evidence, the non-code exemption, and the
# processor bucket (declared-only in v1) all keep the built-in ``_declared_level2``
# behaviour. Injected only for ``--manifest-mode repo`` paper-candidate runs; the
# paper manifest arm is untouched (faithful arm), and legacy opaque candidates
# have no manifest-backed stage 4 to certify. Recorded as deviation M-22. The gate
# callable is injected through the engine's existing seam
# (``VariantPoolEngine(..., gate=...)`` -> ``run_gate(..., check_roundtrip=...)``,
# gate.py:384-410); nothing under experiments/ or harnessx/ is modified.


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
) -> tuple[bool, str]:
    """Write the machine-certification audit into the recipe's candidate meta.

    Records under ``_candidate_meta[candidate_id]["l2_certification"]`` and returns
    the ``(passed, reason)`` pair stage 4 expects. A PASS reason is tagged as ours;
    a FAIL reason is the bare note so the archived ``ROUNDTRIP_L2: <note>`` reads as
    the specific catch (empty return / dropped content / never invoked / no target).
    """
    record = {
        "outcome": outcome,
        "tool": tool,
        "output_chars": output_chars,
        "note": note,
        "provenance": "OURS_machine_certified",
    }
    try:
        recipe._candidate_meta.setdefault(candidate_id, {})["l2_certification"] = record
    except Exception:  # noqa: BLE001 - auditing must never break the gate decision
        pass
    if passed:
        return True, f"OURS machine-certified ({outcome}): {note}"
    return False, note


def _make_l2_certifier(
    recipe: Any,
    candidate: Any,
    parent_config: Any,
    serializer_factory: Callable[[], Callable[[str], Any]],
) -> Callable[[Any], tuple[bool, str]]:
    """Build the stage-4 ``check_roundtrip`` for one candidate (SPEC §7.11).

    Receives the manifest the gate unwraps (gate.py:409). Cases 甲/exempt/processor
    defer to the built-in :func:`_declared_level2` (meta wins / no code / declared-
    only); a tool-bucket manifest with no declared Level-2 entry is machine-
    certified from the candidate's real eval-trajectory tool output.
    """

    def _check(manifest: Any) -> tuple[bool, str]:
        # 甲 (declared) / non-code exempt / processor-only (declared-only, v1):
        # all keep the built-in stage-4 behaviour exactly.
        if (
            not isinstance(manifest, ChangeManifest)
            or manifest.level2_evidence() is not None
            or not manifest.needs_code_verification()
            or "tools" not in set(manifest.bucket)
        ):
            return _declared_level2(manifest)

        # 乙: tools bucket, no declared evidence -> certify from the real run.
        candidate_id = manifest.candidate_id or str(getattr(candidate, "candidate_id", "") or "")
        target_variant = str(getattr(candidate, "target_variant", "") or manifest.target_variant or "")
        candidate_config = getattr(candidate, "config_path", None)

        targets = _l2_target_tool_names(manifest, parent_config, candidate_config)
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

        Reads ``digest.prior_history`` — the cross-round continuity the
        EvidenceStore attaches at write time (``ships`` = what was already tried).
        This is the "prior-ship history the recipe can already reach"; no store is
        queried directly.
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

    # -- prompt assembly ------------------------------------------------------

    def _build_prompt(
        self,
        summary: str,
        *,
        truncation: tuple[str, ...],
        retry_error: str | None,
    ) -> str:
        parts = [
            _LLM_PLANNER_PROMPT,
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
                        eligible=self._variants_with_settled_trajectories(),
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
        )

    @property
    def _planner_adapter_name(self) -> str:
        """Truthful pipeline-audit name for the active Planner role."""
        return (
            "MetaModel_llm_planner"
            if self.aegis_planner == "llm"
            else "deterministic_failure_cluster_fallback"
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
                planner_brief=_planner_brief_with_regressions(
                    asdict(brief), context.regressions
                ),
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
                "digester": self._digester_adapter_name,
                "planner": self._planner_adapter_name,
                "evolver": "MetaAgent_isolated_slots",
                "critic": "deterministic_portfolio_fallback",
                # Stays False until ALL THREE roles (Digester/Planner/Critic) are
                # LLM; A1/A2 model-back the Digester and Planner (per-role names
                # above), but the Critic is still deterministic.
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
                    (
                        "LLM Digester (A1) + deterministic Planner/Critic fallbacks "
                        "+ MetaAgent Evolver"
                        if self.aegis_digester == "llm"
                        else "deterministic Digester/Planner/Critic fallbacks + MetaAgent Evolver"
                    )
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
                (
                    "llm_digester+deterministic_planner_critic+llm_metaagent_evolver"
                    if str(getattr(args, "aegis_digester", DEFAULT_AEGIS_DIGESTER)) == "llm"
                    else "deterministic_evidence_digester_planner_critic+llm_metaagent_evolver"
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
