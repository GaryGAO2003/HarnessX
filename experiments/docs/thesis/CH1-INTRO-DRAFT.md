# Chapter 1 — Introduction

> **Draft status.** This chapter states motivation, research questions, and
> contributions; it introduces no numbers from our own runs, because the main runs
> are in flight and Chapter 1 does not depend on them. Citations use only the
> arXiv identifiers primary-source verified in the project's evidence documents
> (`NOVELTY-TIERS.md`, `deepread/DR-A..F`); no new citations are introduced. The
> crisp novelty-positioning sentence in §1.4 is deliberately left as a
> placeholder for the user to fill. Attribution language throughout follows the
> project's discipline: only oracle-gated comparisons are described as clean
> attribution, all other effects are labelled *indicative*, and empirical
> conclusions are stated in hypothesis form.

---

## 1.1 Background and Motivation

An LLM *agent* is not only a language model. It is a model wrapped in a
*harness* — the scaffold of prompts, tool interfaces, control loops, retry
policies, and output contracts that turns a next-token predictor into a
task-completing system (the term is developed in full in Chapter 2). A recurring
observation in recent agent research is that the harness, rather than the
underlying model weights, is frequently the dominant lever on end-task
performance: holding the base model fixed and changing only the harness can move
success on the same benchmark by a wide margin. If the harness is that
consequential, it becomes a natural object of *automatic* optimization — an
artifact to be searched over, mutated, and selected rather than hand-tuned once
and then frozen. This is the premise of the *self-evolving harness*: a
meta-level loop that inspects an agent's own failures, proposes edits to its
harness configuration, validates those edits, and ships the survivors, so that
the agent's scaffolding improves over successive rounds without human
intervention.

The central difficulty in this line of work is *durability*. An unconstrained
edit-and-ship loop tends to repair one failure while silently introducing
another, producing a "fix-one-break-one treadmill" whose net durable gain over
many rounds can approach zero. HarnessX/AEGIS (arXiv:2606.14249) — the system
this thesis reproduces and extends — is best read as a direct attempt to defeat
this treadmill, principally through a deterministic acceptance gate and a
*variant pool*: rather than committing to a single evolving harness, the system
maintains and evolves up to K parallel harness variants, forking specialized
descendants as evolution proceeds. Whether maintaining such a pool actually
changes the long-horizon degradation form — relative to evolving a single
harness under otherwise identical conditions — is an empirical question the
original work does not settle on the full benchmark, and it is the first
question this thesis asks.

The variant pool also creates an opportunity the original system does not
exploit. When evolution terminates, one holds not a single scaffold but a *set*
of differentiated harness variants — a latent supply of would-be specialists.
Such diversification is a stochastic outcome of the forking process, not a
guarantee — a pool can collapse to a single effective variant — so the extending
question below is conditional on an evolution run that actually diversifies its
pool (§3.6.3, §7.4.4). A
long-standing intuition in agent design is that complex, multi-step tasks should
be decomposed into subtasks and dispatched to specialized executors. Yet on
GAIA — the benchmark on which HarnessX/AEGIS is evaluated — this intuition has
repeatedly disappointed: decomposition and added multi-agent structure have not
reliably beaten a strong single agent. This is the tension that motivates the
second, extending question of the thesis: a benchmark on which decomposition
tends to fail, standing next to an evolution procedure that nevertheless
produces a pool of differentiated variants that seem built to be used as
specialists.

## 1.2 The Central Question

The evidence that decomposition underperforms on GAIA is not a single result but
a convergence of independent ablations, and we state it in the form that governs
this thesis:

> On GAIA, adding decomposition/multi-agent structure has repeatedly failed to
> beat a strong single agent **despite spending equal-or-greater compute**
> (JoyAgent 71.5>70.3; MiroFlow 74.8>71.9 GAIA-only; AgentOrchestra
> planner-only=36.54; cf. 2606.13003, 2604.02460, 2606.15017).

