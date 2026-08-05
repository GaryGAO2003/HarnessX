# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""Sub-edit-level acceptance (idea N-10): the evolution gate accepts or kills a
candidate as ONE atomic unit, yet every candidate in this corpus edits between 2
and 6 surfaces at once. If one surface in a six-surface candidate is bad, the
other five die with it. This script sizes that cost and replays a per-surface
acceptance rule against the recorded Critic decisions.

It is NOT an audit of the system. It is a feasibility-and-opportunity measurement
for a mechanism change: does a per-surface acceptance rule have anything to accept,
and how much would it recover? Every section is framed that way.

THE ATOMICITY, in the code (verified, not asserted):
  ``ChangeManifest.file_changes`` is a list and ``bucket`` is a list, so a
  candidate is intrinsically multi-surface. But ``CriticRejection`` carries only
  ``(candidate_id, reason)``, and ``gate.py::_decide`` (L271) seesaws over whole
  candidates: improved empty -> REJECT, regressed empty -> APPLY, else FORK. Its
  own comment (gate.py:280-283) says the quiet part -- "The improvement is dropped
  with it, because the seesaw forbids applying an edit that regresses an
  ever-solved task." That is atomicity at the TASK level; N-10 is the same knife
  one level down, at the SURFACE level. No smaller acceptance unit exists anywhere
  in the chain.

THE SURFACE-SOURCE PROBLEM, stated once and honoured throughout.
  The Critic writes a ``mutation_surface`` only for candidates it RANKS (verdicts).
  A rejected candidate is discarded with ``{candidate_id, reason}`` only -- verdicts
  and Critic rejections are DISJOINT candidate sets here (exactly 1 of 40 rejections
  also carries a verdict). So the 84 verdict-carriers have a sanctioned surface list
  and the 40 rejected candidates, in general, do NOT. The only per-candidate surface
  artefact that exists for rejected candidates is a **scratch-directory** file --
  ``R<n>/V<k>/pipeline/candidates/<cid>/**/_meta_scratch/changeset.json`` -- which the
  pipeline was NOT contracted to persist as an output (unlike ``pool_state.json`` /
  ``pipeline_audit.json``). Its schema is ``processors_added|config_changed|removed``,
  ``templates_added|removed``, ``tools_added|removed``; it cannot express the
  universal ``config.yaml`` surface. Sections 2/3/6 rest on this derived file, and
  say so out loud. Before relying on it, Section 0 CROSS-VALIDATES it against
  ``mutation_surface`` on the 84 verdict-carriers (where both exist) and refuses to
  proceed on Option B if they disagree beyond the known config gap on more than
  ``XVAL_DISCORD_MAX`` of candidates -- in which case only the 1 sanctioned rejection
  is classifiable and N-10 is reported as NOT MEASURABLE from sanctioned artefacts.

THE config.yaml FINDING (not a nuisance): all 84 verdict candidates touch
  ``config.yaml``. So no two surfaces in this system are ever file-disjoint -- any
  partial acceptance must still write the shared config. Section 2 reports the
  decomposable rate BOTH with and without ``config.yaml``; Section 5 leads its
  separability answer with this fact, ahead of the syntactic proxy.

n is SMALL: 84 verdict candidates, 40 Critic rejections, 10 seesaw FORKs (primary).
These are mechanism demonstrations, not statistical claims. The count is printed in
Section 1 and carried into the verdict. No randomness is used anywhere in this
script (nothing to seed). Read-only, stdlib only, zero API cost.

Usage:  python experiments/analysis/novelty/w_subedit_acceptance.py [run ...]
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import _common as C  # noqa: E402
import i_gate_noise_replay as I  # noqa: E402

RUNS = C.RUNS_ROOT

# --------------------------------------------------------------------------- #
# corpus facts (verified before writing; appear ONLY inside asserts as drift
# tripwires). Every other number in this script is computed at run time.
# --------------------------------------------------------------------------- #
EXP_FILES = 94                                     # pipeline_audit.json across the default corpus
EXP_VERDICTS = 84                                  # candidates carrying a Critic verdict
EXP_SURFACE_DIST = {2: 27, 3: 4, 4: 28, 5: 22, 6: 3}   # mutation_surface size histogram
EXP_REJECTIONS = 40                                # CriticRejection records
FORCED = ("forceprobe2", 1, "C-R1-01")             # synthesized FORCED_GATE fork (real_decision=reject)
EXP_FORKS_PRIMARY = (37, 10, 5)                    # (seesaw, fork, net-dominated) over pool_report runs
EXP_FORKS_WIDER = (50, 13, 6)                       # (seesaw, fork, net-dominated) over any-pool_state runs

XVAL_DISCORD_MAX = 0.25    # >this share of non-config-concordant verdict-carriers -> abandon Option B
REASON_PREVIEW = 160       # chars of each rejection reason printed for human overrule

# malformed / concatenated rejection candidate ids that resolve to no surface
# source at all (named here so they are visible nulls, not silent drops).
KNOWN_NULL_REJECTIONS = {("s1k8b103", 10, "C-R10-0201"),
                         ("s1k8b103", 6, "C-R6-0401"),
                         ("s2k8b50", 12, "C-R12-0401")}


def banner(title: str) -> None:
    print("=" * 74)
    print(title)
    print("=" * 74)


