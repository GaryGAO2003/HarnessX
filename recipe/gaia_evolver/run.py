# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
from __future__ import annotations

import argparse
import asyncio
import gc
import json
import logging
import os
import shutil
import sys
import time
from pathlib import Path

# ── project root on sys.path ────────────────────────────────────────────────
_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

# ── load .env ────────────────────────────────────────────────────────────────
_env_path = Path(_PROJECT_ROOT) / ".env"
if _env_path.exists():
    for line in _env_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

from typing import Any

from harnessx.core.harness import HarnessResult
from harnessx.core.model_config import ModelConfig
from harnessx.meta_harness import MetaAgent

from benchmarks.gaia.evaluator import GAIAPipelineEvaluator
from benchmarks.gaia.harness import make_gaia_builder_gpt5
from benchmarks.gaia.task import GAIATask, load_gaia_tasks, load_gaia_tasks_from_json

# The unbiased pass@k estimator (paper A.3 p.29, formula 6) already lives in
# the variant-pool reporting module; W17 reuses it rather than growing a second
# copy that could drift from it.
from experiments.variant_pool.reporting import pass_at_k

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
    PASS_COUNT_NOISE_THRESHOLD,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-5s %(name)s: %(message)s",
)
logger = logging.getLogger("gaia_evolver")

# Suppress LiteLLM's own debug/verbose output — it defaults to noisy
# debug logging that drowns out the evolver's progress lines.
logging.getLogger("LiteLLM").setLevel(logging.WARNING)
logging.getLogger("litellm").setLevel(logging.WARNING)
try:
    import litellm as _litellm

    _litellm.suppress_debug_info = True
    _litellm.set_verbose = False
except ImportError:
    pass

# All recipe outputs live under runs/ — one subdir per --run-tag.
# Each run_tag dir is self-contained: configs, trajectories, sessions, evolve
# artifacts all nest under R{N}/ subfolders for easy inspection/cleanup.
_RECIPE_DIR = Path(__file__).resolve().parent
RUNS_DIR = _RECIPE_DIR / "runs"
# Benchmark-specific meta-agent skills. Mounted into the meta-agent's system
# prompt via ``extra_skills_dirs`` so the generic persona (under
# ``harnessx/meta_harness/workspace/``) stays benchmark-agnostic. If this
# directory does not exist the meta-agent simply sees no GAIA playbook,
# which is the desired fallback.
_GAIA_SKILLS_DIR = _RECIPE_DIR / "skills"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_provider(
    model: str,
    provider_id: str,
    *,
    extended_thinking: bool = False,
    thinking_budget_tokens: int = 10_000,
    max_tokens: int = 8192,
    api_base: str | None = None,
    api_key: str | None = None,
    reasoning_effort: str | None = None,
):
    """Create a model provider from CLI args.

    ``extended_thinking`` / ``thinking_budget_tokens`` / ``max_tokens`` only
    take effect when the ``AnthropicProvider`` branch is selected — LiteLLM
    routes do not expose Anthropic's thinking API directly. Callers that
    need thinking on a non-Anthropic deployment should instead set the
    equivalent kwarg through the LiteLLM path (no-op here).

    ``api_base`` routes the LiteLLM branch to a custom OpenAI-compatible
    endpoint (local vLLM / SGLang / etc.). The ``X-Model-Provider-Id`` header is dropped in that case — it's
    vendor-specific and will be rejected or ignored by other backends.

    ``reasoning_effort`` (``none``/``minimal``/``low``/``medium``/``high``/``xhigh``/``max``) is forwarded to the
    LiteLLM/vLLM endpoint **only when set**, so an unset caller's request body is
    byte-identical to the pre-flag path (no ``reasoning_effort`` key). It is a
    LiteLLM/vLLM knob, not an Anthropic one — the ``anthropic/`` branch uses
    ``extended_thinking`` instead and ignores it.
    """
    from harnessx.providers.anthropic_provider import AnthropicProvider
    from harnessx.providers.litellm_provider import LiteLLMProvider

    if model.startswith("anthropic/"):
        model_name = model[len("anthropic/") :]
        return AnthropicProvider(
            model=model_name,
            base_url=os.environ.get("ANTHROPIC_API_BASE"),
            api_key=os.environ.get("ANTHROPIC_API_KEY"),
            extended_thinking=extended_thinking,
            thinking_budget_tokens=thinking_budget_tokens,
            max_tokens=max_tokens,
        )
    # Forward reasoning_effort only when set: an unset run keeps a byte-identical
    # request body (no ``reasoning_effort`` key). A literal "none" is a real
    # endpoint value (disable thinking) and is truthy, so it is still sent.
    effort_kwargs = {"reasoning_effort": reasoning_effort} if reasoning_effort else {}
    if api_base:
        return LiteLLMProvider(
            model,
            api_base=api_base,
            api_key=api_key or "EMPTY",
            **effort_kwargs,
        )
    extra_headers = {"X-Model-Provider-Id": provider_id}
    return LiteLLMProvider(model, extra_headers=extra_headers, **effort_kwargs)


#: Characters Windows forbids in a path component. ':' is the one that bites:
#: decomposition subtask ids are ``<parent_task_id>::<subtask_id>``.
_FS_UNSAFE = str.maketrans({c: "_" for c in '<>:"/\\|?*'})


def _fs_safe(name: str) -> str:
    """Make ``name`` usable as a single path component.

    A no-op for the ids this recipe has always produced (UUIDs, ``V0``,
    ``R3-V1-active``), so existing session directory names are unchanged.
    """
    return name.translate(_FS_UNSAFE)


async def _run_task(
    harness: Any,
    task: GAIATask,
    label: str,
    *,
    pipeline_eval: "GAIAPipelineEvaluator",
    harness_config: Any | None = None,
    attempt_idx: int = 0,
) -> dict:
    """Run a single task + externally evaluate the answer.

    The harness no longer contains an EvaluationProcessor. We call
    ``pipeline_eval.evaluate_answer`` after the run completes. The
    result flows into two places:

    * ``comparison.json`` — the external run-level report (includes
      the dataset's expected answer for offline analysis; not read
      by the meta-agent).
    * The per-task trajectory ``.md`` **frontmatter** — as
      ``eval_passed`` / ``eval_score`` only, so the meta-agent can
      see correctness outcomes (pass/fail) without seeing the
      expected answer text or any reason string that could
      smuggle it.

    The trajectory **body** (conversation transcript) and its
    **frontmatter** are both ground-truth-free: neither the task-agent
    nor the meta-agent sees the expected answer. The meta-agent only
    sees pass/fail signals, so it evolves on correctness, not on a
    known target string.

    ``harness_config`` is the HarnessConfig (typed Any to avoid a core
    import) threaded through so the caller can dump processor / tool
    info into the trajectory markdown and read `tool_registry` for the
    `unused_tools` signal.

    ``attempt_idx`` is the 0-based rollout index under pass@k (§6.1 p.15).
    It only disambiguates the session id and the log line: attempt 0 keeps
    the historical `{label}-{task_id}` session id verbatim, so a
    ``--pass-k 1`` run is indistinguishable from a pre-pass@k run.
    """
    t0 = time.time()
    task_id = task.task_id or "?"
    # The session id becomes a directory name. GAIA task ids are UUIDs and are
    # already path-safe, but a decomposition subtask carries the synthetic id
    # ``<parent>::<subtask>`` and ':' is illegal in a Windows path -- it made
    # every subtask rollout die with WinError 123 before reaching the model.
    # Sanitising here (rather than at the id's source) keeps the logical id
    # intact for logging and accounting, and leaves UUID-only ids untouched, so
    # the historical session id is byte-identical for every pre-existing path.
    session_id = _fs_safe(f"{label}-{task_id}")
    if attempt_idx != 0:
        session_id = f"{session_id}-a{attempt_idx + 1}"
    attempt_tag = "" if attempt_idx == 0 else f" [a{attempt_idx + 1}]"
    logger.info("[%s] Running %s (Level %d)%s...", label, task_id, task.level, attempt_tag)

    try:
        result = await harness.run(task, session_id=session_id)
        elapsed = time.time() - t0

        # External evaluation — does NOT feed back into trajectory/stats.
        eval_result = await pipeline_eval.evaluate_answer(
            result.final_output or "",
            task.final_answer or "",
        )
        passed = bool(eval_result.passed)
        score = float(eval_result.score)
        reason = (eval_result.reason or "")[:200]

        # Behavioural signals + external eval outcome. Eval fields
        # (passed/score/reason/expected) feed both comparison.json and
        # the trajectory frontmatter's eval_* block; the trajectory
        # body remains ground-truth-free.
        output = result.final_output or ""
        state_snapshot = getattr(getattr(result, "task_end", None), "state_snapshot", None) or {}
        slots = state_snapshot.get("slots") if isinstance(state_snapshot, dict) else {}
        if not isinstance(slots, dict):
            slots = {}
        model_empty_end_turn = bool((slots.get("__model_empty_end_turn_seen") or {}).get("content"))
        empty_end_turn_recovered = bool((slots.get("__empty_end_turn_recovered") or {}).get("content"))
        record = {
            "task_id": task_id,
            "attempt": attempt_idx,
            "level": task.level,
            "question": task.question[:150],
            "expected": task.final_answer,
            "output": output[:300],
            "final_output": output,
            "passed": passed,
            "score": score,
            "reason": reason,
            "steps": result.total_steps,
            "total_tokens": result.total_tokens,
            "cost_usd": result.total_cost_usd,
            "elapsed_s": round(elapsed, 1),
            "exit_reason": getattr(result, "exit_reason", "?"),
            "model_empty_end_turn": model_empty_end_turn,
            "empty_end_turn_recovered": empty_end_turn_recovered,
        }

        status = "PASS" if passed else "FAIL"
        logger.info(
            "[%s] %s %s — steps=%d cost=$%.3f time=%.1fs",
            label,
            task_id,
            status,
            result.total_steps,
            result.total_cost_usd,
            elapsed,
        )
        if model_empty_end_turn:
            logger.warning(
                "[%s] %s model_empty_end_turn=true recovered=%s",
                label,
                task_id,
                empty_end_turn_recovered,
            )
        return {**record, "_result": result}

    except Exception as exc:
        elapsed = time.time() - t0
        logger.error("[%s] %s ERROR: %s (%.1fs)", label, task_id, exc, elapsed)
        return {
            "task_id": task_id,
            "attempt": attempt_idx,
            "level": task.level,
            "question": task.question[:150],
            "expected": task.final_answer,
            "passed": False,
            "score": 0.0,
            "reason": f"error: {exc}",
            "elapsed_s": round(elapsed, 1),
            "exit_reason": "error",
            "tool_call_counts": {},
            "tool_error_counts": {},
            "_result": None,
        }


