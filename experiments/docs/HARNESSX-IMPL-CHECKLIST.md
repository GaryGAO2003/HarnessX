# HarnessX 变体池施工清单 v2 —— 论文优先版

## ⚠️ CORRECTION / ERRATA INDEX（2026-07-25）

> 本文主体是 Jul-23/24 的历史施工计划，保留原文用于追踪决策演进；它不再单独代表当前实现或方法学裁决。完整 gap ledger 与验收门见 [`PAPER-METHODOLOGY-DEVIATIONS.md`](./PAPER-METHODOLOGY-DEVIATIONS.md)。

| Errata | 被取代的历史段落 | 当前权威裁决 |
|---|---|---|
| CHK-E01 | **§1** 将 `±5%` 称为 noise threshold；**§7** 建议以 `(2,2)` 控 fork noise | 论文没有定义 fork 的任务计数门槛，也没有说明 `±5%` 如何映射到 seesaw/fork。`(2,2)` 是我方旧工程选择，且实际挡住了 `1+1`、`2+1` mixed conflict。当前默认 `(1,1)`；`(2,2)` 仅作消融。 |
| CHK-E02 | **§3 W7** 的三种“簇”解释与 routed/self-cluster 裁决 | cluster 构造仍属论文空白。当前主接口要求注入真实 `task_id -> cluster_id` 映射，GAIA recipe 暂用 level 作为可审计代理；`task_tournament` 是单独的兼容/消融模式，不是 cluster 定义。 |
| CHK-E03 | **§4 W4** 对 full-set 与 scoped gate 复用的未决描述 | 候选只在 scoped routed tasks 上 gate；settled active pool 必须在固定全任务集上独立计分。只有同配置、同 carrier、完整相同子集时可安全复用；REJECT 候选结果绝不能进入 final/peak。 |
| CHK-E04 | **§2 主施工表、§6 里程碑、§9 摩擦点**中的待建/接线状态 | 默认 `(1,1)`、cluster API、task/cluster macro retirement、两阶段 settle、独立 active-pool scoring、CandidatePipeline/Critic 契约均已实现并有测试。旧工作表继续保留，但状态需按下方“当前实现状态”读取。 |
| CHK-E05 | **§7** 对 pass@2 的正当化若被读成充分指标 | pass@2 是论文评估要求，但会掩盖单次成功概率下降。正式报告必须并列 pass@1、底层 rollout 计数、逐任务 paired outcome 和分母。 |
| CHK-E06 | **§11 开放缺口**以及全文中“按论文来”的强表述 | `K`、cluster、估计器/窗口/冷启动/tie-break、fork noise、继承/退役/孤儿/freeze、跨 cluster 退役可比性、新任务路由等仍没有唯一论文答案。所有补全项必须标为 **我方工程选择**，写入 lock，并经预注册 ablation；不得写成论文参数。 |
| CHK-E07 | **§0/§1/§2 W12** 将 `K_t=4` 直接映射为当前多候选实现 | Algorithm 1 L15 的 `K_t` 是整个 round 的候选集合。当前 API 的 queue limit 是 per target/variant，遍历活跃池可膨胀到 `4 × active_variant_count`，不能冒充论文设置。论文又未给 target selector；正式主臂仍需“一轮一个预注册 target、全局至多 4 个候选”的 coordinator。 |
| CHK-E08 | **§0、§2 W16** 的 Critic/ship_ranking 与 **SPEC §2.4** 首过者语义 | Algorithm 1 L21–25 的 first-pass single-ship 与 Appendix B.1 p.34 的 bucket-disjoint multi-ship 相互冲突。当前 first-pass-wins 只是按主文作出的工程裁决；Appendix multi-ship 未实现，必须作为独立消融。 |
| CHK-E09 | **§2 W12/W16、§6** 若把 CandidatePipeline 完成状态读成完整 selective invocation | Algorithm 1 的 actionability `a_t < α` / empty-landscape 前置 short-circuit 尚未实现：当前 Digester 无 round-level actionability artifact，空 briefs 也可能继续进入 Evolver。结构化 pipeline 合约已测试，但不得据此声称这一调用策略已完成。 |

### 当前实现状态

**Implemented / tested**

