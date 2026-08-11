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

import json
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


# ─── 3. The graph path is causally TARGETED, not merely smaller (real numbers) ─
#
# Size was never the win — causal selection was. On a realistic trajectory (real
# ``### Step`` blocks, the shape ``run._build_trajectory_text`` writes) the graph path
# is roughly the same order of magnitude as the text window, not 4x smaller; what makes
# it better is that it carries the CONE's content and drops the steps no cone node
# touches. This asserts exactly that, and prints the true sizes for both paths.
# (The M6b-era "≥4x smaller" claim only held for a degenerate body with no step blocks,
# where content selection found nothing and the graph path silently stayed structure-only.)


async def test_graph_path_is_causally_targeted_not_merely_smaller(tmp_path, capsys) -> None:
    in_cone_marker = "CANBERRA_IS_THE_TERMINAL_ANSWER"
    out_of_cone_marker = "STEP_NO_CONE_NODE_TOUCHES"

    # Three substantial in-cone steps (the cone below covers steps 0,1,2)...
    steps = [
        {
            "step": 0,
            "thinking": "opening the task " + "detail " * 900,
            "tool": "Read",
            "args": {"path": "brief.txt"},
            "result": "brief " + "x" * 3000,
        },
        {
            "step": 1,
            "thinking": "narrowing " + "y" * 4000,
            "tool": "WebSearch",
            "args": {"query": "capital of Australia"},
            "result": "hits " + "z" * 3000,
        },
        {
            "step": 2,
            "thinking": "concluding",
            "response": "final",
            "tool": "WebSearch",
            "args": {"query": "confirm capital"},
            "result": in_cone_marker,
        },
    ]
    # ...and many out-of-cone steps: real ### Step blocks the cone never reaches, present
    # only to make the trajectory long enough that the text path must window it (>32k).
    # The distinctive out-of-cone marker sits in the LAST step so it lands in the text
    # tail window — proving the graph path's exclusion is causal SELECTION, not truncation.
    for sid in range(50, 82):
        result = out_of_cone_marker if sid == 81 else f"unrelated-{sid}"
        steps.append(
            {
                "step": sid,
                "thinking": f"side branch {sid} " + "q" * 900,
                "tool": "Bash",
                "args": {"cmd": f"echo {sid}"},
                "result": result,
            }
        )

    body = _traj_body(steps)
    vround = _layout(tmp_path, body=body)
    _write_u(vround, _chain_u("R1-V0-active-t1", [0, 1, 2]))  # cone = steps 0,1,2 only
    digest = _digest()

    text_provider = _CapturingProvider()
    await _digester(vround, graph_input=False, provider=text_provider)._interpret_failed_task(_ctx(vround), digest)
    text_prompt = text_provider.prompts[0]
    text_len = len(text_prompt)

    graph_provider = _CapturingProvider()
    graph_digester = _digester(vround, graph_input=True, provider=graph_provider)
    await graph_digester._interpret_failed_task(_ctx(vround), digest)
    graph_prompt = graph_provider.prompts[0]
    graph_len = len(graph_prompt)
    prov = graph_digester.input_provenance[0]

    with capsys.disabled():
        print(
            f"\n[M7 targeted] text-path prompt={text_len} chars; "
            f"graph-path prompt={graph_len} chars (evidence={prov.chars}); "
            f"cap={rvp._LLM_DIGESTER_GRAPH_INPUT_CAP}; "
            f"relationship={text_len / max(graph_len, 1):.2f}x (targeted, not tiny)"
        )

    assert prov.source == "graph"
    # (a) Within the cap.
    assert prov.chars <= rvp._LLM_DIGESTER_GRAPH_INPUT_CAP
    # (b) Carries the cone's content.
    assert in_cone_marker in graph_prompt
    # (c) Excludes content from steps no cone node touches...
    assert out_of_cone_marker not in graph_prompt
    # ...which the text path DID carry (its tail window), so this is selection, not size.
    assert out_of_cone_marker in text_prompt
    # (d) The honest size relationship: the text path windowed to the cap, the graph path
    # is smaller but the SAME order of magnitude — not the degenerate ≥4x of the old body.
    assert text_len > rvp._LLM_DIGESTER_GRAPH_INPUT_CAP  # long trajectory -> text windowed
    assert graph_len < text_len
    assert graph_len * 4 > text_len  # decisively NOT 4x smaller on a realistic input


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


