# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""Graph-backed signature check (module G2, piece 1).

The vendored official attributor (:mod:`harnessx.aegis.data.attribution`) answers
"did this ship's mechanical signature fire on a task?" by **regex-parsing digest
markdown** — ``_TOOL_COUNT_RE`` over a ``tool_call_counts`` table.  A trajectory
that merely *looks* like it invoked a tool (the string is present in the text)
satisfies that check.

The unfolded graph U records the same fact **structurally**: a ``tool:<name>``
invocation node exists in the run's U with N occurrences, or it does not.  This
module answers the identical direct/orphan/joint question by counting those nodes
instead of parsing text — an answer that cannot be forged by evidence-shaped
prose.

Signature schema (mirrors the vendored ``attribution.py`` docstring)::

    {type: tool_call, tool_name: <str>, expected_min_calls: <int>}   # tools bucket
    {type: processor_invocation, class_name: <str>}                  # processor bucket
    None                                                             # prompt/config → joint

Only ``tool_call`` has a ``tool:<name>`` node representation, so only it is
answered by the graph backend.  For ``processor_invocation``, unknown types, and
the prompt/config buckets (no signature at all), the result says so — it records
``backend="none"`` with a reason, per the 4e0810f discipline of reporting what was
actually answered rather than guessing.  ``min_calls`` is read from
``expected_min_calls`` (official field) or ``min_calls`` (alias), defaulting to the
same floor the vendored code uses.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..graph.unfold import UnfoldedGraph

# The floor the vendored attributor uses: a single accidental call does not credit
# a ship. Kept in sync with ``attribution._DEFAULT_MIN_CALLS``.
_DEFAULT_MIN_CALLS = 1

# The three official labels (attribution.py). We speak the same vocabulary so a
# downstream consumer never has to translate between the text backend and this
# graph backend.
DIRECT = "direct"
ORPHAN = "orphan"
JOINT = "joint"

# Which backend produced the answer. ``graph`` = counted ``tool:<name>`` nodes;
# ``none`` = the graph has no representation for this signature type, so it did
# NOT answer (the label is joint-by-honest-abstention, not a measured result).
BACKEND_GRAPH = "graph"
BACKEND_NONE = "none"

_TOOL_CALL = "tool_call"


@dataclass(frozen=True)
class GraphSignatureResult:
    """The graph backend's answer for one signature against one U.

    ``label`` is one of the official ``direct``/``orphan``/``joint`` strings.
    ``backend`` states who answered: ``graph`` (a real node count) or ``none``
    (no graph representation — the label is an honest abstention, never a guess).
    ``fired`` is ``True``/``False`` only when the graph answered; ``None`` marks
    "not answered by this backend". The ``tool_name``/``count``/``min_calls`` fields
    carry the evidence a reader (or a mutation test) can check.
    """

    label: str
    backend: str
    reason: str
    fired: bool | None = None
    tool_name: str = ""
    count: int = 0
    min_calls: int = 0

    @property
    def answered_by_graph(self) -> bool:
        """True only when a ``tool:<name>`` node count actually decided the label."""
        return self.backend == BACKEND_GRAPH


def count_tool_invocations(u: UnfoldedGraph, tool_name: str) -> int:
    """Number of ``tool:<tool_name>`` invocation nodes in ``u``.

    A tool call mints one U node with ``static_node_id == f"tool:{tool_name}"`` and
    ``hook == "tool"`` (see :meth:`UnfoldRecorder.record_tool_invocation`).  Each
    such node is one distinct invocation (unique ordinal), so counting them is the
    structural equivalent of the vendored ``tool_call_counts[tool_name]``.
    """
    target = f"tool:{tool_name}"
    return sum(1 for n in u.nodes if n.static_node_id == target and n.hook == "tool")


