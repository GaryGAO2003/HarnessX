"""Shadow-evolution round-loop runner — B / F0 arms and the calibration rehearsal.

This module drives the *outer* loop that the shadow kernel
(:mod:`experiments.variant_pool.shadow_evolution`) and the evaluation bridge
(:mod:`experiments.variant_pool.eval_bridge`) only supply single steps of.  One
call to :func:`run_rehearsal` runs ``rounds`` generations of:

    parent config  →  to_graph  →  propose  →  hard gate (run_shadow_round)
      →  materialize each GATED candidate  →  measured evaluation (TaskBed)
      →  K=1 selection  →  winner's config becomes the next parent

Two arms share this driver:

* ``mode="b"``  — the full propose→gate→evaluate→select loop above.
* ``mode="f0"`` — the honest-denominator arm: each round evaluates ONLY the
  parent baseline (no proposer, no gate, no ledger writes), so F0 measures the
  parent's own task-bed variance across the same number of rounds.

Selection semantics — **K=1 Global** (docstring is the spec of record):

* The pool holds exactly one incumbent (the parent).  Each round the gate
  survivors are measured and the single best challenger is compared against the
  freshly-measured parent baseline.  A challenger is promoted (``APPLY``) iff its
  ``pass_rate`` is the round maximum AND strictly exceeds ``parent_pass_rate +
  min_delta``; every other measured challenger is ``REJECT``.  With no promotion
  the parent survives unchanged into the next round.
* **No FORK is ever produced.**  Forking needs a pool of K>1 to hold a divergent
  lineage; under K=1 the shadow kernel's FORK decision has no home and degrades
  to REJECT (回滚策略 / 上线门禁: only measured evidence writes APPLY/REJECT — see
  :func:`experiments.variant_pool.shadow_evolution.record_evaluation`).
* ``min_delta`` defaults to ``0.0`` (strict ``>`` — equal scores never promote).
  A future significance gate (noise-band / paired-bootstrap threshold) plugs in
  at exactly this comparison point, replacing the scalar ``min_delta`` band.

Division of labor: this runner writes ONLY raw per-round facts to
``out_dir/report.json`` (baselines, per-candidate measured numbers, decisions,
winners, lineage paths, wall-clock).  The formal nine process metrics are
computed downstream from the ledger + this report by a separate metrics module;
here the sole contract is that every raw fact those metrics need is present.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

from harnessx.core.harness import HarnessConfig
from harnessx.graph import GraphSnapshot, NodeType, to_graph

from experiments.variant_pool.eval_bridge import (
    StubTaskBed,
    TaskBed,
    materialize_candidate,
    measured_from_result,
)
from experiments.variant_pool.proposer import ExtractionResult, llm_propose
from experiments.variant_pool.shadow_evolution import (
    ShadowLedger,
    record_evaluation,
    run_shadow_round,
)

logger = logging.getLogger(__name__)

#: A proposer is any ``snapshot -> ExtractionResult`` — the LLM transport and
#: ``max_candidates`` are bound by the caller (CLI / test), so the runner stays
#: provider-agnostic and the same signature covers stub and real proposers.
Proposer = Callable[[GraphSnapshot], ExtractionResult]


# ── report dataclasses (raw facts only) ──────────────────────────────────────


@dataclass
class CandidateOutcome:
    """One GATED candidate's measured outcome and selection decision."""

    candidate_id: str
    genotype_hash: str
    config_path: str
    measured: dict = field(default_factory=dict)
    decision: str = ""              # "APPLY" | "REJECT"
    pass_rate: float = 0.0
    infra_failed: bool = False


