# NOVELTY × 实验设计 × 占位核验(Jul-30 深扫)

> 研究员:researcher (Opus 4.8)。窗口 = 6 月中 ~ 7 月底 2026,优先 ~Jul-27 上轮扫描之后新货。
> 网检仅 WebSearch/WebFetch。**反幻觉纪律**:占位/负判以亲开 abstract 的中性抽取为准
> (承前 DECOMP-LITSCAN-B 检索日志⑥ leading 提问致 confab 的教训);未亲开的以 L0 标注、禁承重。
> 我方基线 = HarnessX/AEGIS 2606.14249;增量 = task decomposition + 子任务级分工路由到**演化出的**变体池。
>
> **查证深度标签**:S5 = 亲取 verbatim 引句在手 / L1 = 亲开 abstract 中性抽取 / L2 = 略读 §method /
> L3 = 上轮深核(本轮沿用) / L0 = 仅搜索摘要二手(**不作承重**)。

---

## 0. TL;DR(三问结论)

- **Q1**:6 周窗口新货未撼动我方缝,但补齐三块承重外证 + 一个必须新增的对照臂来源。最相关新货 =
  **2607.17044**(Jul-19,production GAIA agent,planner+specialist 路由,**把 uplift 拆成组件**,
  75.2% pass@1)——它既是 Q1(a/d) 的 SOTA 锚,又是 Q2 "组件归因" 的方法范式 + 撞车监测项(但用
  post-trained 专家,非演化 harness-config 池)。
- **Q2 最大漏洞 = 算力/token 混淆**(examiner 一击必杀)。已用**两篇一手**(2606.13003 / 2601.12307)
  锁死:自动 MAS 在配平预算下输给 CoT-SC(10× 更贵);且 OneFlow 证同底座**同质**工作流可被单智能体
  多轮吃掉。⇒ headline B2−B1 必须(a)配平调用预算,(b)加一条**算力配平单智能体基线**,(c)B1 用
  round_robin(等预算均匀分工)而非 single。
- **Q3**:四合取缝 [分解]×[子任务粒度路由]×[**自演化 seesaw 池**]×[training-free harness-config·GAIA]
  **仍无人全占**;本轮新邻(AgentFactory / 2607.17044 / HERA / EvolveRouter / TASER)各占**相邻格**,
  强化而非闭合缝。最可辩护 novelty = **开放实证问句式**(见 §4 候选 1),非裸 "first"。

---

## PART A — 已核验(亲开来源)

### Q1(a) GAIA 系任务分解 SOTA

**[新·承重] 2607.17044 — "Where Does Agent Reliability Come From? ..."(Arunabh Dastidar & the Leni Team,提交 2026-07-19)· L1(abstract 中性抽取,S5 引句在手)**
- WHY:多步企业 agent 在"决定答案→提交"之间无检查点而静默失败。
- HOW:production 系统 Leni = verification loops(execute/observe/compare/correct)+ **lightweight
  task-specialized post-trained models** + planner 路由 executors 跨异质模型。
- WHAT / 数字:GAIA validation **75.2% pass@1(n=165);83.0% best-of-k**;SpreadsheetBench 91.25 vs 80.25
  (n=400, p<0.001);BullshitBench 98 vs 91(n=100)。
- **S5 承重引句**(arXiv:2607.17044 abstract):
  > "Our central contribution is a decomposition of that uplift: **most of it comes from scaffolding,
  > routing, and specialist models rather than from the verification step itself**, whose isolated
  > contribution is small (+1.5 points)…"
  > "…verification loops … staffed by **lightweight task-specialized post-trained models**."
- **对 Q2 的用途**:①"把 uplift 拆成组件"= 我方 B0/B1/B2 归因逻辑的**同期一手先例**,related work + method
  可正引;②rigor 范式(报 n / p / 混淆矩阵 / valid-premise 控制)可照抄;③报 pass@1 **与** best-of-k
  分列——我方也须如此(防 best-of-k 膨胀)。
