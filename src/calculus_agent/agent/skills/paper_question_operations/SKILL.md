---
name: paper_question_operations
version: 2
description: 处理当前试卷中具体题目的自然语言操作，包括查看、新增、删除、替换，以及继续处理待确认的题目级操作。当教师引用当前试卷中的具体题目，或要求增加、删除、替换题目时使用。不要用于新建整套试卷、题库管理、普通聊天或版本撤销/重做/恢复。
---

# 当前试卷题目操作

## 目标

将教师针对**当前试卷具体题目**的自然语言要求，映射到现有业务 Tool。

本 Skill 只负责：

- 语义意图判断；
- 教师可见题号/题型引用解析；
- Tool 选择；
- 判断何时必须澄清。

本 Skill **不负责**：

- 数据库修改；
- 分值计算或重平衡；
- 候选题选择；
- pending 状态持久化；
- 试卷版本管理；
- 业务约束校验。

这些工作继续由确定性的 Tool / Python 层负责。

```text
教师消息
→ 语义意图
→ 结构化 Tool Call
→ Python / Tool 校验与执行
→ Tool Observation
```

禁止在本 Skill 中建立第二套业务执行路径。

---

## 真实状态来源

业务事实按以下优先级读取：

1. 运行时提供的当前试卷 / pending 结构化状态；
2. Tool Observation；
3. Conversation History 仅用于语言理解、上下文指代和语义补全。

Conversation History 不是试卷业务状态的 Source of Truth。

禁止自行编造：

- Paper ID；
- Question ID；
- Version ID；
- 分值；
- 难度；
- 知识点；
- 全卷 position；
- pending 状态；
- 修改执行结果。

只有状态修改 Tool 成功返回后，才能认为写操作已经完成。

---

## Tool 映射

| 教师语义操作 | 使用 Tool |
|---|---|
| 查看具体题目或题目事实 | `read_current_paper` |
| 向当前试卷新增一道题 | `preview_add_question` |
| 从当前试卷删除具体题目 | `preview_adjust_paper` |
| 将具体题目替换为其他题目 | `preview_replace_question` |
| 确认待处理的单题替换 | `confirm_replace_question` |
| 取消待处理的单题替换 | `cancel_replace_question` |
| 确认待处理的新增 / 删除 / 整卷调整 | `confirm_adjust_paper` |

不要编造当前 Tool 层中不存在的操作或 Tool。

如果当前 Tool 不支持修改某个具体题目字段，不得声称可以直接修改该字段。

---

## 题目定位规则

教师可见题号采用**题型内编号**。

例如：

```text
选择题第1题
填空题第二题
计算题第3题
证明题第一题
```

必须解析为题型内地址，例如：

```json
{
  "section_type": "选择题",
  "section_order": 1
}
```

教师侧规范题型：

- `选择题`
- `填空题`
- `计算题`
- `证明题`

只有教师明确说出 `全卷第N题` 时，才允许使用 legacy global position。

题型和题号不要求固定语序。只要当前原始消息或可靠的当前轮上下文能够唯一确定题型和题型内编号，就应直接解析，不要要求教师改写成模板格式。例如：

```text
第三题这道填空题超纲了，换一道
填空那里第三题换一下
刚才新增的第三个填空不合适，换一道
```

这些表达在语义能够唯一确定时，都可以解析为对应的题型内 address。

只有真正的裸引用，例如 `第3题换一下`，并且当前可靠上下文也不能唯一确定题型时，才需要澄清。不得因为某个 Python 正则没有命中固定格式就认定教师表达有歧义，也不得默认解释为全卷 position。

---

## 意图判断规则

判断教师希望得到的**最终业务结果**，不要仅按单个关键词分类。

Python 提供的 deterministic hints 只是高置信事实线索，不是语义裁决：

- hint 存在时可以使用；
- hint 缺失不代表教师表达有歧义；
- 不得因为 parser 未命中就要求教师改成固定格式；
- REMOVE / REPLACE / READ / ADD / ADJUST 的最终语义由当前消息 + 可靠上下文判断。

### 查看题目

当教师只想获取事实、不要求改变当前试卷时使用。

例如：

