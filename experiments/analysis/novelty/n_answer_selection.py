# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""The masking gap: pass@2 is an oracle-OR, but deployment ships one answer.

pass@2 - pass@1 is the headline metric's slack -- the space between what the
harness CAN produce and what it can DELIVER. On the 103-task bed that slack is
an order of magnitude larger than every complementarity effect we have measured
(cross-variant excess +0.78pp CI spanning 0; best failure-mode partition
+1.12pp). And unlike routing, closing it does not need a-priori predictability
of which variant suits which task -- only post-hoc distinguishability of which
attempt got it right.

So: how much of the gap is reachable with signals already on disk, at zero API?

Three questions, one data pass:

  (1) DECOMPOSE. Split the "exactly one attempt passed" band by WHY the other
      one failed. budget_exceeded is a resource accident; a normal termination
      with a wrong answer is a capability miss; infra is neither. Different
      bands want different interventions, and one of them is nearly free.

  (2) SELECT. Score deterministic selection rules against the oracle. The
      capture rate is (rule - pass@1) / (pass@2 - pass@1): what fraction of the
      achievable slack a rule actually banks. A rule that cannot beat 0 is
      worse than always taking the first attempt.

  (3) CEILING. How much of the gap is even in principle reachable from
      surface signals -- i.e. in the "exactly one passed" band, how often do the
      two attempts LOOK different at all? Two identical answers where one is
      graded pass and one fail is a grader artefact, not a selectable choice.

