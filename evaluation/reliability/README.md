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

## Holdout policy

Frozen Holdout cases are execution-controlled. Never inspect Blind outcomes to tune prompts, runtime behavior, evaluators, thresholds, or datasets. Use Dev and Golden Smoke for iteration.
