# DR-C — Red-Flag Verification + Method Templates (Deep Read)

- **Date:** 2026-07-30
- **Mode:** S5 正文+附录级 (full-text, appendix-searched). `research-guardrails (enforce)` bound; Domain C (anti-hallucination) + E (report n/cost) active.
- **Sources:** `arxiv.org/abs/<ID>`, `arxiv.org/html/<ID>`, `ar5iv.labs.arxiv.org/html/<ID>`. WebFetch/WebSearch only (per hard constraint).
- **Anti-confabulation protocol used (C4):** Every load-bearing quote/verdict was checked against a **second independent rendering** (ar5iv vs arxiv/html) OR an **arithmetic self-consistency test** on reported decimals. Where a datum rests on a single small-model pass, it is flagged `⚠ single-pass — eyeball PDF before precise quote`. Prior project history (a confabulated "7.7 decomp 工单" quote) motivates this discipline.

---

## 1. arXiv 2605.25233 — **Meta-Agent** (HIGH red flag) — FINAL: **CLEAR**

### 设置卡
- **Title:** *Meta-Agent: From Task Descriptions to Verified Multi-Agent Systems.* Authors: Andy Xu, Yu-Wing Tai.
- **What it is:** A **two-phase, inference-time** framework. **Construction phase:** task planner → DAG of agent specs (explicit I/O contracts + verification criteria) → web-search grounding module → code-gen module (system prompts + tool configs) → construction-time verification with **targeted regeneration**. **Execution phase:** coordinator dispatches subtasks; execution-time verification gates intermediate outputs; **three-level error attribution** (local / upstream / structural).
- **Benchmarks (verified, 2 independent renderings agree):** 6 total — **DROP, HumanEval, HotpotQA, MBPP, GSM8K, MATH**. Backbone: **GPT-4o-mini** (main), **Claude Sonnet 4.6** (substitution robustness run).
- **Baseline-doc assertion under test** (`DECOMP-METHOD-CATALOG.md` L201): *"workers generated fresh per task (no persistent evolving pool); GAIA number not shown."* → **CONFIRMED at full-text level.**

### 引句 (verbatim, cross-verified across arxiv/html + ar5iv)
- **No cross-task reuse** — §3.1 (construction phase): *"The DAG structure, agent specifications, and tool configurations are generated per task; no templates are reused across benchmarks."* (This exact sentence appears in **both** renderings → not a confabulation.)
- **Keyword sweep** for `reuse/reused/reusable/cache/pool/library/persist/persistent/archive/replay/across tasks` → **ABSENT** from full text (only the "per task / across benchmarks" negation above).
- **Targeted regeneration is intra-task** — §3.2: *"a grounding failure triggers a re-execution of the knowledge retrieval stage, while a contract violation triggers a revision of the task decomposition"* — i.e. it repairs the **current** system-under-construction, "without recomputing the entire pipeline." No regeneration state persists across tasks.
- **Exec-time gate mechanism** — §3.2: *"Before y_i is passed to downstream agents, a verifier checks whether y_i ∈ C_i ... If the output fails verification, it is not propagated further, and the system initiates a recovery process."*
- **Three-level recovery** — §3.2: Local = *"retries the same agent with additional feedback from the verifier"*; Upstream = *"identifies the responsible upstream agent and re-executes it before retrying a_i"*; Structural = *"escalates to the construction phase and reconstructs the affected part of the agent graph."*
- **GAIA / web-search benchmark** — `GAIA` string **ABSENT — searched full text (both renderings).** Web search is an **internal grounding tool** in the construction phase (§3.3 / Appendix A.1), **not** an evaluated task; no web-search benchmark number anywhere.

### Q1 — persistence / reuse (the four-conjunct test)
Our **N1** = "**演化持久池**" = a pool/library of specs-or-variants, **(a) pooled**, **(b) persisted across tasks**, **(c) evolved by selection over time**, **(d) reused**. Meta-Agent satisfies **NONE** of (a)–(d): every MAS is compiled fresh from the NL task description; specs are explicitly *not* templated/reused; no archive/library exists; construction repair is within-task.

### Q2 — exec-time gating vs our seesaw (APPLY / FORK / REJECT)
| Axis | Meta-Agent exec-time gate | Our seesaw gate |
|---|---|---|
| **Object gated** | a single run's intermediate output `y_i` vs contract `C_i` | a **candidate harness variant** vs a **persistent variant pool** |
| **Decision space** | {propagate ∣ recover-by-typed-attribution} → retry / re-execute-upstream / reconstruct-subgraph | **{APPLY, FORK, REJECT}** = admit / branch-for-diversity / discard into an archive |
| **Diversity** | none (correctness-only; error is repaired in place) | **FORK preserves niches/islands** (diversity retention) |
| **Temporal scope** | **within a single task execution** | **across tasks/episodes** (population evolves) |
| **Purpose** | intermediate-output correctness + localized recovery | **selection + archiving** of an evolving population |

