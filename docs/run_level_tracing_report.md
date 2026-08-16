# Run-Level Tracing — 实施报告（Teacher Agent）

> 范围：仅做追踪/可观测性，未改动组卷业务逻辑、未修复 `insufficient_candidates`、未改 Prompt、未重构 Agent 架构。
> 本地 Run Trace 为唯一权威来源；Langfuse 为可选后端，任何异常都不得影响业务响应。

---

## 1. Before（改造前的问题）

| 问题 | 说明 |
| --- | --- |
| 无统一 `run_id` | `TeacherAgentRunTrace` 仅有内部 `id` 与不可唯一定位的 `conversation_id` 索引；一次用户消息无法直接、唯一地定位。 |
| 失败路径不落库 | DB trace 行在 `run_teacher_agent` 中部（工具循环前）才创建，但 `conversation_context_error`、`agent_model_unavailable` 两条 early-return 失败路径在创建之前就已返回 → **用户看到 failed 却查不到任何 trace**。 |
| 工具调用不可查询 | `tool_calls_json` 是扁平 blob，无 `parent_span_id`、无结构化 span；无法重建调用树。 |
| 技术错误 vs 业务失败未区分 | 无法区分「工具返回 `{"ok": false}`（业务失败）」与「真实异常（技术错误）」。 |
| 表只写不读 | 无 API 回查，trace 不可观测、不可检索。 |
| 生命周期未保证 | 无统一的「创建→运行→收尾」保证，存在成功有 trace / 失败无 trace 的不一致。 |

---

## 2. Architecture After（改造后）

```
conversation_id (整段对话)
   └── run_id (一次用户消息执行，唯一)         ← 顶层，进入函数即创建
         └── teacher_agent_span (树状，parent_span_id 串联)
               ├── agent            (teacher_agent_run，根 span，parent=null)
               ├── model_call      (每次 LLM 调用)
               ├── tool_call       (每次工具调用；业务失败 ok=false → status=success)
               │     └── state_transition (工具导致的 working-memory 前后快照)
               └── tool_call ...
```

- **两级关联**：`conversation_id → run_id → spans`，`run_id` 每次请求唯一，`conversation_id` 可含多个 `run_id`。
- **进入即创建**：`TeacherAgentRunManager.create()` 在 `run_teacher_agent` 最顶部（任何业务逻辑与 early-return 之前）执行，`run_id` 立即落库。
- **技术 vs 业务**：
  - 业务失败（工具返回 `ok=false`、模型不可用）→ `tool_call` span `status="success"`，失败留在 `output`；run 的 `error_*` 列**保持为空**（沿用既有约定，仅技术异常写 `error_*`）。
  - 真实异常 → `tool_call` span `status="error"`，run 写入 `error_code/error_type/error_stage`。
- **所有路径必落库**：`success / failed / needs_clarification / waiting_confirmation / model_unavailable / tool_exception` 全部经过同一个 `finish()` → `run_manager.finalize()`。
- **本地权威 + Langfuse 可选**：所有写入走共享 `session`，与业务同事务；`TeacherAgentRunManager` 全部 best-effort 吞异常，永不破坏业务。
- **HTTP 响应新增 `run_id`**：`TeacherAgentResult` 增加 `run_id: str | None = None`（仅新增字段，业务字段语义不变）。

---

## 3. Changed Files

