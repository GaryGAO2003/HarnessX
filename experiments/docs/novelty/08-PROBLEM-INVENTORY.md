# 问题总台账（自查后定稿）

> 2026-08-03。**本文是 novelty/ 目录下所有问题主张的唯一权威索引。**
> 与 `01`/`04`/`06`/`07` 冲突时以本文为准；本文与那几份的关系见 §6。
> 所有数字来自主循环亲验（脚本在 `experiments/analysis/novelty/`），零 API 成本。
> ⚠️ 本文经过**两轮**对抗性自查：把每条主张拆成"它依赖什么前提"再逐个打前提。撤回记录见 §5，**跨 session 引用前必读**。
> **第二轮（2026-08-04）**首次做了两件此前没做的事：①**核对论文原文 PDF**
> （`docs/assets/paper/HarnessX_Tech_Report.pdf`，43 页），不再依赖转引；②**对齐
> `PAPER-METHODOLOGY-DEVIATIONS.md` 的 M 编号**。结果推翻了 §3 归因表的两条方向（P2 升级、
> P3 降级），并诊断了 P8。所有数字经独立重算复核，逐位命中。

---

## 0′. 与 `09-SCORE-LEVERS.md` 的关系（2026-08-04 加）

`09` 报的根因 **silent processor drop**（`harness.py:353-360` 裸 except ⇒ 上线编辑不落地）
**在本文 P1–P8 之上**，且 `09 §6-6` 记 **s1k8b103 已被论文草稿弃用**
（`CH3-REPRODUCTION-DRAFT.md:313-317`）⇒ **本文所有基于 s1k8b103 的逐轮数字不得进论文正文**，
机理层结论（根因在源码里）仍有效。

⚠️ **`09` 有三处沿用了本文第二轮已改掉的旧结论**，交叉引用时以本文为准：
`09 §4-S3` 的「完美分离，零反例」（半恒真，见 P4）、`09 §5` 的「`peak−final` 精确为 0」
（四位精度不成立，见 P6）、`09 §2` 的「SD 4.59pp、23.3% 翻面」（窗口 n=3 截短，见 P3）。
另 `09 §4-S3` 写「6/16 轮」，实为 **6/15**（R0 是基线轮，不进引擎）。

⚠️ **`09 §4-S2` 想把 ±5% 噪声阈接进 gate，与 M-25 的裁定冲突**：M-25 据 App C 的
`tasks_at_risk=[]` 与 §4.1 逐字零容忍，裁定 **±5% 是分析层口径，门不改**。本文按 M-25 执行
（已落在 `h_config_epoch_curve.py`）。若要改门，须先推翻 M-25 的证据。

🟢 **本文对 `09` 的一处反证**：`09 §6-2` 由「e_pervar3 抽查 150 条轨迹同样全部 20 步封顶」
推断该跑也可能是 no-op。**该推断对 e_pervar3 不成立**——R2 上线的编辑是加一个
`StepCountdownProcessor`（`_hook_: '*'`），它只**播报**剩余步数，**不抬上限**，所以 20 步封顶
正是它落地后应有的现象。且该候选**两个致命模式都不沾**：`_target_` 是正常点路径（非
Windows `file://` 盘符 URI），且不传 kwarg（`StepCountdownProcessor()` 已实测无参可实例化）。
（`09` 对 s1k8b103 的 V3/V4 判 CONFIRMED 仍成立——那里 `budget_floor=30/55` 本应移动上限而没移动。）

🟢 **由此得出本轮唯一一个显著的正向结果**（见 P6 的 epoch 口径）：

| e_pervar3 上线 | 效应 | SE | t (df=10) | |
|---|---|---|---|---|
| **R2 fork（加 StepCountdownProcessor）** | **+10.97pp** | 2.66 | **+4.12** | 🟢 **显著** |
| R8 apply | −1.75pp | 1.93 | −0.91 | n.s. |

口径：相邻 epoch 均值差；两 epoch 之间恰好隔一个上线轮，故该差**就是**这次上线的效应。
⚠️ n=1 seed、无 held-out、单跑，**不得写成 "+11pp 的方法收益"**；可写的是"两次上线中一次
产生了超出噪声地板的可测效应"。

---

## 0. 证据强度标记

| 记号 | 含义 |
|---|---|
| 🟢 | 主循环亲验，且**无反例**（完美分离或直接测量） |
| 🟡 | 机制坐实（代码/逐字），但**幅度小或未隔离归因** |
| ⚪ | 未验证 |
| ❌ | 已撤回 |

---

## 1. 问题清单

### 🟢 P4 — 目标选择发生在路由冻结之前（原 N-10）

**病理**：目标在 `recipe:4260` 选定，用的是**上一轮**的承载情况；路由冻结在 `engine:297`（`run_round` 内部第一句），随即把该目标的题按 argmax 判给同簇更强的兄弟；`engine:316-319` 跳过空簇变体 ⇒ 零候选。

**证据（s1k8b103）**
- 6 个 `no_candidate` 轮（R3/R5/R8/R9/R12/R14），目标**冻结时承载 6/6 = 0**
- 6 个废轮的目标**上一轮都还带着题**（V1@R2=64、V2@R4=21、V4@R7=12、V5@R8=12、V4@R11=10、V6@R13=61）⇒ **"刚被夺食"，非长期空载**
- 目标分数是真低分，**无一走 0.5 冷启动路径**：0.48 / 0.15 / 0.38 / 0.11 / 0.25 / 0.49
- 离线重构复现真实 `paper_target_variant` **15/15 轮**

