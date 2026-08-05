# 论文方法偏差与验证台账

> **状态：2026-07-25 文档纠错版;2026-07-28 双盲审计增补**(M-23/M-24 新增,M-07 尾注扩写,M-12/M-16/M-18 行内 [更新] 注记;审计=FLOW-PAPER/FLOW-IMPL/FLOW-DIVERGENCE-VERDICT 三件套);**2026-07-29 M-23 用户裁定**(开关化,默认 global,SPEC §7.17);**2026-07-30 M-25 新增**(Table 8 噪声阈 ±5%,发射前设置复核发现,分析层采纳/门不改)。
>
> 本文是当前方法学边界的权威索引。它不把工程补全项包装成论文参数，也不把已有探针运行包装成 Ensemble 验证。历史计划、SPEC 和实验总结中的旧文字均保留；若与本文冲突，以各文件顶部的 `CORRECTION / ERRATA` 和本文为准。

## 1. 证据标签与裁决原则

- **[PAPER]**：论文明确陈述，或可由论文算法/表格直接读出。
- **[OURS]**：为让未开源机制可执行而作出的保守工程选择；不是论文参数。
- **[UNKNOWN]**：论文没有给出足以唯一实现的信息。
- **[UNVALIDATED]**：已有单元/集成测试不等于真实 LLM 实验验证。

任何正式运行都必须把 `[OURS]` 项写入 experiment lock；不同合理解释必须作为 ablation 分臂，而不是在结果出来后调整。当前代码的正确表述是“实现并测试了若干机制与契约”，不是“复现或验证了论文的 Ensemble 结论”。

## 2. 方法学 gap / 不合理选择台账

