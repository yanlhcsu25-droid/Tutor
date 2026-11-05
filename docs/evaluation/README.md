# Evaluation Engineering

Tutor separates four repository responsibilities:

```text
src/         Agent product implementation
tests/       software correctness tests
evaluation/  Agent capability evaluation
docs/        architecture, decisions, and reviewed conclusions
```

## Boundaries

- Tests use assertions to verify functions, modules, contracts, and integration behavior.
- Evaluation executes complete Agent tasks and measures outcomes with datasets and evaluators.
- Evaluation infrastructure is itself verified under `tests/evaluation/`.
- Runtime code under `src/calculus_agent/` remains independent from generated evaluation artifacts.

## Domains

- [Workflow Reliability](reliability/): workflow completion, state, constraints, and safety.
- [Teaching Quality](teaching_quality/): structural and semantic quality of educational artifacts.
- [Efficiency](efficiency/): token, context, latency, and tool-schema cost.

Raw runs are not design documentation. Promote only reviewed, reproducible conclusions into `docs/evaluation/` and preserve their dataset, Git, model, prompt, and evaluator provenance.
