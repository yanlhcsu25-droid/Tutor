# Holdout Freeze Review v1

## Decision

**Rejected — do not freeze or run Holdout.** The candidate remains staging-only.

## Configuration preflight

The proposed system configuration is locked in `evaluations/holdout_preflight_v1.json`:

- Provider/model: `siliconflow` / `Qwen/Qwen3.5-35B-A3B`
- Adapter/protocol: `BailianChatBackend` / `openai_compatible`
- Runtime, runner, grader, fixture, and model-configuration SHA256 values are recorded there.

This is a reproducibility lock for review, not a Holdout freeze authorization.

## Deterministic audit

`evaluations/tutor_workflow_eval_300_v2_contract_audit.json` confirms:

- 300 cases; split count is 200 Dev / 100 Holdout Candidate.
- All 300 cases have `review.status: pending`; none has completed human semantic review.
- 10 cases use unqualified `第 N 题` addressing but do not require `needs_clarification`.
- 5 confirmation cases expect completion while asserting `paper_unchanged: true`.

## Existing candidate blockers

The staging bundle provenance (`tests/evals/staging/tutor_workflow_eval_300_v2/README.md`) additionally records:

- 40 exact scenario-signature groups cross Dev/Holdout, affecting 105 cases.
- Cancellation contracts do not require `discard_pending_plan`.
- 213 cases have only a final-status assertion, not a business-fact oracle.

These are benchmark-validity blockers, not runtime failures. No Holdout prompt, fixture, grader, runtime, or model execution was performed.

## Required before a Holdout freeze

1. Remove cross-split scenario leakage and regenerate split provenance.
2. Repair the addressing, cancellation, and confirmation contracts.
3. Add business-state/oracle assertions for status-only cases.
4. Complete and record per-case human semantic review.
5. Create a new immutable Holdout manifest with dataset, fixture, grader, model, and clean code SHA locks.
6. Obtain explicit authorization before the first Holdout execution.
