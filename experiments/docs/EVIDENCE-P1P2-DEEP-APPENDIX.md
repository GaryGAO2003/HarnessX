# P-1/P-2 深研证据附录（2026-08-13 夜，四路 deep-research 全文级）

> 终裁见 DECISION-P1P2-NIGHT-RULING.md；本文件是四路调查的数字级存档。
> 置信级：高=原文两次独立抓取一致；中=单次/转述；低=摘要级。灰区=FAIL：
> 标"未证实"的条目一律不可引用。

## A. P-1 深查（draft-first 四面）

### A1 家族普查（9 个 AIDE 后继 scaffold）
- **跨系统元发现：无一做过 draft-early 消融**（数量 N、预算占比、开/关皆无）。
- MLE-STAR (2506.15692, NeurIPS 2025)：Merger 先合成完整 s₀ 再精修；Valid 100.0±0.0%
  vs AIDE 78.8±5.0%（Lite 22 赛、gemini-2.5-pro；**不可与 82.8% 背景数字混用**）。中置信。
- R&D-Agent (2505.14738)：显式命名 "Draft Stage"；Valid 96.0% / Medal 35.1%；
  **Submit() 仅循环结束调一次，无中途保底**。中置信。
- ML-Master (2506.16499)：MCTS 空节点强制 Draft；Valid 93.3%；论文自认消融未完成且
  至今未兑现。注意存在两代论文（29.3% vs "2.0" 56.44%），勿混用。
- Agent K (2411.03562)：无 draft 机制；v1 的 92.5% 已在 v3 整体移除（引用须注明版本）。
- AutoKaggle (2410.20424)：线性流水线最后阶段才首次产出 submission，Made Submission
  仅 0.85——自证 P-1 担忧。低置信。
- Operand Quant (2510.11694)：无阶段化，Medal 39.56% 自称第一——**反对 draft 阶段化的
  间接最强信号**。低-中置信。
- MLE-bench A.6.1 逐字（三版本一致）："Actively track whether a solution generates a
  submission.csv file; flag solutions that fail...as buggy" + "previously under-emphasized"。
  "buggy" 全文仅此一处；Figure 8 更强："failure to submit...will result in a failed attempt"。

### A2 "做了活没交付"死法实证（全部是诊断，无一是解药对照）
- **MAST (2503.13657, NeurIPS 2025)**：FM-3.1 "Premature termination" 占 1642 轨迹失败
  案例 6.20%（κ=0.88）——唯一同行评审级正式命名+量化。高置信。
- **SWE-Marathon (2606.07682)**：Timeout 31.4% + Premature Termination 7.6%（526 例）。
- **OpenHands #10767**：max_iterations=100 → ~45% 撞墙；=500 → ~30%；99.2% 提交但仅
  26.2% resolved。高置信。
- **SWE-agent Table 13**：resolved 实例 93.0% 主动 submit vs 全体 69.0%；submit 收尾
  resolved 14.3% vs exit_cost 3.1%——**反向因果混淆，不可当解药证据**。
- **SWE-Master (2602.03411)**：~24.3% 被截断未提交轨迹本来是正确解；forced-submission
  (α=0.5) 61.4%，"非 DONE 即零奖励"训练崩溃。高置信（训练侧类比）。
- soliloquizing（SWE-agent #717 + EnIGMA Table 4）：Claude 3.5 在 CTF 域 10-48% 发生率，
  GPT-4 系 0%。TheAgentCompany：partial-score 与二元成功差 9-10pp（12/12 配置同向）。
- 第三方 .traj 解析（nilenso）：未提交率 GPT-5 14.7% / Gemini2.5Pro 5.5% / Sonnet4.5 1.6%。中置信。
- 措辞警示：issue 库里搜"forgot to submit"不成立——真实死法是"submit 了但产物空/错"
  或"被基础设施打断"，不是"忘了调 submit"。

### A3 反面专项
- 锚定：METR RE-Bench §5.2 "attempts to use lightly modified transformer architectures
  **84%** of the time, despite...work very poorly"（逐字）；**SCoRe (2409.12917) Table 1**：
  Δ(t1→t2) Base −11.2% / **Self-Refine 纯提示 −1.0%** / SCoRe RL +4.4%——纯提示自查
  近零效。宽动作空间消融（2507.02554）：窄算子集下搜索算法无差异。
  CMU 元认知监督（2603.24768）：0/30 → 20/30（数字二手转述，中置信）。
- 灌水：MLE-bench "已提交 vs 有效"缺口 15.6-19.0pp；节点 500→5000 奖牌率有时反降
  （"imperfect method...to select its 'best' attempt"）；Inference Scaling fLaws
  (2411.17501)：非 oracle 验证器下最优采样 **常 <10 次**、曲线下弯——反驳 Large Language
  Monkeys (2407.21787) 的 oracle 前提。curl "Death by a thousand slops"：~20% AI slop、
  仅 ~5% 真实（强类比）。AIDE 自身 num_drafts=5 封顶（steps 的 25%）。
