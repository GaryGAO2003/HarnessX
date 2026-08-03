# 涨分杠杆：根因、排序与已判死清单

> 2026-08-03。全部零 API 成本，纯落盘产物重分析 + 源码核验。
> 本文回答的问题是**"哪里有分数"**，与 `08-PROBLEM-INVENTORY.md` 的**"哪里错了"**是两个问题。
> 🔴 本文撤回了 `06`/`h_pool_headroom` 的一条判词，见 §3.1。

脚本（均为本轮新增，读，零成本）：
- `experiments/analysis/novelty/h_pool_headroom.py` — 变体池互补性 / 路由 headroom + 噪声零假设
- `experiments/analysis/novelty/i_gate_noise_replay.py` — gate 决定的噪声审计 + 反事实重放
- `experiments/analysis/novelty/j_ceiling_headroom.py` — 步数墙 no-op 核验 + 预算耗尽分布

---

## 0. 一句话

**上线的编辑会被运行时静默丢弃，而 AEGIS 的确定性 gate 没有任何一关能察觉。** 这一条根因同时解释了本轮三个独立的"死胡同"，也是所有其他涨分动作的前置条件。

---

## 1. 根因：silent processor drop

### 1.1 代码

| 锚 | 事实 |
|---|---|
| `harnessx/core/harness.py:353-360` | `_instantiate_proc` = 裸 `except Exception: return None`，**零日志** |
| `harnessx/core/harness.py:586-590` | 拿到 `None` 实例**静默跳过**，变体退回原厂 stack |
| `runs/s1k8b103/R4/V2/pipeline/candidates/C-R4-01/config.yaml:34`<br>`runs/s1k8b103/R6/V3/pipeline/candidates/C-R6-01/config.yaml:34-40` | 两个独立致命点：① `_target_: file:///D:/PycharmProj/...` 的 Windows 盘符 URI 运行时解析不了；② 传了 `nudge=` kwarg，而 `CommitNudgeProcessor.__init__(warn_at_remaining, budget_floor, extend_only_when_low)` 不接受 → `TypeError` |
| `experiments/variant_pool/tests/test_processor_targets.py` | **仓库已有回归测试记录本案**："instantiated set byte-identical to stock baseline, zero matching lines across 408,880 log lines" |

### 1.2 meta-agent 的自述是错的（重要）

`learnings_V7.md:233` 声称 *"State is a frozen dataclass, so the assignment silently raised `FrozenInstanceError`"*。

**否证**：`harnessx/core/state.py:119` 是普通 `@dataclass`（无 `frozen=True`）；`:142` `max_steps` 是可变字段；`:161-168` `budget_exceeded()` 每次调用实时读 `self.step >= self.max_steps`。传播链逐跳核验，**没有一跳阻断**：

- `runloop.py:201` `TaskStartEvent(..., state=state, ...)` 传的是**同一个对象**，且 `state` 在 `run_loop` 内**从未被重新绑定**
- `runloop.py:206` `on_task_start` 在预算检查（`:257`/`:773`）**之前**触发
- `processor.py:770-786` / `:401-481` 按**引用**传递，无 deepcopy
- `harness.py:1226` 先从 `task.max_steps` 写 `state.max_steps`，**再**进 `run_loop` ⇒ on_task_start 的写会覆盖它，不会被它覆盖（同时否证 R6 给的第二种解释）

⇒ **真实失效 + 虚构机理 + 在虚构机理之上继续演化。** R6 当时还给过**第三个**互不相容的解释（`learnings_V4.md:137`）。

### 1.3 遥测独立坐实（不采信自述）

来源：`comparison.json → rounds[].attempts[]`（3090 条，206/轮）。⚠️ 记录顶层的 `steps` 是两次 attempt 之**和**，只能用嵌套的 `attempts[].steps`。

| 检验 | 结果 |
|---|---|
| V3（R4 设 `budget_floor=30`）+ V4（R6 设 55） | **822 次尝试，最大步数 = 20，超过 20 步 = 0** |
| 全跑 `exit_reason=="budget_exceeded"` | **1024 / 3090 = 33.1%**，其中 **1005 次卡在整整 20 步** |
| 超过 20 步的尝试 | 仅 **49 次（1.6%）**，最大 40 步，**全部在 R1 的 V0/V1**——CommitNudge 变体尚不存在 |
| R2–R15 每一轮 | 最大步数一律 20 |

**判定：CONFIRMED。** 若 `budget_floor=30/55` 生效，V3/V4 的耗尽应停在 30/55；实测全停在 20。

⚠️ R1 例外须记：其尝试跑到 40 步，而轨迹分母仍渲染 `/20`（`R1/active_pool/V0/.../0a3cd321...a2.md` 显示 `steps: 40/20`）。⇒ **R1 的分母不可信**，且 R1 早于任何 CommitNudge 变体，与 no-op 无关。

