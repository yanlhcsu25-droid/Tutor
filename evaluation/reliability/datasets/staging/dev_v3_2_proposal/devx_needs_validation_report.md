# Teacher Agent Dev V3.2：6 条 NEEDS_VALIDATION 离线验证报告

> **教师审核后的当前状态：** 本文主体保留早期离线验证证据，不代表当前有效集合。DEVX-015 已从有效集合排除，原因是并发 Pending 不符合教师实际使用习惯；DEVX-021 已新增为“无历史记录”失败场景。DEVX-010/016/018 的技术合同已落实并批准；DEVX-003/019/021 保持 `NEEDS_CAPABILITY`，不得宣称尚未落实的最终状态、历史排重或无历史 Tool Observation 已可用。本轮未运行任何真实 API。

## 结论

本轮只使用完整仓库中的真实 `CaseLoader`、隔离 SQLite Fixture、`run_case` Runner、业务 Tool 和 Grader；模型端统一替换为 `behavior=scripted` 的本地 Backend。**外部模型 API 调用数为 0**。

| Case | Runner 离线结果 | 审核状态 | 结论 |
|---|---|---|---|
| DEVX-004 | PASS | **NEEDS_REVISION** | 真实读取、再次读取、替换预览均成功，Paper 未修改；但现有聚合 Oracle 不能证明第一轮确实读取并准确回答，未完整覆盖“跨轮目标指代”。 |
| DEVX-005 | FAIL | **NEEDS_REVISION** | 第二轮“刚读的第5题”被地址守卫判为 `ambiguous_question_address`，未调用预览工具；用例文本与当前正式题号语义不一致。 |
| DEVX-008 | PASS | **READY** | 6 题 60 分及三种题型各 2 题原样进入 Pending，未确认、未生成 Paper。 |
| DEVX-011 | FAIL | **BLOCKED** | `改成7题` 未被显式题数解析器识别，Tool 参数被守卫清成空 patch，Pending 仍为 10 题；Oracle 本身不应降低。 |
| DEVX-013 | PASS | **READY** | 仅清除 generation pending，返回 completed，原 Paper 及分数、题量均不变。 |
| DEVX-015 | FAIL | **BLOCKED** | 同时存在待确认 Generation 和待确认 Teaching Design 时，确定性路由误走 `discard_teaching_design`，且该工具未暴露；组卷 Pending 未清除。并发 Pending 的业务合法性也尚未定义。 |

当前建议首批真实 API 范围仅为 **DEVX-008、DEVX-013**。DEVX-004 虽可执行通过，但应先补强逐轮 Oracle，避免把弱 Oracle 带入真实模型评测。

## 验证方法与证据

执行了：

```bash
uv run python evaluation/reliability/datasets/staging/dev_v3_2_proposal/validate_offline.py
# PASS: 122 unique original definitions, 78 archived duplicates, 20 new candidates

PYTHONPATH=. .venv/bin/python \
  evaluation/reliability/datasets/staging/dev_v3_2_proposal/validate_devx_needs_validation.py
# 使用从 new_capability_20_candidate.yaml 真实加载出的 6 个 EvalCase，
# 仅在内存中注入 scripted Backend 后调用 evaluation.reliability.runner.run_case。

PYTHONPATH=. .venv/bin/python -m pytest -q \
  tests/evaluation/reliability/test_business_fact_projection.py \
  tests/evaluation/reliability/test_workflow_safety_grader.py
# 23 passed
```

完整机器可读结果：

- `evaluation/reliability/datasets/staging/dev_v3_2_proposal/devx_needs_validation_offline_run.json`

该结果写入 staging 新文件，没有覆盖 V3.1、Holdout 或历史报告。目标路径检查中，V3.1、`holdout_v3_1_69.yaml` 和已有 `evaluation/artifacts/reports/reliability/runs/` 均无本轮 tracked 修改。

## 逐条验证

### DEVX-004 — NEEDS_REVISION