- fork 默认 `(1,1)`；真实 cluster API 与 `task_tournament` 兼容分臂。
- task-macro / cluster-macro / raw retirement；prior-round routing freeze 与两阶段 settle。
- candidate gate 与 settled active-pool 全任务评分分离，REJECT 不污染 headline。
- CandidatePipeline/Critic 的结构化候选、隔离、确定性去重/排序、最多一次 revision 与 audit 契约；当前 per-variant first-pass queue 行为已有测试，但不含 actionability selective short-circuit。

**Integrated but not live-tested**

- GAIA recipe 的 level cluster、cluster routing、独立 active-pool scorer、分母/错误分类和 provenance lock 已接线并通过自动化测试，但尚未完成真实 provider 的端到端 smoke；接线仍缺 round-global target selector / `K_t≤4` coordinator，不能标作完整论文主臂。

**Not yet run**

- 真实 LLM Digester/Planner/Evolver/Critic adapter 的 live recipe 接入与运行。
- actionability artifact/threshold 与 empty-landscape 前置 no-op gate。
- round-global target selector / `K_t≤4` coordinator，以及 Algorithm first-pass 与 Appendix bucket-disjoint multi-ship 两个分臂。
- 预注册 Global vs Ensemble 对照、held-out 验证，以及论文规模 `103 tasks × 15 rounds × 3 seeds`。
- cluster、估计器/窗口、fork threshold、retirement、pass@1/pass@2 和 live-web/replay 的正式消融。

> 下一步逐项验收 checklist 见方法学台账 §5。以下原施工表均按“历史计划”阅读。

> **设计原则(用户裁定 Jul-23):一切按论文来。凡我方设计与论文冲突,一律采用论文的方法。**
> 依据:PDF 逐字精读(正文 §4–§6 + 附录 A/B.1/B.3/C)+ 本地仓 `D:\PycharmProj\HarnessX` 逐文件核验。所有判断带 `文件:行号` 或 `PDF 页码`。
> 前置事实:论文变体池机制在官方 repo **零实现**;repo 真实现 = 单谱系 best-so-far 爬山环 ≈ 论文 Table 5 的 **Global 对照臂**。
> v1(Jul-22 缩水自选版)已作废,差异见 §10。

---

## 0. 论文优先带来的范围变化(先读这一节)

采纳论文优先后,**三块此前被判为"可选/降级"的内容重新成为必做**:

| 项 | v1 判定 | v2 判定 | 依据 |
|---|---|---|---|
| 多候选 Evolver(每轮 4 个候选) | M2 可选 | **必做** | Algorithm 1 L15(p.9);Table 8 `K_t=4`(p.29) |
| Critic 阶段 + ship_ranking | 省略(曾引 Table 6 为据) | **必做** | §4.3(p.10);Algorithm 1 L19–24 |
| pass@2 双 rollout | 省略(repo 无 pass@k) | **必做** | §6.1(p.15);A.3 公式 6(p.29) |

⚠️ **成本与工期含义**:这三项把 M1 从约 1000 行扩到约 1900 行,API 成本因 pass@2 翻倍。其中 **Critic + 多候选 ≈ AEGIS 重建**,而论文 Table 6 自证四阶段相对单 agent evolver 仅差 1.0pp(落在一个标准误 ~3.3pp 内),贡献是约 12% token 效率而非准确率。**已按指令纳入,单列为 W12/W16 便于随时取舍。**

---

## 1. 与论文对齐的实验参数(Table 8 p.29 + A.2/A.3 p.28-29)

以下全部照抄论文,不再自选:

| 参数 | 论文值 | repo 现状 | 动作 |
|---|---|---|---|
| 每轮候选数 `K_t` | **4** | 1(`agent.py:646` 每轮只写一个 config) | 改造(W12) |
| 随机种子 / 谱系数 | **3** | — | 编排层设定 |
| 评测 rollout | **pass@2**(2 次独立) | 1 次(无 pass@k 参数) | 新建(W17) |
| 噪声阈值 | **±5%** 单轮 pass 数波动 | `pass_count_noise_threshold` 概念一致 | 对齐取值 |
| GAIA max-steps | **20** | 默认 15;GPT-5 预设 40 | 传参对齐 |
| 任务并发 | **10** | `--concurrency` | 传参对齐 |
| 元 agent 并发 / 步上限 | **4 / 200** | `--evolve-steps` | 传参对齐 |
| 轮数 T / 早停 P | **15 / 3** | 有 `--num-rounds`,**无早停** | 新建(W15) |
| `H_0` | 手工 competent harness + 床专属工具注册表 | `make_gaia_builder_gpt5` | ✅ 已吻合 |
| GAIA 任务集 | **103 题 text-only,难度 39/52/12** | 我方脚本按 task_id 取前 N | 改**分层抽样**(W18) |
| 基础设施故障 rollout | **计为失败**,不剔除(A.3) | — | 评测脚本须遵守 |
| 变体池容量 `K` | **全文未给** ⚠️ | — | 我方定义并报告(见 §11) |