⚠️ **口径更正（原表混了两种量）**：`pool_state.routing` 是**轮末**快照。9 个出候选轮曾被记为承载
`82/36/21/60/50/12/10/61/39`，那是 fork **之后**的残留；门实际测到的（＝冻结时承载）是
`103/39/52/64/52/12/12/64/39`（＝ `candidate_gate_measurements[target]` 的 cell 数）。
R1 尤其醒目：表里写 82，实际 V0 冻结时带着全部 **103** 题。
**废轮那 6 个 0 不受影响**（无 fork ⇒ 轮末＝冻结时），"上一轮承载"那 6 个数也正确
（目标本就按上一轮轮末状态选）。

⚠️ **"完美分离"有一半是恒真式，不计入证据**：候选只对 target 生成（16 轮的
`candidate_gate_measurements` / `pipeline_audit_paths` / `decisions` 键**永远只有 target**），
叠加 `engine:316-319` 的空簇 `continue` ⇒ **"目标承载 0 ⇒ no_candidate" 是机械恒等**，
"9 个出候选轮承载 >0" 不可能出反例。
**有信息量的只有另一方向：6/6 废轮均由空目标解释**（废轮本可有别的成因——P8 即是）。

**性质**：**保真度修复，不是改进。** 论文 §4.5 逐字 *"a candidate targeting variant k is tested only against tasks routed to k"* 本身就预设 k 有 routed tasks，即预设路由已定。我方把选目标放在冻结之前，违反了该句的隐含前提。论文对目标选择规则与其时序**均无规定**（lock 里我方自己的 provenance：`target_strategy_provenance = "OURS: paper leaves target selection undefined"`）。

**复现范围**：仅 s1k8b103。s1k8=单变体 0 废轮；a1big5 太短 0 废轮；**e_pervar3 有 4 个废轮但目标承载 = 103/39/39/39 ≠ 0 ⇒ 另一种病（见 P8）**。⇒ 需"多变体 + 足够轮数"才显现，不可写成普遍规律。

**修法定案**：**(a) 预览路由**——选目标前先调一次 `freeze_routing`（已证在 `epsilon=0` + `tie_break=fewest_attempts` 下是纯函数，与真实冻结逐字节相同）。~15–25 行，只动 `run_variant_pool.py`。
⚠️ **不要加 `Hyperparams` 字段**（会写进每个 lock、破坏 resume 哈希比对），走 `_epsilon_provenance` 那个 provenance 模式。旗标 `--retarget-after-freeze`，默认关。
（(b) 下沉进 engine 40–80 行改公开契约；(c) 冻结后兜底 50–90 行且 freeze→evolve 之间无 recipe 钩子，**最贵**。）

**收益**：6 轮空转消除。但反事实目标里 **R5/R14 会去打 V0（39 题、~90%，只剩 4–5 个失败）**，R9 打 V4（12 题）⇒ 真有余量的只有 3 轮。诚实估计 **+2~3 次上线**，非提分。

---

### 🟢 P6 — fork 轮计分含 100% 保送块（已修正口径）

**病理**：新生变体在诞生轮的分数来自 `candidate_reuse`，而复用的正是"当初判定它改进了"的那次测量 ⇒ **该块 pass@2 按构造必然 100%**。7/7 fork 轮无一例外。

**失真规模**：R1 保送 21 道（占床 **20.4%**）、R4 保送 31 道（**30.1%**），其余 2–4 道。

**修正结果（s1k8b103，R1–R15）**

| 曲线 | peak | 峰位 | final | peak−final |
|---|---|---|---|---|
| reported | 0.8058 | R1 | 0.7476 | **+0.0583** |
| fresh（丢保送块，分母缩水不可比） | 0.7561 | R1 | 0.7476 | +0.0085 |
| **imputed（无偏，主用）** | **0.7476** | **R15** | **0.7476** | **0.0000** |

**相对 R0（0.6408）的 +10.68pp 增益不受影响**（末轮无保送块）。

⚠️ **imputed 曲线并非全轮 n=103**：R1 的 `imp_n=100`（3 个不可插补）、R6 `imp_n=102`。本文以
"分母缩水不可比"否掉 fresh 曲线，imputed 有同一毛病的缩小版，且恰落在 R1——整条"冠军保留归零"
所系的那一轮。R15 干净（103），故 peak/final 不受影响。
⚠️ **`peak−final = 0.0000` 不该写到四位小数**：每轮测量带 ±3.2–4.3pp 噪声（见 P3）。
诚实写法是"**与 0 不可区分，且远低于纯噪声下 7–8pp 的期望**"。撤回本身成立，精度不成立。

**连带**：①N-01 冠军保留对本跑**归零**；②RISK F14 由"落在噪声带内"**升级**为"逐字复现论文 Ensemble 签名形态（peak=final、峰在末轮 R15/15）"；③`pool_report.curve` **今后不得直接引用**。

⚠️ **③ 过宽，误伤了 APPLY 复用。** 判据只有一句：**被复用的 cell 集合是否由这次测量自身选出**。
- **FORK 复用有偏**：子变体只拿走"被判定改进"的那部分 ⇒ 按构造必然 100%
- **APPLY 复用合法**：变体承载自己**完整**的 T_k，候选正是在这整个 T_k 上测的 ⇒ 满足 M-07 的
  "同配置、同 carrier、完整相同任务子集"三条件（e_pervar3 R8 的 `V0:39` 即此类，不应剔除）