@dataclass
class RoundReport:
    """Raw facts for one rehearsal round."""

    round_id: str
    mode: str
    parent_config: str              # config that seeded THIS round
    parse_ok: bool = True
    n_proposals: int = 0
    n_gated: int = 0
    n_evaluated: int = 0
    parent_pass_rate: float = 0.0
    baseline_measured: dict = field(default_factory=dict)
    #: Isolated artefact dir of THIS round's parent baseline evaluation. Metric 9
    #: (selective_retest_savings) reads ``<baseline_eval_dir>/footprints.jsonl``
    #: from it; ``""`` when the bed produced no dir (StubTaskBed / dry-run).
    baseline_eval_dir: str = ""
    candidates: list = field(default_factory=list)   # list[CandidateOutcome]
    winner: str = ""                # winning candidate_id, "" when parent survives
    wall_clock_s: float = 0.0
    error: str = ""                 # proposer error text when parse_ok is False
    #: Holdout read-outs — populated ONLY on an APPLY round (cost discipline).
    #: Each is ``asdict(TaskBedResult)`` for the OUTGOING parent / promoted
    #: winner measured on the disjoint holdout set; per_task detail rides inside
    #: (metric 8 tallies regression per task, so the aggregate is not enough).
    #: Empty ``{}`` on every round that does not promote.
    holdout_before: dict = field(default_factory=dict)
    holdout_after: dict = field(default_factory=dict)


@dataclass
class RehearsalReport:
    """Whole-run rehearsal record (dataclass → dict → out_dir/report.json)."""

    mode: str
    rounds: int
    initial_parent_config: str
    final_parent_config: str
    round_reports: list = field(default_factory=list)   # list[RoundReport]


# ── deterministic dry-run proposer (zero API) ────────────────────────────────


