# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""Round-boundary resume: rebuild engine state from a crashed run directory.

The variant-pool driver settles one round at a time and, at the end of every
settled round, writes ``R<n>/pool_state.json`` (routing / decisions / forked /
retired / idle / ``active_pool_measurements`` / ...). This module reads that
trail back and rebuilds exactly enough state — the pool membership and its
``routed_tasks`` partition, the success ledger (cells + per-round buckets +
``ever_solved``), the global idle counter, each variant's deployed config and
last settled trajectories dir — to let the driver continue from the round
*after* the last settled one. Killing the process at any instant then loses at
most the round that was in flight.

Faithful vs. conservative (SPEC §5 discipline: no network, no ``run.py`` import)
--------------------------------------------------------------------------------
Everything the settled rounds persisted is rebuilt **exactly** by replaying it
in round order:

* ledger cells / per-round buckets / ``ever_solved`` — replayed from each
  round's ``active_pool_measurements`` (paper mode) or selected-candidate
  measurements (legacy mode) through :meth:`SuccessLedger.record`, so the
  routing freeze (``before_round``) and the W21 seesaw baseline are byte-exact;
* routing partition, pool membership, ``idle``, monotone ``next_id`` — read
  straight off the last settled ``pool_state.json`` (ids are never reused, so
  ``next_id`` scans *every* round);
* per-variant deployed config — the run snapshots each active variant's config
  to the stable path ``R<r>/active_pool/<vid>/config.yaml`` every settled round
  it is scored by a fresh rollout, which in paper mode is *every* round for a
  non-target variant; the latest such snapshot is the on-disk truth.

Fields the disk does **not** pin unambiguously are reported, never guessed
("宁可重算,不可猜"):

* ``parent_id`` — ``pool_state.json`` names forked children but not their
  parents. Recovered only when a round has a single FORK decision (unambiguous);
  otherwise ``None`` with a warning. It is not read on the continuation path
  (only fresh, same-round forks read it), so a conservative ``None`` is safe.
* deployed config after a ship in the **final** settled round — an APPLY reuses
  the candidate outcome, so that round writes no fresh active-pool snapshot. The
  latest snapshot is then one ship stale; this is detected and reported, and the
  variant resumes from its last snapshotted config (recomputed forward by the
  next evolve, not guessed).
* router RNG stream position — a fresh :class:`Router` re-seeds. Exact for the
  defaults (``epsilon == 0`` and ``tie_break != 'random'`` never draw); reported
  when a stochastic arm is on.

``slot_dirs`` is always empty in this recipe (nothing populates W23 slot
ownership), so reconstructing it empty is exact, not conservative.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping

from .experiment_lock import LOCK_FILENAME, ExperimentLock
from .ledger import SuccessLedger
from .pool import Variant, VariantPool
from .router import Router

#: Sections whose difference blocks a resume (comparability first, no bypass).
#: ``h0`` / ``models`` / ``dataset`` freeze the experiment family (SPEC §6.3);
#: ``hyperparams`` / ``env`` fix every ablation blank and the RNG seed, all of
#: which change what the continued rounds compute.
_LOCK_BLOCKING_SECTIONS = ("h0", "models", "dataset", "hyperparams", "env")

#: Top-level fields (not sections) that also block. ``provenance_warnings`` is
#: where the frozen-``Hyperparams`` taints live (``--force-gate`` / ``--ship-policy``
#: / ``--step-countdown`` / ... are recorded here, not as fields), so comparing it
#: catches a flag flip that would otherwise slip past the hyperparams diff.
_LOCK_BLOCKING_TOP_FIELDS = ("provenance_warnings",)

#: Lock fields that legitimately differ on a new process and must NOT block a
#: resume: the wall-clock stamp always moves, the id is verified separately, and
#: ``git_sha`` / ``spec_version`` are code/schema versions rather than experiment
#: parameters (a harness change is already caught by ``h0``).
_LOCK_IGNORED_TOP_FIELDS = ("created_at", "experiment_id")

_VARIANT_ID_RE = re.compile(r"^V(\d+)$")


class ResumeError(RuntimeError):
    """Raised when a run directory cannot be resumed.

    Not recoverable by retrying: the run is already finished, has no settled
    round, or its ``pool_state.json`` trail is unreadable / inconsistent.
    """


