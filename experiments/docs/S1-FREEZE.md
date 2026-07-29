# S1 冻结包(预注册草案 v1,2026-07-29;**待用户过目 + 开跑令,未生效**)

> 目的:K=1 vs K=8 正式对照(RQ1:退化 vs 不退化分岔形态)。全部读数与判读规则
> 先于开跑写死,防事后挑数。生效条件:①用户过目本文件 ②导师 usage 确认(实验级
> 负载许可)③**Serper 付费档就位**(用户实测:a1big5 一场即耗尽免费 2,500 次;
> 以此锚外推 S1 双臂 ≈2 万次、全程 ≈3-3.5 万次 ⇒ $50/5 万次档必须先充,覆盖 ~1.4×,
> 消融臂为额度弹性项;每晚开跑前查 serper.dev 余量——额度枯竭=静默回落爬虫链污染臂)
> ④用户开跑令。届时按本文件生成 launcher 并把最终 CLI 回填此处存档。

## 1. 臂与配置(两臂仅 --pool-k / --run-tag 异,其余逐字同)

- 床 `pilot30.json` × `--num-rounds 16`(=15 适应轮,M-24 对齐,SPEC §7.17-4)
  × `--pass-k 2` × `--max-steps 20` × `--candidates-per-round 2` × `--evolve-retry 2`
- 旗:`--search-backend serper` / `--evolve-commit-bounce on` /
  `--regression-accountability shipped_only` / `--regression-baseline global`(默认,
  §7.17-3)/ step-countdown **不传**(关,§7.17-2)/ `--manifest-mode repo` /
  `--aegis-digester llm --aegis-planner llm --aegis-critic llm`
- 臂 A1:`--pool-k 8 --run-tag s1k8`;臂 A0:`--pool-k 1 --run-tag s1k1`
- `--max-cost 40`(内部假价 kill-switch,≈4× a1big5 帽;现金实际≈0)
- env(labsmoke1 模板):`DEEPSEEK_API_BASE`=实验室端点 + lab key + SERPER_API_KEY
  + HF_TOKEN + PYTHONUTF8 + HARNESSX_REPLAY_TIMEOUT_CAP_S=120
- 执行序:先 s1k8 夜跑 → 白天验收 → s1k1 夜跑;SOP v3 脱会话启动 + 增长哨兵;
  中断恢复一律 `--resume`(护栏含七旗 + 端点纪元字段)
- **并发 = 3(已裁,Jul-29 晚)**:用户选定并按此口径向导师报备("并发 3,每分钟
  几十次请求")——报备口径即运行口径,S1 全程不升并发;若后续需提速(如 B 臂窗口
  紧),先向导师重新报备再动。墙钟推算:~10–14h/臂,s1k8 与 s1k1 各一夜。

## 2. 预注册读数(开跑前写死,不加不减)

1. **主读数**:两臂 final−peak 漂移的配对差(K=8 预期 ≈0 棘轮形态,K=1 预期负漂);
2. R0 实验室纪元重基线 ×2(两臂各一,兼互验)vs 官方纪元 50–53%(跨纪元只作形态参照);
3. **M-23 显形率**:global 基线下"跨变体回退"触发计数与轮位(NEW-1 首次实测);
4. 池谱系:fork/retire 轮位、二代 fork 有无、末池 K、APPLY/FORK/REJECT 分布;
5. 路由:hit rate、簇分化画像(= decomp 前提判据②,直接喂 B 臂设计);
6. 运行病理:预算耗尽率 / infra fail / 403(对照官方纪元 54%→35% 谱系);
7. 成本:内部假价累计 + Serper 调用数(现金口径)+ 双臂墙钟。

## 3. 判读规则(先写死)

- **分岔形态成立**(定性,主):K=8 呈 final=peak / 近零漂移,且 K=1 出现峰后持续
  下行(≥3 轮连降或末段低于峰 ≥ 噪声参考带);
- **幅度口径**:n=30 单轮 CI ≈±18%,一切幅度只报带 CI 的配对差,不与论文 103 题
  幅度直接对标;噪声参考带 = keyless 纪元零 ship 漂移 −13.3pp(a1big4,保守上界,
  系坏环境实测——实验室纪元的零 ship 漂移带若 S1 自产更新之);
  **硬阈值(若需)待开跑前用户钉**,本草案不预设定量门槛;
- **分岔不显**:不改叙事框架——论文主叙事仍可立于复现审计 + B 臂分工(DESIGN §7
  风险预案),S1 数据全额入"复现审计"章;
- 消融(S1 后,视时间):`--regression-baseline per_variant` 单臂重放 s1k8 配置。

## 4. S1 之后的既定序(已裁,不在本包内)

E0 神谕门(oracle 分解注入,`--decomp-source file:` + B0 配置)→ B0/B1/B2 分工臂
(吃 s1k8 终池,`decomp_plans.json` 跨臂同分解)→ 视预算/时间 B3 画像臂。
