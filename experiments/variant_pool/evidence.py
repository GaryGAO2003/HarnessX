# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""W25 — the persistent evidence chain behind the Digester.

Without this module the Critic, attribution checking and auditability are all
suspended in mid-air (SPEC §6.1, Codex critique 3), which is why it is M1
mandatory rather than optional.

What the paper gives us
-----------------------
The Digester's job, §4.3 p.10 verbatim: a single GAIA iteration "generates ~10M
tokens of raw traces"; the Digester "compresses each task's traces into a
structured per-task summary: binary outcome, failure category (if any),
implicated component identifiers, and supporting evidence excerpts. It also
provides cross-iteration continuity: each task's summary links to its history
of prior outcomes and shipped edits, enabling the Planner to distinguish
persistent failures from transient noise." :class:`TaskDigest` is that summary
and :meth:`EvidenceStore.attach_prior_history` is that link.

The on-disk layout is §E.1 p.43::

    runs/<run_name>/data/
      |-- task_history.jsonl      # one line per (round, task)
      |-- ship_outcomes.json      # one entry per historical ship
      `-- rejected_candidates.jsonl

**Scope of this batch.** The paper does not publish the Digester prompt
(appendix F.1 lists it as withheld), so the LLM compression instruction has to
be rebuilt by us and recorded as ours — that is batch C. This module therefore
builds only the **data structures and their persistence**: what a digest
contains, where it is written, and how ship outcomes are scored. Nothing here
calls a model.

Ours — the variant dimension
----------------------------
``task_history.jsonl`` is, verbatim, "one line per (round, task)": the paper's
artifact layout has no variant dimension anywhere (no ``variants/``, no
``V<k>/``; report §3.5 warning). Under variant isolation the same task can be
carried by different variants in different rounds, so a digest that does not say
*which variant produced it* cannot be read back. :attr:`TaskDigest.variant_id`
is that extension, and SPEC §6.6 records per-variant persistence as an explicit
design choice.

Hit-rate
--------
:meth:`EvidenceStore.hit_rate` is the number the Critic's portfolio audit runs
on (SPEC §6.5 rule 1: "same lever in >=2 of the last 3 rounds and cumulative
hit_rate < 0.4 -> ban the lever"), and the same statistic the paper reports as
lever effectiveness ("tasks flipped / predicted", Figure 12c p.43). The worked
example fixes the arithmetic: C-R10-02 predicted 5 unlocks plus 2 stabilises and
five of those seven flipped -> 0.71 (p.37). A ship with an empty prediction has
an undefined hit-rate, not a zero one, and cannot count as evidence against its
lever.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

#: Critic portfolio-audit rule 1 (§3.4 p.33-34 / SPEC §6.5). The window and the
#: repeat count are the paper's ("last 3 rounds", ">=2 times"); so is the 0.4
#: threshold. Whether the rate is pooled or averaged is ours — see
#: :meth:`EvidenceStore.hit_rate`.
LEVER_BAN_WINDOW = 3
LEVER_BAN_MIN_SHIPS = 2
LEVER_BAN_HIT_RATE = 0.4


@dataclass
class TaskDigest:
    """One task's compressed evidence for one round (§4.3 p.10; SPEC §6.1).

    Field order differs from the SPEC listing only because ``variant_id`` is
    required and has to precede the defaulted fields.

    ``outcome`` is the pass@2 pair ``(n_pass, n_att)`` — the paper's "binary
    outcome" is pass/fail, but under pass@2 the pair is what distinguishes
    ``PARTIAL_PASS`` from a clean pass, which the manifest categories need
    (M17, :func:`.manifest.impact_category`).
    """

    task_id: str
    round_idx: int
    variant_id: str
    outcome: tuple[int, int]
    #: Failure cluster label, e.g. GAIA's blocked-source / reasoning /
    #: figure-visual / doc-table-parse / scope-ambiguity (appendix D.1 p.37).
    failure_category: str | None = None
    #: hook / processor / tool identifiers the failure implicates.
    implicated_components: list[str] = field(default_factory=list)
    #: Trace anchors, e.g. ``trajectories/db4fd70a_r0.jsonl#step_0`` (p.36).
    evidence_anchors: list[str] = field(default_factory=list)
    #: Cross-round continuity, filled by
    #: :meth:`EvidenceStore.attach_prior_history`.
    prior_history: list[dict[str, Any]] = field(default_factory=list)
    #: v6 step 1 — ON-GRAPH attribution: processor/tool node ids
    #: (``proc:<slug>`` / ``tool:<name>``) taken from the graph-fed Digester's
    #: causal cone. Kept PARALLEL to :attr:`implicated_components` (rather than
    #: mixed into it) so the graph ids never dilute that field's controlled
    #: off-graph prose vocabulary (``tools/<name>``, ``processor/<name>``,
    #: ``prompt/<section>``, ``environment``, ``model_capability``) that the LLM
    #: Digester sanitizes and downstream readers join verbatim. Empty by default,
    #: so a text-path (no-cone) digest is byte-identical to today.
    implicated_nodes: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        n_pass, n_att = self.outcome
        if n_att < 0 or n_pass < 0:
            raise ValueError(f"{self.task_id}: negative rollout counts {self.outcome}")
        if n_pass > n_att:
            raise ValueError(f"{self.task_id}: n_pass exceeds n_att {self.outcome}")

    @property
    def solved(self) -> bool:
        """pass@k semantics: one passing rollout is enough (§6.1 p.15)."""
        return self.outcome[0] >= 1

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "task_id": self.task_id,
            "round_idx": self.round_idx,
            "variant_id": self.variant_id,
            "outcome": list(self.outcome),
            "failure_category": self.failure_category,
            "implicated_components": list(self.implicated_components),
            "evidence_anchors": list(self.evidence_anchors),
            "prior_history": list(self.prior_history),
        }
        # Emitted ONLY when populated, so a text-path digest serializes
        # byte-identically to pre-v6 JSONL (the key is simply absent).
        if self.implicated_nodes:
            payload["implicated_nodes"] = list(self.implicated_nodes)
        return payload

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TaskDigest:
        return cls(
            task_id=data["task_id"],
            round_idx=data["round_idx"],
            variant_id=data["variant_id"],
            outcome=tuple(data["outcome"]),  # type: ignore[arg-type]
            failure_category=data.get("failure_category"),
            implicated_components=list(data.get("implicated_components", [])),
            evidence_anchors=list(data.get("evidence_anchors", [])),
            prior_history=list(data.get("prior_history", [])),
            implicated_nodes=list(data.get("implicated_nodes", [])),
        )


@dataclass
class ShipOutcome:
    """One shipped edit and what it actually did (``ship_outcomes.json``, §E.1).

    The manifest predicted; this records what happened. ``predicted_flips``
    comes straight from :meth:`.manifest.PredictedImpact.predicted_flips` and
    ``realized_flips`` from the next round's results, so the pair is exactly the
    falsifiability test of p.35.

    ``levers`` is the shipped candidate's ``bucket`` list. A composite ship such
    as C-R10-02 (``[tools, prompt, config]``) counts towards **all three**
    levers in the portfolio audit: the paper never says how to attribute a
    multi-bucket ship, and splitting credit would require a per-bucket
    counterfactual that does not exist. Ours; recorded as such.
    """

    candidate_id: str
    round_idx: int
    variant_id: str
    levers: list[str]
    predicted_flips: list[str] = field(default_factory=list)
    predicted_at_risk: list[str] = field(default_factory=list)
    realized_flips: list[str] = field(default_factory=list)
    realized_regressions: list[str] = field(default_factory=list)
    #: W19 — did the declared ``attribution_signature`` actually appear in the
    #: next round's traces? ``None`` = not checked yet.
    attribution_satisfied: bool | None = None
    #: v6 — the graph node ids this shipped candidate actually edited, taken from
    #: the candidate's ``graph_edits`` (``GraphEdit.affected_node_ids``). ``None``
    #: (the default) is RECORDED-AS-UNKNOWN: graph-edit information was not
    #: captured for this ship, so its touch history is unknown — never inferred to
    #: be "touched nothing". An empty list is the opposite fact: recording ran and
    #: the ship touched no graph node (e.g. a prompt-text-only edit). The Planner
    #: uses the distinction to tell an implicated node a prior ship never edited
    #: from one whose edit history is simply unrecorded.
    graph_nodes_touched: list[str] | None = None

    @property
    def hits(self) -> list[str]:
        """Predicted flips that actually flipped, in prediction order."""
        realized = set(self.realized_flips)
        return [task_id for task_id in self.predicted_flips if task_id in realized]

    @property
    def hit_rate(self) -> float | None:
        """``hits / predicted`` — ``None`` when nothing was predicted.

        Paper check: C-R10-02 predicted seven and hit five -> 0.71 (p.37).
        """
        if not self.predicted_flips:
            return None
        return len(self.hits) / len(self.predicted_flips)

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "candidate_id": self.candidate_id,
            "round_idx": self.round_idx,
            "variant_id": self.variant_id,
            "levers": list(self.levers),
            "predicted_flips": list(self.predicted_flips),
            "predicted_at_risk": list(self.predicted_at_risk),
            "realized_flips": list(self.realized_flips),
            "realized_regressions": list(self.realized_regressions),
            "attribution_satisfied": self.attribution_satisfied,
            "hit_rate": self.hit_rate,
        }
        # Emitted ONLY when recording ran, so a ship with no graph-edit info
        # serializes byte-identically to pre-v6 ``ship_outcomes.json`` (the key
        # is simply absent, which reads back as ``None`` = unknown).
        if self.graph_nodes_touched is not None:
            payload["graph_nodes_touched"] = list(self.graph_nodes_touched)
        return payload

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ShipOutcome:
        return cls(
            candidate_id=data["candidate_id"],
            round_idx=data["round_idx"],
            variant_id=data["variant_id"],
            levers=list(data.get("levers", [])),
            predicted_flips=list(data.get("predicted_flips", [])),
            predicted_at_risk=list(data.get("predicted_at_risk", [])),
            realized_flips=list(data.get("realized_flips", [])),
            realized_regressions=list(data.get("realized_regressions", [])),
            attribution_satisfied=data.get("attribution_satisfied"),
            graph_nodes_touched=(
                list(data["graph_nodes_touched"]) if data.get("graph_nodes_touched") is not None else None
            ),
        )


@dataclass
class RejectedCandidate:
    """An archived failure (``rejected_candidates.jsonl``, §E.1).

    §4.3 p.10: failing candidates are "archived with their rejection reason".
    The fields map one-to-one onto :class:`.gate.GateResult`; the caller does
    the mapping so this module stays independent of the gate.
    """

    candidate_id: str
    round_idx: int
    variant_id: str
    failed_stage: str
    archive_reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "round_idx": self.round_idx,
            "variant_id": self.variant_id,
            "failed_stage": self.failed_stage,
            "archive_reason": self.archive_reason,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RejectedCandidate:
        return cls(
            candidate_id=data["candidate_id"],
            round_idx=data["round_idx"],
            variant_id=data["variant_id"],
            failed_stage=data["failed_stage"],
            archive_reason=data["archive_reason"],
        )


class EvidenceStore:
    """The run's ``data/`` directory (§E.1 p.43), as an append-only store.

    ``task_history.jsonl`` and ``rejected_candidates.jsonl`` are append-only
    JSONL; ``ship_outcomes.json`` is a JSON array, as the paper names it, so it
    is rewritten whole on every append. Files are created lazily and read back
    on each query — the whole store is a few thousand short lines per run, and
    a cache would only add a way for the audit trail to disagree with the disk.
    """

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.data_dir = self.root / "data"

    # ------------------------------------------------------------------
    # paths
    # ------------------------------------------------------------------

    @property
    def task_history_path(self) -> Path:
        return self.data_dir / "task_history.jsonl"

    @property
    def ship_outcomes_path(self) -> Path:
        return self.data_dir / "ship_outcomes.json"

    @property
    def rejected_candidates_path(self) -> Path:
        return self.data_dir / "rejected_candidates.jsonl"

    # ------------------------------------------------------------------
    # digests
    # ------------------------------------------------------------------

    def append_digest(self, digest: TaskDigest) -> None:
        """Write one ``(round, task, variant)`` line to ``task_history.jsonl``."""
        self._append_jsonl(self.task_history_path, digest.to_dict())

    def iter_digests(self) -> Iterator[TaskDigest]:
        """Every digest on disk, in write order."""
        for record in self._read_jsonl(self.task_history_path):
            yield TaskDigest.from_dict(record)

    def cross_round_history(self, task_id: str, variant_id: str | None = None) -> list[dict[str, Any]]:
        """This task's prior outcomes and the ships that named it, by round.

        §4.3 p.10: the summary "links to its history of prior outcomes and
        shipped edits, enabling the Planner to distinguish persistent failures
        from transient noise".

        ``variant_id`` extends the SPEC signature by being optional: ``None``
        spans variants, which is what a task moved between variants by routing
        (or by a fork) needs. Each entry is ``{round_idx, variant_id, outcome,
        solved, failure_category, ships}``, where ``ships`` are the candidate
        ids shipped in that round whose manifest named this task.

        v6: when a ship in that round recorded ``graph_nodes_touched`` (not
        ``None``), the entry also carries ``ship_nodes`` — the sorted union of
        the graph node ids those ships edited. The key is present ONLY when
        recording ran (an empty list then means "recorded, touched no node"); its
        absence is the "unknown" fact, so an entry with no graph-recording ship
        stays byte-identical to before. This lets the Planner tell an implicated
        node never edited from one whose edit history is simply unrecorded.
        """
        by_round: dict[int, list[str]] = {}
        nodes_by_round: dict[int, set[str]] = {}
        recorded_rounds: set[int] = set()
        for ship in self.ship_outcomes():
            named = set(ship.get("predicted_flips", [])) | set(ship.get("predicted_at_risk", []))
            if task_id in named:
                round_of_ship = int(ship["round_idx"])
                by_round.setdefault(round_of_ship, []).append(str(ship["candidate_id"]))
                touched = ship.get("graph_nodes_touched")
                if touched is not None:
                    recorded_rounds.add(round_of_ship)
                    nodes_by_round.setdefault(round_of_ship, set()).update(str(node) for node in touched)

        history: list[dict[str, Any]] = []
        for digest in self.iter_digests():
            if digest.task_id != task_id:
                continue
            if variant_id is not None and digest.variant_id != variant_id:
                continue
            entry: dict[str, Any] = {
                "round_idx": digest.round_idx,
                "variant_id": digest.variant_id,
                "outcome": list(digest.outcome),
                "solved": digest.solved,
                "failure_category": digest.failure_category,
                "ships": sorted(by_round.get(digest.round_idx, [])),
            }
            if digest.round_idx in recorded_rounds:
                entry["ship_nodes"] = sorted(nodes_by_round.get(digest.round_idx, set()))
            history.append(entry)
        history.sort(key=lambda entry: (entry["round_idx"], entry["variant_id"]))
        return history

    def attach_prior_history(self, digest: TaskDigest) -> TaskDigest:
        """Return a copy of ``digest`` carrying the rounds *before* its own.

        Returns a copy rather than mutating in place so a digest can be written
        once and never silently rewritten; the cut is strict (``round_idx <
        digest.round_idx``) because "prior history" that includes the current
        round is not history.
        """
        prior = [
            entry
            for entry in self.cross_round_history(digest.task_id, digest.variant_id)
            if entry["round_idx"] < digest.round_idx
        ]
        return replace(digest, prior_history=prior)

    # ------------------------------------------------------------------
    # ships
    # ------------------------------------------------------------------

    def append_ship(self, outcome: ShipOutcome) -> None:
        """Add one entry to ``ship_outcomes.json``."""
        records = self.ship_outcomes()
        records.append(outcome.to_dict())
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.ship_outcomes_path.write_text(
            json.dumps(records, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    def ship_outcomes(self) -> list[dict[str, Any]]:
        """Every historical ship, predicted versus realised (§E.1 p.43)."""
        if not self.ship_outcomes_path.exists():
            return []
        text = self.ship_outcomes_path.read_text(encoding="utf-8").strip()
        if not text:
            return []
        records = json.loads(text)
        if not isinstance(records, list):
            raise ValueError(f"{self.ship_outcomes_path} must hold a JSON array")
        return records

    def iter_ships(self) -> Iterator[ShipOutcome]:
        for record in self.ship_outcomes():
            yield ShipOutcome.from_dict(record)

    # ------------------------------------------------------------------
    # rejected candidates
    # ------------------------------------------------------------------

    def append_rejected(self, rejected: RejectedCandidate) -> None:
        """Archive a failed candidate with its rejection reason (§4.3 p.10)."""
        self._append_jsonl(self.rejected_candidates_path, rejected.to_dict())

    def rejected_candidates(self) -> list[dict[str, Any]]:
        return self._read_jsonl(self.rejected_candidates_path)

    # ------------------------------------------------------------------
    # hit-rate — the Critic's portfolio audit input (SPEC §6.5)
    # ------------------------------------------------------------------

    def hit_rate(
        self,
        lever: str | None = None,
        *,
        since_round: int | None = None,
        variant_id: str | None = None,
    ) -> float | None:
        """Realised flips over predicted flips; ``None`` when nothing was predicted.

        Pooled, not averaged: numerators and denominators are summed across the
        selected ships before dividing, so a ship predicting seven tasks weighs
        seven times a ship predicting one. Averaging per-ship rates would let a
        single one-task lucky ship cancel a large failed one. Ours — the paper
        reports lever effectiveness as "tasks flipped / predicted" (Figure 12c
        p.43) without saying how ships are aggregated.

        ``lever`` filters to ships whose ``bucket`` list contains it,
        ``since_round`` to ships at or after a round, ``variant_id`` to one
        variant's ships. An undefined rate (no predictions at all) is returned
        as ``None`` and must not be read as 0.0: a lever nobody has made a
        falsifiable prediction with has not been shown to be ineffective.
        """
        hits = 0
        predicted = 0
        for ship in self.iter_ships():
            if lever is not None and lever not in ship.levers:
                continue
            if since_round is not None and ship.round_idx < since_round:
                continue
            if variant_id is not None and ship.variant_id != variant_id:
                continue
            hits += len(ship.hits)
            predicted += len(ship.predicted_flips)
        if predicted == 0:
            return None
        return hits / predicted

    def lever_ship_rounds(self, lever: str) -> list[int]:
        """Rounds in which this lever shipped, ascending (duplicates kept)."""
        return sorted(ship.round_idx for ship in self.iter_ships() if lever in ship.levers)

    def lever_is_banned(
        self,
        lever: str,
        round_idx: int,
        *,
        window: int = LEVER_BAN_WINDOW,
        min_ships: int = LEVER_BAN_MIN_SHIPS,
        threshold: float = LEVER_BAN_HIT_RATE,
    ) -> bool:
        """Critic portfolio-audit rule 1 (§3.4 p.33-34 / SPEC §6.5).

        Verbatim shape: the same lever shipped in **>= 2 of the last 3 rounds**
        *and* its **cumulative** hit-rate is **< 0.4** -> it may not ship again
        and a ``strategy_concern`` is recorded.

        Note the two different time scopes, which are the paper's and not a
        slip: recency uses the ``window`` (rounds ``round_idx - window`` up to
        but excluding ``round_idx``), while the rate is cumulative over the
        whole run. A lever that has never carried a prediction has an undefined
        rate and is never banned — you cannot convict a lever of ineffectiveness
        without a falsifiable prediction to score it on.
        """
        if window < 1:
            raise ValueError(f"window must be >= 1, got {window}")
        recent = [r for r in self.lever_ship_rounds(lever) if round_idx - window <= r < round_idx]
        if len(recent) < min_ships:
            return False
        rate = self.hit_rate(lever)
        if rate is None:
            return False
        return rate < threshold

    # ------------------------------------------------------------------
    # JSONL plumbing
    # ------------------------------------------------------------------

    def _append_jsonl(self, path: Path, record: dict[str, Any]) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    @staticmethod
    def _read_jsonl(path: Path) -> list[dict[str, Any]]:
        if not path.exists():
            return []
        records: list[dict[str, Any]] = []
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if line:
                    records.append(json.loads(line))
        return records


def ship_outcome_from_manifest(
    manifest: Any,
    round_idx: int,
    *,
    realized_flips: Iterable[str] = (),
    realized_regressions: Iterable[str] = (),
    attribution_satisfied: bool | None = None,
    graph_nodes_touched: Iterable[str] | None = None,
) -> ShipOutcome:
    """Score a shipped :class:`.manifest.ChangeManifest` against what happened.

    Kept as a free function rather than a method on either type so
    :mod:`.evidence` and :mod:`.manifest` stay independent: the manifest is the
    prediction, the store is the record, and this is the one place they meet.
    ``manifest`` is duck-typed for that reason.

    ``graph_nodes_touched`` is threaded through untouched: ``None`` (the default)
    keeps the ship recorded-as-unknown for graph touches, a supplied iterable
    (the shipped candidate's ``graph_edits`` node ids) records exactly what was
    edited. It is never derived here — the caller holds the candidate.
    """
    return ShipOutcome(
        candidate_id=manifest.candidate_id,
        round_idx=round_idx,
        variant_id=manifest.target_variant,
        levers=list(manifest.bucket),
        predicted_flips=list(manifest.predicted_impact.predicted_flips()),
        predicted_at_risk=list(manifest.predicted_impact.tasks_at_risk),
        realized_flips=list(realized_flips),
        realized_regressions=list(realized_regressions),
        attribution_satisfied=attribution_satisfied,
        graph_nodes_touched=(None if graph_nodes_touched is None else list(graph_nodes_touched)),
    )