**Substantive difference:** Meta-Agent's gate is an **intra-run correctness verifier with error-localized recovery**; the seesaw is a **cross-task selection-and-archiving operator over a persistent, diversity-preserving population.** Different object, decision semantics, and temporal scope.

### 三态判定 → **CLEAR** (for N1 = evolving persistent pool)
- **Why not OCCUPIED/PARTIAL:** zero persistence, zero pool, zero cross-task evolution. The verdict rests on a **doubly-verified** verbatim negation ("no templates are reused across benchmarks") plus a keyword-sweep miss across two renderings.
- **Honest caveat (do NOT over-claim):** Meta-Agent **does** share the *generic* motif of "construction-time regeneration + execution-time verification gating." So our differentiation must be carried on the **persistence / pool / cross-task-evolution** axis — **NOT** on "we have a gate" (gate-existence is crowded; Meta-Agent is one occupant). Frame N1's novelty as the *persistent evolving pool with fork-diversity*, never as "verified spec construction."

---

## 2. arXiv 2512.08296 — **Towards a Science of Scaling Agent Systems**

### 设置卡
- **Authors:** Yubin Kim, Ken Gu, Chanwoo Park, ... Xin Liu (MIT/Google Health et al.; 20 authors).
- **Design:** **N=260 configurations**, **6 benchmarks × 5 architectures × 9 models (3 families)**, matched compute, standardized tools/prompts. Predictive scaling model: cross-validated **R²=0.373** (0.413 with a task-grounded capability metric); identifies best architecture for **87% of held-out configs**.
- **Six benchmarks (Table 1, verified):** BrowseComp-Plus, **Finance-Agent**, **PlanCraft**, WorkBench, SWE-bench Verified, Terminal-Bench.
- **Five architectures (verified):** Single-Agent System (SAS) + four MAS — **Independent, Centralized, Decentralized, Hybrid.**
- **Model roster (verified, resolves the "3 families" ambiguity = 9 models):** OpenAI {GPT-5-nano, GPT-5-mini, GPT-5}; Google {Gemini-2.0 Flash, Gemini-2.5 Flash, Gemini-2.5 Pro}; Anthropic {Claude Sonnet 3.7, Sonnet 4, Sonnet 4.5}. (Sonnet 3.7 deprecated Feb 2026 → absent for SWE-bench Verified + Terminal-Bench, hence those use 8 models.)
- **n (verified):** BrowseComp-Plus / Finance-Agent / PlanCraft / WorkBench = **50–100 instances per config**; SWE-bench Verified / Terminal-Bench = **20-instance subsets**. Config decomposition: (45×4)+(40×2)=260. ✓ matches abstract.

### Task categories: decomposable vs sequential
- **No formal definition** — the paper **operationalizes descriptively** via observed execution structure, not a taxonomy. Report this honestly; do **not** cite a crisp definitional criterion.
  - *Decomposable* = tasks that "naturally decompose into parallel information streams" → **Finance-Agent** (+80.8%), WorkBench (+5.6% ⚠ single-pass).
  - *Sequential* = "coordination complexity exceeds task complexity" / sequential dependency → **PlanCraft** (−70.0%).
- Load-bearing mechanism sentence (§4.2): *"coordination overhead becomes counterproductive when coordination complexity exceeds task complexity (PlanCraft), but provides substantial gains when tasks naturally decompose into parallel information streams (Finance Agent)."*

### +80.8% / −70.0% protocol (ALL arithmetic-verified → genuine)
- **+80.8%** = **Finance-Agent**, **Centralized MAS vs SAS**: *"Centralized reaches +80.8% (mean 0.631 vs. SAS 0.349)."* Check: (0.631−0.349)/0.349 = **+80.8%** ✓
- **−70.0%** = **PlanCraft**, **Independent MAS vs SAS**: *"Independent to −70.0% (0.170)"*, SAS baseline **0.568** (from *"Centralized declines to −50.3% (0.282 vs. SAS 0.568)"*). Check: (0.170−0.568)/0.568 = **−70.0%** ✓; and (0.282−0.568)/0.568 = **−50.3%** ✓.
- **Aggregation:** means across the 9-model roster; n = 50–100 instances/config. "Relative performance change vs single-agent baseline."

