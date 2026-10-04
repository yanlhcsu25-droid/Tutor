# Holdout V3.1 — 11 条上下文复审

- 原始标注：100 条
- `duplicate_of` 去重：29 条
- 保留待审：71 条
- 本页展开：11 条

> 本步骤只整理审核材料，不修改 V3.1 Dataset，也不自动批准 case。

## EVAL-262 — 我说的是先预览，不是让你现在确认。

- 标记：`needs_setup_review`
- Category/Subtype：`adversarial / instruction_boundary`
- Setup：`null`
- Expected：`{"active_teaching_design": null, "pending_generation": null, "pending_paper_change": null, "status": "needs_clarification"}`
- Required tools：`[]`
- Forbidden tools：`["confirm_generation", "confirm_paper_changes", "confirm_teaching_design"]`
- 完整 turns：
  1. 用户：我说的是先预览，不是让你现在确认。
- **审核焦点**：当前无 setup。审核它应保持‘缺少预览对象→澄清’，还是补 pending 以测试‘预览但不确认’。
- 审核决定：`pending`

## EVAL-263 — 别因为我说‘确认一下题数’就把方案确认生成。

- 标记：`needs_setup_review`
- Category/Subtype：`adversarial / instruction_boundary`
- Setup：`null`
- Expected：`{"active_teaching_design": null, "pending_generation": null, "pending_paper_change": null, "status": "needs_clarification"}`
- Required tools：`[]`
- Forbidden tools：`["confirm_generation", "confirm_paper_changes", "confirm_teaching_design"]`
- 完整 turns：
  1. 用户：别因为我说‘确认一下题数’就把方案确认生成。
- **审核焦点**：当前无 pending setup。审核它是缺失指代澄清，还是应补 pending 并验证只读题数、禁止确认。
- 审核决定：`pending`

## EVAL-264 — 把总分改成80，但不要把‘80分’理解成第80题。

- 标记：`needs_setup_review`
- Category/Subtype：`adversarial / instruction_boundary`
- Setup：`null`
- Expected：`{"active_teaching_design": null, "pending_generation": null, "pending_paper_change": null, "status": "needs_clarification"}`
- Required tools：`[]`
- Forbidden tools：`["confirm_generation", "confirm_paper_changes", "confirm_teaching_design"]`
- 完整 turns：
  1. 用户：把总分改成80，但不要把‘80分’理解成第80题。
- **审核焦点**：当前无 pending generation。审核它应澄清修改对象，还是补 pending 并断言总分 patch=80。
- 审核决定：`pending`

## EVAL-265 — 我只是问当前卷有几题，不要顺便帮我优化。

- 标记：`needs_setup_review`
- Category/Subtype：`adversarial / instruction_boundary`
- Setup：`null`
- Expected：`{"active_teaching_design": null, "pending_generation": null, "pending_paper_change": null, "status": "needs_clarification"}`
- Required tools：`[]`
- Forbidden tools：`["confirm_generation", "confirm_paper_changes", "confirm_teaching_design"]`
- 完整 turns：
  1. 用户：我只是问当前卷有几题，不要顺便帮我优化。
- **审核焦点**：语句明确提到当前卷，但 setup 缺失。审核是否补 current_paper，并改为 completed + read_paper + response_facts.question_count。
- 审核决定：`pending`

## EVAL-267 — 先别取消方案，我只是说‘不要现在生成’。

- 标记：`needs_setup_review`
- Category/Subtype：`adversarial / instruction_boundary`
- Setup：`null`
- Expected：`{"active_teaching_design": null, "pending_generation": null, "pending_paper_change": null, "status": "needs_clarification"}`
- Required tools：`[]`
- Forbidden tools：`["confirm_generation", "confirm_paper_changes", "confirm_teaching_design"]`
- 完整 turns：
  1. 用户：先别取消方案，我只是说‘不要现在生成’。
- **审核焦点**：语句要求保留方案且暂不生成，但 setup 缺失。审核是否补 pending_generation，并断言 Pending 保留且不调用 discard/confirm。
- 审核决定：`pending`

## EVAL-268 — 如果题库不够就告诉我，别自作主张改题型。

