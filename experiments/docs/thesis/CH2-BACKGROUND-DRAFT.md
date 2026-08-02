# Chapter 2 — Background and Related Work (safe-block draft)

> **Draft status.** This chapter contains only material that does not depend on
> pending user adjudications or on unarrived experimental data. The novelty
> positioning paragraph (§2.7) is deliberately left as a placeholder for the user
> to fill. Every citation and numeric claim has been cross-checked against six
> PDF-level deep-reads of the rival and compute-confound literature
> (`deepread/DR-A`…`DR-F`) and their errata; verbatim quotations and page-anchored
> numbers are sourced from those files, and secondhand (L0/L1) findings are omitted
> or hedged. Where a benchmark number is a system's own report it is labelled as
> such, and metric/split (pass@1 vs avg@3 vs best-of-k) is stated wherever two
> systems are compared.

---

## 2.1 Self-Evolving Agent Harnesses

An LLM *agent* is not only a language model: it is a model wrapped in a *harness* —
the scaffold of prompts, tool interfaces, control loops, retry policies, and
output contracts that turns a next-token predictor into a task-completing system.
A growing body of work observes that the harness, rather than the underlying
model weights, is often the dominant lever on end-task performance: holding the
base model fixed and changing only the harness has been reported to swing agent
success by a wide margin on the same benchmark. If the harness
is that consequential, then the harness itself becomes a natural object of
*automatic* optimization — an artifact to be searched over, mutated, and selected,
rather than hand-tuned once and frozen.

This motivates the *self-evolving harness*: a meta-level loop that repeatedly
inspects an agent's own failures, proposes edits to its harness configuration,
validates the edits, and ships the survivors, so that the agent's scaffolding
improves over successive rounds without human intervention. The central design
tension in this line of work is *durability*: an unconstrained edit-and-ship loop
tends to fix one failure while silently introducing another, producing a
"fix-one-break-one treadmill" whose net durable gain over many rounds approaches
zero. The representative system we reproduce and extend, HarnessX/AEGIS
(arXiv:2606.14249), is best understood as a direct attempt to defeat this
treadmill through a deterministic gate and a *variant pool*; we describe its
mechanism in detail below because our contribution is defined relative to it.

## 2.2 The HarnessX / AEGIS System (arXiv:2606.14249)

AEGIS is the meta-agent that evolves an agent's harness; HarnessX is the surrounding
evolution protocol and variant-pool machinery. We describe the mechanism as stated
in the paper (§4.1–§4.5, Algorithm 1, Tables 8–9), separating what the paper
specifies from what the open-source repository actually implements — a gap that is
itself a finding (§2.2.6) and the origin of the reproduction contribution in
Chapter 3.

### 2.2.1 The evolution loop: four meta-agent roles

Each evolution round is driven by a single meta-agent LLM playing four sequential
roles:

1. **Digester** — consumes the round's raw execution traces (on the order of
   millions of tokens) and compresses them into a per-task structured summary
   plus cross-round history.
2. **Planner** — constructs the *edit space* from the digest: which tasks are
   failing, what edits have already been tried, and which classes of harness edit
   remain unexplored.
3. **Evolver** — produces up to `K_t` candidate harness edits, each accompanied by
   a *change manifest* (see §2.2.2).
4. **Critic** — compares each candidate's manifest against its evidence, permits at
   most one revision, and emits a `ship_ranking` over the surviving candidates.

Before the roles run, the round *freezes routing*: every task is assigned to the
pool variant with the highest historical success rate, read only from prior-round
ledgers, so that within-round results cannot retroactively influence the routing
that produced them. Execution uses **pass@2** (a task counts as solved if at least
one of two rollouts succeeds; the paper gives the unbiased estimator in
Appendix A.3). The paper is explicit that evaluation uses the *entire* task set on
every round: "The full task set is evaluated every round (no subsampling)" (§6.1).

The first three roles may *short-circuit*: if the digested failure landscape
carries insufficient actionability (`a_t < α`) or the edit space is empty, the
round skips candidate generation entirely. The Critic and the gate, by contrast,
are mandatory — every candidate must pass them.

### 2.2.2 The deterministic gate: five stages

