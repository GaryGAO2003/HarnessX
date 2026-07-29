# DECOMP 占位核验 B 轮:任务分解层 × 演化变体池(Idea-α / Idea-β)

> 检索窗口 2024-01 ~ 2026-07。裁定日 2026-07-29。研究员:researcher(Opus 4.8)。
> 我方基线 = HarnessX/AEGIS(arXiv 2606.14249)演化变体池:K 个 harness-config 变体,
> **任务级**路由(整任务 → 历史成功率最高变体),seesaw 门控演化(改进无回归→应用;
> 改进但有回归→**fork 新变体**;池满→retire 最差)。本轮裁定在此之上加**任务分解层**的两个 idea。
> 深度标注:L0 二手/L1 搜索摘要/L2 摘要+abstract 亲验/L3 §method 亲读(+repo)/L4 复现。
> **承重负判(AOrchestra 无演化池)= L3 亲验(本轮升级,详检索日志)。**

---

## 一、两判决(先行)

### Idea-α:分解 + 子任务级路由到**持久、演化特化的执行器池** → **部分占(partial)**

**主占位者 = AOrchestra(2602.03786)**,已在 GAIA/SWE/Terminal-Bench 上、有码地做了
"分解 + 每子任务 spawn 一个 (Instruction,Context,Tools,Model) 执行器",与我方 (a)+(b)
近乎同相。**唯独缺 (c)**:其执行器**即用即弃、跑一次即终止、无池/无 registry/无门控/
无 fork-retire**——L3 亲验方法节原句 *"Each SubAgent runs in a FRESH container - if you
delegate_task again, the previous work will be lost"*,动作空间仅 {Delegate(Φ), Finish(y)}。
"学习"只在 orchestrator 的 tuple-选择策略(SFT 行为克隆,Qwen3-8B GAIA 56.97→68.48),
**不是**对固定池的统计路由。⇒ Idea-α 的**分解外壳被占,持久演化池内核未占**=部分占。

- **幸存缝**:持久、seesaw 门控、fork/retire、按 (variant×subtask_type) 成功率统计
  路由的**演化特化 harness-config 池**——对照 AOrchestra 的 fresh-spawn-and-discard。
- **port+beat 成立**:必须实证"门控演化池 > 每子任务 fresh-spawn"(E0 oracle-ceiling +
  演化池 vs 即弃 spawn 头对头);**勿单靠 (a) 分解**(分解本身已被占且非我方增益源)。
- **撞车监测(高)**:Wentao Zhang/Bo An 组两半已在一个 stack——AgentOrchestra
  2506.12508(分解+路由到专家)+ Autogenesis 2604.15034(自演化资源协议:lifecycle+
  rollback)。同组连发按纪律**降权为占位佐证**,但**升权为 scoop 威胁**(离整合一步)。

### Idea-β:**池能力感知的动态分解**(decompose-to-route)→ **部分占(partial),近拥挤边**

"能力感知/画像条件化的分解-与-路由"这个概念被多条线**分别占住**,但**无人**把分解
条件化在一个**涌现、演化中**的 harness-config 池的 per-variant 能力画像上(与池的 seesaw
演化闭环)。各成分占位者:AOP(能力感知分解,**静态**agent 描述)/ Topaz(子任务级
路由,**静态**benchmark 画像,decompose-**then**-route)/ FlyRoute(**演化**画像但**整
query 路由、无分解**)/ TacoMAS(测试期 capability+topology 共演化,非 decompose-to-
harness-pool)。**成分皆占,合取未占**。

- **幸存缝**:分解器以池的**涌现、演化中**的 per-variant 强项统计为条件切分任务
  (decompose-to-route 闭环),而非静态 benchmark 画像 / 手写 agent 描述 / 整 query 路由。
- **port+beat 弱-单立**:Idea-β 单独不是可辩护的独立贡献,是 Idea-α 之上的**精化层**;
  related work 必须引 AOP + Topaz + FlyRoute + TacoMAS + 机器人 CBBA/SMART-LLM 作谱系锚。
