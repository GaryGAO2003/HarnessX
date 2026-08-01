# AEGIS 复现符合性对照表(2026-08-01)

> 用途:逐行审查我方实现与论文 arXiv:2606.14249 的每一处机制差异。
> **页码 = 该 PDF 的印刷页码(与 PDF 页序一致)。**
>
> **判定**:✅ 一致 · ⚠️ **论文未规定,我方自填**(必须在论文里声明)· 🔴 不一致(需修或需申报)· 📋 已有 M-xx 台账
>
> **证据等级**:【亲】= 主循环直查 PDF/代码 ·【双】= 双盲两路一致 ·【单】= 单路报告未复核
>
> ⚠️ 通用提醒:论文对 **K_t**(每轮候选数,有下标)与 **K**(变体池上限,无下标)是**两个不同符号**,勿混。

---

## 一、循环与阶段

| # | 机制点 | 我方实现 | 论文怎么说 | 位置 | 判定 | 处置 |
|---|---|---|---|---|---|---|
| 1 | 适应循环骨架 | 逐轮:冻结路由 → 评测 → 四阶段 → 门 → 结算 | Algorithm 1「AEGIS Harness Evolution Loop (selective invocation)」 | **p.9** | ✅【双】 | — |
| 2 | 轮预算 T | `T=16`(为得满 15 适应轮) | 「each experiment runs for up to **T=15** evolution rounds」 | **p.15** | 📋 M-24【亲】 | 论文里声明"适应轮口径" |
| 3 | R0 是否演化 | R0 纯基线,不进引擎 | 「In round 0, the baseline is a competent composed harness…」;**是否 ship 未明说** | **p.29 §A.4** | ⚠️📋 M-24【双】 | 已申报 |
| 4 | 四阶段 | Digester/Planner/Critic **实跑用 llm**;Evolver 恒 LLM | 「four stages … **all driven by the same meta-agent LLM**」 | **p.9** | ✅【亲 lock】 | ⚠️ 代码**默认**是 deterministic,勿把默认当实跑 |
| 5 | 选择性调用门 | 可行动性 α / 空 landscape / 零候选 三处短路 | 「Digester, Planner, Evolver each gate on a continuation condition」 | **p.11** | ✅【双】 | — |
| 6 | **α 阈值** | **0.5** | Algorithm 1 收 α 为输入,**全篇无数值**,`a_t` 定义与量纲亦无 | **p.9** | ⚠️【双】 | lock 已写 "OURS",论文须声明 |
| 7 | Critic 返工 | ≤1 次 | 「After **at most one** revision cycle」 | **p.10** | ✅【双】 | — |
| 8 | 早停 patience | **16** | 「early stopping after **P=3** consecutive rounds without a shipped edit」 | **p.15** | 📋【亲】 | 已申报;p3 停点可后验重建(实测 R10/60.2%) |

## 二、门与 seesaw(**争议最集中区**)