@dataclass(frozen=True)
class LedgerEvent:
    """One ``(variant, task, round)`` measurement to replay into the ledger."""

    variant_id: str
    task_id: str
    n_pass: int
    n_att: int
    round_idx: int


@dataclass(frozen=True)
class VariantRebuild:
    """Everything needed to recreate one :class:`Variant` at the resume point."""

    variant_id: str
    config_path: Path
    journal_path: Path
    created_round: int
    parent_id: str | None
    routed_tasks: tuple[str, ...]
    #: Where ``config_path`` came from, e.g. ``active_pool_snapshot@R2`` /
    #: ``baseline_v0`` / ``active_pool_snapshot@R1(stale:R2_ship)``.
    config_source: str
    #: Latest settled active-pool trajectories dir, or ``None`` if none exists.
    last_traj_dir: Path | None


@dataclass(frozen=True)
class ResumeState:
    """The reconstructed inputs the driver needs to continue past round ``k``."""

    run_dir: Path
    candidate_mode: str
    settled_rounds: tuple[int, ...]
    last_settled_round: int
    next_round: int
    variants: tuple[VariantRebuild, ...]
    next_id: int
    idle: int
    ledger_events: tuple[LedgerEvent, ...]
    ever_solved: frozenset[str]
    config_change_rounds: Mapping[str, tuple[int, ...]]
    pool_states: tuple[dict, ...]
    warnings: tuple[str, ...] = field(default_factory=tuple)

    def variant(self, variant_id: str) -> VariantRebuild:
        for item in self.variants:
            if item.variant_id == variant_id:
                return item
        raise KeyError(variant_id)


# ---------------------------------------------------------------------------
# CLI planning
# ---------------------------------------------------------------------------


def plan_resume(args: Any, runs_dir: str | Path) -> Path | None:
    """Validate ``--resume`` and resolve the run directory (main-loop glue).

    Returns ``None`` and touches nothing when ``--resume`` was not given, so the
    default CLI path is byte-identical. When it *was* given:

    * ``--resume`` with ``--clean`` is a hard error (they contradict — one wipes
      the dir the other continues);
    * the target may be a run tag (resolved under ``runs_dir``) or a path to a
      run directory; the run tag is **derived** from it (the less error-prone of
      the two options in the SPEC) and written back onto ``args.run_tag``;
    * an explicit ``--run-tag`` that disagrees with the derived tag is a hard
      error rather than a silent mismatch.

    Errors raise :class:`SystemExit` so the CLI aborts with a message, matching
    the surrounding ``main()`` style.
    """
    resume = getattr(args, "resume", None)
    if not resume:
        return None
    if getattr(args, "clean", False):
        raise SystemExit("--resume and --clean are mutually exclusive: one continues a run, the other wipes it")

    resume = str(resume)
    candidate = Path(resume)
    runs_dir = Path(runs_dir)
    if candidate.exists() and candidate.is_dir():
        run_dir = candidate
    elif (runs_dir / resume).exists():
        run_dir = runs_dir / resume
    else:
        raise SystemExit(
            f"--resume target not found: neither {candidate} nor {runs_dir / resume} is an existing run directory"
        )
    run_dir = run_dir.resolve()
    derived_tag = run_dir.name

    explicit_tag = getattr(args, "run_tag", None)
    if explicit_tag and str(explicit_tag) != derived_tag:
        raise SystemExit(
            f"--run-tag {explicit_tag!r} disagrees with the --resume directory tag {derived_tag!r}; "
            "omit --run-tag on resume (it is derived from the run directory) or make them match"
        )
    args.run_tag = derived_tag
    return run_dir


# ---------------------------------------------------------------------------
# loading
# ---------------------------------------------------------------------------


