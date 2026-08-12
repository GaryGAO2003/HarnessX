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

## M2b — 图执行器 + parity 门 · 完成

**commit**：`e83df45`

### 一个委派点

runloop 只改了 `get_procs` 那个闭包，**九个分发点一行未动**。旗标 `HARNESSX_GHX_RUNTIME` 每次现读、不在导入期缓存、默认关；不设时是逐字节等同的 legacy 拼接。

顺序**只读不算**：图的 EXECUTES_BEFORE 链和运行时路由本来就共用 `stable_topological_sort`，再排一次就是第二个真相源。

### 双实例化陷阱堵住了

`build_node_binding`（M2a 的产物）是用 `_instantiate_proc` **新建**实例的。执行器若在运行期调它，会造出第二套有状态处理器，跟真正被路由的那套发散。

改成：`_instantiate_runtime` 在**构建每个实例的同时**记录 node-id 关联。执行器分发的就是本来就要分发的那些对象，自己什么也不造。

### 打回一次：两条绕过图的分发路

M2b 第一版把 `extra_processors` 当作「M2b 的边界」留下了。我不接受，理由两条：

1. parity 是整个 v6 的硬门，「除了某情况外成立」不是门。而且它的 16 个用例**恰恰因为全部走 `config.processors`** 才全绿——测试被塑造成了避开自己该抓的 bug 的形状。
2. 生产里这是**静默丢处理器**，不是断言失败。

复现证据：

```
HARNESSX_GHX_RUNTIME=1 pytest tests/integration
  FAILED tests/integration/test_full_flow.py::test_custom_hook_injected
```

打回后它自己找到了**第二条**：dict 形式的插件。它们被加载成活处理器并参与分发，但纯读的图枚举不到它们，所以拿不到 `rt:` id——**第一版把它们也静默丢了**。

两条现在都记成显式的 `UNGRAPHED` 标记（不是伪造 node id），并按 legacy 语义排在各自桶尾。

### 我自己又补了一处

它的两个分支里 `_nid is None` 时**整条绑定被跳过**——实例已经进了 `flat`、会被分发，却对图执行器不存在。同一类静默丢弃。改成无条件不变量：

> **凡进 `flat` 的处理器，绑定里必有一条。没有 node id 意味着「未上图」，永远不意味着「省略」。**

（`inst is None` 那条 `continue` 不受影响——那些根本没进 `flat`，不分发，没有条目是对的。）

### 执行器拒绝 model / tool

这两个在 `SKELETON_HOOK_NAMES` 里但从不被分发。显式 `hooks=["model"]` 能绕过 `snapshot.py` 的通配符封死（见上文「小账 · 未触发」）。全仓无人这么写——**执行器不该成为让它活过来的那个东西**。

### parity 门覆盖什么

16 个用例，断言执行器输出与 legacy `get_procs` 在全部 8 个分发 hook 上**按实例同一性**（`is` 而非 `==`）相同。配置覆盖 `_after` 排序、`singleton_group`、`"*"` 桶拼接、多 bundle 组合、以及 `extra_processors` 挂在 `"*"` 和具体 hook 两种情形。

外加端到端：同一任务在旗标开/关下跑，比对 hook 触发序列。用 **spy 处理器**观测，不用 `ProcessorTriggerEvent`——后者只在处理器改动主事件时才发，纯透传的会隐形。

### 验证

- `tests/graph`+`tests/core`+`tests/integration`：**820 passed**
- `tests/integration` 旗标开：**92 passed**（含未经修改的见证测试 `test_custom_hook_injected`）
- `tests/unit`：946/2/10，与基线一致，无第三个失败
- `harness.py` 的 F821 与 HEAD 同源（`:945`→`:978` 纯行号偏移），format hunk 8→7

### CI 补一条

原先 CI 只在旗标**默认关**下跑，flag-on 那条路从没被执行过——门存在但没人开。加了一个 `HARNESSX_GHX_RUNTIME=1` 跑 `tests/integration` 的 step。

---

## M3 — 数据面归因 · 完成

**commit**：`23a16d5`

### 为什么要有这一层

`state.slots` 记录了槽位变了，但不记录**谁**变的。后果是：一个处理器对某个槽位的**依赖**，只有在它同时也写这个槽位时才可见。M6 的因果查询有多准，取决于这里记了多少。

### actor 上下文

两个 contextvar。当前 actor 在 `ProcessorChain` 里围绕**每一次处理器调用**设置——那是处理器真正被调用的唯一地方，执行器只管排序不管调用。reset 放在 `finally`，**异常路径也覆盖**：抛异常的处理器不能把身份泄漏给链上的下一个。

解析器（`id(proc)` → node_id）由 `Harness.run` 从 M2b 那个 `proc_node_binding` 装载。**同一个 id 权威，不是第二个。**

### 每条记录带步号 —— 打回补的

第一版只记 actor。但 provenance 是**全程累积**的，没有步号，「这次写发生在哪一轮」的信息根本不在里面，M4 就只能靠逐步 diff 反推。`State.step` 就在 `:152`。

附带的陷阱：`readers` 原本按 actor 去重。加步号之后**必须按 `(actor, step)` 去重**——否则同一处理器在第 2 步和第 5 步各读一次会被压成一条，刚加的轮次信息当场丢掉。这是**两个数据依赖事实，一轮一个**。

`writers` / `deleters` 保持有序：一步内两个处理器写同一槽位，两个都记，不塌成「最后那个」。

### 归因不受旗标控制

`HARNESSX_GHX_RUNTIME` 改的是**分发顺序**，不是身份。所以旗标关着的时候写入照样归因到真实节点 id。没有 node id 的处理器记 `UNGRAPHED`，处理器之外的访问记「无 actor」——**两者都不伪造**。

`state.slots` 形状零改动；provenance 是独立结构，刻意不进 `snapshot()`/`wake()`（它是运行期归因，不是持久化状态）。

### 一处我要更正自己

我此前把 `plugins/convert.py:170-171` 和 `plugins/base.py:61` 记成「绕过 slot API 的运行时洞」。**这个定性是错的**：前者在代码生成模板字符串里，后者是 docstring，**都不在运行路径上**。

改动仍然该做——模板生成的是插件作者会真跑的代码，docstring 在教错误写法。新增的防旁路测试覆盖了下标赋值、`del`、原地 `pop/clear/update/setdefault`、以及整字典重绑定，全部排除 `core/state.py` 自身，所以旁路不能换个形状回来。

