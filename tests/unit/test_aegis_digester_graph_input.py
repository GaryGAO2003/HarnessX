# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""The AEGIS Digester's per-task input can be built from the causal cone (v6 M6b).

When an unfolded graph U exists for a failed task's run AND the graph path is
enabled, the Digester's evidence section is the causal cone of the run's terminal
invocation instead of a serialized-and-truncated trajectory window. When no U
exists (recording off, file missing, pre-unfold run) the existing text path runs
exactly as before. Which path executed is RECORDED per task — never inferred from
config — so a silent fall-back cannot masquerade as a graph-derived digest (the
4e0810f failure this module must not repeat).

No network / API keys: the completion path is a capturing stub and the U is built
on disk with the real writer, so resolution/serialisation are exercised for real.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import recipe.gaia_evolver.run_variant_pool as rvp  # noqa: E402
from experiments.variant_pool.evidence import TaskDigest  # noqa: E402
from harnessx.graph.causal import terminal_node  # noqa: E402
from harnessx.graph.types import unfolded_id  # noqa: E402
from harnessx.graph.unfold import (  # noqa: E402
    UnfoldedEdge,
    UnfoldedGraph,
    UnfoldedNode,
    write_unfolded,
)

_OK_JSON = (
    '{"failure_category":"blocked_source",'
    '"implicated_components":["tools/Bash"],'
    '"evidence_anchors":["step 3"],"notes":""}'
)


# ─── stubs / builders ─────────────────────────────────────────────────────────


class _CapturingProvider:
    """Async provider stub: returns a canned reply, records every prompt seen."""

    def __init__(self, reply: str = _OK_JSON) -> None:
        self._reply = reply
        self.prompts: list[str] = []

    async def complete(self, messages, tools):  # noqa: ANN001 - test stub
        self.prompts.append(str(messages[0].content))
        return SimpleNamespace(content=self._reply)


def _control_u(run_dir_session: str, n_nodes: int, *, run_id: str = "run123") -> UnfoldedGraph:
    """A linear control chain 0->1->...->(n-1); terminal is the highest ordinal."""
    nodes = [
        UnfoldedNode(
            id=unfolded_id(f"tool:T{i}", i),
            static_node_id=f"tool:T{i}",
            graphed=True,
            hook="tool" if i % 2 else "before_model",
            step=i,
            ordinal=i,
            label=f"Tool{i}",
        )
        for i in range(n_nodes)
    ]
    edges = [
        UnfoldedEdge(
            source=nodes[i].id,
            target=nodes[i + 1].id,
            edge_type=rvp._U_CONTROL,
            metadata={"hook": "control"},
        )
        for i in range(n_nodes - 1)
    ]
    # One data edge so DATA FLOW renders (writer ordinal 0 -> reader ordinal n-1).
    if n_nodes >= 2:
        edges.append(
            UnfoldedEdge(
                source=nodes[0].id,
                target=nodes[-1].id,
                edge_type=rvp._U_DATA,
                metadata={"slot_key": "scratch"},
            )
        )
    return UnfoldedGraph(run_id=run_id, session_id=run_dir_session, nodes=nodes, edges=edges)


def _layout(tmp_path: Path, *, task_id: str = "t1", body: str = "line\n" * 50) -> Path:
    """Write the trajectory .md; return the vround dir (trajectories/ + sessions/ siblings)."""
    vround = tmp_path / "R1" / "V0"
    traj_dir = vround / "trajectories"
    traj_dir.mkdir(parents=True, exist_ok=True)
    (traj_dir / f"{task_id}.md").write_text(body, encoding="utf-8")
    return vround


def _write_u(vround: Path, graph: UnfoldedGraph) -> Path:
    return write_unfolded(graph, base_dir=str(vround / "sessions"))


def _digest(task_id: str = "t1") -> TaskDigest:
    return TaskDigest(
        task_id=task_id,
        round_idx=1,
        variant_id="v1",
        outcome=(0, 2),
        evidence_anchors=[f"trajectories/{task_id}.md"],
    )


