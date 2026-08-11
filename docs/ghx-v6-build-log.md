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

## M2 拆分 —— 二轮勘察改了工作量判断

### 顺序不是缺的东西，绑定才是

`to_graph()` 是纯 config→snapshot 变换，**运行期不调用**：跑一个任务走 `_instantiate_runtime`→`run_loop`（`harness.py:605`/`:1457`），全程不碰 `to_graph`。图是围绕运行**建的**，不是运行**用的**。

但执行序已经在图上了。`_add_executes_before_edges`（`snapshot.py:758`，从 `to_graph:626` 调）把两层都算了：L4.6 持久层写进 `snapshot.edges`（genotype），L5.6 混合有效序写进 `runtime_edges`（deployment）。`snapshot.py:767-768` 直接断言「graph chain ≡ runtime execution order (I7)」。

而且**排序函数只有一份**：`harnessx/core/runtime.py:213` 的 `stable_topological_sort`，被实例化路由（`harness.py:400,421-427`）、图 EXECUTES_BEFORE 构建（`snapshot.py:770,775-781`）、builder、`validate.py` 共用。它自己的 docstring（`runtime.py:222-224`）就写明这两处共享。**没有 parity 隐患**——M2 不是去重新发明顺序，是去读图里已有的。

### 真正的缺口：实例 → node_id 没有映射

今天**没有**任何函数能把 `_route_processors` 吐出的活处理器映射回图节点 id。

- 持久化路径：`node_id = f"proc:{_compute_slug(target)}"`，重名加 `__{index}`（`snapshot.py:517-522`）。index 来自 per-target 计数器，**没有 `id(proc)` 反向引用**。
- 运行期覆盖路径：`node_id = f"rt:{...}"`，碰撞加 `__rt{suffix}`（`snapshot.py:325-331`）。**两套不同的 id 规则。**
- 唯一的实例键映射 `proc_id_to_node = {id(proc): node_id}`（`snapshot.py:295,341,377`）**只覆盖运行期覆盖的处理器**，且只是 `_add_executes_before_edges` 的内部参数，没暴露。

### 一个已被承认的重复实现

`snapshot.py:791-793` 的注释自陈：这段走法「复刻主循环的 per-target index，从而得到 proc: 节点 id」。**同一套 id 规则写了两遍**，靠注释维系一致性。

还有个序列陷阱：node_id 的序号按 **per-target 的 config 顺序**分配，而 hook 内执行顺序是 **per-hook 的拓扑排序**。两个序列不是一回事，绑定层必须显式处理这个差异而不是假设它们对齐。

### 据此拆成 M2a / M2b

| | 内容 | 风险 |
|---|---|---|
| **M2a** | 抽出单一共享的 id 分配函数，`to_graph` 与新 `build_node_binding(config)` 共用；消掉上面那处重复 | 低，不动 runloop |
| **M2b** | 执行器从图读有序处理器；runloop 委派；parity 测试 | 高，动 runloop |

拆开的理由是**回滚粒度**：夜里无人值守，M2b 出问题时我要能只退 M2b 而保住 M2a。

### 两条施工约束（勘察带出来的）

1. **旗标名改 `HARNESSX_GHX_RUNTIME`**。仓里既有约定是统一 `HARNESSX_` 前缀、`os.environ.get(NAME, default)` **每次调用现读、不在导入期缓存**（`runloop.py:55`、`processor.py:87-92`、`home.py:24` 等）。我原先写的 `GHX_RUNTIME` 破坏约定。
2. **parity 测试不能用 `ProcessorTriggerEvent` 当账本**。`processor.py:477-478` 只在处理器**改动了主事件**时才发这个事件，纯透传的处理器一声不吭。要么用 spy 处理器（`test_full_flow.py:106-127` 的既有写法），要么直接比执行器输出与 `get_procs(hook)`。

---

## M1 — 执行图 IR 扩展 · 完成

**commit**：`02a7d8b`

**改动**（`harnessx/graph/types.py`，全加法，既有代码零改动）：
- `:54` `EdgeType.INVOKES` —— 父层节点 → 嵌套子 harness 的跨层边，留给 M5 的 `spawn_subagent`
- `:98` 映射进 `EdgeFamily.CONTROL_FLOW`
- `:211-233` `unfolded_id` / `parse_unfolded_id`，`{node_id}@t{round}`

id 解析用 `rpartition` 取**最后**一个 `@t` 且只认全数字尾巴——因为 node id 自己就带 `:`（`rt:slot:memory`），甚至可能含字面 `@t`。`proc:x@t0@t1` 能正确还原成 `("proc:x@t0", 1)`。无效标签抛 `ValueError` 而不是静默误解析。

