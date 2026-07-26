# 复现完成计划(Jul-26 定,用户令"继续复现整个系统")

> **进度(Jul-27)**:Phase A 施工全毕——A1(ad56292,已实战 a1smoke 全绿)/
> A2(a4eeae7,离线验收)/ A3(7fedd88,修订路径首次接线);B2 由 A1+A2 关闭
> (α 门与空 landscape 短路均已真实触发/落地,α 模式感知默认=2ad682e)。
> paper 收敛序列:paper1(承诺墙)→2(钳制+署名墙)→3(词表墙)→4(四修复齐,
> 运行中)。待办:aegis1 三角色全 LLM 冒烟(吸收原 a1smoke2)→ C 阶段随跑 →
> pilot 校准 → 冻结。B1(round-global multi-ship)维持可顺延。

> 目标:关掉五层复现度记分卡(RUN-LOG/总结)里的剩余缺口。当前:结构层 ~90%、
> 实证层 ~75%、**认知层 ~30-40%(只有 Evolver 是真 LLM)**、制度层 ~70%、效果层 0%。
> 完成后进入 pilot 校准 → 冻结 → 正式实验(K=1 vs K=8)。

## Phase A — LLM-AEGIS 三角色(认知层主攻,~2.5-3.5 agent-日)

论文的 Digester/Planner/Critic 均由 LLM 驱动;我方现为确定性近似(审计标
`llm_aegis_reproduction: false`)。逐角色替换,全部走现成 Protocol 缝,
**门与引擎一行不动**(上线权威保持确定性,§4.3 原则)。每角色一个开关
(`--aegis-digester/planner/critic {llm,deterministic}`),默认 deterministic
= 现行为字节稳定;deterministic 臂保留为消融对照(论文加分项)。

- **A1 LLM Digester**(~1 日):meta 模型对每个失败任务产出结构化 digest
  (failure_category / implicated_components / evidence_anchors / 可行动性贡献),
  批式、缓存友好;prompts=对 §4.3 角色描述的 OURS 重建(声明在案)。
  离线测试用脚本化 LLM 桩;一次 live smoke(~¥5-8)。
- **A2 LLM Planner**(~0.5-1 日):从 digests+journal+已上线史构造改造空间,
  产出 K_t 份桶多样化 brief;**空 landscape → 真实短路**(顺手关 EXP-E07)。
- **A3 LLM Critic**(~1 日):组合审计 + ship_ranking + 至多一次修订请求——
  **修订环首次可被真实触发**(补实证层欠账 C3)。

风险与对策:pro 输出结构稳定性(复用 retry 契约模式);digest token 成本
(缓存 + 预算帽);LLM 角色新增方差(deterministic 消融臂在,正式实验可并报)。

## Phase B — Algorithm 1 完备(结构层收尾)

- **B1** round-global K_t 协调器 + 附录 B.1 ranked bucket-disjoint multi-ship,
  作可切换臂(关 EXP-E06;~1 日;**时间紧可顺延到正式实验后**——现行
  first-pass-wins 是声明过的工程裁决)。
- **B2** actionability 短路全语义——由 A1/A2 自然关闭。

## Phase C — 机制实证补票(随跑动完成,各记 RUN-LOG)

- **C1 有机 fork**:A 阶段候选质量抬升后,pilot12 × K_t=2 × 多轮寻机;三跑无果
  则一次 force-gate 制造第二 fork,顺带触发 **retire+孤儿重分配 live**(C2)。
- **C3 修订环 live**:A3 上线即测。
- C4 早停:自然发生时在台账断言。

## Phase D — 校准、冻结、正式实验(顺延至 A 完成后,内容不变)

pilot12 校准(2 run × 3 轮 × pass@2,产出基线带/方差/W0 headroom/依从率/出货率)
→ 冻结 experiment.lock(worker=Flash 已裁 §7.13;环境=yt-dlp 2026.06.09 + POSIX 桥;
AEGIS 模式按 A 阶段验收结果定)→ M0 预算简报重算 → **充值拍板(用户+导师)**
→ pilot 正式跑 → 全床 103 题。

## 预算与时间

- 到冻结点:A 阶段 smokes ~¥20-40 + 校准 ~¥40-60 + LLM 角色每轮增量 ≈+¥3-8
  ⇒ 合计 ≈**¥100-150**,现余额(¥176)覆盖;全床正式跑另需充值。
- agent 速度:A1→A2→A3 串行 2.5-3.5 日;B1 可选 +1 日;⇒ **冻结点距今 ≈3-5 工作日**。

## 完成定义(复现声明的诚实边界)

全部完成后,论文可声明:§4.5 机制全量重建 + AEGIS 四角色 LLM 化(prompts 为
OURS 重建)+ 留白以声明参数填补 + 跨模型迁移(DeepSeek 替 GPT-5.4/Opus 4.6,
§7.7 未测面的首批观察)。**不声明**:与原文逐字节同 prompt 的复刻、原模型复现、
附录 B.1 multi-ship(若 B1 顺延)。
