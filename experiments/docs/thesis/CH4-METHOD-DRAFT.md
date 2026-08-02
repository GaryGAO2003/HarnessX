# Chapter 4 — Method: Intra-Task Division of Labour over an Evolved Pool

> **Draft status (Aug-02).** Complete except for result tables, which are held open
> until the arms of §4.6 land. Rewritten from the Jul-30 draft in three places: the
> budget position of §4.5 (which previously declined to match budgets and is now
> matched by construction), the credit mechanism of §4.4 (rebuilt — the previous
> signal could not separate what it was routing on), and the arm ladder of §4.6.

---

## 4.1 The layer, and the two problems that shape it

Chapter 3 routes a **whole task** to a single variant. This chapter adds an
orthogonal, training-free layer on top of the evolved pool: a lightweight planner
decomposes a task into a short list of typed subtasks; a router assigns each subtask
to a — possibly different — variant; a fixed, variant-agnostic synthesis step
aggregates the outputs into one answer, scored by the same deterministic end-task
gate used throughout. Where the pool asks *which single variant should solve this
whole task*, this layer asks *which variant should solve each part of it*.

Two design problems govern every choice that follows.

**P-A — credit without subtask ground truth.** GAIA scores only the final answer.
Individual subtasks have no reference label, and a passing attempt typically mixes
the contributions of several variants. Any per-subtask credit signal must therefore
be produced without pretending to a causal attribution the data cannot support.

**P-B — separating the value of decomposing from the value of dividing.** If a
divided pipeline beats an undivided one, the gain could come from decomposing at all,
from routing the pieces to specialised variants, or from spending more compute. This
is not a side concern; it is the cause-analysis requirement of the research question,
and it forces the arm structure of §4.6 rather than a single before/after comparison.

A third constraint is inherited rather than chosen. The mechanism presupposes
variants that differ **in capability**, not merely in the difficulty of the tasks
they happen to hold. A pool partitioned by difficulty contains a "hard-task variant",
which is not the same thing as a variant that is good at retrieval. This is why
§3.5.5 exists, and why this chapter's headline arm runs on the capability-partitioned
pool.

The layer is built as an evaluation-only, dependency-injected, flag-gated module: it
imports no recipe or upstream symbol, touches no network, and the default code path
is byte-identical to upstream. It runs in a separate mode that loads a **frozen**
pool and never enters an evolution round, so the gate, seesaw and critic are
untouched and the credit signal cannot feed back into pool evolution.

---

## 4.2 Decomposition

### 4.2.1 Mechanism

Decomposition is a single meta-model call returning a JSON array of subtask records,
each `{id, type, instruction, dep}`. The planner emits a short list, is forbidden to
decompose a subtask recursively — which bounds plan size and stops cascades — and
must assign each subtask exactly one of a **fixed four-type taxonomy**:

- **search** — find or retrieve facts and sources;
- **browse** — open a specific source and extract details;
- **compute** — calculate or reason over gathered information;
- **verify** — check or synthesise an intermediate result.

The taxonomy is frozen rather than discovered, so that the `(variant × type)` credit
cells of §4.4 are stable across tasks. Validation enforces the schema, the type
vocabulary, the count cap, and that the `dep` edges form a DAG; a deterministic
topological sort turns the DAG into a serial execution order. Parallel execution of
independent subtasks is left to future work.

Aggregation is a fixed synthesis step over the ordered subtask outputs, deliberately
**variant-agnostic**, so that aggregation does not itself become a source of
between-arm variance.

### 4.2.2 The synthesis constraint

The synthesis step has a failure mode that biases in this thesis's own favour and is
therefore constrained rather than merely noted. When an upstream subtask returns
nothing — because it exhausted its step budget, say — the synthesiser does not
report the gap. It fills it from the model's own parametric knowledge and returns a
confident answer. One observed instance opens "Based on the known content from the
[…] episode", where the browse subtask that should have supplied that content had
returned empty.

The direction of the resulting bias matters. A recalled answer that happens to be
correct is scored as a pipeline success, so the failure inflates the **decomposed**
side and only that side. Under the guard, the synthesiser is required to name any
subtask that failed to return what was asked and to state that the answer could not
be obtained, rather than supplying it. The guard is flag-gated and its setting must
be identical across arms, since enabling it lowers the decomposed side's absolute
score — the part of that score that was never earned.

Measured frequency is a **lower bound, not an estimate**: one of twenty synthesis
attempts on the smoke bench carries a self-announced marker, and that attempt failed,
so this particular sample was not inflated. Detection catches only fabrication that
announces itself; silent fabrication is invisible to it.

### 4.2.3 Why the decomposer never sees the pool

A natural alternative would let the decomposer see the pool's strengths and shape the
plan accordingly. The method deliberately does not, for three reasons in order of
force.

