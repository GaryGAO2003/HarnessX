# DR-D — N1 Rivals, PDF-Direct Deep Read (gold standard)

> **Method**: arXiv PDFs downloaded and text-extracted locally with pypdf 6.14.2 (no
> WebFetch summarizer in the loop). Every load-bearing quote carries a page number
> located in the extracted text (page markers re-derived from the PDF page tree).
> Hyphenation artifacts from PDF extraction normalized when quoting.
> **Date**: 2026-07-30 · researcher (Opus 4.8) · under `research-guardrails` (enforce).
> **Scope**: AOrchestra 2602.03786 (N1 α-placeholder), Uno-Orchestra 2605.05007,
> plus verbatim upgrade of JoyAgent 2510.00510 and MiroFlow 2602.22808 (were DR-B
> summarizer-mediated). Compared against NOVELTY-TIERS.md §"深读修订" and
> DECOMP-METHOD-CATALOG.md ERRATA.
>
> **Depth**: all four read at S5 (verbatim quote in hand) for the asked items.

---

## 1. AOrchestra 2602.03786 — "Automating Sub-Agent Creation for Agentic Orchestration"

**Setup card.** DeepWisdom / HKUST(GZ) / RUC / ECNU / UdeM & Mila; corr. Yuyu Luo,
Jiayi Zhang. **MetaGPT/FoundationAgents lineage** (repo `FoundationAgents/AOrchestra`).
Preprint **February 10, 2026** (p.1). Headline: **16.28% relative improvement** over the
strongest baseline with **Gemini-3-Flash**, across GAIA / SWE-Bench-Verified /
Terminal-Bench 2.0, in a **training-free** setting (p.3 lines 221-226). SFT and ICL are
*optional add-ons*, not the headline.

### 1a. Decomposition mechanism / subtask format / type system
- Core abstraction: an agent instance is an **instantiable four-tuple Φ = (I, C, T, M)** —
  I = instruction (objective + success criteria), C = curated working context, T = tool set,
  M = model (**p.4 line 377**). "This abstraction explicitly separates ... **working memory
  (I, C)** and **capabilities (T, M)**" (p.4 line 383-384).
- Delegation: `Delegate` takes Φt=(It,Ct,Tt,Mt) and **instantiates an executor** that
  "runs with model Mt, is restricted to the tool set Tt, and conditions only on (It,Ct)"
  (p.5 line 410-413). The subtask itself is a **free-text instruction** — Fig 3 caption:
  the orchestrator "**Formulates clear, self contained, actionable success criteria**"
  (p.4 line 320). **No subtask type system** — structure lives in the 4 executor slots,
  not in a discrete subtask taxonomy.
- Action space is **exactly {Delegate(Φ), Finish(y)}** and the orchestrator "**never
  directly takes environment actions**" (p.5 line 398-401). Decomposition is
  **adaptive / per-step (interleaved)**, no upfront plan.

### 1b. "fresh container / previous work will be lost" — PDF re-anchor
Location: **p.12, Appendix §B.1.2 "Terminal-Bench Main Agent Prompt"** (lines 1095-1097):

> CRITICAL: CONTAINER LIFECYCLE
> - Each SubAgent runs in a FRESH container - if you delegate_task again, the previous work will be lost
> - When SubAgent reports status="done", use 'submit' immediately to run tests in that container

This is a **Terminal-Bench sandbox** instruction: each `delegate_task` spawns a fresh
Docker container; sub-agent work does not even persist across delegations *within a task*.
Stronger than "no cross-task pool."

