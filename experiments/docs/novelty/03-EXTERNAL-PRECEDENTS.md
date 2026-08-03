# 外部先例与效应量

> 2026-08-03，researcher 深扫。置信度 L0(传闻)~L4(全文逐字)。
> 用途：证明我们要做的每个小改动**都是成熟标准技术**，不需要为"novelty"辩护，只需要报效应量。
> 这正是"克制改进"的立论基础：我们不发明机制，我们把已在别处验证过的机制接到这个环上，并诚实报告增量。

---

## (a) 精英保留 / archive / 回滚 → 支撑 **N-01**

| 系统 | 做法 | 报告效应 | 置信 |
|---|---|---|---|
| **DGM** 2505.22954 (ICLR'26) | 保留不断增长的 agent **archive**，新个体从 archive 采样变异 ⇒ 任何更早更好的个体永远可达 = 隐式回滚 | SWE-bench **20.0→50.0%**，Polyglot **14.2→30.7%** | L3 |
| ↑ **消融（承重）** | 去掉开放式 archive 探索 | *"poor modifications trap the system in degraded states with **no recovery path**"* | L2 |
| **GEPA** 2507.19457 (ICLR'26 Oral) | 维护候选 prompt 的 **Pareto 前沿**（而非单一最优）再采样，防谱系坍缩 | 胜 GRPO **+6% 均值 / +20% 最大，rollout 少 35×**；胜 MIPROv2 **>10%** | L3 |
| **AFlow** 2410.10762 (ICLR'25) | MCTS 搜工作流代码，树上保留 best-so-far 节点 | 胜手工方法 **+5.7%**，胜既有自动方法 **+19.5%**；小模型以 **4.55% 成本**胜 GPT-4o | L3 |
| FunSearch(Nature'23) / ADAS 2408.08435 / SICA 2504.15228 | 均保留程序或 agent archive | — | L1 |

**裁定：标准技术，直接用。** DGM 的消融是本条最有力的外证——**移除 archive 会导致不可恢复的退化**，与论文 Fig.6(f) 的 *"Rollback fails; final 49.5%"* 同构。

⚠ 但见 `04-PROBE-RESULTS.md`：在我们这个噪声床上，**朴素 best-so-far 是噪声追逐**。可辩护的形态是 **δ-阈值冠军** 或 **GEPA 式 Pareto 前沿**（保留一组而非一个），而不是裸取历史最高分。

---

## (b) bandit 路由 → 支撑 **N-05**

| 来源 | 要点 | 效应 | 置信 |
|---|---|---|---|
| **UCB1** (Auer et al. 2002) / Thompson / ε-greedy | 教科书结论：**纯贪心 argmax 可以永久锁死在次优臂上（线性 regret），并饿死更好的臂**；UCB1 对数 regret | — | L2 |
| **PILOT** 2508.21141 (EMNLP'25 Findings) | LinUCB 上下文 bandit 做 LLM 路由，带偏好先验 | **93% GPT-4 质量 @ 25% 成本**；胜 LinUCB / Epoch-Greedy / random | L3 |
| ↑ **关键警告** | 探索参数 α 呈**倒 U 形**：太小 → 欠探索/臂饿死；太大 → 浪费 | | L3 |
| **RouteLLM** 2406.18665 (ICLR'25) | 训练式路由（非 bandit），但路由收益的最干净证据 | MT-Bench **成本 −85% @ 95% GPT-4 质量** | L3 |

**裁定：bandit 机制本身是标准技术，直接用**（ε-greedy / UCB 加在 argmax 上是一行改动）。但**别声称某个路由器最优**——LLM 路由的最佳设计仍是活跃研究。
**对我方的直接含义**：`epsilon=0.0` 是教科书上的失败设定；PILOT 的倒 U 说明**不能盲目调大**，要扫。

---

## (c) 用统计检验替代二值门 → 支撑 **N-02** 与 N-01 的 δ 阈值

| 来源 | 要点 | 置信 |
|---|---|---|
| **irace / Iterated F-Race** (López-Ibáñez et al. 2016, *ORP*) | 算法配置领域的事实标准工具：候选在实例上"赛跑"，用**统计检验（Friedman / 配对 t）淘汰**，而不是单次跑 | L2 |
| **Hoeffding Races** (Maron & Moore, NeurIPS 1993) | 用 Hoeffding 置信界提前淘汰不可能翻盘的候选，把评测预算集中在接近的对手上 | L2（加速倍数 [未核验]） |
| **Heidrich-Meisner & Igel** (ICML 2009) | 在 CMA-ES 里加 Hoeffding / 经验 Bernstein race，**在噪声适应度下判定两个候选何时可靠可排序**——每个只评到"刚好够排序" | L2 |
| Beta-Bernoulli 后验 / SPRT (Wald 1945) | 把逐题 pass/fail 建模成 Beta-Bernoulli 并在后验上接受 | L1 |

**裁定：标准技术，直接用。** 噪声适应度下的 racing / 序贯接受是成熟做法，**严格低于单次二值门的风险**。
**这是本目录理论支架最硬的一条**：论文的 seesaw 是"单次二值 pass/fail"，而我们床的同配置重测 SD≈4.6pp、23% 题会翻转 ⇒ 单次二值门在这个噪声水平下本就不可靠。irace/Hoeffding races 给了现成的替代范式。

---

## (d) 新生保护 / niching / island → 支撑 **N-06**

| 来源 | 要点 | 置信 |
|---|---|---|
| **FunSearch** (Nature 2023) ★最贴 | 程序数据库是 **island model**：多个子种群半独立演化，**定期丢弃最差的岛并用好岛的幸存者重播种** | L2 |
| **MAP-Elites** 1504.04909 | 按行为描述子分格，**每格各留自己的 elite** ⇒ 全局平庸但局部最优的个体不会被早期全局赢家挤掉 | L2 |
| **Novelty Search** (Lehman & Stanley 2011) | 按行为新颖度而非适应度选择；在贪心适应度会陷入死胡同的欺骗性任务上**显著优于目标导向搜索** | L2 |
| fitness sharing / deterministic crowding (Mahfoud, Goldberg) | 教科书 niching 算子 | L1 |

**裁定：标准技术，直接用。** FunSearch 的 island + 重播种已在**一个 LLM 程序演化环里**验证过，可直接移植。
**对我方的直接含义**：`04-PROBE-RESULTS.md` 实测 s1k8b103 有 7 次 fork、零 retire，但 V0 覆盖 99/103 题、其余全是短命挑战者 ⇒ **正是 island/niching 要解决的那个坍缩**。

---

## (e) 算子/编辑类型的自适应选择 → 支撑 **N-07**

| 来源 | 要点 | 置信 |
|---|---|---|
| **DaCosta et al.** (GECCO 2008) | 把"选哪个变异算子"建模成 **MAB**（信用分配 + 自适应规则），用 dynamic MAB 跟踪非平稳的算子价值 | L2 |
| **Fialho et al.** (PPSN 2008 及 2010 分析) ★**正中论文病理** | **均值/概率匹配式的信用分配会系统性地少分配给"罕见但高回报"的算子**；改用**极值(最好近期结果)或秩**的信用规则 | L2 |
| **EoH** 2401.02051 (ICML'24 Oral) | LLM 启发式演化用**固定的 5 算子组合**(E1/E2 探索 + M1/M2/M3 修改)维持编辑类型多样性；在线装箱上胜 FunSearch 与手工启发式，LLM 查询远少 | L2/L3 |

**裁定：标准技术，但信用规则要设计一下。** MAB-over-operators 与编辑类型配额是标准低风险；**唯一需要选择的是信用规则——用极值/秩，不要用均值**（Fialho）。

★ **这条与论文的病理逐字对上**：论文 Fig.6(h) 显示 GAIA 上 *"R1~9 0 tools ships"* 然后 *"R10 WikiTextFetch; **+4.9pp**"*（全文最大单轮增益，来自九轮一次没发过的编辑类型）。这**就是** Fialho 描述的"罕见但高回报的算子被均值信用饿死"，而修法是文献里已定型的。

---

## 规划用一句话

五个机制**全部是成熟、低风险、可 drop-in 的标准技术，且都有已发表的效应量**。只有两处需要设计决策（不是辩护）：
1. **(b) 探索常数要扫**（PILOT 的倒 U 形）；
2. **(e) 信用规则用极值/秩，不要用均值**（Fialho 记录的失败模式及其定型修法）。

外加本目录自己的一条：
3. **(a) 精英保留必须带噪声阈值**（`04-PROBE-RESULTS.md` 实测：裸 best-so-far 在本床上是噪声追逐）。
