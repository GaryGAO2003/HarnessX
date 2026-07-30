# DR-E — N2 Credit-Assignment Evidence Base (PDF-verbatim deep-read)

> Compiled 2026-07-30 by researcher (Opus 4.8). Method: 4 arXiv PDFs → scratchpad via `curl`; text extracted with `pypdf 6.14.2` (per-page) under `PYTHONUTF8=1`; grep (`grep -a`, hyphen-artifact tolerant) + targeted page reads. Every load-bearing claim carries a **verbatim quote + page number** from the extracted text. Depth = **S5** (verbatim in hand) for all four.
> Scope: supply the primary-source base for **N2** (`NOVELTY-TIERS.md` N2 row) and **C.3** (`NOVELTY-EXPDESIGN-RESEARCH.md` §C.3) — the zero-rollout observational (variant×type) credit ledger + leave-one-slot-out (LOO) calibration.
> Guardrails (enforce): verbatim-only quotes; superlatives ("cheapest in literature") flagged as inductive over-claims; each paper's own self-limiting language (TreeMem "marginal overhead"; 09863 ground-truth dependence) reported and inherited as caveats, not silently dropped; transfer-to-GAIA-subtask marked as *our inference* wherever the source does not test it.

---

## 0. N2 FINAL VERDICT (top of file)

**N2's external-evidence spine is REAL and largely verbatim-defensible — but three inherited phrasings must be corrected before they enter the pitch.**

| N2 claim (as written in tiers/C.3) | Verdict | One-line reason |
|---|---|---|
| "外证齐: 2605.27621 LOO≈组合法" | ✅ **CONFIRMED verbatim** | Abstract p.1 + Table 1 p.5 (3.3×–7.6× token savings). |
| "躲开不可靠子任务判官 (2606.09863 AUROC≤0.65)" | ✅ **CONFIRMED verbatim**, ⚠ scope | Table 5 p.8: no config exceeds 0.65. BUT bench = final-state verification *with programmatic ground truth*; GAIA-subtask transfer is **our a-fortiori inference**, not tested. |
| "27621: introspective judge 失真" (弃用子任务判官第二证) | ✅ **CONFIRMED verbatim**, on-point | Table 2 p.6: best judge R²=0.42/ρ=0.63; **negative R²** in hierarchical topologies. This is the *closer* analog (judge simulating ablation). |
| "昂贵 MC 树 (TreeMem) … 大量额外 rollout … 我方零 rollout 更省" | 🟠 **PARTIAL — REFRAME** | TreeMem branches are extra rollouts **at training only**; paper self-claims "**only marginal per-step overhead**" + "**no extra rollout branches**" at inference (p.8–9). Honest contrast = **regime** (RL-training+reward-model+branch-sampling vs. training-free inference-time ledger), NOT raw token count. |
| "文献最便宜" (cheapest in literature) | ❌ **DROP superlative** | Guardrail A: unfalsifiable/black-swan-vulnerable. Replace with relative "avoids both the rollout-heavy MC-tree regime and the unreliable-judge regime." |
| GPA 2510.08847 "明言 reference-free plan eval 是 critical 开放缺口" (C.3/Q1b line 84) | ❌ **CORRECT framing** | GPA calls it a "critical gap" **but positions itself as the solution** (a reference-free LLM-judge suite). It is a **competitor**, not an open-gap anchor. Citable for *motivation*, not for "nobody does it." |
| "LOO is zero-rollout" (latent conflation risk) | ⚠ **KEEP DISTINCT** | LOO = **n+1 re-evaluations** (27621 p.5). Only the **observational ledger** is zero-*additional*-rollout; LOO calibration genuinely costs (N+1) replays per calibrated task. |

**Net:** N2's two load-bearing pillars (LOO≈combinatorial at a fraction of cost; LLM judges unreliable) are verbatim-solid. The "cheaper than TreeMem" pillar survives only as a *regime* argument. The GPA line in the adjacent Q1(b) needs a factual correction. N2 stays a defensible **second contribution** (not headline), exactly as the tier row states.

