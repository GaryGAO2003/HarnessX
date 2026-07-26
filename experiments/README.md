# Variant-Pool / Routing Lab (private)

## ⚠️ CORRECTION / ERRATA INDEX (2026-07-25)

> The original roadmap below is preserved as historical planning text. It is not the current implementation or experiment status.

| Errata | Superseded passage | Current reading |
|---|---|---|
| README-E01 | **Roadmap P2 = M1** as a future minimal pool | The pool core now has a `(1,1)` fork default, injectable cluster API, `task_tournament` compatibility mode, task/cluster macro retirement, two-phase settlement, and separate settled active-pool scoring. |
| README-E02 | **Roadmap P3 = M2** describing multi-candidate evolution and cluster routing as optional/future | CandidatePipeline/Critic contracts and cluster routing are implemented and tested. Real LLM AEGIS adapters are not yet integrated into a live end-to-end run. |
| README-E03 | Any inference that “built” means the paper's Ensemble result was validated | Existing probes did not produce a valid evaluated forked Ensemble and previously allowed rejected-candidate scores into final reporting. No current experiment validates Ensemble performance; the formal `103 tasks × 15 rounds × 3 seeds` matrix has not run. |
| README-E04 | Roadmap wording that treats “four candidates” and shipping as unambiguous | Paper Algorithm 1 makes `K_t=4` round-global, while the current queue limit is per target/variant; a global target selector/coordinator is still missing. Algorithm first-pass shipping also conflicts with Appendix B.1 bucket-disjoint multi-ship; the latter is not implemented. |
| README-E05 | Any inference that the structured CandidatePipeline implements all of Algorithm 1's selective invocation | The actionability `a_t < α` / empty-landscape short-circuit is not implemented; empty digests/briefs may still reach Evolver. |

Current sources of truth:

- [Experiment errata and corrected probe interpretation](docs/EXPERIMENT-SUMMARY.md)
- [Paper-methodology deviations and acceptance gates](docs/PAPER-METHODOLOGY-DEVIATIONS.md)
- [Current normative SPEC addendum](variant_pool/SPEC.md)
- [Implementation checklist errata](docs/HARNESSX-IMPL-CHECKLIST.md)

Status shorthand: **implemented/tested** = pool mechanics, structural offline contracts, and the current per-variant first-pass queue; **integrated but not live-tested** = GAIA recipe/reporting/provenance wiring, still without a round-global `K_t` coordinator; **not yet run/not yet implemented** = actionability short-circuit, global target selection, Appendix multi-ship arm, real LLM adapters, formal Global-vs-Ensemble, held-out, ablations, and `103 × 15 × 3`.

Naming note: roadmap **P2 = M1**, the `forkprobe_p2` run directory, and the **pass@2** metric are three different things.

Experiment layer on top of upstream HarnessX (MIT, `Darwin-Agent/HarnessX`).
Upstream implements only the single-lineage MetaHarness hill-climb loop
(≈ paper Table 5 "Global" arm); the paper's §4.5 variant-pool / ensemble-routing
mechanism has no public implementation. This lab builds it.

## Remote conventions (IMPORTANT)

- `origin`  = public fork `GaryGAO2003/HarnessX` — upstream tracking only.
  **Never push `exp/*` branches here.**
- `lab`     = private `GaryGAO2003/harnessx-variant-routing` — all experiment
  work lives here. `remote.pushDefault` is set to `lab`.
- `upstream` = `Darwin-Agent/HarnessX` — fetch-only sync.

## Roadmap

| Phase | Content | Gate |
|---|---|---|
| P0 | env + keys + GAIA data + smoke run | per-task cost calibrated |
| P1 = M0 | K Global lineages → offline oracle ceiling (`oracle_ceiling.py`) | headroom ≈ 0 → stop; large → M1 |
| P2 = M1 | minimal online variant pool (W1–W11: pool, router, estimator, scoped eval, per-task fork gate, per-variant journal) | beats best single lineage? |
| P3 = M2 | multi-candidate evolver + cluster routing (optional) | — |

Full work-item table with file:line anchors: `docs/HARNESSX-IMPL-CHECKLIST.md`.

## Layout

- `experiments/docs/` — specs, checklists, analysis notes
- `recipe/gaia_evolver/oracle_ceiling.py` — W0 offline oracle aggregation (M0)
- upstream code is modified in place on `exp/*` branches; keep diffs reviewable
