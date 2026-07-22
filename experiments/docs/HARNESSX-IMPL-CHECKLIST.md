# HarnessX 变体池自建施工清单(Jul-22,实施级)

> 来源:researcher 两轮全库精读(clone = `%LOCALAPPDATA%\Temp\claude\D--PycharmProj-MAS-Directions\8d4fb152-586f-49b7-9d92-83d5b63d60ab\scratchpad\HarnessX`)+ 论文 PDF p.8-11/17-18 亲读。主循环抽查锚点:`run.py:633/672/683/1174`、`task.py:76`、`validate_workflow.py:688-694` 全部属实。
> 前提(L4 已验):repo 里论文 §4.5 变体池机制**零实现**;repo 真实现 = MetaHarness 单谱系爬山环 ≈ Table 5 Global 对照组(49.5)。自建 = 在其上建 Ensemble 臂(87.4)。
> 深度标注:全文 L4(代码逐行亲验 + PDF 亲读),除 LOC/人时估计(工程判断)。

## Part A — 基础设施深挖结论(五块)

1. **任务加载**:GAIA recipe 走 `load_gaia_tasks_from_json`(`benchmarks/gaia/task.py:286`),103 任务 = Table 5 同集。`GAIATask.level`(`task.py:76`,1/2/3)= **现成三桶 cluster key,零解析成本**;`annotator_metadata`(`task.py:80`)是自由文本勿当域标签。
2. **并发模型**:每任务独立 `model_config.agentic(round_config)` 新建 runtime(`run.py:731`;`harness.py:999`),config 仅按引用共享,已验证安全。改多变体 = 逐任务换 `variant_configs[route(task)]`,**不引入新共享态风险**。⚠️ tracer 是每轮单一 journal(`run.py:697`),多变体须每变体独立 tracer/session 子目录。
3. **成本记账**:per-task cost/tokens 已在 records(`run.py:208-215`),per-variant 只是换 group key,琐碎。
4. **EvolveValidator 链**(`validate_workflow.py:857-870`):build 阶段(canonicalize/replay/contract)与 evidence 门变体无关可复用;**⚠️ novelty 阶段主动敌对**——`check_novelty`(`:694`)按全局 journal 的 `(levers, predicted_affected)` 签名拦 `reverted_signature_reused`(签名 `:688-691`),会误伤 fork 兄弟变体对同 cluster 同 lever 的合法重试。逃生口 `retry_rationale`(`:755`);干净修法 = 每变体独立 journal(W9)。
5. **meta-agent brief**:`MetaAgent` 绑单一 `memo_path`(`run.py:655-664`);brief 的 Pareto 全局约束段(`agent.py:765-773`)要求保护所有 already-passing clusters,变体隔离下须收窄为只保护本变体路由到的 cluster(W11),否则回到 Global 语义。

## Part B — 主施工表

| # | 工作项 | 类型 | 接入点 | 可复用符号 | 需自己发明的设计决策 | LOC | 依赖 | 里程碑 |
|---|---|---|---|---|---|---|---|---|
| W0 | oracle 天花板离线聚合(K 条 Global 谱系事后取并集) | 新建脚本 | 新 `recipe/gaia_evolver/oracle_ceiling.py` | `comparison.json`(`run.py:978`)、各轮 `config.yaml`(`run.py:707-708`) | 变体来源(K seed / 轮 checkpoint);oracle=真值 argmax | 30-80 | 无 | M0 |
| W1 | 变体池状态容器(替换单 `current_config`/`best_so_far`) | 新建模块 | 新 `variant_pool.py`;接 `run.py:633,672` | `HarnessConfig.copy`(`harness.py:929`,浅拷贝独立)、`to_yaml_file` | Variant 结构;K 值 | 120-220 | 无 | M1 |
| W2 | 路由表 + router(argmax success) | 新建模块 | 新 `router.py`;接 `run.py:723` | records `task_id`+`passed`(`run.py:198-215`) | 冷启动路由;cluster 定义(`level`?) | 100-180 | W1 | M1 |
| W3 | success-rate 估计器(per (variant,task/cluster) 跨轮) | 新建 | `router.py`/`estimator.py` | `compute_attribution`(`journal.py:605`)、per-round passed 集(`run.py:779,837`) | 估计口径(裸率/平滑/窗口);陈旧条目处理 | 80-150 | W1 | M1 |
| W4 | 评测环 per-variant scoping(逐任务选变体;候选只跑路由子集) | 改造 | `run.py:723-774` | sem+gather 骨架、`agentic`、`_run_task` | 路由子集 vs 全量刷新的预算权衡 | 100-180 | W1,W2 | M1 |
| W5 | 冲突检测 + fork 触发(聚合门→逐任务分支) | 改造+新建 | 替换 `_score_and_gate`(`run.py:1174-1257`);接 `run.py:802-820` | `flipped`/`regressed`(`journal.py:628-666`) | fork 继承规则;net-worse 判据 | 100-160 | W1,W3 | M1 |
| W6 | 变体退役(池满退最差) | 新建(小) | `variant_pool.py` | 变体聚合 success(W3) | "lowest-performing"口径 | 30-50 | W1,W3 | M1(可设大 K 暂缓) |
| W7 | 聚类器 | 复用/薄新建 | `router.py`;`GAIATask.level`(`task.py:76`) | `level` 现成 | per-task / per-level / 学习聚类 | 0-150 | W2 | M1=0;M2 |
| W8 | 编排重写(抽引擎 + 多变体报表/tracer) | 改造 | 抽 `run.py:683-974`;重写 `print_multiround_comparison`(`run.py:255`);tracer(`run.py:697`) | 循环骨架、`comparison.json` writer | 引擎 API 边界 | 120-220 | W1,W4,W5 | M1 部分;M2 完整 |
| W9 | 按变体隔离 journal/novelty(修 Part A-4 误伤) | 改造 | `run.py:655-664`;`agent.py:511,692`;`check_novelty`(`validate_workflow.py:694`) | 每变体 `learnings_{vid}.md`;`retry_rationale`(`:755`) | novelty 作用域=变体 or 变体+cluster | 50-100 | W1 | M1 |
| W10 | per-variant 成本记账(重桶) | 改造(小) | `run.py:780,1160` | per-task cost(`run.py:208-215`) | 无 | 30-60 | W8 | M1 |
| W11 | brief Pareto 约束收窄到本变体 cluster | 改造(小) | `agent.py:765-773` | brief 其余段 | 隔离下的约束措辞 | 20-40 | W9 | M1 |
| W12 | 多候选 evolver(evolve 一轮吐 {H_t^k},per-candidate fork) | 大改造 | `MetaAgent.evolve`(`agent.py:538-678`);validator 逐候选 | 现单 config 产出路径、build 阶段 validator | 候选枚举协议;Critic 排序 | 300-500 | W5,W8 | M2 |

