# 变体池实现蓝图 SPEC

## ⚠️ CORRECTION / ERRATA INDEX（ERRATA_REVISION `2026-07-25`）

> 本节是当前实现语义的权威 addendum。下方原 SPEC 原样保留，记录 Jul-23/24 的设计过程；凡与本节冲突均视为历史版本。论文未规定项统一标为我方工程选择，详见 [`../docs/PAPER-METHODOLOGY-DEVIATIONS.md`](../docs/PAPER-METHODOLOGY-DEVIATIONS.md)。

| Errata | 被取代的历史段落 | 当前权威语义 |
|---|---|---|
| SPEC-E01 | 顶部 `SPEC_VERSION="2026-07-24"` 所代表的旧默认；**§2.4** 伪代码/默认；**§6.6** fork row；**§9.4** `(1,1)` 消融 | fork 默认是 `min_fork=(1,1)`。论文未给计数阈值；旧 `(2,2)` 是我方工程选择，现仅作为 ablation。日志已有 `1+1`、`2+1` mixed conflict，旧 `(2,2)` 直接将其拒绝。 |
| SPEC-E02 | **§2.3 router** 中 routing-induced cluster 作为默认解释；**§6.6 cluster row** | 主路径支持并要求可审计的 `task_id -> cluster_id` 映射来表示真实 cluster；GAIA recipe 暂以 level 为代理。`task_tournament` 是明确命名的兼容/消融模式，不是 cluster 的同义词。routing-induced cluster 只能标作我方备选解释。 |
| SPEC-E03 | **§2.2 ledger、§2.3 router、§6.6 estimator/window/cold-start/tie-break rows** 若被读成论文参数 | 论文只规定“estimated success rate”目标，没有给估计器、窗口、平滑、冷启动或 tie-break。当前全历史 cluster 聚合、确定性冷启动、`fewest_attempts` tie-break 均是可配置的 **我方工程选择**，正式运行必须写入 lock 并消融。 |
| SPEC-E04 | **§2.1 fork/retire、§6.6 inherit/retire、§8.4 C1** 的单序列流程 | 当前引擎使用 two-phase settle：基于 prior-round frozen routing 完成候选评估/裁决，随后统一 reconcile APPLY/FORK/REJECT/retire；同轮结果不得反向改变本轮路由。退役支持 `task_macro`、`cluster_macro`、`raw`，默认 task-macro；这些比较口径均为我方选择。 |
| SPEC-E05 | **§6.7 评测输出契约**中 final/peak 未限定状态边界；**§8.4** 中 gate 结果紧邻报告 | candidate gate diagnostic 与 settled active-pool score 必须分流。active pool 每轮在固定全任务集上评分；只有同配置、同 carrier、完整相同子集时可复用 rollout。REJECT 候选不得进入 final/peak/curve。旧 `forkprobe_p2` 把被拒 R3 候选 `0.6667` 报成 final，已判为报告错误。 |
| SPEC-E06 | **§8.3/§8.4** 尚未出现的候选生产/Critic 完成状态 | CandidatePipeline/Critic 的结构化合约、候选隔离、确定性排序/去重、最多一次 revision 和 audit 已实现并测试。确定性 Critic 是 fallback，不等同论文完整 AEGIS；真实 LLM adapter 尚未接入 live GAIA recipe。 |
| SPEC-E07 | **§9 实验设计**若被误读为已完成 | §9 仍是预注册计划。真实 LLM 端到端、Global vs Ensemble 正式对照、held-out，以及 `103 tasks × 15 rounds × 3 seeds` 均未运行；现有探针没有验证 Ensemble 效果。 |
| SPEC-E08 | 顶部符号说明、**§2.1、§6.6** 中 `K=8` | `K` 仍为论文缺失参数；`K=8` 只是旧我方默认，不得称为论文设置。`K_t=4` 仅是 Table 8 的每轮候选数，不能推出池容量。正式运行必须分别锁定两者并做容量敏感性分析。 |
| SPEC-E09 | **§8.3/§8.4** 按每个 freeze-time variant 调用 evolve/queue；全文对 `K_t=4` 的实现暗示 | Algorithm 1 L15 的 `K_t` 是一个 round 的候选集合。当前 queue limit 是 per target/variant，遍历全池时可达 `4 × active_variant_count`；它不能冒充 round-global `K_t=4`。论文也未规定 target selector。正式主臂仍需“一轮一个预注册 target、全局至多 4 个候选”的 coordinator。 |
| SPEC-E10 | **§2.4** 首个过门者胜出；候选/Critic 的 ship_ranking 描述 | Algorithm 1 L21–25 支持 first-pass single-ship 读法，但 Appendix B.1 p.34 明写按 ship_ranking ship 所有 bucket-disjoint candidates。当前 first-pass-wins 只是按主文作出的工程裁决；Appendix multi-ship 尚未实现，必须单列消融。 |
| SPEC-E11 | **§6.1/§8.3** Digester/CandidatePipeline 完成状态 | Algorithm 1 的 actionability `a_t < α` / empty-landscape selective short-circuit 尚未进入当前合约：Digester 只返回 digests，空 briefs 仍可能继续调用 Evolver。不得声称 selective invocation 已实现；需在 Planner/Evolver 前增加可审计 no-op gate。 |

### 当前规范流程

```text
prior-round evidence
  → freeze routing
  → produce/evaluate candidate(s) on scoped routed tasks
  → gate candidate diagnostics
  → settle + reconcile APPLY/FORK/REJECT/retire
  → score the settled active pool on the fixed full task set
  → publish active-pool final/peak separately from candidate diagnostics
```

这条状态边界是报告正确性的硬约束：候选测量回答“这个候选过门了吗”，active-pool 测量回答“本轮最终部署的组合表现如何”，二者不得混用。

### 验收状态

- **Implemented / tested**：默认 `(1,1)`、真实 cluster API、`task_tournament` 兼容、task/cluster macro retirement、two-phase settle、独立 active-pool scoring、CandidatePipeline/Critic 的结构化/隔离合约，以及当前 per-variant first-pass queue 行为；不含 selective invocation。
- **Integrated but not live-tested**：GAIA recipe 的 level cluster、cluster routing、settled active-pool scorer、错误分类与 provenance lock；当前接线仍可能每个 active variant 各取 queue，不能称为 round-global `K_t=4` 论文主臂。
- **Not yet run / not yet implemented**：actionability/empty-landscape 前置 short-circuit、round-global target selector/`K_t≤4` coordinator、Appendix bucket-disjoint multi-ship 分臂、真实 LLM AEGIS adapters、正式 Global/Ensemble 对照、held-out、各方法学消融及 `103 × 15 × 3`。

> **定位(修正 Jul-23,吸收 Codex 批判):这是"按论文附录重建的、论文启发的跨模型复现(paper-informed re-implementation)",不是"照抄论文"。** 论文未开源(仅承诺未来开源),且我方用 DeepSeek V4 替换了论文的 Opus 4.6 / Sonnet 4.6 / GPT-5.4 内外环模型。⇒ **不得对照论文的绝对分数**;所有未被论文规定的设计选择必须显式记录(§6.6 留白契约表)。冲突处采论文;论文没给的自定并标注,作为贡献面。
> 完整机制依据:`experiments/docs/HarnessX_VariantPool_TheoryFast_Report`(精读报告,全文逐字);工作项表:`experiments/docs/HARNESSX-IMPL-CHECKLIST.md`。
> 论文页码指 arXiv 2606.14249v2。
> **`SPEC_VERSION = "2026-07-24"`** —— §6.6 任一默认值变更时手工 bump;`experiment_lock` 记录本值(§7.6 裁决②)。
>
> ⚠️ **符号钉死:`K`(变体池容量,论文全文未给 → 我方自定,默认 8)≠ `K_t`(每轮 Evolver 候选数 = 4,Table 8)≠ buffer 版本序号 k。三者不可互指。**(Codex 批判第 1 条)

## 0. 分阶段建设(每步一 commit,逐步 review)

| 阶段 | 模块 | 接触 API? | 可单测? | 破坏 repo? |
|---|---|---|---|---|
| **A 离线核心** | `pool` `ledger` `router` `gate` `target` `manifest` | 否 | 全单测 | 否(纯新增,不改 run.py) |
| B 评测层 | `pass_at_k` `scoped_eval` | 是 | 桩 + 小床 | 改 run.py 局部 |
| C 演化环集成 | 引擎抽取 / 多候选 evolver / Critic / 早停 / 回退台账 | 是 | 集成测 | 改 run.py 主体 |

**本 SPEC 详规阶段 A。** B/C 待 A 就位 + M0 基线跑后再细化。阶段 A 全部是纯 Python 逻辑,依赖仅 stdlib + repo 现有(pydantic/pyyaml),**不 import run.py,不改任何现有文件**。

## 1. 包结构

```
experiments/variant_pool/
├── __init__.py
├── SPEC.md                  # 本文件
├── pool.py                  # W1 变体池容器 + W23 slot 复制
├── ledger.py                # W3 成功率账本 + W21 全历史 seesaw 基准
├── router.py                # W2 路由 + W7 簇(主臂)+ 冷启动
├── gate.py                  # W5 三路 fork 门 + fork 最小规模门槛
├── target.py                # W14 目标变体选择
├── manifest.py              # W13 change manifest + W24 Level-2 契约
├── evidence.py              # W25 Digester 数据结构 + 持久证据链(契约见 §6.1)
└── tests/
    ├── test_pool.py
    ├── test_ledger.py
    ├── test_router.py
    ├── test_gate.py
    ├── test_target.py
    ├── test_manifest.py
    └── test_evidence.py
```

## 2. 数据模型与契约(逐模块)

### 2.1 `pool.py` — W1 + W23

```python
@dataclass
class Variant:
    variant_id: str            # "V0","V1",… 单调递增
    config_path: Path          # 该变体的 config.yaml(走 HarnessConfig 的 _target_ 序列化路径)
    journal_path: Path         # W9:每变体独立 learnings_{id}.md(隔离 novelty)
    created_round: int
    parent_id: str | None      # fork 来源;V0 = None
    routed_tasks: set[str]     # 当前路由到本变体的 task_id(= 论文"cluster",主臂读法)
    # W23 slot 复制:变体在 D4(tools)维度分叉时,tool_registry/workspace/sandbox 需独立
    # 记录本变体独占的 slot 资源目录(None = 与父共享,惰性复制)
    slot_dirs: dict[str, Path | None] = field(default_factory=dict)

class VariantPool:
    K: int                     # 容量上限【我方自定】默认 8;必须可配置;记录到实验 config
    variants: dict[str, Variant]
    next_id: int
    def add_root(self, config_path, journal_path) -> Variant   # V0,routed_tasks = 全部
    def fork(self, parent_id: str, improved_tasks: set[str], at_round: int) -> Variant
        # W5 触发:克隆父 config;新变体 routed_tasks = improved_tasks(从父转移)
        # 【我方自定】继承规则:improved_tasks 从父的 routed_tasks 移除,转给新变体
        # W23:若父在 tools 维度有独占 slot,fork 时深拷贝其 slot_dirs
    def is_full(self) -> bool                                  # len(variants) >= K
    def retire(self, variant_id: str) -> set[str]              # W6;返回被退役变体的 routed_tasks(需再分配)
    def reassign(self, orphan_tasks: set[str], router)         # 退役后孤儿任务重路由
```
**论文依据**:§4.5 p.11 "up to K harness variants {H^(1..Vt)} (Vt ≤ K)";fork/retire 逐字见报告 §3.1。
**我方自定**:K 值、fork 的任务继承规则、退役后孤儿任务再分配——论文全空(信息缺口 1/4/5)。

### 2.2 `ledger.py` — W3 + W21

