"""P5 — MermaidFlow-inspired shadow evolution kernel (shadow mode ONLY).

Data flow (engineering plan §9)::

    Evolver/LLM → structured GraphEdit[] only → candidate normalization
      → GraphValidator S0–S4 → build + re-graph → Critic (advisory)
      → selective retest → VariantPool / SuccessLedger

This module is the deterministic kernel of that flow: proposal
normalization, the per-round hard gate, and the append-only shadow ledger.
It deliberately has NO API that writes a production config — shadow records
are the only output (回滚策略: P5 只写 shadow ledger).

上线门禁 encoded here:
  - proposals are accepted ONLY as typed operator specs — Python / YAML /
    Mermaid payloads are format violations and are rejected at parse time;
  - every candidate passes the hard S0–S4 gate BEFORE any critic ranking
    (only gate survivors are exposed for ranking);
  - the runtime overlay is never a genotype mutation input (operators target
    persistent nodes only; ``rt:`` targets reject);
  - APPLY / FORK decisions require measured evaluation evidence — an LLM
    judge alone can never promote a candidate;
  - the ledger is append-only: evaluation decisions are new records, never
    rewrites of gate records.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from experiments.variant_pool.envelope_gate import Envelope, judge_candidate
from harnessx.graph.identity import deployment_hash, genotype_hash
from harnessx.graph.operators import (
    InsertProcessor,
    MutateProcessorParams,
    RemoveProcessor,
    ReplaceSameSingletonGroup,
    RewireOrdering,
    SwapBundle,
    apply_operator,
)
from harnessx.graph.types import GraphSnapshot
from harnessx.graph.validate import validate_snapshot

MAX_CANDIDATES_PER_ROUND = 4

#: The only admissible proposal vocabulary — typed operators, nothing else.
PROPOSAL_OPERATORS: "dict[str, type]" = {
    "mutate_processor_params": MutateProcessorParams,
    "insert_processor": InsertProcessor,
    "remove_processor": RemoveProcessor,
    "replace_same_singleton_group": ReplaceSameSingletonGroup,
    "rewire_ordering": RewireOrdering,
    "swap_bundle": SwapBundle,
}

#: Top-level keys that betray a free-form payload (code / config dumps) —
#: the proposal contract is {"operator", "params"[, "rationale"]} and only that.
_FORBIDDEN_KEYS = frozenset({
    "python", "code", "yaml", "config", "mermaid", "diagram", "diff", "patch",
})
_ALLOWED_KEYS = frozenset({"operator", "params", "rationale"})


# ── proposal normalization ──────────────────────────────────────────────────


def parse_proposal(raw: Any) -> "tuple[Any | None, str]":
    """Normalize one structured proposal into an operator instance.

    Returns ``(operator, "")`` or ``(None, reason)``.  Fail-closed: anything
    that is not exactly ``{"operator": <known name>, "params": {...}}``
    (plus an optional advisory ``rationale``) is rejected with a concrete
    reason — free-form Python/YAML/Mermaid payloads never reach the gate.
    """
    if not isinstance(raw, dict):
        return None, f"proposal must be a dict, got {type(raw).__name__}"
    lowered = {str(k).lower() for k in raw}
    smuggled = lowered & _FORBIDDEN_KEYS
    if smuggled:
        return None, (f"format violation: keys {sorted(smuggled)} — proposals "
                      "are typed operator specs, never code/config payloads")
    unknown = set(raw) - _ALLOWED_KEYS
    if unknown:
        return None, f"unknown proposal keys {sorted(unknown)}"
    name = raw.get("operator")
    if not isinstance(name, str) or name not in PROPOSAL_OPERATORS:
        return None, (f"unknown operator {name!r} — admissible: "
                      f"{sorted(PROPOSAL_OPERATORS)}")
    params = raw.get("params", {})
    if not isinstance(params, dict):
        return None, f"params must be a dict, got {type(params).__name__}"
    try:
        op = PROPOSAL_OPERATORS[name](**params)
    except TypeError as exc:
        return None, f"bad params for {name}: {exc}"
    return op, ""


# ── ledger records ──────────────────────────────────────────────────────────


@dataclass
class CandidateRecord:
    """One shadow-ledger row — everything plan §9 requires a candidate to keep."""

    record_kind: str            # "gate" | "evaluation"
    candidate_id: str
    round_id: str
    parent_genotype: str
    operator: str = ""          # operator name ("" when the proposal never parsed)
    operator_params: dict = field(default_factory=dict)
    boundary_signature: str = ""            # SwapBundle only
    decision: str = ""          # "REJECT" | "GATED" | "APPLY" | "FORK"
    decided_by: str = ""        # "gate" | "evaluation"
    validation_passed: bool = False
    validation_issues: list = field(default_factory=list)      # [{layer, error_type, message}]
    validation_warnings: list = field(default_factory=list)
    genotype_hash: str = ""     # candidate's (gate survivors only)
    deployment_hash: str = ""
    evaluation_scope: dict = field(default_factory=dict)
    measured: dict = field(default_factory=dict)               # evaluation records only
    rationale: str = ""         # advisory text — NEVER a decision input
    created_at: float = 0.0


class ShadowLedger:
    """Append-only JSONL candidate ledger — the kernel's only write target."""

    def __init__(self, path: "str | Path"):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, record: CandidateRecord) -> None:
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(asdict(record), ensure_ascii=False) + "\n")

    def records(self) -> "list[CandidateRecord]":
        if not self.path.exists():
            return []
        out: list[CandidateRecord] = []
        with self.path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    out.append(CandidateRecord(**json.loads(line)))
        return out


