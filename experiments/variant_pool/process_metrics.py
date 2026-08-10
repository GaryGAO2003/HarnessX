"""Nine process metrics — the thesis's primary read-out layer (端点倒置).

Endpoint inversion: the *process* metrics below are the headline reading; pass@k
only sets a non-inferiority bound and is not computed here.  This module reads
two frozen inputs — a rehearsal ``report.json`` (``RehearsalReport`` asdict, see
:mod:`experiments.variant_pool.rehearsal`) and a ``ShadowLedger`` JSONL (rows of
``CandidateRecord`` asdict, see :mod:`experiments.variant_pool.shadow_evolution`)
— and returns the nine metrics plus run ``totals``.

It is a *pure reader*: no task bed is ever re-run.  It recomputes in two places,
both from artefacts already on disk: roundtrip (metric 3) re-derives each
candidate's ``genotype_hash`` from the materialized config rather than trusting
the bridge's re-graph assertion; selective retest (metric 9) re-derives each
gated candidate's danger set from the parent config and intersects it with the
parent-baseline coverage footprints the bridge collected under
``--collect-footprints``.

Division-by-zero convention (no metric ever raises on empty input):
  * rate / ratio metrics       → ``0.0`` on an empty denominator;
  * event-index / time metric 7 → ``null`` when the promotion never occurs;
  * holdout metric 8           → ``null`` (honest absence) with a reason when no
                                 APPLY round carries holdout data; a real rate
                                 otherwise;
  * selective metric 9         → ``null`` (honest absence) with a reason when no
                                 footprints were collected; a real saving
                                 otherwise.

No silent caps: any malformed input line (bad JSON, wrong shape, missing file)
is counted into ``skipped_inputs`` with a reason — never dropped silently.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from harnessx.core.harness import HarnessConfig
from harnessx.graph import (
    EdgeType,
    danger_edge_set,
    genotype_hash,
    intersects_footprint,
    to_graph,
)

from experiments.variant_pool.shadow_evolution import parse_proposal

#: Honest-null reasons. Metric 9 (now computed) is null only when there is
#: nothing to read — no footprints were collected, or none of the gated
#: candidates could be re-derived to danger edges. Metric 8's two null branches:
#: no promotion at all, or promotions carrying no holdout data.
SELECTIVE_NO_FOOTPRINTS_REASON = "footprints not collected (run with --collect-footprints)"
SELECTIVE_NO_SCORE_REASON = (
    "footprints present but no gated candidate could be re-derived to danger edges")
#: Emitted at top level whenever any candidate was scored by hook projection
#: (below): the observation layer currently records coverage at hook
#: granularity (no proc:* attribution), so a processor-level danger set is read
#: through the hooks its procs attach to — an upper-bound, conservative saving
#: until the observation layer attributes coverage to individual processors.
SELECTIVE_HOOK_PROJECTION_NOTE = (
    "hook-projected candidates read danger-vs-footprint at hook granularity "
    "(observation layer emits hook-level footprints, no proc:* attribution); their "
    "saving is a conservative upper bound pending an observation-layer upgrade")
HOLDOUT_NO_APPLY_REASON = "no APPLY rounds"
HOLDOUT_NOT_WIRED_REASON = "holdout not wired for this run"


# ── input loading (skips counted, never silent) ──────────────────────────────


def _load_report(path: Path, skipped: "list[dict]") -> dict:
    """Read the rehearsal report; a bad/absent report yields ``{}`` + a skip."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        skipped.append({"source": "report", "path": str(path),
                        "reason": f"{type(exc).__name__}: {exc}"})
        return {}
    if not isinstance(data, dict):
        skipped.append({"source": "report", "path": str(path),
                        "reason": "report root is not a JSON object"})
        return {}
    return data


