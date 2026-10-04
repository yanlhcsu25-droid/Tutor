# Tutor Workflow Eval 300 v3.1 — staging only

A reproducible contract-remediated derivative of immutable V3.

- Source: `../tutor_workflow_eval_300_v3/tutor_workflow_eval_300_v3.yaml`
- Source YAML SHA256: `25ee3fc094c494854334bae8a199accabf031878244b252d79570e9a8dcc0e8a`
- V3.1 YAML SHA256: `0c97d5cd975d2931b1f68eb9defa5da5b5afa4bf8862ec3cfd98a0e0f728a230`
- Split: 200 Dev / 100 Holdout Candidate
- Cross-split `(category, subtype)` families: 0
- Status: `staging`; not frozen and not authorized for Holdout execution.

## Deterministic remediation

- 10 unqualified `第 N 题` cases now fail closed with `needs_clarification`, unchanged Paper, and no guessed read/preview/mutation.
- 21 cancellation cases now require `discard_pending_plan`, assert cleared Pending state, and retain the no-confirm/no-mutation boundary.
- All 213 previously status-only cases now include a business-state, artifact, persistence, or no-side-effect oracle. The generator reports 194 direct oracle additions because 19 cases receive their oracle during addressing/cancel remediation first.
- The previous 5 “confirmation conflict” findings were audit false positives: cancellation text such as `待确认方案取消` contains the token `确认`. The auditor now excludes explicit cancellation intent before applying the confirmation invariant.
- EVAL-265 now seeds a 10-question current Paper and requires grounded `read_paper` output with no write-side Tool; its human review is recorded as approved.
- The 100-case Holdout review queue is generated at `holdout_review_queue_v3_1.csv`; EVAL-265 is approved and the other 99 remain pending.

The deterministic audit has no remaining addressing, cancellation, status-only, split-count, or coarse family-leakage finding. EVAL-265 is valid; the other 299 cases remain blocked on recorded human semantic review.
