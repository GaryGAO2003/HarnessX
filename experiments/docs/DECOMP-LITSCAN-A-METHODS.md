# DECOMP-LITSCAN-A: Task-Decomposition Methods vs D1-lite (2025-01 .. 2026-07)

**Scope of this scan:** LLM-agent task-decomposition / planner-executor / hierarchical / typed / adaptive
decomposition methods published 2025-01 to 2026-07, judged against our constraints.
**D1-lite** = HuggingGPT-style typed-slot schema + serial/DAG decomposition (our incumbent).
**Judge axes (all three must hold to "beat" D1-lite):**
(A) simple + reproducible (~350-550 LOC new module, few days);
(B) GAIA-level web/tool empirical numbers (GAIA / WebArena / BrowseComp / AssistantBench);
(C) cheap = inference-time only (no train/finetune), tiny budget, DeepSeek-flash-tier worker.
Extra requirement: decomposition output must carry **routable typed subtask labels** (to dispatch to
different harness-config variants = division of labor).

Author's note on evidence discipline: each load-bearing row carries a depth tag L1 (abstract only) /
L2 (skimmed body) / L3 (read key sections). Numbers are quoted with bench name + n. Two search-summary
findings (Gaia2, MiroFlow/OxyGent headline scores) are explicitly marked L1/secondhand and are NOT used
as load-bearing for the verdict.

---

## 1. VERDICT (decision-first)

**D1-lite stands. No 2025-2026 method beats it on all three axes (A simple + B GAIA-level + C cheap)
simultaneously, and none delivers cheap training-free *typed* subtask routing with GAIA-level proof.**

The 2025-2026 literature splits cleanly into two non-overlapping buckets, and the gap between them is
exactly where D1-lite lives:

- **Bucket 1 — simple + training-free + cheap, but validated OFF GAIA-level web/tool benches.**
  TDP (2601.07577), AdaptOrch (2602.16873), and E3 (2607.13034) are all training-free, prompt/algorithm-
  only, and cheap. But their evidence is on TravelPlanner / ScienceWorld / HotpotQA (TDP), SWE-bench /
  GPQA / HotpotQA (AdaptOrch), and a synthetic coding file-edit bench MSE-Bench (E3). None reports a
  GAIA / WebArena / BrowseComp number. Their demonstrated benefit is dominated by **cost/token reduction
  at roughly equal accuracy**, not an accuracy jump (see §3).

- **Bucket 2 — GAIA-level numbers, but full frameworks (fail axis A) and/or trained (fail axis C), and
  where decomposition is *ablated* it is not the lever.** JoyAgent-JDGenie (GAIA val 75.2 pass@1),
  MiroFlow (74.5), OxyGent (59.1), AgentOrchestra (83.4, prior knowledge) are whole systems, not
  350-550 LOC modules. JoyAgent's own ablation shows **multi-agent decomposition alone (70.3 avg) is
  *worse* than a single ReAct agent (71.5 avg)** on GAIA; its gain comes from paradigm fusion + posterior
  voting. This echoes our prior-knowledge findings (AgentOrchestra gain from Tool-Generator, not
  decomposition; Magentic-One -31% only when the ledger is removed).

- **The one method that does exactly our target — typed routing / division of labor on GAIA —
  requires heavy training and still does not beat a non-routed baseline on accuracy.** Uno-Orchestra
  (2605.05007) emits a typed `(model, primitive)` routing pair per subtask (the precise pattern we want),
  scores **GAIA 82.0 vs AgentOrchestra 83.4 (it loses on accuracy)**, wins only on **cost (~$0.10 vs
  $1.21/query, ~10x)**, and needs SFT on 61,201 trajectories + Agentic-GRPO RL on 2,976 questions —
  disqualified by axis C. Training-free typed division-of-labor does exist (OneManCompany, 2604.22446)
  but only off-GAIA (PRDBench software projects, $6.91/task).

**Consequence for us:** D1-lite (typed-slot + serial/DAG, training-free) is the *unfilled cheap cell*:
the simple/cheap papers drop typed routing and skip GAIA; the GAIA/typed-routing papers require training
or a full framework. The highest-value *optional* upgrade with real (if off-GAIA) evidence is a
**difficulty gate** — decompose only hard items, run easy items flat — borrowed training-free from E3 /
complexity-aware routing; but its GAIA-level accuracy payoff is unproven, so it is an efficiency hedge,
not an accuracy win. Recommendation: keep D1-lite as the decomposition core; treat difficulty-gated depth
as a cheap ablation arm, not a replacement.

