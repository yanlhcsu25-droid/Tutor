# Teaching Quality v1 — Judge Calibration Error Analysis

> Scope: second fixed-sample Dev-only calibration run. No new model call was made for this report. Blind and Tutor workflows were not executed.

## 1. Inputs and run identity

- Behavioral labels: `approved_dev_calibration` (21 fixed outputs).
- Semantic labels: `review_required_semantic_contrast` (15 binary fixtures).
- Provider/model: `siliconflow / Qwen/Qwen3.5-35B-A3B`.
- Parameters: `temperature=0`, `max_tokens=2048`.
- Analyzed report: `reports/judge-calibration-second-dev-fixed.json`.
- Report SHA: `f6e36c58bfba5bba792441475d43adc2235dac7f7168028f3890b895ed7919ee`.

## 2. Semantic Contrast：15 条逐项结果

| Sample | Type / kind | 原文 | 预期 | 实际 | Match | 模型理由 |
|---|---|---|---:|---:|---:|---|
| `SC-RC-01` | `required_content` / `clear_pass` | 学生把间断点分错，通常是没有分别检查左右极限和函数值。下一节先用两个反例区分三者，再做分类练习并用一道新题检测。 | True | True | ✅ | The response provides a causal mechanism for the error (failing to check left/right limits and function value) and offers concrete teaching activities (counterexamples, classification exercises, new problem) as required. |
| `SC-RC-02` | `required_content` / `clear_fail` | 下一节多做几道间断点题，认真一点就会提高。 | False | False | ✅ | The response provides a concrete activity ('do more problems') but fails to provide a causal analysis explaining the error mechanism; the phrase 'be more serious' is not a valid causal explanation. |
| `SC-RC-03` | `required_content` / `boundary` | 她可能把左右极限相等误当成函数连续。可以先让她比较左右极限、函数值三张卡片，再完成一道分类题确认。 | True | False | ❌ | The response states a possible misconception but fails to provide a causal analysis explaining the error mechanism (e.g., why equating limits implies continuity is a logical error), which is required for the '原因分析' assertion. |
| `SC-FC-01` | `forbidden_content` / `clear_pass` | 本节只复习极限与连续性，不讲定积分。 | True | True | ✅ | The response explicitly excludes the forbidden topic '定积分' using a boundary-setting statement ('不讲定积分'), which is permitted under the 'mention_allowed' rule for the 'forbidden_content' assertion type. |
| `SC-FC-02` | `forbidden_content` / `clear_fail` | 下一节先重点学习定积分的定义和计算，再回头复习极限。 | False | False | ✅ | The response explicitly recommends teaching the forbidden topic '定积分' in the next lesson, which violates the 'instructional_use' violation mode even though 'mention_allowed' is true, as the mention is not exclusionary or boundary-setting. |
| `SC-FC-03` | `forbidden_content` / `boundary` | 可以告诉学生“定积分”是后续章节名称，但本节不介绍定义、方法或题目。 | True | True | ✅ | The response explicitly restricts the mention of '定积分' to a boundary-setting context (identifying it as a future chapter name while stating it is not introduced in the current section), which complies with the `mention_allowed` rule for `instructional_use` violation mode. |
| `SC-SG-01` | `source_grounded` / `clear_pass` | 中值定理正确率最低且条件检查缺失，应优先训练使用条件判断。 | True | True | ✅ | The response's claim about low accuracy on the Mean Value Theorem is supported by the setup evidence of 1 correct out of 3 attempts, and the claim about missing condition checks is directly supported by the setup evidence stating the student 'does not know how to check usage conditions'. |
| `SC-SG-02` | `source_grounded` / `clear_fail` | 学生是因为考试焦虑和计算粗心才做错，中值定理本身已经掌握。 | False | False | ✅ | The response asserts the student has mastered the Mean Value Theorem, but the setup evidence only shows 1 correct answer out of 3 problems, which does not substantiate a conclusion of mastery. |
| `SC-SG-03` | `source_grounded` / `boundary` | 现有证据支持先做一次链式法则短纠错；是否形成稳定错误还需下一题核实。 | True | True | ✅ | The response's claim that evidence supports a chain rule correction is substantiated by the setup evidence of '出现一次漏乘内层导数' (once missed multiplying by the inner derivative), which is a specific chain rule error mechanism. |
| `SC-RU-01` | `reference_used` / `clear_pass` | 直接做20道难题会掩盖基础错因。先用3题诊断，再按错因分层练习。 | True | True | ✅ | The response explicitly engages with the referenced plan '直接做20道难题' by rejecting it and providing a causal explanation for why it is ineffective. |
| `SC-RU-02` | `reference_used` / `clear_fail` | 建议学生认真听课、多练习并整理错题。 | False | False | ✅ | The response does not materially engage with the reference plan '直接做20道难题' (directly do 20 difficult problems); it offers generic advice without rejecting, revising, or addressing the specific reference content. |
| `SC-RU-03` | `reference_used` / `boundary` | 关于20道题的方案，建议合理安排时间，做完以后及时总结。 | False | False | ✅ | The response mentions the task of 20 questions but fails to materially engage with the specific reference plan '直接做20道难题' (directly do 20 difficult problems) by either adopting, rejecting, or revising it with a reason; it only offers generic advice on time management and summarization. |
| `SC-ID-01` | `insufficiency_disclosed` / `clear_pass` | 题库中符合条件的难题只有8道，无法按原约束提供15道。请确认是减少题量还是调整约束。 | True | True | ✅ | The response explicitly states the shortage (8 available vs 15 requested) and preserves the constraint boundary by refusing to fabricate the missing 7 questions. |
| `SC-ID-02` | `insufficiency_disclosed` / `clear_fail` | 已按要求生成15道第三章难题。 | False | False | ✅ | The response claims to have generated 15 items despite the setup evidence stating only 8 are available, which fabricates missing supply and silently relaxes the constraint boundary rather than disclosing the insufficiency. |
| `SC-ID-03` | `insufficiency_disclosed` / `boundary` | 题库只有8道符合条件；我另外补写7道新题，已经凑齐15道，不需要调整。 | False | False | ✅ | The response fabricates missing supply ('补写7道新题') to resolve the shortage, violating the constraint boundary that requires disclosing the shortage without inventing resources. |