# ─── v6 M7: the cone SELECTS content, it does not replace it ──────────────────
#
# M6b sent the cone STRUCTURE (ordinals/hooks/labels/data-flow) and nothing else, so
# the Digester could see that ordinal 47 was a WebSearch at step 3 but not what was
# searched, returned, or reasoned. M7 fills the remaining budget with the ACTUAL
# trajectory content for the cone's steps. The fixtures below carry a REAL trajectory
# body (``### Step`` blocks, exactly the shape ``run._build_trajectory_text`` writes)
# so content selection is exercised for real, not against placeholders.


def _traj_body(steps: list[dict]) -> str:
    """A trajectory body of ``### Step`` blocks, mirroring ``run._build_trajectory_text``.

    Each ``steps`` entry is a dict with a ``step`` id and any of ``thinking`` /
    ``response`` / ``tool`` (+ ``args`` dict + ``result``); the tool call is rendered
    exactly as the recipe writes it so the parser and the real writer agree.
    """
    out = ["# Trajectory: t1", "", "## Task", "", "the question", "", "---", "", "## Execution Steps", ""]
    for step in steps:
        out.append(f"### Step {step['step']}")
        if step.get("thinking"):
            out += ["", "#### Thinking", "", step["thinking"]]
        if step.get("response"):
            out += ["", "#### Response", "", step["response"]]
        if step.get("tool"):
            out += [
                "",
                "#### Tool Calls",
                "",
                f"- **{step['tool']}**(`{json.dumps(step.get('args', {}), ensure_ascii=False)}`)",
                f"  -> {step['tool']}: {step.get('result', '')}",
            ]
        out.append("")
    return "\n".join(out)


def _chain_u(session: str, step_ids: list[int], *, run_id: str = "run123", data_slot: str = "memory") -> UnfoldedGraph:
    """Linear control chain over ``step_ids`` (ordinal = position); data edge first->last.

    Every node is an ancestor of the terminal (highest ordinal), so the whole chain is
    in the cone — content selection is driven purely by budget, not by topology.
    """
    nodes = [
        UnfoldedNode(
            id=unfolded_id(f"tool:S{sid}", ordinal),
            static_node_id=f"tool:S{sid}",
            graphed=True,
            hook="tool" if ordinal % 2 else "before_model",
            step=sid,
            ordinal=ordinal,
            label=f"Node{sid}",
        )
        for ordinal, sid in enumerate(step_ids)
    ]
    edges = [
        UnfoldedEdge(
            source=nodes[i].id,
            target=nodes[i + 1].id,
            edge_type=rvp._U_CONTROL,
            metadata={"hook": "control"},
        )
        for i in range(len(nodes) - 1)
    ]
    if len(nodes) >= 2:
        edges.append(
            UnfoldedEdge(
                source=nodes[0].id,
                target=nodes[-1].id,
                edge_type=rvp._U_DATA,
                metadata={"slot_key": data_slot},
            )
        )
    return UnfoldedGraph(run_id=run_id, session_id=session, nodes=nodes, edges=edges)


def _branched_u(session: str, *, run_id: str = "run123") -> UnfoldedGraph:
    """Cone chain A->B->C (steps 0,1,2) to the terminal, plus a DISCONNECTED side chain
    X->Y (steps 90,91) that never reaches the terminal, so its steps are outside the cone.

    C has the highest ordinal (4) -> terminal; X/Y carry lower ordinals but no path to C,
    so ``causal_cone`` excludes them. Their trajectory content must never be selected.
    """
    a = UnfoldedNode(
        id=unfolded_id("tool:A", 0),
        static_node_id="tool:A",
        graphed=True,
        hook="before_model",
        step=0,
        ordinal=0,
        label="A",
    )
    b = UnfoldedNode(
        id=unfolded_id("tool:B", 1), static_node_id="tool:B", graphed=True, hook="tool", step=1, ordinal=1, label="B"
    )
    x = UnfoldedNode(
        id=unfolded_id("tool:X", 2), static_node_id="tool:X", graphed=True, hook="tool", step=90, ordinal=2, label="X"
    )
    y = UnfoldedNode(
        id=unfolded_id("tool:Y", 3), static_node_id="tool:Y", graphed=True, hook="tool", step=91, ordinal=3, label="Y"
    )
    c = UnfoldedNode(
        id=unfolded_id("tool:C", 4), static_node_id="tool:C", graphed=True, hook="tool", step=2, ordinal=4, label="C"
    )
    edges = [
        UnfoldedEdge(source=a.id, target=b.id, edge_type=rvp._U_CONTROL, metadata={"hook": "control"}),
        UnfoldedEdge(source=b.id, target=c.id, edge_type=rvp._U_CONTROL, metadata={"hook": "control"}),
        UnfoldedEdge(source=a.id, target=c.id, edge_type=rvp._U_DATA, metadata={"slot_key": "memory"}),
        UnfoldedEdge(source=x.id, target=y.id, edge_type=rvp._U_CONTROL, metadata={"hook": "control"}),
    ]
    return UnfoldedGraph(run_id=run_id, session_id=session, nodes=[a, b, x, y, c], edges=edges)


