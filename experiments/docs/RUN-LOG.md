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

### forceprobe2 — **fork 结算链首次真实走通** ✅
- 配置:calib6 / rounds 3 / `--force-gate fork` / **pass@2** / **K_t=2** / max-cost 8
- 判决:`r1 fork V1`(历史首次 fork 事件);变体数 [1,2,1](R2 的"1"=at-freeze 语义,
  池内仍 2);R2 空转(见发现④)
- 全链验证清单(全过):①C-R1-01(levers=[instruction],故过第 4 关)达第 5 关,真实
  判决 REJECT(improved=[] regressed=[])被强制改判,审计串完整:`FORCED_GATE(fork):
  real_decision=reject; synthesized_improved=[00d579ea, 05407167]`——合成集恰为两个
  失败媒体任务,按设计;②C-R1-02(processor 桶)死第 4 关 ROUNDTRIP_L2,探针按
  stage-5-only 规矩未碰;③结算:pool.fork → V1 出生,config=候选 config,
  **journal=获胜候选 slot memo**(`learnings_V1.md`,67 行完整条目,
  `cited_candidates: [C-0101]` 别名入 journal)= inherit-then-diverge 正确;APPLY 侧
  对等收养已核实存在(`_adopt_candidate_memo` :1750);④R2 路由冻结在双变体池上
  合理决策:V1 账本全是候选评测惨案 → 六题全给 V0、V1 空转(活着,未退役);
  ⑤pass@2 全链上线:masking gap 0.1667 实测(§7.1 现象);**R1 regressed=[] ——
  pass@2 如预期削掉方差墙**(pass@1 的 calib6 轮轮翻转);⑥run-total headline 口径
  生效(36/0/5)。
- 新发现(待修/待记):
  - **P1 报告误分类**:headline "candidates rejected: 2" 把 fork 赢家也计入拒绝
    (诊断字段本身正确=fork);应加 "candidates forked" 行并修 rejected 口径;
  - **P2 目标选择饿死**:R2 `paper_target_variant=V1`,而 V1 零路由零轨迹 → 无法
    evolve → 整轮空转(requested_slots=0)。有机 fork 的子变体带着真赢下的任务集,
    通常不会犯;但"目标选择可选中无轨迹变体"是真实设计缝(论文 round-global 目标
    选择本就是留白)——修法:目标选择跳过无 settled 轨迹的变体;
  - 观察:强制 fork 一个劣质候选 ⇒ 僵尸变体(占 K 名额直到退役)——探针可接受,
    正式实验里由有机门防住。

### gaia_agent.j2 守则补丁落盘(forceprobe2 结束后)
- +6 行 / 3 折入点(Step Budget 节 + Tool availability 条 + no-progress 条),文献
  依据与两处渲染事实修正见会话记录与 researcher 证据表;全文件 Jinja 渲染验证
  (`max_steps | default(20)` → 20/18)。备份 `gaia_agent.j2.h0-original` 同步入库。
- **可比性边界:smoke_hard2 … forceprobe2(含)全部使用原版 prompt;此后的 run
  使用守则版**。H0 变更由用户明令(Jul-26"加 guardrail,不要大改")。

### L2 机器自证(乙+甲)落地(SPEC §7.11 → M-22)
- `--l2-cert {auto,off}` 默认 auto,仅 repo 模式注入;meta 亲笔 L2 优先(甲);tools 桶
  无申报时从**候选评测真轨迹**取新工具最长真实输出,过 provider 真序列化路径
  (`to_openai_content` → tool message content,与 litellm 内联构造一致),
  `check_level2_roundtrip` 判存活;从未调用=`no_invocation` 诚实拒;processor 桶 v1
  仍须亲笔;certification 结果入 candidate meta(OURS 标记)+ W28 一行摘要。
- 16 新测试(527/0)。**真盘集成验证**(coder 因规格书路径笔误未见真产物,主循环补验):
  forceprobe2 真 sessions 上 WebFetch 30,044 字符/sidecar 解析/未知工具 None 全对;
  真序列化器 30,044 字符存活、空串拒绝。
- 两处偏差已审核接受:①legacy 模式不注入(无 manifest 可证,保 off-identity 断言);
  ②序列化器返回 content 字段而非整条 JSON(整条会因转义使子串检查虚假失败,
  content 才是"模型读到的东西")。

