# DR-H — Replanning / Plan-Revision as a Mechanism Family (deep-read)

**Scope:** Replanning *itself* as a mechanism family — trigger, scope, agent, state-handling; whether it
helps (budget-aware); its cost/failure modes; and the methodological problem it creates for **cross-arm
replay of one shared decomposition plan**. Sibling doc `DR-G-PLAN-DISPATCH.md` owns plan-then-dispatch
naming/lineage — **not re-done here**. Starting point = `experiments/docs/DECOMP-METHOD-CATALOG.md`
(45-system census with a `Replan?` column).
**Method:** WebSearch/WebFetch breadth sweep → arXiv **PDF direct read** (curl + `pypdf 6.14.2` +
regex, `[\s-]*` hyphen tolerance) for every load-bearing number/quote. Two parallel researcher sub-agents
extracted the classic "does-replanning-help" cluster and the verifier/GAIA/hierarchical cluster.
**Guardrails:** `research-guardrails` (enforce). Negatives reported as negatives; every number carries
metric+split+n; budget-parity flagged on every ablation; no fabricated conditions; L0 never load-bearing.
**Date:** 2026-07-31.

### Depth / provenance legend
- **S5** = verbatim string in hand from the primary PDF (page index cited; pypdf is **0-based**, so `[p12]`
  ≈ printed p.13). Load-bearing.
- **S5†** = verbatim but summarizer-mediated (WebFetch small model) — numbers triangulated, wording ±.
- **L2/L1** = method skimmed / abstract only. **L0** = secondhand search snippet — *never* load-bearing.
- **INFER** = my reading, not the paper's words.

---

## 0. TL;DR (the compressed answer)

- **Taxonomy (one line):** replanning = *{trigger: per-step / on-failure / on-verifier-reject / on-stall /
  low-confidence / fixed-interval} × {scope: whole-remaining-plan / next-step-refine / local-subtree-patch /
  full-restart} × {who: original planner / separate critic-verifier / executor self-report} × {state: clear+reset
  context / carry completed-step results forward / episodic-memory of past failures}* — and almost every real
  system is a **point** in this grid, not a knob.
- **Does it help? — HAS CONDITIONS, and on GAIA/web the honest answer is "little, and unproven at matched budget."**
  The one clean GAIA-adjacent number that isolates a replanning *loop* (Leni 2607.17044) puts its marginal
  contribution at **∼+1 pp and "cannot yet be cleanly isolated"**; a security analysis of WebArena (Piet et al.
  2605.14290) finds **"None of the tasks in WebArena are replan-needed."** The strongest *negative*: intrinsic
  self-correction **degrades** reasoning (Huang 2310.01798, GPT-3.5 CommonSenseQA **75.8 → 38.1** after 1 round),
  and "always-plan" degrades long-horizon agents (Paglieri 2509.03581, "Goldilocks" zone). Positive evidence
  (AdaPlanner/ADaPT/Reflexion/DEPS/H-RePlan) is real but **off-GAIA**; only **two are budget-matched** — ADaPT
  (+23.8 vs a compute-matched retry baseline) and H-RePlan (Pareto-better) — and both sit on task distributions
  with **recoverable failures + local-subtree structure**, exactly what GAIA-text sequential chains lack. **The
  operative split: *conditional, on-failure, local-scope* replanning with a reliable failure signal helps;
  *unconditional per-step / intrinsic self-correction* hurts.**
- **Q4 has a ready-made answer.** The variance-reduction from replaying one shared plan across arms is exactly a
  **common-random-numbers / paired-comparison** design (Sharma 2512.24145, Thm 1). Its precondition — **positive
  seed-level correlation** — is destroyed the moment the two arms diverge: CRN work (Klein 2409.02086) states the
  **"first difference causes a significant loss of correlation."** An **adaptive plan is exactly such a first
  divergence**, so it breaks the pairing. The engineering fix already has a name in the agent literature:
  **replay pairing** (Mehta & Datta 2606.22953) — replay one arm's exact action-observation trajectory into the
  other so the only difference is the controlled factor.
- **Recommendation: DO NOT add replanning to the headline pipeline.** It breaks the paired variance control that
  is your contribution, multiplies cost, risks oscillation, and buys ∼0–1 pp of unproven accuracy on GAIA-text.
  Keep static one-shot DAG; add replanning only as a **separately-analyzed backlog ablation** using replay-pairing
  if a reviewer demands it. (Full minimal-change design in §5.)

---

## 1. Q1 — TAXONOMY OF REPLANNING (four axes)

Replanning is not one mechanism; it is a **cross-product of four independent design axes**. Below, each cell
lists ≥1 representative system with an arXiv ID. Depth tag per claim.

### Axis A — TRIGGER (when does the plan get revised?)