### 1c. Full-text persistence scan (placeholder final judgment — body level)
Every persistence-adjacent hit accounted for:
- **"working memory (I,C)"** = per-step tuple component, within-task (p.4 l.384; p.7 l.468).
- **"memory" field** in prompts = within-episode scratchpad ("Write key reasoning in memory
  for future steps", p.15 l.1283).
- "persisted it into the context for Attempt 6" (p.19 l.1440) = within-task, across *attempts*
  of the same task, carried via context.
- "we **cache** retrieved web content ... " is scoped to **"Reproducibility"** (p.25 l.1858)
  — a web-content cache for logging, not a skill/tool library.
- **Table 4 "reusability"** (p.8 l.768-770) = plug-and-play swapping of sub-agent
  *implementations* (ReAct-style vs Mini-SWE-style) under a fixed orchestrator — a
  framework-agnostic property, **not** cross-task memory.
- Black-swan sweep (skill/pool/accumulate/library/bank/repository/catalog): "orchestration
  **skill**" = abstract capability (p.2 l.183, p.8 l.759); "mixed-LM **pool**" = set of models
  to route among (p.8 l.699); "**accumulated** history" = within-trajectory (p.4 l.294);
  "catalog(ue)" = museum content in GAIA tasks. **No persistent skill/tool library, no
  cross-task memory bank, no experience pool.**
- The only cross-task artifacts are **offline** ones: SFT weights of the orchestrator, or an
  ICL-optimized orchestrator prompt. Both are fixed, not online-evolving.

**Placeholder verdict: CONFIRMED (strong).** AOrchestra is fresh-spawn-and-discard with no
persistent pool. It occupies only the **"fresh-spawn" adjacent cell** of N1's four-way
conjunction; the "training-free **evolving persistent pool**" axis is unoccupied.

### 1d. SFT (+11.51%) protocol and GAIA 80.0 protocol
- **SFT protocol** (p.6 l.569-576): fine-tune **Qwen3-8B** orchestrator (non-thinking mode);
  seed dataset **TaskCraft**; **Gemini-3-Flash collects 2K orchestration trajectories**;
  **full-parameter FT under LLaMA-Factory, 2 epochs, lr 1e-5**. SFT "primarily distills task
  orchestration: improving subtask decomposition and the synthesis of (It,Ct,Tt)" (p.7 l.466).
- **+11.51% = 68.48 − 56.97** exactly (Table 3, p.8 l.714-715 + narrative l.751-753):
  a **weak Qwen3-8B orchestrator** improves GAIA acc **56.97 → 68.48** (Gemini-3-Flash
  sub-agent). It does **NOT** lift the 80.0 headline (which uses a Gemini-3-Flash orchestrator,
  training-free). "increasing the total number of attempts by 56%" (p.8 l.757-758) → the SFT
  gain buys longer horizons, at +$0.32/task.
- **ICL +3.03% = 75.15 − 72.12** (Mixed-model track, p.8 l.712-713, l.783-784), cost
  0.70→0.57 (**−18.5%**). Separate track from SFT.
- **GAIA 80.0 protocol**: "**GAIA validation split, ... 165 tasks**" (p.11 l.1000; p.18
  l.1413-1415), **pass@1 = 80.00 / pass@3 = 86.06**, **Gemini-3-Flash as both orchestrator
  and sub-agent**, training-free. Best baseline OpenHands 66.06 → +13.94 absolute (p.7
  l.605-610). (Terminal-Bench 2.0 test = 89 tasks; SWE-Bench-Verified = sample 100 of 500,
  p.11 l.1003-1008.)

---

## 2. Uno-Orchestra 2605.05007 — "Parsimonious Agent Routing via Selective Delegation"

**Setup card.** Zhiqing Cui (project lead, NUIST) et al. — **different lineage from
AOrchestra**. arXiv:2605.05007v1, **6 May 2026** (p.1). Repo `CuiZHIQ/Uno-Orchestra`,
dataset `Uno-Curriculum`. Headline: **77.0% macro pass@1** across a **13-benchmark** suite
vs 22 baselines, "**roughly 16% above the strongest workflow baseline** [= AgentOrchestra],
at roughly an **order of magnitude lower per-query cost**" (abstract; p.7 l.407-408). GAIA is
one of the 13.

### 2a. Typed (model, primitive) routing — full type system
- **Single causal-LM router** (Qwen2.5-7B-Instruct, p.6 l.382) emits a **subtask DAG**
  Pt=(Vt,Et). Every node carries: (a) a **free-form NL description**, (b) an **admissible
  routing pair (m,s) ∈ P**, (c) dependency edges (p.17 l.950-952).
- **Workers M** = **closed pool of frozen worker LLMs**; **primitives S** = **closed
  vocabulary** of routing primitives; **admissible set P ⊆ M × S** of pairs p=(m,s), each with
  a token-level cost (p.3 l.120-123). Main pool = **9 commercial workers** spanning >2 orders
  of magnitude in price (Gemini-2.5-Flash-Lite … Claude-Opus-4-6; p.6 l.383-385).
- **Primitive vocabulary S** (Table 6, **p.17 l.925-944**): 5 semantic clusters —
  **Answer&reason** (direct_answer, reason), **Retrieve** (web_search, database_query,
  fact_check), **Skills** (read_document, read_code, extract_field, parse_structured),
  **Execute** (execute_python, execute_shell, call_api), **Symbolic** (symbolic_math).
  Each `<route>` tag commits to one primitive (p.17 l.920).
- So each subtask is **typed by (which of 9 models) × (which of ~13 primitives)** plus a
  free-form description. **Richer type system than AOrchestra** (which has no primitive taxonomy).
- **Pool is frozen/closed at inference** (p.3 l.120 "closed pool of frozen worker LLMs";
  p.17 l.911 "nine frozen large language models"; p.28 l.1380 pool only "reduced for an
  ablation"). **No inference-time evolution / persistence.** Uno does **not** occupy N1's
  "evolving pool" cell either.

### 2b. Training pipeline (SFT 61k + GRPO)
- Two stages (p.2 l.71-76): **Stage 1 SFT** on a **verifier-gated curriculum of 61,201
  teacher-distilled trajectories** from **38 public datasets disjoint from eval**, keeping only
  "correctly decomposed gold-aligned trajectories" (p.4 l.234-235). **Stage 2 Agentic-GRPO**
  — a multi-turn GRPO extension with observation/process-level **intermediate rewards**.
- RL detail (p.6 l.376-381): 2,976 verifier-filtered questions, G=8 rollouts, Tmax=8 turns,
  16,384-token decode cap, AdamW 1e-6, PPO clip 0.2, KL β=1e-3, on **a single 8×A100-80GB
  node**. **"61k + GRPO" CONFIRMED**, now precise.

### 2c. GAIA 82.0 protocol + **which AgentOrchestra version it compares to** (the audit)
**Uno re-runs ALL baselines under a "unified rollout harness" with a shared 9-worker pool**
(p.6 l.375-376, l.398). The tell: **Uno's own reproduction of AOrchestra lands at GAIA
pass@1 = 69.4** (Table 2, p.8 l.470) versus AOrchestra's self-reported 80.0 — proof these are
genuine reproductions, not citations.

Per-benchmark GAIA (Uno's controlled harness):

| Regime | AOrchestra [50] | AgentOrchestra [77] | Uno-Orchestra |
|---|---|---|---|
| **pass@1** (Table 2, p.8 l.470-472) | **69.4** | **83.4** | **82.0** |
| **pass@2** (Table 11, p.24 l.1216-1226) | 77.1 | **88.7** | **87.0** |

- Ref [77] = "**Agentorchestra**: … Wentao Zhang, Ce Cui, … Bo An" (p.15 l.812) = AgentOrchestra
  (TEA) = **2506.12508**. So Uno's **83.4 is its own controlled pass@1 reproduction**, and
  Uno **independently reproduces AgentOrchestra pass@2 = 88.7**, which nearly matches
  AgentOrchestra's self-reported **89.04**. → The apparent 83.4↔89.04 gap is a
  **pass@1-vs-pass@2 / best-config** difference reproduced faithfully within Uno, **not a
  stale-version citation**.
- **On GAIA, Uno LOSES to AgentOrchestra at both budgets**: pass@1 82.0 < 83.4 (Rel. gain
  **−1.7%**, p.8 l.473) and pass@2 87.0 < 88.7. Uno also loses SWE (−0.7%); wins big on AIME
  (+80.2%), LCB (+54.9%), GPQA (+26.5%), MRCR (+22.6%). GAIA split not restated as full/text;
  standard GAIA, metric pass@1/pass@2 ("single attempt" / "one allowed retry", p.6 l.369).

### 2d. "10× cheaper" cost caliber
- **Macro (13-bench avg)**: Uno-Orchestra **$0.1011/q** (411 tok) vs AgentOrchestra
  **$1.2118/q** (1724 tok) → **~12× ≈ "order of magnitude"** (Table 1, p.7 l.444, l.451).
  AOrchestra macro = $0.9932 (p.7 l.443). "roughly an order of magnitude lower per-query cost"
  (p.1 l.88; p.9 l.548).
- **GAIA-specific**: AgentOrchestra GAIA = **$0.8935/q** (Table 12, p.24 l.1245); Uno family
  cells "every (method, benchmark) cell falling in the **$0.1–$0.2 range**" (p.25 l.1255) →
  GAIA ratio is only **~5×**, not 10×. The headline "10×" is the **macro** figure; on GAIA
  alone it is ~5×.

---

## 3. JoyAgent-JDGenie 2510.00510 — verbatim upgrade (was DR-B summarizer-mediated)

**Setup card.** JINGDONG CHO-EI Team. **October 2, 2025** (p.1). Repo `jd-opensource/joyagent-jdgenie`.
Fusion of Plan-Execute + ReAct agents via **critic-model posterior voting**; hierarchical memory
(working/semantic/procedural); tool suite. GAIA: **75.2 Pass@1 / 82.4 Pass@3 (validation),
67.1 Pass@1 (test)** (abstract, p.1 l.16-18).

### Table 2 (GAIA, **p.6 l.252-260**) — verbatim
Caption: "The Fusion refers to fusing **Single and Multiple (3)** with an additional critic model."

| Model | **Average** (=pass@1) | Level 1 | Level 2 | Level 3 |
|---|---|---|---|---|
| **Single** | **71.5** | 84.9 | 75.6 | 30.8 |
| Multiple (2) | 69.9 | 80.6 | 74.4 | 33.5 |
| **Multiple (3)** | **70.3** | 81.1 | 74.4 | 34.6 |
| Multiple (4-A) | 52.7 | 56.6 | 58.1 | 26.9 |
| Multiple (4-B) | 58.8 | 60.3 | 65.1 | 34.3 |
| **Fusion** | **75.2** | 86.6 | 77.9 | 42.3 |

"Average" = overall GAIA-validation pass@1 (Fusion 75.2 matches the abstract). Base model =
**Claude-4-sonnet** (Table 3, p.7 l.316: Claude-4-sonnet Average = 75.2 = the Fusion row;
p.7 l.279-280 "Claude-family … Claude-4-sonnet … 75.2"; Ours = "Claude-4 + o4-mini", Table 1
p.5 l.238).

### "Multiple(3)" vs four-role naming — mystery SOLVED (p.6 l.261-270)
- Four agent roles: **Plan / Retrieval / Logic / Browser** (p.6 l.261-263). **No "Supervisor"**
  role (CATALOG's "Supervisor+…" label is slightly off; the extra piece is the separate
  **Critic** model used in Fusion voting).
- The **"(N)" is the agent count**: **Multiple(2)=Plan+Retrieval; Multiple(3)=Plan+Retrieval+Logic;
  Multiple(4/5)=all agents** (p.6 l.266-267). So **Multiple(3) deliberately EXCLUDES the Browser
  agent** — "After introducing the browser agent, the system performance exhibits significant
  deterioration" (p.6 l.270); Multiple(4-A/B) collapse to 52.7/58.8. Fusion is built from
  Single + **Multiple(3)** (the best-behaving decomp config) + Critic (p.6 l.271-273).

### Single-vs-Multiple resource asymmetry (original text)
- Single = one basic **ReAct** with all tools except browser-use, longer max steps
  (p.6 l.247-249). Multiple(3) = 3 role-agents. **Fusion runs Single AND Multiple(3) AND a
  Critic** → most compute. Posterior voting "can be configured with **3 or 5 models depending
  on resource availability**" (p.3 l.124). ⇒ the winning arm (Fusion 75.2) and the decomp arm
  (Multiple(3), 3 agents) both use **≥ compute than Single (1 ReAct)**, yet **pure decomp still
  loses to Single** (70.3 < 71.5). Decomposition wins **only when fused with the single agent
  via voting** — the N3 interaction, not "decomposition per se."

---

## 4. MiroFlow 2602.22808 — verbatim upgrade (was DR-B summarizer-mediated)

**Setup card.** Tsinghua + MiroMind AI + NUS + Nanjing U (Jifeng Dai et al.). Open-source
"deep research" agent framework: agent graph, optional heavy-reasoning mode, robust workflow.
**Evaluation protocol (p.6 l.557-563)**: "For **GAIA-Val-Text, which consists of 103 text-only
questions from the GAIA-Val set**", LLM-as-judge; "**all reported metrics are avg@3 (averaged
over three runs)**." → MiroFlow's GAIA numbers are **avg@3 on 103 text-only tasks, NOT pass@1.**
Headline default (Table 1, p.7 l.616): MiroFlow(GPT-5) GAIA-Val = **71.90**, GAIA-Test = 79.90.

### Table 5 (Single- vs Multi-Agent ablation, **p.8 l.667-672**) — verbatim
Caption: "**Multi-agent settings perform better on most benchmarks but not on GAIA.**"

| Setting | GAIA-Val | BC-200 | HLE-200 |
|---|---|---|---|
| **Single-Agent** | **74.8** | 63.9 | 40.6 |
| **Multi-Agent** | **71.9** | **68.3** | **42.0** |

- GAIA: Single **74.8 > 71.9** Multi (+2.9). BrowseComp-200: Multi **68.3 > 63.9** Single.
  HLE-200: Multi **42.0 > 40.6** Single. **All six numbers CONFIRMED.**
- "**better on most benchmarks but not on GAIA**" quote = **Table 5 caption, p.8 l.668-669.**
- Mechanism (p.8 l.693-699): GAIA "**strongly sequential task structure: multi-agent
  decomposition increases the risk of mistake propagation across sub-agents, whereas a
  single-agent model maintains a continuous reasoning trajectory**."
- **Budget note**: Multi-agent gets the per-node turn limit applied to *each* of main+sub
  (p.8 l.710-713) → decomp arm has **≥ total budget** and still loses on GAIA. Supports the
  "≥compute, still loses" reading. Metric = avg@3 on 103 tasks, std-dev ~1.2% (Table 3 p.7
  l.633) → the 2.9pp GAIA gap is suggestive, not large; single-source caveat stands.

---

## 5. Verdicts vs existing assertions (CONFIRMED / WEAKENED / REVERSED)

### vs NOVELTY-TIERS.md §"深读修订" + N1/N3
| Claim | Verdict | Note |
|---|---|---|
| N1 α-placeholder: AOrchestra = fresh-spawn / discard / no pool / offline-trained only | **CONFIRMED (strong)** | Body + p.12 container quote + black-swan sweep. Occupies only the "fresh-spawn" adjacent cell; "training-free evolving persistent pool" axis unoccupied. |
| N1: Uno = post-trained expert, no evolving pool | **CONFIRMED** | SFT-61k + Agentic-GRPO on 8×A100; closed frozen 9-worker pool, no inference-time evolution. |
| N1: "matched-budget test = the four rivals' common methodological void" | **CONFIRMED / reinforced** | None do matched compute; JoyAgent & MiroFlow decomp arms use **≥** compute and still lose on GAIA. |
| Fact-chain JoyAgent "单 71.5 > 分解 70.3, Claude-4-sonnet, val pass@1" | **CONFIRMED verbatim** | Table 2 p.6; base = Claude-4-sonnet; "Average" = val pass@1. |
| Fact-chain MiroFlow "GPT-5, val-text 103" | **CONFIRMED + refined** | 103 text-only confirmed (p.6 l.559); **metric = avg@3, not pass@1** (add this). |
| N3: MiroFlow 74.8>71.9 only on GAIA; multi wins BrowseComp/HLE; GAIA sequential → propagation | **CONFIRMED verbatim** | Table 5 p.8 + mechanism l.693-699. |
| N3: "decomp hurts" as a general claim | **WEAKENED (as expected by N3)** | JoyAgent shows pure decomp loses **but decomp+single Fusion WINS (75.2)** → decomposition's value is conditional (exactly N3's ④−③>0), not "decomp is bad." |

### vs DECOMP-METHOD-CATALOG.md
| Row / errata | Verdict | Note |
|---|---|---|
| AOrchestra row: "80.0 p@1 (Gemini-3-Flash)" | **CONFIRMED** | GAIA val 165, pass@1, Gemini-3-Flash both roles. |
| "per-step 4-tuple (I,C,T,M)"; "no (free-text actionable subtask)"; "{Delegate,Finish}" | **CONFIRMED** | p.4-5. |
| "FRESH-spawn per subtask, discarded, no pool" | **CONFIRMED (strong)** | p.12 container-lifecycle + persistence scan. |
| "ablates context not decomp (96 vs 86/84)" | **CONFIRMED** | Table 2 p.7 (No-Context 86.00 / Full 84.00 / Ours 96.00). |
| AOrchestra "SFT 56.97→68.48→75.15 ICL" (single chain) | **WEAKENED (chain misleading)** | Two separate tracks: **SFT 56.97→68.48** (Qwen3-8B orchestrator) and **ICL 72.12→75.15** (Mixed-model). 75.15 does **not** follow from 68.48. Individual numbers all correct. |
| Uno row: "typed (model, primitive) pairs; YES"; "TRAINED (SFT 61k + GRPO)" | **CONFIRMED** | Table 6 p.17; two-stage pipeline p.2. |
| Uno row: "GAIA 82.0 vs AgentOrchestra 83.4 (loses acc); ~$0.10 vs $1.21 (~10×)" | **CONFIRMED** | 83.4/82.0 pass@1; macro cost 0.1011/1.2118. (Loss also holds at pass@2: 87.0<88.7.) |
| ERRATA ①: "83.4 = 版本漂移旧数,Uno 对比行同步失效" | **REVERSED** | See §6. Uno's 83.4 is its **own controlled pass@1 reproduction** (shared 9-worker pool), not a stale citation; Uno reproduces AgentOrchestra pass@2 = 88.7 ≈ 89.04. Uno's comparison row is **valid**, not invalidated. |
| ERRATA ②: "MiroFlow 74.8>71.9 仅限 GAIA" | **CONFIRMED verbatim** | Table 5 p.8. |
| MiroFlow row: "74.5 p@1" | **WEAKENED/corrected** | Current paper: default 71.9 / single-agent 74.8, **avg@3 on 103 text-only**, not "74.5 p@1". The 74.5 was an earlier-version citation (JoyAgent Table 1 also cites 74.5). |
| JoyAgent row: roles "Supervisor+Plan/Retrieval/Logic/Browser" | **WEAKENED/corrected** | Paper roles = Plan/Retrieval/Logic/Browser (**no Supervisor**) + a separate Critic; Multiple(3) drops Browser. |

---

## 6. New reversals / flags for cross-line ledger

1. **REVERSAL — "Uno compared against a drifted AgentOrchestra 83.4" is wrong.** Uno re-ran
   AgentOrchestra under a unified harness + shared 9-worker pool → **83.4 pass@1 / 88.7 pass@2**
   are fresh controlled measurements. 88.7 ≈ AgentOrchestra's 89.04 self-report ⇒ the 83.4↔89.04
   gap is **pass@1-vs-pass@2/best-config**, not version staleness. **The substantive conclusion
   is unchanged and even stronger: Uno loses to AgentOrchestra on GAIA at BOTH pass@1 (82.0<83.4)
   AND pass@2 (87.0<88.7), while winning ~5× (GAIA) / ~12× (macro) on cost.** The catalog should
   drop "版本漂移 / 对比行同步失效" for the Uno row and relabel 83.4 as "Uno's controlled pass@1
   reproduction."

2. **NEW cross-paper datum (pro-N1).** Uno reproduces **AOrchestra at GAIA pass@1 = 69.4**
   (Table 2 p.8) vs AOrchestra's self-reported **80.0** — an ~11-point drop under a different
   harness/pool. Direct empirical support for N1's "matched-compute / shared-pool changes the
   ranking" thesis: even the fresh-spawn rival is heavily harness/pool-dependent. (Caveat: this
   is Uno's reproduction of AOrchestra; reproductions can under-tune the original.)

3. **AOrchestra headline is training-free.** Its 16.28% / 80.0 do **not** rely on SFT; SFT
   (+11.51%) is demonstrated only on a *weak Qwen3-8B orchestrator* (56.97→68.48) and does not
   stack on 80.0. So "AOrchestra is a trained system" is imprecise — it is training-free by
   default with an optional learned-orchestrator variant. N1's "training-free" differentiation
   must therefore rest on **evolving persistent pool**, not on "we don't train" (AOrchestra also
   ships a training-free mode).

4. **JoyAgent metric grounding.** Table 2 numbers are GAIA-**validation pass@1** (avg over
   levels), Claude-4-sonnet. MiroFlow Table 5 numbers are **avg@3 over 103 text-only** tasks.
   These are different metrics/splits — do not pool them into one "pass@1" column when writing.
