# Chapter 3 — Reproducing the Variant Pool

> **Draft status (Aug-02).** Sections 3.1–3.5 are complete and depend on no pending
> run. Section 3.6 states the two arms and their pre-registered readouts; its result
> tables are held open until both arms land. Rewritten from the Jul-30 draft: the
> operational narrative (watchdog drills, a mid-run endpoint redeployment, the
> artefact-delivery defect and its repair) has been reduced to Appendix B and to the
> threats section, and §3.5 is new.

---

## 3.1 What is reproduced, and what "reproduced" is allowed to mean

HarnessX §4.5 introduces *Ensemble routing*: instead of evolving a single harness,
the system maintains up to `K` variants and routes each task to the variant with the
highest estimated success rate on that task's cluster. The mechanism has no public
implementation. This chapter rebuilds it and reports what the rebuild reveals.

The reimplementation provides a configurable variant pool, cluster routing over
Laplace-smoothed `(p+1)/(a+2)` success estimates with a prior-round routing freeze,
the fork/retire lifecycle, an `ever_solved` ratchet, the pass@2 estimator following
Appendix A.3 as far as its published text allows, and round-boundary resume. Every
mechanism is flag-gated; the upstream `harnessx/` tree is untouched and flag-off
behaviour is byte-equal to upstream, with all changes confined to the recipe layer.
The reimplementation carries 1,005 passing tests.

