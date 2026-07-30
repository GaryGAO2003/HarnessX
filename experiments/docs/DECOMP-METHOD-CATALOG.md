# DECOMP-METHOD-CATALOG — How 2026 Agent Systems Decompose Tasks (methodology census)

> Compiled 2026-07-30 by researcher (Opus 4.8). Question asked of every paper: **HOW does it break a task apart** — not "did it occupy our niche."
> Window: 2025-12 .. 2026-07 (focus 2026); pre-window ancestors kept where they anchor a lineage, flagged by date.
> Web access: WebSearch/WebFetch only. Anti-confab discipline enforced (neutral prompts; gray-zone = FAIL → "UNVERIFIED"; no invented IDs).
> **Depth tags**: L0 = secondhand search snippet (NOT load-bearing) · L1 = abstract opened · L2 = method section skimmed · L3 = method read carefully · S5 = verbatim quote in hand.
> **Provenance tags**: [D]=carried from prior HarnessX docs (LITSCAN-A/B, NOVELTY-EXPDESIGN, DIVISION-DESIGN) · [O]=abstract opened this session · [W]=this-session breadth sweep (single pass, sweep-level).

> **⚠️ ERRATA(Jul-31 凌晨,正文级深读 deepread/DR-A/B/C 后;引用本表前必读)**:
> ①AgentOrchestra 自报头条 = **89.04 Test(v6)**;83.4 = **Uno 统一 harness 受控
> pass@1 复现**(其 pass@2=88.7≈89.04,差异为 pass@1/pass@2 口径非版本漂移——
> DR-D PDF 级二次修正,**Uno 对比行有效**);归因照旧"planner-only 36.54,增益全
> 在执行器子代理"(Tool-Gen 是后期最大单项,非全部);
> ②MiroFlow 74.8>71.9 **仅限 GAIA**(BrowseComp-200/HLE-200 多体反胜),禁作普遍
> 结论;③GAIA 消融四家**无一配平预算**——事实链措辞收口"≥算力下加分解仍不敌强
> 单体 on GAIA";④Meta-Agent 红旗解除(CLEAR:零跨任务持久化,双渲染逐字);
> ⑤2606.13003 **不配平**(10×=as-deployed 观测),降为动机引;配平一手改由
> 2604.02460(matched thinking-token+bootstrap CI)与 2606.15017(web 床)承担;
> ⑥"truly heterogeneous" 话术 REVERSED:OneFlow 定义 heterogeneous=换底座,我方
> 同底座 config 池落其 homogeneous"可被单体吃掉"范围 ⇒ SA-matched 升生死线。
> ⑦GUI 行四修正(DR-F,PDF 页码级):Lybic "worker pool"=**三固定角色**(Operator/
> Technician/Analyst,p.7)非演化池,占位 **CLEAR**,OSWorld 57.07%@50 无分解消融;
> Agent S2 的 +18.9/+32.7=全系统 vs 基线(Table 1 p.6)**非分解单项消融**(真组件
> 消融:MoG +3.08/+4.61pp、PHP +4.62/+6.15pp,Fig 5 p.8);UFO2=依赖序子任务图、
> per-app 会话级代理、无分解消融;RSTD 2605.15425 三配置**精度全 100% 打平**
> (p.3,retry-token 轴)——我方"配平精度 on GAIA"边界句双正交成立。

## 0. Coverage statement (honest scope)

| Sub-domain | Coverage | Notes |
|---|---|---|
| Dedicated decomposition/planning methods (GAIA/web + general) | **near-exhaustive of the mandatory list** | 4 local docs harvested for all arXiv IDs; re-read under the "how" lens |
| GAIA leaderboard systems | **near-exhaustive of public toppers** | OWL/Workforce, Alita/ALITA-G, Magentic-One, smolagents, TapeAgents, h2oGPTe, MiroFlow, OxyGent, JoyAgent, AOrchestra, AgentOrchestra covered |
| SWE agents | **representative sample** | SWE-agent, OpenHands, Agentless, AutoCodeRover, Moatless/SWE-Search, SWE-Gym, SWE-smith |
| OS/GUI agents | **representative sample** | UFO/UFO2, Cradle, Agent S2, Agentic Lybic, OSWorld(bench) |
| MAS frameworks | **representative sample** | MetaGPT, CAMEL, AutoGen/AG2, AFlow, MaAS, MasRouter, GraphRouter, Meta-Agent |
| DeepResearch / BrowseComp | **sampled, not exhaustive** | WebThinker, DeepResearcher, ManuSearch, DecomposeR, DeepPlanner, Tongyi/WebSailor family |
| **Gaps / not chased** | multimodal-GUI agents beyond OSWorld; Chinese-ecosystem MAS beyond named list; the newest (post-Jul-20) DeepResearch RL policies at method depth | flagged in §7 |
| **UNVERIFIED (no resolvable arXiv)** | smolagents/CodeAgent (HF lib+blog), h2oGPTe (h2o.ai blog), WebSailor/WebDancer sub-IDs | kept but never load-bearing |

Depth honesty: **no paper in this catalog was read to L3 this session**; L3 rows are carried from prior docs (TDP, AdaptOrch, E3, AOrchestra, Magentic-One). Most sweep rows are L1–L2 single-pass. Treat sweep ablation numbers as indicative pending verbatim confirmation.

