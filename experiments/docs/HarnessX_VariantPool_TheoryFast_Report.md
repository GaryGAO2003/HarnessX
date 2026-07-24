# HarnessX 变体池机制 · 原文替代式报告(单一状态版)

> 论文:HarnessX,arXiv 2606.14249v2(2026-07-02),43 页。
> **本版为合并稿**,取代此前的"正文 + 三轮补遗"分层形态。此前各轮的错误结论已直接改写,不再保留痕迹;错误清单见 §2.2,供追责与方法反思。
> 对照对象:`HarnessX\experiments\docs\ARCHITECTURE.md` §10 + `HARNESSX-IMPL-CHECKLIST.md` v2。

---

## 1. 核心判断

**问题**:单一 harness 在**异质任务集**上演化会停滞。一个修改改善 A 类任务却弄坏 B 类,seesaw 约束(不许回退任何历史已解任务)只能整体拒绝,局部有益的改动被丢弃。GAIA + GPT-5.4 上表现为 ∆=0.0 的完全停滞,且 15 轮后从峰值 73.8% 崩到 49.5%。

**机制**:维护至多 K 个 harness 变体,每道任务路由到"在该任务所属簇上历史成功率最高"的变体。门层分三路——无回退则并入目标变体;**有增有减则 fork 出新变体**(池满退役最差者);净劣化则拒绝。一旦多变体存在,seesaw 按变体收窄:针对变体 k 的候选只在路由给 k 的任务上检验。

**最值得带走的三点**:
1. **把"冲突"从拒绝理由变成分叉理由**——机制上最锋利的一步;
2. **约束的作用域跟随结构演化**:seesaw 从全局收窄到变体,既保防遗忘又解除跨簇误伤;
3. 变体在数据结构上 = **hook→processor 映射的一个差分**(§3.2 p.6 承重句),类型系统保证任意变体的插拔不破坏管线良构性。

**对我方**:🟢 友军兼工单。三条硬事实支撑:
- **Table 6 亲证**单 agent evolver 在变体隔离下达 86.4%,与四阶段 AEGIS 的 87.4% 差距落在一个标准误(~3.3pp)内 ⇒ 我方基于 repo 单 agent evolver 建变体池,**等价于论文自己的 CC SDK 对照臂,不是降级复现**;
- **八项设计参数论文从未定义**(§5),构成可发表的贡献面;
- **变体隔离是全文实证最薄弱的部分**(§2.1),必要性钉死。

---

## 2. 阅读覆盖与可信度声明

### 2.1 覆盖状态:全文逐字读毕(含主循环亲验分级)

| 范围 | 状态 | 亲验层级 |
|---|---|---|
| §1 引言、§2 相关工作、§3 组合层(p.1–6) | ✅ 逐字 | 主循环亲读 |
| §4 适应 + Algorithm 1(p.7–11) | ✅ 逐字 | 主循环亲读 |
| §5 共演化(p.11–14) | ✅ 逐字 | **主循环亲读**(修正:不是"零交集",而是**假设单谱系、与变体池冲突**,见 M15) |
| §6 实验 + Table 4/5/6(p.14–18) | ✅ 逐字 | 主循环亲读 |
| §6.6 失败分析(p.19–20) | ✅ 逐字 | 主循环亲读 |
| §7 讨论、§8 结论(p.21–23) | ✅ 逐字 | 主循环亲读 |
| **参考文献(p.24–27)** | ✅ 逐字 | 主循环亲读(**novelty 立论证据,见 §8**) |
| 附录 A / B.1 / B.2 / B.3(p.28–36) | ✅ 逐字 | 主循环亲读 |
| 附录 C 实例(p.36–37)、D.1 GAIA + D.5 SWE(p.38–39, 42–43)、E 产物布局(p.43)、B.1 prompt 中段(p.32–33) | ✅ 逐字 | **主循环亲读**(数字逐格核对无误) |
| 附录 D 的 WebShop / ALFWorld 尾 / τ³ 失败簇(p.40–41) | ✅ 逐字 | researcher 亲读,主循环采信(**唯一采信项**;关键词扫描确认 variant/ensemble/routing 零出现,与变体池无机制交集) |
| 附录 B.1 中论文自带的 5 处 `[... truncated ...]` | ❌ | **论文本身省略,不可得** |

关键页经 pypdf + pdfplumber 双提取器交叉验证。**全文除 p.40–41 三床失败簇分析(与变体池零交集)外,每一页均为主循环逐字亲读。** 上一版曾把 §5/附录 D 整体标为"researcher 采信",本版已将其中除 p.40–41 外全部补为主循环亲读。

