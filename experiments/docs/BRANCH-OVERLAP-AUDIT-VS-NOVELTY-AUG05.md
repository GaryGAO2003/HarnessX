# 分支治理审计:audit 线 vs novelty 线的 overlap 与问题(Aug-05 2026 深夜)

**任务**(用户令):系统性总结 `fix/audit-ideas-7-12`(audit 线)与 `fix/novelty-s4`(novelty 线)有无 overlap 及问题;先 review 仓库,再读论文最新原文与官方库,后行动。
**三源输入**:①仓库盘点(researcher 全量 + 主循环抽验;s4 @`d440e8b` 27 commits ahead,porcelain 净);②论文 **v3 逐字**(arXiv 2606.14249v3,Jul-23,43 页,researcher 逐机制提取,PDF 存 scratchpad);③官方库主循环 L4 直验(六轴 + counterfactual 生产者侧 + voting 补扫,ref 锁定)。
**一句话结论**:两分支**文件层 overlap 一处(良性)、判词层冲突一处(已裁决并修正)、概念层 overlap 密集且结构性**——⑦–⑫ 的"诊断"与"修复"横跨两线,需要一条明文归属规则,否则 ⑧↔s 脚本、⑫↔o 脚本、⑦↔m_cluster_validation 三对已在重复立项边缘。

---

## 1. 分支内容快照

| | fix/novelty-s4(主仓权威) | fix/audit-ideas-7-12(本分支) |
|---|---|---|
| 领先 main | 27 commits,52 文件 +10256/−225 | 1 commit(+修正批),纯文档 |
| 构成 | evolver 修复线 8(SPEC 7.30–7.37)/ novelty 分析 5 / docs 10 / channel 合并 2 / tip `d440e8b` 混合 | 原审计落盘 + VERIFIED 亲验记录 + 本文档 |
| 代码 | **全部代码在此**(variant_pool + 测试 + 分析脚本) | **零代码** |
| 关键资产 | novelty/ 候选库 01–12(06/07/08/09/10/11 带 ⭐/🔴)、两代 o–s 脚本、RUN-LOG 台账 | 六轴证据基线、counterfactual 两层裁决 |

T1 K=8 状态(RUN-LOG:2875):解冻已达但**仍待发车令**;prefix-vote go/no-go **未终局**(+19pp 中 8.92pp 纯缓存已自撤,+6.66pp `exit=done` 规则存活,批次标"analysis only,SPEC 无裁决零配置变更")。

## 2. 文件层 overlap(一处,良性但需治理)

`experiments/docs/AUDIT-IDEAS-7-12-VS-UPSTREAM-AUG05.md` **同时提交于两分支**(s4 `d440e8b` / 本分支 `6063052`),当前**逐字节一致**(`git diff` 空 → 合并无冲突)。风险不在现在而在**双主进化**:任一侧改动即分叉,且本分支 VERIFIED 文档已对原审计 §4.2 做了修正(两层裁决),"原文两分支同文 + 修正只在一支"的割裂已经发生。→ **已执行分开(Aug-05 深夜,用户令):s4=canonical,本分支移除重复副本(`git rm`,历史见 6063052),两分支共享文件数归零;后续对原审计的任何修订只在 s4 做。**

另:`experiments/analysis/novelty/` 存在**两代同字母脚本**(doc-12 批 `o_prefix_voting/p_ratchet_violation/q_budget_censoring/r_effective_n/s_effective_votes` vs batch-2 `o_decorrelation_headroom/p_prefix_vote/q_cache_design/r_marginal_retention/s_budget_reallocation`)。RUN-LOG:2899 已自 flag"引用时写文件全名勿混"。s4 侧内务,本分支不代办。

## 3. 判词层冲突(一处,已裁决)

**counterfactual gate:VERIFIED 首版 vs doc-11 L122 正面冲突,主循环补验后裁决为两层并立**:

