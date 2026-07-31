# DR-G — "Plan → step-wise execute → per-step executor-pool selection": naming, lineage, placeholder

> **Method**: arXiv PDFs downloaded and text-extracted locally with pypdf 6.14.2 (no
> WebFetch summarizer in the load-bearing loop). Every load-bearing quote carries a
> page/line anchor located in the extracted text. Hyphenation artifacts normalized when
> quoting. WebSearch used only to *find* candidates; two summarizer claims were caught
> as hallucinations and discarded (see §9).
> **Date**: 2026-07-31 · researcher (Opus 4.8) · under `research-guardrails` (enforce).
> **Anti-duplication**: builds ON TOP of `DECOMP-METHOD-CATALOG.md` (45-system census,
> Jul-30 + Jul-31 ERRATA) and `deepread/DR-D-RIVALS-PDF.md`; those are not re-derived.
> This doc adds the *pattern-naming / ReAct-boundary / static-vs-replan / pool-persistence*
> lens the catalog did not organize around.
> **Depth tags**: L0 secondhand · L1 abstract · L2 method skim · L3 method careful · S5 verbatim in hand.

---

## 0. The pattern under test (user's words, formalized into 4 features)

> "A planning agent first produces a plan, then executes step-by-step; each step (subtask)
> is dispatched to an executor pool, and the most suitable executor is chosen for that step."

| # | Feature | Definition |
|---|---|---|
| **F1** | planner ≠ executor | a distinct planner role produces the plan; a *different* role/agent executes |
| **F2** | step-wise, feedback-carrying | steps run in sequence; step *k+1* can see step *k*'s result |
| **F3** | per-step executor **selection** | for each step, *pick the best executor from a pool* (not one fixed executor, not a fresh throwaway) |
| **F4** | in-execution replanning | (optional) the plan may be revised after seeing execution results |

---

## 1. TL;DR verdicts (the four asked deliverables)

1. **Name.** There is **no single canonical name** that carries all four features. The pattern is
   **"plan-and-execute" (F1+F2+F4) fused with HuggingGPT-style per-step executor selection (F3)** —
   i.e. an **orchestrator-worker** system whose orchestrator performs **per-subtask capability-aware
   routing** over a worker pool. The F3 operation's canonical ancestor is **HuggingGPT's "Model
   Selection" stage** (2303.17580); its formalization in the routing literature is **"agent-oriented
   planning" / decompose-then-route** (AOP 2410.02189, Topaz 2604.03527). The modern GAIA-family
   instantiations that already have all four are **Magentic-One / AgentOrchestra / Workforce / EvoMAS /
   Leni** — every one with a **fixed or trained** pool.

2. **Static vs replanning (the design question) — CONTESTED / regime-dependent, NOT a clean "replan wins".**
   - *Trained planner-executor*: adding replanning **helps** — Plan-and-Act **+10.3 pts** (43.63→53.94)
     on WebArena; Magentic-One loses **−31%** on GAIA when its plan/progress ledgers are removed.
   - *Training-free, controlled*: **static often BEATS dynamic** — PLANAHEAD (WebArena, 158 hard tasks,
     3 backbones) reports *"Static Planning Often Outperforms Dynamic Single-Agent Planning"* because
     naive dynamic agents "enter action loops, lose track of task progress, or prematurely mark
     subtasks as completed."
   - **There is no clean training-free head-to-head of *static planner-executor* vs *planner-executor-
     with-replan* on GAIA.** Net: replanning is a **conditional** win (pays off when execution reveals
     info that was unknowable at plan time; costs accuracy via loops when added naively/untrained).

