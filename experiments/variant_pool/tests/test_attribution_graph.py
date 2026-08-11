# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""v6 M7 — the W19 attribution signature as a graph existence query.

The paper's attribution signature is the anti-reward-hacking clause: an edit
whose declared trace feature never fires did not run. As a *string* check it only
proves the manifest declares a feature — a candidate satisfies it by emitting
text that looks like evidence. Turned into a *query over the unfolded graph U* it
proves the edited node actually executed, and text can no longer stand in.

These tests pin the four behaviours the spec requires: satisfied (the node ran),
refused (the node is absent from U — the change was made but never ran), fallback
(no U, today's structural check runs and the record says why), and the untouched
prompt-bucket exemption — plus the additive-regression guarantee that the default
(no U) path is byte-identical to before.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from harnessx.graph.types import unfolded_id
from harnessx.graph.unfold import (
    UnfoldedGraph,
    UnfoldedNode,
    load_unfolded,
    write_unfolded,
)
from variant_pool.manifest import (
    AttributionGraphResult,
    ChangeManifest,
    check_attribution_in_graph,
)

# ---------------------------------------------------------------------------
# builders
# ---------------------------------------------------------------------------


def _u_node(base: str, ordinal: int, *, hook: str = "tool") -> UnfoldedNode:
    """One invocation of static node ``base`` at ``@t{ordinal}`` in U."""
    return UnfoldedNode(
        id=unfolded_id(base, ordinal),
        static_node_id=base,
        graphed=True,
        hook=hook,
        step=0,
        ordinal=ordinal,
        label=base,
    )


def _u(*nodes: UnfoldedNode, run_id: str = "run1", session_id: str = "R10-V0-active-t1") -> UnfoldedGraph:
    return UnfoldedGraph(run_id=run_id, session_id=session_id, nodes=list(nodes))


def _tool_candidate(**overrides) -> ChangeManifest:
    """A complete tools-bucket manifest attributing to the WikiTextFetch tool.

    Modelled on the paper's C-R10-02 (p.37) — the one worked instance whose
    signature is ``{type: tool_call, tool_name: WikiTextFetch}``.
    """
    data = {
        "candidate_id": "C-R10-02",
        "bucket": ["tools", "config"],
        "capability_evidence": [{"type": "other", "claim": "x", "evidence": "y"}],
        "file_changes": [{"path": "config.yaml", "action": "create", "diff_summary": "register WikiTextFetch"}],
        "predicted_impact": {"tasks_will_unlock": ["t1"]},
        "attribution_signature": {"type": "tool_call", "tool_name": "WikiTextFetch", "expected_min_calls": 1},
        "target_variant": "V0",
    }
    data.update(overrides)
    return ChangeManifest.model_validate(data)


# ===========================================================================
# 1. Satisfied — the edited node genuinely executed in U
# ===========================================================================


def test_signature_satisfied_when_the_edited_tool_actually_ran() -> None:
    manifest = _tool_candidate()
    # A run in which WikiTextFetch really fired (alongside unrelated nodes).
    graph = _u(_u_node("proc:memory", 0, hook="before_model"), _u_node("tool:WikiTextFetch", 3))

    result = check_attribution_in_graph(manifest, graph)
    assert result.available is True
    assert result.satisfied is True
    assert result.expected_node_id == "tool:WikiTextFetch"
    assert result.observed_count == 1

    # The graph query is folded into the completeness gate: a genuinely-run
    # signature adds no problem.
    assert manifest.validate_complete(unfolded=graph) == []


def test_signature_satisfied_survives_a_u_written_and_reloaded_from_disk(tmp_path: Path) -> None:
    """The satisfied case over a real serialized U (M4/M5 round-trip), not a stub."""
    manifest = _tool_candidate()
    graph = _u(_u_node("tool:WikiTextFetch", 1), _u_node("tool:WikiTextFetch", 5))
    path = write_unfolded(graph, base_dir=str(tmp_path))
    reloaded = load_unfolded(path)

    result = check_attribution_in_graph(manifest, reloaded)
    assert result.available is True
    assert result.satisfied is True
    assert result.observed_count == 2
    assert manifest.validate_complete(unfolded=reloaded) == []


# ===========================================================================
# 2. Refused — the change was made but the node never ran
# ===========================================================================


def test_signature_refused_when_the_edited_tool_is_absent_from_u() -> None:
    """The case the whole mechanism exists for: a real run that used a DIFFERENT
    tool, so the attributed node never executed."""
    manifest = _tool_candidate()
    # A genuine run: the agent called WebFetch (the old, broken path), never the
    # newly added WikiTextFetch the manifest credits its improvement to.
    graph = _u(
        _u_node("proc:memory", 0, hook="before_model"),
        _u_node("tool:WebFetch", 2),
        _u_node("tool:WebFetch", 4),
    )

    result = check_attribution_in_graph(manifest, graph)
    assert result.available is True
    assert result.satisfied is False
    assert result.expected_node_id == "tool:WikiTextFetch"
    assert result.observed_count == 0

    problems = manifest.validate_complete(unfolded=graph)
    assert any(p.startswith("attribution_signature") for p in problems), problems
    assert any("never ran" in p for p in problems), problems


def test_signature_refused_when_the_node_ran_fewer_times_than_declared() -> None:
    """Existence with cardinality: one firing does not satisfy expected_min_calls=2."""
    manifest = _tool_candidate(
        attribution_signature={"type": "tool_call", "tool_name": "WikiTextFetch", "expected_min_calls": 2},
    )
    graph = _u(_u_node("tool:WikiTextFetch", 3))

    result = check_attribution_in_graph(manifest, graph)
    assert result.available is True
    assert result.satisfied is False
    assert result.observed_count == 1
    assert result.expected_min_calls == 2
    assert any("attribution_signature" in p for p in manifest.validate_complete(unfolded=graph))


# ===========================================================================
# 3. Fallback — no U (or an unbindable signature): today's check, recorded reason
# ===========================================================================


def test_fallback_records_that_the_graph_check_was_unavailable_with_no_u() -> None:
    manifest = _tool_candidate()

    result = check_attribution_in_graph(manifest, None)
    assert result.available is False
    assert result.satisfied is None
    assert "no unfolded graph U available" in result.reason
    assert "structural W19 declaration check" in result.reason
    # No U threaded in -> the completeness gate is exactly today's structural one.
    assert manifest.validate_complete() == []
    assert manifest.validate_complete(unfolded=None) == []


def test_fallback_records_why_a_processor_signature_cannot_be_bound() -> None:
    """A signature type that names no graph node is honestly unavailable, not a
    silent pass claiming U proved something."""
    manifest = _tool_candidate(
        bucket=["processor", "config"],
        attribution_signature={"type": "processor_invocation", "expected_min_calls": 1},
    )
    graph = _u(_u_node("proc:memory", 0, hook="before_model"))

    result = check_attribution_in_graph(manifest, graph)
    assert result.available is False
    assert result.satisfied is None
    assert "names no graph node id" in result.reason
    # Unbindable -> the graph query adds no problem; the structural gate stands.
    assert manifest.validate_complete(unfolded=graph) == []


# ===========================================================================
# 4. Prompt bucket stays exempt
# ===========================================================================


def test_prompt_bucket_candidate_stays_exempt_even_with_a_u_present() -> None:
    prompt_only = ChangeManifest.model_validate(
        {
            "candidate_id": "C-R3-01",
            "bucket": ["prompt"],
            "capability_evidence": [],
            "file_changes": [{"path": "gaia_agent.md", "action": "modify", "diff_summary": "one line"}],
            "predicted_impact": {"tasks_will_unlock": ["t1"]},
            "target_variant": "V0",
        }
    )
    assert prompt_only.attribution_signature is None
    # A U in which nothing the candidate touched appears must not make an exempt
    # prompt candidate fail — the exemption is untouched by the graph check.
    graph = _u(_u_node("tool:WebFetch", 1))
    assert prompt_only.validate_complete(unfolded=graph) == []
    assert prompt_only.validate_complete() == []


# ===========================================================================
# 6. Regression — with the graph check disabled, behaviour is identical
# ===========================================================================


@pytest.mark.parametrize(
    "manifest",
    [
        _tool_candidate(),
        _tool_candidate(attribution_signature=None),  # non-prompt, missing signature
        _tool_candidate(bucket=["config"], attribution_signature={"type": "tool_call", "expected_min_calls": 1}),
    ],
)
def test_default_path_is_byte_identical_to_before(manifest: ChangeManifest) -> None:
    """The additive guarantee: no ``unfolded`` argument reproduces today's list."""
    assert manifest.validate_complete() == manifest.validate_complete(unfolded=None)


def test_result_is_an_immutable_record() -> None:
    result = check_attribution_in_graph(_tool_candidate(), None)
    assert isinstance(result, AttributionGraphResult)
    with pytest.raises(Exception):
        result.available = True  # type: ignore[misc]
