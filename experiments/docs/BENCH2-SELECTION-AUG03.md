# 第二评测床选型 — 裁决记录（2026-08-03）

**接续**：`FINDINGS-AUG01.md` D-1（GAIA 内部无法构造 held-out）、E-5（SD 口径未统一）、E-7（分层预注册）。
**动因**：GAIA-Text-103 已无 held-out、n=103 功效弱、且逼近有效天花板（74.8% vs 93.2%）。

**置信度标注约定**（针对既往「MAP 置信度污染」教训，逐条标明来源与核验深度）：

- 🔬 **亲验** = 本次主循环直接读原文件 / 原论文表格得到
- 📄 **子代理报告** = researcher 返回，主循环未独立复核
- 🌐 **外部 L2 / L1** = 论文原表 / README-leaderboard

---

## 0. 一句话结论

> **Aug-03 二次修订（用户令「找一个权威点的 benchmark」）：主第二床改为 BrowseComp。** 详见 §2.5。以下 MuSiQue / InfoDeepSeek 段落降为备份方案，保留其分析。

**主第二床 = BrowseComp（OpenAI，1,266 题，MIT）。** 生死项已 🔬 亲验通过：发布 CSV 含明文 `problem_topic` 列，10 类、0 空值，有效池 `min(K, 9)`，池不塌缩。
**落点已知**：V4-Flash 官方 BrowseComp = **53.5（effort=high）/ 73.2（max）**，Pro 83.4 —— 正中甜区且天花板在上。
**FRAMES 出局**（太简单，V4-Flash 会顶到 82–90%）。**MuSiQue / InfoDeepSeek 降为备份**（前者无前沿实测数 + 2019/2026 时间漂移，后者 n=245 + license 存疑）。

**三个必须接受的前提**：① Serper 配额不够做演化跑（需加购）；② 逐簇 EM 洁净度不均 × 按簇路由 = 隐性混淆；③ 约 40% 题可闭卷答出，需 no-tools 控制臂。

**结构性利好**：live-web MuSiQue 上裸规模斜率非单调（32B vanilla 18.9 < 7B-RL 27.1），**harness 是主效应** —— 变体不会被模型强度碾平，正面回应 §7 的 scaffold 压平威胁。BrowseComp 同理：弱模型有 0 分地板（换 scaffold 只挪 0–3 分），但 **V4-Flash@53.5 远在地板之上**，变体预期可分。

---

## 1. 判据（三条硬门 + 一条量化门）

| # | 门 | 依据 | 来源 |
|---|---|---|---|
| 1 | **短答案** — scorer 是自写二值 normalized-EM（数值 1%/3% 容差 + 0.9 fuzzy + 逗号列表包含），LLM judge 仅在无标答时兜底触发 | `benchmarks/gaia/evaluator.py:437-496`、`:59-112` | 📄 |
| 2 | **每题须有原生簇标签** — 变体池按簇路由，有效池 = `min(K, n_clusters)`；平床 → 池塌缩成 1，Ensemble 机制空转 | CH3 §3.5、`run_meta.py` | 📄 |
| 3 | **工具面兼容** — 仅有 `web_search / web_fetch / browser / read / bash`，固定不可变；无本地检索器 | `harnessx/tools/builtin/__init__.py:140-152` | 📄 |
| 4 | **落点须在甜区** — 太高无空间、太低地板效应压平变体差异。目标 **30–60%** | 本次新增判据（用户指定） | — |

### 功效换算（决定采样量）

RUN-LOG 已实证 SD 随 √n 缩放（n=50 实测 6.41pp vs 预测 6.56pp，`RUN-LOG.md:1553-1557`）📄。往大了外推：

| n | 投影 SD | 2·SD 检出门槛 |
|---|---|---|
| 103（现状） | 4.57pp | **9.1pp** |
| 250 | ~2.9pp | ~5.9pp |
| **512** | **~2.1pp** | **~4.1pp** |
| 824 | ~1.6pp | ~3.2pp |

> ⚠️ 外推假设噪声以二项采样为主；实测只验证过**缩小**方向，放大方向是投影，非实测。

**配对必需**：非配对两比例检验测 10pp 需 ~387 题/臂；同题配对 + McNemar 只需 107–233 题（取决于不一致率）📄。**新床必须同题配对跑**。

---

## 2. 逐床裁决

### ✅ 采用

| 床 | 规模 / license | 簇键 | 判分 | 落点估计 | 角色 |
|---|---|---|---|---|---|
| **MuSiQue-Ans** | 2,417 dev / CC-BY-4.0 | **2/3/4-hop**（编码在 question id） | 原生 EM + F1，**零 judge** | **~32–55%** | 主力第二床 |

