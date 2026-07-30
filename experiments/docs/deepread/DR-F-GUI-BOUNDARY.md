# DR-F — GUI Cross-Domain Precedent + Boundary Papers (PDF-verbatim deep-read)

> Compiled 2026-07-30 by researcher (Opus 4.8). Method: arXiv PDFs downloaded to scratchpad, text extracted with `pypdf 6.14.2` (per-page), grep + targeted page reads. Every load-bearing claim carries a **verbatim quote + page number** from the extracted text. Depth = **S5** (verbatim in hand) unless marked otherwise.
> Scope: verify the catalog's GUI row (§1D) + red-flag zone (§5 MED) against primary sources; deliver the **Lybic N1 occupancy verdict** and the **2605.15425 boundary-sentence defense**.
> Guardrails (enforce): efficiency-vs-accuracy axes kept separate; negative/null findings reported as such; catalog overstatements flagged as errata, not silently inherited.

---

## 0. LYBIC N1 OCCUPANCY — FINAL VERDICT (top of file)

**VERDICT: CLEAR.** Agentic Lybic (2509.11067) does **NOT** occupy our N1 (the *persistent, seesaw-gated, fork/retire-evolving executor pool*). Its "worker pool" is **three FIXED functional roles** (Operator / Technician / Analyst) assigned per-DAG-node by subtask kind — structurally identical to Magentic-One's fixed 5-role set, not an evolving pool.

Three sub-questions, answered verbatim:

| Question | Answer | Evidence |
|---|---|---|
| **Worker persistent?** | Only trivially (the 3 roles are architectural constants). No per-worker learned state accumulates or carries across tasks. | "The Worker subsystem … implementing **three specialized execution roles**" (p.8, §3.4). Backbones are off-the-shelf, chosen per component (p.11). |
| **Cross-task evolution?** | **NO.** No worker is grown, versioned, or improved across tasks. | The only "self-evolving" in the paper is **GUI-Owl [ref 39]** in *related work* — a cited training pipeline, not Lybic's workers: "More recently, GUI-Owl [39] has pushed the … self-evolving trajectory production … GUI-Owl-7B achieves … 29.4 on OSWorld" (p.4). Lybic itself runs on **o3 & UI-TARS** (p.10, Table 1). |
| **Selection / elimination?** | **NO.** Routing is deterministic by the node's assigned role type; no competitive selection or retirement among variants. | DAG NODES carry an "**assigned worker role** (Technician for system operations, Operator for GUI interactions, or Analyst for decision support)" (p.7, §3.3). |

The catalog's phrase "manager + **worker pool**" (§1D) is an **overstatement** → see Errata E-1. Lybic remains a valid **cross-domain precedent** for the *ingredient* "typed decomposition → heterogeneous executor" (cite it so a reviewer can't call the pattern novel), but it does **not** scoop the full N1 conjunction. The "candidate … selection" phrase (p.5) that a keyword scan might flag also belongs to *other* systems' inference-time scaling in related work ("sampling multiple candidate actions and using MLLM judges for selection"), not to Lybic's workers.

---

## 1. SETUP CARDS

### Card A — Agentic Lybic (2509.11067, Guo et al., ACM manuscript, 21 pp, 25-09)

- **Bench / numbers:** OSWorld (361 tasks, official held-out eval). **57.07% success @ 50 steps** = new SOTA at submission, beating CoAct-1 (56.39%) and "Agent S2.5 w/ o3" (p.11). Backbone = "**Agentic Lybic w/ o3 & UI-TARS Agentic Framework**" (p.10, Table 1) — a framework over off-the-shelf models, **not** a trained specialist.
  - Quote: "achieving a new state-of-the-art success rate of **57.07% in 50 steps**" (p.14, §Conclusion; also p.2 abstract, p.3 intro).
- **Decomposition format / type system:** Manager builds a **DAG**. "it employs a **directed acyclic graph (DAG)** representation for subtask dependencies, enabling sophisticated scheduling and parallel execution" (p.7). DAG structure: "**NODES:** Each node contains a subtask title, detailed description, and **assigned worker role** … **EDGES:** Directed connections … dependencies"; then "**topological sorting** to generate the actual execution sequence" (p.7). Adaptive replan in 3 tiers: "(1) light adjustment … (2) medium adjustment … (3) heavy adjustment for complete task re-decomposition" (p.7).
- **Executor:** 3 fixed roles (Operator=GUI VLM; Technician=terminal/Python/Bash; Analyst=reasoning/decision support), coordinated by a Central Controller state machine (6 situations: REPLAN, SUPPLEMENT, GET_ACTION, QUALITY_CHECK, FINAL_CHECK, EXECUTE_ACTION) + Evaluator quality gates (p.6–8).
- **Decomposition ablation:** **NONE isolated.** No ablation section; contributions are listed (p.3) but the 57.07% is a full-system number with no "remove-DAG-decomposition" arm. Catalog's "SOTA claim (not isolated)" = **CONFIRMED**.
- **Depth:** S5.