| Trigger | Representative systems (arXiv) | Depth |
|---|---|---|
| **Every step** (re-decide before each action) | ReAct 2210.03629; AOrchestra 2602.03786 (per-step 4-tuple decomposition, no upfront plan); "always-plan" baseline in 2509.03581 | L2 [catalog] |
| **On execution failure** (executor self-reports it cannot finish) | ADaPT 2311.05772 (as-needed recursive decomp); DEPS 2302.01560 (descriptor→explainer on failure); OWL/Workforce 2505.23885 (recursive re-decomp on failure); Agent S2 2504.00906 | **S5(sa)** ADaPT/DEPS/OWL confirmed |
| **On verifier / critic reject** | LLM-Modulo 2402.01817 & 2405.20625 (external verifiers re-prompt); LLM+P 2304.11477; Reflexion 2303.11366 (eval/test feedback → reflect) | **S5(sa)** LLM-Modulo/Reflexion confirmed |
| **On stall / loop / no-progress counter** | **Magentic-One 2411.04468** — "maintains a counter for how long the team has been stuck or stalled … As long as this counter remains below a threshold (≤ 2)" ([p6]); "Stall count > 2" triggers outer-loop replan (Fig 2, [p4]) | **S5 [p6]** |
| **On low confidence / uncertainty** | WebUncertainty 2604.17821 (action-uncertainty → MCTS); Learning-When-to-Plan 2509.03581 (learned gate deciding *when* to plan) | S5 [2509.03581] / L1 |
| **On artifact-inconsistency (typed check vs plan)** | **Leni 2607.17044** — planner "inspects each artifact against the original task and re-plans … In our GAIA run the planner re-planned on ∼38% of tasks, mostly to recover from failed sources" (§4.3, [p6]) | **S5 [p6]** |
| **Fixed interval** (replan every k steps) | *Rare / discouraged.* 2509.03581 shows fixed schedules lose to a learned adaptive gate; no strong pure-fixed-interval exemplar found | INFER |

### Axis B — SCOPE (how much of the plan changes?)

| Scope | Representative systems | Depth |
|---|---|---|
| **Whole remaining plan re-generated** | Magentic-One 2411.04468 (outer loop rewrites task-ledger plan); OWL/Workforce recursive re-decomp | S5 / L2 |
| **Next-step / in-plan refinement only** | AdaPlanner 2305.16653 ("in-plan" refinement adjusts within the existing plan; "out-of-plan" for larger deviations) | **S5(sa)** [p.4,6] |
| **Local subtree patch** (re-decompose only the failing node) | ADaPT 2311.05772 (recursive decomposition of *the failing subtask*); H-RePlan 2606.20487 ("Beyond Global Replanning": device-local / CLI-local recovery before orchestrator-level reassignment); Leni 2607.17044 ("inserting recovery steps or course corrections", [p6]) | **S5(sa)** H-RePlan Table 3 p.9; Leni [p6] |
| **Full restart / reset** | Magentic-One 2411.04468 — `replan()+reset()` on stall; "restarts and resets upon stalling" ([p2]) | **S5 [p2]** |

### Axis C — WHO replans?

| Who | Representative systems | Depth |
|---|---|---|
| **Original planner refines its own plan** | AdaPlanner 2305.16653; Magentic-One Orchestrator 2411.04468; Leni planner 2607.17044 | S5 / L1 |
| **Separate critic / verifier module** | LLM-Modulo 2402.01817 (external verifiers); Reflexion 2303.11366 (self-reflection + evaluator); DEPS 2302.01560 (explainer + selector); Leni uses a **small trained verifier**, and "replacing the small trained verifier with the generating frontier model eliminates most rescues" (2607.17044 [p0]) — *who observes matters* | S5 [Leni p0] / L1 |
| **Executor self-reports up to planner** | ADaPT 2311.05772 (executor self-assesses success/failure); Magentic-One agents report progress → progress ledger | **S5(sa)** ADaPT [p.6] / S5 |

### Axis D — STATE HANDLING (what happens to already-completed steps?)

| State policy | Representative systems | Depth |
|---|---|---|
| **Clear + reset context on replan** (facts kept, agent contexts wiped) | **Magentic-One 2411.04468** — "Since this plan may be revisited with each iteration of the outer loop, we force all agents to clear their contexts and reset their states after each plan update" (§4.1, [p5]); the **task ledger (facts/plan) persists** as short-term memory | **S5 [p5]** |
| **Carry completed-step results forward into the new plan** | ADaPT 2311.05772 (completed subtasks inform recomposition); DEPS 2302.01560 (descriptor summarizes current state); Leni typed artifacts (value·citation·error-class) fed to planner (2607.17044 Fig 4, [p8]) | S5 [Leni p8] / L1 |
| **Episodic memory of past failures across retries** | Reflexion 2303.11366 — reflective text in an episodic buffer across trials (retry until 3 consecutive fails) | L1 [search] |
| **Plan lives only in context (not internalized)** — governs whether carried state survives compression | **Plans-Don't-Persist 2606.22953** — plan signal "spikes … then falls 4.1× in a single action-observation step"; agents "do not carry plans forward as persistent state, and instead depend on the plan remaining in context" ([p0]) | **S5 [p0]** |

> **INFER (taxonomy verdict):** The two axes that matter most for *our* design are **B (scope)** and **D (state)**.
> A *local-subtree patch* that *carries completed results forward* is the least disruptive to a paired design
> (it perturbs only one subtree); a *whole-plan regenerate with context reset* (Magentic-One) is maximally
> disruptive — it makes the two arms' trajectories diverge globally. This distinction drives §5.

---

## 2. Q2 — DOES REPLANNING HELP? (the most important question)

**Headline:** replanning is **conditionally** useful and its payoff is **task-structure-dependent**; on the
**GAIA/web** distribution the demonstrated accuracy gain is **small and not established at matched budget**, and
there are **direct negative results**. Positive within-framework ablations exist but are **off-GAIA and mostly
budget-confounded** (the replan arm buys more LLM calls).

### 2A. The honest ablation table (direction + budget-parity flag)

