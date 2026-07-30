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

### pilot30 床构造(Jul-28 晨,用户令"扩大题库")
- 规则(确定性、零人工挑题):pilot12 全集 + 各级按 sha1(task_id) 字典序补齐至
  L1=11/L2=16/L3=3(比例≈全床 38/50/12%);pilot12 ⊆ pilot30(校准可比)。
- **flash 强弱之争的分辨实验**(用户问"ds flash 会不会太弱"):审计驱动因子#4 预测的
  地板效应与旱灾同构,但 flash 整床 58-83% 非全局趴底——**a1big1 点火实验分辨**:
  软柿子床上恢复出货 ⇒ flash 保住(2.4× 省费);仍零出货 ⇒ 地板实锤,升 pro。

### a1big1 — 点火实验(设计)
- pilot30 × **4 轮**(R0+3 演化轮=旱灾签名窗口)× K=8 × pass@2 × 全 LLM × 论文
  prompt × retry 2 × replay 120s;预估 ¥55-70(可支配 ¥70,地板 ¥30 守住);
- 读数:ship 数 >0 = 点火成功(旱灾系床构成,flash 无罪)/ =0 = 地板证据加强
  (升 pro 试验进入议程,需充值)。

### a1big1 — 执行记录:R1 中段被会话事件误杀(Jul-27,无效跑)
- 时间线:launch 15:54:26 → R0 结算 16:43(~50 min)→ R1 管线 16:50-17:04 →
  C-R1-01 门评 17:04-17:54(50 min)→ **诚实拒 ROUNDTRIP_L2**("new tool was never
  invoked during candidate evaluation")→ C-R1-02 门评 17:54 起 → **18:42:15 进程死**。
  死因=Claude 会话 /compact 连带终止后台 shell(外因,非代码缺陷);无 resume 机制,
  R1 未结算。实测花费 ¥103.01 → ¥91.81 = **¥11.20**。
- **免费带回的情报(全部落盘 runs/a1big1,留作 forensics)**:
  1. **R0 基线 pilot30 = 15/30(pass@2 50%)**:11 题 2/2、4 题 1/2(软柿子:
     023e9d44 / 23dd907f / 42d4198c / 6b078778)、15 题 0/2——扩床确实带进可翻余量;
  2. R1 Planner(论文 prompt)聚出两大失败簇:封锁源(6 题,tools+config 桶)/
     预算耗尽不合成(3 题,processor 桶),与 pilot12 诊断同构但簇更大;
  3. C-R1-01(bing_search_tool + SystemPromptProcessor,tools+prompt 双桶)过 replay
     (经 retry_01)后死于 L2:**adoption-coupling(prompt 提及新工具)不足以保证
     flash 实际调用它**——M-22 乙机器自证按设计诚实拒,该规则首次实战裁决;
  4. C-R1-02(ForcedAnswerSynthesisProcessor,processor 单桶)被杀时门评第 48 分钟,
     判决未出;
  5. M-22 Critic 路由指令实战生效:critic_review 逐字 "not grounds for rejection per
     runtime rule",L2 证据裁决正确下放确定性门。
- 台账裁定:a1big1 计为**无效跑(外因中止)**,不进效果口径;点火判决顺延 a1big2。

### a1big2 — 重启失败:WMI 启动链 ~2.5 分钟无声死亡(Jul-27,无效跑)
- CLI 与 a1big1 逐字相同(仅 --run-tag);HEAD=9519f82(与 a1big1 的 7bd24cd 间隔
  提交均为默认关闭的加法特性+文档,693/0,行为等同);18:50:58 起跑,**日志冻结于
  18:52:30**,无 traceback——硬杀特征;
- 排查:Defender 零检测(MpThreatDetection 空、operational 无 1116/1117)、Application
  日志无 python/WER 崩溃事件 ⇒ 最可疑=**WmiPrvSE 宿主回收连带其子进程**(WMI
  `Win32_Process.Create` 启动法本身不可靠),该启动法弃用;折损微量(R0 前 2.5 分钟);
- **勘误(主循环自误)**:18:56 的"心跳确认正常滚动"实为误读——所读日志(47179 字节
  /18:52:30)当时已冻结、进程已死。教训:**健康判定必须看增长(两次采样的尺寸差),
  不得凭单次时间戳**。

### a1big3 — 点火实验第三发(Jul-27 18:58:23 起,进行中)
- 启动法 v2:**schtasks 计划任务**(`HarnessX_a1big3`,父链=Schedule 服务,系统常驻
  无回收问题);启动器加 `LAUNCHER_START` / `LAUNCHER_EXIT code=%ERRORLEVEL%` 标记,
  下次可直接区分"被杀"(无 EXIT 标记)与"自行退出"(有码);
- 哨兵 v2:改监日志**增长**(240s 周期,连续 3 次零增长=12 分钟冻结才报警,不依赖
  PID,也不受会话事件影响判定);
- CLI 仍与 a1big1 逐字相同(--run-tag a1big3);起跑余额 ≈¥91.5(a1big2 折损计内);
  预算口径不变:全程预估落点 ¥25-40,可支配 ¥70 内,地板 ¥30 安全。

### a1big3 — 执行记录:schtasks 默认设置第三杀(Jul-27,无效跑)
- 18:58:23 → **20:21:06 整树被终止**(1h23m):无 LAUNCHER_EXIT 标记(cmd 壳都没活到
  写标记)+ 任务 Last Result = **0xC000013A**(CTRL+C 式控制台终止)+ 任务带 schtasks
  默认 `StopIfGoingOnBatteries=true` 且本机**为笔记本(有电池)**;两个等可能凶手:
  ①电源切到电池 → 计划任务服务停任务;②schtasks 用户会话任务**弹出可见黑色 cmd 窗**,
  被误关。花费 ¥91.67 → ¥85.09 = **¥6.58**。
- **免费带回**:a1big3 的 R0 完整结算 = **16/30(6 题 1/2)**,与 a1big1 R0(15/30,
  4 题 1/2)互验——**pilot30 基线 50-53%,床可复现性两点确认**;
- 排雷:旧任务遗留 23:59 触发器(会 --clean 重启 a1big3 并抹掉尸体)已删除。

### a1big4 — 点火实验第四发(Jul-27 20:38:12 起,进行中)
- 启动法 v3(三杀三防):`Register-ScheduledTask` 无触发器任务,设置显式
  `AllowStartIfOnBatteries + DontStopIfGoingOnBatteries + ExecutionTimeLimit=0`
  (拆电池雷);action = `wscript //B` 调 vbs 以 **0 窗口**拉起 cmd 启动器
  (拆误关窗雷);无触发器(拆定时炸弹);哨兵仍为日志增长制;
- CLI 与 a1big1 逐字相同(--run-tag a1big4);起跑余额 **¥85.09**;45s 增长验证
  +12.7KB 通过;**运维要求已告知用户:今晚保持插电、不合盖(睡眠会挂起网络)**;
- 三次事故累计折损 ≈¥17.9(其中 a1big1/a1big3 带回两份 R0 基线与 R1 管线情报,
  非纯损失);点火判决预计 a1big4 出。

### a1big4 — 完跑与判决(Jul-28 凌晨;花费 ¥85.09→¥59.78=**¥25.31**)
- **完整走完 R0-R3,零出货,idle=3 触发 patience 与轮数上限同时到点**。曲线(pass@2):
  53.3 → 53.3 → 46.7 → **40.0**(final−peak=**−13.3pp,且全程零上线**——纯环境方差/
  漂移,240 attempts 中 **130 次预算耗尽(54%)**);分层:L1 81.8% / L2 18.8% / L3 0%。
- **判决 = 不定判(inconclusive-by-contamination),worker 能力读数被污染**:六个候选
  名额里**只有 1 个真正进了门评考场**(R1 C-R1-02:零改进+回退4,merit 拒=对 flash 的
  弱负证据 n=1)。其余五个名额的死因与 worker 无关:
  1. R1 C-R1-01:meta 空手死 ×3(写完模板不交 config.yaml,"End of turn";时间帽 10000s
     只用 284.7s=3%,重试简报置顶验尸仍复发——**文本契约衰减 n=3 实锤**);
  2. **R2、R3 整轮被 OURS 问责规则废弃(4 个名额)**:"regressions were neither handled
     in tasks_at_risk nor explained"。**R2 的触发清单来自 R1 被拒修订的门评回退(被拒
     候选从未进主池,其回退是否应问责=规则过度延伸);R3 的触发清单(023e9d44/08c0b6e9/
     4b6bb5f7/65638e28)全部是主池自身轮间翻转——零出货下配置未变,翻转=纯方差。
     ⇒ 病理互锁确诊:床方差 → 台账记"回退" → 问责规则硬门 → 整轮作废 → 零出货。
     此为**我方桥接的病理,不是论文机制或 flash 的证据**。**
