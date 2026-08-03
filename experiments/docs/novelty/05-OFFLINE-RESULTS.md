# 05 — 离线分析结果（零 API 成本，只读已落盘 run 产物）

主床：`recipe/gaia_evolver/runs/s1k8b103`（16 轮：R0 引导 + R1–R15 演化，103 题，pass@2，3 个
GAIA-level 簇，`worst_first` 目标选择，`routing_mode=cluster`）。
交叉核对床：`s1k8`、`a1big5`、`e_pervar3`。

- 脚本目录：`experiments/analysis/novelty/`（全部新增，未改任何现有模块，未碰 runner）。
- 复现：见文末《复现附录》。所有脚本纯读 `pool_state.json` / `pipeline_audit.json` /
  `pool_report.json` / 数据集 json，零 API、零重跑。
- 口径纪律：真跑数字与重放/反事实数字分栏；反事实一律标 **re-scoring / indicative**；n 小处显式标 n。

---

## 0. 字段语义验证（先验证再算）

| 字段 | 验证方法 | 结论 |
|---|---|---|
| `active_pool_measurements[v][t]=[n_pass,n_att]` | 跨相邻轮比较同一 (v,t) 的 `n_att`（累计会增长）。脚本 B 步骤 1：315 对多轮 (v,t)，`n_att` 取值集合 = `{2}`，相邻轮增长 0 次 | **逐轮 pass@2 快照**（非累计）。这是"落地池"结算分 |
| `routing` 元素类型 | 直接取样 | key=`str`（变体 id），元素=`str`（task_id，uuid），非嵌套 |
| gate `before` | 读 `engine._task_eval`（L846-849） | **二值化**：变体历史上有过 pass → `(2,2)`，否则 `(0,2)`。这就是 any-k 看不见 `2/2→1/2` 的代码级原因 |
| 编辑效应 before/after | before=`active_pool_measurements[ptgt]`（父，无编辑，上线轮 `fresh_rollout`）；after=`candidate_gate_measurements[ptgt]`（候选，含编辑）。二者同轮、同谱系、仅差编辑、均 pass@2 | 用于 B/D 的编辑归因对照 |
| 簇映射（`cluster_source=gaia_level`） | run 产物无逐题 level；从 `experiment.lock.json.dataset.path` 指向的 `webthinker_gaia_dev.json` 取 `task_id→Level` | 3 簇 = GAIA level {1,2,3}，s1k8b103 分布 39/52/12 |

> 关键：**7 个 drought 轮（`no_candidate=True`）没有 `pipeline_audit.json`**（只有 `active_pool/` +
> `pool_state.json`），也没有落盘 actionability。它们的短路阶段只能靠 `candidate_accounting` +
> `routing` 反推——这直接决定了 A 的可算边界（见下）。

---

## A. 选择性调用瓶颈（最高优先）

### 结论（一句话）
**旱灾是"上游短路"，且不是 digester 的 actionability 门，而是"路由把目标变体饿死"。** `worst_first`
在每个 drought 轮都选中了本轮**承载 0 道题**的变体作目标；无题即无失败证据，候选管线从不请求 slot
（`requested_slots=0`，不写 audit）。降 α 救不了旱灾。真正的下游门拦只有 2 轮（R10、R15）。

