# 17 — 论文台账（图基座 / 双图一迹 全部涉及文献）

> **定位**：2026-08-07 session（doc 15/16 + artifact v3.1）涉及的全部文献的唯一登记处。
> **08-07 深读批**：D 簇 9 篇 + H 簇 9 篇 + G 簇 3 篇已精读（机制笔记见 **18 号**），
> 该 21 篇状态一律以 18 号为准（本表未逐行改标）；勘误两处（KG-A2C 号、PLaG 名）已改行。
> 不铸新编号。**核验状态四档**：〔精读〕全文已读并出机制报告；〔已核〕researcher 已抓
> arXiv 原文页确认存在与机制；〔检索〕仅搜索命中，引用前核对；〔记忆〕凭模型记忆，
> **编号与出处引用前必须逐条核对**。2026 号段（26xx.x）条目一律引用前二次确认终稿。

---

## A. GS-C 对手：轨迹图归因（正面对话，精读优先级最高）

| 论文 | 出处 | 机制一句话 | 论文角色 | 状态 |
|---|---|---|---|---|
| GraphGPO (Cheng et al.) | arXiv 2605.26684, 2026 | rollout 聚合状态转移图，按目标图距离缩短量发 step 优势 | GS-C 最近对手：训练期/多 rollout vs 我们推理期/零额外 | 〔已核〕 |
| Liu et al. | arXiv 2605.29697, 2026 | 实体-关系图最短路 k^(−d) 衰减发 step reward（GDCR→SAPO） | GS-C 衰减形态参照；实体图 vs def-use 图分界 | 〔已核〕 |
| SPA-RL | arXiv 2505.20732, 2025 | MLP progress estimator 重分配终奖（非图） | GS-C 实验的非图 baseline | 〔检索〕 |
| Causal Agent Replay | arXiv 2606.08275, 2026 | 反事实重采样 + Shapley 归因 | 与内部 ⑨ 撞 MDE 同族；GS-C"不吃 MDE"论证对照 | 〔检索〕 |

## B. 循环检测（GS-B 对手）

| 论文 | 出处 | 机制一句话 | 论文角色 | 状态 |
|---|---|---|---|---|
| George et al. "Unsupervised Cycle Detection in Agentic Applications" | ICPE'26 Companion **WIP**；arXiv 2511.10650 | **无 ALDG 术语、无 SCC、无图算法**——CDDAG 边频（P 0.05）/CDCS 栈重复子串（F1 0.45）候选 → CDSA 兄弟节点余弦确认，混合 F1 0.72；单 LangGraph 股票应用，患病率 3.6%；real-time 仅 future work | GS-B 的 C4 对手；其 productive cycle 定义 + 从不度量 progress = 进展判据的动机 | 〔精读〕 |
| When Agents Do Not Stop | arXiv 2607.01641, 2026 | 无限 agentic loop 现象刻画 | 动机引用 | 〔检索〕 |

## C. 图式上下文 / 记忆（GS-A 近邻，拥挤区）

| 论文 | 出处 | 机制一句话 | 状态 |
|---|---|---|---|
| HippoRAG | NeurIPS'24; arXiv 2405.14831 | 实体图 Personalized PageRank 定多跳召回 | 〔检索〕 |
| HippoRAG 2 | arXiv 2502.14802, 2025 | PPR + dense/sparse 融合 | 〔检索〕 |
| Zep / Graphiti | arXiv 2501.13956, 2025 | 双时态知识图，边随时间失效，图遍历检索 | 〔检索〕 |
| A-MEM | arXiv 2502.12110, 2025 | Zettelkasten 记忆图，LLM 生成链接与 consolidation | 〔检索〕 |
| RepoGraph | arXiv 2410.14684 | 行级 def-ref 代码图，ego-network 入 SWE 上下文 | 〔检索〕 |
| LocAgent | arXiv 2503.09089, 2025 | 异构代码图 + 图引导遍历做定位 | 〔检索〕 |
| CodexGraph | arXiv 2408.03910 | Neo4j 代码图库，agent 发 Cypher 查询 | 〔检索〕 |
| GraphCoder | EMNLP'24 | 控制流+数据流上下文图 coarse-to-fine 检索 | 〔检索〕 |