### Card B — Agent S2 (2504.00906, Agashe et al., Preprint, 18 pp, 25-04)

- **Bench / numbers:** OSWorld, WindowsAgentArena (WAA), AndroidWorld. **Headline = full-system vs leading baselines**, NOT an isolated decomposition ablation:
  - OSWorld Table 1 (p.6): Agent S2 w/ Claude-3.7-Sonnet = **27.0 (15-step) / 34.5 (50-step)**. Footnote: "↑ represents a relative increase with respect to **leading baselines UI-TARS and Claude Computer Use**" (p.2).
  - **+18.9%** = 27.0 vs **UI-TARS-72B-DPO 22.7** (15-step); **+32.7%** = 34.5 vs **CCU w/ Claude-3.7-Sonnet 26.0** (50-step). (Both baselines in Table 1, p.6; arithmetic checks: 27.0/22.7=1.189, 34.5/26.0=1.327.)
  - WAA: 29.8% overall, "new SOTA … outperforms … NAVI … by 52.8%" (p.7, Table 3). AndroidWorld: 54.3% (↑16.5%) (p.2).
- **Decomposition format:** Manager→**subgoal LIST**. "the Manager M generates a plan for instruction I, breaking it into coherent subgoals: **I = g0, g1, …, gN**" (p.3, §3). Worker executes atomic actions per subgoal. **Proactive Hierarchical Planning**: Manager "updates its list of remaining subgoals after the completion of each individual subgoal" (p.4) — proactive replan, not just on-failure.
- **Mixture-of-Grounding "pool" — FIXED / PREBUILT, not learned:** Worker "acts as a **gating mechanism** and routes each generated action to the correct grounding expert" (p.4). Three fixed experts (p.5): **Visual** (uses UGround / UI-TARS), **Textual** (OCR), **Structural** (UNO / Universal Network Objects interface, p.7). Experts are **swappable pretrained modules**, not evolved: Figure 6 (p.8) swaps in Claude-3.7-Sonnet 24.61% / UGround-V1-7B 24.61% / UI-TARS-7B-DPO 29.23% / UI-TARS-72B-DPO 30.77% as the visual expert. Only "self-correction" is the Worker refining the *description it sends* to an expert (p.5), not changing the expert set.
- **Ablations (live & ablated — on a 65-example OSWorld subset, p.6):** Figure 5 (p.8): **MoG** +3.08pp @15-step (30.77 vs 27.69) / +4.61pp @50-step (38.46 vs 33.85); **PHP** +4.62pp @15 / +6.15pp @50 (p.8 text). Per-expert removal (p.7-8): drop textual expert → subtask SR 70.6%→65.2%; drop structural → 73.7%→69.4%.
- **Depth:** S5.

### Card C — UFO2 (2504.14603, Zhang et al., "The Desktop AgentOS", 24 pp, 25-04)

- **Bench / numbers:** WindowsAgentArena (WAA) + OSWorld-W (Windows subset). UFO2-base w/ GPT-4o = **23.4% SR** (p.15); "27.9% SR on WAA, exceeding Operator by … 7.1%" (p.15); OSWorld-W "**28.6% SR compared to Operator's 14.3%**, effectively doubling" (p.15); with API actions up to **51.9%** (UFO2-o1, p.16).
- **HostAgent→AppAgent decomposition & routing:** "HostAgent identifies the underlying task goal and **decomposes it into a dependency-ordered subtask graph**" (p.5, §Task Decomposition); then "dynamically dispatches execution to specialized **AppAgents** —expert modules tailored for specific Windows applications" (p.2). Routing is **by application identity** (deterministic), serialized into an FSM (p.5).
- **AppAgent experts — PREBUILT-per-app + memory-refined, NOT an evolved pool:** "HostAgent **spawns** the corresponding AppAgent for each active application, providing it with task context, memory references, and relevant toolchains" and "manages the **creation and teardown** of application-specific AppAgent instances" (p.5). Improvement is experiential, not retraining and not pool-evolution: "**memory layer, enabling each AppAgent to incrementally refine its behavior without retraining**" (p.2); "improve autonomously over time without retraining" (p.3). No fork/retire/selection *among competing variants* — one expert per app, keyed by identity.
- **"Remove decomposition" ablation:** **NONE** (catalog "not stated" = CONFIRMED). Ablations are orthogonal: hybrid **control detection** (Table 3, SR & CRR, p.17) and **GUI-only vs GUI+API** actions (Table 5, p.18). Neither isolates the HostAgent decomposition.
- **Depth:** S5.

