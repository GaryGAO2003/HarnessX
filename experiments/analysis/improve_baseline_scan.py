"""Improve/regress baseline asymmetry scan (M-2x, Aug-01).

WHAT THIS MEASURES
------------------
``variant_pool/gate.py::_classify`` judges the two sides of the APPLY / FORK /
REJECT decision against *different* histories (the module docstring says so
explicitly: "the two sides use different baselines"):

* **improved** requires ``before_passes == 0``, where ``before`` is
  ``SuccessLedger.cell(variant_id, task_id)`` and ``record()`` does
  ``cell.passes += n_pass`` -- i.e. the *cumulative* pass count.  So a task is
  improvable only while the variant has **never** passed it in any round.
* **regressed** requires ``after_passes == 0`` and
  ``SuccessLedger.ever_solved``, which ``ledger.py`` documents as
  "once solved it stays in ever_solved **forever**".

Both pools are therefore monotone, in opposite directions: the improvable pool
only shrinks, the regressable pool only grows.  A single lucky pass -- including
one produced by pass@2 sampling noise -- **irreversibly** removes a task from
the improvable side and adds it to the regressable side.

This script quantifies that drift from ``data/task_history.jsonl`` alone, and
replays the same classifier under the counterfactual ``last_round`` baseline
(improved = failed *last round*; regressed = passed *last round*).

SCOPE -- READ BEFORE CITING
---------------------------
The decision columns are a **structural proxy, not a gate replay**.  They apply
the classifier to the round-over-round diff of each variant's *deployed* config,
whereas the real gate saw *candidate* evaluations (under
``R<n>/V<k>/pipeline/candidates/``).  The decisions below are therefore NOT the
decisions the gate actually made.

The two eligibility-pool columns (``elig_cum`` / ``elig_last``) ARE exact: they
depend only on the deployed history and not on any candidate, so
"variant V0's improvable pool reached zero at round R12" is a hard fact.

Usage::

    python improve_baseline_scan.py --run <run_dir> [--out <dir>]
"""

from __future__ import annotations

import argparse
import collections
import json
from pathlib import Path

DECISIONS = ("APPLY", "FORK", "REJECT")


def _decide(improved: set[str], regressed: set[str]) -> str:
    """Mirror ``variant_pool/gate.py::_decide`` at ``min_fork == (1, 1)``."""
    if not improved:
        return "REJECT"
    if not regressed:
        return "APPLY"
    return "FORK"


def load_history(run_dir: Path) -> dict[int, dict[tuple[str, str], tuple[int, int]]]:
    """``round -> (variant, task) -> (n_pass, n_att)`` from task_history.jsonl."""
    path = run_dir / "data" / "task_history.jsonl"
    if not path.is_file():
        raise SystemExit(f"no task_history.jsonl under {run_dir}")
    hist: dict[int, dict[tuple[str, str], tuple[int, int]]] = collections.defaultdict(dict)
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            hist[d["round_idx"]][(d["variant_id"], d["task_id"])] = tuple(d["outcome"])
    return hist


def scan(hist: dict[int, dict[tuple[str, str], tuple[int, int]]]) -> list[dict]:
    """One row per (round, variant), replaying both baselines in round order."""
    cum: dict[tuple[str, str], int] = collections.defaultdict(int)  # cumulative passes
    last: dict[tuple[str, str], int] = {}                          # previous round's passes
    ever: set[str] = set()                                         # global ever_solved
    rows: list[dict] = []

    for rnd in sorted(hist):
        cells = hist[rnd]
        by_variant: dict[str, list[tuple[str, tuple[int, int]]]] = collections.defaultdict(list)
        for (variant, task), outcome in cells.items():
            by_variant[variant].append((task, outcome))

        for variant in sorted(by_variant):
            items = by_variant[variant]
            imp_cum = {t for t, o in items if cum[(variant, t)] == 0 and o[0] >= 1}
            imp_last = {t for t, o in items if last.get((variant, t), 0) == 0 and o[0] >= 1}
            reg_ever = {t for t, o in items if o[0] == 0 and t in ever}
            reg_last = {t for t, o in items if o[0] == 0 and last.get((variant, t), 0) >= 1}
            rows.append(
                {
                    "round": rnd,
                    "variant": variant,
                    "n_tasks": len(items),
                    # exact, candidate-independent
                    "elig_cum": sum(1 for t, _ in items if cum[(variant, t)] == 0),
                    "elig_last": sum(1 for t, _ in items if last.get((variant, t), 0) == 0),
                    # structural proxy
                    "improved_cum": len(imp_cum),
                    "improved_last": len(imp_last),
                    "regressed_ever": len(reg_ever),
                    "regressed_last": len(reg_last),
                    "decision_cum": _decide(imp_cum, reg_ever),
                    "decision_last": _decide(imp_last, reg_last),
                }
            )

        # fold this round in only AFTER every variant of the round is judged
        for (variant, task), outcome in cells.items():
            cum[(variant, task)] += outcome[0]
            last[(variant, task)] = outcome[0]
            if outcome[0] >= 1:
                ever.add(task)

    return rows