---

## 1. SETUP CARDS

### Card A — 2605.27621 "Agents that Matter: Optimizing Multi-Agent LLMs via Removal-Based Attribution"
Mingyu Lu, Yushan Huang, Chris Lin, Su-In Lee (Paul G. Allen School, UW). 21 pp. `arXiv:2605.27621v1 [cs.MA] 26 May 2026`. **Depth S5.**

- **Bench / arch (p.4, §4):** three benchmarks — **PlanCraft** (Minecraft planning/tool-use), **WorkBench** (business-workflow planning/tool-selection), **BrowseComp-Plus** (deep-research retrieval/reasoning). "we sample 50 instances and evaluate each MAS configuration over three independent runs" (p.4). 5 role-specialized subagents each for PlanCraft/WorkBench, 4 for BrowseComp-Plus (p.4). Models: Gemini-2.5-Flash, Claude-3.5-Haiku, GPT-5-mini; replacement substitute = "Qwen3.5-122B-A10B" (p.5, §4.4). **Four topologies:** Independent / Centralized / Decentralized / Hybrid (p.4). **Not GAIA.**
- **Mechanism (p.4, §3.3):** attribution = cooperative game parameterized by "the removal protocol b, the attribution distribution wᵢ(S), and the behavior metric G" (p.5). **Three removal protocols:**
  - *Agent ablation* — "uses a null protocol bᵢ=∅, excising agents from the system … ablating indispensable roles such as routers or orchestrators can result in system failures. A common workaround is to assign zero utility to non-executable coalitions" (p.4).
  - *Introspective removal* (Cui et al. 2025) — "Rather than deleting aᵢ, the remaining agents or a judge are prompted to reason as if aᵢ were absent, yielding a simulated null protocol bᵢ=∅sim … the resulting attribution depends on the fidelity of the simulated counterfactual and may inherit biases from the judge or prompts used" (p.4).
  - *Model replacement* (their new one) — "**a topology-preserving protocol that substitutes an agent's backbone model while keeping its role instantiated.** Formally, for agent aᵢ, we define a replacement protocol bᵢ=ãᵢ" (p.4). **← this is the exact shape of our subtask-slot variant-swap.**
  - *Coalition distributions:* LOO, Shapley, Owen, Myerson (p.5, §5.1). **What Δ is measured:** "the Area Under the Curve (AUC) for bottom-ranked deletions … measuring how performance changes as agents are sequentially removed according to their attribution rank" (p.5).
- **"Fraction of cost" — exact口径:**
  - Abstract (p.1): "Leave-One-Out (LOO) identifies bottleneck agents **as effectively as combinatorial methods, but at a fraction of the computational cost**."
  - Intro (p.1): "LOO effectively identifies key agents while **reducing computational costs by 3.2× to 7.5×** compared to methods that require combinatorial coalitions."
  - Results (p.5, Table 1 caption + text): "The savings are substantial, **reducing token usage by roughly 3.3×–7.6× across datasets and removal protocols.**" LOO = **n+1** coalition evaluations vs Shapley/Owen/Myerson = **2ⁿ−190**. Concrete (model replacement / PlanCraft): "LOO uses **278.3M tokens ($53.48)**, compared with **1.62B tokens ($259.96)** for the Shapley/Owen … and **1.43B tokens ($231.19)** for the Myerson"; BrowseComp-Plus (Table 5): "LOO uses **1.63B tokens ($808.72)**, compared with **7.57B ($3,624.61)** … and **6.61B ($3,226.30)**."
  - ⚠ *Minor internal inconsistency:* intro says 3.2×–7.5×, results say 3.3×–7.6×. Cite the **results figure (3.3×–7.6×, Table 1)** as authoritative.
  - Model-replacement *intervention* payoff (abstract p.1): "improve task performance by **up to 17%** while reducing cost by **up to 35%** across three benchmarks."