```python
@dataclass
class CellStats:            # 每个 (variant, task) 格
    passes: int             # 累计通过的 rollout 数
    attempts: int           # 累计 rollout 数(pass@2 下每轮 +2)
    last_round: int         # 最近更新轮次(用于窗口/陈旧判定)

class SuccessLedger:
    # S[variant_id][task_id] -> CellStats
    ever_solved: set[str]   # W21:全历史曾被【任一变体】解出的 task_id(seesaw 基准)
    def record(self, variant_id, task_id, n_pass: int, n_att: int, round_idx: int)
        # pass@2:n_att=2,n_pass∈{0,1,2};n_pass>=1 时把 task 加入 ever_solved
    def estimate(self, variant_id, task_id, *, window: int|None = None) -> float
        # Ŝ【我方自定口径】默认:Laplace 平滑 (passes+1)/(attempts+2)
        #   window 非 None 时只算最近 window 轮;陈旧格(未评测)返回先验 0.5
    def is_ever_solved(self, task_id) -> bool          # W21
    def variant_rollup(self, variant_id) -> float      # 变体整体成功率(退役/部署期路由用)
```
**论文依据**:路由查 "highest estimated success rate … across prior rounds"(§4.5);seesaw 基准 "any previously solved task recorded in T_t"(§4.1 p.8,逐字=全历史,非上一轮)。
**我方自定**:估计口径(平滑/窗口/陈旧格处理)——论文三处措辞各异、无估计量(信息缺口 2)。

### 2.3 `router.py` — W2 + W7 + 冷启动

```python
class Router:
    cluster_mode: str        # "routed"(主臂)| "failure"(消融b)| "level"(消融c)
    def cluster_of(self, task_id, pool) -> str
        # 主臂 "routed":返回当前承载该 task 的 variant_id(自举=路由诱导划分)
    def freeze_routing(self, tasks, pool, ledger, round_idx) -> dict[str,str]
        # ⚠️⚠️ 正确性核心(Codex 批判第 5 条):本轮路由必须在【本轮 rollout 之前】一次性冻结,
        #   且只读 ledger 中【<round_idx 的先前轮】状态。严禁用本轮结果反向路由——
        #   否则在线路由退化为变相 oracle,M1 vs M0 对比自欺。
        #   返回 task_id -> variant_id 的冻结映射,本轮全程不变;跑完只更新账本供【下一轮】。
        #   实现须在签名/断言层拒绝访问 round_idx 的 CellStats(last_round >= round_idx 报错)。
    def route(self, task_id, pool, ledger, *, before_round: int) -> str
        # argmax_v ledger.estimate(v, task, before_round=before_round);tie-break 见 §6.6;冷启动见下
    def cold_start(self, task_id, pool) -> str
        # 【我方自定】round 0 或从未见任务:V0(唯一变体)/池内 variant_rollup 最高者
    def explore(self, ...) -> str | None
        # 【我方自定】可选 ε-greedy:防 argmax 冻结失手变体;默认关闭,ε 可配
```
**论文依据**:§4.5 路由句;部署期退化规则 §7.5 p.22 "highest overall success rate on the evolution set"。
**我方自定**:冷启动、探索率、tie-break、簇定义主臂选型——论文全空(信息缺口 3/8/§6.6)。
**⚠️ routing freeze 是不可协商的正确性约束**,不是设计选择——见 §6.2。

### 2.4 `gate.py` — 完整确定性门序列(Codex 批判第 2 条)

⚠️ **修正**:门不是"只做 seesaw / 三路 fork"。论文 §4.3 p.10 的确定性门是**有序五关,首个失败即中止,失败候选归档理由**;变体隔离只把**第五关**从二值改成三路 + 收窄到 T_k。M0 前就要建完整门,不能只建第五关。

```python
class GateStage(Enum):
    MANIFEST_COMPLETE; CANONICALIZE; BUILD_SMOKE_L1; ROUNDTRIP_L2; SEESAW_REGRESSION

@dataclass
class GateResult:
    passed: bool
    failed_stage: GateStage | None
    decision: "Decision | None"      # 仅当走完到第五关才有 APPLY/FORK/REJECT
    archive_reason: str               # 失败候选必须归档理由(§4.3 "archived with rejection reason")

def run_gate(candidate, parent_config, ledger, tk_results, *, min_fork=(2,2)) -> GateResult:
    # 有序,首关失败即 halt + 归档:
    # 1 MANIFEST_COMPLETE : manifest.validate_complete() 无缺字段(W13)
    # 2 CANONICALIZE      : 候选 config canonicalize 成功且规范(复用 harness.py:944)
    # 3 BUILD_SMOKE_L1    : 新 processor/tool 实例化 + 驱动 hook,无异常(复用 replay.py:64)
    # 4 ROUNDTRIP_L2      : 内容经 provider 序列化后存活(W24 check_level2_roundtrip)
    # 5 SEESAW_REGRESSION : 三路 fork 判定(下),基准=全历史已解集(W21)

class Decision(Enum): APPLY; FORK; REJECT
def _seesaw_three_way(tk_results, ledger, *, min_fork=(2,2)) -> Decision:
    # 仅在 T_k(收窄评测,§4.5)上,基于 pass@2 的 before/after 逐 task 分类:
    #   improved:  before 未解(0/2)              → after ≥1/2
    #   regressed: before ∈ ledger.ever_solved     → after 0/2      ← W21 全历史基准
    #   improved≠∅ ∧ regressed=∅  → APPLY
    #   improved≠∅ ∧ regressed≠∅  → FORK(受 min_fork 门槛,见下)
    #   否则                        → REJECT
    # 【我方自定】min_fork=(min_improve,min_regress):防 pass@2 残余噪声撑爆池
```
**论文依据**:五关序列 §4.3 p.10 逐字("manifest completeness → configuration normalization → build/smoke tests → seesaw constraint";"first failing check halts";"archived with rejection reason");Figure 6(c) 图注补 replay/novelty(H6,repo 已有);三路 §4.5 p.11;seesaw 基准 §4.1 p.8;Level-2 prompt p.32。
**我方自定**:fork 最小规模门槛。
**关键**:门是唯一 ship 判据(§4.3 "only deterministic checks govern shipping");Critic 的 ship_ranking **只排序、不放行**——排序后的候选仍须逐个过完整门,第一个过门者胜出(Alg.1 L21-24)。
**C4 裁定(Jul-24,见 §7.8)**:CANONICALIZE(2)/ BUILD_SMOKE_L1(3)两关经调查确认与 `meta_agent.evolve` 内部门(`validate_workflow.py:890`)+ evaluate 期 `_prepare_round_config` 的 canonicalize **完全冗余**,**正式标注为 no-op-by-design,不重复接线**(重跑无新增校验,且会二次 replay timeout——即首次真跑暴露的失败)。保留五关枚举位、`GateResult`/`GATE_SEQUENCE` 结构与注入缝(注入的 `check_canonicalize`/`check_smoke` 仍优先,V1 反 reward-hacking §9.5 可强制失败);gate 实际拦截来自 MANIFEST_COMPLETE / ROUNDTRIP_L2(manifest)/ SEESAW_REGRESSION(恒真)三关。

### 2.5 `target.py` — W14

```python
def select_target_variant(pool, ledger, *, strategy="worst_first") -> str:
    # 【全部我方自定】论文只说 "a candidate targeting variant k",未给选法
    #   "worst_first": variant_rollup 最低的变体(最需要改进)
    #   "round_robin" / "failure_density": 备选,作消融
```
**论文依据**:§4.5 "candidate targeting variant k"(仅措辞,机制空)。信息缺口 6。

### 2.6 `manifest.py` — W13 + W24

```python
class PredictedImpact(BaseModel):
    tasks_will_unlock: list[str]     # ALL_FAIL(0/2)→ expect ≥1 pass
    tasks_will_stabilize: list[str]  # PARTIAL_PASS(1/2)→ expect 2/2   ← 只在 pass@2 下有意义(M17)
    tasks_at_risk: list[str]         # currently ≥1 pass → might regress

class AttributionSignature(BaseModel):
    type: Literal["tool_call","processor_invocation","prompt_feature"]
    tool_name: str | None
    expected_min_calls: int = 1

class ChangeManifest(BaseModel):     # 字段照抄 Table 9 p.36 + iterates_from(p.32,Table9漏)
    candidate_id: str                # "C-R{round}-{NN}"
    bucket: list[str]                # prompt|tools|config|processor
    iterates_from: str | None = None
    capability_evidence: list[dict]  # {type, claim, evidence}
    file_changes: list[dict]         # {path, action, diff_summary}
    predicted_impact: PredictedImpact
    attribution_signature: AttributionSignature | None
    target_variant: str              # 【我方扩展】论文 manifest 无此字段,变体隔离必需(报告 §3.3 ⚠️)
    def to_yaml(self) / from_yaml(cls, text)
    def validate_complete(self) -> list[str]   # 门第一关:manifest 完整性

@dataclass
class Level2Evidence:                # W24;prompt p.32 + C-R10-02 实例 p.37
    survived: bool                   # 内容是否在 provider 序列化后存活
    serialized_len: int
    note: str
def check_level2_roundtrip(tool_output: str, serializer) -> Level2Evidence:
    # 断言 tool 返回在 _prepare_messages 后内容存活(照抄 C-R10-02:"keeps content as N-char string")
```
**论文依据**:manifest Table 9 p.36 + 模板 p.32 + 实例 C-R10-02 p.37;三分类别绑定 pass@2(M17);Level-2 prompt p.32。
**我方扩展**:`target_variant` 字段——论文 schema 无,但变体隔离下候选必须声明目标变体。

## 3. 单测要求(每模块)

- **纯离线**:不起 LLM、不联网、tmp_path 落盘。
- **覆盖**:每个"我方自定"决策至少一个显式测试(平滑口径、冷启动、fork 门槛、退役再分配、seesaw 全历史基准的时序案例:task R3 解出/R5 坏掉/R6 判回退)。
- **论文一致性测试**:三路门的三个分支各一个 fixture,对齐 §4.5 逐字语义;manifest YAML 往返 = C-R10-02 实例可 round-trip。
- 全部 `pytest experiments/variant_pool/tests/ -q` 绿。

## 4. Commit 粒度(体现"每一步都要建立")

每个模块 = 一个 commit,信息含工作项号,例:
`variant-pool: W1+W23 pool container with slot cloning`
`variant-pool: W3+W21 success ledger with full-history seesaw baseline`
…先本地 commit,主循环 review diff 后统一 push lab。

## 5. 边界(阶段 A 不做)

- 不改 `run.py` / `agent.py` / 任何现有文件;
- 不接 HarnessConfig 真实序列化(用 config_path 占位,阶段 C 接);
- 不跑评测(pass@2 逻辑在阶段 B);
- 簇消融 (b)/(c) 只留 `cluster_mode` 接口,不实现算法(阶段 C/M2)。

---

# 6. Codex 批判吸收(2026-07-23)—— 新增契约

Codex 逐条批判成立。本节把其全部硬项契约化;新增工作项 W25–W30,并修正阶段划分。

## 6.1 新增模块:Digester + 持久证据链(W25)—— Codex 批判第 3 条

没有它,Critic / attribution / 可审计全悬空。**升为 M1 必做,不是可选。**

```python
# evidence.py
@dataclass
class TaskDigest:                    # 每 (round, task) 一条,持久化 JSONL
    task_id: str; round_idx: int
    outcome: tuple[int,int]          # pass@2 结果 (n_pass, n_att)
    failure_category: str | None     # 失败簇标签(GAIA: blocked-source/reasoning/…,附录 D.1)
    implicated_components: list[str]  # 涉事 hook/processor/tool id
    evidence_anchors: list[str]      # trajectories/<task>_r0.jsonl#step_N
    prior_history: list[dict]        # 跨轮:该 task 历轮 outcome + 命中的 ship
    variant_id: str                  # ⚠️ 每条 digest 绑定其变体(论文 task_history 无此维度,变体隔离必需)

class EvidenceStore:                 # 对应论文产物目录 data/task_history.jsonl + digests/
    def append_digest(self, d: TaskDigest)
    def cross_round_history(self, task_id, variant_id) -> list[dict]
    def ship_outcomes(self) -> list[dict]         # 每次 ship 的 predicted vs realized
    def rejected_candidates(self) -> list[dict]   # 归档的失败候选 + 理由
```
**论文依据**:Digester 职责 §4.3 p.10(10M→10K 压缩、per-task 摘要、跨轮链接);产物 §E.1 p.43。
**注**:repo 的 `journal.py:compute_attribution` + `digests/` 是半成品,可复用其 flipped/regressed;但**跨变体的证据链要新建**(repo 是单谱系)。⚠️ 论文未公开 Digester prompt(附录 F1),压缩指令须我方重建并记录。

## 6.2 routing freeze — 正确性约束(Codex 批判第 5 条,已入 router 契约 §2.3)

**本轮路由在 rollout 前用先前轮账本一次性冻结,全程不变;跑完只更新账本供下一轮。** 违反 = 变相 oracle。
单测必测:构造"若用本轮结果会改变路由"的 fixture,断言 `freeze_routing` 拒绝访问本轮 CellStats 并给出与先前轮一致的映射。这是 M1 vs M0 分离的命脉。