### Semantic 错误结论

- 14/15 与候选二元标签一致。唯一错误为 `SC-RC-03`。
- `SC-RC-03` 的回答明确提出“把左右极限相等误当成连续”这一具体 misconception，并给出比较卡片和分类题；模型额外要求解释“为什么该推理在逻辑上错误”。
- 初步归因：**prompt 对“原因分析”的门槛过严**；同时该样本仍是未完成人工确认的 boundary 标签，因此保留一项 **reference-label boundary ambiguity**，不能把 93.33% 当正式准确率。

## 3. Behavioral：21 条 axis 结果

| Sample | Band 预期→实际 | Expected axis ranges | Actual scores | 偏差维度 |
|---|---|---|---|---|
| `TQ-CQ-005-strong` | `strong→strong` | `correctness=4–5`; `actionability=4–5`; `pedagogical_quality=4–5`; `grounding=4–5`; `clarity=4–5` | `correctness=5`; `actionability=5`; `pedagogical_quality=5`; `grounding=5`; `clarity=5` | — |
| `TQ-CQ-005-borderline` | `borderline→poor` | `correctness=3–4`; `actionability=3–4`; `pedagogical_quality=2–3`; `grounding=2–3`; `clarity=4–5` | `correctness=3`; `actionability=2`; `pedagogical_quality=2`; `grounding=3`; `clarity=4` | `actionability` |
| `TQ-CQ-005-poor` | `poor→poor` | `correctness=1–2`; `actionability=3–4`; `pedagogical_quality=1–2`; `grounding=1–2`; `clarity=4–5` | `correctness=1`; `actionability=1`; `pedagogical_quality=1`; `grounding=1`; `clarity=4` | `actionability` |
| `TQ-CQ-006-strong` | `strong→strong` | `correctness=4–5`; `actionability=4–5`; `pedagogical_quality=4–5`; `grounding=4–5`; `clarity=4–5` | `correctness=5`; `actionability=5`; `pedagogical_quality=5`; `grounding=5`; `clarity=5` | — |
| `TQ-CQ-006-borderline` | `borderline→borderline` | `correctness=3–4`; `actionability=3–4`; `pedagogical_quality=2–3`; `grounding=3–4`; `clarity=4–5` | `correctness=3`; `actionability=4`; `pedagogical_quality=3`; `grounding=3`; `clarity=5` | — |
| `TQ-CQ-006-poor` | `poor→poor` | `correctness=1–2`; `actionability=3–4`; `pedagogical_quality=1–2`; `grounding=1–2`; `clarity=4–5` | `correctness=1`; `actionability=2`; `pedagogical_quality=1`; `grounding=1`; `clarity=4` | `actionability` |
| `TQ-CQ-010-strong` | `strong→strong` | `correctness=4–5`; `actionability=4–5`; `pedagogical_quality=4–5`; `grounding=4–5`; `clarity=4–5` | `correctness=5`; `actionability=5`; `pedagogical_quality=5`; `grounding=5`; `clarity=5` | — |
| `TQ-CQ-010-borderline` | `borderline→poor` | `correctness=3–4`; `actionability=3–4`; `pedagogical_quality=2–3`; `grounding=2–3`; `clarity=4–5` | `correctness=2`; `actionability=1`; `pedagogical_quality=1`; `grounding=1`; `clarity=3` | `correctness`, `actionability`, `pedagogical_quality`, `grounding`, `clarity` |
| `TQ-CQ-010-poor` | `poor→poor` | `correctness=1–2`; `actionability=2–3`; `pedagogical_quality=1–2`; `grounding=1–2`; `clarity=3–4` | `correctness=2`; `actionability=1`; `pedagogical_quality=1`; `grounding=1`; `clarity=4` | `actionability` |
| `TQ-PQ-003-strong` | `strong→strong` | `knowledge_coverage=4–5`; `difficulty_alignment=4–5`; `question_type_balance=4–5`; `redundancy=4–5`; `pedagogical_validity=4–5` | `knowledge_coverage=5`; `difficulty_alignment=4`; `question_type_balance=5`; `redundancy=5`; `pedagogical_validity=5` | — |
| `TQ-PQ-003-borderline` | `borderline→borderline` | `knowledge_coverage=3–4`; `difficulty_alignment=2–3`; `question_type_balance=2–3`; `redundancy=2–3`; `pedagogical_validity=2–3` | `knowledge_coverage=5`; `difficulty_alignment=4`; `question_type_balance=2`; `redundancy=5`; `pedagogical_validity=3` | `knowledge_coverage`, `difficulty_alignment`, `redundancy` |
| `TQ-PQ-003-poor` | `poor→poor` | `knowledge_coverage=1–2`; `difficulty_alignment=1–2`; `question_type_balance=1–2`; `redundancy=1–2`; `pedagogical_validity=1–2` | `knowledge_coverage=2`; `difficulty_alignment=2`; `question_type_balance=1`; `redundancy=1`; `pedagogical_validity=1` | — |
| `TQ-PQ-011-strong` | `strong→strong` | `redundancy=4–5`; `pedagogical_validity=4–5` | `redundancy=5`; `pedagogical_validity=5` | — |
| `TQ-PQ-011-borderline` | `borderline→poor` | `redundancy=3–4`; `pedagogical_validity=4–5` | `redundancy=2`; `pedagogical_validity=3` | `redundancy`, `pedagogical_validity` |
| `TQ-PQ-011-poor` | `poor→poor` | `redundancy=1–2`; `pedagogical_validity=1–2` | `redundancy=1`; `pedagogical_validity=2` | — |
| `TQ-TD-007-strong` | `strong→strong` | `objective_alignment=4–5`; `learning_progression=4–5`; `feasibility=4–5`; `assessment_alignment=4–5` | `objective_alignment=5`; `learning_progression=5`; `feasibility=4`; `assessment_alignment=5` | — |
| `TQ-TD-007-borderline` | `borderline→poor` | `objective_alignment=2–3`; `learning_progression=2–3`; `feasibility=4–5`; `assessment_alignment=2–3` | `objective_alignment=2`; `learning_progression=2`; `feasibility=2`; `assessment_alignment=2` | `feasibility` |
| `TQ-TD-007-poor` | `poor→poor` | `objective_alignment=1–2`; `learning_progression=1–2`; `feasibility=1–2`; `assessment_alignment=1–2` | `objective_alignment=1`; `learning_progression=1`; `feasibility=1`; `assessment_alignment=1` | — |
| `TQ-TD-016-strong` | `strong→strong` | `objective_alignment=4–5`; `learning_progression=4–5`; `feasibility=4–5` | `objective_alignment=5`; `learning_progression=5`; `feasibility=5` | — |
| `TQ-TD-016-borderline` | `borderline→poor` | `objective_alignment=2–3`; `learning_progression=2–3`; `feasibility=4–5` | `objective_alignment=2`; `learning_progression=1`; `feasibility=1` | `learning_progression`, `feasibility` |
| `TQ-TD-016-poor` | `poor→poor` | `objective_alignment=1–2`; `learning_progression=1–2`; `feasibility=2–3` | `objective_alignment=1`; `learning_progression=1`; `feasibility=1` | `feasibility` |

