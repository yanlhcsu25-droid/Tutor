---
name: teaching-design
version: 2
description: 处理高层教学任务。新 TeachingDesign 必须先基于真实 Curriculum 与 Question Bank Observation 调查环境，再形成可版本化、可确认、可执行的教学设计；不得把聊天历史当业务事实或伪造 evidence_refs。
---

# TeachingDesign 业务契约

## 适用范围

本 Skill 处理“为什么这样教、教哪些知识、重点如何分配、课程顺序、讲义组织和测评目标”等高层教学任务，例如多章复习、课堂设计、覆盖权重和概念/计算侧重。已经明确题型数量、分值等执行约束的新建试卷请求可以直接走 Generation，不要强制创建 TeachingDesign；普通教学方法讨论也可直接自然语言回答。

## 事实来源

事实优先级为：当前 Tool Observation → persisted TeachingDesign → current structured runtime state → Conversation History（仅用于语言理解）。题库数量、章节归属、题型供给、难度画像和知识点必须来自 Tool Observation，不得凭聊天或模型知识猜测。

## 新建设计

标准流程：`Teacher Goal → inspect_curriculum → inspect_question_bank(detail_level="aggregate") → 必要时 chapter_detail → create_teaching_design → 等待教师修改或确认`。不得凭常识直接创建，也不得先读取整个题库；环境调查必须 aggregate first、drill-down only when needed。

先调用 `inspect_curriculum(scope_names=[...])`，确认教师范围能映射到当前教材并获取受控章节/知识节点；范围无法解析时先澄清。随后调用 `inspect_question_bank(scope_names=[...], detail_level="aggregate")`，检查各章真实可组卷题量、题型供给、approved QuestionProfile 覆盖率、难度分布和主要知识点供给，其 eligibility 口径与正式 Paper selector 一致。

只有进一步调查可能改变设计时才调用 `chapter_detail`，例如某章题量明显不足、指定知识点必须重点考、某题型稀缺或 aggregate 无法解释供给失衡。禁止无目的逐章下钻、重复查询或在信息已足够时继续调查；每个 Teacher Turn 最多调用 4 次环境调查 Tool。

Observation 必须影响设计。例如第三章题量或证明题不足时，可保留教学重点，但不要设置不可实现的硬配额，并在 `feasibility_warnings` 说明风险。教师明确要求第三章占 50% 时，不能擅自覆盖其目标，只能说明供给风险并等待确认。

调查完成后调用 `create_teaching_design`。LLM 负责 `objective`、`scope_names`、`knowledge_plan`、`teaching_priorities`、`teaching_sequence`、`lecture_plan`、`assessment_plan` 和 `feasibility_warnings`；题库计数、ID、Solver 约束、evidence provenance 和数据库写入由 Python/Tool 负责。成功状态为 `awaiting_confirmation`，同一轮不得确认。

## Evidence

`evidence_refs` 是系统管理字段。LLM 不得生成 `ref_id` 或 `observed_by_run_id`，不得复制旧聊天中的题库数字，也不得在 revise 时手工编辑。成功的 inspection Tool 会在当前运行上下文注册证据，`create_teaching_design` 自动注入同范围的 `curriculum_scope`、`question_bank_aggregate` 和可选 `question_bank_detail`；缺少同范围 Curriculum 与 aggregate evidence 时，创建应被确定性拒绝。

## 修改、确认与历史

教师修改当前设计时调用 `revise_teaching_design`，只提交本轮真实改变的语义字段。scope 变化（如“再加第四章”）必须先重新调查新范围；仅调整重点、顺序或讲义策略且现有 evidence 足够时，不必机械重查。若本轮重新调查，系统自动把新 Observation 追加到新版本证据。

教师后续明确接受时调用 `confirm_teaching_design`，之后由 `Constraint Compiler → CP-SAT → Paper → Validation` 执行，不再要求第二次组卷业务确认。Solver/Validation 不可行时不得偷偷降低已确认条件；需要教师接受修改并创建新版本。历史设计使用 `search_teaching_design_history` 查询，多个候选无法唯一确定时必须澄清，不得翻聊天记录猜测。

## 回复与禁止事项

任何业务事实以 Observation 为准：Tool 失败不能说成功，不得编造题库数字、EvidenceReference 或 version，不得绕过 scope、stale、Solver 校验。回复应简洁、连续：优先用一个短段落说明设计结果、供给风险和待确认事项；只有多个独立风险时使用少量完整列表，不逐句换行，不展示内部 Tool 参数或执行协议。
