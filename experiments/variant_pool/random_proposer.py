"""R-arm proposer — B-arm's action space + gate, LLM swapped for pure chance.

The R (random) arm is the control for the B (brain / LLM) arm: it emits the
*same* typed-operator vocabulary against the *same* parent snapshot and feeds
the *same* S0–S4 + Δ8 hard gate — the only thing removed is the intelligence.
Every proposal is a uniformly-sampled, **formally** legal edit; semantic
legality is deliberately NOT enforced here (that is the gate's job), so the R
arm reproduces the "most candidates die at the gate" regime by construction.
Comparing R-arm and B-arm gate-survival / evaluation curves isolates how much
of the benefit is the typed action space + verification substrate versus the
LLM's judgement.

The output is interchangeable with :func:`experiments.variant_pool.proposer`'s
LLM path: this module returns the same
:class:`~experiments.variant_pool.proposer.ExtractionResult` (a ``proposals``
list plus ``error``), so the runner swaps a single callable to switch arms.
Each proposal is the exact shape ``parse_proposal`` expects —
``{"operator": <snake_case>, "params": {...}, "rationale": "..."}`` — with an
auto-generated rationale that records the random source and the target node.

Determinism contract: a given ``rng`` seed plus a given snapshot yields a
byte-identical proposal sequence.  All snapshot scans are ``sorted()`` (node
ids, ctor-kwarg keys, singleton groups, hook pool) so nothing rides on dict
iteration order, and every stochastic choice flows through the injected
``random.Random``.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Any

from harnessx.core.processor import PROCESSOR_HOOK_NAMES
from harnessx.graph.types import GraphSnapshot, NodeType

# The R arm's output contract IS the kernel's input contract — bind to the same
# operator vocabulary the gate enforces, and reuse the B arm's result type so
# the two proposers are drop-in interchangeable (read-only imports).
from experiments.variant_pool.proposer import ExtractionResult
from experiments.variant_pool.shadow_evolution import PROPOSAL_OPERATORS

__all__ = ["random_proposer", "INSERT_TARGET_POOL"]


#: Real, importable processor targets for ``insert_processor`` /
#: ``replace_same_singleton_group`` specs.  Every entry is a class that exists
#: in the main library and carries a WELL_KNOWN_DECLARATION (hook / order /
#: singleton_group), so an inserted node is a plausible processor rather than a
#: phantom target — the gate still decides whether the concrete edit survives.
INSERT_TARGET_POOL: "tuple[str, ...]" = (
    "harnessx.processors.control.cost_guard.CostGuardProcessor",
    "harnessx.processors.control.loop_detection.LoopDetectionProcessor",
    "harnessx.processors.control.token_budget.TokenBudgetProcessor",
    "harnessx.processors.control.step_countdown.StepCountdownProcessor",
    "harnessx.processors.observability.checkpoint.CheckpointProcessor",
)

#: ``_order_`` samples for rewire_ordering / insert_processor.
_ORDER_CHOICES: "tuple[int, ...]" = (0, 10, 25, 50, 75, 100)

#: Probability that rewire_ordering also references an existing singleton group.
_AFTER_PROBABILITY = 0.3

#: Hook pool for insert specs — sorted for cross-run determinism regardless of
#: the source container's own ordering.
_INSERT_HOOKS: "tuple[str, ...]" = tuple(sorted(PROCESSOR_HOOK_NAMES))


# ── snapshot scan (sorted → deterministic) ───────────────────────────────────


@dataclass(frozen=True)
class _SnapshotView:
    """Sorted, rng-free projection of the raw material each operator samples."""

    proc_ids: "tuple[str, ...]"                    # persistent PROCESSOR node ids
    sg_pairs: "tuple[tuple[str, str], ...]"        # (node_id, singleton_group)
    groups: "tuple[str, ...]"                      # distinct singleton groups
    bundle_ids: "tuple[str, ...]"                  # persistent BUNDLE node ids


def _scan(snapshot: GraphSnapshot) -> _SnapshotView:
    """Project a snapshot into sorted, rng-free sampling material.

    Only ``snapshot.nodes`` is scanned (persistent, editable genotype nodes);
    the runtime overlay (``rt:`` nodes) is never a mutation input, matching the
    operator kernel's own contract.
    """
    proc_ids: "list[str]" = []
    sg_pairs: "list[tuple[str, str]]" = []
    bundle_ids: "list[str]" = []
    for node_id, node in snapshot.nodes.items():
        if node.node_type is NodeType.PROCESSOR:
            proc_ids.append(node_id)
            sg = node.metadata.get("_singleton_group_")
            if isinstance(sg, str) and sg:
                sg_pairs.append((node_id, sg))
        elif node.node_type is NodeType.BUNDLE:
            bundle_ids.append(node_id)
    return _SnapshotView(
        proc_ids=tuple(sorted(proc_ids)),
        sg_pairs=tuple(sorted(sg_pairs)),
        groups=tuple(sorted({sg for _, sg in sg_pairs})),
        bundle_ids=tuple(sorted(bundle_ids)),
    )


def _perturb_value(val: Any, rng: random.Random) -> Any:
    """Type-aware random perturbation of one ctor-kwarg value.

    bool → flip; int → ±(1..10); float → ×(0.5..2); str → suffix; anything else
    → a fresh random int.  bool is checked before int (bool ⊂ int in Python).
    """
    if isinstance(val, bool):
        return not val
    if isinstance(val, int):
        return val + rng.randint(1, 10) * rng.choice((-1, 1))
    if isinstance(val, float):
        return val * rng.uniform(0.5, 2.0)
    if isinstance(val, str):
        return f"{val}_r{rng.randint(0, 9999)}"
    return rng.randint(0, 1000)


# ── per-operator param samplers (formal legality only) ───────────────────────


def _sample_mutate(snapshot: GraphSnapshot, rng: random.Random,
                   view: _SnapshotView) -> "tuple[dict, str]":
    node_id = rng.choice(view.proc_ids)
    ctor = snapshot.nodes[node_id].metadata.get("_ctor_kwargs_")
    if isinstance(ctor, dict) and ctor:
        key = rng.choice(sorted(ctor))
        param_changes = {key: _perturb_value(ctor[key], rng)}
        source = f"perturb ctor kwarg {key!r}"
    else:
        # No ctor kwargs on the node: inject a harmless synthetic key and let
        # the gate decide its fate (the R arm never pre-filters).
        param_changes = {"rnd_param": rng.randint(0, 1000)}
        source = "inject synthetic ctor kwarg"
    return ({"node_id": node_id, "param_changes": param_changes},
            f"random[mutate_processor_params] {source} on {node_id}")


def _sample_insert(snapshot: GraphSnapshot, rng: random.Random,
                   view: _SnapshotView) -> "tuple[dict, str]":
    target = rng.choice(INSERT_TARGET_POOL)
    hook = rng.choice(_INSERT_HOOKS)
    order = rng.choice(_ORDER_CHOICES)
    spec = {"_target_": target, "_hook_": hook, "_order_": order}
    return ({"spec": spec},
            f"random[insert_processor] insert {target} at hook:{hook}")


def _sample_remove(snapshot: GraphSnapshot, rng: random.Random,
                   view: _SnapshotView) -> "tuple[dict, str]":
    node_id = rng.choice(view.proc_ids)
    return ({"node_id": node_id},
            f"random[remove_processor] drop {node_id}")


def _sample_replace(snapshot: GraphSnapshot, rng: random.Random,
                    view: _SnapshotView) -> "tuple[dict, str]":
    node_id, group = rng.choice(view.sg_pairs)
    target = rng.choice(INSERT_TARGET_POOL)
    new_spec = {"_target_": target, "_singleton_group_": group}
    return ({"node_id": node_id, "new_spec": new_spec},
            f"random[replace_same_singleton_group] swap {node_id} in group "
            f"{group!r} -> {target}")


def _sample_rewire(snapshot: GraphSnapshot, rng: random.Random,
                   view: _SnapshotView) -> "tuple[dict, str]":
    node_id = rng.choice(view.proc_ids)
    order = rng.choice(_ORDER_CHOICES)
    params: dict = {"node_id": node_id, "order": order}
    rationale = f"random[rewire_ordering] set order={order} on {node_id}"
    if view.groups and rng.random() < _AFTER_PROBABILITY:
        after_group = rng.choice(view.groups)
        params["after"] = [after_group]
        rationale += f" after group {after_group!r}"
    return params, rationale


def _sample_swap(snapshot: GraphSnapshot, rng: random.Random,
                 view: _SnapshotView) -> "tuple[dict, str]":
    bundle_id = rng.choice(view.bundle_ids)
    sig = snapshot.nodes[bundle_id].metadata.get("interface_signature", "")
    replacement = f"bundle:rnd_{rng.randint(0, 9999)}"
    return ({"bundle_node_id": bundle_id,
             "replacement_bundle_id": replacement,
             "replacement_signature": sig if isinstance(sig, str) else ""},
            f"random[swap_bundle] swap {bundle_id} -> {replacement}")


#: Snake_case operator name → param sampler.  Keys must stay in lockstep with
#: ``PROPOSAL_OPERATORS`` (asserted by the R-arm test suite).
_SAMPLERS: "dict[str, Any]" = {
    "mutate_processor_params": _sample_mutate,
    "insert_processor": _sample_insert,
    "remove_processor": _sample_remove,
    "replace_same_singleton_group": _sample_replace,
    "rewire_ordering": _sample_rewire,
    "swap_bundle": _sample_swap,
}


def _is_available(name: str, view: _SnapshotView) -> bool:
    """Whether ``name`` has the raw material to form a formally-legal proposal.

    ``insert_processor`` is always available (constant target pool); the others
    need a matching node.  Generalizes the plan's swap_bundle rule ("no BUNDLE
    node → drop it from the sampling space, resample the rest") to every
    operator so an empty projection can never mint a malformed proposal.
    """
    if name == "insert_processor":
        return True
    if name == "swap_bundle":
        return bool(view.bundle_ids)
    if name == "replace_same_singleton_group":
        return bool(view.sg_pairs)
    return bool(view.proc_ids)  # mutate / remove / rewire


def _available_operators(view: _SnapshotView) -> "list[str]":
    """Canonical-order operator space for this snapshot (single source order)."""
    return [name for name in PROPOSAL_OPERATORS
            if name in _SAMPLERS and _is_available(name, view)]


# ── entry point (interchangeable with llm_propose) ───────────────────────────


def random_proposer(
    snapshot: GraphSnapshot,
    rng: random.Random,
    *,
    max_candidates: int = 4,
) -> ExtractionResult:
    """Emit uniformly-random, formally-legal typed edits against ``snapshot``.

    Draws ``n = rng.randint(1, max_candidates)`` proposals; each independently
    picks one operator uniformly from the snapshot's available space (swap_bundle
    dropped when there is no BUNDLE node, etc.) and samples its params from the
    sorted snapshot projection.  Only *formal* legality is guaranteed — semantic
    legality is left entirely to the S0–S4 + Δ8 gate downstream, so ``error`` is
    always ``""`` (a random draw can never fail to parse).  Feed
    ``result.proposals`` straight to ``run_shadow_round``, exactly as with the
    B arm's ``llm_propose``.
    """
    view = _scan(snapshot)
    ops = _available_operators(view)
    n = rng.randint(1, max_candidates)
    proposals: "list[dict]" = []
    if ops:
        for _ in range(n):
            name = rng.choice(ops)
            params, rationale = _SAMPLERS[name](snapshot, rng, view)
            proposals.append(
                {"operator": name, "params": params, "rationale": rationale})
    return ExtractionResult(proposals=proposals, error="")
