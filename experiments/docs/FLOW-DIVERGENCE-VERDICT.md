# FLOW-DIVERGENCE-VERDICT.md — 仲裁方 C 双盲对账裁决

> **实验角色**:仲裁方 C。阶段 1 只读 `FLOW-PAPER.md`(A 侧/论文)与 `FLOW-IMPL.md`(B 侧/代码),外加 4 处**定点**源核实(见末表);阶段 1 清单定稿后才打开我方台账(`PAPER-METHODOLOGY-DEVIATIONS.md` M-01..M-22、`PAPER-GAP-AUDIT.md`、`SPEC.md §7.1–7.16`)做阶段 2 对账。未通读任一原始源建立自有全图。
> **定点核实记录(阶段1内,仅裁该点)**:①`grep PASS_COUNT_NOISE_THRESHOLD/noise` → experiment_lock.py:232/236、gate.py:222-224;②`ledger.py:265-267` is_ever_solved 作用域;③(附带)gate.py 注释段。graphify 图属 MAS_Directions 项目、不索引本仓,且违背盲仲裁纪律,未使用。

---

## 一、执行摘要(≤12 行)

1. 阶段 1 共比对 **23 条偏差**,覆盖任务要求的 12 个面。
2. **NEW(未申报新发现)= 3 条**,全部集中在 Ensemble(K>1)的 seesaw 语义与评测因果,**默认 K=1 档不显形**——这正是它们逃过既有台账的原因。
3. **MISDECLARED(台账与事实不符)= 1 条**:GAP-AUDIT §1 把"per-variant seesaw 范围"列为 verbatim,但回退基线实为**全局 ever_solved(跨变体)**,只有测试集 T_k 才是 per-variant(与 NEW-1 同一事实的台账面)。
4. **台账过期 = 3 条**:M-12 / M-18 / M-16 正文的"LLM adapter / selective short-circuit / round-global coordinator 尚未实现/接入"措辞,已被代码(FLOW-IMPL §4.3 的 `--aegis-* llm` 旗、a_t/α 短路、`--target-strategy`)超越;GAP-AUDIT 已口头承认"A1/A2 已实质闭合",但 M-xx 正文未同步勘误。
5. 其余 **~19 条全部 DECLARED**,四分类以 **(b) 留白填补**为主(U1–U13 逐条落地),少量 (a)/(d) 已被 M-04/M-17/M-19/§7.8 精确覆盖。
6. **总体判语:申报完备度高(约 83% 偏差已被 M-xx/§7.x 覆盖,且多数带预注册 ablation)。** 台账的短板不是漏报大偏差,而是 (i) 三行时效滞后、(ii) 一处 verbatim 过度声明掩盖了 Ensemble 回退基线的跨变体耦合。**默认配置复现的是 Global 冒烟档而非论文旗舰 Ensemble——此点被 GAP-AUDIT §0 显著且正确地申报**,是台账质量最强的一处。

**NEW 一句话版**:
- **NEW-1**:seesaw 回退基线是**全局 ever_solved(跨变体)**,而改进基线是 per-variant,二者不对称;§4.5 的"改进一簇不得回退另一簇"只在测试集层面成立,基线层面在 Ensemble 下把变体耦合起来。
- **NEW-2**:R0 是纯基线轮(不进引擎),吃掉一个适应槽 → `num_rounds=15` 实得 **14 个适应轮 vs 论文 15**(Algorithm 1 自 t=0 即适应),off-by-one。
- **NEW-3**:门的回退判定是**跨批次**的(before/ever_solved 来自结算后全量流、after 来自候选 T_k 流),seesaw **决策本身**暴露于 pass@2 抽样方差(区别于 M-08 的报告层掩蔽)。

---

## 二、阶段 1 偏差总表(四分类 + 严重度 + 双侧锚点)

严重度 = 对"复现效果可信度"的影响;⚑=NEW,✔=DECLARED,✖=MISDECLARED,⌛=台账过期。

