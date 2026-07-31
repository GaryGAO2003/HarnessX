"""Generate a Markdown acceptance report for a variant-pool run (read-only).

Combines the per-round pass@2 curve (from ``R{n}/pool_state.json``) with a
best-effort scrape of the run's console log, and writes
``experiments/analysis/out/<tag>/report.md``. Only the settled rounds are
reported; an in-flight run is annotated as such and never crashes the tool.

Usage::

    python experiments/analysis/acceptance_report.py --run-dir recipe/gaia_evolver/runs/s1k8
    python experiments/analysis/acceptance_report.py --run-dir recipe/gaia_evolver/runs/s1k8b103 \\
        --console-log recipe/gaia_evolver/runs/s1k8b103.console.log
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path

sys.dont_write_bytecode = True  # keep experiments/analysis/ free of __pycache__

import _poolscan as ps
import deep_metrics as dm

# M-25 noise band: a round-over-round delta with |Δ| <= this (percentage points)
# is treated as measurement noise rather than a real move.
NOISE_BAND_PP = 5.0

_DATE_RE = re.compile(r"(\d{4})/(\d{2})/(\d{2})")
_TIME_RE = re.compile(r"(\d{1,2}):(\d{2}):(\d{2})(?:\.(\d+))?")
# Round/phase activity tags: "[R5]" (round-level) and "[R0-V0-active]" (per-task).
_ROUND_MARK_RE = re.compile(r"\[R\d+")
_EXIT_CODE_RE = re.compile(r"LAUNCHER_EXIT\s+code=(-?\d+)")


# --------------------------------------------------------------------------- #
# Console-log scraping
# --------------------------------------------------------------------------- #
def _parse_launcher_dt(line: str) -> dt.datetime | None:
    """Parse the ``YYYY/MM/DD <weekday> H:MM:SS.cc`` stamp on a LAUNCHER line.

    The weekday token is a locale string (often mojibake in the captured log), so
    we pick the date and time tokens by pattern and ignore everything between.
    """
    dm = _DATE_RE.search(line)
    tm = _TIME_RE.search(line)
    if not (dm and tm):
        return None
    y, mo, d = (int(x) for x in dm.groups())
    hh, mm, ss = (int(tm.group(i)) for i in (1, 2, 3))
    frac = tm.group(4)
    micro = int(float("0." + frac) * 1_000_000) if frac else 0
    try:
        return dt.datetime(y, mo, d, hh, mm, ss, micro)
    except ValueError:
        return None


def scan_console_log(path: Path) -> dict:
    """Grep-style stats over a console log; tolerant of ANSI codes and bad bytes.

    Every counter is defined against a marker the runner actually emits (see the
    inline notes); missing markers yield 0 rather than an error.
    """
    stats: dict = {
        "path": str(path),
        "exists": False,
        "lines": 0,
        "serper_search_failed": 0,  # logger.warning("Serper search failed: ...")
        "serper_mentions": 0,  # any line naming serper (case-insensitive)
        "traceback": 0,  # "Traceback (most recent call last):"
        "error_level": 0,  # harness "[ERROR" log-level lines
        "budget_grant_lines": 0,  # per-candidate "budget=$N" evolve grants
        "budget_exhaust_lines": 0,  # explicit "budget ... exhaust/exceed" phrasing
        "round_markers": 0,  # "[Rn]" activity lines
        "launcher_start": None,
        "launcher_exit": None,
        "exit_code": None,
        "wall_clock_seconds": None,
    }
    if not path or not Path(path).exists():
        return stats
    stats["exists"] = True

    raw = Path(path).read_bytes().decode("utf-8", errors="replace")
    for line in raw.splitlines():
        line = ps.strip_ansi(line)
        stats["lines"] += 1
        low = line.lower()
        if "serper search failed" in low:
            stats["serper_search_failed"] += 1
        if "serper" in low:
            stats["serper_mentions"] += 1
        if line.startswith("Traceback (most recent call last)"):
            stats["traceback"] += 1
        if "[ERROR" in line:
            stats["error_level"] += 1
        if "budget=$" in line:
            stats["budget_grant_lines"] += 1
        if "budget" in low and ("exhaust" in low or "exceed" in low or "over budget" in low):
            stats["budget_exhaust_lines"] += 1
        if _ROUND_MARK_RE.search(line):
            stats["round_markers"] += 1
        if "LAUNCHER_START" in line and stats["launcher_start"] is None:
            stats["launcher_start"] = _parse_launcher_dt(line)
        if "LAUNCHER_EXIT" in line:
            stats["launcher_exit"] = _parse_launcher_dt(line)
            m = _EXIT_CODE_RE.search(line)
            if m:
                stats["exit_code"] = int(m.group(1))

    if stats["launcher_start"] and stats["launcher_exit"]:
        stats["wall_clock_seconds"] = (stats["launcher_exit"] - stats["launcher_start"]).total_seconds()
    return stats


def _fmt_dt(x: dt.datetime | None) -> str:
    return x.strftime("%Y-%m-%d %H:%M:%S") if isinstance(x, dt.datetime) else "(not found)"


def _fmt_duration(seconds: float | None) -> str:
    if seconds is None:
        return "n/a"
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}h{m:02d}m{s:02d}s"


# --------------------------------------------------------------------------- #
# Markdown sections
# --------------------------------------------------------------------------- #
def _per_variant_md(rd: ps.RoundData) -> str:
    parts = []
    for v, (vp, tot) in rd.per_variant.items():
        pct = 100.0 * vp / tot if tot else 0.0
        parts.append(f"{v} {vp}/{tot} ({pct:.1f}%)")
    return "; ".join(parts)


def build_report(
    run_dir: Path,
    console_path: Path | None,
    level_map: dict | None = None,
    console_paths: list[Path] | None = None,
) -> str:
    tag = ps.run_tag(run_dir)
    rounds = ps.scan_run(run_dir)
    if level_map is None:
        level_map = dm.load_level_map()
    if console_paths is None:
        console_paths = dm.default_console_paths(run_dir, console_path)
    settled = [r for r in rounds if r.ok]
    pending = [r for r in rounds if not r.ok]
    report = ps.load_pool_report(run_dir)
    console = scan_console_log(console_path) if console_path else {"exists": False, "path": str(console_path)}

    in_flight = bool(pending) or report is None or (console.get("exists") and console.get("launcher_exit") is None)

    L: list[str] = []
    ap = L.append
    ap(f"# Variant-pool acceptance report — `{tag}`")
    ap("")
    ap(f"- Run dir: `{Path(run_dir).resolve()}`")
    ap(f"- Generated: {dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    ap(f"- Rounds discovered: {len(rounds)} (settled {len(settled)}, pending {len(pending)})")
    ap(f"- Status: {'IN-FLIGHT (settled rounds only below)' if in_flight else 'complete'}")
    if report is not None:
        ap("- `pool_report.json` present -> cross-checks enabled.")
    else:
        ap("- `pool_report.json` absent -> run still in-flight; curve computed from `R*/pool_state.json`.")
    ap("")

    # --- Curve ------------------------------------------------------------- #
    ap("## Pass@2 curve")
    ap("")
    ap("Semantics: pass@2 = a task with `successes >= 1`; pool aggregate = each")
    ap("routed task scored by its routing variant, divided by the evaluated denominator.")
    ap("")
    ap("| Round | vc | pool pass@2 | num/den | Δ vs prev | note (M-25 band ±5pp) | per-variant |")
    ap("|------:|---:|------------:|:-------:|:---------:|:----------------------|:------------|")
    prev = None
    for rd in settled:
        if prev is None:
            delta_s, note = "—", "baseline"
        else:
            delta = rd.pool_pct - prev
            delta_s = f"{delta:+.1f}pp"
            note = "noise" if abs(delta) <= NOISE_BAND_PP else ("signal ↑" if delta > 0 else "signal ↓")
        ap(
            f"| R{rd.round} | {rd.variant_count} | {rd.pool_pct:.1f}% | "
            f"{rd.pool_pass}/{rd.denominator} | {delta_s} | {note} | {_per_variant_md(rd)} |"
        )
        prev = rd.pool_pct
    for rd in pending:
        ap(f"| R{rd.round} | pending | — | — | — | in-flight/unreadable | {rd.error or ''} |")
    ap("")

    # --- Peak / drift ------------------------------------------------------ #
    stats = ps.curve_stats(rounds)
    ap("## Peak, final and drift")
    ap("")
    if stats["settled"]:
        ap(f"- Peak: **R{stats['peak_round']} = {stats['peak_pct']:.1f}%**")
        ap(f"- Final settled: **R{stats['final_round']} = {stats['final_pct']:.1f}%**")
        ap(f"- Drift (final − peak): **{stats['drift']:+.1f}pp**")
        if report is not None:
            r_peak = report.get("peak_pass_at_2")
            r_final = report.get("final_pass_at_2")
            r_drift = report.get("drift_from_peak")
            ap("")
            ap("Cross-check vs the run's own `pool_report.json`:")
            ap("")
            ap("| metric | this tool | pool_report.json | match |")
            ap("|:-------|:---------:|:----------------:|:-----:|")

            def _row(name: str, a: float, b: float | None) -> None:
                mark = "✓" if b is not None and abs(a - b) < 0.05 else "—"
                ap(f"| {name} | {a} | {b} | {mark} |")

            if r_peak is not None:
                _row("peak pass@2 %", stats["peak_pct"], r_peak * 100)
            if r_final is not None:
                _row("final pass@2 %", stats["final_pct"], r_final * 100)
            if r_drift is not None:
                _row("drift pp", stats["drift"], r_drift * 100)
    else:
        ap("- No settled rounds yet.")
    ap("")

    # --- Pedigree ---------------------------------------------------------- #
    ap("## Pool pedigree")
    ap("")
    events = ps.lineage_events(rounds, run_dir)
    if events:
        ap("| event | round | variant | parent |")
        ap("|:------|:-----:|:-------:|:------:|")
        for e in events:
            ap(f"| {e.get('kind')} | R{e.get('round_idx')} | {e.get('variant_id')} | {e.get('parent_id') or '—'} |")
    else:
        ap("- No fork/retire events (single-lineage pool).")
    ap("")
    if settled:
        last = settled[-1]
        ap(f"- Final pool size K (last settled round R{last.round}): **{last.variant_count}** variant(s).")
    ship_rounds = [r for r in settled if r.shipped]
    if ship_rounds:
        ap("- Ship rounds:")
        for r in ship_rounds:
            dec = ", ".join(f"{k}:{v}" for k, v in r.decisions.items()) or "(no decisions recorded)"
            cats = r.regression.get("category_counts", {})
            ship_type = "apply" if "apply" in r.decisions.values() else (
                "fork" if "fork" in r.decisions.values() else "other")
            ap(f"  - **R{r.round}** — type={ship_type}; decisions=[{dec}]; candidate categories={cats or '{}'}")
    else:
        ap("- No ship rounds among settled rounds.")
    ap("")

    # --- M-23 -------------------------------------------------------------- #
    ap("## M-23: cross-variant regression classification (best-effort)")
    ap("")
    ap("Source: `decisions` + `candidate_diagnostics[*].archive_reason` / `failed_stage`")
    ap("in each `R{n}/pool_state.json`. `archive_reason` is of the form")
    ap("`\"APPLY: improved=[...] regressed=[...]\"` (also FORK / SEESAW_REGRESSION /")
    ap("ROUNDTRIP_L2 / FORCED_GATE). Confidence: **medium** — the labels and")
    ap("improved/regressed id lists are read verbatim; rounds with an empty")
    ap("`candidate_diagnostics` map carry no classifiable data and are reported as such.")
    ap("")
    any_diag = any(r.regression.get("diagnostics_present") for r in settled)
    if any_diag:
        ap("| Round | decisions | archive categories | failed_stage | improved ids | regressed ids | cands w/ regression |")
        ap("|:-----:|:----------|:-------------------|:-------------|:------------:|:-------------:|:-------------------:|")
        agg_improved = agg_regressed = agg_regr_cands = 0
        cat_totals: dict[str, int] = {}
        for r in settled:
            reg = r.regression
            if not reg.get("diagnostics_present"):
                continue
            dec = reg.get("decision_counts", {})
            cats = reg.get("category_counts", {})
            fs = reg.get("failed_stage_counts", {})
            ap(
                f"| R{r.round} | {dec or '{}'} | {cats or '{}'} | {fs or '{}'} | "
                f"{reg.get('improved_task_instances', 0)} | {reg.get('regressed_task_instances', 0)} | "
                f"{reg.get('candidates_with_regression', 0)} |"
            )
            agg_improved += reg.get("improved_task_instances", 0)
            agg_regressed += reg.get("regressed_task_instances", 0)
            agg_regr_cands += reg.get("candidates_with_regression", 0)
            for k, v in cats.items():
                cat_totals[k] = cat_totals.get(k, 0) + v
        ap("")
        ap(
            f"Totals across settled rounds: improved task-instances={agg_improved}, "
            f"regressed task-instances={agg_regressed}, candidates carrying a regression={agg_regr_cands}."
        )
        ap(f"Archive-reason category totals: {cat_totals}.")
    else:
        ap("No `candidate_diagnostics` recorded in any settled round — no M-23 classification available from this data.")
    ap("")

    # --- Console log ------------------------------------------------------- #
    ap("## Console-log statistics")
    ap("")
    if not console.get("exists"):
        ap(f"- Console log not found at `{console.get('path')}` (skipped).")
    else:
        ap(f"- Log: `{console['path']}` ({console['lines']:,} lines, ANSI-stripped).")
        ap(f"- LAUNCHER_START: {_fmt_dt(console['launcher_start'])}")
        ap(
            f"- LAUNCHER_EXIT: {_fmt_dt(console['launcher_exit'])}"
            + (f" (code={console['exit_code']})" if console["exit_code"] is not None else " — **absent (in-flight)**")
        )
        ap(f"- Wall clock (EXIT − START): **{_fmt_duration(console['wall_clock_seconds'])}**")
        ap(f"- Round/phase activity markers `[Rn...]`: {console['round_markers']:,}")
        ap(f"- Tracebacks (`Traceback (most recent call last)`): {console['traceback']}")
        ap(f"- Harness `[ERROR` level lines: {console['error_level']}")
        backend = "unknown (no experiment.lock.json)"
        lock_path = Path(run_dir) / "experiment.lock.json"
        if lock_path.is_file():
            try:
                lock = json.loads(lock_path.read_text(encoding="utf-8"))
                hits = [
                    m.group(1)
                    for w in lock.get("provenance_warnings", [])
                    if (m := re.search(r"search_backend=(\w+)", str(w)))
                ]
                backend = hits[0] if hits else "chain (default; no search_backend provenance warning)"
            except (OSError, ValueError):
                backend = "unknown (lock unreadable)"
        ap(f"- Search backend (authoritative, from `experiment.lock.json` provenance): **{backend}**.")
        ap(
            f"- Serper: `Serper search failed` = {console['serper_search_failed']}; "
            f"any-serper mentions = {console['serper_mentions']}."
        )
        ap(
            "  - Note: successful Serper calls are **not** logged "
            "(`harnessx/tools/contrib/serper_search.py` logs only the failure→fallback "
            "warning), so actual Serper burn is **not measurable from logs** — read it "
            "off the serper.dev dashboard. A failure count > 0 is the quota-exhaustion "
            "signature, independent of which backend the lock declares."
        )
        ap(
            f"- Budget: per-candidate `budget=$N` grant lines = {console['budget_grant_lines']}; "
            f"exhaustion lines (`budget_exceeded` / exhausted / exceeded) = {console['budget_exhaust_lines']}."
        )
        if report is not None and "budget_exhaustions_run_total" in report:
            ap(
                f"  - Authoritative (from `pool_report.json`): "
                f"budget_exhaustions_run_total = {report.get('budget_exhaustions_run_total')}, "
                f"last-round budget_exhaustions = {report.get('budget_exhaustions')}, "
                f"rate = {report.get('budget_exhaustion_rate')}."
            )
        else:
            ap(
                "  - The console log has no clean budget-exhaustion string; the authoritative "
                "exhaustion count lives in the structured `pool_report.json` "
                "(`budget_exhaustions*`), written only when the run completes."
            )
    ap("")

    # --- T1 deep metrics (A-H) ------------------------------------------- #
    ap("")
    for line in dm.build_deep_sections(rounds, run_dir, console_paths, level_map):
        ap(line)

    if in_flight:
        ap("---")
        ap("")
        ap("> **In-flight run.** Only settled rounds are reported above. "
           "Pending rounds: " + (", ".join("R" + str(r.round) for r in pending) if pending else "none")
           + ". Re-run this report after further rounds settle.")
        ap("")

    return "\n".join(L)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Write a Markdown acceptance report for a variant-pool run (read-only).")
    parser.add_argument("--run-dir", required=True, help="Path to runs/<tag>")
    parser.add_argument(
        "--console-log",
        help="Path to the run's console log. Defaults to <run-dir-parent>/<tag>.console.log if present.",
    )
    parser.add_argument("--data", help=f"GAIA difficulty JSON (default: {dm.DEFAULT_GAIA_DATA}).")
    parser.add_argument(
        "--latex", action="store_true",
        help="Also write LaTeX tables (curve / noise band / territory) to out/<tag>/tables/.",
    )
    args = parser.parse_args(argv)

    run_dir = Path(args.run_dir)
    if not run_dir.is_dir():
        print(f"error: run-dir not found: {run_dir}", file=sys.stderr)
        return 2

    tag = ps.run_tag(run_dir)
    if args.console_log:
        console_path = Path(args.console_log)
    else:
        # Convention discovered in the repo: runs/<tag>.console.log sits next to runs/<tag>/.
        default_console = run_dir.resolve().parent / f"{tag}.console.log"
        console_path = default_console if default_console.exists() else None
        if console_path:
            print(f"note: using default console log {console_path}", file=sys.stderr)

    level_map = dm.load_level_map(Path(args.data) if args.data else None)
    console_paths = dm.default_console_paths(run_dir, console_path)
    md = build_report(run_dir, console_path, level_map=level_map, console_paths=console_paths)

    out_dir = ps.out_dir_for(tag)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "report.md"
    out_path.write_text(md, encoding="utf-8")
    print(f"wrote report -> {out_path}")

    if args.latex:
        written = dm.write_latex_tables(tag, ps.scan_run(run_dir), out_dir)
        for p in written:
            print(f"wrote LaTeX -> {p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
