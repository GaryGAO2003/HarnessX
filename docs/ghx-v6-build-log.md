# GHX v6 构建日志

分支：`ghx/v6-graph-runtime`（从 `ghx/v5.3-core-runtime` @ `b289b31` 开出）

本文件是 v6 构建的流水账。每个模块完成后追加一节：改了什么、测了什么、遇到什么、裁决了什么。
小问题记在「小账」里不阻断推进；重大问题触发回滚并在「回滚记录」里写明。

---

## 开工裁决

在计划里挂着的三个开放项，用户授权我自裁，取值如下：

| 项 | 取值 | 理由 |
|---|---|---|
| 迁移路径 | desugar-then-replace | config→图编译保留，图变成被执行的东西；线性 runloop 留在 `GHX_RUNTIME` 旗标后面直到 parity 绿 |
| 代码桶（`tools`/`processor` 真代码编辑） | **不补** | 超出 v6 范围；作为 SPEC 偏差表的显式条目记录 |
| guard（条件）边 | **不做** | 超出论文，且要同步扩门；v6 之后再议 |

---

## M0 — 把 graph/core 测试接进 CI

**commit**：`b289b31`（落在 `ghx/v5.3-core-runtime`，不在 v6 分支上——它修的是既有缺陷，不是 v6 功能）

**改动**：`.github/workflows/ci.yml` 增加一个 step，跑 `pytest tests/graph tests/core -x -q`。

**为什么**：CI 原先只跑 `tests/unit` 和 `tests/integration`。`tests/graph/` 底下 10 个文件、`tests/core/` 2 个文件，合计 **705 个测试从来没被 CI 跑过**。v6 整个建在 graph 子系统上，这个洞会变成承重的。

**验证**：本地 `705 passed in 9.80s`。

**没接 `tests/e2e`**：需要真 provider 和网络，进 CI 会因缺 key 挂掉。该走单独的手动 workflow。

---

## D1 — 三笔欠账

### (2) `docs/agents.md` 约束 7 —— 已修，`a0336f5`

原文说「yields nothing 会阻断后续处理器**和 hook 动作本身**」。后半句是错的。

**比原先估计的更广**：这不是 `before_tool` 一处的问题。`runloop.py` 的**全部 9 个分发点**用同一个回退模式：

```
runloop.py:207 / 328 / 397 / 438 / 519 / 576 / 615 / 682 / 845
    next((e for e in reversed(_events) if isinstance(e, XEvent)), <pre-hook event>)
```

链子吐空 → 拿到原始事件 → 动作照跑。真正能拦住东西的是**吐一个改过的事件**：`ToolCallEvent(approved=False)`（`runloop.py:546` 才是真门），或者吐一个不同类型的事件短路（`task_start` 处理器吐 `TaskEndEvent` 结束整个 run，见 `runloop.py:211`）。

按旧文档写否决权的人，会得到一个静默失效的否决。`docs/agents.md` 的「What NOT to do」也加了这一条。

### (3) `run_variant_pool.py:5476` 过期 docstring —— 已修，`a0336f5`

原说 prompts 是「OURS reconstructions」。prompt 出处是**另一个轴**，由 `aegis_prompts` 带，默认 `"paper"`。

### (1) `_critic_adapter_name` 审计不实 —— 施工中

**比原先记的更严重：三个角色同病，不是 Critic 一个。**

| 角色 | 静默回退点 | 名字来源 |
|---|---|---|
| Digester | `:3202-3209` 捕获 `_DigesterWholesaleFallback` + 裸 `Exception` | `:5390` 读 config |
| Planner | `:3728-3735` 同模式 | `:5426` 读 config |
| Critic | `:4176-4183` 同模式 | `:5462` 读 config |

三个 `_wholesale_fallback` 都会在 provider 报错或 JSON 连续解析失败时**静默换成确定性实现**，但 `:5998-6010` 写进审计的名字来自 `self.aegis_* == "llm"`，即**配置**。回退发生时那一轮的账仍自称 `MetaModel_llm_critic`，`llm_aegis_reproduction` 仍是 `true`。