def _resolve_min_calls(signature: dict) -> int:
    """Read the invocation floor from ``expected_min_calls`` (official) or the
    ``min_calls`` alias; fall back to the vendored default on absent/garbage."""
    for key in ("expected_min_calls", "min_calls"):
        if signature.get(key) is not None:
            try:
                return int(signature[key])
            except (TypeError, ValueError):
                continue
    return _DEFAULT_MIN_CALLS


def check_signature_in_u(signature: dict | None, u: UnfoldedGraph) -> GraphSignatureResult:
    """Answer fired/not-fired for ``signature`` against ``u`` by node existence.

    - ``tool_call`` with a ``tool_name`` → counted structurally: ``direct`` when the
      count meets the floor, ``orphan`` when it does not (both ``backend="graph"``).
    - No signature (prompt/config bucket) → ``joint`` by definition, ``backend="none"``.
    - ``processor_invocation`` / unknown type / ``tool_call`` missing its name → no
      ``tool:<name>`` representation → ``joint`` with ``backend="none"`` and a reason
      that names why the graph did not answer. Never a guess.
    """
    if not signature:
        return GraphSignatureResult(
            label=JOINT,
            backend=BACKEND_NONE,
            fired=None,
            reason="no mechanical signature (prompt/config bucket) — joint by definition",
        )

    sig_type = str(signature.get("type") or "")
    if sig_type == _TOOL_CALL:
        tool_name = str(signature.get("tool_name") or "")
        if not tool_name:
            return GraphSignatureResult(
                label=JOINT,
                backend=BACKEND_NONE,
                fired=None,
                reason="tool_call signature carries no tool_name — the graph cannot answer it",
            )
        min_calls = _resolve_min_calls(signature)
        count = count_tool_invocations(u, tool_name)
        if count >= min_calls:
            return GraphSignatureResult(
                label=DIRECT,
                backend=BACKEND_GRAPH,
                fired=True,
                tool_name=tool_name,
                count=count,
                min_calls=min_calls,
                reason=f"tool:{tool_name} present in U with {count} invocation(s) (>= {min_calls})",
            )
        return GraphSignatureResult(
            label=ORPHAN,
            backend=BACKEND_GRAPH,
            fired=False,
            tool_name=tool_name,
            count=count,
            min_calls=min_calls,
            reason=f"tool:{tool_name} not present in U at the required floor ({count} < {min_calls})",
        )

    # processor_invocation, unknown types: no tool-node representation. The vendored
    # attributor answers processor_invocation by text-matching a class name; the
    # graph backend does not, so it abstains honestly rather than guess from labels.
    return GraphSignatureResult(
        label=JOINT,
        backend=BACKEND_NONE,
        fired=None,
        tool_name=str(signature.get("tool_name") or signature.get("class_name") or ""),
        reason=(
            f"signature type {sig_type!r} has no tool-node representation in the graph — "
            "the graph backend does not answer it (no guess)"
        ),
    )


def infer_signature(
    bucket: str | None,
    manifest: dict | None,
    attribution_signature: dict | None = None,
) -> dict | None:
    """Resolve the signature to check for a candidate, exactly as the vendored
    attributor would.

    Mirrors ``attribution.compute_evidence``'s resolution order — an explicit
    ``attribution_signature`` wins; otherwise the default is inferred from the
    bucket + manifest by the **vendored** ``_infer_default_signature`` (reused, not
    reimplemented, so the graph gate checks the identical signature the official
    text attributor would).  ``None`` means "no mechanical signature" (prompt/config).
    """
    if isinstance(attribution_signature, dict):
        return attribution_signature
    from ..aegis.data.attribution import _infer_default_signature

    return _infer_default_signature(bucket, manifest)


__all__ = [
    "DIRECT",
    "ORPHAN",
    "JOINT",
    "BACKEND_GRAPH",
    "BACKEND_NONE",
    "GraphSignatureResult",
    "count_tool_invocations",
    "check_signature_in_u",
    "infer_signature",
]
