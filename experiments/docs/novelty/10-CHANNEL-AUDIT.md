# 观测通道审计：环子看得见什么，以及它因此做错了什么

> 2026-08-04。s1k8b103 与 e_pervar3 双 run 全量核查，纯落盘产物 + 源码，零 API 成本。
> 本文回答用户提出的分类框架：**对每个真实失败模式——信息到没到环子？到了修没修？修了落没落地？**
> 分类：**a = 信号从未进入环子** / **b = 进入了但环子没行动** / **c = 环子行动了**（落没落地另记）。
> 结论先行：**问题不在演化算法，在喂给它的东西。** 本分支（`feat/observation-channel`）即为修复。

---

## 0. 一句话

环子拿到的是一份只有"生死 + 住院天数"的病历（frontmatter），化验单（轨迹正文）从未递到它手上。它据此判定"病人没救了"并自我关停 7 轮；唯一一次它自己翻到化验单（R8），立刻开出了对症的药——**但那个提案没被采纳**。

---

## 1. 两个 run 的编辑落地核查（先分清 c 类里的"落地"与"蒸发"）

| | s1k8b103 | e_pervar3（CH3 主跑）|
|---|---|---|
| 上线编辑真正生效 | **2 / 7**（且都只是部分）| **2 / 2** ✅ |
| 空系统提示词 rollout | **890**（=634 active + 256 gate；V5:128 / V6:274 / V7:232）| **0** ✅ |
| `file:///D:/` 路径 bug | 5 条编辑死于此 | 不存在（全为点分模块路径）✅ |
| 提示词加载核查 | V5/V6/V7 首调 input_tokens ≈875–895、无 system 消息 | 全部 3500–3900 band，0 个可疑 ✅ |

### s1k8b103 逐条（`RUN-LOG.md` M-27 的 890 已逐字核实 = 空提示词 rollout 数，与搜索失败计数无关）

| 轮 | 变体 | 桶 | 路径写法 | 跑了吗 | 证据 |
|---|---|---|---|---|---|
| R1 | V1 | prompt | 普通路径 | ✅ | input_tokens 3613，agent 按新提示词行动 |
| R1 | V1 | tool `python_eval` | `file://` | ❌ | 49 个 session 调它 → `Tool 'python_eval' not found` |
| R2 | V2 | processor | `file://` | ❌ | nudge 注入 **0** 次（3198 session）|
| R4 | V3 | processor | `file://` | ❌ | V3/V4 共 822 尝试，最大步数 20 |
| R6 | V4 | processor | `file://` | ❌ | 同上 |
| R7 | V5 | prompt | `file://` | ❌ | 空提示词 ×128 |
| R11 | V6 | `StepCountdownProcessor` | **点分模块路径** | ✅ | `[step-countdown]` 注入 271/274 session |
| R11 | V6 | prompt + nudge 重写 | `file://` | ❌ | 空提示词 ×274 |
| R13 | V7 | prompt | `file://` | ❌ | 空提示词 ×232 |

**机理（已证，非推断）**：`builder.py:520-542` 的 `_instantiate` 剥 `len("file://")`=7 字符，`file:///D:/...` 剩 `/D:/...`，Windows 上 `isfile=False` → 抛错 → `harness.py:353-360` 裸 `except Exception: return None` **零日志**吞掉 → `:586-590` 静默跳过。模板走另一条：`SystemPromptProcessor` 装得上（模块路径），但打不开 `file:///…j2` → `processor.py:705-714` 记 WARNING 放行 → **系统提示词 = ""**（SHA256 `e3b0c442`）。
meta-agent 事后自述 *"State is a frozen dataclass → FrozenInstanceError"* 为**编造**（`state.py:119` 普通 `@dataclass`）——真实失效 + 虚构机理 + 在虚构之上继续演化。

### e_pervar3 逐条（两条编辑都是 `StepCountdownProcessor`，参数级指纹验证）

| 轮 | 决定 | 参数 | 运行时指纹 |
|---|---|---|---|
| R2 | FORK→V1 | `escalate_within=2`（默认）| 升级触发在 **第 18 步**（N−2）；R3/V1 128/128 session、1514 次注入 |
| R8 | APPLY→V0 | `escalate_within=3` | 升级触发在 **第 17 步**（N−3）；R9/V0 65 session、478 次注入 |

