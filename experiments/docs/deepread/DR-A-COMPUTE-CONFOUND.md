# DR-A — 算力混淆三件套 深读档案(正文+附录级)

> 目的:为 Q2 实验设计的 **裁 C/D(headline 改口 + SA-matched 臂)** 提供 S5 级承重依据。
> 每个承重数字落到 section/table 锚点。相对我方现存断言(NOVELTY-EXPDESIGN-RESEARCH.md PART C.1/C.2/D)判三态:CONFIRMED / WEAKENED / REVERSED。
> 日期:2026-07-30。护栏:research-guardrails(enforce)。约束:WebSearch/WebFetch only。

## 来源可达性与核验方法(诚实声明)

- 全部经 **WebFetch**(arxiv.org/html/<ID>,小模型转 markdown 后应答)提取,**非直接读取原始文本**;故对易被改写的承重项做了**双次独立抓取交叉核验**。
- **身份已锚定(非幻觉)**:2606.13003 摘要**逐字复现**了我方 PART C.1 早已独立记录的两段(“…underperform CoT-SC despite being up to 10x more expensive” 与 “…failing to account for the marginal utility of increased computational cost”)→ 抓取忠实。2604.02460 作者 = Tran & Kiela(Stanford)= 任务点名一致。2601.12307 方法名 “OneFlow” 在 Table 1 出现 = 一致。
- **附录可达**:2606.13003 的 Table 4 在 Appendix B,已成功取到 → HTML 含附录。
- **单次抓取项(置信度降一档,已标注)**:2606.15017 全部数字;2601.12307 Table 1 单/多智能体逐 benchmark 数字。
- **一处抓取内部不一致已解决**:2606.13003 Table 4 Gemini-2.5-Pro 列“最佳 MAS”首抓 MaAS 81.85、复核误报 DyLAN 66.07;第三次定点抓取确认 **MaAS 81.85 为该列 MAS 最大值**(DyLAN 66.07 只是首行,被误当极值)。以定点抓取为准。

---

## 1. arXiv 2606.13003 — “The Illusion of Multi-Agent Advantage”

**作者/机构**:Jwalapuram, Lin, Li, Jiao, Wang, Ming, Ke, Qin, Carenini, Joty(Salesforce Research / HKUST-GZ / UBC / NTU)。

### 设置卡
- **对象**:自动生成型 MAS(automatic MAS,强调泛化性)vs 单智能体 **CoT-SC**。
- **MAS 框架(§3.1)**:DyLAN、MAS-Zero、ADAS、AFlow、MaAS、MAS-Orchestra(6 个)。
- **CoT-SC 的 k**:**5-sample majority vote**,跨所有数据集与底座固定(§3.1:“CoT-SC baseline employs a 5-sample majority vote across all datasets and backbones”)。
- **床(§3.1)**:GPQA-Diamond、HLE-Maths、SWE-Bench Lite、**BrowseComp-Plus**(web/交互多步系)+ 自建诊断合成集 SMFR。
- **运行**:3 independent runs 取均值;**Gemini-2.5-Pro 单次运行(成本)**。仅报 ± 标准差,**无 p 值/显著性检验**。
- **n(§ Table 3, App A)**:GPQA-D 166 / HLE-Maths 168 / SWE-Bench Lite 168 / **BrowseComp-Plus 268** / SMFR 588。

