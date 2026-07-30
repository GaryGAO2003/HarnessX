"""Extract the per-round pass@2 curve from a variant-pool run (read-only).

Scans every ``R{n}/pool_state.json`` settlement snapshot under a run directory and
prints one row per round: round, variant count, pool aggregate pass@2 %
(numerator/denominator), each variant's pass@2, shipped / forked / retired / idle,
and the key ``candidate_accounting`` fields. In-flight runs are tolerated: a round
whose snapshot is missing or still being written is reported as ``pending`` and
skipped rather than crashing the scan.

Usage::

    python experiments/analysis/curve_extract.py --run-dir recipe/gaia_evolver/runs/s1k8
    python experiments/analysis/curve_extract.py --run-dir recipe/gaia_evolver/runs/s1k8 --csv out.csv
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

sys.dont_write_bytecode = True  # keep experiments/analysis/ free of __pycache__

import _poolscan as ps

# candidate_accounting fields surfaced in the table / CSV (best-effort; absent
# keys render blank).
ACCOUNTING_FIELDS = (
    "requested_slots",
    "actual_candidates",
    "valid_considered",
    "evaluated",
    "gate_rejected",
    "selected",
    "skipped",
)


def _fmt_per_variant(rd: ps.RoundData) -> str:
    parts = []
    for variant, (vpass, total) in rd.per_variant.items():
        pct = 100.0 * vpass / total if total else 0.0
        parts.append(f"{variant}:{vpass}/{total}({pct:.1f}%)")
    return " ".join(parts)


def _accounting_cell(rd: ps.RoundData) -> str:
    ca = rd.candidate_accounting
    bits = [f"{k}={ca[k]}" for k in ACCOUNTING_FIELDS if k in ca]
    return " ".join(bits)


def build_rows(rounds: list[ps.RoundData]) -> list[dict]:
    rows: list[dict] = []
    for rd in rounds:
        if not rd.ok:
            rows.append(
                {
                    "round": rd.round,
                    "status": "pending",
                    "variant_count": "",
                    "pool_pass_at_2_pct": "",
                    "pool_pass": "",
                    "denominator": "",
                    "per_variant": "",
                    "shipped": "",
                    "forked": "",
                    "retired": "",
                    "idle": "",
                    "candidate_accounting": rd.error or "",
                }
            )
            continue
        rows.append(
            {
                "round": rd.round,
                "status": "settled",
                "variant_count": rd.variant_count,
                "pool_pass_at_2_pct": f"{rd.pool_pct:.1f}",
                "pool_pass": rd.pool_pass,
                "denominator": rd.denominator,
                "per_variant": _fmt_per_variant(rd),
                "shipped": rd.shipped,
                "forked": ",".join(rd.forked),
                "retired": ",".join(rd.retired),
                "idle": rd.idle,
                "candidate_accounting": _accounting_cell(rd),
            }
        )
    return rows


def print_table(tag: str, rows: list[dict]) -> None:
    print(f"# variant-pool curve  |  run={tag}")
    header = (
        f"{'Rnd':>3}  {'st':<7} {'vc':>2} {'pass@2%':>8} {'num/den':>9}  "
        f"{'ship':<5} {'fork':<8} {'retire':<8} {'idle':>4}  per-variant / accounting"
    )
    print(header)
    print("-" * len(header))
    for r in rows:
        if r["status"] == "pending":
            print(f"{r['round']:>3}  {'pending':<7}  (in-flight / unreadable: {r['candidate_accounting']})")
            continue
        numden = f"{r['pool_pass']}/{r['denominator']}"
        print(
            f"{r['round']:>3}  {r['status']:<7} {r['variant_count']:>2} "
            f"{r['pool_pass_at_2_pct']:>8} {numden:>9}  "
            f"{str(r['shipped']):<5} {r['forked']:<8} {r['retired']:<8} {r['idle']:>4}  "
            f"{r['per_variant']}"
        )
        if r["candidate_accounting"]:
            print(f"{'':>3}  {'':<7} {'':>2} {'':>8} {'':>9}  -> accounting: {r['candidate_accounting']}")


def write_csv(path: Path, rows: list[dict]) -> None:
    fields = [
        "round",
        "status",
        "variant_count",
        "pool_pass_at_2_pct",
        "pool_pass",
        "denominator",
        "per_variant",
        "shipped",
        "forked",
        "retired",
        "idle",
        "candidate_accounting",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Extract per-round pass@2 curve from a variant-pool run (read-only).")
    parser.add_argument("--run-dir", required=True, help="Path to runs/<tag> (e.g. recipe/gaia_evolver/runs/s1k8)")
    parser.add_argument("--csv", help="Optional path to write the curve as CSV")
    args = parser.parse_args(argv)

    run_dir = Path(args.run_dir)
    if not run_dir.is_dir():
        print(f"error: run-dir not found: {run_dir}", file=sys.stderr)
        return 2

    tag = ps.run_tag(run_dir)
    rounds = ps.scan_run(run_dir)
    if not rounds:
        print(f"error: no R*/pool_state.json rounds under {run_dir}", file=sys.stderr)
        return 1

    rows = build_rows(rounds)
    print_table(tag, rows)

    settled = [r for r in rounds if r.ok]
    pending = [r for r in rounds if not r.ok]
    stats = ps.curve_stats(rounds)
    print("-" * 40)
    if settled:
        print(
            f"settled={len(settled)} pending={len(pending)}  "
            f"peak=R{stats['peak_round']} {stats['peak_pct']:.1f}%  "
            f"final=R{stats['final_round']} {stats['final_pct']:.1f}%  "
            f"drift(final-peak)={stats['drift']:+.1f}%"
        )
    if pending:
        print(f"pending rounds (in-flight / unreadable): {', '.join('R' + str(r.round) for r in pending)}")

    if args.csv:
        csv_path = Path(args.csv)
        write_csv(csv_path, rows)
        print(f"wrote CSV -> {csv_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
