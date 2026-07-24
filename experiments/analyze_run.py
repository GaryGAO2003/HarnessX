"""Post-run analysis: outcomes by level, cache-aware cost, and M0 projection.

Reads a gaia_evolver run directory and reports what ``comparison.json`` alone
cannot: the per-call token split including cache reads (which lives in the
session logs, not in the per-task records) priced through
``experiments/variant_pool/accounting.py``.

The repo's own ``cost_usd`` is deliberately ignored: ``runloop._estimate_cost``
hardcodes Claude Sonnet pricing, so on DeepSeek it overstates the bill by ~66x.

Usage:
    python experiments/analyze_run.py recipe/gaia_evolver/runs/pilot30
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

EXPERIMENTS_DIR = Path(__file__).resolve().parent
if str(EXPERIMENTS_DIR) not in sys.path:
    sys.path.insert(0, str(EXPERIMENTS_DIR))

from variant_pool.accounting import AttemptCost, CostLedger, Pricing  # noqa: E402

#: Full-scale M0 (checklist §6): 3 lineages x 103 tasks x 15 rounds x pass@2.
M0_ATTEMPTS = 3 * 103 * 15 * 2


def find_usage(obj: Any) -> dict | None:
    """Locate the nested usage block in a session-log line."""
    if isinstance(obj, dict):
        if "input_tokens" in obj or "cache_read_tokens" in obj:
            return obj
        for value in obj.values():
            found = find_usage(value)
            if found:
                return found
    elif isinstance(obj, list):
        for value in obj:
            found = find_usage(value)
            if found:
                return found
    return None


def collect_costs(run_dir: Path, ledger: CostLedger) -> tuple[int, int, int, int]:
    """Walk every round's session logs, recording one AttemptCost per model call.

    Returns ``(calls, prompt_tokens, cached_tokens, output_tokens)``. Prompt
    tokens include the cached prefix (that is what providers report); the
    ledger stores misses and hits separately.
    """
    calls = prompt = cached = output = 0
    for round_dir in sorted(run_dir.glob("R*")):
        try:
            round_idx = int(round_dir.name[1:])
        except ValueError:
            continue
        sessions = round_dir / "sessions"
        if not sessions.is_dir():
            continue
        for log in sessions.rglob("*.jsonl"):
            if log.name.endswith("_trace.jsonl"):  # timing only, no usage
                continue
            task_id = log.parent.name.split("-", 1)[-1]
            for line in log.read_text(encoding="utf-8").splitlines():
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                usage = find_usage(event)
                if not usage:
                    continue
                in_tok = int(usage.get("input_tokens", 0) or 0)
                cache_tok = int(usage.get("cache_read_tokens", 0) or 0)
                out_tok = int(usage.get("output_tokens", 0) or 0)
                if in_tok == 0 and out_tok == 0:
                    continue
                calls += 1
                prompt += in_tok
                cached += cache_tok
                output += out_tok
                ledger.record(
                    AttemptCost(
                        task_id=task_id,
                        round_idx=round_idx,
                        variant_id="V0",
                        role="task_agent",
                        input_tokens=max(in_tok - cache_tok, 0),
                        cached_input_tokens=cache_tok,
                        output_tokens=out_tok,
                    )
                )
    return calls, prompt, cached, output


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("run_dir", type=Path)
    args = ap.parse_args()

    run_dir: Path = args.run_dir
    comparison = json.loads((run_dir / "comparison.json").read_text(encoding="utf-8"))
    rounds = comparison["rounds"]

    print("=" * 72)
    print(f"OUTCOMES  ({len(rounds)} round(s), {len(rounds[0])} tasks)")
    print("=" * 72)
    curve = []
    for idx, records in enumerate(rounds):
        passed = sum(1 for r in records if r.get("passed"))
        curve.append(passed / len(records) if records else 0.0)
        exits = Counter(r.get("exit_reason") for r in records)
        by_level: dict[int, list[int]] = defaultdict(lambda: [0, 0])
        for r in records:
            by_level[r.get("level", 0)][1] += 1
            if r.get("passed"):
                by_level[r.get("level", 0)][0] += 1
        levels = " ".join(f"L{lv}:{p}/{n}" for lv, (p, n) in sorted(by_level.items()))
        print(f"  R{idx}: {passed}/{len(records)} = {curve[-1]:6.1%}  {levels}   {dict(exits)}")

    if curve:
        peak = max(curve)
        peak_round = curve.index(peak)
        # Both, always — the paper reports peak only (§7.7), which hides collapse.
        print(f"\n  final = {curve[-1]:.1%} (R{len(curve) - 1})   peak = {peak:.1%} (R{peak_round})")
        print(f"  final - peak = {curve[-1] - peak:+.1%}   <- negative means degradation")

    ledger = CostLedger()
    calls, prompt, cached, output = collect_costs(run_dir, ledger)

    print()
    print("=" * 72)
    print("COST (cache-aware, priced from tokens)")
    print("=" * 72)
    if not calls:
        print("  no model calls found in session logs")
        return
    hit = ledger.cache_hit_rate()
    flash = Pricing.deepseek_v4_flash()
    billed = ledger.total_billed(flash)
    real = sum(billed.values()) if isinstance(billed, dict) else billed
    no_cache = (prompt * 0.14 + output * 0.28) / 1e6
    attempts = sum(len(r) for r in rounds)

    print(f"  model calls      : {calls:,}")
    print(f"  prompt tokens    : {prompt:,}  (cached: {cached:,})")
    print(f"  output tokens    : {output:,}")
    print(f"  cache hit rate   : {hit:.1%}" if hit is not None else "  cache hit rate   : n/a")
    print(f"  billed (real)    : ${real:.4f}")
    print(f"  billed (no cache): ${no_cache:.4f}   <- upper bound if the prefix stops being stable")
    print(f"  per attempt      : {prompt / attempts:,.0f} prompt tokens, ${real / attempts:.5f}")
    repo_cost = sum(r.get("cost_usd", 0) for rd in rounds for r in rd)
    print(f"  repo cost_usd    : ${repo_cost:.2f}  (hardcoded Sonnet price — ignore, off by ~{repo_cost / real:.0f}x)")

    print()
    print("=" * 72)
    print("M0 PROJECTION (3 lineages x 103 tasks x 15 rounds x pass@2)")
    print("=" * 72)
    print(f"  attempts             : {M0_ATTEMPTS:,}")
    print(f"  task agent, cached   : ${real / attempts * M0_ATTEMPTS:,.0f}")
    print(f"  task agent, no cache : ${no_cache / attempts * M0_ATTEMPTS:,.0f}")
    print("\n  NOTE: meta-agent cost is not in this figure unless the run had >1 round.")


if __name__ == "__main__":
    main()
