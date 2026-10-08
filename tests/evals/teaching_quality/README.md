# Teaching Quality Eval v1 — candidate specification

This dataset evaluates artifact quality independently from the frozen Workflow Reliability holdout. It contains **36 Teaching Quality candidates**: 12 teaching design, 13 paper quality, and 11 consultation. Twelve deterministic cases were moved to `tests/evals/structural_regression/`, and one information-insufficient teaching-design request was moved to `tests/evals/clarification_regression/`. No model outputs or benchmark scores have been produced.

## Layout

- `cases/`: three candidate YAML suites
- `schema/teaching_quality_case.schema.json`: suite/case contract
- `schema/judge_output.schema.json`: constrained LLM-judge response
- `rubrics/`: suite-specific 1–5 scoring anchors
- `fixtures/README.md`: fixture provenance and stability contract
- `reports/`: intentionally empty until review and execution
- `runner/validate_candidates.py`: approved-case metadata validation; it never invokes a model
- `grader/`: deterministic structural grading, judge parsing, and outcome scoring
- `prompts/`: suite-specific behavioral judge contracts; no invocation code
- `tests/`: structural, scoring, metadata, split, and judge-parser tests
- `manifests/split_v1.yaml`: frozen family-isolated 24 Dev / 12 Blind split
- `manifests/split_v1.yaml.sha256`: external manifest hash (avoids self-referential hashing)
- `calibration/calibration_set_v1.yaml`: 7 Dev cases with 21 fixed strong/borderline/poor outputs and approved axis ranges
- `calibration/calibration_set_v1.provenance.yaml`: human-review summary and pre-approval artifact SHA
- `calibration/semantic_contrast_v1.yaml`: 15 binary semantic pass/fail/boundary fixtures pending human label review
- `runner/run_judge_calibration.py`: fixed-sample calibration only; never runs Tutor or Blind cases

Validate locally:

```bash
.venv/bin/python tests/evals/teaching_quality/runner/validate_candidates.py
.venv/bin/python tests/evals/teaching_quality/runner/validate_split_manifest.py
.venv/bin/python tests/evals/teaching_quality/runner/validate_calibration_set.py
.venv/bin/python tests/evals/teaching_quality/runner/validate_semantic_contrast_set.py
```

## Evaluation boundary

Deterministic structural assertions decide hard facts such as count, score arithmetic, scope, exclusions, duration, duplication, and supply. The LLM judge scores soft teaching quality only. It must not override a structural result.

Case admission follows an information-sufficiency boundary:

- sufficient scope, learner evidence, goal, and relevant constraints → Teaching Quality;
- a best response dominated by requests for missing information → Workflow/Clarification Regression or a separate Clarification Quality suite.

Teaching Quality may retain at most a small number of explicit anti-hallucination boundary cases; they must not dominate Consultation Quality scoring.

### Semantic assertion contract

- `required_content` checks whether the required meaning is present; it must not require fixed headings or literal phrases.
- `forbidden_content` fails only when forbidden material is taught, recommended, or used to solve the task. A correct exclusion such as “本节不讲定积分” is allowed when `mention_allowed: true`.
- Raw substring matching is not a valid implementation for either assertion type.

The deterministic structural grader is implemented for all assertion types referenced by the 36 approved candidates plus the documented regression primitives. Semantic assertions delegate to a strict `ModelBackedSemanticAssertionEvaluator`, which accepts only `passed / evidence / reason` and cannot emit quality scores. Its provider backend and model configuration are intentionally not selected or invoked yet; tests use a scripted backend and must not be interpreted as calibration evidence.

### Teaching-design derived fields

`teaching_design.chapters` is a grader-derived view, not a model-declared field. The grader must map `scope_names`, `knowledge_plan` names, and `teaching_sequence[].knowledge_points` through the curriculum fixture, then compare the resulting chapters. It must not compare topic strings directly with chapter names or trust a claimed chapter list.

Likewise, chapter time share is computed from actual timed lesson activities grouped through the curriculum mapping; per-lesson duration is computed from each lesson's activities. `count_min_per_topic` groups actual `examples` or `misconceptions` by topic and counts those objects. Model-declared summaries such as `chapter_time_share` or `minimum_examples_per_topic` are not evidence.

All judge axes use the following anchors:

- **5**: fully satisfies the requirement with clear teaching value
- **4**: correct with a minor non-blocking weakness
- **3**: usable with a material weakness
- **2**: partly meets the need but quality is poor
- **1**: seriously wrong or unusable

Default suite weights are declared in each YAML file. Teaching Design and Paper Quality cases select only applicable axes with case-local weights summing to 1.0; Consultation currently uses all default axes. `hallucination_control` and `constraint_preservation` may be reported as cross-suite diagnostics, but are not included in the v1 weighted score.

## Outcome policy

- **PASS**: every critical structural assertion passes, weighted judge score ≥ 4.0, and every core axis ≥ 3.
- **PARTIAL**: every critical structural assertion passes and 3.0 ≤ weighted score < 4.0, unless a fail condition applies.
- **FAIL**: any critical structural failure, weighted score < 3.0, a core teaching axis score of 1, a material factual error, scope violation, or serious user-constraint violation.

## Failure taxonomy

`scope_violation`, `objective_misalignment`, `difficulty_mismatch`, `poor_progression`, `unrealistic_plan`, `weak_assessment_alignment`, `knowledge_coverage_gap`, `question_type_imbalance`, `score_inconsistency`, `redundant_questions`, `constraint_violation`, `question_bank_hallucination`, `incorrect_diagnosis`, `generic_advice`, `poor_actionability`, `ungrounded_inference`, `clarity_issue`, `judge_error`, `fixture_issue`, `provider_failure`.

## Frozen split and access policy

`manifests/split_v1.yaml` freezes 24 Dev and 12 Blind cases with no family crossing splits. Blind contains 4 Teaching Design, 4 Paper Quality, and 4 Consultation cases. The manifest records source file hashes, aggregate dataset SHA, source Git checkpoint, case/family metadata, and an externally recorded manifest SHA.

The Blind split is **do not run** until the semantic evaluator, fixtures, judge model/configuration, prompts, rubric, thresholds, scoring, and execution provenance contract are frozen. Prompt, grader, rubric, threshold, or case tuning may use Dev only. Blind results must never be used to modify v1 cases or contracts.

The source YAML remains labeled `split: candidate`; split membership is owned exclusively by the frozen manifest. Human review and suite status are `approved`, but no benchmark result exists yet.

## Dev Judge calibration set

`calibration/calibration_set_v1.yaml` selects 7 Dev-only cases: 2 Teaching Design, 2 Paper Quality, and 3 Consultation. Each has fixed strong, borderline, and poor outputs with human-approved axis ranges. These 21 samples test band separation, ranking, axis agreement, and repeat stability; they are not Tutor outputs and must not be reported as Agent performance. The review retained all 21 overall bands and outputs, while revising 11 samples' axis ranges; provenance preserves the pre-approval SHA. No Blind case is included.

`calibration/semantic_contrast_v1.yaml` contains 15 fixed binary samples across the five semantic assertion types. Its labels remain review-required. Preliminary real-model reports under `reports/` classify valid wrong judgments as `judge_error` or `semantic_assertion_error`; transport failures, malformed JSON, and schema-invalid responses are `provider_error`. See `reports/judge-calibration-error-analysis-v1.md` for the no-new-call error analysis and minimal-change proposal.