### 2.2 本报告修正过的错误(方法反思)

前两版报告因**按话题定位而非按顺序通读**(首次下结论时仅读了 43 页中的约 15 页)产生五处实质错误,均已在本版改正:

| # | 错误结论 | 正确事实 | 漏读位置 |
|---|---|---|---|
| 1 | manifest 是论文留白 | 附录 B.3 有完整字段表 + YAML schema | 附录 B.3 |
| 2 | cluster "唯一自洽读法" | 三读法并存,论文从未定义 | 附录 B.1 prompt |
| 3 | 143.7M = 内环计费 token(⇒46.5K/次) | 是轨迹体量/元 agent 消耗;内环计费约 7 倍 | §7.5 |
| 4 | pass@2 纯为降噪之利 | §7.1:它**同时**掩盖亚阈值回退 | §7.1 |
| 5 | `GAIATask.level` 可当 cluster,W7 归零 | level 属论文列为"另一条策略"的 domain-aware clustering | §6.3 p.18 |

**方法结论:43 页顺序通读一次的代价,远小于按话题抽读后翻案三次。**

---

## 3. 机制的完整原子分解

### 3.1 形式化

**状态**(§4.1 p.8):`s_t = (H_t, T_t)` —— 当前 harness 配置 + 累积 trace store。
**harness 配置**(Definition 1, p.7):`H = (c1,…,c9)`,九个行为维度,受 hook 类型契约与 singleton 组互斥约束。
**动作**(Definition 2, p.8):代码级编辑 `e: H → H`,**离散但开放**,由 LLM 生成而非从预枚举集合选取。
**seesaw 约束**(p.8 逐字):`U(H̃_t, T_t, r_t)` 要求 "the candidate must **not regress any previously solved task recorded in T_t**"。⚠️ 基准是**整个 trace store 的历史已解任务**,不是上一轮。

**变体池**(§4.5 p.11):
$$\mathcal{H}_t=\{H_t^{(1)},\dots,H_t^{(V_t)}\},\quad V_t\le K$$

**路由**:
$$\text{route}(x)=\arg\max_{k}\ \widehat S\big(k,\ \text{cluster}(x)\big)$$
论文对 `Ŝ` 的全部描述仅一句 "highest estimated success rate on that task's cluster across prior rounds"。

**三路门**(p.11 逐字):"(1) the edit improves some tasks without regressing any, in which case it is applied to its target variant; or (2) it improves a subset while regressing others, in which case the system **forks a new variant** rather than rejecting the edit outright (**retiring the lowest-performing variant if the pool is full**)."

**收窄的 seesaw**:"a candidate targeting variant k is tested **only against tasks routed to k**, so improvements to one cluster cannot regress another."

**预测的三个性质**(§4.5,§6.3 验证):非退化轨迹(峰值=终值)、更持久的探索、更低 token 消耗。

### 3.2 一轮的控制流(Algorithm 1, p.9)

```
for t = 0 … T-1:
    在 H_t 上跑批次 B_t,得轨迹 ∆T_t ; T_{t+1} ← T_t ∪ ∆T_t
    (evidence_t, a_t) ← Digester(∆T_t, T_t)        # a_t < α → idle++, continue
    landscape_t ← Planner(evidence_t)              # 空 → idle++, continue
    {(H̃_t^k, manifest_k)}_{k=1..K_t} ← Evolver(…)  # K_t = 0 → idle++, continue
    ranking ← Critic({候选}, evidence_t)            # 可发一次修订请求
    for k in ranking:                              # ⚠️ 见 §6 的 H1 矛盾
        if DeterministicGate(H̃_t^k, H_t, T_t): k* ← k; break
    ship 或 no-op;idle 更新;idle ≥ P → break
```

**四阶段职责**(§4.3 p.10):
- **Digester**:单轮 ~10M 原始 trace token → ~10K 结构化摘要(二值结果、失败类别、涉事组件 id、证据摘录)+ 跨轮历史链接;
- **Planner**:构造 adaptation landscape(谁在失败、试过什么、涉及哪些组件、**哪些编辑类型尚未尝试**),防欠探索;
- **Evolver**:产出类型安全候选,**每个附 change manifest**;新 processor 代码须附冒烟测试;
- **Critic**:比对 manifest 与 trace 证据、评估非局部效应,**至多一次修订请求**,返回 no_op 或有序 ship_ranking,防 reward hacking;
- **确定性门**:按序 manifest 完整性 → 配置规范化 → build/冒烟 → seesaw,**首个失败即中止**,防灾难性遗忘。

**设计原则**(p.11 逐字):"Language-model subagents explore, hypothesize, and propose; typed structure and deterministic gates determine what ships."