- **"Introspective LLM judges fail to faithfully approximate" — the experiment (p.6, Table 2):** judge models = **DeepSeek-V4-Flash, Claude-3.5-Haiku, GPT-5-mini**; metric = **R² / Spearman ρ** of introspective coalition values **vs. agent-ablation** values, per topology, avg over 3 runs, on PlanCraft.
  - "the best judge, e.g., GPT-5-mini, reaches only moderate agreement (**R²=0.42, ρ=0.63**)" in the independent setting (p.6).
  - "In centralized and hybrid settings, **all judges obtain negative R²**, indicating poor value calibration even when rank correlations remain moderate to high. For example, in the hybrid setting, Claude-3.5-Haiku reaches ρ=0.66 but has **R²=−4.21**" (p.6). (Table 2 cells: GPT-5-mini centralized **−9.63**, hybrid **−9.94**.)
  - "On BrowseComp-Plus, where agent conversations are denser and more reasoning-heavy, judges perform even worse, with **correlations close to zero** (Table 4)" (p.6).
  - Conclusion (p.6): "**introspective removal is not a faithful approximation of agent ablation, and its fidelity depends on topology and judge model.**" Abstract form (p.1): "**Agent ablation isolates structural bottlenecks, whereas introspective LLM judges fail to faithfully approximate this behavior.**"
- **★ Load-bearing nuance for transfer (p.6–7):** "**Removal protocols are not interchangeable: They answer different attribution questions**" (p.6, Key Takeaway). "Agent ablation captures both an agent's capability and structural importance, whereas **model replacement captures the marginal benefit of its backbone model over a substitute model in the same role**" (p.7).
- **Transfer to "subtask-slot swap variants" (our LOO calibration shape): SUPPORTS.**
  - Our "route a subtype to a different variant, keep the slot" = **their model-replacement protocol** (topology-preserving substitution, role kept). Our name "leave-one-slot-out" = **LOO coalition distribution**. The paper *built and validated* both (model replacement drives their 17%/35% intervention). Nothing in the paper argues against it.
  - **Caveat 1 (cost):** LOO ≠ zero. It is **n+1 full re-evaluations**. Our "zero additional rollout" belongs to the *observational ledger*; the LOO subset genuinely costs (N+1) replays per calibrated task. Do not conflate.
  - **Caveat 2 (semantics):** per the "different questions" finding, slot-swap credit = *marginal benefit of variant-X over the substitute in that slot* — **relative, not absolute** importance. Fine for routing (we want per-slot variant ranking); must be labeled so.
  - **Caveat 3 (bench):** PlanCraft/WorkBench/BrowseComp-Plus, 50 inst × 3 runs — external validity to GAIA is analogical, not direct.

### Card B — 2606.09863 "From Confident Closing to Silent Failure: Characterizing False Success in LLM Agents"
Laksh Advani (University of Colorado). 18 pp. **Accepted to Workshop: Failure Modes in Agentic AI (FAGEN) at ICML 2026** (p.1 footnote). `arXiv:2606.09863v1 [cs.LG] 1 Jun 2026`. **Depth S5.** *(Single-author workshop paper — weigh accordingly.)*

