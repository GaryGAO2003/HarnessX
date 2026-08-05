# 新纪元 baseline 发射配置(冻结草案)

> 建档 2026-08-05。状态:**草案,统一冒烟通过后冻结**。
> 记账说明:channel 合流 + F4 在途(另一工作流正在改 SPEC.md / RUN-LOG.md),
> 为避免与在途 merge 冲突,本文件先行承载裁决内容;SPEC §6.6 指针、RUN-LOG 条目、
> git 提交在合流落地后统一补(四表面纪律不豁免,只延迟)。

---

## 1. 目标选取裁决:`--target-strategy failure_density`(Aug-05,用户令「选取机制得更符合逻辑」)

| 项 | 内容 |
|---|---|
| 论文依据 | **无**——p.11 仅 "a candidate targeting variant k",k 如何选通篇未定义(conformance #28,⚠️ OURS,亲验);worst_first 与 failure_density 同为我方自填,替换零一致性成本 |
| 替换理由 | worst_first 的键 `variant_rollup` 含冻结格:fork 划走题后旧格永不更新、永远垫底 → 目标锁死复现 **12/12(e_pervar3)/ 15/15(s1k8b103)**;failure_density 只遍历 `routed_tasks` 活题,污染源结构性不存在 |
| 机制 | 取「当前路由题中从未跑通的题数最多」的变体(绝对质量,非比率);untried 计为 unsolved → 新 fork 子代天然被照顾;fork 后燃料跟随子代,锁死不成立 |
| 实现成本 | **0 行**——target.py 现成三选一(`STRATEGIES`),发射配置一行 |
| run lock 义务 | 按 SPEC §6.3/§6.6 口径记录:choice=failure_density,标 OURS,替换理由=#28 冻结格污染 |
| 备用 | round_robin(盲轮转)降为备用旗标;STATPOOL 矩阵目标键(Tier A 计数)= B 臂精化版,不入 base |

## 2. 路由粒度裁决:按簇分,不改(Aug-05)

| 理由 | 说明 |
|---|---|
| 论文机制本体 | 按簇路由是 §4.5 结构本身;#11「candidate 只在路由给 k 的题上测」以簇路由为前提。baseline 改粒度即不再是复现,CH4 臂失去对照锚 |
| 换簇不满足换 worst_first 的两条件 | 簇定义(GAIA by-level)确系 OURS(#20 🔴 未定义),但:①无现成干净替代(failure-mode 分区是候选,未验证);②无死锁级实锤病因(by-level 的病是软的:E1 测得 level 仅解释 6.9–10.6% 特化轴;F5 漏水仅 3 题) |
| 改分区的正确路径($0,已铺好) | `m_cluster_validation.py`:failure-mode 分区 vs **同尺寸随机分区 null**(防簇数 winner's curse)。分区赢 null → 升级为臂,不进 base |
| 簇数天花板由第二床解决 | GAIA 3 簇 → 有效演化池 min(K,3);BrowseComp problem_topic 10 类 → min(K,9)(BENCH2-SELECTION-AUG03 已裁) |

**题粒度的两个既设臂(不改 base)**:F5 三不管题 → `--pin-regressed`(STATPOOL D3);跨变体同轮证据 → overlap 额外 attempt 版。

**failure_density × 簇的已知互动**:3 簇下任意时刻 ≤3 个承载变体;failure_density 只会在承载者中选目标(无题变体 unsolved=0)。非缺陷:#11 规定无题变体本就不可被门评、不可演化。非承载变体的冷启动证据是 overlap/F2 臂的事。

## 3. 旗标冻结清单(草案,冒烟后定稿)

| 旗标 | 值 | 来源 |
|---|---|---|
| `--search-backend` | `serper_only` | §7.30,用户裁「原生的没法用」 |
| `--ship-confirmation` | `full_bed` | §7.32,用户裁「变体没好好测过」 |
| `--evolve-continuity` / `--evolve-abstain` / `--proposal-repair-retry` | 全开 | §7.33(P1–P3) |
| `--target-strategy` | `failure_density` | 本文件 §1 |
| `--planner-retry` | `2`(空白率 2/34≈6% → ~0.02%) | F4,§7.36 已落地(f9bc724) |
| `--candidate-load-feedback` | **开**(槽内 fail-closed 验证+失败反馈;**主修路径依赖 `--evolve-continuity`=开**,本表已开;continuity 关则仅评测期托底生效) | §7.37 已落地(9fae30a),观测审计 Q1 修复 |
| `--traj-failure-signals` | **开**(Aug-05 用户裁「这些肯定需要给」;观测通道完整性属基座,见 §3.5) | channel f8271dd |
| 并发 | 6(60 req/min 上限) | Aug-04 裁定 |
| `--pool-k` | 1(默认) | retire 换尺前不动 |
| K / 床 | 8 / GAIA-Text-103 | 沿用 |
| 其余旗标 | 一律默认(off) | 字节等同纪律 |

### 3.5 观测通道原则 + 边界(Aug-05 用户两连裁)

> 「让 meta agent 看到足够多——error warning、logs、工作记录」
> +「meta agent 层的不进去,只有 harness 里的进去;warning/error 是 harness 的问题就需要,是基建就不需要」

**边界规则:喂给 meta agent 的信息以 harness 为界。**

- **进**(harness 内部的一切):任务轨迹与失败形态(M-42,开)/ agent 工具调用失败 /
  evolver 自身工作记录(NOTES.md P1、ABSTAIN 理由 P2)/ 提案 schema 定向反馈(P3)/
  **自己上一个编辑没加载成功**(组件 fail-closed 报错——这是 harness 问题,必须到达下一次尝试);
- **不进**(实验机制层与基建):门判决及理由、池/路由/账本内部、serper 重试、429 等
  eval 基建噪声。机制层的病在机制层修(目标池烧干 → failure_density 自动绕开 / B 臂判定量修根),
  不喂给 LLM——兼防"评测机制泄漏"效度威胁(meta agent 若可见门内部,存在演化出 gaming 行为的通道)。

**双向审计结果(Aug-05,researcher 全查 + 主循环抽验三处承重代码)**:

1. **边界内完整性 = GAP(修复已派)**:fail-closed 加载失败只对操作者响亮(raise + ERROR 日志),
   不进任何 meta-agent 通道——continuity 循环只包 `slot_agent.evolve()`(:2566),评测期
   `_prepare_round_config` 的 raise 在圈外,永远到不了 NOTES/DECISION_REQUIRED;
   `_instantiate_proc` ERROR 仅日志;M-42 只数四个正文标记。
   **修复方案(遵 §3.5 既裁「必须到达下一次尝试」,不需新裁决)**:①主修——候选配置在
   **槽内提案时**即跑同一套 fail-closed 验证,失败文本走既有 DECISION_REQUIRED/NOTES 通道
   喂下一次尝试,重试耗尽转显式 abstain(理由=加载失败);②托底——评测期 raise 从「整跑硬崩」
   改判候选 infra-fail(**活动变体配置失败仍硬崩**,那是跑完整性事件)。旗标默认关字节等同,发射开。
   (审计建议之「DROPPED 计入 frontmatter」不做:fail-closed 网保证计分 rollout 零 drop,计数恒 0;
   vendored 内部 smoke 盲区由①的槽内验证顺带覆盖,不碰 vendored。)

2. **边界外零泄漏 = GAP(待用户裁,§5 按钮)**:always-on 一处——`_planner_brief_with_regressions`
   (:1833,无条件布线 :5560)把回退题单 + **Critic 否决规则逐字引文**("the Critic rejects the
   whole round otherwise…")并进 evolver brief;另 `critic_revision_request.reason`(Critic 判决理由)。
   分层:①机制引文与判决理由 = 按 Aug-05 边界明确越界;②回退题单 + tasks_at_risk 要求 =
   论文 App-B manifest 契约(全剥则 Critic 整轮否决回归,forceprobe1 R2 实证)→ **建议:留题单
   与中性要求措辞,削机制引文**;③llm-planner 侧泄漏点仅 `--aegis-planner llm` 模式活,默认
   deterministic 下休眠。serper/429/基建噪声全域零泄漏(干净)。

3. **S1 旁路 = PASS**:全部评测构造点(主评 / 门窗 / ship-confirm 全床 / active-pool / decomp
   :8674 / resume)均先过 fail-closed 网(:6007/:8674 唯二布线,亲验);唯一网外构造 =
   vendored meta_harness 内部 replay smoke(非计分路径)。**过审即效度声明素材:计分路径
   「演化侧对评测机制盲」在 2 的裁决落地后成立。**

## 4. 已知保留机制(故意不修,SPEC/发射记录标注义务)

1. **#14b REJECT 墙**(improved=全史首过 → 晚期 ship 停摆)——B 臂(STATPOOL 判定量)的靶,baseline 曲线即对照图一半。
2. **F5 簇粒度漏水**(回退题被路由回致害配置)——D3 臂(`--pin-regressed`)的靶。
3. **非承载变体冷启动**(≤3 承载者,其余账本停在出生态)——overlap/F2 臂的靶。

## 5. 发射前置检查清单

- [x] channel 合流 + F4 落地,全量 1112 绿(edde4fc / f9bc724,Aug-05 亲测复核)
- [x] 合流后观测审计完成(Aug-05):Q3 S1 旁路 PASS / Q1 边界内 GAP(修复在途)/ Q2 边界外 GAP(待裁)
- [x] Q1 修复落地(9fae30a,SPEC §7.37,全量 **1125 绿**主循环亲测):槽内验证走 DECISION_REQUIRED/NOTES 既有通道、验证失败不扣步数预算、耗尽转显式弃权;评测期候选降级 infra-fail、active-pool 仍硬崩
- [ ] **边界裁决(§3.5-2)**:回退题单保留、Critic 机制引文削不削——待用户一句话
- [ ] Serper 配额确认(BENCH2 自记「配额不够做演化跑」,先查余量/加购)
- [x] 脏树处置:l_rank +56 用户裁「需要的」已入库;m_cluster_validation.py / BENCH2 / 本文件同批入库
- [ ] **T1 最小机制双跑(§6)——K=8 机制跑兼任冒烟**,通过 → 本文件转「冻结」
- [ ] run lock 写入 §1/§2 两项裁决 + §6.3 题集
- [ ] 供给源裁决(§6.4,待用户)

## 6. 最小实验量级设计(Aug-05,用户令「跑全部的太贵太久,设计一个 minimal 的实验量级」)

**原则:机制命题用最小跑测(结构量,免疫分数噪声);分数命题只在幸存臂上花全床预算;凡处理发生在决策层的,一律离线反事实重放($0),不另开跑。**

### 6.1 三层漏斗

| 层 | 跑什么 | 能测什么 | 明示不测什么 |
|---|---|---|---|
| **T1 最小机制跑** | 30 题 × 10 轮,K=8 与 K=1 各一跑(同题集同种子,配对) | 机器全链路(**兼任冒烟**)/ 早期分化(出生宽度、真 ship/fork 数)/ REJECT 墙出现轮次 / K=8 vs K=1 的探索轮数与 token 总量对比(论文预言 2/3 的低噪声形态) | 一切 <~16pp 的分数差:30 题的 task_macro 噪声 ≈ 8.7pp × √(103/30) ≈ 16pp |
| **T2 全床锚** | 103 题 baseline K=8 **仅一次** | 分数级结论 + E1 特化矩阵 + 换定义救援重放 + 结构量正式版 | — |
| **T3 臂跑** | 每臂先 30×10 配对(vs T1 的 K=8,同题集) | 臂的机制效应(认证 ship 持续性、墙推迟轮次) | 臂的 pp 值——结构量赢家才升全床确认跑 |

### 6.2 成本账(参照 e_pervar3:103 题 × 16 轮 ≈ 4254 rollouts ≈ 11h @并发10)

| 跑 | rollouts 估算 | 相对成本 | 墙钟(@并发6) |
|---|---|---|---|
| T1 K=8 | eval 30×2×10=600 + 门窗 ~250 + 全床确认(30 题版)~200 ≈ **1050** | ~0.25× | ~4h |
| T1 K=1 | ≈ **900**(无 fork/确认分支) | ~0.21× | ~3.5h |
| T2 全床 | ≈ 4254 | 1× | ~15–18h |
| 每条 T3 臂 | ≈ 1050 | ~0.25× | ~4h |

旧计划(逐臂全床)≈ 4–5 个 e_pervar3 当量;漏斗后 ≈ **2–2.5 当量**,且第一批结论(T1 双跑)一夜之内出。

### 6.3 设计细则

- **题集**:30 题按簇分层抽样(L1/L2/L3 比例同全床),固定种子,题单入 run lock;**所有 T1/T3 跑复用同一题集**(配对设计,消床成分方差)。已知 flaky 题不排除——池燃烧机制正需要它们。
- **轮数 = 10**:30 题下 never-passed 池更小、REJECT 墙来得**更早**(预计 R5–8 可见),10 轮足以看到「早期分化 + 晚期墙」全形态;触发 idle 停机则提前收,更省。
- **种子 n=1**:结构量由机制强制(墙=账本数学必然),不靠运气;两跑 ship 数差 ≤2 记平局,判读含糊才补种子。
- **failure_density vs worst_first 不开双跑**:T1 用 failure_density 正跑,worst_first 用同账本**离线反事实重放**($0)证明其锁死——同一笔数据出两个结论。
- **E1 预登记**:发车前先用 `power_curve()`(本批入库的 +56)按 30 题的真实测量结构算检出下限,**预先声明 T1 的矩阵看不见多小的特化**;E1 正式判读留给 T2 全床矩阵。
- **判定规则(sequential)**:T1 K=8 过机器 + 见早期分化 + 见墙 → 冻结配置 → 发 T2;T1 哪里含糊才在哪里加预算;臂一律 T3 先行,结构量赢才花全床确认。

### 6.4 供给源选项(待用户裁)

本次本就是新纪元(基修全落地必须重冒烟),若**同时**切导师实验室 LiteLLM 端点(DS V4 双档,成本→0),两次纪元切换并成一次,「太贵」从根上消失,只剩时间约束(并发上探后可能反而更快)。代价:模型换代,与 e_pervar3 谱系跨模型不可比——但 CH3 锚定保持旧跑不受影响,新纪元本就是 CH4 的世界。**裁决点:换 / 不换。**
