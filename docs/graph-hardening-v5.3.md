# HarnessX Core ↔ Graph 加强规格 — v5.2

> v5.2 (2026-08-08): 八轮修正，共 37 项：
> **第一轮（v5.1 → v5.2）：** 1–6
> **第二轮（增量审计）：** 7–13
> **第三轮（语义修正）：** 14–16
> **第四轮（窄 blocker 闭合）：** 17–19
> **第五轮（可执行性缺口闭合）：** 20–23
> **第六轮（可执行性闭合二）：** 24–27
> **第七轮（单源真相闭合）：** 28–31
> **第八轮（写 API 规范化 + 执行顺序编码）：** 32–37
>
> 1. Builder 覆盖规则不可执行 → 无条件使用 _ProcEntry 解析值
> 2. Runtime-only 丢失注册元数据 → 完整绑定；冲突注册抛 ValueError
> 3. Hook/非 MultiHook 推导不完整 → MRO 遍历；非 Multi 分支读取全部字段
> 4. ComponentDecl 构造后 mutation → 累积-一次性构造模式
> 5. Runtime slot 污染 genotype → 移入 runtime_nodes；genotype_hash 隔离
> 6. Hash 正则化不闭合 → `at_root` 上下文 + 共享 `_hash_nodes_edges` 内核
> 7. 显式空 hooks 被 WKD 回退覆盖 → `hooks_present` 键存在性标志
> 8. _hook MRO 清空语义错误 → `"_hook" in base.__dict__` 检测；空值停止遍历
> 9. entry.hook="*" 未持久化 → `_hook_` 始终写 entry.hook；`_hook_`/`_hooks_` 语义分离
> 10. Runtime metadata 污染共享实例 → 完整四元组冲突检测
> 11. Runtime ID 不符 VM8 → `_compute_slug` 拆分；仅遍历 PROCESSOR 节点
> 12. deployment_hash 未写缓存 → 三 hash 统一缓存 + 返回对象清空
> 13. WKD ProgressiveSkillLoader + Blocker 表过时 → 补齐 `task_end`；修正描述
> 14. **Config-owned registration record**：Runtime 注册不可变，由 config 持有 `_RuntimeReg`，不修改共享 processor 实例
> 15. **`"*"` 桶与 handler coverage 彻底分离**：`_hooks_` 永不含 `"*"`；MHP 从 dispatch 推导；只有具体 hook 才收缩 `_hooks_`
> 16. **统一 graph invariant**：canonical 8-hook tuple 单一来源；`apply_edits` 返回前验证 edge 端点；`_sort_dict` 用 `at_root` 标志，嵌套层绝对保序
> 17. **Hook 契约闭合**：MHP natural `_hook_` = 最近非空类 `_hook`（无则 `"*"`）；legacy `_hook_=""` → `hooks=()` 且 `hooks_present=True`（禁 WKD 回退）；ComponentDecl 排序统一引用 `PROCESSOR_HOOK_NAMES`
> 18. **`RuntimeReg` 完整接入运行时**：放入无循环依赖的 `harnessx/core/runtime.py`；`coerce_runtime_reg()` / `unwrap_runtime_proc()`；迁移全部 `_rt_procs` 消费者；`_route_processors` 按 reg.hook 分桶并执行 reg.proc；兼容直接注入的裸 processor
> 19. **单 owner fail-fast**：record 只隔离注册元数据；Harness 绑定仍写实例状态（harness.py:1069-1078）→ 第二个 Harness 绑定同一实例时抛 ValueError；并行复用需 factory/clone
> 20. **非 MHP `"*"` 闭合**：runloop 在 8 个 processor hook 执行 wildcard 桶（runloop.py:154 `_star_procs`，调用点 206/321/393/436/515/572/611/681/844）→ 非 MHP + `"*"` 的 `_hooks_` = `PROCESSOR_HOOK_NAMES`（三态规则：具体 hook → 单元素；MHP+"*" → dispatch 推导；非 MHP+"*" → 8 个）；coerce 裸实例默认桶 `"*"`
> 21. **统一 routing envelope**：`RoutingEnvelope(reg, seq)` 承载排序元数据；`_route_processors` 排序后输出裸 processor（第六轮第 24 项将 seq 来源升级为 `_processor_regs`，排序升级为 order → `_after_` 拓扑 → seq）
> 22. **Owner claim 安全**：不可复用 token（非 id）；只 claim `_rt_procs` 去重实例；锁内两阶段"全量检查→全量 claim"；位置早于 sub-harness 创建；构造失败回滚；cleanup 完整成功后才释放
> 23. **Hash/消费者补缺**：`phenotype_hash` = deployment + observed（合并 runtime overlay）；`harness.py:864 required_model_keys` unwrap；`spawn_subagent.py:541` 用 `dataclasses.replace(reg, proc=new)` 保留四元组；tau2/gaia 渲染读取 unwrap
> 24. **config-owned 统一注册序列 `_processor_regs`**：`__post_init__` **分离前**固化 `tuple[SerializedReg | RuntimeReg, ...]`（下标即 seq）；`_instantiate_runtime` 只消费它（不再双循环 config.processors + _rt_procs）；桶内排序复用 Builder 规则：order → `_after_` 拓扑 → seq 破平；SerializedReg 用键存在性读 `_hook_`（不写 "override or natural"）
> 25. **canonical hook 常量移到 core**：`PROCESSOR_HOOK_NAMES = _HOOK_LIFECYCLE_ORDER` 定义在 `core/processor.py`；Builder 与 graph/declaration 均从 core 导入（消除 core → graph 反向依赖与运行时 NameError）
> 26. **身份 registry owner claim**：`id(proc) → (strong_ref, token)` 锁保护 registry（不依赖 hash/eq，防 id 复用）；claim 覆盖 `_rt_procs` + `extra_processors`（harness.py:1013，参与 `_bind_*`）+ 复用型来源；构造失败只回滚本 token；cleanup 所有 await 完成后再释放，取消后允许重试
> 27. **迁移/warning 链补齐**：`benchmarks/tau2/agent.py:299` isinstance + append 裸实例纳入迁移；trajectory_digester 替换**必须** `dataclasses.replace(reg, proc=new)`（删"或放裸实例"）；`_runtime_reason` 无生产者 → 删除 L7.4 warning 承诺
> 28. **单源真相**：`_processor_regs` 唯一可写真相；`_rt_procs` 改只读派生视图（property）；`add_runtime_reg()` / `replace_runtime_regs()` 写 API；迁移全部写入端（digester:160、tau2:303、copy、canonicalize）；`_instantiate_runtime` 只消费 canonical 一次，**不再追加 `_rt_procs`**
> 29. **copy/canonicalize/spawn 保留混合顺序**：copy() 无 `processors=` override 时原样浅拷贝 canonical（不再用纯 dict 重跑）；canonicalize() 直接转换 canonical 序列（dict 去重 + RuntimeReg 保留）；spawn_subagent 直接转换 `_processor_regs`
> 30. **SerializedReg presence flags**：`hook_present` / `order_present` / `sg_present` / `after_present` 区分"缺失"与"显式 falsey"；缺失 → 实例化后自然元数据回退；显式空 hook → **空桶不执行**（VM14 成立，不再走默认推断）
> 31. **plugin 尾部 envelopes + edit.py 迁移**：plugin 处理器收集为尾部 envelopes（seq=max+1）统一路由与 claim（harness.py:696-706）；`edit.py:132` 迁移 `_compute_slug`（`f"proc:{_compute_slug(target)}"`）；机械修正：runtime.py 补 `import threading`；拓扑排序器泛化适配 `_ProcEntry`/`RoutingEnvelope`；函数名实为 `_instantiate_runtime`
32. **写 API 规范化**：新增唯一 `normalize_processor_reg()`（SerializedReg/RuntimeReg/dict/裸实例，拒绝 None — coerce 会把 SerializedReg 误包装成 processor）；`replace_runtime_regs` 更名 `replace_processor_regs`（必须传完整混合序列）；`_rt_procs` 返回 **tuple**（list 视图 `.append()` 静默无效）；`copy(processors=...)` 六调用点迁移（cli.py:367/489/1068、api/routes/run.py:316、gateway/main.py:173/198 — 全部从 view 重建、会丢 RuntimeReg）
33. **SerializedReg presence/value 改 dict_ref 动态 property**：不缓存解析值（dict 被外部共享，缓存造成 graph 读新值、runtime 读旧值的分叉）；`_instantiate_runtime` 统一 natural 回退（`natural = coerce_runtime_reg(inst)`，`value = explicit if *_present else natural.*` 四字段一致 — after 不再硬编码 `()`、order/sg 含实例级覆盖、无 hook 裸实例默认 `"*"`）；显式 hook="" → 空桶 + runtime graph `_hooks_=[]`
34. **sorter 补 group_key + plugin/owner 闭合**：`stable_topological_sort` 完整抽取 builder.py:678 语义（group-map 解析 after 组名、跨 order 冲突、同 order cycle、soft deps 忽略）；`HarnessConflictError` 迁入 runtime.py（builder 再导出，无反向依赖）；plugin 尾部 seq = `max(seq)+1`（DROPPED 空洞与 `len(flat)` 冲突）；plugin id 身份去重保留（:700-706 语义）；owner claim 改对**最终路由 processors 中全部 MHP 一次覆盖**（canonical + plugin + extra，claim 面 = `_bind_*` 绑定面）
35. **cleanup 屏蔽状态机**：`_cleanup_task` + `asyncio.shield`；`_sandbox` 等资源引用只在 impl 内 await 完成（finally）后清除（:1390-1391 旧行为取消即丢引用）；owner 最后释放；调用方取消 → 底层清理继续，重试 await 同一 task（VM17 改写）
36. **执行顺序编码**：新 `EdgeType.EXECUTES_BEFORE`；persistent-relative 链 → 主图 edges（genotype，L4.6）；完整 mixed 有效链（含实例插件，seq=max+1）→ runtime_edges（deployment/phenotype，L5.6）；dict 插件不可枚举 → 收窄契约 + warning
37. **新增验收**：VM19（S,R↔R,S 同桶 hash 差异、持久序链、冲突传播、star 桶契约、apply_edits 陈旧性）+ VM20（S-R-S-R 经五变换不变、四字段 missing/falsey、plugin 空洞与重复、cleanup 逐 await 点取消、hash 差异）

## 设计原则（3 条，治理全部后续规则）

### P1: 派生，不复制

Graph 元数据在 `to_graph()` 时从源头实时派生，不维护平行 sidecar 数据结构。

- hook 集合 → 从 `cls._DISPATCH` + MRO 派生（不来自预存 `_hooks_` 列表）
- slot I/O → 从 `cls._writes_slot_keys` / `cls._reads_slot_keys` 类属性读取
- runtime 处理器 → `to_graph()` 直接内省实例的类，不依赖 `_rt_procs_meta` sidecar
- 唯一的例外：`_target_` dict 中的序列化元数据键（`_order_`、`_singleton_group_`、`_after_`）可被 Builder 预写入，以加速 graph 导出并避免重复导入类。但这些键必须是源头信息的精确副本，不能偏离。

### P2: 键存在性区分"显式空"与"未声明"

- `"key" in dict` 判断键是否存在
- 键存在且值为 `0`、`""`、`[]`、`()` → 显式空/零，不回退到任何 fallback
- 键不存在 → 未声明，可回退到 WKD 或类内省
- **绝不用** `bool(dict.get(key))`、`if not decl.field` 等 truthiness 判断

### P3: 生命周期序

Hook 名称在任何集合/列表中始终按 runloop 执行顺序排列，永不用字母序：

```
task_start → step_start → before_model → after_model
→ before_tool → after_tool → step_end → task_end
```

---

## 分层规格

### L1: Processor 类属性（源头）

**文件**：`harnessx/core/processor.py` — `MultiHookProcessor`

**L1.1 事件→hook 映射**（模块级常量，放 `processor.py` 顶部）：

```python
_EVENT_TO_HOOK_NAME: dict[type, str] = {
    TaskStartEvent: "task_start",
    StepStartEvent: "step_start",
    BeforeModelEvent: "before_model",
    ModelResponseEvent: "after_model",
    ToolCallEvent: "before_tool",
    ToolResultEvent: "after_tool",
    StepEndEvent: "step_end",
    TaskEndEvent: "task_end",
}

_HOOK_LIFECYCLE_ORDER: tuple[str, ...] = (
    "task_start", "step_start", "before_model", "after_model",
    "before_tool", "after_tool", "step_end", "task_end",
)
```

**L1.1a 统一 canonical 8-hook tuple**（定义在 **core**，所有消费方从 core 导入）：

`PROCESSOR_HOOK_NAMES` 与 `_HOOK_LIFECYCLE_ORDER` 是同一对象的两个名字，**定义在
`harnessx/core/processor.py`**（L1.1 代码块之后）：

```python
# core/processor.py — 唯一 canonical 来源（公开名，全部消费方从 core 导入）
PROCESSOR_HOOK_NAMES: tuple[str, ...] = _HOOK_LIFECYCLE_ORDER
```

- Builder（`core/builder.py`）、graph/declaration.py、`get_graph_metadata`（同模块）全部
  ``from harnessx.core.processor import PROCESSOR_HOOK_NAMES`` —— 不允许 core → graph
  反向依赖，也不允许在 graph 侧另起名字。
- 生命周期排序（R6 / ComponentDecl `__post_init__`）、wildcard 展开（`hook_name == "*"`）、
  `SKELETON_HOOK_NAMES` 的子集关系均引用此 tuple。

**L1.2 新增/正式化类属性**（加在 `MultiHookProcessor` 类体上，紧接 `_DISPATCH` 之后）：

```python
class MultiHookProcessor:
    _DISPATCH: dict[type, str]  # 不变

    # 正式化（当前通过 getattr 隐式使用）
    _hook: str | None = None
    _order: int = 0
    _singleton_group: str | None = None
    _after: tuple[str, ...] = ()   # 类型从 list 改为 tuple

    # 新增 graph 数据依赖声明
    _writes_slot_keys: tuple[str, ...] = ()
    _reads_slot_keys: tuple[str, ...] = ()
    _reads_event_fields: tuple[str, ...] = ()
    _writes_event_fields: tuple[str, ...] = ()
```

**L1.3 `compute_effective_hooks(cls) → tuple[str, ...]`**（模块级函数）：

```
R0. 提取共享辅助 `_find_class_hook(cls) → str | None`（`compute_effective_hooks` 与
    `get_graph_metadata` 共用，MHP 的 natural bucket 也由它决定）：
     对 base in cls.__mro__：
       若 base is MultiHookProcessor → 停止（基类本身无 _hook）
       若 "_hook" in base.__dict__：
         若 base.__dict__["_hook"] 非空（truthy）→ 返回该 hook
         否则（``_hook=None`` 或 ``_hook=""``）→ 返回 None（"显式无单 hook"）
     遍历完毕未找到 → 返回 None
     **关键**：子类显式 ``_hook=None``/``""`` 表示"我不要单 hook，从 dispatch 推导"。
     "找到空值"与"未找到"殊途同归 → 均返回 None，进入 R2。
     语义与 builder.py:121 ``getattr(cls, "_hook", None)`` 一致 — 子类空值不得继承父类 hook。

R1. `h = _find_class_hook(cls)`；若 h 非空 → 返回 `(h,)`。否则继续 R2。

R2. 收集 cls（及其所有祖先，除 MultiHookProcessor 自身）覆写的事件类：
    对 cls._DISPATCH 中每一对 (event_class, method_name)：
      遍历 cls.__mro__：
        若 method_name in base.__dict__：
          若 base is MultiHookProcessor → 此 handler 未被覆写，跳过（收集空）
          否则 → 收集 event_class
          break（停在第一个定义此方法的类）

R3. 扫描 cls 及其祖先的 @on() 属性方法：
    遍历 cls.__mro__：
      对 base.__dict__ 中每个 callable(v) 检查 hasattr(v, "_on_event_type")：
      若 v._on_event_type 未在当前结果集中 → 收集
    （@on() 装饰的函数无需重复检查"是否已被 R2 覆盖" —
     因为同一个 event_class 被同时收集也不影响最终 hook 集合。
     但如果 @on() 修饰的方法名与 _DISPATCH 中某 handler 相同，
     则 R3 不应再生成重复的 hook。此情况下 R2 已收集该 event_class，
     R3 的 set 去重自动处理。）

R4. 若 R2+R3 结果集为空 → 使用 cls._DISPATCH 的全部键（继承全部 8 个父类 handler）

R5. event_class → hook_name（_EVENT_TO_HOOK_NAME 映射），跳过不在表中的

R6. 按 _HOOK_LIFECYCLE_ORDER 排序

R7. 过滤：移除 "model" 和 "tool"（处理器从不附着此二 hook）
```