## 6.3 新增模块:实验元数据 manifest + H0 冻结(W26)—— Codex 批判第 7 条

M0 基线不可解释 = 全盘无意义。**每次 run 落盘一份 `experiment.lock.json`**:
- H0 内容哈希(system prompt + 工具注册表 + 初始 processors);
- 模型/provider 版本(DeepSeek V4-flash/pro 的 model id)、api_base;
- GAIA 数据哈希(103 题 JSON 的 sha256)、分层配比、seed;
- 缓存策略、oracle_ceiling.py 版本(git sha)、SPEC 版本;
- 全部超参(K、K_t、T、P、min_fork、估计口径、cluster_mode、ε、pass@k)。
**H0 一经冻结,同一实验族内不得改动;改动即开新实验族并记新 lock。**

## 6.4 反奖励黑客(主动机制,非仅 threats)—— Codex 批判第 8 条

GAIA exact-match 易被 format/答案泄漏 hack(论文 §6.6 R10 实证)。阶段 B/C 必做:
1. **禁止把 reference answer / verifier 逻辑暴露给 task agent**(repo 轨迹已"刻意不写标准答案",须核验并保持);
2. **工具性改善要求 trace attribution**(W19 attribution_signature 是硬门,非建议):编辑若未在轨迹触发其声明的签名,判为未生效、不计改善;
3. 关键来源二次交叉验证(WikiTextFetch 类工具)——可选;
4. 检出 verifier-format exploit 后可回滚或加 guard(论文 R12 做法)。

## 6.5 Critic portfolio audit(W16 契约细化)—— Codex 批判第 4 条

Critic = Part 1 逐候选 + Part 2 组合审计,二者皆须实现(prompt p.33-34 逐字):
- **Part 1**:每 verdict 必须回答候选 mutation surface 与父 config 的重叠(有意取代 / 意外碰撞),不答记 ask-more;
- **Part 2 三硬规则**:①同 lever 最近 3 轮≥2 次且累计 hit_rate<0.4 → 禁该 lever + 记 strategy_concern;②点名从未触碰而 digests 指向的 bucket/cluster;③本轮 regressions 有回退任务而 Evolver 未处理未解释 → 整轮 no-op;
- **strategy_concern 经 decision.md → 下一轮 Planner → Evolver** 中继(Evolver 只读 landscape);
- **一次最多一个 Evolver revision**(§4.3 "at most one revision cycle")。
⚠️ Critic 只产 ship_ranking,**不放行**——放行只由 §2.4 完整门决定。

## 6.6 留白契约表(Codex 批判第 5 条:写成可执行契约,非仅"贡献面")

每项给:默认值 + 消融范围 + 落在哪个模块 + 必测。原 8 项 + Codex 补充:

| 留白 | 默认(可执行) | 消融范围 | 模块 |
|---|---|---|---|
| K 池容量 | 8 | {4,8,16} | pool |
| Ŝ 估计口径 | Laplace (p+1)/(a+2) | {裸率, EMA, 窗口} | ledger |
| Ŝ 时间窗口 | 全历史 | {全, 最近5轮} | ledger |
| 陈旧格先验 | 0.5 | {0, 0.5, 父继承} | ledger |
| 冷启动 | V0 / rollup 最高 | — | router |
| **路由 tie-break** | attempts 少者优先(鼓励探索) | {随机(seed), attempts少, id小} | router |
| **探索率 ε** | 0(纯 argmax) | {0, 0.1} | router |
| fork 任务继承 | improved 从父转移给新变体 | — | pool |
| fork 最小规模 | (2,2) | {(1,1),(2,2)} | gate |
| 退役度量 | variant_rollup 最低 | {rollup, 近窗} | pool |
| 退役后再分配 | 孤儿任务重路由 | — | pool |
| 目标变体选择 | worst_first | {worst, round_robin, failure_density} | target |
| **idle 作用域** | 全局(对齐 Alg.1 单 idle) | {全局, 逐变体} | 引擎(阶段C) |
| **α Digester 可行动阈值** | 复用 repo 近似 | — | evidence(阶段C) |
| **每变体独立持久化** | config+slots+task_history+route_history+scoreboard+lineage 各自一份 | — | pool/evidence |
| 簇定义 | routed(主臂) | {routed, failure, level} | router |

**每个"默认"都是一个显式实验选择,写入 6.3 的 lock;每个消融范围是一个可跑的对照臂。** 这是贡献面的可执行化。

## 6.7 评测输出契约(Codex 批判第 9 条)

最终报告**必须同时**给(不得只报 peak):
- final 准确率 + peak 准确率 + **每轮曲线**;
- **pass@1 / per-attempt 成功率**(揭示 pass@2 掩盖的概率漂移);
- **按 Level(1/2/3)分层**结果;
- 变体池专属:变体数随轮次、路由命中率、各变体覆盖任务数、fork/retire 事件轴。
新增两条 threats(写入论文):①同 103 题既演化又报 peak = 自适应过拟合,无 held-out;②pass@2 掩盖成功概率下降(Global 崩塌之源)。

## 6.8 成本校准修正(Codex 批判第 10 条)

6 题只够测 token/cache,**估不了能力地板/成功率**。改为:
- **Level 分层小样本(2/3/1 = 6 题,或放大到 8/12/4 = 24 题)**;
- 每次尝试分账记录:input / cached-input / output / 工具调用 / 重试 / **task-agent token 与 meta-agent(AEGIS)token 分开**;
- **每次 harness 大改后重新校准**(Digester 每轮吞 ~10M 原始 trace,才是 M1 成本大头,非 pass@2);
- 产出真实 per-attempt 计费 + 缓存命中率 + flash 通过率是否高于能力地板。

## 6.9 新增工作项汇总(并入施工清单)

| # | 工作项 | 里程碑 | 来源 |
|---|---|---|---|
| W25 | Digester + 持久证据链(evidence.py) | M1 | Codex 3 |
| W26 | 实验元数据 lock + H0 冻结 | **M0** | Codex 7 |
| W27 | 完整确定性门序列(gate 五关) | M1(阶段A建骨架) | Codex 2 |
| W28 | 主动反奖励黑客(禁答案泄漏 + attribution 硬门) | M1 | Codex 8 |
| W29 | 评测输出契约(final/peak/曲线/pass@1/分层) | **M0** | Codex 9 |
| W30 | 成本校准分账 + Level 分层 | **M0**(先于跑批) | Codex 10 |

**阶段划分修正**:W26/W29/W30 提前到 **M0 前置**(基线可解释性 + 输出规范 + 校准),W25/W27/W28 在 M1。routing freeze 已入 router 契约。

---

# 7. SPEC 裁决记录(阶段 A 第二批 review 后,Jul-23)

实现过程中暴露的 5 处 SPEC 歧义,逐条裁决如下。**裁决即契约**,阶段 C 依此实现。

## 7.1 Level-2 证据的归属:两处都要,职责不同

论文两处都有,不是二选一:
- **Evolver 侧必须实跑**(prompt p.32 逐字):"Verify by actually running it -- **not by reasoning about it**";证据贴进 `capability_evidence`;
- **Critic/gate 侧检查声明**(p.37 逐字):"the Critic **verified Level-2 evidence** ... before accepting any tools-bucket candidate"。

**裁决**:保留实现的两强度设计。阶段 C 追加一条——**gate 的 stage 4 对 `tools` / `processor` bucket 应在可行时实时重跑**,而非只信 manifest 声明。理由:Evolver 可能声称验证过而实际没跑,这正是 §6.4 反奖励黑客要防的"声明与事实脱节"。`run_gate` 签名届时需把 probe 接进来。

## 7.2 Level-2 证据缺机器可读标记 → 加我方扩展类型

论文用 `type: other` + 自由文本 claim 承载 Level-2,检测只能靠匹配 "Level 2" 字样,脆弱且易被措辞绕过。
**裁决:采纳建议**,新增 `type: level2_roundtrip` 作为**我方扩展**(与 `target_variant` 并列,同样记入 §6.6)。序列化时保留论文原字段以兼容,但门按类型判定而非字符串匹配。

## 7.3 §6.5 规则 1 的双时间尺度:按论文字面保留

论文 prompt p.33 逐字:"For any lever item shipped in **>=2 of the last 3 rounds** with **cumulative** hit_rate < 0.4"。递归窗口(最近 3 轮)与比率口径(累计)确实不同尺度,后果是"早期很差、近期很好"的 lever 仍会被禁。
**裁决:论文优先,保持字面实现。** 但把"cumulative 是否应加窗口"记入 §6.6 作为可消融项(默认 cumulative,消融 {cumulative, 最近5轮}) —— 这是论文自身的设计,不是我方 bug。

## 7.4 ship outcome 的 schema:确认从 C-R10-02 反推的字段

§6.1 只给了 `ship_outcomes()` 读接口,未定义内容。论文 p.37 的 realization 段给了要素:predicted vs realized flips、hit rate(5/7=0.71)、attribution 是否满足。
**裁决:确认实现的推导**,ship outcome 至少含:`candidate_id` / `buckets` / `round` / `predicted_flips` / `realized_flips` / `regressions` / `attribution_satisfied`。阶段 C 的 Critic portfolio 审计与 Planner 的 `ship_outcomes.json` 依此消费。

## 7.5 多 bucket ship 的 lever 归因:论文空白,记入留白表

复合 ship(如 C-R10-02 的 `[tools, prompt, config]`)的成败该记到哪个 lever 名下,**论文与 SPEC 均未定义**。实现选择"计入全部 bucket",副作用是搭便车的 lever 也承担 ban 风险。
**裁决:默认保留"计入全部"**(更保守 → 更早触发 ban → 更早强制探索新 lever),并记入 §6.6:

| 留白 | 默认 | 消融范围 | 模块 |
|---|---|---|---|
| **多 bucket ship 的 lever 归因** | 计入全部 bucket | {全部, 仅主 bucket, 按 file_changes 加权} | evidence |
| **hit_rate 比率口径**(§7.3) | cumulative(论文字面) | {cumulative, 最近5轮} | evidence |
| **Level-2 证据类型**(§7.2) | `level2_roundtrip`(我方扩展) | — | manifest |

## 7.6 M0 前置批次的六处裁决(Jul-24)

**① 分层配额:超配 level 3 保留,但外推必须按真实比例加权。**
§6.8 的 24 题 = 8/12/4 确实不等于论文的 39/52/12(按最大余数法应为 9/12/3)。实现的 docstring 给了一个我起初忽略的正当理由:**level 3 按比例只有 3 题,这一层太小,得不出任何结论**——而校准恰恰要看难度对成本与通过率的影响。
但超配有代价:level 3 任务更长更贵,**直接用样本均值乘全规模会系统性高估成本**。
**裁决:两个目的分开处理,不二选一**——
- **取样**:保留 8/12/4(超配 level 3,让分层通过率有意义);6 题 = 2/3/1 恰好等于比例,不变;
- **外推**:`project()` 必须**按论文 39/52/12 对各层成本加权**,而非对全样本取均值。
  **已核实现状**:`project()` 目前走 `per_attempt_billed()` = 全样本均值,**未按层加权**。
  影响范围:**6 题校准不受影响**(2/3/1 恰等于比例,均值即加权均值);**24 题会高估**——样本 level 3 占 16.7% 而全集占 11.7%(超配 1.43×),若 level 3 单题成本显著更高,外推随之偏高。
  ⇒ 列为待修(需 `AttemptCost` 携带 level,`project` 按层聚合再加权)。在此之前 **24 题外推的成本数字不得直接引用**,只可用于分层通过率与缓存命中率。
- 两个配额与加权口径都写进 `experiment_lock`,使外推可复核。

**② SPEC 加版本字段。** 采纳建议:本文件顶部 `SPEC_VERSION` 见下,§6.6 任一默认值变更时手工 bump,lock 记录它。

**③ 两个 seed 是对的,不是一个。** §6.3 只写了 "seed",但设计需要两类:per-run 的 RNG seed(router tie-break 用)与 family 的三个 lineage seed(Table 8 的 "seeds=3")。**裁决:确认拆分**为 `env.seed` 与 `hyperparams.seeds`,两者均**不**参与 family 判定。

