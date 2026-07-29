# AEGIS 改进设计:Task Decomposition × 变体分工(Jul-27 立项,方法待定)

> 用户定向:①先测现 AEGIS 的论文效果(pilot 校准 → 冻结 → K=1 vs K=8,兼作本改进
> 的对照臂);②改进 = 任务分解(方法待定)+ 子任务分派不同变体分工执行。
> 本文为设计骨架:选项空间 + 待决问题;方案经方法调研后由用户拍板;建设期一切
> 旗控、默认关(现行为字节稳定)。

## 0. 定位

论文的变体池 = **任务级**路由(整任务 → 历史成功率最高的变体)。本改进 = **任务内**
分工:decomposer 把任务拆成子任务,router 把不同子任务派给不同特化变体,执行后聚合
成终答案。这是论文空白 = 论文的改进贡献面(非复现)。

## 1. 两个成败问题(设计必须先答)

- **P-A 信用分配**:GAIA 只评终答案,子任务无真值。候选只改一个变体,评测却混入
  其它变体的子任务贡献 → seesaw 归因被污染;账本的 (variant, task) 细胞在流水线
  执行下语义失效。候选解:子任务级 LLM 判官(代理信号,须校准)/ 按整任务归因 +
  组件消融控 / 只允许"单变体流水线 vs 全分工流水线"的臂级对比(放弃候选级归因)。
- **P-B 收益拆解**:必须 2×2 = {分解, 不分解} × {分工, 单变体},否则分解收益与
  分工收益混淆。基线臂 = 现 AEGIS(K=1 与 K=8 各一)。

## 2. 分解方法选项空间(Jul-27 调研完毕;证据全部亲开来源,详见调研台账节)

| 选项 | 机制 | 调研判定(GAIA 级实测锚点) |
|---|---|---|
| D1 前置规划器→类型化 DAG | 每任务一次轻量分解调用 → **小固定分类学**子任务 DAG | LLMCompiler 3.7×延迟/6.7×成本降+~9%准确(多跳 QA 非 GAIA);HuggingGPT 的 {task,id,dep,args} 槽式 schema 便宜可路由。**v1 推荐载体** |
| D2 类型化模板 | 任务分类 → 固定阶段链 | AgentOrchestra(现题 TEA Protocol,6 版)累积消融 **36.54→89.04**(GAIA;勘误 Jul-28:旧记 36.5→83.4 系版本漂移,且增益主要靠 **Tool-Generator agent 非分解本身**);deep-research 综述固定四段。**被 D1 吸收**(固定分类学=D2 的实质,套在 D1 的 DAG 里) |
| D3 orchestrator-worker | 执行中动态委派(Magentic-One 双台账) | GAIA 38.0±5.5,去台账 −31%(分解状态承重的实证);但角色手工、~2-5× token、与不可动 run loop 冲突最大。**否决 v1** |
| D4 演化分解策略 | 分解 policy 作为 harness 组件交 AEGIS 演化 | GPTSwarm GAIA 18.45 vs 9.70;DAAO 比 MaAS +8.33% 且 64% 成本;**推理期反而更便宜**(离线搜索+按查询分配)。**留 v2**(与论文机制同构=终局形态,但冷启动+方差,先立 v1 基线) |
| 补:涌现特化 | QD/种群小生境(AC/DC 档案、MaAS supernet) | 全部在权重/架构层——**无人做过 harness-config 特化涌现:我们的缝** |

## 2.1 调研关键结论(全部亲开来源;未验数字已剔)

- **分类学**:三系统独立收敛于 4-5 类 → v1 取 **search/retrieve · browse/extract ·
  compute/reason · verify/synthesize** 四类,禁递归再分解(防过度分解级联);
- **P-A 信用分配裁决依据**:Who&When 步级归因仅 **14.2%**、AgentProp 判官步级
  **κ=0.432**(三 LLM 集成,单作者床;勘误 Jul-28:旧记 0.43-0.57 的上界未证已删)、
  Shapley 系对基线敏感 ⇒ **子任务级 LLM 判官出局**;真值=确定性
  任务级门(before/after exact-match),按类信用**观察式累积**(某变体承接某类的
  任务通过率),零额外 rollout;leave-one-slot-out 只作校准用;
- **级联控制**:静态分解重试成本 ≈+80%(2605.15425;勘误 Jul-28:非原文字面数,由
  51.7%/73.2% 反推,且属 **coding-agent 域非 GAIA**,引用须带双 caveat);阶段间
  verify-then-proceed 门实测 −23pp 幻觉但**模型依赖**(Gemini 上无效)——旗控实现,
  flash 上先测后信;
