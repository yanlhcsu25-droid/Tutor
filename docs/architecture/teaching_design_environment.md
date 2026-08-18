# T3 Environment-Aware TeachingDesign

## Problem

Before T3, a high-level teacher request could become a TeachingDesign without
first reading the current curriculum or question-bank supply.

That made the semantic design structurally valid but environment-blind.

T3 changes the planning boundary to:

```text
Teacher Goal
    ↓
Agent
    ↓
Curriculum Observation
    ↓
Question-Bank Aggregate Observation
    ↓
optional bounded Drill-Down
    ↓
TeachingDesign
    ↓
teacher confirmation
    ↓
T2 Constraint Compiler / CP-SAT / Validation
```

No Multi-Agent architecture is introduced.

## Deterministic vs semantic responsibility

### Deterministic

Python owns:

- active curriculum resolution;
- chapter ownership;
- paper-candidate eligibility;
- question/type/profile/knowledge aggregation;
- inspection call budget;
- aggregate-before-drill-down guard;
- evidence fingerprinting;
- trusted evidence ledger;
- evidence injection into TeachingDesign;
- scope/evidence coverage validation.

### Semantic

The LLM owns:

- deciding whether aggregate evidence is sufficient;
- deciding which chapter merits drill-down;
- interpreting supply imbalance;
- balancing teacher goals against feasibility;
- writing the semantic TeachingDesign.

The LLM never calculates question counts itself.

## Shared supply contract

`questions/eligibility.py` is now the shared base predicate for:

```text
paper selector
question-bank inspection
```

This avoids a dangerous mismatch where an inspection says “80 available
questions” but the selector later rejects part of those rows.

Eligibility remains:

- `Question.review_status == approved`;
- active question;
- current knowledge match;
- source not in demo/test/dataset exclusions.

The selector still applies further execution-specific scope/profile filters.

## Aggregate first

Two read-only tools are exposed:

```text
inspect_curriculum
inspect_question_bank
```

Question-bank inspection supports:

```text
detail_level = aggregate
detail_level = chapter_detail
```

`chapter_detail` is rejected unless the same turn already has aggregate evidence
for that scope.

This is a deterministic anti-over-retrieval guard, not just a Prompt rule.

## Bounded autonomy

Each Teacher turn may make at most four environment-inspection calls.

This is intentionally a separate budget from the general Agent Tool round
limit. It prevents:

```text
inspect chapter 1
inspect chapter 2
inspect chapter 3
inspect every knowledge point
...
```

when aggregate data was already enough.

The Tool returns a recoverable budget-exhausted observation so the Agent can
finish from existing evidence instead of looping.

## Evidence provenance

Successful inspection returns a compact `EvidenceReference`:

```text
kind
ref_id = SHA256(normalized observation)
summary
observed_by_run_id
```

The full Tool Observation remains in:

```text
TeacherAgentRunTrace.tool_calls_json
```

The Agent execution context keeps a current-turn trusted evidence ledger.

When `create_teaching_design` runs:

```text
model semantic content
+
trusted current-run evidence
    ↓
Python validation
    ↓
TeachingDesign version
```

The model cannot fabricate:

```text
ref_id
observed_by_run_id
question-bank count evidence
```

`evidence_refs` are system-managed.

## Required evidence for a new design

A new TeachingDesign is accepted only when current-run evidence contains both:

```text
curriculum_scope
question_bank_aggregate
```

and both observations cover the same scope as the design.

This guard is implemented in the Tool adapter, not only in the Skill prompt.

## Revision behavior

A normal semantic revision such as:

```text
第三章再重点一点
```

does not force a redundant environment scan.

A scope-changing revision such as:

```text
再加入第四章
```

must first inspect the new scope.

If a same-scope revision does run new inspection, new EvidenceReferences are
merged into the new immutable design version.

## Evidence affects design, not teacher authority

Supply evidence may change an agent-generated implementation choice.

Example:

```text
teacher: 三章尽量均衡
observation: 第三章题量明显少
```

The Agent may avoid a mechanically equal hard quota and surface a feasibility
warning.

But:

```text
teacher: 第三章必须占50%
```

cannot be silently weakened by supply evidence. The Agent must explain the
risk, preserve the teacher goal in the proposed design, and wait for teacher
confirmation/revision.

## Trace chain

T3 can now reconstruct:

```text
Teacher message
→ Run R17
→ inspect_curriculum Observation
→ inspect_question_bank Observation
→ EvidenceReference hashes
→ TeachingDesign v1 created_by_run_id=R17
→ evidence_refs observed_by_run_id=R17
→ teacher confirmation Run R18
→ Paper
```

## What remains after T3

T3 does not yet implement autonomous recovery after Solver infeasibility.

The next meaningful capability boundary is:

```text
Solver / Validation failure
→ deterministic diagnosis
→ Agent proposes a TeachingDesign revision
→ teacher reconfirms if the business goal changes
```

It should be built only after T3 capability evals show that environment
observations genuinely improve design decisions.
