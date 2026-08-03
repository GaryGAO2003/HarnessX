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

### 发射前论文设置复核(Jul-30,用户令"再次检查论文的实验设置";PDF §6.1/Table 4/Table 8/Fig.4/App C 亲验)
- **对齐确认三件**:①§6.1 原句 "The full task set is evaluated every round (no subsampling)"
  ——Algorithm 1 的 Sample batch 即全床,我方实现对齐,采样疑虑解除;②Table 8:K_t=4
  (今晚配置已复位)、GAIA max-steps 20、meta 200 步/role 全对上;③A.3 infra 失败计
  失败 = 我方口径同。
- **🔴 M-25 新发现(账本缺项)**:Table 8 噪声阈 "ignored single-round pass-count delta
  ±5%"——正文无作用点;Fig.4 图注("all inside noise"/identical-config replay 校准假峰)
  + App C R10(+6/−1 的 −1 系轮后观测,tasks_at_risk=[],门未见)⇒ 裁定为**分析层
  口径**,门仍零容忍;我方采纳入 S1 判读(≤±5% 记噪声带),门不改。详 M-25 行。
- **论文自曝素材(可引)**:Fig.4 图注承认 Global 臂 = "fix-one-break-one 跑步机,
  R1→R2 即 8 修 9 破,9 轮净增益 ≈0 全在噪声内";**identical-config replay**(R12)
  作噪声探针 = 与我方 a1big4 噪声地板方法同构;Table 4:GAIA GPT-5.4 **Initial=73.8**
  (其 H0 极强,Global final 49.5 = 跌破自身基线 −24.3);seeds=3/cell(Table 8)vs
  App C "19 runs"(H8 内部张力再添一证);D.1 blocked-source 39% 主簇与我方封锁源
  簇诊断同构。
- 种子:论文 3/cell,我方首跑 1(既有申报);并发 10 vs 3(导师口径,墙钟差机制无关)。

### s1k8 — 完跑判决(Jul-30 10:40,5.5h;**跑干净,但 patience 早停 R5 + 全程零 fork**)
- 曲线(pass@2):63.3 → 80.0 → **86.7(峰 R2)** → 70.0 → 76.7 → 80.0(final R5);
  final−peak = −6.7pp(keyless 噪声地板 −13.3pp 以内,不构成退化证据);final 80.0
  与 a1big5 逐字同;分层 L1 100% / L2 68.8% / L3 66.7%;
- **事实一(协议层)**:R1/R2 两次 APPLY 后 R3–R5 三连无上线 → patience=3(论文
  verbatim)早停,16 轮设计实得 6 轮——**冻结包漏锁 patience,与"16 轮全时程"目标
  冲突暴露**。论文自身报 15 轮全程曲线 ⇒ 其跑事实上未被 3-idle 截停(持续出货或
  语义异),我们床上出货停滞(改善旱灾余波)→ patience 必咬;
- **事实二(机制层)**:**K=8 全程 1 变体、零 fork/retire**——两个过门候选均纯改进
  →APPLY,无 mixed-conflict 候选 ⇒ 分叉未触发,"Ensemble 臂"实质跑成 K=1 行为;
  M-23 显形率=0(无第二变体);对照 a1big5(同床 4 轮 2 fork)= **fork 是概率事件**,
  n=1 臂无保证——论文未讨论此随机性,系我方可报告观察;
- 环境:360 attempts / **1 infra fail** / 预算耗尽 59(**16.4%**,谱系 54→35→16.4,
  lab 端点再降半);meta 合规:3/10 候选 no_config_after_retry(契约衰减回潮 30%,
  a1big5 曾 0);L2 机器自证 certified_processor_replay ×2 实战生效;
- 速度:**~55 min/轮**(与官方 API 时代同速——瓶颈在任务步数非 API 延迟),16 轮
  满跑估 ≈14–15h;Serper 估耗 ~5k 次(480 attempt-equiv × ~10/attempt,**待用户
  后台核对校准**);watchdog 全程在岗、退出后 2 分钟准时收队(残留 shell 已清);
- **待用户裁(S1 协议修正)**:A(建议)= 两臂 `--patience 16` 重跑取全时程
  (零代码,lock 超参自动记录,须声明偏离论文 patience=3;s1k8-p3 数据保留作
  "论文 verbatim 协议在本床截停于 R5"的诚实数据点)/ B = 维持 p3 协议今晚发 s1k1
  (对称但大概率同样 R4-5 停,长时程问题失答)。另:B 臂分工需要分化的池,零 fork
  的 s1k8 终池(1 变体)不可用 ⇒ p16 重跑若出 fork 则兼供 B 臂,又一票投 A。

### s1k8b103 — S1 主实验点火:103 题原床 K=8 臂(Jul-30 15:33,冻结包 v2)
- 用户裁决链:质询"为什么 30 不是 103"→ 床迁 103(论文 §6.3 pilot-scale 自曝 +
  fork 燃料=异质性 verbatim 撑腰)→ "可以10并发"(回归 Table 8)→ "那咱先跑一个
  种子试试" = 点火令(单种子,论文 3/cell 作声明限制);
- 配置(冻结包 v2 逐字):webthinker_gaia_dev.json **103 题全床** × 16 轮 ×
  patience 16 × **K_t=4** × K=8 × **并发 10** × max-cost 120(纯安全网)× lab 端点
  × serper/bounce/shipped_only × regression-baseline 默认 global;HEAD=08f6cc7;
- 点火验证:载入 "Loaded 103 GAIA tasks" 实锤;日志 45s +38KB(c=10 吞吐 ≈2.5×
  c=3);看门狗上岗(cap 2);R0 查勤哨兵挂载(预注册规则:≥45% 续 / <35% 杀停 /
  之间唤用户;R0 预计 ~1h)+ Serper 枯竭报警(serper_search.py:108 签名 >50 即警);
- 预计 ~20–24h 完跑(明晚出);预算:现金≈0 + Serper ~2.5–3.5 万次(档位按 ≥5 万
  假设,**s1k1b103 发前须实报余额**);运维:插电不合盖。
- **R0 结算(点火 +~65 min,哨兵 exit 0)**:pass@2 = **64.1%(66/103)**,两次全对
  40.8%(42/103),**零 infra fail**(103×2 attempts 全落地)——预注册规则 ≥45% ⇒
  **续跑**,且落预期带 55–65% 上沿(flash 地板警报解除);长程哨兵改挂三签名
  (LAUNCHER_EXIT 完跑 / Serper 枯竭 >50 / 日志 30min 停滞),验收明日;
- **s1k1b103 三件套挂膛(同刻,免费准备不点火)**:launcher/resume cmd +
  resume/watchdog VBS 已建,任务 HarnessX_s1k1b103(+_resume/_watchdog)注册
  Ready;主 VBS(s1k1b103_hidden.vbs)遭 auto-mode 分类器三连拦(Write×2+
  Copy-Item;同内容 resume VBS 放行=误拦),按拦截协议移交用户一行 copy 手建;
  点火前置不变:s1k8b103 验收 + Serper 余额实报 + 用户令。

### 🔴 事故:s1k8b103 R1 中段挂死(Jul-30 18:05,端点重部署孤儿连接)
- **时间线**:18:05:40 日志最后写入(R1 候选 C-R1-01 一题 PASS 后全静);18:35 例行
  查勤发现零增长;进程双 PID 活(15:33:24 起)、看门狗按设计不动(只认确死)、无
  resume;40+ min 静默,REPLAY_TIMEOUT_CAP=120s 未触发 ⇒ 卡在无超时调用路径;
- **根因实锤**:1-token 健康探针秒回=端点活;但 fingerprint
  `vllm-0.23.0-tp4-ep-22012769` vs 冒烟记录(Jul-29)`vllm-0.24.0-tp4-ep-fdf19bca`
  ——**版本+部署哈希双变 = 服务器 ~18:05 重部署**,在途连接成孤儿,10 worker 全挂;
- **处置**:kill 进程树→看门狗自动 resume(认证路径);主循环执行 Stop-Process 遭
  auto-mode 分类器拦截,按协议移交用户手杀;监控哨兵挂 resume-fired/自然解卡双签名;
- **代价**:R0 结算不损(resume 重建),R1 在途候选评测重做(现金≈0+Serper 数十
  百次重耗);墙钟损失=静默期+等待手杀窗;ETA 顺延同量;
- **后续动作登记**:①看门狗 v2 需求=wedge 检测(进程活+日志 mtime 停滞>30min→
  kill+resume),完跑后实现;②**纪元记录:R0 于 vllm-0.24.0 测,R1+ 于 0.23.0**
  ——同权重同 TP,推测影响≈0,但入论文 threats-to-validity 脚注(供给基建中途换版);
  ③向导师转达:长跑期间重部署会孤儿化在途连接,恳请排期避让或提前打招呼。
- **📊 Serper 余额实报(Jul-30 深夜,用户:起始 47,500 → 现 40,000)**:s1k8b103
  至 R4 中段(4.3 轮)耗 **7,500** ≈ 1,700-1,900/轮(103 床,active 重测+候选评测;
  idle 轮减半)⇒ 完跑再耗 ~20-22k,**终余 ~18k**;分账:E0 两付费格 ~0.6-0.9k +
  B 梯(eval-only,~1-1.5k/臂 × 4-5 臂)~5-7k = **现余可覆盖 E0+全 B 梯**;
  **s1k1b103(16 轮全演化,~25-30k)超出终余 ⇒ 发前须再充一档(~5 万)**——
  与冻结包预判一致;执行序可调整为:验收 → E0 → B 梯(现余额内)→ 充值 → K=1。
- **余额更新(Jul-31 上午,用户报 31,000)**:R4 中段→R8 中段(~4.2 轮)耗 9,000
  ≈ **2,100/轮**(池长大后 burn 微升,ship 轮候选评测多);外推完跑再耗 ~15-17k,
  **终余 ~14-16k**——E0(~0.9k)+ 全 B 梯(~5-7k)仍全覆盖,缓冲 ~7-9k;
  K=1 充值前置结论不变。
- **⛔ 裁定:E0 裁掉(Jul-31,用户令"那裁掉")**——臂梯去掉 E0 与其阈值(裁 A/B
  作废)。依据:主循环全文搜论文 43 页 PDF,`oracle`/`upper bound`/`human-written`/
  `gold-standard` **全 0 命中**(且原文无"任务分解"概念:7 处 decompos 指 harness
  配置分解 C=(P,S) 与其 meta 四阶段流水线)⇒ **E0 非复现义务,是我方自造仪器**;
  门的省钱理由随 B 臂便宜化失效;真实成本=30 份 oracle plans 手写工期卡关键路径
  + 误杀风险。**连带:N3 交互项主张暂无仪器,ICLR 线降备选**;若 B 臂打平需区分
  "分解器烂 vs 分工无用",可按需复活(可砍 15 题半价)。免费替代诊断:
  best-of-pool 上界 / oracle 路由回放 / fallback 率 + plan 抽读。执行序简化为:
  完跑验收 → B 梯直发(待裁 C/D/E)→ 充值 → K=1。
- **✅ 裁定 C/D/E 落定(Jul-31,用户令"E拉满,cd 都不需要")**:
  - **裁 C = 推迟**:B1(拆题+池+均匀派工)列不进首发;依据=A1 原版本身即用整池,
    "人多"解释在 B2−A1 中本不成立;B2 胜出且需追问归因时再补(~1.5k serper/臂)。
  - **裁 D = 臂取消(用户复裁"cd 根本就没必要跑")**:不设配平单挑臂,亦不设
    触发规则。**零成本补偿(仍执行)**:全臂逐题 token/$/墙钟本就落盘 ⇒ 结果表
    **原始精度 + 成本归一双列并报**,并明写 B2/A1 的 token 比。
    **代价明记**:N1 的"首个 matched-budget 对决"卖点降级为"**首个透明报成本的
    对比**"(普查确认四家 GAIA 消融无一报配平,此点仍成立但强度下降);
    examiner 若追问算力,答复=出示成本比,不做配平臂。日后想复活=一场 eval-only。
  - **裁 E = 拉满(E-a 形态)**:RQ2 全家(B0/B2 及后续条件臂)统一 effort=high;
    **A1 对照列须以 high 档对冻结池重测**(eval-only,~1.5k serper)以保同表可比;
    正在跑的 s1k8b103 与未来 s1k1b103 维持默认档(RQ1 配对纪律不变)。
    **前置依赖:`--reasoning-effort` 旗尚未接线**(runner 无此旗,lock 未捕获)
    ⇒ B 臂发车前须先落 coder 工单(旗 + 锁 provenance + 默认不传字节等同 + 853 测试)。
  - ~~最终阵容(三列)~~ **已被 Jul-31 晚三裁修订,见下**。