This recognized negative result is what makes the question worth asking, not a
claim this thesis sets out to overturn: it is precisely why one cannot assume in
advance that adding decomposition to the reproduced system will help. Our own
comparison is deliberately *internal*. Rather than measuring the added layer
against the literature's strong single agents, we measure it against the system
we ourselves reproduce (Chapter 3) — the evolved variant pool with whole-task
routing — holding the base model, benchmark, pool, and seed fixed, so that any
difference is attributable to the added layer and not to a change of setting. The
layer itself does not decompose over freshly spawned generic workers; it routes
subtasks over the *evolved* pool of harness variants that the preceding evolution
run has already differentiated. And because a gain could be merely bought with
compute, we report each arm's full cost and give the headline reading both raw and
normalised by that cost. The central question of this thesis is therefore posed not
as a claim but as an open empirical test:

> **Does adding a layer of task decomposition with subtask-level *division of
> labor* improve on the system we reproduce — the evolved variant pool with
> whole-task routing — on GAIA, and, either way, why?**

We do not presuppose the answer. The experimental design (Chapter 5) is
pre-registered so that the outcome is interpretable in either direction: a
positive result is reported as a mechanism finding, a null result as a
strongest-setting negative result, and any difference falling inside a
pre-declared noise band as no claim at all. The comparison is made against the
reproduced system — the evolved pool with whole-task routing — with every arm's
full cost reported and the headline read both raw and normalised by that cost: a
negative outcome is reported as a bound on what the added layer buys over the
reproduced system, and a positive outcome is checked against the cost-normalised
reading before division of labor — rather than raw compute — is credited as its
source. Either way the outcome does not stand alone: the same design decomposes
the effect into what the pool contributes, what specialization contributes, and
whether decomposition quality is the binding constraint, so that the question of
*why* is answered alongside the question of *whether*.

## 1.3 Research Questions

The thesis is organized around two research questions, the first a reproduction
audit and the second an extension.

**RQ1 (Reproduction and audit).** Does maintaining and evolving a pool of eight
harness variants (K=8), rather than a single harness (K=1), change the
long-horizon degradation form of harness evolution? We reproduce the paper's
variant-pool mechanism and run both configurations for up to sixteen rounds,
subject to the patience protocol discussed in §3.6.4, on the original 103-task
GAIA text-only benchmark, comparing their degradation
trajectories under an identical acceptance gate and a governed supply-epoch
discipline.

**RQ2 (Extension).** Does adding a layer of task decomposition with subtask-level
division of labor to the reproduced system improve on that system — and, either
way, why? The baseline is **A1**, the reproduced system: the evolved variant pool
with whole-task routing, exactly as reproduced in Chapter 3. The comparison is
fully internal — same base model, same benchmark, same pool, same seed. The
**primary result** is the best divided arm minus A1: does the added layer improve
on the original? The *why* is a deliverable in its own right, carried by three
cause-analysis contrasts — the pool's contribution (the divided pipeline over the
evolved pool minus the same pipeline over a single fresh executor), specialization's
contribution (specialized routing minus round-robin routing over the same pool),
and, via an oracle-decomposition gate, whether decomposition quality is the binding
bottleneck. Inference is by within-task paired testing with bootstrap confidence
intervals; differences within the pre-declared noise band are reported as noise, and
`pass@1` and best-of-k are reported separately, with every arm reporting its full
compute cost and the headline read both raw and normalised by that cost.

## 1.4 Contributions

The contributions are organized in three layers, from conservative to ambitious,
so that the thesis has something defensible to report regardless of how the
empirical question in §1.2 resolves.

**(i) Reproduction and audit (unconditional).** We deliver a paper-informed
reproduction of the HarnessX/AEGIS variant-pool mechanism (§4.5 of the original
work), together with a systematic deviation register of twenty-five documented
items marking the methodological boundary between the paper's specification and
our paper-informed reimplementation, including documented cases where the
paper and its public repository themselves disagree. This layer
includes the K=8-versus-K=1 degradation-form comparison (RQ1), a governed
reproduction protocol (frozen experimental packages, a single supply epoch, and
a pre-registration discipline), and the reliability infrastructure that made a
long, multi-round run auditable (certified resume, watchdog recovery, and a
recorded incident log). This layer stands independently of any of the method or
empirical results below.