- **瓶颈栈排序(取代原二元判决)**:①OURS 问责规则×方差互锁(4/6 名额)>②meta 合规
  (1/6)>③环境(54% 预算耗尽+无 ship 漂移 −13.3pp)>④worker 能力(n=1 弱负)。
- **决策**:**暂不升 pro**(×2.4 买不到当前瓶颈);先行免费/廉价修复三件套——
  F-A **交付拦截器**(end_turn 缺 config.yaml 当场弹回热上下文补交)、
  F-B **问责规则修龄**(只对"已上线变更引发的回退"启用硬门;被拒候选回退与无 ship
  轮间翻转降级为 strategy concern;或预填 tasks_at_risk 应答模板)、
  F-C 环境包(搜索 API/网页闸门/缓存,54% 预算耗尽的主治)——然后 **a1big5 复测**
  (预估 ≈¥25)拿干净 worker 判决。M-22 机器自证本轮再次实战生效(certified_processor_replay=1)。
- 论文资产入账:契约不遵守分类学 n=5(空手死×3+问责无视×2 轮)、"文本契约衰减,须
  机械契约"证据链、**无 ship 漂移 −13.3pp = 单配置轮间噪声地板实测**(解读论文 Global
  臂后期退化时的必要对照)、seesaw 门正确拦截有害候选的实战样本。