GS-A 差异句：图对象 = run 自身消息历史，非外部库/知识图；"检索什么进来"→"历史里留什么不走"。

## D. 架构即图 2025–26（宏观 IR 层正面威胁簇，08-07 定向扫）

| 论文 | 出处 | 机制一句话 | 与"类型化 IR + 验证 + 图编辑"的距离 | 状态 |
|---|---|---|---|---|
| **AgentFlow / ADG** | arXiv 2607.01640, 2026 | 从异构框架恢复 Agent Dependency Graph（类型化节点 agents/prompts/models/tools/memory；类型化边依赖/控制/数据流），跑静态分析（Agent BOM + 污点） | **最接近类型化 IR，但纯只读**：无编辑、无 rebuild、无重测 | 〔已核〕 |
| **MermaidFlow** | arXiv 2505.22967, 2025 | workflow 表示为 Mermaid 图 IR，演化算子 = 图编辑（crossover/mutation/insert/delete），safety-constrained 静态可验（有效率 >90% vs AFlow ~50%） | **单篇最接近**；缺：对象是每任务 workflow 非组合层、整图重验非增量、Mermaid 语法非类型化契约 | 〔已核〕 |
| **HierFlow** | arXiv 2607.21609, 2026 | 上层 topology + 下层 execution 耦合分层搜索，test-time，gating 触发 MCTS | 多智能体 workflow 谱系分层版；无类型化 IR 无验证器 | 〔已核〕 |
| **HarnessForge** | arXiv 2606.01779, 2026 | harness–policy 对分离并联合演化（fault-guided harness tailoring + harness-conditioned policy alignment） | **概念级威胁**："harness 结构可演化"立意重叠；但未图化、无验证器——必须点名 | 〔已核〕 |
| **NLAH**（Natural-Language Agent Harnesses） | arXiv 2603.25723, 2026 | harness 逻辑抽成自然语言可编辑文档，主打可检查/可迁移 | **表示层正面分叉（NL vs 图）——C4 对手**：同攻"harness 埋在控制器代码"痛点 | 〔已核〕 |
| SIGIL / AG-IR | arXiv 2607.27309, 2026 | typed IR 把 prose skill 编译成 typed harness，compile gate（规则须有原文引用支撑） | compile gate 先例；对象是单 skill 非整架构 | 〔已核〕 |
| Agint | arXiv 2511.19635, 2025 | 编译器 type floors text→data→spec→code，产出 code DAG + JIT | 程序合成编译器，图是产物非优化对象 | 〔已核〕 |
| Declarative Policy Compilation | arXiv 2603.27299, 2026 | 非图灵完备 DSL 编译决策节点，保穷尽路由/无冲突/引用完整 | 验证强；对象是路由 policy，position paper | 〔已核〕 |
| GBC | arXiv 2606.28187, 2026 | MAS 计算图 token 级影响权重梯度反传定位错误做 prompt 优化，**固定拓扑** | 计算图线后继仍在固定图调参——结构可演化的缝没被堵 | 〔已核〕 |
| SEARL | arXiv 2604.07791, 2026 | 演化 tool graph memory | 属技能/记忆图类 | 〔已核〕 |
| **AgentGraph**（Wu et al., Holistic AI + UCL） | **AAAI-26 demo**, Proc. AAAI 40(48):41721-41723（⚠️ 与 arXiv 2603.20356 **无关**，勿混引） | LLM（schema 约束 structured outputs）从执行日志抽语义知识图（角色边 consumed by/produces/next 等）+ trace 行号回链 + relation-level prompt 越狱/公平扰动重测 + DoWhy 归因 | "Trace-to-Graph" 仅名近：**无经典图算法、纯离线、无 def-use、无架构投影**——三个空位均未占；定位句素材见 16 号 | 〔精读〕 |
| **Agentproof**（Xavier et al., Luleå） | arXiv 2603.20356, 2026 | agent workflow 图的**静态验证**：类型化工作流图 + 六项结构检查 + graph×DFA | 占三要件中"验证"的又一先例；非 trace、无图编辑、非组合层——方法节待读 | 〔已核〕 |
| MOSS | arXiv 2605.22794 | source-level rewriting 自演化（代码文本，DGM 同类） | 文本表示支线 | 〔未核〕 |
| surveys | 2603.22386 / 2607.13104 / FlowEvo 2607.21596 | — | 查漏用，非先例 | 〔未核〕 |