# --------------------------------------------------------------------------- #
# surface -> edit bucket. Defined explicitly here (NOT the p_hat bucket() in
# j_ceiling_headroom.py, which is a different concept). Printed and audited.
# --------------------------------------------------------------------------- #
BUCKET_RULES = [
    ("config",    "surface contains 'config.yaml'"),
    ("processor", "surface contains 'processors/'"),
    ("prompt",    "surface contains 'templates/' or ends .j2/.md or contains 'prompt'"),
    ("tools",     "surface contains 'tools/'"),
]
BUCKET_WORD_RE = {  # generic words a Critic reason uses to name a bucket (not a surface).
    # WORD-BOUNDARY anchored on purpose: a bare substring 'prompt' would match
    # inside the processor class name 'SystemPromptProcessor' and wrongly mark a
    # template surface as implicated, inflating 'global' at the expense of
    # 'decomposable' -- the exact direction that would falsely bury N-10.
    "config": re.compile(r"\bconfig"),
    "processor": re.compile(r"\bprocessors?\b"),
    "prompt": re.compile(r"\b(?:prompts?|templates?)\b"),
    "tools": re.compile(r"\btools?\b"),
}
BUCKET_WORD_DESC = {"config": r"\bconfig", "processor": r"\bprocessors?\b",
                    "prompt": r"\b(prompts?|templates?)\b", "tools": r"\btools?\b"}


def surface_bucket(s: str) -> str | None:
    sl = s.lower()
    if "config.yaml" in sl:
        return "config"
    if "processors/" in sl:
        return "processor"
    if "templates/" in sl or sl.endswith(".j2") or sl.endswith(".md") or "prompt" in sl:
        return "prompt"
    if "tools/" in sl:
        return "tools"
    return None


def surface_tail(s: str) -> str:
    """Last path component: processor class name / template basename / tool name."""
    return re.split(r"[\\/]", s.strip())[-1]


def canon(s: str) -> tuple[str | None, str]:
    """Canonical surface identity for cross-source comparison and recurrence:
    (bucket, lowercased basename). config collapses to ('config','config.yaml')."""
    b = surface_bucket(s)
    if b == "config":
        return (b, "config.yaml")
    return (b, surface_tail(s).lower())


# --------------------------------------------------------------------------- #
# load every pipeline_audit.json in the given runs (verdicts / rejections /
# strategy_concerns), keyed by (run, round, candidate_id)
# --------------------------------------------------------------------------- #
def load_audits(runs):
    verdicts = {}                 # (run,rnd,cid) -> verdict dict
    rejections = []               # ordered [(run,rnd,cid,reason)]
    strat = []                    # [(run,rnd,concern)]
    files = []
    for run in runs:
        root = RUNS / run
        if not root.is_dir():
            continue
        for f in sorted(root.glob("R*/*/pipeline_audit.json")):
            files.append(f)
            d = json.loads(f.read_text(encoding="utf-8"))
            rnd = d.get("round")
            cr = d.get("critic_review") or {}
            for v in (cr.get("verdicts") or []):
                verdicts[(run, rnd, v["candidate_id"])] = v
            for rj in (cr.get("rejections") or []):
                rejections.append((run, rnd, rj["candidate_id"], rj.get("reason") or ""))
            for sc in (cr.get("strategy_concerns") or []):
                strat.append((run, rnd, sc))
    return verdicts, rejections, strat, files


# --------------------------------------------------------------------------- #
# gate fate of any (run, round, candidate_id), via candidate_diagnostics
# --------------------------------------------------------------------------- #
def build_states_cache(runs):
    cache = {}
    for run in runs:
        try:
            cache[run] = C.all_states(run)
        except FileNotFoundError:
            cache[run] = {}
    return cache


def gate_fate(run, rnd, cid, states_cache):
    """One of: SEESAW-APPLY/FORK/REJECT, FORCED_GATE, never-eval:<reason>, no-round."""
    st = states_cache.get(run, {}).get(rnd)
    if not st:
        return "never-eval:no-settled-round"
    info = (st.get("candidate_diagnostics") or {}).get(cid)
    if info is None:
        return "never-eval:no-diagnostic"
    ar = info.get("archive_reason") or ""
    if "FORCED_GATE" in ar:
        return "FORCED_GATE"
    if "improved=" in ar:                     # reached the seesaw
        return "SEESAW-" + (info.get("decision") or "?").upper()
    stage = info.get("failed_stage") or info.get("skipped_reason") or "unknown"
    return "never-eval:" + stage


# --------------------------------------------------------------------------- #
# changeset.json (the SCRATCH fallback surface source for rejected candidates)
# --------------------------------------------------------------------------- #
def load_changeset(run, rnd, cid, cache):
    key = (run, rnd, cid)
    if key in cache:
        return cache[key]
    base = RUNS / run / f"R{rnd}"
    hits = list(base.glob(f"*/pipeline/candidates/{cid}/**/changeset.json")) if base.is_dir() else []
    cs = json.loads(min(hits, key=lambda p: len(p.parts)).read_text(encoding="utf-8")) if hits else None
    cache[key] = cs
    return cs


