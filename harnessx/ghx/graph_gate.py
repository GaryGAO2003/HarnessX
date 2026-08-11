# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""The sixth gate — graph existence check on a candidate's replay (module G2, piece 3).

Official Stage 4 runs five deterministic gates (structure / novelty / canonicalize
/ counterfactual / replay) before a candidate ships.  The replay gate boots the
candidate's config through the real run loop; with ``HARNESSX_GHX_UNFOLD=1`` that
run itself records an unfolded graph U.  This gate adds one question the other five
never ask: **did the candidate's declared mechanical signature actually fire at
runtime?**  A candidate that edits a ``tool:<name>`` into the config but whose
``tool:<name>`` node never appears in the replay U is *edited but never ran* — a
no-op ship in disguise — and is refused, with the evidence recorded.

Honesty contract (the whole point — a gate nobody has watched fail is not a gate):

* The gate REFUSES only when it actually checked — the U was available AND the
  signature is graph-representable AND the ``tool:<name>`` node is absent.
  ``verdict.checked`` is ``True`` only in that answered case.
* When the replay U is unavailable (``replay_model`` is None so replay was skipped,
  unfold was off, or the U file is missing) the gate records *why it could not
  check* and passes the candidate through unchanged — an unverifiable candidate is
  today's behaviour, never a new rejection.  ``verdict.checked`` is ``False`` and
  ``verdict.ok`` is ``True``.  A gate that set ``checked=True`` on this path would be
  claiming an answer it does not have; that is the exact failure this gate guards
  against.

Resolver contract.  The gate is handed a U per candidate through a caller-supplied
resolver (same shape as G1's evidence resolver: ``candidate_id -> UnfoldedGraph |
path | None``).  The caller must resolve a U in which the signature *would* fire if
the mechanism works — a real task-replay U, not the trivial synthetic-smoke U where
a newly-added tool is never exercised — or ``None`` to take the pass-through path.
The gate never fabricates a U and never guesses provenance.

Flag ``HARNESSX_GHX_GRAPH_GATE`` (call-time read, default off).  Off → the wrapper
is a pure pass-through that delegates to ``orchestrator.run_round`` unchanged and
writes nothing, exactly like the vendored round.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

from .attribution_graph import BACKEND_GRAPH, check_signature_in_u, infer_signature
from ..graph.unfold import UnfoldedGraph, load_unfolded

_LOG = logging.getLogger("harnessx.ghx.graph_gate")
_ENABLE_VALUES = frozenset({"1", "true", "on", "yes"})


def graph_gate_enabled() -> bool:
    """True when the sixth (graph-existence) gate should run.

    Read at call time (never cached at import), default OFF, so a run pays nothing
    unless ``HARNESSX_GHX_GRAPH_GATE`` is explicitly set — same convention as the
    other GHX flags.
    """
    return os.environ.get("HARNESSX_GHX_GRAPH_GATE", "").strip().lower() in _ENABLE_VALUES


@dataclass(frozen=True)
class GraphGateVerdict:
    """One candidate's graph-gate outcome.

    ``ok`` — may this candidate still ship (True unless refused).
    ``checked`` — did the graph actually answer? True ONLY when a U was available and
    the signature was graph-representable. False on every pass-through path, so a
    reader can never mistake "unverifiable, let through" for "verified, passed".
    ``reason`` — human-readable account. ``label``/``backend`` mirror the signature
    result; ``tool_name``/``count``/``min_calls`` carry the evidence.
    """

    ok: bool
    checked: bool
    reason: str
    label: str = ""
    backend: str = ""
    tool_name: str = ""
    count: int = 0
    min_calls: int = 0

    @property
    def refused(self) -> bool:
        return not self.ok


def check_graph_gate(signature: dict | None, u: UnfoldedGraph | None) -> GraphGateVerdict:
    """The gate's pure decision for one candidate.

    - ``u is None`` → pass-through, ``checked=False``: the replay U is unavailable, so
      the gate cannot verify and lets the candidate through (unchanged behaviour).
    - signature not graph-representable (prompt/config bucket, processor_invocation,
      unknown type) → pass-through, ``checked=False``: nothing for this backend to
      verify.
    - graph-representable and fired → pass, ``checked=True``.
    - graph-representable and absent → REFUSE, ``checked=True``: edited but never ran.
    """
    if u is None:
        return GraphGateVerdict(
            ok=True,
            checked=False,
            reason=(
                "replay U unavailable (replay skipped / unfold off / U file missing) — "
                "cannot verify; passing candidate through unchanged"
            ),
        )

    result = check_signature_in_u(signature, u)
    if result.backend != BACKEND_GRAPH:
        return GraphGateVerdict(
            ok=True,
            checked=False,
            label=result.label,
            backend=result.backend,
            reason=f"nothing graph-representable to verify ({result.reason}) — passing through",
        )

    if result.fired:
        return GraphGateVerdict(
            ok=True,
            checked=True,
            label=result.label,
            backend=result.backend,
            tool_name=result.tool_name,
            count=result.count,
            min_calls=result.min_calls,
            reason=f"signature fired in replay U — {result.reason}",
        )

    return GraphGateVerdict(
        ok=False,
        checked=True,
        label=result.label,
        backend=result.backend,
        tool_name=result.tool_name,
        count=result.count,
        min_calls=result.min_calls,
        reason=f"REFUSED — edited but never ran: {result.reason}",
    )