| # | 机制点 | 我方实现 | 论文怎么说 | 位置 | 判定 | 处置 |
|---|---|---|---|---|---|---|
| 9 | 门的检查序列 | manifest → canonicalize → smoke → seesaw,首错即停 | 「manifest completeness, configuration normalization, build or smoke tests, and the seesaw constraint. **The first failing check halts**」 | **p.10** | ✅【双】 | — |
| 10 | canonicalize / smoke | **默认 no-op**(委托上游 evolve 内部校验) | 论文要求「smoke test confirming the processor instantiates and runs」 | **p.10, p.32** | 🔴【双】 | **门内无对应实现**,须申报或补 |
| 11 | **候选在哪些题上被评** | `T_k` = 路由给该变体的题 | 「a candidate targeting variant k is **tested only against tasks routed to k**」 | **p.11 §4.5** | ✅【亲】 | **这是 p.11 唯一明确规定的事,我们做对了** |
| 12 | **「以前做对过」查谁的账本** | `global`(全历史跨变体 `ever_solved`) | 正文只有一句:「must not regress any previously solved task **recorded in Tt**」(Tt=全局 trace store) | **p.8 §4.1** | ⚠️【双】 | 我方读法可辩护;但见 #13 |
| 13 | **↑ 第三种读法** | **未实现** | 附录目录写 `regressions.md # tasks **worsened vs R<n-1>**`;Planner prompt 同 | **p.30, p.43** | 🔴⚠️【双】 | **论文自相矛盾**;我方两个开关(global/per_variant)**都不实现"跟上一轮比"** |
| 14 | 「改进」判定 | `before_passes==0 and after>=1`,查**该变体自己**的格 | 「the edit improves some tasks without regressing any」;pass@2 二值翻转 | **p.11, p.23** | ✅【双】 | — |
| 15 | 门里有没有"改进检查" | 有(`if not improved: REJECT`) | 枚举的四步门**没有**改进检查,但 §4.5 又要求判定改进 | **p.10 vs p.11** | ⚠️【双】 | 论文自身不一致,我方取 §4.5 |
| 16 | APPLY / FORK / REJECT | 零回退→APPLY;有改有坏→FORK;无改进→REJECT | 「(1) improves some without regressing any → **applied to its target variant**;(2) improves a subset while regressing others → **forks a new variant**」 | **p.11** | ✅【双】 | — |
| 17 | `min_fork` 阈值 | `(1,1)` | **论文无此概念** | — | ⚠️【亲】 | 我方引入,须声明 |
| 18 | 噪声阈 ±5% 是否进门 | **不进**(门逐题零容忍) | Table 8 列 noise threshold「ignored single-round pass-count delta ±5%」,**未说是否作用于门** | **p.29** | ⚠️📋 M-25【双】 | 已申报为分析层口径 |

## 三、路由与池

| # | 机制点 | 我方实现 | 论文怎么说 | 位置 | 判定 | 处置 |
|---|---|---|---|---|---|---|
| 19 | 路由规则 | 各簇取历史成功率最高的变体 | 「routing each task to the variant with the **highest estimated success rate on that task's cluster across prior rounds**」 | **p.11** | ✅【亲】 | — |
| 20 | **簇是什么** | **GAIA 三档难度**(L1 39 / L2 52 / L3 12) | 🔴 **全篇从未定义路由簇**。主循环亲扫:64 处 "cluster",**零处定义**。附录只列 GAIA **失败类型簇**(blocked-source 39% / reasoning 33% / figure-visual 11% / doc-table 11% / scope 6%),且**未说那是路由簇** | **p.11 / p.18 / p.37-39** | 🔴⚠️【亲】 | **本表分量最重的一条**。三簇 winner-takes-all 一次换 52 题,直接决定漂移形态 |
| 21 | 路由统计口径 | 簇级 Laplace `(p+1)/(a+2)` | p.11 说「on that task's **cluster**」,p.17 说「highest **prior success rate**」——**粒度两读** | **p.11 vs p.17** | ⚠️【双】 | 论文自身不一致 |
| 22 | 历史窗口 | 全历史(`--routing-window` 未设) | 「across **prior rounds**」 | **p.11** | ✅【亲】 | — |
| 23 | 平局处置 | `fewest_attempts` → 低 id | **未规定** | — | ⚠️【亲】 | 我方自填 |
| 24 | **池上限 K** | **8** | **全篇无数值**(主循环正则扫零命中);只有「up to K variants (Vt ≤ K)」 | **p.11, p.17 Table 5** | ⚠️【亲】 | 我方自选,须声明 |
| 25 | 满池时 | 先退休最弱者再 fork | 「retiring the **lowest-performing variant** if the pool is full」 | **p.11** | ✅【双】 | 本跑零退休(未触上限) |
| 26 | 退休指标 | `task_macro`(逐题成功率宏平均) | 只说 "lowest-performing",**指标与窗口均未定义** | **p.11** | ⚠️【双】 | 我方自填 |
| 27 | fork 如何播种 | 子代继承父配置;**改进题从父转移给子** | **完全未规定**(新变体初始配置、领哪些题、target 如何选) | — | ⚠️【双】 | 我方自填 |
| 28 | target 变体选谁 | `worst_first` | **未定义** | — | ⚠️【亲 lock】 | lock 已写 "OURS" |