Every attempt carries its extracted answer in `reason` ("match: extracted='x'" /
"no_match: extracted='y'"), so the answers are recoverable for FAILED attempts
too -- which is the only reason this is measurable offline.
"""
from __future__ import annotations

import collections
import json
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = pathlib.Path(__file__).resolve().parents[3]
RUNS = ROOT / "recipe/gaia_evolver/runs"

_ANS = re.compile(r"^(match|no_match):\s*extracted='(.*)'$", re.S)

# Surface features that mark an answer as junk without knowing the truth.
_JUNK = re.compile(
    r"</?(html|body|div|span|script|head|table|tr|td)\b"      # raw markup leaked
    r"|^\s*$"                                                  # empty
    r"|\b(unable to|cannot determine|could not find|i don'?t know|no answer)\b"
    r"|^(none|n/?a|null|error|unknown)\.?$",
    re.I,
)


def parse_attempt(a: dict) -> dict | None:
    """One attempt -> the fields a selection rule is allowed to see."""
    m = _ANS.match((a.get("reason") or "").strip())
    if m is None:
        return None
    return {
        "answer": m.group(2).strip(),
        "passed": bool(a.get("passed")),
        "exit": a.get("exit_reason") or "?",
        "steps": a.get("steps"),
        "infra": bool(a.get("infra_failure")),
    }


def load_cells(runs):
    """Yield one dict per (run, round, variant, task) that has >=2 attempts."""
    for run in runs:
        f = RUNS / run / "comparison.json"
        if not f.exists():
            continue
        d = json.loads(f.read_text(encoding="utf-8"))
        for rnd in d.get("rounds") or []:
            for rec in rnd or []:
                atts = [parse_attempt(a) for a in (rec.get("attempts") or [])]
                atts = [a for a in atts if a is not None]
                if len(atts) < 2:
                    continue
                yield {
                    "run": run,
                    "round": rec.get("round"),
                    "variant": rec.get("variant_id"),
                    "task": str(rec.get("task_id")),
                    "level": rec.get("level"),
                    "atts": atts,
                }


# ---------------------------------------------------------------- selection
def rule_first(atts):
    """pass@1 baseline: ship attempt 0. What the system does today."""
    return atts[0]


def rule_prefer_done(atts):
    """Ship the first attempt that terminated normally; else attempt 0.

    Costs nothing -- exit_reason is already recorded. This is the whole rule.
    """
    for a in atts:
        if a["exit"] == "done":
            return a
    return atts[0]


def rule_done_then_clean(atts):
    """prefer_done, then drop answers that are junk on their face."""
    ok = [a for a in atts if a["exit"] == "done" and not _JUNK.search(a["answer"])]
    if ok:
        return ok[0]
    ok = [a for a in atts if a["exit"] == "done"]
    if ok:
        return ok[0]
    ok = [a for a in atts if not _JUNK.search(a["answer"])]
    return ok[0] if ok else atts[0]


def rule_agree_else_done(atts):
    """If two attempts agree on an answer, ship it; else fall back to done."""
    norm = [a["answer"].strip().lower() for a in atts]
    cnt = collections.Counter(n for n in norm if n)
    if cnt:
        top, k = cnt.most_common(1)[0]
        if k >= 2:
            for a in atts:
                if a["answer"].strip().lower() == top:
                    return a
    return rule_prefer_done(atts)


def rule_last(atts):
    """Control: ship the LAST attempt. Same information cost as `first`."""
    return atts[-1]


RULES = [
    ("pass@1  (first attempt)", rule_first),
    ("last attempt  [control]", rule_last),
    ("prefer exit=done", rule_prefer_done),
    ("done + drop junk answer", rule_done_then_clean),
    ("agree>=2 else done", rule_agree_else_done),
]


# ---------------------------------------------------------------- report
def main(runs):
    cells = list(load_cells(runs))
    if not cells:
        print("no cells")
        return
    n = len(cells)

    band = collections.Counter()
    why_other_failed = collections.Counter()
    distinguishable = collections.Counter()
    per_run = collections.defaultdict(lambda: collections.Counter())

    for c in cells:
        atts = c["atts"]
        k = sum(a["passed"] for a in atts)
        b = "all pass" if k == len(atts) else "none pass" if k == 0 else "SPLIT"
        band[b] += 1
        per_run[c["run"]][b] += 1
        if b != "SPLIT":
            continue
        # why did the failing attempt(s) fail?
        for a in atts:
            if not a["passed"]:
                why_other_failed[a["exit"]] += 1
        good = [a for a in atts if a["passed"]]
        bad = [a for a in atts if not a["passed"]]
        same = {a["answer"].strip().lower() for a in good} & {
            a["answer"].strip().lower() for a in bad
        }
        junk_bad = any(_JUNK.search(a["answer"]) for a in bad)
        done_bad = any(a["exit"] == "done" for a in bad)
        distinguishable["identical answer graded both ways" if same
                        else "bad answer is junk on its face" if junk_bad
                        else "bad answer terminated normally (hard)" if done_bad
                        else "bad answer aborted (easy: exit_reason)"] += 1

    print(f"cells (run x round x variant x task, >=2 attempts): {n}")
    print(f"runs: {len(per_run)}   {', '.join(sorted(per_run))}\n")

    print("=" * 74)
    print("1. BANDS -- where the masking gap lives")
    print("=" * 74)
    for k, v in band.most_common():
        print(f"  {k:<12} {v:6d}  {100*v/n:5.1f}%")
    split = band["SPLIT"]
    print(f"\n  the SPLIT band IS the masking gap: {100*split/n:.2f}pp of cells "
          f"where the harness produced a correct answer but might ship a wrong one")

    print("\n" + "=" * 74)
    print("2. DECOMPOSE -- why the losing attempt lost (SPLIT band only)")
    print("=" * 74)
    tw = sum(why_other_failed.values()) or 1
    for k, v in why_other_failed.most_common():
        print(f"  {k:<24} {v:6d}  {100*v/tw:5.1f}%")

    print("\n" + "=" * 74)
    print("3. CEILING -- is the losing attempt even distinguishable?")
    print("=" * 74)
    for k, v in distinguishable.most_common():
        print(f"  {k:<40} {v:6d}  {100*v/split:5.1f}%")
    print("\n  'identical answer graded both ways' is an upper bound on what NO")
    print("  surface rule can fix -- the two attempts look the same to any selector.")

    print("\n" + "=" * 74)
    print("4. SELECT -- deterministic rules vs the oracle")
    print("=" * 74)
    oracle = sum(1 for c in cells if any(a["passed"] for a in c["atts"]))
    base = None
    print(f"  {'rule':<28}{'acc':>9}{'vs pass@1':>12}{'capture':>10}")
    print(f"  {'-'*28}{'-'*9}{'-'*12}{'-'*10}")
    for name, fn in RULES:
        hit = sum(1 for c in cells if fn(c["atts"])["passed"])
        acc = hit / n
        if base is None:
            base = acc
        gap = oracle / n - base
        cap = (acc - base) / gap * 100 if gap > 1e-12 else float("nan")
        print(f"  {name:<28}{100*acc:8.2f}%{100*(acc-base):+11.2f}pp{cap:9.1f}%")
    print(f"  {'oracle (pass@k)':<28}{100*oracle/n:8.2f}%"
          f"{100*(oracle/n-base):+11.2f}pp{100.0:9.1f}%")

    print("\n  capture = share of the achievable slack the rule banks.")
    print("  the `last attempt` control must sit near 0; if it does not, the")
    print("  attempts are not exchangeable and every number above is suspect.")

    print("\n" + "=" * 74)
    print("5. PER RUN -- does the gap replicate?")
    print("=" * 74)
    print(f"  {'run':<14}{'cells':>7}{'split%':>9}{'pass@1':>9}"
          f"{'best rule':>11}{'oracle':>9}")
    for run in sorted(per_run):
        sub = [c for c in cells if c["run"] == run]
        m = len(sub)
        if m < 30:
            continue
        o = sum(1 for c in sub if any(a["passed"] for a in c["atts"])) / m
        p1 = sum(1 for c in sub if rule_first(c["atts"])["passed"]) / m
        bst = max(sum(1 for c in sub if fn(c["atts"])["passed"]) / m
                  for _, fn in RULES)
        print(f"  {run:<14}{m:7d}{100*per_run[run]['SPLIT']/m:8.1f}%"
              f"{100*p1:8.2f}%{100*bst:10.2f}%{100*o:8.2f}%")


if __name__ == "__main__":
    argv = sys.argv[1:]
    if argv:
        main(argv)
    else:
        main([p.name for p in sorted(RUNS.iterdir())
              if (p / "comparison.json").exists()])
