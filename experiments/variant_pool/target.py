# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""W14 — which variant should the next candidate target?

Once a pool exists, every candidate must name a target variant: the seesaw is
narrowed to that variant's tasks ("a candidate targeting variant k is tested
only against tasks routed to k", §4.5 p.11) and the manifest needs a
``target_variant`` field the paper's schema does not have (report §3.3).

The paper says only "a candidate targeting variant k" and never says how *k* is
chosen (report §5, gap 6). Everything here is therefore ours, and the strategy
in force is an explicit experiment choice recorded in the run lock
(SPEC §6.3/§6.6).

Default ``worst_first``: aim the round's edit at the weakest variant, the one
with the most headroom. The alternatives exist as ablation arms — the choice
interacts with pool dynamics (always improving the strongest variant would
widen the gap and starve the rest, always improving the weakest spreads effort
thin), and this module is where that arm gets switched.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only, no runtime import cycle
    from collections.abc import Collection

    from .ledger import SuccessLedger
    from .pool import VariantPool

#: Selection strategies. ``worst_first`` is the default arm; the other two are
#: ablations (SPEC §6.6).
STRATEGIES = ("worst_first", "round_robin", "failure_density")


def select_target_variant(
    pool: VariantPool,
    ledger: SuccessLedger,
    *,
    strategy: str = "worst_first",
    round_idx: int | None = None,
    eligible: Collection[str] | None = None,
) -> str:
    """Pick the variant the next candidate should target.

    ``worst_first`` (default)
        Lowest :meth:`SuccessLedger.variant_rollup`. A freshly forked variant
        with no history scores the stale prior, so it is neither singled out
        nor protected.
    ``round_robin``
        Cycle through the variants by id. Genuinely needs a clock, so
        ``round_idx`` is required — it extends the SPEC signature rather than
        hiding module-level state, which would make selection irreproducible.
    ``failure_density``
        The variant carrying the most unsolved routed tasks. Read as absolute
        failure *mass*, not failures per routed task: a rate would let a
        variant carrying one failing task outrank one carrying twenty, which is
        the opposite of "where is there most to fix".

    All three break ties on the lowest variant id, so selection is
    reproducible across runs.

    ``eligible`` (optional) restricts every strategy to a caller-supplied set of
    variant ids — for example the variants that hold settled trajectories and
    can therefore actually be evolved this round. ``None`` (default) considers
    the whole pool and is byte-identical to the historical behaviour. When a set
    is given but *no* pool variant is in it, selection falls back to the whole
    pool rather than raising, so the result stays deterministic and total and
    the caller's own no-trajectories guard handles the starve case (today's
    outcome, no new failure mode).
    """
    if strategy not in STRATEGIES:
        raise ValueError(f"strategy must be one of {STRATEGIES}, got {strategy!r}")
    variant_ids = sorted(pool.variants)
    if not variant_ids:
        raise RuntimeError("cannot select a target variant from an empty pool")

    if eligible is not None:
        filtered = [variant_id for variant_id in variant_ids if variant_id in eligible]
        # Empty -> keep the unfiltered pool (fall-back ruling above). A
        # non-empty subset preserves the sorted order, so tie-breaks on the
        # lowest id are unchanged.
        if filtered:
            variant_ids = filtered

    if strategy == "worst_first":
        # min() keeps the first minimum, and variant_ids is sorted -> lowest id
        return min(variant_ids, key=ledger.variant_rollup)

    if strategy == "round_robin":
        if round_idx is None:
            raise ValueError("round_robin requires round_idx")
        return variant_ids[round_idx % len(variant_ids)]

    return max(variant_ids, key=lambda vid: _unsolved_routed(vid, pool, ledger))


def _unsolved_routed(variant_id: str, pool: VariantPool, ledger: SuccessLedger) -> int:
    """Routed tasks this variant has never got a passing rollout on.

    A task it has never attempted counts as unsolved: from the targeting point
    of view untried and failing are both "no working answer yet".
    """
    variant = pool.variants[variant_id]
    unsolved = 0
    for task_id in variant.routed_tasks:
        cell = ledger.cell(variant_id, task_id)
        if cell is None or cell.passes == 0:
            unsolved += 1
    return unsolved