---

## 2. CANDIDATE TABLE (mechanism / bench+n / cost / code / depth)

| # | Method (arXiv, date) | Mechanism (1 line) | Train-free? | Bench + numbers (n) | Cost/latency | Code | Typed routing? | Depth |
|---|---|---|---|---|---|---|---|---|
| 1 | **TDP – Task-Decoupled Planning** (2601.07577, 2026-01) | Supervisor builds DAG of sub-goals; single Planner-Executor with node-scoped context; 6 prompts | Yes | TravelPlanner 34% vs ReAct 30% / Plan-and-Act 25% (n=120); HotpotQA 85.88 vs 72.74 (n=100); ScienceWorld 53.24 vs 49.69 (n=100) | up to **-82% tokens** | not stated | **No** (single executor, no typed labels) | L3 |
| 2 | **AdaptOrch** (2602.16873, 2026-02) | Compute DAG width/depth/coupling → threshold rules → pick parallel/sequential/hierarchical/hybrid pattern | Yes | SWE-bench Verified 52.6 vs 42.8 single-best (n=500); GPQA-Diamond 53.1 vs 46.2 (n=198); HotpotQA-F1 76.4 vs 68.3 (n=500) | ~half MoA-3L tokens; routing <50ms/task | github/adaptorch | Pattern-level only (not subtask-type→executor) | L3 |
| 3 | **E3 – "Do AI Agents Know When a Task Is Simple?"** (2607.13034, 2026-07) | Estimate difficulty (lexical + 1 probe) → minimum-viable scope 1-3 → Expand on verify-fail | Yes | MSE-Bench coding/file-edit (n=121): 100% success at **-85% cost** vs max-context; tougher Adaptive-Retrieval baseline also 100% (accuracy Δ≈0) | -91% tokens, -58.6% latency vs max-context | github (released) | No (scope levels, not types) | L3 |
| 4 | **JoyAgent-JDGenie** (2510.00510, 2025-10) | Heterogeneous ensemble: Plan-Execute + ReAct + posterior voting | Yes (inference) | **GAIA val 75.2 p@1 / 82.4 p@3, test 67.1 p@1** (n=165 val / 300 test). Ablation: single ReAct 71.5 > **multi-agent 70.3** < fusion 75.2 | not isolated | jd-opensource/joyagent-jdgenie | No | L2 |
| 5 | **Uno-Orchestra** (2605.05007, 2026-05) | One policy emits plan + typed `(model, primitive)` routing pair per subtask (selective delegation) | **No** (SFT 61,201 traj + GRPO 2,976 Q) | **GAIA 82.0 vs AgentOrchestra 83.4 (loses)**; macro 77.0 across 13 benches | **$0.10/query vs $1.21 (~10x cheaper)** | CuiZHIQ/Uno-Orchestra + HF data | **Yes** (but trained) | L2 |

**Also-ran / ruled out (brief):**
- **OneManCompany / "From Skills to Talent"** (2604.22446, 2026-04) — training-free typed division-of-labor
  (Talent-Container, 6 typed interfaces, E²R tree + DAG). PRDBench 84.67% SR (n=50 software projects),
  $6.91/task. **No GAIA; software-dev domain; not cheap.** [L2]
- **Plan-and-Act** (2503.09572, ICML 2025) — trained Planner+Executor. WebArena-Lite 57.58%, WebVoyager
  81.36%. **Training-based → fails C.** [L1]
- **WORKFORCE / OWL** (2505.23885) — hierarchical planner/executor, GAIA 69.70%; but "Optimized Workforce
  Learning" = **training → fails C**; full framework → fails A. [L1]
- **MiroFlow** (2602.22808) — GAIA val **74.5 p@1 / 82.4 p@3**; full deep-research framework, not a module
  → fails A; no decomposition ablation surfaced. [L1, headline via search]
- **OxyGent** (2604.25602) — DeepSeek-R1 master orchestrates decomposition; GAIA **59.14%**; full
  multi-agent platform → fails A. [L1, headline via search]
- **AgentOrchestra** (2506.12508) — GAIA 83.39% (prior knowledge; gain from Tool-Generator not decomp). [prior]

