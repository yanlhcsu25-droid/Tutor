# Dev V3.2 — 扩展到 50 条的新增 Batch 30 选择表

选择原则：排除已执行的 20 条、`review.open_issues` 非空条目、与 DEVX/既有 Batch 精确定义重复条目；优先扩展 Subtype、工具生命周期和多轮状态覆盖。Teaching 类只有 4 条剩余无阻塞用例，因此不使用 8 条弱教学质量 Oracle；空缺 2 条分配给 Pending 与 Paper。

| Category | Case | Subtype | 审核状态 | Oracle / 选择理由 |
|---|---|---|---|---|
| generation | EVAL-001 | preview_generation | pending；无 open issue | 第一章4题40分 Preview；scope/题数/总分/Pending/禁止确认完整 |
| generation | EVAL-008 | preview_generation | pending；无 open issue | 8题100分非等比例约束，验证总分与题数独立 |
| generation | EVAL-012 | preview_generation | pending；无 open issue | 明示选择3/填空2/计算3的硬题型分布 |
| generation | EVAL-025 | preview_generation | pending；无 open issue | 12题120分较大 Preview，覆盖题库供给边界 |
| generation | EVAL-031 | preview_generation | pending；无 open issue | “只需要草案”措辞；验证 canonical scope 修复 |
| generation | EVAL-044 | preview_generation | pending；无 open issue | 草案+2/2/5 明示题型，覆盖较高计算题需求 |
| pending | EVAL-072 | partial_patch | pending；无 open issue | 仅改题数为7；总分/scope继承 |
| pending | EVAL-073 | multi_field_patch | pending；无 open issue | 同轮改12题120分并保持第一章 |
| pending | EVAL-074 | patch_no_confirm | pending；无 open issue | 改90分且暂不确认；覆盖 pause 与 patch 边界 |
| pending | EVAL-075 | idempotent_plus_patch | pending；无 open issue | 题数幂等、仅改总分80 |
| pending | EVAL-108 | pause_keep_pending | pending；无 open issue | 明示保留且不确认；必须零生命周期写 Tool |
| pending | EVAL-110 | pause_keep_pending | pending；无 open issue | “不要取消”否定取消语义边界 |
| pending | EVAL-114 | explicit_confirm | pending；无 open issue | Confirm 后持久化6题60分 Paper并清 Pending |
| paper | EVAL-116 | read_count | pending；确定性 Oracle 已修复 | read Observation 与回复题数10一致，Paper不变 |
| paper | EVAL-118 | read_type_distribution | pending；确定性 Oracle 已修复 | 四类题型分布完整事实校验 |
| paper | EVAL-146 | typed_replace | pending；无 open issue | 选择题第1题同型替换 Preview，禁止提前确认 |
| paper | EVAL-147 | multi_op_preview | pending；无 open issue | 删除+目标总分90的组合 Preview，正式 Paper 不变 |
| paper | EVAL-148 | confirm_boundary | pending；无 open issue | 用户要求直接应用仍必须停在 Preview 待确认 |
| paper | EVAL-153 | typed_delete | pending；无 open issue | 最后一道计算题删除 Preview与类型内地址解析 |
| paper | EVAL-155 | score_change_preview | pending；无 open issue | 全卷position=8改12分，仅创建 Preview |
| teaching | EVAL-235 | underspecified | pending；无 open issue | 学生安排请求缺少范围/产物，必须澄清且无Artifact |
| teaching | EVAL-237 | underspecified | pending；无 open issue | “规划一下”低信息请求安全澄清 |
| teaching | EVAL-238 | underspecified | pending；无 open issue | “这一部分”缺少可验证指代，不得创建设计 |
| teaching | EVAL-240 | underspecified | pending；无 open issue | “复习一下”不得猜 scope 或创建业务状态 |
| multi_turn | EVAL-272 | pending_patch | pending；逐轮 Oracle 已修复 | turn1创建8题80分；turn2仅改100分 |
| multi_turn | EVAL-273 | cancel | pending；逐轮 Oracle 已修复 | 创建后明确取消；prepare→discard，最终无Pending |
| multi_turn | EVAL-275 | preview_then_cancel | pending；逐轮 Oracle 已修复 | 改题 Preview 后取消；正式Paper始终不变 |
| multi_turn | EVAL-277 | three_turn_patch | pending；逐轮 Oracle 已修复 | 创建→改总分90→改题数7，逐轮 prepare |
| multi_turn | EVAL-278 | readonly_chain | pending；逐轮 Oracle 已修复 | 读取→不修改→读取证明题数量，全程无写副作用 |
| multi_turn | EVAL-280 | pause_then_cancel | pending；逐轮 Oracle 已修复 | 创建→暂停保留→明确取消，验证 pause/cancel 修复 |

分层数量：generation 6、pending 7、paper 7、teaching 4、multi_turn 6，共 30。Teaching 缺少的 2 条不从未解决质量 Judge 用例中强行补齐。
