# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""Batch 4c Item 1 — the IV-3..IV-12 structure gate (``--structure-invariants``).

Faithful port of the invariants in
``upstream/feat/aegis:harnessx/aegis/gates/structure.py`` that our existing
manifest completeness check does NOT already enforce. The official gate is the
FIRST (cheap-first) gate in the AEGIS chain; here it is wired as the first
pre-flight check in ``_run_paper_candidate_pipeline``.

What our pipeline already enforces (so this module does NOT duplicate it)
------------------------------------------------------------------------
:meth:`experiments.variant_pool.manifest.ChangeManifest.validate_complete` — run
at gate stage 1 on every produced candidate — already covers:

* **IV-3 (keys)**: ``candidate_id`` (+ ``C-R<round>-<NN>`` regex), ``bucket``
  (+ enum), non-empty ``file_changes`` (+ ``{path, action, diff_summary}`` shape),
  ``predicted_impact`` (≥1 predicted flip).
* **IV-8 (structured)**: ``capability_evidence`` entries are ``{type, claim,
  evidence}`` non-empty triples with a valid ``type`` — required for the code
  buckets (``tools``/``processor``); ``prompt``/``config`` are exempt by our
  manifest.py's deliberate design (module docstring there).

So this module ports only the *net-new* invariants:

* **IV-3 (body)**: the ``## Failure Evidence`` section + ≥1 evidence anchor in the
  manifest body prose — reusing the M-49 anchor validator (``_parse_digest_anchors``),
  injected so the exact same anchor grammar is used (no duplicate regex).
* **IV-8 (slot_type dispatch)**: ``explorer`` bypasses all evidence checks right
  after the required keys; ``legacy`` relaxes IV-8/IV-9/IV-11/IV-12 (keeps the
  body check). Our manifest schema has NO ``slot_type``, so every produced
  candidate defaults to ``regular`` (full checks); the dispatch is ported
  faithfully and exercised directly by tests.
* **IV-9**: bucket ↔ file-extension consistency (union of per-bucket allowlists
  for our list-valued ``bucket``, the 4b deviation).
* **IV-11**: prior-round Critic ``strategy_concern`` exploration enforcement.
* **IV-12**: ``iterates_from`` lineage validation.

1:1 deviations (documented, faithful-where-possible)
----------------------------------------------------
* **Body-dependent checks are conditional on a body being available.** Our
  ``CandidateArtifact`` does not retain manifest-body prose; the pipeline sources
  it from ``_meta_scratch/manifest.yaml`` (front-matter + prose) when present. A
  repo-journal candidate adapted from a journal entry has no manifest file and
  therefore no body carrier. When ``body_md`` is empty the body-dependent
  invariants (IV-3 body, IV-11 justification section, IV-12 body citation) are
  reported N/A rather than rejecting for a carrier the pipeline never produced.
  When a body IS present (paper-manifest candidates), all body checks run
  faithfully. This mirrors the official contract wherever the carrier exists.
* **IV-4 / IV-6 are N/A.** The official gate also validates a Critic *verdict*
  file (``evidence_anchors`` frontmatter, IV-4) and a *decision-chain* file
  (``ship_ranking`` ids exist, IV-6). Our LLM Critic is a bare JSON completion:
  it emits no per-candidate ``verdicts/V-*.md`` with an ``evidence_anchors``
  frontmatter, and its ranking flows through ``ranked_for_gate`` (which contains
  only real candidates by construction, so "cited id exists" is vacuously true).
  Rather than invent those carriers, IV-4/IV-6 are left unported.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any


@dataclass
class GateResult:
    """Mirror of the official ``structure.GateResult``."""

    ok: bool
    reason: str = ""


# ---------------------------------------------------------------------------
# IV-9 — bucket ↔ file-extension consistency
# ---------------------------------------------------------------------------
#: Faithful copy of the official ``_ALLOWED_EXTENSIONS`` (structure.py L119-124).
_ALLOWED_EXTENSIONS: dict[str, tuple[str, ...]] = {
    "config": (".yaml", ".yml"),
    "prompt": (".md", ".yaml", ".yml"),
    "processor": (".py", ".yaml", ".yml"),
    "tools": (".py", ".yaml", ".yml"),
}


def _normalize_bucket(bucket: Any) -> list[str]:
    """Normalize a manifest bucket value to a list of strings.

    Faithful copy of the official helper (structure.py L127-140): accepts a
    scalar ``str`` (legacy) or a ``list[str]`` (our list-valued bucket, the 4b
    deviation). Empty / invalid inputs return ``[]``.
    """
    if bucket is None:
        return []
    if isinstance(bucket, str):
        return [bucket] if bucket else []
    if isinstance(bucket, (list, tuple)):
        return [str(b) for b in bucket if b]
    return []