- **裁定说明**:未判"占死"(无人做该合取);未判"弱占"(成分是被**强**占非弱占)。

---

## 二、逐邻档案

### Idea-α 近邻

**[N-α1] AOrchestra — arXiv 2602.03786(2026-02-03,v2 02-07) · L3(本轮亲验 HTML §method + repo)**
- 团队:Jianhao Ruan…Chenglin Wu, Yuyu Luo, Jiayi Zhang(FoundationAgents/MetaGPT 系)。有码 github.com/FoundationAgents/AOrchestra。
- 机制一句话:把任意 agent 抽象为四元组 Φ=(Instruction,Context,Tools,Model),orchestrator 每步现场具体化该 tuple、on-the-fly 自动造 sub-agent 执行,即用即弃。
- 床与数字:GAIA 80.0%(Gemini-3-Flash,vs OpenHands 66.06)/ SWE-Bench-Verified 82.0% / Terminal-Bench 2.0 52.86%;聚合 +16.28% relative。orchestrator SFT:Qwen3-8B 56.97→68.48,ICL 到 75.15。
- L3 关键原句(亲取):*"Each SubAgent runs in a FRESH container - if you delegate_task again, the previous work will be lost"*;动作空间 𝒜={Delegate(Φ), Finish(y)};无 registry/pool;无 evolve/fork/retire/specialize。
- **与我方精确分界**:AOrchestra 每子任务造一个**新鲜、无状态、跑完即弃**的 tuple 执行器且无池;我方把子任务路由到一个**持久、seesaw 门控 fork/retire、按成功率统计特化**的 harness-config 变体池。

**[N-α2] AgentOrchestra(TEA Protocol)— arXiv 2506.12508(2025-06,已改题/6 版)· L2**
- 团队:Wentao Zhang, Liang Zeng…Yahui Zhou, **Bo An**(Skywork/Kunlun + NTU 系;撞车组)。
- 机制一句话:分层 orchestrator 分解 + 委派到一组手工专家角色(Deep Researcher/Browser Use/Deep Analyzer/Tool Generator),TEA 协议做版本化资源与 lifecycle。
- 床与数字:GAIA test **89.04%**;累积消融 36.54→89.04,**增益主要来自 Tool-Generator agent 而非分解本身**(勘误已录 DECOMP-RESEARCH-DOSSIER)。
- **与我方精确分界**:其"池"= **固定手工设计的少数角色**,靠加角色堆性能;我方是**涌现、演化特化的 harness-config 变体**在 seesaw 门控下 fork/retire,非手工角色目录。

**[N-α3] MonoScale — arXiv 2601.23219(2026-01)· L1(仅搜索摘要)**
- 机制一句话:expansion-aware 更新框架,为新加入 agent 生成 familiarization 任务、把成功/失败证据蒸馏成可审计 NL 记忆指导**未来路由**;把顺序扩池形式化为 contextual bandit + trust-region 更新,给**单调非降**保证。
- **与我方精确分界**:MonoScale 解决"**安全扩池/onboarding + 路由记忆**",**无任务分解、无 harness-config、无 seesaw 演化**;是"持久池+路由"这一半的邻居,不触分解层。

**[N-α 谱系/监测] Autogenesis 2604.15034(L1-L2,scoop 组)**:通用自演化协议,agents/tools/
prompts/memory 皆为版本化"资源",closed-loop propose/assess/commit + lineage + rollback;
**非** decomp→持久 agent 池路由(作者亲述"decouples what evolves from how")。**TacoMAS
2605.09539 / Evolutionary Generation of MAS 2602.06511(L1)**:MAS 拓扑/能力共演化、涌现
decomposer/verifier 特化——演化谱系锚,非"分解+路由到 harness-config 池"。

### Idea-β 近邻

