# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""Is there room for a decorrelation-aware mechanism to pay on this bed?

A pool only converts into score through some consumption rule. Whole-cluster
routing -- what the system does today -- has near-zero measured headroom. The
other consumption rule is plurality over the pool's answers, and THAT rule has a
specific failure mode a mechanism can attack: the pool is wrong TOGETHER. When
several variants miss, they tend to miss onto the same wrong string, and a wrong
bloc that votes as one beats a correct answer that only two members found.

So the question is not "does the pool disagree" (measured: yes, +9.0pp
cross-variant) but "when plurality loses, does it lose to a CONCENTRATED wrong
bloc?" Only concentrated wrong blocs are attackable by decorrelation. If the
correct answer is simply rare, no amount of spreading the errors saves it -- that
is a capability problem and belongs to a different mechanism.

Four quantities, all on logged rollouts, no API:

  SINGLE      what one attempt gets. The system's current consumption rule.
  PLURALITY   what plurality over the pool's answers gets today.
  DECORR-CEIL plurality already correct, OR the largest correct bloc is >=2.
              Under perfect error decorrelation every wrong bloc shrinks to one
              vote, so a correct bloc of two wins; and a task plurality already
              takes cannot be lost by spreading the errors. Upper bound, not a
              projection.
  ORACLE      >=1 attempt correct. The pool reaches the answer at all.

The gap PLURALITY -> DECORR-CEIL is the decorrelation mechanism's entire budget.
The gap DECORR-CEIL -> ORACLE is unreachable by decorrelation by construction
(a lone correct vote can never win a plurality it does not have) and belongs to
selection-with-a-verifier instead. Reporting them apart keeps the two mechanisms
from being credited with each other's headroom.

Note the ceiling uses the largest single correct BLOC, not the total of correct
votes: two spellings both graded correct do not combine into one candidate, so
they contest the plurality separately.

