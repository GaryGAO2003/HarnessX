# 亲验记录:改进 ideas ⑦–⑫ 审计核验(Aug-05 2026 晚)

**性质**:对 `AUDIT-IDEAS-7-12-VS-UPSTREAM-AUG05.md`(下称"原审计",同批落盘本分支)§2/§4.2 全部 🟡 项的主循环亲验记录。**结论:六轴判定与 doc-11 拟改判词全部坐实,file:line 逐一精确命中,🟡 全部清零升 🟢。**
**方法**:主循环直跑 `git show` / `git grep`(对象库读取,ref 锁定,无摘要器中介——manifest 两次反转教训的合规执行);另一 session 在飞的工作树未触碰。
**定性(用户裁定 Aug-05)**:⑦–⑫ 严格来讲是**问题**(缺陷诊断),不是 novelty——归 fix 线,本分支 `fix/audit-ideas-7-12` 即由此开。
**ref 锚**:upstream 主审 `feat/aegis-experiment@90f5d2d`(tip 未动 = 原审计后官方零新提交);`upstream/main@bf5f199`(Jul-29,最新);我方 `origin/main@a533b07`(Aug-05 19:07)。上游共 10 分支 = 4 代码分支(main + 3×AEGIS)+ 6 网页分支;原审计"全 4 分支"口径 = 4 代码分支,无误。upstream/main 有 recipe evolvers(gaia/tau2/tb2/slime/verl)但**无 aegis 包**。

---

## 1. 六轴逐条(全部 ✅ 坐实)

| 轴 | 原判定 | 亲验证据(逐字引句/精确行号) |
|---|---|---|
| ⑦ 变体×任务路由 | **缺失** | `variant_pool / ALORS / routing freeze / task_rout*` 等在 4 代码分支 + 最新 upstream/main 全零命中。我方 `experiments/variant_pool/`(origin/main,24 文件)仍是论文 §4.5 唯一存世实现 |
| ⑧ 统计接收门 | **缺失** | 三断言全中:(a) `harnessx/aegis/stages/commit.py:254` 逐字 `if all(v.ok for v in results.values()):`;(b) `recipe/gaia_evolver/defaults.py:23` 逐字 `PASS_COUNT_NOISE_THRESHOLD = 3`,配 `--regression-tolerance` 默认 0.03,`run.py:1222` 起双条件同破才回滚——**upstream/main 同路径同行号同在**;(c) stage-5 `adjudicate` 的 hit_rate<0.5 自动回滚**全仓零调用方**,`orchestrator.py:20` NOTE 逐字 "``adjudicate_previous_round`` is intentionally NOT imported here" + `orchestrator.py:130` `TODO(stage5)`。全部为启发式阈值,无任何统计检验 |
| ⑨ 编辑归因 | 部分(观察式) | `harnessx/aegis/data/attribution.py` 模块 docstring 逐字:"joint — …Without an ablation we cannot disentangle cause; credit is shared with any concurrent ships."。direct/orphan/joint 按机械签名(tool_call / processor_invocation)触发,纯观察无消融 |
| ⑩ 冷启动 | 部分(静态) | `harnessx/aegis/data/reputation.py:13` 逐字 `_UNKNOWN_BOOST = 0.7`;4 桶(prompt/tools/config/processor)窗口 5 移动平均 + `downweight_all` 衰减;无 UCB/无计数加成/无 per-variant |
| ⑪ regret/bandit | **缺失** | 全部命中仅 `gateway/core/prompt_templates/SOUL.md:13` 英语散文 "Don't make them regret it"(无关);regret/bandit/UCB/VBS/Thompson 零机制 |
| ⑫ 多样性压力 | 部分(去重) | `gates/novelty.py` 全文 17 行:仅当 `signature in refuted_signatures` 时拒;签名 = `data/signatures.py` 对排序 (path, diff_sha_after) 对的 SHA-256 = 精确签名重提检测;similarity/embedding/QD 零命中。**加料:官方曾有 diversity/explorer/archive 硬配额,已整体废弃**(`stages/plan.py` `BriefQuotaViolation` docstring:"The whole brief-dispatch model is gone… this exception is never raised")= 官方从多样性配额撤退的一手证据 |

