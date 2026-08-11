# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""W13 + W24 — the change manifest and its Level-2 round-trip contract.

Every Evolver candidate carries a change manifest (§4.3 p.10). Its purpose is
stated at p.35, verbatim: "The manifest makes every harness modification
**falsifiable**: the Critic checks whether the next round's trace features match
the mechanism and impact the manifest predicted." It is therefore the loop's
evidence ledger, not paperwork — :mod:`.evidence` scores ships against the
predictions made here, and the gate's first check (§4.3 p.10) is nothing but
:meth:`ChangeManifest.validate_complete`.

Fields are copied from Table 9 (p.36) and the manifest template in the Evolver
prompt (p.32), which do not agree: ``iterates_from`` appears only in the
template and is missing from Table 9 (report §3.3, contradiction H7). It is the
only hook for revision/rollback lineage, so it is included.

Ours — ``target_variant``
-------------------------
The paper's schema has **no** variant or cluster field (report §3.3 warning).
That is a real hole: §7.1 claims the compositional structure makes an edit's
intended scope explicit, yet under variant isolation the scope lives nowhere in
the manifest. Since the seesaw is narrowed to "tasks routed to k" (§4.5 p.11),
a candidate that cannot name *k* cannot be gated, so ``target_variant`` is added
as an our-extension field and is required by :meth:`validate_complete`.

Ours — a permissive model with a strict gate
--------------------------------------------
Every field has a default, so an incomplete manifest is still a constructible
object. This is deliberate: manifests arrive from an LLM, and rejecting them at
parse time would move the accept/reject decision out of the deterministic gate
and into pydantic. The model parses; :meth:`validate_complete` judges; the gate
archives the reason (§4.3 "archived with rejection reason"). ``extra="forbid"``
is the one exception — an unknown key means the Evolver invented schema, which
is a parse error, not an incomplete manifest.

W24 — Level 2
-------------
Evolver prompt p.32, verbatim: "Level 2 -- round-trip reaches the model: a unit
call that returns does not prove the agent sees the return. Simulate the path
from your code to the model's next input and assert the content survives it
(provider serializer for tools; the next pipeline stage for processors)."
:func:`check_level2_roundtrip` is that assertion, and its evidence note copies
the shape of the C-R10-02 instance (p.37): ``"_prepare_messages([tool_msg])
keeps content as 10,529-char string"``. The serializer is **injected**: stage A
has no provider (SPEC §5), and batch C passes the repo's real one.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

#: Edit types (Table 9 p.36). The paper calls the same thing a "bucket" in the
#: manifest and a "lever" in the effectiveness analysis (Figure 12c p.43).
BUCKETS = ("prompt", "tools", "config", "processor")

#: Buckets that introduce executable code and therefore need the two-level
#: verification of p.32; a pure ``prompt`` candidate is exempt there ("the
#: counterfactual gate provides the equivalent smoke check").
CODE_BUCKETS = frozenset({"tools", "processor"})

#: ``capability_evidence`` entry types (template p.32; Table 9's schema block
#: lists a shorter set, the template's is a superset).
EVIDENCE_TYPES = ("python_package", "http_endpoint", "builtin_tool", "filesystem", "other")

#: ``file_changes`` actions (Table 9 p.36).
FILE_ACTIONS = ("create", "modify", "delete")

#: ``attribution_signature`` types. Table 9's schema block (p.36) lists three;
#: the prompt template (p.32) omits ``prompt_feature``. We take the schema.
SIGNATURE_TYPES = ("tool_call", "processor_invocation", "prompt_feature")

#: ``C-R{round}-{NN}`` (Table 9 p.36, "e.g. C-R3-01").
CANDIDATE_ID_RE = re.compile(r"^C-R\d+-\d{2,}$")

#: The Level-2 claim string, verbatim from the C-R10-02 manifest (p.37). Used
#: as the marker for "this manifest carries round-trip evidence"; the paper has
#: no machine-readable marker, so matching on the claim wording is ours.
LEVEL2_CLAIM = "tool return survives provider serialization to the model (Level 2)"

#: Substrings that identify a Level-2 entry inside ``capability_evidence``.
_LEVEL2_MARKERS = ("level 2", "level-2", "level2")

#: Default label for the round-trip note, mirroring the paper's evidence line.
DEFAULT_LEVEL2_LABEL = "_prepare_messages([tool_msg])"

# ---------------------------------------------------------------------------
# Manifest provenance (--manifest-mode) — W28
# ---------------------------------------------------------------------------
#
# The open-source ``harnessx`` meta-agent is trained against the repo's own
# journal format (``harnessx/meta_harness/workspace/skills/journal/SKILL.md``),
# not the paper's Table 9 (p.36) manifest schema, which the published repo does
# not contain. A real $3 run (``runs/forkprobe_11``) showed every structured
# candidate dying at parse time because the meta-agent filled our Table-9 keys
# with journal vocabulary (``levers`` / ``predicted_affected`` / ``hypothesis_id``
# and ``lens`` / ``lever`` / ``intent`` inside ``attribution_signature``) and
# with natural-language strings where the schema wants lists/dicts.
#
# ``provenance`` records which contract a manifest was produced under so the
# report can declare it honestly and so the gate's completeness check can relax
# *paper-only* requirements (capability_evidence, attribution_signature) that a
# repo-journal candidate legitimately cannot supply. The deterministic seesaw
# (gate stage 5, real evaluation) remains the shipping authority either way;
# paper-manifest provenance keeps the strict Table-9 contract unchanged.

