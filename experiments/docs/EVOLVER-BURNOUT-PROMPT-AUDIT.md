# Evolver 烧穿 · Prompt 与输入面审计（2026-08-12）

**触发**：L2_103x10 R1 Evolver `exit=budget_exceeded steps=200 candidates=0`（$27.16 / 8.77M tok），
产物全部造出但从未提交（`applied/` 有代码+config+验证脚本，`candidates/` 空）。
历史同款：L0 官方基线 v3 时代 "Evolver R2 烧穿零候选"。本文回答：**模型到底看到了什么，
哪些输入在制造这个行为，改哪里**。

会话解剖数据（200 步逐步账）：Bash 115 / Read 41 / Write 41 / Grep 11 / Edit 2；
step39/46 已写出两个候选的实现，step83/127 写出两份 applied config，step183–198
临帽前在重写第四版验证脚本；会话被 compaction 切成 5 段。

---

## 1. Evolver 的完整输入面（逐件清点）

| # | 部件 | 内容 | 出处 |
|---|---|---|---|
| 1 | **系统提示** | 渲染后的 `evolver.md` 全文（434 行，含验证教义、manifest 规格、YAML 形状库、参考实现表） | `harnessx/aegis/templates/evolver.md` → `StaticSystemPromptBuilder`（`agents/evolver.py:146`） |
| 2 | **任务消息** | "Produce K candidate manifests + K applied scratch dirs… only files written via write_tool survive" | `stages/propose.py:60-64` |
| 3 | **硬约束（模型不可见）** | `max_steps=200, max_cost_usd=100`；CostGuard $100 兜底 | `stages/propose.py:66-67`、`agents/evolver.py:225` |
| 4 | **Compaction 摘要** | >300k tok 触发，摘要模板四节：Decisions / Facts / Errors / **Pending Actions**；retention 4 轮、驱逐 90% | `agents/evolver.py:227-246` |
| 5 | **Scope 门提示** | 读被拦时的 hint（指向参考实现与 run 台账）；写域=candidates_dir+applied_root | `agents/evolver.py:176-208` |
| 6 | **自选读料** | landscape.md、102 份 digest、trajectories、INDEX、三本台账（"No required reading… Pull what supports your candidates"） | 模板 174-195 行 |
| 7 | GHX 注入 | **无**（L2 只注 Digester×42 + Planner×1；两臂 Evolver 输入同构） | R1 injections.json |

**关键缺席**：任何形式的步数/成本进度可见性。模型从第 1 步到第 200 步对"还剩多少预算"零感知。

## 2. 烧穿因果链（每环带 prompt 证据）

1. **提交末置是明令，不是坏习惯**——模板 96-97 行：
   > "you MUST complete this loop IN YOUR SESSION **before writing the manifest**"
   验证（含 Level-1 + Level-2）被规定为 manifest 的前置条件。提交合法地排在最后。
2. **验证教义是全文档情绪最重的段落**（93-172 行，约 80 行）："the single most
   valuable investment your session can make"、"each burns a round"、"~$60 wasted"。
   v4-pro 的服从性把它执行到病态——8 个验证脚本、同一脚本四版重写。
3. **无截止感**：约束在 BaseTask 里，模型看不见；没有 step countdown 处理器；任务
   消息不提预算。它没有任何信号告诉它"该收尾了"。
4. **manifest 仪式很重**：frontmatter 五大件（capability_evidence / file_changes /
   predicted_impact 三分类 / attribution_signature）+ 四个 body 节。两个候选的
   合规 manifest ≈ 10–20 步——恰好是被步帽砍掉的尾部。
5. **压缩失忆放大**：8.77M tok / 300k 阈值 → 压缩 ~4 次（5 段）。摘要模板虽有
   "Pending Actions" 节，但不强制盘点"哪个候选的 manifest 还没写"；教义段
   （在系统提示里）每段都在，"我还没提交"的进度状态却不保证在。
   （注：`agents/evolver.py:228-229` 的 E5 注释假设全程 600k tok"至多压 2 次"——
   v4-pro 实际用量 8.77M，假设失效 14 倍。）