- **Corpus (p.1; Table 1 p.3; Table 2 p.4):** "**9,876 tau2-bench trajectories from 8 model families** and **1,879 AppWorld trajectories from 4 model families** with text-independent ground truth" (p.1). AppWorld self-assessing subset = "1,425 false success and 454 honest failure" (p.4).
- **False-success definition (p.1):** "a mismatch between the agent's natural-language claim of completion and the programmatic environment state." Frequency: "**44–52% of all failures in single-control domains and only 3% in a dual-control domain**" (tau2, p.1); "**75.8% of failures among architectures that produce explicit completion signals**" (AppWorld, p.1). "Per-model false-success rates span **13% to 89%**"; "Qwen3-Max-Thinking exhibits the highest false-success rate (**79%**)" (p.1).
- **5 judges × 5 prompts — protocol + the 0.65 ceiling (p.8, Table 5):** caption — "LLM judge Frame A (FS vs TS) AUROC on tau2-bench, **across 5 judge models and 5 prompt conditions plus strong-real. No configuration exceeds 0.65.**" Judges = **GPT-4o, Sonnet 4.5, Llama 3.3-70B, DeepSeek-R1, o3-mini**. Prompt columns = **blind / no-closing / checklist / tool / step + strong(-real)**. Body (p.8): "Across **5 judge models, 5 prompt conditions, 3 prompt phrasings, and a strong baseline that provides the full ground-truth task specification** … no configuration exceeds AUROC 0.65 on FS vs TS detection (Table 5). The strongest single cell is **Sonnet 4.5 no closing at 0.640**." "Providing the full task specification (strong-real) changes Sonnet's AUROC from 0.640 to 0.632; **judges do not lack information, they fail to use it.**"
- **AppWorld 0.54 (p.8, Figure 4 p.9, conclusion p.11):** "GPT-4o peaks at AUROC **0.537** under the checklist condition." Conclusion (p.11): "AUROC 0.65 on tau2-bench; the same judge family reaches **only 0.54 AUROC on AppWorld API-call traces**." Mechanism — Frame B (FS vs HF) tau2: "Across all 25 judge × condition cells, AUROC ranges from **0.18 to 0.30** … judges are systematically **anti-correlated with truth**" (p.8).
- **TF-IDF detector 0.83 / 0.95 (p.6–7, Table 4):** abstract (p.1) — "Lightweight TF-IDF detectors achieve **task-disjoint AUROC 0.83 on tau2-bench and 0.95 on AppWorld**, recovering **4–8× more false successes than the best judge at the same flag rate with 3,300× lower latency.**" Protocol (p.5): "TF-IDF + Logistic Regression" (bigram), "TF-IDF + XGBoost", plus DeBERTa "within 0.03 AUROC" (p.1); "AUROC as the primary metric, threshold-tuned"; **task-disjoint** split. tau2 0.83 via "closing-message vocabulary features"; AppWorld 0.95 via "API call sequence features" (p.2).
- **Scope / GAIA boundary (p.2, related work) — the paper's OWN scope statement:** "**AgentBench (Liu et al., 2024) and GAIA (Mialon et al., 2023) assess reasoning and tool use across diverse domains.** Tau-bench … focus on customer-service scenarios with programmatic reward signals derived from database state. **We use tau2-bench as our data source because it provides ground-truth completion labels independent of agent language, which is essential for detecting false success.**" (p.2)
  - **⇒ Boundary for us:** the entire method *requires* programmatic ground truth to *define* false success. GAIA subtasks have no such ground truth. So AUROC≤0.65 is proven for **final-state verification judges**, not for **subtask credit judges**. Extrapolating to "GAIA subtask judges are unreliable" is an **a-fortiori inference we make**, not a result the paper reports. Defensible as motivation ("if judges can't verify final state *with the full task spec*, finer subtask credit without any ground truth is even less justified") — must be *labeled as our inference*.
  - Generalization the paper *does* test: LOMO (leave-one-model-out) AppWorld **0.856**; LODO (leave-one-domain-out) tau2 drops to **0.69**; cross-temporal (train tau-v1 → test tau2) **0.73** (p.7–8). "LODO does not apply to AppWorld (single domain)" (p.7).
- **Use for N2:** ✅ back "don't use subtask LLM judges" + "cheap deterministic detector beats judge" (the TF-IDF 3,300× story parallels our deterministic end-task gate). Boundary-tag the GAIA extrapolation.

### Card C — 2605.04811 "Tree-based Credit Assignment for Multi-Agent Memory System" (TreeMem)
Marina Mao, Alexandr Liu, Pengbo Li, Siheng Li, Bo Zhou*, Xiang Wang* (USTC / Tencent LLM Dept / HKUST / CUHK). 17 pp. Preprint. `arXiv:2605.04811v1 [cs.MA] 6 May 2026`. **Depth S5.**

