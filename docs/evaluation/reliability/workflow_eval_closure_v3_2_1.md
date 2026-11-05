# Tutor Workflow Reliability Eval Closure

## 1. Scope

This phase evaluates Tutor's engineering workflow reliability across:

- generation;
- paper modification;
- TeachingDesign workflow;
- clarification;
- routing;
- pending / confirm / cancel lifecycles;
- multi-turn state;
- grounded read;
- side-effect safety;
- persistence / state consistency.

This is **not** a teaching-quality benchmark and does not demonstrate that generated teaching content, papers, or advice are pedagogically superior. Content quality requires a separate evaluation contract.

## 2. Official Results

**V3.1 First Official:**

- 25/69 = 36.23%

**V3.2.1 Second Official:**

- 64/69 = 92.75%

**Improvement:**

- +39 cases
- +56.52 percentage points

**Regressions:** 0  
**Provider failures:** 0

The V3.1 baseline is immutable. The V3.2.1 Second Official result is also immutable. Neither result was rerun, recalculated, relabeled, or overwritten during closure. The archived reports remain the source of truth:

- [`V3.1 First Official report`](official_holdout_v3_1_69_first_report.md)
- [`V3.2.1 Second Official report`](official_holdout_v3_2_1_69_second_report.md)

## 3. Remaining Known Failures

| Case | Current classification | Root cause | Type | Blocks closure? |
| --- | --- | --- | --- | --- |
| EVAL-059 | Difficulty-contract ambiguity | Runtime permits difficulty 3 as a fallback for `hard`, while the oracle interprets “全部必须是高难度” as a stricter shortage condition. | Contract ambiguity | No |
| EVAL-066 | False context-dependency guard | A complete generation request containing “不要跨章节补题” was treated as depending on a missing prior target. | Known Runtime limitation | No |
| EVAL-068 | Generation routing failure | An explicit difficulty-5 generation request was routed into TeachingDesign finalization instead of the generation workflow. | Known Runtime limitation | No |
| EVAL-145 | False question-address ambiguity | “全卷最后一题” was treated as an ambiguous “第N题” rather than a unique whole-paper address. | Known Runtime limitation | No |
| EVAL-267 | Pending-preservation safety failure | “不要现在生成” incorrectly triggered `prepare_generation_plan` instead of preserving the existing pending plan without action. | Known Runtime limitation / safety issue | No |

These cases remain failures; none is represented as resolved. They enter a future targeted-regression backlog, but do not block closure of the current benchmark. See [`workflow_eval_known_limitations_v3_2_1.md`](workflow_eval_known_limitations_v3_2_1.md).

## 4. Closure Decision

The **Workflow Reliability Eval phase is formally closed**.

The fixed primary result is:

> **64/69 = 92.75%**

This phase will not continue toward 69/69 by:

- expanding the main Holdout;
- modifying benchmark oracles;
- adjusting historical graders;
- editing the frozen dataset;
- rerunning the Official Holdout.

Any future fix for a remaining case must be evaluated in a new version, such as a V3.3 targeted regression. It must not replace or revise the V3.2.1 Official result.

## 5. Why 69 Cases Is Acceptable

The 69 cases form a human-reviewed, deduplicated, independent Blind Holdout. Review quality, case independence, and stable evaluation contracts matter more than mechanically increasing the count to 100 or 200.

This suite evaluates engineering workflow reliability; it is not presented as a large-scale statistical benchmark. The Holdout will not be enlarged through template expansion, automatic paraphrasing, or synthetic duplication merely to increase its denominator.

## 6. Next Evaluation Phase

The next phase is **Teaching Quality Eval**. This section is a roadmap only; no formal benchmark data has been created.

The phase should remain fully separate from the 69-case Workflow Holdout and should begin with rubric and schema design before case authoring. Proposed suites:

### A. TeachingDesign Quality

- `scope_fidelity`
- `objective_alignment`
- `learning_progression`
- `feasibility`
- `assessment_alignment`

### B. Paper Quality

- `knowledge_coverage`
- `difficulty_alignment`
- `question_type_balance`
- `score_consistency`
- `constraint_fidelity`
- `redundancy`

### C. Consultation / Teaching Advice Quality

- `correctness`
- `actionability`
- `pedagogical_quality`
- `grounding`
- `clarity`

The future evaluation should combine deterministic structural assertions, rubric-based behavioral or quality judging, and multi-axis scoring. Its cases and conclusions must remain independent of the Workflow Reliability Holdout.

See [`teaching_quality_eval_plan.md`](../teaching_quality/plan.md).
