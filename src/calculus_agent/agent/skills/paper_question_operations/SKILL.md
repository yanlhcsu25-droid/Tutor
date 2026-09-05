---
name: paper_question_operations
version: 3
description: 处理当前试卷的读取、分析和已有试卷修改计划。所有写操作统一经过 preview_paper_changes → confirm_paper_changes；放弃未提交方案统一使用 discard_pending_plan。
---

# 当前试卷操作契约

## 职责与事实来源

本 Skill 只负责语义判断、题目引用解析、Tool 选择和必要澄清；数据库状态、候选选择、分数计算、约束校验、Pending 持久化和 Paper Version Mutation 均由 Python/Tool 完成。调用链为 `教师消息 → Tool Call → Python Validation/Execution → Tool Observation`，Conversation History 不是业务状态 Source of Truth。

读取当前试卷事实使用 `read_paper`，分析分布使用 `analyze_paper`，预览修改使用 `preview_paper_changes`，确认修改使用 `confirm_paper_changes`，放弃未提交方案使用 `discard_pending_plan`，撤销/重做/恢复使用 `operate_paper_version`。新建试卷属于 Generation，不走已有试卷修改路径。

## 题目定位

教师可见题号默认采用题型内编号，如“选择题第1题”“填空题第2题”，解析为 `QuestionAddress(section_type, section_order)`；题型和题号语序不固定，只要能唯一确定即可直接解析。只有教师明确说“全卷第N题”时才使用 global position。若用全卷题号修改，先调用 `read_paper(positions=[N])` 获取真实 section，再用题型内 QuestionAddress 创建修改计划；不得把 global position 直接传入 PaperChange operation。无法唯一定位时必须澄清，不能猜测。

## 统一修改请求

所有已有试卷写操作都调用 `preview_paper_changes`，其 `operations` 可组合 `replace_question`、`remove_question`、`add_questions`、`change_question_score` 和 `change_question_type_distribution`。多个修改应形成一个整体 PaperChangeRequest；LLM 不计算最终题量、总分或候选 Question ID。

删除使用 `remove_question + target QuestionAddress`。仅删除时不要设置 `target_total_score`；只有教师明确要求“总分保持100”或“总分改成90”时才设置顶层 `target_total_score`，分值重平衡由 Python 完成。

替换使用 `replace_question + target QuestionAddress`，可按教师要求设置 `difficulty_direction`，不得指定 replacement Question ID。`preserve_knowledge_points` 是显式 opt-in：只有教师明确说“知识点不变、保持原考点、保留原知识点”时才能设为 true；“换一道、超纲、简单一点、不合适”均不代表保持知识点。

新增使用 `add_questions`，只提交题型、数量以及教师明确指定的单题 `score`。候选、scope、approved/active/current、去重和默认分值由 Python 处理。

改单题分值使用 `change_question_score + target + score`。“总分保持100”属于顶层 `target_total_score`，不得误写成某一道题的 score。调整题型数量且保持总题量时使用 `change_question_type_distribution`，LLM 只表达教师要求的数量变化，具体替换哪些题由 Python 决定。

## Preview、Confirm 与 Pending

生命周期始终是 `教师提出修改 → preview_paper_changes → 等待确认 → confirm_paper_changes`。Preview 成功不等于 Paper 已修改，同一轮刚创建 preview 后不得替教师自动确认。

已有 pending paper-change plan 时，教师继续修改应再次调用 `preview_paper_changes`；只有 Tool 返回的新 plan 才是 pending Source of Truth，不得在聊天历史中自行 merge。若教师明确确认旧方案并同时提出新修改，应先 `confirm_paper_changes`，再基于新 Paper Version 调用 `preview_paper_changes`，新修改仍需后续确认，不能偷偷并入已确认方案。

教师说“算了、不改了、还是用原来的、这个方案不要了”时调用 `discard_pending_plan`；它只清除未提交 Pending，Paper 保持不变，不是 rollback。修改已经确认后若要回到旧版，应使用 `operate_paper_version`：`action="undo"`、`action="redo"` 或 `action="restore", target_version=N`。

## Observation 与回复

Preview 不能说成已应用；Confirm、Discard 或版本操作没有成功 Observation 时不能声称成功。Tool 返回的 blocking errors 和 clarification questions 必须告诉教师；不得为完成任务擅自放宽 scope、知识点、难度、题量或总分约束。最终回复应简洁、连续：用一个短段落说明预览/执行结果和是否需要确认；有多个独立变更或阻塞时才使用少量完整列表，不逐句换行，不展示内部 JSON、Tool 协议或冗长执行过程。
