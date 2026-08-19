# 错题反馈 → 针对性巩固卷 V1 交付报告

Branch: `refactor/runtime-toolkit-phase4a`

## A. 实际修改文件

### 新增
| 文件 | 说明 |
| --- | --- |
| `src/calculus_agent/agent/services/reinforcement.py` | `ReinforcementService` + 领域模型 + 纯函数 `reinforcement_weight` |
| `tests/teacher_agent/test_reinforcement_plan.py` | 19 个测试（单元 + Tool 集成 + 完整 A→B 集成 + Agent 回归 + Pending 状态隔离回归） |

### 修改
| 文件 | 说明 |
| --- | --- |
| `src/calculus_agent/agent/schemas.py` | 新增 `FeedbackItemInput`、`PrepareReinforcementPlanInput` |
| `src/calculus_agent/agent/services/generation.py` | 新增 fresh-task preview 模式；reinforcement 新任务独立于旧 PendingGeneration 编译，成功后再覆盖旧 pending |
| `src/calculus_agent/agent/paper_tool_registry.py` | `PAPER_TOOL_NAMES` 加 `prepare_reinforcement_plan`；docstring 去掉 magic number |
| `src/calculus_agent/agent/agent.py` | `_SYSTEM_PROMPT` 加「错题反馈与巩固卷」路由规则；guard 补 TeachingDesign 抑制条件 |
| `tests/teaching_design/test_agent_runtime_integration.py` | 更新 stale 测试（见 G 节"pre-existing 失败"） |

## B. 最终领域模型

```python
class FeedbackItemInput(BaseModel):          # extra=forbid
    address: QuestionAddress | None = None    # section_type + section_order
    position: int | None = Field(ge=1)        # 全卷题号
    teacher_note: str | None = Field(max_length=500)
    # validator: address 与 position 二选一

class PrepareReinforcementPlanInput(BaseModel):  # extra=forbid
    items: list[FeedbackItemInput] = Field(min_length=1, max_length=100)

class ReinforcementEvidence(BaseModel):
    paper_item_id, question_id, position, section_type, section_order,
    question_type, difficulty, knowledge, teacher_note

class KnowledgeReinforcementTarget(BaseModel):
    knowledge_node_id, knowledge_name, evidence_count, weight

class ReinforcementContext(BaseModel):
    source_paper_id, source_question_ids, evidence,
    target_knowledge, scope_names, scope_chapter_ids, warnings
```

`ReinforcementContext` 是运行时编译结果，**不持久化**。`PaperFeedback` 也是运行时对象，无 DB 生命周期。

## C. Deterministic Rule

1. **错题 → PaperItem**：复用 `QuestionAddress` + `resolve_section_item_from_items`（section-local）或 `position`（全卷）；不解析中文、不猜。
2. **Knowledge 聚合**：每道 Question 对同一 KnowledgeNode 最多贡献 1 次 evidence（`set` 去重，忽略 relation 主次）。
3. **去重**：重复 feedback item 按 `PaperItem.id` 去重，记 `duplicate_feedback_reference_ignored` warning。
4. **weight**：`reinforcement_weight(evidence_count) = min(5, 2 + evidence_count)`，纯函数。
5. **scope**：`Question.curriculum_chapter_id → CurriculumNode` 上溯到 chapter 的 title；稳定去重；无章节则 `reinforcement_scope_unresolved`。

## D. Tool API

```python
# prepare_reinforcement_plan
# 输入：PrepareReinforcementPlanInput(items: list[FeedbackItemInput])  extra=forbid
# 输出 payload:
{
  "ok": bool,
  "reinforcement_context": { ... },
  "generation_preview": { ... }   # 现有 GenerationPlanPreview
}
# result_fields 继续写 generation_preview / warnings / blocking_errors /
# clarification_questions，前端现有蓝图卡无需改动。
# status: waiting_confirmation | needs_clarification | failed
```

模型**禁止**传 `paper_id / question_id / knowledge_node_id / weight`（`extra=forbid` 会拒绝）。当前 Paper 取自 `context.version_id or context.paper_id`。

## E. State

- `PaperFeedback`：**不持久化**（运行时对象）。
- `ReinforcementContext`：**不持久化**（运行时对象）。
- `PendingGeneration`：沿用现有 source of truth（唯一等待确认的业务状态）。
- `Paper`：Database source of truth。

无新增 DB 表、无 migration。

## F. Agent Routing

`_SYSTEM_PROMPT` 新增「错题反馈与巩固卷」段（最小规则）：

> 当教师反馈当前已生成试卷中的错题并希望据此强化/再出一套/出巩固卷时，使用 `prepare_reinforcement_plan`。题目引用遵循 Paper addressing：「选择题第2题」用 section_type+section_order；只有「全卷第N题」才用 position；无法唯一定位必须澄清。不要根据题目文本自行判断知识点、章节或 weight——这些来自 Tool Observation。`prepare_reinforcement_plan` 只准备 GenerationPlanPreview，不创建 Paper；教师确认后用 `confirm_generation`。不要把错题描述成已证明学生"不会"，也不要把它解释成高层 TeachingDesign。

## G. Tests

新增 19 个测试覆盖 Case A–J（单题单点/重复/多知识点/同题重复 link/重复 feedback/无效地址/无 Paper/无 KnowledgeNode/无章节/跨章节）+ Tool 集成（`waiting_confirmation`、preview 非空、pending 存在、Paper 数不增）+ 完整 A→B 集成（confirm 后 Paper B 创建、pending 清空）+ Agent 回归（feedback → `prepare_reinforcement_plan` 而非 read_paper+猜、确认走 `confirm_generation`、无 TeachingDesign 复活、设计意图不被 runtime 覆盖）+ schema `extra=forbid` + PendingGeneration 状态隔离回归。

- `uv run pytest -q tests/teacher_agent` ✅
- `uv run pytest -q tests/generation` ✅
- `uv run pytest -q tests/teaching_design` ✅
- `uv run pytest -q` ✅（全绿，仅 skip，无失败）

### pre-existing 失败（非本功能引入）已修复
`tests/teaching_design/test_agent_runtime_integration.py` 原 `test_real_conversation_create_revise_confirm...` 断言 fresh request 表面含 `create_teaching_design`，与 prior 会话「TeachingDesign 只保留 legacy compatibility」产品决策冲突（`apply_generation_design_intent.py` 落地时漏改该测试）。处理：新增 `test_fresh_request_does_not_expose_teaching_design_creation`（断言 fresh surface 不含 create/search/activate），原集成测试改为 seed legacy design → revise → confirm，保留版本化与可追溯性验证。

## H. 未实现项（后续版本）

- 错误类型诊断（knowledge_gap / calculation_error / concept_confusion）
- student mastery / 长期学生画像
- 长期 feedback history / paper_feedback 持久化
- 自动避开原题（`avoid_previous_paper_questions` 仍是 unsupported preference）
- 跨历史 Paper feedback（历史 Paper resolver）
- 基于 difficulty 的下一卷难度策略
