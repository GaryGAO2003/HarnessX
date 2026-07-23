# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""Offline unit tests for ``variant_pool.pool`` (W1 + W23).

Everything runs under ``tmp_path``; no LLM, no network, no repo imports.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from variant_pool.pool import DEFAULT_K, Variant, VariantPool


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _root(tmp_path: Path, *, tasks=None, K: int = DEFAULT_K, write_files: bool = True) -> tuple[VariantPool, Variant]:
    """A pool with V0 in place; its config/journal exist on disk by default."""
    pool = VariantPool(K=K)
    config = tmp_path / "V0.yaml"
    journal = tmp_path / "learnings_V0.md"
    if write_files:
        config.write_text("harness: root\n", encoding="utf-8")
        journal.write_text("# V0 learnings\n", encoding="utf-8")
    return pool, pool.add_root(config, journal, tasks=tasks)


class _StubRouter:
    """Duck-typed stand-in for :class:`variant_pool.router.Router`.

    ``pool.reassign`` only ever calls ``route``/``cold_start``; keeping a stub
    here means the pool tests stay independent of the router module.
    """

    def __init__(self, choice: str) -> None:
        self.choice = choice
        self.cold_start_calls: list[str] = []
        self.route_calls: list[tuple[str, int]] = []

    def cold_start(self, task_id, pool, ledger=None):  # noqa: ARG002 - stub signature
        self.cold_start_calls.append(task_id)
        return self.choice

    def route(self, task_id, pool, ledger, *, before_round):  # noqa: ARG002 - stub signature
        self.route_calls.append((task_id, before_round))
        return self.choice


# ---------------------------------------------------------------------------
# Capacity K — our default (paper never assigns K a value; report §5 gap 1)
# ---------------------------------------------------------------------------


def test_default_capacity_is_eight() -> None:
    """Explicit test of an *our-default* knob: K = 8 (SPEC §6.6)."""
    assert DEFAULT_K == 8
    assert VariantPool().K == 8


def test_capacity_is_configurable_and_validated() -> None:
    assert VariantPool(K=4).K == 4
    assert VariantPool(K=16).K == 16
    with pytest.raises(ValueError):
        VariantPool(K=0)


def test_is_full_tracks_capacity(tmp_path: Path) -> None:
    pool, root = _root(tmp_path, K=2)
    assert not pool.is_full()
    pool.fork(root.variant_id, set(), at_round=1)
    assert pool.is_full()
    with pytest.raises(RuntimeError, match="pool is full"):
        pool.fork(root.variant_id, set(), at_round=1)


# ---------------------------------------------------------------------------
# add_root
# ---------------------------------------------------------------------------


def test_add_root_creates_v0_carrying_every_task(tmp_path: Path) -> None:
    pool, root = _root(tmp_path, tasks={"t1", "t2", "t3"})
    assert root.variant_id == "V0"
    assert root.parent_id is None
    assert root.created_round == 0
    assert root.routed_tasks == {"t1", "t2", "t3"}
    assert pool.variants == {"V0": root}


def test_add_root_without_tasks_starts_empty(tmp_path: Path) -> None:
    _, root = _root(tmp_path)
    assert root.routed_tasks == set()


def test_add_root_twice_is_rejected(tmp_path: Path) -> None:
    pool, _ = _root(tmp_path)
    with pytest.raises(RuntimeError, match="non-empty pool"):
        pool.add_root(tmp_path / "other.yaml", tmp_path / "other.md")


# ---------------------------------------------------------------------------
# fork — identity, lineage, task inheritance
# ---------------------------------------------------------------------------


def test_fork_transfers_improved_tasks_off_the_parent(tmp_path: Path) -> None:
    """Our inheritance rule (SPEC §2.1/§6.6): improved tasks *move*."""
    pool, root = _root(tmp_path, tasks={"t1", "t2", "t3"})
    child = pool.fork(root.variant_id, {"t1", "t2"}, at_round=4)

    assert child.routed_tasks == {"t1", "t2"}
    assert root.routed_tasks == {"t3"}
    # no task is carried twice
    assert root.routed_tasks & child.routed_tasks == set()


