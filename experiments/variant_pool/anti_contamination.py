# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""Anti-contamination layer 1 — GAIA answer-key blocklist wiring (B-arm).

Two complementary, default-off gates in core, both keyed on env vars that
hard-code no GAIA terms:

* :mod:`harnessx.tools.url_blocklist` (``HARNESSX_URL_BLOCKLIST``) refuses
  *result* hosts — the leaderboard / answer-key sites that publish per-task
  ``Expected answer: X`` strings.
* :mod:`harnessx.tools.query_blocklist` (``HARNESSX_QUERY_BLOCKLIST``) refuses
  the *query* itself at the search-tool entry, before any network request —
  catching an agent that searches for the benchmark's gold answers instead of
  solving the task.

This experiment-side module holds the GAIA-specific values for both and installs
them into the environment so every WebSearch / WebFetch / Browser call in a run
is gated.

Enabling from other entry points (A-arm, ad-hoc runs, CI) uses the *same* env
vars — there is no second switch. Either export them before launch::

    export HARNESSX_URL_BLOCKLIST="hal.cs.princeton.edu,neurometric.ai,harbor-index.org,huggingface.co/datasets,datasets-server.huggingface.co"
    export HARNESSX_QUERY_BLOCKLIST="gaia+benchmark,gaia+dataset,gaia+answer,gaia+validation,gaia+huggingface"

or call :func:`install_blocklist_env` at process start. The function uses
``os.environ.setdefault`` on each var, so an operator-supplied value is always
preserved and never overwritten — set a var explicitly to widen, narrow, or
disable (empty string) the corresponding gate without editing code.
"""

from __future__ import annotations

import os

from harnessx.tools.query_blocklist import QUERY_BLOCKLIST_ENV_VAR
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

#: GAIA answer-hunting query rules for the core query blocklist. Each entry is a
#: ``+``-joined term group that blocks a search only when *all* of its terms
#: co-occur (case-insensitive substring) in the query — so a single incidental
#: word never trips the gate. The pairing on ``gaia`` targets the queries the
#: ghx smokes showed agents issuing to find the leaderboard / HF dataset / gold
#: answers rather than solve the task.
#:
#: Trade-off: ``gaia+dataset`` also matches a *legitimate* ESA Gaia-telescope
#: query such as ``"Gaia DR3 dataset"`` — an accepted false positive. None of the
#: current calib6 / holdout6 (12 tasks) needs the astronomy Gaia dataset, so the
#: default stays broad; a future taskbed that does must narrow this via an
#: explicit ``HARNESSX_QUERY_BLOCKLIST`` export (operator value always wins —
#: setdefault).
GAIA_QUERY_RULES = (
    "gaia+benchmark",
    "gaia+dataset",
    "gaia+answer",
    "gaia+validation",
    "gaia+huggingface",
)


def install_blocklist_env() -> str:
    """Install the GAIA URL and query blocklists into their env vars.

    :data:`GAIA_ANSWER_DOMAINS` -> ``HARNESSX_URL_BLOCKLIST`` and
    :data:`GAIA_QUERY_RULES` -> ``HARNESSX_QUERY_BLOCKLIST``, each via
    ``setdefault`` (an operator-supplied value for either var is left untouched).
    Returns the effective *URL* blocklist string now in the environment
    (unchanged contract — callers that read the return value predate the query
    gate); the query rules are installed as a side effect on the environment.
    """
    os.environ.setdefault(BLOCKLIST_ENV_VAR, ",".join(GAIA_ANSWER_DOMAINS))
    os.environ.setdefault(QUERY_BLOCKLIST_ENV_VAR, ",".join(GAIA_QUERY_RULES))
    return os.environ[BLOCKLIST_ENV_VAR]
