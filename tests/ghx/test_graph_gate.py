# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""G2 piece 3 — the sixth gate (graph existence on a candidate's replay U).

Test 4: a candidate whose signature fired in the replay U passes; an
edited-but-never-ran candidate is refused with evidence recorded; a candidate whose
replay U is unavailable is passed through with the reason recorded (and, critically,
``checked=False`` — the gate never claims to have checked when it did not).

Test 5: flag off → no gate, no surface files, the vendored round is delegated to
untouched (a spy proves it). A flag-on integration test exercises the actual seam:
the wrapper temporarily wraps ``run_stage_4`` so the shipped set is filtered before
the round commits, then restores it.
"""

from __future__ import annotations

from pathlib import Path

from harnessx.ghx.graph_gate import (
    apply_graph_gate_to_stage4,
    check_graph_gate,
    graph_gate_enabled,
    run_round_with_graph_gate,
)
from harnessx.graph.types import unfolded_id
from harnessx.graph.unfold import UnfoldedGraph, UnfoldedNode


def _tool_node(name: str, ordinal: int) -> UnfoldedNode:
    return UnfoldedNode(
        id=unfolded_id(f"tool:{name}", ordinal),
        static_node_id=f"tool:{name}",
        graphed=True,
        hook="tool",
        step=ordinal,
        ordinal=ordinal,
        label=name,
    )


def _u(*names: str) -> UnfoldedGraph:
    return UnfoldedGraph(
        run_id="r",
        session_id="s",
        nodes=[_tool_node(n, i) for i, n in enumerate(names)],
    )


_SIG = {"type": "tool_call", "tool_name": "SmartFetch", "expected_min_calls": 1}


def _manifest(tmp_path: Path, cid: str) -> Path:
    p = tmp_path / f"{cid}.md"
    p.write_text(
        "---\n"
        "bucket: tools\n"
        "attribution_signature:\n"
        "  type: tool_call\n"
        "  tool_name: SmartFetch\n"
        "  expected_min_calls: 1\n"
        "---\n\n"
        "A tool candidate.\n",
        encoding="utf-8",
    )
    return p


# ── test 4: pure gate decision ────────────────────────────────────────────────


def test_gate_passes_when_signature_fired():
    v = check_graph_gate(_SIG, _u("SmartFetch", "Bash"))
    assert v.ok is True
    assert v.checked is True
    assert v.count == 1


def test_gate_refuses_edited_but_never_ran():
    v = check_graph_gate(_SIG, _u("Bash", "Read"))  # SmartFetch never fired
    assert v.ok is False
    assert v.refused is True
    assert v.checked is True
    assert "edited but never ran" in v.reason


def test_gate_passes_through_when_u_unavailable():
    v = check_graph_gate(_SIG, None)
    assert v.ok is True
    # The load-bearing honesty: it did NOT check, and says so.
    assert v.checked is False
    assert "unavailable" in v.reason


def test_gate_passes_through_when_signature_not_graph_representable():
    v = check_graph_gate(None, _u("SmartFetch"))  # prompt/config bucket
    assert v.ok is True
    assert v.checked is False


# ── test 4 (ship level): apply to a Stage-4 result ────────────────────────────


async def test_apply_gate_drops_only_the_unfired_candidate(tmp_path: Path):
    candidates_info = {
        "c1": (_manifest(tmp_path, "c1"), tmp_path / "c1.yaml"),
        "c2": (_manifest(tmp_path, "c2"), tmp_path / "c2.yaml"),
    }

    def resolver(cid: str):
        return _u("SmartFetch") if cid == "c1" else _u("Bash")  # c2 never fires

    stage_4 = {
        "shipped_cid": "c1",
        "shipped_cids": ["c1", "c2"],
        "gate_results": {"c1": {}, "c2": {}},
        "reason": None,
        "candidate_signatures": {"c1": "sigA", "c2": "sigB"},
    }
    run_dir = tmp_path / "run"

    out = apply_graph_gate_to_stage4(
        stage_4,
        candidates_info=candidates_info,
        u_resolver=resolver,
        run_dir=run_dir,
        round_n=1,
    )

    assert out["shipped_cids"] == ["c1"]
    assert out["shipped_cid"] == "c1"
    assert out["gate_results"]["c2"]["graph_existence"].ok is False
    assert [r["cid"] for r in out["graph_gate_refusals"]] == ["c2"]
    # Evidence recorded for both the pass and the refusal.
    gate_dir = run_dir / "R1" / "graph_evidence" / "gate"
    assert "signature fired" in (gate_dir / "c1.md").read_text(encoding="utf-8").lower()
    assert "refused" in (gate_dir / "c2.md").read_text(encoding="utf-8").lower()


async def test_apply_gate_u_unavailable_keeps_candidate(tmp_path: Path):
    candidates_info = {"c1": (_manifest(tmp_path, "c1"), tmp_path / "c1.yaml")}
    stage_4 = {"shipped_cids": ["c1"], "shipped_cid": "c1", "gate_results": {"c1": {}}, "reason": None}
    run_dir = tmp_path / "run"

    out = apply_graph_gate_to_stage4(
        stage_4,
        candidates_info=candidates_info,
        u_resolver=lambda cid: None,  # U unavailable
        run_dir=run_dir,
        round_n=1,
    )

    # Unchanged result object (nothing refused) and pass-through evidence.
    assert out is stage_4
    assert out["shipped_cids"] == ["c1"]
    ev = (run_dir / "R1" / "graph_evidence" / "gate" / "c1.md").read_text(encoding="utf-8")
    assert "not checked" in ev.lower()


async def test_apply_gate_all_refused_sets_reason(tmp_path: Path):
    candidates_info = {"c1": (_manifest(tmp_path, "c1"), tmp_path / "c1.yaml")}
    stage_4 = {"shipped_cids": ["c1"], "shipped_cid": "c1", "gate_results": {"c1": {}}, "reason": None}

    out = apply_graph_gate_to_stage4(
        stage_4,
        candidates_info=candidates_info,
        u_resolver=lambda cid: _u("Bash"),  # never fires → refused
        run_dir=tmp_path / "run",
        round_n=1,
        write_evidence=False,
    )

    assert out["shipped_cids"] == []
    assert out["shipped_cid"] is None
    assert out["reason"] == "all_candidates_failed_graph_gate"


# ── test 5: flag gating + the delegate-called spy ─────────────────────────────


class _SpyOrch:
    def __init__(self, run_dir: Path, result: dict) -> None:
        self.run_dir = Path(run_dir)
        self._result = result
        self.calls: list[dict] = []

    async def run_round(self, **kwargs):
        self.calls.append(kwargs)
        return self._result


def _run_round_kwargs(tmp_path: Path) -> dict:
    return {
        "round_n": 1,
        "raw_sessions_dir": tmp_path / "sessions",
        "pass_flags_by_task": {},
        "current_config_path": tmp_path / "config.yaml",
    }


async def test_flag_off_delegates_untouched_and_writes_nothing(tmp_path: Path):
    spy = _SpyOrch(tmp_path / "run", {"shipped_cids": ["c1"]})
    res = await run_round_with_graph_gate(
        spy,
        u_resolver=lambda cid: _u("Bash"),  # would refuse if the gate ran
        parent_config_path=tmp_path / "config.yaml",
        gate_enabled=False,
        **_run_round_kwargs(tmp_path),
    )
    # Delegate called once, result passed through verbatim, our params not forwarded.
    assert res == {"shipped_cids": ["c1"]}
    assert len(spy.calls) == 1
    assert "u_resolver" not in spy.calls[0]
    assert "parent_config_path" not in spy.calls[0]
    # Nothing written.
    assert not (spy.run_dir / "R1" / "graph_evidence").exists()


def test_flag_default_off(monkeypatch):
    monkeypatch.delenv("HARNESSX_GHX_GRAPH_GATE", raising=False)
    assert graph_gate_enabled() is False
    monkeypatch.setenv("HARNESSX_GHX_GRAPH_GATE", "1")
    assert graph_gate_enabled() is True
    monkeypatch.setenv("HARNESSX_GHX_GRAPH_GATE", "off")
    assert graph_gate_enabled() is False


async def test_flag_on_seam_filters_before_commit(tmp_path: Path, monkeypatch):
    """The seam: with the flag on, the wrapper wraps ``run_stage_4`` so the round's
    commit bookkeeping sees the FILTERED shipped set, then restores the original."""
    import harnessx.aegis.orchestrator as orch_mod

    async def fake_stage_4(**kwargs):
        return {
            "shipped_cid": "c1",
            "shipped_cids": ["c1", "c2"],
            "gate_results": {"c1": {}, "c2": {}},
            "reason": None,
            "candidate_signatures": {},
        }

    monkeypatch.setattr(orch_mod, "run_stage_4", fake_stage_4)
    sentinel = orch_mod.run_stage_4

    candidates_info = {
        "c1": (_manifest(tmp_path, "c1"), tmp_path / "c1.yaml"),
        "c2": (_manifest(tmp_path, "c2"), tmp_path / "c2.yaml"),
    }
    s4_kwargs = {"round_n": 1, "candidates_info": candidates_info}

    class _RebindSpy:
        """Stands in for the vendored run_round: it calls the module-level
        ``run_stage_4`` (which the wrapper has wrapped) and returns its shipped set."""

        def __init__(self, run_dir: Path) -> None:
            self.run_dir = Path(run_dir)

        async def run_round(self, **kwargs):
            import harnessx.aegis.orchestrator as m

            stage_4 = await m.run_stage_4(**s4_kwargs)
            return {"shipped_cids": stage_4.get("shipped_cids"), "reason": stage_4.get("reason")}

    spy = _RebindSpy(tmp_path / "run")

    def resolver(cid: str):
        return _u("SmartFetch") if cid == "c1" else _u("Bash")  # c2 edited but never ran

    res = await run_round_with_graph_gate(
        spy,
        u_resolver=resolver,
        gate_enabled=True,
        **_run_round_kwargs(tmp_path),
    )

    # c2 was refused before the (simulated) commit read the shipped set.
    assert res["shipped_cids"] == ["c1"]
    # The module global is restored to exactly what it was before the call.
    assert orch_mod.run_stage_4 is sentinel
    # Gate evidence was written for the refusal.
    assert (spy.run_dir / "R1" / "graph_evidence" / "gate" / "c2.md").exists()
