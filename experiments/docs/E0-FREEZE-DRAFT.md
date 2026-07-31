# E0 冻结包草案 v0 —— ⛔ **用户裁定:E0 裁掉(Jul-31)**

> **裁定(Jul-31,用户令"那裁掉")**:E0 退出臂梯与关键路径。理由链:①**非复现
> 义务**——主循环全文搜 HarnessX 43 页 PDF:`oracle` 0 命中 / `upper bound` 0 /
> `human-written` 0 / `gold-standard` 0,原文从未做过 oracle 上限实验(且原文
> 无"任务分解"概念,7 处 decompos 均指 harness 配置分解与其 meta 四阶段流水线);
> ②门的省钱理由失效(B 臂 eval-only 便宜,Serper 余额覆盖);③真实成本=30 份
> oracle plans 手写工期卡在关键路径 + 误杀风险。
> **连带**:裁 B(阈值)随之失效;**N3(“分工救活分解”交互项)暂无仪器,自 ICLR
> 线主张降为备选**——若 B 臂打平且需区分"分解器烂 vs 分工无用",可按需复活
> (可砍至 15 题半价);替代免费诊断:best-of-pool 上界 / oracle 路由回放 /
> fallback 率 + plan 抽读。本文件其余内容保留作复活时的现成设计,**当前不生效**。
> 现行裁定项只剩 C(headline 改口)/ D(SA-matched 臂)/ E(effort 档)。

# (以下为原草案 v0 存档,2026-07-30)

> E0 = 神谕分解注入门:B 臂线的 go/no-go + 分解质量上限锚。
> 本草案吸收 NOVELTY-EXPDESIGN-RESEARCH.md PART C(Jul-30 researcher 深扫)。
> 判读规则先写死防事后挑数;【待裁】处未钉前不生效。

## 1. 语义改案(裁定 A)

原案(S1-FREEZE §4):E0 = oracle 注入 + **B0 配置**(纯 h0 单变体)。
**改案:2×2 on 子集** —— {h0 单变体, 演化池+round_robin} × {oracle 分解, 无分解}:

| 格 | 配置 | 回答 |
|---|---|---|
| ① h0 · 无分解 | = s1k8b103 R0 V0 现成数据(免费) | 基线 |
| ② h0 · oracle | 付费评测 | 负信号复核(JoyAgent:分解单独无用) |
| ③ 池rr · 无分解 | = s1k8b103 末轮池测量限子集(免费,caveat:末轮任务级路由) | 池基线 |
| ④ 池rr · oracle | 付费评测 | **主判据格:分工语境下完美分解的净增益** |

- **改案理由**:decomp-alone 无用是已知结论(DESIGN §7 负信号三连),原案单格会错杀
  "分工救活分解"这条主立论;2×2 恰好同时产出"救活"证据与上限锚,付费格只有两个。
- **门逻辑**:④−③ 过门 ⇒ B 臂全发;不过门 ⇒ 立论转"复现审计+诚实负结果"(DESIGN 风险预案)。
- 可选加强:③′ 结构安慰剂(单子任务=整任务的 trivial plan 走 --decomp-eval 管线),
  隔离管线开销;视预算,非必需。

## 2. 床、池、oracle plans

- 子集 = **pilot30**(103 床按 L1/L2/L3 难度配比裁出的 30 题;R0 参照系现成);
  in-sample 于演化床——E0 是机理诊断非泛化主张,声明即可;
- 池 = **s1k8b103 终池**(`--decomp-pool-from runs/s1k8b103`)⇒ **硬前置 = s1k8b103 完跑验收**;
  若终池 K=1(零 fork 复发),④ 格降为"单变体+分解",门语义改注(引导分化预案另议);
- oracle plans:30 份,知答案与解路径撰写(D1-lite 四类型 schema,DAG 校验过 file: 通道注入,
  fallback 率=0 by construction);撰写=主循环+抽查,禁入床答案泄漏到 plan 文本(plan 只含
  步骤指令不含答案)。

