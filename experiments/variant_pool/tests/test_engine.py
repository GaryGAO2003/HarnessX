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

from variant_pool.engine import DEFAULT_MIN_FORK, DEFAULT_PATIENCE, RoundResult, VariantPoolEngine
from variant_pool.gate import Decision, GateStage
from variant_pool.ledger import SuccessLedger
from variant_pool.manifest import CandidateArtifact, ChangeManifest
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


class QueueScripted:
    """A Critic-ranked queue with candidate-specific deterministic outcomes."""

    def __init__(
        self,
        candidates,
        outcomes: dict[str, dict[str, tuple[int, int]]],
    ) -> None:
        self.candidates = candidates
        self.outcomes = outcomes
        self.evolve_calls: list[tuple[str, int]] = []
        self.evaluate_calls: list[str] = []

    def evolve(self, variant, round_idx):
        self.evolve_calls.append((variant.variant_id, round_idx))
        return self.candidates

    def evaluate(self, candidate, t_k, round_idx):  # noqa: ARG002 - scripted round
        self.evaluate_calls.append(candidate.candidate_id)
        full = self.outcomes[candidate.candidate_id]
        return {task: full[task] for task in t_k}


class BucketCand:
    """An opaque candidate that also declares an edit ``bucket`` (M-17).

    It has no ``.manifest``, so the gate treats it opaquely and the decision is
    the pure seesaw (as in :class:`Cand`); the extra ``.bucket`` is what the
    ``bucket_disjoint`` ship policy reads via ``engine._candidate_buckets``.
    """

    def __init__(self, candidate_id: str, target_variant: str, bucket) -> None:
        self.candidate_id = candidate_id
        self.target_variant = target_variant
        self.bucket = list(bucket)


class BucketQueue:
    """Per-variant Critic-ranked queues of :class:`BucketCand` with outcomes."""

    def __init__(
        self,
        queues: dict[str, list[BucketCand]],
        outcomes: dict[str, dict[str, tuple[int, int]]],
    ) -> None:
        self.queues = queues
        self.outcomes = outcomes
        self.evolve_calls: list[tuple[str, int]] = []
        self.evaluate_calls: list[str] = []

    def evolve(self, variant, round_idx):
        self.evolve_calls.append((variant.variant_id, round_idx))
        return list(self.queues.get(variant.variant_id, []))

    def evaluate(self, candidate, t_k, round_idx):  # noqa: ARG002 - scripted round
        self.evaluate_calls.append(candidate.candidate_id)
        full = self.outcomes[candidate.candidate_id]
        return {task: full[task] for task in t_k}


def _artifact(tmp_path, candidate_id: str, *, target_variant: str = "V0") -> CandidateArtifact:
    """A complete prompt-only artifact suitable for the real manifest gate."""
    candidate_dir = tmp_path / candidate_id
    candidate_dir.mkdir()
    config_path = candidate_dir / "config.yaml"
    config_path.write_text(f"candidate: {candidate_id}\n", encoding="utf-8")
    manifest = ChangeManifest.model_validate(
        {
            "candidate_id": candidate_id,
            "bucket": ["prompt"],
            "file_changes": [
                {
                    "path": "gaia_agent.md",
                    "action": "modify",
                    "diff_summary": f"apply {candidate_id}",
                }
            ],
            "predicted_impact": {"tasks_will_unlock": ["new"]},
            "target_variant": target_variant,
        }
    )
    return CandidateArtifact(
        config_path=config_path,
        manifest=manifest,
        target_variant=target_variant,
    )


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
        kwargs.setdefault("routing_mode", "task_tournament")
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


def _task_router(**kwargs) -> Router:
    """Legacy task tournament for tests whose fixture partitions per task."""
    return Router(routing_mode="task_tournament", **kwargs)


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
    # ...plus the failures from the rest of the evaluated T_k. Omitting these
    # would give the child an optimistic routing/retirement prior.
    assert ledger.cell("V1", "a").attempts == 2
    assert ledger.cell("V1", "a").passes == 0
    assert ledger.cell("V1", "b").attempts == 2
    assert ledger.cell("V1", "b").passes == 0
    # Candidate outcomes are never credited to the unchanged parent.
    assert ledger.cell("V0", "a").attempts == 2
    assert ledger.cell("V0", "b").attempts == 2


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
    # Two-phase semantics: V1 was still evolved against the common freeze-time
    # snapshot; it returned no candidate, then retirement happened at settle.
    assert ("V1", 1) in scripted.evolve_calls
    assert all(call[0] != "V1" for call in scripted.evaluate_calls)


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


