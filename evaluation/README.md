# Tutor Evaluation System

`evaluation/` measures Agent capability. It does not replace `tests/`, which verifies code correctness.

## Domains

- `reliability/`: workflow reliability datasets, fixtures, evaluators, and runner.
- `teaching_quality/`: teaching-design, paper-quality, and consultation evaluation.
- `efficiency/`: token, context, latency, and tool-schema efficiency audits.
- `artifacts/`: generated runs and reviewed baseline reports; never imported by evaluation code.

Evaluation-system tests live in `tests/evaluation/`.

## Experiment metadata

New experiment reports must identify at least:

```json
{
  "experiment_id": "...",
  "dataset_version": "...",
  "git_commit": "...",
  "model": "...",
  "prompt_version": "...",
  "evaluator_version": "...",
  "started_at": "...",
  "status": "completed"
}
```

Equivalent existing fields such as `git_sha`, `model_ids`, or `generated_at` may be retained for historical reports, but new runners should document the mapping.

## Artifact policy

- Runner, evaluator, dataset, manifest, rubric, prompt, and reviewed baseline files are versioned.
- `artifacts/**/runs/` contains local or CI output and is ignored by Git.
- Reviewed baselines are explicitly promoted to `artifacts/**/reviewed/`.
- Frozen Holdout data must not be used for prompt, evaluator, threshold, or runtime tuning.

Langfuse Experiment integration is intentionally outside this restructuring.