1. **A subtask's type is a task attribute, not a pool attribute.** Whether a task
   requires retrieval or calculation follows from what the task asks, determinable
   from its text alone. Consulting the pool would import pool-specific idiosyncrasy
   into a task-level judgment.
2. **Causal isolation.** If plans depended on the pool, two arms with different pools
   would receive different decompositions, and a between-arm difference would confound
   "the plans differed" with "the routing differed". Holding decomposition fixed and
   replaying the same plans across arms (§4.5) attributes the contrast to routing.
3. **No feedback loop.** A decomposer conditioned on the in-arm credit ledger would be
   shaped by a table accumulated from its own plans, on a non-stationary pool. The
   method refuses this.

**A consequence worth stating explicitly.** Because decomposition depends only on the
task text, its output is admissible as an input to Chapter 3: the capability
partition of §3.5.5 is the set of subtask types each task's plan requires, computed
once before any attempt and frozen. Decomposition therefore serves twice in this
thesis — as the object of study here, and as the instrument that supplies Chapter 3's
partition — and pool-blindness is exactly the property that makes the second use
legitimate.

---

## 4.3 Two-level routing

### 4.3.1 Level one: task-level routing

The outer level is Chapter 3's router: the task selects one variant by `argmax` over
the Laplace-smoothed `(variant, cluster)` estimate, where the cluster function is the
configurable partition of §3.5. This is the reproduced mechanism, and here it plays
two roles: it is the variant used when division is off, and it is the **fallback
target** for the subtask router whenever the finer signal is untrustworthy or
decomposition fails.

### 4.3.2 Level two: subtask routing

The inner router assigns each subtask under one of three modes.

- **`single`** — every subtask goes to the task-level choice. Decomposition without
  division: one variant executes the whole pipeline.
- **`round_robin`** — subtasks are spread deterministically by a rotation keyed on
  `(crc32(task_id) + attempt + subtask_index)`, with no random number generator. This
  divides labour *uniformly*, isolating the value of dividing at all from the value of
  dividing *well*.
- **`ledger`** — each subtask goes to the variant with the highest `(variant × type)`
  rate for that subtask's type (§4.4). This is the specialising router.

`ledger` carries a **cold-start fallback**: it takes the `argmax` over per-type rates,
but if the winning cell has fewer than `min_obs` observations it discards the finer
choice and returns the task-level choice. The specialising router therefore defers to
the reproduced mechanism exactly where subtask-level evidence is too thin, and
overrides it only once a cell has accumulated enough. Ties break toward the lowest
variant index.

Both `single` and `round_robin` are pure functions of `(task_id, attempt,
subtask_index)` and hold no shared state. `ledger` reads a table that other tasks
write, which is why evaluation concurrency is refused for that mode: under
overlapping tasks the route would depend on which rollouts finished first, and the
arm would not be reproducible from its own frozen inputs.

### 4.3.3 Decomposition failure

Any decomposition error — a parse failure surviving one repair retry, a missing
oracle entry, a schema or DAG validation failure — degrades the attempt to a single
whole-task rollout on the task-level choice, using sentinel identifiers that cannot
collide with a real credit cell. Synthesis is skipped, no credit is booked, and the
attempt is tagged with its fallback reason. The per-arm **fallback rate** is reported
as a diagnostic, so that a division arm whose behaviour is really the undivided
fallback in disguise is visible rather than hidden.

---

## 4.4 Credit

This section addresses P-A. It is not a supporting detail: the specialising router
reads the table built here, so the headline contrast of §4.6 has a mechanism only if
this table can separate competence.

### 4.4.1 Why the task outcome is the wrong signal

The natural signal is the one the benchmark supplies: when a task passes, credit the
variants that worked on it. Concretely, book the task's pass or fail against every
distinct `(variant, type)` pair on the chain.

This does not measure what the router needs. A chain touches 4.30 cells on average,
so one correct final answer credits the searcher, the calculator and the verifier
alike, without regard to which of them was responsible. The table that results
records **participation in tasks that passed**, not competence at a kind of work. Two
runs in which every subtask exhausted its budget and one in which none did produce
*identical* credit matrices, provided the final answers agree. A router reading such
a table is not specialising; it is following a variable that is largely noise with
respect to the decision it is making.

### 4.4.2 Subtask convergence as the signal

The alternative used here books **one observation per executed subtask**, scored on
whether that subtask finished inside its own step budget. Exhausting the budget
counts as failure.

The signal has four properties that the task outcome lacks. It is **free** — step
counts are already recorded, so no extra rollout or judge is required. It is
**objective and binary**, requiring no model judgment. It is **independent**: a
subtask's outcome is not shared with the rest of the chain. And it is **aligned with
the observed failure mode** — 88.7% of failures in the reproduction are tasks that
did not finish rather than tasks the agent could not do, with a 22% cap-hit rate, so
the signal is dense enough to accumulate.