| # | 面 | 偏差(论文 → 代码) | 类 | 严重度 | PAPER 锚(A) | IMPL 锚(B) | 台账 |
|---|---|---|---|---|---|---|---|
| P1 | 四角色调用 | 四阶段皆"同一 meta-agent LLM 自主选择性调用,无外部路由";代码**默认 Digester/Planner/Critic 全确定性、仅 Evolver 恒 LLM**,且由外部 pipeline 编排短路 | (a) | **顶** | §4.3 p.9;Fig2 p.7 | `--aegis-* deterministic`(§4.3);candidate_pipeline.py:162/1962/2500 | ✔ M-12,M-18 ⌛ |
| P2a | 池上限 | 旗舰是 Ensemble(up to K);**默认 `--pool-k 1` = 单谱系 Global,永不 fork** | (b)+区制 | **顶** | §4.5 p.11;Table5 p.17 | run_variant_pool.py:3748;pool.py:60,226-227 | ✔ M-01,§8.1,GAP§0 |
| P2b | K_t 生产 | Table8 `K_t=4`;**CLI 默认 `--candidates-per-round 1`**(常量兜底 4 但 CLI 胜) | (d) | **顶** | Table8 p.29 | :6116 vs :3578;PAPER_CANDIDATES_PER_ROUND=4 :200 | ✔ M-20 |
| P2c | 协议规模 | "full task set every round, no subsampling";**默认 `--max-tasks 6`**(冒烟档),实跑 6–12 题/3 轮 | (b)+区制 | **顶** | §6.1 p.15;A.2 p.28 | defaults.py:13;GAP§0 | ✔ GAP§0/§2 |
| NEW-1 | 三路裁决/评测口径 | §4.5"per-variant seesaw";代码回退基线 = **全局 ever_solved(任意变体、全史)**,改进基线才是 per-variant cell(不对称) | (a)/(d) | **高**(K>1) | §4.5 p.11;§4.1 p.8 | gate.py:206-209;ledger.py:265-267(定点核实);engine.py:822-824 | ⚑ NEW / ✖ GAP§1 |
| P3 | 账本口径 | 门的 before/ever_solved/routing **只**由结算后全量重评写入;候选 T_k 门评测**永不进账本**;每轮两批真 rollout | (b) | **高** | §4.1 p.8;§6.1 p.15 | :4986(唯一写);engine.py:544;:4875-4972 | ✔ M-07,M-14 |
| P5 | 门关卡数/实义 | 论文 4 关皆实检;代码 **5 关中 stage2/3 恒过、stage4 默认只查申报**(真跑需 `--l2-cert`),实拦截仅 1/4(若)/5 | (a)/(c) | **高** | §4.3 p.10 | gate.py:398-421,403-410,194-210 | ✔ §7.8,§7.11,M-22 |
| P9 | manifest 契约 | Table9 严格契约;**默认 `--manifest-mode repo`**(manifest 可选、journal 适配、paper-only 字段标缺不捏造);加 `target_variant`(OURS) | (a)/(b) | 中高 | Table9 p.35-36 | manifest.py:344-395,392-393 | ✔ M-19,§7.2 |
| P6 | 超参/门 | Table8 "±5% noise threshold";**门未实现**(`noise_threshold=None`),±5% 仅存 `planned_noise_threshold=0.05`(计划注记),抗噪改由 `min_fork` | (a) | 中 | Table8 p.29 | experiment_lock.py:232/236;gate.py:222-224(定点核实) | ✔ M-04 |
| NEW-3 | 评测口径 | 门回退判定跨批次:before/ever_solved(结算后全量流)vs after(候选 T_k 流),同一 task 的前后态来自两次独立 pass@2 采样 → 门决策层方差(非仅报告层) | (b) | 中 | §4.1 p.8 | :4986;engine.py:822-824;gate.py:206-209 | ⚑ NEW(细化 M-07/M-08) |
| P8 | multi-ship | C1 单发↔多发;**默认 `first_wins` 单发**(取 Algorithm 1 读),`bucket_disjoint` 多发为可切换臂 | (d) | 中 | Alg1 L22-23 p.9;AppB.1 p.34 | engine.py:364-417,631-675 | ✔ M-17,§7.14 |
| P7 | K_t 生产 | U7 未定"一会话 vs K_t 会话";代码 = **K_t 个独立 meta 会话 asyncio.gather**,成本×N | (b) | 中 | U7 p.31-32 | candidate_pipeline.py:309-329 | ✔ M-20 |
| P10 | 三路裁决 | U4/U13/C3;`min_fork=(1,1)`(改进≥1 且回退≥1 即 fork);无改进→REJECT;**实现了 FORK**(Algorithm 1 本无) | (b)/(d) | 中 | §4.5 p.11 | gate.py:213-225 | ✔ M-04 |
| P17 | 路由/target | 论文未定每轮选哪个 target;代码 = 每轮**单一全局 target**(`worst_first`) | (b) | 中 | Alg1 L15 p.9 | target.py:87-89;`--target-strategy` | ✔ M-16 ⌛ |
| P12 | fork 继承 | U3 未定;代码 = 子体深拷父 config + **improved 任务从父转移到子**(父失之) | (b) | 中 | U3 p.11 | pool.py:131-201,186-197 | ✔ M-05 |
| P11 | 簇定义 | U2 未定;代码 = 注入 `gaia_level_{level}`(GAIA 难度层,GAIA 专属) | (b) | 中 | U2 p.11 | run_variant_pool.py:3742-3745 | ✔ M-02 |
| P13 | 估计量 | U5 未定;代码 = Laplace `(p+1)/(a+2)`,空 cell 先验 0.5,先聚合后 Laplace,tie=fewest_attempts,ε=0 | (b) | 中低 | U5 p.11 | ledger.py:258-263;router.py:326-348 | ✔ M-03 |
| P15 | 选择性调用 | U8/U9;α 默认 1.0(det)/0.5(llm);二元 a_t 使默认档"仅全解才短路",丢失论文"signal too sparse"半支 | (b)/(a-微) | 中低 | Alg1 L8 p.9 | :306-321;candidate_pipeline.py:438 | ✔ M-12,M-18 ⌛ |
| NEW-2 | 轮循环顺序 | R0 纯基线不进引擎、适应自 R1 起 → `num_rounds=15` 实得 14 适应轮 vs 论文自 t=0 起 15 轮 | (a) | 低 | Alg1 L3 p.9 | run_variant_pool.py:3823-3833 | ⚑ NEW |
| P14 | 冻结/路由 | U6 未定;代码 = `freeze_routing(before_round=round_idx)` 只读严格先前轮,账本为下一轮写 | (b) | 低 | U6 p.22 | router.py:167-197 | ✔ §8.4,M-05 |
| P16 | 修订 | U12 未定;代码 = 至多一次,**只修排序首个**(其余 suppressed),revise→再 review→门 | (b) | 低 | §4.3 p.10 | candidate_pipeline.py:552-659 | ✔ GAP§1 |
| P19 | 超参/种子 | Table8 "3 seeds/cell";代码单次 `--seed 0`,3 seed 只作 `planned_seeds` 锁注记 | (c)/协议 | 低 | Table8 p.29 | `--planned-seeds (0,1,2)`;`--seed 0` | ✔ M-10,§7.6③ |
| P21 | 元模型 | Opus4.6/Sonnet4.6/GPT5.4/Qwen;代码模型为运行时占位实参(实值 DeepSeek) | (b) | 低 | Table8 p.29 | `--meta-model`/`--model` 占位 | ✔ M-15,§7.13 |
| P20 | 范围边界 | 论文 5 床 + §5 co-evolution;本 recipe 仅 GAIA,§5 GRPO 外壳不在此路径 | 范围 | 低 | Table3 p.15;§5 p.12 | recipe/gaia_evolver | ✔ 部分(M-15;范围内建) |

