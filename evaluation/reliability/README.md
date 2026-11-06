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

`--mode diagnostic` permits pending Dev cases and flexible case filters, but the report sets `benchmark_eligible` to false. `--mode benchmark` requires a frozen, human-reviewed exact manifest, approved cases, a clean Git worktree, and matching Git/component locks. A `BLOCKED` case is rejected in either mode. Use `--dataset-role` to keep Development, Smoke/regression, Holdout, and historical duplicate regression runs separate.

A manifest is an exact selection: it cannot be narrowed with `--case` or `--category`. The runner aborts on unknown/duplicate IDs, count mismatches, dataset/manifest hash mismatches, or source-lock mismatches. Existing manifests missing the full component locks must be reviewed and frozen again; the runner does not upgrade them automatically. For example:

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

Holdout requires Benchmark mode, an exact Holdout manifest, and a new active single-use authorization. The authorization must include `authorization_id`, `dataset_sha256`, `manifest_sha256`, `expires_at`, `status: active`, `single_use: true`, `declared_grader_version`, and the exact SHA256 for every manifest component (`runtime`, `agent`, `tools`, `services`, `prompt`, `graders`, `fixture`, `runner`, and `model_configuration`). The runner atomically claims the authorization ID in the fixed user state directory before the first case; callers cannot select a receipt path, and copying the authorization file does not create a new claim. A consumed, expired, historical, or hash-mismatched authorization fails closed. Authorization issuance is intentionally outside the runner.

Single-use consumption is guaranteed only for the same OS user on one machine/filesystem. A global cross-machine guarantee requires a separately designed centralized authorization store; this runner does not claim to provide one.
