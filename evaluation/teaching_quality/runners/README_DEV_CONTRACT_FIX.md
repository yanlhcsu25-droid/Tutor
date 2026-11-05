# Dev Eval contract repair — 2026-10-09

This change makes frozen Dev assertions observe **evidence actually returned by the runtime** without modifying frozen cases, rubric thresholds, or model prompts. No automatic calls to a model are required to audit an existing report.

- `teaching_design.scope_names` is projected into chapter prefixes for chapter-scope assertions only; topic-level validation remains separate.
- `teaching_design.lesson_count` is used for an `exact_count(teaching_design.lessons)` assertion only. No fake lesson objects are created.
- Explicit `knowledge_plan.name` and `teaching_sequence.knowledge_points` can be used as topic evidence. Plain lexical equivalents (e.g., `函数的极限` / `函数极限`) are annotated. **Examples are not fabricated** from `example_strategy`.
- Explicit numbered-minute blocks can provide duration evidence. Any unmatched timing mention (apart from a stated total-time budget) prevents a guess.
- `waiting_confirmation` for a **consultation** response that created a real `teaching_design` can be evaluated diagnostically; requests for actual teacher confirmation are not automatically confirmed.
- If a required assertion cannot be observed, the score remains **incomplete** rather than pretending it passed.
- Paper cases still remain blocked when controlled immutable question fixtures are unavailable. Copying a nonempty application SQLite database is **not** equivalent to seeding frozen case fixtures.
- Offline audits always report `official_accuracy: null`. They do not touch Blind cases, invoke Tutor/Judge, overwrite old reports, or modify frozen fixtures.

After applying the patch, run offline tests and audit the **already saved** run:

```bash
uv run python -m pytest -q tests/evaluation/teaching_quality/test_dev_contract.py
uv run python evaluation/teaching_quality/runners/audit_saved_dev.py --input evaluation/artifacts/reports/teaching_quality/runs/dev_eval_runs/dev-20261009T015413Z-555910/report.json
```

The second command emits `offline-contract-audit.json` next to the original report. It is diagnostic and uses cached Judge observations/ratings where available. No fresh model call is performed.

Remaining work before an official 24-case score: reviewed immutable question-bank fixtures and setup seeding for paper cases, alignment of response/artifact contracts for open consultation and teaching-design requests, investigation of three failed tool-argument validation cases, and a new controlled Dev run. Do **not** modify/run frozen Blind cases during these repairs.
