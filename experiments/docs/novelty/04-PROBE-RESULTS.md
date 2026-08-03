# Tier-0 首轮探针实测（2026-08-03）

> 🔴 **部分失效（2026-08-03 晚）**：本文"探针 2/3"对变体池的**解释**已被 `06-POOL-PATHOLOGY-VERDICT.md` 取代——"僵尸变体""坟场""K=8 永不填满""祖先永久死亡"四处判断均**不成立**（池确实填满到 8；承载 0 是**休眠**不是死亡，V5 曾零两轮后拿走 52 道题）。
> **原始测量数字仍然有效**（逐轮 routing、fork 谱系、coverage），失效的只是解释层。
> 🔴 **追加失效**：探针 1 里 s1k8b103 的 `peak−final = +5.8pp` **作废**——该 peak 建立在 fork 轮的 100% 保送块上。无偏计分下 **peak = final，peak−final = 0.0000**，见 `07-FORK-REUSE-CORRECTION.md`。结论方向不变（裸 best-so-far 不是真提升），但对本跑而言它挽回的量**恰好是零**。

> 全部跑在已落盘 run 产物上，零 API 成本。脚本见 scratchpad `probe.py`（一次性，未入库）。
> **本文两条结论都推翻了主循环先前的判断**，以本文为准。

---

## 探针 1 — 精英保留（best-so-far）的反事实增益

数据源：各 run 的 `pool_report.curve`（逐轮 pass@2）。

| run | 轮数 | final | peak | peak−final |
|---|---|---|---|---|
| **s1k8b103** | 15 | 0.748 | 0.806 | **+0.058** |
| s1k8 | 6 | 0.800 | 0.867 | +0.067 |
| a1big5 | 4 | 0.800 | 0.800 | 0.000 |
| a1big4 | 4 | 0.400 | 0.533 | +0.133 |
| a1pilot2 | 4 | 0.583 | 0.833 | +0.250 |
| forkprobe | 5 | 0.417 | 0.583 | +0.167 |
| （其余 ≥3 轮 run 见脚本输出） | | | | |

**汇总（≥3 轮的 16 个 run）**：mean(peak−final) = **+5.78pp**；其中 **8/16** 的 peak > final，这 8 个的均值 **+11.6pp**。

### ⛔ 结论：朴素 best-so-far 精英保留**不是**一个安全的改进

在同一批评测集上取历史最高分，**本身就是选择偏差**。用 `02-DETECTION-PLAN.md` 的零假设算：SD≈4.3pp、15 轮的纯噪声下，E[peak−final] ≈ **7–8pp**。

⇒ **s1k8b103 实测的 +5.8pp 低于纯噪声期望值。** 也就是说，在旗舰跑上，"保留冠军"买到的东西**完全可以由噪声解释**，不构成真实提升。

### ✅ 改成可辩护的版本：δ-阈值冠军

只有当候选轮次比在位冠军高出**超过噪声带 δ**（δ ≈ 1.96×SE ≈ 8.5pp，或用平滑分数）时才换冠军；否则留任。

- 这把 N-01 从"平凡的 elitism"变成了一个**真实机制**：在噪声床上，朴素 elitism 是噪声追逐，带阈值的 elitism 才是稳定性策略。
- 它同时给论文的稳定性叙事一个更准确的修正：论文用**单轮**二项 CI（±8.5%）去判定一个 **max-over-15-rounds** 统计量，零假设选错；正确的零假设下 Global 的 −24.3pp 里约 7–8pp 属于峰选择，真退化约 −16pp。
- 预期收益因此**很小**。这符合"克制"的目标，但必须诚实上报，不能拿 +5.8pp 当战果。

---

## 探针 2 — 变体池的实际形态（s1k8b103，15 轮 × 103 题 × K=8）

**配置（`experiment.lock.json` 逐字）**：
`cluster_source=gaia_level`，`cluster_mode=routed`，`routing_mode=cluster`，`epsilon=0.0`，`estimator=laplace`，`retirement_metric=task_macro`，`retire_reassign=reroute_orphans`，`candidates_per_round=global_up_to_4`，`seed=0`。