#: A manifest written to the paper's Table 9 schema (the strict default).
PAPER_MANIFEST_PROVENANCE = "paper_manifest"

#: A manifest adapted from the repo's own journal vocabulary via
#: :func:`adapt_repo_journal_manifest`.
REPO_JOURNAL_PROVENANCE = "repo_journal"

#: The repo journal's ``levers`` vocabulary (SKILL.md line 46,
#: ``{configuration, control, action, instruction}``) mapped to our Table-9
#: ``bucket`` edit types. Values already in :data:`BUCKETS` pass through, so a
#: manifest that happens to use paper vocabulary still maps cleanly.
REPO_LEVER_TO_BUCKET = {
    "configuration": "config",
    "config": "config",
    "instruction": "prompt",
    "prompt": "prompt",
    "action": "tools",
    "tools": "tools",
    "control": "processor",
    "processor": "processor",
}


class RepoJournalFormatError(ValueError):
    """The manifest text is not a parseable repo-journal mapping.

    Raised by :func:`adapt_repo_journal_manifest` for a *format mismatch* (the
    YAML does not load as a mapping at all), as opposed to a *missing field*
    (the mapping parses but omits a required field, which surfaces later through
    :meth:`ChangeManifest.validate_complete`). Keeping the two apart lets the
    pipeline archive the right rejection reason.
    """


# ---------------------------------------------------------------------------
# M17 — the three predicted-impact categories are pass@2 categories
# ---------------------------------------------------------------------------


class ImpactCategory(Enum):
    """Which ``predicted_impact`` list a task belongs in, given its outcome.

    Values are the manifest field names, so the enum can address the model
    directly.
    """

    WILL_UNLOCK = "tasks_will_unlock"
    WILL_STABILIZE = "tasks_will_stabilize"
    AT_RISK = "tasks_at_risk"


def impact_category(n_pass: int, n_att: int) -> ImpactCategory:
    """Classify a task's *current* pass@k outcome into a manifest category.

    The template (p.32) defines the three categories in terms of the current
    rollout outcome, verbatim::

        tasks_will_unlock:    [ALL_FAIL      -> expect >=1 rollout to pass]
        tasks_will_stabilize: [PARTIAL_PASS  -> expect all rollouts to pass]
        tasks_at_risk:        [currently >=1 pass -> might regress]

    so the partition on ``(n_pass, n_att)`` is

    ===========  ==================  ====================
    outcome      state               category
    ===========  ==================  ====================
    ``0 / n``    ALL_FAIL            ``tasks_will_unlock``
    ``0<p<n``    PARTIAL_PASS        ``tasks_will_stabilize``
    ``n / n``    all rollouts pass   ``tasks_at_risk``
    ===========  ==================  ====================

    **``tasks_will_stabilize`` only exists under pass@k with k >= 2** (report
    §7 M17): ``PARTIAL_PASS`` is unreachable when a task is attempted once, so
    under single-attempt evaluation the category is permanently empty and the
    manifest contract is semantically incomplete. This is the third independent
    argument for pass@2 (W17) and the reason it is a definitional part of the
    mechanism rather than an evaluation nicety.

    The paper's ``tasks_at_risk`` criterion is literally "currently >=1 pass",
    which also covers ``PARTIAL_PASS``; the two categories overlap in the
    paper's own wording. This function returns the **primary** category, giving
    a partial pass to the more specific ``tasks_will_stabilize``. A manifest may
    still legitimately list a partially-passing task under ``tasks_at_risk`` as
    well, so :meth:`ChangeManifest.validate_complete` does not treat that
    overlap as an error — it only rejects the contradictory
    ``unlock`` and ``at_risk`` overlap (a task at 0/n has nothing to lose).
    """
    if n_att < 1:
        raise ValueError(f"n_att must be >= 1, got {n_att}")
    if n_pass < 0 or n_pass > n_att:
        raise ValueError(f"n_pass must be in [0, {n_att}], got {n_pass}")
    if n_pass == 0:
        return ImpactCategory.WILL_UNLOCK
    if n_pass < n_att:
        return ImpactCategory.WILL_STABILIZE
    return ImpactCategory.AT_RISK


# ---------------------------------------------------------------------------
# The manifest
# ---------------------------------------------------------------------------


class PredictedImpact(BaseModel):
    """The falsifiable prediction (Table 9 p.36; categories per :func:`impact_category`)."""

    model_config = ConfigDict(extra="forbid")

    tasks_will_unlock: list[str] = Field(default_factory=list)
    tasks_will_stabilize: list[str] = Field(default_factory=list)
    tasks_at_risk: list[str] = Field(default_factory=list)

    def predicted_flips(self) -> list[str]:
        """Tasks the edit claims it will *turn into* passes, in manifest order.

        Unlock plus stabilize; ``tasks_at_risk`` is a risk disclosure, not a
        claimed flip, and stays out. This is the hit-rate denominator, matching
        the paper's C-R10-02 arithmetic: 5 unlock + 2 stabilize = "seven tasks
        the tool was predicted to affect", of which five flipped -> 0.71 (p.37).
        """
        seen: list[str] = []
        for task_id in (*self.tasks_will_unlock, *self.tasks_will_stabilize):
            if task_id not in seen:
                seen.append(task_id)
        return seen

    def is_empty(self) -> bool:
        """No prediction at all — nothing to falsify."""
        return not (self.tasks_will_unlock or self.tasks_will_stabilize or self.tasks_at_risk)