def test_fork_records_lineage_and_monotonic_ids(tmp_path: Path) -> None:
    pool, root = _root(tmp_path, tasks={"t1", "t2", "t3"})
    first = pool.fork(root.variant_id, {"t1"}, at_round=4)
    second = pool.fork(root.variant_id, {"t2"}, at_round=7)
    third = pool.fork(first.variant_id, {"t1"}, at_round=9)

    assert [first.variant_id, second.variant_id, third.variant_id] == ["V1", "V2", "V3"]
    assert first.parent_id == "V0"
    assert third.parent_id == "V1"
    assert (first.created_round, second.created_round, third.created_round) == (4, 7, 9)
    assert pool.next_id == 4


def test_fork_ids_are_never_reused_after_retire(tmp_path: Path) -> None:
    pool, root = _root(tmp_path, tasks={"t1", "t2"})
    child = pool.fork(root.variant_id, {"t1"}, at_round=1)
    pool.retire(child.variant_id)
    again = pool.fork(root.variant_id, {"t2"}, at_round=2)
    assert again.variant_id == "V2"


def test_fork_unknown_parent_raises(tmp_path: Path) -> None:
    pool, _ = _root(tmp_path)
    with pytest.raises(KeyError):
        pool.fork("V9", set(), at_round=1)


# ---------------------------------------------------------------------------
# fork — config clone + per-variant journal (W9 novelty isolation)
# ---------------------------------------------------------------------------


def test_fork_clones_config_and_gives_the_child_its_own_journal(tmp_path: Path) -> None:
    pool, root = _root(tmp_path, tasks={"t1"})
    child = pool.fork(root.variant_id, {"t1"}, at_round=1)

    assert child.config_path != root.config_path
    assert child.journal_path != root.journal_path
    assert child.config_path.name == "V1.yaml"
    assert child.journal_path.name == "learnings_V1.md"
    # inherit-then-diverge: content is cloned ...
    assert child.config_path.read_text(encoding="utf-8") == "harness: root\n"
    assert child.journal_path.read_text(encoding="utf-8") == "# V0 learnings\n"

    # ... but the files are independent, which is what isolates novelty (W9)
    child.journal_path.write_text("# V1 learnings\n", encoding="utf-8")
    assert root.journal_path.read_text(encoding="utf-8") == "# V0 learnings\n"


def test_fork_tolerates_placeholder_paths(tmp_path: Path) -> None:
    """Batch A keeps config paths as placeholders (SPEC §5) — absent is fine."""
    pool, root = _root(tmp_path, tasks={"t1"}, write_files=False)
    child = pool.fork(root.variant_id, {"t1"}, at_round=1)
    assert not child.config_path.exists()
    assert child.config_path.name == "V1.yaml"


# ---------------------------------------------------------------------------
# fork — W23 slot cloning
# ---------------------------------------------------------------------------


def test_fork_deep_copies_owned_slot_dirs(tmp_path: Path) -> None:
    """W23: a variant that owns a slot hands the child an independent copy."""
    pool, root = _root(tmp_path, tasks={"t1"})
    tools = tmp_path / "slots" / "V0_tools"
    (tools / "registry").mkdir(parents=True)
    (tools / "registry" / "search.json").write_text('{"tool": "v0"}', encoding="utf-8")
    root.slot_dirs["tools"] = tools

    child = pool.fork(root.variant_id, {"t1"}, at_round=3)
    child_tools = child.slot_dirs["tools"]

    assert child_tools is not None
    assert child_tools != tools
    assert child_tools.name == "V1_tools"
    assert (child_tools / "registry" / "search.json").read_text(encoding="utf-8") == '{"tool": "v0"}'

    # divergence in the tools dimension must not touch the parent's registry
    (child_tools / "registry" / "search.json").write_text('{"tool": "v1"}', encoding="utf-8")
    assert (tools / "registry" / "search.json").read_text(encoding="utf-8") == '{"tool": "v0"}'


def test_fork_keeps_shared_slots_shared(tmp_path: Path) -> None:
    """``None`` means "still shared with the parent" — stays lazy on fork."""
    pool, root = _root(tmp_path, tasks={"t1"})
    root.slot_dirs["workspace"] = None

    child = pool.fork(root.variant_id, {"t1"}, at_round=1)
    assert child.slot_dirs == {"workspace": None}