**选它的四条理由**：

1. **难度来自构造而非新鲜度** — 构造时做过组合式反重叠过滤，2026 模型闭卷 EM 仅 0.18；同为维基来源的 HotpotQA 闭卷 0.40 且被多篇论文点名「模型在作弊」📄。不会随模型变强而失效。
2. **真检索下确实难**（🔬 亲验 `arXiv 2504.03160` Table 2，live open web，512 题采样，Qwen2.5-7B）：

   | 系统 | F1 | GPT-4o-mini judge |
   |---|---|---|
   | CoT（闭卷） | 8.5 | 7.4 |
   | Search-o1 | 14.7 | 19.7 |
   | R1-Searcher | 22.8 | 25.6 |
   | Search-R1 | 26.5–26.7 | 27.5–28.3 |
   | DeepResearcher | 27.1 | 29.3 |

   **规模斜率平、harness 斜率陡**（Aug-03 补，📄）：同为 live-web MuSiQue，**QwQ-32B vanilla 仅 F1 18.9 < Qwen-7B+RL-search 的 27.1**；32B+RL-search 30.6–34.8。裸规模非单调，搜索能力/harness 才是主效应。
   → **对本实验是结构性利好**，且正面回应 `2606.08529` 的威胁（「底模越强 scaffold 差距越小」）：在 MuSiQue 上变体会分离，不会被模型强度碾平。
3. **hop 数 = E-7 预注册的干净外部刻度**。`FINDINGS-AUG01.md` C-3 已量到增益集中在高组合度/长步数题（1 项能力 75→81.2；3 项 47.4→57.9；1–4 步 80→92；9+ 步 51.5→63.6）。MuSiQue 的 2/3/4-hop 带真值标签，是这个假设的受控外部验证。
4. **零 judge**，保住我们床唯一的干净属性。

**已发表多步增益 +7～9 F1（IRCoT）📄** —— 在旧的 9.1pp 门槛下测不出，在 n=512 的 4.1pp 门槛下测得出。**这是采样量必须 ≥500 的直接理由。**

---

## 2.5 ⭐ 主第二床：BrowseComp（Aug-03 二次修订）

### 2.5.1 生死项 —— 🔬 主循环亲验通过

下载官方 CSV（`https://openaipublic.blob.core.windows.net/simple-evals/browse_comp_test_set.csv`，1,196,283 bytes）后本地解析：

```
表头: problem,answer,problem_topic,canary        1266 行
TV shows & movies 205 | Other 197 | Science & technology 173 | Art 127
History 125 | Sports 123 | Music 116 | Video games 71 | Geography 70 | Politics 59
problem_topic 空值: 0
```

- **`problem_topic` 是明文列**（仅 `problem` / `answer` 被 XOR 加密，key 由 SHA-256(canary) 派生）。
- 有效池 = **`min(K, 9)`**（剔除语义杂烩的 `Other`）或 `min(K, 10)`。**不塌缩。**
- 最小干净簇 = Politics(59) / Geography(70) / Video games(71) → **逐簇均衡抽样的绑定约束**。
- **无难度分层字段**，只有 topic。若需难度路由须自建（如以 no-tools 可答性代理）。

> ⚠️ **易踩空点**：官方 eval 脚本 `browsecomp_eval.py` 只读 `problem/answer/canary`，**不读 `problem_topic`**。故二手资料普遍称「BrowseComp 无分类标签」——**错**。须自行 `read_csv` 取该列做路由。

### 2.5.2 为何胜过 MuSiQue / InfoDeepSeek

| 维度 | BrowseComp | MuSiQue | InfoDeepSeek |
|---|---|---|---|
| 权威性（模型卡必报） | **✅ 最高** | 学术床 | 小组论文 |
| 簇键 | ✅ 亲验 10 类 | hop 数 3 类 | 6 布尔属性 |
| **本模型落点** | **✅ 已知 53.5@high** | ❌ 文献空白 | 家族数 10–34% |
| n | **1,266** | 2,417 | 245 |
| 防污染 | ✅ XOR + canary | 2019 快照漂移 | — |
| license | MIT（代码） | CC-BY-4.0 | ⚠️ NC/ND 冲突 |
| 答案形态 | 中位 **2 词 / 14 字符**，82% ≤3 词 | 短串 | 短串 |
| 对 browsing harness 敏感度 | **✅ 最高** | 中 | 高 |

**外部尺子（BrowseComp 独有）**：DeepSeek 官方公布 Flash 73.2 / Pro 83.4，**差 10.2pp = 一整个模型档位**。若变体池把 Flash 推过 10pp，即可论证「harness 结构改动的收益大于换一档模型」。

