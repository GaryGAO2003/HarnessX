# ARMS-LEDGER (append-only)

Local record-of-four-surfaces substitute for the audit-line T1 arms (idea ⑧
racing gate, idea ⑩ cold-start shrinkage). Canonical RUN-LOG lives on s4; this
branch (`fix/audit-ideas-7-12`) records only its own audit_arms decisions and
sim runs here. Newest entries appended at the bottom.

---

## Entry 1 -- Implementation (2026-08-05/06, spec ARMS-SPEC v0.1)

Scope: new files under `experiments/audit_arms/` only. Zero import of
`experiments.variant_pool` or any s4 analysis script; stdlib only. Data read
strictly read-only from `D:/PycharmProj/HarnessX/recipe/gaia_evolver/runs`.

Files created:
- `racing_gate.py` -- SPRT (sequential McNemar on discordant pairs) + Hoeffding race. Pure.
- `cold_start.py` -- beta-binomial `shrink` primitive + two-level chain `estimate` + `from_cells`. Pure.
- `replay_data.py` -- independent comparison.json loader + `manifest` (sha1[:8]).
- `sim_racing_offline.py` -- Sim A CLI.
- `sim_cold_start_offline.py` -- Sim B CLI.
- `tests/` -- pytest suite (synthetic fixtures only) + stdlib fallback runner `run_all.py`.
- `__init__.py` (pkg) ; modules also import by bare name via sys.path self-insertion.

Pre-registered constants (hardcoded as defaults, per spec §5):
- `alpha=0.05`, `beta=0.20`, `p0=0.5`.
- SPRT H1 grid `p1 in {0.55, 0.60, 0.75}`; implied uplift theta = d_hat*(2*p1-1).
- Wald boundaries `A=ln((1-beta)/alpha)=ln 16`, `B=ln(beta/(1-alpha))=ln(0.2/0.95)`.
- Shrinkage pseudo-count grid `m in {1, 2, 4, 8}`.
- Bootstrap `B=2000`, cluster = `(run, task)`, `seed=20260805`.
- `drop_infra=True` default (attempt-level `infra_failure` attempts removed; a
  cell with no surviving attempt is dropped).
- Empty-cell baselines: raw MLE with 0.5 and 0.7 conventions; 0.7 hardcoded to
  align with the official router `_UNKNOWN_BOOST` (value copied, NOT imported).

### Deviation D1 (pairing scheme, Sim A) -- REASON RECORDED
Spec §Sim A says "variant pairs within the same (run, round, task) ... real
per-task paired results streamed in round order". Empirically the evolver router
PARTITIONS each round's task set across active variants: verified that
`shared_tasks == 0` for every round of every run (no two variants co-measure the
same task in the same round), so the literal same-round reading yields exactly
ZERO pairs and an empty sim. The operative phrases "real per-task paired results"
and "stream in round order" only cohere across rounds. Resolution used: match
base vs candidate on shared `task_id` ACROSS rounds (each variant's EARLIEST-round
result per task), stream by the candidate's round. This is the reading that both
yields data and honours the per-task + round-order wording. All 56 ordered variant
pairs (8x7, per "全对/all pairs") are replayed, not just V0-as-base.

### Clarification C1 (n=1 flip metric, Sim B)
Spec: "n=1 overcorrection-flip = cells whose estimate crosses the 0.5 routing
threshold after a single observation (F2 style)". Operationalised as: at
prediction points whose (variant, cluster) history holds EXACTLY one observation,
count a flip when the routing side `(estimate >= 0.5)` differs between the
estimator's EMPTY-prior state (no cell obs) and its ONE-observation state. Own
per-estimator prior reference (MLE prior = its 0.5/0.7 empty convention; shrink
prior = the variant rollup). Verified on synthetic data (test: 20 MLE flips vs 0
for shrink@8).

### Choice C2 (Sim B auto-discovery)
Spec pins `--min-variants 8` (=> K=8) for Sim A only. Sim B defaults to the same
`--min-variants 8` for pre-registration consistency (both sims discover the same
run set). Overridable via `--runs` / `--min-variants`. s1k8b103 has 16 rounds,
ample for leave-future-out.

### Verification
- `python -m pytest tests/ -q` => 32 passed.
- Fallback `python -m tests.run_all` => PASSED 32 FAILED 0.

---

## Entry 2 -- Sim A run (racing gate, 2026-08-05)

Command: `python sim_racing_offline.py` (defaults).
Config: runs-dir=`D:/PycharmProj/HarnessX/recipe/gaia_evolver/runs`,
min_variants=8, alpha=0.05, beta=0.20, p1={0.55,0.60,0.75}, B=2000,
seed=20260805, drop_infra=True.

Auto-discovered / used runs: **s1k8b103** (only run with >=8 distinct variants;
34 runs skipped as variants<8). Data manifest (sha1[:8]):
`s1k8b103/comparison.json = f548ade3`.

Scale: 56 ordered variant pairs; 2492 task-matched pairs streamed; overall
discordant rate 0.2705; A/A null = 1492 same-variant attempt0-vs-attempt1 pairs
across 103 (run,task) clusters.

Headline (true-effect SPRT, ACCEPT / REJECT / CONTINUE over 56 pairs):
- p1=0.55 (implied 2.71pp): 0 / 0 / 56  -- too weak to accumulate evidence.
- p1=0.60 (implied 5.41pp): 0 / 12 / 44 -- 12 REJECTs, all in the <=-5pp bin.
- p1=0.75 (implied 13.52pp): 15 / 28 / 13 -- 13 of 15 ACCEPTs in the >10pp bin.
- (1,3]pp observed-uplift bin: **0 ACCEPT at every p1** => gate manufactures no
  power at small effects; honest-check headline confirmed.