- 点火四部曲总账:a1big1 ¥11.20 + a1big2 ¥0.14 + a1big3 ¥6.58 + a1big4 ¥25.31 =
  **¥43.23**;余额 **¥59.78**,地板 ¥30 未破。

### 修复三件套施工(Jul-28 下午,coder 实现+主循环验收,commit e45d48d)
- **W1 搜索后端**:重大更正——上游 `web_search.py:354` 本就带 SerpAPI/Tavily 正规 API
  后端,四场点火全程 keyless 跑在降级爬虫链上(坏环境半自找);经济选型定 Serper
  (serper.dev,$1/千次,与 SerpAPI 是两家),repo 不原生支持故新增
  `harnessx/tools/contrib/serper_search.py`(同名同 schema 换后端,worker 无感知;
  `__hx_target__` 保证候选继承);key 已实测(验证查询首名命中昨夜 403 整夜的正确页面);
- **W2 交付弹回** + **W3 问责修龄**:详 SPEC §7.15;W3 对 a1big4 R2/R3 实例地面真值
  验证(两轮均正确降级放行);
- 测试 693→**733 全绿**(主循环亲跑);默认全字节等同;三新旗标 provenance 记录齐。

### fixsmoke1 — 三件套点火 smoke(Jul-28 16:16 起,进行中;用户令"先跑smoke试试")
- calib6 × 2 轮 × K=2 × 全 LLM × 论文 prompt,**三旗全开**(serper / bounce on /
  shipped_only)+ SERPER_API_KEY;脱会话 SOP 启动,哨兵在位;
- 早期验证(起跑 2 分钟):部署配置实锤 WebSearch=custom serper 路径、lock provenance
  正确;出现 1 次设计内回落(Serper 空结果→原链兜底,403 仅 2 条 vs a1big4 风暴级)。
- 验收点:①403 比例大降 ②管线完整走通 ③shipped_only 不误废轮 ④(机会性)bounce 首秀。
- **结果(16:29 完跑,LAUNCHER_EXIT code=0 首次实证退出标记;花费 ¥0.72)**:**PASS**。
  ①**R0 = 6/6(pass@2 100%)**——同床历史(爬虫链)33-67%,环境修复的抬升幅度剧烈;
  全程 403 仅 8 行(a1big4 风暴级)、Serper 零 API 异常、预算耗尽 7/24;
  ②管线完整:R1 走到 **selective-invocation no-op(a_t=0 < α=0.5)**——R0 全解无可
  消化失败,论文 §4.3 选择性调用机制按设计短路,M-18 首次实战正向触发;
  ③W3 未到硬门时机(无候选)但也无误触发;④W2 未遇空手死(无从弹回)——两者留待
  a1big5 实战;⑤serper 下 h0.config_sha256 变化如实入 lock(环境谱系新起点)。