class AttributionSignature(BaseModel):
    """The trace feature that must appear if the edit actually fired (Table 9 p.36).

    W19/SPEC §6.4 promote this from the paper's "recommended" to a hard
    requirement for every non-prompt candidate: an edit whose declared signature
    never shows up in the next round's traces did not fire, and any improvement
    credited to it is reward hacking rather than mechanism.
    """

    model_config = ConfigDict(extra="forbid")

    type: Literal["tool_call", "processor_invocation", "prompt_feature"] | None = None
    tool_name: str | None = None
    expected_min_calls: int = 1

    def expected_graph_node_id(self) -> str | None:
        """The base unfolded-graph (U) node id whose execution proves this fired.

        v6 M7 — the handle the W19 *graph existence query*
        (:func:`check_attribution_in_graph`) looks up in U. Only ``tool_call``
        resolves today: a tool invocation is the node ``tool:<tool_name>`` in U
        (M5). ``processor_invocation`` and ``prompt_feature`` carry no node
        handle in the Table-9 schema, so they return ``None`` and the query
        cannot bind them — the check then falls back, recorded, to the
        structural W19 declaration gate rather than silently claiming U proved
        anything.
        """
        if self.type == "tool_call":
            name = (self.tool_name or "").strip()
            return f"tool:{name}" if name else None
        return None


