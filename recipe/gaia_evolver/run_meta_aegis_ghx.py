#!/usr/bin/env python3
# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""GHX ladder launcher — the vendored AEGIS pilot plus the graph-hardening overlays.

This is the single launchable entry that connects the GHX modules (G1 evidence,
G2 gate, and the core U/identity flags) to the vendored GAIA-AEGIS pilot.  It adds
nothing to the pilot's science: it reuses the vendored
:func:`recipe.gaia_evolver.run_meta_aegis.run_pilot` verbatim and only injects the
overlays at the one seam the design names — where the round loop's
``orchestrator.run_round`` fires.

The ladder (``--ghx-level``):

* **L0** — every flag off.  The launcher applies no environment and delegates to the
  vendored ``run_pilot`` unchanged, so L0 through here is indistinguishable from
  running ``run_meta_aegis`` directly.
* **L1** — ``+HARNESSX_GHX_UNFOLD`` (record the unfolded graph U per run) and
  ``+HARNESSX_GHX_IDENTITY`` (record the three run-identity hashes).  Both are
  call-time env reads inside the core, so L1 is *still* the vendored pilot: no round
  wiring, just U/identity files appearing next to the trajectories.
* **L2** — ``+HARNESSX_GHX_AEGIS_EVIDENCE``.  Now the launcher wires the round through
  :func:`~harnessx.ghx.overlay.run_round_with_graph_evidence`, materialising causal
  cones + cross-task facts for the round's *failing* tasks before Stage P dispatches
  the Digester.
* **L3** — currently identical to L2.  The cross-task ``facts.md`` is written by L2's
  materialiser, so the "facts" step is not yet a separate switch; the level exists so
  the ladder can split it later without renumbering.
* **L4** — ``+HARNESSX_GHX_GRAPH_GATE``.  Additionally wraps the round with
  :func:`~harnessx.ghx.graph_gate.run_round_with_graph_gate`, the sixth (graph-
  existence) gate.

Precedence.  ``--ghx-level`` is a convenience that sets each flag with
``setdefault`` — an environment variable the user exported already wins.  So
``HARNESSX_GHX_AEGIS_EVIDENCE=0 ... --ghx-level 2`` leaves evidence OFF, and
``HARNESSX_GHX_GRAPH_GATE=1 ... --ghx-level 1`` leaves the gate ON.  The actual
overlay behaviour is driven by the flags (each wrapper re-reads its own flag at call
time), never by the level number directly, so "explicit env wins" holds end to end.
``HARNESSX_GHX_RUNTIME`` (graph dispatch) is deliberately **not** on this ladder — the
evidence and gate overlays do not require runtime graph dispatch.

Reuse shape (reported for the record).  The vendored ``run_pilot`` is monolithic and
never calls ``orchestrator.run_round`` itself — it calls ``AegisAgent.evolve``, which
constructs the :class:`~harnessx.aegis.orchestrator.AegisOrchestrator` internally and
calls ``run_round`` two levels down.  There is therefore no ``run_round`` call site in
the pilot to wrap, and the orchestrator is byte-identity vendored code we may not edit.
So the launcher installs the overlay at that buried seam with a runtime monkeypatch of
``AegisOrchestrator.run_round`` — the exact pattern the vendored-integrity-safe gate
wrapper already uses on ``run_stage_4`` — restored in a ``finally``.  This keeps 100%
of the vendored pilot (rollouts, focus sets, rollback, curves, resume) running
unchanged; only the single round call is routed through the overlays.  No vendored
file's bytes change, so ``tests/ghx/test_vendored_integrity.py`` stays green.

Usage::

    python recipe/gaia_evolver/run_meta_aegis_ghx.py --ghx-level 2 \\
        --smoke --run-tag ghx_smoke_l2
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import gc
import os
import re
import sys
from pathlib import Path

_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

# The vendored pilot is reused wholesale; importing it also runs its .env bootstrap
# and logging setup, which we want to share verbatim.
import recipe.gaia_evolver.run_meta_aegis as _pilot  # noqa: E402
from harnessx.aegis.orchestrator import AegisOrchestrator  # noqa: E402
from harnessx.ghx.evidence_files import core_layout_resolver  # noqa: E402
from harnessx.ghx.graph_gate import run_round_with_graph_gate  # noqa: E402
from harnessx.ghx.overlay import run_round_with_graph_evidence  # noqa: E402

