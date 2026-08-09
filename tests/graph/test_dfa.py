"""Δ8 block 21 — lifecycle DFA pass: six checks, witnesses, gate wiring.

Baseline: real exports produce zero witnesses.  The headline capability is
catching "structurally legal but protocol-violating" candidates that S0–S4
passes — cross-bucket ``_after_`` (silently unenforced by the per-bucket
router) and temporally impossible ordering intents.
"""

from harnessx.core.builder import HarnessBuilder
from harnessx.core.harness import HarnessConfig
from harnessx.graph.dfa import LIFECYCLE_TRANSITIONS, lifecycle_dfa_check
from harnessx.graph.snapshot import to_graph
from harnessx.graph.types import Edge, EdgeType
from harnessx.graph.validate import validate_snapshot

from tests.graph.fixtures import config_srsr, serialized_dict


def _checks(report):
    return {w.check for w in report.witnesses}


# ── automaton sanity ────────────────────────────────────────────────────────


def test_transition_table_covers_the_eight_states():
    from harnessx.core.processor import PROCESSOR_HOOK_NAMES

    assert set(LIFECYCLE_TRANSITIONS) == set(PROCESSOR_HOOK_NAMES)
    assert LIFECYCLE_TRANSITIONS["task_end"] == frozenset()  # accept state
    for targets in LIFECYCLE_TRANSITIONS.values():
        assert targets <= set(PROCESSOR_HOOK_NAMES)


# ── baseline: real exports are protocol-clean ───────────────────────────────


def test_context_bundle_zero_witnesses():
    from harnessx.bundles import context

    report = lifecycle_dfa_check(to_graph((HarnessBuilder() | context).build()))
    assert report.passed, report.reason()


def test_mixed_srsr_zero_witnesses():
    report = lifecycle_dfa_check(to_graph(config_srsr()))
    assert report.passed, report.reason()


def test_empty_config_zero_witnesses():
    report = lifecycle_dfa_check(to_graph(HarnessConfig(processors=[])))
    assert report.passed, report.reason()


# ── check 1: alphabet violation ─────────────────────────────────────────────


def test_alphabet_violation_in_coverage():
    snap = to_graph(HarnessConfig(processors=[
        {"_target_": "x.P", "_hooks_": ["task_start"]}]))
    snap.nodes["proc:p"].metadata["_hooks_"] = ["task_start", "model"]
    report = lifecycle_dfa_check(snap)
    assert "alphabet_violation" in _checks(report)


def test_alphabet_violation_in_attachment():
    snap = to_graph(HarnessConfig(processors=[
        {"_target_": "x.P", "_hooks_": ["task_start"]}]))
    snap.edges.append(Edge(source_id="proc:p", target_id="hook:tool",
                           edge_type=EdgeType.ATTACHED_TO, metadata={}))
    report = lifecycle_dfa_check(snap)
    assert "alphabet_violation" in _checks(report)


# ── check 2: dead processor ─────────────────────────────────────────────────


def test_dead_processor_witnessed():
    snap = to_graph(HarnessConfig(processors=[
        {"_target_": "x.P", "_hooks_": []}]))  # explicit-empty coverage
    report = lifecycle_dfa_check(snap)
    assert "dead_processor" in _checks(report)
    assert report.witnesses[0].node_ids == ["proc:p"]


def test_unknown_coverage_makes_no_dead_claim():
    # co-located / file-URI style target: no hook metadata anywhere — the
    # runtime natural fallback would fire it, so Δ8 must not call it dead
    # (false-positive class observed on the e_pervar3 replay corpus)
    snap = to_graph(HarnessConfig(processors=[
        {"_target_": "workspace/custom.py::LocalProcessor"}]))
    report = lifecycle_dfa_check(snap)
    assert "dead_processor" not in _checks(report)


# ── checks 3+4: after enforcement / temporal possibility ────────────────────


def _cross_bucket_after():
    # declarer registered at step_end bucket, target group at before_model
    # bucket (neither strict temporal case): both pass S0–S4 — each bucket
    # sorts fine — but the per-bucket router never enforces the ordering
    return to_graph(HarnessConfig(processors=[
        serialized_dict("x.Guard", hook="before_model", singleton_group="guard"),
        serialized_dict("x.Early", hook="step_end", singleton_group="early",
                        after=("guard",)),
    ]))


def test_cross_bucket_after_witnessed_and_s0s4_blind():
    snap = _cross_bucket_after()
    assert validate_snapshot(snap).passed        # S0–S4 cannot see it
    report = lifecycle_dfa_check(snap)
    assert "after_never_enforced" in _checks(report)
    w = next(x for x in report.witnesses if x.check == "after_never_enforced")
    assert set(w.node_ids) == {"proc:early", "proc:guard"}


