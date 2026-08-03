# 冻结包 · CH4 四条臂(Aug-03 立,发车前不得修改判读规则)

> 本文在跑之前写定 ①测什么 ②每种结果怎么读。发车后只允许追加数值。
>
> **前置**:必须先有一个冻结池。池来自 CH3 两条臂中**分化更好**的那条
> (判据见 `FREEZE-CAPABILITY-ARM.md` §3 的三轴)。用哪个池须在发车前写定并记入本文。

---

## 1. 四条臂

全部跑在**同一个冻结池**、**同一批重放计划**上,`--decomp-synth-guard` 档位**必须一致**。

| 臂 | 分解 | 子任务路由 | 预算 | 隔离出什么 |
|---|---|---|---|---|
| **A1** | 否 | — | 整任务 20 步 | 被复现的系统:整任务、单变体 |
| **B0** | 是 | `single` | **共享 20 步** | 只有"拆开",等预算 |
| **B1** | 是 | `round_robin` | 共享 20 步 | 分工但无专业化 |
| **B2** | 是 | `ledger` | 共享 20 步 | 分工**且**专业化 |

**主读数 = `B2 − B1`。** 两臂拆同样的计划、跑同一个池、分同样的活,
**只差"派活依据是信用信号还是固定轮转"** ⇒ 隔离出「派得好」相对「派了」的价值。
这就是 RQ2 的答案。

**次读数**:`B1 − A1`(拆开+分工整体有没有用)、`B0 − A1`(只拆开有没有用,等预算)、
`B0 − B1`(拆开 vs 分工)。

---

## 2. 命令

```bash
POOL=recipe/gaia_evolver/runs/<CH3 胜出臂>
COMMON="--provider-id deepseek --decomp-eval \
  --data-path recipe/gaia_evolver/data/webthinker_gaia_dev.json \
  --max-tasks 103 --pass-k 2 --max-steps 20 \
  --model deepseek/deepseek-v4-flash --meta-model deepseek/deepseek-v4-pro \
  --search-backend serper --decomp-synth-guard strict \
  --decomp-pool-from $POOL"

# A1 — 整任务。空 oracle ⇒ 每题走整任务兜底,不需要单独代码路径
... $COMMON --decomp-source file:recipe/gaia_evolver/data/empty_plans.json \
    --decomp-concurrency 8 --run-tag ch4_a1

# 三条 B 臂共用(注意 --decomp-credit 也在这里,见下)
BCOMMON="--decomp-source file:recipe/gaia_evolver/data/frozen_plans.json --decomp-budget shared \
         --decomp-credit subtask_convergence"

# B0 — 拆开,不分工,等预算
... $COMMON $BCOMMON --decomp-routing single      --decomp-concurrency 8 --run-tag ch4_b0

# B1 — 分工,无专业化
... $COMMON $BCOMMON --decomp-routing round_robin --decomp-concurrency 8 --run-tag ch4_b1

# B2 — 分工 + 专业化。⚠️ 必须串行:ledger 路由读的账本正被并发任务写
... $COMMON $BCOMMON --decomp-routing ledger      --decomp-concurrency 1 --run-tag ch4_b2
```

⚠️ **`--decomp-credit subtask_convergence` 必须三条 B 臂全开,不能只给 B2。**
否则 `B2 − B1` 差的是**两样**东西(路由 + 信用信号),主读数就不再是单因子。
B0/B1 的路由不读账本,故该旗标对它们**无行为影响**,只是让 manifest 三臂一致、
消除审稿疑点。A1 不涉分解,保持默认。

**成本**:A1/B0/B1 各约 41 分(并发 8),B2 约 6h(强制串行)。合计约 **8h**。

**冻结计划**(已产出,四臂必须共用):

```
recipe/gaia_evolver/data/frozen_plans.json
  sha256 60abc1dc1b8adef4afcdb16cddbf5431b12ac28a35c8f4d360ce1459eb085fb4
  103 题,子任务数 1–9(均 4.25)
  已验证 FileDecomposer 可重放 103/103
```

