"""Zero-cost validator replay over a historical candidate corpus (保底主读数).

Runs every historical candidate config through the P3 gate (S0–S4 + Δ8,
fail-closed) WITHOUT any model call, and reports the fraction that would
have been intercepted BEFORE evaluation — plus, where the pool report
carries cost data, the budget those interceptable candidates burned.

Fairness note (thesis-reading integrity): a build ImportError on a replayed
config may be an environment artifact (the candidate's co-located processor
file existed in ITS run workspace, not on today's path).  Import failures
are therefore reported SEPARATELY as ``build_import_uncertain`` and are NOT
counted into the headline interception rate; the headline uses only
verdicts that are environment-independent: static S0–S3, Δ8 DFA witnesses,
and build conflicts (HarnessConflictError-class).

Usage::

    python -m experiments.analysis.replay_validator \
        [--root recipe/gaia_evolver/runs/e_pervar3] [--out replay_report.json]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

from experiments.variant_pool.graph_gate import validate_candidate_graph

_CAND_RE = re.compile(r"C-R\d+-\d+")


def discover_candidates(root: Path) -> "dict[str, Path]":
    """candidate_id → canonical config path.

    Recursive across every layout a run may use (pipeline products,
    candidate_gate copies, bounce/retry re-attempts, nested
    ``C-*/output_dir/config.yaml``); any config.yaml whose path carries a
    candidate id counts.  Per candidate id the SHORTEST path wins — that is
    the canonical product, re-attempt/nested copies live deeper.
    """
    found: dict[str, Path] = {}
    for path in sorted(root.rglob("config.yaml"),
                       key=lambda p: (len(p.parts), str(p))):
        m = _CAND_RE.search(str(path))
        if m:
            found.setdefault(m.group(0), path)
    return found


def _classify(report) -> str:
    """Environment-fair verdict class for one gate report."""
    if report.passed:
        return "passed"
    types = [e.error_type for e in report.errors]
    if any(t.startswith("dfa_") for t in types):
        return "intercepted_dfa"
    if any(t == "build_conflict" for t in types):
        return "intercepted_build_conflict"
    if all(t == "build_failed" for t in types):
        # distinguish import-shaped failures (environment-uncertain) from
        # other build explosions
        if any("ImportError" in e.message or "ModuleNotFoundError" in e.message
               for e in report.errors):
            return "build_import_uncertain"
        return "intercepted_build_other"
    return "intercepted_static"  # S0–S3 layer rejections (and config_load)


def _evaluated_ids(root: Path) -> "set[str]":
    """Best-effort: candidate ids that reached evaluation, from pool_report."""
    report_path = root / "pool_report.json"
    if not report_path.exists():
        return set()
    try:
        data = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()
    ids: set[str] = set()

    def _walk(obj):
        if isinstance(obj, str):
            for m in _CAND_RE.finditer(obj):
                ids.add(m.group(0))
        elif isinstance(obj, dict):
            for k, v in obj.items():
                _walk(k)
                _walk(v)
        elif isinstance(obj, (list, tuple)):
            for v in obj:
                _walk(v)

    _walk(data.get("candidate_diagnostics", {}))
    return ids


def replay(root: Path) -> dict:
    return replay_many([root])


def replay_many(roots: "list[Path]") -> dict:
    per_candidate: dict = {}
    verdicts: Counter = Counter()
    error_types: Counter = Counter()

    for root in roots:
        candidates = discover_candidates(root)
        evaluated = _evaluated_ids(root)
        for cid in sorted(candidates):
            key = f"{root.name}/{cid}"  # ids repeat across runs
            path = candidates[cid]
            report = validate_candidate_graph(path)
            verdict = _classify(report)
            verdicts[verdict] += 1
            for e in report.errors:
                error_types[e.error_type] += 1
            per_candidate[key] = {
                "config": str(path.relative_to(root)),
                "verdict": verdict,
                "passed": report.passed,
                "error_types": sorted({e.error_type for e in report.errors}),
                "evaluated": cid in evaluated,
            }

    total = len(per_candidate)
    interceptable = sum(v for k, v in verdicts.items()
                        if k.startswith("intercepted_"))
    interceptable_evaluated = sum(
        1 for row in per_candidate.values()
        if row["evaluated"] and row["verdict"].startswith("intercepted_"))

    return {
        "root": ", ".join(str(r) for r in roots),
        "candidates": total,
        "evaluated_matched": sum(1 for r in per_candidate.values() if r["evaluated"]),
        "verdicts": dict(sorted(verdicts.items())),
        "headline": {
            "interceptable": interceptable,
            "interceptable_rate": (interceptable / total) if total else 0.0,
            "interceptable_among_evaluated": interceptable_evaluated,
            "note": ("headline excludes build_import_uncertain — import "
                     "failures may be co-located-file environment artifacts"),
        },
        "error_types": dict(sorted(error_types.items())),
        "per_candidate": per_candidate,
    }


def main(argv: "list[str] | None" = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, action="append", default=None,
                        help="corpus run root(s); repeatable")
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)

    roots = args.root or [Path("recipe/gaia_evolver/runs/e_pervar3")]
    missing = [r for r in roots if not r.exists()]
    if missing:
        print(f"corpus root not found: {missing}", file=sys.stderr)
        return 2

    result = replay_many(roots)

    print(f"corpus: {result['root']}")
    print(f"candidates discovered: {result['candidates']} "
          f"(evaluated-matched: {result['evaluated_matched']})")
    for verdict, n in result["verdicts"].items():
        print(f"  {verdict:28s} {n}")
    h = result["headline"]
    print(f"headline interceptable: {h['interceptable']}/{result['candidates']} "
          f"= {h['interceptable_rate']:.1%}  "
          f"(among evaluated: {h['interceptable_among_evaluated']})")

    if args.out:
        args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2),
                            encoding="utf-8")
        print(f"report written: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
