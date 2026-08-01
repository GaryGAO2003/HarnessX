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
| 10 | canonicalize / smoke | C1 门内 no-op;**实际由 `meta_agent.evolve` 内部的 `EvolveValidator` 执行**(`run_variant_pool.py:1364` 逐字:「The paper's gates (canonicalize → replay smoke → novelty → evidence) run *inside* `meta_agent.evolve`, not in the C1 gate」) | 论文:Evolver **提供** smoke test(「the Evolver **must also provide** a smoke test confirming that the processor instantiates and runs on synthetic input without raising exceptions」p.10);门**「when applicable」**执行 | **p.10, p.32** | ⚠️【亲 Aug-01】 | **改判 🔴→⚠️**。`replay.py:73` = "Synthetic-task replay gate",`:43` = "`ok=False` means the gate should reject",`agent.py:814` = 跑一个合成 smoke 任务、`exit_reason=error` 即拒;`run_variant_pool.py:1152` 逐字「候选**只有在 evolve 内部 replay smoke 通过后**才能到达此门,而通过的 replay 驱动真实 run loop 端到端 → 每个注册 processor 都被执行」。**实质符合且执行证据强于论文**(真 run loop vs 合成输入)。残留差异只有两条,**申报即可、不需补、不需重跑**:①粒度(论文要 Evolver 为每个新 processor 亲写专属 smoke;我方是统一合成任务 replay)②位置(门内 vs 上游硬门,而论文的「when applicable」已给余地)。⚠️ 附带小 bug:`agent.py:817` 的 Evolver prompt 仍宣传 `replay_mode=config_only`,但 `replay.py:7-13` 明写该模式**已移除** |
| 11 | **候选在哪些题上被评** | `T_k` = 路由给该变体的题 | 「a candidate targeting variant k is **tested only against tasks routed to k**」 | **p.11 §4.5** | ✅【亲】 | **这是 p.11 唯一明确规定的事,我们做对了** |
| 12 | **「以前做对过」查谁的账本** | `global`(全历史跨变体 `ever_solved`) | 正文只有一句:「must not regress any previously solved task **recorded in Tt**」(Tt=全局 trace store) | **p.8 §4.1** | ⚠️【双】 | 我方读法可辩护;但见 #13 |
| 13 | **↑ 所谓"第三种读法"** | 两个开关都不实现"跟上一轮比" —— **这是对的** | `regressions.md # tasks **worsened vs R<n-1>**`(附录 E.1 p.43 / Planner prompt p.30) | **p.30, p.43** | ⚠️【亲 Aug-01】 | **改判 🔴→⚠️,判词推翻**。逐字复核全文所有 seesaw/regress 出现处:**`regressions.md` 从来不是 gate 的账本,是给 Planner 的诊断产物**。三证:①E.1 中它与 `landscape.md`/`digests/`/`trajectories/` 并列于诊断层;②B.1 Planner 读它,理由是「hit-rate **only counts predicted-task improvements** … **regressions.md is the only place it surfaces**」(p.30);③B.1 Critic 查的是「**Evolver 有没有处理**」它列的回归(p.33),是对 Evolver 行为的策略检查、非 accept/reject。**§4.3 p.10 枚举的门四检查里没有它**。故「论文自相矛盾」不成立,我方不实现是正确的。**真空在别处**:论文**并行跑两套回归会计**(门用全局 `T_t` §4.1 p.8 / 诊断用 vs 上一轮 B.1+E.1)且**从不调和** —— 一个 edit 可以既过门又该上 `regressions.md`,论文无规定。**我方缺的是诊断那一套**:`regressed_unpredicted` 在 `run.py:1207`/`run_meta.py:711` 有,**`run_variant_pool.py` 零命中**(变体池臂无附带损害通道),须补(纯移植,可离线回填,不改决策流) |
| 14 | 「改进」判定 | `before_passes==0 and after>=1`,查**该变体自己**的格 | 「the edit improves some tasks without regressing any」;pass@2 二值翻转 | **p.11, p.23** | ✅【双】 | — |
| 14b | **↑「improves」的时间口径**(与 #12 对称的另一半空白) | **全史**:`before` 取 `SuccessLedger.cell()`,而 `record()` 是 `cell.passes += n_pass`(跨轮累加)⇒「该变体**历来从未**通过」 | **论文从未定义 "improves"**。§4.5 只说「the edit **improves some tasks**」;两处旁证反指向**逐轮**:附录 C「five of the seven tasks … **flipped to pass**」(p.37)、B.1 manifest「`tasks_will_unlock: [<**ALL_FAIL** -> …>]`」未说 ALL_FAIL 是本轮还是历来(p.32) | **p.11 / p.37 / p.32** | ⚠️【亲 Aug-01】 | **本表新增,与 #12 是同一处欠定的两半**。我方两侧都取全史 ⇒ improved 池**单调收缩**、regressed 池(`ever_solved` "stays … **forever**",`ledger.py:117`)**单调膨胀** ⇒ **一次幸运通过在两个账本上都不可逆**。实测(`analysis/improve_baseline_scan.py`,run `s1k8b103`):**V0 可改进池 R1=16 → R2=3 → R8=1 → R12=0**,自 R12 起其 39 题全部曾通过 ⇒ `improved=∅` ⇒ **settlement 被强制 REJECT,与候选无关**;fork 是唯一重置池的机制(子代新 cell),形成棘轮。反事实 `last_round` 口径下 **44 格中 18 格(41%)判定不同**,REJECT 17→3、FORK 19→28。**我方读法合法但最保守**,须声明。已写入 CH3 §3.8 |
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
2. **#14b「improves」的时间口径** —— **Aug-01 新增**。与 #12 是同一处欠定的两半,且已实测出后果(V0 自 R12 起被强制 REJECT)。既是论文空白,又直接决定 fork 率与 ship 率
3. ~~#13 回退基线第三读~~ —— **已结**(Aug-01 逐字复核:`regressions.md` 非门账本,我方不实现正确)。**衍生待办**:补 `run_variant_pool.py` 的 `regressed_unpredicted`
4. ~~#10 门内 smoke 未实现~~ —— **已结**(Aug-01 亲验:上游 `EvolveValidator` 的 replay smoke 是硬门且强于论文要求)。改为申报粒度+位置差异,**不需补、不需重跑**
5. **#38 GRPO scope 声明** —— 最便宜、必须做
6. **#24 / #6 / #17 / #23 / #26 / #27 / #28 / #14b** —— 一批"我方自填"参数与判据,统一在论文里列一节声明
7. 其余 ✅ 项可快速过

---

*证据等级为【单】的三条(#38 / #40 以及 #10 的部分)尚未经主循环复核,取用前请要求复核。*