- **推论**:环境修复实锤"坏环境是主混淆源"假设的前半(403/预算耗尽大降);pilot30
  上的复测(a1big5)现在才有资格给 flash 干净判决。等用户令。

### a1big5 — 干净复测(Jul-28 16:33:09 起,用户令"跑!";进行中)
- CLI = a1big4 逐字 + 三旗全开(serper / bounce on / shipped_only)+ SERPER_API_KEY;
  起跑余额 **¥59.06**;HEAD=2563b6d;脱会话 SOP,哨兵在位;
- **预注册判读**:①R0 基线预期 >50%(环境抬升,两次 keyless R0=15-16/30 作对照);
  ②预算耗尽率与 403 相对 a1big4(54%/风暴)的降幅=环境假设后半的定量证据;
  ③ship>0 ⇒ flash 无罪进正式实验;仍 0 ship 且无环境/规则背锅 ⇒ 地板实锤,pro 议程
  重启(×2.4);④W2 弹回与 W3 修龄的实战首秀(30 题床必产空手死/翻转);
- 预估 ¥20-30(Serper 减少满步烧钱,或低于 a1big4 的 ¥25.31)。

### a1big5 — 判决:点火成功,三轮三船,史上最佳跑(Jul-28 20:0x 完跑,¥21.97)
- **曲线(pass@2)**:R0 73.3 → R1 63.3(探索期回撤)→ R2 73.3 → **R3 80.0 =
  final=peak,drift 0.0**——升不退化,正是论文 Ensemble 臂签名形态(n 小仅作形态
  证据,不作幅度声明);分层 **L1 100% / L3 100%(3 道媒体钉子户全翻)** / L2 62.5%。
- **三轮三船**:R1 V0→FORK(V1 生,prompt 桶 gaia_agent_v1.j2,翻活 00d579ea 等死题)
  → R2 V0→APPLY(C-R2-02)→ **R3 V1→FORK(V2 生)= 二代演化**;池 1→2→2→3,
  路由命中 72.5%,R2 起床被真实瓜分(V0 11 题簇 11/11,V1 19 题簇 11/19,画像分化);
- **四个预注册读数全部兑现**:①R0 73.3% vs keyless 50-53%(环境抬升 +20pp);
  ②预算耗尽 54%→**35%**,infra failures **0**,403 个位数;③**ship=3>0 ⇒ flash 无罪
  终审,pro ×2.4 议程永久关闭**;④W2 弹回全程零触发(环境修好后 pro 零空手死——
  修环境连带治了 meta 交付病),W3 无误杀(R1 回撤未废轮);
- 点火系列总账:五跑 ¥65.2(a1big1-5),余额 **¥37.09**;**结论:S1 正式实验绿灯,
  worker=flash,等预算到位即冻结开跑**。
- 附:decomp 前提判据②(变体间按簇对比度)首批真数据已在本跑产生,S1 判读时并入。

### 双盲流程审计(Jul-28 晚,用户令;A 论文侧/B 代码侧/C 仲裁,三 agent 隔离)
- **设计**:A 只读 PDF(禁代码/文档)、B 只读 .py(禁论文/一切 .md/git log)、C 先盲
  仲裁两图再开台账对账——检验 M-xx 申报完备度,防申报清单锚定;
- **判决(FLOW-DIVERGENCE-VERDICT.md,commit 95375d5)**:23 条偏差,~83% 已申报
  (台账质量高);**NEW ×3 全部藏在 K>1 的 seesaw 语义里**(K=1 探针原理上测不到,
  这正是逃过此前所有审计的原因):
  1. **NEW-1(高危)**:回退基线=全局跨变体 ever_solved,改进基线=per-variant,
     不对称——变体 k 对"任何变体解过的任务"背负不回退义务;**S1(K=8)必然显形,
     冻结前须用户终裁基线读法** → 已立 **M-23**;
  2. NEW-2(低):R0 纯基线轮 ⇒ num_rounds=15 实得 14 适应轮(off-by-one)→ **M-24**,
     S1 冻结时选 16 轮或申报换算;
  3. NEW-3(中):门判定 before/after 来自两条独立 pass@2 采样流(决策层方差,
     非 M-08 报告层)→ M-07 尾注扩写;