def test_retirement_defaults_to_task_macro_but_keeps_raw_compatibility() -> None:
    pool = VariantPool(K=3)
    pool.add_root("cfg/V0.yaml", "j/V0.md", tasks=set())
    pool.fork("V0", set(), 0)  # V1
    pool.fork("V0", set(), 0)  # V2
    ledger = SuccessLedger()
    ledger.record("V1", "easy", 100, 100, 0)
    ledger.record("V1", "hard", 0, 2, 0)  # task-macro .5, raw .98
    ledger.record("V2", "a", 3, 4, 0)
    ledger.record("V2", "b", 3, 4, 0)     # task-macro/raw .75
    scripted = Scripted({})

    macro = VariantPoolEngine(
        pool,
        ledger,
        Router(),
        evaluate=scripted.evaluate,
        evolve=scripted.evolve,
    )
    raw = VariantPoolEngine(
        pool,
        ledger,
        Router(),
        evaluate=scripted.evaluate,
        evolve=scripted.evolve,
        retirement_metric="raw",
    )

    assert macro._weakest_other("V0") == "V1"
    assert raw._weakest_other("V0") == "V2"


def test_two_phase_settlement_handles_multiple_full_pool_forks_deterministically() -> None:
    """Both candidates are gated before one planned retire/reassign/fork batch."""
    ledger = SuccessLedger()
    router = Router()
    pool = SpyPool(K=4)
    pool.add_root(
        "cfg/V0.yaml",
        "j/V0.md",
        tasks={"new0", "old0", "new1", "old1", "orphan2", "orphan3"},
    )
    pool.fork("V0", {"new1", "old1"}, 0)   # V1
    pool.fork("V0", {"orphan2"}, 0)        # V2
    pool.fork("V0", {"orphan3"}, 0)        # V3
    pool.fork_calls.clear()

    ledger.record("V0", "old0", 2, 2, 0)
    ledger.record("V1", "old1", 2, 2, 0)
    ledger.record("V2", "orphan2", 2, 2, 0)
    ledger.record("V3", "orphan3", 2, 2, 0)
    scripted = Scripted(
        {
            ("V0", 1): {"new0": (2, 2), "old0": (0, 2)},
            ("V1", 1): {"new1": (2, 2), "old1": (0, 2)},
        }
    )
    engine = VariantPoolEngine(
        pool,
        ledger,
        router,
        evaluate=scripted.evaluate,
        evolve=scripted.evolve,
    )

    result = engine.run_round(
        1,
        {"new0", "old0", "new1", "old1", "orphan2", "orphan3"},
    )

    assert result.decisions["V0"] is Decision.FORK
    assert result.decisions["V1"] is Decision.FORK
    assert result.retired == ["V2", "V3"]
    assert result.forked == ["V4", "V5"]
    assert pool.reassign_calls == [{"orphan2", "orphan3"}]
    assert pool.fork_calls == [
        ("V0", {"new0"}, 1),
        ("V1", {"new1"}, 1),
    ]
    assert set(pool.variants) == {"V0", "V1", "V4", "V5"}
    assert len(pool) == pool.K
    # Both freeze-time parents were evaluated before either retire occurred.
    assert ("V0", frozenset({"new0", "old0"}), 1) in scripted.evaluate_calls
    assert ("V1", frozenset({"new1", "old1"}), 1) in scripted.evaluate_calls


