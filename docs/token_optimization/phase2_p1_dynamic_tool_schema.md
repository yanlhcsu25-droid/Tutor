# Phase 2 · P1 Dynamic Tool Schema Optimization

## 1. 范围

本阶段只裁剪模型可见 Tool Schema，不压缩 System Prompt、Working Memory 或 Tool Observation，不改变 Tool 参数 Schema、执行器、数据库契约及 Preview → Confirm 安全边界。

Feature Flag：

```env
AGENT_DYNAMIC_TOOL_SELECTION_ENABLED=false
```

默认关闭。关闭时 `ToolExposurePolicy.initial_tools()` 返回 Phase 1 原有集合；开启后才应用 P1 策略。

## 2. 当前架构审计

### 注册与传输

- `build_agent_tools()` 当前注册 **21 个工具**。
- 当前完整 Registry Schema 合计 **33,335 chars**。
- `Runtime Coordinator` 先取得 `TaskRouter` 的确定性/语义路由结果及 Paper/Pending 状态，再构造 `ToolExposureContext`。
- `ToolExposurePolicy` 已是模型可见工具面的单一入口；`Toolkit.schemas(names=...)` 生成原始 Pydantic Schema，`ToolLoop.run_result()` 在每次模型请求中重新传入当前 `definitions`。
- 因此 Tool Schema 会在每轮重复发送，但 Coordinator 会在 grounding read、paper preview、generation patch、post-inspection 等边界替换为更小的阶段集合。
- 执行前仍使用 `exposed_tool_names(definitions)` 检查模型选择。注册但未暴露的工具返回 `tool_not_exposed`，不会绕过 Toolkit/Pydantic/业务校验。

### 当前各工具 Schema

| Tool | Chars | 完整 Registry 占比 |
|---|---:|---:|
| revise_teaching_design | 6,435 | 19.30% |
| create_teaching_design | 6,040 | 18.12% |
| prepare_generation_plan | 5,372 | 16.12% |
| preview_paper_changes | 4,201 | 12.60% |
| prepare_reinforcement_plan | 2,027 | 6.08% |
| prepare_teaching_planning_draft | 1,521 | 4.56% |
| read_paper | 1,390 | 4.17% |
| inspect_question_bank | 1,009 | 3.03% |
| select_teaching_scope | 645 | 1.93% |
| inspect_curriculum | 618 | 1.85% |
| retrieve_curriculum_candidates | 571 | 1.71% |
| operate_paper_version | 510 | 1.53% |
| activate_teaching_design | 422 | 1.27% |
| confirm_teaching_design | 383 | 1.15% |
| search_teaching_design_history | 368 | 1.10% |
| confirm_paper_changes | 363 | 1.09% |
| discard_pending_plan | 312 | 0.94% |
| discard_teaching_design | 300 | 0.90% |
| confirm_generation | 293 | 0.88% |
| analyze_paper | 289 | 0.87% |
| read_active_teaching_design | 266 | 0.80% |

### 为什么放在 ToolExposurePolicy 后

动态裁剪位于 `TaskRouter → ToolExposurePolicy → Toolkit.schemas()` 之间：

1. 复用现有 Task Route，不增加 LLM 分类调用。
2. 先由 Phase 1 Policy 施加生命周期和权限边界，再做任务级裁剪。
3. 不复制 Toolkit Schema、Tool Loop 或业务执行校验。
4. Boundary Tool 集合仍由现有 Policy 管理，多轮阶段转换不被绕过。

## 3. P1-A 动态选择策略

`src/calculus_agent/runtime/tool_selection.py` 输出：选择集合、任务族、原因、fallback 标记、Feature Flag 状态，并可离线计算 Full/Selected Tool Count、Schema chars 和字符减少量。

### READ

- 当前试卷事实：仅 `read_paper`；明确分析请求增加 `analyze_paper`。
- 教材/题库查询：保留 Phase 1 的 `retrieve_curriculum_candidates`、`inspect_curriculum`、`inspect_question_bank` 链。
- 不暴露试卷写工具。

### GENERATION

- 新组卷：`inspect_curriculum` / `inspect_question_bank` / scope retrieval → `prepare_generation_plan`。
- Pending Generation 明确确认：仅 `confirm_generation`。
- 明确取消：仅 `discard_pending_plan`。
- Pending 修改保留完整 Pending Generation 生命周期集合，避免多操作请求失去 confirm/discard。
- 当前 Paper 上的生成/强化请求保留 Phase 1 generation chain。

### PAPER_CHANGE

- 新修改：`read_paper` → `preview_paper_changes`；只有分析/版本词出现时才保留对应工具。
- 没有 Pending 时不暴露 `confirm_paper_changes`。
- Pending 明确确认/取消分别只暴露 confirm/discard。
- Pending 修改保留 read/preview/discard。
- Tool 执行成功后仍由现有 Runtime 强制 Preview 终止并等待下一轮确认。

### TEACHING_DESIGN

保留现有分阶段策略：scope retrieval/select → curriculum/question-bank inspection → create/revise → confirm/discard。Phase 1 已经按阶段裁剪，本轮不进一步冒险压缩。

### MIXED / UNKNOWN

- 路由低置信度或要求澄清：回退到 Phase 1 安全集合。
- 同时包含 Paper Change 和 Generation：使用有界并集，只包含 read/analyze/preview/version 与 generation preview/reinforcement；没有 Pending 时不加入 confirm/discard，也不暴露 TeachingDesign 写工具。
- fallback 不是全部21个工具。

## 4. 业务工具链