def load_resume_state(run_dir: str | Path, *, candidate_mode: str | None = None) -> ResumeState:
    """Rebuild a :class:`ResumeState` from a settled-round trail on disk.

    ``candidate_mode`` selects which per-round measurement feeds the ledger; it
    defaults to the value recorded in ``experiment.lock.json`` (paper mode when
    the lock is absent), matching the driver's own default.
    """
    run_dir = Path(run_dir)
    if not run_dir.is_dir():
        raise ResumeError(f"resume: {run_dir} is not a directory")

    # A finished run has already written its final report; resuming it is a
    # no-op at best and a double-count at worst.
    if (run_dir / "pool_report.md").exists():
        raise ResumeError(
            f"resume: {run_dir} already holds pool_report.md — the run finished (or early-stopped) "
            "and has nothing to resume. Start a fresh run (new --run-tag) instead."
        )

    settled = _settled_rounds(run_dir)
    if not settled:
        raise ResumeError(
            f"resume: {run_dir} has no settled round (no R0/pool_state.json). Nothing to continue from; "
            "start a fresh run."
        )

    warnings: list[str] = []
    pool_states = tuple(_read_pool_state(run_dir, n) for n in settled)
    last = settled[-1]
    last_state = pool_states[-1]

    resolved_mode = candidate_mode or _lock_candidate_mode(run_dir)

    routing = _require_mapping(last_state, "routing", last)
    variant_ids = sorted(routing, key=_variant_num)
    if not variant_ids:
        raise ResumeError(f"resume: R{last}/pool_state.json has an empty routing partition")

    created_round, parent_id = _lineage(pool_states, variant_ids, warnings)
    next_id = _next_variant_id(pool_states)
    idle = int(last_state.get("idle", 0))
    config_change_rounds = _config_change_rounds(pool_states)

    ledger_events = _ledger_events(pool_states, resolved_mode, warnings)
    ever_solved = frozenset(e.task_id for e in ledger_events if e.n_pass >= 1)

    variants: list[VariantRebuild] = []
    for vid in variant_ids:
        config_path, config_source = _resolve_config(
            run_dir, vid, last, config_change_rounds.get(vid, ()), warnings
        )
        variants.append(
            VariantRebuild(
                variant_id=vid,
                config_path=config_path,
                journal_path=_journal_path(run_dir, vid),
                created_round=created_round[vid],
                parent_id=parent_id[vid],
                routed_tasks=tuple(sorted(_as_str_list(routing[vid]))),
                config_source=config_source,
                last_traj_dir=_latest_traj_dir(run_dir, vid, last),
            )
        )

    return ResumeState(
        run_dir=run_dir,
        candidate_mode=resolved_mode,
        settled_rounds=settled,
        last_settled_round=last,
        next_round=last + 1,
        variants=tuple(variants),
        next_id=next_id,
        idle=idle,
        ledger_events=tuple(ledger_events),
        ever_solved=ever_solved,
        config_change_rounds={k: tuple(v) for k, v in config_change_rounds.items()},
        pool_states=pool_states,
        warnings=tuple(warnings),
    )


# ---------------------------------------------------------------------------
# rebuild primitives (testable without a full recipe)
# ---------------------------------------------------------------------------


def rebuild_pool(state: ResumeState, *, K: int) -> VariantPool:
    """Recreate the :class:`VariantPool` as it stood after the last settled round."""
    pool = VariantPool(K=K)
    for rebuild in sorted(state.variants, key=lambda item: _variant_num(item.variant_id)):
        pool.variants[rebuild.variant_id] = Variant(
            variant_id=rebuild.variant_id,
            config_path=Path(rebuild.config_path),
            journal_path=Path(rebuild.journal_path),
            created_round=rebuild.created_round,
            parent_id=rebuild.parent_id,
            routed_tasks=set(rebuild.routed_tasks),
        )
    pool.next_id = state.next_id
    return pool


def replay_ledger(state: ResumeState, ledger: SuccessLedger) -> SuccessLedger:
    """Fold every settled measurement into ``ledger`` in round order.

    Uses :meth:`SuccessLedger.record` only, so it rebuilds the cells, the
    per-round buckets and ``ever_solved`` without touching the evidence store on
    disk (the append-only ``data/`` trail is left to continue on its own).
    """
    for event in state.ledger_events:
        ledger.record(event.variant_id, event.task_id, event.n_pass, event.n_att, event.round_idx)
    return ledger


# ---------------------------------------------------------------------------
# lock guardrail
# ---------------------------------------------------------------------------