The paper's sharpest design decision is that the *ship* decision is deterministic,
not delegated to a judge. The paper (p.10) itself enumerates four deterministic
checks — manifest completeness, configuration normalization, build/smoke, and the
seesaw; the Level-2 round-trip is enforced not as a discrete listed check but through
the Evolver's `capability_evidence` (p.32) and the Critic's re-verification (p.37).
We reconstruct the gate as five ordered stages, inserting Level-2 as stage 4. A
candidate must pass these stages in order; failing any stage archives the candidate
with a recorded reason:

1. **Manifest completeness** — the change manifest must populate a structured
   schema (Table 9, p.36: `capability_evidence`, `file_changes`,
   `predicted_impact`, `attribution_signature`, and related fields).
2. **Config normalization** — the edited configuration must parse and normalize.
3. **Build / smoke** — the edited harness must build and pass a smoke run.
4. **Level-2 round-trip** — for tool-bucket edits, the candidate must supply
   evidence that a tool call's *return* actually reaches the model. The Evolver
   prompt (p.32) is emphatic that "a unit call that returns does not prove the
   agent sees the return" and rejects "I believe this will work" in favour of an
   attached verification output as `capability_evidence`; the Critic re-verifies
   Level-2 before shipping (p.37, the C-R10-02 worked example).
5. **Seesaw three-way decision** — described next.

### 2.2.3 The seesaw and the variant pool (§4.5)

Stage 5 is the pivot of the whole system. Rather than treating a candidate that
*improves some tasks while regressing others* as a rejection, the seesaw routes it
into one of three outcomes:

- **APPLY** — the candidate improves a subset of tasks and regresses none; it is
  merged into the target variant.
