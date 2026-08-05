# 13 — P1 零成本离线批:截断感知门 / racing 门 / cap regime(2026-08-05)

> 护栏 `research-guardrails` enforce。零 API、零跑、36-run 重放。
> 本文件是 **P1 的勘误面与结果面**;git / RUN-LOG 两面在批次收尾统一落。
> SPEC 无裁决 —— 纯分析,零配置变更(同 Aug-05 夜批口径)。

---

## 0. 本批要回答的三个问题

roadmap P1 三项,依赖顺序排:

| 项 | 问题 | 脚本 |
|---|---|---|
| **C** | cap-40 对照到底能不能支持推断?(§5-A 欠账) | `v_cap_regime.py` |
| **A** | 把 Schmee-Hahn 插补装进门的 improved/regressed 算术,多少 REJECT/FORK 会翻? | `t_censored_gate.py` |
| **B** | F-race racing 门离线重放能省多少预算,决策变不变? | `u_racing_gate.py` |

C 先跑,因为 A 需要 C 产出的残余成功概率 `r`。

---

## 1. 语料的硬上限 —— **全库只有 38 个 seesaw 决策**

主循环直接清点(遍历全部 36 run 的 `R*/pool_state.json`,取 `candidate_diagnostics`
里带 `improved=` 的候选,即真正走到 seesaw 那一级的):

```
seesaw 决策总数 38    reject 21 / fork 11 / apply 6
贡献的 run 11 个:  e_pervar3 11 · s1k8b103 10 · s1k8 4 · a1big5 3 · organic1 3
                   a1pilot2 2 · a1big4 1 · a1pilot3 1 · forceprobe2 1 · paper4 1 · smoke_hard2 1
improved 集大小    均值 2.7   最大 31
regressed 集大小   均值 3.7   最大 21   非零者 27/38 (71.1%)
```

**这条钉死了 A 与 B 的体裁**:任何"决策翻转"结论的分母是 38,不是 103 题也不是 3 万次 attempt。
⇒ A/B 只能写成**机理演示**(mechanism demonstration),**不能写成统计主张**。
任何"X% 的门决策被截断污染"的句子必须紧跟 `n=38`。

---

## 2. ⛔ 勘误:`q_budget_censoring.py` 的 cap 按 **run** 解析,漏掉半个语料

### 2.1 病灶

`caps_per_run()`(`q_budget_censoring.py:67-80`)把步数上限当作 **run 的属性**,
取该 run 内 `budget_exceeded` attempt 的最大 steps。然后 `main` 只保留"主 regime"
(`caps[run] == 20`)的 attempt 做全部下游统计。

**但上限是 `(run, round)` 的属性,不是 run 的属性。** 主循环逐轮核验 `s1k8b103`:

```
R1 : cap=40   n_budget_exceeded=47   attempts_past_step_20 = 49  (其中通过 25)
R2 : cap=20   n_budget_exceeded=84   attempts_past_step_20 =  0
R3 : cap=20   ...                                            0
...
R15: cap=20   n_budget_exceeded=40   attempts_past_step_20 =  0
```

`s1k8b103` **只有 R1 一轮跑在 cap 40**,R2–R15 十五轮全在 cap 20。
按 run 解析把整个 `s1k8b103` 打上 cap=40 标签 ⇒ **十五轮 cap-20 数据被整块剔出主 regime**。

### 2.2 代价(修复前的实测)

跑 `python experiments/analysis/novelty/q_budget_censoring.py`,SETUP 段自供:

```
attempts with a recorded step count : 6730
step cap by run                     : {20: 16, 40: 1}
dominant regime                     : cap=20, 3640 attempts (54.1%)     ← 45.9% 被丢
```

被丢掉的 ~3090 次 attempt 几乎全部来自 `s1k8b103` —— **本项目全部池级头条数字的那个 run**
(1024 次 `budget_exceeded`,全库最多)。其中真正属于另一 regime 的只有 R1 的约 206 次。

⇒ §13 的每一个数(成功步数分位、`≥cap-2` 的 10.87%、194 对孪生、"最后 5 步内 47.4%")
**都是在缺失半个语料的情况下算的**,必须以 `v_cap_regime.py` 的修正值替换。

### 2.3 这条怎么被抓到的

不是复核脚本抓到的,是**逐轮打印 cap 时**抓到的 —— 按 run 聚合的表格
(`{20: 16 个 run, 40: 1 个}`)看上去完全正常,粒度一降到轮就露馅。
**教训:任何"按 X 解析常量"的辅助函数,必须打印比 X 更细一级的分布做体检。**

