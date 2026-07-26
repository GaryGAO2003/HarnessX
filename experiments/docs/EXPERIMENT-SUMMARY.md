# HarnessX 变体池复现 · 实验总结

## ⚠️ CORRECTION / ERRATA INDEX（2026-07-25）

> 本索引是本文的当前权威读法。下列历史文字为保留实验演进记录而不删除，但已被明确纠正。完整的方法学缺口、我方工程选择和验证要求见 [`PAPER-METHODOLOGY-DEVIATIONS.md`](./PAPER-METHODOLOGY-DEVIATIONS.md)。

| Errata | 被取代的历史段落 | 纠正 |
|---|---|---|
| EXP-E01 | **§0 一句话结论**；**§4 核心结果**中“没有 mixed conflict，所以 fork 未触发”；**§5** 对该原因的判断 | 日志明确包含 mixed conflict：`forkprobe` R2 为 `1 improved + 1 regressed`；`forkprobe_p2` R2 为 `1+1`、R3 为 `2+1`。这些已观察 conflict 没有产生 fork 的直接原因是旧工程门槛 `min_fork=(2,2)` 将它们 REJECT，**不是没有 mixed conflict**。 |
| EXP-E02 | **§4** 中把两个探针都描述为“12 tasks × 5 rounds”及等价完整运行 | `forkprobe_p2` 不是五个完整 scored rounds：状态只有 R0–R3，R1 的 `evaluated_tasks=0`，报告曲线只有 R0/R2/R3。它最多提供三个 scored checkpoints，不能作为五轮完整实验解释。 |
| EXP-E03 | **§2.3、§4** 将 `forkprobe_p2` 的 R3 分数作为 final/peak；**§5** 基于该 final 的推断 | R2/R3 候选均被 REJECT。旧 reporter 错把被拒候选 R3 的 `0.6667` 报成 active-pool final/peak。该数只能保留为 candidate diagnostic，不能代表 settled pool 的最终结果。 |
| EXP-E04 | **§0、§5** 中“链路正确”与 fork/Ensemble 负结果的结论性表述 | 单元/集成测试证明的是实现契约，不是机制效果。已有探针既没有获得有效 fork 后的 Ensemble 池，也没有可靠的 settled active-pool final，故**尚未验证 Ensemble，不能据此判断 Ensemble 优于、等于或劣于 Global**。 |
| EXP-E05 | **§6 下一步**中把 `(1,1)` 仅列为未来想法；本文各处隐含的旧实现状态 | 当前默认已改为 `(1,1)`；已有真实 cluster API、`task_tournament` 兼容分臂、task/cluster macro retirement、两阶段 settle、独立 active-pool scoring，以及 CandidatePipeline/Critic 契约。当前候选 queue 上限仍是 per target/variant，不是论文 round-global `K_t=4`；真实 LLM adapter 和正式 `103 × 15 × 3` 实验仍未运行。 |
| EXP-E06 | **§2.1/§2.2** 对多候选、Critic 与 shipping “建成”的概括 | Algorithm 1 的 round-global `K_t` coordinator/target selector 尚未实现；当前 first-pass-wins 是按主文 Algorithm 作出的工程裁决，而 Appendix B.1 明述的 ranked bucket-disjoint multi-ship 尚未实现。两种 shipping 语义必须作为分臂，不能宣称已有唯一论文实现。 |
| EXP-E07 | **§2.1/§2.2** 对 Digester/AEGIS 调用链“建成”的概括 | CandidatePipeline 的结构化合约已测试，但 actionability `a_t < α` / empty-landscape 前置 short-circuit 尚未实现；空 briefs 仍可能进入 Evolver。故不得宣称 Algorithm 1 selective invocation 已完成。 |

**纠正后的唯一可支持结论：**旧探针暴露了 fork 门槛与报告边界缺陷；它们没有构成 Ensemble 效果实验。本文以下旧结论仅作为历史记录阅读，不再作为当前证据。