**四分类计数**:(a) 语义 4(P1、P5、P6、NEW-2)+2 混合(NEW-1、P9);(b) 留白填补 11;(c) 表面/结构 2(P5 结构半、P19);(d) 双读裁定 4(P2b、P8、P10、NEW-1)。

---

## 三、NEW 发现详述(本审计的金子)

### NEW-1 —— seesaw 回退基线是全局 ever_solved(跨变体),破坏 §4.5 per-variant 隔离 【高 · Ensemble 专属】

**事实**:门的分类(gate.py:206-209)两条基线**不对称**:
- `improved`:`before_passes==0 and after_passes>=1`,其中 `before` 取**该变体自身**的账本 cell(engine.py:822-824,per-variant)。
- `regressed`:`after_passes==0 and ledger.is_ever_solved(task_id)`,而 `is_ever_solved`(ledger.py:265-267,**定点核实**)docstring 逐字为 *"was this task ever solved, by any variant, in any round?"* —— 即**全局、跨变体、全史、只增**。

后果:某任务 t 现路由给变体 k、在 k 的 cell 里未解,但曾被变体 j 解出。候选(targeting k)若在 t 上失败,即被判 `regressed`(因 j 的历史成功进入全局 `ever_solved`),从而 FORK/REJECT。论文 §4.5 明言 *"improvements to one cluster cannot regress another"*,其隔离靠"候选只在 T_k 上评测"实现——**测试集层面成立**(B 的 T_k 收窄忠实),但**回退基线层面被击穿**:k 继承了"任何变体解过的任何任务"的不回退义务。这实质是论文自身 §4.1(全史 T_t)与 §4.5(per-variant)张力的一次未申报**双读**:实现给回退基线选了 §4.1 全局读、给测试集选了 §4.5 per-variant 读。默认 `--pool-k 1` 下只有 V0,全局=per-variant,**不显形**;故此前所有 K=1 探针都测不到。

