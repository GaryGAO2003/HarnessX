# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""Offline unit tests for ``variant_pool.engine`` (C1).

Everything here runs with deterministic injected stubs — no LLM, no rollouts, no
repo imports (SPEC §8.3/§8.4). The two load-bearing tests come first: that the
engine's routing is frozen from prior-round evidence only (the M1 correctness
core, SPEC §6.2) and that a one-variant pool reproduces a single lineage.

Stubs
-----
``Scripted`` drives ``evolve`` and ``evaluate`` from a table keyed by
``(variant_id, round_idx)``: ``evolve`` returns a candidate when the table has an
entry (else ``None``), and ``evaluate`` returns that entry narrowed to ``T_k``.
Candidates are plain objects, not :class:`ChangeManifest`, so the gate's manifest
stages are skipped and the decision is the pure seesaw — which is exactly the
orchestration under test (the manifest gate is covered by ``test_gate.py``).
"""

from __future__ import annotations

import pytest

from variant_pool.engine import DEFAULT_PATIENCE, RoundResult, VariantPoolEngine
from variant_pool.gate import Decision
from variant_pool.ledger import SuccessLedger
from variant_pool.pool import VariantPool
from variant_pool.router import Router, RoutingFreezeError


# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------


class Cand:
    """A candidate the gate treats opaquely (not a ChangeManifest)."""

    def __init__(self, candidate_id: str, target_variant: str) -> None:
        self.candidate_id = candidate_id
        self.target_variant = target_variant


class Scripted:
    """Deterministic ``evolve`` / ``evaluate`` driven by a scripted table.

    ``table[(variant_id, round_idx)]`` holds ``{task: (n_pass, n_att)}`` for that
    variant's candidate in that round. A missing entry means "no candidate".
    """

    def __init__(self, table: dict[tuple[str, int], dict[str, tuple[int, int]]]) -> None:
        self.table = table
        self.evolve_calls: list[tuple[str, int]] = []
        self.evaluate_calls: list[tuple[str, frozenset[str], int]] = []

    def evolve(self, variant, round_idx):
        self.evolve_calls.append((variant.variant_id, round_idx))
        if (variant.variant_id, round_idx) not in self.table:
            return None
        return Cand(f"C-R{round_idx}-{variant.variant_id}", variant.variant_id)

    def evaluate(self, candidate, t_k, round_idx):
        self.evaluate_calls.append((candidate.target_variant, frozenset(t_k), round_idx))
        full = self.table[(candidate.target_variant, round_idx)]
        # narrow to T_k so a leak (touching another variant's tasks) is visible
        return {task: full[task] for task in t_k}


class SpyPool(VariantPool):
    """A pool that records every fork / retire / reassign it is asked to do."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.fork_calls: list[tuple[str, set[str], int]] = []
        self.retire_calls: list[str] = []
        self.reassign_calls: list[set[str]] = []

    def fork(self, parent_id, improved_tasks, at_round):
        improved = set(improved_tasks)
        self.fork_calls.append((parent_id, improved, at_round))
        return super().fork(parent_id, improved, at_round)

    def retire(self, variant_id):
        self.retire_calls.append(variant_id)
        return super().retire(variant_id)

    def reassign(self, orphan_tasks, router, ledger=None, *, before_round=None):
        orphans = set(orphan_tasks)
        self.reassign_calls.append(orphans)
        return super().reassign(orphans, router, ledger, before_round=before_round)