---

## 2. 主施工表 v2

图例:🆕 = v2 新增(论文优先所致);⬆️ = v1 已有但范围扩大。

| # | 工作项 | 类型 | 接入点 | 论文依据 | 需自己定的决策 | LOC | 依赖 | 里程碑 |
|---|---|---|---|---|---|---|---|---|
| W0 | oracle 天花板离线聚合 | 新建 | `recipe/gaia_evolver/oracle_ceiling.py` ✅ **已完成** `6a84dd7` | — | 变体来源 | 30-80 | — | M0 |
| W1 | 变体池状态容器 | 新建 | 新 `variant_pool.py`;替换 `run.py:633,672` | §4.5 p.11 `V_t ≤ K` | **K 取值** | 120-220 | — | M1 |
| W2 | 路由器 argmax | 新建 | 新 `router.py`;接 `run.py:723` | §4.5 p.11 | **冷启动** | 100-180 | W1 | M1 |
| W3 | 成功率账本 | 新建 | `estimator.py` | §4.5 p.11 | **估计口径/窗口** | 80-150 | W1 | M1 |
| W4 | 逐变体范围收窄评测 | 改造 | `run.py:723-774` | §4.5 p.11;§6.3 p.18 | 与全集测量的分工(§4) | 100-180 | W1,W2 | M1 |
| W5 ⬆️ | 三路 fork 门 | 改造+新建 | 替换 `_score_and_gate`(`run.py:1174`) | §4.5 p.11 | fork 继承规则 | 120-200 | W1,W3,W17 | M1 |
| W6 | 变体退役 | 新建 | `variant_pool.py` | §4.5 p.11 | **退役指标** | 30-50 | W1,W3 | M1 |
| W7 ⬆️ | 簇定义 | 视选型 | `router.py` | §4.5/§6.3 **三读法并存** | **须裁决+消融**(§3) | 0-200 | W2 | M1 |
| W8 | 编排引擎抽取 + 多变体报表/tracer | 改造 | 抽 `run.py:683-974` | — | 引擎边界 | 120-220 | W1,W4,W5 | M1 |
| W9 | 逐变体 journal 隔离 | 改造 | `run.py:655-664`;`check_novelty`(`validate_workflow.py:694`) | —(repo 自有约束) | novelty 作用域 | 50-100 | W1 | M1 |
| W10 | per-variant **token** 记账 | 改造 | `run.py:780,1160` | — | 无(用 token 不用 cost,§5) | 30-60 | W8 | M1 |
| W11 | brief 的 Pareto 约束收窄 | 改造 | `agent.py:765-773` | §4.5 收窄语义 | 措辞 | 20-40 | W9 | M1 |
| **W12** ⬆️ | **多候选 Evolver `{H̃^k}_{k=1..4}`** | 大改造 | `MetaAgent.evolve`(`agent.py:538-678`) | **Alg.1 L15;Table 8** | 候选↔目标变体配对 | 300-500 | W5,W8 | **M1** |
| **W13** 🆕 | **change manifest 结构** | 新建 | 新 `manifest.py` + evolve 产物契约 | **B.3 Table 9 + YAML schema p.35-36** | 无(照抄)+ 目标变体字段 | 80-150 | W12 | M1 |
| **W14** 🆕 | **目标变体选择策略** | 新建 | `router.py` / 编排 | §4.5 "targeting variant k"(**机制未给**) | **全部**:轮转/最差优先/失败密度加权 | 60-120 | W1,W3 | M1 |
| **W15** 🆕 | **早停 idle ≥ P** | 新建(小) | 编排引擎 | Alg.1 L29;§6.1 P=3 | **计数作用域:全局 or 逐变体** | 30-60 | W8 | M1 |
| **W16** 🆕 | **Critic 阶段 + ship_ranking** | 新建 | 新 `critic.py`,接 evolve 与门之间 | §4.3 p.10;Alg.1 L19-24 | 排序判据具体化 | 200-350 | W12,W13 | M1 |
| **W17** 🆕 | **pass@2 双 rollout** | 改造 | `run.py:723-774` `_run_one` | §6.1 p.15;A.3 公式 6 | 无(照抄无偏估计量) | 60-120 | — | **M0+M1** |
| **W18** 🆕 | **GAIA 子集分层抽样 39/52/12** | 改造 | `experiments/build_gaia_subset.py` | A.2 p.28 | 无 | 20-40 | — | M0 |
| **W19** 🆕 | **attribution_signature 核对** | 新建 | `critic.py` + 轨迹扫描 | **B.3 p.36**;p.35 "falsifiable" | 无(schema 已给) | 80-150 | W13,W16 | M1 |
| **W20** 🆕 | **预测外回退台账** | 新建 | 编排 + journal | **Planner prompt p.30**(k-aware 回退清单) | 无 | 60-120 | W5 | M1 |
| **W21** 🆕 | **seesaw 基准改全历史已解集** | 改造 | W3 账本 + W5 门 | **§4.1 p.8 逐字** "any previously solved task recorded in `T_t`" | 无 | 40-80 | W3,W5 | M1 |
| W22 🟡 | bucket 声誉/命中率台账 | 新建 | 编排 | Planner prompt p.30 | 窗口大小 | 60-120 | W20 | M1 可选 |
| **W23** 🆕 | **slot 复制(K 份 tool registry/workspace/sandbox)** | 新建 | `variant_pool.py`(W1 内) | §3.1 p.6 slot=配置级单例;D2/D4 最高频编辑(§3.3) | 变体分叉时哪些 slot 需独立、哪些可共享 | 60-120 | W1 | M1 |
| **W24** 🆕 | **Evolver Level-2 往返验证** | 新建 | `manifest.py`/evolve 产物契约 | prompt p.32;C-R10-02 实例 p.37 | 无(照抄 "_prepare_messages 内容存活" 检查) | 60-120 | W13 | M1 |
| **W25** 🆕🔴 | **Digester + 持久证据链** | 新建 | `evidence.py` | §4.3 p.10;§E.1 p.43 | Digester 压缩指令(prompt 未公开) | 150-300 | — | M1 |
| **W26** 🆕🔴 | **实验元数据 lock + H0 冻结** | 新建 | `experiment_lock.py` | Codex 7;A.4 competent H0 | 无 | 60-120 | — | **M0** |
| **W27** 🆕🔴 | **完整确定性门五关**(非仅 seesaw) | 改造+新建 | `gate.py`(§2.4 修正) | §4.3 p.10 逐字五关 | 无 | 120-200 | W13 | M1 |
| **W28** 🆕 | **主动反奖励黑客** | 新建 | 评测层+`critic.py` | §6.6;附录 C R10 | 交叉验证是否启用 | 80-150 | W19 | M1 |
| **W29** 🆕🔴 | **评测输出契约**(final/peak/曲线/pass@1/分层) | 新建 | 报表层 | §6.1/§6.3/§7.7 | 无 | 60-120 | — | **M0** |
| **W30** 🆕🔴 | **成本校准分账 + Level 分层** | 改造 | 校准脚本 | §4.3;Codex 10 | 样本量 | 40-80 | — | **M0** |
| **—** 🔴 | **routing freeze**(本轮前冻结,禁反向路由) | 契约 | `router.py`(§2.3/§6.2) | Codex 5:正确性核心 | 无 | 入 W2 | W2 | M1 |