### 3.3 change manifest(附录 B.3 Table 9, p.36)

| 字段 | 含义 |
|---|---|
| `candidate_id` | 如 `C-R3-01` |
| `bucket` | 编辑类型:`prompt` / `tools` / `config` / `processor` |
| `capability_evidence` | 该机制**确实可用**的已验证声明(type / claim / evidence) |
| `file_changes` | path / action(create·modify·delete)/ diff_summary |
| `predicted_impact` | **三分**:`tasks_will_unlock` / `tasks_will_stabilize` / `tasks_at_risk` |
| `attribution_signature` | 编辑若真的生效必然出现的 trace 特征:`processor_invocation`\|`tool_call`\|`prompt_feature` + `tool_name` + `expected_min_calls` |
| `iterates_from` ⚠️ | **Table 9 未列,只在 B.1 prompt(p.32)出现**;修订/回退谱系的唯一钩子 |

定位(p.35):"The manifest makes every harness modification **falsifiable**: the Critic checks whether the next round's trace features match the mechanism and impact the manifest predicted."

⚠️ **manifest schema 中没有任何 variant_id / cluster_id 字段**——§7.1 声称"组合结构让编辑的意图作用域显式化"是变体隔离的前提,但这个作用域在 manifest 层没有对应载体。**这是可攻的接续点。**

### 3.4 Critic 的裁决判据(附录 B.1 p.33-34,论文正文未述)

逐候选:"Pick the single candidate (**or multiple bucket-disjoint candidates**) whose shipping is most likely to raise next round's pass rate without hurting it." 每条裁决**必须**回答候选与现有配置的交互面是否重叠、重叠是有意安全还是意外碰撞。

组合审计三条硬规则:
1. 同一 lever 在最近 3 轮中出现 ≥2 次且累计 hit_rate < 0.4 → 禁止再上线,标 `strategy_concern`;
2. 点名"Evolver 从未触碰、而 digests 指向的 bucket 或 cluster";
3. 本轮 `regressions.md` 若有回退任务,而 Evolver 既未处理也未写"为何可接受"→ **整轮 no-op**。

**Evolver 强制的两级验证**(p.32):Level-1 单元调用可跑;**Level-2 往返可达模型**——"a unit call that returns does not prove the agent sees the return",须模拟到模型下一次输入并断言内容存活;证据贴进 `capability_evidence`。

### 3.5 产物布局(附录 E.1 p.43)

