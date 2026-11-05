# DEVX-005 真实结果确定性复核

原始报告 `evaluation/artifacts/reports/reliability/runs/dev-v3.2-proposal-ready-next3-api.json` 永久保留：DEVX-005 原始评分为 FAIL。

失败不是 Agent 业务结果错误，而是 `content_consistency` 只取回复中的第一组“共 N 题/总分 N 分”，并无条件按 Preview after-state 校验。该回复明确同时陈述：

- 删除前：10 题、100 分；
- 删除后：9 题、90 分。

Tool Observation 的 before/after summary 与上述四项完全一致，Preview 最终命中 `remove_question(position=5)`，正式 Paper 未修改。因此原 Grader 把正确的 before-state 误判为 after-state，属于确定性 false negative。

修复后 Grader 会根据每个数字声明最近的“删除前/修改前/原卷”或“删除后/修改后/预览”限定词分别对照 persisted Paper 和 preview。未限定的 Preview 回复仍默认对照 after-state，没有降低 Oracle。

新增单测覆盖：正确 before/after 双组事实必须通过；错误 after 总分必须失败。

未再次调用模型 API。基于原始 Trace 的独立复评结果保存在：

`evaluation/artifacts/reports/reliability/runs/dev-v3.2-proposal-ready-next3-api.regraded.json`

复评：DEVX-004 PASS、DEVX-005 PASS、DEVX-011 PASS，3/3。

注意：DEVX-005 模型在同轮曾产生 3 次失败的错误地址 Preview 调用，均未落地副作用，随后通过重新读取恢复并成功创建 position=5 删除预览。现有 Oracle 允许无副作用恢复重试；该事实保留在原始 Trace 中。
