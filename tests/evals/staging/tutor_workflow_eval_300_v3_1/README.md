# Tutor Workflow Eval 300 v3.1 — staging only

A reproducible contract-remediated derivative of immutable V3.

- Source: `../tutor_workflow_eval_300_v3/tutor_workflow_eval_300_v3.yaml`
- Source YAML SHA256: `25ee3fc094c494854334bae8a199accabf031878244b252d79570e9a8dcc0e8a`
- V3.1 YAML SHA256: `2f3c9c48936a8208a53f850194d15118be9601e380e1249726a42cc140e394ad`
- Split: 200 Dev / 69 Holdout Candidate / 31 Duplicate Regression
- Cross-split `(category, subtype)` families: 0
- Status: `staging`; not frozen and not authorized for Holdout execution.

## Deterministic remediation

- 10 unqualified `第 N 题` cases now fail closed with `needs_clarification`, unchanged Paper, and no guessed read/preview/mutation.
- 21 cancellation cases now require `discard_pending_plan`, assert cleared Pending state, and retain the no-confirm/no-mutation boundary.
- All 213 previously status-only cases now include a business-state, artifact, persistence, or no-side-effect oracle. The generator reports 194 direct oracle additions because 19 cases receive their oracle during addressing/cancel remediation first.
- The previous 5 “confirmation conflict” findings were audit false positives: cancellation text such as `待确认方案取消` contains the token `确认`. The auditor now excludes explicit cancellation intent before applying the confirmation invariant.
- EVAL-265 now seeds a 10-question current Paper and requires grounded `read_paper` output with no write-side Tool; its human review is recorded as approved.
- EVAL-267 now seeds a Pending Generation and asserts that “不要现在生成” preserves it without prepare, confirm, discard, or any write-side Tool; its human review is recorded as approved.
- EVAL-279 keeps its two-turn prepare/confirm flow and now requires the persisted Paper to retain 6 questions / 60 points; its human review is recorded as approved.
- All 31 confirmed `duplicate_of` decisions are applied to the Dataset as `duplicate_regression`; none counts toward the main Holdout denominator.
- The generated Holdout queue contains 69 independent cases, all recorded as human approved.
- No replacement/template cases were added to restore an arbitrary count of 100.

The deterministic audit has no remaining addressing, cancellation, status-only, review, or coarse family-leakage finding for the 69-case Holdout. The original 100-case split check is intentionally false after deduplication. The 200 Dev cases remain `pending` and are outside this Holdout freeze decision.