### 逐轮表（脚本 A）
| R | no_cand | ship | 目标 | 目标承载题数 | requested_slots | actual | audit | a_t | α | 判定阶段 |
|---|---|---|---|---|---|---|---|---|---|---|
| 0 | ✔ | ✘ | – | – | 0 | 0 | – | – | – | bootstrap |
| 1 | ✘ | ✔ | V0 | 82 | 4 | 4 | ✔ | 0.9 | 0.5 | shipped |
| 2 | ✘ | ✔ | V0 | 36 | 4 | 4 | ✔ | 0.8 | 0.5 | shipped |
| 3 | ✔ | ✘ | V1 | **0** | 0 | 0 | – | – | – | **routing_starvation** |
| 4 | ✘ | ✔ | V2 | 21 | 4 | 4 | ✔ | 0.85 | 0.5 | shipped |
| 5 | ✔ | ✘ | V2 | **0** | 0 | 0 | – | – | – | **routing_starvation** |
| 6 | ✘ | ✔ | V3 | 60 | 4 | 4 | ✔ | 0.9 | 0.5 | shipped |
| 7 | ✘ | ✔ | V3 | 50 | 4 | 4 | ✔ | 0.85 | 0.5 | shipped |
| 8 | ✔ | ✘ | V4 | **0** | 0 | 0 | – | – | – | **routing_starvation** |
| 9 | ✔ | ✘ | V5 | **0** | 0 | 0 | – | – | – | **routing_starvation** |
| 10 | ✘ | ✘ | V4 | 12 | 4 | 4 | ✔ | 0.7 | 0.5 | **critic/gate（下游）** |
| 11 | ✘ | ✔ | V4 | 10 | 4 | 4 | ✔ | 0.7 | 0.5 | shipped |
| 12 | ✔ | ✘ | V4 | **0** | 0 | 0 | – | – | – | **routing_starvation** |
| 13 | ✘ | ✔ | V6 | 61 | 4 | 4 | ✔ | 0.9 | 0.5 | shipped |
| 14 | ✔ | ✘ | V6 | **0** | 0 | 0 | – | – | – | **routing_starvation** |
| 15 | ✘ | ✘ | V0 | 39 | 4 | 4 | ✔ | 0.9 | 0.5 | **critic/gate（下游）** |

阶段统计：`shipped=7, routing_starvation=6, bootstrap=1, critic_or_gate=2`。
`actionability_threshold_provenance = "OURS: configurable reconstruction parameter; the paper does not
specify one unique operational alpha"`。

**关键观察**：所有 9 个"真正跑了管线"的轮，actionability ∈ {0.7,0.8,0.85,0.9}，阈值恒 0.5，
`short_circuit` 全为 `None`——digester 的选择性调用门**从未**因 actionability 短路。所有 6 个非引导 drought
轮的目标变体承载题数都 = 0（`ptgt ∈ zero_task_variants` 100% 成立）。

### 反事实：α ∈ {0.0, 0.3, 0.5(实际), 0.7}（indicative / re-scoring）
只有落盘了 actionability 的 9 轮可被此门评估；drought 轮无 actionability（管线没跑），α 对其**不适用**。

| α | 9 个已评分轮里"继续"的数 | "被 digester 门短路"的数 |
|---|---|---|
| 0.0 | 9 | 0 |
| 0.3 | 9 | 0 |
| 0.5（实际） | 9 | 0 |
| 0.7 | 9 | 0 |

最小落盘 actionability = 0.7；边界规则"a_t ≥ α 继续"。⇒ **α 在观测区间内完全无区分力**：没有任何一轮的
digester 结局会因改 α 而改变。旱灾与 α 无关。

### A 的结论
主因 = **上游短路（路由饿死目标）**，不是下游门拦；且"给 actionability/选择性调用加探索"这条候选被证伪
——门根本没在拦。若要修旱灾，杠杆在 `worst_first × cluster 路由`的交互（目标被选中却无承载题），不在 α。

---

## B. all-k 拦截率

### 结论
现行 seesaw 是 **any-k**（`after≥1` 即视为"仍解出"），对 `2/2→1/2` 的**软回退结构性失明**（代码级原因：
gate 把 before 二值化为 `(2,2)`）。7 个上线编辑共造成 **20 次软回退（any-k 看不见）+ 17 次硬回退**。
若改 all-k（要求 succ 不下降），**7/7 上线编辑都会被拦**——因为 7 个上线全是 FORK，FORK 按定义至少带 1 道
硬回退。所以真正被"藏起来"的信号是那 20 次软回退。

### B1 快照 vs 累计（验证，写进结论）
脚本 B 步骤 1：`n_att` 全程取值集合 `{2}`，315 对多轮 (v,t) 中相邻轮 `n_att` 增长 **0** 次 ⇒
`active_pool_measurements` 是**逐轮快照**。后续 B 的所有计算据此按"同轮对照"处理（无需去累计化）。