- MISDECLARED ×1:GAP-AUDIT §1 "per-variant seesaw 范围=verbatim" 过度声明已勘误
  (测试集层 verbatim、基线层双读);台账过期 ×3:M-12/M-16/M-18 已按实跑事实
  加 [更新] 注记(主循环以 runtime 知识修正了 C 的保守草稿:全 LLM 已在 7 场实跑
  盖章,非"code-present, not-live-tested");DEVIATIONS §4 加 CORRECTION 横幅;
- 副产品:A 侧 15 条 UNSPECIFIED "几乎逐条命中 M-xx"=台账预言力的独立确认;
  B 侧三条意外设计(账本由结算后全量流垄断/五关实三关/α 双默认退化)全数为真。
- **S1 冻结新增两决策项(用户)**:①M-23 回退基线三选(全局/仅本变体/本簇);
  ②M-24 轮数(16 轮对齐 vs 申报 14 适应轮)。

### 断点续跑机制落地(Jul-28 晚,coder 实现+主循环验收,commit 86ff8d1)
- `--resume <run>`:轮边界重建(pool_state 谱系回放 SuccessLedger、active_pool 快照
  重建 config 路径、stale-final-ship 侦测);**lock 护栏含 provenance_warnings 比对**
  (六个旗臂全在其中,防"参数漂移续跑");legacy 歧义 fork 配对拒绝不猜;默认字节等同;
  测试 733→**755 全绿(主循环亲跑)**。详 SPEC §7.16。正式实验 15 轮长跑的断电保险就位。

### 会话成本合计(实测)
¥194.14 → ¥103.01(**¥91.13**:十七个 run;a1big1 另计)

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

### decomp 方法双路文献扫描 + v1 终裁建议(Jul-29,用户令"先思考方法/深搜 25-26/制定骨架不写细节")
- 两路 researcher(A 方法扫 / B 占位核验)报告落盘:`DECOMP-LITSCAN-A-METHODS.md` /
  `DECOMP-LITSCAN-B-OCCUPANCY.md`;设计文档新增 §7 终裁建议 + §7.1 臂梯 + §8 骨架。
- **A 判决:D1-lite 站得住**(25-26 无方法同时满足简单+GAIA 实证+免训练;唯一类型化
  路由 Uno-Orchestra 需 SFT 61k+GRPO 且精度输 AgentOrchestra 1.4pp);**JoyAgent 消融
  纯分解 70.3 < 单体 ReAct 71.5(GAIA)= "分解单独无用"第三证 ⇒ E0 门升承重设计、
  论文头条钉臂间差**。
- **B 判决:α(持久演化池分工)= 部分占**——AOrchestra L3 亲验 fresh-spawn 即弃
  (原句 "Each SubAgent runs in a FRESH container…previous work will be lost"),
  幸存缝 = seesaw 演化池上的 (variant×type) 路由分工;**β(池感知动态分解)= 部分占
  近拥挤**——AOP/Topaz/FlyRoute 成分皆占、合取未占,降维为 B3 旗标臂(画像限 S1
  冻结统计,禁臂内账本条件化=鸡生蛋+非平稳)。
- 臂梯 v1.1:新增 **B0 = 分解 + 纯 h0**(fresh-spawn 类比臂,`--decomp-pool-from`
  缺省即得,零建设成本)⇒ B1−B0 = 池底座收益 / **B2−B1 = 指派收益(头条)** /
  B2−B0 = 演化池 vs 即弃头对头(port+beat 要件)。
- 🔴 撞车监测(高):Wentao Zhang/Bo An 组两半已齐(AgentOrchestra 2506.12508 +
  Autogenesis 2604.15034),尚无整合单篇(至 2026-07);最好防御 = 8 月内跑完 B 臂。
- 纪律事件:B 路 PDF 摘要器对 FlyRoute 的 leading 提问虚构三条肯定答,经中性提示取
  verbatim abstract 纠正为"整 query 路由、无分解"——"占位/证伪通道必须一手 verbatim"
  再添实证案例。
- **状态:方法终裁与 coder 开工均待用户令;本批零付费跑,零上游改动。**

### S1 冻结裁定落锤 + M1 decomp 开工(Jul-29 晚,用户逐项裁决)
- ①方法按建议(D1-lite 头牌 + B0/B1/B2 臂梯 + B3 旗标二期)②**开工令**
  ③step-countdown 关 ④M-23 开关化 `--regression-baseline {global,per_variant}`
  默认 global + per_variant 消融(论文两读张力,用户规则"没明写就开关+对照",
  本簇读弃)⑤`--num-rounds 16`。详 SPEC §7.17;DEVIATIONS M-23 行已记裁定。