```
runs/<run_name>/
|-- INDEX.md  journal.md  curves.json  scoreboard.json  audit.jsonl
|-- data/  task_history.jsonl  ship_outcomes.json  rejected_candidates.jsonl
`-- R<n>/
    |-- landscape.md              Planner 综合
    |-- candidates/C-R<n>-NN.md   Evolver manifest(每轮 K 份)
    |-- applied/C-R<n>-NN/        已应用的配置与资产
    |-- decision.md               Critic 裁决
    |-- verdicts/V-C-R<n>-NN.md   逐候选裁决
    |-- regressions.md            相对 R<n-1> 变差的任务
    |-- digests/*.md              逐任务失败分析
    `-- trajectories/*.jsonl      原始 rollout
```

⚠️ **布局中没有任何 variant 维度**:无 `variants/`、无 `V<k>/`;`task_history.jsonl` 逐字是 "one line per **(round, task)**"。

---

## 4. 论文给定的全部参数(可直接照抄)

**Table 8(p.29)+ A.2/A.3(p.28-29)+ §6.1(p.15)**:

| 参数 | 值 |
|---|---|
| 每轮候选数 `K_t` | **4**(但 Evolver prompt 说 "You decide how many candidates (K ≥ 1)",见 H2) |
| 随机种子/格 | **3** |
| 噪声阈值 | 忽略 ±5% 的单轮 pass 数波动 |
| 轮数 T / 耐心 P | **15 / 3**(P 的数值在 §6.1 正文,不在 Table 8) |
| 评测 | **pass@2**,标准无偏估计量(公式 6);**基础设施故障计为失败,不剔除** |
| `H_0` | 手工 competent harness + 床专属工具注册表;**各床 Round-0 提示词"列在仓库里"**(B.2 p.34) |
| 元 agent | Opus 4.6;并发 4,每角色 200 步上限 |
| 任务 agent | Sonnet 4.6 / GPT-5.4 / Qwen3.5-9B |
| 任务并发 | 10;每 rollout 跑在全新环境实例中 |
| max-steps | GAIA 20 / WebShop 20 / ALFWorld 15 / τ³ 200 / SWE-V 200 |
| GAIA 任务集 | **103 题 text-only,难度 39/52/12**;每轮重新评分同一集合 |
| GAIA 工具 | web search(**Baidu API**)、web fetch、bash、file read |
| GAIA 失败簇 | blocked-source 39% / reasoning 33% / figure-visual 11% / doc-table parse 11% / scope ambiguity 6% |
| 变体池容量 `K` | ⚠️ **全文未给** |

---

## 5. 论文未定义的八项(我方须设计,即贡献面)

穷尽搜索(双提取器、含图注表注脚注)后确认**全部维持,无一被任何章节填补**:

| # | 缺口 | 论文最接近的表述 |
|---|---|---|
| 1 | **变体池容量 K** | 仅 "up to K variants"(p.11 / Table 5 行标签),无值。⚠️ K 在文中**三义复用**:池容量 / 每轮候选数(=4)/ buffer 中 harness 版本序号 |
| 2 | 成功率估计 `Ŝ` 的口径 | 三处措辞各异:"estimated…across prior rounds"(p.11)/ "prior"(p.17)/ "overall"(p.22);无窗口、无平滑、无置信下界 |
| 3 | 路由冷启动(第 0 轮) | 无。仅有部署期规则(见下 §7-M1) |
| 4 | fork 后新变体的任务与历史继承 | 无 |
| 5 | 退役的 "lowest-performing" 度量 | 无指标、无窗口、无平局处理、无被退役变体任务的再分配 |
| 6 | 候选的目标变体选择机制 | 仅 "a candidate targeting variant k",怎么选未述 |
| 7 | 变体隔离下 idle 计数的作用域 | Algorithm 1 只有一个全局 idle;§4.5 从未回到伪代码 |
| 8 | **cluster 的正式定义** | 见 §5.1 |

### 5.1 cluster 的三读法裁决

| 读法 | 证据 | 裁定 |
|---|---|---|
| **(a) 路由诱导的任务划分** | §4.5 "tested only against **tasks routed to k**";§6.3/§7.1/§7.5 "its target cluster" | ✅ **主臂**(字面机制、零额外成本) |
| (b) 元 agent 自生成的失败模式分组 | Planner prompt p.30 "recurring failure modes … **your own grouping**";"name the neglected bucket and **the cluster it would target**";C.1 p.36 "grouped the 23 failed tasks **by failure mode**";附录 D 约 40 次 "failure clusters" | 🔬 消融臂(需 meta-agent 输出可复用簇 id) |
| (c) 语义/领域聚类 | §4.5 字面 | ❌ **可证伪排除**:p.18 把 "Domain-aware clustering" 与 "Task-level tournament" 列为**与 Ensemble 并列的另外两种** pilot 策略(30–40 题、≤8 轮) |

**(a) 与 (b) 在同一段内混用,论文从未桥接。** 我方 SPEC 须选定并在 threats to validity 声明。

---

## 6. 论文内部矛盾十条

| # | 矛盾 | 对实现的含义 |
|---|---|---|
| **H1** | Algorithm 1(p.9)`break` = 只上线**第一个**过门候选;Critic prompt(p.34)"Stage 4 ships **every** listed candidate in order but skips any whose bucket was already claimed" = bucket 互斥的**多船** | 伪代码 ≠ 实现。多船语义与"目标变体"耦合更自然,我方须选一并声明 |
| **H3** | Table 7 表注 "**All main experiments use the Global** strategy" vs Figure 6(d) 逐字 "**R8~9 Ensemble recovers → 99.1%**" | 主实验是否混入 Ensemble 存疑,削弱 Global/Ensemble 的干净分离 |
| **H6** | §4.3 门控清单 = manifest 完整性 → 规范化 → build/smoke → seesaw;Figure 6(c) 图注 = "Gates check **replay/novelty**/structure" | replay 与 novelty 在正文别处**不存在**,却正是 repo 的 `replay.py` 与 `check_novelty` ⇒ **repo 比论文正文更接近真实实现** |
| **H10** | §7.6 "每个 shipped edit 都带 **rollback target**" vs Figure 6(f) "**R12 Rollback fails**; final 49.5%" | 回滚机制存在 ≠ 回滚有效;头条崩塌那次它失败了 |
| H2 | Table 8 `K_t = 4` vs Evolver prompt "K ≥ 1,你决定" | 4 是上限/典型值 |
| H4 | Figure 6 面板标题(Qwen / Sonnet)与图注(GPT-5.4)模型不符 | 图注可疑,引用须谨慎 |
| H5 | Eq.(4) 含 KL 锚 vs §6.5 "no KL penalty (coefficient 0)" | 仅涉共演化 |
| H7 | Table 9 六字段 vs B.1 实际 manifest 另有 `iterates_from` | 实现须补该字段 |
| H8 | 附录 C "across all **19 runs**" vs 正文 "**15** model–benchmark configurations" | 引用数字时避开 |
| H9 | Figure 8 图注"仍未解出任务的失败簇" vs D.1 正文"整轮累积的失败簇" | 失败簇统计口径不明 |