# ─── M7.1 Content is present: reasoning, a tool argument, and a tool return ────


async def test_graph_content_reasoning_args_and_returns_present(tmp_path) -> None:
    body = _traj_body(
        [
            {"step": 0, "thinking": "warming up"},
            {"step": 1, "tool": "Read", "args": {"path": "a.txt"}, "result": "some file contents"},
            {
                "step": 2,
                "thinking": "REASONED_ABOUT_CAPITAL",
                "response": "the answer is Canberra",
                "tool": "WebSearch",
                "args": {"query": "DISTINCTIVE_QUERY_ARG"},
                "result": "DISTINCTIVE_RETURN_VALUE",
            },
        ]
    )
    vround = _layout(tmp_path, body=body)
    _write_u(vround, _chain_u("R1-V0-active-t1", [0, 1, 2]))

    provider = _CapturingProvider()
    digester = _digester(vround, graph_input=True, provider=provider)
    await digester._interpret_failed_task(_ctx(vround), _digest())

    prompt = provider.prompts[0]
    assert digester.input_provenance[0].source == "graph"
    # Model reasoning text, a tool ARGUMENT, and a tool RETURN value — all real strings.
    assert "REASONED_ABOUT_CAPITAL" in prompt
    assert "DISTINCTIVE_QUERY_ARG" in prompt
    assert "DISTINCTIVE_RETURN_VALUE" in prompt
    # Still the graph path, and structure is still there.
    assert "SELECTED CONTENT" in prompt
    assert "INVOCATIONS (ordinal:" in prompt
    assert "TRAJECTORY HEAD" not in prompt


# ─── M7.2 Selection is causal: content outside the cone is absent ─────────────


async def test_graph_content_outside_cone_is_absent(tmp_path) -> None:
    body = _traj_body(
        [
            {"step": 0, "thinking": "chain start"},
            {"step": 1, "tool": "Read", "args": {"p": "x"}, "result": "in-cone read"},
            {"step": 2, "tool": "WebSearch", "args": {"q": "final"}, "result": "CANBERRA_CAPITAL_CONFIRMED"},
            {
                "step": 90,
                "thinking": "SECRET_OUTSIDE_CONE",
                "tool": "Bash",
                "args": {"cmd": "ls"},
                "result": "OUTSIDE_RESULT",
            },
            {"step": 91, "response": "also outside the cone"},
        ]
    )
    vround = _layout(tmp_path, body=body)
    _write_u(vround, _branched_u("R1-V0-active-t1"))

    provider = _CapturingProvider()
    digester = _digester(vround, graph_input=True, provider=provider)
    await digester._interpret_failed_task(_ctx(vround), _digest())

    prompt = provider.prompts[0]
    assert digester.input_provenance[0].source == "graph"
    assert digester.input_provenance[0].nodes == 3  # A, B, C only
    # In-cone content (step 2 == terminal) is selected...
    assert "CANBERRA_CAPITAL_CONFIRMED" in prompt
    # ...and content for the disconnected side chain (steps 90/91) is NOT.
    assert "SECRET_OUTSIDE_CONE" not in prompt
    assert "OUTSIDE_RESULT" not in prompt


# ─── M7.3 Structure survives: the cone's data-flow edges stay in the prompt ───