def changeset_surfaces(cs) -> list[str]:
    """Reconstruct mutation_surface-style strings from a changeset.json. Note there
    is NO config.yaml entry -- the schema cannot express it."""
    out = []
    for k in ("processors_added", "processors_config_changed", "processors_removed"):
        out += [f"processors/{n}" for n in (cs.get(k) or [])]
    for k in ("templates_added", "templates_removed"):
        out += [f"templates/{p}" for p in (cs.get(k) or [])]
    for k in ("tools_added", "tools_removed"):
        out += [f"tools/{n}" for n in (cs.get(k) or [])]
    seen, uniq = set(), []
    for s in out:
        c = canon(s)
        if c not in seen:
            seen.add(c)
            uniq.append(s)
    return uniq


def rejection_surfaces(run, rnd, cid, verdicts, cs_cache):
    """(surfaces, source_tag) for a rejected candidate: sanctioned verdict first,
    scratch changeset.json second, nothing third."""
    v = verdicts.get((run, rnd, cid))
    if v is not None:
        return list(v.get("mutation_surface") or []), "verdict.mutation_surface"
    cs = load_changeset(run, rnd, cid, cs_cache)
    if cs is not None:
        return changeset_surfaces(cs), "_meta_scratch/changeset.json[SCRATCH]"
    return [], "none"


def substantive(surfaces):
    return [s for s in surfaces if surface_bucket(s) != "config"]


# --------------------------------------------------------------------------- #
# reason<->surface mention heuristic (prints its own working; overrule-able)
# --------------------------------------------------------------------------- #
def mention(surface: str, reason_lc: str):
    """(bucket, specific_hit_or_None, bucket_word_hit). A surface is 'mentioned'
    when its specific name (>=5 chars, e.g. a processor class) appears, OR its
    bucket word ('prompt'/'processor'/'tool'/'config') appears."""
    b = surface_bucket(surface)
    tail = surface_tail(surface).lower()
    stem = tail.rsplit(".", 1)[0]
    specific = next((t for t in (tail, stem) if len(t) >= 5 and t in reason_lc), None)
    rx = BUCKET_WORD_RE.get(b)
    bword = bool(rx and rx.search(reason_lc))
    return b, specific, bword


OBJECTION_KEYWORDS = {  # first match wins; printed at run time
    "portability/path": ("portab", "absolute", "windows path", "d:\\", "not portable"),
    "missing-capability/L2-evidence": ("capability evidence", "no capability", "capability_evidence",
                                       "level-2", "level 2", "round-trip", "roundtrip"),
    "scope/overlap": ("overlap", "duplicate", "subset", "superset", "collision", "collid",
                      "redundant", "orthogonal", "coexist", "already present",
                      "already in the config", "superseded", "same processor"),
    "architectural/strategy": ("root cause", "symptom", "over-engineer", "unsound",
                               "wrongly claims", "overclaim", "ambitious", "strategy",
                               "hit_rate", "does not address", "not address", "shipped in",
                               "do not ship", "no orthogonal"),
}


def classify_objection(reason_lc: str) -> str:
    for name, kws in OBJECTION_KEYWORDS.items():
        if any(k in reason_lc for k in kws):
            return name
    return "unclear"


# =========================================================================== #
# (0) CROSS-VALIDATION GATE for the scratch surface source
# =========================================================================== #
def section0(verdicts, cs_cache):
    banner("(0) CROSS-VALIDATION GATE -- is the scratch changeset.json trustworthy?")
    print("  The 40 Critic rejections carry no sanctioned mutation_surface (verdicts and")
    print("  rejections are disjoint here). Sections 2/3/6 therefore fall back to the")
    print("  SCRATCH artefact _meta_scratch/changeset.json. Before trusting it, compare it")
    print("  to the sanctioned mutation_surface on the 84 verdict-carriers, where BOTH exist.")
    print("  Normalisation = edit buckets with config.yaml EXCLUDED (changeset cannot express")
    print(f"  it). Concordant = the two non-config bucket sets are equal. Gate: abandon Option")
    print(f"  B (fall back to the 1 sanctioned rejection only) if discordant share > "
          f"{XVAL_DISCORD_MAX:.0%}.")
    rows, fwd_sum, rev_sum, concord = [], 0.0, 0.0, 0
    for (run, rnd, cid), v in verdicts.items():
        cs = load_changeset(run, rnd, cid, cs_cache)
        if cs is None:
            continue
        M = {surface_bucket(s) for s in v.get("mutation_surface") or []} - {None, "config"}
        K = {surface_bucket(s) for s in changeset_surfaces(cs)} - {None, "config"}
        if not (M or K):
            continue
        inter = M & K
        fwd_sum += len(inter) / len(K) if K else 1.0
        rev_sum += len(inter) / len(M) if M else 1.0
        ok = (M == K)
        concord += ok
        rows.append((run, cid, sorted(M), sorted(K), ok))
    n = len(rows)
    disc = [r for r in rows if not r[4]]
    print(f"\n  verdict-carriers with a changeset.json : {n} of {len(verdicts)}")
    print(f"  mean overlap changeset->mutation       : {fwd_sum / n:.3f}   (share of changeset buckets found in mutation)")
    print(f"  mean overlap mutation->changeset       : {rev_sum / n:.3f}   (share of mutation buckets found in changeset)")
    print(f"  concordant (non-config bucket sets ==) : {concord}/{n}   discordant: {len(disc)}   "
          f"({len(disc) / n:.1%})")
    if disc:
        print("  DISCORDANT candidates (audit these):")
        for run, cid, M, K, _ in disc[:25]:
            print(f"    {run}/{cid}: mutation={M}  changeset={K}")
    else:
        print("  no discordant candidates -- every compared pair agrees once config.yaml is set aside.")
    proceed = (len(disc) / n) <= XVAL_DISCORD_MAX if n else False
    print(f"\n  GATE: discordant {len(disc) / n if n else float('nan'):.1%} "
          f"{'<=' if proceed else '>'} {XVAL_DISCORD_MAX:.0%}  ->  "
          + ("PROCEED with Option B (changeset.json as rejected-candidate surface source)."
             if proceed else
             "ABANDON Option B. Only the 1 verdict-backed rejection is classifiable; N-10 is"
             " NOT MEASURABLE from sanctioned artefacts (reported as such below)."))
    return proceed, n