| 文件 | 改动 |
| --- | --- |
| `src/calculus_agent/models.py` | `TeacherAgentRunTrace` 增加 `run_id`(唯一索引)、`status`、`started_at`、`ended_at`、`latency_ms`、`agent_name`、`state_before_json`、`state_after_json`；新增 `TeacherAgentSpan` 表（`span_id/run_id/parent_span_id/span_type/name/status/started_at/ended_at/latency_ms/input_json/output_json`）。 |
| `src/calculus_agent/db.py` | `create_schema()` 增加幂等 ALTER 块，为 `teacher_agent_run_trace` 加上述列；`teacher_agent_span` 由 `create_all` 自动建表。 |
| `migrations/013_run_level_tracing.sql` | 文档化迁移 SQL（与现有迁移文件风格一致）。 |
| `src/calculus_agent/agent/run_tracing.py` | **新增** `TeacherAgentRunManager`：本地持久化 run 级追踪唯一来源。方法全部 best-effort。`create / mark_running / finalize / set_state_before / add_span / update_span`。 |
| `src/calculus_agent/agent/agent.py` | 顶部创建 `run_manager` 与根 `agent` span；`finish()` 中 `finalize` 并回填 `result.run_id`；复用 `run_manager.row` 作为唯一 trace 行（消除重复行）；`set_state_before`；`model_call` / `tool_call` / `state_transition` span 埋点（业务失败=success，真实异常=error）。 |
| `src/calculus_agent/api.py` | `TeacherAgentResult` 经 `run_id` 字段自动成为响应字段；新增 `TeacherAgentRunRead` / `TeacherAgentSpanRead` 读模型；新增 `GET /teacher-agent/runs/{run_id}` 与 `GET /teacher-agent/runs?conversation_id=`。 |
| `tests/test_teacher_agent_run_tracing.py` | **新增** 8 个确定性测试（6 场景 + 全链路 span 树 + GET 端点）。 |

> 生产库迁移：已对 `calculus_agent.db` 执行 `create_schema` 完成迁移（备份见 `calculus_agent.db.bak_20260816_run_tracing`）。`test_teacher_agent_knowledge_status.py::test_agent_real_bad_case_*` 直连真实库回归，必须迁移后方可落 trace。

---

## 4. Trace Coverage（覆盖矩阵）

| 场景 | run 落库 | run_id 返回 | agent span | model_call | tool_call | state_transition | error 列 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Success（纯聊天） | ✅ | ✅ | ✅ | ✅ | — | — | — |
| Business Failed（`unknown_tool`，ok=false） | ✅ | ✅ | ✅ | ✅ | ✅ `status=success` | — | 空（业务失败） |
| Needs Clarification（未落地事实核验） | ✅ | ✅ | ✅ | ✅ | — | — | 空 |
| Model Unavailable（early-return） | ✅ | ✅ | ✅ | — | — | — | 空（业务失败） |
| Tool Exception（真实异常） | ✅ | ✅ | ✅ `error` | ✅ | ✅ `error` | — | `agent_execution_failed` |
| Multi-turn（同 conversation） | ✅×N | 各唯一 | 每轮 1 个 | ✅ | ✅ | ✅ | — |
| 工具型成功（read+preview） | ✅ | ✅ | ✅ | ✅×N | ✅×2 | ✅×2（挂在 tool_call 下） | — |

不变量（测试断言）：每请求恰好一个 `run_id`；每个非根 span 的 `parent_span_id` 指向同 run 内某 span；每 run 恰好一个 `agent` span；业务失败 `tool_call` span 不为 `error`；真实异常 `tool_call` span 为 `error`。

---

## 5. Example Trace（真实生成，temp DB，read + preview 流程）

**Run 行**（节选）
```json
{
  "run_id": "abf04a3c-304a-47a2-97c9-14f3aa8bf09e",
  "conversation_id": "example-turn",
  "paper_id": "ex-paper",
  "user_message": "第3题太难了，换简单一点",
  "status": "waiting_confirmation",
  "started_at": "2026-08-16 14:03:23.368896",
  "ended_at":   "2026-08-16 14:03:23.473411",
  "latency_ms": 104,
  "agent_name": "teacher_agent",
  "result_status": "waiting_confirmation",
  "final_response": "已找到第3题更简单的替代题，请确认。",
  "state_before_json": {"active_task":{}, "generation_summary":{}, ...},
  "state_after_json":  {"active_task":{}, "generation_summary":{}, ...},
  "error_code": null, "error_type": null, "error_message": null, "error_stage": null
}
```

