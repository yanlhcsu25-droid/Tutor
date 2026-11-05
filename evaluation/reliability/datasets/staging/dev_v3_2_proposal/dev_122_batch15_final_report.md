# Teacher Agent Dev V3.2 — Dev 122 分层 Batch 15 综合评测

## 1. 选择与离线预检

选择清单及运行前理由：

- `evaluation/reliability/datasets/staging/dev_v3_2_proposal/dev_122_batch15_selection.md`
- `evaluation/reliability/datasets/staging/dev_v3_2_proposal/dev_122_batch15_preflight.json`

离线预检结果：**15/15 PASS**。每类 3 条；所有条目 `review.open_issues=[]`，真实 CaseLoader 加载、隔离 Fixture 初始化、已实现 Grader 合同和 DEVX 定义指纹去重均通过。没有选择仍带地址语义、Fixture、弱 Oracle 或教学质量 Judge 前置条件的 Case。

| Category | 选择 Case |
|---|---|
| generation | EVAL-007、EVAL-011、EVAL-045 |
| pending | EVAL-071、EVAL-107、EVAL-113 |
| paper | EVAL-119、EVAL-144、EVAL-151 |
| teaching | EVAL-234、EVAL-236、EVAL-239 |
| multi_turn | EVAL-271、EVAL-274、EVAL-276 |

Teaching 类无 open issue 的可运行条目均为 underspecified 澄清合同；8 条弱教学质量 Oracle 用例继续排除，没有为了凑数使用它们。

## 2. 原始真实 API 结果

原始文件永久保留：

- `evaluation/artifacts/reports/reliability/runs/dev-v3.2-dev122-batch15-first-api.json`
- `evaluation/artifacts/reports/reliability/runs/dev-v3.2-dev122-batch15-first-api.log`
- 派生统计：`evaluation/artifacts/reports/reliability/runs/dev-v3.2-dev122-batch15-first-api.analysis.json`

本批每条仅运行一次，使用真实 Runner、隔离 SQLite 和随机 conversation ID。Provider 为 SiliconFlow，模型 `Qwen/Qwen3.5-35B-A3B`，temperature=0。没有运行 Holdout，没有重复 5 条 DEVX。

**原始结果：13 PASS / 2 FAIL，86.7%。本批没有进行 Grader 复评。**

| Case | Category | 原始结果 | 最终状态 | Tool Trace | Failed Tool Obs. | 语义错误调用 | 恢复 | Tokens | 耗时 ms | 数据库副作用 |
|---|---|---|---|---|---:|---:|---:|---:|---:|---|
| EVAL-007 | generation | PASS | waiting_confirmation | inspect_curriculum → inspect_question_bank → prepare_generation_plan | 0 | 0 | 0 | 24,592 | 13,810 | 创建 generation pending；未创建 Paper |
| EVAL-011 | generation | PASS | waiting_confirmation | inspect_curriculum → inspect_question_bank → prepare_generation_plan | 0 | 0 | 0 | 25,502 | 15,769 | 创建 2/2/3 题型约束 Pending；未创建 Paper |
| EVAL-045 | generation | **FAIL** | waiting_confirmation | prepare_generation_plan | 0 | 1 | 0 | 11,764 | 6,807 | Pending 已创建，但 scope 错存为“第一章” |
| EVAL-071 | pending | PASS | waiting_confirmation | prepare_generation_plan | 0 | 0 | 0 | 7,221 | 45,856 | Pending 保持10题/第一章，总分改80 |
| EVAL-107 | pending | **FAIL** | completed | discard_pending_plan | 0 | 1 | 0 | 6,219 | 2,230 | **错误清除** generation pending |
| EVAL-113 | pending | PASS | completed | confirm_generation | 0 | 0 | 0 | 6,620 | 7,679 | 清除 Pending，持久化 Paper v1（6题60分） |
| EVAL-119 | paper | PASS | completed | read_paper | 0 | 0 | 0 | 9,153 | 4,998 | 只读；Paper v1 10题100分不变 |
| EVAL-144 | paper | PASS | waiting_confirmation | read_paper → preview_paper_changes | 0 | 0 | 0 | 14,929 | 10,555 | 创建11题110分加题 Preview；正式 Paper 不变 |
| EVAL-151 | paper | PASS | needs_clarification | 无 | 0 | 0 | 0 | N/A | 37 | 仅澄清；Paper 不变、无 Pending |
| EVAL-234 | teaching | PASS | needs_clarification | 无 | 0 | 0 | 0 | N/A | 5,471 | 仅澄清；无业务 Artifact |
| EVAL-236 | teaching | PASS | needs_clarification | 无 | 0 | 0 | 0 | N/A | 2,387 | 仅澄清；无业务 Artifact |
| EVAL-239 | teaching | PASS | needs_clarification | retrieve_curriculum_candidates | 1 | 0 | 0 | 1,846 | 6,217 | 候选读取失败后安全澄清；无 Artifact |
| EVAL-271 | multi_turn | PASS | waiting_confirmation | inspect_curriculum → inspect_question_bank → prepare → prepare | 0 | 0 | 0 | 32,993 | 21,720 | Pending v2 为6题80分；无 Paper |
| EVAL-274 | multi_turn | PASS | waiting_confirmation | read_paper → read_paper → preview | 0 | 0 | 0 | 24,014 | 13,511 | 删除 position=2 Preview（9题90分）；正式 Paper 不变 |
| EVAL-276 | multi_turn | PASS | waiting_confirmation | inspect_curriculum → inspect_question_bank → create_design → revise_design | 0 | 0 | 0 | 18,076 | 45,128 | 持久化 Design v1/v2；active v2 为3课时待确认 |

