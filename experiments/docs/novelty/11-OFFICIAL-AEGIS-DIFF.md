# 官方 AEGIS 对比审计：feat/aegis + feat/aegis-experiment vs 论文 vs 我方复现

> 2026-08-05。三路 researcher 并行读 upstream（Darwin-Agent/HarnessX）三条分支的 git 对象，零 checkout、零 API。
> 分支：`feat/aegis`（核心，tip 5-21）、`feat/aegis-experiment`（tau2 实验层，tip 6-02）、`feat/he-tb2-add-aegis`（TB2，5-26）。
> 三条 tip 均 author-date == committer-date（未 rebase）。
> 🔴 **推送时间已定（2026-08-05 用户提供 GitHub 分支页 UI）**：`feat/aegis-experiment` "2 months ago"、`feat/he-tb2-add-aegis` "3 months ago" ⇒ **AEGIS 分支自 2026 年 5–6 月起就公开存在**。本文初版"七月下旬后补推"的推断**错误，撤回**——真相是**我方 Jul-21 审计漏查了分支列表**（只看了 main/落地页），"Darwin-Agent repo 内容≠论文 / AEGIS 未开源"的旧判词自 5–6 月起即不成立。审计教训：查开源状态必须 `git fetch --all` + 枚举全部分支。
> 作者：tenghl、zhao9797；`1a62993` 作者 Tingyang Chen (chentingyang@xiaomi.com)。
>
> **分支全覆盖（10 条）**：4 条代码分支（main + 三 AEGIS）见本文；另 6 条 = 网站线（shuolucs/Codex，Jul-20~23：homepage-site / homepage-accessible-link / deploy-pages-workflow / pages-enable-config / remove-pages-workflow / gh-pages），为 GitHub Pages 主页工程，无代码价值——但 **gh-pages 的 index.html 公开展示论文 Table 5 全部数字（87.4 / 73.8 / +13.6 / 49.5 / "Variant isolation"）**，即主页宣传的头条机制在其发布代码中不存在（见 §0-①）。站内另有论文 PDF 与图表资产（`assets/paper/`）。

---

## 0. 三个头条发现

### ① 官方发布里**没有变体池** —— 论文 §4.5 的头条机制未随代码发布

全分支 grep（`variant|ensemble|fork|retire|router|cluster|pool`，逐 hit 排除）：**零个变体池/Ensemble 路由/fork-retire 命中**。`ensemble` 在两条分支全树 **0 次**。官方架构是**单谱系**：一份 config 逐轮演化，ship / no_op / revert。连它自带的叙事文档 `aegis_story.md` 也从未提 ensemble/variant/pool。

⇒ **我方 `experiments/variant_pool/` 引擎没有官方对应物。它是论文文本的净新实现，不是对官方代码的复刻。** 论文的 GAIA 头条数字（Ensemble Δ=0.0→+13.6%）建立在一个**未发布**的机制上。

### ② GAIA 没有提交任何成规模实验

- 唯一成套的实验 = **tau2**：3 域 × ≤4 task-agent 模型（mimo-v2.5 / gpt-5.4 / qwen3.5-9b / sonnet-4-6）× 10 轮 × 2 trials，meta 恒为 Opus，11 个 `run_aegis_*.sh`。
- GAIA：runner（`recipe/gaia_evolver/run_meta_aegis.py`，1031 行）存在，但默认 **`MAX_TASKS_DEFAULT=1`（smoke）**、无任何 `.sh`、无数字。
- `aegis_story.md`（中文叙事）自述跑过 **GAIA-64 十四轮 pilot（task model 冻结）**，但**零产物入库**（`.gitignore` 明确排除 runs）。
- 全仓唯一的数字在 commit message 里：`9b27fd6` "telecom v3 R2 hit 100% pass_rate (vs v1/v2 never recovered from R1's 55% crash)"。

### ③ 论文 ↔ 官方代码的实质分歧（写作时必须并报）

