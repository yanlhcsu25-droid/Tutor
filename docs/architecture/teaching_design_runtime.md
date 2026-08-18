# TeachingDesign Runtime Integration — T1.1

## Scope

T1.1 connects the already-tested TeachingDesign domain to the existing
Teacher Agent runtime without moving business rules back into `agent.py`.

```text
Teacher message
    ↓
Agent Runtime
    ↓
TeachingDesign Tool Adapter
    ↓
TeachingDesignService
    ↓
TeachingDesignRepository
    ↓
DB
```

## Agent responsibilities

The Agent is allowed to:

- detect that a teacher is expressing a high-level teaching task;
- form semantic TeachingDesign content;
- select create/revise/read/confirm/history tools;
- write a semantic `change_reason`;
- explain Tool Observations.

The Agent is not allowed to:

- write TeachingDesign ORM rows directly;
- choose version numbers;
- overwrite an older version;
- confirm its own newly created/revised design in the same teacher turn;
- use chat history as the authoritative historical design store.

## Tool adapter boundary

`agent/tool_adapters/teaching_design.py` is intentionally thin.

It:

- validates structured Tool input;
- injects trusted runtime provenance (`run_id`, current user message,
  conversation id, owner key);
- calls `TeachingDesignService`;
- converts domain results to machine-readable Tool observations.

It does not implement versioning rules itself.

## Traceability

Every Teacher Agent turn already has a durable `run_id`.

T1.1 passes that run id through the Tool adapter into the TeachingDesign
domain, producing:

```text
TeacherAgentRunTrace.run_id
    ↓
TeachingDesignVersion.created_by_run_id
TeachingDesignVersion.confirmed_by_run_id
```

Runtime trace state additionally contains:

```text
working_memory
active_teaching_design
```

These are separate state categories and should not be merged.

## Confirmation boundary

A successful create or revise immediately narrows the remaining tool surface
for that teacher turn to zero:

```text
create/revise
    ↓
Tool Observation
    ↓
natural-language summary
    ↓
STOP
```

The next teacher turn may call `confirm_teaching_design`.

This is a runtime guard in addition to prompt/Skill guidance.

## Migration strategy

T1.1 is additive.

The legacy `preview_generation_plan` entry remains available while the new
TeachingDesign → Generation bridge is incomplete and unevaluated.

Do not remove the legacy path until:

1. confirmed TeachingDesign can project all enforced constraints;
2. generation from confirmed design is end-to-end tested;
3. regression eval proves the new default path does not break direct
   generation behavior.

This avoids replacing a known-working path with a partially implemented one.
