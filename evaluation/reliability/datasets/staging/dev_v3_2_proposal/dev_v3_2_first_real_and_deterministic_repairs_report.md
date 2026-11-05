# Teacher Agent Dev V3.2 — 第一批真实评测与确定性修复报告

## 1. 首批真实 SiliconFlow Smoke Test

执行范围严格限定为 `DEVX-008`、`DEVX-013`，每条一次。未运行 Holdout，也未重复运行其他候选。

```bash
PYTHONPATH=. .venv/bin/python evaluation/reliability/runner.py \
  --cases-file evaluation/reliability/datasets/staging/dev_v3_2_proposal/new_capability_20_candidate.yaml \
  --case DEVX-008 --case DEVX-013 \
  --variant state-policy \
  --case-delay-seconds 5 \
  --report evaluation/artifacts/reports/reliability/runs/dev-v3.2-proposal-ready2-first-api.json
```

结果文件：

- `evaluation/artifacts/reports/reliability/runs/dev-v3.2-proposal-ready2-first-api.json`
- `evaluation/artifacts/reports/reliability/runs/dev-v3.2-proposal-ready2-first-api.log`

| Case | 结果 | 状态 | Tool Loop | 最终业务状态/副作用 |
|---|---|---|---|---|
| DEVX-008 | **PASS** | `waiting_confirmation` | `inspect_curriculum → inspect_question_bank → prepare_generation_plan`，全部成功 | Pending 固定为第一章、6 题 60 分、选择/填空/计算各 2 题；无 Paper、Teaching Design 或确认副作用 |
| DEVX-013 | **PASS** | `completed` | `discard_pending_plan`，成功 | Generation Pending 已清除；原 Paper 仍为 10 题 100 分、version=1；未创建新版本 |

两条均由真实 Agent 自主选择并调用 Tool，Runner 没有强制工具调用。所有现有 Oracle、Acceptance、Workflow Safety、Outcome 和 Content Consistency Grader 均通过。

### API 与性能

| Case | Model Calls | Tool Calls | Input Tokens | Output Tokens | Total Tokens | Latency | Provider 错误 |
|---|---:|---:|---:|---:|---:|---:|---|
| DEVX-008 | 4 | 3 | 25,192 | 301 | 25,493 | 12,583 ms | 无 |
| DEVX-013 | 2 | 1 | 8,590 | 31 | 8,621 | 2,365 ms | 无 |
| **合计** | **6** | **4** | **33,782** | **332** | **34,114** | **14,948 ms** | **0** |

- Provider：`siliconflow`
- Model：`Qwen/Qwen3.5-35B-A3B`
- Adapter：`BailianChatBackend`
- Protocol：`openai_compatible`
- Temperature：0
- Usage：两条均 `actual_usage_available=true`；cached/reasoning tokens 均为 0。
- 报告保存了逐轮 model spans、tool spans、Tool Observation、最终 Trace、数据库快照、耗时和 usage。

### 隔离说明

每条 Case 使用不同的临时 SQLite、不同的随机 `conversation_id`，因此数据库状态和 owner 命名空间物理隔离。现有 Runner 向 Agent 传递的 owner 值仍是项目的 `DEFAULT_TEACHER_OWNER_KEY`，并非两个不同字符串；这两条均不涉及跨 owner 操作。为遵守“不增加评测框架功能”，本轮没有修改 Runner owner 注入机制。若后续 Case 必须验证同库跨 owner 隔离，应继续保留 `DEVX-020` 的 Runner Extension 前置条件。

## 2. DEVX-004、005、011、015

### DEVX-004 — READY

新增并通过以下 Oracle：

- turn 1 必须成功调用 `read_paper`；
- turn 2 必须按 `read_paper → preview_paper_changes` 顺序执行；
- Preview operation 必须命中全卷 `position=3` 且类型为 `replace_question`；
- 最终为 `waiting_confirmation`；
- 正式 Paper 每一轮均保持 10 题 100 分且无提前确认。

真实 Fixture + Scripted Backend 离线 Runner PASS。

### DEVX-005 — READY

当前业务判定：当本轮含“刚读的”且紧邻上一用户轮只有一个读取题目目标时，指代唯一；没有先前单一读取上下文时仍返回地址澄清。

实现没有使用 Case ID：