说明：

- `Failed Tool Obs.` 只统计 Tool Observation 中 `ok=false`。
- “语义错误调用”统计 Tool 技术执行成功、但调用或参数违反 Case Oracle 的情况。
- “恢复”统计失败 Tool 后同名 Tool 在本 Case 内再次成功；本批为 0。
- EVAL-239 的 `curriculum_candidates_not_found` 是安全澄清路径，不导致 Case FAIL。
- Provider 错误、超时和缺失 Trace：均为 0。

## 3. Category 通过率与资源

| Category | PASS / Total | Pass Rate | Tokens | 耗时 ms |
|---|---:|---:|---:|---:|
| generation | 2 / 3 | 66.7% | 61,858 | 36,386 |
| pending | 2 / 3 | 66.7% | 20,060 | 55,765 |
| paper | 3 / 3 | 100% | 24,082 | 15,590 |
| teaching | 3 / 3 | 100% | 1,846 | 14,075 |
| multi_turn | 3 / 3 | 100% | 75,083 | 80,359 |
| **合计** | **13 / 15** | **86.7%** | **182,929** | **202,175** |

- Input tokens：179,686
- Output tokens：3,243
- Cached tokens：11,520
- Reasoning tokens：0
- Model calls：38
- Tool calls / Tool rounds：25 / 25
- 延迟中位数：7,679 ms
- 单 Case 最大延迟：45,856 ms（EVAL-071）

EVAL-151、234、236 由确定性边界直接澄清，没有可用模型 Token Usage；其运行耗时仍计入总延迟。

## 4. 两条失败根因

### EVAL-045 — 真实模型约束落地失败

教师明确“第一章”，Oracle 要求 Pending 使用正式 canonical scope `第一章 函数与极限`。模型跳过 curriculum inspection，直接调用：

```text
prepare_generation_plan(scope_names=["第一章"], ...)
```

Tool 技术执行成功，但 Pending 最终仍保存别名 `第一章`，未满足 canonical scope Oracle。题量、总分和 2 道选择题/3 道计算题均正确。这不是 Provider 或 Grader false negative，不复评、不修改 Oracle。

### EVAL-107 — 暂停被误判为取消

教师说“先别生成，我还要再想想”，合同要求保留 Pending 并保持 `waiting_confirmation`。模型调用了：

```text
discard_pending_plan
```

Tool 成功清除 Generation Pending，最终状态变为 `completed`。这是明确的意图分类和破坏性副作用错误，不是 Oracle 问题，不复评。

## 5. 其他重要观察

- EVAL-276 按现有 Oracle PASS：active Teaching Design 为 version 2、`lesson_count=3`。Trace 中设计正文的 `teaching_sequence` 仍只有两段课时内容；现有 Oracle 不检查 sequence 长度。本轮不临时扩展 Oracle，也不改变原始 PASS，但应作为后续教学设计内容一致性审核项记录。
- EVAL-071 没有 Provider 错误，但耗时 45.856 秒，明显高于本批中位数，应在后续运行观察长尾。

## 6. 与此前 5 条 DEVX 合并的首批 20 条

此前 5 条 DEVX：原始执行 4 PASS / 1 FAIL；DEVX-005 因确定性 Grader false negative，基于原始 Trace 复评为 PASS。与本批合并时同时保留两种口径：

| 口径 | PASS / Total | Pass Rate |
|---|---:|---:|
| 全部原始报告 | 17 / 20 | 85.0% |
| 仅应用已留痕的 DEVX-005 Grader 复评 | **18 / 20** | **90.0%** |

复评口径按 Category：

| Category | PASS / Total | Pass Rate |
|---|---:|---:|
| generation | 3 / 4 | 75.0% |
| pending | 4 / 5 | 80.0% |
| paper | 5 / 5 | 100% |
| teaching | 3 / 3 | 100% |
| multi_turn | 3 / 3 | 100% |

20 条合计资源：

- Input tokens：276,813
- Output tokens：4,652
- Total tokens：281,465
- Cached tokens：19,456
- Model calls：60
- Tool calls：39
- 累计延迟：269,992 ms
- Provider errors：0

当前有效失败仍为：`EVAL-045`、`EVAL-107`。没有对这两条做 Grader 复评，也没有为了提高通过率修改 Oracle。

## 7. 历史与边界

- 本批只运行 `dev_122_revised_candidate.yaml` 中选定的 15 条。
- 每条只运行一次。
- 未运行 Holdout。
- 未重复 5 条 DEVX。
- 原始 V3.1、正式 Holdout 和既有原始/复评报告均未覆盖。