### 承重数字表
| 项 | 值 | 锚点 |
|---|---|---|
| “10x more expensive” | 摘要逐字;§3.2:“CoT-SC … frequently achieving higher accuracy at **less than 10% of the computational cost**” | Abstract + §3.2 + Fig.2 caption(“frequently incur 10× inference costs vs. SAS baselines for negligible gains, except on HLE-Math”) |
| **预算配平机制** | **无主动配平**。各系统按**默认配置 as-deployed** 运行,**只报实测成本**;10x = **未配平的观测成本差**(非配平后残差) | §3.1–3.2 |
| 其对“未控预算”的批评(针对他人) | “these comparisons rarely control for inference budgets such as number of LLM calls, total cost, retries, or sampled paths” | §1 Introduction |
| **BrowseComp-Plus:CoT-SC vs 最佳 MAS** | **CoT-SC 全床 4 底座均 > 一切 MAS**:GPT-4o 67.26 vs MaAS 64.88(**+2.38**);GPT-OSS-120B 70.43 vs MaAS 68.85(**+1.58**);GPT-5 83.92 vs MaAS 81.55(**+2.37**);Gemini-2.5-Pro 83.04 vs MaAS 81.85(**+1.19**) | **Table 4, Appendix B** |

### 三态判定(相对我方 PART C.1 断言1)
- 我方原文锁死引用(“automatic MAS consistently underperform CoT-SC despite being up to 10x more expensive … marginal utility of increased computational cost”,“床含 BrowseComp-Plus”)→ **CONFIRMED(逐字 + 附录数字齐)**。
- **但一处 WEAKENED(归因精修,重要)**:此篇**不做预算配平**,其论证是“**MAS 花 10x 成本仍输**(架构臃肿/bloat)”,**不是**“**配平预算后优势消失**”。我方 PART C.1 把它当作“配平即消失”杀的一手锁,属**轻度误用**——“配平→消失”的一手证据应改由 **2604.02460 + 2606.15017** 承担;2606.13003 承担的是“**成本-无效性/架构臃肿**”动机与**web 床上 MAS<CoT-SC 的存在性**。
- **web 床成立度**:MAS < CoT-SC 在 BrowseComp-Plus **方向成立且 4/4 底座一致**,但**margin 仅 1.19–2.38pp**,且与部分 cell 的 ±std(如 ±1.5–1.6)重叠、**无显著性检验、Gemini 单次运行** → 判 **CONFIRMED(方向)+ 附带效度 caveat(margin 小、未做显著性)**,写作时不得夸成“大幅落后”。

### 对裁 C/D 与 SA-matched 的启示
- 裁 C(headline 改口)动机引用**保留**此篇,但**措辞收敛**为:“web 类床上自动 MAS 未能胜过 5-sample CoT-SC,且成本高达 ~10×”——**不要**用它支撑“matched budget 下消失”。
- 采纳其**成本全报**纪律(model calls + $ + 墙钟),与我方 C.4-3 一致。
- 采纳其 **CoT-SC k=5** 作为我方 SA-matched“self-consistency 变体”的**对齐参照点**(但见 §4 关于“该配平什么资源”的更强建议)。

---

## 2. arXiv 2601.12307 — “Rethinking the Value of Multi-Agent Workflow: A Strong Single Agent Baseline”(方法名 **OneFlow**)

**作者**:Jiawei Xu, Arief Koesdwiady, Sisong Bei, Yan Han, Baixiang Huang, Dakuo Wang, Yutong Chen, Zheshen Wang, Peihao Wang, Pan Li, Ying Ding。
> 注:任务称“OneFlow”是**方法名**;论文标题如上。

### 设置卡
- **床(Table 1)**:HumanEval、MBPP(code)、GSM8K、MATH(math)、HotpotQA、DROP(QA)+ **TravelPlanner**(tool-use,Fig.3)。**无 GAIA、无 web 检索床**。
- **单体等价构造(§3.2 “Single agent implementation of multi-agent workflow”)**:单一 chat history $h_t$,按路由策略 $E$ 选 agent,将该 agent 的 system message $p_{i_t}$ **作为 user message 追加进共享对话史**,以固定解码参数 query 同一 base $b$,执行工具、更新状态——**全程一个模型实例、复用 KV cache**。