def test_two_phase_settlement_protects_apply_and_fork_parents() -> None:
    ledger = SuccessLedger()
    router = Router()
    pool = SpyPool(K=3)
    pool.add_root(
        "cfg/V0.yaml",
        "j/V0.md",
        tasks={"apply", "kept", "new", "old", "orphan"},
    )
    pool.fork("V0", {"new", "old"}, 0)  # V1
    pool.fork("V0", {"orphan"}, 0)      # V2
    pool.fork_calls.clear()
    ledger.record("V0", "kept", 2, 2, 0)
    ledger.record("V1", "old", 2, 2, 0)
    ledger.record("V2", "orphan", 2, 2, 0)

    scripted = Scripted(
        {
            ("V0", 1): {"apply": (2, 2), "kept": (2, 2)},
            ("V1", 1): {"new": (2, 2), "old": (0, 2)},
        }
    )
    result = VariantPoolEngine(
        pool,
        ledger,
        router,
        evaluate=scripted.evaluate,
        evolve=scripted.evolve,
    ).run_round(1, {"apply", "kept", "new", "old", "orphan"})

    assert result.decisions == {"V0": Decision.APPLY, "V1": Decision.FORK}
    assert result.retired == ["V2"]
    assert result.forked == ["V3"]
    assert set(pool.variants) == {"V0", "V1", "V3"}
    assert ledger.cell("V0", "apply").passes == 2
    assert ledger.cell("V3", "new").passes == 2
    assert ledger.cell("V3", "old").passes == 0


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


def test_default_fork_threshold_matches_the_paper() -> None:
    assert DEFAULT_MIN_FORK == (1, 1)


# ===========================================================================
# 6. Narrowed evaluation — evolve/evaluate only touch each variant's T_k
# ===========================================================================


def test_evolve_and_evaluate_stay_within_each_variants_tk() -> None:
    """Two variants with disjoint clusters; a third that carries nothing is skipped."""
    ledger = SuccessLedger()
    router = _task_router()
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
    assert result.no_candidate_variants == ["V0"]
    assert result.candidate_diagnostics == {}
    assert result.shipped is False
    assert result.idle == 1
    assert ledger.cell("V0", "a") is None      # nothing recorded


# ===========================================================================
# 7. Ranked candidate queues
# ===========================================================================


def test_ranked_queue_rejects_then_applies_in_the_supplied_order(tmp_path) -> None:
    """Critic order is preserved; a rejection advances to the next candidate."""
    from variant_pool.evidence import EvidenceStore

    ledger = SuccessLedger()
    ledger.record("V0", "old", 2, 2, 0)
    pool = VariantPool(K=2)
    pool.add_root("cfg/V0.yaml", "j/V0.md", tasks={"old", "new"})
    evidence = EvidenceStore(tmp_path / "run")

    # Deliberately reverse lexical id order: Critic rank, not id sorting, owns
    # the evaluation queue.
    rejected = _artifact(tmp_path, "C-R1-02")
    applied = _artifact(tmp_path, "C-R1-01")
    scripted = QueueScripted(
        (rejected, applied),
        {
            rejected.candidate_id: {"old": (2, 2), "new": (0, 2)},
            applied.candidate_id: {"old": (2, 2), "new": (2, 2)},
        },
    )
    settled: list[tuple[str, CandidateArtifact]] = []
    engine = VariantPoolEngine(
        pool,
        ledger,
        Router(),
        evaluate=scripted.evaluate,
        evolve=scripted.evolve,
        evidence=evidence,
    )
    engine._apply_candidate = lambda variant, candidate: settled.append(  # type: ignore[method-assign]
        (variant.variant_id, candidate)
    )

    result = engine.run_round(1, {"old", "new"})

    assert scripted.evaluate_calls == ["C-R1-02", "C-R1-01"]
    assert result.decisions == {"V0": Decision.APPLY}
    assert result.selected_candidate_ids == {"V0": "C-R1-01"}
    assert result.per_variant_pass["V0"] == {"new": (2, 2), "old": (2, 2)}
    assert settled == [("V0", applied)]  # settlement keeps the artifact, not only its manifest

    first = result.candidate_diagnostics["C-R1-02"]
    second = result.candidate_diagnostics["C-R1-01"]
    assert first.decision is Decision.REJECT
    assert first.failed_stage is GateStage.SEESAW_REGRESSION
    assert first.evaluation == {"new": (0, 2), "old": (2, 2)}
    assert second.decision is Decision.APPLY
    assert second.failed_stage is None

    archived = evidence.rejected_candidates()
    assert [item["candidate_id"] for item in archived] == ["C-R1-02"]
    assert archived[0]["failed_stage"] == "SEESAW_REGRESSION"
    assert "no task improved" in archived[0]["archive_reason"]