- **FORK** — the candidate improves one subset while regressing another (a "mixed
  conflict"). Instead of discarding it, the system *forks a new variant* that
  carries the improvement, leaving the parent variant to serve the tasks the edit
  would have broken. If the pool is already at capacity `K`, the
  lowest-performing variant is *retired* to make room.
- **REJECT** — anything else (notably: regressions with no compensating
  improvement) is archived.

The variant pool is thus a set of up to `K` harness configurations that
*specialize* over time: fork turns a conflict from a reason to reject into a reason
to *split*, so that "improvements to one cluster cannot regress another" (§4.5,
p.11). New tasks are routed to the variant with the best historical success rate
for their cluster; the pool accumulates a per-variant, per-cluster competence
profile. A monotone `ever_solved` ratchet — our implementation's term for the paper's
seesaw constraint that no previously solved task may be regressed (p.8, p.10) —
records tasks the system has *ever* solved, and the gate forbids regressing them.

The paper contains an internal tension about the *scope* of this
regression baseline that becomes visible only when `K > 1`: §4.1 (p.8) defines the
seesaw over the global task history `T_t`, whereas §4.5 (p.11) requires per-variant
isolation. The two readings disagree on whether one variant must avoid regressing a
task that a *different* variant solved. This tension is one of the audit findings
carried in Chapter 3 and Appendix A (M-23).

### 2.2.4 The K=1 degenerate case as the built-in control

A useful property falls directly out of the mechanism: when `K = 1` the pool holds
a single variant that can never fork, degenerating into a single-lineage evolution
loop — exactly the paper's **Global** control arm. When `K > 1` the pool can fork
and specialize — the **Ensemble** arm. Because the only difference is the pool
capacity, `K=1` versus `K>1` under otherwise identical code and data is the
cleanest possible controlled comparison of the variant-pool mechanism, and it
frames RQ1 of this thesis.

### 2.2.5 Protocol hyperparameters and the headline result

The evolution protocol (Table 8, p.29) fixes, among others: `K_t = 4` candidates
per round; GAIA `max-steps = 20`; meta-agent budget of 200 steps per role;
3 seeds per cell; and a **noise threshold** listed as "ignored single-round
pass-count delta ±5%". The main loop runs `for t = 0 … T-1` (Algorithm 1) with an
early-stop *patience*: after a number of consecutive rounds with no ship, evolution
halts.

The paper's headline claim is that the variant pool is what defeats the treadmill.
On GAIA (103 tasks × 15 rounds; three seeds per cell by the Table 8 protocol, though
Table 5's caption restates only "GAIA, GPT-5.4, AEGIS evolver, 15 rounds" and not the
seed count, cf. M-10), the Ensemble arm reaches **87.4%** while the Global arm reaches
only **49.5%** — a **+37.9pp** gap (our arithmetic: the difference between the two
reported finals, not a figure the paper prints) attributed entirely to the
persistent-pool-plus-cluster-routing layer. Notably, the pool advantage is not bought
with extra compute: Table 5 (p.17) reports the Ensemble arm consuming 107.8M tokens
against the Global arm's 143.7M, so the pool wins by that margin while spending
roughly 25% *fewer* tokens. The Global arm is reported to peak early and then degrade
in sustained fashion; its final 49.5% falls *below* both its own strong starting
harness and its own peak, which coincide at 73.8% (Table 4 reports Initial = 73.8% for
the GPT-5.4 configuration; p.17 notes the Global arm peaks early at R4, also 73.8%).
The paper's own column labels the −24.3pp gap "Final −Peak" — catastrophic forgetting
relative to the arm's own peak; because peak and Initial coincide here, the number is
the same read either way. The paper's own Fig. 4 annotations characterize the Global
trajectory as a "fix-one-break-one" treadmill whose net durable gain over nine rounds
is "≈0, all inside noise", and the paper uses an *identical-config replay* as a noise
probe to distinguish real gains from sampling fluctuation.

Two further specification ambiguities matter for reproduction and are documented in
Appendix A: Algorithm 1 (L21–25) reads as *single-ship* (take the first candidate
that passes the gate), whereas Appendix B.1 (p.34) prescribes shipping *all*
bucket-disjoint candidates in `ship_ranking` order (*multi-ship*) — the two cannot
both be the operational semantics (M-17).

### 2.2.6 The open-source gap

The paper's strongest claim — the §4.5 variant pool and Ensemble routing — is *not
present in the official open-source repository*: the repo ships a single monolithic
meta-agent with no variant pool, no cluster routing, no routing freeze, no
fork/retire, one candidate per round rather than `K_t = 4`, its own journal-format
manifest rather than the Table 9 schema, and no pass@2 estimator. The paper's most
consequential result therefore has no publicly runnable implementation. This is
simultaneously a reproduction risk (the headline is independently unverified) and
the research opening this thesis takes: an independent reimplementation and
systematic audit of the §4.5 layer, followed by a bounded extension on top of it.

## 2.3 Task Decomposition for LLM Agents

The improvement direction of this thesis adds a *task-decomposition* layer above the
variant pool, so we situate it in the decomposition literature.

**Taxonomy anchors.** The canonical survey of LLM-agent planning (arXiv:2402.02716)
partitions planning into task decomposition, multi-plan selection,
external-planner-aided, reflection, and memory, and splits decomposition into
*decomposition-first* versus *interleaved*. A 2026 architectures survey
(arXiv:2601.12560) organizes reasoning topologies as linear (CoT), interleaved
(ReAct), tree (ToT), hierarchical (recursive sub-agents), and inference-time. Our
incumbent decomposition scheme sits at *decomposition-first, hierarchical,
typed-slot, training-free, single-planner*.

**Training-free decomposition off GAIA.** Several recent methods are simple,
training-free, and cheap, but validated off GAIA-level web/tool benchmarks. TDP
(Task-Decoupled Planning, arXiv:2601.07577) builds a DAG of sub-goals with
node-scoped context and reports up to −82% tokens on TravelPlanner/HotpotQA/
ScienceWorld. AdaptOrch (arXiv:2602.16873) computes DAG width/depth/coupling and
threshold-routes to an orchestration *pattern*, beating static baselines by
+6.9–9.8pp on SWE-bench/GPQA/HotpotQA at roughly half the tokens. E3
(arXiv:2607.13034) estimates task difficulty and picks a minimum-viable scope,
cutting cost ~85% at equal success on a coding file-edit bench. A consistent theme
across this bucket is that the demonstrated benefit is *cost/token efficiency at
roughly equal accuracy*, not an accuracy jump, and none reports a GAIA/WebArena/
BrowseComp number. RSTD (Runtime-Structured Task Decomposition, arXiv:2605.15425)
sharpens the point on agentic-coding workloads: it finds that "decomposition
structure alone does not reliably reduce retry cost" — static decomposition in fact
costs *more* retry tokens than a monolithic baseline — with end-task correctness
tied at 100% across monolithic, static, and runtime-structured configurations.
Where decomposition pays in this literature it pays in tokens, and even that payoff
is conditional.

**GAIA-level decomposition — full systems or trained, with single-vs-multi
ablations pointing the same way.** The papers that *do* report GAIA numbers are
whole frameworks and/or trained, and where they isolate decomposition it is not the
lever. JoyAgent-JDGenie (arXiv:2510.00510; Claude-4-sonnet; GAIA validation pass@1)
reaches 75.2% only in its *Fusion* configuration; its own Table 2 ablation shows the
best pure multi-agent decomposition arm (Multiple(3) = Plan+Retrieval+Logic, 70.3)
is *worse* than a single ReAct agent (71.5), even though neither arm is
compute-starved — the single agent was given extra execution steps and the
decomposition arm extra agents — and the gain appears only once the single and
decomposition trajectories are fused by a Critic posterior vote. MiroFlow
(arXiv:2602.22808; GPT-5; GAIA-Val-Text 103 text-only; avg@3) shows the same
direction, a single-agent 74.8 versus 71.9 for its main–sub decomposition; but this
is *GAIA-specific* — the same paper's multi-agent arm wins on BrowseComp-200 (68.3
vs 63.9) and HLE-200 (42.0 vs 40.6), and the authors attribute the GAIA loss to its
"strongly sequential task structure", in which multi-agent decomposition "increases
the risk of mistake propagation across sub-agents". MiroFlow therefore supports
"decomposition hurts on GAIA's sequential tasks", not a blanket claim, and its
decomposition arm issues *more* agent calls yet still loses on GAIA. AgentOrchestra
(arXiv:2506.12508, v6) reports GAIA Test 89.04% (as reported; gemini-3-flash-preview), but
its cumulative ablation (Table 5) never removes the planner — it adds specialized
executor sub-agents on top of a planner that is always present. Planner/
decomposition alone scores 36.54; all of the uplift comes from added executors (the
Deep Researcher is the largest single jump, 36.54→57.14; the Tool Generator is the
largest late-stage jump, 79.07→89.04). Decomposition is not the lever.
Uno-Orchestra (arXiv:2605.05007) emits exactly the typed `(model, primitive)`
routing pair per subtask that a division-of-labor design wants, but under its own
unified nine-worker reproduction harness it *loses* on GAIA accuracy — pass@1 82.0
versus its controlled reproduction of AgentOrchestra at 83.4 (and pass@2 87.0 vs
88.7) — while winning on cost (~5× cheaper on GAIA, ~12× on its 13-benchmark macro
average). It requires two-stage training (SFT on 61k trajectories plus agentic
GRPO), disqualifying it from a training-free setting.

