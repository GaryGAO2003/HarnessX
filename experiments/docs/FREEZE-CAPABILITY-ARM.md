# 冻结包 · E-capability 臂(Aug-03 立,发车前不得修改判读规则)

> **冻结包制度**:本文在跑之前写定 ①测什么 ②每种结果怎么读。
> 目的是取消"结果不好看就换个指标"的选项。
> 发车后本文只允许追加实际数值,**不允许改判读规则**。

---

## 1. 这一臂问什么

CH3 的诊断是:路由按簇 `argmax`,故**有效池 = min(K, 簇数)**;
GAIA 难度分簇只有 3 个,`K=8` 中五个变体在算术上闲置。
第二条通道:未测量的 `(变体,簇)` 取先验 0.5,永远输给已测量的,早期落败即永久冻结。

**本臂问:把分簇维度从难度换成能力、并打开探索,池子会分化吗?**

对照臂是 **e_pervar3**(难度 3 簇,ε=0),两臂**只差三个旗标**。

---

## 2. 发车命令

```bash
cd /d/PycharmProj/HarnessX && \
DEEPSEEK_API_BASE="https://litellm.yangtzeailab.com/v1" \
DEEPSEEK_API_KEY="<lab key>" \
SERPER_API_KEY="<serper key>" \
python -u -m recipe.gaia_evolver.run_variant_pool \
  --run-tag e_capability \
  --provider-id deepseek \
  --data-path recipe/gaia_evolver/data/webthinker_gaia_dev.json \
  --max-tasks 103 --pool-k 8 --num-rounds 16 --patience 16 \
  --max-steps 20 --concurrency 10 --pass-k 2 --seed 0 \
  --candidates-per-round 4 \
  --model deepseek/deepseek-v4-flash --meta-model deepseek/deepseek-v4-pro \
  --aegis-digester llm --aegis-planner llm --aegis-critic llm \
  --actionability-threshold 0.5 --evolve-retry 2 \
  --search-backend serper --evolve-commit-bounce on \
  --regression-accountability shipped_only \
  --regression-baseline per_variant \
  --cluster-source capability \
  --cluster-map recipe/gaia_evolver/data/task_clusters_consensus.json \
  --epsilon 0.05 \
  > recipe/gaia_evolver/runs/e_capability.console.log 2>&1
```

**三个变更旗标**:`--cluster-source` / `--cluster-map` / `--epsilon`。

**ε 定为 0.05,不是 0.1。** 理由:重放显示 0.1 铺开更多,但**重放看不到准确率代价**;
ε 的作用是买测量,不是最大化"有负载变体数",故取能买到测量的较小值。
0.05 在 103 题上约等于每轮 5 道题走随机,16 轮累计约 80 次探索,
足以让任一变体在任一簇上积累观测。

**发车后立即按 `PREFLIGHT.md` A1 做全字段 diff**,参照跑 = `e_pervar3`。
预期残余差异:`cluster_source`;预期新增 provenance:`cluster_source=capability` 与 `epsilon=0.05` 两条。
出现其它任何差异 = 停跑。

---

## 3. 预注册读数

### R-1 分化(主读数,三轴同时看)

| 轴 | 怎么算 | 
|---|---|
| 提示词 | 终池中**互不相同的系统提示词个数** |
| 能力 | **逐簇胜率的离散度**:对每个变体,取它在各簇上的 Laplace 胜率,算标准差;再对活跃变体取均值 |
| 配置 | 终池中**互不相同的归一化配置摘要个数** |

工具:`experiments/variant_pool/pool_differentiation.py`。

**判据(发车前定死)**:称"分化出现"要求**三轴同时高于对照臂**。
任一轴不动而声称分化 = 不成立。三轴齐动但幅度小于噪声 = 不成立(见 R-4)。

### R-2 负载(检验离线重放的预测)

- 终轮**基尼系数**
- 终轮**持有 ≥1 道题的变体数**
- **全程曾持有过任务的变体数**