---

## 1. MASTER CENSUS TABLE

Schema: `ID | System | Date | Bench | Decomp mechanism | Plan format | Typed subtasks? | Trigger | Executor binding | Replan? | Decomp ablation (numbers) | Depth`

### 1A. Dedicated task-decomposition methods (the on-topic core)

| ID | System | Date | Bench | Mechanism | Plan fmt | Typed subtasks? | Trigger | Executor binding | Replan? | Decomp ablation | Depth |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 2601.07577 | **TDP** (Task-Decoupled Planning) | 26-01 | TravelPlanner/HotpotQA/ScienceWorld | one-shot plan (decomp-first) | **DAG** of sub-goals | no (single executor, no labels) | upfront | same single planner-executor, node-scoped context | supervisor-level | vs ReAct: TravelPlanner 34 vs 30; HotpotQA 85.9 vs 72.7; **win is −82% tokens** | L3 [D] |
| 2602.16873 | **AdaptOrch** | 26-02 | SWE-bench-V/GPQA/HotpotQA | adaptive: DAG width/depth/coupling → threshold → pick pattern | DAG (analyzed for shape) | **pattern-level** (parallel/sequential/hierarchical/hybrid), not subtask-type | adaptive | routes to an orchestration *pattern*, not typed executors | structure per task | **+6.9 to +9.8pp** vs Static-Parallel/Sequential & single-best, ~½ tokens (cleanest adaptive>static) | L3 [D] |
| 2607.13034 | **E3** ("Do agents know when a task is simple?") | 26-07 | MSE-Bench (coding) | difficulty estimate → minimum-viable scope 1–3 → expand-on-fail | scope levels (not DAG) | no (scope, not types) | adaptive + on-failure | same agent, scope-controlled | yes (expand) | 100% success at **−85% cost**; **accuracy Δ≈0** vs adaptive-retrieval (win is cost) | L3 [D] |
| 2605.05007 | **Uno-Orchestra** | 26-05 | GAIA + 13 benches | one policy emits plan + typed routing pair | plan + **typed (model, primitive) pairs** | **YES** — (model, primitive) | upfront | **expert-model routing** (typed) | policy-driven | **GAIA 82.0 vs AgentOrchestra 83.4 (loses acc)**; wins cost ~$0.10 vs $1.21 (~10×). **TRAINED** (SFT 61k + GRPO) | L2 [D] |
| 2604.22446 | **OneManCompany** ("From Skills to Talent") | 26-04 | PRDBench (software) | one-shot typed division-of-labor | **E²R tree + DAG** | **YES** — 6 typed interfaces (Talent-Container) | upfront | typed talent-containers | E²R tree | 84.67% SR (n=50), $6.91/task. Training-free but **off-GAIA / software** | L2 [D] |
| 2601.12560 | (survey) Agentic AI taxonomy | 26-01 | — | anchors topology axis | linear/interleaved/tree/**hierarchical**/inference-time | — | — | — | — | WebArena <15% long-horizon (qual.) | L2 [D] |
| 2402.02716 | (survey) Planning of LLM Agents | 24-02 | — | canonical 5-part planning taxonomy; **decomposition-first vs interleaved** | — | — | — | — | — | — | L1 [D] |
| 2603.28005 | Rethinking Atomic Decomposition | 26-03 | TruthfulQA/ASQA/QAMPARI | **NOT task-execution** — decomposes *answers into claims for LLM-judge* | — | fully/partially/un-supported | in-prompt | LLM judge | — | **holistic judge ≥ atomic on 2/3 benches** (atomic decomp does not reliably help) | L1 [O] |
| 2605.15425 | **Runtime-Structured Task Decomposition** | 26-05 | coding (debug/RCA/review) | **program-template**: executable control logic; LLM only for focused judgment; schema-validated | schema-validated stages | schema-typed | runtime-structured | executable control logic (LLM for judgment) | — | **−51.7% retry cost vs monolithic; −73.2% vs *static* decomposition** (coding, not GAIA; cost not acc) | L1 [O] |
| 2509.22502 | **InfiAgent** | 25-09 | multi | recursive: "agent-as-a-tool", decompose into hierarchical MAS | **pyramid DAG** | not stated | adaptive (self-evolve) | routed agents (agent-as-tool), can nest | **yes — self-evolves DAG on poor perf** | not stated | L1 [O] |
| 2503.09572 | Plan-and-Act | 25-03 | WebArena-Lite/WebVoyager | learned planner + executor | plan (NL) | no | upfront | fixed planner+executor (**trained**) | yes | WA-Lite 57.6; WebVoyager 81.4 | L1 [D] |
| 2503.23053 | IFD | 25-03 | off-GAIA | training-free subtask interaction | — | — | — | — | — | — | L1 [D] |
| 2604.17821 | WebUncertainty | 26-04 | WebArena/WebVoyager | adaptive: task-uncertainty→plan, action-uncertainty→**MCTS** | — | no | adaptive | same agent + MCTS rollouts | yes | "superior" (no numbers extracted); MCTS = costly | L1 [D] |
| 2603.07915 | Ares | 26-03 | — | per-step reasoning-effort routing (**over fixed menu**, orthogonal to decomp) | — | no | adaptive | same agent | — | up to −52.7% reasoning tokens | L2-via-E3 [D] |