# ─── Level → flag table ───────────────────────────────────────────────────────
#
# Each level is the CUMULATIVE set of core/overlay flags it turns on.  RUNTIME is
# intentionally absent (see the module docstring).  L2 == L3 today (one materialiser
# writes both cones and facts); the split is deferred, not forgotten.
_UNFOLD = "HARNESSX_GHX_UNFOLD"
_IDENTITY = "HARNESSX_GHX_IDENTITY"
_EVIDENCE = "HARNESSX_GHX_AEGIS_EVIDENCE"
_GATE = "HARNESSX_GHX_GRAPH_GATE"

LEVEL_FLAGS: dict[int, tuple[str, ...]] = {
    0: (),
    1: (_UNFOLD, _IDENTITY),
    2: (_UNFOLD, _IDENTITY, _EVIDENCE),
    3: (_UNFOLD, _IDENTITY, _EVIDENCE),
    4: (_UNFOLD, _IDENTITY, _EVIDENCE, _GATE),
}
MAX_LEVEL = max(LEVEL_FLAGS)


def apply_level_flags(level: int, environ: dict | None = None) -> tuple[str, ...]:
    """Set the level's flags in ``environ`` (default ``os.environ``) via ``setdefault``.

    ``setdefault`` is the whole precedence contract: a flag the user exported already
    (to any value, including ``"0"``) is left untouched, so an explicit environment
    variable always beats the level.  Returns the level's flag tuple for logging/tests.
    """
    if level not in LEVEL_FLAGS:
        raise ValueError(f"unknown ghx level {level!r}; expected 0..{MAX_LEVEL}")
    env = os.environ if environ is None else environ
    flags = LEVEL_FLAGS[level]
    for flag in flags:
        env.setdefault(flag, "1")
    return flags


# ─── Resolvers ────────────────────────────────────────────────────────────────

_ROUND_RE = re.compile(r"R(\d+)")


def _round_from_label(label: str | None) -> int | None:
    """Parse the round index out of a rollout ``label`` (``"aegis/R0"``, ``"aegis/R2/r1"``)."""
    if not label:
        return None
    m = _ROUND_RE.search(label)
    return int(m.group(1)) if m else None


def _make_evidence_resolver(base_dir, session_run_by_task: dict):
    """A task→U resolver for GAIA's per-(round, task) session layout.

    ``session_run_by_task`` maps ``task_id -> (session_id, run_id)`` captured at the
    rollout call site (see :func:`_ghx_round_wiring`).  GAIA writes one U per rollout
    under ``session_id = f"{label}-{task_id}"`` (``run_meta._run_task`), so a single
    fixed session does not name every task's U —
    :func:`~harnessx.ghx.evidence_files.core_layout_resolver` assumes exactly one.  We
    therefore delegate to ``core_layout_resolver`` **per task**, handing it that task's
    own session_id, which is the same primitive (``unfolded_path`` + ``load_unfolded``)
    the fixed-session resolver is built from.  A task with no captured run id, or whose
    U file is absent, resolves to ``None`` (unavailable), never an empty graph.
    """

    def resolve(task_id: str):
        entry = session_run_by_task.get(task_id)
        if not entry:
            return None
        session_id, run_id = entry
        return core_layout_resolver(base_dir, session_id, {task_id: run_id})(task_id)

    return resolve


def _gate_u_resolver(candidate_id: str):
    """The honest gate resolver: no REAL replay U exists yet, so always ``None``.

    The vendored Stage-4 replay boots each candidate config against a trivial synthetic
    task, not a real GAIA task under ``HARNESSX_GHX_UNFOLD`` — so there is no U in which
    a candidate's newly-added ``tool:<name>`` would actually fire.  Two tempting shortcuts
    are both lies and are refused here:

    * the **parent round's U** — the candidate's new tool cannot appear in a run that
      predates the candidate, so checking against it would refuse every real tool add;
    * the **synthetic-smoke U** — the added tool is never exercised there either.

    Returning ``None`` makes :func:`~harnessx.ghx.graph_gate.check_graph_gate` take the
    pass-through path (``checked=False``, ``ok=True``) and record *why* it could not
    check.  The gate goes live the day a future module makes Stage-4 replay run a real
    task under ``HARNESSX_GHX_UNFOLD`` and hands that replay's U back here per candidate;
    until then this seam is wired but honestly inert.
    """
    return None


# ─── Round-seam composition ───────────────────────────────────────────────────