**为什么之前漏了**:(i) A 侧是论文纯读,§4.5 与 §4.1 分处两页,A 正确记为 C4 家族张力但未推到"基线跨变体"这一步;(ii) B 侧代码读**已精确记下** `ever_solved` = 任意变体(FLOW-IMPL §2/§7.1),但 B 无论文视角、不知这与 §4.5 冲突;(iii) 台账 GAP-AUDIT §1 把"per-variant seesaw 范围"整体列为 verbatim,一句话把测试集的忠实**外推**到了基线,恰好盖住此缝(见第四节 MISDECLARED)。

**建议动作**:新增 **M-23**(草稿):
> **M-23 seesaw 回退基线的变体作用域** — [PAPER 张力] §4.1 p.8 用全史 T_t 定义 seesaw、§4.5 p.11 又要求 per-variant 隔离,二者对"回退基线是否跨变体"给出不同读法。[事实] `is_ever_solved`(ledger.py:265-267)全局跨变体,改进基线(engine.py:822-824)per-variant,门(gate.py:206-209)因此不对称:一个变体一旦被路由到任意变体解过的任务,即背上不回退义务。[OURS 裁决] 保留全局 ever_solved 作为**更保守的反回退**(对齐 §4.1 字面),但须在 lock 显式声明其为 §4.1/§4.5 双读中的**全局读**,并作 per-variant-baseline 消融。[ablation] K≥2 下比较 {全局 ever_solved, 仅本变体 ever_solved, 本簇 ever_solved} 三种回退基线对 fork 率、池膨胀、final 的影响;报告跨变体误判回退的比例。**并勘误 GAP-AUDIT §1**:"per-variant seesaw 范围"仅对测试集 T_k 成立,回退基线是全局。

### NEW-2 —— R0 纯基线吃掉一个适应轮(off-by-one)【低】

**事实**:R0 走 `paper 且 round_idx==0` 分支(run_variant_pool.py:3823-3833),**不进引擎**,只做全量评测 + 写账本,演化自 R1 起(FLOW-IMPL §0)。论文 Algorithm 1(L3 p.9)`for t=0..T-1`,t=0 时 N3 评测 H_0 → ΔT_0 → Digester 即可产 landscape 并演化。故同为 15 轮预算:论文得 **15 个适应轮**,实现得 **14 个**(R0 + R1..R14)。

