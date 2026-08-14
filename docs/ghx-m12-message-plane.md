# GHX v6 M12 — 消息数据面（独立记录）

**日期** 2026-08-14 · **分支** `ghx/v6-graph-runtime` · **落地 commit** `3cf588e`
**面属性** GHX overlay + core 仪表；**零 vendored 字节**（未碰 `harnessx/aegis/**`、
`run_meta_aegis.py`、`run_meta.py`），故不入
[`aegis-vendored-deviations.md`](aegis-vendored-deviations.md) 台账，单独记在此。

---

## 1. 为什么改

U 的 `OBSERVED_DATA` 只从 `State.slot_provenance` 推导。GAIA 这套栈**什么都走消息
列表，几乎不写 slot**，所以这条通道恒空。叠上第二条既有事实——`observed_control`
按设计**不跨 hook 派发边界**（`unfold.py:204-210`）——两条合起来产生一个确定的后果：

> **任何因果锥 = 锚点所在的那一次 firing，不多一个节点。**

实测已记在 [`experiments/docs/GHX-L2-EMPTY-CONE-ROOT-CAUSE.md`](../experiments/docs/GHX-L2-EMPTY-CONE-ROOT-CAUSE.md)：
160 个锥**全部**退化成同一条最终 `task_end` 拆解链（8 个 `proc:` 节点），
`observed_data` 抽样 12/12 恒为 0。

这一条根因同时解释了写入端的病：`facts.md` 是从这些锥里聚出来的，于是 evolver 的
节点词汇表永远只有那 8 个 `proc:` 名字，它去造 `tool:Write` / `tool_registry` 之类的
端点就必然 `dangling_endpoint`（L5 v3 R2 实测 8 次），退而求其次只能出
`insert_node` / `replace_same_group`——**恰好就是 compose 桶搬得动的那些平铺操作**，
于是所有候选看起来都像在改 YAML。

## 2. 改了什么

消息成为**同一套到达定值日志里的第二个访问面**。没有新引擎，`_build_data_edges`
一个字没动语义——它本来就只按 key 分组、追踪最近一次写、遇读连边，不关心 key 是谁。

| 位置 | 改动 | 语义 |
|---|---|---|
| `core/processor.py` | 派发循环里已为契约校验算好的 `prev_msgs`/`curr_msgs`，在录制开启时也算，并由**同一次迭代里那个 `_inv_id`** 认领 | 出现在 out 不在 in = **写**；在 in 不在 out = **读**（截断/压缩/改写的旧半边）；改写两者都记 |
| `core/runloop.py` | provider 调用点铸一个节点 `model:<name>`（hook `"model"`），与 M5 给工具铸节点同形 | 模型**读**流水线装配出的整张消息表，**写**回复；它不属于周围任何处理器 |
| `core/runloop.py` | 工具结果消息显式记在 `_tool_inv_id` 名下 | 工具**写**它的结果消息 |
| `graph/unfold.py` | `record_model_invocation` / `message_key` / `log_message_access`；`_build_data_edges` 按 plane 分流 | 边元数据带 `plane`；消息面豁免 provenance 交叉校验 |
| `ghx/evidence_files.py` | `_cone_anchors` = 终末调用 ∪ **本次运行最后一次模型调用** | 见 §4 |

### 消息身份

**对象身份**。原样透传保持同一个对象（不是新定义）；改写分配新对象（是新定义）——
到达定值要的就是这个语义，基本白送。recorder 把每个键过的消息**钉住**
（`_msg_pin`），否则 CPython 回收后 id 复用会把两条无关消息焊成一条定义。

### 两个反直觉但必须的细节

1. **写 = 该对象的第一次露面，仅此一次。**
   step_start 的上下文装配器把 `State.messages` 材料化进一个空事件元组，在派发器
   diff 看来"这一整包都是它产的"。若照记，last-write-wins 会让装配器**每一步**夺走
   全部消息的著作权，`tool→model`、`model→model` 这两条全部消失——正是整个改动的目的。
   身份即定义：对象只在它诞生那一刻被定义，之后的再现是搬运不是著作。

2. **模型的读记在 `_before_msg_list` 上，不是 `final_messages`。**
   `_validate_messages` 是 runloop 内部的规范化器，它会**把连续的 user 消息并成一个
   新对象**（`runloop.py:962-963`）。若在它的输出上读身份，凡被它碰过的消息其著作权
   边全部静默丢失——在这套栈上这是常见情形，不是边角。
   代价写明：被它**丢弃**的消息（孤儿 tool result）仍会被记成"模型读过"。

