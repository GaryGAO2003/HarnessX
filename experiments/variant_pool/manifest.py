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
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Literal

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
        """Serialise to the manifest YAML block (schema p.36)."""
        return yaml.safe_dump(
            self.model_dump(mode="json"),
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

    def validate_complete(self) -> list[str]:
        """Everything wrong with this manifest, as human-readable strings.

        Empty list = complete = gate stage 1 passes. Each entry starts with the
        offending field name so the archived rejection reason (§4.3 "archived
        with rejection reason") reads cleanly.

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
        problems.extend(self._attribution_problems())

        if not self.target_variant.strip():
            problems.append("target_variant: missing (ours; required under variant isolation)")

        return problems

    def _capability_evidence_problems(self) -> list[str]:
        problems: list[str] = []
        if self.needs_code_verification() and not self.capability_evidence:
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

    def _attribution_problems(self) -> list[str]:
        signature = self.attribution_signature
        if signature is None or signature.type is None:
            if self.needs_attribution():
                return [
                    "attribution_signature: missing for a non-prompt candidate "
                    f"(bucket={self.bucket}; W19 hard gate)"
                ]
            return []
        problems: list[str] = []
        if signature.type == "tool_call" and not (signature.tool_name or "").strip():
            problems.append("attribution_signature: tool_call needs tool_name")
        if signature.expected_min_calls < 1:
            problems.append(
                f"attribution_signature: expected_min_calls must be >= 1, got {signature.expected_min_calls}"
            )
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