### organic1 — **首次有机 APPLY ×2:演化环自持** ✅✅
- 配置:calib6 / rounds 3 / pass@2 / K_t=2 / **无强制** / 四修复首次同场(守则 prompt
  首跑 + 回退接线 + L2 自证 + run-total 口径);成本 ¥6.18 实测(182.67→176.49)
- **判决:R1 `C-R1-02` APPLY(improved=[04a04a9b], regressed=∅)→ V0 配置首次有机更新;
  R2 在新配置上 `C-R2-02` 再次 APPLY(improved=[00d579ea]——L3 媒体任务首次被上线候选
  翻转)**。主池 pass@2 0.50 → 0.67 → 0.83(爬升含真实上线增益,与方差的归因需账本
  细分,不作疗效声明)。
- **首个有机上线候选内容 = `h_yt_dlp_ytsearch_v1`**:meta 自主发现 yt-dlp 直调可用,
  教 agent 在 WebSearch 退化时用 `ytsearch` 兜底——与主循环同日的环境探测互证,
  演化环第一发打在 yt-dlp 线索靶心上。
- 其余候选:C-R1-01 no-config-after-retry(retry 契约用尽仍不写 config,DeepSeek 顽疾
  仍偶发);C-R2-01 诚实 SEESAW 拒(improved=∅)。
- ⚠ **报告误导实录(P1 bug 现行犯)**:pool_report.md 写 "rejected: 4 / candidate 列
  无 ship 迹象",实际两发 APPLY——organic1 进程载入的是 P1 修复前代码,APPLY 的
  archive_reason 字段令旧分类器误计。**真相以 R1/R2 pool_state.json 为准**;修复
  (applied/forked/rejected 三分互斥)已随本批提交,此报告不追溯重生成(保运行时
  原貌,以本条勘误替代)。
- 🔴 **新发现 F2 守则不合规**:21 条 budget_exceeded 轨迹里 20 条空手而死(仅 1 条按
  "第 18 步交底"守则交了答案);且主池预算耗尽 16/36,高于 forceprobe2 的 5/36——
  "有步数就继续干"可能压过了截止条款,而 flash 无法在 30 万 token 轨迹里自数步数
  (smolagents 的同款守则依赖 harness 每轮渲染剩余步数)。**裁定:不再迭代 prompt;
  留作演化食物**(步数注入器正是论文 Control 杠杆的标准形态)+ 论文观察点
  (开源小模型的守则依从性,接 §7.7 未测面)。

### 环境诊断勘误 + POSIX 桥接(Jul-26 晚)
- **勘误**:此前"yt-dlp 未安装"的诊断作废——yt-dlp 2026.06.09 一直装着且 rollout 同路
  shell 直调可用(rc=0);python3 也真实存在(3.13.5)。真凶 = agent 的 POSIX 习惯撞
  Windows cmd:`2>/dev/null` 使整条命令报「系统找不到指定的路径」而输出全空、
  `/tmp` 路径不存在。hard3 no-op 的"工具不可用"论据部分为环境伪影;守则里
  "yt-dlp 可能缺失"措辞不精确(它在,是调用方式死),不改(通用防线仍有效)。
- **桥接(已实施并按 rollout 同路验证)**:建 `C:\tmp` `D:\tmp` 目录 + `C:\dev\null`
  `D:\dev\null` 文件;验证 `2>/dev/null` rc=0、`/tmp` 写读全通。**可比性边界:
  organic1(含)之前无桥接;此后的 run 带桥接**。ffmpeg 仍缺(记录在案,pilot 校准时
  与 whisper 一并定)。冻结时环境清单进 lock。

### paper1 — 忠实臂首考:阴性,但死因反转
- 配置:calib6 / rounds 2 / pass@1 / K_t=2 / `--manifest-mode paper`;成本 ¥2.85
  (176.49→173.64);R0 基线 4/6 pass@1(桥接+守则环境下最高 R0,单点不作声明)
- 判决:两候选 ×(首发+retry)**四次尝试全部** PIPELINE_PROPOSAL(不写 config.yaml),
  零候选达门。P1 修复生效确认(报告出现 applied/forked 行)。