class ChangeManifest(BaseModel):
    """One candidate's change manifest (Table 9 p.36 + template p.32).

    Field order follows the paper's YAML template, so :meth:`to_yaml` emits the
    manifest in the shape the loop logs it.
    """

    model_config = ConfigDict(extra="forbid")

    candidate_id: str = ""
    bucket: list[str] = Field(default_factory=list)
    #: Template p.32 only ("OPTIONAL -- set for a revert/improve"); absent from
    #: Table 9 (contradiction H7). The sole lineage hook for revisions and
    #: rollbacks, so we keep it.
    iterates_from: str | None = None
    capability_evidence: list[dict[str, Any]] = Field(default_factory=list)
    file_changes: list[dict[str, Any]] = Field(default_factory=list)
    predicted_impact: PredictedImpact = Field(default_factory=PredictedImpact)
    attribution_signature: AttributionSignature | None = None
    #: **Ours** — the variant this candidate targets (§4.5 p.11 "a candidate
    #: targeting variant k"). The paper's schema has no such field.
    target_variant: str = ""
    #: **Ours (W28)** — which contract produced this manifest. Not a Table 9
    #: field, so :meth:`to_yaml` excludes it (the logged YAML stays byte-for-byte
    #: the paper's eight-key shape). ``paper_manifest`` keeps the strict schema;
    #: ``repo_journal`` marks a manifest adapted from the repo's journal
    #: vocabulary and relaxes the paper-only completeness requirements.
    provenance: str = PAPER_MANIFEST_PROVENANCE
    #: **Ours (W28)** — the repo journal's ``hypothesis_id`` retained across the
    #: adaptation so cross-round lineage is not lost. ``None`` for paper
    #: manifests. Excluded from :meth:`to_yaml` for the same reason.
    source_hypothesis_id: str | None = None

    @field_validator("bucket", mode="before")
    @classmethod
    def _accept_scalar_bucket(cls, value: Any) -> Any:
        """The template allows ``bucket: prompt`` as well as ``[prompt, tools]`` (p.32)."""
        if isinstance(value, str):
            return [value]
        return value

    # ------------------------------------------------------------------
    # YAML round-trip
    # ------------------------------------------------------------------

    def to_yaml(self) -> str:
        """Serialise to the manifest YAML block (schema p.36).

        The two provenance fields (``provenance``, ``source_hypothesis_id``) are
        ours, not Table 9, so they are excluded here — the logged manifest keeps
        the paper's eight-key shape and order. Provenance is surfaced through the
        report/audit, not the manifest YAML.
        """
        return yaml.safe_dump(
            self.model_dump(mode="json", exclude={"provenance", "source_hypothesis_id"}),
            sort_keys=False,
            allow_unicode=True,
            default_flow_style=False,
        )

    @classmethod
    def from_yaml(cls, text: str) -> ChangeManifest:
        """Parse a manifest YAML block.

        Accepts a bare mapping and the paper's on-disk form, where the manifest
        is YAML front matter inside ``candidates/C-R<n>-NN.md`` fenced by
        ``---`` lines with prose after it (p.32/p.37). Only the front matter is
        read; the ``## Failure Evidence`` / ``## Root Cause`` prose below it is
        a human-facing section the schema does not cover.
        """
        body = _strip_front_matter(text)
        data = yaml.safe_load(body)
        if data is None:
            data = {}
        if not isinstance(data, dict):
            raise ValueError(f"manifest YAML must be a mapping, got {type(data).__name__}")
        return cls.model_validate(data)

    # ------------------------------------------------------------------
    # Gate stage 1 — manifest completeness (§4.3 p.10)
    # ------------------------------------------------------------------

    def validate_complete(self, *, unfolded: Any = None) -> list[str]:
        """Everything wrong with this manifest, as human-readable strings.

        Empty list = complete = gate stage 1 passes. Each entry starts with the
        offending field name so the archived rejection reason (§4.3 "archived
        with rejection reason") reads cleanly.

        ``unfolded`` (v6 M7, optional) is the run's unfolded graph U. When given,
        the W19 attribution check stops being a string-shaped declaration gate
        and becomes a *graph existence query* — the declared signature must name
        a node that actually executed in U (see
        :func:`check_attribution_in_graph`). It is strictly additive: the default
        ``None`` reproduces today's behaviour byte-for-byte, so every existing
        caller (the gate's ``_manifest_complete`` among them) is unchanged.

        Paper-derived requirements: all Table 9 fields carry content; buckets
        and actions come from the paper's enumerations; ``capability_evidence``
        entries are ``{type, claim, evidence}`` triples ("REQUIRED -- may be
        empty []", p.32).

        Ours, each an explicit experiment choice:

        * ``target_variant`` is required (see the module docstring);
        * a candidate touching ``tools``/``processor`` must carry at least one
          capability-evidence entry — p.32 forbids "I believe this will work",
          and a code candidate with no observed evidence "will burn a round's
          ship slot for zero flips";
        * ``predicted_impact`` must claim at least one flip, otherwise the
          manifest predicts nothing and the hit-rate denominator (§3.4, and
          :mod:`.evidence`) is undefined — an unfalsifiable manifest defeats
          the whole point of p.35;
        * ``attribution_signature`` is required for every non-``prompt``-only
          candidate (W19/SPEC §6.4 hard gate; the paper says "recommended for
          tools/processor/config");
        * ``file_changes`` must be non-empty: a builder edit that changes no
          file is not an edit.
        """
        problems: list[str] = []

        if not self.candidate_id.strip():
            problems.append("candidate_id: missing")
        elif not CANDIDATE_ID_RE.match(self.candidate_id):
            problems.append(f"candidate_id: expected C-R<round>-<NN>, got {self.candidate_id!r}")

        if not self.bucket:
            problems.append("bucket: missing")
        else:
            unknown = [b for b in self.bucket if b not in BUCKETS]
            if unknown:
                problems.append(f"bucket: unknown edit types {unknown}, expected any of {list(BUCKETS)}")

        problems.extend(self._capability_evidence_problems())
        problems.extend(self._file_change_problems())
        problems.extend(self._predicted_impact_problems())
        problems.extend(self._attribution_problems(unfolded))

        if not self.target_variant.strip():
            problems.append("target_variant: missing (ours; required under variant isolation)")

        return problems

    def _capability_evidence_problems(self) -> list[str]:
        problems: list[str] = []
        if self.needs_code_verification() and not self.capability_evidence:
            # A repo-journal manifest has no structured capability-evidence
            # source (SKILL.md never asks for one). We mark the gap via
            # :meth:`paper_only_gaps` instead of fabricating an entry; the
            # deterministic seesaw still governs shipping. Paper provenance keeps
            # the strict Table-9 requirement.
            if self.provenance != REPO_JOURNAL_PROVENANCE:
                problems.append(
                    f"capability_evidence: missing for a code candidate (bucket={self.bucket})"
                )
        for idx, entry in enumerate(self.capability_evidence):
            missing = [key for key in ("type", "claim", "evidence") if not str(entry.get(key, "")).strip()]
            if missing:
                problems.append(f"capability_evidence[{idx}]: missing {missing}")
                continue
            if entry["type"] not in EVIDENCE_TYPES:
                problems.append(
                    f"capability_evidence[{idx}]: unknown type {entry['type']!r}, "
                    f"expected any of {list(EVIDENCE_TYPES)}"
                )
        return problems

    def _file_change_problems(self) -> list[str]:
        if not self.file_changes:
            return ["file_changes: missing"]
        problems: list[str] = []
        for idx, change in enumerate(self.file_changes):
            missing = [key for key in ("path", "action", "diff_summary") if not str(change.get(key, "")).strip()]
            if missing:
                problems.append(f"file_changes[{idx}]: missing {missing}")
                continue
            if change["action"] not in FILE_ACTIONS:
                problems.append(
                    f"file_changes[{idx}]: unknown action {change['action']!r}, "
                    f"expected any of {list(FILE_ACTIONS)}"
                )
        return problems

    def _predicted_impact_problems(self) -> list[str]:
        impact = self.predicted_impact
        problems: list[str] = []
        if not impact.predicted_flips():
            problems.append(
                "predicted_impact: no predicted flip "
                "(tasks_will_unlock and tasks_will_stabilize are both empty)"
            )
        both_ways = sorted(set(impact.tasks_will_unlock) & set(impact.tasks_will_stabilize))
        if both_ways:
            problems.append(
                f"predicted_impact: {both_ways} listed as both ALL_FAIL and PARTIAL_PASS"
            )
        cannot_regress = sorted(set(impact.tasks_will_unlock) & set(impact.tasks_at_risk))
        if cannot_regress:
            problems.append(
                f"predicted_impact: {cannot_regress} listed as at risk while currently at 0 passes"
            )
        return problems

    def _attribution_problems(self, unfolded: Any = None) -> list[str]:
        signature = self.attribution_signature
        if signature is None or signature.type is None:
            # The repo journal's lens/lever/intent tags are not a paper
            # attribution signature, so a repo-journal manifest cannot supply
            # one. Marked missing via :meth:`paper_only_gaps` rather than
            # blocked; paper provenance keeps the W19 hard gate.
            if self.needs_attribution() and self.provenance != REPO_JOURNAL_PROVENANCE:
                return [
                    (
                        "attribution_signature: missing for a non-prompt candidate "
                        f"(bucket={self.bucket}; W19 hard gate)"
                    )
                ]
            return []
        problems: list[str] = []
        if signature.type == "tool_call" and not (signature.tool_name or "").strip():
            problems.append("attribution_signature: tool_call needs tool_name")
        if signature.expected_min_calls < 1:
            problems.append(
                f"attribution_signature: expected_min_calls must be >= 1, got {signature.expected_min_calls}"
            )
        # v6 M7 — the W19 graph existence query. When an unfolded graph U is
        # available, the declared signature must actually appear in it: string
        # evidence that merely *looks* like a fired edit is no longer enough.
        # Strictly additive — with no U (the default) this is skipped entirely
        # and the structural declaration checks above are the whole gate, so the
        # record honestly says the graph check was unavailable rather than
        # claiming the stronger check ran (the 4e0810f failure mode).
        if unfolded is not None:
            result = check_attribution_in_graph(self, unfolded)
            if result.available and result.satisfied is False:
                problems.append(f"attribution_signature: {result.reason}")
        return problems

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    def needs_code_verification(self) -> bool:
        """Does this candidate ship executable code (p.32 build->verify->iterate)?"""
        return bool(CODE_BUCKETS & set(self.bucket))

    def needs_attribution(self) -> bool:
        """W19 — every candidate except a pure ``prompt`` one must be attributable."""
        return bool(set(self.bucket) - {"prompt"})

    def level2_evidence(self) -> dict[str, Any] | None:
        """The Level-2 round-trip entry in ``capability_evidence``, if any.

        The paper carries it as a free-text claim (p.37, ``type: other``,
        claim :data:`LEVEL2_CLAIM`), so detection is by claim wording — ours,
        and the reason :func:`check_level2_roundtrip` writes its evidence
        through :meth:`Level2Evidence.as_capability_evidence`.
        """
        for entry in self.capability_evidence:
            claim = str(entry.get("claim", "")).lower()
            if any(marker in claim for marker in _LEVEL2_MARKERS):
                return entry
        return None

    def paper_only_gaps(self) -> list[str]:
        """Paper-schema fields a repo-journal manifest legitimately cannot fill.

        Empty for paper provenance. For ``repo_journal`` provenance it names the
        Table-9 fields the repo journal has no source for and that this manifest
        therefore omits — reported honestly rather than fabricated. Only fields
        the paper *would* require for this candidate's bucket count as gaps, so a
        prompt-bucket candidate (which the paper exempts from both) has none.
        """
        if self.provenance != REPO_JOURNAL_PROVENANCE:
            return []
        gaps: list[str] = []
        if self.needs_code_verification() and not self.capability_evidence:
            gaps.append("capability_evidence")
        if self.needs_attribution() and (
            self.attribution_signature is None or self.attribution_signature.type is None
        ):
            gaps.append("attribution_signature")
        return gaps