Taken together these are the recognized negative result that motivates this thesis's
improvement question: **on GAIA, adding decomposition or multi-agent structure has
repeatedly failed to beat a strong single agent despite spending equal-or-greater
compute** (JoyAgent 71.5 > 70.3; MiroFlow 74.8 > 71.9, GAIA-only; AgentOrchestra
planner-only = 36.54; cf. the compute-confound evidence of §2.6). This is what makes
the question open and worth asking — it is why one cannot assume in advance that
adding decomposition will help — rather than a claim this thesis sets out to
overturn; the thesis's own comparison is internal, made against the reproduced pool
of Chapter 3. The compute-confound behind that pattern is developed in §2.6, and §2.7
positions this thesis within the surrounding landscape.

**The closest neighbor.** AOrchestra (arXiv:2602.03786) decomposes adaptively and,
for each subtask, instantiates a fresh `(Instruction, Context, Tools, Model)`
executor on-the-fly, reporting GAIA validation 80.0% pass@1 (Gemini-3-Flash as both
orchestrator and sub-agent) in a *training-free* setting. PDF-level reading of its
method confirms the executors are instantiated-and-discarded: an appendix
container-lifecycle instruction states that "Each SubAgent runs in a FRESH
container - if you delegate_task again, the previous work will be lost", and a
full-text persistence scan finds no pool, registry, gate, or fork/retire — the only
cross-task artifacts are *offline* ones (SFT weights or an ICL-optimized prompt for
the orchestrator). Its optional supervised fine-tuning does *not* raise the 80.0
headline; it only rescues a weak Qwen3-8B orchestrator (56.97 → 68.48). The
differentiator from this thesis therefore cannot be "we do not train" — AOrchestra
also ships a training-free mode — but the presence of a persistent, statistically
routed, self-evolved executor pool, which AOrchestra does not have.

