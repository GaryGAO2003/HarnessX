"""R-arm random proposer — B-arm action space + same gate, zero intelligence.

Covers seed determinism (byte-identical sequences), the parse-contract shape,
uniform operator coverage (swap_bundle present iff a BUNDLE node exists), the
``max_candidates`` bound, and the kernel hand-off: every random proposal is
formally legal (``parse_proposal`` accepts it) and every gate decision is
GATED or REJECT.  The ``materialize=True`` case shows the verification
substrate genuinely filtering the R arm — random ctor mutations on a real
processor class die at the S4 build gate while structural edits survive.

Zero API: all proposals are pure ``random.Random`` draws; the only "model" is
chance.  Style mirrors ``tests/graph/test_proposer.py``.
"""

import json
import random
from collections import Counter

import pytest

from experiments.variant_pool.random_proposer import (
    INSERT_TARGET_POOL,
    _SAMPLERS,
    random_proposer,
)
from experiments.variant_pool.shadow_evolution import (
    PROPOSAL_OPERATORS,
    ShadowLedger,
    parse_proposal,
    run_shadow_round,
)
from harnessx.core.harness import HarnessConfig
from harnessx.graph.snapshot import to_graph
from harnessx.graph.types import Node, NodeType

from tests.graph.fixtures import serialized_dict

PROBE_TARGET = "tests.graph.fixtures.RuntimeProbe"
# A real, importable class so materialize=True can actually build the parent.
REAL_TARGET = "harnessx.processors.control.cost_guard.CostGuardProcessor"


def _parent():
    """Single probe node carrying a singleton group + int/str/float/bool ctor
    kwargs (exercises every ``_perturb_value`` branch and makes all five
    non-bundle operators samplable)."""
    d = serialized_dict(PROBE_TARGET, hook="task_start",
                        singleton_group="probe", order=10)
    d.update(threshold=5, label="probe", ratio=0.5, enabled=True)
    return to_graph(HarnessConfig(processors=[d]))


def _real_parent():
    """Parent built from a real class with NO ctor kwargs — it builds cleanly,
    so random junk-kwarg mutations are what die at the S4 build gate."""
    return to_graph(HarnessConfig(processors=[
        serialized_dict(REAL_TARGET, hook="before_model",
                        singleton_group="cost_guard", order=10),
    ]))


def _bundle_snapshot():
    """A `_parent()` snapshot with one BUNDLE node injected (to unlock
    swap_bundle in the sampling space)."""
    snap = _parent()
    snap.nodes["bundle:ctx"] = Node(
        node_id="bundle:ctx", node_type=NodeType.BUNDLE, label="ctx",
        metadata={"interface_signature": "sig-ctx", "child_graph_id": "g1"},
    )
    return snap


# ── determinism ──────────────────────────────────────────────────────────────


def test_seed_determinism_byte_identical():
    snap = _parent()
    for seed in (0, 7, 42, 123, 9999):
        a = random_proposer(snap, random.Random(seed)).proposals
        b = random_proposer(snap, random.Random(seed)).proposals
        assert a == b
        # byte-identical, not just structurally equal
        assert json.dumps(a) == json.dumps(b)


def test_distinct_seeds_diverge():
    # different seeds almost always yield different sequences (single-node
    # snapshots can collide on e.g. remove_processor, so assert on the aggregate
    # rather than any specific pair).
    snap = _parent()
    seqs = {json.dumps(random_proposer(snap, random.Random(s)).proposals)
            for s in range(50)}
    assert len(seqs) >= 30


# ── shape / parse contract ───────────────────────────────────────────────────


def test_result_shape_and_operator_names():
    snap = _parent()
    for seed in range(60):
        res = random_proposer(snap, random.Random(seed), max_candidates=4)
        assert res.error == ""
        assert isinstance(res.proposals, list)
        assert 1 <= len(res.proposals) <= 4
        for pr in res.proposals:
            assert set(pr) == {"operator", "params", "rationale"}
            assert pr["operator"] in PROPOSAL_OPERATORS
            assert isinstance(pr["params"], dict)
            assert isinstance(pr["rationale"], str) and pr["rationale"]


@pytest.mark.parametrize("max_candidates", [1, 2, 3, 4, 8])
def test_max_candidates_respected(max_candidates):
    snap = _parent()
    for s in range(100):
        n = len(random_proposer(
            snap, random.Random(s), max_candidates=max_candidates).proposals)
        assert 1 <= n <= max_candidates


