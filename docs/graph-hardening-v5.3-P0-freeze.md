# v5.3 P0 — 语义冻结与迁移说明

> 核验日期 **2026-08-09**（分支 `ghx/main`）。本文件是 P0 交付物之一，冻结
> hash / API / hook / 注册项语义，并记录编码前必须锁定的命名冲突。P1–P7 不得
> 自行改名或改变这些含义；语义变更须先改本文件并过评审。
>
> 配套交付物：`tests/graph/fixtures/`（注册/配置构造器）、
> `tests/graph/test_contract_matrix.py`（I1–I10 契约矩阵）。
> 规格正文见 `docs/graph-hardening-v5.3.md`；实施顺序见
> `docs/graph-hardening-v5.3-engineering-plan.md`。

---

## 0. P0 铁律回顾

P0 **不写任何功能代码**：不改 `harnessx/` 下任何 .py 的运行时行为。本轮只产出
测试 fixtures、契约矩阵、本冻结文档。下列所有"现状/实况"均为**只读核验**结果，
不附带任何代码修改。

---

## 1. 四类注册项状态转换（冻结）

统一入口 `normalize_processor_reg()`（`harnessx/core/runtime.py:153`）是唯一写
规范化点，四类输入各自的目标状态：

| # | 源形态 | 归一为 | 入口 | 说明 |
|---|--------|--------|------|------|
| 1 | `dict`（含 `_target_`） | `SerializedReg(dict_ref=dict)` | `normalize_processor_reg` | presence/value 为 `dict_ref` 动态 property，不缓存 |
| 2 | 裸 processor 实例 | `RuntimeReg(proc=inst, …)` | `coerce_runtime_reg` | 元数据由 `get_graph_metadata(inst)` 自然派生；无 `_hook` 的 MHP → `"*"` |
| 3 | `SerializedReg`（实例化后取自然元数据） | `RuntimeReg` | `coerce_runtime_reg(inst)` | L2.3c 规则 4：缺失字段用实例化后自然元数据回退 |
| 4 | `RuntimeReg` | `RoutingEnvelope(reg, seq)` → routed 裸 processor | `_route_processors` / `stable_topological_sort` | 排序后**输出裸 processor**；runloop 只执行裸 proc（I5） |

**单源真相**：`HarnessConfig._processor_regs` 是唯一可写注册序列
（`tuple[SerializedReg | RuntimeReg, …]`，下标即 seq）。`processors` 与
`_rt_procs` 为只读派生视图；写入只能经 `add_runtime_reg()` /
`replace_processor_regs()`（均走 `normalize_processor_reg`）。

---

## 2. 四种边界政策（冻结）

| 情形 | 政策 | 现状落点 |
|------|------|---------|
| **可恢复的缺失字段**（key 缺失，presence=False） | natural fallback：实例化后取自然元数据 | `SerializedReg.*_present` + `coerce_runtime_reg` |
| **显式空值**（`_hook_=""` 等，presence=True 但空） | 保留并**停止执行**：空桶 0 边、runloop 不执行（VM14） | `SerializedReg.hook` 返回 `""`；graph `_hooks_=[]` |
| **无法实例化** | 记录原因并**本轮路由剔除**（DROPPED），不崩溃 | plugin 尾部 envelope / seq=max+1（空洞与 `len` 冲突已在规格处理） |
| **冲突 / 非法结构** | **抛异常，不进评测** | `HarnessConflictError`（`runtime.py:18`）；`normalize_processor_reg` 对 None/内建标量抛 `ValueError` |

边界一致性由 `test_contract_matrix.py` 的 I3（显式空 hook 保留）、I8（非法 edit
抛错且原 snapshot 不变）部分锁定；完整 fail-closed 路由为 P1/P3。

---

## 3. canonical 8-hook 冻结 + 旧 10-hook 迁移策略

### 3.1 两个 hook 常量并存，语义不同（**非冲突**）

| 常量 | 定义处 | 长度 | 含义 |
|------|--------|------|------|
| `PROCESSOR_HOOK_NAMES` (= `_HOOK_LIFECYCLE_ORDER`) | `harnessx/core/processor.py:39/45` | **8** | processor 可注册/挂载的生命周期 hook（canonical 单源） |
| `SKELETON_HOOK_NAMES` | `harnessx/graph/types.py:139` | **10** | runloop 的 10 个固定执行相位（graph SKELETON_HOOK 节点骨架） |

关系（已由 I1 断言锁定）：
`set(PROCESSOR_HOOK_NAMES) ⊂ set(SKELETON_HOOK_NAMES)`，
`SKELETON − PROCESSOR == {"model", "tool"}`。