---

## 3. P0 治理:audit→s4 合流的冲突面(干跑,未执行)

`git merge-tree --write-tree fix/novelty-s4 fix/audit-ideas-7-12`(非破坏性,不动工作树):

```
CONFLICT (content): experiments/docs/RUN-LOG.md
CONFLICT (content): experiments/variant_pool/SPEC.md
CONFLICT (content): recipe/gaia_evolver/run_variant_pool.py
自动合并成功: experiments/docs/PAPER-METHODOLOGY-DEVIATIONS.md · experiments/variant_pool/gate.py
```

**冲突面 = 六步预案预判的三个文件,一个不多一个不少。** 预案有效。

规模复核(`git diff --stat fix/novelty-s4...fix/audit-ideas-7-12`):
59 文件 / +14,606 / −384,其中 `run_variant_pool.py` 单文件 +4,766 行
—— audit 分支携带 **M-43..M-67 全部 copy-all 实现**(serper 后端、trace_facts、
ship_confirmation、structure invariants 等)。s4 侧只有分析与文档。

⇒ 两分支**不是重复而是互补**;s4 目前 SPEC 只到 **M-42**,M-43..M-67 全在 audit 侧。
**合流未执行**(roadmap 标注等三项裁决);本节仅落盘冲突面,供拍板。

---

## 4. 结果(三脚本)

### 4.1 `v_cap_regime.py` —— regime 修正 + §5-A 结案 + 残余概率

**(a) regime 修正的量级**

```
按 (run, round) 解析 [正确]   cap-20:  75 cell / 6524 attempt      cap-40: 1 cell / 206 attempt
按 run       解析 [此前]      cap-20:            3640 attempt      cap-40:          3090 attempt
误归档 2884 次 attempt = s1k8b103 R2..R15(14 轮)
```

**cap-40 regime 此前被吹大 15 倍**(3090 vs 真实 206)。

**(b) §5-A 结案:不是 z 会缩,是处理臂只有一个 cluster**

```
cap-40(处理) :  1 cluster    206 attempt   pass 0.6699
cap-20(对照) : 75 cluster   6524 attempt   pass 0.6093

cap-20 臂的簇内相关(ANOVA ICC,簇 = (run, round)):
  ICC = 0.0225   平均簇大小 87.0   design effect 2.93   有效 n 2225(名义 6524)
  ⇒ 即便是多簇那一臂,attempt 级朴素 SE 也小了 1.71×

簇 bootstrap(重抽整个 (run,round) cell,2000 draw):
  处理臂  [0.6699, 0.6699]  宽度 0.0000  ← 单簇重抽恒等于自身,区间按构造塌成一点
  对照臂  [0.5849, 0.6335]  ← 非退化,对照用
```

⛔ **正式撤回(脚本内 `RETRACTED, DO NOT REINSTATE` 段)**:cap-40 vs cap-20 的
+24.6pp / z=4.7,以及题配对 bootstrap +24.2pp [+13.8,+35.0]。
**撤回理由从"数值不稳"升级为"设计上不可识别"** —— 题配对 bootstrap 在**那一个 cell 内部**
重抽题,正好把整个问题所在的簇间方差假设掉了;同组配对上的符号检验 p=0.4296 早就在提示这一点。
**语料里只有一个 cap-40 cluster,任何 cap 对照都不成立。** 后继者必须先拿到第二个 cap-40 (run, round)。

**(c) 无需推断即成立的直接计数(修正 regime 后)**

```
真正跑过第 20 步的 attempt(全部来自 s1k8b103 R1):
  21-25 步  18 次 / 通过 14      26-30 步   7 次 / 通过  5
  31-40 步  24 次 / 通过  6      合计      49 次 / 通过 25

cap-20 臂 3975 次成功的步数分位: 50% ≤8 · 75% ≤13 · 90% ≤18 · 95% ≤19 · 99% ≤20
成功落在 ≥cap-2(18)步:  424 次 (10.67%)         [此前 253 次 (10.87%)]

配对孪生(同 (run,round,variant,task),一次撞顶、另一次 exit=done 通过):
  415 对        [此前 194 对,修正后 +221 对全部来自 s1k8b103 R2..R15]
  通过那次的步数 中位 15 · p90 19 · max 20
  最后 1 步内  39 (9.4%)  · 最后 3 步内 127 (30.6%) · 最后 5 步内 196 (47.2%)
```