**判定（08-07）**："组合层类型化图 IR + build 验证 + 图编辑动作空间"三合一位置**无单篇占据**——AgentFlow 有 IR 无编辑、MermaidFlow 有编辑+验证但对象错层、SIGIL 有 gate 但域窄。gap (i) 措辞需相应细化（见 16 号 §5）。

## E. 编排 / 拓扑（背景板 + 划界）

| 论文 | 出处 | 一句话 | 状态 |
|---|---|---|---|
| GPTSwarm | ICML'24; 2402.16823 | agent 图上 REINFORCE 优化边 | 〔检索〕 |
| AFlow | ICLR'25 oral; 2410.10762 | 代码化 workflow 图上 MCTS | 〔检索〕 |
| ADAS | 2408.08435 | meta-agent 代码空间搜索（文本表示） | 〔检索〕 |
| AgentSquare | 2410.06153 | 模块化 agent 设计空间搜索 | 〔检索〕 |
| MaAS | 2502.04180 | agentic supernet 架构搜索 | 〔检索〕 |
| EvoFlow | 2502.07373 | workflow 演化 | 〔检索〕 |
| DGM | 2505.22954 | 自改进（代码文本表示）——gap (i) 的文本代表 | 〔检索〕 |
| DyLAN | 2310.02170 | 动态 agent 网络 + 重要性分 | 〔记忆〕 |
| G-Designer | 2410.11782 | VGAE 生成任务自适应通信拓扑 | 〔记忆〕 |
| AgentPrune | 2410.02506 | 时空消息图一次性剪枝省 token | 〔检索〕 |
| AgentDropout | ACL'25 | 动态删 agent 省 token | 〔检索〕 |
| ATOM | 2605.26178, 2026 | RL 自适应算力分配 + 任务专属拓扑 | 〔检索〕 |
| ZEBRA | 2605.20485, 2026 | 预算 zero-shot 切分到 pipeline 各段 | 〔检索〕 |
| Retrieval-conditioned topology | 2605.05657, 2026 | 可证明预算守恒的拓扑选择 | 〔检索〕 |
| LangGraph | 文档 | 图仅编排表示，不跑算法 | — |

## F. 计算图上优化 agent 程序（"固定图调参"线——与 IR 的分界必须写）

| 论文 | 出处 | 一句话 | 状态 |
|---|---|---|---|
| DSPy | 2310.03714 | 模块图 + compiler/teleprompter 调 prompt/示例 | 〔记忆〕 |
| TextGrad | 2406.07496 | 文本梯度沿计算图反传 | 〔记忆〕 |
| Trace / OptoPrime | NeurIPS'24; 2406.16218 | 执行 trace 建计算图做广义反传（术语撞名，写作注意） | 〔记忆〕 |
| Archon | 2409.15254 | 推理期架构搜索 | 〔记忆〕 |
| Semantic Backpropagation | 2412.03624 | 语言化反传正确性修正 | 〔检索〕 |

分界句：此线在**固定**程序图上做参数/prompt 优化（GBC 2026 仍如此）；我们把**结构编辑**作为动作空间 + 类型化边 + build 验证 + 选择性重测。

## G. 技能图（S6 根基）