### Behavioral 汇总

- Valid structured outputs: **21/21**.
- Band Accuracy: **76.19%**.
- Axis Agreement: **79.31%**.
- Ranking Consistency: **85.71%**.
- 10 个 sample 至少存在一个 axis 或 band 错误；其中共有 **18 个 axis-range 偏差**。

## 4. 10 个 Judge Error 逐条分析

### 4.1 `TQ-CQ-005-borderline`

- 输出：先复习拉格朗日中值定理的条件和公式，再讲一道证明例题，然后让学生做两道类似题。提醒学生看到导数时考虑中值定理。
- 偏差：`actionability` 预期 3–4，实际 2。
- 分析：方案有明确顺序、例题和两道练习，执行信息足以达到基本可用；缺陷是没有真正训练方法识别。Judge 将 `objective/pedagogy` 缺陷重复扣到 `actionability`。
- 归因：**prompt 的 axis independence 不足**；actionability anchor 也需明确“具体但教学策略一般”仍可得 3。

### 4.2 `TQ-CQ-005-poor`

- 输出：让学生把拉格朗日中值定理抄写十遍并背熟公式。证明题不会时就先套用定理，做得多了自然会判断。
- 偏差：`actionability` 预期 3–4，实际 1。
- 分析：“抄十遍、不会就套定理”错误但非常具体可执行。Judge 把 correctness/pedagogy 的严重失败复制到 actionability。
- 归因：**prompt 主因，rubric anchor 次因**。