- gaming（证据链最硬）：METR 系列——RE-Bench "impressive and unexpected loopholes"；
  2025-06 博客 **30.4%**（39/128）run 含 reward hacking、单任务 100%（21/21）、o3 被问
  是否符合用户意图 10/10 答否仍继续、被问是否作弊**主动否认**；o3/o4-mini 预部署 ~1-2%
  （与 30.4% 口径不同，**不可合并**）；GPT-5 预部署 ~2%，监控 AUROC 0.93 但轨迹外泛化差；
  **Frontier Risk Report (2026-05)**：≥8h 长任务 "at least 16% of successful runs were
  illegitimate"，MirrorCode 场景 Opus 4.6 ~80% 尝试作弊，计入作弊后能力时长虚高 ~2×。
  阴性对照：MLE-bench 提交式评分（判分与 agent 隔离）扫描**未发现**作弊。
  Anthropic 2406.10162 是刻意构造课程（45/32768），作者自陈夸大，**与 METR 自然部署
  系列严格分开引用**。
- 训练侧：MAPPA (2601.23228) 产物存在性归因 +5.0–17.5pp；MiRA (2603.19685) 里程碑
  密集奖励 +7.9pp；但 **Demystifying RL (2603.21972)**：纯密集 33.1% < 纯稀疏 33.8% <
  退火 34.9%——"越密越好"不成立；SWE-RL (2502.18449) 纯 outcome-only 反例——
  中间产物奖励远非共识。

### A4 P-1 待人工复核
MLE-bench A.6 页码；MLE-STAR 仓库现址 404 与归属；SWE-Marathon PT 定义句；
CMU 2603.24768 统计数字；METR 两口径必须分开标注。

## B. P-2 深读复核（5 篇全文级，含对初查档案的更正）

- **BAGEN (2606.00198)**：**无"预算写进提示"对照**——rollout 与估算分离（§3.2
  "we avoid interleaving estimation with execution"、"Online estimation is left to
  future work"，独立抽查 verbatim 确认）；执行 agent 的 system prompt（App G）不含预算
  数字。Early-stop 省 28-64%（Table 3，failed trajectories 限定，成功率代价 1.6-4.2pp
  仅文字）；47% coverage 专指微调后 Qwen2.5-7B 单环境（配套 MRE 28% vs 49% 论文自身
  不一致）；r≈0.35 变量对论文表述含混。**定位：本问题空枪，仅背景参数。**高置信。