- **与我方分界**(非 scoop):专家 = **post-trained** 异质**基础模型**(Opus/Sonnet/Haiku/OpenAI),非
  training-free harness-config 变体;无 seesaw fork/retire 演化池;是"人搭 production 系统的组件审计",
  非"自演化池上的子任务分工"。**撞车度中**:它占了 "GAIA 上 planner+specialist 路由 + 组件归因",
  我方剩 "池由演化循环长出 + 免训练 + 门控特化"。

**[re-confirm] AOrchestra 2602.03786 · L3(上轮)/ 本轮搜索复现**:仍是 α 主占位者,GAIA 80.0(Gemini-3-Flash);
"每子任务 fresh container、跑完即弃、无池"负判不变。搜索复现其 "SFT 改进 decomposition/synthesis +11.51% pass@1"。
**[re-confirm] Uno-Orchestra 2605.05007 · L2(上轮)**:typed (model,primitive) 子任务路由,但**需 SFT 61k+GRPO**;
GAIA 82.0 **输** AgentOrchestra 83.4。训练依赖 = 与我方 training-free 的硬分界,不变。

### Q1(b) 无真值下的分解质量评估

**核心发现**:存在"无真值**轨迹**评估"与"false-success 检测",但**无人做"分解计划质量(plan quality)的
reference-free 评估"并用于 GAIA-web**;且他人明确承认这是开放缺口。⇒ 支持我方**回避**该未解问题、改以
**确定性终答案门 + E0 神谕对照 + 组件归因**为锚的设计。

**[新·承重] 2606.09863 — "From Confident Closing to Silent Failure: Characterizing False Success in LLM Agents"
(Laksh Advani,提交 2026-06-01)· L1(S5 引句在手)**
- WHAT:false success 普遍;**LLM judge 不可靠**。
- **S5 引句**(arXiv:2606.09863 abstract):
  > "LLM judges fail reliably: no configuration across 5 judges, 5 prompt strategies, and full task
  > specifications **exceeds AUROC 0.65** on tau2-bench, and the same judges reach only 0.54 AUROC on
  > AppWorld…"；"Lightweight TF-IDF detectors achieve … 0.83 … 0.95 …"
- 床 = tau2-bench / AppWorld(**非 GAIA**)。
- **用途**:承重外证支持我方**不用子任务 LLM 判官**、依赖**确定性 exact-match 门**;Q2 预注册可引。

**[新] 2510.02837 — "Beyond the Final Answer …"(TRACE;Wonjoong Kim 等;v1 2025-10-03,v3 2026-05-25)· L1**
- reference-free 多维**轨迹**评估 + evidence bank;自建 meta-eval 数据集(flawed trajectories)。
- **分界**:评 efficiency/hallucination/adaptivity **轨迹**质量,非"分解计划质量",非 GAIA-decomp。related work 锚。

**[L0 二手,未亲开——印象节见 PART B]**:Agent Planning Benchmark 2606.04874(reference-**aware** LLM-judge)/
GPA 2510.08847(**明言 reference-free plan eval 是 critical 开放缺口**)/ GUIDE 2604.04399(事后轨迹分解诊断)/
CRAB-Bench 2606.01815 / Rethinking Atomic Decomposition 2603.28005。

### Q1(c) 无子任务真值的 credit assignment

**[新·承重] 2605.27621 — "Agents that Matter: Optimizing Multi-Agent LLMs via Removal-Based Attribution"
(Mingyu Lu, Yushan Huang, Chris Lin, Su-In Lee;提交 2026-05-26)· L1(S5 引句在手)**
- HOW:agent attribution 形式化为**合作博弈**(coalition dist / removal protocol / target metric)。
- **S5 承重引句**(arXiv:2605.27621 abstract):
  > "Leave-One-Out (LOO) identifies bottleneck agents **as effectively as combinatorial methods**, but at
  > a fraction of the computational cost."
  > "**Agent ablation isolates structural bottlenecks, whereas introspective LLM judges fail to faithfully
  > approximate this behavior.**"
