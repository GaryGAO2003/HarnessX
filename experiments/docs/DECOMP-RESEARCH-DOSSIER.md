# DECOMP 研究档案：Task Decomposition × 子任务分变体分工（决策就绪版，Jul-27）

> 定位：MSc 论文核心贡献方向的调研地基。菜单 + 实验协议，非最终决定（S2 方法拍板顺延至
> S1 正式结果，见 `DECOMP-DIVISION-DESIGN.md:80`）。本档在该设计骨架上加深、不推翻。
> 语言中文为主，术语保留英文。所有对论文/代码断言带 file:line 或页码锚点。
> 查证深度标注 L0（二手）/L1（landing 页）/L2（abstract+skim 或 HTML method）/L3（PDF 关键节）。
> **占位核验的两条承重负判（AOrchestra 无演化池 / Adaptive-Auto-Harness whole-task）为 L2,
> 锁进论文 related-work 前必须亲开 PDF §method 复核**——本仓历史有 4 次 WebFetch 幻觉记录。

---

## 执行摘要（≤15 行）

1. **占位核验结论 = 弱占（WEAKLY OCCUPIED,双侧钳形,非空地）**。三合一 cell（分解 × 子任务→同底座异构配置变体 × 自演化门控池）无单篇全占,但被两面夹住。
2. **最危险的近邻 = AOrchestra(arXiv 2602.03786,FoundationAgents/MetaGPT 系,有码,GAIA 实测)**:它已做"分解 + 每子任务 spawn 一个 (Instruction,Context,Tools,Model) 执行者",**唯独缺我们的 (c)——持久、seesaw 门控、fork/retire 的演化池**(其 sub-agent 是即用即弃)。这是本方向此前 harness 线分析从未列出的论文。
3. **我们的贡献 = "给 HarnessX 加 (a) 子任务分解" ≡ "给 AOrchestra 加 (c) 演化池"**。MSc 尺度下作为"新颖组合 + 真实实证问题(门控演化池 vs 即弃式 spawn 谁更强)"可辩护,但审稿人会看见两翼。
4. **用户 Jul-27 裁定已吸收**:分解器取"易复现、公开数字硬、加法面 ≤1 文件"的成熟方法;novelty 押组合与两大成败题(信用分配、2×2 防混淆),不押分解器本身。
5. **最简可行组合(推荐)**:静态前置规划器 → 固定 4 类型化子任务(HuggingGPT-schema-lite,一次 planner 调用) × **集成点 (b) 但落 recipe 层的部署期路由**(gate/ledger 保持任务级,engine/harness 零改)。新文件 `recipe/gaia_evolver/subtask_pipeline.py`(~350-550 行),旗标 `--decomp`/`--subtask-routing`。
6. **信用分配(承重墙)**:主 = outcome-only aggregated bandit(零额外 rollout,reward=任务级 pass@2);可选加固 = 有界 fixed-context LOO(C3 2603.06859,因同底座变体故 counterfactual 干净)。子任务级 LLM 判官出局(Who&When step 14.2% / κ=0.432)。
7. **2×2 防混淆(承重墙)**:{不分解} × {分解+轮转指派} × {分解+账本路由},轮转臂隔离"分解收益",账本臂加载"指派收益";× `--pool-k 1 vs 8`。
8. **最深连锁面裁决**:**不改路由单元**。池照论文任务级演化门控(seesaw/ever_solved/pass@2 语义全保),子任务路由只作**部署期执行层**叠加——这是把改动面压到最小且不破坏论文机制的关键设计。
9. **头号实证风险 = 分工前提**:RUN-LOG 的 W0 headroom +0.0pp(校准期)与"改善旱灾"预示 pilot30 上变体可能不分化 ⇒ oracle 路由天花板 ≈0 ⇒ 无论 novelty 多干净,机制无增益空间。**E0(oracle ceiling)必须先跑**,读数点后移至 a1big4(K=8)。
10. **五处对本仓文档的勘误**(详风险登记簿):静态分解 +80.5%(2605.15425)非原文数字且属 coding 域;AgentOrchestra 36.5→83.4 应为 36.54→89.04 且靠 tool-generator;2603.06859 是"信用分配 LOO"非"RL 训练期 decomp-vs-assignment";κ=0.43-0.57 应为 κ=0.432(0.57 未证);HarnessX "§7.7 分解工单"系早期摘要幻觉,真钩子在 §4.2/p.8。
11. **并发 scoop 警报**:Wentao Zhang / Bo An 组已有 AgentOrchestra(分解+路由,2506.12508)+ Autogenesis(自演化+生命周期+rollback,2604.15034)——两半在一个 stack 里,离整合成我们的想法只差一篇。设引用告警。
12. 最推荐组合的"何时选它":S1 显示变体自然分化(headroom 显著) ⇒ 直接部署期路由(推荐);S1 旱灾/不分化 ⇒ 转"引导分化"(planner brief 定向 bucket,backup)或先做纯分解收益臂。

---

## 用户裁定记录（Jul-27,coordinator 中途明令,必须执行）

原话大意:"task decomposition 不用做太复杂——选**易复现、公开效果数字硬**的成熟方法,与变体池结合就行。novelty 押在**组合**(decomp × 子任务分变体 × 自演化池)和**两大成败题**(信用分配、2×2 防混淆)上,不押分解器本身。"

落到本档的具体口径:
- **Q1** 排序标准 = 复现难度低 × 公开基准效果硬 × 实现面小(加法纪律下 ≤1 新 processor/wrapper 文件);只给 top-3,每个附"别人复现过吗/官方码有吗/GAIA 类 web 公开数字"。
- **Q4** 集成点 (c)(meta 演化层自演化分解策略)**降级为 future work 一段**;主力比较 (a) H0 processor 与 (b) 路由层,倾向实现面更小者。
- **最终推荐**给**一个**最简可行组合(方法×集成点),明确到"新增哪个文件、约多少行、旗标叫什么";备选一个;其余进弃选清单附弃因。
- **Q3 占位核验 / Q5 实验设计 / 信用分配分析 = 不变,全深度**(三块承重墙)。

---

## Q1 分解方法景观（按"复现易 × 公开数字硬 × 加法面小"排序,只给 top-3）