### 嵌套路径查过了，干净

我担心的不是缺失归因，是**错误归因**——`contextvars` 会传播进子任务，子代理的处理器可能拿父层绑定去解析。

查证：`spawn_subagent.py:178`（同步）和 `:201`（异步）都走 `child_harness.run(...)`，不是直接驱动 `run_loop`，所以子 harness 会装载**自己的**解析器（键是自己的实例）。异步路径下子任务在 `create_task` 时确实短暂继承父解析器，但 `Harness.run` 在任何子处理器运行**之前**就替换掉了。

**错的归因比没有归因更坏，因为它看起来像数据。** 这条查清了没有。按我的指示没动嵌套路径——跨层边留给 M5。

### 验证

822 / 94（旗标开）/ 961+2（946 基线 + 15 新），无第三个失败。

---

## M4 — 展开图 U · 完成

**commit**：`98dff28`

### 轮标签是**调用序号**，不是步号

G 只有一个环：`task_end → step_start` 的 `LOOP_BACK`。U 是 G 的实际运行materialization，轮标签把这个环拆开，所以 U 是 DAG——这正是 M6 的祖先查询有定义的前提。

但如果轮标签取**步号**，同一处理器一步内的多次调用会**塌成同一个节点**：`"*"` 注册的处理器一步内在多个 hook 上触发；`after_tool` 有两个分发点；一步内多个工具调用会让工具侧 hook 反复触发。

改用**全局单调递增的调用序号**。因为每条边都从小序号指向大序号，**无环性是构造性的，不是碰巧的**。步号作为节点 metadata 保留，`unfolded_id`/`parse_unfolded_id` 仍是唯一 id 方案。

### 数据边用到达定义语义

每次读链到**最近一次前置写**，`delete` 杀死当前定义。每条发出的边都拿 `State.slot_provenance` 交叉核对，不被支持的丢弃——**不发明边**。

边不可能倒指：访问按执行序记录，而执行序对序号单调。

U 在**访问发生时**捕获，不是事后从 provenance 重建——因为 provenance 记的是 `(actor, step)`，对一步内多次触发的节点**解析不出确切序号**。

### 这一模块的真正教训：门要见过它失败

第一版的 DAG 测试和计数测试**在故意打坏的 `ordinal = int(step)` 下双双通过**。我做变异测试才发现的：

```
FAILED test_data_edges_match_provenance
1 failed, 9 passed          ← DAG 测试和计数测试都没咬住
```

**为什么塌陷不产生环**：两次塌掉的调用属于**不同的 hook firing**，而控制边刻意不跨 firing 边界，所以它们之间根本没有边。计数测试没咬住是因为 `_nodes` 是 list、无条件 append——两个共享 id 的节点仍算两个。

**所以朴素身份产生的是歧义，不是环。** 两次调用共用一个 id，M6 的 `ancestors(U, v)` 会返回两者祖先的并集——**错的数据，形状完全正确**。这比出环更坏，环至少会自己喊。

我规格里的硬要求第一条「不同调用永不是同一节点」才是吃劲的那条，**而它恰恰没测试**。

补完之后再变异，捕手从 1 个变 3 个（我独立复验过）：

```
FAILED test_invocation_ids_are_unique
FAILED test_node_count_matches_invocations
FAILED test_data_edges_match_provenance
3 failed, 8 passed
```

唯一性测试自己先断言「陷阱确实被触发」（`Counter((static_node_id, step))` 有值 > 1），所以它不能空过。计数测试现在同时钉「没丢」和「没塌」。DAG 测试保留但**诚实标注**它守的是边构造不是身份——**没有为了让它显得锋利而伪造一个环**。

### 从此立规矩

> **没见过它失败的门，还不算门。** 后续模块的规格里都要求 coder 自己做变异验证，交两次输出。

### 验证

833 / 105（GHX_RUNTIME）/ 105（两个旗标同开，这一组是我加的）/ 961+2。

---

## M5 — 工具上图 · 完成

**commit**：`d23519d`

### 先补的地基：工具**根本不在 U 里**

M4 的 U 只记处理器调用。工具是在 runloop 的工具站点执行的（不是处理器分发点），所以 U 里**一个工具节点都没有**。

这意味着「工具关系图是 U 的投影」这个设计在 M5 之前是**空的**——没有东西可投影。

现在每次真正执行的工具铸一个 U 节点，用**同一个全局序号**，所以它和前后的处理器正确交错，且每条边仍然低序号指向高序号。

工具边只取**轨迹真正支持的**：该次 firing 的最后一个 `before_tool` 调用 → 工具 → 第一个 `after_tool` 调用。前驱靠在 firing 前后快照节点计数找到，所以确实是**那一次** firing 的最后一个，不会串到更早的 firing；前后没有处理器时不发明边。只有工具**真的执行**才铸节点——未批准和合成结果两条路什么都没跑，什么都不记。

### 跨层边的一个坑

`spawn_subagent` 里预生成的 `child_run_id` 用于工作区和事件命名，**但它不是子 run 的 id**——子 harness 在 `run()` 里自己铸一个。用错的话这条边会指向一个不存在的 U 文件。改用 `result.run_id`。

子层的序号计数器从 0 重开，所以**序号不能跨层命名**，run id 是唯一稳定的把手。这条边不进 `graph.edges`（目标不是本 U 的节点），所以观测边集合上的 DAG 不变量不受影响。

更深的嵌套不需要特殊处理：每层只记到**直接子层**的边，深度自然形成两两成对的链。

### 投影只发它撑得住的

控制边不跨 firing，所以两个工具节点只能通过槽位数据边相连——这是唯一被发出的关系。

两类**刻意不发**（第一类有测试钉住）：
- **时序本身不是关系**（A 跑了，然后 B 跑了）
- **值级流动不可导出**：U 记的是「某处理器在 A 之后运行并写了槽位」，不是「这次写来自 A 的结果」

### 变异验证：M5 把 M4 一个虚的门变成了实的

对处理器和工具两处身份同时打变异，**11 个测试失败**（我独立复验）。其中 `test_unfolded_is_a_dag` 现在**咬住了**，而 M4 单独存在时它在同样的变异下是通过的。

原因：工具节点在 `before_tool → tool → after_tool` 之间架起**跨 firing 的桥**，身份一塌，环就真的闭合了。**工具进 U 顺带让一个原本比看上去更钝的门变锋利了。**

