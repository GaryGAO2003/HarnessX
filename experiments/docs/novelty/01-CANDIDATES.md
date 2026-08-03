# 克制改进候选清单

> 🔴 **失效通告（2026-08-03 晚）：本文关于 N-06 / N-08 / N-10 的一切表述已被 `06-POOL-PATHOLOGY-VERDICT.md` 取代，以那一份为准。**
> 具体：N-10 的机理不是"僵尸变体"而是**目标选择与路由冻结的时序错配**，修法也不是 5–10 行；**N-06 已毙掉**（单轮标定假象、零决策后果）；N-08 的"池永不填满""祖先永久死亡"两句**不成立**。
> 本文其余条目（N-01 / N-02′ / N-03 / N-04 / N-05 / N-07 / N-09）仍然有效。

> 2026-08-03。**v2 — 已按 `04-PROBE-RESULTS.md` 的实测与 `03-EXTERNAL-PRECEDENTS.md` 的外证修订；v1 的两条判断被自己的探针推翻，见各条 ⚠ 标注。**
> 论文引句来自本地 `docs/assets/paper/HarnessX_Tech_Report.pdf` 的 pdftotext 抽取，主循环逐字亲核（L4）。
> 代码锚标 ✔ 为主循环亲验；标 ⚠ 来自子代理审计，**用前须复核 file:line**。我方实测标 [我方]。

## 判据

一条候选要进执行，须同时满足：
1. **改动小**——单旗标、或 <150 行、或纯离线分析脚本；
2. **有论文锚**——论文自己记录了这个病理，或自己点名了这个药方；
3. **有外部先例**——机制是成熟标准技术（见 `03`），我们不为 novelty 辩护，只报增量；
4. **可读数**——在 SD≈4.6pp 的床上能被检出（见 `02`：配对 + 同窗交错，+3pp 需 ~13 配对轮）；
5. **带修复**——不是纯测量（导师红线）。

## 全局前置条件（先做，否则下面一半做不了）

**落 attempt 粒度日志**（每 attempt 的 pass/fail、分数、seed）。`02 §2` 的免费重放全部依赖它。目前 `active_pool_measurements` 存的是 `[succ, att]` 计数对 ⚠——够做 all-k，但要确认是否逐轮而非累计。

---

# Tier 0 — 零跑动，纯离线重放

同一批已落盘 rollout 重新结算。**只在"不改变 agent 后续生成/观测"时精确有效**（`02 §2` 的分界线），越界者必须真跑。

## N-01 冠军保留 —— ⚠ **已从"最稳"降级为"必须带阈值才成立"**

**病理（论文自证）**
- Algorithm 1（p.9）**没有任何 revert 分支**：每轮只有接受或拒绝，已上线的编辑永不复查。
- 但 §7.6（p.22）写 *"every shipped edit carries a manifest and **a rollback target**"*；Fig.4 注写 *"BudgetDeadlineProc **shipped-and-reverted twice**"*；Fig.6(f) 写 **"Rollback fails; final 49.5% (−24.3pp)"**。
  ⇒ **回滚在正文里发生过、在旗舰失败案例里失效了、在算法里根本不存在。**
- Fig.4 图例已在画 **Best-so-far**，但部署返回 last。

**现状（代码）**：`experiments/variant_pool/` 内**无** best-so-far / rollback / champion 实现 ✔。现成可复用件：`recipe/gaia_evolver/run.py:1544` 有 *"Best-so-far gating kernel"* ✔。

**⚠ 实测结果（`04-PROBE-RESULTS.md`）**
16 个 ≥3 轮的 run：mean(peak−final) = **+5.78pp**，8/16 的 peak>final（这 8 个均值 +11.6pp）。**但 s1k8b103 的 +5.8pp 低于纯噪声期望值（15 轮、SD≈4.3pp ⇒ E[peak−final] ≈ 7–8pp）。**
⇒ **朴素 best-so-far 是噪声追逐，不构成真实提升。**

**改成可辩护的版本**
- **δ-阈值冠军**：只有超出噪声带（δ ≈ 1.96×SE ≈ 8.5pp，或用平滑分数）才换冠军；
- 或 **GEPA 式 Pareto 前沿**：保留一组而非一个（`03(a)`）。

