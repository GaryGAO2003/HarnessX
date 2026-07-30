"""Read-only pool_state.json scanner + scoring for HarnessX variant-pool runs.

Shared by ``curve_extract.py``, ``acceptance_report.py`` and ``plot_curve.py``.
This module NEVER writes to ``runs/**`` and NEVER imports the runner code; it
only parses the per-round settlement snapshots ``runs/<tag>/R{n}/pool_state.json``.

Scoring semantics (verified to reproduce the run's own ``pool_report.json`` curve
for runs/s1k8: R0-R5 = 63.3 / 80.0 / 86.7 / 70.0 / 76.7 / 80.0, and R0 = 64.1%
(66/103) for the in-flight runs/s1k8b103):

* A single task "passes" at pass@2 when its measurement ``[successes, attempts]``
  has ``successes >= 1`` (attempts is typically 2).
* Pool aggregate pass@2 for a round = build the task -> variant map from
  ``routing`` (``{variant: [task_id, ...]}``); for every routed task, look up
  ``active_pool_measurements[variant][task_id]`` and count it as a pass when
  ``successes >= 1``; the percentage divides by ``evaluated_task_denominator``.
* Per-variant pass@2 = the same count restricted to that variant's routed tasks
  (``active_pool_measurements[variant]`` is scoped exactly to the routed subset in
  multi-variant rounds, so routing and measurements agree task-for-task).

The M-23 cross-variant regression classification is a best-effort extraction from
``decisions`` and ``candidate_diagnostics[*].archive_reason`` / ``failed_stage``;
see :func:`extract_regression`.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

# ANSI SGR escape sequences (console logs are colourised; strip before parsing).
ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
_ROUND_DIR_RE = re.compile(r"^R(\d+)$")
_IMPROVED_RE = re.compile(r"improved=\[([^\]]*)\]")
_REGRESSED_RE = re.compile(r"regressed=\[([^\]]*)\]")


def strip_ansi(text: str) -> str:
    """Remove ANSI colour codes from a line of console text."""
    return ANSI_RE.sub("", text)


@dataclass
class RoundData:
    """One settled (or pending) round parsed from ``R{n}/pool_state.json``."""

    round: int
    path: Path
    ok: bool  # True when the JSON parsed cleanly (a settled round)
    error: str | None = None  # populated when ok is False (missing / mid-write)
    variant_count: int = 0
    denominator: int = 0
    pool_pass: int = 0
    pool_measured: int = 0  # routed tasks that actually carried a measurement
    routed_total: int = 0  # tasks named in routing (should equal denominator)
    per_variant: dict[str, tuple[int, int]] = field(default_factory=dict)  # V -> (pass, routed)
    shipped: bool = False
    forked: list[str] = field(default_factory=list)
    retired: list[str] = field(default_factory=list)
    idle: int = 0
    decisions: dict[str, str] = field(default_factory=dict)
    candidate_accounting: dict = field(default_factory=dict)
    reconcile_status: dict = field(default_factory=dict)
    active_score_source: dict = field(default_factory=dict)
    regression: dict = field(default_factory=dict)  # M-23 best-effort extraction

    @property
    def pool_pct(self) -> float:
        """Pool aggregate pass@2 as a percentage of the evaluated denominator."""
        if not self.denominator:
            return 0.0
        return 100.0 * self.pool_pass / self.denominator


def discover_round_files(run_dir: Path) -> list[tuple[int, Path]]:
    """Return ``[(round_idx, pool_state_path), ...]`` sorted by round index.

    Includes every ``R<n>`` directory even if its ``pool_state.json`` is missing
    or mid-write, so callers can surface those rounds as pending.
    """
    run_dir = Path(run_dir)
    out: list[tuple[int, Path]] = []
    if not run_dir.is_dir():
        return out
    for child in run_dir.iterdir():
        if not child.is_dir():
            continue
        m = _ROUND_DIR_RE.match(child.name)
        if not m:
            continue
        out.append((int(m.group(1)), child / "pool_state.json"))
    out.sort(key=lambda t: t[0])
    return out


def pool_score(state: dict) -> tuple[int, int, int, dict[str, tuple[int, int]]]:
    """Compute ``(pool_pass, pool_measured, routed_total, per_variant)`` for a round.

    See the module docstring for the exact semantics.
    """
    routing: dict = state.get("routing", {}) or {}
    apm: dict = state.get("active_pool_measurements", {}) or {}
    pool_pass = 0
    pool_measured = 0
    routed_total = 0
    per_variant: dict[str, tuple[int, int]] = {}
    for variant, tasks in routing.items():
        tasks = tasks or []
        vpass = 0
        vmeas = apm.get(variant, {}) or {}
        for task_id in tasks:
            routed_total += 1
            m = vmeas.get(task_id)
            if m is None:
                continue
            pool_measured += 1
            # m == [successes, attempts]; pass@2 == successes >= 1
            if isinstance(m, (list, tuple)) and len(m) >= 1 and m[0] >= 1:
                vpass += 1
        per_variant[variant] = (vpass, len(tasks))
        pool_pass += vpass
    return pool_pass, pool_measured, routed_total, per_variant


def _parse_id_list(blob: str) -> int:
    """Count comma-separated ids inside an ``improved=[...]`` / ``regressed=[...]`` blob."""
    blob = blob.strip()
    if not blob:
        return 0
    return len([p for p in blob.split(",") if p.strip()])


def extract_regression(state: dict) -> dict:
    """Best-effort M-23 cross-variant regression classification for one round.

    Draws on two fields that the runner actually records:

    * ``decisions`` -> ``{variant: "apply"|"fork"|"reject"}`` (the action taken).
    * ``candidate_diagnostics`` -> per-candidate ``archive_reason`` (of the form
      ``"APPLY: improved=[...] regressed=[...]"`` / ``"FORK: ..."`` /
      ``"SEESAW_REGRESSION: ..."`` / ``"ROUNDTRIP_L2: ..."`` /
      ``"FORCED_GATE(fork): ..."``) and ``failed_stage``.

    Returns a dict of counts; ``diagnostics_present`` is False when the round
    recorded no candidate diagnostics (so the caller reports "no data" rather than
    inventing a classification).
    """
    decisions: dict = state.get("decisions", {}) or {}
    diags: dict = state.get("candidate_diagnostics", {}) or {}

    decision_counts = Counter(v for v in decisions.values())
    category_counts: Counter = Counter()
    failed_stage_counts: Counter = Counter()
    improved_ids = 0
    regressed_ids = 0
    candidates_with_regression = 0
    candidates_clean_improve = 0

    for cand in diags.values():
        if not isinstance(cand, dict):
            continue
        ar = cand.get("archive_reason") or ""
        fs = cand.get("failed_stage")
        if fs:
            failed_stage_counts[str(fs)] += 1
        if ar:
            category = ar.split(":", 1)[0].strip() or "(unlabelled)"
            category_counts[category] += 1
            im = _IMPROVED_RE.search(ar)
            rg = _REGRESSED_RE.search(ar)
            n_im = _parse_id_list(im.group(1)) if im else 0
            n_rg = _parse_id_list(rg.group(1)) if rg else 0
            improved_ids += n_im
            regressed_ids += n_rg
            if n_rg > 0:
                candidates_with_regression += 1
            elif n_im > 0:
                candidates_clean_improve += 1
        else:
            category_counts["(none)"] += 1

    return {
        "diagnostics_present": bool(diags),
        "decision_counts": dict(decision_counts),
        "category_counts": dict(category_counts),
        "failed_stage_counts": dict(failed_stage_counts),
        "improved_task_instances": improved_ids,
        "regressed_task_instances": regressed_ids,
        "candidates_with_regression": candidates_with_regression,
        "candidates_clean_improve": candidates_clean_improve,
        "candidate_count": len(diags),
    }


def load_round(round_idx: int, path: Path) -> RoundData:
    """Parse one ``pool_state.json`` tolerantly (missing / mid-write -> pending)."""
    path = Path(path)
    if not path.exists():
        return RoundData(round=round_idx, path=path, ok=False, error="missing pool_state.json")
    try:
        with path.open(encoding="utf-8") as fh:
            state = json.load(fh)
    except (json.JSONDecodeError, OSError, ValueError) as exc:
        # A mid-write snapshot in an in-flight run lands here -> mark pending.
        return RoundData(round=round_idx, path=path, ok=False, error=f"parse error: {exc}")

    pool_pass, pool_measured, routed_total, per_variant = pool_score(state)
    return RoundData(
        round=int(state.get("round", round_idx)),
        path=path,
        ok=True,
        variant_count=int(state.get("variant_count", 0) or 0),
        denominator=int(state.get("evaluated_task_denominator", 0) or 0),
        pool_pass=pool_pass,
        pool_measured=pool_measured,
        routed_total=routed_total,
        per_variant=per_variant,
        shipped=bool(state.get("shipped", False)),
        forked=list(state.get("forked", []) or []),
        retired=list(state.get("retired", []) or []),
        idle=int(state.get("idle", 0) or 0),
        decisions=dict(state.get("decisions", {}) or {}),
        candidate_accounting=dict(state.get("candidate_accounting", {}) or {}),
        reconcile_status=dict(state.get("reconcile_status", {}) or {}),
        active_score_source=dict(state.get("active_score_source", {}) or {}),
        regression=extract_regression(state),
    )


def scan_run(run_dir: Path) -> list[RoundData]:
    """Scan every ``R{n}/pool_state.json`` under ``run_dir`` (sorted by round)."""
    return [load_round(idx, path) for idx, path in discover_round_files(run_dir)]


def lineage_events(rounds: list[RoundData], run_dir: Path | None = None) -> list[dict]:
    """Fork/retire lineage events with round positions.

    Prefers the authoritative ``pool_report.json`` ``fork_retire_events`` when the
    run has completed and written one; otherwise derives events from each round's
    ``forked`` / ``retired`` lists (parent = the variant whose decision that round
    was ``"fork"``).
    """
    report = load_pool_report(run_dir) if run_dir is not None else None
    if report and isinstance(report.get("fork_retire_events"), list) and report["fork_retire_events"]:
        return list(report["fork_retire_events"])

    events: list[dict] = []
    for rd in rounds:
        if not rd.ok:
            continue
        fork_parents = [v for v, d in rd.decisions.items() if d == "fork"]
        for i, child in enumerate(rd.forked):
            parent = fork_parents[i] if i < len(fork_parents) else (fork_parents[0] if fork_parents else None)
            events.append({"kind": "fork", "round_idx": rd.round, "variant_id": child, "parent_id": parent})
        for retired in rd.retired:
            events.append({"kind": "retire", "round_idx": rd.round, "variant_id": retired, "parent_id": None})
    return events


def curve_stats(rounds: list[RoundData]) -> dict:
    """Peak round/value, final value and final-minus-peak drift over settled rounds."""
    settled = [r for r in rounds if r.ok]
    if not settled:
        return {"settled": 0, "peak_round": None, "peak_pct": None, "final_round": None, "final_pct": None, "drift": None}
    peak = max(settled, key=lambda r: r.pool_pct)
    final = settled[-1]
    return {
        "settled": len(settled),
        "peak_round": peak.round,
        "peak_pct": peak.pool_pct,
        "final_round": final.round,
        "final_pct": final.pool_pct,
        "drift": final.pool_pct - peak.pool_pct,
    }


def load_pool_report(run_dir: Path | None) -> dict | None:
    """Load the run's own ``pool_report.json`` summary if it exists (completed runs).

    Used only for cross-checking / lineage; never required. Returns None when the
    run is still in-flight (no report written yet) or the file cannot be parsed.
    """
    if run_dir is None:
        return None
    path = Path(run_dir) / "pool_report.json"
    if not path.exists():
        return None
    try:
        with path.open(encoding="utf-8") as fh:
            return json.load(fh)
    except (json.JSONDecodeError, OSError, ValueError):
        return None


def run_tag(run_dir: Path) -> str:
    """The short run tag = the run directory's own name (e.g. ``s1k8``)."""
    return Path(run_dir).resolve().name


def out_dir_for(tag: str) -> Path:
    """Output directory ``experiments/analysis/out/<tag>/`` next to this module."""
    return Path(__file__).resolve().parent / "out" / tag