计划取自**第二遍分解**(第一遍只存了 id+type,不可重放);
**不是取自共识** —— 共识是逐类型投票的产物,没有任何分解器真的产出过它,
重放它等于重放一个从未跑过的东西。

配套划分:`task_clusters_consensus.json`,sha256 `ae3fec2f…`,
三遍多数票,合并后 **7 簇** `[34,17,16,10,9,9,8]`。

---

## 3. 预注册判读规则

- **一律同题配对**(McNemar / 配对 bootstrap)。噪声地板 SD **4.57pp**(n=103),
  2SD 门槛 **9.1pp**。**聚合差值低于门槛不得作为结论。**
- **`--decomp-synth-guard strict` 四臂一致**。开启后分解侧绝对分数会下降 ——
  下降的部分本就不是管线挣的(M-33)。四臂档位不一致则全部作废。
- **逐臂报告 fallback 率**。分解频繁失败的臂实际在跑整任务兜底,
  对比会被稀释到零,而原因与分工无关(M-33 之外的独立污染源)。
  ⚠️ **A1 的 fallback 率按设计就是 100%**(`fallback_reason=oracle_missing`)——
  空 oracle 正是它走整任务的机制,**不是失败**。三条 B 臂的 fallback 率才是诊断量。
  已离线验证:空 oracle 抛 `DecompUnavailable` → `_fallback` → 整任务**全额** 20 步(M-39 保留),
  故 A1 与 B 臂的预算对照成立。
- **收敛信用的局限随 B2 结果并报**:它测**做完**不测**做对**;
  提前结束但答错的子任务被记为成功(M-36)。
- **共享预算的下限须声明**:计划长于预算时每子任务保底 1 步,
  该情形下链会略超预算(M-39)。报告受影响的题数。
- **成本一并报告**,但**不作为归一化手段** —— 预算已由构造配平(M-39),
  再做事后归一化等于重复校正。

---

## 4. 每种结果许可什么结论

| 结果 | 可写的结论 |
|---|---|
| `B2 > B1` 过门槛 | **按能力分工优于均匀分工**,信用信号起了作用。RQ2 正面 |
| `B2 ≈ B1`,但 `B1 > A1` | 分工有用、**但派给谁无所谓** ⇒ 增益来自并行/结构,不来自专业化。这是**明确结论** |
| `B2 ≈ B1 ≈ A1` | 等预算下分解不带来增益。由 C-1 独立支持(**88.7% 的失败是"没做完"**),指向预算而非结构 |
| `B0 ≈ A1` 但 `B1 > B0` | 拆开本身无用,**分工才有用** ⇒ 价值在多变体而非在分解 |
| 任一 B 臂 < A1 | 分解有害。须先查 fallback 率与共享预算下限的影响,再下此结论 |

**每一种都有可写的结论。** A1/B0/B1 三条对照臂的存在就是为了让阴性结果可解释。

---

## 5. 已知威胁(结果中必须并列)

1. **单种子**。
2. **收敛信用测"做完"不测"做对"**(M-36)。精确替代(逐子任务裁判 / 反事实换变体重跑)
   贵数倍,列 future work。
3. **共享预算的下限**:长计划的题会略超预算(M-39)。
4. **B2 强制串行**(M-35),故其墙钟与其余三臂不可比;**准确率可比,成本不可比**。
5. **搜索环境**:一部分检索返回空,压低绝对分数。四臂条件相同。
6. **计划质量**:全部来自同一次分解器调用。计划本身的好坏是四臂的公共上界,
   不是变量 —— 但它限制了所有 B 臂能达到的天花板,须声明。

---

## 6. 收官检查

- [ ] 四臂 `decomp_manifest.json` 的 `decomp_synth_guard` / `decomp_budget` / `decomp_source` 逐项一致(除设计差异)
- [ ] 四臂重放了同一份计划文件(sha 相同)
- [ ] 逐臂 fallback 率
- [ ] `B2 − B1` 同题配对 + 2SD
- [ ] 三条次读数
- [ ] 受共享预算下限影响的题数
- [ ] 结果写入 RUN-LOG,表存 `experiments/analysis/`
