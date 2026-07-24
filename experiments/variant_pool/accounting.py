# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""W30 — cost accounting, split by role and priced from tokens.

SPEC §6.8 (Codex critique 10). Two facts make this module necessary.

**The repo's dollar figure is wrong for our provider.** ``runloop.py:947-949``
estimates cost as ``input * $3/M + output * $15/M`` — Claude Sonnet's price,
hard-coded, with no provider branch, and it is called unconditionally at
``:452``. Under DeepSeek V4-flash that overstates the bill by roughly 27x, so
``cost_usd`` / ``round_cost`` are unusable here and nothing in this module reads
them. Everything is priced from **token counts** (checklist §5).

**The two loops must be billed apart.** The task agent and the meta agent
(Planner/Evolver/Critic, the AEGIS side) run different models at different
prices, and SPEC §6.8 notes the Digester alone swallows ~10M raw trace tokens
per round — the M1 cost driver is the outer loop, not pass@2. A single total
cannot tell you which one to cut, so :class:`AttemptCost` carries ``role`` and
:meth:`CostLedger.by_role` never merges them.

The cache term
--------------
The M0 budget range ($130-310, checklist §6) is dominated by one number: the
context-cache hit rate. DeepSeek V4 reads a cache hit at 1/50 of a miss
(flash: $0.0028 vs $0.14 per M) and charges nothing to write, so a run at 80%
hit rate costs a third of the same run at 0%. :meth:`CostLedger.cache_hit_rate`
is that number and :meth:`CostLedger.project` reports the no-cache upper bound
next to the point estimate, because a projection that assumes the measured hit
rate holds at 100x the scale is a projection with a hidden premise.

⚠️ **``input_tokens`` means cache *misses* only.** SPEC §6.8 defines the hit
rate as ``cached / (input + cached)``, so the two fields must not overlap.
Providers report the opposite convention — OpenAI's ``prompt_tokens`` and
DeepSeek's ``prompt_tokens`` both *include* the cached prefix, and the repo's
``Usage.input_tokens`` follows them. :meth:`AttemptCost.from_usage` does the
subtraction; feeding a raw ``prompt_tokens`` into ``input_tokens`` by hand would
bill the cached prefix twice, at 50x the right rate.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

#: Billing roles. Kept separate end to end (SPEC §6.8): the inner loop is the
#: task agent, the outer loop (Planner/Evolver/Critic/Digester) is the meta
#: agent.
ROLES = ("task_agent", "meta_agent")

#: One million tokens — prices are quoted per this unit throughout.
MTOK = 1_000_000


@dataclass(frozen=True)
class Pricing:
    """Per-million-token prices, cache discount included.

    ``cached_input_per_mtok`` is the cache **read** price and
    ``cache_write_per_mtok`` the cache **write** price, which DeepSeek does not
    charge for. Both are explicit rather than derived, so a provider that starts
    charging for writes needs a number changed, not a formula rewritten.
    """

    model: str
    input_per_mtok: float
    cached_input_per_mtok: float
    output_per_mtok: float
    cache_write_per_mtok: float = 0.0

    def __post_init__(self) -> None:
        for name in ("input_per_mtok", "cached_input_per_mtok", "output_per_mtok", "cache_write_per_mtok"):
            value = getattr(self, name)
            if value < 0:
                raise ValueError(f"{name} must be >= 0, got {value}")

    @property
    def cache_discount(self) -> float | None:
        """How many times cheaper a cache read is than a miss (``None`` if free)."""
        if self.cached_input_per_mtok == 0:
            return None
        return self.input_per_mtok / self.cached_input_per_mtok

    def cost(
        self,
        *,
        input_tokens: int = 0,
        cached_input_tokens: int = 0,
        output_tokens: int = 0,
        cache_write_tokens: int = 0,
    ) -> float:
        """Billed dollars for one call's tokens. ``input_tokens`` = misses only."""
        return (
            input_tokens * self.input_per_mtok
            + cached_input_tokens * self.cached_input_per_mtok
            + output_tokens * self.output_per_mtok
            + cache_write_tokens * self.cache_write_per_mtok
        ) / MTOK

    @classmethod
    def from_litellm(cls, model: str) -> Pricing:
        """Build from litellm's model cost map.

        litellm quotes per *token*; this scales to per-million and keeps the
        model string so the lock and the report can name what was priced. The
        import is local: this module stays importable in a bare stdlib
        environment, and the manual constructors below exist for exactly that
        case.
        """
        from litellm import get_model_info  # local: keeps the module dependency-light

        info = get_model_info(model)
        cached = info.get("cache_read_input_token_cost")
        if cached is None:
            cached = info.get("input_cost_per_token", 0.0)
        return cls(
            model=model,
            input_per_mtok=float(info.get("input_cost_per_token") or 0.0) * MTOK,
            cached_input_per_mtok=float(cached or 0.0) * MTOK,
            output_per_mtok=float(info.get("output_cost_per_token") or 0.0) * MTOK,
            cache_write_per_mtok=float(info.get("cache_creation_input_token_cost") or 0.0) * MTOK,
        )

    @classmethod
    def deepseek_v4_flash(cls) -> Pricing:
        """DeepSeek V4-flash: $0.14 in, $0.0028 cached (50x), $0.28 out, writes free."""
        return cls(
            model="deepseek/deepseek-v4-flash",
            input_per_mtok=0.14,
            cached_input_per_mtok=0.0028,
            output_per_mtok=0.28,
        )

    @classmethod
    def deepseek_v4_pro(cls) -> Pricing:
        """DeepSeek V4-pro: $0.435 in, $0.003625 cached (120x), $0.87 out, writes free."""
        return cls(
            model="deepseek/deepseek-v4-pro",
            input_per_mtok=0.435,
            cached_input_per_mtok=0.003625,
            output_per_mtok=0.87,
        )


