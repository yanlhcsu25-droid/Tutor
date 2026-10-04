# Tutor Workflow Eval 300 v3 — staging only

A reproducible V2 derivative. V2 remains unchanged.

- Source: `../tutor_workflow_eval_300_v2/tutor_workflow_eval_300_v2.yaml`
- Source YAML SHA256: `e2249d95960769d5ba34e93d558dd14ff9ccaf28b5a660954fa81e200c74a7ae`
- V3 YAML SHA256: `25ee3fc094c494854334bae8a199accabf031878244b252d79570e9a8dcc0e8a`
- Split: 200 Dev / 100 Holdout Candidate
- Split rule: each `(category, subtype)` family belongs to exactly one split; the deterministic audit reports zero cross-split families.
- Status: `staging`; not a benchmark and not a frozen Holdout.

## Remaining blockers

This V3 only remediates the coarse cross-split family leakage. It does **not** certify semantic independence or resolve V2's remaining blockers: pending human review, unqualified paper addressing, cancellation/confirmation contracts, and status-only business oracles.

Do not run Holdout until those blockers are remediated, reviewed, and frozen in a new manifest.