# ── round runner ────────────────────────────────────────────────────────────


@dataclass
class RoundResult:
    """Outcome of one shadow round; only gate survivors may be critic-ranked."""

    round_id: str
    records: "list[CandidateRecord]" = field(default_factory=list)
    metrics: dict = field(default_factory=dict)

    @property
    def gated_records(self) -> "list[CandidateRecord]":
        """Candidates that passed the hard gate — the ONLY critic input."""
        return [r for r in self.records if r.decision == "GATED"]


def run_shadow_round(
    parent_snapshot: GraphSnapshot,
    proposals: "list[Any]",
    ledger: ShadowLedger,
    *,
    round_id: str,
    evaluation_scope: "dict | None" = None,
    materialize: bool = True,
    envelope: "Envelope | None" = None,
) -> RoundResult:
    """Run one shadow round: normalize → hard gate → ledger, nothing else.

    Proposals beyond ``MAX_CANDIDATES_PER_ROUND`` are explicitly REJECTED
    with a capacity reason (recorded, never silently dropped).  The parent
    snapshot is never mutated; no config is written anywhere.

    ``envelope`` (#33) turns on the envelope-aware reachability gate: a
    parameter edit whose EVERY changed param is provably dead in the experiment
    envelope (old and new both unreachable ⇒ runtime no-op) is REJECTED before
    the bed.  ``None`` (the default) leaves the gate byte-identical to its
    pre-#33 behavior — the check simply never runs.
    """
    parent_geno = genotype_hash(parent_snapshot)
    # Parent's own dangling `_after_` references (soft deps the builder already
    # tolerates). A candidate is only faulted for `unresolved_after` entries it
    # ADDS on top of this baseline — a pre-existing one is inherited, not caused.
    parent_unresolved = {
        w.message for w in validate_snapshot(parent_snapshot).warnings
        if w.error_type == "unresolved_after"
    }
    scope = dict(evaluation_scope or {})
    records: list[CandidateRecord] = []
    parsed = 0
    gate_passed = 0
    seen_genotypes: set[str] = set()

    def _parent_param(node_id: str, param: str) -> "Any | None":
        """#33 old-value source: the parent node's stored ctor kwarg (``None``
        when the node or param is absent — insufficient evidence ⇒ gate passes).
        """
        node = parent_snapshot.nodes.get(node_id)
        if node is None:
            return None
        ck = node.metadata.get("_ctor_kwargs_")
        if not isinstance(ck, dict):
            return None
        return ck.get(param)

    for i, raw in enumerate(proposals):
        candidate_id = f"{round_id}/c{i}"
        base = dict(
            record_kind="gate",
            candidate_id=candidate_id,
            round_id=round_id,
            parent_genotype=parent_geno,
            evaluation_scope=scope,
            created_at=time.time(),
        )
        rationale = raw.get("rationale", "") if isinstance(raw, dict) else ""

        if i >= MAX_CANDIDATES_PER_ROUND:
            records.append(CandidateRecord(
                **base, decision="REJECT", decided_by="gate",
                rationale=rationale,
                validation_issues=[{
                    "layer": "S0", "error_type": "round_capacity",
                    "message": f"round holds at most {MAX_CANDIDATES_PER_ROUND} "
                               f"candidates; proposal #{i} rejected",
                }],
            ))
            continue

        op, parse_err = parse_proposal(raw)
        if op is None:
            records.append(CandidateRecord(
                **base, decision="REJECT", decided_by="gate",
                rationale=rationale,
                validation_issues=[{
                    "layer": "S0", "error_type": "parse_error",
                    "message": parse_err,
                }],
            ))
            continue
        parsed += 1

        op_name = next(n for n, cls in PROPOSAL_OPERATORS.items()
                       if isinstance(op, cls))
        result, report = apply_operator(parent_snapshot, op,
                                        materialize=materialize)
        issues = [{"layer": x.layer, "error_type": x.error_type,
                   "message": x.message} for x in report.issues]
        warns = [{"layer": x.layer, "error_type": x.error_type,
                  "message": x.message} for x in report.warnings]

        if result is None:
            records.append(CandidateRecord(
                **base, operator=op_name,
                operator_params=dict(raw.get("params", {})),
                boundary_signature=getattr(op, "replacement_signature", ""),
                decision="REJECT", decided_by="gate",
                validation_passed=False,
                validation_issues=issues, validation_warnings=warns,
                rationale=rationale,
            ))
            continue

        # Δ8 lifecycle-DFA pass — a hard gate alongside S0–S4 (separately
        # implemented so the V− ablation can toggle each; both on by default)
        from harnessx.graph.dfa import lifecycle_dfa_check

        dfa = lifecycle_dfa_check(result)
        if not dfa.passed:
            records.append(CandidateRecord(
                **base, operator=op_name,
                operator_params=dict(raw.get("params", {})),
                boundary_signature=getattr(op, "replacement_signature", ""),
                decision="REJECT", decided_by="gate",
                validation_passed=False,
                validation_issues=[{
                    "layer": "Δ8", "error_type": w.check, "message": w.message,
                } for w in dfa.witnesses],
                validation_warnings=warns,
                rationale=rationale,
            ))
            continue

        # A candidate must not introduce a NEW dangling `_after_` reference: the
        # builder would silently drop it as an unresolved soft dep, so the edit is
        # a no-op the ledger would otherwise bank as a real candidate. Reject any
        # `unresolved_after` warning absent from the parent baseline (fail-closed).
        new_unresolved = [w for w in warns
                          if w["error_type"] == "unresolved_after"
                          and w["message"] not in parent_unresolved]
        if new_unresolved:
            dangling = "; ".join(w["message"] for w in new_unresolved)
            records.append(CandidateRecord(
                **base, operator=op_name,
                operator_params=dict(raw.get("params", {})),
                boundary_signature=getattr(op, "replacement_signature", ""),
                decision="REJECT", decided_by="gate",
                validation_passed=False,
                validation_issues=[{
                    "layer": "S2", "error_type": "unresolved_after",
                    "message": "candidate introduces dangling _after_ reference(s) "
                               f"absent from parent: {dangling}",
                }],
                validation_warnings=warns,
                rationale=(f"REJECT: new dangling _after_ reference(s) — {dangling}"
                           + (f" | {rationale}" if rationale else "")),
            ))
            continue

        # #33 envelope-aware reachability gate — the final gate check. Reject a
        # candidate whose EVERY changed param is provably dead in the experiment
        # envelope (old and new both unreachable ⇒ runtime no-op; measuring it
        # banks only noise, as the v4 max_usd 40→50 lazy winner did). Only
        # provably-dead edits are rejected — unknown processors/params, missing
        # parent values, and any binding endpoint all pass (never raises).
        if envelope is not None:
            dead = judge_candidate(
                op_name, dict(raw.get("params", {})), _parent_param, envelope)
            if dead:
                detail = "; ".join(dead)
                records.append(CandidateRecord(
                    **base, operator=op_name,
                    operator_params=dict(raw.get("params", {})),
                    boundary_signature=getattr(op, "replacement_signature", ""),
                    decision="REJECT", decided_by="gate",
                    validation_passed=False,
                    validation_issues=[{
                        "layer": "envelope", "error_type": "envelope_dead",
                        "message": detail,
                    }],
                    validation_warnings=warns,
                    rationale=(f"ENVELOPE_DEAD: {detail}"
                               + (f" | {rationale}" if rationale else "")),
                ))
                continue

        gate_passed += 1
        g, d = genotype_hash(result), deployment_hash(result)
        seen_genotypes.add(g)
        records.append(CandidateRecord(
            **base, operator=op_name,
            operator_params=dict(raw.get("params", {})),
            boundary_signature=getattr(op, "replacement_signature", ""),
            decision="GATED", decided_by="gate",
            validation_passed=True,
            validation_issues=[], validation_warnings=warns,
            genotype_hash=g, deployment_hash=d,
            rationale=rationale,
        ))

    for record in records:
        ledger.append(record)

    total = len(proposals)
    return RoundResult(
        round_id=round_id,
        records=records,
        metrics={
            "proposed": total,
            "parse_rate": (parsed / total) if total else 0.0,
            "gate_pass_rate": (gate_passed / total) if total else 0.0,
            "unique_genotypes": len(seen_genotypes),
        },
    )