## 3. 什么没变

- **slot 面一字未动**，含它的 `slot_provenance` 交叉校验。消息面豁免该校验（消息没有
  provenance 记录，派发点/runloop 点本身就是第一手观察）。
- **全部门在"是否装了 recorder"上**。`HARNESSX_GHX_UNFOLD` 关闭时，多出来的只有原本
  就有的那一次 context-var 读取，行为字节级不变——L1≡L0 身份证明不受影响。
- **未碰 baseline checkout**（`D:\PycharmProj\HarnessX-baseline`）。L0 官方臂在飞，
  按"在飞不追溯"不动。

## 4. 为什么必须同时改锚点

`TaskEndEvent` **没有 `messages` 字段**（`core/events.py:198`，只有 `StepStartEvent`
和 `BeforeModelEvent` 有）。所以 `task_end` 处理器**永远不可能**碰到消息面，
以终末调用为唯一锚点的锥，数据面修好了也照样一个节点都到不了。

这不是把已被判死的 M11 实验 B（"换锚点"）捡回来——B 是在**没有数据边**的前提下换锚点，
自然 9 个节点原地不动。有了数据面之后，最后一次模型调用是**发出最终答案、并读过产生
它的全部历史**的那个调用，它才是消息面上运行的因果根。终末锚点保留，取并集，只增不减。

## 5. 测试

新增 `tests/integration/test_unfold_messages.py`（8 条，真跑 harness、从盘上读回 U）：
模型节点存在且被控制边桥接 · 一次不碰任何 slot 的运行也产出数据边 ·
`tool→model` 与 `model→model` 两条边 · 数据边**跨步**（控制边构造上不能）·
装配器不夺著作权 · 注入者拥有自己那条边 · 锥能到工具和模型（只用终末锚点到不了）·
不设旗标就不录。

改到的既有测试两处，都是 M12 有意改掉的不变量，不是回归：

- `test_unfold.py::test_node_count_matches_invocations` — 处理器节点计数需再排除
  `model`（M5 时排除了 `tool`）。
- `test_unfold.py::test_data_edges_match_provenance` — "每条数据边都对得上 slot
  provenance"现在是**slot 面的**不变量；消息面按设计豁免。测试改为分面断言，
  并加一条"消息 key 不得漏进 slot 面"。

全量：`2270 passed`。两条失败（`test_plugin_capabilities::test_stop_hook_runs_on_task_end`、
`test_sandbox::test_local_sandbox_exec_timeout`）经 `git stash` 验证**在干净树上同样失败**，
与本次改动无关。

## 6. 已知代价与未验事项

- 孤儿 tool result 会被记成"模型读过"（§2.2 的取舍代价）。
- `_msg_pin` 对每条见过的消息持强引用。`State.raw_messages` 本来就全程持有它们，
  额外滞留只有"被处理器造出又丢弃"的瞬时消息，属少数。
- 6 题床**只验机制、不验增益**（同配置跨轮波动 ±2 题 = ±33.3pp，见
  `ghx-l5-landing-chain` 记忆）。任何分数变化本轮都不作数。

## 7. 真床探针（2026-08-14，`M12_probe2`，2 题 × 1 轮，$1.98）

上线前先在真床上打了两题，确认离线用 MockProvider 验的机制在 GAIA 全套处理器下
同样成立。**成立。**

| | `72e110e7` | `d0633230` |
|---|---|---|
| U 节点 / 边 | 1135 / 1526 | 651 / 747 |
| **消息面数据边** | **480** | **147** |
| slot 面数据边 | **0** | **0** |
| 锥（仅终末锚点） | **8** | **8** |
| 锥（M12 锚点） | **422** | **251** |
| 锥跨越步数 | **21** | **12** |

三件事同时被证实：

1. **slot 面确实恒空**——根因判断在真床上直接坐实，不是从小样本外推的。
2. **仅终末锚点仍然恰好是 8**，与 `GHX-L2-EMPTY-CONE-ROOT-CAUSE.md` 记的退化锥
   完全一致：数据面修好了，锚点不改照样一个节点都到不了（§4 的论证成立）。
3. 锥里出现**非处理器节点**：`model:deepseek-v4-flash`、`tool:WebSearch`、
   `tool:WebFetch`，失败那题还多出 `tool:Bash`、`tool:Browser`。

### 验收标准更正（我先前引的 0.418 是废数）