⭐ **这是本项修正最重要的一句**:证据量翻了 **2.14 倍**,而"近一半成功孪生掐在最后 5 步内到达"
这个数**从 47.4% 变成 47.2%**。修正没有推翻结论,它把一个建立在半个语料上的结论
**换成了建立在全语料上的同一个结论**。边界咬合的直接计数证据现在承重更强。

**(d) 残余成功概率 r(下游 Schmee-Hahn 插补需要的参数)**

```
r̂ = P(通过 | 第 20 步仍在跑) = 25/49 = 0.5102    Wilson 95% [0.3747, 0.6442]   clusters = 1
分步带看衰减:  21-25 → 0.778    26-30 → 0.714    31-40 → 0.250
```

**必须当敏感性参数用,不能当点值。** 三条同时成立的理由(脚本打印在输出里):
① 49 个观测全在一个 (run, round),真实区间宽于 Wilson;
② 该 cell(R1)与后续轮在其他方面也不同 ⇒ **cap 与 round 混淆**,r 可能是 R1 效应而非 cap 效应;
③ 残余概率**逐步带衰减**(0.778 → 0.250),尾部行为不是第 20 步行为。

**(e) 主循环独立复核**

从**原始 JSON**(绕开 `load_cells`)重算三个承重数,**三个全部逐位吻合**:
cap-40 恰好一个 cell `('s1k8b103', 1)` / 206 attempt · 过第 20 步 49 次通过 25 次 · 孪生 415 对。

⚠️ **一处口径差(记录而非缺陷)**:脚本在 `load_cells` 宇宙内工作(只收 ≥2 条可解析 attempt 的记录),
主循环原始 JSON 口径得到 **113 个 cap-20 cell / 6852 attempt**,脚本为 **75 / 6524**。
差额来自被 `load_cells` 丢弃的 332 次 attempt(3 次 `reason` 不可解析的撞顶 + 记录内 <2 条可解析 attempt)。
**这是刻意的**:被修正的 `q_budget_censoring` 也在同一宇宙,同口径比较才成立。
影响仅限 ICC 的簇数分母(113 簇下 design effect ≈2.35,而非 2.93);
承重结论(**处理臂 = 1 簇**)在两个宇宙下完全相同。

⭐ **spec 自纠**:主循环给该脚本的 spec 写了"49 次里有 6 次跑过第 30 步"——**错的**。
数据是**跑过第 30 步的有 24 次,其中 6 次通过**。脚本按数据打印并在输出里显式标注了这处不符。
护栏 C 生效的实例:agent 没有顺着 spec 编数。

---

### 4.2 `t_censored_gate.py` —— 截断感知门重放(本批最重要的一条)

**语料**:38 个 seesaw 决策,**可用 37 个**(排除 1 个 `forceprobe2/C-R1-01`,其
`archive_reason` 是 `FORCED_GATE(fork): real_decision=reject; synthesized_improved=[...]`
——`i_gate_noise_replay` 的正则会把 `synthesized_improved=` 当成真 improved,
不排掉的话状态量重建 assert 就过不去)。10 个 run 贡献。

**(a) 可观测诊断:偏差是真的,而且很大**

门旗为 `regressed` 的题,在**同轮平行的 settled 部署测量**里带撞顶的比例:

```
组          有测量  ≥1撞顶    比例          Wilson 95%     全撞顶   比例
regressed     126      98    77.8%   [69.8, 84.2]      62   49.2%
improved       86      38    44.2%   [34.2, 54.7]       5    5.8%
other         810     183    22.6%   [19.8, 25.6]      95   11.7%

差分 regressed − other = +55.2pp,两个 Wilson 区间不重叠
```

**主循环从原始 JSON 独立重算,五个数逐位吻合**(126/98/77.8 · 810/183/22.6 · +55.2pp · 38 · 1 forced)。

⚠️ 这是**两次不同 rollout 之间的关联**,不是门自身 attempt 的测量 ——
**门自己的 rollout 在盘上不带 exit_reason**(`candidate_diagnostics[cid]['evaluation']` 只有
`(n_pass, n_att)` 聚合)。这是本分析的中心数据限制,脚本全程如实标注。

**(b) 代理效度检验:过了**

模型拟在 settled rollout 上、用在 gate rollout 上,必须先验证:
```
两个 rollout 共有的 103 题:  Pearson r = 0.957    mean|settled − gate| = 0.076   ⇒ PROXY HOLDS
```
这一条本来是最可能崩的地方,结果留了很大余量。