def test_first_fork_skips_the_rest_and_settles_the_original_artifact(tmp_path) -> None:
    ledger = SuccessLedger()
    ledger.record("V0", "old", 2, 2, 0)
    pool = VariantPool(K=2)
    pool.add_root("cfg/V0.yaml", "j/V0.md", tasks={"old", "new"})

    forked = _artifact(tmp_path, "C-R1-01")
    skipped = _artifact(tmp_path, "C-R1-02")
    scripted = QueueScripted(
        [forked, skipped],
        {
            forked.candidate_id: {"old": (0, 2), "new": (2, 2)},
            skipped.candidate_id: {"old": (2, 2), "new": (2, 2)},
        },
    )
    settled: list[tuple[str, CandidateArtifact]] = []
    engine = VariantPoolEngine(
        pool,
        ledger,
        Router(),
        evaluate=scripted.evaluate,
        evolve=scripted.evolve,
    )
    engine._apply_candidate = lambda variant, candidate: settled.append(  # type: ignore[method-assign]
        (variant.variant_id, candidate)
    )

    result = engine.run_round(1, {"old", "new"})

    assert scripted.evaluate_calls == ["C-R1-01"]
    assert result.decisions == {"V0": Decision.FORK}
    assert result.selected_candidate_ids == {"V0": "C-R1-01"}
    assert result.forked == ["V1"]
    assert settled == [("V1", forked)]
    assert result.candidate_diagnostics["C-R1-01"].decision is Decision.FORK
    skipped_diagnostic = result.candidate_diagnostics["C-R1-02"]
    assert skipped_diagnostic.decision is None
    assert skipped_diagnostic.evaluation == {}
    assert "earlier candidate C-R1-01 selected fork" == skipped_diagnostic.skipped_reason


def test_legacy_single_candidate_return_remains_compatible() -> None:
    ledger = SuccessLedger()
    pool = VariantPool(K=1)
    pool.add_root("cfg/V0.yaml", "j/V0.md", tasks={"a"})
    scripted = Scripted({("V0", 0): {"a": (2, 2)}})
    engine = VariantPoolEngine(
        pool,
        ledger,
        Router(),
        evaluate=scripted.evaluate,
        evolve=scripted.evolve,
    )

    result = engine.run_round(0, {"a"})

    assert result.decisions == {"V0": Decision.APPLY}
    assert result.per_variant_pass == {"V0": {"a": (2, 2)}}
    assert result.selected_candidate_ids == {"V0": "C-R0-V0"}
    assert result.candidate_diagnostics["C-R0-V0"].decision is Decision.APPLY


# ===========================================================================
# 8. RoundResult shape and evidence side effects
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


@pytest.mark.parametrize(
    ("outcomes", "expected_decision"),
    [
        ({"old": (2, 2), "new": (2, 2)}, Decision.APPLY),
        ({"old": (0, 2), "new": (2, 2)}, Decision.FORK),
    ],
)
def test_selected_gate_results_can_be_left_for_external_settled_scoring(
    tmp_path,
    outcomes,
    expected_decision,
) -> None:
    """Paper-mode full-set scoring can own the round's sole ledger write."""
    from variant_pool.evidence import EvidenceStore

    ledger = SuccessLedger()
    ledger.record("V0", "old", 2, 2, 0)
    pool = VariantPool(K=2)
    pool.add_root("cfg/V0.yaml", "j/V0.md", tasks={"old", "new"})
    evidence = EvidenceStore(tmp_path / "run")
    scripted = Scripted({("V0", 1): outcomes})
    engine = VariantPoolEngine(
        pool,
        ledger,
        Router(),
        evaluate=scripted.evaluate,
        evolve=scripted.evolve,
        evidence=evidence,
        record_selected_results=False,
    )

    result = engine.run_round(1, {"old", "new"})

    assert result.decisions == {"V0": expected_decision}
    assert result.shipped is True
    assert result.per_variant_pass == {"V0": outcomes}
    assert ledger.cell("V0", "new") is None
    assert ledger.cell("V0", "old").last_round == 0
    assert all(ledger.cell("V1", task) is None for task in ("old", "new"))
    assert list(evidence.iter_digests()) == []


