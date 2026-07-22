# Variant-Pool / Routing Lab (private)

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
