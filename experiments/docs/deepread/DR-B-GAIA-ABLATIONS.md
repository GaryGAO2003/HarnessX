# DR-B — GAIA Ablation Deep-Read: "Decomposition Alone Doesn't Raise Scores"

**Scope:** Full-text (body + tables) verification of the four GAIA-ablation witnesses that carry our
"decomposition-alone" fact chain — JoyAgent, AgentOrchestra, MiroFlow, ALITA-G.
**Method:** WebSearch/WebFetch only, primary-source entry `arxiv.org/html/<ID>` + abstract pages, cross-triangulated.
**Guardrails:** `research-guardrails` (enforce) active — negative/GAIA-specific results reported as-is; every number
carries metric direction + split; no fabricated conditions; version drift surfaced.
**Date:** 2026-07-30.

### Evidence-depth legend
- **DATA-TRI** = numeric value triangulated across ≥2 independent fetches/sources (abstract + body + search) → treat as solid.
- **S5†** = verbatim sentence from primary source, but **summarizer-mediated** (WebFetch small model). Numbers in it are
  triangulated; exact wording may drift ±. Per MEMORY discipline, summarizer-mediated *falsification* would not count —
  positive quotes are labelled so they are not over-trusted as literal.
- **INFER** = our reading, not the paper's words.

### Provenance / source-resolution table
| System | arXiv ID | Resolved? | Title / authors (verified) | Version served by `/html/<ID>` |
|---|---|---|---|---|
| JoyAgent-JDGenie | 2510.00510 | YES | *JoyAgent-JDGenie* technical report (JD; repo `jd-opensource/joyagent-jdgenie`) | single version |
| AgentOrchestra | 2506.12508 | YES | *AgentOrchestra: Orchestrating Multi-Agent Intelligence with the TEA Protocol* — W. Zhang … **Bo An** (Skywork AI / NTU) | **v6 (28 May 2026)** — 6 versions total |
| MiroFlow | 2602.22808 | **YES (source problem discharged)** | *MiroFlow: Towards High-Performance and Robust Open-Source Agent Framework for General Deep Research Tasks* — Su et al. (Tsinghua / **MiroMind AI**); GitHub `MiroMindAI/miroflow`, `open-compass/MiroFlow` | v1 |
| ALITA-G | 2510.23601 | YES | *Alita-G: Self-Evolving Generative Agent for Agent Generation* — Qiu … **Mengdi Wang** (Princeton) | v1 (27 Oct 2025) |

---

## 1. JoyAgent-JDGenie (2510.00510) — direct single-vs-multi ablation

**Setup card**
- **Source:** JoyAgent-JDGenie technical report, §4.2.1 "Exploratory Evaluations", **Table 2**.
- **Base model:** Claude-4-sonnet (Table 3 headline; Claude-3.7-sonnet = 68.3 secondary). DATA-TRI.
- **Split / protocol:** GAIA **validation** (165 Q total; ablation Table 2 does not print per-row n). pass@1 ("Average").
- **Arms (Table 2):**
  - **Single = 71.5** — "a basic ReAct pattern, providing all tools (excluding browser-use tools) and **increasing the
    maximum execution steps**" (S5†). One agent, extended step budget.
  - **Multiple (3) = 70.3** — role-split multi-agent: **Plan Agent** (high-level planning) + **Retrieval Agent** +
    **Logic Agent** (+ **Browser Agent**). Sub-agents execute the planner's subtasks. (The label "(3)" vs the 4 named
    roles is not fully disambiguated in the fetched text — noted as a minor residual.)
  - **Fusion = 75.2** — Single + Multiple(3) + an added **Critic** model doing posterior comparison/voting over both
    trajectories.

**Ablation numbers**
| Config | GAIA-Val Average (pass@1) |
|---|---|
| Single (ReAct, +steps) | **71.5** |
| Multiple (3) (decomposition MAS) | **70.3** |
| Fusion (+Critic voting) | **75.2** |