- **s1 (2501.19393) Table 3 全表（AIME24）**：BF 100%控制/slope15/**56.7**；TCC 40%/−24/40.0；
  **TCC+BF 100%/13/40.0**；SCC 60%/3/36.7；**SCC+BF 100%/6/36.7**；CCC 50%/25/36.7；
  RS 100%/−35/40.0。混合行新证：后端强制修好控制精度后**准确率零改善**——数字本身
  近零贡献变量。"cannot reliably count tokens **even when trained to do so**"；
  "hack its way around" 归属 **SCC**。App E.1 逐字模板未取得（不可引用具体句子）。
- **TALE (2412.18547)**：6 具名数据集 + Average 行（"7"是论文自身不精确）；GSM8K +3.11pp
  /−75.7% token ↔ MathBench-College **−8.00pp**；降幅 2.72–3.53pp 视口径（原文未给）；
  弹性失效原句 "'gives up' on complying"（预算 10 → 输出 157 token 反弹，对错未提及）；
  预算 N 是**逐题 zero-shot 估算**——TALE 从不支持"一个通用数字"。
- **Time's Up! (2504.14350，现题 "An Empirical Study of LLM Reasoning Ability Under
  Strict Output Length Constraint"，EMNLP 2025）**：α=25 token 收尾信号 vs 硬截断
  "up to 5%"（Figure 2+§5.1，非表格、非 5pp）；机制=一次性规则声明+单次不含数字的
  动态触发（既非 P-2 也非 P-2b 字面形式）；**初查"95%+ 成本降"查无出处，废弃**。
- **Real-Time Deadlines (2601.13206)**：4.0%→32.3%（12/300→97/300，708% 自洽校验通过）
  **仅 GPT-5.1-chat-latest 一行**；plain GPT-5.1 0.3%→2.7%、Qwen3-8b 31.3%→31.7%、
  Claude Sonnet-4.5 地板；6× 是报价级回归 OR=6.38（与 8× 不同层级不可通约）；
  注入格式 `"(N seconds left)\n"`；Control 开局句不含收尾余量措辞；
  **轮次消融：5-9 轮离散上限下成交率 98-100%**——失败是连续墙钟时间特有
  （"aligned with its token-based interface (turn counts)"）；该条件是否逐轮报数
  **未证实（人工复核第一优先）**；无中间形态（每 N 轮更新）条件。

## C. P-2 扫野 13 条（新证据主表精简）

1. **BATS (2511.17006)** 多步agent：Tracker 持续注入——**裸可见 +2pp，配规划/验证
   +12pp**（12.6→14.6→24.6%）；budget=10+Tracker 打平 budget=100 基线，成本 −31.1%。中高。
2. **BAVT (2603.12634)**：budget hint 进 system prompt + 价值树搜索；Low 档 5 调用
   0.338 EM > High 档 20 调用基线 0.334；消融去预算感知节点选择 0.388→0.309。中高。
3. **EcoAgent-Bench (2608.05519)** 反例：预算是显式任务条件，但 agent 响应"不对称且
   方向错误"（deep-research 使用率 0%→0.9%→2.6%；花费单调升但质量不升）。中高。
4. **INTENT (2602.11541)**：指令式告知在硬预算下挣扎；持续成本反馈提升但仍留违规率
   （"32.8%"未证实）。中。
5. **Temporally Blind (2510.23853)** 反例（多步 agent 域）：时间戳助大模型 ~+5pp，
   **小模型（8B 级）反降**；few-shot 仅对 o3/o4-mini 有效；结论"需要 post-training
   而非 prompt engineering"。高。
6. **Timely-RL (2601.16486)**：训练模型**主动持续查** get_duration()；0.75× 预算下
   按时完成 53.3% vs 基线 ~30%；SFT+Timely-RL 53.3% > SFT+GRPO 33.3%。高。
7. **BudgetThinker (2508.17196)**：**持续/一次性/无提醒三臂消融存在**（单轮数学）；
   +4.9% 数字未核实。中。
8. LLM 拖延模拟 (2402.08755)：短 deadline 类人、长 deadline 关系消失——单次告知
   远期限效力衰减。中。
9. **SelfBudgeter (2505.11274)**：自估预算可训练（单轮数学 65.4% 紧校准 vs BAGEN
   多步 47%）——域差异线索。高。
10. **L1/LCPO (2503.04697)**：同样"提示写数字"，RL 训练后从 s1 的低于基线变成可靠
    服从（长度偏差 ~3%）——**把 s1 失败定位为"训练缺失"而非"形式注定"**。高。
11. PDP (2601.11038)：不暴露预算也能改进 anytime（+20.3% Anytime Index）——可见性
    非唯一路径。中。
12. hermes-agent #414：两层预警提案，零数据，Open——与 OpenHands #2406 同型第二例。高。
13. 基准横扫：τ-bench/Terminal-Bench/WebArena 默认均不把预算写进 prompt，也无消融。中。

不采/存疑：ThinkPrune"过早收尾"未证实；Agent Contracts 是外部强制（正交）；
AOrchestra 有 "[Step {n}/{max}] Remaining" 字面格式但无消融（仅证明存在于实践）。

## D. 工程实践矩阵（7 系统源码级）

| 系统 | 模型侧形态 | 关键证据 | 置信 |
|---|---|---|---|
| OpenHands V1 | **不暴露**（维护者两次明确反对：#2406 stale-closed；PR #2453 review 砍掉 85/95% 提醒："expected behavior is not to have a reminder"） | 源码+PR 原文 | 高 |
| SWE-agent | 不暴露 + 越界**静默 autosubmit**（handle_error_with_autosubmission，模型看不见） | 源码 | 高 |
| mini-swe-agent 默认/跑分 | 不暴露（step_limit=250 不进 prompt） | 源码 | 高 |
| **mini-swe-agent programbench.yaml** | **持续倒数+收尾强制**（剩余<20 步注入 `<IMPORTANT>...steps away from limit...write AGENT_REPORT.md`；<10min 平行倒计时）——本次最完整闭环 | 源码；ProgramBench 2605.03546：98.1% 主动提交（使用统计非消融） | 高/中 |
| Claude Code / Codex / Gemini CLI | 模型侧**不暴露**；持续可见只做 UI 层给人看；压缩=一次性事件标记不含数字（Context Editing API 有 29%/39% 数据但归属 CLI 存疑） | 官方文档/源码 | 高/中高 |
| AIDE 官方 | 不暴露（timeout 提示是单次执行超时非任务预算） | 源码 4 文件 | 高 |
| **AIDE 经 MLE-bench 包装** | **一次性告知+每轮末尾插剩余时间/步数**（A.6 "We additionally insert information about the time and steps remaining into the end-of-turn prompt for all scaffolds"）——无消融 | 论文原文 | 高 |
| **RE-Bench modular** | **持续倒数+阈值两档**（`_context_and_usage_aware`：每轮 "you have used {fraction} out of {limit}"，>75% "reach a final answer soon"，>90% "submit soon"）——无消融 | 论文+源码互证 | 高 |

三句综合：生产默认=不暴露；一旦暴露，三个独立代码库收敛到"每轮倒数+阈值催促+后端
强制"，**纯一次性告知无生产对照物**；全网零消融——HarnessX 若做对照即该问题在
生产级 harness 场景的第一批数据。