| Source (arXiv) | Bench / split / n | Arm A (less/no replan) | Arm B (replan) | Direction | Budget matched? | Depth |
|---|---|---|---|---|---|---|
| **Huang 2310.01798** (self-correct) | GSM8K/CSQA/HotpotQA, GPT-3.5, Table 3 | Standard 1-call | Self-Correct r1 (3 calls) / r2 (5 calls) | **HURTS**: CSQA **75.8→38.1→41.8**; GSM8K 75.9→75.1→74.7; HotpotQA 26→25→25 | **N/A — replan arm gets 3–5× calls and STILL loses** | **S5 [p3]** |
| **Huang 2310.01798** (matched-budget control) | GSM8K, Table 7 | Self-Consistency @6 = **85.3**, @9 = **88.2** | Multi-Agent Debate r1(6 resp)=83.2, r2(9)=83.0 | debate < self-consistency **at equal #responses** | **YES (matched #responses)** — replan-flavoured "debate" loses | **S5 [p6]** |
| **Leni 2607.17044** (GAIA) | GAIA-val, n=165, Opus-4.6 planner | structure tiers (planner-exec ∼70%, +routing ∼74%) | + verification/re-planning loop | loop marginal **∼+1 pp**, "cannot yet be cleanly isolated" (§7, [p1]) | **NO — matched-budget test explicitly UNRUN** (§10, [p15]) | **S5 [p1,p15]** |
| **Leni 2607.17044** (SpreadsheetBench) | Verified, n=400 | scaffolding+prompting = **+9.5 pp** of +11.0 | + recalculation loop = **+1.5 pp** (rescues 6 tasks) | loop adds little but "positionally decisive" at top | NO (cumulative-add) | **S5 [p1]** |
| **Magentic-One 2411.04468** (GAIA) | GAIA-val, base 38.0 | remove full ledgers | full ledgers (plan+facts+progress+replan) | **−31% without ledgers** → ledger/replan machinery **load-bearing** | N/A — bundled ablation (see caveat) | **S5 [p12]** |
| **Learning-When-to-Plan 2509.03581** | POGS + Crafter (trained SFT+RL) | intermediate planning frequency | **always-plan** (ReAct-like) | **always-plan DEGRADES**; "Goldilocks" peak at intermediate freq | frequency = compute axis (more plan = more tokens) | **S5 [p0,p5]** |
| **Plan-Then-Execute 2605.14290** | WebArena (all sites) | static plan-then-execute | reactive replanning | **"None of the tasks in WebArena are replan-needed"** ([p7]); 81.28% purely programmatic | task-compatibility analysis, not accuracy run | **S5 [p0,p7]** |
| **ADaPT 2311.05772** ★ (only budget-matched positive) | ALFWorld test, GPT-3.5, Table 1 | Try-Again w/ ReAct (d_max blind retries) **47.8**; static Plan-and-Execute **43.3** | as-needed recursive **local** replan **71.6** | **HELPS +23.8** vs compute-matched retry (+28.3 vs static) | **YES — "ensure all baselines use a comparable number of LLM calls" (§6.5, Fig 7)** | **S5(sa) Table 1 p.6 / §6.5 p.9** |
| **H-RePlan 2606.20487** ★ (Pareto-better) | HeraBench cross-device, Table 3 | w/o Global-Replan **41.25** comp; global-only (w/o Strategy-Planner) **44.97** | full hierarchical local+global **75.84** | **+34.6pp** (global replan) & **+30.9pp** (hierarchical-local over global-only) | **NO CONFOUND — higher quality AND lower cost/success (1.93M vs 2.41M/3.99M tok)** | **S5(sa) Table 3 p.9** |
| **DEPS 2302.01560** | Minecraft, n=30/task, Table 4 | **round 0 = vanilla, no replan** | replan rounds 1/3/5/∞ | **HELPS, monotone**: MT1 28.6→79.8 (+51.2); MT3 15.1→62.4; MT8 0.0→0.6 | **NO** — each round = +1 call + prompt-concat token growth (cap ~7–8) | **S5(sa) Table 4 p.9** |
| **AdaPlanner 2305.16653** | ALFWorld 134, Fig 4a/4b | 0 corrections (open-loop) | 1–5 closed-loop corrections | HELPS, monotone trend (per-point values **figure-only, UNVERIFIED**) | **NO** — more corrections = more calls (refine-then-resume only partly offsets) | S5(sa) trend p.8; numbers UNVERIFIED |
| **Reflexion 2303.11366** | HotPotQA / ALFWorld / HumanEval | episodic-memory / single attempt | + self-reflection retry | **MIXED**: +8pp over episodic-memory baseline; ALFWorld 130/134 (+22pp); **NULL on weak model starchat-beta 0.26→0.26** | **PARTIAL — trial-matched, NOT call/token-matched** | **S5(sa) p.5,7,12** |
| LLM-Modulo Travel 2405.20625 | TravelPlanning val, 180 Q, GPT-4-Turbo | Direct **4.4** | verifier-reject → whole-plan regen, ≤10 iters **20.6** | HELPS 4.6× — but only **20.6% converge within budget** (~79% never pass) | **NO — ≤10 iters vs 1** | **S5(sa) Table 1 p.2–3** |
| LLM+P 2304.11477 | 7 IPC domains, GPT-4 | raw LLM (Blocksworld **20**) | classical planner one-shot (**90**) | HELPS — but **NO replan** (open-loop; static reference point) | N/A (architecture, not revision) | **S5(sa) Table I p.5** |

### 2B. Reading of the GAIA / web-agent evidence (guardrails-honest)
1. **No system provides a clean, budget-matched "replan on/off" accuracy ablation on GAIA.** The closest isolable
   loop (Leni) reports **∼+1 pp and explicitly flags the matched-budget test as unrun** — this is *the same gap*
   DR-A/DR-B already identified for decomposition, now confirmed for replanning specifically.
