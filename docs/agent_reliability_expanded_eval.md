# Teacher Agent Expanded Eval

本报告将真实模型验收与确定性故障注入分开统计；两套数据集不是同一输入，不能作为 Variant 横向对比。

## 结果

| Suite | Cases | Passed | Pass rate | Model |
| --- | ---: | ---: | ---: | --- |
| Teacher Acceptance v1 | 20 | 20 | 100% | Qwen/Qwen3.5-35B-A3B, temperature 0 |
| Failure Injection v1 | 20 | 20 | 100% | Deterministic scripted backends |
| **Total** | **40** | **40** | **100%** | Mixed live/deterministic |

### Teacher Acceptance 分类

| Category | Passed / Cases |
| --- | ---: |
| TeachingDesign | 6 / 6 |
| Generation | 5 / 5 |
| Paper modification | 2 / 2 |
| Pending lifecycle | 2 / 2 |
| Consultation | 1 / 1 |
| Confirmation | 1 / 1 |
| Explicit Paper target | 1 / 1 |
| Paper read | 1 / 1 |
| Error handling | 1 / 1 |

### Failure Injection 分类

| Category | Passed / Cases |
| --- | ---: |
| Error handling | 10 / 10 |
| Idempotency | 4 / 4 |
| Confirmation boundary | 3 / 3 |
| Capability boundary | 2 / 2 |
| Explicit Paper target | 1 / 1 |

## 新增覆盖

- Tool 调用顺序、调用次数、错误码与模型调用上限 grader。
- hidden/unknown Tool、缺失 Paper target、无 pending confirmation。
- Tool/Model timeout、malformed arguments/response/ToolResult。
- operation ID 重放、冲突、非法长度及单轮重复 mutation。
- TeachingDesign 咨询/创建/确认/放弃、多章节与章节主题连写。
- 多章节组卷、显式总题数与总分、无目标 Paper 查询和只读分析。

## Eval 发现并修复的问题

扩展后的首次 Live run 为 16/20；新增 case 暴露并修复了四个问题：

1. “期中复习方案”被错误路由为组卷。
2. Active TeachingDesign 未暴露 discard Tool。
3. 模型遗漏紧凑表达中的 `12题100分` 时，程序未恢复教师显式约束。
4. 无明确 Paper target 的查询可能由模型直接返回 `completed`。

修复后重新运行完整 20-case Live suite，结果为 20/20。

## 可复现输入

- `tests/evals/cases/teacher_acceptance_v0.yaml`
- Dataset version: `57d83dea3665`
- Report: `tests/evals/reports/state-policy-v1.json`
- `tests/evals/cases/reliability_failure_injection_v0.yaml`
- Dataset version: `e052996d4590`
- Report: `tests/evals/reports/failure-injection-state-policy.json`

## 限制

这是一次 40-case、实现相邻的回归评测。Live suite 当前只有一次完整模型运行；100% 表示这些固定 case 全部通过，不代表未知生产输入上的通用可靠性。下一步应进行 3–5 次重复 Live run，并报告均值、最差值和方差。