Two limits are stated once and hold throughout. First, this is a *paper-informed
reimplementation*, not a bit-exact replication: the base model differs (DeepSeek V4
in place of the paper's Sonnet/GPT/Opus configurations), so absolute scores are not
comparable to the paper's. The objects of comparison are **shape** — does the
trajectory ratchet or degrade — and **between-arm differences** under one fixed
configuration. Second, the paper leaves several mechanisms underdetermined. Where it
does, the choice is recorded as ours rather than presented as the paper's, and where
two readings are both defensible the alternatives become arms rather than decisions
taken after seeing results.

The chapter proceeds through the reproduction protocol (§3.2–§3.4), then reports the
structural finding that the rebuild produced (§3.5) and the two arms that test its
remedy (§3.6).

---

## 3.2 Pre-registration: freeze packages and the deviation registry

**Freeze packages.** Every formal run is governed by a document written *before* the
run starts, fixing both what will be measured and how each outcome will be read,
paired with an `experiment.lock` recording the code SHA, the dataset/config/prompt
digests, and every parameter that is ours rather than the paper's. The purpose is to
remove the option of rescuing a disappointing result by choosing a different metric
afterwards. A pre-registered **noise band** accompanies each readout: a single-round
aggregate change within ±5% licenses no improvement or regression claim, while the
gate itself remains per-task zero-tolerance.

**The deviation registry.** Because the paper underspecifies many mechanisms, the
reproduction maintains a registry as the authoritative index of its methodological
boundary. Entries are tagged by evidence class: **[PAPER]** (stated or directly
readable), **[OURS]** (an engineering choice made to render an unspecified mechanism
executable — never a paper parameter), **[UNKNOWN]** (the paper does not give enough
to implement uniquely), **[UNVALIDATED]** (unit and integration tests are not
real-LLM validation). The registry's governing rule is that the correct description
of the code is "implemented and tested several mechanisms and contracts", not
"reproduced or validated the paper's Ensemble conclusions". It is reproduced in full
as Appendix A.

The registry was populated in part by a double-blind audit in which two isolated
readers reconstructed the mechanism from a single source each — one from the paper,
one from the code — and a third compared them. Beyond confirming already-declared
deviations, the audit surfaced **nine internal contradictions within the paper**, of
which two bear on this chapter: Algorithm 1's single-ship against Appendix B.1's
ranked multi-ship (M-17), and §4.1's global against §4.5's per-variant seesaw scope
(M-23). The latter is not a coverage gap but a structural asymmetry — the regression
side of the gate consults a global cross-variant `ever_solved` set while the
improvement side consults a per-variant cell — and it is mathematically invisible at
`K=1`, appearing only once a second variant exists. It is the reason `K=1` smoke
testing could not have caught it.

---

## 3.3 Epoch discipline

The experiments span two compute epochs, and the boundary is treated as a
methodological hazard rather than an implementation detail. Early runs used a
commercial API; the supply source later moved to a supervisor-hosted DeepSeek V4
deployment behind a LiteLLM proxy over vLLM, exposing a `flash` executor tier and a
`pro` meta-agent tier.

The governing rule is that data from different epochs may be compared by **shape
only, never by absolute magnitude**. Each epoch re-establishes its own **R0
rebaseline** — a fresh baseline round on the same bench under the new endpoint — so
that within-epoch drift is measured against a same-epoch anchor. Cross-epoch runs
serve as shape references and nothing more.

One provenance defect was found and fixed before the main runs: because the endpoint
was supplied by environment variable, the lock recorded `api_base = "unresolved"`,
which made the two epochs indistinguishable in the provenance record and defeated
the resume guard's ability to detect an endpoint swap. The base URL (never the key)
is now captured into the lock, and resume refuses a cross-epoch continuation.

---

## 3.4 Reliability infrastructure

Long unattended runs require infrastructure that survives interruption without
corrupting a pre-registered analysis. Three properties were certified by drill
rather than asserted, and are documented in Appendix B: round-boundary resume
(validated by a deliberate mid-round kill, rebuilding exactly the settled rounds and
re-spending nothing on them), a watchdog that acts only on a confirmed-dead process
and fires the pre-registered resume rather than a clean restart, and an off-session
launch procedure that survives session termination.

The infrastructure was exercised once in earnest, when a server redeployment
mid-run orphaned in-flight connections and stalled every worker. The certified
resume path continued from the last settled round. The incident is recorded as a
threats-to-validity item in §3.7 because it split one run across two server versions,
not as evidence about the mechanism under study.

---

## 3.5 The effective pool is `min(K, n_clusters)`

This section reports the chapter's structural finding. It concerns the reproduction's
own configuration as much as the paper's text, and it is stated in that order.

### 3.5.1 The paper's routing rule contains an undefined function

§4.5 p.11 specifies routing as sending each task "to the variant with the highest
estimated success rate on that task's **cluster**". The clustering function is never
defined — the paper gives neither an algorithm nor a mapping — and §6.3 lists
"domain-aware clustering" among strategies that "lack sufficient rounds and tasks for
statistically meaningful comparison", indicating that its default is coarser than
domain-level without saying what it is. That the function is undefined was already
recorded as deviation M-02, which adopted GAIA difficulty level as an auditable
stand-in and listed alternative clusterings as a pending ablation.

### 3.5.2 The consequence M-02 did not state

What the earlier registry entry treats as a choice of proxy is in fact a **capacity
constraint**. Routing is `argmax` *per cluster*: every task in a cluster goes to
whichever variant scores highest on that cluster, so the number of variants that can
hold any task at all is bounded by the number of clusters. The effective pool is

> **`min(K, n_clusters)`**

GAIA has three difficulty levels. Under a difficulty partition, therefore, a pool of
`K = 8` can never load more than three variants — regardless of the gate, the
evolution strategy, or the number of rounds. Five of the eight are idle by
arithmetic.

This is observable in the reproduction run. Cross-tabulating per-round routing
against GAIA level shows the partition *is* the level:

| Round | Variant → tasks |
|---|---|
| R8 | V0:{L1:39} V3:{L2:52} V5:{L3:12} |
| R12 | V0:{L1:39} V6:{L2:52, L3:12} |
| R15 | V0:{L1:39} V7:{L2:52} V6:{L3:12} |

Load concentration rises monotonically with pool size — Gini 0.22 at R4, when four
variants existed, to 0.78 at R14, when eight did — and the final distribution is
`[52, 39, 12, 0, 0, 0, 0, 0]`. Adding variants to this pool adds idle variants.

The consequence propagates into the gate. Under §4.5 a candidate is tested only
against the tasks routed to its target variant; a variant holding no tasks therefore
cannot have a candidate evaluated at all, has no evidence to feed the digester, and
has no route by which it could be improved. It is not merely unused but unimprovable.

### 3.5.3 A second channel: routing is confounded with measurement history

An unmeasured `(variant, cluster)` cell sits at the Laplace prior of 0.5. A variant
that has never been routed a cluster therefore scores 0.5 on it, and loses to any
variant with a measured rate above that. To be measured a variant must first win; to
win it must first be measured. A variant that loses early is frozen out permanently,
and the resulting concentration reflects measurement history rather than a difference
in competence.

The reimplementation inherits an ε-greedy escape hatch whose own documentation names
this failure — its purpose is to "stop a variant that lost early from being frozen
out by argmax forever" — but the parameter was never exposed on the command line, so
every run to date took the default of zero. Both formal runs are therefore in the
regime the escape hatch exists to prevent.

### 3.5.4 What the remedy has to be

The two channels call for different interventions, and neither works alone.

A finer partition creates capacity but does not distribute it: with more clusters and
unmodified `argmax`, a leading variant simply wins more of them. Exploration
distributes load but cannot create capacity: with three clusters there are three
owners to rotate among no matter what ε is. This was checked by replaying the
recorded per-round measurements of the reproduction run under counterfactual routing
rules — a free procedure on real data, since the measurements are fixed and only the
assignment rule changes:

| Partition | Clusters | ε | Final Gini | Variants loaded |
|---|---|---|---|---|
| difficulty | 3 | 0 | 0.72 | 3 / 8 |
| capability | 11 | 0 | **0.82** | **2 / 8** |
| difficulty | 3 | 0.1 | 0.66 | 6.9 / 8 |
| capability | 11 | 0.1 | 0.76 | 6.8 / 8 |

The capability partition **alone is worse than the difficulty partition it replaces**.
Only the combination reduces concentration. The replay measures where load would go,
not what accuracy would result; the accuracy cost of exploration — some tasks are
routed to variants that are worse at them, in proportion to ε — is invisible to it
and is reported separately in §3.6.

### 3.5.5 The capability partition

The finer partition keys each task on the **set** of subtask types its decomposition
requires, drawn from the four-type taxonomy of Chapter 4 (`search`, `browse`,
`compute`, `verify`). Labels are computed from the task text alone, before any
attempt, and frozen to a digest-stamped file that every arm reads, so routing on them
encodes no outcome.

The *dominant* type is unusable: nearly every GAIA task begins with retrieval, and a
ten-task probe assigned `search` as dominant to all ten, collapsing the partition to
a single cluster. The type *set* separates them: over the 103-task bench it yields
eleven profiles, which merge to five once profiles below eight tasks are folded into
their nearest neighbour by Jaccard similarity over the type sets — a threshold set by
the gate's needs, since a candidate is tested only on its variant's tasks and a
two-task cluster gives that test no power.

| | difficulty | capability |
|---|---|---|
| clusters | 3 | 5 |
| largest cluster | 52 / 103 (50%) | 38 / 103 (37%) |
| effective pool ceiling | **3** | **5** |

The ceiling of five, not eight, is itself a finding: the bench supports about five
capability niches with enough tasks to sustain a gate decision, so a pool sized to
the benchmark rather than to a chosen `K` would hold five variants.

---

## 3.6 Arms and pre-registered readouts

Two arms differ by three flags and nothing else.

| Arm | Partition | ε | Purpose |
|---|---|---|---|
| **E-pervar** | difficulty (3) | 0 | The configuration as reproduced |
| **E-capability** | capability (5) | 0.05–0.1 | Both remedies of §3.5.4 |

Both run 103 tasks, `K = 8`, 16 rounds, pass@2, concurrency 10, with the per-variant
seesaw scope of §4.5 p.11. Both are single-seed; the paper plans three, and the
budget permits one.

**Readouts, fixed before the runs.**

1. *Differentiation*, on three axes: the number of distinct system prompts across the
   final pool, the dispersion of per-cluster win rates, and the number of distinct
   normalised config digests. Differentiation is claimed only if all three move
   together.
2. *Load*, as the final Gini and the count of variants holding at least one task.
   This is the axis the offline replay predicts, so it is the one that tests the
   prediction rather than merely reporting an outcome.
3. *Accuracy*, as paired per-task pass@2, read under the interpretation rules below.

**Interpretation rules.**

- All accuracy comparisons are **within-task paired** (McNemar, paired bootstrap).
  The directly measured noise floor is SD 4.57 pp at *n* = 103 and 6.41 pp at
  *n* = 50, giving 2 SD thresholds of 9.1 and 12.8 pp; an aggregate pass@2 difference
  is therefore very unlikely to clear the floor, and unpaired aggregate comparisons
  are not reported as evidence.
- **Peak is `max`-of-N and must be de-biased.** With N = 16 rounds and σ = 4.57 pp
  the expected maximum exceeds the mean by roughly 1.8 SD ≈ 8 pp, so a negative
  final-minus-peak drift is guaranteed by construction and is not, on its own,
  evidence of degradation.
- **Exploration is not free.** ε routes a fraction of tasks to variants that are not
  the argmax choice, at an accuracy cost proportional to ε. The arm reports load and
  accuracy together; a load improvement bought at an accuracy loss is reported as
  such.
- **Infrastructure failures count as failures** and are not resampled, following
  Appendix A.3 p.29. The measured rate is 2.5–3.9% and is reported separately.

**Outcomes and what each licenses.** If differentiation appears under the capability
partition and not under difficulty, the finding is that specialisation in this system
is limited by the routing partition rather than by the gate. If it appears under
neither, the finding is that the difficulty of the bench, and not the partition, is
the binding constraint — a reading the paper's own §6.2 supports, where the GAIA
GPT-5.4 configuration yields ∆ = 0.0 and is attributed to task heterogeneity. Both
are results; neither leaves the chapter empty.

---

## 3.7 Threats to validity

**Single seed.** The paper plans three seeds and the budget permits one. The noise
floor is measured directly rather than assumed, and every comparison carries the 2 SD
threshold of §3.6.

**Reasoning effort is uncontrolled, in the paper as well as here.** Table 8 and
Appendix A.5 specify models, concurrency and step caps but no reasoning-effort,
temperature or thinking-budget setting — while the paper's headline GAIA
configuration uses a model family that exposes such a control. Measured on the
`pro` tier used here, moving from no reasoning to high reasoning changes completion
tokens by roughly 19× and wall-clock by 15×. Both arms of this chapter hold the
setting unset and identical, so the comparison is internally valid, but the paper's
absolute results are not reproducible along this axis by anyone.

**Model family.** DeepSeek V4 in place of Sonnet/GPT/Qwen. No cross-family
extrapolation is claimed.

**Search environment.** A fraction of retrievals return nothing: the primary provider
returns empty results for some queries and the built-in fallback chain then fails as
well, 374 times over the run. This depresses absolute scores and adds variance. It is
constant across arms, so the paired comparison is unaffected.

**Server version split.** One run spans two server versions at identical weights and
tensor parallelism, following a mid-run redeployment (§3.4).

**A discarded run.** An earlier execution of the reproduction did not deliver the
evolved artefacts its configurations declared; that run is discarded and reported
nowhere in this thesis. The current implementation validates that each declared
artefact actually instantiates before a round begins, and all reported results come
from runs under that check.

---

## 3.8 Summary

The reproduction rebuilds §4.5's variant pool and, in doing so, locates a constraint
the original does not state: routing `argmax` per cluster bounds the effective pool
at `min(K, n_clusters)`, so the reported `K` is an upper bound on pool size rather
than a description of it, and the paper reports `K` without reporting the number of
clusters. Under a difficulty partition on GAIA the bound is three, and five of eight
variants are idle by arithmetic — unused, and by the scoping rule of the gate,
unimprovable. A second and independent channel confounds routing with measurement
history, freezing out any variant that loses early.

Chapter 4 builds on the pool this chapter produces. Its central mechanism —
assigning each subtask to the variant best suited to it — presupposes variants that
differ in capability rather than in the difficulty of the tasks they happen to have
been given, which is what §3.5.5 is for.