**验证**：`Child(Parent)` 其中 Parent 定义 `on_step_end`，Child 不覆写 → 结果含 `step_end`。Child 新增自己的 `on_before_model` → 结果含 `step_end` AND `before_model`。GrandChild(Child) 其中 Parent 的 `on_step_end` 被 @on() 覆盖 → MRO 找到 GrandChild 新定义。

**为什么用 MRO 遍历而非 `vars(cls)`**：
- `vars(cls)` 只看叶子类自己的 `__dict__`。若 `ParentProcessor` 定义 `on_step_end`，`ChildProcessor(ParentProcessor)` 不覆写它，则 `"on_step_end" in vars(ChildProcessor)` 为 False → 算法错误地认为 Child 不处理 `step_end`
- MRO 遍历找到第一个定义该方法的祖先，正确处理继承链

**L1.4 `get_graph_metadata(cls_or_instance) → dict`**（模块级函数，`to_graph()` 调用）：

```python
def get_graph_metadata(cls_or_instance) -> dict:
    """从类（或类型，或实例）读取 graph 元数据。返回 dict 供 Builder 或 to_graph() 使用。

    接受类或实例 — to_graph() 可传 runtime 实例进来。
    安全处理非 MultiHookProcessor 类型（如 function-based processor）。

    _hook_ vs _hooks_ 语义：
      - _hook_ = 注册桶（registration bucket）；"*" 表示 MultiHookProcessor
      - _hooks_ = handler 覆盖范围（coverage）；用于 graph ATTACHED_TO 边
      - 只有显式指定具体 hook 时才收缩 _hooks_；"*" 保留 dispatch 推导结果
    """
    is_instance = not isinstance(cls_or_instance, type)
    cls = cls_or_instance if isinstance(cls_or_instance, type) else type(cls_or_instance)

    # ── _read() 必须定义在分支之前（两个分支都用到）──
    def _read(name: str, default):
        """读取实例 __dict__ 优先（若存在），否则回退到类属性。"""
        if is_instance and name in getattr(cls_or_instance, "__dict__", {}):
            return getattr(cls_or_instance, "__dict__", {})[name]
        return getattr(cls, name, default)
        # getattr(..., "__dict__", {}) 防御 __slots__ 处理器实例（无实例 __dict__，
        # 直接访问会 AttributeError）；__slots__ 类本身仍可用（类属性回退）。

    # ── hook 推导 ──
    if hasattr(cls, "_DISPATCH"):
        # MultiHookProcessor：_hook_ = 最近非空类 _hook；没有才是 "*"
        class_hook = _find_class_hook(cls)
        hooks_list = list(compute_effective_hooks(cls))  # R1 命中时 = [class_hook]
        bucket = class_hook if class_hook else "*"
    else:
        # 单 hook / function-based processor
        hook = _read("_hook", None) or ""
        if hook == "*":
            # 显式 wildcard 桶：runloop 在全部 8 个 processor hook 执行（runloop.py:154 _star_procs）
            hooks_list = list(PROCESSOR_HOOK_NAMES)
        else:
            hooks_list = [hook] if hook else []
        bucket = hook

    # ── 统一输出（两个分支使用相同的 _read() 逻辑）──
    return {
        "_hook_": bucket,
        "_hooks_": hooks_list,
        "_order_": _read("_order", 0),
        "_singleton_group_": _read("_singleton_group", None) or "",
        "_after_": list(_read("_after", ())),
        "_writes_slots_": list(_read("_writes_slot_keys", ())),
        "_reads_slots_": list(_read("_reads_slot_keys", ())),
        "_reads_event_fields_": list(_read("_reads_event_fields", ())),
        "_writes_event_fields_": list(_read("_writes_event_fields", ())),
    }
```

**关键规则**：
- `_read()` 在两个分支之前定义，两个分支都使用它。非 Multi 分支也读取全部 8 个键（含 slot/event 字段）。
- 始终写入所有 9 个键（P2：键存在性）。空列表/空字符串表示"显式空"。
- `_hook_` 是注册桶：MHP → 最近非空类 `_hook`，无则 `"*"`；单 hook → hook 名；无 hook → `""`。
- `_hooks_` 是 handler 覆盖范围（用于 graph ATTACHED_TO 边）：MHP → dispatch 推导（R1 命中时 = `[class_hook]`）；单 hook → `[hook]` 或 `[]`。
- 实例 `__dict__` 覆盖类属性（支持 instance-level override，VM7 的核心前提）。
- 非 MultiHookProcessor：安全回退，不调用 `compute_effective_hooks()`，不访问 `_DISPATCH`。

---

### L2: Builder 序列化

**文件**：`harnessx/core/builder.py` — `build()` 方法

**L2.1 元数据键写入**（在 `build()` 中，`_serialize_processor` 返回 dict 之后）：

对每个可序列化处理器（`serialized is not None`），调用 `get_graph_metadata(proc)` 并将所有键/值写入 `serialized` dict：

```python
meta = get_graph_metadata(proc)
for k, v in meta.items():
    serialized[k] = v  # 始终写入，包括空列表
```

**L2.2 `_hook_` / `_hooks_` 语义与覆盖规则**（替换现有 line 305-307）：

核心原则：`_ProcEntry` 的解析值（在 `add()` 中已合并类默认值与显式传参）是 ground truth。
序列化时无条件写入这些值，不比较、不条件化。

**`_hook_` vs `_hooks_` 语义**：
- `_hook_` = **注册桶**（registration bucket）。始终等于 `entry.hook`，包括 `"*"`。
- `_hooks_` = **实际 handler 覆盖范围**，用于 graph 的 ATTACHED_TO 边生成。
  当注册桶是 `"*"`（MultiHookProcessor）时，从 dispatch 派生；当注册桶是具体 hook 时，收缩为单元素列表。

```python
# 1. 始终先调用 get_graph_metadata() 获取完整元数据（含 dispatch 派生的 _hooks_）
meta = get_graph_metadata(proc)
for k, v in meta.items():
    serialized[k] = v

# 2. 无条件覆盖为 _ProcEntry 解析值（ground truth）
serialized["_order_"] = entry.order
serialized["_singleton_group_"] = entry.singleton_group or ""
serialized["_after_"] = list(entry.after)

# 3. _hook_ 始终写 entry.hook（包括 "*"）——注册桶是 ground truth
serialized["_hook_"] = entry.hook

# 4. _hooks_ 三态规则（与 runloop 的 "*" 桶行为闭合）：
#    - 具体 hook → 收缩为单 hook 列表
#    - MHP + "*" → 保留 dispatch 推导（compute_effective_hooks 结果）
#    - 非 MHP + "*" → PROCESSOR_HOOK_NAMES（runloop 在全部 8 个 processor hook 执行 wildcard）
from .processor import MultiHookProcessor  # build() 内已局部导入

if entry.hook and entry.hook != "*":
    serialized["_hooks_"] = [entry.hook]
elif entry.hook == "*" and not isinstance(proc, MultiHookProcessor):
    serialized["_hooks_"] = list(PROCESSOR_HOOK_NAMES)
```

**关键**：不比较 `class_first_hook`，不设 `_order_explicitly_set` 标志。`_ProcEntry` 在 `add()` 中
已经完成了"显式传参优先于类默认"的解析，`build()` 只需原样输出。`entry.hook="*"` 是真实注册桶，
必须写入 `_hook_`；`_hooks_` 必须与 runloop 实际执行范围一致（三态），graph 与 runtime 不得分叉。

**L2.3 Runtime-only 处理器**（`serialized is None`）— **Config-owned registration record**：

**核心约束**：绝不修改共享 processor 实例（注册元数据层面）。`_ProcEntry` 是 frozen dataclass
（builder.py:41-49），runtime 注册也必须遵循同等的不变性。两个 Builder 共享同一实例时，
构建结果不能取决于先后顺序。

**新模块 `harnessx/core/runtime.py`**（无循环依赖：只 import `harnessx/core/processor.py` 的
`get_graph_metadata`，不 import builder / harness / graph；builder、harness、snapshot 均单向
依赖它；`HarnessConflictError` 定义在此，builder 再导出——保持
`from harnessx.core.builder import HarnessConflictError` 兼容，不引入 builder → runtime
反向依赖）：

```python
"""Runtime-only processor registration — immutable, config-owned."""
from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any, Iterable


class HarnessConflictError(Exception):
    """配置冲突（_after 跨 order 矛盾 / 同 order 内 cycle）— 从 builder.py:57 迁入。

    builder / 运行时路由 / graph EXECUTES_BEFORE 边生成共享此异常；
    builder 侧再导出（``from .runtime import HarnessConflictError``）。
    """


@dataclass(frozen=True)
class RuntimeReg:
    """不可变 runtime 注册 record — 隔离注册元数据，不修改 processor 实例。

    只承载注册时刻的信息（hook 桶 / order / singleton_group / after）。
    实例状态绑定（_bind_* 系列）发生在 Harness 构造时，由 L2.3b 单 owner 规则治理。
    """

    proc: Any
    hook: str = "*"
    order: int = 0
    singleton_group: str | None = None
    after: tuple[str, ...] = ()


def coerce_runtime_reg(x) -> "RuntimeReg | None":
    """统一规范化入口：RuntimeReg → 自身；dict / None → None；裸 processor → 包成 record。

    裸实例的 hook 取 natural bucket（get_graph_metadata 的 _hook_ =
    最近非空类 _hook，MHP 无则 "*"），**无 _hook 的裸实例默认桶必须为 "*"**
    （与 runloop `getattr(proc, "_hook", None) or "*"` 现状一致 — harness.py:391
    `_route_processors` 三分中的第三分支）。
    order/sg/after 取类默认值。
    这是"继续兼容直接注入的裸 processor"的唯一入口。
    """
    if isinstance(x, RuntimeReg):
        return x
    if isinstance(x, dict) or x is None:
        # None 与 dict 同为"不可规范化"→ None（normalize 见 None 抛 ValueError）。
        # 缺此守卫时 None 会落入裸实例分支：L1.4 `_read` 已用
        # getattr(..., "__dict__", {}) 防御 __slots__（块 7），对 None 不再抛错、
        # 而是回退类属性 → 静默包成 RuntimeReg(proc=None, hook="*", ...) ——
        # 违反 normalize 的 "None → ValueError" 契约（第八轮第 32 项）。
        return None
    from .processor import get_graph_metadata  # 无循环：processor 不 import runtime

    meta = get_graph_metadata(x)
    return RuntimeReg(
        proc=x,
        hook=meta["_hook_"] or "*",        # 空桶 → "*"（非 MHP 裸实例也执行于 8 个 hook）
        order=meta["_order_"],
        singleton_group=meta["_singleton_group_"] or None,
        after=tuple(meta["_after_"]),
    )


def unwrap_runtime_proc(x):
    """读取型消费者统一入口：RuntimeReg → .proc；裸 processor → 自身。"""
    return x.proc if isinstance(x, RuntimeReg) else x


def normalize_processor_reg(x) -> "SerializedReg | RuntimeReg":
    """**唯一规范化入口** — 显式接受 SerializedReg | RuntimeReg | dict | 裸 processor。

    所有写入口（``__post_init__``、``add_runtime_reg``、``replace_processor_regs``、
    ``copy(processors=...)`` override、spawn 混合列表）统一经它（第八轮第 32 项）：

    - SerializedReg → 自身（**不**经 coerce —— coerce 会把 record 当 processor
      包装，误包装修复）；
    - dict → ``SerializedReg(dict_ref=x)``（presence 动态 property 实时解析）；
    - RuntimeReg → 自身；
    - 其余（裸 processor 实例）→ ``coerce_runtime_reg(x)``；
    - None / 黑名单内置类型（str/int/list/...）→ ``ValueError``（防静默丢注册项）；
      **其余任意对象 → 裸实例兼容语义**（coerce 包装，注入兼容边界 — 无法枚举所有
      带 ``__dict__`` 的类型，slice/memoryview/函数/模块等未列类型会落入此路径）。
    """
    if x is None:
        raise ValueError("normalize_processor_reg: 不接受 None（防静默丢注册项）")
    if isinstance(x, (str, int, float, bytes, bytearray, list, tuple, set,
                      frozenset, complex, range, type)):
        # 内置标量/容器/类对象一律拒绝（bool 是 int 子类，已覆盖）→ ValueError。
        # 不走 coerce —— 否则 get_graph_metadata(str) 在 L1.4 `_read` 访问
        # str.__dict__ 抛 AttributeError，与"→ ValueError"承诺不一致。
        # 契约边界：黑名单是显式列举，非完备（slice/memoryview/函数/模块等未列
        # 类型有 __dict__，落入裸实例兼容语义 — 注入兼容设计边界，不追加列举）。
        raise ValueError(
            f"normalize_processor_reg: {type(x).__qualname__} 不是注册项"
            f"（只接受 SerializedReg / RuntimeReg / dict / processor 实例）"
        )
    if isinstance(x, SerializedReg) or isinstance(x, RuntimeReg):
        return x
    if isinstance(x, dict):
        return SerializedReg(dict_ref=x)
    reg = coerce_runtime_reg(x)
    if reg is None:
        raise ValueError(
            f"normalize_processor_reg: 无法将 {type(x).__qualname__} 规范化为注册项"
        )
    return reg


@dataclass(frozen=True)
class RoutingEnvelope:
    """统一路由信封 — 保留全局注册序列 + 排序元数据，路由后解包为裸 processor。

    - ``seq``：全局注册序号（``config._processor_regs`` 下标，见 L2.3c）— 稳定破平。
    - 桶内排序复用 Builder 规则：**先 order，同 order 内按 ``_after_`` 拓扑排序，
      seq 稳定破平**（手写配置不能假设 ``_after_`` 已编码进顺序）。
    """

    reg: RuntimeReg
    seq: int


@dataclass(frozen=True)
class SerializedReg:
    """序列化 dict 条目的注册视图 — presence/value 是 dict_ref 的**动态 property**。

    **不缓存解析值**（第八轮第 33 项）：``dict_ref`` 可变且被外部共享
    （``config.processors`` 视图暴露同一批 dict）——缓存会造成"修改 dict 后
    graph 读新值、runtime 读旧值"的分叉。每次访问都实时从 ``dict_ref`` 读
    （P2 键存在性）：

    - ``*_present=True`` 且值为 ``""``/``0``/``[]`` → **显式空，原值保留**。
      ``_hook_=""`` → 路由到**空桶（不执行）**，不再走默认推断（VM14：graph 0 边 +
      运行时不执行，一致）。
    - ``*_present=False``（键缺失）→ 实例化后从**自然元数据回退**
      （``natural = coerce_runtime_reg(inst)``，见 L2.3c 规则 4）。
    """

    dict_ref: dict

    @property
    def hook(self) -> str:
        v = self.dict_ref["_hook_"] if "_hook_" in self.dict_ref else None
        return v if isinstance(v, str) else ""
        # 非 str（含 None）→ 显式空（空桶不执行）——不产生 str(None)="None" 脏桶；
        # builder 保证写 str（L2.2），手工 YAML 的异型值安全降级（与 L3.1 过滤空串一致）。

    @property
    def hook_present(self) -> bool:
        return "_hook_" in self.dict_ref

    @property
    def order(self) -> int:
        v = self.dict_ref["_order_"] if "_order_" in self.dict_ref else None
        try:
            return int(v)          # int / 数字字符串均可；int(None) 抛错 → 走 except
        except (TypeError, ValueError):
            return 0               # 解析失败 → 默认 0（与类默认一致；不崩溃）

    @property
    def order_present(self) -> bool:
        return "_order_" in self.dict_ref

    @property
    def singleton_group(self) -> "str | None":
        v = self.dict_ref["_singleton_group_"] if "_singleton_group_" in self.dict_ref else None
        return v if isinstance(v, str) else None   # 非 str（含 None）→ 缺失语义

    @property
    def sg_present(self) -> bool:
        return "_singleton_group_" in self.dict_ref

    @property
    def after(self) -> "tuple[str, ...]":
        v = self.dict_ref["_after_"] if "_after_" in self.dict_ref else None
        return tuple(v) if isinstance(v, (list, tuple)) else ()
        # 只接受 list/tuple：None/空/异型值（int/str 等）→ ()。
        # 纯 `tuple(v) if v else ()` 有两个隐患：非空非迭代（_after_: 7）truthy →
        #   tuple(7) 抛 TypeError；str（_after_: "ab"）→ char-split ("a","b")。
        # isinstance 守卫同时消除两者（str 不 char-split，安全降级 ()）。

    @property
    def after_present(self) -> bool:
        return "_after_" in self.dict_ref


def stable_topological_sort(items, *, order_key, after_key, group_key, seq_key):
    """泛化稳定拓扑排序 — 完整抽取 builder.py:678 ``_topological_sort_entries`` 语义。

    适配 ``_ProcEntry`` 与 ``RoutingEnvelope``（graph 的 EXECUTES_BEFORE 边生成
    也用同一函数，见 L4.6 / L5.6）：

    1. ``group_map``：{group_key(e): e for e in items if group_key(e)} —— ``after``
       依赖按 **singleton_group 名**解析（group_key 提供条目自己的组名；无组条目
       不可被引用）。
    2. **跨 order 冲突**：条目 e 的 after 目标 t 若 order_key(t) > order_key(e) →
       抛 HarnessConflictError（builder:696-709 同语义 — 目标更晚却声明先执行，
       约束永不可满足）。
    3. 按 order_key 升序分组；组内 Kahn 拓扑：adj 按 seq_key 升序构建、queue 初始化
       与出队追加均保持 seq 序 → 稳定（builder:724-746 同语义，FIFO 队列）。
    4. 组内 cycle → HarnessConflictError（列 cycle 成员，builder:748-752 同语义）。
    5. **soft deps**：after 引用未注册 group → 静默忽略（builder:732 同语义 —
       可选排序依赖，不耦合可能缺席的插件）。
    """


# ── owner registry（身份去重，不依赖 hash/eq；强引用防 id 复用）─────────────

_OWNER_LOCK = threading.Lock()
_OWNERS: "dict[int, tuple[Any, object]]" = {}  # id(proc) -> (strong_ref, token)


def claim_owners(procs: "Iterable[Any]", token: object) -> None:
    """锁内两阶段：全量检查 → 全量 claim。

    - 身份去重按 ``id``（``setdefault``），不调用 hash/eq — 不可哈希处理器安全。
    - 任一实例已属其他 token → 抛 ValueError，且**无任何实例被写**。
    - 强引用保存在 registry 中 → id 在 claim 期间不可被复用。
    """
    unique: "dict[int, Any]" = {}
    for p in procs:
        unique.setdefault(id(p), p)
    with _OWNER_LOCK:
        for pid, p in unique.items():
            existing = _OWNERS.get(pid)
            if existing is not None and existing[1] is not token:
                raise ValueError(
                    f"runtime processor {type(p).__qualname__} 已绑定到另一个 Harness；"
                    f"runtime-only 处理器单 owner，禁止跨 config 复用。"
                    f"并行复用请改用 factory/clone 生成新实例"
                )
        for pid, p in unique.items():
            _OWNERS[pid] = (p, token)


def release_owners(token: object) -> None:
    """释放本 token 的全部 claim（cleanup 完整成功后 / 构造失败回滚均调用）。

    幂等：token 无 claim 时安全 no-op。释放后实例可被新 Harness claim。
    """
    with _OWNER_LOCK:
        for pid in [k for k, (_, t) in _OWNERS.items() if t is token]:
            del _OWNERS[pid]
```