class _GateProxy:
    """Make the evidence overlay's inner ``run_round`` *be* the graph-gate overlay.

    The evidence wrapper reads ``orchestrator.run_dir`` and then calls
    ``orchestrator.run_round(**kwargs)``.  Handing it this proxy composes the two named
    wrappers without either duplicating the other: the evidence wrapper materialises its
    cones/facts, then its ``run_round`` call lands in the gate wrapper, whose own
    ``run_round`` call lands on the real orchestrator.
    """

    def __init__(self, orch, gate_u_resolver, parent_config_path) -> None:
        self._orch = orch
        self.run_dir = orch.run_dir
        self._gate_u_resolver = gate_u_resolver
        self._parent_config_path = parent_config_path

    async def run_round(self, **kwargs):
        return await run_round_with_graph_gate(
            self._orch,
            u_resolver=self._gate_u_resolver,
            parent_config_path=self._parent_config_path,
            gate_enabled=None,  # re-reads HARNESSX_GHX_GRAPH_GATE
            **kwargs,
        )


async def _ghx_run_round(
    orch,
    *,
    failed_task_ids,
    evidence_resolver,
    gate_u_resolver,
    parent_config_path,
    **run_round_kwargs,
):
    """Route one round through the GHX overlays, flag-driven.

    Both wrappers re-read their own env flag at call time and are pure pass-throughs
    when off, so this one code path covers every level:

    * evidence off, gate off  → plain ``orch.run_round`` (L0/L1);
    * evidence on,  gate off  → cones/facts materialised, then ``orch.run_round`` (L2/L3);
    * evidence off, gate on    → gate-wrapped ``orch.run_round``;
    * evidence on,  gate on    → cones/facts, then gate-wrapped ``orch.run_round`` (L4).

    ``failed_task_ids``/``evidence_resolver`` feed the evidence overlay;
    ``gate_u_resolver``/``parent_config_path`` feed the gate overlay; the remaining
    ``run_round_kwargs`` are forwarded verbatim to the real ``run_round``.
    """
    from harnessx.ghx.graph_gate import graph_gate_enabled

    target = _GateProxy(orch, gate_u_resolver, parent_config_path) if graph_gate_enabled() else orch
    return await run_round_with_graph_evidence(
        target,
        failed_task_ids=failed_task_ids,
        resolver=evidence_resolver,
        evidence_enabled=None,  # re-reads HARNESSX_GHX_AEGIS_EVIDENCE
        **run_round_kwargs,
    )


def _make_wrapped_run_round(orig_run_round, capture: dict, gate_u_resolver):
    """Build the ``AegisOrchestrator.run_round`` replacement that routes through the overlays.

    The first entry for a given orchestrator instance builds the round's overlay inputs
    and dispatches through :func:`_ghx_run_round`; because the overlays ultimately call
    ``orchestrator.run_round`` again, a re-entry guard (keyed on ``id(self)``) sends that
    inner call to the original method — otherwise the wrapper would recurse into itself.
    """
    active: set[int] = set()

    async def _wrapped(self, **kwargs):
        if id(self) in active:
            return await orig_run_round(self, **kwargs)
        active.add(id(self))
        try:
            round_n = kwargs["round_n"]
            # The pilot always evolves round_n from the rollouts that JUST ran in
            # R{round_n-1} (run_meta_aegis passes round_n=round_idx+1 with
            # raw_sessions_dir pointing at R{round_idx}); the orchestrator confirms
            # this ("round_n here is the round whose rollouts ALREADY ran").  So the
            # failing tasks and their U files belong to R{round_n-1}.
            rollout_round = round_n - 1
            pass_flags = kwargs.get("pass_flags_by_task") or {}
            failed = [tid for tid, flags in pass_flags.items() if not any(flags)]
            base_dir = Path(self.run_dir) / f"R{rollout_round}" / "sessions"
            resolver = _make_evidence_resolver(base_dir, capture.get(rollout_round, {}))
            return await _ghx_run_round(
                self,
                failed_task_ids=failed,
                evidence_resolver=resolver,
                gate_u_resolver=gate_u_resolver,
                parent_config_path=kwargs.get("current_config_path"),
                **kwargs,
            )
        finally:
            active.discard(id(self))

    return _wrapped


