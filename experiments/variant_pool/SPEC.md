# 变体池实现蓝图 SPEC

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

**判定**:#1 是唯一的"功能"改动,已做成默认关闭的开关(`--pass-k`);#2/#3 是让 upstream 在非 Claude 模型上不出错的修复,**不影响论文原配置(Claude)的行为**。⇒ **"原体默认行为 = 原版" 已满足**;无需撤出。
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