---

## 7. 其余机制细节

**M1 · 部署期路由塌缩为单变体**(§7.5 p.22 逐字):"tasks outside the evolution set are routed to the variant with the **highest overall success rate on the evolution set**"。⇒ 训练期按簇路由,**部署期对未见任务退化为全局最优单变体**,集成事实上塌缩。二者关系论文未解释。既部分回答冷启动,也是可攻的弱点。

**M2 · slot 是 per-configuration 单例**(§3.2 p.6):tool registry / tracer / workspace / sandbox provider / plugin list。⇒ **K 个变体若在 tools 维度分叉,需要 K 份独立 registry / workspace / sandbox**。§7.5 的成本分析**完全没有计入这项**(只报 token)。

**M3 · 类型层的 precondition 承重句**(§3.2 p.6):"each variant differs only in **which processors occupy which hooks**, and the type system ensures that no variant can silently violate the pipeline contract"。⇒ 变体在数据结构上 = hook→processor 映射的差分。保证来自:每 hook 的类型契约(输入事件类型 = 输出事件类型)、`_singleton_group` 互斥 / `_order` / `_after`、以及运行时逐次契约校验。

**M4 · pass@2 的双面性**(§7.1 p.21 逐字):"Under pass@2, a task whose success probability has degraded can still register as 'solved,' so **sub-threshold regressions evade the seesaw constraint**"。⇒ pass@2 降噪的同时掩盖概率漂移,正是 Global 崩塌的机理。

**M5 · 亚阈值耦合是未修复的结构性缺陷**(§7.6 p.23):τ³ Telecom 五轮同型编辑累积,第六轮触发 −14.0%,但**没有任何单个编辑违反约束**。"This is a **structural limitation of per-edit gating**"。**变体隔离并不修复它。**

**M6 · 未定义术语 "counterfactual gate"**(prompt p.32 唯一一次出现,纯 prompt 类候选据此豁免代码验证):**全文从未定义**。

**M7 · 变体池管理无文献锚定(参考文献逐条核对后坐实)**:①§4.5 全节零引用;②**54 篇参考文献里无任何 ensemble / MoE / bandit / expert-routing / model-routing 先例**(逐条扫描 p.24-27);③§2 related work 的三层分类(primitive/orchestrator/productized)与 self-evolving 两线(prompt 优化 / memory)**均未提及变体池或路由**;④唯一沾边先例是 Darwin Gödel Machine [18] 的 "database of agent variants"(p.5),仅一句带过、无机制对比。⇒ **变体池管理在整篇论文里无文献锚定、无 related work 铺垫、无独立实证、且被 §1 与 §4 逐字标注为 "An optional variant-isolation strategy"。这是我方立论最坚实的部分。**

**M11 · D2/D4 是最高频编辑目标 ⇒ slot 复制是高频路径(§3.3 p.6 亲验)**:"D2 (context assembly) and D4 (tool ecosystem) are the most frequent edit targets"。而 D4 分叉正好触及 tool registry(§3.1 的配置级单例 slot)。⇒ A22 的 slot 复制开销不是边缘情况,是**变体分叉的主路径**,W1 必须计入。

**M12 · processor 的 `split` 出口(§3.2 p.6 亲验)**:processor 五种出口之一是 "split (yield **multiple same-type events**, processed independently downstream)"。⇒ 一步内可产生多个下游事件流,我方抽引擎(W8)时的事件循环必须支持此分叉,不能假设一进一出。

**M13 · τ³ Telecom R8-R9 恢复的双重归因(§6.6 亲验,强化 H3)**:§6.6 正文(p.19)把恢复归因于 "a **structural edit** that replaced the conflicting reminder stack"(Planner 的结构性编辑);Figure 6(d) 面板逐字(p.20)却写 "R8~9 **Ensemble** recovers → 99.1%"。⇒ **论文对同一恢复事件给了两种互斥机制解释**,直接决定"变体隔离到底有几个实证数据点":若恢复真由 Ensemble 达成,则它是 GAIA 之外第二个数据点;若由结构编辑达成,变体隔离仍只有 GAIA 单点。**论文两说并存,无法判定** ⇒ 变体隔离的实证基础比"Table 5 两行终值"更薄弱。