#: Default M0 split: flash runs the tasks, pro runs the meta agent.
DEFAULT_PRICING: dict[str, Pricing] = {
    "task_agent": Pricing.deepseek_v4_flash(),
    "meta_agent": Pricing.deepseek_v4_pro(),
}


@dataclass
class AttemptCost:
    """What one rollout (or one meta-agent turn) consumed.

    ``input_tokens`` counts **cache misses only** — see the module docstring;
    ``cached_input_tokens`` counts the prefix served from the provider's cache.
    Their sum is the prompt the model saw.

    ``variant_id`` is ``None`` for a baseline run with no pool and for meta-agent
    work that is not aimed at one variant. ``infra_failure`` mirrors
    :class:`.reporting.TaskResult`: a failed attempt is still billed, so the
    flag excuses nothing — it only lets the report separate money spent on
    infrastructure from money spent on tasks.
    """

    task_id: str
    round_idx: int
    variant_id: str | None
    role: Literal["task_agent", "meta_agent"]
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    cache_write_tokens: int = 0
    tool_calls: int = 0
    retries: int = 0
    infra_failure: bool = False

    def __post_init__(self) -> None:
        if self.role not in ROLES:
            raise ValueError(f"role must be one of {ROLES}, got {self.role!r}")
        for name in (
            "input_tokens",
            "cached_input_tokens",
            "output_tokens",
            "cache_write_tokens",
            "tool_calls",
            "retries",
        ):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} must be >= 0, got {getattr(self, name)}")

    @property
    def prompt_tokens(self) -> int:
        """What a provider would call ``prompt_tokens``: misses plus hits."""
        return self.input_tokens + self.cached_input_tokens

    def billed(self, pricing: Pricing) -> float:
        return pricing.cost(
            input_tokens=self.input_tokens,
            cached_input_tokens=self.cached_input_tokens,
            output_tokens=self.output_tokens,
            cache_write_tokens=self.cache_write_tokens,
        )

    @classmethod
    def from_usage(
        cls,
        usage: Any,
        *,
        task_id: str,
        round_idx: int,
        role: str,
        variant_id: str | None = None,
        **extra: Any,
    ) -> AttemptCost:
        """Build from a repo ``Usage``-shaped object, splitting hits from misses.

        Duck-typed on purpose (batch A imports nothing from the repo, SPEC §5):
        anything exposing ``input_tokens`` / ``output_tokens`` /
        ``cache_read_tokens`` works. The subtraction is the whole point —
        ``Usage.input_tokens`` is the provider's ``prompt_tokens`` and *includes*
        the cached prefix, so passing it through unchanged would bill that
        prefix at the full miss rate on top of the cache read.
        """
        prompt = int(getattr(usage, "input_tokens", 0) or 0)
        cached = int(getattr(usage, "cache_read_tokens", 0) or 0)
        if cached > prompt:
            raise ValueError(f"cache_read_tokens={cached} exceeds prompt tokens={prompt}")
        return cls(
            task_id=task_id,
            round_idx=round_idx,
            variant_id=variant_id,
            role=role,  # type: ignore[arg-type]
            input_tokens=prompt - cached,
            cached_input_tokens=cached,
            output_tokens=int(getattr(usage, "output_tokens", 0) or 0),
            cache_write_tokens=int(getattr(usage, "cache_write_tokens", 0) or 0),
            **extra,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "round_idx": self.round_idx,
            "variant_id": self.variant_id,
            "role": self.role,
            "input_tokens": self.input_tokens,
            "cached_input_tokens": self.cached_input_tokens,
            "output_tokens": self.output_tokens,
            "cache_write_tokens": self.cache_write_tokens,
            "tool_calls": self.tool_calls,
            "retries": self.retries,
            "infra_failure": self.infra_failure,
        }


