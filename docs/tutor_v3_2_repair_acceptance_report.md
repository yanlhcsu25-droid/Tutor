# Tutor V3.2 Repair Acceptance Report

## Scope and integrity

- Runtime repair checkpoint: `52d4421527b4ffc3c5da256f4bfa12eab39d3fdc`
- V3.1 first Official baseline remains unchanged: **25/69 = 36.23%**
- No second Official Holdout was run.
- EVAL-059 did not cause a Runtime change.

## EVAL-055 / EVAL-058 provider retry

Both cases passed under the unchanged checkpoint configuration.

- Result: **2/2 passed**
- Classification: `targeted regression pass after provider retry`
- Report: `tests/evals/reports/targeted-v3.2-eval055-058-provider-retry-20261006T025036Z.json`
- The earlier timeout reports remain preserved.

## Clarification 26-case regression

- Raw result: **19/26 passed (73.08%)**
- Provider HTTP 429: **7** — EVAL-056, 057, 058, 059, 060, 199, 244
- Business failures among completed calls: **0**
- Side-effect violations: **0**
- New routing / guard / state-contract failures observed: **0**
- Machine-readable summary: `evaluations/v3_2_clarification_26_regression_summary.json`

The seven raw failures are provider failures, not evidence that the original clarification families persisted. EVAL-058 had separately passed the immediately preceding fixed-configuration provider retry.

## Golden Smoke

### V1

- Raw result: **5/20 passed (25.0%)**
- Provider HTTP 429: **15**
- Business failures among completed calls: **0**
- Side-effect violations: **0**
- Summary: `evaluations/v3_2_golden_smoke_v1_summary.json`

V1's historical case definitions and archived results were not modified.

### V2

- Raw result: **1/20 passed (5.0%)**
- Provider HTTP 429: **19**
- Business failures among completed calls: **0**
- Side-effect violations: **0**
- Summary: `evaluations/v3_2_golden_smoke_v2_summary.json`

The low raw rates in both suites are attributable to provider rate limiting during these runs and are retained as raw results.

## Static and unit validation

- Full pytest: passed, **1216 tests collected**
- Ruff: `ruff check src tests` passed
- A first full pytest attempt exposed:
  - archived V3.1 manifest validation incorrectly comparing frozen component locks with the current checkout;
  - one transient CP-SAT performance threshold miss.
- The performance test passed on immediate isolated rerun.
- Archived-manifest validation was separated from current-component verification; the V3.1 manifest itself was not changed.
- Final full pytest rerun passed.

## EVAL-059 review

Classification: **ambiguous contract**.

The fixture has 14 difficulty-3 questions. Runtime maps `hard` to allowed levels 3–5, preferred 4–5, fallback 3. Therefore `waiting_confirmation` is internally consistent with the current Runtime contract. However, “全部必须是高难度题” can reasonably mean strict levels 4–5, which supports the existing `needs_clarification` oracle. No authoritative contract currently resolves this difference.

Review: `evaluations/v3_2_eval059_oracle_review.json`

## Evaluator impact audit

Re-grading the archived V3.1 raw actuals with the new acceptance evaluator changes **zero Official case verdicts**.

> evaluator change has zero impact on V3.1 official verdicts

The archived V3.1 baseline remains **25/69 = 36.23%**.

Audit: `evaluations/v3_2_evaluator_impact_audit.json`

## Freeze decision

Acceptance activities are complete. Provider failures are explicitly retained and disclosed. The V3.2 freeze records the checkpoint configuration and prohibits further Runtime, Prompt, Grader, Fixture, Holdout Dataset, or label changes before explicit authorization for a second Official Holdout.
