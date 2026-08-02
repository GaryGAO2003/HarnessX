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