@dataclass(frozen=True)
class CandidateArtifact:
    """A non-opaque Evolver result that can be audited and gated.

    The paper's candidate files and manifest are described separately.  Under
    variant isolation that is too weak: a config with no structured prediction,
    or a manifest with no concrete config, cannot be checked by the deterministic
    gate.  This binding is therefore an explicit engineering contract.

    ``regression_explanations`` is also ours.  SPEC §6.5 requires the Critic to
    stop a whole round when regressions are neither handled nor explained, but
    the published manifest schema provides no machine-readable home for those
    explanations.  Keeping the sidecar on the artifact preserves the paper
    manifest's field order while making the audit deterministic.
    """

    config_path: Path
    manifest: ChangeManifest
    target_variant: str
    regression_explanations: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "config_path", Path(self.config_path))
        object.__setattr__(
            self,
            "regression_explanations",
            tuple(sorted((str(task_id), str(reason)) for task_id, reason in self.regression_explanations)),
        )

    @property
    def candidate_id(self) -> str:
        return self.manifest.candidate_id

    @property
    def explained_regressions(self) -> frozenset[str]:
        return frozenset(task_id for task_id, reason in self.regression_explanations if reason.strip())

    def validation_errors(
        self,
        *,
        expected_target: str | None = None,
        expected_round: int | None = None,
        require_config: bool = True,
    ) -> list[str]:
        """Return every machine-checkable contract violation.

        This deliberately reuses :meth:`ChangeManifest.validate_complete`
        rather than duplicating gate stage 1.  The pipeline rejects malformed
        and opaque proposals early, while the deterministic gate remains the
        sole authority over whether a *valid* candidate ships.
        """

        problems = list(self.manifest.validate_complete())
        if not self.target_variant.strip():
            problems.append("artifact.target_variant: missing")
        if self.manifest.target_variant != self.target_variant:
            problems.append(
                "target_variant: artifact/manifest mismatch "
                f"({self.target_variant!r} != {self.manifest.target_variant!r})"
            )
        if expected_target is not None and self.target_variant != expected_target:
            problems.append(
                f"target_variant: expected {expected_target!r}, got {self.target_variant!r}"
            )
        if expected_round is not None:
            prefix = f"C-R{expected_round}-"
            if not self.candidate_id.startswith(prefix):
                problems.append(
                    f"candidate_id: expected round {expected_round} prefix {prefix!r}, "
                    f"got {self.candidate_id!r}"
                )
        if require_config and not self.config_path.is_file():
            problems.append(f"config_path: file not found: {self.config_path}")
        for task_id, reason in self.regression_explanations:
            if not task_id.strip():
                problems.append("regression_explanations: empty task id")
            if not reason.strip():
                problems.append(f"regression_explanations[{task_id!r}]: empty explanation")
        return problems