- `paper_request.has_unique_recent_question_reference()` 检查上下文指代、最近用户读取意图和单一题号；
- Coordinator 将真实 recent conversation messages 交给歧义守卫；
- 无上下文的“刚读的第5题”仍为 ambiguous；
- 有唯一上下文时继续真实 Tool Loop。

增强 Oracle 要求：turn 1 读取；turn 2 再读取并创建删除预览；Preview 必须为 `remove_question(position=5)`；Paper 不提前修改。离线 Runner PASS。

### DEVX-011 — READY

Parser 已支持：

- `改成7题`
- `改为7道题`
- `题数调整到7道`
- `保持原总分，改成7题`

修复位于 `src/calculus_agent/runtime/request_guards.py`，新增的是通用数量 patch 模式，不包含 Case ID。

验证结果：

- Tool input 保留 `question_count=7`；
- 原 `paper_type`、第一章 scope、100 分保持不变；
- Constraint Preservation Grader 已加入该 Case；
- 未调用 confirm；
- 真实 Fixture + Runner 离线 PASS。

### DEVX-015 — BLOCKED

只做了业务语义检查，没有为通过 Case 开发并发工作流功能。

当前 Fixture 同时创建：

1. Generation Pending；
2. `awaiting_confirmation` Teaching Design。

数据库允许两类记录共存，但 Runtime 只有一个 active workflow 语义。`resolve_pending_teaching_design_intent()` 将包含“教学设计保持原样”的取消请求解释为 Teaching Design cancel，Confirmation Guard 随后强制 `discard_teaching_design`；Generation Pending 未被清除。因此“两个待确认工作流并发且可按域取消”不是当前已冻结业务能力。

替代测试方案：使用**已确认且 active** 的 Teaching Design 加 Generation Pending，验证 `discard_pending_plan` 只清除 Generation 且设计不变；或者在 Service 层测试不同 pending 表的清理隔离。现有 Fixture 只会创建 awaiting-confirmation 设计，待业务确认后再扩展 Fixture。本轮不强制实现。

## 3. 原 39 条审核问题

### 修复统计

| 原问题类别 | 数量 | 处理 |
|---|---:|---|
| RESPONSE_FACTS_PARSER_VALIDATION | 4 | 题数、总分事实进入确定性 `response_facts` Oracle |
| WEAK_READ_RESPONSE_ORACLE | 13 | 题型数量/结构/难度使用 Fixture 真值；指定题内容使用成功 `read_paper` Observation 对照回复 |
| READ_ITEM_CONTENT_GRADER_GAP | 3 | 按正式地址语义修正为 `needs_clarification`：未声明全卷或题型命名空间时不猜题号 |
| INTERMEDIATE_TURN_ORACLE_WEAK | 9 | Acceptance 增加逐轮状态、Required/Forbidden Tools 和工具顺序合同 |
| **确定性修复合计** | **29** | **29/29 Schema、Fixture、Grader 离线 PASS** |
| ADDRESS_SEMANTICS_NEEDS_REVIEW | 2 | 保留人工业务语义前置条件 |
| WEAK_TEACHING_RESPONSE_ORACLE | 8 | 保留教学质量 Judge/人工 rubric 前置条件 |
| **继续未决** | **10** | 未批准、未降低 Oracle |

逐条离线结果：

- `evaluation/reliability/datasets/staging/dev_v3_2_proposal/deterministic_review_repairs_validation.json`
- 29 条全部由真实 CaseLoader 加载；每条使用独立 SQLite 初始化真实 Fixture，并调用现有 Grader。
- `evaluation/reliability/datasets/staging/dev_v3_2_proposal/remaining_review_items.json` 现只保留 10 条未决项。
- `evaluation/reliability/datasets/staging/dev_v3_2_proposal/resolved_deterministic_review_items.json` 保存 29 条修复账目。

修复前后差异保存在：

- `evaluation/reliability/datasets/staging/dev_v3_2_proposal/dev_122_deterministic_oracle_fixes.patch`
- `evaluation/reliability/datasets/staging/dev_v3_2_proposal/devx_004_005_oracle_fixes.patch`

原始 V3.1、正式 Holdout 和既有历史报告均未修改或覆盖。

## 4. 当前 READY 清单

### 新 20 条候选中 READY

