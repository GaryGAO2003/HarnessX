"""Runtime-only processor registration — immutable, config-owned.

Registration records isolate registration metadata (hook bucket / order /
singleton_group / after) from the shared processor instances they wrap.
``HarnessConfig`` keeps a single canonical sequence of these records
(``_processor_regs``, L2.3c); this module only imports from
``harnessx.core.processor`` (lazily, to avoid a cycle) and is imported by
builder / harness / graph — never the other way around.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any, Callable, Iterable


class HarnessConflictError(Exception):
    """Configuration conflict (``_after`` crossing orders / same-order cycle).

    Migrated from builder.py:57 (block 5); builder re-exports it so
    ``from harnessx.core.builder import HarnessConflictError`` keeps working.

    ``error.conflicts`` is the full list of conflict descriptions so callers
    can programmatically inspect or log them.
    """

    def __init__(self, conflicts: list[str]) -> None:
        self.conflicts = list(conflicts)
        lines = "\n".join(f"  [{i + 1}] {c}" for i, c in enumerate(conflicts))
        super().__init__(f"{len(conflicts)} conflict(s) detected:\n{lines}")


@dataclass(frozen=True)
class RuntimeReg:
    """Immutable runtime registration record — never mutates the processor.

    Carries only registration-time metadata (hook bucket / order /
    singleton_group / after).  Instance state binding (``_bind_*``) happens
    at Harness construction, governed by the single-owner rule (L2.3b).
    """

    proc: Any
    hook: str = "*"
    order: int = 0
    singleton_group: str | None = None
    after: tuple[str, ...] = ()


@dataclass(frozen=True)
class SerializedReg:
    """Registration view over a serialized ``_target_`` dict.

    Presence/value are **dynamic properties** over ``dict_ref`` — nothing is
    cached, because the dict is mutable and externally shared (the
    ``config.processors`` view exposes the same dicts); caching would let
    graph read new values while runtime reads stale ones (P2 key presence):

    - ``*_present=True`` with ``""`` / ``0`` / ``[]`` → explicit empty,
      preserved as-is (``_hook_=""`` routes to an empty bucket that never
      executes — VM14: 0 graph edges and no runtime execution, consistent).
    - ``*_present=False`` (key missing) → fall back to natural metadata after
      instantiation (``natural = coerce_runtime_reg(inst)``, L2.3c rule 4).
    """

    dict_ref: dict

    @property
    def hook(self) -> str:
        v = self.dict_ref["_hook_"] if "_hook_" in self.dict_ref else None
        # Non-str (incl. None) → explicit empty (empty bucket, never executes) —
        # never a str(None)="None" bucket; builder writes str (L2.2), hand-written
        # YAML of another type degrades safely (consistent with L3.1 filtering).
        return v if isinstance(v, str) else ""

    @property
    def hook_present(self) -> bool:
        return "_hook_" in self.dict_ref

    @property
    def order(self) -> int:
        v = self.dict_ref["_order_"] if "_order_" in self.dict_ref else None
        try:
            return int(v)  # int / numeric string; int(None) raises → except
        except (TypeError, ValueError):
            return 0  # unparseable → default 0 (class default; no crash)

    @property
    def order_present(self) -> bool:
        return "_order_" in self.dict_ref

    @property
    def singleton_group(self) -> "str | None":
        v = self.dict_ref["_singleton_group_"] if "_singleton_group_" in self.dict_ref else None
        return v if isinstance(v, str) else None  # non-str (incl. None) → missing semantics

    @property
    def sg_present(self) -> bool:
        return "_singleton_group_" in self.dict_ref

    @property
    def after(self) -> "tuple[str, ...]":
        v = self.dict_ref["_after_"] if "_after_" in self.dict_ref else None
        return tuple(v) if isinstance(v, (list, tuple)) else ()
        # Only list/tuple: None / empty / non-iterables (int/str) → ().
        # A bare `tuple(v) if v else ()` has two hazards: non-empty non-iterable
        # (_after_: 7) is truthy → tuple(7) raises TypeError; str (_after_: "ab")
        # → char-split ("a","b").  The isinstance guard kills both.

    @property
    def after_present(self) -> bool:
        return "_after_" in self.dict_ref


@dataclass(frozen=True)
class RoutingEnvelope:
    """Routing envelope — global registration seq + ordering metadata.

    - ``seq``: global registration index (``config._processor_regs`` subscript)
      — stable tiebreak.
    - In-bucket ordering reuses the Builder rule: order first, then ``_after_``
      topo within the same order, seq stable tiebreak (hand-written configs
      cannot assume ``_after_`` is encoded in registration order).
    """

    reg: RuntimeReg
    seq: int


def coerce_runtime_reg(x) -> "RuntimeReg | None":
    """Normalize a registration input: RuntimeReg → itself; dict / None → None;
    bare processor → record.

    A bare instance's hook is its natural bucket (``get_graph_metadata``'s
    ``_hook_`` = nearest non-empty class ``_hook``; MHP without one is ``"*"``),
    and a bare instance without ``_hook`` defaults to the ``"*"`` bucket
    (matches current runloop behaviour — harness.py:391 ``_route_processors``
    third branch).  order/sg/after come from class defaults.  This is the only
    entry point that keeps directly-injected bare processors working.
    """
    if isinstance(x, RuntimeReg):
        return x
    if isinstance(x, dict) or x is None:
        # None and dict are both "not coercible" → None (normalize raises
        # ValueError on None).  Without the guard, None falls into the bare
        # branch: L1.4 `_read` already uses getattr(..., "__dict__", {}) for
        # __slots__ safety, so it no longer raises on None — it falls back to
        # class attrs and silently wraps RuntimeReg(proc=None, hook="*", ...),
        # violating normalize's "None → ValueError" contract.
        return None
    from .processor import get_graph_metadata  # lazy: processor never imports runtime

    meta = get_graph_metadata(x)
    return RuntimeReg(
        proc=x,
        hook=meta["_hook_"] or "*",  # empty bucket → "*" (bare non-MHP runs on all 8 hooks)
        order=meta["_order_"],
        singleton_group=meta["_singleton_group_"] or None,
        after=tuple(meta["_after_"]),
    )


def unwrap_runtime_proc(x):
    """Read-side consumer entry: RuntimeReg → .proc; bare processor → itself."""
    return x.proc if isinstance(x, RuntimeReg) else x


def normalize_processor_reg(x) -> "SerializedReg | RuntimeReg":
    """Single canonicalization entry — accepts SerializedReg | RuntimeReg |
    dict | bare processor.

    Every write entry (``__post_init__``, ``add_runtime_reg``,
    ``replace_processor_regs``, ``copy(processors=...)`` override, spawn mixed
    list) goes through it (round 8, item 32):

    - SerializedReg → itself (NOT via coerce — coerce would wrap the record as
      a processor, the mis-wrap fix);
    - dict → ``SerializedReg(dict_ref=x)`` (presence dynamic property);
    - RuntimeReg → itself;
    - None / blacklist builtins (str/int/list/...) → ValueError (no silent loss
      of registrations); **any other object → bare-instance compatibility**
      (coerce wrap — an injection-compat boundary: not every type with
      ``__dict__`` can be enumerated; slice/memoryview/functions/modules fall
      into this path).
    """
    if x is None:
        raise ValueError("normalize_processor_reg: 不接受 None（防静默丢注册项）")
    if isinstance(x, (str, int, float, bytes, bytearray, list, tuple, set,
                      frozenset, complex, range, type)):
        # Builtin scalars/containers/class objects are rejected outright (bool
        # is an int subclass, already covered) → ValueError.  Not routed through
        # coerce: get_graph_metadata(str) would hit str.__dict__ in L1.4 `_read`
        # and raise AttributeError, contradicting the "→ ValueError" promise.
        # Contract boundary: this blacklist is explicit, not exhaustive (unlisted
        # types with __dict__ fall into bare-instance compat — a design boundary,
        # not something to enumerate further).
        raise ValueError(
            f"normalize_processor_reg: {type(x).__qualname__} 不是注册项"
            f"（只接受 SerializedReg / RuntimeReg / dict / processor 实例）"
        )
    if isinstance(x, SerializedReg) or isinstance(x, RuntimeReg):
        return x
    if isinstance(x, dict):
        return SerializedReg(dict_ref=x)
    reg = coerce_runtime_reg(x)
    if reg is None:
        raise ValueError(
            f"normalize_processor_reg: 无法将 {type(x).__qualname__} 规范化为注册项"
        )
    return reg


def stable_topological_sort(
    items: "Iterable[Any]",
    *,
    order_key: "Callable[[Any], int]",
    after_key: "Callable[[Any], Iterable[str]]",
    group_key: "Callable[[Any], str]",
    seq_key: "Callable[[Any], int]",
) -> list:
    """Generalised stable topological sort — full semantics of builder.py:678.

    Adapts ``_ProcEntry`` and ``RoutingEnvelope`` (the graph's EXECUTES_BEFORE
    edge generation uses the same function, L4.6 / L5.6):

    1. ``group_map``: {group_key(e): e for e in items if group_key(e)} —
       ``after`` deps resolve by singleton_group name (group_key provides each
       entry's own group; ungrouped entries cannot be referenced).
    2. **Cross-order conflict**: e's after target t with order_key(t) >
       order_key(e) → HarnessConflictError (constraint can never be satisfied).
    3. Group by order_key ascending; within a group, Kahn topological sort with
       the queue initialised and drained in seq_key order → stable FIFO.
    4. In-group cycle → HarnessConflictError (lists the cycle members).
    5. **Soft deps**: after refs to unregistered groups are silently ignored
       (optional ordering — must not couple to possibly-absent plugins).
    """
    from collections import defaultdict, deque

    items = list(items)
    group_map: dict = {group_key(e): e for e in items if group_key(e)}

    conflicts: list[str] = []
    for entry in items:
        for after_group in after_key(entry):
            target = group_map.get(after_group)
            if target is None:
                continue
            if order_key(target) > order_key(entry):
                conflicts.append(
                    f"{group_key(entry) or type(entry).__name__} (order={order_key(entry)}) "
                    f"declares after=['{after_group}'] but target has order="
                    f"{order_key(target)} — contradictory: target runs later"
                )
    if conflicts:
        raise HarnessConflictError(conflicts)

    order_buckets: dict[int, list] = {}
    for entry in items:
        order_buckets.setdefault(order_key(entry), []).append(entry)

    result: list = []
    for order_val in sorted(order_buckets):
        bucket = order_buckets[order_val]
        n = len(bucket)
        if n == 1:
            result.append(bucket[0])
            continue

        entry_to_idx = {id(e): i for i, e in enumerate(bucket)}
        adj: dict = defaultdict(list)
        in_deg = [0] * n

        for j, entry in enumerate(bucket):
            for after_group in after_key(entry):
                target = group_map.get(after_group)
                if target is None or id(target) not in entry_to_idx:
                    continue  # not in this bucket; cross-order handles it
                i = entry_to_idx[id(target)]
                adj[i].append(j)
                in_deg[j] += 1

        # Stable FIFO: the queue is always kept in seq_key order.  A node
        # becomes ready exactly once (its last predecessor processed), so we
        # simply insert newly-ready nodes into the sorted queue on the fly.
        import bisect

        ready_seq = sorted(
            (seq_key(bucket[i]), i) for i in range(n) if in_deg[i] == 0
        )
        queue = deque(i for _, i in ready_seq)
        sorted_bucket: list = []
        while queue:
            i = queue.popleft()
            sorted_bucket.append(bucket[i])
            for j in adj[i]:
                in_deg[j] -= 1
                if in_deg[j] == 0:
                    # insert j into the seq-ordered queue
                    s = seq_key(bucket[j])
                    seqs = [seq_key(bucket[k]) for k in queue]
                    pos = bisect.bisect_left(seqs, s)
                    queue.insert(pos, j)

        if len(sorted_bucket) != n:
            cycle_members = [
                getattr(bucket[i], "singleton_group", None) or type(bucket[i]).__name__
                for i in range(n) if in_deg[i] > 0
            ]
            raise HarnessConflictError(
                [f"Cycle in after dependencies at order={order_val}: {', '.join(cycle_members)}"]
            )

        result.extend(sorted_bucket)

    return result


# ── owner registry（身份去重，不依赖 hash/eq；强引用防 id 复用）─────────────

_OWNER_LOCK = threading.Lock()
_OWNERS: "dict[int, tuple[Any, object]]" = {}  # id(proc) -> (strong_ref, token)


def claim_owners(procs: "Iterable[Any]", token: object) -> None:
    """Two-phase, lock-protected: full check → full claim.

    - Identity dedup by ``id`` (``setdefault``) — never hash/eq; unhashable
      processors are safe.
    - Any instance already owned by another token → ValueError, and NO instance
      is written (atomic).
    - Strong refs stored in the registry → an id cannot be reused while claimed.
    """
    unique: "dict[int, Any]" = {}
    for p in procs:
        unique.setdefault(id(p), p)
    with _OWNER_LOCK:
        for pid, p in unique.items():
            existing = _OWNERS.get(pid)
            if existing is not None and existing[1] is not token:
                raise ValueError(
                    f"runtime processor {type(p).__qualname__} 已绑定到另一个 Harness；"
                    f"runtime-only 处理器单 owner，禁止跨 config 复用。"
                    f"并行复用请改用 factory/clone 生成新实例"
                )
        for pid, p in unique.items():
            _OWNERS[pid] = (p, token)


def release_owners(token: object) -> None:
    """Release all claims for this token (after full cleanup / constructor rollback).

    Idempotent: safe no-op when the token has no claims.  Released instances
    can be claimed again by a new Harness.
    """
    with _OWNER_LOCK:
        for pid in [k for k, (_, t) in _OWNERS.items() if t is token]:
            del _OWNERS[pid]