def lock_blocking_diffs(new_lock: ExperimentLock, existing_lock: ExperimentLock) -> list[str]:
    """Every lock difference that must block a resume (comparability first).

    Compares the experiment-defining and run-determinism sections
    (:data:`_LOCK_BLOCKING_SECTIONS`) plus the taint-carrying
    ``provenance_warnings`` field, and returns each mismatch as
    ``"dotted.path: new -> existing"``. ``created_at`` / ``experiment_id`` (they
    legitimately move / are checked separately) and ``git_sha`` / ``spec_version``
    (code and schema versions, not experiment parameters) never block. There is no
    ``--force`` bypass: an inconsistent lock means the continued rounds would not
    be comparable to the ones already on disk.
    """
    blocking: list[str] = []
    for entry in new_lock.diff(existing_lock):
        top = entry.split(":", 1)[0].split(".", 1)[0]
        if top in _LOCK_IGNORED_TOP_FIELDS:
            continue
        if top in _LOCK_BLOCKING_SECTIONS or top in _LOCK_BLOCKING_TOP_FIELDS:
            blocking.append(entry)
    return blocking


def annotate_resume_provenance(
    run_dir: str | Path,
    existing_lock: ExperimentLock,
    *,
    resumed_at_round: int,
    resumed_at: str | None = None,
) -> dict[str, Any]:
    """Append a ``resume_provenance`` entry to ``experiment.lock.json`` in place.

    The original lock fields (the frozen family definition) are left untouched;
    the provenance is added as a top-level ``resume_provenance`` list so repeated
    resumes accumulate. :meth:`ExperimentLock.from_json` does not read this
    top-level key, so a re-loaded lock is byte-for-byte comparable — the anno
    never changes the run's identity or any future guardrail comparison.

    ``resumed_at`` (a caller-supplied timestamp) is optional; omit it to keep the
    entry deterministic. Returns the entry that was appended.
    """
    run_dir = Path(run_dir)
    lock_path = run_dir / LOCK_FILENAME
    data = json.loads(lock_path.read_text(encoding="utf-8"))
    entry: dict[str, Any] = {
        "resumed_at_round": int(resumed_at_round),
        "prior_lock_sha256": existing_lock.sha256(),
    }
    if resumed_at is not None:
        entry["resumed_at"] = str(resumed_at)
    history = data.get("resume_provenance")
    if not isinstance(history, list):
        history = []
    history.append(entry)
    data["resume_provenance"] = history
    lock_path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return entry


# ---------------------------------------------------------------------------
# recipe glue (duck-typed; no import of the recipe module -> no import cycle)
# ---------------------------------------------------------------------------


def apply_resume_state(recipe: Any, state: ResumeState) -> None:
    """Rehydrate a freshly-constructed recipe in place, ready to run round k+1.

    Mutates the recipe's already-configured ``pool`` / ``ledger`` / ``engine`` /
    bookkeeping so :meth:`run(start_round=state.next_round)` continues exactly
    where the killed process stopped. The ledger keeps the recipe's own
    estimator / rollup / cluster configuration (built in ``__init__``); only its
    contents are replayed here.
    """
    # Pool: replace the __init__ V0-only pool with the reconstructed membership.
    recipe.pool.variants.clear()
    for rebuild in sorted(state.variants, key=lambda item: _variant_num(item.variant_id)):
        recipe.pool.variants[rebuild.variant_id] = Variant(
            variant_id=rebuild.variant_id,
            config_path=Path(rebuild.config_path),
            journal_path=Path(rebuild.journal_path),
            created_round=rebuild.created_round,
            parent_id=rebuild.parent_id,
            routed_tasks=set(rebuild.routed_tasks),
        )
    recipe.pool.next_id = state.next_id

    # Ledger: replay every settled measurement in round order.
    replay_ledger(state, recipe.ledger)

    # Engine: restore the global idle counter (patience continues from here).
    recipe.engine._idle = int(state.idle)

    # Trajectories + config-change lineage for the driver's next round.
    last_traj = getattr(recipe, "_last_traj_dir", None)
    if isinstance(last_traj, dict):
        for rebuild in state.variants:
            if rebuild.last_traj_dir is not None:
                last_traj[rebuild.variant_id] = Path(rebuild.last_traj_dir)
    change_rounds = getattr(recipe, "_config_change_rounds", None)
    if isinstance(change_rounds, dict):
        for vid, rounds in state.config_change_rounds.items():
            if rounds:
                change_rounds[vid] = list(rounds)

    # Preload the settled pool-state dicts so the final pool_states.json still
    # carries the pre-resume rounds (the per-round R*/pool_state.json remain the
    # authoritative record for those rounds either way).
    pool_states = getattr(recipe, "pool_states", None)
    if isinstance(pool_states, list):
        pool_states.extend(dict(s) for s in state.pool_states)

    _warn_router_rng(recipe, state)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _settled_rounds(run_dir: Path) -> tuple[int, ...]:
    """Contiguous ``R0..Rk`` that each hold a ``pool_state.json``.

    Enumeration stops at the first gap: a settled round always writes its state
    before the next round begins, so a missing ``R<n>`` means ``n`` never
    settled and everything after it is in-flight / absent.
    """
    settled: list[int] = []
    n = 0
    while (run_dir / f"R{n}" / "pool_state.json").is_file():
        settled.append(n)
        n += 1
    return tuple(settled)