**外证**：**DGM 2505.22954 的消融**——去掉 archive 后 *"poor modifications trap the system in degraded states with no recovery path"*；GEPA 2507.19457 用 Pareto 前沿防谱系坍缩（`03(a)`，L2/L3）。

**重放效度**：纯读出策略（返回冠军快照）**精确有效**；"轮末回滚"会改变后续轨迹 ⇒ 只能给一步反事实，标 *indicative*。

**怎么写**：预期收益**很小**，符合"克制"，但必须诚实报——不能拿 +5.8pp 当战果。它的真正价值是**一个修正**：论文把稳定性归功于变体隔离，而其中相当部分只是冠军选择策略与峰选择偏差。

---

## N-02 all-k tripwire（分级 seesaw）

**病理（论文点名了药方却没做）**
- §6.1（p.15）自认 pass@2 *"at the cost of **masking sub-threshold success-probability drift**"*；
- §6.6（p.19）*"This regression evaded the seesaw constraint because pass@2 registers only per-task binary flips, not sub-threshold coupling."*；
- Fig.4 注（p.15）**逐字点名药方**：*"the stricter **all-k** channel is the needed tripwire"*；
- §7.2（p.21）*"…necessary for detecting pathologies, **but not sufficient for preventing them**."*

**现状**：分级信号已在盘上（`[succ, att]` 原始对 ⚠、`candidate_gate_measurements` ⚠）；`reporting.py:720` 已实现 `pass@k − pass@1` 并注明 `(masking gap, §7.1)` ✔ —— **但只进报表，没接进门控**。

**外证**：irace / Hoeffding races / Heidrich-Meisner CMA-ES racing——**噪声适应度下用统计检验替代单次二值判定是成熟范式**（`03(c)`）。我方床同配置重测就翻 23% 的题 ⇒ 单次二值门在这个噪声水平下本就不可靠。

**最小改动**：seesaw 从 `any-k` 改双通道，`2/2→1/2` 记软回退，累计超阈即拒。旗标化，默认关。

**读数**：离线重放 —— "all-k 下哪些 ship 会被拦"是**精确计数，不是估计**（`02 §2`）。零成本先出诊断，再决定是否真跑。

**风险**：门变严 ⇒ ship 更少 ⇒ 在已有"改善旱灾"的床上可能一轮都发不出。**必须先看拦截率。**

---

## N-03 patience 重放

论文 verbatim `P=3`（§6.1 p.15）。[我方] p3 停在 R10（60.2%）而 p16 到 74.8% ⇒ 论文早停参数在高噪声床上过早熄火。`--patience` 与 `deep_metrics.py` metric D 的离线重放现成 ⚠。零成本。属 M1 复现审计的一部分，不算独立贡献但**免费**，且给其他候选定轮数。

## N-04 actionability 阈值 α 反事实

α 是 Algorithm 1 的输入（p.9 `threshold α`、`if a_t < α`），**全文从未给出取值** ✔。它控制"整轮跳过"，是隐藏强超参。`actionability` 已逐轮落盘但 `experiments/analysis/` 零消费者 ⚠。离线重放 α∈{0.3,0.5,0.7,1.0}，算"省了多少轮 vs 漏了多少 ship"。零成本，同时补上论文一个规格洞。

---

# Tier 1 — 单旗标或 <100 行，需配对真跑验证

## N-05 给路由器一点探索（ε>0 / UCB）

**病理**：论文 §4.5（p.11）*"routing each task to the variant with the highest estimated success rate on that task's cluster"* —— **纯贪心 argmax、无探索、估计量未定义、冷启动未定义**。
代码证实：`router.py` 默认 `epsilon=0.0`、`routing_mode="cluster"`、`cluster_mode="routed"` ✔，而 `routed` 的簇定义是 `pool.carrier_of(task_id)` ✔——**簇就是"这个变体已经带着的那批任务"**，自指。注释坦承 *"the paper publishes no clustering algorithm"* ✔。
s1k8b103 的 lock 逐字确认 `epsilon=0.0`、`cluster_source=gaia_level`、`estimator=laplace` ✔。