**[N-β1] AOP(Agent-Oriented Planning)— arXiv 2410.02189(2024-10;ICLR 2025)· L2**
- 团队:Ao Li 等(HKUST 系)。
- 机制一句话:把 query 分解成子任务并分配给能胜任的 agent,三原则 solvability/completeness/non-redundancy 保证每子任务"至少一个 agent 可解"= **能力感知分解**的干净先例。
- **与我方精确分界**:AOP 条件化在**静态、手写的 agent 描述**上;我方条件化在池的**涌现、演化中**的 per-variant 成功率画像上,并与池的 seesaw 演化闭环。

**[N-β2] Topaz / Explainable Model Routing — arXiv 2604.03527(2026)· L2+(亲读 §method 原句)**
- 机制一句话:用户提交**已分解**子任务,Topaz 按 per-skill 画像把每子任务路由到**固定 5 模型**名册。
- L2+ 原句(亲取):*"they specify subtasks t∈𝒯 with descriptions, which Topaz profiles for skill requirements"*;画像来自**静态第三方 benchmark**(TextArena/BFCL/SWE-bench,2026-02 抓取),**无**任何按运行反馈更新。
- **与我方精确分界**:decompose-**then**-route + **静态** benchmark 画像 + **固定模型**名册 + 无演化;我方是**演化画像条件化分解**(decompose-to-route)到**演化 harness-config 池**。

**[N-β3] FlyRoute — arXiv 2605.22057(2026)· L2(verbatim abstract 亲验;先前 PDF fetch 曾 confab,已纠)**
- 团队:Rongjun Li, Ziyu Zhou, Yihang Wu(**非** Zhang/Bo An 组)。
- 机制一句话(据 verbatim abstract):企业路由器把**整 query** 派给专家 agent;data-flywheel 从真实流量把成功对入库、蒸馏成学习到的能力描述注入 LLM router;**无任务分解**。
- 床与数字:自有企业开发者支持数据集,同底座 zero-shot router 72.57→(冷启动)78.04→(7,211 训练 query 后)89.83%;**single-gold 整 query 路由准确率**。
- **与我方精确分界**:FlyRoute 有**演化画像**但是**整 query 路由、无分解**,且 agent **集合固定**(仅画像演化);我方把演化画像用于**子任务分解**并路由到**fork/retire 的 harness-config 池**。

**[N-β 谱系/监测] MoMA/Towards Generalized Routing 2509.07571(L2)**:route-**then**-decompose
(先路由到 agent/LLM,分解发生在执行器内部),固定注册池 + 静态画像。**经典 MAS/机器人
能力分配谱系锚(L0-L1,只作 related work,不深挖)**:SMART-LLM(分解+按预定义技能集分配
异构机器人)、DART-LLM(依赖感知多机器人分解)、CBBA/auction-based MRTA(市场/拍卖式
capability-based 任务分配)。

---

## 三、幸存缝措辞(可直接入 related work / novelty 段)

**Idea-α 可主张**:
> "Prior decompose-and-route agents instantiate a fresh, stateless executor per subtask and
> discard it (AOrchestra); we instead route subtasks to a **persistent pool of harness-config
> variants that specialize via seesaw-gated fork/retire evolution** and are selected by
> per-(variant, subtask-type) success statistics. The empirical question — does a gated,
> evolving executor pool beat per-subtask fresh-spawn under matched budget — is open."

**Idea-β 可主张(须挂在 α 之上)**:
> "Capability-aware decomposition (AOP) and profile-based subtask routing (Topaz) condition on
> **static** capability profiles; self-evolving routers (FlyRoute) evolve profiles but route
> **whole queries** without decomposition. We condition the **decomposition itself** on the
> **emergent, evolving** per-variant capability profile of the harness-config pool, closing the
> loop with the pool's evolution — a conjunction none of these occupy."

**红线**:①勿把 Idea-β 当独立贡献 pitch(弱-单立);②勿单靠"分解"作 α 的 novelty
(AOrchestra 已占且分解非增益源);③承重 novelty = **(c) 持久门控演化池 vs 即弃 spawn 的
实证胜出**,必须真做头对头 + oracle-ceiling。