- **What it is (p.1):** an **RL (GRPO) training** method. "derives agent-specific credit from the final reward **without task-specific annotations**"; "extends the multi-agent pipeline (builder–summarizer–retrieval) into a tree structure, where each agent's outputs are expanded into multiple subsequent branches. **The contribution of each agent is estimated via Monte Carlo averaging over its subsequent branches.**" "These signals are then used to **update all agent policies simultaneously.**" Uses a reward model V; runs on **8× A100 GPUs** (p.14).
- **Rollout-cost口径 — the branching (p.4, §3.2):** "the memory builder samples **G** candidate actions; for each builder action, the summarizer samples **J** branches; and for each builder–summarizer pair, the retrieval-based responder samples **K** final responses." ⇒ per training example, leaf rollouts = **G × J × K**.
  - Ranges/defaults (p.8 §4.5; p.14 App B): "we vary the Builder group size **G from 2 to 32**, the Summarizer branch count **J from 1 to 4**, and the Retrieval branch count **K from 1 to 4**." App-B defaults: "batch size is 128, the **group size is 8**, … the number of training epochs is 5, and the maximum number of output tokens is 3k."
- **★ TreeMem's OWN efficiency claim — the honesty caveat (p.8–9, §4.6/RQ5):** "At training time, TreeMem's tree-structured rollout **introduces additional computation due to its branching factors, which we mitigate via sampling rollouts** … resulting in **only marginal per-step overhead relative to the '+Rfinal' and '+Rtask' variants.**" "At inference, TreeMem adopts the same memory architecture and agent backbone as baseline methods … **while requiring no extra rollout branches.**" "**TreeMem introduces negligible extra computational burden during both training and inference.**"
- **Bench / granularity (p.8, §4.1):** **PersonaMem** (main, "over 180 long conversational" episodes), **LongMemEval**, **LOCOMO**. Backbones Qwen2.5-3B/7B, Llama-3.2-3B / 3.1-8B. Credit granularity = per memory-agent **role** (Builder / Summarizer / Retrieval). **Not decomposition, not GAIA, not harness-config.**
- **How to write our "zero-rollout more省" sentence so it stands verbatim:** frame as **regime**, not token count.
  - ✅ Defensible: "TreeMem (2605.04811) derives annotation-free agent credit, but via **Monte-Carlo averaging over sampled branch rollouts (builder group G, summarizer/responder branches J, K) inside an RL/GRPO training loop with a reward model**; our observational (variant×type) ledger requires **no training, no reward model, and no branch rollouts** — credit is read from the deterministic end-task gate already computed."
  - ❌ Do NOT write "TreeMem needs a large amount of extra rollout / is expensive per step" — the paper explicitly self-claims *marginal* per-step overhead and *no* extra rollout at inference. That sentence is refutable by its own §4.6.

### Card D — 2510.08847 "What is Your Agent's GPA? A Framework for Evaluating Agent Goal-Plan-Action Alignment"
Allison Sihan Jia, Daniel Huang, Nikhil Vytla, Seung Won Wilson Yoo, Nirvika Choudhury, Shayak Sen, John C. Mitchell (Stanford), Anupam Datta (Snowflake / Stanford). 54 pp. `arXiv:2510.08847` (Oct 2025; catalog notes a 2026-05-25 revision). **Depth S5** for the framing question.