- **用途(双重承重)**:①正面背书我方 **leave-one-slot-out 校准**(LOO≈组合法但省算力);②再证**弃用子任务
  LLM 判官**(introspective judge 失真)。⇒ 我方 P-A 裁决(DECOMP-DIVISION-DESIGN §2.1)现有一手外证。
- **勘误连带**:上轮记 "AgentProp κ=0.432 / Who&When 14.2%" 结论方向被本篇独立佐证。

**[新] 2605.04811 — "Tree-based Credit Assignment for Multi-Agent Memory System"(TreeMem;Marina Mao 等;
提交 2026-05-06)· L1(S5 引句在手)**
- **S5 引句**:"derives agent-specific credit from the final reward **without task-specific annotations**";
  "extends the … pipeline (builder–summarizer–retrieval) into a **tree structure** … Monte Carlo averaging over
  its subsequent branches"。
- **分界 = 成本论据**:TreeMem 无标注但靠**MC 树分支 = 大量额外 rollout**;我方观察式账本 = **零额外 rollout**。
  可作 related work 正引 + "我方更省"的对照。

**[re-confirm] Exact Is Easier 2603.06859 · L2(上轮)**:LOO/反事实信用,我方 LOO 方案最佳正面先例,不变。

### Q1(d) 分解 × 异质执行器路由/调度

**[新] 2604.05149 — "EvolveRouter: Co-Evolving Routing and Prompt for Multi-Agent QA"(Jiatan Huang 等;提交
2026-04-06)· L1(S5 引句在手)**
- **S5 引句**:"they typically optimize over a **fixed pool of agents without improving the agents themselves**";
  "couples **graph-based query routing** with targeted instruction refinement in a **closed-loop co-evolution**"。
- **分界**:**整 query 路由,无任务分解**;"agent" = prompt 变体(co-evolve),非 seesaw 门控 harness-config 池;
  5 个 QA 床(非 GAIA)。= FlyRoute 邻格 + "同时改 agent"。β 谱系锚,非 scoop。

**[新] 2606.21307 — "TASER: Task-differentiated Atomic Skill Expansion and Routing …"(Jiacheng Wang 等;提交
2026-06-19)· L1(S5 引句在手)**
- **S5 引句**:"jointly determines **how many new atomic skills to introduce** for each task and **which skills to
  activate**";"skill dynamic routing … through lightweight task-conditioned gating"。
- **分界**:**continual-learning 权重/技能模块层**(HeteroCLBench,非 GAIA);"expand+route" 概念与我方
  fork/retire+route 平行,但对象是 CL 技能模块非 harness-config,无任务分解。谱系锚,非 scoop。

**[新·相邻] 2603.18000 — "AgentFactory: Self-Evolving via Executable Subagent Accumulation and Reuse"(Zhang
Zhang, Shuqi Lu, Hongjin Qian, Di He, Zheng Liu;提交 2026-03-18)· L1(S5 引句在手)**
- **S5 引句**:"preserves successful task solutions as **executable subagent code** …";"its **library of
  executable subagents grows and improves over time** …"。抽取器明示:"decomposition, routing, forking,
  retirement: **Not present**";GAIA **not mentioned**。
- **分界(占了"持久累积池"半格)**:AgentFactory = 经验即代码的**持久累积/自refine subagent 库**,但**无
  任务分解、无门控路由、无 seesaw fork/retire、非 GAIA**;我方 = 分解 + 子任务路由 + seesaw 门控演化
  harness-config 池。**是 α "持久池" 半边的新邻,不占我方合取**。

**[新·相邻] 2604.00901 — "HERA / Experience as a Compass: Multi-agent RAG with Evolving Orchestration and
Agent Prompts"(Sha Li, Naren Ramakrishnan;提交 2026-04-01)· L1(S5 引句在手)**
- **S5 引句**:"jointly evolves multi-agent orchestration and role-specific agent prompts";"optimizes
  query-specific agent topologies through reward-guided sampling and experience accumulation";"emergent
  self-organization … sparse exploration yields compact, high-utility multi-agent networks"。6 KI 床(非 GAIA)。
