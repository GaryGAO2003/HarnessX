# 审计:改进 ideas ⑦–⑫ vs 官方 upstream(Aug-05 2026)

**范围**:对话层头脑风暴清单第 ⑦–⑫ 条(该清单 repo 未落盘,ALORS/低秩全仓零命中)在官方 upstream(Darwin-Agent/HarnessX)下是否仍成立,及作为改进是否正确。
**方法**:三路并行审计——(R1) RUN-LOG 尾部+五个在飞脚本;(R2) upstream 全分支 git grep/show 六轴扫描;(R3) PD-TABLE/SPEC/STATPOOL/LAUNCH-CONFIG 机制现状。
**证据深度标注**:🟢 = 本地文件可复核(file:line 已录);🟡 = researcher 直跑 git 命令的逐行输出,主循环因分类器故障**未亲验**(勿据此改 doc-11 类承重判词,恢复后先直查)。

---

## 1. 现场状态(Aug-05,🟢)

- 付费主实验 T1 K=8 排队,**等发车令**(解冻三条件全达:b65ca01 web_fetch 加固 / ab6eb27 二次合流 1152 绿 / T1 配置不变)。
- doc-12 测量批(power/ratchet/censoring)因撞车(PACE 2606.08106、2607.13683)+九处自撤**整体撤下**;替代批 = 「pool 输出当选票」五脚本(o 去相关余量 / p 前缀投票 / q 缓存设计 / r 边际保留 / s 预算重分配),go/no-go 在 `p_prefix_vote.py`(前缀投票塌 → 整线今晚放弃,不花钱)。
- doc-12 撤框架但**数字存活且承重**:摆动集 77;功效地板 3.22pp ≈ A/A 地板 3.18pp;**MDE = 9.03pp**(vs 编辑量级 1–3pp);跨变体误差去相关 +0.2pp≈0 而答案分歧 +9.0pp(N_eff=1.31, ρ=0.717);交付缺口 10.82pp(`exit=done` 规则收回 +6.66pp)。

## 2. 官方 upstream 六轴判定(主审 `feat/aegis-experiment` @90f5d2d Jun-02,🟡)

| 轴 | 判定 | 证据 |
|---|---|---|
| ⑦ 变体×任务路由 | **缺失** | 全 4 分支零命中;无持久变体池 ⇒ 我方 variant_pool 仍是 §4.5 唯一实现 |
| ⑧ 统计接收门 | **缺失** | ship=五个二值门全过(`commit.py:254 all(v.ok)`);最强分数门=固定容忍+**硬编码整数阈 3**(`defaults.py:23`);包内 hit_rate<0.5 回滚=故意不接线死代码(`orchestrator.py:20`) |
| ⑨ 编辑归因 | 部分(观察式) | `attribution.py` direct/orphan/joint 按机械触发;docstring 自供 "**Without an ablation we cannot disentangle cause**" |
| ⑩ 冷启动 | 部分(静态) | `reputation.py:13 _UNKNOWN_BOOST=0.7`,仅 4 桶级,无 UCB/计数/per-variant |
| ⑪ regret/bandit | **缺失** | regret/oracle/VBS/bandit 零命中 |
| ⑫ 多样性压力 | 部分(去重) | novelty gate 仅挡 refuted 编辑**精确 SHA 重提**;无相似度惩罚/QD |

另:官方 Evolver 靶选择**无机械规则**(无 worst_first/failure_density),由 LLM agents 语义决定 → 记入 CH3 保真度素材。⑧⑨⑩⑫ 各得一句官方自供/原始表亲作 related-work 弹药;无一预占我方方案。

## 3. 逐条裁定(生死线 = MDE 9.03pp 与单载不可辨识性,非占位)

