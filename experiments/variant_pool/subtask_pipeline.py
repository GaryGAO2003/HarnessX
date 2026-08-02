# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""M1 task-decomposition x variant-division layer (static D1-lite).

Evaluation-only building blocks for the DESIGN-doc B0/B1/B2 arm ladder
(``experiments/docs/DECOMP-DIVISION-DESIGN.md`` §7-8; SPEC §7.17). Everything
here is **pure and dependency-injected**: the module imports no ``recipe`` /
``harnessx`` symbol and touches no network. The three live seams — a session
``runner``, a meta ``complete`` and a task-level ``scorer`` — are passed in, so
the whole pipeline is exercised offline with plain async stubs. The
``recipe.gaia_evolver.run_variant_pool`` driver wires the real seams.

Pieces:

* :class:`SubtaskSpec` / :class:`DecompPlan` + :func:`validate_plan` — the
  HuggingGPT-lite schema (``{id, type, instruction, dep}``), its four-type
  taxonomy, and DAG validation -> serial topological order.
* :class:`LlmDecomposer` / :class:`FileDecomposer` — ``--decomp-source`` (llm
  with one repair retry then fallback; ``file:`` oracle for E0 / replay / tests).
* :class:`SubtaskRouter` — ``--decomp-routing`` (single / round_robin / ledger).
* :class:`TypeCreditLedger` — observational ``(variant x subtask_type)`` credit,
  same Laplace ``(p+1)/(a+2)`` form as the task-level ledger.
* :class:`Synthesizer` / :class:`VerifyGate` / :class:`PipelineExecutor` — the
  per-attempt orchestration.
* :func:`build_pool_profile` — the minimal B3 pool-capability briefing.