- **What GPA itself is — reference-FREE LLM-judge framework (p.1):** "We introduce the **Agent GPA (Goal-Plan-Action) framework** … We operationalize the framework with a **factorized suite of LLM judges** designed to measure distinct elements of Goal-Plan-Act alignment … we use state-of-the-art automated prompt optimization techniques to systematically generate domain-specific evaluation criteria." Validated on "**TRAIL/GAIA** … **TRAIL/SWE-bench** … and a private … **Snowflake Intelligence**" (p.1). It is explicitly "automated **reference-free**, LLM-as-a-Judge (LLM judge) evaluators" (p.1). Numbers: identifies "**95%** of human-annotated errors", localizes "**86%**", "highest error coverage (ranging from **76% to 86%**)", "improve judge consistency **by up to 38%** through iterative refinement of evaluation rubrics" (p.1).
- **The "critical gap" sentences (do they claim reference-free plan eval is an OPEN gap?) — verbatim, p.2–3:**
  - (p.2) "While this has motivated the creation of plan evaluations, **current methods rely on validation with a simulation verifier, human annotation, or ground-truth** [30]."
  - (p.2) "As more systems adopt explicit planning, **developing reference-free evaluations for plan quality and plan adherence will be critical** [30]."
  - (p.2→3) "In addition to accuracy, agent evaluation … must consider … cost and efficiency [15]. **This highlights a critical gap for a granular, reference-free framework that can isolate and identify operational breakdowns.**"
  - (p.2) "This underscores an important need for **reference-free methods that can evaluate goal fulfillment, even in the absence of ground-truth answers.**"
- **★ TERMINAL VERDICT on GPA: CITABLE, but the doc's current framing is WRONG and must be corrected.**
  - GPA **does** call reference-free plan/goal evaluation a "critical gap" / "will be critical" — **but every one of those sentences is the setup for GPA's own contribution.** GPA is a **competitor that claims to solve** reference-free plan-quality + plan-adherence evaluation with a factorized LLM-judge suite. It is **not** an unaddressed open problem.
  - ⇒ Citing GPA as "reference-free plan eval is an *open gap*, therefore we avoid it" is **misleading** — a reviewer who opens GPA sees GPA doing exactly that evaluation and reporting 76–95% coverage. **Correct the Q1(b) note (`NOVELTY-EXPDESIGN-RESEARCH.md` line 84).**
  - **How to cite it honestly (supports our "avoid decomposition direct-eval" strategy — as motivation, not open-gap):** "Reference-free plan-quality / plan-adherence judging is actively pursued (GPA, 2510.08847) but requires a **heavyweight factorized LLM-judge suite with automated prompt optimization**, and its judge **consistency was itself only *improved by up to 38%* via evolutionary rubric refinement** — i.e., it starts inconsistent. Combined with the judge-unreliability evidence of 2606.09863, we **deliberately sidestep** subtask-plan-quality judging in favor of a deterministic end-task gate + E0 oracle."
  - **Support-anchor value:** (a) the field agrees prior plan evals are reference-dependent (needs simulator/human/ground-truth) — motivates *why we don't* rely on those; (b) doing reference-free plan eval *well* demands enough machinery + still-fragile consistency to justify avoiding it at MSc scale. **Competitor value:** blocks any "no one evaluates plan quality reference-free" novelty phrasing.
  - **Note the asymmetry we can lean on:** GPA is reference-free *at inference* but reference-*dependent at validation* (it meta-evaluates against TRAIL/GAIA human error annotations). Our E0 oracle plays the analogous validation role; our headline routing avoids the per-subtask judge entirely.

---

## 2. CROSS-CHECK vs `NOVELTY-EXPDESIGN-RESEARCH.md` §C.3 (line-by-line, three-state)