**空地**：📄 未找到任何「同一底模 × ≥3 个具名 harness × BrowseComp」的 apples-to-apples 对照表 → **本实验无直接先例**（利于新意，但也无现成分差量级可引；最近锚 = Deep Research 单次 51.5% vs best-of-64 ≈75%，+15–25pp，L2）。

### 2.5.3 判分：解析 `Exact Answer:` 行 + 逐簇校准

- 官方 grader = **LLM judge，`gpt-4.1-2025-04-14`**（`simple-evals` 仓库现状；发布时为 GPT-4o），模板借自 HLE。📄
- 答案极短 → EM 友好：**76.8% 为干净短 alnum**（≤4 词、无风险标点）。📄
- 官方 `QUERY_TEMPLATE` 本就要求模型输出 `Exact Answer: {…}` 行 → **解析该行再做 normalized-EM**，可消掉大部分抽取失败型误杀。
- 📄 未找到「用 EM 替官方 judge 跑 BrowseComp」的公开先例。

### 2.5.4 🔴 三个必须处理的风险

**风险 1 —— 逐簇 EM 洁净度不均 × 按簇路由 = 隐性混淆（本床最尖锐的问题）**

| 簇 | EM 风险 |
|---|---|
| Sports | 多段答案率 **20.3%**（比分/对阵） |
| Politics | 干净率最低 **67.8%** |
| Music / TV | 干净率 **85%** |

变体池**按簇路由** → 被分到 Sports 的变体会因**判分器 artifact** 系统性多扣分，而非能力差。**这不是普通判分噪声，是与路由机制交互的偏差。**
**缓解**：在随机 ~100 题子集上跑一次 LLM judge，测 EM–judge 差距**并验证其不与臂交互**。一致低估无害，交互性低估致命。

**风险 2 —— effort 升档在本床是 19.7pp**

V4-Flash 官方：High **53.5** vs Max **73.2**。而 `MODEL-SELECTION-FLASH-VS-PRO.md` 记载服务端对「复杂 agent 请求」自动升 effort（标为不可控项）。
**BrowseComp 正是最易触发该判定的床。** 若结构更复杂的变体更易触发升档，19.7pp 会被算成 harness 的功劳。
→ **必须逐次调用记录实际 effort 档位**，不能只「如实记录」。此项在 BrowseComp 上比在 GAIA 上严重得多。

**风险 3 —— 约 40% 题可闭卷答出**

LiveBrowseComp（`2605.28721`，⚠️ 未审计）报 ~40% 存在 Intrinsic Knowledge Dependence（MiniMax-M2.5 44.5% / DeepSeek-V4-Pro 22.5% 无工具）。这些题上 harness 不起作用，稀释信号。
→ **加一条 no-tools 控制臂**测出 V4-Flash 的参数地板；各臂相对该地板的增益才是真信号。（V4-Flash SimpleQA-Verified 仅 34.1，参数记忆弱，实际闭卷率大概率低于 40%。）

### 2.5.5 🔴 预算：真正的绑定约束

每题约 **15–25 次搜索**（📄 BrowseComp-Plus 实测：GPT-5/o3 >20、gpt-4.1 8.67–10.03；effort low→high 使搜索量 2→24）。与 GAIA（实测约 20 次/题）**同量级，不便宜**。

剩余 Serper 配额约 **40,000**：

| 设计 | 查询数 | 判定 |
|---|---|---|
| 跑满 1,266 题 × 1 臂 × 1 次 | 25,000–32,000 | 一遍去掉大半 |
| **GAIA 式演化跑（16 轮）× 270 题** | **≈86,000** | **超预算 2 倍** |
| 抽 270 题（30×9 簇）可负担臂-运行数 | ≈7（=3 臂×2 重复） | 勉强 |
| 抽 450 题（50×9 簇） | ≈4 | 更紧 |

- **best-of-N 是预算杀手**（线性放大）：best-of-8 臂在 270 题 × 20 次 = 43,200，一条臂就爆预算。
- 对照：GAIA 完整跑（103 题 × 16 轮）本身已烧约 30,000。

> **结论：BrowseComp 做演化实验需加购 Serper 配额。这是可购买的约束，不是设计死结** —— 相对论文价值成本很低。**建议先确认预算能否追加，再定题量。**
> 注：仅 `web_search` 消耗 Serper，`web_fetch` / `browser` / `read` 不消耗。

### 2.5.6 饱和与污染