def _digester(vround: Path, *, graph_input: bool, provider: _CapturingProvider) -> rvp._LLMDigester:
    return rvp._LLMDigester(
        evidence=None,
        pool=None,
        provider=provider,
        tasks_by_id={},
        run_dir=vround,
        fallback=None,
        graph_input=graph_input,
    )


def _ctx(vround: Path) -> SimpleNamespace:
    return SimpleNamespace(trajectories_dir=vround / "trajectories")


# ─── 1. U available -> graph-derived input, recorded as such ──────────────────


async def test_graph_input_used_and_recorded_when_u_exists(tmp_path) -> None:
    vround = _layout(tmp_path)
    session = f"R1-V0-active-{'t1'}"
    graph = _control_u(session, 6)
    _write_u(vround, graph)

    provider = _CapturingProvider()
    digester = _digester(vround, graph_input=True, provider=provider)
    new_digest, note = await digester._interpret_failed_task(_ctx(vround), _digest())

    assert note is None  # parsed the canned JSON, no fall-back
    assert new_digest.failure_category == "blocked_source"

    assert len(digester.input_provenance) == 1
    prov = digester.input_provenance[0]
    assert prov.source == "graph"
    assert prov.reason is None
    # Anchor is the run's terminal invocation (highest ordinal).
    assert prov.anchor == terminal_node(graph)
    assert prov.nodes == 6

    prompt = provider.prompts[0]
    assert "CAUSAL CONE" in prompt
    assert "INVOCATIONS (ordinal:" in prompt
    assert "DATA FLOW" in prompt
    # It is the graph path, not the text window.
    assert "TRAJECTORY HEAD" not in prompt


# ─── 2. No U (enabled) -> text path, recorded unavailable WITH the reason ──────


async def test_no_u_falls_back_to_text_and_records_reason(tmp_path) -> None:
    # Trajectory present, but no sessions/ dir beside it.
    vround = _layout(tmp_path)
    provider = _CapturingProvider()
    digester = _digester(vround, graph_input=True, provider=provider)

    new_digest, note = await digester._interpret_failed_task(_ctx(vround), _digest())

    assert note is None  # text path parsed the JSON fine
    assert new_digest.failure_category == "blocked_source"

    assert len(digester.input_provenance) == 1
    prov = digester.input_provenance[0]
    assert prov.source == "text"
    assert prov.reason is not None and "sessions dir" in prov.reason

    prompt = provider.prompts[0]
    assert "TRAJECTORY HEAD" in prompt
    assert "CAUSAL CONE" not in prompt


async def test_no_u_file_in_session_dir_records_that_reason(tmp_path) -> None:
    # sessions/<matching dir> exists but holds no *_unfolded.jsonl.
    vround = _layout(tmp_path)
    (vround / "sessions" / "R1-V0-active-t1").mkdir(parents=True)
    provider = _CapturingProvider()
    digester = _digester(vround, graph_input=True, provider=provider)

    await digester._interpret_failed_task(_ctx(vround), _digest())

    prov = digester.input_provenance[0]
    assert prov.source == "text"
    assert "no unfolded graph U file" in prov.reason


# ─── 3. Size: the graph-derived input is materially smaller (real numbers) ────


