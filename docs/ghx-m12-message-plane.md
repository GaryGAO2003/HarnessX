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
- **锥会变大多少、跨任务还分不分得开——未测。** 验收标准沿用 M11 离线已定的硬指标：
  失败任务两两之间的锥 Jaccard **必须掉到 A 基线 0.418 以下**，且锥中出现非处理器
  节点（工具、模型）。这要在真床上读，是下一步 6×3 的唯一目的。
- 6 题床**只验机制、不验增益**（同配置跨轮波动 ±2 题 = ±33.3pp，见
  `ghx-l5-landing-chain` 记忆）。任何分数变化本轮都不作数。