2. **Magentic-One's −31% is the best GAIA replan signal but is a BUNDLED ablation** — removing "the full ledgers"
   deletes facts + plan + progress-tracking + the replan/stall machinery *together* (S5 [p12]), so it certifies
   "structured-state + replanning scaffold is load-bearing," **not** "replan-on beats replan-off at equal state."
   (Magentic-One base GAIA = 38.0; a low-scoring system where scaffolding helps most.)
3. **Web-agent counter-evidence is real:** Plan-Then-Execute (Berkeley, cs.CR) analyzes every WebArena task and
   finds **zero require replanning** (§6.1, Table 1 [p7]); >80% are fully static programs. Their argument is
   *security* (static plans resist prompt injection) but the **task-structure fact** — most web tasks have a
   control flow determinable up front — directly undercuts "web agents need replanning."
4. **Counterexamples / no-difference / degrade:**
   - **Huang 2310.01798**: intrinsic self-correction (a per-answer replan) **reduces** accuracy across GSM8K/CSQA/
     HotpotQA/Llama-2; the mechanism (S5 [p3]): the model "is more likely to modify a correct answer to an
     incorrect one." Root cause: LLMs "cannot properly judge the correctness of their reasoning."
   - **Learning-When-to-Plan 2509.03581**: "always planning is computationally expensive and **degrades**
     performance on long-horizon tasks" (abstract [p0]); the always-plan agent has the **highest backtracking**
     and **lowest success** (App B, [p22]).
   - **MiroFlow (DR-B)**: single-agent 74.8 > multi-agent 71.9 on GAIA (multi = more delegation calls) — decomp
     with more agent calls *loses* on GAIA's sequential structure.
5. **Is the gain "the plan" or "the extra calls"? — Largely unconfirmed, and where controlled, it's the calls.**
   Huang's matched-#response control (Table 7, [p6]) shows the replan-flavoured "debate" **underperforms plain
   self-consistency at equal budget**. Leni names the exact missing experiment: replan-loop vs **best-of-n at
   matched token budget** "have not been run … therefore unmeasured" (§10, [p15]). **This is precisely the
   controlled comparison our study is positioned to run** (cf. DR-A compute-confound line).