- 2026 单智能体 SOTA ~**83%**（DeepSeek-V4-Pro 83.4 / Kimi-K2.6 83.2）、并行 64 searchers ~86.2%（⚠️ 未审计，我方知识截止后）。**接近饱和但无 >90%；对 V4-Flash（53.5/73.2）这一档 headroom 充足**，非个人饱和。
- 防污染：`problem`/`answer` XOR 加密 + canary GUID，真实 Q/A 从不明文上网 🔬。无泄漏实锤报告，关切是记忆化而非泄漏。
- license：`openai/simple-evals` = **MIT**（代码）；数据 CSV 托管于 blob，**无单独数据 license 文件**。

### 2.5.7 对照：HLE text-only（判为次床）

`cais/hle`，MIT，test=2500，text-only ≈90%（≈2250 题）。字段含 **`category`（8 高层类）+ `raw_subject`（细）+ `answer_type`** → 簇键比 BrowseComp 更丰富（两级）。官方 judge = `o3-mini-2025-01-31`（仓库默认）。V4-Flash w/tools = 45.1，也在甜区。📄

- **优**：几乎不吃 Serper（closed-book，可跑满 2250 题）；n 大；两级簇键。
- **劣（对本实验是硬伤）**：**closed-book 设计 → harness/scaffold 撬不动分**，正是本实验需要的东西它给不了；数据 **gated**（需 HF token）；`exactMatch` 含数学/符号表达式，自写 EM 比 BrowseComp 的短事实答案更易误杀；需过滤多模态。
- **判定**：**就「区分 browsing harness 变体」这一目标，BrowseComp > HLE。** HLE 保留为对照/次床，或实验转向「推理 scaffold」时才升为主床。

---

### ❌ 排除

| 床 | 排除理由 | 证据 |
|---|---|---|
| **FRAMES** | **太简单**（见 §3） | 🔬 |
| GAIA 剩余 62 题 | 全为带附件/多模态题，本 harness 不可用 → GAIA 内部无 held-out | 🔬 `FINDINGS-AUG01.md:239-243` |
| xbench-DeepSearch | schema 无任何分类字段 → 池塌缩成 1；且仅 100 题 | 📄 |
| BrowseComp-Plus | 离线语料优势需接其 BM25 索引（我们无检索器基建）；不接则退化为普通 BrowseComp；簇键仅 `gold_docs` 数值分箱 | 📄 |
| AssistantBench | 181 题 test 答案不公开且 `difficulty` 全 NULL；本地仅 33 题可判分 | 📄 |
| HotpotQA / 2Wiki / Bamboogle | 落点 65–85%，太高；HotpotQA 闭卷 0.40 且公认污染；Bamboogle 仅 125 题 | 📄 |
| SealQA | 落点 20–40% 但 Seal-0 上 o3=17.1 / o4-mini=6.3，地板风险高，会压平变体差异 | 📄 |
| ToolHop / ToolQA / BFCL | 每题自带工具集或需接 8 套领域数据库 → 要重建工具层，非换 JSON | 📄 |
| HLE / GPQA / SimpleQA | 闭卷考试，无检索可分解，我们的分解变体没有作用点 | 📄 |
| DeepResearch Bench / Mind2Web 2 / ResearchQA / FanoutQA(严格) | 长答案 / 需 rubric-judge，判分器不兼容 | 📄 |
| AgentBoard | 数据 GPL-2 传染 | 📄 |
| WebArena / OSWorld / GAIA2-ARE | 动作型交互环境，非短答案 QA | 📄 |

### 🔶 并列候选：InfoDeepSeek（Aug-03 新增，建议与 MuSiQue 一同 pilot）

| | |
|---|---|
| 规模 | **245 题**（偏小；2·SD ≈ 5.9pp，仍优于现状 9.1pp） |
| 设置 | **原生 live-web agentic 检索** — 无 MuSiQue 的 2019/2026 时间错配 |
| **已实测前沿落点**（judge，live-web）📄 | Gemini-2.5-Flash+Google **34.29%** / Gemini-2.5-Pro 22.5 / GPT-4o 10.2 / DS-R1 15.1 —— **全在带内** |
| 簇字段 | **最富**：布尔 `multi_hop` / `long_tail` / `time_sensitive` / `false_premise` / `distracting_info` / `freshness` + `domain` / `difficulty` |
| license | ⚠️ 论文 CC-BY-**NC** vs HF 卡 CC-BY-**ND**，两处冲突，锁定前须核 |
| 出处 | `arXiv 2505.15872` · github.com/YunjiaXi/InfoDeepSeek |

**相对 MuSiQue 的优势**：① 有**同设置下的前沿实测落点**（MuSiQue 此格为文献空白）；② 六个布尔属性极适合变体归因 —— 可直接论证「分解在 `multi_hop` 上有效、分工在 `distracting_info` 上有效」，比单一 hop 数丰富得多。

**警告**：N=245 功效弱于 MuSiQue；**Flash 34.29 > Pro 22.5 的反序可疑**，可能是其评测工具链假象，锁定前须查清。

