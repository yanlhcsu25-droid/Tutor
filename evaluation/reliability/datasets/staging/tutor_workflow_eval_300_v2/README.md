# Tutor Workflow Eval 300 v2 — staging only

Canonical candidate imported without modifying the source YAML.

- Bundle SHA256: `56dde4c6f2f45d08b74d83d274425a16ee8499b737c0d2525410c2ba94e8b01e`
- YAML SHA256: `e2249d95960769d5ba34e93d558dd14ff9ccaf28b5a660954fa81e200c74a7ae`
- Status: `staging`; not a benchmark and not a frozen holdout.

Known blockers:

- 40 exact scenario-signature groups cross Dev/Holdout (105 affected cases).
- 10 cases violate the current paper-addressing contract.
- Cancellation cases do not require `discard_pending_plan`.
- 213 cases assert only final status, without a business-fact oracle.
- All 300 cases still require human review.

The bundle's `generate_tutor_workflow_eval_300_v2.py` was not imported because it is a provenance note, not an executable generator. The YAML above is the source of truth.