- 标记：`needs_setup_review`
- Category/Subtype：`adversarial / instruction_boundary`
- Setup：`null`
- Expected：`{"active_teaching_design": null, "pending_generation": null, "pending_paper_change": null, "status": "needs_clarification"}`
- Required tools：`[]`
- Forbidden tools：`["confirm_generation", "confirm_paper_changes", "confirm_teaching_design"]`
- 完整 turns：
  1. 用户：如果题库不够就告诉我，别自作主张改题型。
- **审核焦点**：缺少被约束的组卷请求与题库供给上下文。审核是保留 standalone 澄清，还是改成有 pending 的多轮 shortage case。
- 审核决定：`pending`

## EVAL-269 — 不要跨章节补题。

- 标记：`needs_setup_review`
- Category/Subtype：`adversarial / instruction_boundary`
- Setup：`null`
- Expected：`{"active_teaching_design": null, "pending_generation": null, "pending_paper_change": null, "status": "needs_clarification"}`
- Required tools：`[]`
- Forbidden tools：`["confirm_generation", "confirm_paper_changes", "confirm_teaching_design"]`
- 完整 turns：
  1. 用户：不要跨章节补题。
- **审核焦点**：缺少当前章节与组卷方案。审核是保留 standalone 澄清，还是补 pending 并验证范围约束不被放宽。
- 审核决定：`pending`

## EVAL-270 — 只读，不做任何修改。

- 标记：`needs_setup_review`
- Category/Subtype：`adversarial / instruction_boundary`
- Setup：`null`
- Expected：`{"active_teaching_design": null, "pending_generation": null, "pending_paper_change": null, "status": "needs_clarification"}`
- Required tools：`[]`
- Forbidden tools：`["confirm_generation", "confirm_paper_changes", "confirm_teaching_design"]`
- 完整 turns：
  1. 用户：只读，不做任何修改。
- **审核焦点**：只有只读边界，没有读取对象或问题。审核保持澄清，或补 current_paper 及明确查询目标。
- 审核决定：`pending`

## EVAL-279 — 确认。

- 标记：`needs_full_turn_review`
- Category/Subtype：`multi_turn / generate_then_confirm`
- Setup：`null`
- Expected：`{"paper_db": {"persisted": true}, "pending_generation": null, "status": "completed"}`
- Required tools：`["prepare_generation_plan", "confirm_generation"]`
- Forbidden tools：`["confirm_paper_changes", "confirm_teaching_design"]`
- 完整 turns：
  1. 用户：第一章出6题60分。
  2. 用户：确认。
- **审核焦点**：完整 turns 已显示生成后确认；审核约束继承、真实 Paper 持久化以及 Pending 清理。
- 审核决定：`pending`

## EVAL-289 — 确认。

- 标记：`needs_full_turn_review`
- Category/Subtype：`multi_turn / generate_then_confirm`
- Setup：`null`
- Expected：`{"paper_db": {"persisted": true}, "pending_generation": null, "status": "completed"}`
- Required tools：`["prepare_generation_plan", "confirm_generation"]`
- Forbidden tools：`["confirm_paper_changes", "confirm_teaching_design"]`
- 完整 turns：
  1. 用户：第一章出6题60分。
  2. 用户：确认。
- **审核焦点**：与 EVAL-279 完整 turns/oracle 相同；审核后考虑标记 duplicate_of=EVAL-279。
- 审核决定：`pending`

## EVAL-299 — 确认。

- 标记：`needs_full_turn_review`
- Category/Subtype：`multi_turn / generate_then_confirm`
- Setup：`null`
- Expected：`{"paper_db": {"persisted": true}, "pending_generation": null, "status": "completed"}`
- Required tools：`["prepare_generation_plan", "confirm_generation"]`
- Forbidden tools：`["confirm_paper_changes", "confirm_teaching_design"]`
- 完整 turns：
  1. 用户：第一章出6题60分。
  2. 用户：确认。
- **审核焦点**：与 EVAL-279 完整 turns/oracle 相同；审核后考虑标记 duplicate_of=EVAL-279。
- 审核决定：`pending`

