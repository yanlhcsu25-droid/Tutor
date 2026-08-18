# TeachingDesign Constraint Execution — T2

## Why this version exists

T1.2 was intentionally conservative: many TeachingDesign fields were treated as
unsupported and blocked Paper generation.

Repository inspection showed that the current system already has the
deterministic data and Solver primitives needed for more:

- `QuestionProfile.estimated_time_min`
- `QuestionProfile.reasoning_depth`
- `QuestionProfile.calculation_load`
- `QuestionProfile.knowledge_depth`
- `QuestionProfile.comprehensive_level`
- `PaperBlueprint.knowledge_quotas`
- `PaperBlueprint.soft_knowledge_preferences`
- CP-SAT question selection
- persisted `GenerationConstraints` metadata

T2 connects those existing capabilities instead of giving the LLM more direct
control.

## Constraint classes

```text
TeachingDesign
    ↓
generation_adapter
    ├── HARD
    ├── BOUNDED
    ├── SOFT
    ├── ADVISORY
    └── UNSUPPORTED
```

### Hard

Failure makes the paper infeasible:

- scope
- total score
- required knowledge coverage

Each `knowledge_plan` item with `role=required` becomes a minimum knowledge
quota of one selected question.

### Bounded

Must stay inside a deterministic range:

- difficulty band
- estimated duration

Duration uses:

```text
target ± max(5 minutes, 10% of target)
```

The exact range is compiled and then enforced by CP-SAT using approved
`QuestionProfile.estimated_time_min`.

### Soft

Used in the CP-SAT objective, but not a feasibility blocker:

- required/optional knowledge priority
- preferred difficulty
- ability emphasis

Ability mapping:

```text
concept_understanding -> knowledge_depth
calculation           -> calculation_load
reasoning             -> reasoning_depth
application           -> comprehensive_level
```

The Agent does not calculate profile values. It only provides semantic weights;
approved QuestionProfile rows remain the source of truth.

### Advisory

Preserved and surfaced but not faked as Solver guarantees:

- prerequisite knowledge
- free-text coverage strategy
- question-design ideas
- assessment notes

These can later guide lecture generation or be upgraded to structured
constraints.

### Unsupported

Only genuinely unrepresentable structured semantics block generation. An
unknown ability dimension is one example.

## Selector architecture

T2 extends the existing `papers/selector.py`.

It does not create `teaching_design_selector.py` or a second CP-SAT engine.

For profile-sensitive generation, candidate questions must have an approved
latest QuestionProfile.

```text
Question
 + QuestionProfile
 + KnowledgeLink
        ↓
 existing candidate query
        ↓
 CP-SAT
```

Hard constraints are added to the model before optimization. Soft objective
coefficients are then combined into one CP-SAT objective.

## Validation

The exact `GenerationConstraints` are already persisted inside Blueprint
`_agent_metadata`.

T2 reuses that metadata during `validate_paper()` so duration remains
revalidated after later Paper versions/operations.

This matters because:

```text
Paper v1 meets 90-minute design
    ↓ replace question
Paper v2
    ↓
validate again against the same persisted generation constraints
```

A later Paper version cannot silently drift outside the duration contract.

## Source of truth

- TeachingDesign version: pedagogical business truth.
- QuestionProfile approved version: question capability/time evidence.
- GenerationConstraints persisted metadata: compiled execution contract.
- Paper/PaperItem: generated artifact state.
- ValidationReport: post-generation compliance result.
- Agent trace: why/when Tools were called.

Conversation history remains non-authoritative.

## What T2 intentionally does not do

T2 does not yet:

- infer question-bank supply before designing;
- dynamically replan after Solver failure;
- generate lecture content;
- convert arbitrary coverage prose into chapter weights;
- add Multi-Agent.

Those belong after the deterministic execution contract is proven.