**④ routing hit rate 的定义,以及它为何分属两个里程碑。** **裁决:采纳实现的读法**——"被路由的评测中,路由到的变体确实解出该任务的比例",无路由时返回 `None`(M0 无路由器,返回 0.0 会被误读成"路由全错")。
另一种读法("路由到的是否为当时最优变体")需要反事实数据:得知道**其他**变体在该任务上的表现,而变体隔离恰恰不评测它们(§4.5 收窄评测)。⇒ **M1 在线路由拿不到这个量;但 M0 拿得到**,因为 M0 全量评测所有 checkpoint。故:M1 报"路由命中率",M0 报"oracle 后悔值"(路由选择与事后最优的差),两者不可互相替代。

**⑤ headline 指标算最后一轮。** **裁决:确认**。跨轮池化会把一个 harness 与它自己的早期版本混在一起;`final()` / `peak()` 从同一条曲线导出,保证 §6.7 的"final 与 peak 必须成对报"落在同一口径上。

**⑥ 精读报告须随仓自包含。** SPEC 前言与多个模块 docstring 引用 `experiments/docs/HarnessX_VariantPool_TheoryFast_Report`,但该文件只存在于 MAS_Directions 项目,**不在本仓**——导致 coder 无法核验 A.3 公式 6 的形状与 A.4 的 H0 措辞。**裁决:把精读报告复制进本仓 `experiments/docs/`**,使论文依据链在仓内闭合。

## 8. 阶段 C 架构(Jul-24,用户裁定直接建 M1 fork)

**用户裁定:跳过独立 M0,直接建变体池(fork 互补)。** 对照不因此丢失——反而更干净。

### 8.1 K=1 即 Global,对照内建
变体池容量 `K=1` 时退化为单谱系,等价于论文 Global 臂。故**同一份变体池 recipe 跑 `--pool-k 1` 与 `--pool-k 8`,即 Table 5 的 Global vs Ensemble**,同代码路径、同数据、同参数,唯一变量是变体数。比独立 M0(用原 `run.py` 跑 Global,跨代码路径)可比性更强。

### 8.2 平行 recipe,不改 run.py 主体
阶段 C **不重构** `run.py` 的内联主循环(会破坏已能跑的基线,且 pass@2 刚改过)。新写平行 recipe `recipe/gaia_evolver/run_variant_pool.py`,复用 run.py 的零件(`_run_task_pass_k`、`meta_agent.evolve`、gate 的 canonicalize/smoke),用变体池引擎编排。这是 repo 既有模式(`run.py` / `run_meta.py` 本就平行)。

### 8.3 分解
| 批次 | 模块 | 接触 API | 破坏 repo |
|---|---|---|---|
| **C1** 演化引擎 | 新 `variant_pool/engine.py`:路由→逐变体评测→门→fork/apply/retire→更新账本→下轮;评测与 evolve 为**注入回调** | 否(全离线单测) | 否 |
| C2 真实接线 | 新 `recipe/gaia_evolver/run_variant_pool.py`;`--pool-k`(1=单谱系回归) | 是(小烟雾) | 否(新增文件) |
| C3 逐变体 journal 隔离(W9) | 每变体 `learnings_{id}.md`,novelty 按变体作用域 | 否 | 局部 |
| C4 门两关去向裁定(W27) | 调查 evolve 内部门 vs gate 两关 → 裁定 canonicalize(`harness.py:944`)/ smoke(`replay.py:64`)**冗余**,正式标注 no-op-by-design,不重复接线(§7.8) | 否 | 否 |

### 8.4 C1 引擎契约(本批)
`VariantPoolEngine` 编排一轮 = 论文 Algorithm 1 + §4.5 变体隔离:
1. **路由冻结**:`router.freeze_routing(tasks, pool, ledger, round_idx)` —— rollout 前,只读先前轮账本(§6.2 正确性核心)。
2. **逐变体评测**(注入回调 `evaluate(variant, tasks) -> {task: (n_pass,n_att)}`):候选只在 `T_k` 上评测(§4.5 收窄)。
3. **evolve**(注入回调 `evolve(variant) -> candidate`):产候选(C1 用桩;C2 接 `meta_agent.evolve`)。
4. **门**:`run_gate(candidate, ..., tk_results)` → APPLY / FORK / REJECT。
5. **fork/retire**:FORK 时 `pool.fork`;池满 `pool.retire` + `pool.reassign`。
6. **更新账本**:`ledger.record`(供**下一轮**路由;本轮不得回读——freeze 已强制)。
7. **早停**:`idle >= P`(P=3);idle 作用域=全局(§6.6 默认,对齐 Alg.1 单 idle)。
8. 每轮落 per-variant 报表(reporting.py)与 EvidenceStore digest。

**K=1 回归**:池只有 V0,路由恒返回 V0,永不 fork,行为等同单谱系。C1 必须有一个 `--pool-k 1` 等价的测试证明这一点。

## 9. 实验设计(冻结基线,吸收对抗审查 Jul-24)

Gate 式:前一层不过就止损,不进下一层。K 值由 P1 反推,不盲定。evolver(单候选/多候选+Critic)待用户裁定。

### 9.0 预注册主假设(防 HARKing / 多重比较污染)
- **H1(唯一确认性主假设)**:McNemar **配对**检验(两臂同 103 题),`Ensemble_final > Global_final`,单尾,**Δ ≥ 10pp**,α=0.05。分析单元 = **task-level 配对**。
- **H2(次)**:TOST 等价检验,`Ensemble (peak − final)` 落在 ±5% 噪声带内(证明不退化)。
- 其余全部 **exploratory**,BH 校正或明确不作推断。seed-level 只报粗方差带,**不作主推断**(3 seed 估不出可靠方差)。

### 9.1 阶段 0 — 探针(必须最先跑,任一不过止损)
| # | 配置 | 判据 | ~成本 |
|---|---|---|---|
| P1 fork 触发 | K=4 / 20题分层 / 6轮 | fork 触发数次;测触发率(反推可行 K)与方差 | $5 |
| P2 Global 崩塌 | K=1 / 40题 / **12+轮** | Global **final < peak** 超 ±5% 噪声带 | $40 |
P1 不触发=变体池名存实亡;P2 不崩=Ensemble 无东西可赢,主对比空转。

### 9.2 阶段 1 — 主对比(3 臂 × 3 seed)
| 臂 | 配置 | 作用 |
|---|---|---|
| M-A K=1 Global | 103题/15轮/pass@2 ×3seed | baseline |
| M-B K=K* fork-on-conflict | 同上 | 论文机制 |
| **M-C K=K* 非-fork portfolio** | 同上 | **隔离"机制 vs 集成红利"** —— 缺此臂,M-B 赢会被判"只是 ensemble" |
+ **held-out**:20 题不参与演化,最终池两种路由(按簇/塌缩单变体)各评(复现论文 §7.5 未测的部署塌缩)。
成本:9 run ≈ **$400-700** + held-out $25-40。

### 9.3 阶段 2 — 诊断
**免费**(阶段1日志算):路由命中率 / pass@1 / per-attempt / level分层 / 种子方差 / **实现K vs 名义K**(按实现K分组)/ **变体多样性指标**(config diff + 路由重叠)/ infra失败率跨臂平衡。
**付费**:oracle headroom(需全网格 K×103,scoped只测对角线)= **$70-150**。

### 9.4 阶段 3 — 消融(exploratory,只描述不推断)
n=40 欠功效(只检出17-20pp)+ 有状态演化轨迹分叉不可比 → **禁写"X优于Y"**。
K{4,8}(**删K=16**:破碎)/ 簇{routed/failure/level}/ 口径{裸率/Laplace/EMA}/ 门槛{(1,1)/(2,2)};每臂≥2seed。~**$130-300**。

### 9.5 阶段 4 — 验证性($5 each)
- V1 反 reward-hacking 阳性:植入已知 format-exploit,确认门拒绝。
- V2 pass@2 独立性:确认两次 rollout 不被 prefix cache 拖成相关。

### 9.6 横切原则
- **token 分账**:匹配 task-agent rollout 预算,meta-token 作独立效率轴**单独报不匹配**(论文效率优势正是"不matched反而更省")。
- **成本总账(修正后)**:最小可发表(探针+主对比+免费诊断+held-out)~**$500-800**;完整 ~**$700-1200**。此前 $115 是漏算机制分离臂/K=8增量/held-out/headroom,错近10×。

## 10. 原体改动清单(upstream 污染台账,用户裁定 Jul-24:全部可配置、默认=原体)

**原则**:变体池的全部逻辑(~12,900 行)在**新增文件**里,零污染原体。对 upstream 原有文件的改动只有下表三处,**每处都默认关闭或只在非 Claude 路径生效**——用原论文配置(Claude + `--pass-k 1` 缺省)跑,原体行为逐字节不变。

| # | 文件 | 改动 | 性质 | 如何关回原样 |
|---|---|---|---|---|
| 1 | `recipe/gaia_evolver/run.py` | pass@k 双 rollout(~430 行:`_rollout_once` / `_run_task_pass_k` / `_merge_attempt_records` / `_round_pass_rate` + `--pass-k`) | 功能(论文 §6.1 pass@2,repo 未实现) | **`--pass-k 1`(默认)= 单次评测,与 pre-pass@k 逐字节不可区分**(`run.py:179` 自证)。传 2 才启用论文 pass@2 |
| 2 | `harnessx/providers/litellm_provider.py` | cache token 提取(~30 行:`_cache_read_tokens` / `_cache_write_tokens`) | **Bug 修复**(补 upstream 疏漏:anthropic/responses provider 都读了 cache 字段,litellm 漏了) | 只影响**走 litellm 路径**的模型(DeepSeek/OpenAI…);**Claude 走 `anthropic_provider`,完全不碰此处**。读 cache 字段对任何 provider 都正确,不改变行为,只修正成本核算 |
| 3 | `harnessx/providers/litellm_provider.py` | 空 content 发 `""` 而非 `null`(~3 行) | **Bug 修复**(DeepSeek 反序列化器拒收 `content: null`,长任务全崩) | 同上,只走 litellm 路径;`""` 对 OpenAI/DeepSeek 都合法,Claude 路径不受影响 |

| 4 | `benchmarks/gaia/prompts/gaia_agent.j2` | +6 行守则(Step Budget 节:`{{ max_steps \| default(20) }}` 硬预算+第 N−2 步强制交底;Tool availability 条:勿假设 yt-dlp/whisper/ffmpeg 存在、失败即转向;no-progress 条:连续 2 次无新信息=换策略) | **H0 基线变更**(用户明令 Jul-26"加 guardrail,不要大改";文献依据=researcher 证据表:BATS 2511.17006 / s1 2501.19393 / StressWeb 2604.16385 / smolagents 逐字部署等) | 恢复 `gaia_agent.j2.h0-original`(字节级备份,与补丁同 commit 入库)。**非默认关闭**——是经批准的基线变更;可比性边界记于 RUN-LOG(≤forceprobe2 为原版) |
| 5 | `harnessx/meta_harness/agent.py` | replay 冒烟硬帽从写死 `min(x, 20.0)` 改为 `min(x, _replay_timeout_cap_s())`——环境变量 `HARNESSX_REPLAY_TIMEOUT_CAP_S` 门控,默认 20s 字节等同 | **Bug 修复级**(参数 `replay_timeout_s=300` 被静默钳到 20;对 DeepSeek 思考模式=延迟彩票,runs/paper2 成形候选恰死于 20.0s;影响两种 manifest 模式的所有 evolve) | 不设环境变量 = 原行为逐字节不变;测试 `test_replay_cap.py` 钉死默认与回退 |

**判定**:#1 是唯一的"功能"改动,已做成默认关闭的开关(`--pass-k`);#2/#3 是让 upstream 在非 Claude 模型上不出错的修复,**不影响论文原配置(Claude)的行为**;#4 是**用户明令的 H0 基线变更**(唯一非默认关闭项),字节级备份与可比性边界在案。⇒ 除 #4(经批准)外,"原体默认行为 = 原版"仍满足。
新增文件(平行 recipe + experiments 包)对 upstream 零侵入,可随时 `git checkout origin/main -- <原体文件>` 完全复原。

## 7.7 实现中确立、值得保留的三条设计原则

