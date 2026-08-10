"""#33 — envelope-aware reachability gate (dead lazy-edit rejection).

The B-arm graph gate spends a full measurement budget on every GATED
candidate.  A whole class of parameter edits is *provably* a behavioral no-op
before any measurement runs: an edit that only moves a threshold nothing in
the experiment envelope can ever reach.  Measuring such an edit banks pure
noise — the ghx_6x3_v4 lineage promoted a ``max_usd 40→50`` edit at 0.67 pass
purely because the parent baseline was noise-slammed that round, with the
edit's runtime behavior identical to the parent's (the per-attempt task cap
was $1, so a $40 or $50 CostGuard threshold is dead either way).

This module is the static gate that kills those edits *before* the bed.

Polarity — READ THIS BEFORE EDITING THE RULE TABLE
==================================================
The gate rejects **only provably dead** edits and defaults to PASS on
everything it cannot prove dead.  Mis-passing a lazy edit (it wastes one
measurement) is far cheaper than mis-rejecting a *binding* edit (it silently
removes a real lever from the search).  Three guardrails encode that bias:

1. **Whitelist, not blacklist.** Only ``(processor slug, param)`` pairs in
   :data:`_RULES` are judged; an unrecognized processor or param always
   passes (:func:`judge_param_edit` returns ``None``).

2. **Both-sides death.** An edit ``old → new`` is dead **iff both** ``old``
   and ``new`` are unreachable in the envelope — i.e. the attempt stops at the
   same place regardless, so the edit changes nothing observable.  If either
   endpoint is reachable the edit is *binding* and passes.  Example: under a
   $1 cap, ``max_usd 40→50`` is dead (both ≥ cap, both pre-empted by the task
   cap), but ``40→0.8`` is binding (new value fires at $0.80, a new stopping
   behavior the parent never had) and ``0.9→0.8`` is binding (both fire, and
   at different costs).

3. **Absent evidence passes.** No envelope dimension, an unreadable parent
   ``old`` value, or any parse/type failure ⇒ ``None`` (pass).  The functions
   never raise.

A candidate (:func:`judge_candidate`) is rejected only when **every** changed
param is dead — one binding-or-unknown param keeps the whole candidate alive.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Optional


# ── envelope ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Envelope:
    """The per-attempt experiment envelope a candidate runs inside.

    ``None`` on a dimension means that dimension is unknown and no rule keyed
    to it fires (the check is off for that dimension — never a rejection).
    """

    max_cost_usd: Optional[float] = None
    max_steps: Optional[int] = None


# ── reachability rules ──────────────────────────────────────────────────────


@dataclass(frozen=True)
class _Rule:
    """One reachability predicate keyed to ``(slug substring, param names)``.

    ``reach(value, envelope, context) -> bool | None`` returns whether a single
    threshold ``value`` can be *reached* inside the envelope — i.e. whether the
    processor's behavior at that threshold is observable before the attempt is
    otherwise stopped.  ``None`` means "cannot decide" and forces a pass.
    """

    slug_substr: str
    params: frozenset
    dim_attr: str          # Envelope attribute the rule reads
    dim_label: str         # how that dimension prints in the rationale
    reach: Callable[[float, Envelope, dict], Optional[bool]]


def _cost_reach(value: float, env: Envelope, _ctx: dict) -> Optional[bool]:
    """CostGuard.max_usd: it raises at ``cost >= max_usd``.  For that to fire
    *before* the per-attempt task cap pre-empts the attempt, the threshold must
    sit strictly under the cap — reachable iff ``value < max_cost_usd``.
    """
    cap = env.max_cost_usd
    if cap is None:
        return None
    return value < cap


def _warn_reach(value: float, env: Envelope, ctx: dict) -> Optional[bool]:
    """CostGuard.warning_threshold: the warning logs at ``cost >= max_usd *
    warning_threshold``.  Its effective trigger cost is that product; reachable
    iff the product sits under the cap.  ``max_usd`` comes from the edited value
    when the same edit changed it, else the parent's current value (both live in
    ``ctx``); an unresolvable ``max_usd`` yields ``None`` (pass).
    """
    cap = env.max_cost_usd
    if cap is None:
        return None
    raw = ctx.get("max_usd")
    if raw is None:
        return None
    try:
        max_usd = float(raw)
    except (TypeError, ValueError):
        return None
    return (max_usd * value) < cap


def _count_reach(value: float, env: Envelope, _ctx: dict) -> Optional[bool]:
    """LoopDetection consecutive-count thresholds: a repeat/drop count of ``t``
    cannot accumulate in fewer than ``t`` steps, so a threshold above the step
    budget can never trip — reachable iff ``value <= max_steps``.
    """
    steps = env.max_steps
    if steps is None:
        return None
    return value <= steps


#: First-batch rule table — DO NOT EXTEND without a matching reachability proof.
#: Keyed by processor-slug substring (matched against the node_id minus its
#: ``proc:`` prefix) and the constructor param name.
_RULES: "tuple[_Rule, ...]" = (
    _Rule("cost_guard", frozenset({"max_usd"}),
          "max_cost_usd", "max_cost_usd", _cost_reach),
    _Rule("cost_guard", frozenset({"warning_threshold"}),
          "max_cost_usd", "max_cost_usd", _warn_reach),
    _Rule("loop_detection",
          frozenset({"threshold", "warn_threshold",
                     "name_warn_threshold", "compaction_drop_threshold"}),
          "max_steps", "max_steps", _count_reach),
)


def _match_rule(node_slug: str, param: str) -> Optional[_Rule]:
    """First rule whose slug substring is in ``node_slug`` and that governs
    ``param`` — or ``None`` (unknown processor/param ⇒ default-pass).
    """
    for rule in _RULES:
        if rule.slug_substr in node_slug and param in rule.params:
            return rule
    return None


def _fmt(v: Any) -> str:
    """Render a threshold for the rationale: ``50.0``/``1.0`` for floats,
    ``20``/``30`` for ints — matching the canonical ENVELOPE_DEAD format.
    """
    if isinstance(v, bool):
        return str(v)
    if isinstance(v, float):
        if v == int(v):
            return f"{v:.1f}"
        return repr(round(v, 6))
    return str(v)


def _safe_reach(rule: _Rule, value: Any, env: Envelope, ctx: dict) -> Optional[bool]:
    """Coerce ``value`` to float and evaluate ``rule.reach`` — never raises;
    any coercion or predicate failure collapses to ``None`` (pass).
    """
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    try:
        return rule.reach(v, env, ctx)
    except Exception:  # noqa: BLE001 — never-raise contract
        return None


# ── per-param judgment ──────────────────────────────────────────────────────


def judge_param_edit(
    node_slug: str,
    param: str,
    old_value: Any,
    new_value: Any,
    envelope: Optional[Envelope],
    context_params: Optional[dict] = None,
) -> Optional[str]:
    """Judge a single ``old_value → new_value`` param edit for envelope death.

    Returns ``None`` to PASS (the default for everything not provably dead) or
    an explanation string when the edit is dead — i.e. **both** endpoints are
    unreachable in ``envelope``, so the runtime behavior is identical and the
    edit is a no-op.

    Passes (returns ``None``) whenever evidence is missing: no envelope, no
    matching rule, an unknown envelope dimension, an unreadable ``old_value``,
    or any value that will not coerce.  ``context_params`` supplies sibling
    values a rule needs (``max_usd`` for ``warning_threshold``).
    """
    if envelope is None:
        return None
    rule = _match_rule(node_slug, param)
    if rule is None:
        return None
    env_value = getattr(envelope, rule.dim_attr, None)
    if env_value is None:
        return None
    if old_value is None:                       # parent value unreadable ⇒ pass
        return None
    ctx = context_params if isinstance(context_params, dict) else {}
    old_reach = _safe_reach(rule, old_value, envelope, ctx)
    new_reach = _safe_reach(rule, new_value, envelope, ctx)
    if old_reach is None or new_reach is None:  # cannot decide one side ⇒ pass
        return None
    if (not old_reach) and (not new_reach):
        return (
            f"{rule.slug_substr}.{param}={_fmt(new_value)} unreachable under "
            f"{rule.dim_label}={_fmt(env_value)} "
            f"(old={_fmt(old_value)} also unreachable)"
        )
    return None


# ── per-candidate judgment ──────────────────────────────────────────────────


def judge_candidate(
    operator: str,
    operator_params: dict,
    parent_param_reader: Callable[[str, str], Any],
    envelope: Optional[Envelope],
) -> "list[str]":
    """Judge a whole candidate; return the dead-param explanations that justify
    rejecting it, or ``[]`` to pass.

    Only parameter-change operators are judged (those carrying a
    ``param_changes`` dict — i.e. ``mutate_processor_params``); every other
    operator passes with ``[]``.  A non-empty list is returned **only when every
    changed param is dead** — the first binding-or-unknown param short-circuits
    to ``[]`` (one live lever keeps the candidate).  Never raises.

    ``parent_param_reader(node_id, param)`` reads the parent's current ctor
    value for that node/param (``None`` when absent); it resolves each edit's
    ``old`` endpoint and the ``max_usd`` context for ``warning_threshold``.
    """
    if envelope is None:
        return []
    if not isinstance(operator_params, dict):
        return []
    param_changes = operator_params.get("param_changes")
    if not isinstance(param_changes, dict) or not param_changes:
        return []
    node_id = operator_params.get("node_id")
    if not isinstance(node_id, str) or not node_id:
        return []
    node_slug = node_id.split(":", 1)[1] if ":" in node_id else node_id

    def _read(param: str) -> Any:
        try:
            return parent_param_reader(node_id, param)
        except Exception:  # noqa: BLE001 — never-raise contract
            return None

    # Effective sibling context: edited values win, parent fills the gaps. Only
    # max_usd is consumed today (by the warning_threshold rule); resolving it
    # here keeps judge_param_edit free of reader plumbing.
    context_params = dict(param_changes)
    if "max_usd" not in context_params:
        parent_max_usd = _read("max_usd")
        if parent_max_usd is not None:
            context_params["max_usd"] = parent_max_usd

    reasons: "list[str]" = []
    for param, new_value in param_changes.items():
        old_value = _read(param)
        verdict = judge_param_edit(
            node_slug, param, old_value, new_value, envelope, context_params)
        if verdict is None:
            return []                # binding or unknown ⇒ whole candidate lives
        reasons.append(verdict)
    return reasons