M11 那个 **A=0.418** 是**用坏了的度量算出来的**——按 `#` 切 id 取基名，而 id 的格式是
`{static_node_id}@t{ordinal}`，根本没有 `#`，等于在比较序号。build log 2264 行已自我
更正过，改按 `@t` 切之后重算：

> **A（现状）：mean Jaccard = 1.000。103 个任务的锥去掉序号后只有 1 个互不相同的
> 处理器集合——锥不是任务的函数，信息量 0 比特。** C（跨 firing 边）=0.799。

所以对照系是 **1.000**，不是 0.418。**本记录先前写的"必须掉到 0.418 以下"作废。**

探针两题的静态节点集 Jaccard = **0.846**（13 个 vs 11 个节点，共有 11 个）。
比 A 的 1.000 低，与 C 的 0.799 同量级。**但这是 n=2 的单一配对，不足以下结论**——
区分度全部来自工具集差异（失败那题多用了 Bash 和 Browser），八个处理器 + 模型节点
在两题里完全相同。6 题给 15 个配对，那才是能读的数。

**仍未验**：15 配对的均值是否稳定低于 1.000 且低到有用；`facts.md` 的共有节点表在
有了 `tool:` / `model:` 词汇后是否让 evolver 不再造 `dangling_endpoint` 端点。
后者是 L5 的事，本轮不测。

## 8. L2 6×3 收口（`L2_msgplane6x3`，$65，53 分钟）

分数 3/6 → 4/6 → 4/6，**不读**（±2 题 = ±33.3pp 就是全部量程）。三轮都 ship：
R1 两个候选（processor + prompt，Critic 明确查过桶不冲突），R2 一个。

### 锥区分度：三轮稳定

15 配对 × 3 轮，同一批锥换投影重算（对照系 A = 1.000，锥是常函数）：

| 投影 | R0 | R1 | R2 | 均值 |
|---|---|---|---|---|
| 静态节点（`facts.md` 现用） | 0.851 | 0.892 | 0.846 | **0.863** |
| 静态节点 + hook | 0.933 | 0.952 | 0.932 | 0.939 |
| 只看工具 | 0.513 | 0.626 | 0.473 | 0.537 |
| 数据边签名（写者→读者） | 0.612 | 0.700 | 0.577 | 0.630 |
| **数据边 + 跨步距离** | 0.331 | 0.364 | 0.288 | **0.328** |

**不是单轮偶然**：两端在三轮里各自稳在 0.85±0.02 和 0.33±0.04。结论照旧——
信息在锥里，是投影扔的。

### 一条干预证据：改进流水线会反过来劣化证据通道

**R1 每一个投影都比 R0 和 R2 更不区分**（0.851→0.892，0.331→0.364，五行同向）。
原因是 R1 ship 了一个新处理器 `ToolResultClarityGuard`，节点集从 11–13 涨到 12–14。
**多一个处理器 = 多一个恒亮的名字**，静态投影只会更饱和。

这是饱和诊断的干预验证，不只是观察：**evolver 每改进一次流水线，指导它的那条证据
通道就更钝一分**。这条反馈病理必须写进威胁节。

### 消费情况：只有 Planner 读，而且读得对

| 角色 | R1 | R2 |
|---|---|---|
| Planner (`landscape.md`) | 2 处引用 | 2 处引用 |
| Digester | **0 / 6** | **0 / 6** |
| Critic (`decision.md`) | 0 | 0 |

Planner 的原文值得抄下来——它**独立复现了本记录 §7–8 的两条结论**：

> *"the **same static cone nodes appear in all three failing tasks**: `model:...` and
> every processor... The tool set differs — `0b26` and `851e` share `tool:Bash`;
> `0b26` and `48eb` share `WebFetch`/`WebSearch` — matching that these are genuinely
> different tool modalities... pointing at the **same silent-output-drop fault**,
> which is upstream of the tool layer."*

它**主动跳过了饱和的那部分，只拿工具集差异当信号**，并据此推出"故障在工具层上游"——
正是唯一带信息的那个维度。同一段里它还说：

> *"The cones' 'trajectory steps that causally mattered' are essentially the whole
> run (steps 0–20)"*

——**步指针饱和这件事，是消费者自己说出来的**，不是我从外面测的。

Digester 三轮零引用，与既有 L2 审计一致（806 份里 75 份引用、31 份只是复述）。
锥文件 523–915 行、32 倍重复，逐任务读的角色读不动它，是可以预期的。