A pair occurring twice in one chain books twice, unlike the task-outcome mode which
deduplicates: under a convergence rule two executions are two measurements.

**The limitation is declared rather than mitigated.** This signal scores
**completion, not correctness**. A subtask that stops early with a wrong answer is
recorded as a success. This is a deliberate trade of precision for being free, and it
must accompany every report of the arm that uses it. The two precise alternatives —
a per-subtask judge, or a counterfactual re-run of the same subtask under a different
variant — cost several times more per attempt and are out of budget for this thesis.
They are the natural next step for the mechanism and are listed as future work.

---

## 4.5 Cross-arm plan replay

Decomposition is a model call and therefore varies between invocations. If each arm
generated its own plans, an arm difference would confound routing with plan variation.

The pipeline therefore separates plan **generation** from plan **use**. Plans
generated once are written to a digest-stamped file which subsequent arms consume in
place of live decomposition, so every arm executes the *same* decomposition of the
same task and differs only in where the pieces are sent. The same channel admits
hand-verified oracle plans, which fix decomposition quality at a ceiling and let a
comparison against it attribute how much of the routing headroom is bounded by plan
quality rather than by routing.

A degenerate use of the channel is what supplies the undivided baseline: an empty
plan file routes every task through the whole-task fallback of §4.3.3, giving a
whole-task arm on the frozen pool without a separate code path.

---

## 4.6 Arms and readouts

All arms run on the frozen pool produced by Chapter 3, over the same replayed plans,
with the synthesis guard at the same setting.

| Arm | Decomposed | Subtask routing | Budget | What it isolates |
|---|---|---|---|---|
| **A1** | no | — | 20 steps | The reproduced system: whole task, one variant |
| **B0** | yes | `single` | 20 steps, shared | Decomposition alone, at matched budget |
| **B1** | yes | `round_robin` | 20 steps, shared | Division without specialisation |
| **B2** | yes | `ledger` | 20 steps, shared | Division *with* specialisation |

**Primary readout: `B2 − B1`.** Both arms decompose the same plans over the same pool
and divide the same work; they differ only in whether the assignment uses the credit
signal or a fixed rotation. The contrast therefore isolates the value of routing
*well* from the value of routing *at all*, and it is the answer to the improvement
question of this thesis.

**Secondary readouts.** `B1 − A1` gives the value of dividing at all. `B0 − A1` gives
the value of decomposing at all, at matched budget. `B0 − B1` separates decomposing
from dividing.

### 4.6.1 Budget is matched by construction, not reported alongside

An earlier version of this design reported per-arm cost next to the result rather
than constraining it. That is not sufficient. The decomposed pipeline as originally
implemented gave **each subtask** the full whole-task step budget, so a decomposed
attempt spent 2.2× the steps of an undivided one. Under that arrangement any positive
result is answerable with "you spent twice the compute", and any null result is
uninterpretable.

The budget is therefore shared across the chain: a decomposed attempt receives the
same total step budget as an undivided one, divided among its subtasks. `A1 − B0` is
then an equal-budget comparison by construction rather than by post-hoc
normalisation, and no separate matched-budget arm is required.

The related question — whether simply *raising* the whole-task budget would recover
the same gain — is answered from data already collected rather than by a new arm: the
reproduction's measured budget-exhaustion rate is 19.4%, which bounds the headroom
available to a budget increase.

### 4.6.2 Interpretation rules

The rules of §3.6 apply unchanged: within-task paired tests, the measured noise floor
with its 2 SD thresholds, infrastructure failures counted as failures and reported
separately. Three additions are specific to this chapter.

- **The fallback rate is reported per arm.** An arm whose decompositions frequently
  fail is really running the undivided baseline, and the contrast would be diluted
  towards zero for a reason that has nothing to do with division.
- **The convergence signal's limitation accompanies every result that depends on it**
  (§4.4.2): it scores completion, not correctness.
- **The synthesis guard setting is identical across arms** and stated with the
  results, since it changes the decomposed side's absolute score.

### 4.6.3 What each outcome licenses

If `B2 > B1` beyond the paired threshold, division by capability is worth more than
division alone, and the credit signal is doing work. If `B2 ≈ B1`, the finding is
that specialised assignment adds nothing over uniform spreading on this bench — which,
combined with `B1 − A1`, distinguishes "division does not help" from "our signal for
who should do what does not help". If `B0 ≈ A1` at matched budget, the finding is
that decomposition is budget-neutral on this bench rather than beneficial, a reading
the failure-mode analysis independently supports: 88.7% of failures are tasks that
did not finish, which points at the budget rather than at the structure of the
attempt.

None of these outcomes leaves the chapter without a result. The design is arranged so
that the negative readings are as interpretable as the positive one, which is the
purpose of having `A1`, `B0` and `B1` at all.