**Budget parity:** **NOT matched.** Neither arm is compute-controlled: Single was *given extra execution steps*;
Multiple(3) was *given extra agents/roles*. So this is **not** a clean equal-token test. BUT the decomposition arm uses
**more agents** and still **loses** (70.3 < 71.5), and the single arm was explicitly *not* compute-starved (it got more
steps). → **Mild strengthening** for our thesis ("adding multi-agent decomposition did not beat a well-resourced single
agent"), and it rules out the "single only won because we fed it more compute" objection. It does **not** license the
stronger claim "decomposition strictly hurts at equal budget."

**S5† paper's own framing:** "Surprisingly, this simple structure did not exhibit performance collapse; instead, it
achieved the highest performance of 71.5 under the non-fusion approach." (i.e., the authors themselves flag single>multi
as a surprise.)

**Verdict vs §3 row ("decomp alone HURTS"): CONFIRMED.** Numbers, arms, direction exact. Attach budget caveat
(not matched; both arms boosted on different axes).

---

## 2. AgentOrchestra (2506.12508 v6) — cumulative-add ablation

**Setup card**
- **Source:** §5.2 "Ablation Studies" / "Effectiveness of the specialized sub-agents", **Table 5**.
- **Base models:** planning (m=50), deep researcher (m=3), tool generator (m=10), deep analyzer (m=3), reporter =
  **gemini-3-flash-preview**; browser-use = gpt-4.1 (m=5) + computer-use-preview(4o) (m=50). DATA-TRI (v6, May 2026 —
  *not* anachronistic; `/html/2506.12508` serves latest v6).
- **Headline:** **89.04% GAIA Test, pass@1** (abstract: "it achieves 89.04% on the GAIA Test set"). DATA-TRI.

**Ablation numbers (Table 5, cumulative)**
| Components (P=Plan, R=Researcher, B=Browser, A=Analyzer, T=ToolGen) | L1 | L2 | L3 | Avg | Δ |
|---|---|---|---|---|---|
| P only | 54.84 | 33.96 | 10.20 | **36.54** | – |
| P + R | 86.02 | 47.17 | 34.69 | **57.14** | +56.40% |
| P + R + B | 89.25 | 71.07 | 46.94 | **72.76** | +27.33% |
| P + R + B + A | 91.40 | 77.36 | 61.22 | **79.07** | +8.67% |
| P + R + B + A + T | 98.92 | 85.53 | 81.63 | **89.04** | +12.61% |

**Key structural fact:** the ablation **never removes the planner/decomposition** — it *adds executor sub-agents on top
of a planner that is always present*. So it measures the marginal value of executors, **not** "decomposition on/off."
The nearest proxy for "decomposition alone" is **P-only = 36.54**.

**Attribution — original vs inference:**
- **ORIGINAL (S5†):** "the **Tool Generator** provides the largest **late-stage** gain, improving the average score from
  79.07 to 89.04 and Level 3 from 61.22 to 81.63"; AND "**information acquisition is the dominant early bottleneck** on
  GAIA: adding the **Deep Researcher** raises the average score from 36.54 to 57.14"; AND "the Deep Analyzer improves the
  average score from 72.76 to 79.07."
- **INFER (ours):** "gain is from executor/tool capability, not from the decomposition step." Well-supported (P-only=36.54;
  every uplift is an added executor). **BUT** the catalog's "gain **mostly** Tool-Generator" **overstates** it: the single
  **largest** jump is the **Deep Researcher** (+20.6 pts, 36.54→57.14); Tool-Generator is the largest *late-stage* jump
  (+10.0 pts). Correct framing → "planner/decomposition alone = 36.54; **all** uplift comes from adding specialized
  executor sub-agents (Researcher largest early, Tool-Generator largest late); decomposition is not the lever."

**Budget parity:** N/A as a single-vs-multi test — compute rises monotonically with each added agent. Here *more
agents/tools ⇒ higher score*, but the driver is added **capability**, not the split. Not a controlled decomposition test.

**Verdict vs §3 row:** "decomp not the lever" → **CONFIRMED**. Sub-claim "gain **mostly** Tool-Generator" → **WEAKENED**
(broaden to executor sub-agents; Researcher is the biggest single jump). Headline "83.4" → **ERRATUM: correct to 89.04
Test / pass@1 / gemini-3-flash-preview (v6)**; "83.4" is stale earlier-version drift (not present in current primary source).

---

## 3. MiroFlow (2602.22808) — single-vs-multi ablation, GAIA-specific