def _strip_front_matter(text: str) -> str:
    """Return the YAML front-matter block of ``text``, or ``text`` unchanged."""
    stripped = text.lstrip()
    if not stripped.startswith("---"):
        return text
    lines = stripped.splitlines()
    body: list[str] = []
    for line in lines[1:]:
        if line.strip() == "---":
            return "\n".join(body)
        body.append(line)
    # unterminated front matter: treat the whole remainder as the block
    return "\n".join(body)


# ---------------------------------------------------------------------------
# W24 — Level-2 round-trip
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Level2Evidence:
    """Did the tool's return survive the trip to the model's next input? (p.32)"""

    survived: bool
    serialized_len: int
    note: str

    def as_capability_evidence(self) -> dict[str, str]:
        """The manifest entry this check produces, shaped like C-R10-02 (p.37).

        p.32: "Attach the verifying output as ``capability_evidence``."
        """
        return {"type": "other", "claim": LEVEL2_CLAIM, "evidence": self.note}


def check_level2_roundtrip(
    tool_output: str,
    serializer: Callable[[str], Any],
    *,
    label: str = DEFAULT_LEVEL2_LABEL,
) -> Level2Evidence:
    """Assert a tool return still reaches the model after provider serialisation.

    Verbatim rationale (p.32): "a unit call that returns does not prove the
    agent sees the return". The C-R10-02 instance (p.37) is the reference
    shape — ``"_prepare_messages([tool_msg]) keeps content as 10,529-char
    string"`` — and ``label`` names whichever stage is being simulated
    (``_prepare_messages`` for tools, the next pipeline stage for processors).

    ``serializer`` is injected because stage A has no provider (SPEC §5); batch
    C passes the repo's real one. Anything it raises is caught and reported as
    a failed round-trip rather than propagated: a serializer that blows up on
    the content is exactly the failure this check exists to catch.

    A zero-length ``tool_output`` never survives. Vacuously it does — the empty
    string is a substring of everything — but an empty return is the very
    failure C-R10-02 was written to fix ("every Wikipedia fetch in round 10's
    traces returned zero characters", p.36), so it is reported as a failure.
    """
    if not isinstance(tool_output, str):
        raise TypeError(f"tool_output must be a str, got {type(tool_output).__name__}")

    if not tool_output:
        return Level2Evidence(
            survived=False,
            serialized_len=0,
            note=f"{label} was handed 0 chars; there is nothing to survive",
        )

    try:
        serialized = serializer(tool_output)
    except Exception as exc:  # noqa: BLE001 - a raising serializer is a failed round-trip
        return Level2Evidence(
            survived=False,
            serialized_len=0,
            note=f"{label} raised {type(exc).__name__}: {exc}",
        )

    if not isinstance(serialized, str):
        return Level2Evidence(
            survived=False,
            serialized_len=0,
            note=f"{label} returned {type(serialized).__name__}, not a string the model can read",
        )

    if tool_output in serialized:
        return Level2Evidence(
            survived=True,
            serialized_len=len(serialized),
            note=f"{label} keeps content as {len(tool_output):,}-char string",
        )

    return Level2Evidence(
        survived=False,
        serialized_len=len(serialized),
        note=(
            f"{label} did not preserve the content: "
            f"{len(tool_output):,} chars in, {len(serialized):,} chars out"
        ),
    )


# ---------------------------------------------------------------------------
# W19 (v6 M7) — the attribution signature as a graph existence query
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AttributionGraphResult:
    """What the W19 *graph existence query* found — and what actually happened.

    The paper's attribution signature is the anti-reward-hacking clause: an edit
    whose declared trace feature never fires did not run, and any improvement
    credited to it is reward hacking rather than mechanism. As a string check it
    only proves the manifest *declares* a feature; a candidate satisfies it by
    emitting text that looks like evidence. Turned into a query over the unfolded
    graph U it proves the edited node *executed* — text can no longer stand in.

    This record is deliberately shaped like M6b's ``_DigesterInputProvenance``:
    it says which check actually ran. ``available`` is ``True`` only when U was
    present *and* the signature resolved to a concrete graph node, so the query
    could really be answered; then ``satisfied`` is the answer. When ``available``
    is ``False`` the query could not run (no U, or a signature type that names no
    graph node) and ``reason`` says which — the structural declaration gate stood
    in, and nothing here pretends U proved anything (the 4e0810f failure mode).
    """

    available: bool
    satisfied: bool | None
    reason: str
    expected_node_id: str | None = None
    observed_count: int = 0
    expected_min_calls: int = 1
    signature_type: str | None = None


def _count_executed(unfolded: Any, base_node_id: str) -> int:
    """How many invocations of ``base_node_id`` appear in an unfolded graph U.

    U nodes carry their base (static) graph node id directly on
    ``static_node_id`` (M2a is its sole authority; ``parse_unfolded_id`` recovers
    the same base from the ``{static_node_id}@t{ordinal}`` node id). Tools are
    ``tool:<name>`` (M5). Duck-typed on ``.nodes`` / ``.static_node_id`` so this
    module keeps its stdlib-only import surface and never depends on the graph
    package at import time.
    """
    return sum(1 for node in getattr(unfolded, "nodes", ()) if getattr(node, "static_node_id", None) == base_node_id)