## Part C — 里程碑分层(熟练 MSc + Claude 辅助,不含算力等待/调参反复)

| 里程碑 | 内容 | 累计 LOC | 累计人时 | 判据/备注 |
|---|---|---|---|---|
| **M0** oracle 天花板 | W0 + 复用单谱系环跑 K 条 baseline(零新代码) | ~30-80 | **4-8h** | headroom = oracle 并集 − 最优单谱系轮。贴近→止损;远大→进 M1 |
| **M1** 最小在线变体池 | W1-W6,W7=0(cluster=task),W8 部分,W9-W11;round-level fork、裸率估计 | ~900-1200 | **~60-100h** | = E0 之后第一个真在线系统 |
| **M2** 忠实复现 | + W7(level/学习聚类)、W12、W5 升级、W6/W8 完整 | ~1700-2100 | **~180-320h** | 能否复现 87.4 仍受欠定件+GAIA 联网噪声制约 |

## Part D — 对首轮评估(Jul-22 上午)的修正

- 🔧 修正1(C7 降本):cluster 非从零发明——`GAIATask.level` 现成三桶,C7 从"发明"降"接线"。
- 🔧 修正2(C8 降本):per-variant 成本记账 = 换 group key,非从零。
- 🔧 修正3(**新增风险**):"EvolveValidator 可直接复用"仅对 build 阶段成立;novelty 阶段与全局 journal 强耦合,对 fork 变体**主动敌对**,必须 W9 隔离。
- 🔧 修正4(C1 降本):`HarnessConfig.copy`(`harness.py:929-942`)对 processors/plugins/_rt_procs 各建独立 list,变体拷贝廉价。
- ✅ 维持:估计口径/冷启动/退役仍欠定且是 87.4 vs 49.5 科学承重件;评测预算×K 与 GAIA 噪声仍是主风险;忠实 fork 需 W12 重构 evolver(repo 每轮只吐一个 config,`agent.py:646`)。

## 科学欠定件(必须自己发明、且 headline 吊在其上)

router 估计口径 / 冷启动 / fork 继承 / 退役指标 / cluster 粒度——论文 §4.5 与 6.3 措辞自相矛盾(cluster 一词在 6.3 消失)。**含义:M2 复现数字无法干净对表;反面 = estimator/router 设计空间可做成带消融的一等贡献(接"修评估洞"立论)。**

## 关键文件索引(clone 内相对路径)

- `recipe/gaia_evolver/run.py` — 演化外环(683)/单 config·best(633,672)/评测环(721-774)/门(1174)/accept-revert(802-820)/evolve 调用(940-965)/comparison.json(978)
- `harnessx/meta_harness/agent.py` — evolve(538-678)/brief(719-812)/compute_changeset(439-485)
- `harnessx/meta_harness/validate_workflow.py` — check_novelty(694)/签名(688-691)/EvolveValidator(857)
- `harnessx/meta_harness/journal.py` — compute_attribution(605)/build_context(366)/per-task 矩阵(559-576)
- `harnessx/meta_harness/replay.py` — 合成任务冒烟门(64-151)
- `harnessx/core/harness.py` — HarnessConfig.copy(929)/canonicalize(944)/Harness.__init__(978-999)
- `benchmarks/gaia/task.py` — GAIATask(60-122)/level(76)/loader(286)