## 2.4 Division of Labor and Routing

A second body of work routes subtasks (or whole queries) to specialized executors.

Capability-aware decomposition has a clean precedent in AOP (Agent-Oriented
Planning, arXiv:2410.02189, ICLR 2025), which decomposes a query into subtasks and
allocates each to a capable agent under solvability/completeness/non-redundancy
constraints — but conditions on *static, hand-written* agent descriptions. Topaz
(arXiv:2604.03527) takes *already-decomposed* subtasks and routes each to a fixed
five-model roster using per-skill profiles drawn from *static* third-party
benchmarks (decompose-*then*-route, no runtime update). FlyRoute
(arXiv:2605.22057) does evolve its capability profiles from a data flywheel, but
routes *whole queries* to a *fixed* set of expert agents with *no* decomposition.
Adjacent evolving-orchestration work includes HERA (arXiv:2604.00901, evolving
orchestration plus fixed-role prompts, whole-query topologies), EvolveRouter
(arXiv:2604.05149, co-evolving graph routing and prompts over a *fixed pool of
agents without improving the agents themselves*), TASER (arXiv:2606.21307,
task-differentiated skill expansion and routing over continual-learning skill
modules), and AgentFactory (arXiv:2603.18000, a persistent, self-refining library
of executable subagents but *without* task decomposition or gated routing).
MonoScale (arXiv:2601.23219) formalizes safe pool expansion and routing memory as a
contextual bandit with a monotone-improvement guarantee, but has no decomposition
and no harness-config evolution.

**Cross-domain precedent for the ingredient.** The specific ingredient this thesis
combines — typed decomposition feeding routing to heterogeneous executors — is not
itself new: it is routine in the GUI-agent domain, where it has been built and
*ablated*. Agent S2 (arXiv:2504.00906) routes each generated action through a
Mixture-of-Grounding gate to one of three fixed, prebuilt grounding experts
(visual/textual/structural), and isolates that mechanism at +3.08/+4.61pp
(15-/50-step) in its own OSWorld ablation — its larger +18.9/+32.7% figures are
full-system-versus-baseline, not the isolated mechanism. UFO2 (arXiv:2504.14603)
decomposes into a dependency-ordered subtask graph and routes by application
identity to per-app expert agents; Agentic Lybic (arXiv:2509.11067) routes DAG
nodes to three fixed functional roles (Operator/Technician/Analyst), reaching
57.07% at 50 steps on OSWorld. In all three the executor set is *fixed and prebuilt*
(swappable but not grown by selection), and the benchmark is GUI control rather than
GAIA. They establish the decompose-then-route pattern as prior art; what none has is
an executor pool that a self-evolution loop grows and specializes.

The most relevant contemporaneous system is Leni (arXiv:2607.17044), a production
agent that routes across heterogeneous, lightweight *post-trained* specialist models
(0.5–4B, distillation SFT plus task-specific RL) via a planner, reaching GAIA
validation 75.2% pass@1 (83.0% best-of-k, n=165, text-only). Its central
methodological contribution is a *decomposition of the observed uplift*: most of it
comes from scaffolding, routing, and specialist models rather than from the
verification step itself. Notably, Leni reports a *clean* component confusion matrix
only on SpreadsheetBench, where deterministic ground truth exists and the verification
step's isolated contribution is a small +1.5 points, and explicitly downgrades its
GAIA component figures to "indicative only" — a separation this thesis inherits for its
own attribution. Leni is both the current GAIA planner-plus-specialist anchor and a
template for component-level attribution, but its specialists are *post-trained*
base models, not a training-free, seesaw-gated pool of harness configurations; it
also discloses only serving-cost multipliers, not training cost, so a like-for-like
cost comparison is not possible from the paper.

Across §2.3–§2.4 the landscape splits cleanly: the simple/cheap/training-free
methods drop typed routing and skip GAIA, while the GAIA-level typed-routing methods
require training or ship a full framework, and where decomposition is ablated it is
typically *not* the lever. No single system routes decomposed subtasks to a pool
that a harness self-evolution loop *itself grew and specialized* (seesaw-gated
fork/retire), training-free, on GAIA — the conjunction that §2.7 positions this
thesis against.

## 2.5 Credit Assignment without Subtask Ground Truth