### 引用白名单 / 黑名单 (updates the 7/21 "承重句未验勿引" hold)
- ✅ **WHITELIST — safe to cite now** (abstract-authoritative + arithmetic-verified):
  1. Central claim: *"architecture-task alignment, not number of agents, determines collaborative success"* and *"Agent effectiveness depends on alignment between coordination and task structure, and that mismatched coordination degrades the performance."*
  2. Headline range **+80.8% (Finance-Agent, Centralized) to −70.0% (PlanCraft, Independent)** — cite WITH benchmark + architecture named (never the bare range).
  3. Setup facts: 260 configs / 6 benchmarks / 5 architectures / 3 families (9 models); R²=0.373 (0.413 task-grounded); best-arch for 87% held-out.
  4. Three patterns: coordination diminishing returns above a single-agent threshold; tool-heavy tasks incur MAS overhead; no-centralized-verification propagates errors more.
- ⚠ **CITE-WITH-EYEBALL (single small-model pass; verify exact table cell before printing a precise decimal in the thesis):** raw per-cell decimals 0.631 / 0.349 / 0.170 / 0.568 / 0.282; WorkBench "+5.6%"; the "50–100 instances / 20-subset" n. (The +80.8/−70.0/−50.3 *percentages* are self-verified; only the underlying decimals want a final glance.)
- ⛔ **STILL DO NOT ASSERT:** any *formal definition* of "decomposable" vs "sequential" — the paper gives none; asserting one would fabricate a taxonomy (C1 violation).

### 转述裁决: 我方用法 "任务结构对齐才是杠杆" → **CONFIRMED**
- The paper literally says alignment "**determines** collaborative success" and that it is the driver "**not number of agents**" — this directly supports "task-structure alignment is the lever."
- **Caveat to carry (E-discipline):** it is a **predictive/correlational scaling study with modest fit (R²=0.373)**, not a causal proof that alignment is the *sole* lever. Cite as "same-period external support for structure-alignment being the primary determinant," not as proof of mechanism.

### 三态判定 (occupancy of our direction) → **CLEAR / supportive**
Does not occupy our decomp-division method; it is a **landscape/motivation citation** that *supports* the "structure is the lever" premise. No seesaw, no evolving pool, no component-attribution-of-a-harness. Use as motivation + external anchor, not competitor.

---

## 3. arXiv 2607.17044 — **Leni** (component-attribution rigor template)

### 设置卡
- **Title:** *Where Does Agent Reliability Come From? ...* Authors: Arunabh Dastidar & the Leni Team (submitted 2026-07-19). Production GAIA agent.
- **Headline finding (supports our thesis):** reliability comes mostly from **structure (planner–executor + routing) + specialist models**, **not** from the verification step, whose isolated contribution is small. Table 5 quantifies this.
- **Benchmarks:** **SpreadsheetBench** (fully instrumented), **GAIA validation** (headline), **BullshitBench/DRACO** (firewall sensitivity + valid-premise).

### 组件归因方法 (THE Ch5 rigor template — copy this)
1. **Cumulative / sequential addition, NOT leave-one-out** — §7 "Decomposing the Uplift", **Table 5**: `Base → + Structure → + Verification loop`, each increment reported in **pp**.
   - **SpreadsheetBench (Table 5):** 80.25% → **89.75%** (+prompt+scaffold, +9.5pp) → **91.25%** (+verification loop, **+1.5pp**). Total uplift +11.0pp; structure = +9.5, verification = +1.5.
   - **GAIA (Table 5):** ~60% → ~70% (+planner–executor) → ~74% (+routing) → **75.2%** (+loop, ~+1pp). ⚠ **labelled "indicative only."**
   - **BullshitBench (Opus):** 87% → 97% (**+10pp**, whole firewall).
2. **Task-level confusion matrix** for the verification component — §6.2, **Table 4**, on **SpreadsheetBench** (deterministic ground-truth): *"catch rate c=8/40=0.20, fix rate r=6/8=0.75, false-alarm rate f=0/357=0"* over 397 loop-triggering tasks (40 truly-erroneous + 357 truly-correct; 8 caught, 6 repaired, 0 false alarms). **This is the copy-target: define catch/fix/false-alarm on an instrumented benchmark.**
3. **Valid-premise / over-rejection control** — §6.4, **DRACO** = *"an internal rubric-graded corpus of 100 legitimate, professionally difficult, jargon-dense questions"* → **0 refusals** → bounds firewall false-positive rate at **≤3.6%**. This is the "does the gate wrongly reject good outputs" control. (Separate set — NOT SpreadsheetBench/GAIA.)
4. **Honest instrumentation caveat (the key rigor lesson)** — §7 / Table 5 footnote: *"The GAIA structure tiers are internal estimates whose selection rules were not recorded, so the GAIA loop-isolated figure is indicative only"*; *"GAIA layer figures are from internal ablation runs (complete controlled ablation forthcoming)."* → **They report a clean confusion matrix ONLY where deterministic ground-truth exists (SpreadsheetBench); on GAIA they explicitly downgrade to "indicative."** Copy this separation — do not claim clean attribution on a benchmark lacking error ground-truth.
5. **pass@1 AND best-of-k reported separately** (anti-inflation): **75.2% pass@1** vs **83.0% best-of-all-runs** — never let best-of-k stand as the headline.