> 全表 14 个 ID 由 researcher 逐一 WebFetch 亲验,均解析到真论文;我另亲验 HuggingGPT/
> AFlow/ADAS/AOrchestra/AgentOrchestra/C3 六篇 abstract。**张力提示**:公开 GAIA 硬数字的方法
> (AgentOrchestra 89、Magentic-One 38)实现面都大;实现面小的方法公开数字多在别的 bench。
> 因 novelty 押组合不押分解器,**分解器取实现面最小 + 可复现即可,不必 SOTA**。

### Top-3(推荐候选,加法纪律下 ≤1 文件)

| 排名 | 方法 | arXiv(深度) | 机制一句话 | 复现/官方码/GAIA 类公开数字 | 加法面 | 短任务反噬? |
|---|---|---|---|---|---|---|
| **① 首选** | **HuggingGPT-schema 静态类型化计划** | 2303.17580(L3 by agent + L2 by me) | 一次 planner 调用把任务解析成类型化子任务表 `{task,id,dep,args}`,`dep` 构 DAG,`<resource>-N` 传递输出 | **官方码 JARVIS(微软),被广泛复现**;`task` 字段=我们的变体特化键;原文无 GAIA 数(在自建 eval);schema 成熟稳 | **最小**:1 planner 文件 + dispatcher,复用现有 router,不碰 base loop | 是(无条件分解)→ 用 ② 的门治 |
| **② 门控** | **ADaPT 按需分解(仅失败才分解)** | 2311.05772(L2 by agent) | 先整任务试执行,**失败才递归分解**,再交 LLM+任务能力 | 有官方码(AllenAI);数在 ALFWorld/WebShop/TextCraft(+28.3/27/33pp vs baseline);非 GAIA | 小:失败触发 wrapper(≈1 文件) | **反其道设计**——专治短任务过度分解;与我们现有 seesaw 门天然咬合 |
| **③ 地板对照** | **Plan-and-Solve / 最简前置计划** | 2305.04091(L2 by agent) | 单 prompt:先出完整计划再顺序执行 | 复现极易(纯 prompt);数为推理域(GSM8K 等),非 web/GAIA;无路由 schema | 极小(单 prompt) | 是(总是计划,无门) |

**Top-3 选用逻辑(推荐)**:以 **① HuggingGPT-schema-lite 作载体**(类型化子任务表=天然路由键,加法面最小,schema 成熟)+ **② ADaPT 触发门作旗控选项**(`--decomp-trigger on_failure`,防 pilot30 里 37% 的 L1 短任务过度分解反噬)。③ 仅作最简地板对照。**固定 4 类型分类学**沿用设计骨架:`search/retrieve · browse/extract · compute/reason · verify/synthesize`(`DECOMP-DIVISION-DESIGN.md:52`;三系统独立收敛于 4-5 类),**禁递归再分解**防级联。

### 弃选(实现面大或非加法,不作首发分解器)

| 方法 | arXiv | 弃因 |
|---|---|---|
| Magentic-One(双台账 orchestrator-worker) | 2411.04468(L3) | GAIA 硬数(**38.0±5.5**,去台账 **−31%**)但**替换整个 run loop**,违加法纪律;作"分工映射模板/北极星"引用,不实现 |
| AgentOrchestra(TEA,5 角色) | 2506.12508(L2 by me) | GAIA test **89.04%** 但靠 **Tool-Generator agent**(非分解本身),5 角色重实现;北极星引用 |
| LLMCompiler(DAG 并行) | 2312.04511(L2) | 多跳 QA 上 **3.7× 延迟/6.7× 成本降+~9% acc**(verbatim),但 3 组件中等实现;可留作二期并行执行层 |
| GPTSwarm / MaAS / DAAO / TDAG | 2402.16823 / 2502.04180 / 2509.11079 / 2402.10178 | 均需**训练**图/supernet/router 或合成 subagent;非加法;MaAS/DAAO 非 GAIA |
| AgentOccam(反分解基线) | 2410.13825(L2) | 反证:WebArena 上简化动作空间 **+29.4%** 打败复杂编排——作"复杂度非免费"的对照警示引用 |

### 短任务反噬证据(数字,直接进风险登记簿)

- **论文本体(最贴)**:ALFWorld 上"task-decomposition prompts lengthen execution",每任务 token **+60%**(`HarnessX_Tech_Report.pdf` p.22 §7.5);GAIA 上靶向工具选择反而 **−25% token**——分解开销**依赖任务形态**,GAIA 短任务(L1)是反噬高危区。
- **2605.15425(L2)**:runtime-structured 分解比 monolithic 省 51.7% retry、比 **static 分解**省 73.2% ⇒ 反推 static 分解 ≈ **+80% retry 成本 vs monolithic**(但**属 coding-agent 域,非 GAIA;且 "+80.5%" 非原文字面数**,见勘误)。
- **ADaPT(2311.05772)** 全方法存在的理由=固定前置分解在简单子任务上过度规划;只在失败时分解。
- **AgentProp-Bench(2604.16706,L2,单作者)**:verify-then-proceed 门在 GPT-4o-mini 上砍幻觉 **−23pp**,在 Gemini-2.0-Flash 上**无显著效果**⇒ 分解/校验脚手架**模型依赖**,直接关系我们的 DeepSeek worker(flash 上先测后信)。
- **pilot30 床实测**:L1=11/L2=16/L3=3(37%/53%/10%),问句中位仅 232 字符,样例题 2 跳("BBC Earth 视频里是什么鸟")⇒ 近四成题分解深度浅,反噬风险真实(`recipe/gaia_evolver/data/pilot30.json`)。

---

## Q2 子任务→异构执行者分工先例 + 信用分配（承重墙,全深度）

### (1) 分工先例:谁做过"子任务→同底座不同配置变体"

