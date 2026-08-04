# PD-TABLE — HarnessX 论文缺陷主表

> 记号见 `README.md`。🔴论文自认 / 🟠文本可查 / 🟡我方推导 · 📄正文 / 📎附录 / ⚡互相打架
> 页码来自 `docs/assets/paper/HarnessX_Tech_Report.pdf`(43 页版)。

---

## A 组 · 论文自认的局限(🔴 最硬,可直接引)

| PD | 缺陷 | 位置 | 论文原话要点 | 我们能做什么 | 状态 |
|---|---|---|---|---|---|
| **PD-01** | **无 held-out 评估** | 📄 §7.7 第1条 p.23 | 「所有报告的增益都在**用于演化的同一任务集**上测。由于我们报 peak 且在适应集本身上评估,这些数字**同时带着选择偏差和潜在过拟合**」 | 切 held-out,比三种规则:论文的部署规则 / 簇相似路由 / oracle。只要「簇相似路由 > 最优单变体」就是干净小胜;反向也可写(negative result) | 🥇 **N-09 已评估为「仍有效」** |
| **PD-02** | 只测离散动作空间 | 📄 §7.7-2 | 无连续控制/机器人 | — | 不攻(超范围) |
| **PD-03** | meta-agent 必须闭源强模型 | 📄 §7.7-3 | 开源权重模型「remain untested as meta-agents」 | 我方用 DeepSeek-V4 跑 = **一次未被论文覆盖的 meta-agent 能力档实测**。可写成"论文未测的档位" | 🟢 我方数据天然覆盖 |
| **PD-04** | 协同演化要求联合控制 | 📄 §7.7-4 | 共享 replay buffer「impractical without cross-team coordination」 | — | 不攻(不做 RL) |
| **PD-05** | benchmark 覆盖窄 | 📄 §7.7-5 | SWE-bench **仅 55 题子样本**;τ³ 仅 3 域 | 第二床(BrowseComp)可作证据幅度 | 🥉 |
| **PD-06** | **RL 镜像只是设计启发,不是形式框架** | 📄 §7.3 p.21 | 「a design heuristic, **not a formal framework**」;无收敛保证;「nothing guarantees this extends to longer horizons (where **variants may over-specialize**)」;三病理「representative, not exhaustive」 | 「变体过度特化」是论文自己点名的未测风险 —— 我方的分化研究正好落在这个洞里 | 🥈 |
| **PD-07** | **四阶段管线不提升准确率** | 📄 §6.4 + §7.4 p.22 | 自己的消融:相对单 agent evolver 在 **1 SE 内**;只有 ~12% token 效率 + 可审计性。§7.4:「efficiency … and auditability **rather than measurable accuracy improvement**」 | 论文标题卖点被自己消融否掉。我方任何"改 meta-agent 架构"的方案都要绕开这个已知无效面 | 🟢 已知,规避用 |
| **PD-08** | 结构化 trace 必要但不充分 | 📄 §7.2 | 耦合只在**损害发生后**才被记录 | 接"运行时产物消费审计"(我方 silent-drop 的一手实证) | 🥈 |
| **PD-09** | 单任务推理成本可能上升 | 📄 §7.5 | ALFWorld **+60%** | 成本并报纪律的论文侧依据 | 🟢 |
| **PD-10** | 人在环 gating 从未行使 | 📄 §7.6 | 「not exercised in our automated experiments」;并承认 per-edit gating 有**结构性极限**(sub-threshold 累积) | — | 🥉 |
| **PD-11** | 无 Future Work 章节 | 📄 §7→§8 | 已核实:§7 止于 7.7 → §8 Conclusion,**全文无前瞻章节** | ⚠️ 之前引用过的"§7.7 分解工单"是 **confabulation**,已作废 | ⛔ **勘误已记** |

---

## B 组 · ⚡ 正文自己打自己