| # | 缺陷仍在? | 改进正确? | 裁定 |
|---|---|---|---|
| ⑧ racing 门 | ✅ 三层(官方整数阈/论文 PD-25·27/我方 #14b 靶) | ✅ **最正确** | racing 不造功效只重分配 ⇒ 在 1–3pp 效应下诚实地几乎不放行,恰与幸存缝 R9(「功效可先验算出」)成对 = 诊断+修复体裁。先零成本:36-run 重放离线模拟。与 `s_budget_reallocation` 同族 |
| ⑩ 冷启动 | ✅ PD-19("cold" 0 次) | ✅ 最便宜 | 层级收缩恰治已记录的 F2 单开翻车(点估计过矫);PD-14+PD-19 耦合 ⇒ 一切细簇臂的**依赖项**;router 已留钩(默认关) |
| ⑫ 差异化压力 | ✅ PD-06 自认未测;实测反向病(欠特化) | ⚠️ 修形 | generic novelty search=错误移植(奖新颖不看适应度,会 ship 噪声);vote 转向后正解=**NCL/误差去相关**(Brown-Kuncheva good diversity);等 prefix-vote go/no-go + `o_decorrelation_headroom`>0 双门 |
| ⑦ ALORS 低秩 | ✅ PD-14(5/8 变体闲置) | ⚠️ 修形+等待 | 两坎:**非平稳性**(变体每 ship 行分布漂移,AS 假设固定组合 ⇒ 须 epoch 截断/折扣)+空行无侧信息补不出;E1 加性零假设(M1=路由价值恰为零)是正确前置证伪门。勘误:「秩≤2」= `l_rank_ablation.py` 构造上限,非经验结论;经验事实只有「功效不足不能判」 |
| ⑨ LOO 归因 | ✅ 官方 docstring 自供 | ❌ 原样必败 | per-edit 效应 1–3pp < MDE ⇒ 测出的是噪声,且 n+1 全套重评昂贵。修形:lever-bucket 级聚合、只审高 hit_rate 杠杆、带功效预算(18 rollouts/摆动任务 = 9×),排 racing 基建后 |
| ⑪ regret 曲线 | ✅ 官方零命中 | ❌ 「便宜纯记账」不成立 | freeze_routing 单载下未路由格**未观测** ⇒ VBS 不可构造 = **不可辨识**(非仅"偏描述");且噪声矩阵取 max 有 winner's curse,须并报 A/A 地板。降为 overlap 探针臂之后的配图 |

**优先级**:T1 = ⑧(先离线)+⑩;T2 条件 = ⑫(双门)+⑦(E1+密度);T3 配套 = ⑨(bucket 级)+⑪(等 overlap)。与 vote 转向互补:⑧⑩ 修「进货」(收什么编辑),vote 批修「出货」(输出怎么用),同咬 MDE 主刺。

## 4. 合规与治理旗

1. **六条全部走 CH4 臂/flag**(默认关、字节等价、不进 Hyperparams 锁),不动基线——#14b REJECT 墙/F5 泄漏/非载体冷启动是故意留的对照靶(LAUNCH §4);routing-freeze 只读上一轮 = 正确性不变量,⑦⑩ 实现须兼容。
2. **doc-11 判词待改但缓落笔**(🟡):官方 counterfactual gate 实为「已接线、能拒、仅查改写输出的 processor 链,对 prompt/工具/预算类编辑恒过」= **窄域真门**,非 latent no-op。分类器恢复后亲跑 `git show upstream/feat/aegis-experiment:harnessx/aegis/gates/counterfactual.py` 直验再改(manifest 两次反转教训)。
3. **RUN-LOG 纪律违规**:Aug-05 15:04 物理删除 doc-12 批次 16 行,违反 append-only(RUN-LOG.md:2833)。建议恢复条目+撤稿标注;撤框架≠撤数据(§1 五数字是 CH7 threats 与 ⑧ 立项承重证据)。
4. ①–⑫ 清单应落盘(建议进 doc-12 替代文档),否则跨 session 只剩转述。

---
*三路审计原始报告见各 session 转录;本文档为主循环合成,upstream 部分(§2、§4.2)统一 🟡 待亲验。未提交 git——另一 session 在飞,勿纠缠其工作树。*