Builder 创建 record 存入 `processors` 列表，**不修改 proc 实例**：

```python
else:
    # Runtime-only：创建不可变 registration record，不修改实例
    reg = RuntimeReg(proc, hook, entry.order, entry.singleton_group or None, entry.after)
    processors.append(reg)
```

**`HarnessConfig.__post_init__` 调整**（harness.py:851-859）：非 dict 一律
`existing_rt.append(coerce_runtime_reg(p))` — 裸实例统一包成 record，`_rt_procs` 只存
`RuntimeReg` 列表。直接注入的裸 processor 由此兼容。

**`to_graph()` 读取**：解包 `reg.proc`，注册元数据直接取自 record（不发散到实例属性），
类级元数据（slot keys、event fields、dispatch hooks）仍从 `type(reg.proc)` 读取（L5.1）。

**重复注册保护**：`RuntimeReg` 是 frozen dataclass — 自然不可变。同一实例以不同参数添加
两次产生两个不同的 record，互不污染注册元数据。

**L2.3a `_rt_procs` 消费者全量迁移**（grep 全库，逐处改法）：

| 文件:行 | 现状 | 改法 |
|---------|------|------|
| harness.py:851-859 `__post_init__` | 非 dict 直接进 `_rt_procs`（混合顺序丢失 + 双源） | **构建 `_processor_regs`（唯一真相，经 `normalize_processor_reg`）**；`_rt_procs` 改只读派生 **tuple** property；`self.processors` 重建为 dict 视图（见 L2.3c） |
| harness.py:604-624 `_instantiate_runtime` | 双循环：先 dict 后 runtime（顺序漂移 + 重复） | **只消费 `_processor_regs` 一次**（enumerate 下标 = seq）：SerializedReg → `_instantiate_proc(dict_ref)` → **统一 natural 解析**（`natural = coerce_runtime_reg(inst)`；`value = explicit if *_present else natural.*` 四字段一致，含实例级覆盖；显式空 → 空桶不执行）→ 包 `RuntimeReg` → `RoutingEnvelope(reg, seq)`；RuntimeReg → 直接使用；**plugin 尾部 envelopes**（见下）。**不再追加 `_rt_procs`**（已在 canonical 中）；**不再写 `__hx_hook_override__`**（新 `_route_processors` 按 env.reg.hook 分桶，旧 override 分支仅防御） |
| harness.py:378-393 `_route_processors` | MHP / override / `_hook` 三分，忽略 order/after | 输入 `list[RoutingEnvelope]`：按 `env.reg.hook` 分桶（含 `""` 空桶 → runloop 不执行）→ 桶内 `stable_topological_sort`（泛化共享函数，适配 `_ProcEntry`/`RoutingEnvelope`）→ 输出 `[e.reg.proc for e in ...]`（裸 processor）；裸实例分支保留为防御 |
| harness.py:696-706 plugin 处理器 | 主路由后单独 `_route_processors([proc])` 追加（不参与 order/after 排序、不进 owner claim） | **收集为尾部 envelopes**：`base_seq = max(e.seq for e in flat) + 1`（serialized DROPPED 造成 seq 空洞时 `len(flat)` 会与已有 seq 冲突）；**id 身份去重保留**（`seen_ids = {id(e.reg.proc)}`，同 id 跳过 — 原 :700-706 语义）；与主序列**一次统一路由**；claim 在 Harness 侧对最终路由 processors 全量收集（L2.3b） |
| harness.py:864 `required_model_keys` | `type(proc).required_model_keys` 直读 | `type(unwrap_runtime_proc(proc)).required_model_keys`（record 静默丢 model key） |
| harness.py:966 `copy()` | 列表浅拷贝 | 改用 **L2.3c 规则 5 `copy()` 伪代码**：无 override → canonical 原样保留 + `__post_init__` 重建视图；override → 丢弃旧 canonical 从新混合序列重建。旧行"浅拷贝不变"仅部分成立（record 不可变 ✓，但视图重建/override 行为已超出） |
| trajectory.py:437 | `proc_iter.extend(_rt_procs)` | `unwrap_runtime_proc(p)` 后再 extend（label/type 读取正确，record 不冒充 processor） |
| spawn_subagent.py:541-569 | 按 dicts + runtime 重组（破坏混合顺序）+ 传 `_rt_procs=[]` | **直接转换 `config._processor_regs`**（canonical 序列）：SerializedReg → 实例化/检测 → 替换时产出 `RuntimeReg(proc=new_inst, ...)`（从 SerializedReg 或 replace 原 RuntimeReg 保留四元组）；无需替换的原样保留（SerializedReg 对象可直接出现在混合列表）；最后 `config.copy(processors=混合列表)`（override 经 `normalize_processor_reg` 规范化重建），**删除 `_rt_procs=[]`**（只读 tuple property — 传了即 AttributeError） |
| cli.py:367 / api/routes/run.py:316 `_mount_plugin` | view + 插件实例 append（override 丢弃 RuntimeReg） | `copy(processors=[*config._processor_regs, *plugin_procs], plugins=plugins)` — canonical 原样保留（含交错位置）+ plugin 尾部；裸实例经 normalize coerce |
| cli.py:489 路由开关 | 遍历 view，ModelRouter → 裸实例（runtime 项丢失） | 遍历 `_processor_regs`：SerializedReg 非 router 原样保留；router → 实例化后 `coerce_runtime_reg(inst)`；RuntimeReg 原样 |
| cli.py:1068 chat 处理器 | 前置 SlashCommand + view + 尾部 ToolPrinter | `copy(processors=[*[SlashCommandProcessor(...)], *config._processor_regs, *[_tool_printer]])` — 前置/尾部不变，canonical 原样 |
| gateway/main.py:173 | im_procs + stripped view | `_strip_env_context_injector` 改作用于 `_processor_regs` 中 SerializedReg.dict_ref（RuntimeReg 原位保留）；`copy(processors=[*im_procs, *regs])` |
| gateway/main.py:198 | stripped view | strip 同上作用于 canonical；`copy(processors=regs)` |
| harness.py:960-973 `copy()` | `new.processors = list(self.processors)`（纯 dict）+ `new._rt_procs = list(...)`（交错位置丢失） | 无 `processors=` override：canonical 随 `copy.copy` 原样保留，`__post_init__` 只重建视图；有 override：`new._processor_regs = None` 后从新混合序列经 `normalize_processor_reg` 重建（见 L2.3c） |
| harness.py:982-991 `canonicalize()` | `new.processors = out` 事后赋值（不重建 canonical） | **直接转换 canonical 序列**：SerializedReg 按 `repr(dict_ref)` 去重、RuntimeReg 原样保留 → `new._processor_regs = tuple(out)` → `__post_init__()` 重建视图（见 L2.3c） |
| meta_harness/agent.py:372 | `type(p).__name__` 当 label | `unwrap_runtime_proc(p)` 后再读 `_singleton_group`/type（否则 label 变成 "RuntimeReg"） |
| meta_harness/agent.py:422 | `getattr(p, "system_builder", None)` | 同上 unwrap（否则 template_path 静默丢失） |
| validate_workflow.py:100,273,488 | 直接遍历 | unwrap 后再检查 |
| harnessx/meta_harness/workers/trajectory_digester.py:151-160 | `isinstance(proc, SystemPromptProcessor)` + `child_cfg._rt_procs = new_rt` | 读取 `unwrap_runtime_proc`；替换**必须** `dataclasses.replace(reg, proc=new_proc)` 保留四元组；**对完整 `_processor_regs` map**（SerializedReg 与无关 RuntimeReg 原样保留——只构造 runtime 子序列会丢掉 serialized 位置）；写回走 **`child_cfg.replace_processor_regs(...)`**（不再直接赋 `_rt_procs` — 只读视图） |
| benchmarks/tau2/agent.py:299-304 | `isinstance(p, _PHProc)` 直读 + `_rt_procs.append(_PHProc())` | 读取 `unwrap_runtime_proc(p)`；追加走 **`_config.add_runtime_reg(_PHProc())`**（coerce 内部完成） |
| recipe/tau2_evolver/run.py:1038 | 渲染读取（label/order 展示） | `unwrap_runtime_proc(p)` 后再读属性 |
| graph/edit.py:132 | `from .snapshot import _slug_from_target`（旧函数已替换） | 迁移为 `_compute_slug`：`node_id = f"proc:{_compute_slug(target)}"`（edit 的 INSERT_NODE 节点是主图处理器节点，`proc:` 前缀正确） |
| recipe/gaia_evolver/run.py:1810 | 渲染读取（同上） | `unwrap_runtime_proc(p)` 后再读属性 |
| plugins/dimensions/light_meta/processors.py:43 | — | **不是** config._rt_procs 消费者（是处理器上的方法，读 `_harness_runtime.processors` 即路由后的实例 dict）— **不改** |

**runloop 执行路径**：`_route_processors` 输出 `proc_dict[hook]` 中的元素是 `reg.proc`
实例 → runloop 与 `_bind_*`（harness.py:1069-1078）、light_meta 全部拿到裸实例，
无需再解包。record 永不进入 proc_dict。

**L2.3b 单 owner fail-fast（身份 registry 版）**（**替代**旧主张"跨 config 共享实例不再是问题"）：

record 只隔离注册元数据。Harness 在构造时仍向实例绑定状态（harness.py:1069-1078：
`_bind_sub_harnesses` / `_bind_tool_registry` / `_bind_model_config` /
`_bind_harness_config` / `_bind_runtime`）→ "同一实例可安全跨 config 共享"**不成立**。

**机制**：`core/runtime.py` 的锁保护身份 registry（`_OWNERS: id(proc) → (strong_ref, token)`，
见模块代码块 `claim_owners` / `release_owners`）。**不用实例属性写 owner** ——
registry 外的任何实例属性方案都依赖 hash/eq 去重且无法处理不可哈希处理器。

**规则四条**：

1. **claim 覆盖面 = 最终路由 processors 中的全部 MHP，一次覆盖所有来源**（第八轮第 34 项）：
   ```python
   # Harness.__init__ — extra_processors 合并后（:1032-1034）、sub-harness 创建前（:1054）
   claim_owners(
       (p for procs in self._rt.processors.values() for p in procs
        if isinstance(p, MultiHookProcessor)),
       self.__hx_owner_token,
   )
   ```
   - 覆盖面 = canonical RuntimeReg 实例 + 尾部 plugin 实例 + extra_processors 实例
     **一次覆盖**（extras 已合并进 `_rt.processors`，:1032-1034 — 无 `self.extra_processors`
     属性，那是构造参数，harness.py:1013；扫描 canonical 也覆盖不到 plugin，故从最终
     路由结果 claim）。
   - serializable 实例化出的 MHP 也 claim — 每次构造全新实例，claim 无害（幂等）。
   - **claim 面 = `_bind_*` 绑定面**（:1073 `isinstance(proc, MultiHookProcessor)`）：
     任何被写状态的实例必被 claim；非 MHP 无绑定状态，无需 claim。
   - 身份去重由 `claim_owners` 按 `id` 完成（`setdefault`，不依赖 hash/eq）。

2. **不可复用 owner token**：`self.__hx_owner_token = object()`（`id()` 可被 GC 复用，禁止）。
   同一 Harness 内同实例多次注册 → 同 token，幂等放行。

3. **位置与两阶段**：`claim_owners` 调用在 extras 合并后（harness.py:1032-1034）、
   sub-harness 创建前（:1054）与 `_bind_*`（1069-1078）。锁内"全量检查 → 全量 claim"
   由 `claim_owners` 实现：任一实例已属其他 token → `ValueError` 且**无任何实例被写**。

4. **失败回滚 + cleanup 释放（shielded 状态机，取消可重试）**（第八轮第 35 项）：
   - **`Harness.__init__` 增加 `self._cleanup_task: "asyncio.Task | None" = None`**
     （harness.py:1025-1026 现有 `self._closed = False` / `self._sandbox = None` 旁）——
     修复后的 cleanup 伪代码首调直读 `self._cleanup_task`，缺初始化即 AttributeError。
   - 构造失败（claim 之后任何一步异常）→ `except` 调 `release_owners(self.__hx_owner_token)`
     （只回滚本 token，不触碰其他 token 的 claim）。
   - **cleanup 屏蔽状态机**（harness.py:1366-1401 现状问题：`_closed=True` 在 :1370
     先于任何 await 设置；`self._sandbox = None` 在 :1391 先于 release await — 取消后
     `_closed` 已置（重试 no-op）且 sandbox 引用已丢（释放永不发生）→ 泄漏。
     只移动 `_closed` 不够，引用清除必须进 impl 内部）：
     ```python
     async def cleanup(self) -> None:
         """幂等：首次调用启动底层清理任务；调用方取消不取消底层清理。

         **存底层任务，不存 shield 外层**：shield 外层在调用方取消时自身会被置为
         cancelled —— 若把它存进 ``_cleanup_task``，重试 ``await`` 同一对象会立即
         抛 CancelledError（asyncio 已验证语义），"取消后重试等待同一任务"无法成立。
         """
         if self._closed:
             return
         if self._cleanup_task is None or self._cleanup_task.done():
             # done() 分支：任务级死亡（事件循环关闭 / impl 异常）→ 重建重跑；
             # 正常完成路径由首行 _closed 短路，不会走到这里。
             # 幂等重跑安全：sub.cleanup / plugin.stop 幂等，sandbox 已清则跳过。
             self._cleanup_task = asyncio.ensure_future(self._cleanup_impl())
         await asyncio.shield(self._cleanup_task)
         # 调用方取消 → 只取消本次 await（CancelledError 抛给调用方）；
         # _cleanup_task 本身未被取消、继续运行 → 重试 await 同一任务至完成。

     async def _cleanup_impl(self) -> None:
         for sub in reversed(list(getattr(self, "_sub_harnesses", {}).values())):
             try:
                 await sub.cleanup()     # ①
             except Exception:
                 pass
         for plugin in reversed(self._rt.plugins):
             try:
                 result = plugin.stop()
                 if inspect.isawaitable(result):
                     await result        # ②
             except Exception as exc:
                 warnings.warn(...)      # 原文案保留
         sandbox = self._sandbox
         if sandbox is not None:
             try:
                 await self._rt.sandbox_provider.release(sandbox)   # ③
             except Exception as exc:
                 warnings.warn(...)      # 原文案保留
             finally:
                 self._sandbox = None    # 资源引用只在 release await 结束后清除
         # 收尾三操作异常隔离（全部 await 完成后）：
         try:
             release_owners(self.__hx_owner_token)
         finally:
             self._closed = True         # 即使 release_owners 抛（内部 bug 级），
             _ACTIVE_HARNESSES.discard(self)   #  closed/discard 必执行；否则 closed 已置
                                        #  而 owner 未释放 → 泄漏且无法自愈
     ```
     - 任意 await 点（①②③）被取消 → shield 保证 impl 继续跑完；资源引用在 impl
       内 finally 清除（取消不丢引用）；**重试 = 再次 `await harness.cleanup()` →
       await shield(同一 `_cleanup_task` 底层任务)** —— 该任务未被取消，await 至完成；
       任务级死亡（loop 关闭 / impl 异常）时 done() 分支重建任务重跑，不永久失败。
     - **并发契约**：cleanup 单 event loop 使用（`ensure_future` 建的任务属于调用方
       loop，另一 loop `await shield` 会抛 "attached to a different loop"）；
       跨 loop / 多线程并发调用未定义（现状 `async def cleanup` 亦如此，不新增约束）。
   - 释放后实例可被新 Harness claim（registry 删条目即允许）。