- **分界**:演化 orchestration+**固定角色**的 prompt,query 级拓扑,非"分解为 typed 子任务 → 路由到 harness-config
  演化池",非 GAIA。β 谱系锚(演化画像/拓扑),非 scoop。

---

### Q3 占位核验 — 近邻账本(本轮更新)

命题 = **task decomposition + 子任务级路由到"由 harness 演化循环自己长出"的变体池(GAIA-web)**。
拆成四个承重要件:①分解 ②子任务粒度路由 ③池由**自演化循环**长出并**门控特化**(seesaw fork/retire)
④**training-free harness-config** 变体 · GAIA-web。

| 近邻 (arXiv) | 深度 | 逐字占了什么 | 缺哪个要件 |
|---|---|---|---|
| AOrchestra 2602.03786 | L3 | ①②(分解+每子任务造执行器,GAIA 80.0) | **③④**:fresh-spawn 即弃、无池、无演化 |
| Uno-Orchestra 2605.05007 | L2 | ①②(typed 子任务路由,GAIA 82.0) | **③④**:需 SFT+GRPO(非 free)、路由到固定 primitive、无演化池 |
| 2607.17044 (Leni) | L1 | ①②(planner+specialist 路由,GAIA 75.2)+ 组件归因 | **③④**:post-trained 基础模型专家、无 seesaw 演化 harness 池 |
| AgentFactory 2603.18000 | L1 | ③半(持久累积+自refine 池) | **①②**:无分解/路由;④:subagent 代码非 harness-config;非 GAIA |
| AgentOrchestra 2506.12508 | L2 | ①②(分层分解+委派) | ③:**手工固定角色**非演化涌现;撞车组 |
| HERA 2604.00901 / EvolveRouter 2604.05149 / FlyRoute 2605.22057 | L1/L1/L2 | ③半(演化 router/prompt/画像) | **①**:整 query 无分解;④:固定角色/prompt 变体非 harness-config;非 GAIA |
| Topaz 2604.03527 / AOP 2410.02189 | L2 | ①②(能力感知分解→路由) | ③:**静态**画像/描述,无演化 |
| TASER 2606.21307 | L1 | ②③半(expand+route+gating) | ①:CL 技能模块非任务分解;④:非 harness-config·非 GAIA |

**裁定**:四合取**仍无单篇全占**。本轮五个新邻各占**相邻格**(持久池 / GAIA 专家路由 / 演化 router),
**强化缝的四周而非闭合**。承重 novelty 只能落**合取 + 实证胜出**,不能落单一要件。

**撞车监测**:
- **最高·组件归因同期**:2607.17044(Jul-19)与我方 B0/B1/B2 归因逻辑同构 → 立即成为 related-work 必引 +
  "我方与其分界 = 演化池 vs post-trained 专家" 必须写清。
- **最高·主占位**:AOrchestra 不变(承重 (c) 头对头 = 演化池 > fresh-spawn)。
- **Zhang/Bo An scoop**:本轮 July 搜索**未见** AgentOrchestra+Autogenesis 的整合单篇;两半仍分立。状态不变(监测)。

---

## PART B — 印象 / 未核验(L0,禁承重)