- `DEVX-004`：跨轮读取、唯一目标替换预览
- `DEVX-005`：近期唯一读取指代、删除预览
- `DEVX-008`：明确题型蓝图 Pending（真实 API PASS）
- `DEVX-011`：Pending 题数局部 Patch 与约束保留
- `DEVX-013`：取消 Generation Pending 且 Paper 不变（真实 API PASS）

这 5 条现均已完成真实 API：DEVX-004、008、011、013 原始评分 PASS；DEVX-005 原始评分因 before/after 数字归属的 Grader false negative 为 FAIL，修复后基于原始 Trace 离线复评 PASS。原始与复评报告均保留。

### 原 122 条中本轮 Oracle/Fixture/Grader 已就绪

`EVAL-116, 117, 118, 119, 120, 121, 122, 123, 124, 125, 181, 182, 183, 184, 185, 186, 187, 188, 189, 190, 271, 272, 273, 274, 275, 276, 277, 278, 280`

这 29 条是“确定性审核问题已关闭”，不是未经真实模型执行即可宣称 Benchmark PASS。

## 5. 新增 20 条候选及覆盖类别

| Case | 覆盖类别 | 当前门槛 |
|---|---|---|
| DEVX-001 | Paper 版本 Undo | NEEDS_GRADER_EXTENSION |
| DEVX-002 | Paper 版本 Redo | NEEDS_GRADER_EXTENSION |
| DEVX-003 | 无效版本 Restore | NEEDS_SEMANTIC_REVIEW |
| DEVX-004 | 跨轮读取后替换预览 | **READY** |
| DEVX-005 | 唯一指代后删除预览 | **READY** |
| DEVX-006 | 读取回复事实落地 | NEEDS_PARSER_VALIDATION |
| DEVX-007 | 题型分布读取 | NEEDS_PARSER_VALIDATION |
| DEVX-008 | 首章明确题型蓝图 | **READY / API PASS** |
| DEVX-009 | Generation Preview/Confirm 边界 | NEEDS_PER_TURN_GRADER |
| DEVX-010 | 第三章题型别名 | NEEDS_FIXTURE |
| DEVX-011 | Pending 题数局部 Patch | **READY** |
| DEVX-012 | 暂停但保留 Pending | NEEDS_PER_TURN_GRADER |
| DEVX-013 | 取消 Pending 且 Paper 不变 | **READY / API PASS** |
| DEVX-014 | 重复确认幂等 | NEEDS_GRADER_EXTENSION |
| DEVX-015 | Generation/Teaching 双 Pending | **BLOCKED** |
| DEVX-016 | 教学咨询无业务副作用 | NEEDS_QUALITY_JUDGE |
| DEVX-017 | Prepare 故障注入 | NEEDS_FAULT_TEST |
| DEVX-018 | 知识点溯源只读 | NEEDS_SEMANTIC_GRADER |
| DEVX-019 | 错题巩固与已做题排除 | NEEDS_FIXTURE |
| DEVX-020 | Owner/Session 隔离 | NEEDS_RUNNER_EXTENSION |

因此当前可进入下一轮正式 Dev 候选验证的是 5 条，而不是把 20 条全部计入正式通过率。其余 15 条继续保留明确门槛。

## 6. 回归验证

```text
相关 generation / Teacher Agent / Eval 测试：PASS（含预期 skip）
validate_offline.py：PASS
6 条 NEEDS_VALIDATION 离线重跑：5 PASS，DEVX-015 按业务语义保持 BLOCKED
29 条确定性修复验证：29 PASS / 0 FAIL
外部 API：仅 DEVX-008、DEVX-013 各一次
Holdout：未运行
```

## 7. 后续可复制命令

DEVX-004、005、011 已真实执行，不应重复消耗 API。可直接复核保存结果：

```bash
python - <<'PY'
import json
for path in (
    'evaluation/artifacts/reports/reliability/runs/dev-v3.2-proposal-ready-next3-api.json',
    'evaluation/artifacts/reports/reliability/runs/dev-v3.2-proposal-ready-next3-api.regraded.json',
):
    report = json.load(open(path))
    print(path, report['summary'])
PY
```

再次离线复核 29 条确定性修复：

```bash
PYTHONPATH=. .venv/bin/python \
  evaluation/reliability/datasets/staging/dev_v3_2_proposal/validate_deterministic_review_repairs.py
```