### 1.4 计数口径（引用时必须带）

| 数 | 口径 | 出处 |
|---|---|---|
| **1024 / 3090 = 33.1%** | 直接数 per-attempt `exit_reason` | `comparison.json` 逐条 |
| 994 / 3090 = 32.2% | 记录层 `budget_exhaustions` 字段求和（**少计 30**） | `pool_report.json:2-6`、`RUN-LOG.md:2557` |
| 40 / 206 = 19.4% | **settled 单轮**口径，勿与上面混用 | `pool_report.json` 顶层 |
| 323 / 1056 | **候选评测**独立作用域，不嵌套在 994 里 | `pool_report.json` `candidate_diagnostics` |
| 合计 ≈ 1347 / 4146 = 32.5% | active 逐条 + candidate 单元 | 二者相加 |

⚠️ `exit_reason=="budget_exceeded"` 是**三合一枚举**：`state.py:161-168` 的 `budget_exceeded()` 对 步数上限 / `TokenBudget(0.85)` / `CostGuard($120)` 返回同一个值，`runloop.py:777-778` 还把 `BudgetExceededError` 也归进来。**产物无法拆分**。逐条证据强烈指向步数墙：`traj_summary.txt` 的耗尽行 steps 列全是 20，成本 $0.4–2.4 ≪ $120，token 12–79 万未触 TokenBudget。

---

## 2. 它解释了什么

```
harness.py:353-360 裸 except  →  上线编辑静默不落地（+ M-27 空提示词 890）
        ↓
变体与原厂 stack 逐字节相同
        ↓
①  池子无真实差异  →  路由无燃料          （= 本轮"死胡同一"）
②  gate 在 no-op 周围测噪声 → "中性编辑"与"无编辑"不可区分（= 本轮"死胡同三"的一半）
③  meta-agent 从空结果学习、编造机理、在其上继续叠编辑（= misevolution）
```

`08-PROBLEM-INVENTORY.md` 的 P1–P8 全部位于这条链的**下游**。本文的根因应排在 P1 之前。

---

## 3. 撤回与修订

### 3.1 🔴 撤回："变体之间互相冗余、不互补"

`h_pool_headroom.py` 在 s1k8b103 上测得：置换零假设下观测 union **低于**打乱带 ⇒ 判"变体冗余"。

**该判词作废**——s1k8b103 的池按 M-27 记载是 **"池 8, 配置 3, 提示词 2, 空 890"**，V5/V6/V7 全程空提示词，且本文证明 V3/V4 的 processor 编辑从未装载。**测到的冗余是 silent-drop 的症状，不是变体池机制的性质。**

正确表述：**在我方已产出的池上没有可兑现的互补性；变体池的互补性尚未被真正测量过。** §4 的 S1 落地后必须用同一脚本重测。

### 3.2 🔴 撤回：「论文基线 73.8，我们 R0 64.08，低 10pp」

Table 5 四列是 **final / peak / Δ / tokens**：Global = 49.5 / **73.8** / −24.3；Ensemble = 87.4 / 87.4 / 0.0。
⇒ **73.8 是 Global 臂的 R4 峰值，不是任何基线。Table 5 没有 R0 列，论文的 GAIA H0 从未公开**（`PAPER-GAP-AUDIT.md:30` 早有此记载）。
（87.4 − 13.6 = 73.8 是巧合。）

### 3.3 模型不同，绝对水位不可比

`runs/s1k8b103/experiment.lock.json:83-88`：task agent = **`deepseek/deepseek-v4-flash`**，meta = `deepseek/deepseek-v4-pro`，端点 `litellm.yangtzeailab.com`。论文是 GPT-5.4 / Opus 4.6。**蓄意选择、已登记 M-15**（`MODEL-SELECTION-FLASH-VS-PRO.md`：留 failure margin + 便宜 3×；Flash 在 BrowseComp 53.5–73.2 vs 前沿 80–83）。

⇒ **追 87.4 这个绝对水位 = 跟一个刻意的模型选择对打，不在演化环里。** 可比的是**形状**（peak=final）与**臂间差**。

### 3.4 任务子集**是**可比的（修正早前假设）

`recipe/gaia_evolver/data/webthinker_gaia_dev.json`：103 题，**零题带 `file_name`/附件字段**，L1=39 / L2=52 / L3=12。论文侧同为 GAIA-Val-Text 103 text-only（`deepread/DR-B-GAIA-ABLATIONS.md:114`）。
⇒ 早前"论文含附件题、我们不含 ⇒ 不可比"的猜测**不成立**，子集不是差距来源。

---

## 4. 涨分杠杆排序

### S1 ★ 效力验证（efficacy verification）— 必须第一

