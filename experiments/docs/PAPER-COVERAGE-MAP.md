# 我们相对原论文覆盖了什么

**对象**:HarnessX / AEGIS,arXiv 2606.14249,43 页。
**核验方式**:主循环直接从本地 PDF(`D:\PycharmProj\MAS_Directions\harnessx_2606.14249.pdf`)
提取全文逐字读取,2026-08-03。本文所有引文为原文逐字,页码为 PDF 页码。
**用途**:定位陈述的唯一权威来源。汇报、related work、答辩口径都以此为准。

---

## 0. 首要事实:这篇论文没有 Future Work 章节

实际章节结构(§7–§8,p.21–23):

```
7.1 Why Compositional Structure Matters for Evolution
7.2 The Role of Trace Richness
7.3 Scope and Limits of the Operational Mirror
7.4 Generalization Across Model Families
7.5 Cost-Performance Tradeoffs
7.6 Ethical Considerations
7.7 Limitations                      ← 五条
8   Conclusion
```

**全文无 future work / 后续方向段落。**

⛔ **因此下列说法一律不得使用**:

- 「我们在做 HarnessX 的 future work」
- 「HarnessX 的 future work 点名邀请了任务分解」
- 任何形如「§7.7 分解工单」的引用 —— **该引文经 PDF 亲验为 confabulation,已作废**

正确定位见 §4。

---

## 1. §4.2 三种「演化器够不着的结构改动」

### 原文(p.8–9,§4.2 Pathologies in Symbolic Space,Under-exploration 段)

> "Under-exploration [16] manifests as a bias toward low-risk local edits: prompt
> rephrasing, tool-description tuning, or minor control-flow tweaks. These edits are
> cheap to generate and frequently pass gating without regressing solved tasks,
> biasing subsequent Planner hypotheses toward the same edit neighborhood.
> **Structural changes (decomposing one agent into several, replacing the control
> strategy, or adopting a new memory architecture) require deliberate hypothesis
> formation and rarely emerge from trace-conditional local repair.** Without a
> mechanism to propose edits beyond the immediate failure neighborhood, the system
> plateaus once local edits are exhausted."

这是他们**自认的病理**,不是 future work。而「把一个 agent 分解成多个」是他们列的**头号例子**。

### 覆盖:1.5 / 3

| # | 原文项 | 我们 | 落在哪 |
|---|---|---|---|
| 1 | decomposing one agent into several | ✅ **实现** | CH4:任务分解 + 子任务派给不同变体 |
| 2 | replacing the control strategy | 🟡 **副产品** | 同一机制把扁平循环换成 plan-and-execute |
| 3 | adopting a new memory architecture | ❌ 未做 | 信用账本是路由器状态,非 agent 记忆架构 |

⚠️ **第 2 项措辞纪律**:它**不是独立实现的第二个杠杆**,是第 1 项的必然后果。
写成「我们实现了两项」会被抓。正确写法:「实现第 1 项,其带来的控制策略变更是副产品」。

### 实现状态

代码全部在位(`recipe/gaia_evolver/run_variant_pool.py` 已核):

```
--decomp-source        --decomp-routing     --decomp-credit
--decomp-budget        --decomp-synth-guard --decomp-concurrency
```

四条臂 A1 / B0 / B1 / B2 冻结包见 `FREEZE-CH4-ARMS.md`,两份冻结资产已产出并核验。
**一条都还没跑。**

---

## 2. §4.5 三条可证伪预言(CH3 的靶)

### 原文(p.11,§4.5 Variant Isolation via Ensemble Routing)

> "Variant isolation lifts this limitation by maintaining up to K harness variants
> {H(1)t , . . . ,H(Vt)t } (Vt ≤ K) and **routing each task to the variant with the
> highest estimated success rate on that task's cluster** across prior rounds. We
> term this mechanism Ensemble routing."

> "This design **predicts three properties** validated in Section 6.3: (1) non-degrading
> aggregate trajectory (peak = final), (2) sustained exploration across more rounds,
> and (3) lower total token consumption."

