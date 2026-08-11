# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""End-to-end smoke for the GHX v6 graph runtime — the whole chain in one process.

This is a script, not a pytest case: it drives ONE real ``Harness.run`` with the
three GHX v6 flags on (``HARNESSX_GHX_RUNTIME`` / ``HARNESSX_GHX_UNFOLD`` /
``HARNESSX_GHX_IDENTITY``), then reads the persisted artifacts back and prints the
real numbers.  It proves the modules landed tonight are *wired together*, not that
any one of them is individually correct (their unit/integration tests do that).

The parts it lights up, in the order they fire in one run:

  M2b  dispatch resolved from the graph (executor built from the run's own binding);
  M3   slot reads/writes attributed to the acting processor node id;
  M4   the unfolded graph U recorded per invocation, written as JSONL;
  M5   tool invocations in U + an INVOKES edge from a spawn_subagent tool node;
  M6a  causal_cone over the U this run produced;
  M9   the three identity hashes, phenotype present when unfold is on.

It is offline-only: no network, no API keys — ``MockProvider`` scripts every model turn
(shared across parent and child, so a real sub-agent spawn is exercised too).

Run:  .venv312\\Scripts\\python.exe experiments\\smoke_ghx_v6_chain.py
"""

from __future__ import annotations

import asyncio
import os
import shutil
import sys
import tempfile
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
# tests/ carries the shared MockProvider + tool fixtures; tests/integration/ carries
# the Kahn cycle detector we reuse rather than write a second one.
sys.path.insert(0, str(_REPO_ROOT / "tests"))
sys.path.insert(0, str(_REPO_ROOT / "tests" / "integration"))

from fixtures.mock_provider import MockProvider  # noqa: E402
from fixtures.mock_tools import add_tool, echo_tool, make_registry  # noqa: E402
from test_unfold import _find_cycle_node  # noqa: E402  reuse the existing Kahn detector

from harnessx import (  # noqa: E402
    BaseTask,
    HarnessConfig,
    ModelConfig,
    MultiHookProcessor,
)
from harnessx.graph.causal import causal_cone, invokes_frontier, terminal_node  # noqa: E402
from harnessx.graph.executor import build_graph_executor  # noqa: E402
from harnessx.graph.identity_record import identity_path, load_identity  # noqa: E402
from harnessx.graph.unfold import load_unfolded, unfolded_path  # noqa: E402
from harnessx.tools.spawn_subagent import spawn_subagent_tool  # noqa: E402
from harnessx.tracing.journal import HarnessJournal  # noqa: E402

_FLAGS = ("HARNESSX_GHX_RUNTIME", "HARNESSX_GHX_UNFOLD", "HARNESSX_GHX_IDENTITY")


# ── instrumentation ──────────────────────────────────────────────────────────


class StarPassthrough(MultiHookProcessor):
    """Fires on every hook as a pass-through — the bulk of U's invocation nodes, so
    the id-uniqueness property is genuinely exercised (a node recurs within a step)."""


class WriterTS(MultiHookProcessor):
    """Writes a slot within the task_start firing — a real-node-id provenance write."""

    def __init__(self, key: str = "shared") -> None:
        super().__init__()
        self._key = key

    async def on_task_start(self, event):
        event.state.set_slot(self._key, "smoke", 42)
        yield event


class ReaderTS(MultiHookProcessor):
    """Reads that slot within the same task_start firing — the OBSERVED_DATA edge target."""

    def __init__(self, key: str = "shared") -> None:
        super().__init__()
        self._key = key

    async def on_task_start(self, event):
        event.state.get_slot(self._key)
        yield event


def _tool_turn(tag: str, name: str, inp: dict) -> dict:
    return {"content": tag, "tool_calls": [{"id": tag, "name": name, "input": inp}]}


# The MockProvider queue is shared across parent AND child (no model override on
# spawn), so these five turns script both layers in strict call order:
#   parent step0 -> add          (a real, non-spawn tool call)
#   parent step1 -> spawn        (wait=True: runs the child synchronously)
#     child step0 -> echo
#     child step1 -> "child done"
#   parent step2 -> "parent done"
_ON_RESPONSES = [
    _tool_turn("s0", "add", {"a": 2, "b": 2}),
    _tool_turn("s1", "spawn_subagent", {"task": "say hi", "wait": True}),
    _tool_turn("c0", "echo", {"message": "hi-from-child"}),
    "child done",
    "parent done",
]


def _build(base_dir: str, session_id: str):
    journal = HarnessJournal(base_dir=base_dir, export_jsonl=True, session_id=session_id, silent=True)
    registry = make_registry(add_tool, echo_tool)
    registry.register(spawn_subagent_tool)
    config = HarnessConfig(
        tool_registry=registry,
        tracer=journal,
        processors=[WriterTS(), ReaderTS(), StarPassthrough()],
    )
    config.init_workspace = False  # no on-disk workspace for an offline smoke
    harness = ModelConfig(main=MockProvider(responses=list(_ON_RESPONSES))).agentic(config)
    return config, harness


def _set_flags(on: bool) -> None:
    for name in _FLAGS:
        if on:
            os.environ[name] = "1"
        else:
            os.environ.pop(name, None)


def _hn(h: str) -> str:
    # ASCII-only so the Windows console never mojibakes the deliverable output.
    return (h[:12] + "...") if h and len(h) > 13 else (h or "(none)")


# ── the on-path: all three flags on ──────────────────────────────────────────


async def run_on(base_dir: str) -> dict:
    session_id = "smoke_on"
    _set_flags(True)
    config, harness = _build(base_dir, session_id)

    # M2b — dispatch resolved FROM THE GRAPH.  Build the executor from the very
    # binding the runloop consumes (HARNESSX_GHX_RUNTIME on ⇒ the run routes through
    # this same path) and read back the order the graph resolves for a hook.
    binding = harness._rt.proc_node_binding
    executor = build_graph_executor(harness.config, binding)
    resolved_step_end = executor.procs_for("step_end")
    star_resolved = any(isinstance(p, StarPassthrough) for p in resolved_step_end)

    result = await harness.run(BaseTask(description="smoke", max_steps=10))
    assert result.exit_reason == "done", f"on-run did not finish cleanly: {result.exit_reason}"

    # M4 — U exists, is loadable, ids are unique.
    u_path = unfolded_path(base_dir, session_id, result.run_id)
    assert u_path.exists(), f"U not written at {u_path}"
    u = load_unfolded(u_path)
    node_ids = [n.id for n in u.nodes]
    unique_ids = len(node_ids) == len(set(node_ids))
    assert unique_ids, f"{len(node_ids) - len(set(node_ids))} invocations collapsed onto a shared id"

    # M4 — U is acyclic (reuse the shipped Kahn detector).
    cycle = _find_cycle_node(u)
    assert cycle is None, f"U contains a cycle at {cycle}"

    # M5 — tool nodes present + INVOKES names the child run id.
    tool_nodes = [n for n in u.nodes if n.hook == "tool"]
    tool_bases = sorted({n.static_node_id for n in tool_nodes})
    assert "tool:add" in tool_bases, f"add tool node missing; have {tool_bases}"
    assert "tool:spawn_subagent" in tool_bases, f"spawn tool node missing; have {tool_bases}"
    assert len(u.invokes) >= 1, "no INVOKES edge — the sub-agent spawn did not connect to U"
    node_by_id = {n.id: n for n in u.nodes}
    iv = u.invokes[0]
    src = node_by_id.get(iv.source)
    assert src is not None and src.static_node_id == "tool:spawn_subagent", (
        f"INVOKES source is not the spawn tool node: {iv.source}"
    )
    assert iv.child_run_id, "INVOKES edge names no child run id"
    assert iv.child_run_id != result.run_id, "INVOKES child id equals the parent run id"
    assert iv.child_run_id not in u.node_ids(), "INVOKES child id is (wrongly) a node in this U"
    child_files = list(Path(base_dir).rglob(f"{iv.child_run_id}_unfolded.jsonl"))
    child_u_found = bool(child_files) and load_unfolded(child_files[0]).run_id == iv.child_run_id

    # M3 — a slot write attributed to a REAL processor node id (not UNGRAPHED).
    prov = result.resume_state.slot_provenance
    real_writes = [
        (key, w) for key, p in prov.items() for w in p.writers if isinstance(w.actor, str) and w.actor != "UNGRAPHED"
    ]
    assert real_writes, "no slot write attributed to a real processor node id"
    ex_key, ex_write = real_writes[0]

    # M6a — causal cone from the terminal invocation is strictly smaller than all of U.
    term = terminal_node(u)
    assert term is not None, "empty U has no terminal invocation"
    cone = causal_cone(u, term)
    cone_size, total = len(cone), len(u.nodes)
    assert 0 < cone_size < total, f"causal cone not strictly smaller than U ({cone_size} vs {total})"
    frontier = invokes_frontier(u, cone)

    # M9 — identity record exists; genotype + deployment present; phenotype PRESENT.
    ipath = identity_path(base_dir, session_id, result.run_id)
    assert ipath.exists(), f"identity not written at {ipath}"
    ident = load_identity(ipath)
    assert ident.genotype, "genotype hash missing"
    assert ident.deployment, "deployment hash missing"
    assert ident.phenotype is not None, "phenotype absent while unfold is on"

    return {
        "run_id": result.run_id,
        "exit_reason": result.exit_reason,
        "m2b_binding_size": len(binding),
        "m2b_step_end_resolved": [type(p).__name__ for p in resolved_step_end],
        "m2b_star_resolved": star_resolved,
        "u_path": str(u_path),
        "u_nodes": total,
        "u_edges": len(u.edges),
        "u_unique_ids": unique_ids,
        "u_acyclic": cycle is None,
        "tool_nodes": len(tool_nodes),
        "tool_bases": tool_bases,
        "invokes": len(u.invokes),
        "invokes_child_run_id": iv.child_run_id,
        "child_u_found": child_u_found,
        "prov_slot": ex_key,
        "prov_actor": ex_write.actor,
        "prov_step": ex_write.step,
        "cone_size": cone_size,
        "cone_frontier": len(frontier),
        "genotype": ident.genotype,
        "deployment": ident.deployment,
        "phenotype": ident.phenotype,
        "phenotype_edges": ident.projected_edge_count,
    }


# ── the off-path: same task, all flags off, nothing recorded ─────────────────


async def run_off(base_dir: str) -> dict:
    session_id = "smoke_off"
    _set_flags(False)
    _config, harness = _build(base_dir, session_id)
    result = await harness.run(BaseTask(description="smoke", max_steps=10))

    # Dispatch still works with the flags off...
    assert result.exit_reason == "done", f"off-run did not finish cleanly: {result.exit_reason}"
    u_exists = unfolded_path(base_dir, session_id, result.run_id).exists()
    i_exists = identity_path(base_dir, session_id, result.run_id).exists()
    # ...and NOTHING was recorded.
    assert not u_exists, "U file was written with the flags off"
    assert not i_exists, "identity file was written with the flags off"

    return {
        "run_id": result.run_id,
        "exit_reason": result.exit_reason,
        "u_recorded": u_exists,
        "identity_recorded": i_exists,
    }


# ── reporting ────────────────────────────────────────────────────────────────


def _print_on(on: dict) -> None:
    print("\n=== ON-PATH (HARNESSX_GHX_RUNTIME + _UNFOLD + _IDENTITY = 1) ===")
    print(f"  run_id                : {on['run_id']}  exit={on['exit_reason']}")
    print("  M2b dispatch from graph:")
    print(f"    binding entries     : {on['m2b_binding_size']}")
    print(f"    step_end resolved   : {on['m2b_step_end_resolved']}")
    print(f"    StarPassthrough seen: {on['m2b_star_resolved']}")
    print("  M4 unfolded graph U:")
    print(f"    path                : {on['u_path']}")
    print(f"    nodes / edges       : {on['u_nodes']} / {on['u_edges']}")
    print(f"    ids unique          : {on['u_unique_ids']}")
    print(f"    acyclic (Kahn)      : {on['u_acyclic']}")
    print("  M5 tools + inter-layer INVOKES:")
    print(f"    tool nodes          : {on['tool_nodes']}  bases={on['tool_bases']}")
    print(f"    INVOKES edges       : {on['invokes']}")
    print(f"    child run id        : {on['invokes_child_run_id']}")
    print(f"    child U on disk     : {on['child_u_found']}")
    print("  M3 slot provenance:")
    print(f"    slot '{on['prov_slot']}' written by node id : {on['prov_actor']!r} @ step {on['prov_step']}")
    print("  M6a causal cone (terminal invocation):")
    print(f"    cone size / total U : {on['cone_size']} / {on['u_nodes']}  (frontier={on['cone_frontier']})")
    print("  M9 run identity:")
    print(f"    genotype            : {_hn(on['genotype'])}")
    print(f"    deployment          : {_hn(on['deployment'])}")
    print(f"    phenotype           : {_hn(on['phenotype'])}  (present, {on['phenotype_edges']} projected edges)")


def _print_off(off: dict) -> None:
    print("\n=== OFF-PATH (all three flags unset) ===")
    print(f"  run_id                : {off['run_id']}  exit={off['exit_reason']}")
    print(f"  U recorded            : {off['u_recorded']}  (expected False)")
    print(f"  identity recorded     : {off['identity_recorded']}  (expected False)")


def _print_summary(on: dict, off: dict) -> None:
    rows = [
        ("module", "signal", "on", "off"),
        ("M2b", "dispatch source", "graph executor", "legacy concat"),
        ("M4", "U file", "written", "absent"),
        ("M4", "U nodes (unique)", f"{on['u_nodes']} ({on['u_unique_ids']})", "-"),
        ("M4", "U acyclic", str(on["u_acyclic"]), "-"),
        ("M5", "tool nodes", str(on["tool_nodes"]), "-"),
        ("M5", "INVOKES edges", str(on["invokes"]), "-"),
        ("M3", "real-node slot write", f"{on['prov_actor']}", "-"),
        ("M6a", "cone / total", f"{on['cone_size']} / {on['u_nodes']}", "-"),
        ("M9", "phenotype", _hn(on["phenotype"]), "absent"),
        ("--", "run exit", on["exit_reason"], off["exit_reason"]),
    ]
    widths = [max(len(str(r[i])) for r in rows) for i in range(4)]
    print("\n=== SUMMARY ===")
    for ri, r in enumerate(rows):
        line = "  " + " | ".join(str(r[i]).ljust(widths[i]) for i in range(4))
        print(line)
        if ri == 0:
            print("  " + "-+-".join("-" * widths[i] for i in range(4)))


async def _amain() -> int:
    base_dir = tempfile.mkdtemp(prefix="ghx_v6_smoke_")
    saved = {name: os.environ.get(name) for name in _FLAGS}
    try:
        on = await run_on(base_dir)
        off = await run_off(base_dir)
        _print_on(on)
        _print_off(off)
        _print_summary(on, off)
        print("\nPASS - the GHX v6 chain holds together end to end.")
        return 0
    finally:
        for name, val in saved.items():
            if val is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = val
        shutil.rmtree(base_dir, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_amain()))