**为什么之前漏了**:此偏差是实现"Digester 读先前轮账本"设计(P3)的**必然推论**——实现的 Digester 需要 R0 先播种账本才能在 R1 工作,论文的 Digester 读轮内 ΔT_t 故无需独立基线轮。两侧 FLOW 各自记录了自己的轮 0 语义,但无人跨侧相减得出适应轮差。台账 M-16/M-09 谈 target/held-out,未触及适应轮计数。

**建议动作**:M-16 或新 **M-24** 补一句:
> 轮预算换算:实现 `num_rounds=N` = 1 纯基线轮 R0 + (N−1) 适应轮,对齐论文 T=N 时须设 `num_rounds=N+1` 或在报告中声明适应轮 = N−1。默认 15 → 14 适应轮,须与论文 15 轮对齐时补记。

### NEW-3 —— 门决策跨批次,seesaw 判定本身暴露于 pass@2 方差【中 · 细化 M-07/M-08】

**事实**:P3 已确立"候选 T_k 门评测不进账本、账本只由结算后全量重评写(:4986)"。推论:门判 `improved/regressed` 时,**同一 task 的 before(来自结算后全量流)与 after(来自候选 T_k 流)来自两次独立的 pass@2 采样**。对真实成功率 ~50% 的任务,前一批可能记为 solved、后一批 rollout 恰失败,门即记一次**幻影回退**(反之幻影改进)。这不同于 M-08(pass@2 在**报告层**掩蔽亚阈值退化)——此处是 **seesaw 决策层**的采样噪声,直接改变 APPLY/FORK/REJECT 走向。

**为什么之前漏了**:M-07 只申报"候选诊断与 settled 评分两条数据流、REJECT 不污染 final"——是**报告完整性**口径,未推到"门决策基线取自另一条流故跨批"这一决策口径;M-08 谈 pass@2 但落在 final/peak 报告。二者相邻但都不覆盖门内跨批方差。

**建议动作**:扩写 M-07 尾注(草稿):
> [补] 门的回退/改进基线取自结算后全量流(唯一 `ledger.record`),after 取自候选 T_k 流,故 seesaw 判定跨两次独立 pass@2 采样。须报告:门级幻影回退/改进率(用同 config 重评估计),并在 V2(§9.5 pass@2 独立性)中一并检验门决策而非仅报告的方差敏感性。

---

## 四、MISDECLARED / 台账过期清单

### ✖ MISDECLARED-1 —— GAP-AUDIT §1 "per-variant seesaw 范围 = verbatim" 过度声明
- **证据**:回退基线全局 ever_solved(ledger.py:265-267 定点核实),非 per-variant(见 NEW-1)。verbatim 只对测试集 T_k 成立。
- **勘误措辞(草稿)**:GAP-AUDIT §1 "verbatim" 行改为 —— *"per-variant seesaw **测试集范围** verbatim(候选只在 T_k 评测,§4.5);**回退基线为全局 ever_solved**(§4.1 读),二者不对称,见 M-23。"* 同时 §1 的 "seesaw 全史回退判定(gate.py:194-210 ↔ §4.1 p.8)" 一句**本身正确**(全局 ever_solved 恰是 §4.1 全史读)——保留,但须点明它与"per-variant"是**两个不同 §** 的读,不可并列为无冲突 verbatim。

### ⌛ 台账过期-1 —— M-12 "真实 LLM adapter 尚未接入 live recipe"
- **事实**:FLOW-IMPL §4.3 显示 `--aegis-digester/planner/critic llm` 三旗均已在 build_arg_parser 接线(llm=一次 meta 调用建 landscape/排名/否决/≤1修订);GAP-AUDIT §1 已改口 "全 LLM 模式下经 A1/A2 已实质闭合"。M-xx 正文(2026-07-25 版)滞后于 GAP-AUDIT(07-27)与代码。
- **勘误**:M-12"我方保守决定"栏"真实 LLM ... adapter 尚未接入 live recipe"→ *"LLM adapter 已作 `--aegis-* llm` 旗接线(代码在;**未 live 端到端跑**);状态从'未接入'降级为'code-present, not-live-tested'。"* 不改其 [UNVALIDATED] 结论。

