# Teacher Agent Dataset Contract Audit

- Dataset SHA256: `46436b6d837b83f4b5c6f9ba159aa8efbe9307b6513ba122ae122b6b01b2c118`
- Cases found: **240** / expected **300**
- 200 Development + 100 Holdout Candidate: **FAIL**

## Dispositions

| Disposition | Count |
| --- | ---: |
| valid | 40 |
| fixture_mismatch | 34 |
| ambiguous_label | 48 |
| runner_unsupported | 114 |
| grader_unsupported | 4 |

## Suites

| Suite | Split | Cases | Parse |
| --- | --- | ---: | --- |
| reliability_failure_injection_v0.yaml | unspecified | 20 | PASS |
| task_intent_validation_v0.yaml | validation | 100 | PASS |
| task_routing_boundary_v0.yaml | unspecified | 9 | PASS |
| teacher_acceptance_v0.yaml | unspecified | 21 | PASS |
| teacher_agent_regression.yaml | unspecified | 30 | PASS |
| teacher_workflow_candidate_v0.yaml | candidate | 60 | PASS |

## Non-valid cases

| Case | Suite | Disposition | Findings |
| --- | --- | --- | --- |
| INT-DIR-001 | task_intent_validation_v0.yaml | runner_unsupported | turns must be a non-empty list; expected must be an object for workflow runner; case has no grader |
| INT-DIR-002 | task_intent_validation_v0.yaml | runner_unsupported | turns must be a non-empty list; expected must be an object for workflow runner; case has no grader |
| INT-DIR-003 | task_intent_validation_v0.yaml | runner_unsupported | turns must be a non-empty list; expected must be an object for workflow runner; case has no grader |
| INT-DIR-004 | task_intent_validation_v0.yaml | runner_unsupported | turns must be a non-empty list; expected must be an object for workflow runner; case has no grader |
| INT-DIR-005 | task_intent_validation_v0.yaml | runner_unsupported | turns must be a non-empty list; expected must be an object for workflow runner; case has no grader |
| INT-DIR-006 | task_intent_validation_v0.yaml | runner_unsupported | turns must be a non-empty list; expected must be an object for workflow runner; case has no grader |
| INT-DIR-007 | task_intent_validation_v0.yaml | runner_unsupported | turns must be a non-empty list; expected must be an object for workflow runner; case has no grader |
| INT-DIR-008 | task_intent_validation_v0.yaml | runner_unsupported | turns must be a non-empty list; expected must be an object for workflow runner; case has no grader |
| INT-DIR-009 | task_intent_validation_v0.yaml | runner_unsupported | turns must be a non-empty list; expected must be an object for workflow runner; case has no grader |
| INT-DIR-010 | task_intent_validation_v0.yaml | runner_unsupported | turns must be a non-empty list; expected must be an object for workflow runner; case has no grader |
| INT-DIR-011 | task_intent_validation_v0.yaml | runner_unsupported | turns must be a non-empty list; expected must be an object for workflow runner; case has no grader |
| INT-DIR-012 | task_intent_validation_v0.yaml | runner_unsupported | turns must be a non-empty list; expected must be an object for workflow runner; case has no grader |
| INT-DIR-013 | task_intent_validation_v0.yaml | runner_unsupported | turns must be a non-empty list; expected must be an object for workflow runner; case has no grader |
| INT-DIR-014 | task_intent_validation_v0.yaml | runner_unsupported | turns must be a non-empty list; expected must be an object for workflow runner; case has no grader |
| INT-DIR-015 | task_intent_validation_v0.yaml | runner_unsupported | turns must be a non-empty list; expected must be an object for workflow runner; case has no grader |
| INT-DIR-016 | task_intent_validation_v0.yaml | runner_unsupported | turns must be a non-empty list; expected must be an object for workflow runner; case has no grader |
| INT-DIR-017 | task_intent_validation_v0.yaml | runner_unsupported | turns must be a non-empty list; expected must be an object for workflow runner; case has no grader |
| INT-DIR-018 | task_intent_validation_v0.yaml | runner_unsupported | turns must be a non-empty list; expected must be an object for workflow runner; case has no grader |
| INT-DIR-019 | task_intent_validation_v0.yaml | runner_unsupported | turns must be a non-empty list; expected must be an object for workflow runner; case has no grader |
| INT-DIR-020 | task_intent_validation_v0.yaml | runner_unsupported | turns must be a non-empty list; expected must be an object for workflow runner; case has no grader |
| INT-DIR-021 | task_intent_validation_v0.yaml | runner_unsupported | turns must be a non-empty list; expected must be an object for workflow runner; case has no grader |
| INT-DIR-022 | task_intent_validation_v0.yaml | runner_unsupported | turns must be a non-empty list; expected must be an object for workflow runner; case has no grader |
| INT-DIR-023 | task_intent_validation_v0.yaml | runner_unsupported | turns must be a non-empty list; expected must be an object for workflow runner; case has no grader |
| INT-DIR-024 | task_intent_validation_v0.yaml | runner_unsupported | turns must be a non-empty list; expected must be an object for workflow runner; case has no grader |
| INT-DIR-025 | task_intent_validation_v0.yaml | runner_unsupported | turns must be a non-empty list; expected must be an object for workflow runner; case has no grader |
| INT-DES-001 | task_intent_validation_v0.yaml | runner_unsupported | turns must be a non-empty list; expected must be an object for workflow runner; case has no grader |
| INT-DES-002 | task_intent_validation_v0.yaml | runner_unsupported | turns must be a non-empty list; expected must be an object for workflow runner; case has no grader |
| INT-DES-003 | task_intent_validation_v0.yaml | runner_unsupported | turns must be a non-empty list; expected must be an object for workflow runner; case has no grader |
| INT-DES-004 | task_intent_validation_v0.yaml | runner_unsupported | turns must be a non-empty list; expected must be an object for workflow runner; case has no grader |
| INT-DES-005 | task_intent_validation_v0.yaml | runner_unsupported | turns must be a non-empty list; expected must be an object for workflow runner; case has no grader |
| INT-DES-006 | task_intent_validation_v0.yaml | runner_unsupported | turns must be a non-empty list; expected must be an object for workflow runner; case has no grader |
| INT-DES-007 | task_intent_validation_v0.yaml | runner_unsupported | turns must be a non-empty list; expected must be an object for workflow runner; case has no grader |
| INT-DES-008 | task_intent_validation_v0.yaml | runner_unsupported | turns must be a non-empty list; expected must be an object for workflow runner; case has no grader |
| INT-DES-009 | task_intent_validation_v0.yaml | runner_unsupported | turns must be a non-empty list; expected must be an object for workflow runner; case has no grader |
| INT-DES-010 | task_intent_validation_v0.yaml | runner_unsupported | turns must be a non-empty list; expected must be an object for workflow runner; case has no grader |
| INT-DES-011 | task_intent_validation_v0.yaml | runner_unsupported | turns must be a non-empty list; expected must be an object for workflow runner; case has no grader |
| INT-DES-012 | task_intent_validation_v0.yaml | runner_unsupported | turns must be a non-empty list; expected must be an object for workflow runner; case has no grader |
| INT-DES-013 | task_intent_validation_v0.yaml | runner_unsupported | turns must be a non-empty list; expected must be an object for workflow runner; case has no grader |
| INT-DES-014 | task_intent_validation_v0.yaml | runner_unsupported | turns must be a non-empty list; expected must be an object for workflow runner; case has no grader |
| INT-DES-015 | task_intent_validation_v0.yaml | runner_unsupported | turns must be a non-empty list; expected must be an object for workflow runner; case has no grader |
| INT-DES-016 | task_intent_validation_v0.yaml | runner_unsupported | turns must be a non-empty list; expected must be an object for workflow runner; case has no grader |
| INT-DES-017 | task_intent_validation_v0.yaml | runner_unsupported | turns must be a non-empty list; expected must be an object for workflow runner; case has no grader |
| INT-DES-018 | task_intent_validation_v0.yaml | runner_unsupported | turns must be a non-empty list; expected must be an object for workflow runner; case has no grader |
| INT-DES-019 | task_intent_validation_v0.yaml | runner_unsupported | turns must be a non-empty list; expected must be an object for workflow runner; case has no grader |
| INT-DES-020 | task_intent_validation_v0.yaml | runner_unsupported | turns must be a non-empty list; expected must be an object for workflow runner; case has no grader |
| INT-DES-021 | task_intent_validation_v0.yaml | runner_unsupported | turns must be a non-empty list; expected must be an object for workflow runner; case has no grader |
| INT-DES-022 | task_intent_validation_v0.yaml | runner_unsupported | turns must be a non-empty list; expected must be an object for workflow runner; case has no grader |
| INT-DES-023 | task_intent_validation_v0.yaml | runner_unsupported | turns must be a non-empty list; expected must be an object for workflow runner; case has no grader |
| INT-DES-024 | task_intent_validation_v0.yaml | runner_unsupported | turns must be a non-empty list; expected must be an object for workflow runner; case has no grader |
| INT-DES-025 | task_intent_validation_v0.yaml | runner_unsupported | turns must be a non-empty list; expected must be an object for workflow runner; case has no grader |
| INT-PLN-001 | task_intent_validation_v0.yaml | runner_unsupported | turns must be a non-empty list; expected must be an object for workflow runner; case has no grader |
| INT-PLN-002 | task_intent_validation_v0.yaml | runner_unsupported | turns must be a non-empty list; expected must be an object for workflow runner; case has no grader |
| INT-PLN-003 | task_intent_validation_v0.yaml | runner_unsupported | turns must be a non-empty list; expected must be an object for workflow runner; case has no grader |
| INT-PLN-004 | task_intent_validation_v0.yaml | runner_unsupported | turns must be a non-empty list; expected must be an object for workflow runner; case has no grader |
| INT-PLN-005 | task_intent_validation_v0.yaml | runner_unsupported | turns must be a non-empty list; expected must be an object for workflow runner; case has no grader |
| INT-PLN-006 | task_intent_validation_v0.yaml | runner_unsupported | turns must be a non-empty list; expected must be an object for workflow runner; case has no grader |
| INT-PLN-007 | task_intent_validation_v0.yaml | runner_unsupported | turns must be a non-empty list; expected must be an object for workflow runner; case has no grader |
| INT-PLN-008 | task_intent_validation_v0.yaml | runner_unsupported | turns must be a non-empty list; expected must be an object for workflow runner; case has no grader |
| INT-PLN-009 | task_intent_validation_v0.yaml | runner_unsupported | turns must be a non-empty list; expected must be an object for workflow runner; case has no grader |
| INT-PLN-010 | task_intent_validation_v0.yaml | runner_unsupported | turns must be a non-empty list; expected must be an object for workflow runner; case has no grader |
| INT-PLN-011 | task_intent_validation_v0.yaml | runner_unsupported | turns must be a non-empty list; expected must be an object for workflow runner; case has no grader |
| INT-PLN-012 | task_intent_validation_v0.yaml | runner_unsupported | turns must be a non-empty list; expected must be an object for workflow runner; case has no grader |
| INT-PLN-013 | task_intent_validation_v0.yaml | runner_unsupported | turns must be a non-empty list; expected must be an object for workflow runner; case has no grader |
| INT-PLN-014 | task_intent_validation_v0.yaml | runner_unsupported | turns must be a non-empty list; expected must be an object for workflow runner; case has no grader |
| INT-PLN-015 | task_intent_validation_v0.yaml | runner_unsupported | turns must be a non-empty list; expected must be an object for workflow runner; case has no grader |
| INT-PLN-016 | task_intent_validation_v0.yaml | runner_unsupported | turns must be a non-empty list; expected must be an object for workflow runner; case has no grader |
| INT-PLN-017 | task_intent_validation_v0.yaml | runner_unsupported | turns must be a non-empty list; expected must be an object for workflow runner; case has no grader |
| INT-PLN-018 | task_intent_validation_v0.yaml | runner_unsupported | turns must be a non-empty list; expected must be an object for workflow runner; case has no grader |
| INT-PLN-019 | task_intent_validation_v0.yaml | runner_unsupported | turns must be a non-empty list; expected must be an object for workflow runner; case has no grader |
| INT-PLN-020 | task_intent_validation_v0.yaml | runner_unsupported | turns must be a non-empty list; expected must be an object for workflow runner; case has no grader |
| INT-PLN-021 | task_intent_validation_v0.yaml | runner_unsupported | turns must be a non-empty list; expected must be an object for workflow runner; case has no grader |
| INT-PLN-022 | task_intent_validation_v0.yaml | runner_unsupported | turns must be a non-empty list; expected must be an object for workflow runner; case has no grader |
| INT-PLN-023 | task_intent_validation_v0.yaml | runner_unsupported | turns must be a non-empty list; expected must be an object for workflow runner; case has no grader |
| INT-PLN-024 | task_intent_validation_v0.yaml | runner_unsupported | turns must be a non-empty list; expected must be an object for workflow runner; case has no grader |
| INT-PLN-025 | task_intent_validation_v0.yaml | runner_unsupported | turns must be a non-empty list; expected must be an object for workflow runner; case has no grader |
| INT-INF-001 | task_intent_validation_v0.yaml | runner_unsupported | turns must be a non-empty list; expected must be an object for workflow runner; case has no grader |
| INT-INF-002 | task_intent_validation_v0.yaml | runner_unsupported | turns must be a non-empty list; expected must be an object for workflow runner; case has no grader |
| INT-INF-003 | task_intent_validation_v0.yaml | runner_unsupported | turns must be a non-empty list; expected must be an object for workflow runner; case has no grader |
| INT-INF-004 | task_intent_validation_v0.yaml | runner_unsupported | turns must be a non-empty list; expected must be an object for workflow runner; case has no grader |
| INT-INF-005 | task_intent_validation_v0.yaml | runner_unsupported | turns must be a non-empty list; expected must be an object for workflow runner; case has no grader |
| INT-INF-006 | task_intent_validation_v0.yaml | runner_unsupported | turns must be a non-empty list; expected must be an object for workflow runner; case has no grader |
| INT-INF-007 | task_intent_validation_v0.yaml | runner_unsupported | turns must be a non-empty list; expected must be an object for workflow runner; case has no grader |
| INT-INF-008 | task_intent_validation_v0.yaml | runner_unsupported | turns must be a non-empty list; expected must be an object for workflow runner; case has no grader |
| INT-INF-009 | task_intent_validation_v0.yaml | runner_unsupported | turns must be a non-empty list; expected must be an object for workflow runner; case has no grader |
| INT-INF-010 | task_intent_validation_v0.yaml | runner_unsupported | turns must be a non-empty list; expected must be an object for workflow runner; case has no grader |
| INT-INF-011 | task_intent_validation_v0.yaml | runner_unsupported | turns must be a non-empty list; expected must be an object for workflow runner; case has no grader |
| INT-INF-012 | task_intent_validation_v0.yaml | runner_unsupported | turns must be a non-empty list; expected must be an object for workflow runner; case has no grader |
| INT-INF-013 | task_intent_validation_v0.yaml | runner_unsupported | turns must be a non-empty list; expected must be an object for workflow runner; case has no grader |
| INT-INF-014 | task_intent_validation_v0.yaml | runner_unsupported | turns must be a non-empty list; expected must be an object for workflow runner; case has no grader |
| INT-INF-015 | task_intent_validation_v0.yaml | runner_unsupported | turns must be a non-empty list; expected must be an object for workflow runner; case has no grader |
| INT-INF-016 | task_intent_validation_v0.yaml | runner_unsupported | turns must be a non-empty list; expected must be an object for workflow runner; case has no grader |
| INT-INF-017 | task_intent_validation_v0.yaml | runner_unsupported | turns must be a non-empty list; expected must be an object for workflow runner; case has no grader |
| INT-INF-018 | task_intent_validation_v0.yaml | runner_unsupported | turns must be a non-empty list; expected must be an object for workflow runner; case has no grader |
| INT-INF-019 | task_intent_validation_v0.yaml | runner_unsupported | turns must be a non-empty list; expected must be an object for workflow runner; case has no grader |
| INT-INF-020 | task_intent_validation_v0.yaml | runner_unsupported | turns must be a non-empty list; expected must be an object for workflow runner; case has no grader |
| INT-INF-021 | task_intent_validation_v0.yaml | runner_unsupported | turns must be a non-empty list; expected must be an object for workflow runner; case has no grader |
| INT-INF-022 | task_intent_validation_v0.yaml | runner_unsupported | turns must be a non-empty list; expected must be an object for workflow runner; case has no grader |
| INT-INF-023 | task_intent_validation_v0.yaml | runner_unsupported | turns must be a non-empty list; expected must be an object for workflow runner; case has no grader |
| INT-INF-024 | task_intent_validation_v0.yaml | runner_unsupported | turns must be a non-empty list; expected must be an object for workflow runner; case has no grader |
| INT-INF-025 | task_intent_validation_v0.yaml | runner_unsupported | turns must be a non-empty list; expected must be an object for workflow runner; case has no grader |
| ROUTE-B01 | task_routing_boundary_v0.yaml | runner_unsupported | turns must be a non-empty list; expected must be an object for workflow runner; case has no grader |
| ROUTE-B02 | task_routing_boundary_v0.yaml | runner_unsupported | turns must be a non-empty list; expected must be an object for workflow runner; case has no grader |
| ROUTE-B03 | task_routing_boundary_v0.yaml | runner_unsupported | turns must be a non-empty list; expected must be an object for workflow runner; case has no grader |
| ROUTE-B04 | task_routing_boundary_v0.yaml | runner_unsupported | turns must be a non-empty list; expected must be an object for workflow runner; case has no grader |
| ROUTE-B05 | task_routing_boundary_v0.yaml | runner_unsupported | turns must be a non-empty list; expected must be an object for workflow runner; case has no grader |
| ROUTE-B06 | task_routing_boundary_v0.yaml | runner_unsupported | turns must be a non-empty list; expected must be an object for workflow runner; case has no grader |
| ROUTE-B07 | task_routing_boundary_v0.yaml | runner_unsupported | turns must be a non-empty list; expected must be an object for workflow runner; case has no grader |
| ROUTE-B08 | task_routing_boundary_v0.yaml | runner_unsupported | turns must be a non-empty list; expected must be an object for workflow runner; case has no grader |
| ROUTE-B09 | task_routing_boundary_v0.yaml | runner_unsupported | turns must be a non-empty list; expected must be an object for workflow runner; case has no grader |
| MOD-001 | teacher_acceptance_v0.yaml | ambiguous_label | unqualified 第N题 addressing is not labelled needs_clarification |
| PENDING-001 | teacher_acceptance_v0.yaml | fixture_mismatch | expected.status=None is not legal |
| GEN-01 | teacher_agent_regression.yaml | fixture_mismatch | expected.status=None is not legal; grader 'semantic' is skipped by runner |
| GEN-02 | teacher_agent_regression.yaml | fixture_mismatch | expected.status=None is not legal |
| CLA-01 | teacher_agent_regression.yaml | grader_unsupported | grader 'semantic' is skipped by runner |
| CLA-02 | teacher_agent_regression.yaml | fixture_mismatch | expected.status=None is not legal |
| MOD-01 | teacher_agent_regression.yaml | fixture_mismatch | expected.status=None is not legal |
| MOD-02 | teacher_agent_regression.yaml | fixture_mismatch | expected.status=None is not legal |
| MOD-03 | teacher_agent_regression.yaml | fixture_mismatch | expected.status=None is not legal |
| MOD-04 | teacher_agent_regression.yaml | runner_unsupported | expected must be an object for workflow runner |
| MOD-05 | teacher_agent_regression.yaml | runner_unsupported | turns must be a non-empty list; expected.status=None is not legal |
| SCORE-01 | teacher_agent_regression.yaml | fixture_mismatch | expected.status=None is not legal |
| SCORE-02 | teacher_agent_regression.yaml | fixture_mismatch | expected.status=None is not legal |
| DEL-01 | teacher_agent_regression.yaml | fixture_mismatch | expected.status=None is not legal; grader 'paper_state' is skipped by runner; unqualified 第N题 addressing is not labelled needs_clarification |
| DEL-02 | teacher_agent_regression.yaml | fixture_mismatch | expected.status=None is not legal; grader 'paper_state' is skipped by runner; unqualified 第N题 addressing is not labelled needs_clarification |
| REP-01 | teacher_agent_regression.yaml | fixture_mismatch | expected.status=None is not legal; grader 'tool_rule' is skipped by runner; unqualified 第N题 addressing is not labelled needs_clarification |
| REP-02 | teacher_agent_regression.yaml | fixture_mismatch | expected.status=None is not legal; grader 'context' is skipped by runner; unqualified 第N题 addressing is not labelled needs_clarification |
| REP-03 | teacher_agent_regression.yaml | fixture_mismatch | expected.status=None is not legal; grader 'paper_state' is skipped by runner |
| REP-04 | teacher_agent_regression.yaml | fixture_mismatch | expected.status=None is not legal; grader 'paper_state' is skipped by runner |
| QUERY-01 | teacher_agent_regression.yaml | fixture_mismatch | expected.status=None is not legal; grader 'semantic' is skipped by runner; unqualified 第N题 addressing is not labelled needs_clarification |
| UNSUP-01 | teacher_agent_regression.yaml | fixture_mismatch | expected.status=None is not legal; grader 'semantic' is skipped by runner |
| UNSUP-02 | teacher_agent_regression.yaml | runner_unsupported | turns must be a non-empty list; expected.status=None is not legal; grader 'schema' is skipped by runner |
| CHAT-01 | teacher_agent_regression.yaml | runner_unsupported | expected must be an object for workflow runner; grader 'tool_rule' is skipped by runner; grader 'semantic' is skipped by runner |
| MEM-01 | teacher_agent_regression.yaml | fixture_mismatch | expected.status=None is not legal |
| STATE-01 | teacher_agent_regression.yaml | fixture_mismatch | expected.status=None is not legal |
| STATE-02 | teacher_agent_regression.yaml | grader_unsupported | grader 'semantic' is skipped by runner |
| KP-01 | teacher_agent_regression.yaml | grader_unsupported | grader 'semantic' is skipped by runner |
| KP-02 | teacher_agent_regression.yaml | runner_unsupported | unsupported setup keys: ['knowledge_status'] |
| CONF-01 | teacher_agent_regression.yaml | fixture_mismatch | expected.status=None is not legal; grader 'paper_state' is skipped by runner |
| ERR-01 | teacher_agent_regression.yaml | grader_unsupported | grader 'error' is skipped by runner; grader 'trace' is skipped by runner |
| TRACE-01 | teacher_agent_regression.yaml | fixture_mismatch | expected.status=None is not legal; grader 'trace' is skipped by runner |
| WF-001 | teacher_workflow_candidate_v0.yaml | ambiguous_label | manual review status is 'pending' |
| WF-002 | teacher_workflow_candidate_v0.yaml | ambiguous_label | manual review status is 'pending' |
| WF-003 | teacher_workflow_candidate_v0.yaml | ambiguous_label | manual review status is 'pending' |
| WF-004 | teacher_workflow_candidate_v0.yaml | fixture_mismatch | expected.status=None is not legal; manual review status is 'pending' |
| WF-005 | teacher_workflow_candidate_v0.yaml | fixture_mismatch | expected.status=None is not legal; manual review status is 'pending' |
| WF-006 | teacher_workflow_candidate_v0.yaml | ambiguous_label | manual review status is 'pending' |
| WF-007 | teacher_workflow_candidate_v0.yaml | ambiguous_label | manual review status is 'pending' |
| WF-008 | teacher_workflow_candidate_v0.yaml | ambiguous_label | manual review status is 'pending' |
| WF-009 | teacher_workflow_candidate_v0.yaml | ambiguous_label | manual review status is 'pending' |
| WF-010 | teacher_workflow_candidate_v0.yaml | fixture_mismatch | expected.status=None is not legal; manual review status is 'pending' |
| WF-011 | teacher_workflow_candidate_v0.yaml | ambiguous_label | manual review status is 'pending' |
| WF-012 | teacher_workflow_candidate_v0.yaml | fixture_mismatch | expected.status=None is not legal; manual review status is 'pending' |
| WF-013 | teacher_workflow_candidate_v0.yaml | ambiguous_label | manual review status is 'pending' |
| WF-014 | teacher_workflow_candidate_v0.yaml | ambiguous_label | manual review status is 'pending' |
| WF-015 | teacher_workflow_candidate_v0.yaml | ambiguous_label | manual review status is 'pending' |
| WF-016 | teacher_workflow_candidate_v0.yaml | ambiguous_label | manual review status is 'pending' |
| WF-017 | teacher_workflow_candidate_v0.yaml | ambiguous_label | manual review status is 'pending' |
| WF-018 | teacher_workflow_candidate_v0.yaml | ambiguous_label | manual review status is 'pending' |
| WF-019 | teacher_workflow_candidate_v0.yaml | ambiguous_label | manual review status is 'pending' |
| WF-020 | teacher_workflow_candidate_v0.yaml | ambiguous_label | manual review status is 'pending' |
| WF-021 | teacher_workflow_candidate_v0.yaml | ambiguous_label | manual review status is 'pending' |
| WF-022 | teacher_workflow_candidate_v0.yaml | ambiguous_label | manual review status is 'pending' |
| WF-023 | teacher_workflow_candidate_v0.yaml | ambiguous_label | manual review status is 'pending' |
| WF-024 | teacher_workflow_candidate_v0.yaml | ambiguous_label | manual review status is 'pending' |
| WF-025 | teacher_workflow_candidate_v0.yaml | fixture_mismatch | expected.status=None is not legal; manual review status is 'pending' |
| WF-026 | teacher_workflow_candidate_v0.yaml | fixture_mismatch | expected.status=None is not legal; manual review status is 'pending'; unqualified 第N题 addressing is not labelled needs_clarification |
| WF-027 | teacher_workflow_candidate_v0.yaml | fixture_mismatch | expected.status=None is not legal; manual review status is 'pending'; unqualified 第N题 addressing is not labelled needs_clarification |
| WF-028 | teacher_workflow_candidate_v0.yaml | fixture_mismatch | expected.status=None is not legal; manual review status is 'pending'; unqualified 第N题 addressing is not labelled needs_clarification |
| WF-029 | teacher_workflow_candidate_v0.yaml | ambiguous_label | manual review status is 'pending' |
| WF-030 | teacher_workflow_candidate_v0.yaml | ambiguous_label | manual review status is 'pending' |
| WF-031 | teacher_workflow_candidate_v0.yaml | ambiguous_label | manual review status is 'pending' |
| WF-032 | teacher_workflow_candidate_v0.yaml | ambiguous_label | manual review status is 'pending' |
| WF-033 | teacher_workflow_candidate_v0.yaml | ambiguous_label | manual review status is 'pending' |
| WF-034 | teacher_workflow_candidate_v0.yaml | fixture_mismatch | expected.status=None is not legal; manual review status is 'pending' |
| WF-035 | teacher_workflow_candidate_v0.yaml | ambiguous_label | manual review status is 'pending' |
| WF-036 | teacher_workflow_candidate_v0.yaml | fixture_mismatch | expected.status=None is not legal; manual review status is 'pending' |
| WF-037 | teacher_workflow_candidate_v0.yaml | ambiguous_label | manual review status is 'pending' |
| WF-038 | teacher_workflow_candidate_v0.yaml | ambiguous_label | manual review status is 'pending' |
| WF-039 | teacher_workflow_candidate_v0.yaml | ambiguous_label | manual review status is 'pending' |
| WF-040 | teacher_workflow_candidate_v0.yaml | ambiguous_label | manual review status is 'pending' |
| WF-041 | teacher_workflow_candidate_v0.yaml | ambiguous_label | manual review status is 'pending' |
| WF-042 | teacher_workflow_candidate_v0.yaml | fixture_mismatch | expected.status=None is not legal; manual review status is 'pending' |
| WF-043 | teacher_workflow_candidate_v0.yaml | ambiguous_label | manual review status is 'pending' |
| WF-044 | teacher_workflow_candidate_v0.yaml | ambiguous_label | manual review status is 'pending' |
| WF-045 | teacher_workflow_candidate_v0.yaml | ambiguous_label | manual review status is 'pending' |
| WF-046 | teacher_workflow_candidate_v0.yaml | ambiguous_label | manual review status is 'pending' |
| WF-047 | teacher_workflow_candidate_v0.yaml | ambiguous_label | manual review status is 'pending' |
| WF-048 | teacher_workflow_candidate_v0.yaml | ambiguous_label | manual review status is 'pending' |
| WF-049 | teacher_workflow_candidate_v0.yaml | ambiguous_label | manual review status is 'pending' |
| WF-050 | teacher_workflow_candidate_v0.yaml | ambiguous_label | manual review status is 'pending' |
| WF-051 | teacher_workflow_candidate_v0.yaml | ambiguous_label | manual review status is 'pending' |
| WF-052 | teacher_workflow_candidate_v0.yaml | ambiguous_label | manual review status is 'pending' |
| WF-053 | teacher_workflow_candidate_v0.yaml | ambiguous_label | manual review status is 'pending' |
| WF-054 | teacher_workflow_candidate_v0.yaml | ambiguous_label | manual review status is 'pending' |
| WF-055 | teacher_workflow_candidate_v0.yaml | ambiguous_label | manual review status is 'pending' |
| WF-056 | teacher_workflow_candidate_v0.yaml | fixture_mismatch | expected.status=None is not legal; manual review status is 'pending' |
| WF-057 | teacher_workflow_candidate_v0.yaml | fixture_mismatch | expected.status=None is not legal; manual review status is 'pending' |
| WF-058 | teacher_workflow_candidate_v0.yaml | ambiguous_label | manual review status is 'pending' |
| WF-059 | teacher_workflow_candidate_v0.yaml | ambiguous_label | manual review status is 'pending' |
| WF-060 | teacher_workflow_candidate_v0.yaml | ambiguous_label | manual review status is 'pending' |

## Scope limitation

This is a static contract audit. Semantic labels, real fixture supply, and content correctness still require human review before Holdout freeze.
