# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""Paired readout for a two-arm AEGIS campaign (L0 vendored vs L{n} GHX).

The readout the thesis quotes.  Everything here is derived from artefacts on
disk — round curves, per-round ``config.yaml``, decision files, and the run
console logs.  No API calls, no re-scoring.

Three things it exists to get right:

* **Behavioural round groups.**  ``config_hash`` in ``curves.json`` embeds
  absolute paths, so it differs across checkouts and across rounds even when the
  processor stack is identical.  :func:`normalised_config` strips the two
  bookkeeping lines (the per-round tracer ``base_dir``, the checkout root) and
  hashes what is left, which is what actually decides behaviour.  Rounds that
  share a normalised hash are repeat draws of one configuration.

* **The measured noise envelope.**  Every same-config group with more than one
  scored round is a replicate.  The largest observed spread across all groups is
  the envelope any peak-minus-final claim must clear; it is *measured here*, not
  assumed, and it is reported even when it is inconvenient.

* **The pre-registered endpoint.**  peak - final per arm, stated together with
  the envelope and with whether the peak round carried a new ship.  A peak that
  lands inside a same-config group is flagged, because such a peak is a draw
  from the noise distribution rather than an adoption effect.

Usage::

    python -m experiments.analysis.campaign_readout \
        --arm L0=D:/.../runs/L0_103x10 --arm L2=D:/.../runs/L2_103x10 \
        --log L0=D:/.../L0_103x10.err.log --log L2=D:/.../runs_L2.console.log \
        --json out/campaign_readout.json
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

# Lines whose content is per-round or per-checkout bookkeeping rather than
# behaviour.  Neutralised before hashing so two rounds running the same
# processor stack hash the same.
_BOOKKEEPING_KEY = "base_dir:"
_CHECKOUT_ALIASES = ("HarnessX-baseline", "HarnessX")

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
_VERDICT_REJECT_RE = re.compile(r"Verdict (\S+) failed validation: (.+)")
_DIGEST_REJECT_RE = re.compile(r"Digest (\S+) has broken anchors: ([^:]+)")
_ROUND_BANNER_RE = re.compile(r"ROUND (\d+)/(\d+)\s+config=(\w+)\s+evolve_status=(\w+)")
_EARLY_STOP_RE = re.compile(r"EARLY STOP")
_RESUME_RE = re.compile(r"resume: loading current_config")


# ---------------------------------------------------------------------------
# config identity
# ---------------------------------------------------------------------------


def normalised_config(path: Path) -> str:
    """Config text with per-round / per-checkout bookkeeping neutralised."""
    out = []
    for line in io.open(path, encoding="utf-8", errors="replace"):
        if _BOOKKEEPING_KEY in line:
            line = "  base_dir: <NORM>\n"
        line = line.replace(_CHECKOUT_ALIASES[0], _CHECKOUT_ALIASES[1])
        out.append(line)
    return "".join(out)


def config_group(path: Path) -> str:
    return hashlib.sha256(normalised_config(path).encode()).hexdigest()[:10]


# ---------------------------------------------------------------------------
# per-arm assembly
# ---------------------------------------------------------------------------


def ship_ranking(round_dir: Path) -> list[str]:
    """Candidate ids this round's decision actually shipped (empty for no_op)."""
    dec = round_dir / "decision.md"
    if not dec.is_file():
        return []
    text = io.open(dec, encoding="utf-8", errors="replace").read()
    head = text.split("---")[1] if text.startswith("---") else text
    if re.search(r"decision_type:\s*no_op", head):
        return []
    block = re.search(r"ship_ranking:\s*\n((?:\s*-\s*candidate_id:.*\n?)+)", head)
    return re.findall(r"candidate_id:\s*(\S+)", block.group(1)) if block else []


def read_arm(run_dir: Path) -> dict:
    """Round table for one arm: score, evolve status, config group, ships."""
    curves = {r["round"]: r for r in json.load(io.open(run_dir / "curves.json", encoding="utf-8"))}
    rounds = []
    for n in sorted(int(p.name[1:]) for p in run_dir.glob("R*") if p.name[1:].isdigit()):
        cfg = run_dir / f"R{n}" / "config.yaml"
        if not cfg.is_file():
            continue
        c = curves.get(n)
        rounds.append(
            {
                "round": n,
                "passed": c["passed"] if c else None,
                "total": c["total_tasks"] if c else None,
                "pass_rate": c["pass_rate"] if c else None,
                "evolve_status": c["evolve_status"] if c else None,
                "cost_usd": c["cost_usd"] if c else None,
                "tokens": c["total_tokens"] if c else None,
                "config_group": config_group(cfg),
                # Candidates the meta phase INTENDED to put into this round's
                # config.  They only take effect when the apply step succeeded;
                # a noop/crashed evolve leaves the previous config in force and
                # the ranking is then a record of intent, not of what ran.  The
                # config group is the arbiter — see ``ships_landed`` below.
                "ships": ship_ranking(run_dir / f"R{n}"),
                "scored": c is not None,
            }
        )
    for prev, cur in zip(rounds, rounds[1:]):
        cur["ships_landed"] = bool(cur["ships"]) and cur["config_group"] != prev["config_group"]
    if rounds:
        rounds[0]["ships_landed"] = False
    return {"run_dir": str(run_dir), "rounds": rounds}


