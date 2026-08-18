# T4-1 Deterministic Generation Failure Foundation

Baseline:

```text
repo   yanlhcsu25-droid/calculus-teacher-agent-dev
tag    teacher-agent-t3
commit 8472382bcd1a6f5a074b1705705037299c457049
```

T4-1 fixes only proven P0 failure boundaries. It does not add an LLM replanner.

## Contract 1 — Validation failure is not generation success

If `validate_paper()` returns `passed=false`, generation returns:

```text
ok = false
blocking_errors contains paper_validation_failed
validation_status = failed
validation_report preserved
```

A failed persisted Paper remains inspectable for deterministic diagnosis.

## Contract 2 — Failed artifacts retain provenance

Any Paper actually persisted from a TeachingDesign execution keeps
`teaching_design_version_id`, including validation-failed artifacts.

## Contract 3 — Persistence owns only a SAVEPOINT

`create_paper_draft()` must not rollback the caller's Teacher-turn transaction.
A paper persistence exception rolls back only its nested transaction.

## Contract 4 — Execution failure is not automatically a design revision

Until deterministic diagnosis exists:

```text
generation failure
→ requires_design_revision = false
```

## Deferred — global curriculum resolver unification

The first T4-1 attempt changed shared scope helpers to active-textbook-only
semantics. Full regression testing proved that this breaks established legacy
taxonomy and local-DB integration contracts.

That broad refactor is reverted. Environment inspection remains scoped to the
active textbook. Generic generation scope resolution keeps its existing
compatibility behavior.

A future unification must be introduced behind a dedicated domain boundary,
not by globally narrowing shared legacy helpers.