def exhaustion_points(rows: list[dict]) -> dict[str, int | None]:
    """First round at which each variant's cumulative improvable pool hits zero."""
    out: dict[str, int | None] = {}
    for row in rows:
        v = row["variant"]
        if out.get(v) is None and row["elig_cum"] == 0:
            out[v] = row["round"]
        out.setdefault(v, None)
    return out


def render(rows: list[dict], run_name: str) -> str:
    tally = {
        "cum": collections.Counter(r["decision_cum"] for r in rows),
        "last": collections.Counter(r["decision_last"] for r in rows),
    }
    disagree = [r for r in rows if r["decision_cum"] != r["decision_last"]]
    exhaust = exhaustion_points(rows)

    lines = [
        f"# Improve-baseline asymmetry scan -- `{run_name}`",
        "",
        "> `elig_*` columns are exact. Decision columns are a **structural proxy on the",
        "> deployed round-over-round diff**, not a replay of the gate's candidate",
        "> evaluations -- see the module docstring before citing them.",
        "",
        "| round | variant | tasks | elig_cum | elig_last | imp_cum | imp_last | reg_ever | reg_last | dec_cum | dec_last |",
        "|------:|:--------|------:|---------:|----------:|--------:|---------:|---------:|---------:|:--------|:---------|",
    ]
    for r in rows:
        flag = " **!=**" if r["decision_cum"] != r["decision_last"] else ""
        lines.append(
            f"| {r['round']} | {r['variant']} | {r['n_tasks']} | **{r['elig_cum']}** | {r['elig_last']} | "
            f"{r['improved_cum']} | {r['improved_last']} | {r['regressed_ever']} | {r['regressed_last']} | "
            f"{r['decision_cum']} | {r['decision_last']}{flag} |"
        )

    lines += [
        "",
        "## Decision tally",
        "",
        "| baseline | APPLY | FORK | REJECT |",
        "|:---------|------:|-----:|-------:|",
        f"| cumulative (current) | {tally['cum']['APPLY']} | {tally['cum']['FORK']} | {tally['cum']['REJECT']} |",
        f"| last_round (counterfactual) | {tally['last']['APPLY']} | {tally['last']['FORK']} | {tally['last']['REJECT']} |",
        "",
        f"Disagreement: **{len(disagree)}/{len(rows)}** "
        f"({100.0 * len(disagree) / max(len(rows), 1):.0f}%) of (round, variant) cells.",
        "",
        "## Improvable-pool exhaustion (exact)",
        "",
        "| variant | first round with elig_cum == 0 |",
        "|:--------|:-------------------------------|",
    ]
    for v in sorted(exhaust):
        r = exhaust[v]
        lines.append(f"| {v} | {'never' if r is None else f'R{r}'} |")
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--run", required=True, type=Path, help="run dir containing data/task_history.jsonl")
    ap.add_argument("--out", type=Path, default=None, help="output dir (default: experiments/analysis/out/<run>)")
    args = ap.parse_args()

    run_dir = args.run.resolve()
    rows = scan(load_history(run_dir))
    out_dir = args.out or (Path(__file__).parent / "out" / run_dir.name)
    out_dir.mkdir(parents=True, exist_ok=True)

    md = render(rows, run_dir.name)
    (out_dir / "improve_baseline_scan.md").write_text(md, encoding="utf-8")
    (out_dir / "improve_baseline_scan.json").write_text(
        json.dumps(
            {"run": run_dir.name, "rows": rows, "exhaustion": exhaustion_points(rows)},
            indent=2,
        ),
        encoding="utf-8",
    )
    print(md)
    print(f"\n[written] {out_dir / 'improve_baseline_scan.md'}")
    print(f"[written] {out_dir / 'improve_baseline_scan.json'}")


if __name__ == "__main__":
    main()