### 覆盖:1 / 3

| # | 预言 | 状态 |
|---|---|---|
| 1 | non-degrading aggregate trajectory(peak = final) | ✅ 已测 |
| 2 | sustained exploration across **more rounds** | ⏳ **无对照,测不了** |
| 3 | **lower** total token consumption | ⏳ **无对照,测不了** |

**预言 2、3 都是比较级** —— 「更多轮」「更少 token」,比较对象是**单 harness**。
我们手上只有变体池那一侧。

> 🔴 **`ARM-LADDER-DECISIONS.md` 中的 K=1 对照臂不是可选项,是唯一能测预言 2、3 的臂。**
> 缺它,CH3 只能回答三分之一。此前把它列为「可选」是低估。

### 预言 1 上的发现

peak 是 **max-of-N** 统计量。Blom 近似给出 n=16 时期望上偏 ≈ **1.77 SD ≈ 8.1pp**。
⇒ **「peak = final」这条预言在结构上被构造偏置支持**,不是纯经验结果。

### 有效池上界:他们自己机制的直接推论

§4.5 原文写明路由是 **"the variant with the highest estimated success rate on that
task's cluster"** —— 即**每簇 argmax**。

```
每簇 argmax  ⇒  每簇至多一个变体能持有任务
             ⇒  能装任务的变体数 ≤ 簇数
             ⇒  有效池 = min(K, 簇数)
```

他们设 K=8,**全文不报簇数**。我方实测 Gini 0.22→0.78,终态 `[52,39,12,0,0,0,0,0]`;
16 轮中 9 个非 fork 轮**每簇恰好一个变体**,相关性 100%。

**这不是外部发现,是把他们没算完的算完了。**

---

## 3. §7.7 五条 Limitations

### 原文(p.23)

> "Beyond the limitations noted above, five additional constraints bound the
> generality of our results:
> • **No held-out evaluation.** All reported gains are measured on the same task set
>   used for evolution. Since we report peak accuracy and evaluate on the adaptation
>   set itself, the numbers carry both selection bias and potential overfitting.
>   Generalization to unseen tasks within the same distribution is plausible but untested.
> • **Discrete action spaces only.** All experiments use agents with discrete,
>   text-based action spaces. We have not tested whether the framework extends to
>   continuous action spaces (e.g., robotic control).
> • **Closed-source meta-agent.** AEGIS requires a meta-agent capable of multi-file
>   code generation, structured trace analysis, and multi-step planning. Open-weight
>   models approaching this capability level (e.g., Qwen3.5-72B, Llama-4-Maverick)
>   remain untested as meta-agents.
> • **Joint control assumption.** Co-evolution requires joint control over both harness
>   evolution and model training. In practice, these concerns are often separated across
>   teams or organizations, making a shared replay buffer (Section 5.1) impractical
>   without cross-team coordination.
> • **Benchmark coverage.** All **SWE-bench Verified** runs use a 55-task subsample, and
>   τ3-Bench evaluates only three domains (Retail, Airline, Telecom). Conclusions,
>   particularly the inverse-scaling effect, may not generalize to domains with different
>   task heterogeneity or to larger evaluation sets."

### 覆盖:1 / 5

| # | 限制 | 我们 |
|---|---|---|
| 1 | No held-out | 🟡 记为偏差 M-09 + 纪律,**未真做 split** |
| 2 | 仅离散动作空间 | ❌ |
| 3 | **Closed-source meta-agent** | ✅ **全程用 DeepSeek V4 当元 agent** |
| 4 | 需联合控制(co-evolution) | ❌ 不做共演化 |
| 5 | 基准覆盖 | ❌ 只跑 GAIA |

### ⛔ 第 5 条的常见误用

原文的 **55-task subsample 是 SWE-bench Verified 的**,不是 GAIA。
我们跑 GAIA n=103 —— **那是他们 GAIA 的全量**。

**「55 题评估洞」这个钩子对本工作不适用,不得使用。**

### 第 3 条 —— 唯一实打实碰到的一格,且零额外成本