**Span 树**
```
agent            teacher_agent_run        (parent=null, status=success)
├── model_call    llm_completion          (parent=agent, status=success, tool_calls=1)
├── tool_call     read_current_paper      (parent=agent, status=success)
│     └── state_transition read_current_paper_state_change   (parent=tool_call, status=success)
├── model_call    llm_completion          (parent=agent, status=success, tool_calls=1)
├── tool_call     preview_replace_question (parent=agent, status=success)
│     └── state_transition preview_replace_question_state_change (parent=tool_call, status=success)
└── model_call    llm_completion          (parent=agent, status=success, tool_calls=0)
```

> 注：`state_before/after` 在两工具调用间相等，是因为 `AgentWorkingMemory` 模型未在该场景下反映 pending preview；span 仍如实记录前后快照，供排查。

---

## 6. Tests

新增 `tests/test_teacher_agent_run_tracing.py`（8 个用例，全部通过）：

1. `test_success_completed_chat_has_run_id_and_spans` — 成功聊天：run_id + agent/model_call span。
2. `test_business_failed_tool_call_is_success_span` — `unknown_tool` 业务失败：run 落库、`tool_call` span `status=success`、失败在 `output.ok=false`。
3. `test_needs_clarification_is_traced` — 业务澄清信号同样落库。
4. `test_model_unavailable_early_return_still_traced` — **关键回归**：`backend=None` 早期返回路径此前不落 trace，现保证 `run_id` 可查。
5. `test_tool_exception_records_error_span` — 真实异常：`tool_call` span `status=error`、`error_code=agent_execution_failed`、agent span `error`。
6. `test_multi_turn_same_conversation_distinct_run_ids` — 同 conversation 两回合 `run_id` 各异且均可查。
7. `test_tool_using_turn_produces_full_span_tree` — 完整 span 树（agent→model→tool→state），parent 串联校验。
8. `test_get_run_by_run_id_and_list_by_conversation` — `GET /teacher-agent/runs/{run_id}` 与 `?conversation_id=` 经 `TestClient` 验证可查、404 正确。

**回归结果**：`tests/test_teacher_agent_*.py` 全量 **81 passed**（含既有 autonomous / knowledge_status / api / langfuse / trace_log / structured_generation 等）。

---

## 7. Known Limitations（已知限制）

- **`error_*` 列仅用于技术异常**：业务失败（含 `insufficient_candidates`、模型不可用）按项目既有约定**不写入** `error_*` 列，其失败信号存在于 `status` / `final_response` / `tool_calls_json(ok=false)`。若后续希望业务失败也在 run 行单独留痕，需先与既有测试（`error_type is None` 断言）对齐约定。
- **`state_transition` 为信息性快照**：当前以「每次工具执行」为单位记录 working-memory 前后快照；若两快照相等（如本例），仍会保留一条 span。占用行数 = 工具调用次数，属预期。
- **真实异常下 `latency_ms` 统计**：`model_call`/`tool_call` 用 `ended_at-now` 计算；`state_transition` 为瞬时 marker，延迟≈0。
- **`run_id` 唯一约束**：正常路径每轮生成 UUID 必唯一；极端降级路径（`create()` 失败）会回退为无 `run_id` 的遗留行（此路径下 DB 本身已异常，非预期场景）。
- **生产库需迁移**：任何既有 `calculus_agent.db` 必须先跑 `create_schema` 才能落 `run_id`/`teacher_agent_span`；本次已对仓库根 `calculus_agent.db` 完成迁移并备份。
- **未覆盖**：跨服务分布式追踪、trace 保留/清理策略、Langfuse 双写一致性（按需求 Langfuse 仍为可选、失败不影响本地 trace）。

---

## 完成标准核对

- [x] 每个请求有且仅有一个 `run_id`
- [x] 所有 span 经 `parent_span_id` 串联到 run
- [x] 业务失败（`ok=false`）与真实异常均不丢 trace，且 span 状态正确区分
- [x] 终态响应可由 `run_id` 回查（GET 端点）
- [x] 业务逻辑零改动（既有 81 个 teacher-agent 测试全绿）
- [x] 回归测试通过
