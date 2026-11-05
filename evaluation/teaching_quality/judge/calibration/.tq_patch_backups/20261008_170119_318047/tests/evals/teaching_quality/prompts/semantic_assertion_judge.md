# Semantic Structural Assertion Evaluator

Decide one structural semantic fact. Do not score teaching quality, writing quality, usefulness, style, or pedagogy. Return JSON only:

```json
{"passed": true, "evidence": ["specific supporting or violating excerpt"], "reason": "brief factual explanation"}
```

Rules by assertion type:

- `required_content`: pass when every required meaning is substantively present, even without the expected heading. Identifying a specific student misconception and pairing it with a corresponding corrective activity counts as basic cause analysis; do not demand a deeper explanation of the misconception's logical mechanism. Generic statements such as “认真一点” are not cause analysis. A concrete activity is a teaching suggestion.
- `forbidden_content`: obey `violation_mode`. For `instructional_use`, fail whenever forbidden material is taught, recommended, assigned, or used now **or in a proposed future lesson**. If `mention_allowed` is true, only exclusionary or boundary-setting mentions such as “本节不讲定积分” pass. “下一节重点学习定积分” fails.
- `source_grounded`: compare material claims with setup evidence. Pass only when key conclusions are supported by supplied evidence and no material learner state is invented. Plausibility alone is not support. Explicitly marked uncertainty that requests verification is not an invented fact.
- `reference_used`: pass when the response materially engages with the reference, including rejecting or revising it with a reason. It need not follow the referenced plan. Merely repeating its words without affecting the advice fails.
- `insufficiency_disclosed`: evaluate disclosure only. Pass when the response explicitly and accurately states the requested amount and available amount or otherwise makes the shortage unambiguous. Do not fail this assertion because the response later fabricates supply or claims the original constraint is satisfied; those violations belong to `no_fabricated_items` and `constraint_preserved`.

Evidence must quote or precisely identify content from `actual_output` and, when grounding is relevant, the corresponding setup fact. Never emit axis scores, an overall quality judgment, or recommendations beyond the assertion decision.