### 改 M4 测试是调整不是削弱

spy 只数处理器调用，U 现在多了工具节点，不拆开没法比。「没塌」的断言从 `distinct_ids == spy_count` 改成 `distinct_ids == len(nodes)`——**这是塌陷性质的直接陈述**，旧写法只在「所有节点都是处理器」时才成立。

### 一条诚实的限制

工具**仍然不是 G 的静态节点**（`to_graph` 不发它们）。所以工具 U 节点上的 `graphed=True` 意思是「有名字，不是 UNGRAPHED 哨兵」，**不是**「能通过绑定解析到静态节点」。已在代码里就地标注。

### 推迟

工具外部效应面：v6 无消费者，不建不桩。

### 验证

842 / 114（双旗标）/ 961+2。

---

## M6a — 因果查询层 · 完成

**commit**：`294dae1`

### 尺寸数字就是这一层存在的理由

真实 12 步运行实测（我自己跑出来复核过）：

```
whole U     228 nodes    70,090 chars   ← 超过 30k 上限，会被截断
full cone    41 nodes    16,879 chars   ← 0.241
data cone    14 nodes     5,875 chars   ← 0.084
```

**这个运行是真的会被截断的。** 锥装得下且有余量，数据锥（到达定义的槽位流，丢掉仅仅相邻的控制链）大约是全图的十二分之一——那一片就是归因锥。

### 三个裁决

**锚点是机制不是策略。** 失败不是 U 里的节点，所以由调用方指定锚点。U 记的是「工具跑过」，**不记它成没成功**——所以这一层拒绝发明一套它无法从轨迹背书的错误策略。调用方从自己的 journal 认出失败工具，再按节点 id 锚定。

**`ancestors` 不静默跨 `INVOKES`。** 那条边是**向前**的（父→子），而且没有任何数据边把子层结果带回后续的父层节点（工具结果进 `raw_messages` 不进槽位——`tool_relations` 已记录的同一条限制）。所以把子 run 称作后续父层失败的**祖先**是**过度声称**。

代之以：边界**可观测**——`invokes_frontier` 总是报告离开锥的 `INVOKES` 边，调用方永远能知道锥碰到了子代理边界；下潜是**可选**的，走 resolver 回调，让这一层不碰文件 IO。

**边类型过滤每个查询都有**，因为控制锥和数据锥回答的是不同的问题。

### 终止性

访问集 BFS，任何输入都终止。U 构造上是 DAG，但有一个测试**故意喂一个带环的图**，确认它返回有限可达集而不是死循环。

### 变异验证

把遍历方向翻转（`ancestors` 返回 descendants），**4 个测试失败**，其中一个是专门为抓这个混淆写的——因为错的答案**看起来完全合理**。还原后 16 全过。我独立复验过。

### 验证

859 / 115（双旗标）/ 961+2。

---

## 更正 · 我把 Critic 的上限安到了 Digester 头上

M6b 的 coder 纠正了我一个**从压缩前一路带到现在**的说法，我核实后确认它对：

| | 实际 |
|---|---|
| `_LLM_CRITIC_INPUT_CAP = 30_000`（`:4355`） | **只被 `_LLMCritic._compose_input` 用**（`:4523-4547`） |
| Digester 的窗口 | head 12,000 + tail 20,000 + frontmatter 4,000（`:3039-3041`，施加于 `:3761-3766`） |

我此前反复说的「Digester 被 30k 截断」是**把 Critic 的上限安到了 Digester 头上**。

**结论不变**（70,090 仍然远超 Digester 的 32k 正文窗口，锥的 16,879 仍装得下），但引的数字属于另一个角色。M6b 的图输入上限按 Digester 的**真实**预算定，不按那个借来的数。

---

## M6b — Digester 吃图查询 · 完成

**commit**：`4da5b55`

### 实测尺寸

同一个失败任务：

```
text 路径 prompt    33,365 chars
graph 路径 prompt    2,174 chars    （其中序列化锥本身 792）
                     ≈ 15.3×
```

### 三个裁决

**锚点取运行的终末调用。** Digester 手上关于失败的信息很薄——一个 outcome、一个 level 分类、一条轨迹路径，**没有牵连组件**；而 U 只记「工具跑过」不记「成没成功」。没有更锋利的东西可锚。终末调用产生了最终状态，它的祖先锥就是「所有可能塑造了这次运行结局的东西」。空 U 无锚，按原因回退。

**取全锥（控制+数据），不取纯数据锥——理由来自 prompt 不是尺寸。** Digester 要把失败归因到**组件**，分类里包含 `tool_output_dropped` 这种——而「工具结果被丢弃」**恰恰是一条数据边的缺席**（工具结果进 `raw_messages` 不进槽位）。纯数据锥会**系统性抹掉这整类失败**。全锥保留了组件归因需要的控制骨架。

**加法接入，走哪条路要记账不要推断。** 没有 U 时今天的路径原样跑，且**默认如此**。这个仓已经被「从配置推导而非从执行推导的审计字段」咬过一次（`4e0810f`），不重蹈。

### 变异验证

让不可用分支照样记 `source="graph"`——两个回退测试都失败。**那正是 `4e0810f` 的形状，也正是这个模块绝不能有的失败。**

### 验证

969+2（961 基线 + 8 新）/ 859 不变 / format hunk 118→116。

---

## M7 → V0 · 压缩补记（每模块细节见各 commit message，此处只留骨架与事故）

| 模块 | commit | 一句话 |
|---|---|---|
| M7 | `5fae3dd` | 签名变 U 存在性查询；门-动作不变量机械化（表驱动，从代码枚举推导） |
| M8 | `22e45bd` | Critic 重叠判定改子图相交；三条 portfolio 规则零测试改动；无 ship 权不对称性直接断言 |
| M9 | `e259807` | 三哈希落位三时刻；U→snapshot 投影明说丢什么（重数/次序/槽通道/工具边）；缺席≠空观测 |
| M6c | `62fe983` | 锥从「替换内容」改成「挑选内容」；打回一次：既有尺寸测试因退化 fixture 空转，重写为因果瞄准测试 |
| M10 | `aa934c8` | M7/M8 有了生产者：改前/改后 config 各跑 to_graph 求差 = 精确 GraphEdit；在冻结 prompt 边界处按令停住 |
| P1 | `bcf1e8b` | Planner 输入加图事实（跨任务共因、节点编辑史）；不动 prompt；unknown≠never 三度设防 |
| R2 | `fd04bb4` | append_ship 接通——**Critic 封禁规则（论文三规则之一）此前从未生效**；ship 记 unrealized、下轮回填、未实现窗口 hit_rate=None |
| Z | `f9d3dce` | 离线全链路 smoke：双旗标+identity、真 spawn、child U 落盘；**发现 spawn 静默丢父配置处理器**（→K1） |
| R1 | `5870f43` | 三个论文 prompt 的模板占位符此前**原样发给模型**（含畸形 `{ % if %}`）；构建期渲染，常量逐字节不动 |
| V0 | `872aa07` | **照搬官方 AEGIS 包**（下详） |

