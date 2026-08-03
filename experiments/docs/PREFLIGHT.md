# 发车前检查清单(Aug-03 立)

> **立此清单的原因**:`e_pervar` 因漏传 `--candidates-per-round 4` 作废,损失 6h22m。
> 当时我做过"最终确认",但确认方式是**从 lock 的 provenance 反推参数** ——
> 而取默认值的旗标**不产生 provenance 记录**,该方法在结构上就看不见这类失误。
>
> 本清单的每一条都对应一次真实事故。**逐条执行,不凭印象。**

---

## A. 参数(挡 e_pervar 那类失误)

**A1. 全字段 lock diff,不是反推。** 参照跑必须先选定,发车 90 秒后 lock 落盘即比:

```python
import json
a = json.load(open('runs/<参照>/experiment.lock.json', encoding='utf-8'))
b = json.load(open('runs/<新跑>/experiment.lock.json', encoding='utf-8'))
d = [k for k in set(a['hyperparams']) | set(b['hyperparams'])
     if a['hyperparams'].get(k, '<缺>') != b['hyperparams'].get(k, '<缺>')]
print('残余差异:', d)
sa = {w[:36] for w in a.get('provenance_warnings', [])}
sb = {w[:36] for w in b.get('provenance_warnings', [])}
print('仅新跑:', sorted(sb - sa))
```

**判据**:`残余差异` 必须**只含本次有意改变的项**;`仅新跑` 的 provenance 必须**只有预期那一条**。
任何意外项 = 立即停跑。

**A2. 一个旗标可能同时控多项。** `--candidates-per-round` 一项同时决定
`candidate_limit` / `candidates_per_round` / `meta_concurrency`。**不要按旗标数核对,按 lock 字段核对。**

**A3. 默认值是隐形的。** 未传的旗标既不出现在命令行,也不出现在 provenance。
A1 的全字段 diff 是唯一能看见它们的手段。

---

## B. 环境(挡 401 与静默降级)

**B1. key 与端点配对。** 免费 `GET /v1/models` 验一次:

```bash
curl -s -o /dev/null -w '%{http_code}\n' https://<endpoint>/v1/models -H "Authorization: Bearer $KEY"
```

**判据**:200。曾经踩过:环境里是官方 DeepSeek key,打实验室端点 401。

**B2. 静默降级的旗标要单独验。** `--search-backend serper` 缺 `SERPER_API_KEY` 时
**静默落到内建兜底链,零日志**,而 lock 照写 `serper ENABLED`。
发车前直接打一次 Serper API 确认 key 有效。

**B3. 数据文件 sha 对上参照跑的 lock。**

---

## C. 代码(挡"跑的不是你以为的代码")

**C1. 主仓全量测试绿**,且在**将要执行的那份工作树**里跑,不是副本里。
**C2. 工作树干净**(`git status --short` 为空),lock 的 `git_sha` 是刚提交的那个。
**C3. 若本轮含新旗标**,在 lock 里确认它被记录;不被记录的旗标 = lock 会说谎,先修记录再发车。

---

## D. 发车后 90 秒

**D1. 进程存活。**
**D2. rollout 计数在涨。**
**D3. 跑 A1 的全字段 diff。**
**D4. `ERROR|CRITICAL` 为 0。**

以上任一不过 = 立即停,不要"先跑着看看"。

---

## E. 首个演化轮之后(R1 结算时)

**E1. 投递警告按路径分类计数:**

```bash
grep -aE 'processor crashed|tool_registry.custom: failed' <log> | grep -cE 'candidate_gate|active_pool'
```

**判据**:`candidate_gate` / `active_pool` 路径下必须为 **0**。
出现在这两个作用域 = 测量被污染 = 停跑(M-27/M-31 的教训)。
只出现在 `pipeline/candidates/` = M-37,已裁定接受,记录即可。

**E2. 直接读候选 config,验 `file:` 拼写**(M-38 的验证点,别只看日志):

```bash
grep -rhoE "file:/*[A-Za-z]:[^'\"]*" <run>/R*/*/pipeline/candidates/*/config.yaml | sort -u
```

**判据**:全部为**两条斜杠**。出现 `file:///` = M-38 的 brief 未被遵守。

**E3. 候选数 = `candidate_limit`**,不是 1。

---

## F. 记录

每次发车在 RUN-LOG 记:run tag、起跑时间、参照跑、A1 diff 结果、B1/B2 验证结果、git_sha。
**diff 结果要贴出来**,不写"已核对"。
