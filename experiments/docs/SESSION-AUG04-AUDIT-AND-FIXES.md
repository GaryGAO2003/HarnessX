# Session Aug-04 · e_pervar3 收官审计 + 基建三修 + evolver 批开工

> 范围:e_pervar3(CH3 主跑)收官后的全部审计、由审计引出的三个已落地特性、
> evolver 修复批的设计裁决与实施状态。
> 状态图例:✅ 已落地(有 commit)/ 🔧 实现中 / 📋 已定形待实施 / ⏸ 待用户裁决。
> 本文是索引与结论;逐字证据与过程记录在 RUN-LOG.md 对应节与 SPEC §7.30–7.32。

---

## 1. e_pervar3 收官审计三连 ✅(全录 RUN-LOG "Aug-04" 三节)

| 审计 | 结论 | 关键数字 |
|---|---|---|
| error/warning 全量清点 | **零测量有效性问题**;29 个告警家族全部归类 | 405,927 行日志;429 全被重试吸收(5/5 深度零条);M-37 作用域复点:`active_pool/`=0、`candidate_gate/`=0、`pipeline/candidates/`=50 |
| 空 prompt 硬否决 | 三跑全清("fix holding") | e_pervar3 0/4254、e_pervar 0/1180、e_pervar2 0/701 |
| pool_differentiation | 池分化只到 config 层 | V0/V1 两 config(`57d115ac`/`14c94e8e`),prompt 全程同一(`762e9441`) |

审计顺带定位了两个真缺陷,直接驱动 §2 的前两个特性:

- **serper 静默降级**:`serper` 档缺 key / 空 organic 两条路径静默走原生链,仅异常打 WARNING。
  判别实验:seg4(key 确证在场)从 R6(零候选轮)恢复,13:13:59 即出 Bing 降级警告
  ⇒ key 在场时测量域内也在降级。原生链使用下界:Bing 失败 996 / DDG 1365 / 熔断 22。
- **工具加载器截断**:`file:///D:/…`(canonical 三斜杠)被硬切成 `/D:/…` → `D:\D:/…` Errno 22
  静默死;C-R8-04 的 pdf_fetch 因此从未注册,烧完一轮评测才被 ROUNDTRIP_L2 宣判。

## 2. 已落地特性(三 commit,全在 `fix/novelty-s3`,四表面记账齐)

| commit | 特性 | 语义一句话 | SPEC | 测试 |
|---|---|---|---|---|
| `2d14b6e` | `--search-backend serper_only` | 缺 key 启动即拒;错误重试 3 次;空结果如实返回;**任何路径不碰原生链**(用户裁「原生的没法用」) | §7.30 | 1025→1035 绿 |
| `6cea7bb` | `file://` URI 加载端正规化 | `normalize_file_uri` 收敛全部斜杠拼写,4 个 core 加载点接入;"工具静默不注册"形态从结构上消失(用户裁「一定要保证 agent 能读到东西」) | §7.31 | →1046 绿 |
| `9315e3e` | `--ship-confirmation full_bed` | **窗口=预筛、全床=判决**:窗口 REJECT 零确认成本;APPLY/FORK 必须全床重裁,翻案以 `SHIP_CONFIRM` 归档携双证据;确认 rollouts 独立记账;默认 off 字节等同(用户裁「变体没好好测过,这个才是该修的」) | §7.32 | →1058 绿 |

(注:`8692f3d`/`3df77f1` 两条 novelty/sim 提交系并行的 E1 工作,非本线;
`l_rank_ablation.py` 另有 +56 行 E1-power 段未提交,待用户处置。)

## 3. 设计裁决串(与用户逐条对齐,已录项目记忆)

### 3.1 evolver 三修 ✅(P1–P3,已落地 `30321c0` @ `fix/novelty-s4`,SPEC §7.33;+50 测试,全量 1108 绿)

