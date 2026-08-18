---
name: teaching_design
version: 2
description: 处理高层教学任务。新 TeachingDesign 必须先基于真实 Curriculum / Question Bank Observation 做环境调查，再形成可版本化、可确认、可执行的教学设计。不要把聊天历史当业务事实，不要伪造 evidence_refs。
---

# TeachingDesign — Environment-Aware Contract

## 核心工作流

高层教学任务默认流程：

```text
Teacher Goal
    ↓
inspect_curriculum
    ↓
inspect_question_bank(detail_level="aggregate")
    ↓
必要时 chapter_detail
    ↓
create_teaching_design
    ↓
等待教师修改 / 确认
```

不是：

```text
Teacher Goal
→ 凭模型常识直接 create_teaching_design
```

也不是：

```text
先把整个题库全部查出来
→ 再设计
```

环境调查必须 **aggregate first，drill-down only when needed**。

---

## 什么请求属于高层 TeachingDesign

例如：

- 第一到第三章做一次90分钟期中复习，中等偏难；
- 帮我设计这节课怎么讲，同时安排测试；
- 这三章覆盖尽量均衡，但第三章重点一点；
- 概念理解多一点，少强调纯计算。

这些请求描述的是：

```text
为什么这样教
教哪些知识
重点怎么分配
课程顺序是什么
讲义怎么组织
测评要考什么
```

不是最终 Question ID / PaperItem / PaperBlueprint。

明确的执行型组卷命令，例如已经给定具体题型数量和分值，可以继续走兼容 Direct Generation；不要为了“Agent 化”强制创建 TeachingDesign。

---

## Source of Truth

优先级：

1. 当前 Tool Observation；
2. persisted TeachingDesign；
3. current structured runtime state；
4. Conversation History 只做语言理解。

题库数量、章节归属、题型供给、难度画像等事实必须来自 Tool Observation，不得凭聊天或模型知识猜测。

---

## 第一步：Curriculum Inspection

新 TeachingDesign 必须先：

```text
inspect_curriculum(scope_names=[...])
```

目标：

- 确认教师说的范围能映射到当前激活教材；
- 了解该范围中的受控章节 / 知识节点；
- 避免使用数据库中不存在的章节或知识点。

如果范围无法解析，先澄清，禁止继续假装已经掌握课程结构。

---

## 第二步：Question Bank Aggregate

Curriculum 成功后必须：

```text
inspect_question_bank(
  scope_names=[...],
  detail_level="aggregate"
)
```

Aggregate 重点关注：

- 各章真实可组卷题量；
- 各题型供给；
- approved QuestionProfile 覆盖率；
- 难度分布；
- 主要知识点供给。

这里的候选口径和正式 Paper selector 使用同一 eligibility contract。

---

## 什么时候 Drill Down

只有 aggregate Observation 表明进一步调查**可能改变设计**时，才调用：

```text
inspect_question_bank(
  scope_names=[...],
  detail_level="chapter_detail",
  chapter_name="第三章"
)
```

合理情况：

- 某章题量明显少；
- 教师要求某知识点必须重点考；
- 某题型看起来稀缺；
- 需要判断一个重点知识是否有足够题目支撑；
- aggregate 无法解释明显的供给不平衡。

不合理情况：

- 无目的把每章都 drill-down；
- 为了“显得 Agent 在思考”重复查询相同数据；
- 已经足够做决策仍继续查。

每个 Teacher Turn 最多 4 次环境调查 Tool Call。

---

## Observation 必须真正影响设计

不要仪式化调用 Tool。

例如 Observation：

```text
第一章 80题
第二章 45题
第三章 13题
第三章证明题 1题
```

如果教师只说“覆盖尽量均衡”，不要机械生成：

```text
三章完全等权
证明题平均分配
```

应该把真实供给转化为设计决策，例如：

- 保留第三章为教学重点；
- 测评覆盖不要求与题库数量机械成正比；
- 不把第三章证明题设置成不可实现的硬配额；
- 必要时把稀缺事实放入 feasibility_warnings。

反过来，如果教师明确说“第三章必须占50%”，Observation 不能偷偷覆盖教师目标；只能说明供给风险并等待教师确认设计。

---

## evidence_refs

`evidence_refs` 是系统管理字段。

LLM：

- 不得自己生成 ref_id；
- 不得自己填写 observed_by_run_id；
- 不得复制旧聊天里的题库数字当证据；
- 不得在 revise 时手工编辑 evidence_refs。

成功的 inspection Tool 会把可信 EvidenceReference 注册到当前运行上下文。

`create_teaching_design` 会自动把当前范围对应的：

```text
curriculum_scope
question_bank_aggregate
question_bank_detail（如果有）
```

注入 TeachingDesign。

如果没有同一范围的 Curriculum + Question Bank aggregate evidence，创建会被确定性拒绝。

---

## Create

调查完成后：

```text
create_teaching_design
```

LLM 负责语义设计：

- objective；
- scope_names；
- knowledge_plan；
- teaching_priorities；
- teaching_sequence；
- lecture_plan；
- assessment_plan；
- feasibility_warnings。

LLM 不负责：

- 题库计数；
- ID；
- Solver 约束计算；
- evidence provenance；
- DB mutation。

成功后状态：

```text
awaiting_confirmation
```

同一轮不得确认。

---

## Revise

教师修改当前设计：

```text
revise_teaching_design
```

只提交教师真实改变的语义字段。

如果教师改变了 scope，例如：

```text
“再加第四章”
```

必须重新调查新范围后才能 revise。

如果只是：

```text
“第三章再重点一点”
“先讲概念再讲例题”
```

且已有 evidence 足够，不要求机械重查题库。

如果本轮确实重新调查，新 Observation 会由系统自动追加到新版本的 evidence_refs。

---

## Confirm + Execute

教师后续明确接受：

```text
confirm_teaching_design
```

然后：

```text
TeachingDesign confirmed
→ Constraint Compiler
→ CP-SAT
→ Paper
→ Validation
```

没有第二次组卷业务确认。

Solver / Validation 不可行时，不得偷偷降低 confirmed TeachingDesign 条件。需要教师接受修改后创建新 TeachingDesign Version。

---

## 历史设计

历史设计查询：

```text
search_teaching_design_history
```

不是翻聊天记录猜。

多个候选无法唯一确定时，向教师澄清。

---

## Tool Observation

任何业务事实以 Observation 为准。

禁止：

- Tool 失败说成功；
- 自己编造题库数字；
- 自己编造 EvidenceReference；
- 自己修改 version；
- 为了完成任务绕开 scope / stale / Solver 校验；
- 把“已经调查”当成“必须继续调查”的理由。