| PD | 缺陷 | 位置 | 详情 | 我们能做什么 | 状态 |
|---|---|---|---|---|---|
| **PD-12** | **Figure 4 的标注否定 Table 4 的口径** | 📄 Fig.4 vs Table 4 p.16 | 作者在 Figure 4(GAIA/Sonnet-4.6)上**自己标注**:「The treadmill … **Net durable gain over 9 rounds ≈ 0, all inside noise**」;「Apparent peak, **noise-inflated**: identical-config replay (R12) fell to 81 → R11 真实水平 ≈83–84,与 R10 统计上是**同一个平台,不是第二次胜利**」;「**Metric masking.** 头条指标掩盖 channel 级退化」。而 Table 4 的口径正是 **"Evolved = peak accuracy achieved"** | **论文自己承认 peak 是噪声膨胀的**,这是 PD-01 的直接放大器。我方的 epoch 聚合口径(P6 修正)正是它的无偏替代 | 🔴 **最硬的一条** |
| **PD-13** | **头条数字被自己的附录否认** | ⚡ Table 4 p.16 vs 📎 App D.5 p.42 | Table 4 头条 **SWE-Qwen +18.2%**(23.6→41.8, best round 2);Appendix D.5 说同一个 run:「every lever collapses to near-zero (prompt 0.05, config 0.05, processor 0.06) … **yields only noise on Qwen3.5 (peak 42%, zero durable gains)**」 | 一个具体的「peak 选择制造了一个自家分析不认的头条」实例。可直接引 | 🟠 **可直接引** |

---

## C 组 · ⚡ 正文 ↔ 附录矛盾

| PD | 缺陷 | 正文说 | 附录说 | 后果 | 状态 |
|---|---|---|---|---|---|
| **PD-14** | **`cluster(task)` 完全未定义 —— 最大的洞** | 📄 §4.5 p.11 用「that task's cluster」路由,但 "cluster" 全文出现 **7 次,没有一次为路由定义它**。无算法、无特征空间、无 k | 📎 **App D p.37–42 有 failure clusters**(blocked-source 39% / reasoning 33% 等),但论文**从未说它们是路由簇**;§6.3 提到 "Domain-aware clustering / Task-level tournament" 只是 **pilot 规模**(30–40 题 ≤8 轮),明写「lack sufficient rounds and tasks for statistically meaningful comparison」 | **决定哪 52 道题归哪个变体的机制,完全由实现者定义。**我方实测:有效池 = `min(K, 簇数)`,终局负载 `[52,39,12,0,0,0,0,0]`,基尼 0.22→0.78 ⇒ **8 个变体里 5 个算术上注定闲置,且因「候选只在被路由任务上评测」而永不可改进** | 🥇 **第一攻击面**<br>⚠️ 硬约束:反事实重放 难度3簇 **3/8** → 能力11簇 **2/8(更差)** → **细簇+探索 7.8/8**,细簇**必须配探索** |
| **PD-15** | **"improves" 从未定义(阈值+时点都没有)** | 📄 §4.5 只说「the edit improves some tasks without regressing any」 | 📎 唯一线索是 manifest 字段 `tasks_will_unlock: [ALL_FAIL → expect ≥1 rollout to pass]`(p.32),但 **"ALL_FAIL" 本身没标时点**(本轮?历来?);App C 的 worked example(p.37)报「five of the seven tasks flipped to pass」也不消歧基线 | 我方取全史读法 ⇒ 变体可改进池单调收缩到 0 ⇒ 强制 REJECT 的**绝育棘轮**。实测:V0 可改进池 16(R1)→3(R2)→1(R8)→**0(R12)**;换 `last_round` 基线后 **18/44(41%)** 决策翻转 | 🥈 🟡实测已有 |
| **PD-16** | 目标变体 `k` 如何选 —— 未定义 | 📄 §4.5「a candidate targeting variant k is tested only against tasks routed to k」,但从不说 Evolver 怎么**选**这个 k | — | 我方 `worst_first` 是纯自填。实测后果:15 轮目标全是 V0,承载 62% 床的 V1 **零次**被演化 | 🥈 |
| **PD-17** | 单发 vs 多发 —— 操作语义矛盾 | 📄 Algorithm 1 p.9:首个过门即 `break`,**恰好 ship 一个** | 📎 Critic prompt p.33:「pick the single candidate **(or multiple bucket-disjoint candidates)**」;p.34 明写「**Multi-ship:** Stage 4 ships **every** listed candidate in order but skips any whose bucket was already claimed」 | 两种语义产生**不同的池轨迹和成本**,不可同为操作语义。我方选单发,多发留作消融(未实现) | 🟠 待预算 |
| **PD-18** | `Kt` 每轮候选数 —— 三种规格并存 | 📄 Algorithm 1 允许 **0** | 📎 Table 8 p.29 固定 **Kt = 4**;Evolver prompt p.31:「**You decide how many candidates (K ≥ 1)**」 | 三处规格互不相容 | 🟠 |
| **PD-19** | 冷启动 —— 完全未定义 | 📄 路由要求「highest estimated success rate … across **prior rounds**」,而 round 0–1 不存在 prior。字符串 "cold" 全文出现 **0 次** | 📎 A.4 p.29 只说 round-0 是「a competent composed harness augmented with the benchmark-specific tool registry」 | 池如何播种(从 V=1 开始?第一次簇分配怎么做?)全空。这直接卡住 PD-14 的细簇方案(细簇下冷启动更严重) | 🥈 与 PD-14 耦合 |
| **PD-20** | 门关卡枚举不一致 | 📄 §4.3:manifest → config → build/smoke → seesaw | 📎 Fig.6 SWE 面板:「Gates check **replay / novelty / structure** — not pass rate」 | 两张清单对不上 | 🟠 |
| **PD-21** | seesaw ledger 作用域含糊 + 两套回退账 | 📄 §4.1 p.8:regress none「recorded in `Tt`」(全局 trace store);§4.5 却把 seesaw 限定 per-variant | 📎 E.1 p.43 的 `regressions.md`(「tasks worsened vs R‹n-1›」)是 **Planner 的诊断产物,明确不是 gate ledger**(prompt p.30/33 只要求 Critic 检查 Evolver 是否**处理了**它) | **论文同时跑着两套回退账(全局 gate + 逐轮诊断),从不 reconcile。**我方代码级发现:改进侧读 per-variant cell,回退侧读全局 `is_ever_solved` ⇒ **击穿 §4.5 白纸黑字的「improvements to one cluster cannot regress another」** | 🟠+🟡 K≥2 才显形,至今零 fork 未演练 |