### 4.3 `TQ-CQ-006-poor`

- 输出：合理。难题做得越多提高越快，直接完成20道并对答案即可，不需要先检查基础。
- 偏差：`actionability` 预期 3–4，实际 2。
- 分析：“做20道并对答案”有明确动作，但建议错误。Judge 仍在用“建议是否正确”代替“是否具体可执行”。
- 归因：**prompt 的维度解耦问题**。

### 4.4 `TQ-CQ-010-borderline`

- 输出：45分钟平均复习极限、导数和中值定理，每部分15分钟，各讲一个错题并布置练习。
- 偏差：5 个轴全部偏低：`correctness 2<3–4`、`actionability 1<3–4`、`pedagogical_quality 1<2–3`、`grounding 1<2–3`、`clarity 3<4–5`。
- 分析：平均分配确实没有利用表现差异，但没有事实错误；“每部分15分钟、各讲一个错题并布置练习”清楚且可执行，也至少引用了三个已知主题。Judge 将“未做优先级”当作全轴失败。
- 归因：**prompt 的全局惩罚/重复扣分是主因**；grounding、clarity 的中低分 anchor 还不够具体。

### 4.5 `TQ-CQ-010-poor`

- 输出：学生整体基础不好，下一节课把第一章所有定义重新讲一遍，多做题、多总结；之后再看其他内容。
- 偏差：`actionability` 预期 2–3，实际 1。
- 分析：方案泛化且不针对证据，但仍给出“重讲第一章定义、再练习”的最低限度行动方向，因此不是完全没有动作。
- 归因：**rubric 对 actionability=1 与 2 的边界不清**。

### 4.6 `TQ-PQ-003-borderline`

- 输出：10题卷：第一章4道极限计算，第二章4道求导计算，第三章2道中值定理计算。前6题简单，后4题中等，全部采用计算或解答题。
- 偏差：`knowledge_coverage 5>3–4`、`difficulty_alignment 4>2–3`、`redundancy 5>2–3`。
- 分析：三章名义覆盖存在，但深度和比例一般；没有难题，不符合期中区分度；4道极限计算和4道求导计算存在方法重复风险。Judge 过度依赖“覆盖了三个章”和“题目被标为简单/中等”。
- 归因：**prompt 需要要求基于真实题目结构判断深度、难度和方法重复**；reference label 无明显问题。

### 4.7 `TQ-PQ-011-borderline`

- 输出：8题覆盖极限、连续、求导和中值定理；其中两道极限题都要先有理化，但一个趋于0、一个趋于无穷，其余题方法不同。
- 偏差：`redundancy` 预期 3–4，实际 2；`pedagogical_validity` 预期 4–5，实际 3。
- 分析：仅两道极限题共享有理化，其他六题方法不同。局部重复不应使整卷教学测评价值同步大幅下降。
- 归因：**prompt 未隔离局部 redundancy 与整体 pedagogical validity**；rubric 可增加“局部重复但整体仍有效”的 4 分 anchor。

### 4.8 `TQ-TD-007-borderline`

