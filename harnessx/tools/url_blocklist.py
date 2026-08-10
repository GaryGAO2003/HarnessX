# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""URL domain blocklist — a generic, opt-in, default-off gate (anti-contamination L1).

Core (``harnessx``) is a general-purpose library, so this module hard-codes **no**
domains. The blocklist is read from the ``HARNESSX_URL_BLOCKLIST`` environment
variable (comma-separated). When the variable is missing or empty, nothing is
blocked and every helper here is a no-op — behaviour is identical to a build that
never imported this module.

A caller that wants to block specific hosts sets the env var before the harness
starts (see ``experiments/variant_pool/anti_contamination.py`` for the GAIA
answer-key wiring that uses ``os.environ.setdefault`` on this same variable).

Entry syntax (each comma-separated token in the env var):

* ``domain``              — block when the URL host equals ``domain`` or ends with
  ``"." + domain`` (so sub-domains are always covered).
* ``domain/path-prefix``  — additionally require the URL path to start with
  ``/path-prefix``.

Matching is case-insensitive on both host and path. The two public primitives —
:func:`load_blocklist` and :func:`is_blocked` — are pure and side-effect free
(apart from reading the environment), so they are trivially unit-testable and
reusable from any tool.
"""

from __future__ import annotations

import os
from typing import Mapping, Sequence
from urllib.parse import urlsplit

#: Name of the environment variable holding the comma-separated blocklist.
BLOCKLIST_ENV_VAR = "HARNESSX_URL_BLOCKLIST"


def _parse_entry(entry: str) -> "tuple[str, str] | None":
    """Parse one raw token into ``(domain, path_prefix)``; ``None`` if blank.

    Tolerant of a leading scheme (``https://host/...``) and of stray leading /
    trailing dots or slashes so hand-written env values are forgiving.
    """
    entry = entry.strip()
    if not entry:
        return None
    if "://" in entry:  # tolerate a full URL pasted as an entry
        entry = entry.split("://", 1)[1]
    entry = entry.strip()
    if "/" in entry:
        domain, raw_path = entry.split("/", 1)
        path = ("/" + raw_path.strip("/")).lower()
    else:
        domain, path = entry, ""
    domain = domain.strip().strip(".").lower()
    if not domain:
        return None
    return (domain, path)


def load_blocklist(
    env: "Mapping[str, str] | None" = None,
) -> "tuple[tuple[str, str], ...]":
    """Load and parse the blocklist from ``HARNESSX_URL_BLOCKLIST``.

    Returns a tuple of ``(domain, path_prefix)`` pairs. A missing or empty
    variable returns an empty tuple, i.e. "block nothing". ``env`` overrides the
    process environment (used by tests).
    """
    source = os.environ if env is None else env
    raw = source.get(BLOCKLIST_ENV_VAR, "") or ""
    if not raw.strip():
        return ()
    entries: list[tuple[str, str]] = []
    for token in raw.split(","):
        parsed = _parse_entry(token)
        if parsed is not None:
            entries.append(parsed)
    return tuple(entries)


def url_host(url: str) -> str:
    """Return the lower-cased host of *url* (no port), or ``""`` if unparseable.

    A scheme-less input such as ``example.com/foo`` is tolerated by assuming
    ``http://``.
    """
    raw = (url or "").strip()
    if not raw:
        return ""
    if "://" not in raw:
        raw = "http://" + raw
    try:
        return (urlsplit(raw).hostname or "").lower()
    except Exception:
        return ""


def is_blocked(
    url: str,
    blocklist: "Sequence[tuple[str, str]] | None" = None,
) -> bool:
    """True iff *url*'s host (and path, for path-scoped entries) is on the blocklist.

    ``blocklist`` defaults to :func:`load_blocklist`; an empty blocklist always
    returns ``False``. Never raises — an unparseable URL is treated as not
    blocked (the caller's normal fetch/search path then handles it).
    """
    if blocklist is None:
        blocklist = load_blocklist()
    if not blocklist:
        return False
    raw = (url or "").strip()
    if not raw:
        return False
    if "://" not in raw:
        raw = "http://" + raw
    try:
        parts = urlsplit(raw)
    except Exception:
        return False
    host = (parts.hostname or "").lower()
    if not host:
        return False
    path = (parts.path or "/").lower()
    for domain, prefix in blocklist:
        if host == domain or host.endswith("." + domain):
            if not prefix or path.startswith(prefix):
                return True
    return False


def filter_results(
    results: "Sequence[dict]",
    blocklist: "Sequence[tuple[str, str]] | None" = None,
) -> "tuple[list[dict], int]":
    """Drop result dicts whose ``url`` is blocked.

    Returns ``(kept, n_dropped)``. With an empty blocklist the input is returned
    unchanged (and ``n_dropped == 0``), so the common default-off path costs one
    env read and nothing else.
    """
    if blocklist is None:
        blocklist = load_blocklist()
    if not blocklist:
        return list(results), 0
    kept: list[dict] = []
    dropped = 0
    for r in results:
        url = r.get("url", "") if isinstance(r, dict) else ""
        if url and is_blocked(url, blocklist):
            dropped += 1
        else:
            kept.append(r)
    return kept, dropped
