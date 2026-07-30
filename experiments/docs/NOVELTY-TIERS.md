# 贡献分层:ICLR 级(novel)vs MSc 级(不 novel 但够用)

> 2026-07-30,基于 NOVELTY-EXPDESIGN-RESEARCH.md(11 篇 L1 亲核)+ 两轮 litscan
> + MSc bar 裁定(port/integration 合法)。用途:论文叙事分层 + 投稿路线决策。

## Tier-N:novel,冲 ICLR 线(全部条件于 B 臂结果)

| # | 主张 | 成立条件 | 占位状态 |
|---|---|---|---|
| N1 | **四合取主命题**(开放实证问句式):training-free seesaw 演化 harness-config 池 × 子任务粒度路由,配平算力下 match/beat fresh-spawn(AOrchestra)与 post-trained 专家(Uno/Leni)on GAIA | B2 > SA-matched 且 B2−B1(rr) > 噪声带 | Jul-30 核验:四合取无人全占,近邻各占相邻格 |
| N2 | **零 rollout 观察式 (variant×type) 信用 + LOO 校准**:躲开不可靠子任务判官(2606.09863 AUROC≤0.65)与昂贵 MC 树(TreeMem),文献最便宜 | 半独立:B2 输赢皆可写(负结果下降为方法注记) | 外证齐(2605.27621 LOO≈组合法) |
| N3 | **"分工救活分解"机理发现**:E0 2×2 交互项——②−①≈0(复现已知阴性)而 ④−③>0 ⇒ 分解的价值以异质执行器池为前提 | E0 出交互形态 | 无人发表过该交互;JoyAgent 只有单格阴性 |
| N4 | (条件涌现)**fork 特化动力学**:演化循环未被指示却长出 (variant×type) 特化画像 | s1k8b103 出 fork 且画像分化 | 风险:fork 随机(s1k8 零 fork 前科) |

**生死线**:N1/N3 全部依赖裁 C/D 的配平对照——没有 SA-matched 与 B1(rr),
N1 被 2606.13003("配平即消失")当场击杀。

**时间现实**:ICLR'27 主会 deadline ≈ 9 月中,与论文冲刺(9/5)重叠,且单种子
单床撑不起主会。现实路径 = **论文先行(Tier-M 保底)→ 8-9 月 N1/N3 若阳性,
补 3 种子 + 第二床 → ICML'27(~1 月)或 ICLR'27 workshop**。本周期不硬冲。

## Tier-M:不 novel,MSc 论文够用(无条件可立)

| # | 内容 | 性质 |
|---|---|---|
| M1 | **复现审计**:§4.5 变体池 paper-informed 复现 + M-1..M-25 偏差登记 + K=8/K=1 分岔 on 103 原床 + 供给纪元纪律 | 审计贡献,零 novelty 需求 |
| M2 | **D1-lite 分解器**:LLM 类型化 one-shot DAG = 标准技术 port(MSc bar 裁定合法) | port |
| M3 | **ledger 路由机制本体**:成功率账本 = 标准簿记;接到演化池上的那部分归 N1 | port + 集成 |
| M4 | **E0 oracle 仪器**:oracle 注入 = 公认方法学;2×2 应用是组合非发明 | 方法学应用 |
| M5 | **可靠性基建 + 预注册治理**:resume/watchdog/冻结包制度 | 工程严谨性,加分项 |

## 叙事装配

- **论文(9/5)**:M1-M5 为骨架保底 + N1-N3 按实际结果装配(阳性=主贡献章,
  阴性=诚实负结果 + 机理讨论,组件归因决策树保证两头都有话说);
- **投稿(若 N 线阳性)**:N1 主陈述(问句式)+ N2 第二贡献 + N3 机理章;
  M 层压缩进 setup/appendix。