未来若需并行跨 config 复用：引入 factory/clone（按注册元数据复制新实例），**不**放宽 record。

**L2.3c config-owned 统一注册序列 `_processor_regs`（单源真相）**：

**问题**：`__post_init__`（harness.py:851）把 runtime 项从 `processors` 分离到 `_rt_procs` 后，
任何遍历 `config.processors` 都只见纯 dict —— 混合顺序丢失；而 `_processor_regs` 与
`_rt_procs` 双列表共存又形成同步问题（重复执行 / 只改其一被忽略）。

**规则（单源真相，消除双源）**：

1. **`_processor_regs` 是唯一可写真相**。`__post_init__` 首次构建（经
   `normalize_processor_reg` — 唯一规范化入口，SerializedReg 不被 coerce 误包装）：
   ```python
   # __post_init__（替代现 harness.py:853-859）：
   raw = getattr(self, "_processor_regs", None)
   if raw is None:
       self._processor_regs: tuple = tuple(
           normalize_processor_reg(p) for p in self.processors
       )
   #   混合列表可含 SerializedReg / RuntimeReg / dict / 裸实例。
   self._refresh_processors_view()   # 从 canonical 重建视图（每次 __post_init__ 都执行）
   ```

2. **`_rt_procs` 改为只读派生视图（property，无 setter，返回 tuple）**：
   ```python
   @property
   def _rt_procs(self) -> tuple:
       """只读派生视图 — canonical 中的 RuntimeReg 元组。

       tuple（非 list）：list 视图的 ``.append()`` 只改临时列表、对 canonical
       静默无效；tuple 直接抛 AttributeError，迫使写入走写 API。
       """
       return tuple(r for r in self._processor_regs if isinstance(r, RuntimeReg))
   ```
   **时序契约**：`_rt_procs` 仅在 `__post_init__` 之后可用（依赖 `_processor_regs`
   已构建）；构造完成前访问为未定义行为（实现可在 property 体内用
   `getattr(self, "_processor_regs", ())` 防御）。

3. **写 API（全部写入端迁移到这两个方法，均经 `normalize_processor_reg`；末尾刷新视图）**：
   ```python
   def _refresh_processors_view(self) -> None:
       """从 canonical 重建 processors 视图（__post_init__ 与写 API 共用）。"""
       self.processors = [r.dict_ref for r in self._processor_regs
                          if isinstance(r, SerializedReg)]

   def add_runtime_reg(self, reg: Any) -> None:
       """追加注册项（末尾，seq = len）。裸实例自动 coerce；dict 也可追加。"""
       self._processor_regs = (*self._processor_regs, normalize_processor_reg(reg))
       self._refresh_processors_view()

   def replace_processor_regs(self, regs: Iterable) -> None:
       """整体替换 canonical 序列 — **必须传完整混合序列**（digester 场景：
       对全部 _processor_regs map 后写回；只传 runtime 子序列会丢掉 serialized
       位置）。每项经 normalize_processor_reg 规范化。"""
       self._processor_regs = tuple(normalize_processor_reg(r) for r in regs)
       self._refresh_processors_view()
   ```
   **视图刷新是必需项**（不是优化）：只改 canonical 不刷新视图，`config.processors`
   仍暴露已被替换/删除的旧 dict_ref —— digester 把 SerializedReg 换成 RuntimeReg 后，
   `to_graph()` 遍历 `config.processors` 会为已替换处理器再生成 persistent 节点，
   与 `_rt_procs` 的 runtime 节点构成双重表示（陈旧数据）。
   写入端迁移清单：`harnessx/meta_harness/workers/trajectory_digester.py:160` → `replace_processor_regs`（完整 map）；
   `benchmarks/tau2/agent.py:303-304` → `add_runtime_reg`；`spawn_subagent.py:569` →
   经 `copy(processors=...)` override（见下）；`copy(processors=...)` 六调用点
   （cli:367/489/1068、api:316、gateway:173/198）→ 见 L2.3a 表；`copy()` /
   `canonicalize()` 见下。

4. **`_instantiate_runtime` 只消费 canonical 一次，不再追加 `_rt_procs`**：
   ```python
   flat: list[RoutingEnvelope] = []
   for seq, reg in enumerate(config._processor_regs):
       if isinstance(reg, SerializedReg):
           inst = _instantiate_proc(reg.dict_ref)
           if inst is None:
               continue                        # 原 DROPPED 日志逻辑保留
           # 统一 natural 解析（第八轮第 33 项）：
           #   natural = coerce_runtime_reg(inst) — get_graph_metadata 读实例
           #   __dict__ 优先 → 实例级覆盖（VM7 模式）；无 _hook 裸实例 → "*"。
           #   value = explicit if *_present else natural.* —— 四字段一致；
           #   不再有 "after 硬编码 ()" / "跳过实例级覆盖" / "空 hook 得空桶" 分叉。
           natural = coerce_runtime_reg(inst)
           flat.append(RoutingEnvelope(RuntimeReg(
               proc=inst,
               hook=reg.hook if reg.hook_present else natural.hook,  # 显式空 "" → 空桶
               order=reg.order if reg.order_present else natural.order,
               singleton_group=reg.singleton_group if reg.sg_present else natural.singleton_group,
               after=reg.after if reg.after_present else natural.after,
           ), seq))
       else:
           flat.append(RoutingEnvelope(reg, seq))
   # plugin 尾部 envelopes（第八轮第 34 项）：
   #   base_seq = max(seq)+1 — serialized DROPPED 造成 seq 空洞时 len(flat) 会冲突；
   #   id 身份去重保留（原 harness.py:700-706 语义 — mounted 插件与 canonical 同实例）。
   seen_ids = {id(e.reg.proc) for e in flat}
   base_seq = max((e.seq for e in flat), default=-1) + 1
   for plugin in config.plugins or []:
       for proc in getattr(plugin, "processors", []) or []:
           if id(proc) in seen_ids:
               continue
           seen_ids.add(id(proc))
           flat.append(RoutingEnvelope(coerce_runtime_reg(proc), base_seq))
           base_seq += 1
   # 不再追加 getattr(config, "_rt_procs", []) — 已包含在 canonical 中；
   # 不再设置 __hx_hook_override__ — 新 _route_processors 按 env.reg.hook 分桶，
   #   旧 override 分支仅作防御（外部手工设置的实例仍被尊重）
   ```

5. **`copy()` / `canonicalize()` 保留混合顺序**：
   ```python
   def copy(self, **kwargs):
       new = copy.copy(self)                   # _processor_regs 浅拷贝随对象保留
       new.plugins = list(self.plugins)
       new._rt_sandbox = getattr(self, "_rt_sandbox", None)
       if "processors" in kwargs:
           new._processor_regs = None          # override：丢弃旧 canonical，__post_init__ 从新混合序列重建
       # 无 override：canonical 原样保留，__post_init__ 只重建 processors 视图
       for key, value in kwargs.items():
           setattr(new, key, value)
       new.__post_init__()
       return new

   def canonicalize(self):
       # 直接浅拷贝（不经 self.copy()，避免双重 __post_init__ — copy() 内部已调一次，
       # 首次构建的视图立即被二次调用覆盖，浪费且混淆）：
       new = copy.copy(self)
       new.plugins = list(self.plugins)
       new._rt_sandbox = getattr(self, "_rt_sandbox", None)
       seen: set = set()
       out: list = []
       for r in new._processor_regs:           # 直接转换 canonical 序列
           if isinstance(r, SerializedReg):
               # 去重 key 键序无关（repr(dict) 对插入序敏感 — 内容相同键序不同
               # 的两个 dict 会生成不同 repr，去重失效）：
               key = repr(sorted(r.dict_ref.items(), key=lambda kv: repr(kv[0])))
               if key in seen:
                   continue
               seen.add(key)
           out.append(r)
       new._processor_regs = tuple(out)        # RuntimeReg 原样保留（不重复）
       new.__post_init__()                     # 仅一次：重建视图
       return new
   ```
   `spawn_subagent.py:541-569`：直接转换 `config._processor_regs`（SerializedReg → 实例化/替换 → 产出
   `RuntimeReg(proc=new_inst, ...)` 或原 SerializedReg 保留），最后
   `config.copy(processors=混合列表)`（override 路径经 `normalize_processor_reg` 规范化重建），
   **删除 `_rt_procs=[]`**（只读 tuple property — 传了即 AttributeError）。

**桶内排序规则**：`stable_topological_sort`（runtime.py 共享函数，签名
`order_key`/`after_key`/`group_key`/`seq_key`，完整抽取 builder.py:678 语义：
group-map 把 after 组名解析到条目、跨 order 冲突与同 order cycle 抛
`HarnessConflictError`、soft deps 静默忽略、FIFO 稳定）：order 分组 → 组内
after 稳定拓扑（Kahn，同层按 seq）→ 跨组 order 升序。手写配置不能假设 `_after_`
已编码进注册顺序 —— 拓扑排序在路由层强制执行。graph 的 EXECUTES_BEFORE 边生成
（L4.6 / L5.6）用同一函数，保证图链 ≡ 运行时实际执行序。

**L2.4 无 `_rt_procs_meta` sidecar**：

`HarnessConfig` 上不新增 `_rt_procs_meta` 属性。无平行列表需要同步。

**L2.5 `_compute_entry_hooks()` 不再需要**：

Builder 直接调用 L1.4 的 `get_graph_metadata()`，优先级已在其中处理（`_hook` 先于 `compute_effective_hooks`）。Builder 的 `entry.hook` 覆盖在 L2.2 中单独处理。

---

### L3: Snapshot — dict → ComponentDecl 提取

**文件**：`harnessx/graph/snapshot.py`

**L3.1 `_extract_declaration(proc_dict, target) → ComponentDecl`**：

**关键设计**：先累积所有字段（dict → WKD → 兜底），最后一次性构造 `ComponentDecl`。
禁止先构造再 mutation —— `__post_init__` 只执行一次，事后修改 `decl.hooks` 不会触发
`hook` 同步和生命周期排序。

```python
def _extract_declaration(proc_dict: dict, target: str) -> ComponentDecl:
    """逐字段从 dict metadata 提取 ComponentDecl。回退链：dict → WKD → 类内省兜底。

    每个字段独立判断来源。使用键存在性（P2）。
    最终一次性构造 ComponentDecl，确保 __post_init__ 正确触发。
    """
    # ── 步骤 1：从 dict 累积 ──
    hooks = ()
    order = 50  # sentinel（"未知"）
    singleton_group = ""
    after = ()
    writes_to = ()
    reads_from = ()
    reads_event_fields = ()
    writes_event_fields = ()
    source = DeclarationSource.UNKNOWN
    confidence = 0.0

    hooks_present = "_hooks_" in proc_dict or "_hook_" in proc_dict

    hooks_raw: tuple = ()
    if "_hooks_" in proc_dict:
        hooks_raw = tuple(proc_dict["_hooks_"])
    elif "_hook_" in proc_dict:
        hooks_raw = (proc_dict["_hook_"],)
    # 过滤空字符串：legacy `_hook_=""` 必须得到 hooks=()
    # （hooks_present 仍为 True → 禁止 WKD 回退与字符串推断 → 0 条 ATTACHED_TO 边）
    hooks = tuple(h for h in hooks_raw if h)  # 保留 "*"（truthy，L4.1 通配展开）

    if "_order_" in proc_dict:
        order = int(proc_dict["_order_"])

    if "_singleton_group_" in proc_dict:
        singleton_group = str(proc_dict["_singleton_group_"])

    if "_after_" in proc_dict:
        v = proc_dict["_after_"]
        after = tuple(v) if v else ()

    if "_writes_slots_" in proc_dict:
        writes_to = tuple(proc_dict["_writes_slots_"])
    if "_reads_slots_" in proc_dict:
        reads_from = tuple(proc_dict["_reads_slots_"])

    if "_reads_event_fields_" in proc_dict:
        reads_event_fields = tuple(proc_dict["_reads_event_fields_"])
    if "_writes_event_fields_" in proc_dict:
        writes_event_fields = tuple(proc_dict["_writes_event_fields_"])

    # ── 步骤 2：WKD 回退（仅填充 dict 中键完全缺失的字段）──
    wkd = WELL_KNOWN_DECLARATIONS.get(target)
    if wkd is not None:
        if not hooks_present and wkd.hooks:
            # 关键：用 hooks_present 而非 truthiness。_hooks_=[] 是显式空，不回退。
            hooks = wkd.hooks
        if "_order_" not in proc_dict:
            order = wkd.order
        if "_singleton_group_" not in proc_dict and wkd.singleton_group:
            singleton_group = wkd.singleton_group
        if "_after_" not in proc_dict and wkd.after:
            after = wkd.after
        if "_writes_slots_" not in proc_dict and wkd.writes_to:
            writes_to = wkd.writes_to
        if "_reads_slots_" not in proc_dict and wkd.reads_from:
            reads_from = wkd.reads_from
        if "_reads_event_fields_" not in proc_dict and wkd.reads_event_fields:
            reads_event_fields = wkd.reads_event_fields
        if "_writes_event_fields_" not in proc_dict and wkd.writes_event_fields:
            writes_event_fields = wkd.writes_event_fields
        if source == DeclarationSource.UNKNOWN:
            source = wkd.source
        if confidence < wkd.confidence:
            confidence = wkd.confidence

    # ── 步骤 3：兜底推断（仅当键完全缺失时）──
    if not hooks_present:
        inferred = _infer_hook_from_target(target)
        if inferred and inferred != "unknown":
            hooks = (inferred,)

    # ── 步骤 4：一次性构造（__post_init__ 处理 hook↔hooks 同步 + 生命周期排序）──
    return ComponentDecl(
        target=target, hooks=hooks, order=order,
        singleton_group=singleton_group, after=after,
        writes_to=writes_to, reads_from=reads_from,
        reads_event_fields=reads_event_fields,
        writes_event_fields=writes_event_fields,
        source=source, confidence=confidence,
    )
```

**L3.2 `ComponentDecl` 字段**（`declaration.py`）：

```python
@dataclass
class ComponentDecl:
    target: str
    hook: str = ""                         # 向后兼容字段；__post_init__ 中与 hooks 同步
    hooks: tuple[str, ...] = ()            # 多 hook，有序（生命周期序）
    order: int = 50                         # 默认 50（"未设置"语义见 L3.3）
    singleton_group: str = ""
    after: tuple[str, ...] = ()
    writes_to: tuple[str, ...] = ()         # State slot keys
    reads_from: tuple[str, ...] = ()        # State slot keys
    reads_event_fields: tuple[str, ...] = ()    # "EventClass.field"
    writes_event_fields: tuple[str, ...] = ()   # "EventClass.field"

    source: DeclarationSource = DeclarationSource.UNKNOWN
    confidence: float = 0.0
    llm_evidence: str = ""

    def __post_init__(self):
        """hook/hooks 兼容处理 + 规范化。
        
        - 构造时若传入 ``hook=``（非 ""）但未传入 ``hooks=`` →
          ``hooks`` 设为 ``(hook,)``。单参数向后兼容。
        - 若两者都传入 → ``hooks`` 优先，``hook`` 从 ``hooks`` 派生。
        - ``hooks`` 始终按生命周期序排序。
        
        注意：``hook`` 是普通 dataclass 字段（非 @property），
        ``object.__setattr__`` 可直接写入。ComponentDecl 未冻结。
        """
        if self.hook and not self.hooks:
            object.__setattr__(self, "hooks", (self.hook,))
        if self.hooks and (not self.hook or self.hook != self.hooks[0]):
            object.__setattr__(self, "hook", self.hooks[0])
        # normalize hooks to lifecycle order
        # 只引用本模块（declaration.py）的 PROCESSOR_HOOK_NAMES（L1.1a 别名 = _HOOK_LIFECYCLE_ORDER），
        # 不直接引用未导入的 _HOOK_LIFECYCLE_ORDER — 避免未绑定名字。
        if self.hooks and len(self.hooks) > 1:
            order = {name: i for i, name in enumerate(PROCESSOR_HOOK_NAMES)}
            sorted_hooks = tuple(sorted(self.hooks, key=lambda h: order.get(h, 999)))
            if sorted_hooks != self.hooks:
                object.__setattr__(self, "hooks", sorted_hooks)
                object.__setattr__(self, "hook", sorted_hooks[0])
```

