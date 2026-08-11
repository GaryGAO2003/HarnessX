# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""Run identity — the three content-addressed hashes at their three moments (v6 M9).

``identity.py`` defines three pure hash functions but never says *when* to call
them.  They are three moments in a run's life:

  * **genotype** — what was declared.  Available the instant the config is turned
    into a graph (:func:`~harnessx.graph.snapshot.to_graph`); it reads only
    ``nodes`` / ``edges``, both frozen properties of the built config.
  * **deployment** — what was actually deployed.  Available at the same instant:
    the runtime overlay (``runtime_nodes`` / ``runtime_edges``) is produced by the
    same ``to_graph`` call from the config's ``RuntimeReg`` entries, which are
    likewise frozen before any step runs.
  * **phenotype** — what actually happened.  Only complete once the run is over,
    because its extra inputs are the observed edges of the unfolded graph U, and U
    is not final until the last invocation is recorded.

So genotype and deployment are computed once, up front, over the snapshot; the
phenotype is computed at the end by *projecting* U's observed edges back onto that
same snapshot.  Computing any hash before its inputs are final would be worse than
not computing it — a phenotype taken mid-run would claim "this is what happened"
over a truncated observation.

Projection (U → snapshot).  A U node is an *invocation* (``{static_node_id}@t{ordinal}``);
a snapshot node is a *component*.  To fold U's observed edges into the phenotype we
project each observed edge onto the static node ids its endpoints came from
(:func:`~harnessx.graph.types.parse_unfolded_id`), collapse duplicates, and drop
edges whose endpoints are not static components of the deployed graph (tool nodes,
the ``UNGRAPHED`` sentinel — neither is a node in G).  What the projection loses:

  * **multiplicity and order** — two firings of the same component collapse to one
    edge; how many times, and in what interleaving, is gone;
  * **the slot channel** — an ``OBSERVED_DATA`` edge no longer says which slot the
    data flowed through (metadata is dropped to the empty dict);
  * **tool / ungraphed participation** — observed edges touching a ``tool:`` node
    or the ``UNGRAPHED`` sentinel are not represented, because those are not static
    nodes in G (the same honest limitation M5 recorded for tools).

What survives is the observed *relation set*: which declared components observably
controlled or fed which, split by control vs data.  Two runs that exercised the
same relations get the same phenotype regardless of how often or in what order —
which is exactly what a phenotype (observable behaviour) should key on.

Absence vs. empty.  When U was not recorded at all (``HARNESSX_GHX_UNFOLD`` off,
the default), the phenotype cannot be computed honestly, so it is recorded as
*absent, with a reason* — never as a hash over an empty observed set.  Those are
different claims: "observation was off" vs. "nothing was observed".  A run that
DID record U but observed no slot/control flow still gets a real phenotype hash
(equal to its deployment hash) — observation was on, it simply saw nothing.