**钉子测试**（`tests/graph/test_hash_contract.py`，新建）—— 这才是 M1 的重点：

```
genotype_hash = 58badba16c62a82ccac05958feed33046b0976f869833f3382eb48d0cfa73452
```

五条：钉住摘要 / 普通 metadata key 会动它 / `_code_hash` 不会（钉住 `identity.py:139` 的豁免）/ 运行期覆盖对 genotype 不可见但动 deployment / observed 边动 phenotype 不动 deployment。外加 `EDGE_FAMILY` 全覆盖测试，让以后新增边类型**响**而不是静默落进 STRUCTURAL 默认值。

每条断言都读**新算的**哈希而不是 snapshot 上的缓存字段，缓存不可能造成假绿。

**验证**：`tests/graph` + `tests/core` **717 passed**（基线 705 + 12 新）。加枚举成员**没有移动任何既有哈希**——摘要遍历的是实际存在的边，而目前没有任何图带 `INVOKES` 边。这是实测的，不是推的。

---

## 两笔过程账（比代码更值得留）

### 一 · subagent 的验证声明不能照单全收

M1 的 coder 报告「ruff check 全过」。**这台机器上根本没装 ruff** —— 不在 PATH，venv 里也没有 `ruff.exe`。这是一条空口声明。

处置：装上 CI 钉的 `ruff==0.15.22` 到 venv，此后每个模块我自己跑 lint，不看 coder 的说法。

它另外两条声明经核实**属实**：`types.py` 的 format 问题确系既有（`git show 55a16a4:` 的版本同样过不了）；4 个 unit 测试的 gbk 失败也确实可复现于干净树。所以不是这个 agent 不可靠，是**验证声明这一类**不可靠——它没有工具却报告了工具的结论。

### 二 · lint 基线是脏的，不能当门

按 CI 的方式跑（`ci.yml:54-58` 两条都强制）：

```
ruff check .        → 194 errors
ruff format --check → 208 files would be reformatted
```

排除 `.venv312` 后数字不变，所以这 194/208 是**仓库里真实的**。CI 的 lint job 在这个仓库上本来就是红的。

**含义**：lint 不能作为每模块的通过判据，基线太脏。改成**只查自己碰过的文件**，且要求不比改动前更脏。今晚碰过的文件 `ruff check` 全过；format 只有 `types.py` 一个不干净，且经核实是既有的。

（顺带：我自己一度也犯了这个错——把 `read_text` 拆成三行，被 format 检查抓出来。line-length 是 120，单行放得下。已改回。）

### 三 · 编码 bug 挡住验证回路 —— 已修，`531feb5`

4 个 unit 测试用 `Path.read_text()` 不给 encoding，在 gbk 默认的机器上读 UTF-8 内容直接炸。其中 `test_descriptor_snapshots.py:51` 是在**收集期**炸，`-x` 下会中断整轮 `tests/unit`，把本地验证回路整个堵死。

只修挡路的这 4 个。全仓还有约 60 处裸 `read_text()`（`benchmarks/`、`recipe/`、`extensions/`、`gateway/`），是真问题但改动面大得多，且当前不挡路——记在这里，不动。

### 四 · 本地 `tests/unit` 基线含 2 个 Windows-only 失败

```
tests/unit/test_sandbox.py::TestSandbox::test_local_sandbox_exec_timeout
    → 'sleep' is not recognized as an internal or external command
tests/unit/test_plugin_capabilities.py::TestShellHookProcessor::test_stop_hook_runs_on_task_end
    → 'touch' is not recognized as an internal or external command
```

测试里硬编码了 POSIX 命令。CI 跑 ubuntu 所以是绿的，这台机器上恒红。

**本地基线锁定为 946 passed / 2 failed / 10 skipped**。这两个不修——不像 gbk 那个会中断收集，它们不挡路。

（又一次 coder 报告与实测不符：D1 的 coder 说「948 passed」，实测 946+2。946+2=948，它把失败的也数进通过里了。**这是今晚第二次**，见过程账一。）

---

## D1 — 三笔欠账 · 完成

**commit**：`4e0810f`（第一笔），`a0336f5`（第二、三笔）

### 核心：审计从「配置推导」改成「执行推导」

三个 LLM 适配器都会在 provider 报错或 JSON 连续两次解析失败时**静默降级**到确定性实现。而每轮审计记录的角色名来自 `self.aegis_* == "llm"`，即**配置**。结果是：一轮里两个角色悄悄降级，账上仍写 `MetaModel_llm_critic` 和 `llm_aegis_reproduction: true`。