### Card D — Runtime-Structured Task Decomposition / RSTD (2605.15425, Asthana et al., IBM Research + Zoom, ACM CAIS 2026, 5 pp, 26-05)

- **What it is:** an architectural pattern where "task partitioning decisions are governed by **executable control flow** rather than static prompt text, and LLMs are invoked only for narrowly scoped judgment tasks with schema-validated outputs" (p.1).
- **Setup:** two SE workloads (**Kubernetes root-cause-analysis** + **multi-file debugging**) × **three configs** — monolithic, **static decomposition** ("same subtask graph, no runtime branching"), runtime-structured — 10 runs each (p.1).
- **The metric is RETRY COST (tokens). Accuracy is TIED at 100% across all three configs** — this is load-bearing for our boundary:
  - "All three configurations correctly identified and fixed all three bugs across all 10 runs (**100% correctness**)" (p.3); Table 1 & Table 2 both: "**Correct (all runs) 100% 100% 100%**" (p.3).
- **Key honest finding + numbers:**
  - "we find that **decomposition structure alone does not reliably reduce retry cost**" (p.1, abstract).
  - Static is **worse than monolithic**: "the **static baseline's retry cost (1,632±145 tokens) exceeds the monolithic baseline (904±17 tokens) by 80.5%**, because fixed sequential execution must rerun multiple downstream subtasks" (p.1). (Debugging: 933 vs 703 tokens, same direction smaller.)
  - Runtime-structured wins by **selective retry**: "reduces retry cost … to 436±132 (RCA) and 460 (debugging)—achieving up to a **51.7% reduction over monolithic and a 73.2% reduction over static decomposition**" (p.1).
- **Depth:** S5.

---

## 2. THREE-STATE DETERMINATION (relative to catalog §5 red-flag zone + §1D GUI row)

Two levels must be scored separately — this is the crux.

**Level 1 — our full N1 conjunction cell** = *training-free × static one-shot typed DAG × GAIA-web × routed to a self-evolved, seesaw-gated, fork/retire harness-config pool* (catalog §4).

| Paper | State vs N1 | Why |
|---|---|---|
| Agentic Lybic 2509.11067 | **CLEAR** | 3 fixed roles, no evolving pool, no selection/retire; OSWorld ≠ GAIA. |
| Agent S2 2504.00906 | **CLEAR** | 3 fixed prebuilt grounding experts (swappable, not evolved); OSWorld/WAA/Android ≠ GAIA. |
| UFO2 2504.14603 | **CLEAR** | per-app instances, memory-refined but not fork/retire/selection-evolved; WAA/OSWorld-W ≠ GAIA. |
| RSTD 2605.15425 | **CLEAR (it is a boundary/threat, not an occupier)** | makes an *efficiency* claim on *coding*; never touches accuracy-on-GAIA. See §3. |

**→ The N1 cell holds. No GUI system occupies it.**

**Level 2 — the *ingredient* "typed decomposition → heterogeneous executor routing"** (what catalog §5 MED says the GUI column proves is common & evaluated).

| Paper | State vs ingredient | Note |
|---|---|---|
| Agent S2 | **OCCUPIED-as-precedent (and ABLATED)** | strongest: typed-decomp→expert-routing is *live and measured* (MoG +3.08/+4.61pp). **Must cite** so a reviewer cannot call the pattern novel. |
| UFO2 | **OCCUPIED-as-precedent** | typed-by-app expert routing, but the split itself is **un-ablated**. |
| Agentic Lybic | **OCCUPIED-as-precedent** | typed DAG → 3-role routing, un-ablated (SOTA not isolated). |

**Net:** exactly as catalog §5 MED framed it, now verbatim-verified — cite all three as **cross-domain precedent for the ingredient**; our defensible novelty is not the pattern but (a) the **self-evolved seesaw-gated pool** and (b) the **matched-budget head-to-head on GAIA**. None of these three weakens (a) or (b).

---

## 3. BOUNDARY SENTENCE — how to write it so 2605.15425 cannot touch us

**Our sentence:** *"We claim only accuracy under matched budget on GAIA; the efficiency axis (retry cost / token overhead) is explicit backlog."*

**Why this is airtight against RSTD (strongest defense, with anchors):**