**(c) 三个估计量,同一批决策**

```
[1] treat-as-uncensored(现状)   37/37 逐个重现记录在案的裁决(assert 通过)
[2] listwise deletion            3/37 改变决策,且**双向**:
      a1big5/C-R3-01    FORK  → APPLY   (丢掉 2 道全撞顶的 regression ⇒ 否决被撤)
      s1k8b103/C-R2-04  FORK  → APPLY   (丢掉 3 道)
      organic1/C-R2-02  APPLY → REJECT  (丢掉 1 道全撞顶的 improvement ⇒ 收益被撤)
    ⇒ Hutter「反向偏差」在门上的正确形态是**非单调**,不是「一律偏向 APPLY」
[3] Schmee-Hahn 多重插补  M=2000, seed=0, r̂=0.5102
```

**(d) ⭐ 结论:截断确实到达决策层,但到达的是 scope,不是 throughput**

```
变体 A(保守:只撤销 regression,不制造 improvement)
  每次插补翻转 1.26 个决策(中位 1,95% 区间 [0,3]),**全部是 FORK → APPLY**
  丢弃的 improvement:  0 个
  前向吞吐损失界:      0.00pp     ← 远低于 9.03pp MDE

变体 B(激进:额外把插补通过的 never-solved 题提升为 improvement)
  每次翻转 10.93 个(95% [8,14]):REJECT→APPLY 5.30 · REJECT→FORK 4.36 · FORK→APPLY 1.26
  最大单 run 界 12.62pp(a1big4,14 道插补 improvement 中 13 道后续从未被解出)
  但这 14 道全是**从未被任何变体解出**的题的插补通过,不是门丢掉的观测通过
```

**r 敏感性**(`r=0` 严格给 0 翻转,sanity 通过):
A 变体首次期望翻转出现在 **r=0.50**,B 变体在 **r=0.05**。
⇒ **A 的结论对 r 极不敏感(它本来就不救 REJECT),B 的结论几乎完全由 r 撑着**,
而 r 只由**一个 (run, round)** 识别。

**(e) 这对方向意味着什么(必须原样传下去)**

doc 12 §6 给截断线开的四条必备条件里,第 4 条是
「**回答 so what:量化假 REJECT/假 FORK 造成的损失,否则就是一篇关于惰性机制的负结果**」。

**现在这条被回答了,答案在吞吐维度上基本是负的。**

- 观测层:偏差真实且大(+55.2pp,区间不重叠);
- 决策层:**可辩护的那个修法只改 scope** —— 把一个编辑从「隔离进 fork」改成「全池 ship」,
  不多解一道题,吞吐损失界 **0.00pp**;
- 只有**激进变体**才救得回 REJECT,而它靠的是给从未解出的题插补通过,且靠单簇识别的 r。

⚠️ **scope 效应在本语料上同样是惰性的**:池从未满过(K=8 而同时在册最多 4),
所以多出来的 fork 既没有触发退役,也没有稀释路由。
**FORK→APPLY 要能变成吞吐,前提是池满 + 有退役压力** —— 那是一个**可以设计的实验条件**,
不是本语料能回答的问题。这是本批给 P2 留下的最具体的一条钩子。

**(f) 港口声明**:估计量 = Schmee & Hahn 1979;偏差结果 = Hutter et al. 1310.1947。
唯一原创动作是**把它装到演化门的 improved/regressed 算术上**,而不是装在运行时截断上。
`9.03pp` 是全脚本唯一外借常量,处处标 `[imported from r_effective_n.py]`。

---

### 4.3 `u_racing_gate.py` —— F-race 门离线重放

**(a) 确定性支配停(无统计)**

按随机题序逐题喂,一旦"剩下的题怎么翻都不改变裁决"就停:

```
决策一致性  37/37 候选 · 37000/37000 试次 = 100%(构造上必然,已 assert)
平均需评估的 T_k 比例  mean 81.1% · median 100% · p10 18.8%

按裁决拆(这一拆才是重点):
  apply  n= 6   平均评估 100.0%   最优题序 100.0%
  fork   n=10   平均评估  30.1%   最优题序   5.7%
  reject n=21   平均评估 100.0%   最优题序 100.0%

省下:  rollout 773.4 / 2475 = 31.2%   ≈ $540(均价外推)
最优题序上界: 1036 / 2475 = 41.9%  ≈ $725
```