# ===========================================================================
# 9. Constructor validation
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


# ===========================================================================
# 10. Ship policy — M-17 App B.1 ranked bucket-disjoint multi-ship
# ===========================================================================


def _mk(ledger, pool, *, tasks=("old", "new")):
    """Route ``old`` as solved-by-V0 and ``new`` as unsolved (fork/apply fuel)."""
    ledger.record("V0", "old", 2, 2, 0)
    pool.add_root("cfg/V0.yaml", "j/V0.md", tasks=set(tasks))


def test_ship_policy_defaults_to_first_wins() -> None:
    pool = VariantPool(K=1)
    pool.add_root("cfg/V0.yaml", "j/V0.md", tasks={"a"})
    engine = VariantPoolEngine(
        pool, SuccessLedger(), Router(), evaluate=lambda *a: {}, evolve=lambda *a: None
    )
    assert engine.ship_policy == "first_wins"


@pytest.mark.parametrize("bad", ["", "first-wins", "bucketdisjoint", "off"])
def test_ship_policy_rejects_unknown_value(bad) -> None:
    pool = VariantPool(K=1)
    pool.add_root("cfg/V0.yaml", "j/V0.md", tasks={"a"})
    with pytest.raises(ValueError):
        VariantPoolEngine(
            pool, SuccessLedger(), Router(), evaluate=lambda *a: {}, evolve=lambda *a: None, ship_policy=bad
        )


def test_first_wins_and_bucket_disjoint_diverge_on_the_same_queue() -> None:
    """Identity pin: the same ranked queue ships one under first_wins (the break)
    and both disjoint-bucket candidates under bucket_disjoint (the scan-on)."""

    def build():
        ledger = SuccessLedger()
        pool = VariantPool(K=4)
        _mk(ledger, pool)
        a = BucketCand("C-R1-01", "V0", ["prompt"])
        b = BucketCand("C-R1-02", "V0", ["tools"])
        driver = BucketQueue(
            {"V0": [a, b]},
            {
                "C-R1-01": {"old": (0, 2), "new": (2, 2)},  # regress old, improve new -> FORK
                "C-R1-02": {"old": (0, 2), "new": (2, 2)},  # same seesaw, disjoint bucket
            },
        )
        return ledger, pool, driver

    # first_wins (default): the first fork wins, the queue breaks, the rest are
    # skipped *without evaluation* with the legacy reason — byte-identical.
    ledger, pool, driver = build()
    fw = VariantPoolEngine(pool, ledger, Router(), evaluate=driver.evaluate, evolve=driver.evolve)
    fw_res = fw.run_round(1, {"old", "new"})
    assert driver.evaluate_calls == ["C-R1-01"]
    assert fw_res.forked == ["V1"]
    assert fw_res.candidate_diagnostics["C-R1-02"].skipped_reason == (
        "earlier candidate C-R1-01 selected fork"
    )
    assert fw_res.candidate_diagnostics["C-R1-02"].decision is None  # never gated

    # bucket_disjoint: the queue is scanned on and both disjoint forks ship.
    ledger, pool, driver = build()
    bd = VariantPoolEngine(
        pool, ledger, Router(), evaluate=driver.evaluate, evolve=driver.evolve, ship_policy="bucket_disjoint"
    )
    bd_res = bd.run_round(1, {"old", "new"})
    assert driver.evaluate_calls == ["C-R1-01", "C-R1-02"]
    assert bd_res.forked == ["V1", "V2"]
    assert bd_res.candidate_diagnostics["C-R1-02"].skipped_reason is None
    assert bd_res.candidate_diagnostics["C-R1-02"].decision is Decision.FORK