| 论文说 | 官方代码做 |
|---|---|
| §4.5 Ensemble routing / K 变体 / APPLY-FORK-REJECT 三分 | **全部不存在**；决定只有 `ship \| no_op`，判决 `accept \| reject \| ask-more` |
| §4.1 seesaw："不得回退任何 T_t 中已解题"（全局棘轮）| **无棘轮**。窗口式相邻轮 watchlist（advisory）+ counterfactual 重放门（硬，精确等值）+ hit_rate<0.5 事后自动回滚 |
| Table 8 "seeds per cell = 3" | 代码 k=1 默认、focus 集 k=2（自适应），**无 3** |
| Table 8 "noise threshold ±5%" | **代码未实现**；以三态离散化（ALL/PARTIAL/NONE）+ Critic 提示词一句话近似 |
| §4.3 Planner 发 K_t briefs | **briefs 已废**；Planner 只写一份 `landscape.md`，单 Evolver 会话自选 K≥0 个候选 |

---

## 1. 官方管线（6 阶段，单谱系）

`orchestrator.py:3` "6-stage loop"；`__init__.py` 自称 "3-role adversarial MAS (Planner + Evolver × N + Critic)" 但 `num_evolvers` 已是**遗骸字段**（`orchestrator.py:111` "no longer dispatches"）。

| 阶段 | 名 | 产出 |
|---|---|---|
| P | Preprocess（P.1 Cleaner → P.2 Digester → P.3 Aggregate）| `digests/*.md` + actionability |
| 1 | Plan | 一份 `landscape.md`（无 briefs）|
| 2 | Propose | 单 Evolver 会话 → K≥0 个 `C-R{n}-NN.md` + `applied/…/config.yaml` |
| 3 | Judge | Critic 判决 + `decision.md`；带 **`ask_evolver` 工具**（可质询 Evolver，上限 2 轮）|
| 4 | Commit | 确定性门 × 每候选；**多 ship 循环**（全过门的都上）|
| 5 | Adjudicate | `hit_rate<0.5` 自动回滚 —— **定义了但 `run_round` 未接线**（tau2 pilot 以 `auto_revert_enabled=True` 外接）|

要点：
- **选择性调用**：`min_actionability=0.3` 提前退出（我方 α=0.5）。分数机械计算：任一 ALL_FAIL→1.0 / PARTIAL→0.8 / ALL_PASS 但有 Latent Fragility→0.3 / 纯 ALL_PASS→0.0。
- **Digester 连成功也消化**（三模板 ALL_FAIL/ALL_PASS/PARTIAL_PASS；ALL_PASS 模板："Your job is NOT to celebrate; identify the reusable strategy and any latent fragility"）。我方只消化失败。
- 每个 agent 都套 Read/Write scope gate；`harnessx/` 源码与历史 run 对全部 agent **禁读**（防 prompt injection / 跨实验泄漏）；`read_scope_gate` 还拦 **Bash 里的绝对路径**（275fde0）。
- Critic（314 行，最大）两部分：逐候选（含**候选×现有配置相互作用分析** + tools/processor 候选的 **Level-2 round-trip 证据核验**）+ **组合审计**（读 scoreboard + ship_outcomes，某桶 ≥3 次 ship 且命中率 <40% → `strategy_concern`，可整轮否决）。

## 2. 官方门序 vs 我方门序

**官方**：`structure → novelty → canonicalize → counterfactual → replay`（cheap-first 短路）
**我方**：`MANIFEST_COMPLETE → CANONICALIZE → BUILD_SMOKE_L1 → ROUNDTRIP_L2 → SEESAW_REGRESSION`

