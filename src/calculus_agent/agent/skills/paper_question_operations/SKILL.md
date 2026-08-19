---
name: paper_question_operations
version: 3
description: 处理当前试卷的读取、分析和已有试卷修改计划。所有已有试卷写操作统一通过 preview_paper_changes → confirm_paper_changes；放弃未提交方案统一使用 discard_pending_plan。
---

# 当前试卷操作

## 职责

本 Skill 只负责语义判断、题目引用解析、Tool 选择和澄清。

数据库状态、候选题选择、分数计算、约束校验、Pending 持久化和 Paper Version Mutation 均由 Python / Tool 完成。

```text
教师消息
→ 语义理解
→ Tool Call
→ Python Validation / Execution
→ Tool Observation
```

Conversation History 不是业务状态 Source of Truth。

## Tool 边界

| 目标 | Tool |
|---|---|
| 读取当前试卷事实 | `read_paper` |
| 分析当前试卷分布 | `analyze_paper` |
| 预览已有试卷修改 | `preview_paper_changes` |
| 确认已有试卷修改 | `confirm_paper_changes` |
| 放弃当前未提交方案 | `discard_pending_plan` |
| 撤销 / 重做 / 恢复版本 | `operate_paper_version` |

新建试卷由 generation 能力负责，不属于本 Skill 的已有试卷修改路径。

## 题目定位

教师可见题号默认采用题型内编号：

```text
选择题第1题
填空题第2题
计算题第3题
证明题第1题
```

解析为：

```json
{
  "section_type": "填空题",
  "section_order": 2
}
```

题型和题号不要求固定语序。只要语义能够唯一确定，就应直接解析。

只有教师明确说“全卷第N题”时，`read_paper` 才使用 legacy global position。

如果教师用“全卷第N题”要求修改，先 `read_paper(positions=[N])` 获取该题真实 section，再使用题型内 `QuestionAddress` 创建修改计划。不要把 global position 直接塞进 PaperChange operation。

## 统一 PaperChange

已有试卷的所有支持写操作都通过：

```text
preview_paper_changes
```

其 `operations` 可以组合：

```text
replace_question
remove_question
add_questions
change_question_score
change_question_type_distribution
```

例如：

```text
删除选择题第2题，把计算题第1题换简单一点，总分保持100
```

应表达成一个整体 PaperChangeRequest，而不是拆成互相独立的旧 Tool。

不要由 LLM 计算最后题量、总分或具体候选 Question ID。

## 删除

删除使用：

```json
{
  "type": "remove_question",
  "target": {
    "section_type": "选择题",
    "section_order": 2
  }
}
```

教师只要求删除时，不要自行设置 `target_total_score`。

只有教师明确说：

```text
总分保持100
总分改成90
```

才设置顶层 `target_total_score`。分值重平衡交给 Python。

## 替换

替换使用：

```json
{
  "type": "replace_question",
  "target": {
    "section_type": "计算题",
    "section_order": 1
  },
  "difficulty_direction": "easier"
}
```

不得指定 replacement Question ID。

`preserve_knowledge_points` 是显式 opt-in 硬约束。只有教师明确要求：

```text
知识点不变
保持原考点
保留原知识点
```

才允许设置 true。

以下表达不代表保持知识点：

```text
换一道
这题超纲
换简单一点
这题不合适
```

## 新增

新增使用：

```json
{
  "type": "add_questions",
  "question_type": "填空题",
  "count": 1
}
```

只有教师明确指定单题分值时才传 `score`。

候选题、scope、approved/active/current、去重和默认分值推导全部由 Python 处理。

## 改改单题分值

教师明确指定某题新分值时：

```json
{
  "type": "change_question_score",
  "target": {
    "section_type": "计算题",
    "section_order": 1
  },
  "score": 12
}
```

不要把“总分保持100”误写成某一道题的 score；它属于 request 顶层 `target_total_score`。

## 调整题型结构

教师明确改变题型数量结构且保持总题量时，可以使用：

```json
{
  "type": "change_question_type_distribution",
  "changes": {
    "选择题": -1,
    "填空题": 1
  }
}
```

LLM 只表达教师要求的结构变化；具体换哪些题由 Python 决定。

## Preview / Confirm

所有已有试卷写操作：

```text
教师提出修改
→ preview_paper_changes
→ 等待教师确认
→ confirm_paper_changes
```

Preview 成功不等于 Paper 已修改。

不得在同一轮刚创建新的 preview 后替教师自动确认。

## Pending 修改

如果当前已有 pending paper-change plan，而教师继续修改：

```text
之前那个删除保留，再把第一题换简单一点
```

继续调用 `preview_paper_changes`。

只有 Tool 返回的新 plan 才是新的 pending Source of Truth；不要在聊天历史里自行 merge。

如果教师明确确认旧方案，同时又提出新的额外修改：

```text
刚才删除就按这个，再把第一题换掉
```

应：

```text
confirm_paper_changes
→ 基于新 Paper Version
→ preview_paper_changes
```

新的替换仍然需要后续确认。

不要把教师尚未确认的新修改偷偷并入已经被确认的旧 plan。

## 放弃 Pending

教师说：

```text
算了
不改了
还是用原来的
这个方案不要了
```

调用：

```text
discard_pending_plan
```

它只做：

```text
清除未提交 Pending
Paper 保持不变
```

不是 rollback，也不是恢复旧版本。

如果修改已经确认执行，随后教师觉得旧版更好，应使用：

```text
operate_paper_version
```

而不是 `discard_pending_plan`。

## Version

统一使用：

```text
operate_paper_version(action="undo")
operate_paper_version(action="redo")
operate_paper_version(action="restore", target_version=N)
```

## Tool Observation

必须遵守：

- Preview 不能说成已应用；
- Confirm 失败不能说成成功；
- Discard 没有成功 Observation 时不能说 Pending 已清；
- 版本操作没有成功 Observation 时不能说已恢复；
- Tool 返回 blocking errors / clarification questions 时必须暴露给教师；
- 不得为了成功擅自放宽 scope、知识点、难度、题量或总分约束。
