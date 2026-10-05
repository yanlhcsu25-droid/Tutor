# Tutor Workflow Eval 300 v3.1 — staging only

A reproducible contract-remediated derivative of immutable V3.

- Source: `../tutor_workflow_eval_300_v3/tutor_workflow_eval_300_v3.yaml`
- Source YAML SHA256: `25ee3fc094c494854334bae8a199accabf031878244b252d79570e9a8dcc0e8a`
- V3.1 YAML SHA256: `a81c83c9c6b98d72c8806bbb6b1e63ec7043208969214fd3ef04410cc55241d9`
- Split: 200 Dev / 98 Holdout Candidate / 2 Duplicate Regression
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
- EVAL-289/EVAL-299 are marked `duplicate_of: EVAL-279`, moved to `duplicate_regression`, and excluded from the main Holdout denominator.
- The generated Holdout queue now contains 98 split members; EVAL-265/EVAL-267/EVAL-279 are approved and 95 remain pending.

The deterministic audit has no remaining addressing, cancellation, status-only, or coarse family-leakage finding. The original 100-case split check is intentionally false because two duplicates were removed; replacements are required only if a 100-case formal Holdout remains mandatory. The other 295 dataset cases remain blocked on recorded human semantic review.