3. **Placeholder — the full conjunction is CLEAR (open); every *half* is OCCUPIED.**
   Our cell = *training-free × per-step decomposition-routing × to a **self-evolved persistent** harness-
   config pool × GAIA-web*. No system occupies the conjunction. The closest new near-neighbor is
   **EvoMAS 2605.08769** (PARTIAL: per-stage workflow selection over a pool on GAIA — but **RL-trained**
   and the pool is a **fixed candidate set**). Defensible contribution is the **conjunction + the
   empirical head-to-head**, not the architecture primitive (which is old — HuggingGPT 2023).

4. **Recommendation: KEEP the static one-shot plan; do NOT adopt in-execution replanning as the headline.**
   Reasons in §8. Short version: (a) replanning's accuracy payoff is unproven in the *training-free*
   regime and the one controlled training-free study tilts *against* it; (b) replanning breaks
   cross-arm replay (the plan becomes a function of execution results, which differ per arm) —
   fatal for your matched-budget variant-pool comparison; (c) cost is 2–3× tokens (§7). Add
   **bounded on-failure re-decomposition** as an explicit backlog arm, not the main claim.

---

## 2. Naming & lineage — the four-feature matrix

Legend: ✓ present · ✗ absent · ~ partial/weak. Anchors are page/line in the extracted PDF.

| Candidate (arXiv) | F1 planner≠exec | F2 step-wise+feedback | F3 per-step pool-select | F4 replan | What it actually is |
|---|:---:|:---:|:---:|:---:|---|
| **ReAct** 2210.03629 | ✗ | ✓ | ✗ | ~ | single agent interleaving thought/action/obs; "plan" is implicit inside one LLM |
| **Plan-and-Solve** 2305.04091 | ✗ | ~ | ✗ | ✗ | a **zero-shot prompting** trick ("devise a plan… carry out step by step"); one LLM, one call |
| **Plan-and-Execute** (LangChain) / **Plan-and-Act** 2503.09572 | ✓ | ✓ | ~ | ✓ | separate planner + **one** executor agent (tool-select inside it); replan supported |
| **LLM-Compiler** 2312.04511 | ✓ | ✗ | ~ | ✓ | planner emits DAG → **parallel** executor + joiner-replan; built to *avoid* sequential |
| **HuggingGPT** 2303.17580 | ✓ | ~ | **✓✓** | ✗ | plan → **Model Selection (per task, top-K from HF hub)** → execute → respond |
| **Orchestrator-Worker** / **Magentic-One** 2411.04468 | ✓ | ✓ | ~ | ✓ | Orchestrator + ledgers; per-step "selects an appropriate agent" from **fixed 4–5 roles** |
| **Router-MAS** / **AOP** 2410.02189, **Topaz** 2604.03527 | ✓ | ~ | **✓✓** | ✓ | decompose → capability-match each subtask to a **statically-described** agent set |
| **← our target pattern** | ✓ | ✓ | ✓ | ~(opt) | = Plan-and-Execute skeleton **+** HuggingGPT/AOP per-step executor selection |

**Reading of the matrix.** The target pattern is a *hybrid* whose two halves come from two different
lineages: the **plan/execute split + stepwise + replan** half is the **Plan-and-Execute** lineage
(ReAct → Plan-and-Solve → Plan-and-Execute → orchestrator-worker); the **per-step choose-best-executor-
from-a-pool** half is the **HuggingGPT "model selection"** lineage, later formalized as **capability-aware
routing / agent-oriented planning**. No canonical single term unifies them; in practice authors call the
whole thing **"orchestrator-worker with (per-step) routing"** or **"plan-and-execute with expert
delegation."**

### Verbatim anchors (load-bearing)
- **ReAct** = single agent, no separate planner (2210.03629 p.2 L173-177): *"ReAct prompts LLMs to
  generate both verbal reasoning traces and actions … in an interleaved manner, which allows the model
  to perform dynamic reasoning to **create, maintain, and adjust high-level plans** for acting (reason
  to act), while also interact with the external environments … (act to reason)."* The plan lives
  **inside one model's reasoning trace** — there is no planner agent and no executor pool.