| 业务 | 必要链 |
|---|---|
| Read current paper | `read_paper` → deterministic final response |
| Generate | curriculum/question-bank inspection → `prepare_generation_plan` → later-turn `confirm_generation` or patch/discard |
| Paper change | `read_paper` → `preview_paper_changes` → later-turn `confirm_paper_changes` or discard |
| Teaching design | scope retrieval/select → inspection → create/revise → later-turn confirm/discard |
| Mixed | bounded union + original execution/lifecycle guards |

Tool Selection 只决定“模型能看到什么”。模型返回的调用仍须通过：暴露集合检查、Pydantic 参数校验、ToolExecutor、版本校验、Pending 校验和事务 savepoint。

## 5. 确定性场景测量

以下比较的是**当前 Phase 1 Tool surface 与 P1 selected surface 的 Schema 字符数**，不是 Provider Token：

| 场景 | Full Tools | Selected | Full chars | Selected chars | Reduction chars | Fallback |
|---|---:|---:|---:|---:|---:|---|
| READ_CURRENT_PAPER | 6 | 1 | 7,065 | 1,390 | 5,675 | No |
| GENERATION_NEW | 8 | 4 | 13,960 | 7,570 | 6,390 | No |
| PAPER_CHANGE_PREVIEW | 6 | 2 | 7,065 | 5,591 | 1,474 | No |
| TEACHING_DESIGN | 3 | 3 | 7,667 | 7,667 | 0 | No |
| MIXED | 7 | 6 | 14,101 | 13,789 | 312 | Yes |
| PENDING_GENERATION_CONFIRM | 3 | 1 | 5,977 | 293 | 5,684 | No |
| PENDING_PAPER_CHANGE_CONFIRM | 5 | 1 | 6,555 | 363 | 6,192 | No |

## 6. P1-B Shadow Audit

命令：

```bash
.venv/bin/python tests/evals/token_optimization/tool_schema_shadow_audit.py
```

输出：`tests/evals/token_optimization/reports/phase2_p1_tool_schema_shadow_audit.json`。

历史报告没有保存原始 Task Route 和每轮暴露工具名称。Shadow Audit 使用当前确定性/启发式 TaskRouter 重建 Route，使用历史成功 Case 的实际 Tool Call 作为 required tools，并以保守 multi-round chain 检查覆盖。所有限制均写入机器报告。

### 全部历史 Trace 加权投影

| 指标 | 结果 |
|---|---:|
| Turns / historical model calls | 70 / 163 |
| Mean Full Tool Count | 6.33 |
| Mean Selected Tool Count | 3.46 |
| Weighted Full Schema chars | 1,750,579 |
| Weighted Selected Schema chars | 1,017,515 |
| Schema chars reduction | 733,064（41.88%字符投影） |
| Fallback | 29/70 turns；16/163 model calls |
| Unsafe pruning cases | **0** |
| Historical required tool coverage | **100%** |

41.88% 是在当前策略和当前 Schema 上，将每 Turn 初始集合按历史模型调用次数加权得到的 Shadow 字符投影；它不是实际 Token 节省率，也不代表模型行为不变。

### 按任务族

| Task Family | Model Calls | Mean Full / Selected Tools | Schema char projection reduction | Fallback model-call rate | Coverage | Unsafe |
|---|---:|---:|---:|---:|---:|---:|
| GENERATION | 122 | 7.18 / 3.64 | 46.64% | 6.56% | 100% | 0 |
| PAPER_CHANGE | 9 | 6.00 / 2.00 | 20.86% | 0% | 100% | 0 |
| READ | 4 | 3.00 / 3.00 | 0% | 0% | 100% | 0 |
| TEACHING_DESIGN | 20 | 2.50 / 2.50 | 0% | 0% | 100% | 0 |
| MIXED_UNKNOWN | 8 | 5.00 / 5.00 | 0% | 100% | 100% | 0 |

历史 READ 样本是教材/题库查询，未覆盖“当前试卷只读”的场景，因此历史加权 READ 没有减少；确定性场景已验证当前试卷只读从6个缩至1个。

## 7. 可观测性

新的 Model Span input 记录 `tool_names`，后续受控 A/B 可以直接看到每次实际暴露名称；原有 `n_definitions`、`tool_schema_chars` 和 Provider usage 保持不变。

## 8. 测试与安全

- Feature Flag 关闭的完整确定性回归：425 PASS、1 SKIP（426 collected）。
- Feature Flag 开启的路由、读取、Generation、Paper Preview、恢复和 Tool 拒绝定向回归：89 PASS。
- Eval Grader 定向回归：49 PASS。
- Provider 可观测性：2 PASS。
- P0/P1 离线审计测试：2 PASS。
- Ruff 与 `git diff --check`：PASS。
- 未调用付费模型、未运行 Dev117、未修改历史报告/Oracle/Grader、未修改 Phase 1 Tag。

## 9. 仍需真实模型验证

Shadow Audit 只能证明历史工具链覆盖，不能证明裁剪后模型仍以相同概率选择正确工具。后续最小 A/B 应在相同模型、temperature、数据和 Commit 条件下分别运行 Flag off/on，比较：

1. 当前试卷题数/题型查询。
2. 新建章节测试卷并形成 Pending Preview。
3. Pending Generation patch 与明确 confirm。
4. 单题替换 read → preview，再下一轮 confirm。
5. Generation shortage/conflict 的快速终止。
6. TeachingDesign 无 scope → scope select → create。
7. Paper Change + Generation 混合请求，确认 fallback 无越权工具。
8. 路由低置信度请求，确认 fallback 覆盖且无新增写权限。

A/B 必须分别报告业务通过率、模型调用数、Provider Input Token、每调用 Tool Schema chars 和工具选择错误；在真实结果前不声明 Token 降幅。