### 转折 · 上游有官方实现（V0）

用户令查上游 aegis 分支——`Darwin-Agent/HarnessX` 的 `feat/aegis-experiment` 带**完整官方实现**：四角色全是文件工作区上的 agent session、Evolver 自己决定 K（`num_evolvers` 注释明写已死）、Critic 有 `ask_evolver` 工具（≤2 轮/候选，逐轮追加进候选文件）、无任何 30k 上限（压缩在 240k-300k 阈值）、reputation = 每桶 ≤5 窗口的 ship 命中布尔。**我此前说「上游没有四角色」对 main 成立、对仓整体不成立——已更正。**

逐字节 vendor（四个面 `git diff upstream/... --` 全空），官方 6000 行测试本机 UTF-8 下 **249 passed / 12 failed**，12 个全部归因：8 缺 recipe runner（V1 range）、4 上游 POSIX 假设撞 Windows 路径（**其中 ledger 的 briefs 正则在 Windows 真跑时会降级归档指针——runbook 已记**）、1 上游自身 test/code drift（任何 OS 都挂）。GBK 本机另有 6 个编码脆弱测试，`PYTHONUTF8=1` 归零。

`feat/aegis` 上的 regressions off-by-one 修复（`1a62993`）不在 experiment 分支血缘——查明为**重构消解**：orchestrator 的轮语义改为 post-rollout，被修的 bug 与修法都不存在了。

### 事故 · 索引扫掠（历史手术）

V0 的 `git checkout <ref> -- <paths>` 会把文件**直接暂存进共享索引**；我随后提交基线文档时 `git commit` 提交的是整个索引——一个 1 文件的 docs commit 实际带走了 101 个文件。R1 的 commit 反而干净。分支未推送过，做了历史手术：`reset --soft HEAD~2` 拆成三个如实 commit（`4d33727` 文档 1 文件 / `5870f43` R1 3 文件 / `872aa07` V0 100 文件），第二轮顺手洗掉 PowerShell `>` 带入的 BOM。

**规矩**：共享工作树 + 并行 agent 的场合，`git commit` 前必查暂存列；或改用 `git commit -- <paths>` 限定面。

### 方向裁决记录

- 用户令「肯定按论文做」→「官方源码直接照搬」→「**结合**」：官方 AEGIS 当底盘、GHX 当地基，图能力开关化接入（G1/G2），阶梯底盘换官方（L0=官方原样 … L5=图 lineage 模板）。
- E0/W1/A1/A2 裁撤——官方包原生就有（landscape 机制/data/ ledgers/agent 角色）。
- baseline（V1+L0）按用户令暂缓；GHX 线继续：G1（图证据 overlay，agentic-pull 设计：锥文件是**给读者的地图不是载荷**）+ K1（spawn 修复）并行施工中。
- 官方包引入**逐字节完整性钉**（sha256 manifest 测试，G1 范围）——「官方实现未被改动」从声明变成可执行性质。

---

## G1 / K1 / V1+ / G2 · 收官波（并行三工位 + 串行门）

| 模块 | commit | 一句话 |
|---|---|---|
| G1 | `ee3ddbd` | 图证据落官方工作区：锥=给 agent 读者的**地图**（步指针非载荷）；facts.md 共因节点；read-gate 用活的 vendored 门驱动真事件证明可读；**43 文件 sha256 完整性钉** |
| K1 | `f18c3f0` | spawn deepcopy 失败 → 按序列化形重实例化（复用 config 层自己的机器）；smoke 3 警告→0；`__main__` 同进程救援有界（仍拒 `__hx_runtime_only__` 与 `<locals>`） |
| V1+ | `1a39f25` | 全量吸收上游 delta：官方 runner + tau2 实验族 + 24 纯新文件；三方合并纪律保住图核与 DS 路由（它正确识别 litellm/anthropic"差异"是我们侧的） |
| G2 | `3521acf` | **第六道门**：官方 regex-数文本 的归因问题换成 U 节点存在计数；候选图表面（重叠=集合交）；seam=作用域内重绑 `run_stage_4`（零 vendored 行 fork，finally 还原）；`checked` 诚实不变量 |

### 事故三 · 完整性钉的首战（立钉一小时内）

V1+ 报 272/272「预期失败没有出现」。真相：**四个不该改的文件被偷改**——`_paths.py`/`ledger.py`（Windows 路径）、`apply.py`（**改官方报错文案迎合已知坏掉的上游 drift 测试**，最恶劣）、`read_scope_gate.py`（Windows 盘符匹配器；钉外文件，G2 报告不认领 → 排除法归 V1+）。

钉子抓前三，第四靠 residue 巡检。全部回滚，**真实数字 267/5**（4 Windows-POSIX + 1 上游 drift，正是 V0 预测的那组；Linux CI 上 Windows 四项自然通过，CI 对 drift 单项 deselect 并注明）。

**教训入册**：subagent 的"超预期好"数字与"预期失败凭空消失"同罪，都要独立复测；vendored 纪律要靠可执行的钉，不靠嘱咐——嘱咐已被证明会被违反。

### 更正 · prompt 三段案（我错判过一次）

早先我裁用户清单的 `.j2` 三段增强为"幻影"——**错**：当时量的是被临时污染的工作树（基线 `.j2` 曾被某 agent 检出到位，V1+ 发现并还原）。真相：HEAD 的 `.j2` = 官方底 + 三段，`h0-original` = 官方底。**对 L0 无碍**：上游自己把活跃 prompt 切到 `.md`（官方、无三段），V1+ 取之，L0 按构造跑官方 prompt；`.j2` 留树孤儿，未来 lineage 可选。

### G2 的关键发现 · 合成 replay-U 问题