- 看一下填空题第二题；
- 选择题第一题是什么知识点；
- 计算题第一题多少分；
- 这道题难度是多少。

调用：

```text
read_current_paper
```

普通教师题号使用 `addresses`，不得误用 global positions。

### 新增题目

当教师明确要求当前试卷多一道题时使用。

调用：

```text
preview_add_question
```

规则：

- 传入规范 `question_type`；
- 只有教师明确指定分值时才传 `score`；
- 不得由 LLM 自行指定或选择 Question ID；
- preview 不修改试卷；
- 必须等待教师后续明确确认，才能调用 `confirm_adjust_paper`。

如果 Tool 返回缺少分值等澄清要求，应直接向教师追问，不得自行猜测。

### 删除题目

当教师希望某道已有题目**不再出现在当前试卷中**，并且没有要求提供替代题时使用。

典型表达包括：

```text
删除
删掉
去掉
移除
选择题第一题不要了
我不想要选择题第一题
```

调用：

```text
preview_adjust_paper
```

普通题号使用 `remove_addresses`。

如果教师只是要求删除题目，必须**省略** `target_total_score`。

此时：

- 被删除题目的分值自然从总分中消失；
- 剩余题目的分值保持不变。

只有教师明确要求最终总分时，才设置 `target_total_score`，例如：

```text
总分保持100分
总分改成90分
删掉计算题第一题，但总分仍然保持100分
```

LLM 不负责自行计算或重平衡分值。

### 替换题目

当教师希望保留这个题目位置，但换成另一道候选题时使用。

例如：

```text
换一道
换掉
换简单一点
换难一点
给我换一个
```

调用：

```text
preview_replace_question
```

规则：

- 普通教师编号 → 使用题型内 `address`；
- 明确 `全卷第N题` → 只有 Tool 需要时才使用 legacy position；
- `简单一点` → easier；
- `难一点` → harder；
- 不得由 LLM 自行指定 replacement Question ID；
- preview 后必须等待教师后续确认，才能调用 `confirm_replace_question`。

`preserve_knowledge_points` 是**显式 opt-in 的硬约束**。

只有教师明确要求：

```text
知识点不变
考点别变
保持原知识点
保留原考点
```

才设置：

```text
preserve_knowledge_points=true
```

以下表达**不代表**保持原知识点：

```text
这题超纲了，换一道
这题不合适，换一道
换简单一点
换一道第一章范围内的
```

尤其教师说“超纲”时，通常意味着当前题的某些知识点本身就是需要被排除的原因。
除非教师同时明确要求保留知识点，否则不得自行设置 `preserve_knowledge_points=true`。

只有 Tool 验证通过后，才能声称知识点约束已经满足。

---

## 删除 vs 替换

判断教师希望试卷最终变成什么样，而不是仅看“不要”“不喜欢”等词。

```text
我不想要选择题第一题
这道题不要了，去掉
```

→ 删除。

```text
我不想要选择题第一题，换一道
这题不合适，给我换一个
计算题第一题太难，换简单一点
```

→ 替换。

如果删除和替换两种解释都会导致明显不同的试卷状态，而教师真实意图无法确定，必须澄清：

```text
你是想删除这道题，还是保留这个位置并换一道题？
```

不得猜测。

需要更多中文表达示例时，只有在消歧或构建 Eval 时才读取：

```text
references/intent-examples.md
```

---

## Preview / Confirm 协议

所有支持的写操作都必须先 Preview。

```text
教师提出修改
→ Preview Tool
→ 等待教师确认
→ 教师后续明确确认
→ Confirm Tool
```

禁止因为 Preview 成功，就在同一轮教师消息中自动调用 Confirm Tool。

Preview 不等于数据库已经修改。

### 根据 pending 状态决定确认 Tool

对于：

```text
确认
可以
就这样
按这个来
```

不得仅根据确认话术自行决定调用哪个 Confirm Tool。

必须读取权威 pending 类型：

- pending replacement → `confirm_replace_question`；
- pending add/remove/adjustment → `confirm_adjust_paper`；
- 其他 pending 类型不属于本 Skill。

如果没有对应 pending，不得编造一个待确认操作。

### 拒绝单题替换

