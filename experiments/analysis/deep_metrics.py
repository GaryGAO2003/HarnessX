"""Deep T1 analysis metrics (A-H) for variant-pool runs (read-only).

Implements the eight free-data analyses in ``experiments/docs/ANALYSIS-PLAN.md``
§T1 on top of the shared :mod:`_poolscan` scanner. Every function is pure and
read-only: it consumes already-scanned :class:`_poolscan.RoundData` (or a console
log's text) and returns a plain, JSON-friendly dict. Nothing here writes to
``runs/**`` or imports the runner.

The eight metrics, and the scoring semantics that make them defensible:

* **A. Noise floor** — Δpass@2 across every *settled no-ship round* (a round that
  itself shipped no config change, so its active/paper configuration equals the
  previous round's). The spread is the empirical replacement for M-25 (the paper's
  Table-8 ±5% band). CAVEAT (must ship with the number): the routing is still free
  to re-partition on a no-ship round, so this is the *"configuration constant but
  routing free"* band, **not** pure replay noise.
* **B. Best-of-pool ceiling** — the cross-round union of every (variant, task) that
  any variant solved in any round (= the gate's ``is_ever_solved`` ledger), over the
  denominator. CAVEAT: this union is a ``pass@(2 x settled_rounds)`` object, a
  *different measurement scope* from a single round's pass@2; it must **not** be
  subtracted from a single-round score to quote a "routing loss". The *best single
  round* is reported alongside as the same-scope reference.
* **C. Post-hoc optimal routing replay** — per task, estimate each variant's success
  rate from its cross-round (task, variant) history and take the best variant; sum
  the best rates. CAVEAT: post-hoc selection bias + observation bias (each round only
  measures the routed variant), so this is a reference estimate, never a claim.
* **D. Protocol stop-point reconstruction** — for patience p in {3,5,8,16}, replay
  "stop after p consecutive no-ship rounds" and report the stop round and its pass@2.
* **E. Variant specialization & routing dynamics** — (variant x round) territory size
  and in-territory pass@2; each variant's birth-cluster score vs its later expanded
  score (cold-start over-confidence); per-round territory churn; variant survival.
* **F. M-23 manifestation** — cross-variant regression counts and round positions
  from ``candidate_diagnostics[*].archive_reason`` (the ``regressed=[...]`` lists are
  scored against the global ``is_ever_solved`` ledger, so M-23 can only manifest at
  K>=2). Reports "not available" honestly when the data is absent.
* **G. Difficulty stratification** — per-round pass@2 split by GAIA ``Level``
  (L1=39 / L2=52 / L3=12). L3 (n=12) is described, never tested.
* **H. Cost account** — per-round / per-variant ``cost=$`` and eval time scraped from
  the console log (``pool_state.json`` carries no cost fields), plus budget grants and
  the Serper caveat.

Standalone use::

    python experiments/analysis/deep_metrics.py --run-dir <runs/tag> --metric all
    python experiments/analysis/deep_metrics.py --run-dir <runs/tag> --metric A
    python experiments/analysis/deep_metrics.py --run-dir <runs/tag> --metric H \\
        --console-log <runs/tag.console.log>
    python experiments/analysis/deep_metrics.py --run-dir <runs/tag> --latex
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.dont_write_bytecode = True  # keep experiments/analysis/ free of __pycache__

import _poolscan as ps

# --------------------------------------------------------------------------- #
# Constants / captions
# --------------------------------------------------------------------------- #
DEFAULT_PATIENCES = (3, 5, 8, 16)

# Default GAIA difficulty file (task_id -> Level). experiments/analysis/ -> repo root.
DEFAULT_GAIA_DATA = (
    Path(__file__).resolve().parents[2] / "recipe" / "gaia_evolver" / "data" / "webthinker_gaia_dev.json"
)

# Mandatory scope captions (ship verbatim with every number).
CAPTION_A = (
    "Scope: round-over-round pass@2 deltas across settled *no-ship* rounds — the "
    "active/paper configuration is constant across each pair (the round applied no "
    "change), but routing is still free to re-partition the pool. This is the "
    "\"configuration-constant-but-routing-free\" wobble, NOT pure replay noise. It is "
    "the empirical stand-in for M-25 (the paper's Table-8 +/-5% band)."
)
CAPTION_B = (
    "Scope WARNING: this union is every task solved by ANY variant in ANY round "
    "(= the gate's is_ever_solved ledger). It is a pass@(2 x settled_rounds) object "
    "and is therefore a DIFFERENT measurement scope from a single round's pass@2 — do "
    "NOT subtract it from a single-round score to quote a 'routing loss'. The 'best "
    "single round' below is the same-scope (single-round pass@2) reference. This is an "
    "optimistic 'ever solved' ceiling, not 'currently solvable by some variant', and "
    "carries no causal claim."
)
CAPTION_C = (
    "Bias: each task is routed to the variant with the highest empirical success rate "
    "estimated from the SAME history (post-hoc selection bias), and each round only "
    "measures the routed variant, so most (variant, task) cells are never observed "
    "(observation bias). Reference estimate only — no claim."
)
CAPTION_D = (
    "Replay of the ship/no-ship stream: 'stop after p consecutive no-ship rounds'. "
    "Supplies the empirical evidence for the S1 patience protocol decision: the freeze "
    "package runs at patience 16 so the paper-verbatim patience-3 stop point can be "
    "reconstructed post hoc from one run. NB patience carries no M-number in the "
    "deviation registry -- M-24 is the round-budget off-by-one, not this."
)
CAPTION_E = (
    "Territory = the tasks routed to a variant in a round; in-territory pass@2 is that "
    "variant scored on exactly those tasks. A fork is born on the small cluster it "
    "improved (often 100%), then dilutes as its territory expands — the birth-vs-latest "
    "gap quantifies cold-start over-confidence. Churn = tasks whose owning variant "
    "changed vs the previous round; it explains how a no-ship round can still move "
    "pass@2 (routing re-partition, not editing)."
)
CAPTION_F = (
    "M-23 = the gate scores `regressed` against the GLOBAL is_ever_solved ledger (ever "
    "solved by ANY variant, ANY round) while `improved` is scored per-variant; the "
    "cross-variant asymmetry can only manifest at K>=2. Source fields queried: "
    "candidate_diagnostics[*].archive_reason (the 'regressed=[...]' id lists), "
    ".failed_stage, and per-round `decisions`. Confidence: medium (labels/id-lists read "
    "verbatim). Rounds with no candidate_diagnostics carry no classifiable data and are "
    "reported as such rather than invented."
)
CAPTION_G = (
    "Per-round pass@2 split by GAIA Level via task_id join. NOTE: L3 has n=12 — "
    "described only, no test is run on the L3 stratum."
)
CAPTION_H = (
    "pool_state.json carries no cost/token/budget fields; per-task cost is scraped from "
    "the console log ('[R{n}-{V}-{phase}] {task} PASS|FAIL ... cost=$X time=Ys'). "
    "'cost' is the run's own internal assumed price. Per-round eval-time is a sum of "
    "per-task times (compute-seconds; tasks run concurrently, so it exceeds wall clock). "
    "Serper: successful calls are NOT logged (only the failure->fallback warning is), so "
    "actual Serper burn is not measurable from logs — read it off the serper.dev "
    "dashboard; a failure count > 0 is the quota-exhaustion signature."
)

# Console-log cost line, e.g.:
#   15:34:51 [INFO ] recipe.gaia_evolver.run - [R0-V0-active] <task> PASS - steps=7 cost=$0.191 time=72.6s
_COST_LINE_RE = re.compile(
    r"\[R(?P<round>\d+)-(?P<variant>V\d+)-(?P<phase>[A-Za-z0-9_]+)\]\s+"
    r"(?P<task>\S+)\s+(?P<verdict>PASS|FAIL)\b.*?cost=\$(?P<cost>[0-9.]+).*?time=(?P<time>[0-9.]+)s"
)
# Evolve budget grant lines carry the round inside the trajectories path (...\R<n>\...).
_BUDGET_ROUND_RE = re.compile(r"[\\/]R(\d+)[\\/]")
_BUDGET_VAL_RE = re.compile(r"budget=\$([0-9.]+)")

# Minimal LAUNCHER wall-clock parse (mirrors acceptance_report, kept local to stay
# import-cycle free).
_DATE_RE = re.compile(r"(\d{4})/(\d{2})/(\d{2})")
_TIME_RE = re.compile(r"(\d{1,2}):(\d{2}):(\d{2})(?:\.(\d+))?")
_EXIT_CODE_RE = re.compile(r"LAUNCHER_EXIT\s+code=(-?\d+)")


# --------------------------------------------------------------------------- #
# Small shared helpers
# --------------------------------------------------------------------------- #
_STATE_CACHE: dict[str, dict] = {}


def _read_state(path: Path) -> dict:
    """Read one ``pool_state.json`` (cached by path). Returns {} on any error."""
    key = str(path)
    if key in _STATE_CACHE:
        return _STATE_CACHE[key]
    try:
        state = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {}
    _STATE_CACHE[key] = state
    return state


def _task_solved(measurement) -> bool:
    """pass@2: a measurement ``[successes, attempts]`` passes when successes >= 1."""
    return isinstance(measurement, (list, tuple)) and len(measurement) >= 1 and measurement[0] >= 1


def _routing_assignment(state: dict) -> dict[str, str]:
    """Invert ``routing`` ({variant: [task,...]}) to a task -> variant map."""
    out: dict[str, str] = {}
    for variant, tasks in (state.get("routing", {}) or {}).items():
        for task in tasks or []:
            out[task] = variant
    return out


def _settled(rounds: list[ps.RoundData]) -> list[ps.RoundData]:
    return [r for r in rounds if r.ok]


def load_level_map(data_path: Path | None = None) -> dict[str, int]:
    """Load ``task_id -> Level`` from the GAIA dev JSON (list of task dicts)."""
    path = Path(data_path) if data_path else DEFAULT_GAIA_DATA
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    entries = data if isinstance(data, list) else list(data.values())
    out: dict[str, int] = {}
    for e in entries:
        if not isinstance(e, dict):
            continue
        tid = e.get("task_id") or e.get("id")
        lvl = e.get("Level", e.get("level"))
        if tid is not None and lvl is not None:
            try:
                out[str(tid)] = int(lvl)
            except (TypeError, ValueError):
                continue
    return out


# --------------------------------------------------------------------------- #
# A. Noise floor
# --------------------------------------------------------------------------- #
def noise_floor(rounds: list[ps.RoundData], max_round: int | None = None) -> dict:
    """Δpass@2 spread over settled no-ship rounds (see :data:`CAPTION_A`).

    A round N (with a settled predecessor P) contributes when ``shipped[N]`` is False:
    the round applied no change, so its configuration equals P's and ``pct(N) - pct(P)``
    is configuration-constant wobble. ``max_round`` optionally truncates the settled
    set (used to reproduce an earlier in-flight snapshot).
    """
    settled = _settled(rounds)
    if max_round is not None:
        settled = [r for r in settled if r.round <= max_round]
    samples: list[dict] = []
    for prev, cur in zip(settled, settled[1:]):
        if not cur.shipped:
            samples.append(
                {"round": cur.round, "prev_round": prev.round, "delta": cur.pool_pct - prev.pool_pct}
            )
    deltas = [s["delta"] for s in samples]
    n = len(deltas)
    res: dict = {
        "n": n,
        "samples": samples,
        "mean": None,
        "sd_sample": None,
        "sd_pop": None,
        "abs_mean": None,
        "abs_max": None,
        "band_1sd_sample": None,
        "band_2sd_sample": None,
        "band_1sd_pop": None,
        "band_2sd_pop": None,
        "caption": CAPTION_A,
    }
    if n == 0:
        return res
    mean = statistics.mean(deltas)
    sd_pop = statistics.pstdev(deltas)  # ddof=0 (matches the pre-registered hand calc)
    sd_sample = statistics.stdev(deltas) if n >= 2 else 0.0  # ddof=1 (default estimator)
    res.update(
        mean=mean,
        sd_sample=sd_sample,
        sd_pop=sd_pop,
        abs_mean=statistics.mean([abs(d) for d in deltas]),
        abs_max=max(abs(d) for d in deltas),
        band_1sd_sample=(mean - sd_sample, mean + sd_sample),
        band_2sd_sample=(mean - 2 * sd_sample, mean + 2 * sd_sample),
        band_1sd_pop=(mean - sd_pop, mean + sd_pop),
        band_2sd_pop=(mean - 2 * sd_pop, mean + 2 * sd_pop),
    )
    return res


# --------------------------------------------------------------------------- #
# B. Best-of-pool ceiling
# --------------------------------------------------------------------------- #
def best_of_pool(rounds: list[ps.RoundData]) -> dict:
    """Cross-round union of solved tasks (is_ever_solved) + best single round.

    See :data:`CAPTION_B` for the mandatory scope warning.
    """
    settled = _settled(rounds)
    solved: set[str] = set()
    best_single = {"round": None, "pass": 0}
    denom = settled[-1].denominator if settled else 0
    for rd in settled:
        state = _read_state(rd.path)
        apm = state.get("active_pool_measurements", {}) or {}
        for meas in apm.values():
            for tid, m in (meas or {}).items():
                if _task_solved(m):
                    solved.add(tid)
        if rd.pool_pass > best_single["pass"]:
            best_single = {"round": rd.round, "pass": rd.pool_pass}
    union = len(solved)
    return {
        "settled_rounds": len(settled),
        "denominator": denom,
        "union_solved": union,
        "union_pct": (100.0 * union / denom) if denom else 0.0,
        "best_single_round": best_single["round"],
        "best_single_pass": best_single["pass"],
        "best_single_pct": (100.0 * best_single["pass"] / denom) if denom else 0.0,
        "union_equiv_pass_at": 2 * len(settled),  # pass@(2 x settled_rounds)
        "caption": CAPTION_B,
    }


# --------------------------------------------------------------------------- #
# C. Post-hoc optimal routing replay
# --------------------------------------------------------------------------- #
def posthoc_optimal_routing(rounds: list[ps.RoundData]) -> dict:
    """Per-task best-variant success rate, summed (see :data:`CAPTION_C`)."""
    settled = _settled(rounds)
    denom = settled[-1].denominator if settled else 0
    # task -> variant -> [1/0 pass per measured round]
    hist: dict[str, dict[str, list[int]]] = defaultdict(lambda: defaultdict(list))
    for rd in settled:
        state = _read_state(rd.path)
        apm = state.get("active_pool_measurements", {}) or {}
        for variant, meas in apm.items():
            for tid, m in (meas or {}).items():
                hist[tid][variant].append(1 if _task_solved(m) else 0)
    total = 0.0
    per_task: list[dict] = []
    for tid, vmap in hist.items():
        rates = {v: sum(xs) / len(xs) for v, xs in vmap.items()}
        best_v = max(rates, key=rates.get)
        total += rates[best_v]
        per_task.append({"task": tid, "best_variant": best_v, "best_rate": rates[best_v]})
    return {
        "denominator": denom,
        "tasks_with_history": len(hist),
        "posthoc_score": total,
        "posthoc_pct": (100.0 * total / denom) if denom else 0.0,
        "per_task": per_task,
        "caption": CAPTION_C,
    }


# --------------------------------------------------------------------------- #
# D. Protocol stop-point reconstruction
# --------------------------------------------------------------------------- #
def protocol_stops(rounds: list[ps.RoundData], patiences=DEFAULT_PATIENCES) -> dict:
    """Replay 'stop after p consecutive no-ship rounds' for each patience p."""
    settled = _settled(rounds)
    out: dict[int, dict] = {}
    for p in patiences:
        streak = 0
        stop = None
        for rd in settled:
            streak = 0 if rd.shipped else streak + 1
            if streak >= p:
                stop = rd
                break
        if stop is not None:
            out[p] = {"triggered": True, "stop_round": stop.round, "stop_pct": stop.pool_pct,
                      "stop_pass": stop.pool_pass, "denominator": stop.denominator}
        elif settled:
            last = settled[-1]
            out[p] = {"triggered": False, "stop_round": None, "stop_pct": last.pool_pct,
                      "stop_pass": last.pool_pass, "denominator": last.denominator,
                      "terminal_round": last.round}
        else:
            out[p] = {"triggered": False, "stop_round": None, "stop_pct": None}
    return {"patiences": list(patiences), "stops": out, "settled_rounds": len(settled), "caption": CAPTION_D}


# --------------------------------------------------------------------------- #
# E. Variant specialization & routing dynamics
# --------------------------------------------------------------------------- #
def variant_dynamics(rounds: list[ps.RoundData]) -> dict:
    """Territory matrix, birth-vs-latest gap, churn, survival (see :data:`CAPTION_E`)."""
    settled = _settled(rounds)
    variants: list[str] = []
    for rd in settled:
        for v in rd.per_variant:
            if v not in variants:
                variants.append(v)

    # territory matrix: variant -> round -> {territory, pass, pct}
    matrix: dict[str, dict[int, dict]] = {v: {} for v in variants}
    for rd in settled:
        for v, (vp, terr) in rd.per_variant.items():
            matrix[v][rd.round] = {"territory": terr, "pass": vp,
                                   "pct": (100.0 * vp / terr) if terr else None}

    # birth (first round with territory > 0) vs latest (last round with territory > 0)
    birth_vs_latest: dict[str, dict] = {}
    survival: dict[str, dict] = {}
    for v in variants:
        alive_rounds = [r for r, c in sorted(matrix[v].items()) if c["territory"] > 0]
        if not alive_rounds:
            survival[v] = {"birth_round": None, "last_alive_round": None,
                           "alive_rounds": 0, "currently_alive": False}
            continue
        br, lr = alive_rounds[0], alive_rounds[-1]
        bc, lc = matrix[v][br], matrix[v][lr]
        birth_vs_latest[v] = {
            "birth_round": br, "birth_territory": bc["territory"], "birth_pct": bc["pct"],
            "latest_round": lr, "latest_territory": lc["territory"], "latest_pct": lc["pct"],
            "overconfidence_gap": (bc["pct"] - lc["pct"]) if (bc["pct"] is not None and lc["pct"] is not None) else None,
        }
        survival[v] = {
            "birth_round": br, "last_alive_round": lr, "alive_rounds": len(alive_rounds),
            "currently_alive": settled[-1].round in alive_rounds,
        }

    # per-round territory churn (tasks whose owning variant changed vs prev round)
    churn: dict[int, dict] = {}
    prev_assign: dict[str, str] | None = None
    for rd in settled:
        assign = _routing_assignment(_read_state(rd.path))
        if prev_assign is not None and assign:
            changed = sum(1 for t, v in assign.items() if prev_assign.get(t) != v)
            churn[rd.round] = {"changed": changed, "total": len(assign),
                               "pct": (100.0 * changed / len(assign)) if assign else 0.0}
        prev_assign = assign

    # variant holds territory only if it ever has >0 tasks; single-variant pools have
    # no specialization dynamics.
    territory_holders = [v for v in variants if any(c["territory"] > 0 for c in matrix[v].values())]
    return {
        "variants": variants,
        "territory_holders": territory_holders,
        "single_variant": len(territory_holders) <= 1,
        "matrix": matrix,
        "birth_vs_latest": birth_vs_latest,
        "churn": churn,
        "survival": survival,
        "caption": CAPTION_E,
    }


# --------------------------------------------------------------------------- #
# F. M-23 manifestation
# --------------------------------------------------------------------------- #
def m23_manifestation(rounds: list[ps.RoundData]) -> dict:
    """Cross-variant regression counts + round positions (see :data:`CAPTION_F`)."""
    settled = _settled(rounds)
    diag_rounds = [r for r in settled if r.regression.get("diagnostics_present")]
    manifest: list[dict] = []
    for r in diag_rounds:
        reg = r.regression
        regressed = reg.get("regressed_task_instances", 0)
        # M-23 (cross-variant) can only manifest at K>=2; a K=1 round's "regressed"
        # is against that lone variant's own history, not cross-variant.
        cross_variant = regressed > 0 and r.variant_count >= 2
        manifest.append({
            "round": r.round,
            "variant_count": r.variant_count,
            "decisions": reg.get("decision_counts", {}),
            "categories": reg.get("category_counts", {}),
            "failed_stage": reg.get("failed_stage_counts", {}),
            "regressed_task_instances": regressed,
            "improved_task_instances": reg.get("improved_task_instances", 0),
            "candidates_with_regression": reg.get("candidates_with_regression", 0),
            "cross_variant": cross_variant,
        })
    cross_rounds = [m for m in manifest if m["cross_variant"]]
    all_single = bool(settled) and all(r.variant_count <= 1 for r in settled)
    return {
        "data_available": bool(diag_rounds),
        "single_variant_run": all_single,
        "diag_round_count": len(diag_rounds),
        "manifest": manifest,
        "cross_variant_rounds": [m["round"] for m in cross_rounds],
        "cross_variant_round_count": len(cross_rounds),
        "manifestation_rate": (len(cross_rounds) / len(diag_rounds)) if diag_rounds else None,
        "total_regressed_task_instances": sum(m["regressed_task_instances"] for m in manifest),
        "fields_queried": ["candidate_diagnostics[*].archive_reason",
                           "candidate_diagnostics[*].failed_stage", "decisions"],
        "caption": CAPTION_F,
    }


# --------------------------------------------------------------------------- #
# G. Difficulty stratification
# --------------------------------------------------------------------------- #
def difficulty_strata(rounds: list[ps.RoundData], level_map: dict[str, int]) -> dict:
    """Per-round pass@2 split by GAIA Level (see :data:`CAPTION_G`)."""
    settled = _settled(rounds)
    levels = sorted(set(level_map.values())) if level_map else []
    per_round: list[dict] = []
    unmapped_total = 0
    for rd in settled:
        state = _read_state(rd.path)
        assign = _routing_assignment(state)
        apm = state.get("active_pool_measurements", {}) or {}
        by_level: dict[int, dict[str, int]] = {lv: {"pass": 0, "den": 0} for lv in levels}
        unmapped = 0
        for tid, variant in assign.items():
            lv = level_map.get(tid)
            if lv is None:
                unmapped += 1
                continue
            cell = by_level.setdefault(lv, {"pass": 0, "den": 0})
            cell["den"] += 1
            if _task_solved((apm.get(variant, {}) or {}).get(tid)):
                cell["pass"] += 1
        unmapped_total += unmapped
        per_round.append({
            "round": rd.round,
            "by_level": {lv: {"pass": c["pass"], "den": c["den"],
                              "pct": (100.0 * c["pass"] / c["den"]) if c["den"] else None}
                         for lv, c in sorted(by_level.items())},
            "unmapped": unmapped,
        })
    level_counts = {lv: sum(1 for v in level_map.values() if v == lv) for lv in levels}
    return {
        "available": bool(level_map),
        "levels": levels,
        "level_counts": level_counts,
        "per_round": per_round,
        "unmapped_total": unmapped_total,
        "caption": CAPTION_G,
    }


# --------------------------------------------------------------------------- #
# H. Cost account
# --------------------------------------------------------------------------- #
def _parse_launcher_dt(line: str):
    import datetime as dt
    dm, tm = _DATE_RE.search(line), _TIME_RE.search(line)
    if not (dm and tm):
        return None
    y, mo, d = (int(x) for x in dm.groups())
    hh, mm, ss = (int(tm.group(i)) for i in (1, 2, 3))
    micro = int(float("0." + tm.group(4)) * 1_000_000) if tm.group(4) else 0
    try:
        return dt.datetime(y, mo, d, hh, mm, ss, micro)
    except ValueError:
        return None


def parse_cost_lines(text: str) -> list[dict]:
    """Extract per-task cost records from console-log text (ANSI-stripped)."""
    out: list[dict] = []
    for line in text.splitlines():
        line = ps.strip_ansi(line)
        m = _COST_LINE_RE.search(line)
        if not m:
            continue
        out.append({
            "round": int(m.group("round")),
            "variant": m.group("variant"),
            "phase": m.group("phase"),
            "task": m.group("task"),
            "verdict": m.group("verdict"),
            "cost": float(m.group("cost")),
            "time": float(m.group("time")),
        })
    return out


def cost_account(console_paths: list[Path], run_dir: Path | None = None) -> dict:
    """Per-round / per-variant cost + eval time from the console log(s).

    Reads every path in ``console_paths`` (e.g. the main log and a ``*.resume`` log),
    de-duplicating identical physical log lines. ``pool_state.json`` holds no cost, so
    the console log is the only free source (see :data:`CAPTION_H`).
    """
    seen: set[tuple] = set()
    records: list[dict] = []
    round_logs: dict[int, set] = defaultdict(set)  # round -> {log filenames that carried it}
    budget_grants: dict[int, dict] = defaultdict(lambda: {"grants": 0, "budget": 0.0})
    launcher_start = launcher_exit = None
    exit_code = None
    serper_failed = 0
    read_paths: list[str] = []

    for path in console_paths:
        path = Path(path)
        if not path.exists():
            continue
        read_paths.append(str(path))
        raw = path.read_bytes().decode("utf-8", errors="replace")
        for line in raw.splitlines():
            line = ps.strip_ansi(line)
            low = line.lower()
            if "serper search failed" in low:
                serper_failed += 1
            if "LAUNCHER_START" in line and launcher_start is None:
                launcher_start = _parse_launcher_dt(line)
            if "LAUNCHER_EXIT" in line:
                launcher_exit = _parse_launcher_dt(line)
                cm = _EXIT_CODE_RE.search(line)
                if cm:
                    exit_code = int(cm.group(1))
            if "budget=$" in line:
                rm = _BUDGET_ROUND_RE.search(line)
                bm = _BUDGET_VAL_RE.search(line)
                if rm:
                    r = int(rm.group(1))
                    budget_grants[r]["grants"] += 1
                    if bm:
                        budget_grants[r]["budget"] += float(bm.group(1))
            m = _COST_LINE_RE.search(line)
            if m:
                key = (m.group("round"), m.group("variant"), m.group("phase"),
                       m.group("task"), m.group("verdict"), m.group("cost"), m.group("time"))
                if key in seen:
                    continue
                seen.add(key)
                rnd = int(m.group("round"))
                round_logs[rnd].add(path.name)
                records.append({
                    "round": rnd, "variant": m.group("variant"),
                    "phase": m.group("phase"), "task": m.group("task"),
                    "verdict": m.group("verdict"), "cost": float(m.group("cost")),
                    "time": float(m.group("time")),
                })

    # per-round aggregation
    per_round: dict[int, dict] = {}
    for rec in records:
        r = rec["round"]
        pr = per_round.setdefault(r, {"cost": 0.0, "time": 0.0, "tasks": 0, "passes": 0,
                                       "by_variant": defaultdict(float), "by_phase": defaultdict(float)})
        pr["cost"] += rec["cost"]
        pr["time"] += rec["time"]
        pr["tasks"] += 1
        pr["passes"] += 1 if rec["verdict"] == "PASS" else 0
        pr["by_variant"][rec["variant"]] += rec["cost"]
        pr["by_phase"][rec["phase"]] += rec["cost"]

    rows = []
    for r in sorted(per_round):
        pr = per_round[r]
        rows.append({
            "round": r, "cost": pr["cost"], "time": pr["time"], "tasks": pr["tasks"],
            "passes": pr["passes"],
            "budget_grants": budget_grants.get(r, {}).get("grants", 0),
            "budget_granted": budget_grants.get(r, {}).get("budget", 0.0),
            "by_variant": {k: round(v, 4) for k, v in sorted(pr["by_variant"].items())},
            "by_phase": {k: round(v, 4) for k, v in sorted(pr["by_phase"].items())},
        })

    total_cost = sum(x["cost"] for x in rows)
    total_time = sum(x["time"] for x in rows)
    total_tasks = sum(x["tasks"] for x in rows)
    wall = (launcher_exit - launcher_start).total_seconds() if (launcher_start and launcher_exit) else None

    # authoritative budget-exhaustion count from pool_report.json when complete
    report = ps.load_pool_report(run_dir) if run_dir is not None else None
    budget_exhaustions = report.get("budget_exhaustions_run_total") if report else None

    # Rounds whose cost lines come from more than one log (e.g. a crash/resume
    # boundary): their lines are summed as complementary halves. Exact-duplicate
    # physical lines are already de-duped above, but a full re-execution of a round
    # would inflate it — flag such rounds so the reader can sanity-check.
    overlap_rounds = sorted(r for r, logs in round_logs.items() if len(logs) > 1)

    return {
        "console_logs_read": read_paths,
        "cost_line_count": len(records),
        "overlap_rounds": overlap_rounds,
        "per_round": rows,
        "total_cost": total_cost,
        "total_time": total_time,
        "total_tasks": total_tasks,
        "wall_clock_seconds": wall,
        "launcher_exit_present": launcher_exit is not None,
        "exit_code": exit_code,
        "serper_search_failed": serper_failed,
        "budget_exhaustions_run_total": budget_exhaustions,
        "caption": CAPTION_H,
    }


def default_console_paths(run_dir: Path, explicit: Path | None = None) -> list[Path]:
    """Resolve the console log(s): explicit path (+ its resume sibling), else the
    conventional ``<parent>/<tag>.console.log`` and ``<tag>.resume.console.log``."""
    run_dir = Path(run_dir)
    tag = ps.run_tag(run_dir)
    parent = run_dir.resolve().parent
    candidates: list[Path] = []
    if explicit:
        explicit = Path(explicit)
        candidates.append(explicit)
        # resume sibling: foo.console.log -> foo.resume.console.log
        name = explicit.name
        if name.endswith(".console.log"):
            candidates.append(explicit.with_name(name[: -len(".console.log")] + ".resume.console.log"))
    else:
        candidates.append(parent / f"{tag}.console.log")
        candidates.append(parent / f"{tag}.resume.console.log")
    # de-dupe, keep existing only
    out, seen = [], set()
    for c in candidates:
        rc = str(Path(c).resolve())
        if rc not in seen and Path(c).exists():
            out.append(Path(c))
            seen.add(rc)
    return out


# --------------------------------------------------------------------------- #
# Markdown rendering
# --------------------------------------------------------------------------- #
def _pct(x) -> str:
    return f"{x:.1f}%" if isinstance(x, (int, float)) else "n/a"


def _pp(x) -> str:
    return f"{x:+.1f}pp" if isinstance(x, (int, float)) else "n/a"


def _band(b) -> str:
    return f"[{b[0]:+.1f}, {b[1]:+.1f}]pp" if b else "n/a"


def render_A(res: dict) -> list[str]:
    L = ["### A. Noise floor (empirical M-25)", "", res["caption"], ""]
    if not res["n"]:
        L += ["No settled no-ship round with a predecessor — noise floor unavailable.", ""]
        return L
    L += [f"- Samples (n): **{res['n']}** no-ship rounds",
          f"- Mean Δ: **{_pp(res['mean'])}**",
          f"- SD: **{res['sd_sample']:.2f}pp** (sample, ddof=1) / **{res['sd_pop']:.2f}pp** (population, ddof=0)",
          f"- |Δ| mean / max: **{res['abs_mean']:.2f}pp** / **{res['abs_max']:.2f}pp**",
          f"- Band ±1SD (sample): {_band(res['band_1sd_sample'])};  ±2SD (sample): {_band(res['band_2sd_sample'])}",
          f"- Band ±1SD (pop): {_band(res['band_1sd_pop'])};  ±2SD (pop): {_band(res['band_2sd_pop'])}",
          "",
          "| no-ship round | vs prev | Δ pass@2 |", "|:-:|:-:|:-:|"]
    for s in res["samples"]:
        L.append(f"| R{s['round']} | R{s['prev_round']} | {_pp(s['delta'])} |")
    L += ["",
          "> Reconciliation note: n counts every settled no-ship round that has a "
          "predecessor; a hand calc taken at an earlier in-flight snapshot sees fewer "
          "samples (any later-settled no-ship round adds one). The pre-registered "
          "'SD~=4.7pp' is the **population** SD (ddof=0).", ""]
    return L


def render_B(res: dict) -> list[str]:
    L = ["### B. Best-of-pool ceiling", "", res["caption"], "",
         f"- Union solved (any variant, any round): **{res['union_solved']}/{res['denominator']} "
         f"= {_pct(res['union_pct'])}**  _(pass@{res['union_equiv_pass_at']} equivalent)_",
         f"- Best single round (same-scope pass@2 reference): **R{res['best_single_round']} = "
         f"{res['best_single_pass']}/{res['denominator']} = {_pct(res['best_single_pct'])}**",
         f"- Settled rounds in union: {res['settled_rounds']}", ""]
    return L


def render_C(res: dict) -> list[str]:
    return ["### C. Post-hoc optimal routing replay", "", res["caption"], "",
            f"- Post-hoc optimal routing estimate: **{res['posthoc_score']:.2f}/{res['denominator']} "
            f"= {_pct(res['posthoc_pct'])}** (best-variant success rate per task, summed)",
            f"- Tasks with any measurement history: {res['tasks_with_history']}", ""]


def render_D(res: dict) -> list[str]:
    L = ["### D. Protocol stop-point reconstruction", "", res["caption"], "",
         "| patience | stop round | pass@2 at stop | triggered |", "|:-:|:-:|:-:|:-:|"]
    for p in res["patiences"]:
        s = res["stops"][p]
        if s.get("triggered"):
            L.append(f"| p{p} | R{s['stop_round']} | {_pct(s['stop_pct'])} | yes |")
        else:
            term = s.get("terminal_round")
            where = f"ran to end (R{term})" if term is not None else "n/a"
            L.append(f"| p{p} | {where} | {_pct(s['stop_pct'])} | no |")
    L.append("")
    return L


def render_E(res: dict) -> list[str]:
    L = ["### E. Variant specialization & routing dynamics", "", res["caption"], ""]
    if res["single_variant"]:
        L += ["**Single-variant / no-fork pool** — no specialization dynamics to report "
              f"(territory holders: {res['territory_holders'] or 'V0 only'}). Territory is "
              "constant and churn is ~0 by construction.", ""]
    # birth vs latest
    if res["birth_vs_latest"]:
        L += ["Birth cluster vs latest (cold-start over-confidence):", "",
              "| variant | birth R | birth terr | birth pass | latest R | latest terr | latest pass | gap |",
              "|:-:|:-:|:-:|:-:|:-:|:-:|:-:|:-:|"]
        for v in sorted(res["birth_vs_latest"]):
            b = res["birth_vs_latest"][v]
            L.append(f"| {v} | R{b['birth_round']} | {b['birth_territory']} | {_pct(b['birth_pct'])} | "
                     f"R{b['latest_round']} | {b['latest_territory']} | {_pct(b['latest_pct'])} | "
                     f"{_pp(b['overconfidence_gap'])} |")
        L.append("")
    # churn
    if res["churn"]:
        L += ["Per-round territory churn (tasks that changed owning variant vs prev round):", "",
              "| round | churn | of | % |", "|:-:|:-:|:-:|:-:|"]
        for r in sorted(res["churn"]):
            c = res["churn"][r]
            L.append(f"| R{r} | {c['changed']} | {c['total']} | {_pct(c['pct'])} |")
        L.append("")
    # survival
    if res["survival"]:
        L += ["Variant survival:", "",
              "| variant | birth R | last-alive R | rounds alive | currently alive |",
              "|:-:|:-:|:-:|:-:|:-:|"]
        for v in sorted(res["survival"]):
            s = res["survival"][v]
            L.append(f"| {v} | {('R'+str(s['birth_round'])) if s['birth_round'] is not None else '—'} | "
                     f"{('R'+str(s['last_alive_round'])) if s['last_alive_round'] is not None else '—'} | "
                     f"{s['alive_rounds']} | {'yes' if s['currently_alive'] else 'no'} |")
        L.append("")
    return L


def render_F(res: dict) -> list[str]:
    L = ["### F. M-23 manifestation (cross-variant regression)", "", res["caption"], ""]
    if res["single_variant_run"]:
        L += ["**Single-variant run (K=1 throughout)** — M-23 is cross-variant and cannot "
              "manifest here; manifestation is 0 by construction. Any candidate_diagnostics "
              "present are in-variant apply/reject records, not cross-variant regressions.", ""]
    if not res["data_available"]:
        L += [f"No candidate_diagnostics recorded in any settled round (fields queried: "
              f"{', '.join(res['fields_queried'])}) — **M-23 classification not available from this data.**", ""]
        return L
    L += [f"- Diagnostic rounds: {res['diag_round_count']}; cross-variant-regression rounds: "
          f"**{res['cross_variant_round_count']}** "
          f"({', '.join('R'+str(r) for r in res['cross_variant_rounds']) or 'none'})",
          f"- Manifestation rate (cross-variant / diagnostic rounds): "
          f"{res['manifestation_rate']:.2f}" if res["manifestation_rate"] is not None else "- Manifestation rate: n/a",
          f"- Total regressed task-instances: {res['total_regressed_task_instances']}", "",
          "| round | K | decisions | categories | failed_stage | improved | regressed | cross-variant |",
          "|:-:|:-:|:--|:--|:--|:-:|:-:|:-:|"]
    for m in res["manifest"]:
        L.append(f"| R{m['round']} | {m['variant_count']} | {m['decisions'] or '{}'} | "
                 f"{m['categories'] or '{}'} | {m['failed_stage'] or '{}'} | {m['improved_task_instances']} | "
                 f"{m['regressed_task_instances']} | {'yes' if m['cross_variant'] else 'no'} |")
    L.append("")
    return L


def render_G(res: dict) -> list[str]:
    L = ["### G. Difficulty stratification (GAIA Level)", "", res["caption"], ""]
    if not res["available"]:
        L += ["GAIA difficulty file not found / unreadable — stratification unavailable.", ""]
        return L
    lv = res["levels"]
    L += [f"- Level counts: " + ", ".join(f"L{k}={v}" for k, v in res["level_counts"].items()),
          f"- Unmapped routed tasks across rounds: {res['unmapped_total']}", "",
          "| round | " + " | ".join(f"L{l} pass@2" for l in lv) + " |",
          "|:-:|" + "|".join(":-:" for _ in lv) + "|"]
    for pr in res["per_round"]:
        cells = []
        for l in lv:
            c = pr["by_level"].get(l)
            cells.append(f"{c['pass']}/{c['den']} ({_pct(c['pct'])})" if c and c["den"] else "—")
        L.append(f"| R{pr['round']} | " + " | ".join(cells) + " |")
    L.append("")
    return L


def render_H(res: dict) -> list[str]:
    L = ["### H. Cost account", "", res["caption"], ""]
    if not res["console_logs_read"]:
        L += ["No console log found — cost account unavailable.", ""]
        return L
    L += [f"- Console log(s): {', '.join('`'+p+'`' for p in res['console_logs_read'])}",
          f"- Cost lines parsed: {res['cost_line_count']}",
          f"- Run total cost (internal assumed price): **${res['total_cost']:.2f}** over "
          f"{res['total_tasks']} task-evals",
          f"- Run total eval time (compute-seconds, concurrent): {res['total_time']:.0f}s",
          f"- Wall clock (LAUNCHER): "
          + (f"{res['wall_clock_seconds']:.0f}s" if res['wall_clock_seconds'] is not None
             else "n/a — LAUNCHER_EXIT absent (in-flight)"),
          f"- Serper `search failed` count: {res['serper_search_failed']} "
          f"(successful calls unlogged — see caption)",
          f"- Budget exhaustions (authoritative, pool_report.json): "
          + (str(res['budget_exhaustions_run_total']) if res['budget_exhaustions_run_total'] is not None
             else "n/a (run in-flight; no pool_report.json)"),
          (f"- CAVEAT: rounds spanning >1 log (crash/resume boundary): "
           f"{', '.join('R'+str(r) for r in res['overlap_rounds'])} — their cost lines are summed as "
           f"complementary halves (task-evals stay <= one round's worth; a full re-run would double it)."
           if res.get("overlap_rounds") else "- No crash/resume round overlap detected."),
          "",
          "| round | cost $ | eval-time s | task-evals | passes | budget grants | $ granted | by-phase |",
          "|:-:|:-:|:-:|:-:|:-:|:-:|:-:|:--|"]
    for r in res["per_round"]:
        L.append(f"| R{r['round']} | ${r['cost']:.2f} | {r['time']:.0f} | {r['tasks']} | {r['passes']} | "
                 f"{r['budget_grants']} | ${r['budget_granted']:.2f} | {dict(r['by_phase'])} |")
    L.append("")
    return L


def build_deep_sections(rounds: list[ps.RoundData], run_dir: Path,
                        console_paths: list[Path], level_map: dict[str, int]) -> list[str]:
    """The eight T1 deep-metric Markdown sections, in order A-H."""
    L: list[str] = ["## T1 deep metrics (A-H)", ""]
    L += render_A(noise_floor(rounds))
    L += render_B(best_of_pool(rounds))
    L += render_C(posthoc_optimal_routing(rounds))
    L += render_D(protocol_stops(rounds))
    L += render_E(variant_dynamics(rounds))
    L += render_F(m23_manifestation(rounds))
    L += render_G(difficulty_strata(rounds, level_map))
    L += render_H(cost_account(console_paths, run_dir))
    return L


# --------------------------------------------------------------------------- #
# LaTeX tables
# --------------------------------------------------------------------------- #
def _tex_escape(s: str) -> str:
    return str(s).replace("_", r"\_").replace("%", r"\%").replace("&", r"\&")


def latex_curve_table(rounds: list[ps.RoundData]) -> str:
    settled = _settled(rounds)
    lines = [r"% requires \usepackage{booktabs}",
             r"\begin{tabular}{rrrrr}", r"\toprule",
             r"Round & $K$ & pass@2 (\%) & num/den & $\Delta$ prev (pp) \\", r"\midrule"]
    prev = None
    for rd in settled:
        d = "" if prev is None else f"{rd.pool_pct - prev:+.1f}"
        lines.append(f"R{rd.round} & {rd.variant_count} & {rd.pool_pct:.1f} & "
                     f"{rd.pool_pass}/{rd.denominator} & {d} \\\\")
        prev = rd.pool_pct
    lines += [r"\bottomrule", r"\end{tabular}"]
    return "\n".join(lines)


def latex_noise_table(res: dict) -> str:
    lines = [r"% requires \usepackage{booktabs}",
             r"\begin{tabular}{ll}", r"\toprule",
             r"Noise-floor statistic & value \\", r"\midrule"]
    if not res["n"]:
        lines += [r"samples & 0 (unavailable) \\", r"\bottomrule", r"\end{tabular}"]
        return "\n".join(lines)
    b1s, b2s = res["band_1sd_sample"], res["band_2sd_sample"]
    lines += [
        f"samples $n$ & {res['n']} \\\\",
        f"mean $\\Delta$ & {res['mean']:+.2f} pp \\\\",
        f"SD (sample, ddof=1) & {res['sd_sample']:.2f} pp \\\\",
        f"SD (population, ddof=0) & {res['sd_pop']:.2f} pp \\\\",
        f"$|\\Delta|$ mean & {res['abs_mean']:.2f} pp \\\\",
        f"$|\\Delta|$ max & {res['abs_max']:.2f} pp \\\\",
        f"$\\pm 1$SD band (sample) & [{b1s[0]:+.1f}, {b1s[1]:+.1f}] pp \\\\",
        f"$\\pm 2$SD band (sample) & [{b2s[0]:+.1f}, {b2s[1]:+.1f}] pp \\\\",
        r"\bottomrule", r"\end{tabular}"]
    return "\n".join(lines)


def latex_territory_table(res: dict) -> str:
    lines = [r"% requires \usepackage{booktabs}"]
    if res.get("single_variant"):
        lines.append(r"% single-variant pool (no fork) -- V0 territory shown for reference")
    lines += [r"\begin{tabular}{lrrrrrrr}", r"\toprule",
              r"Variant & birth R & birth terr & birth pass (\%) & latest R & latest terr & "
              r"latest pass (\%) & gap (pp) \\", r"\midrule"]
    if not res["birth_vs_latest"]:
        lines += [r"\multicolumn{8}{c}{single-variant pool -- no territory dynamics} \\",
                  r"\bottomrule", r"\end{tabular}"]
        return "\n".join(lines)
    for v in sorted(res["birth_vs_latest"]):
        b = res["birth_vs_latest"][v]
        bp = f"{b['birth_pct']:.0f}" if b["birth_pct"] is not None else "--"
        lp = f"{b['latest_pct']:.0f}" if b["latest_pct"] is not None else "--"
        gap = f"{b['overconfidence_gap']:+.0f}" if b["overconfidence_gap"] is not None else "--"
        lines.append(f"{_tex_escape(v)} & R{b['birth_round']} & {b['birth_territory']} & {bp} & "
                     f"R{b['latest_round']} & {b['latest_territory']} & {lp} & {gap} \\\\")
    lines += [r"\bottomrule", r"\end{tabular}"]
    return "\n".join(lines)


def write_latex_tables(tag: str, rounds: list[ps.RoundData], out_dir: Path) -> list[Path]:
    """Write curve / noise-band / territory LaTeX tabulars to out/<tag>/tables/."""
    tables_dir = Path(out_dir) / "tables"
    tables_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for name, tex in (("curve.tex", latex_curve_table(rounds)),
                      ("noise_band.tex", latex_noise_table(noise_floor(rounds))),
                      ("territory.tex", latex_territory_table(variant_dynamics(rounds)))):
        p = tables_dir / name
        p.write_text(tex + "\n", encoding="utf-8")
        written.append(p)
    return written


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
_RENDERERS = {
    "A": lambda rounds, run_dir, cp, lm: render_A(noise_floor(rounds)),
    "B": lambda rounds, run_dir, cp, lm: render_B(best_of_pool(rounds)),
    "C": lambda rounds, run_dir, cp, lm: render_C(posthoc_optimal_routing(rounds)),
    "D": lambda rounds, run_dir, cp, lm: render_D(protocol_stops(rounds)),
    "E": lambda rounds, run_dir, cp, lm: render_E(variant_dynamics(rounds)),
    "F": lambda rounds, run_dir, cp, lm: render_F(m23_manifestation(rounds)),
    "G": lambda rounds, run_dir, cp, lm: render_G(difficulty_strata(rounds, lm)),
    "H": lambda rounds, run_dir, cp, lm: render_H(cost_account(cp, run_dir)),
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Deep T1 metrics (A-H) for a variant-pool run (read-only).")
    parser.add_argument("--run-dir", required=True, help="Path to runs/<tag>")
    parser.add_argument("--console-log", help="Console log path (resume sibling auto-added).")
    parser.add_argument("--data", help=f"GAIA difficulty JSON (default: {DEFAULT_GAIA_DATA}).")
    parser.add_argument("--metric", default="all", choices=list(_RENDERERS) + ["all"],
                        help="Which metric to print (default: all).")
    parser.add_argument("--latex", action="store_true", help="Also write LaTeX tables to out/<tag>/tables/.")
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
    console_paths = default_console_paths(run_dir, Path(args.console_log) if args.console_log else None)
    level_map = load_level_map(Path(args.data) if args.data else None)

    if args.metric == "all":
        lines = build_deep_sections(rounds, run_dir, console_paths, level_map)
    else:
        lines = _RENDERERS[args.metric](rounds, run_dir, console_paths, level_map)
    print("\n".join(lines))

    if args.latex:
        written = write_latex_tables(tag, rounds, ps.out_dir_for(tag))
        for p in written:
            print(f"wrote LaTeX -> {p}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