### 🔶 备选（若前两者均出界）

- **FanoutQA**（1,034 题，落点 ~45–65%）— fan-out 问题**本身就是并行子问题分解**，与「分工」变体概念最贴合；有闭卷/开卷/给证据三设置可切。代价：答案是列表，Loose F1 判分较噪，且无前沿实测数。📄
- **WebDetective**（`2510.05137`）落点堪称教科书级（o3-Pro 56 / GPT-5 50.5 / Claude-Opus-4.1 44.5 / Gemini-2.5-Pro 28.5 / DS-R1 20），有原生跳数+题型字段 —— **但需自建「维基实体遮蔽沙盒」，不能跑通用 live web，且无公开 release/license** → 按「不重建环境」红线判死。📄

---

## 3. FRAMES 排除的证据链（本次结论反转，记录始末）

**初判（错误）**：推 FRAMES 为首选 —— 依据是「闭卷 0.41 / 单步 0.47 / 多步 0.66 / oracle 0.73」，其中 +25pp 的单步→多步阶梯正对应我们的分解变体。

**反转依据**（🔬 三条锚点均由主循环亲查原表）：

| 锚点 | 数字 | 设置 | 出处 |
|---|---|---|---|
| **ODS-v2 + DeepSeek-R1** | **FRAMES 75.3%** | 真实搜索，平均 **3.39 次查询/题**，824 题全量 | `arXiv 2503.20201` Table 1 |
| 同表基线 | GPT-4o 闭卷 50.5 / GPT-4o Search Preview 65.6 / Perplexity Sonar Reasoning Pro 44.4 | — | 同上 |
| **Tongyi DeepResearch** | **GAIA 70.9 / FRAMES 90.6** | 同一 Table 1 两格 | `arXiv 2510.24701` Table 1 |

**推理**：DeepSeek-R1（2025）配一个只搜 3.4 次的简单 agentic 环即达 75.3。我方底模更新（V4）、harness 每题约 20 次查询（GAIA 实测 ~2,100 次/轮 ÷ 103 题）→ **落点 82–90%，顶天花板**。

**原判为何错**：0.41 / 0.66 是 **Gemini-1.5-Pro（2024）** 的数，已被两年模型进步碾过。绝对值封顶后，那条 +25pp 阶梯还剩多少无人测过。

**同时作废的两条中间说法**（记入勘误）：

1. 「FRAMES 已被刷到 0.87 ⇒ 饱和」— 该数为 oracle/长上下文（gold 维基页灌进 prompt），不是 agentic 检索。**作废，但结论方向恰好正确**（另有更硬的证据支持排除）。
2. 「FRAMES 需自建维基语料」— **作废**。`Prompt` 字段自足，`wikipedia_link_*` 仅为 gold-evidence 元数据，不参与提问 📄。FRAMES 本可零改造 drop-in —— 只是没必要了。

**Tongyi 锚点的未坐实处**（🔬 查过两遍原文，确认论文未写）：

- 未写明 GAIA 用哪个子集，仅称「following the evaluation protocol of Li et al. (2025d)」+ Qwen2.5-72B 判官 —— 该协议指向 WebThinker 的 text-103，但**是推断非明述**。
- 未写明 FRAMES 的评测设置，由「系统全程使用真实 web 工具」推断为 agentic。
- 其判分为 **LLM judge**，我方为 EM，两者不严格可比。

> ⚠️ **承重的是 ODS 那条 DeepSeek 系锚点**（不依赖任何 GAIA 映射、不依赖子集推断）。Tongyi 条为佐证，不可单独承重。

---

## 4. MuSiQue 接入方案

### 4.1 复用成本 ≈ 零改代码

新数据集只需转成五字段 JSON（`load_gaia_tasks_from_json` 直接吃）：

```
{"task_id", "Question", "answer", "Level", "Annotator_Metadata"}
```

再用 `--data-path` 指过去。scorer / harness / 工具 / processor **全部不动**，约 30–50 行转换脚本 📄。

- `Level` 槽位放 **hop 数（2/3/4）** → 天然三簇，与 GAIA 的 L1/L2/L3 结构同构，路由逻辑零改。
- MuSiQue 是维基多跳题，**可直接走现有实时搜索**，无需本地检索器。代价：偏离官方 protocol（官方在给定段落池内检索），故**我方数字不与文献榜对齐** —— 但实验要的是变体间的差，不是对榜。

### 4.2 ⚠️ 待裁决：判分口径

文献数用 **F1 / LLM judge**；我方 scorer 是**二值 normalized-EM**。二值 EM 低于 F1 → 落点会落在 **32–45%**，甜区下沿。