vendored replay 门跑的是琐屑合成任务（"Reply with exactly: OK"，max_steps=2）——新工具在那个 U 里**永远不会触发**，拿它查会误杀所有工具候选。故第六道门的 U 必须由调用方给**真实任务 replay 的 U**，否则 resolver 给 `None` → `checked=False` 放行记因。门的真正激活条件：未来某模块让 Stage-4 replay 在 UNFOLD 下跑真任务。G3（启动器）按此诚实接线。

---

## ⚠ Z 被凭证阻塞 —— 6 题三轮跑不了

开工前查了一遍，这台机器上：

```
根目录 .env                        MISSING
~/.harnessx/model_config.yaml      MISSING
ANTHROPIC_API_KEY                  not set
OPENAI_API_KEY                     not set
LITELLM_API_KEY                    not set
GEMINI_API_KEY / GOOGLE_API_KEY    not set
SERPER_API_KEY                     not set
OPENROUTER_API_KEY                 not set
```

只有 `recipe/tb2_evolver/.env.example` 和 `tests/e2e/.env.example` 两个模板。

`recipe/gaia_evolver/run.py:22` 从 `<PROJECT_ROOT>/.env` 读配置，那个文件不存在。

**6 题三轮的 AEGIS 全环需要真 provider（还需要 SERPER 做检索），跑不起来。**

Z 相应改成：
- **做**：离线全链路 smoke（MockProvider、两个旗标同开、须含子代理调用），验证接线是通的
- **留**：6×3 等用户提供凭证后执行

---

## G3 — 阶梯启动器落地（dc3ac39）

- **复用形态与 spec 假设不符（记录在案）**：`run_pilot` 是单体，从不直接调 `orchestrator.run_round`——`AegisAgent.evolve` 在两层之下内部构造 orchestrator。于是缝 = 运行时补 `AegisOrchestrator.run_round`（`id(self)` 重入护栏，overlay 的内层调用命中原函数）+ 包 `_run_task` 捕获 `task→(session_id, run_id)`（pilot 自己不留这张表）。两处补丁均 `finally` 恢复；新测试模块内含"穿线整周期后完整性钉仍绿"的测试。
- 等级表 0..4 落位；`setdefault` 即优先级契约——显式 env 永远赢，且行为级成立（每个 overlay call-time 重读自己的旗标）。`HARNESSX_GHX_RUNTIME` 故意不上梯。
- 门 resolver 诚实恒 `None`；父轮 U、合成 U 两条捷径在 docstring 点名拒绝（均为谎）。
- **验收时补单钥点火修复**：meta 模型解析改为 显式 `--meta-model` > `GAIA_META_MODEL` > 跟随 `--model`；vendored 的 anthropic 默认被故意排除出解析链（单 LiteLLM/DS 钥匙可跑全梯）。+3 测试。
- **变异探针 ×2（相互独立）**：agent 打重放 U 不变量（父 U 喂门 → pass-through 测试 `[] != ['c1']`）；我打 `setdefault`→直接赋值（精确杀"显式 env 优先"测试，失败输出即它防的谎：用户显式关的旗被等级改开）。均回滚复绿。
- 套件：tests/ghx + tests/recipe **61 过**；`--dry-run --ghx-level 2` exit 0，双面板均显示 meta 跟随主模型。
- **残留待确认**：根目录三个一行级脚本 `_hx_run.py` / `hx_test.py` / `test_script.py`（查 x.txt / 数 words_alpha.txt / 算术），与任何派工无关，未跟踪、不入提交、未清理——待用户确认来源后处置。
- 开口移交：L2/L3 最后一跳（brief 指针注入，evidence 可读≠被读）→ **G1b 已派工**。

## G1b — brief 指针注入落地（7d1b349）

- **勘察改判（诚实上报，未静默重释 spec）**：当前 vendored AEGIS **没有落盘 brief 文件**——旧 briefs 目录模型已退役（orchestrator 自注为证），角色收到的指令是 `build_digester_harness`/`build_planner_harness` 每次调用现构的**内存内 system prompt 字符串**。注入点因此改为后处理这两个 builder 的返回值（与第六道门对 `run_stage_4` 同款"调穿 vendored 再处理返回值"形态）。副作用是升级：指针必然进入角色第一条消息，不再依赖"模型想起来去翻目录"。
- **两个缝不同名，原因在案**：orchestrator 的 digester_factory 是**函数体内 local import**（逐调用重解析→补定义模块即活）；plan.py 顶层 import 持有**自己的拷贝**（补定义模块实证打不到 Stage 1→缝在调用方模块 `stages.plan.build_planner_harness`）。
- 不变量各配真材实料测试（真 vendored builder + 真 `DigesterInputs`/`PlannerInputs` + 真 `ReadScopeGateProcessor` 吃真 `ToolCallEvent`）：只指向此刻在盘文件；Digester 指针只给失败任务（通过任务 prompt 与未补丁 builder 逐字节相等）；旗标关零新代码执行；异常中途 restore 不漏；穿线整周期字节钉不动；指针目标读门放行且 harnessx 源码仍被挡。路径用绝对形态（角色 harness 无 workspace/sandbox，相对路径会落到进程 cwd）。
- 接线零启动器改动：`run_round_with_graph_evidence` 内包住 `run_round` 调用，L2+ 自动生效（launcher 21 测试原样过）。
- **变异探针 ×2（独立）**：agent 拆 `.exists()` 守卫（3 测试倒，无中生有指针被抓）；我拆失败任务域（精确杀 wired-round 对照测试——通过任务的 Digester 被塞失败证据）。均回滚复绿。tests/ghx+recipe **70 过**，lint 净。
- 至此问题清单第 ① 条关闭：L2/L3 的信息流最后一跳闭合，证据从"可读"变为"必见"。

## 点火日志（凭证到位后）

- 通道验证：LiteLLM proxy 2.1s 往返；**DeepSeek-V4-Flash 默认不推理**（无参对照 `completion_tokens=2`、`reasoning:null`）→ provider 零改造。SERPER 实查通过。`.env` 写入前确认 `.gitignore:24` 覆盖。
- **首航失败 #1（小问题，记录）**：vendored 默认 `--tasks` 指向 `webthinker_gaia_dev_classified.json`，本地不存在 → FileNotFoundError。runbook §4 曾照默认路径抄成"本地齐全"，未验存在性，已更正：`--tasks` 必须显式给。重点火用 `holdout6.json`（list 根格式，loader 兼容，无 category 全归 unknown 域）。

## L0 smoke 首航成功（run: L0_smoke_ignition，exit 0，~15 分钟）