⚠️ **APPLY 与 REJECT 一次也省不了**,因为任何一道未看的题都可能翻转它们
(REJECT 可能因为出现一个 improvement 变 APPLY/FORK;APPLY 可能因为出现一个 regression 变 FORK)。
**31% 的节省全部来自 FORK**,而 FORK 只占 10/37。

**(b) ⭐ 统计臂:不是预期的空值,而是暴露了 fork 规则本身的病**

spec 预判「1–3pp 效应 + |T_k|≤103 ⇒ 符号检验永远不显著」。**这个预判在细微效应上成立,
在 fork 子集上不成立** —— 因为 `min_fork=(1,1)` 的 seesaw
**只要有 ≥1 道 improvement 就 fork,不管背上多少 regression**。

主循环独立复核全部 10 个 FORK:

```
run         cid          improved  regressed   net    双侧符号检验 p
s1k8b103    C-R6-01             4         21   -17    p=0.001   ← 净劣势
s1k8b103    C-R13-01            3         15   -12    p=0.008   ← 净劣势
s1k8b103    C-R7-02             2         14   -12    p=0.004   ← 净劣势
a1big5      C-R1-01             3          5    -2    p=0.727   ← 净劣势
s1k8b103    C-R11-04            2          4    -2    p=0.688   ← 净劣势
s1k8b103    C-R2-04             3          3    +0    p=1.000
a1big5      C-R3-01             5          2    +3    p=0.453
e_pervar3   C-R2-01            14          3   +11    p=0.013
s1k8b103    C-R1-01            21          2   +19    p=0.000
s1k8b103    C-R4-01            31         12   +19    p=0.005

净劣势 FORK: 5 / 10 (50%)      其中显著净劣势(p≤0.008): 3 个
```

**门为了 2 道题的改善,开一个变体槽去背 14 道回退。**

F-race 的稳健裁定(在多数题序下都触发):
- **3 个稳健分歧**:F-race 丢弃,seesaw 却 fork(上表 p≤0.008 那三个);
- **9 个稳健一致**:双方都 REJECT,但 F-race **更早**到达
  —— 这正是确定性臂(§4.3a)永远省不到的那一类;
- **2 个题序脆弱**:全集 p=0.688 / 0.727(**不显著**),却在少数题序下被中途淘汰
  ⇒ **朴素 F-race 的分块重复检验会抬高 I 类错误**,真上线必须配 alpha-spending / group-sequential。
  脚本把每个候选的**全集 p** 一并打印,让这条自证。

**(c) 省下的预算买不到功效(承重负结果)**

```
MDE(n) = 9.03pp × sqrt(2/n)          [1/√n 投影,不是测量;外借常量已标注]
总 rollout 固定 2475,题位从 1242 降到 855.3
每题 attempt   1.993 → 2.894        MDE  9.03pp → 7.51pp
最优题序上界   → 3.419              MDE       → 6.91pp
```

**编辑量级 1–3pp,MDE 只从 9.03 掉到 7.51 —— 仍然差一个数量级的量级。**
racing **不制造功效,只重分配预算**,AUDIT 文档对 ⑧ 的预判在这一点上完全命中。

**(d) 一处工程陷阱(值得记)**:`T_k` 原本是字符串 task-id 的 `set`,
Python 逐进程字符串哈希随机化会让**加了种子的 shuffle 在不同进程给出不同结果**。
改成从 `sorted(...)` 建列表后,两次运行输出逐字节相同。
**"设了 seed" ≠ "可复现",只要中间经过 set 迭代。**

---

## 8. N-15a 一致率驱动预算(`x_budget_reallocation.py`)—— 信号真实,交付近零

**(a) 天花板本来就小**

```
never   3 题   always  13 题   swing  87 题        (主循环独立复核逐位吻合)
花在 never+always 上的 attempt   1012 / 6730 = 15.0%   ← 唯一"可见浪费"
p_t > 0 的题                     100 / 103            ← 这才是天花板小的真因

oracle 上界(已知真 p_t,不可达)    +4.55pp
```

**两次 attempt 已经把大部分可达质量吃掉了**;最优解只从 never/always 里腾出 19 次 attempt 可重分配。

**(b) 三重控制**

```
in-sample  (A 估 p,A 上打分)      +4.65pp
out-of-sample (A 估 p,B 上打分)   +3.64pp        过拟合溢价 +1.01pp
null(1000 次标签置换)95 分位      −6.02pp        ⇒ OOS 过线
```