**⇒ CH3 主跑的编辑交付链是干净的，s1 的发现不污染它。** 且演化确实起效：撞墙率从早期 30–34% 降到 R6–R15 的 12.6%。

---

## 2. 通道的真实形态：自助式，且两个 run 同构

**Evolver 是个编程 agent，拿到的是一个目录指针，自己写扫描脚本去读轨迹。** `traj_summary` 的 schema 是 meta-agent 自己定的，不是 harness 给的。

| 通道特征 | s1k8b103 | e_pervar3 |
|---|---|---|
| 自写扫描脚本 | `scan_traj.py` 等 | **48 个**（`scan_traj.py`×6、`analyze_traj.py`×4、一次性 ~30）|
| 只解析 frontmatter、丢弃全部正文 | ✅ | ✅（主流脚本）|
| **跳过 `.a2.md`**（pass@2 第二次 rollout 从未被读）| ✅ `if '.a2.' in fname: continue` | ✅ 同一行 |
| 持久化的摘要 | `traj_summary.txt`（其中 `errors`/`jmc_present` 两列**全是 `?`**——正则打了不存在的字段）| **无**（scanner 走 stdout，未持久化 → 它实际报了什么**不可考**）|
| `failure_category`（本应为语义失败聚类）| 只有 `null`/`gaia_level_N` | **79.5% null**，其余仅 `gaia_level_N`（1648 条）|
| `implicated_components` | **1648 条全 `[]`** | **1648 条全 `[]`** |
| `TASK.md` 承诺的 `pareto_archive.json`/`task_catalog.md`/`env_probe.md` | **全 run 0 个文件** | 同 |
| 简报自述"per-task digests 已注入" | **与产物不符**（假话）| 同 |
| LLM Digester 窗口 | frontmatter 4k + head 12k + **tail 20k，中段丢弃** | 同代码路径 |

### 信号进出表（e_pervar3；s1k8b103 同构）

| 信号 | 原始轨迹里 | 到达 `_meta_scratch` | 分类 |
|---|---|---|---|
| pass/fail、`exit_reason`、steps、tokens、cost、`judge_verdict` | ✅ frontmatter | ✅ | **c** |
| **`Fetch error for`**（web_fetch 浏览器路径）| **423 文件 / 652 次** | **0** | **a** |
| `[fetch failed` | 36 / 42 | **0** | **a** |
| **`[LoopDetection]`** 警告 | **243 文件 / 807 次** | **0** | **a** |
| **`[SEARCH UNAVAILABLE]`** | 289 文件 / 437 次（另 a2:274/412）| **7 文件 / 26 次**（唯一例外，见 §4）| **a**（例外见下）|
| `judge_missing_capability` | ✅ frontmatter | s1 死列 / pervar 可用但少用 | a/部分 |
| 第二次 rollout 全部内容 | ✅ | **0**（扫描器跳过）| **a** |

**为什么搜索信号进不去**：`web_search.py:366-372` 把 `[SEARCH UNAVAILABLE]` **作为工具结果 `return`，不是 `raise`** —— 永远不计入 `tool_error_counts`，于是 frontmatter 里没有任何痕迹。fetch/loop 同理（正文注入）。

---

## 3. 后果：环子把"看不见的失败"判成"修不了"，然后自我关停

e_pervar3 十六轮里 **7 轮 `no_candidate`**（R0 为种子轮；R1/R3/R6/R9/R14/R15 为演化期旱灾）。**目标变体每轮带着 39–103 道题，旱灾与承载无关**（这推翻了 P8 沿用 s1 病因的猜测——s1 的旱灾是目标零承载，pervar 是**自我门控**）。