| ID | 论文陈述 / 可读出的要求 | 问题 | 我方保守决定（均为 **[OURS]**，不是论文参数） | 必须补做的 ablation / validation |
|---|---|---|---|---|
| M-01 `K` 与 `K_t` | [PAPER] 池中可有至多 `K` 个 Level-2 变体；Table 8 给出每轮候选数 `K_t=4`。 | [UNKNOWN] 论文未给 `K`；`K_t` 是候选生成数，不是池容量，二者不可互换。旧 SPEC 的 `K=8` 只是我方默认。 | 运行锁中把池容量与 `candidates_per_round` 分开记录；不得由 `K_t=4` 推导 `K`。没有预注册时不声称某个 `K` 来自论文。 | 预注册至少两个池容量；固定候选预算比较容量敏感性，并报告实际活跃池大小、fork/retire 次数与成本。 |
| M-02 `cluster(x)` | [PAPER] 路由按任务所属 cluster 的历史成功率；目标被写成“target cluster”。另有 task-level tournament pilot。 | [UNKNOWN] 论文没有公布 cluster 构造算法或完整映射；task-level tournament 是单独实验，不能当作 cluster 的定义。 | 主路径只接受显式 `task_id -> cluster_id` API；GAIA recipe 暂以 level 作为可审计代理。`task_tournament` 仅作为兼容/消融分臂。 | 对 level、failure-mode、domain/人工标注 cluster 和 task tournament 分别运行；报告未知 cluster 比例与映射版本。 |
| M-03 成功率估计器 | [PAPER] 路由到该 cluster 上“estimated success rate”最高的变体。 | [UNKNOWN] 未指定平滑、分母、历史窗口、冷启动、缺失值和并列裁决。不同选择会改变路由与退役。 | 当前实现使用可审计的历史 ledger；默认全历史、cluster 聚合估计；未知/无证据走确定性冷启动，并列默认优先该 cluster 尝试数更少者。窗口和 tie-break 可配置。 | 预注册并比较全历史/滑窗、无平滑/平滑、不同冷启动和 tie-break；报告路由熵、命中率与切换率。 |
| M-04 fork 噪声与阈值 | [PAPER] mixed conflict 时 fork；论文同时讨论约 `±5%` 的波动/噪声。 | [UNKNOWN] 论文没有给 fork 所需的改善/退化任务计数，也没有说明 `±5%` 与单任务 seesaw、聚合分数或统计显著性的关系。旧实现采用 `(2,2)` 是我方门槛，并直接挡住了日志中的 `1+1` 和 `2+1` mixed conflict。 | 当前默认 `min_fork=(1,1)`，即有至少一个改善和一个退化任务就 fork；`(2,2)` 只保留为工程消融。`±5%` 不被冒充为已定义的 fork 阈值。 | 在固定随机种子与任务集上比较 `(1,1)`、`(2,1)`、`(2,2)` 和基于置信区间的门槛；报告假 fork、fork 后收益和池膨胀。 |
| M-05 fork/retire 生命周期 | [PAPER] 冲突时 fork；达到上限时退役最低表现变体。 | [UNKNOWN] 未规定 parent/child 继承哪些 ledger、路由、版本和身份；也未定义同轮 retire、孤儿任务、冻结时点与回滚。 | 当前引擎采用两阶段 settle：先基于冻结的 prior-round 路由评估/裁决，再统一 reconcile fork/apply/retire；不让同轮结果反向污染自身路由。身份与实际 carrier 分开记录。 | 构造 APPLY/FORK/REJECT 与同轮 retire 组合测试；真实运行审计每个任务始终有 carrier、无悬空变体、可由日志重放。 |
| M-06 “最低表现”可比性 | [PAPER] 池满时退役 lowest-performing 变体。 | [UNKNOWN] 各变体服务不同 cluster、任务难度和样本量；原始成功率不可直接横比。 | 默认用 task-macro 退役分数；实现 cluster-macro 与 raw 作为显式分臂。任何一种都是我方选择。并列按稳定 ID 确定性裁决。 | 比较 task-macro、cluster-macro、raw 和样本量/不确定性校正；报告每次退役前的覆盖、分母与反事实影响。 |
| M-07 gate 与组合评分范围 | [PAPER] 候选只在路由给目标变体的任务上测试；最终 Ensemble 是全任务组合表现。 | [UNKNOWN] 能否复用 scoped candidate gate rollout 来报告整池分数、何时必须重跑，以及 fork 后 carrier 对应关系未说明。 | 候选诊断与 settled active-pool 评分分成两条数据流。只有“同配置、同 carrier、完整相同任务子集”时才允许安全复用；REJECT 不得污染最终分数。 | 人工覆盖 REJECT/APPLY/FORK/空候选轮；逐轮核对 active-pool 恰好覆盖固定全任务集，并以独立 scorer 复算抽样轮。**[补 2026-07-28,双盲审计 NEW-3]** 门的 before 基线取自结算后全量流(唯一 `ledger.record`),after 取自候选 T_k 流——seesaw 判定跨两次独立 pass@2 采样,是**决策层**方差暴露(区别于 M-08 的报告层掩蔽),可产生幻影回退/改进并直接改变 APPLY/FORK/REJECT 走向;须报告门级幻影率(同 config 重评估计),§9.5 检验门决策而非仅报告的方差敏感性。 |
| M-08 `pass@2` 的掩蔽 | [PAPER] 主评估使用 pass@2。 | pass@2 只关心两次至少一次成功，可能在单次成功概率下降时仍保持不变；小样本离散化也会隐藏退化。 | gate 按预注册的 pass@2 语义运行，但报告必须同时给出 pass@1/底层 rollout 计数与分母；不以 pass@2 平坦推断“无退化”。 | 固定预算同时报告 pass@1、pass@2、每任务 paired outcome 与置信区间；模拟/实测识别 pass@2 不变但概率恶化的情况。 |
| M-09 peak/final 与 held-out | [PAPER] 报告演化过程的 peak/final。 | 当前 peak/final 都来自反复用于适应、路由和选择的 evolution/adaptation set，没有独立 held-out；存在选择偏差。 | adaptation 指标只标作 adaptation diagnostic，不称泛化结果。正式结论要求冻结后的 held-out 一次性评估。 | 预注册 adaptation/validation/test 划分；选择完成后只评一次 held-out，并报告多重选择修正或 bootstrap 区间。 |
| M-10 三 seed 聚合 | [PAPER] Table 8/实验设计使用 3 seeds；Table 5 给聚合结果。 | [UNKNOWN] Table 5 的方差、seed 聚合方式、跨任务权重和 peak/final 聚合顺序不清楚。三个 seed 也不足以稳定估计重尾在线任务方差。 | 锁中保存单次 seed 与 `planned_seeds`，不把 seed 列表伪装成已运行。正式表同时报告逐 seed 结果、均值/中位数、离散度和聚合公式。 | 完成全部预注册 seeds；比较“先按 seed 求 peak 再平均”与“逐轮聚合后求 peak”，并给 bootstrap/随机效应敏感性分析。 |
| M-11 live web / 基础设施混杂 | [PAPER] GAIA 涉及在线工具与基础设施，且 infra failure 可计为失败。 | 网页内容、搜索排名、限流、认证、provider 和时间都会变；把 infra failure 与推理失败合并会混淆机制效果。 | 运行锁记录 provider、模型、配置/数据/oracle/prompt 哈希和时间；报告把 infra failure、budget exhaustion 与任务失败分开。无法解析的 provenance 标为 `unresolved`，不填假值。 | 使用缓存/固定网页快照复跑子集；比较 live 与 replay；报告错误类别、重试政策、时延与限流。 |
| M-12 AEGIS 完整性 | [PAPER] 描述 Digester/Planner/Evolver/Critic 多角色流程及部分提示/算法。 | 公开材料没有给出足以逐字重建全部角色、提示、结构化 schema、重试与 ship-ranking 的实现；“实现接口”不等于“复现 AEGIS”。 | 当前提供 CandidatePipeline、结构化候选校验、最多一次 revision、隔离 Evolver adapter 与确定性 Critic 合约；确定性 Critic 只是 fallback。**[更新 2026-07-28]** LLM 四角色已以 `--aegis-digester/planner/critic llm` 接线并 **live 端到端跑**(aegis1 与 a1big1-5 系列;工件含 `llm_aegis_reproduction: true`);Planner prompt 论文全文逐字、Evolver 78.9%/Critic 81.7% 公开部分并入,Digester prompt 论文未公开系 [OURS]。机制层已实战;效果层结论仍待 S1,[UNVALIDATED] 判词不变。 | 用公开/预注册 prompt 完成真实 adapter；做角色消融、prompt 稳健性、schema failure/retry 与成本审计；不得在此之前声称 AEGIS 已验证。 |
| M-13 新任务路由 | [PAPER] 新任务按其 cluster 的历史成功率选变体。 | [UNKNOWN] 真正未见任务/新 cluster 没有历史；cluster 识别本身可能依赖答案或失败后信息，形成泄漏。 | cluster 映射必须在解题前可得；未知 cluster 走确定性冷启动，不偷偷降级到 task-level 历史。记录未知映射。 | 构造时间切分或留一 cluster 外推；只用解题前特征映射，报告新任务、新 cluster 的路由准确度和后悔值。 |
| M-14 旧探针结果边界 | [PAPER] 无直接对应；这是本复现的报告完整性要求。 | `forkprobe_p2` 的 R1 没有评分，只有 R0/R2/R3 有 scored records；R2/R3 候选均被 REJECT。旧 reporter 却把被拒 R3 的 `0.6667` 当成 final，候选诊断污染了 settled pool 结果。 | 历史文件原样保留并标 ERRATA。新报告 final/peak/curve 只消费 settled active-pool score，候选结果单列 diagnostic。 | 对旧 artifact 写一次离线迁移/审计；用故意高分 REJECT 候选验证 headline 不变；逐轮验证 evaluated denominator。 |
| M-15 跨模型可比性 | [PAPER] 原实验使用论文指定的内外环模型与其当时基础设施。 | 本项目替换为 DeepSeek V4；模型能力、采样、上下文和工具调用不同。绝对分数和 fork 频率都不能直接归因于论文机制。 | 定位始终是 paper-informed re-implementation；只做同一运行条件下的预注册对照，不对齐论文绝对分数。 | 在预算允许时做至少一个同模型 Global vs Ensemble 对照；若跨 provider，分别报告，不把差异解释为机制因果。 |
| M-16 round-global `K_t` 与 target 选择 | [PAPER] Algorithm 1 L15 在一个 round 中生成候选集合 `{h_t^k}_{k=1..K_t}`；Table 8 给 `K_t=4`。 | 当前 `CandidatePipeline.k_t`/engine queue 的边界是一次 target/variant invocation。若每个 active variant 各取 4 个，一个 round 可膨胀为 `4 × active_variant_count`，这不是论文的 round-global `K_t=4`。同时论文没有给出每轮应选哪个 target variant。 | 不把当前 per-variant limit 冒充论文 `K_t`。最保守正式主臂应先用 prior-round evidence 选**一个** target variant，再在整个 round 全局生成至多 4 个候选；target selector 必须明确标作我方选择并预注册。**[更新 2026-07-28]** live 行为=每轮经 `--target-strategy`(默认 worst_first,[OURS])选**单目标变体** + 全局候选帽 `--candidates-per-round`;单目标政策下实质等效 round-global(a1big5 R3 目标为 V1=选择器实战工作中);**多目标** coordinator 仍未建,仅多目标政策时才需要。轮预算换算另见 M-24。 | 比较 round-global-4 与 per-variant-4 的效果/成本；对 lowest-performing、error-mass、round-robin 等 target selector 做预注册消融，并报告每轮候选总数与被选 target。 |
| M-17 single-ship / multi-ship 矛盾 | [PAPER] Algorithm 1 L21–25 看起来选择首个过 gate 的 `k*` 并单次 ship；Appendix B.1 p.34 又明确要求按 `ship_ranking` 顺序 ship 所有 bucket-disjoint candidates（Multi-ship）。 | 两处不能同时作为唯一操作语义；它们会产生不同 pool 轨迹、成本和同轮相互作用。当前 queue 对每次 target invocation 采用 first-pass-wins，但结合 M-16 的多 target loop 也不等于严格的 round-global single-ship。 | 当前 first-pass-wins 仅标为“按 Algorithm 1 主文作出的工程裁决”，不称唯一论文行为。Appendix multi-ship/bucket-disjoint reconciliation 尚未实现；必须作为独立 ablation。 | 实现并比较 round-global first-pass single-ship、ranked bucket-disjoint multi-ship；固定总候选/rollout 预算，报告同轮 ship 数、bucket overlap、顺序敏感性和 pool 轨迹。 |
| M-18 actionability / selective invocation | [PAPER] Algorithm 1 先形成 failure landscape / actionability `a_t`，并在 `a_t < α` 或没有可行动 landscape 时跳过候选演化。 | **[更新 2026-07-28]** 前置短路已实现并**实战触发**:`a_t<α` 短路(candidate_pipeline.py:438)与 empty-landscape 短路均位于 Planner/Evolver 之前;fixsmoke1 实跑触发 selective no-op(a_t=0)。残留偏差:默认确定性档 a_t 为二元 {0,1} 且 auto α=1.0,退化为"仅全部路由任务已解才短路";`--aegis-digester llm` 才有分级 a_t(α=0.5,[OURS])。 | 不声称 selective invocation 已实现。正式接线前增加可审计 actionability artifact 与 Planner/Evolver 前的确定性 short-circuit；`α` 的数值/估计若论文未唯一给定，必须标作我方参数并锁定。 | 测试 empty landscape、低于/等于/高于 `α` 三个边界，断言跳过时 Planner/Evolver/Critic 零调用；报告触发率、节省成本和阈值敏感性。 |
| M-19 manifest 契约不存在于开源 repo（`--manifest-mode`） | [PAPER] Table 9 (p.36) 定义 change manifest schema（capability_evidence/file_changes/predicted_impact/attribution_signature 等结构化字段）；gate stage 1 = manifest completeness。 | [UNKNOWN→事实] 论文 Table 9 契约在开源 `harnessx` repo 中**不存在**；repo 的 meta-agent 按其自带 `skills/journal/SKILL.md` 的 journal 词汇（`levers`/`predicted_affected`/`hypothesis_id`，以及 `lens`/`lever`/`intent`）写作。`runs/forkprobe_11`（$3 真跑）中 ~3/12 候选因用 journal 词汇填我方 `extra="forbid"` schema、并以自然语言字符串填 list/dict 字段而在 `PIPELINE_PROPOSAL` 解析阶段死亡。 | `--manifest-mode` 双分臂，均为 **[OURS]**：①`repo`（默认）＝适配层 `adapt_repo_journal_manifest`，把 `levers→bucket`、`predicted_affected→predicted_impact.tasks_will_unlock`、`hypothesis_id→source_hypothesis_id`，prose file-change bullets 结构化；论文有而 repo 没有的字段（capability_evidence、attribution_signature）由 `paper_only_gaps()` **显式标为缺失，绝不伪造**，并以 `provenance="repo_journal"` 在 gate stage 1 放宽这两项（确定性 seesaw 仍是唯一 ship 权威）。②`paper`＝在**我方 recipe brief**注入 Table 9 schema + C-R10-02 实例，要求严格 paper 格式；**不改 `harnessx/` 任何文件**。`repo` 放宽 manifest-completeness 是 [OURS] 决定，须预注册并锁。 | 预注册 `repo` vs `paper` 双分臂：比较两模式下候选被评测率、gate 通过率、被 `paper_only_gaps` 标缺失的候选比例与其 seesaw 结果；报告 repo 放宽是否引入 reward-hacking（声称的 attribution 从不出现却 ship）。 |
| M-20 单 Evolver 阶段多候选 vs N 次独立会话（`--candidates-per-round`） | [PAPER] Algorithm 1 L15 生成候选集合 `{H̃^k}_{k=1..K_t}`，读起来是**一个 Evolver 阶段**一次产出多候选；Table 8 `K_t=4`。 | 当前实现把每个 slot 跑成**一次独立 meta-agent 会话**（独立 output_dir、独立 evolve、独立内部 gate），`N` 候选 = `N` 次会话，把每轮成本与失败率乘以 `N`；这不是论文单阶段多候选语义。与 M-16 的 round-global vs per-variant 是**不同**的偏离轴。 | 默认 `--candidates-per-round 1`（repo 原生单候选，先跑通）；`4` 对齐 Table 8。不把 N 次独立会话冒充论文单阶段多候选；help/docstring 明写此偏离。 | 比较单阶段多候选（共享一次 Digester/Planner 上下文）与 N 次独立会话在效果/成本/多样性上的差异；报告每轮总会话数、总成本与候选间冗余度。 |
| M-21 no-config 重试复用 §4.3 一次 revision（`--evolve-retry`） | [PAPER] §4.3 允许 Critic 对候选做**一次** revision。 | repo 的 meta-agent（DeepSeek pro）常以"分析完但不写 config.yaml"结束，触发 `agent.py:910-939` 的 `DECISION_REQUIRED.md`；`runs/forkprobe_11` 中 ~9/12 候选如此死亡。论文的一次 revision 针对的是 Critic 对**已成形候选**的修订，不是对"未产出 config"的重试。 | `--evolve-retry N`（默认 1）＝检测到无 config（以 `DECISION_REQUIRED.md` 出现为准）时把该文件内容附回 brief 再跑一次，最多 N 次；每次写入隔离子目录。**绝不**把"未产出"自动当 no-op 兜底（会污染 idle 统计）。把"借用 §4.3 一次 revision 额度用于 no-config 重试"标作 [OURS]，重试次数记进 `pool_report.md` 的 W28 节。 | 报告触发重试的候选比例、重试后成功率与额外成本；对照 `--evolve-retry 0` 量化重试净收益；确认无 config→重试→仍无 config 时归档为失败而非 no-op。 |
| M-22 repo-mode L2 证据机器自证（`--l2-cert`；SPEC §7.11 乙+甲） | [PAPER] Evolver prompt p.32：tools 桶候选必须提供 Level-2 往返证据（"round-trip reaches the model: a unit call that returns does not prove the agent sees the return"），作为 gate stage 4 (ROUNDTRIP_L2)；p.32 反对"I believe this will work"，要求附上验证输出为 `capability_evidence`。Critic 在 ship 前核验 Level-2（p.37, C-R10-02）。 | [UNKNOWN→事实] 开源 repo journal 无 `capability_evidence` 槽位；即便契约明文要求（e79e76d，`TASK.md:89` 逐字），DeepSeek meta 仍不写（`runs/forceprobe1` R1，n=1 不遵守）→ 所有 tools/processor 桶候选死在 ROUNDTRIP_L2，媒体簇的 Action 杠杆瘫痪，失去与 C-R10-02 对话资格。这是 §7.11 的直接证据来源。 | `--l2-cert {auto,off}`（默认 auto），均 **[OURS]**：**甲（保留）**＝meta 亲笔 Level-2 证据始终优先采用（顺带产出"亲笔率"作论文数据点），契约措辞一字不动。**乙（新增兜底，repo 模式 + tools 桶）**＝meta 未申报时，recipe 取该候选评测轨迹中新工具的**真实输出**（HarnessJournal session JSONL 的 `raw_tool`/`tool` 记录，含大输出的 `tool_results/*.txt` 外化），过 provider **真序列化器**（`manifest.check_level2_roundtrip` + litellm `to_openai_content` tool-message 路径，**生产路径无 stub**），stage 4 按该测量 pass/fail，机器证据记入 `_candidate_meta[...]["l2_certification"]` 标 `OURS_machine_certified`，并在 `pool_report.md` W28 节出一行汇总。**边界**：新工具在候选评测中从未被调用→无证据可取，**诚实拒**（"no capability evidence possible"，与 attribution `expected_min_calls≥1` 同精神）；真实输出不存活序列化→**诚实拒**（"failed_probe"，C-R10-02 级捕获，比"未申报"更有信息）；processor 桶 v1(**v2 已升级为 replay-execution 机器自证,OURS-v2 待晨间复核,详见本行末 ablation 栏**)维持"必须亲笔申报"（轨迹取证语义未定,不装覆盖，委托内置 `_declared_level2` 保 FAIL）；paper manifest 臂一字不动（忠实臂，从不注入）；legacy 不透明候选无 manifest 支撑的 stage 4，不注入。**诚实性论证**：这是**测量而非软化判据**（软化=戊/丁墓碑老路），符 p.32 反"I believe this will work"之精神——叙事作"证据优先由 Evolver 提供；缺失时由 recipe 对真实运行产物执行同一探针并标注机器来源"，比口头声明更强。经引擎现成注入缝（`VariantPoolEngine(gate=...)` → `run_gate(check_roundtrip=...)`，gate.py:384-410）落地，不改 `harnessx/`/gate/manifest/engine。否决案：丙 retry 再教（费 meta 调用且无保证，留论文期补充）、丁放宽第 4 关、戊禁工具候选。 | 报告 meta 亲笔率 vs 机器兜底率；机器自证候选的 certified/failed_probe/no_invocation/no_target 分布及其后续 seesaw 结果；核对机器证据不引入 reward-hacking（声称的工具从不出现却 ship——机器兜底恰好用真实调用堵此缝）；补做丙（retry 再教）预注册分臂量化其相对机器兜底的净收益；processor 桶轨迹取证语义 **v2 已实现(OURS-v2,pending morning review / 待晨间复核,Jul-27)**：当候选为 PROCESSOR-only(无 tools 增改,复用 `_l2_target_tool_names` target-derivation 判别)且未申报 L2 时,recipe 以 **replay-execution 证据**机器自证——校验候选 `_meta_scratch/REPLAY.md`(与胜出尝试 config 同址,定位镜像 `_finalize_slot` 的 manifest 定位 `config_path.parent/_meta_scratch/`)存在且含 pass marker `# Replay gate passed`;通过则 stage 4 PASS,记 `l2_certification` outcome `certified_processor_replay`、provenance `OURS_machine_certified_v2`、note 明标"弱于 tools 序列化探针——真实执行证据而非序列化存活"。依据:候选抵达门 ⇒ evolve 内部 replay 冒烟已通过 ⇒ 所有已注册 processor(无条件 run-loop hook)均在真实运行循环执行。REPLAY.md 缺失或无 pass marker → 维持 v1 原 FAIL(`no Level-2 round-trip evidence for bucket=['processor']`,reason 字节不变);tools 桶行为字节不变;mixed tools+processor 由 tools 规则裁决(探工具;工具从未被调用仍诚实拒,processor 路径不救援);甲(亲笔)/paper 臂/legacy 均不变。a1pilot2 两 processor 候选死于该 v1 边界是本升级直接证据源。须补 ablation:亲笔率 vs replay-execution 兜底率、`certified_processor_replay` 分布及其后续 seesaw、核对 replay-execution 不引入 reward-hacking(声称的 processor 从不执行却 ship)。 |
| M-23 seesaw 回退基线的变体作用域(双盲审计 NEW-1,**高危·Ensemble 专属**) | [PAPER 张力] §4.1 p.8 以全史 `T_t` 定义 seesaw;§4.5 p.11 又要求 per-variant 隔离("improvements to one cluster cannot regress another")——对"回退基线是否跨变体"给出两读。 | `is_ever_solved`(ledger.py:265-267)为**全局跨变体全史只增**;改进基线(engine.py:822-824)为 per-variant cell;门(gate.py:206-209)因此**不对称**:变体 k 对"任何变体解过的任何任务"背负不回退义务。K=1 下全局=per-variant 故不显形,此前全部 K=1 探针测不到;**K≥2(含 S1 的 K=8 臂)必然显形**。 | 暂保留全局 ever_solved 读(更保守的反回退,对齐 §4.1 字面),lock 须显式声明为双读中的**全局读**;**S1 冻结前须用户终裁**(全局/仅本变体/本簇三选)。已同步勘误 GAP-AUDIT §1 的 verbatim 过度声明。**[裁定 2026-07-29]** 用户终裁:开关化 `--regression-baseline {global, per_variant}`,默认 global(=现行为,字节等同);per_variant 入 S1 消融;本簇读弃。接线排 M1 构建后、S1 冻结前(SPEC §7.17)。**[接线 2026-07-29 落地]** 开关已建(SPEC §7.19,853 绿):per_variant 复用 TaskEval.before 的 per-variant 采样语义;第七旗入 provenance_warnings,resume 拦换基线续跑。 | K≥2 下比较 {全局 ever_solved, 仅本变体, 本簇} 三种回退基线对 fork 率、池膨胀、final 的影响;报告"跨变体误判回退"比例。 |
| M-24 轮预算换算 off-by-one(双盲审计 NEW-2) | [PAPER] Algorithm 1 `for t=0..T-1` 自 t=0 即适应 ⇒ T=15 得 **15 个适应轮**。 | 实现 R0 为纯基线轮(不进引擎,run_variant_pool.py:3823-3833),适应自 R1 起 ⇒ `num_rounds=15` 实得 **14 个适应轮**。系"Digester 读先前轮账本"设计(需 R0 播种账本)的必然推论。 | 对齐论文 15 适应轮须 `--num-rounds 16`,或在报告中声明适应轮=N−1;**S1 冻结时二选一并锁定**。 | 报告口径统一以适应轮计;S1 lock 记录换算选择。 |
| M-25 噪声阈 ±5%(Table 8;Jul-30 发射前设置复核新发现) | [PAPER] Table 8 (p.29) 列 evolution-protocol 超参:noise threshold = "ignored single-round pass-count delta ±5%";**正文通篇无作用点阐述**。佐证指向**分析层**:Fig.4 (p.16) 图注以 "Net durable gain over 9 rounds ≈0, all inside noise" / "statistically one plateau, not a second win" 解读轮间波动,并用 **identical-config replay**(R12)校准噪声抬高的假峰;App C (p.37) R10 实现 +6/−1 的 −1 系**轮后事后观测**(manifest `tasks_at_risk=[]`,门评未见该回归)⇒ 非门级宽容,门仍零容忍(§4.1 verbatim"must not regress any previously solved task")。 | 我方全栈无此过滤:门逐题零容忍(对齐 §4.1);报告层此前亦无噪声带口径。 | **采纳为分析层口径**:S1 判读规则增补"单轮聚合 Δ≤±5% 记噪声带"(n=103 ≈5 题;n=30 ≈1.5 题);**门不改**(不引入论文未言明的门宽容;若被主张门级读法,以 App C tasks_at_risk=[] 证据反驳)。论文的 identical-config replay 噪声探针与我方 a1big4"无 ship 漂移 −13.3pp 噪声地板"方法同构,可互引。 | 可选消融:门级 ±5% 宽容臂 vs 零容忍臂对 ship 率 / fork 率的影响——检验论文 Global"ship 流不断"是否部分源于更宽的实际门径。 |