def _check_bucket_file_consistency(bucket: Any, file_changes: Sequence[Any]) -> str | None:
    """IV-9 — ensure ``file_changes`` extensions match the declared bucket(s).

    Faithful port of the official ``_check_bucket_file_consistency`` (structure.py
    L143-197). For a list-valued bucket the allowed-extension set is the UNION of
    the per-bucket allowlists (e.g. ``[prompt, processor]`` permits ``.md`` +
    ``.py`` + ``.yaml``). Returns an ``IV-9``-tagged error message, or ``None``.
    """
    buckets = _normalize_bucket(bucket)
    if not buckets or not file_changes:
        return None
    ok_ext_set: set[str] = set()
    unknown_bucket = False
    for b in buckets:
        exts = _ALLOWED_EXTENSIONS.get(b)
        if exts is None:
            unknown_bucket = True
            continue
        ok_ext_set.update(exts)
    if unknown_bucket and not ok_ext_set:
        return None  # all unknown — leave to future extension
    violations: list[str] = []
    for fc in file_changes:
        if not isinstance(fc, dict):
            continue
        path = str(fc.get("path", ""))
        if not path:
            continue
        if path.endswith(("__pycache__",)):
            continue
        lower = path.lower()
        if not any(lower.endswith(ext) for ext in ok_ext_set):
            violations.append(f"{path}")
    if violations:
        return (
            f"IV-9 bucket={buckets!r} file_changes include paths outside allowed "
            f"extensions {sorted(ok_ext_set)}: {violations[:3]}"
        )
    return None


# ---------------------------------------------------------------------------
# IV-11 — prior-round strategy_concern exploration enforcement
# ---------------------------------------------------------------------------
_INFEASIBLE_MARKER = "## Why flagged direction is infeasible"


def _check_exploration_response(
    bucket: Any,
    body_md: str,
    strategy_concern_flagged: set[str] | None,
) -> str | None:
    """IV-11 — if the prior Critic flagged a bucket, the candidate must target it
    or carry a ``## Why flagged direction is infeasible`` section (≥50 chars).

    Faithful port of the official ``_check_exploration_response`` (structure.py
    L200-259). ``strategy_concern_flagged`` is the set of bucket names the
    previous round's Critic flagged; ``None`` / empty → no-op.

    Body dependency (documented deviation): the justification-section path is
    only checkable when ``body_md`` is present. When a candidate does NOT target
    the flagged bucket and no body is available, this returns ``None`` (N/A)
    rather than rejecting — the bucket-target path (which needs no body) still
    runs faithfully.
    """
    if not strategy_concern_flagged:
        return None
    candidate_buckets = set(_normalize_bucket(bucket))
    if candidate_buckets & strategy_concern_flagged:
        return None  # candidate targets at least one flagged bucket
    if not body_md:
        return None  # no body carrier: justification section is N/A (see deviation)
    if _INFEASIBLE_MARKER not in body_md:
        flagged_list = sorted(strategy_concern_flagged)
        return (
            f"IV-11 strategy_concern flagged bucket(s) {flagged_list} not "
            f"targeted by this candidate (bucket={sorted(candidate_buckets) or '?'}) "
            f"and no '{_INFEASIBLE_MARKER}' body section present"
        )
    idx = body_md.find(_INFEASIBLE_MARKER)
    tail = body_md[idx + len(_INFEASIBLE_MARKER):]
    next_hdr = tail.find("\n## ")
    section_body = tail[:next_hdr] if next_hdr != -1 else tail
    if len(section_body.strip()) < 50:
        return (
            f"IV-11 '{_INFEASIBLE_MARKER}' section too short — must contain "
            f"concrete evidence showing the direction is genuinely unreachable"
        )
    return None