- Setup：`current_paper=10题/100分` 可由真实 Fixture 初始化。
- 成功工具链：第一轮 `read_paper(positions=[3])`；第二轮为满足当前同轮事实策略再次 `read_paper(positions=[3])`，随后 `preview_paper_changes`。
- 业务结果：替换目标解析为“选择题第 3 题”，候选难度从 3 降至 2；产生 pending preview；持久化 Paper 仍为 10 题 100 分。
- Required/Forbidden Tools：Required 均出现；未调用任何 confirm/design 写工具。
- 问题：当前 Acceptance 只按整个 Case 聚合工具名。它不能证明第一轮读取发生在第一轮，也不能证明第一轮回复准确；同样不能显式约束第二轮预览前的同轮重新读取。
- 建议：增加逐轮 Oracle：turn 1 要求成功 `read_paper` 且回复题号/题型与 Observation 一致；turn 2 要求成功 `read_paper -> preview_paper_changes`，并保持 Paper 不变。不要删除现有状态和工具要求。

### DEVX-005 — NEEDS_REVISION

- Setup：10 题 100 分 Paper 初始化成功；第一轮 `read_paper(positions=[5])` 成功，真实地址是“填空题第 1 题”。
- 第二轮：在 Backend 执行前被确定性地址守卫拦截，状态为 `needs_clarification`、错误码 `ambiguous_question_address`；因此没有 `preview_paper_changes`，也没有 pending change。
- 原因：当前正式语义要求修改时使用“全卷第 N 题”或“某题型第 N 题”；裸“第5题”即使带“刚读的”仍被判为歧义。
- 建议二选一：
  1. 若遵守现合同，把第二轮改成“删掉刚读的**全卷第5题**”，并补逐轮 Oracle；
  2. 若目标确实是跨轮代词消解，则保留文本，但先实现经已验证 Observation 绑定“刚读的那题”的能力，再期待 preview。不能把 Oracle 改弱为只看最终文本。

### DEVX-008 — READY

- Setup：空 Setup 合法。
- 工具链：`prepare_generation_plan` 成功，状态 `waiting_confirmation`。
- Pending：scope 为第一章，`question_count=6`、`total_score=60`；选择题、填空题、计算题各 2 道，均带 teacher-explicit hard provenance。
- 禁止项：未调用任何 confirm、Paper change 或 Teaching Design 工具；没有生成持久化 Paper。
- Oracle、Required/Forbidden Tools 与业务状态一致。

### DEVX-011 — BLOCKED

- Setup：Pending Generation 10 题 100 分、第一章初始化成功。
- 实际工具链：虽然调用了 `prepare_generation_plan`，但真实 Tool input 被守卫改写为 `{}`；结果只把 pending version 从 1 更新到 2，题数仍为 10。
- 根因：`request_guards.py` 的显式题数模式支持“题数改为7题”等表达，不识别常见的“改成7题”。因此模型即使正确提交 `question_count=7`，该字段仍会被移除。
- 交叉验证：仅把内存中的用户句改成“题数改为7题……”后，相同真实 Fixture/Runner/Tool/Grader PASS，Pending 为 7 题 100 分，章节保持不变。
- 建议：扩展显式题数 patch 解析以覆盖 `改成|改为|调整到 + N题`，增加确定性单测，再保留原 Case 和原 Oracle 重跑。不要把 expected 改回 10。

### DEVX-013 — READY

- Setup：Pending Generation 与当前 10 题 100 分 Paper 均成功初始化。
- 工具链：`discard_pending_plan` 成功，Observation 为 `discarded_types=[generation]`、`paper_unchanged=true`。
- 结果：状态 `completed`，`pending_generation=null`，Paper 仍为 10 题 100 分。
- Required/Forbidden Tools、Oracle 和 workflow safety 一致。

### DEVX-015 — EXCLUDED（以下为排除前历史证据）