def _coerce_u(resolved) -> UnfoldedGraph | None:
    """Normalise a resolver result (``UnfoldedGraph`` | path-like | ``None``).

    A missing file reads as *unavailable* (``None``), never as an empty graph — the
    same discipline the G1 evidence resolver uses.
    """
    if resolved is None:
        return None
    if isinstance(resolved, UnfoldedGraph):
        return resolved
    path = Path(resolved)
    if not path.exists():
        return None
    return load_unfolded(path)


def _gate_dir(run_dir, round_n: int) -> Path:
    return Path(run_dir) / f"R{round_n}" / "graph_evidence" / "gate"


def _render_gate_evidence(candidate_id: str, verdict: GraphGateVerdict) -> str:
    if verdict.refused:
        head = "REFUSED (edited but never ran)"
    elif verdict.checked:
        head = "PASSED (signature fired)"
    else:
        head = "PASSED THROUGH (unverifiable — not checked)"
    lines = [
        f"# Graph-existence gate — {candidate_id}",
        "",
        f"**{head}**",
        "",
        f"- ok: {verdict.ok}",
        f"- checked: {verdict.checked}",
        f"- backend: {verdict.backend or '(n/a)'}",
        f"- label: {verdict.label or '(n/a)'}",
    ]
    if verdict.tool_name:
        lines.append(f"- tool: `tool:{verdict.tool_name}`")
        lines.append(f"- invocations in replay U: {verdict.count} (floor {verdict.min_calls})")
    lines += [
        "",
        f"{verdict.reason}",
        "",
        "`checked=false` means the graph did not answer (unavailable U or a signature with "
        "no tool-node representation); the candidate was let through, not verified.",
        "",
    ]
    return "\n".join(lines)


def _write_gate_evidence(run_dir, round_n: int, candidate_id: str, verdict: GraphGateVerdict) -> str:
    out_dir = _gate_dir(run_dir, round_n)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{candidate_id}.md"
    path.write_text(_render_gate_evidence(candidate_id, verdict), encoding="utf-8")
    return str(path)


def _shipped_from_result(result: dict) -> list[str]:
    shipped = result.get("shipped_cids")
    if shipped:
        return list(shipped)
    single = result.get("shipped_cid")
    return [single] if single else []