## 3. 配置(两付费格)

`--decomp-eval --decomp-source file:oracle_plans_pilot30.json --decomp-routing round_robin
--decomp-pool-from runs/s1k8b103`(④ 格;② 格 pool-from 缺省=纯 h0)+ 床=pilot30.json
+ pass-k 2 / max-steps 20 / c=10 / lab 端点 / serper。
成本:~120 评测 ≈ 2-4h 墙钟 + Serper ~0.6-1k 次(现档位可承,不吃 K=1 臂预算)。

## 4. 预注册读数与判读(阈值待钉 = 裁定 B)

1. 逐格 pass@2(30 题,四格同题=题内配对);pass@1 与 best-of-k 分列;
2. **主判据 ④−③**:过门 = **≥ +3 题(+10pp)或 McNemar 单侧 p<0.1**【草案值,待钉】;
   ≤ 0 题不过门;之间唤用户裁;
3. 副读数:②−①(负信号复核,预期 ≈0 或负,若大正 = 意外发现单列)、交互项
   (④−③)−(②−①)、两付费格成本与 fallback 率(应=0);
4. E0 兼上限锚:B 臂跑完后回算 LLM-decomp 臂达 ④ 格的百分比(C.4-6:达 ≥X% ⇒
   分解质量非瓶颈,X 待 B-FREEZE 钉)。

## 5. B 臂梯改案(裁定 C/D;非 E0 本体,一并呈裁,细则入 B-FREEZE)

- **裁 C(深读修订版):B1 冻结 = round_robin(执行器调用与 B2 配平),headline 改
  B2(ledger) − B1(rr)**。一手依据重新分工(DR-A):"配平后单体≥多体"由
  **2604.02460**(matched thinking-token,95% bootstrap CI,SAS≥MAS)与
  **2606.15017**(web 床 WebArena/WorkArena,token-matched 长 horizon 对照下增益
  消失)承担;**2606.13003 降为动机引**(as-deployed 10× 成本 + web 床 MAS<CoT-SC
  存在性;其自身不配平,margin 仅 1.19–2.38pp 无显著性)。B1(single) 降辅助臂。
  统计口径:95% bootstrap CI(2604.02460 式)与 M-25 噪声带并用。
- **裁 D(深读修订版):新增 SA-matched 臂,配平资源 = 总 token(+成本+墙钟),
  调用数并报**——两篇一手 matched 研究均配 token 而非调用数;且须含**两个变体**:
  (i) **长 horizon 单体**(Vanilla-IB 式,步数扩到与 B2 同 token;web 多步床上比
  投票更强的对照);(ii) self-consistency k=5。**B2 须同时 > 两者**。
  ⚠️ 原"truly heterogeneous 反攻话术"已删(DR-A REVERSED:OneFlow §2 逐字定义
  heterogeneous=|ℬ(W)|>1 换底座;我方同底座 config 池按其定义 homogeneous 且 KV
  可共享,**逐字落入"可被单体吃掉"范围**——SA-matched 因此从防御升为生死线);
  若 B2 ≤ SA-matched,预注册如实报"无净架构优势"。利好先验可引:2604.02460 §5.3
  "分解在上下文退化 regime(α=0.7)才有正增益"——GAIA 长上下文恰是该 regime。
  须 coder 新工单(B-FREEZE 前建成)。
- C.4 九条判读规则(McNemar 配对/噪声带/成本双报/组件归因决策树/结论作假设)
  整表吸收进 B-FREEZE(下一份冻结文件,E0 过门后写)。

## 6. 生效条件

① 用户裁 A(2×2 改案)/ B(过门阈值)/ C(headline 改口)/ D(SA-matched 臂)
② s1k8b103 完跑验收过 ③ oracle plans 30 份写就+抽查 ④ 用户点火令。
执行窗建议:明日 S1 验收后即发(现 Serper 档位可承),K=1 臂照旧夜发。