- **死因反转**:并非"Table 9 负担过重"——四次全都写出了接近合法的 manifest.yaml
  (candidate_id 用外向别名 C-0101 ✓、双 capability_evidence 带真实代码行引证、
  结构化 file_changes/predicted_impact/attribution_signature)+ candidates.md,
  **唯独没写 config.yaml 和声明的工具文件**——"分析完不提交"老病根在 paper 负担下
  100% 复发(同模型在 repo 模式多数轮能写)。注:该 manifest 即便有 config 也会死
  在第 4 关(tools 桶无 'Level 2' claim;paper 模式无机器兜底,按设计)。
- **意外收获(真 harness 缺陷,meta 发现)**:WebFetch 经 html2text
  `ignore_images=True`(harnessx/tools/builtin/web_fetch.py:35)**主动丢弃图像**,
  媒体任务只见 alt-text 是我们自己的抓取配置所致——**留作演化食物,不手修**。
- 后续:①paper 契约补"写作顺序"条款(先 config.yaml+工具文件、后 manifest/
  candidates;"描述了未写出的 config 的 manifest = 自动失败轮")+ tools 桶 L2 claim
  提醒——**等 A1 coder 落地后再改**(同文件并发冲突);→ paper2 复验(~¥4);
  ②若复验仍阴性 → 论文口径:"忠实臂需更强 meta(原文 Opus 4.6),n=6 证据"。