以下仅搜索摘要,**未亲开 abstract**,引用前须亲验:
- **Q1(b) plan-quality 评估**:Agent Planning Benchmark **2606.04874**(称 reference-aware LLM-judge+多粒度)/
  **GPA 2510.08847**(据摘要"称 reference-free plan eval 是 critical 开放缺口"——若属实 = 我方"回避分解质量
  直评"的最佳背书,**优先亲验**)/ GUIDE **2604.04399** / CRAB-Bench **2606.01815** / Rethinking Atomic
  Decomposition **2603.28005**。
- **Q1(c)**:C3 / CCPO(counterfactual credit,搜索称 RL 训练期)/ Clarus 2606.30246。
- **Q1(d)**:Iterative Critique-and-Routing 2605.08686 / InfiAgent 2509.22502(DAG 分解+路由,自演化 pyramid,
  但 2025-09 旧)/ MasRouter / GraphRouter(已知谱系)。
- **Q2 token-matched**:**arXiv:2604.02460**(Tran & Kiela "Single-Agent…Equal Thinking Token Budgets")——
  **ID 未核验**(仅 blog/beancount.io 二手),**禁引**直到亲开;所幸现象已由下方两篇一手锁定,不依赖它。
- "Towards a Science of Scaling Agent Systems" 2512.08296 —— 承 MEMORY⑧ "承重句未验勿引",维持。

---

## PART C — Q2 实验设计审查(苛刻 examiner 视角)

给定臂梯:E0 神谕分解注入门 → B0 纯池 / B1 只分解不分工 / B2 分解+分工,headline = **B2−B1**,
decomp_plans.json 跨臂重放,观察式信用,exact-match 门,噪声带 ±5%,种子 1(S1-FREEZE)。

### C.1 漏洞清单(按致命度排序)

1. **🔴 算力/token 混淆(examiner 一击必杀)**。B2(分解 1 调用 + N 执行器调用 + 合成)比 B1/B0 花更多调用;
   增益可能只是"花更多 token 买的"。**一手锁死**:
   - 2606.13003 S5:"automatic MAS consistently **underperform CoT-SC despite being up to 10x more expensive**"
     …"failing to account for the **marginal utility of increased computational cost**"(床含 **BrowseComp-Plus**,web 系)。
   - 2601.12307 (OneFlow) S5:"a single agent can **reach the performance of homogeneous workflows**…"——
     且关键:"single-LLM methods **cannot capture heterogeneous workflows** due to lack of KV cache sharing…
     highlighting future opportunities in developing **truly heterogeneous** multi-agent systems"。
   - **威胁双面**:我方 K=8 变体**同底座**(DeepSeek),差异只在 harness prompt/config = OneFlow 定义的
     "**同质**"工作流 → 可被单智能体多轮吃掉。**必须**(a)配平调用预算,(b)加**算力配平单智能体基线**
     (给单体等于 B2 的总调用预算做多轮/self-consistency),(c)把 headline 从 B2−B1 改为 **B2−B1(round_robin)**
     = 等预算下"专业化路由 vs 均匀分工"的净值(见 C.2)。**反守为攻**:把 harness-config 差异 pitch 成 OneFlow
     所称"truly heterogeneous"缝(单体无法 KV-share 不同 config)——但须实证 B2 > 配平单体,否则被判 architectural bloat。

2. **🔴 "分工收益" vs "只是用了多个变体" 混淆**。B2(ledger 路由)可能仅因动用多变体而非**智能**路由取胜。
   **缺一个对照臂**:B2-random / B1-round_robin(**等调用预算的均匀/随机分工**)。若 B2(ledger) ≈ B1(round_robin),
   "特化路由" 主张作废。→ **B1 冻结须选 round_robin**(等预算),headline = B2−B1(rr) 才干净;B1-single 另答
   "分解+管线本身收益"(与 B0 比)。

3. **🟠 组件归因**。B2−B1 的增益来自(i)分工?(ii)路由把难 subtype 恰好给强变体的选择效应?(iii)额外合成步?
   现设计已把**合成器设为变体无关** + decomp 跨臂重放 = 隔离 (i)/(iii),好;但 (ii) 需 B2-random 控(见 2)。
   范式外证:2607.17044 "decomposition of that uplift"、2605.27621 "ablation > LLM judge"、AgentOrchestra
   "增益来自 Tool-Generator 非分解"。

4. **🟠 单种子(种子 1)做 headline 差**。103 题 pass@2 的臂间差无方差估计。**修**:跨臂 decomp_plans.json 重放
   ⇒ 用**配对 McNemar 精确检验**(题内配对,已含方差缩减);预算允许则执行器采样 ≥3 种子;噪声带由数据自产。

5. **🟠 池 provenance 非平稳(scope 限制)**。K=8 池在 S1 是**整任务路由**下演化出的,B 臂却在**子任务**粒度重用;
   测得的 (variant×type) 特化未必等于"若池在子任务反馈下演化"的形态。→ 诚实声明:我方测"**整任务演化的池**上做
   子任务路由",非"子任务反馈共演化池"(后者 = B3/v2)。

6. **🟠 分解质量无真值可判**。无法判 LLM 分解是否"对"。**修**:**不主张分解质量**;锚定终答案 exact-match +
   fallback 率 + **E0 神谕分解对照**(oracle 上限)。外证缺口:GPA 2510.08847(待亲验)。

7. **🟡 n 小 / text-only 子集(外部效度)**。103 题、pass@2、text-only 排除多模态 GAIA。→ 声明;报 **pass@1 与
   best-of-k 分列**(2607.17044 纪律,防 best-of-k 膨胀);配对检验缓解 n 小。

8. **🟡 B0 "fresh-spawn 类比" 保真度**。B0 = 分解+单 h0 变体,**非** AOrchestra 的每子任务 fresh tuple+工具合成;
   已声明"工具合成不复现"。→ 框成"**单变体基线**"而非"AOrchestra 复现",防 examiner 打保真度。

9. **🟡 污染/泄漏**。GAIA validation 或在底座训练集内。→ 声明;但**配对差 B2−B1 抵消共享污染**(同底座)=
   设计强项,须明写。

### C.2 最小可辩护臂集(headline 防三杀)

| 臂 | 配置 | 回答 / 防御 |
|---|---|---|
| **E0** | oracle 分解注入(file:)+ 池 | 上限锚:LLM-decomp 臂达 E0 的百分比 = "分解质量是否瓶颈" |
| **SA-matched**(**新增·必须**) | 单变体多轮 / self-consistency,**总调用预算 = B2** | 防 2606.13003/2601.12307 的"配平即消失"杀;B2 须 > 此臂 |
| **B1(round_robin)** | 分解 + 池 + 均匀分工(**等 B2 调用预算**) | headline 分母;隔离"特化路由 vs 均匀分工" |
| **B2(ledger)** | 分解 + 池 + (variant×type) 成功率路由 | **headline = B2 − B1(rr)**;特化路由净值 |
| (可选) B1(single) | 分解 + 单变体全程 | 与 B0 比 = 分解+管线收益;与 B2 比 = 分工+特化合并收益 |
| (语境) A1 | 现 AEGIS K=8 整任务路由 | 论文复现 + "不分解"基线 |

> A0(K=1)只服务 S1 复现叙事,decomp headline 非必需。B0(fresh-spawn 对照)保留但降为"保真度受限的类比臂"。

### C.3 无子任务真值的 credit-assignment 指标选项

- **主(零成本,描述性)**:观察式 (variant×type) 成功率累积。**只报为"特化画像",不称因果信用**
  (受兄弟子任务共现混淆)。
- **校准(子集,少量成本)**:leave-one-slot-out 反事实(把某 subtype 改路由到另一变体,测 Δ 终答案)。
  **一手背书**:2605.27621(LOO≈组合法、省算力)、2603.06859。
- **弃用**:子任务级 LLM 判官。**一手反证**:2606.09863(AUROC≤0.65)、2605.27621(introspective judge 失真)。
- **不采(太贵)**:TreeMem 2605.04811 的 MC 树分支(大量额外 rollout);我方零 rollout 更省,作对照正引。

### C.4 该预注册的判读规则(开跑前写死)

1. **headline**:B2(ledger) − B1(round_robin) 的**题内配对差**(103 题,pass@2),**McNemar 精确检验 + 95% CI**。
2. **噪声带 ±5%(≈5 题)**:带内 = 无效应,**不作改进/退化声明**(承 M-25)。
3. **成本全臂必报**(Serper 调用 + 模型调用 + 墙钟);headline **原始值 + 成本归一值**双报;B1(rr) 结构上配平 B2 执行器调用数。
4. **特化真实性门**:B2(ledger) − B1(round_robin/random) 须 > 噪声带,否则**如实报"路由不优于均匀"**。
5. **组件归因决策树(预注册)**:(a) B2−B1rr>带 ⇒ 特化路由真;(b) B1single−B0>带 但 B2−B1rr≤带 ⇒ 增益来自
   **池/管线非特化路由**(如实报);(c) B 臂 ≈ A1 ⇒ **分解在 GAIA 无增益**(如实报负结果,与 JoyAgent 一致)。
6. **E0 上限**:预注册 "LLM-decomp 臂达 E0 的 ≥X% ⇒ 分解质量非瓶颈"。
7. **fallback 率**(分解解析失败→整任务)全臂必报;超阈则该臂解读加 caveat。
8. **pass@1 与 best-of-k 分列**(2607.17044 纪律)。
9. **结论作假设非证明**(guardrails A):"在 GAIA text-only 103、配平预算下未被证伪"。

---

## PART D — novelty 陈述候选(按可辩护度排序)

**候选 1(最可辩护 · 开放实证问句 · port+beat)**
> "Prior decompose-and-route agents on GAIA-family tasks either instantiate a **fresh, stateless executor
> per subtask and discard it** (AOrchestra, 2602.03786) or route subtasks/steps to **post-trained specialist
> models** (Uno-Orchestra 2605.05007; Leni 2607.17044); persistent self-improving executor libraries exist
> but **without task decomposition or gated routing** (AgentFactory 2603.18000). We pose and answer
> empirically the open question: *can a **training-free, seesaw-gated evolving pool of harness-config
> variants**, routed at subtask granularity by per-(variant, subtask-type) success, match or beat both
> fresh-spawn and post-trained-specialist routing **under a matched compute budget** on GAIA?*"
> — 可辩护度 **高**:实证问句(非裸 priority),每个近邻已亲核,"matched compute budget" 预掐 token 杀。

**候选 2(方法子贡献 · 高可辩护 · 宜作第二贡献)**
> "We assign subtask-type credit **observationally from a deterministic end-task gate at zero additional
> rollout cost**, using leave-one-slot-out only for calibration — avoiding both unreliable subtask LLM
> judges (2606.09863; 2605.27621) and rollout-heavy Monte-Carlo credit (TreeMem 2605.04811)."
> — 可辩护度 **高**(有一手外证),但是支撑性方法贡献,非 headline。

**候选 3(合取 "first" · 中可辩护 · 须加 hedge + 全 related work)**
> "To our knowledge we are first to route **decomposed subtasks** to a pool of executor variants that a
> **harness self-evolution loop itself grew and specialized** (seesaw-gated fork/retire), as opposed to a
> hand-designed role set (AgentOrchestra), a fresh-spawn tuple (AOrchestra), a static-profile roster
> (Topaz), or a whole-query self-evolving router (FlyRoute/EvolveRouter/HERA)."
> — 可辩护度 **中**:合取确无人全占,但相邻格拥挤;"first" 须 "to our knowledge" + 完整谱系引用。

---

## PART E — 开放问题 / 待办

1. **优先亲验 GPA 2510.08847**:若确称 "reference-free plan eval = 开放缺口",则升为 Part A 承重(我方回避分解直评的最佳背书)。
2. **亲验 arXiv:2604.02460**(Tran & Kiela)或直接弃用——现象已由 2606.13003/2601.12307 一手覆盖,非必需。
3. **B1 冻结二选一**:强烈建议 **round_robin**(等预算),使 headline 干净;single 作辅助臂。
4. **SA-matched 臂的实现**:单智能体多轮 + self-consistency,总调用预算 = B2——需 coder 工单(不在本研究范围)。
5. **监测**:Zhang/Bo An H2 整合单篇;2607.17044 后续版本(production 系统迭代快)。
6. **数字未再核**:JoyAgent 70.3<71.5、AgentOrchestra 89.04 系上轮 L2,本轮未重开(列为已知威胁,深度沿用)。