`get_graph_metadata` 在派生 processor `_hooks_` 覆盖集时**过滤掉 `model`/`tool`**
（`processor.py:960-961`：`if h not in ("model", "tool")`）。即 processor 永远只
落在 8 个 hook 上，10 个骨架相位是 runloop 相位（`model`/`tool` 无 processor 挂
载）——二者是**设计并存**，不是同名冲突。

### 3.2 旧 10-hook 快照迁移策略

**Grep 结论（2026-08-09）**：仓库内**未发现**将 processor 注册/序列化到 10 个
hook 的历史快照或序列化产物。
- `git grep _hooks_ -- *.json *.yaml *.yml`：无 graph-snapshot 型产物（仅
  benchmark 任务清单 `swebench_lite_test.json` 等偶发字符串命中，与 graph 无关）。
- `SKELETON_HOOK_NAMES`（10）仅出现在**源码与测试**（`types.py`、`snapshot.py`、
  `graph/__init__.py`、`tests/graph/test_s1_types.py:123`、
  `tests/graph/test_s1_snapshot.py:29`），是当前正确的骨架常量，非"待迁移遗留"。

**因此迁移策略为空操作 + 载入时断言**：
1. 无存量 10-hook processor 产物需要转换（no-op）。
2. 载入/校验时断言 `len(PROCESSOR_HOOK_NAMES) == 8` 且任何 processor 的 `_hooks_`
   不含 `model`/`tool`——`test_contract_matrix.py::test_i1_*` 即此断言样例
   （`SKELETON − PROCESSOR == {model, tool}`），作为迁移守卫，捕获任何未来把
   processor 挂到 `model`/`tool` 的"旧 10-hook"输入。

---

## 4. Hash 命名冻结表 + 语义变更警告

### 4.1 冻结命名

| 名称 | 冻结语义 | 当前代码实况 | 状态 |
|------|---------|-------------|------|
| `genotype_hash` | 持久 graph nodes/edges（结构） | `identity.py:22`，`_canonical_form(include_observed=False)`，只读 `snapshot.nodes/edges` | ✅ 与冻结语义一致 |
| `deployment_hash` | genotype + runtime overlay + deployment 边 | **字段存在**（`types.py:117`）但 **identity.py 无计算函数**；`apply_edits` 不清此字段（`edit.py:100-101` 只清 genotype/phenotype） | ⚠️ 未实现，P2 补 |
| `phenotype_hash` | **deployment** + observed 边 | `identity.py:35`，当前 = **genotype** + observed（`include_observed=True` 只读 `snapshot.edges`，**不含** runtime overlay） | 🔴 同名不同物（见 4.2） |
| `ir_observed_hash` | "仅 observed IR" 保留名 | 不存在 | 🔒 保留，禁止复用 `phenotype_hash` 承担此职责 |

### 4.2 🔴 头号语义变更警告（P2 前后 `phenotype_hash` 不可跨版本比较）

> **`phenotype_hash` 当前 = genotype + observed；冻结目标 = deployment + observed
> （deployment = genotype + runtime overlay + deployment 边）。**
>
> - P2 会把 runtime overlay（`runtime_nodes`/`runtime_edges`）并入 phenotype 计算，
>   **P2 之前产出的 `phenotype_hash` 值与 P2 之后不可跨版本比较**——凡持久化了
>   phenotype 值的 ledger/缓存，跨 P2 边界须失效重算，不得直接相等判断。
> - `ir_observed_hash` 名称为"仅 observed IR"保留：未来 GS 执行图哈希**禁止**复用
>   `phenotype_hash` 这个名字（否则重演"同名两义"）。
> - 迁移安全：P2 若改 hash 语义，恢复旧命名 alias、不删旧数据（工程计划 §0.4 回滚
>   条款），失败可回退。

---

## 5. P0 已核实实况清单（2026-08-09）

### 5.1 任务简报 5 条实况的复核

1. **`replace_runtime_regs` / `replace_processor_regs`**：简报称"两者在代码中完全
   不存在" —— **与实况不符**。`replace_processor_regs`（新名）**存在**于
   `harness.py:899`，`add_runtime_reg` 存在于 `:890`；旧名 `replace_runtime_regs`
   确实**不存在**。⇒ P0 工作项 1 的"改名"**已完成**（新名在、旧名不在），无需
   创建函数、无需兼容 wrapper。结论（不创建函数）不变，但理由需更正。
2. **`harnessx/core/runtime.py` 已存在（343 行）**：✅ 复核属实。含
   `HarnessConflictError`、`RuntimeReg`、`SerializedReg`、`RoutingEnvelope`
   (`:116`)、`coerce_runtime_reg`、`unwrap_runtime_proc`、`normalize_processor_reg`、
   `stable_topological_sort`、`claim_owners`/`release_owners`。