## 3. 已知实验事实的纠正

以下是对现有 artifact 的事实描述，不是对 Ensemble 效果的统计结论：

1. `recipe/gaia_evolver/runs/forkprobe/data/rejected_candidates.jsonl` 记录了 R2 的 `1 improved + 1 regressed` mixed conflict；它因旧工程门槛 `min_fork=(2,2)` 被拒。
2. `recipe/gaia_evolver/runs/forkprobe_p2/data/rejected_candidates.jsonl` 记录了 R2 的 `1+1` 和 R3 的 `2+1` mixed conflict；二者均因未达到旧 `(2,2)` 被拒。
3. 因而，这些已观察 mixed conflict 没有产生 fork 的直接原因是旧 `(2,2)` 门槛将它们 REJECT；“没有 mixed conflict，所以没有 fork”是错误解释。
4. `forkprobe_p2` 不是“五个完整 scored rounds”：其状态含 R0–R3，但 R1 的 `evaluated_tasks=0`；报告曲线仅含 R0、R2、R3。
5. `forkprobe_p2` 的 R2/R3 是被拒候选。旧报告把 R3 候选分数当作 active-pool final/peak，因此该 `0.6667` 只能视为历史候选诊断，不能视为 settled Ensemble final。
6. 这些探针没有激活并评估一个有效 fork 后的 Ensemble 路由池，故**现有实验尚未验证 Ensemble，也不能支持 Ensemble 优于/劣于 Global 的结论**。
7. `recipe/gaia_evolver/runs/forkprobe_11/data/rejected_candidates.jsonl`（$3 真跑，K=3/pass@2/12 题/5 轮）记录 **12/12 候选全部死在 `PIPELINE_PROPOSAL`，`candidates evaluated: 0`**，fork 门槛根本未被执行到：~3/12 是 manifest 格式不符（journal 词汇 vs 我方 Table 9 `extra="forbid"`），~9/12 是 meta-agent 分析完未写 config（`DECISION_REQUIRED.md`）。这是 M-19/M-20/M-21 三项修复的直接证据来源，`forkprobe_11` 目录只读保留为证据。

## 4. 当前实现状态（不等于实验结论）

> **CORRECTION(2026-07-28)**:本节以下为 07-25 快照,多项"尚未实现/Not yet run"已被
> 后续施工与实跑超越——LLM 四角色 live(aegis1、a1big1-5)、selective short-circuit
> 实现并实战触发(fixsmoke1)、§5 第二组 smoke gate 清单全部完成(smoke_hard2→a1big5)。
> 快照原文按本文档约定保留;现状以 M-12/M-16/M-18 行内 [更新] 注记与 RUN-LOG 为准。

### Implemented / tested

- fork 默认门槛为 `(1,1)`；`(2,2)` 仅作显式消融。
- Router 支持真实 `task_id -> cluster_id` 注入与 cluster 聚合估计；`task_tournament` 是兼容/消融模式。
- retirement 支持 `task_macro`、`cluster_macro`、`raw`，默认 `task_macro`。
- engine 采用 prior-round routing freeze 与两阶段 settle，之后统一 reconcile APPLY/FORK/REJECT/retire。
- 候选 gate 数据与 settled active-pool 全任务评分分离；REJECT 候选不能进入 final/peak。
- CandidatePipeline/Critic 结构化契约、候选隔离、确定性排序/去重、最多一次 revision 与 audit 已实现并测试。
- 候选 queue 当前按每次 target/variant invocation 选择首个过 gate 者；engine 可遍历所有 active variants，候选上限也是 per-variant。这个行为已经被测试，但**不等于**论文的 round-global `K_t=4` 或严格单次 ship。
- actionability `a_t/α` 与 empty-landscape selective short-circuit 尚未实现；当前 Pipeline 合约不得被描述为完整 Algorithm 1 invocation policy。

### Integrated but not live-tested

- GAIA recipe 已接入 level cluster、cluster routing、两阶段 settlement、独立 active-pool scorer、报告分母/错误分类与 provenance lock。
- 这些接线已经通过自动化测试；尚未用真实 provider 端到端跑出可作为论文结果的完整 live artifact。
- 该接线仍缺 round-global target selector / `K_t` coordinator，且没有 Appendix B.1 multi-ship；因此在修正前不能称为论文主臂的 live integration。

### Not yet run

- 真实 LLM Digester/Planner/Evolver/Critic adapter 与 CandidatePipeline 的 live recipe 端到端运行。
- round-global target selector / `K_t≤4` coordinator，以及 Algorithm-first-pass 与 Appendix multi-ship 两个预注册分臂。
- actionability artifact/threshold 和在 Planner/Evolver 前执行的 selective no-op。
- 预注册的 Global vs Ensemble 同条件对照。
- 论文规模的 `103 tasks × 15 rounds × 3 seeds` 正式矩阵。
- held-out 泛化评估、cluster/estimator/fork/retirement ablations，以及 live-web replay 稳健性检查。

## 5. 下一步验收清单

### Implemented / tested：合入前必须持续为真

- [x] 默认 `(1,1)` 与 `(2,2)` 消融可区分并锁定。
- [x] cluster API 与 `task_tournament` 模式语义分开。
- [x] task-macro / cluster-macro retirement 有确定性测试。
- [x] 两阶段 settle 不发生同轮路由泄漏。
- [x] rejected candidate 不污染 final/peak/curve。
- [x] active-pool score 覆盖固定全任务集并报告真实分母。
- [x] CandidatePipeline/Critic 的结构化失败、revision、audit、隔离与 per-invocation queue 合约有测试。
- [x] 当前 per-variant queue 行为有明确测试与文档，不被误标为 round-global `K_t`。