**L3.5 `HarnessConfig.copy()` 清除旧 `_rt_procs`**：

当 `copy(processors=...)` 传入新 processors 时，旧 `_rt_procs` 必须被清除。
当前行为（`harness.py:960-973`）：`copy()` 浅拷贝后显式复制 `_rt_procs` 列表，
再调用 `__post_init__`。但 `__post_init__` 只从 `processors` 分离实例 —
不在新 `processors` 中的旧 runtime 实例不会自动清除。

修正（第七轮 L2.3c 单源真相版 — `_rt_procs` 为只读视图，copy 不再触碰它）：

```python
def copy(self, **kwargs):
    new = copy.copy(self)               # _processor_regs 浅拷贝随对象保留（单源真相）
    new.plugins = list(self.plugins)
    new._rt_sandbox = getattr(self, "_rt_sandbox", None)
    if "processors" in kwargs:
        new._processor_regs = None      # override：丢弃旧 canonical，__post_init__ 从新混合序列重建
    for key, value in kwargs.items():
        setattr(new, key, value)
    new.__post_init__()                 # 无 override：重建 processors 视图；有 override：重建 canonical + 视图
    return new
```

**禁止**在 `copy()` 中修改 `self._rt_procs` — 只读派生视图，赋值或 append 即 `AttributeError`
（写入走 `add_runtime_reg` / `replace_processor_regs`）。

**L3.3 `order = 50` 的语义**：

`ComponentDecl.order = 50` 是"未知/未声明"标记（类似 HTTP 默认端口）。在 `_extract_declaration` 中：
- dict 中有 `_order_` → 取 dict 值（覆盖默认 50）
- dict 中无 `_order_` → WKD 有值 → 取 WKD 值
- 两者都无 → 保持 50

下游消费者若在意"是否已知"：
- `_order_` 键在 dict 中存在 → 已知
- `_order_` 键在 dict 中不存在 → 未知（即使 `decl.order == 50`，也无法区分"已知值为 50"和"未知"）

但 graph 消费者不应依赖 50 做决策。若需区分，检查 dict 键存在性。

**L3.4 `merge_declarations()` 多 hook 正确处理**（`declaration.py:209-234`）：

```python
def merge_declarations(declared, observed):
    result = {}
    for target in set(declared) | set(observed):
        dec = declared.get(target)
        obs = observed.get(target)
        if dec is None:
            result[target] = obs
        elif obs is None:
            result[target] = dec
        else:
            # hooks: observed wins if non-empty
            hooks = obs.hooks if obs.hooks else dec.hooks
            result[target] = ComponentDecl(
                target=target,
                hooks=hooks,
                order=dec.order,
                singleton_group=dec.singleton_group,
                after=dec.after,
                writes_to=obs.writes_to if obs.writes_to else dec.writes_to,
                reads_from=obs.reads_from if obs.reads_from else dec.reads_from,
                reads_event_fields=obs.reads_event_fields if obs.reads_event_fields else dec.reads_event_fields,
                writes_event_fields=obs.writes_event_fields if obs.writes_event_fields else dec.writes_event_fields,
                source=DeclarationSource.OBSERVATION_VERIFIED if obs.is_trusted() else dec.source,
                confidence=max(dec.confidence, obs.confidence),
            )
    return result
```

---

### L4: Snapshot — 图边创建

**文件**：`harnessx/graph/snapshot.py` — `to_graph()`

**L4.0 `_slug_from_target` 重构**（修复 VM8 ID 冲突）：

当前 `_slug_from_target` 返回 `proc:{slug}`。持久节点和 runtime 节点需要不同前缀，
所以将其拆分为两步：

```python
def _compute_slug(target: str) -> str:
    """CamelCase → snake_case，不带任何前缀。"""
    short = target.rsplit(".", 1)[-1] if "." in target else target
    slug = ""
    for i, ch in enumerate(short):
        if ch.isupper() and i > 0 and (short[i - 1].islower() or (i + 1 < len(short) and short[i + 1].islower())):
            slug += "_"
        slug += ch.lower()
    return slug
```

`_make_processor_node` 内部使用 `f"proc:{_compute_slug(target)}"` 作为 node_id。
Runtime 节点使用 `f"rt:{_compute_slug(target)}"` 作为 node_id。
两者共享同一个 slug 计算，前缀区分命名空间。VM8 期望的 `rt:sliding_window_memory` 得以满足。

**L4.1 ATTACHED_TO 边遍历 `decl.hooks`（非旧局部变量 `hook`）**：

替换 `snapshot.py:188-202` 的逻辑：不再使用 `proc_dict.get("_hook_", "")` 局部变量决定边。改为遍历 `_extract_declaration()` 返回的 `decl.hooks`：

```python
decl = _extract_declaration(proc_dict, target)
# ... 构建 node，注入 metadata ...

# ATTACHED_TO 边：遍历 decl.hooks
for hook_name in decl.hooks:
    if hook_name == "*":
        targets = list(PROCESSOR_HOOK_NAMES)  # 8 个
    elif hook_name in SKELETON_HOOK_NAMES:
        targets = [hook_name]
    else:
        targets = []
    for t in targets:
        hook_node_id = f"hook:{t}"
        if hook_node_id in snapshot.nodes:
            snapshot.edges.append(Edge(
                source_id=node_id, target_id=hook_node_id,
                edge_type=EdgeType.ATTACHED_TO, metadata={},
            ))
```

**L4.2 `PROCESSOR_HOOK_NAMES` 常量**（**从 core 导入**，graph 侧不再定义）：

```python
# declaration.py / snapshot.py — 都从 core 导入同一对象
from harnessx.core.processor import PROCESSOR_HOOK_NAMES
```

`snapshot.py` 通过 `from .declaration import PROCESSOR_HOOK_NAMES` 间接使用。
wildcard 展开、生命周期排序、hook 推导均引用 core 定义的同一 tuple，无分叉，
且不存在 core → graph 反向依赖。

**L4.3 动态 slot 节点**（替换 `_add_slot_nodes()` 的 6 固定 slot）：

1. 收集所有处理器节点的 `_writes_slots_` 和 `_reads_slots_` → slot key 集合
2. 为每个 key 创建 `slot:{key}` 节点
3. 6 个硬编码 slot key（`memory`, `plan`, `cost`, `tool_registry`, `workspace`, `sandbox`）作为保底始终创建

**L4.4 `_ctor_kwargs_` 保存**：

在 processor node metadata 中存储非 `_` 前缀的 dict 键（作为 `_ctor_kwargs_`），供反向转换恢复构造器参数。
必须 `deepcopy` — 浅拷贝会导致后续 mutation 污染 graph（VM12g 双向隔离）：

```python
from copy import deepcopy
ctor_kwargs = deepcopy({k: v for k, v in proc_dict.items()
                        if not k.startswith("_")})
if ctor_kwargs:
    extra["_ctor_kwargs_"] = ctor_kwargs
```

**L4.5 纯读取保证**：

`to_graph()` 绝不 import `_target_` 指向的类。所有元数据来自：
1. `proc_dict` 中的序列化键（`_hooks_`、`_order_` 等）
2. `WELL_KNOWN_DECLARATIONS`（静态 dict，无 import）
3. `_infer_hook_from_target()`（纯字符串匹配，无 import）

若 dict 元数据完整，即使 `_target_` 指向不可导入的模块，`to_graph()` 仍正常返回 snapshot。这确保 graph 导出是纯数据操作，不依赖运行时环境。

**例外（第八轮第 36 项）**：`to_graph()` 可能抛 `HarnessConflictError`（after 跨 order
矛盾 / 同 order cycle — 与 builder:678 / 运行时路由同一异常）。graph 层即校验层，
无效配置显式失败，不静默降级 hash。

**L4.6 EXECUTES_BEFORE 边 — persistent-relative 执行序（→ 主图 edges，影响 genotype）**：

`to_graph()` 在处理器节点 + ATTACHED_TO 边之后生成。**同一桶内的持久处理器按
(order, after 拓扑, persistent-seq) 的有效序连链**，相邻处理器之间一条
`EdgeType.EXECUTES_BEFORE` 边（metadata `{"hook": bucket_name}`）。桶集合 =
`PROCESSOR_HOOK_NAMES`（8）+ `"*"`；显式空 `_hook_=""` 的桶不生成链。

- 输入：`config._processor_regs` 中的 **SerializedReg**（dict 纯读取，不实例化 — L4.5）。
- **桶（hook）解析**：`_hook_` 键存在 → 原值；缺失 → **WKD.hook（注册桶 = 类的
  natural bucket：`_find_class_hook` 结果，MHP 无 `_hook` 时 `"*"`；见 WKD 修正表
  hook 字段）**；再缺失 → 推断。**绝不使用 declaration 的 `hooks[0]`（coverage 首元素）
  当桶** —— CostGuard 类 `_hooks_=("before_model",)` 但注册桶是类 `_hook` 或 `"*"`，
  两者对 multi-hook / 无 `_hook` 类分叉（运行时 natural bucket ≠ coverage 首元素）。
- **order 解析**：`_order_` 键存在 → 原值；缺失 → WKD.order（WKD 值 == 类 getattr
  值契约）；WKD 无条目 → **0**（与运行时 natural 的类默认一致）。**不使用
  ComponentDecl 的 50 未知哨兵参与排序**（L3.3：graph 消费者不得依赖 50 做决策；
  非 WKD 类 + 无键时 declaration 兜底是 50、runtime natural 是类默认 0 —— 用它排序，
  同桶混排 WKD 类时链序与运行序反向，破坏 I7）。
- sg/after 解析：`_extract_declaration` 同一回退链（dict 键存在 → 原值；缺失 → WKD）。
  运行时侧 natural 回退用实例化类元数据，以 code_introspection 与 WKD 对齐
  （declaration 既有契约；WKD 修正表原则保证 WKD 值 == 类 getattr 值，见实施约束第 6 条）。
- 排序：`stable_topological_sort(order_key, after_key, group_key, seq_key)`
  （core/runtime.py 共享函数 — 与 builder / 运行时路由同源）。
  **persistent-seq = SerializedReg 在 canonical 中的序号**（只计 persistent 相对位置，
  与运行时全局 seq 区分）。
- 冲突（after 指向更高 order / cycle）→ 传播 `HarnessConflictError`（见上例外）。
- **star 桶（"*"）独立链**；runloop `_star_procs` 固定先于 specific 执行
  （runloop.py:154-158）是执行语义，不参与跨桶编码（文档契约）。

**派生边契约**：EXECUTES_BEFORE 是派生边（to_graph 时从 canonical 计算）。`apply_edits`
后的快照无 canonical seq，无法重建 → **视为陈旧**（hash 已清空，VM11）；REMOVE_NODE
删除入射 order 边（edit.py:162-165 已删全部入射边）；下游需要有效 order 边必须先
重新 `to_graph()`（见 L5.5 补充）。

---

### L5: Runtime overlay

**前提**：`GraphSnapshot` 新增两个字段（`types.py:100-118`）：

```python
@dataclass
class GraphSnapshot:
    nodes: dict[str, Node] = field(default_factory=dict)
    edges: list[Edge] = field(default_factory=list)
    runtime_nodes: dict[str, Node] = field(default_factory=dict)   # ← 新增
    runtime_edges: list[Edge] = field(default_factory=list)        # ← 新增
    genotype_hash: str = ""
    deployment_hash: str = ""                                       # ← 新增
    phenotype_hash: str = ""
    source_config_hash: str = ""
```

**文件**：`harnessx/graph/snapshot.py` — `to_graph()` 末尾

**L5.1 Runtime 节点创建**（从 `config._rt_procs` 派生，`RuntimeReg` 解包，coerce 兜底）：

```python
for reg in (coerce_runtime_reg(p) for p in config._rt_procs):   # property 恒存在（__post_init__ 后）
    if reg is None:
        continue                         # 防御：dict 不应出现在 _rt_procs
    proc = reg.proc                      # 解包 processor 实例
    meta = get_graph_metadata(proc)      # 类级元数据（slot keys, event fields, dispatch hooks）
    cls = type(proc)
    target = getattr(proc, "__hx_target__", "") or f"{cls.__module__}.{cls.__qualname__}"

    # 用 RuntimeReg 的注册值覆盖类级默认值（不修改实例）
    meta["_hook_"] = reg.hook
    meta["_order_"] = reg.order
    meta["_singleton_group_"] = reg.singleton_group or ""
    meta["_after_"] = list(reg.after) if reg.after else []
    # _hooks_ handler coverage 四态（第八轮第 33 项，与 L2.2 / runloop "*" 桶行为闭合）：
    #   显式空 "" → 空桶：不执行，graph 也无自然 handler 边（VM14/VM18 一致）
    #   具体 hook → 收缩单元素；MHP+"*" → 保留 dispatch 推导；非 MHP+"*" → 8 个
    if reg.hook == "":
        meta["_hooks_"] = []
    elif reg.hook and reg.hook != "*":
        meta["_hooks_"] = [reg.hook]
    elif reg.hook == "*" and not isinstance(proc, MultiHookProcessor):
        meta["_hooks_"] = list(PROCESSOR_HOOK_NAMES)
    # else: keep dispatch-derived _hooks_ from get_graph_metadata() (MHP + "*")

    node_id = f"rt:{_compute_slug(target)}"
    # 去重：若 node_id 已存在，追加后缀
    suffix = 0
    base_id = node_id
    while node_id in snapshot.nodes or node_id in snapshot.runtime_nodes:
        suffix += 1
        node_id = f"{base_id}__rt{suffix}"

    extra = dict(meta)
    extra["_runtime_only"] = True
    extra["_target_"] = target

    node = Node(node_id=node_id, node_type=NodeType.PROCESSOR,
                label=target.rsplit(".", 1)[-1], metadata=extra)
    snapshot.runtime_nodes[node_id] = node

    # ATTACHED_TO 边：仅遍历 handler coverage（_hooks_），不再处理 "*" 通配
    for hook_name in meta["_hooks_"]:
        if hook_name in SKELETON_HOOK_NAMES:
            snapshot.runtime_edges.append(Edge(
                source_id=node_id, target_id=f"hook:{hook_name}",
                edge_type=EdgeType.ATTACHED_TO,
                metadata={"provenance": "runtime_only"},
            ))
```

**L5.1b 实例插件的 runtime 节点**（部署表示 — 第八轮第 36 项）：

`config.plugins` 中的**非 dict 项**（已挂载实例）→ 其 `.processors` 经
`coerce_runtime_reg` 走 L5.1 同流程（节点 `rt:{slug}` + `_hooks_` 四态 + ATTACHED_TO；
node_id 去重沿用同一 while 循环计数）。**id 身份去重**：与 canonical RuntimeReg 同一
实例（mounted 插件处理器同时在 `_rt_procs` 与 plugin.processors）→ 跳过
（原 harness.py:700-706 语义）。

```python
   # L5.1b 伪代码 — 与 L5.1 共用节点创建（把 L5.1 的节点创建循环提取为
   # `_add_runtime_node(snapshot, reg, node_id_seen)` 共享函数，node_id 去重
   # 计数由调用方传入、两边共用）：
   canonical_ids = {id(r.proc) for r in config._rt_procs}
   node_id_seen: set[str] = set()          # 与 L5.1 循环同一计数
   for plugin in config.plugins or []:
       if isinstance(plugin, dict):
           # dict 插件（YAML 声明）：纯读取不可枚举其处理器（L4.5 禁止 import）→
           # 不进 deployment 表示（运行时仍完整路由）；warning 收窄契约（L5.6）。
           _log.warning("dict 插件不进 deployment 表示（纯读取不可枚举）— %s", plugin)
           continue
       for proc in getattr(plugin, "processors", []) or []:
           if id(proc) in canonical_ids:   # 与 canonical RuntimeReg 同一实例 → 跳过
               continue                    #   （mounted 插件同时在 _rt_procs 与 plugin.processors）
           reg = coerce_runtime_reg(proc)
           if reg is None:
               continue
           _add_runtime_node(snapshot, reg, node_id_seen)
```

**dict 形式插件（YAML 声明）**：纯读取不可枚举其处理器（L4.5 禁止 import）→
**不进 graph deployment 表示**（运行时仍完整路由，harness.py:701-706）；`to_graph()`
检测到 dict 插件时打 `_log.warning`（deployment hash 契约收窄，见 L5.6）。

**L5.2 稳定 ID**：`rt:{slug}` 形式，`slug` 从 target 类名派生（与 `proc:...` 节点同规则）。同一 target 多次出现时追加 `__rt1`、`__rt2` 后缀。