class SpyRouter(Router):
    """Captures each freeze: the round, the ledger age it saw, and the map."""

    def __init__(self, *args, events=None, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.events = events if events is not None else []
        self.freeze_log: list[tuple[int, int]] = []
        self.frozen_maps: dict[int, dict[str, str]] = {}

    def freeze_routing(self, tasks, pool, ledger, round_idx):
        self.events.append(("freeze", round_idx))
        self.freeze_log.append((round_idx, ledger.max_last_round()))
        frozen = super().freeze_routing(tasks, pool, ledger, round_idx)
        self.frozen_maps[round_idx] = dict(frozen)
        return frozen


class SpyLedger(SuccessLedger):
    """Logs the round of every ``record`` so ordering vs freeze is checkable."""

    def __init__(self, *args, events=None, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.events = events if events is not None else []

    def record(self, variant_id, task_id, n_pass, n_att, round_idx):
        self.events.append(("record", round_idx))
        return super().record(variant_id, task_id, n_pass, n_att, round_idx)


def _first_pos(events, item):
    return next(i for i, event in enumerate(events) if event == item)


def _pass_fraction(outcomes: dict[str, tuple[int, int]]) -> float:
    solved = sum(1 for n_pass, _ in outcomes.values() if n_pass >= 1)
    return solved / len(outcomes)


# ===========================================================================
# 1. Routing freeze — the correctness core (SPEC §6.2). M1's whole point.
# ===========================================================================


def test_routing_is_frozen_from_prior_rounds_only() -> None:
    """Round 1's routing must follow round-0 evidence, never round-1 results.

    V0 fails ``y`` in round 0, so in round 1 the argmax sends ``y`` to the
    untried V1 (0.5 beats V0's smoothed 0.25) and keeps ``x`` on V0. The engine
    must compute that map from the round-0 ledger alone — captured here as the
    ledger age (``max_last_round``) the freeze saw, which must be 0, i.e. no
    round-1 cell has leaked in. If it had peeked at the round it was routing,
    "send the task to whoever solves it" would be a disguised oracle.
    """
    events: list = []
    ledger = SpyLedger(events=events)
    router = SpyRouter(events=events)
    pool = SpyPool(K=8)
    pool.add_root("cfg/V0.yaml", "j/V0.md", tasks={"x", "y"})
    pool.fork("V0", set(), 0)  # bring V1 into being (empty) so y has somewhere to flip
    pool.fork_calls.clear()

    scripted = Scripted(
        {
            ("V0", 0): {"x": (2, 2), "y": (0, 2)},  # x solved, y fails
            ("V0", 1): {"x": (2, 2)},               # x stays solved (routed to V0)
            ("V1", 1): {"y": (2, 2)},               # V1 unlocks y (routed to V1)
        }
    )
    engine = VariantPoolEngine(pool, ledger, router, evaluate=scripted.evaluate, evolve=scripted.evolve)

    engine.run(tasks={"x", "y"}, num_rounds=2)

    # the freeze for round 1 saw only round-0 data (ledger age 0)
    assert (1, 0) in router.freeze_log
    # routing evolved from prior evidence: y moved off the variant that failed it
    assert router.frozen_maps[1] == {"x": "V0", "y": "V1"}
    # and that frozen map is what actually narrowed the round-1 evaluation
    assert scripted.evaluate_calls.count(("V0", frozenset({"x"}), 1)) == 1
    assert scripted.evaluate_calls.count(("V1", frozenset({"y"}), 1)) == 1


def test_freeze_precedes_every_record_of_the_same_round() -> None:
    """Ledger writes land only after that round's routing is frozen (SPEC §8.4).

    The account-timing guarantee: within round *r* the freeze event must come
    before any ``record`` for *r*, so routing can never see its own round.
    """
    events: list = []
    ledger = SpyLedger(events=events)
    router = SpyRouter(events=events)
    pool = SpyPool(K=8)
    pool.add_root("cfg/V0.yaml", "j/V0.md", tasks={"a", "b"})

    scripted = Scripted(
        {
            ("V0", 0): {"a": (2, 2), "b": (0, 2)},
            ("V0", 1): {"a": (2, 2), "b": (2, 2)},
        }
    )
    engine = VariantPoolEngine(pool, ledger, router, evaluate=scripted.evaluate, evolve=scripted.evolve)
    engine.run(tasks={"a", "b"}, num_rounds=2)

    for round_idx in (0, 1):
        freeze_at = _first_pos(events, ("freeze", round_idx))
        # no record of this round appears before its freeze
        assert all(event != ("record", round_idx) for event in events[:freeze_at])
        # and at least one record of this round appears after it
        assert ("record", round_idx) in events[freeze_at:]


def test_routing_refuses_to_run_on_its_own_rounds_results() -> None:
    """If this round's cell is already in the ledger, freezing raises (SPEC §6.2)."""
    ledger = SuccessLedger()
    router = Router()
    pool = VariantPool(K=8)
    pool.add_root("cfg/V0.yaml", "j/V0.md", tasks={"a"})
    ledger.record("V0", "a", 2, 2, 1)  # round-1 result present before round 1 runs

    scripted = Scripted({("V0", 1): {"a": (2, 2)}})
    engine = VariantPoolEngine(pool, ledger, router, evaluate=scripted.evaluate, evolve=scripted.evolve)

    with pytest.raises(RoutingFreezeError):
        engine.run_round(1, {"a"})


# ===========================================================================
# 2. K = 1 regression — a one-variant pool is a single lineage (SPEC §8.1)
# ===========================================================================


def test_k1_pool_reproduces_a_single_lineage_and_never_forks() -> None:
    """With only V0 the per-round pass rate equals a hand-computed single lineage.

    The schedule solves one more task each round, then plateaus: the decisions
    are APPLY, APPLY, APPLY, REJECT, and ``pool.fork`` is never called because a
    K=1 pool has no second variant to branch against.
    """
    schedule = {
        ("V0", 0): {"a": (2, 2), "b": (0, 2), "c": (0, 2)},
        ("V0", 1): {"a": (2, 2), "b": (2, 2), "c": (0, 2)},
        ("V0", 2): {"a": (2, 2), "b": (2, 2), "c": (2, 2)},
        ("V0", 3): {"a": (2, 2), "b": (2, 2), "c": (2, 2)},  # all solved -> nothing new
    }
    ledger = SuccessLedger()
    router = Router()
    pool = SpyPool(K=1)
    pool.add_root("cfg/V0.yaml", "j/V0.md", tasks={"a", "b", "c"})

    scripted = Scripted(schedule)
    engine = VariantPoolEngine(pool, ledger, router, evaluate=scripted.evaluate, evolve=scripted.evolve)

    results = engine.run(tasks={"a", "b", "c"}, num_rounds=4)

    assert [r.decisions["V0"] for r in results] == [
        Decision.APPLY,
        Decision.APPLY,
        Decision.APPLY,
        Decision.REJECT,
    ]
    # routing was always the whole set to V0 (single lineage), so the per-round
    # measurement equals the schedule, and its pass rate matches the reference.
    expected_rates = [1 / 3, 2 / 3, 3 / 3, 3 / 3]
    for round_idx, result in enumerate(results):
        assert result.per_variant_pass["V0"] == schedule[("V0", round_idx)]
        assert _pass_fraction(result.per_variant_pass["V0"]) == pytest.approx(expected_rates[round_idx])
        assert result.variant_count == 1

    assert pool.fork_calls == []  # a single lineage never forks


def test_k1_downgrades_an_impossible_fork_to_reject() -> None:
    """Even a seesaw conflict cannot fork a full one-variant pool (SPEC §8.1).

    A conflict that would FORK at K>=2 is downgraded to REJECT here — there is no
    variant other than V0 to retire — and ``pool.fork`` stays untouched.
    """
    ledger = SuccessLedger()
    router = Router()
    pool = SpyPool(K=1)
    pool.add_root("cfg/V0.yaml", "j/V0.md", tasks={"a", "b", "c", "d"})

    scripted = Scripted(
        {
            ("V0", 0): {"a": (2, 2), "b": (2, 2), "c": (0, 2), "d": (0, 2)},  # solve a, b
            ("V0", 1): {"a": (0, 2), "b": (0, 2), "c": (2, 2), "d": (2, 2)},  # break a,b / fix c,d
        }
    )
    engine = VariantPoolEngine(pool, ledger, router, evaluate=scripted.evaluate, evolve=scripted.evolve)
    results = engine.run(tasks={"a", "b", "c", "d"}, num_rounds=2)

    assert results[0].decisions["V0"] is Decision.APPLY
    assert results[1].decisions["V0"] is Decision.REJECT  # would be FORK at K>=2
    assert results[1].shipped is False
    assert pool.fork_calls == []


# ===========================================================================
# 3. Fork — improve a batch while breaking another (SPEC §8.4 step 4)
# ===========================================================================


def test_a_seesaw_conflict_forks_a_new_variant() -> None:
    """Round 1 improves {c, d} while regressing the ever-solved {a, b} -> FORK.

    The pool gains a variant, and the improved tasks move onto it (§4.5: the new
    variant serves the tasks the edit unlocked).
    """
    ledger = SuccessLedger()
    router = Router()
    pool = SpyPool(K=8)
    pool.add_root("cfg/V0.yaml", "j/V0.md", tasks={"a", "b", "c", "d"})

    scripted = Scripted(
        {
            ("V0", 0): {"a": (2, 2), "b": (2, 2), "c": (0, 2), "d": (0, 2)},  # solve a, b
            ("V0", 1): {"a": (0, 2), "b": (0, 2), "c": (2, 2), "d": (2, 2)},  # unlock c,d / break a,b
        }
    )
    engine = VariantPoolEngine(pool, ledger, router, evaluate=scripted.evaluate, evolve=scripted.evolve)
    results = engine.run(tasks={"a", "b", "c", "d"}, num_rounds=2)

    assert results[0].decisions["V0"] is Decision.APPLY
    fork_round = results[1]
    assert fork_round.decisions["V0"] is Decision.FORK
    assert fork_round.shipped is True

    # one fork of V0 for exactly the improved tasks
    assert pool.fork_calls == [("V0", {"c", "d"}, 1)]
    assert fork_round.forked == ["V1"]
    assert len(pool) == 2
    # the improved tasks transferred onto the new variant, off the parent
    assert pool.variants["V1"].routed_tasks == {"c", "d"}
    assert pool.variants["V0"].routed_tasks == {"a", "b"}
    # and the new variant carries the candidate's results for those tasks
    assert ledger.cell("V1", "c").passes == 2
    assert ledger.cell("V1", "d").passes == 2


def test_fork_narrows_evaluation_to_the_forking_variants_tasks() -> None:
    """The candidate that forks was evaluated only on its own T_k (§4.5)."""
    ledger = SuccessLedger()
    router = Router()
    pool = SpyPool(K=8)
    pool.add_root("cfg/V0.yaml", "j/V0.md", tasks={"a", "b", "c", "d"})

    scripted = Scripted(
        {
            ("V0", 0): {"a": (2, 2), "b": (2, 2), "c": (0, 2), "d": (0, 2)},
            ("V0", 1): {"a": (0, 2), "b": (0, 2), "c": (2, 2), "d": (2, 2)},
        }
    )
    engine = VariantPoolEngine(pool, ledger, router, evaluate=scripted.evaluate, evolve=scripted.evolve)
    engine.run(tasks={"a", "b", "c", "d"}, num_rounds=2)

    # round 1 evaluated V0's candidate on all four of its tasks and nothing else
    assert ("V0", frozenset({"a", "b", "c", "d"}), 1) in scripted.evaluate_calls


# ===========================================================================
# 4. Retirement — a full pool retires its weakest before forking (SPEC §8.4)
# ===========================================================================


def test_a_full_pool_retires_and_reassigns_before_forking() -> None:
    """K=2 full: forking V0 retires the other variant, re-routes its orphans,
    then branches — and the pool never exceeds K.
    """
    ledger = SuccessLedger()
    router = Router()
    pool = SpyPool(K=2)
    pool.add_root("cfg/V0.yaml", "j/V0.md", tasks={"a", "b", "e", "f"})
    pool.fork("V0", {"c", "d"}, 0)  # create V1; pool now full at K=2
    pool.fork_calls.clear()

    # round-0 history: V0 solves a,b; V1 solves c,d; e,f untried by anyone
    ledger.record("V0", "a", 2, 2, 0)
    ledger.record("V0", "b", 2, 2, 0)
    ledger.record("V1", "c", 2, 2, 0)
    ledger.record("V1", "d", 2, 2, 0)

    scripted = Scripted(
        {
            # V0's candidate unlocks e,f (untried) while regressing a,b (ever-solved)
            ("V0", 1): {"a": (0, 2), "b": (0, 2), "e": (2, 2), "f": (2, 2)},
        }
    )
    engine = VariantPoolEngine(pool, ledger, router, evaluate=scripted.evaluate, evolve=scripted.evolve)
    result = engine.run_round(1, {"a", "b", "c", "d", "e", "f"})

    assert result.decisions["V0"] is Decision.FORK
    assert pool.retire_calls == ["V1"]            # the only other variant is retired
    assert pool.reassign_calls == [{"c", "d"}]    # its orphans are re-routed
    assert pool.fork_calls == [("V0", {"e", "f"}, 1)]
    assert result.retired == ["V1"]
    assert result.forked == ["V2"]

    assert "V1" not in pool.variants
    assert len(pool) <= pool.K == 2               # never exceeds capacity
    assert pool.variants["V2"].routed_tasks == {"e", "f"}
    # V1 was retired before its turn in the loop, so it was never evolved
    assert ("V1", 1) not in scripted.evolve_calls


def test_weakest_variant_is_the_one_retired() -> None:
    """``_weakest_other`` picks the lowest-rollup variant that is not the parent."""
    ledger = SuccessLedger()
    router = Router()
    pool = VariantPool(K=3)
    pool.add_root("cfg/V0.yaml", "j/V0.md", tasks=set())
    pool.fork("V0", set(), 0)  # V1
    pool.fork("V0", set(), 0)  # V2

    ledger.record("V1", "t", 2, 2, 0)  # V1 rollup = 1.0 (strong)
    ledger.record("V2", "t", 0, 2, 0)  # V2 rollup = 0.0 (weakest)

    scripted = Scripted({})
    engine = VariantPoolEngine(pool, ledger, router, evaluate=scripted.evaluate, evolve=scripted.evolve)

    assert engine._weakest_other("V0") == "V2"  # not V0 (parent), not V1 (stronger)


# ===========================================================================
# 5. Early stop — patience consecutive no-ship rounds halt the run (SPEC §8.4)
# ===========================================================================


def test_run_stops_after_patience_consecutive_non_ships() -> None:
    """Every round rejects, so idle climbs 1,2,3 and the run halts at patience."""
    ledger = SuccessLedger()
    router = Router()
    pool = VariantPool(K=8)
    pool.add_root("cfg/V0.yaml", "j/V0.md", tasks={"a"})

    # a is never solved -> improved is always empty -> REJECT every round
    scripted = Scripted({("V0", r): {"a": (0, 2)} for r in range(10)})
    engine = VariantPoolEngine(pool, ledger, router, evaluate=scripted.evaluate, evolve=scripted.evolve, patience=3)

    results = engine.run(tasks={"a"}, num_rounds=10)

    assert len(results) == 3                       # stopped early, not all 10
    assert [r.idle for r in results] == [1, 2, 3]
    assert all(r.shipped is False for r in results)
    assert all(r.decisions["V0"] is Decision.REJECT for r in results)
    assert engine.idle == 3


def test_a_ship_resets_the_idle_counter() -> None:
    """A shipping round zeroes idle, so the run keeps going (SPEC §6.6)."""
    ledger = SuccessLedger()
    router = Router()
    pool = VariantPool(K=8)
    pool.add_root("cfg/V0.yaml", "j/V0.md", tasks={"a", "b"})

    scripted = Scripted(
        {
            ("V0", 0): {"a": (0, 2), "b": (0, 2)},  # nothing improves -> REJECT, idle 1
            ("V0", 1): {"a": (2, 2), "b": (0, 2)},  # unlock a -> APPLY, idle resets to 0
            ("V0", 2): {"a": (2, 2), "b": (0, 2)},  # nothing new -> REJECT, idle 1
        }
    )
    engine = VariantPoolEngine(pool, ledger, router, evaluate=scripted.evaluate, evolve=scripted.evolve, patience=3)
    results = engine.run(tasks={"a", "b"}, num_rounds=3)

    assert [r.idle for r in results] == [1, 0, 1]
    assert [r.shipped for r in results] == [False, True, False]


def test_default_patience_matches_the_paper() -> None:
    assert DEFAULT_PATIENCE == 3


# ===========================================================================
# 6. Narrowed evaluation — evolve/evaluate only touch each variant's T_k
# ===========================================================================


def test_evolve_and_evaluate_stay_within_each_variants_tk() -> None:
    """Two variants with disjoint clusters; a third that carries nothing is skipped."""
    ledger = SuccessLedger()
    router = Router()
    pool = VariantPool(K=8)
    pool.add_root("cfg/V0.yaml", "j/V0.md", tasks={"a", "b", "c", "d"})
    pool.fork("V0", set(), 0)  # V1
    pool.fork("V0", set(), 0)  # V2 (will carry nothing -> skipped)

    # round-0 history routes a,b to V0 and c,d to V1; V2 is untried everywhere
    ledger.record("V0", "a", 2, 2, 0)
    ledger.record("V0", "b", 2, 2, 0)
    ledger.record("V1", "c", 2, 2, 0)
    ledger.record("V1", "d", 2, 2, 0)

    scripted = Scripted(
        {
            ("V0", 1): {"a": (2, 2), "b": (2, 2)},
            ("V1", 1): {"c": (2, 2), "d": (2, 2)},
        }
    )
    engine = VariantPoolEngine(pool, ledger, router, evaluate=scripted.evaluate, evolve=scripted.evolve)
    engine.run_round(1, {"a", "b", "c", "d"})

    # V2 has an empty cluster -> never evolved
    evolved = {vid for vid, _ in scripted.evolve_calls}
    assert evolved == {"V0", "V1"}
    # each evaluation saw exactly its own variant's cluster, nothing else
    assert ("V0", frozenset({"a", "b"}), 1) in scripted.evaluate_calls
    assert ("V1", frozenset({"c", "d"}), 1) in scripted.evaluate_calls
    for target, t_k, _ in scripted.evaluate_calls:
        if target == "V0":
            assert t_k == frozenset({"a", "b"})
        if target == "V1":
            assert t_k == frozenset({"c", "d"})


def test_a_variant_with_no_candidate_contributes_only_to_idle() -> None:
    """``evolve`` returning ``None`` ships nothing and records nothing (SPEC §8.4)."""
    ledger = SuccessLedger()
    router = Router()
    pool = VariantPool(K=8)
    pool.add_root("cfg/V0.yaml", "j/V0.md", tasks={"a"})

    scripted = Scripted({})  # no table entry -> evolve returns None
    engine = VariantPoolEngine(pool, ledger, router, evaluate=scripted.evaluate, evolve=scripted.evolve)
    result = engine.run_round(0, {"a"})

    assert scripted.evolve_calls == [("V0", 0)]
    assert scripted.evaluate_calls == []       # never evaluated
    assert result.decisions == {}
    assert result.shipped is False
    assert result.idle == 1
    assert ledger.cell("V0", "a") is None      # nothing recorded


# ===========================================================================
# 7. RoundResult shape and evidence side effects
# ===========================================================================


def test_round_result_reports_variant_count_and_curve_point() -> None:
    ledger = SuccessLedger()
    router = Router()
    pool = VariantPool(K=8)
    pool.add_root("cfg/V0.yaml", "j/V0.md", tasks={"a", "b"})

    scripted = Scripted({("V0", 0): {"a": (2, 2), "b": (1, 2)}})
    engine = VariantPoolEngine(pool, ledger, router, evaluate=scripted.evaluate, evolve=scripted.evolve)
    result = engine.run_round(0, {"a", "b"})

    assert isinstance(result, RoundResult)
    assert result.round_idx == 0
    assert result.variant_count == 1
    assert result.per_variant_pass == {"V0": {"a": (2, 2), "b": (1, 2)}}
    assert result.decisions == {"V0": Decision.APPLY}


def test_evidence_store_receives_digests_and_rejections(tmp_path) -> None:
    """When an evidence store is given, kept results are digested and rejects archived."""
    from variant_pool.evidence import EvidenceStore

    ledger = SuccessLedger()
    router = Router()
    pool = VariantPool(K=8)
    pool.add_root("cfg/V0.yaml", "j/V0.md", tasks={"a", "b"})
    evidence = EvidenceStore(tmp_path / "run")

    scripted = Scripted(
        {
            ("V0", 0): {"a": (2, 2), "b": (0, 2)},  # APPLY -> digests written
            ("V0", 1): {"a": (2, 2), "b": (0, 2)},  # nothing new -> REJECT -> archived
        }
    )
    engine = VariantPoolEngine(
        pool, ledger, router, evaluate=scripted.evaluate, evolve=scripted.evolve, evidence=evidence
    )
    engine.run(tasks={"a", "b"}, num_rounds=2)

    digests = list(evidence.iter_digests())
    assert {(d.task_id, d.round_idx) for d in digests} == {("a", 0), ("b", 0)}
    assert all(d.variant_id == "V0" for d in digests)

    rejected = evidence.rejected_candidates()
    assert len(rejected) == 1
    assert rejected[0]["round_idx"] == 1
    assert rejected[0]["variant_id"] == "V0"


# ===========================================================================
# 8. Constructor validation
# ===========================================================================


@pytest.mark.parametrize("patience", [0, -1])
def test_patience_must_be_positive(patience) -> None:
    pool = VariantPool(K=1)
    pool.add_root("cfg/V0.yaml", "j/V0.md", tasks={"a"})
    with pytest.raises(ValueError):
        VariantPoolEngine(
            pool, SuccessLedger(), Router(), evaluate=lambda *a: {}, evolve=lambda *a: None, patience=patience
        )


@pytest.mark.parametrize("min_fork", [(0, 2), (2, 0), (2,)])
def test_min_fork_must_be_a_positive_pair(min_fork) -> None:
    pool = VariantPool(K=1)
    pool.add_root("cfg/V0.yaml", "j/V0.md", tasks={"a"})
    with pytest.raises(ValueError):
        VariantPoolEngine(
            pool, SuccessLedger(), Router(), evaluate=lambda *a: {}, evolve=lambda *a: None, min_fork=min_fork
        )