---

## D 组 · 📎 只在附录、正文缺失

| PD | 缺陷 | 详情 | 后果 | 状态 |
|---|---|---|---|---|
| **PD-22** | **FORK 根本不在算法里** | 📄 Algorithm 1 和 Fig.2 都是**严格二元**(ship one / no-op);**FORK 只在 §4.5 的散文里**,从未回填算法 | 「**只读算法的人永远不会 fork**」—— 旗舰的 Ensemble 机制缺席形式化。我方已修的 P6(fork 轮计分含 100% 保送块)就是这个形式化空白的下游后果:修正后 s1k8b103 的 `peak−final` 从 **+5.83pp → 与 0 不可区分** | 🟠 ✅ **P6 口径我方已修** |
| **PD-23** | **真正的决策策略在 prompt 里,不在方法里** | 决定 ship 与否的具体规则只出现在 📎 App B:Critic「**do NOT ship a lever shipped in ≥2 of last 3 rounds with cumulative hit_rate < 0.4**」(p.33)、Planner「hit-rate only counts predicted-task improvements」(p.30)、reputation/scoreboard 信号。📄 §4 方法章一字未提 | **只读 §4 的人复现不出这个系统。**这是"方法在正文、策略在提示词"的结构性问题 | 🟠 可直接引 |
| **PD-24** | 论文契约不在作者自己的开源 repo 里 | 📎 Table 9 的 change-manifest schema(`capability_evidence` / `attribution_signature`)与 L2 往返证据 gate,在开源 `harnessx` repo 里**不存在**;repo 的 meta-agent 写的是 journal 词汇(`levers`/`predicted_affected`) | repo 里 stage2/3 **恒过**、stage4 默认只查是否申报,真正拦截只剩 manifest 完整 + seesaw。我方真跑 `runs/forkprobe_11`:**12/12 候选全死在 `PIPELINE_PROPOSAL`,fork 门槛根本没被执行到** | 🟠+🟡 **代码级+实测,很硬** |

