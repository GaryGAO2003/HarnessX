# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""Pinned replay corpus + strict decision cut + full-S4 leg (保底主读数 glue).

Three conventions this module nails down (previously散文-only, numbers drifted):

1. **Corpus manifest** — the thesis replay corpus is the pinned, on-disk root
   list below, with per-root expected config-bearing candidate counts asserted
   at runtime (drift prints loudly, never silently).  VP-era roots (K=8
   variant-pool campaign, the corpus doc-12 measured) are the headline;
   post-restructure roots (K=1/GHX era) are census-only and excluded from the
   headline unless ``--include-post-era``.  Reconciliation vs the two older
   claims: doc-12's 142 (2026-08-05) counted runs deleted since (``a1big4`` at
   least; doc-13's contributing-run list has 5 roots gone today:
   ``a1pilot2 a1pilot3 forceprobe2 paper4 smoke_hard2``); the replay_validator
   docstring's "140 across 11 surviving roots" (2026-08-10) predates further
   deletions/additions.  Neither is reconstructible; THIS list is.

2. **Strict evaluated cut** — "evaluated" means ``decision ∈ {reject, fork,
   apply}`` read from diagnostics rows, NOT the regex mention-matching
   ``replay_validator._evaluated_ids`` uses.  Rows are merged from BOTH
   schemas: the final ``pool_report.json`` (``candidate_diagnostics.candidates``
   is a LIST of rows keyed by ``candidate_id``) and every per-round
   ``R*/pool_state.json`` (``candidate_diagnostics`` is a DICT cid → row).
   A non-null decision always beats null; among non-null, the later round wins.
   Candidates present in diagnostics but with no config on disk are the
   ``metadata_only`` tier — reported separately, never in the replay
   denominator (they died before a config existed; S0–S4/Δ8 never saw them).

3. **Full S4** — ``graph_gate.validate_candidate_graph``'s build step is
   S4-build-only.  The ``s4_full`` leg here additionally runs each candidate
   config through ``roundtrip_validator.roundtrip_config`` (``transactional_
   apply`` + re-graph genotype stability), so the thesis can write
   "S0–S4 全量 + Δ8" and mean it on this exact corpus.

Usage::

    python -m experiments.analysis.replay_corpus [--include-post-era]
        [--skip-s4] [--out FILE.json]

Chain the budget leg on the written report::

    python -m experiments.analysis.burned_budget --replay-report FILE.json
        --root <each corpus root>
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

from experiments.analysis.replay_validator import discover_candidates, replay_many

#: VP-era (K=8) surviving roots — the headline corpus.  name → expected
#: config-bearing candidate count (census 2026-08-12).
VP_ERA_ROOTS: dict[str, int] = {
    "e_pervar3": 41,
    "s1k8": 10,
    "s1k8b103": 36,
    "s2k8b50": 32,
    "a1big5": 6,
    "organic1": 4,
}

#: Post-restructure roots (K=1 / GHX / calibration era).  Census-only.
POST_ERA_ROOTS: dict[str, int] = {
    "z_6x3": 4,
    "aprime_dress2": 8,
    "gaia_calib6_ds": 1,
    "MetaPro_smoke_evolver": 1,
}

_DEFAULT_BASE = Path("recipe/gaia_evolver/runs")
_ROUND_DIR_RE = re.compile(r"^R(\d+)$")


def corpus_roots(base: Path, include_post_era: bool = False) -> list[Path]:
    """Expand the pinned manifest under *base*; a missing root is an error."""
    names = list(VP_ERA_ROOTS)
    if include_post_era:
        names += list(POST_ERA_ROOTS)
    roots = [base / n for n in names]
    missing = [str(r) for r in roots if not r.exists()]
    if missing:
        raise FileNotFoundError(f"pinned corpus root(s) missing: {missing}")
    return roots


def census_drift(base: Path, include_post_era: bool = False) -> dict[str, tuple[int, int]]:
    """name → (expected, found) for every root whose count drifted."""
    expected = dict(VP_ERA_ROOTS)
    if include_post_era:
        expected.update(POST_ERA_ROOTS)
    drift: dict[str, tuple[int, int]] = {}
    for name, exp in expected.items():
        found = len(discover_candidates(base / name))
        if found != exp:
            drift[name] = (exp, found)
    return drift


def _rows_from_final_report(root: Path) -> "dict[str, dict]":
    """Final ``pool_report.json`` rows (list schema), keyed by candidate id."""
    path = root / "pool_report.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    rows = data.get("candidate_diagnostics", {})
    if isinstance(rows, dict):
        rows = rows.get("candidates", [])
    out: dict[str, dict] = {}
    for row in rows if isinstance(rows, list) else []:
        cid = row.get("candidate_id")
        if cid:
            out[cid] = row
    return out


def _rows_from_round_states(root: Path) -> "dict[str, tuple[int, dict]]":
    """Per-round ``pool_state.json`` rows (dict schema), keyed by candidate id.

    Returns cid → (round_number, row); among rounds carrying the same cid the
    LATER round wins (the diagnostics dict is rewritten per round).
    """
    out: dict[str, tuple[int, dict]] = {}
    for rdir in sorted(root.iterdir()) if root.exists() else []:
        m = _ROUND_DIR_RE.match(rdir.name)
        if not m or not rdir.is_dir():
            continue
        rnum = int(m.group(1))
        path = rdir / "pool_state.json"
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        diags = data.get("candidate_diagnostics", {})
        if not isinstance(diags, dict):
            continue
        for cid, row in diags.items():
            if not isinstance(row, dict):
                continue
            prev = out.get(cid)
            if prev is None or rnum >= prev[0]:
                out[cid] = (rnum, row)
    return out