### Integrated but not live-tested：下一次小规模 live smoke gate

- [ ] 使用真实、非占位 provider/provenance 启动 3–5 个任务、2–3 轮的 smoke run。
- [ ] 至少人工核对一轮 REJECT 和一轮 APPLY/FORK 的 candidate/settled 两条数据流。
- [ ] 核对 cluster 映射在解题前生成、无答案/失败后信息泄漏。
- [ ] 核对每轮 active carrier、路由、分母、infra failure 和 budget exhaustion 可重放。
- [ ] 接入真实 LLM AEGIS adapters 后，验证 schema retry、一次 revision 上限和 token/cost 记录。
- [ ] 在任何论文主臂 smoke 前，实现并锁定“一轮一个 target、round-global 候选总数 ≤4”；记录 target selector 是我方参数。
- [ ] 在任何论文主臂 smoke 前，实现 `a_t < α` / empty-landscape short-circuit，并验证跳过时 Planner/Evolver/Critic 不被调用。

### Not yet run：正式结论 gate

- [ ] 冻结 SPEC、prompt、代码 SHA、dataset/config/oracle 哈希和全部 `[OURS]` 参数。
- [ ] 实现并测试 actionability / empty-landscape 前置 short-circuit；在此之前不得称 Algorithm 1 selective invocation 已完成。
- [ ] 预注册 Global/Ensemble、cluster、estimator、fork threshold、retirement 与 seed 聚合分臂。
- [ ] 预注册 round-global first-pass single-ship 与 ranked bucket-disjoint multi-ship 消融；不得把任一分臂称为唯一论文行为。
- [ ] 完成 `103 × 15 × 3` 或在报告中明确、定量说明任何缩减。
- [ ] 同时报告 pass@1/pass@2、逐 seed 结果、适应集 peak/final 与一次性 held-out。
- [ ] 通过 live/replay 基础设施敏感性检查。
- [ ] 只有在上述 gate 完成后，才允许使用“验证 Ensemble 效果”或“复现 Table 5 结论”等表述。

## M-26 reasoning effort 分层(Jul-31 新增,裁 E)

| 项 | 内容 |
|---|---|
| 论文原设 | **未指定**——原文 Table 8 无 reasoning effort 参数(其 meta 为 Opus 4.6 @200 步/角色) |
| 我方做法 | RQ1 两臂(s1k8b103 / s1k1b103)**维持服务器默认档**(配对纪律:两臂只差 pool-k);RQ2 家族(A1 高档补测 / B0 / B2)**统一 effort=high**;**判分器恒定在默认档**(测量仪器不随处理变化) |
| 理由 | 端点支持该旋钮而原文纪元不存在此参数 ⇒ 无复现义务,按内部可比性择优:族内统一、跨族声明 |
| 影响面 | RQ1 内部可比 ✅;RQ2 内部可比 ✅;**RQ1 与 RQ2 之间不可直接比绝对值**(故 A1 须以 high 档重测才进 RQ2 表);判分口径全程恒定 |
| 裁决层 | 用户裁定(Jul-31 "E拉满");实现 SPEC §7.20,旗控默认不传=字节等同 |

## M-27 演化产物路径 fail-closed 校验(Aug-02 新增)

| 项 | 内容 |
|---|---|
| 论文原设 | **未指定**——原文未讨论演化产物的投递失败,亦无产物消费性检查 |
| 触发 | s1k8b103 中 Evolver 把三个变体的 `template_path` 写成 `file:///D:/...` URI;无人校验;URI 打不开;`harnessx/core/processor.py:714` 的崩溃处理 `yield event` 静默透传 ⇒ 大量 rollout 跑在 **空系统提示词**上,而准确率曲线照常上升。**精确计数(Aug-02 用 `pool_differentiation.py` 全量重取,引用须带范围标签)**:active 池 **634** 次(V5 128 / V6 274 / V7 232);candidate 门**另有 256** 次(V3 104 / V6 128 / V4 24)⇒ 被污染的不只是测量,**还包括 ship/reject 决策链**,且 V3/V4 只在候选范围显形。此前的「642」系仅扫 active 池且计数略偏,**作废** |
| 表述修正(Aug-02 03:2x,双轴复测后) | 早前写法「变体从来没被分化开」**不准确,已改**。末轮 R15 实测:active 池 3 个变体(V0/V6/V7),**归一化 config 哈希 3 个全不同**(processor 级改动确实生效),但**提示词轴 V6/V7 均为空**。⇒ 准确说法是「池在**配置层**分化了,被静默丢弃的**只是提示词那一路**」;所谓互补性实为在 {基线提示词+演化 processor} 与 **{无提示词+演化 processor}** 之间测量。B-1 因果解释仍作废,但据此也解释了 V0/V2/V3/V4 共用模板哈希 = 它们本就靠 processor 分化。**判定分化必须双轴同读**(仪器:`pool_differentiation.py`) |
| 我方做法 | `_prepare_round_config` 在加载时(a)正规化 `file://` URI,(b)**校验产物可读,不可读则 raise**。内存内改写,**不触碰磁盘上的运行产物** |
| 理由 | 演化提示词**就是**变体本身。产物打不开时静默回落到默认值,会把「没有变体」伪装成「变体表现平平」——这正是本跑发生的事。Fail-closed 把不可见的降级变成加载期的显式失败 |
| 影响面 | 冻结池的不同提示词数 **3 → 5**,V5/V6/V7 各自独有 ⇒ B 臂首次跑在真分化的池上;s1k8b103 的已有产物**未被修改**,其历史数字仍按原样引用(并附 RUN-LOG 的撤回块) |
| 威胁章义务 | Ch7 须记:**本复现的 R5–R15 期间,harness 演化对 3/8 变体是空操作,而系统自身的门控/跷跷板/验收报告全程无感** —— 这是对「自演化系统需要产物消费审计」的一手实证,也是本跑准确率增益不可全额归因于演化的原因 |
| 裁决层 | 主循环发现 + 用户令「先修复」(Aug-02);实现 `2e2c68c`,回归测试 `test_artefact_paths.py` |

## M-28 decomp 子任务 session id 路径消毒(Aug-02 新增)

| 项 | 内容 |
|---|---|
| 论文原设 | 不适用(分解是我方扩展) |
| 触发 | 子任务 id 为 `<parent_task_id>::<subtask_id>`,`:` 在 Windows 路径非法 ⇒ 首次冒烟 47/47 子任务 rollout 在 0.0 秒 WinError 123 死亡,零模型调用 |
| 我方做法 | `run.py` 的 session id 过 `_fs_safe()`(仅替换 `<>:"/\|?*`);**逻辑 id 保持原样**用于日志与记账 |
| 影响面 | UUID 类 id 与既有 `R3-V1-active-…` 命名逐字节不变 ⇒ 所有历史 session 目录名不受影响 |
| 裁决层 | 用户令「先修复」(Aug-02);实现 `2e2c68c` |

## M-29 分化池重跑的缩床(50 题;Aug-02 新增)

| 项 | 内容 |
|---|---|
| 论文原设 | §4.5 变体池在完整评测床上演化;我方 S1 对应 GAIA-Text-103 全床(`s1k8b103`) |
| 触发 | M-27 证明 `s1k8b103` 的**选择历史**建立在脏信号上(V5/V6/V7 全程空提示词被评估,唯一真分化的 V1 被选择过程杀掉)⇒ 池必须在诚实信号下重长。而全床实测 26h29m / 15 轮 = **1.76 h/轮**,单夜放不下 |
| 我方做法 | 新建冻结子床 `pool_bed50.json`:分层 L1 19 / L2 25 / L3 6(按 39/52/12 比例),seed 20260801,床内 id 摘要 `4eafb24cf3170035`,规则与 `build_smoke_set.py` 同构(`build_pool_bed.py`)。**除 `--data-path` 与 `--run-tag` 外,命令行与 `s1k8b103` 逐字相同** |
| 理由 | 池的分化深度由**轮数**决定(每轮至多 ship 一个候选;`s1k8b103` 16 轮长出 7 次 fork),非由床大小决定 ⇒ 砍床保轮是唯一能在单夜产出可用池的切法 |
| 代价(必须随数字引用) | 每轮噪声地板由实测 SD **4.57pp**(n=103,B-FREEZE §4.1)扩到约 **6.6pp**(×√(103/50))⇒ **`s2k8b50` 的准确率曲线不可与 `s1k8b103` 比较**。该跑只交付分化池,不交付准确率结论 |
| 威胁章义务 | Ch7 须记:CH4 所依赖的池若最终取自缩床演化,则「池的分化」与「池的质量」须分开陈述——前者本跑可证,后者须回到全床 |
| 裁决层 | 用户睡前授权无人值守(Aug-02 ~02:05「跑你该跑的实验……早上我需要看到完整的分化池」);缩床比例与 seed 由主循环选定并在此冻结 |

## M-30 actionability 阈值 alpha 与池规模的交互(Aug-02 新增)

> **与 M-18 的分工(勿视作重复条目)**:M-18 已确立 alpha 属 [OURS]、须锁定并做阈值敏感性
> —— 那是**机制层**的既有认定。M-30 记的是 Aug-02 夜跑新发现的**经验事实**:
> alpha 与**池规模经由 routing 发生交互**,这一点 M-18 未预见,且它把 M-18 的
> 「阈值敏感性」从可选 ablation 升级为**复现 K=8 的前置条件**。引用时两条并列。

| 项 | 内容 |
|---|---|
| 论文原设 | Algorithm 1 有 alpha,但**未规定唯一操作值**;实现自述 `actionability_threshold_provenance = "OURS: configurable reconstruction parameter; the paper does not specify one unique operational alpha"` |
| 我方现值 | `--actionability-threshold` 未传时按 Digester 模式取默认:llm 模式 **0.5**(`OURS_DEFAULT_ACTIONABILITY_THRESHOLD`),deterministic 模式 1.0(保持字节等同) |
| 已知先例 | `runs/a1smoke`:遗留默认 1.0 下 a_t=0.9 < 1.0,**静默跳过每一轮**;当时的修法即把 llm 模式默认降到 0.5。**同类失败(阈值高于实际 a_t ⇒ 静默不演化)已发生过一次** |
| 🔴 本次触发 | `s2k8b50` R3/R5/R6 = a_t **0.0 / 0.4 / 0.3** < 0.5 ⇒ `short_circuit: actionability_below_threshold`,连续 4 轮无 ship。对比 R1 = a_t **0.9**(50 digests / 18 失败)正常产出 |
| 机制(**已被 R7 部分证伪,以本行为准**) | 初判为「分叉后每变体样本骤降(19 题 / 1–4 失败)⇒ a_t 结构性上不去」。**R7 证伪了该强形式**:同样 19 digests,a_t = **1.0**(全跑最高),未短路,产出 4 候选。⇒ **a_t 取决于失败的「构成」(是否 harness 可修),不是失败的「数量」**;样本小只是**降低了出现可整改失败簇的机会**,不构成硬上限。低 a_t 轮的理由均自述为「模型能力极限/环境问题,harness 改不了」 |
| 停滞的完整归因(**勿只归因于 alpha**) | 7 个演化轮:**ship 2**(R1 fork / R2 apply)、**actionability 短路 3**(R3 a_t=0.0 / R5 0.4 / R6 0.3)、**门拒 2**(R4、R7 各产出 4 候选、评 2 拒 2)。⇒ 降 alpha 只覆盖 5 个不产出轮中的 **3** 个;R4/R7 是候选做出来但**门不收**,降 alpha 不解决,甚至可能放行更弱候选而增加门拒 |
| 影响面 | alpha **直接决定演化频率**,进而决定池深度与 K 的可达值 ⇒ 是 §4.5 复现的一等参数,此前未单列 |
| 待做(**已按 R7 修订**) | ①**先分清两个瓶颈再动手**:短路(3 轮)与门拒(2 轮)成因不同,单调 alpha 只治前者。②若测 alpha 敏感性,须**同时报告门拒率** —— 降 alpha 放行更弱候选时,门拒率上升即为副作用证据。③Ch7 的正确写法**不是**「小床压低 a_t」(R7 反例:19 digests 得 a_t=1.0),而是「**小床减少了每轮出现「可整改失败簇」的机会**,而候选能否过门是另一个独立瓶颈」。④真正的 K=8 可达性问题应表述为:ship 率 = P(有可整改失败簇) × P(候选过门),两项都要测 |
| 裁决层 | 主循环夜跑诊断(Aug-02);**alpha 改动须用户明令**,本文件不构成授权 |

