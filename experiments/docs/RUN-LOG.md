# RUN-LOG — 每次实验跑动一条,当场记录

> 规则(用户令 Jul-26):配置、实测成本(**只认 DeepSeek 余额差**,repo 的 `cost_usd`
> 是按 Claude Sonnet 定价的假账,对 DeepSeek 虚高 ~66×)、判决、发现。
> 本文件从 2026-07-26 建立;更早的 runs(forkprobe/forkprobe_p2/forkprobe_11、
> smoke_fix2、smoke_sub 等)见各自 run 目录与 git 历史,未回填。

## ⚠ 读旧报告的勘误

- **2e78468 之前生成的所有 `pool_report.md`**:headline 行 `attempts / infra / budget
  exhaustion` 三个数口径不一致——attempts 是全程总计,infra/budget **只数末轮**。
  例:smoke_calib6 实际 7 次预算耗尽,headline 报 2。读旧报告的耗尽数一律以逐任务
  行/轨迹 frontmatter 为准。`to_dict/to_json` 的同类不一致**尚未修**(与末轮 rates
  配对,需单独决定),读 JSON 时同样警惕。

---

## 2026-07-26 (branch exp/variant-pool)

### smoke_hard2 — 候选链首次全通
- 配置:hard3.json(3 稳定失败题)/ pool-k 2 / pass@1 / rounds 2 / max-cost **1** / repo 模式 / K_t=1
- 成本:**¥2.26 实测**(194.14→191.88)
- 判决:`candidates evaluated: 1`(历史首次 >0);门首次真实判决 `SEESAW_REGRESSION:
  improved=[] regressed=[04a04a9b]` → REJECT
- 发现:①候选 ID 别名修复端到端验证(candidates.md=`C-0101` 过 repo 正则,内部全程
  `C-R1-01`,SPEC §7.9);②retry 契约生效(1 次重试后产出);③**被判回退是假定价
  饿死**:04a04a9b 第 15/20 步、假 $1.114 触 `--max-cost 1` 被掐,离答案一步——
  拒绝正确但输入被污染。→ 后续 run 一律 max-cost 8。

### smoke_hard3 — 有理由的显式 no-op
- 配置:同上,max-cost **8**
- 成本:未单独测(见会话合计)
- 判决:`explicit_noop`(0 retry),门前无候选——**机制正常,是 meta 的主动判断**
- 发现:①journal `h_noop_r1_media_gap` 论证"两失败=缺模态(GIF/视频),harness 修不了",
  建议未来加视频工具;②max-cost 8 校准验证:失败任务跑满 `steps: 20`,无假定价截杀;
  ③与 hard2 同证据相反结论(hard2:"提示词能修")= DeepSeek 作 meta 的判断随机性,论文
  §7.7 未测面,可作论文观察点。

### smoke_calib6 — 离首次 APPLY 一关之差
- 配置:calib6.json(6 题)/ 其余同 hard3
- 成本:未单独测
- 判决:候选(bucket=[tools,prompt])死于 **ROUNDTRIP_L2**(无 L2 申报)
- 发现:①死因是我方 repo 契约原文"你不需要 capability_evidence"——对工具候选是致命
  误导(→ e79e76d 修正措辞);②账本复盘:该候选 improved={0383a3ee,023e9d44}、
  regressed=∅(ever-solved 仅 {11af4e1a} 且保住)→ **若过第 4 关即首次有机 APPLY**;
  ③方差实锤:同 config R0 1/6 → R1 4/6,零上线;轨迹见 `[SEARCH UNAVAILABLE]`
  (基建噪声,且未被计入 infra_failures——报告口径问题之一);④headline 低报耗尽
  (7→2)→ 2e78468 修正。

### forceprobe1 — 强制 fork 未触发,但三道防线首次实测
- 配置:calib6 / rounds 3 / `--force-gate fork` / K_t=1 / pass@1
- 成本:未单独测
- 判决:fork 未触发(variants [1,1,1])——两轮候选均未达第 5 关,探针按设计不出手
- 发现:①R1 候选(bucket=[processor])再死 ROUNDTRIP_L2——**新 L2 措辞逐字在其
  TASK.md(:89)仍不遵守,n=1 不遵守证据**;②R2 **Critic 组合审计(W16)首次真实
  开火**:R1 主池 04a04a9b 纯方差翻转(零上线),候选未在 tasks_at_risk 处置 → 整轮
  no-op、`ranked_for_gate=[]`——暴露"Critic 考 meta 没被告知的题"接线断路(→ 2e78468
  修:planner_brief 携带 active_regressions);③FORCED 横幅+lock 污点标记验证;
  ④"只改写第 5 关判定"的守卫行为正确。

### forceprobe2 — 进行中
- 配置:calib6 / rounds 3 / `--force-gate fork` / **pass@2** / **K_t=2** / max-cost 8
- 目的:pass@2 削两墙噪声 + 双候选提高第 5 关到达率;若双候选同轮被强制 FORK,
  顺带实测 K=2 容量降级结算路径
- 状态:后台运行中,完成后回填本条

### 会话成本合计(实测)
¥194.14 → ¥183.14(**≈¥11**,含 forceprobe2 已烧部分;终值待其结束后回填)

### 本日提交索引(全部已推 lab)
| commit | 内容 |
|---|---|
| 04a00de | 论文候选管线 C1.5 + 候选 ID 双空间别名(SPEC §7.9) |
| 1cb7d4e | SPEC §7.9 裁决记录 |
| 0d263dd | 架构文档:smoke_hard2 里程碑 |
| 5f96a21 | `--force-gate` 探针开关(默认 off 字节等同;stage-5-only;污点标记) |
| e79e76d | repo 契约:工具/processor 候选必须申报 L2 证据 |
| 2e78468 | 回退情报接线 + headline 全程口径 + journal-isolation fixture(511/0) |
| 16777ab | 移除误入的 graphify 缓存 + .gitignore |
