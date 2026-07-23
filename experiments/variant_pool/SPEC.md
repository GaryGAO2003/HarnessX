# 变体池实现蓝图 SPEC

> **定位(修正 Jul-23,吸收 Codex 批判):这是"按论文附录重建的、论文启发的跨模型复现(paper-informed re-implementation)",不是"照抄论文"。** 论文未开源(仅承诺未来开源),且我方用 DeepSeek V4 替换了论文的 Opus 4.6 / Sonnet 4.6 / GPT-5.4 内外环模型。⇒ **不得对照论文的绝对分数**;所有未被论文规定的设计选择必须显式记录(§6.6 留白契约表)。冲突处采论文;论文没给的自定并标注,作为贡献面。
> 完整机制依据:`experiments/docs/HarnessX_VariantPool_TheoryFast_Report`(精读报告,全文逐字);工作项表:`experiments/docs/HARNESSX-IMPL-CHECKLIST.md`。
> 论文页码指 arXiv 2606.14249v2。
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

## 7.6 实现中确立、值得保留的三条设计原则

review 中确认的判断,记录以免后续被改坏:
1. **宽松解析,严格门控**:`ChangeManifest` 所有字段带默认值,不完整的 manifest 仍能解析,由 `validate_complete()` 判定 —— 否则是 pydantic 而非确定性门在决定什么能 ship,违背 §4.3 "only deterministic checks govern shipping"。唯一例外是 `extra="forbid"`(未知键 = 臆造 schema,属解析错误)。
2. **hit-rate 池化而非平均**:`sum(hits)/sum(predicted)` 跨 ship 汇总;平均会让一个走运的 1 任务 ship 抵消一个失败的 5 任务 ship。分母为 0 返回 `None` 而非 0.0,禁用规则拒绝在未定义比率上触发。
3. **pass@2 语义忠实**:2/2 掉到 1/2 **不算回退**(仍 solved),这正是论文 §7.1 所述 pass@2 掩盖亚阈值退化的机制;实现不得"顺手修好"它——那会偏离论文并使 Global 臂的崩塌不可复现。
