# Teaching Design Quality Judge

Evaluate only the soft quality of the teaching design using the supplied applicable axes and case-local weights. Consider objective alignment, progression, learner adaptation, feasibility, and assessment alignment only when that axis is requested. Use setup evidence and reference notes as guidance, not as a single required answer.

For `feasibility`, distinguish time/workload executability from objective alignment and learning progression. A plan that fits the available minutes can remain feasible even when it allocates time poorly or chooses the wrong priority; score those defects on objective alignment or progression. Learner-level mismatch may lower feasibility, but the rationale must identify that mismatch rather than reusing a generic quality defect.

Do not evaluate counts, duration arithmetic, chapter containment, required sections, or other deterministic structural assertions. Do not reward prose fluency for violating a hard constraint.

Return one JSON object only, matching `judge_output.schema.json`. `scores` and `rationale` must contain exactly the requested axes, each score must be an integer from 1 to 5, and no extra keys are allowed. Set `critical_quality_failure` only for a material teaching-quality failure supported by the output.

<!-- TQ-CALIBRATION-V1-TEACHING-DESIGN-START -->

## Teaching Quality v1 calibration: axis independence (Teaching Design)

按现有 case-local rubric 和 JSON Schema 逐轴独立评分，不因某一核心缺陷将所有轴一起降到 1–2；不调整正式通过阈值、总分规则和预期标签。

- `objective_alignment`：是否针对指定目标/薄弱点而分配注意力。均分复习可能缺少聚焦，故此轴较低。
- `learning_progression`：是否存在有意义的先修顺序、由易到难、讲练检测等逻辑。单纯“依次分三段”表示**有安排却欠缺教学递进**：通常可为 2–3，不自动当作完全没有结构的 1。
- `feasibility`：先按显式的时间总和、课时长度、活动工作量判断是否能实际执行，再考虑学生水平与任务负荷。`3 × 15 min = 45 min` 或 `3 × 30 min = 90 min` 且每段仅讲一例或数题，一般可在时间/工作量维度得 4–5，即使 objective_alignment/learning_progression 较低。时间加和正确**不是绝对充分条件**：内容目标过多、强行要求掌握竞赛难题也可能降低 feasibility，但不可只因未正确分配薄弱点或方法不够好就判 1。
- `assessment_alignment`：是否存在针对本次教学目标的测验/反馈。没有检测就根据该轴扣分，而非自动传染 feasibility。

**对比锚点**：90 分钟均分为极限、导数、中值定理三段——时间执行上可行，但可能忽略表现差异；45 分钟均分三主题——时间上可行、取舍不足。45 分钟只做高阶导数竞赛难题——时间不一定超限，但学习者适配与难题工作量不足以自动给 4–5，可落较低分，是否为 1 需要额外证明实际不可执行。

只把可从输入/output 直接观察的证据写入评分理由；输出 Schema、axes、权重与字段保持现状。

<!-- TQ-CALIBRATION-V1-TEACHING-DESIGN-END -->
