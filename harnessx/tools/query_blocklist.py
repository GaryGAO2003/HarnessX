# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""Search-query blocklist — a generic, opt-in, default-off gate (anti-contamination L1).

Sibling of :mod:`harnessx.tools.url_blocklist`: the URL blocklist refuses *result*
hosts, this module refuses the *query* itself at the search-tool entry, before any
network request. It catches an agent that is trying to look up a benchmark's
gold answers (e.g. searching ``"<bench> benchmark answer key"``) rather than
solving the task.

Core (``harnessx``) is a general-purpose library, so this module hard-codes **no**
terms. The rules are read from the ``HARNESSX_QUERY_BLOCKLIST`` environment
variable. When the variable is missing or empty, nothing is blocked and every
helper here is a no-op — behaviour is identical to a build that never imported
this module.

A caller that wants to block specific queries sets the env var before the harness
starts (see ``experiments/variant_pool/anti_contamination.py`` for the GAIA
answer-key wiring that uses ``os.environ.setdefault`` on this same variable).

Rule syntax (the env var is a comma-separated list of rules):

* Each **rule** is a ``+``-separated group of terms, e.g. ``gaia+benchmark``.
* A rule matches a query iff **every** term in it appears in the query as a
  case-insensitive substring (order-independent). This "all terms co-occur"
  semantics is what distinguishes an answer-hunting query (``gaia`` *and*
  ``answer``) from an incidental mention of a single word.
* The query is blocked iff **any** rule matches.

Terms are lower-cased and surrounding whitespace is trimmed; blank terms and
whole-blank rules are dropped. Because ``,`` and ``+`` are the two delimiters, a
term cannot itself contain either character — that is the only syntactic
restriction (there is no escaping).

The two public primitives — :func:`load_query_rules` and :func:`is_query_blocked`
— are pure and side-effect free (apart from reading the environment), so they are
trivially unit-testable and reusable from any tool. Neither ever raises.
"""

from __future__ import annotations

import os
from typing import Mapping, Sequence

#: Name of the environment variable holding the comma-separated rule list.
QUERY_BLOCKLIST_ENV_VAR = "HARNESSX_QUERY_BLOCKLIST"

#: Uniform tool-result text returned by every search entry point for a blocked
#: query. Intentionally terse — it does NOT explain *why* the query was blocked,
#: so the model is not taught to rewrite around the rule. The ``[blocked]``
#: prefix matches ``web_fetch``'s refusal wording, so trajectories can be grepped
#: for a single marker across both gates.
QUERY_BLOCKED_MESSAGE = "[blocked] search query blocked by policy"


def load_query_rules(
    env: "Mapping[str, str] | None" = None,
) -> "tuple[tuple[str, ...], ...]":
    """Load and parse the rules from ``HARNESSX_QUERY_BLOCKLIST``.

    Returns a tuple of rules, each a non-empty tuple of lower-cased terms. A
    missing or empty variable returns an empty tuple, i.e. "block nothing".
    Blank terms (from stray/duplicate ``+``) and whole-blank rules (from stray/
    duplicate ``,``) are skipped. ``env`` overrides the process environment (used
    by tests).
    """
    source = os.environ if env is None else env
    raw = source.get(QUERY_BLOCKLIST_ENV_VAR, "") or ""
    if not raw.strip():
        return ()
    rules: list[tuple[str, ...]] = []
    for raw_rule in raw.split(","):
        terms = tuple(
            term
            for term in (t.strip().lower() for t in raw_rule.split("+"))
            if term
        )
        if terms:
            rules.append(terms)
    return tuple(rules)


def is_query_blocked(
    query: str,
    rules: "Sequence[Sequence[str]] | None" = None,
) -> bool:
    """True iff *query* matches any rule (all of a rule's terms co-occur in it).

    ``rules`` defaults to :func:`load_query_rules`; an empty rule set always
    returns ``False``. Matching is case-insensitive and substring-based. Never
    raises — a non-string or otherwise unusable ``query`` is treated as not
    blocked (the caller's normal search path then runs).
    """
    if rules is None:
        rules = load_query_rules()
    if not rules:
        return False
    try:
        text = str(query or "").lower()
    except Exception:
        return False
    if not text:
        return False
    for terms in rules:
        if terms and all(term in text for term in terms):
            return True
    return False