- **Plan-and-Solve** = prompting, not architecture (2305.04091 title L3 + L28-40): *"Plan-and-Solve (PS)
  Prompting … zero-shot prompting consistently outperforms Zero-shot-CoT."* One LLM completion.
- **Plan-and-Execute** (LangChain, canonical framing, L1/web): planner "generate[s] a multi-step plan";
  executor(s) "accept the user query and a step in the plan and invoke one or more tools"; after
  execution "the agent is called again with a re-planning prompt." Inspired by BabyAGI + Plan-and-Solve.
  Note: the *reference* implementation has **one** executor (tool-selection lives inside it), so F3 is
  weak unless you add a router.
- **LLM-Compiler** = parallel, not stepwise (2312.04511 L27, L84, L181-185): planner + "**Task Fetching
  Unit**" + "an Executor, executing these tasks **in parallel**"; supports "dynamic re-[planning] …
  repeated replanning based on the intermediate results." The design's *purpose* is to break ReAct's
  "sequential function calling," so F2 (step-k+1 sees step-k) is deliberately *not* the model.
- **HuggingGPT** = the F3 ancestor (2303.17580 §3.2 L362-378): *"selecting the most appropriate model
  for each task … a dynamic in-context task-model assignment mechanism … we first filter out models
  based on their task type … rank them based on the number of downloads … and then select the top-K
  models as the candidates."* This **is** "per-step pick-best-executor-from-a-pool," 2023 vintage.
- **Magentic-One** = orchestrator-worker + per-step role pick + replan (2411.04468 L289): *"Once this
  initial plan is formed, the Orchestrator then selects an appropriate [agent]"*; L59-64: Orchestrator
  "plans, tracks progress … and **re-plans to recover from errors**." Pool = fixed WebSurfer/FileSurfer/
  Coder/Terminal.
- **AOP** = the F3 selection formalized over a *static* pool (2410.02189 L16-75, L130): decompose into
  "sub-tasks that can be allocated to **suitable agents capable of solving them**"; a "**reward model**
  … evaluate[s] the solvability of sub-tasks … some sub-tasks … require re-planning"; agents are a
  fixed described set *D = {d₁,…,dₙ}*.

---

## 3. The precise ReAct boundary (Q2)

**Confirmed from primary source.** ReAct (2210.03629) is a **single agent** that **interleaves**
reasoning and acting *within one LLM's generation*. Its "plan" is emergent ("create, maintain, and
adjust high-level plans", p.2 L175-176) — it has **no independent planner role, no executor pool, and
no executor-selection step.** A trajectory is "multiple thought-action-observation steps" (p.3 L232),
all produced by the same policy.

**What the target pattern adds over ReAct — three things ReAct structurally lacks:**
1. **A separated planner (F1).** The plan is a first-class object emitted by a *different* role, not
   a side effect of the executor's own chain-of-thought.
2. **A per-step executor-selection operator (F3).** ReAct has exactly one actor; the target pattern
   *chooses* an executor per subtask from a pool (HuggingGPT's "model selection", AOP's capability
   assignment). This is the single biggest departure and the one ReAct cannot express.