## M-31 演化工具的投递失败:`tool_registry.custom` 的 `file://` 在 Windows 上永不加载(Aug-02 新增)

> **与 M-27 的关系(勿并为一条)**:M-27 是**我方缺校验**(提示词路径无人验证);
> M-31 的根因在 **vendored 解析器本身**(`harnessx/core/harness.py` 对 file URI 的朴素前缀截断
> 在 Windows 上必然拼错路径)。同族(演化产物"产出了≠加载了≠用上了"),但**修法不同**:
> M-27 靠校验+正规化,M-31 靠改写成 vendored 解析器唯一吃得下的形式。

| 项 | 内容 |
|---|---|
| 论文原设 | **未指定**——原文不讨论演化产物的投递,亦无工具加载失败的处理 |
| 根因 | `harnessx.core.harness._parse_file_tool_target` 用 `target[len("file://"):]` 朴素截断。Windows 下 RFC 式 `file:///D:\x` 截完余 `/D:\x`,被解析为「当前盘根目录下的 D: 目录」⇒ **`D:\D:\x`**,抛 `[Errno 22]`。加载器只记 WARNING 后继续 ⇒ **变体带着不存在的工具运行** |
| 🔴 实测范围 | `s1k8b103` **466** 次(含 **`R2`/`R4` 的 active_pool/V1 配置——池本身,不止候选**);`s2k8b50` **40** 次(仅 R11 候选,**池未受影响**);`b_smoke` **14** 次 = **V1 的 14 个会话,一次不漏** |
| 关键推论 | 冻结池中 **V1 = 演化提示词 + `python_eval` 工具,而工具从未加载成功**。V1 正是唯一带真演化提示词、却被选择过程淘汰的变体 ⇒ **它是带着残缺工具集被评估的**,这可能正是其被淘汰的原因。工具文件真实存在(94 行可用 Python) |
| 实测取证 | 四种拼写打真 vendored 解析器:`file:///D:\x` **FAIL**、`file:///D:/x` **FAIL**、`file://D:/x` **OK**、`file://D:\x` **OK** ⇒ 仅**两斜杠**形式可用 |
| 我方做法 | `_resolve_tool_targets`(`run_variant_pool.py`)在加载时把 `file:` 目标改写为 `file://<绝对路径>::<符号>`,并**校验文件可读,不可读则 raise**(同 M-27 fail-closed)。点分模块目标(如 serper)原样不动。`harnessx/` **零改动** |
| 端到端验证 | 真实 V1 配置:修前注册表 `[Bash, Browser, Read, WebFetch, WebSearch]`,修后 **`+python_eval`**,无其他增减 |
| 测试 | 专项 `experiments/variant_pool/tests/test_tool_targets.py`(13 项)。**刻意打真 vendored 解析器/加载器而非断言字符串形状** —— 若只断言形状,日后有人把两斜杠"修正"回 RFC 三斜杠,测试仍绿而 bug 悄悄回归。含一项 `test_the_rfc_spelling_is_the_one_that_breaks`,当 vendored 侧修好时会主动失败以提示简化 |
| 威胁章义务 | Ch7 须记:**M-27 与 M-31 是同一现象的两个实例** ⇒ 「自演化系统需要产物**消费**审计」不是个案而是系统性结论;且**审计必须逐条投递路径做**(修好提示词那条不代表工具那条也好了——这正是本次踩的坑) |
| 裁决层 | 用户令「把这些错误全改掉然后设计专项测试」(Aug-02);全量测试 937 绿 |

## M-32 演化 processor 的投递失败 + 判据由「文件存在」升级为「真能实例化」(Aug-02 新增)

> **三条投递路径,同一现象(M-27 / M-31 / M-32),按可发现性排序**:
> 提示词 898 条 WARNING → 工具 466 条 WARNING → **processor 零日志**。
> 引用「产物消费审计」结论时**必须三条并列**,只提其一会低估问题的系统性。

| 项 | 内容 |
|---|---|
| 论文原设 | **未指定** |
| 🔴 根因(两个,不是一个) | ①**路径**:`builder._parse_file_target` 同样朴素截断 ⇒ Windows 下 `file:///D:\x` → `D:\D:\x`。②**版本漂移**:配置传了被引类版本不接受的 kwarg(s1k8b103 R10/V4 引 C-R6-01 版 `CommitNudgeProcessor` 却传 `nudge=` ⇒ `TypeError`)。**文件完好,仅实例化失败** |
| 🔴 为何最难发现 | `harnessx.core.harness._instantiate_proc` 是 `try: … except Exception: return None`,**零日志**。变体遂以原版处理器栈运行,而配置声称有演化 processor。408,880 行日志中**匹配行数为 0** |
| 实测范围 | s1k8b103:**19 个 active_pool 配置**受影响;干净对照 R0/V0 声明 8 实例化 8,而 R10/V4 声明 9 **只实例化 8**,且实例化集合与原版基线**逐个相同**。s2k8b50 / b_smoke:**active 池未受影响**(仅候选) |
| 我方做法 | `_normalise_artefact_node` 递归处理 `_target_` 与 `template_path`(builder 自身也递归实例化嵌套 spec,只补顶层会留缝);`_target_` 改写为 `file://<绝对路径>::<类名>`;**并以 `_assert_processor_instantiates` 用运行期同一调用真正实例化一次,失败即 raise** |
| 判据升级(重要) | 第一版判据是「文件可读」——**它抓不到版本漂移**(文件完好)。现判据 = **「这个 processor 到底能不能装出来」**,一并覆盖两个成因。这是本条相对 M-27/M-31 的方法学增量 |
| 🔴 对冻结池的后果(**发臂前必须裁**) | s1k8b103 终池 8 变体在新判据下:**V0/V1/V6/V7 可加载**(V6/V7 各拿回 `CommitNudgeProcessor`+`StepCountdownProcessor`),**V2/V3/V4/V5 fail-closed** ⇒ **4/8 不可用**。这四个变体在原跑中一直以原版处理器栈运行 |
| 测试 | 专项 `tests/test_processor_targets.py`(10 项),含 `test_kwargs_the_class_does_not_accept_fail_closed`(版本漂移)与 `test_nested_targets_are_reached`(递归)。**三条路径三个测试文件**,刻意不合并——本批教训正是「修好一条不代表另一条也好」 |
| 裁决层 | 用户令「再次确认没有其他问题」→ 全等级+全尾部审计发现(Aug-02);全量 **947 绿** |

## M-33 合成器从参数化记忆补全缺失子任务结果(`--decomp-synth-guard`,Aug-02 新增)

> **偏差方向对本论文不利,必须主动披露**:该失效**只会虚高「分解」这一侧**——
> 一个凭记忆回想出来的答案若恰好正确,会被记为管线成功。
> 也就是说它**朝着我们自己的假设方向注水**,是最坏的一种偏差走向。

| 项 | 内容 |
|---|---|
| 论文原设 | **未指定**。论文未描述子任务失败时合成步骤的行为 |
| 现象 | 上游子任务未产出所需信息时,合成器不声明缺失,而是用模型自身先验补齐并给出自信答案 |
| 实证 | `b_smoke` 任务 `20194330`:browse 子任务什么都没带回,合成输出为 *"…Based on the known content from the Game Grumps episode \"Sonic '06: Oh No - PART 1,\" the phrase shown on screen…"* |
| 频率(**下界**) | 真走分解合成的 **20 次尝试中 1 次**带自报编造标记,且该次 `passed=False`(本次冒烟未因此虚高)。检测基于 `"Based on the known"` 一类**自报措辞**,**默默编造抓不到**,故 1/20 是下界,不是估计值 |
| 为何原提示词不够 | 模板已写 `"Using only the information above"`,**实测挡不住** |
| 我方做法 | `--decomp-synth-guard {off, strict}`,**默认 off**。`strict` 在合成提示词的**最终答案指令之前**插入约束:缺失须点名子任务 id、禁止以先验补齐、必要时声明无法获得。`off` 走原模板**逐字节等同** |
| fail-closed | 插入锚点为 `_SYNTH_FINAL_INSTRUCTION`;模板一旦改写致锚点消失,`strict` **直接 raise 而非静默失效**——静默失效会让 manifest 声称一份从未生效的保护,正是 M-27/M-31/M-32 同一形状 |
| 记录面 | `--decomp-eval` 在建 lock **之前** return,故 lock 不覆盖此模式;记录写入 **`decomp_manifest.json` 的 `decomp_synth_guard`** 字段——该模式唯一的溯源面 |
| 读数影响 | 开启后,原本"蒙对"的尝试将转为失败,**分解侧的绝对分数会下降**。这是修正而非退步:下降部分本就不是管线挣来的 |
| 测试 | 专项 `tests/test_synth_guard.py`(15 项),含 `test_default_is_byte_identical_to_the_stock_prompt`(最要紧的一条)、`test_guard_fails_closed_if_the_template_stops_carrying_its_anchor`、以及锁住"even when it turns out to be correct"措辞的一条 |
| 裁决层 | 用户令「加入并且记录」(Aug-02);全量 **962 绿** |

## M-34 簇数是池规模的硬上限,且 ε 从未接线(`--cluster-source` / `--epsilon`,Aug-02 新增)

> **⚠️ 本条不是"发现论文未定义 cluster"——那是 M-02 早已登记的**,
> M-02 亦已把「对 level / failure-mode / domain 分别运行」列为待补消融。
> **本条的增量只有三点**:①`cluster` 的选择在**结构上**限死池规模
> (每簇 argmax ⇒ 有效池 = min(K, 簇数)),这一后果 M-02 未指出;
> ②在 s1k8b103 上把它**量出来了**;③第二条独立通道(测量锁定 / ε 从未接线)。
> 引用时不得把 ① 之前的部分说成新发现。