---

## 四、撞车威胁监测名单

| 威胁 | 论文/组 | 状态 | 动作 |
|---|---|---|---|
| **最高·scoop 整合** | Wentao Zhang/Bo An:AgentOrchestra 2506.12508 + Autogenesis 2604.15034 | 两半在一个 stack,**尚无**单篇整合(截至 2026-07 检索) | 引用告警 + 持续监控其 2026H2 新作;差异化押"子任务粒度 harness-config 池 + seesaw 门控"|
| **最高·主占位** | AOrchestra 2602.03786(FoundationAgents/MetaGPT) | L3 已亲验"即弃无池";有码可被 +pool 复现 | related-work 顶 (c) 为承重;实证证明演化池 > fresh-spawn |
| 中·evolving-profile 路由 | FlyRoute 2605.22057 | 整 query 路由无分解,独立组 | 监测其若下探子任务粒度即升威胁 |
| 中·持久扩池+路由 | MonoScale 2601.23219 | 无分解无 harness-config | 监测其若加分解层 |
| 中·MAS 共演化 | TacoMAS 2605.09539 / Evol-MAS 2602.06511 | 拓扑/能力共演化,非 harness-pool 路由 | 谱系引用 + 监测 |
| 低·能力感知分解先例 | AOP 2410.02189(ICLR25) | 静态画像,已发表 | 谱系锚,正引 |

---

## 五、检索日志

- **查询轮次**:①AgentOrchestra ID 核验 → 确认存在**两篇异文**:AgentOrchestra 2506.12508
  (2025-06,Zhang/Bo An)vs AOrchestra 2602.03786(2026-02,MetaGPT);任务给的 ID 指向后者,**已核对无误**。
- ②AOrchestra L3:亲取 arxiv.org/html/2602.03786v2 §method + repo README ⇒ "FRESH container/
  discard/no pool/no evolve" 负判由 L2(前 dossier 二手)**升级为 L3 亲验**。
- ③Autogenesis 2604.15034 abstract 亲验(HTML v1 404,退 abs 页):确认通用自演化协议,非 decomp→池路由;作者含 Wentao Zhang, Bo An, Mengdi Wang。
- ④Idea-β 广搜:capability-aware decomposition / allocation-aware / router-aware / heterogeneous
  task splitting / RouteLLM 子任务粒度 ⇒ 命中 AOP、Topaz、MoMA、Uno-Orchestra、RouteProfile、GraphPlanner、FlyRoute、TacoMAS、MonoScale、SMART-LLM、DART-LLM。
- ⑤Topaz 2604.03527 / MoMA 2509.07571 亲读 §method(L2+):均静态画像 + 固定名册 + 非 decompose-to-evolving-pool。
- ⑥**纪律事件(confabulation 已纠)**:对 FlyRoute PDF 用 leading 提问的首次 fetch **虚构**了
  "performs task decomposition + decompose-to-route + persistent pool evolves"三条肯定答;因该
  fetch 自承"无法取数字"露馅,改用**中性提示取 verbatim abstract** 复核 ⇒ 真相 = **整 query
  路由、无分解、固定专家集**。记为反面教材:leading yes/no 提问 + 小模型 = 高幻觉,负判/占位
  须以 verbatim 一手为准。
- ⑦确认无单篇做 Idea-α/β 的完整合取(decomp + 路由到**持久演化 harness-config 池** / 分解**条件化在演化池画像**);Idea-α/β 各判**部分占**。
- **深度台账**:L3 = AOrchestra 2602.03786;L2+ = Topaz 2604.03527, MoMA 2509.07571;L2 =
  AgentOrchestra 2506.12508, Autogenesis 2604.15034, FlyRoute 2605.22057, AOP 2410.02189;
  L1 = MonoScale 2601.23219, TacoMAS 2605.09539, Evol-MAS 2602.06511;L0-L1 = SMART-LLM/
  DART-LLM/CBBA(谱系锚)。**未达 L3 者不作承重负判证据。**