**M14 · operational mirror 的理论来源是一篇博客(参考文献 [38] 亲验)**:"[38] Jiayi Weng. Learning beyond gradients. https://trinkle23897.github.io/... 2026. **Blog post**." ⇒ 论文核心理论框架(RL-symbolic mirror,§4.1)锚定在个人博客而非同行评议文献,是可攻的严谨性软肋(与我方变体池工作无直接关系,但可用于 related work 的理论定位段)。

**M15 · 变体隔离与共演化(§5)数据需求直接冲突(§5.3 亲验,修正 researcher 判断)**:researcher 曾判 §5 "与变体池零交集";主循环亲读后修正为**冲突**。cross-harness GRPO 把同一任务在不同 harness 版本下的**所有**轨迹归为一组(Eq. 2 `Gx = {τi | task(τi)=x} = ∪_k {τ~Agent(Mk,Hk,x)}`),靠 "harness identity dominates that variation" 估计 group-relative advantage。但此处 k 是**时间序列的串行版本** H₀…Hₜ,天然保证每任务跨轮有多版本轨迹;而变体池是**同一轮 K 个并发变体**,且变体隔离让每任务只在其路由到的**单一**变体上跑(§4.5 "tested only against tasks routed to k")。⇒ **变体隔离会饿死 GRPO 分组**:每任务每轮只有一个变体的轨迹,组内无跨策略对比,advantage 退化为纯采样噪声。论文 §5 全程假设单谱系,从未触及此冲突。**对当前计划无影响(M0/M1 模型冻结、不涉 GRPO);但这是 M3 共演化的已知障碍,也是一个 novel 贡献点——"变体隔离下的 GRPO 分组策略"(按 cluster 而非 task 分组?或每任务在多变体上保留少量对照 rollout?)论文完全空白。**

**M16 · task agent 能力地板 ⇒ M0 假阴性风险(附录 D.5 SWE 亲验)**:SWE-bench 上 Qwen3.5-9B 每个 lever 的 hit-rate 均塌到 ≈0.05,论文逐字 "a **capability floor** below which evolution cannot compound";同床 GPT-5.4 达 0.39–0.48。⇒ **内环模型若太弱,不是 headroom 小,而是根本演化不出有差异的变体**——变体间无互补性,M0 直接假阴性。DeepSeek V4-flash 应远强于 Qwen3.5-9B,但**必须在 6 题校准中验证 flash 在 GAIA 上的通过率显著高于能力地板**。⇒ 6 题校准新增第三目的(前两为:缓存命中率、单次尝试计费 token)。

**M17 · manifest 的 `predicted_impact` 三分类别直接绑定 pass@2(prompt 模板 p.32 亲验)**:manifest 模板逐字定义三类别为 `tasks_will_unlock: [ALL_FAIL → expect ≥1 rollout to pass]` / `tasks_will_stabilize: [PARTIAL_PASS → expect all rollouts to pass]` / `tasks_at_risk: [currently ≥1 pass → might regress]`。⇒ **`PARTIAL_PASS`(两次 rollout 一次通过)这个类别只在 pass@k(k≥2)下存在;单次评测下 `tasks_will_stabilize` 恒空,manifest 语义残缺。** 这是 pass@2 必需性的**第三个独立证据**(证据一:§6.1 pass@2 为 seesaw 提供二值信号;证据二:单次评测下噪声撑爆 fork;证据三:此条)。⇒ **pass@2(W17)从"应做"升为"机制定义的组成部分,不做则 manifest 契约不完整"。**

**M18 · counterfactual gate 的语境(prompt p.32 亲验)**:全文仍未定义,但语境明确——"Pure prompt-bucket candidates (no code asset) are exempt [from build-verify-iterate] -- **the counterfactual gate provides the equivalent smoke check**"。⇒ 它是纯 prompt 候选的等价冒烟检查(替代代码候选的两级验证)。仍为信息缺口,但确认其功能位置。

**M8 · 作者点名邀请的后续方向**(§7.3 p.21):"nothing guarantees this extends to longer horizons (**where variants may over-specialize**) or to task distributions whose **inter-task dependencies prevent clean variant separation**"。

**M9 · §7.7 五条限制**:无留出集评估(**"只报峰值"并入本条**,自认 selection bias + overfitting)/ 仅离散动作空间 / **闭源元 agent(开源权重模型作为 meta-agent "remain untested")** / 共同控制假设 / 床覆盖(SWE 55 题、τ³ 三域)。
⚠️ 第三条命中我方:计划用 DeepSeek V4-pro 作元 agent,正落在论文点名"未测"的区间。