**问题**：gate 的四关（manifest 完整性 / 配置规范化 / build-smoke / seesaw）没有一关能区分"编辑生效且中性"与"编辑被静默丢弃"。seesaw 查的是**结果**，而在 SD 4.59pp、23.3% 翻面的床上，no-op 的测量与中性编辑的测量无法分辨。

**修法（两段，都很小）**：
1. **止血**：`harness.py:353-360` 的裸 `except` 改为记录并**上抛可归因的实例化失败**；`:586-590` 遇 `None` 不得静默跳过。
2. **成关**：在确定性 gate 加一道**运行时效力断言**——应用候选后跑一次微探针，断言申报的改动在运行系统中**可观测**（processor 已实例化 / 提示词哈希变了 / 工具已注册）。

**仓库自带先例**：`manifest.py:68` `CODE_BUCKETS = frozenset({"tools","processor"})` 已声明**需要 Level-2 round-trip 证据**——这道关我方自己写在规格里，R4/R6 两条 processor 编辑还是空转着过去了。⚠️ **待核**：`test_processor_targets.py` 是在断言"现已修复"还是在记录史实缺陷，动手前必须先跑一次看它对当前 HEAD 是什么语义。

**论文侧立论**：§4.3 的 gate 序列写的是 *"build or smoke tests (when applicable)"*——`when applicable` 承担了全部含糊，论文没有任何**语义效力**检查。这是规格洞，不是我方独有缺陷。

**涨分机理**：15 轮里 7 次上线，已证至少 2 次（R4/R6）完全没落地，另有 M-27 记载的空提示词。**已测得的 +10.68pp 全部来自真正落地的那个子集。** 修完之后同样 15 轮上的是真编辑。

**顺带解锁**：S4。

---

### S2 净效应 gate（net-effect criterion）

**问题**：`gate.py:257-269` 的 `min_fork=(1,1)` 意味着**只要改进 ≥1 题就永不 REJECT**，回退多少题都不管。实测 s1k8b103 的 fork：

| 轮 | improved | regressed | 现行判定 |
|---|---|---|---|
| R6 | 4 | **21** | FORK |
| R7 | 2 | **14** | FORK |
| R13 | 3 | **15** | FORK |
| R11 | 2 | 4 | FORK |
| R2 | 3 | 3 | FORK |

`i_gate_noise_replay.py` 的反事实：净效应规则下 **7 个 fork 里 5 个变 REJECT**。⇒ **池子正在被净负编辑填充。**

**同时可关掉一个规格洞**：`experiment_lock.py:250` `noise_threshold = None`，`:254` `planned_noise_threshold = 0.05`——**论文 Table 8 的 ±5% 噪声阈值，我方计划了但从未接线**。

⚠️ **诚实预期**：这条大概率**压低**报表曲线、**提高**池子质量。且在 S1 落地前它是空谈（编辑本身是幻影）。

---

### S3 P4 目标选择时序

6/16 轮零候选，因为被选中的目标变体当轮承载 = 0（完美分离，零反例，离线重构 15/15 复现）。修法 = freeze 之后再预览路由，~15–25 行，只动 `run_variant_pool.py`，旗标默认关。
⚠️ **不得新增 `Hyperparams` 字段**（写进每个 lock、破坏 resume 哈希）——走 `_epsilon_provenance` 的 provenance 模式（`run_variant_pool.py:6216`，默认值返回 `None` 使 lock 逐字节不变）。

**涨分机理**：废轮 → 活轮，出货量上升。同样在 S1 之后才有意义。

---

### S4 S1 之后重测池 headroom（零成本）

`h_pool_headroom.py` 已写好。变体真正产生差异之后重跑；若 excess 越过噪声零假设，被 §3.1 撤回的路由/组合线**复活**。

---

### S5 步数墙 —— 弱杠杆，但值得一次诊断臂

`j_ceiling_headroom.py` 的分布（1024 次 active 耗尽，按 pooled p̂ 分桶）：

| 桶 | 占比 |
|---|---|
| settled-fail (p̂ ≤ 0.15) | **44.1%**（其中 p̂ = 0 恰好：148 = 14.5%）|
| p̂ < 0.20（更细） | **61.5%** |
| 争议带中上段 [0.50, 0.85) | **仅 13.1%** |
| settled-pass (p̂ ≥ 0.85) | 3.3% |

按 BE 率排序的头部任务几乎全是 p̂ ≤ 0.25（`d5141ca5` 96.7% p̂=0.000、`0e9e85b8` 90% p̂=0.000 …）。**撞墙最狠的就是最难的题。** p̂=0 且 BE ≥ 50% 的乐观转化集**只有 6 题**，而这 6 题在全部合并尝试里**一次都没过**。

