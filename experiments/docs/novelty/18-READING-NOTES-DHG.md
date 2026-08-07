# 18 — 精读笔记：D / H / G 三簇（2026-08-07 深读批，六并行 researcher）

> **定位**：D 簇 9 篇 + H 簇 9 篇 + G 簇 3 篇的机制级精读收束。本批 21 篇全部升
> 〔精读〕，17 号台账状态以本文为准。台账勘误两处见 §5。原文短语均经 arXiv
> HTML/PDF 核对（MermaidFlow 读了 raw PDF，全文落盘 tool-results/mermaidflow.txt）。

---

## 1. D 簇：架构即图

### MermaidFlow（2505.22967，NTU/A*STAR）
节点 = 6 类型化声明单元（CustomOp/Interface/ProgrammerOp/ScEnsembleOp/TestOp/
CustomCodeGenerateOp），边 = 类型约束执行流 + 数据标签。无图论算法——约束保持
进化搜索；验证器 = **纯语法级**：软检查 W1–W5（正则实现：接口存在/PROBLEM→RETURN
连通/类型归类/类型合法/ScEnsemble≥2 入边）+ Mermaid CLI 编译。锚点：均分 80.75
vs AFlow 78.67；有效代码率 >90% vs AFlow 50%。**关键发现**：宣称 "composed
without revalidation"（闭包引理 Lemma 1）但实现对每个候选**全量重跑 checker**——
无增量重验、无图哈希去重、无声明↔观测对账（纯静态无观测迹）。
**判定**：弱形态三合一在每任务 workflow 层已成立（供体）；组合层 + 语义级验证 +
增量重验 + 对账全缺（C4 冲突点：正面主张其"免验"设计在组合层不成立）。