| 方案 | 说明 | 评价 |
|---|---|---|
| **A. 保持二值 EM** | 零改动；落点靠下沿；只能自比 | **倾向此项** — 保住零 judge 噪声，这在 SD 已 4.6pp 时很值钱 |
| B. F1 灌进 `eval_score`，门仍用二值 | 两套口径并存，可对齐文献 | 次选 |
| C. 上 LLM judge | 判分噪声 + SD 上升 | **不推荐** — 会毁掉本床唯一的干净属性 |

> 变体池的门按通过率走，需要二值信号，F1 塞不进门；但 `eval_score` 连续字段是存在的。

### 4.3 ⚠️ 时间漂移：MuSiQue 走 live-web 的固有威胁（Aug-03 新识别）

**「前沿模型 × live-web × MuSiQue」的公开实测数 = 0 篇，且是结构性不存在**：MuSiQue 金标答案锁定 **2019 维基快照**，跑实时网会答案漂移，故文献一律用冻结语料评它；跑 live-web 的全是 7B–32B 的 RL-search 训练论文 📄。

**对本实验的实际危害小于表面**（主循环分析）：

- 配对设计下，同一道题两臂都跑，标答过期则**两臂同错** → 恒定偏移，McNemar 只看不一致对，**组间比较基本不受影响**。
- 真实损失是**有效 n 缩水**：过期题成为「死票」，不参与区分。若 15% 过期，功效按 15% 打折。
- 附带利好：`disconnected-reasoning` 走捷径地板（given-context 下约 37.8 F1）**在 live-web 下基本失效**（找不到段落就无法作弊）📄。

**三档应对（成本递增）**：

| # | 方案 | 成本 | 代价 |
|---|---|---|---|
| **1** | **时间敏感性筛选** — 剔除「现任/当前/最新」类题；MuSiQue 大量题为历史事实（出生年、导演、发行日），本不漂移 | ≈0 | 略减 n |
| 2 | 将每题自带的 20 段落做成本地检索工具 | ~100 行 | 检索变易、分数升高；类比变窄 |
| 3 | 建全维基冻结索引 | 高 | MSc 时限内不划算 |

**建议方案 1**，并在 pilot 中实测漂移率。

### 4.4 Pilot（动手前必做）—— BrowseComp 优先

> Aug-03 二次修订：主床改为 BrowseComp（§2.5），本节相应改写。MuSiQue pilot 降为备份路径。

**BrowseComp 90 题（10×9 簇均衡）+ no-tools 控制臂同题 90 题，K=1 baseline，现有 harness，零改代码。**

确认五件事：

1. **落点**是否接近官方 53.5@high（现有 harness 的实际水位）
2. **no-tools 控制臂落点** —— 扣除闭卷记忆地板（文献称约 40%，未审计；V4-Flash 参数记忆弱，预期更低）
3. **每题实际 Serper 查询数** —— 直接决定正式实验的题量上限（规划取 S≈20，最坏 30–40）
4. **逐簇 EM–judge 差距** —— 在同一 90 题上跑一次 LLM judge，验证偏差**不与簇/臂交互**（§2.5.4 风险 1）
5. **逐次调用的实际 effort 档位** —— 检查服务端是否自动升 max（§2.5.4 风险 2，本床 19.7pp）

**成本**：模型在实验室端点近乎免费；Serper 约 90×20×2 ≈ **3,600 次**，另加 ~90 次 judge 调用（几美元）。数小时出结果。

**决策规则**：**用实测选床与定题量，不用外推。** 若落点 >75% 或 no-tools 地板 >50% → 回退 MuSiQue（§2 的备份分析仍有效）。

**备份路径（仅在 BrowseComp 因预算被否时启用）**：MuSiQue 50 题 + InfoDeepSeek 50 题单臂，观测落点 / 变体分离 / MuSiQue 漂移率 / 2-3-4 跳梯度。

### 4.5 正式实验设计约束

- **采样 ≥512 题**，按 hop 分层（512 是 DeepResearcher 的通行做法，也正好压到 2·SD ≈ 4.1pp）🔬
- **同题配对跑**所有变体臂 + McNemar / 混合效应逻辑回归（带 item 随机截距）
- **预注册**：效应集中在高 hop 层（兑现 E-7），全床平均与分层结果并报
- **禁用 given-context**（灌 gold 段落会把落点推到 65–80%）📄

---

## 5. 仓库内既有资产核查（结论：不可当捷径）

曾假设「最便宜的第二床 = 复用已有适配器」。核完后**否决**：

