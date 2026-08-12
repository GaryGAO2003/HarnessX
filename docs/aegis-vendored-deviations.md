# Vendored AEGIS 偏离台账（authoritative ledger）

**规矩**：vendored 面上的每一处改动都必须与原本区分开、单独记录（用户令 2026-08-12）。
本文件是唯一权威清单；论文附录 A（偏差登记）的 vendored 节从这里直转。

## Vendored 面的定义与纯净基准

| 面 | 纯净基准 commit | 说明 |
|---|---|---|
| `harnessx/aegis/**` | `872aa07`（V0，byte-identical 落库）+ `1a39f25`（V1+，全量吸收上游 delta） | 官方 AEGIS 包 |
| `recipe/gaia_evolver/run_meta_aegis.py` | `1a39f25` | vendored pilot CLI |
| `recipe/gaia_evolver/run_meta.py` | `58d0ede`（init）+ `1a39f25` | pilot 执行层 |

**机械校验**（任何时刻可复核）：

```bash
git diff 1a39f25..HEAD -- harnessx/aegis/ recipe/gaia_evolver/run_meta_aegis.py recipe/gaia_evolver/run_meta.py
# 上述 diff 必须恰好等于下表"已生效"各行 diff 的并集，多一字节即为未登记偏离。
```

## 已生效的偏离（L0_103x10 / L2_103x10 两臂当前运行的就是 基准+这四条）

| ID | commit | 文件 | 内容 | 动机 |
|---|---|---|---|---|
| V-D1 | `3bf8b38` | `templates/critic.md`（+1 行） | 锚点示例补齐三种校验器合法形态 | 判决书锚点格式高频废件 |
| V-D2 | `5a2eaa4` | `_paths.py` `apply.py` `data/ledger.py` | Windows 垫片（路径/编码） | 官方验收套件在 Windows 跑通 |
| V-D3 | `df17af9` | `data/regressions.py` `orchestrator.py` | 回归账 evolve 轮初 off-by-one 修正 | 回归账恒空实锤（L0 基线审计） |
| V-D4 | `98084d2`（baseline 侧 `0fd17a2`） | `run_meta_aegis.py` | `--search-backend chain\|serper\|serper_only`，默认 chain 字节不变 | 原生刮链 403 大面积失效；旧裁定"原生链不可用" |

## 已登记、未生效的偏离（补丁文件形态，L4 发车门统一裁决）

载荷在 `patches/aegis/`，**vendored 工作区当前为纯净态**（生成补丁后立即还原）。
生效流程：baseline 分支逐条 `git apply` + 独立 commit（前缀 `deviation(aegis):`）→
cherry-pick 到 ghx 分支——与 V-D4 同流程。**两臂必须同改**；正在跑的臂不追溯。

| ID | 补丁文件 | 目标文件 | 一句话 | 治什么 |
|---|---|---|---|---|
| V-D5 (P-1) | `P-1-draft-manifest-early.patch` | `templates/evolver.md` | "before writing the manifest"→"before FINALIZING" + Draft-manifests-EARLY 段 | 提交末置（烧穿主因） |
| V-D6 (P-2) | `P-2-budget-visibility.patch` | `stages/propose.py` | 任务消息写明 200 步/$上限，留 30 步收尾 | 无截止感 |
| V-D7 (P-3) | `P-3-anchor-contract.patch` | `templates/critic.md` | 锚点合同显式化：仅三种前缀、禁 `: `、禁 `applied/`/`meta_sessions/`，GOOD/BAD 例 | 判决格式彩票（双臂已废 ≥6 份） |
| V-D8 (P-4) | `P-4-verification-done-cap.patch` | `templates/evolver.md` | 每候选一次 L1+L2 通过即"验证完成"，验证脚本 ≤2 | 验证无底洞 |
| V-D9 (P-5) | `P-5-compaction-deliverables-section.patch` | `agents/evolver.py` | 压缩摘要强制第五节 Deliverables status | 压缩失忆 |

依据：`experiments/docs/EVOLVER-BURNOUT-PROMPT-AUDIT.md`（输入面清点 + 因果链 +
预期读数）。校验器合同出处：`harnessx/aegis/gates/structure.py:41-61`。

## 登记纪律

1. 新偏离 = 先在本表加行（含动机），再施工；一条偏离一个 commit，消息前缀
   `deviation(aegis):`（历史四条保留原前缀，不改写历史）。
2. 补丁文件是偏离的**先行记录**：`git apply --check patches/aegis/P-*.patch`
   随时可验证可应用性；应用后该行从"未生效"表移入"已生效"表并补 commit 号。
3. GHX overlay（`harnessx/graph/**`、`recipe/gaia_evolver/run_meta_aegis_ghx.py`、
   注入器）**不属于本台账**——它们是研究贡献本身，不碰 vendored 字节
   （G3 启动器 + L1 三哈希身份证明 ≡L0 为证），有自己的模块与测试。
