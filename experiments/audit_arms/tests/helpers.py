"""Synthetic-data helpers for the audit_arms test suite (no real runs dir)."""

import json
from pathlib import Path


def make_comparison_json(path, rounds):
    """Write a synthetic comparison.json matching the gaia_evolver schema.

    `rounds` is a list (one entry per round) of lists of record specs; each
    record spec is a dict:
      {round, variant, task, level, attempts: [(passed_bool, infra_bool), ...]}
    """
    out_rounds = []
    for round_recs in rounds:
        recs = []
        for spec in round_recs:
            attempts = [
                {"attempt": i, "passed": bool(p), "score": 1.0 if p else 0.0,
                 "steps": 10, "exit_reason": "done" if p else "budget_exceeded",
                 "reason": ("match: extracted='x'" if p else "no_match: extracted=''"),
                 "infra_failure": bool(inf)}
                for i, (p, inf) in enumerate(spec["attempts"])
            ]
            n_pass = sum(1 for a in attempts if a["passed"] and not a["infra_failure"])
            recs.append({
                "task_id": spec["task"],
                "variant_id": spec["variant"],
                "round": spec["round"],
                "level": spec.get("level", 1),
                "passed": n_pass > 0,
                "n_pass": n_pass,
                "n_att": len(attempts),
                "primary_attempt": 0,
                "exit_reason": attempts[0]["exit_reason"] if attempts else "done",
                "infra_failures": sum(1 for a in attempts if a["infra_failure"]),
                "attempts": attempts,
            })
        out_rounds.append(recs)
    doc = {"rounds": out_rounds, "round_summaries": [], "run_config": {}}
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc), encoding="utf-8")
    return str(path)