⚠️ **null 这道门是弱门,不能当作支持证据。** 它的均值是 **−7.82pp**(负的):
贪心给出的是**集中**分配,而由凹性,集中分配落在随机题上比均匀更差
⇒ **任何放对位置的集中都能过线**。真正有信息量的是 OOS 与 in-sample 的差(+1.01pp 溢价)。
agent 自己指出了这一点,没有拿"过了 null"当卖点。

**(c) ⛔ 判死:pass@k 的增益在选择那一步蒸发 76%**

```
配置                          pass@k 增益    交付增益(过 prefer_done 之后)
prior-driven  [OOS,可行]        +2.23 题        +0.54 题 = +0.52pp
agreement-driven [反应式]       +1.05 题        +0.69 题 = +0.67pp
oracle        [上界]            +4.68 题        +2.91 题
```

**可行规则的交付增益 +0.52 / +0.67pp,比 9.03pp 的门 MDE 低一个数量级** ——
**现有仪器连测都测不到。N-15a 作为分数机制死了。**

**(d) ⭐ 但它死的方式,是本批最有用的一条**

`prior-driven` 的 pass@k 增益 **+2.23 题,交付只剩 +0.54 题 —— 蒸发 76%**。

**多给的 attempt 只兑现了四分之一,因为你挑不出对的那个。**

这条对**任何"加东西"的机制**都成立:加 attempt、加变体、加投票,都撞同一堵墙。
它和另外五个路由/多样性的零、以及 `exit=done` 的 +6.66pp,指向同一句话:

> **这张床上的瓶颈是选择,不是生成。**

**(e) 选择层还剩多少**

```
pass@1 (现状)      60.77%
prefer_done        67.43%    ← 一行规则,+6.66pp
oracle pass@2      71.59%
                   ─────
剩余可取           4.16pp    ← 最好的现有规则之后,选择层自己还留在桌上的
```

**(f) 两条语料事实(assert 逼出来的)**

- 36 个有 `comparison.json` 的目录里,**只有 17 个真跑了 pass_k≥2**;另 19 个是单次 attempt 的
  smoke/calib/aegis 跑,重分配在它们身上无定义,已排除。
- 原始 attempt/记录分布 `{0: 1.6%, 1: 8.7%, 2: 89.7%}` —— "每题两次"是主流但**不是全部**。

---

## 7. N-10 子编辑级接受(`w_subedit_acceptance.py`)—— 机制真实,机会很小

> 编号见 `00-NUMBERING.md`:N-10 属于用户审过的想法池,与 P1-A/B/C 不同线。
> 本节放在这里是因为同一夜完成,且与 §6 的 fork 判据同族。

**(a) 机制本身坐实**

```
带 verdict 的候选          84 个
mutation_surface 尺寸      {2:27, 3:4, 4:28, 5:22, 6:3}   100% 多 surface,均值 3.64,最小 2
每候选 distinct bucket 数  {2:32, 3:41, 4:11}
CriticRejection            只有 (candidate_id, reason),零逐项裁决位
gate.py::_decide           只吃整份候选
```

**接受单位平均由 3.64 个部件组成,而整条链上不存在比"整份候选"更小的接受单位。** 这一条是硬的。

**(b) ⛔ 但机会很小 —— 主要死因是数据,不是设计**

```
rejection 与 verdict 同键相交   1 / 40        ← Critic 只给排序过的候选写 mutation_surface
                                                被拒的只留 {candidate_id, reason}
替代来源 _meta_scratch/changeset.json 覆盖 37/40(scratch 产物,非契约输出)
交叉验证门(82 个双源候选):  一致 82/82,不一致 0(0.0%) ⇒ 放行

只点名部分 surface 的 rejection(排除 config.yaml,主口径):   6 / 35 可分类 = 17%
                                       (计入 config.yaml 的敏感性口径:  22/36 = 61%)
重放后真正能 ship 部分编辑的候选:  3 个,共 3 个 surface
```

**为什么这么低**:40 条 rejection 里 **27 条(agent 分类器)/ 28 条(主循环独立复核关键词)是
「缺 Level-2 / capability 证据」的全局否决** —— 它指控整份候选,不指控某个 surface。
逐项接受对这类否决**无能为力**。

⭐ **这条本身指向另一个杠杆**:如果 70% 的拒绝理由是「你没给证据」,
那瓶颈不在**接受粒度**,而在**证据要求**(以及候选生成端为什么系统性交不出证据)。
这不是 N-10,是一条新的、尚未编号的线索。