### 1B. GAIA leaderboard systems

| ID | System | Date | GAIA | Mechanism | Plan fmt | Typed subtasks? | Trigger | Executor binding | Replan? | Decomp ablation | Depth |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 2602.03786 | **AOrchestra** (MetaGPT/FoundationAgents) | 26-02 | **80.0 p@1** (Gemini-3-Flash) | adaptive per-step decomposition (no upfront plan) | per-step **4-tuple** (Instruction,Context,Tools,Model) | no (free-text "actionable subtask") | adaptive/per-step | **FRESH-spawn sub-agent per subtask, discarded, no pool** | yes ({Delegate,Finish}) | ablates *context* not decomp (96 vs 86/84); orchestrator **SFT** 56.97→68.48→75.15 ICL | L3 [D]+[W] |
| 2506.12508 | **AgentOrchestra** (TEA) — Zhang/Bo An | 25-06 | 89.04 test | hierarchical decompose + delegate | hierarchical NL | **fixed 4 roles** (DeepResearcher/BrowserUse/DeepAnalyzer/ToolGenerator) | upfront | **fixed manual expert roles** | hierarchical | cumulative 36.54→89.04 but **gain mainly Tool-Generator, not decomposition** | L2 [D] |
| 2607.17044 | **Leni** | 26-07 | 75.2 p@1 / 83.0 bo-k | planner + verification loops | NL plan | specialist models (task-specialized) | upfront + verify loop | **expert-model routing** (post-trained specialists) | yes (verify/correct) | **"decomposition of uplift": most from scaffolding/routing/specialists; verification isolated +1.5 pts** | L1/S5 [D] |
| 2505.23885 | **OWL / Workforce** | 25-05 | 69.7 val | one-shot plan + **recursive re-decomp on failure** | subtask **list** in shared task channel | no (workers typed by domain) | upfront + on-failure | **persistent domain-worker pool**; coordinator assigns | yes | +16.37% is a **training** gain (OWL), not decomp | L2 [W] |
| 2505.20286 | **Alita** | 25-05 | 75.15 p@1 | **NONE/implicit** — single minimal-predefinition loop; self-generates MCP tools | NL, no formal plan | no | adaptive | same single agent + web + self-made MCPs | implicit | none | L2 [W] |
| 2510.23601 | **ALITA-G** (Alita successor) | 25-10 | **83.03 p@1 = current open SOTA** | **NONE explicit** — MCP-RAG retrieval | — | no | same agent + retrieved MCPs | implicit | none | L1 [W] |
| 2411.04468 | **Magentic-One** | 24-11 | 38.0±5.5 | upfront plan + iterative replan (two-loop ledger) | NL step-list in **Task Ledger**; Progress Ledger tracks | facts typed (given/lookup/derive/guess), **not subtasks** | upfront + on-stall | **fixed 5 roles** (Orchestrator/WebSurfer/FileSurfer/Coder/Terminal) | yes | **remove ledgers −31%; agent removals −21..39%** (decomp-state load-bearing) | L3 [D]+[W] |
| 2412.08445 | **TapeAgents** | 24-12 | 37.0 (GPT-4o) | node-graph; hierarchical manager/subagent *optional* | **"tape" = typed step log** | **YES** — steps typed (Thought/Action/Observation + Call/Respond) | adaptive (SetNextNode) | root agent + optional hierarchical subagents | resumable from any tape state | none on GAIA | L2 [W] |
| 2602.22808 | **MiroFlow** | 26-02 | 74.5 p@1 | upfront hierarchical decomposition | **directed graph** (declare-then-define) | no | upfront | main agent delegates to specialized sub-agents (nestable) | not stated | **single-agent 74.8 vs multi-agent 71.9 — decomposition HURTS** (L2 sweep, single-source) | L1[D]/L2[W] |
| 2604.25602 | **OxyGent** | 26-04 | 59.14 | permission-driven dynamic planning; **runtime-synthesized DAG** | DAG at runtime | not stated | adaptive/runtime | Master→Task Agent (decomps)→functional Oxy agents | not stated | none | L2 [W] |
| 2510.00510 | **JoyAgent-JDGenie** | 25-10 | 75.2 p@1 | hybrid: upfront Plan-Execute + step-level ReAct | high-level plan | no | upfront + reflection + per-step | **fixed roles** (Supervisor+Plan/Retrieval/Logic/Browser) + posterior voting | yes | **single ReAct 71.5 > multi-agent decomp 70.3 < fusion 75.2** | L2 [D]+[W] |
| — (UNVERIFIED) | smolagents / CodeAgent (HF) | 25– | val-topping | **NONE by default** — single ReAct writing Python; optional manager delegation | code + NL | no | adaptive | same agent; optional fixed manager | yes (code loop) | none | L1 [W] |
| — (UNVERIFIED) | h2oGPTe (h2o.ai) | 25 | 79.7 val (2025 record) | undisclosed orchestrator+planning | not stated | not stated | not stated | main agent + tools (proprietary) | not stated | none | L1 [W] |

### 1C. SWE issue-fixing agents

