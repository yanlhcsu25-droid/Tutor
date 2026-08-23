# Teacher Agent Reliability Benchmark

All displayed variants use the same case inputs, fixtures, model temperature, and graders. Missing variants are omitted rather than estimated.

> Limitations: this is a single run over 7 cases. The cases and graders remain implementation-adjacent, so the result is a reproducible regression benchmark, not evidence of reliability on unknown scenarios.

| Variant | Cases | Task Success | False Success | Confirmation | Recovery | Grounding | State | Avg Tools |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| failure-injection | 7 | 100.0% | 0.0% | 100.0% | 100.0% | N/A | 100.0% | 0.57 |

## Run metadata: failure-injection

- Run time: `2026-08-23T12:35:22.972411+00:00`
- Git SHA: `064232065106c3c60ad892501ad07e840e63edcb`
- Git dirty: `True`
- Model ID: `eval-invalid-response-backend, eval-scripted-backend`
- Temperature: `0`
- Dataset: `tests/evals/cases/reliability_failure_injection_v0.yaml`
- Dataset version: `b195eb4280a3`

## Case details: failure-injection

| Case | Category | Status | Result | Observed error | Failure reason |
| --- | --- | --- | --- | --- | --- |
| FI-001 | error_handling | failed | PASS | tool_timeout | — |
| FI-002 | error_handling | waiting_confirmation | PASS | stale_pending_version | — |
| FI-003 | error_handling | needs_clarification | PASS | candidate_insufficient | — |
| FI-004 | error_handling | needs_clarification | PASS | no_pending_generation | — |
| FI-005 | error_handling | failed | PASS | agent_invalid_tool_arguments | — |
| FI-006 | error_handling | failed | PASS | agent_invalid_model_response | — |
| FI-007 | error_handling | failed | PASS | agent_invalid_tool_result | — |

## Metric definitions

- **Task Success:** case-level acceptance pass rate.
- **False Success:** failed cases whose final status was `completed`.
- **Confirmation:** pass rate for cases expected to await confirmation.
- **Recovery:** pass rate for error-handling or clarification cases.
- **Grounding:** pass rate for paper-modification cases.
- **State:** state-grader pass rate.
- **Avg Tools:** mean observed Tool calls per case.