An extension that routes subtasks to variants must, to explain *why* it works,
attribute end-task success to per-(variant, subtask-type) decisions — without
ground-truth labels for individual subtasks. Several primary results shape the safe
design space.

Removal-based attribution (arXiv:2605.27621) formalizes agent attribution as a
cooperative game and finds that "Leave-One-Out (LOO) identifies bottleneck agents as
effectively as combinatorial methods, but at a fraction of the computational cost"
(a 3.3×–7.6× token reduction, Table 1), and — the on-point result for any scheme
that would use a judge to *simulate* ablation — that "agent ablation isolates
structural bottlenecks, whereas introspective LLM judges fail to faithfully
approximate this behavior" (Table 2: the best judge reaches only R²=0.42/ρ=0.63,
with *negative* R² in centralized and hybrid topologies). Its *model-replacement* protocol —
substituting an agent's backbone while keeping its role instantiated — is the exact
shape of a subtask-slot variant swap, and its LOO coalition costs n+1
re-evaluations.

A separate false-success study (arXiv:2606.09863) reports that no LLM-judge
configuration across 5 judge models and 5 prompt strategies — even one given the
full task specification — exceeds AUROC 0.65 for detecting false success on
tau2-bench (Table 5); "judges do not lack information, they fail to use it", whereas
a lightweight deterministic TF-IDF detector reaches 0.83/0.95 AUROC at roughly
3,300× lower latency, paralleling a deterministic end-task gate. Two scope caveats
are load-bearing. First, 2606.09863 measures *final-state verification* judges
against programmatic ground truth (tau2/AppWorld), not subtask judges; that its
ceiling implies GAIA-*subtask* judges are unreliable is *our a-fortiori inference* —
if a judge cannot verify the final state even with the full task specification,
finer per-subtask credit without any ground truth is even less justified — not a
result the paper reports. Second, reference-free plan-quality judging is *not* an
open gap: GPA (arXiv:2510.08847) already provides a factorized reference-free
LLM-judge suite for goal–plan–action alignment, though its own judge consistency was
only improved "by up to 38%" through evolutionary rubric refinement. Together with
the deterministic-detector evidence, these motivate deliberately *sidestepping*
subtask-level judges in favor of a deterministic end-task gate.

Deriving label-free credit from rollouts is a *different regime* rather than merely
a costlier one. TreeMem (arXiv:2605.04811) obtains agent-specific credit via
Monte-Carlo averaging over sampled branch rollouts inside an RL/GRPO training loop
with a reward model; it self-reports only "marginal per-step overhead" and "no extra
rollout branches" at inference, so the honest contrast is *training-dependent versus
training-free*, not expensive versus cheap. Reference-free *trajectory* evaluation
also exists (TRACE, arXiv:2510.02837) but targets efficiency/hallucination/
adaptivity of trajectories rather than the quality of a decomposition *plan*. These
findings jointly point toward observational credit read from an already-computed
deterministic gate at *zero additional rollout cost*, with leave-one-slot-out
counterfactuals — the n+1 model-replacement protocol above — reserved only for
calibration. (Only the observational ledger is zero-additional-rollout; the LOO
calibration genuinely costs n+1 replays and is not itself described as zero-rollout.)

> The specific credit-assignment instrument, arm set, and headline contrast for the
> improvement experiments are design decisions reserved for Chapters 4–5 and are
> **not** committed here. See [ADJUDICATION-PENDING: 裁 C — headline framing].

## 2.6 Compute Confounds in Multi-Agent Evaluation

A recurring threat to any "more structure helps" claim is that the structure simply
buys more compute, so the evidence must separate motivation from token-matched
results.

The motivating observation is that automatic multi-agent systems are expensive
without a commensurate return: arXiv:2606.13003 reports that they "consistently
underperform CoT-SC despite being up to 10× more expensive", faulting evaluations
that ignore "the marginal utility of increased computational cost", and it includes
a web-style benchmark (BrowseComp-Plus) on which a 5-sample CoT-SC beats every
multi-agent system it tests. This study does *not* itself match budgets — its 10× is
an *as-deployed* cost gap and its web-bench margins are small (1.19–2.38pp) — so it
anchors *motivation* (architectural overhead; the existence of a web bench where a
single agent wins), not a token-matched conclusion.