# ---------------------------------------------------------------------------
# pass@k — k independent rollouts per task per round (W17)
# ---------------------------------------------------------------------------
#
# §6.1 p.15, verbatim: "each task receives two independent attempts per round
# (pass@2: solved if either succeeds), reducing sampling noise while preserving
# a binary per-task signal for the seesaw constraint". A.3 p.29 gives the
# unbiased estimator (formula 6) and the rule that infrastructure failures
# "count as failures" and are not dropped.
#
# The two-sidedness is deliberate and must not be "fixed" here: §7.1 p.21 notes
# that under pass@2 "a task whose success probability has degraded can still
# register as 'solved,' so sub-threshold regressions evade the seesaw
# constraint". That masking is the mechanism behind the paper's Global-arm
# collapse; ``n_pass`` / ``n_att`` are recorded per task precisely so pass@1 can
# be reported alongside pass@2 (SPEC §6.7) and the drift stays visible.

#: Per-attempt fields carried into the merged record's ``attempts`` list.
#: Deliberately a whitelist: the merged record already holds the primary
#: attempt's question/expected/output, and duplicating them k times would
#: bloat comparison.json for no audit value.
_ATTEMPT_SUMMARY_KEYS = (
    "exit_reason",
    "passed",
    "score",
    "steps",
    "total_tokens",
    "cost_usd",
    "elapsed_s",
    "trajectory_file",
    "reason",
)

#: Fields that are *resource* accounting and therefore sum over attempts: a
#: pass@2 round really did spend both attempts' tokens, and ``round_cost``
#: sums these records. Everything else is taken from the primary attempt.
_SUMMED_ATTEMPT_KEYS = ("steps", "total_tokens", "cost_usd", "elapsed_s")


def _is_infra_failure(record: dict) -> bool:
    """Did this attempt die on infrastructure rather than on the task?

    ``exit_reason == "error"`` is how this runner marks a rollout that raised
    out of ``harness.run`` — a provider timeout, a sandbox that would not
    start, an exception in the pipeline. A.3 p.29 is explicit that such
    attempts *count as failures* and are not resampled, so the flag never
    removes an attempt from the denominator; it exists so the rate can be
    audited after the fact.
    """
    return (record.get("exit_reason") or "") == "error"


def _merge_attempt_records(attempts: list[dict]) -> dict:
    """Fold k independent rollouts of one task into one comparison.json record.

    Backward compatibility is the constraint: ``passed`` stays a bool and every
    field an existing reader looks at keeps its meaning, so ``comparison.json``
    is still readable by code written before pass@k existed. What is *added* is
    ``n_pass`` / ``n_att`` / ``attempts`` (plus ``infra_failures`` and
    ``primary_attempt``), which is what lets the same file yield pass@1 as well
    as pass@k.

    Two choices worth naming:

    * ``passed = n_pass > 0`` — "solved if either succeeds" (§6.1 p.15).
    * The *primary* attempt (whose answer/verdict/trajectory the flat fields
      describe) is the first passing one, falling back to attempt 0. Picking
      attempt 0 unconditionally would leave ``passed: true`` sitting next to a
      failing ``output``/``score``, which reads as a bug in every downstream
      view.

    Resource fields sum across attempts; that is what makes the round-level
    cost and token totals true of the run that was actually paid for.
    """
    if not attempts:
        raise ValueError("_merge_attempt_records needs at least one attempt record")

    n_att = len(attempts)
    n_pass = sum(1 for a in attempts if a.get("passed"))
    primary_idx = next((i for i, a in enumerate(attempts) if a.get("passed")), 0)
    merged = dict(attempts[primary_idx])

    for key in _SUMMED_ATTEMPT_KEYS:
        # Absent in every attempt (the error branch carries no steps/tokens) →
        # leave it absent, so a k=1 record is byte-identical to the old shape.
        if not any(key in a for a in attempts):
            continue
        total = sum(float(a.get(key) or 0) for a in attempts)
        if key in ("steps", "total_tokens"):
            merged[key] = int(total)
        elif key == "elapsed_s":
            merged[key] = round(total, 1)
        else:
            merged[key] = total

    merged["passed"] = n_pass > 0
    merged["n_pass"] = n_pass
    merged["n_att"] = n_att
    merged["infra_failures"] = sum(1 for a in attempts if _is_infra_failure(a))
    merged["primary_attempt"] = primary_idx
    merged["attempts"] = [
        {
            "attempt": int(a.get("attempt") if a.get("attempt") is not None else i),
            **{k: a[k] for k in _ATTEMPT_SUMMARY_KEYS if k in a},
            "infra_failure": _is_infra_failure(a),
        }
        for i, a in enumerate(attempts)
    ]
    return merged


async def _rollout_once(
    task: GAIATask,
    attempt_idx: int,
    *,
    label: str,
    model_config: Any,
    round_config: Any,
    pipeline_eval: "GAIAPipelineEvaluator",
    max_cost: float,
    run_task: Any = None,
) -> dict:
    """One *independent* rollout of ``task`` (§6.1 p.15).

    Independence is the whole point, so nothing is shared between attempts:

    * a fresh harness is built from ``round_config`` per attempt, which is also
      how the pre-pass@k code obtained a fresh runtime per task (Table 8: "each
      rollout runs in a fresh environment instance"). No slots, tool registry,
      or processor state cross over;
    * the task dataclass is copied per attempt, so the per-task cost cap
      applies to each attempt separately — k=2 can therefore spend up to twice
      ``--max-cost`` on one task, by design.

    The harness instance is handed back on the private ``_harness`` key because
    the caller needs it to retrieve the judge verdict; private keys are
    stripped before serialisation. ``run_task`` is injectable so the rollout
    path can be exercised offline.
    """
    from dataclasses import replace as _dc_replace

    runner = run_task if run_task is not None else _run_task
    task = _dc_replace(task, max_cost_usd=max_cost)
    harness = model_config.agentic(round_config)
    record = await runner(
        harness,
        task,
        label,
        pipeline_eval=pipeline_eval,
        harness_config=round_config,
        attempt_idx=attempt_idx,
    )
    record["_harness"] = harness
    return record


async def _run_task_pass_k(
    task: GAIATask,
    *,
    pass_k: int,
    sem: asyncio.Semaphore,
    rollout: Any,
    finalize: Any = None,
) -> dict:
    """Run ``pass_k`` independent rollouts of one task; return one merged record.

    Concurrency: the semaphore is acquired **per attempt, not per task**, so
    raising k does not multiply the number of harnesses in flight — the k
    attempts of a task queue for the same ``--concurrency`` slots as every
    other attempt in the round. Post-processing (judge lookup, trajectory
    write) runs outside the semaphore, exactly as it did before pass@k.
    """
    if pass_k < 1:
        raise ValueError(f"pass_k must be >= 1, got {pass_k}")

    async def _attempt(attempt_idx: int) -> dict:
        async with sem:
            record = await rollout(task, attempt_idx)
        if finalize is not None:
            record = await finalize(task, attempt_idx, record)
        return record

    attempts = list(await asyncio.gather(*(_attempt(i) for i in range(pass_k))))
    return _merge_attempt_records(attempts)


def _round_pass_rate(records: list[dict], pass_k: int) -> float:
    """Round score = mean over tasks of the unbiased pass@k estimator.

    Reuses :func:`experiments.variant_pool.reporting.pass_at_k` (A.3 p.29,
    formula 6) rather than re-deriving it. At ``n == k`` the estimator and the
    naive "did any attempt pass" ratio coincide — so ``--pass-k 1`` reproduces
    the historical ``passed / len(records)`` number exactly, and so does
    ``--pass-k 2`` with two rollouts. They diverge only when a task was sampled
    more often than k, which is the case the estimator exists for.

    A task with fewer attempts than k is scored at ``k_eff = n_att`` rather
    than guessed at: that degrades to "any attempt passed" for that task and is
    logged, because silently clamping is how a report starts lying.
    """
    if not records:
        return 0.0
    total = 0.0
    for record in records:
        n_att = int(record.get("n_att") or 0)
        n_pass = int(record.get("n_pass") or 0)
        if n_att < 1:
            # No attempt recorded at all — scores 0, exactly like a failure.
            continue
        k_eff = min(pass_k, n_att)
        if k_eff < pass_k:
            logger.warning(
                "task %s has %d attempt(s) but pass_k=%d — scoring it as pass@%d",
                record.get("task_id", "?"),
                n_att,
                pass_k,
                k_eff,
            )
        total += pass_at_k(n_att, n_pass, k_eff)
    return total / len(records)