The evaluation loop that wires these to real rollouts lives in
``run_variant_pool.py`` (``_run_decomp_eval``); it stays out of this module so
the byte-identical default path never imports a rollout.
"""

from __future__ import annotations

import json
import logging
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Awaitable, Callable, Iterable, Protocol

logger = logging.getLogger(__name__)

# --- taxonomy + defaults (DESIGN §2.1) -------------------------------------
#: The four static subtask types (three systems converged on 4-5; recursion is
#: forbidden to stop over-decomposition cascades — DESIGN §2.1).
SUBTASK_TYPES: frozenset[str] = frozenset({"search", "browse", "compute", "verify"})
ROUTING_MODES: tuple[str, ...] = ("single", "round_robin", "ledger")

#: ``--decomp-credit``. What a ``(variant x type)`` observation is scored on.
#:
#: ``task`` (default) books the WHOLE task's pass/fail against every distinct
#: pair on the chain -- a passing task credits the searcher, the calculator and
#: the verifier equally (4.30 cells per chain on average). The resulting ledger
#: measures participation in successful tasks, not competence at a kind of work,
#: which is a problem because ``ledger`` routing -- the B2 arm, and the thesis's
#: headline B2-B1 contrast -- reads exactly this table to decide who does what.
#:
#: ``subtask_convergence`` books one observation per executed subtask, passing
#: iff that subtask finished inside its own step budget. It scores COMPLETION,
#: not CORRECTNESS: a subtask that stops early with a wrong answer counts as a
#: success. That is a deliberate trade -- free and per-subtask, against coarse --
#: and it has to be declared wherever the arm is reported. GAIA labels only the
#: final answer, so the precise alternatives (a per-subtask judge, or a
#: counterfactual re-run under a different variant) cost several times more.
CREDIT_TASK = "task"
CREDIT_SUBTASK_CONVERGENCE = "subtask_convergence"
CREDIT_MODES: tuple[str, ...] = (CREDIT_TASK, CREDIT_SUBTASK_CONVERGENCE)
DEFAULT_MAX_SUBTASKS = 20
DEFAULT_LEDGER_MIN_OBS = 3

#: Sentinels for the whole-task fallback "subtask" so it never collides with a
#: real (variant x type) credit cell.
WHOLE_TASK_ID = "__whole_task__"
WHOLE_TASK_TYPE = "__whole__"


# --- prompt templates (module constants; static == no profile bytes) --------
DECOMPOSE_PROMPT_TEMPLATE = (
    "You are a task planner. Break the task below into a short list of "
    "self-contained subtasks that together solve it.\n\n"
    "TASK:\n{task}\n\n"
    "Each subtask MUST declare exactly one of these four types:\n"
    "  - search:  find or retrieve facts / sources\n"
    "  - browse:  open a specific source and extract details\n"
    "  - compute: calculate or reason over already-gathered information\n"
    "  - verify:  check or synthesise an intermediate result\n\n"
    "Rules:\n"
    "  - Emit as many subtasks as the task genuinely needs, up to {max_subtasks}. A capability may recur (e.g. search then compute then search again); express the ordering with 'dep'. Do NOT further decompose a subtask.\n"
    "  - Every 'instruction' must be self-contained.\n"
    "  - 'dep' lists the ids of subtasks whose output this one needs "
    "(a DAG; no cycles, no self-reference).\n\n"
    "Return ONLY a JSON array, no prose and no code fences:\n"
    '[{{"id": "s1", "type": "search", "instruction": "...", "dep": []}}]\n'
)
DECOMPOSE_REPAIR_SUFFIX = (
    "\n\nYour previous reply could not be parsed. Return ONLY a JSON array of "
    "subtask objects with keys id, type, instruction and dep — no prose, no "
    "code fences, no trailing commentary."
)
#: Injected only when a pool profile is supplied (B3). Its absence is asserted
#: by the default-path tests: a static decomposition carries none of these bytes.
PROFILE_HEADER = (
    "\nPOOL CAPABILITY PROFILE (bias the decomposition toward these strengths):\n"
)
SYNTHESIS_PROMPT_TEMPLATE = (
    "You are composing the final answer to a GAIA task from its solved "
    "subtasks.\n\n"
    "ORIGINAL TASK:\n{task}\n\n"
    "SUBTASK RESULTS (in execution order):\n{subtasks}\n\n"
    "Using only the information above, give the final answer. End with a line "
    "of exactly the form:\nFINAL ANSWER: <answer>"
)
#: Substring of :data:`SYNTHESIS_PROMPT_TEMPLATE` the guard clause is inserted
#: *before*, so the guard is read ahead of the final-answer contract rather than
#: after it. Asserted at call time: if the template is reworded and this marker
#: disappears, the guard fails closed instead of silently no-opping.
_SYNTH_FINAL_INSTRUCTION = "Using only the information above,"

SYNTH_GUARD_MODES: tuple[str, ...] = ("off", "strict")

#: ``--decomp-synth-guard strict``. The stock template already says "Using only
#: the information above", which is demonstrably too weak: on ``b_smoke`` task
#: ``20194330`` the browse subtask returned nothing and the synthesiser filled
#: the gap from parametric memory ("Based on the known content from the Game
#: Grumps episode ..."). This inflates the DECOMPOSED arm specifically -- a
#: recalled answer that happens to be right scores as a pipeline success -- so
#: it biases the comparison towards this thesis's own hypothesis.
SYNTHESIS_GUARD_CLAUSE = (
    "A subtask result that does not contain what its instruction asked for is "
    "MISSING information, not an invitation to supply it yourself. If any "
    "subtask failed to return what it was asked for, say so explicitly and name "
    "its id. Do not fill the gap from what you already know about the subject: "
    "an answer you recall, rather than one these subtask results establish, is "
    "a fabrication even when it turns out to be correct. If the missing piece "
    "is needed for the final answer, say that it could not be obtained.\n\n"
)

VERIFY_PROMPT_TEMPLATE = (
    "Check whether the RESULT correctly and completely satisfies the "
    "INSTRUCTION.\n\nINSTRUCTION:\n{instruction}\n\nRESULT:\n{output}\n\n"
    "Answer with a single word: YES if it is acceptable, NO if it must be redone."
)
CONTEXT_HEADER = "Context from prerequisite subtasks:\n"


# --- errors ----------------------------------------------------------------
class DecompError(Exception):
    """Base class: any :class:`DecompError` triggers whole-task fallback."""


class DecompValidationError(DecompError):
    """A decomposition violated the schema / DAG rules."""


class DecompParseError(DecompError):
    """The LLM decomposition could not be parsed even after one repair retry."""


class DecompUnavailable(DecompError):
    """An oracle (``file:``) source had no entry for this task."""


# --- schema ----------------------------------------------------------------
@dataclass(frozen=True)
class SubtaskSpec:
    """One node of a decomposition DAG."""

    id: str
    type: str
    instruction: str
    dep: tuple[str, ...] = ()


@dataclass(frozen=True)
class DecompPlan:
    """A validated decomposition: subtasks + a serial topological ``order``."""

    subtasks: tuple[SubtaskSpec, ...]
    order: tuple[str, ...]

    @property
    def by_id(self) -> dict[str, SubtaskSpec]:
        return {s.id: s for s in self.subtasks}

    def ordered(self) -> list[SubtaskSpec]:
        """Subtasks in dependency-respecting execution order."""
        index = self.by_id
        return [index[sid] for sid in self.order]

    def to_records(self) -> list[dict]:
        """Round-trippable JSON records (persistence + cross-arm ``file:`` replay)."""
        return [
            {"id": s.id, "type": s.type, "instruction": s.instruction, "dep": list(s.dep)}
            for s in self.subtasks
        ]


def _topological_order(specs: list[SubtaskSpec]) -> tuple[str, ...]:
    """Kahn's algorithm with declared-order tie-breaks (deterministic).

    Raises :class:`DecompValidationError` when a cycle leaves nodes unscheduled.
    """
    ids = [s.id for s in specs]
    rank = {sid: i for i, sid in enumerate(ids)}
    deps = {s.id: set(s.dep) for s in specs}
    indeg = {sid: len(deps[sid]) for sid in ids}
    remaining = set(ids)
    ready = sorted((sid for sid in ids if indeg[sid] == 0), key=rank.__getitem__)
    order: list[str] = []
    while ready:
        sid = ready.pop(0)
        order.append(sid)
        remaining.discard(sid)
        for other in ids:
            if other in remaining and sid in deps[other]:
                indeg[other] -= 1
                if indeg[other] == 0:
                    ready.append(other)
        ready.sort(key=rank.__getitem__)
    if len(order) != len(ids):
        raise DecompValidationError("dependency cycle among subtasks (not a DAG)")
    return tuple(order)


def validate_plan(records: object, *, max_subtasks: int = DEFAULT_MAX_SUBTASKS) -> DecompPlan:
    """Validate raw decomposition records into a :class:`DecompPlan`.

    Enforces: JSON array, non-empty, ``<= max_subtasks``; each record an object
    with a non-empty ``id`` (unique), a ``type`` in :data:`SUBTASK_TYPES`, a
    non-empty ``instruction``, and a ``dep`` list of declared ids with no
    self-reference (recursion forbidden); the ``dep`` graph must be a DAG.
    """
    if not isinstance(records, list):
        raise DecompValidationError("decomposition must be a JSON array of subtasks")
    if not records:
        raise DecompValidationError("decomposition is empty")
    if len(records) > max_subtasks:
        raise DecompValidationError(
            f"too many subtasks: {len(records)} > --decomp-max-subtasks={max_subtasks}"
        )

    specs: list[SubtaskSpec] = []
    seen: set[str] = set()
    for i, rec in enumerate(records):
        if not isinstance(rec, dict):
            raise DecompValidationError(f"subtask #{i} is not a JSON object")
        sid = rec.get("id")
        if not isinstance(sid, str) or not sid.strip():
            raise DecompValidationError(f"subtask #{i} has an empty/invalid id")
        sid = sid.strip()
        if sid in seen:
            raise DecompValidationError(f"duplicate subtask id: {sid!r}")
        seen.add(sid)
        stype = rec.get("type")
        if stype not in SUBTASK_TYPES:
            raise DecompValidationError(
                f"subtask {sid!r} has invalid type {stype!r}; allowed: {sorted(SUBTASK_TYPES)}"
            )
        instr = rec.get("instruction")
        if not isinstance(instr, str) or not instr.strip():
            raise DecompValidationError(f"subtask {sid!r} has an empty instruction")
        dep_raw = rec.get("dep", [])
        if dep_raw is None:
            dep_raw = []
        if not isinstance(dep_raw, list) or not all(isinstance(d, str) for d in dep_raw):
            raise DecompValidationError(f"subtask {sid!r} has a malformed dep list")
        specs.append(
            SubtaskSpec(
                id=sid,
                type=stype,
                instruction=instr.strip(),
                dep=tuple(d.strip() for d in dep_raw),
            )
        )

    all_ids = {s.id for s in specs}
    for s in specs:
        for d in s.dep:
            if d == s.id:
                raise DecompValidationError(
                    f"subtask {s.id!r} depends on itself (recursion forbidden)"
                )
            if d not in all_ids:
                raise DecompValidationError(f"subtask {s.id!r} depends on unknown id {d!r}")

    return DecompPlan(subtasks=tuple(specs), order=_topological_order(specs))


# --- decomposition sources -------------------------------------------------
CompleteFn = Callable[[str], Awaitable[str]]


def _extract_json(text: str) -> object:
    """Best-effort strict-first JSON parse: fences stripped, else array bounds."""
    s = (text or "").strip()
    if s.startswith("```"):
        s = s.strip("`").strip()
        if s[:4].lower() == "json":
            s = s[4:].strip()
    try:
        return json.loads(s)
    except (ValueError, TypeError):
        pass
    start, end = s.find("["), s.rfind("]")
    if start != -1 and end != -1 and end > start:
        return json.loads(s[start : end + 1])
    raise ValueError("no JSON array found in decomposition response")


def _records_of(obj: object) -> object:
    """Accept a bare array or an object wrapping ``{"subtasks": [...]}``."""
    if isinstance(obj, dict) and "subtasks" in obj:
        return obj["subtasks"]
    return obj


class Decomposer:
    """Interface: ``decompose`` returns a plan or raises :class:`DecompError`."""

    async def decompose(
        self, task_id: str, task_text: str, *, profile: str | None = None
    ) -> DecompPlan:  # pragma: no cover - abstract
        raise NotImplementedError


class LlmDecomposer(Decomposer):
    """Static D1-lite decomposition via one meta-model call (repair once).

    ``profile_text`` (or a per-call ``profile``) injects the B3 pool briefing;
    when both are ``None`` the prompt carries no :data:`PROFILE_HEADER` bytes.
    ``calls`` is exposed so tests can assert 1 call on success / 2 on repair.
    """

    def __init__(
        self,
        complete: CompleteFn,
        *,
        max_subtasks: int = DEFAULT_MAX_SUBTASKS,
        profile_text: str | None = None,
    ) -> None:
        self._complete = complete
        self.max_subtasks = int(max_subtasks)
        self.profile_text = profile_text
        self.calls = 0

    def build_prompt(self, task_text: str, profile: str | None = None) -> str:
        prof = profile if profile is not None else self.profile_text
        body = DECOMPOSE_PROMPT_TEMPLATE.format(task=task_text, max_subtasks=self.max_subtasks)
        if prof:
            body = body + PROFILE_HEADER + prof.strip() + "\n"
        return body

    def _parse(self, response: str) -> DecompPlan:
        return validate_plan(_records_of(_extract_json(response)), max_subtasks=self.max_subtasks)

    async def decompose(
        self, task_id: str, task_text: str, *, profile: str | None = None
    ) -> DecompPlan:
        prompt = self.build_prompt(task_text, profile)
        self.calls += 1
        try:
            return self._parse(await self._complete(prompt))
        except (ValueError, DecompValidationError):
            self.calls += 1  # one repair retry
            try:
                return self._parse(await self._complete(prompt + DECOMPOSE_REPAIR_SUFFIX))
            except (ValueError, DecompValidationError) as exc:
                raise DecompParseError(
                    f"decomposition parse failed after repair: {exc}"
                ) from exc


class FileDecomposer(Decomposer):
    """Oracle source: a JSON object keyed by ``task_id`` (E0 / replay / tests)."""

    def __init__(self, mapping: dict, *, max_subtasks: int = DEFAULT_MAX_SUBTASKS) -> None:
        self._mapping = dict(mapping)
        self.max_subtasks = int(max_subtasks)

    @classmethod
    def from_path(cls, path: str | Path, *, max_subtasks: int = DEFAULT_MAX_SUBTASKS) -> "FileDecomposer":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise DecompValidationError(
                "oracle decomposition file must be a JSON object keyed by task_id"
            )
        return cls(data, max_subtasks=max_subtasks)

    async def decompose(
        self, task_id: str, task_text: str, *, profile: str | None = None
    ) -> DecompPlan:
        if task_id not in self._mapping:
            raise DecompUnavailable(f"no oracle decomposition for task_id {task_id!r}")
        return validate_plan(_records_of(self._mapping[task_id]), max_subtasks=self.max_subtasks)


# --- routing ---------------------------------------------------------------
class SubtaskRouter:
    """Assign each subtask to a variant (``--decomp-routing``).

    * ``single``      — always the task-level routed variant.
    * ``round_robin`` — deterministic rotation over the whole pool keyed by
      ``(task_id, attempt, subtask_index)`` (no RNG).
    * ``ledger``      — argmax observational ``(variant x type)`` Laplace rate;
      the winning cell falls back to the task-level choice when it holds fewer
      than ``min_obs`` observations (cold-start). Ties break on variant order.
    """

    def __init__(
        self,
        mode: str,
        *,
        variant_ids: Iterable[str],
        ledger: "TypeCreditLedger | None" = None,
        min_obs: int = DEFAULT_LEDGER_MIN_OBS,
    ) -> None:
        if mode not in ROUTING_MODES:
            raise ValueError(f"routing mode must be one of {ROUTING_MODES}, got {mode!r}")
        self.mode = mode
        self.variant_ids = tuple(variant_ids)
        if not self.variant_ids:
            raise ValueError("SubtaskRouter needs at least one variant")
        self.ledger = ledger
        self.min_obs = int(min_obs)

    def route(
        self,
        *,
        task_id: str,
        attempt: int,
        subtask_index: int,
        subtask_type: str,
        task_level_choice: str,
    ) -> str:
        if self.mode == "single":
            return task_level_choice
        if self.mode == "round_robin":
            offset = zlib.crc32(task_id.encode("utf-8"))
            return self.variant_ids[(offset + int(attempt) + int(subtask_index)) % len(self.variant_ids)]
        # ledger
        if self.ledger is None:
            raise ValueError("ledger routing requires a TypeCreditLedger")
        best_v = self.variant_ids[0]
        best_rate = -1.0
        for vid in self.variant_ids:  # sorted order -> strict '>' keeps lowest id on ties
            rate = self.ledger.rate(vid, subtask_type)
            if rate > best_rate:
                best_rate, best_v = rate, vid
        if self.ledger.attempts(best_v, subtask_type) < self.min_obs:
            return task_level_choice  # cold cell -> task-level fallback
        return best_v


# --- observational (variant x subtask_type) credit -------------------------
class TypeCreditLedger:
    """Accumulate task-level pass/fail into every ``(variant, type)`` cell used.

    Laplace-smoothed rate ``(passes + 1) / (attempts + 2)`` — identical in form
    to the task-level :class:`~experiments.variant_pool.ledger.SuccessLedger`
    (DESIGN §2.1). Purely observational: no extra rollouts, no RNG.
    """

    def __init__(self) -> None:
        self._cells: dict[tuple[str, str], list[int]] = {}

    def record(self, *, pairs: Iterable[tuple[str, str]], passed: bool) -> None:
        """Fold one attempt's outcome into each distinct ``(variant, type)`` used."""
        for key in {(v, t) for v, t in pairs}:
            cell = self._cells.setdefault(key, [0, 0])
            cell[1] += 1
            if passed:
                cell[0] += 1

    def passes(self, variant_id: str, subtask_type: str) -> int:
        return self._cells.get((variant_id, subtask_type), [0, 0])[0]

    def attempts(self, variant_id: str, subtask_type: str) -> int:
        return self._cells.get((variant_id, subtask_type), [0, 0])[1]

    def rate(self, variant_id: str, subtask_type: str) -> float:
        p, a = self._cells.get((variant_id, subtask_type), [0, 0])
        return (p + 1) / (a + 2)

    def matrix(self) -> dict[str, dict]:
        """Serialisable ``(variant x type)`` matrix for the run summary."""
        return {
            f"{v}::{t}": {
                "variant": v,
                "type": t,
                "passes": p,
                "attempts": a,
                "rate": (p + 1) / (a + 2),
            }
            for (v, t), (p, a) in sorted(self._cells.items())
        }