- M1 构建工单发 coder:subtask_pipeline.py + `--decomp-eval` 接线 + 测试;基线 755;
  硬约束=零上游改动/默认字节等同/零网络测试/不 commit(主循环验收后提交)。
- M-23 接线排 M1 之后(同 run_variant_pool.py 防冲突),S1 冻结前完成。

### labsmoke1 — 实验室端点部署冒烟(Jul-29 17:28–17:41,用户令"部署+跑 smoke";**PASS**)
- 背景:用户提供导师本地部署 DS V4(LiteLLM 代理 litellm.yangtzeailab.com,vLLM 0.24.0
  TP4 后端),令模型名全小写。**部署=零代码**:litellm 原生 `DEEPSEEK_API_BASE` 环境
  变量回退链(venv 亲验 deepseek/chat/transformation.py:261),launcher 仅换两个 env
  (lab key + base);CLI 与 fixsmoke1 逐字同(仅 run-tag);`deepseek-v4-flash`/`-pro`
  双档均在代理注册,模型串本就小写。
- 前置探针 5/5 PASS:参数级 / env 级 / **工具调用**(vLLM function calling 开,命门)
  / pro 档 / reasoning_effort=high litellm 透传(思考链返回);延迟 1–2s。
- 冒烟从 6de5e86 隔离 worktree 跑(主树 M1 coder 施工中);床数据系 gitignore,worktree
  需手工复制(运维注意)。结果:LAUNCHER_EXIT code=0,**13 分钟**(=fixsmoke1 同带);
  **R0 5/6(83.3%)→ R1 6/6(100%)**,final=peak;24 attempts / **0 infra fail** /
  预算耗尽 5/24(fixsmoke1 7/24 同带);候选 0(双轮短跑无候选相,与 fixsmoke1 同,
  meta 候选管线深度留 S1 前置检查);现金成本 ≈¥0(lab 端点)+ Serper 零头。
- 🔴 **provenance 缺口(S1 前必修)**:lock `models.api_base="unresolved"`——env 注入对
  lock 不可见,官方/实验室两纪元 lock 无法区分,续跑护栏认不出换端点 ⇒ 把
  `DEEPSEEK_API_BASE` 纳入 lock env 捕获(base URL 非密可记,key 永不入 lock),
  **并入 M-23 接线工单**。
- 环境代际:本跑起开"实验室端点纪元";官方 API 时代数据(fixsmoke1/a1big1-5)只作
  跨纪元形态对照,不作同口径幅度比较。产物已归档主树 runs/labsmoke1,worktree 已拆。
- 意义:**S1 的现金成本降至 Serper 零头,8/3 资金死线实质解除**;剩余门=导师 usage
  确认(实验级负载许可)+ M1/M-23 落地 + S1 冻结包 + 用户开跑令。

### M1 分解层构建验收(Jul-29 晚,coder 实现 + 主循环验收;755→**829 全绿亲跑**)
- 交付:`experiments/variant_pool/subtask_pipeline.py`(772 行,import-pure,runner
  依赖注入)+ run_variant_pool.py 接线(**+351/−0 纯加法**)+ 两测试文件(+74 条);
- 验收抽查:diff 形状(0 删除)/ 829 亲跑 12.9s / 字节稳定专测在位 / 主分发位置
  正确(setup 后、lock/recipe 前,与 --resume 硬互斥)/ harnessx/ 零改动 /
  --regression-baseline 零触碰(留下一工单);
- 关键语义裁定五条记 SPEC §7.18(GT 哨兵防子任务级 LLM judge 误触发 / 冷启动回退 /
  确定性轮转 / 去重记信用 / decomp_plans.json 跨臂重放通道);772 行超 350-550 估算
  系 docstring 密度对齐 repo 规范,验收接受;
- 下一工单(即发 coder):M-23 `--regression-baseline` 开关(§7.17-3)+ lock 捕获
  `DEEPSEEK_API_BASE`(labsmoke1 provenance 缺口),S1 冻结前落地。

### M-23 开关 + 端点纪元捕获验收(Jul-29 深夜,coder 实现 + 主循环验收;829→**853 亲跑**)
- `--regression-baseline {global,per_variant}`(默认 global 字节等同,engine 条件转发;
  per_variant 复用 TaskEval.before 的 per-variant 采样语义,零新 ledger 方法)+ 第七旗
  入 provenance;lock env 新字段 `deepseek_api_base`(URL/官方哨兵;跨纪元续跑阻断,
  旧 lock 向后兼容);