---

## 3. 必须由我方裁决的一项:簇的定义(W7)

论文对 cluster **从未正式定义**,证据支持三种读法:

| 读法 | 证据 | 工作量 |
|---|---|---|
| **(a) 路由诱导的任务划分** | §4.5 "a candidate targeting variant k is tested only against **tasks routed to k**";§6.3 p.18 "its target cluster" | W7 = 0 |
| (b) 元 agent 生成的失败模式分组 | Planner prompt p.30 "recurring failure modes … **your own grouping**";"name the neglected bucket and **the cluster it would target**";C.1 p.36 "grouped the 23 failed tasks **by failure mode**";附录 D 全部图表 "Failure clusters" | 150-200,需 meta-agent 输出可复用簇 id |
| (c) 语义/难度分层(如 `GAIATask.level`) | §4.5 字面 | 低,但**≈ 论文的 "Domain-aware clustering"**,p.18 明列为**另一种** pilot 策略 |

**论文优先无法裁决自身歧义。** 裁定:**主臂采 (a)**——它是 §4.5 同段的字面机制且零额外成本;**(b) 与 (c) 作为消融臂**。
⚠️ v1 曾写"`GAIATask.level` 是现成 cluster key,W7 可归零"——**该结论作废**:level 属读法 (c),是论文自己列为待探索的另一策略,不能当主臂。