6. **When properly budget-matched, replanning CAN help — but the two clean cases are OFF-GAIA and structurally
   unlike GAIA-text.** ADaPT (2311.05772) matches LLM-call budget vs a blind-retry baseline ("Try-Again") and
   still wins **+23.8** on ALFWorld (S5(sa) §6.5/Fig 7); H-RePlan (2606.20487) is **Pareto-better** — higher
   completion AND lower tokens-per-success (S5(sa) Table 3). Both live on **recoverable-failure + local-subtree**
   distributions (embodied task-solving; cross-device GUI/CLI). GAIA-text is the opposite regime: sequential
   tool-chains where a mid-chain error **propagates** (MiroFlow's stated mechanism), so the "recover a failed
   subtask" value that ADaPT/H-RePlan exploit is largely absent.
7. **The direction of the effect is CONDITIONAL on the trigger, not on "replanning" as such.** *Conditional,
   on-failure, local-scope* replanning with a reliable failure signal is monotone-helpful (ADaPT, DEPS Table 4
   round-0→∞, H-RePlan, AdaPlanner Fig 4 — all monotone, none reports oscillation). *Unconditional per-step or
   intrinsic self-correction without a trustworthy verifier* is what degrades (Huang; always-plan 2509.03581;
   Reflexion's **NULL on a weak base model**, S5(sa) starchat-beta 0.26→0.26 — "an emergent quality of stronger
   models"). So the failure signal quality, trigger conditionality, and scope locality — not the presence of
   replanning — decide the sign.
   *(Provenance flag: the oft-cited "Blocksworld → 82% within 15 back-prompt rounds" is **secondhand** — it lives
   in the LLM-Modulo position paper 2402.01817 citing Valmeekam et al. 2023c, not a primary result; do not load-bear.)*

> **Q2 verdict: HAS CONDITIONS.** Replanning pays off on **long-horizon, non-sequential, recoverable-failure**
> tasks with a **reliable (external/oracle) failure signal** and **local-scope** revision (ADaPT, H-RePlan,
> DEPS — budget-matched only for the first two, all off-GAIA). It **degrades** when unconditional / intrinsic /
> weak-model (Huang, always-plan, Reflexion-on-starchat). On **GAIA-text sequential tool-chains** the isolated
> accuracy gain is **∼+1 pp, unproven at matched budget**, with real counterexamples. The reliable payoff of
> replanning in the literature is **cost/efficiency and robustness**, not raw accuracy.

---

## 3. Q3 — COST & FAILURE MODES

### 3A. Extra calls / tokens / latency
- **Per-answer self-correction ≈ 2k+1 calls for k rounds** (Huang 2310.01798: r1 = 3 calls, r2 = 5 calls, for a
  1-call baseline; S5 [p3]). So even a shallow 2-round loop is a **3–5×** inference multiplier.
- **Verifier-reject loops run many rounds and often DON'T converge:** LLM-Modulo Travel caps at **≤10 iterations**
  and only **20.6% of tasks reach a fully valid plan within budget** → ~79% never pass in 10 rounds (2405.20625
  S5(sa) Table 1). Position-paper Blocksworld figure is 15 rounds (secondhand; see §2B note).
- **Replan-count = test-time compute (monotone but confounded):** OWL/Workforce Fig 4b, replan count 0/1/2 gives
  {0.43,0.54,0.60} & {0.51,0.60,0.69} (2505.23885 S5(sa) p.10) — each extra replan is a full re-decompose+execute
  pass; DEPS concatenates every round into the prompt so **tokens grow per round** (cap ~7–8).
- **Wall-clock blow-up:** StructuredAgent (AND/OR-tree replan) runs GitLab **18.0 min vs AgentOccam 5.2 min**
  (~3.5×) from "deliberate planning and dynamic replanning upon encountering failures" (2603.05294 S5(sa) p.13).
- **GAIA replanning fires often:** Leni's planner "re-planned on ∼38% of tasks" (2607.17044 [p6]) — i.e. ~1 extra
  planner pass on a third of the set, plus the recovery steps it inserts.
- **Planning frequency ∝ output tokens** (2509.03581 App B [p22]): "Increasing planning frequency leads not only
  to higher costs (more output tokens) but also to a higher backtrack count."
- **Counter-data point (replanning can be Pareto-cheaper):** H-RePlan spends **fewer** tokens/perfect-pass than
  its no-global-replan ablation (1.93M vs 2.41M vs 3.99M; 2606.20487 S5(sa) Table 3) — good failure abstraction +
  local-first recovery makes replanning *net cheaper*, not costlier. Cost sign depends on trigger/scope design.

### 3B. Named failure modes (all S5)
- **Replanning oscillation / thrashing** — 2509.03581 [p3]: "Frequent replanning, especially with imperfect or
  inconsistent plans, can introduce behavioral instability (e.g., inefficient backtracking, **subgoal
  oscillation**) that ultimately hinders task success." App B [p22]: always-plan "continually change[s] its mind,"
  highest backtracking → reduced success. Modeled as an **instability cost** `C_noise = k·f_p·(1−Q̄_p)` (scales
  with planning frequency × low plan quality).
- **Plan drift** — 2509.03581 §3.1/App B.2 [p3,p21]: "the usefulness of an existing plan is not static; it
  typically diminishes over time as the agent acts and the environment evolves (**plan drift**)." High-level plans
  drift slowly; low-level detailed plans "become outdated quickly." (This *motivates* replanning but also makes a
  static plan's validity horizon a real design variable.)
- **Correct→incorrect flips** — Huang 2310.01798 [p3,p5]: self-correction converts correct answers to incorrect
  more often than the reverse (GSM8K GPT-3.5: Correct→Incorrect 8.8% vs Incorrect→Correct 7.6%; CSQA far worse).
- **Plan eviction collapse** — Plans-Don't-Persist 2606.22953 [p1]: "naive plan eviction cuts ALFWorld success by
  **34.7 pp**, and probe-gated re-surfacing does *not* recover it" — if a replanner drops/rewrites the plan and
  the model never internalized it, behavior collapses.
- **Non-convergence guard is manual** — Magentic-One caps the stall counter at ≤2 (2411.04468 [p6]); OWL caps
  replans at K=2; without such a hard cap, loops don't self-terminate.
- **Deep-replan degradation when the failure signal is poor** — H-RePlan w/o its compact failure abstraction
  (CLFE): "later replans increasingly end in aborts" (2606.20487 S5(sa) p.9). I.e. replanning quality collapses if
  the *evidence* fed to the replanner is thin — reinforcing §2B-7 (signal quality decides the sign).

> **Honest calibration:** the four *classic conditional* replanners (ADaPT, DEPS, AdaPlanner, OWL) report
> **monotone / plateauing** gains with more rounds and **do NOT** exhibit oscillation in their own ablations. The
> oscillation/thrashing/plan-drift evidence comes from **unconditional/always-plan** regimes (2509.03581) and
> **intrinsic self-correction** (Huang). So "replanning thrashes" is a property of *unconditional, weak-signal,
> global-scope* replanning — not of bounded, on-failure, well-verified, local-scope replanning.

---

## 4. Q4 — METHODOLOGY: DOES ADAPTIVE REPLANNING BREAK CROSS-ARM PAIRING? (critical for us)

**Our design premise:** replay **one shared decomposition plan** across arms so that per-item paired differencing
cancels the "which plan did we get" variance source. **Question:** if the plan adapts to execution results, is the
pairing still valid, and how do people run controlled comparisons under replanning?

### 4A. The pairing you rely on IS a common-random-numbers design — and its precondition is explicit
- **Sharma 2512.24145, "When Does Pairing Seeds Reduce Variance?"** formalizes exactly this. **Theorem 1** ([p1]):
  `Var(Δ̂_pair) = Var(Δ̂_ind) − (2/n)·Cov(Y(1,s), Y(0,s))`, and Eq (2): the variance *reduction* `= (2/n)·ρ·σ₁·σ₀`.
  "paired evaluation is statistically equivalent to a **common random-numbers estimator**" ([p1]).
- **The precondition is positive correlation, and it can reverse** (§5 Limitations, [p4], **S5**):
  > "efficiency gains from pairing arise only when seed-level correlation is **non-negative** … In contrast,
  > **negative seed-level correlation reverses the variance comparison, in which case paired evaluation may be
  > less efficient than independent designs**."
  And ([p4]): pairing is "most effective when random seeds control the primary sources of … variability.
  Environments with substantial sources of stochasticity **not governed by the [shared unit]** may exhibit weaker
  seed-level correlation, limiting achievable [gains]."

### 4B. An adaptive plan is precisely the "first divergence" that collapses the coupling
- **Klein 2409.02086 (CRN for agent-based sims)** states the failure mechanism in one sentence ([p1], **S5**):
  > "While X and Y may be identical initially, **the first difference causes a significant loss of correlation**
  > due to stochastic random number noise … the outcomes quickly lose covariance following the first difference."
  It even names the property: algorithms are **"CRN safe"** only if a local change (their example: one extra agent)
  does **not** cascade into globally different draws ([p5]).
- **INFER (the load-bearing deduction):** cross-arm plan-replay makes the shared plan the common random number →
  high `Cov(Y_A, Y_B)` → your paired estimator gets the Thm-1 variance reduction. **An adaptive replan is a
  structural divergence in the middle of the trajectory** — the arms stop sharing the realization from that point,
  `Cov` drops toward (or below) zero, and by Thm 1 / Eq (2) the variance-reduction benefit **shrinks or reverses**.
  With your **SD ≈ 4.7 pp, n = 103, single seed**, losing the pairing means losing the *only* thing making a small
  effect detectable. **So yes — adaptive replanning directly undermines your paired design's validity.**
- **Nobody states this specific claim ("adaptive replanning breaks LLM-agent cross-arm pairing") verbatim** — it is
  a **gap**. But the *general principle* is rock-solid and citable (CRN / paired-comparison; 2512.24145 Thm 1;
  2409.02086 first-difference-decorrelation). This is a point **in your favour**: your static plan is the
  methodologically *correct* choice for a paired study, not a limitation to apologize for.

### 4C. The known engineering fix: **replay pairing** (a "replayable trajectory" method already exists)
- **Mehta & Datta 2606.22953, "Plans Don't Persist"** introduce **replay pairing** (§3.3, Fig 1, **S5 [p1,p3]**):
  > "For each trajectory we run two matched conditions: A keeps the plan in history; B **replays the same
  > trajectory** with the plan removed … We replay A's exact trajectory (feeding A's observations and action
  > outputs) … **Both conditions see the same action-observation sequence; they differ only in whether the plan
  > exchange is in history.**"
  This is the canonical way to keep two arms comparable when a factor (here: the plan) would otherwise make them
  diverge: **designate one arm's trajectory as the driver and replay it into the counterfactual arm**, so the only
  varying quantity is the factor under test.
- **Adaptation to replanning (INFER):** if you must compare replan-on vs replan-off, you can (a) run the
  replan-on arm, **log its full action-observation trajectory**, and (b) replay that fixed trajectory into the
  other arm's harness, changing only the replan flag. This preserves per-item pairing *by construction* — at the
  cost that you are now measuring a **within-trajectory counterfactual**, not two independently-evolving plans.
- **Caveats the same paper flags:** a **reasoning-trace confound** — `<think>` blocks re-derive plan content and
  contaminate paired-trajectory measurements on reasoning models; fix = strict stripping ([p1]). If your arms use
  reasoning models, replay-pairing needs this correction.
- **Supporting frame:** the replay-analysis paper 2607.12338 uses **paired per-task differences** `D_i = Y_{i,A} −
  Y_{i,B}` as the unit of decision ([p2]), and treats "**scaffolding, retry logic**" as part of *system identity*
  ([p0]) — i.e., the harness/plan is a variable you must hold fixed. The harness-disclosure position paper
  2605.23950 ("Binding Constraint Thesis": scaffold, not model, dominates long-horizon variance; up to ~15 pp
  scaffold-only swing on SWE-bench Verified — L1/search) is the same lesson: **the plan/harness is a first-order
  confound that a valid comparison must pin down.**

> **Q4 verdict:** (1) The pairing you use is a CRN/paired design whose validity **requires the arms to share the
> stochastic realization**; an adaptive plan breaks that (Thm 1 + first-difference decorrelation). (2) The
> problem is **not** discussed verbatim for LLM-agent plans — a citable gap you occupy. (3) The engineering
> workaround **exists and is named**: **replay pairing** (2606.22953) — replay one arm's fixed trajectory into
> the other. Use it only if replanning is forced; it changes the estimand to a within-trajectory counterfactual.

---

## 5. Q5 — RECOMMENDATION FOR OUR STUDY

**Constraints:** GAIA text-only 103 Q · single seed · noise **SD ≈ 4.7 pp** · 5 weeks to submission · existing
static-DAG pipeline with **874 tests** · upstream results already injected into downstream prompts.

### 5A. Verdict: **DO NOT add replanning to the headline pipeline.** (High confidence.)
Four independent reasons, each cited:
1. **It breaks your core contribution.** Adaptive plans destroy the cross-arm plan-replay pairing (Q4: 2512.24145
   Thm 1; 2409.02086 first-difference decorrelation). At SD 4.7 pp / n=103 / single seed you **cannot afford** to
   lose the variance reduction — an unpaired design would need a much larger effect (or many more seeds you don't
   have time for) to clear noise.
2. **The accuracy payoff on GAIA is ∼0–1 pp and unproven at matched budget** (Leni 2607.17044 §7/§10, S5 [p1,p15]),
   with **direct negatives** (Huang self-correction degrades, S5 [p3]; always-plan degrades, 2509.03581 [p0]) and
   a web-agent task-structure result that **0/WebArena tasks need replanning** (2605.14290 [p7]).
3. **Cost & risk are real:** 3–5× calls for a shallow loop (Huang [p3]), replanning fires on ~38% of GAIA tasks
   (Leni [p6]), plus oscillation / plan-drift / correct→incorrect-flip failure modes (§3B). In 5 weeks against an
   874-test static harness, that is a large surface of new bugs.
4. **Static one-shot planning is a principled choice, not a shortcut** — you can defend it with Plan-Then-Execute
   (security + task-structure) and the "Goldilocks / never-plan is a valid regime for short-horizon" framing of
   2509.03581. Pre-empt the reviewer by **confining the claim to accuracy-under-matched-budget on GAIA-text** and
   listing replanning as explicit backlog (as DR-A/catalog already advise for decomposition).

### 5B. IF a reviewer forces "you must at least ablate replanning" — the minimal-change version
Design that touches the fewest existing invariants and **stays paired**:

- **What to add:** one **bounded, local recovery trigger**, *not* whole-plan regeneration.
  - **Trigger:** a stall/verifier-reject counter on the *existing inter-stage verify gate* (Magentic-One pattern,
    hard cap k ≤ 2 to prevent oscillation — 2411.04468 [p6]). Only fires on a hard node failure.
  - **Scope:** **local-subtree patch** — re-decompose only the failing typed node, leaving all sibling/upstream
    nodes and their injected results untouched. This is the least-divergent scope (Axis B), so it perturbs the
    trajectory minimally — **and it is the empirically-favoured scope**: H-RePlan shows local-first recovery beats
    immediate global escalation (**76.81% vs 68.89%** completion) and that adding the hierarchical-local layer over
    a global-only replanner is worth **+30.9pp** (2606.20487 S5(sa) Table 3, p.8). Do **not** use whole-plan
    regeneration (Magentic-One/OWL/LLM-Modulo scope) — it is the maximally-divergent, oscillation-prone option.
  - **State:** carry completed-node results forward unchanged (you already inject upstream→downstream) — matching
    ADaPT, which "does **not** re-execute completed sub-tasks" (contrast Reflexion's full-restart re-execution;
    2311.05772 S5(sa) p.6) and Leni's typed-artifact recovery. Do **not** clear context (avoid Magentic-One's
    global reset [p5], which maximizes cross-arm divergence).
- **How to keep it paired (mandatory):** run the replan-on arm, **log its full action-observation trajectory per
  item**, then **replay-pair** (2606.22953): feed that fixed trajectory into the replan-off arm's harness so both
  arms see the identical sequence up to the divergence, differing only in the replan flag. Analyze the
  **replan-fired subset separately** (it is unpaired w.r.t. the divergent tail) with its own wider CI; keep the
  **main paired result on the static plan** for the non-fired majority.
- **Where in the pipeline:** the trigger hooks the existing verify gate; the subtree-patch reuses the existing
  planner call path with a scoped instruction; replay logging is an append to your trajectory recorder.
- **Cost:** ~1 extra planner call on the ≤38% of items that trigger (Leni prior [p6]); modest token delta; +
  engineering for the counter, the scoped re-decompose, and trajectory replay.
- **What it breaks (be honest in the writeup):**
  - Paired variance control is **void on the replan-fired subset** → report that subset unpaired, wider CI (Q4).
  - Adds ~a handful of new code paths → budget for new tests against the 874-test baseline.
  - Changes the estimand on fired items to a **within-trajectory counterfactual** (replay-pairing caveat).
- **Do NOT** implement: per-step replanning, whole-plan regeneration, or context-reset — each maximizes cross-arm
  divergence and/or oscillation and directly attacks your variance control.

### 5C. One-line recommendation
> **Keep the static one-shot typed DAG. Treat replanning as a scoped, replay-paired *backlog ablation*, never the
> headline.** The literature says its GAIA accuracy payoff is ~0–1 pp and unproven at matched budget, while it
> would forfeit the paired variance control that makes a 103-item single-seed study statistically legible.

---

## 6. DEPTH / VERIFICATION LEDGER

**First-hand PDF reads this session (S5, page-cited):**
- 2310.01798 Huang — self-correct degrades (Tables 3–7); matched-budget debate<self-consistency. [p1–p6]
- 2411.04468 Magentic-One — ledgers −31%; stall≤2 trigger; clear/reset state on replan. [p2,p4,p5,p6,p12]
- 2605.14290 Plan-Then-Execute — 0 WebArena tasks replan-needed; 81.28% programmatic; recovery caveat. [p0,p2,p6,p7,p8]
- 2512.24145 Sharma — Thm 1 pairing=CRN; positive-corr required; negative-corr reverses. [p0,p1,p4]
- 2509.03581 Learning-When-to-Plan — always-plan degrades; oscillation/plan-drift/instability cost; Goldilocks. [p0,p3,p5,p7,p21,p22]
- 2409.02086 Klein — var[Z]=varX+varY−2cov; first-difference decorrelation; "CRN-safe". [p1,p2,p5]
- 2607.12338 replay analysis — paired per-task D_i; harness = system identity; coverage. [p0,p2,p5,p8]
- 2607.17044 Leni — GAIA replan on 38% tasks; loop isolated ∼+1 pp not isolable; matched-budget test unrun. [p0,p1,p6,p8,p14,p15]
- 2606.22953 Plans-Don't-Persist — **replay pairing**; plan decays from hidden state; eviction −34.7 pp; reasoning-trace confound. [p0,p1,p3]

**Sub-agent PDF reads (S5(sa) = verbatim PDF-string extracted by a researcher sub-agent, same pypdf method;
page = printed page):**
- 2311.05772 **ADaPT** — Try-Again(compute-matched) 47.8 → 71.6 (+23.8); §6.5/Fig 7 explicit LLM-call matching;
  local-subtree scope; does NOT re-execute completed subtasks. **[Table 1 p.6, §6.5 p.9]** — cleanest budget-matched positive.
- 2606.20487 **H-RePlan** — Table 3: full 75.84 vs w/o-Global-Replan 41.25 vs global-only 44.97; local-first
  76.81 vs escalate 68.89; Pareto-cheaper (1.93M<2.41M<3.99M tok/pass); w/o-CLFE → deeper replans abort. **[Table 3 p.9, p.8]**
- 2302.01560 **DEPS** — Table 4 round-0(vanilla)→∞ monotone (MT1 28.6→79.8 +51.2 … MT8 0.0→0.6), n=30; NOT
  budget-matched (prompt-concat token growth, cap ~7–8). **[Table 4 p.9]**
- 2305.16653 **AdaPlanner** — Fig 4a/4b monotone in #closed-loop corrections (per-point values figure-only,
  UNVERIFIED); in-plan vs out-of-plan refine; not budget-matched. **[p.8]**
