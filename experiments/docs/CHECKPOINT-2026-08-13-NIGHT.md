# CHECKPOINT · 2026-08-13 夜班（用户就寝，代理值守）

## 裁决状态（P 补丁六件全部定案）

| 补丁 | 裁决 | 依据 |
|---|---|---|
| P-1 draft-early | ✅ 批准（用户 08-13 白天）+ 深查复核**维持**（夜，代理）| 诊断硬（MAST 6.2%/SWE-Marathon 39%/SWE-Master 24.3%），解药零消融 → 论文写"待验证假设"；两条注意事项（判分隔离不变量；提示级自查预期近零，L5 写通=结构级形态）|
| P-2 budget-visibility | ✅ 上车，载荷不改（夜，**用户授权代理裁决**）| 效应归因预登记到收尾余量+成组干预；数字告知=近零贡献变量（s1 混合行）|
| P-2b 逐步倒计时 | ❌ 不建 | 离散域因果缺位；8×=连续时间+单模型挑选；裸可见 ≈+2pp |
| P-2b′ 阈值式两档催促 | 📋 预声明升级梯（75%/90%，生产界收敛形状；再升级走 L5 Status 主动查询式）| 烧穿残留时按新偏离行登记 |
| P-3 / P-4 / P-5 | ✅ 批准（用户 08-13 白天）；P-3 带三级升级梯 | — |
| P-6 noop-patience | ⏸ 押后（用户："先不急"）| 早停由预声明续跑协议兜底 |

**物理 apply 全部未执行**：夜间检查确认战役双臂进程在飞（L0 official PID 对 +
L2 ghx PID 对，垫片成对）——按台账"在飞不追溯"铁律（vendored 模板每轮重读，
中途 apply 造成两臂不对称），apply 推迟到双臂收官后的发车门：
`git apply` 逐条 → `deviation(aegis):` commit → cherry-pick → `_regenerate()` 重钉 →
全阶梯 L0→L5 smoke。

## 在飞/挂起的自动化

- **6×3 哨兵**（后台 bash）：每 10 分钟探针 `deepseek/deepseek-chat`，恢复即自动
  点火 `--ghx-level 5 --tasks holdout6.json --num-rounds 3 --max-tasks 0
  --search-backend serper --run-tag L5_holdout6x3`；12h 无恢复放弃。
  日志：`recipe/gaia_evolver/runs/L5_holdout6x3.launch.log`。
- ~~端点故障未解~~ **【08-13 晨更正：这是误诊，根因是代理用错模型名】**
  `deepseek/deepseek-chat`（抄自本文档旧版）在本机 proxy 无健康部署；战役实际
  用 `deepseek-v4-flash`/`deepseek-v4-pro`，两者一直正常。**"在飞战役 rollout
  也在撞死"这句是错的**——战役全程未受影响（L2 已到 R7、L0 已按续跑协议
  `--start-round 8` 重启）。夜班的 L5 smoke rollout 腿空转与哨兵 29 次白探
  （6×3 从未点火，损失约 5 小时）全部由此错误造成。
  已修：BASELINE-L0-STATUS.md 模型名节 + 示例命令；6×3 已用正确模型重发。
  **教训（进运维红线）：点火前必须先探针目标模型名，不得从文档抄了就跑。**

## L5 状态（本会话主线，已收口）

- 建成+两轮审计+修净：commits `1846e5c`/`f25e098`/`0c7e04e`（+docs `9f6decb`/`087e0ed`）。
- 首发 smoke 4.5/5：tool_path 实锤、零载体死、门链留痕、钉绿；rollout 腿因端点
  故障空转——6×3 即完整版补测。
- smoke 标本：Evolver 在任务基底全死时根因了端点故障并提出 ModelHealthRouter
  候选（机器 manifest+真 L1/L2 证据），死于 Critic 判决锚点非法（源码路径变种）
  ——P-3 病灶活标本 + 升级梯方向佐证。

## 两臂物理位置（08-13 晨补记，找它花了若干次探查）

| 臂 | checkout | run 目录 |
|---|---|---|
| L0 官方 | **`D:\PycharmProj\HarnessX-baseline`**（独立克隆，纯 vendored） | `…-baseline\recipe\gaia_evolver\runs\L0_103x10`（+ `.out.log`/`.err.log` 同级） |
| L2 GHX | `D:\PycharmProj\HarnessX`（本 repo） | `recipe\gaia_evolver\runs\L2_103x10` |

在本 repo 里搜 `L0_103x10` 永远搜不到——不是没跑，是不在这个盘位。

## 晨间 TODO（建议顺序）

1. 裁端点：修 litellm 代理 deepseek 组（战役与 6×3 都卡在这）。
2. 查 6×3：哨兵日志 + `runs/L5_holdout6x3/`（若已点火）→ L5 完整 smoke 验尸。
3. 裁战役：双臂是否已收官/需续跑（早停按预声明 `--start-round` 协议）。
4. 双臂收官后：发车门执行六补丁 apply 流程（P-1/2/3/4/5 上、P-6 押后）+ 重钉
   + 全阶梯 smoke。
5. 若推翻夜间任何代理裁决：改台账"发车门裁决记录"节 + DECISION 文件，一条
   commit 说明即可。

## 文档索引

- 终裁：`experiments/docs/DECISION-P1P2-NIGHT-RULING.md`
- 证据附录（四路数字级）：`experiments/docs/EVIDENCE-P1P2-DEEP-APPENDIX.md`
- 初查档案（已被附录部分更正，以附录为准）：`EVIDENCE-P1P2-BUDGET-DRAFT-EARLY.md`
- 台账：`docs/aegis-vendored-deviations.md`（裁决记录节已更新）
- L5 设计/审计/as-built：artifact e438dd46（claude.ai/code/artifact/…）