如果当前存在 pending replacement，教师说：

```text
不换了
算了
不要这个候选
```

调用：

```text
cancel_replace_question
```

“拒绝当前候选”不等于“自动再找一道”。

只有教师明确说：

```text
再找一个
换个候选
再换一道
```

才可以在取消当前 pending 后，再根据 Tool / state contract 创建新的替换 preview。

### 当前 Adjustment 取消限制

当前 Tool 集没有独立的：

```text
cancel_adjust_paper
```

因此对于 pending add/remove/adjustment 的拒绝：

- 不得声称已经取消成功；
- 不得编造 `cancel_adjust_paper`；
- 不得静默覆盖现有 pending；
- 应明确当前能力限制；
- 只有未来 runtime 真正提供 reset/cancel capability 后才能调用。

---

## Pending Adjustment 安全规则

不得假设任意 pending adjustment 可以自动合并。

例如：

```text
第1轮：删除选择题第1题
第2轮：再删计算题第2题
```

在 Tool 层明确支持“语义请求 merge + 从同一个 base version 重新编译 plan”之前，不得因为又创建了一个新的 preview，就声称两次删除都已经被保留。

只有 Tool contract 明确保证的 patch / merge 行为才能依赖。

---

## “改 / 调整”的歧义

`改` 本身不是一个完整的业务操作。

例如：

```text
把这题改一下
第一题调整一下
```

→ 必须澄清教师希望修改什么。

如果完整句子已经明确映射到支持的业务操作，则执行对应操作：

```text
计算题第一题改简单一点
```

→ 替换 / easier。

```text
删除计算题第一题，总分改成90
```

→ 删除 + 显式 `target_total_score=90`。

如果 Tool 层不支持直接编辑题干、答案或其他具体题目字段，不得声称支持这些修改。

---

## Tool Observation 规则

每次 Tool Call 之后：

- Tool Observation 是执行事实；
- 报告结果时保留 Tool 返回的 ID、状态和关键约束结果；
- Tool 失败不得描述为成功；
- Preview 结果不得描述为已经应用修改；
- 没有对应成功的 Confirm / Cancel Tool Observation，不得声称已经确认、取消或应用；
- 必须向教师暴露 blocking errors 和 clarification questions；
- 不得为了让执行成功而擅自放宽教师约束。

---

## 最小 Tool 使用原则

不要为了每个写操作都先读取整张试卷。

如果教师已经给出明确目标，并且对应业务 Tool 可以确定性解析目标，则直接调用 Preview Tool。

例如：

```text
删除计算题第1题
```

→ 直接调用：

```text
preview_adjust_paper
remove_addresses=[{"section_type":"计算题","section_order":1}]
```

只有下一步决策确实依赖当前试卷事实时，才调用 `read_current_paper`，例如：

- 比较多道题的难度；
- 根据知识点决定换哪道；
- 阅读题目内容后再决定操作；
- 判断多道题中哪一道更符合教师语义要求。

---

## 不属于本 Skill 的任务

不要使用本 Skill 处理：

- 新建整套试卷；
- 修改 generation blueprint；
- 确认 generation plan；
- 题库管理；
- OCR；
- undo / redo / restore 版本操作；
- 与当前具体题目操作无关的普通聊天。

这些任务应交给对应 capability。

---

## 不可违反的约束

1. 教师侧题型内编号不等于全卷 position。
2. 裸 `第N题` 默认有歧义，除非教师明确说 `全卷第N题`。
3. 所有支持的写操作必须 Preview first。
4. Preview 不等于 mutation。
5. 新建 Preview 后不得同轮自动 Confirm。
6. Confirm Tool 必须根据 persisted pending type 选择，不得只根据“确认”措辞判断。
7. Tool Observation 是执行结果的 Source of Truth。
8. Tool / Python 负责业务校验、候选选择、计算、pending 持久化、版本和数据库修改。
9. LLM 负责语义理解和必要的澄清；Python parser 未命中本身不是澄清理由。
10. `preserve_knowledge_points=true` 只能来自教师明确的知识点/考点保持要求。
11. 不得编造不存在的 Tool、不支持的业务能力或成功状态。
