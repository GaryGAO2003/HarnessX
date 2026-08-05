# AUDIT-ARMS SPEC v0.1(Aug-05/06 2026,audit 线 T1 双臂)

**定位**:审计线优先级 T1 = ⑧ 统计接收门(racing)+ ⑩ 冷启动层级收缩,两臂均为**离线零 API 成本**实现。诊断出处 = `AUDIT-IDEAS-7-12-VERIFIED-AUG05.md`(六轴亲验)与原审计(canonical = fix/novelty-s4 @d440e8b)。论文锚(v3 亲验):(h) 统计接收检验 ABSENT + §6.6 自供 sub-threshold coupling(5 个编辑逐个过关、第 6 个 −14.0pp);(b) 路由冷启动 ABSENT(H₀ 只定义初始 harness)。生死线 = MDE 9.03pp(引用 doc-12 存活数字,不重推导)。

**硬约束(分开原则,不可失误)**:
1. **只允许新文件**,全部位于 `experiments/audit_arms/` 下;**禁止编辑仓库任何既有文件**(含 RUN-LOG/SPEC/variant_pool/*)。验收时 `git status` 只能出现 audit_arms 新文件。
2. **零依赖 s4-only 代码与 variant_pool 内部**:不 import `experiments.variant_pool`、不 import s4 分支的分析脚本;数据解析独立实现。
3. **数据只读**:`D:/PycharmProj/HarnessX/recipe/gaia_evolver/runs/<run>/comparison.json`(另一 worktree,绝不写入);每次 sim 把所用文件清单+sha1 前 8 位记入 ledger。
4. **记录四表面的本分支替代**:所有规格裁决与 sim 运行记入 `experiments/audit_arms/ARMS-LEDGER.md`(append-only);RUN-LOG canonical 在 s4,本分支不动,合并时摘录。
5. **预注册常数**(防 forking paths;改动须在 ledger 记录理由):`alpha=0.05, beta=0.20`;SPRT H1 网格 `p1 ∈ {0.55, 0.60, 0.75}`(报告各自隐含 uplift θ̂ = d̂·(2·p1−1),d̂=discordant 率);shrinkage 伪计数网格 `m ∈ {1, 2, 4, 8}`;bootstrap `B=2000`,cluster=(run,task);`seed=20260805`。
6. 两臂在生产侧的接线**本分支不做**,只在本文档 §Integration 记集成点(默认关 flag)。

## 数据契约(comparison.json)

`{rounds: [[{round, variant_id, task_id, level, attempts: [{passed, exit_reason, steps, infra_failure, reason}]}]]}`。cell = (run, round, variant, task);`reason` 含 `match|no_match: extracted='...'`(本双臂不需要 answer 文本,只用 passed/exit_reason/infra_failure)。加载器提供 `drop_infra` 开关(默认 True,记 ledger)。

## Module 1 — `racing_gate.py`(⑧)

- **SPRT on discordant pairs**(sequential McNemar):输入为逐对 `(base_pass, cand_pass)`;concordant 对不更新 LLR;H0: p(cand 胜|discordant)=0.5,H1: p=p1;Wald 边界 `A=ln((1−β)/α)`,`B=ln(β/(1−α))`;`decide(pairs) → ACCEPT | REJECT | CONTINUE` + n_used + LLR 轨迹。
- **Hoeffding race** 第二读数:paired uplift 的 Hoeffding CI 排除 0 即判,同 alpha。
- 纯函数 + dataclass 配置;无 I/O。

## Module 2 — `cold_start.py`(⑩)

- 基元 `shrink(s, n, parent_p, m) = (s + m·parent_p)/(n + m)`(beta-binomial 经验贝叶斯)。
- 两级链 `estimate(counts, variant, cluster, m1, m2)`:cell→variant 汇总→池全局;counts 为自持 dict 结构,提供 `from_cells()` 适配器。
- 极限性质:m→0 = 原始 MLE;m→∞ = parent;单调内插。

## Module 3 — `replay_data.py`

- `load_cells(runs_dir, runs=None, min_variants=None, drop_infra=True)` 独立解析 comparison.json,cell schema 同上;`manifest(files)` 返回 {path: sha1[:8]}。

## Sim A — `sim_racing_offline.py`(⑧ 离线重放)

- 数据:K=8 runs(`--min-variants 8` 自动发现,`--runs` 显式覆盖)。
- 真效应配对:同 (run, round, task) 内变体对 (Vi→base, Vj→cand) 全对,真实 per-task 配对结果按 round 顺序流入 SPRT;报告 ACCEPT/REJECT/CONTINUE 率、n_used 分布、按观测 uplift 分箱的放行率。
- **A/A 校准**:同变体 attempt0 vs attempt1 配对(真 null)→ false-ACCEPT 率必须 ≈ alpha 以内。
- 预算重分配读数:相对固定 N 协议,早停 REJECT 节省的 rollouts 期望。
- 输出:`out/racing_offline_report.md` + `.json`。预期 headline(审计裁定的诚实性检验):1–3pp 效应下几乎不放行 → 坐实「racing 不造功效只重分配」。

## Sim B — `sim_cold_start_offline.py`(⑩ 离线重放)

- Leave-future-out:每 run 每 round r≥1,用 rounds<r 计数预测 round r 各 (variant, cluster) 成功率;`--cluster-key {task, level}` 两口径。
- 对照:raw MLE(空 cell 两口径:0.5 / 0.7 boost,后者对齐官方 `_UNKNOWN_BOOST`)vs shrinkage(m 网格)。
- 指标:Brier、log-loss;**n=1 过矫翻转计数**(单观测后估计跨越 0.5 路由阈的 cell 数,F2 风格);m 敏感度曲线。
- 输出:`out/cold_start_offline_report.md` + `.json`。

## Tests(pytest,合成数据,零 artifacts 依赖)

- SPRT:全 concordant→CONTINUE;极端 p1→快速判;alpha/beta 边界数值案例;REJECT 对称性。
- Hoeffding:界单调、n 增大收窄。
- shrink:m→0/m→∞ 极限、单调内插、两级链一致性。
- replay_data:测试自建 tiny comparison.json fixture,解析/过滤/manifest 正确。
- sims 纯函数层:配对构造、LFO 切分各一测试。

## Integration(合并时才接,本分支不做)

- ⑩:Router 估计调用处以 flag `cold_start_shrinkage`(默认 off)切换到 `cold_start.estimate`;兼容 routing-freeze 只读不变量。
- ⑧:gate 侧 flag `ship_racing_gate`(默认 off),SPRT 结果作为 ship 判据的并行读数先跑 shadow 模式。

## 验收

pytest 全绿;两 sim 在真实数据可达时跑通并产出报告与 ledger 记录;`git status` 无任何既有文件改动。
