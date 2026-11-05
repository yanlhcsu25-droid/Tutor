# Workflow Eval Known Limitations — V3.2.1

The following five cases are the complete known-failure set from the immutable V3.2.1 Second Official result. They are not resolved and are not relabeled as passes.

| Case | Classification | Root Cause | Current Impact | Future Action |
| --- | --- | --- | --- | --- |
| EVAL-059 | Contract ambiguity | Runtime allows difficulty 3 as a `hard` fallback, while the oracle expects a stricter interpretation of “全部必须是高难度”. | Difficulty-shortage behavior cannot be judged consistently until the strict-hard contract is defined. | Decide and version the difficulty contract before a future targeted regression; do not rewrite the V3.2.1 oracle retrospectively. |
| EVAL-066 | Known Runtime limitation: false context dependency | “不要跨章节补题” causes a complete generation request to be treated as requiring a prior active target. | The system asks an unnecessary clarification and does not prepare the requested generation plan. | Add a family-level routing/guard regression in a later Runtime version. |
| EVAL-068 | Known Runtime limitation: routing | A difficulty-5 generation request is misrouted into TeachingDesign finalization. | The generation request ends as failed without calling `prepare_generation_plan`. | Correct generation intent routing in a later Runtime version and validate with targeted cases. |
| EVAL-145 | Known Runtime limitation: address resolution | “全卷最后一题” is incorrectly matched by the ambiguous ordinal guard. | A uniquely addressable delete request receives unnecessary clarification instead of a modification preview. | Add explicit whole-paper relative-address handling in a later Runtime version. |
| EVAL-267 | Known Runtime limitation: pending-preservation safety | “不要现在生成” triggers a forbidden `prepare_generation_plan` call instead of preserving the pending plan without action. | One side-effect-safety violation is recorded; the pending workflow is not handled as expected. | Prioritize a targeted pending-state safety regression before the next Runtime release. |

These limitations do not invalidate the **64/69 = 92.75%** Workflow Reliability result: all five were counted as failures, no new regression occurred relative to V3.1, and no provider failure affected the V3.2.1 result. They remain visible backlog items rather than hidden or adjusted outcomes.
