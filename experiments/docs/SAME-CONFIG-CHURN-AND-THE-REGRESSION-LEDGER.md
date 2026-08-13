# 同配置churn：回归账把噪声当成因果，并据此回滚了一个 ship

**日期**：2026-08-13（L0/L2_103x10 收官分析）
**一句话**：在逐字节相同的配置上，相邻轮之间 17–24% 的题会翻转。官方回归账
把这些翻转如实列出并指认"联合嫌疑 ship"，于是全战役 **154 条被下达整改的回归里
有 75 条（49%）发生在配置未变的轮之间**——不可能由任何 ship 造成。
L2 R8 把这条链走完了：13 条零配置变更的翻转 → 回滚一个已上车的提示词。

这是本论文"开环诊断"一节最干净的实例，因为它自带对照组：同配置轮就是零干预对照。

---

## 1. 同配置轮之间的逐题churn（对照组）

配置身份按归一化哈希判定（抹掉逐轮 tracer `base_dir` 与 checkout 根名，
其余全部参与）。数据源 `data/task_history.jsonl`，k=1，n=103。

| 臂 | 轮对（同配置） | 变好 | 变坏 | 净 | churn | churn 率 |
|---|---|---|---|---|---|---|
| L0 | R1→R2 | +10 | −13 | −3 | 23 | 22% |
| L0 | R3→R4 | +14 | −11 | +3 | 25 | 24% |
| L0 | R6→R7 | +9 | −11 | −2 | 20 | 19% |
| L0 | R7→R8 | +14 | −8 | +6 | 22 | 21% |
| L0 | R6→R8 | +11 | −7 | +4 | 18 | 17% |
| L2 | R0→R1 | +11 | −12 | −1 | 23 | 22% |
| L2 | R4→R5 | +11 | −7 | +4 | 18 | 17% |
| L2 | R6→R7 | +11 | −13 | −2 | 24 | 23% |

**总分几乎不动（净 −3…+6），底下每轮约 20 题在两个方向上换位。**
"修一个坏一个"这个形状，在**零演化**的条件下就已经完整存在。

原论文（arXiv:2606.14249, Fig. 4）把 Global 臂的这一形状归给演化跑步机。
本表不否定它的 −24.3pp 幅度（远超本包络），但它说明：**该形状本身不能作为
"演化在拆自己的台"的证据**，除非先扣掉同配置churn。据我们所知原文没有这个对照。

## 2. 回归账：算术对，归因错

`R<N>/regressions.md` 由 `data/task_history.jsonl` 机械计算，
逐条核对**列出数恒等于实际丢失数**——账本没有算错。

问题在它同时写下的因果断言（L2 R8/regressions.md 原文）：

> Tasks whose pass-state worsened in R7 versus R6 … **Joint-suspect ships are
> those tagged `round=7` (they built the R7 config that produced these
> regressions).** Evolver/Critic must address these when proposing for R8.

而 R7 的配置与 R6 **逐字节相同**（R7 evolve_status=noop）。没有任何 ship 建过
R7 的配置。这句话在这一轮是空指认。

全战役 16 本非空回归账，**7 本覆盖的是同配置轮对**：

| 臂 | 回归账 | 覆盖轮对 | 配置 | 列出回归 |
|---|---|---|---|---|
| L0 | R3 | R2 vs R1 | 相同 | 13 |
| L0 | R5 | R4 vs R3 | 相同 | 11 |
| L0 | R8 | R7 vs R6 | 相同 | 11 |
| L0 | R9 | R8 vs R7 | 相同 | 8 |
| L2 | R2 | R1 vs R0 | 相同 | 12 |
| L2 | R6 | R5 vs R4 | 相同 | 7 |
| L2 | R8 | R7 vs R6 | 相同 | 13 |
| | | | **合计** | **75** |

两臂所有回归账合计 154 条。**75/154 = 49% 的整改指令指向不可能由 ship 造成的翻转。**

## 3. 闭环实例：13 条噪声 → 回滚一个 ship（L2 R8）

1. R6→R7 同配置，13 题由 ALL_PASS 变 ALL_FAIL（churn 表：L2 R6→R7 丢 13）。
2. `R8/regressions.md` 列出这 13 条，标注"联合嫌疑 = round=7 的 ship"。
3. R8 decision.md 把嫌疑落到 **C-R6-02**（round 6 上车、仍在编的提示词）：
   "each path iterating from the joint suspect (C-R6-02, in force since R6,
   `superseded_by: null`)"。
4. Evolver 提 **C-R8-01 = 把提示词回滚到 R4-02 那版**（decision.md 原文：
   "Revert of the **harmful** C-R6-02 prompt lineage to the R4-02 peak prompt"）。
5. Critic 接受，`decision_type: ship`，**已上车**（R8 起生效）。

"harmful" 这个判语的全部证据基础，是一次零配置变更下的噪声抽样。
注意链条上每一环都尽了本分：账本算对了，Critic 的技术审查也严谨（它在同一份
decision.md 里以实质理由否掉了别的候选）。**错的是这条链缺一个"配置是否变过"
的前置判据**——没有它，噪声与效应在账面上无法区分。

## 4. 修法（登记为 P-8，收官后按偏离流程上车）

回归账在计算 R{N} vs R{N-1} 之前先比配置身份：

- **配置未变** → 该轮 diff 是同配置抽样。账本仍可列出（观测有价值），但必须
  改写为"零干预对照，无联合嫌疑"，**不得下达整改指令，不得指认 ship**。
- **配置已变** → 现行行为不变，但把同配置churn 的实测均值（本战役 ~20 题）
  写进账本抬头，作为读者判断幅度的基线。
- 更进一步（可选）：把"回归"的判据从单轮 diff 改为跨多轮的持续失败，
  churn 在多轮上会互相抵消，持续项才是信号。

这条**不动 GHX，只动 vendored 面**，因此进 `docs/aegis-vendored-deviations.md`
登记，两臂同改，在飞不追溯。

## 5. 复核命令

```bash
# churn 表
python - <<'PY'
import io,json,os,hashlib,itertools
def cfg(p):
    o=[('  base_dir: <NORM>\n' if 'base_dir:' in l else l).replace('HarnessX-baseline','HarnessX')
       for l in io.open(p,encoding='utf-8',errors='replace')]
    return hashlib.sha256(''.join(o).encode()).hexdigest()[:10]
base=r'…\runs\L2_103x10'
h={}
for line in io.open(os.path.join(base,'data','task_history.jsonl'),encoding='utf-8'):
    d=json.loads(line); h.setdefault(d['round'],{})[d['task_id']]=bool(d['passed'])
g={}
for n in sorted(h):
    p=os.path.join(base,'R%d'%n,'config.yaml')
    if os.path.isfile(p): g.setdefault(cfg(p),[]).append(n)
for _,rs in g.items():
    for a,b in itertools.combinations(rs,2):
        c=set(h[a])&set(h[b])
        print(a,b,'+%d -%d'%(sum(1 for t in c if not h[a][t] and h[b][t]),
                             sum(1 for t in c if h[a][t] and not h[b][t])))
PY
```

出处：`R<N>/regressions.md`（账本自述）、`R8/decision.md`（L2，C-R8-01 判语）、
`data/task_history.jsonl`（逐题逐轮）、归一化配置身份见
`experiments/analysis/campaign_readout.py:normalised_config`。