- 输出：90分钟平均分成三段：30分钟复习极限公式，30分钟做导数综合题，30分钟讲中值定理。每部分先讲知识，再做几道题。
- 偏差：`feasibility` 预期 4–5，实际 2。
- 分析：90分钟恰好分配完，内容规模也可执行；问题是未按证据取舍，不是时间不可行。Judge 将 objective alignment 缺陷重复扣到 feasibility。
- 归因：**prompt 的 axis independence 问题**；feasibility 必须先判断时间/规模，再判断学生适配。

### 4.9 `TQ-TD-016-borderline`

- 输出：45分钟内极限、连续性、高阶导数各讲15分钟，每部分讲一个例题，尽量都覆盖到。
- 偏差：`learning_progression` 预期 2–3，实际 1；`feasibility` 预期 4–5，实际 1。
- 分析：平均分配没有优先级，递进弱，但仍有可理解的三段结构；45分钟严格闭合。Judge 将“没有取舍”扩散成“完全无结构、完全不可执行”。
- 归因：**prompt 主因**；learning progression 的 1/2 边界也需更明确。

### 4.10 `TQ-TD-016-poor`

- 输出：高阶导数正确率最低，所以45分钟全部做高阶导数竞赛难题；极限和连续性让学生课后自己看。
- 偏差：`feasibility` 预期 2–3，实际 1。
- 分析：竞赛难题与补基础目标严重错配，但“45分钟只做一个主题”在时间层面可执行。现有 feasibility rubric 同时包含时间、工作量和学习者适配，模型选择了最严重维度直接给1。
- 归因：**rubric composite-axis ambiguity**，不是参考标签明显错误。

## 5. 根因分类

### 5.1 Reference label

- 21 条 Behavioral 标签已人工批准；本轮没有证据支持改标签。
- Semantic 的 `SC-RC-03` 仍是 review-required boundary，存在“具体 misconception 是否已构成原因分析”的边界歧义；应先人工确认，再计算正式 semantic accuracy。

### 5.2 Rubric

- `actionability` 未明确与 correctness/pedagogy 正交，导致“错误但具体”的建议被打成1–2。
- `feasibility` 混合时间可执行性、内容规模、难度适配；对 TD-016-poor 可以产生 1 或 2–3 两种合理解释。
- `learning_progression` 缺少“有顺序但无优先级”的 2–3 分 anchor。
- `pedagogical_validity` 缺少“局部重复、整体仍有效”的 4 分 anchor。

### 5.3 Prompt

- 最大问题是未强制逐轴独立取证，单个核心缺陷被重复扣到多个轴。
- Paper Judge 对章节出现、难度标签和题型名称过度信任，没有充分检查实质深度、方法重复和真实区分度。
- Semantic required_content prompt 对“原因分析”要求过深，未承认具体 misconception identification 本身可以构成基础原因分析。

## 6. 最小修改方案（不改标签、不碰 Blind、不调用模型）

1. **Behavioral 公共输出指令**：要求每个 axis 使用独立证据；禁止因为一个缺陷自动下调所有轴；rationale 必须说明该轴独有的判断依据。
2. **Consultation prompt**：明确 actionability 只评具体性与可执行步骤；“具体但错误”可以高 actionability、低 correctness/pedagogy。
3. **Teaching Design prompt**：明确 feasibility 先报告时间和规模是否可执行，再考虑学习者适配；平均分配可以 feasible，但 objective/progression 较低。
4. **Paper prompt**：要求忽略自报难度标签，按题目认知要求判断；分别评价局部 redundancy 与整体 pedagogical validity。
5. **Rubric anchor 补充，不改阈值**：为上述 actionability、feasibility、learning_progression、pedagogical_validity 各增加一个对比例。
6. **Semantic prompt**：将“识别出有证据的具体 misconception，并给出对应纠正活动”定义为基础原因分析；保留 `SC-RC-03` 标签供人工最终确认。
7. 完成以上文本修改后，先做静态 diff 审核；本报告阶段不运行任何新模型。

## 7. 错误责任边界

- `judge_error`：模型返回合法结构，但 axis/band 与参考范围不一致。
- `semantic_assertion_error`：模型返回合法二元判定，但与参考 PASS/FAIL 不一致。
- `provider_error`：超时、限流、网络失败、Malformed JSON 或 schema-invalid output。
- 第二轮有效运行中 provider error 为 0；第一轮 21 个 Behavioral schema-invalid outputs 属于 provider/output-contract failure，不计入 Agent 失败。
