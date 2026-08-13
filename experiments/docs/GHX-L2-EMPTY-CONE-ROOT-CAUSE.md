# L2 的因果锥是空的 —— 根因、证据、后果

**日期**：2026-08-13（L0/L2_103x10 在飞期间的收官分析）
**结论一句话**：L2 臂注给 Digester/Planner 的"图证据"在结构上不含诊断信息。
注入管线全通，载荷为空。因此 **L0-vs-L2 这一对不是"图证据是否有用"的检验**，
而是"空锥是否有用"的检验——答案是否，且这是可预期的。

---

## 1. 现象：160 份锥，无一例外地退化

扫 `L2_103x10` 各轮 `R<N>/graph_evidence/cones/*.md`：

| 轮 | 锥文件数 | 每锥调用数 | "causally mattered" 步数 | 出现过的 hook |
|---|---|---|---|---|
| R2 | 43 | min 8 / max 8 | 1 / 1 | 仅 `task_end` |
| R3 | 41 | 9 / 9 | 1 / 1 | 仅 `task_end` |
| R5 | 26 | 9 / 9 | 1 / 1 | 仅 `task_end` |
| R6 | 23 | 9 / 9 | 1 / 1 | 仅 `task_end` |
| R8 | 27 | 9 / 9 | 1 / 1 | 仅 `task_end` |

每一份锥都恰好是**末步那一次 `task_end` 派发里的全部处理器调用**，
"要读哪些步"恒为一个数字（该任务的最后一步）。方差为零。

连带后果：`facts.md` 的"跨任务共享锥节点"退化成**全部处理器 × 全部失败任务**
的满矩阵（R8：9 个 `proc:` 节点，每个都列出全部 27 个失败任务）。
Planner 从这张表里得不到任何区分信号。

## 2. 根因：两条独立事实的合取

### 事实 A —— 控制边按设计不跨 firing 边界

`harnessx/graph/unfold.py:204-210`（`UnfoldRecorder.record_invocation` 文档串）：

> ``prev_in_firing`` is the previous invocation's id *within the same hook
> firing* (``None`` for the first), so control edges never cross firing
> boundaries.

即 U 的 `observed_control` 只把**同一次 hook 派发内部**的相邻调用串起来。
一次 run 有 ~57 次派发 → U 是 ~57 条互不相连的链。

### 事实 B —— 数据面在真实跑里是空的

`observed_data` 边来自 `State.slot_provenance` 的到达定值。实测（`_unfolded.jsonl`
逐行统计，L2_103x10 R2/R4/R6/R7 共 412 份 U 抽样 12 份）：

```
R2-00d579ea…  nodes=1327 steps=21  ctrl=1214 data=0  acc=0
R2-8131e2c0…  nodes=1213 steps=21  ctrl=1106 data=0  acc=0
R7-384d0dd8…  nodes=2446 steps=41  ctrl=2232 data=0  acc=0
…（12/12 同）      TOTAL: {'observed_control': 9074}
```

**`observed_data` 恒为 0，slot 访问记录恒为 0。** GAIA 处理器栈里没有任何处理器
读写受追踪的 slot——这条流水线的真实数据流走的是 messages / context，不是 slot 字典。

### 合取 ⇒ 锥必然退化

`harnessx/ghx/evidence_files.py:230-234`：

```python
anchor = terminal_node(u)                       # causal.py:190 — 最大 ordinal 的调用
cone   = causal_cone(u, anchor, include_anchors=True)   # causal.py:145 — 反向 BFS
```

锚点是最后一次调用（`task_end` 链的末位，通常 `OTelProcessor`）。反向 BFS 沿
`observed_control` 只能在**它自己那次 firing 内部**回溯（事实 A），沿
`observed_data` 无边可走（事实 B）。于是锥 = 末步 task_end 链，恒定。

`causal.py` 与 `evidence_files.py` 本身没有 bug——反向 BFS 是对的，锚点选取
按其文档串也是自洽的（"terminal invocation produced the run's final state"）。
错的是**这个锚点在这张边集上没有可回溯的过去**。

## 3. 对本次战役读数的后果（必须前置声明）

1. **L2 的注入是通的，载荷是空的。** R8 的 `injections.json` 记了 33 条注入，
   cones/facts 文件都写了、指针都进了 prompt——管线可复核。但被读到的内容
   不携带哪一步、哪个工具、哪次模型响应导致失败的信息。
2. 因此 **L2−L0 的差值不能被解释为"图证据的效果"**。它至多是
   "多给一份无信息文档 + 多消耗 Digester 上下文"的效果。论文里凡涉及 L2 的
   句子都必须带这条限定，否则是过度声称。
3. 与之独立地，L2 的 **L1 身份证明（三哈希 ≡L0）仍然成立**——那证的是
   "记录 U 不改变行为"，不受本条影响。