Recording is free when nothing consumes it: the three are computed and persisted
per run only when :func:`identity_enabled` is true (env ``HARNESSX_GHX_IDENTITY``).
With it absent, ``to_graph`` is never called and no hash is taken.
"""

from __future__ import annotations

import datetime
import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from .identity import deployment_hash, genotype_hash, phenotype_hash
from .snapshot import to_graph
from .types import Edge, EdgeType, parse_unfolded_id

SCHEMA = "ghx-identity-v1"

_ENABLE_VALUES = frozenset({"1", "true", "on", "yes"})

# Default reason recorded when the phenotype is absent because U was never taken.
_UNFOLD_OFF_REASON = "unfold_disabled"

_OBSERVED_PREFIX = "observed_"


def identity_enabled() -> bool:
    """True when the three run-identity hashes should be computed and persisted.

    Read at call time (never cached at import), default OFF, so a run pays nothing
    unless ``HARNESSX_GHX_IDENTITY`` is explicitly set.  Kept separate from
    ``HARNESSX_GHX_UNFOLD`` on purpose: identity can be on while unfold is off,
    which is exactly the case that records the phenotype as absent-with-reason.
    """
    return os.environ.get("HARNESSX_GHX_IDENTITY", "").strip().lower() in _ENABLE_VALUES


# ── record ──────────────────────────────────────────────────────────────────


@dataclass
class RunIdentity:
    """The three moments of one run, plus the transient snapshot they share.

    ``phenotype`` is ``None`` exactly when ``phenotype_absent_reason`` is set —
    the two are mutually exclusive and together encode the absence distinction.
    ``snapshot`` is the live ``to_graph`` snapshot (kept so the phenotype can be
    projected onto the same object the genotype/deployment were taken over); it is
    never serialized.
    """

    run_id: str
    session_id: str
    genotype: str = ""
    deployment: str = ""
    phenotype: "str | None" = None
    phenotype_absent_reason: "str | None" = None
    projected_edge_count: int = 0
    snapshot: object = field(default=None, repr=False, compare=False)


# ── the three moments ────────────────────────────────────────────────────────


def begin_run_identity(config, run_id: str, session_id: str) -> RunIdentity:
    """Moments 1 and 2: compute genotype + deployment from the config's graph.

    Called at run start.  This is the earliest honest point for both: the config
    is frozen (it is the harness's built ``config``), and the runtime overlay the
    deployment reads is produced from that same frozen config by ``to_graph`` — so
    neither input can still change once the loop begins.  The snapshot is retained
    on the returned record for the phenotype projection at run end.
    """
    snapshot = to_graph(config)
    return RunIdentity(
        run_id=run_id,
        session_id=session_id,
        genotype=genotype_hash(snapshot),
        deployment=deployment_hash(snapshot),
        snapshot=snapshot,
    )


def project_observed_edges(snapshot, unfolded) -> int:
    """Fold U's observed edges onto the snapshot as static, deduplicated edges.

    Each U observed edge ``a@t{i} -> b@t{j}`` projects onto the static ids ``a``,
    ``b`` it came from.  Edges are kept only when BOTH ids are components of the
    deployed graph (``nodes`` ∪ ``runtime_nodes``); an endpoint that resolves to a
    ``tool:`` node or the ``UNGRAPHED`` sentinel (never a static node in G) drops
    the whole edge.  Duplicates — the many invocation-level edges that collapse to
    one ``(source, target, type)`` — are emitted once, with empty metadata (the
    per-invocation slot / step detail is deliberately not carried into identity).

    The projected edges are appended to ``snapshot.edges`` with the SAME observed
    edge types they had in U, so the genotype and deployment hashes — which filter
    ``observed_*`` edges out — are unmoved by this call; only the phenotype sees
    them.  Returns the number of static edges appended.
    """
    static_ids = set(snapshot.nodes) | set(snapshot.runtime_nodes)
    seen: set = set()
    appended = 0
    for e in getattr(unfolded, "edges", []):
        etype = e.edge_type
        # U carries only observed edges; guard anyway so a structural type can
        # never slip into the projection (that would move the genotype).
        if not isinstance(etype, str) or not etype.startswith(_OBSERVED_PREFIX):
            continue
        try:
            src_base, _ = parse_unfolded_id(e.source)
            tgt_base, _ = parse_unfolded_id(e.target)
        except ValueError:
            continue
        if src_base not in static_ids or tgt_base not in static_ids:
            continue
        key = (src_base, tgt_base, etype)
        if key in seen:
            continue
        seen.add(key)
        snapshot.edges.append(
            Edge(
                source_id=src_base,
                target_id=tgt_base,
                edge_type=EdgeType(etype),
                metadata={},
            )
        )
        appended += 1
    return appended


def finalize_run_identity(identity: RunIdentity, unfolded, *, absent_reason: str = _UNFOLD_OFF_REASON) -> RunIdentity:
    """Moment 3: compute the phenotype, or record its honest absence.

    ``unfolded is None`` means U was never recorded (unfold off): the phenotype is
    recorded as absent-with-reason, NOT as a hash over an empty observed set.  A
    non-``None`` U — even one that observed nothing — yields a real phenotype hash
    over the projected relations (equal to the deployment hash when U is empty).
    """
    if unfolded is None:
        identity.phenotype = None
        identity.phenotype_absent_reason = absent_reason
        identity.projected_edge_count = 0
        return identity
    snapshot = identity.snapshot
    identity.projected_edge_count = project_observed_edges(snapshot, unfolded)
    identity.phenotype = phenotype_hash(snapshot)
    identity.phenotype_absent_reason = None
    return identity


# ── persistence ──────────────────────────────────────────────────────────────


def _iso_now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def identity_record(identity: RunIdentity) -> dict:
    """The single JSON record persisted for a run's identity."""
    if identity.phenotype is None:
        pheno: dict = {"absent": True, "reason": identity.phenotype_absent_reason or _UNFOLD_OFF_REASON}
    else:
        pheno = {"hash": identity.phenotype, "projected_edge_count": identity.projected_edge_count}
    return {
        "schema": SCHEMA,
        "run_id": identity.run_id,
        "session_id": identity.session_id,
        "created": _iso_now(),
        "genotype_hash": identity.genotype,
        "deployment_hash": identity.deployment,
        "phenotype": pheno,
    }


def identity_path(base_dir: str, session_id: str, run_id: str) -> Path:
    """Path the identity is written to: ``{base_dir}/{session_id}/{run_id}_identity.json``."""
    return Path(base_dir) / session_id / f"{run_id}_identity.json"


def write_identity(identity: RunIdentity, base_dir: str = "sessions") -> Path:
    """Write ``identity`` as one JSON object under the HarnessJournal session layout."""
    path = identity_path(base_dir, identity.session_id, identity.run_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(json.dumps(identity_record(identity), ensure_ascii=False) + "\n")
    return path


def load_identity(path) -> RunIdentity:
    """Read an identity JSON file back into a :class:`RunIdentity` (round-trip)."""
    with open(path, encoding="utf-8") as f:
        rec = json.loads(f.read())
    pheno = rec.get("phenotype") or {}
    if pheno.get("absent"):
        phenotype = None
        reason = pheno.get("reason", _UNFOLD_OFF_REASON)
        count = 0
    else:
        phenotype = pheno.get("hash")
        reason = None
        count = int(pheno.get("projected_edge_count", 0))
    return RunIdentity(
        run_id=rec.get("run_id", ""),
        session_id=rec.get("session_id", ""),
        genotype=rec.get("genotype_hash", ""),
        deployment=rec.get("deployment_hash", ""),
        phenotype=phenotype,
        phenotype_absent_reason=reason,
        projected_edge_count=count,
    )