def test_bucket_disjoint_ships_an_apply_and_a_disjoint_fork_the_same_round() -> None:
    """(a) One in-place APPLY + one FORK child on disjoint buckets both ship."""
    ledger = SuccessLedger()
    pool = SpyPool(K=4)
    _mk(ledger, pool)
    apply_c = BucketCand("C-R1-01", "V0", ["prompt"])
    fork_c = BucketCand("C-R1-02", "V0", ["tools"])
    driver = BucketQueue(
        {"V0": [apply_c, fork_c]},
        {
            "C-R1-01": {"old": (2, 2), "new": (2, 2)},  # improve new, keep old -> APPLY
            "C-R1-02": {"old": (0, 2), "new": (2, 2)},  # improve new, regress old -> FORK
        },
    )
    settled: list[tuple[str, str]] = []
    engine = VariantPoolEngine(
        pool, ledger, Router(), evaluate=driver.evaluate, evolve=driver.evolve, ship_policy="bucket_disjoint"
    )
    engine._apply_candidate = lambda variant, candidate: settled.append(  # type: ignore[method-assign]
        (variant.variant_id, candidate.candidate_id)
    )

    result = engine.run_round(1, {"old", "new"})

    # Both ranked candidates evaluated (no first-wins break).
    assert driver.evaluate_calls == ["C-R1-01", "C-R1-02"]
    # V0 mutated in place; the disjoint fork spawned a child (different targets).
    assert result.decisions["V0"] is Decision.APPLY  # not clobbered by the fork
    assert result.forked == ["V1"]
    assert result.selected_candidate_ids["V0"] == "C-R1-01"
    assert result.shipped is True
    assert settled == [("V0", "C-R1-01"), ("V1", "C-R1-02")]
    assert result.candidate_diagnostics["C-R1-01"].decision is Decision.APPLY
    assert result.candidate_diagnostics["C-R1-01"].skipped_reason is None
    assert result.candidate_diagnostics["C-R1-02"].decision is Decision.FORK
    assert result.candidate_diagnostics["C-R1-02"].skipped_reason is None


def test_bucket_disjoint_skips_a_candidate_whose_bucket_is_already_claimed() -> None:
    """(b) A later candidate sharing any claimed bucket is skipped (App B.1)."""
    ledger = SuccessLedger()
    pool = VariantPool(K=4)
    _mk(ledger, pool)
    first = BucketCand("C-R1-01", "V0", ["prompt"])
    shadow = BucketCand("C-R1-02", "V0", ["prompt", "config"])  # shares 'prompt'
    driver = BucketQueue(
        {"V0": [first, shadow]},
        {
            "C-R1-01": {"old": (2, 2), "new": (2, 2)},  # APPLY, claims 'prompt'
            "C-R1-02": {"old": (2, 2), "new": (2, 2)},  # would APPLY, but 'prompt' taken
        },
    )
    engine = VariantPoolEngine(
        pool, ledger, Router(), evaluate=driver.evaluate, evolve=driver.evolve, ship_policy="bucket_disjoint"
    )
    result = engine.run_round(1, {"old", "new"})

    assert result.decisions == {"V0": Decision.APPLY}
    assert result.forked == []
    assert result.selected_candidate_ids == {"V0": "C-R1-01"}
    skipped = result.candidate_diagnostics["C-R1-02"]
    assert skipped.decision is Decision.APPLY  # the gate approved it
    assert skipped.skipped_reason is not None
    assert "already claimed" in skipped.skipped_reason
    assert "prompt" in skipped.skipped_reason


def test_bucket_disjoint_skips_a_second_same_variant_apply_as_unreconstructable() -> None:
    """(c) A second APPLY to an already-applied variant is skipped, even on a
    disjoint bucket, because whole-config candidates cannot be soundly merged."""
    ledger = SuccessLedger()
    pool = VariantPool(K=4)
    _mk(ledger, pool)
    first = BucketCand("C-R1-01", "V0", ["prompt"])
    second = BucketCand("C-R1-02", "V0", ["config"])  # disjoint bucket, but 2nd APPLY to V0
    driver = BucketQueue(
        {"V0": [first, second]},
        {
            "C-R1-01": {"old": (2, 2), "new": (2, 2)},  # APPLY
            "C-R1-02": {"old": (2, 2), "new": (2, 2)},  # APPLY on a disjoint bucket
        },
    )
    engine = VariantPoolEngine(
        pool, ledger, Router(), evaluate=driver.evaluate, evolve=driver.evolve, ship_policy="bucket_disjoint"
    )
    result = engine.run_round(1, {"old", "new"})

    assert result.decisions == {"V0": Decision.APPLY}
    assert result.forked == []
    second_diag = result.candidate_diagnostics["C-R1-02"]
    assert second_diag.decision is Decision.APPLY  # gate approved; policy skipped
    assert second_diag.skipped_reason == (
        "ship_skipped: bucket-disjoint same-variant apply requires config "
        "merge (unreconstructable from whole-config candidates)"
    )


