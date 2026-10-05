# Official Holdout V3.1 — `clarification_contract` Failure Clustering

## Scope

The first official report contains 36 failures labeled `clarification_contract`. Ten carry recorded provider HTTP 429 errors and are excluded here. This analysis covers the remaining **26 cases** without changing or regrading the official 25/69 result.

## Executive finding

This is not primarily a single “model asks too many questions” failure class.

| Primary cluster | Count | Case IDs |
|---|---:|---|
| Semantic clarification was returned as `completed`, with no structured question | 13 | 049, 050, 053, 054, 058, 242, 247, 249, 262, 263, 268, 269, 270 |
| TeachingDesign guard overrode a valid infeasibility exit | 4 | 048, 052, 055, 199 |
| Generation feasibility guard accepted an impossible hard-constraint plan | 4 | 051, 056, 057, 060 |
| Generation request was routed to TeachingDesign | 2 | 046, 047 |
| Deterministic `no_current_paper` clarification omitted its structured question | 2 | 244, 264 |
| Probable case-oracle mismatch | 1 | 059 |

The dominant issue is therefore **status/payload contract drift**, followed by **deterministic feasibility and workflow-guard defects**.

## Requested hypotheses

### 1. 明明参数足够却多问一句

Only **EVAL-059** resembles this hypothesis, but the evidence points more strongly to a probable case-oracle mismatch:

- Request: 10 hard questions from Chapter 1.
- Observation: 14/14 questions profiled, all under difficulty key `3`.
- Actual: a 10-question hard plan was prepared and correctly stopped at confirmation.
- Expected: `needs_clarification` with no pending plan.

The model did not ask for a missing parameter; it asked for normal two-phase confirmation. This case needs human oracle review before any benchmark change.

### 2. 已有 setup/pending/current paper，却没正确利用

**Not observed in this population.** All 26 dataset cases have `setup: null` and begin without a Pending Generation, Pending Paper Change, current Paper, or active TeachingDesign.

This hypothesis may exist elsewhere, but it does not explain these 26 failures.

### 3. 把约束句误判成缺信息

Relevant cases: **EVAL-264, 268, 269, 270**.

These messages are standalone constraints:

- change total score to 80, but do not parse it as question 80;
- do not fill from another chapter;
- read-only, make no changes.

Without an attached task or target, requesting the missing object is reasonable. The defects are:

- 268–270 acknowledged the constraint and asked for the target, but finalized as `completed`;
- 264 correctly became `needs_clarification`, but the deterministic guard emitted no structured clarification question.

So this is primarily a **response-contract problem**, not evidence that the constraints were misunderstood.

### 4. 把确认/否定边界理解错

Relevant cases: **EVAL-262, 263**.

The safety semantics were correct:

- “先预览，不是现在确认” caused no confirm or write-side Tool call;
- “确认一下题数” was correctly distinguished from “确认生成”.

Both failures came from returning `completed` while the prose asked what should be previewed or explained that no pending plan existed. This is **safe behavior with incorrect result typing**, not a confirmation-side-effect failure.

### 5. 工具 observation 已足够，但模型仍要求澄清

Nine cases had enough observation to conclude infeasibility:

- **EVAL-048, 049, 050, 052, 053, 054, 055, 058, 199**.

However, clarification was not intrinsically unnecessary. The Tool observation answered “can the current hard request be satisfied?”; it did not choose whether the teacher wanted to relax, change, wait, or abandon the request.

Two implementation failures occurred:

1. 049/050/053/054/058 explained the shortage and requested a decision, but returned `completed` with `clarification_questions=[]`.
2. 048/052/055/199 were changed to `failed` by `teaching_design_creation_required`, even though an infeasibility clarification should have been a valid terminal branch.

### 6. deterministic guard 过严，而不是模型本身问题

**Confirmed for EVAL-048, 052, 055, 199.**

After successful curriculum and question-bank inspection, the runtime required creation of a TeachingDesign artifact. When no artifact was created, it deterministically returned:

- status `failed`;
- blocking error `teaching_design_creation_required`;
- “环境调查已经完成，但本轮没有成功创建可确认的 TeachingDesign。”

That guard is too strict for the infeasible-supply branch. The runtime should permit a structured `needs_clarification` exit before requiring an artifact.

A different deterministic defect appears in **EVAL-244 and 264**: the `no_current_paper` guard chose the correct status but failed to populate `clarification_questions`.

## Supply-contract failures

Four cases show the opposite problem—guards were too lax:

- **EVAL-051**: requested 15 unique questions; supply was 14; Pending Generation was still persisted.
- **EVAL-056**: requested 30 Chapter 2 multiple-choice questions; supply was 0; Pending Generation was persisted.
- **EVAL-057**: requested 40 total / 20 calculation questions; supply was 14 total / 5 calculation; the plan silently changed the total to 20 while preserving an impossible 20-calculation requirement.
- **EVAL-060**: requested 8 calculation questions; supply was 5; Pending Generation was persisted.

In every case, `prepare_generation_plan` returned `ok=true`, `blocking_errors=[]`, and `clarification_questions=[]`. This cannot be fixed reliably by prompt wording alone; the shared Tool contract needs deterministic demand-versus-supply validation.

## Workflow-routing failures

**EVAL-046 and 047** were explicit paper-generation requests, but the model called `create_teaching_design` and persisted an active design. EVAL-047 even recorded a shortage warning inside that design. These are genuine model/tool-routing failures, although stronger tool exposure and intent boundaries can make them deterministic.

## Remediation order

1. **P0 — Generation feasibility contract:** compare hard question count/type/difficulty constraints with authoritative aggregate supply before accepting a pending plan. Shortage must return blocking errors and no Pending Generation.
2. **P0 — Infeasibility terminal branch:** permit structured `needs_clarification` before TeachingDesign creation guards.
3. **P0 — Clarification schema invariant:** every `needs_clarification` result must include at least one structured question, including deterministic guard paths.
4. **P1 — Status acceptance:** a response requesting a user decision with no produced artifact must not finalize as `completed`; enforce this through structured output rather than prose-only heuristics.
5. **P1 — Workflow routing:** explicit question count, score, and question-type requests should stay in the generation workflow and should not expose `create_teaching_design` as the completion path.
6. **P2 — Human oracle review:** review EVAL-059 against the fixture difficulty scale. Do not alter the sealed first result.

## Benchmark integrity

- Official result changed: **no**
- Cases regraded: **no**
- Denominator changed: **no**
- Probable grader/case gap: **EVAL-059 only, pending human confirmation**
