# E0 冻结包草案 v0(2026-07-30;待用户四裁 + s1k8b103 验收后生效)

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

- **裁 C:B1 冻结 = round_robin(等 B2 调用预算),headline 改 B2(ledger) − B1(rr)**。
  理由:原 B2−B1(single) 含算力混淆 = examiner 一击必杀(2606.13003:MAS 配平预算输
  CoT-SC 且贵 10×;2601.12307 OneFlow:同底座同质工作流可被单体吃掉)。B1(single)
  降辅助臂(答"分解+管线收益",与 B0 比)。
- **裁 D:新增 SA-matched 臂**(单变体多轮/self-consistency,总调用预算=B2)。
  防"配平即消失"杀;须 coder 新工单(B-FREEZE 前建成)。反守为攻:harness-config
  异质性 pitch 成 OneFlow 明言的 "truly heterogeneous" 开放缝,但须 B2 > SA-matched
  实证撑腰,否则 = architectural bloat。
- C.4 九条判读规则(McNemar 配对/噪声带/成本双报/组件归因决策树/结论作假设)
  整表吸收进 B-FREEZE(下一份冻结文件,E0 过门后写)。

## 6. 生效条件

① 用户裁 A(2×2 改案)/ B(过门阈值)/ C(headline 改口)/ D(SA-matched 臂)
② s1k8b103 完跑验收过 ③ oracle plans 30 份写就+抽查 ④ 用户点火令。
执行窗建议:明日 S1 验收后即发(现 Serper 档位可承),K=1 臂照旧夜发。