| 轮 | actionability | `short_circuit` | 机理 |
|---|---|---|---|
| R1 | **0.85（过线）** | `planner_empty_landscape` | planner 写了完整六类失败诊断（"20 步预算是首要瓶颈…常已收集足够证据却无法定稿"），**然后交出 0 条工单** |
| R3/R6/R15 | 0.2–0.3 < α=0.5 | `actionability_below_threshold` | digester 原话：*"All identified failures are attributed to **model_capability or environmental issues, which are not addressable through harness edits**"* —— 而同期 **29% 的尝试死在它看不见的 fetch 错误上** |
| R9 | 0.0 | 同上 | **账本盲区**：4 道题当轮实跑 0/2，但 digester 读的是**结算账本**（记为已解决）→ "failed=0，无事可做"。同批题（`3cef3a44`/`46719c30`/`bda648d7`/`c365c1c7`）正是慢性噪声题 |
| R14 | 0.0 | 同上 | 仅存失败被判 model_capability |

非旱灾的 R4/R12 另一病：产出 4 候选全卡提案段——*"meta-agent finished after 71.0s but no config.yaml was written… ended with analysis but did not commit to a final decision."*

**9 次 SEESAW REJECT 全部 `improved=0`**（改进≥1 题的候选只会 APPLY/FORK，永不 REJECT），挡路回退题高度集中：`7673d772` 在 9 次里出现 **7 次**。

---

## 4. ⭐ 自然实验：信号一进去，环子立刻会修

**R8 轮，某个 meta-agent 实例碰巧自己写了个扫正文的脚本**（`scan2.py`：`text.count('[SEARCH UNAVAILABLE]')`）。它当场发现并写下：

> *"`7673d772` … spent **5 of 20 steps** retrying a blocked endpoint, each time getting `[SEARCH UNAVAILABLE]`"* —— `R8/.../C-R8-01/_meta_scratch/candidates.md`

并提出针对性修复 **`SearchUnavailableSwitch`**（C-R8-01）。**但同轮被采纳的是 C-R8-03（StepCountdownProcessor），该提案丢失。**

⇒ **诊断能力从来不缺，缺的是输入。** 这是"喂进去它就会修"假设的一次免费的、落盘在案的验证。

---

## 5. 客观失败分类（谁大谁小）

### e_pervar3（R6–R15 settled，2060 次尝试；R0–R5 无逐尝试记录，frontmatter 抽查显示早期撞墙 30–34%）

| 类 | 占比 | 备注 |
|---|---|---|
| 通过 | **70.1%** | s1: 56.8% |
| fetch 失败（叠加层）| 29.1% | s1: 42.4% |
| 搜索死（叠加层）| 17.8% | s1: 17.1% |
| 撞墙 | 12.6% | s1: 33.1%；**逐级 2.7% / 14.8% / 35.4%**（s1: 13.3/39.2/71.1）|
| loop 警告（叠加层）| 12.6% | s1: 21.6%；硬 raise **0 次** |
| **真答错（无任何工具失败）** | **7.2%** | s1: **4.0%** |
| infra | 1.5% | s1: 1.9% |

**撞墙死因构成（n=260）**：fetch 50.8% / 死搜索 39.2% / **纯净撞墙仅 17.3%**（s1: 65.2/29.1/12.4）。⇒ **墙是症状，web 工具失败是主因。**

### 步数墙终判（P8 关联悬案，就此了结）

通过的尝试：**均值 9.2 步、中位数 8**；≥18 步的通过仅 **10.3%**、恰 20 步 **2.4%**。⇒ 墙偶尔卡到可解题，但**抬上限是弱杠杆**——大多数可解题 8 步完事，撞墙的多是本就不可解 + 被 fetch/搜索拖死的。

---

## 6. 分类框架总表（用户的 a/b/c 判据）

| 失败模式 | 客观规模（pervar）| 进输入了吗 | 环子诊断了吗 | 修了吗 | 落地了吗 | 分类 |
|---|---|---|---|---|---|---|
| 步数耗尽 | 12.6%（早期 30%+）| ✅ `exit_reason` | ✅ 每轮点名 | ✅ 2/2 上线 | ✅（pervar）/ ❌（s1 5/7 蒸发）| **c** |
| fetch 失败 | **29.1%** | ❌ 0 | s1 借错误计数提过；pervar 个别实例 | R7(s1) 一次，pervar 未上线 | — | **a 为主** |
| 搜索死 | 17.8% | ❌（除 R8 例外）| R8 例外：精准诊断 | 提案未被采纳 | — | **a**（例外证明机制）|
| loop 空转 | 12.6% | ❌ 0 | ❌ 从未 | ❌ | — | **a** |
| 第二次 rollout | 50% 的测量 | ❌ 扫描器跳过 | — | — | — | **a** |
| 真答错 | 7.2% | ✅（判语可见）| ✅ | R13(s1) 格式规则 | s1 蒸发 | c |