| 系统 | arXiv(深度) | 路由什么 | 执行者差异 | 同底座? |
|---|---|---|---|---|
| RouterBench | 2403.12031(L1) | 每 query 选 11 个 LLM 之一 | **不同模型** | 否 |
| HuggingGPT | 2303.17580(L3) | 每子任务→HF 专家模型/工具 | **不同模型** | 否 |
| MoA / Self-MoA | 2406.04692 / 2502.00674(L1) | 分层聚合 / 单最优模型重采样 | 层间模型 / 同模型重采样 | 部分/聚合非路由 |
| **SPP(Solo Performance Prompting)** | 2307.05300(L1) | 单 LLM 模拟**多人格**自协作 | **同模型不同人格(≈config)** | **是**(最近但自协作非池路由) |
| AutoGen / AgentVerse | 2308.08155 / 2308.10848(L1) | 角色 agent 承接子任务/动态招募 | 角色/prompt/tools | 多为是;**角色手工**,非学习路由 |
| CrewAI / Claude subagents | 无 arXiv | 编排框架 | 角色/prompt/tools | 可配 |

**Q2 核心裁决(与 Q3 互证)**:**无任何论文做"每子任务在一个维护的、演化的同底座变体池(prompt/tools/processors/config)里选变体"**。最诚实的一句话贡献框定 = **"HarnessX 的任务级粒度 + HuggingGPT 的每子任务粒度,但跨的是同底座配置而非不同模型"**。近邻各差一轴:SPP 是自协作非池路由;AutoGen/AgentVerse 角色手工非演化变体;HuggingGPT 是跨模型。

### (2) 信用分配:只评终答案(GAIA exact-match)时,子任务无真值