def test_fork_slot_clone_refuses_to_overwrite(tmp_path: Path) -> None:
    pool, root = _root(tmp_path, tasks={"t1"})
    tools = tmp_path / "slots" / "V0_tools"
    tools.mkdir(parents=True)
    root.slot_dirs["tools"] = tools
    (tmp_path / "slots" / "V1_tools").mkdir()

    with pytest.raises(RuntimeError, match="already exists"):
        pool.fork(root.variant_id, {"t1"}, at_round=1)


# ---------------------------------------------------------------------------
# retire + reassign (W6)
# ---------------------------------------------------------------------------


def test_retire_returns_orphan_tasks_and_drops_the_variant(tmp_path: Path) -> None:
    pool, root = _root(tmp_path, tasks={"t1", "t2", "t3"})
    child = pool.fork(root.variant_id, {"t1", "t2"}, at_round=1)

    orphans = pool.retire(child.variant_id)

    assert orphans == {"t1", "t2"}
    assert child.variant_id not in pool.variants
    assert len(pool) == 1
    # the orphans are *not* silently handed back to the parent
    assert root.routed_tasks == {"t3"}


def test_retire_refuses_to_empty_the_pool(tmp_path: Path) -> None:
    pool, root = _root(tmp_path, tasks={"t1"})
    with pytest.raises(RuntimeError, match="last remaining variant"):
        pool.retire(root.variant_id)


def test_retire_unknown_variant_raises(tmp_path: Path) -> None:
    pool, _ = _root(tmp_path)
    with pytest.raises(KeyError):
        pool.retire("V7")


def test_reassign_routes_orphans_back_into_the_pool(tmp_path: Path) -> None:
    pool, root = _root(tmp_path, tasks={"t1", "t2", "t3"})
    child = pool.fork(root.variant_id, {"t1", "t2"}, at_round=1)
    orphans = pool.retire(child.variant_id)

    router = _StubRouter(choice=root.variant_id)
    mapping = pool.reassign(orphans, router)

    assert mapping == {"t1": "V0", "t2": "V0"}
    assert root.routed_tasks == {"t1", "t2", "t3"}
    assert sorted(router.cold_start_calls) == ["t1", "t2"]
    assert router.route_calls == []


def test_reassign_with_a_ledger_uses_the_frozen_route(tmp_path: Path) -> None:
    """With ledger + before_round the orphans go through the normal argmax."""
    pool, root = _root(tmp_path, tasks={"t1"})
    child = pool.fork(root.variant_id, {"t1"}, at_round=1)
    orphans = pool.retire(child.variant_id)

    router = _StubRouter(choice=root.variant_id)
    mapping = pool.reassign(orphans, router, ledger=object(), before_round=5)

    assert mapping == {"t1": "V0"}
    assert router.route_calls == [("t1", 5)]
    assert router.cold_start_calls == []


# ---------------------------------------------------------------------------
# routing bookkeeping helpers
# ---------------------------------------------------------------------------


def test_carrier_of_finds_the_current_variant(tmp_path: Path) -> None:
    pool, root = _root(tmp_path, tasks={"t1", "t2"})
    child = pool.fork(root.variant_id, {"t1"}, at_round=1)

    assert pool.carrier_of("t1") == child.variant_id
    assert pool.carrier_of("t2") == root.variant_id
    assert pool.carrier_of("nope") is None


def test_apply_routing_repartitions_without_duplicating(tmp_path: Path) -> None:
    pool, root = _root(tmp_path, tasks={"t1", "t2"})
    child = pool.fork(root.variant_id, {"t1"}, at_round=1)

    pool.apply_routing({"t1": root.variant_id, "t2": child.variant_id})

    assert root.routed_tasks == {"t1"}
    assert child.routed_tasks == {"t2"}


def test_apply_routing_rejects_unknown_variants(tmp_path: Path) -> None:
    pool, _ = _root(tmp_path, tasks={"t1"})
    with pytest.raises(KeyError):
        pool.apply_routing({"t1": "V9"})