⚠️ **更根本的口径问题本文没修：逐轮取 peak。** 在 SD≈3.2pp 的床上，peak 是对一串噪声取 max，
按构造抬高 `peak−final`。正解是**按配置块（config epoch）聚合**——见 `h_config_epoch_curve.py`：
epoch = 连续的"未上线**且**路由分区逐字节相同"的轮；上线轮是过渡轮，本身就是混合态，须排除。
**副产品：所有 `candidate_reuse` 块都落在上线轮，排除上线轮即自动清除 P6 污染，无需插补。**

| 跑 | per-round peak−final | **epoch-level peak−final** |
|---|---|---|
| s1k8b103 | +5.83pp | **不可算**（该跑几乎无同配置重测，见 P3） |
| e_pervar3 | +5.83pp | **+1.75pp，SE 1.93，t=+0.91（df=10），不显著** |

⇒ **e_pervar3 与论文 §4.5 预测(1) `peak = final` 一致**；先前"e_pervar3 推翻该预测（+8.74pp）"
的读数是逐轮取峰的假象（且该跑续跑 R13/R14 后 per-round final 自己就漂了 2.9pp）。

**跨 run**：a1big5 增益 **+6.67 → +2.67pp**（缩水 4pp）；e_pervar3 峰/终轮恰好无保送块故不变；**s1k8 有一整轮的分完全由复用测量构成（fresh_n=0），其曲线不可作定量用途**。

细节见 `07-FORK-REUSE-CORRECTION.md`。脚本 `f_fork_reuse_correction.py`。

---

### 🟢 P5 — 门测量被丢弃

门在判定 fork 时已把候选（=子变体配置）在**父代整个 T_k** 上实测过，但只有子变体实际承载的那部分进了它的账本：

| 轮 | 门测 cell | 子变体入账 | 丢弃 |
|---|---|---|---|
| R1 | 103 | V1: 21 | 82 |
| R2 | 39 | V2: 3 | 36 |
| R4 | 52 | V3: 31 | 21 |
| R6 | 64 | V4: 4 | 60 |
| R7 | 52 | V5: 2 | 50 |
| R11 | 12 | V6: 2 | 10 |
| R13 | 64 | V7: 3 | 61 |
| **合计** | **386** | **66** | **320** |

⇒ 新生变体的档案里**只留了它考好的那部分**。这是下面"入场估计偏高"的直接成因。

**连带（新生入场估计）**：子变体在 fork+1 轮的簇估计 vs 实际

| fork | child | fork 轮 cell | Laplace 估计 | 下一轮承载 | 下一轮实测 | 偏差 |
|---|---|---|---|---|---|---|
| R1 | V1 | n=21, 26/42 | 0.614 | 64 | 0.516 | +0.098 |
| R2 | V2 | n=3, 5/6 | 0.750 | 12 | 0.333 | **+0.417** |
| R4 | V3 | n=31, 50/62 | 0.797 | 64 | 0.484 | **+0.312** |
| R6 | V4 | n=4, 4/8 | 0.500 | 12 | 0.500 | 0.000 |
| R7 | V5 | n=2, 2/4 | 0.500 | 12 | **0.083** | **+0.417** |
| R11 | V6 | n=2, 2/4 | 0.500 | 64 | 0.641 | −0.141 |
| R13 | V7 | n=3, 4/6 | 0.625 | 64 | 0.656 | −0.031 |

**7/7 在 fork 后一轮夺走整簇**（12 或 64 道）。5/7 高估，3 次超 +0.3；2 次低估。
⚠️ **n=7，不做显著性宣称**；小 n 那几个方向不一致，噪声成分大。但"**新生必夺簇**"是 7/7 的结构性事实。

**机制补充**：Laplace 把小样本拉回 **0.5**，而难簇在位者的实测在 0.4 上下 ⇒ **0.5 先验在难簇上是"乐观的"，任何证据稀薄的变体自动获胜**。这是无意的"面对不确定性时保持乐观"，且无置信宽度或 ε 控制。

**修法**：补记那 320 个非承载 cell。**已证不会双计**——子变体的 candidate_reuse cell 与 gate cell 在共享（改进）题上**逐值相等且 child ⊆ gate，7/7 fork**；只补记补集即可。防双计位置 `recipe._safe_candidate_reuse:5441` / `_record_settled_active_outcomes:5398`。

⚠️ **修法成本被高估了：引擎里已有现成实现，是被旗标关掉的。**
`engine.py:591-595` 在 FORK 时把候选在**整个 T_k** 上的成败全部记给子变体，注释逐字写着
只记改进题会 *"gives a child an optimistic prior and inflates both routing and retirement rollups"*
——**引擎作者独立识别出了 P5 一模一样的病理并写了缓解**。它由 `record_selected_results` 控制，
而 `recipe:4212` 把它设成 `candidate_mode != "paper"` ⇒ **paper 模式恒为 False**，本项目全部跑都关着。
⇒ ①这反过来**确证 P5 的前提**（320 个 cell 确实被丢，用 `active_pool_measurements` 重建账本的基是对的）；
②修法应优先复用该路径，而非新写补记逻辑。
⚠️ 但翻旗标会连带改 APPLY 的记账（`engine:569`）并偏离 paper 模式，不是免费开关。

---

### 🟢 P7 — `ever_solved` 只增不减（棘轮）

论文 §4.1 逐字：*"the candidate must not regress any previously solved task recorded in T_t"*，而 `T_t` 单调（`pool.retire` 都特意保留 `ever_solved`，`pool.py:218`）。