| 官方门 | 干什么 |
|---|---|
| `structure`（460 行！）| 证据不变量 IV-1..IV-12：**引用锚必须真实存在**（文件在盘、`step_N` 定位不越界）、manifest 必备键、桶↔文件扩展名一致、`iterates_from` 引用完整性、被 Critic 点名的方向必须响应否则要写"为何不可行" |
| `novelty`（17 行）| 对**已被驳回的签名账本**去重（sha256 over file_changes），防重提已证伪的编辑 |
| `canonicalize` | 同我方 |
| **`counterfactual`（176 行）** | **把新候选的 processor 链重放在上一轮通过任务的已录事件上（k=3 抽样），`final_output` 或 `exit_reason` 任何精确变化即拒**。零 LLM 零工具执行。这是官方的"硬 seesaw"——但对象是**重放等值**，不是 pass@2 计数 |
| `replay` | 合成任务 smoke（= 我方 BUILD_SMOKE_L1）|

**Level-2 证据**：官方放在 Evolver 模板里当强制流程（"inspect the tool-role message AS THE PROVIDER SERIALIZES IT"）+ Critic 核验；纯 prompt 桶豁免（"the counterfactual gate provides the equivalent smoke check"）——与我方 `manifest.py:65-68` 逐字同构。四桶分类 prompt/tools/config/processor **两边完全一致**。

## 3. 数据层对比（我方四病理重新定性）

官方 `data/` 十件套：ledger(678)/regressions(265)/attribution(203)/scoreboard(118)/journal(76)/archive(69)/audit(62)/reputation(50)/signatures(22)。

| 方面 | 官方 | 我方 | 判 |
|---|---|---|---|
| 记录单元 | (round, task) 行，`passed_flags` 位列表 + k | (variant, task) 簇聚合 | 官方无变体轴 |
| 平滑 | **无**（`sum/len` 裸率，三态离散化）| Laplace 在簇聚合 | 我方加的 |
| 回退基线 | **窗口式相邻轮**（R{N-2}→R{N-1}），advisory，agent 可申辩"transient" | 全局 `ever_solved` 单调棘轮，硬否决 | **根本不同** |
| 信用分配 | **三层齐备**：attribution（机械签名点火：新工具在该题真被调了吗）→ reputation（桶级滑窗均分，**Planner 消费**，未知桶 0.7 助探索）→ scoreboard | 无（只有 meta-agent 自写 CONTEXT.md）| 官方有真反馈环 |
| 防静默无效 | `ShipNotLandedError`（合成后 config 与 base 逐字节相同即拒绝继续）+ `apply.py` 强制 prompt 桶的 `template_path` 指向 scratch 内（**明写防"YAML 仍指向共享模板的静默 no-op"**）| M-41（本分支）| **他们撞过同一类病** |

**我方 P3/P5/P6/P7 的重新定性**（写论文措辞的关键）：

| 病理 | 在官方架构下 | 正确表述 |
|---|---|---|
| P3 2 样本硬门 | 不存在此门（ship 决定是 Critic LLM 判断 + 结构门 + 事后 hit_rate）| **论文所述 seesaw 的忠实实现产生 P3；官方代码绕开了它** |
| P5 门测量丢弃 | 结构上不可能（唯一测量流全进 ledger）| 同上 |
| P6 fork 轮循环计分 | 无 fork 轮 | 池是我方净新物，其病理是池的 |
| P7 棘轮饱和 | **无棘轮**（窗口式）| 论文原文"any previously solved task recorded in T_t"是全局棘轮；**官方自己没照论文做** |

⇒ 三条病理是**论文规格本身**的病，官方实现用不同设计**默默绕开**了它们——这是"论文欠规格 + 代码偏离论文"的可证据链，比"我们的 bug"强得多。

## 4. 观察通道对比（M-42 定位终判）