def test_bucket_disjoint_same_variant_multi_fork_without_retirement() -> None:
    """(d-i) Two disjoint-bucket forks from one variant both spawn children when
    the pool has spare capacity (no retirement needed)."""
    ledger = SuccessLedger()
    pool = SpyPool(K=4)
    _mk(ledger, pool)
    f1 = BucketCand("C-R1-01", "V0", ["prompt"])
    f2 = BucketCand("C-R1-02", "V0", ["tools"])
    driver = BucketQueue(
        {"V0": [f1, f2]},
        {
            "C-R1-01": {"old": (0, 2), "new": (2, 2)},  # FORK
            "C-R1-02": {"old": (0, 2), "new": (2, 2)},  # FORK, disjoint bucket
        },
    )
    engine = VariantPoolEngine(
        pool, ledger, Router(), evaluate=driver.evaluate, evolve=driver.evolve, ship_policy="bucket_disjoint"
    )
    result = engine.run_round(1, {"old", "new"})

    assert result.decisions["V0"] is Decision.FORK
    assert result.forked == ["V1", "V2"]
    assert result.retired == []
    assert pool.retire_calls == []
    assert len(pool) == 3
    assert [parent for parent, _, _ in pool.fork_calls] == ["V0", "V0"]
    assert driver.evaluate_calls == ["C-R1-01", "C-R1-02"]


def test_bucket_disjoint_preserves_multi_variant_fork_retire_capacity() -> None:
    """(d-ii) The App B.1 policy leaves ``_plan_forks`` capacity/retire/reassign
    planning intact: this is the multi-variant full-pool multi-fork test rerun
    under bucket_disjoint (forks on different variants carry no buckets, so the
    settled outcome is identical to first_wins)."""
    ledger = SuccessLedger()
    router = Router()
    pool = SpyPool(K=4)
    pool.add_root(
        "cfg/V0.yaml",
        "j/V0.md",
        tasks={"new0", "old0", "new1", "old1", "orphan2", "orphan3"},
    )
    pool.fork("V0", {"new1", "old1"}, 0)  # V1
    pool.fork("V0", {"orphan2"}, 0)  # V2
    pool.fork("V0", {"orphan3"}, 0)  # V3
    pool.fork_calls.clear()

    ledger.record("V0", "old0", 2, 2, 0)
    ledger.record("V1", "old1", 2, 2, 0)
    ledger.record("V2", "orphan2", 2, 2, 0)
    ledger.record("V3", "orphan3", 2, 2, 0)
    scripted = Scripted(
        {
            ("V0", 1): {"new0": (2, 2), "old0": (0, 2)},
            ("V1", 1): {"new1": (2, 2), "old1": (0, 2)},
        }
    )
    engine = VariantPoolEngine(
        pool,
        ledger,
        router,
        evaluate=scripted.evaluate,
        evolve=scripted.evolve,
        ship_policy="bucket_disjoint",
    )

    result = engine.run_round(1, {"new0", "old0", "new1", "old1", "orphan2", "orphan3"})

    assert result.decisions["V0"] is Decision.FORK
    assert result.decisions["V1"] is Decision.FORK
    assert result.retired == ["V2", "V3"]
    assert result.forked == ["V4", "V5"]
    assert pool.reassign_calls == [{"orphan2", "orphan3"}]
    assert pool.fork_calls == [("V0", {"new0"}, 1), ("V1", {"new1"}, 1)]
    assert set(pool.variants) == {"V0", "V1", "V4", "V5"}
    assert len(pool) == pool.K
