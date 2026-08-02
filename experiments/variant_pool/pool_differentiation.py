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


#: tracer/session lines differ per run by construction and say nothing about
#: what the variant *does*, so they are stripped before hashing a config.
_NOISE = re.compile(r"\s*(session_id|base_dir|export_jsonl|silent):")


def _lineage(run: str, round_dirs: list[str]) -> int | None:
    """Report ship/fork decisions from each round's ``pool_state.json``.

    This has to be read separately because the ``R<n>/active_pool`` dirs lag by
    a round: a fork decided in R1 is recorded in R1's pool_state, but the child
    variant is not *measured* until R2. Judging pool size from the active_pool
    directories alone therefore under-reports it by one round -- which is the
    difference between "no differentiation yet" and "already forked".
    """
    print("\nLINEAGE, from each round's pool_state.json")
    print(f"{'round':>5}  {'variants':>8}  {'decision':<22} {'forked':<10} evaluated/ranked")
    latest_count = None
    for rnd in round_dirs:
        path = os.path.join(run, rnd, "pool_state.json")
        if not os.path.exists(path):
            continue
        try:
            with open(path, encoding="utf-8") as handle:
                state = json.load(handle)
        except Exception:  # noqa: BLE001 - a round still settling is not fatal
            continue
        acct = state.get("candidate_accounting") or {}
        decisions = state.get("decisions") or {}
        latest_count = state.get("variant_count", latest_count)
        print(f"{state.get('round'):>5}  {state.get('variant_count'):>8}  "
              f"{json.dumps(decisions, ensure_ascii=False):<22} "
              f"{str(state.get('forked') or []):<10} "
              f"{acct.get('evaluated', '-')}/{acct.get('ranked_for_gate', '-')}"
              f"  (gate_rejected={acct.get('gate_rejected', '-')},"
              f" skipped={acct.get('skipped', '-')})")
    return latest_count


def _config_axis(run: str, round_dirs: list[str]) -> int | None:
    """Report differentiation at the *configuration* level.

    Prompt hash alone is not enough. AEGIS evolves processors and tools as well
    as the template, so two variants can be genuinely different agents while
    serving byte-identical system prompts -- and, as s1k8b103 showed, they can
    also carry three distinct configs while two of them quietly serve no prompt
    at all. Reading both axes together is what separates "differentiated" from
    "differentiated, but the prompt half never arrived".
    """
    import hashlib

    latest = None
    for rnd in reversed(round_dirs):
        if glob.glob(os.path.join(run, rnd, "active_pool", "*", "config.yaml")):
            latest = rnd
            break
    if latest is None:
        print("\nno active-pool config dumps yet")
        return None

    groups: dict[str, list[str]] = collections.defaultdict(list)
    for path in sorted(glob.glob(os.path.join(run, latest, "active_pool", "*", "config.yaml"))):
        vid = os.path.basename(os.path.dirname(path))
        with open(path, encoding="utf-8") as handle:
            body = "\n".join(l for l in handle.read().splitlines() if not _NOISE.match(l))
        groups[hashlib.sha256(body.encode()).hexdigest()[:12]].append(vid)

    print(f"\nCONFIG AXIS, {latest} active pool")
    for digest, vids in groups.items():
        dup = "   <-- SAME AGENT" if len(vids) > 1 else ""
        print(f"  {digest}  {', '.join(vids)}{dup}")
    print(f"  distinct configs: {len(groups)} across {sum(len(v) for v in groups.values())} variants")
    return len(groups)


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

    measured_rnd, measured_prompts = (None, 0)
    if latest:
        measured_rnd, hashes = latest[0], latest[1]
        measured_prompts = len(set(hashes.values()))
        print(f"\nlast MEASURED round       : R{measured_rnd} -- {len(hashes)} variants, "
              f"{measured_prompts} distinct prompts")

    pool_size = _lineage(run, round_dirs)
    n_configs = _config_axis(run, round_dirs)

    # The verdict deliberately keys off the lineage, not the measured round.
    # active_pool dirs lag a round behind the fork decision, so judging by them
    # alone reports "no differentiation yet" on a pool that has already forked.
    print("\n" + "-" * 70)
    if empty_hits:
        verdict = "RED: empty prompt present -- a delivery path is still unpatched"
    elif pool_size and pool_size >= 4 and (measured_prompts >= 2 or (n_configs or 0) >= 2):
        verdict = "PASS: pool has depth and the variants really differ -- usable for the arms"
    elif pool_size and pool_size >= 2:
        verdict = ("PARTIAL: pool has forked but is shallow -- resume to deepen "
                   "(differentiation may be config-level; read both axes above)")
    else:
        verdict = "THIN: no fork yet -- expected only in the first rounds"
    print(f"pool size (lineage)       : {pool_size}")
    print(f"distinct configs          : {n_configs}")
    print(f"distinct prompts          : {measured_prompts}  (measured at R{measured_rnd})")
    print(f"VERDICT                   : {verdict}")

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