# =========================================================================== #
# (1) GRANULARITY
# =========================================================================== #
def section1(verdicts, states_cache, assert_corpus):
    banner("(1) GRANULARITY -- the size of the unit an acceptance decision is taken over")
    cands = list(verdicts.items())
    n = len(cands)
    print(f"  verdict-carrying candidates (n)  : {n}   <-- SMALL n; carried to the verdict")
    print("  (these are the candidates the Critic RANKED; each carries a mutation_surface.)")

    # -- bucket mapping, printed and audited ------------------------------- #
    print("\n  surface -> edit-bucket mapping (first rule wins):")
    for name, desc in BUCKET_RULES:
        print(f"    {name:<10} : {desc}")
    unmatched = Counter()
    for (_k, v) in cands:
        for s in (v.get("mutation_surface") or []):
            if surface_bucket(s) is None:
                unmatched[s] += 1
    print("  surfaces matching NO rule (mapping audit): "
          + ("NONE" if not unmatched else json.dumps(dict(unmatched))))

    # -- surface-count distribution ---------------------------------------- #
    sizes = [len(v.get("mutation_surface") or []) for _k, v in cands]
    dist = dict(sorted(Counter(sizes).items()))
    mean = sum(sizes) / n
    print(f"\n  mutation_surface size per candidate:")
    for k in sorted(dist):
        print(f"    {k} surfaces : {dist[k]:>3}  {'#' * dist[k]}")
    print(f"    mean = {mean:.4f}   min = {min(sizes)}   max = {max(sizes)}   "
          f"(every candidate is multi-surface)")
    if assert_corpus:
        assert dist == EXP_SURFACE_DIST, f"surface-size drift: {dist} != {EXP_SURFACE_DIST}"

    # -- distinct buckets per candidate ------------------------------------ #
    bkt_sizes, config_universal = [], 0
    for _k, v in cands:
        bs = {surface_bucket(s) for s in (v.get("mutation_surface") or [])} - {None}
        bkt_sizes.append(len(bs))
        config_universal += ("config" in bs)
    bdist = dict(sorted(Counter(bkt_sizes).items()))
    print(f"\n  distinct edit-buckets per candidate: {bdist}")
    print(f"  candidates touching config.yaml    : {config_universal}/{n}  "
          + ("(UNIVERSAL -- see Section 5)" if config_universal == n else "(NOT universal)"))
    assert config_universal == n, "config.yaml expected universal across verdict-carriers"

    # -- surface count x eventual fate ------------------------------------- #
    print("\n  surface-count x eventual fate (fate from candidate_diagnostics):")
    fates = {}
    grid = defaultdict(Counter)
    for (run, rnd, cid), v in cands:
        f = gate_fate(run, rnd, cid, states_cache)
        # collapse never-eval:* into one column for the cross-tab, keep FORCED apart
        col = f if f.startswith("SEESAW-") or f == "FORCED_GATE" else "never-eval"
        fates[(run, rnd, cid)] = f
        grid[len(v.get("mutation_surface") or [])][col] += 1
    cols = ["SEESAW-APPLY", "SEESAW-FORK", "SEESAW-REJECT", "never-eval", "FORCED_GATE"]
    print(f"    {'surf':>4} " + "".join(f"{c:>15}" for c in cols) + f"{'total':>7}")
    for sz in sorted(grid):
        row = grid[sz]
        print(f"    {sz:>4} " + "".join(f"{row.get(c, 0):>15}" for c in cols)
              + f"{sum(row.values()):>7}")
    fate_tot = Counter(f if f.startswith('SEESAW-') or f == 'FORCED_GATE' else 'never-eval'
                       for f in fates.values())
    print(f"    fate totals: {dict(fate_tot)}")
    print(f"\n  HEADLINE: an acceptance decision in this system is taken over a unit of mean")
    print(f"  size {mean:.2f} surfaces (2-6, never 1); no smaller acceptance unit exists anywhere")
    print("  in the chain -- the Critic rejection, the manifest, and gate.py::_decide are all")
    print("  whole-candidate. That is the atomicity N-10 proposes to break.")
    return n, mean, fates