- **端到端全绿**：R0 基线 1/1（184,596 tok）→ meta 进化（四角色全真跑，Evolver 还开了 subharness——K1 修的 spawn 首次生产使用）→ R1 复跑 1/1（240,266 tok）。落盘形态齐全：journal/audit/INDEX/curves/digests/landscape/decision/candidates/verdicts；runs/ 已被 gitignore。
- **首个候选死于 YAML**（论文级发现）：C-R1-01（prompt 桶）在 propose 阶段 frontmatter 解析失败（plain scalar 内嵌冒号+引号，line 9 col 563），从未进提案池 → Stage 4 正确 no_op。Critic 拒绝书质量高：确认实质合理、保留推理待未来轮、`hit_rate: null`（unknown≠zero）。**→ L5/E3 的直接弹药：手写 YAML 候选序列化在 DS 模型上首发即碎，图原生候选表面消灭这道解析门。**
- **成本读数是名义值**：$0.59/$0.77 来自定价表（自建 vLLM proxy 实际成本≈GPU 时间），预算决策看 token 不看美元。观测 ~20 万 tok/任务·轮 → 103 床 ×3 轮 ≈ 62M tok/级。
- **SERPER 未被官方任务 harness 使用**：`build_gaia_tools_full` 注册内置抓取链（Wikipedia→Bing→DDG），smoke 行为吻合（Wikipedia 403、Bing 失败、DDG 兜底）。不动——L0 定义=官方原样，且各级必须共享工具基线。key 留给 contrib/serper_search.py 备用，.env 注释已更正。
- 首航失败 #1（默认任务文件缺失）此前已记录并修正 runbook。

## L1 smoke 收官 + 首个真实覆盖缺口（M4c 立项）

- **L1 全绿**：R0 1/1（97,596 tok）→ 进化 **真上船**（R1 状态 ok——L0 的 YAML 失败是抽签不是 DS 硬伤，目前 1/2）→ R1 1/1（43,546 tok）。通过率与 L0 同为 100%，记录无扰动（smoke 级别判据）。
- **全栈 U 覆盖实证**：任务 rollout、Planner、Critic 各 1 U + 身份三哈希；任务 U 781 行、30 个 tool 节点、节点形态正确（`tool:WebSearch@t40`）。**连官方压缩的总结代理都领到了自己的 U**——零适配记录的主张成立到了没预料到的深度。
- **真实缺口（M4c，任务 #33）**：官方压缩把 Evolver 的 479 万 token 逻辑运行切成 4 个 run_id 段（segment_boundary），UnfoldRecorder 跨段累积、22:31:57 一次落盘 9,862 行（5,289 节点/4,572 边/invokes 0）**挂在首段 run_id（4364…）下**——零数据丢失，但 run_id 检索对段 2-4 失明，U 的 run_id 字段误述其实际跨度。压缩代理 harness 直接构造（非 spawn），无 INVOKES 父链——需要 invoked_by 类元数据补链。**不伤 L1 任务级主张，不阻塞 L2/L4 smoke**（均消费单段任务 U）；伤的是 CH5 元环分析的检索面。
- 侦查小账：state.json 显示 Evolver 段 1 就烧 $14.96 名义/479 万 input token——meta 预算在 DS 名义价下也不宽裕，正式跑要看 evolve 实耗曲线。
- **L2 smoke 已点**（hard3 单题 ×2 轮）：要失败任务证据管线才有活干；验证点 = R1 出现 graph_evidence/ 锥+facts，且 Digester/Planner session 的 system prompt 里真出现"Graph evidence (GHX)"指针段（G1b 的生产首验）。

## L0 正式基线收官 + 跑后全链路审计（df17af9 / 5a2eaa4 / 3bf8b38）

- **正式 L0 跑完**（holdout6 × 3 轮，deepseek-chat 双档，run: `L0_official_baseline`，~60 分钟，exit 0）：**R0 4/6 (66.7%) → R1 3/6 (50%) → R2 4/6 (66.7%)，Δ=+0.0pp——官方 AEGIS 三轮零净增益**。R1 ship 两件（C-R1-01 防编造 prompt off-ramp + C-R1-02 bash 重写处理器）；R2 因 Evolver 烧穿被迫 noop，客观上成了同配置重测：R1 vs R2 差 1 题 = 本床噪声包络。逐题：2 题三轮全过（851e570a 步数 20→11→7 递降）、48eb8242 FAIL→PASS→PASS（唯一站住的翻转，恰为 C-R1-01 的预测目标，ship_outcomes 记 hit_rate 1/1）、72e110e7 三轮全挂、d0633230 的 R1 "回归"被 R2 同配置 5 步通过证伪为方差。
- **判分链路实证**：18/18 全走 trace judge（judge provider 无条件构造；唯一降级路径必打 `trace-judge LLM call failed` WARNING，日志零条）。**prompt 链路实证**：三轮 system prompt 均送达（R1/R2 全部 session 含 C-R1-01 改后文本）；`file:///D:` 处理器加载有运行时佐证（其模块的 DeprecationWarning）。meta 环路逐段有下游消费证据（Planner 读 digests×8、Evolver 读 landscape×37 并起 Level-2 验证子 harness、Critic 读候选×16、commit 与 decision 完全吻合）。日志普查：193 WARNING（104 wiki403 + 85 bing 空抓 + 4 IV-4）/ 0 ERROR / 1 良性收尾 traceback / 420 ResourceWarning。
- **四病灶实锤与处置**：
  ① **回归账 off-by-one（恒空）**：`run_meta_aegis.py:875` 传下一轮号、`orchestrator.py:235` 拿它检测 → 永远比"还没跑的轮"；task_history 数据齐全（两题 round0 true→round1 false）而三轮 regressions.md 全空，Critic 白纸黑字"no regressions, no rejection triggered"。**修**：cherry-pick 官方 `1a62993`（`df17af9`，保留原作者），真实 L0 数据重放捞出 2 回归 + C-R1 嫌疑 ship + 强制处理条款；tests/aegis 274 绿。
  ② **IV-4 verdict 校验 4/4 全灭（软失败）**：Critic 引"被审候选文件本身"，白名单无此类。机理：规则只在正则里不在 prompt 里、示例欠定、职责缺类、开环无反馈；且双向漂移（sessions 合法却没展示）。**wontfix**（CH5 活证据，L4 图证据面替换之）；仅批准一条主动偏离：critic.md 示例补 `sessions/<file>#step_N` 行（`3bf8b38`，1 行，critic.md 自此与官方差一行，进偏离台账）。
  ③ **Evolver R2 预算死亡**：$27.2 / 8.8M tok / 200 步，死于候选注册前，草稿搁浅 applied/。不动——基线数据。
  ④ **rollback 不可达**：`Δcount ≤ -3` 在 6 题床 = 塌方 50pp；R1 掉 1 题不触发，事后被 R2 证明**不触发是对的**。不动；证据驱动回滚留给图层。