**实测（s1k8b103）**：全局 `ever_solved` 由 66 涨到 **96**，**吃满全床**（床上测过 96 道，另有 7 道从未通过）。逐轮 gap（在约束集但当轮未通过）**R2 起**稳定在 **18–30 道**（R1=5，因 `ever_solved` 刚涨完）。

> 下表以 16 轮为分母，前提是**每轮 routing 划分全部 103 题、每题每轮必测**——已核 16 轮
> `sum(routing) ≡ 103`。若改用抽样路由，该分母失效。

**每题通过轮数分布（16 轮）**

```
0轮×7  1轮×3  2轮×5  3轮×3  4轮×2  5轮×6  6轮×6  7轮×1  8轮×2
9轮×4  10轮×2 11轮×1 12轮×2 13轮×3 14轮×5 15轮×10 16轮×41
```

⇒ **11 道题（占约束集 11.5%）通过率 <20%，却是永久义务。** 任何候选只要测到它们，就会被记上躲不掉的回退。

**实例**（R15 判定回退的 5 道题的完整通过史）

```
46719c30   8/16   1001101110011000   ← 全程在翻
7673d772   2/16   0101000000000000   ← R3 之后连续 12 轮没对过
840bfca7  12/16   1011111110101011
935e2cff  13/16   0111111110011111
c365c1c7   1/16   0000000000010000   ← 只在 R11 蒙对过一次
```

5 个里 **2 个是"基本从来做不对"的题**，纯靠蒙对一两次进入永久约束集。

脚本 `g_ever_solved_ratchet.py`。

---

### 🟡 P3 — 单次噪声二值测量用作硬判定（~~★最上游~~ 已降级，见 §5 第二轮）

⚠️ **归因更正（核了论文原文，本条不是对论文的批评）**：论文 Table 8 (p.29) 逐字给出
`noise threshold | ignored single-round pass-count delta | ±5%` 与 `seeds | random seeds per cell | 3`。
**我方两个降噪装置一个都没接**（全部 14 跑 `noise_threshold=None`，实跑 `seed=0` 一个）。
所以"论文的方法太噪声"站不住；可写的是"**在缺失论文自带两个降噪装置的条件下，本床噪声地板实测 3.18pp**"。

**基础事实（口径已更正）**：原表用 e_pervar3 的 R3/R4/R5 三点（SD 4.59pp、23.3% 的题至少翻转
一次）。**同配置窗口实际有 5 轮**（R2 fork 到 R8 apply 之间零上线，routing 恒为 {V0:39,V1:64}）：
76.7 / 85.4 / 83.5 / 84.5 / 76.7，SD 4.31pp，**33.0% 的题至少翻转一次**。
⚠️ 翻转率**不是 n-不变量**（轮数越多越易翻），引用必须带 n。

**更严的口径见 `h_config_epoch_curve.py`**：要求"未上线 **且** 路由分区逐字节相同"才算重测，
得 e_pervar3 三个 epoch（n=2/5/6），**合并组内 SD = 3.18pp（df=10）** —— 这是本床可用的噪声地板，
**A/A 空校准不必再跑**。非配对双臂 80% 功效检出 3pp 需**每臂 18 轮**，4pp 需 10 轮。
⚠️ **s1k8b103 在该口径下退化**：它的路由几乎每轮都变（`freeze_routing` 每轮重跑 argmax，簇会迁移，
如 R8→R9 的 12 题簇从 V5 挪到 V4），即使没上线；⇒ 它没有可用重测，**跨轮 peak/final 对它无意义**。

四点 R0 基线 64.1/66.0/69.9/70.9（均为 103 题床：s1k8b103 / e_pervar / e_pervar2 / e_pervar3），
SD 3.21pp。⚠️ 四跑 `config_sha256` 与 `git_sha` 均不同（见 P1），故此值混入了跨跑代码漂移，是噪声上界。

**三个后果**
1. **APPLY 几乎不可达** —— APPLY 要求"零回退"，而测的题越多，"至少一道因运气翻掉"越接近必然
2. 拒绝可能是误杀（**但见 §5 撤回**：R15 不是好例子）
3. 反向：`2/2 → 1/2` 的真实软退化 any-k 看不见（`engine:846-849` 把 before 二值化为 (2,2)/(0,2)）

**支持证据**：s1k8b103 六次"非 R4"的 fork，其**自己变体的**回退数为 2/1/9/8/3/5，而目标手上 50–60 道题 ⇒ **约 15%，正落在噪声带**。

**跨 run apply/fork 分布**

| run | 床 | 轮 | fork | apply | reject | 回退基线 |
|---|---|---|---|---|---|---|
| **s1k8b103** | 103 | 16 | **7** | **0** | 2 | global |
| e_pervar3 | 103 | 11 | 1 | 1 | 3 | **per_variant** |
| s2k8b50 | 50 | 13 | 1 | 1 | 5 | ? |
| s1k8 | 30 | 6 | 0 | 2 | 2 | global（单变体，两基线等价）|
| a1big5 | 30 | 4 | 2 | 1 | 0 | global |

**s1k8b103 是全项目唯一零 apply 的跑。**

🟡 **未隔离**：噪声与"测试集大小"、"回退基线口径"三者纠缠，现有数据无法分离。**需要受控对照。**