4. 真正的 L2 实验**尚未做过**。修好锥之后需要重跑才谈得上结论。

## 3b. 空载荷被消费了多少、被怎么用（实测）

"载荷为空"和"没人读"是两件事，分开量。全 8 轮统计（分母 = 该轮 digest 总数）：

| 轮 | digests | 引用图证据 | 复述"共享"假象 | 转成底盘级因果主张 |
|---|---|---|---|---|
| R1 | 102 | 10 | 2 | 0 |
| R2 | 103 | 8 | 2 | 0 |
| R3 | 103 | 13 | 4 | 0 |
| R4 | 102 | 12 | 7 | 1 |
| R5 | 99 | 6 | 3 | 0 |
| R6 | 99 | 5 | 2 | 0 |
| R7 | 101 | 11 | 5 | 0 |
| R8 | 97 | 10 | 6 | 1 |
| 合计 | 806 | **75** | **31** | **2** |

Planner 的 `landscape.md` 每轮提及 2–4 次；到 Evolver 的 candidates 只剩 0–1 次，
Critic 的 verdicts **0 次**。

读法（不要夸大）：

- **管线是通的**：75 份 digest 真的打开并引用了锥/facts（按有锥的失败任务数
  263 算，约 28%）。所以 L2 的失败不是"注了没人看"。
- **多数引用是空转**：31 份复述"这九个处理器出现在每个失败任务的锥里"——
  那是锥退化的结构性产物，不是一条关于失败的发现。
- **少数把假象转成了因果主张**：只有 2 份（R4、R8 各一）写成
  "所有失败任务共享这九个节点 → 指向底盘级评分/关停共因，而非任务侧原因"。
  **2/75，属个例，不构成系统性误导**。此处不作"注入退化证据会系统性带偏诊断"
  的主张——数据不支持。
- 有一份 digest 反过来**读出了退化本身**："the graph cone … anchors only the
  processor `task_end` chain"——模型自己看见了空。

结论：空载荷在本战役里**基本是惰性的**（被读、被复述、极少被误用），
所以 L2−L0 的差值主要该归给"多一份无信息文档占了 Digester 的上下文"，
而不是"被证据带偏"。

## 4. 修法（按治本程度排序，本轮不施工）

1. **消息面到达定值（治本）**。把数据面从 slot 字典换成 message 列表：
   记录每次调用对 messages 的追加/改写，读者链回写者。这才是 LLM agent 里
   真正的 reaching-definition 结构，也是"图运行时"相对日志的实质增量。
2. **锚点改选**。锚在产生最终答案的那次调用（末次 `model_response`，或判分器
   读到的那条消息的写者），而不是 `task_end` 链末位的遥测处理器。
3. **对比锥**。失败任务锥的节点集减去通过任务锥的节点集——`facts.md` 的满矩阵
   问题只有做差才消得掉，单看失败侧永远是"所有处理器都在"。
4. **跨 firing 步链边**（仅作兜底）。加"上一次派发末位 → 本次派发首位"的控制边
   会让锥变成整个前缀——非空但同样无区分度，**单独做这条不解决问题**，
   只在与 (1)(2) 合用时有意义。

## 5. 本轮明确不做的事

- **不热修在飞的两臂。** 中途改证据生成会毁掉臂内可比性（L2 前 8 轮吃空锥、
  后 2 轮吃真锥 = 两个不同的臂）。修法在收官后随 L4 发车门统一上车。
- 不因此撤下 L2 的数据。空锥臂是有价值的对照：它给出"注入了但没信息"的
  下界，将来真锥臂的增益要从这条线上量。

## 6. 复核命令

```bash
# 锥退化
python - <<'PY'
import glob,io,re,os
d=r'…\runs\L2_103x10\R8\graph_evidence\cones'
for f in glob.glob(os.path.join(d,'*.md'))[:3]:
    t=io.open(f,encoding='utf-8').read()
    print(os.path.basename(f), len(re.findall(r'^- t\d+:', t, re.M)),
          re.search(r'steps to read:\s*\n(.*)$', t, re.S).group(1).strip())
PY

# 数据面为空
python - <<'PY'
import json,io,collections,glob
p=glob.glob(r'…\runs\L2_103x10\R7\sessions\aegis\*\*_unfolded.jsonl')[0]
c=collections.Counter()
for line in io.open(p,encoding='utf-8'):
    d=json.loads(line)
    if d.get('kind')=='edge': c[d['edge_type']]+=1
    elif d.get('kind')=='access': c['access']+=1
print(dict(c))
PY
```

出处：`harnessx/graph/unfold.py:204-210`、`harnessx/graph/causal.py:145-167`
与 `:190-198`、`harnessx/ghx/evidence_files.py:230-236`。