| ID | System | Date | Bench | Mechanism | Plan fmt | Typed? | Trigger | Executor binding | Replan? | Decomp ablation | Depth |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 2405.15793 | SWE-agent | 24-05 | SWE-bench | **NO explicit decomp** — single ReAct loop over ACI | NL actions | no | n/a | same agent | n/a | gains from **ACI, not decomposition** | L1-2 [W] |
| 2407.16741 | OpenHands / CodeAct | 24-07 | SWE-bench/WebArena | **NO explicit decomp** — single CodeAct loop (action=code); delegation optional | code/NL | no | on-demand | same agent (+optional spawn) | no formal plan | none | L1-2 [W] |
| 2404.05427 | AutoCodeRover | 24-04 | SWE-bench-lite 19% | **fixed program-template**: AST/code-search retrieval → patch (+SBFL) | staged, no plan object | no | upfront stages | same agent across stages | no plan-level | SBFL context ablation | L2 [W] |
| 2407.01489 | **Agentless** | 24-07 | SWE-bench-Lite 32% | **fixed 3-phase** localize→repair→validate; **deliberately anti-agentic** | fixed template | no | upfront fixed | staged LLM calls, no roles | **NO replan** | whole paper = ablation: simple pipeline **beats** agentic scaffolds | L2 [W] |
| 2410.20285 | SWE-Search (Moatless) | 24-10 | SWE-bench | **SEARCH not decomp** — MCTS over trajectories + value-agent debate | search tree | no | adaptive/backtrack | Action Agent + Value Agent | backtrack | MCTS gain | L2 [W] |
| 2412.21139 | SWE-Gym | 24-12 | SWE-bench-V/Lite | **learned-via-training** scaffold + verifier best-of-n | n/a | no | n/a | trained single agent + verifier | no | verifier inference-scaling gains | L1 [W] |
| 2504.21798 | SWE-smith | 25-04 | SWE-bench | data-scaling to **train** single-loop agent | n/a | no | n/a | trained single agent | no | none for decomp | L1 [W] |

### 1D. OS/GUI agents

| ID | System | Date | Bench | Mechanism | Plan fmt | Typed? | Trigger | Executor binding | Replan? | Decomp ablation | Depth |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 2402.07939 | UFO | 24-02 | 9 Win apps | dual-agent host/app split | NL | by-app (2 roles) | adaptive | **fixed 2-role set** | stepwise | none | L1 [W] |
| 2504.14603 | **UFO2** | 25-04 | 20+ Win apps | **LLM decomposition** — HostAgent decomposes, routes to per-app AppAgents | NL/list | **YES — per-app expert AppAgents** | upfront + coord | **expert-executor per application** | not detailed | not stated | L2 [W] |
| 2403.03186 | Cradle | 24-03 | OSWorld+games | **fixed 6-module** cognitive pipeline; emits code | module template | no | adaptive (self-reflect) | same LMM across modules | yes (self-reflect) | module ablations | L1-2 [W] |
| 2504.00906 | **Agent S2** | 25-04 | OSWorld/WinArena/AndroidWorld | **LLM hierarchical decomp** — Manager → subgoal list → Worker; Mixture-of-Grounding | subgoal **list** | **YES — Manager/Worker + MoG grounding experts** | **proactive + on-failure** | fixed roles + **grounding-expert pool** | **yes** | +18.9 / +32.7% rel. vs Claude-CU / UI-TARS | L2 [W] |
| 2509.11067 | **Agentic Lybic** | 25-09 | OSWorld | manager-worker, **DAG subtask decomposition**, tiered orchestration | **DAG** | tiered roles | adaptive/scheduling | manager + **worker pool** | yes | SOTA claim (not isolated) | L1 [W] |
| 2404.07972 | OSWorld | 24-04 | benchmark | N/A (benchmark) | — | — | — | — | — | best model 12.24% | L1 [W] |

### 1E. MAS frameworks