**L5.3 动态 slot 节点 + WRITES_TO/READS_FROM 边**（在创建 runtime 节点之后执行）：

**关键约束**：runtime-only 创建的 slot 节点**不**放入 `snapshot.nodes`（否则 genotype_hash 会被 runtime overlay 影响）。
仅当同名 slot 已作为持久 slot 存在时，边指向主图节点；否则在 `runtime_nodes` 中创建 `rt:slot:{key}`。

```python
# 收集 runtime PROCESSOR 节点的所有 slot I/O key（仅遍历 PROCESSOR，跳过 slot 节点）
rt_slot_keys: set[str] = set()
for node_id, node in snapshot.runtime_nodes.items():
    if node.node_type != NodeType.PROCESSOR:
        continue
    for slot_name in node.metadata.get("_writes_slots_", []):
        rt_slot_keys.add(slot_name)
    for slot_name in node.metadata.get("_reads_slots_", []):
        rt_slot_keys.add(slot_name)

# 为尚不存在的 slot key 创建 runtime-only slot 节点（不污染 snapshot.nodes）
# 按排序后的 key 迭代，保证确定性
for key in sorted(rt_slot_keys):
    persistent_id = f"slot:{key}"
    rt_slot_id = f"rt:slot:{key}"
    if persistent_id not in snapshot.nodes and rt_slot_id not in snapshot.runtime_nodes:
        snapshot.runtime_nodes[rt_slot_id] = Node(
            node_id=rt_slot_id, node_type=NodeType.SLOT,
            label=key,
            metadata={"slot_name": key, "slot_type": "dynamic", "_runtime_only": True},
        )

# Runtime WRITES_TO / READS_FROM 边：仅遍历 PROCESSOR 节点
for node_id, node in snapshot.runtime_nodes.items():
    if node.node_type != NodeType.PROCESSOR:
        continue
    for slot_name in node.metadata.get("_writes_slots_", []):
        target = f"slot:{slot_name}" if f"slot:{slot_name}" in snapshot.nodes else f"rt:slot:{slot_name}"
        snapshot.runtime_edges.append(Edge(
            source_id=node_id, target_id=target,
            edge_type=EdgeType.WRITES_TO,
            metadata={"provenance": "runtime_only"},
        ))
    for slot_name in node.metadata.get("_reads_slots_", []):
        target = f"slot:{slot_name}" if f"slot:{slot_name}" in snapshot.nodes else f"rt:slot:{slot_name}"
        snapshot.runtime_edges.append(Edge(
            source_id=node_id, target_id=target,
            edge_type=EdgeType.READS_FROM,
            metadata={"provenance": "runtime_only"},
        ))
```

**genotype 隔离验证**：修改 runtime overlay → `snapshot.nodes` 不新增任何 slot 节点 → `genotype_hash` 不变（VM10）。

**L5.4 跨层边约束**：
- Runtime 节点的 ATTACHED_TO 边指向主图 `nodes` 中的 hook skeleton 节点（`hook:task_start` 等）— 这是跨 `runtime_nodes → nodes` 的边
- Runtime 节点的 WRITES_TO / READS_FROM 边可指向主图中的 slot 节点（包括 L5.3 刚创建的动态 slot）
- `genotype_hash()` 忽略 runtime 节点和边
- `deployment_hash()` 包含 runtime 节点和边

**L5.5 拓扑不变式 + Hash 缓存生命周期**：

**拓扑修改只能经 `apply_edits()`**。返回前验证运行时边端点存在，再清空 hash：

```python
def apply_edits(snapshot: GraphSnapshot, edits: list[GraphEdit]) -> GraphSnapshot:
    result = deepcopy(snapshot)
    # ... apply edits to result ...
    
    # 验证：所有 runtime edge 端点必须存在
    all_node_ids = set(result.nodes) | set(result.runtime_nodes)
    for edge in result.runtime_edges:
        if edge.source_id not in all_node_ids:
            raise GraphEditError(f"runtime edge source missing: {edge.source_id}")
        if edge.target_id not in all_node_ids:
            raise GraphEditError(f"runtime edge target missing: {edge.target_id}")
    
    # 清空所有 hash 缓存（编辑后失效）
    result.genotype_hash = ""
    result.deployment_hash = ""
    result.phenotype_hash = ""
    return result
```

- `to_graph()` 创建全新 snapshot，所有 hash 字段初始为 `""`。
- 各 hash 函数按需计算并写入对应字段。
- 原 `snapshot` 不变。
- 机械修正（同函数族）：`_apply_change_dependency` add 分支改用
  `edit.edge_type.to_edge(...)` —— 现实现硬编码 `EdgeType.ATTACHED_TO`，而
  remove 分支按 `edit.edge_type` 过滤，add/remove 不对称；`diff_graphs` 产出的
  非 ATTACHED_TO 边 roundtrip 即失真。

**EXECUTES_BEFORE 陈旧性补充（第八轮第 36 项）**：`apply_edits` 后的快照无 canonical
seq，order 边（L4.6 / L5.6）无法重建 → 视为陈旧；hash 已清空（VM11）保证不会把
陈旧链哈希出去。REMOVE_NODE 删除入射 order 边（edit.py:162-165 删全部入射边），
**且同一过滤必须作用于 `runtime_edges`**（L5.6 允许 persistent↔persistent 链进
runtime_edges、mixed 链引用主图 proc: 节点 —— 只过滤主图 edges 时，删除被引用
节点后 runtime edge 端点缺失，L5.5 端点校验必抛 GraphEditError，REMOVE_NODE
永不可用）。INSERT_NODE 不补 order 边（派生边，下次 to_graph 重算）。下游需要
有效 order 边先重新 `to_graph()`。

**L5.6 EXECUTES_BEFORE 边 — 完整 mixed 有效序（→ runtime_edges，影响 deployment/phenotype）**：

**部署表示 = 图可枚举的全部路由处理器**：canonical（SerializedReg 用 declaration 解析值、
RuntimeReg 用 reg 字段）+ 实例插件 processors（L5.1b）。**dict 插件处理器不在契约内**
（纯读取不可枚举 — 明确收窄，L5.1b warning）。

- 每桶（`PROCESSOR_HOOK_NAMES` + `"*"`；空桶 `""` 无链）内**全部有效处理器**按
  (order, after 拓扑, 全局 seq) 连链；边进 `snapshot.runtime_edges`（deployment-only，
  genotype 不可见）。
- 全局 seq：canonical 下标；插件 seq = `max(seq)+1`（空洞安全，与 L2.3c 规则 4 同规则）。
- 排序与运行时 `_route_processors` **同函数同参数**（`stable_topological_sort`）→
  **graph 链 ≡ 运行时实际执行序**：S,R 与 R,S 同桶 → 链反向 → deployment_hash 不同
  （VM19）。
- 节点 id 沿用 L5.1 / L5.1b 分配（`_compute_slug` + 去重后缀），与节点创建循环同一遍、
  同一计数 — 链引用的端点一定存在。
- 冲突 → 传播 `HarnessConflictError`（同 L4.6）。
- 单桶内持久↔持久处理器之间也可出链（端点均在主图 nodes，边在 runtime_edges —
  允许；L5.5 校验取 nodes ∪ runtime_nodes）。

**伪代码（核心）**——排序与运行时 `_route_processors` **同函数同参数**：

```python
   from harnessx.core.runtime import RoutingEnvelope, RuntimeReg, stable_topological_sort

   mixed: list[RoutingEnvelope] = []
   for seq, r in enumerate(config._processor_regs):
       if isinstance(r, SerializedReg):
           decl = _extract_declaration(r.dict_ref, str(r.dict_ref.get("_target_", "")))
           mixed.append(RoutingEnvelope(RuntimeReg(
               proc=None,                 # 纯读取：L4.5 禁止实例化；链端点用节点 id
               hook=_bucket(decl, r.dict_ref),          # L4.6 桶解析（dict 键 → WKD.hook → 推断）
               order=_order_parse(decl),  # L4.6 order 解析（dict 显式 → WKD → 0，不用 50 哨兵）
               singleton_group=decl.singleton_group or None,
               after=decl.after,
           ), seq))
       else:
           mixed.append(RoutingEnvelope(r, seq))        # RuntimeReg 用 reg 字段
   # 实例插件尾部（seq = max+1；id 去重同 L2.3c 规则 4；dict 插件跳过 — L5.1b warning）
   base_seq = max((e.seq for e in mixed), default=-1) + 1
   for plugin in config.plugins or []:
       if isinstance(plugin, dict):
           continue
       for proc in getattr(plugin, "processors", []) or []:
           reg = coerce_runtime_reg(proc)
           if reg is not None:
               mixed.append(RoutingEnvelope(reg, base_seq))
               base_seq += 1

   # 每桶排序连链（桶集合 = PROCESSOR_HOOK_NAMES + "*"；空桶 "" 无链）
   for bucket in (*PROCESSOR_HOOK_NAMES, "*"):
       bucket_envs = [e for e in mixed if e.reg.hook == bucket]
       if not bucket_envs:
           continue
       ordered = stable_topological_sort(
           bucket_envs,
           order_key=lambda e: e.reg.order,
           after_key=lambda e: e.reg.after,
           group_key=lambda e: e.reg.singleton_group or "",
           seq_key=lambda e: e.seq,
       )
       ids = [bucket_node_id(e) for e in ordered]       # 与 L4.6/L5.1/L5.1b 已分配的节点 id 对齐
       for a, b in zip(ids, ids[1:]):
           snapshot.runtime_edges.append(Edge(
               source_id=a, target_id=b,
               edge_type=EdgeType.EXECUTES_BEFORE,
               metadata={"hook": bucket},
           ))
```

`bucket_node_id(e)`：SerializedReg 派生项 → 主图 `proc:` 节点（L4.6 已分配）；
RuntimeReg / 插件项 → `rt:` 节点（L5.1 / L5.1b 已分配）——节点创建与链生成共用
同一 id 分配规则（`_compute_slug` + 去重后缀），端点一定存在（L5.5 校验兜底）。

---

### L6: Identity & Hash

**文件**：`harnessx/graph/identity.py`

**L6.1 无序列表键白名单**（`_sort_dict` 仅在深度 0 且键在白名单中时排序 primitive list）：

```python
_ORDER_INDEPENDENT_LIST_KEYS: frozenset[str] = frozenset({
    "_after_",
    "_writes_slots_",
    "_reads_slots_",
    "_reads_event_fields_",
    "_writes_event_fields_",
    "_hooks_",
})
```

**L6.2 `_sort_dict(d, *, at_root=True)`** — 核心约束：

- **`at_root=True`**（node.metadata 顶层）：键在白名单中 → primitive list 排序（确定性）；键不在白名单中 → list 保序。
- **`at_root=False`**（任何嵌套 dict，不限于 `_ctor_kwargs_`）：**所有 list/tuple 严格保序**。即使键恰好叫 `_after_`，也不排序。
- 递归进入任何嵌套 dict 时 `at_root=False`，规则一致传播。

```python
def _sort_dict(d: dict, *, at_root: bool = True) -> dict:
    result = {}
    for key in sorted(d):
        val = d[key]
        if isinstance(val, dict):
            result[key] = _sort_dict(val, at_root=False)
        elif isinstance(val, (list, tuple)):
            if not at_root:
                # 任何嵌套层：绝对保序
                result[key] = list(val)
            elif key in _ORDER_INDEPENDENT_LIST_KEYS:
                # 顶层白名单键：排序以求确定性
                try:
                    result[key] = sorted(val, key=str)
                except TypeError:
                    result[key] = list(val)
            else:
                result[key] = list(val)
        else:
            result[key] = val
    return result
```

**L6.3 `deployment_hash(snapshot) → str`** — 含 runtime overlay，**显式排除** OBSERVED_* 边：

```python
def deployment_hash(snapshot: GraphSnapshot) -> str:
    """Compute deployment hash and cache it on the snapshot."""
    all_nodes = {**snapshot.nodes, **snapshot.runtime_nodes}
    all_edges = snapshot.edges + snapshot.runtime_edges
    h = _hash_nodes_edges(all_nodes, all_edges, include_observed=False)
    snapshot.deployment_hash = h
    return h
```

OBSERVED_* 边属于运行时 trace 数据，不属于部署配置。`include_observed=False` 是 deployment 的硬要求。
**必须写入** `snapshot.deployment_hash`，后续代码依赖此缓存字段。

同时，`genotype_hash()` 和 `phenotype_hash()` 也应在计算后写入对应的 `snapshot` 字段（`genotype_hash` / `phenotype_hash`），
与 `deployment_hash` 保持一致的缓存契约。

**L6.4 `genotype_hash()`** — 仅操作 `nodes`/`edges`，不变。调用 `_hash_nodes_edges(nodes, edges, include_observed=False)`。

**EXECUTES_BEFORE 参与 hash（第八轮第 36 项）**：order 边作为普通边经共享内核哈希 —
主图 persistent-relative 链（L4.6）进 genotype；runtime_edges 的 mixed 链（L5.6）进
deployment/phenotype（runtime_edges 天然被 deployment 合并、被 genotype 忽略）。
新增边会改变现有 graph 测试的边数/hash 断言 — 测试随实现更新（实施约束第 4 条）。

**L6.5 `phenotype_hash()`** — **= deployment + observed**：合并主图与 runtime overlay，
再含 OBSERVED_CONTROL / OBSERVED_DATA 边：

```python
def phenotype_hash(snapshot: GraphSnapshot) -> str:
    all_nodes = {**snapshot.nodes, **snapshot.runtime_nodes}
    all_edges = snapshot.edges + snapshot.runtime_edges
    h = _hash_nodes_edges(all_nodes, all_edges, include_observed=True)
    snapshot.phenotype_hash = h
    return h
```

层级关系：genotype ⊆ deployment ⊆ phenotype（deployment = genotype + runtime overlay；
phenotype = deployment + observed 边）。

**L6.6 共享内核 `_hash_nodes_edges(nodes, edges, *, include_observed)`**：

替换现有的 `_canonical_form()`，提取为接受显式 nodes/edges 参数的共享函数，供三个 public hash 函数复用。

---

### L7: 反向转换

**文件**：`harnessx/graph/transform.py` — `graph_to_config_dict()`

**L7.1 保留的元数据键**：

```python
_METADATA_KEYS_TO_PRESERVE = (
    "_hook_", "_hooks_", "_order_", "_singleton_group_", "_code_hash",
    "_writes_slots_", "_reads_slots_",
    "_reads_event_fields_", "_writes_event_fields_",
    "_after_",
)
```

**L7.2 `_ctor_kwargs_` 恢复**：从 node.metadata 读 `_ctor_kwargs_`，`deepcopy` 后恢复到输出 dict 顶层。`deepcopy` 防止后续修改污染 graph。

**L7.3 `_after_` 优先恢复原始值**：若 node.metadata 同时有 `_after_` 和 `_ctor_kwargs_` 中的 after，`_after_` 优先（它是规范化的 singleton group 列表）。

**L7.4 处理器覆盖**：

| 条件 | 行为 |
|------|------|
| `_runtime_only` 不存在或 False | 完整输出到 `processors` |
| `_runtime_only == True` | 跳过 |

**注**：`_runtime_reason` warning 承诺**删除** —— 该字段无生产者，且
`graph_to_config_dict()` 只遍历 `snapshot.nodes`（看不到 `runtime_nodes`），
warning 永远无法触发。若未来需要，由 runtime overlay 生成 reason 并单独扫描
runtime 节点，另行成规格。

**L7.5 falsey 元数据保留**：值为 `0`、`""`、`[]` 的元数据键仍需输出。使用 `"key" in metadata and metadata["key"] is not None` 判断，不跳过 falsey 值。

---

### L8: 数据通道 schema

**L8.1 当前支持的通道**：

| 通道 | 类属性 | 序列化键 | ComponentDecl 字段 |
|------|--------|---------|-------------------|
| State slot writes | `_writes_slot_keys` | `_writes_slots_` | `writes_to` |
| State slot reads | `_reads_slot_keys` | `_reads_slots_` | `reads_from` |
| Event field reads | `_reads_event_fields` | `_reads_event_fields_` | `reads_event_fields` |
| Event field writes | `_writes_event_fields` | `_writes_event_fields_` | `writes_event_fields` |

**L8.2 格式**：
- State slot：裸字符串 `"model.route"`（slot key）
- Event field：`"EventClass.field"` 格式，如 `"BeforeModelEvent.cumulative_cost_usd"`

**L8.3 已声明处理器清单**（实施时审计，非本次范围）：

| 处理器 | writes_slots | reads_slots | reads_event | writes_event |
|--------|-------------|-------------|-------------|-------------|
| ModelRouterProcessor | `("model.route",)` | — | — | — |
| CostGuardProcessor | — | — | `("BeforeModelEvent.cumulative_cost_usd",)` | — |