def _read_pool_state(run_dir: Path, n: int) -> dict:
    path = run_dir / f"R{n}" / "pool_state.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ResumeError(f"resume: {path} is unreadable: {exc}") from exc
    if not isinstance(data, dict):
        raise ResumeError(f"resume: {path} must hold a JSON object")
    if int(data.get("round", n)) != n:
        raise ResumeError(
            f"resume: {path} declares round {data.get('round')} but sits in R{n}"
        )
    return data


def _require_mapping(state: dict, key: str, round_idx: int) -> dict:
    value = state.get(key)
    if not isinstance(value, dict):
        raise ResumeError(f"resume: R{round_idx}/pool_state.json is missing a {key!r} object")
    return value


def _variant_num(variant_id: str) -> int:
    match = _VARIANT_ID_RE.match(str(variant_id))
    if match is None:
        raise ResumeError(f"resume: {variant_id!r} is not a VN variant id")
    return int(match.group(1))


def _as_str_list(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    return [str(item) for item in value]


def _lineage(
    pool_states: Iterable[dict],
    variant_ids: Iterable[str],
    warnings: list[str],
) -> tuple[dict[str, int], dict[str, str | None]]:
    """Recover ``created_round`` (exact) and ``parent_id`` (best-effort).

    ``created_round`` is the round a variant first appears in ``forked`` (0 for
    V0). ``parent_id`` is only unambiguous when that round had a single FORK
    decision; with concurrent forks the pairing is not recoverable from disk, so
    it is left ``None`` and reported. Nothing on the continuation path reads a
    historical variant's ``parent_id``, so ``None`` is a safe conservative value.
    """
    states = list(pool_states)
    created: dict[str, int] = {}
    parent: dict[str, str | None] = {}
    for vid in variant_ids:
        parent.setdefault(vid, None)
    for state in states:
        round_idx = int(state.get("round", 0))
        forked = _as_str_list(state.get("forked"))
        if not forked:
            continue
        fork_parents = sorted(
            vid for vid, dec in _require_mapping(state, "decisions", round_idx).items() if dec == "fork"
        )
        for child in forked:
            if child not in created:
                created[child] = round_idx
                if len(fork_parents) == 1:
                    parent[child] = fork_parents[0]
                else:
                    parent[child] = None
                    warnings.append(
                        f"variant {child}: parent_id not recoverable — R{round_idx} had "
                        f"{len(fork_parents)} concurrent FORK decisions; left None (not read on resume)"
                    )
    for vid in variant_ids:
        created.setdefault(vid, 0)
        parent.setdefault(vid, None)
    return created, parent


def _next_variant_id(pool_states: Iterable[dict]) -> int:
    """``next_id`` = one past the highest variant number *ever* seen.

    Ids are monotonic and never reused (retired ones included), so this scans
    every round's routing / decisions / forked / retired / measurement keys, not
    just the survivors at the resume point.
    """
    highest = -1
    for state in pool_states:
        seen: set[str] = set()
        for key in ("routing", "decisions", "per_variant_pass", "active_pool_measurements", "candidate_gate_measurements"):
            value = state.get(key)
            if isinstance(value, dict):
                seen.update(value)
        for key in ("forked", "retired"):
            seen.update(_as_str_list(state.get(key)))
        for vid in seen:
            match = _VARIANT_ID_RE.match(str(vid))
            if match is not None:
                highest = max(highest, int(match.group(1)))
    return highest + 1


def _config_change_rounds(pool_states: Iterable[dict]) -> dict[str, list[int]]:
    """Rounds each variant's deployed config changed (APPLY on it, or its fork)."""
    changes: dict[str, list[int]] = {}
    for state in pool_states:
        round_idx = int(state.get("round", 0))
        decisions = state.get("decisions")
        if isinstance(decisions, dict):
            for vid, dec in decisions.items():
                if dec == "apply":
                    changes.setdefault(str(vid), []).append(round_idx)
        for child in _as_str_list(state.get("forked")):
            changes.setdefault(child, []).append(round_idx)
    for rounds in changes.values():
        rounds.sort()
    return changes


def _ledger_events(
    pool_states: Iterable[dict],
    candidate_mode: str,
    warnings: list[str],
) -> list[LedgerEvent]:
    """The measurements that fed the ledger, in round order, per mode.

    Paper mode (default): the settled active pool is scored on the full task set
    every round and written to ``active_pool_measurements``; that is exactly what
    ``_record_settled_active_outcomes`` folded into the ledger, so replaying it
    is byte-exact. Legacy mode records only the selected candidate — an APPLY on
    its variant, a FORK on the new child — so it is replayed from
    ``per_variant_pass`` keyed by the decision.
    """
    events: list[LedgerEvent] = []
    if candidate_mode == "paper":
        for state in pool_states:
            round_idx = int(state.get("round", 0))
            measurements = state.get("active_pool_measurements")
            if not isinstance(measurements, dict):
                raise ResumeError(
                    f"resume: R{round_idx}/pool_state.json is missing active_pool_measurements "
                    "(required to rebuild the paper-mode ledger)"
                )
            events.extend(_events_from_measurements(measurements, round_idx))
        return events

    # legacy_single: the engine records the selected candidate only.
    for state in pool_states:
        round_idx = int(state.get("round", 0))
        per_variant = state.get("per_variant_pass")
        if not isinstance(per_variant, dict):
            continue
        selected = state.get("selected_candidate_ids")
        selected = selected if isinstance(selected, dict) else {}
        decisions = state.get("decisions")
        decisions = decisions if isinstance(decisions, dict) else {}
        forked = _as_str_list(state.get("forked"))
        fork_parents = sorted(vid for vid, dec in decisions.items() if dec == "fork")
        for vid in sorted(selected):
            decision = decisions.get(vid)
            outcomes = per_variant.get(vid)
            if not isinstance(outcomes, dict):
                continue
            if decision == "apply":
                target = vid
            elif decision == "fork":
                if len(fork_parents) == 1 and len(forked) == 1:
                    target = forked[0]
                else:
                    raise ResumeError(
                        f"resume: legacy-mode R{round_idx} has ambiguous FORK settlement "
                        f"({len(fork_parents)} forks); its ledger cannot be rebuilt unambiguously. "
                        "Resume is faithful for paper mode or single-fork legacy rounds."
                    )
            else:
                continue
            for task_id, outcome in outcomes.items():
                n_pass, n_att = _outcome_pair(outcome)
                events.append(LedgerEvent(target, str(task_id), n_pass, n_att, round_idx))
    if events:
        warnings.append(
            "legacy_single mode: ledger rebuilt from selected-candidate measurements "
            "(APPLY->variant, FORK->child); active-pool scores are not the ledger source in this mode"
        )
    return events


def _events_from_measurements(measurements: Mapping[str, Any], round_idx: int) -> list[LedgerEvent]:
    events: list[LedgerEvent] = []
    for vid in sorted(measurements):
        outcomes = measurements[vid]
        if not isinstance(outcomes, dict):
            continue
        for task_id in sorted(outcomes):
            n_pass, n_att = _outcome_pair(outcomes[task_id])
            events.append(LedgerEvent(str(vid), str(task_id), n_pass, n_att, round_idx))
    return events


def _outcome_pair(outcome: Any) -> tuple[int, int]:
    if not isinstance(outcome, (list, tuple)) or len(outcome) != 2:
        raise ResumeError(f"resume: measurement {outcome!r} is not a [n_pass, n_att] pair")
    return int(outcome[0]), int(outcome[1])


def _journal_path(run_dir: Path, variant_id: str) -> Path:
    """The per-variant novelty journal, by the pool's naming convention.

    V0 is ``run_dir/learnings.md`` (:meth:`VariantPool.add_root`); a fork child
    is ``run_dir/learnings_<vid>.md`` (:meth:`VariantPool.fork`).
    """
    if variant_id == "V0":
        return run_dir / "learnings.md"
    return run_dir / f"learnings_{variant_id}.md"


def _resolve_config(
    run_dir: Path,
    variant_id: str,
    last_round: int,
    change_rounds: tuple[int, ...],
    warnings: list[str],
) -> tuple[Path, str]:
    """The deployed config for ``variant_id`` at the resume point, from disk.

    The run snapshots each variant's deployed config to
    ``R<r>/active_pool/<vid>/config.yaml`` whenever it is scored by a fresh
    rollout; the latest such snapshot (<= ``last_round``) is the on-disk truth.
    A ship in the very last settled round reuses its candidate outcome and writes
    no fresh snapshot, so the snapshot is then one ship stale — detected here and
    reported; the variant resumes from its last snapshotted config. V0 falls back
    to the frozen ``V0/config.yaml`` baseline.
    """
    snap_round = _latest_snapshot_round(run_dir, variant_id, last_round)
    last_change = max(change_rounds) if change_rounds else None

    if snap_round is not None:
        path = run_dir / f"R{snap_round}" / "active_pool" / variant_id / "config.yaml"
        if last_change is not None and last_change > snap_round:
            warnings.append(
                f"variant {variant_id}: shipped in R{last_change} after its last config snapshot "
                f"(R{snap_round}); that round reused the candidate outcome and wrote no fresh snapshot, "
                f"so resume continues from the R{snap_round} config (the R{last_change} ship is recomputed "
                "forward by the next evolve, not lost silently)"
            )
            return path, f"active_pool_snapshot@R{snap_round}(stale:R{last_change}_ship)"
        return path, f"active_pool_snapshot@R{snap_round}"

    # No snapshot ever for this variant (e.g. only ever candidate_reuse/injected).
    baseline = run_dir / "V0" / "config.yaml"
    if variant_id == "V0" and baseline.is_file():
        warnings.append(
            "variant V0: no active-pool config snapshot found; falling back to the frozen V0/config.yaml baseline"
        )
        return baseline, "baseline_v0"
    warnings.append(
        f"variant {variant_id}: no active-pool config snapshot found on disk; "
        "config path points at the (missing) canonical snapshot and must be verified before continuing"
    )
    return run_dir / f"R{last_round}" / "active_pool" / variant_id / "config.yaml", "unresolved_missing_snapshot"


def _latest_snapshot_round(run_dir: Path, variant_id: str, last_round: int) -> int | None:
    for r in range(last_round, -1, -1):
        if (run_dir / f"R{r}" / "active_pool" / variant_id / "config.yaml").is_file():
            return r
    return None


def _latest_traj_dir(run_dir: Path, variant_id: str, last_round: int) -> Path | None:
    for r in range(last_round, -1, -1):
        traj = run_dir / f"R{r}" / "active_pool" / variant_id / "trajectories"
        if traj.is_dir():
            return traj
    return None


def _lock_candidate_mode(run_dir: Path) -> str:
    """The recorded ``candidate_mode`` (paper when no lock / field is present)."""
    lock_path = run_dir / LOCK_FILENAME
    if not lock_path.is_file():
        return "paper"
    try:
        data = json.loads(lock_path.read_text(encoding="utf-8"))
        mode = data.get("hyperparams", {}).get("candidate_mode")
    except (OSError, ValueError, AttributeError):
        return "paper"
    return str(mode) if mode else "paper"


def _warn_router_rng(recipe: Any, state: ResumeState) -> None:
    """Warn when the router's RNG stream position cannot be reconstructed.

    A fresh :class:`Router` re-seeds from ``seed``; the defaults never draw
    (``epsilon == 0`` and ``tie_break != 'random'``), so resume is exact. A
    stochastic arm consumes the RNG each round, and its position after k rounds
    is not on disk, so the continued draws differ from an uninterrupted run.
    """
    router = getattr(recipe, "router", None)
    if not isinstance(router, Router):
        return
    if getattr(router, "epsilon", 0.0) > 0.0 or getattr(router, "tie_break", "") == "random":
        # Recorded on the recipe so the run log / report can surface it.
        note = (
            "router RNG stream position is not persisted; a stochastic routing arm "
            f"(epsilon={router.epsilon}, tie_break={router.tie_break!r}) resumes from a fresh seed, "
            "so post-resume tie-breaks/exploration diverge from an uninterrupted run"
        )
        existing = list(getattr(recipe, "_resume_warnings", []) or [])
        existing.append(note)
        try:
            recipe._resume_warnings = existing
        except Exception:  # noqa: BLE001 - a warning must never break resume
            pass