**(c) config.yaml 普遍性 = 分离性的主证据(不是噪声)**

```
config.yaml 出现在 84/84 个候选的 mutation_surface 里(主循环独立复核吻合)
⇒ 这个系统里没有任何两个 surface 是文件不相交的
⇒ 任何"只接受一部分"都仍要改写那个共享文件
```

两种读法数据分不开:(a) config 是**接线**(去掉一个 processor 就必须改 config)⇒ 真耦合;
(b) config 是**被动清单**⇒ 可以只带子集。
`file_changes` 的实际 diff **没有持久化**,盘上的数据无法裁决。这是硬限制,不是懒。

**(d) 判词**

**机制真实,机会小,证据弱。** N-10 是一个便宜、值得做原型的机制,
**但这批数据没有把它立成一个 win**。两件事必须同时为真它才付得起:
① 真的能分离(而 config.yaml 普遍性正好指向反面);
② 那些没被点名的 surface 值得留(而实测里有 4 个此后**再没被重新提出过**)。

**(e) 顺带的观测**:19 条 `strategy_concerns` 明确在说跨轮重复尝试,
含 `[s1k8b103 R10]`「Prompt bucket (C-R10-01) continues to be attempted across rounds」
—— **churn 是 Critic 自己记下来的**,不需要我们去证明它存在。

---

## 6. ⭐ 本批的合流结论:病灶是 fork 规则,不是截断

A 与 B 从两条互不相干的路走到同一处:

| | 说的是什么 | 方向 |
|---|---|---|
| **§4.2 (A)** | 截断插补把 FORK → APPLY(1.26/次),因为 regression 里混着撞顶 | fork **不该发生** |
| **§4.3 (B)** | F-race 把净劣势 FORK 判为丢弃(3 个稳健,p≤0.008) | fork **不该发生** |
| 主循环复核 | **10 个 FORK 里 5 个净劣势**;最极端 4 improved / 21 regressed | fork 门槛太松 |

⚠️ **口径限定(引用"10 个 FORK"时必须带)**:该数取自**已完成的 run**(有 `pool_report.json`,
即三个姊妹脚本的默认语料)。放宽到**任何有 `pool_state.json` 的 run**(多出未完成的
`e_pervar` / `e_pervar2` / `s2k8b50`)后是 **50 seesaw / 13 FORK / 6 净劣势**。

```
完成的 run          37 seesaw   10 fork   5 净劣势 (50%)   fork 分布 {a1big5:2, e_pervar3:1, s1k8b103:7}
所有有 pool_state    50 seesaw   13 fork   6 净劣势 (46%)
```

**结论对两个口径都成立**(净劣势约占一半),但分母不同,**引用时必须写明哪一个**。

**`min_fork=(1,1)` 是我方工程默认,不是论文规定。** `SPEC-E01` 逐字:
> fork 默认是 `min_fork=(1,1)`。**论文未给计数阈值**;旧 `(2,2)` 是我方工程选择,现仅作为 ablation。

论文 §4.5 只说「forks a new variant rather than rejecting the edit outright」,
**没有给任何判据**。所以这里不是"我们实现错了",而是**论文留空的位置我们填了一个太松的值**。

⇒ **可落地的机制修改(不是审计,是让系统跑分更好的改动)**:
把 fork 的条件从「improved 非空且 regressed 非空」改成
**「improved 非空,且 improved 相对 regressed 未被净支配」**——
一个净优势条件(或直接用符号检验作门),改动量在 `gate.py::_decide` 里是几行。

**为什么这条值得做而前两条不值得单独做**:
- 截断修法在本语料上吞吐损失界 **0.00pp**(§4.2d),单开没有"so what";
- racing 省下的预算把 MDE 从 9.03 拉到 7.51,**买不到功效**(§4.3c),单开也没有"so what";
- 但**两者都指向同一个可改的判据**,而这个判据**论文空着**、我方填得过松、
  改动便宜、且效果可以在 36-run 上离线重放验证(5/10 个 fork 会被拦下)。

⚠️ **动手前必须先回答的两件事**(不得跳过):
1. **拦下这 5 个 fork,池的最终分数会更好吗?** 目前只知道"这些 fork 净劣势",
   不知道"不 fork 会怎样"。需要按 §4.2e 的路子做前向重放:
   被拦下的编辑其 improvement 是否在后续轮被别的变体拿到。
