# Holdout V3.1 Freeze — 69 Independent Cases

## Decision

The deduplicated Holdout is **frozen** with 69 independent, human-approved cases. No template cases were added to restore an arbitrary size of 100.

This freeze records a reproducible contract only. `execution_authorized` remains `false`; no formal Holdout run was performed.

## Composition

- Main Holdout: 69 cases, all `review.status: approved`
- Duplicate Regression: 31 cases, all with `duplicate_of`
- Dev: 200 cases, outside this Holdout decision
- Difficulty: Easy 23 / Medium 9 / Hard 37
- Category: Generation 25 / Pending 8 / Paper 4 / Teaching 6 / Ambiguity 15 / Adversarial 10 / Multi-turn 1

## Frozen artifacts

- Dataset: `tests/evals/staging/tutor_workflow_eval_300_v3_1/tutor_workflow_eval_300_v3_1.yaml`
- Dataset file SHA256: `2f3c9c48936a8208a53f850194d15118be9601e380e1249726a42cc140e394ad`
- Dataset bundle SHA256: `7c7eb05853b406306ca484311049e3c66a5bcbf283b4680c70a8deb22b18d2d1`
- Manifest: `tests/evals/manifests/holdout_v3_1_69.yaml`
- Manifest SHA256: `dcc364efa9f54babe11bb3bb84b2540299502097286c1bc56f4e3c625bb2acfd`
- Manifest validation: `evaluations/holdout_v3_1_69_manifest_validation.json`
- Static dataset audit: `evaluations/tutor_workflow_eval_300_v3_1_holdout69_contract_audit.json`
- Code baseline: `e855f32d83d4ec14bb30aef97015d33626844756`

The static dataset audit reports the 69 Holdout cases and 31 duplicate-regression cases as valid. Its legacy `required_split_matches` field is false because that check encodes the superseded 200/100 candidate split; the frozen manifest validator instead requires the exact deduplicated 69-case membership.

## Execution lock

- Provider: `siliconflow`
- Model: `Qwen/Qwen3.5-35B-A3B`
- Adapter: `BailianChatBackend`
- Protocol: `openai_compatible`
- Temperature: 0
- Fixture: `eval-fixture-v1`
- Grader: `grader-v1`

Runtime, grader, fixture, runner, and model-configuration component SHA values are locked in the manifest and independently recomputed by the manifest validator.