- 结构层(VERIFIED 四点,成立):已接线(orchestrator:517-525)、有真实拒绝路径、仅重放 processors 段三 hook;
- 行为层(doc-11 L122,成立):门认 `kind`-tagged 事件,官方 journal 只写 `type`-tagged;**全分支唯一 kind 生产者 = 门自己的单测**(`tests/aegis/unit/test_gate_counterfactual.py:18,24`),无适配层 ⇒ 官方自产轨迹喂入恒 `ok=True`;
- 终裁:**结构真门 + 数据契约断裂 ⇒ 官方管线内行为等价 no-op**(成因 schema 失配非未接线)。VERIFIED §2 已带 ERRATUM 修正;我方移植件早已修此病(零覆盖检测),是独立佐证。
- **治理教训**:同一对象的判词分居两分支两文档,冲突三天内发生两次反转(manifest 教训重演)。→ 待裁决 B:判词唯一权威位置。

## 4. 概念层 overlap 矩阵(⑦–⑫ × 论文 v3 × 官方代码 × novelty 线现役)

分类线(论文 v3 逐字定标):**论文已定义** → 补实现=fix;**论文欠规格必补件** → 规格=fix、机制选择=我方;**论文空白** → 修复=超论文=novelty 体裁。

| 条目 | 论文 v3(逐字核) | 官方代码 | novelty 线现役对应 | overlap 判定与归属建议 |
|---|---|---|---|---|
| ⑧ 统计接收门 | **空白**(ship 判据全确定性;显著性/racing/sequential 全文零命中);但 **§6.6 自供 sub-threshold coupling**(5 个同桶编辑逐个过关、第 6 个 −14.0pp;"headline metric masks channel-level regressions") | 五门全二值 + 阈 3 启发式(已 L4 亲验) | **`s_budget_reallocation` 同族**(原审计自认);vote 批"出货侧"互补 | 诊断=audit(论文自供为锚);racing 修复=超论文 → novelty 体裁。**最易重复立项对之一** |
| ⑫ 多样性压力 | **空白**(QD/novelty-search 零命中;p13 "diversity" 是 replay buffer 描述词,p20 "novelty" 是去重门) | 仅 SHA 精确去重;曾有硬配额已废弃 | **`o_decorrelation_headroom` 即 ⑫ 修形(NCL/误差去相关)的测量仪**;原审计已把 ⑫ 拴在"prefix-vote go/no-go + o>0"双门上 | 诊断=audit;NCL 修复=novelty。**已经事实上合线,须明文化** |
| ⑦ 变体×任务路由 | §4.5 只给 **greedy argmax**;**`cluster(task)` 63 处零定义**;池上限 K 无数值 | 零实现 | **`m_cluster_validation` 臂**(簇路由赢 null 才升臂)与 ⑦ 的 E1 加性零假设是**同一个前置证伪门的两个仪器** | 诊断=audit;ALORS=超论文 novelty。E1/m_cluster 二择一或合并,勿双跑 |
| ⑩ 冷启动 | **拆两概念**:H₀(Table 8 已定义="Handcrafted base harness")vs **路由/声誉冷启动(全文 ABSENT**,cold/warm-start 零命中;§4.5 对新 fork 变体如何起估计沉默) | 静态 `_UNKNOWN_BOOST=0.7`(4 桶) | 我方 router 已留钩默认关(PD-19 "cold 0 次") | **fix 线最名正言顺一条**:实现 §4.5 必须补路由冷启动=论文欠规格必补件;层级收缩这个机制选择记 deviation 即可 |
| ⑨ 编辑归因 | **部分**:Table 9 有 `attribution_signature`(fired-or-not);**并发编辑分解 credit assignment 空白**(p5 "credit assignment" 仅类比) | 观察式 direct/orphan/joint(自供无消融) | 无现役对应 | 诊断=audit;LOO=超论文且原审计自判"原样必败"→ T3 挂起 |
| ⑪ regret/bandit | **空白**(bandit/UCB/regret/ALORS 全文零命中) | 零实现 | 无现役对应 | 原审计自判不可辨识(单载未路由格未观测),降为配图 → 挂起 |
| vote 批(batch-2 o–s) | **空白且语义相反**:voting/majority 全文零命中;"Ensemble routing"=**路由到单变体执行**,非输出聚合 | 无实现;仅 gaia-playbook 知识条目提及"Majority voting over N samples (typically 3),+3-5pp at 3× cost"(同配置 N 采样,非跨变体) | novelty 线现役主推 | **干净 novelty,零 audit overlap**。措辞红线:不得称"实现 §4.5 Ensemble"——它是叠加在池上的新机制;related work 划开官方 playbook 条目 |