2. **n=10 个 FORK。** 任何"5/10"的句子必须带这个分母。这是机理演示,不是统计主张。

---

## 5. 勘误表(本批)

| # | 我方此前说过 | 实际 | 触发 |
|---|---|---|---|
| ⛔10 | doc 12 §13 的截断统计:253 次 `≥cap-2`(10.87%)· **194 对孪生** · 最后 5 步内 47.4% | cap 按 run 解析漏掉 2884 次 attempt。修正后:**424 次 (10.67%)· 415 对孪生 · 47.2%**。证据量 ×2.14,**结论不变** | 主循环逐轮打印 cap |
| ⛔11 | §5-A「必须用 run 级聚类重报,**z 会缩**」 | 不是缩的问题。处理臂 = **1 个 cluster**(`s1k8b103` R1),簇 bootstrap 区间宽度按构造 = **0.0000**。cap 对照**不可识别**,不是精度不足 | `v_cap_regime.py` §2 |
| ⛔12 | doc 12 §13「主 regime = cap 20(**3640 attempts**),`{20: 16 run, 40: 1}`」 | 真实 cap-40 只有 **206** 次 attempt / 1 个 (run,round);按 run 的口径把 cap-40 吹大 **15 倍**,把 cap-20 缩小 **1.79 倍** | 同上 |
| ⚠️13 | (新)`r_effective_n.py` 的 MDE 无出处 | `(z_a/2+z_b)·SE` = **Miller 2411.00640 Eq.9**,非我方推导;2602.07150 必须正面区分;IRT 选题机制是借的,新的只是**用途**。已写入脚本 docstring 与打印输出 | doc 12 §12 欠账 |
| ⚠️14 | doc 12「seesaw 语料」未给分母 | **全库只有 38 个 seesaw 决策**(可用 37,排除 1 个 `FORCED_GATE` 探针),其中 **FORK 仅 10 个**。此前所有门相关判词都缺这个分母 | `t_censored_gate` / `u_racing_gate` §1 |
| ⚠️15 | `i_gate_noise_replay.seesaw_decisions` 可直接使用 | 它的正则会把 `forceprobe2/C-R1-01` 的 **`synthesized_improved=[...]`** 当成真 improved(该候选 `archive_reason` 是 `FORCED_GATE(fork): real_decision=reject`)⇒ 状态量重建对不上。**任何基于它的分析必须先排 `FORCED_GATE`** | 两个 agent 独立撞到 |
| ⚠️16 | (新)`min_fork=(1,1)` 是合理默认 | 10 个 FORK 里 **5 个净劣势**,最极端 **2 improved / 14 regressed**(p=0.004)。论文 §4.5 **未给任何 fork 判据**(`SPEC-E01` 逐字),这个过松的值是我方填的 ⇒ 见 §6 | §4.2+§4.3 合流 |
| ⚠️17 | (新)加了 seed 就可复现 | `T_k` 曾是字符串 `set`,**逐进程字符串哈希随机化**让加种子的 shuffle 跨进程给出不同结果。凡中间经过 set 迭代的随机流程都要先 `sorted()` | `u_racing_gate` 自查 |
| ⚠️18 | §6 首版写「10 个 FORK 里 5 个净劣势」未给口径 | 该数是**已完成 run**(有 `pool_report.json`)的语料。全语料是 **13 / 6**。两个口径结论一致(约一半),但**分母必须随句子走** | N-10 agent 报 13/6 与主循环 10/5 不符,复核后系population 定义差异 |
| ⚠️19 | §6 首版称最极端 fork 是「2 improved / 14 regressed」 | 那是 `s1k8b103/C-R7-02`。**真正最极端的是 `C-R6-01`:4 improved / 21 regressed**(p=0.001)。主循环给下游 spec 时引了次极端的一个 | 同上 |
| ⚠️20 | (新)Critic 的 `rejections` 可以用 `mutation_surface` 分析 | **verdicts 与 rejections 是不相交的候选集** —— 40 条 rejection 里只有 **1 条**与某个 verdict 共享 `(run, round, cid)`。Critic 只给它**排序过**的候选写 `mutation_surface`,被拒的只留 `{candidate_id, reason}`。⇒ N-10 的逐项分析必须另找 surface 来源(`_meta_scratch/changeset.json`,覆盖 37/40,**scratch 目录产物非契约输出**) | N-10 agent 拒绝按 spec 硬跑,主循环复核确认 |