def merge_decisions(root: Path) -> "dict[str, dict]":
    """cid → merged decision record for one run root.

    Sources: final report rows ∪ round-state rows.  A non-null ``decision``
    always beats null; between two non-null sources the round-state (per-round,
    closer to the event) wins only via its later-round rewrite — the final
    report, when it carries a non-null decision, is written last and wins.
    """
    merged: dict[str, dict] = {}
    for cid, (rnum, row) in _rows_from_round_states(root).items():
        merged[cid] = {
            "decision": row.get("decision"),
            "evaluated_flag": bool(row.get("evaluated")),
            "source": f"pool_state:R{rnum}",
        }
    for cid, row in _rows_from_final_report(root).items():
        rec = merged.setdefault(
            cid, {"decision": None, "evaluated_flag": False, "source": ""}
        )
        if row.get("decision") is not None or rec["decision"] is None:
            rec["decision"] = row.get("decision", rec["decision"])
            rec["source"] = "pool_report"
        rec["evaluated_flag"] = rec["evaluated_flag"] or bool(row.get("evaluated"))
    return merged


def full_s4(config_path: Path) -> "tuple[str, dict]":
    """Full-S4 verdict (transactional_apply + re-graph stability) for one config."""
    from experiments.analysis.roundtrip_validator import roundtrip_config
    from harnessx.core.harness import HarnessConfig

    try:
        config = HarnessConfig.from_yaml_file(str(config_path))
    except Exception as exc:  # noqa: BLE001 — fail closed, mirrors the gate
        return "config_load_failed", {"error": f"{type(exc).__name__}: {exc}"[:200]}
    return roundtrip_config(config)


def run_corpus(
    base: Path, include_post_era: bool = False, skip_s4: bool = False
) -> dict:
    roots = corpus_roots(base, include_post_era)
    result = replay_many(roots)

    # Strict decision merge + metadata-only tier, per root.
    strict = Counter()
    metadata_only: list[dict] = []
    s4_verdicts: Counter = Counter()
    for root in roots:
        decisions = merge_decisions(root)
        configs = discover_candidates(root)
        for cid, rec in decisions.items():
            if cid not in configs:
                metadata_only.append(
                    {"root": root.name, "candidate_id": cid, **rec}
                )
        for cid, path in configs.items():
            key = f"{root.name}/{cid}"
            row = result["per_candidate"].get(key)
            if row is None:
                continue
            rec = decisions.get(cid)
            decision = rec["decision"] if rec else None
            row["decision"] = decision
            row["decision_source"] = rec["source"] if rec else ""
            row["evaluated_strict"] = decision is not None
            strict[decision or "none"] += 1
            if not skip_s4:
                verdict, detail = full_s4(path)
                row["s4_full"] = verdict
                if verdict != "roundtrip_ok":
                    row["s4_detail"] = {
                        k: v for k, v in detail.items() if k != "genotype"
                    }
                s4_verdicts[verdict] += 1

    rows = result["per_candidate"].values()
    interceptable_strict = sum(
        1
        for r in rows
        if r.get("evaluated_strict") and r["verdict"].startswith("intercepted_")
    )
    result["strict_decisions"] = dict(sorted(strict.items()))
    result["metadata_only"] = metadata_only
    result["headline"]["evaluated_strict"] = sum(
        1 for r in rows if r.get("evaluated_strict")
    )
    result["headline"]["interceptable_among_evaluated_strict"] = interceptable_strict
    if not skip_s4:
        result["s4_full_verdicts"] = dict(sorted(s4_verdicts.items()))
    result["manifest"] = {
        "vp_era": dict(VP_ERA_ROOTS),
        "post_era_included": include_post_era,
        "census_drift": {
            k: {"expected": e, "found": f}
            for k, (e, f) in census_drift(base, include_post_era).items()
        },
    }
    return result


def main(argv: "list[str] | None" = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", type=Path, default=_DEFAULT_BASE)
    parser.add_argument("--include-post-era", action="store_true")
    parser.add_argument("--skip-s4", action="store_true")
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)

    try:
        result = run_corpus(args.base, args.include_post_era, args.skip_s4)
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    drift = result["manifest"]["census_drift"]
    if drift:
        print(f"⚠ census drift vs pinned manifest: {drift}")
    print(f"corpus roots: {result['root']}")
    print(
        f"candidates: {result['candidates']}  "
        f"metadata-only (excluded): {len(result['metadata_only'])}"
    )
    print(f"strict decisions: {result['strict_decisions']}")
    for verdict, n in result["verdicts"].items():
        print(f"  {verdict:28s} {n}")
    h = result["headline"]
    print(
        f"headline interceptable: {h['interceptable']}/{result['candidates']} "
        f"= {h['interceptable_rate']:.1%}  (among strict-evaluated "
        f"{h['evaluated_strict']}: {h['interceptable_among_evaluated_strict']})"
    )
    if "s4_full_verdicts" in result:
        print(f"s4_full: {result['s4_full_verdicts']}")

    if args.out:
        args.out.write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"report written: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
