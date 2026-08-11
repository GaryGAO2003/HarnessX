# Baseline（L0）与阶梯点火 Runbook

> 目标：**官方 AEGIS 原样**跑 GAIA = L0 基线；`--ghx-level` 逐级拨到 4 = 阶梯对照。
> 撰于 2026-08-11；G3 落地（`dc3ac39`）后全面刷新，分支 `ghx/v6-graph-runtime`。
> 唯一入口：`recipe/gaia_evolver/run_meta_aegis_ghx.py`（L0 时纯委派 vendored runner，行为与直跑官方等同）。

---

## 1 · 官方源码发现（历史背景，保留）

上游 `Darwin-Agent/HarnessX` 的 **`feat/aegis-experiment`** 分支带完整官方 AEGIS 实现：

```
harnessx/aegis/
├── agents/          planner / critic / digester / evolver —— 四角色全是真 agent
├── templates/       planner.md / critic.md / evolver.md / digester_*.md
├── data/            ledger / regressions / reputation / scoreboard / journal / archive / attribution
├── gates/           canonicalize / counterfactual / novelty / replay / structure
├── stages/          preprocess / trace_facts / plan / propose / judge / adjudicate / commit
├── orchestrator.py  878 行
└── cli.py           壳——真接线在 recipe/gaia_evolver/run_meta_aegis.py
```

已全量 vendored 逐字节（V0 `872aa07` + V1+ `1a39f25`），`tests/ghx/test_vendored_integrity.py` 以 sha256 清单执法。

---

## 2 · 两臂结构（零文件冲突，同目录共存）

```
recipe/gaia_evolver/
├── run.py / run_variant_pool.py / run_meta.py   ← 我们的重构臂（保留：对照 + 审计资产）
├── run_meta_aegis.py                            ← 官方臂 runner（vendored 逐字节，912 行）
├── run_meta_aegis_ghx.py                        ← ★ 阶梯启动器（G3，唯一日常入口）
└── data/                                        ← 两臂共用任务集
```

官方臂跑在我们的图原生核上：U 记录 / 槽位归因全部挂在 runloop 层，官方角色 session 与任务 rollout **零适配**即被记录（默认旗标全关，L0 纯净）。

---

## 3 · 代码现状（全部完成）

| 项 | 状态 |
|---|---|
| 图原生核 M0–M10 / P1 / R2 / K1 / R1 / D1 | ✅ |
| V0 + V1+ · vendor 官方包 + runner + tests/aegis（完整性钉执法） | ✅ |
| G1 · 图证据落官方工作区（锥地图 + facts，读门实证放行） | ✅ |
| G2 · 第六道门（图存在性计数替代正则数文本；`checked` 诚实不变量） | ✅ |
| G3 · 阶梯启动器 `--ghx-level 0..4` + meta 模型单钥解析 | ✅ `dc3ac39` |
| G1b · brief 指针注入（让 L2/L3 证据**被读到**，不只可读） | 🔄 施工中 |
| P2/E3 · 图 lineage 模板（L5 真偏离） | ⏳ 等阶梯数据 |

套件基线：tests/ghx+recipe 61 ｜ graph+core+integration 872 ｜ variant_pool 1184 ｜ unit 976（+2 Windows 既有失败）｜ aegis 267（+5 已归因环境失败，Linux CI 绿）。

---

## 4 · 任务数据（本地齐全，无需下载）

`recipe/gaia_evolver/data/`：`holdout6.json`、`calib6.json`、`hard3.json`、`pilot12/20/30.json`、`smoke10.json`、`pool_bed50.json`、`webthinker_gaia_dev.json`（完整 dev 集）等。
⚠ vendored runner 的默认 `--tasks` 指向 `webthinker_gaia_dev_classified.json`——**本地不存在**（首航实测 FileNotFoundError），所以 `--tasks` 必须显式给。本地文件是 list 根格式，loader 兼容（无 `category` 字段则全部归 "unknown" 域）。

**床位建议**：103×3 实测同配置噪声包络 ±5 题（≈±5%）。pilot6/12 上级间差会被噪声淹没——阶梯对照要么上大床（DS 价位可负担），要么小床多种子重复。点火前先定。

---

## 5 · 运行命令与凭证（⚠ 本节较旧版有实质更正）

