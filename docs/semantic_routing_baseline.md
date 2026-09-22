# Semantic Routing Baseline

Recorded: 2026-09-22

## Eval layers

- **Contract / Regression Eval**: deterministic overrides, heuristic fallback, provenance, clarification Tool safety, and frozen 100-case routing regression set. This is not reported as LLM accuracy.
- **Live Semantic Eval**: real structured LLM routing over the 9-case ambiguity/boundary suite, with deterministic state precedence and production fallback enabled.

## Frozen datasets

- `tests/evals/cases/task_intent_validation_v0.yaml`: `9f8d326420471961f25b1afd6e7499caf03eba50c23b0af47b7b9fd0034b351a`
- `tests/evals/cases/task_routing_boundary_v0.yaml`: `0ac2e94809c645a63c6327840ccefb7ac09bd54871a22b497584f1a8da0ca7f8`

## Baseline

- Model: `Qwen/Qwen3.5-35B-A3B`
- Pytest: 1076 collected nodes, all non-live tests passed (expected skips excluded)
- Ruff: passed
- `git diff --check`: passed
- Live Semantic Boundary Eval: 9/9 passed
- Semantic LLM calls in boundary suite: 7; fallback count in latency run: 0
- Routing latency: P50 2626.1 ms; P95 5464.4 ms
- Git revision: the commit containing this report

Latency is a single seven-call local baseline, not a production SLO.