## 四、评测协议

| # | 机制点 | 我方实现 | 论文怎么说 | 位置 | 判定 | 处置 |
|---|---|---|---|---|---|---|
| 29 | pass@k | pass@2,任一成功即解出 | 「Each task receives two independent attempts per round (**pass@2: solved if either succeeds**)」 | **p.15, p.29** | ✅【双】 | — |
| 30 | 每轮评全量? | 是,103 题固定 | 「The **full task set is evaluated every round (no subsampling)**」 | **p.15** | ✅【亲】 | — |
| 31 | 床与配比 | 103 题,L1/L2/L3 = **39/52/12** | 「GAIA uses a fixed **103-task set** drawn across the three difficulty levels **(39/52/12)**」 | **p.28 §A.2** | ✅【亲】 | **逐数字对上** |
| 32 | 每轮候选数 K_t | **4** | Table 8:「K_t \| candidates proposed per round \| **4**」 | **p.29** | ✅【亲 lock】 | ⚠️ 但 p.31 的 Evolver prompt 说"你自己决定 K≥1"——论文自身不一致 |
| 33 | **单发 / 多发 ship** | **单发** | Algorithm 1 是单发(`break`);**附录 B.1 明写多发**(bucket-disjoint 可同轮齐发) | **p.9 vs p.34** | ⚠️📋 M-17【亲】 | **论文自相矛盾,已写进 ch2 正文** |
| 34 | 种子数 | **1** | Table 8:seeds = **3** | **p.29** | 🔴📋【亲】 | 已声明为限制;**方差不可估** |
| 35 | infra 失败处理 | 计为失败,不重采(58 次) | 「Rollouts terminated by infrastructure failures … **count as failures** rather than being excluded」 | **p.29** | ✅【亲】 | — |
| 36 | 噪声判据 | 我方实测 SD 4.57pp,±2SD=±9.1pp | 论文自述:**「±8.5% 二项 95% CI at n=103」**,并以此论证 −24.3「far exceeds」噪声 | **p.17** | ✅【亲】 | **两把尺几乎重合,可互证** |
| 37 | 模型 | meta=deepseek-v4-pro,task=deepseek-v4-flash | meta=Claude Opus 4.6;task=Sonnet 4.6 / GPT-5.4 / Qwen3.5-9B | **p.15** | 📋【亲 lock】 | 跨纪元**只可比形态,不可比数** |

## 五、范围外(必须显式声明,否则被问"为什么只复现一半")

| # | 机制点 | 我方实现 | 论文怎么说 | 位置 | 判定 | 处置 |
|---|---|---|---|---|---|---|
| 38 | **Harness-Model 协同演化(GRPO)** | **零实现** | 论文**整个第 5 章** + §6.5 实验 | **§5, p.18** | ⚠️【单】 | **在 Ch3 scope 段明确排除** |
| 39 | 其他四个 benchmark | 零实现 | ALFWorld 134 / WebShop 100 / τ³ 三域 / SWE-bench 55 | **p.15 Table 3** | ⚠️【亲】 | 同上,声明只做 GAIA |
| 40 | 伪影目录树 | 折叠进 `pipeline_audit.json` | 附录 E.1 是扁平文件树(INDEX.md / journal.md / decision.md / regressions.md / digests/ …) | **p.43** | ⚠️【单】 | 数据都在、形态不同;**建议只做派生渲染器,不动引擎** |

---

## 审查建议顺序

1. **#20 簇的定义** —— 唯一一条既是论文空白、又直接决定我方曲线形态的
2. **#13 回退基线第三读** —— 论文自相矛盾,且我方两个开关都不覆盖
3. **#10 门内 smoke 未实现** —— 论文明写要求,我方委托上游,须申报
4. **#38 GRPO scope 声明** —— 最便宜、必须做
5. **#24 / #6 / #17 / #23 / #26 / #27 / #28** —— 一批"我方自填"参数,统一在论文里列一节声明
6. 其余 ✅ 项可快速过

---

*证据等级为【单】的三条(#38 / #40 以及 #10 的部分)尚未经主循环复核,取用前请要求复核。*