def test_temporal_contradiction_task_start_declarer():
    snap = to_graph(HarnessConfig(processors=[
        serialized_dict("x.Late", hook="task_end", singleton_group="late"),
        serialized_dict("x.First", hook="task_start", singleton_group="first",
                        after=("late",)),
    ]))
    report = lifecycle_dfa_check(snap)
    assert "temporal_contradiction" in _checks(report)


def test_temporal_contradiction_task_end_target():
    snap = to_graph(HarnessConfig(processors=[
        serialized_dict("x.Last", hook="task_end", singleton_group="last"),
        serialized_dict("x.Mid", hook="before_model", singleton_group="mid",
                        after=("last",)),
    ]))
    report = lifecycle_dfa_check(snap)
    assert "temporal_contradiction" in _checks(report)


def test_same_bucket_after_is_clean():
    snap = to_graph(HarnessConfig(processors=[
        serialized_dict("x.A", hook="task_start", singleton_group="a"),
        serialized_dict("x.B", hook="task_start", singleton_group="b",
                        after=("a",)),
    ]))
    assert lifecycle_dfa_check(snap).passed


def test_unresolved_after_makes_no_dfa_claim():
    snap = to_graph(HarnessConfig(processors=[
        serialized_dict("x.A", hook="task_start", singleton_group="a",
                        after=("missing",)),
    ]))
    assert lifecycle_dfa_check(snap).passed  # soft dep: S2 warns, Δ8 silent


# ── check 5: skeleton integrity ─────────────────────────────────────────────


def test_missing_hook_node_witnessed():
    snap = to_graph(HarnessConfig(processors=[]))
    del snap.nodes["hook:task_start"]
    snap.edges = [e for e in snap.edges
                  if "hook:task_start" not in (e.source_id, e.target_id)]
    report = lifecycle_dfa_check(snap)
    assert "skeleton_broken" in _checks(report)


def test_missing_loop_back_witnessed():
    snap = to_graph(HarnessConfig(processors=[]))
    snap.edges = [e for e in snap.edges if e.edge_type is not EdgeType.LOOP_BACK]
    report = lifecycle_dfa_check(snap)
    assert "skeleton_broken" in _checks(report)


# ── check 6: derived-chain consistency ──────────────────────────────────────


def test_chain_bucket_mismatch_witnessed():
    snap = to_graph(HarnessConfig(processors=[
        serialized_dict("x.A", hook="task_start", order=0),
        serialized_dict("x.B", hook="task_start", order=50),
    ]))
    # corrupt the derived chain's bucket tag
    chain = next(e for e in snap.edges
                 if e.edge_type is EdgeType.EXECUTES_BEFORE)
    chain.metadata["hook"] = "task_end"
    report = lifecycle_dfa_check(snap)
    assert "chain_state_mismatch" in _checks(report)


# ── gate + shadow-round wiring ──────────────────────────────────────────────


def test_gate_rejects_cross_bucket_after(tmp_path):
    from experiments.variant_pool.graph_gate import validate_candidate_graph

    # two importable processors, cross-bucket after (neither strict temporal
    # case): CostGuard registered at before_model, Checkpoint at step_end
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "processors:\n"
        "  - _target_: harnessx.processors.control.cost_guard.CostGuardProcessor\n"
        "    _hook_: before_model\n"
        "    _singleton_group_: guard\n"
        "  - _target_: harnessx.processors.observability.checkpoint.CheckpointProcessor\n"
        "    _hook_: step_end\n"
        "    _singleton_group_: early\n"
        "    _after_: [guard]\n",
        encoding="utf-8",
    )
    report = validate_candidate_graph(cfg)
    assert not report.passed
    assert any(e.error_type == "dfa_after_never_enforced" for e in report.errors)


def test_shadow_round_rejects_dfa_violation(tmp_path):
    from experiments.variant_pool.shadow_evolution import ShadowLedger, run_shadow_round

    parent = to_graph(HarnessConfig(processors=[
        serialized_dict("tests.graph.fixtures.RuntimeProbe",
                        hook="task_start", singleton_group="probe"),
        serialized_dict("tests.graph.fixtures.OrderedProbe",
                        hook="task_end", singleton_group="late"),
    ]))
    # rewire probe (task_start bucket) to run after `late` (task_end bucket):
    # S0–S4 passes, Δ8 must reject
    result = run_shadow_round(parent, [
        {"operator": "rewire_ordering",
         "params": {"node_id": "proc:runtime_probe", "after": ["late"]}},
    ], ShadowLedger(tmp_path / "s.jsonl"), round_id="r1", materialize=False)
    rec = result.records[0]
    assert rec.decision == "REJECT"
    assert rec.validation_issues[0]["layer"] == "Δ8"
    assert rec.validation_issues[0]["error_type"] == "temporal_contradiction"