---

## E 组 · 方法论缺陷(审稿人视角)

| PD | 缺陷 | 详情 | 我们能做什么 | 状态 |
|---|---|---|---|---|
| **PD-25** | **seeds=3 写在表里,方差从不报** | 📎 Table 8 声明 seeds=3,但**全文没有任何 seed 级离散度**;唯二的不确定性数字都是**从 n 来的二项近似**(「±8.5% binomial 95% CI at n=103」p.17、「one standard error ∼3.3%」p.18)。三个种子怎么聚合的,没说 | 我方单 seed 但**并报方差**;并已实测同池噪声地板(3.18–3.44pp) | 🥈 |
| **PD-26** | **无 token-matched 非演化对照** | §6.4 的 meta-agent 消融是公平的(共享模型/预算/infra),但**头条**的 evolved-vs-static 一臂烧 **100–175M** meta token、另一臂零,**没有 best-of-N / pass@k 在匹配预算下的对照** | ⛔ 算力/token 配平臂**已被用户 Jul-31 裁掉**(改全臂成本并报)。只进 threats | ⛔ 🥉 |
| **PD-27** | 多处增益落在自己的 CI 内且无检验 | 用论文自己的 ±8.5%(n=103):τ³-Qwen **+1.1%**、协同演化 **+4.3/+5.0%**、AEGIS vs CC-SDK **+1.0%**(明写 within 1 SE)全是 sub-CI 却当发现报。论文用同一个 CI **否定** Global 的 −24.3%(正确),却对这些正向 sub-CI 不做检验 | 我方一律报显著性 | 🟠 |
| **PD-28** | 旗舰稳定性主张建立在 n=1 配置上 | 「变体隔离是稳定演化的必要条件」(§7.1)只靠**单个** GAIA×GPT-5.4 的 Global vs Ensemble(Table 5)。更细的策略只做了 pilot(30–40 题 ≤8 轮)。**且该比较有循环味**:让 Global 难看的指标(final−peak = −24.3%)正是 PD-12 批评的 peak 伪影,而 Ensemble 被设计成 peak=final | 我方 epoch 聚合口径可给出无偏版本 | 🟠 |
| **PD-29** | 协同演化不是算力对等 | +4.7% 只有 **2 个 bed、1 个模型(Qwen)、无种子无方差**,peak-based,曲线「coincide until R4 then diverge」。「无额外成本」只覆盖 **rollout**;额外的 GRPO 梯度步(8×H100、5 步/轮)是 frozen 臂**从未得到**的真实算力 | 只进 threats(我方不做 RL) | 🥉 |
| **PD-30** | reward-hacking 增益计入了 peak | 📎 §6.6/App C:作弊编辑 **R10 上线并把准确率抬到 79.6**(计入 peak),R11 才检出、R12 才防住 | 论文很坦白,但意味着部分头条增益**经由 exploit 中转过**。是"per-edit gating 有结构性极限"(PD-10)的实例 | 🟠 |

---

## 附:两条我方内部口径的更正

| | 原口径 | 更正 |
|---|---|---|
| GAIA 规模 | 「我们跑 n=103,他们的**完整** GAIA」 | ❌ 论文自称 103 是「the GAIA **text-only subset**」,而 GAIA 公开验证集是 **165**。可说「他们每轮评的全集」,**不能说 = 整个 GAIA** |
| 我方推导的防火墙 | — | 「有效池 = min(K,簇数)」和 peak 偏差 **+8.1pp** 都是**我方推导**。论文从不报簇数;论文的 peak 膨胀自认(Fig.4)是**定性**的,不是那个数字 |

---

## 修复进度

| PD | 动作 | 状态 |
|---|---|---|
| PD-22 | P6 fork 轮计分口径 → epoch 聚合 | ✅ **已修**,`h_config_epoch_curve.py` |
| PD-11 | "§7.7 分解工单" confabulation | ✅ **已作废并记录** |
| 其余 | — | 📋 未动 |