他们全程用 Opus 4.6 当元 agent,并**明写开源权重模型未测**。
我们从头到尾用 DeepSeek V4 当元 agent,跑通完整演化闭环:16 轮、有 ship、有 fork、有 Critic 门。

> ⚠️ **写入论文前必须核实**:DeepSeek V4 的权重是否公开发布。
> 实验室本地部署(`litellm.yangtzeailab.com`)强烈暗示是开源权重,**但尚未验证**。
> 这是 load-bearing 事实,未核实前不得作为贡献点陈述。

---

## 4. 🔴 CH4 必须正面处理的负面先例

他们**已经测过分解**,而且是负面结果。

### 原文(p.18,§6.4 Meta-Agent Effectiveness)

> "Accuracy is comparable. The 1.0% accuracy gap falls within one standard error
> (∼3.3% at n=103), indicating that **the four-stage decomposition does not improve
> final accuracy at this meta-agent capability level.** However, the single-agent
> variant consumes ∼14% more tokens (123.1M vs. 107.8M)."

> "The four-stage decomposition contributes **efficiency (∼12% fewer tokens) and
> interpretability (auditable intermediate artifacts) but not measurable accuracy**
> at this scale."

### 原文(p.22,§7.4)

> "...at this meta-agent capability level, the four-stage decomposition primarily
> provides efficiency gains (∼12% fewer tokens) and auditability rather than
> measurable accuracy improvement."

### 原文(p.22,§7.5)

> "On GAIA, per-task token consumption drops by ∼25% (targeted tool selection shortens
> trajectories); on ALFWorld, it rises by ∼60% (**task-decomposition prompts lengthen
> execution**)."

### 轴的区别与应对

|  | 谁被分解 | 结果 |
|---|---|---|
| 他们 §6.4 | **元 agent**(Digester/Planner/Editor/Critic 四阶段) | 准确率无增益,省 ~12% token |
| 我们 CH4 | **被评估任务** | 待测 |

轴不同,但**导师必问这个区别**。而且他们 §7.5 已观察到任务分解会**涨** token(ALFWorld +60%),
与我方实测分解成本 **2.2×** 预算同向。

> **应对:主动写进 CH4 related work,不要等被问。**
> 而且这条其实帮我们 —— 分解在这个体系里**已被证明是效率杠杆而非准确率杠杆**,
> 所以 CH4 的假设本来就该是「**在什么条件下**分解能换来准确率」,
> 而非「分解能提准确率」。这让 CH4 的问题形状从"再试一次"变成"划边界"。

---

## 5. 一句话定位(可直接用于汇报 / 答辩)

> 不是做它的 future work —— 它没有 future work 章节。
> **CH3** 是对 §4.5 三条可证伪预言的独立复现,已发现其中「peak = final」受构造偏置支持,
> 且有效池受 min(K, 簇数) 约束 —— 后者是其自身路由规则的直接推论而原文未报簇数。
> **CH4** 是把 §4.2 中它自认演化器够不着的头号结构改动(把一个 agent 分解成多个)
> 外生实现,测其代价与收益边界。

---

## 6. 总账与未决

```
实现了     1.5 / 3   §4.2 结构改动     ← CH4 代码就位,零臂已跑
测出来了     1 / 3   §4.5 预言         ← 缺 K=1 对照
覆盖了       1 / 5   §7.7 限制         ← 第 3 条,待核 V4 权重公开性
```

**未决事项**

| | 事项 | 性质 |
|---|---|---|
| 1 | 核实 DeepSeek V4 权重是否公开发布 | 决定 §7.7-3 能否作为贡献点 |
| 2 | K=1 对照臂从「可选」升为必需,重排 `ARM-LADDER-DECISIONS.md` | 只改文档,不动跑 |
| 3 | §6.4 负面先例写入 CH4 related work | 只改文档 |
| 4 | 「55 题钩子」「FW 邀请分解」两条作废说法记入 `03-ERRATA` | 只改文档 |

**已核**:六份论文草稿中**不含**任何 future-work 挂钩或 55-题钩子的表述,无需改稿。
