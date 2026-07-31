# 贡献分层:ICLR 级(novel)vs MSc 级(不 novel 但够用)

> 2026-07-30,基于 NOVELTY-EXPDESIGN-RESEARCH.md(11 篇 L1 亲核)+ 两轮 litscan
> + MSc bar 裁定(port/integration 合法)。用途:论文叙事分层 + 投稿路线决策。

## Tier-N:novel,冲 ICLR 线(全部条件于 B 臂结果)

| # | 主张 | 成立条件 | 占位状态 |
|---|---|---|---|
| N1 | **四合取主命题**(开放实证问句式):training-free seesaw 演化 harness-config 池 × 子任务粒度路由,配平算力下 match/beat fresh-spawn(AOrchestra)与 post-trained 专家(Uno/Leni)on GAIA | B2 > SA-matched 且 B2−B1(rr) > 噪声带 | Jul-30 核验:四合取无人全占,近邻各占相邻格 |
| N2 | **零额外 rollout 观察式 (variant×type) 信用 + LOO 校准**(n+1 重评;slot-swap = 2605.27621 的 model-replacement 协议同形,弃"最便宜"最高级) | 半独立:B2 输赢皆可写(负结果下降为方法注记) | DR-E PDF 级:27621 双引句实(LOO 省 3.3–7.6× token,判官 hierarchical 负 R²、BrowseComp-Plus 近零相关);09863 限 final-state 判官(GAIA 子任务迁移=a-fortiori 须标);TreeMem 对比=regime 口径(训练依赖 vs 免训练推理账本) |
| N3 ⛔ | **"分工救活分解"机理发现**:E0 2×2 交互项 | **暂停(Jul-31 用户裁掉 E0)**——无仪器则无此主张;B 臂打平时可按需复活 E0(15 题半价)重启 | 无人发表过该交互;JoyAgent 只有单格阴性 |
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

## 普查修正(Jul-30 深夜,DECOMP-METHOD-CATALOG.md 45 系统落地后)

- **N1 措辞再收紧**:成分比 GAIA 视角显示的更常见——"类型化分解→异质执行器路由"
  在 GUI 域是常规操作(Agent S2 2504.00906 / UFO2 2504.14603 / Agentic Lybic,
  有消融有数字,必引作跨域先例);Meta-Agent 2605.25233 已做 inference-time 生成
  式版本(⚑fresh worker 无持久池,GAIA 无数)⇒ **N1 可辩护形态只剩配平预算下的
  实证对决(演化门控池 vs fresh-spawn vs trained-specialist),架构本身不称
  novel**;候选 3("first-to")进一步降权。
- **N3 升强**:"分解非精度赢面"从 JoyAgent 单证升为 **≥5 独立团队合流**(JoyAgent
  / MiroFlow 74.8>71.9 / AgentOrchestra / 2606.13003 / Tran&Kiela 2604.02460 /
  Scaling-Science 2512.08296,后者:可分解任务 +80.8% 但序贯规划 −70% = 任务结构
  对齐才是真杠杆)——E0 的 ②−① 格=复现公认现象,④−③ 格=新问题,机理叙事更稳。
- **SA-matched(裁 D)从防御升必修**:GAIA 开源 SOTA(ALITA-G 83.03 p@1)与
  BrowseComp SOTA(Tongyi/WebSailor 系)皆**单智能体不分解**——单体就是前沿形态,
  配平单体基线=与前沿对标,不是选修。
- **主张边界**:限定"**配平预算下的精度** on GAIA";分解的常见赢面是成本效率
  (E3 −85% 成本 Δacc≈0 / Uno 10× 便宜输 1.4pp),效率轴有 2605.15425 占静态分解
  弱基线位——留 backlog 臂,不进主张。
- **占位监视更新(Jul-31 凌晨,正文级深读)**:Meta-Agent 2605.25233 **红旗解除
  = CLEAR**(双渲染逐字 §3.1:*"no templates are reused across benchmarks"*,
  零跨任务持久化,无 GAIA 数)——但其占死"构造期再生成+执行期门控"母题 ⇒
  **N1 novelty 轴只能落在持久池/跨任务演化,永不落在"我们有门"**;GUI 三件套、
  MasRouter 照旧;smolagents/h2oGPTe/WebSailor 无可解析 ID。

## 深读修订(Jul-31 凌晨,DR-B/DR-C 落地;DR-A 待归)

- **事实链四家全 CONFIRMED 零反转,ID 齐**:JoyAgent 2510.00510(单 71.5 > 分解
  70.3,Claude-4-sonnet,val pass@1)/ AgentOrchestra 2506.12508v6 / MiroFlow
  2602.22808(GPT-5,val-text 103)/ ALITA-G 2510.23601(83.03 pass@1 单体)。
