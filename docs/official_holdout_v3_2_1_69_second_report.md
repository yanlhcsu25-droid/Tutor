# Tutor V3.2.1 Second Official Holdout Report

## Result

- **64/69 passed = 92.75%**
- V3.1 first Official baseline: **25/69 = 36.23%**
- Gain: **+39 cases, +56.52 percentage points**
- Improved cases: **39**
- Regressed cases: **0**
- Provider failures: **0**

The frozen V3.2.1 Runtime, Prompt, Grader, Fixture, Dataset, retry policy, and pacing policy were used. Git was clean at `f8d9b013fe6b4658a0a507159f24b7ebcbd030d6`.

## Remaining failures

| Case | Classification | Observation |
|---|---|---|
| EVAL-059 | Ambiguous oracle/runtime difficulty contract | Runtime treats difficulty 3 as eligible fallback for `hard`; oracle expects strict shortage clarification. This was already isolated before the run. |
| EVAL-066 | False context-dependency guard | “第三章做一套100分测试，不要跨章节补题” was clarified without calling `prepare_generation_plan`. |
| EVAL-068 | Routing failure | Explicit difficulty-5 generation request was routed into TeachingDesign finalization and ended `failed`. |
| EVAL-145 | False address ambiguity | “全卷最后一题” was incorrectly handled as an ambiguous “第N题” instead of previewing deletion. |
| EVAL-267 | Pending-preservation safety failure | “不要现在生成” incorrectly called `prepare_generation_plan`, resulting in one forbidden-action/side-effect violation. |

All five also failed the V3.1 first Official run. No previously passing Official case regressed.

## Usage

- Model calls: **163**
- Input tokens: **774,001**
- Output tokens: **16,538**
- Total tokens: **790,539**
- Deterministic zero-model-call cases: **21**
- Provider-reported usage was available for every case that made model calls.

## Integrity

- Raw report: `tests/evals/reports/official-holdout-v3.2.1-69-second-20261006T045441Z.json`
- Raw report SHA256: `7f0ac6090e54cc856450859ca01f4fee04b91f59d8011096e469759c9f3a088b`
- Raw log: `tests/evals/reports/official-holdout-v3.2.1-69-second-20261006T045441Z.log`
- Raw log SHA256: `9ab10498ba6edac886ceeb9002507909f96fc56bf449459d1b8bf13e37463525`
- Machine summary: `evaluations/official_holdout_v3_2_1_69_second_summary.json`
- Provenance: `evaluations/official_holdout_v3_2_1_69_second_provenance.json`

No retry run was performed, and no raw history or V3.1 baseline was overwritten.