| 项 | 内容 |
|---|---|
| 论文原设 | §4.5 p.11 逐字:*"routing each task to the variant with the highest estimated success rate on that task's **cluster**"* —— 无聚类函数定义(**已由 M-02 登记**)。`router.cluster_of` 的 `NotImplementedError` 亦写明「the paper publishes no clustering algorithm」 |
| 相对 M-02 的增量 | M-02 把 level 记为「可审计代理」并要求做消融,**但未指出代理的选择会限死池的可用规模**。3 簇 ⇒ K=8 中 5 个变体在算术上注定闲置,与闸门、演化策略均无关 |
| 🔴 结构性后果 | 每簇 argmax ⇒ 最多 `min(K, 簇数)` 个变体能拿到任务。我方重建取 `gaia_level` ⇒ **3 簇** ⇒ **K=8 的池子里 5 个变体在算术上注定闲置** |
| 实测(s1k8b103) | 逐轮按 GAIA level 交叉制表:R8 = `V0:{L1:39} V3:{L2:52} V5:{L3:12}`,R15 = `V0:{L1:39} V7:{L2:52} V6:{L3:12}`。**划分严格等于 level**。负载基尼 R4 **0.22** → R14 **0.78**;终局 `[52,39,12,0,0,0,0,0]` |
| 第二条通道:测量锁定 | 未测量的 `(变体, 簇)` 单元取拉普拉斯先验 0.5,**永远输给已测量的**。变体要被测须先赢,要赢须先被测 ⇒ 早期落败即永久冻结。`Router.explore` 的 docstring 原话:*"stop a variant that lost early from being frozen out by argmax forever"* —— **该开关早已实现,`epsilon` 从未接到 CLI,两次正式跑均为 0.0** |
| 离线重放(真实录得数据) | 在 s1k8b103 的逐轮 `active_pool_measurements` 上重放路由。**两个修法单独无效或有害,只有合用才成立**:难度3簇+无探索 基尼 0.72 / 3 个有负载;**能力11簇+无探索 0.82 / 2 个(更差)**;难度3簇+ε=0.1 0.66 / 6.9 个;能力11簇+ε=0.1 0.76 / 6.8 个;能力+ε=0.2+偏置 **0.44 / 7.8 个** |
| 我方做法 | `--cluster-source {gaia_level, capability}` 默认 gaia_level(**与旗标前硬编码逐字节等同**);`--cluster-map` 冻结表;`--cluster-min-size` 默认 8(小簇按类型集合 Jaccard 并入最相似大簇,不设"杂项"桶);`--epsilon` 默认 0.0 接到既有 `Router.explore` |
| 标签来源与防泄漏 | 标签 = 该任务 D1-lite 子任务类型的**集合**,由分解器**仅从题面**算出、解题前冻结成文件并记 sha256。**主导类型不可用**——GAIA 几乎每题都以 search 主导,10 题探针下主导类型只分出 **1 组** |
| 实测分组 | 103/103 标注成功,原始 **11 簇**(30/16/16/13/10/7/5/3/1/1/1);min-size=8 合并 6 个小簇后 **5 簇**(38/25/16/14/10,最小 10 题) |
| lock 诚实性(**本条的关键**) | `cluster_source` 原**硬编码**于两处(`comparison.json` 与 `Hyperparams`)。若只加旗标而不修,lock 会声称按难度分簇而运行时按能力分簇 —— **正是 M-27/M-31/M-32 的同一形状**。现两处均读旗标,并以 provenance 记录**分组表 sha256 + 合并阈值 + 实得簇数**(冻结表在仓外,无 sha 则"5 簇"不可核验) |
| 代价须并报 | ε 靠随机路由买测量,**准确率代价正比于 ε**,而离线重放**看不到这一侧**。ε 应取"够买到测量的最小值",不是让有负载变体数最大化的值 |
| 测试 | `tests/test_cluster_source.py`(20 项),含 `test_default_reproduces_the_previous_hardcoded_partition`、`test_default_partition_has_exactly_three_clusters_which_is_the_ceiling`(把发现钉死)、`test_partial_coverage_fails_closed_rather_than_cold_starting` |
| 裁决层 | 用户令「可以写掉」(Aug-02);全量 **982 绿** |

## M-35 评估路径并发化,并对 `ledger` 路由 fail-closed(`--decomp-concurrency`,Aug-02 新增)

> 纯工程改动,**不改变任何测量语义**。列在此处是因为它引入了一个
> **"某些配置组合被拒绝执行"** 的新约束,而该约束的理由是可复现性,须留档。

| 项 | 内容 |
|---|---|
| 论文原设 | **不适用**(评估基建,非机制) |
| 问题 | `_run_decomp_eval` 完全串行(`for task … for attempt … await`),而演化循环以 `--concurrency 10` 打满端点。**一条评估臂只用了约十分之一的吞吐**。实测端点上限约 5 rollout/分钟,故 A1 一条臂 3.9h 中绝大部分是空等 |
| 我方做法 | `--decomp-concurrency` 默认 **1**(与旗标前路径一致:逐任务运行并逐任务落盘,JSONL 流式写出不变)。>1 时任务级并发,**同一任务的多次 attempt 仍串行**——它们是同一任务的重复测量,管线在其间写信用,重叠会改变第二次尝试所见 |
| 🔴 对 `ledger` 路由 fail-closed | `SubtaskRouter.route` 在 `ledger` 档读 `TypeCreditLedger.rate()`,而该账本正被并发任务写入 ⇒ **路由取决于哪些 rollout 先完成**,该臂无法由自己的冻结输入复现。故 `--decomp-concurrency > 1` 与 `--decomp-routing ledger` 组合**直接 SystemExit** |
| 两个安全档的依据 | `single` = `task_level_choice` 的恒等函数;`round_robin` = `crc32(task_id) + attempt + subtask_index`,**无共享状态、无 RNG**。二者均为纯函数,测试中逐一验证(拒绝规则正建立在这一点上) |
| 顺序确定性 | 累加与落盘从协程中**移出**,统一在 `gather` 之后按 task_id 排序执行 ⇒ 同一批测量在任何并发度下产出**顺序相同**的 artefact。`asyncio.gather` 按参数序返回而非完成序,故排序天然成立 |
| 记录面 | `decomp_manifest.json` 的 `decomp_concurrency` 字段(该模式不建 lock,同 M-33) |
| 预期收益 | 端点上限约 5 rollout/分钟 ⇒ A1 从 **3.9h → 约 41 分钟**。注意**不是 10 倍**:天花板是端点而非本地并发 |
| 测试 | `tests/test_decomp_concurrency.py`(12 项),含 `test_ledger_routing_does_change_when_other_tasks_write`(证明拒绝有必要)与 `test_ledger_at_concurrency_one_is_not_rejected_for_that_reason` |
| 裁决层 | 用户令「写」(Aug-02);全量 **994 绿** |

## M-36 逐子任务信用信号(`--decomp-credit`,Aug-02 新增)

> **这条直接决定 RQ2 有没有答案**:B2(账本路由)是 headline 臂,
> **B2 − B1 就是 RQ2 的结果**。B2 靠这本账决定"哪类活交给谁";
> 账分不出能力,headline 对比就没有机制支撑。
> 因此本条**不是可选优化,是 B2 能否成立的前提**。

| 项 | 内容 |
|---|---|
| 论文原设 | **未指定**。论文未描述子任务级信用如何获得(GAIA 只标注最终答案) |
| 🔴 现状缺陷 | `record(pairs=used, passed=task_passed)`:整道题的成败被记进链上**每一个**去重后的 (变体×类型) 格子,平均 **4.30 格/链**。一道题做对,搜索的、算数的、验证的**同等记功** ⇒ 该表测的是「参与过多少道做对的题」,**不是「擅长哪类活」** |
| 后果 | B2 读这张表做分工,**分工依据本身是糊的**;`B2 − B1` 因此测不到它声称要测的东西 |
| 我方做法 | `--decomp-credit {task, subtask_convergence}`,**默认 task 字节等同**。`subtask_convergence` 改为**每个已执行子任务记一次观测**,成败 = 该子任务是否在**自己的步数预算内**完成(`steps < subtask_max_steps`) |
| 为何选"是否收敛" | 免费(步数本就在记)、客观(二值,无需裁判)、**独立**(不摊给链上其他人),且正对着 C-1(**88.7% 的失败是「没做完」**);实测撞顶率 22%,信号密度足够 |
| 🔴 必须声明的局限 | **它测「做完了」不测「做对了」**。一个提前结束但答错的子任务会被记成成功。这是**刻意的取舍**(免费换粗糙),须随该臂结果一并声明 |
| 更精确的替代(future work) | ①逐子任务模型裁判(每子任务多一次调用);②换变体重跑同一子任务做反事实对比(rollout 成本翻倍)。二者均贵数倍,本论文预算内不可行 |
| 重复格子的语义差异 | `task` 档对 `used` 去重 ⇒ 一条链里出现两次的 (变体×类型) 只记一次;`subtask_convergence` **记两次** —— 该档下两次执行就是两次测量,不去重才正确 |
| 记录面 | `decomp_manifest.json` 的 `decomp_credit`(该模式不建 lock,同 M-33/M-35) |
| 与 M-35 的交互 | `ledger` 路由在并发 >1 时被拒(M-35),与本条无关:换信用信号不改变"读账本时账本正被写"这一竞态 |
| 测试 | `tests/test_decomp_credit.py`(11 项)。含两条**把缺陷本身钉死**的测试:`test_task_credit_books_one_outcome_against_every_pair_on_the_chain`(三个子任务全部撞顶、任务仍通过 ⇒ 三格全记功)与 `test_task_credit_cannot_tell_a_capped_subtask_from_a_clean_one`(全撞顶与全干净产出**完全相同**的矩阵) |
| 裁决层 | 用户指出「B2 不是我们实验的主要测试对象吗」→ 我方原将"降级 B2"与"换信号"并列为等价选项,**该并列是错的**;用户令「可以」(Aug-02);全量 **1005 绿** |

## M-37 meta-agent 自测路径上的产物投递失败(Aug-03 新增,**未修复,已裁定接受**)

> **与 M-27/M-31 同因不同果,严重性低一档,不可混为一谈**:
> 那两条污染的是**测量**(变体声称有演化产物却在跑原版 ⇒ 整跑作废);
> 本条污染的是 **meta-agent 对自己候选的自测**,**闸门与 active 池的测量不受影响**。

| 项 | 内容 |
|---|---|
| 论文原设 | **不适用**(候选生成期的自测不是论文机制) |
| 现象 | meta-agent 生成候选时会拿刚写好的配置自试跑。该次加载由 vendored `Harness` 直接进行,**不经 `_prepare_round_config`**,故 M-27/M-31 的 `file://` 截断缺陷在此路径上依然存在 |
| 实证(e_pervar2) | 三条警告,全部位于 `R1/V0/**pipeline/candidates/**C-R1-0{2,3}/`:①`processor crashed: SystemPromptProcessor … [Errno 22] '/D:/…/templates/gaia…'`(M-27 形状);②③`tool_registry.custom: failed to load 'file:///D:\…\retry_01\tools\{pdf_text,html_extract}_tool.py' … 'D:\D:\PycharmProj\…'`(M-31 形状,双盘符) |
| 🔴 作用域(**必须随本条引用**) | 截至 R1 为止,**零条**出现在 `candidate_gate/` 或 `active_pool/` 路径下。闸门评估经 `_run_evaluation → _run_config_evaluation → _prepare_round_config`,产物路径在那里被改写,**我们报告的测量因此是干净的**。⚠️ 该核验是**跑到 R1 的快照,不是终局**;收官时必须按作用域重新计数,若出现在 gate/active 路径下则结论翻转 |
| 后果 | meta-agent 在**缺少自己所写工具**的条件下评估自己的候选 ⇒ 自我反馈失真 ⇒ 可能丢弃好候选或保留坏候选。**候选质量正是 CH3 所测对象**,故这是一个真实 confound,不是纯噪声 |
| 为何不修(用户裁定 Aug-03「那就不改」) | ①加载发生在 **vendored `harnessx/`** 内,受零改动约束;②我方层唯一可行的修法是**改 meta-agent 提示词**令其写两斜杠形式 —— 但那等于改变演化器的输入,引入新变量,与本轮的单因子设计冲突;③两条臂承受同一缺陷,**是常量**,对照仍成立 |
| 威胁章表述 | 「候选生成期的自测在缺少候选自身工具的条件下进行,故 meta-agent 的自我评估被系统性削弱;所有报告的测量取自闸门与 active 池路径,该路径的产物投递经校验。两条臂条件相同。」 |
| 未来工作 | 提示词层修法(令 meta-agent 写 `file://<abs>` 两斜杠形式)是干净且低成本的,但必须作为**独立变量**在单独一轮中引入,不可与本轮混合 |
| 裁决层 | 用户令「那就不改」(Aug-03);e_pervar2 继续跑 |