**成熟解法**：irace / Iterated F-Race（López-Ibáñez 2016）、Hoeffding Races（Maron & Moore 1993）、Heidrich-Meisner & Igel（ICML 2009，CMA-ES 内加 race）——噪声适应度下用统计检验替代单次判定，是算法配置领域的事实标准。详 `03-EXTERNAL-PRECEDENTS.md` §(c)。

---

### 🔴 P2 — global 回退基线算别的变体的账（**论文 §4.5 明写 per-variant，我方没做到**）

**机制代码级坐实**
- `gate.py:88` 文档："*regressed is judged against `SuccessLedger.ever_solved`, the [cross-variant set]*"
- `gate.py:252-254`：`per_variant` 分支之外，global 路径 `return ledger.is_ever_solved(outcome.task_id)` —— **只查 task，不带 variant**
- provenance 全文逐字："*a task only ever solved by a different variant no longer counts as a regression for this candidate*"

**但幅度经实测远小于预期**：逐次 fork 的回退来源

| 轮 | 决定 | 目标 | 回退数 | 目标**自己**解过 | **只有别人**解过 |
|---|---|---|---|---|---|
| R1 | fork | V0 | 2 | 2 | 0 |
| R2 | fork | V0 | 3 | 1 | 2 |
| **R4** | fork | V2 | 12 | **0** | **12** |
| R6 | fork | V3 | 21 | 9 | 12 |
| R7 | fork | V3 | 14 | 8 | 6 |
| R10 | reject | V4 | 7 | 4 | 3 |
| R11 | fork | V4 | 4 | 3 | 1 |
| R13 | fork | V6 | 15 | 5 | 10 |
| R15 | reject | V0 | 5 | 5 | 0 |

⇒ **只有 R4（1/7）的回退全部来自外来题**，换 per_variant 后回退归零、本该 APPLY。其余 6 次 fork 都有真实的自身回退，换基线**照样 fork**。

（唯一余地：per_variant 下"自己没解过但候选解出了"的题会由"回退"翻成"改进"，实际可能翻多于 1 次；但能**确凿**认定的只有 R4。）

🔴 **归因更正（已核论文原文 `docs/assets/paper/HarnessX_Tech_Report.pdf`，43 页）**

M-23 把本条记成「[PAPER 张力] §4.1 与 §4.5 给出两读」，据此裁定默认 `global`。
**原文没有张力。** §4.5 p.11 逐字：

> The adaptation loop (Section 4.4) maintains a **single harness** H_t. … **Once multiple variants
> exist, the seesaw constraint is scoped per-variant**: a candidate targeting variant k is tested
> only against tasks routed to k, **so improvements to one cluster cannot regress another**.

p.17 复述：*"Edits are proposed and evaluated **per-variant**, so an edit improving one cluster
cannot regress another."*

⇒ §4.1 p.8 是 **K=1 单 harness** 情形；§4.5 对 K≥2 **显式覆盖**。这不是并列两读。
⇒ **本条不是"论文留白/两读"，是"论文明写、我方没做到"**：s1k8b103 跑的是 K=8 + `global`。
⇒ **R4 就是 §4.5 承诺 "cannot happen" 的那件事在我方实现里发生的实例**（12 个回退全部来自
只有别的变体解过的题）。证据强度远高于"1/7 收益"。
⇒ 唯一辩护空间：冒号后那句只讲测试集范围 T_k，而我方确实按 T_k 测了；但结尾目的从句与 p.17
的 "evaluated per-variant" 把这条路堵死——global 下 §4.5 承诺的保证在 R4 实测失效。

**各跑合规实况**：`e_pervar` / `e_pervar2` / `e_pervar3` 三跑均为 `per_variant` ✅；
`s1k8b103` 为 `global` ❌。判定 s1k8b103 的**决定性证据是行为不是配置**：R4 判 FORK，若为
per_variant 则 regressed=0 ⇒ 按 §4.5 outcome(1) 应判 APPLY。
（补强：`fork_inheritance=transfer` 只转路由不转 ledger cell，`pool.py:196`，故 V2 的 ledger
确不含 V0 历史。旁证：lock 的 `provenance_warnings` 无 M-23 条目；`run_config` 无
`regression_baseline` 键 ⇒ 走默认。）

⚠️ **用户裁定（2026-08-03）：本条不写进论文。** 本节为内部记录。
注意 **不写 ≠ 可声称合规**：略过回退基线不谈是裁量权，但若正文写"s1k8b103 复现了 §4.5 的
Ensemble"，那是主动陈述，重放一次 R4 即穿。

⚠️ **由此产生的最大风险**：**没有任何一跑同时满足「§4.5 合规」+「多变体真正展开」**——
合规三跑至今只产生 V0/V1 两个变体，展开到 8 变体的 s1k8b103 不合规。而 M-23 自己写了
「K=1 下 global=per_variant 故不显形」，V_t=2 时两种基线的差异也几乎无从显形。

**修法**：`--regression-baseline per_variant`（M-23，旗标现成）。零成本。
⚠️ **不再以"预期收益 1/7"作为排序依据**——1/7 衡量的是这次偏离**代价多大**，不是修不修可选。

---

### 🟡 P1 — 跑与跑之间不可直接比较

- 🟢 **亲验**：`h0.config_sha256` 不同 —— s1k8b103 `cfcb70d9…` vs e_pervar3 `46039060…`（system prompt sha 与 tool_registry 清单相同，配置哈希不同；两跑相隔 4 天，其间代码有改动）
- 🟢 **亲验（更直接的证据，原表漏引）**：`git_sha` 不同 —— `08f6cc77…` vs `f85a8cdb…`；`created_at`
  2026-07-30 vs 2026-08-03。**"其间代码有改动"不必靠推断，lock 里就有。**