**这是唯一有事前预测的读数**,故它检验的是预测而非仅描述结果:
重放预测能力分簇 + ε=0.1 → 基尼 0.76、约 6.8/8 有负载(对照 0.72、3/8)。
ε=0.05 的预测介于两者之间。**实测与预测方向不符,须在结果中明写,不得只报实测。**

### R-3 准确率

- 逐轮 pass@2 曲线、pass@1 曲线
- 终值、峰值、`终−峰`

### R-4 判读规则

- **一律同题配对**(McNemar / 配对 bootstrap)。噪声地板实测 SD **4.57pp**(n=103),
  2SD 门槛 **9.1pp**。**聚合 pass@2 差值小于该门槛不得作为结论。**
- **R0 基线本身有方差**:同一 H0 已测出 64.1 / 66.0 / 69.9(极差 5.8pp)。
  两臂比较必须**各自相对自身 R0**,不得跨臂比绝对值。
- **峰值须去偏**:peak = max-of-16,σ=4.57 时期望比均值高约 **8pp**,
  故 `终−峰` 为负是构造必然,**不得单独当退化证据**。
- **infra 失败**按论文 A.3 p.29 计为失败、不重采样,单列报告。
- **ε 的代价须并报**:若负载改善而准确率下降,如实写成"以准确率换测量覆盖",
  不得只报有利的一侧。

---

## 4. 每种结果许可什么结论

| 结果 | 可写的结论 |
|---|---|
| 三轴分化↑ 且 负载铺开 | 专业化受**路由分簇维度**限制,而非受闸门限制。CH3 主结论成立 |
| 负载铺开 但 三轴不动 | 分簇只解决了**容量**,没解决**方向**。变体拿到了活但没长出差异 ⇒ 指向"演化压力"而非"路由"。这是**明确结论**,不是空结果 |
| 负载也没铺开 | 重放预测被证伪。须回头查 ε 是否真的生效(读 lock 的 provenance + 路由日志) |
| 三轴分化↑ 但准确率↓ | 分化以准确率为代价。如实报告;这仍是 CH4 可用的池子(CH4 要的是差异,不是绝对分数) |

**四种情况都有可写的结论。** 这是分层读数的目的。

---

## 5. 已知威胁(结果中必须并列)

1. **单种子**。论文规划 3 个,预算允许 1 个。
2. **M-37 残留**。若 Evolver 仍写错 `file:` 拼写,自测路径的产物投递仍失败。
   收官须按 `PREFLIGHT.md` E1/E2 重新计数。
3. **搜索环境**。一部分检索返回空,压低绝对分数、增加方差。两臂条件相同。
4. **🔴 分解器是有噪声的仪器 —— 必须报这个数**。同一批 103 题三次独立分解,
   **逐题标签完全相同只有 42.7% / 44.1% / 46.1%**(Jaccard 0.77–0.78)。
   故已改用**三遍逐类型多数票**的共识划分。留一验证(两遍共识互比)显示可重现度
   升到 **63.1–66.0%**(Jaccard 0.84–0.87)——**改善实质,但仍只有约 64%**。
   ⚠️ 实际使用的是三遍共识,应不低于此,但**未直接测过**,措辞须照实。
   标签只从题面算、解题前冻结、两臂共用同一文件(sha 记入 lock),故不构成泄漏。
5. **簇数 7 < K=8**。共识划分 min-size=8 合并后得 **7 簇**
   `[34,17,16,10,9,9,8]`,故有效池上限是 7 不是 8。
   **这是床的性质不是配置失误**,报告时须写明。
   (对照:`gaia_level` 只有 3 簇;单遍划分合并后 5 簇。)

---

## 6. 收官检查

- [ ] `PREFLIGHT.md` E1:投递警告按路径计数,gate/active 必须为 0
- [ ] `PREFLIGHT.md` E2:候选 config 的 `file:` 拼写全为两斜杠
- [ ] 三轴分化 vs e_pervar3
- [ ] 负载三数 vs 重放预测(方向是否一致)
- [ ] 同题配对检验 + 2SD 门槛
- [ ] infra 失败率单列
- [ ] 全部结果写入 RUN-LOG,曲线与三轴表存入 `experiments/analysis/`