**Evolver 靶选补验** ✅:`worst_first|failure_density|target_select` 全分支零命中;`templates/evolver.md` 证实靶由 LLM 按提示语义选(digester pattern label + "Failure Evidence" 硬要求)。我方 `experiments/variant_pool/target.py`(worst_first 默认 + round_robin/failure_density 消融)无官方对应物 → CH3 保真度素材成立。

## 2. doc-11 判词坐实(counterfactual gate)

原审计 §4.2 拟改判词「已接线、能拒、仅查改写输出的 processor 链的窄域真门,非 latent no-op」四点全部直验成立:

1. **已接线**:`orchestrator.py:517-525` 在 run_round 内无条件 `set_counterfactual_context({passing_task_ids, trajectories_dir, k_samples: 3})`;`stages/commit.py:19` import `check_counterfactual_replay`,gate 链第 4 道(structure→novelty→canonicalize→**counterfactual**→replay)。
2. **能拒**:两条真实 fail 路径——候选 YAML 解析失败 → `ok=False`;重放后任一采样任务的 `final_output` 或 `exit_reason` 相对原 passing 轨迹改变 → `ok=False`("counterfactual replay flagged regressions")。
3. **仅查 processor 链**:只实例化配置 `processors` 段(`_instantiate_processors`),只重放 `after_model/after_tool/task_end` 三种 hook;docstring 自供 "No LLM calls, no tool execution — replay works only on recorded events."
4. **对 prompt/工具/预算类编辑恒过**(结构性):此类编辑不改 processor 对录制事件的改写行为 → 重放终态恒等 → pass。另三条静默恒过路径:ctx 未接(`GateVerdict(True, "skipped: no counterfactual context wired")`)/无候选文本/无先前 passing 任务。

→ doc-11 的「官方 counterfactual gate = latent no-op」旧判词应废,替换为上述窄域真门表述。

## 3. 我方 origin/main 侧前提核验(a533b07)

- `experiments/variant_pool/` 全套在 main ✅(pool/router/target/gate/counterfactual_gate/reputation/manifest/ledger…24 文件);
- ⑦–⑫ 对应机制我方代码**同样零实现** ✅(racing/SPRT/UCB/bandit/regret/ALORS 无一存在;grep 命中全为 "t**racing**" 假阳性)→ 六项仍是空地,原审计 §3 优先级(T1=⑧先离线+⑩;T2 条件=⑫双门+⑦;T3 配套=⑨bucket 级+⑪)立项前提有效;
- `\bALORS\b` 全仓零命中 ✅ → 原审计头注「①–⑫ 清单 repo 未落盘」属实;**本分支落盘原审计 + 本记录即为该项修复**。

## 4. 三处偏差(不推翻任何判定,引用时注意)

1. **「五个二值门」有例外路径**:structure/novelty 便宜门先挂时,E6 短路只返回 4 个 verdict(counterfactual 缺席),官方注释 "audit.jsonl still shows all four entries" 自身已过时。全量路径确为 5 门;ship 判据 `all(v.ok)` 不受影响。引用「五门」时加短路脚注。
2. **ab6eb27 不在 origin/main**:它是 `fix/novelty-s4` 上的 merge 提交(Aug-05 03:46,merge feat/observation-channel@12c444e),未合入 main;b65ca01 已在 main。原审计 §1 解冻三条件若按「落在 gh main」口径读则第二条未达;T1 从 fix/novelty-s4 发车则无碍——口径须写明。
3. **RUN-LOG 删 16 行实锤,但未污染远端**:working tree 的 `experiments/docs/RUN-LOG.md` 恰为 0 增 16 删(未提交);origin/main 历史全纯增(近 4 提交 numstat 7/0、2/0、3/0、8/0)。修复动作(恢复条目+撤稿标注,原审计 §4.3)应由持有该工作树的 session 在提交前完成——**本分支不碰该文件**。

## 5. 遗留待办(归属)

- [ ] `fix/novelty-s4` session:提交前恢复 RUN-LOG 16 行 + 撤稿标注(原审计 §4.3);
- [ ] 原审计 §1 解冻条件补一句口径(ab6eb27 所在分支);
- [ ] ⑧⑩(T1)进 CH4 臂/flag 实现时,以本记录为占位与证据基线(全部走默认关 flag,不动基线,兼容 routing-freeze 只读不变量——原审计 §4.1)。

---
*所有证据可复跑:命令均为对 ref 锁定的 `git show`/`git grep`,不依赖任何工作树状态。亲验结论同步记入跨 session 记忆(harnessx-official-aegis-aug05)。*
