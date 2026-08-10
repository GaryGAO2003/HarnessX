"""Historical-config roundtrip runner — G1 gate (graph→config→build→re-graph).

Roadmap (``experiments/docs/THESIS-RESTRUCTURE-GHX.md``): every existing
``HarnessConfig`` must survive ``graph → config → build → re-graph`` with its
genotype preserved (``roundtrip 100%``).  This runner MEASURES that fraction
over two corpora and reports it fail-open (it never mutates anything).

Corpus (two sources)
--------------------
* **builtin** — in-process ``HarnessBuilder | <bundle>`` compositions.  These
  are born as processor *instances*, so both ends of the round-trip read their
  metadata from the same class introspection → genotype-stable by construction.
  They are the always-green baseline.
* **yaml** — historical run configs discovered under ``--root`` (default
  ``recipe/gaia_evolver/runs``): every ``config.yaml``, with ``C-*`` candidate
  copies deduplicated per run (shortest path wins, mirroring
  ``replay_validator.discover_candidates``) and non-candidate parent/base
  snapshots included individually.

Roundtrip criterion (GENOTYPE only)
-----------------------------------
``genotype_hash`` (``harnessx.graph.identity``) covers ONLY the persistent
graph — the runtime overlay (``RuntimeReg`` → ``runtime_nodes`` /
``runtime_edges``) is invisible (I6), and ``graph_to_config_dict`` emits only
the persistent layer (runtime-only processors are skipped).  A config carrying
runtime-only processors is therefore expected to preserve its GENOTYPE across
the round-trip even though its runtime overlay is not serialized — so the
judgment compares genotype, never deployment/phenotype.

``transactional_apply`` reuse
-----------------------------
``harnessx.graph.validate.transactional_apply(g, [], materialize=True)`` already
runs the P3 transaction protocol: materialize → build → re-graph, fail-closed,
checking (a) re-graph DETERMINISM (two exports agree) and (b) processor-target
multiset preservation.  It does NOT assert equality with the ORIGINAL snapshot's
genotype.  This runner reuses it as the build/soundness gate (empty edits), then
adds the missing assertion — ``genotype_hash(re_graph) == genotype_hash(g1)`` —
explicitly.  That assertion is the round-trip proper.

Fairness (thesis-reading integrity)
-----------------------------------
A build ``ImportError`` on a replayed config may be an environment artifact (the
config's co-located processor file lived in ITS run workspace, not on today's
path).  Import failures are reported SEPARATELY as ``build_import_uncertain`` and
are NOT counted into the headline round-trip denominator; the headline uses only
environment-independent verdicts.

Usage::

    python -m experiments.analysis.roundtrip_validator \
        [--root recipe/gaia_evolver/runs]* [--out roundtrip_report.json]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

#: mirrors ``replay_validator._CAND_RE`` — a candidate id (round + slot).
_CAND_RE = re.compile(r"C-R\d+-\d+")

# ── verdict labels ───────────────────────────────────────────────────────────
OK = "roundtrip_ok"
MISMATCH = "roundtrip_hash_mismatch"
IMPORT_UNCERTAIN = "build_import_uncertain"
BUILD_OTHER = "build_failed_other"
LOAD_FAILED = "config_load_failed"

# ── mismatch sub-classification (second normalization loop) ──────────────────
#: The first-loop mismatch settled to a fixed point after ONE more normalization
#: (g3 == g2) — the drift was pre-v5.3 partial serialization, not divergence.
MISMATCH_STABILIZES = "mismatch_stabilizes"
#: A second loop did NOT reach a fixed point (g3 != g2, or its build failed).
MISMATCH_DIVERGENT = "mismatch_divergent"


# ── builtin corpus ───────────────────────────────────────────────────────────


def _builtin_recipes() -> "list[tuple[str, object]]":
    """``(label, factory)`` for each always-buildable builtin composition.

    ``context | coding | control`` (named in the roadmap) is deliberately absent:
    ``coding`` and ``control`` both register the reliability-guard singletons
    (loop_detection, parse_retry, tool_call_correction, self_verify, todo_check,
    repeated_edit_detector), so composing them raises ``HarnessConflictError`` at
    build.  It is NOT always-buildable and therefore not a valid baseline member.
    """
    from harnessx.bundles import (
        coding, context, contrarian, control, reliability, window_mgmt,
    )
    from harnessx.core.builder import HarnessBuilder

    return [
        ("builtin/context", lambda: (HarnessBuilder() | context).build()),
        ("builtin/context+reliability",
         lambda: (HarnessBuilder() | context | reliability).build()),
        ("builtin/context+coding",
         lambda: (HarnessBuilder() | context | coding).build()),
        ("builtin/context+window_mgmt",
         lambda: (HarnessBuilder() | context | window_mgmt).build()),
        ("builtin/context+contrarian",
         lambda: (HarnessBuilder() | context | contrarian).build()),
        ("builtin/context+control",
         lambda: (HarnessBuilder() | context | control).build()),
    ]


# ── on-disk corpus discovery ─────────────────────────────────────────────────


def discover_yaml_configs(root: "Path | str") -> "dict[str, Path]":
    """``label → config path`` for every persisted config under *root*.

    Mirrors ``replay_validator.discover_candidates`` for ``C-*`` candidates (per
    candidate id the SHORTEST path wins — the canonical product; re-attempt /
    output_dir copies live deeper), but SCOPES the dedup to the run subtree (the
    first path component under *root*) so a candidate id that repeats across runs
    is not collapsed.  Non-candidate ``config.yaml`` (parent / base / active_pool
    snapshots) are included individually, keyed by their relative path.
    """
    root = Path(root)
    cand: dict[tuple[str, str], Path] = {}   # (run, cid) → shortest path
    others: dict[str, Path] = {}             # rel-posix → path
    for path in sorted(root.rglob("config.yaml"),
                       key=lambda p: (len(p.parts), str(p))):
        rel = path.relative_to(root)
        run = rel.parts[0] if len(rel.parts) > 1 else "."
        m = _CAND_RE.search(rel.as_posix())
        if m:
            cand.setdefault((run, m.group(0)), path)
        else:
            others[rel.as_posix()] = path
    items: dict[str, Path] = {f"{run}/{cid}": p for (run, cid), p in cand.items()}
    items.update(others)
    return items


# ── round-trip kernel ────────────────────────────────────────────────────────


def _err(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}"[:200]


def _materialize_and_regraph(snapshot):
    """``graph → config dict → build → re-graph``.  A seam tests can monkeypatch."""
    from harnessx.core.builder import build_from_config
    from harnessx.graph.snapshot import to_graph
    from harnessx.graph.transform import graph_to_config_dict

    return to_graph(build_from_config(graph_to_config_dict(snapshot)))


def _classify_failed(report) -> str:
    """Environment-fair verdict for a failed ``transactional_apply`` report."""
    for issue in report.issues:
        if issue.error_type == "build_failed" and (
            "ImportError" in issue.message
            or "ModuleNotFoundError" in issue.message
        ):
            return IMPORT_UNCERTAIN
    return BUILD_OTHER


def roundtrip_config(config) -> "tuple[str, dict]":
    """Run the ``graph→config→build→re-graph`` round-trip on one HarnessConfig.

    Returns ``(verdict, detail)``.  For ``roundtrip_ok`` *detail* carries the
    single stable genotype.  A first-loop ``roundtrip_hash_mismatch`` triggers a
    SECOND normalization loop (``g2 → re-graph → g3``) that reads the fixed
    point: *detail* then carries a ``subclass`` (``mismatch_stabilizes`` when
    ``g3 == g2`` — one normalization was enough — else ``mismatch_divergent``)
    and the three genotypes ``genotype_g1`` / ``genotype_g2`` / ``genotype_g3``.
    Build failures carry a truncated reason.
    """
    from harnessx.graph.identity import genotype_hash
    from harnessx.graph.snapshot import to_graph
    from harnessx.graph.validate import transactional_apply

    try:
        g1 = to_graph(config)
        h1 = genotype_hash(g1)
    except Exception as exc:  # noqa: BLE001 — fail closed
        return BUILD_OTHER, {"stage": "to_graph", "error": _err(exc)}

    # Reuse the P3 transaction protocol (empty edits) as the build/soundness
    # gate: materialize → build → re-graph, fail-closed, with re-graph
    # determinism + processor-target preservation checked internally.
    _result, report = transactional_apply(g1, [], materialize=True)
    if not report.passed:
        return _classify_failed(report), {
            "genotype_before": h1,
            "reason": report.reason()[:400],
        }

    # transactional_apply guarantees re-graph determinism but NOT equality with
    # the ORIGINAL genotype — assert it explicitly (this is the round-trip).
    try:
        re_graph = _materialize_and_regraph(g1)
        h2 = genotype_hash(re_graph)
    except Exception as exc:  # noqa: BLE001 — unreachable once ta passed, guarded
        return BUILD_OTHER, {"stage": "regraph", "genotype_before": h1,
                             "error": _err(exc)}

    if h1 == h2:
        return OK, {"genotype": h1}

    # First-loop mismatch — run a SECOND normalization loop to read the fixed
    # point.  A pre-v5.3 partial serialization (missing _order_/_hooks_/…) drifts
    # exactly once: g1 (partial) != g2 (full instance introspection), but g2
    # already carries full metadata, so g3 = re-graph(g2) should equal g2.
    #   g3 == g2 → mismatch_stabilizes  (one normalization reaches a fixed point)
    #   g3 != g2 → mismatch_divergent   (no fixed point after one more loop)
    try:
        re_graph2 = _materialize_and_regraph(re_graph)
        h3 = genotype_hash(re_graph2)
    except Exception as exc:  # noqa: BLE001 — a 2nd-loop build failure = divergence
        return MISMATCH, {
            "subclass": MISMATCH_DIVERGENT,
            "genotype_g1": h1, "genotype_g2": h2,
            "stage": "regraph2", "error": _err(exc),
        }

    subclass = MISMATCH_STABILIZES if h3 == h2 else MISMATCH_DIVERGENT
    return MISMATCH, {
        "subclass": subclass,
        "genotype_g1": h1, "genotype_g2": h2, "genotype_g3": h3,
    }


# ── corpus runner ────────────────────────────────────────────────────────────


def run(roots: "list[Path]") -> dict:
    """Round-trip the builtin corpus + every on-disk config under *roots*."""
    per_item: dict[str, dict] = {}
    verdicts: Counter = Counter()
    by_source: Counter = Counter()
    subclasses: Counter = Counter()  # mismatch fixed-point sub-classification

    def _record(label, source, verdict, detail, config_path=None):
        row: dict = {"source": source, "verdict": verdict}
        if config_path is not None:
            row["config"] = str(config_path)
        row.update(detail)
        per_item[label] = row
        verdicts[verdict] += 1
        by_source[source] += 1
        sub = detail.get("subclass")
        if sub:
            subclasses[sub] += 1

    # 1. builtin corpus (always-green baseline)
    for label, factory in _builtin_recipes():
        try:
            config = factory()
        except Exception as exc:  # noqa: BLE001 — a builtin that stops building
            _record(label, "builtin", BUILD_OTHER,
                    {"stage": "builtin_build", "error": _err(exc)})
            continue
        verdict, detail = roundtrip_config(config)
        _record(label, "builtin", verdict, detail)

    # 2. on-disk YAML corpus
    from harnessx.core.harness import HarnessConfig

    for root in roots:
        for local, path in discover_yaml_configs(root).items():
            label = f"{root.name}/{local}"
            try:
                config = HarnessConfig.from_yaml_file(str(path))
            except Exception as exc:  # noqa: BLE001 — schema / parse failure
                _record(label, "yaml", LOAD_FAILED, {"error": _err(exc)},
                        config_path=path)
                continue
            verdict, detail = roundtrip_config(config)
            _record(label, "yaml", verdict, detail, config_path=path)

    total = len(per_item)
    import_uncertain = verdicts.get(IMPORT_UNCERTAIN, 0)
    ok = verdicts.get(OK, 0)
    stabilizes = subclasses.get(MISMATCH_STABILIZES, 0)
    divergent = subclasses.get(MISMATCH_DIVERGENT, 0)
    denom = total - import_uncertain
    fixed_point = ok + stabilizes
    buildable = ok + stabilizes + divergent
    return {
        "roots": [str(r) for r in roots],
        "total": total,
        "by_source": dict(sorted(by_source.items())),
        "verdicts": dict(sorted(verdicts.items())),
        "subclasses": dict(sorted(subclasses.items())),
        "headline": {
            # headline 1 — strict first-loop round-trip
            "roundtrip_ok": ok,
            "denominator": denom,
            "roundtrip_rate": (ok / denom) if denom else 0.0,
            # headline 2 — fixed point after one normalization loop
            "fixed_point": fixed_point,
            "mismatch_stabilizes": stabilizes,
            "mismatch_divergent": divergent,
            "fixed_point_rate": (fixed_point / denom) if denom else 0.0,
            # headline 3 — G1 gate reading (adopted 2026-08-10): fixed point
            # over the BUILDABLE denominator.  Configs that do not build
            # cannot round-trip by definition — they are the interception
            # story (replay headline), not the round-trip story.
            "buildable": buildable,
            "fixed_point_rate_buildable": (
                (fixed_point / buildable) if buildable else 0.0),
            "build_import_uncertain_excluded": import_uncertain,
            "note": (
                "roundtrip_rate = roundtrip_ok / (total - build_import_uncertain) "
                "is the STRICT first-loop rate; fixed_point_rate = "
                "(roundtrip_ok + mismatch_stabilizes) / (total - "
                "build_import_uncertain) credits mismatches that reach a fixed "
                "point after ONE normalization loop (g3 == g2 — pre-v5.3 partial "
                "serialization, not divergence); genotype-only criterion (runtime "
                "overlay excluded, I6); import failures may be co-located-file "
                "environment artifacts and are excluded from both denominators"
            ),
        },
        "per_item": dict(sorted(per_item.items())),
    }


# ── CLI ──────────────────────────────────────────────────────────────────────


def main(argv: "list[str] | None" = None) -> int:
    parser = argparse.ArgumentParser(
        description="Historical-config roundtrip runner (G1 gate).")
    parser.add_argument("--root", type=Path, action="append", default=None,
                        help="corpus run root(s); repeatable "
                             "(default: recipe/gaia_evolver/runs)")
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)

    roots = args.root or [Path("recipe/gaia_evolver/runs")]
    present = [r for r in roots if r.exists()]
    for r in roots:
        if not r.exists():
            print(f"warning: corpus root not found, skipping: {r}", file=sys.stderr)

    result = run(present)

    print(f"corpus roots: {', '.join(result['roots']) or '(builtin only)'}")
    print(f"items: {result['total']}  "
          f"({', '.join(f'{k}:{v}' for k, v in result['by_source'].items())})")
    for verdict, n in result["verdicts"].items():
        print(f"  {verdict:26s} {n}")
        if verdict == MISMATCH:
            for sub, sn in result["subclasses"].items():
                print(f"    └ {sub:24s} {sn}")
    h = result["headline"]
    print(f"headline roundtrip (strict):  {h['roundtrip_ok']}/{h['denominator']} "
          f"= {h['roundtrip_rate']:.1%}  "
          f"(build_import_uncertain excluded: {h['build_import_uncertain_excluded']})")
    print(f"headline fixed-point:         {h['fixed_point']}/{h['denominator']} "
          f"= {h['fixed_point_rate']:.1%}  "
          f"(+{h['mismatch_stabilizes']} stabilizes, "
          f"{h['mismatch_divergent']} divergent)")
    print(f"headline G1 (buildable):      {h['fixed_point']}/{h['buildable']} "
          f"= {h['fixed_point_rate_buildable']:.1%}  "
          "(non-building configs are the interception story, not roundtrip)")

    if args.out:
        args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2),
                            encoding="utf-8")
        print(f"report written: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