**a 类占失败体量的大头 ⇒ 环子的天花板卡在输入，不在算法。**

---

## 7. 本分支的修复（= 实验自变量）

**地基修复（两臂共用，非贡献）**：
1. `builder.py` 的 `file://` URI 解析修复（`file:///D:/`、`file://D:/`、POSIX 全覆盖）；
2. `harness.py` 实例化失败**响亮报错**（ERROR 级、带 target 与异常），消费端记录被丢弃的组件——**永不静默丢弃**；
3. 模板路径同样处理。

> ⚠️ 与既有 **M-37/M-38** 的关系：另一 session 已做**提示词侧**修复（brief 教 Evolver 两斜杠拼写，live 验证 10/10），但其遗留栏明记"加载缺陷本身仍在 vendored 代码内（零改动约束）"。本分支的装载器修复是**互补的第二层**，并**明示打破**该零改动约束（依据与登记见 M-41）。

**通道拓宽（实验旋钮，`--traj-failure-signals`，默认关）**：
把三类正文信号在轨迹落盘时计数进 **frontmatter** 平铺键：
`search_unavailable_count / fetch_error_count / fetch_empty_count / loop_warning_count`
- **为什么是 frontmatter**：meta-agent 自写的扫描器**本来就只读 frontmatter**——信号从它已在用的通道进入，**不改提示词、不改扫描器、不改演化逻辑**；
- **为什么不改工具语义**（如让搜索失败 raise）：那会改变 agent 行为，污染对照；计数是纯观测；
- **为什么无过拟合之虞**：全部信号来自运行时，**零字节来自数据集标签**（响应此前"按 GAIA level 分配步数"被否的裁定）；
- 旗标走 `_epsilon_provenance` 模式（默认返回 `None`，lock 逐字节不变），**不加 `Hyperparams` 字段**。

**实验设计（待用户明令后才跑）**：
- 臂 0：旗标关（= 论文原样通道）
- 臂 1：旗标开（frontmatter 携带失败信号）
- 同床同预算同轮数，读数 = 演化产出的 harness 终分 + 环子行为（旱灾轮数、候选针对性）。
- 预注册的机理预言：臂 1 的 digester actionability 不再把 fetch/搜索失败判成 "model_capability"，旱灾轮数下降；候选出现 fetch/搜索方向的编辑。

---

## 8. 方法学警告（跨 session 必读）

1. **`ripgrep` 会静默跳过轨迹 `.md`**（正文含 NUL 字节被判 binary → **假零**）。一切轨迹计数必须 `grep -a`。本文早前一版的"890 次搜索失败"即为 rg 假零后的误值。
2. **`[SEARCH UNAVAILABLE]` 的全树计数是口径敏感的**：s1k8b103 两次独立扫描给出 908/703（active+gate 轨迹）与 1327/946（含 pipeline 副本），差异来自扫描范围；**可靠口径是 settled 逐尝试受影响数（s1: 529/3090 = 17.1%）**。引用必须带口径。
3. M-27 的 "空 890" = **空提示词 rollout 数**（634+256），与搜索计数无关，数值相近纯属巧合。
4. e_pervar3 的 `comparison.json` **只覆盖 R6–R15**；R0–R5 只能靠 frontmatter 重建。
5. 主仓当前分支为 `fix/novelty-s3`（非 `exp/variant-pool`）；本文所在分支 `feat/observation-channel`（worktree `HarnessX-channel`）。
6. e_pervar3 已于 2026-08-04 00:45 完结（R0–R15 + `pool_report.json`），早前"无聚合产物"的记载过时。

---

## 9. 记账

- 本分支四表面：git 提交（本 worktree）/ `RUN-LOG.md` 台账 / `SPEC.md` §7.x 裁决 / `PAPER-METHODOLOGY-DEVIATIONS.md` M-xx —— 随代码提交同步补。
- **任何付费跑必须用户明令**；本文 §7 的两臂实验为提案，未启动。