官方 Layer-A（`trace_facts.py`，459 行，机械提取、逐字注入、"do not rewrite"）：
- `ToolCallFact`：step/tool/args_sha/预览/**return_type**/长度/**next_uses_result**（下一条助手消息是否含工具输出 ≥20 字符子串 = 工具有效性启发）
- `ExitFact`：exit_reason/步数/passed/末 200 字符终局片段
- `RepeatRun`（同参连打 ≥2）、`ToolBurst`（total≥20 或单步峰≥10；severity high = total≥30 或峰≥15；例子"75 SmartFetch in 19 steps"——**SmartFetch 不是内置工具，是演化产物的原型名**）

Layer-B：**9 类受控病理词表**（`tool_effect_missing / repeat_without_progress / error_ignored / multimodal_silent_drop / hallucinated_reference / missing_capability / budget_starvation / prompt_rule_violation / final_answer_brittle`），每条带 anchor + ≤200 字符逐字引文 + severity；structure 门 IV-1 强制锚真实。Layer-C 按 pattern 分（ALL_FAIL→共同根因+harness 偏置假设；ALL_PASS→可复用策略+潜在脆弱性；PARTIAL→决定性分歧）。

P.1 Cleaner：工具输出 sha 去重 + >2048B 外置为 `content_ref`。

**⭐ 但两个最大的失败模式仍是机械盲区**：`trace_facts.py:76` 的失败分类器只认 `low.startswith("error") or "traceback" in low[:200]`。而 `[SEARCH UNAVAILABLE]`（以 `[` 开头）和 `[fetch failed: …]` / `Fetch error for …` 全部被标成 `return_type="text"`，**永远数不进来**。唯一重叠 = 循环维（ToolBurst ≈ 我方 loop_warning_count）。

⇒ **M-42 终判**：方向被官方独立验证（他们建了整个 Layer-A/B/C 就是在做"递化验单"），但我方旗标数的 `search_unavailable_count` / `fetch_error_count`（实测占失败 17-42% 的两类）**连官方机械通道也没数**——加法仍然成立，且立论升级为"官方与我方独立收敛到通道拓宽；官方机械层仍漏掉 web 工具自报失败标记；我们补上并测增量"。

## 5. 生态级主题：静默失效是自演化基建的职业病

| 案例 | 谁 |
|---|---|
| `file:///` 剥 7 字符 + 裸 except 吞掉，5/7 编辑蒸发 | 我方 s1k8b103（M-41 修）|
| 890 rollout 空系统提示词 | 我方（M-27）|
| **rollback 检测匹配错字段（`kind=="ship"` 永不命中），6-02 前静默失灵** | **官方**（5d4bd13 自修）|
| regressions.md 差一轮 off-by-one，"silently hiding every regression caused by the just-shipped config" | **官方**（1a62993 自修）|
| `TemplateSystemPromptBuilder.extra_context` 序列化往返静默丢弃 | **官方**（digester.py 注释自曝，绕道 StaticSystemPromptBuilder）|
| `ShipNotLandedError` / `apply.py` template_path 校验 | 官方的防复发层（≈我方 S1"成关"）|
| **⭐ counterfactual 门本体 = 潜伏 no-op**：门按 `kind` 字段分发（`after_model/after_tool/task_end`）并读 `final_output`——**而官方全管线零个生产者产出该 schema**（runner 拷贝的是 type-based 消息流 `raw_assistant/raw_tool/episode_end`，preprocess 原样透传，从不改名、从不合成 `final_output`；`kind` 式事件行只存在于该门的单元测试夹具里）。真实喂入 → 零 `kind` 匹配 → 双侧 `final_output=None` → **恒 `ok=True`**。防静默回退的门，自己在静默空转 | **官方**（我方 Aug-05 前提检查发现；未见官方自修）|

⇒ 双方独立撞上同一类病并各自建了防线。"**效力验证是自演化系统的一等公民问题**"有了双边证据。

## 6. tau2 特有情报

- `benchmark_context="tau2"` 硬约束注入 Evolver 模板：禁止替换 `NullSystemPromptBuilder`——因为 runloop 在设置非空系统提示词时**删除**基准注入的 23K 字符域策略 → "catastrophic regression…dropped 55%"。修法是提示词约束而非代码防护。
- tau2 pilot 的轮级回滚：`delta_reward ≤ −tol 且少过 ≥3 题` → 回到 `pre_ship_config` + 桶信誉记 False；另有 best-so-far 内核（≈我方 `_score_and_gate`，GAIA runner 直接 import 它）。
- web_fetch +55 行 = 挂死加固（PDF 页 `inner_text` 永挂、25/30/60s 三层超时、二进制不再落 Playwright）+ 结构化失败标记——动机注释明写 "the GAIA runs of 2026-05-13"。

## 7. 对论文的影响（CH3/写作层）

1. **复现对象要三分**：论文文本 / 官方代码 / 我方实现——三者两两有实质差。我方变体池 = 论文 §4.5 的**唯一存世实现**；措辞从"复现"改为"paper-faithful implementation of a mechanism absent from the official release"。
2. P3/P5/P6/P7 从"我方缺陷"升格为"**论文规格的可测后果**（官方代码以未记载的设计偏离绕开之）"。
3. M-42 立论升级（§4）；M-41 获官方同类病防线佐证（§5）。
4. 论文↔代码分歧清单（§0-③）进 threats/related；"seeds=3 vs k=1-2"、"±5% 未实现"两条须并报。
5. 官方 Layer-B 九类词表可作我方失败分类的**对照词表**（我方 `failure_category` 79.5% null 的修复参照）。
6. 日期口径（已修正）：写作引用官方代码注 "publicly available since May–June 2026 (GitHub branches UI, verified 2026-08-05)"；我方 Jul-21 审计的"未开源"判断为**漏查分支所致的审计失误**，如需引用当时判断须连同此勘误。

## 7.5 counterfactual 门移植前提检查（Aug-05，已闭）

**判定：(a) REPLAYABLE NOW** —— 我方落盘 `sessions/<sid>/<run_id>.jsonl` 足以重放，需一个**强制适配器**（~60–120 行）。

- **为什么必须有适配器**：官方门的 `kind`/`final_output` schema **无任何生产者**（见 §5 新行）——移植 = 门 + 适配器一起写。映射表：`raw_assistant→after_model`（content/tool_calls 从 `message.*` 拍平）、`raw_tool→after_tool`（tool_name/result/tool_call_id）、`episode_end→task_end`（`exit_reason` 自带；**`final_output` 需注入**，三个落盘来源任选：`comparison.json` 逐尝试记录（`run.py:246-262` 写入）/ 轨迹 `.md` 的 `extracted_answer`+`## Result` / 消息流最后一条 `raw_assistant.message.content`）。
- **前向 1 行修**：`harnessx/tracing/journal.py:1136-1164` 写 `episode_end` 时手里就有 `event.final_output` 却丢掉（`message:None`）——加一行即让未来 run 原生带上。
- **保真边界（须并报）**：journal 从未持久化 `thinking_blocks`/`usage`/`ToolResultEvent.content_blocks`/`final_messages`——读这些字段的 processor 重放时退化，且门的 `try/except` 会吞掉 AttributeError ⇒ **假"无回退"风险**。对门的既定契约（检测 `final_output`/`exit_reason` 改写）可重建面足够。
- **移植纪律（由官方之病反推）**：门的测试**必须对着真实生产者输出**跑一条端到端，不许只用手搓夹具——官方恰恰死在"夹具 schema ≠ 生产 schema"上。

## 8. 未闭项

- `args.k_all` 默认值未定位（GAIA runner 的逐题 rollout 下限）。
- 官方 Stage-5 在 GAIA pilot driver 里是否接线未读（`run_meta_aegis.py` 1031 行只读了默认段）。
- 官方模板引用的测试名与实际文件名不一致（`test_custom_processor_registry.py::test_harness_config_supports_file_target_without_init_py` vs 另有 `test_custom_processor_registry_file_uri.py` 在盘）——两者都存在，引用未核对到函数级。
- `aegis_story.md`（GAIA-64 十四轮叙事）未逐字读，只确认了无 ensemble/pool 词汇。
- ~~upstream/main 三个提交尚未合入~~ **已合**：merge commit `91466f0`（Aug-05，用户令"把能抄的抄了"），零冲突（我方未触 spawn_subagent/trajectory）。
