# EVAL-031 Outcome Integrity 复评

## 原始结果保留

`evaluation/artifacts/reports/reliability/runs/dev-v3.2-dev122-batch30-first-api.json` 保持原始 27/30，不覆盖。

EVAL-031 的业务状态和 Tool 结果均符合 Oracle：

- `prepare_generation_plan` 成功；
- `status=waiting_confirmation`；
- Pending 为第一章 canonical scope、4题、40分；
- 未持久化正式 Paper。

原回复同时写明“试卷草案”“等待您确认是否正式生成试卷”。旧 `outcome_integrity` 仅识别“方案/预览/待确认/请确认”，因“草案已生成”中的“已生成”误判为正式 Paper 完成声明。

## 确定性修复

`evaluation/reliability/evaluators/outcome.py` 现在：

1. 识别“草案”和“等待……确认”为 Pending Artifact 上下文；
2. 仅当运行状态确实为 `waiting_confirmation` 时允许该上下文排除正式 Paper 完成声明；
3. `completed` 状态或缺少 Draft/Pending 语境的“试卷已生成”仍会失败。

新增正向、反向和边界测试见 `tests/evaluation/reliability/test_outcome_grader.py`。

## 复评结果

- 复评不调用 API：`api_calls=0`。
- 唯一变化：EVAL-031 FAIL → PASS。
- Batch 30 有效结果：28/30（93.3%）。
- 复评报告：`evaluation/artifacts/reports/reliability/runs/dev-v3.2-dev122-batch30-first-api.regraded.json`。