def replicate_groups(rounds: list[dict]) -> list[dict]:
    """Same-config groups with >1 scored round — the replicate measurements."""
    by_group: dict[str, list[dict]] = defaultdict(list)
    for r in rounds:
        if r["scored"]:
            by_group[r["config_group"]].append(r)
    groups = []
    for g, rs in by_group.items():
        if len(rs) < 2:
            continue
        passed = [r["passed"] for r in rs]
        groups.append(
            {
                "config_group": g,
                "rounds": [r["round"] for r in rs],
                "passed": passed,
                "spread_tasks": max(passed) - min(passed),
                "spread_pp": round((max(passed) - min(passed)) / rs[0]["total"] * 100, 1),
            }
        )
    return sorted(groups, key=lambda x: -x["spread_tasks"])


def pooled_noise(all_groups: list[dict], total_tasks: int) -> dict:
    """Within-group pooled SD of the per-round score, in tasks.

    Every replicate group is a set of repeat measurements of one configuration,
    so the deviation of its members from the group mean is pure round-to-round
    noise.  Pooling across groups gives a single-round SD; the difference of two
    independent rounds carries ``sqrt(2)`` times that.  Reported with its degrees
    of freedom because with a handful of groups the estimate is itself loose.
    """
    ss = 0.0
    df = 0
    for g in all_groups:
        vals = g["passed"]
        mean = sum(vals) / len(vals)
        ss += sum((v - mean) ** 2 for v in vals)
        df += len(vals) - 1
    if df == 0:
        return {}
    var = ss / df
    sd = var**0.5
    diff_sd = sd * 2**0.5
    return {
        "single_round_sd_tasks": round(sd, 2),
        "single_round_sd_pp": round(sd / total_tasks * 100, 2),
        "degrees_of_freedom": df,
        "round_difference_sd_tasks": round(diff_sd, 2),
        # Two-sided alpha=0.05, ~80% power for a two-round comparison: the
        # smallest arm difference this design could reliably call.
        "min_detectable_difference_tasks": round(2.8 * diff_sd, 1),
        "min_detectable_difference_pp": round(2.8 * diff_sd / total_tasks * 100, 1),
    }


def endpoint(rounds: list[dict], groups: list[dict]) -> dict:
    """peak - final, plus whether the peak is a draw inside a replicate group."""
    scored = [r for r in rounds if r["scored"]]
    if not scored:
        return {}
    peak = max(scored, key=lambda r: r["passed"])
    final = scored[-1]
    first = scored[0]
    in_group = next((g for g in groups if peak["round"] in g["rounds"]), None)
    out = {
        "peak_round": peak["round"],
        "peak_passed": peak["passed"],
        "final_round": final["round"],
        "final_passed": final["passed"],
        "drop_tasks": peak["passed"] - final["passed"],
        "drop_pp": round((peak["passed"] - final["passed"]) / peak["total"] * 100, 1),
        "peak_carried_new_ship": bool(peak["ships"]),
        "peak_inside_replicate_group": None if in_group is None else in_group["rounds"],
        "net_tasks_vs_first": final["passed"] - first["passed"],
        "net_pp_vs_first": round((final["passed"] - first["passed"]) / first["total"] * 100, 1),
        "rounds_complete": len(scored),
    }
    # A peak that is the max of k repeat draws of one configuration is biased
    # upward by construction -- the more times a configuration is measured, the
    # higher its best round. When that is the case the group's mean is the
    # unbiased estimate of what the configuration scores, and the drop measured
    # against it is the one worth quoting.
    if in_group is not None and len(in_group["passed"]) > 1:
        mean = sum(in_group["passed"]) / len(in_group["passed"])
        out["peak_group_mean"] = round(mean, 2)
        out["peak_is_max_of_k_draws"] = len(in_group["passed"])
        out["drop_vs_group_mean_tasks"] = round(mean - final["passed"], 2)
        out["drop_vs_group_mean_pp"] = round((mean - final["passed"]) / peak["total"] * 100, 1)
    return out