@dataclass
class CostLedger:
    """Per-attempt token accounting, split by role (SPEC §6.8).

    ``pricing`` may be one :class:`Pricing` for everything or a mapping from
    role to :class:`Pricing` (the M0 default: flash for tasks, pro for the meta
    agent). Every method that prices takes an override, so a projection can be
    re-run against another provider without rebuilding the ledger.
    """

    pricing: Pricing | Mapping[str, Pricing] | None = None
    costs: list[AttemptCost] = field(default_factory=list)

    # ------------------------------------------------------------------
    # writes
    # ------------------------------------------------------------------

    def record(self, cost: AttemptCost) -> None:
        self.costs.append(cost)

    def extend(self, costs: Iterable[AttemptCost]) -> None:
        for cost in costs:
            self.record(cost)

    # ------------------------------------------------------------------
    # reads
    # ------------------------------------------------------------------

    def of_role(self, role: str) -> list[AttemptCost]:
        return [cost for cost in self.costs if cost.role == role]

    def attempts(self, role: str | None = None) -> int:
        """Recorded attempts, optionally for one role."""
        return len(self.costs if role is None else self.of_role(role))

    def rounds(self, role: str | None = None) -> list[int]:
        source = self.costs if role is None else self.of_role(role)
        return sorted({cost.round_idx for cost in source})

    def totals(self, role: str | None = None) -> dict[str, Any]:
        """Token and call totals, plus the cache hit rate for the selection."""
        source = self.costs if role is None else self.of_role(role)
        return {
            "attempts": len(source),
            "input_tokens": sum(cost.input_tokens for cost in source),
            "cached_input_tokens": sum(cost.cached_input_tokens for cost in source),
            "output_tokens": sum(cost.output_tokens for cost in source),
            "cache_write_tokens": sum(cost.cache_write_tokens for cost in source),
            "tool_calls": sum(cost.tool_calls for cost in source),
            "retries": sum(cost.retries for cost in source),
            "infra_failures": sum(1 for cost in source if cost.infra_failure),
            "cache_hit_rate": self.cache_hit_rate(role),
        }

    def by_role(self) -> dict[str, dict[str, Any]]:
        """Totals per role — the split SPEC §6.8 requires.

        Every role in :data:`ROLES` appears even with no records, so a caller
        cannot mistake "the meta agent was never billed" for "the meta agent key
        is missing".
        """
        return {role: self.totals(role) for role in ROLES}

    def cache_hit_rate(self, role: str | None = None) -> float | None:
        """``cached / (input + cached)``; ``None`` when no prompt tokens exist.

        ``None``, never 0.0: a ledger with nothing in it has not measured a 0%
        hit rate, and the M0 budget is far too sensitive to this number to let
        an empty measurement look like a bad one.
        """
        source = self.costs if role is None else self.of_role(role)
        prompt = sum(cost.prompt_tokens for cost in source)
        if prompt == 0:
            return None
        return sum(cost.cached_input_tokens for cost in source) / prompt

    # ------------------------------------------------------------------
    # pricing
    # ------------------------------------------------------------------

    def total_billed(self, pricing: Pricing | Mapping[str, Pricing] | None = None) -> dict[str, float]:
        """Dollars per role plus ``total`` — priced from tokens, never from ``cost_usd``."""
        resolved = self._resolve(pricing)
        out = {role: sum(cost.billed(resolved[role]) for cost in self.of_role(role)) for role in ROLES}
        out["total"] = sum(out[role] for role in ROLES)
        return out

    def per_attempt_billed(
        self,
        pricing: Pricing | Mapping[str, Pricing] | None = None,
        *,
        role: str = "task_agent",
    ) -> float | None:
        """Mean billed dollars per recorded attempt of ``role``.

        Defaults to the task agent because that is the unit M0 scales by
        (lineages x tasks x rounds x k). ``None`` when the role recorded
        nothing. Pass ``role=None`` to average over every record, which mixes
        two price lists and is only meaningful for a single-model run.
        """
        resolved = self._resolve(pricing)
        source = self.costs if role is None else self.of_role(role)
        if not source:
            return None
        return sum(cost.billed(resolved[cost.role]) for cost in source) / len(source)

    def project(
        self,
        *,
        lineages: int,
        tasks: int,
        rounds: int,
        k: int,
        pricing: Pricing | Mapping[str, Pricing] | None = None,
    ) -> dict[str, Any]:
        """Extrapolate this sample to a full M0 run.

        The two loops scale differently and are extrapolated differently:

        * the **task agent** scales with attempts, ``lineages * tasks * rounds *
          k`` (the pass@2 evaluation of the full set every round, §6.1 p.15 /
          A.2 p.28);
        * the **meta agent** scales with ``lineages * rounds`` — one evolution
          cycle per lineage per round — so its unit is the mean cost of an
          observed *round*, not of an attempt.

        Returns the point estimate and ``total_usd_no_cache``: the same
        projection with every cached token repriced as a miss. That upper bound
        is not pessimism for its own sake — it is what the bill becomes if the
        prefix stops being stable at scale, and the M0 range ($130-310) is
        exactly this spread.

        ``pricing`` extends the SPEC signature as an optional override; without
        it the ledger's own pricing is used.
        """
        for name, value in (("lineages", lineages), ("tasks", tasks), ("rounds", rounds), ("k", k)):
            if value < 1:
                raise ValueError(f"{name} must be >= 1, got {value}")
        resolved = self._resolve(pricing)

        attempts = lineages * tasks * rounds * k
        meta_cycles = lineages * rounds

        per_attempt = self.per_attempt_billed(resolved, role="task_agent") or 0.0
        observed_meta_rounds = len(self.rounds("meta_agent"))
        meta_total = sum(cost.billed(resolved["meta_agent"]) for cost in self.of_role("meta_agent"))
        per_meta_cycle = meta_total / observed_meta_rounds if observed_meta_rounds else 0.0

        task_usd = per_attempt * attempts
        meta_usd = per_meta_cycle * meta_cycles

        no_cache = self._no_cache_ledger()
        per_attempt_nc = no_cache.per_attempt_billed(resolved, role="task_agent") or 0.0
        meta_total_nc = sum(cost.billed(resolved["meta_agent"]) for cost in no_cache.of_role("meta_agent"))
        per_meta_cycle_nc = meta_total_nc / observed_meta_rounds if observed_meta_rounds else 0.0

        return {
            "attempts": attempts,
            "meta_cycles": meta_cycles,
            "per_attempt_usd": per_attempt,
            "per_meta_cycle_usd": per_meta_cycle,
            "task_agent_usd": task_usd,
            "meta_agent_usd": meta_usd,
            "total_usd": task_usd + meta_usd,
            "total_usd_no_cache": per_attempt_nc * attempts + per_meta_cycle_nc * meta_cycles,
            "cache_hit_rate": self.cache_hit_rate(),
            "basis": {
                "sample_attempts": self.attempts("task_agent"),
                "sample_meta_rounds": observed_meta_rounds,
                "meta_agent_measured": observed_meta_rounds > 0,
                "pricing": {role: resolved[role].model for role in ROLES},
            },
        }

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------

    def _resolve(self, pricing: Pricing | Mapping[str, Pricing] | None) -> dict[str, Pricing]:
        pricing = pricing if pricing is not None else self.pricing
        if pricing is None:
            pricing = DEFAULT_PRICING
        if isinstance(pricing, Pricing):
            return {role: pricing for role in ROLES}
        missing = [role for role in ROLES if role not in pricing]
        if missing:
            raise ValueError(f"pricing is missing role(s) {missing}")
        return {role: pricing[role] for role in ROLES}

    def _no_cache_ledger(self) -> CostLedger:
        """A copy where every cached token is billed as a miss (worst case)."""
        cold = CostLedger(pricing=self.pricing)
        for cost in self.costs:
            cold.record(
                AttemptCost(
                    task_id=cost.task_id,
                    round_idx=cost.round_idx,
                    variant_id=cost.variant_id,
                    role=cost.role,
                    input_tokens=cost.prompt_tokens,
                    cached_input_tokens=0,
                    output_tokens=cost.output_tokens,
                    cache_write_tokens=cost.cache_write_tokens,
                    tool_calls=cost.tool_calls,
                    retries=cost.retries,
                    infra_failure=cost.infra_failure,
                )
            )
        return cold