| # | 病(e_pervar3 实证) | 修(定形) | 旗标 |
|---|---|---|---|
| P1 | 空手 end_turn:11 死、R4 4/4 团灭;retry 每次重开新会话+全额步数,上下文丢、烧 3× | 每 attempt 仍新线程(新想法能换路)+ `NOTES.md` 工作记忆注入下轮(接着自己笔记写)+ **步数一本账**(slot 共享一份 evolve_steps)+ 耗尽自动落显式弃权 ⇒ 团灭形态结构性消失 | `--evolve-continuity` |
| P2 | 白卷(byte-identical copy)是设计内合法弃权,却被记 ValueError 错误、不给理由、planner 无信号 → R12–R13 连环白卷 | 升一等 **ABSTAIN** 结果:单独记账、必须附理由(`ABSTAIN.md`)、理由回流 planner 降权该方向 | `--evolve-abstain` |
| P3 | 提案格式缺陷下游才炸:bucket 缺失 ×2、no-flip ×3、证据缺 ×1 | producer 出口 schema 校验 + 一次**定向重试**(指名缺哪个字段) | `--proposal-repair-retry` |

三旗标默认全 off 字节等同;`continuity on` 依赖 `abstain outcome`、与 commit-bounce 互斥。

### 3.2 P5 撤销(重要负结论,防止再立错项)

"evolver 因果假设命中率低"经两轮证伪**撤销**:①1/25 上船是门控演化正常形态;
②9 次 `improved=[]` 的 seesaw 拒无法与噪声区分(同池零改动摆 8.7pp),且
**大部分由账本定义病解释**(#14b:improved 按全史二值化,V0 可改进池 R9 起七轮=0,
门在数学上不可能承认进步)。实质问题拆成两半:**覆盖**(→ §2 确认门,已修第一刀)
与 **决策层统计病**(→ STATPOOL 整包,见 §4)。

### 3.3 D2 预算分档口径(用户顾虑「总预算少分槽会摊薄」的裁定,录 STATPOOL §12.5)

问题 6 拆两半:(a) **选目标键修复无条件上**(零成本,反事实单独断死锁);
(b) 同轮多目标分槽为预算条件项,由 K_t 分档——紧档 K_t=1–2 时分配规则自动退化为
跨轮轮换(≈零溢价),正常档 K_t=4(1.3–1.5×/轮)。模拟算力对等:总槽砍到 1 仍
+1.0pp/胜率 80%;BASE 的病是 87% 槽位空转花不出去,非缺预算。

## 4. 待用户裁决 ⏸

1. **STATPOOL 四裁决**(STATPOOL-DESIGN.md §12):B 臂范围 / 试用期版本
   (next_round vs PACE)/ 种子数 / 开工时机。推荐:演化侧 only / next_round / n=1 先读结构量 / P1–P3 落地后。
2. `l_rank_ablation.py` +56 行 E1-power 遗留:提交还是丢弃。

## 5. 执行队列(当前)

```
✅ P1–P3 已验收落地(30321c0 @ fix/novelty-s4,SPEC §7.33,1108 绿)
📋 STATPOOL 六步实施(~665 行,等四裁决)
📋 新纪元统一冒烟(serper_only + 确认门 + evolver 批) → 发臂
```

分支布局:`fix/novelty-s3` = 三基建特性 + 本文档(HEAD `113967f`);
`fix/novelty-s4` = s3 全部 + evolver 三修(HEAD `30321c0`)。后续工作在 s4 上继续。

## 6. 锚点索引

- 过程与证据:`experiments/docs/RUN-LOG.md` Aug-04 各节
- 特性规格:`experiments/variant_pool/SPEC.md` §7.30 / §7.31 / §7.32
- 决策层整包:`experiments/docs/STATPOOL-DESIGN.md`(§12.5 为本日新增口径)
- 跨 session 记忆:`~/.claude/projects/D--PycharmProj-MAS-Directions/memory/harnessx-evolver-coverage-aug04.md`