### ⌛ 台账过期-2 —— M-18 "actionability/selective short-circuit 尚未实现"
- **事实**:FLOW-IMPL 候选子图显示 `a_t < alpha` 短路(candidate_pipeline.py:438)、Planner-empty 短路(EB)、K_t=0 短路均已在 pipeline 前置,非"只有 Evolver 无候选才 no-op"。α 双默认(:306-321)亦在。GAP-AUDIT §1 已承认 A1/A2 闭合。
- **勘误**:M-18"问题"栏"当前 ... 只有 Evolver 最终没有有效候选时才 no-op" → *"actionability/empty-landscape 前置短路已实现(A1/A2);默认确定性档因二元 a_t + α=1.0 退化为'仅全解才短路'(见 B #3),须以 `--aegis-digester llm` 取真值 a_t 才分级。"*

### ⌛ 台账过期-3 —— M-16 "round-global coordinator 当前尚未接入 live recipe"(部分)
- **事实**:`--target-strategy worst_first`(target.py:87-89)已接线,每轮单一全局 target;GAP-AUDIT §1 已注"现行'每轮单目标变体'政策下 candidates-per-round=4 实质等效 round-global,协调器仅多目标时才需要"。
- **勘误**:M-16 结论保留(多 target 时仍需 coordinator),但"尚未接入"改为 *"单-target 政策已接线,round-global 语义在单 target 下等效;仅多并发 target 时缺协调器。"*

> 说明:三条"台账过期"的根因一致——`PAPER-METHODOLOGY-DEVIATIONS.md` 自锚 "2026-07-25 版",而 GAP-AUDIT(07-27)与当前代码已前移。**GAP-AUDIT 已在摘要层口头修正,但 M-xx 逐条正文未回填**;建议在 M-12/M-16/M-18 行首加 `⌛ 见 GAP-AUDIT §1(07-27):此行状态已被代码超越` 的交叉引用,避免跨文档传播旧结论。

---

## 五、反向核查:台账已申报、阶段 1 未列的条目

判定:属于**效度/协议**类(非 flow 偏差,两侧 FLOW 都不会当"分歧"列)= 正确缺席;属于**已申报机制但某侧 FLOW 漏画** = 记为该侧遗漏。

| 台账项 | 阶段1为何未列 | 判定 |
|---|---|---|
| M-08 pass@2 掩蔽 | 论文自身指标局限,两侧忠实实现,非分歧 | 正确缺席(效度);其**门决策层**变体已由我方升为 NEW-3 |
| M-09 peak/final 无 held-out | 实验协议/选择偏差,非 flow 步骤;两 FLOW 均不含 held-out 评测 | 正确缺席(协议) |
| M-11 live-web/infra 混杂 | 效度/provenance;A 侧从论文记"infra 失败计为失败"(N3),**B 侧 FLOW 未记 infra 失败如何计分** | 正确缺席(效度)+ **B 侧文档小遗漏** |
| M-13 新任务路由泄漏 | cluster-ID 泄漏效度问题;B 的注入 level-cluster 恰是解题前可得,与 M-13 要求一致 | 正确缺席(效度) |
| M-14 旧探针 forkprobe_p2 边界 | 我方历史 artifact 报告勘误,非论文↔代码 flow 偏差 | 正确缺席(数据卫生) |
| M-21 no-config 重试借 §4.3 额度 | B 的 FLOW-IMPL §7.4 有画(evolve-retry + commit-bounce),我阶段1只当旗标掠过、未升为独立偏差 | DECLARED(M-21);我方欠强调,非漏报 |
| §7.3 lever-ban 双时间尺度 / §7.5 多桶 lever 归因 | evidence.py 深层机制,两 FLOW 都仅列常量未展开;论文优先字面实现,非分歧 | 正确缺席(忠实实现) |
| §7.9 候选 ID 双空间(paper 形↔repo 数字别名) | **B 侧 FLOW-IMPL 只画了内部 `^C-R\d+-\d{2,}$` 正则(manifest.py),未画 repo 边界的 `C-R1-01→C-0101` 别名翻译**(candidate_pipeline.outward_candidate_id) | DECLARED(§7.9);**B 侧遗漏一机制** |
| §7.16 --resume / §7.15 三修复旗 / §7.10 force-gate | B 的 FLOW-IMPL 旗标矩阵/子图均有覆盖 | DECLARED,已对齐 |

