# P-1/P-2 先例证据档案 — draft-early 与 budget-visibility 的文献实测

> 2026-08-13，为 vendored 偏离补丁 P-1（draft manifests early）/ P-2（budget
> visibility）的发车门裁决收集的先例调查。方法：web 检索 + 原文抓取，每条带
> 置信级（高=原文数字两次独立抓取一致；中=单次抓取/转述；低=仅摘要，用前必查原文）。
> 结论供裁决参考；写入论文前按"待人工复核清单"逐条肉眼核对。

## P-1 族：draft / submit early（先落盘再迭代）

| 工作 | 干预形态 | 实测 | 与 P-1 关系 | 置信 |
|---|---|---|---|---|
| MLE-bench (2410.07095) + AIDE | AIDE 树搜索硬编码 drafting 先行；App A.6 在提示里加重"必须生成合法 submission.csv"，无该文件=buggy | AIDE+o1-preview 有效提交 82.8%±1.1、+GPT-4o 54.9%±1.0 vs OpenHands 52.0%、MLAB 44.3%；正文点名"过早收工零提交"为独立失败模式 | **支持**（scaffold 级机制，非纯提示；无正面消融） | 高 |
| AIDE (2502.13138) | drafting/debugging/improving 三算子形式化 | 无自做消融，量化引 MLE-bench | 部分支持（机制坐实，反事实缺失） | 中 |
| METR RE-Bench (2411.15114) | 打分=全程最高分记账，agent 可随时查分 | agent 查分 25-37 次/时 vs 人类 3.4；间接证明"随时有被记账的草稿"被高频使用 | 部分支持（环境可供性，非提示干预） | 中 |
| OpenHands issue #2406 | 提案：预算写进 system prompt + 80-95% 处收尾警告 | **零数据，Closed as not planned**；现产行为=触顶硬 ERROR，未整理工作全丢 | 阴性对照：问题真实存在、解法未验证 | 高 |
| deer-flow issue #2820 | 无机制、纯 150-turn 硬顶的自然实验 | 探索即耗 50-80 turns，"forces agents to rush...before exhausting their budget"——常连 patch 都没产出 | 支持 P-1 的反面论证 | 中低 |
| Runaway is Ashamed (2505.17616) | anytime 反方向：EXIT 动作/外部 verifier 提前停 | 省 50-70% 冗余步，成功率仅"minor"降 | 同源类比（该停时停 ≠ 该交时先交） | 中 |

## P-2 族：budget visibility（预算明写 + 收尾余量）

| 工作 | 干预形态 | 实测 | 与 P-2 关系 | 置信 |
|---|---|---|---|---|
| s1 (2501.19393) Table 3 | 提示词写 token 数/步数/泛化指令 vs decode 端 budget forcing | AIME24：decode 强制 56.7%（控制精度 100%）；**提示词三法 36.7–40.0%，全部低于无干预基线 50%**；token-conditional 负 scaling（−24）；"model cannot reliably count tokens"、"hack its way around the constraint" | **矛盾**（对"数字写进提示"的最干净负证；域=单轮数学，外推 agent 需谨慎） | 高 |
| TALE (2412.18547) Table 3 | 提示词逐字写 "use less than N tokens" | 7 数据集均值 token −67%、准确率 −<3pp；但 GSM8K +3.1 / MathBench-College **−8.0**；预算过紧（50→10）模型不服从（输出反增 86→157 token） | 部分支持，有边界：简单任务赚、长链任务亏、过紧失效 | 高 |
| Time's Up! (2504.14350) | 预算耗尽前 25 token 注入收尾信号 vs 硬截断 | 难数据集上比硬截断最高 +5pp；预算收紧 95%+ 成本降仍可用 | **支持"留收尾余量"子命题**（非"明写数字"） | 中 |
| Real-Time Deadlines (2601.13206) | 每轮更新"剩余时间" vs 开局一次性告知 | GPT-5.1 成交率 32% vs 4%（8×）；接受率 6× | **支持"持续可见"强于"一次性告知"**（域=谈判对话，迁移谨慎） | 中 |
| AI Agents That Matter (2407.01502) | （澄清）成本受控**评测**方法论，不涉运行时预算暴露 | "simple baselines Pareto-dominate SOTA agents at ~50× lower cost"（定性可信；Table A1 具体美元数抓取不一致，勿直接引用） | **不适用**——最易被误引的一条 | 定性高/数字低 |
| SWE-agent (2405.15793) | 有 step/cost limit 配置；**现仓库模板未见预算注入提示**（早前摘要称有，两次抓取未证实，不采信） | 成功案例 93% 在预算内提交 vs 全体 69%；中位成功 $1.21/12 步 vs 失败 $2.52/21 步 | 空白印证：主流 harness 默认不做 budget visibility | 中 |
| BAGEN (2606.00198) | agent 自估剩余预算（反向问题） | 摘要级：early stop 省 28-64% token；前沿模型"consistently over-optimistic"；校准 47%；能力与预算感 r=0.35 | 高度对口，**待读全文**（优先补读） | 低 |

## 综合判断（原文结论，节略）

两条干预均处"机制讲得通、业界公认需要、缺严格因果消融"阶段。P-1 最强证据是
scaffold 级（AIDE 算子 + MLE-bench 失败模式记录 + RE-Bench 记账设计互证），
deer-flow 反面印证；**无正面 A/B**。P-2 出现最干净反例（s1：提示词写预算全面弱于
decode 强制、部分弱于基线），机制解释=模型不会自我计数且会绕约束；但**"留收尾
余量"与"持续可见"两个窄子命题分别在两个独立域拿到正数**（+5pp；8×）。已知失效
条件：任务越难提示侧预算越反噬（TALE −8pp）；预算过紧模型不服从；纯提示无后端
强制时控制精度仅 40-60%。**真实多步 agent 任务上"预算是否写入提示"的严格 A/B
是文献空白**——HarnessX 阶梯若做实测，属该问题在 agentic 场景的第一批对照数据。

## 待人工复核清单

s1 Table 3（写论文前肉眼核对页码表号）；TALE 弹性失效的准确率代价未取得；
AI-Agents-That-Matter Table A1 美元数需查原文；BAGEN 全文优先补读；
SWE-agent 提示模板结论基于现仓库 default.yaml 两次抓取。