- ⚪ **未亲验**：台账称 e_pervar3 为 thinking OFF。⚠️ **原表措辞不准**：不是"两个 lock 的
  `reasoning_effort` 都是 `null`"——**s1k8b103 的 lock 根本没有这两个 key**（`models` 只有
  `api_base`/`meta_agent_model`/`provider`/`task_agent_model`），e_pervar3 多出
  `reasoning_effort`/`meta_reasoning_effort` 且值均 `null`。**缺字段 ≠ 值为 null**，而 P1 恰恰是
  provenance 严谨性条目，这个区别不能糊。⇒ 若 thinking 差异真实存在则**锁文件未捕获**。**尚未开 RUN-LOG 核实。**
- 两跑的 `hyperparams` **逐字相同**（40+ 字段全等）；差异只出现在 `models`（e_pervar3 多 `reasoning_effort` / `meta_reasoning_effort` 两字段，值均 null）、`h0.config_sha256`、`provenance_warnings`（多 M-23 一条）

⇒ **地基问题：不补则任何两跑对照都站不住。**

---

### 🟢 P8 — e_pervar3 的旱灾（已诊断：两个已登记机制，非新病）

e_pervar3 有 4 个 `no_candidate` 轮（R1/R3/R6/R9），目标承载 = **103/39/39/39 ≠ 0** ⇒ 与 P4 确实
不同源。**病因由 `candidate_accounting` 直接定死**：废轮全是
`requested_slots=4, actual_candidates=0, producer_or_pipeline_rejected=0`
——要了 4 个槽回来 0 个，且一个都没被 pipeline 拒 ⇒ **不是"产出被拒"，是根本没产出**。
（对照 R4：`actual_candidates=4, pipeline_rejected=4`，那是另一种失败。）

再按轮目录分开，是**两个不同的已登记机制**：

| 废轮 | `V0/pipeline` 目录 | `DECISION_REQUIRED.md` | 病因 |
|---|---|---|---|
| **R1 / R9** | **不存在** | 0 | **管线从未启动** ⇒ **M-18** 前置短路（`a_t<α` 或 empty landscape，短路点在 Planner/Evolver 之前） |
| **R3 / R6** | 存在 | 6 / 5 | 管线跑了，meta-agent 不写 `config.yaml`，`evolve_retry` 耗尽 ⇒ **M-21** |

⇒ **CH3 的洞已补**。两条都不是新发现的病理，是已在 `PAPER-METHODOLOGY-DEVIATIONS` 登记的
已知失效模式在本跑的实例；写作时引 M-18 / M-21，不要当成新问题。
残留：要把 R1/R9 精确区分为 actionability 还是 empty-landscape，需要 `a_t` 值，目前未持久化。

---

## 2. 因果链（自查后重排）

```
P3  噪声 + 单次二值硬判定             ← 最上游
 │
 ├─→ APPLY 几乎不可达 ⇒ 上线被迫全走 fork（s1k8b103: 7 fork / 0 apply）
 │     ├─→ 池被撑到 8 变体
 │     │     └─→ P2  global 基线的不公平被放大（跨变体的题越来越多）
 │     ├─→ P5  新生变体档案不全 + 入场估计偏高（只有 fork 才产生新生）
 │     └─→ P6  fork 轮计分灌水（只有 fork 才有复用块）
 │
 └─→ P7  棘轮吸进"蒙对过一次"的题 ⇒ 制造躲不掉的回退

P4  时序错配        ← 独立成因，与上链无关
P1  provenance 洞   ← 横切，影响所有对照
P8  ?               ← 未知
```

**P2 / P5 / P6 都是"fork 太频繁"的下游后果，而 fork 太频繁的因是 P3。**

⚠️ **上链已被第二轮自查改写（2026-08-04）。** 它把 P3 当作方法的固有属性，而核了论文才发现
论文 Table 8 自带 **3 seeds** 与 **±5% 噪声带**两个降噪装置，我方一个都没接。修正后的链：

```
我方未接论文的两个降噪装置（3 seeds + ±5% 带）   ← 真正的最上游
 │
 └─→ P3  单次二值判定暴露在 3.18pp 的失控噪声下
       ├─→ APPLY 难达 ⇒ 上线偏向 fork（s1k8b103: 7 fork / 0 apply）
       │     ├─→ P5  新生档案不全 + 入场估计偏高
       │     └─→ P6  fork 轮计分灌水
       └─→ 逐轮取 peak 造假峰（epoch 聚合后 e_pervar3 由 +5.83 → +1.75pp n.s.）

P2  §4.5 保真度缺口  ← 独立，不是 P3 的下游；论文明写而我方没做到
P4  时序错配         ← 独立成因
P1  provenance 洞    ← 横切
P8  M-18 短路 + M-21 no-config  ← 已诊断，与上链无关
```

**⇒ 原链"P2 是 fork 太频繁的下游后果"不成立**：P2 是独立的保真度缺口，与 fork 频率无因果关系。
**⇒ 修复顺序随之改变**，见 §4。

---

## 3. 谁的锅（论文写作要分开写）

⚠️ **本节已按论文原文重排（2026-08-04）。原表只有两栏，且两条归错了方向——都错在
对我方有利的一侧。** 原表：P2 记为"论文未明说范围"、P3 记为"论文自身规则"。

**实际是四类，不是两类：**

