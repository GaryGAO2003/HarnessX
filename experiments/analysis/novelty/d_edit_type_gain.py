# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""Analysis D -- edit type x gain (n = 7 shipped edits; OBSERVATION ONLY).

For each ship round the selected candidate's manifest (`candidates.md`, written
by the meta-agent) lists the files it created. The edit bucket is read from those
concrete file paths (specific enough to avoid prose false-matches):
    tools/<name>.py or @tool           -> tool
    templates/<name>.j2                -> prompt
    processors/<name>.py               -> processor
    config.yaml                        -> config
The plan brief buckets (pipeline_audit.plan.briefs[].buckets) are reported
alongside as the *considered* landscape.

Gain is measured on the tasks the shipped (child) variant actually carries, NOT
the global curve (task requirement). Two views:
  * edit_contrast  : mean(after) - mean(before) on the gate's edit contrast
                     (before = parent active_pool, after = candidate_gate) over
                     shared tasks -- the direct measured edit effect.
  * forward_delta  : mean pass@2 change on the child's R+1 carried tasks from
                     round R (pool incumbent) to R+1 -- the persisted one-step
                     effect after shipping.

n = 7. No significance is claimed or computed; means and extremes only.

Usage:  python experiments/analysis/novelty/d_edit_type_gain.py [run]
"""

from __future__ import annotations

import re
import sys
from collections import defaultdict

import _common as C

# import sibling analysis B helpers for the identical contrast/forward machinery
from b_allk_interception import ship_contrast, next_round_delta

FILE_BUCKET_PATTERNS = [
    (re.compile(r"tools/[\w\-]+\.py"), "tool"),
    (re.compile(r"@tool\b"), "tool"),
    (re.compile(r"templates/[\w\-]+\.j2"), "prompt"),
    (re.compile(r"processors?/[\w\-]+\.py"), "processor"),
    (re.compile(r"\bconfig\.yaml\b"), "config"),
]
# The paper's intervention lever -> HarnessX change bucket. Used as a fallback
# only when a manifest describes its edit semantically (e.g. "CommitNudgeProcessor",
# lever=control) rather than by listing shipped file paths. Documented mapping,
# not a guess: action edits add tools, instruction edits change the prompt,
# control edits add a processor hook, configuration edits touch config.yaml.
LEVER_BUCKET = {"action": "tool", "instruction": "prompt", "control": "processor",
                "config": "config", "configuration": "config"}


def selected_candidate(state: dict) -> tuple[str, str]:
    ptgt = state["paper_target_variant"]
    sel = state.get("selected_candidate_ids", {})
    cid = sel.get(ptgt) or (next(iter(sel.values())) if sel else None)
    return ptgt, cid


def parse_lever(text: str) -> list[str]:
    m = re.search(r"lever:\s*([^|\]\n]+)", text, re.I)
    if not m:
        return []
    return [tok.strip().lower() for tok in re.split(r"[+,]", m.group(1)) if tok.strip()]


def edit_buckets(run: str, r: int, ptgt: str, cid: str) -> tuple[list[str], dict]:
    """(resolved_buckets, evidence).

    resolved = shipped-file buckets when the manifest lists file paths, else the
    lever-mapped buckets. evidence carries the raw lever tokens and file matches
    so every classification is auditable.
    """
    md = C.run_path(run) / f"R{r}" / ptgt / "pipeline" / "candidates" / cid / "_meta_scratch" / "candidates.md"
    file_buckets: set[str] = set()
    matched: list[str] = []
    levers: list[str] = []
    if md.is_file():
        text = md.read_text(encoding="utf-8")
        m = re.search(r"##\s*(Files created|What was shipped)", text, re.I)
        scope = text[m.start():] if m else text
        for pat, tag in FILE_BUCKET_PATTERNS:
            hits = pat.findall(scope)
            if hits:
                file_buckets.add(tag)
                matched.append(f"{tag}:{hits[0]}")
        levers = parse_lever(text)
    lever_buckets = sorted({LEVER_BUCKET[lv] for lv in levers if lv in LEVER_BUCKET})
    resolved = sorted(file_buckets) if file_buckets else lever_buckets
    return resolved, {"levers": levers, "file_matches": matched,
                      "lever_buckets": lever_buckets, "file_buckets": sorted(file_buckets),
                      "source": "shipped_files" if file_buckets else "lever"}


def brief_buckets(run: str, r: int) -> list:
    audit = C.find_pipeline_audit(run, r)
    if audit is None:
        return []
    _, pa = audit
    return [b.get("buckets") for b in pa.get("plan", {}).get("briefs", [])]


def main(run: str = C.DEFAULT_RUN) -> None:
    states = C.all_states(run)
    ships = C.ship_rounds(states)
    print(f"# Analysis D -- edit type x gain  (run={run}; n={len(ships)} ships, OBSERVATION ONLY)\n")

    rows = []
    print("{:>3} {:>5} {:>5} {:>12} {:>22} {:>12} {:>13}".format(
        "R", "tgt", "child", "cid", "edit_buckets(shipped)", "contrast_dpass", "fwd_dpass(R+1)"))
    for r in ships:
        st = states[r]
        ptgt, cid = selected_candidate(st)
        buckets, ev = edit_buckets(run, r, ptgt, cid)
        con = ship_contrast(st)
        before_mean = C.mean(int(con["before"][t][0]) for t in con["shared"])
        after_mean = C.mean(int(con["after"][t][0]) for t in con["shared"])
        contrast_d = (after_mean - before_mean) if (before_mean is not None) else None
        nd = next_round_delta(states, r, con["child"])
        fwd = nd["mean_delta_pass"] if nd else None
        rows.append({
            "round": r, "target": ptgt, "child": con["child"], "cid": cid,
            "buckets": buckets, "evidence": ev,
            "contrast_dpass": contrast_d, "fwd_dpass": fwd,
            "n_shared": con["n_shared"], "fwd_n": nd["n"] if nd else 0,
        })
        cs = f"{contrast_d:+.3f}" if isinstance(contrast_d, float) else str(contrast_d)
        fs = f"{fwd:+.3f}" if isinstance(fwd, float) else str(fwd)
        print("{:>3} {:>5} {:>5} {:>12} {:>22} {:>12} {:>13}".format(
            r, str(ptgt), str(con["child"]), str(cid), ",".join(buckets) or "?", cs, fs))

    print("\nedit-type evidence per ship (resolved = shipped-files if present else lever-map):")
    for row in rows:
        ev = row["evidence"]
        print(f"  R{row['round']:>2} {row['cid']}: resolved={row['buckets']} via {ev['source']}"
              f" | levers={ev['levers']} file_matches={ev['file_matches']}"
              f" | considered_briefs={brief_buckets(run, row['round'])}")

    # bucket distribution (a ship can touch >1 bucket)
    dist = defaultdict(int)
    for row in rows:
        for b in (row["buckets"] or ["?"]):
            dist[b] += 1
    print("\nedit-bucket distribution over shipped edits (multi-bucket edits count in each):")
    print("  ", dict(sorted(dist.items())))

    # per-bucket forward gain mean + extremes
    print("\nper-bucket forward one-step gain (mean pass@2 delta on child's R+1 tasks); n shown:")
    by_bucket = defaultdict(list)
    for row in rows:
        if row["fwd_dpass"] is None:
            continue
        for b in (row["buckets"] or ["?"]):
            by_bucket[b].append((row["round"], row["fwd_dpass"]))
    print("  {:>10} {:>3} {:>9} {:>9} {:>9}   rounds".format("bucket", "n", "mean", "min", "max"))
    for b in sorted(by_bucket):
        vals = [d for _, d in by_bucket[b]]
        rs = [rr for rr, _ in by_bucket[b]]
        print("  {:>10} {:>3} {:>+9.3f} {:>+9.3f} {:>+9.3f}   {}".format(
            b, len(vals), C.mean(vals), min(vals), max(vals), rs))

    print("\nper-bucket direct edit-contrast gain (mean(after)-mean(before)); n shown:")
    by_bucket2 = defaultdict(list)
    for row in rows:
        if row["contrast_dpass"] is None:
            continue
        for b in (row["buckets"] or ["?"]):
            by_bucket2[b].append((row["round"], row["contrast_dpass"]))
    print("  {:>10} {:>3} {:>9} {:>9} {:>9}   rounds".format("bucket", "n", "mean", "min", "max"))
    for b in sorted(by_bucket2):
        vals = [d for _, d in by_bucket2[b]]
        rs = [rr for rr, _ in by_bucket2[b]]
        print("  {:>10} {:>3} {:>+9.3f} {:>+9.3f} {:>+9.3f}   {}".format(
            b, len(vals), C.mean(vals), min(vals), max(vals), rs))

    print(f"\nNOTE: n={len(ships)} (and per-bucket n is smaller). Observation only; "
          f"no significance test is applicable or claimed.")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else C.DEFAULT_RUN)