| ID | System | Date | Bench | Mechanism | Plan fmt | Typed? | Trigger | Executor binding | Replan? | Decomp ablation | Depth |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 2308.00352 | **MetaGPT** | 23-08 | SoftwareDev/HumanEval | **fixed SOP** (human template, not LLM-gen) | NL docs down assembly line | **fixed roles** PM/Architect/PM/Engineer/QA | upfront | fixed role set (1 agent/role) | limited (QA loop) | not in abstract | L2 [W] |
| 2303.17760 | CAMEL | 23-03 | dialogue | **NO explicit decomp** — 2-agent role-play; task-specifier refines once | NL dialogue | 2 fixed roles | upfront role assign | fixed 2-agent | no (emerges) | none | L1 [W] |
| 2308.08155 | AutoGen / AG2 | 23-08 | framework | **NO built-in decomp** — developer wires agents | code/config | n/a | dev-defined | dev-defined | n/a | n/a | L0 [W] |
| 2410.10762 | **AFlow** | 24-10 | 6 code/QA sets | **searched workflow** (MCTS, offline) | **code graph** (LLM nodes+edges) | typed operators (predefined) | search-time, deploy fixed | LLM-call nodes | search-time only | +5.7% avg; small model > GPT-4o @ ~4.5% cost | L1 [W] |
| 2502.04180 | **MaAS** | 25-02 | 6 sets | **searched supernet + per-query sample** | sampled operator subnet | typed operators, difficulty-conditioned | adaptive/per-query | custom MAS per query | struct varies/query | 6–45% infer cost, +0.54–11.82% | L2 [W] |
| 2502.11133 | **MasRouter** | 25-02 | GSM8K/HumanEval/MBPP/MATH | **learned cascade routing** | MAS config | **YES — 3 stages**: collab-mode→roles→LLM/role | adaptive/per-query | **expert-model per role** | per-query rebuild | +1.8–8.2% MBPP, −52% overhead | L1 [W] |
| 2410.03834 | GraphRouter | 24-10 | routing sets | **NO decomp — model selection** | task/query/LLM graph | typed nodes (not subtasks) | per-query | picks 1 LLM/query | n/a | inductive to unseen LLMs | L1 [W] |
| 2605.25233 | **Meta-Agent** ⚑ | 26-05 | not extracted | **inference-time LLM-generated MAS per task** | **DAG of agent specs + I/O contracts + verify criteria** | **YES — generated specs** | upfront + construction-time verify/regen | coordinator → **generated workers** + exec-time gating | **yes (targeted regeneration)** | "consistent gains" (no #) | L1 [W] |

### 1F. DeepResearch / BrowseComp

| ID | System | Date | Bench | Mechanism | Plan fmt | Typed? | Trigger | Executor binding | Replan? | Decomp ablation | Depth |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 2504.21776 | WebThinker | 25-04 | GAIA/GPQA/HLE | **NO explicit decomp** — interleaved Think-Search-Draft | NL trace | no | adaptive (gap-triggered) | **single agent** (LRM) + Deep Web Explorer | implicit; DPO-trained | not extracted | L1 [W] |
| 2504.03160 | DeepResearcher | 25-04 | open-domain QA | **NO module** — planning **emerges from end-to-end RL** | NL (emergent) | no | adaptive/learned | single agent + real web | emergent | +28.9 vs prompt, +7.2 vs RAG-RL | L1 [W] |
| 2505.18105 | ManuSearch | 25-05 | ORION | **iterative decomp into fixed roles** | sub-queries (NL) | **3 fixed** (planner/searcher/reader) | adaptive (iterative subq) | fixed 3-agent | yes | beats open + some closed | L1 [W] |
| 2605.30824 | DecomposeR (Planner-Centric RL) | 26-05 | long-form DR | **learned-via-RL** (planner-RL → answerer-RL) | **typed DAG** | yes (node types not extracted) | upfront plan | planner + answerer | plan-conditioned | finer reward vs flat (no #) | L1 [W] |
| 2510.12979 | DeepPlanner | 25-10 | DR | RL advantage-shaping planning | high-level plan | not stated | upfront | single trained agent | multistage | — | L0 [W] |
| 2510.24701 | Tongyi DeepResearch (+WebSailor/WebDancer) | 25-10 | BrowseComp/HLE/GAIA | **single-agent ReAct + RL, NO explicit MAS** | NL think-act | no | adaptive | single RL policy + "Heavy" mode | emergent | **SOTA BrowseComp** as single agent | L0-1 [W] |

### 1G. Capability-aware routing / evolving-pool adjacency (α/β lineage)

| ID | System | Date | What it does (how) | Typed? | Executor binding | Missing vs typed-DAG→evolving-pool | Depth |
|---|---|---|---|---|---|---|---|
| 2410.02189 | **AOP** (Agent-Oriented Planning) | 24-10 | capability-aware decomposition; solvability/completeness/non-redundancy | capability-matched | assign to capable agent (**static descriptions**) | static profiles, no evolution | L2 [D] |
| 2604.03527 | **Topaz** | 26 | **decompose-then-route** pre-split subtasks to fixed 5-model roster by per-skill profile | skill-profiled | **fixed model roster, static benchmark profiles** | no decomposition of its own; static | L2+ [D] |
| 2509.07571 | MoMA / Generalized Routing | 25-09 | **route-then-decompose** (route to agent, decomp inside) | — | fixed registry, static profile | order inverted; static | L2 [D] |
| 2605.22057 | **FlyRoute** | 26-05 | **whole-query routing** + data-flywheel evolving profiles | — | fixed expert set (profiles evolve) | **no decomposition** | L2 [D] |
| 2604.05149 | **EvolveRouter** | 26-04 | co-evolving graph routing + prompt | — | evolving prompt variants (whole-query) | **no decomposition**; not GAIA | L1/S5 [D] |
| 2604.00901 | **HERA** | 26-04 | evolving orchestration + role prompts; query-level topology | — | fixed roles, evolving prompts | **no typed-subtask split**; not GAIA | L1/S5 [D] |
| 2603.18000 | **AgentFactory** | 26-03 | persistent, self-refining **executable subagent library** | — | grown subagent library | extractor: "decomposition, routing, forking, retirement: **Not present**"; not GAIA | L1/S5 [D] |
| 2601.23219 | MonoScale | 26-01 | expansion-aware pool onboarding + routing memory (bandit + trust-region) | — | persistent pool | **no decomposition, no harness-config** | L1 [D] |
| 2604.15034 | Autogenesis (Zhang/Bo An) | 26-04 | self-evolving protocol; versioned agents/tools/prompts/memory | — | versioned resources | not decomp→pool routing | L1-2 [D] |
| 2605.09539 | TacoMAS | 26-05 | test-time capability+topology co-evolution; emergent decomposer/verifier | emergent | co-evolved topology | not harness-config pool routing | L1 [D] |
| 2602.06511 | Evol-MAS | 26-02 | MAS topology/capability co-evolution | — | evolved MAS | genealogy only | L1 [D] |
| 2606.21307 | TASER | 26-06 | atomic-skill expand + route (continual-learning modules) | task-conditioned gating | CL skill modules | **no task decomposition**; not GAIA | L1/S5 [D] |

---

## 2. TAXONOMY + FREQUENCY — which decomposition mechanism dominates in 2026?

**Eight mechanisms observed** (a system may combine two):

1. **One-shot upfront plan (decomposition-first)** — TDP, Uno-Orchestra, OneManCompany, AgentOrchestra, AOP, MiroFlow, Leni, Plan-and-Act, ManuSearch, DecomposeR, our D1-lite.
2. **Iterative / interleaved replan (decomposition-interleaved)** — AOrchestra, Magentic-One, OxyGent, JoyAgent (hybrid), WebUncertainty, E3 (expand-on-fail), Agent S2.
3. **Recursive decomposition** — InfiAgent (pyramid, agent-as-tool), MiroFlow (nestable), Workforce (recursive re-decomp).
4. **Fixed program-template / SOP (NOT LLM-generated)** — MetaGPT (SOP), Agentless, AutoCodeRover, Cradle, 2605.15425 (control logic), deep-research fixed-4-stage. AgentOrchestra's role set is effectively a template.
5. **Learned-via-training decomposition** — Uno (SFT+GRPO), AOrchestra (SFT orchestrator), Plan-and-Act, Workforce/OWL, MasRouter, DecomposeR (RL), DeepResearcher (RL-emergent).
6. **Searched / optimized workflow (offline)** — AFlow, MaAS, MasRouter, GPTSwarm, DAAO, EvoFlow, MetaGen.
7. **Adaptive difficulty/structure gating** — AdaptOrch (DAG-shape), E3 (difficulty→scope), WebUncertainty (uncertainty), MaAS (per-query), InfiAgent (self-evolve).
8. **No explicit decomposition (single loop / whole-query routing)** — Alita, ALITA-G, smolagents, h2oGPTe, CAMEL, AutoGen, WebThinker, DeepResearcher, Tongyi/WebSailor, SWE-agent, OpenHands, SWE-Gym/-smith, FlyRoute, EvolveRouter, HERA, GraphRouter, MonoScale, AgentFactory.

**Frequency verdict (2026):**
- **The single most common pattern among the very TOP scorers is "no explicit decomposition"** — a single agent (often RL-tuned) with strong scaffolding/tools. The current open GAIA SOTA (ALITA-G, 83.03 p@1) and BrowseComp SOTA (Tongyi/WebSailor family; Kimi K3 model 0.912) are **single-agent**, not decomposed MAS.
- **Among systems that DO decompose, "orchestrator-led upfront-plan + interleaved/on-failure replan" dominates** (Magentic-One, Workforce, MiroFlow, OxyGent, JoyAgent, AOrchestra). Pure static one-shot plans without replan are a minority; fully-learned decomposition and offline-searched workflows are their own clusters.
- **Typed subtask systems are RARE.** Almost everyone types the **executor** (role/domain/tool), **not the subtask**. First-hand typed-*subtask* schemes: Uno-Orchestra (model,primitive), OneManCompany (6 interfaces), TapeAgents (typed steps), MasRouter (typed stages), Meta-Agent (generated specs), AFlow/MaAS (typed operators), and our D1-lite (4-class). That is ~7 of ~45 systems.
- **Plan format**: natural-language step-lists dominate; explicit **DAG** appears in TDP, AdaptOrch, InfiAgent, OxyGent (runtime), MiroFlow, Agentic Lybic, AFlow (code graph), Meta-Agent, DecomposeR, and D1-lite. Formal JSON/typed-slot schemas are uncommon (HuggingGPT-style {task,id,dep,args}; our D1-lite {id,type,instruction,dep}).

---

## 3. EFFECT CORRELATION — did decomposition help, and by how much?

### 3A. GAIA/web high scorers vs their decomposition style
- **Highest scorers do the LEAST decomposition.** ALITA-G 83.03 (none), Alita 75.15 (none), Tongyi/WebSailor BrowseComp SOTA (single-agent RL). AgentOrchestra's 89.04 (test) came **mainly from adding a Tool-Generator agent, not from decomposition**.
- Where decomposition is present in a topper, it is **orchestrator+replan with a fixed role set or fresh-spawn**, and the reported headline gain is usually attributable to **scaffolding/routing/specialists/tools**, not the split itself.

### 3B. Standalone decomposition ablations (the honest number table)
| Source | Setup | Result | Direction |
|---|---|---|---|
| JoyAgent 2510.00510 [D] | GAIA | single ReAct **71.5** > multi-agent decomp **70.3** < fusion **75.2** | **decomp alone HURTS** |
| MiroFlow 2602.22808 [W,L2] | GAIA-Val | single-agent **74.8** > multi-agent **71.9** | **decomp HURTS** (single-source, verify) |
| Magentic-One 2411.04468 [D+W] | GAIA | remove dual-ledger **−31%**; agent removals −21..39% | **decomp-STATE load-bearing** (helps) |
| AgentOrchestra 2506.12508 [D] | GAIA | cumulative 36.54→89.04, gain **mostly Tool-Generator** | decomp not the lever |
| Leni 2607.17044 [D,S5] | GAIA | "most uplift from scaffolding/routing/specialists; verification isolated **+1.5**" | routing/specialists > the step |
| AdaptOrch 2602.16873 [D,L3] | SWE-bench/GPQA/HotpotQA | **+6.9..+9.8pp** vs static-parallel/sequential | **adaptive structure HELPS** (off-GAIA) |
| E3 2607.13034 [D,L3] | MSE-Bench | 100% success, **−85% cost**, **acc Δ≈0** | efficiency win only |
| Uno-Orchestra 2605.05007 [D] | GAIA | 82.0 < AgentOrchestra 83.4; **10× cheaper** | typed routing = cost win, acc loss |
| 2604.02460 Tran&Kiela [O] | multi-hop, **equal thinking-token budget** | **single-agent ≥ multi-agent**; "MAS advantages better explained by unaccounted computation and context" | **decomp/MAS not inherent** |
| 2606.13003 [D,S5] | incl. BrowseComp-Plus | automatic MAS **underperform CoT-SC despite ~10× cost** | **decomp/MAS loses under matched budget** |
| 2512.08296 Scaling-Science [O] | 260 configs, 5 archs | **+80.8% on decomposable tasks to −70.0% on sequential planning** | **task-structure alignment is the lever** |
| 2601.12307 OneFlow [D,S5] | — | single agent matches **homogeneous** workflows | homogeneous split absorbable |
| 2605.15425 [O] | coding | runtime-structured **−73.2% retry cost vs static decomposition** | **static decomp is the weak baseline** (cost, coding) |
| 2603.28005 [O] | QA judging | holistic ≥ atomic on 2/3 | atomic decomposition not free |

**Net reading (guardrails-honest):** the 2026 evidence is that **decomposition is not a reliable accuracy win**; its demonstrated payoff is **cost/efficiency at ~equal accuracy** (E3, AdaptOrch, Uno) or is **task-structure-dependent** (+80.8% decomposable / −70% sequential; Magentic-One helps, JoyAgent/MiroFlow hurt). Under **matched compute budget**, several first-hand results say a single agent matches or beats the MAS. This is *convergent across ≥5 independent groups* and is the strongest signal in the corpus.

---

## 4. OUR D1-lite POSITIONING (where the static one-shot typed DAG lands)

**D1-lite (incumbent), precise form** (from DIVISION-DESIGN §2.2/§8):
> front-loaded lightweight planner (meta model, 1 call) → **four-typed subtask DAG {search, browse, compute, verify}, serial, recursion forbidden** → route by **(variant × subtype) ledger** (Laplace-smoothed, cold-start falls back to whole-task cluster prior) → fixed variant-agnostic synthesis step → inter-stage verify gate (flag-controlled). Credit = **observational** (per-cell pass-rate at the deterministic end-task gate, zero extra rollout); leave-one-slot-out only for calibration. **Training-free.** Executor target = a **persistent, seesaw-gated, fork/retire-evolving pool of harness-config variants**.

**Taxonomy cell:** *decomposition-first · hierarchical · typed-slot · DAG · training-free · single upfront planner · subtask-typed routing to an EVOLVING executor pool.*

**Same-cell neighbours (who is closest, and what each misses):**
| Neighbour | Shares | Misses vs D1-lite |
|---|---|---|
| HuggingGPT (ancestor) | typed {task,id,dep,args} slot DAG, training-free | no GAIA-web, no evolving pool |
| TDP 2601.07577 | training-free DAG + scoped context | **no typed subtasks, single executor** (no routing) |
| Uno-Orchestra 2605.05007 | typed subtask routing on GAIA | **trained** (SFT+GRPO); fixed primitives; no evolving pool |
| Topaz 2604.03527 | typed subtask → expert routing | **static profiles, fixed roster, decompose-*then*-route, no evolution** |
| OneManCompany 2604.22446 | training-free typed division-of-labor | **off-GAIA (software)**, no evolving pool |
| AgentOrchestra 2506.12508 | hierarchical decompose + delegate | **fixed manual roles**, not evolving; and decomp not its lever |
| AOrchestra 2602.03786 | decompose + per-subtask executor, GAIA 80 | **fresh-spawn & discard, no pool, SFT orchestrator** |
| Meta-Agent 2605.25233 ⚑ | inference-time typed-spec DAG + routing + regeneration | **workers generated fresh per task** (no persistent evolving pool); GAIA number not shown |

**The empty cell = D1-lite's cell:** *training-free × static one-shot typed DAG × GAIA-web × routed to a self-evolved seesaw-gated harness-config pool.* **No single system occupies the full conjunction.** But note the honest caveat surfaced by this census: **the individual ingredients are more common than a GAIA-only reading suggests** — typed-subtask→expert-executor routing is routine in GUI/OS agents (UFO2, Agent S2, Agentic Lybic) and now inference-time in Meta-Agent; the only genuinely open piece is **"routed to a pool that a harness self-evolution loop grew and gated,"** plus the empirical claim that this **beats fresh-spawn / trained-specialist routing under matched budget on GAIA**. That empirical head-to-head — not the architecture novelty — is the defensible contribution.

**Extra positioning fact for the write-up:** static one-shot decomposition is, in one first-hand coding result (2605.15425), the *weak* baseline that a runtime-structured variant beats on retry cost (−73.2%). We are choosing static one-shot deliberately (cross-arm replay / variance control), so we should **pre-empt this** by (a) confining the claim to accuracy-under-matched-budget on GAIA, not efficiency, and (b) keeping E3-style difficulty-gating and D4-style evolved decomposition as explicit backlog arms, not as the headline.

---

## 5. RED FLAGS — 2026 "typed subtasks → heterogeneous executor routing" we had NOT catalogued

Ranked by how close each is to our target and how much it was missing from our four prior docs.

1. **⚑ HIGH — Meta-Agent 2605.25233 (MAS, 26-05)** — *inference-time* LLM-generated **DAG of agent specs with I/O contracts + verification criteria**, coordinator dispatches to generated workers with **exec-time gating** and **targeted regeneration**. This is the closest **new** analogue to our design that our docs never logged: it is (near-)training-free, typed, DAG-planned, routed, and self-correcting. It differs from us in that **workers are generated fresh per task (no persistent evolving pool)** and it reports **no GAIA number** (L1, abstract; get method + benches before relying on it). **Action: add to occupancy watch immediately; verify whether a persistent/reused pool appears in the body.**

2. **MED — GUI hierarchical decomposers (our GAIA-centric docs missed the whole GUI column):**
   - **Agent S2 2504.00906** — Manager decomposes into a **subgoal list** → Worker + **Mixture-of-Grounding expert pool**; proactive + on-failure replan; +18.9/+32.7% rel. The typed-decomp→expert-pool pattern, **live and ablated**, in GUI.
   - **UFO2 2504.14603** — HostAgent **task decomposition → per-application expert AppAgents** (typed by app).
   - **Agentic Lybic 2509.11067** — manager-worker **DAG decomposition → worker pool**, tiered orchestration.
   These route by app/grounding (not a rich subtask taxonomy) and are off-GAIA, so they do not scoop the exact cell — but they prove the *ingredient* (typed decomposition → heterogeneous executor pool) is **common and evaluated** outside GAIA. **Action: cite as cross-domain precedent so a reviewer cannot call the pattern novel.**

3. **LOW — MasRouter 2502.11133 (MAS, 25-02)** — learned 3-stage cascade collab-mode→roles→LLM/role, per-query rebuild, typed. Routes roles to expert-models; but **learned** and it is MAS-config routing, not subtask-typed routing to a pool. Genealogy, not scoop.

No system found does **training-free typed-DAG → self-evolved gated harness-config pool on GAIA-web**. The cell holds. But #1 (Meta-Agent) and #2 (GUI column) materially raise the related-work bar and should be added to the corpus.

---

## 6. VERIFIED vs IMPRESSION (depth ledger)

**First-hand this session (L1, abstract opened, neutral prompt):** 2603.28005 (answer-claim decomp for judges — NOT task-execution), 2509.22502 (InfiAgent, recursive self-evolving DAG), 2604.02460 (Tran&Kiela, single≥multi at matched tokens — **previously flagged UNVERIFIED, now confirmed real**), 2512.08296 (Scaling-Science, +80.8%/−70% — confirmed real), 2605.15425 (Runtime-Structured coding decomp, −73.2% vs static).

**Carried at L2–L3/S5 from prior docs (already neutral-verified there):** TDP, AdaptOrch, E3, AOrchestra, JoyAgent, Uno-Orchestra, OneManCompany, AgentOrchestra, Topaz, AOP, FlyRoute, MoMA, Exact-Is-Easier; S5 quotes in hand for Leni 2607.17044, 2606.09863, 2605.27621, TreeMem 2605.04811, EvolveRouter 2604.05149, TASER 2606.21307, AgentFactory 2603.18000, HERA 2604.00901, 2606.13003, OneFlow 2601.12307.

**This-session breadth sweep (L1–L2, single pass — treat as indicative):** all rows in §1B–§1F tagged [W]. **Single-source claims to re-verify before load-bearing use:** MiroFlow single-vs-multi 74.8/71.9; ALITA-G 83.03 SOTA; Agentic Lybic/Meta-Agent mechanisms; Tongyi/WebSailor architecture.

**UNVERIFIED (no resolvable arXiv — never load-bearing):** smolagents/CodeAgent, h2oGPTe, WebSailor/WebDancer sub-IDs.

**L0 / not opened (impression only, from docs' PART B — do NOT cite without opening):** GPA 2510.08847 (reference-free plan eval = "open gap" per snippet), GUIDE 2604.04399, CRAB-Bench 2606.01815, Clarus 2606.30246, Iterative Critique-and-Routing 2605.08686, Agent Planning Benchmark 2606.04874, EvoFlow 2502.07373, MetaGen 2601.19290.

**Adjacent, NOT task-execution decomposition (do not mis-file as methods):** 2603.28005 (LLM-judge answer decomposition), TRACE 2510.02837 (trajectory eval), 2606.09863 (false-success/judge reliability), 2605.27621 (removal-based credit), TreeMem 2605.04811 (MC credit), Exact-Is-Easier 2603.06859 (LOO credit), GraphRouter 2410.03834 (model selection).