R2 之所以 120 步成功：R1 的 applied/ 遗产把"探索+实现+验证"三段全部预付，
剩余步数刚好够走完 manifest 仪式。

## 3. 改法（P-1 至 P-5，全部是下次发车的偏离台账项）

**部署纪律**：所有条目=被测系统变更 → 两臂同改（baseline 分支 + cherry-pick ghx，
与 serper 补丁同流程）、正在跑的 L0/L2 不追溯、L4 臂发车前统一生效、
偏离台账各记一行（先例：critic.md 行、--search-backend）。

### P-1 提交先行（治"提交末置"，最重要的一刀）
`evolver.md` 96-97 行 "before writing the manifest" → "before **FINALIZING** the
manifest"，并在 "## Where to write" 前新增一段：

> **Draft manifests EARLY.** As soon as a candidate direction is chosen, write its
> manifest skeleton to `candidates/C-R{round}-<NN>.md` (frontmatter + stub body,
> `capability_evidence: []`) and its `applied/` config draft — THEN verify and refine
> in place. A draft manifest with thin evidence is reviewable (the Critic can
> ask-more or reject); an unwritten manifest is a wasted session. Never end the
> session with work in `applied/` that has no manifest in `candidates/`.

### P-2 预算可见性（治"无截止感"）
两级，可只做 (a)：
(a) `stages/propose.py` 任务消息追加一句：
    "Hard limits: 200 tool steps, $100. Budget ≈1/3 for exploration, ≈1/3 for
    build+verify, and RESERVE the final ~30 steps for writing/finalizing manifests."
(b) `build_evolver_harness` 挂现成的 additive `step_countdown` 处理器
    （`harnessx/processors/control/step_countdown.py`），每步给模型报剩余步数。

### P-3 锚点格式防线（顺手治判决彩票，双臂已各废 5 份判决）
`critic.md` 的 evidence_anchors 规格处加：

> Each anchor must be ONE single-line YAML string. NEVER put `: ` (colon+space)
> inside an anchor — YAML would parse it as a nested mapping and the whole verdict
> fails validation. Write annotations with ` — ` instead:
> GOOD: `- applied/_verify_proc.py — L1+L2 assertion, GAIA 20->40`
> BAD:  `- applied/_verify_proc.py (L1+L2 assertion: GAIA 20->40)`

### P-4 验证预算上限（治"无底洞"）
`evolver.md` 验证教义段尾加：

> **Verification is DONE when one Level-1 and one Level-2 run pass per candidate.**
> Do not re-run verification after unrelated edits; do not write more than two
> verify scripts per candidate. Extra verification past this point spends steps
> the manifest needs.

### P-5 压缩摘要保单（治"压缩失忆"）
`agents/evolver.py` summarize_prompt_template 四节后加第五节：

> "5) Deliverables status — for EACH candidate under work: manifest written to
> candidates/? applied config.yaml written? list the exact file paths still missing."

（M4c 是同一问题的机制解；本条是它落地前的 prompt 级临时保单。）

## 4. 不改的东西（边界）

- Level-2 证据要求本身**不降**——它是官方管线对"幽灵采纳"的唯一真防线，
  今天的 Critic 判空目录标本恰恰证明它不能松。改的是**顺序与上限**，不是标准。
- 步帽 200 不动：P-1/P-2 生效后 200 步足够（R2 实测 120 步走完全流程）；
  单独放大步帽只会把无底洞挖深。
- 早停耐心 `noop_streak >= 2`（`run_meta_aegis.py:963`）本轮不动，P-1+P-3 会
  从源头把 noop 概率打下去；若 L4 前仍想加保险，2→3 单列一行偏离再议。

## 5. 预期读数变化（供 L4 后验证）

- Evolver 烧穿率（exit=budget_exceeded 且 candidates=0）：基线 1/4 轮 → 目标 0。
- 判决 anchor-malformed 率：基线 10/10 份中 5 废 → 目标 <10%。
- noop/crashed 轮占比下降；每轮 meta 成本应下降（验证上限砍掉重复验证）。