**Setup card**
- **Source problem: DISCHARGED.** Real, citable — arXiv 2602.22808 (MiroMind AI), corroborated by GitHub repos and an
  independent search snippet. No longer "single-source."
- **Source:** §4.3, **Table 5** ("Single- vs. Multi-Agent ablation within MiroFlow").
- **Base model:** **GPT-5** ("all experiments … using GPT-5 as the underlying model"). DATA-TRI.
- **Split / protocol:** **GAIA-Val-Text = 103 text-only questions**; pass@1.
- **Arms:** Single-Agent = one GPT-5, longer context, continuous trajectory. Multi-Agent = main–sub architecture (both
  GPT-5) with decomposition + delegation.

**Ablation numbers (Table 5)**
| Setting | **GAIA-Val** | BrowseComp-200 | HLE-200 |
|---|---|---|---|
| Single-Agent | **74.8** | 63.9 | 40.6 |
| Multi-Agent | **71.9** | **68.3** | **42.0** |

**CRITICAL SCOPING CAVEAT (report-as-is):** multi-agent **wins on 2 of 3** benchmarks (BrowseComp-200 +4.4, HLE-200 +1.4)
and **only loses on GAIA**. Table 5 caption (S5†): "Multi-agent settings perform better on most benchmarks **but not on
GAIA**." Paper's mechanism (S5†): "GAIA's strongly sequential task structure: multi-agent decomposition **increases the
risk of mistake propagation** across sub-agents, whereas a single-agent model maintains a continuous reasoning
trajectory." → MiroFlow supports **"decomposition hurts *on GAIA's sequential tasks*,"** NOT a blanket "decomposition hurts."

**Budget parity:** not explicitly matched; the multi-agent (main+sub) arm issues **more** agent calls than the single
GPT-5 and still loses on GAIA → mild strengthening on GAIA only.

**Verdict vs §3 row ("decomp HURTS (single-source, verify)"): CONFIRMED + VERIFIED** (74.8 > 71.9 exact; source resolved;
"single-source, verify" tag discharged). **Add scoping caveat**: effect is GAIA-specific; multi-agent wins elsewhere.

---

## 4. ALITA-G (2510.23601) — frontier "no-decomposition" existence witness