**反向核查小结**:台账无"已申报却被后续代码演进修复=过期作废"的整条(区别于上节三行"措辞过期");已申报项要么被阶段 1 命中、要么属效度/协议类正确缺席。唯二结构性发现是 **B 侧漏画 §7.9 ID 双空间与 infra 计分**(下节)。

---

## 六、A / B 侧质量评注(供主循环参考)

**A 侧(FLOW-PAPER.md)—— 质量高,可作论文事实基准。**
- 强:15 条 UNSPECIFIED 与 9 条内部矛盾(C1–C9)几乎逐条命中实现的裁决点(U1→M-01、U2→M-02、U3→M-05/pool.py、U4/U13→M-04、U5→M-03、U6→§8.4、U7→M-20、U8/U9→M-18、U12→修订、C1→M-17、C2→M-20、C3→FORK、C4→NEW-1 家族)。**A 的 UNSPECIFIED 表几乎就是台账 M-xx 的预言。**
- 唯一可改进:C4 只记到"全史 vs 前一轮",**未推到"全史 vs per-variant"这条更咬合 §4.5 的双读**(NEW-1 的论文侧种子)。非错读,是欠一步外推。
- 未见错读。co-evolution 正确标为"可选外壳、非 AEGIS 内环"。

**B 侧(FLOW-IMPL.md)—— 代码读精确,两处文档遗漏 + 一处措辞易误导。**
- 强:`ever_solved` 跨变体(§2/§7.1)、账本双评测因果错位(§8.1)、五关三空(§8.2)、alpha 双默认退化(§8.3)三个"最意外设计"全部是真金,直接支撑 NEW-1/NEW-3/P5/P15。file:line 锚可复核、定点核实全部命中。
- **遗漏 1**:§7.9 候选 ID **repo 边界数字别名翻译**(`C-R1-01→C-0101`)未画——只画了内部正则。此机制关乎 repo evidence 门能否收候选,建议 B 补。
- **遗漏 2**:infra/预算失败**如何计入 pass@2**(A 从论文记"计为失败")在 B 的评测流里未显式,建议 B 补一句(engine 评测路径)。
- **措辞易误导 1**:§5 超参表把 `PASS_COUNT_NOISE_THRESHOLD=3`(defaults.py:23)列为实值,**未注明 paper-mode 门实为 `noise_threshold=None`**(experiment_lock.py:232,定点核实)——该常量在本 gate 是**死值/遗构**。读者可能误以为门用了 3 的抗噪带。建议 B 加 `[未接入 paper-mode 门]` 标注。台账 M-04 本身正确(±5% 不冒充 fork 阈值),故这是 B 文档而非台账问题。

**跨侧一致性**:A 的 §6.1"full set every round"与 B 的"候选 T_k + 结算后全量"表面冲突,经仲裁为**互补而非矛盾**——结算后全量评测满足"每轮全量",候选门评测是 §4.5 收窄的 T_k;C5/U15 的"batch vs full"张力被实现用"门用 batch、计分用 full"两分解消化(P3),此点两侧都没说破,已在 P3/NEW-3 补齐。

---

*End FLOW-DIVERGENCE-VERDICT.md — 仲裁方 C,阶段1(2 FLOW + 4 定点)→ 阶段2(M-01..M-22 / GAP-AUDIT / SPEC §7)。*