## M-38 向 Evolver 声明 `file:` 目标的拼写(M-37 的修复,Aug-03 新增)

| 项 | 内容 |
|---|---|
| 论文原设 | **不适用**(平台契约,非论文机制) |
| 起因 | M-37:Evolver 自测路径上产物静默不加载。诊断后确认两件事:①**它看不见**——`harnessx/core/harness.py:536` 捕获异常仅写日志并 `return registry`,meta-agent 观察流中无任何信号,故不可能自我纠正;②**从没人告诉过它格式**——提示词中零字提及,我方一直在下游默默改写 |
| 机制严重性(比 M-37 初判更高) | 同一份 brief 要求**加工具的候选必须提供 Level-2 往返证据**(观察到工具输出完整进入下一条模型消息)。**工具没注册就永远拿不到该观察** ⇒ 论文四杠杆(提示词/工具/配置/processor)中的**工具杠杆近乎结构性失效**;而论文自身 GAIA 分析中最大单项改进恰来自一次工具编辑(WikiTextFetch) |
| 我方做法 | 新增 `_FILE_TARGET_SPELLING_BRIEF`,注入 `REPO_MANIFEST_SCHEMA_BRIEF` **与** `PAPER_MANIFEST_SCHEMA_BRIEF`。规定 `file://<绝对路径>::<符号>`,**`file:` 后正好两条斜杠**,并说明为何看不见(静默、无可观察报错、导致拿不到 L2 证据) |
| 是否偏离论文提示词 | **否**。两份 brief 均为**我方文本**(paper 档是 Table 9 schema 的注入适配,非 App B.1 逐字提示词),且二者本就载有多条源自实跑事故的运行期契约(`runs/paper1` 四次、`runs/smoke_calib6`、`runs/a1pilot2`)。本条属同类 |
| 单元验证 | `tests/test_file_target_brief.py`(13 项)。**最要紧的是验证 brief 说的是真话**:复刻 vendored 加载器的固定截断,证明所教形式截断后得到干净绝对路径、所禁形式留下前导 `/`。教错格式比不教更糟 |
| **实测验证(live)** | 同一模型(`deepseek-v4-pro`)、真实 brief、强制其输出工具注册块,各 10 次:**无 brief 两斜杠 0/10;有 brief 两斜杠 10/10、三斜杠 0/10** |
| 实测的边界(须并报) | 该探针提示词是孤立的,**未复现"写成三斜杠"这一失败**(裸提示下模型直接写 `path:`,不写 URI)。故它证明的是**"给了指令会照做"**,不是"原本必然写错";后者由实跑日志佐证(`file:///D:\…` 三条) |
| 遗留 | 加载缺陷本身仍在 vendored 代码内(零改动约束),本条只是让 Evolver 不去踩它。若 Evolver 某轮仍写错,M-37 的现象会复现,收官审计须按路径重新计数 |
| 裁决层 | 用户令「重开,改完测试一下这个功能,然后再跑」(Aug-03);全量 **1018 绿** |

## M-39 分解臂的预算按构造配平(`--decomp-budget`,Aug-03 新增)

> **本条修的是我自己在 CH4 里写下的一个假声明**:章节 §4.6.1 称
> 「预算由构造配平」,而当时**代码并不支持** —— 每个子任务各拿整任务全额。

| 项 | 内容 |
|---|---|
| 论文原设 | **不适用**(分解层非论文机制) |
| 缺陷 | 每个子任务继承整任务步数上限 ⇒ 分解攻击花 `子任务数 × 上限`,对整任务的 `上限`。冒烟实测 **2.2×**。该条件下**正面结果可被"你多花了两倍算力"驳掉,阴性结果不可解释** |
| 为何固定上限不能替代 | 冻结的 103 题计划里子任务数 **1–12**(均值 4.38,中位数 4)。固定上限 4 仍有 **25%** 的题总量超过整任务预算,上限 5 有 **41%**。**除法必须逐题做,因为计划长度是逐题的** |
| 我方做法 | `--decomp-budget {per_subtask, shared}`,**默认 per_subtask 字节等同**。`shared` 把上限给**整条链**并逐题均分:每子任务 `max(1, 上限 // 子任务数)`。上限只能在**计划长度已知之后**算出,故它是 `run_attempt` 内的运行期量,不是构造参数 |
| 下限保护 | 计划长度超过预算时 floor 会给 0 步、链根本跑不动;取 `max(1, …)`。**这是唯一允许链略微超出预算的情形**,须在报告中说明 |
| 与 M-36 的耦合 | 收敛判据改用**本次生效的上限**比较,而非整任务上限。否则 `shared` 档下子任务拿 4 步却按 20 步判定,**永远算收敛**,M-36 的信号被静默废掉。专项测试钉死 |
| 兜底路径不分割 | 分解失败退化为一次整任务 rollout(即 A1 的等价物),保持整任务全额。分割它等于因**规划器失败**而惩罚该题,而非因它被分解 |
| 读数影响 | `A1 − B0` 自此是**构造上的等预算对照**,不再需要 A2 臂;"多给预算会怎样"由已有数据回答:`budget_exhaustion_rate = 0.194` |
| 测试 | `tests/test_decomp_budget.py`(25 项)。含把缺陷本身钉死的两条(默认档 5 子任务花 5 倍预算)、逐长度的除法表、`shared` 档总量恒 ≤ 整任务预算、下限保护、以及收敛判据对齐 |
| 裁决层 | 夜间自查发现 CH4 §4.6.1 与代码不符(Aug-03);全量 **1043 绿** |

### M-38 补记(Aug-03):实跑验证通过,且影响面比原述**宽得多**

**实跑验证(e_pervar3 R2,首个写出产物的候选)**:

```
C-R2-04 config 第 56 行:
  _target_: file://D:\PycharmProj\HarnessX\…\processors\search_budget_guard.py::SearchBudgetGuardProcessor
                  ^^ 两斜杠,符合 brief
运行期 _as_local_path 实测解析 → 文件存在 = True
```

三斜杠 0 次。**M-38 的 brief 在真实演化循环中被遵守。**

**影响面修正(此前说窄了)**。原表述聚焦"工具杠杆结构性失效"。实测历史:

| 跑 | 候选 config | 含 `file:` 目标 | 目标种类 |
|---|---|---|---|
| s1k8b103 | 34 | **30(88%)** | processors 36 / templates 8 / **tools 仅 4** |
| s2k8b50 | 31 | 15(48%) | processors 13 / tools 3 |

⇒ **受影响的是绝大多数候选,主体是 processor 与模板,不是工具。**
故正确表述为:**在 M-38 之前,约 88% 的候选写出的产物目标在其自身自测中静默加载失败**;
"工具杠杆失效"只是其中一个子集(且是最小的那个)。

引用本条时用这个表述,不要只说工具。

### M-38 终验(Aug-03):工具路径上的前后对照,`__pycache__` 为证

**同一目录形状、同一类产物、相反结果**:

| | 目标 | 结果 |
|---|---|---|
| e_pervar2(修复前) | `file:///D:\…\C-R1-02\retry_01\tools\pdf_text_tool.py` | **三斜杠** → `D:\D:\…` → 加载失败 |
| e_pervar3(修复后) | `file://D:\…\C-R2-03\retry_01\tools\fetch_and_extract.py::fetch_and_extract` | **两斜杠** → 解析成功 |

修复后该目标经运行期 `_as_local_path` 实测:**文件存在 = True**,
且 `tools/` 下**存在 `__pycache__`** —— Python 仅在**模块真被 import** 时生成该目录,
故这是"工具确实被 meta-agent 自测加载"的**直接物证**,而非仅"路径看起来对"。

⇒ **M-37 所述的失效通道已闭合**:演化器现在能在自测中真正装载自己所写的工具,
因而具备产出闸门所要求的 Level-2 往返证据的条件。

**验证层级(四层,逐层加强)**:
①单元 13 项(验 brief 说真话)→ ②孤立探针 10/10 →
③实跑 processor 目标两斜杠 → ④**实跑工具目标两斜杠 + `__pycache__` 物证**。

### 🔴 M-38 严重性更正(Aug-03 晨):停跑重发的理由**不成立**

**我当时的论证**(用以升级 M-37 并停掉跑了 3h 的 e_pervar2):
工具装不上 ⇒ 演化器永远拿不到 Level-2 往返证据 ⇒ **工具杠杆结构性失效**。

**该论证是错的。M-22 早已解决这一点。**

`--l2-cert auto`(默认档,本跑正在用)**从候选的【闸门评测轨迹】取证**,
不从演化器自测取证:tools 桶取新工具在评测中的真实输出、过真序列化器判 stage 4;
processor 桶 v2 用 replay-execution 证据;**演化器亲笔申报优先采用,缺失不致命**。

而**闸门评测走 `_prepare_round_config`** —— 路径在那里被修好。
**工具在闸门路径上装得上、证据拿得到。**

**实跑佐证**(e_pervar3 R2,Critic 对 C-R2-03 的裁定原文):
> *"No Level-2 round-trip evidence declared for processor components, but in repo
> manifest mode **the deterministic gate certifies this from real trajectories**
> (deviation M-22); recorded as **strategy concern rather than rejection**"*

**修正后的 M-38 严重性**:

| | 原述 | 实际 |
|---|---|---|
| 后果 | 工具杠杆结构性失效 | **演化器自我评估失真**(= M-37 的原始表述) |
| 覆盖面 | — | 约 **88%** 候选的自测路径(此项不变,已实测) |
| 是否值得停跑 | 是 | **否** —— 我原本已判 M-37"不值得停跑",该判断才是对的 |

**M-38 的修复本身仍然正确且已四层验证通过**;错的是升级它的论证。
引用 M-38 时**不得再使用"工具杠杆结构性失效"这一表述**。

## M-40 resume 在「刚 fork 的变体」上丢失配置指针(Aug-03 新增,**未改代码,已数据补救**)

> **这不是本次被杀造成的损坏,是 resume 路径的通用缺口** ——
> **任何在 fork 之后立刻 resume 都会命中**。