# --- execution seams -------------------------------------------------------
@dataclass
class SessionResult:
    """What a subtask session returns (the injected ``runner`` contract)."""

    output: str
    steps: int = 0
    cost_usd: float = 0.0


class SessionRunner(Protocol):
    async def __call__(
        self,
        *,
        instruction: str,
        variant_id: str,
        max_steps: int,
        subtask_id: str,
        subtask_type: str,
    ) -> SessionResult:  # pragma: no cover - typing only
        ...


ScoreFn = Callable[[str, str], Awaitable[bool]]


class Synthesizer:
    """Variant-agnostic aggregation: one meta call over ordered subtask outputs.

    ``guard`` (``--decomp-synth-guard``, default ``off``) controls whether the
    prompt forbids filling a missing subtask result from parametric memory. The
    default emits the stock prompt byte-for-byte, so an ``off`` run is
    indistinguishable from a pre-flag one.
    """

    def __init__(self, complete: CompleteFn, *, guard: str = "off") -> None:
        if guard not in SYNTH_GUARD_MODES:
            raise ValueError(f"guard must be one of {SYNTH_GUARD_MODES}, got {guard!r}")
        self._complete = complete
        self.guard = guard

    def build_prompt(self, task_text: str, subtask_outputs) -> str:
        parts = [
            f"### {spec.id} ({spec.type})\nInstruction: {spec.instruction}\nResult: {out}"
            for spec, out in subtask_outputs
        ]
        prompt = SYNTHESIS_PROMPT_TEMPLATE.format(task=task_text, subtasks="\n\n".join(parts))
        if self.guard == "off":
            return prompt
        # Fail closed rather than silently no-op if the template is reworded:
        # a guard that quietly stops applying would leave the lock claiming a
        # protection the run never had.
        if _SYNTH_FINAL_INSTRUCTION not in prompt:
            raise RuntimeError(
                "synth guard cannot be placed: SYNTHESIS_PROMPT_TEMPLATE no longer "
                f"contains {_SYNTH_FINAL_INSTRUCTION!r}"
            )
        return prompt.replace(
            _SYNTH_FINAL_INSTRUCTION,
            SYNTHESIS_GUARD_CLAUSE + _SYNTH_FINAL_INSTRUCTION,
            1,
        )

    async def synthesize(
        self, *, task_text: str, subtask_outputs: list[tuple[SubtaskSpec, str]]
    ) -> str:
        return await self._complete(self.build_prompt(task_text, subtask_outputs))


