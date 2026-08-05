# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""Per-round regression watchlist (batch-4a Item 3).

Ported from ``upstream/feat/aegis:harnessx/aegis/data/regressions.py``. The
official module reads ``data/task_history.jsonl``; OUR settled per-round pass
state lives in :class:`experiments.variant_pool.ledger.SuccessLedger` (the same
per-round buckets the ``--regression-baseline windowed`` gate reads via
``gate._solved_in_previous_settled_round``), so :func:`detect_regressions`
takes a ledger and reads adjacent rounds through the public ledger API
(``variants`` / ``tasks_of`` / ``aggregate_counts`` with a one-round window)
rather than re-deriving a history file.

Grades (identical to the official ``_grade``):

    regressed_hard      ALL_PASS -> ALL_FAIL      (pass_rate 1.0 -> 0.0)
    regressed_soft      ALL_PASS -> PARTIAL        (1.0 -> 0<c<1)
    regressed_partial   PARTIAL  -> lower / ALL_FAIL (0<p<1 and c<p)

The comparison is *windowed* (adjacent settled rounds only), matching the
official adjacent-round watchlist: a task solved once and since gone stale is
not counted unless it was solved in the immediately-previous round. A task
solved in the previous round and now failing flags ``regressed_hard`` -- the
"luck task" case the official calls out.