# =========================================================================== #
# (2) HOW MANY REJECTIONS NAME A PROPER SUBSET
# =========================================================================== #
def section2(rejections, verdicts, cs_cache, proceed):
    banner("(2) DO REJECTION REASONS NAME A PROPER SUBSET OF THE SURFACES?")
    print("  PROVENANCE: for the 40 Critic rejections the surface list comes from the")
    print("  SCRATCH file _meta_scratch/changeset.json (37/40), a sanctioned verdict (1/40),")
    print("  or nowhere (the malformed-id nulls). changeset.json is a derived debug artefact")
    print("  the pipeline was not contracted to persist -- every number below rests on it.")
    print("  A rejection is DECOMPOSABLE when the reason mentions >=1 surface and leaves >=1")
    print("  surface unmentioned. Heuristic over prose: a surface is 'mentioned' if its name")
    print("  (>=5 chars) or its bucket word appears in the reason. The full working is printed")
    print("  so any call can be overruled. config.yaml handled two ways (see totals).")
    print("\n  bucket words (word-boundary anchored, so 'prompt' does NOT match inside")
    print("  'SystemPromptProcessor'): " + "  ".join(f"{b}={p}" for b, p in BUCKET_WORD_DESC.items()))
    print("\n  objection keyword sets (first match wins):")
    for name, kws in OBJECTION_KEYWORDS.items():
        print(f"    {name:<32}: {', '.join(kws)}")

    if not proceed:
        print("\n  ** Option B ABANDONED at the Section 0 gate: reporting sanctioned-only result. **")

    rows = []
    for (run, rnd, cid, reason) in rejections:
        surfaces, source = rejection_surfaces(run, rnd, cid, verdicts, cs_cache)
        if not proceed and not source.startswith("verdict"):
            surfaces, source = [], "none(option-A)"
        reason_lc = reason.lower()
        sub = substantive(surfaces)                      # config excluded (primary)
        # mention test on substantive surfaces
        mset = []
        for s in sub:
            b, spec, bword = mention(s, reason_lc)
            mset.append((s, spec, bword, bool(spec or bword)))
        n_ment = sum(1 for _s, _sp, _bw, hit in mset if hit)
        # WITHOUT config (primary)
        if not sub:
            klass_wo = "no-surface-source" if not surfaces else "config-only"
        elif n_ment == 0:
            klass_wo = "no-surface-matched"
        elif n_ment == len(sub):
            klass_wo = "global"
        else:
            klass_wo = "decomposable"
        # WITH config (sensitivity): config is a known-universal surface, mentioned
        # only if the reason explicitly cites config.
        full = surfaces if source.startswith("verdict") else (surfaces + (["config.yaml"] if surfaces else []))
        config_mentioned = "config" in reason_lc
        n_ment_full = n_ment + (1 if (any(surface_bucket(s) == "config" for s in full) and config_mentioned) else 0)
        n_full = len(full)
        if n_full == 0:
            klass_w = "no-surface-source"
        elif n_ment_full == 0:
            klass_w = "no-surface-matched"
        elif n_ment_full == n_full:
            klass_w = "global"
        else:
            klass_w = "decomposable"
        rows.append(dict(run=run, rnd=rnd, cid=cid, reason=reason, source=source,
                         sub=sub, mset=mset, n_ment=n_ment, klass_wo=klass_wo,
                         klass_w=klass_w, objection=classify_objection(reason_lc),
                         is_null=(run, rnd, cid) in KNOWN_NULL_REJECTIONS))

    # -- per-rejection working table --------------------------------------- #
    print("\n  per-rejection working (surfaces are config-EXCLUDED; * = matched):")
    for r in rows:
        marks = " ".join(("*" if hit else " ") + surface_tail(s)
                         for s, _sp, _bw, hit in r["mset"]) or "(no substantive surface)"
        tag = "  <NULL-ID>" if r["is_null"] else ""
        print(f"    [{r['run']}/{r['cid']} R{r['rnd']}] src={r['source'].split('[')[0]}"
              f" obj={r['objection']} class={r['klass_wo']}{tag}")
        print(f"        surfaces: {marks}")
        print(f"        reason  : {r['reason'][:REASON_PREVIEW].replace(chr(10), ' ')}")

    # -- tallies ----------------------------------------------------------- #
    def tally(key):
        return Counter(r[key] for r in rows)
    two = tally("klass_wo")
    tw = tally("klass_w")
    obj = tally("objection")
    n_total = len(rows)
    classifiable_wo = two.get("global", 0) + two.get("decomposable", 0)
    classifiable_w = tw.get("global", 0) + tw.get("decomposable", 0)
    nulls = [r for r in rows if r["is_null"]]
    print(f"\n  totals over {n_total} rejections:")
    print(f"    objection types           : {dict(obj)}")
    print(f"    malformed-id nulls        : {len(nulls)}  "
          f"{[r['run'] + '/' + r['cid'] for r in nulls]}")
    print(f"    WITHOUT config.yaml (primary): {dict(two)}")
    print(f"      decomposable / classifiable = {two.get('decomposable', 0)}/{classifiable_wo}"
          + (f"  ({two.get('decomposable', 0) / classifiable_wo:.0%})" if classifiable_wo else "")
          + f"   decomposable / all-40 = {two.get('decomposable', 0)}/{n_total}"
          + f"   ({two.get('decomposable', 0) / n_total:.0%})")
    print(f"    WITH config.yaml (sensitivity): {dict(tw)}")
    print(f"      decomposable / classifiable = {tw.get('decomposable', 0)}/{classifiable_w}"
          + (f"  ({tw.get('decomposable', 0) / classifiable_w:.0%})" if classifiable_w else ""))
    print(f"    reasons where NO surface matched (unclassifiable, not folded either way): "
          f"{two.get('no-surface-matched', 0)}")
    print("\n  READ: config.yaml is universal, so counting it as a surface makes almost every")
    print("  multi-surface candidate trivially 'decomposable' (config is nearly never the object")
    print("  of the complaint). The WITHOUT-config number is the honest one; the gap between the")
    print("  two is exactly the container-surface inflation, reported not hidden.")
    return rows, two