- 2303.11366 **Reflexion** — +8pp over episodic-memory baseline; ALFWorld 130/134; **NULL on starchat-beta
  0.26→0.26**; full-restart scope; trial-matched not call-matched. **[p.5,7,12]**
- 2405.20625 **LLM-Modulo Travel** — Direct 4.4 → 20.6 (≤10 iters); only 20.6% converge in budget; whole-plan regen. **[Table 1 p.2–3]**
- 2304.11477 **LLM+P** — open-loop reference (no replan); classical planner Blocksworld 20→90. **[Table I p.5]**
- 2505.23885 **OWL/Workforce** — Fig 4b replan-count 0/1/2 monotone {.43,.54,.60}/{.51,.60,.69}; K=2; test-time-scaling confound; pass@k asymmetry. **[p.7,10]**
- 2402.01817 **LLM-Modulo position** — "Blocksworld 82% within 15 rounds" is a **secondhand cite of Valmeekam 2023c** (L0 vs primary). **[p.8]**
- 2603.05294 **StructuredAgent** — AND/OR-tree local revise+prune; WebArena 0.526 vs 0.464; ~3.5× wall-clock; no replan-isolating ablation. **[p.7,13]**

**L1 / search-only (NOT load-bearing; flagged inline):** 2605.23950 harness Binding-Constraint numbers (up to
15 pp scaffold-only swing; Terminal-Bench 69.7→77.0) — search-derived, not PDF-verified this session.