# ---------------------------------------------------------------------------
# log-derived counters
# ---------------------------------------------------------------------------


def scan_log(path: Path) -> dict:
    """Anchor-rejection tallies, round banners, resumes and early stops."""
    verdicts_lost: list[dict] = []
    digest_reasons: Counter = Counter()
    banners: list[dict] = []
    early_stops = resumes = 0
    with io.open(path, "rb") as fh:
        for raw in fh:
            line = _ANSI_RE.sub("", raw.replace(b"\x00", b"").decode("utf-8", "replace")).rstrip("\n")
            if (m := _VERDICT_REJECT_RE.search(line)) is not None:
                verdicts_lost.append({"verdict": m.group(1), "reason": m.group(2)[:400]})
            elif (m := _DIGEST_REJECT_RE.search(line)) is not None:
                digest_reasons[m.group(2).strip()] += 1
            elif (m := _ROUND_BANNER_RE.search(line)) is not None:
                banners.append(
                    {"round": int(m.group(1)), "config_hash": m.group(3), "evolve_status": m.group(4)}
                )
            elif _EARLY_STOP_RE.search(line):
                early_stops += 1
            elif _RESUME_RE.search(line):
                resumes += 1
    return {
        "verdicts_lost_to_anchor_format": len(verdicts_lost),
        "verdicts_lost": verdicts_lost,
        "digest_anchor_rejections": dict(digest_reasons),
        "digest_anchor_rejections_total": sum(digest_reasons.values()),
        "round_banners": banners,
        "early_stops": early_stops,
        "operator_resumes": resumes,
    }


# ---------------------------------------------------------------------------
# GHX evidence audit
# ---------------------------------------------------------------------------


def cone_audit(run_dir: Path) -> dict:
    """Are the injected cones carrying signal, or are they degenerate?

    A cone whose invocations all sit at one step cannot point the reader at any
    earlier step, so it carries no diagnostic signal regardless of how many
    nodes it names.  Counting those is the honest check on the L2 payload.
    """
    per_round = {}
    degenerate = total = 0
    for cones_dir in sorted(run_dir.glob("R*/graph_evidence/cones")):
        rnd = int(cones_dir.parent.parent.name[1:])
        n = deg = 0
        for f in cones_dir.glob("*.md"):
            text = io.open(f, encoding="utf-8", errors="replace").read()
            steps = set(re.findall(r"\[step (\d+)\]", text))
            n += 1
            if len(steps) <= 1:
                deg += 1
        if n:
            per_round[rnd] = {"cones": n, "single_step": deg}
            total += n
            degenerate += deg
    injections = 0
    for inj in run_dir.glob("R*/graph_evidence/injections.json"):
        try:
            injections += len(json.load(io.open(inj, encoding="utf-8")))
        except (ValueError, OSError):
            pass
    return {
        "cones_total": total,
        "cones_single_step": degenerate,
        "degenerate_fraction": round(degenerate / total, 3) if total else None,
        "injections_logged": injections,
        "per_round": per_round,
    }


# ---------------------------------------------------------------------------
# report
# ---------------------------------------------------------------------------


def build(arms: dict[str, Path], logs: dict[str, Path]) -> dict:
    report: dict = {"arms": {}, "envelope": {}}
    all_spreads = []
    for name, run_dir in arms.items():
        arm = read_arm(run_dir)
        arm["replicate_groups"] = replicate_groups(arm["rounds"])
        arm["endpoint"] = endpoint(arm["rounds"], arm["replicate_groups"])
        arm["cost_usd"] = round(sum(r["cost_usd"] or 0 for r in arm["rounds"]), 2)
        arm["tokens"] = sum(r["tokens"] or 0 for r in arm["rounds"])
        arm["cone_audit"] = cone_audit(run_dir)
        if name in logs:
            arm["log"] = scan_log(logs[name])
        all_spreads += [(name, g) for g in arm["replicate_groups"]]
        report["arms"][name] = arm
    if all_spreads:
        worst_name, worst = max(all_spreads, key=lambda x: x[1]["spread_tasks"])
        total_tasks = next(
            (r["total"] for a in report["arms"].values() for r in a["rounds"] if r["total"]), 103
        )
        report["envelope"] = {
            "max_same_config_spread_tasks": worst["spread_tasks"],
            "max_same_config_spread_pp": worst["spread_pp"],
            "measured_on": {"arm": worst_name, "rounds": worst["rounds"], "passed": worst["passed"]},
            "replicate_group_count": len(all_spreads),
            **pooled_noise([g for _, g in all_spreads], total_tasks),
            "note": (
                "peak-minus-final below this envelope is not evidence of degradation; "
                "the envelope is measured from behaviourally identical rounds in this "
                "campaign, not assumed from a prior run."
            ),
        }
    return report


