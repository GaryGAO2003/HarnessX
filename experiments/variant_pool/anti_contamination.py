# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""Anti-contamination layer 1 — GAIA answer-key blocklist wiring (B-arm).

Core (:mod:`harnessx.tools.url_blocklist`) ships a *generic*, default-off URL
blocklist keyed on the ``HARNESSX_URL_BLOCKLIST`` env var; it hard-codes no
domains. This experiment-side module holds the GAIA-specific list of leaderboard
/ answer-key hosts that publish per-task ``Expected answer: X`` strings, and
installs it into the environment so every WebSearch / WebFetch / Browser call in
a run refuses to surface those sites.

Enabling from other entry points (A-arm, ad-hoc runs, CI) uses the *same* env
var — there is no second switch. Either export it before launch::

    export HARNESSX_URL_BLOCKLIST="hal.cs.princeton.edu,neurometric.ai,harbor-index.org,huggingface.co/datasets,datasets-server.huggingface.co"

or call :func:`install_blocklist_env` at process start. The function uses
``os.environ.setdefault``, so an operator-supplied value is always preserved and
never overwritten — set the env var explicitly to widen, narrow, or disable
(empty string) the list without editing code.
"""

from __future__ import annotations

import os

from harnessx.tools.url_blocklist import BLOCKLIST_ENV_VAR

#: GAIA leaderboard / answer-key domains that publish per-task expected answers.
#: Bare registrable domains suffice — the core matcher covers every sub-domain
#: (e.g. ``leaderboard.neurometric.ai`` is caught by ``neurometric.ai``).
#:
#: The ``huggingface.co/datasets`` entry is deliberately the whole dataset tree,
#: not just ``/datasets/gaia-benchmark``: the ghx_6x3_v3 smoke showed agents
#: pulling gold answers from third-party GAIA *mirrors* under other namespaces
#: (e.g. ``m-ric/...``), which a per-org path cannot enumerate.
#: ``datasets-server.huggingface.co`` is the dataset-viewer rows API — same
#: content, different host. Trade-off: a taskbed whose questions legitimately
#: require reading an HF dataset page must narrow this via an explicit
#: ``HARNESSX_URL_BLOCKLIST`` export (operator value always wins — setdefault);
#: none of the current calib6/holdout6 tasks needs one.
GAIA_ANSWER_DOMAINS = (
    "hal.cs.princeton.edu",
    "neurometric.ai",
    "harbor-index.org",
    "huggingface.co/datasets",
    "datasets-server.huggingface.co",
)


def install_blocklist_env() -> str:
    """Install :data:`GAIA_ANSWER_DOMAINS` into ``HARNESSX_URL_BLOCKLIST``.

    ``setdefault`` semantics: if the operator already set the variable it is left
    untouched. Returns the effective blocklist string now in the environment.
    """
    os.environ.setdefault(BLOCKLIST_ENV_VAR, ",".join(GAIA_ANSWER_DOMAINS))
    return os.environ[BLOCKLIST_ENV_VAR]