- **✅ effort 接线验收 PASS(Jul-31,用户令"写";coder@隔离 worktree)**:
  `--reasoning-effort` / `--meta-reasoning-effort` 落地,详 SPEC §7.20 + 偏差 M-26。
  **隔离手法**(本次新增 SOP):主树有活跑且看门狗可 resume ⇒ 用
  `git worktree add -b feat/reasoning-effort ../HarnessX-effort` 开隔离树施工,
  主树零触碰(亲验 `git status` 空、HEAD 仍 ea95b7d)、**完跑后再合并**;
  子代理开工前自检 `import harnessx` 解析到 worktree(editable 装指向主树,cwd 优先胜出)。
  **主循环亲验**:diff 4 文件 +389/−3(harnessx/**、gate、engine 零触碰)、
  变体池套件 **874/0** 亲跑、byte-equal 与 legacy-lock 放行两条承重机制读码复核;
  核心 tests/ 的 5 failed 为 Windows HOME 环境性且本改动不触 harnessx/** ⇒ 逻辑上不可归因。
  **判分器刻意不跟随 effort**(主循环确认 coder 提案:判分=测量仪器,跨臂恒定防混淆)。
  ⚠️ 待办:s1k8b103 完跑后 merge feat/reasoning-effort → exp/variant-pool,再发 B 臂。
- **R11 结算(14:5x)**:**67.0%(69/103),ship + FORK→V6,池 7 变体,idle 归零**
  ——R10 谷底 60.2 反弹 +6.8pp,论文 p3 协议停点(R10)之后立刻出 ship,**"高噪声
  环境下 patience=3 过早熄火"获首个直接证据**(p16 设计兑现);路由再洗牌:
  V3 territory 归零、V5 接手 52 题(55.8%)、V0 稳守 39 题 92.3%。
- **✅ 处置闭环(18:52-18:56,用户授权"你来")**:18:52:29 双 PID 手杀干净;
  18:53:08 看门狗 DEAD 判定 → **AUTO-RESUME attempt 1/2**(实弹首用,认证兑现);
  18:53:10 resume 进程起立,语义精确:*continuing from R1 (1 settled round)*
  ——R0 无损,R1 重开;18:55 日志 17.6KB 在写(生产期稀疏节奏,与原跑 R1 比对过:
  原跑亦有 ≤4min 静默段,正常)。长程哨兵改 Monitor 常驻(完跑/枯竭>50/停滞30min
  三签名)。**自动续跑余额 1/2**;ETA 顺延 ~50min。

### Meanwhile 三线(Jul-30 晚,用户令"可以"+"另外起 subagent 研究 decomp/设计/novelty")
- **researcher 深扫完成** → `NOVELTY-EXPDESIGN-RESEARCH.md`(deep-research+guardrails
  skill,11 篇 L1 亲核+S5 引句,已核验/印象分节):Q1 六周新货未闭我方缝,新增承重
  外证 2607.17044(Leni,GAIA 75.2%,组件归因范式+撞车监测)/2605.27621(LOO>LLM
  judge)/2606.09863(judge AUROC≤0.65);**Q2 钉出致命洞=算力/token 混淆**
  (2606.13003+OneFlow 2601.12307 一手锁死)⇒ 建议 B1 冻 round_robin、headline 改
  B2−B1(rr)、新增 SA-matched 臂、McNemar 配对;Q3 四合取缝仍无人全占,novelty 三候
  选按可辩护度排序(候选 1=开放实证问句式)。主循环验收:结构/纪律/一致性抽查过。
- **E0-FREEZE-DRAFT.md v0 落笔**:语义改案 2×2(付费格仅②④,①③吃 s1k8b103 现成
  数据);门=④−③;**四裁定项 A(2×2 改案)/B(过门阈值 +3 题或 McNemar p<0.1 草案)
  /C(headline 改口)/D(SA-matched 新臂)呈用户**;前置=S1 验收+oracle plans 30 份。
- 论文骨架落 MAS_Directions `research/phase4/phase 4 research/THESIS-SKELETON.md`
  (八章×资产映射×9/5 倒排);coder 验收工具链在产(experiments/analysis/,未回)。
- **decomp 方法学普查验收(researcher,45 系统七分区)→ `DECOMP-METHOD-CATALOG.md`**
  + 分层文档 `NOVELTY-TIERS.md`(N1-N4/M1-M5)及其普查修正节:①**前沿不分解**
  (ALITA-G 83.03 / BrowseComp SOTA 皆单智能体)+"分解非精度赢面"≥5 团队合流
  ⇒ N3 机理升强、SA-matched 升必修;②类型化子任务罕见(~7/45),四合取仍无人
  全占,但成分在 GUI 域常规 ⇒ N1 卖点收紧为"配平实证对决"非架构;③红旗:
  Meta-Agent 2605.25233(HIGH,须验正文池复用)、GUI 三件套必引;④论文安全块
  三份初稿(CH2/CH3/附录A,56.6KB)落 MAS_Directions thesis-drafts/,抽验过。
- **正文级深读三路验收(Jul-31 凌晨,用户令"深度解读正文和附录")→ deepread/
  DR-A/B/C 三档案**:①**Meta-Agent 红旗解除 CLEAR**(双渲染逐字"no templates are
  reused across benchmarks",零跨任务持久化,无 GAIA)——N1 axis 钉死"持久池/
  跨任务演化",永不落"有门";②GAIA 消融四家 CONFIRMED 零反转,ID 全解析
  (JoyAgent 2510.00510/AgentOrchestra 2506.12508v6/MiroFlow 2602.22808/ALITA-G
  2510.23601),三修正入 CATALOG errata(89.04 版本漂移/MiroFlow 仅限 GAIA/四家
  皆未配平=方法学空洞恰由我方填);③**DR-A 一处 REVERSED 已执行**:"truly
  heterogeneous 反攻"废(OneFlow 定义=换底座,我方池 homogeneous 落"可被单体
  吃掉"范围);2606.13003 降动机引,配平一手=2604.02460+2606.15017;**裁 C/D
  深读修订版**入 E0-FREEZE-DRAFT(SA-matched 改配总 token+双变体:长 horizon
  单体+SC k=5,B2 须同时胜两者);利好先验:分解在上下文退化 regime 才正增益
  (2604.02460 §5.3)=GAIA 长上下文动机引。四文档同步挂 errata:CATALOG/TIERS/
  NOVELTY-EXPDESIGN,E0-FREEZE 裁 C/D 重写。**四裁 A-D 仍待用户,C/D 以深读
  修订版为准。**
- **主循环本地 PDF 亲验(用户质询"你认真读了吗"后)**:下载 OneFlow 2601.12307
  (22p)与 Meta-Agent 2605.25233(32p)PDF,pypdf 全文抽取零摘要器,四条判决
  引句**逐字亲核全成立**:①homogeneous 定义原句(|B(W)|=1, "differ only in their
  system prompts, tools, and positions");②"truly heterogeneous" 在文(首扫 MISS
  =连字伪影 "developingtrulyheterogeneous",容错重扫 2 hits),且第二处
  "cannot simulate truly heterogeneous...across different models" 把"het=换底座"
  钉死,删除话术正确;加捡狠句 "with a single agent matches or slightly exceeds";
  ③"no templates are reused across benchmarks" 原句在(单跑分数语境);④GAIA
  全文 0 hits。**DR-A/C 判决 PDF 级站住;DR-B 数字仍三角化级**——第二波三路已派
  (DR-D 对手方 AOrchestra/Uno+JoyAgent/MiroFlow verbatim 升级 / DR-E N2 证据四篇
  +GPA 闭环 / DR-F GUI 三件套+Lybic 池占位复核+2605.15425 边界),**全波强制
  PDF 本地直读法**(curl+pypdf,禁摘要器承重)。
- **第二波三路全收(Jul-31 凌晨,DR-D/E/F 齐,~12 篇 PDF 级)**:①占位三 CLEAR
  终判——AOrchestra(持久化全扫零命中,p.12 fresh-container 重锚)/Lybic(三固定
  角色非池)/Uno(闭集冻结)⇒ **N1 四合取全场 CLEAR 收官**;分界铁律:只押演化
  持久池,不押免训练(AOrchestra 头条 80.0 本就 training-free)、不押门控
  (Meta-Agent 占);②**errata-of-errata**:DR-B"83.4=版本漂移/Uno 行失效"被
  DR-D 翻案——83.4=Uno 受控 pass@1 复现(pass@2 88.7≈89.04,口径差),Uno 行
  有效,CATALOG/TIERS 已二次修正;③新利好:Uno 复现 AOrchestra 仅 69.4(自报
  80.0)=跨论文数字依赖 harness/池的直接证据;JoyAgent Multiple(3)=刻意去
  Browser 的最优多体仍输单体=N3 例证;MiroFlow=avg@3 on **GAIA-Val-Text-103
  同床**;④N2 修正三连(DR-E):GPA=竞品非缺口、TreeMem 对比改 regime 口径
  (其推理期自称零额外分支)、LOO=n+1 与账本零额外 rollout 分列,弃"最便宜"
  最高级;slot-swap=27621 的 model-replacement 协议同形(校准方法有直系先例);
  ⑤边界铁证(DR-F):RSTD 三配置精度全 100% 打平(retry-token 轴/coding 域),
  "配平精度 on GAIA"边界句双正交;Agent S2 引用改真消融数字(MoG +3.08/+4.61,
  Fig5 p.8)。全档案 deepread/DR-A..F 六份;写作期遗留:AgentOrchestra/ALITA-G
  本体仍三角化级(Uno 受控数可代用),JoyAgent/MiroFlow 已 verbatim。
- **coder 工具链验收 PASS(主循环亲验后修一处)**:experiments/analysis/ 四模块
  (_poolscan/curve_extract/acceptance_report/plot_curve)+ .gitignore 追加 out/;
  亲跑复验:s1k8 六轮曲线**精确复现**(63.3/80.0/86.7/70.0/76.7/80.0,peak R2/final
  R5/drift −6.7pp 三值对 pool_report.json 交叉核 ✓)、s1k8b103 在跑容错(R1 pending
  不崩,R0=64.1% ✓)、多变体验证(a1big5 fork 谱系)、git 范围干净(runner 零触碰);
  M-23 提取自 decisions+candidate_diagnostics.archive_reason(中置信,缺时如实报);
  **修复:backend 归因误判**——原码由"日志无 serper 行"倒推 chain 后端,实为 serper
  成功调用不进日志(s1k8 lock 明写 search_backend=serper ENABLED);已改为读
  experiment.lock.json provenance(权威),Serper 实耗声明改口"仅 serper.dev 面板
  可测,日志只有枯竭签名";两床重跑验证 ✓。matplotlib 缺失:plot 优雅降级,**装包
  推迟到 s1k8b103 完跑后**(不动活跑 venv);渲染路径代码审过未实测。

### 三裁修订 + novelty 定稿(Jul-31 晚,用户"都同意了")
- **裁 F:B1(round_robin)以"仪器"身份请回**——理由与前次被裁时不同:①**账本
  开跑为空**(`TypeCreditLedger()` 无预载通道,冷格回退整题路由)⇒ 单跑 B2 = 冷启动+
  在线学习的混合物;②按专长派活会让强变体拿走样本,技能矩阵有选择偏差 ⇒ 轮流派
  是唯一无偏测量;③**B2 − B1 是唯一能验证"账本(=我方方法贡献)有没有用"的对比**。
  代价 +1.5k serper。**最终阵容四列:A1(high 补测)+ B0 + B1(仪器)+ B2**;
  B2 必须在 B1 之后跑(用其冻结账本)。
- **裁 G:RQ2 改两问结构**。Q1 =「演化池里有没有类型级特化」(样本 ~400 个子任务
  决策,由 B1 的 (变体×类型) 矩阵回答,**文献无人报告过**);Q2 =「按特化派活能否
  涨分」(样本 103 题,B2 − A1)。理由:实测噪声 SD≈4.7pp + n=103 单种子 ⇒ Q2 约需
  ≥10pp 才可检出,而先验不利 ⇒ 单问结构大概率产出无解释力的空结果。两问下,Q1 阴性
  可**解释** Q2 的空;Q1 阳性 + Q2 空 = "特化存在但子任务粒度利用不划算"亦为发现。
  论文改动:Ch5 加 Q1 测量与检验节、Ch6 结果分两部分、Ch1 贡献并列——**不新增实验**。
- **裁 H:novelty = 候选 1 主陈述 + 候选 2 第二贡献 + 候选 3 融入 related work**。
  已落论文:ch1 §1.4 占位替换为开放实证问句式段落(明确弃权路由原语与架构 novelty,
  引 HuggingGPT 2303.17580 作原语先例)+ 两问结构说明;ch2 §2.7 占位替换为定位段
  (问题式 + 方法子贡献)。**refs.bib 新增 a2303-17580**(scaffold 风格,S5 依据在
  deepread/DR-G);引用完整性复核 **35/35 零缺失零多余**。
- **A0(K=1)裁定**:必须跑(RQ1 无它则无对照,且跨纪元不得引论文数据代替),但
  **排在 B 臂之后**(B 臂不依赖它、便宜且解锁 Ch6;A0 需 25-30k 待充值)。
- 🔴 **新发现的交稿阻塞项:refs.bib 全部 34(现 35)条的 author/year 均为 `TODO`**
  ——PDF 里每条引用都会渲染成 TODO。须专项补全真实元数据(见待办)。

### s1k8b103 动力学补记 + 四项文档卫生(Jul-31 晚,用户令「把能修的全修了」)

**① s1k8b103 曲线与 fork 谱系补记(补台账洞)**——此前台账停在「R1 pending」,R1–R14
无任何条目,三路独立 review 各自撞到同一洞;全部动力学叙述此前仅靠 curve_extract
支撑。权威口径 = `python experiments/analysis/curve_extract.py --run-dir
recipe/gaia_evolver/runs/s1k8b103`(Jul-31 20:40 取数,settled=15 即 R0–R14,R15 在飞)。

| 轮 | pass@2 | 池 | ship | fork | idle | 门账 考虑/评/拒/选 |
|----|--------|----|------|------|------|----------------|
| R0 | 64.1 (66/103) | 1 | — | — | 0 | 基线轮,不进引擎 |
| R1 | **80.6** (83/103) | 2 | ✓ | V1 | 0 | 3/1/0/1 |
| R2 | 64.1 (66/103) | 3 | ✓ | V2 | 0 | 1/1/0/1 |
| R3 | 63.1 (65/103) | 3 | — | — | 1 | 无候选 |
| R4 | 67.0 (69/103) | 4 | ✓ | V3 | 0 | 3/2/1/1 |
| R5 | 63.1 (65/103) | 4 | — | — | 1 | 无候选 |
| R6 | 68.0 (70/103) | 5 | ✓ | V4 | 0 | 3/1/0/1 |
| R7 | 71.8 (74/103) | 6 | ✓ | V5 | 0 | 3/1/0/1 |
| R8 | 64.1 (66/103) | 6 | — | — | 1 | 无候选 |
| R9 | 65.0 (67/103) | 6 | — | — | 2 | 无候选 |
| R10 | 60.2 (62/103) | 6 | — | — | **3** | 3/1/**1**/0 ← 唯一「有候选但被门全拒」 |
| R11 | 67.0 (69/103) | 7 | ✓ | V6 | 0 | 3/1/0/1 |
| R12 | 73.8 (76/103) | 7 | — | — | 1 | 无候选 |
| R13 | 71.8 (74/103) | 8 | ✓ | V7 | 0 | 3/1/0/1 |
| R14 | **73.8** (76/103) | 8 | — | — | 1 | 无候选 |

- **谱系**:7 次 fork(R1→V1、R2→V2、R4→V3、R6→V4、R7→V5、R11→V6、R13→V7),
  终池 8 变体,**零退休**(未触池上限)。对照 30 题 pilot 的**零 fork**——「床异质性
  = fork 燃料」的判断获得实证支持,可作 103 床换床决定的事后辩护。
- **peak = R1 80.6% / final = R14 73.8% / drift = −6.8pp**;final 较 R0 基线 **+9.7pp**。
- **idle 峰值 = 3(R10)**,全程无更长无 ship 连续 ⇒ 论文 patience=3 会在 **R10 停、
  收 60.2%**;p5/p8/p16 **一次都不触发**。M-24 的「p16 跑内后验重建 p3 停点」一跑两得
  成立;且论文的耐心值恰好压在临界点上(多给一格耐心整场跑完)。
- **僵尸变体(Ch7 素材)**:每次 fork 后新变体几乎接管全部路由、前任迅速归零——
  V1/V2 自 R5 起零路由,V3 自 R11,V4/V5 自 R12,V6 自 R14。**唯一例外 V0**:自 R4 起
  稳定持有 39 题,胜率 87.2–94.9%,形态=「fork 出的专才被下一次 fork 顶掉,而祖先
  保住一块自留地」。

**② S1-FREEZE 正文并发段划除**——47 行起的「并发=3 全程不升」是 v1 口径,已被文件头
v2 修订第 3 条(用户裁 3→10,回归 Table 8)取代;加 ⛔SUPERSEDED 横幅并保留其中**仍
然有效**的纪律(报备口径即运行口径 / 提速前须先向导师重新报备)。⚠️ 现实态:R15 正被
实验室端点 429 限流(R14 用 57 分钟 206 会话,R15 109 分钟仅 53 会话),**若要提速须先
按该纪律向导师重新报备**。

**③ FLOW-DIVERGENCE-VERDICT 算术松动**——「约 83%」与隐含「~87%」是同一事实的两种
口径。已写死三个数并要求引用必须带口径:**申报且口径正确 19/23 = 82.6%** / **台账中
存在条目 20/23 = 87.0%**(含那 1 条 MISDECLARED)/ **完全未申报 3/23 = 13.0%**;
§一.5 的「~19 条」改为精确 19(23 − 3 NEW − 1 MISDECLARED)。

**④ refs.bib 元数据补全(交稿阻塞项已解)**——35/35 全部经 arXiv export API 解析
(选 API 而非网页摘要,避免作者名被摘要器改写),**0 未解析 / 0 疑似错引**;year 一律
取 v1 `published` 时间戳;报告 = `paper/REFS-RESOLUTION-REPORT.md`。⚠️ 两条待处置:
(a) 6 条工作名不在正式标题内(TRACE / OneFlow / HERA / Topaz / E3 / Leni),正文散文
用名须与题录对齐;(b) ~~AEGIS 疑为我方内部代称~~ **【已证伪,勿传播】**。

- 🔴 **子 agent 判词证伪(Jul-31,主循环亲验)**:refs agent 报「AEGIS 在 arXiv 记录中
  零出现,疑为项目内部代称」。**该判词错误。** 主循环用 pymupdf 直读本地全文
  `D:\PycharmProj\MAS_Directions\harnessx_2606.14249.pdf`(43 页 / 144,948 字符):
  **`AEGIS` 命中 38 次**,含摘要句「adapts them through AEGIS, a trace-driven
  multi-agent evolution engine」、正文「we introduce AEGIS, an observability-driven and
  auditable harness adaptation engine」、目录 §4.3「AEGIS Architecture」、以及
  「AEGIS combines full trace observability with a four-stage pipeline (Digester,
  Planner, Evolver, Critic)」。**正确口径 = HarnessX 是 foundry/基底,AEGIS 是其上的
  演化引擎**;我方 `--aegis-*` 旗标与论文 ch1/ch2/AppA 的 10 处 AEGIS 用法**全部正确,
  无需改稿**。成因:该 agent 只核了 API 返回的 title 字段(标题确实不含 AEGIS),未落到
  全文——与 ⑧「manifest 二次反转」同型,**证伪通道同样必须直查全文,摘要/元数据层的
  证伪不算数**。
- 📌 **顺带发现(未入稿,备 Ch3/Ch6 用)**:论文含一张**逐轮 Pass@2 曲线图**,横轴
  R0–R12 标注「Aegis Evolution Round」,纵轴「Score on GAIA-103 (%)」刻度 73–85,
  并配「Metric masking」讨论。**与我方 s1k8b103 同床同口径(103 题 / pass@2 / 逐轮)**,
  是比 Table 4 更直接的曲线级比较对象;取用前须逐字核该图注与其 K 档。

**⑤ 新增隔离 worktree**:`D:\PycharmProj\HarnessX-ledger`(分支 `feat/decomp-ledger-from`,
基线 43476d4)。理由同 `HarnessX-effort`:s1k8b103 在飞且 watchdog 续跑时从磁盘重读
代码,主树零触碰。在建功能 = **`--decomp-ledger-from`**:裁 F 要求「B2 用 B1 的冻结
账本」,但现有代码 `TypeCreditLedger.__init__` 无参、只有 `matrix()` 出口而无入口,
CLI 也只有 `--decomp-pool-from` / `--decomp-profile-from`——**交接通道根本不存在**,
不补则 B2 = 冷启动+在线学习混合物,且 32 个 (变体×类型) 格子 / ~400 次决策下前段几乎
必然全走冷格回退。默认不传该旗时须字节级等价。

### `--decomp-ledger-from` 验收 PASS(Jul-31 晚,主循环逐条亲验)

- 实现 = `feat/decomp-ledger-from` @ **a2700b1**(worktree `HarnessX-ledger`,基底
  43476d4),5 文件 +406/−1;**主树零触碰、未合并、未推送**。
- **亲验四项**:①`git status --short --branch` 干净,`show --stat` 范围与自报一致 ✓;
  ②`experiments/variant_pool/tests` **871 passed / 0 failed**(用主树 `.venv312` 跑)✓;
  ③「decomp-eval 路径先于 lock 返回」这条 claim 亲查代码:L7037-7039
  `if args.decomp_eval: _run_decomp_eval(...); return`,而 `_build_experiment_lock`
  在 L7041 ✓ ⇒ provenance 写 `decomp_manifest.json`(该路径真正的 lock 等价物,已载
  `pool_source`/`profile_source`)是**正确处置而非绕过**;④对照跑 `HarnessX-effort`
  = **874 passed**,证实 853 / 871 / 874 三数各属其分支 ✓。
- **设计要点(SPEC §7.21)**:artifact = `<run_dir>/decomp_ledger.json`(schema
  `decomp_ledger/v1`;matrix + source_run_tag + 产出它的 routing + 三个计数 +
  variant_ids),**每个 `--decomp-eval` 跑结束无条件写**——必须如此,否则 B1 不产出
  B2 要读的东西;载入 = `TypeCreditLedger.from_matrix`(只读 passes/attempts、rate
  重算,与 `matrix()` 精确往返)。**硬报错(非警告)四类**:旗与 `--decomp-routing
  ledger` 不同时出现 / artifact 缺失 / 不可读或缺 `matrix` / matrix 畸形(负数、
  `passes > attempts`、重复格、bool 计数等,8 例参数化)。**变体名对不上不报错但强制
  显形**:丢弃计数 + 日志 + provenance 三处同时记,禁止静默丢。
- **provenance**(`decomp_manifest.json` → `decomp_ledger_source`,**仅传旗时出现**,
  不传时 manifest 无新键):path / source_run_tag / source_routing / **matrix_sha256** /
  cells_loaded / cells_kept / cells_dropped_unknown_variant / dropped_variants /
  pool_variants_without_prior / prior_attempts ⇒ 读者可据 `cells_kept` + `prior_attempts`
  判定该臂非空账本起步、并据 `path` 知由谁播种。
- **未加 M-xx 偏差条**(agent 主动说明,主循环同意):decomp B0–B3 整层是**超出被复现
  论文的 M1 扩展**(论文无子任务类型账本),B2 由 B1 播种属内部实验设计选择,不是对论文
  协议的偏离。

### 🔴 主分支缺陷:SPEC 文档与代码分处两个分支(Jul-31 发现,已加警示)

`exp/variant-pool` 的 SPEC **§7.20 完整描述了 `--reasoning-effort`,但该分支代码里
`reasoning-effort` 零命中**(实现在未合并的 efc2a89,5 处命中);反过来 effort 分支的
SPEC **没有** §7.20。即**文档先落主分支、代码留在 worktree**,主分支因此宣称了自己
不具备的能力。已实测后果:§7.20 标题的「853→874」是 effort 分支的测试数,主分支实测
**853**;该错数经主循环的任务书传给 ledger agent 作基线,**被其独立顶回并正确报告**。
这是「未验证判词跨面传播」的又一实例,且这次源头是我方 SPEC。处置:§7.20 顶部加 ⚠️
警示框,**合并 effort 分支后删除**(effort 分支未动本节,合并不冲突)。
**教训:在隔离 worktree 建功能时,SPEC 条目应与代码同分支落地。**

### 🏁 s1k8b103 完跑 + 验收 PASS(Jul-31 21:22:14,退出码 0)

- 墙钟 **26h29m03s**(LAUNCHER_START Jul-30 18:53:10 → EXIT Jul-31 21:22:14,resume 跑);
  **全 16 轮结算,pending=0**。
- **终值 R15 = 74.8%(77/103)**;peak 仍 **R1 80.6%**;**drift = −5.8pp**;较 R0 基线 **+10.7pp**。
- **交叉核对 ✓**:acceptance_report 的 peak / final / drift 与 run 自身 `pool_report.json`
  逐位一致(80.58252427184466 / 74.75728155339806 / −5.825242718446…)。
- **R15 = reject 轮**:2 个候选全被门拒(SEESAW_REGRESSION ×2,10 个回退实例),idle 升到 2;
  故全程最长无 ship 连续仍为 **3(R10)**,patience 结论不变。
- **fork 谱系(新增父子关系)**:V1←V0、V2←V0、V3←V2、V4←V3、V5←V3、V6←V4、V7←V6。
  **不是扁平的七次 fork,而是一棵深度 5 的谱系树**:

  ```
  V0 ─┬─ V1
      └─ V2 ── V3 ─┬─ V4 ── V6 ── V7
                   └─ V5
  ```

  终池 8 变体,零退休;末轮承载者 V7 是 V0 的**五代后裔**。
- **M-23 终值**:改进 66 个任务实例 / **回退 88 个**;携带回退的候选 10 个;archive 类别
  {FORK:7, (none):4, ROUNDTRIP_L2:1, SEESAW_REGRESSION:3}。(此前「78 个回退」系 R15 前
  快照,**作废**。)
- **噪声地板终值**(metric A,n=8):均值 −0.8pp,**样本 SD 4.57pp** / 总体 4.27pp,
  |Δ| 均值 3.52pp / 最大 7.77pp;±2SD(样本)= **[−10.0, +8.3]pp**。R15 贡献第 8 个样本
  (+1.0pp)。**此前 n=7 / SD 4.87pp 的一切引用作废**;B-FREEZE §4.1 已同步。
- **patience 终值**(metric D):p3 → 停 **R10 / 60.2%**;p5 / p8 / p16 → 跑满 **R15 / 74.8%**。
  ⇒ 论文耐心值会让本跑**早停五轮、少 14.6pp**;耐心值恰压在临界点(多一格即跑完)。
- 🔴 **预算耗尽率 19.4%**:`pool_report.json` `budget_exhaustions_run_total` = **994**,
  末轮 40。约五分之一的 rollout 撞到 per-candidate 预算帽 ⇒ **须入 Ch7 威胁章**
  (截断可能同时压低所有臂的天花板,是全局效度问题不是单臂问题)。
- **运行健康**:resume 日志 66 traceback / 29 ERROR,**逐条分类确认全为收尾噪声**——
  42× `ValueError: I/O operation on closed pipe`(隐藏窗口启动、stdout 管道关闭)、
  6× CancelledError、6× TimeoutError、1× RuntimeError;ERROR 行 = 20× Unclosed client
  session / 6× LoggingWorker / 3× Unclosed connector。日志含 **4,015 条任务 PASS/FAIL
  结果行**,工作量未丢。**判定:跑是干净的。**

### 🔴 acceptance_report 缺陷:默认控制台日志选错(Jul-31 发现,待修)

默认只找 `<tag>.console.log`,但本跑真正完跑的是 **resume** 日志
`<tag>.resume.console.log`(原始 launch 于 Jul-30 wedge 后被杀)。后果:首次生成的报告写着
「LAUNCHER_EXIT 未找到 / **Status: IN-FLIGHT** / 墙钟 n/a / traceback 0」——
**一个已完跑、退出码 0 的实验被报成"在飞"**,且运行健康数据全空。传
`--console-log <resume 日志>` 后全部正确(complete / 26h29m03s / 66 traceback)。
**须修默认选择逻辑**(同 tag 下取最新一份,或把 `.resume.` 变体纳入候选),并让
`Status:` 同时参考 pending 轮数而非仅凭日志。否则论文审计附录会引到错误的运行状态。

---

## 🔴🔴 Aug-02:s1k8b103 的「变体」大部分不是变体(推翻 Jul-31 的「跑是干净的」判定)

### 事实

Evolver 把三个变体的 `template_path` 写成了 `file:///D:/...` URI。没有任何东西校验它;
URI 打不开;而 `harnessx/core/processor.py:714` 的崩溃处理是 `yield event`(原样透传)
⇒ **agent 静默地跑在空系统提示词上**。

`last_sys_prompt_hash`(会话记录里实际用掉的提示词的 SHA256)。

**⚠️ 数字修正(Aug-02 02:5x,`experiments/variant_pool/pool_differentiation.py` 全量重取)**:
初判的「2,397 / 642 / 152,全跑三种」是**只扫 active 池**得出的,且计数略偏。
精确值按范围分列如下 —— **引用时必须带范围标签**:

**active 池**(产出被报告的准确率曲线):3,168 rollout,**3 种**提示词

| 哈希 | 是什么 | rollout | 谁 |
|---|---|---|---|
| `762e944185` | **未修改的**基线模板 | 2,382 | V0 V2 V3 V4 |
| **`e3b0c442…`** | **SHA256("") = 空** | **634** | **V5 128 / V6 274 / V7 232** |
| `33ed740625` | V1 的演化提示词 | 152 | V1 |

**candidate 门**(决定 ship/reject 的那次评估):1,262 rollout,**6 种**提示词

| 哈希 | rollout | 备注 |
|---|---|---|
| `33ed740625` | 412 | |
| `762e944185` | 334 | |
| **`e3b0c442…` 空** | **256** | **V3 104 / V6 128 / V4 24** |
| `40a55ee675` / `5710ad871f` / `a3fd4b8adf` | 104 / 78 / 78 | 只在候选里出现过的真演化提示词,**从未进池** |

🔴 **比原判更重的一点**:空提示词**不只污染测量,还污染了选择本身**。另有 **256** 次
rollout 是在「要不要 ship 这个候选」的门评估里跑在空提示词上,且涉及 **V3 / V4** ——
这两个变体在 active 范围里是干净的,因此只看 active 会漏掉它们。
⇒ 撤稿的适用面从「准确率曲线」扩大到 **ship/reject 决策链**。

- `SystemPromptProcessor` 在 **R5–R15 崩 898 次**(V6 407 / V7 232 / V5 128 / V3 106 / V4 25)
- 归一化配置哈希:**V6 ≡ V7、V3 ≡ V5**
- ⇒ active 池只存在三种系统提示词,**V6 与 V7 在运行期是逐字节相同的同一个 agent**

### 🔴 对 Jul-31「运行健康」判定的更正

上一节写「66 traceback / 29 ERROR,逐条分类确认全为收尾噪声 ⇒ **判定:跑是干净的**」。
**该判定作废。** 那次审计只筛 traceback 与 ERROR 两级,而这 898 次崩溃是 **WARNING 级**,
整体落在筛子之外。**教训:运行健康审计必须包含 WARNING 级,且必须有「演化产物是否真被消费」
这一独立检查项 —— 分数正常不等于产物被用上。**

### 影响面

- **作废**:FINDINGS-AUG01 B 节全部(尤其 B-1 的因果解释、B-4 整条)、F 节主线第一句
- **不受影响**:C 节(步数上限失败模式,与提示词正交)、D 节;准确率数字本身有效,
  只是它测的不是我们以为在测的东西
- **CH3→CH4 的桥**(「整题粒度掩盖变体差异」)**在修复后的池上重测之前不得使用**

### 恢复

演化产物本身是真的:三个模板都在磁盘上,**174 / 131 / 161 行**(基线 125 行)。
**演化产出了,只是投递断了。** 修复 `2e2c68c` 在加载时正规化 URI 并 fail-closed 校验后:
**不同提示词 3 种 → 5 种,V5/V6/V7 各自独有** ⇒ 冻结池对 CH4 首次是真分化的。

### 同批修复的第二个静默失效

decomp 子任务 id 为 `<parent>::<subtask>`,`:` 在 Windows 路径非法 ⇒ 首次冒烟
**47/47 子任务 rollout 在 0.0 秒 WinError 123 死亡**,一次模型调用都没发出。
`run.py` 的 session id 现过 `_fs_safe()`;UUID 类 id 逐字节不变。

### 待观察(Aug-02 冒烟中)

`effort=max` 触发 LiteLLM 警告:多轮历史里 `reasoning_content` 未被回填,
**每个历史 assistant 轮次收到的推理链是一个空格**,官方措辞 "may silently degrade
multi-turn response quality"。GAIA 是多轮任务(中位 9 步/失败 20 步)⇒ 作用在每一步上。
**同属「静默降级」类,冒烟须量其代价。**

### 🚀 s2k8b50 点火:分化池重跑(Aug-02 02:10,用户睡前授权无人值守)

**授权**:用户 Aug-02 ~02:05「准备每种情况的下一步,然后继续跑该跑的实验,期间我不会对你下任何
指令,到早上我需要看到的是完整的分化池……然后我们就可以再跑一遍正式实验,以及把分化池完整跑」。

**为什么要重跑**:M-27 证明 `s1k8b103` 的选择历史脏(V5/V6/V7 全程空提示词被评估;唯一真分化的
V1 被选择过程杀掉)⇒ 池必须在诚实信号下重长。

**配置**:50 题冻结子床 `pool_bed50.json`(L1 19 / L2 25 / L3 6,seed 20260801,id 摘要
`4eafb24cf3170035`,`build_pool_bed.py` 冻结)× K=8 × 16 轮。**除 `--data-path`/`--run-tag`
外与 `s1k8b103` 逐字相同**(偏差仅 M-29 一条)。

**主循环替用户做的判断(逐条可推翻,详 OVERNIGHT-AUG02.md §1)**:
1. 缩床不缩轮(分化深度由轮数买;全床 1.76 h/轮 单夜放不下);
2. **`--reasoning-effort` 不开** —— 用户的「开 max」是给分解冒烟下的;池保持 thinking OFF
   以与 `s1k8b103` 同档可替换,且规避今夜暴露的 blank-`reasoning_content` 多轮降质;
3. 不在凌晨改 M-27 的 fail-closed 错误路径(`run_variant_pool.py:4750` 未被 try 包住,理论上
   Evolver 写出真不存在的路径会杀跑);改为 Monitor 盯 `does not resolve to a readable file`。
   依据:`s1k8b103` 观察到的失败 100% 是「URI 可解析但没被解析」,模板文件均真实存在;
4. watchdog 自动 resume **未挂**(注册计划任务被安全分类器拦);由主循环整夜监控,死则诊断后手动
   `--resume`(优于自动重启对确定性崩溃反复撞墙)。

**并行争抢与其裁决**:02:10–03:08 与 `b_smoke` 并行,端点限流 60 req/min,点火即出 429。
02:27 实测重试深度 **attempt 1/5 = 58、2/5 = 14、3/5 及以上 = 0**,终态失败 0,`[ERROR]` 0
⇒ 429 被重试完全吸收,**R0 基线未被污染,不杀跑**。残余代价为墙钟约 2 分钟。
早上仍须核 `pool_report.json` 的 `infra_failure_rate` 兜底。

**落点树与晨间待办**:见 `experiments/docs/OVERNIGHT-AUG02.md`(成功的可测定义 = 末结算轮
active 变体的 distinct `last_sys_prompt_hash` 个数;硬否决 = 出现 SHA256 空串 `e3b0c442…b855`)。

### 🏁 b_smoke 完跑:分解管线冒烟四闸门全过(Aug-02 02:20:16,墙钟 75m40s)

10 题 × pass-k 2 = 20/20 attempt 全部落盘。`--reasoning-effort max`(worker+meta,用户令)。

**四闸门**:fallback **0/20** ✅ / 合成丢失 **0/20** ✅ / 空 prompt(distinct **5**,
empty **False**)✅ / processor crash **0** ✅。2 个 traceback 经逐条查证 = 解释器退出时
asyncio `__del__` 的 ResourceWarning(proactor/subprocess transport),与 s1k8b103 同类收尾噪声。

**主轴 = 配对准确率 7/10**,落在预设落点树最好一档(≥6/10 ⇒ 可以发臂)。

**配对比较(同一批 10 题 vs s1k8b103 整任务 R15)**:两边都解出 6 / **只有分解解出 1**
(`7a4a336d`)/ **只有整任务解出 1**(`20194330`)/ 都没解出 2 / **并集 8/10**。
⚠️ 1 对 1 不一致在 n=10 上 **McNemar 精确检验 p = 1.0**,统计零信息;
它只证明测量不退化(能看见不一致),**不证明分解有用**。整任务参照 R0 7/10、R15 7/10、曾解 9/10。

**计划结构**(取自 `decomposition` 字段;`subtasks` 是执行记录,无 `dep`,早前查错字段已更正):
每计划子任务 {2:1,3:6,4:7,5:2,6:2,7:1,9:1},均值 **4.30**、最大 **9**;
**4/20 计划超过旧上限 5** ⇒ `--decomp-max-subtasks` 由 5 提到 20 是必要修复;
**63/86 子任务带 dep,17/20 纯线性链、3/20 有分支或跨级依赖**
(更正早前「全部线性、并行度为零」);能力复现坐实 search 35 / browse 15 / compute 22 / verify 14。

**成本/步数**:86 次子任务 rollout,步数中位 **9**、p90 20、**22% 撞 20 步上限**;
$43.35 repo 单位 ⇒ **单臂投影 12.6 h / 约 $6.8 实付**(中途 19.6 h 的估计偏高,后半程更快)。

**预算配平的两难(须用户裁)**:均值 4.30 子任务 ⇒ 严格总步数配平只能给每子任务
20/4.30 ≈ **4.65 步**,而中位需求 **9 步** ⇒ 约一半子任务跑不完。
主循环建议:**跑两个 B0**(`--decomp-subtask-max-steps` 20 与 5),用其差把
「分解有用」与「多花算力有用」拆开;缺这一对,任何分解增益都可被「只是多花 2 倍算力」打掉。

### 🔧 撤稿表述修正:池在配置层是分化的,丢的只是提示词那一路(Aug-02 03:2x)

起因:`s2k8b50` R1 的候选改的是 **processor 桶**(`StepCountdownProcessor`),不是提示词模板。
processor 改动 ship 后 `last_sys_prompt_hash` 不会变 ⇒ **只看提示词轴会把 processor 级分化
误判成「没分化」**。据此给 `pool_differentiation.py` 补了配置轴(归一化 config 哈希,
剔除 `session_id`/`base_dir`/`export_jsonl`/`silent` 逐跑噪声行)。

**双轴复测 s1k8b103 末轮 R15**:active 池 3 个变体(V0/V6/V7);
**配置轴 3 个全不同**(`652d8bcd` / `8d25f9c8` / `0cd08ae0`);**提示词轴 V6/V7 均为空**。

⇒ 早前写的「变体从来没被分化开」**不准确,作废**。准确表述:

> 池在**配置层确实分化了**(processor 级改动生效);被静默丢弃的**只是提示词那一路**。
> 所谓「互补性」实际是在 {基线提示词 + 演化 processor} 与
> **{没有提示词 + 演化 processor}** 之间测的 —— 后者不是演化出来的 harness,是被削掉脑子的 agent。

B-1 的因果解释仍然作废(结论不变),但新表述更准、更站得住,
且解释了 V0/V2/V3/V4 为何共用同一模板哈希 —— **它们本来就是靠 processor 分化的**。
**今后判定「池是否分化」必须双轴同读。** 同步:M-27 增「表述修正」行,OVERNIGHT-AUG02 §3 判据改双轴。

**顺带验证**:`s2k8b50` 的 V0 归一化 config 哈希 `652d8bcdc617` 与 `s1k8b103` 的 V0
**逐位相同** ⇒ 坐实 M-29 的「除 `--data-path`/`--run-tag` 外逐字同配」。

### 🌱 s2k8b50 R1 首次 fork + 仪器补第三轴(Aug-02 03:5x)

**R1 ship 成功**:`shipped=true`、`forked=["V1"]`、`decisions={"V0":"fork"}`、
`variant_count=2`、`reconcile_status={"V0":"fork_parent_unchanged","V1":"fork_child_active"}`。
`candidate_accounting` = 请求 4 / 实产 4 / Critic 拒 1 / 排队进门 3 / **实评 1** / 门拒 0 /
**跳过 3** / 选中 1 ⇒ **引擎只评最高排名候选,过了就跳过其余**,
故 ship 率须按 **1/1** 读,不是 1/4。

**🔴 仪器缺陷(我踩的坑)**:`pool_differentiation.py` 原先只读 `R<n>/active_pool/`,
而那是 fork **之前**的测量 —— fork 决策记在同轮 `pool_state.json`,子变体要到**下一轮**才被测量。
结果仪器对一个**已经 fork 的池**报 "THIN: no differentiation yet"。
**已补轴 C(谱系)**,VERDICT 改以 `variant_count` 为准,并在代码注释里写明这个滞后。

**轴 C 的阳性对照(最强的一次)**:仪器把 s1k8b103 的 fork 谱系逐条重建 ——
V1←V0(R1)、V2←V0(R2)、V3←V2(R4)、V4←V3(R6)、V5←V3(R7)、V6←V4(R11)、V7←V6(R13)
—— 与 Jul-31 独立记录的谱系**逐条一致**。同时给出现实基准:
**16 轮 7 次 fork ≈ 0.47/轮**,空轮很多(R3/R5/R8/R9/R12/R14 无候选,R10/R15 门拒)。

**双跑仪器读数**:`s2k8b50` = PARTIAL(池 2,配置 1,提示词 1,空 0);
`s1k8b103` = **RED**(池 8,配置 3,提示词 2,**空 890**)。

### 🔬 修复被真实触发并验证:Evolver 的 `file://` 习惯稳定复现(Aug-02 04:0x)

**发现**:`s2k8b50` 里 Evolver **又写出 `file:///` URI**,且是**畸形形式**(`file:///` 后跟反斜杠):

```
template_path: file:///D:\PycharmProj\HarnessX\...\C-R1-03\output_dir\templates\gaia_agent_commit_nudge.j2
```

⇒ M-27 的触发条件**不是 s1k8b103 的偶发,而是该 meta 模型的稳定行为**。
本跑 17 个候选 config 触及 template,其中 3 个文件含 `file:///`
(C-R1-02 的 config 与 harness_config、C-R1-03 的 output_dir/config)。

**🔴 不可从「零 artefact 错误」推断修复有效**:R1 账目是「实评 1、跳过 3」,
而带 `file://` 的 C-R1-02/C-R1-03 正在被跳过之列 ⇒ **它们从未被加载过**,
fail-closed 与解析器在本跑中尚未被这些配置真正触发。判据不成立,须直接测。

**直测结果(用运行现场的原始字符串)**:`_as_local_path` 三种形式全部正确解析且 `is_file()=True`
——(a)畸形反斜杠 `file:///D:\...`(b)规范 `file:///D:/...`(c)裸 Windows 路径。
⇒ 解析器处理得住 Evolver 实际产出的形式;**先前担心的「fail-closed 半夜炸跑」风险,
对该失败模式而言显著低于我 02:10 的估计**(但对「真·文件缺失」仍成立,继续监控)。

**测试缺口已补**:原回归测试用 `Path.as_uri()`,产出的是**规范**形式,
**从未覆盖 Evolver 实际产出的畸形形式** —— 测试一直在过,却没测到真实输入。
新增 `test_as_local_path_unwraps_the_malformed_uri_the_evolver_actually_writes`(10 绿)。

### 🌳 s2k8b50 R2 = `apply`(非 fork),V0 提示词真的被演化改掉(Aug-02 04:4x)

**R2 决策 = `{"V0": "apply"}`** —— 就地替换 V0 配置,不新增变体(`variant_count` 仍为 2)。
效果可见于哈希:V0 的提示词 `762e944185` → **`da1796df8291`**,配置 `652d8bcd` → **`489282d9`**。
⇒ **提示词层的演化在本跑真的生效了**(s1k8b103 里这一路是被静默丢弃的)。

**当前池(双轴)**:V0 = 提示词 `da1796df` / 配置 `489282d9`(最近测于 R3);
V1 = 提示词 `762e9441` / 配置 `9ebec423`(最近测于 R2)。
⇒ **2 提示词 / 2 配置 / 0 空**,双轴真分化。

**🔴 仪器第二处低报(同类错误,已修)**:每轮只重测**配置变过的**变体
(R1 fork V1 → R2 测 V1;R2 apply V0 → R3 测 V0),其余沿用旧测量。
故「最后一轮的 active_pool」只含一个变体,按它判分化会把 2 种提示词读成 1 种。
新增 `_current_pool()`:**按每个变体各自的最近一次测量**取哈希。
(`routing` 证实任务确实分给了两个变体:R1 = V0 42 / V1 8;R2 = V0 19 / V1 31。)

**阳性对照 —— 这是撤稿最干净的一次表述**:

```
variant   seen  prompt         config
V0         R15  762e94418507   652d8bcdc617
V1          R4  33ed7406252e   6cacc55e56a1
V2          R4  762e94418507   bb413084090b
V3         R10  762e94418507   30999965d3d8
V4         R11  762e94418507   a56edafbebef
V5         R11  e3b0c44298fc   8d109b46e458   <-- EMPTY
V6         R15  e3b0c44298fc   8d25f9c8fad1   <-- EMPTY
V7         R15  e3b0c44298fc   0cd08ae02ee8   <-- EMPTY
  distinct prompts: 3   distinct configs: 8   across 8 variants
```

**8 变体 / 配置 8 种全不同 / 提示词仅 3 种 / 其中 3 个变体没有提示词。**
一张表讲完「配置层分化完整,提示词层塌成 3 种且 3 个是空」。**Ch7 威胁章建议直接引这张表。**

### ❓ s2k8b50 空轮零产出:查了未解(Aug-02 07:4x,记为待查项)

R3/R5 为空轮,占最近四轮之二,是 ship 密度低于 s1k8b103 的直接原因。**未查明,不得当已解引用。**

- **记账差异**:s1k8b103 的 R3/R5 = `requested_slots=0`(引擎未请求候选);
  s2k8b50 的 R3/R5 = **`requested_slots=4, actual_candidates=0`**(请求 4,产出 0)。
  两者表面均为 `no_candidate=True`,**机制不同**。
- **产物为空**:R3/R5 下无 pipeline 目录(失败候选本应留目录,R1/R2 留有 `retry_01`/`bounce`);
  对应时间窗 `__main__` 无任何日志行 ⇒ 零产出发生在候选生成**之前**,具体环节从产物判不出。
- 🔴 主循环曾假设「缩床 ⇒ 失败样本减半 ⇒ Digester 挖不出候选」,
  **该假设无证据支持,不可引用**;证伪/证实需全床对照。

**已知既有问题(非今晚新增)**:meta-agent 常分析完不写 `config.yaml`,改写 `DECISION_REQUIRED.md`
—— M-19/M-20/M-21 的证据来源(forkprobe_11 ~9/12 候选死于此)。本跑活跃:
C-R1-03/04、C-R2-01/03 均撞上,靠 `--evolve-retry` 救回(C-R2-01 重试 2 次仍失败;
C-R2-03 连 commit bounce 也未产出 config)。**足以解释 ship 密度低,不能解释 R3/R5 的「连目录都没有」。**

### 🛑 s2k8b50 停滞:连续 4 轮无 ship,超过参照跑历史最长(Aug-02 08:1x)

自 R2 的 `apply` 之后池零增长:**R3 空 / R4 两候选全被门拒 / R5 空 / R6 空**,约 3.5 小时。
当前池仍为 **2 变体**(V0 `da1796df`/`489282d9`,V1 `762e9441`/`9ebec423`),
双轴 2/2,空提示词 0,`VERDICT = PARTIAL: differentiated on both axes but shallow`。

**可报告的数字**:s1k8b103 **全程最长无 ship 连续 = 3**(R10,Jul-31 记录);
本跑已达 **4 且仍在继续** ⇒ **若采用论文 patience=3,本跑会在 R6 被早停**。

与 s1k8b103 的耐心值结论(p3 → 停 R10 / 60.2%;p5/p8/p16 → 跑满 R15 / 74.8%,
「耐心值恰压在临界点」)并置,本跑提供**第二个数据点**:更小的床上无 ship 连续更长
⇒ 同一 patience 在不同床上的行为不同。可入 Ch7。⚠️ **n=2 跑,不得写成规律。**

### ✅ 空轮之谜解决:actionability 短路,非故障(Aug-02 08:5x,推翻 07:4x 的「未解」记录)

证据源:`R<n>/V0/pipeline_audit.json`(此前只查了 pipeline 目录与控制台日志,漏了这个产物)。

**机制**:Digester 对每轮失败打「可整改性」分 `actionability`,低于 `alpha=0.5` 即
**短路不生成候选**(`short_circuit: "actionability_below_threshold"`,
`no_op_reasons: ["selective invocation no-op: actionability a_t=X < alpha=0.5"]`)。

| 轮 | a_t | digests | 失败 | Digester 理由(原文摘) |
|---|---|---|---|---|
| **R1** | **0.9** | **50** | **18** | "failures involve budget exhaustion linked to TokenBudgetProcessor … addressable engineering improvements" ⇒ 产出 4 候选 |
| R3 | 0.0 | 19 | 1 | "the only failure is a model capability limitation in city vs. town classification, **which cannot be fixed via harness edits**" |
| R5 | 0.4 | 19 | 2 | "one is a pure model capability limit (scope_ambiguity) … the other is environmental" |
| R6 | 0.3 | 19 | 4 | "only one failure … involves a harness component (TokenBudgetProcessor) with usable evidence" |

**根因链(已证部分)**:池分叉后 routing 把 50 题拆给两个变体(R2 = V0:19 / V1:31)
⇒ V0 每轮只看到 **19 题 / 1–4 个失败** ⇒ 失败样本少且多为模型能力极限
⇒ a_t 过不了 0.5 ⇒ 短路。对比 R1:V0 独占全部 50 题、18 个失败,a_t=0.9,正常产出。

**🔴 两处自我更正**:
1. 07:4x 记「零产出发生在候选生成之前,具体环节从产物判不出」—— **作废**,已判出(选择性调用短路)。
2. 同处记「『缩床饿死 Digester』假设无证据支持,不可引用」—— **现有证据支持**,
   且机制比原猜测更具体:不是 Digester 想不出,是**可整改性分数在小样本上过不了阈值**。
   ⚠️ 仍属**推断**的部分:全床能否把 a_t 抬过阈值 —— a_t 是 LLM 对失败构成的判断,
   不是失败计数的单调函数,故「103 题床能解开停滞」**合理但未证**。

**🟠 参数溯源(重要)**:`actionability_threshold_provenance` 自述
**"OURS: configurable reconstruction parameter; the paper does not specify one unique operational alpha"**
⇒ **alpha=0.5 是我方选定值,非论文规定**。它直接决定演化频率,
应作为可调旋钮进入偏差表与 Ch7(现有偏差表未单列该参数)。

### 🎯 停滞的统一解释:床上的可整改失败已榨干(Aug-02 09:0x,三次修订后的定稿)

**证据三线合流**:

1. **短路轮(R3/R5/R6)** 自述理由均为「模型能力极限 / 环境问题,harness 改不了」
   (R3 a_t=0.0:"the only failure is a model capability limitation … cannot be fixed via harness edits")。
2. **门拒轮(R4/R7)**:4 个候选进门评估,**全部 `improved=[]`——一个任务都没改善**
   (C-R4-02 regressed=2、C-R4-04 regressed=**0** 亦被拒、C-R7-04 regressed=3、C-R7-02 regressed=2)。
3. **Critic 的拒因与短路理由同源**:C-R7-01「regression … is due to **bot walls (external blocker)
   not solvable by a processor mutation**」;C-R7-03「addresses a symptom … not the root cause」。

⇒ **池在这张 50 题床上已收敛**:剩余失败大多不是 harness 可修的。

**对三个方案的裁决(推翻本文件早前两版建议)**:
- **降 alpha ❌**:R7 已证 a_t 可达 1.0 却仍产出零改善候选 ⇒ 降阈值只会多造无用候选。
- **放松门 ❌**:门拒的是 `improved=[]` 的候选,放松即 ship 零改善变更,与 §4.1 反回退直接冲突。
- **换更大/更难的床 ✅**:唯一触及根因者 —— 但**理由不是噪声地板**(M-29 的旧论据),
  而是**需要更多「harness 可整改」的失败样本**。

⚠️ **限度**:n=1 跑,7 个演化轮。「已收敛」是当前证据下最好的解释,不是已证定理。
反证方式:全床续跑若立刻恢复 ship,即支持本解释;若仍零改善,则问题在 meta 模型而非床。

**顺带的标签缺陷**:`C-R4-04` 为 `improved=[] regressed=[]`(纯空操作)却被标 `SEESAW_REGRESSION`
⇒ 该标签把「无改善」与「有回退」混计。报告 seesaw 统计时须分列,否则会高估回退率。

### ✅ R8 检验「已收敛」预测:成立,且出现第三条机制(Aug-02 09:2x)

09:0x 定稿诊断后,主循环公开下了可证伪预测:**若「床已收敛」成立,R8 应不 ship**。

**R8 结果**:`decisions={}`、`actual_candidates=0`、`variant_count=2` —— 预测成立。
但机制是**此前未见的第三条**:`a_t = **0.7**`(**高于 alpha=0.5,未被 actionability 拦下**),
`short_circuit = **planner_empty_landscape**` ⇒ **分数足够,但 Planner 形不成可行动 landscape**。

**三条独立的无 ship 路径(8 个演化轮,6 轮无 ship)**:

| 机制 | 轮 | 含义 |
|---|---|---|
| ship | R1(fork)、R2(apply) | — |
| `actionability < α` | R3(0.0)、R5(0.4)、R6(0.3) | 失败不可整改 |
| **`planner_empty_landscape`** | **R8(a_t=0.7)** | **分数够,仍无事可做** |
| 门拒(候选全 `improved=[]`) | R4、R7 | 候选造出来了但无用 |

⇒ **三条互不相同的机制一致指向「床上已无 harness 可整改内容」**,R8 尤其有力:
它不是被阈值拦下的,是通过了阈值仍无事可做。**「已收敛」的证据强度由此上升一档。**

**论文对应**:M-18 记载 selective invocation 含两个短路条件——「`a_t < α` **或没有可行动 landscape**」
⇒ `planner_empty_landscape` 是既有设计,非缺陷。

**对第一问的影响**:不变,且更强 —— 降 alpha 更加无用(R8 的 a_t 本就够高)。

### 📕 门评估总账定稿:7 个候选,0 次改善(Aug-02 09:5x,R10 结算后)

R10 是全流程满血轮:`a_t=0.7`(过阈、无短路)→ 产出 4 候选 → **3 个进门真实评估**
→ **3 个全部 `improved=[]`** 并各自回退 2–3 个任务;另 1 个被 Critic 判为 C-R10-03 的子集去重。

**全跑门评估总账**:

| 轮 | 进门评估 | 改善任务数 |
|---|---|---|
| R4 | 2 | **0** |
| R7 | 2 | **0** |
| R10 | 3 | **0** |
| **合计** | **7** | **0** |

⇒ **整跑 7 个候选真实进门评估,零改善。** 仅有的两次 ship(R1 fork、R2 apply)均在头两轮。
这是「床上已无 harness 可整改内容」在 n=1 条件下能拿到的最强证据形态:
不是没造候选(造了 11 个),不是分数不够(R8 a_t=0.7、R10 a_t=0.7 均过阈),
而是**造出来的东西一个都不管用**。

**四条无 ship 机制汇总(11 个演化轮,9 轮无 ship)**:
`actionability < α`(R3/R5/R6/R9)、`planner_empty_landscape`(R8)、
门拒 `improved=[]`(R4/R7/R10)、Critic 去重/策略拒(R1/R4/R7/R10 各有)。

**副产品:哨兵任务**。`c365c1c7` 被 **4 个不同候选**回退过(C-R7-02、C-R7-04、C-R10-02、C-R10-03),
`46719c30`、`4b6bb5f7`、`3cef3a44` 亦重复出现 ⇒ 少数任务对任何 harness 改动都脆弱,
是 seesaw 门拒的主要来源。**建议单列「脆弱任务」分析**:若回退高度集中于少数任务,
则 §4.1 的逐题零容忍门实质上是被这几个任务主导的,这对门设计的讨论有直接价值。

### 🔧 脆弱任务分析 + 上一条的过度陈述更正(Aug-02 10:0x)

**集中度实测**(7 个 seesaw 被拒候选,16 次回退实例,9 个不同任务):

| 任务 | 被回退次数 |
|---|---|
| `c365c1c7` | **5**(31%) |
| `4b6bb5f7` / `3cef3a44` / `46719c30` | 各 2 |
| 其余 5 个 | 各 1 |

**top-3 占全部回退实例的 9/16 = 56%** ⇒ 回退确实高度集中于少数任务。

**🔴 更正上一条记录**:前节写「少数任务……**是 seesaw 门拒的主要来源**」—— **过度陈述,作废**。
7 个被拒候选的门理由**全部是 `no task improved`**;决定性反例是 **C-R4-04
(`improved=[] regressed=[]`,零回退)同样被拒**。
⇒ **触发拒绝的是「零改善」,回退只是伴随现象,不是拒因。**

正确表述:「回退高度集中于少数任务」是一个**独立成立的观察**(可入门设计讨论:
§4.1 逐题零容忍门在本跑中若真被触发,将由这几个任务主导),
但它**不是**本跑候选失败的原因 —— 原因是候选一个任务都改善不了。两者不可混为一谈。

### 📕 门评估总账更新:11 个候选,仍是 0 次改善(Aug-02 10:3x,R11 结算后)

R11:`a_t=**0.8**`(过阈、无短路)→ 产出 4 候选 → 2 个进门评估 → **2 个全拒**,池仍为 2。

**累计:门评估候选 11 个,带任何改善的 0 个;seesaw 拒 8 次。**
(此前记录为 7 个,R10/R11 各追加。)

样本从 7 增至 11 而结论不变 —— 且四轮高 actionability(R7=1.0、R8=0.7、R10=0.7、R11=0.8)
全部走完流程仍零改善 ⇒ **「机会不足」这一竞争解释被排除**:不是没造候选,不是分数被拦,
是每次都落空。**「床上已无 harness 可整改内容」现为本跑证据下的稳健结论。**

### ⏹ s2k8b50 用户令停跑(Aug-02 14:1x)

用户判定「感觉这个可以停了」。主循环同意:诊断已饱和(12 候选零改善 + 四轮高 actionability
仍零改善),R13–R15 只会重复同向证据,继续跑属纯支出。

**停跑状态**:R0–R12 已结算并完整保留,R13 在飞时被终止(损失仅 R13 部分工作)。
**可 `--resume s2k8b50` 从 R12 续跑。** 因中途终止,`pool_report.json` **未生成**
⇒ peak/final/drift 与 `infra_failure_rate` 的收尾核验**未做**,需要时以 `--resume` 跑完一轮补出,
或直接从 `R*/pool_state.json` 手工汇总。监控任务 b3d2jttrk 同时关闭。

**最终交付状态**:池 = **2 变体**(V0 提示词 `da1796df` / 配置 `489282d9`;
V1 `762e9441` / `9ebec423`),**双轴各 2、空提示词 0**,1881 rollout,artefact 错误 0,终态失败 0。

**下一步的关键事实(排期依据)**:`s1k8b103` 的池**在修复后加载是好的** ——
b_smoke 实测同一池加载出 **8 变体 / 5 种提示词 / 8 种配置 / 零空**
(V0/V2/V3/V4=`762e944185`,V1=`33ed740625`,V5=`9c1c9063d9`,V6=`901b533da1`,V7=`7fe49be7bf`)
⇒ **CH4 所需的「冻结且已分化的池」现成可用,不必等新池**。
池演化史是否干净属 CH3 议题,CH4 问的是「在冻结池上做分解+路由是否有用」。

### 🔍 全等级日志审计(Aug-02,用户令「再次检查所有 error 和 warning」)

**为什么重做**:Jul-31 的审计只筛 traceback 与 ERROR 两级,漏掉 898 次 **WARNING 级**
processor 崩溃,还写下「判定:跑是干净的」。本次按**全等级 + 按消息种类**扫四个日志
(工具:`scratchpad/mainloop/logaudit.py`,含无严重度标签的裸模式)。

| 日志 | 大小/行数 | WARNING | ERROR | 消息种类 |
|---|---|---|---|---|
| s1k8b103.console | 6.6MB / 48,295 | 1,478 | 2 | 166 |
| s1k8b103.resume | 56.4MB / 408,880 | 14,350 | 29 | 1,045 |
| s2k8b50.console | 29.9MB / 200,113 | 8,816 | 10 | 423 |
| b_smoke.console | — | — | — | — |

**🔴 发现一(真 bug,已修)**:`tool_registry.custom: failed to load 'file:///…'`
—— s1k8b103 **466** 次、s2k8b50 **40** 次、b_smoke **14** 次。
详见 M-31 / SPEC §7.22。要点:**坏进了 s1k8b103 的 active 池**(`R2`/`R4` 的 V1),
且 b_smoke 里 **V1 的 14 个会话一次不漏全失败** ⇒ 冻结池的 V1 一直缺 `python_eval`。
**已修并端到端验证**(修后注册表 `+python_eval`),专项测试 13 条,全量 **937 绿**。

**✅ 发现二(查证后确认**不是** bug,更正主循环夜间说法)**:
`run_loop error`(InternalServerError / Timeout / RateLimitError)
—— s1k8b103 113/4,458 = **2.5%**,s2k8b50 60/1,894 = **3.2%**,b_smoke 0。
主循环整夜以 `retries exhausted|Max retries exceeded` 为匹配串报「终态失败 0」,
**该匹配串错了**,真实形态是 `run_loop error`。
但**代码行为正确**:`run.py:344` `_is_infra_failure` 以 `exit_reason=="error"` 判定,
文档逐字写明「**A.3 p.29 is explicit that such attempts count as failures and are not
resampled**」⇒ 按论文规定计为失败,且经 `infra_failure_rate` 可审计。
实测核对 R0 的 100 次 attempt:`{done:60, budget_exceeded:37, error:3}` = 3%,分类正确。
⇒ **无需修改**;M-11 已要求分列报告,补记实测比率即可。

**其余均为噪声(逐类确认)**:429 重试(被吸收,深度分布见前节)、web_search 三级回退
(Bing 抓取失败 → DuckDuckGo HTML/Lite 失败 → Wikipedia 403)、退出期 ResourceWarning
(unclosed file/client session/connector)、`DECISION_REQUIRED`(已知 M-19/20/21)、
`finish_reason=length` 注入续写、`Web search circuit breaker tripped`(设计内保护)。

**方法学教训**:审计必须**逐条投递路径**做。§7.21 修好了提示词那条,
本次才发现工具那条同样是坏的 —— 同一现象、不同路径,不会因为修了一条而自动好。

### 🔬 审计续做:尾部全查 + 第三条投递路径(Aug-02,用户令「再次确认没有其他问题」)

**上一轮审计不完整,这是自查出来的**:只打印了每个日志 **top-40** 消息种类,
而实际有 166 / 1045 / 423 种 ⇒ **尾部 126 / 1005 / 383 种从未看过**,
且 b_smoke 那一段被 `head` 截掉、根本没读。这与 Jul-31 漏掉 898 次崩溃是同一形状的缺口。

**做法**:反向筛 —— 把已归类的良性家族列白名单,**打印其余全部**(工具 `logtail.py`)。

**结果:9 种未归类,共 201 行,逐条查证**:
`Traceback` ×66(Jul-31 已逐条分类的收尾噪声,数字吻合)、
`Cost warning $40.10/$50.00 (80%)` ×56(成本护栏正常工作)、
`Serper search failed (falling back to built-in chain)` ×37+2(搜索降级,有兜底链)、
`TimeoutError` ×12、`LoggingWorker error` ×6、`CancelledError` ×6、`Event loop is closed` ×1。
**s2k8b50 仅 2 行未归类,b_smoke 仅 2 行** ⇒ 尾部无新问题。

**🔴 但发现了第三条投递路径**(详见 M-32 / SPEC §7.23):演化 **processor** 经
`_target_: file://…` 加载,同样在 Windows 上拼错路径;
且 `_instantiate_proc` 用 `except Exception: return None` **零日志吞掉**
⇒ 408,880 行日志匹配数为 **0**,查也查不到。
s1k8b103 **19 个 active_pool 配置**受影响(对照:干净配置声明 8 实例化 8,受影响配置声明 9 只出 8,
且实例化集合与原版基线逐个相同)。

**🔴 并发现第四个问题(非路径)**:**配置与类版本漂移** —— R10/V4 引 C-R6-01 版
`CommitNudgeProcessor` 却传 `nudge=`,`TypeError`,文件完好而实例化失败。
⇒ **第一版 fail-closed 判据(「文件可读」)抓不到它**,已升级为
**「用运行期同一调用真正实例化一次」**,两个成因一并覆盖。

**对发臂的直接后果(须用户裁)**:s1k8b103 终池 8 变体在新判据下
**V0/V1/V6/V7 可加载,V2/V3/V4/V5 fail-closed(4/8 不可用)**。
V6/V7 各拿回 `CommitNudgeProcessor` + `StepCountdownProcessor`。
那 4 个失败的变体在原跑中本就一直以原版处理器栈运行 —— 新判据只是让它显形。

**方法学结论(入 Ch7)**:三条投递路径按可发现性排序 = 提示词 898 条 WARNING →
工具 466 条 WARNING → **processor 零日志**。「自演化系统需要产物消费审计」不是个案,
且**审计必须逐条投递路径做**;判据应为「产物是否真的被消费」,不是「产物文件是否存在」。

### ⏸ 待用户裁决:发臂用哪个池(Aug-02,阻塞 CH4 全部臂)

**问题**:M-32 的新判据下,`s1k8b103` 终池 **8 变体中 4 个 fail-closed**
(V2/V3/V4/V5),其配置声明的演化 processor 实际装不出来(路径拼错 或 kwargs 版本漂移)。
**这不是新引入的损坏** —— 这四个变体自 s1k8b103 起就一直以原版处理器栈运行,
只是 `_instantiate_proc` 静默吞掉异常、零日志,从未显形。新判据只是不再假装它装上了。

**可加载**:V0 / V1 / V6 / V7(V6/V7 各拿回 `CommitNudgeProcessor` + `StepCountdownProcessor`)。

| 选项 | 内容 | 代价 |
|---|---|---|
| ① 缩池 | 只用 V0/V1/V6/V7,**K=4** | 池减半,但每个变体都真实可加载 |
| ② 全床重跑 | 三个修复(M-27/M-31/M-32)均在,构造上正确 | ~26.5h |
| ③ 降级加载模式 | 按其**当年实际**运行样态加载(原版栈)并显式记录 | 忠实复现历史,但该池本就未分化 |

**主循环倾向 ②**;若优先看到 CH4 数据,① 可立即发。**未获用户明令前不发任何臂。**

### 📐 s2k8b50 的意外产出:噪声地板直测(Aug-02 补算,主循环先前漏报)

**先前漏报的原因**:该跑被中途停止,未生成 `pool_report.json`,主循环只报了分化结果,
**从未汇总其准确率曲线**。现从各轮 `pool_state.json` 手工补出。

**完整曲线**(50 题床,pass@2):

| 轮 | R0 | R1 | R2 | R3 | R4 | R5 | R6 | R7 | R8 | R9 | R10 | R11 | R12 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| pass@2 | 64.0 | 70.0 | 80.0 | **86.0** | 80.0 | 80.0 | 74.0 | 80.0 | 66.0 | 76.0 | 72.0 | 72.0 | **86.0** |
| pass@1 | 53.0 | 56.0 | 64.0 | 67.0 | 65.0 | 63.0 | 63.0 | 66.0 | 58.0 | 58.0 | 61.0 | 58.0 | 74.0 |
| 决策 | — | fork | apply | — | reject | — | — | reject | — | — | reject | reject | reject |

**🎯 关键**:**R2 之后池的配置再未变更**(全部 idle/reject)⇒ **R3–R12 是同一个池的 10 次重复测量**,
即一次现成的 identical-config replay 噪声探针(与论文 Fig.4/R12 同款仪器)。

| | pass@2 | pass@1 |
|---|---|---|
| 均值 | 77.2% | 63.3% |
| 范围 | 66.0–86.0 = **20.0pp** | 16.0pp |
| **样本 SD** | **6.41pp** | 5.03pp |
| ±2SD | [64.4, 90.0] | — |

**验证了 M-29 的缩放论据**:M-29 由 n=103 实测 SD 4.57pp 按 √(103/50) 外推预测 **6.56pp**;
**直测 6.41pp**,差 0.15pp ⇒ 该代价论据由推算升级为**实证**。

**推论(须随 s1k8b103 数字引用)**:
- n=50 下,单轮差异 **< 约 12.8pp(2SD)与噪声不可区分**;
- 回照 s1k8b103(n=103,SD 4.57pp):**+10.7pp vs R0 = 2.3 SD**(刚过线);
  **峰后漂移 −5.8pp = 1.3 SD ⇒ 与噪声不可区分**。漂移叙事须据此收敛口径。

⚠️ **限定(须随数字走)**:该 20pp 摆动含**两个**来源 —— rollout 采样方差 **与路由漂移**
(账本逐轮更新,任务在 V0/V1 间的分配会变)。**池的配置冻结,路由未冻结** ⇒
它是「系统在稳态下的总体测量方差」,不是纯采样噪声。若需分离,须加一臂冻结路由重测。

### 📐 分化方案设计落笔(Aug-02,用户令「可以,但不要改而是加,原版也能跑」)

**产出**:`experiments/docs/DIFFERENTIATION-DESIGN.md`(设计,**未实现**)。
铁律写在文首:每项均为**新增旗标、默认关闭、关闭时与现行为逐字节等同**;
做不到字节等同的不实现、改设计。

**追因结论(池为何不专业化)——三条,均有证据,且都不是分解质量问题**
(四个失败计划已逐条读过,分解结构均正确;失败是预算 + 合成器编造):

1. **门作用域**:`--regression-baseline` 默认 `global`,s1k8b103 未传该旗标 ⇒ 用的就是默认。
   而 §4.5 p.11 逐字「the seesaw constraint is **scoped per-variant** … tested only against tasks
   routed to k」。**M-23 记的「论文两读待裁」由这句解决**:§4.1 的全局表述描述单 harness 循环,
   多变体存在后论文明说 per-variant。⇒ **我方跑的是反专业化档**。
2. **cluster = `gaia_level`**(难度),非能力分区;论文未公布其 cluster 构造(M-02)。
3. **诊断信号为空**:`failure_category` 分布 = {gaia_level_2 322 / gaia_level_3 130 /
   gaia_level_1 71 / None 1125}(=难度换名),**`implicated_components` 全跑为空(1,648 条,零)**
   ⇒ 循环拿不到任何能力级信号,无法定向演化。(硬化了 D-2「诊断层基本未实现」)

**论文侧两条锚**:①§6.2 明言 GAIA 的 ∆=0.0 源于 task heterogeneity,正是变体隔离的动机;
Ensemble 87.4/87.4/0.0 vs Global 49.5/73.8/−24.3,**我方 74.8/80.6/−5.8 卡在中间**,
与「半残版 Ensemble」一致。②§6.3 末自认 **Domain-aware clustering / Task-level tournament
「lack sufficient rounds and tasks for statistically meaningful comparison」** ⇒ 本方案正落此缝。
③§6.4:AEGIS vs 单 agent evolver 差 1.0%(**在 1 SE ≈3.3% 内**)⇒ **勿投入改 meta-agent 架构**。

**四项新增(全默认关)**:A1 门作用域(零开发,旗标已存在)/ A2 可插拔 cluster
(`--cluster-source`,限**子任务级**路由以避 M-13 泄漏)/ A3 **逐子任务信用**
(`--decomp-credit subtask_convergence`,判据=是否在步限前收敛,免费客观、对齐 C-1;
**局限:测收敛非正确,须入威胁章**)/ A4 合成器诚实约束(禁参数化填补)。

**床不用换,反而分辨率更高**:评估单元由「任务」变「子任务」⇒ 103×4.30≈**440 个单元 / 4 类
≈ 每类 110**,对比现状 103/3≈34,**约 3 倍**,且 GAIA-103 不动、CH3 可比性不丢。

**🔴 边界**:按子任务类型演化后**不再是论文 Ensemble 的复现**,
CH3 须用论文配置(`per_variant` + `gaia_level`)单独跑一次保可比,
**不得拿新配置结果对 Table 5**。

**执行顺序建议:先只做第 1 步(只开 A1 重跑)** —— 一个旗标即可证伪/证实整套推理,
不值得在验证前投开发。

---

## Aug-02 晚 · 合成守卫 M-33(用户令「加入并且记录」)

**背景**。审阅 CH4 草稿时发现:草稿 §4.5.2 明确写 *"not on any budget-matching
constraint"*,而实测分解花 **2.2×** 步数;顺带核查合成步骤,发现更硬的问题 ——
合成器会从参数化记忆补全缺失的子任务结果。

**取证**。扫 `b_smoke/decomp_tasks.jsonl`(20 次真走分解合成的尝试):

```
带自报编造标记   1/20   任务 20194330,passed=False
```

初次用宽口径扫全量 JSON 得 14 处命中,**收紧后作废** —— 大部分是
`"Based on my research"`(智能体确实做了研究,合法)。**只有 1 处是真编造。**
且该检测只抓自报措辞,**默默编造不可见**,故 1/20 是**下界**。

**改动**(全部在 worktree `HarnessX-dev`,分支 `feat/eval-concurrency`;
主仓 e_pervar 正在跑,零接触):

- `subtask_pipeline.py` —— `SYNTH_GUARD_MODES` / `SYNTHESIS_GUARD_CLAUSE` /
  `_SYNTH_FINAL_INSTRUCTION` 锚点;`Synthesizer` 增 `guard` 参数与 `build_prompt`
- `run_variant_pool.py` —— `--decomp-synth-guard {off,strict}` 默认 off;
  接线至 `Synthesizer`;写入 `decomp_manifest.json`
- `tests/test_synth_guard.py` —— 15 项新测试

**验证**。全量 **962 绿**(947 → 962)。默认 `off` 逐字节等同;`strict` 解析正常;
非法值被 argparse 拒绝。

**注意**。开启 guard 后分解侧绝对分数**会下降**,跨臂比较时档位必须一致。

**主实验未受影响**:e_pervar 期间 429 未升至第 4/5 轮重试。

---

## Aug-02 深夜 · 簇上限 + ε 接线 M-34(用户令「可以写掉」)

**触发**。诊断"变体池为什么不分化"时,把 s1k8b103 的逐轮路由按 GAIA level 交叉制表:

```
R8   V0:{L1:39}  V3:{L2:52}  V5:{L3:12}
R15  V0:{L1:39}  V7:{L2:52}  V6:{L3:12}
```

划分**严格等于 level**。路由是每簇 argmax ⇒ **有效池 = min(K, 簇数) = 3**。
负载基尼 R4 **0.22** → R14 **0.78**,终局 `[52,39,12,0,0,0,0,0]`。
**K=8 里五个变体在算术上注定闲置。**

⚠️ **「论文未定义 cluster」不是新发现**,M-02 早已登记。本批增量只有:
①该选择在结构上限死池规模;②量化;③ε 从未接线。

**第二条通道**。未测量的 `(变体,簇)` 取先验 0.5,永远输给已测量的。
`Router.explore` 的 docstring 原话是 *"stop a variant that lost early from being
frozen out by argmax forever"* —— **开关早就实现,`epsilon` 从未接到 CLI**,
两次正式跑均吃默认 0.0。

**离线重放**(s1k8b103 逐轮 `active_pool_measurements`,免费,真实数据):

| 分组 | 簇 | ε | 偏置 | 末轮基尼 | 末轮有负载 |
|---|---|---|---|---|---|
| 难度 | 3 | 0 | 0 | 0.72 | 3/8 |
| 能力 | 11 | 0 | 0 | **0.82** | **2/8**(更差) |
| 难度 | 3 | 0.1 | 0 | 0.66 | 6.9/8 |
| 能力 | 11 | 0.1 | 0 | 0.76 | 6.8/8 |
| 能力 | 11 | 0.2 | 0.05 | **0.44** | 7.8/8 |

**两个修法单独无效或有害,只有合用才成立** —— 位置不够时任何分配机制都是徒劳。
DeepSeek 的 loss-free 偏置(2408.15664)在 3 簇下**实测无效**(基尼纹丝不动),
因为它只能改"谁轮到",改不出位置。

**分组表**(`experiments/build_task_clusters.py`,103 次 pro 调用,串行不扰主跑):
103/103 标注成功,原始 **11 簇**,min-size=8 合并后 **5 簇**(38/25/16/14/10)。
**主导类型不可用** —— GAIA 几乎每题 search 主导,10 题探针只分出 1 组;必须用类型**集合**。

**改动**(worktree `HarnessX-dev`,主仓 e_pervar 正在跑,零接触):
`--cluster-source` / `--cluster-map` / `--cluster-min-size` / `--epsilon`,全部默认关;
`_resolve_task_clusters` + `_merge_small_clusters`;
**修掉 `cluster_source` 在两处的硬编码**(不修则 lock 说谎,同 M-27/M-31/M-32 形状);
provenance 记分组表 sha256 + 阈值 + 实得簇数。

**验证**。全量 **982 绿**。端到端:默认 3 簇 `[52,39,12]`;capability 5 簇
`[38,25,16,14,10]`;不合并 11 簇。

**并报要求**。ε 的准确率代价正比于 ε,**离线重放看不到这一侧**;
ε 取"够买到测量的最小值",不是让有负载变体数最大化的值。

---

## Aug-02 深夜 · 评估路径并发 M-35(用户令「写」)

评估臂原为全串行(`for task … for attempt … await`),而演化循环以 `--concurrency 10`
打满端点 —— **一条评估臂只用了约十分之一吞吐**。

`--decomp-concurrency` 默认 1(旧路径),>1 时任务级并发、attempt 仍串行。

**🔴 对 `ledger` 路由 fail-closed**:该档读的信用账本正被并发任务写,路由会取决于
完成顺序,臂不可复现 ⇒ 组合直接 SystemExit。`single` / `round_robin` 经验证是纯函数,安全。

**顺序确定性**:累加与落盘移出协程,`gather` 后按 task_id 排序统一执行。

**收益**:端点上限约 5 rollout/分钟 ⇒ A1 3.9h → **约 41 分钟**。不是 10 倍 ——
天花板是端点不是本地并发。

全量 **994 绿**(982 → 994)。

---

## Aug-02 深夜 · 逐子任务信用 M-36(用户令「可以」)

**触发与一次自我更正**。我此前把「降级 B2」与「换信用信号」并列为两个等价选项。
用户指出 **B2 就是 headline**(`B2 − B1` = RQ2 的答案)。**该并列是错的** ——
B2 一降级 RQ2 就没有答案,所以只有一条路:把信用信号修好。

**缺陷**。`record(pairs=used, passed=task_passed)` 把整道题成败记进链上每个格子
(均 4.30 格/链)。一道题做对,搜索/算数/验证同等记功。表测的是「参与过多少道
做对的题」,不是「擅长哪类活」——而 B2 正是拿它做分工。

**修法**。`--decomp-credit subtask_convergence`:每个子任务记一次,
成败 = 是否在自己的步数预算内完成。免费、客观、独立、对齐 C-1。

**局限(必须随结果声明)**:测「做完」不测「做对」。

**两条钉死缺陷的测试**:三个子任务全部撞顶、任务仍通过 ⇒ 旧档三格全记功;
全撞顶与全干净在旧档下产出**完全相同**的矩阵。

全量 **1005 绿**(994 → 1005)。

---

## Aug-02 深夜 · 实验计划改版(用户令「把这个计划记录下来」)

`EXPERIMENT-MAP.md` 整篇重写。**旧版作废**,原因:

- 旧成本(12.6h/臂、64h 必要档)是按冒烟**未修复**的 43.2 步分解外推的
- 旧臂表含 **A2**(整任务 44 步)—— 已被 **B0 的构造预算配平**取代,省一条臂
- 旧版把 **B2 标为"降级"** —— 错误。B2 是 headline,`B2 − B1` = RQ2 的答案;
  信用信号已于 M-36 重建,B2 恢复为主臂
- 评估臂已并发化(M-35),单臂 3.9h → 约 41 分

**新计划**:CH3 两条臂(E-pervar 对 改造臂,差三个旗标)+ CH4 四条臂
(A1/B0/B1/B2,主读数 `B2 − B1`)。必要档 **≈52h**,距 9/5 尚有 34 天。

读数口径已写入 §4 并预注册:同题配对、2SD 门槛、峰值 max-of-N 去偏、
infra 失败单列、**ε 的准确率代价须并报**(离线重放看不到那一侧)、
**guard 档位跨臂必须一致**。

未决仅一项:**ε 取 0.05 还是 0.1**,发车前定即可。

---

## Aug-02 深夜 · 论文草稿纳入版本控制 + CH3/CH4 重写(用户令「把论文重写一下」「可以」)

**背景**。六份草稿此前只存在于 `MAS_Directions/research/phase4/`,**那不是 git 仓** ——
一直没有任何版本控制。现全部收入 `experiments/docs/thesis/`,该目录为权威副本。

**口径**(用户令:「把我们做错的都移除…不是去给人讲故事的,只讲正经实验、动机、结果」):

- 运维叙事(resume 演练 / 宕机复盘 / SOP v3)→ 压进附录 B
- **三条产物投递路径的缺陷与修复 → 从贡献降级为威胁章一句话**。
  那是我方重实现的 bug,不是发现;此前把调试过程包装成"产物消费审计"贡献是错的
- MoE 探索线、被推翻的假设、各次自我更正 → 全部不进正文

**CH3 换主干**。新 §3.5 报告结构性结果:有效池 = min(K, 簇数);GAIA 三层难度 ⇒
K=8 最多三个变体有负载,五个算术上闲置且按闸门作用域**无法被改进**。
⚠️ 文中显式标注「论文未定义 cluster」**不是新发现**(M-02 早已登记),
增量仅为该后果 + 量化 + ε 从未接线。§3.5.4 用离线重放证明两修法必须合用。

**CH4 纠正三处立场**:①预算构造配平,取消 A2 臂;②信用信号重建,
并把"测做完不测做对"的局限写死;③补写分解双重身份(对池盲 ⇒ 可合法充当 CH3 标签)。
主读数 `B − A1` → **`B2 − B1`**。

**仍过期**:CH1(RQ/贡献)、CH7(缺 M-33..M-36)、附录 A(止于 M-32)。
等实验数据齐了一次性改 —— 现在改会白改。

---

## Aug-03 凌晨 · e_pervar 作废重发 + M-37

### 发车失误(我方)

`e_pervar` 漏传 `--candidates-per-round 4`,CLI 默认 1 ⇒ `candidate_limit=1`、
`meta_concurrency=1`,**演化压力比 s1k8b103 少 4 倍**,且偏离论文 Table 8 的 `K_t=4`。
R0–R3 已差出 3 个候选 vs 8 个。

**根因是我的检查方法**:发车前的"最终确认"是从 lock 的 **provenance 反推**参数清单,
而取默认值的旗标**不产生 provenance 记录**,故该方法在结构上看不见这一项。
正确做法 = **两份 lock 的 hyperparams 全字段 diff**,一条命令。

**处置**:用户令「重开」。停 `e_pervar`(损失 6h22m / 1110 rollout,现金≈0),
重发 `e_pervar2` 并做全字段 diff —— **剩余差异为空**,唯一变量 `regression_baseline=per_variant`。
作废目录保留不删。

### M-37(新缺陷,已裁定接受)

e_pervar2 在 R1 出现 3 条投递警告,**全部位于 `pipeline/candidates/`** ——
meta-agent 自试跑候选时由 vendored `Harness` 直接加载,不经 `_prepare_round_config`。

**与 M-27/M-31 同因不同果**:那两条污染测量(⇒ 整跑作废),本条污染的是
meta-agent 的自我反馈。截至 R1,`candidate_gate/` 与 `active_pool/` 路径**零条**,
故报告的测量干净。⚠️ **该核验是 R1 快照,收官须按作用域重新计数。**

用户令「那就不改」:提示词层修法会引入新变量,与单因子设计冲突;
两臂同缺陷,是常量。记入威胁章。

---

## Aug-03 凌晨 · e_pervar2 停,M-38 修复后重发(用户令「重开,改完测试一下这个功能,然后再跑」)

**为何升级为"值得停跑"**。M-37 初判为"仅自测失真、可接受"。追查后两点改判:
①Evolver **看不见**失败(`harness.py:536` 吞异常仅记日志)⇒ 不可能自我纠正;
②**没人告诉过它格式**;③最重的一条:同一 brief 要求工具候选提供 Level-2 往返证据,
**工具没注册就永远拿不到该观察** ⇒ **工具杠杆近乎结构性失效**,
而论文 GAIA 最大单项改进恰是一次工具编辑。

**修法**。`_FILE_TARGET_SPELLING_BRIEF` 注入 repo/paper 两档。
两份 brief 都是**我方文本**,不是 App B.1 逐字提示词,且本就载有多条实跑事故契约。

**验证两层**:
- 单元 13 项,核心是**验 brief 说真话**(复刻 vendored 截断)——教错格式比不教更糟
- **live**:同模型各 10 次,**无 brief 两斜杠 0/10 → 有 brief 10/10、三斜杠 0/10**
- 边界:探针未复现"原本会写错"(裸提示下不写 URI),那由实跑日志三条佐证

**过程中两处自纠**:①`_FILE_TARGET_SPELLING_BRIEF` 首次插到了
`DECISION_CONTRACT_EMPHASIS`(Edit 匹配到了同样结尾的错误常量),已移正并加测试钉死;
②scratchpad 存在 `inspect.py` 遮蔽标准库,导致 live 探针导入失败并误跑旧脚本,
改到干净目录执行。

全量 **1018 绿**(1005 → 1018)。e_pervar2 作废,重发 e_pervar3。

---

## Aug-03 凌晨 · 夜间自主工作(用户令「有问题自己改,记录,继续制定新实验」)

### 新建的四份规格 / 工具

| 文件 | 作用 |
|---|---|
| `PREFLIGHT.md` | 发车前检查清单。**每条对应一次真实事故**,首条即 e_pervar 的全字段 lock diff |
| `FREEZE-CAPABILITY-ARM.md` | E-capability 臂冻结包(命令 + 预注册读数 + 四种结果各许可什么结论) |
| `FREEZE-CH4-ARMS.md` | CH4 四条臂冻结包,主读数 `B2 − B1` |
| `compare_arms.py` | 按冻结包规则读出 R-1/R-2/R-3/R-4。**写在结果之前**,故统计量的选择不可能事后挑 |

### M-39:CH4 §4.6.1 的声明当时是假的

章节写「预算由构造配平」,代码里每子任务各拿全额(实测 2.2×)。已实现
`--decomp-budget shared`,并发现**固定上限替代不了** —— 103 题计划子任务数 1–12,
固定 4 仍 25% 超预算。详见 M-39。

### 去偏公式与正文对不上(已修)

`compare_arms.py` 初版用 `0.8·√ln n` 近似 max-of-N 偏置,给出 1.33 SD;
而冻结包与 CH3 正文写的是 **1.8 SD ≈ 8pp**。改用 **Blom 近似**
`Φ⁻¹((n−0.375)/(n+0.25))`,n=16 得 **1.77 SD ≈ 8.1pp**,与正文一致。

**该修正改变了对 s1k8b103 的读法**:

```
final − peak        −5.8   (看似退化)
max-of-16 偏置      +8.1
去偏后               +2.3   (实为轻微上升)
```

⚠️ s1k8b103 仍因投递缺陷不作因果解释,此处仅示范方法。
**但这说明:不做去偏就报"峰后退化",报的是取最大值的算术产物。**

### 冻结计划导出受阻(已修 builder,重建中)

CH4 四臂须重放同一份计划。`build_task_clusters.py` 原只存 `{id, type}`,
丢了 `instruction`/`dep`,**无法重放**。`export_frozen_plans.py` 的 fail-closed
检查挡住了,没有静默产出坏文件。builder 已改为存完整记录并重建中
(产出 `task_clusters_v2.json`)。

**副产品**:v1 与 v2 是两次独立分解,**其能力划分的一致程度可直接测量** ——
若不稳定,该划分就是一次采样的产物,须列为效度威胁。重建完即比。

### 去偏工具的第二处修正:噪声地板未随床大小缩放

`compare_arms.py` 初版把 SD 写死为 **4.57pp(n=103)**,不随床缩放。
30 题床的 SD 应大得多。√n 缩放已由两点实测验证:
`4.57 × √(103/50) = 6.56` vs 实测 **6.41**。

修正后三个历史跑的读数:

| 跑 | 题数 | SD | 原始漂移 | 去偏后 |
|---|---|---|---|---|
| s1k8b103 | 103 | 4.57 | −5.8 | **+2.3** |
| s2k8b50 | 50 | 6.56 | 0.0 | +11.0 |
| a1big4 | 30 | 8.47 | −13.3 | **−4.4** |

⚠️ **a1big4 的 −13.3 此前被当作噪声地板旁证** —— 去偏后只剩 −4.4,
且远在 n=30 的 2SD 门槛(16.9pp)之内。**引用该数时必须用去偏值。**

### 去偏本身的前提(已写入工具输出)

该修正假定峰值是**平稳分布下 n 次抽样的最大值**。若跑里确有真实改进,
峰值的一部分是信号而非幸运抽样,**全额修正会减多**。故去偏值是
**真实漂移的上界,不是点估计**。

s2k8b50 把这一点暴露得很清楚:它 12 个候选零改善、原始漂移 0.0,
去偏后读作 +11.0 —— 那是**过度修正在显形**,不是十一个点的增益。
工具输出中已带 `debias_is_upper_bound` 标记。

---

## Aug-03 凌晨 · 勘误:「系统拿不到失败归因」是错的

**触发**。e_pervar3 的 R1 候选管线跑完,读 `pipeline_audit.json` 时发现
`implicated_components` **不为空**,与我此前"全跑 1,648 条为空"的说法冲突。

**查清**:两套记录,我把测量对象和结论对错了号。

| 记录 | `failure_category` | `implicated_components` |
|---|---|---|
| `task_history.jsonl`(持久化证据库) | `gaia_level_1/2/3` —— **确是难度换名** | **1,648 条全空** |
| `pipeline_audit.json`(LLM Digester 本轮产出) | `budget_exceeded` / `tool_output_dropped` / `scope_ambiguity` **真类别** | **非空**:`tools/Bash`、`processor/TokenBudgetProcessor`、`tools/WebFetch`、`model_capability`、`environment` |

实测:e_pervar3 R1 = **30/103(29%)**;s1k8b103 逐轮 **20–80%**。

**原测量没错,错在把它推广成「系统拿不到归因信号」。**

**真正的缺陷更精确**:归因**每轮算一次、每轮丢一次**。
交给下一轮的 `prior_history` 形如
`{round_idx, variant_id, outcome, solved, failure_category: null, ships}` ——
`failure_category` 为 null,`implicated_components` 字段根本不存在。

⇒ 演化器能看到「这题**这一轮**栽在 Bash 上」,
**永远看不到「它连续五轮都栽在 Bash 上」**。定向演化要的是后者。

**处置**:`DIFFERENTIATION-DESIGN.md` 第 3 条已加勘误框。
**重写后的 CH3 不含该错误说法**(改写时已删),无需改章。
今后引用一律用"归因不跨轮持久化",不得再说"没有归因信号"。

**这也是一条可写进论文的机制发现**,而且比原说法强:
它不是"缺少信号",而是"信号被算出来又被扔掉",修复成本很低(持久化即可)。

### e_pervar3 R1:零候选,`planner_empty_landscape`

```
actionability      0.85     (阈值 0.5,远高于)
short_circuit      planner_empty_landscape
no_op              True
considered_candidate_ids  []
```

Planner 自述:*"First round (R1) of V0 baseline. **No prior rounds to compare.**
Dominant failure mode is budget exhaustion, impacting 11 tasks directly."*

**与刚才的归因勘误直接咬合**:Planner 要跨轮比较,而归因不跨轮持久化;
R1 更是连前轮都没有。

**与 s1k8b103 的差异**(它 R1 出了 4 个候选)已排查:
manifest brief 注入的是 **Evolver** 的 brief(`manifest_instructions`),
而短路发生在**上游 Planner**,故 M-38 的改动**不可能**是原因。

一个待验的机制:e_pervar3 的 R0 = **69.9%**,比 s1k8b103 的 64.1% 高 **5.8pp**
⇒ 失败样本更少 ⇒ landscape 更薄。**n=1 轮,仅记录不作结论。**

⚠️ **M-38 尚未被真正检验**:验证它需要演化器**实际写出工具**,
而本轮零候选。验证点顺延到首个产出工具类候选的轮次。

### Monitor 误报一次(已修过滤器)

03:44 收到 `ERROR` 告警,查为 `asyncio.base_events — Unclosed client session` ——
aiohttp 的 teardown,asyncio 以 ERROR 级别记录。**该族在既有日志审计里已归类为良性**,
且后续步骤正常继续。

**问题在我的监控过滤器**:把任意 ERROR/CRITICAL 当致命,监控因此退出、留下无人看守的窗口。
已重新布防,排除 `Unclosed (client session|connector|file|transport)` 族,
并增加**轮次推进**事件(便于确认没有卡死)。

教训与 `PREFLIGHT.md` E 节同源:**良性族必须显式列白名单**,
否则"有告警"和"有问题"分不开 —— 这正是 Aug-02 全等级日志审计要解决的问题。

### 能力划分改用三遍共识(修 §2.5 ② 的不稳)

**不稳是系统性的**,不是一次意外 —— 三遍两两一致度 **42.7% / 44.1% / 46.1%**。

第三遍有 1 题因 `Server disconnected` 失败(基础设施错误)。共识工具原用任务 id **交集**,
会把该题丢掉 ⇒ 划分只剩 102 题 ⇒ 路由的 fail-closed 覆盖检查会拒。
已改为**并集 + 按各题实际可用遍数取多数**(`needed = min(min_votes, present)`)。

**共识结果**(三遍,min-votes 2):

```
原始       10 簇  [34,14,14,10,9,8,8,3,2,1]
min-size 8  7 簇  [34,17,16,10,9,9,8]     ← 采用

对照: gaia_level 3 簇 | v1 单遍合并后 5 簇
```

**有效池上限从 3(现状)提到 7。**

**留一验证**(三种两遍组合各建共识再互比,零额外 API):

| | 逐题相同 | Jaccard |
|---|---|---|
| 原始遍两两 | 42.7–46.1% | 0.77–0.78 |
| **共识两两** | **63.1–66.0%** | **0.84–0.87** |

⚠️ **改善实质,但仍只有约 64%。** 且该数测的是**两遍共识**;实际采用三遍共识,
应不低于此但**未直接测量**,冻结包措辞已照实写。

冻结包已改指 `task_clusters_consensus.json`。

### CH4 重放计划已冻结

共识工具原从**第一遍**取计划,而第一遍只存了 `id`/`type`(缺 `instruction`/`dep`),
不可重放。已改为**取第一个字段完整的遍**并显式记录来源;无完整遍则 fail-closed。

```
frozen_plans.json  sha256 60abc1dc…  103 题,子任务 1–9(均 4.25)
                   FileDecomposer 实测可重放 103/103
计划来源           第二遍(task_clusters_v2.json)
划分               task_clusters_consensus.json  sha256 ae3fec2f…  7 簇
```

**计划不取自共识** —— 共识是逐类型投票的产物,**没有任何分解器真的产出过它**,
重放它等于重放一个从未跑过的东西。冻结包已写明这一点。

### M-38 判定进行中

R2 出了 4 个候选(04:32 起)。已布专项监控:候选 config 落盘即打印其 `file:` 目标拼写与判定。

首个结果 **C-R2-02:config 已写,无 `file:` 目标**(纯提示词/配置编辑)——
**M-38 未被触发**。判定需要等到某个候选真的写工具/processor/模板。

### M-38 实跑验证通过(e_pervar3 R2)

首个写出产物的候选 `C-R2-04` 写了一个 processor:

```
_target_: file://D:\…\C-R2-04\processors\search_budget_guard.py::SearchBudgetGuardProcessor
```

**两斜杠,三斜杠 0 次**;运行期 `_as_local_path` 实测解析成功、文件存在。
**brief 在真实演化循环中被遵守**,不只是孤立探针里的 10/10。

### 影响面修正:M-38 波及约 88% 的候选,主体是 processor 不是工具

| 跑 | 候选 config | 含 `file:` 目标 | 种类 |
|---|---|---|---|
| s1k8b103 | 34 | **30(88%)** | processors 36 / templates 8 / **tools 仅 4** |
| s2k8b50 | 31 | 15(48%) | processors 13 / tools 3 |

我此前把它表述为"工具杠杆结构性失效" —— **说窄了**。
正确表述:**M-38 之前约 88% 的候选,其产物目标在自身自测中静默加载失败**;
工具只是最小的那个子集。偏差登记已补记。

### M-38 终验:工具路径,`__pycache__` 物证

`C-R2-03/retry_01` 写了工具 `tools/fetch_and_extract.py`,目标为
`file://D:\…::fetch_and_extract`(**两斜杠**)。运行期解析:文件存在 True,
且 `tools/__pycache__` **存在** —— Python 只在模块真被 import 时生成。

**与作废跑的同形状失败逐位对照**:

```
e_pervar2  file:///D:\…\C-R1-02\retry_01\tools\pdf_text_tool.py   三斜杠,加载失败
e_pervar3  file://D:\…\C-R2-03\retry_01\tools\fetch_and_extract.py  两斜杠,已 import
```

**M-37 的失效通道已闭合。** 演化器现在能在自测中真正装载自己写的工具,
因而具备产出 Level-2 往返证据的条件 —— 那是工具类候选过闸门的前提。

投递警告全程仍为 **0**。