**M10 · 共演化与变体池的关系 = 空**:§5 全节假设单一 harness 线性谱系,GRPO 分组的 k 是**迭代序号**而非变体编号。**多变体下 GRPO 怎么处理,论文完全没有回答。**

---

## 8. 变体隔离是全文实证最薄弱的部分

| 层 | 证据 |
|---|---|
| 公开 prompt | 三个 meta prompt **无任何 variant / routing / cluster-assignment 词汇** |
| 公开产物 schema | 运行目录**无 variant 维度**;`task_history.jsonl` = one line per (round, task) |
| 代码 | 官方 repo 全库核验:变体池零实现,真实现 = 单谱系爬山环 |
| 补充实验 | 附录 D 五个床的分析中 variant/ensemble/routing/pool/isolation **零出现** |
| 过程数据 | §6.6 无任何 per-variant 数据(无变体数曲线、无路由命中率、无各变体任务覆盖) |
| 论文自身定位 | §1 逐字 "**An optional** variant-isolation strategy" |

**全部实证 = Table 5 的两行终值。** 且 Table 8 声明每格 3 种子,而 Table 5/6 **只报单一数字、无方差**;§6.4 讨论误差时用的是解析二项标准误而非种子间方差 ⇒ **头条结果是否 n=1 单次运行,论文未说明**。

⇒ 我方 3 谱系设计在这一点上**比论文的消融更严谨**。

---

## 9. 统计自洽性审计(独立复算,6/6 全过)

| 论文声明 | 出处 | 复算 |
|---|---|---|
| 二项 95% CI = ±8.5%(n=103, p≈0.74) | p.17 | ±8.5% ✅ |
| 一个标准误 ≈ 3.3%(n=103) | p.18 | 3.3% ✅ |
| 单题翻转 ≈ 1.8%(n=55)/ ≈1.0%(n=103) | p.17 | 1.8% / 1.0% ✅ |
| 变体隔离 +13.6% | 摘要 | 87.4 − 73.8 = 13.6 ✅ |
| 107.8M 在 ~1,300 次调用内摊销 | p.22 | 82.9K/次 ✅ |

**算术无误;缺口在于未报方差(见 §8)。**

---

## 10. 成本计量的正确口径

**Table 5/7 的 "Total Tokens" 不是任务 agent 的计费 token。**

证伪链:
1. §7.5:"per-task token consumption drops by **∼25%**" + "**∼83K tokens saved per invocation**" ⇒ 单次任务计费 ≈ 83K/0.25 = **332K**;
2. 若 143.7M 是内环计费总量,单次尝试仅 46.5K,与上式差 **7.1 倍**;
3. p.10:"A single iteration on GAIA (103 tasks, pass@2) generates **∼10M tokens of raw traces**" ⇒ 15 轮 = 150M **≈ 143.7M**,严丝合缝。

⇒ **143.7M / 107.8M = 原始轨迹体量 / 元 agent 消耗**(二者数量级重合,因 Digester 正是吃这批轨迹)。真实内环计费量约 **1,026M**。
这也解释了 §6.3 的 token 归因为何成立:候选只在目标簇上评测 ⇒ 待压缩轨迹更少 ⇒ **元 agent** 消耗下降,与 A.2"同一评测集每轮重新评分"不矛盾。

**抵消因素:DeepSeek V4 缓存定价**(litellm 实查):flash 缓存读 $0.0028/M(未命中 $0.14,**50×**);pro 缓存读 $0.003625/M(未命中 $0.435,**120×**);**缓存写入免费**。agent 循环每步重发上下文、前缀高度稳定 ⇒ 命中率天然很高。

**M0 预算(3 谱系 × 103 题 × 15 轮 × pass@2 = 9,270 次尝试)**:

| 缓存命中率 | 内环(flash) | 合计(pro 元) | 合计(flash 元) |
|---|---|---|---|
| 0% | $470 | $676 | $536 |
| 70% | $174 | $310 | $218 |
| **85%** | **$111** | **$232** | **$150** |
| 90% | $90 | $206 | $127 |

⇒ **真实区间 $130–$310**。**缓存命中率是唯一大杠杆且必须实测** ⇒ 6 题校准跑的首要目的是测缓存命中率与单次尝试计费 token,而非测单价。

---

## 11. 我方设计的覆盖矩阵与遗漏清单

