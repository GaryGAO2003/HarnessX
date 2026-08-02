# Chapter 7 — Discussion (partial draft)

> **Scope of this draft.** This file contains only the *threats-to-validity backbone*
> of Chapter 7 plus one qualitative *discussion seed*, per the task-book instruction
> to present the threats checklist first (full mechanism discussion waits for the
> settled S1/E0/B data). Section numbers (§7.4, §7.5) are provisional and will be
> re-indexed when the full Discussion chapter is assembled. Grounding cross-references
> point to Appendix A (deviation ledger, M-01…M-25) and Appendix B (infrastructure
> audit / RUN-LOG). No run-specific magnitudes are asserted as results here: every
> quantity that would come from an in-flight run is held with `[PENDING]`.

## 7.4 Threats to Validity

We adopt the attribution-language discipline used throughout this thesis: only the
oracle-gated cell (E0, §7.4.5) licenses language of *clean attribution*; every other
comparison is reported as *indicative*, and every conclusion is stated in hypothesis
form with its scope tag attached (e.g. "not falsified on GAIA text-only, n = 103,
single seed"). The threats below are organised so that each makes
explicit (i) what the threat is, (ii) why it arises in *our* concrete setup, grounded
in the deviation ledger and run log, (iii) what it does and does not invalidate, and
(iv) the mitigation we take or the limitation we honestly accept.

### 7.4.1 Single-seed evaluation

**What the threat is.** All headline runs — the K = 8 reproduction arm, and the
planned K = 1, E0, and B0/B1/B2 arms — are executed under a single random
seed. The baseline protocol specifies three seeds per cell (Table 8). One seed cannot
separate a mechanism effect from seed-level sampling variance, which is heavy-tailed
for online, tool-using agentic tasks.

**Why it arises here.** This is deviation M-10 (three-seed aggregation). The run lock
stores a single executed seed alongside `planned_seeds`; we never present a seed list
as if it had been run. The single-seed decision was a deliberate, budget-driven scope
reduction recorded in the S1 freeze (single seed rather than the paper's three per cell, with the limitation declared) and taken as the
ignition order after the床 was moved to the full 103 tasks. The specific launch
configuration and settled curve are held with `[PENDING: s1k8b103 results]`.

**What it does and does not invalidate.** It does *not* touch the reproduction-audit
contribution (the deviation ledger and infrastructure audit are unconditional). It
does *not* invalidate our within-run paired comparisons, because the inferential unit
is the *within-task paired outcome*: McNemar's test over the 103 shared tasks with
bootstrap 95 % confidence intervals controls task-sampling variance even under a single
seed. It *does* invalidate any claim about *between-seed* variance or robustness of an
effect to re-seeding — we have no seed-variance estimate, and reported magnitudes are
conditioned on one sampling draw.

**Mitigation / honest acceptance.** Partial mitigation: the pre-registered McNemar
within-task pairing plus bootstrap CI recovers the task-level uncertainty that matters
for the headline contrasts. Honest acceptance: there is no seed-variance estimate; all
conclusions carry the explicit "single seed" scope tag, and `planned_seeds` is recorded
so the full three-seed matrix can be completed later without re-labelling the executed
run as more than it was.

### 7.4.2 Supply-source epoch switching

**What the threat is.** The model supply source changed during the project, and even
within the main run the serving stack version changed mid-trajectory. Absolute scores,
and possibly mechanism-adjacent behaviour (sampling, tool-calling, latency, rate
limiting), may not be commensurable across an epoch boundary.

**Why it arises here.** There are two boundaries, both in the run log.
(1) *Project-level supply migration.* The official DeepSeek API epoch (fixsmoke1,
a1big1–5) gave way to the lab-endpoint epoch (LiteLLM proxy, vLLM TP4). The RUN-LOG
labsmoke1 entry (Jul-29) opens the lab epoch explicitly and rules that official-epoch
data is used only as a cross-epoch *shape* reference, never as a same-caliber magnitude
comparison. (2) *Within-run boundary in the main run.* R0 was evaluated on
`vllm-0.24.0`; all adaptation rounds R1+ on `vllm-0.23.0`. The RUN-LOG incident entry
(Jul-30) attributes this to a server redeploy at ~18:05 — the serving fingerprint moved
from `vllm-0.24.0-tp4-ep-fdf19bca` (smoke record) to `vllm-0.23.0-tp4-ep-22012769`,
a simultaneous version-plus-deploy-hash change — which orphaned in-flight connections.
The run log records the same weights and the same tensor-parallel layout, judges the
expected effect ≈ 0, and files the R0/R1+ split as a threats-to-validity footnote.

**What it does and does not invalidate.** It does *not* invalidate within-epoch,
within-run paired comparisons — the arms we actually contrast (K = 8 vs K = 1, B2 vs
B1) are each run inside a single epoch and compared within-task. It does *not*
invalidate the infrastructure-audit narrative (Appendix B), for which the incident is
itself evidence. It *does* caution against (i) cross-epoch magnitude comparison, which
we explicitly avoid, and (ii) treating R0 (0.24.0) and R1+ (0.23.0) as bit-identical
serving inside the main run. Because R0 is the pre-adaptation baseline round and every
adaptation round runs on 0.23.0, the within-run drift readout (final − peak) is computed
inside the homogeneous 0.23.0 sub-trajectory, which limits the exposure of the headline
degradation-shape statistic to the version split. The corresponding drift value is
`[PENDING: s1k8b103 final data]`.

**Mitigation / honest acceptance.** Provenance capture: the lock now records
`deepseek_api_base` and an endpoint-epoch field — captured under no deviation number of
its own, this provenance wiring landed alongside the M-23 regression-baseline switch in
the same acceptance batch (2026-07-29) — and the resume guard
blocks cross-epoch continuation of a run. Honest acceptance: the R0/R1+ version split is
documented rather than smoothed over — we do not claim identical serving across it — and
cross-epoch data is used only for shape.

> **Note on concurrency provenance.** The S1 freeze file contains two concurrency
> entries: an initial adjudication (concurrency fixed at 3, adjudicated on the evening of Jul-29, reported to the
> supervisor in those terms) and a superseding head-of-file amendment (concurrency
> raised from 3 to 10 on the user's adjudication that a concurrency of 10 was
> acceptable, reverting to the paper's Table 8, with the supervisor-facing framing to
> be updated by the user), the latter consistent
> with the same file's R0-watchdog pre-registration "under c = 10" and with the
> s1k8b103 launch config (concurrency 10, Jul-30). The operative value is therefore
> reconciled at c = 10 by a recorded user re-adjudication; the residual defect is
> documentary — the superseded "3, never raise during S1" paragraph was left unstruck
> in the freeze file. We report this as a freeze-document hygiene deviation, not a
> protocol breach.

### 7.4.3 Pool provenance non-stationarity

**What the threat is.** The evolved variant pool that the RQ2 arms (E0, B1, B2) route
over is the product of *one* evolutionary trajectory. B0 is pool-free by design —
decomposition routed to a single fresh `h0` variant — which is exactly what makes it the
baseline and places it outside this provenance threat. The pool's composition — how many
variants survive, which clusters they specialise on, which tasks each effectively owns —
is itself a random draw. Any statement about "division of labour over the evolved pool"
is conditioned on that specific pool instance.

**Why it arises here.** The E0, B1, and B2 arms draw the pool with `--decomp-pool-from
runs/s1k8b103`, i.e. the single-seed final pool of the main run (E0 freeze, §2). That
pool is produced by one seed's adaptation trajectory over a set that is *reused* for
adaptation, routing, and selection — deviation M-09 (no independent held-out; selection
bias on the adaptation set). Because fork events are path-dependent (§7.4.4), the pool's
cardinality and cluster partition are non-stationary across hypothetical re-runs. The E0
freeze conditions on this explicitly: if the final pool collapses to K = 1 (no fork
recurrence), cell ④ degrades to "single variant + decomposition" and the gate semantics
are re-annotated. The realised composition is `[PENDING: s1k8b103 final pool]`.

**What it does and does not invalidate.** It does *not* invalidate the reproduction-audit
observation that a pool forms and self-partitions by cluster — that is a
mechanism-existence finding. It does *not* invalidate within-pool paired arm contrasts,
which are all evaluated against the *same* fixed pool on the same 103 tasks. It *does*
invalidate any claim that the *magnitude* of the division-of-labour effect generalises to
a differently-seeded pool: the pool is one instance, not a sampled distribution. We also
note (per the prohibition on the "truly heterogeneous" framing) that our pool is
*homogeneous* — variants share the backbone and differ only in system prompt, tools, and
position — so it sits inside the regime that a single strong agent can in principle
absorb. The design includes no separate single-agent baseline that would test this
absorption directly (Chapter 4, §4.5.2); accordingly we report the pool as homogeneous
rather than claiming architectural heterogeneity, and we make no claim that the
pool-plus-decomposition apparatus outperforms a single un-evolved agent.

**Mitigation / honest acceptance.** Honest acceptance: the pool is a single provenance
instance; pool-conditioned results are labelled *indicative* and stated conditional on
the observed pool. Structural mitigation: the provenance lock records the pool's
fork/retire lineage so the exact instance is reproducible and auditable, and the
pre-registered plan reports actual active-pool size and fork/retire counts. Full
mitigation (multiple independently-seeded pools) is deferred to the `planned_seeds`
completion.

### 7.4.4 Fork stochasticity and path dependence

**What the threat is.** Forking — the mechanism that grows the pool — is stochastic and
path-dependent. Which candidate wins a slot, which target variant is selected each round,
and whether a mixed conflict crosses the fork threshold all depend on prior-round
outcomes that are themselves sampled. The resulting genealogy (V0 → V1 → V2 …) is one
realisation of many possible ones.

**Why it arises here.** Several ledger and log entries converge:
- M-04 (fork noise and threshold): the paper gives neither the improved/regressed task
  counts required to fork nor a definition tying its ±5 % noise band to forking; our
  default `min_fork = (1,1)` is an `[OURS]` choice, so fork frequency is sensitive to a
  threshold we selected.
- M-05 (fork/retire lifecycle): inheritance of ledger/routing/identity, same-round
  retirement, and freeze timing are `[OURS]` two-phase-settle decisions, not paper
  parameters.
- RUN-LOG forceprobe2 ("fork settlement chain first ran end-to-end"): the target
  selector can pick a variant with zero routed trajectory, producing a whole-round idle
  ("P2 target-selection starvation"), and a forced low-quality fork produces a "zombie
  variant" that occupies a pool slot until retirement — direct evidence that the
  genealogy is contingent on target selection and routing state.
- RUN-LOG a1big5 (n = 30 pilot): the pool grew 1 → 2 → 2 → 3 across rounds via successive
  forks, including a second-generation fork (a child of a child) — an explicitly
  path-dependent lineage.
- M-23 (regression-baseline scope): under K ≥ 2 the regression baseline is
  global-across-variants and asymmetric, and it only surfaces at K ≥ 2; this shapes fork
  and pool-inflation behaviour, and is switch-controlled (default `global`).
The genealogy for the main run is `[PENDING: s1k8b103 fork lineage]`.

**What it does and does not invalidate.** It does *not* invalidate that forking occurs
and yields cluster-specialised children (mechanism existence). It does *not* invalidate
within-run paired comparisons. It *does* invalidate any deterministic reading of the
specific genealogy or fork count — a re-seed could produce a different pool shape — and it
means any attribution of an outcome to "the fork at round r" is *indicative*, never clean;
only the oracle-gated E0 cell licenses clean attribution.

**Mitigation / honest acceptance.** Pre-registered fork-threshold ablation
((1,1)/(2,1)/(2,2)/CI-based) on a fixed seed and task set (M-04), reporting false-fork
rate, post-fork gain, and pool inflation; deterministic tie-breaks and two-phase settle
that remove same-round routing leakage (M-05). Honest acceptance: the reported genealogy
is a single realisation, and its path-dependence is stated rather than hidden.

### 7.4.5 In-sample oracle-decomposition gate (E0)

**What the threat is.** E0 — the oracle-decomposition 2 × 2 gate and decomposition-quality
upper-bound anchor — is evaluated in-sample. The pilot30 subset is drawn from the same
GAIA床 the pool evolved on, and the oracle plans are authored with knowledge of the
answers and solution paths. E0 therefore measures an in-distribution ceiling, not
out-of-sample generalisation.

**Why it arises here.** The E0 freeze states the subset is pilot30 (30 tasks carved from
the 103床 by L1/L2/L3 difficulty ratio) and records explicitly that it is "in-sample 于
演化床 — E0 是机理诊断非泛化主张, 声明即可" (in-sample on the evolution set — E0 is a
mechanistic diagnostic, not a generalisation claim; declaration suffices). The oracle
plans are written with answer / solution-path knowledge (the D1-lite four-type schema,
DAG-validated, fallback rate = 0 by construction), with an explicit guard against leaking
床 answers into the plan text. This is the M-09 selection-bias pattern *by design*: the
adaptation set doubles as the E0 evaluation set. E0 cell values are `[PENDING: E0
results]`.

**What it does and does not invalidate.** E0's designed role is a go/no-go for the B-arm
line and a decomposition-quality ceiling; in-sample-ness does *not* invalidate that role,
because a ceiling is legitimately read in-distribution — cell ④ − ③ is the net gain of
division of labour *under perfect decomposition*, an upper bound by construction. It
*does* invalidate reading E0 as evidence of generalisation: E0 says nothing about
held-out performance. E0 is the single oracle-gated cell, so under the attribution
discipline it is the one place we may speak of *clean* attribution — and even there the
claim is explicitly bounded to the in-sample ceiling.

**Mitigation / honest acceptance.** Honest acceptance: E0 is declared in-sample and framed
as a diagnostic / ceiling, never as generalisation, consistent with the freeze. Optional
strengthening: the ③′ structural placebo (a trivial single-subtask plan pushed through
the same `--decomp-eval` pipeline) isolates the pipeline artefact from the decomposition
content. The general held-out requirement (M-09: a frozen, one-shot held-out evaluated
once after selection) is acknowledged as unmet for the adaptation-set metrics and deferred.

### 7.4.6 Domestic-model format-layer noise

**What the threat is.** The re-implementation is driven by DeepSeek V4, substituted for
the baseline's inner- and outer-loop models. Beyond capability differences, the model
introduces *format-layer* noise: it emits journal-style vocabulary and prose where the
pipeline expects a structured schema, and it sometimes finishes its analysis without
writing the required configuration — so candidates die at parse or gate stages for reasons
that are formatting artefacts, not reasoning failures. Left unaccounted, this biases
mechanism statistics (candidate-evaluation rate, gate pass rate, fork frequency).

**Why it arises here.** This is the M-15 cross-model boundary made concrete by three
downstream deviations:
- M-15 (cross-model comparability): DeepSeek V4 replaces the paper models; capability,
  sampling, context, and tool-calling differ, so absolute scores and fork frequency
  cannot be attributed to the paper mechanism, and the project is positioned as a
  paper-informed re-implementation.
- M-19 (manifest schema absent from the open repo): the meta-agent writes journal
  vocabulary (`levers` / `predicted_affected` / …) that collides with our `extra =
  "forbid"` schema; in forkprobe_11 roughly a quarter of candidates died at
  `PIPELINE_PROPOSAL` for this formatting reason. (forkprobe_11 is an early probe run; the
  run log records that the forkprobe series was never backfilled into the narrative log, so
  this fraction and the next survive only in the run directory and git history and are
  reported here as approximate, not as log-carried results.)
- M-21 (no-config termination): the meta-agent frequently "analyses but never writes
  `config.yaml`" (`DECISION_REQUIRED.md`); in the same probe run roughly three-quarters of
  candidates died this way.
- M-22 (Level-2 evidence not declared): the open repo has no `capability_evidence` slot,
  so tool/processor candidates die at the `ROUNDTRIP_L2` gate even when the contract asks
  for the evidence.
These are output-convention failure modes specific to the substituted model.

**What it does and does not invalidate.** It does *not* invalidate the reproduction-audit
contribution — cataloguing these format-layer deaths and building the `[OURS]` adapters
around them *is* part of the audit. It does *not* invalidate within-run comparisons in
which both arms use the same model. It *does* invalidate any cross-model magnitude
comparison to the paper's absolute numbers, and it requires candidate-mortality statistics
to be decomposed into "format-layer death" versus "genuine reasoning / gate rejection"
before any mechanism claim; otherwise format noise would depress fork frequency for
reasons unrelated to the mechanism.

**Mitigation / honest acceptance.** Mitigations are `[OURS]` and pre-registered as
ablations: a repo-mode manifest adapter mapping journal fields to the schema and marking
paper-only fields as *missing rather than fabricated* (M-19); `--evolve-retry` for
no-config recovery (M-21); and the `--l2-cert` machine fallback that certifies Level-2
capability from real tool outputs or replay execution, honestly refusing when no evidence
exists (M-22). Each ablation reports its format-death rate and checks that the relaxations
do not introduce reward-hacking (a claimed capability that never appears yet ships). Honest
acceptance: absolute scores are never compared to the paper; all magnitude claims are
within-model, within-epoch, and *indicative*.

### 7.4.7 Unmatched compute across arms

**What the threat is.** The arms differ in how much compute they spend. A decomposed,
divided pipeline issues more model and tool calls than an undivided rollout, so an
accuracy difference between arms may reflect the *amount of compute spent* rather than the
*mechanism* — the division of labour — we intend to study. This is not a hypothetical: the
literature reviewed in Chapter 2 (§2.6) repeatedly finds that apparent gains from added
structure or extra agents shrink or vanish once compute is equalised across the compared
systems.

**Why it arises here.** Budgets are *deliberately not equalised* across arms. This is a
scope decision, taken to keep the design and its claims simple rather than to buy an
advantage: no arm is throttled or padded to hit a common token target, and no control
equalises compute across the arms. As a consequence the decomposed, divided arms
(B0/B1/B2, and E0) may consume more — or fewer — tokens, tool calls, and wall-clock than
the undivided pool arm (A1), and the difference is not controlled by construction.

**What it does and does not invalidate.** It does *not* touch the reproduction-audit
contribution, which is unconditional. It does *not* invalidate the within-arm structural
observations — pool formation, fork genealogy, routing cold-start dynamics — because those
are read inside a single arm and do not rest on a between-arm compute equality. It *does*
mean that no causal attribution of an accuracy gain to *division of labour rather than
compute* can rest on the design alone: because compute is not equalised across arms, a raw
between-arm accuracy difference is confounded with spend, and the design by itself cannot
separate the two.

**Mitigation / honest acceptance.** The mitigation is cost transparency, not equalisation.
Every arm reports its full cost — tokens, dollar spend, search-API calls, and wall-clock —
and the headline contrast is read *both* raw and cost-normalised. A gain that survives cost
normalisation is reported as a gain that is not merely bought with extra compute; a gain
that disappears once cost is normalised is reported as such, plainly. Honest acceptance: the
design does not license a compute-independent causal claim, and only the cost-normalised
reading — not the design — speaks to whether an effect is attributable to the mechanism
rather than to spend.

## 7.5 Discussion seed `[PENDING: s1k8b103 final data]`: routing cold-start over-confidence and self-correction

> **Status.** This section is a *qualitative* discussion seed drawn from RUN-LOG entries
> only. It reports no in-flight pass rates as results; every quantitative shape is held
> with `[PENDING: s1k8b103 final data]`. Its purpose is to name the phenomenon and
> motivate the routing-dynamics analysis that the settled run will support.

**The phenomenon.** In the variant-pool mechanism a newly forked child variant enters
with an empty — or candidate-evaluation-only — routing ledger. At that cold-start moment
the router must decide how much task traffic to send it on very thin evidence. Two RUN-LOG
observations bracket the resulting dynamics.

- *Under-trust pole (forceprobe2, RUN-LOG).* At R2, facing a two-variant pool in which the
  freshly forked V1's ledger consisted entirely of candidate-evaluation losses, the router
  made the conservative decision: it routed all tasks to the incumbent V0 and left V1 idle
  (alive, not retired). A new variant with no positive evidence is starved of the traffic
  it would need to prove itself — the cold-start under-trust extreme. This pole was observed
  under a **forced-fork probe**: forceprobe2 was launched with a forced fork gate and
  K_t = 2, a synthetic configuration that the run log notes the organic fork gate prevents
  in real runs — so it is not a product of organic evolution, and its transfer to the main
  run is provisional.
- *Self-correction pole (a1big5 pilot, RUN-LOG).* The pilot showed an early-round
  exploration retreat after the first fork, followed by recovery as the pool grew across
  rounds and routing began to partition tasks by cluster once real per-variant evidence
  accrued — traffic re-allocating itself as the ledgers filled.

**The hypothesis.** Between these poles we conjecture a transient *over-confidence →
self-correction* dynamic: on thin cold-start evidence the router mis-allocates — either
over-trusting a new variant after a lucky candidate win, or over-committing to the
incumbent — and then corrects as per-variant evidence accumulates. The main run (K = 8,
with a multi-variant pool reported in-flight) is the setting in which this would be
observed at scale, but the quantitative shape — round-by-round routing entropy,
per-variant hit-rate, switch rate, and the magnitude of any retreat-then-recover — is held
with `[PENDING: s1k8b103 final data]`. We deliberately report no in-flight pass rates: the
pilot evidence establishes the phenomenon *qualitatively* and motivates a cold-start
routing-dynamics analysis (routing entropy, switch rate, cold-start regret) once the run
settles.

**Caveats.** The pilot evidence is n = 30 with wide single-round confidence intervals, and
the dynamics are confounded by two mechanism choices: target-selection starvation (M-16 —
round-global target selection is a paper blank, and our `worst_first` selector is `[OURS]`)
and the global regression baseline (M-23), both of which shape *which* variant accrues
evidence and *when*. Any dynamical claim is therefore *indicative*, not clean attribution,
and is stated in hypothesis form pending the settled data.