def normalize_parent(parent_config: Path, out_dir: Path) -> Path:
    """One build-backed fixed-point cycle: load → graph → config → build → YAML.

    Pre-v5.3 parents carry partial serialized metadata; their first
    graph→config cycle drifts (the G1 roundtrip corpus: strict first-cycle
    2.3%, fixed point 100% among buildable configs, 0 divergent).  Lineage
    must START at the fixed point, otherwise every materialized child fails
    the independent genotype re-hash (metrics ``roundtrip_rate``) for
    inherited reasons rather than real ones.  Fail-closed: a parent that
    does not build cannot anchor a rehearsal — the exception propagates.
    """
    import yaml

    from harnessx.core.builder import build_from_config
    from harnessx.graph.transform import graph_to_config_dict

    from experiments.variant_pool.eval_bridge import graft_base_fields

    cfg = HarnessConfig.from_yaml_file(parent_config)
    built = build_from_config(graph_to_config_dict(to_graph(cfg)))
    # normalization touches ONLY the composition layer: graft every non-graph
    # field (tool_registry / workspace / tracer / sandbox…) back from the
    # source YAML — without them the config runs toolless (observed live).
    norm_dict = graft_base_fields(
        {"processors": yaml.safe_load(built.to_yaml())["processors"]},
        parent_config,
    )
    norm = Path(out_dir) / "parent_normalized" / "config.yaml"
    norm.parent.mkdir(parents=True, exist_ok=True)
    # dump the merged dict directly (never back through HarnessConfig(**...):
    # construction coerces raw grafted specs and loses them on re-serialization)
    norm.write_text(
        yaml.safe_dump(norm_dict, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return norm


def stub_proposer(snapshot: GraphSnapshot) -> ExtractionResult:
    """A deterministic proposer: one legal ``rewire_ordering`` per snapshot.

    Picks the lexicographically first persistent PROCESSOR node and nudges its
    ``_order_`` by +10 (always a real change, never a no-op).  The proposal
    shape matches what ``run_shadow_round`` gates GATED in
    ``tests/graph/test_shadow_evolution.py`` / ``test_eval_bridge.py``.  Used by
    ``--dry-run`` and the zero-API tests so the outer loop can be exercised with
    no model calls.
    """
    proc_ids = sorted(
        nid for nid, node in snapshot.nodes.items()
        if node.node_type is NodeType.PROCESSOR
    )
    if not proc_ids:
        return ExtractionResult(
            proposals=[], error="stub_proposer: snapshot has no PROCESSOR node"
        )
    node_id = proc_ids[0]
    current = snapshot.nodes[node_id].metadata.get("_order_")
    base = current if isinstance(current, int) else 0
    new_order = base + 10
    return ExtractionResult(proposals=[{
        "operator": "rewire_ordering",
        "params": {"node_id": node_id, "order": new_order},
        "rationale": f"stub: nudge {node_id} _order_ to {new_order}",
    }], error="")


# ── the round loop ───────────────────────────────────────────────────────────


async def run_rehearsal(
    parent_config: Path,
    *,
    rounds: int,
    task_bed: TaskBed,
    proposer: "Proposer | None" = None,
    ledger: ShadowLedger,
    out_dir: Path,
    mode: str = "b",
    max_candidates: int = 4,
    min_delta: float = 0.0,
    task_ids: "list[str] | None" = None,
    normalize: bool = True,
    holdout_bed: "TaskBed | None" = None,
    holdout_task_ids: "list[str] | None" = None,
) -> RehearsalReport:
    """Run ``rounds`` shadow-evolution rounds and return the raw-facts report.

    See the module docstring for the K=1 Global selection semantics.  ``mode``
    is ``"b"`` (full loop), ``"r"`` (same loop — the arm difference lives
    entirely in ``proposer``, which the CLI wires to the seeded uniform
    :func:`~experiments.variant_pool.random_proposer.random_proposer`), or
    ``"f0"`` (baseline-only honest denominator).
    ``proposer`` defaults to :func:`stub_proposer` (zero-API).  The report is
    flushed to ``out_dir/report.json`` after every round so a crash mid-run
    still leaves the completed rounds on disk.

    ``holdout_bed`` (optional, same :class:`TaskBed` protocol as ``task_bed``,
    pointed at a task set DISJOINT from the evolution bed) turns on the holdout
    regression read-out — the metric-8 signal against the fix-one-break-one
    treadmill (THESIS B-arm pre-registered endpoint「留出回归率↓」: how much a
    promoted generation regressed on tasks it never evolved against).  Cost
    discipline: ONLY a round that promotes (APPLY) pays for it — the runner then
    evaluates the OUTGOING parent and the promoted winner on the holdout set and
    stores both (``asdict(TaskBedResult)``, per_task detail included) into that
    round's ``holdout_before`` / ``holdout_after``.  ``mode="f0"`` never
    promotes, so it never touches the holdout bed; every no-APPLY round leaves
    the two fields empty.  ``holdout_task_ids`` optionally restricts the subset.
    """
    parent_config = Path(parent_config)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    if normalize:
        parent_config = normalize_parent(parent_config, out_dir)
    report_path = out_dir / "report.json"
    active_proposer: Proposer = proposer or stub_proposer

    report = RehearsalReport(
        mode=mode,
        rounds=rounds,
        initial_parent_config=str(parent_config),
        final_parent_config=str(parent_config),
    )
    current_parent = parent_config

    def _flush() -> None:
        report.final_parent_config = str(current_parent)
        report_path.write_text(
            json.dumps(asdict(report), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    for i in range(rounds):
        round_id = f"r{i}"
        t0 = time.time()

        # (2) Parent baseline — every round, both arms. In F0 this is the whole
        # round; in B it supplies parent_pass_rate for the selection compare.
        baseline_result = await task_bed.evaluate(current_parent, task_ids)
        parent_pass_rate = float(baseline_result.pass_rate)
        rr = RoundReport(
            round_id=round_id,
            mode=mode,
            parent_config=str(current_parent),
            parent_pass_rate=parent_pass_rate,
            baseline_measured=measured_from_result(baseline_result),
            baseline_eval_dir=baseline_result.eval_dir,
        )

        if mode == "f0":
            rr.wall_clock_s = time.time() - t0
            report.round_reports.append(rr)
            _flush()
            continue

        # (1) parent config → typed graph snapshot (lineage always via on-disk file)
        snapshot = to_graph(HarnessConfig.from_yaml_file(current_parent))

        # (3) propose — neither a parse failure nor a proposer TRANSPORT
        # failure (auth / network / provider exception) aborts the run: both
        # are a round-level parse_ok=False, the parent survives and the loop
        # advances (parse 率 counts them; a long run must outlive one bad
        # round).
        try:
            extraction = active_proposer(snapshot)
        except Exception as exc:  # noqa: BLE001 — transport is out of our hands
            extraction = ExtractionResult(
                proposals=[], error=f"proposer transport: {type(exc).__name__}: {exc}")
        proposals = list(extraction.proposals or [])
        if extraction.error or not proposals:
            rr.parse_ok = False
            rr.error = extraction.error or "proposer returned no proposals"
            rr.wall_clock_s = time.time() - t0
            report.round_reports.append(rr)
            _flush()
            continue

        proposals = proposals[:max_candidates]
        rr.n_proposals = len(proposals)

        # (4) hard gate — only GATED survivors are materialized/evaluated.
        round_result = run_shadow_round(
            snapshot, proposals, ledger, round_id=round_id, materialize=True
        )
        gated = round_result.gated_records
        rr.n_gated = len(gated)

        # (5) materialize + measure every gate survivor.
        rounds_dir = out_dir / "rounds" / round_id
        outcomes: "list[CandidateOutcome]" = []
        for rec in gated:
            cfg_path = materialize_candidate(
                snapshot, rec, rounds_dir, base_config=current_parent)
            cand_result = await task_bed.evaluate(cfg_path, task_ids)
            outcomes.append(CandidateOutcome(
                candidate_id=rec.candidate_id,
                genotype_hash=rec.genotype_hash,
                config_path=str(cfg_path),
                measured=measured_from_result(cand_result),
                pass_rate=float(cand_result.pass_rate),
                infra_failed=bool(cand_result.infra_failed),
            ))
        rr.n_evaluated = len(outcomes)

        # (6) K=1 Global selection with a fragility gate. Two eligibility
        # conditions: a challenger must not have failed infra, AND it must not
        # truncate (exit_reason budget_exceeded) more than the parent's same-round
        # baseline — trading pass_rate for a higher run-out rate is more fragile,
        # not better. The veto is an eligibility FILTER, not a demotion of the
        # argmax: the best ELIGIBLE challenger promotes iff it strictly beats
        # parent+min_delta, so a vetoed top scorer lets a lower-scoring eligible
        # one win, and if none are eligible the parent survives unchanged.
        parent_trunc = float(rr.baseline_measured.get("truncation_rate", 0.0))
        vetoed: "dict[str, str]" = {}
        eligible: "list[CandidateOutcome]" = []
        for o in outcomes:
            if o.infra_failed:
                continue
            cand_trunc = float(o.measured.get("truncation_rate", 0.0))
            if cand_trunc > parent_trunc:
                vetoed[o.candidate_id] = (
                    f"FRAGILITY_VETO: truncation_rate {cand_trunc:.3f} > parent "
                    f"baseline {parent_trunc:.3f}")
                continue
            eligible.append(o)
        winner_o: "CandidateOutcome | None" = None
        if eligible:
            best = max(eligible, key=lambda o: o.pass_rate)
            if best.pass_rate > parent_pass_rate + min_delta:
                winner_o = best

        for o in outcomes:
            decision = "APPLY" if o is winner_o else "REJECT"
            record_evaluation(
                ledger, o.candidate_id, decision=decision, measured=o.measured,
                rationale=vetoed.get(o.candidate_id, ""),
            )
            o.decision = decision
            rr.candidates.append(o)

        # (7) winner's materialized config seeds the next round (lineage on disk).
        if winner_o is not None:
            rr.winner = winner_o.candidate_id
            # (7a) Holdout regression read-out (metric 8), cost discipline: only
            # a promoting round pays for it.  Evaluate the OUTGOING parent
            # (``current_parent`` — still the pre-promotion incumbent) and the
            # winner on the disjoint holdout set; ``asdict`` keeps per_task so the
            # per-task regression tally downstream has题级明细.  Measured before
            # ``current_parent`` advances to the winner below. f0 never reaches
            # here (it ``continue``s above), so f0 never touches the holdout bed.
            if holdout_bed is not None:
                before = await holdout_bed.evaluate(current_parent, holdout_task_ids)
                after = await holdout_bed.evaluate(
                    Path(winner_o.config_path), holdout_task_ids)
                rr.holdout_before = asdict(before)
                rr.holdout_after = asdict(after)
            current_parent = Path(winner_o.config_path)

        rr.wall_clock_s = time.time() - t0
        report.round_reports.append(rr)
        _flush()

    _flush()
    return report


# ── CLI ──────────────────────────────────────────────────────────────────────


def _make_sync_llm_call(model: str, provider_id: str) -> "Callable[[str], str]":
    """Wrap a real async provider into a synchronous ``str -> str`` llm_call.

    Lazy-imports the recipe's ``_make_provider`` (deferred so importing this
    module never triggers recipe side effects, mirroring ``GaiaTaskBed``).  The
    provider's async ``complete`` is driven on a fresh event loop in a worker
    thread — ``run_rehearsal`` is itself async, so a bare ``asyncio.run`` inside
    the synchronous proposer would collide with the already-running loop.
    """
    import concurrent.futures

    from harnessx.core.events import Message
    from recipe.gaia_evolver.run import _make_provider

    provider = _make_provider(model, provider_id)

    def llm_call(prompt: str) -> str:
        def _run() -> str:
            async def _go() -> str:
                event = await provider.complete(
                    [Message(role="user", content=prompt)], tools=[]
                )
                return event.content
            return asyncio.run(_go())

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(_run).result()

    return llm_call


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m experiments.variant_pool.rehearsal",
        description="Shadow-evolution rehearsal runner (B / F0 arms).",
    )
    p.add_argument("--parent", required=True, help="parent HarnessConfig YAML")
    p.add_argument("--rounds", type=int, default=3)
    p.add_argument("--mode", choices=["b", "f0", "r"], default="b")
    p.add_argument("--seed", type=int, default=0,
                   help="rng seed for --mode r (uniform random proposer)")
    p.add_argument("--dry-run", action="store_true",
                   help="StubTaskBed + stub_proposer (zero API)")
    p.add_argument("--out-dir", required=True)
    p.add_argument("--ledger", default=None,
                   help="shadow ledger JSONL (default: <out-dir>/shadow.jsonl)")
    p.add_argument("--max-candidates", type=int, default=4)
    p.add_argument("--min-delta", type=float, default=0.0)
    p.add_argument("--task-ids", nargs="*", default=None)
    # holdout regression read-out (metric 8): a second bed on the disjoint set
    p.add_argument("--holdout-data-path",
                   default="recipe/gaia_evolver/data/holdout6.json",
                   help="disjoint holdout task set for the regression read-out")
    p.add_argument("--no-holdout", action="store_true",
                   help="disable the holdout regression read-out")
    # real-run (non-dry-run) knobs
    p.add_argument("--data-path", default="recipe/gaia_evolver/data/calib6.json")
    p.add_argument("--model", default=None)
    p.add_argument("--meta-model", default=None)
    p.add_argument("--provider-id", default=None)
    p.add_argument("--max-cost", type=float, default=0.5)
    p.add_argument("--max-steps", type=int, default=20)
    p.add_argument("--pass-k", type=int, default=1)
    # selective_retest_savings (metric 9): collect per-task coverage footprints
    # to each eval's footprints.jsonl. Default off — pure instrumentation, no
    # change to evaluation behaviour or cost when unset. Dry-run ignores it
    # (StubTaskBed produces no footprints).
    p.add_argument("--collect-footprints", action="store_true",
                   help="collect coverage footprints for selective_retest_savings (metric 9)")
    return p


def _serper_key_present() -> bool:
    """Whether ``SERPER_API_KEY`` is set. Recorded in the run summary so a run
    that silently degraded to the built-in scrape chain is auditable afterwards.
    """
    return bool(os.environ.get("SERPER_API_KEY"))


def _warn_if_no_serper_key() -> bool:
    """Warn — without blocking — when a real evaluation is about to run with no
    ``SERPER_API_KEY``. WebSearch then silently degrades to the built-in scrape
    chain, so the A/B arms no longer share a search stack (arm parity breaks).

    Running a no-key control on purpose is legal, but it must leave a trace; this
    is the CLI-side mirror of the once-per-process guard in ``serper_search``.
    Returns key presence so the caller need not re-read the environment.
    """
    present = _serper_key_present()
    if not present:
        logger.warning(
            "real evaluation without SERPER_API_KEY: search will silently "
            "degrade to the scrape chain; arm parity at risk"
        )
    return present


def main(argv: "list[str] | None" = None) -> int:
    # Anti-contamination L1: block GAIA answer-key domains before any tool runs.
    # setdefault, so an explicit HARNESSX_URL_BLOCKLIST env override still wins.
    from experiments.variant_pool.anti_contamination import install_blocklist_env

    install_blocklist_env()
    args = _build_parser().parse_args(argv)
    out_dir = Path(args.out_dir)
    ledger = ShadowLedger(Path(args.ledger) if args.ledger
                          else out_dir / "shadow.jsonl")
    holdout_bed: "TaskBed | None" = None

    def _random_proposer() -> "Proposer":
        # R arm: uniform random legal edits, seeded — needs NO proposer model
        # (that is the arm's point: same action space + gate, zero LLM).
        import random as _random

        from experiments.variant_pool.random_proposer import random_proposer

        rng = _random.Random(args.seed)
        return lambda snapshot: random_proposer(
            snapshot, rng, max_candidates=args.max_candidates)

    if args.dry_run:
        task_bed: TaskBed = StubTaskBed(pass_rate=0.5)
        proposer: "Proposer | None" = (
            _random_proposer() if args.mode == "r" else stub_proposer)
    else:
        if not (args.model and args.meta_model and args.provider_id):
            _build_parser().error(
                "--model, --meta-model and --provider-id are required "
                "unless --dry-run is set"
            )
        # A real run with no key silently degrades to the scrape chain and
        # breaks arm parity — warn, but do not block (no-key controls are legal).
        _warn_if_no_serper_key()
        from experiments.variant_pool.eval_bridge import GaiaTaskBed

        task_bed = GaiaTaskBed(
            model=args.model,
            meta_model=args.meta_model,
            provider_id=args.provider_id,
            data_path=Path(args.data_path),
            pass_k=args.pass_k,
            max_cost=args.max_cost,
            max_steps=args.max_steps,
            out_dir=out_dir / "taskbed",
            collect_footprints=args.collect_footprints,
        )
        # Second bed on the disjoint holdout set (metric 8). Built only when the
        # read-out is enabled, the mode can promote (f0 never does), and the data
        # file is present — same params as the main bed, its own out_dir so the
        # two evaluations never collide. dry-run builds no holdout bed (zero-API).
        holdout_path = Path(args.holdout_data_path)
        if not args.no_holdout and args.mode != "f0" and holdout_path.exists():
            holdout_bed = GaiaTaskBed(
                model=args.model,
                meta_model=args.meta_model,
                provider_id=args.provider_id,
                data_path=holdout_path,
                pass_k=args.pass_k,
                max_cost=args.max_cost,
                max_steps=args.max_steps,
                out_dir=out_dir / "holdout_taskbed",
                collect_footprints=args.collect_footprints,
            )
        if args.mode == "b":
            # arm discipline: the proposer runs on the META model (same tier
            # and budget as the A arm's evolver), never the task model.
            llm_call = _make_sync_llm_call(args.meta_model, args.provider_id)

            def proposer(snapshot: GraphSnapshot) -> ExtractionResult:
                return llm_propose(
                    snapshot, llm_call, max_candidates=args.max_candidates
                )
        elif args.mode == "r":
            proposer = _random_proposer()
        else:
            proposer = None

    report = asyncio.run(run_rehearsal(
        Path(args.parent),
        rounds=args.rounds,
        task_bed=task_bed,
        proposer=proposer,
        ledger=ledger,
        out_dir=out_dir,
        mode=args.mode,
        max_candidates=args.max_candidates,
        min_delta=args.min_delta,
        task_ids=args.task_ids,
        holdout_bed=holdout_bed,
    ))

    print(json.dumps({
        "mode": report.mode,
        "rounds": report.rounds,
        "serper_key_present": _serper_key_present(),
        "holdout_wired": holdout_bed is not None,
        "final_parent_config": report.final_parent_config,
        "rounds_report": [
            {"round_id": rr.round_id, "parse_ok": rr.parse_ok,
             "n_gated": rr.n_gated, "n_evaluated": rr.n_evaluated,
             "winner": rr.winner, "parent_pass_rate": rr.parent_pass_rate}
            for rr in report.round_reports
        ],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