**VM7 桥接要求**：`ModelRouterProcessor` 需在 `__init__` 中同步 `slot_key` 到 `_writes_slot_keys`：
```python
class ModelRouterProcessor(MultiHookProcessor):
    _writes_slot_keys: tuple[str, ...] = ("model.route",)  # 类默认

    def __init__(self, slot_key: str = "model.route", ...):
        self.slot_key = slot_key
        self._writes_slot_keys = (slot_key,)  # 实例级覆盖，供 get_graph_metadata() 读取
        ...
```
`get_graph_metadata()` 通过 `_read("_writes_slot_keys", ())` 从实例 `__dict__` 读到 `("custom.route",)`，
从而在 graph 中创建正确的 `slot:custom.route` 节点和 WRITES_TO 边。

（其余处理器逐类声明，在工作分支中补齐，不作为 v5 审批前提。）

---

## 12 Blocker 闭合清单

| # | Blocker | 闭合规则 | 所在层 |
|---|---------|---------|--------|
| 1 | Hook MRO + 字母序 | MRO 遍历 `base.__dict__` + `_HOOK_LIFECYCLE_ORDER` 排序 | L1.3 |
| 2 | `_extract_declaration` post_init 同步 | 累积所有字段后一次性构造 `ComponentDecl(...)`，确保 `__post_init__` 正确触发 hook↔hooks 同步 | L3.1, L3.2 |
| 3 | `merge_declarations` 压缩多 hook | 改为 `hooks=obs.hooks if obs.hooks else dec.hooks`，含全部新字段 | L3.4 |
| 4 | Snapshot 用旧 `hook` 变量建边 | ATTACHED_TO 边改为遍历 `decl.hooks` | L4.1 |
| 5 | Builder 非空序列化与回退冲突 | `get_graph_metadata()` 始终写入所有键（包括空列表）→ 键存在即"已声明" | L1.4, L2.1 |
| 6 | order=50 无法区分缺失 | `_order_` 键存在 → 已知值；键不存在 → 未知（即使 decl 显示 50）。WKD 无 order 的条目不写 50，留默认 | L3.3 |
| 7 | ModelRouter override 类型不匹配 | `get_graph_metadata()` 从实例 `__dict__` 读属性（`_read` helper），ModelRouter 在 `__init__` 中设 `self._writes_slot_keys = (slot_key,)` 覆盖类默认值。无需全局 override map | L1.4 |
| 8 | `_rt_procs_meta` 手工同步不可行 | 取消 `_rt_procs_meta` sidecar。`to_graph()` 直接对 `_rt_procs` 实例调用 `get_graph_metadata()` | L2.3, L5.1 |
| 9 | Runtime overlay 无稳定 ID/跨层约束/失效 | `rt:{slug}` ID，跨 `runtime_nodes → nodes` 边，`apply_edits()` 清空 `deployment_hash` | L5 |
| 10 | Hash 不区基因型/表型 + `_ctor_kwargs_` 排序 | 白名单式 `_ORDER_INDEPENDENT_LIST_KEYS`；`_hash_nodes_edges` 共享函数处理 observed 过滤 | L6 |
| 11 | Transform falsey / after 优先级 / deepcopy | 键存在性保留 falsey；`_after_` 优先于 ctor kwargs；`deepcopy` ctor kwargs | L7 |
| 12 | 46 处理器数据通道审计未闭环 | 四通道 schema（slot read/write + event read/write）已定义；逐类声明在分支中补齐 | L8 |

---

## WELL_KNOWN 修正（最小集）

**当前代码的 WKD 错误**（基于已提交源码，非工作区未提交修改）：

| 条目 | 错误 | 修正 |
|------|------|------|
| ProgressiveSkillLoader | `singleton_group="skill_loader"` | `"progressive_skill_loader"` |
| ProgressiveSkillLoader | order 缺失 | `order=12` |
| ProgressiveSkillLoader | hooks 缺失 | `hooks=("task_start","step_start","before_model","task_end")` |
| CostGuardProcessor | `reads_from=("cost",)` — 幻影依赖 | 删除；添加 `reads_event_fields=("BeforeModelEvent.cumulative_cost_usd",)` |
| CostGuardProcessor | hooks 缺失 | `hooks=("before_model",)` |
| ModelRouterProcessor | `writes_to=("model_route",)` — 下划线 | `("model.route",)` — 点号 |
| ModelRouterProcessor | hooks 缺失 | `hooks=("task_start","task_end")` |
| EvaluationProcessor | order 缺失（默认 50） | `order=0`（`getattr(cls, "_order", 0)` 返回 0） |
| EvaluationProcessor | hooks 缺失 | `hooks=("task_end",)` |
| LLMJudgeProcessor | order 缺失（默认 50） | `order=0`（`getattr(cls, "_order", 0)` 返回 0） |
| LLMJudgeProcessor | hooks 缺失 | `hooks=("task_end",)` |
| 所有 WKD 条目 | 缺少 `hooks` 字段 | 每个条目必须声明 `hooks`（匹配类的实际 `on_*` 方法）；VM14 fallback 依赖此字段 |
| 所有 WKD 条目 | 缺少 `hook`（注册桶）字段 | 每个条目必须声明 `hook`（= 类的 natural bucket：`_find_class_hook(cls)` 结果，MHP 无 `_hook` 时 `"*"`）——**不是** `hooks[0]`（coverage 首元素，multi-hook 处理器与其分叉）；L4.6/L5.6 的桶解析依赖此字段（dict 无 `_hook_` 的 legacy 场景） |

**WKD 审计原则**：每个条目的 `order`、`hooks`、`singleton_group` 必须与 `getattr(cls, name, default)` 返回值一致。
若类未声明 `_order` → WKD 设为 `0`（`getattr` 默认值），不可设 `50`（`50` 是 ComponentDecl 的"未知"标记，
在 WKD 中表示"已知其值为 50"，与运行时行为 `0` 冲突）。

---

## 实施约束

1. 当前工作区有未提交修改（`declaration.py`、`adapter.py`、`snapshot.py` 已回退到最简基线）。新代码在此基线之上构建。
2. 实施顺序：L1（类属性 + hook 推导 + `_find_class_hook`）→ L2（`core/runtime.py`（含 `normalize_processor_reg` + `HarnessConflictError` 迁移 + `stable_topological_sort` 泛化）+ Builder 序列化 + `_route_processors` + `__post_init__` 迁移 + 单 owner fail-fast + shielded cleanup）→ L3+L4（Snapshot + 图边 + L4.6 持久序链）→ L5+L6（Runtime + L5.1b/L5.6 部署链 + Hash）→ L7（Transform）→ 消费者迁移（L2.3a 表格 + copy 六调用点）→ 测试修正
3. 每个 L 层完成后运行 `smoke_test_graph.py` 确认不退化
4. `tests/graph/test_s1_snapshot.py:76-87` 的 `"*"` → 10 边断言改为 8 边；EXECUTES_BEFORE 新增边后，现有 graph 测试中的总边数/hash 断言按新图更新（以 VM19/VM20 为准）
5. **迁移顺序硬约束**：`core/runtime.py`（`RuntimeReg` + `SerializedReg`(动态 presence property) + `RoutingEnvelope` + `normalize_processor_reg` + `HarnessConflictError` + `stable_topological_sort`(group_key) + owner registry）先独立写测 → 然后**同一步**切换 harness.py：`__init__`（补 `_cleanup_task = None`）+ `__post_init__`（构建 `_processor_regs` 单源真相 + `_rt_procs` 只读 tuple 视图 + 写 API + `_refresh_processors_view`）、`_instantiate_runtime`（只消费 canonical + 统一 natural 解析 + plugin 尾部 envelopes）、`_route_processors`（envelope 分桶 + `stable_topological_sort`）、owner claim（L2.3b）、shielded `cleanup`、`copy()`/`canonicalize()`（canonical 保留/转换）→ 再切换 L5.1/L5.1b/L5.6 与 graph 侧（含 edit.py `_compute_slug` 迁移 + EXECUTES_BEFORE 链）→ 最后批量迁移写入端（`add_runtime_reg`/`replace_processor_regs`：digester、tau2、spawn）**与 copy(processors=...) 六调用点**（cli:367/489/1068、api:316、gateway:173/198）及 unwrap 读取端。不允许中间态（否则 record 漏进 runloop 或双源分叉）。
6. **WKD == 类值一致性（L4.6/L5.6 链序前提）**：WKD 修正表承诺"每个条目的 order / hooks / hook / singleton_group 必须与 `getattr(cls, name, default)` / `_find_class_hook(cls)` 返回值一致"——补测试断言（WKD 条目 vs 类属性）；`to_graph()` 检测不一致时 `_log.warning`。否则类代码更新而 WKD 遗漏时，L4.6/L5.6 链序与运行时执行序静默分叉（破坏 I7 / VM19）。

## 验证矩阵（18 项，每项可独立验证）

### VM1: MRO — Child 继承 Parent hook + 新增自身 hook

| 场景 | 验收标准 |
|------|---------|
| `Child(Parent)`, Parent 定义 `on_step_end`, Child 不覆写 | `compute_effective_hooks(Child)` 含 `step_end` |
| Child 新增 `on_before_model` | 结果含 `step_end` AND `before_model`，按生命周期序 |
| Parent 的 `on_step_end` 被 GrandChild 用 `@on()` 覆盖 | GrandChild 结果含被覆盖后的 hook，不含重复 |
| 继承的 `@on()` 方法（Parent 用 `@on()` 声明，Child 不覆写）| MRO 遍历到 Parent.__dict__，收集到对应 event_class |

### VM2: Graph 边 — 指定 hook → 精确边数

| 场景 | 验收标准 |
|------|---------|
| `_hooks_=["task_start","task_end"]` | 产生恰好 2 条 ATTACHED_TO 边：`→ hook:task_start` + `→ hook:task_end` |
| `_hooks_=["before_model"]` | 恰好 1 条边 |
| `_hooks_=["*"]` | 恰好 8 条边（全 hook，不含 model/tool） |
| `_hooks_=[]` | 0 条 ATTACHED_TO 边 |

### VM3: ComponentDecl — `hook=` 向后兼容 + hook/hooks 冲突

| 场景 | 验收标准 |
|------|---------|
| `ComponentDecl(target="x", hook="task_start")` | `decl.hooks == ("task_start",)`，`decl.hook == "task_start"` |
| `ComponentDecl(target="x", hooks=("a","b"))` | `decl.hooks == ("a","b")` 或生命周期排序 |
| 同时传 `hook="x"` 和 `hooks=("y",)` | `hooks` 优先 → `decl.hooks == ("y",)`，`decl.hook == "y"` |
| 旧代码 `decl.hook` 访问 | 不报错，返回 `hooks[0]` 或 `""` |

### VM4: merge_declarations — 保留 reads_event_fields + 多 hook

| 场景 | 验收标准 |
|------|---------|
| `obs.hooks=("task_start","task_end")` + `dec.hooks=("task_start",)` | 结果 `("task_start","task_end")` |
| `obs.reads_event_fields=("Ev.f",)` 非空 | 合并后保留 |
| `obs.reads_event_fields` 为空 | 回退到 `dec.reads_event_fields` |
| 所有新增字段（writes_event_fields, reads_event_fields）参与合并 | 逐字段按 `obs X if obs X else dec X` 规则 |

### VM5: Builder — `_hook_` 始终为 entry.hook，`_hooks_` 按桶收缩

| 场景 | 验收标准 |
|------|---------|
| `builder.add(SomeProcessor(), hook="task_end")` 但 `SomeProcessor._hook = "step_start"` | serialized 中 `_hook_="task_end"`，`_hooks_=["task_end"]` |
| entry.hook == class._hook（如 class._hook="task_end"，entry 也是 "task_end"）| `_hook_="task_end"`（无条件写入），`_hooks_=["task_end"]`（因 entry.hook 是具体 hook 非 "*"，收缩为单元素）|
| entry.hook == "*"（MultiHookProcessor）| `_hook_="*"`（无条件写入注册桶），`_hooks_` 保留 `get_graph_metadata()` 从 dispatch 派生的结果 |
| entry.hook == "*" 的处理器之后通过 `_extract_declaration` 读回 | `_hook_="*"` 可被下游识别为 MultiHookProcessor |

### VM6: Builder — 所有空依赖键必须序列化

| 场景 | 验收标准 |
|------|---------|
| 处理器无任何 slot 依赖（`_writes_slot_keys=()`、`_reads_slot_keys=()`） | serialized 中 `_writes_slots_`、`_reads_slots_` 为 `[]`（键存在） |
| 处理器无 event 依赖 | `_reads_event_fields_`、`_writes_event_fields_` 为 `[]`（键存在） |
| 处理器有 slot 依赖 | 值为实际列表 |

验证方式：检查 serialized dict 的 key set，所有 8 个元数据键必须存在。

### VM7: ModelRouter — 自定义 slot_key → 正确 WRITES_TO 边

| 场景 | 验收标准 |
|------|---------|
| `ModelRouterProcessor(slot_key="custom.route")` | 实例 `__dict__["_writes_slot_keys"] = ("custom.route",)`；`get_graph_metadata(instance)` 返回 `_writes_slots_=["custom.route"]`；graph 中创建 `slot:custom.route` 节点 + WRITES_TO 边 |
| 默认 `slot_key="model.route"` | 类属性 `_writes_slot_keys=("model.route",)`；graph 中创建 `slot:model.route` 节点 + WRITES_TO 边 |
| slot key 含点号 | 不被截断，完整保留 |
| 实现方式 | `get_graph_metadata()` 的 `_read()` 从实例 `__dict__` 读 `_writes_slot_keys`，覆盖类默认值。ModelRouterProcessor 在 `__init__` 中设置 `self._writes_slot_keys = (slot_key,)` |

### VM8: Runtime — 稳定无冲突 ID + 跨层边

| 场景 | 验收标准 |
|------|---------|
| 同一 target 多次出现的 runtime 处理器 | ID 为 `rt:{slug}`、`rt:{slug}__rt1`、`rt:{slug}__rt2`，不冲突 |
| Runtime ATTACHED_TO 边 | 指向主图 `hook:task_start` 等节点（跨 `runtime_edges → nodes`） |
| Runtime WRITES_TO 边 | 可指向主图中的 slot 节点 |
| 多次调用 `to_graph()` 同一 config | runtime node ID 稳定（确定性） |

### VM9: Copy — 替换 processors 后清除**副本**的旧 _rt_procs（不修改原对象）

| 场景 | 验收标准 |
|------|---------|
| `new_config = config.copy(processors=[new_proc])` 后 | `new_config._rt_procs` 为空列表或只含新 processors 对应的实例 |
| `to_graph(new_config)` 后 | 旧 runtime 节点不出现 |
| 原始 `config._rt_procs` | **不变**（copy 不能修改原对象） |
| 未替换 processors 的 copy | `new_config._rt_procs` 与 `config._rt_procs` 相等（浅拷贝列表） |

### VM10: Hash — genotype/deployment 排除 OBSERVED_*，phenotype 包含

| 场景 | 验收标准 |
|------|---------|
| 添加 OBSERVED_CONTROL 边 → genotype_hash | 不变 |
| 添加 OBSERVED_CONTROL 边 → deployment_hash | 不变（deployment = genotype + runtime overlay，不含 observed） |
| 添加 OBSERVED_CONTROL 边 → phenotype_hash | 改变 |
| 修改 runtime overlay → genotype_hash | 不变 |
| 修改 runtime overlay → deployment_hash | 改变 |
| 修改 runtime overlay → phenotype_hash | 改变（phenotype = deployment + observed，含 runtime overlay） |
| 创建 runtime-only slot（`rt:slot:custom`）→ `snapshot.nodes` | 不新增任何 slot 节点（仅在 `runtime_nodes` 中创建） |
| 创建 runtime-only slot → genotype_hash | 不变（genotype 仅读取 `snapshot.nodes` + `snapshot.edges`） |
| 层级关系 | `genotype ⊆ deployment ⊆ phenotype` 单调：相同 overlay/observed 变更下三 hash 的变化方向一致 |

### VM11: Hash — apply_edits 返回新对象，hash 缓存必须清空

| 场景 | 验收标准 |
|------|---------|
| `result = apply_edits(snapshot, edits)` 后 | `result.deployment_hash == ""`，`result.genotype_hash == ""`，`result.phenotype_hash == ""` |
| 原始 `snapshot.deployment_hash` | 不变（apply_edits 不修改原对象） |
| `to_graph(config)` 创建的新 snapshot | `snapshot.deployment_hash == ""`，`snapshot.genotype_hash == ""` |
| `deployment_hash(snapshot)` 调用后 | `snapshot.deployment_hash` 非空，且对相同结构产生相同 hash |
| `result2 = apply_edits(result, edits2)` 后 | `result2.deployment_hash == ""`（新对象 hash 清空，`result` 不变） |

### VM12: Round-trip — 7 子项全覆盖