# ── evaluation decisions (the only path to APPLY / FORK) ────────────────────


def record_evaluation(
    ledger: ShadowLedger,
    candidate_id: str,
    *,
    decision: str,
    measured: dict,
    rationale: str = "",
) -> CandidateRecord:
    """Append a measured-evaluation decision for a GATED candidate.

    上线门禁: only deterministic gates and MEASURED evaluation write
    APPLY/FORK/REJECT.  ``measured`` must carry non-empty numeric evidence —
    advisory text (an LLM judge's opinion) alone can never promote.
    """
    if decision not in ("APPLY", "FORK", "REJECT"):
        raise ValueError(f"decision must be APPLY/FORK/REJECT, got {decision!r}")
    if not isinstance(measured, dict) or not measured:
        raise ValueError(
            "measured evaluation evidence required — an LLM judge alone "
            "cannot promote a candidate (上线门禁)")
    if not any(isinstance(v, (int, float)) and not isinstance(v, bool)
               for v in measured.values()):
        raise ValueError(
            "measured must contain at least one numeric metric "
            "(advisory text is not evidence)")

    gate_rows = [r for r in ledger.records()
                 if r.candidate_id == candidate_id and r.record_kind == "gate"]
    if not gate_rows:
        raise ValueError(f"unknown candidate {candidate_id!r} — no gate record")
    gate = gate_rows[-1]
    if gate.decision != "GATED":
        raise ValueError(
            f"candidate {candidate_id!r} was rejected by the gate — "
            "evaluation cannot resurrect it")

    record = CandidateRecord(
        record_kind="evaluation",
        candidate_id=candidate_id,
        round_id=gate.round_id,
        parent_genotype=gate.parent_genotype,
        operator=gate.operator,
        operator_params=gate.operator_params,
        boundary_signature=gate.boundary_signature,
        decision=decision,
        decided_by="evaluation",
        validation_passed=True,
        genotype_hash=gate.genotype_hash,
        deployment_hash=gate.deployment_hash,
        evaluation_scope=gate.evaluation_scope,
        measured=dict(measured),
        rationale=rationale,
        created_at=time.time(),
    )
    ledger.append(record)
    return record


def ledger_metrics(ledger: ShadowLedger) -> dict:
    """Cross-round summary of the plan-§9 evaluation metrics."""
    rows = ledger.records()
    gate_rows = [r for r in rows if r.record_kind == "gate"]
    parsed = [r for r in gate_rows
              if not any(i.get("error_type") == "parse_error"
                         for i in r.validation_issues)]
    gated = [r for r in gate_rows if r.decision == "GATED"]
    return {
        "proposals": len(gate_rows),
        "parse_rate": (len(parsed) / len(gate_rows)) if gate_rows else 0.0,
        "gate_pass_rate": (len(gated) / len(gate_rows)) if gate_rows else 0.0,
        "unique_genotypes": len({r.genotype_hash for r in gated if r.genotype_hash}),
        "applied": sum(1 for r in rows
                       if r.record_kind == "evaluation" and r.decision == "APPLY"),
        "forked": sum(1 for r in rows
                      if r.record_kind == "evaluation" and r.decision == "FORK"),
    }