### B3 每个上线编辑造成的软/硬回退（**精确计数**，真跑数字）
before=父变体本轮 `active_pool`（无编辑，`fresh_rollout`）；after=`candidate_gate`（含编辑）；
口径均 pass@2（`att=2` 一致）；scope = 父变体保留题（fork 移走的是"改进题"，按构造非回退）。

| 上线轮 R | 目标 | 子 | 对照题数 | 改进 | **软回退** | **硬回退** |
|---|---|---|---|---|---|---|
| 1 | V0 | V1 | 82 | 11 | 3 | 2 |
| 2 | V0 | V2 | 36 | 7 | 3 | 0 |
| 4 | V2 | V3 | 21 | 0 | 0 | 2 |
| 6 | V3 | V4 | 60 | 3 | 7 | 5 |
| 7 | V3 | V5 | 50 | 10 | 3 | 5 |
| 11 | V4 | V6 | 10 | 2 | 1 | 0 |
| 13 | V6 | V7 | 61 | 10 | 3 | 3 |
| **合计** | | | | | **20** | **17** |

### B4 反事实：all-k 会拦几个（**re-scoring / indicative，仅一步**）
拦截判据：对照集上存在 ≥1 次软或硬回退（任一 succ 下降）即拦。"后一轮分数变化"=子变体在 R+1 承载题上、
相对 R 轮池内该题分的均值 Δpass@2（单步、仅供参考）。

| 上线轮 R | all-k 是否拦 | 软 | 硬 | 后一轮题数 | 子变体均值 Δpass@2 (R→R+1) |
|---|---|---|---|---|---|
| 1 | 拦 | 3 | 2 | 64 | −0.328 |
| 2 | 拦 | 3 | 0 | 12 | +0.083 |
| 4 | 拦 | 0 | 2 | 64 | −0.031 |
| 6 | 拦 | 7 | 5 | 12 | +0.500 |
| 7 | 拦 | 3 | 5 | 12 | −0.583 |
| 11 | 拦 | 1 | 0 | 64 | +0.219 |
| 13 | 拦 | 3 | 3 | 64 | +0.094 |

⇒ all-k 下 **7/7 被拦**。注意：B3 是精确计数；B4 的"是否拦"与"后一轮 Δ"是重放，不与真跑并栏。
7/7 这个数本身不 informative（全 FORK 必带硬回退）；**真正的诊断信号是那 20 次软回退**——它们是 any-k
门在上线时完全看不到的分级退化。

---

## C. 路由器 Ŝ 校准

### 结论
路由器 Ŝ 在"实际发生的分配"上**整体校准良好**：ECE = 0.051，可靠性曲线贴对角线（Brier=0.217 接近 pass@2
的伯努利方差地板，不是校准信号）。⇒ **"给路由加探索"这条候选动机很弱**——估计已经跟得上实际通过率。
唯一的真偏置正是你标注的"fork 当轮新变体来源不同"：`candidate_reuse` 格（刚 fork 的子变体）预测 ~0.50 却
实际更高（ECE=0.205），路由器**低估新 fork**（与"需要更多探索"相反）。

### 方法
Ŝ(v, level) = `(Σpass+1)/(Σatt+2)`，对簇聚合**只平滑一次**（复核 `ledger.estimate_cluster`），证据取
截至上一轮的累计 `active_pool_measurements` 快照；与下一轮该 (v,level) 的实际每次通过率比较。这是从"落地池
结算信号"重建的忠实代理（路由器内部 ledger 可能还叠了 candidate-gate rollout，故非逐字复现）。

### 指标（脚本 C）
| 子集 | n_cells | Brier（每次加权） | ECE | 说明 |
|---|---|---|---|---|
| 全部 | 56 | 0.217 | **0.051** | 可靠性曲线基本贴对角线 |
| 有先验证据（prior_att>0） | 40 | 0.213 | 0.053 | 排除冷启动 0.5 先验后一致 |
| `fresh_rollout` | 45 | 0.216 | **0.063** | 校准良好 |
| `candidate_reuse` | 11 | 0.250 | **0.205** | 预测 0.50 → 实际 0.705，**低估新 fork** |

