# 题目操作意图示例

此文件用于开发、调试和 Eval，不要求每次模型调用都加载。

| 教师表达 | 期望意图 | 期望 Tool / 行为 |
|---|---|---|
| 看一下选择题第一题 | READ | read_current_paper |
| 选择题第一题是什么知识点 | READ | read_current_paper |
| 加一道填空题 | ADD | preview_add_question |
| 再加一道5分计算题 | ADD | preview_add_question(score=5) |
| 删除计算题第一题 | REMOVE | preview_adjust_paper(remove_addresses=...) |
| 我不想要选择题第一题 | REMOVE | preview_adjust_paper |
| 我不想要选择题第一题，换一道 | REPLACE | preview_replace_question |
| 计算题第一题换简单一点 | REPLACE | preview_replace_question(difficulty_direction=easier) |
| 这题知识点别动，换一道 | REPLACE | preserve_knowledge_points=true |
| 第3题删掉 | AMBIGUOUS | needs_clarification |
| 把这题改一下 | AMBIGUOUS | needs_clarification |
| 这题不太行 | AMBIGUOUS | needs_clarification |
| 删除填空题第二题，总分保持100 | REMOVE+TOTAL | preview_adjust_paper(target_total_score=100) |
| 删除填空题第二题 | REMOVE | 不传 target_total_score |
| 确认 | PENDING-DEPENDENT | 先根据 persisted pending 类型选 confirm Tool |
