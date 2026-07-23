# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""W1 + W23 — variant-pool state container with slot cloning.

Implements the harness *variant pool* of HarnessX §4.5 (p.11)::

    H_t = {H_t^(1), ..., H_t^(V_t)},   V_t <= K

A :class:`Variant` is one harness configuration plus the bookkeeping the paper
leaves implicit: which tasks it currently carries (``routed_tasks`` = the
paper's ``T_k``, the "tasks routed to k" that narrow the seesaw check), which
per-variant journal isolates its novelty check (W9), and which
configuration-level *slot* directories it owns outright (W23).

Paper-derived vs. ours
----------------------
Derived (§4.5 p.11):
    * pool holds at most ``K`` variants;
    * a fork spawns a new variant from a parent;
    * a full pool retires its lowest-performing variant.

Ours — the paper is silent (report §5, gaps 1/4/5); every default below is an
explicit experiment choice recorded in the run lock (SPEC §6.3/§6.6):
    * ``K = 8`` (paper never assigns a value; ablation {4, 8, 16});
    * fork task inheritance: ``improved_tasks`` *transfer* off the parent;
    * retirement leaves orphan tasks that must be re-routed (:meth:`reassign`).

W23 / slot cloning
------------------
Report §7 M2 (paper §3.2 p.6): tool registry, tracer, workspace and sandbox
provider are *per-configuration singletons*. Report §7 M11 (paper §3.3 p.6):
D4 (tool ecosystem) is one of the two most frequent edit targets, so forking in
the tools dimension is the **main path**, not an edge case — a forked variant
needs its own copies of those slot directories. ``slot_dirs[name] is None``
means "shared with the parent, copy lazily"; a real path means "this variant
owns it", and a fork deep-copies it (SPEC §2.1).

Notes
-----
Batch A is pure offline logic: ``config_path``/``journal_path`` are placeholders
and no HarnessConfig serialisation happens here (SPEC §5); the only filesystem
work is cloning files/directories that actually exist, which keeps the module
testable under ``tmp_path``.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Iterable

if TYPE_CHECKING:  # pragma: no cover - typing only, no runtime import cycle
    from .ledger import SuccessLedger
    from .router import Router

#: Our default pool capacity. The paper writes "up to K harness variants"
#: (§4.5 p.11) but never assigns K a value (report §5, gap 1). Ablation
#: range {4, 8, 16} per SPEC §6.6.
DEFAULT_K = 8


@dataclass
class Variant:
    """One harness variant in the pool.

    ``routed_tasks`` is the paper's ``T_k`` under the main-arm cluster reading
    ("routed"): the set of tasks currently carried by this variant. A candidate
    targeting variant *k* is checked only against these tasks (§4.5 p.11).
    """

    variant_id: str
    config_path: Path
    journal_path: Path
    created_round: int
    parent_id: str | None
    routed_tasks: set[str] = field(default_factory=set)
    #: slot name -> directory owned by this variant, or ``None`` when the slot
    #: is still shared with the parent (lazy copy). See W23 above.
    slot_dirs: dict[str, Path | None] = field(default_factory=dict)


class VariantPool:
    """Container for at most ``K`` variants (§4.5 p.11).

    The pool owns variant identity (``V0``, ``V1``, ... monotonically
    increasing and never reused), the fork/retire lifecycle, and the
    ``routed_tasks`` bookkeeping. It deliberately knows nothing about success
    rates: ranking lives in :mod:`.ledger`, and choosing *whom* to retire or
    target lives in the caller / :mod:`.target`.
    """

    def __init__(self, *, K: int = DEFAULT_K) -> None:
        if K < 1:
            raise ValueError(f"pool capacity K must be >= 1, got {K}")
        self.K = K
        self.variants: dict[str, Variant] = {}
        self.next_id: int = 0

    # ------------------------------------------------------------------
    # construction
    # ------------------------------------------------------------------

    def add_root(
        self,
        config_path: str | Path,
        journal_path: str | Path,
        tasks: Iterable[str] | None = None,
    ) -> Variant:
        """Create ``V0``, the root variant (``parent_id is None``).

        ``tasks`` is our extension of the SPEC signature: when the caller knows
        the evaluation set up front, V0 starts out carrying *all* of it
        (SPEC §2.1 "routed_tasks = 全部"). When omitted, ``routed_tasks``
        starts empty and is filled in by routing / :meth:`reassign`.
        """
        if self.variants:
            raise RuntimeError("add_root() on a non-empty pool; the root variant already exists")
        variant = Variant(
            variant_id=f"V{self.next_id}",
            config_path=Path(config_path),
            journal_path=Path(journal_path),
            created_round=0,
            parent_id=None,
            routed_tasks=set(tasks) if tasks is not None else set(),
        )
        self.variants[variant.variant_id] = variant
        self.next_id += 1
        return variant

    def fork(self, parent_id: str, improved_tasks: Iterable[str], at_round: int) -> Variant:
        """Fork a new variant off ``parent_id`` (§4.5 p.11, gate decision FORK).

        The paper's rule is only "the system forks a new variant rather than
        rejecting the edit outright (retiring the lowest-performing variant if
        the pool is full)". Everything else is ours:

        * **task inheritance** — ``improved_tasks`` move off the parent and
          become the child's ``routed_tasks``. The tasks the candidate improved
          are exactly the ones the new variant exists to serve; leaving them on
          the parent too would double-route them.
        * **config / journal** — the child gets its own paths, cloned from the
          parent when the files exist (inherit-then-diverge). A separate
          journal file per variant is what isolates the novelty check (W9).
        * **slots (W23)** — every slot the parent *owns* is deep-copied so the
          child can diverge in the tools dimension; slots the parent shares
          (``None``) stay shared.

        A full pool is **not** silently trimmed here: retiring is a ranked
        decision that needs the ledger, so the caller must
        :meth:`retire` first. This keeps the pool free of success-rate logic.
        """
        parent = self.variants.get(parent_id)
        if parent is None:
            raise KeyError(f"cannot fork unknown variant {parent_id!r}")
        if self.is_full():
            raise RuntimeError(
                f"pool is full (K={self.K}); retire the lowest-performing variant before forking"
            )

        new_id = f"V{self.next_id}"
        child_config = parent.config_path.with_name(f"{new_id}{parent.config_path.suffix}")
        child_journal = parent.journal_path.parent / f"learnings_{new_id}{parent.journal_path.suffix or '.md'}"

        if parent.config_path.exists():
            child_config.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(parent.config_path, child_config)
        if parent.journal_path.exists():
            child_journal.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(parent.journal_path, child_journal)

        child_slots: dict[str, Path | None] = {}
        for slot_name, slot_path in parent.slot_dirs.items():
            if slot_path is None:
                # parent shares this slot with *its* parent -> keep sharing (lazy copy)
                child_slots[slot_name] = None
                continue
            source = Path(slot_path)
            dest = source.parent / f"{new_id}_{slot_name}"
            if dest.exists():
                raise RuntimeError(f"slot clone target already exists: {dest}")
            if source.exists():
                shutil.copytree(source, dest)
            child_slots[slot_name] = dest

        improved = set(improved_tasks)
        child = Variant(
            variant_id=new_id,
            config_path=child_config,
            journal_path=child_journal,
            created_round=at_round,
            parent_id=parent_id,
            routed_tasks=improved,
            slot_dirs=child_slots,
        )
        # inheritance: the improved tasks transfer off the parent
        parent.routed_tasks -= improved

        self.variants[new_id] = child
        self.next_id += 1
        return child

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------

    def is_full(self) -> bool:
        """``V_t >= K`` — no room for another variant (§4.5 p.11)."""
        return len(self.variants) >= self.K

    def retire(self, variant_id: str) -> set[str]:
        """W6 — drop a variant, returning the tasks it carried (now orphans).

        The caller picks *whom* to retire (the paper says "lowest-performing",
        our default metric is ``variant_rollup``; SPEC §6.6). The returned set
        must be fed to :meth:`reassign` or those tasks stop being evaluated.

        Ledger cells for the retired variant are intentionally left in place:
        they are historical fact, they can never be routed to again, and
        ``ever_solved`` must keep them for the full-history seesaw baseline
        (W21). Slot directories on disk are likewise left for the caller to
        garbage-collect.
        """
        if variant_id not in self.variants:
            raise KeyError(f"cannot retire unknown variant {variant_id!r}")
        if len(self.variants) <= 1:
            raise RuntimeError("cannot retire the last remaining variant; the pool would be empty")
        retired = self.variants.pop(variant_id)
        return set(retired.routed_tasks)

    def reassign(
        self,
        orphan_tasks: Iterable[str],
        router: Router,
        ledger: SuccessLedger | None = None,
        *,
        before_round: int | None = None,
    ) -> dict[str, str]:
        """Re-route the orphans left behind by :meth:`retire`.

        ``ledger``/``before_round`` extend the SPEC signature: with them the
        orphans are re-routed by the normal argmax rule *under the routing
        freeze* (only rounds ``< before_round`` are readable), which is what the
        engine wants mid-round. Without them we fall back to the cold-start
        rule, since a bare ``(tasks, router)`` call has no success data to rank
        with. Returns the task -> variant mapping it applied.
        """
        if not self.variants:
            raise RuntimeError("cannot reassign into an empty pool")
        mapping: dict[str, str] = {}
        for task_id in orphan_tasks:
            if ledger is not None and before_round is not None:
                target = router.route(task_id, self, ledger, before_round=before_round)
            else:
                target = router.cold_start(task_id, self, ledger)
            self.variants[target].routed_tasks.add(task_id)
            mapping[task_id] = target
        return mapping

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    def carrier_of(self, task_id: str) -> str | None:
        """Variant currently carrying ``task_id``, or ``None``.

        Deterministic (lowest id) if the invariant "one carrier per task" is
        ever violated by a caller.
        """
        carriers = sorted(vid for vid, v in self.variants.items() if task_id in v.routed_tasks)
        return carriers[0] if carriers else None

    def apply_routing(self, mapping: dict[str, str]) -> None:
        """Adopt a frozen routing map as the new ``routed_tasks`` partition.

        Not in the SPEC signature list, but the engine (batch C) needs a single
        place where a frozen map from :meth:`.Router.freeze_routing` becomes the
        pool's own partition, and keeping it here stops that write from leaking
        into the (deliberately read-only) freeze step.
        """
        unknown = sorted(set(mapping.values()) - set(self.variants))
        if unknown:
            raise KeyError(f"routing map references unknown variants: {unknown}")
        routed = set(mapping)
        for variant in self.variants.values():
            variant.routed_tasks -= routed
        for task_id, variant_id in mapping.items():
            self.variants[variant_id].routed_tasks.add(task_id)

    def __len__(self) -> int:
        return len(self.variants)