The token-matched evidence comes from two other primary sources. Under equal
*thinking-token* budgets and with 95% bootstrap confidence intervals, arXiv:2604.02460
finds that a single-agent system "is the best-performing system or statistically
indistinguishable from the best for all budgets except the lowest one" on text-only
multi-hop reasoning; its information-theoretic argument also predicts that
decomposition becomes competitive only when a single agent's context utilization
*degrades* — a heavily-sequential regime that GAIA's long-horizon tasks may enter,
a favorable prior for the improvement question here (the authors flag tool/web
settings as out of scope). Closest to this thesis's setting, arXiv:2606.15017
studies *web agents* under a token-matched budget and finds that "a token-matched
vanilla actor matches or surpasses AWM, ASI, and ReasoningBank in aggregate success
rate while often using fewer total tokens", by spending the extra budget on a longer
interaction horizon rather than on extra modules.

Two further results frame why compute accounting matters *here* specifically.
OneFlow (arXiv:2601.12307) defines a *homogeneous* workflow as one in which "all
agents share the same base LLM and differ only in their system prompts, tools, and
positions", and shows that a single LLM can match such workflows because it "reuses
the KV cache"; its remark that "single-LLM methods cannot capture heterogeneous
workflows due to the lack of KV cache sharing across different LLMs" applies only to
systems that *swap the base model*. Because a pool of harness-config variants over a
*single* base model is exactly a homogeneous workflow in this sense, it falls inside
the range a single agent can in principle absorb — so a division-of-labor gain
cannot be claimed via heterogeneity, and any such gain must be read against what it
costs rather than asserted from structure alone. Independently, a compute-controlled
scaling study (arXiv:2512.08296) reports correlationally
(cross-validated R²=0.373) that "architecture-task alignment, not number of agents,
determines collaborative success", with the same coordination structure helping on
one benchmark (Finance-Agent, Centralized, +80.8%) and hurting on another
(PlanCraft, Independent, −70.0%); this supports treating structure *alignment*, not
agent count, as the operative variable, while remaining correlational and offering
no formal decomposable-versus-sequential taxonomy. Taken together, these results
establish that compute accounting is a real confound; we therefore report each
arm's full cost and give the headline reading both raw and cost-normalised, without
claiming to equalise budgets. The experimental design carries no separate
single-agent baseline arm; what it does contain, and what that omission costs, is
set out in Chapters 4 and 7.

## 2.7 Positioning of This Work

The reproduction-and-audit contribution (Chapter 3) is positioned by §2.2.6: an
independent implementation and systematic audit of a §4.5 variant-pool / Ensemble
routing layer that has no public implementation. The improvement contribution
(Chapters 4–5) is positioned by the landscape of §2.3–§2.4: prior decompose-and-route
agents either instantiate a fresh, stateless executor per subtask and discard it
(AOrchestra, arXiv:2602.03786), or route to post-trained specialist models
(Uno-Orchestra arXiv:2605.05007; Leni arXiv:2607.17044); persistent self-improving
executor libraries exist but without task decomposition or gated routing
(AgentFactory arXiv:2603.18000); and evolving-profile routers act on whole queries
without decomposition (FlyRoute arXiv:2605.22057). The four-way conjunction —
decomposition × subtask-granularity routing × a *self-evolved, seesaw-gated* pool ×
training-free harness-config variants on GAIA — is unoccupied, with each neighbor
holding an adjacent cell.

> [PLACEHOLDER: novelty statement — awaiting user pick among
> NOVELTY-EXPDESIGN-RESEARCH.md Part D candidates 1-3]

> [Note (2026-07-31): the answer space changed — the thesis no longer frames its
> novelty around equalising compute across arms; the anchor is now the *evolved
> persistent pool* axis plus the reproduction/audit layer.]

*(The three candidate framings on file are, in decreasing defensibility: (1) an
open empirical-question framing — can a training-free, seesaw-gated evolving pool
of harness-config variants, routed at subtask granularity, match or beat both
fresh-spawn and post-trained-specialist routing on GAIA;
(2) a method sub-contribution framing — observational subtask-type credit from a
deterministic end-task gate at zero additional rollout cost, with leave-one-slot-out
only for calibration; (3) a conjunction "first" framing with a to-our-knowledge
hedge. The choice among them is the user's and is not made in this draft.)*
