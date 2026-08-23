# Teacher Agent Reliability Benchmark

All displayed variants use the same case inputs, fixtures, model temperature, and graders. Missing variants are omitted rather than estimated.

> Limitations: this is a single run over 10 cases. The cases and graders remain implementation-adjacent, so the result is a reproducible regression benchmark, not evidence of reliability on unknown scenarios.

The deterministic failure-injection run is reported separately in [agent_reliability_failure_injection.md](agent_reliability_failure_injection.md).

| Variant | Cases | Task Success | False Success | Confirmation | Recovery | Grounding | State | Avg Tools |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| state-policy | 10 | 100.0% | 0.0% | 100.0% | 100.0% | 100.0% | 100.0% | 2.3 |
| tool-agent | 10 | 90.0% | 10.0% | 100.0% | 50.0% | 100.0% | 90.0% | 2.4 |
| prompt-only | 10 | 10.0% | 90.0% | 0.0% | 50.0% | 0.0% | 20.0% | 0.1 |

## Run metadata: state-policy

- Run time: `2026-08-23T12:45:39.685884+00:00`
- Git SHA: `064232065106c3c60ad892501ad07e840e63edcb`
- Git dirty: `True`
- Model ID: `Qwen/Qwen3.5-35B-A3B, eval-tool-failure-backend`
- Temperature: `0`
- Dataset: `tests/evals/cases/teacher_acceptance_v0.yaml`
- Dataset version: `4efa7ee45ec0`

## Case details: state-policy

| Case | Category | Status | Result | Observed error | Failure reason |
| --- | --- | --- | --- | --- | --- |
| TD-001 | teaching_design | waiting_confirmation | PASS | — | — |
| TD-002 | teaching_design | waiting_confirmation | PASS | — | — |
| TD-003 | teaching_design | waiting_confirmation | PASS | invalid_tool_arguments | — |
| GEN-001 | generation | waiting_confirmation | PASS | — | — |
| GEN-002 | generation | completed | PASS | — | — |
| GEN-003 | generation | needs_clarification | PASS | — | — |
| MOD-001 | paper_modification | waiting_confirmation | PASS | question_address_not_found | — |
| MOD-002 | paper_modification | waiting_confirmation | PASS | — | — |
| PENDING-001 | pending | completed | PASS | — | — |
| ERR-001 | error_handling | failed | PASS | unknown_tool | — |

## Run metadata: tool-agent

- Run time: `2026-08-23T12:48:31.797532+00:00`
- Git SHA: `064232065106c3c60ad892501ad07e840e63edcb`
- Git dirty: `True`
- Model ID: `Qwen/Qwen3.5-35B-A3B, eval-tool-failure-backend`
- Temperature: `0`
- Dataset: `tests/evals/cases/teacher_acceptance_v0.yaml`
- Dataset version: `4efa7ee45ec0`

## Case details: tool-agent

| Case | Category | Status | Result | Observed error | Failure reason |
| --- | --- | --- | --- | --- | --- |
| TD-001 | teaching_design | waiting_confirmation | PASS | — | — |
| TD-002 | teaching_design | waiting_confirmation | PASS | — | — |
| TD-003 | teaching_design | waiting_confirmation | PASS | — | — |
| GEN-001 | generation | waiting_confirmation | PASS | — | — |
| GEN-002 | generation | completed | PASS | — | — |
| GEN-003 | generation | completed | FAIL | — | status: expected='needs_clarification', actual='completed'; required tools not called: ['confirm_generation']; unexpected final status: expected one of ['needs_clarification'], actual='completed' |
| MOD-001 | paper_modification | waiting_confirmation | PASS | — | — |
| MOD-002 | paper_modification | waiting_confirmation | PASS | — | — |
| PENDING-001 | pending | completed | PASS | — | — |
| ERR-001 | error_handling | failed | PASS | unknown_tool | — |

## Run metadata: prompt-only

- Run time: `2026-08-23T12:49:19.843921+00:00`
- Git SHA: `064232065106c3c60ad892501ad07e840e63edcb`
- Git dirty: `True`
- Model ID: `Qwen/Qwen3.5-35B-A3B, eval-tool-failure-backend`
- Temperature: `0`
- Dataset: `tests/evals/cases/teacher_acceptance_v0.yaml`
- Dataset version: `4efa7ee45ec0`

## Case details: prompt-only

| Case | Category | Status | Result | Observed error | Failure reason |
| --- | --- | --- | --- | --- | --- |
| TD-001 | teaching_design | completed | FAIL | — | status: expected='waiting_confirmation', actual='completed'; required tools not called: ['create_teaching_design']; unexpected final status: expected one of ['waiting_confirmation'], actual='completed' |
| TD-002 | teaching_design | completed | FAIL | — | status: expected='waiting_confirmation', actual='completed'; required tools not called: ['create_teaching_design']; unexpected final status: expected one of ['waiting_confirmation'], actual='completed' |
| TD-003 | teaching_design | completed | FAIL | — | status: expected='waiting_confirmation', actual='completed'; required tools not called: ['create_teaching_design']; unexpected final status: expected one of ['waiting_confirmation'], actual='completed' |
| GEN-001 | generation | completed | FAIL | — | status: expected='waiting_confirmation', actual='completed'; pending_generation: expected mapping, actual=None; expected total_score=100, but actual total_score is unavailable; required tools not called: ['prepare_generation_plan']; unexpected final status: expected one of ['waiting_confirmation'], actual='completed' |
| GEN-002 | generation | completed | FAIL | — | required tools not called: ['confirm_generation'] |
| GEN-003 | generation | completed | FAIL | — | status: expected='needs_clarification', actual='completed'; required tools not called: ['confirm_generation']; unexpected final status: expected one of ['needs_clarification'], actual='completed' |
| MOD-001 | paper_modification | completed | FAIL | — | status: expected='waiting_confirmation', actual='completed'; required tools not called: ['read_paper', 'preview_paper_changes']; unexpected final status: expected one of ['waiting_confirmation'], actual='completed' |
| MOD-002 | paper_modification | completed | FAIL | — | status: expected='waiting_confirmation', actual='completed'; required tools not called: ['preview_paper_changes']; unexpected final status: expected one of ['waiting_confirmation'], actual='completed' |
| PENDING-001 | pending | completed | FAIL | — | pending_generation: expected=None, actual={'paper_type': 'chapter_test', 'scope_names': ['第一章 函数与极限'], 'audience': None, 'question_count': 10, 'total_score': 100, 'question_type_requirements': None, 'knowledge_preferences': None, 'required_knowledge_names': None, 'knowledge_priority_weights': None, 'difficulty_level': None, 'difficulty_ratio': None, 'difficulty_preference': None, 'diversity_preference': None, 'target_duration_min': None, 'duration_tolerance_min': None, 'ability_weights': None, 'constraint_provenance': {}, 'total_score_source': 'teacher_explicit', 'locked_score_question_types': [], 'pending_version': 1}; required tools not called: ['discard_pending_plan'] |
| ERR-001 | error_handling | failed | PASS | unknown_tool | — |

## Metric definitions

- **Task Success:** case-level acceptance pass rate.
- **False Success:** failed cases whose final status was `completed`.
- **Confirmation:** pass rate for cases expected to await confirmation.
- **Recovery:** pass rate for error-handling or clarification cases.
- **Grounding:** pass rate for paper-modification cases.
- **State:** state-grader pass rate.
- **Avg Tools:** mean observed Tool calls per case.