async def test_graph_input_materially_smaller_than_text(tmp_path, capsys) -> None:
    # A large trajectory (the text path sends head+tail of it); a small U.
    big_body = "The agent searched, read, and reasoned in a long trace.\n" * 6000
    vround = _layout(tmp_path, body=big_body)
    graph = _control_u("R1-V0-active-t1", 20)
    _write_u(vround, graph)
    digest = _digest()

    text_provider = _CapturingProvider()
    text_digester = _digester(vround, graph_input=False, provider=text_provider)
    await text_digester._interpret_failed_task(_ctx(vround), digest)
    text_len = len(text_provider.prompts[0])

    graph_provider = _CapturingProvider()
    graph_digester = _digester(vround, graph_input=True, provider=graph_provider)
    await graph_digester._interpret_failed_task(_ctx(vround), digest)
    graph_len = len(graph_provider.prompts[0])
    cone_chars = graph_digester.input_provenance[0].chars

    with capsys.disabled():
        print(
            f"\n[M6b size] text-path prompt={text_len} chars; "
            f"graph-path prompt={graph_len} chars; cone={cone_chars} chars; "
            f"ratio={text_len / max(graph_len, 1):.1f}x"
        )
    # Materially smaller: at least 4x here, and far under the cap.
    assert graph_len * 4 < text_len
    assert cone_chars <= rvp._LLM_DIGESTER_GRAPH_INPUT_CAP


# ─── 4. Anchors: terminal invocation; empty U -> no usable anchor -> text ─────


async def test_anchor_is_terminal_invocation(tmp_path) -> None:
    vround = _layout(tmp_path)
    graph = _control_u("R1-V0-active-t1", 9)
    _write_u(vround, graph)
    digester = _digester(vround, graph_input=True, provider=_CapturingProvider())

    await digester._interpret_failed_task(_ctx(vround), _digest())
    prov = digester.input_provenance[0]
    # Highest ordinal node is the anchor, and its whole ancestor chain is the cone.
    assert prov.anchor == unfolded_id("tool:T8", 8)
    assert prov.nodes == 9


async def test_empty_u_has_no_usable_anchor_and_falls_back(tmp_path) -> None:
    vround = _layout(tmp_path)
    empty = UnfoldedGraph(run_id="run123", session_id="R1-V0-active-t1", nodes=[], edges=[])
    _write_u(vround, empty)
    provider = _CapturingProvider()
    digester = _digester(vround, graph_input=True, provider=provider)

    await digester._interpret_failed_task(_ctx(vround), _digest())
    prov = digester.input_provenance[0]
    assert prov.source == "text"
    assert "no invocations" in prov.reason
    assert "TRAJECTORY HEAD" in provider.prompts[0]


# ─── 5. Truncation, when it fires, is VISIBLE in the record and the text ──────


async def test_oversized_cone_truncation_is_visible(tmp_path) -> None:
    vround = _layout(tmp_path)
    # ~1400 invocation lines overrun the 32k cap; the cone is the full chain.
    graph = _control_u("R1-V0-active-t1", 1400)
    _write_u(vround, graph)
    provider = _CapturingProvider()
    digester = _digester(vround, graph_input=True, provider=provider)

    await digester._interpret_failed_task(_ctx(vround), _digest())
    prov = digester.input_provenance[0]
    assert prov.source == "graph"
    assert prov.truncated is True
    assert prov.chars <= rvp._LLM_DIGESTER_GRAPH_INPUT_CAP
    assert "truncated to 32000 chars" in provider.prompts[0]


# ─── 6. Regression: graph path OFF -> byte-identical prompt, nothing recorded ─


async def test_graph_disabled_is_byte_identical_to_today(tmp_path) -> None:
    # A U is present on disk; with the graph path off it must be ignored entirely.
    vround = _layout(tmp_path)
    _write_u(vround, _control_u("R1-V0-active-t1", 6))
    digest = _digest()

    provider = _CapturingProvider()
    digester = _digester(vround, graph_input=False, provider=provider)
    await digester._interpret_failed_task(_ctx(vround), digest)

    # The exact prompt today's text path would build from the trajectory window.
    frontmatter, head, tail = digester._trajectory_window(digest)
    expected = digester._build_task_prompt(
        digest=digest,
        question=digester._question_for(digest.task_id),
        frontmatter=frontmatter,
        head=head,
        tail=tail,
        retry_error=None,
    )
    assert provider.prompts[0] == expected
    # Nothing recorded -> the audit block stays empty -> payload byte-identical.
    assert digester.input_provenance == []