### paper2 — 写作顺序条款生效,墙前移两级
- 配置:同 paper1 + 顺序条款(9818314);判决:C-R1-02 **首次在 paper 模式写出
  config.yaml**,死于 replay 冒烟超时(**恰 20.0s——揪出上游钳制 bug**,见下);
  C-R1-01 死于 W19(bucket=[prompt,config] 缺 attribution_signature,注释"纯 prompt
  可 null"被读宽)。→ 修复:HARNESSX_REPLAY_TIMEOUT_CAP_S 环境门控(bb0a90d,
  SPEC §10#5,默认 20s 字节不变)+ attribution 注释收紧。

### paper3 — Table 9 manifest 首次全清,死因退到类型词表
- 配置:同 paper2 + 60s replay 帽 + 两处措辞修复;判决:`provenance:
  {paper_manifest: 2}, parse ok: 2` ——**两个候选的论文 manifest 全部合法解析**
  (历史首次);死于 capability_evidence `type` 自创词(search_backend /
  trajectory_analysis ∉ 枚举)——**契约从未给出词表**,proposal 期即拒,零评测
  浪费。→ 修复:必填键行补 type 枚举 + "不确定用 other"(等 A3 落地后改,同文件
  冲突纪律);paper4 复验。
- **收敛判断**:paper1→3 每轮一句话措辞消一堵墙且墙持续变小;忠实臂"DeepSeek 撑不起"
  的初判正在被推翻,更准确口径="撑得起,但契约必须把 schema 词表逐项挑明"——本身
  即 §7.7 开源 meta 观察点。

### a1smoke — LLM Digester 首次实战:全绿 + 首次真实触发选择性调用短路
- 配置:calib6 / rounds 2 / pass@1 / repo 模式 / `--aegis-digester llm`(其余角色确定性)
- **Digester 判决:全绿**——审计如实 `MetaModel_llm_digester`;失败任务归因高质量
  (search_failure→[tools/WebSearch, model_capability] / budget_exceeded→
  [processor/TokenBudgetProcessor] / unretrievable_media→[tools/WebSearch,WebFetch],
  受控词表);通过任务零 LLM 调用;实值 a_t=0.9 + 论证式 rationale;零回退。
- **意外首触发**:a_t=0.9 < α=1.0 → `selective_invocation no_op` 整轮跳过——
  EXP-E07 前半(α 门)首次真实开火,但暴露**阈值默认错配**:recipe CLI 默认 α=1.0
  (二值 digester 时代校准),库层 OURS 默认=0.5。→ 修法排队(llm 模式默认 0.5,
  显式传参覆盖;等 A3 落地,同文件纪律);a1smoke2 用显式 0.5 复验全链。
- 时序纪律:A3 coder 在 run_variant_pool.py 施工期间**冻结一切 live run**
  (启动会导入半成品代码)。

### paper4 — **忠实臂冲线:paper 模式候选首次走完全程** ✅(收敛序列收官)
- 配置:同 paper3 + type 词表(2ad682e 系列四修复齐:顺序条款/署名收紧/60s replay 帽/
  词表);首发因 `float(None)` 启动崩(见提交 2ad682e 的教训记录),无管道重启后干净跑完。
- **判决**:C-R1-01 全程通过——`paper_manifest` 合法解析 → replay 过关 → **首次真实
  评测** → 第 5 关诚实判决(improved=[] regressed=[])→ 按实力 REJECT。另一 slot 死于
  no_config_after_retry(随机顽疾,非结构;正式 paper 跑可升 --evolve-retry)。
- **四连跑结论(论文口径定稿)**:DeepSeek 作 meta **撑得起论文忠实臂**,条件是契约把
  隐性知识显性化(写作顺序/署名适用域/schema 词表)+ replay 帽适配延迟——"开源 meta
  需要显式契约脚手架,强模型靠隐性能力补齐"= §7.7 未测面的干净观察,证据链 n=10
  (paper1×4 + paper2×2 + paper3×2 + paper4×2)。

### aegis1 — **Phase A 验收:三角色全 LLM 首跑绿灯** ✅(`llm_aegis_reproduction: true` 首次盖章)
- 配置:calib6 / rounds 2 / pass@1 / repo 模式 / 三角色全 llm / α auto=0.5 / 60s replay 帽
- 判决:四角色审计名全真、零回退;Digester a_t=1.0(auto α 无误杀);**Planner 改造
  空间成立**(两份桶多样化 brief,YouTube-JS 诊断 [tools,config] + 预算策略诊断
  [prompt]);**Critic 实质审计**——拒掉唯一候选,理由证据锚定("所改组件与失败证据
  不对症、无能力证据、会浪费检查资源")= p.32 纪律由 LLM 角色在评测前执行,较确定性
  回退省 ~6 条评测 rollout。另一 slot no_config(顽疾,随机)。
- 认知层复现度 ~40% → **~90%**(余差=论文原版 prompt 未知 + B1 顺延)。Phase A 收官。

### calib_p12_a — S1 校准谱系 A(pilot12 首跑)
- 配置:pilot12 / **pass@2** / rounds 3 / K=1 / 三角色全 LLM / α auto / 60s replay 帽;
  成本 **¥15.09** 实测(168.94→153.85)
- **基线带:床落中段** ✅——pass@2 0.667→0.667→0.583(失败余量充足,方差较 pass@1
  时代明显收敛);预算耗尽 30/72(pilot12 的 L2 更多 + F2 die-empty 顽疾,冻结时
  连同 max-steps 一起裁);
- **候选 4 攻 0 上线——归因改判,两个都是旋钮不是病**:①3× "no config" 完成时间
  294/298/315s 聚在 300s = **`--evolve-wall-clock` 默认 300s 切断了更大 digest 的
  分析**(pilot12 失败任务多,meta 没做完就被掐;wasted pro 时间也白烧钱)→ 正式跑
  传 600s 并入 lock;②1× replay 60s 帽下仍超时(延迟彩票残余)→ 冻结时升 90-120s
  或接受低频损耗,二选一记录;
- 待谱系 B 完成后:W0 headroom(两谱系喂 oracle_ceiling)+ 方差/依从率/出货率汇总
  → 冻结草案 + M0 简报。

### calib_p12_b + W0 — S1 校准收口
- B:pass@2 0.583→0.667→0.583(与 A 同带 ✅ 床稳定);耗尽 31/72(与 A 一致);
  成本 **¥10.66**(153.85→143.19);两谱系合计 ¥25.75。
- **有机修订首次触发**:`C-R1-01-revision-01` 槽真实出现并运行(LLM Critic 发修订
  请求 → revision producer 跑)——A3 接线后首个野外触发;死于预算(见下)。
- ~~八拒联合归因:旋钮 6/8~~ **[勘误 Jul-27,全文审计抓出]**:`EVOLVE_WALL_CLOCK_S`
  代码默认 **10000s**(defaults.py:31,亲验)——**300s 的墙不存在**,A 的 294-315s
  聚类是巧合。**修正归因:5× 真 no-commit 顽疾(178-315s 自然时长自停,retry 未救)
  + 3× replay 60s 帽超时**。出货率的真实杠杆:replay 帽升 90-120s(3/8)、retry 预算、
  **采纳论文已公开的角色 prompt**(审计发现 Planner 全文公开/Evolver ~60%/Critic
  ~70% 而我方采纳 0%——提交纪律可能随原文措辞改善);"墙钟 600s"从冻结旋钮清单
  移除(本就 10000s,是我误记)。
- **W0 headroom = +0.0pp(全级)——按混淆解读,勿直接判死分工前提**:两谱系零上线
  ⇒ 六检查点全是同一 H0 配置,W0 测的是"同配置抽样噪声的联合"而非变体互补;真实
  信息=**床的失败是结构性的**(~4 个 L2 任务任何抽样恒败;解集嵌套,Jaccard 中位
  0.875,oracle≡best_single)⇒ headroom 只能由**配置演化分化**创造;
  **分工前提判据①的读出点后移至 A1(K=8,墙钟 600s)**。
- 冻结旋钮定案:`--evolve-wall-clock 600` / replay 帽 90-120s / max-steps 维持 20
  (可比性,声明);床=pilot12(带 0.58-0.67,余量充足,方差已驯)。

### 今夜计划(Jul-27 夜,用户睡前令"保证跑一晚上";自主执行)
1. P1 coder(论文 prompt 采纳 + 双臂 CI)返回 → 验收(全套测试绿)→ 提交;
   **45 分钟死线**已挂:届时未落地则以 `--aegis-prompts ours` 照发,夜不空转;
2. **主跑 = a1pilot:首次 K=8**——pilot12 × 15 轮 × pass@2 × 全 LLM 角色 ×
   replay 帽 120s × K_t=2(预算内保守;K_t=4 留正式跑),预估 ¥85-115 /
   5-6 小时;目标:首批有机 fork/退役/变体分化数据 + 分工前提判据②读出;
3. 完成后自动:台账回填 + 变体分化分析(按簇对比度、fork 事件、W0 复算)+
   提交推送 + 晨间报告与 P2 决策包(步数注入/multi-ship/规模档+充值);
4. 安全轨:余额地板 ¥30 以下不再发新跑;启动失败最多一次诊断重试;只提交
   亲验绿的状态。

### a1pilot — 夜车一号:早停机制首实战 + 两个根因揪出(¥9.33)
- 配置:**首次 K=8** / pilot12 / 15 轮计划 / pass@2 / 全 LLM / **论文 prompt 首跑** /
  replay 帽 120s(未生效,见下);实跑 R0-R3 即 **patience=3 早停**(R1-R3 六候选全灭
  → 连续三轮零上线)——早停机制首次实战开火,行为正确。
- **大好消息(论文 prompt 立竿见影)**:no-commit **0/6**(校准 5/8)——提交纪律疑似
  被原文措辞治好;meta 火力转向**工具/processor 候选 4/6**,正攻 GAIA 最大杠杆。
- **根因一(架构缝)**:论文 prompt 的 LLM Critic 按论文纪律在 **PIPELINE_CRITIC_INITIAL
  枪毙缺 L2 声明的代码候选(4/6)**——上游于门的 M-22 机器自证,且 Critic 时点评测
  轨迹尚不存在,机器证据想早也早不了。→ 修:双臂 Critic prompt 加路由指令
  (记 concern 不拒;门仍以实测证据强制)。
- **根因二(修复从未生效)**:replay 帽环境门控**从未起过作用**——replay.py:246 有
  第二处硬编码 `min(timeout_s, 20.0)`,agent.py 传下来的 120 被重新钳回 20(REPLAY_FAIL
  elapsed 恒 20.0s;此前单测只盖 helper 未盖链路,教训:测集成不测孤件)。→ 修:单一
  真源迁 replay.py + 双模块源扫描钉(4224af7)。校准期"60s 帽"记录随之勘误:实际仍 20s。
- 曲线(参考):pass@2 0.583→0.750→0.667→0.667(K=8 但零上线未分化,无臂间意义)。

### a1pilot2 — 夜车二号(修复后重发)
- 同参数 + `--evolve-retry 2` + replay 帽 120s(**首次真实生效**);预算余量核查后发车;
  期望:L2 误杀解除 + replay 真 120s + 提交纪律已愈 ⇒ 候选首次大概率抵达评测与门,
  K=8 分化窗口打开。

### a1pilot2 — 夜车二号:管线全通,门前三堵新墙(¥16.70)
- 修复栈全部生效:**6/6 候选抵达评测**(一号车 0/6)——L2 路由指令解堵 Critic、
  replay 帽 120s 首次真实生效、no-commit 0/6。仍 R0-R3 早停(6/6 门前被拒)。
- 三堵墙解剖:①2× processor 桶死 L2(**M-22 v1 只盖 tools 的边界成为约束**;meta
  连提 processor 候选=它看见 F2 空手死想装处理器,方向正确);②2× "新工具从未被
  调用"诚实拒(**工具采用瓶颈**:注册≠使用;论文 C-R10-02 桶=[tools,prompt,config]
  ——工具须耦合教 agent 使用的 prompt 改动,我方 meta 未做);③2× improved=∅
  (残余未解题=结构地板,prompt 类啃不动)。
- 主池方差仍凶:pass@2 0.50→0.67→**0.83**→0.58(drift −0.25)——ever-solved 棘轮
  +高方差使 APPLY 随轮次递难,fork 通道(improved≥1∧regressed≥1)因 improved=∅
  未开。
- → 夜车三号前置修复:M-22 **v2 processor 认证**(以 replay 冒烟实跑为证据:config
  canonicalize+replay 通过 ⇒ 注册 processor 必已在真实循环执行;弱于 tools 的序列化
  探针但同精神,标注 OURS-v2 待晨间复核)+ 契约加**工具-采用耦合**要求(tools 桶候选
  必须含指导使用的 prompt 改动 + predicted 任务触发说明)。

### a1pilot3 — 夜车三号:管线证明完备,"改善旱灾"现出真身(¥14.15)
- 全墙尽拆配置(L2 路由+replay 真 120s+processor v2+采用耦合+retry 2+论文 prompt);
  仍 R0-R3 早停。五拒:3× no-commit 复发(436/255/204s,随机顽疾,retry 未救)、
  1× 诚实 SEESAW(该候选**完整走完评测与门**——管线各段皆有实证)、1× Critic 高质量
  否决(正确识破"预测翻转任务全被环境阻断",含搜索商故障情报)。
- **夜间三车总结论**:①管线 100% 打通且每段有实证;②早停机制 3 连验;③论文 prompt
  对 no-commit 的疗效是随机的(0/6→3/5 波动);④**根本瓶颈=改善旱灾**——pilot12 的
  未解余量集中在结构地板(媒体/搜索阻断),prompt 类啃不动、工具/processor 类现在能
  过门但也没啃动 + no-commit 蚕食名额;ever-solved 棘轮×高方差使 APPLY 门槛逐轮升高;
  ⇒ **K=8 分化无法点火,因为点火需要 ship,ship 需要能啃地板的候选**。这与论文 §D.5
  "能力地板之下演化不复利"论题同构 = 论文级发现,非工程失败。
- 夜间三车合计 ¥40.18(9.33+16.70+14.15),每车早停使成本可控;不发四号车
  (同配置只会复读,安全轨生效)。

### 会话成本合计(实测)
¥194.14 → ¥103.01(**¥91.13**:十七个 run;夜车三部曲 ¥40.18)

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
| 2f2bfb7 | RUN-LOG 台账建立 + SPEC §7.10-7.12 + 勘误 E08/E09(记录纪律批次) |
| da9e0e7 | 守则 prompt 落盘(+6 行,备份入库)+ forceprobe2 fork 里程碑台账 |
| 881199a | L2 机器自证(乙+甲,M-22;527/0;真盘集成验证) |
| 2f11cd7 | worker 裁决 Flash(SPEC §7.13 + 证据档案) |
| e384636 | P1 三分计数 / P2 目标资格(轨迹∩路由任务)/ to_dict run-total + organic1 里程碑台账(535/0) |
