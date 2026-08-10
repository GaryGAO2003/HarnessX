"""Burned-budget leg of the 保底主读数 (zero-cost replay cost accounting).

The roadmap's replay gate item asks for two numbers: the fraction of
historical candidates the fail-closed gate (S0–S4 + Δ8) would have
intercepted BEFORE evaluation, and **the budget those candidates burned**.
``replay_validator`` produces the first; this tool produces the second by
walking the per-attempt ``*_state.json`` files each evaluation left behind
(``cumulative_cost_usd`` / ``cumulative_tokens`` — the ground truth of
recorded spend; the pool report's "evaluated" matching is only a lower
bound, some intercepted candidates carry attempt states without a pool
report row).

Usage::

    python -m experiments.analysis.burned_budget \
        --replay-report full_corpus_replay.json \
        [--root recipe/gaia_evolver/runs/s1k8b103]* [--out burned.json]

Roots default to every distinct run directory named in the replay report.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

_CAND_RE = re.compile(r"C-R\d+-\d+")


def _intercepted_by_run(report: dict) -> "dict[str, set[str]]":
    out: "dict[str, set[str]]" = {}
    for key, row in report.get("per_candidate", {}).items():
        if not str(row.get("verdict", "")).startswith("intercepted"):
            continue
        run, _, cid = key.rpartition("/")
        out.setdefault(run, set()).add(cid)
    return out


def _walk_states(root: Path) -> "tuple[dict[str, list[float]], list[float], int]":
    """Sum ``(cost_usd, tokens)`` per candidate id + the run total, one walk."""
    per: "dict[str, list[float]]" = {}
    total = [0.0, 0.0]
    n_states = 0
    for sf in root.rglob("*_state.json"):
        try:
            d = json.loads(sf.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        cost = float(d.get("cumulative_cost_usd") or 0.0)
        toks = float(d.get("cumulative_tokens") or 0)
        total[0] += cost
        total[1] += toks
        n_states += 1
        m = _CAND_RE.search(str(sf))
        if m:
            bucket = per.setdefault(m.group(0), [0.0, 0.0])
            bucket[0] += cost
            bucket[1] += toks
    return per, total, n_states


def compute(report_path: Path, roots: "list[Path]") -> dict:
    report = json.loads(report_path.read_text(encoding="utf-8"))
    intercepted = _intercepted_by_run(report)

    runs: dict = {}
    grand = {"burned_usd": 0.0, "burned_tokens": 0.0,
             "run_usd": 0.0, "run_tokens": 0.0}
    for root in roots:
        cids = intercepted.get(root.name, set())
        per, total, n_states = _walk_states(root)
        rows = {c: {"usd": round(per.get(c, [0.0, 0.0])[0], 2),
                    "tokens": int(per.get(c, [0.0, 0.0])[1]),
                    "has_recorded_spend": c in per}
                for c in sorted(cids)}
        burned_usd = sum(r["usd"] for r in rows.values())
        burned_tok = sum(r["tokens"] for r in rows.values())
        runs[root.name] = {
            "intercepted": rows,
            "burned_usd": round(burned_usd, 2),
            "burned_tokens": burned_tok,
            "run_total_usd": round(total[0], 2),
            "run_total_tokens": int(total[1]),
            "attempt_states": n_states,
            "burned_share_of_run_cost": (
                round(burned_usd / total[0], 4) if total[0] else 0.0),
        }
        grand["burned_usd"] += burned_usd
        grand["burned_tokens"] += burned_tok
        grand["run_usd"] += total[0]
        grand["run_tokens"] += total[1]

    return {
        "replay_report": str(report_path),
        "runs": runs,
        "totals": {
            "burned_usd": round(grand["burned_usd"], 2),
            "burned_tokens": int(grand["burned_tokens"]),
            "corpus_recorded_usd": round(grand["run_usd"], 2),
            "corpus_recorded_tokens": int(grand["run_tokens"]),
            "burned_share_of_corpus_cost": (
                round(grand["burned_usd"] / grand["run_usd"], 4)
                if grand["run_usd"] else 0.0),
        },
    }


def main(argv: "list[str] | None" = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--replay-report", type=Path, required=True)
    parser.add_argument("--root", type=Path, action="append", default=None)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)

    report = json.loads(args.replay_report.read_text(encoding="utf-8"))
    roots = args.root
    if not roots:
        # default: every run dir named in the report, resolved against the
        # corpus roots recorded there (root strings are comma-joined).
        corpus = [Path(s.strip()) for s in str(report.get("root", "")).split(",")]
        roots = [p for p in corpus if p.exists()]
    missing = [r for r in roots if not r.exists()]
    if missing:
        print(f"run root not found: {missing}", file=sys.stderr)
        return 2

    result = compute(args.replay_report, roots)

    for run, row in result["runs"].items():
        if not row["intercepted"]:
            continue
        print(f"{run}: burned ${row['burned_usd']:.2f} / "
              f"{row['burned_tokens'] / 1e6:.1f}M tok across "
              f"{len(row['intercepted'])} intercepted candidates "
              f"= {row['burned_share_of_run_cost']:.1%} of run cost "
              f"(${row['run_total_usd']:.2f})")
    t = result["totals"]
    print(f"TOTAL burned: ${t['burned_usd']:.2f} / "
          f"{t['burned_tokens'] / 1e6:.1f}M tok "
          f"= {t['burned_share_of_corpus_cost']:.1%} of corpus recorded cost")

    if args.out:
        args.out.write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"report written: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