可靠性（全部，十分位，`mean_pred → mean_actual`）：`[0.5,0.6):0.525→0.448`，`[0.6,0.7):0.641→0.608`，
`[0.7,0.8):0.778→0.764`——主质量区间偏差 ≤ 0.08。

### C 的结论
路由估计本身够准，"加探索"弱动机。若要动，方向不是"多探索未知格"，而是"别用 0.5 冷先验低估刚 fork 的
子变体"。**口径**：格是"被 argmax 选中"的条件样本（选择偏差），未路由的 (v,level) 不可观测，故这是对
**已用估计**的校准上界，非全空间校准。

---

## D. 编辑类型 × 增益（n=7，仅观察，不做显著性）

### 结论
7 个上线编辑：**processor ×4、prompt ×3、config ×1、tool ×1**（R1 一个编辑同触 tool+prompt+config，计入 3
类）。观察到 processor 类的"后一轮"增益均值为正（+0.193，n=4），prompt 类为负（−0.273，n=3，受 R1/R7 拖累）
——**但 n 极小，纯观察，不构成任何结论**。

### 编辑类型来源（可审计）
优先取上线候选 `candidates.md` 的"What was shipped/Files created"里的**文件路径**（tools/*.py→tool、
templates/*.j2→prompt、processors/*.py→processor、config.yaml→config）；无文件段的用 manifest 的
`lever` 兜底映射（action→tool, instruction→prompt, control→processor，已文档化）。

| R | 目标 | 子 | 候选 | 解析编辑类型 | 依据 | 直接对照增益 Δpass | 后一轮增益 Δpass |
|---|---|---|---|---|---|---|---|
| 1 | V0 | V1 | C-R1-01 | config+prompt+tool | shipped_files（python_eval.py / gaia_agent_v2.j2 / config.yaml） | +0.098 | −0.328 |
| 2 | V0 | V2 | C-R2-04 | processor | lever=control（CommitNudgeProcessor） | +0.139 | +0.083 |
| 4 | V2 | V3 | C-R4-01 | processor | lever=control | −0.095 | −0.031 |
| 6 | V3 | V4 | C-R6-01 | processor | lever=control | −0.133 | +0.500 |
| 7 | V3 | V5 | C-R7-02 | prompt | lever=instruction | +0.040 | −0.583 |
| 11 | V4 | V6 | C-R11-04 | processor | shipped_files（processors/commit_nudge.py） | +0.100 | +0.219 |
| 13 | V6 | V7 | C-R13-01 | prompt | lever=instruction | +0.082 | +0.094 |

增益配到该子变体**实际承载题**上算（非全局分）。"直接对照" = after−before（编辑当轮的直接测量效应）；
"后一轮" = 子变体 R+1 承载题相对 R 轮池内分的均值 Δ。

### 每类型均值与极值
| 类型 | n | 后一轮增益（均/最小/最大） | 直接对照增益（均/最小/最大） |
|---|---|---|---|
| processor | 4 | +0.193 / −0.031 / +0.500 | +0.003 / −0.133 / +0.139 |
| prompt | 3 | −0.273 / −0.583 / +0.094 | +0.073 / +0.040 / +0.098 |
| config | 1 | −0.328 | +0.098 |
| tool | 1 | −0.328 | +0.098 |

> **n=7（每类型更小）。仅报观察，不做任何显著性宣称。** config/tool 各只有 R1 一例（同一多类编辑），
> 与 prompt 的 R1 分量完全同值，不可解读为"类型差异"。

---

## E. 交叉核对：承载变体数（我方待验结论）

### 结论
待验结论 **成立**：`routing_mode=cluster` + 3 个 gaia_level 簇下，argmax 把每簇题送给单一承载者，故
**非 fork 轮承载变体数恒 ≤ 3（=簇数）** 在全部 4 个 run 上都成立；fork 当轮可因新生子变体临时达 **4**。
"4"这个上界只在 s1k8b103 被实际打到（其余 run 太短/fork 太少，未累积到 4，但都不违反上界）。

| run | 簇源 / 路由 | 簇数 | 非 fork 轮承载数(max) | fork 轮承载数(max) | 判定 |
|---|---|---|---|---|---|
| s1k8 | gaia_level / cluster | 3 | 1（6 轮全 V0，从未 fork） | – | 成立（未触 4） |
| a1big5 | gaia_level / cluster | 3 | 2 | 3（fork@R1,R3） | 成立（未触 4） |
| e_pervar3 | gaia_level / cluster | 3 | 2 | 2（仅 fork@R2；R7 未落盘已跳过） | 成立（未触 4） |
| **s1k8b103** | gaia_level / cluster | 3 | **3** | **4**（fork@R4,R7,R11） | **成立（观测到 4）** |

s1k8b103 明细：非 fork 轮承载 ∈ {1,2,3}（如 R8/R9/R10/R15=3，R3/R5/R12/R14=2）；fork 轮
R4=4、R7=4、R11=4，R1/R2/R6/R13=2~3。**未证伪**；结构不变量（非 fork ≤ 簇数）四床全过。

> 口径：`e_pervar3` 的 R7 无 `pool_state.json`（末轮未结算），脚本自动跳过只读已结算轮；`s1k8` 整个 run
> 从未 fork，故"4"不可能被它证实或证伪。

---

## 复现附录（每个数字对应命令）

全部零 API、只读产物。在仓库根 `D:\PycharmProj\HarnessX` 下运行（Python 3.13）：

| 分析 | 命令 | 主要产出 |
|---|---|---|
| A | `python experiments/analysis/novelty/a_selective_invocation.py` | 逐轮阶段表、α 反事实、上/下游结论 |
| B | `python experiments/analysis/novelty/b_allk_interception.py` | 快照验证、软/硬回退精确计数、all-k 反事实 |
| C | `python experiments/analysis/novelty/c_router_calibration.py` | Brier/ECE、可靠性曲线、来源分层 |
| D | `python experiments/analysis/novelty/d_edit_type_gain.py` | 类型分布、每类型均值/极值、可审计依据 |
| E | `python experiments/analysis/novelty/e_cross_check_carriers.py` | 4 床承载数逐轮表 + 判定 |
| 全部 | `python experiments/analysis/novelty/run_all.py` | A–E 顺序全跑 |

- 换床：A–D 接第一个位置参数，如 `... a_selective_invocation.py s1k8`；E 接多个 run 名。
- 共享读取层：`experiments/analysis/novelty/_common.py`（字段语义注释即审计留痕）。
- 未改任何现有模块、未碰 runner；`git status` 仅显示两个新增目录。

## 局限与口径（统一声明）
1. **A**：drought 轮不落盘 actionability/short_circuit（无 pipeline_audit），其"digester vs planner"细分不可从
   产物区分；但 `requested_slots=0` + 目标承载 0 题已足以锁定"上游、路由饿死"。α 反事实仅对 9 个已评分轮成立，
   标 indicative。
2. **B**：before/after 为同轮"父(无编辑) vs 候选(含编辑)"对照，scope=父保留题；移走的改进题不入回退计数（按构造
   非回退）。软回退计数对**已测量值**精确；单个 `2→1` 含 pass@2 抽样噪声（不同 config 各 2 次 rollout），但这恰是
   any-k 门无论如何都看不到的。B4 为单步重放。
3. **C**：Ŝ 从 `active_pool_measurements` 重建（忠实代理，非逐字 ledger）；格为 argmax 条件样本（选择偏差），
   为已用估计的校准上界。
4. **D**：n=7，per-bucket 更小；processor/prompt 的方向性差异**不可**解读为类型效应。编辑类型解析优先文件路径、
   兜底 lever 映射，逐条依据已打印可审计。
5. **E**：短 run 未触及"4"不构成反证；结构不变量（非 fork ≤ 簇数）四床全过。
