# TeachingDesign Execution Bridge — T1.2

## Business contract

Teacher confirmation is the **only business confirmation** for a
TeachingDesign-driven generation flow.

```text
TeachingDesign awaiting_confirmation
    ↓ teacher confirms
TeachingDesign confirmed
    ↓ immediate deterministic execution
Generation projection
    ↓
GenerationService preview
    ↓ internal validation only
GenerationService confirm
    ↓
Paper
```

The intermediate GenerationService pending state is an internal compatibility
mechanism inherited from the old generation engine. It is **not** shown as a
second teacher confirmation step.

## Dependency direction

```text
TeachingDesign domain
    ↓ produces durable confirmed design

application/teaching_design_generation.py
    ↓ composes existing capabilities

TeachingDesign generation_adapter
    ↓
legacy GenerationService
    ↓
Paper / Solver
```

`calculus_agent.teaching_design` still does not import `calculus_agent.agent`.

The application layer is allowed to bridge the new domain to the legacy
generation service until that service is moved out of `agent/` in a separate,
eval-protected refactor.

## Unsupported constraints

A confirmed design may contain semantics that the current generation engine
cannot truthfully enforce.

Current examples include:

- assessment duration;
- ability weights;
- required knowledge-plan coverage;
- question-design ideas.

T1.2 treats these as an explicit execution capability gap:

```text
confirmed design
→ projection
→ unsupported constraints found
→ generation BLOCKED
→ design remains confirmed
→ propose a revised design / add deterministic capability
```

It does **not** silently drop those fields.

## Failure semantics

If projection succeeds but the existing generation engine reports infeasible
supply or another business failure:

- TeachingDesign stays confirmed;
- no automatic weakening occurs;
- internal pending generation is cleared;
- result says `requires_design_revision=true`;
- teacher must consent to a new TeachingDesign version if the goal changes.

## Provenance

`GenerationService` receives the exact:

```text
teaching_design_version_id
```

and Paper stores that exact version reference.

Later Paper revisions inherit the same reference through the existing Paper
version chain.