| 项 | 内容 |
|---|---|
| 论文原设 | **不适用**(复现基建) |
| 现象 | 变体在某轮**结算末尾** fork 诞生,其 `active_pool/<vid>/config.yaml` 快照要到**下一轮首次测量**才写出。`resume._latest_snapshot_round` 只扫描 `<= 末结算轮`,因而找不到 |
| 处置缺陷 | `resume.py:726` 返回一个**指向不存在文件**的路径,provenance 标 `unresolved_missing_snapshot`,**仅警告不阻断**。运行随后在测量该变体时读该文件而崩 |
| 实例 | e_pervar3:V1 于 R2 结算末尾 fork(持 **14 题**),运行在 R3 中途被外部杀死。resume 警告 `variant V1: no active-pool config snapshot found on disk` 后继续,若不干预将在 R3 测 V1 时崩溃 |
| 我方处置(**数据补救,非代码修改**) | 将 V1 的来源候选 `C-R2-01` 的**原始** config 复制到 `R2/active_pool/V1/config.yaml`,并在同目录留 `RESTORED.md` 说明来历 |
| 为何该补救不是伪造 | fork 时 `child.config_path = Path(candidate.config_path)`,**候选的配置就是 V1 的配置**;且该候选两次尝试(`output_dir` / `retry_01`)**逐位相同**(sha `a914cec4…`),无歧义。补的是**指针**不是**测量值** |
| 刻意不用哪一份 | **不用** `candidate_gate/C-R2-01/config.yaml`(sha `b6fa9fe3…`)——那是 `_prepare_round_config` **预处理后**的配置,写回去等于让预处理跑两遍 |
| 为何不改代码 | 本项目纪律:**运行期间冻结 runner 代码**,否则 auto-resume 会把新代码载入一个 lock 记录着旧 SHA 的跑(同跑版本漂移)。故代码修复推迟到本跑结束 |
| 验证 | 补写后再次 resume,`[resume] continuing … from R3` **不再出现 V1 警告**,配置解析干净 |
| 待办(代码层) | `resume.py` 应在无快照时,从 `pool_state.selected_candidate_ids` + `forked` 反解该变体的来源候选配置路径,而非返回一个已知不存在的路径。**当前实现"警告后带着坏指针继续"是最差的一种**——既不 fail-closed 也不自愈 |
| 裁决层 | 夜间自主处置(Aug-03);运行已恢复 |

## M-41 `file:` 装载器侧鲁棒化 + 实例化失败不再静默(Aug-04 新增,分支 `feat/observation-channel`)

| 项 | 内容 |
|---|---|
| 论文原设 | **不适用**(平台契约,非论文机制) |
| 起因 | M-37/M-38 只修了**提示词侧**(教 Evolver 正确拼写),装载缺陷本体仍在 vendored 代码内。s1k8b103 法证(`novelty/10-CHANNEL-AUDIT.md` §1):**7 条上线编辑仅 2 条生效**,5 条死于 `file:///D:/…` 剥 7 字符剩 `/D:/…`;`harness.py` 裸 `except: return None` 零日志吞掉;模板同病 ⇒ **890 个 rollout 跑在空系统提示词上**(=M-27 的"空 890")。e_pervar3 因 Evolver 恰好写了点分模块路径而幸免——纯属运气,非机制保障 |
| 我方做法 | ① `builder.py` 新增 `_resolve_target_path`:`file:///D:/`、`file://D:/`(M-38 brief 教的两斜杠形)、裸盘符路径、POSIX `file:///home/…` **全部可解析**;`::` 作判据,点分模块路径不受影响。② `harness.py` `_instantiate_proc` 保留"失败返回 None 不炸整跑",但**ERROR 级记录完整 `_target_` 与异常**;消费端记录被丢弃的组件;工具注册同法 WARNING→ERROR。③ `template.py` 模板路径复用同一解析器 |
| 与 M-38 的关系 | **互补两层**:M-38=预防(教拼写,防新错),本条=鲁棒(任何拼写都能载,旧错也能活)。M-38 遗留栏预言的"若 Evolver 某轮仍写错,M-37 现象复现"**自此关闭** |
| ⚠️ 政策偏离(明示) | 本条**打破 M-38 遗留栏所记的"vendored 代码零改动"约束**。依据:用户 Aug-04 指令「我们需要一个 robust 的框架来 evolve」「可以把这份也改进,放进新的 branch 里」;隔离于 `feat/observation-channel` 分支(worktree `HarnessX-channel`),主线与活跑不受影响,M-40 的"运行期冻结 runner"纪律仍被遵守 |
| 读数影响 | 纯前向:对已落盘 run 零影响。前向效果=消除 s1k8b103 型静默蒸发;比较跨越本分支前后的 run 时,**"编辑落地率"是新的混杂变量,必须并报** |
| 测试 | `tests/unit/test_builder.py`(5 种拼写参数化 + caplog 断言 ERROR);`experiments/variant_pool/tests/test_processor_targets.py` 由"断言缺陷存在"改为"断言修复生效"(史料注释保留);`test_custom_tool_registry.py` 2 项 WARNING→ERROR。变体池套件 **1026 绿**;`tests/unit` **864 绿**、8 项既有失败(gbk locale/沙箱)与 HEAD 基线逐项一致(`git stash` 法证) |
| 裁决层 | 用户令「可以把这份也改进,放进新的 branch 里」(Aug-04) |

## M-42 观测通道拓宽旗标 `--traj-failure-signals`(Aug-04 新增,默认关,实验自变量)

| 项 | 内容 |
|---|---|
| 论文原设 | Digester 的输入=轨迹文本;论文未定义任何失败信号的结构化通道,frontmatter 无失败列 |
| 起因 | 通道审计(`novelty/10-CHANNEL-AUDIT.md` §2-4):Evolver 实为**自写扫描器只读 frontmatter**的编程 agent;正文专属信号 **fetch 652 次 / loop 807 次 / 搜索 437 次到达率 0**;digester 据此把工具失败判成"model_capability…not addressable"并自我关停 7 轮;**R8 自然实验**证明信号一进去它立刻产出对症提案(`SearchUnavailableSwitch`) |
| 我方做法 | 旗标开时,轨迹落盘阶段对**渲染后的正文**计数 5 个精确标记串,四个平铺标量写入 frontmatter 行为层(落在 `Read limit=30` 窗口内):`search_unavailable_count / fetch_error_count / fetch_empty_count / loop_warning_count`。**默认关=frontmatter 逐字节等同**(专项测试钉死) |
| 为何是 frontmatter 而非改工具/提示词 | meta-agent 的扫描器**本来就只读 frontmatter**——信号从它已用的通道进入,零提示词改动、零工具语义改动(搜索失败仍是 `return` 不是 `raise`,不扰动 agent 行为、不污染对照) |
| 无标签泄漏 | 四个计数全部来自运行时工具输出,**零字节来自数据集标注**(呼应"按 GAIA level 分步数"因偷标签被否的裁定) |
| 锁与 resume | 走 `_epsilon_provenance` 模式:`_traj_failure_signals_provenance(args)` 默认返回 `None`(lock 逐字节不变),开启时记 provenance 警告。**未新增 `Hyperparams` 字段,`experiment_lock.py` 未动** |
| 实验设计(待用户明令) | 臂 0=关(论文原样通道) vs 臂 1=开;同床同预算同轮数。预注册预言:臂 1 的 actionability 不再把工具失败判死、旱灾轮数下降、出现 fetch/搜索方向的候选 |
| 已知边界 | 计数按渲染正文做,模型若在自述中逐字回显标记串会被计入——按"该失败模式是否出现"的粗信号使用,不作供源精确计量(docstring 已注明) |
| 测试 | `tests/unit/test_trajectory_frontmatter_v2.py`:开=计数正确(2/1/0/1 用例),关=键不存在、输出与 HEAD 逐字节一致;端到端过 `_write_task_trajectory` 双态 |
| 裁决层 | 用户令「可以把这份也改进,放进新的 branch 里」(Aug-04);**跑臂对比须另行明令** |

## M-43 `--ship-efficacy-gate` 候选评测前效力预检(Aug-05 新增,默认关)

| 项 | 内容 |
|---|---|
| 论文原设 | §4.3 门序 "build or smoke tests (when applicable)"——无任何语义效力检查 |
| 起因 | s1k8b103 七条上线编辑五条运行时 no-op,却**逐关全过**且各花掉整批候选评测 rollout(`novelty/10`/`11`)。官方同思路防线:`apply.py` 校验 + `ShipNotLandedError`(`novelty/11` §5) |
| 我方做法 | Evolver 写完 config 后、任何候选评测 rollout 之前,只读预检四项:①加载+canonicalize;②声明的 processor/自定义工具全部可实例化(**复用真实 prep 路径** `_resolve_artefact_paths`/`_resolve_tool_targets`,即评测前本来就会跑的 fail-closed 检查,提前到花钱之前);③提示词模板解析非空;④声明了桶但 `compute_changeset(parent, candidate)` 为空 ⇒ `efficacy: declared <buckets> but runtime surface identical to parent`。失败候选从 `ranked_for_gate` 掉队,记 `efficacy:` AuditRecord → 走既有账面(`RejectedCandidate` 证据、`producer_or_pipeline_rejected` 计数),**零新产物文件** |
| 边界(须并报) | stock 点分模块 processor 不做实例化断言(与真实 prep 一致);legacy 消融臂未接;仅标量 config 改动的边角以"父比对不可得则跳过"守护,不误杀 |
| 锁与 resume | 旗标默认关=队列/账面/产物/lock 逐字节等同;`_ship_efficacy_provenance` 默认返 None;零 `Hyperparams` 字段 |
| 测试 | `test_ship_efficacy_gate.py` 6 项;变体池 **1032 绿**;`tests/unit` 872 绿(8 项既有环境失败与基线逐项一致) |
| 裁决层 | 用户令「把能抄的抄了」「全抄官方的」(Aug-05) |

## M-44 upstream 同步:main 合并 + web_fetch 挂死加固移植(Aug-05)

| 项 | 内容 |
|---|---|
| 内容 | ①merge `upstream/main`(91466f0):spawn 任务强引用防 GC、`to_markdown` 处理 list 形 processors、ruff 锁版;我方未触这三个文件,零冲突。②`web_fetch.py` 移植官方 feat/aegis 的 +55 行挂死加固:25/30/60s 三层超时、`page.inner_text` 单独 wait_for(官方注释:PDF 页无 `<body>` 永挂,"the observed cause of worker hangs"于其 2026-05-13 GAIA 跑)、二进制/错误响应不再落 Playwright、结构化失败标记。**逐字节等同上游已验证**(`git diff upstream/feat/aegis -- web_fetch.py` 为空) |
| 动机 | fetch 失败是我方实测头号失败模式(29.1–42.4% 尝试受累,`novelty/10` §5);此为地基修复,默认开、两臂共用 |
| M-42 兼容 | 新失败拼写均以 `[fetch failed` 开头 ⇒ `fetch_error_count` 计数器仍命中;每次失败恰发一个标记串,无双计 |
| ⚠️ 事故记录 | web_fetch 的 +55 行**物理落在 `b65ca01`(一个文档提交)里**:并行 coder 的 `git apply --3way` 把改动写进了暂存区,主循环随后 `git add <doc> && git commit` 时将暂存区一并带走。内容已验证无误;分支未推送且树内有活跃 agent,**不重写历史**。教训:**共享 worktree 内每次提交前必查 `git diff --cached`**(已入操作纪律) |
| 政策 | 修改 vendored 面,依据同 M-41(用户「robust 框架」令;独立分支隔离) |
| 测试 | `test_web_fetch_browser_retry.py` 4 项(二进制/错误标记不落浏览器重试) |
| 裁决层 | 用户令「全抄官方的」(Aug-05) |