| 类 | 条目 | 逐字依据 |
|---|---|---|
| 🅐 **论文明写、我方没做到**<br>（保真度缺口） | **P2 per-variant 作用域** | §4.5 p.11「Once multiple variants exist, the seesaw constraint is **scoped per-variant**」+ p.17 |
| | **±5% 噪声阈** | Table 8 p.29「noise threshold \| ignored single-round pass-count delta \| ±5%」；实况全跑 `noise_threshold=None` |
| | **3 seeds** | Table 8 p.29「seeds \| random seeds per cell \| 3」；实况 `seed=0` 一个（M-10） |
| 🅑 **论文规则、我方照做、规则本身有代价**<br>（＝对论文的机制分析） | P7 棘轮 | §4.1 p.8「must not regress any previously solved task recorded in `T_t`」+「`T_{t+1} = T_t ∪ ΔT_t`」⇒ 时间单调性确是论文的 |
| | P3 门单次二值零容忍 | 同上；M-25 已裁定 ±5% **不是**门级宽容（据 App C `tasks_at_risk=[]`） |
| 🅓 **论文留白、我方自补** | P4 目标选择规则与时序 | 全文 grep 无任何 target 选择规定；lock 自供 `"OURS: paper leaves target selection undefined"`（M-16） |
| | P5 门测量入账 | M-07 [UNKNOWN]「能否复用 scoped candidate gate rollout…未说明」 |
| 🅔 **我方自订条件有洞 / 记账错** | P6 计分口径 | M-07 的"安全复用"三条件没排除"子集由该次测量自身选出"；M-14 另立「final/peak/curve 只消费 settled active-pool score」 |
| | P1 provenance | M-11「无法解析的 provenance 标 `unresolved`，不填假值」 |

**🅐🅑 可入论文正文（🅐 是自我披露，🅑 是机制分析）；🅓🅔 属复现偏差登记。四种体裁，不能混写。**
⚠️ 🅐 的 P2 一条，用户已裁定不写（见该节）。

🔴 **流程根因**：`novelty/` 整个目录**只引用了 1 个 M 编号**（本文 P2 的 M-23），而 P3/P5/P6/P7
早已分别登记在 **M-25 / M-07 / M-14 / M-08** 下。脱节的直接代价就是上面两处归因反向。
**今后任何新分析文档，第一步是对 `PAPER-METHODOLOGY-DEVIATIONS.md`，不是从零重推。**

---

## 4. 修复顺序

| 序 | 修什么 | 依据 | 工作量 |
|---|---|---|---|
| ✅ | **P8 诊断** | **已完成**：M-18 短路（R1/R9）+ M-21 no-config（R3/R6） | 纯离线 |
| ✅ | **P6 计分口径** | **已完成**，但口径已升级：改用 **epoch 聚合**（`h_config_epoch_curve.py`），不再需要 imputed | — |
| ✅ | **±5% 噪声带（S2-5）** | **已完成**，分析层接入（M-25 裁定门不改） | — |
| **1** | **P2 `--regression-baseline per_variant`** | **论文 §4.5 明写**，非可选消融 | 一个开关 |
| **2** | **P1 补 provenance 洞** | 地基，h0 不同则一切对照无效 | 小 |
| **3** | **P4 预览路由** | 机制已定案、独立可做 | 15–25 行，旗标默认关 |
| 4 | P5 补记 320 个 cell | 已证不双计；引擎已有现成路径 | 小，旗标默认关 |
| 5 | P3 门改统计判定 | ⚠️ **降级**：先补 3 seeds 与 ±5% 带，再看还剩多少噪声 | 中（有成熟先例）|
| 6 | P7 历史失效（时间窗 / 按修改关系） | 三档可选 | 中 |

⚠️ **P3 已从"最上游"降级。** 原排序建立在"噪声是方法的固有属性"之上；核了论文才发现
论文自带 3 seeds 与 ±5% 两个降噪装置而我方一个都没接。**先补论文规定的，再谈改门。**

⚠️ **e_pervar3 在跑（R14 在飞）**：3 与 4 改行为，必须旗标默认关，否则一旦崩溃重启会
中途换行为、废掉这一跑。且不得加 `Hyperparams` 字段（会写进每个 lock、破坏 resume 哈希比对）。

**⚠️ 全部修完，改的是"判断做得对不对"，不是"能力上限"。** 诚实预期：上线次数变多（稳）、曲线可信方差变小（稳）、**分数不会大涨**。

---

## 5. 撤回与修正记录（跨 session 必读）

| 曾经的主张 | 检验 | 处置 |
|---|---|---|
| "7 次上线全被 global 基线逼成 fork" | 逐次核回退来源，仅 R4 成立 | ❌ **撤回**，改 1/7 |
| "R15 的拒绝是噪声误杀" | 拒绝理由首句为 `no task improved`，零改进本就不会上线 | ❌ **撤回** |
| "门在结构上不可满足（约束集 > 可过题数）" | 两次拒绝均正当 | ❌ 撤回 |
| "僵尸变体堵住目标选择" | 是时序错配；目标上一轮都还有题 | ❌ 撤回 |
| "K=8 永不填满 ⇒ 退役永不触发" | `variant_count` 单调涨到 8，池确实填满 | ❌ 撤回 |
| "新生变体被饿死" | 新生 carry 7/7 增长；饿死的是被顶替的老变体 | ❌ 撤回 |
| "被替换的祖先永远回不来" | 会复活（V5 零两轮后 R11 拿走 52 题）；正确词是**休眠** | ❌ 撤回 |
| "N-10 修法 5–10 行、风险低" | 需重排/预览冻结，中等工程量 + 确定性风险 | ⬆️ 修正 |
| "N-06 新生冷启动值得修" | fork 轮的 0.5 从不被任何决策消费，单轮标定假象 | ❌ 毙掉（但换形态复活为 P5 的入场估计）|
| "冠军保留能挽回 5.8pp" | 无偏计分下 peak−final = 0.0000 | ❌ 归零 |
| "α 阈值是旱灾主因" | 9/9 轮任何 α 都继续，门从未触发 | ❌ 证伪 |
| "路由器需要加探索" | ECE 0.051、Brier 近伯努利地板 | ❌ 大幅削弱 |
| P2 排第 2 / P3 排第 3 | 见 §2 因果链 | 🔄 对调 |
| 丢弃 340 个 cell | 亲数 320 | 数字修正 |