### 承重数字表(★ = 本次深读最关键)
| 项 | 逐字 / 值 | 锚点 |
|---|---|---|
| **★ homogeneous 精确定义** | “**Homogeneous workflows: \|ℬ(W)\|=1, where all agents share the same base LLM and differ only in their system prompts, tools, and positions within the workflow structure.**” | **§2 “Homogeneous vs. Heterogeneous Workflows”**(双抓取逐字一致) |
| heterogeneous 定义(对照) | “**Heterogeneous workflows: \|ℬ(W)\|>1, where agents utilize different base LLMs** …” | §2 |
| 单体 ≈ 同质工作流(实证) | 单/多智能体逐床几乎相等,单体从不落后:HumanEval 92.1 vs 91.6;MBPP 81.4 vs 81.1;GSM8K 93.3 vs 93.0;MATH 54.1 vs 53.4;HotpotQA 73.5(并列);DROP 81.7 vs 81.1 | **Table 1**(单次抓取) |
| tool-use 上也成立 | “A single LLM executing the AFlow and OneFlow workflows **matches the task success rate** of their original multi-agent counterparts.” | §4 / **Fig.3 (TravelPlanner)** |
| KV 复用机制 | “Because every step calls the same base model b, the simulator **reuses the KV cache** across t, so the prefill cost scales with incremental growth ΔLt rather than the full prefix Lt.” | §3.2 |
| **★ “truly heterogeneous” 开放前沿句(Intro)** | “We also note that **single-LLM methods cannot capture heterogeneous workflows due to the lack of KV cache sharing across different LLMs**, highlighting **future opportunities in developing truly heterogeneous multi-agent systems**.” | **§1 Introduction**(双抓取逐字一致) |
| 同句(Conclusion) | “While single-LLM simulation **cannot realize true heterogeneity**, our pilot shows it can even match the performance of AFlow-optimized heterogeneous workflows.” | §6 Conclusion |
| KV-cache 论证性质 | **纯 discussion/prose(Intro),非 Proposition/Theorem,未实证检验**;heterogeneous pilot(§4.2.3)展示性能界但**未直接验证 KV 限制**。(首抓提到同质效率或有 “Proposition 1” 陈“asymptotically no worse”,但**异质“cannot capture”限制是散文断言**,以定点复核为准) | §1 / §4.2.3 |

### 三态判定(相对我方 PART C.1 断言1 与 PART D 候选1/“反守为攻”)
- **★ CONFIRMED 且 STRENGTHENED(威胁面)**:homogeneous = **同底座、仅 system prompt/tools/position 不同**。我方 **K=8 harness-config 变体 = 同一 DeepSeek 底座、仅 harness prompt/config 不同 → 逐字落入 OneFlow “homogeneous” 定义**。故“单智能体可吃掉我方变体池”的威胁**真实且精确**,SA-matched 臂**必需**。我方 PART D 候选1“under a matched compute budget” 的预掐是对的。
- **★ REVERSED(致命,须立即改写):我方 PART C.1 的“反守为攻——把 harness-config 差异 pitch 成 OneFlow 所称 truly heterogeneous 缝”不成立。** OneFlow 明确定义 heterogeneous 为 **|ℬ(W)|>1(不同 base LLM)**;我方同底座 config 变体**按其定义是 homogeneous、KV 可共享**,单体 simulator 原则上**能**廉价复现之。“truly heterogeneous / KV 不可共享”只属于**换底座**的系统,**我方无权援引**。→ **删除该反攻话术**;B2 的胜出只能落**纯实证**(见启示)。
- **CONFIRMED(可用的反手点,但降格)**:KV-cache-异质限制是**断言非实证**(散文、无 proposition、pilot 未验),故“OneFlow 未封死异质前沿”**可作 related-work 中性引用**——但只对“**未来换底座异质系统**”有效,**不能**移植到我方同底座池。
- **边界(利好动机)**:OneFlow 的“单体≈同质”只在 code/math/QA + **TravelPlanner(tool-use)** 上验证,**未在 GAIA/web 检索验证**。我方床(GAIA-web,多步工具)更接近 TravelPlanner——而 OneFlow 在 TravelPlanner 上**单体也追平**,故威胁**延伸到 tool-use**,不能靠“床不同”开脱。