Tasks are then bucketed so the attackable share is explicit rather than implied.
"""
from __future__ import annotations

import collections
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import n_answer_selection as N  # noqa: E402

_norm = lambda s: re.sub(r"[^a-z0-9 ]", "", s.strip().lower())  # noqa: E731


def build(runs, min_attempts=4):
    """(run, task) -> every attempt, unfiltered.

    Eligibility is by TOTAL attempts, never by clean ones. Selecting tasks on
    "has >=k normally-terminated attempts" silently drops precisely the hard
    tasks -- the ones where most attempts hit the step ceiling -- and inflates
    every base rate. The ballot is filtered later; the electorate is not.
    """
    pool = collections.defaultdict(list)
    for c in N.load_cells(runs):
        for a in c["atts"]:
            pool[(c["run"], c["task"])].append(a)
    return {k: v for k, v in pool.items() if len(v) >= min_attempts}


def analyse(atts):
    """One task. Returns the bucket plus the numbers that justify it.

    Ballot = attempts that terminated normally with a non-empty answer. A
    budget-exceeded attempt passes 2.4% of the time; letting it vote adds noise.
    Tasks whose every attempt aborted keep their place in the denominator and
    simply lose.
    """
    ballot = [a for a in atts if a["exit"] == "done" and _norm(a["answer"])]
    votes = collections.Counter(_norm(a["answer"]) for a in ballot)
    # correctness is judged over ALL attempts -- a right answer found only by an
    # attempt that later ran out of steps still proves the pool can reach it
    correct = {_norm(a["answer"]) for a in atts if a["passed"] and _norm(a["answer"])}
    n_correct_votes = sum(v for k, v in votes.items() if k in correct)
    wrong = collections.Counter({k: v for k, v in votes.items() if k not in correct})

    if votes:
        top, top_n = votes.most_common(1)[0]
        plurality_ok = top in correct
    else:
        top, top_n, plurality_ok = "", 0, False   # nothing terminated: forfeit
    biggest_wrong = wrong.most_common(1)[0][1] if wrong else 0
    wrong_total = sum(wrong.values())

    n_pass_all = sum(a["passed"] for a in atts)
    # largest single correct bloc -- two spellings both graded correct do not
    # combine into one vote, so this is what actually contests the plurality
    max_correct = max((votes[k] for k in correct if k in votes), default=0)
    # under perfect error decorrelation every wrong bloc shrinks to 1, so a
    # correct bloc of >=2 wins. a task plurality already wins cannot be lost.
    ceiling = plurality_ok or max_correct >= 2

    if not correct:
        bucket = "B. no attempt ever correct  (capability)"
    elif n_pass_all == len(atts):
        bucket = "A. every attempt correct    (settled)"
    elif plurality_ok:
        bucket = "C. plurality already correct"
    elif not ballot:
        bucket = "F. solved only by attempts that ran out of steps (BUDGET)"
    elif max_correct >= 2:
        bucket = "D. plurality wrong, correct bloc >=2   (DECORRELATION TARGET)"
    else:
        bucket = "E. plurality wrong, correct bloc <=1   (needs a verifier)"

    return {
        "bucket": bucket,
        "n": len(atts),
        "n_ballot": len(ballot),
        "n_pass_all": n_pass_all,
        "n_correct": n_correct_votes,
        "plurality_ok": plurality_ok,
        "oracle": bool(correct),
        "decorr_ceiling": ceiling,
        "biggest_wrong": biggest_wrong,
        "wrong_total": wrong_total,
        "wrong_distinct": len(wrong),
        "top_n": top_n,
    }


def main(runs, min_attempts=4):
    pool = build(runs, min_attempts)
    if not pool:
        print("no tasks")
        return
    rows = [analyse(v) for v in pool.values()]
    n = len(rows)
    single = sum(r["n_pass_all"] / r["n"] for r in rows) / n

    print(f"(run, task) pairs with >={min_attempts} attempts of ANY kind: {n}")
    print(f"mean attempts per task: {sum(r['n'] for r in rows)/n:.1f}"
          f"   of which on the ballot (terminated normally): "
          f"{sum(r['n_ballot'] for r in rows)/n:.1f}\n")

    print("=" * 72)
    print("CONSUMPTION RULES -- what the same rollouts are worth under each")
    print("=" * 72)
    plur = sum(r["plurality_ok"] for r in rows) / n
    ceil = sum(r["decorr_ceiling"] for r in rows) / n
    orac = sum(r["oracle"] for r in rows) / n
    print(f"  SINGLE      one attempt, as the system does today   {100*single:6.2f}%")
    print(f"  PLURALITY   vote over the pool                      {100*plur:6.2f}%"
          f"   {100*(plur-single):+6.2f}pp")
    print(f"  DECORR-CEIL every wrong vote a different string     {100*ceil:6.2f}%"
          f"   {100*(ceil-single):+6.2f}pp")
    print(f"  ORACLE      any attempt correct                     {100*orac:6.2f}%"
          f"   {100*(orac-single):+6.2f}pp")
    print()
    print(f"  decorrelation mechanism's entire budget   PLURALITY -> CEIL "
          f"= {100*(ceil-plur):+.2f}pp")
    print(f"  out of decorrelation's reach by construction  CEIL -> ORACLE "
          f"= {100*(orac-ceil):+.2f}pp")

    print("\n" + "=" * 72)
    print("WHERE THE TASKS SIT")
    print("=" * 72)
    b = collections.Counter(r["bucket"] for r in rows)
    for k in sorted(b):
        print(f"  {k:<52}{b[k]:5d}  {100*b[k]/n:5.1f}%")

    print("\n" + "=" * 72)
    print("IS THE WRONG BLOC ACTUALLY CONCENTRATED? (only D and E can be attacked)")
    print("=" * 72)
    for tag in ("D.", "E.", "F."):
        sub = [r for r in rows if r["bucket"].startswith(tag)]
        if not sub:
            continue
        share = [r["biggest_wrong"] / r["wrong_total"] for r in sub if r["wrong_total"]]
        distinct = [r["wrong_distinct"] for r in sub]
        lab = {"D.": "D (correct bloc >=2)", "E.": "E (correct bloc <=1)", "F.": "F (no clean ballot)"}[tag]
        print(f"  {lab:<24} n={len(sub):4d}   "
              f"largest wrong bloc = {100*sum(share)/max(len(share),1):5.1f}% of wrong votes"
              f"   distinct wrong answers/task = {sum(distinct)/len(distinct):4.1f}")
    print("\n  a HIGH largest-bloc share means the misses land on one string --")
    print("  that is what decorrelation breaks up. a LOW share means the errors")
    print("  are already scattered and spreading them further buys nothing.")


if __name__ == "__main__":
    argv = sys.argv[1:]
    k = int(argv[0]) if argv and argv[0].isdigit() else 4
    main([p.name for p in sorted(N.RUNS.iterdir())
          if (p / "comparison.json").exists()], min_attempts=k)