**日期**:2026-07-25  
**定位**:论文启发的跨模型复现(paper-informed re-implementation)——在 HarnessX(arXiv 2606.14249)官方开源仓上,用 DeepSeek V4(flash 内环 / pro 外环)替换论文的 GPT-5.4 / Opus 4.6,复现其 §4.5 变体池 / Ensemble routing 机制。

---

## 0. 一句话结论

**代码全部建成并验证链路正确;但两个探针实验一致显示:变体池的核心机制"fork"在 DeepSeek flash 上不触发,原因是该模型的演化"要么纯改善、要么无效",不产生论文机制赖以启动的"改善一批 + 弄坏一批"的冲突。** 这既是一个待进一步排除规模/门槛因素的负信号,也可能本身是一个有价值的负结果(论文机制的激活条件从未被测过)。

---

## 1. 目标与前提

- **复现对象**:论文 Table 5 的变体池消融——单一 harness(Global)会灾难性遗忘(GAIA 上峰值 73.8% → 终值 49.5%),变体池(Ensemble)通过"遇冲突不拒绝而分叉"保持不退化(终值 87.4%)。
- **关键前提**(精读发现):论文的变体池机制在官方开源仓中**零实现**——仓库只有单谱系爬山环(= 论文 Global 对照臂)。所以本工作是"补实现论文未开源的机制",不是运行现成代码。
- **跨模型定位**:因换用 DeepSeek,不与论文绝对分数对标;所有论文未规定的设计(K 值、成功率估计、簇定义、fork 继承、退役…)自行设计并作为贡献面。

---

## 2. 已完成的工作

### 2.1 代码建设(变体池全套,原体零功能改动)
分阶段建成,每阶段全离线单测,逐步 review:

| 阶段 | 内容 | 累计测试 |
|---|---|---|
| A | 变体池静态零件(池/账本/路由/门/manifest/证据链) | 188 |
| M0 前置 + pass@2 | 实验 lock / 输出契约 / 成本分账 / pass@k | 355 |
| C1 | 演化引擎(路由→评测→门→fork/退役 编排) | 376 |
| C2 | 接真实 GAIA 评测与 meta-agent 演化 | 383 |
| C3 | 逐变体 journal 隔离(防 novelty 门误杀 fork 兄弟) | 388 |
| C4 | 门两关裁定(确认与 evolve 内部门冗余) | **400** |

**对上游原体的改动仅 2 文件**:`run.py` 的 pass@2(430 行,`--pass-k 1` 默认 = 原版逐字节等价)、`litellm_provider.py` 的两个 bug 修复(40 行,只影响非 Claude 路径)。变体池逻辑约 12,900 行**全部在新增文件**,原体可 `git checkout` 完全复原。

### 2.2 正确性核心(代码结构保证,非测试碰巧)
- **routing freeze**:本轮路由在评测前冻结、只读先前轮账本 —— 防止"用本轮结果反向路由"使在线路由退化为变相 oracle(自欺);
- **K=1 结构性等价单谱系**:满 K=1 池无法 fork,是 Global 对照臂的干净地基;
- **seesaw 基准 = 全历史已解集**(非上一轮);
- **pass@2 忠实保留其局限**(2/2→1/2 不算回退),否则复现不出论文的崩塌机制。

### 2.3 实验(校准 + 探针)
- **6 题成本校准**:实测单次尝试 ~289k tokens、缓存命中率 90.9–93.2%、6 题 $0.055;
- **单谱系 pilot(20 题 6 轮)**:得首个时序 headroom **+10pp**(最强单轮 checkpoint 55% vs 各轮并集 65%),且独立复现了论文的峰值后退化(final < peak);
- **fork 触发探针 ×2**(见 §4)。

---

## 3. 关键发现(过程中)