- 验收:853/0 亲跑;**11 删行逐行核**=签名/调用点/注释改行,零行为删除;harnessx/
  与 decomp 路径零触碰;详 SPEC §7.19。**S1 前代码建设至此清零。**

### resumedrill1 — resume 实弹演习(Jul-30 凌晨 02:48–04:52,用户令"跑";**PASS 5/5**)
- 设计:calib6 × 4 轮 × K=2 × lab 端点 × **chain 后端(零 Serper 消耗)**;R2 中段
  主循环故意整树杀灭(cmd + python×2,无 EXIT 标记 = a1big3 式外杀签名)→ 计划任务
  拉 `--resume` 续跑;
- **五项验收全过**:①轮边界重建(日志实证 `continuing from R2 (2 settled rounds)`)
  ②候选管线用 R1 轨迹精确接续 R2,R0/R1 零重花 ③lock 护栏放行 + **端点纪元字段
  首次实写实比**(env.deepseek_api_base = lab URL)④续跑 69 分钟补完 R2–R3,
  LAUNCHER_EXIT code=0 ⑤lock sha 不变(report 头 = resume_provenance.prior_lock_sha256
  逐字同)+ resumed_at_round=2 落盘 + R0–R3 逐轮 pool_state 链完整;
- 已知限实证(§7.16 声明行为):续跑后 RunReport 曲线仅含 R2–R3——**S1 若发生续跑,
  全曲线由逐轮 pool_state 拼接(分析侧动作,数据无损)**;
- 附带收获:**853 代码首次真跑通过**(M1/M-23 默认关路径 + lock 新字段常开路径);
  成本 ¥0(lab 端点 + chain);chain 后端轮速 ≈ serper 的一半(演习专用,正式跑不用);
- **结论:断电保险实弹认证完成;s1k8 点火唯一闸 = Serper 充值。** auto-resume
  看门狗(哨兵检死→自动拉续跑任务,仅 --resume 永不 --clean、限 2 次、留审计行)
  已向用户提案,待"加"字。

### auto-resume 看门狗落地(Jul-30 凌晨,用户令"auto resume加入")
- `experiments/ops/run_watchdog.ps1`(参数化 RunTag,可复用于 s1k1/B 臂;无密钥,
  入库):**仅进程确死才动手**(python 进程消失 + 最新 EXIT 标记非 0 或缺失),
  活跑慢跑永不杀;动作 = 拉预注册 `HarnessX_<tag>_resume` 任务(--resume 永不
  --clean);**自动续跑硬帽 2 次**(state 文件持久,看门狗自身重启也不超帽);
  完跑 exit 0 自动收队;笔笔审计入 `<tag>.watchdog.log`;
- 双分支 DryRun 验证:resumedrill1(有 EXIT 0)→ 收队;伪造死跑(无标记)→
  报 "AUTO-RESUME attempt 1/2" 不真启;
- s1k8 三件套任务全 Ready:主跑 / 续跑 / 看门狗;点火程序更新为双 Start(主跑 +
  看门狗),已回填 S1-FREEZE §5。

### s1k8 — S1 正式实验 K=8 臂点火(Jul-30 05:07,用户令"serper充了,按3并发跑")
- 前置四件套全绿:Serper 付费额度到账(用户确认)+ 导师侧反馈正面 + 冻结包
  S1-FREEZE(读数/判读规则预注册)+ 用户开跑令;HEAD=63f23fa,853 测试基线;
- 配置 = 冻结包 §1 逐字:pilot30 × 16 轮 × K=8 × pass@2 × 并发 3 × lab 端点
  (DEEPSEEK_API_BASE env)× serper/bounce/shipped_only 三旗 × regression-baseline
  默认 global × step-countdown 关;
- 点火验证:双 Start(主跑 + 看门狗)后日志 45s +15.3KB;**看门狗审计首行落盘**
  (maxRestarts=2);V0/config.yaml 实锤 `harnessx.tools.contrib.serper_search`
  自定义工具路径(付费 Serper 真挂上,非 chain);
- 预计 10–14h 完跑;续跑/看门狗/lock 端点纪元护栏全部实弹认证在先(resumedrill1);
  s1k1 待 s1k8 验收后次夜发。