A/A false-ACCEPT (cluster bootstrap, must be ~<=alpha):
- p1=0.55: 0.034 (CI [0.026,0.042]) -- within alpha.
- p1=0.60: 0.047 (CI [0.038,0.056]) -- within alpha.
- p1=0.75: 0.0865 (CI [0.074,0.099]) -- ABOVE alpha (finding, not deviation:
  aggressive p1 overshoots the nominal Wald type-I bound via discrete LLR steps).

Budget reallocation (pairs saved vs fixed-N=2492, all from early REJECT):
- p1=0.55: 0 saved (0.0%). p1=0.60: 164 saved (6.6%). p1=0.75: 1003 saved (40.3%).

Outputs: `out/racing_offline_report.md`, `out/racing_offline_report.json`.

---

## Entry 3 -- Sim B run (cold-start shrinkage, 2026-08-05)

Command: `python sim_cold_start_offline.py` (defaults).
Config: min_variants=8, cluster-key=both {task, level}, m={1,2,4,8},
unknown_boost=0.7, drop_infra=True. Leave-future-out (rounds<r predict round r).

Used run: **s1k8b103** (manifest `comparison.json = f548ade3`; 34 skipped
variants<8).

cluster-key = task: 1438 prediction points (373 empty-history cells); 13 n=1
flip points.
| estimator | Brier | log-loss | n=1 flips |
|---|---|---|---|
| mle@0.5 | 0.15659 | 0.80733 | 8 |
| mle@0.7 | 0.17391 | 0.84465 | 8 |
| shrink@1 | 0.16832 | **0.50148** | 8 |
| shrink@2 | 0.17006 | 0.50610 | 8 |
| shrink@4 | 0.17504 | 0.52033 | 6 |
| shrink@8 | 0.18317 | 0.54195 | 6 |

cluster-key = level: 1438 points (154 empty); 0 n=1 flip points (only 3 clusters,
cells almost always have history). Brier ~0.219-0.226, log-loss ~0.629-0.644;
shrinkage differences negligible at this coarse grain.

Findings:
- Log-loss is where shrinkage wins big: shrink@1 0.501 vs mle@0.5 0.807 vs
  mle@0.7 0.845 (~38-40% reduction) -- MLE's 0/1 point predictions are punished;
  shrinkage is calibrated.
- The official 0.7 unknown-boost is MISCALIBRATED UPWARD for this low-pass-rate
  benchmark: mle@0.7 loses to a neutral mle@0.5 on BOTH Brier (0.174 vs 0.157)
  and log-loss (0.845 vs 0.807).
- Best Brier overall = mle@0.5 (raw, neutral empty); best log-loss = shrink@1.
  Classic calibration/sharpness trade-off.
- n=1 flips are thin on real data (13 points at task; 0 at level) because routed
  tasks are seen 0 or many times by a variant, rarely exactly once; among them
  shrinkage cuts flips 8->6 as m grows. Mechanism proven cleanly on synthetic
  data (test: 20 MLE flips -> 0 for shrink@8).

Outputs: `out/cold_start_offline_report.md`, `out/cold_start_offline_report.json`.

---

## Entry 4 -- Main-loop verification & ratification (2026-08-06, pre-commit)

Independent re-verification by the main session before commit:
- `git status --porcelain -uall`: 20 files, ALL under `experiments/audit_arms/`;
  zero existing-file modifications (separation invariant holds).
- pytest re-run by main loop: **32 passed** (0.24s), independent of the coder's run.
- Code review: SPRT increments/Wald boundaries/ASN formula correct; Hoeffding
  half-width correct (range=2 for paired diffs); repeated-looking alpha inflation
  of the Hoeffding race is acknowledged and empirically bounded by the A/A readout;
  shrink primitive/two-level chain/limits correct; A/A pair construction correct
  (same-cell attempt0-vs-attempt1, cluster=(run,task)).
- **D1 RATIFIED** (cross-round pairing): forced by the router's per-round task
  partitioning (same fact the audit doc records as "单载下未路由格未观测").
  Added threat-to-validity: cross-round pairs confound round-to-round harness
  drift with variant differences, so per-pair uplift attribution is noisy; the
  headline claims used (honest gate admits ~nothing at 1-3pp; REJECT
  concentrates in clear losers; A/A calibration) do not depend on clean
  attribution. Do NOT quote per-pair uplifts as causal variant effects.
- C1 / C2 accepted as recorded.
- Honest-finding kept prominent: p1=0.75 A/A false-ACCEPT = 0.0865 > alpha
  (discrete-LLR overshoot) -- aggressive-H1 SPRT must not be quoted as
  alpha-controlled; p1<=0.60 configs are the citable ones.
- Evidence status for the arms: idea ⑧ headline confirmed on real data
  (0 ACCEPT in the (1,3]pp bin at every p1 = "racing does not manufacture
  power, it reallocates budget" -- 40.3% rollouts saved via early REJECT at
  p1=0.75); idea ⑩ headline = shrink@1 cuts log-loss ~38% and the official
  0.7 boost is miscalibrated upward on GAIA (loses to neutral 0.5 on both
  metrics) -- direct empirical motivation for both arms' CH4 flags.
- Data caveat: only ONE K=8 run exists (s1k8b103, manifest f548ade3); all
  numbers are single-run; re-run sims when T1 lands more K=8 data.