3. **An explicit plan-state to replan against (F4, optional).** ReAct "adjusts plans" implicitly in
   text; the target pattern (if it replans) revises a structured plan object (Magentic-One's Task
   Ledger; Plan-and-Act's regenerated plan).

**Caveat for honesty:** ReAct *does* perform "dynamic reasoning" over observations, so F2 (feedback)
is genuinely present in ReAct — the boundary is **not** "ReAct is static." The boundary is **role
separation + executor selection**, not the presence of feedback.

---

## 4. Static plan vs in-execution replanning (Q3) — the design-critical evidence

### 4A. Direct/near-direct ablations, with numbers and provenance

| Source (depth) | Benchmark | Comparison | Result | Direction |
|---|---|---|---|---|
| **Plan-and-Act** 2503.09572 (S5, PDF Table p.15-16) | WebArena-lite (avg over sites) | planner-exec **static** (row "+Targeted Aug") vs **+Dynamic Replanning** (next row), *same trained planner+executor* | Base-exec **29.63→44.24**; best-exec **43.63→53.94 (+10.31 pt)** | **replan HELPS** — but system is **trained** (finetuned planner+executor) |
| **Magentic-One** 2411.04468 (S5, p. L658-662) | GAIA-val (GPT-4o) | full system vs **remove both ledgers** (plan + progress/replan state) | **−31%**; remove any worker −21%(Coder)…−39%(FileSurfer) | plan/replan **state load-bearing** |
| **PLANAHEAD** 2605.29927 (S5, Table 1 p.4) | WebArena, 158 hard tasks, 3 MLLM backbones, N=5 | **static planner-executor** vs **dynamic single-agent** planning, *training-free, identical conditions* | *"**Static Planning Often Outperforms Dynamic** Single-Agent Planning"*; dynamic agents "enter action loops, lose track of task progress, or prematurely mark subtasks as completed" | **static ≥ dynamic** in the training-free regime |
| **Plan-and-Act §3.3** 2503.09572 (S5, p. L305-320) | (motivation) | why static fails | *"static plans are unequipped to handle dynamic content interpretation … content that is unknown a priori at planning time … the EXECUTOR may blindly follow the steps … rather than trying a different approach"* | names the **exact** failure static must mitigate |

### 4B. Why these do not actually contradict — the reconciling reading
The two "replan helps" results and the one "static wins" result are measuring **different things**:
- Plan-and-Act compares *planner-exec-without-replan* vs *planner-exec-**with**-replan* — **same, trained**
  system, replan added → +10 pt. Replanning **on top of a disciplined planner** helps.
- PLANAHEAD compares *static planner-exec* vs *dynamic **single-agent**-replanner* — **training-free**;
  the "dynamic" arm is a ReAct-like self-replanner, and it **loses to static** because it loops/loses
  track. Naive replanning **without a disciplined planner** hurts.
- Magentic-One shows the *plan/replan state* (ledgers) is load-bearing on GAIA — but that is a "remove
  the whole planning apparatus" ablation, not "static vs dynamic plan."

**Net, guardrails-honest:** on GAIA-family multi-step web tasks, **replanning is a conditional win, not a
default.** It pays when (i) the system is competent enough not to loop and (ii) execution genuinely
surfaces info unknowable at plan time. In the **training-free** regime — *our* regime — the only
controlled study on the table (PLANAHEAD) finds **static planner-executor competitive with or better
than dynamic**. Corroborating cost-side nuance: 2605.08477 ("Do Agents Need to Plan Step-by-Step?")
finds *full-horizon (upfront) plan + **lazy** on-demand replan achieves **accuracy parity** with eager
step-by-step at **2–3× fewer tokens**"* (on data-centric tool-calling, not GAIA) — i.e. eager per-step
replanning buys little accuracy for a large token bill.

### 4C. What is missing (open question)
No paper runs the **exact** experiment we care about: *training-free static planner-executor* vs
*training-free planner-executor-with-replan* **on GAIA-web, matched budget, multi-seed CIs.** Every
"replan helps" number is either trained (Plan-and-Act) or an apparatus-removal (Magentic-One); the one
training-free controlled static-vs-dynamic study is on WebArena and its "dynamic" arm is single-agent,
not planner-executor. **This gap is itself a defensible mini-contribution if you run it.**

---

## 5. "Per-step choose-best-from-pool", and the pool-**persistence** axis (Q4)

The F3 operator (per-step executor selection) is **common**. The differentiator our proposition rides on
is **what kind of pool** — a throwaway, a fixed pool, a fixed-but-persistent pool, or a **self-evolving**
one. Three tiers, from the corpus:

### Tier A — fresh executor per step (NOT a pool) — the thing we are NOT
- **AOrchestra** 2602.03786 (S5, DR-D): `Delegate(Φ)` "instantiates an executor"; Terminal-Bench prompt
  (p.12 §B.1.2): *"Each SubAgent runs in a **FRESH container** — if you delegate_task again, the previous
  work will be lost."* No pool, no reuse.

### Tier B — per-step selection over a **fixed / persistent-but-static** pool (F3 present, no evolution)
- **HuggingGPT** 2303.17580 — per-task selection from the **HF hub** (persistent, cross-task, **static**;
  models don't change based on task outcomes). The canonical persistent-pool router.
- **AOP** 2410.02189 — capability-match subtask → agent from static descriptions *D={d₁…dₙ}* + reward-model
  solvability gate + replan-on-unsolvable. Closest *mechanism* to ours; pool is **static**.
- **Topaz** 2604.03527 (L2, catalog) — decompose-then-route to a **fixed 5-model roster** by static
  per-skill benchmark profiles.
- **Magentic-One** 2411.04468 — per-step pick from **fixed 4–5 roles**.
- **OWL / Workforce** 2505.23885 (L2/W, catalog) — one-shot plan + recursive re-decomp on failure to a
  **persistent domain-worker pool**; coordinator assigns. Persistent but **domain-fixed**, and its
  headline +16.37% is a *training* gain, not a pool-evolution gain.
- **EvoMAS** 2605.08769 (**L2, S5 anchors this session**) — per-stage workflow selection over an agent
  pool on **GAIA**, but a "**learned Workflow Adapter … trained with policy gradients / REINFORCE**"
  (L24, L82, L258-267) that "constructs 3-layer workflows from a **fixed candidate agent pool**"
  (L149, L276-277). **RL-trained, fixed pool.** ← new near-neighbor the catalog lacked.
- **Leni** 2607.17044 (S5 this session) — routes each step "to the lightest model that can perform it
  reliably, including small (0.5–3B) **post-trained specialists**" (L60). Fixed specialist pool,
  **partially trained**. (NB: the dupple.com blog's "planner-exec +10pp / routing +4pp / re-plans on 38%
  of tasks" decomposition is **not in the paper** — see §9; the paper's isolable routing effect is
  "∼+1 pp and cannot yet be cleanly isolated", L76.)

### Tier C — **self-evolving** pool, but **whole-query** routing (evolution present, F2/decomp absent)
From catalog §1G — these evolve a pool but do **not** decompose a plan and route per-subtask:
- **FlyRoute** 2605.22057 — data-flywheel **evolving profiles**, **whole-query** routing, no decomposition.
- **EvolveRouter** 2604.05149 — co-evolving prompt variants, whole-query, not GAIA.
- **AgentFactory** 2603.18000 — persistent self-refining subagent library, but extractor confirms
  "decomposition, routing, forking, retirement: **Not present**."
- **MonoScale** 2601.23219 — expansion-aware pool onboarding + routing memory, **no decomposition**.
- **TASER** 2606.21307 — continual-learning skill modules + route, **no task decomposition**.

**The gap is exactly the Tier-B ∩ Tier-C intersection:** *per-subtask decomposition-routing (Tier B) TO
a self-evolved persistent pool (Tier C), training-free, on GAIA-web.* Tier-B systems have the routing but
a static/trained pool; Tier-C systems have the evolving pool but route the whole query. **Nobody joins
them.**

---

## 6. Placeholder final judgment (Q5)

Our proposition: **"on a harness-evolution loop's own persistent variant pool, do step-wise plan-execute
+ per-step executor best-selection (GAIA-web, training-free)."**

| Neighbor | Verdict | What it literally occupies / misses |
|---|---|---|
| **HuggingGPT** 2303.17580 | **PARTIAL** | occupies F1+F3 (plan + per-task model selection from a persistent hub); misses evolving pool, misses GAIA-web, misses stepwise-replan |
| **AOP** 2410.02189 | **PARTIAL** | occupies F1+F3+F4 with reward-gated selection + replan; pool **static-described**, not GAIA, no evolution |
| **Topaz** 2604.03527 | **PARTIAL** | typed-subtask→expert routing; **fixed roster, static profiles, decompose-then-route, no evolution** |
| **Magentic-One** 2411.04468 | **PARTIAL** | full F1+F2+F4 on GAIA; F3 = **fixed-role** routing, no evolving pool |
| **EvoMAS** 2605.08769 | **PARTIAL (closest on "execution-time pool selection on GAIA")** | per-stage selection over a pool on GAIA + execution-aware; but **RL-trained** and **fixed candidate pool** → fails our two load-bearing axes (training-free, self-evolved pool) |
| **OWL/Workforce** 2505.23885 | **PARTIAL** | has a **persistent** worker pool + re-decomp; pool is domain-fixed, gain is a training gain |
| **AOrchestra** 2602.03786 | **CLEAR on the pool axis** | per-subtask executor, but **fresh-spawn & discarded**, SFT orchestrator → not a pool at all |
| **Meta-Agent** 2605.25233 | **PARTIAL** | inference-time typed-spec DAG + routing + regeneration; **workers generated fresh per task**, no persistent evolving pool; no GAIA number |
| Tier-C evolving-pool routers (FlyRoute/EvolveRouter/AgentFactory/MonoScale/TASER) | **CLEAR on the decomposition axis** | evolve a pool but route **whole-query**; no per-subtask plan-routing |
| Symphony-Coord 2602.00966 | **UNVERIFIED adjacency** (L0, null-byte extract; not opened to S5) | web-snippet claims "router selects different candidates for decomposition and step-wise execution"; **verify before load-bearing** |

**Terminal placeholder verdict: the full conjunction is CLEAR (not yet refuted).** Every *ingredient* is
occupied — per-step decomposition-routing is routine (Tier B, since HuggingGPT 2023), and self-evolving
pools exist (Tier C) — but **no system routes per-subtask to a self-evolved persistent pool, training-free,
on GAIA-web.** Consistent with the catalog's §4/§5 conclusion. Popperian caveat: this is "not yet refuted",
resting on a near-exhaustive but not omniscient sweep; the residual risk lives in (a) Symphony-Coord
2602.00966 and (b) any post-Jul-20 DeepResearch routing policy not yet read to S5.

**Novelty is therefore NOT the architecture primitive** (old) **but the conjunction + the empirical claim:**
*does routing to a harness-self-evolved, seesaw-gated config pool beat fresh-spawn (AOrchestra) / fixed-role
(Magentic-One) / trained-specialist (Leni, EvoMAS) / static-profile (Topaz, AOP) routing under matched
budget on GAIA-web?* Frame the paper on that head-to-head, not on "we invented per-step routing."

---

## 7. Cost of replanning + the cross-arm comparability hazard (Q6)

**Call/token cost.**
- Mechanism (Plan-and-Act §3.3, S5 p. L321-326): dynamic replanning = *"the PLANNER updates the plan
  **after each EXECUTOR step**"* — i.e. **+1 planner LLM call per executed step**. Static = **O(1)**
  planner call; eager replan = **O(N)** planner calls for an N-step task (executor calls unchanged).
- Quantified overhead (2605.08477, S5 L40-44, L143-154): eager step-by-step vs **full-horizon plan +
  lazy/on-demand replan** = **accuracy parity at 2–3× fewer tokens**; eager replanning carries "the
  massive overhead of continuous feedback." So the accuracy-per-token of eager replanning is poor;
  if you ever add replanning, prefer **on-failure/lazy**, not per-step.

**The methodological hazard the user flagged — replanning breaks cross-arm comparability.**
No paper states this in exactly these words (it is our synthesis, grounded in the E-domain fair-baseline
guardrail), but the mechanism is direct and decisive for *our* experiment:
- Our comparison holds the **plan + the per-step subtask sequence** fixed and swaps only the **executor
  variant** per arm (matched-budget, replay-style, variance-controlled — DIVISION-DESIGN §2.2/§8).
- **In-execution replanning makes the plan a function of execution results** (Plan-and-Act L322-326:
  the planner conditions on "the current state as well as the previous plans and actions"). Because
  different executor variants produce different intermediate results, **each arm would get a different
  plan and a different subtask sequence** → the arms are no longer comparable; per-cell pass-rate
  credit is confounded by plan drift, not executor quality. This is the same failure the matched-budget
  MAS literature warns about (catalog: 2604.02460 "MAS advantages better explained by unaccounted
  computation and context"; 2606.13003 loses under matched budget).
- **Static one-shot plan is the methodologically clean choice** precisely because it makes the plan an
  arm-invariant constant, so any pass-rate delta is attributable to the executor selection, not to the
  plan. Keep it, and say so explicitly in the write-up (pre-empt the reviewer who cites
  "replanning helps").

---

## 8. Concrete recommendation for our design (change / don't change, and the cost)

**Recommendation: DON'T change the core. Keep the static one-shot typed-DAG plan + per-step routing to
the evolving pool. Do NOT promote in-execution replanning to the headline.**

Rationale, ranked:
1. **Comparability (decisive).** Replanning confounds the exact thing we measure (§7). Our whole value
   proposition is a clean matched-budget head-to-head of *pool-routing policies*; a plan that varies
   per arm destroys it. This alone settles it.
2. **The accuracy case for replanning is unproven in our regime.** Trained systems gain (Plan-and-Act
   +10 pt); the one **training-free** controlled study tilts the other way (PLANAHEAD: static ≥ dynamic).
   We are training-free. Adopting replanning would be betting on evidence that doesn't hold in our regime.
3. **Cost.** Eager replanning = O(N) planner calls, 2–3× tokens, for parity-level accuracy (§7). Bad
   trade under a budget cap.

**But mitigate static's known failure mode (Plan-and-Act §3.3) so a reviewer can't sink us:**
- Static's documented weakness = "content unknown a priori at planning time" / "search returns nothing"
  → executor blindly follows a dead plan. Our **inter-stage verify gate** (already flag-controlled in
  D1-lite) and **on-failure scope-expansion** partly cover this. Keep the verify gate ON for the
  headline runs.
- **Add — as an explicit, clearly-labeled backlog arm, not the headline —** a **bounded on-failure
  re-decomposition** (E3-style expand-on-fail 2607.13034; Workforce-style recursive re-decomp
  2505.23885): trigger at most once, only on a verify-gate failure, and **log it as a separate arm** so
  the primary matched-budget comparison stays replay-clean. Cost of this arm: +1 planner call per
  triggered task (not per step), and a second evaluation pass — cheap and comparability-preserving if
  gated on failure only.
- **Framing fix (do this regardless):** confine the headline claim to **accuracy-under-matched-budget on
  GAIA-web**, not efficiency — because static one-shot decomposition is a *known weak baseline on the
  cost axis* (2605.15425: runtime-structured beats static by −73.2% retry cost). Don't fight on cost;
  win on matched-budget accuracy of the routing policy.

**If the user insists on some execution-time adaptivity:** the comparability-safe option is **not**
replanning the plan, but **allowing the per-step router to re-select an executor on a step's failure**
(the plan/subtask sequence stays fixed; only the executor assignment for the failing step changes). That
preserves arm-invariance of the plan while capturing most of replanning's practical benefit (retrying a
step with a different pool member). This is a strictly smaller, cleaner change than plan-level replanning
and is the recommended compromise.

---

## 9. Integrity / depth ledger (guardrails)

**Read to S5 (verbatim in hand) this session:** ReAct 2210.03629 (p.2 defn), HuggingGPT 2303.17580
(§3.2 Model Selection), Plan-and-Solve 2305.04091 (title/abstract), Plan-and-Act 2503.09572 (§3.3 +
ablation Table), Magentic-One 2411.04468 (§5.3 ablation), AOP 2410.02189 (abstract/mechanism),
PLANAHEAD 2605.29927 (Table 1 + findings), EvoMAS 2605.08769 (train/pool facts), Leni 2607.17044
(uplift decomposition), 2605.08477 (lazy-replan cost). LLM-Compiler 2312.04511 read to L2 (parallel/
joiner confirmed).

**Carried from catalog/DR-D (not re-derived):** Topaz, Workforce/OWL, Meta-Agent, AOrchestra, Uno,
AgentOrchestra, FlyRoute/EvolveRouter/AgentFactory/MonoScale/TASER, 2604.02460, 2606.13003, 2605.15425,
2512.08296. Treat their numbers at the catalog's stated depth.

**Two summarizer hallucinations caught and DISCARDED (C4 anti-fabrication):**
1. A WebSearch summary claimed *"Dynamic planning significantly boosts Level 2 reasoning by 18.24% on
   the **GAIA** benchmark"* attributed to 2605.29927. **False.** The PDF is a **WebArena** study
   (L164 "WebArena test split", L183 "158 Hard tasks"); it reports **no GAIA number** and its actual
   finding is the **opposite** ("Static Planning Often Outperforms Dynamic"). Not used.
2. The dupple.com Leni blog claimed a clean *"planner-executor split +10pp / cross-provider routing
   +4pp / verification +3.6pp / re-plans on 38% of tasks"* decomposition. **Not in the paper.** Leni
   2607.17044 reports verification isolated **+1.5 pp** and routing "**∼+1 pp … cannot yet be cleanly
   isolated**" (L73-76). The catalog's S5 (+1.5) is correct; the blog is confabulated. Not used.

**UNVERIFIED / do not cite load-bearing:** Symphony-Coord 2602.00966 (null-byte extract, L0), any
post-Jul-20 DeepResearch RL routing policy, LangChain Plan-and-Execute internals beyond the public
blog/docs framing.

**Residual open questions:** (a) no training-free static-vs-planner-executor-replan head-to-head exists
on GAIA — runnable gap; (b) Symphony-Coord's exact mechanism (does it self-evolve the pool?) unresolved;
(c) whether Meta-Agent 2605.25233's regenerated workers ever persist across tasks in the body (catalog
flagged, still open).

---

## 主循环亲验修正(Jul-31,PDF 直读 2605.29927)

**已核实**:小节标题逐字在文——*"Static Planning Often Outperforms Dynamic
Single-Agent Planning. Even without the possibility of regenerating or updating
plans during execution static plans are able to…"*;床 = **WebArena**(12 命中),
**GAIA 0 命中**(scope 限定成立)。

**⚠️ 但本档原判词须收窄**:该句的对照面是 **dynamic *single-agent* planning**
(即 ReAct 式单体边做边想),**不是**"planner-executor 架构内部的 静态计划 vs
执行中重规划"。因此:
- 不能用它支持"训练自由体制下重规划无益";
- 正确读法 = "planner-executor + 静态计划 > 单体动态规划(WebArena)"。

**修正后的证据态势**:**训练自由体制下,planner-executor 内部的"重规划 vs 不重
规划"受控对照,在 WebArena 与 GAIA 均属空白**(Plan-and-Act 的 +10.3 是**已训练**
系统)。⇒ 我方"保持静态"的决定**主要立于跨臂可比性与成本,而非"重规划被证无益"**;
论文措辞须如此,不得暗示文献否定了重规划。
**副产品**:该空白本身是一个可跑的小贡献(见 DR-H 的建议)。