**Setup card**
- **Source:** abstract + §3.x / §4 (Table 1).
- **Headline:** "On GAIA validation, it achieves **83.03% pass@1** and 89.09% pass@3" (abstract, DATA-TRI).
  So **83.03 = pass@1** (not avg@k). Split = **GAIA validation** (n not stated in abstract; standard full-val = 165;
  an earlier fetch's "466 questions" was a **confabulation**, discarded).
- **Base model:** Manager Agent = **Claude-Sonnet-4**; Web Agent sub-component = GPT-4.1.

**Architecture / decomposition boundary (the load-bearing definitional claim):**
- Pipeline (S5†): a generalist agent runs target-domain tasks and "synthesizes candidate MCPs from successful
  trajectories … abstracted to parameterized primitives and consolidated into an **MCP Box**." The final specialized agent
  = **Manager + Task Analyzer + MCP Retriever + MCP Executor**, running a **CodeAct loop**.
- **No explicit multi-agent task decomposition** into a plan of subtasks delegated to spawned sub-agents. The **Task
  Analyzer** does task *understanding for tool retrieval*, not subtask splitting; **MCP-RAG** selects tools via semantic
  embeddings at inference.
- **Boundary (defensible in body):** MCP **generation** = offline abstraction of reusable sub-solutions into *tools*;
  MCP-RAG = inference-time *tool selection*. Both are **tool-building / tool-routing, not task decomposition.** One honest
  hedge: the paper's word "reusable **sub-solutions**" refers to packaged tool primitives, not runtime delegated subtasks —
  so "no explicit decomposition" holds, but ALITA-G is not literally "one agent" (it has a Web Agent + modular Manager).

**Budget parity:** N/A (existence proof, no single-vs-multi ablation).

**Verdict vs §3A ("highest scorers do the least decomposition; ALITA-G 83.03, none"): CONFIRMED** as an existence witness
(top open GAIA-val system, 83.03 pass@1, no explicit decomposition). **Soften "current open SOTA" wording** — cross-system
rank is noisy (different subsets/models; MiroFlow single = 74.8 on 103 text-only vs ALITA-G 83.03 on full val), and SOTA
ranking is not load-bearing for us; the load-bearing point is the *architecture*, which holds.

---

## 5. Consolidated ablation table
| System | GAIA arm A (no/less decomp) | GAIA arm B (decomp/MAS) | Direction | Base model | Split / metric | Budget matched? |
|---|---|---|---|---|---|---|
| JoyAgent | Single ReAct **71.5** | Multiple(3) **70.3** (< Fusion 75.2) | single > multi | Claude-4-sonnet | val / pass@1 | No (both boosted, diff axes) |
| AgentOrchestra | P-only **36.54** | +executors → **89.04** | executors, not decomp, drive gains | gemini-3-flash-preview | Test / pass@1 | No (cumulative-add) |
| MiroFlow | Single **74.8** | Multi **71.9** *(GAIA only)* | single > multi **on GAIA**; multi wins BC-200/HLE-200 | GPT-5 | val-text 103 / pass@1 | No (multi = more calls) |
| ALITA-G | Single-agent **83.03** (no explicit decomp) | — | frontier ⇒ no decomposition | Claude-Sonnet-4 | val / pass@1 | N/A |

---

## 6. Fact-chain revision (which rows bear weight)

**Survives — load-bearing (direct within-framework ablation, decomp arm ≤ single):**
1. **JoyAgent 70.3 < 71.5** — CONFIRMED. Strongest *direct* witness (same framework isolates decomposition vs single).
   Carry budget caveat.
2. **MiroFlow 71.9 < 74.8 (GAIA)** — CONFIRMED + now verified. Second direct witness, **but must carry the GAIA-specific
   scope** (multi-agent wins on 2/3 benchmarks). Do **not** cite as generic "decomposition hurts."

**Survives — supporting (not a single-vs-multi ablation):**
3. **AgentOrchestra: planner-only 36.54; all uplift from executor sub-agents** — CONFIRMED as "decomposition is not the
   lever." Downgrade the *specific* "gain mostly Tool-Generator" → "gain from executor sub-agents (Researcher largest
   early, Tool-Generator largest late)." Fix headline 83.4 → **89.04 Test**.
4. **ALITA-G 83.03 pass@1, no explicit decomposition** — CONFIRMED as an existence/frontier witness. Soften "SOTA."

**No REVERSALs.** The chain holds; two refinements are mandatory before load-bearing use: (a) MiroFlow is GAIA-specific;
(b) AgentOrchestra's driver is executor capability broadly (Researcher biggest single jump), not "mostly Tool-Generator,"
and its headline is 89.04 Test not 83.4.

**Errata to push into DECOMP-METHOD-CATALOG.md:**
- §3 + §1B AgentOrchestra: **83.4 → 89.04 (GAIA Test, pass@1, gemini-3-flash-preview, v6)**; reword driver attribution.
- §3 MiroFlow: discharge "(single-source, verify)"; **add** "GAIA-specific; multi-agent wins BC-200 (68.3>63.9) & HLE-200
  (42.0>40.6)"; base = GPT-5; split = GAIA-Val-Text (103).
- §1B JoyAgent: add "budget not matched — Single got +steps, Multiple(3) got +agents."
- §3A ALITA-G: 83.03 = **pass@1** (89.09 pass@3); base Claude-Sonnet-4; soften "current open SOTA."

## 7. Residual uncertainties / open questions
- All verbatim quotes are **summarizer-mediated (S5†)** — numbers triangulated, exact wording approximate. A literal
  full-text read (ar5iv/PDF) would upgrade S5† → S5 if any exact sentence must appear in the thesis.
- JoyAgent "Multiple (3)": label "(3)" vs 4 named roles (Plan/Retrieval/Logic/Browser) not fully disambiguated.
- AgentOrchestra "83.4": could not locate in current v6 — presumed an earlier-version (v1–v3) or validation-split number;
  confirm against a pinned older version only if the exact 83.4 value is ever needed.
- No system provides a rigorously **token-matched** single-vs-decomposition experiment on GAIA — so the defensible thesis
  claim is "adding decomposition/more agents did not beat a strong single agent on GAIA (despite ≥ compute)," **not**
  "decomposition strictly hurts at equal budget." (This is exactly the matched-budget gap our own study can occupy.)