**Open questions / residual uncertainty:**
- **No 2026 GAIA/DeepResearch system runs a *matched-budget* replan-on/off accuracy ablation** (confirmed absent;
  Leni §10 explicitly names it as unrun) — this is the occupiable gap.
- AdaPlanner's exact per-correction success values are figure-only (Appendix 8.5 per-task) — UNVERIFIED numerically.
- OWL Fig 4b model→series mapping beyond endpoints (which curve is GPT-4o vs Claude) — UNVERIFIED.
- "Blocksworld 82%/15 rounds" — verify against primary (Valmeekam 2023c) before any load-bearing use.
- Q4's specific claim ("adaptive replanning breaks LLM-agent cross-arm pairing") is **stated by nobody verbatim** —
  the general CRN/paired principle is solid (2512.24145 Thm 1; 2409.02086); framing it for agent plans is our contribution.

---

## 主循环亲验修正(Jul-31,PDF 直读 2605.14290)

**原文实际说的**(15 页,`GAIA` 0 命中,WebArena 18 命中):
> "We analyze WebArena … and find that **all tasks are compatible with plan-then-execute**,
> while **81.28% can be completed with a purely programmatic plan**…"
> "**Not every web task can be reduced to a static script. We do not claim** …"
> "Our claim is that **reactive replanning should be reserved for tasks that actually require it**."

