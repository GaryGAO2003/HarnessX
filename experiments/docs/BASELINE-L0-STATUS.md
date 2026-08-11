# Baseline（L0）线代码现状

> 目标：**官方 AEGIS 原样**跑 GAIA 6 题 × 3 轮 = L0 基线；同命令加 `HARNESSX_GHX_UNFOLD=1` 即 L1。
> 撰于 2026-08-11，分支 `ghx/v6-graph-runtime`。

---

## 1 · 官方源码发现（改变了整条 baseline 路线）

上游 `Darwin-Agent/HarnessX` 的 **`feat/aegis-experiment`** 分支（2026-06-02，他们实际跑实验的分支）带完整官方 AEGIS 实现——此前我们以为不存在、因此手工重构了一整套：

```
harnessx/aegis/
├── agents/          planner / critic / digester / evolver —— 四角色全是真 agent
├── templates/       planner.md / critic.md / evolver.md / digester_*.md（真 Jinja 模板）
├── data/            ledger(678行) / regressions / reputation / scoreboard / journal / archive / attribution
├── gates/           canonicalize / counterfactual / novelty / replay / structure
├── stages/          preprocess / trace_facts / plan / propose / judge / adjudicate / commit
├── orchestrator.py  878 行
└── cli.py           壳——真接线在 recipe/gaia_evolver/run_meta_aegis.py
```

**关键事实**（读源码确认）：

| 事实 | 出处 | 对我们重构的否定 |
|---|---|---|
| Planner 是 agent，读 overview+digests+ledgers，写 `landscape.md` | `agents/planner.py` docstring | 我们的单次调用 + 5 字段 brief 是重构偏离 |
| **Evolver 自己决定 K** | 同上：「downstream Evolver … decides itself how many candidates」 | 我们的 K_t≤4 槽分配是重构偏离 |
| Critic 是 agent，写 verdicts+`decision.md`，且有 `ask_evolver` 工具**有界质询 Evolver** | `agents/critic.py` | 四角色对话真实存在，我们完全没有 |
| 角色 read-scope 挡 harnessx 源码（防 prompt 注入） | `agents/planner.py` | 我们没有 |
| 元预算按角色分账：Planner 25 / Evolvers 50 / Critic 30（每轮 $100 上限，Anthropic 量级） | `run_meta_aegis.py` `EVOLVE_COST` 注释 | — |

---

## 2 · 两臂结构（零文件冲突，同目录共存）

```
recipe/gaia_evolver/
├── run.py                  ┐
├── run_variant_pool.py     ├─ 我们的重构臂（保留：对照 + 审计资产）
├── run_meta.py             ┘
├── run_meta_aegis.py       ← 官方臂（V1 将 vendor，912 行，唯一消费 AegisOrchestrator 的 recipe）
└── data/                   ← 两臂共用的任务集
```

官方臂跑在**我们的图原生核**上：U 记录 / 槽位归因 / 图分发全部挂在 `ProcessorChain`/runloop 层，官方角色 session 与任务 rollout **零适配**即被记录（默认旗标全关，L0 纯净）。

---

## 3 · 代码现状

| 项 | 内容 | 状态 |
|---|---|---|
| 图原生核（M0–M10, P1, R2, Z） | 34 个 commit，四套件全绿，旗标默认关 | ✅ 完成 |
| **V0** · vendor `harnessx/aegis` + `tests/aegis`（逐字节，官方 ~6000 行测试全绿为验收） | agent 施工 | 🔄 在跑 |
| **V1** · vendor `run_meta_aegis.py` + `run_meta.py` delta + `tools/dump_pristine_base.py` | 排队 | ⏳ 等 V0 |
| R1 · 重构臂 prompt 占位符渲染修复 | agent 施工 | 🔄 在跑 |
| G1/G2 · 图层接入官方臂（锥文件 / graph_edits / 第六道门） | L2–L4 | ⏳ 等 V0 报告 |
| P2/E3 · 图 lineage 模板 | L5，真偏离 | ⏳ 等对照数据 |

**V0 报告必须回答的四件事**（放行 G1/G2 的前置）：工作区确切布局；候选记录形状；门插入点；官方 `data/attribution.py`（203 行）已做了什么——避免和 M7 的 U 存在性查询并排造两个。

**已知风险**：官方分支基于旧 upstream/main，核心依赖差（`core/trajectory.py` +21、`read_scope_gate.py` +52、`benchmarks/gaia/evaluator.py` +122）要在我们深度分叉的核上最小化缝合；`feat/aegis` 上有个 regressions off-by-one 修复（`1a62993`）**不在** experiment 分支血缘里，V0 在查它重写后的 `regressions.py` 是修了还是带病。

---

## 4 · 任务数据（本地齐全，无需下载）

`recipe/gaia_evolver/data/`：`holdout6.json`（6 题主集）、`calib6.json`、`hard3.json`、`pilot12/20/30.json`、`frozen_plans.json`。

---

## 5 · 运行命令（LiteLLM / DeepSeek 路由）

官方 runner 的 provider 解析：`--model` 以 `anthropic/` 开头走 AnthropicProvider，**其余全走 LiteLLMProvider**（`--api-base` / `--api-key` 可显式传）。我们一直用 LiteLLM 的 DS key，对应写法：

```powershell
# 项目根目录 .env（run.py:22 同款加载器，两臂共用）：
#   DEEPSEEK_API_KEY=sk-...        ← litellm 对 deepseek/* 模型认这个名字
#   SERPER_API_KEY=...             ← 任务 harness 的 WebSearch
# （若走自建 LiteLLM proxy：改用 --api-base + --api-key 显式传）

# L0 基线（官方原样，图旗标全关）
python recipe/gaia_evolver/run_meta_aegis.py `
  --tasks recipe/gaia_evolver/data/holdout6.json `
  --num-rounds 3 `
  --max-tasks 0 `
  --model deepseek/deepseek-chat `
  --meta-model deepseek/deepseek-chat `
  --run-tag L0_official_baseline

# L1 = 同命令，前面加：
#   $env:HARNESSX_GHX_UNFOLD="1"; $env:HARNESSX_GHX_IDENTITY="1"
```

**官方默认旋钮**（DS 价位下都很宽裕，可原样保留）：`NUM_ROUNDS=3`、`MAX_STEPS=20`、`MAX_COST_USD=15`/任务、`CONCURRENCY=6`、`EVOLVE_COST=100`/轮（Anthropic 量级的元预算；DS 实耗会远低于此）、`NUM_EVOLVERS=2`。注意 `--max-tasks` 默认 **1**（smoke 档），正式跑必须显式给 `0`（=全部）。

**注意**：`--model`/`--meta-model` 的确切默认与 DS 模型名（`deepseek-chat` vs `deepseek-reasoner`）在 V1 vendor 后以实物为准；上表来自上游分支源码直读。

---

## 6 · 阶梯（底盘=官方）

```
L0  官方原样                              ← 本文件的目标
L1  + UNFOLD/IDENTITY（只记录不干预）
L2  + Digester 吃锥文件
L3  + Planner 图事实文件
L4  + Evolver graph_edits + 第六道门 + Critic 子图表面
L5  + 图 lineage 模板（真偏离，P2/E3）
```

每级一个开关，增益逐段归因。

---

## 7 · 阻塞

**唯一外部依赖：`.env` 里的两个 key（DS + SERPER）。** V0→V1 落地后即可起跑 L0。