---

## 3. DYNAMIC / ADAPTIVE DECOMPOSITION (专节)

Question: does "adjust decomposition granularity/structure by executor-capability / difficulty / runtime
feedback" beat a fixed typed-DAG, with evidence?

**What the evidence actually shows — the gain is efficiency, not GAIA-level accuracy:**

- **By difficulty (E3, 2607.13034, L3):** adaptive execution-scope cuts cost 85% and tokens 91% at
  **equal 100% success** on MSE-Bench (n=121). Crucially the tougher adaptive-retrieval baseline *also*
  hits 100% — so adaptivity's **accuracy delta ≈ 0; the entire win is cost**. Domain = coding file-edits,
  not GAIA. Estimator accuracy degrades 85.1%→66.9% on paraphrased tasks but success stays 100% (robust).

- **By structure (AdaptOrch, 2602.16873, L3):** routing DAG-shape → orchestration pattern beats
  Static-Parallel and Static-Sequential baselines and single-best by +6.9 to +9.8 pp on SWE-bench/GPQA/
  HotpotQA at ~half the tokens. This is the cleanest "adaptive > static decomposition" evidence in scope —
  **but off GAIA/web, and it routes to a *pattern*, not to typed heterogeneous executors.** Training-free,
  O(V+E), <50ms overhead — cheap and simple to port.

