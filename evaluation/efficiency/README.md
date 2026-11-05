# Efficiency Evaluation

Measures token, context, latency, and tool-schema cost without changing Tutor business behavior.

## Audits

- `baseline_audit.py`: offline token-cost profile from a saved reliability report.
- `tool_schema_audit.py`: offline tool-schema exposure audit.
- `tool_schema_ab_evaluation.py`: explicitly opted-in provider A/B evaluation.

Unit tests live in `tests/evaluation/efficiency/`. Generated output belongs in `evaluation/artifacts/reports/efficiency/runs/`; reviewed baselines are in `evaluation/artifacts/reports/efficiency/reviewed/`.

```bash
uv run python -m evaluation.efficiency.baseline_audit
uv run python -m evaluation.efficiency.tool_schema_audit
uv run pytest -q tests/evaluation/efficiency
```

Character-count reductions are context proxies, not token savings. Token claims require complete provider usage. A/B conclusions must report business regressions, model-call changes, latency, and comparable-pair scope.
