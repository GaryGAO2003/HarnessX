# 全论文差距审计(Jul-27,用户令"review 一遍,总结与原文+附录差多少差在哪")

> 方法:43 页论文全读(本地 `docs/assets/paper/HarnessX_Tech_Report.pdf`,内容与
> arXiv 2606.14249 相符)+ 承重机制代码亲验(gate/ledger/defaults/j2/CLI 默认一手,
> 其余六路子调查交叉核对);我方文档只作索引、逐条对代码复核。主循环已抽验其关键
> 反转(墙钟 10000s)属实。完整报告存任务档;本文为定稿摘要 + 全部结论。

## 0. 结构性发现(最重要的一条)

**默认配置复现的是论文的 Global 对照臂,且床与轮数比论文小/短约三个数量级,效果
在数学上不可见。** 论文 GAIA 头条效果(Table 5, p.17):Ensemble 87.4%(R14 峰,
不退化)vs Global 49.5%(R4 峰 73.8% 起持续退化,R5-6 悬崖)——是**长时程、大 n**
现象。论文自报统计尺(p.17-18):n=103 时单轮 95% CI ≈ ±8.5%;n=12 时 ≈ **±28%**,
而待检出效应 +13.6% —— **信号约为噪声带一半**;3 轮时两臂都还贴着 H0,
Final−Peak 对比根本没成形。⇒ **保真修复在解决可见性(床/轮数/双臂/种子)之前
全部白费。**

## 1. 差距矩阵要点(五态:verbatim / 声明偏差 M-xx / OURS 近似 / 缺失 / 顺延)

**verbatim(核心纪律全对上)**:seesaw 全史回退判定(gate.py:194-210 ↔ §4.1 p.8)、
三路判定、per-variant seesaw 范围、fork/retire 机制、至多一次修订、pass@2 无偏
估计量逐公式(reporting.py:188-210 ↔ App A.3 Eq6)、patience=3、max-steps 20、
concurrency 10、meta 200 步、Table 9 六字段全匹配(+我方扩展另列)。

**OURS 近似(论文留白,我方声明填补)**:路由估计量 Ŝ(Laplace (p+1)/(a+2)+冻结
+全史窗)、簇定义(routed/level 代理,论文三读不兼容)、退役度量(task_macro)、
冷启动、H0(论文只公开 ALFWorld 的 H0,GAIA H0 未公开 ⇒ **绝对分与论文不可比,
天然限定**)。

**声明偏差(M-xx)**:K_t 默认 1≠4 且为 N 独立会话非单 Evolver 批(M-16/M-20);
选择性调用部分接线(M-18;全 LLM 模式下经 A1/A2 已实质闭合);repo 模式放宽
capability_evidence(M-19;乙=机器自证兜底 M-22);模型=DeepSeek 双档替
Sonnet-4.6/GPT-5.4/Qwen+Opus-4.6(M-15;§7.7 自认开源 meta 未测);轮数/床/种子
(实跑 3 轮/6-12 题/1 种子 vs 论文 15/103/3);meta token 预算(论文 100-175M/床,
我方无 token 帽只有 $ 与墙钟帽)。

**缺失**:round-global K_t 协调器(M-16;**注:现行"每轮单目标变体"政策下
candidates-per-round=4 实质等效 round-global,协调器仅多目标时才需要**);
**bucket-disjoint multi-ship**(M-17,App B.1 p.34 与 Algorithm 1 单 ship 是论文
内部两套语义 H1,我方按 Algorithm 1 读);Digester prompt(论文未公开,0% 可采)。

**审计新发现(最有价值)**:**论文 App B.1 公开了 Planner prompt 全文、Evolver
~60%、Critic ~70%——我方采纳 0%,全部自写 OURS 重建。免费保真度在桌上没拿。**

## 2. 效果差距驱动因子(按量级排)

1. 床 12 vs 103 —— CI ±28% vs ±8.5%,**主导**;
2. 轮数 3 vs 15 —— 退化/分化都未及发生,**主导**;
3. 默认 pool-k 1 = 只跑 Global 臂,无对比(二值);
4. 模型档次(task=flash 近能力地板风险:论文 §D.5 Qwen-9B 0.05 hit-rate 例证
   "地板下演化不复利";meta=非 Opus,§7.7 未测);
5. L2 门 × DeepSeek 不写证据 ⇒ 工具杠杆受阻(GAIA 最大单轮增益 +4.9pp 正是工具类
   C-R10-02;39% blocked-source 簇待解;M-22 机器自证已备,待实战);
6. K_t=1/2 + 无 multi-ship ⇒ 每轮探索少、至多 1 ship;
7. 角色确定性默认(正式跑应全 LLM,已验收);
8. 运行病理:预算耗尽 ~42% 任务轮、F2 空手死 20/21、replay 彩票、no-commit 顽疾;
9. 单种子无方差。

**净判断:即使保真度完美,当前床(12×3×1)下 Ensemble−Global 的可测增益≈0。**

## 3. 文档-代码冲突(审计揪出,处置)

- 墙钟:代码 10000s ↔ RUN-LOG 曾写"默认 300s→600s" —— **RUN-LOG 已勘误**
  (谱系 A 归因错误,修正为 5× no-commit + 3× replay);
- defaults.py:8-9 模型字面量是死占位(claude-*/YOUR_PROVIDER),操作真值=DeepSeek
  (SPEC §7.13+定价表)——待 F7 卫生修;
- 成本虚高系数三处不一致(27×/66×/27-66×)——待统一口径(与所用模型-价格对相关);
- SPEC 正文残留 min_fork (2,2) 旧文(勘误在案,代码 (1,1) 权威);
- to_dict/to_json 口径残留(EXP-E08 已记)。

## 4. 论文内部矛盾(佐证我方 digest)

Algorithm 1 单 ship ↔ App B.1 multi-ship(H1);iterates_from 在 B.1 模板但不在
Table 9(H7);App C "19 runs" ↔ 主文 "15 configurations"(H8)。我方按 Algorithm 1
读并记 M-17。