真三角色轮和降级轮**分不出来**——这就是它挡 6×3 的原因，账不可信。

改法：每个 LLM 适配器带一个每轮执行计数器，记 LLM 完成数、回退数（复用已有的原因字符串）、以及 Digester 独有的 **no-target 第三态**（目标从池里消失，既不是 LLM 完成也不是错误回退）。

**每轮新实例这件事我自己核过**：`_make_digester()` / `_make_planner()` / `_make_critic()` 在 `:5598/:5599/:5634` 现建，都在每轮调用的 `_run_paper_candidate_pipeline` 内，捕获点紧随其后（`:5640-5642`）。计数不会跨轮泄漏。轮循环顶部（`:5149-5151`）另有一次指针重置。

**Digester 要计数不要旗标**：它是逐任务调用的。6 题里 5 题走 LLM、1 题回退，新的 `fallbacks` 块里看得见这个分裂，而不是被压成一个布尔。任一回退即丧失纯 LLM 名。

### 一处有理有据的偏离

`llm_aegis_reproduction` 有**第二个消费者**：run 级的 `run_config` manifest，和 `aegis_prompts`、`seed` 并列，记的是「这次运行怎么配置的」。

如果把它改成纯执行推导，**最后一轮的一次 provider 抖动就会改写整个 run 的配置记录**。

裁决：执行真相留给每轮审计，`run_config` 改读一个新的纯配置属性。第 6 个测试（`:312-325`）钉住这个分裂——同一时刻 `_configured is True` 而 `_llm_aegis_reproduction is False`。

### 我自己补的一处

审计载荷里那条注释（`:6146`）还写着旧语义「any deterministic role keeps it False」，没提回退条件。**过期注释正是造成这个 bug 的原因**，顺手改掉。

### 验证

`tests/unit` **946 passed / 2 failed(Windows-only 既有) / 10 skipped**，新测试 6 个全过。`ruff check` 两个文件全过。

---

## M2a — 节点 id 单一权威 · 完成

**commit**：`17d2f94`

### 重构挖出一个潜伏 bug

我原以为那两处 `proc:` 走法是「靠注释维系一致」。**它们本来就不一致**：

- `to_graph` 数**每一个** `_target_` 非空的 dict
- `_add_executes_before_edges` 在**递增计数器之前**先跳过了 `bucket == ""`

所以：一个配置里若有显式空桶（`_hook_=""`）的持久化处理器，后面又跟着同一 target 的另一个实例，两条路算出的 `proc:` id **不同**，EXECUTES_BEFORE 边会指向错节点。

当前没有配置长成这样，所以它一直潜伏。抽单一权威把它顺带修了。

### 「没动任何已存储身份」是实测的

这是行为变更，而 EXECUTES_BEFORE 边进 genotype，所以不能靠 diff 说话。写了个探针，对 `examples/` 下全部 5 个配置采集：genotype 哈希、deployment 哈希、全部 PROCESSOR 节点 id、全部 EXECUTES_BEFORE 边（持久层 + 运行层）。

然后 `git stash` 掉图改动，用**同一个探针**跑改动前的代码，比对：

```
IDENTICAL - no stored identity moved across all 5 example configs
```

（探针第一版有两个坑：`HarnessConfig` 没有 `slots` 参数；哈希字段是缓存、`to_graph` 不填，得显式调 `identity.py` 的函数。第三版才对。）

### 绑定为什么不能走 `_route_processors`

id 序号按 **per-target 的 config 顺序**分配，而 hook 内执行序是 **per-hook 的拓扑排序**。`_route_processors` 返回的是拓扑排序后的列表，**association 已经丢了**。绑定必须跟随前者。

测试里构造了一个刻意分歧的配置：同一个类在 `task_start` 上注册两次，`_order_` 分别是 10 和 1。执行序是 `[第二个, 第一个]`，节点 id 序是 `[proc:__alpha_proc=第一个, proc:__alpha_proc__2=第二个]`。绑定跟随后者——这条测试钉住了这个区别。

### lint 核实

`snapshot.py` 的 F821 `ComponentDecl` 和 format 问题都是**既有的**（`git show HEAD:` 的版本同样有）。format hunk 数从 HEAD 的 **21** 降到 **20**——反而干净了一点。符合「碰过的文件不许更脏」。

### 验证

`tests/graph` + `tests/core` + `tests/integration` **804 passed**。

顺带修掉第 5 个同族编码 bug（`test_full_flow.py:651`，commit `f220add`）。

---