| # | 论文机制原子 | 位置 | 我方工作项 | 覆盖 |
|---|---|---|---|---|
| A1 | 变体池 `V_t ≤ K` | p.11 | W1 | ✅ |
| A2 | 按估计成功率路由 | p.11 | W2 | ✅ |
| A3 | 逐变体×逐簇成功率账本 | p.11 | W3 | ✅ |
| A4 | seesaw 按变体收窄 | p.11 | W4 | ✅ |
| A5 | 三路门 并入/fork/拒绝 | p.11 | W5 | ✅ |
| A6 | 池满退役最差 | p.11 | W6 | ✅ |
| A7 | 簇定义 | p.11/p.18 | W7 | ⚠️ 须裁决 + 消融 |
| A8 | 多候选 Evolver `K_t=4` | p.9/p.29 | W12 | ✅(论文优先后升为必做) |
| A9 | change manifest(6 字段 + `iterates_from`) | p.35-36/p.32 | W13 | ✅ |
| A10 | Critic + ship_ranking + 组合审计三规则 | p.10/p.33-34 | W16 | ✅ |
| A11 | 候选按 ranking 试门 | p.9 | W16 | ⚠️ H1 矛盾,须选一 |
| A12 | 目标变体指定 | p.11 | W14 | ✅(机制论文未给) |
| A13 | 早停 idle ≥ P=3 | p.9/p.15 | W15 | ✅ |
| A15 | seesaw 基准 = 全历史已解集 | p.8 | W21 | ✅ |
| A16 | pass@2 | p.15/p.29 | W17 | ✅ |
| A17 | 新 processor 附冒烟 + **Level-2 往返验证** | p.10/p.32 | 复用 replay 门 + **须补 Level-2** | ⚠️ |
| A19 | attribution_signature 核对 | p.36 | W19 | ✅ |
| A20 | 预测外回退台账 | p.30 | W20 | ✅ |
| A21 | bucket 声誉/hit_rate 台账 | p.30/p.33 | W22 | 🟡 可选 |
| **A22** | **slot 单例 ⇒ K 份 registry/workspace/sandbox** | **p.6** | **无** | ❌ **新缺** |
| **A23** | **Evolver 的 Level-2 往返验证** | **p.32** | **无** | ❌ **新缺** |
| **A24** | **Critic 组合审计的 hit_rate<0.4 禁令** | **p.33** | 并入 W16 | ⚠️ 须显式实现 |

**本轮新增两项遗漏**:A22(变体分叉在 tools 维度时的 slot 复制开销,论文成本分析也漏了)、A23(Evolver 的两级验证,尤其 Level-2)。

---

## 12. 原文替代式掌握卡

**方法一句话**:维护至多 K 个 harness 变体,按历史成功率把任务路由到变体;当一个编辑"有增有减"时不拒绝而是分叉出新变体,并把 seesaw 约束的检验范围收窄到该变体负责的任务。

**易混符号总表**:

| 符号 | 含义 | 易混于 |
|---|---|---|
| `K` | 变体池容量(**未赋值**) | `K_t`;Figure 3 的版本序号 k |
| `K_t` | Evolver 单轮候选数 = **4** | 池容量 K |
| `V_t` | 第 t 轮实际变体数 | — |
| `T_t` | trace store(全历史) | `T_k` |
| `T_k` | 路由给变体 k 的任务集 | `T_t` |
| bucket | **编辑类型**(prompt/tools/config/processor) | cluster |
| cluster | **任务/失败的分组**(未定义) | bucket |
| seesaw | 不许回退**历史**已解任务 | 逐轮 delta |

**运行主线(8 步)**:①路由全部任务到各变体 → ②Digester 压缩轨迹 → ③Planner 构造改造空间 → ④Evolver 产 4 个候选 + manifest(指定目标变体)→ ⑤Critic 排序 + 组合审计 → ⑥候选只在 `T_k` 上评测 → ⑦三路门(并入 / fork+可能退役 / 拒绝)→ ⑧更新账本;连续 3 轮无 ship 则早停。

**实现主线**:论文机制**零开源**;repo 可复用件 = `compute_attribution`(逐题 delta)/ `replay.py`(冒烟门)/ `canonicalize` / `EvolveValidator` / `HarnessConfig.copy` / `comparison.json`。

**迁移主线(三个立即动作)**:
1. **W17 pass@2** —— 缺它则 fork 在噪声上空转(正确性问题);但须知它同时掩盖亚阈值回退(M4);
2. **W21 seesaw 基准改全历史已解集** —— 否则复现的是更弱的约束;
3. **元 agent 能力检查** —— §7.7 点名开源权重模型作 meta-agent 未测,而我方计划用 DeepSeek V4-pro;能力不足会导致变体不分岔 ⇒ M0 假阴性。

**信息缺口**:见 §5 的八项 + §6 的十条矛盾 + §11 的 A22/A23。
