# Teaching Quality Eval Plan

## Status

Candidate specification authored: 36 unapproved Teaching Quality cases now exist under `tests/evals/teaching_quality/`; 12 deterministic cases were reclassified under `tests/evals/structural_regression/`, and 1 information-insufficient request under `tests/evals/clarification_regression/`. They are not formal benchmark cases; no model outputs or scores exist yet.

## Independence

Teaching Quality Eval is independent of the frozen 69-case Workflow Reliability Blind Holdout. It evaluates the quality of teaching artifacts and advice, not workflow state-machine reliability. Results from the two evaluations must be reported separately.

## Build order

1. Define versioned rubric dimensions and scoring anchors.
2. Define case, response, evidence, judge, and score schemas.
3. Pilot inter-rater review and resolve rubric ambiguity.
4. Manually construct 30–50 high-quality cases.
5. Human-review, deduplicate, and freeze the evaluation split before model execution.
6. Run deterministic structural assertions and rubric-based quality judging.
7. Report per-axis scores, uncertainty, disagreements, and known limitations.

Cases must not be copied, automatically paraphrased, or template-expanded from the existing 69 Workflow cases. Generated data must not be presented as a human-reviewed Blind benchmark. Synthetic material, if used during rubric development, must remain clearly labeled and outside the formal benchmark.

## Proposed suites

### `teaching_design`

Candidate dimensions:

- `scope_fidelity`
- `objective_alignment`
- `learning_progression`
- `feasibility`
- `assessment_alignment`

### `paper_quality`

Candidate dimensions:

- `knowledge_coverage`
- `difficulty_alignment`
- `question_type_balance`
- `score_consistency`
- `constraint_fidelity`
- `redundancy`

### `consultation`

Candidate dimensions:

- `correctness`
- `actionability`
- `pedagogical_quality`
- `grounding`
- `clarity`

## Evaluation approach

Use three complementary layers:

- deterministic structural assertions for schema, arithmetic, coverage, and hard constraints;
- rubric-based behavioral or quality judges with explicit evidence requirements;
- multi-axis scoring rather than a single opaque pass/fail score.

The current files are review candidates only. The rubric, schema, fixtures, and case labels must be approved before any split is frozen or model is run.