1. **Different metric — and RSTD proves it themselves.** RSTD's entire result is on **retry cost (tokens)**; its **accuracy is tied at 100% across monolithic / static / runtime-structured** ("Correct (all runs) 100% 100% 100%", p.3). So RSTD's own data shows *decomposition style makes no accuracy difference in their setting* — it can only speak to efficiency, exactly the axis we cede. It makes **zero** accuracy claim that could contradict an accuracy-on-GAIA result.

2. **Different domain.** RSTD's workloads are **K8s RCA + multi-file debugging** (agentic *coding*), not GAIA-web (p.1). Their failure mode is code-pipeline-specific: "fixed sequential execution must **rerun multiple downstream subtasks**" (p.1) — a retry-cost pathology.

3. **Matched budget absorbs their pathology.** The static-decomp penalty they find *is* extra retry tokens; holding budget matched and scoring accuracy at a fixed end-task gate is precisely the control that neutralizes a retry-cost gap. Their headline "73.2% reduction over static decomposition" (p.1) is a **token-count** delta, not a success delta.

4. **We pre-register the concession, not get caught by it.** Keep **E3-style difficulty-gating** and **D4-style runtime/evolved decomposition** as *named backlog arms* (catalog §4 already commits this), so RSTD reads as "future work we scoped out," not "baseline we lost to."

**One-line reviewer rebuttal (paste-ready):** *"2605.15425 reports retry-cost (token) reductions with correctness held at 100% across all decomposition styles; it neither measures nor claims an accuracy effect, and its workloads are agentic coding, not GAIA. Our matched-budget accuracy claim on GAIA is orthogonal, and runtime-structured/difficulty-gated decomposition is retained as an explicit efficiency backlog arm."*

---

## 4. ERRATA / REFINEMENTS TO DECOMP-METHOD-CATALOG.md (verify before load-bearing reuse)

- **E-1 (Lybic §1D):** "manager + **worker pool**" is misleading → it is a **fixed 3-role Worker subsystem** (Operator/Technician/Analyst), no persistence/evolution/selection. Add bench+number: **OSWorld 57.07% @ 50 steps (o3 & UI-TARS, SOTA at submission)**. "SOTA (not isolated)" CONFIRMED — no decomposition ablation exists. "self-evolving" seen in the PDF belongs to **GUI-Owl [ref 39]** in related work, **not** Lybic.
- **E-2 (Agent S2 §1D / §5):** "+18.9/+32.7% rel." pinned: **27.0 vs UI-TARS-72B-DPO 22.7 (15-step)** and **34.5 vs CCU-Claude-3.7 26.0 (50-step)**, OSWorld, Table 1 p.6 — a **full-system-vs-baseline** number, *not* an isolated decomposition ablation. The genuine component ablations are **MoG +3.08/+4.61pp** and **PHP +4.62/+6.15pp** (Fig 5, p.8). Grounding "pool" = **3 fixed prebuilt swappable experts** (Visual=UI-TARS/UGround, Textual=OCR, Structural=UNO), not learned/evolved.
- **E-3 (UFO2 §1D):** decomposition ablation "not stated" CONFIRMED. AppAgents = **per-app instances spawned/torn-down per session with a memory layer that refines without retraining** — a self-refining-per-app executor, NOT an evolved/selected pool. Benches = **WAA + OSWorld-W**; UFO2-base GPT-4o **23.4%**, up to **51.9%** (o1+API). Decomposition output is a "**dependency-ordered subtask graph**" (p.5), i.e., a DAG, not just a flat list.
- **E-4 (2605.15425 §3B/§4):** "−73.2% retry cost vs static decomposition (cost not acc)" CONFIRMED **and strengthened**: **accuracy is 100% for all three configs** (p.3), so the paper is *purely* an efficiency result; and static decomposition is even **+80.5% worse than monolithic** on retry cost (1,632 vs 904 tokens, p.1). This makes our efficiency-backlog boundary *stronger*, not weaker.

---

## 5. FILE / SOURCE LEDGER

- PDFs (scratchpad): `2509.11067.pdf` (21p), `2504.00906.pdf` (18p, benign pypdf FloatObject warnings — text intact), `2504.14603.pdf` (24p), `2605.15425.pdf` (5p).
- Extracted text: `…/scratchpad/txt/<id>/page_NNN.txt` + `_full_dehyph.txt`.
- Catalog compared: `D:\PycharmProj\HarnessX\experiments\docs\DECOMP-METHOD-CATALOG.md` (§1D GUI row, §3B ablation table, §4 positioning, §5 red-flag zone).