| | 代码量 | RUN-LOG 实跑记录 |
|---|---|---|
| `benchmarks/tau2/` | 2,215 行（含 test） | **零** |
| `benchmarks/swebench/` | 1,914 行 | **零** |
| `benchmarks/terminal_bench_2/` | 2,422 行 | **零** |
| `benchmarks/locomo/` | 1,557 行 | **零** |
| `recipe/tau2_evolver/` | 1,661 行 | **零** |
| `recipe/tb2_evolver/` | 1,324 行（`ARCHITECTURE.md:233`：有演化**无门**） | **零** |
| `recipe/gaia_evolver/` | 61,790 行 | 全部实验 |

🔬 亲验：行数由目录统计得出；`RUN-LOG.md` 全文检索 tau2 / tb2 / swebench / terminal-bench **零命中**。`ARCHITECTURE.md:14,233` 自称「带漂移的同构副本」。

**结论**：是未验证的半成品骨架，省下的不是调通的时间。

**另**：`τ²-bench` 不宜作第二床（genre 偏移=客服对话、user-simulator 注入随机性抬高噪声底、telecom 仅 114 题不如现状、且 tau2 自身拥有仿真循环与变体池驱动结构不合）。**但必须在 related work 处理** —— 其 Figure 4 ablation（Oracle-Plan = 注入分解，No-User/dual-control = 分工，同底模摆 +39–54pp）**就是本文的自变量**📄。

---

## 6. 连带产出的两项待办

### 6.1 SD 口径必须先裁（阻塞论文写作）

- `FINDINGS-AUG01.md` E-5 待办：**5.59 vs B-FREEZE 的 4.57 未统一**
- 4.57pp 的算法是：**同一次 `s1k8b103` run 内 8 个「零发船」轮次的跨轮漂移**，**非独立重跑、非 seed 重复**（`RUN-LOG.md:991-993`）📄
- 对外引用时必须讲清算法，否则是被抓的点
- **好消息**：该量级在文献里正常偏低（小床 seed SD 普遍 5–15pp；AI Agents That Matter 五次重跑 LATS-HumanEval 极差 9.2pp）📄

### 6.2 口径辩护：GAIA-Text-103 有公认先例

GAIA-Text-103（39/52/12）是 **WebThinker（2504.21776）/ WebDancer** 立的协议，Search-o1、Youtu-Agent、MiroThinker 等在用 📄。**非自创筛法**，「近迁移 / 子集挑选」的质疑有现成先例可引。

---

## 7. 必须处理的外部威胁

**`arXiv 2606.08529`《Scaffold Effects on GAIA: A Controlled Comparison》** 📄

- 已有人在 GAIA 上做了受控 scaffold 对比：139 题、5 模型、官方 EM、混合效应逻辑回归带 `sample_id` 随机截距；三个 scaffold 为 ReAct / Planner-Actor-Rater / Planner-then-Executor
- 同底模最大分差 **28pp**（Claude Opus, L2 层）
- ⚠️ **但底模越强分差越小** —— GPT-5 上仅剩 3.8–5.8pp 且 S2−S1 置信区间跨零

**含义**：比「绝对分被刷高」更危险的失败模式是**分差被压平**。我方已有反证（DS V4 在 GAIA 上演化确实推动了分数，不在「scaffold 免疫」区间），但 pilot 除看绝对分外，**还须看变体间有无差**。

---

## 8. 已知不确定性（勿当已证事实传播）

### 8.1 底模校准（Aug-03 补，回应「我们是 DS V4」）

worker = **V4-Flash**（284B 总 / **13B 激活**），非 Pro；裁决见 `MODEL-SELECTION-FLASH-VS-PRO.md` + SPEC §7.13。官方跑分（Max 档）🔬 亲读该文档：

| 基准 | Flash | Pro |
|---|---|---|
| BrowseComp（最贴 GAIA） | **73.2**（High 档 **53.5**） | 83.4 |
| SimpleQA-Verified（事实回忆） | **34.1** | 57.9 |
| HLE w/ tools | 45.1 | 48.2 |
| GPQA-D | 88.1 | 90.1 |

**三条推论**：

1. **本文的床判据与既有方法学一致**。该文档裁决理由 (a) 逐字：「Pro 在 BrowseComp 已近开源天花板(80-83)，会压平 harness 演化的可测效应窗；Flash 落在曲线中段」。**选模型时已用过同一条"留出可测效应窗"原则** → FRAMES 因太高而排除，是既有标准的延用，非新增。
2. **本地先验带 = 校准床 33–67% pass@2**（同文档）。比任何外推都硬的落点参照。
3. **Flash 参数化事实回忆偏弱**（SimpleQA-Verified 34.1，比 Pro 低 23.8）。MuSiQue 是实体密集多跳事实题 → **不易被靠记忆做掉**，检索与分解的杠杆得以保留。