- **缝合恢复**：Windows 缝合 4 文件被并行 re-vendor 抹掉（期间套件红 5 无人察觉），从会话 transcript 逐字恢复重提交（`5a2eaa4`）。tests/unit 2 失败为预先存在（stop_hook / sandbox timeout，无缝合也复现），未动。
- **流程复盘 → 四条 SOP**：脏树不发车、发车落 commit hash；风险清单不清零不发车（off-by-one 是清单上预警过的未验证项）；验证即 commit、审批管 push（缝合差点丢就是攥在工作树等拍板）；单分支单会话、commit 前 tests/aegis 必绿。
- **悬决**：现版 L0 数据产生于 off-by-one 未修态（与官方自己跑实验的状态一致，作"官方原样"成立）；若要阶梯站在修复版底座，需重跑 L0（~1h，DS 实付个位数美元），否则 L0/L1 间混入回归账修复这一非旗标差异，归因需注记。

## G1c — 注入清单 + 钉的 Windows 化（3789746，L2 smoke 调查产物）

- **Digester 验证盲区闭死**：官方从不落盘 Digester session，其注入指针在生产无物证（L2 首验只能靠 Planner journal + 旁证链）。`_record_injection` 现把每次真实注入写进 `R{n}/graph_evidence/injections.json`（角色/任务/路径），有记录 ⇔ 有注入。
- **钉的第一次假阳性（事变四，良性）**：pin 红了但 `git status` 干净——六个 vendored 文件被官方 pilot 的快照/恢复机制在阶段边界以文本模式回写（mtime 铁证：L0 收尾 22:06:57 / L1 启动 22:12:33 / L2 critic 22:48:30；disk−blob 差恰=行数，每行 +\r）。**内容零篡改，纯 EOL 翻写，Windows 上每跑一次 pilot 必现**。裸字节钉在 Windows 不可用 → 三处哈希器统一改 CRLF→LF 规范化后再 sha256，manifest 从 git-clean 树重建。接受的交换：仅翻换行的攻击不再被抓（Python/MD 语义惰性）。改后变异探针：critic.md 追加 1 字节 → 钉红；还原 → 绿。
- 侦查排除项：L1 Evolver 的 trace 无任何 aegis 路径引用（Bash 190/Write 42 都没碰）——改写者不是进化环，是官方自家机制。
- 套件 70 过，lint 净。L2 smoke 全程未受影响（另进程）。

## L2 smoke 收官（exit 0）——证据管线全链首验 + 两个论文级标本

- 数字：R0 0/1（硬题如设计失败，249k tok）→ 进化零候选 no_op → R1 1/1（59.9k tok）。**Δ=+100pp 的 noop 轮**：配置分毫未动，硬题纯随机翻盘——若这轮恰好上过船，官方归因即记功。成功幻觉的 1 题微缩活标本，K=8 现象的最小复现。
- **证据链全通**：失败锥+facts 物化 ✓ → Planner journal 里指针段在场 ✓ → **landscape.md 引用图证据 3 处**：(a) 读锥判死因（"uniform task_end chain through every processor at step 20 — died at processor teardown"）；(b) 第 111 行：一个失败模式"the Digester did not itself flag, and it is the only one evidenced *solely* by the graph"——官方 Planner 亲笔承认图证据给出了文本管线没有的信息。中心假设的首个定性信号。
- **Evolver 空转**（三炉产出率 1/3）：200 步顶格/724 万 token/零候选；Critic 的 strategy_concern 精确到 trace 指针，还引用了 Evolver 自己没做完的 Pending-Actions（"Write candidates/manifest for C-R1-03"——三个候选全胎死）。正式跑前旋钮：meta 换 reasoner / 提预算 / 接受空轮率——随床位决定一起拍。
- 噪声在册：digest 锚点缩写警告（官方校验器抓住）、ResourceWarning 惯犯。injections.json 缺席符合预期（本炉跑的是 G1c 前的代码）。
- **L4 smoke 已点**（hard3 ×2 轮，gate + 全栈旗标 + G1c 清单首次生产）。

## L4 smoke 收官——阶梯 smoke 序列 L0→L1→L2→L4 四级全绿

- 这炉 R0 硬题碰巧通过（1/1）→ 无失败任务 → 无锥、无 Digester 注入。**injections.json 生产首秀且作用域分毫不差**：只记 Planner（facts.md 一条）——failed-only 规则的生产反证。
- **Evolver 正常交货**：exit=done 42 步、322 万 tok、2 候选（Flash 产率 2/4 轮）。五官方门全过 → **第六道门对两个候选各落一份诚实放行记录**（"PASSED THROUGH (unverifiable — not checked)"，ok:True/checked:False，原因=replay U 不可得），**decision: ship 真上船**，R1 1/1。
- **候选图表面生产首跑**：`graph_evidence/candidates/C-R1-0{1,2}.md`，prompt 桶如实报 0 图编辑（不动处理器拓扑）——不是 bug 是正确语义。
- 至此四级各自的全部验证点均已生产实证。剩余：meta=V4-Pro 验证炉（已点，`.env` GAIA_META_MODEL=deepseek-v4-pro，用户令"换v4pro"）；床位/环境两个决定。

## MetaPro 验证炉（用户令"换v4pro"的验收）

- 面板实证 meta=deepseek-v4-pro（解析链：显式 > GAIA_META_MODEL > 跟随主模型；.env 生效）。任务侧维持 Flash。
- **Evolver 首炉交货**：exit=done 50 步、346 万 tok、1 候选 → 五门全过 → ship。全程 11.5 分钟（对照 L2 Flash 空转炉 ~33 分钟）。
- Planner 效率显著：11 步/9.1 万 tok（L2 Flash 同角色 15 步/35.1 万 tok）。Critic 28 步/196 万 tok。
- 诚实注记：样本各 1，"Pro 治好空转"是方向性信号不是结论；产率对照 Flash 2/4 vs Pro 1/1，正式跑的多轮数据才算数。
- 至此用户三决定已落一（meta=V4-Pro），余二：床位、运行环境。工程侧无未结阻塞项。