@contextlib.contextmanager
def _ghx_round_wiring():
    """Install the run-id capture + overlay seam for the duration of a pilot run.

    Two runtime patches, both restored on exit (no vendored bytes touched):

    1. ``run_meta_aegis._run_task`` → a wrapper that records ``(session_id, run_id)`` per
       ``(round, task_id)`` from each rollout's ``HarnessResult`` — the pilot itself keeps
       no task→run_id map, so we capture it here at the rollout call site.
    2. ``AegisOrchestrator.run_round`` → the overlay router (see :func:`_make_wrapped_run_round`).

    Yields the capture dict so callers/tests can inspect it.
    """
    capture: dict[int, dict[str, tuple[str, str]]] = {}
    orig_run_task = _pilot._run_task
    orig_run_round = AegisOrchestrator.run_round

    async def _capturing_run_task(harness, task, label, **kw):
        record = await orig_run_task(harness, task, label, **kw)
        try:
            result = record.get("_result")
            run_id = getattr(result, "run_id", None)
            tid = getattr(task, "task_id", None)
            rnd = _round_from_label(label)
            if run_id and tid and rnd is not None:
                # session_id is exactly what _run_task handed harness.run(): f"{label}-{tid}".
                capture.setdefault(rnd, {})[tid] = (f"{label}-{tid}", run_id)
        except Exception:  # noqa: BLE001 — capture is best-effort; never sink a rollout
            pass
        return record

    _pilot._run_task = _capturing_run_task
    AegisOrchestrator.run_round = _make_wrapped_run_round(orig_run_round, capture, _gate_u_resolver)
    try:
        yield capture
    finally:
        _pilot._run_task = orig_run_task
        AegisOrchestrator.run_round = orig_run_round


# ─── CLI ──────────────────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    """The vendored pilot's parser plus ``--ghx-level``.

    Reuses ``run_meta_aegis._build_argparser`` (a fresh parser each call, so adding an
    argument here never mutates the vendored surface) to keep the CLI identical.
    """
    p = _pilot._build_argparser()
    p.description = "GHX ladder launcher over the GAIA AEGIS pilot (adds --ghx-level)."
    # The vendored default bakes in an Anthropic meta model at import time; with a
    # single-key setup (e.g. only a LiteLLM/DeepSeek key) that crashes the first meta
    # phase.  None here means "not given on the CLI" so _resolve_meta_model can fall
    # back to GAIA_META_MODEL, then to the MAIN model — one key runs the whole ladder.
    p.set_defaults(meta_model=None)
    p.add_argument(
        "--ghx-level",
        type=int,
        default=0,
        choices=sorted(LEVEL_FLAGS),
        help=(
            "Graph-hardening ladder (cumulative, off by default): "
            "0=vendored pilot; 1=+UNFOLD+IDENTITY; 2=+AEGIS_EVIDENCE; "
            "3=same as 2 (facts share L2's materialiser); 4=+GRAPH_GATE. "
            "Explicit HARNESSX_GHX_* env vars always win over the level."
        ),
    )
    return p


def _resolve_meta_model(args: argparse.Namespace) -> None:
    """Fill ``args.meta_model`` when ``--meta-model`` was not given.

    Precedence (mirrors the ladder's "explicit wins" contract):
    explicit ``--meta-model`` > ``GAIA_META_MODEL`` env > follow ``--model``.
    The vendored runner's own default (an Anthropic model) is deliberately NOT in the
    chain: it requires a second credential the common single-key setup does not have.
    """
    if args.meta_model is None:
        args.meta_model = os.environ.get("GAIA_META_MODEL") or args.model


def _print_ghx_plan(level: int, applied: tuple[str, ...], meta_model: str | None = None) -> None:
    print("=" * 70)
    print(f"  GHX ladder — level {level}")
    print(f"  flags set (setdefault; explicit env wins): {', '.join(applied) or '(none)'}")
    if meta_model is not None:
        print(f"  meta model (explicit > GAIA_META_MODEL > follows --model): {meta_model}")
    on = [
        f
        for f in (_UNFOLD, _IDENTITY, _EVIDENCE, _GATE)
        if os.environ.get(f, "").strip().lower() in {"1", "true", "on", "yes"}
    ]
    print(f"  flags effective now: {', '.join(on) or '(none)'}")
    print("=" * 70)


async def _dispatch(args: argparse.Namespace) -> None:
    """Run the pilot, wiring the overlay seam only when an overlay flag is effective."""
    from harnessx.ghx.graph_gate import graph_gate_enabled
    from harnessx.ghx.overlay import aegis_evidence_enabled

    if aegis_evidence_enabled() or graph_gate_enabled():
        with _ghx_round_wiring():
            await _pilot.run_pilot(args)
    else:
        # L0/L1 (or any run with no effective overlay flag): the vendored round loop,
        # unwrapped. U/identity recording, if on, happens inside the core by flag.
        await _pilot.run_pilot(args)


async def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    _resolve_meta_model(args)

    # Mirror run_meta_aegis.main's preset handling (kept in lock-step by intent).
    if args.smoke:
        args.num_rounds = 2
        args.max_tasks = 1
        args.num_evolvers = 2

    applied = apply_level_flags(args.ghx_level)

    if args.dry_run:
        _pilot._print_dry_run_plan(args)
        _print_ghx_plan(args.ghx_level, applied, args.meta_model)
        return

    _print_ghx_plan(args.ghx_level, applied, args.meta_model)
    await _dispatch(args)


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