**外证**：UCB1(Auer 2002) 教科书结论——**纯贪心可永久锁死次优臂并饿死更好的臂**；PILOT 2508.21141 实测 LinUCB 路由 93% GPT-4 质量 @25% 成本。
⚠ **PILOT 同时报告探索参数呈倒 U 形**：太小欠探索、太大浪费 ⇒ **要扫，不能盲目调大**（`03(b)`）。

**最小改动**：`--epsilon` 调 0.05–0.1 ⚠，或估计量换 UCB/Thompson。

**先做的零成本步**：Ŝ 校准（先验 vs 实现的 Brier/可靠性），从 `pool_states.json` 离线算。**若校准本来就好，这条不必真跑，省一次跑。**

**风险**：ε>0 主动把任务送给较差变体 ⇒ **短期掉分**。在只求"好一点"的目标下这是负分项。

---

## N-06 新生变体保护期 —— ⬆ **实测把它顶成 Tier 1 首选**

**病理**：论文 §4.5（p.11）*"retiring the lowest-performing variant if the pool is full"* —— 退役标准未定义，且与贪心路由耦合：**新 fork 的变体还没被路由到过，估计值最弱**。

**⭐ 实测（`04-PROBE-RESULTS.md`，s1k8b103，15 轮）**
- **7 次 fork，零 retire 事件**；
- 累计覆盖 `V0=99, V1=69, V2=36, V3=64, V4=15, V5=64, V6=64, V7=64`（103 题床）；
- **V0 每一轮都在册、覆盖 99/103**；其余全是短命挑战者（V1 只在 R1/R2/R4，V4 累计仅 15 题）；
- 它们不是被退役，而是**拿不到任务**——ε=0 的贪心路由下在位者一旦领先就不再让位。

⇒ **名义 K=8，任一时刻有效多样性 ≈2–4，持久多样性 ≈1。**

**外证**：**FunSearch (Nature 2023) 的 island model**——多子种群半独立演化，定期丢弃最差岛并从好岛幸存者**重播种**；这是**已在一个 LLM 程序演化环里验证过**的防坍缩机制。MAP-Elites 的每格各留 elite 同理（`03(d)`）。

**最小改动**：退役/路由规则加 **grace rounds**——新生变体前 n 轮保底分到最小任务份额。<50 行。

**风险**：低。最坏无事发生。

---

## N-07 编辑类型配额 —— ⬆ **外证正中论文病理**

**病理（论文自证，且给了效应量）**
- §6.6（p.19）：*"The R7 Critic **flagged** the concentration risk ('All 5 prior ships occupy the same bucket: [prompt, processor]') **but still approved** the edit for shipping"* —— **检出了，没有否决权。**
- Fig.6(h)（p.19）：*"R1~9 **0 tools ships**; Blocked-source 39%"* → *"R10 WikiTextFetch; **+4.9pp**"* —— **全文最大单轮增益，来自系统九轮一次没发过的编辑类型。**
- Fig.6(i)：WebShop 同样 7 轮 prompt-locked。