# =========================================================================== #
# (3) WHAT ATOMIC REJECTION COSTS
# =========================================================================== #
def section3(rows, verdicts, states_cache, strat):
    banner("(3) WHAT ATOMIC REJECTION COSTS -- do the unimplicated surfaces come back?")
    print("  For each DECOMPOSABLE rejection (config-excluded), the unimplicated surfaces are")
    print("  the ones the reason never faults. Do they reappear in a LATER round of the SAME")
    print("  run (forward-only; never backward, never cross-run)? Matched by canonical surface")
    print("  identity (bucket, basename) -- so a processor class recurs, a per-candidate template")
    print("  path does not. n is tiny; this is illustration, not estimation.")

    # later-round verdict surfaces per run
    later = defaultdict(list)   # run -> [(round, cid, canon-set)]
    for (run, rnd, cid), v in verdicts.items():
        cs = {canon(s) for s in (v.get("mutation_surface") or [])}
        later[run].append((rnd, cid, cs))

    delays, never = [], 0
    decs = [r for r in rows if r["klass_wo"] == "decomposable"]
    print(f"\n  decomposable rejections examined: {len(decs)}")
    for r in decs:
        unimpl = [s for s, _sp, _bw, hit in r["mset"] if not hit]
        for s in unimpl:
            c = canon(s)
            future = sorted((rr, cc) for (rr, cc, cs) in later[r["run"]]
                            if rr > r["rnd"] and c in cs)
            if future:
                fr, fc = future[0]
                fate = gate_fate(r["run"], fr, fc, states_cache)
                delays.append(fr - r["rnd"])
                print(f"    {r['run']}/{r['cid']} R{r['rnd']} unimplicated {surface_tail(s)!r} "
                      f"re-proposed R{fr} by {fc} (+{fr - r['rnd']} rounds) -> {fate}")
            else:
                never += 1
                print(f"    {r['run']}/{r['cid']} R{r['rnd']} unimplicated {surface_tail(s)!r} "
                      f"NEVER re-proposed later in {r['run']}  <-- permanently discarded by atomicity")
    if delays:
        ds = sorted(delays)
        print(f"\n  re-proposal delay (rounds): min={ds[0]} median={ds[len(ds) // 2]} max={ds[-1]}  "
              f"(n={len(ds)})")
    print(f"  unimplicated surfaces NEVER re-proposed in-run: {never}")
    if not decs:
        print("  (no decomposable rejections -> nothing to trace; this is a null, reported as one.)")

    # strategy_concerns noticing churn -- free evidence, printed verbatim
    print("\n  Critic strategy_concerns that themselves flag repeated cross-round attempts")
    print("  (the harness noticing the churn; verbatim, run/round attached):")
    pat = re.compile(r"across rounds|continues to be attempted|repeatedly|persistent|"
                     r"re-?attempt|no sustained", re.I)
    hits = [(run, rnd, sc) for (run, rnd, sc) in strat if pat.search(sc)]
    for run, rnd, sc in hits:
        print(f"    [{run} R{rnd}] {sc[:200].strip()}")
    if not hits:
        print("    (none found)")
    return len(decs), never


# =========================================================================== #
# (4) LINK TO THE NET-DOMINATED FORKS
# =========================================================================== #
def forks_over(run_names, states_cache):
    seesaw, forks = 0, []
    for run in run_names:
        states = states_cache.get(run)
        if states is None:
            try:
                states = C.all_states(run)
            except FileNotFoundError:
                states = {}
            states_cache[run] = states
        for d in I.seesaw_decisions(states):
            info = (states[d["round"]].get("candidate_diagnostics") or {}).get(d["cid"], {})
            if "FORCED_GATE" in (info.get("archive_reason") or ""):
                continue
            seesaw += 1
            if (d["decision"] or "").lower() == "fork":
                forks.append((run, d["round"], d["cid"], len(d["improved"]), len(d["regressed"])))
    return seesaw, forks