- Setup：数据库层可以同时创建 Pending Generation 与 `awaiting_confirmation` Teaching Design。
- 实际流程：确定性路由没有执行脚本要求的 `discard_pending_plan`，而是尝试 `discard_teaching_design`；随后得到 `tool_not_exposed`，最终仍为 `waiting_confirmation`，Generation Pending 保留。
- 一致性问题：Fixture 创建的是“待确认教学设计”，不是普通已确认的持久化教学设计；这让同一会话出现两个竞争中的待确认工作流。当前 Runtime 没有定义这种并发状态的优先级和显式域选择规则。
- 建议：先作业务决定：
  - 若只允许一个待确认工作流：扩展 Teaching Design Fixture，使本 Case 使用“已确认且 active”的设计，然后验证取消 Generation 不影响它；
  - 若允许多 Pending：扩展状态模型/路由，使“只取消组卷待确认方案”确定性选择 generation 域，并保留逐域不变性快照。
- 在语义确定前，不建议调用真实 API；模型无法修复确定性路由和状态合同冲突。

## 原 122 条中的 39 条未决问题分类

| 类别 | 数量 | Case | 修复路径 |
|---|---:|---|---|
| RESPONSE_FACTS_PARSER_VALIDATION | 4 | EVAL-116, 117, 123, 183 | **可确定性修复**：参数化中文题数/总分表达测试，并与 Fixture 真值比对。 |
| WEAK_READ_RESPONSE_ORACLE | 13 | EVAL-118–122, 125, 182, 185–190 | **可主要用确定性检查修复**：题型计数、存在性、结构、难度分布直接对 DB snapshot；指定题内容对 Tool Observation/Fixture 内容做规范化包含或一致性检查。 |
| READ_ITEM_CONTENT_GRADER_GAP | 3 | EVAL-124, 181, 184 | **可确定性修复**：验证请求地址、成功 Observation 以及最终回复包含对应题干，而不只检查调用过 `read_paper`。 |
| INTERMEDIATE_TURN_ORACLE_WEAK | 9 | EVAL-271–278, 280 | **可确定性修复**：对 `evaluated_turns` 增加逐轮状态、工具顺序、禁止副作用和 Pending version 断言。 |
| ADDRESS_SEMANTICS_NEEDS_REVIEW | 2 | EVAL-149, 150 | **先人工定业务语义**；确定“第N题”是否默认全卷题号后，执行本身可用确定性 parser/Tool 测试。 |
| WEAK_TEACHING_RESPONSE_ORACLE | 8 | EVAL-214–221 | 不能仅靠确定性 Workflow Grader判断教学质量；保留安全/无副作用检查，并增加经校准的教学质量 Judge 或人工 rubric。 |

汇总：**29/39**（4+13+3+9）可以直接通过确定性 Fixture/Observation/逐轮状态检查修复；另 **2/39** 在业务语义定稿后可确定性落地；剩余 **8/39** 需要教学质量语义评审，不能通过降低 Oracle 解决。

## 后续真实 API 命令（本轮未执行）

首批仅跑 READY 的 2 条，并写入全新报告文件：

```bash
uv run python evaluation/reliability/runner.py \
  --cases-file evaluation/reliability/datasets/staging/dev_v3_2_proposal/new_capability_20_candidate.yaml \
  --case DEVX-008 \
  --case DEVX-013 \
  --variant state-policy \
  --case-delay-seconds 5 \
  --report evaluation/artifacts/reports/reliability/runs/dev-v3.2-proposal-ready2-first-api.json
```

以下命令是历史计划，**不得执行**；DEVX-015 已排除，且本轮禁止真实 API：

```bash
uv run python evaluation/reliability/runner.py \
  --cases-file evaluation/reliability/datasets/staging/dev_v3_2_proposal/new_capability_20_candidate.yaml \
  --case DEVX-004 --case DEVX-005 --case DEVX-008 \
  --case DEVX-011 --case DEVX-013 \
  --variant state-policy \
  --case-delay-seconds 5 \
  --report evaluation/artifacts/reports/reliability/runs/dev-v3.2-proposal-six-first-api.json
```

这两个命令都会使用原候选文件中未配置 scripted Backend 的 Case，因此会进入真实模型 Backend；只有在明确解除当前“禁止外部 API”阶段后才能执行。