- **冷启动**:per-(variant×subtype) 细胞计数在 103×pass@2 规模下可辨识性存疑
  (调研开放题 #1)——回退整任务簇先验(现 AEGIS 路由)直至类计数累积;
- **Q6**:推理时 web agent 上"分解收益 vs 指派收益"的干净拆分**无人做过**
  (勘误 Jul-28:2603.06859 实为 "Exact Is Easier"——**信用分配 LOO/反事实**,旧记
  "RL 训练期"系误标;它不占 2×2 的位,反而是我方 LOO 信用方案的**最佳正面先例**,
  应正引;Q6 结论暂仍成立但原锚点作废,详 DECOMP-RESEARCH-DOSSIER)⇒ 2×2 即贡献。

## 2.2 v1 推荐包(待用户拍板)

**D1-lite**:前置轻量规划器(meta 模型一调用)→ 四类型化子任务 DAG(串行,禁递归)
→ 按 (variant×subtype) 账本路由、冷启动回退整任务簇先验 → 固定聚合步 →
阶段间 verify 门旗控。信用=观察式;**2×2 headline**:{不分解} × {分解+轮转指派,
分解+账本路由}(轮转臂隔离"分解收益",账本臂加载"指派收益")。
v2 留:D4 演化分解 policy、QD 小生境特化涌现、Shapley 校准。

## 3. 子任务→变体路由的待决项

- 子任务分类学(search / browse / compute / file / media / synthesis?)——账本细胞
  改键 (variant, subtask_type);簇定义问题在子任务粒度重现;
- 冷启动:子任务类型无历史 → 均匀探索或按整任务先验;
- 特化从哪来:自然分化(演化自发)vs 引导分化(planner brief 定向不同 bucket)。

## 4. 聚合与执行模型

单 rollout → 子任务流水线(串行 DAG 先行;并行子任务留二期);聚合器 = 固定
synthesis 步(变体无关)以隔离变量。执行层实现落 recipe(新 rollout 编排器),
harness/ 零改动原则维持。

## 5. 实验设计(冻结前不动工)

臂:A0 现 AEGIS K=1 / A1 现 AEGIS K=8(= 论文效果测试,先行)/ B1 分解+单变体 /
B2 分解+分工(K=8)。指标:pass@2 主 + 每任务成本 + 子任务级代理指标(若 P-A 选
判官路线)。床与轮数沿用冻结 lock。

## 6. 阶段门

S1 论文效果基线(pilot 校准 → 冻结 → A0/A1 正式跑)→ S2 方法拍板(researcher
证据表 + 用户裁)→ S3 旗控建设(默认关,字节稳定)→ S4 B1/B2 实验。

**用户裁定(Jul-27):S2 拍板顺延至 S1 结果之后。** 判据链(明确写死):
- **分工前提检验** = S1 的三个数:①W0 headroom(oracle 联合 − 最佳单变体;≈0 ⇒
  变体冗余,分工前提弱)②A1(K=8)臂内各变体的按簇/按任务成功率**对比度**(变体间
  是否已自然分化出不同强项)③有机 fork/特化事件频率;
- headroom 与对比度显著 ⇒ 回到 §2.2 菜单拍板(D1-lite 为默认推荐);
- ≈0 ⇒ 分工改进前提存疑,改进方向重议(候选:先做引导分化——planner brief 定向
  不同 bucket——再谈分工;或转向 D4 演化分解本身作为贡献)。

## 7. v1 方法终裁建议(Jul-29,双路文献扫描后;**待用户终裁**)

> 输入:`DECOMP-LITSCAN-A-METHODS.md`(25-26 方法扫)+ `DECOMP-LITSCAN-B-OCCUPANCY.md`
> (占位核验)。建设顺序已由用户令(Jul-29)提前:骨架先行,付费跑仍受 E0/资金门控。

**建议:静态 D1-lite 为头牌载体;"池能力感知动态分解"降维为分解器可选输入
(B3 旗标臂,二期);E3 式难度门记 backlog 不入 v1。**

依据(承重证据均 ≥L2,细节见两份 LITSCAN):
1. **A 路判决"D1-lite 站得住"**:25-26 文献两桶不相交——简单免训练的全部 off-GAIA
   (TDP/AdaptOrch/E3,且赢的是成本非精度);GAIA 级的全是重框架或需训练。唯一做了
   类型化子任务路由的 Uno-Orchestra 需 SFT 61k+GRPO 且精度输 AgentOrchestra 1.4pp
   ⇒ **免训练类型化分工 on GAIA = 空位,恰是 D1-lite 所在格**。
2. **负信号三连,E0 门升承重**:JoyAgent 消融纯分解 70.3 < 单体 ReAct 71.5(GAIA)
   + AgentOrchestra 增益靠 Tool-Generator + Magentic-One 靠台账 ⇒ 分解单独无用是
   **已知结论**;论文立论改写为"分工能否救活分解",头条钉死臂间差(见 §7.1 臂梯)。
3. **B 路 α = 部分占**:AOrchestra(L3 亲验:*"Each SubAgent runs in a FRESH
   container…previous work will be lost"*,无池/无 fork-retire)/ AgentOrchestra
   (固定手工角色)/ MonoScale(无分解)⇒ 幸存缝 = **持久 seesaw 演化 harness-config
   池上的 (variant×type) 分工**;port+beat 成立,须"演化池 vs fresh-spawn"头对头
   (§7.1 B0 臂免费提供)。
4. **B 路 β = 部分占近拥挤**:AOP(静态能力描述)/ Topaz(静态画像 decompose-then-
   route)/ FlyRoute(演化画像但整 query 无分解)⇒ 成分皆占、合取未占;β 非独立
   贡献,是 α 的精化层 ⇒ 只配旗标臂;若跑,related work 必须引 AOP+Topaz+FlyRoute+
   TacoMAS+CBBA/SMART-LLM 谱系。

**统计护栏(动态为何不能当头牌)**:臂间对比要求分解恒定(file: 重放通道跨臂复用
同一份分解 JSON),池感知分解会把"分解差异"混进"分工差异";B3 的画像必须来自
**S1 冻结统计**(`--decomp-profile-from <S1目录>`),禁用臂内 (variant×type) 账本
作条件(鸡生蛋 + 治疗非平稳)。

**🔴 撞车监测(高)**:Wentao Zhang/Bo An 组两半已齐(AgentOrchestra 分解路由 +
Autogenesis 演化 lifecycle),尚无整合单篇(至 2026-07)。最好的防御 = 8 月内跑完
B 臂;监测其 H2 新作,引用告警挂起。

### 7.1 臂梯(v1.1,替代 §5 两臂制;B0 为 Jul-29 新增,骨架白送)

| 臂 | 配置 | 回答什么 |
|---|---|---|
| A0 / A1 | 现 AEGIS K=1 / K=8(= S1 双臂) | 论文复现效果 |
| **B0** | 分解 + 纯 h0 单变体(`--decomp-pool-from` 缺省) | fresh-spawn 类比臂(AOrchestra 式通用即弃执行器;其工具合成不复现,声明限制) |
| B1 | 分解 + 演化池 + single/round_robin(冻结时二选一) | 池底座收益(B1−B0);隔离分解收益 |
| B2 | 分解 + 演化池 + ledger 路由 | **指派收益(B2−B1)= 论文核心数字**;B2−B0 = 演化池 vs fresh-spawn 头对头 |
| B3(二期,可不跑) | B2 + S1 画像注入分解器 | 池感知分解增量(合取缝) |

## 8. v1 代码骨架(Jul-29 冻结接口层;细节由 coder 工单展开,未开工)

```
experiments/variant_pool/subtask_pipeline.py        ← 唯一新模块(+tests)
├── SubtaskSpec / DecompPlan      {id, type∈{search,browse,compute,verify}, instruction, dep}
│                                 校验:JSON/类型/DAG/禁递归/数量帽 → 拓扑序
├── Decomposer                    decompose(task, profile=None) → DecompPlan
│   ├── LlmDecomposer             静态:profile=None;B3:注入 S1 冻结池画像(同类一参)
│   ├── FileDecomposer            oracle 注入(E0)+ 跨臂重放(B0/B1/B2 同分解)+ 测试
│   └── fallback                  解析失败 → 整任务直跑,记 fallback 率(上报指标)
├── SubtaskRouter                 {single, round_robin, ledger};ledger=(variant×type)
│                                 Laplace 同式,min-obs 冷启动回退任务级路由
├── PipelineExecutor              串行 DAG;session_runner 依赖注入(测试 stub 点)
├── Synthesizer                   变体无关聚合(meta 直调,复用现有答案抽取/归一化)
├── TypeCreditLedger              观察式信用:终答案过任务级门后记入所有 (variant×type)
└── PoolProfile                   S1 簇级统计 → 冻结能力简报(B3 输入)

recipe/gaia_evolver/run_variant_pool.py             ← 接线(仿 --resume 分支先例)
└── --decomp-eval 评测专用模式:载冻结池(复用 resume 池重建)→ 逐题流水线 × pass-k
    → 现有 pass@2 无偏估计 → decomp_manifest + 每题 JSONL + (variant×type) 矩阵落盘
    旗标:--decomp-source {llm,file:} / --decomp-routing / --decomp-pool-from
         --decomp-profile-from / --decomp-max-subtasks / --decomp-verify-gate(默认全关)
```

骨架五性质:gate/engine/harnessx 零接触;全旗控默认关=字节等同;分解落盘可重放
(跨臂配对,方差减半);评测模式不进演化轮(绕开 P-A seesaw 污染);oracle 通道
使 E0 零新代码。backlog:E3 难度门旗标、并行子任务、LOO 校准工具。
