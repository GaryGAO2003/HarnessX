"""Read out one or two evolution arms against the pre-registered rules.

Everything here is fixed by the freeze packages before the runs land
(``FREEZE-CAPABILITY-ARM.md`` §3, ``FREEZE-CH4-ARMS.md`` §3). It is written now,
against no results, so that the choice of statistic cannot be made after seeing
which one flatters the outcome.

Reads:

* **R-1 differentiation**, three axes — distinct system prompts, dispersion of
  per-cluster win rates, distinct normalised config digests. All three must move
  together; one axis alone licenses nothing.
* **R-2 load** — final Gini, variants holding a task, variants that ever held
  one. This is the only readout with a prior prediction (the offline replay), so
  it is the one that can be wrong rather than merely descriptive.
* **R-3 accuracy** — per-round pass@2 / pass@1, final, peak, final-peak.
* **R-4 paired test** — McNemar on the two arms' final per-task outcomes.

Two things it deliberately does not do: it never compares absolute scores across
arms (each arm moves relative to its own R0, since the same H0 has measured
64.1 / 66.0 / 69.9 on this bench), and it never reports peak drift without the
max-of-N correction alongside.
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import math
import statistics
from pathlib import Path

RUNS = Path("recipe/gaia_evolver/runs")
#: Directly measured at n=103. The floor scales as 1/sqrt(n) -- measured again
#: at n=50 giving 6.41pp against a predicted 4.57*sqrt(103/50)=6.56 -- so a run
#: on a smaller bench must scale it up rather than inherit this number. Using the
#: n=103 figure on a 30-task bench under-corrects the peak bias and overstates
#: degradation, which is the direction that flatters a "we found decay" claim.
NOISE_SD_103 = 4.57
NOISE_REF_N = 103


def noise_sd(n_tasks: int) -> float:
    return NOISE_SD_103 * math.sqrt(NOISE_REF_N / max(1, n_tasks))


# --- helpers --------------------------------------------------------------


def _rounds(run: Path) -> list[dict]:
    out = []
    for i in range(64):
        p = run / f"R{i}" / "pool_state.json"
        if p.exists():
            out.append(json.loads(p.read_text(encoding="utf-8")))
    return out


def _gini(values) -> float:
    s = sorted(values)
    n, tot = len(s), sum(s)
    if not n or not tot:
        return 0.0
    return sum((2 * i - n + 1) * x for i, x in enumerate(s)) / (n * tot)


def _cells(state: dict) -> dict[str, dict[str, tuple[int, int]]]:
    """variant -> task -> (passes, attempts). Stored as [passes, attempts]."""
    out = {}
    for vid, per in (state.get("active_pool_measurements") or {}).items():
        out[vid] = {
            t: (int(o[0]), int(o[1]))
            for t, o in per.items()
            if isinstance(o, list) and len(o) == 2
        }
    return out


def _pass_at_2(cells: dict) -> tuple[int, int]:
    solved = sum(1 for per in cells.values() for a, _ in per.values() if a >= 1)
    total = sum(len(per) for per in cells.values())
    return solved, total


def _pass_at_1(cells: dict) -> tuple[int, int]:
    p = sum(a for per in cells.values() for a, _ in per.values())
    n = sum(b for per in cells.values() for _, b in per.values())
    return p, n


# --- readouts -------------------------------------------------------------


def r1_differentiation(run: Path, states: list[dict], clusters: dict | None) -> dict:
    last = states[-1]
    live = [v for v, ts in last["routing"].items() if ts] or list(last["routing"])

    prompts, configs = set(), set()
    for vid in live:
        for rel in (f"R{last['round']}/active_pool/{vid}", f"R{last['round']}/{vid}"):
            cfg = run / rel / "config.yaml"
            if cfg.exists():
                body = cfg.read_text(encoding="utf-8", errors="replace")
                configs.add(hashlib.sha256(body.encode()).hexdigest())
                for line in body.splitlines():
                    if "template_path" in line or "system_prompt" in line:
                        prompts.add(line.strip())
                break

    # Capability axis: per variant, the spread of its win rate across clusters.
    # A generalist scores flat; a specialist scores high on its own cluster and
    # low elsewhere, so a rising mean dispersion is what specialisation looks
    # like in the ledger.
    dispersion = None
    cells = _cells(last)
    if clusters:
        spreads = []
        for vid, per in cells.items():
            by_cluster = collections.defaultdict(lambda: [0, 0])
            for task, (a, b) in per.items():
                c = clusters.get(task)
                if c is None:
                    continue
                by_cluster[c][0] += a
                by_cluster[c][1] += b
            rates = [(p + 1) / (n + 2) for p, n in by_cluster.values() if n]
            if len(rates) >= 2:
                spreads.append(statistics.pstdev(rates))
        if spreads:
            dispersion = statistics.mean(spreads)

    return {
        "live_variants": len(live),
        "distinct_prompt_lines": len(prompts),
        "distinct_config_digests": len(configs),
        "mean_per_cluster_dispersion": dispersion,
    }


def r2_load(states: list[dict]) -> dict:
    last = states[-1]
    loads = {v: len(ts) for v, ts in last["routing"].items()}
    ever = {v for st in states for v, ts in st["routing"].items() if ts}
    return {
        "final_gini": _gini(loads.values()),
        "loaded_now": sum(1 for x in loads.values() if x),
        "pool_size": len(loads),
        "ever_loaded": len(ever),
        "final_distribution": sorted(loads.values(), reverse=True),
    }


def r3_accuracy(states: list[dict]) -> dict:
    n_tasks = max((sum(len(p) for p in _cells(st).values()) for st in states), default=0)
    sd = noise_sd(n_tasks or NOISE_REF_N)
    curve, curve1 = [], []
    for st in states:
        c = _cells(st)
        s, t = _pass_at_2(c)
        p, n = _pass_at_1(c)
        curve.append(round(100 * s / t, 1) if t else None)
        curve1.append(round(100 * p / n, 1) if n else None)
    real = [x for x in curve if x is not None]
    peak = max(real) if real else None
    final = real[-1] if real else None
    out = {
        "bench_tasks": n_tasks,
        "noise_sd_pp": round(sd, 2),
        "pass_at_2_curve": curve,
        "pass_at_1_curve": curve1,
        "r0": real[0] if real else None,
        "final": final,
        "peak": peak,
        "final_minus_peak": round(final - peak, 1) if real else None,
    }
    if real:
        # peak is a max over N draws, so even with no real drift its expectation
        # sits above the mean and final-minus-peak is negative by construction.
        # Reporting that difference alone reports an artefact of taking a
        # maximum. Blom's approximation for the expected maximum of n iid
        # normals, E[max] ~ sigma * Phi^-1((n - 0.375) / (n + 0.25)), which gives
        # ~1.77 SD at n=16 and matches the 1.8 SD quoted in the freeze packages.
        n = len(real)
        bias = sd * _blom_expected_max(n) if n > 1 else 0.0
        out["expected_max_of_n_bias_pp"] = round(bias, 1)
        out["drift_after_debias"] = round(final - peak + bias, 1)
        # The correction assumes the peak is the maximum of n draws from a
        # stationary distribution. Where a run genuinely improved, part of the
        # peak is signal rather than a lucky draw and the full correction removes
        # too much, so the de-biased figure is an UPPER BOUND on the true drift,
        # not a point estimate. On s2k8b50 -- twelve gate-evaluated candidates,
        # zero improvements, raw drift 0.0 -- it reads +11.0, which is the
        # over-correction showing itself rather than an eleven-point gain.
        out["debias_is_upper_bound"] = True
    return out


def _inv_norm_cdf(p: float) -> float:
    """Acklam's rational approximation to the standard normal quantile."""
    a = [-3.969683028665376e01, 2.209460984245205e02, -2.759285104469687e02,
         1.383577518672690e02, -3.066479806614716e01, 2.506628277459239e00]
    b = [-5.447609879822406e01, 1.615858368580409e02, -1.556989798598866e02,
         6.680131188771972e01, -1.328068155288572e01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e00,
         -2.549732539343734e00, 4.374664141464968e00, 2.938163982698783e00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e00,
         3.754408661907416e00]
    plow, phigh = 0.02425, 1 - 0.02425
    if p < plow:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / (
            (((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
    if p > phigh:
        q = math.sqrt(-2 * math.log(1 - p))
        return -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / (
            (((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
    q = p - 0.5
    r = q * q
    return (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q / (
        ((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1)


def _blom_expected_max(n: int) -> float:
    """Expected maximum of n iid standard normals, in SD units."""
    return _inv_norm_cdf((n - 0.375) / (n + 0.25))


def r4_paired(a: list[dict], b: list[dict]) -> dict:
    """McNemar on the two arms' final per-task pass@2 outcomes."""
    def solved(states):
        out = {}
        for vid, per in _cells(states[-1]).items():
            for t, (p, _) in per.items():
                out[t] = out.get(t, 0) or (1 if p >= 1 else 0)
        return out

    x, y = solved(a), solved(b)
    shared = sorted(set(x) & set(y))
    b01 = sum(1 for t in shared if not x[t] and y[t])
    b10 = sum(1 for t in shared if x[t] and not y[t])
    n = b01 + b10
    stat = ((abs(b01 - b10) - 1) ** 2 / n) if n else 0.0
    diff = 100 * (sum(y[t] for t in shared) - sum(x[t] for t in shared)) / len(shared)
    sd = noise_sd(len(shared))
    return {
        "paired_tasks": len(shared),
        "only_arm_b": b01,
        "only_arm_a": b10,
        "mcnemar_chi2_cc": round(stat, 3),
        "significant_at_0_05": stat > 3.841,
        "aggregate_diff_pp": round(diff, 1),
        "clears_2sd_floor": abs(diff) > 2 * sd,
        "noise_floor_2sd_pp": round(2 * sd, 1),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--arm", required=True)
    ap.add_argument("--vs", default=None)
    ap.add_argument("--cluster-map", default=None)
    args = ap.parse_args()

    clusters = None
    if args.cluster_map:
        clusters = json.loads(Path(args.cluster_map).read_text(encoding="utf-8"))["clusters"]

    def report(tag: str):
        run = RUNS / tag
        states = _rounds(run)
        if not states:
            raise SystemExit(f"{run}: no settled rounds")
        print(f"\n{'=' * 66}\n### {tag}  ({len(states)} settled rounds)")
        for name, data in (
            ("R-1 differentiation", r1_differentiation(run, states, clusters)),
            ("R-2 load", r2_load(states)),
            ("R-3 accuracy", r3_accuracy(states)),
        ):
            print(f"\n{name}")
            for k, v in data.items():
                print(f"  {k:<32} {v}")
        return states

    a = report(args.arm)
    if args.vs:
        b = report(args.vs)
        print(f"\n{'=' * 66}\n### paired  {args.arm} -> {args.vs}")
        for k, v in r4_paired(a, b).items():
            print(f"  {k:<32} {v}")
        print(
            "\n  Read with the rules: each arm moves relative to its OWN R0 "
            "(the same H0 has measured 64.1 / 66.0 / 69.9 here), and an "
            "aggregate difference below the 2 SD floor licenses no claim."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