**亲验的具体数(勘误本仓)**:
- **Who&When**(2505.00212,ICML'25):三法最优 = agent 级 **53.5%**、step 级 **14.2%**;后续 AgenTracer(2509.03312)训练 8B 归因器 step 级仍仅 **~20%**。⇒ **step 级归因是未解难题**。
- **AgentProp-Bench**(2604.16706,**单作者**):substring judge κ=0.049,**三 LLM 集成 judge κ=0.432**(中等,保守偏置)。**"0.57 上界未证**"(不在 abstract)⇒ 本仓 κ=0.43-0.57 应改口径为 **κ=0.432**。
- 对照(非 step 信用):MAST(2503.13657)失败**模式分类** κ=0.88 人 / 0.77 LLM;人-人 step 一致远高于 LLM judge。

**可行信用方案排名(仅终答案可评时)**:

| 排名 | 方法 | 需子任务真值? | 额外成本 | 可靠性 | 适配 |
|---|---|---|---|---|---|
| **①** | **outcome-only aggregated(bandit 式)**:终 pass/fail 作 reward,credit 记到实际发生的 (subtask_type→variant) 决策,跨题平均 | **否** | **零额外 rollout** | 标准 RL/bandit 信号;单实例不判别但期望一致 | **首选默认** |
| **②** | **counterfactual / LOO 重放**:换/去某子任务的变体,重跑量 Δ 终分 | 否 | 额外 rollout,但**fixed-context 只从分叉点重跑**(历史是确定性文本) | **C3**(2603.06859):LOO ≈ 组合法但成本零头;**Agents that Matter**(2605.27621):LOO 隔离瓶颈 **+17% 性能/−35% 成本**;两者警告 naive 删 agent 失真→用 fixed-context | **有预算即加固** |
| ③ | step 级 LLM-judge(Agent-as-a-Judge 2410.10934 / Who&When) | 否 | judge LLM 调用(比 rollout 便宜) | **信用用途差**:14.2% / κ0.432 | **仅诊断,不作信用信号** |
| ④ | Shapley 近似(Shapley-Coop 2506.07388) | 否 | **高**(多联盟 rollout);近似仍贵 | 原则性但**基线/联盟分布敏感**(2605.27621 已形式化) | MSc 过重+脆,弃 |

**信用分配推荐(MSc,零/低额外 rollout)**:
- **主信号 = ① outcome-only bandit**:零额外 rollout,精确对齐 GAIA exact-match,标准可辩护。把子任务路由框成 contextual bandit,reward=终任务分,credit 归实际路由决策;好的 (subtype→variant) 组合随跨床与终成功相关而浮现。
- **有小预算则用 ② 有界 fixed-context LOO 加固**(只在 *decisive* subtask 或子集,非全轨迹全子任务)。**在我们的场景里异常干净**:因变体**共享底座模型**,在某子任务槽换一个变体 = 对确定性文本历史的**最小干预**,counterfactual well-posed(正是 C3 2603.06859 的论证)——**这是把每子任务信用做"判别式"的最可辩护路径**。
- **明确不作信用信号**:(i) step 级 judge(14.2%/κ0.432 使其作训练信用不可辩护,只留定性诊断);(ii) Shapley(成本+基线敏感,风险回报差)。

> 与设计骨架一致:`DECOMP-DIVISION-DESIGN.md:38-40` 已裁"子任务级 LLM 判官出局、真值=确定性任务级门、leave-one-slot-out 只作校准";本节把 ② 的 LOO 从"仅校准"升为"有预算即判别式加固",并补 C3/Agents-that-Matter 证据与"同底座故 counterfactual 干净"的论证。

---

## Q3 占位核验（novelty 保卫,承重墙,全深度）

### 裁决:**弱占(WEAKLY OCCUPIED)——双侧钳形,非空地**

无单篇在**子任务粒度**同持三件套 ⇒ 精确 cell 技术上未占;但称"全空"不诚实——被两侧夹得极紧,机制读起来像两个已发表系统的**一步组合**:
- **(b)+(c) 演化变体池 + 路由**:被 **HarnessX 2606.14249** 与 **Adaptive Auto-Harness 2606.01770** 在**整任务**粒度占住。我们对它们唯一的新轴 = (a) 子任务分解。
- **(a)+(b) 分解 + 每子任务同底座配置路由**:被 **AOrchestra 2602.03786** 在 **GAIA 上、有码**占住。它**只缺 (c)**——持久、门控、演化的池。

⇒ **贡献 = "给 HarnessX 加 (a)" ≡ "给 AOrchestra 加 (c)"**。可辩护(新颖组合 + 真实实证问题:门控演化池是否胜过 AOrchestra 的即弃 spawn),但审稿人会看见两翼。

### 精确 cell 分解表(本轮亲验)

| 论文 | (a)分解→子任务 | (b)每子任务→同底座异构配置 | (c)自演化门控池(fork/retire/no-regression) | 粒度 | 深度 |
|---|---|---|---|---|---|
| **AOrchestra** 2602.03786 | ✅ 4-tuple(非语义类) | ✅ (Instruction,Context,Tools,Model)同模或换模 | ❌ **无**——sub-agent 即用即弃、无池/registry、无门;只 orchestrator 做 SFT | **子任务** | L2(我+agent) |
| **HarnessX** 2606.14249 | ❌(自认 §4.2 欠探索"decompose one agent into several") | ✅(同模,9 维类型化 harness) | ✅ seesaw fork-on-regression、退最弱、≤K | **整任务**(cluster ensemble) | L3+(本地全 PDF) |
| **Adaptive Auto-Harness** 2606.01770 | ❌(整任务路由到分支) | ✅(git-branch harness 树:prompts/skills/tools/memories,同模) | ~部分(演化分支树;未引用显式 no-regression 门) | **整任务** | L2(agent) |
| Uno-Orchestra 2605.05007 | ✅ | ~每子任务(model,primitive)对,但**不同模型** | ❌ 冻结商用 LLM 池、RL 路由 | 子任务 | L1/L2 |
| Compositional Skill Routing 2606.18051 | ✅(decompose→retrieve→compose) | ~每子任务 **skill** 检索(非 harness config) | ❌ | 子任务 | L1/L2 |
| AgentFactory 2603.18000 | ✅(Meta-Agent 分解 create_subagent) | ❌ sub-agent 是独立 Python 代码件,非 harness-config 变体 | ❌ 累积/精炼、无门/退役;优化 token 效率(30 自建题,非 GAIA) | 子任务 | L2 |

**枢纽事实(锁定)**:AOrchestra 的 sub-agent 无状态、按需、即弃("Each SubAgent runs in a FRESH container — if you delegate_task again, the previous work will be lost"——**此句为 agent 从 HTML §method/repo 取,L2;我亲验 abstract 只到"spawn on demand",未证即弃**);无池、无回退门、只 orchestrator SFT。**这正是我们 (c) 填的缝,也是最可能被拿来打我们的论文**(此前 harness 线分析从未列它)。

### 近邻线地图(优化目标 × 粒度 × 是否触我们的 cell)

- **自演化 harness 线——无一分解,全整任务**:HarnessX 2606.14249(9 维 harness + seesaw,整任务 ensemble)/ Adaptive Auto-Harness 2606.01770(演化分支树,整任务路由)/ Self-Harness 2606.09498、HarnessForge 2606.01779(需训练)、AHE 2604.25850、GSQD 2607.13683(gated 语义 QD,病理键控,**QD 仍键控整任务 cluster,从不子任务**)/ "No Universally Superior Harness" 2607.18235(**非演化器**;30 harness×12 model-problem,3.1M rollout,预算匹配;论点="harness 是每问题超参,跑多个+自适应分配"——**强外部动机**但也是 framing 风险)/ Offline-RL harness 控制 2607.05458(整任务,单 harness)。
- **agentic workflow 演化——优化图/模块/prompt,非每子任务 harness 池**:ADAS 2408.08435(meta-agent 搜整系统代码,离线 archive,**我亲验 L2**)/ AFlow 2410.10762(MCTS 搜 workflow 图,离线,**我亲验 L2**)/ MaAS 2502.04180 / AgentSquare 2410.06153 / DAAO(operator 分配给**不同模型**)/ GEPA 2507.19457(反思式 prompt 演化)。**优化目标无一是"每子任务类型特化的自演化 harness-config 变体池"**。
- **多智能体/QD 特化——专家涌现,但经 skill/DAG/prompt,非 harness-config**:EvoAgent 2604.20133(分解+委派,专家=演化 skill 库)/ InfiAgent 2509.22502(自演化 agent DAG + 路由,演化=DAG 重构)/ GPTSwarm、EvoMAC(优化图边/网络+prompt,专家=图节点)。

### Framing-collision 风险(overlap 卖相,不 scoop 机制)

1. **AOrchestra 2602.03786(最高)**:卖相几乎相同(分解 GAIA、每子任务 (instruction,context,tools,model) 执行者、同模或换模、有码)。审稿人会说"AOrchestra + HarnessX 的池 = 你的论文"。**必须**:(i) 把 (c) 持久 seesaw 门控演化池 vs 即弃 discard 顶为承重 novelty;(ii) **实证证明演化池胜过每子任务 fresh-spawn**(=核心实验 + E0 oracle-ceiling 测)。其无类 4-tuple 分解也削弱"类型化子任务"卖点——**勿单靠 (a)**。
2. **AgentOrchestra 2506.12508 + Autogenesis 2604.15034——同组(Wentao Zhang, Bo An),并发 scoop 威胁**:AgentOrchestra=分层分解+路由到专家(GAIA)+版本化资源;Autogenesis=自演化版本化资源协议(**生命周期、门控、rollback**)。**我们想法的两半已在一个组的 stack 里**,离整合成一篇只差一步。设引用告警、持续监控。
3. **Adaptive Auto-Harness 2606.01770**:与 HarnessX 同卖相(演化 harness 树+路由+regime 分支);审稿人略读会说"演化分支路由已做"而漏其**整任务**性。必须**早、明**声明粒度差(任务级分支路由 vs 子任务级任务内分工)。
4. 次级:**2607.18235**("harness=每问题超参,跑多个+自适应分配"既动机化也可能预占动机);**AgentFactory 2603.18000**(卖相 overlap,以 harness-config-vs-code、gated-pool-vs-accumulation 区分)。

### 诚信 caveat(锁进论文前必做)

- **承重负判为 L2**:"AOrchestra 无演化池"与"Adaptive-Auto-Harness 整任务"来自 HTML 摘录;certifying-absence 是最易幻觉一类。二者被各自**正向机制**佐证(即用即弃 fresh-container;router 路由"the incoming task"),但**入论文 related-work 前须亲开 AOrchestra 2602.03786 §method + repo(github.com/FoundationAgents/AOrchestra)与 Adaptive-Auto-Harness 2606.01770 §method 复核**。
- **勘误(锁定)**:HarnessX "§7.7 动态任务分解 future-work 工单"系早期摘要器**幻觉**;真 §7.7 limitations = 无 held-out eval、离散动作空间、闭源 meta、co-evolution、bench 覆盖。**勿引 HarnessX 为"邀请"分解**。合法钩子 = **§4.2 / p.8**(AEGIS 欠探索结构性编辑,如"decomposing one agent into several",亲验 `HarnessX_Tech_Report.pdf` p.8)。
- **移动裁决的开放题**:① AOrchestra 后续/repo 是否加持久+门控?若加,(c) 差异化坍缩——锁前复查。② Wentao Zhang/Bo An 组是否出 AgentOrchestra×Autogenesis 整合?最可能直接 scoop。③ **实证 crux(非文献)**:GAIA 上 seesaw 门控**演化**的每子任务类型 harness 池,是否在 token-matched 预算下真胜过 AOrchestra 式**即弃** spawn?若演化变体的 oracle 路由天花板 ≈0(参本仓 co-failure-ceiling 隐忧),机制无 headroom,novelty 再干净也白搭——**committing 前先跑 E0**。

---

## Q4 集成点分析（读代码后的方案;(a)(b) 主力,(c) 降 future work）

> 代码事实(亲验):recipe 全 6000 行**零** `decompos/subtask` 出现——干净加法面。
> 路由单元现状=**整任务**:`VariantPoolRecipe.run()` 以 `set(all_ids)` 全任务 id 调 `engine.run_round`
> (`run_variant_pool.py:3643`);`Router.route` 把**一个任务**经 `cluster_of`→`estimate_cluster` 路由到一个变体
> (`router.py:203`);gate 的 improved/regressed 键在**任务级 pass@2 exact-match + is_ever_solved**
> (`gate.py:194-210`)。**这套任务级语义正是子任务路由会撼动的东西**。

### 最深连锁面裁决(全设计最关键的一句):**不改路由单元**

若把路由单元从"任务→变体"改成"子任务→变体"(集成点 b 的字面读法),连锁破坏:
- `T_k` 每变体任务簇(`engine.py:298`)失义:变体不再"拥有一簇整任务",而是横跨所有任务的一簇**子任务类型** ⇒ 论文"candidate targeting variant k is tested only against tasks routed to k"(§4.5 p.11)的**收窄评测坍塌**,候选评测范围膨胀到"pipeline 含 k 的子任务的所有任务"(可能=全床)。
- seesaw `_classify`(`gate.py:194-210`)无处落脚:子任务无 exact-match ⇒ 无 `after_passes` ⇒ improved/regressed 与 `GateResult.improved`(喂 `pool.fork` 决定子变体继承哪些任务)全失效(=P-A 信用污染的根)。
- `is_ever_solved` 棘轮、pass@2 均任务级 exact-match;子任务级无对应真值。

**⇒ 裁决(把改动面压到最小且不破论文机制)**:**池照论文任务级演化门控**(seesaw/ever_solved/pass@2/fork/retire 语义**零改**,engine.py/gate.py/router.py/harnessx 全不动),**子任务路由只作部署期(deployment/execution)执行层**叠在已演化好的池之上。这把"演化"与"子任务路由"解耦:演化保持干净任务级语义,子任务路由查一张 **observational (variant, subtask_type) 成功表**。信用因此不进 gate(gate 仍任务级),只进路由表(outcome-only bandit)。

### 三集成点对比

| | (a) H0 级 processor | **(b) 部署期路由层(推荐,recipe 落地)** | (c) meta 演化层 |
|---|---|---|---|
| 机制 | decomposer 作 harness processor,任务进来在**单变体**内分解+顺序执行子目标,池不知情(仍整任务路由) | 新 recipe rollout 编排器:分解→每子任务经 subtype 路由到最擅长该 type 的变体→各在其 config 上跑→聚合;池的演化/门控**不变** | 让 Evolver 把"分解策略/sub-agent 拆分"作为 harness 编辑演化出来 |
| 改动面 | 新 processor 文件(harnessx/processors/,新增不改上游),约 ~150 行;变体 config 挂载 | **新文件 `recipe/gaia_evolver/subtask_pipeline.py` ~350-550 行**,全在我们 recipe territory;复用 `router.py` 的 `task_to_cluster` seam(子任务 id→subtype 作 cluster,`router.py:91-96`,**用现有 seam 不改 router**)+ `_rollout_once`(`run_variant_pool.py:4398`);gate/engine/harnessx 零改 | 改 Evolver prompt/候选空间(`_produce_paper_candidate` `run_variant_pool.py:3999`→`meta_agent.evolve` `harnessx/meta_harness/agent.py:539`);候选=分解器 config |
| 与论文机制兼容 | 高(=论文 p.35 ALFWorld"分解成有序子目标"prompt 原则的 GAIA 移植);但**不产跨变体分工**——只是"分解收益"臂 | **高**(池机制字节不变;子任务路由=论文 §7.5 部署路由 p.22 的子任务化升级) | 中(=论文 §4.2/p.8 明认欠探索的"decompose one agent into several",但论文自己没做;§6.4 警示分解=效率非精度) |
| 信用分配 | 无需(单变体,任务级 gate 照旧) | outcome-only bandit(表)+ 可选 LOO;gate 仍任务级无污染 | P-A 信用问题**复合**(演化 × 分解 × 跨变体) |
| 致命风险 | **不实现"分工"**——只能作 2×2 的"分解收益"臂,非完整贡献 | 依赖变体**已分化**(S1 前提);oracle 天花板 ≈0 则无增益 | 冷启动 + 方差 + 信用污染三重;MSc 时限内难 defensible |

**(c) 降 future work(用户裁定)**:一句带过——"让 AEGIS 自演化出分解/sub-agent 拆分策略,是与论文机制同构的终局形态(呼应 §4.2/p.8 欠探索面),但冷启动、方差、信用污染三重成本使其超出 MSc 时限;留二期。"

**(a) vs (b) 取舍**:表面看 (a) 更小,但 **(a) 不产'分工',只是分解收益臂**——真要跨变体分工必须走 (b)。而 (b) 落 **recipe 层**(我们的地盘)反而比往 harnessx/processors 塞新 processor **更安全**(零上游、零 engine/gate 触碰)。故 **(b) 落 recipe 部署期路由 = 实现面小 + 语义不破 + 直达贡献**;(a) 作为 (b) 的一个旗控模式(division off)自然并入,同时充当 2×2 的分解收益臂。

---

## Q5 实验设计（条件于两个未决判决,分支树 + 协议）

> 未决判决 A = a1big4 点火:flash 保住(ship>0)/ 升 pro(ship=0,旱灾实锤,需充值)。
> 未决判决 B = S1 正式实验:变体**分化**(headroom 显著、按簇对比度显著)/ **不分化**(旱灾)。
> 依据:`DECOMP-DIVISION-DESIGN.md:80-86`(判据链)+ `RUN-LOG.md`(a1big4 在跑,R0 pilot30 50-53%)+
> `OPTIMIZATION-PLAN.md:32`(规模三档)+ SPEC §9(预注册假设)。

### 前置门 E0(必须最先跑,任一不过止损)——把 Q3 开放题③变成实验

- **E0-headroom(oracle ceiling)**:在**已演化 K=8 池**上,离线算"每子任务类型选事后最优变体"的联合上界 − 最佳单变体/任务级 ensemble。**≈0 ⇒ 分工前提死,分解方向重议**(转引导分化或纯分解收益叙事);**显著 ⇒ 进主实验**。复用 `oracle_ceiling.py`(recipe 已有)+ 子任务分解一次。成本:一次分解调用 × 30 题 + 池现有轨迹重算,≈¥5-15。
- **E0-differentiation**:a1big4(K=8)读出**分工前提判据②**(变体间按簇/按任务成功率对比度)+ fork/特化事件频率(`DECOMP-DIVISION-DESIGN.md:82-84`)。

### 四世界分支树

| 世界 | A(flash/pro) | B(分化/不分化) | 主实验怎么跑 |
|---|---|---|---|
| **W1(理想)** | flash 保住 | 分化 | 全跑推荐组合;2×2 × K∈{1,8};M 档 n=50×15 轮×2 seed |
| **W2** | flash 保住 | 不分化(旱灾) | 转"引导分化":planner brief 定向不同 subtype bucket 制造分化,再测分工;或先只发"分解收益"臂(B1) |
| **W3** | 升 pro(需充值) | 分化 | 同 W1 但 worker=pro;失败余量收窄,效应窗变小,加样或降 n |
| **W4(最差)** | 升 pro | 不分化 | 分工前提存疑;退守"分解收益"单臂 + 把 E0≈0 作为诚实负结果发表(接 SEA/negative-results venue) |

### 臂设计(能分离"分解收益 vs 分工收益"=P-B 防混淆)

同一份**已演化池** + `subtask_pipeline.py` 的旗控,部署期比较:

| 臂 | 旗标 | 隔离什么 |
|---|---|---|
| A0 | `--pool-k 1 --decomp off` | Global 基线(=论文对照,`SPEC §8.1`) |
| A1 | `--pool-k 8 --decomp off` | 论文 Ensemble(任务级路由) |
| B1 | `--pool-k 8 --decomp on --subtask-routing roundrobin` | **分解收益**(分解在,指派随机=无特化红利) |
| B2 | `--pool-k 8 --decomp on --subtask-routing ledger` | **+ 分工收益**(账本路由=加载特化) |
| (可选)B0 | `--pool-k 8 --decomp on --decomp-trigger on_failure` | ADaPT 门:防短任务反噬(与 B1/B2 交叉) |

分解收益 = B1 − A1;**分工收益 = B2 − B1**(核心贡献量);ensemble 红利 = A1 − A0。**缺 B1(轮转臂),B2 赢会被判"只是 ensemble/只是分解",无法归因到分工**。

### 床 / 轮数 / n / 统计 / 预算

- **床**:pilot30(带 0.50-0.53 R0,失败余量足;`RUN-LOG` a1big1/a1big3 两点互验)。**held-out 20 题**不参与演化,末池两路由(任务级 vs 子任务级)各评,复现论文 §7.5 未测的部署差。
- **轮数**:池演化 15 轮(`OPTIMIZATION-PLAN.md:11`);部署期臂各评 held-out 一次 × ≥2 seed。
- **n**:M 档 n=50(扩床)或 pilot30 起步;S 档 pilot30×双臂×1 seed ≈¥750-950(CI±18%,方向勉强);**M 档 n=50×15×双臂×2 seed ≈¥2.5-3.2k(用户尺度)**。
- **配对统计**:**McNemar 配对检验**(B2 vs B1 同题配对,单尾,Δ≥?pp,α=0.05)——预注册主假设,镜像 `SPEC §9.0 H1`(把 H1 从 Ensemble>Global 扩展为 B2>B1);次:B2 分解开销 token(防"赢在算力")作独立效率轴单独报(`SPEC §9.6`,token-matched)。分析单元=task-level 配对。
- **预算实测尺**:flash 单 rollout ¥0.20-0.25(任务书);pilot30×几轮×K=8 实测 ¥6-16/run(`RUN-LOG` a1pilot 系列)。分解加一次 planner 调用/任务 + 聚合一次;B1/B2 子任务 pipeline 使每任务 rollout 数 ↑(≈子任务数×),**成本 2-4× 于 A1**——预算须计入,首发建议 pilot30 而非 n=50。

### 判读标准(什么=改进成立/不成立/需加样)

- **成立**:B2 − B1 ≥ 10pp 且 McNemar p<0.05(配对);或 held-out 上子任务路由 > 任务级路由且 CI 不含 0。
- **不成立**:B2 ≈ B1(分工无增益)⇒ 特化不存在或路由无效,回 E0 复核;或 B1 < A1(分解净损)⇒ 短任务反噬主导,开 ADaPT 门(B0)复验。
- **需加样**:|B2 − B1| ∈ (0, 10pp) 且 CI 跨 0(n=30 下 CI±18% 使 <18pp 效应不可判,`PAPER-GAP-AUDIT.md:13`)⇒ 升 M 档 n=50×2 seed 或 L 档。
- **诚实负结果出口**:E0≈0 或 W4 ⇒ 写"同底座配置变体在此床不分化 ⇒ 子任务分工无 headroom",作 negative result(SEA/Meta-Agents 类 venue 收)。

---

## 方法选择菜单（method × 集成点组合,带"何时选它"触发条件）

> 用户要"**一个**最简可行组合 + 一个备选 + 弃选清单"。

### ★ 推荐(唯一最简可行组合)= M1

- **方法**:HuggingGPT-schema-lite 静态前置规划器 → 固定 4 类型化子任务表(一次 meta/planner 调用,`{id, type∈{search,browse,compute,verify}, desc, dep}`,串行、禁递归)+ **可选 ADaPT 触发门**(`--decomp-trigger on_failure`)。
- **集成点**:(b) 部署期路由层,落 recipe。
- **新增文件**:`recipe/gaia_evolver/subtask_pipeline.py`(~350-550 行):`SubtaskPlanner`(1 调用)/`SubtaskTypeRouter`(注入 subtask_id→subtype 给现有 `variant_pool.router`,不改 router)/`SubtaskPipelineEvaluator`(分解→路由→`_rollout_once` 逐子任务→固定 synthesis 聚合)/observational `(variant, subtype)` 表。(可选)`reporting.py` 旁挂一个 flag-gated 汇总(或另起小文件),不改主 reporting。
- **旗标**:`--decomp {off,on}`、`--subtask-routing {roundrobin,ledger}`、`--decomp-trigger {always,on_failure}`、`--decomp-taxonomy search,browse,compute,verify`;**全默认关**(`--decomp off` = 现行为字节等同)。
- **信用**:outcome-only bandit(主)+ 有界 fixed-context LOO(可选加固)。
- **gate/engine/harnessx 改动**:**零**。
- **何时选它**:S1 显示变体**自然分化**(E0-headroom 显著)——直接部署期路由。**这是默认推荐**,因它实现面最小、语义不破、直达贡献、2×2 一文件内旗控完成。

### ○ 备选 = M2(引导分化)

- **触发条件**:S1 = 旱灾/不分化(W2/W4),自然分化不存在。
- **做法**:在 M1 基础上,让 planner brief **定向**把不同变体推向不同 subtype bucket(引导分化制造特化),再测分工。改动面 +小(brief 模板 + 一个 `--differentiation guided` 旗);但**信用回到 P-A**(变体按 subtype 表现被评需子任务信号)⇒ 须上 LOO 加固。novelty 更高(制造特化而非假设特化),风险也更高。

### 弃选清单(附弃因一句)

- **(a) 纯 H0 processor 单变体分解**:不产分工,只能当 2×2 的分解收益臂(已并入 M1 的 `--subtask-routing` 关档)。
- **(c) meta 演化分解策略**:冷启动+方差+信用污染三重,超 MSc 时限 → future work(用户裁定)。
- **Magentic-One / AgentOrchestra orchestrator-worker**:替换整个 run loop,违加法纪律 → 仅作分工映射北极星引用。
- **GPTSwarm / MaAS / DAAO / TDAG**:需训练图/supernet/router → 非加法。
- **子任务级 LLM judge 作信用**:Who&When step 14.2% / κ0.432 → 只留定性诊断。
- **Shapley 信用**:多联盟 rollout 成本 + 基线敏感 → 风险回报差。
- **递归再分解**:级联成本 + 短任务反噬(2605.15425 静态分解 +~80% retry;p.22 ALFWorld +60% token)→ 固定深度 1,禁递归。

---

## 风险登记簿

| # | 风险 | 证据/锚点 | 缓解 |
|---|---|---|---|
| R1 | **分工前提为空**(变体不分化,oracle 天花板 ≈0) | W0 headroom **+0.0pp**(校准期,`RUN-LOG` calib_p12_b);"改善旱灾"(`RUN-LOG` a1pilot3);Q3 开放题③ | **E0 先跑**;≈0 则转 M2/负结果出口 |
| R2 | **AOrchestra 2602.03786 scoop 卖相**(分解+每子任务同底座路由,有码,GAIA) | Q3 枢纽事实;我亲验有码+16.28% relative | 顶 (c) 演化池为承重 novelty;实证证明演化池 > 即弃 spawn;**亲开其 §method+repo 复核"即弃"** |
| R3 | **同组并发整合**(Wentao Zhang/Bo An:AgentOrchestra+Autogenesis) | Q3 framing 风险 2;2506.12508 + 2604.15034 | 引用告警;差异化=子任务粒度 harness-config 池 |
| R4 | **短任务分解反噬**(GAIA L1 占 37%) | p.22 §7.5 ALFWorld **+60% token**;2605.15425;pilot30 中位 232 字 | ADaPT 触发门(`--decomp-trigger on_failure`)旗控先测 |
| R5 | **分解=效率非精度**(论文自证) | p.22 §6.4:单 agent evolver **86.4 vs 四阶段 87.4**,分解只给 ~12% 省 token+可审计,**非精度** | 主张改口径:我们测的是"子任务**分工/特化**"增益,非"分解"增益;2×2 的 B2−B1 才是靶 |
| R6 | **信用污染 P-A**(子任务无真值,终答案混多变体贡献) | `gate.py:194-210` 任务级 exact-match;Who&When 14.2% | gate 保持任务级(不进子任务信用);表用 outcome-only bandit;判别式用 LOO(同底座故干净) |
| R7 | **模型依赖**(flash 上分解/校验门可能无效) | 2604.16706 Gemini-null;flash BrowseComp 73.2 vs pro 83.4(`MODEL-SELECTION`) | flash 上先测后信;a1big4 决 flash/pro |
| R8 | **成本膨胀**(子任务 pipeline 使每任务 rollout ×子任务数) | flash ¥0.20-0.25/rollout;B1/B2 ≈2-4× A1 | 首发 pilot30 非 n=50;E0 止损门在前 |
| R9 | **inter-subtask 依赖破坏变体隔离** | 论文 p.21 自认"inter-task dependencies prevent clean variant separation" | 串行 DAG + 固定聚合步(变体无关)隔离变量;`DECOMP-DIVISION-DESIGN.md:64-67` |
| R10 | **负判为 L2 幻觉风险**(本仓 4 次 WebFetch 幻觉史) | Q3 诚信 caveat | 两承重负判入论文前亲开 PDF §method |
| R11 | **统计功效不足**(n=30 CI±18%,<18pp 效应不可判) | `PAPER-GAP-AUDIT.md:13`;`SPEC §9.0` | 预注册 McNemar 配对;需加样则升 M/L 档 |

---

## 参考文献（arXiv ID + 查证深度）

> 深度:L0 二手 / L1 landing 页(标题+摘要) / L2 abstract+skim 或 HTML §method / L3 PDF 关键节。
> "(me)"=我本 session 亲 WebFetch;"(A1/A2/A3)"=对应 researcher 子代理亲验;"(local)"=本地 PDF。

**基座 / harness 线**
- HarnessX(base)= **2606.14249** — L3+ (local PDF,多节亲读:§4.3 p.7-9、§4.5 p.11、§6.4/§7.5 p.22、p.8、Table 9 p.36)
- Adaptive Auto-Harness = **2606.01770** — L2 (A3);演化分支树,整任务路由 **[负判待 PDF 复核]**
- Self-Harness 2606.09498 / HarnessForge 2606.01779 / AHE 2604.25850 / GSQD 2607.13683 / "No Universally Superior Harness" **2607.18235** / Offline-RL harness 2607.05458 — L1-L3 (A3,多为本仓 prior)

**占位核验最近邻**
- **AOrchestra = 2602.03786** — L2 (me + A3);标题/作者(FoundationAgents)/有码/16.28% relative 亲验;**"即弃无池"负判 L2 待 §method 复核**
- AgentOrchestra(TEA)= **2506.12508** — L2 (me);6 版本,GAIA test 89.04% 亲验
- Autogenesis = 2604.15034 — L1 (A3)
- AgentFactory 2603.18000 / Uno-Orchestra 2605.05007 / Compositional Skill Routing 2606.18051 — L1-L2 (A3)

**分解方法(Q1)**
- HuggingGPT = **2303.17580** — L3 (A1) + L2 (me)
- ADaPT = **2311.05772** — L2 (A1)
- Plan-and-Solve 2305.04091 / LLMCompiler **2312.04511** / Magentic-One **2411.04468** / AgentOccam 2410.13825 / Plan-and-Act 2503.09572 / TDAG 2402.10178 — L2 (A1)
- GPTSwarm **2402.16823**(GAIA 18.45 vs 9.70)/ MaAS 2502.04180 / DAAO 2509.11079 — L2 (A1;DAAO 数字 L2-secondary)
- ADAS 2408.08435 / AFlow 2410.10762 — L2 (me;均离线搜索,非本 cell)
- 静态分解 retry = **2605.15425** — L2 (A1) **[+80.5% 非原文,coding 域,见勘误]**

**信用分配(Q2)**
- Who&When = **2505.00212**(step 14.2% / agent 53.5%)— L1 (A2);AgenTracer 2509.03312 — L1
- AgentProp-Bench = **2604.16706**(κ=0.432;−23pp/Gemini-null)— L2 (A1/A2)**[0.57 未证]**
- C3 "Exact Is Easier" = **2603.06859** — L2 (me) **[信用分配 LOO,非 RL 训练期,见勘误]**
- Agents that Matter 2605.27621(+17%/−35%)/ Shapley-Coop 2506.07388 / Agent-as-a-Judge 2410.10934 / MAST 2503.13657 — L1 (A2)

**分工先例(Q2)**
- RouterBench 2403.12031 / MoA 2406.04692 / Self-MoA 2502.00674 / SPP 2307.05300 / AutoGen 2308.08155 / AgentVerse 2308.10848 — L1 (A2)

**workflow / QD 演化(Q3 near-neighbor)**
- AgentSquare 2410.06153 / GEPA 2507.19457 / EvoAgent 2604.20133 / InfiAgent 2509.22502 / Survey 2606.20683 — L1 (A3)

**未证 / L0(不采信,仅记录)**:SHARP(无 ID)、CCPO 2603.21563、COSAC 2604.17693、Market Regime Council 2605.24490、Causal Agent Replay 2606.08275、AgentProcessBench 2603.14465(κ 数 L0)。

---

## 附:对本仓既有文档的勘误清单（researcher 亲验驱动,建议主循环采纳）

1. **`DECOMP-DIVISION-DESIGN.md:42`** "静态分解重试成本 +80.5%(2605.15425)":ID 真,但 **"+80.5%" 非原文字面**(可从 51.7%/73.2% 反推 ~+80%),且属 **coding-agent 域,非 GAIA**。引用须加双 caveat。
2. **`DECOMP-DIVISION-DESIGN.md:28`** "AgentOrchestra 5 角色累积消融 36.5→83.4(GAIA)":**版本漂移**。真实 = 36.54→**89.04**(v4 累积消融),标题已改 "TEA Protocol"(6 版),且 89% **靠 Tool-Generator agent 非分解本身**。
3. **`DECOMP-DIVISION-DESIGN.md:46`** "最近者 2603.06859 是 RL 训练期且关键数字开卷未见":**误标**。2603.06859 = "Exact Is Easier: Credit Assignment for Cooperative LLM Agents"(**信用分配 LOO/counterfactual**,非 RL 训练期 decomp-vs-assignment)。**Q6 结论(2×2 干净拆分无人做)可能仍成立,但锚点须换**;并且 C3 恰是我们 LOO 信用方案的最佳先例(应正向引用)。
4. **`DECOMP-DIVISION-DESIGN.md:39`** "AgentProp 判官步级 κ=0.43-0.57":应改 **κ=0.432**(三 LLM 集成,2604.16706 单作者);**0.57 上界未证,删或脚注**。
5. **harness 线记忆/地图** "HarnessX §7.7 动态分解 future-work 工单":系早期摘要器**幻觉**(Q3 复核 + 我 p.8 亲验)。真 §7.7=无 held-out/离散动作/闭源 meta/co-evo/bench 覆盖;合法钩子=**§4.2 / p.8** "decomposing one agent into several"(欠探索结构编辑)。**勿引 HarnessX 为"邀请"分解**;引 §6.4/p.22 时须并报"分解=效率非精度"的自证警示。
