# TeachingDesign Domain Boundary

## Purpose

`TeachingDesign` is the durable business source of truth for:

- why a lesson should be taught in a certain way;
- what knowledge should be covered;
- how the lecture should be structured;
- what the assessment is intended to measure.

It is **not** an Agent scratchpad and it is **not** a PaperBlueprint.

## Dependency direction

```text
Teacher / API
    ↓
Agent Runtime
    ↓
Tool Adapter / Application Orchestration
    ↓
TeachingDesignService
    ↓
TeachingDesignRepository
    ↓
Database

confirmed TeachingDesign
    ↓
generation_adapter
    ↓
existing GenerationService / Paper / Solver
```

Forbidden dependencies:

```text
TeachingDesign domain → agent.py
TeachingDesign persistence → model provider
TeachingDesign service → chat history
```

## Source of truth

- Conversation history: conversational context only.
- Agent working memory: temporary execution context only.
- TeachingDesignVersionRecord: teaching-design business truth.
- Paper/PaperItem: generated assessment truth.
- TeacherAgentRunTrace: execution observability/audit trail.

## Version semantics

Every content change creates a new immutable row.

```text
v1 confirmed
   ↓ teacher requests change
v2 awaiting_confirmation

active = v2
effective_confirmed = v1
```

Only after explicit confirmation:

```text
v1 superseded
v2 confirmed

active = v2
effective_confirmed = v2
```

Old content is never overwritten.

## Traceability

Each version stores:

- `parent_version_id`
- `created_by_run_id`
- `source_user_message`
- `change_reason`
- `confirmed_by_run_id`
- `confirmed_at`
- `superseded_by_version_id`
- `superseded_at`

Each Paper stores only:

- `teaching_design_version_id`

It does **not** copy the entire TeachingDesign JSON.

This allows:

```text
Paper
→ TeachingDesign version
→ parent version
→ teacher message
→ Agent run
```

## Active vs confirmed

`active` means the version currently being discussed in a conversation.

`confirmed` means the version allowed to drive generation.

They are intentionally independent.

## Long-term recall

Past designs are retrieved from persisted TeachingDesign records, not inferred
from chat history. The deterministic repository returns candidates; semantic
resolution belongs to the Agent layer.

## Current T1 capability boundary

T1 implements:

- domain schema;
- persistence;
- linear versioning;
- active pointer;
- confirmation;
- long-term recall candidates;
- run-level provenance;
- confirmed-design generation projection;
- Paper provenance link.

T1 intentionally does not implement:

- autonomous question-bank inspection;
- planning/replanning loop;
- solver feedback recovery;
- knowledge self-healing;
- lecture content generation.

## Generation projection gaps

The current generation contract supports only part of TeachingDesign.
The adapter maps supported constraints and explicitly returns unsupported ones.

Do not silently pretend unsupported teaching constraints are enforced.
