# Fixture contract

Fixtures are reviewed inputs, not generated answers. A case references them by relative path or stable ID.

- `curriculum/`: textbook edition, chapter/topic hierarchy, and allowed prerequisites.
- `question_bank/`: immutable item IDs with chapter, topics, type, difficulty, score, and similarity-group metadata.
- `student_profiles/`: observed mastery, errors, completed items, and observation dates; unknown fields stay unknown.
- `papers/`: existing paper/item IDs and scores used by modification or exclusion cases.

Required rules:

1. IDs are unique and stable; cases never depend on database row order.
2. Every claim used by a structural assertion must be machine-readable in the fixture.
3. Missing supply remains missing: runners must not fabricate items or silently widen scope/type/difficulty.
4. Fixture revisions require a new fixture version and human re-review of affected cases.
5. Candidate fixtures and cases must not be treated as blind holdout data until split/freeze metadata and hashes are recorded.