**据此下修落点估计**：MuSiQue **F1 40–55 / 二值 EM 约 35–50**（原估 50–60 F1）。仍在甜区，且现有本地先验背书。
FRAMES 结论不受影响：R1 配 3.4 次搜索已 75.3，Flash 更强且每题约 20 次搜索。

> ⚠️ **新识别的混淆风险**：该文档记载服务端对「复杂 agent 请求」会自动把 effort 升至 max，标为不可控项。BrowseComp 上 high→max 是 **53.5→73.2（20pp）**。若结构更复杂的变体更易触发升档，即构成 **harness 结构 × 有效模型能力的混淆**，比 token 预算混淆更隐蔽。分解×分工 2×2 设计中建议主动控制（记录每次调用的实际 effort 档位），而非仅「如实记录」。

### 8.2 不确定性清单

| 项 | 状态 |
|---|---|
| DS V4-Flash 在 MuSiQue 的落点 35–55% | **外推，非实测**。基于 7B agent 的 27 分 + 规模斜率 + §8.1 本地先验。**Pilot 未跑前不可写进任何结论** |
| 前沿/大模型在 MuSiQue **live-web** 的公开实测数 | **已查证：0 篇，结构性不存在**（金标锁 2019 快照 → 文献一律用冻结语料评）。最近锚点 = DeepSeek-Chat(V3 级) 冻结语料 EM 31.9 / F1 42.5（📄 单次抽取，锁定前建议亲核 PRISM `2510.14278` Table 4） |
| InfoDeepSeek 的 license | 论文 CC-BY-**NC** vs HF 卡 CC-BY-**ND** 冲突，未裁 |
| InfoDeepSeek 的 Flash 34.29 > Pro 22.5 反序 | 可疑，疑为其评测工具链假象，未查清 |

### 8.3 勘误（本次调查中作废的数据点）

| 作废项 | 更正 |
|---|---|
| 「Qwen-72B + FSM prompting MuSiQue F1 41.2 = 真检索」 | **实为 closed-book**，不可作 open-retrieval 锚点 |
| 「DeepSeek-R1 MuSiQue EM 17」 | 设置标注正确（closed-book），补 F1 = 27.5 |
| 主循环据上述两点推出的「7B→27、72B→41 规模斜率」 | **推理基础错误，作废**。真实斜率见 §2 —— 裸规模非单调（32B vanilla 18.9 < 7B-RL 27.1），harness 才是主效应 |
| 「FRAMES 已被刷到 0.87 ⇒ 饱和」 | 该数为 oracle/长上下文，非 agentic 检索。作废（结论方向另有硬证据支持，见 §3） |
| 「FRAMES 需自建维基语料」 | 作废。`Prompt` 字段自足，`wikipedia_link_*` 仅为 gold-evidence 元数据 |
| **「BrowseComp 无原生簇字段」** | **作废（🔬 数据文件亲验）**。`problem_topic` 是明文列，10 类 0 空值。误判来源 = 官方 `browsecomp_eval.py` 不读该列，故二手资料普遍称其无标签。**教训：数据集有无某字段，须查发布文件本身，不可据官方 eval 脚本读了什么来推断。** |
| 「BrowseComp 太难，对弱 harness 不友好」（本次调查早期主循环判词） | **作废**。当时不知底模落点。V4-Flash@high=53.5 远在 0 分地板之上，正中甜区 |
| Tongyi 的 GAIA 子集 = text-103 | **推断**（据「Li et al. 2025d」协议 + Qwen 判官），论文未明述 |
| Tongyi 的 FRAMES 评测设置 = agentic | **推断**，论文未明述 |
| SD 向 n>103 的 √n 外推 | **投影**；实测仅验证过缩小方向（n=50） |
| MuSiQue 闭卷 EM 0.18 / HotpotQA 0.40 | 📄 子代理报告，主循环未复核原表 |
| 各床簇键字段分布 | 📄 子代理经 HF datasets-server 查得，主循环未复核 |
| §1 三条硬门的 file:line | 📄 子代理报告，主循环未逐行复核（`FINDINGS-AUG01.md` 相关行已亲验） |

**方法学限制（须写入 threats to validity）**：MuSiQue 是纯 QA，工具面仅用得上 search + fetch，比 GAIA（另有 bash、browser）窄。分解在多跳检索规划上确有杠杆，但这是**更窄的类比**。

---

## 9. 环境备注

`graphify` CLI 未安装在当前机器（`graphify-out/` 目录存在但命令不可用）。项目 CLAUDE.md 中「codebase 问题先跑 `graphify query`」的规则目前**不可执行**，本次调查改用 Grep/Read 完成。