- **By runtime uncertainty (WebUncertainty, 2604.17821, L1 — abstract only, PDF wouldn't extract):**
  task-uncertainty → adaptive planning; action-uncertainty → **MCTS** reasoning. Tested on WebArena +
  WebVoyager, claims "superior" but no numbers extracted. **MCTS = many rollouts → likely too expensive
  for our tiny budget.** Code: github.com/windbd/WebUncertainty. Not verifiable as a cheap win.

- **By executor capability (Gaia2, 2602.11964, L1 — secondhand search summary, PDF wouldn't extract):**
  reported finding that Agent2Agent sub-task decomposition / collaboration **benefits weaker models
  (Llama 4 Maverick) more than frontier models**, improving pass@k and tool-call stability; GPT-5(high)
  tops at 42% pass@1. This is *encouraging* for a DeepSeek-flash worker (weak-tier → decomposition may
  help more), but it is pass@k scaling via multi-agent collaboration, not a clean adaptive-granularity
  delta, and it is secondhand — **NOT load-bearing.** Worth a primary-source re-read if we lean on it.

- **Complexity-aware routing family (named in E3's related work, L2 via E3):** BoundaryRouter (LLM-vs-agent
  escalation), Select-then-Solve (route task→reasoning paradigm, "no single paradigm dominates"), Ares
  (2603.07915, per-step reasoning-effort, up to -52.7% reasoning tokens). All are *routing over a fixed
  menu*, orthogonal to *decomposition*; all report efficiency wins, none a GAIA decomposition-accuracy win.

**专节 conclusion:** adaptive decomposition is real and training-free-portable, but its proven payoff is
**cost/token efficiency at ~equal accuracy**, demonstrated **entirely off GAIA-level web/tool benches**.
No paper shows "adaptive decomposition granularity beats a fixed typed-DAG on GAIA-level tasks, cheaply,
training-free." For us: a **difficulty gate** (E3-style: decompose hard, run easy flat) is the lowest-risk,
cheapest borrow and fits the 350-550 LOC budget as an ablation arm; adaptive *structure* routing (AdaptOrch
threshold rules) is a second cheap borrow. Neither is proven to raise GAIA accuracy — adopt as hedges, not
as the primary bet.

---

## 4. SURVEYS / POSITION PAPERS (related-work taxonomy anchors)

| Survey (arXiv/venue, date) | Taxonomy framing (1 line) | Depth |
|---|---|---|
| **Understanding the Planning of LLM Agents: A Survey** (2402.02716, 2024) | Canonical 5-part planning taxonomy: **task decomposition / multi-plan selection / external-planner-aided / reflection / memory**; decomposition split into **decomposition-first vs interleaved**. The anchor for our "typed-DAG = decomposition-first" positioning. | L1 (well-known) |
| **Agentic AI: Architectures, Taxonomies, and Evaluation of LLM Agents** (2601.12560, 2026-01) | Planning under "Cognitive Architecture"; reasoning-topology axis = **linear (CoT) / interleaved (ReAct) / tree (ToT) / hierarchical (ReAcTree recursive sub-agents) / inference-time (o1/o3)**. Cites GAIA & WebArena qualitatively (WebArena <15% long-horizon), no decomposition-specific numbers. Best 2026 topology anchor. | L2 |
| **A Survey of Task Planning with LLMs** (Intelligent Computing / icomputing.0124) | Journal survey of LLM task planning; decomposition via chain/tree/PDDL. | L1 |
| **Agent Planning Benchmark: A Diagnostic Framework** (2606.04874, 2026-06) | Diagnostic framework isolating planning sub-capabilities — useful if we need a planning-only diagnostic. | L1 |

Taxonomy placement for our related-work: D1-lite = *decomposition-first, hierarchical, typed-slot, DAG,
training-free, single-planner* — sits at the intersection HuggingGPT (typed slots) × TDP (training-free DAG
+ scoped context) × the division-of-labor routing that only Uno-Orchestra/OneManCompany attempt (and both
step outside our constraints).

---

## 5. SEARCH LOG (queries used)

WebSearch queries:
1. `plan-and-act LLM agent task decomposition GAIA WebArena 2025 arxiv`
2. `adaptive task decomposition LLM agent difficulty-aware 2025 2026 arxiv`
3. `hierarchical planner executor agent GAIA benchmark 2026 arxiv`
4. `survey task decomposition planning LLM agents 2025 2026 taxonomy`
5. `training-free planner executor agent GAIA tool-use inference-time decomposition 2026`
6. `task decomposition subtask routing heterogeneous agents division of labor GAIA 2026 arxiv`
7. `Beyond Entangled Planning Task-Decoupled Planning Long-Horizon Agents arxiv 2601.07577`
8. `runtime feedback adaptive decomposition granularity agent WebArena BrowseComp 2026`
9. `Gaia2 benchmark decomposition depth weaker models Llama Claude finding "task decomposition" performance`
10. `GAIA training-free plan-execute typed subtask decomposition pass@1 2026 arxiv DeepSeek`
11. `MiroFlow GAIA validation pass@1 score OxyGent GAIA score benchmark open source agent`

WebFetch (primary-source reads):
- arxiv.org/abs/2601.07577 + /html/2601.07577v1 (TDP) — L3
- arxiv.org/html/2602.16873v1 (AdaptOrch) — L3
- arxiv.org/html/2607.13034v1 (E3) — L3
- arxiv.org/html/2510.00510 (JoyAgent-JDGenie) — L2
- arxiv.org/html/2605.05007v1 (Uno-Orchestra) — L2
- arxiv.org/html/2604.22446v1 (OneManCompany) — L2
- arxiv.org/html/2601.12560v1 (Agentic AI survey) — L2
- arxiv.org/abs/2604.17821 (WebUncertainty) — L1 (abstract; full PDF would not extract)
- arxiv.org/pdf/2602.11964 (Gaia2) — L1 (PDF would not extract; finding via search summary, secondhand)
- arxiv.org/pdf/2602.22808 (MiroFlow), /pdf/2604.25602 (OxyGent) — L1 (PDF would not extract; headline via search)

**Depth distribution:** L3 = 3 (TDP, AdaptOrch, E3) · L2 = 4 (JoyAgent, Uno-Orchestra, OneManCompany,
Agentic-AI survey) · L1 = 6+ (WebUncertainty, Gaia2, MiroFlow, OxyGent, Plan-and-Act, OWL, planning surveys).
All verdict-load-bearing rows (TDP, AdaptOrch, E3, JoyAgent, Uno-Orchestra) are >= L2. Secondhand items
(Gaia2, MiroFlow/OxyGent headline scores) are flagged and excluded from the load-bearing verdict chain.

**Not yet chased (open questions for a follow-up pass):** (a) primary-source Gaia2 table for the
weak-model-decomposition delta (currently secondhand); (b) AdaptOrch's explicit static-decomposition
deltas (I have single-best deltas, not the Static-Parallel/Sequential row values); (c) whether any
BrowseComp/AssistantBench paper reports a decomposition-vs-flat delta (none surfaced with numbers);
(d) AOrchestra (2602.03786, "Automating Sub-Agent Creation") and IFD (2503.23053, training-free subtask
interaction) left at L1 — both appear off-GAIA / full-system, unlikely to move the verdict.