物理层事实:六条的**代码落点全部只能在 s4**(variant_pool 代码在那里),audit 分支永远只有文档 → 归属规则天然应为"audit=诊断与证据,s4=全部实现"。

## 5. 论文 v3 逐字带来的口径修正(独立于分支问题,但引用前必改)

1. **"三态门 APPLY/FORK/REJECT"非论文词汇**:论文的门是 **SHIP/REJECT 二值**(Fig 2)+ §4.5 散文"two outcomes"(apply-to-target / fork-new,fork 替代 reject);APPLY/FORK/REJECT 是我方形式化。CH3 与 doc-11 五分歧表的第 3 条措辞须降级("论文散文 implies,我方形式化命名")。
2. **v3 表编号**:跨模型 headline = **Table 4**(+14.5% avg,3 模型 × 5 床 = 15 格);**Table 5 = GAIA×GPT-5.4 单格策略对比**(Ensemble 87.4/87.4/0.0/107.8M vs Global 49.5/73.8/−24.3/143.7M)。现行"Table 5 87.4/+13.6"口径数字无误,但"Table 5 = headline"的混用要停。
3. **±5% 只在 Table 8**:标签"ignored single-round pass-count delta",**未接进 Algorithm 1**、正文无执行位置 →"论文定义了阈值"成立,"论文规定了在哪执行"不成立;实现处按 deviation 记。
4. **⑩ 冷启动拆分**(修正 paper-defines-five-gaps 口径):"冷启动已定义"只对 H₀ 成立;路由冷启动论文真空,⑩ 不是读漏。
5. 论文侧确认:τ³-Bench × 3 任务模型(Sonnet4.6/GPT-5.4/Qwen3.5-9B)——与官方代码 tau2 × 4 模型的"第六分歧"论文侧坐实;§7.7 自认 no held-out/选择偏差;§4.5 实验支撑 = GAIA×GPT-5.4 单格。

## 6. 问题清单与待用户裁决

**P1(已修)** counterfactual 判词冲突 → 两层裁决落 VERIFIED ERRATUM;doc-11 L122 幸存。
**P2(已执行)** 原审计文档双落两分支 → 用户令"分开"后执行:**s4 为 canonical**,本分支重复副本已移除(历史见 6063052),两分支共享文件数=0;后续判词修正只走 canonical。
**P3(s4 内务)** 两代 o–s 同字母脚本:建议子目录或前缀重命名;引用全名(RUN-LOG:2899 已 flag)。
**P4(待裁 B,核心)** 归属规则缺失:⑦–⑫ 诊断=问题(用户已裁,fix 线),但六条修复全部超论文(§4 矩阵)→ 建议明文规则:**诊断+证据基线+论文欠规格必补件规格(⑩、cluster 定义)= audit 线;超论文增益机制(racing/ALORS/NCL/regret/vote)与全部代码 = novelty 线(s4);每条目在对方分支只留一行指针;判词唯一权威位置 = 首发文档,他文引用不复写。** 否则 ⑧↔s、⑫↔o、⑦↔m_cluster 三对将重复立项。
**P5(口径)** §5 五条,引用前必改(尤其 CH3/doc-11 分歧表)。
**P6(已闭)** RUN-LOG 16 行:s4 `d440e8b` 已恢复提交;VERIFIED §4.3 已更新。
**P7(待裁 C)** 本 audit 分支去向:短命分支尽快 PR 合入(main 或 s4),或长期并行。建议:**证据文档批稳定后即合入 s4**,避免第三条长期活分支。

---
*证据深度:§2/§3/§4 官方代码列 = 主循环 L4 亲验;论文列 = researcher 逐字提取(PDF 与 text dump 在 scratchpad 可复核);s4 盘点 = researcher 全量 + 主循环对双落文件/`kind` 生产者抽验。本文档只在 fix/audit-ideas-7-12。*