**(ii) Method layer (direct lineage precedents).** We add a training-free layer
that decomposes a task into typed subtasks and routes those subtasks over the
evolved variant pool. Its components are deliberately standard and are presented
as ported and integrated technique, not as invention: a one-shot,
type-conditioned decomposer; a two-level router whose cold-start fallback
reduces to the original per-task mechanism; and an observational
credit-assignment ledger over (variant × type) slots together with a
leave-one-out calibration that is specified and pre-registered but not yet
implemented (§4.4.3) — the ledger adds no extra rollouts, while the calibration
would cost n+1 re-evaluations, and the two are costed separately — whose
slot-swap procedure mirrors the model-replacement protocol of arXiv:2605.27621. Each component has explicit
precedents, catalogued with citations in Chapters 2 and 4; the layer's value is
in composing them onto an evolved persistent pool, not in the components
themselves.

**(iii) Empirical layer (pre-registered, interpretable either way).** We report
the RQ2 test of the added layer over the evolved variant pool, delivering two
things: (a) whether adding the layer improves on the reproduced system — the best
divided arm against A1, the evolved pool with whole-task routing — and (b) a
decomposition of that effect into what the pool contributes, what specialization
contributes, and whether decomposition quality is the binding bottleneck. In the
positive branch, the result is a mechanism finding — evidence that the value of
decomposition on GAIA is conditional on routing subtasks to a differentiated
pool of executors rather than to a single or homogeneous one — stated as a
hypothesis not falsified on GAIA text-only, n=103, single seed. In the negative
branch, it is reported as a bound: the added layer does not improve on the
reproduced pool on GAIA, with every arm's full cost disclosed so the reader can
see what each side spent. A pre-registered decision tree, with an
oracle-decomposition 2×2 gate for clean bottleneck attribution and a declared
noise band that suppresses claims on differences of five tasks or fewer,
guarantees that either outcome is interpretable; effects outside the oracle-gated
cell are reported as *indicative* rather than as clean attribution.

[PLACEHOLDER: novelty positioning — awaiting user selection]
[Note (2026-07-31): the answer space changed — the thesis no longer frames its
novelty around equalising compute across arms; the anchor is now the *evolved
persistent pool* axis plus the reproduction/audit layer.]

With the lineage above fully cited (cf. 2606.13003, 2604.02460, 2606.15017), the
empirical layer's contribution is this RQ2 test itself — division of labor routed
over an evolved, persistent variant pool on GAIA, reported with every arm's full
compute cost and the headline read both raw and normalised by that cost; we make
no novelty claim for the architecture itself, whose components all have
precedents.

## 1.5 Thesis Outline

Chapter 2 develops the background and related work: the lineage of self-evolving
harnesses, a precise description of the HarnessX/AEGIS mechanism, and the three
related lines this thesis draws on — task decomposition, subtask routing, and
credit assignment. Chapter 3 presents the reproduction and audit (RQ1): the
frozen-package protocol, the supply-epoch and reliability infrastructure, the
deviation register, and the K=8-versus-K=1 degradation-form results. Chapter 4
specifies the method layer: the typed decomposer, the two-level router, and the
observational credit scheme, together with the cross-arm plan-replay design.
Chapter 5 defines the experimental design for RQ2 — the arm ladder, the
pre-registered readout logic, and the anti-confound controls. Chapter 6 reports
the results and analysis. Chapter 7 discusses threats to validity — single seed,
supply-epoch switching, pool provenance, fork stochasticity, in-sample oracle
use, and format-layer noise from the substituted base model — and the
mechanism-level observations that fall out of the runs.
Chapter 8 concludes. Appendix A is the full deviation table; Appendix B
documents the reproduction infrastructure.