**Provider 路由（读 vendored 源码 `run_meta.py:_make_provider` 确认）**：
- `--model` 以 `anthropic/` 开头 → AnthropicProvider（读 `ANTHROPIC_API_KEY` / `ANTHROPIC_API_BASE`）。
- **其余一律 → OpenAIProvider（OpenAI 兼容客户端）**，key 解析：`--api-key` > `OPENAI_API_KEY` > `LITELLM_API_KEY`；base：`--api-base` > `OPENAI_API_BASE`。
- ❌ 旧版写的 `DEEPSEEK_API_KEY` **vendored runner 根本不读**——照旧版配置点火必失败。
- `--provider-id`（默认 `azure_openai`）只变成 `X-Model-Provider-Id` 请求头：直连 DS 会被忽略、无害；自建 LiteLLM proxy 若按该头路由则需按网关约定覆盖（`GAIA_PROVIDER_ID` env 或 `--provider-id`）。

**根目录 `.env`**（runner 自动加载）：

```ini
# 走 LiteLLM proxy（我们的常用形态）：
OPENAI_API_BASE=<litellm-proxy-url>/v1
OPENAI_API_KEY=<litellm key>            # LITELLM_API_KEY 亦可
# 或直连 DeepSeek：OPENAI_API_BASE=https://api.deepseek.com/v1 + DS key
SERPER_API_KEY=...                       # 任务 harness 的 WebSearch
```

模型名以端点认的为准：LiteLLM proxy 常用 `deepseek/deepseek-chat`；直连 DS 用 `deepseek-chat` / `deepseek-reasoner`（非 anthropic/ 前缀的名字原样透传）。

**L0 基线（Windows PowerShell；`PYTHONUTF8=1` 必须，cp936 环境防编码炸）**：

```powershell
$env:PYTHONUTF8="1"
python recipe/gaia_evolver/run_meta_aegis_ghx.py `
  --ghx-level 0 `
  --tasks recipe/gaia_evolver/data/holdout6.json `
  --num-rounds 3 --max-tasks 0 `
  --model deepseek/deepseek-chat `
  --run-tag L0_official_baseline
```

- `--max-tasks` 默认 **1**（smoke 档），正式跑必须显式 `0`（=全部）。
- **meta 模型不用管**：G3 解析链 = 显式 `--meta-model` > `GAIA_META_MODEL` > **跟随 `--model`**。vendored 的 anthropic 默认已被排除出链，单钥可跑全梯（旧版要求手工传 `--meta-model` 已不必要）。
- 先加 `--dry-run` 核对面板（模型、meta、旗标、任务数），exit 0 再去掉。
- L1–L4 = 同命令改 `--ghx-level 1..4`。显式 `HARNESSX_GHX_*` env 永远压过等级号。

**官方默认旋钮**：`NUM_ROUNDS=3`、`MAX_STEPS=20`、`MAX_COST_USD=15`/任务、`CONCURRENCY=6`、`EVOLVE_COST=100`/轮（Anthropic 量级元预算，DS 实耗远低）、`NUM_EVOLVERS=2`（注：官方 Evolver 自决 K，此旋钮实际不起数量作用）。

---

## 6 · 阶梯（as-built，以启动器实现为准）

```
L0  官方原样（启动器纯委派，零旗标）
L1  + UNFOLD + IDENTITY          只记录不干预；U/身份文件出现在轨迹旁
L2  + AEGIS_EVIDENCE             锥地图 + facts 落轮次工作区（G1b 落地后角色 brief 带指针）
L3  = L2（facts 与锥共用一个开关；将来可拆，编号不重排）
L4  + GRAPH_GATE                 第六道门。⚠ 现阶段无真任务 replay U，门诚实放行
                                 （checked=False 记因）——行为上 L4≡L3，直到 replay
                                 改跑真任务的模块落地
L5  图 lineage 模板（P2/E3，等对照数据后再做）
```

---

## 7 · 阻塞与环境决定

1. **唯一外部依赖：`.env` 两个 key**（LiteLLM/DS + SERPER，按 §5 的**新**变量名）。
2. **运行环境要先定**：官方 ledger 归档指针是 POSIX 正则，Windows 长跑会伤 reputation 记账；CRLF 亦可能让代码哈希跨 OS 漂移。**整条阶梯在同一环境跑完，推荐 WSL**；Windows 跑 pilot 可接受。
3. 床位与种子策略（§4）点火前定。