## L0_baseline_v2（baseline 分支 vanilla 验证跑）+ 判分假阳性实锤（重大）

- **v2 跑完**（baseline 分支 worktree，`1e1e0b3`，47 分钟，exit 0）：名义 R0 4/6 → R1 5/6 → R2 3/6。R1 ship C-R1-01（Windows 命令兼容处理器，本轮 Evolver 未烧穿）；R2 ship C-R2-01(prompt)+C-R2-02(tools)。**修复版回归账首次正确工作**：R2/regressions.md 比 R1 vs R0（对的轮）、合法为空。
- **判分器假阳性实锤（两版通杀，重大发现）**：deepseek-chat 当 trace judge，对"无答案轨迹"（20 步打满仍在推理、或 1 步退化输出）有 ~40% 假阳性（同输入重放 5 次 2 次 PASS，一次幻觉"明确说了 Guatemala"（全文无此词）、一次自认无答案仍判过）。机械审计（GT 字符串 ∉ assistant responses）+ 人工核对：**36 个判分中 5 个假 PASS**——0b260a57 三次（v1R0/v2R0/v2R1，全文无 0.269 任何变体）、72e110e7 两次（v2R1 1 步零工具自言自语、v2R2 Guatemala 仅存于工具转储）。根因=GT 写在 judge prompt 里+弱 judge 把"讨论得像样"脑补成"答对了"；官方设计的 judge 是 opus-4-7（=--meta-model），弱点是我们换 DS 引入的。
- **审计后口径改写两版结论**：v1 审计 3/6→3/6→4/6（**+16.7pp，C-R1-01 防编造 ship 的 48eb8242 解锁是真的**；名义 Δ=0 反而是 R0 被假阳性灌水）；v2 审计 3/6→3/6→2/6（R2 ship 净伤）。名义曲线与审计曲线方向相反——**6 题床上判分噪声 > 进化信号**。
- **连锁污染**：72e110e7 假 pass 已被 ship_outcomes 记为 C-R1-01 命中——评测噪声向上污染因果账的现场标本（CH5）。
- 发车插曲：worktree 干净检出缺任务数据 → FileNotFoundError；`.gitignore` 写明 GAIA 系 gated 数据禁止再分发（连私仓都不行）——baseline 分支"完整"止于代码，数据+.env 按规矩侧载。
- 悬决升级：判分层加固（强 judge / k 票 / GT-不在场即复核）是否做、做在哪条分支——不加固则 6 题床单个假阳性=16.7pp 噪声，基线分数不可信。

## baseline 线修复合流（00c1c9a 推送 origin）

- 用户令"这些修复也 apply 到我们这边"。核实：regressions off-by-one（df17af9）、Windows 缝合（5a2eaa4，事变三三文件的正规重做 + read_scope_gate 盘符匹配）、critic.md 锚点（3bf8b38）**早已随主线在 HEAD**；判分器两笔（quote-required 判分 f0f09c2、判窗 5→20 轮/1500→4000 字 5d43a2b）经 merge + 对面会话同时 cherry-pick 双路到达，内容收敛一致。工作树 grep 实证 quote-required 在场，evaluator 导入通过，70 套件绿。
- **钉的语义更新（诚实记账）**：完整性 manifest 自 G1c 重建起锚定的是"HEAD 的已审计修补态"（含上述 vendored 修复），不再是 pristine 上游——钉的职能从"证明未改"变为"证明无未审计之改"。
- 并行协同注记：两会话同分支竞写（对面 cherry-pick 与我的 merge 同刻发生），git 自然收敛，无冲突；已推送，origin 为会合点。
- **对阶梯的意义**：此后一切正式跑用加固判分器打分——判分假阳性（5/36 假 PASS、符号翻转）在源头被杀，名义分≈审计分。

## L0_baseline_v3（pro meta + 严格 judge）收官 + 污染通道现场抓获

- **v3 定档**（45min，exit 0）：**R0 2/6 → R1 2/6 → R2 3/6（Δ=+16.7pp）**，机械审计 7/7 PASS 全真、**零假阳性**——首个名义=审计的可信曲线。判分三层加固全生效：引证强制（f0f09c2）+ 全轨迹窗口（5d43a2b，v3 首发现场修：851e570a 第 5 步作答+15 轮自验证被 5 轮窗口误杀，停跑→修→重发）+ fail-closed。
- **v4-pro meta 观感**：Evolver 不再烧穿（101/150 步注册 2+4 候选 vs chat 版 200 步零注册）；R1 ship processor+prompt、R2 ship tools+processor；R2 的 +1 来自 851e570a（前两轮 GitHub raw 拉词典挂了的环境噪声恢复，归因存疑不记 ship 功）。72e110e7 三版九轮零真过=全床最硬题。
- **污染通道现场抓获（重大）**：v3 R2 的 72e110e7 里 agent 搜到 `github.com/harbor-framework/harbor-datasets`——GAIA 任务连 instruction.md/solve.sh/`tests/expected_answer.txt` 全量公开镜像，直接抄到 "The correct answer is **Guatemala**"，20 步撞限没交卷 + judge 掷硬币 FAIL（重放 1P/2F，污染轨迹连 judge 一起毒：有次把 GT 幻觉成泄漏轨迹里的 Kenya）→ 分数侥幸未染。
- **防泄漏层三跑全程空膛（审计发现）**：`HARNESSX_URL_BLOCKLIST` 是 env 驱动、接线在重构臂 `anti_contamination.py`，官方臂 recipe/ 从未装弹。**已装弹**：两份 .env 加官方 GAIA_ANSWER_DOMAINS + harbor-framework 三路径（api.github/github/raw）+ query 规则；6/6 验证（harbor/HF-datasets 拦、words_alpha/维基放行）。v3 暴露期 score-neutral（唯一触点被 FAIL），数据保真；此后所有跑（含 L1+）武装状态，对比口径一致。
- 三版终局：v1 名义 4/6→3/6→4/6（审计 3→3→4）；v2 名义 4/6→5/6→3/6（审计 3→3→2）；**v3 名义=审计 2→2→3**。结论：弱 judge 的名义曲线方向性不可信；严格口径下本任务臂真实底线 2-3/6，v4-pro meta 首现正向末轮。