def render(report: dict) -> str:
    out: list[str] = []
    w = out.append
    for name, arm in report["arms"].items():
        w(f"== {name}  ({arm['run_dir']})")
        w(f"{'rnd':<5}{'passed':<9}{'rate':<8}{'evolve':<10}{'cfg':<12}ships")
        for r in arm["rounds"]:
            rate = f"{r['pass_rate']*100:.1f}%" if r["pass_rate"] is not None else "-"
            passed = f"{r['passed']}/{r['total']}" if r["scored"] else "(running)"
            if not r["ships"]:
                ships = "-"
            elif r.get("ships_landed"):
                ships = ",".join(r["ships"])
            else:
                ships = f"(intended, did not land: {','.join(r['ships'])})"
            w(
                f"R{r['round']:<4}{passed:<9}{rate:<8}{str(r['evolve_status'] or '-'):<10}"
                f"{r['config_group']:<12}{ships}"
            )
        w("  replicate groups (behaviourally identical rounds):")
        for g in arm["replicate_groups"]:
            w(
                f"    R{'/R'.join(map(str, g['rounds']))}: passed={g['passed']} "
                f"spread={g['spread_tasks']} tasks = {g['spread_pp']}pp"
            )
        e = arm["endpoint"]
        if e:
            w(
                f"  endpoint: peak R{e['peak_round']}={e['peak_passed']} "
                f"final R{e['final_round']}={e['final_passed']} "
                f"drop={e['drop_tasks']} tasks ({e['drop_pp']}pp) "
                f"peak_new_ship={e['peak_carried_new_ship']} "
                f"peak_in_replicate_group={e['peak_inside_replicate_group']}"
            )
            if "peak_group_mean" in e:
                w(
                    f"            peak is max of {e['peak_is_max_of_k_draws']} draws of one config "
                    f"(mean {e['peak_group_mean']}) -> debiased drop = "
                    f"{e['drop_vs_group_mean_tasks']} tasks ({e['drop_vs_group_mean_pp']}pp)"
                )
            w(
                f"            net vs first round: {e['net_tasks_vs_first']:+d} tasks "
                f"({e['net_pp_vs_first']:+}pp)"
            )
        c = arm["cone_audit"]
        if c["cones_total"]:
            w(
                f"  cones: {c['cones_single_step']}/{c['cones_total']} single-step "
                f"(degenerate={c['degenerate_fraction']}), injections={c['injections_logged']}"
            )
        if "log" in arm:
            lg = arm["log"]
            w(
                f"  log: verdicts lost to anchor format={lg['verdicts_lost_to_anchor_format']}, "
                f"digest anchor rejections={lg['digest_anchor_rejections_total']}, "
                f"early stops={lg['early_stops']}, operator resumes={lg['operator_resumes']}"
            )
        w(f"  cost=${arm['cost_usd']}  tokens={arm['tokens']:,}")
        w("")
    env = report.get("envelope")
    if env:
        w(
            f"== measured same-config envelope: {env['max_same_config_spread_tasks']} tasks "
            f"({env['max_same_config_spread_pp']}pp) on {env['measured_on']['arm']} "
            f"R{'/R'.join(map(str, env['measured_on']['rounds']))} "
            f"across {env['replicate_group_count']} replicate groups"
        )
        if "single_round_sd_tasks" in env:
            w(
                f"   pooled single-round SD = {env['single_round_sd_tasks']} tasks "
                f"({env['single_round_sd_pp']}pp, df={env['degrees_of_freedom']}); "
                f"two-round difference SD = {env['round_difference_sd_tasks']} tasks"
            )
            w(
                f"   smallest arm difference this design could call: "
                f"{env['min_detectable_difference_tasks']} tasks "
                f"({env['min_detectable_difference_pp']}pp)"
            )
        w(f"   {env['note']}")
    return "\n".join(out)


def _kv(pairs: list[str]) -> dict[str, Path]:
    out = {}
    for p in pairs or []:
        name, _, path = p.partition("=")
        if not path:
            raise SystemExit(f"expected NAME=PATH, got {p!r}")
        out[name] = Path(path)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--arm", action="append", required=True, help="NAME=RUN_DIR (repeatable)")
    ap.add_argument("--log", action="append", help="NAME=LOGFILE (repeatable)")
    ap.add_argument("--json", help="write the full report here")
    args = ap.parse_args()

    report = build(_kv(args.arm), _kv(args.log))
    print(render(report))
    if args.json:
        p = Path(args.json)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"\nwrote {p}")


if __name__ == "__main__":
    main()
