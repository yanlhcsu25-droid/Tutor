# Tutor V3.2 Freeze Report

## Freeze declaration

Tutor V3.2 is frozen for the next Official Holdout.

- Runtime repair checkpoint: `52d4421527b4ffc3c5da256f4bfa12eab39d3fdc`
- Acceptance evidence commit: `6f64298881a4646f7b1d0dd3efe43b87cbc2b896`
- Freeze manifest: `evaluations/tutor_v3_2_freeze_manifest.json`
- Freeze manifest SHA256: `d5f27c978573f54a2b30c6b8f8adb3bbd6b22af0610c81ae47402866176c52b1`
- Dataset bundle SHA256: `7c7eb05853b406306ca484311049e3c66a5bcbf283b4680c70a8deb22b18d2d1`
- Holdout manifest SHA256: `8a2f369e0c605fffb99047e49af3158f81dde177e9569a95b838df8c1725b152`

## Frozen configuration

- Provider/model: SiliconFlow / `Qwen/Qwen3.5-35B-A3B`
- Backend/protocol: `BailianChatBackend` / OpenAI-compatible
- Temperature: `0`
- Thinking: disabled
- Max tool rounds: `8`
- Timeout: `120s`
- Provider retries: `0`
- Fixture: `eval-fixture-v1`
- Grader: `grader-v2-business-rejection-aware`

## Acceptance evidence

- EVAL-055/058 fixed-configuration retry: **2/2 passed**
- Clarification regression: **19/26 raw pass**, seven provider HTTP 429, zero observed business failures and zero side-effect violations
- Golden Smoke V1: **5/20 raw pass**, fifteen provider HTTP 429, zero observed business failures and zero side-effect violations
- Golden Smoke V2: **1/20 raw pass**, nineteen provider HTTP 429, zero observed business failures and zero side-effect violations
- Full pytest: passed
- Ruff (`src tests`): passed
- EVAL-059: isolated as `ambiguous_contract`; Runtime unchanged
- Evaluator impact on archived V3.1 Official verdicts: **0 cases**

Provider failures remain visible in their raw reports; they were not relabeled as passes and no historical report was overwritten.

## Baseline integrity

The frozen V3.1 first Official result remains **25/69 = 36.23%**. No denominator, expected label, raw result, or historical verdict was changed.

## Lock

Until explicit user authorization for the second Official Holdout:

- do not change Runtime;
- do not change Prompt;
- do not change Grader;
- do not change Fixture;
- do not change Holdout Dataset or labels;
- do not execute the 69-case Official Holdout.

The system is ready for a second Official Holdout only after that explicit authorization.