### "+1.5 isolated" attribution method
Computed as the **final increment of the cumulative chain** on **SpreadsheetBench** (91.25 − 89.75 = **+1.5pp**), mechanistically = **6 rescued tasks**, and **cross-validated by Table 4** (the loop flags only true errors, 0 false alarms). It is **not** a leave-one-out. NB: the clean +1.5 is **SpreadsheetBench**; the GAIA loop figure (~+1pp) is "indicative only."

### 75.2 / 83.0 协议 (Q: n=165 = which subset? text-only?)
- **n=165 = the FULL GAIA validation split**, all three levels: **L1:53 + L2:86 + L3:26 = 165** (not a subset). Matches the canonical GAIA validation set.
- **Modality: text-only** tool-using agent (no multimodal).
- **Models:** planner = **Claude Opus 4.6**; executors **routed across Opus 4.6 / Sonnet 4.6 / Haiku 4.5**.
- **75.2%** = pass@1 (*"last completed attempt per task within final-configuration campaigns"*). **83.0%** = *"Best-of-all-runs across campaigns ... (137/165), a pass@k quantity."* Scoring: *"exact match on final answer, no partial credit."*

### specialist post-training 成本 (for our training-free contrast)
- **Specialists ARE post-trained** (NOT training-free) — §3.4: *"distillation SFT from a Claude Opus 4.6 teacher ... followed by task-specific reinforcement (RLVR/GRPO where a deterministic checker exists, for Cell-S, Parse-S, and Route-XS; SFT-then-DPO on preference pairs for Triage-S), 4-bit quantization for serving."*
- **Sizes:** ~4B (Cell-S, Triage-S), ~1.5B (Parse-S), ~0.5B (Route-XS).
- **Training cost:** **NONE disclosed** — no GPU-hours, token counts, or dollars. Only **serving** multipliers reported (§8): 0.1× (Cell-S/Triage-S), 0.05× (Parse-S), 0.02× (Route-XS) of frontier cost.
- **Our contrast (defensible):** "Leni buys part of its reliability with **post-trained 0.5–4B specialist models** (distillation SFT + RL); our approach is **training-free**. Leni discloses only serving-cost multipliers, not training cost — so a like-for-like cost comparison is not possible from the paper."

### 三态判定 (occupancy) → **PARTIAL (precedent, not blocker)**
- **OCCUPIES / precedes:** the **component-attribution rigor slot** — "decompose the uplift into components on a production GAIA agent (planner+specialist routing)." It is a **same-period first-mover** → **must be cited** as the method precedent in related-work + Ch5, and its rigor paradigm (cumulative addition + confusion matrix + valid-premise + pass@1/best-of-k split + honest "indicative" caveat) is what we copy.
- **CLEAR of our core:** it has **no seesaw evolving pool**, **no training-free constraint** (it post-trains specialists), and its clean attribution rests on **deterministic ground-truth (SpreadsheetBench)** — our contribution can be the **oracle-controlled / E0-gated** attribution of **decomposition×division** on a harness variant population, which Leni does not do.
- **Design implication for us:** to obtain Leni-grade *clean* component attribution (confusion matrix), we need an **instrumented benchmark with deterministic error detection**; on benchmarks lacking it we must, like Leni, label results "indicative" rather than claim clean isolation.

---

## Verification ledger (what is solid vs what wants a final eyeball)
- **Solid (≥2 independent renderings OR arithmetic self-consistency):** Meta-Agent = no reuse / no GAIA / 6-benchmark list; Scaling-Science +80.8/−70.0/−50.3 percentages + model roster + config decomposition; Leni GAIA n=165 split, Table 5 chain, Table 4 confusion matrix, DRACO, specialist post-training + no training-cost.
- **⚠ Single-pass — eyeball PDF before quoting a precise decimal:** Scaling-Science raw cell decimals (0.631/0.349/0.170/0.568/0.282), WorkBench +5.6%, exact per-config n; Leni exact percentages of the GAIA tier chain (~60/~70/~74 are approximate as printed).
- **Section-anchor caveat:** Meta-Agent §-labels for the "no reuse" sentence differed between renderings (§3.1 vs §4.1); the *sentence text* is stable, the *section number* should be eyeballed if cited with a §.
