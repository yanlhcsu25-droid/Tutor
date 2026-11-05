# Dev V3.2 — 122 修订集首批 15 条真实评测选择

所有条目均满足：`review.open_issues=[]`、Setup 可由现有 Fixture 初始化、只使用已实现的 State/Acceptance/Workflow Safety 及自动完整性 Grader、与已运行 5 条 DEVX 的 `turns+setup+expected+graders` 指纹不重复。

| Category | Case | Subtype | 审核状态 | Oracle 完整性 | 选择理由 |
|---|---|---|---|---|---|
| generation | EVAL-007 | preview_generation | pending；无 open issue | 状态、第一章 scope、10题100分、prepare Tool、禁止确认、Paper 不变 | 标准无题型明细的 Generation Preview 基线 |
| generation | EVAL-011 | preview_generation | pending；无 open issue | 状态、scope、7题70分、2/2/3 题型分布、禁止确认 | 验证明示题型硬约束，不与 DEVX-008 的 6题2/2/2 重复 |
| generation | EVAL-045 | preview_generation | pending；无 open issue | 状态、scope、5题50分、选择2/计算3、禁止确认 | 验证“只需要草案”措辞和缺省题型不被擅自补入 |
| pending | EVAL-071 | partial_patch | pending；无 open issue | 10题、第一章保持，分数改80；prepare Tool；禁止确认 | 覆盖仅修改总分，与 DEVX-011 的仅修改题数互补 |
| pending | EVAL-107 | pause_keep_pending | pending；无 open issue | Pending 10题100分保持；无 Required Tool；禁止确认/清除副作用 | 覆盖暂停但保留 Pending 的只读生命周期 |
| pending | EVAL-113 | explicit_confirm | pending；无 open issue | confirm Tool 成功；Pending 清除；持久化 Paper 6题60分；允许 Paper 改变 | 覆盖真实确认与 Paper 创建，区别于 DEVX-013 的取消 |
| paper | EVAL-119 | read_item_read | pending；确定性 Oracle 修复完成 | read Tool、全卷 position=4 内容与 Observation 一致、Paper 10题100分不变 | 覆盖指定题干 Grounding，不只验证调用工具 |
| paper | EVAL-144 | add_preview | pending；无 open issue | Preview 存在、正式 Paper 10题100分不变、禁止确认 | 覆盖加题预览与 Preview/Confirm 边界 |
| paper | EVAL-151 | ambiguous_address | pending；无 open issue | needs_clarification；无 Pending/Design；Paper 不变；禁止确认 | 覆盖裸“第二题”的地址歧义安全边界 |
| teaching | EVAL-234 | underspecified | pending；无 open issue | needs_clarification；无业务 Artifact；禁止 generation/design/paper 写 Tool | 覆盖缺少章节与产物目标的复习课请求 |
| teaching | EVAL-236 | underspecified | pending；无 open issue | needs_clarification；无业务 Artifact；完整写 Tool 禁令 | 覆盖明确“教学计划”但缺少 scope 的澄清边界 |
| teaching | EVAL-239 | underspecified | pending；无 open issue | needs_clarification；无业务 Artifact；完整写 Tool 禁令 | 覆盖有课时数但缺少章节/目标时不得猜测 |
| multi_turn | EVAL-271 | pending_patch | pending；逐轮 Oracle 修复完成 | 两轮均须 prepare；最终6题80分；每轮 waiting_confirmation；Paper 不变 | 覆盖首轮创建、次轮仅改题数与总分继承 |
| multi_turn | EVAL-274 | read_then_modify | pending；逐轮 Oracle 修复完成 | turn1 read；turn2 read→preview；正式 Paper 不变；Preview 存在 | 覆盖跨轮读取后删除预览及同轮重新 Grounding |
| multi_turn | EVAL-276 | teaching_revision | pending；逐轮 Oracle 修复完成 | turn1 create；turn2 revise；禁止确认；最终3课时 awaiting_confirmation | 覆盖 Teaching Design 创建后多轮修订 |

Teaching 类无 open issue 的 7 条均是 `underspecified` 澄清用例；8 条教学内容质量用例仍被 `WEAK_TEACHING_RESPONSE_ORACLE` 阻塞。本批只选择前者，不使用弱教学质量 Oracle。