### AgentFlow / ADG（2607.01640）
6 类型化节点（Agent/Prompt/Model/Capability/Memory/Policy）+ **三族强类型边**：
ACDG 组件依赖（无向）、ACFG 控制流（有向可守卫）、ADFG 数据流（prompt/参数/
返回/消息/**state 读写**）。纯静态、无 LLM、over-approximate（"possible
dependencies rather than a single concrete execution trace"）。锚点：中位 17 节点/
28 边恢复，59/60 成功；BOM 2295 组件+1008 绑定 vs 基线 0。
**判定**：ADFG 是 GS def-use 与 IR 类型词表的**双料蓝本**；我们 = 它的"推理期
在线、具体单迹"版。Δ7 的供体坐实。

### Agentproof（2603.20356，Luleå）
类型化 workflow 图（节点 8 类；边 direct/conditional/parallel/loop）+ BFS/DFS
六项结构检查：①出口可达 ②反向可达 ③死端 ④router 形状 ⑤HITL 存在 ⑥工具声明；
失败产 witness trace。时序 DSL 手写规约→DFA（直接构造）。锚点：5000 节点
104.7ms；**27%/18 = 自建含缺陷基准，作者自承非生产基线**——经验锚点弱，引用需注。
**判定**：Δ8 供体坐实；且 ②反向可达 ≈ GS-C 反向切片的静态版、①出口可达 ≈ GS-A
可达性的判据来源——related work 可用此对应立"离线静态 → 在线动态"的搬运句。

### HarnessForge（2606.01779，北航+清华）
harness = **(Planning, Action, Memory) 文本三元组，非图非 typed IR**；policy =
冻结 reasoner + LoRA δ，成对演化（fault-attribution → archive-guided → generation
→ Pareto 半选）；**涉及训练**（SFT 对齐，可选 GRPO）。验证仅 interface/smoke
test + Pareto fitness。锚点：RestBench-TMDB 76.0% vs GRPO 64.0%（最大 +12），
均值 +3.56%。
**判定**：立意最重叠的对手；差异化三连：typed build 验证 / 图哈希去重 / 声明↔观测
对账它全无。且它要训练，我们 training-free 侧立。

### NLAH（2603.25723，清华深研院）
harness = 可编辑 NL 文档，**运行时解释非编译**（IHR 直接把文本转 agent call/
handoff/state/validation gate）。锚点：token 60.1k→2.9k（−95%）；Live-SWE 73.0
vs code 67.0。**自列局限（附录 G）**："natural-language imprecision …
under-specified, interpreted differently across models, or weakened by
paraphrase"——**无机器可核约束**，这句是 C4 的靶心原文。
**判定**：表示层正面分叉对手；其自曝软肋 = 我们 typed IR + build 验证的存在理由。

### SIGIL / AG-IR（2607.27309，Michigan）
prose skill → typed IR 图 → 确定性 lowering 成可执行。**Owner Test**："is this
step's output a function of its inputs?"——是则 code-owned 结构，否则 model-owned
槽位。12 节点类型（Mind 4 + Flow 4 + Boundary&Code 4）；**6 道 gate 全部确定性
代码判、无 LLM judge**。锚点：mandate 合规 56%→86%，全流程 28%→65%（2.3×），
token 0.58×；跨代模型 harness 恒 86%（"the graph, not the model, carries the
procedure"——金句）。
**判定**：Δ9 供体坐实；**Owner Test 为待吸收候选**（IR 的结构/行为分界判据，
见 §4）。

### HierFlow（2607.21609）
上层拓扑（改/增/删子任务、重组依赖）× 下层 MCTS 代码搜索，gating Z=S·U
（未探索×熵）。锚点：MATH +1.85 vs MaAS，迁移退化 0.00 vs ADAS 3.88%——
**增益微弱**。**判定**：拓扑搜索谱系对手；边界句：它按 query 现场搜（贵），
我们编译 + 验证固定类型化 IR。

### GBC（2606.28187，UIUC）
agent DAG + 归因图（top-m 前驱，默认 m=1）；**真梯度**（embedding 概率 L1 范数），
attribution trajectory 反传 verbal loss 改 prompt。**固定拓扑为作者明写**：
"the order of the agents is fixed… you should not suggest changing the order"
（附录 C.2）——**全 session 最锋利的边界句原文**。锚点：MultiWOZ JGA 28.9→54.4；
定位准确率 85–90% 仅为图表目测、非硬引数字。
**判定**：对手兼供体（attribution trajectory ≈ 反向切片的梯度版；我们结构反切、
零梯度、且图本身可变）。

### Agint（2511.19635，DL4C@NeurIPS'25 workshop）
六层 type floors：TEXT→TYPED→SPEC→STUB→SHIM→PURE + 解析状态机；code DAG +
JIT 合成。**零实验**（自认 "Comprehensive evaluation … remains future work"；
"3–10× speedup" 无支撑）。
**判定**：只能当机制供体（type floors 分层思想），不可当定量对手。

## 2. H 簇：单代理内部图

### 推理图四篇（定位句"前瞻脚手架"验证：**全部成立，LATS 带注**）
- **GoT**（AAAI'24, 2308.09687 ✅）：边 = "constructed using t1 as direct input"
  ——形式上最接近 def-use 的前瞻版；GoO 静态、执行前定死。锚点：排序误差 −62%
  同时成本 −31%。
- **RAP**（**EMNLP 2023**, 2305.14992 ✅）：世界模型**想象**未来态上跑 MCTS——
  定位句最干净的一篇。锚点：LLaMA-33B 超 GPT-4+CoT 相对 33%。
- **LATS**（ICML'24, 2310.04406 ✅）：**唯一需带注的**——真环境 observation 折进
  节点 state，但图拓扑仍是前瞻搜索（边=待探索 action）。related work 定式：
  "结构前瞻、历史仅节点标量载荷"。锚点：HumanEval 92.7。
- **PLaG**（ICML'24, 2402.02805 ✅；**论文名勘误**：实为 "Graph-enhanced Large
  Language Models in Asynchronous Plan Reasoning"，PLaG 只是其提示法）：DAG 塞
  prompt 让 LLM 读，**无图算法执行**。锚点：0.777 vs 0.657。

### 记忆/工具图五篇（"内容 vs 结构"验证：**成立；ToolChain\* 需补一条**）
- **AriGraph**（2407.04363 ✅）：环境观测抽三元组建语义+情景图——内容图/世界
  模型；情景边至多是观测时间线，非动作 def-use。锚点：0.79–1.0 vs 基线 0.17–0.52。
- **KG-A2C**（**台账勘误：正确号 2001.08837，Ammanabrolu & Hausknecht，ICLR
  2020**；原记 1908.06556 是 Riedl 合著的 KG-DQN 迁移论文）：OpenIE 建状态图，
  GAT 编码 + graph mask 砍动作空间。锚点：Zork1 34 vs TDQN 9.9。内容图干净代表。
- **ControlLLM**（ECCV'24, 2310.17796 ✅）：**任务前预建**的工具 I/O 类型图上
  DFS——先验结构最干净代表。锚点：难任务 93% vs 59%。
- **ToolChain\***（ICLR'24, 2310.13227 ✅）：**二分法的例外**——per-run 增量
  展开的候选动作搜索树（多数节点从未执行），既非先验也非执行史。**边界句必须
  补**："per-run 候选搜索树 ≠ def-use 执行图"（假设候选 vs 已执行输出；plan
  顺序 vs 数据依赖）。锚点：比 MCTS 快 7.35×。
- **ToolNet**（2403.00839 ✅）：跨 run 轨迹聚合的 2-gram 工具转移图，可在线
  更新——聚合先验，非单 run def-use。锚点：3992 API，token 38.5–49.7%。

## 3. G 簇：技能图三篇（S6 实现级细节）

- **SkillDAG**（2606.03056 ✅）：5 边类（depends_on/specializes/composes_with/
  similar_to/**conflicts_with 仅在线**）+ 三不变式（无环/非矛盾/append-only 可逆）
  + propose(dry-run)→commit 三查 + 边带 origin/reason/task_id。检索 = 余弦 K=5 +
  可行边 BFS D=2。**诚实注：换 gpt-5.2-codex 后 ALFWorld 打平（93.6=93.6）——
  增益随强模型收窄**，S7 选模型档位时要记住这条。
- **Graph-of-Skills**（2604.05333 ✅）：4 边类（dep 由 I/O schema 重叠 ≥0.6
  确定性生成；余者稀疏 LLM 验证）；**reverse-aware PPR**（α=0.2、迭代 50）+
  双预算注水（per-skill 2400 字符 / global 12000 / top-8）——S6 检索可照抄的
  参数面。**Appendix E Error Taxonomy 是"边故障分类空白"的最近先例**：
  Retrieval Misses/Partial/Good-Bad-Exec/Infra 是**结果级**分类，从未归因到
  具体边的缺失/错误/陈旧——论文里必须点名它并写清"结果级→边级"这一步。
- **GATE**（2502.14848 ✅）：分层工具图但**边无类型**；合并（Smith-Waterman）
  /剪枝（频次阈按层）/Self-Check（生成测试用例验证再入库）；检索 GraphRank
  （阻尼 0.4 的 PageRank 变体）。明确无根因诊断。锚点：Gold Sword 14.0 vs
  Voyager 46.3 iters（3.3×）。

**边故障分类空白主张：成立**，最近先例 = GoS Appendix E（结果级），须点名。

## 4. 净影响汇总

1. **gap (i) 精化坐实**：MermaidFlow 在每任务 workflow 层已有弱三合一（语法级
   验证 + 全量重验）；我们的主张收窄为**组合层 + 语义/协议级验证（DFA、引证门）
   + 增量选择性重验 + 声明↔观测对账**——四个词它一个都没有，C4 冲突点是它的
   "composed without revalidation" 引理与实现的落差。
2. **推理期在线图算法：D 簇九篇全空**——全部离线/静态/test-time 搜索，无一在
   run 内消费执行历史结构。GS 三位与 π 仍是白地（π 为"对全批最干净的白地"）。
3. **新边界句弹药**：GBC "the order of the agents is fixed"（固定拓扑明写）；
   NLAH 附录 G 自曝无机器可核约束；SIGIL "the graph, not the model, carries
   the procedure"；LATS/ToolChain* 两条补丁句（§2）。
4. **待吸收候选**（未落 Δ，待定夺）：①SIGIL Owner Test 作 IR 结构/行为分界
   判据（Δ14 候选）；②Agentproof "反向可达 ≈ 反向切片静态版"的对应句入
   related work；③GoS 的 PPR+双预算参数面直接给 S6 实现。
5. **证据强度警示**（引用时标注）：Agint 零实验；Agentproof 27% 为自建缺陷
   基准；GBC 85–90% 为图表目测；SkillDAG 增益随强模型收窄；HierFlow 增益微弱。

## 5. 台账勘误（已同步 17 号）

1. **KG-A2C**：arXiv 1908.06556 → **2001.08837**；合著者 Riedl → **Hausknecht**；
   出处补 ICLR 2020。（1908.06556 = KG-DQN 迁移论文，另一篇。）
2. **PLaG**：论文名实为 "Graph-enhanced Large Language Models in Asynchronous
   Plan Reasoning"（ICML'24），PLaG 是其中的提示方法名。