def _signature_for_candidate(candidates_info: dict, cid: str) -> dict | None:
    """Infer the candidate's mechanical signature from its manifest, reusing the
    vendored inference so it matches what the official attributor would check."""
    info = candidates_info.get(cid)
    if not info:
        return None
    manifest_path = info[0]
    try:
        from ..aegis.agents.evolver import parse_candidate_manifest

        fm, _body = parse_candidate_manifest(Path(manifest_path).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — unreadable manifest → no signature → pass-through
        return None
    if not isinstance(fm, dict):
        return None
    return infer_signature(fm.get("bucket"), fm, fm.get("attribution_signature"))


def apply_graph_gate_to_stage4(
    result: dict,
    *,
    candidates_info: dict,
    u_resolver,
    run_dir=None,
    round_n: int | None = None,
    write_evidence: bool = True,
) -> dict:
    """Apply the sixth gate to a Stage-4 result, refusing edited-but-never-ran ships.

    For each shipped candidate: infer its signature (from its manifest via the
    vendored inference), resolve its replay U, and run :func:`check_graph_gate`.
    A refusal drops the candidate from ``shipped_cids`` and records a
    ``graph_existence`` verdict in ``gate_results[cid]`` (so the audit trail shows
    the sixth gate alongside the five vendored ones).  Candidates that pass, or that
    cannot be checked (U unavailable / no graph-representable signature), are kept.

    Returns a NEW result dict when anything was refused; the original (unmodified)
    when nothing shipped or nothing was refused.  Never raises on a per-candidate
    problem — an unresolvable candidate takes the pass-through path.
    """
    shipped = _shipped_from_result(result)
    if not shipped:
        return result

    gate_results = dict(result.get("gate_results") or {})
    kept: list[str] = []
    refusals: list[dict] = []

    for cid in shipped:
        signature = _signature_for_candidate(candidates_info, cid)
        try:
            u = _coerce_u(u_resolver(cid)) if u_resolver else None
        except Exception as exc:  # noqa: BLE001 — resolver failure is an unavailable U
            _LOG.warning("graph gate: U resolver failed for %s (treating as unavailable): %s", cid, exc)
            u = None
        verdict = check_graph_gate(signature, u)

        if write_evidence and run_dir is not None and round_n is not None:
            _write_gate_evidence(run_dir, round_n, cid, verdict)

        if verdict.ok:
            kept.append(cid)
            continue

        gr = dict(gate_results.get(cid) or {})
        gr["graph_existence"] = verdict  # has .ok / .reason — the audit reads both
        gate_results[cid] = gr
        refusals.append({"cid": cid, "reason": verdict.reason, "label": verdict.label})

    if not refusals:
        return result

    new_result = dict(result)
    new_result["shipped_cids"] = kept
    new_result["shipped_cid"] = kept[0] if kept else None
    new_result["gate_results"] = gate_results
    new_result["graph_gate_refusals"] = refusals
    if not kept:
        # No survivors: reuse the vendored "all_candidates_*" reason prefix so the
        # orchestrator's refuted-signature path (which keys off that prefix) records
        # the refused candidates like any other gate failure.
        new_result["reason"] = "all_candidates_failed_graph_gate"
    return new_result


async def run_round_with_graph_gate(
    orchestrator,
    *,
    u_resolver,
    parent_config_path=None,
    gate_enabled: bool | None = None,
    write_evidence: bool = True,
    **run_round_kwargs,
):
    """Flag-gated wrapper: run a round with the sixth gate wired in before commit.

    Off (default) → a pure pass-through: it writes nothing, wires nothing, and simply
    awaits ``orchestrator.run_round(**run_round_kwargs)`` — the vendored round, byte-
    for-byte behaviour (test: a delegate-called spy proves it).

    On → the narrowest seam that forks zero vendored logic: for the duration of this
    one call, the ``run_stage_4`` name in the vendored orchestrator module is wrapped
    (restored in a ``finally``).  The wrapper delegates to the real ``run_stage_4``,
    then — while the round is still *before* its commit bookkeeping — writes each
    candidate's mutation surface (piece 2) and applies the graph gate to the shipped
    set (piece 3).  The vendored ``run_round`` then composes / journals / scores the
    *filtered* ``shipped_cids``.  Vendored bytes are untouched; the orchestrator is
    neither subclassed nor its ``run_round`` duplicated.

    ``u_resolver`` maps a candidate id to its replay U (``UnfoldedGraph`` | path |
    ``None``); see the module docstring for the resolver contract.
    ``parent_config_path`` (usually the same as the ``current_config_path`` run-round
    kwarg) enables the per-candidate mutation-surface files; omit to skip them.
    """
    if gate_enabled is None:
        gate_enabled = graph_gate_enabled()
    if not gate_enabled:
        return await orchestrator.run_round(**run_round_kwargs)

    from .candidate_surface import materialize_candidate_surfaces
    import harnessx.aegis.orchestrator as _orch_mod

    run_dir = getattr(orchestrator, "run_dir", None)
    original_run_stage_4 = _orch_mod.run_stage_4

    async def _wrapped_run_stage_4(**s4_kwargs):
        stage_4 = await original_run_stage_4(**s4_kwargs)
        candidates_info = s4_kwargs.get("candidates_info") or {}
        round_n = s4_kwargs.get("round_n")
        # Piece 2: per-candidate mutation surface for every candidate (not only the
        # shipped ones) — evidence for readers, independent of the ship decision.
        if parent_config_path is not None and run_dir is not None and round_n is not None:
            try:
                materialize_candidate_surfaces(
                    run_dir,
                    round_n,
                    parent_config_path,
                    {cid: info[1] for cid, info in candidates_info.items()},
                )
            except Exception as exc:  # noqa: BLE001 — evidence write must never break a round
                _LOG.warning("graph gate: candidate-surface materialisation failed (non-fatal): %s", exc)
        # Piece 3: refuse edited-but-never-ran ships before the round commits them.
        try:
            stage_4 = apply_graph_gate_to_stage4(
                stage_4,
                candidates_info=candidates_info,
                u_resolver=u_resolver,
                run_dir=run_dir,
                round_n=round_n,
                write_evidence=write_evidence,
            )
        except Exception as exc:  # noqa: BLE001 — a crashing gate fails open (today's behaviour)
            _LOG.warning("graph gate: apply failed (passing stage-4 result through): %s", exc)
        return stage_4

    _orch_mod.run_stage_4 = _wrapped_run_stage_4
    try:
        return await orchestrator.run_round(**run_round_kwargs)
    finally:
        _orch_mod.run_stage_4 = original_run_stage_4


__all__ = [
    "graph_gate_enabled",
    "GraphGateVerdict",
    "check_graph_gate",
    "apply_graph_gate_to_stage4",
    "run_round_with_graph_gate",
]
