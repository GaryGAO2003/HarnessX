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
| V-D10 (P-6) | `P-6-noop-patience-flag.patch` | `run_meta_aegis.py` | `--noop-patience N` 旗标（默认 2 = 官方语义字节级不变；调大即不早停） | 用户裁决 2026-08-12"别早停，一直跑"；官方 patience=2 在判决格式彩票下会截断臂 |

**配套的运行期协议偏离（非 vendored 字节，预先声明）**：当前战役（L0/L2_103x10）
若任一臂触发官方早停，立即以 `--start-round <N>` 续跑至满 10 轮（新进程
noop_streak 归零，续跑段代码与原段完全同字节）。这改变的是**删失规则**而非任何
单轮行为——逐轮配对比较不受影响；论文偏差表记一行"early-stop overridden by
operator resume (user decision), official patience=2 preserved in code"。

依据：`experiments/docs/EVOLVER-BURNOUT-PROMPT-AUDIT.md`（输入面清点 + 因果链 +
预期读数）。校验器合同出处：`harnessx/aegis/gates/structure.py:41-61`。

## 发车门裁决记录（2026-08-13，用户）

- **P-3 / P-4 / P-5：批准上车。** 生效仍按本台账流程在发车门统一执行——**在飞
  战役不追溯**（模板每轮从盘上重读，跑批中途 apply 会造成两臂不对称），待
  L0/L2_103x10 收官后 apply + 重钉 + 全阶梯 smoke。
- **P-3 预声明升级梯**（治判决彩票，逐级解锁，届时按新偏离行登记）：
  ① 提示合同（本补丁）→ ② 确定性 sanitizer（只修格式类：锚点行冒号/注释；
  配"救回判决数"计数器）→ ③ 校验错误回喂原 Critic 限一次重发（治语义类
  非法前缀）。禁止第三方 LLM 改写判决——那是代裁。
- **P-6：押后**（用户 08-13"先不急"）；早停由上节预声明续跑协议兜底。
- **P-1：批准上车**（用户 08-13 裁决原话"可以实施，但是明确记录"）。证据基础：
  `experiments/docs/EVIDENCE-P1P2-BUDGET-DRAFT-EARLY.md` P-1 族——方向证据一致
  （MLE-bench/AIDE 有效提交率差 3–38pp、deer-flow 反面印证），**但无任何正面
  消融**，此注记随行进论文偏差表。物理 apply 仍按发车门流程（在飞不追溯）。
- **P-2：终裁上车，载荷不改**（2026-08-13 夜，用户授权代理裁决；四路深研
  完整依据见 `experiments/docs/DECISION-P1P2-NIGHT-RULING.md` +
  `EVIDENCE-P1P2-DEEP-APPENDIX.md`）。预期归因预登记：效应归"收尾余量+成组
  干预"，数字告知按近零贡献变量对待（s1 混合行实证）。**P-2b 裸逐步倒计时
  不建**（离散域因果缺位、8× 系连续时间+单模型挑选、裸可见 agent 域实测
  ≈+2pp）；**预声明 P-2b′ 升级梯**：若 P-1..P-5+L5 打满后烧穿残留 → 阈值式
  两档催促（75%/90%，生产界三处独立实现收敛形状）；再升级走 L5 Status
  主动查询式。届时按新偏离行登记。
- **P-1 深查复核（08-13 夜）：批准维持** + 两条硬性注意事项（判分隔离不变量：
  capability_evidence 自报永不单独承重 ship；"评审自查"类提示预期设近零，
  结构性 checkpoint 才是文献支持形态——L5 写通即结构形态，P-1 为提示级弱
  形态服务 L0–L4 臂）+ 论文定位降级为"待验证假设"（~30 工作零消融）。

## 登记纪律

1. 新偏离 = 先在本表加行（含动机），再施工；一条偏离一个 commit，消息前缀
   `deviation(aegis):`（历史四条保留原前缀，不改写历史）。
2. 补丁文件是偏离的**先行记录**：`git apply --check patches/aegis/P-*.patch`
   随时可验证可应用性；应用后该行从"未生效"表移入"已生效"表并补 commit 号。
3. GHX overlay（`harnessx/graph/**`、`recipe/gaia_evolver/run_meta_aegis_ghx.py`、
   注入器）**不属于本台账**——它们是研究贡献本身，不碰 vendored 字节
   （G3 启动器 + L1 三哈希身份证明 ≡L0 为证），有自己的模块与测试。