### 对裁 C/D 与 SA-matched 的启示
1. **SA-matched 臂坐实为“主威胁模型”**:它就是 OneFlow 的“单体执行同质工作流”。B2 **必须 > SA-matched(等预算)**,否则按 OneFlow 判为 architectural bloat。
2. **PART C.1/D 措辞硬改**:凡出现“truly heterogeneous / KV 不可共享 = 我方缝”的句子一律删除或改为“**同底座 config 特化路由的经验净值**”。B2 胜出叙事 = “**在演化出的同质 config 池上做子任务级特化路由,净胜于等算力单体多轮**”——不碰 heterogeneity。

---

## 3. arXiv 2604.02460 — “Single-Agent LLMs Outperform Multi-Agent Systems on Multi-Hop Reasoning Under Equal Thinking Token Budgets”(Tran & Kiela, Stanford)

### 设置卡
- **配平资源 = thinking tokens**:“the total number of tokens used for intermediate reasoning, **excluding prompts and final answers**”(§1);全体 MAS “operate under the same global thinking-token budget B”。
- **预算档(Table 1)**:**100, 500, 1000, 2000, 5000, 10000** thinking tokens。
- **床(§4.1)**:**FRAMES**(Krishna et al., 2025)、**MuSiQue**(Trivedi et al., 2022)——均 **text-only 多跳世界知识**,简答有真值。**非 web/工具床**。
- **臂**:SAS、SAS-L(加 scaffolding 的长思考变体);MAS = Sequential / Subtask-parallel / Parallel-roles / Debate / Ensemble(§4.3)。
- **统计**:**95% bootstrap CI**(Table 1 caption:“Bold indicates highest-accuracy systems and every other system whose 95% bootstrap confidence interval overlaps”)。
- **理论**:基于 **Data Processing Inequality** 的信息论论证——固定思考预算 + 完美上下文利用下,单体信息效率更高;并预测 **MAS 在单体上下文利用退化 / 花更多算力时才变得有竞争力**。

### 承重数字表
| 项 | 逐字 / 值 | 锚点 |
|---|---|---|
| headline | “**SAS is the best-performing system or statistically indistinguishable from the best for all budgets except the lowest one.**” | **§5.1** |
| 例(Gemini-2.5-Pro / FRAMES / 2000 tok) | SAS 0.700;Sequential 0.690;Debate 0.660;Ensemble 0.660 | §5 / Table 1 |
| **MAS 何时赢(masking 退化)** | “SAS leads at mild degradation (**α=0.3**) and the systems are roughly tied at moderate degradation, but **Sequential becomes better at heavy degradation (α=0.7)**.” | **§5.3** |
| MAS 何时赢(substitution 退化) | “SAS is ahead at α=0.3 … and **Sequential is clearly better at α=0.7**.” | §5.3 |
| 最强 MAS | “Debate is the most consistently strong MAS variant … often the strongest overall MAS architecture” —— **但仍与 SAS 统计重叠** | §5.1 |
| 最低预算(100)例外 | 该档 MAS 不劣仅因模型“do not produce a useful reasoning trace at all” | §5 |
| limitations | “(i) text-only multi-hop; MAS advantages with **tools/vision or safety** … out of scope. (ii) Gemini thinking accounting is approximate … (iii) we do not enforce the models to actually use up all of those budgets.” | **Appendix C** |