def check_attribution_in_graph(manifest: ChangeManifest, unfolded: Any) -> AttributionGraphResult:
    """Answer W19 structurally: did the edited node actually execute in the run?

    ``unfolded`` is the run's unfolded graph U (or ``None``). The query resolves
    the declared :class:`AttributionSignature` to the base U node id it claims
    fired (:meth:`AttributionSignature.expected_graph_node_id`) and counts that
    node's invocations in U; the signature is *satisfied* when the count meets
    ``expected_min_calls`` and *refused* when the node is absent (or fired too
    few times) — the change was made but never ran.

    Returns ``available=False`` (query could not run, structural gate stands) when
    there is no declared signature, when the signature type names no graph node
    (only ``tool_call`` resolves today), or when no U is available — each with a
    concrete ``reason`` so a fallback records *why* rather than a bare miss.
    """
    signature = manifest.attribution_signature
    sig_type = signature.type if signature is not None else None
    if signature is None or signature.type is None:
        return AttributionGraphResult(
            available=False,
            satisfied=None,
            reason="no attribution_signature declared; the structural W19 gate applies",
            signature_type=sig_type,
        )

    min_calls = signature.expected_min_calls
    expected = signature.expected_graph_node_id()
    if expected is None:
        return AttributionGraphResult(
            available=False,
            satisfied=None,
            reason=(
                f"signature type {signature.type!r} names no graph node id "
                "(the Table-9 schema carries none); kept the structural W19 declaration check"
            ),
            expected_min_calls=min_calls,
            signature_type=sig_type,
        )

    if unfolded is None:
        return AttributionGraphResult(
            available=False,
            satisfied=None,
            reason=(
                "no unfolded graph U available for this candidate's run (unfold recording "
                "off, the file is absent, or a pre-unfold run); kept the structural W19 "
                "declaration check"
            ),
            expected_node_id=expected,
            expected_min_calls=min_calls,
            signature_type=sig_type,
        )

    observed = _count_executed(unfolded, expected)
    satisfied = observed >= min_calls
    if satisfied:
        reason = (
            f"declared {signature.type} {expected!r} executed {observed} time(s) in the run "
            f"(>= expected_min_calls={min_calls}); W19 graph existence query over U"
        )
    else:
        reason = (
            f"declared {signature.type} {expected!r} but it executed {observed} time(s) in the "
            f"run (< expected_min_calls={min_calls}); the change was made but never ran "
            "(W19 graph existence query over unfolded graph U)"
        )
    return AttributionGraphResult(
        available=True,
        satisfied=satisfied,
        reason=reason,
        expected_node_id=expected,
        observed_count=observed,
        expected_min_calls=min_calls,
        signature_type=sig_type,
    )


# ---------------------------------------------------------------------------
# W28 — repo-journal -> paper-manifest adapter (--manifest-mode repo)
# ---------------------------------------------------------------------------


def _as_str_list(value: Any) -> list[str]:
    """Flatten a scalar/list value into a list of non-empty strings."""
    if value is None:
        return []
    if isinstance(value, (str, bytes)):
        text = value.decode() if isinstance(value, bytes) else value
        text = text.strip()
        return [text] if text else []
    if isinstance(value, dict):
        # e.g. ``predicted_affected: {task_a: ..., task_b: ...}`` — take the keys.
        return [str(key).strip() for key in value if str(key).strip()]
    try:
        items = list(value)
    except TypeError:
        text = str(value).strip()
        return [text] if text else []
    out: list[str] = []
    for item in items:
        text = str(item).strip()
        if text:
            out.append(text)
    return out


def _coerce_file_change(entry: Any) -> dict[str, str]:
    """Map one repo journal change (dict or free-text bullet) to Table-9 shape.

    The repo journal's ``### Changes`` bullets are prose like
    ``config.yaml (template_path updated)``. We structure them into
    ``{path, action, diff_summary}`` — the path is extracted, the full text is
    kept verbatim as the summary, and the action defaults to ``modify`` (the
    common case) unless the bullet opens with an explicit create/delete verb.
    Nothing is invented: the descriptive content is preserved, only re-shaped.
    """
    if isinstance(entry, dict):
        return {
            "path": str(entry.get("path", "")).strip(),
            "action": (str(entry.get("action", "")).strip() or "modify"),
            "diff_summary": str(
                entry.get("diff_summary") or entry.get("summary") or entry.get("description") or ""
            ).strip(),
        }
    text = str(entry).strip()
    if not text:
        return {"path": "", "action": "modify", "diff_summary": ""}
    # The leading token up to the first delimiter is the path-like fragment.
    head = text
    for sep in ("(", " — ", " -- ", ":", " "):
        if sep in head:
            head = head.split(sep, 1)[0]
            if head.strip():
                break
    path = head.strip()
    first_word = text.split()[0].lower() if text.split() else ""
    action = "modify"
    if first_word in {"create", "add", "new"} or text.lower().startswith("create "):
        action = "create"
    elif first_word in {"delete", "remove", "drop"}:
        action = "delete"
    return {"path": path, "action": action, "diff_summary": text}


def _gather(data: dict[str, Any], nested: dict[str, Any], *keys: str) -> Any:
    """First present value for any of ``keys`` at top level, then nested."""
    for source in (data, nested):
        for key in keys:
            if key in source and source[key] not in (None, "", [], {}):
                return source[key]
    return None