| # | 场景 | 验收标准 |
|---|------|---------|
| 12a | `_order_=0` | `to_graph → graph_to_config_dict` 往返后 `_order_` 为 `0`（非缺失） |
| 12b | 空 singleton_group | `_singleton_group_=""` 往返后仍为 `""`（键存在） |
| 12c | 未解析的 `_after_` | `_after_=["unknown_sg"]` 往返后仍为 `["unknown_sg"]` |
| 12d | 嵌套 dict（ctor kwargs）| `{"nested": {"a": 1}}` 往返后结构不变 |
| 12e | 有序 list（ctor kwargs）| `{"tags": ["c", "a", "b"]}` 往返后顺序不变 |
| 12f | falsey ctor 参数 | `{"enabled": False, "count": 0, "name": ""}` 往返后全部保留 |
| 12g | 双向 deepcopy | 修改往返后的 dict → 不影响原始 graph node metadata；反之亦然 |

### VM13: Pure-read — 不存在的 `_target_` 仍可 to_graph()

| 场景 | 验收标准 |
|------|---------|
| dict 中有完整元数据（`_target_="nonexistent.module.Class"` + `_hooks_=["task_start"]` + …） | `to_graph()` 不 import 该模块，不报错，正常返回 snapshot |
| `_target_` 不在 `WELL_KNOWN_DECLARATIONS` 中 | 不报错，使用 dict 中的元数据 |
| 验证方式 | `to_graph()` 调用期间不触发 `ModuleNotFoundError` |

### VM14: Legacy + CostGuard — WKD fallback + 幻影依赖清除

| 场景 | 验收标准 |
|------|---------|
| 旧 YAML（仅含 `_target_`，无任何新元数据键）→ to_graph() | WKD fallback 注入正确的 hooks、order、singleton_group |
| 旧 YAML → `_extract_declaration` | 不会因缺少键而抛 KeyError |
| CostGuardProcessor → to_graph() | WKD 声明 `reads_event_fields=("BeforeModelEvent.cumulative_cost_usd",)`，无 `reads_from` → graph 中**不存在** `READS_FROM slot:cost` 边 |
| CostGuardProcessor → `_extract_declaration` | `decl.reads_event_fields == ("BeforeModelEvent.cumulative_cost_usd",)` |
| CostGuardProcessor → `_extract_declaration` | `decl.reads_from == ()` |
| 显式 `_hooks_=[]` 的处理器（键存在，值空）| WKD 不回退填充 hooks；`decl.hooks == ()` |
| 显式 `_hooks_=[]` + 无 WKD + 无字符串推断 | `decl.hooks == ()`（不抛异常，0 条 ATTACHED_TO 边） |
| legacy `_hook_=""`（键存在，值空串）| `hooks_present=True` 且 `decl.hooks == ()`（空串被过滤）；WKD 与字符串推断均被禁止；0 条 ATTACHED_TO 边 |

### VM15: Hook 契约 — MHP natural `_hook_` = 最近非空类 `_hook`，无则 `"*"`

| 场景 | 验收标准 |
|------|---------|
| MHP 子类声明 `_hook="task_start"` | `get_graph_metadata(cls)["_hook_"] == "task_start"`（**不是** `"*"`）；`_hooks_ == ["task_start"]` |
| MHP 子类无 `_hook`（含 `_hook=None` / `_hook=""`）| `get_graph_metadata(cls)["_hook_"] == "*"`；`_hooks_` 为 dispatch 推导结果 |
| 父类 `_hook="task_end"`，子类显式 `_hook=None` | `_hook_ == "*"`（子类空值停止 MRO，不得继承父类 hook）；`_hooks_` 为子类 dispatch 推导 |
| `coerce_runtime_reg(裸实例)` 的 hook | 等于该实例的 natural bucket（`get_graph_metadata(instance)["_hook_"]`） |

### VM16: Runtime — 裸 processor 直接注入 + runloop 按 reg.hook 分桶

| 场景 | 验收标准 |
|------|---------|
| `HarnessConfig(processors=[SomeProcessor()])` 裸实例注入 | `__post_init__` 经 `coerce_runtime_reg` 包成 `RuntimeReg`；`config._rt_procs` 中无裸实例 |
| `_route_processors(flat)` 含 record | record 按 `reg.hook` 分桶，桶内元素是 `reg.proc` 实例（record 永不进入 proc_dict） |
| record 注册 `hook="task_end"` | 只出现在 `proc_dict["task_end"]`，runloop 在该 hook 执行 `reg.proc` |
| runloop / `_bind_*` / light_meta 消费 `_rt.processors` | 全部拿到裸实例，无需解包 |
| `agent.py` label 读取 / `digester.py` isinstance 检查 | `unwrap_runtime_proc` 后行为与裸实例一致（label 非 "RuntimeReg"，isinstance 命中） |
| 非 MHP 处理器 + `hook="*"` 注册（builder 显式传）| serialized `_hook_="*"` 且 `_hooks_ == PROCESSOR_HOOK_NAMES`（8 个）→ graph 恰好 8 条 ATTACHED_TO 边，与 runloop `_star_procs` 在 8 个 hook 执行一致 |
| MHP + `hook="*"` 注册 | `_hook_="*"`，`_hooks_` 为 dispatch 推导（非 8 个全量，除非确实覆写全部 handler） |
| 裸、无 `_hook` 的 function processor 直接注入 | `coerce_runtime_reg` 默认桶 `"*"` → runloop 行为与现状一致（harness.py:391 `or "*"`） |

### VM17: Runtime — 单 owner fail-fast（身份 registry 版）

| 场景 | 验收标准 |
|------|---------|
| `h1 = model.agentic(config)`；`h2 = model.agentic(config)`（同一批实例）| 第二次 `claim_owners` 抛 `ValueError`（registry 中 token 不同） |
| 同一 Harness 内同实例多次注册 | 不抛错（同 token 幂等） |
| claim 后 | registry 中 `id(proc) → (proc, token)`；token 为 `object()`（非 `id`） |
| 不可哈希处理器（定义 `__eq__` 无 `__hash__`）| claim 不抛 TypeError（身份去重按 `id`，registry 存强引用防 id 复用） |
| claim 覆盖面 | 最终路由 `_rt.processors` 中的**全部 MHP**（canonical RuntimeReg 实例 + 尾部 plugin 实例 + extra_processors 实例一次覆盖；serializable 实例化出的新 MHP 也 claim，每次构造全新实例无害）；claim 面 = `_bind_*` 绑定面（harness.py:1073）|
| 两阶段 | 任一实例已属其他 token → `ValueError` 且**无任何实例被写**（锁内全量检查先于全量写） |
| 构造失败（claim 后异常）| `release_owners(token)` 只回滚本 token 的 claim |
| cleanup 被取消（CancelledError）| shield 保护 `_cleanup_impl` 继续执行；`_sandbox` 等资源引用只在 impl 内 await 完成后清除（取消不丢引用 — :1390-1391 旧行为已修）；再次 `cleanup()` await 同一 `_cleanup_task` 至完成 |
| cleanup 正常完成 | impl 最后一步 `_closed=True` + `release_owners(token)`；释放后实例可被新 Harness claim |
| 并行复用需求 | 规格要求走 factory/clone，不是放宽 record |

### VM18: Runtime — 单源真相 + routing envelope 顺序稳定

| 场景 | 验收标准 |
|------|---------|
| 混合注册（dict + record 交错）| `config._processor_regs` 唯一真相（下标 = seq，混合顺序完整）；`_rt_procs` 是只读派生 property，两者不重复不冲突 |
| 同 hook 混合 serializable（dict）+ runtime（record）| 桶内排序：先 order，同 order 内按 `_after_` 稳定拓扑排序，seq 破平 |
| Builder 中 `A(order=0)` 后 `B(order=50)` 注册于同一 hook | 运行顺序 A 先 B 后 |
| 同 order 且 A `after=B` | A 在 B 后执行（拓扑约束，即使 A 注册序在前）|
| 手写配置（`_after_` 未编码进顺序）| 拓扑排序在路由层强制执行，不依赖注册序 |
| 外部注入（digester / tau2）| 走 `add_runtime_reg` / `replace_processor_regs` 写 API（replace 必须传**完整混合序列**）；`_rt_procs` 赋值**或 append** 抛 AttributeError（只读 tuple 视图 — list 视图的 append 只改临时列表、静默无效） |
| `_instantiate_runtime` 消费 | 只遍历 `_processor_regs` 一次；`_rt_procs` 中的同一批 record **不重复执行** |
| `copy()` 无 `processors=` override | canonical 原样浅拷贝，runtime 项与交错位置保留 |
| `copy(processors=...)` override | 从传入混合序列经 `normalize_processor_reg` 重建 canonical（接受 SerializedReg / dict / RuntimeReg / 裸实例；拒绝 None；**SerializedReg 不被 coerce 误包装**）；六个调用点（cli:367/489/1068、api:316、gateway:173/198）按 L2.3a 表迁移 — RuntimeReg 不丢失 |
| `canonicalize()` | 直接转换 canonical：SerializedReg 按 `repr(dict_ref)` 去重、RuntimeReg 原样保留 |
| `spawn_subagent` | 直接转换 `_processor_regs`，不再 dict+runtime 重组，**删除 `_rt_procs=[]`** |
| `SerializedReg` presence | **动态 property（dict_ref 实时读取）**：`__post_init__` 后修改 dict → runtime 与 graph 读同一新值（无缓存分叉）；`hook_present=True, hook=""` → **空桶（不执行）**（graph 0 边 + runtime `_hooks_=[]`，VM14 一致）；`hook_present=False` → `natural = coerce_runtime_reg(inst)` 全字段回退（order/sg/after 含实例级覆盖；无 hook 裸实例默认 `"*"`）|
| plugin 处理器 | 尾部 envelopes（seq = `max(seq)+1`，DROPPED 空洞不冲突；**id 身份去重保留**）与主序列**一次统一路由**，参与 order/after 排序并进入 owner claim 与 deployment 表示（L5.1b / L5.6） |
| `_route_processors` 输出 | 桶内元素全是裸 processor 实例（envelope 已解包） |

### VM19: 执行顺序编码 — EXECUTES_BEFORE 双层（第八轮第 36 项）

| 场景 | 验收标准 |
|------|---------|
| 同桶混合 S（持久, order=0）在前 vs R（runtime, order=0）在前 | deployment_hash **不同**（mixed 链反向）；genotype_hash 相同（持久桶仅 S 单处理器，无 persistent 链） |
| 同桶全持久 A,B vs B,A（注册序互换）| genotype_hash **不同**（persistent-relative 链反向） |
| 同桶跨 order（order=0 与 order=50）| 链按 (order, after 拓扑, seq)：低 order 在前 |
| 同 order + after 依赖 | after 目标进链在前（拓扑约束生效，即使注册序相反）|
| 冲突配置（after 指向更高 order / 同 order cycle）| `to_graph()` 抛 HarnessConflictError（与 builder:678 / 运行时路由同一异常）|
| star 桶（"*"）| 独立链；star-first（runloop.py:154-158）为执行语义，不参与跨桶编码（文档契约）|
| 实例插件 | 进 runtime 节点（L5.1b）+ mixed 链（seq=max+1）|
| dict 插件 | 不进 deployment 表示（纯读取不可枚举）；`to_graph()` warning；契约收窄文档（L5.1b / L5.6）|
| `apply_edits` 后 | order 边视为陈旧（快照无 canonical seq 无法重建）；hash 已清空（VM11）；重新 `to_graph()` 恢复有效链 |

### VM20: 第八轮补充验收

| # | 场景 | 验收标准 |
|---|------|---------|
| 20a | S-R-S-R 混合 canonical 依次经 `add_runtime_reg` / `replace_processor_regs` / `copy()`（无 override 与 override）/ `canonicalize()` / `spawn_subagent` | canonical 序列与 EXECUTES_BEFORE 链不变（每次变换后 to_graph 输出相同的 genotype/deployment hash 集合）|
| 20b | `_hook_`/`_order_`/`_singleton_group_`/`_after_` 四字段各做 missing vs 显式 falsey | 缺失 → `coerce_runtime_reg(inst)` 自然回退；显式 falsey → 原值保留（hook="" → 空桶 + runtime graph `_hooks_=[]`）；`__post_init__` 后修改 dict → runtime 与 graph 一致（动态 property）|
| 20c | serialized DROPPED 造成 seq 空洞 + 同 id 重复 plugin proc | plugin seq = `max(seq)+1` 无冲突；重复实例单 envelope（身份去重）|
| 20d | 每个 cleanup await 点（sub / plugin.stop / sandbox release）取消 | shield 保证 impl 跑完；资源引用在 impl 内清除（取消不丢引用）；重试 await 同一 task 完成 |
| 20e | 同桶 S,R ↔ R,S | deployment_hash 不同（VM19 首行）；genotype_hash 相同 |

---

## TODO integration addendum (from `experiments/docs/TODO/`, 2026-08-08)

This is a planning delta, not an implementation claim. The v5.2 Core↔Graph contract
remains the prerequisite for every item below.

### Accepted immediate items

1. **Lifecycle DFA gate (T0).** Add a deterministic builder validation pass for the
   canonical eight-hook lifecycle. Reject illegal `after` constraints and return a
   structured witness before candidate scoring or execution.
2. **Trace journal (T1).** Add bidirectional graph-node ↔ journal-record identity
   (`uuid`, source line/event, run id) in `tracing/journal.py`; keep it observational,
   never a second graph source of truth.
3. **Hash naming closure.** Rename the observed-runtime IR hash currently exposed as
   `phenotype_hash` to an unambiguous name (for example `ir_observed_hash`) if an
   execution-graph hash is introduced later. Keep v5.2 genotype/deployment/phenotype
   semantics and use migration aliases only temporarily.
4. **GraphEdit transaction hardening.** Before evolution, make `apply_edits()` validate
   endpoints, edge-kind schemas, relation-specific cycles, singleton/order/after
   invariants, and rollback atomically. INSERT/CHANGE/MUTATE operations must prove
   preconditions; no warning-only acceptance.
5. **Graph type vocabulary.** Add HarnessX-native relation types for processor, hook,
   slot, bundle, event-field, and runtime provenance. Borrow prompt/parameter/return/
   message/state as edge semantics only; keep actual interface typing explicit.
6. **Declaration provenance.** Keep manual, inferred, and observed metadata distinct;
   add source/confidence and require evidence for automatic backfill. WKD alone is not
   proof.
7. **Typed candidate gate.** Use
   `typed edits → apply_edits → fail-closed validator → build → to_graph roundtrip
   → hash checks → selective retest`. Rejected candidates cannot reach runtime or
   the success ledger.

### Conditional items (after the immediate gates)

8. **Declaration self-enumeration (T2).** Allow AST/file-line backfill only for
   high-confidence syntactic facts. Event-field reads/writes remain manual or
   observation-backed until a reliable analyzer exists; T2 depends on item 6.
9. **Harness-native typed operators.** Implement deterministic parameter mutation,
   processor insertion/removal, same-interface replacement, ordering rewiring,
   bundle swap, and matched-boundary crossover. Crossover is persistent-genotype-only;
   runtime overlays are recomputed, never mutated as genotype.
10. **History sampling and crossover.** Extend `VariantPool`/`SuccessLedger` with
    lineage, edit manifests, hashes, and uniform-plus-score parent sampling. LLM judge
    remains advisory; deterministic gates and measured evaluation stay authoritative.
11. **GS-D diagnostic chain.** Add the lower-cost path
    `Digester + CoverageFootprint → Planner root-cause intersection → ordered
    suspicious node/edge table → Evolver target_node_id` as a separate trace lane.
12. **Workflow-plan graph (optional).** If task-level agent workflow evolution is
    needed, introduce a separate `WorkflowPlanGraph` compiled to the workflow
    plugin/sub-harness executor. Do not overload `GraphSnapshot`, whose canonical
    meaning is harness configuration plus runtime overlay.

### Explicitly deferred or rejected

- Mermaid text is a visualization/import-export projection, never canonical truth.
- Mermaid CLI is syntax checking only, not a type or semantic checker.
- LLM graph→Python translation is not a trusted compiler; deterministic
  `graph_to_config → HarnessBuilder` remains the materialization path.
- Reachability/SCC/def-use, valid-time reconciliation, spawn slicing,
  archive/Pareto/island search, and other GS-D/GS-A/B/C items stay deferred until
  their measured consumer and failure policy are identified.

### Required order and acceptance evidence

`v5.2 VM1–VM20 → lifecycle DFA + hash rename → journal → GraphEdit transaction
validator → native type/declaration provenance → shadow-mode typed operators
→ history/crossover → optional WorkflowPlanGraph`.

Every accepted evolution candidate must record: static-gate result, roundtrip/build
result, genotype/deployment/phenotype hashes, edit lineage, runtime provenance,
selective-retest scope, and held-out outcome. The minimum go condition is zero
accepted invariant or roundtrip violations; efficiency or score gains are secondary
and must be measured against a current-pipeline control arm.
