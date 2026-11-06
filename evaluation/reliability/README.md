# Workflow Reliability Evaluation

Measures whether Tutor completes teacher workflows with correct outcomes, constraints, state transitions, and confirmation safety.

## Layout

- `datasets/cases/`: maintained smoke and acceptance suites.
- `datasets/manifests/`: frozen suite manifests.
- `datasets/staging/`: candidate and review datasets; not automatically authorized for live runs.
- `evaluators/`: deterministic outcome, constraint, state, score, and safety evaluators.
- `fixtures/`: isolated curriculum, paper, and question-bank setup.
- `runner.py`: suite execution and report generation.

Reviewed historical outputs are under `evaluation/artifacts/reports/reliability/reviewed/`; raw runs go to `evaluation/artifacts/reports/reliability/runs/`.

## Run

Deterministic smoke of the evaluation infrastructure:

```bash
uv run pytest -q tests/evaluation/reliability
```

Live Agent evaluation requires explicit provider opt-in:

```bash
RUN_LIVE_LLM=1 uv run python -m evaluation.reliability.runner \
  --cases-file evaluation/reliability/datasets/cases/golden_smoke_v2.yaml \
  --report evaluation/artifacts/reports/reliability/runs/golden-smoke-v2.json
```

Reports contain per-case outcomes plus aggregate pass rate, latency, model usage, dataset hash, and Git revision. A passing evaluator test means the evaluation system works; it is not evidence that Tutor capability passes.

### Hardened execution modes

`--mode diagnostic` permits pending Dev cases, but the report sets `benchmark_eligible` to false. `--mode benchmark` accepts only `review.status: approved` cases. A `BLOCKED` case is rejected in either mode. Use `--dataset-role` to keep Development, Smoke/regression, Holdout, and historical duplicate regression runs separate.

A manifest is an exact selection: it cannot be narrowed with `--case` or `--category`. The runner aborts on unknown/duplicate IDs, count mismatches, or dataset/manifest hash mismatches. For example, a pinned non-Holdout manifest Benchmark also requires `--expected-grader-sha256`:

```bash
uv run python -m evaluation.reliability.runner \
  --mode benchmark --dataset-role development \
  --cases-file path/to/approved-dev.yaml \
  --manifest path/to/approved-dev-manifest.yaml \
  --dataset-sha256 DATASET_SHA256 \
  --manifest-sha256 MANIFEST_SHA256 \
  --expected-grader-sha256 GRADER_SOURCE_SHA256 \
  --report evaluation/artifacts/reports/reliability/runs/dev.json
```

Aggregate independently executed Dev and DEVX reports only when their runtime and source fingerprints match:

```bash
uv run python -m evaluation.reliability.aggregate_reports \
  evaluation/artifacts/reports/reliability/runs/dev-122.json \
  evaluation/artifacts/reports/reliability/runs/devx-20.json \
  --output evaluation/artifacts/reports/reliability/runs/dev-142-aggregate.json
```

The suite pass-rate denominator is `passed + agent_failed`. Infrastructure, fixture, runner, and skipped outcomes remain visible but do not count as Agent failures. Grader summaries separately report executed pass/fail, skipped, and not-applicable counts.

## Holdout policy

Frozen Holdout cases are execution-controlled. Never inspect Blind outcomes to tune prompts, runtime behavior, evaluators, thresholds, or datasets. Use Dev and Golden Smoke for iteration.

Holdout requires Benchmark mode, an exact Holdout manifest, and a new active single-use authorization. The authorization must include `authorization_id`, `dataset_sha256`, `manifest_sha256`, `expires_at`, `status: active`, `single_use: true`, `declared_grader_version`, and the exact `grader_sha256`, `runner_sha256`, `prompt_sha256`, and `fixture_sha256`. The runner atomically creates a `.consumed.json` receipt before the first case; a consumed, expired, historical, or hash-mismatched authorization fails closed. Authorization issuance is intentionally outside the runner.