### 三态判定
- **CONFIRMED(升为一手主锚)**:这是三件套中**唯一严格 matched-budget + bootstrap CI** 的研究。它使“**配平(思考 token)预算后 SAS ≥ MAS**”成立——正是我方 SA-matched 臂**真正的**一手依据(接替 2606.13003 的误用位)。
- **边界(利好我方 B2 动机,重要)**:其信息论预测“**当单体有效上下文利用退化时,MAS/分解变得有竞争力**”,且 §5.3 实证 α=0.7 下 Sequential(分解式)胜。**GAIA-web 长多跳 + 大上下文正是“上下文利用可能退化”的 regime** → 为我方**分解(B2)在 GAIA 可能有正增益**提供了**有据的先验假设**(而非纯 wishful)。可作 B2 动机的一手引用。
- **WEAKENED(外部效度)**:床是 **text-only 多跳(FRAMES/MuSiQue)**,作者自认 **tools/vision out of scope**;我方 GAIA-web 是工具/浏览床,故其“clean-context 下 SAS 主导”**不能直接外推**到我方床——但其**方法学(配平 thinking token + bootstrap CI)可移植**,其**degraded-context→分解有用**的预测**利好**我方。

### 对裁 C/D 与 SA-matched 的启示
1. **配平资源改口**:Tran & Kiela 配平的是 **thinking tokens**,非“调用数”。我方 PART C.2/E.4 现写“总**调用**预算 = B2”**偏弱**——见 §4 综合建议。
2. **统计口径升级**:用 **95% bootstrap CI**(题内配对 + McNemar 已在 C.4;bootstrap CI 可作补充/替代“±5% 噪声带”这一无理论依据的阈值)。
3. **B2 动机可正面引用**:“decomposition helps when single-agent context utilization degrades”(§5.3 + 信息论预测)——把 GAIA 长上下文定位为该 regime。

---

## 4. arXiv 2606.15017 — “Are Online Skill and Memory Modules Always Worth Their Tokens? A Budget-Constrained Study of Web Agents”(尽力而为;单次抓取)

**作者/机构**:Hajimiri, Aminbeidokhti, Dolz, Ben Ayed, Laradji, Gella, Gontier(ServiceNow AI Research / ÉTS / UBC / McGill)。
> ⚠ 单次抓取;模型名(“GPT-5.4-mini”“Qwen 3.6-27B”“Gemini 3 Flash”)异常但对 2026-07 尚属可能,数字按单一来源对待。