def _load_ledger(path: Path, skipped: "list[dict]") -> "list[dict]":
    """Read the shadow ledger JSONL; malformed rows are skipped with a reason."""
    rows: "list[dict]" = []
    p = Path(path)
    if not p.exists():
        skipped.append({"source": "ledger", "path": str(path),
                        "reason": "ledger file does not exist"})
        return rows
    with p.open("r", encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except ValueError as exc:
                skipped.append({"source": "ledger", "line": lineno,
                                "reason": f"invalid JSON: {exc}"})
                continue
            if not isinstance(obj, dict):
                skipped.append({"source": "ledger", "line": lineno,
                                "reason": "ledger row is not a JSON object"})
                continue
            rows.append(obj)
    return rows


# ── small numeric accessors ──────────────────────────────────────────────────


def _f(d: dict, key: str) -> float:
    return float((d or {}).get(key) or 0.0)


def _i(d: dict, key: str) -> int:
    return int((d or {}).get(key) or 0)


# ── metric 0: run totals ─────────────────────────────────────────────────────


def _totals(rounds: "list[dict]") -> dict:
    """Aggregate counters + cost/token/wall sums over every round."""
    proposals = gated = evaluated = apply_n = tokens = 0
    cost = wall = 0.0
    for r in rounds:
        proposals += _i(r, "n_proposals")
        gated += _i(r, "n_gated")
        evaluated += _i(r, "n_evaluated")
        wall += _f(r, "wall_clock_s")
        bm = r.get("baseline_measured") or {}
        cost += _f(bm, "cost_usd")
        tokens += _i(bm, "tokens")
        for c in (r.get("candidates") or []):
            m = c.get("measured") or {}
            cost += _f(m, "cost_usd")
            tokens += _i(m, "tokens")
            if c.get("decision") == "APPLY":
                apply_n += 1
    return {
        "rounds": len(rounds),
        "proposals": proposals,
        "gated": gated,
        "evaluated": evaluated,
        "apply": apply_n,
        "cost_usd": cost,
        "tokens": tokens,
        "wall_clock_s": wall,
    }


# ── metric 1: parse_rate (解析成功率) ─────────────────────────────────────────


def _parse_rate(rounds: "list[dict]") -> dict:
    """parse_ok B-rounds / total B-rounds (f0 rounds never propose, so excluded)."""
    b_rounds = [r for r in rounds if r.get("mode") != "f0"]
    n = len(b_rounds)
    ok = sum(1 for r in b_rounds if r.get("parse_ok"))
    return {"value": (ok / n) if n else 0.0, "parsed_rounds": ok, "b_rounds": n}


# ── metric 2: gate_pass_rate (硬门通过率 · S0–S4 + Δ8) ────────────────────────


def _gate_pass_rate(gate_rows: "list[dict]") -> dict:
    """GATED / (GATED + REJECT) over ledger gate rows + a per-error rejection map.

    The rejection breakdown counts *validation_issues entries* (layer/error_type),
    not rows, so a single REJECT carrying two issues contributes to two buckets.
    """
    gated = sum(1 for r in gate_rows if r.get("decision") == "GATED")
    rejected = sum(1 for r in gate_rows if r.get("decision") == "REJECT")
    denom = gated + rejected
    breakdown: "dict[str, int]" = {}
    for r in gate_rows:
        if r.get("decision") != "REJECT":
            continue
        for issue in (r.get("validation_issues") or []):
            key = f"{issue.get('layer', '?')}/{issue.get('error_type', '?')}"
            breakdown[key] = breakdown.get(key, 0) + 1
    return {
        "value": (gated / denom) if denom else 0.0,
        "gated": gated,
        "rejected": rejected,
        "rejection_breakdown": breakdown,
    }


# ── metric 3: roundtrip_rate (回环一致率) ─────────────────────────────────────


def _rehash_config(path: str) -> "tuple[str | None, str]":
    """Independently re-derive a config's genotype_hash from disk.

    Returns ``(hash, "")`` on success, ``(None, reason)`` when the file is gone
    or will not load/graph.  This never trusts the bridge — it re-runs
    ``genotype_hash(to_graph(from_yaml_file(path)))`` from scratch.
    """
    if not path:
        return None, "candidate has no config_path"
    p = Path(path)
    if not p.exists():
        return None, "config file not found"
    try:
        return genotype_hash(to_graph(HarnessConfig.from_yaml_file(p))), ""
    except Exception as exc:  # noqa: BLE001 — any load/graph failure is a fail with a reason
        return None, f"{type(exc).__name__}: {exc}"


def _roundtrip_rate(rounds: "list[dict]") -> dict:
    """Fraction of materialized candidate configs whose re-derived hash matches."""
    total = ok = 0
    failures: "list[dict]" = []
    for r in rounds:
        for c in (r.get("candidates") or []):
            total += 1
            recorded = c.get("genotype_hash", "")
            recomputed, reason = _rehash_config(c.get("config_path", ""))
            if recomputed is not None and recomputed == recorded:
                ok += 1
            else:
                failures.append({
                    "candidate_id": c.get("candidate_id", ""),
                    "config_path": c.get("config_path", ""),
                    "recorded": recorded,
                    "recomputed": recomputed,
                    "reason": reason or "genotype_hash mismatch",
                })
    return {"value": (ok / total) if total else 0.0,
            "matched": ok, "candidates": total, "failures": failures}


# ── metric 4: build_smoke_rate (构建冒烟率) ───────────────────────────────────


def _build_smoke_rate(rounds: "list[dict]") -> dict:
    """Share of evaluated candidates that were not infra_failed (全轮聚合)."""
    evaluated = sum(_i(r, "n_evaluated") for r in rounds)
    infra_ok = sum(1 for r in rounds for c in (r.get("candidates") or [])
                   if not c.get("infra_failed"))
    return {"value": (infra_ok / evaluated) if evaluated else 0.0,
            "infra_ok": infra_ok, "evaluated": evaluated}


# ── metric 5: genotype_diversity (基因型多样性) ───────────────────────────────


def _genotype_diversity(rounds: "list[dict]", gate_rows: "list[dict]") -> dict:
    """Per-round gated diversity + cumulative and proposal-level genotype spread.

    * per_round             — distinct gated genotype_hash / n_gated (report side);
    * cumulative_distinct   — distinct non-empty genotype across GATED gate rows;
    * proposal_level.ratio  — distinct non-empty genotype over ALL gate rows
      (REJECT rows carry no genotype_hash, so a reject-heavy run lowers this).
    """
    per_round = []
    for r in rounds:
        n_gated = _i(r, "n_gated")
        distinct = len({c.get("genotype_hash", "") for c in (r.get("candidates") or [])
                        if c.get("genotype_hash")})
        per_round.append({
            "round_id": r.get("round_id", ""),
            "distinct_genotypes": distinct,
            "n_gated": n_gated,
            "ratio": (distinct / n_gated) if n_gated else 0.0,
        })
    gated_genos = {r.get("genotype_hash", "") for r in gate_rows
                   if r.get("decision") == "GATED" and r.get("genotype_hash")}
    all_distinct = len({r.get("genotype_hash", "") for r in gate_rows if r.get("genotype_hash")})
    n_gate_rows = len(gate_rows)
    return {
        "per_round": per_round,
        "cumulative_distinct_genotypes": len(gated_genos),
        "proposal_level": {
            "distinct_genotypes": all_distinct,
            "gate_rows": n_gate_rows,
            "ratio": (all_distinct / n_gate_rows) if n_gate_rows else 0.0,
        },
    }


# ── metric 6: reward_per_evaluation / reward_per_1k_tokens (单位增益) ──────────


def _reward(rounds: "list[dict]") -> dict:
    """Σ(APPLY pass_rate − that round's parent_pass_rate) over two denominators.

    Denominator口径:
      * reward_per_evaluation — one parent-baseline evaluation per round (both
        arms) PLUS every gated-candidate evaluation → ``rounds + Σ n_evaluated``.
      * reward_per_1k_tokens  — Σ candidate ``measured.tokens`` + Σ
        ``baseline_measured.tokens`` (per 1000).
    With no APPLY the numerator is 0.0, so both rates are 0.0.
    """
    reward_total = 0.0
    n_apply = 0
    tokens = 0
    for r in rounds:
        parent_pr = _f(r, "parent_pass_rate")
        tokens += _i(r.get("baseline_measured") or {}, "tokens")
        for c in (r.get("candidates") or []):
            tokens += _i(c.get("measured") or {}, "tokens")
            if c.get("decision") == "APPLY":
                reward_total += _f(c, "pass_rate") - parent_pr
                n_apply += 1
    n_eval = len(rounds) + sum(_i(r, "n_evaluated") for r in rounds)
    return {
        "reward_total": reward_total,
        "n_apply": n_apply,
        "reward_per_evaluation": (reward_total / n_eval) if n_eval else 0.0,
        "reward_per_1k_tokens": (reward_total * 1000.0 / tokens) if tokens else 0.0,
        "evaluations_incl_baseline": n_eval,
        "tokens": tokens,
    }


# ── metric 7: time_to_first_improvement (首个改进到达) ────────────────────────


def _time_to_first_improvement(rounds: "list[dict]") -> dict:
    """0-based index of the first APPLY round + cumulative wall-clock through it."""
    cumulative = 0.0
    for i, r in enumerate(rounds):
        cumulative += _f(r, "wall_clock_s")
        if any(c.get("decision") == "APPLY" for c in (r.get("candidates") or [])):
            return {"round": i, "round_id": r.get("round_id", ""),
                    "cumulative_wall_clock_s": cumulative, "reason": ""}
    return {"round": None, "round_id": None, "cumulative_wall_clock_s": None,
            "reason": "no APPLY promotion occurred in any round"}


# ── metric 8: holdout_regression_rate (留出集回归率) ───────────────────────────


def _holdout_regression_rate(rounds: "list[dict]") -> dict:
    """Share of holdout ground a promotion lost, summed over every APPLY round.

    THESIS B-arm pre-registered endpoint「留出回归率↓」: how much each promoted
    generation regressed on the DISJOINT holdout set — the read-out against a
    fix-one-break-one treadmill.  The runner measures it ONLY on APPLY rounds
    (:mod:`experiments.variant_pool.rehearsal`), storing the outgoing parent /
    promoted winner per-task holdout outcomes in ``holdout_before`` /
    ``holdout_after``; regression is per task, so the aggregate ``pass_rate``
    alone would not do.

    Per APPLY round: ``regressed`` = tasks the outgoing parent passed and the
    winner then failed; ``unlocked`` = the reverse (parent failed → winner
    passed, reported for context).  The rate is ``Σ regressed / Σ(#tasks the
    outgoing parent passed)`` — the denominator is the holdout ground each
    promotion could have lost, summed over every APPLY round.  Honest ``null``
    (never ``0.0``) when there is nothing to read: no APPLY round at all, or
    APPLY rounds that carry no holdout data (a ``--no-holdout`` run).
    """
    per_apply: "list[dict]" = []
    total_regressed = 0
    total_baseline_passed = 0
    n_apply = 0
    n_with_holdout = 0
    for r in rounds:
        if not any(c.get("decision") == "APPLY" for c in (r.get("candidates") or [])):
            continue
        n_apply += 1
        before = (r.get("holdout_before") or {}).get("per_task") or {}
        after = (r.get("holdout_after") or {}).get("per_task") or {}
        if not before and not after:
            continue                            # APPLY round, holdout not wired
        n_with_holdout += 1
        regressed = sorted(
            t for t, row in before.items()
            if row.get("passed") and not (after.get(t) or {}).get("passed"))
        unlocked = sorted(
            t for t, row in after.items()
            if row.get("passed") and not (before.get(t) or {}).get("passed"))
        total_regressed += len(regressed)
        total_baseline_passed += sum(1 for row in before.values() if row.get("passed"))
        per_apply.append({
            "round_id": r.get("round_id", ""),
            "regressed_tasks": regressed,
            "unlocked_tasks": unlocked,
        })
    if n_apply == 0:
        return {"value": None, "reason": HOLDOUT_NO_APPLY_REASON}
    if n_with_holdout == 0:
        return {"value": None, "reason": HOLDOUT_NOT_WIRED_REASON}
    return {
        "value": (total_regressed / total_baseline_passed)
        if total_baseline_passed else 0.0,
        "regressed": total_regressed,
        "baseline_passed": total_baseline_passed,
        "per_apply": per_apply,
    }


# ── metric 9: selective_retest_savings (选择性重测节省) ─────────────────────────


def _load_footprints(eval_dir: str) -> "dict[str, tuple[set, set]]":
    """Union each task's per-attempt coverage footprint from one eval's
    ``footprints.jsonl`` (written opt-in by the bridge under --collect-footprints).

    Returns ``{task_id: (touched_node_ids, observed_edge_keys)}`` unioned across
    attempts — a union is conservative (wider footprint → more retest → less
    saving claimed).  ``{}`` when the file is absent (footprints not collected)
    or unreadable; malformed lines are skipped, never raised on.
    """
    out: "dict[str, tuple[set, set]]" = {}
    if not eval_dir:
        return out
    p = Path(eval_dir) / "footprints.jsonl"
    if not p.is_file():
        return out
    try:
        text = p.read_text(encoding="utf-8")
    except OSError:
        return out
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if not isinstance(obj, dict) or not obj.get("task_id"):
            continue
        tid = obj["task_id"]
        nodes = set(obj.get("touched_node_ids") or [])
        edges = set(obj.get("observed_edge_keys") or [])
        if tid in out:
            n0, e0 = out[tid]
            out[tid] = (n0 | nodes, e0 | edges)
        else:
            out[tid] = (nodes, edges)
    return out


def _selective_retest_savings(rounds: "list[dict]", gate_rows: "list[dict]") -> dict:
    """Budget a selective retest would save vs. re-evaluating the whole bed.

    THESIS graph-IR read-out.  For each GATED candidate: the danger set of its
    edit (``danger_edge_set`` over ``operator.edits(parent_snapshot)``) is
    intersected with every bed task's PARENT-baseline coverage footprint.  A task
    whose footprint misses the danger set can inherit the parent's score, so the
    per-candidate saving is ``1 − |retest| / |bed|`` — the fraction of the full
    re-evaluation the intersection lets us skip.  The headline ``value`` is the
    mean over every scored gated candidate; per-candidate detail rides alongside.

    Both inputs are read-only artefacts the runner already produced: the parent
    snapshot is re-derived from the round's ``parent_config`` on disk (same basis
    as metric 3's re-hash), and the footprints from the round's
    ``baseline_eval_dir/footprints.jsonl``.  This is the offline reading of the
    same danger∩footprint call :class:`experiments.variant_pool.selective_retest.
    SelectiveRetestEngine` makes live at eval time.

    Granularity guard (read from the data, no flag): a ``proc:*`` danger set is
    invisible to a footprint recorded at hook granularity (the current runtime
    overlay attributes coverage to the hook skeleton, not to individual
    processors — every ``touched_node_ids`` is ``hook:*`` / ``slot:*``), so the
    raw intersection is empty and every candidate reads a spurious ``saving=1``.
    When a round's footprints name no ``proc:*`` node AND a candidate's edit
    anchors on proc nodes, the danger set is instead projected through the HOOKS
    those procs attach to (``ATTACHED_TO``) and intersected there — a conservative
    upper bound (a wildcard/whole-lifecycle processor projects to all 8 hooks →
    saving 0).  Each candidate carries a ``granularity`` of ``"processor"`` (exact
    path) or ``"hook-projected(conservative)"``; a top-level ``note`` flags the
    projection.  Proc-level footprints (a future overlay) route back to the exact
    path automatically.

    Honest ``null`` (never ``0.0``): no footprints collected anywhere, or none of
    the gated candidates could be re-derived to edits.
    """
    gated_by_round: "dict[str, list[dict]]" = {}
    for g in gate_rows:
        if g.get("decision") == "GATED":
            gated_by_round.setdefault(g.get("round_id", ""), []).append(g)

    per_candidate: "list[dict]" = []
    skipped: "list[dict]" = []
    total_savings = 0.0
    n_scored = 0
    footprints_seen = 0
    hook_projected_any = False

    for r in rounds:
        gated = gated_by_round.get(r.get("round_id", ""), [])
        if not gated:
            continue
        footprints = _load_footprints(r.get("baseline_eval_dir", ""))
        if not footprints:
            continue
        footprints_seen += 1
        n_bed = len(footprints)
        # Granularity of THIS round's observation layer, read from the data (not
        # a flag): if no loaded footprint names a single ``proc:*`` node, coverage
        # was recorded at hook granularity — the runtime overlay attributes to the
        # hook skeleton, never to individual processors (see rehearsal_b2).  When
        # a future overlay emits proc-level footprints this flips to False on its
        # own and every candidate takes the exact path below.
        hook_level_obs = not any(
            any(nid.startswith("proc:") for nid in fn)
            for fn, _fe in footprints.values())
        try:
            parent_snap = to_graph(
                HarnessConfig.from_yaml_file(Path(r.get("parent_config", ""))))
        except Exception as exc:  # noqa: BLE001 — a bad parent config skips the round
            skipped.append({"round_id": r.get("round_id", ""),
                            "reason": f"parent snapshot: {type(exc).__name__}: {exc}"})
            continue
        for g in gated:
            cand_id = g.get("candidate_id", "")
            op, reason = parse_proposal({"operator": g.get("operator", ""),
                                         "params": g.get("operator_params") or {}})
            if op is None:
                skipped.append({"candidate_id": cand_id, "reason": reason})
                continue
            try:
                edits = op.edits(parent_snap)
                danger_nodes, danger_edges = danger_edge_set(edits, parent_snap)
            except Exception as exc:  # noqa: BLE001 — inapplicable edit skips the candidate
                skipped.append({"candidate_id": cand_id,
                                "reason": f"danger set: {type(exc).__name__}: {exc}"})
                continue
            # ``proc:*`` nodes this edit anchors on — the processor-level danger
            # set a hook-level footprint can never see.
            affected_procs = {
                nid for e in edits for nid in e.affected_node_ids()
                if nid.startswith("proc:")}
            if hook_level_obs and affected_procs:
                # Conservative hook projection: intersect the footprint with the
                # HOOKS those procs attach to (ATTACHED_TO edges), standing in for
                # the invisible proc nodes.  A wildcard ("*") processor attaches to
                # all 8 processor hooks, so every task's footprint intersects → the
                # whole bed retests → saving 0.  That is the correct reading at hook
                # granularity: a whole-lifecycle processor change must retest all.
                hooks = {e.target_id for e in parent_snap.edges
                         if e.edge_type is EdgeType.ATTACHED_TO
                         and e.source_id in affected_procs}
                retest = sorted(tid for tid, (fn, _fe) in footprints.items()
                                if fn & hooks)
                granularity = "hook-projected(conservative)"
                hook_projected_any = True
            else:
                retest = sorted(
                    tid for tid, (fn, fe) in footprints.items()
                    if intersects_footprint(danger_nodes, danger_edges, fn, fe))
                granularity = "processor"
            savings = 1.0 - (len(retest) / n_bed) if n_bed else 0.0
            total_savings += savings
            n_scored += 1
            per_candidate.append({
                "round_id": r.get("round_id", ""),
                "candidate_id": cand_id,
                "operator": g.get("operator", ""),
                "bed_tasks": n_bed,
                "retest_tasks": retest,
                "savings": savings,
                "granularity": granularity,
            })

    if footprints_seen == 0:
        return {"value": None, "reason": SELECTIVE_NO_FOOTPRINTS_REASON}
    if n_scored == 0:
        return {"value": None, "reason": SELECTIVE_NO_SCORE_REASON, "skipped": skipped}
    result = {
        "value": total_savings / n_scored,
        "candidates_scored": n_scored,
        "per_candidate": per_candidate,
        "skipped": skipped,
    }
    if hook_projected_any:
        result["note"] = SELECTIVE_HOOK_PROJECTION_NOTE
    return result


# ── top-level entry point ────────────────────────────────────────────────────


def compute_process_metrics(report_path: Path, ledger_path: Path) -> dict:
    """Compute the nine process metrics + totals from a report + ledger pair.

    Both paths are read defensively: malformed input is recorded in
    ``skipped_inputs`` and the affected metric degrades to its zero/null
    convention rather than raising.
    """
    skipped: "list[dict]" = []
    report = _load_report(report_path, skipped)
    ledger_rows = _load_ledger(ledger_path, skipped)

    rounds = list(report.get("round_reports") or [])
    gate_rows = [r for r in ledger_rows if r.get("record_kind") == "gate"]

    return {
        "totals": _totals(rounds),
        "parse_rate": _parse_rate(rounds),                         # 1 解析成功率
        "gate_pass_rate": _gate_pass_rate(gate_rows),              # 2 硬门通过率 (S0–S4+Δ8)
        "roundtrip_rate": _roundtrip_rate(rounds),                 # 3 回环一致率
        "build_smoke_rate": _build_smoke_rate(rounds),             # 4 构建冒烟率
        "genotype_diversity": _genotype_diversity(rounds, gate_rows),  # 5 基因型多样性
        "reward": _reward(rounds),                                 # 6 单位评测/千token增益
        "time_to_first_improvement": _time_to_first_improvement(rounds),  # 7 首个改进到达
        "holdout_regression_rate": _holdout_regression_rate(rounds),  # 8 留出集回归率
        "selective_retest_savings": _selective_retest_savings(rounds, gate_rows),  # 9 选择性重测节省
        "skipped_inputs": skipped,
    }


# ── human-readable rendering (ASCII only — safe on any console) ───────────────


def _fmt(v: "float | None") -> str:
    return "null" if v is None else f"{v:.3f}"


def render_text(metrics: dict) -> str:
    """Render the metrics dict as a plain-ASCII table for stdout."""
    t = metrics["totals"]
    out = [
        "HarnessX - nine process metrics",
        "=" * 60,
        (f"totals: rounds={t['rounds']} proposals={t['proposals']} "
         f"gated={t['gated']} evaluated={t['evaluated']} apply={t['apply']} "
         f"cost=${t['cost_usd']:.4f} tokens={t['tokens']} wall={t['wall_clock_s']:.2f}s"),
        "",
    ]

    pr = metrics["parse_rate"]
    out.append(f"1. parse_rate             {_fmt(pr['value'])}"
               f"  [{pr['parsed_rounds']}/{pr['b_rounds']} B-rounds parsed]")

    gp = metrics["gate_pass_rate"]
    out.append(f"2. gate_pass_rate         {_fmt(gp['value'])}"
               f"  [{gp['gated']} GATED / {gp['gated'] + gp['rejected']} gate rows]")
    if gp["rejection_breakdown"]:
        crumbs = ", ".join(f"{k}={v}" for k, v in sorted(gp["rejection_breakdown"].items()))
        out.append(f"     rejections: {crumbs}")

    rt = metrics["roundtrip_rate"]
    out.append(f"3. roundtrip_rate         {_fmt(rt['value'])}"
               f"  [{rt['matched']}/{rt['candidates']} re-hash match]")
    for f in rt["failures"]:
        out.append(f"     fail {f['candidate_id']}: {f['reason']}")

    bs = metrics["build_smoke_rate"]
    out.append(f"4. build_smoke_rate       {_fmt(bs['value'])}"
               f"  [{bs['infra_ok']}/{bs['evaluated']} infra-ok]")

    gd = metrics["genotype_diversity"]
    pl = gd["proposal_level"]
    out.append(f"5. genotype_diversity     cumulative={gd['cumulative_distinct_genotypes']}"
               f"  proposal_level={_fmt(pl['ratio'])} [{pl['distinct_genotypes']}/{pl['gate_rows']}]")
    for row in gd["per_round"]:
        out.append(f"     {row['round_id']}: {row['distinct_genotypes']}/{row['n_gated']}"
                   f" = {_fmt(row['ratio'])}")

    rw = metrics["reward"]
    out.append(f"6. reward_per_evaluation  {_fmt(rw['reward_per_evaluation'])}"
               f"  reward_per_1k_tokens={rw['reward_per_1k_tokens']:.6f}")
    out.append(f"     reward_total={_fmt(rw['reward_total'])} over "
               f"{rw['evaluations_incl_baseline']} evals / {rw['tokens']} tokens"
               f" ({rw['n_apply']} APPLY)")

    tt = metrics["time_to_first_improvement"]
    if tt["round"] is None:
        out.append(f"7. time_to_first_improve  null  ({tt['reason']})")
    else:
        out.append(f"7. time_to_first_improve  round={tt['round']}"
                   f"  wall={tt['cumulative_wall_clock_s']:.2f}s")

    ho = metrics["holdout_regression_rate"]
    if ho["value"] is None:
        out.append(f"8. holdout_regression_rate null  ({ho['reason']})")
    else:
        out.append(f"8. holdout_regression_rate {_fmt(ho['value'])}"
                   f"  [{ho['regressed']}/{ho['baseline_passed']} holdout tasks regressed]")
        for a in ho["per_apply"]:
            if a["regressed_tasks"] or a["unlocked_tasks"]:
                out.append(f"     {a['round_id']}: regressed={a['regressed_tasks']}"
                           f" unlocked={a['unlocked_tasks']}")
    sr = metrics["selective_retest_savings"]
    if sr["value"] is None:
        out.append(f"9. selective_retest_saving null  ({sr['reason']})")
    else:
        head = (f"9. selective_retest_saving {_fmt(sr['value'])}"
                f"  [{sr['candidates_scored']} gated candidate(s) scored]")
        if sr.get("note"):
            head += "  (hook-projected upper bound)"
        out.append(head)
        for c in sr["per_candidate"]:
            proj = c.get("granularity", "processor").startswith("hook")
            tag = " hook-projected" if proj else ""
            out.append(f"     {c['round_id']}/{c['candidate_id']} ({c['operator']}):"
                       f" saving={_fmt(c['savings'])}"
                       f" [{len(c['retest_tasks'])}/{c['bed_tasks']} retest{tag}]")
        if sr.get("note"):
            out.append(f"     note: {sr['note']}")

    out.append("")
    out.append(f"skipped_inputs: {len(metrics['skipped_inputs'])}")
    for s in metrics["skipped_inputs"]:
        out.append(f"     {s}")
    return "\n".join(out)


# ── CLI ──────────────────────────────────────────────────────────────────────


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m experiments.variant_pool.process_metrics",
        description="Nine process metrics from a rehearsal report.json + shadow ledger JSONL.",
    )
    p.add_argument("--report", required=True, help="rehearsal report.json")
    p.add_argument("--ledger", required=True, help="shadow ledger JSONL")
    p.add_argument("--out", default=None, help="also write the metrics dict as JSON here")
    return p


def main(argv: "list[str] | None" = None) -> int:
    args = _build_parser().parse_args(argv)
    metrics = compute_process_metrics(Path(args.report), Path(args.ledger))
    print(render_text(metrics))
    if args.out:
        Path(args.out).write_text(
            json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