def test_every_proposal_is_formally_legal():
    # the R arm's whole point: proposals are always *parseable* typed operators
    # (semantic legality is the gate's job). parse_proposal must never reject.
    for snap in (_parent(), _bundle_snapshot()):
        for s in range(300):
            for pr in random_proposer(snap, random.Random(s)).proposals:
                op, err = parse_proposal(pr)
                assert err == "" and op is not None


def test_samplers_cover_full_vocabulary():
    # drift guard: a new PROPOSAL_OPERATOR without an R-arm sampler must fail.
    assert set(_SAMPLERS) == set(PROPOSAL_OPERATORS)


def test_insert_targets_are_dotted_paths():
    assert len(INSERT_TARGET_POOL) >= 3
    for target in INSERT_TARGET_POOL:
        assert isinstance(target, str) and target.count(".") >= 3


# ── coverage (uniform over the available operator space) ─────────────────────


def test_operator_coverage_no_bundle_yields_five():
    snap = _parent()  # to_graph never emits BUNDLE nodes
    seen = set()
    for s in range(200):
        for pr in random_proposer(snap, random.Random(s)).proposals:
            seen.add(pr["operator"])
    assert seen == set(PROPOSAL_OPERATORS) - {"swap_bundle"}
    assert "swap_bundle" not in seen


def test_operator_coverage_with_bundle_yields_six():
    snap = _bundle_snapshot()
    seen = set()
    for s in range(200):
        for pr in random_proposer(snap, random.Random(s)).proposals:
            seen.add(pr["operator"])
    assert seen == set(PROPOSAL_OPERATORS)
    # the swap targets the injected bundle and mirrors its real signature
    swaps = [pr for s in range(200)
             for pr in random_proposer(snap, random.Random(s)).proposals
             if pr["operator"] == "swap_bundle"]
    assert swaps
    assert swaps[0]["params"]["bundle_node_id"] == "bundle:ctx"


# ── kernel hand-off (same gate as the B arm) ─────────────────────────────────


def test_gate_connection_materialize_false(tmp_path):
    snap = _parent()
    decisions = set()
    for s in range(20):
        props = random_proposer(snap, random.Random(s)).proposals
        ledger = ShadowLedger(tmp_path / f"s{s}.jsonl")
        rr = run_shadow_round(snap, props, ledger,
                              round_id=f"r{s}", materialize=False)
        assert len(rr.records) == len(props)          # nothing dropped / crashed
        for r in rr.records:
            assert r.decision in {"GATED", "REJECT"}
            decisions.add(r.decision)
    assert decisions and decisions <= {"GATED", "REJECT"}
    # Seed 0 is a stable all-GATED round: n=4 formally- and structurally-valid
    # edits on the probe node, and the static S0–S3+Δ8 gate accepts well-formed
    # edits (semantic death is a materialize/eval concern, not static).
    rr0 = run_shadow_round(
        snap, random_proposer(snap, random.Random(0)).proposals,
        ShadowLedger(tmp_path / "seed0.jsonl"), round_id="seed0",
        materialize=False)
    assert sum(r.decision == "GATED" for r in rr0.records) >= 1


def test_gate_rejects_random_ctor_mutation_materialize_true(tmp_path):
    # With a real, buildable parent, the verification substrate actually filters
    # the R arm: random junk ctor kwargs (CostGuardProcessor(rnd_param=…)) die at
    # the S4 build gate, while structural edits (insert/remove/rewire/replace)
    # survive — the intended "most-die-at-the-gate" regime.
    snap = _real_parent()
    decisions = Counter()
    reject_layers = set()
    for s in range(40):
        props = random_proposer(snap, random.Random(s)).proposals
        ledger = ShadowLedger(tmp_path / f"m{s}.jsonl")
        rr = run_shadow_round(snap, props, ledger,
                              round_id=f"m{s}", materialize=True)
        for r in rr.records:
            assert r.decision in {"GATED", "REJECT"}
            decisions[r.decision] += 1
            if r.decision == "REJECT":
                reject_layers.update(i.get("layer") for i in r.validation_issues)
    assert decisions["GATED"] > 0
    assert decisions["REJECT"] > 0
    assert "S4" in reject_layers
