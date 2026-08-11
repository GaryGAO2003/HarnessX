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