**fork 谱系（`fork_retire_events` 逐字）**：7 次 fork，零 retire 事件
`V0→V1(R1)`、`V0→V2(R2)`、`V2→V3(R4)`、`V3→V4(R6)`、`V3→V5(R7)`、`V4→V6(R11)`、`V6→V7(R13)`

**累计覆盖（`coverage_per_variant`）**：
`V0=99, V1=69, V2=36, V3=64, V4=15, V5=64, V6=64, V7=64`（题数，103 题床）

**逐轮 `active_pool_measurements` 键数**（R0→R15）：
`1, 2, 3, 2, 4, 2, 3, 4, 3, 3, 3, 4, 2, 3, 2, 3`

### 结论一：K=8 **确实达到了**（8 个变体被创建）

主循环先前"K=8 结构不可达、有效上限=簇数 3"的推断**错误**——观测到 8 个变体、最多 4 个同时在册，>3。

### 结论二：但池没有产生**持久**多样性——V0 是永久在位者

- V0 覆盖 **99/103** 题、每一轮都在册；
- 其余 7 个变体都是**短命挑战者**：V1 只在 R1/R2/R4 出现过、V2 在 R2–R4、V5 在 R7/R8/R11、V4 覆盖仅 15 题；
- 零 retire 事件 ⇒ 它们不是被退役，而是**拿不到任务**（ε=0 的贪心路由下，在位者一旦领先就不再让位）。

⇒ **名义 K=8，任一时刻的有效多样性 ≈2–4，持久多样性 ≈1。** 这正是 ε=0 贪心 + 自指簇定义预期的"富者愈富"，只是表现形式是**在位者压制 + 挑战者轮换**，而不是我先前猜的"容量被簇数封顶"。

### 对候选清单的影响

- **N-06 新生变体保护期**从"最坏无事发生"升为**由数据直接指向的修复**：新 fork 的变体没有任何最短在位期，一旦首轮拿不到任务就再无翻身机会（V4 覆盖 15 题即例）。
- **N-05 ε>0** 同样被强化，但风险仍在（主动送任务给较差变体 ⇒ 短期掉分）。
- **N-08 改写**：不再主张"容量被簇数封顶"，改为主张"**池缺乏持久多样性**"，这直接关系到 N1 的"持久演化池"轴到底成不成立。

### 键义已查清（探针 3）

`active_pool_measurements` 不是权威字段。`pool_state.json` 里有 **`routing = {variant_id: [task_id,...]}`** 和 **`variant_count`**，这才是承载关系的真值。见下节。

---

## 探针 3 — 逐轮 routing（s1k8b103）★信息量最大

```
R0  vc=1 V0:103                                   ship=F fork=[]   cand=0  nocand=T
R1  vc=2 V0:82  V1:21                             ship=T fork=[V1] cand=4
R2  vc=3 V0:36  V1:64  V2:3                       ship=T fork=[V2] cand=4
R3  vc=3 V0:91  V1:0   V2:12                      ship=F fork=[]   cand=0  nocand=T  idle=1
R4  vc=4 V0:39  V1:12  V2:21  V3:31               ship=T fork=[V3] cand=4
R5  vc=4 V0:39  V1:0   V2:0   V3:64               ship=F fork=[]   cand=0  nocand=T  idle=1
R6  vc=5 V0:39  V3:60  V4:4                       ship=T fork=[V4] cand=4
R7  vc=6 V0:39  V3:50  V4:12  V5:2                ship=T fork=[V5] cand=4
R8  vc=6 V0:39  V3:52  V5:12                      ship=F fork=[]   cand=0  nocand=T  idle=1
R9  vc=6 V0:39  V3:52  V4:12                      ship=F fork=[]   cand=0  nocand=T  idle=2
R10 vc=6 V0:39  V3:52  V4:12                      ship=F fork=[]   cand=4           idle=3
R11 vc=7 V0:39  V4:10  V5:52  V6:2                ship=T fork=[V6] cand=4
R12 vc=7 V0:39  V6:64                             ship=F fork=[]   cand=0  nocand=T  idle=1
R13 vc=8 V0:39  V6:61  V7:3                       ship=T fork=[V7] cand=4
R14 vc=8 V0:39  V7:64                             ship=F fork=[]   cand=0  nocand=T  idle=1
R15 vc=8 V0:39  V6:12  V7:52                      ship=F fork=[]   cand=4           idle=2
```
（`vc` = `variant_count`；未列出的变体该轮承载 0 题）