| C.3 line | External claim | Verdict | Correction / note |
|---|---|---|---|
| **主 (observational ledger, "only report as specialization profile, not causal credit; confounded by sibling co-occurrence")** | (design choice; no direct external validator) | 🟢 **consistent, not externally validated** | 27621's "removal protocols answer different attribution questions" (p.6) *backs the caution* that observational co-occurrence ≠ causal credit. No paper validates a pure-observational ledger — it is our contribution. State as such (honest). |
| **校准 (leave-one-slot-out counterfactual) — cites 2605.27621 (LOO≈组合法、省算力) + 2603.06859** | LOO matches combinatorial at fraction of cost | 🟢 **SUPPORTED (verbatim)** | 27621 abstract p.1 + Table 1 p.5. Add caveat: LOO = **n+1 re-evals**, not zero; our slot-swap = their **model-replacement** protocol (name it in related work). |
| **弃用 (subtask LLM judge) — cites 2606.09863 (AUROC≤0.65) + 2605.27621 (introspective judge 失真)** | LLM judges unreliable for this | 🟢 **STRONGLY SUPPORTED (verbatim)** | 09863 Table 5 p.8 + 27621 Table 2 p.6. Boundary: 09863 = final-state verification (ground-truth-dependent, not subtask); 27621 introspective removal = the *on-point* analog. Tag the GAIA-subtask step as our inference. |
| **不采 (TreeMem MC tree; "大量额外 rollout;我方零 rollout 更省")** | TreeMem too expensive | 🟠 **PARTIAL — REFRAME to regime** | TreeMem self-claims marginal per-step overhead + no extra rollout at inference (p.8–9). Rewrite per Card C's ✅ sentence: training-free / no-reward-model / no-branch-rollout vs. RL-training branch-MC. |

---

## 3. CROSS-CHECK vs `NOVELTY-TIERS.md` N2 row

N2 row text: *"零 rollout 观察式 (variant×type) 信用 + LOO 校准:躲开不可靠子任务判官(2606.09863 AUROC≤0.65)与昂贵 MC 树(TreeMem),文献最便宜 … 外证齐(2605.27621 LOO≈组合法)."*

- **"2606.09863 AUROC≤0.65"** → ✅ verbatim (Table 5 p.8). Keep; add "final-state verification bench" boundary tag.
- **"2605.27621 LOO≈组合法"** → ✅ verbatim (abstract p.1; 3.3×–7.6×, Table 1 p.5). Keep.
- **"昂贵 MC 树 (TreeMem)"** → 🟠 keep the *pointer* to TreeMem but change "昂贵" to "RL-training / branch-rollout regime" (Card C).
- **"文献最便宜"** → ❌ **remove.** Replace with: "avoids both the rollout-heavy MC-tree regime and the unreliable-subtask-judge regime." (Guardrail A: no unfalsifiable superlative.)
- **"半独立:B2 输赢皆可写"** → unaffected by this deep-read; still holds (a null routing result downgrades N2 to a method note, not a failure).

---

## 4. N2 FINAL WORDING — which sentences bear weight verbatim

**CAN bear weight (each fully page-anchored):**
1. "Leave-One-Out identifies bottleneck agents **as effectively as combinatorial methods, but at a fraction of the computational cost** (2605.27621, abstract p.1; **3.3×–7.6×** token reduction, Table 1 p.5)."
2. "**Introspective LLM judges fail to faithfully approximate** structural agent attribution (2605.27621, Table 2 p.6: best judge R²=0.42/ρ=0.63; **negative R²** in hierarchical topologies)."
3. "**No judge configuration across 5 models × 5 prompt strategies (plus a full-task-specification baseline) exceeds AUROC 0.65** for false-success detection (2606.09863, Table 5 p.8)."
4. "A lightweight deterministic detector reaches **0.83 / 0.95 AUROC at 3,300× lower latency** than the best judge (2606.09863, abstract p.1)." — parallels our deterministic end-task gate.
5. "TreeMem derives annotation-free agent credit via **Monte-Carlo averaging over sampled branch rollouts inside an RL/GRPO training loop with a reward model** (2605.04811, §3.2 p.4, §4.6 p.8–9)."

**CANNOT bear weight (soften/correct):**
- ❌ "cheapest in the literature" — drop superlative.
- ❌ "TreeMem needs large/expensive extra rollout" — refuted by TreeMem §4.6 ("marginal per-step overhead", "no extra rollout branches" at inference).
- ⚠ "our LOO is zero-rollout" — LOO = n+1 re-evals; only the *observational ledger* is zero-additional-rollout.
- ❌ "reference-free plan eval is an open gap (GPA)" — GPA fills it; cite as competitor-we-avoid.