- **三修正**:①AgentOrchestra 自报头条=**89.04 Test**;83.4=**Uno 受控 pass@1
  复现,对比行有效**(其 pass@2 88.7≈89.04,口径差非版本漂移——DR-D 二次修正了
  DR-B 的"版本漂移"判);归因照旧"planner-only 36.54,增益全在执行器子代理,
  Deep Researcher 单跳最大 +20.6";②MiroFlow 单胜**仅限 GAIA**(BrowseComp/HLE
  多体反胜,论文自解:GAIA 序贯结构错误传播),禁作"分解普遍有害"引;③四家**无一配平预算**,
  且分解臂花 ≥ 算力仍输 ⇒ 事实链收口:"**≥算力下加分解仍不敌强单体 on GAIA**",
  **matched-budget 检验=四家共同的方法学空洞,恰由我方 SA-matched/B1(rr) 首次
  填补**(N1 再添一层);④DR-B 引句为摘要器中介(数字三角化可靠,verbatim 写作
  期须 PDF 直读升级)。
- **Scaling-Science 2512.08296 白名单生效**:"architecture-task alignment, not
  number of agents"可引(注明 correlational R²=0.373);+80.8/−70.0 须带床+架构名
  (Finance-Agent Centralized / PlanCraft Independent);**"decomposable vs
  sequential"分类学禁引(论文无形式定义)**。
- **Leni 归因模板抄法**:累计添加式(非 LOO);干净混淆矩阵只在确定性真值床
  (SpreadsheetBench 0.20/0.75/0),GAIA 层自降 "indicative" ⇒ **我方同理:E0
  oracle 门控格可称 clean,其余标 indicative**;其专家 post-trained 0.5-4B、训练
  成本未披露——training-free 对比成立,但成本不可像对像。
- **DR-A(算力三件套)三判**:①2606.13003 **WEAKENED**——其自身不配平(10× =
  as-deployed 观测,CoT-SC k=5,web margin 1.19–2.38pp 无显著性),降为动机引;
  配平一手改由 **2604.02460**(matched thinking-token + 95% bootstrap CI,升主锚)
  与 **2606.15017**(web 床,最贴我方)承担;②OneFlow homogeneous 定义 CONFIRMED
  且**威胁加强**:同底座仅 prompt/tools/position 异 = homogeneous,**我方 K=8 池
  逐字落入"可被单体吃掉"范围** ⇒ SA-matched 生死线;③**REVERSED(已执行删除)**:
  "truly heterogeneous 反攻话术"作废——OneFlow 锁死 heterogeneous=换底座,我方
  无权援引;B2 胜出只能走纯实证叙事。附利好先验:2604.02460 §5.3 分解在上下文
  退化 regime(α=0.7)才有正增益,GAIA 长上下文恰是该 regime,可作 B2 动机引。
  SA-matched 规格升级:配总 token 非调用数,双变体(长 horizon 单体 + SC k=5)。
- **DR-D(对手方 PDF 直读)四发现**:①AOrchestra 占位 **CONFIRMED CLEAR(强)**
  ——正文级持久化全扫零命中(工作记忆=步内、"cache"仅复现性用途),fresh-container
  句重锚 p.12 §B.1.2(同任务内跨 delegation 也不留存),无子任务类型系统;
  ⚠️但其**头条 80.0 是 training-free**(SFT 只救弱 orchestrator 轨 56.97→68.48,
  与头条无关)⇒ **N1 与它的分界只能押"演化持久池",不能押"我们免训练"**;
  ②新利好:Uno 统一池下复现 AOrchestra 仅 **69.4 pass@1**(自报 80.0,−11pp)
  ——跨论文 GAIA 数字强烈依赖 harness/池 = matched-compute 论题的直接证据
  (caveat:Uno 复现可能欠调);③JoyAgent 谜底:Multiple(3) 是**刻意去掉 Browser
  agent** 的最优多体(加 Browser 显著恶化,Multiple(4) 崩至 52.7),纯分解臂
  ≥ 算力仍输,Fusion(单+多+Critic 投票)75.2 才赢 = N3 条件价值读法的现成例证;
  ④MiroFlow 口径修正:**avg@3 on GAIA-Val-Text-103(与我方同床)**非 pass@1;
  写作卫生:JoyAgent(val pass@1 Claude-4-sonnet)与 MiroFlow(avg@3/103)禁并列
  同栏;Uno 类型系统=闭集 9 模型 × 13 原语、推理期冻结——同样不占演化池格。