| 论文 | 出处 | 一句话 | 状态 |
|---|---|---|---|
| SkillDAG | 2606.03056, 2026 | 类型化边集 + 提案-提交治理三不变式 | 〔检索〕 |
| Graph-of-Skills | 2604.05333, 2026 | PPR + 预算注水图遍历检索（+25.6% 奖励 −56.7% token） | 〔检索〕 |
| GATE | 2502.14848 | 图技能库 vs Voyager 扁平库最高 4.3× | 〔检索〕 |
| HiSkill | 2607.25853, 2026 | 双节点类分层技能图 | 〔检索〕 |
| SkillGraph(RL) | 2605.12039, 2026 | RL 技能图 | 〔检索〕 |
| Voyager | 2023 | 扁平向量技能库（对照基线） | 〔记忆〕 |

## H. 单智能体内部图（related work 五分法素材）

| 论文 | 出处 | 一句话 | 状态 |
|---|---|---|---|
| Graph of Thoughts | AAAI'24; 2308.09687 | 推理状态图 + 聚合/回炼算子 | 〔记忆〕 |
| RAP | 2305.14992 | 世界模型状态上 MCTS | 〔记忆〕 |
| LATS | ICML'24; 2310.04406 | ReAct + 树搜索（树非图） | 〔记忆〕 |
| PLaG（论文名实为 "Graph-enhanced LLMs in Asynchronous Plan Reasoning"） | ICML'24; 2402.02805 ✅ | 异步计划 DAG 塞 prompt 让 LLM 读，无图算法执行 | 〔精读〕 |
| AriGraph | 2407.04363 | 边玩边建情景+语义知识图（内容图非执行结构图——与 GS 的分界句） | 〔记忆〕 |
| KG-A2C（Ammanabrolu & **Hausknecht**） | ICLR 2020; **2001.08837**（⚠️ 原记 1908.06556 有误——那是 Riedl 合著的 KG-DQN 迁移论文） | 前 LLM：OpenIE 建状态图 + GAT 编码 + graph mask 砍动作空间 | 〔精读〕 |
| ControlLLM | 2310.17796 | 工具图上 Thoughts-on-Graph DFS | 〔记忆〕 |
| ToolChain* | 2310.13227 | 动作空间 A* 搜索 | 〔记忆〕 |
| ToolNet | 2403.00839 | 大规模工具图导航 | 〔记忆〕 |

五分法定位句：推理/记忆/工具/外部资源四类图皆是**前瞻脚手架或知识存储**；无一把**执行历史的结构**（def-use）建图并让环在线消费——第 5 类（轨迹图）推理期为空 = GS。

## I. SE 经典（宏观层根基，全部为学界公认稳定文献）

切片：Weiser ICSE'81/TSE'84 · Korel & Laski '88 · Gyimóthy ESEC/FSE'99（相关切片）·
Bohner & Arnold '96。依赖图：Ferrante TOPLAS'87（PDG）· Horwitz-Reps-Binkley TOPLAS'90
（SDG 摘要边）· Yamaguchi S&P'14（CPG）。对账：Murphy-Notkin-Sullivan FSE'95（反射模型）·
Perry & Wolf '92。选测：Rothermel & Harrold TOSEM'97 · Ekstazi ISSTA'15 · Chianti OOPSLA'04。
定位：Tarantula ASE'05。构建：Dolstra Nix '06 · Mokhov ICFP'18。模块化：Baldwin & Clark '00 ·
MacCormack et al. '06 · Sturtevant MIT '13。缺陷网络：Zimmermann & Nagappan ICSE'08。
演化计算 ablation：Liu ICLR'18 · Walker & Miller TEC'08 · Goldman & Punch '13/'15 · DARTS ICLR'19。
动机章工业素材：Bazel/TAP 可达性选测 · Mono2Micro '21 · 图着色寄存器分配 Chaitin '81。

---

## 阅读优先级（速查）

1. 精读五连：Weiser '84 → GraphGPO → Liu 2605.29697 → George et al. 2511.10650（已精读，读原文校对报告）→ Reflexion Models。
2. 架构威胁四连（本轮新增，精读方法节）：**MermaidFlow → AgentFlow/ADG → HarnessForge → NLAH**。
3. 计算图分界三连：Trace → TextGrad → DSPy（备好"固定图调参 vs 结构演化"分界句）。
4. 其余按 doc 15/16 与本文各节角色略读。