3. **`identity.py` 只有两个 hash，无 `deployment_hash` 函数**：✅ 属实（见 §4）。
   补充：`GraphSnapshot` **已有 `deployment_hash` 字段**（`types.py:117`），但无
   计算它的函数——字段与实现不一致，是 P2 缺口。头号命名冲突（phenotype 同名不
   同物）确认成立。
4. **`processor.py:39-45` canonical 8-hook tuple**：✅ 属实。
   `PROCESSOR_HOOK_NAMES is _HOOK_LIFECYCLE_ORDER`（同一对象），I1 已断言。
5. **`tests/graph/` 无 `fixtures/`、无 `test_contract_matrix.py`**：✅ 属实（本轮
   已创建）。

### 5.2 编码核验中额外发现的规格↔代码不符

- **I7 已部分落地（优于规格默认预期）**：graph 侧 EXECUTES_BEFORE 链
  （`snapshot.py:558`）已 `from ..core.runtime import stable_topological_sort` 并
  调用它；但**runtime 路由/builder 仍用自带的 `builder.py:678` 排序**，二者尚未
  "同源"。⇒ I7 判为 SKIP（阻塞于 P1 把 builder 迁到共享 sorter，round-8 item 34）。
- **`SerializedReg` 动态 property 已实现（优于规格默认预期）**：`runtime.py:66-112`
  已是 `dict_ref` 动态读取 + presence flags。⇒ I3 的**单元切片可今日真验**（本轮已
  真验）；端到端 VM20b（graph/runtime 同源同值读取）仍待 P2。
- **纯 serialized `to_graph` 不 import `_target_`（优于 P2 退出条件的当前状态）**：
  `snapshot.py` 无 `importlib`/`import_module` 调用，只把 `_target_` 当字符串读。
  ⇒ fixtures 用任意 dotted target 均安全过 `to_graph`。
- **I9 gate 当前 fail-open（劣于不变量意图）**：`experiments/variant_pool/graph_gate.py:145-154`
  把 `ImportError` 与泛 `Exception` 降级为 warning（仅 `HarnessConflictError`
  fail-closed），build 失败的候选仍 `passed=True`。⇒ I9 判为 SKIP，阻塞于 P3
  fail-closed（工程计划 §P3 明列此降级行为为待修点）。

---

## 6. I1–I10 契约矩阵处置一览

见 `tests/graph/test_contract_matrix.py`。每条 docstring 首行为不变量原文。

| ID | 处置 | 依据 / 阻塞项 |
|----|------|--------------|
| I1 | **真验** | 值+顺序；`snapshot.PROCESSOR_HOOK_NAMES is core.PROCESSOR_HOOK_NAMES`；10↔8 骨架关系 |
| I2 | **真验** | `_processor_regs` 唯一可写；`processors`/`_rt_procs` 视图；`add_runtime_reg`/`replace_processor_regs` 写 API |
| I3 | **真验**（单元切片） | `SerializedReg` dict_ref 动态重读 + presence flags；端到端 VM20b 待 P2 |
| I4 | **真验** | `RuntimeReg` frozen，构造不改 `proc.__dict__` |
| I5 | **真验**（2026-08-09 块 15 解锁） | 路由结果全裸 proc（record/envelope 不进执行面）；record 的 proc 按 reg.hook 落桶（身份相等）；serialized 每次实例化全新实例 |
| I6 | **真验** | 含/不含 runtime reg 的两配置 `genotype_hash` 相等；runtime proc 仅进 `runtime_nodes` |
| I7 | **真验**（2026-08-09 块 15 解锁） | 同一配置双侧对照：EXECUTES_BEFORE 链节点序 == 路由桶实际序（order → after 拓扑 → seq；含 mixed S+R 方向） |
| I8 | **真验** | 非法 edit 抛 `GraphEditError`，原 snapshot 的 nodes/edges/genotype 不变 |
| I9 | **skip** | 阻塞 P3 fail-closed gate：现状 `graph_gate.py:145-154` fail-open |
| I10 | **真验** | 追加 OBSERVED_* 边后 `genotype_hash` 不变、`phenotype_hash` 改变 |

**无 xfail**：真验的 9 条均通过，未发现"当前代码实际违反已实现不变量"的情形。
I9 的 fail-open 属"P3 尚未实现的强制"，其正测为集成测试，故判 SKIP 并在 §5.2
记录该 gap（而非 xfail 一个需引入 experiments 依赖的用例）。
（原 I5/I7 的 skip 判定与理由见 git 历史；块 15 落地 `_instantiate_runtime`
canonical 单次消费 + `_route_processors` 共享 sorter 后解锁。）