review 中确认的判断,记录以免后续被改坏:
1. **宽松解析,严格门控**:`ChangeManifest` 所有字段带默认值,不完整的 manifest 仍能解析,由 `validate_complete()` 判定 —— 否则是 pydantic 而非确定性门在决定什么能 ship,违背 §4.3 "only deterministic checks govern shipping"。唯一例外是 `extra="forbid"`(未知键 = 臆造 schema,属解析错误)。
2. **hit-rate 池化而非平均**:`sum(hits)/sum(predicted)` 跨 ship 汇总;平均会让一个走运的 1 任务 ship 抵消一个失败的 5 任务 ship。分母为 0 返回 `None` 而非 0.0,禁用规则拒绝在未定义比率上触发。
3. **pass@2 语义忠实**:2/2 掉到 1/2 **不算回退**(仍 solved),这正是论文 §7.1 所述 pass@2 掩盖亚阈值退化的机制;实现不得"顺手修好"它——那会偏离论文并使 Global 臂的崩塌不可复现。

## 7.8 C4 门两关(CANONICALIZE / BUILD_SMOKE_L1)去向:裁定为冗余,标注 no-op-by-design(Jul-24)

C4 首次真跑时,evolve 内部 replay 门 timeout 抛异常,被 `run_variant_pool._evolve` 的 guard 接住当"本轮无候选"。这暗示:候选能到达我方 gate,是否说明它已过 evolve 内部的 canonicalize + replay 门?**调查结论:是,且两关与 gate 完全冗余。**

**① evolve 内部门保证什么。** `meta_agent.evolve`(`agent.py:538`)在 agent turn 后调 `EvolveValidator.run`(`validate_workflow.py:890`),按序跑 validity 相:canonicalize(`harness.py:944` 的 `HarnessConfig.from_yaml_file(...).canonicalize()`)→ contract → replay(synthetic smoke,`replay.py:64`,经 `run_replay_gate_strict:200` 包装)→ 再 policy 相(changeset 非空时 novelty/evidence);**首个失败即 `raise RuntimeError`**。`evolve` 仅在 `validator.run` 无异常返回后才交出 `output_dir/config.yaml`。⇒ **evolve 成功返回 ⟺ 返回的 config 已 canonicalize 通过 + replay smoke 通过**(非空 changeset 另含 novelty/evidence)。今日的 timeout 正是这条 replay 门在 DeepSeek 上触发(论文的强模型未触发),行为符合设计。

**② 三条到达 gate 的候选路径**(`run_variant_pool._evolve`):
- **演化候选**(后续轮真实改动)= `meta_agent.evolve` 产物,**已过 evolve 门**(否则 raise → guard 兜住返回 `None`,根本到不了 gate);
- **baseline 候选**(round-0 或变体首现,`is_baseline=True`)= 变体自身冻结的 H0 config(`V0/config.yaml`,由 `make_gaia_builder_gpt5().build()` 序列化),**未经 `meta_agent.evolve`**;
- **byte-identical no-op** → 返回 `None`,不到 gate。

**③ fork 候选不是新配置。** `engine._fork` 里 `pool.fork` 克隆的是**父**config,但该克隆立即被 recipe 的 `_reconcile` 覆盖:`child.config_path = Path(cand.config_path)`,其中 `cand` 就是刚过门的同一 `PoolCandidate`。⇒ **fork 只是把已过门的候选分给新变体,不产生绕过门的新 config**,故 fork 候选与被 gate 看到的候选门态相同。

**④ baseline 候选也已 canonicalize——被 recipe 自己的 evaluate 步。** `VariantPoolEngine.run_round` 的次序是 evaluate(步 2)→ gate(步 3)。recipe 的 `_evaluate`→`_run_evaluation` 先调 `_prepare_round_config` = `HarnessConfig.from_yaml_file(config_path).canonicalize()`(**未套 try/except**,失败即 raise,候选到不了 gate),再用真实 harness 全量跑 rollout(实例化并驱动全部 processor/tool,比 synthetic smoke 更强)。⇒ **任何候选到达 gate 时其 config 必已 canonicalize 通过**(演化/fork 由 evolve 保证,baseline 由 evaluate 保证);smoke 由 evolve 的 synthetic replay(演化/fork)或 evaluate 的真实 rollout(全部)覆盖。**不存在同时绕过 evolve 门与 evaluate 期 canonicalize 的候选路径。**

**⑤ 裁定:gate 的 CANONICALIZE / BUILD_SMOKE_L1 = 冗余双重检查,正式标注为 no-op-by-design,不重复接线。** 理由:(a) 无候选路径绕过上游两道保证;(b) 重跑 canonicalize 零新增校验;(c) 重跑 replay 会二次 timeout——正是今日暴露的失败。**保留**五关枚举位、`GateResult`/`GATE_SEQUENCE` 结构、以及注入缝(`check_canonicalize`/`check_smoke` 参数)——注入的检查仍优先执行并可 halt,故 V1 反 reward-hacking 探针(§9.5)仍可强制失败;但 gate **不接内建 canonicalize/smoke 检查**(代码里这两关本就没有内建实现,仅 stage 1/4 对 manifest 有内建默认)。gate 的实际拦截来自三关:**MANIFEST_COMPLETE**(candidate 为 manifest 时)、**ROUNDTRIP_L2**(manifest 且 code bucket)、**SEESAW_REGRESSION**(恒真)。

**落点**:`gate.py` 模块 docstring "Which stages are real" 段与 `run_gate` 内联注释已改写标注;`run_variant_pool.PoolCandidate` docstring 的 "batch C4" 前向引用已更新;测试见 `tests/test_gate_c4_redundancy.py`(锁定两关 no-op + 三关拦截 + 结构不变)。

## 7.9 候选 ID 双空间:内部 paper 形,repo 边界用数字别名(Jul-26,用户裁定选项 1)

**冲突**:论文 Table 9 的候选 ID 形如 `C-R3-01`(我方 `manifest.CANDIDATE_ID_RE` 亦如此校验);但 repo 自己的 evidence 门(`validate_workflow.py:1153`)只认 `^##\s+Candidate\s+(C-\d+)\b`——`C-` 后**纯数字**。smoke_hard 中 meta-agent 按我方契约写下 `## Candidate C-R1-01`,被它自己的 repo 当场拒收,候选永远到不了门。

**裁定(用户选选项 1)**:契约交给 meta-agent 的是 repo 兼容 ID,映射由我方维护。实现为**双 ID 空间 + 单点翻译**:

- **内部一律 paper 形**(`C-R<round>-<NN>`):slot 分配器、manifest、gate、ledger、报告、审计全部不变;`CANDIDATE_ID_RE` 与 round 前缀校验保持论文严格性。
- **外部别名**只在两个缝出现(`candidate_pipeline.outward_candidate_id`,纯字符串映射 `C-R1-01 → C-0101`,revision 追加两位序号):
  1. `_build_candidate_contract` 把别名写进契约(TASK.md 的 "use exactly this value"),meta-agent 的 candidates.md 标题因此过 repo 正则;
  2. manifest 收口(`_finalize_slot`)把回声的别名映射回 slot ID,别名本身存 `meta["repo_candidate_id"]` 供审计。
- **否决的替代方案**:全内部改用数字形(会撞 `CANDIDATE_ID_RE`、打乱 `_brief_for_slot` 的位序解析、污染全部 C-R 形文档/fixture);在渲染层翻译(契约 dict 将与 meta 实际所见不一致,审计失真)。
- legacy 路径不注入契约(repo 原生 TASK.md),其 ID 纯内部记账,维持 `C-R{round}-{vid}` 原样。

## 7.10 `--force-gate` 管道探针(Jul-26,应用户"强行做一个变体验证链路"之令)

**目的**:APPLY/FORK 结算链(`engine._settle_round`:pool.fork、journal 继承、双变体路由、退役)从未被真实数据触发;探针在真数据上强制走通。**设计**:recipe 层包装器经引擎现成注入缝(`VariantPoolEngine(gate=...)`)注入,真门照常全跑、审计全留,仅改写**最终判定**;`off`(默认)= 返回原 `run_gate` 同一对象,字节等同。**三条硬规**:①只改写**已到达第 5 关**(`decision is not None`)的结果——前四关完整性失败永不放行(探针不送垃圾过门);②强制 FORK 且真实 improved 为空时,从本轮评测失败任务合成子变体任务集,`archive_reason` 里 `synthesized_improved=` 可审计;③强制轮次带双污点标记(pool_report 首行横幅 + experiment.lock `provenance_warnings`),**永远不算测量**。落点:`run_variant_pool.py`(5f96a21),14 个离线测试。forceprobe1 实测:守卫①行为正确(两轮候选均死于前四关,探针未出手)。

## 7.11 L2 证据机制:乙+甲(Jul-26,用户拍板"可以")

**问题**:repo journal 无 capability_evidence 槽位;契约明文要求后 DeepSeek meta 仍不写(forceprobe1 R1,措辞逐字在 TASK.md:89,n=1 不遵守)→ 工具/processor 候选全死第 4 关,Action 杠杆瘫痪(媒体簇唯一修法)。**裁决**:
- **甲(保留)**:契约措辞不动(e79e76d);meta 亲笔证据优先采用——顺带产出"亲笔率"作论文数据点(§7.7 未测面);
- **乙(新增兜底,repo 模式 + tools 桶)**:判门时经门预留 `check_roundtrip` 注入缝,取**该候选评测轨迹中新工具的真实输出**,过 provider 真序列化器(`check_level2_roundtrip` + `_prepare_messages`),机器生成证据,记入 candidate meta 并标 **OURS**;
- **边界**:新工具在候选评测中从未被调用 → 无证据可取,**诚实拒**(与 attribution_signature 的 `expected_min_calls≥1` 同精神);processor 桶 v1 维持"必须亲笔申报"(轨迹取证语义未定,不装覆盖);paper 模式一字不动(忠实臂);
- **否决案**:丙 retry 再教(费 meta 调用且无保证,留作论文期补充)、丁放宽第 4 关(=软化判据,墓碑老路)、戊禁工具候选(砍 Action 杠杆,砍掉与 C-R10-02 对话资格)。
- 实施时记偏差台账 **M-22**;论文叙事:"证据优先由 Evolver 提供;缺失时由 recipe 对真实运行产物执行同一探针并标注机器来源"——比口头声明更符 p.32 反"I believe this will work"之精神。
- **v2 复核判词(Jul-28 晨,主循环)**:processor 桶的 replay-execution 认证(夜间 OURS-v2)**成立并保留**,但限定如实记录——它证明的是**执行见证**(processor 在真实循环中运行且未致崩溃),弱于 tools 的序列化存活探针,**不证明其输出传导至下一管道级**;正式跑的 lock 须携带此弱证据注记,v2 开/关消融保留。理由:替代方案(仅认亲笔申报)已实测造成 100% 墙(meta 从不申报),而执行见证是非平凡的真实证据。

## 7.12 回退情报接线(Jul-26)

forceprobe1 R2 暴露断路:`context.regressions` 只流入 Critic,meta 全然不知,却因"未解释回退"被整轮否决(W16 考没发的题)。**修**(2e78468):`planner_brief` 非空回退时携带 `active_regressions` 清单 + 引 Critic 原话的硬性要求;空时字节稳定。Critic 判据未动——修的是信息流,不是纪律。

## 7.13 worker 选型:Flash(thinking/high),不上 Pro(Jul-26,用户令案头裁定、免对照实验)

证据与全表=`experiments/docs/MODEL-SELECTION-FLASH-VS-PRO.md`。五条理由:失败余量(Pro 在 BrowseComp 近天花板 83.4,压平效应窗;Flash 53-73 居中,合本地先验 33-67%)/每任务成本 ≈2.4×/失败签名有利(工具机制干净、败在实质——隔离 harness 效应的好失败模式)/唯一文档化硬伤(Non-think 长上下文崩塌)已避开(API `thinking` 默认 enabled、effort 默认 high,轨迹含 Thinking 块亲验)/无公开数据的两风险(格式依从、run 间方差)留 pilot 实测。冻结动作:lock 显式传 `thinking=enabled`,勿赖服务端默认;Pro 留作 meta 与论文期对照。

## 7.14 `--ship-policy`:结算语义双读(M-17,Jul-27,App B.1 排名多发 vs Algorithm 1 单发)