class VerifyGate:
    """Light flag-gated intermediate check (``--decomp-verify-gate on``)."""

    _FAIL = {"no", "fail", "invalid", "incorrect", "false", "wrong"}

    def __init__(self, complete: CompleteFn) -> None:
        self._complete = complete

    async def verify(self, *, subtask: SubtaskSpec, output: str) -> bool:
        verdict = (await self._complete(
            VERIFY_PROMPT_TEMPLATE.format(instruction=subtask.instruction, output=output)
        ) or "").strip().lower()
        first = verdict.split(maxsplit=1)[0].strip(".,!:;") if verdict else ""
        return first not in self._FAIL  # lenient: only an explicit negative retries


# --- per-attempt records ---------------------------------------------------
@dataclass
class SubtaskRecord:
    id: str
    type: str
    variant_id: str
    steps: int
    cost_usd: float
    retried: bool = False


@dataclass
class AttemptResult:
    task_id: str
    attempt: int
    passed: bool
    final_output: str
    fallback: bool
    fallback_reason: str | None
    cost_usd: float
    plan: DecompPlan | None
    subtasks: list[SubtaskRecord] = field(default_factory=list)
    used_pairs: tuple[tuple[str, str], ...] = ()

    def to_json(self) -> dict:
        return {
            "task_id": self.task_id,
            "attempt": self.attempt,
            "passed": self.passed,
            "fallback": self.fallback,
            "fallback_reason": self.fallback_reason,
            "final_output": self.final_output,
            "cost_usd": round(self.cost_usd, 6),
            "decomposition": self.plan.to_records() if self.plan is not None else None,
            "subtasks": [
                {
                    "id": r.id,
                    "type": r.type,
                    "variant_id": r.variant_id,
                    "steps": r.steps,
                    "cost_usd": round(r.cost_usd, 6),
                    "retried": r.retried,
                }
                for r in self.subtasks
            ],
        }