**★ 外证逐字对上**：Fialho et al.(PPSN 2008 及后续分析) 记录的正是这个失败模式——**均值/概率匹配式的信用分配会系统性地少分配给"罕见但高回报"的算子**；定型修法是**用极值(最好近期结果)或秩的信用规则，而不是均值**。DaCosta(GECCO 2008) 把算子选择建模成 MAB；EoH 2401.02051(ICML'24 Oral) 用固定 5 算子组合维持编辑类型多样性（`03(e)`）。

**最小改动**：把 Critic 已在算的 bucket 集中度变成**硬约束**——连续 n 轮同桶后，下一轮 Planner 必须提出至少一个异桶候选。<100 行，或走现有 `--target-strategy` / `--manifest-mode` ⚠。
**设计决策（唯一需要选的）**：信用规则用**极值/秩**，不用均值。

**读数**：先离线画"编辑类型分布 × 各类型平均增益"（`pipeline_audit.plan.briefs[].buckets` ⚠）。若我方也呈现"tool 类稀少但增益高"，这条即有据。

**风险**：强制异桶可能逼出低质候选。配额设为"必须**提出**"而非"必须**上线**"可缓解。

---

# Tier 2 — 结构性但仍克制

## N-08 池缺乏持久多样性 —— ⚠ **v1 的"容量被簇数封顶"已被自己的探针证伪**

**v1 错误主张**：从论文 §4.5 的簇级 argmax 推出"有效变体数 ≤ 簇数 = 3，K=8 结构不可达"。
**实测反证**：s1k8b103 **确实创建了 8 个变体**，逐轮在册数最高到 **4 > 3**（`04-PROBE-RESULTS.md`）。**该推断作废。**

**修订后的主张（有实测支撑）**：**池创建得出多样性，但留不住**——V0 是永久在位者（99/103 覆盖、16 轮全在册），7 个 fork 全是短命挑战者，零 retire 事件。⇒ **持久多样性 ≈1。**

**这关系到 N1 主张的真假**：N1 押的是"**持久**演化池"。按现有数据，我们的池在任一时刻更像"一个在位者 + 1–3 个轮换挑战者"。**这个事实必须在 N1 立论前查清，否则主张的前件不成立。**

**修法**：即 N-06（新生保护）+ N-05（探索）+ 更细的簇源 `--cluster-source` ⚠。

**未决（写论文前必须查）**：`active_pool_measurements` 的键到底是"本轮被路由到任务的变体"还是"本轮有任何测量（含门探针）的变体"——查 `run_variant_pool.py` 的写入点。

---

## N-09 held-out 子集 + 未见任务路由

**病理**
- §7.7（p.23）自认 *"No held-out evaluation. … the numbers carry both selection bias and potential overfitting."*
- 论文没说这**偏袒哪一臂**：Ensemble 有 K 变体 × 15 轮的逐任务选择，过拟合容量结构性高于 Global。
- §7.5（p.22）部署规则逐字：*"tasks outside the evolution set are routed to **the variant with the highest overall success rate on the evolution set**"* ⇒ **未见任务上集成退化成单个最优变体，K 个变体的价值在演化集外零证据。**

**最小改动**：切 held-out 子集，末轮各变体全跑一遍；比 ①论文部署规则（最优单变体）②簇相似度路由 ③oracle 上界。
**成本**：一次末轮评测（非完整演化），约 1/3 轮。
**为什么值得**：只要 ② > ①，就是一个干净的小胜，且填的是论文自认的头号局限。
**风险**：held-out 上池可能不占优（负结果）。但这给 N-08/N1 的"持久池"主张一个真假判定，且负结果在双 workshop 出口下可写。

---

# 不做（避雷）

| 已排除 | 原因 |
|---|---|
| 算力 / token 配平臂 | Jul-31 用户已裁，改全臂成本并报 |
| E0 oracle 分解注入 | Jul-31 用户已裁 |
| τ³ 任务数未给 / 聚合口径 / 落选策略静默丢弃 | 真缺陷，但属纯 benchmark 批判体裁，只进 threats |
| co-evolution 补 RL-only 臂 | 我方不做模型训练 |
| 加种子、把 pass@2 加成 pass@k | `02 §3`：seed 已顶满且失效，主读数固定；预算全投配对轮 |
| 新架构 / 引导分化 / QD 全套 / Shapley | 超出"克制"范围，二期 |

---

# 执行顺序

**第 0 步（先决）**：落 attempt 粒度日志；查清 `active_pool_measurements` 键义。

**第 1 步（一天内，全离线零成本）**
N-03 patience 重放 · N-04 α 反事实 · N-02 拦截率 · N-05 的 Ŝ 校准 · N-07 的编辑类型×增益分布 · N-08 的键义确认。
**其中任意一条为负都能省掉一次真跑。**

**第 2 步（~9h）**：A/A 空校准 5 轮，测出真实 σ_D，重解 `02 §3` 的功效表。

**第 3 步**：据第 1 步结果挑 **1–2 条**进配对真跑。当前排序：**N-06（实测支撑最强、风险最低）> N-07（外证最对症）> N-09（填论文头号局限）> N-05（有短期掉分风险）**。

**第 4 步**：N-01 无论如何都要写进论文——不是当作战果，而是当作**对论文稳定性叙事的修正**（峰选择偏差 + 冠军策略混淆）。