# ---------------------------------------------------------------------------
# IV-12 — iterates_from lineage validation
# ---------------------------------------------------------------------------
def _check_iterates_from(
    iterates_from: Any,
    body_md: str,
    prior_ships: Mapping[str, Any] | None,
    current_round: int | None,
) -> str | None:
    """IV-12 — validate a manifest's ``iterates_from`` lineage claim.

    Faithful port of the official ``_check_iterates_from`` (structure.py
    L262-320). Checks, when ``iterates_from`` is set:

    * it is a non-empty string;
    * (only when ``prior_ships`` is supplied) the target exists in the ship
      ledger, is from a round strictly before ``current_round``, and has not been
      superseded by an earlier iterate;
    * (only when a ``body_md`` is available) the body cites the target id or its
      ``ship_outcomes``/``hit_rate`` evidence.

    No-op when ``iterates_from`` is absent (check-only-when-present) and — for the
    ledger sub-checks — when ``prior_ships`` is ``None`` (the official's own
    back-compat path, and the path our wiring takes: we have no ship-outcomes
    ledger threaded to this seam, so ``iterates_from`` is validated as a
    non-empty string only).
    """
    target = iterates_from
    if not target:
        return None
    if not isinstance(target, str) or not target.strip():
        return "IV-12 iterates_from must be a non-empty string"
    if prior_ships is None:
        return None
    info = prior_ships.get(target)
    if info is None:
        return f"IV-12 iterates_from target {target!r} not found in ship ledger"
    tgt_round = info.get("round")
    if current_round is not None and tgt_round is not None:
        if int(tgt_round) >= int(current_round):
            return (
                f"IV-12 iterates_from target {target!r} is from round {tgt_round}, "
                f"must be < current round {current_round}"
            )
    superseder = info.get("superseded_by")
    if superseder:
        return (
            f"IV-12 iterates_from target {target!r} already superseded by "
            f"{superseder!r}"
        )
    if body_md:
        body_lower = body_md.lower()
        if (
            target.lower() not in body_lower
            and "hit_rate" not in body_lower
            and "ship_outcomes" not in body_lower
        ):
            return (
                f"IV-12 iterates_from={target!r} requires the manifest body to cite "
                f"the target ship id or its ship_outcomes evidence"
            )
    return None


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------
def validate_candidate_structure(
    manifest: Any,
    body_md: str,
    *,
    slot_type: str = "regular",
    strategy_concern_flagged: set[str] | None = None,
    prior_ships: Mapping[str, Any] | None = None,
    current_round: int | None = None,
    anchor_parser: Callable[[str], Sequence[Any]] | None = None,
) -> GateResult:
    """Run the net-new IV-3(body)/IV-8(dispatch)/IV-9/IV-11/IV-12 checks.

    ``manifest`` is a
    :class:`~experiments.variant_pool.manifest.ChangeManifest` (its
    ``bucket`` / ``file_changes`` / ``iterates_from`` are read). The required-keys
    (IV-3) and structured ``capability_evidence`` (IV-8) checks are NOT repeated
    here — ``ChangeManifest.validate_complete`` already ran them at gate stage 1.

    ``slot_type`` (official IV-8 dispatch): ``explorer`` bypasses everything below
    the required keys; ``legacy`` relaxes IV-8/IV-9/IV-11/IV-12 but keeps the body
    check; ``regular`` (our default — no ``slot_type`` in our schema) runs all.

    ``anchor_parser`` is the injected M-49 ``_parse_digest_anchors`` (same anchor
    grammar) used for the IV-3 body anchor check; when it is ``None`` the anchor
    sub-check is skipped (the section check still runs).
    """
    if slot_type == "explorer":
        # Official: explorer returns OK right after the required-keys check
        # (which validate_complete already performed).
        return GateResult(ok=True)

    if slot_type != "legacy":
        # IV-9 — bucket/file-extension consistency (manifest-level; no body).
        bad = _check_bucket_file_consistency(
            getattr(manifest, "bucket", None), getattr(manifest, "file_changes", ()) or ()
        )
        if bad:
            return GateResult(ok=False, reason=f"{bad} (bucket-file mismatch)")

        # IV-11 — exploration enforcement against the prior Critic's concern.
        iv11 = _check_exploration_response(
            getattr(manifest, "bucket", None), body_md, strategy_concern_flagged
        )
        if iv11:
            return GateResult(ok=False, reason=iv11)

        # IV-12 — iterates_from lineage.
        iv12 = _check_iterates_from(
            getattr(manifest, "iterates_from", None), body_md, prior_ships, current_round
        )
        if iv12:
            return GateResult(ok=False, reason=iv12)

    # IV-3 (body) — runs for regular AND legacy, only when a body carrier exists.
    if body_md:
        if "## Failure Evidence" not in body_md:
            return GateResult(ok=False, reason="IV-3 missing Failure Evidence section")
        if anchor_parser is not None and not anchor_parser(body_md):
            return GateResult(
                ok=False, reason="IV-3 candidate manifest body has zero evidence anchors"
            )
    return GateResult(ok=True)