1. **论文与代码多处脱节**:变体池机制零实现;论文的确定性门(canonicalize/replay/novelty)其实藏在 `meta_agent.evolve` 内部,不在正文描述的位置;pass@2 论文用但代码未实现;AEGIS 四阶段 evolver 全仓零命中。
2. **跨模型的真实坑(纯看代码发现不了,真跑才暴露)**:DeepSeek 拒收 `content: null` → 长任务全崩(已修);litellm 路径漏读缓存 token → 成本算错 27–66×(已修)。
3. **成本口径修正**:repo 硬编码 Claude Sonnet 价,失真数十倍;一律改用 token 自行计价。
4. **实验设计经对抗审查大幅加严**:预注册主假设(McNemar 配对检验,≥10pp 才算赢)、机制分离臂(隔离"论文机制 vs 集成红利")、held-out、探针先行 —— 详见 SPEC §9。

---

## 4. 核心结果:fork 机制未激活

两个探针,唯一变量是评测降噪(pass@1 → pass@2),其余全同(12 题分层、5 轮、K=3):

| 探针 | fork 触发 | 变体数 | 逐题噪声 |
|---|---|---|---|
| pass@1(单次评测) | **0 次** | 始终 1 | 3/12 题横跳(如 `0-1-0-1-0`) |
| pass@2(两次评测) | **0 次** | 始终 1 | **噪声消除**(横跳题稳定) |

**推论链**:
1. pass@1 探针 fork 不触发 → 起初怀疑是评测噪声掩盖了信号;
2. pass@2 探针**降噪成功但 fork 仍不触发** → **噪声不是主因**(最廉价的解释被证伪);
3. 逐题分析:全程 6 题稳定解出、3 题稳定失败(evolve 演化不出解法)、几题单向变化 —— **从无一次"这边改好、那边改坏"的拉扯**;
4. ⇒ **DeepSeek flash 的演化产生"纯改善或无效"的编辑,不产生论文机制赖以启动的冲突**。我们观察到的停滞是"evolve 改不动难题"(能力),而论文的停滞是"改善与回退互相拉扯被 seesaw 拒"(冲突)—— **fork 能救后者,救不了前者**。

---

## 5. 判断与负结果的价值

**这是需要正视的负信号**:连续两探针、排除噪声后 fork 仍零触发,揭示一个跨模型复现的真实风险 —— 论文机制在强模型(GPT-5.4)上有效,因强模型演化时会产生冲突;而便宜模型演化"太温和",变体池前提不满足。

**但尚未定论**,两个变量未排除:
- **规模**:12 题 5 轮太小,103 题 15 轮下冲突可能才出现;
- **门槛**:`min_fork=(2,2)` 可能挡掉了小规模的"改善1+弄坏1"。

**即便最终确认不激活,本身可能是可发表的负结果**:论文只在强模型上验证,从未测过机制的激活条件。"变体池依赖模型演化产生冲突,在弱/便宜模型上前提不满足"是诚实、有信息量的发现(NeurIPS SEA workshop 明确收 negative results)。

---

## 6. 下一步(便宜验证先行,不盲目上大实验)

连续负信号后,直接花 ~$230 跑正式主对比风险过高。优先排除剩余两变量:

| 步骤 | 成本 | 回答 |
|---|---|---|
| 读现有探针的 evolve 候选,确认"无冲突"是否系统性 | 免费 | 机理确认 |
| `min_fork=(1,1)` 重跑 | ~$3 | 是否门槛所致 |
| 30 题 10 轮更大探针 | ~$15 | 是否规模所致 |
| (若上述转正)正式主对比 K=1 vs K=8 × 3 seed | ~$230 | 论文主结论 |

---

## 7. 成本实测(锚定预算)

| 项 | 实测 |
|---|---|
| 单次任务尝试 | ~289k prompt tokens |
| 缓存命中率 | 90.9–93.2% |
| 单次尝试计费 | ~$0.011 |
| meta-agent | ~$0.09 / evolve 周期 |
| 6 题校准 | $0.055 |
| 12 题 5 轮探针 | ~$1.5(pass@1)/ ~$3(pass@2) |

repo 自报的 `cost_usd` 硬编码 Sonnet 价,失真约 66×,一律弃用。

---

*代码与实验产物在私有仓 `GaryGAO2003/harnessx-variant-routing` 分支 `exp/variant-pool`;实验设计冻结基线见 `SPEC.md` §9;原体改动台账见 §10。*