def section4(verdicts, states_cache):
    banner("(4) LINK TO THE NET-DOMINATED FORKS -- multi-surface forks that lose on net")
    print("  A net-dominated FORK regresses more tasks than it improves, yet ships (quarantined).")
    print("  Two populations (FORCED_GATE excluded from both):")
    print("    PRIMARY = runs with pool_report.json (completed; the sibling scripts' default)")
    print("    WIDER   = every run with any R*/pool_state.json (adds incomplete runs)")
    corpus = sorted(p.name for p in RUNS.iterdir() if p.is_dir())
    completed = [r for r in corpus if (RUNS / r / "pool_report.json").exists()]
    pool_state = [r for r in corpus if C.round_indices(r)]

    out = {}
    for label, rns, expect in (("PRIMARY", completed, EXP_FORKS_PRIMARY),
                               ("WIDER", pool_state, EXP_FORKS_WIDER)):
        seesaw, forks = forks_over(rns, states_cache)
        nd = sorted((f for f in forks if f[4] > f[3]), key=lambda x: (x[3] - x[4]))
        print(f"\n  {label}: seesaw={seesaw}  FORK={len(forks)}  net-dominated={len(nd)}  "
              f"({len(nd)}/{len(forks)} = {len(nd) / len(forks):.0%} of forks)")
        print(f"    fork run distribution: {dict(Counter(f[0] for f in forks))}")
        print(f"    {'candidate':>22} {'imp':>4} {'reg':>4} {'net':>5} {'surfaces':>9}")
        for (run, rnd, cid, ni, nr) in nd:
            v = verdicts.get((run, rnd, cid))
            ns = len(v.get("mutation_surface") or []) if v else -1
            print(f"    {run + '/' + cid:>22} {ni:>4} {nr:>4} {ni - nr:>5} "
                  f"{(ns if ns >= 0 else 'NO-VERDICT'):>9}")
        assert (seesaw, len(forks), len(nd)) == expect, \
            f"{label} fork drift: {(seesaw, len(forks), len(nd))} != {expect}"
        out[label] = nd

    worst = out["PRIMARY"][0]
    v = verdicts.get((worst[0], worst[1], worst[2]))
    print(f"\n  WORST net-dominated fork (computed): {worst[0]}/{worst[2]}  "
          f"imp={worst[3]} reg={worst[4]}  surfaces={len(v.get('mutation_surface') or []) if v else '?'}")
    print("  NOTE: the spec named C-R7-02 (2 vs 14) as worst; the computed worst is C-R6-01")
    print("  (4 vs 21). Both are real; the qualitative finding survives both populations")
    print(f"  (primary {len(out['PRIMARY'])}/10 = {len(out['PRIMARY']) / 10:.0%}, "
          f"wider {len(out['WIDER'])}/13 = {len(out['WIDER']) / 13:.0%}).")
    print("\n  WHAT THIS CAN AND CANNOT SHOW: a multi-surface net-dominated fork is CONSISTENT")
    print("  WITH one bad surface poisoning the rest -- but the recorded data attributes no task")
    print("  outcome to any individual surface. It is a hypothesis this data cannot settle, only")
    print("  motivate. No causal claim is made.")
    return out


# =========================================================================== #
# (5) WOULD THE PIECES SEPARATE?
# =========================================================================== #
def section5(rows, verdicts):
    banner("(5) WOULD THE PIECES SEPARATE? -- separability of the surfaces")
    n = len(verdicts)
    cfg = sum(1 for v in verdicts.values()
              if any(surface_bucket(s) == "config" for s in (v.get("mutation_surface") or [])))
    print("  PRIMARY EVIDENCE (data, not proxy): config.yaml universality.")
    print(f"    {cfg}/{n} verdict candidates touch config.yaml. So NO two surfaces in this")
    print("    system are ever file-disjoint -- any partial acceptance must still write the")
    print("    shared config file. Two readings the persisted data CANNOT distinguish:")
    print("      (a) COUPLED  : config is how a processor/tool is wired in (registered), so")
    print("          dropping a surface means editing config too -> subsets are not free-standing.")
    print("      (b) SEPARABLE: config is a passive manifest that can list a subset -> a partial")
    print("          edit is coherent as long as config is rewritten to match.")
    print("    Nothing on disk decides between (a) and (b): the ChangeManifest file_changes")
    print("    diffs (the per-hunk config edits) are NOT persisted in these runs. That is the")
    print("    honest limit -- a follow-up would have to persist and read those diffs.")

    print("\n  SECONDARY (syntactic proxy, static): on SUBSTANTIVE surfaces (config excluded),")
    print("  presume separable when surfaces touch different files AND different buckets;")
    print("  presume entangled when they share a file, or the Critic's reasons/overlapping")
    print("  candidates signal coupling (collision/coexist/overlap/interaction/depends).")
    couple = re.compile(r"colli|coexist|overlap|interaction|interact|depend|conflict", re.I)
    sep = ent_file = ent_critic = single = 0
    for r in rows:
        sub = r["sub"]
        if len(sub) < 2:
            single += 1
            continue
        tails = [surface_tail(s).lower() for s in sub]
        shares_file = len(tails) != len(set(tails))
        critic_couples = bool(couple.search(r["reason"]))
        if shares_file:
            ent_file += 1
        elif critic_couples:
            ent_critic += 1
        else:
            sep += 1
    multi = sep + ent_file + ent_critic
    print(f"    rejections with >=2 substantive surfaces: {multi}   (single-surface: {single})")
    print(f"      presumed SEPARABLE (distinct files+buckets, no Critic coupling): {sep}")
    print(f"      presumed ENTANGLED via shared substantive file                : {ent_file}")
    print(f"      presumed ENTANGLED via Critic coupling language               : {ent_critic}")
    print("    WITH config.yaml counted as a file, the 'share a file' rule makes 100% of")
    print("    multi-surface candidates entangled -- which is the Section-5 finding restated:")
    print("    the proxy is dominated by the universal container. This is a static syntactic")
    print("    proxy; real separability needs the manifest's file_changes diffs, absent here.")
    return sep, multi