**⚠️ 修正**:本档转述的"WebArena 无一任务需要重规划"**偏强**。原文是**可行性**主张
(控制流可事前确定 / 兼容 plan-then-execute),且**动机是安全**(隔离不可信数据流与
控制流生成、防 prompt injection),**不是精度对照实验**;并自带 hedge。
⇒ 可引作"静态计划在 web 任务上控制流可事前确定(WebArena,可行性+安全动机)",
**不可引作"重规划不提升精度"**。

## 证据态势终判(合并 DR-G 修正后)

四条"反重规划"证据逐条收窄后:①Leni GAIA ≈+1pp 且自认无法干净隔离、无配平对照;
②Magentic-One −31% 是**捆绑消融**;③PLANAHEAD 对照面是**单体动态**;
④Plan-Then-Execute 是**可行性/安全**主张。⇒ **文献并未裁决"训练自由体制下
planner-executor 内部 重规划 vs 静态"的精度问题——GAIA 与 WebArena 均空白。**

**故我方"保持静态"的论文措辞必须是**:立于 ①跨臂配对可比性(方法学,本档 Q4 已给
统计学依据与 replay-pairing 解法)②成本 ③工期与既有管线风险 ④静态在 web 控制流上
的可行性有据(2605.14290)——**不得写成"文献表明重规划无益"**。

## ⚠️ 流程教训(两连中)

DR-G 与 DR-H 各有一处把原文往"支持静态"的方向读强了一格,而两份任务书都写明了
主循环倾向静态。**推断:任务书里的倾向会锚定子代理的转述。**
**对策**:①今后研究任务书**不写主循环的倾向**,只写待答问题;②凡"用起来特别顺手"
的引句,主循环一律 PDF 亲验后才可承重。