---

## 4. 论文自身的矛盾:评测范围(影响 W4 与预算)

- §6.1 p.15:"The full task set is evaluated every round (**no subsampling**)"
- A.2 p.28:"The same evaluation set for each benchmark is **re-scored at every round**"
- §6.3 p.18:"each edit evaluated **only against its target cluster** rather than the full task set"

**调和读法(采纳)**:**轮次测量**用全任务集(A.2 明证),**候选门控**用目标簇(p.18 明证)。
**预算含义**:W4 的 token 节省**不来自减少轮次评测**,而来自"候选门控只测子集"+"减少无效提议"(p.18 原话 "avoiding the wasted proposals")。**预算按全集每轮重测计算。**

---

## 5. 成本核算的硬约束

`_estimate_cost`(`runloop.py:947-949`)**硬编码 Claude Sonnet 价格**($3/M 入、$15/M 出),`:452` 无条件调用,无 provider 真实成本分支。DeepSeek V4 flash 下报告值约为真实的 **27 倍**。

| 受影响处 | 结论 |
|---|---|
| `cost_usd` / `round_cost` | ❌ 失真,**一律改用 `total_tokens` 自行计价**(W10) |
| CostGuardProcessor | ⚠️ 实为伪装成美元上限的 token 上限($2 ≈ 476k tokens) |
| 门的 `cost_weight` | ✅ 不受影响——用相对比值 `(round_cost−best_cost)/best_cost`(`run.py:1219`),常数倍率约掉 |

---

## 6. 里程碑与预算(论文优先重算)

锚点:论文 143.7M tokens(Global,GAIA-103,15 轮,**pass@2**)⇒ **46.5k tokens/单次尝试**。采纳 pass@2 后按论文 token 量级直接对齐。
DeepSeek V4(litellm 已收录定价,无零成本注册风险):flash $0.14/$0.28,pro $0.435/$0.87。

| 里程碑 | 内容 | 规模 | 预算 |
|---|---|---|---|
| **M0** | W0 ✅ + W17 + W18;3 谱系 Global 基线 → oracle 天花板 | 3 × 103 题 × 15 轮 × **pass@2** | **$130–310**(见下修正) |
| **M1** | W1–W24 全部(见 §2 主表) | 同上 + 变体池 | **约 $200–400** |
| M2 | 簇消融 (b)/(c) 臂 + 其余消融 | — | 视消融数 |

**⚠️ M0 预算修正(第三轮精读后)**:此前 $95 基于"143.7M = 内环计费 token"的错误口径。实际 143.7M 是**轨迹体量/元 agent 消耗**(§7.5 反推:单次任务计费 ≈332K,内环真实总量 ≈1,026M,是 143.7M 的 7 倍)。抵消因素:DeepSeek V4 缓存读比未命中便宜 50–120 倍、缓存写免费,agent 循环前缀稳定命中率高。两者相抵,**M0 真实区间 $130–310,由缓存命中率主导**。

**6 题校准的三个目的**(必须先跑,再定 M0 最终预算):
1. 实测缓存命中率(唯一大杠杆);
2. 实测单次尝试计费 token;
3. **实测 DeepSeek V4-flash 在 GAIA 上的通过率是否显著高于能力地板**(附录 D.5:Qwen3.5-9B 在 SWE 上 hit-rate 塌到 0.05,"演化无法累积";内环模型太弱 → 变体无互补性 → M0 假阴性)。

---

## 7. 为什么 pass@2 不能省(W17 的正当性)

§6.1 p.15 逐字:pass@2 的目的是 "reducing sampling noise **while preserving a binary per-task signal for the seesaw constraint**"。

fork 的触发条件是"改善一批、**同时**弄坏另一批"。单次评测下,**纯随机波动就会持续制造这种混合结果**,后果:变体池被噪声撑爆 → K 迅速耗尽 → 退役频繁触发 → 机制退化为随机漂移。
即:**省掉 pass@2 会让变体池机制在噪声上空转**,这是正确性问题而非精度问题。