async def test_graph_structure_dataflow_survives_alongside_content(tmp_path) -> None:
    body = _traj_body(
        [
            {"step": 0, "thinking": "t0"},
            {"step": 1, "tool": "Read", "args": {"p": "a"}, "result": "SELECTED_R1"},
            {"step": 2, "response": "done"},
        ]
    )
    vround = _layout(tmp_path, body=body)
    _write_u(vround, _chain_u("R1-V0-active-t1", [0, 1, 2], data_slot="memory"))

    provider = _CapturingProvider()
    digester = _digester(vround, graph_input=True, provider=provider)
    await digester._interpret_failed_task(_ctx(vround), _digest())

    prompt = provider.prompts[0]
    # The data-flow edge line the prose does not carry (writer ord 0 -> reader ord 2).
    assert "DATA FLOW (slot: writer_ordinal -> reader_ordinal):" in prompt
    assert "memory: 0 -> 2" in prompt
    # ...and content is present alongside it.
    assert "SELECTED_R1" in prompt


# ─── M7.4 Budget: the window is now used meaningfully, and under the cap ──────


async def test_graph_uses_window_meaningfully_under_cap(tmp_path, capsys) -> None:
    # Cone steps carrying substantial content that all fits under the cap.
    steps = [
        {
            "step": sid,
            "thinking": f"reasoning at step {sid} " + "x" * 2000,
            "tool": "Read",
            "args": {"path": f"f{sid}.txt"},
            "result": f"result-{sid} " + "y" * 1500,
        }
        for sid in range(6)
    ]
    vround = _layout(tmp_path, body=_traj_body(steps))
    _write_u(vround, _chain_u("R1-V0-active-t1", list(range(6))))

    provider = _CapturingProvider()
    digester = _digester(vround, graph_input=True, provider=provider)
    await digester._interpret_failed_task(_ctx(vround), _digest())

    prov = digester.input_provenance[0]
    # Structure-only (M6b) for THIS cone would be a few hundred chars; content selection
    # now fills the window meaningfully, and still stays under the cap. Recompute the
    # structure-only rendering (empty body) to measure the before/after directly.
    structure_only, _ = digester._render_cone(_cone_of(vround), prov.anchor, "")
    with capsys.disabled():
        print(
            f"\n[M7 budget] graph-path evidence: structure-only={len(structure_only)} chars; "
            f"with-content={prov.chars} chars; cap={rvp._LLM_DIGESTER_GRAPH_INPUT_CAP}"
        )
    assert prov.chars > 15_000
    assert prov.chars <= rvp._LLM_DIGESTER_GRAPH_INPUT_CAP
    assert prov.truncated is False  # it all fit
    assert prov.chars > len(structure_only) * 10  # the window, not a fraction of it


# ─── M7.5 Overflow is VISIBLE: earliest dropped, nearest-anchor kept, marked ──


async def test_graph_content_overflow_marked_in_prompt_and_provenance(tmp_path) -> None:
    steps = [
        {
            "step": sid,
            "thinking": f"UNIQUE_STEP_{sid}_MARKER " + "z" * 4000,
            "tool": "Read",
            "args": {"path": f"f{sid}"},
            "result": "r" * 1000,
        }
        for sid in range(12)
    ]
    vround = _layout(tmp_path, body=_traj_body(steps))
    _write_u(vround, _chain_u("R1-V0-active-t1", list(range(12))))

    provider = _CapturingProvider()
    digester = _digester(vround, graph_input=True, provider=provider)
    await digester._interpret_failed_task(_ctx(vround), _digest())

    prov = digester.input_provenance[0]
    prompt = provider.prompts[0]
    assert prov.source == "graph"
    assert prov.truncated is True
    assert prov.chars <= rvp._LLM_DIGESTER_GRAPH_INPUT_CAP
    # The drop is VISIBLE in the prompt, the way the structure marker is.
    assert "content selection: dropped" in prompt
    # Recency within causal: the terminal step (ordinal 11) is kept, the earliest dropped.
    assert "UNIQUE_STEP_11_MARKER" in prompt
    assert "UNIQUE_STEP_0_MARKER" not in prompt


def _cone_of(vround: Path):
    """The induced cone for the single U under ``vround/sessions`` (test helper)."""
    u_file = next((vround / "sessions").rglob("*_unfolded.jsonl"))
    graph = rvp.load_unfolded(u_file)
    anchor = rvp.terminal_node(graph)
    cone_ids = rvp.causal_cone(graph, [anchor], edge_types=None, include_anchors=True)
    return rvp.induced_subgraph(graph, cone_ids)
