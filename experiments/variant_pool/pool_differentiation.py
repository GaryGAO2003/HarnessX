"""Measure whether a variant pool is actually differentiated.

This is the instrument M-27 exists because of. The question it answers is not
"did evolution produce artefacts" -- s1k8b103 produced plenty, 174/131/161-line
evolved templates sitting on disk -- but "did the rollouts actually RUN on
different system prompts". In s1k8b103 the answer was no: three variants ran
642 rollouts on an empty prompt while the accuracy curve climbed and every
gate, seesaw check and acceptance report stayed silent.

The evidence is ``last_sys_prompt_hash``, recorded per session by the harness:
the SHA256 of the system prompt a rollout was actually served. Two hashes worth
knowing by sight:

    e3b0c442...b855   SHA256("") -- an EMPTY system prompt. Never acceptable.
    (any collision)   two "different" variants that are the same variant.

Read-only; never writes to a run directory. ASCII output for GBK consoles.

    python experiments/variant_pool/pool_differentiation.py [run_tag]
"""

from __future__ import annotations

import collections
import glob
import json
import os
import re
import sys

RUNS = os.path.join("recipe", "gaia_evolver", "runs")
EMPTY_SHA = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"

#: session dir basename: ``R<round>-<vid>-<scope>-<task uuid>[-a<n>]``
PAT = re.compile(r"^R(\d+)-(V\d+)-(.+?)-([0-9a-f]{8}-[0-9a-f-]{27})")


def main() -> int:
    tag = sys.argv[1] if len(sys.argv) > 1 else "s2k8b50"
    run = os.path.join(RUNS, tag)

    print("=" * 70)
    print(f"pool differentiation -- {tag}")
    print("=" * 70)
    if not os.path.isdir(run):
        print(f"no run dir: {run}")
        return 1

    round_dirs = sorted((d for d in os.listdir(run) if re.fullmatch(r"R\d+", d)),
                        key=lambda d: int(d[1:]))
    print(f"round dirs present: {len(round_dirs)}  ({', '.join(round_dirs) or '-'})")

    cells: dict[tuple[int, str, str], collections.Counter] = collections.defaultdict(
        collections.Counter
    )
    files = glob.glob(os.path.join(run, "R*", "**", "*_state.json"), recursive=True)
    unparsed = 0
    for path in files:
        match = PAT.match(os.path.basename(os.path.dirname(path)))
        if not match:
            unparsed += 1
            continue
        try:
            with open(path, encoding="utf-8") as handle:
                state = json.load(handle)
        except Exception:  # noqa: BLE001 - a half-written state file is not fatal
            continue
        scope = "active" if match.group(3) == "active" else "candidate"
        cells[(int(match.group(1)), match.group(2), scope)][
            state.get("last_sys_prompt_hash", "?")
        ] += 1

    # "unparsed" is expected, not a warning: it counts round-level pool_state
    # and the Evolver's own meta_workspace sessions, neither of which is a task
    # rollout with a variant identity. Candidate *gate* evaluations DO parse --
    # they are R<n>-<vid>-<candidate_id>-<uuid> -- which matters, because on
    # s1k8b103 the candidate gate is where 256 of the empty-prompt rollouts hid.
    print(f"session state files: {len(files)}  "
          f"(non-rollout state files skipped: {unparsed} -- pool/meta, expected)")
    if not cells:
        print("\nno session state recorded yet -- run is still in its first round")
        return 0

    print("\nACTIVE POOL, by round  (this is what the pool actually is)")
    print(f"{'round':>5}  {'variants':>8}  {'distinct prompts':>16}  detail")
    latest = None
    for rnd in sorted({r for (r, _, s) in cells if s == "active"}):
        vids = sorted({v for (r, v, s) in cells if r == rnd and s == "active"},
                      key=lambda v: int(v[1:]))
        hashes = {}
        for vid in vids:
            counter = cells[(rnd, vid, "active")]
            hashes[vid] = counter.most_common(1)[0][0] if counter else "?"
        detail = " ".join(f"{v}={h[:8]}" for v, h in hashes.items())
        print(f"{rnd:>5}  {len(vids):>8}  {len(set(hashes.values())):>16}  {detail}")
        latest = (rnd, hashes)

    print("\n" + "-" * 70)
    # elements(), not keys: a Counter's keys are the distinct hashes and its
    # counts are the rollouts -- conflating them under-reports the sample.
    all_hashes = [h for c in cells.values() for h in c.elements()]
    empty_hits = sum(1 for h in all_hashes if h == EMPTY_SHA)
    print(f"rollouts sampled          : {len(all_hashes)}")
    print(f"distinct prompts, run-wide: {len(set(all_hashes))}")
    print(f"EMPTY-prompt rollouts     : {empty_hits}"
          f"   {'<-- FIX INCOMPLETE, STOP EVERYTHING' if empty_hits else '(none -- fix holding)'}")

    if latest:
        rnd, hashes = latest
        distinct = len(set(hashes.values()))
        print(f"\nlatest active round       : R{rnd} -- {len(hashes)} variants, "
              f"{distinct} distinct prompts")
        if empty_hits:
            verdict = "RED: empty prompt present -- a delivery path is still unpatched"
        elif distinct >= 4 and rnd >= 6:
            verdict = "PASS: differentiated at depth -- usable for the arms"
        elif distinct >= 2:
            verdict = "PARTIAL: differentiation real but shallow -- resume to deepen"
        else:
            verdict = "THIN: no differentiation yet -- expected only in the first rounds"
        print(f"verdict                   : {verdict}")

    report = os.path.join(run, "pool_report.json")
    if os.path.exists(report):
        with open(report, encoding="utf-8") as handle:
            data = json.load(handle)
        print("\npool_report.json present (run finished)")
        for key in ("final_pass_at_2", "peak_pass_at_2", "peak_round", "drift_from_peak",
                    "infra_failure_rate", "budget_exhaustion_rate"):
            if key in data:
                print(f"  {key:24} = {data[key]}")
        print(f"  {'variant_count_curve':24} = {data.get('variant_count_curve')}")
    else:
        print("\npool_report.json not written yet (run still in flight)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
