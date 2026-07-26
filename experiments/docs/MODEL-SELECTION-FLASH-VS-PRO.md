# Worker 选型:DeepSeek V4-Flash vs V4-Pro(案头分析,2026-07-26)

> 用户令:不跑对照实验,以官方跑分+博客裁定。本文为 researcher 案头分析的存档
> (全部来源当日亲验,见文末台账);裁决记 SPEC §7.13。

## 裁决:worker = **V4-Flash(thinking 开启,effort=high 默认)**;Pro 不用于 worker

## 两档是什么(官方口径)

| | Flash | Pro |
|---|---|---|
| 总/激活参数 | 284B / **13B** | 1.6T / **49B** |
| 训练关系 | **独立预训练**(非 Pro 蒸馏) | 独立预训练 |
| 上下文 | 1M | 1M |
| 模式 | Non-think / High / Max(Max=最大推理努力) | 同 |

## 关键基准差距(官方 HF 模型卡表,Max 档)

| 基准 | Flash | Pro | 差距 | 与我们工作负载的相关性 |
|---|---|---|---|---|
| **BrowseComp**(网页研究,最贴 GAIA) | 73.2(High 仅 53.5) | 83.4 | **10.2**(High 档差 26.9) | ★★★ |
| HLE w/ tools | 45.1 | 48.2 | 3.1 | ★★★ |
| Terminal-Bench 2.0 | 56.9 | 67.9 | 11.0 | ★★ |
| SWE-bench Verified | 79.0 | 80.6 | **1.6** | ★(脚手架内≈平手) |
| **SimpleQA-Verified**(事实回忆) | 34.1 | **57.9** | **23.8** | ★★(幻觉面) |
| GPQA-D / LiveCodeBench / MMLU-Pro | 88.1 / 91.6 / 86.2 | 90.1 / 93.5 / 87.5 | ≈2 | 通用推理≈平手 |
| MRCR-1M(长上下文) | **Non-think 37.5** / think 78.7 | 44.7 / 83.5 | — | ★★★(见红线) |

**规律:差距随任务的开放网页/事实性程度增大**(脚手架编码≈平手,开放网页检索 10-27 分)。我们的工作负载在大差距区——但这恰是**实验设计要的**(见理由 b)。

## 裁决理由(五条)

a. **失败余量约束**:Pro 在 BrowseComp 已近开源天花板(80-83),会压平 harness 演化
   的可测效应窗;Flash 落在曲线中段(53-73),与本地先验(校准床 33-67% pass@2)一致。
b. **成本**:单价 3.1×;计入 Flash ~1.3× 冗长度后**每任务 ≈2.4×**——在数千 rollout
   的正式实验里是 ¥1.2-2k vs ¥4-6k 的差别。
c. **失败签名有利**:第三方实测(kilo.ai)明确 Flash 工具调用机制干净(无跑飞重试、
   无畸形参数),失败在实质正确性/长上下文检索——这是隔离 harness 效应的"好失败模式"。
d. **红线已满足**:Flash 的 Non-think 长上下文崩塌(MRCR-1M 37.5)是唯一文档化硬伤;
   官方 API `thinking` 默认 enabled、effort 默认 high(api-docs/guides/thinking_mode,
   07-26 亲验),且我方全部轨迹含非空 Thinking 块——**从未跑在崩塌区**。
e. **两项无公开数据的风险留给 pilot 实测**(官方无 IFEval/GAIA/方差拆分,不得内插):
   严格 FINAL ANSWER 格式依从、20 步轨迹的 run-to-run 方差。

## 冻结时的动作
- lock 记:`worker=deepseek-v4-flash`,`thinking=enabled(显式传参,勿依赖服务端默认)`,
  effort=high(记录文档默认;服务端对"复杂 agent 请求"自动升 max,属不可控项,如实记录);
- Pro 保留为 meta-agent(现状)与论文期"escalation 对照"选项。

## 来源台账(researcher 当日亲开)
官方:HF Flash/Pro README(基准表)、api-docs.deepseek.com 发布注记 + thinking_mode 指南、
arXiv 2606.19348(架构/独立训练/无方差指标确认)。第三方:Artificial Analysis(文章+两模型页;
Intelligence Index 两快照不一致 52/47 vs 44/40,并报)、blog.kilo.ai 单任务实测、towardsai
20 任务实测。**明确不可验(未采信)**:官方模型卡 PDF(本环境不可提取)、"200-run loop
161 vs 154"与"77% call-success"等 SEO 传言、任何两档的 IFEval/GAIA/τ-bench/BFCL 拆分
(官方表无此项)。