**第二轮自查（2026-08-04，含首次核对论文原文 PDF 与 M 台账）**

| 曾经的主张 | 检验 | 处置 |
|---|---|---|
| P2 是"论文未明说范围/两读" | 原文 §4.5 p.11 + p.17 明写多变体时 per-variant | ⬆️ **升级为"论文明写、我方没做到"** |
| P3 是对论文的批评（噪声是方法固有） | Table 8 逐字给了 ±5% 与 3 seeds，我方两个都没接 | ⬇️ **降级**，改写为噪声地板测量 |
| P3 排第 2（最上游） | 同上 | 🔄 **降到第 5** |
| 「9 个出候选轮承载 9/9>0」是证据 | 候选只对 target 生成 + 空簇 `continue` ⇒ 恒真 | ❌ **不计入证据** |
| 目标承载 `82/36/21/…` | `routing` 是轮末快照；冻结时是 `103/39/52/…` | 数字修正 |
| 三次重测 SD 4.59 / 翻转 23.3% | 同配置窗口实际 5 轮 | 数字修正（4.31 / 33.0%） |
| imputed 曲线无偏且全轮可比 | R1 `imp_n=100`、R6 `102` | ⚠️ 加披露 |
| `peak−final = 0.0000` | 每轮带 ±3.2–4.3pp 噪声 | ⚠️ 改"与 0 不可区分" |
| `pool_report.curve` 一律不得引用 | APPLY 复用满足 M-07 三条件，合法 | 🔄 **收窄**：只禁 FORK 复用 |
| 「e_pervar3 峰终轮无保送块故不变」 | 确证 ✓，但逐轮取峰本身才是大问题 | ⬆️ 改用 epoch 聚合 |
| P8「病因未知」 | `candidate_accounting` 直接定死 | ✅ **已诊断**（M-18 + M-21） |
| P5 修法需新写补记逻辑 | `engine.py:591-595` 已实现，被 `recipe:4212` 关掉 | ⬇️ 工作量下修 |
| 「两个 lock 的 `reasoning_effort` 都是 null」 | s1k8b103 根本没这两个 key | 措辞修正 |
| 逐轮 gap 稳定 18–30 | R1=5 在区间外 | 数字修正 |

---

## 6. 与本目录其它文档的关系

| 文档 | 状态 |
|---|---|
| `01-CANDIDATES.md` | N-01/N-02′/N-03/N-04/N-05/N-07/N-09 仍有效；**N-06/N-08/N-10 三条以本文与 `06` 为准** |
| `02-DETECTION-PLAN.md` | 有效。配对+同窗交错、重放效度边界、功效表、A/A 空校准 |
| `03-EXTERNAL-PRECEDENTS.md` | 有效。**P3 的解法在 §(c)** |
| `04-PROBE-RESULTS.md` | 测量数字有效；**解释层已被 `06` 取代**；探针 1 的 peak−final 已被 `07` 作废 |
| `05-OFFLINE-RESULTS.md` | 有效（A–E 五项分析） |
| `06-POOL-PATHOLOGY-VERDICT.md` | 有效，但 §1.5"排除 vs 退役"的结论被本文 P4 修法取代（真正的修法是时序，不是排除） |
| `07-FORK-REUSE-CORRECTION.md` | 有效，= 本文 P6 |
| **`08`（本文）** | **权威索引** |

## 7. 复现脚本

```
experiments/analysis/novelty/
  a_selective_invocation.py   b_allk_interception.py   c_router_calibration.py
  d_edit_type_gain.py         e_cross_check_carriers.py
  f_fork_reuse_correction.py  ← P6（保送块修正；口径已被 h_ 取代，保留作对照）
  g_ever_solved_ratchet.py    ← P7
  h_config_epoch_curve.py     ← P6 新口径 + P3 噪声地板 + S2-5 的 ±5% 噪声带
  run_all.py
```
`h_config_epoch_curve.py` 用法：`python h_config_epoch_curve.py s1k8b103 e_pervar3`
（多跑时额外给出合并 SD 与功效表）。

P3/P5 的门测量丢弃统计仍为一次性内联查询，**尚未固化成脚本**（TODO）。
⚠️ **P2 的逐次 fork 回退来源已证可脚本化**：约 20 行，从 `candidate_diagnostics[*].archive_reason`
的 `regressed=[...]` 加逐轮 `active_pool_measurements` 即可复现全部 9 行（已验，9/9 命中，
且"谁都没解过"列恒为 0）。既然 1/7 是把 P2 排序的唯一定量依据，不该停在内联查询。