### 设置卡 + 承重数字
| 项 | 逐字 / 值 | 锚点 |
|---|---|---|
| **token-matched 控制 = Vanilla-IB** | “**Vanilla-IB: a vanilla actor with its interaction horizon extended to 15 steps and with rule-based pruning of the accessibility tree.**” 预算花在**额外 observe-act 步**,非 voting/retry;作者自认“an **approximation rather than an exact budget match**” | **§3.2** |
| 增益消失(WebArena,3 域均值) | Gemini-3-Flash:Vanilla-IB **50.74** vs AWM 44.98 / ASI 47.86 / **ReasoningBank 45.54**;GPT-5.4-mini:VIB 36.63 vs 30.74/32.14/**28.42**;Qwen-3.6-27B:VIB 47.44 vs 43.58/45.61/**43.09** | **Table 1** |
| WorkArena-L1(Qwen-3.6-27B) | Vanilla-IB **55.56±2.9** ≈ ReasoningBank 55.56±2.8;> AWM 53.53±3.8;> ASI 48.49±4.3 | **Table 2** |
| 结论 | “a **token-matched vanilla actor matches or surpasses AWM, ASI, and ReasoningBank** in aggregate success rate while **often using fewer total tokens**.” | Abstract / §4 / §7 |

### 三态判定(相对 MEMORY.md 既存“ReasoningBank 增益在 token-matched vanilla 下消失”)
- **CONFIRMED**,并精修:不是单指 ReasoningBank,而是**三种 memory/skill 模块(AWM/ASI/ReasoningBank)在 web agent 床上一致被 token-matched vanilla 追平或反超**;matching 机制 = **给单体更多交互步(延长 horizon)**,非投票。
- **对我方最贴**:三件套里**唯一 web-agent 床 + 逐步 token 配平**,与我方 GAIA-web 最同构。其“**把预算花在更多 observe-act 步**”是**可直接移植的配平算子**。

### 对裁 C/D 与 SA-matched 的启示
- **SA-matched 的“花预算方式”**:对 GAIA-web 这类**多步工具 agent**,把配平预算给**更长交互 horizon 的单体**(Vanilla-IB 式)比“self-consistency 多路投票”**更自然、更强的对照**。我方 SA-matched **应含一个“长 horizon 单体”变体**,不能只做多数投票。

---

## 综合:三件套如何分工撑裁 C/D(勿混用)

| 论点 | 该由谁承担 | 不该由谁 |
|---|---|---|
| MAS 架构臃肿、花 ~10× 成本仍不胜、**web 床(BrowseComp-Plus)MAS<CoT-SC 存在性** | **2606.13003**(未配平、报成本) | — |
| **配平预算后单体 ≥ MAS**(SA-matched 的真正依据) | **2604.02460**(matched thinking-token + bootstrap CI)+ **2606.15017**(matched steps,web 床) | ✗ 2606.13003(它不配平) |
| 我方**同底座 K=8 = homogeneous**,单体可吃 → SA-matched 必需 | **2601.12307** homogeneous 定义(逐字) | — |
| “truly heterogeneous / KV 不可共享 = 我方缝”反攻 | **无人**(REVERSED,删) | ✗ 2601.12307(其 heterogeneous=换底座) |
| **分解在退化上下文可有正增益**(B2 动机先验) | **2604.02460** §5.3 + 信息论预测 | — |

### SA-matched 臂 · 综合设计建议(裁 D)
1. **配平资源从“调用数”改为“总 token(+ $ + 墙钟)”**:2604.02460 配平 thinking-token、2606.15017 配平 total-token;调用数不等价于算力(一次 MAS 调用与一次单体调用 token 足迹迥异)。**headline 的等预算须以 token/成本为准,调用数并报**。
2. **SA-matched 含两形态**:(a) **长 horizon 单体**(Vanilla-IB 式,延长交互步)——对 GAIA-web 最强;(b) self-consistency 多数投票(k 对齐 2606.13003 的 5)。B2 须 **> 两者**。
3. **统计口径**:引入 **95% bootstrap CI**(2604.02460)作为“±5% 噪声带”的有理论依据替代/补充;仍保留题内配对 McNemar。
4. **预注册负结果口径**:若 B2 ≤ SA-matched,**如实报“同底座 config 池的特化路由无净架构优势”**(与三篇一致),不得改口。

---

## 与我方现存断言的三态清单(速查)
- 2606.13003“MAS<CoT-SC / 10× / BrowseComp-Plus” → **CONFIRMED**;归因“配平即消失” → **WEAKENED**(此篇不配平,改由 2604.02460/2606.15017 承)。
- 2601.12307 homogeneous=同底座仅 prompt 异 → **CONFIRMED + 威胁 STRENGTHENED**;我方“harness-config=truly heterogeneous 反攻” → **REVERSED(删)**;KV-异质限制=断言非实证 → **CONFIRMED(仅对换底座有效)**。
- 2604.02460 matched-thinking-token 下 SAS≥MAS → **CONFIRMED(升为一手主锚)**;可外推到 web 床 → **WEAKENED(text-only,作者自限)**;分解在退化上下文有用 → **CONFIRMED(利好 B2 动机)**。
- 2606.15017 ReasoningBank 等在 token-matched vanilla 下消失 → **CONFIRMED**(web 床、matched-steps、最贴我方)。

## 遗留 / 存疑(guardrails C)
- 全部数字经小模型抓取;pivotal 引句已双抓取一致,Table 4 三抓取定案;OneFlow Table 1 与 2606.15017 全表为单次抓取,承重前建议本地下载 PDF 复核。
- 2601.12307 “Proposition 1”是否存在(同质效率的形式陈述)两抓取不一致,已按“异质限制=散文断言”定案;若正文引用同质 proposition 需再核。
- 2606.13003 web 床 margin(1.19–2.38pp)与 std 重叠、无显著性检验 → 写作勿夸大。