**这直接挡 6×3**：分不出「真三角色 LLM 轮」和「两个角色悄悄降级的轮」。

修法：适配器自己记每次调用走了哪条路（LLM / 回退 / Digester 特有的 no-target 第三态），四个属性改读执行记录；审计增加 `fallbacks` 块带每角色计数与原因，让**部分降级**可见而不是被压成一个布尔。Digester 是逐任务调用，所以要计数不要旗标——6 题里 5 题走 LLM、1 题回退，账上必须看得出来。

---

## 地基勘察（M1/M2 施工前）

### 哈希契约 —— 推翻了 M1 的原设计

三个哈希都在 `harnessx/graph/identity.py`，共用一个核：

| 哈希 | 位置 | 吃什么 |
|---|---|---|
| `genotype_hash` | `:23-35` | 只吃 `nodes` + `edges` |
| `deployment_hash` | `:38-52` | 并入 `runtime_nodes` / `runtime_edges` |
| `phenotype_hash` | `:55-68` | 再并入 observed 边 |

序列化：节点按 key 排序、边列表字典序排、`json.dumps(sort_keys=True, separators=(",",":"))` 后 SHA-256（`:104-132`）。

**关键事实**：`_node_canonical` 在 `:139` **只 pop 掉 `_code_hash` 一个 key**，其余 metadata 全部进摘要。边 metadata 更是一个不漏（`:157`）。

**后果**：往持久化节点加任何 metadata key（包括我原计划的 `runtime_kind`）会改掉**全部既有配置的 genotype_hash**。这违反我自己写的 M1 判据。

**裁决：砍掉 `runtime_kind`。** 它本来就是冗余的——`NodeType` 已经区分 PROCESSOR / SKELETON_HOOK / SLOT；而 `SKELETON_HOOK_NAMES`(10, `types.py:192`) 与 `PROCESSOR_HOOK_NAMES`(8, `processor.py:39-45`) 的差集**正好就是** `model` 和 `tool`。信息已在结构里，加 key 是零收益换全量哈希漂移。

M1 范围相应改成：`INVOKES` 边类型 + `v@t` id 规约 + **把这条哈希约束变成钉子测试**（后面每个模块都受它管，必须让它响，而不是被重新发现一遍）。

### 分发与排序 —— M2 的地基

- **9 个分发点，8 个不同 hook**：`after_tool` 在两处触发（`:570` 真实结果 / `:609` 合成结果）。
- `get_procs(key)` = `_star_procs + processors.get(key)`（`runloop.py:156-158`）——`"*"` 桶**前置**到每个 hook。M2 的执行器必须复刻这个前置，光读 hook 桶是错的。
- **排序在 build 期和实例化期各解一次，分发期零重排**：
  - build：`builder.py:297` → `_topological_sort_entries`，按 `_order` 再对 `_after` 做 Kahn
  - 实例化：`harness.py:421-427` → `stable_topological_sort`，键 `order → after → singleton_group → seq`
- **对 parity 是好消息**：图上的 `EXECUTES_BEFORE` 链用的是**同一个** `stable_topological_sort`。图里已经编码了同一个序，M2 不是去重新发明顺序，是去读它。

### 小账 · 未触发

`snapshot.py:532-549` 对 `model`/`tool` 的封死**只对通配符 `"*"` 严密**：`"*"` 展开成 `PROCESSOR_HOOK_NAMES`(8) 排除掉了它们（`:536`）。但**显式**写 `hooks=["model"]` 会命中 `:537` 的 `SKELETON_HOOK_NAMES` 分支被放行，挂上 ATTACHED_TO 边。

今天无害：runloop 从不分发 `model`/`tool` hook，那条边是惰性的。全仓 grep 确认**没有任何处理器这么声明**，属于潜伏未触发。

留给 M2：执行器要么显式拒绝这类边，要么在校验层拦掉。记在这里以免又被重新发现。

---