def print_multiround_comparison(rounds: list[list[dict]], *, pass_k: int = 1) -> None:
    """Print multi-round comparison as two aligned tables + headline.

    Layout:
      * Per-task pass/fail history — PASS/FAIL per round + best-vs-R0 pp delta.
        Under pass@k each cell also carries the task's ``n_pass/n_att``, which
        is where a 2/2 → 1/2 drift becomes visible; §7.1 p.21 warns that pass@2
        hides exactly that decline behind an unchanged "solved".
      * Round totals — pass_rate, cost_usd, tokens, steps per round with a
        dedicated Δ column between consecutive rounds. Under pass@k two more
        rows appear: the unbiased pass@k estimator and the pooled per-attempt
        rate (SPEC §6.7: never report one without the other).
      * Headline — one-line callout of the total pass_rate swing.
    """
    if not rounds:
        return
    n_rounds = len(rounds)
    task_ids = [r["task_id"] for r in rounds[0]]
    n_tasks = len(task_ids)

    # Records written before pass@k carry no attempt counts; treat them as one
    # attempt whose outcome is `passed`, which is what they were.
    def _att(record: dict) -> tuple[int, int]:
        n_att = int(record.get("n_att") or 1)
        n_pass = record.get("n_pass")
        if n_pass is None:
            n_pass = 1 if record.get("passed") else 0
        return int(n_pass), n_att

    max_att = max((_att(r)[1] for rd in rounds for r in rd), default=1)
    multi_attempt = max_att > 1
    k_show = pass_k if pass_k > 1 else max_att

    lines: list[str] = []

    r_word = "round" if n_rounds == 1 else "rounds"
    t_word = "task" if n_tasks == 1 else "tasks"
    header = f"GAIA Evolver — Multi-Round Comparison  ({n_rounds} {r_word} × {n_tasks} {t_word})"
    if multi_attempt:
        header += f"  [pass@{k_show}]"
    lines.append(header)
    lines.append("")

    # Compute historical-best round index for "vs-best" deltas.
    totals = [
        {
            "passed": sum(1 for r in rd if r.get("passed")),
            "cost": sum(r.get("cost_usd", 0) or 0 for r in rd),
            "tokens": sum(r.get("total_tokens", 0) or 0 for r in rd),
            "steps": sum(r.get("steps", 0) or 0 for r in rd),
            "n_pass": sum(_att(r)[0] for r in rd),
            "n_att": sum(_att(r)[1] for r in rd),
            "pass_at_k": _round_pass_rate(
                [{"n_att": _att(r)[1], "n_pass": _att(r)[0], "task_id": r.get("task_id")} for r in rd],
                k_show,
            ),
        }
        for rd in rounds
    ]
    best_idx = min(
        range(n_rounds),
        key=lambda i: (-totals[i]["passed"], totals[i]["cost"], i),
    )

    # ── Per-task pass/fail history ──────────────────────────────────────
    TID_W, STAT_W, PP_W, DELTA_W = 20, 11, 12, 14
    lines.append("Per-task pass/fail history")
    hdr = f"  {'task_id':<{TID_W}}"
    for i in range(n_rounds):
        hdr += f" | {f'R{i} result':^{STAT_W}}"
    if n_rounds > 1:
        hdr += f" | {f'R{best_idx}-vs-R0 pass':^{PP_W}}"
        hdr += f" | {f'R{best_idx}-vs-R0 tokens':^{DELTA_W}}"
        hdr += f" | {f'R{best_idx}-vs-R0 steps':^{DELTA_W}}"
    lines.append(hdr)
    sep = f"  {'-' * TID_W}"
    for _ in range(n_rounds):
        sep += f"-+-{'-' * STAT_W}"
    if n_rounds > 1:
        sep += f"-+-{'-' * PP_W}"
        sep += f"-+-{'-' * DELTA_W}"
        sep += f"-+-{'-' * DELTA_W}"
    lines.append(sep)

    for tid in task_ids:
        rec0 = next((r for r in rounds[0] if r["task_id"] == tid), {})
        rec_best = next((r for r in rounds[best_idx] if r["task_id"] == tid), {})
        row = f"  {tid[:TID_W]:<{TID_W}}"
        for i in range(n_rounds):
            rec = next((r for r in rounds[i] if r["task_id"] == tid), {})
            status = "PASS" if rec.get("passed") else "FAIL"
            if multi_attempt and rec:
                n_pass_i, n_att_i = _att(rec)
                status = f"{status} {n_pass_i}/{n_att_i}"
            row += f" | {status:^{STAT_W}}"
        if n_rounds > 1:
            pp = (100 if rec_best.get("passed") else 0) - (100 if rec0.get("passed") else 0)
            row += f" | {f'{pp:+d}pp':^{PP_W}}"
            tok0 = int(rec0.get("total_tokens") or 0)
            tok_best = int(rec_best.get("total_tokens") or 0)
            stp0 = int(rec0.get("steps") or 0)
            stp_best = int(rec_best.get("steps") or 0)
            row += f" | {_pct_delta(tok_best, tok0):^{DELTA_W}}"
            row += f" | {_pct_delta(stp_best, stp0):^{DELTA_W}}"
        lines.append(row)
    lines.append("")

    # ── Round totals ────────────────────────────────────────────────────
    LBL_W, VAL_W, D_W = 12, 15, 10
    lines.append("Round totals")
    hdr = f"  {'metric':<{LBL_W}}"
    for i in range(n_rounds):
        hdr += f" | {f'R{i}':^{VAL_W}}"
        if i > 0:
            hdr += f"  {'Δ':^{D_W}}"
    lines.append(hdr)
    sep = f"  {'-' * LBL_W}"
    for i in range(n_rounds):
        sep += f"-+-{'-' * VAL_W}"
        if i > 0:
            sep += f"--{'-' * D_W}"
    lines.append(sep)

    n = n_tasks

    def _row(label: str, v_fn, d_fn) -> str:
        line = f"  {label:<{LBL_W}}"
        for i, t in enumerate(totals):
            line += f" | {v_fn(t):^{VAL_W}}"
            if i > 0:
                line += f"  {d_fn(t, totals[i - 1]):^{D_W}}"
        return line

    lines.append(
        _row(
            "pass_rate",
            lambda t: f"{100 * t['passed'] / n:.1f}% ({t['passed']}/{n})" if n else "-",
            lambda cur, prev: f"{100 * (cur['passed'] - prev['passed']) / n:+.1f}pp" if n else "-",
        )
    )
    if multi_attempt:
        # The unbiased estimator (A.3 p.29) and, right under it, the pooled
        # per-attempt rate. Reporting them together is the whole point: they
        # coincide when nothing is drifting and separate when pass@k is
        # masking a decline in per-attempt success probability (§7.1 p.21).
        lines.append(
            _row(
                f"pass@{k_show}",
                lambda t: f"{100 * t['pass_at_k']:.1f}%",
                lambda cur, prev: f"{100 * (cur['pass_at_k'] - prev['pass_at_k']):+.1f}pp",
            )
        )
        lines.append(
            _row(
                "rollouts",
                lambda t: (f"{100 * t['n_pass'] / t['n_att']:.1f}% ({t['n_pass']}/{t['n_att']})" if t["n_att"] else "-"),
                lambda cur, prev: (
                    f"{100 * (cur['n_pass'] / cur['n_att'] - prev['n_pass'] / prev['n_att']):+.1f}pp"
                    if cur["n_att"] and prev["n_att"]
                    else "-"
                ),
            )
        )
    lines.append(
        _row(
            "cost_usd",
            lambda t: f"${t['cost']:.2f}",
            lambda cur, prev: _pct_delta(cur["cost"], prev["cost"]),
        )
    )
    lines.append(
        _row(
            "tokens",
            lambda t: f"{t['tokens']:,}",
            lambda cur, prev: _pct_delta(cur["tokens"], prev["tokens"]),
        )
    )
    lines.append(
        _row(
            "steps",
            lambda t: f"{t['steps']}",
            lambda cur, prev: _pct_delta(cur["steps"], prev["steps"]),
        )
    )

    # ── Headline ────────────────────────────────────────────────────────
    if n_rounds > 1 and n_tasks > 0:
        p0 = 100 * totals[0]["passed"] / n_tasks
        p_best = 100 * totals[best_idx]["passed"] / n_tasks
        pp = p_best - p0
        tok0 = totals[0]["tokens"]
        tok_best = totals[best_idx]["tokens"]
        lines.append("")
        lines.append(
            f"  >>> best-vs-R0 pass_rate: R0 {p0:.1f}% -> R{best_idx} {p_best:.1f}% over {n_rounds} rounds  ({pp:+.1f}pp)"
        )
        lines.append(
            f"  >>> best-vs-R0 tokens:    R0 {tok0:,} -> R{best_idx} {tok_best:,} over {n_rounds} rounds  ({_pct_delta(tok_best, tok0)})"
        )

    width = max(80, max(len(line) for line in lines if line))
    print("\n" + "=" * width)
    for line in lines:
        print(line)
    print("=" * width)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def main() -> None:
    parser = argparse.ArgumentParser(description="GAIA Evolver: multi-round meta-harness")
    parser.add_argument("--max-tasks", type=int, default=MAX_TASKS)
    parser.add_argument("--max-cost", type=float, default=MAX_COST_USD)
    parser.add_argument("--num-rounds", type=int, default=NUM_ROUNDS)
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help=(f"Model used by the task-doing (inner) agent on each GAIA task. Default: {DEFAULT_MODEL}."),
    )
    parser.add_argument(
        "--meta-model",
        default=DEFAULT_META_MODEL,
        help=(
            "Model used by the meta-agent during evolve/reflect. Kept "
            "separate from --model so the outer loop can run on a stronger "
            f"tier than the inner loop. Default: {DEFAULT_META_MODEL}."
        ),
    )
    parser.add_argument("--provider-id", default=DEFAULT_PROVIDER_ID)
    parser.add_argument(
        "--api-base",
        default=None,
        help=(
            "Optional OpenAI-compatible endpoint for the inner task agent "
            "(e.g. http://host:port/v1). When set, the --model request is "
            "routed to this URL through LiteLLM "
            "header is dropped. Only affects --model; --meta-model and the "
            "judge still use the default provider. Default: unset."
        ),
    )
    parser.add_argument(
        "--api-key",
        default=None,
        help=(
            "API key paired with --api-base. Ignored when --api-base is "
            "unset. Defaults to 'EMPTY' (works for most open local endpoints)."
        ),
    )
    parser.add_argument("--clean", action="store_true", help="Wipe runs/<tag>/ before starting")
    parser.add_argument(
        "--no-judge",
        action="store_true",
        help="Disable LLMJudgeProcessor (verdict fields omitted from frontmatter). Default: judge enabled.",
    )
    parser.add_argument(
        "--traj-failure-signals",
        action="store_true",
        help="Emit flat per-attempt failure-signal counts (search_unavailable_count, "
        "fetch_error_count, fetch_empty_count, loop_warning_count) into trajectory "
        "frontmatter. Default off keeps frontmatter byte-identical.",
    )
    parser.add_argument("--evolve-cost", type=float, default=EVOLVE_COST_CAP_USD)
    parser.add_argument("--evolve-steps", type=int, default=EVOLVE_MAX_STEPS)
    parser.add_argument("--evolve-wall-clock", type=int, default=EVOLVE_WALL_CLOCK_S)
    parser.add_argument(
        "--regression-tolerance",
        type=float,
        default=0.03,
        help=(
            "Score drop allowed before reverting to previous config. "
            "0.0 = any regression reverts. 0.05 = up to 5 pp drop tolerated. "
            "Default 0.03 absorbs typical eval stochastic noise (~2-3 pp) so "
            "a genuinely-useful code fix isn't discarded because an unrelated "
            "task flipped. The best_score baseline still only advances on "
            "strict improvements, so tolerance cannot drift it downward. "
            "Score = pass_rate - cost_weight * max(relative_cost_delta, 0)."
        ),
    )
    parser.add_argument(
        "--cost-weight",
        type=float,
        default=0.0,
        help=(
            "How strongly per-round cost increases penalize the gating score. "
            "0.0 = pass_rate only (legacy). 0.1 = a 20%% cost increase is "
            "treated as a 2 pp pass_rate regression."
        ),
    )
    parser.add_argument(
        "--pass-count-noise-threshold",
        type=int,
        default=PASS_COUNT_NOISE_THRESHOLD,
        help=(
            "Absolute passed-task count delta below which a pass_rate "
            "regression is treated as noise (no rollback). A rollback fires "
            "only when BOTH the score-based tolerance is exceeded AND the "
            f"absolute passed-count delta meets this threshold. Default "
            f"{PASS_COUNT_NOISE_THRESHOLD} — small flips on small task sets "
            "are usually eval stochasticity, not regression."
        ),
    )
    parser.add_argument(
        "--run-tag",
        default=None,
        help=(
            "Label for this run's directory. "
            "Output goes to recipe/gaia_evolver/runs/{run_tag}/ so repeated "
            "runs don't clobber each other. Defaults to 'run_YYYYMMDD-HHMMSS'."
        ),
    )
    _DEFAULT_DATA_PATH = str(Path(__file__).resolve().parent / "data" / "webthinker_gaia_dev.json")
    parser.add_argument(
        "--data-path",
        default=_DEFAULT_DATA_PATH,
        help=(
            "Path to a local GAIA JSON file (webthinker schema). Pass '' to fall back to HuggingFace dataset download."
        ),
    )
    parser.add_argument(
        "--attachments-dir",
        default=None,
        help=(
            "Optional dir containing per-task attachment files named "
            "'<task_id>.<ext>'. Only needed if your JSON references attachments."
        ),
    )
    parser.add_argument(
        "--level",
        type=int,
        default=0,
        help="GAIA difficulty level to load (1, 2, or 3). 0 = all levels. Default: 0 (all).",
    )
    parser.add_argument(
        "--max-steps",
        type=int,
        default=MAX_STEPS,
        help=f"Per-task step cap. Default: {MAX_STEPS}.",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=DEFAULT_CONCURRENCY,
        help=(f"Max concurrent trajectories per round. 1 = serial. Default: {DEFAULT_CONCURRENCY}."),
    )
    parser.add_argument(
        "--pass-k",
        type=int,
        default=1,
        help=(
            "Independent rollouts per task per round (paper §6.1 p.15 runs "
            "pass@2: 'solved if either succeeds'). Each attempt builds its own "
            "harness and carries its own --max-cost cap, so k=2 roughly doubles "
            "a round's spend; concurrency is unchanged because the semaphore is "
            "acquired per attempt. Infrastructure failures count as failed "
            "attempts and are not resampled (A.3 p.29). Default: 1 — one "
            "rollout per task, the historical behaviour."
        ),
    )
    args = parser.parse_args()

    if args.pass_k < 1:
        parser.error(f"--pass-k must be >= 1, got {args.pass_k}")

    RUNS_DIR.mkdir(parents=True, exist_ok=True)

    # Scope this run's outputs under runs/{run_tag}/ so repeated runs
    # don't overwrite each other. --clean wipes THIS run's tree (not all runs).
    run_tag = args.run_tag or time.strftime("run_%Y%m%d-%H%M%S")
    RUN_DIR = RUNS_DIR / run_tag
    if args.clean and RUN_DIR.exists():
        shutil.rmtree(RUN_DIR)
        logger.info("Cleaned %s", RUN_DIR)
    elif RUN_DIR.exists() and any(RUN_DIR.iterdir()):
        logger.warning(
            "--run-tag %r already exists and is non-empty; new output will be "
            "interleaved with prior run data. Pass --clean to wipe it first.",
            run_tag,
        )
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    logger.info("Run outputs → %s", RUN_DIR)

    provider = _make_provider(
        args.model,
        args.provider_id,
        api_base=args.api_base,
        api_key=args.api_key,
    )
    model_config = ModelConfig(main=provider)

    # Judge runs on --meta-model (Opus by default) rather than the inner
    # agent's model. Correctness signal comes from the stronger tier even
    # when the task agent is on Sonnet. Extended thinking is intentionally
    # OFF here — a judge call returns a short verdict, so a 32k thinking
    # budget per eval would dominate the run's cost for no quality gain.
    # Used for both GAIAPipelineEvaluator (end-of-task scoring) and
    # LLMJudgeProcessor (per-step verdict injected into the config).
    judge_provider = _make_provider(args.meta_model, args.provider_id)
    pipeline_eval = GAIAPipelineEvaluator(judge_provider=judge_provider)

    # The meta-agent's workload is architectural reasoning over trajectories
    # — it benefits materially from Anthropic's extended thinking when the
    # underlying model is served through AnthropicProvider. On non-Anthropic
    # deployments the kwargs are no-ops and this falls back to the same
    # LiteLLM provider the target uses. The meta-agent runs on --meta-model
    # (defaults to Opus) while the inner task agent runs on --model (defaults
    # to Sonnet), so the outer loop can reason at a stronger tier than the
    # inner loop without paying Opus rates on every benchmark task.
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
    logger.info(
        "Loading GAIA tasks (level=%s, max_tasks=%s)...",
        level_filter if level_filter else "all",
        max_tasks_filter if max_tasks_filter else "all",
    )
    if args.data_path:
        tasks = load_gaia_tasks_from_json(
            args.data_path,
            level=level_filter,
            max_tasks=max_tasks_filter,
            attachments_dir=args.attachments_dir,
        )
    else:
        tasks = load_gaia_tasks(level=level_filter, max_tasks=max_tasks_filter)
    if not tasks:
        logger.error("No tasks loaded!")
        return
    # Apply per-task step cap from CLI (overrides the default baked into GAIATask).
    for t in tasks:
        t.max_steps = args.max_steps
    logger.info("Loaded %d tasks (max_steps=%d)", len(tasks), args.max_steps)

    # Original baseline — never mutated. Each round starts from this (or the
    # previously-accepted compiled config if gating reverted).
    # Uses the GAIA-tuned preset (benchmarks/gaia/prompts/gaia_agent.j2 +
    # build_gaia_tools_full with code_interpreter + GPT-5 token/loop/checkpoint
    # budgets). The actual model
    # is still governed by --model/--provider-id via ModelConfig below.
    original_base = make_gaia_builder_gpt5(
        max_cost_usd=args.max_cost,
    ).build()

    # current_config starts as the original baseline; replaced by the compiled
    # config after each reflect+compile cycle.
    current_config = original_base

    # Add LLMJudgeProcessor to the serializable processors list unless --no-judge
    # was passed. Storing it as a _target_ dict (not in _rt_procs) means it
    # survives every YAML round-trip naturally: from_yaml_file() re-instantiates
    # it via _instantiate_proc, and the meta-agent can see/modify it in config.yaml.
    if not args.no_judge:
        import dataclasses as _dcs
        from harnessx.core.harness import _serialize_processor
        from harnessx.processors.evaluation.llm_judge import LLMJudgeProcessor

        _initial_judge = LLMJudgeProcessor(judge_model=args.meta_model)
        _judge_dict = _serialize_processor(_initial_judge)
        if _judge_dict:
            current_config = _dcs.replace(
                current_config,
                processors=[*current_config.processors, _judge_dict],
            )
            original_base = current_config

    # Cross-round memo for meta-agent continuity (also surfaces "Needs From
    # Human" asks). The file accumulates across the whole experiment.
    LEARNINGS_PATH = RUN_DIR / "learnings.md"

    meta_agent = MetaAgent(
        inner_model=meta_model,
        memo_path=LEARNINGS_PATH,
        extra_skills_dirs=([_GAIA_SKILLS_DIR] if _GAIA_SKILLS_DIR.is_dir() else None),
        max_cost_usd=args.evolve_cost,
        wall_clock_s=float(args.evolve_wall_clock),
        max_steps=args.evolve_steps,
    )

    # Track (pass_rate, round_cost, config_object, round_idx, passed_count)
    # of the historical-best round so tolerance cannot let the baseline
    # drift downward over many rounds. Every round is compared against
    # this; only strictly-better rounds displace the holder. The raw
    # passed_count is tracked alongside the rate so the gate can apply
    # the absolute noise-threshold rule (``--pass-count-noise-threshold``).
    best_so_far: tuple[float, float, Any, int, int] | None = None
    # evolve_status for the round about to start. R0's config came from the
    # baseline (no evolve), every later round's came from the try/except
    # block at the end of the previous iteration.
    next_evolve_status: str = "baseline"

    all_rounds: list[list[dict]] = []
    round_summaries: list[dict] = []

    from harnessx.tracing.journal import HarnessJournal as _HJ

    for round_idx in range(args.num_rounds):
        is_last = round_idx == args.num_rounds - 1

        # Round's self-contained tree: config.yaml + trajectories/ + sessions/
        # + (for R1+) evolve/. Runs/{tag}/R{N}/ answers "everything about R{N}".
        round_dir = RUN_DIR / f"R{round_idx}"
        round_dir.mkdir(parents=True, exist_ok=True)
        traj_dir = round_dir / "trajectories"
        traj_dir.mkdir(parents=True, exist_ok=True)
        sessions_dir = round_dir / "sessions"

        # Per-round journal: isolates this round's JSONL from others, and gives
        # the meta-agent a single directory to glob for "R{N}'s run data".
        # Replaces the cross-round shared tracer that lived across rounds.
        round_journal = _HJ(base_dir=str(sessions_dir), export_jsonl=True)
        round_config = current_config.copy(tracer=round_journal)

        # judge_proc is no longer a round-level handle: each per-task harness
        # instantiates its own LLMJudgeProcessor from the YAML dict, with its
        # own _verdict_sink. Verdict retrieval happens inside _finalize_attempt
        # after the harness has run, by scanning harness._rt.processors directly.

        # Dump the config actually executed this round — baseline or evolved —
        # for reproducibility and for evolve() to Read on the next iteration.
        round_config_path = round_dir / "config.yaml"
        round_config.to_yaml_file(round_config_path)

        config_label = "baseline" if round_idx == 0 else f"compiled_R{round_idx}"
        logger.info("\n" + "=" * 60)
        logger.info("ROUND %d/%d  [%s]", round_idx, args.num_rounds - 1, config_label)
        logger.info("=" * 60)

        # ── Run all tasks (up to args.concurrency in parallel) ─────────────
        # Trajectories are independent: each rollout gets its own harness
        # instance, own session_id, and writes to its own per-attempt file.
        # judge_proc is shared but keyed by run_id. The semaphore bounds
        # concurrent harness.run() calls to stay within LLM provider rate
        # limits; it is acquired per *attempt*, so --pass-k does not multiply
        # concurrency. Cheap post-processing (judge lookup, trajectory write)
        # runs outside it.
        sem = asyncio.Semaphore(max(1, args.concurrency))

        async def _rollout(task: GAIATask, attempt_idx: int) -> dict:
            return await _rollout_once(
                task,
                attempt_idx,
                label=f"R{round_idx}",
                model_config=model_config,
                round_config=round_config,
                pipeline_eval=pipeline_eval,
                max_cost=args.max_cost,
            )

        async def _finalize_attempt(task: GAIATask, attempt_idx: int, record: dict) -> dict:
            harness = record.pop("_harness", None)
            raw = record.get("_result")
            tid = record.get("task_id") or "unknown"
            # Attempt 0 keeps the historical <task_id>.md name so a --pass-k 1
            # round's trajectory tree is unchanged; later attempts get a suffix.
            traj_name = f"{tid}.md" if attempt_idx == 0 else f"{tid}.a{attempt_idx + 1}.md"
            record["trajectory_file"] = f"R{round_idx}/trajectories/{traj_name}"

            # Collect judge verdict: find the LLMJudgeProcessor that ran
            # inside this harness instance and pull its verdict for this run_id.
            judge_entry: dict = {}
            if not args.no_judge and harness is not None:
                from harnessx.processors.evaluation.llm_judge import (
                    LLMJudgeProcessor as _LJP,
                )

                run_id = getattr(raw, "run_id", "") or "" if raw is not None else ""
                for _proc in harness._rt.processors.get("*", []):
                    if isinstance(_proc, _LJP):
                        judge_entry = _proc.get_verdict(run_id) or {}
                        break

            # Fold into record (v2 frontmatter fields):
            record["extracted_answer"] = judge_entry.get("extracted_answer") or ""
            record["llm_judge_verdict"] = judge_entry.get("verdict") or {}

            if raw is not None:
                # Populate behavioral fields FIRST so frontmatter has them.
                record["pivotal_tool"] = _pick_pivotal_tool(raw)
                call_counts, error_counts = _compute_tool_counts(raw)
                record["tool_call_counts"] = call_counts
                record["tool_error_counts"] = error_counts
                _, err_count = _compute_tool_stats(raw)
                record["error_count"] = err_count
                traj_text = _build_trajectory_text(task, raw, harness_config=round_config)
                _write_task_trajectory(
                    traj_dir,
                    task,
                    traj_text,
                    record=record,
                    filename=traj_name,
                    failure_signals=bool(getattr(args, "traj_failure_signals", False)),
                )
            return record

        records: list[dict] = list(
            await asyncio.gather(
                *(
                    _run_task_pass_k(
                        t,
                        pass_k=args.pass_k,
                        sem=sem,
                        rollout=_rollout,
                        finalize=_finalize_attempt,
                    )
                    for t in tasks
                )
            )
        )
        gc.collect()

        all_rounds.append(records)

        # ``passed`` stays the count of *tasks* solved ("either attempt
        # succeeds", §6.1 p.15) — that is the binary per-task signal the
        # seesaw constraint and the noise-threshold gate consume. The rollout
        # totals below are what make pass@1 recoverable from the same file.
        passed = sum(1 for r in records if r.get("passed"))
        n_pass_total = sum(int(r.get("n_pass") or 0) for r in records)
        n_att_total = sum(int(r.get("n_att") or 0) for r in records)
        infra_total = sum(int(r.get("infra_failures") or 0) for r in records)
        round_cost = sum((r.get("cost_usd") or 0) for r in records)
        totals = _compute_round_totals(records)
        round_pass_rate = round(_round_pass_rate(records, args.pass_k), 3)
        round_summaries.append(
            {
                "round": round_idx,
                "config": config_label,
                "tasks": len(records),
                "passed": passed,
                "pass_rate": round_pass_rate,
                "pass_k": args.pass_k,
                "n_pass": n_pass_total,
                "n_att": n_att_total,
                "infra_failures": infra_total,
                "total_cost_usd": round(round_cost, 4),
                "total_tokens": totals["total_tokens"],
                "total_steps": totals["total_steps"],
                "evolve_status": next_evolve_status,
            }
        )
        if args.pass_k > 1:
            logger.info(
                "[R%d] pass=%d/%d  rollouts=%d/%d (infra_fail=%d)  cost=$%.3f",
                round_idx,
                passed,
                len(records),
                n_pass_total,
                n_att_total,
                infra_total,
                round_cost,
            )
        else:
            logger.info("[R%d] pass=%d/%d  cost=$%.3f", round_idx, passed, len(records), round_cost)

        # ── Best-so-far gating ────────────────────────────────────────────
        # Compare against the historical-best round (not the last-accepted)
        # so tolerance cannot silently drift the baseline downward over many
        # rounds. Score = pass_rate - cost_weight * max(cost_delta, 0).
        gate_decision, gate_reason, best_so_far, reverted_cfg = _score_and_gate(
            round_pass_rate=round_pass_rate,
            round_cost=round_cost,
            round_idx=round_idx,
            round_config=current_config,
            round_passed=passed,
            best=best_so_far,
            tolerance=args.regression_tolerance,
            cost_weight=args.cost_weight,
            pass_count_noise_threshold=args.pass_count_noise_threshold,
        )
        if reverted_cfg is not None:
            best_round_for_log = best_so_far[3]
            logger.warning(
                "[R%d] REGRESSION — reverting current_config to R%d for next round",
                round_idx,
                best_round_for_log,
            )
            current_config = reverted_cfg

        # Back-fill the journal entry for this round (if the meta-agent
        # wrote one for it). ``gating_outcome`` + per-task
        # ``gating_attribution`` turn the journal into structured
        # evidence the next evolve's CONTEXT.md can aggregate. R0 is
        # baseline — no meta-agent ran before it — so nothing to fill.
        if round_idx >= 1:
            try:
                from harnessx.meta_harness import journal as _journal

                entries = _journal.read_entries(LEARNINGS_PATH)
                entry = next((e for e in entries if e.round == round_idx), None)
                if entry is not None:
                    prev_records = all_rounds[-2] if len(all_rounds) >= 2 else []
                    prev_passed = {r["task_id"] for r in prev_records if r.get("passed")}
                    prev_appeared = {r["task_id"] for r in prev_records if r.get("task_id")}
                    cur_passed = {r["task_id"] for r in records if r.get("passed")}
                    cur_appeared = {r["task_id"] for r in records if r.get("task_id")}
                    outcome = "reverted" if gate_decision == "REVERTED" else "accepted"
                    # Byte-identical config = explicit noop — surface
                    # that separately from a change that survived gating.
                    if next_evolve_status == "noop":
                        outcome = "noop"
                    attribution = _journal.compute_attribution(
                        entry.predicted_affected,
                        passed_now=cur_passed,
                        passed_before=prev_passed,
                        appeared_now=cur_appeared,
                        appeared_before=prev_appeared,
                    )
                    # Side-effect detection: tasks that regressed this
                    # round but were NOT in predicted_affected. These
                    # reduce the agent's lever precision — a hypothesis
                    # that helps its claimed target but breaks something
                    # else shouldn't read as 100% effective.
                    predicted_set = set(entry.predicted_affected)
                    regressed_unpredicted = sorted(
                        (prev_passed & prev_appeared & cur_appeared) - cur_passed - predicted_set
                    )
                    # Load orchestrator-computed changeset from the evolve
                    # step that produced this round's config. The file is
                    # written under R{round_idx}/evolve/_meta_scratch/.
                    changeset_path = RUN_DIR / f"R{round_idx}" / "evolve" / "_meta_scratch" / "changeset.json"
                    changeset: dict = {}
                    if changeset_path.is_file():
                        try:
                            changeset = json.loads(changeset_path.read_text(encoding="utf-8"))
                        except (json.JSONDecodeError, OSError) as cs_exc:
                            logger.warning(
                                "[R%d] changeset.json unreadable: %s",
                                round_idx,
                                cs_exc,
                            )
                    ok = _journal.fill_gating(
                        LEARNINGS_PATH,
                        round_idx,
                        outcome,
                        attribution,
                        extra_frontmatter={
                            "regressed_unpredicted": regressed_unpredicted,
                            "changeset": changeset,
                        },
                    )
                    if ok:
                        logger.info(
                            "[R%d] journal fill_gating outcome=%s attribution=%s regressed_unpredicted=%d",
                            round_idx,
                            outcome,
                            attribution,
                            len(regressed_unpredicted),
                        )
                else:
                    logger.debug(
                        "[R%d] no journal entry found — meta-agent did not "
                        "append one yet; skipping attribution back-fill",
                        round_idx,
                    )
            except Exception as exc:  # noqa: BLE001
                # Never fail the round over memoisation bookkeeping.
                logger.warning(
                    "[R%d] journal fill_gating failed (non-fatal): %s",
                    round_idx,
                    exc,
                )

        if is_last:
            continue

        # ── Evolve: produce next round's config ───────────────────────────
        next_round_dir = RUN_DIR / f"R{round_idx + 1}"
        next_round_dir.mkdir(parents=True, exist_ok=True)
        evolve_dir = next_round_dir / "evolve"
        logger.info(
            "[R%d] evolve → %s (memo=%s)",
            round_idx,
            evolve_dir,
            LEARNINGS_PATH,
        )

        # Replay gate needs to resolve task_id strings (read from trajectory
        # frontmatter) back to the concrete GAIATask objects we loaded at
        # startup. Index them once per round and expose via a tiny async
        # closure; meta_harness stays benchmark-agnostic and the recipe
        # owns the dataset shape.
        _task_index = {t.task_id: t for t in tasks if t.task_id}

        async def _gaia_task_loader(task_id: str):  # noqa: ANN001
            try:
                return _task_index[task_id]
            except KeyError as exc:
                raise KeyError(
                    f"replay_gate requested task_id={task_id!r} but it is "
                    f"not in the current task set ({len(_task_index)} tasks "
                    "loaded). The gate picks task ids from the last round's "
                    "trajectory frontmatter; mismatches usually mean --data-path "
                    "changed between rounds."
                ) from exc

        try:
            new_yaml = await meta_agent.evolve(
                current_config=round_config_path,
                trajectories_dir=traj_dir,
                output_dir=evolve_dir,
                replay_model=model_config,
                replay_max_cost_usd=min(0.5, args.max_cost),
            )
            from harnessx.core.harness import HarnessConfig as _HC

            candidate_cfg = _HC.from_yaml_file(new_yaml).canonicalize()
            # Byte-identical output = the meta-agent's explicit no-op idiom
            # ("`cp current output/config.yaml`"). Surfacing this lets the
            # next round's summary distinguish "agent decided no change" from
            # "agent produced a meaningful change".
            if round_config_path.read_bytes() == Path(new_yaml).read_bytes():
                next_evolve_status = "noop"
            else:
                next_evolve_status = "ok"
            logger.info(
                "[R%d] R%d config → %s (status=%s)",
                round_idx,
                round_idx + 1,
                new_yaml,
                next_evolve_status,
            )
            current_config = candidate_cfg
        except Exception as exc:  # noqa: BLE001
            next_evolve_status = "crashed"
            logger.exception(
                "[R%d] evolve crashed — R%d reuses current config: %s",
                round_idx,
                round_idx + 1,
                exc,
            )

    # ── Final showcase ─────────────────────────────────────────────────────
    print_multiround_comparison(all_rounds, pass_k=args.pass_k)

    results_path = RUN_DIR / "comparison.json"
    results_path.write_text(
        json.dumps(
            {
                "rounds": [[{k: v for k, v in r.items() if not k.startswith("_")} for r in rd] for rd in all_rounds],
                "round_summaries": round_summaries,
                "run_config": {
                    "model": args.model,
                    "meta_model": args.meta_model,
                    "max_tasks": args.max_tasks,
                    "max_cost_usd": args.max_cost,
                    "num_rounds": args.num_rounds,
                    "pass_k": args.pass_k,
                    "concurrency": args.concurrency,
                    "evolve_steps": args.evolve_steps,
                    "evolve_cost": args.evolve_cost,
                    "evolve_wall_clock": args.evolve_wall_clock,
                    "run_dir": str(RUN_DIR),
                },
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    logger.info("Results → %s", results_path)


def _compute_tool_counts(result: Any) -> tuple[dict[str, int], dict[str, int]]:
    """Per-tool call counts and error counts for one HarnessResult.

    Returns (tool_call_counts, tool_error_counts). Both keyed by tool name.
    Inlined from the deleted recipe/gaia_evolver/signals.py to preserve
    measurement data in the v2 frontmatter.
    """
    call_counts: dict[str, int] = {}
    error_counts: dict[str, int] = {}
    traj = getattr(result, "trajectory", None)
    if not traj or not hasattr(traj, "steps"):
        return call_counts, error_counts
    for step in traj.steps:
        for tr in step.observation or []:
            name = getattr(tr, "tool_name", "") or ""
            if not name:
                continue
            call_counts[name] = call_counts.get(name, 0) + 1
            if getattr(tr, "error", None):
                error_counts[name] = error_counts.get(name, 0) + 1
    return call_counts, error_counts


#: Exact failure-marker substrings that only ever appear in the trajectory
#: *body*, mapped to the flat frontmatter scalar that counts them. The
#: search/fetch tools return ``[SEARCH UNAVAILABLE]`` / ``Fetch error for `` /
#: ``[fetch failed`` / ``No content retrieved from`` as their result string, and
#: LoopDetectionProcessor appends ``[LoopDetection]`` onto the tool result it is
#: warning about (never the system prompt). A meta-agent whose scanners read
#: only frontmatter never sees any of these, so ``--traj-failure-signals`` lifts
#: their per-attempt counts up where those scanners can act on them. Both fetch
#: hard-failure spellings roll into ``fetch_error_count``; the distinct
#: ``No content retrieved from`` (a fetch that returned nothing) is
#: ``fetch_empty_count``.
_FAILURE_SIGNAL_MARKERS: "dict[str, tuple[str, ...]]" = {
    "search_unavailable_count": ("[SEARCH UNAVAILABLE]",),
    "fetch_error_count": ("Fetch error for ", "[fetch failed"),
    "fetch_empty_count": ("No content retrieved from",),
    "loop_warning_count": ("[LoopDetection]",),
}


def _count_trajectory_failure_signals(text: str) -> "dict[str, int]":
    """Count exact-string failure markers in a rendered trajectory body.

    Counts over the serialized step content (``text``, exactly what the body
    serializer produced), not over the tool-result fields in isolation: every
    marker is emitted into the body, and ``[LoopDetection]`` is appended onto a
    tool result rather than surfaced as its own field, so scanning the rendered
    body is both sufficient and the closest match to what a frontmatter-only
    scanner is being taught to summarise. Returns one flat scalar per key in
    :data:`_FAILURE_SIGNAL_MARKERS`, summing every marker mapped to that key. A
    marker the model happens to echo verbatim in its own text is counted too;
    the meta-agent reads these as a coarse "did this failure mode appear this
    attempt" signal, not a provenance-exact tally.
    """
    return {key: sum(text.count(m) for m in markers) for key, markers in _FAILURE_SIGNAL_MARKERS.items()}


def _render_trajectory_frontmatter(record: dict, failure_signals: "dict[str, int] | None" = None) -> str:
    """Render per-task YAML frontmatter (v2 schema) for the trajectory .md file.

    Agent-facing contract: ``Read limit=30`` yields the key measurements + judge
    verdict at the top, so the meta-agent can orient before drilling into the body.

    Fields split into three tiers, in render order:

    1. Behaviour signals — always present (``exit_reason``, ``steps``, counts…).
    2. Evaluation signals — always present. ``eval_passed`` / ``eval_score``
       come from the external pipeline evaluator and are authoritative. The
       ground-truth answer text is intentionally withheld from the
       frontmatter, and so is the evaluator's textual reason — only the
       pass/fail outcome and numeric score are exposed so the meta-agent
       evolves on correctness signals, not on a known target string.
       (``extracted_answer``, written by the judge tier, is what the agent
       committed — not the dataset's expected answer.)
    3. Judge signals — optional, present only when ``llm_judge_verdict`` is
       populated (``--no-judge`` omits all six ``judge_*`` / ``extracted_answer``
       fields). These are opinion, not ground truth.
    """
    import json as _json

    def _yaml_scalar(v: Any) -> str:
        if v is None:
            return '""'
        if isinstance(v, bool):
            return "true" if v else "false"
        if isinstance(v, (int, float)):
            return str(v)
        if isinstance(v, list):
            return _json.dumps(v, ensure_ascii=False)
        if isinstance(v, dict):
            return _json.dumps(v, ensure_ascii=False)
        s = str(v).replace("\n", " ").strip()
        return _json.dumps(s, ensure_ascii=False)

    tool_call_counts = record.get("tool_call_counts") or {}
    tool_error_counts = record.get("tool_error_counts") or {}
    tools_used = sorted(tool_call_counts.keys())
    # Prefer `output` as the canonical source. Keep `final_output` as legacy fallback.
    final_output = (record.get("output") or record.get("final_output") or "").strip()

    fields: list[tuple[str, Any]] = [
        ("task_id", record.get("task_id") or "unknown"),
        ("exit_reason", record.get("exit_reason") or ""),
        ("steps", int(record.get("steps") or 0)),
        ("cost_usd", float(record.get("cost_usd") or 0.0)),
        ("final_output_length", len(final_output)),
        ("model_empty_end_turn", bool(record.get("model_empty_end_turn") or False)),
        ("empty_end_turn_recovered", bool(record.get("empty_end_turn_recovered") or False)),
        ("tools_used", tools_used),
        ("tool_call_counts", tool_call_counts),
        ("tool_error_counts", tool_error_counts),
    ]

    # Optional failure-signal tier (``--traj-failure-signals``). Off => this
    # block is skipped and the frontmatter is byte-identical to the v1/v2
    # schema. On => four flat scalars land in the behaviour tier, ahead of the
    # eval block, so they fall inside the agent-facing ``Read limit=30`` window.
    if failure_signals is not None:
        fields.extend((key, int(failure_signals.get(key, 0))) for key in _FAILURE_SIGNAL_MARKERS)

    total_tokens = record.get("total_tokens")
    if total_tokens is not None:
        fields.insert(4, ("total_tokens", int(total_tokens)))

    # External evaluator (authoritative) — always emitted so the meta-agent
    # can see correctness outcomes. Only pass/fail and numeric score are
    # exposed; the expected answer text AND the evaluator's textual reason
    # are both withheld, because a reason string can still smuggle the
    # agent's extracted answer or correctness-adjacent phrasing. The
    # meta-agent evolves on pure pass/fail signals.
    fields.extend(
        [
            ("eval_passed", bool(record.get("passed") or False)),
            ("eval_score", float(record.get("score") or 0.0)),
        ]
    )

    judge_verdict = record.get("llm_judge_verdict") or {}
    if judge_verdict:
        extracted_answer = record.get("extracted_answer") or ""
        # missing_capability is the richer signal the meta-agent reads when
        # deciding whether to reach for the Action lever (authoring a new
        # tool) over another processor tweak. Always emit when the key
        # exists so it shows up in frontmatter `Read limit=30`; the empty
        # payload ({"present":false,"summary":"","evidence_steps":[]})
        # reads fine as a negative signal too.
        mc = judge_verdict.get("missing_capability")
        if not isinstance(mc, dict):
            mc = {"present": False, "summary": "", "evidence_steps": []}
        fields.extend(
            [
                ("extracted_answer", extracted_answer),
                ("judge_verdict", judge_verdict.get("verdict") or ""),
                ("judge_confidence", float(judge_verdict.get("confidence") or 0.0)),
                ("judge_cause", judge_verdict.get("cause") or ""),
                ("judge_missing", judge_verdict.get("missing") or ""),
                ("judge_lesson", judge_verdict.get("lesson") or ""),
                ("judge_missing_capability", mc),
            ]
        )

    lines = ["---"]
    for k, v in fields:
        lines.append(f"{k}: {_yaml_scalar(v)}")
    lines.append("---")
    return "\n".join(lines)


def _write_task_trajectory(
    round_dir: Path,
    task: Any,
    text: str,
    record: dict | None = None,
    filename: str | None = None,
    failure_signals: bool = False,
) -> None:
    """Write a single task's trajectory to ``round_dir/<task_id>.md``.

    When ``record`` is provided, prepends the YAML frontmatter produced by
    :func:`_render_trajectory_frontmatter`. Legacy callers passing only
    ``(round_dir, task, text)`` still work — they just get a body without
    frontmatter.

    ``filename`` overrides the default ``<task_id>.md``. Under pass@k the
    attempts of one task would otherwise overwrite each other; attempt 0 keeps
    the historical name and later attempts get a suffix, so each rollout has
    its own trajectory with its own (per-attempt) frontmatter.

    ``failure_signals`` (``--traj-failure-signals``, default off) adds the flat
    body-derived failure counts to the frontmatter. Off keeps the frontmatter
    byte-identical; the counts are computed from ``text`` (the body) so they
    reflect exactly what the meta-agent would otherwise have to read the body
    to find.
    """
    round_dir.mkdir(parents=True, exist_ok=True)
    tid = getattr(task, "task_id", None) or "unknown"
    if record is not None:
        counts = _count_trajectory_failure_signals(text) if failure_signals else None
        fm = _render_trajectory_frontmatter(record, failure_signals=counts)
        text = f"{fm}\n\n{text.lstrip()}"
    (round_dir / (filename or f"{tid}.md")).write_text(text, encoding="utf-8")


def write_round_trajectories(
    round_dir: Path,
    task_trajectories: list[tuple[Any, str]],
) -> None:
    """Write per-task trajectories under ``round_dir/<task_id>.md``."""
    for task, text in task_trajectories:
        _write_task_trajectory(round_dir, task, text)


def _compute_round_totals(records: list[dict]) -> dict:
    return {
        "total_tokens": sum(int(r.get("total_tokens") or 0) for r in records),
        "total_steps": sum(int(r.get("steps") or 0) for r in records),
    }


def _pct_delta(new: float, old: float) -> str:
    """Render a percentage change as e.g. '+12%' / '-5%'. '?' when old == 0."""
    if not old:
        return "?"
    return f"{100 * (new - old) / old:+.0f}%"


def _score_and_gate(
    *,
    round_pass_rate: float,
    round_cost: float,
    round_idx: int,
    round_config: Any,
    round_passed: int,
    best: tuple[float, float, Any, int, int] | None,
    tolerance: float,
    cost_weight: float,
    pass_count_noise_threshold: int = 3,
) -> tuple[str, str, tuple[float, float, Any, int, int], Any]:
    """Best-so-far gating kernel.

    Compares this round to the historical best (not the last-accepted round)
    so tolerance cannot drift the baseline downward over many rounds.

    Two guards against noise-driven rollback:

    - ``tolerance`` — relative score drop allowed (pass_rate-based).
    - ``pass_count_noise_threshold`` — absolute passed-task count delta
      below which even a score regression is treated as noise. A rollback
      fires only when BOTH checks fail (score below tolerance AND count
      delta >= threshold). Small task sets (e.g. 6 tasks per round) can
      swing 1-2 passes due to eval stochasticity alone; without this
      guard those would trigger spurious rollbacks.

    Returns ``(decision, reason, new_best, reverted_to_config_or_none)``.

    - ``decision`` ∈ {"ACCEPTED", "REVERTED"}.
    - ``reverted_to_config_or_none`` is ``None`` on ACCEPTED and the
      best-round's config on REVERTED (caller should set ``current_config``
      back to it).
    - ``new_best`` equals ``best`` unless this round strictly beats the best
      score; equal-score rounds do not dethrone the earliest holder.
    """
    score = round_pass_rate  # recomputed below when a baseline exists
    if best is None:
        return (
            "ACCEPTED",
            "first round — no prior to compare against",
            (round_pass_rate, round_cost, round_config, round_idx, round_passed),
            None,
        )
    best_rate, best_cost, best_cfg, best_round, best_passed = best
    cost_delta_ratio = (round_cost - best_cost) / max(best_cost, 1e-3) if best_cost else 0.0
    score = round_pass_rate - cost_weight * max(cost_delta_ratio, 0.0)
    best_score = best_rate  # baseline's own cost-delta against itself is 0
    if score < best_score - tolerance:
        # Score says regress — but first check the absolute count delta.
        # If only 1-2 tasks flipped, that's within eval noise on small
        # task sets and shouldn't wipe out the round's other changes.
        count_delta = abs(round_passed - best_passed)
        if count_delta < pass_count_noise_threshold:
            reason = (
                f"noise-level regression: passed {best_passed}→{round_passed} "
                f"(|Δ|={count_delta} < threshold {pass_count_noise_threshold}); "
                f"score {score:.3f} vs R{best_round} {best_score:.3f} kept despite "
                f"tolerance breach"
            )
            # Don't update best — this round underperformed; next round
            # still gets compared against the same historical high.
            return ("ACCEPTED", reason, best, None)
        reason = (
            f"score {score:.3f} < R{best_round} {best_score:.3f} - "
            f"tolerance {tolerance:.3f} "
            f"(pass_rate {best_rate:.3f}→{round_pass_rate:.3f}; "
            f"passed {best_passed}→{round_passed} |Δ|={count_delta}; "
            f"cost ${best_cost:.2f}→${round_cost:.2f}; "
            f"cost_weight={cost_weight:.2f})"
        )
        return ("REVERTED", reason, best, best_cfg)
    if score > best_score:
        new_best = (
            round_pass_rate,
            round_cost,
            round_config,
            round_idx,
            round_passed,
        )
    else:
        new_best = best
    reason = f"score {score:.3f} ≥ R{best_round} {best_score:.3f} - tolerance {tolerance:.3f}"
    return ("ACCEPTED", reason, new_best, None)


def _compute_tool_stats(result) -> tuple[dict, int]:
    """Return ({tool_name: call_count}, total_error_count) from a HarnessResult."""
    traj = getattr(result, "trajectory", None)
    counts: dict[str, int] = {}
    errors = 0
    if traj and hasattr(traj, "steps"):
        for step in traj.steps:
            for tr in step.observation or []:
                name = getattr(tr, "tool_name", "") or ""
                if name:
                    counts[name] = counts.get(name, 0) + 1
                if getattr(tr, "error", ""):
                    errors += 1
    return counts, errors


def _pick_pivotal_tool(result) -> str:
    """Return the most-used tool name (best-effort)."""
    traj = getattr(result, "trajectory", None)
    counts: dict[str, int] = {}
    if traj and hasattr(traj, "steps"):
        for step in traj.steps:
            for tr in step.observation or []:
                name = getattr(tr, "tool_name", "")
                if name:
                    counts[name] = counts.get(name, 0) + 1
    if not counts:
        return ""
    return sorted(counts.items(), key=lambda kv: -kv[1])[0][0]


def _build_trajectory_text(
    task: GAIATask,
    result: HarnessResult,
    harness_config: Any | None = None,
) -> str:
    """Build detailed human-readable trajectory from HarnessResult.

    Layout (same as the original MetaHarness._dump_trajectory):
      1. Task description
      2. Result (pass/fail, exit reason, final output)
      3. Harness Config (processors, tools)
      4. Diagnostics (budget usage, error rates, top tools)
      5. Execution Steps (tool calls + results per step)
    """
    import hashlib as _hashlib

    task_id = getattr(task, "task_id", "") or _hashlib.sha256(str(task.description)[:100].encode()).hexdigest()[:12]

    # ── Aggregate tool stats from trajectory ────────────────────────────
    traj = getattr(result, "trajectory", None)
    total_tool_calls = 0
    tool_errors = 0
    tool_call_counts: dict[str, int] = {}

    if traj and hasattr(traj, "steps"):
        for step in traj.steps:
            for tr in step.observation or []:
                total_tool_calls += 1
                tname = getattr(tr, "tool_name", "?")
                tool_call_counts[tname] = tool_call_counts.get(tname, 0) + 1
                if getattr(tr, "error", ""):
                    tool_errors += 1

    lines: list[str] = [f"# Trajectory: {task_id}"]

    # ── Section 1: Task ─────────────────────────────────────────────────
    desc = task.description if isinstance(task.description, str) else str(task.description)
    lines.append(f"\n## Task\n\n{desc}")

    # ── Section 2: Result ───────────────────────────────────────────────
    task_end = getattr(result, "task_end", None)
    if task_end:
        lines.append("\n## Result\n")
        lines.append(f"- exit_reason: {getattr(task_end, 'exit_reason', '?')}")
        lines.append(f"- total_steps: {getattr(task_end, 'total_steps', '?')}")
        final_out = getattr(task_end, "final_output", "") or ""
        lines.append(f"- final_output: {final_out}")

    # ── Section 3: Harness Config ───────────────────────────────────────
    if harness_config is not None:
        lines.append("\n## Harness Config\n")
        # ``HarnessConfig.processors`` is a flat ``list[dict]`` (pre- or
        # post-canonicalize — the processor instances live in
        # ``_rt_procs`` after canonicalize). Walk both to render cleanly
        # in either lifecycle.
        proc_parts: list[str] = []
        for entry in getattr(harness_config, "processors", None) or []:
            if isinstance(entry, dict):
                target = entry.get("_target_", "") or ""
                label = target.rsplit("::", 1)[-1] if "::" in target else target.rsplit(".", 1)[-1]
                if label:
                    proc_parts.append(label)
            else:
                group = getattr(entry, "_singleton_group", "")
                order = getattr(entry, "_order", "?")
                label = group or type(entry).__name__
                proc_parts.append(f"{label}({order})")
        for p in getattr(harness_config, "_rt_procs", None) or []:
            group = getattr(p, "_singleton_group", "")
            order = getattr(p, "_order", "?")
            label = group or type(p).__name__
            tag = f"{label}({order})"
            if tag not in proc_parts:
                proc_parts.append(tag)
        if proc_parts:
            lines.append(f"Processors: {', '.join(proc_parts)}")
        registry = getattr(harness_config, "tool_registry", None)
        if registry and hasattr(registry, "list_names"):
            try:
                tool_names = list(registry.list_names())
                lines.append(f"Tools: [{', '.join(sorted(tool_names))}]")
            except Exception:
                pass

    # ── Section 4: Diagnostics ──────────────────────────────────────────
    lines.append("\n## Diagnostics\n")
    total_steps = getattr(result, "total_steps", 0) or 0
    max_steps = getattr(task, "max_steps", 0) or 20
    total_tokens = getattr(result, "total_tokens", 0) or 0
    total_cost = getattr(result, "total_cost_usd", 0) or 0
    max_cost = getattr(task, "max_cost_usd", 0) or 0
    exit_reason = getattr(result, "exit_reason", "?")

    step_pct = f"{100 * total_steps // max_steps}%" if max_steps else "?"
    lines.append(f"- steps: {total_steps}/{max_steps} ({step_pct} budget)")
    lines.append(f"- tokens: {total_tokens}")
    if max_cost:
        cost_pct = f"{100 * total_cost / max_cost:.0f}%"
        lines.append(f"- cost: ${total_cost:.3f}/${max_cost:.2f} ({cost_pct} budget)")
    else:
        lines.append(f"- cost: ${total_cost:.3f}")
    error_rate = f"{100 * tool_errors / total_tool_calls:.0f}%" if total_tool_calls else "0%"
    lines.append(f"- tool_calls: {total_tool_calls}, errors: {tool_errors} (error_rate={error_rate})")
    if tool_call_counts:
        top_tools = sorted(tool_call_counts.items(), key=lambda x: -x[1])[:5]
        lines.append(f"- top_tools: {', '.join(f'{n}({c})' for n, c in top_tools)}")
    lines.append(f"- exit_reason: {exit_reason}")

    # Per-tool error counts (inlined from deleted signals.py)
    _, tool_err = _compute_tool_counts(result)
    err_parts = [f"{n}({c})" for n, c in (tool_err or {}).items() if c]
    lines.append(f"- tool_error_counts: {', '.join(err_parts) if err_parts else '-'}")

    # ── Section 5: Execution Steps ──────────────────────────────────────
    if traj and hasattr(traj, "steps"):
        lines.append("\n---\n")
        lines.append("## Execution Steps\n")
        for step in traj.steps:
            lines.append(f"\n### Step {step.step_id}")

            action = step.action
            if action:
                thinking = getattr(action, "thinking", "") or ""
                raw = getattr(action, "content", None)
                content = raw if isinstance(raw, str) else (str(raw) if raw else "")

                if thinking:
                    lines.append(f"\n#### Thinking\n\n{thinking}")
                if content:
                    lines.append(f"\n#### Response\n\n{content}")

                tool_calls = getattr(action, "tool_calls", None) or ()
                if tool_calls:
                    lines.append("\n#### Tool Calls\n")
                    for tc in tool_calls:
                        input_str = json.dumps(tc.input, ensure_ascii=False)
                        lines.append(f"- **{tc.name}**(`{input_str}`)")

            for tr in step.observation or []:
                tname = getattr(tr, "tool_name", "?")
                error_str = getattr(tr, "error", "") or ""
                result_str = getattr(tr, "result", "") or ""
                if error_str:
                    lines.append(f"  -> {tname}: ERROR: {error_str}")
                else:
                    lines.append(f"  -> {tname}: {result_str}")

    return "\n".join(lines)


if __name__ == "__main__":
    import warnings

    warnings.filterwarnings("ignore", message=".*Event loop is closed.*")

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(main())
    finally:
        loop.run_until_complete(loop.shutdown_asyncgens())
        loop.run_until_complete(loop.shutdown_default_executor())
        gc.collect()
        loop.close()
        gc.collect()