⇒ **全局抬升上限会把额外算力花在基本解不出的题上。** 但：① 这条在本跑里**从未被真正测过**（唯一的干预是 no-op）；② `learnings_V7.md:251,253` 记有耗尽尝试在第 18–19 步仍在推进。⇒ 值得一次**便宜的诊断臂**（单次评测，不跑演化），不值得当主线。
⚠️ 任何"抬上限涨了 X pp"的陈述必须 **token-matched** 上报，否则就是拿算力换分。

---

## 5. 已判死（附理由，勿回锅）

| 想法 | 死因 | 证据 |
|---|---|---|
| **两次 attempt 派给两个变体** | ① 论文侧：pass@2 **就是**头条指标且是 oracle-OR，作者批评的正是它 *"masks sub-threshold regressions"*、想要**更严**的 all-k tripwire ⇒ 此举朝其批评方向再推一步；② 代码侧：`reporting.py:204` `pass_at_k` 在 `n<k` 时 raise、ledger 按 `(variant,task)` 攒 2-attempt 单元、`freeze_routing` 强制单承载 ⇒ 引擎内架构性堵死 | 论文 §6.1/§7.1/Fig4；Q1/Q3 代码追踪 |
| **精英保留 / best-so-far** | 无偏计分下 s1k8b103 `peak = final = 0.7476`，`peak−final` **精确为 0** | `07-FORK-REUSE-CORRECTION.md` |
| **噪声容忍 seesaw 当 throughput 杠杆** | `min_fork=(1,1)` ⇒ 现行 gate **从不**拒绝改进 ≥1 题的候选；所有真实 REJECT 都是 `improved=0`。反事实 **REJECT→APPLY = 0**（两个 run 均是）。噪声造成的是 **fork，不是 reject** | `i_gate_noise_replay.py` §3 混淆矩阵 |
| **追论文 87.4 的绝对水位** | 模型不同且是蓄意选择（M-15，deepseek-v4-flash vs GPT-5.4）；论文 GAIA H0 从未公开 ⇒ 绝对分不可比 | §3.2 / §3.3 |
| **更细的簇 / 逐题路由（作为独立主张）** | 成本近零（`--cluster-source capability --cluster-map` + `--cluster-min-size 1` 零代码；或已实现的 `routing_mode=task_tournament`），但**当前池无燃料**，且 n=1 时 Laplace `(0+1)/(2+2)=0.25` 被先验压死。⚠️ 归属 S4，S1 之后重判 | `h_pool_headroom.py`；`router.py:215`、`ledger.py:181` |

**保留但降级**：噪声本身是真的——improved / regressed 旗标各有 **63%** 预期属纯噪声，α=0.05 下真实回退仅 **1/88**，挡路的回退题 **72%** 是 p̂<0.3 的运气进榜。这仍是 S2 的动机，只是不能拿它讲 throughput。

---

## 6. 未闭项

1. **`test_processor_targets.py` 的语义**——断言"已修复"还是记录史实？动 S1 前必查（§4-S1）。
2. **e_pervar3（CH3 主跑）没有 `pool_report.json` / `comparison.json`**，只有逐轮 `pool_state.json`；聚合三元组（attempts / infra / budget）无从取得。抽查 150 条轨迹**同样全部 20 步封顶**。
3. **994 vs 1024 差 30**：记录层字段少计，原因未查。
4. **`budget_exceeded` 三合一不可拆**：步数墙 / TokenBudget / CostGuard 在产物里同枚举。若 S5 要做，须先加可区分的终止原因。
5. **374 次死搜索**只存在于 `CH3-REPRODUCTION-DRAFT.md:305-308` 的散文里，**无任何机读字段**；它不是独立桶，而是撞步数墙的**上游驱动**。
6. **s1k8b103 已被论文草稿弃用**（`CH3-REPRODUCTION-DRAFT.md:313-317`："did not deliver the evolved artefacts its configurations declared"）。本轮全部分析建立在它之上——**结论的机理层有效（根因在源码里），但任何逐轮数字都不得进论文正文**，须在 e_pervar3 或 S1 之后的新跑上复测。
7. **失败分类只有粗二分**（`reporting.py:73` `classified_failures = infra + budget`），无论文 Appendix D 那样的多类分布。富分类只存在于 meta-agent 的 `learnings_V*.md` 自由文本里。

---

## 7. 记账

本文所依据的三个脚本（`h_` / `i_` / `j_`）与 `08` 的 `f_` / `g_` 均**未提交 git**。按四表面纪律，提交时须同步：`experiments/docs/RUN-LOG.md` 台账 / `experiments/variant_pool/SPEC.md` §7.x 裁决 / `PAPER-METHODOLOGY-DEVIATIONS.md` 的 M-xx。

**S1 若动手，必须新开 `git worktree` 隔离施工**（活跑期改代码的既定纪律）。

**任何付费跑必须用户明令**。本文 S5 的诊断臂、S1 之后的复测，都只是提案。
