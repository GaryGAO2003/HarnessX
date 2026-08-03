# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""H -- config-epoch curve: peak/final defined on configuration blocks, plus the
paper's Table 8 +/-5% noise band.

Why this exists
---------------
The reported curve takes ``peak`` as the max over per-round pass rates. On this
bed a *single* configuration re-measured yields a range of ~8.7pp (analysis in
``08-PROBLEM-INVENTORY.md`` P3), so a per-round max is a max over noisy draws and
inflates ``peak - final`` by construction. This module instead partitions rounds
into **configuration epochs** and compares epoch means.

An epoch is a maximal run of consecutive rounds in which the deployed pool
configuration did not change, i.e. rounds where ``shipped`` is false. A shipped
round is a *transition*: its own measurement is a mix of the pre-ship carriers
(fresh) and the newborn/applied variant (``candidate_reuse``), so it belongs to
neither epoch and is excluded.

Two useful consequences fall out of that exclusion:

* every ``candidate_reuse`` block in these runs lives on a shipped round, so
  dropping shipped rounds removes the P6 scoring contamination outright -- no
  imputation needed;
* epochs with n >= 2 are *identical-config replicates*, i.e. a built-in A/A
  calibration, which is where the pooled within-epoch SD below comes from.

Zero API cost: reads persisted ``pool_state.json`` only.
"""

from __future__ import annotations

import sys
from statistics import mean, stdev

from _common import DEFAULT_RUN, all_states, eprint, pool_state, round_indices

# Table 8 (p.29): ``noise threshold | ignored single-round pass-count delta | +/-5%``.
# PAPER-METHODOLOGY-DEVIATIONS M-25 rules this in at the *analysis* layer only --
# the gate stays zero-tolerance per §4.1. Expressed as a fraction of the bed.
NOISE_BAND = 0.05


def solved_counts(state: dict) -> tuple[int, int]:
    """(n_solved, n_tasks) merged across carriers for one round."""
    merged: dict[str, tuple[int, int]] = {}
    for tasks in state.get("active_pool_measurements", {}).values():
        for tid, sa in tasks.items():
            merged[tid] = (int(sa[0]), int(sa[1]))
    return sum(1 for p, _ in merged.values() if p >= 1), len(merged)


def reuse_blocks(state: dict) -> dict[str, int]:
    """variant -> cell count for blocks scored from a candidate rollout."""
    out = {}
    for vid, src in (state.get("active_score_source") or {}).items():
        if isinstance(src, str) and "reuse" in src.lower():
            out[vid] = len(state.get("active_pool_measurements", {}).get(vid, {}))
    return out


def routing_key(state: dict) -> tuple:
    """Byte-level identity of the deployed partition: variant -> its exact task set.

    Two rounds can both be non-shipped and still not be replicates: freeze_routing
    re-runs the per-cluster argmax every round, so a cluster can migrate between
    variants (s1k8b103 R8->R9 moves the 12-task cluster from V5 to V4) and a
    retire/reassign moves orphans. Either changes *which config runs which task*,
    so the aggregate is no longer a re-measurement of the same system. Requiring
    an identical partition is what makes an epoch a genuine A/A replicate.
    """
    return tuple(sorted((vid, tuple(sorted(tasks))) for vid, tasks in (state.get("routing") or {}).items()))


def epochs(states: dict[int, dict]) -> list[list[int]]:
    """Maximal runs of consecutive non-shipped rounds sharing one routing partition."""
    blocks: list[list[int]] = []
    current: list[int] = []
    for r in sorted(states):
        if states[r].get("shipped") or states[r].get("retired"):
            if current:
                blocks.append(current)
            current = []
            continue
        same = current and r == current[-1] + 1 and routing_key(states[r]) == routing_key(states[current[-1]])
        if current and not same:
            blocks.append(current)
            current = []
        current.append(r)
    if current:
        blocks.append(current)
    return blocks


def analyse(run: str) -> dict:
    states = all_states(run)
    rates = {}
    n_tasks = 0
    for r in sorted(states):
        solved, n = solved_counts(states[r])
        rates[r] = solved / n
        n_tasks = max(n_tasks, n)

    ships = [r for r in sorted(states) if states[r].get("shipped")]
    blocks = epochs(states)

    eprint(f"\n=== {run} : config-epoch curve (bed n={n_tasks}) ===")
    eprint(f"shipped rounds (transitions, excluded): {ships}")
    eprint("")
    eprint(f"{'epoch':>7} {'rounds':>22} {'n':>3} {'mean':>8} {'sd':>7}  observations")
    eprint("-" * 92)

    summaries = []
    for i, block in enumerate(blocks):
        vals = [rates[r] * 100 for r in block]
        m = mean(vals)
        sd = stdev(vals) if len(vals) > 1 else None
        summaries.append({"idx": i, "rounds": block, "n": len(vals), "mean": m, "sd": sd, "vals": vals})
        rng = f"R{block[0]}-R{block[-1]}" if len(block) > 1 else f"R{block[0]}"
        eprint(
            f"{i:>7} {rng:>22} {len(vals):>3} {m:>8.2f} "
            f"{('%.2f' % sd) if sd is not None else '   -':>7}  {[round(v, 1) for v in vals]}"
        )

    # Pooled within-epoch SD -- the identical-config noise floor.
    num = sum((s["n"] - 1) * s["sd"] ** 2 for s in summaries if s["sd"] is not None)
    df = sum(s["n"] - 1 for s in summaries if s["n"] > 1)
    pooled = (num / df) ** 0.5 if df else None
    eprint("")
    if pooled is not None:
        eprint(f"pooled within-epoch SD = {pooled:.2f}pp  (df={df})   <- identical-config noise floor")
    else:
        eprint("pooled within-epoch SD = n/a (no epoch has n>=2)")
    if df < 3:
        eprint("   ** DEGENERATE: this run has almost no identical-config replicates. Its routing")
        eprint("      partition changes between rounds even when nothing ships (freeze_routing re-runs")
        eprint("      the per-cluster argmax every round), so consecutive rounds measure different")
        eprint("      deployed systems and neither the SD nor a cross-round peak/final is meaningful.")

    # Epoch-level peak/final vs the per-round definition the paper reports.
    per_round_peak_r = max(rates, key=lambda r: rates[r])
    per_round_peak = rates[per_round_peak_r] * 100
    per_round_final = rates[max(rates)] * 100
    eprint("")
    eprint(f"per-round  : peak={per_round_peak:.2f} @R{per_round_peak_r}  final={per_round_final:.2f}  "
           f"peak-final={per_round_peak - per_round_final:+.2f}pp")

    if summaries:
        peak_e = max(summaries, key=lambda s: s["mean"])
        final_e = summaries[-1]
        delta = peak_e["mean"] - final_e["mean"]
        line = (f"epoch-level: peak={peak_e['mean']:.2f} @epoch{peak_e['idx']}"
                f"(n={peak_e['n']})  final={final_e['mean']:.2f} @epoch{final_e['idx']}"
                f"(n={final_e['n']})  peak-final={delta:+.2f}pp")
        if pooled is not None and peak_e is not final_e:
            se = pooled * (1 / peak_e["n"] + 1 / final_e["n"]) ** 0.5
            line += f"  SE={se:.2f}  t={delta / se:+.2f} (df={df})"
        eprint(line)

    # Per-ship effect: consecutive epochs are separated by exactly one shipped
    # round, so the epoch-mean difference *is* that ship's measured effect --
    # the only causal read this data supports (a per-round diff straddling a
    # ship confounds the edit with the transition round's mixed scoring).
    if pooled is not None and len(summaries) > 1:
        eprint("")
        eprint("per-ship effect (epoch mean difference across each shipped round):")
        for prev, cur in zip(summaries, summaries[1:]):
            if prev["n"] < 2 and cur["n"] < 2:
                continue
            d = cur["mean"] - prev["mean"]
            se = pooled * (1 / prev["n"] + 1 / cur["n"]) ** 0.5
            t = d / se
            verdict = "SIGNIFICANT" if abs(t) > 2.228 else "n.s."  # df=10 two-sided .05
            eprint(f"   epoch{prev['idx']}(n={prev['n']}) -> epoch{cur['idx']}(n={cur['n']}): "
                   f"{d:+6.2f}pp  SE={se:.2f}  t={t:+.2f}  {verdict}")

    # Table 8 noise band on consecutive single-round deltas.
    band_tasks = NOISE_BAND * n_tasks
    eprint("")
    eprint(f"Table 8 noise band: |delta| <= {NOISE_BAND:.0%} of bed = {band_tasks:.1f} tasks "
           f"({NOISE_BAND * 100:.1f}pp)")
    ordered = sorted(rates)
    inside = outside = 0
    for prev, cur in zip(ordered, ordered[1:]):
        d = (rates[cur] - rates[prev]) * 100
        if abs(d) <= NOISE_BAND * 100:
            inside += 1
        else:
            outside += 1
            mark = "SHIP" if states[cur].get("shipped") else "    "
            eprint(f"   R{prev}->R{cur}: {d:+6.2f}pp  outside band  {mark}")
    eprint(f"   {inside}/{inside + outside} consecutive deltas fall INSIDE the noise band")

    # P6 cross-check: every reuse block should sit on a shipped round.
    stray = [r for r in sorted(states) if reuse_blocks(states[r]) and not states[r].get("shipped")]
    eprint("")
    eprint(f"reuse blocks on non-shipped rounds: {stray if stray else 'none'} "
           f"(none => excluding ships removes all P6 contamination)")
    return {"run": run, "pooled": pooled, "df": df, "summaries": summaries}


def main(argv: list[str]) -> int:
    runs = argv[1:] or [DEFAULT_RUN]
    results = [analyse(run) for run in runs]

    usable = [r for r in results if r["pooled"] is not None]
    if len(usable) > 1:
        num = sum(r["pooled"] ** 2 * r["df"] for r in usable)
        df = sum(r["df"] for r in usable)
        eprint("")
        eprint("=" * 60)
        eprint(f"pooled across runs: SD = {(num / df) ** 0.5:.2f}pp  (df={df})")
        for target in (2.0, 3.0, 4.0):
            sd = (num / df) ** 0.5
            n = (2.802 * sd * (2 ** 0.5) / target) ** 2
            eprint(f"   unpaired two-arm MDE {target:.0f}pp @80% power => {n:.0f} rounds per arm")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