### 结论 A — 承载多样性确实被簇数封顶（**v1 对、v2 过度纠正、以此为准**）

- **非 fork 轮**承载任务的变体数 ∈ **{2, 3}，从未超过 3** —— 配置是 `cluster_source=gaia_level`（3 个簇）+ `routing_mode=cluster`（同簇整体去一个变体）。
- **fork 当轮**可短暂到 4（= 3 个簇 + 新变体拿到的目标子集 T_k，见 R4/R7/R11 的 31/2/2）。
- `variant_count` 单调涨到 8 且**零 retire**，但其中多数每轮承载 0 题——**僵尸变体**：还在册，拿不到任务。

⇒ 精确陈述：**名义 K=8 从未转化为 >3 的稳态承载多样性。K 不是有效容量旋钮，簇粒度才是。**
（记录修订过程：v1 从论文 §4.5 推出该上限但用错了字段；v2 因 `active_pool_measurements` 键数最高到 4 而判"证伪"，属过度纠正；v3 用 `routing` 字段确认 v1 的推理成立，只需加上 fork 当轮的例外。）

### 结论 B — V0 永久锁死 Level-1 簇，其余 64 题在变体间"击鼓传花"

- **R4 起 V0 恒为 39 题，一动不动**（39 = GAIA Level 1 题数，与 e_pervar3 的 V0=39题(Level 1) 吻合）；
- 剩下 **64 题（Level 2+3）作为一个整体在换庄**：R5 归 V3、R12 归 V6、R14 归 V7；
- 每个新 fork 的变体先拿一小撮目标题（21/3/31/4/2/2/3），要么随后整簇接管，要么归零。

⇒ 池的真实形态不是"8 个专家分工"，而是**"一个锁死的 L1 专家 + 一个不断易主的 L2+3 席位"**。**这直接动摇 N1"持久演化池"的前件。**

### 结论 C ★ — 旱灾在上游，不在门

- **7/16 轮 `candidate_count=0`**（R0,R3,R5,R8,R9,R12,R14）= **44% 的轮次连一个候选都没产出**；
- 产生了候选的 9 轮里，**7 轮成功上线（78%）**。

⇒ **瓶颈不是"门太严把候选拦下了"，而是"上游根本没产出候选"。** 这把 **N-04（α / 选择性调用阈值）从"顺手补个规格洞"顶成了旱灾的主嫌**，优先级应高于 N-02（门）。待 `05-OFFLINE-RESULTS.md` 定位这 7 轮各死在 Digester / Planner / Evolver 哪一段。

### 结论 D — patience=3 会停在 R10

`idle` 序列 R3:1 → R5:1 → R8:1 → R9:2 → **R10:3**。论文 verbatim `P=3` 在 R10 触发早停，与 [我方] 记录的 "p3 停 R10(60.2%)" 一致。本跑用的是 `--patience 16` 才跑到 R15。

### 结论 E — fork 当轮新变体的分数不是新采样的

`active_score_source` 显示新 fork 的变体首轮一律 `candidate_reuse`（复用候选评测阶段的 rollout），其后才转 `fresh_rollout`。**这是一个未在论文中讨论的方法学细节，可能给新变体的首轮分数引入偏差**，须进 threats，并在 Ŝ 校准里分开算。