def _fallback_reason(exc: DecompError) -> str:
    if isinstance(exc, DecompParseError):
        return "parse_failure"
    if isinstance(exc, DecompUnavailable):
        return "oracle_missing"
    if isinstance(exc, DecompValidationError):
        return "validation_failure"
    return "decomp_error"


class PipelineExecutor:
    """Run one decomposition attempt: decompose -> DAG -> synthesise -> score.

    Serial DAG execution; every seam (``runner``/``scorer``/``synthesizer``/
    ``verify_gate``) is injected. A :class:`DecompError` degrades to a single
    whole-task rollout (fallback): synthesis is skipped and no credit is booked.
    """

    def __init__(
        self,
        *,
        runner: SessionRunner,
        scorer: ScoreFn,
        decomposer: Decomposer,
        router: SubtaskRouter,
        synthesizer: Synthesizer,
        credit_ledger: TypeCreditLedger | None = None,
        credit_mode: str = CREDIT_TASK,
        verify_gate: VerifyGate | None = None,
        subtask_max_steps: int,
    ) -> None:
        if credit_mode not in CREDIT_MODES:
            raise ValueError(f"credit_mode must be one of {CREDIT_MODES}, got {credit_mode!r}")
        self.credit_mode = credit_mode
        self.runner = runner
        self.scorer = scorer
        self.decomposer = decomposer
        self.router = router
        self.synthesizer = synthesizer
        self.credit_ledger = credit_ledger
        self.verify_gate = verify_gate
        self.subtask_max_steps = int(subtask_max_steps)

    def _subtask_prompt(self, spec: SubtaskSpec, outputs: dict[str, str]) -> str:
        ctx = [f"[Result of {d}]\n{outputs[d]}" for d in spec.dep if d in outputs]
        if not ctx:
            return spec.instruction
        return spec.instruction + "\n\n" + CONTEXT_HEADER + "\n\n".join(ctx)

    async def _fallback(
        self,
        *,
        task_id: str,
        task_text: str,
        ground_truth: str,
        attempt: int,
        task_level_choice: str,
        reason: str,
    ) -> AttemptResult:
        res = await self.runner(
            instruction=task_text,
            variant_id=task_level_choice,
            max_steps=self.subtask_max_steps,
            subtask_id=WHOLE_TASK_ID,
            subtask_type=WHOLE_TASK_TYPE,
        )
        passed = await self.scorer(res.output, ground_truth)
        # No synthesis, and no (variant x type) credit — the whole task carries
        # no meaningful subtype cell.
        rec = SubtaskRecord(
            id=WHOLE_TASK_ID,
            type=WHOLE_TASK_TYPE,
            variant_id=task_level_choice,
            steps=res.steps,
            cost_usd=res.cost_usd,
        )
        return AttemptResult(
            task_id=task_id,
            attempt=attempt,
            passed=passed,
            final_output=res.output,
            fallback=True,
            fallback_reason=reason,
            cost_usd=res.cost_usd,
            plan=None,
            subtasks=[rec],
            used_pairs=(),
        )

    async def run_attempt(
        self,
        *,
        task_id: str,
        task_text: str,
        ground_truth: str,
        attempt: int,
        task_level_choice: str,
        profile: str | None = None,
    ) -> AttemptResult:
        try:
            plan = await self.decomposer.decompose(task_id, task_text, profile=profile)
        except DecompError as exc:
            return await self._fallback(
                task_id=task_id,
                task_text=task_text,
                ground_truth=ground_truth,
                attempt=attempt,
                task_level_choice=task_level_choice,
                reason=_fallback_reason(exc),
            )

        outputs: dict[str, str] = {}
        records: list[SubtaskRecord] = []
        used: list[tuple[str, str]] = []
        used_set: set[tuple[str, str]] = set()

        for position, spec in enumerate(plan.ordered()):
            variant = self.router.route(
                task_id=task_id,
                attempt=attempt,
                subtask_index=position,
                subtask_type=spec.type,
                task_level_choice=task_level_choice,
            )
            prompt = self._subtask_prompt(spec, outputs)
            res = await self.runner(
                instruction=prompt,
                variant_id=variant,
                max_steps=self.subtask_max_steps,
                subtask_id=spec.id,
                subtask_type=spec.type,
            )
            retried = False
            if self.verify_gate is not None and spec.type != "verify":
                if not await self.verify_gate.verify(subtask=spec, output=res.output):
                    retried = True  # at most one retry
                    res = await self.runner(
                        instruction=prompt,
                        variant_id=variant,
                        max_steps=self.subtask_max_steps,
                        subtask_id=spec.id,
                        subtask_type=spec.type,
                    )
            outputs[spec.id] = res.output
            records.append(
                SubtaskRecord(
                    id=spec.id,
                    type=spec.type,
                    variant_id=variant,
                    steps=res.steps,
                    cost_usd=res.cost_usd,
                    retried=retried,
                )
            )
            pair = (variant, spec.type)
            if pair not in used_set:
                used_set.add(pair)
                used.append(pair)

        ordered_outputs = [(spec, outputs[spec.id]) for spec in plan.ordered()]
        final_output = await self.synthesizer.synthesize(
            task_text=task_text, subtask_outputs=ordered_outputs
        )
        passed = await self.scorer(final_output, ground_truth)
        if self.credit_ledger is not None:
            if self.credit_mode == CREDIT_TASK:
                # Every (variant, type) on the chain takes the whole task's
                # outcome, so a pass credits the searcher, the calculator and
                # the verifier alike. The ledger then measures "took part in
                # tasks that passed", not "is good at this kind of work".
                self.credit_ledger.record(pairs=used, passed=passed)
            else:
                # One observation per executed subtask, scored on whether that
                # subtask finished inside its own step budget. Free, objective,
                # binary, and -- unlike the task outcome -- not shared with the
                # rest of the chain. A pair occurring twice books twice, because
                # under this rule the two runs are two measurements.
                # LIMIT, and it must be declared: this scores completion, not
                # correctness. A subtask that stops early with a wrong answer
                # counts as a success here.
                for rec in records:
                    self.credit_ledger.record(
                        pairs=[(rec.variant_id, rec.type)],
                        passed=rec.steps < self.subtask_max_steps,
                    )

        return AttemptResult(
            task_id=task_id,
            attempt=attempt,
            passed=passed,
            final_output=final_output,
            fallback=False,
            fallback_reason=None,
            cost_usd=sum(r.cost_usd for r in records),
            plan=plan,
            subtasks=records,
            used_pairs=tuple(used),
        )