**Recommended N2 second-contribution paragraph (drop-in, every clause defensible):**
> "We assign subtask-type credit **observationally from a deterministic end-task gate at zero additional rollout cost**, reserving **leave-one-slot-out** counterfactuals only for calibration. This sidesteps two regimes shown unreliable or training-bound in prior work: (i) subtask / introspective LLM judges, which **fail to faithfully approximate structural attribution** [2605.27621, Table 2] and **cannot exceed AUROC 0.65** for false-success detection even with full task specifications [2606.09863, Table 5]; and (ii) **Monte-Carlo tree credit, which requires branch-rollout sampling inside an RL training loop with a reward model** [TreeMem, 2605.04811]. Our slot-swap calibration instantiates the **removal-based (model-replacement) attribution shown to match combinatorial methods at a fraction of the cost** [2605.27621]."

**LOO-calibration protocol — how to copy from 27621 (so calibration isn't ad hoc):**
- **Slots / replays:** N subtask-type slots ⇒ per calibrated task, **N+1 replays** (full config + leave-each-slot-swapped-to-alternate-variant). Mirrors 27621's LOO (n+1 coalition evaluations, p.5). Our swap = their **model-replacement** protocol (topology-preserving, keep role).
- **Statistic to report:** **Spearman ρ (and R²)** between the observational ledger's per-(variant, type) ranking and the LOO-swap Δ-end-task ranking, on a calibration subset — *exactly* 27621's fidelity metric (Table 2). Report per 27621's cost discipline: **tokens + $ + wall-clock** for the calibration subset (they report token+$ per protocol, Table 1).
- **Sizing reference:** 27621 used 50 instances × 3 runs per bench; our calibration subset can be much smaller (it is a calibration, not a headline). Pre-register the subset size, N, replay count = (N+1)×|subset|, and the ρ/R² we will report — win-or-lose (N2 half-independence).
- **Framing to pre-empt "different questions" (27621 p.6):** state that slot-swap credit measures the **marginal benefit of a variant over its substitute in a slot** (relative variant ranking per slot), which is exactly what routing needs — *not* an absolute causal importance.

---

## 5. ERRATA / caveats to inherit (do not silently propagate old phrasings)

- **E-1 (Q1b, `NOVELTY-EXPDESIGN-RESEARCH.md` line 84):** "GPA 2510.08847(明言 reference-free plan eval 是 critical 开放缺口)" → **CORRECT:** GPA calls it a "critical gap" but **positions itself as the solution** (reference-free factorized LLM-judge suite). It is a **competitor**; cite for motivation / avoidance-justification, **not** as evidence "nobody does it."
- **E-2 (C.3 不采 + N2 row):** "TreeMem = 大量额外 rollout / 昂贵" → **REFRAME** to training-vs-training-free regime (Card C). TreeMem's §4.6 self-claims marginal per-step overhead and no extra inference rollout.
- **E-3 (N2 row):** "文献最便宜" superlative → **DROP** (guardrail A).
- **E-4 (latent):** keep **LOO (n+1 re-evals)** distinct from **observational ledger (zero additional rollout)** — never let "LOO" carry the "zero-rollout" claim.
- **E-5 (scope tag):** 2606.09863 measures **final-state verification** judges against **programmatic ground truth** (bench = tau2/AppWorld, *not* GAIA; the paper chose tau2 *because* it has ground-truth labels, p.2). Any statement that its AUROC≤0.65 implies GAIA-subtask judges are unreliable must be labeled **our a-fortiori inference**, not a reported result.
- **Provenance:** PDFs @ scratchpad `…/scratchpad/dre/{2605.27621,2606.09863,2605.04811,2510.08847}.pdf`; per-page text `.txt` alongside. pypdf 6.14.2. No `runs/`, no keys, no lab endpoint, no git touched.