Divergence from the official (documented): the official watchlist is whole-batch
and variant-agnostic because ``task_history.jsonl`` carries no variant. OUR
ledger is per-variant, so :func:`_round_flags` sums a task's passes/attempts
across *every* variant for the exact round bucket -- a prior-round solve by any
variant counts, matching the windowed gate's variant-agnostic predicate.
"""

from __future__ import annotations

from pathlib import Path
from typing import Mapping, Sequence

from experiments.variant_pool.ledger import SuccessLedger


def _classify(flags: list[bool] | None) -> str | None:
    if not flags:
        return None
    if all(flags):
        return "ALL_PASS"
    if not any(flags):
        return "ALL_FAIL"
    return "PARTIAL"


def _pass_rate(flags: list[bool] | None) -> float | None:
    if not flags:
        return None
    return sum(flags) / len(flags)


def _grade(prev_flags: list[bool] | None, curr_flags: list[bool] | None) -> str | None:
    """Return the regression grade if there is one, else None (official logic)."""
    p = _pass_rate(prev_flags)
    c = _pass_rate(curr_flags)
    if p is None or c is None:
        return None
    if p == 1.0 and c == 0.0:
        return "regressed_hard"
    if p == 1.0 and 0.0 < c < 1.0:
        return "regressed_soft"
    if 0.0 < p < 1.0 and c < p:
        return "regressed_partial"
    return None


def _round_flags(ledger: SuccessLedger, task_id: str, round_idx: int) -> list[bool] | None:
    """Variant-agnostic pass flags for ``task_id`` in exactly round ``round_idx``.

    Sums passes/attempts for the single round bucket across every variant (the
    windowed one-round selection ``before_round=round_idx+1, window=1`` is the
    same predicate :func:`gate._solved_in_previous_settled_round` uses). Returns
    ``None`` when the task had no attempt that round.
    """
    if round_idx < 0:
        return None
    total_pass = 0
    total_att = 0
    for variant_id in ledger.variants():
        passes, attempts = ledger.aggregate_counts(
            variant_id, (task_id,), before_round=round_idx + 1, window=1
        )
        total_pass += passes
        total_att += attempts
    if total_att == 0:
        return None
    return [True] * total_pass + [False] * (total_att - total_pass)


def detect_regressions(ledger: SuccessLedger, round_n: int) -> list[dict]:
    """Compare round ``round_n-1`` vs ``round_n`` state; one row per regressed task.

    Returns ``[]`` when ``round_n <= 0`` (no prior round to compare against).
    """
    if round_n <= 0:
        return []
    all_tasks: set[str] = set()
    for variant_id in ledger.variants():
        all_tasks |= ledger.tasks_of(variant_id)

    out: list[dict] = []
    for task_id in all_tasks:
        prev = _round_flags(ledger, task_id, round_n - 1)
        curr = _round_flags(ledger, task_id, round_n)
        grade = _grade(prev, curr)
        if grade is None:
            continue
        out.append(
            {
                "task_id": task_id,
                "prev_state": _classify(prev),
                "curr_state": _classify(curr),
                "prev_flags": prev,
                "curr_flags": curr,
                "grade": grade,
            }
        )
    out.sort(
        key=lambda r: (
            {"regressed_hard": 0, "regressed_soft": 1, "regressed_partial": 2}[r["grade"]],
            r["task_id"],
        )
    )
    return out


def render_regressions_md(
    round_n: int,
    regressions: list[dict],
    *,
    joint_suspect_ships: Sequence[Mapping] | None = None,
    for_evolve_round_n: int | None = None,
) -> str:
    """Markdown rendering written to ``R{N}/regressions.md`` (official structure).

    Always emits a body (even when empty) so downstream prompts can reliably
    refer to the file without conditional presence checks. ``joint_suspect_ships``
    is the previous round's shipped candidates (``{ship_id, bucket}`` dicts);
    when ``None``/empty the joint-suspect section is omitted.
    """
    lines: list[str] = []
    if for_evolve_round_n is not None:
        lines.append(f"# Regressions in R{round_n} batch (surfaced before R{for_evolve_round_n} evolve)")
    else:
        lines.append(f"# Regressions detected in R{round_n}")
    lines.append("")
    if for_evolve_round_n is not None:
        lines.append(
            f"Tasks whose pass-state worsened in R{round_n} versus R{round_n - 1}, "
            "computed mechanically from the settled ledger. "
            f"Joint-suspect ships are those that shipped into R{round_n}. "
            f"Evolver/Critic must address these when proposing for R{for_evolve_round_n}."
        )
    else:
        lines.append(
            "Tasks whose pass-state worsened versus the previous settled round, "
            "computed mechanically from the settled ledger. A prior-round solve by "
            "any variant counts (variant-agnostic windowed comparison)."
        )
    lines.append("")

    if not regressions:
        lines.append("_No regressions detected this round._")
        lines.append("")
        return "\n".join(lines)

    lines.append("## Summary")
    lines.append("")
    by_grade: dict[str, list[dict]] = {}
    for r in regressions:
        by_grade.setdefault(r["grade"], []).append(r)
    for grade in ("regressed_hard", "regressed_soft", "regressed_partial"):
        rows = by_grade.get(grade, [])
        if rows:
            ids = ", ".join(f"`{r['task_id'][:8]}`" for r in rows)
            lines.append(f"- **{grade}**: {len(rows)} — {ids}")
    lines.append("")

    ships = list(joint_suspect_ships or [])
    if ships:
        lines.append(f"## Joint-suspect ships from R{round_n}")
        lines.append("")
        for s in ships:
            lines.append(f"- `{s.get('ship_id')}` (bucket=`{s.get('bucket')}`)")
        lines.append("")

    lines.append("## Per-task detail")
    lines.append("")
    lines.append("| task_id | prev | curr | grade | prev_flags → curr_flags |")
    lines.append("|---|---|---|---|---|")
    for r in regressions:
        lines.append(
            f"| `{r['task_id']}` | {r['prev_state']} | {r['curr_state']} "
            f"| {r['grade']} | {r['prev_flags']} → {r['curr_flags']} |"
        )
    lines.append("")
    lines.append(
        "**Required action**: the Evolver MUST either include at least one "
        "candidate that addresses these regressions OR explicitly state in the "
        "journal/manifest why the regression is acceptable / transient / out of "
        "scope. The Critic verifies this."
    )
    return "\n".join(lines)


def write_regressions_md(
    run_root: Path,
    ledger: SuccessLedger,
    *,
    compare_round: int,
    for_evolve_round_n: int | None = None,
    joint_suspect_ships: Sequence[Mapping] | None = None,
) -> tuple[Path, str]:
    """Compute + write ``R{target}/regressions.md``; return ``(path, rendered_md)``.

    ``compare_round`` is the last settled round (``round_n``); regressions are
    ``compare_round-1 -> compare_round``. ``for_evolve_round_n`` (when given)
    selects the output round dir (``R{for_evolve_round_n}``) and the title,
    matching the official orchestrator shape that emits next to the evolve round
    whose batch has not yet run.
    """
    regressions = detect_regressions(ledger, compare_round)
    rendered = render_regressions_md(
        compare_round,
        regressions,
        joint_suspect_ships=joint_suspect_ships,
        for_evolve_round_n=for_evolve_round_n,
    )
    target_round = for_evolve_round_n if for_evolve_round_n is not None else compare_round
    out_path = run_root / f"R{target_round}" / "regressions.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(rendered, encoding="utf-8")
    return out_path, rendered