# --- B3 pool-capability profile (minimal) ----------------------------------
def _variant_num(vid: str) -> int:
    tail = vid[1:] if vid[:1].upper() == "V" else vid
    return int(tail) if tail.isdigit() else 0


def _round_num(name: str) -> int:
    tail = name[1:] if name[:1].upper() == "R" else name
    return int(tail) if tail.isdigit() else -1


def _latest_pool_state(run_dir: Path) -> dict:
    states = sorted(run_dir.glob("R*/pool_state.json"), key=lambda p: _round_num(p.parent.name))
    if not states:
        raise ValueError(f"pool profile: no R*/pool_state.json under {run_dir}")
    return json.loads(states[-1].read_text(encoding="utf-8"))


def build_pool_profile(run_dir: str | Path) -> str:
    """Compact per-variant briefing from a run's frozen ``pool_state`` (B3).

    Minimal by design (DESIGN §7: B3 is a two-phase flag arm): parse the last
    settled ``pool_state.json`` routing partition into a per-variant workload
    line. Must come from **frozen S1 statistics**, never an in-arm ledger.
    """
    run_dir = Path(run_dir)
    state = _latest_pool_state(run_dir)
    routing = state.get("routing")
    if not isinstance(routing, dict) or not routing:
        raise ValueError(f"pool profile: no routing partition in {run_dir}")
    lines = ["Pool capability profile (frozen statistics):"]
    for vid in sorted(routing, key=_variant_num):
        tasks = routing.get(vid) or []
        n = len(tasks) if isinstance(tasks, list) else 0
        lines.append(f"- {vid}: carries {n} routed task(s)")
    return "\n".join(lines)