**补充防线(我方设计,须报告)**:fork 触发的最小规模门槛,如 `|改善集| ≥ 2 且 |回退集| ≥ 2`。

---

## 8. 可直接复用的既有件

| 论文概念 | repo 对应 | 位置 |
|---|---|---|
| 逐题 delta(fork 判据原料) | `compute_attribution` flipped/regressed | `journal.py:605` |
| 冒烟测试(新 processor 必附) | `run_synthetic_task_smoke_gate` | `replay.py:64` |
| 配置规范化 | `canonicalize` | `harness.py:944` |
| manifest 的"预期影响"雏形 | `predicted_affected` / `regressed_unpredicted` | `journal.py` / `run.py:827-904` |
| 门按序检查、首个失败中止 | `EvolveValidator` | `validate_workflow.py:857` |
| 变体 config 拷贝(廉价且独立) | `HarnessConfig.copy` | `harness.py:929` |
| 逐题 pass/fail 落盘 | `comparison.json` | `run.py:978` |
| 并发安全(每任务独立 runtime) | `_instantiate_runtime` | `harness.py:999` |

---

## 9. 架构摩擦点

| 摩擦 | 位置 | 冲击 |
|---|---|---|
| 演化环内联 `main()`,**4 份带漂移副本** | `run.py`;同 recipe 内 `run_meta.py:403` 第二份门;tau2 用 4 元组+reward;tb2 无门 | W8;但**只改 gaia 一条线即可** |
| 单 config / 单 best 元组 | `run.py:633,672` | W1 |
| 门以聚合通过率判定 | `run.py:1174` | W5 |
| **novelty 门读全局 journal,误杀 fork 兄弟变体** | `check_novelty`(`validate_workflow.py:694`),签名 `:688-691` | **W9 必做** |
| evolver 每轮只产一个 config | `agent.py:646` | W12 |
| tracer 每轮单例 | `run.py:697` | W4/W8 |
| `_rt_procs` 不可序列化 | `harness.py:591-593` | W1:变体持久化必须走 `_target_` 路径 |

---

## 10. v1 → v2 变更表

| 变更 | 原因 |
|---|---|
| W12 多候选 Evolver:M2 可选 → **M1 必做** | Alg.1 L15;Table 8 `K_t=4` |
| 新增 W16 Critic | §4.3;论文优先(尽管 Table 6 自证其对准确率无显著贡献) |
| 新增 W17 pass@2 | §6.1;且为 fork 正确性前提 |
| 新增 W13 manifest / W19 attribution_signature | B.3 Table 9 + YAML schema |
| 新增 W14 / W15 / W20 / W21 | Alg.1 L15,L29;§4.1 seesaw 定义;Planner prompt |
| 新增 W18 分层抽样 | A.2 的 39/52/12 |
| W7 簇:判"可归零" → **三读法并存,须裁决+消融** | 附录 Planner prompt 与 Failure clusters 证据 |
| `GAIATask.level` 当 cluster key 的结论 **作废** | level 属读法 (c),即论文另列的 Domain-aware clustering pilot 策略 |
| 预算 M0:$53 → **$95** | pass@2 使内环翻倍 |
| 成本口径:`cost_usd` → **`total_tokens`** | `_estimate_cost` 硬编码 Sonnet 价 |
| 移除"AEGIS 缺失是效度威胁" | Table 6 自证单 agent evolver 与 AEGIS 差在一个标准误内;且 v2 已纳入 Critic/多候选 |

---

## 11. 仍开放的信息缺口(论文未给,我方须定义并作为贡献报告)

1. 变体池容量 **K**;
2. 成功率估计 `Ŝ` 的口径(平滑 / 窗口 / 陈旧格子处理);
3. 路由器冷启动与探索策略;
4. fork 时新变体的任务与历史继承规则;
5. 退役的 "lowest-performing" 度量;
6. 候选的目标变体选择机制(W14);
7. idle 计数在变体隔离下的作用域(W15);
8. 簇的正式定义(§3,已裁定主臂 + 消融臂)。

**这八项即 M1 的科研贡献面**:不是复现既有机制,而是补完论文留白的设计并给出带消融的方案。
（补读 researcher 若在剩余章节找到其中任一项,须回填本节并相应下调贡献主张。）
