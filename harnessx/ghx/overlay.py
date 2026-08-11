# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""The flag-gated wrapper — a function, not a subclass.

:func:`run_round_with_graph_evidence` materialises graph evidence (when the flag
is on) and then delegates to ``orchestrator.run_round(...)`` unchanged.  There is
no subclass of the vendored :class:`~harnessx.aegis.orchestrator.AegisOrchestrator`
and no method override: the vendored orchestrator stays importable and callable
exactly as before, and the vendored-integrity test proves the package is byte-for-
byte untouched.

Flag ``HARNESSX_GHX_AEGIS_EVIDENCE`` is read at call time, default off (same
convention as ``HARNESSX_GHX_UNFOLD`` / ``HARNESSX_GHX_IDENTITY``).  Off → this is
a pure pass-through: it writes nothing and simply awaits ``run_round``.
"""

from __future__ import annotations

import os

from .evidence_files import materialize_graph_evidence

_ENABLE_VALUES = frozenset({"1", "true", "on", "yes"})


def aegis_evidence_enabled() -> bool:
    """True when graph evidence should be materialised for an official-arm round.

    Read at call time (never cached at import), default OFF, so a run pays nothing
    unless ``HARNESSX_GHX_AEGIS_EVIDENCE`` is explicitly set.
    """
    return os.environ.get("HARNESSX_GHX_AEGIS_EVIDENCE", "").strip().lower() in _ENABLE_VALUES


async def run_round_with_graph_evidence(
    orchestrator,
    *,
    failed_task_ids,
    resolver,
    evidence_enabled: bool | None = None,
    **run_round_kwargs,
):
    """Materialise graph evidence (flag-gated) then delegate to ``run_round``.

    ``orchestrator`` is a vendored :class:`~harnessx.aegis.orchestrator.AegisOrchestrator`;
    ``run_round_kwargs`` are forwarded verbatim to ``orchestrator.run_round`` (which
    is keyword-only, so ``round_n`` is always present).  ``failed_task_ids`` and
    ``resolver`` drive :func:`~harnessx.ghx.evidence_files.materialize_graph_evidence`
    and are NOT forwarded to ``run_round``.

    ``evidence_enabled`` overrides the flag for tests; left ``None`` it reads
    :func:`aegis_evidence_enabled`.  When false, nothing is written and the call is a
    pure pass-through.  Evidence is materialised BEFORE delegating so the files exist
    by the time Stage P dispatches the Digester.
    """
    if evidence_enabled is None:
        evidence_enabled = aegis_evidence_enabled()
    if evidence_enabled:
        materialize_graph_evidence(
            run_dir=orchestrator.run_dir,
            round_n=run_round_kwargs["round_n"],
            failed_task_ids=failed_task_ids,
            resolver=resolver,
        )
    return await orchestrator.run_round(**run_round_kwargs)