def adapt_repo_journal_manifest(
    text: str,
    *,
    journal_text: str | None = None,
    fallback_candidate_id: str = "",
    fallback_target_variant: str = "",
) -> ChangeManifest:
    """Adapt a repo-journal ``manifest.yaml`` into a paper :class:`ChangeManifest`.

    This is the ``--manifest-mode repo`` parser. Rather than reject the repo
    meta-agent's natural output (which mixes Table-9 keys with the journal
    vocabulary of ``skills/journal/SKILL.md``), it maps what the journal *does*
    carry and marks what it cannot:

    * ``levers`` / ``lever`` (``{configuration, control, action, instruction}``,
      wherever they appear — top level or inside ``attribution_signature``) ->
      :data:`BUCKETS` via :data:`REPO_LEVER_TO_BUCKET`; an explicit ``bucket`` is
      honoured and mapped too;
    * ``predicted_affected`` -> ``predicted_impact.tasks_will_unlock`` (the flip
      claim), merged with any structured ``predicted_impact`` mapping;
    * ``hypothesis_id`` -> :attr:`ChangeManifest.source_hypothesis_id`;
    * ``file_changes`` prose bullets -> structured ``{path, action, diff_summary}``;
    * ``capability_evidence`` / ``attribution_signature`` — paper-only fields the
      journal has no source for — are left empty/``None`` (see
      :meth:`ChangeManifest.paper_only_gaps`), never fabricated.

    The returned manifest carries ``provenance="repo_journal"``, which relaxes
    exactly those two paper-only completeness requirements at gate stage 1 while
    leaving the deterministic seesaw (stage 5) as the shipping authority.

    Raises
    ------
    RepoJournalFormatError
        If ``text`` does not load as a YAML mapping (a *format mismatch*). A
        mapping that merely omits fields is not a format error — it parses here
        and fails later at :meth:`validate_complete` as a *missing field*.
    """
    body = _strip_front_matter(text)
    try:
        loaded = yaml.safe_load(body)
    except yaml.YAMLError as exc:  # pragma: no cover - defensive
        raise RepoJournalFormatError(f"manifest is not valid YAML: {exc}") from exc
    if loaded is None:
        loaded = {}
    if not isinstance(loaded, dict):
        raise RepoJournalFormatError(
            f"manifest must be a YAML mapping, got {type(loaded).__name__}"
        )
    data: dict[str, Any] = loaded

    signature_block = data.get("attribution_signature")
    nested: dict[str, Any] = signature_block if isinstance(signature_block, dict) else {}

    # --- bucket, from explicit bucket and/or lever vocabulary ---------------
    raw_buckets = _as_str_list(data.get("bucket"))
    raw_buckets += _as_str_list(_gather(data, nested, "levers", "lever"))
    buckets: list[str] = []
    for token in raw_buckets:
        mapped = REPO_LEVER_TO_BUCKET.get(token.lower().strip(), token.lower().strip())
        if mapped in BUCKETS and mapped not in buckets:
            buckets.append(mapped)

    # --- predicted impact ---------------------------------------------------
    impact_kwargs: dict[str, list[str]] = {
        "tasks_will_unlock": [],
        "tasks_will_stabilize": [],
        "tasks_at_risk": [],
    }
    structured_impact = data.get("predicted_impact")
    if isinstance(structured_impact, dict):
        for key in impact_kwargs:
            impact_kwargs[key] = _as_str_list(structured_impact.get(key))
    predicted_affected = _as_str_list(_gather(data, nested, "predicted_affected"))
    for task_id in predicted_affected:
        if (
            task_id not in impact_kwargs["tasks_will_unlock"]
            and task_id not in impact_kwargs["tasks_will_stabilize"]
        ):
            impact_kwargs["tasks_will_unlock"].append(task_id)
    predicted_impact = PredictedImpact(**impact_kwargs)

    # --- file changes (repo bullets or structured entries) ------------------
    file_changes = [
        _coerce_file_change(entry) for entry in _as_list(data.get("file_changes"))
    ]

    # --- capability evidence: keep only already-structured triples ----------
    capability_evidence: list[dict[str, Any]] = [
        entry for entry in _as_list(data.get("capability_evidence")) if isinstance(entry, dict)
    ]

    # --- attribution signature: keep only a real paper signature ------------
    attribution: AttributionSignature | None = None
    if isinstance(signature_block, dict) and signature_block.get("type") in SIGNATURE_TYPES:
        attribution = AttributionSignature(
            type=signature_block.get("type"),
            tool_name=signature_block.get("tool_name"),
            expected_min_calls=int(signature_block.get("expected_min_calls", 1) or 1),
        )

    hypothesis_id = _gather(data, nested, "hypothesis_id", "hypothesis")

    candidate_id = str(data.get("candidate_id") or fallback_candidate_id or "").strip()
    target_variant = str(data.get("target_variant") or fallback_target_variant or "").strip()

    return ChangeManifest.model_validate(
        {
            "candidate_id": candidate_id,
            "bucket": buckets,
            "iterates_from": data.get("iterates_from"),
            "capability_evidence": capability_evidence,
            "file_changes": file_changes,
            "predicted_impact": predicted_impact.model_dump(),
            "attribution_signature": (
                attribution.model_dump() if attribution is not None else None
            ),
            "target_variant": target_variant,
            "provenance": REPO_JOURNAL_PROVENANCE,
            "source_hypothesis_id": str(hypothesis_id).strip() if hypothesis_id else None,
        }
    )


def _as_list(value: Any) -> list[Any]:
    """Coerce a scalar/None/sequence into a list, preserving dict entries."""
    if value is None:
        return []
    if isinstance(value, (str, bytes)):
        text = value.decode() if isinstance(value, bytes) else value
        text = text.strip()
        return [text] if text else []
    if isinstance(value, dict):
        return [value]
    try:
        return list(value)
    except TypeError:
        return [value]