**背景**:论文内部两套结算语义(digest 冲突 #7/H1):Algorithm 1(p.9)每轮单 ship,App B.1(p.34)"ships every listed candidate in ranked order, skipping any whose bucket is already claimed this round"。旧引擎按 Algorithm 1 读(M-17 声明偏差)。本节把 App B.1 作为可切换臂接入(`--ship-policy {first_wins,bucket_disjoint}`,默认 `first_wins`,身份钉住法同 §7.10/§7.11——默认字节等同、lock 不变)。

**Phase-1 可行性裁定(进论文,file:line 证据)**:候选是**完整 `config.yaml`**(`candidate_pipeline.py:544` `CandidateArtifact.config_path`),非 diff。同变体二次 APPLY 的合并**不可靠构造**,证据:①仓库无 apply-changeset/overlay 原语——`compute_changeset`(`harnessx/meta_harness/agent.py:446`)只**产** diff 不**施**,唯一 `_merge_into`(`harnessx/core/builder.py:353`)是**构造期** `HarnessBuilder` 合并(同名工具即 `HarnessConflictError`),不作用于序列化整配置;②按桶的字段归属**不可分**——`HarnessConfig`(`harnessx/core/harness.py:756-779`)无 prompt/template 字段,模板藏在 `processors[].template_path`(`agent.py:405-427`),故 manifest 桶 `prompt` 与 `processor` **共用同一 `processors` 列表**;③`compute_changeset`(`agent.py:459-492`)根本不 diff 顶层标量,`config` 桶在 changeset 里**无表示**,故"canonicalize-verified"合并对 config 桶失明;④桶是 LLM **自声明**(`manifest.py:272`),未经验证界定实际改动面,"桶不相交"≠"字段改动不相交";⑤离线引擎 `_apply_candidate` 是 batch-C no-op 缝(`engine.py`,SPEC §5),无 HarnessConfig loader,且 a1big1 在跑不可实机验证。**⇒ 结论=App B.1 对整配置候选欠定义,同变体合并本身是交付物,不硬造。**

**实现语义(`bucket_disjoint`,只做 sound 的)**:`engine.py` 得 `ship_policy` ctor 参(默认 `first_wins`,recipe 经 `run_variant_pool.py` 传,风格同 force-gate)。移除每变体首胜 break、按轮全局记 `claimed_buckets`/`applied_variants`,按排名扫全队列:桶已被更高排名 ship 占用→跳过并记审计("already claimed",App B.1 原话);桶不相交但对已 APPLY 变体二次 APPLY→跳过记 `ship_skipped: bucket-disjoint same-variant apply requires config merge (unreconstructable from whole-config candidates)`(此记录即论文歧义的诚实重建)。**恒 sound 且实做**:落在**不同结算目标**的多发(1 次原地 APPLY + N 个 FORK 子变体不同目标)照发,`_plan_forks` 既有多 fork 容量/退役规划不变(APPLY 变体的轮级 decision 不被同变体 FORK 覆盖=`_settle_round` 内 `setdefault`-式护栏)。**provenance**:Hyperparams 是 frozen dataclass 且 experiment_lock.py 不改,故非默认策略经 `provenance_warnings` 记一条可审计 note(非 taint,`run_variant_pool.py` `_build_experiment_lock`),默认不发→lock 字节等同。测试:`test_engine.py` 第 10 节(首胜/多发对照身份钉 + a/b/c/d 四场景 + 容量退役)、`test_run_variant_pool.py`(arg 解析 + lock provenance 双向)、`test_reporting.py` to_dict/to_json 收口(EXP-E08)。

## 7.15 修复三件套:`--search-backend` / `--evolve-commit-bounce` / `--regression-accountability`(Jul-28,a1big4 判决驱动)

**背景**:a1big4 判决=不定判,瓶颈栈①OURS 问责规则×床方差互锁(R2/R3 整轮废)②meta 空手死 ×3(文本契约衰减)③环境(54% 预算耗尽,keyless 爬虫链)。三旗一批落地(commit e45d48d,693→733 测试),默认全=现行为字节等同,非默认经 `provenance_warnings` 记录(§7.14 同款钉法)。

**W1 `--search-backend {chain,serper}`(默认 chain)**:`harnessx/tools/contrib/serper_search.py`(纯新增,step_countdown 先例)——Tool 实例逐字复制 builtin WebSearch 的 name/description/schema/tags/target(worker 无感知),Serper 先行(env `SERPER_API_KEY`),无 key/空结果/异常回落 builtin 自身 `fn`(共享其模块级熔断,不复制状态)。**承重机制=`__hx_target__`**:YAML 往返把该工具序列化为 `tool_registry.custom` 导入路径,候选配置因此继承 Serper 而非静默回退 builtin(端到端测试钉死)。注:serper 下 h0.config_sha256 必然变化(builtin→custom),属环境谱系事实,lock warning 如实记录;"subclass"字面不可行(builtin 是 `@tool` 产的 dataclass 实例非类),镜像+委托为忠实实现。

**W2 `--evolve-commit-bounce {off,on}`(默认 off)**:真空手(重试耗尽仍无 config.yaml)时**一次**短续会话弹回——携带上轮 `DECISION_REQUIRED.md` 文本+两种合法收尾指令,`max_steps≤15`(finally 恢复),产出即成功、仍缺走原失败路径,审计记 `bounce_used/bounce_outcome`。机理裁定:`meta_harness/agent.py:545` 的 evolve 无会话续跑缝(每调用新会话、无 per-call 步预算),故取规格允许的"短续会话"实现,`harnessx/meta_harness/` 零改。

**W3 `--regression-accountability {strict,shipped_only}`(默认 strict)**:整轮 no-op 硬门在 `shipped_only` 下只吃"已上线 APPLY/FORK 配置变更引发的回退"(区间归因:载体谱系上 `(last_pass_round, fail_round]` 内落过配置变更);被拒候选门评回退与零 ship 轮间翻转降级 `strategy_concerns`(可见不阻塞,`critic.py:regressions_for_gate/demoted_regression_concern`)。机理裁定:ship 台账(`append_ship`/`ship_outcomes.json`)实跑从不落盘(仅测试写),故改用 `_reconcile` 内存记录配置变更轮(不改存储格式);已知简化:`applied_then_retired` 变体的变更不追踪,误差方向=少硬门(与修复意图同向)。地面真值:对 a1big4 R2/R3 实例,`shipped_only` 两轮均正确降级放行。

## 7.16 `--resume`:轮边界断点续跑(Jul-28,用户令"加 checkpoint 机制";commit 86ff8d1,733→755)

**语义**:暂停=任意时刻杀进程;`--resume <run>` 从最后已结算轮重建、自下一轮续跑,损失 ≤ 进行中的那一轮;与 `--clean` 互斥;默认不传=字节等同。**重建原料与保真等级**(`experiments/variant_pool/resume.py`):Ledger 经 `SuccessLedger.record` 按轮回放 `active_pool_measurements` = 字节精确(与 `_record_settled_active_outcomes` 同一折叠路径,W21 冻结基线不漂);路由分区/idle/next_id(全谱系扫描防退役 id 复用)/created_round/journal 命名 = 直接重建;**保守面如实申报**:部署 config_path 从 `R<r>/active_pool/<vid>/config.yaml` 逐轮快照取最新(末结算轮 APPLY 无新快照 → `stale:_ship` 侦测+警告,交下轮 evolve 前滚,不静默猜);parent_id 仅单 FORK 轮可无歧义恢复;随机路由臂(epsilon>0)的 RNG 流位置不持久化 → 重播种+警告(默认臂不抽签=精确)。**lock 护栏**(`lock_blocking_diffs`,无 force 旁路):比对 `h0/models/dataset/hyperparams/env` **+ `provenance_warnings`**——六个旗臂(force-gate/ship-policy/step-countdown/search-backend/commit-bounce/regression-accountability)全记录于后者,漏比即给"参数漂移续跑"开门,故为承重比对项;忽略 created_at/experiment_id/git_sha(代码漂移已被 h0 捕获)。`resume_provenance` 以顶层键追加(`from_json` 忽略未知键 ⇒ lock sha 不变,后续护栏比对稳定)。legacy_single 模式含并发 fork 的轮 = 配对真歧义 ⇒ 拒绝续跑(不猜)。已知限:pre-resume 轮的 RunReport/round_summaries 不重建(崩溃时本就未写),`pool_states.json` 经预载保持完整,per-round `pool_state.json` 为早期轮权威。

## 7.17 S1 冻结裁定包 + decomp v1 方法终裁(Jul-29,用户逐项落锤)

1. **decomp 方法**:按 DESIGN §7 建议冻结——静态 D1-lite 四类型分解头牌;B0/B1/B2
   臂梯(DESIGN §7.1;B0 = `--decomp-pool-from` 缺省纯 h0 = fresh-spawn 类比臂);
   B3 池感知画像降维为旗标臂(二期;画像限 S1 冻结统计,禁臂内账本条件化)。
   **coder 开工令已下**(纯代码零 API 费;付费跑仍受 E0/资金/用户明令门控)。
2. **step-countdown:S1 关**(论文无此机制,关 = 贴论文;旗标保留供后续消融)。
3. **M-23 回退基线**:论文未明写(§4.1 全局读 vs §4.5 per-variant 隔离,两读张力)⇒
   按用户规则"没明写就开关化 + 对照":新旗 `--regression-baseline {global, per_variant}`,
   **默认 global**(§4.1 字面 = 现行 ledger.py:265-267 行为 = 字节等同);per_variant 作
   S1 消融臂;"本簇"第三读**弃**(收敛二臂)。接线排 M1 构建之后(同
   run_variant_pool.py 防冲突),**S1 冻结前必须落地**。
4. **轮数:`--num-rounds 16`**(= 论文 15 适应轮,M-24 对齐读法;报告口径以适应轮计)。

预算档:按 M0-BUDGET-BRIEF v4 分期制执行(首期 ¥2,000,导师谈判中),不再单列档位裁定。

## 7.18 `--decomp-eval`:M1 分解×分工评测层落地(Jul-29,§7.17-1 的实施;755→829)

**语义**:评测专用模式——载冻结池(`--decomp-pool-from`;缺省=纯 h0 单变体=**B0 臂**)
→ 每题 pass-k 次分解流水线(分解→子任务路由→串行执行→变体无关 synthesis)→ 现有
pass@2 无偏估计出分;**不进演化轮,gate/seesaw/critic 零接触**;与 `--resume` 互斥
(硬拒)。九旗默认全关/中性,默认路径字节等同(专测
`test_default_path_never_touches_decomp_code`)。模块 `subtask_pipeline.py` import-pure
(零 recipe/harnessx 依赖,runner 注入);接线纯加法(run_variant_pool.py +351/−0)。

**关键裁定(coder 自由裁量,主循环验收通过)**:
1. 子任务会话带哨兵 `final_answer="[decomp-subtask: not scored]"`,使复用的 rollout
   路径走确定性 exact-match(结果不用)而不对每个子任务触发空 GT 的 LLM judge——
   **只有 synthesis 终答案过真门**;
2. ledger 冷启动:Laplace (p+1)/(a+2) argmax 后,胜者细胞观测 < min-obs 则回退任务级
   路由;平局取最小变体序号;
3. round_robin = (crc32(task_id)+attempt+subtask_index) mod n,确定性无 RNG;
4. 信用:终门后对本 attempt 用过的每个**去重** (variant,type) 记一次;fallback(四类
   原因:parse_failure/oracle_missing/validation_failure/decomp_error)不记信用、跳
   synthesis;
5. 产物含 `decomp_plans.json`(task_id→plan)= **跨臂 file: 重放通道**(B0/B1/B2 同
   分解配对,方差减半的机制载体)。

模块 772 行超 350–550 估算,系 docstring/prompt 模板密度对齐 repo 规范,验收接受。

## 7.19 `--regression-baseline` + lock 端点纪元捕获(Jul-29,§7.17-3 实施 + labsmoke1 缺口修;829→853)

**件一(M-23 开关)**:`--regression-baseline {global, per_variant}`,默认 global =
现行(`ledger.is_ever_solved` 全局全史只增),默认路径字节等同(engine **条件转发**:
仅非默认才向 gate 传 kwarg,global 调用字面不变);`per_variant` = 候选变体只对**自己**
的解题历史负回退义务——判定复用 `TaskEval.before[0]≥1`(before 本就按 per-variant
cell 语义采样,零新 ledger 方法、零 variant-id 进 gate 的管道)。第七旗入
provenance_warnings,resume 护栏拦"换基线续跑"。
**件二(端点纪元)**:lock env 节新字段 `deepseek_api_base`(记 URL 值;未设 env 记
哨兵 `"official-default"`;**key 永不入 lock**);resume 比较解析值,旧 lock 无字段
视同官方哨兵——跨纪元续跑(官方↔实验室)正确阻断,旧跑在官方环境续跑不受扰;
新跑 lock sha 因新字段而变属预期(纪元入身份)。
**验收注记**:diff 176+/11−;11 删行主循环逐行核 = 签名穿参/调用点补参/注释类改行,
零行为删除;853/0 亲跑。M-23 不对称显形以 gate 级测试证明(路由天然把任务送回解题
变体,引擎级双变体自然场景不存在——此为审计语义的实现级补充发现)。

## 7.20 `--reasoning-effort` / `--meta-reasoning-effort`(Jul-31,裁 E "拉满"实施;853→874)

> ⚠️ **本节所述代码尚未在本分支上(Jul-31 亲验)**:`exp/variant-pool` 的
> `run_variant_pool.py` 里 `reasoning-effort` **零命中**,实现只存在于未合并的
> `feat/reasoning-effort`(efc2a89,5 处命中);而 §7.20 这段文档却先落在了主分支上,
> **文档与代码分处两个分支**。后果已实测发生:标题里的「853→874」是 **effort 分支**的
> 测试数,本分支实测 **853**——该数字曾被当作基线传给下游 agent 并被其正确顶回。
> **合并 `feat/reasoning-effort` 后删除本警示框**(effort 分支未动本节,合并不会冲突)。

**建于隔离 worktree**(`HarnessX-effort`,分支 `feat/reasoning-effort`,基底 ea95b7d)
——主树彼时有 s1k8b103 在跑且看门狗可自动 resume,改主树=同跑内版本漂移;完跑后合并。

- **语义**:`--reasoning-effort {none,low,medium,high}` 作用于**任务代理**;
  `--meta-reasoning-effort` 作用于 **meta 代理**(Digester/Planner/Evolver/Critic),
  未给则回落到前者;两者皆未给 ⇒ 两旗都不传。
- **字节等同**:未设置时 `_make_provider` 的 `effort_kwargs = {}`,
  `LiteLLMProvider.kwargs` 无该键,请求体与接线前逐字相同(测试钉死双分支)。
  ⚠️ 字面值 `none` 是**端点真值**(关思考)且为真,**仍会发送**——与 Python `None`
  (省略)语义不同,勿混。
- **lock provenance**:`models.reasoning_effort` / `models.meta_reasoning_effort`
  记录**生效值**(含回落后的 meta 值);未设置写 `null`。落在 models 段=族阻断段,
  中途改 effort 续跑将被护栏正确拦下(纪元入身份)。
- **legacy lock 兼容(承重)**:旧 lock 无此二键 ⇒ `_build` 以 dataclass 默认 `None`
  填补 ⇒ 与"新跑未设置"比较无 diff ⇒ **老运行(s1k8b103)照常续跑不受阻**;
  专测 `test_legacy_lock_without_effort_keys_resumes` 断言 `lock_blocking_diffs == []`,
  配套反向测试(legacy + 现设 high ⇒ 正确阻断)。
- **judge 刻意不跟随**(主循环确认):判分器/`GAIAPipelineEvaluator` 虽同用 meta_model,
  但它是**测量仪器**,跨臂保持恒定以免混淆 effort 与判分严格度;若日后要它跟随,
  改 `setup()` 的 judge_provider 构造一行即可。
- **未持久化进 `V0/config.yaml`**(同 api_base/api_key 走 kwargs 通道):每次进程启动
  (含 resume)由 args 重建,lock 只作溯源。
- **验收**:diff 4 文件 +389/−3(harnessx/** 核心、gate、engine 零触碰);
  变体池套件 **874/0**(853 基线 + 21 新测)主循环亲跑复验;核心 `tests/` 的 5 个失败
  为 Windows HOME/USERPROFILE 环境性且**先于本改动存在**(且本改动不触 harnessx/**,
  逻辑上不可能致其失败)。

## 7.21 演化产物 fail-closed 校验 + 子任务路径消毒(Aug-02,用户令「先修复」;874→884)

**背景**:两个静默失效,都是「跑得下去但做错事」。

**7.21-1 `_resolve_artefact_paths`(`run_variant_pool.py`)**

- `_prepare_round_config` 是所有变体配置进入 rollout 的唯一咽喉,校验放这
- `_as_local_path()`:`file://` URI → 本地路径。**[Aug-02 改写,见 §7.22]** 原用
  `urlparse` + `url2pathname`,只覆盖三斜杠形;现为「剥 scheme → 去前导斜杠 →
  见盘符即 Windows 绝对路径」,**四种 Windows 拼写全覆盖**
  (`file:///D:\x` / `file:///D:/x` / `file://D:\x` / `file://D:/x`)。
  必须如此:§7.22 产出两斜杠形并经 `to_yaml_file` 落盘,下一轮读回自己的输出,
  而 `urlparse` 在两斜杠形上会把盘符当 netloc 吃掉
- 校验 `_ARTEFACT_PATH_KEYS = ("template_path",)` 指向的文件**可读,否则 raise `FileNotFoundError`**
- **只在内存内改写 `cfg.processors`,磁盘上的运行产物一律不动**(改产物 = 伪造数据)
- 设计理由:演化提示词**就是**变体。产物打不开时静默回落,会把「没有变体」
  伪装成「变体表现平平」——s1k8b103 就是这么过去的
- 未改动的配置**原对象透传**(测试 `test_an_already_plain_path_is_left_byte_identical` 用 `is` 断言)

**7.21-2 `_fs_safe`(`run.py`)**

- session id 用作目录名;decomp 子任务 id 为 `<parent>::<subtask>`,`:` 在 Windows 非法
- 仅替换 `<>:"/\|?*` → `_`;**逻辑 id 不变**,日志与记账仍用原 id
- UUID 类 id 与既有 `R3-V1-active-…` 命名逐字节不变 ⇒ 历史目录名零影响

**验证**:冻结池不同提示词数 **3 → 5**(V5/V6/V7 各自独有);冒烟 ERROR/WinError/
processor-crash 三项归零。回归测试 `tests/test_artefact_paths.py`(9 条)。

**关联**:偏差登记 M-27 / M-28;RUN-LOG「Aug-02」节;FINDINGS-AUG01 B 节撤回块。

---

## 7.22 演化**工具**的投递失败(Aug-02,用户令「把这些错误全改掉然后设计专项测试」;884→937)

**背景**:§7.21 只修了提示词那条投递路径。全等级日志审计发现**工具那条也是坏的**,
且 s1k8b103 里坏进了 **active 池**(`R2`/`R4` 的 V1),不止候选。

**根因在 vendored 侧**:`harnessx.core.harness._parse_file_tool_target` 用
`target[len("file://"):]` 朴素截断。Windows 下 RFC 式 `file:///D:\x` 截完余 `/D:\x`,
被解析为「当前盘根下的 `D:` 目录」⇒ **`D:\D:\x`**,抛 `[Errno 22]`;
加载器只记 WARNING 后继续 ⇒ 变体带着**不存在的工具**运行。

**实测四种拼写打真解析器**:`file:///D:\x` FAIL / `file:///D:/x` FAIL /
`file://D:/x` **OK** / `file://D:\x` **OK** ⇒ 仅两斜杠形可用。

**`_resolve_tool_targets`(`run_variant_pool.py`)**

- 挂在 `_prepare_round_config` 同一咽喉,与 §7.21-1 并列
- `file:` 目标 → 改写为 **`file://<绝对路径>::<符号>`**;点分模块目标(serper)原样不动
- 校验目标文件**可读,否则 raise `FileNotFoundError`**(同 fail-closed 原则)
- **`harnessx/` 零改动**(vendored);无改动时**原对象透传**,幂等
- **只在内存内改写,磁盘产物不动**

**验证**:真实 V1 配置端到端 —— 修前注册表 `[Bash, Browser, Read, WebFetch, WebSearch]`,
修后 **`+python_eval`**,无其他增减。实测受影响面:s1k8b103 **466** 次(含 active 池)、
s2k8b50 **40** 次(仅候选,池未污染)、b_smoke **14** 次(= V1 全部 14 个会话)。

**测试**:`tests/test_tool_targets.py`(13 条)。**刻意打真 vendored 解析器/加载器,
不断言字符串形状** —— 只断形状的话,日后有人把两斜杠「修正」回 RFC 三斜杠,
测试仍绿而 bug 悄悄回归。含 `test_the_rfc_spelling_is_the_one_that_breaks`:
当 vendored 侧修好时该测试会主动失败,提示可以简化本节。

**教训(须入 Ch7)**:M-27 与 M-31 是同一现象的两个实例 ⇒ 产物**消费**审计必须
**逐条投递路径**做;修好提示词那条不代表工具那条也好了。

**关联**:偏差登记 M-31(与 M-27 交叉引用);RUN-LOG「Aug-02 全等级日志审计」节。

---

## 7.23 演化 **processor** 的投递失败 + 判据升级(Aug-02,用户令「再次确认没有其他问题」;937→947)

**第三条投递路径**,与 §7.21(提示词)、§7.22(工具)同族。按**可发现性**排序:

| 路径 | 日志痕迹 | 节 |
|---|---|---|
| 提示词 | 898 条 WARNING | §7.21 |
| 工具 | 466 条 WARNING | §7.22 |
| **processor** | **0 条** | 本节 |

**为何零痕迹**:`harnessx.core.harness._instantiate_proc` 是
`try: return _instantiate(d) except Exception: return None` —— **裸吞,不打日志**。
变体遂以原版处理器栈运行,而配置声称有演化 processor。408,880 行日志匹配数为 0。

**两个成因(判据因此升级)**

1. **路径**:`builder._parse_file_target` 同样 `_target[len("file://"):]` 朴素截断
   ⇒ Windows 下 `file:///D:\x` → `D:\D:\x`。修法同 §7.22(改写为两斜杠形)。
2. **版本漂移**:配置传了被引类版本不接受的 kwarg。s1k8b103 R10/V4 引 C-R6-01 版
   `CommitNudgeProcessor` 却传 `nudge=` ⇒ `TypeError`。**文件完好,仅实例化失败。**

⇒ 第一版判据「文件可读」**抓不到成因 2**。现判据 =
**`_assert_processor_instantiates`:用运行期同一调用真正实例化一次,失败即 raise**。
这是本节相对 §7.21/§7.22 的方法学增量:**判据应为「产物是否真被消费」,不是「产物文件是否存在」。**

**`_normalise_artefact_node`(替代原 `_resolve_artefact_paths` 内联逻辑)**

- **递归**处理 `_target_` 与 `template_path` —— builder 自身也递归实例化嵌套 spec,只补顶层会留缝
- 点分模块目标(原版 processor)**原样透传且不尝试实例化**(不让原版付代价、不冒 import 副作用)
- 无改动时**原对象透传**,幂等;**只在内存内改写,磁盘产物不动**

**实测**:干净对照 R0/V0 声明 8 实例化 8;受影响 R10/V4 声明 9 **只实例化 8**,
且实例化集合与原版基线**逐个相同**。s1k8b103 共 **19 个 active_pool 配置**受影响;
s2k8b50 / b_smoke 的 active 池**未受影响**(仅候选)。

**🔴 对冻结池的后果(发臂前须用户裁)**:s1k8b103 终池 8 变体在新判据下 ——
**V0 / V1 / V6 / V7 可加载**(V6/V7 各拿回 `CommitNudgeProcessor` + `StepCountdownProcessor`),
**V2 / V3 / V4 / V5 fail-closed**,即 **4/8 不可用**。
那 4 个变体在原跑中本就一直以原版处理器栈运行,新判据只是让它显形。

**测试**:`tests/test_processor_targets.py`(10 项)。**三条路径三个测试文件,刻意不合并**
—— 本批教训正是「修好一条不代表另一条也好」。

**关联**:偏差登记 M-32(与 M-27 / M-31 三条并列);RUN-LOG「审计续做」节。

## 7.24 合成守卫 `--decomp-synth-guard`(Aug-02,用户令「加入并且记录」;947→962)

**问题**。分解管线的合成步骤在上游子任务未产出所需信息时,不声明缺失,而是从模型
参数化记忆补齐并输出自信答案。实证:`b_smoke` 任务 `20194330`,browse 子任务空手而归,
合成输出为 *"…Based on the known content from the Game Grumps episode…"*。

**为何必须修**。该失效**只虚高分解侧**:凭记忆回想的答案若恰好正确,会被记为管线成功。
因此它朝**本论文自己的假设方向**注水。频率下界 **1/20**(该次 `passed=False`);检测只抓
自报措辞,默默编造不可见,故这是下界而非估计。

**接口**。

```
--decomp-synth-guard {off, strict}      默认 off
```

- `off` —— 合成提示词与前旗标版本**逐字节等同**(`test_default_is_byte_identical_to_the_stock_prompt` 锁死)
- `strict` —— 在**最终答案指令之前**插入 `SYNTHESIS_GUARD_CLAUSE`:缺失须点名子任务 id、
  禁止以先验补齐、必要时声明「could not be obtained」。`FINAL ANSWER:` 契约保持不变,
  闸门解析不受影响

**fail-closed**。插入锚点 `_SYNTH_FINAL_INSTRUCTION = "Using only the information above,"`。
模板改写致锚点消失时 `strict` **raise**,不静默失效 —— 静默失效会让 manifest 声称一份
从未生效的保护,与 M-27/M-31/M-32 同形。

**记录面**。`--decomp-eval` 在建 lock 之前 return,lock 不覆盖此模式;写入
`decomp_manifest.json` 的 `decomp_synth_guard` 字段,这是该模式唯一的溯源面。

**读数影响**。开启后原本"蒙对"的尝试转为失败,分解侧绝对分数**会下降**。这是修正:
下降的部分本就不是管线挣来的。跨臂比较时 guard 档位必须一致。

**测试**。`tests/test_synth_guard.py` 15 项。全量 **962 绿**。

## 7.25 `cluster(task)` 可选 + ε 接线(Aug-02,用户令「可以写掉」;962→982)

**发现**。§4.5 用 `cluster(task)` 但从不定义它。路由是**每簇 argmax**,故
**有效池 = min(K, 簇数)**。`gaia_level` 只有 3 簇 ⇒ K=8 的池子最多 3 个变体有负载,
与闸门无关。s1k8b103 实测:负载基尼 0.22 → 0.78,终局 `[52,39,12,0,0,0,0,0]`。

**第二条通道**。未测量的 `(变体,簇)` 取先验 0.5,永远输给已测量的 ⇒ 早期落败即永久冻结。
`Router.explore` 本就为此写(docstring 原话),但 `epsilon` 从未接到 CLI。

**接口**。

```
--cluster-source {gaia_level, capability}   默认 gaia_level(字节等同)
--cluster-map    <path>                     capability 必需,fail-closed
--cluster-min-size <int>                    默认 8,小簇按 Jaccard 并入最相似大簇
--epsilon        <float>                    默认 0.0
```

**标签**。子任务类型的**集合**,仅由题面算出、解题前冻结。**主导类型不可用**
(GAIA 几乎全是 search 主导,只分得出 1 组)。实测 11 簇 → 合并后 5 簇(38/25/16/14/10)。

**lock**。`cluster_source` 原硬编码两处,现均读旗标;provenance 另记
**分组表 sha256 + 阈值 + 实得簇数** —— 表在仓外,无 sha 则簇数不可核验。

**并报要求**。ε 的准确率代价正比于 ε,而离线重放看不到这一侧;ε 取"够买到测量的
最小值",不是让有负载变体数最大化的值。

**测试**。`tests/test_cluster_source.py` 20 项。全量 **982 绿**。

## 7.26 评估路径并发 `--decomp-concurrency`(Aug-02,用户令「写」;982→994)

**问题**。`_run_decomp_eval` 全串行,而演化循环打满端点。评估臂只用约十分之一吞吐。

**接口**。

```
--decomp-concurrency <int>    默认 1(与旗标前路径一致)
```

- 1 —— 逐任务运行并逐任务落盘,JSONL 流式写出与旧路径一致
- \>1 —— 任务级并发;**同一任务的 attempt 仍串行**(管线在其间写信用)
- \>1 且 `--decomp-routing ledger` —— **SystemExit**

**为何拒绝 ledger**。`SubtaskRouter.route` 在该档读 `TypeCreditLedger.rate()`,
账本正被并发任务写 ⇒ 路由取决于完成顺序,臂不可由自身冻结输入复现。
`single`(恒等)与 `round_robin`(`crc32(task_id)+attempt+index`,无状态无 RNG)是纯函数,安全。

**顺序确定性**。累加与落盘移出协程,`gather` 后按 task_id 排序统一执行;
`asyncio.gather` 按参数序返回,故任何并发度下 artefact 顺序相同。

**收益**。端点上限约 5 rollout/分钟 ⇒ A1 3.9h → 约 41 分钟。**不是 10 倍**,
天花板是端点不是本地并发。

**记录**。`decomp_manifest.json` 的 `decomp_concurrency`(该模式不建 lock)。

**测试**。`tests/test_decomp_concurrency.py` 12 项。全量 **994 绿**。

## 7.27 逐子任务信用 `--decomp-credit`(Aug-02,用户令「可以」;994→1005)

**为何是前提而非优化**。B2(账本路由)是 headline 臂,`B2 − B1` 就是 RQ2 的答案。
B2 读信用表决定分工;表分不出能力,headline 就没有机制。

**缺陷**。`record(pairs=used, passed=task_passed)` 把**整道题**的成败记进链上每个
去重格子(均 4.30 格/链)。表测的是「参与过多少道做对的题」,不是「擅长哪类活」。

**接口**。

```
--decomp-credit {task, subtask_convergence}    默认 task(字节等同)
```

`subtask_convergence`:每个已执行子任务记一次观测,
成败 = `steps < subtask_max_steps`。

**判据理由**。免费、客观、独立、对齐 C-1(88.7% 失败是"没做完");撞顶率实测 22%。

**🔴 局限须随结果声明**。测「做完」不测「做对」——提前结束但答错会被记成成功。
精确替代(逐子任务裁判 / 反事实换变体重跑)贵数倍,列 future work。

**去重差异**。`task` 档对 `used` 去重;`subtask_convergence` **不去重**——
两次执行就是两次测量。

**记录**。`decomp_manifest.json` 的 `decomp_credit`。

**测试**。`tests/test_decomp_credit.py` 11 项,含两条把缺陷钉死的测试。全量 **1005 绿**。

## 7.28 `file:` 目标拼写 brief(Aug-03,用户令「改完测试再跑」;1005→1018)

**问题**(承 M-37)。Evolver 写 `file:///…` 三斜杠;vendored 加载器固定截断 7 字符,
余下前导 `/` ⇒ `D:\D:\…` ⇒ 静默失败。我方 gate/active 路径已修复,
**Evolver 自测路径未修复**,且**异常被吞、它看不见**。

**升级的严重性**。同一 brief 要求工具候选提供 Level-2 往返证据;
工具没注册 ⇒ 永远拿不到该观察 ⇒ **工具杠杆结构性失效**。

**做法**。`_FILE_TARGET_SPELLING_BRIEF` 注入 repo 与 paper 两档 brief:
`file://<绝对路径>::<符号>`,正好两斜杠,并说明其不可观察性。
两份 brief 均为我方文本,非论文 App B.1 提示词。

**验证**。①单元:复刻 vendored 截断,证明所教形式成立、所禁形式失败
(**教错比不教更糟**,故必须验真);②live:同模型各 10 次,
**无 brief 0/10 → 有 brief 10/10**。
边界:探针未复现"原本会写错",那由实跑日志佐证。

**测试**。`tests/test_file_target_brief.py` 13 项。全量 **1018 绿**。

## 7.29 分解预算 `--decomp-budget`(Aug-03;1018→1043)

**起因**。CH4 §4.6.1 写「预算由构造配平」,代码不支持 —— 每子任务各拿全额,实测 2.2×。

**为何固定上限不行**。103 题计划子任务数 1–12(均值 4.38)。固定 4 仍 25% 超,固定 5 有 41%。
**除法必须逐题**。

**接口**。

```
--decomp-budget {per_subtask, shared}   默认 per_subtask(字节等同)
```

`shared`:每子任务 `max(1, 上限 // 子任务数)`,在 `run_attempt` 内按计划长度算。

**下限保护**。计划长于预算时取 1 步/子任务,**这是唯一允许略微超预算的情形**,须报告。

**与 M-36 耦合**。收敛判据比较**本次生效上限**;否则 shared 档下永远判为收敛,信号被废。

**兜底不分割**。分解失败退化的整任务 rollout 保持全额(它就是 A1 的等价物)。

**测试**。`tests/test_decomp_budget.py` 25 项。全量 **1043 绿**。

## 7.30 观测通道分支:`file:` 装载鲁棒化 + `--traj-failure-signals`(Aug-04,用户令「可以把这份也改进,放进新的 branch 里」;分支 `feat/observation-channel`)

**两项裁决,详表见 M-41 / M-42;审计依据 `experiments/docs/novelty/10-CHANNEL-AUDIT.md`。**

**① M-41 装载器侧修复(政策偏离,明示)**。M-37/M-38 只教了 Evolver 拼写,缺陷本体未动("vendored 零改动"约束)。本分支打破该约束:`builder._resolve_target_path` 使 `file:///`、`file://`、裸路径、POSIX 全拼写可解析;`_instantiate_proc` 失败保留"返回 None 不炸跑"但 **ERROR 级响亮记录**,消费端记录被丢弃组件。依据=用户 Aug-04「robust 框架」指令;隔离于独立分支,不触活跑(M-40 纪律仍守)。

**② M-42 通道拓宽旗标(实验自变量,默认关)**。`--traj-failure-signals` 开时把正文专属失败信号计数入 frontmatter 四平铺键(`search_unavailable_count / fetch_error_count / fetch_empty_count / loop_warning_count`),落在扫描器已读的 `Read limit=30` 窗口;**默认关=字节等同**(测试钉死)。provenance 走 `_epsilon_provenance` 模式,零 `Hyperparams` 新字段。臂 0/臂 1 对比跑**须另行用户明令**。

**测试**。变体池套件 **1026 绿**;`tests/unit` **864 绿**(8 项既有 gbk/沙箱环境失败与 HEAD 基线逐项一致,`git stash` 法证)。新增:`test_builder.py` 拼写参数化、`test_trajectory_frontmatter_v2.py` 双态字节等同;`test_processor_targets.py` 由断言缺陷改为断言修复(史料注释保留)。

## 7.31 全抄官方批 1:`--ship-efficacy-gate` + upstream 同步(Aug-05,用户令「把能抄的抄了」「全抄官方的」;详 M-43 / M-44)

**背景**:官方 AEGIS 三分支现世(对比全文 `novelty/11-OFFICIAL-AEGIS-DIFF.md`);官方无变体池 ⇒ 我方 `experiments/variant_pool/` 为论文 §4.5 唯一存世实现;官方 counterfactual 门本体被我方查出为**潜伏 no-op**(schema 无生产者,`novelty/11` §5/§7.5)。

**① M-43 效力预检门**(默认关)。四项只读检查在候选评测 rollout 之前拦截运行时 no-op 候选;`efficacy:` 拒绝走既有 `RejectedCandidate`/`producer_or_pipeline_rejected` 账面。s1k8b103 的 5/7 蒸发若有此门,五个候选零 rollout 即被拦。

**② M-44 upstream 同步**(默认开,地基)。main 三提交合并 + web_fetch 挂死加固逐字节移植;`[fetch failed` 前缀保持 M-42 计数器兼容。⚠️ 事故:web_fetch 改动物理落在 b65ca01 文档提交内(并行 coder 暂存 + 主循环定向提交连带),内容已验、历史不改;**新纪律:共享 worktree 提交前必查 `git diff --cached`**。

**测试**。变体池 **1032 绿**(+6);`tests/unit` **872 绿**(+8 通过,失败集与基线逐项一致)。

**批次台账**:批 1 = 本节(落地);批 3 = M-45(f15a2f8,落地,CLI 接线在批 2b);批 2a = M-46..M-49(ab9f38f,落地,全套件 1108 绿);批 2b = M-50..M-53(d7d0f8b);批 4a = M-54..M-57(f6b5671);批 4b = M-58..M-61(7bf0948);批 4c = M-62..M-63。**「官方启用=全取」裁决(Aug-05)下,官方启用面 21 项机制 100% 落地(M-41..M-63),全套件 1247 绿;唯一排除=无工单化 Planner(形态非机制)。** smoke/chval30 可行性跑序列已获用户明令(腿 A chsmoke_off 快照 b8de67e 已点火;chval30 30x10 全旗标随后)。臂对比科学跑仍须另行明令。