# =========================================================================== #
# (6) REPLAY AND VERDICT
# =========================================================================== #
def section6(rows, n_cands, mean_surf, primary_nd, sep_share):
    banner("(6) REPLAY A PER-SURFACE ACCEPTANCE RULE -- decisions only, no score")
    print("  Rule: a decomposable rejection (config-excluded) ships its unimplicated,")
    print("  presumed-separable surfaces instead of dying whole. We REPLAY the decision only.")
    print("  PROVENANCE: rests on the SCRATCH changeset.json for the rejected candidates.")
    couple = re.compile(r"colli|coexist|overlap|interaction|interact|depend|conflict", re.I)
    decs = [r for r in rows if r["klass_wo"] == "decomposable"]
    ship_cands, ship_surfaces, withheld = 0, 0, []
    for r in decs:
        unimpl = [s for s, _sp, _bw, hit in r["mset"] if not hit]
        tails = [surface_tail(s).lower() for s in r["sub"]]
        shares_file = len(tails) != len(set(tails))
        if shares_file or couple.search(r["reason"]):
            withheld.append((r, "shared-file" if shares_file else "Critic-coupled"))
            continue                      # not presumed-separable -> do not ship
        if unimpl:
            ship_cands += 1
            ship_surfaces += len(unimpl)
            print(f"    SHIP {r['run']}/{r['cid']} R{r['rnd']}: "
                  f"{[surface_tail(s) for s in unimpl]}  (from a rejected {len(r['sub'])}-surface edit)")
    print(f"\n  of {len(decs)} decomposable rejections, {len(withheld)} are withheld as NOT")
    print("  presumed-separable (Section 5 filter):")
    for r, why in withheld:
        print(f"    withhold {r['run']}/{r['cid']} R{r['rnd']}  ({why})")
    print(f"\n  candidates that would ship a PARTIAL edit: {ship_cands}")
    print(f"  surfaces shipped in total                : {ship_surfaces}")
    print("  DECISIONS ONLY. The partial edits were NEVER run; there is NO score attached to")
    print("  any of this. Anyone quoting these counts as a score or accuracy gain is misquoting")
    print("  them -- the recovered quantity is 'edits not thrown away', not 'tasks solved'.")

    banner("VERDICT (N-10)")
    lines = [
        f"On {n_cands} verdict candidates, 40 Critic rejections, and {len(primary_nd)}/10 net-dominated forks",
        f"(tens, not thousands -- the caveat on every line), the mechanism is real: acceptance is",
        f"atomic over a mean of {mean_surf:.2f} surfaces and no finer unit exists. The OPPORTUNITY is",
        f"{ship_cands} rejected candidates whose unimplicated, presumed-separable surfaces a per-surface",
        "rule would have shipped -- a small, countable number of edits saved from atomic death, with NO",
        "score claim. The EVIDENCE is weak by construction: the rejected candidates carry no sanctioned",
        "surface list, so Sections 2/3/6 rest on a scratch changeset.json (cross-validated in Section 0",
        "but never contracted to persist), and n is tiny. TWO THINGS must be true for the mechanism to",
        "pay off: (1) the surfaces must be genuinely separable -- but config.yaml is universal, so every",
        "partial edit still rewrites the shared config, and the data cannot tell wiring from manifest",
        "(Section 5); (2) the unimplicated surface must actually have been worth keeping -- yet several",
        "are never re-proposed in-run (Section 3), consistent with them being unwanted, not censored.",
        "N-10 is a plausible, cheap mechanism to prototype; it is NOT established as a win by this data.",
    ]
    for ln in lines:
        print("  " + ln)


# --------------------------------------------------------------------------- #
def main(runs, assert_corpus: bool) -> None:
    banner("W. SUB-EDIT-LEVEL ACCEPTANCE (N-10) -- feasibility & opportunity, NOT an audit")
    print(f"  runs scanned (>=1 R*/*/pipeline_audit.json expected): {len(runs)}")
    print("  provenance note: Sections 2/3/6 depend on _meta_scratch/changeset.json, a SCRATCH")
    print("  artefact the pipeline was not contracted to persist (see module docstring).")

    verdicts, rejections, strat, files = load_audits(runs)
    print(f"  pipeline_audit.json files : {len(files)}")
    print(f"  verdicts / rejections     : {len(verdicts)} / {len(rejections)}")
    if assert_corpus:
        assert len(files) == EXP_FILES, f"file-count drift: {len(files)} != {EXP_FILES}"
        assert len(verdicts) == EXP_VERDICTS, f"verdict drift: {len(verdicts)} != {EXP_VERDICTS}"
        assert len(rejections) == EXP_REJECTIONS, f"rejection drift: {len(rejections)} != {EXP_REJECTIONS}"
        fk = FORCED
        assert (fk[0], fk[1], fk[2]) in verdicts, "FORCED_GATE candidate missing from verdicts"
    else:
        print("  (subset run set: corpus-count asserts skipped; Section 4 remains corpus-wide.)")

    states_cache = build_states_cache(runs)
    cs_cache: dict = {}

    print()
    proceed, _ = section0(verdicts, cs_cache)
    print()
    n_cands, mean_surf, _fates = section1(verdicts, states_cache, assert_corpus)
    print()
    rows, _two = section2(rejections, verdicts, cs_cache, proceed)
    print()
    section3(rows, verdicts, states_cache, strat)
    print()
    nd = section4(verdicts, states_cache)
    print()
    sep_share, _multi = section5(rows, verdicts)
    print()
    section6(rows, n_cands, mean_surf, nd["PRIMARY"], sep_share)


if __name__ == "__main__":
    argv = sys.argv[1:]
    default = sorted(p.name for p in RUNS.iterdir()
                     if p.is_dir() and any(p.glob("R*/*/pipeline_audit.json")))
    runs = argv or default
    main(runs, assert_corpus=(not argv))
