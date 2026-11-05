# EVAL-045 / EVAL-107 修复记录

## 约束

- 未修改 Case ID 分支、Oracle、冻结首跑报告或历史报告。
- 修复为通用业务语义；重跑仅写入独立的 post-fix regression 报告。
- EVAL-045 首跑 FAIL、EVAL-107 首跑 FAIL 仍属于首批 20 条的冻结事实。

## EVAL-107：暂缓不等于取消

### 根因

Pending Generation 状态下，“先别生成，我还要再想想”会进入 LLM 生命周期决策；模型可能调用破坏性的 `discard_pending_plan`。原系统没有在工具执行前提供确定性的暂缓语义边界。

### 修复

- `src/calculus_agent/runtime/response_policy.py`
  - 新增通用 `pending_generation_pause_intent`。
  - 区分暂缓（先别生成、再想想、保留方案、不要取消）与明确取消（取消方案、放弃方案、算了、作废）。
  - 若同轮包含可执行 Patch（如“先降到90分，暂时不要确认”），不短路 Patch。
- `src/calculus_agent/runtime/coordinator.py`
  - Pending 存在且为纯暂缓时，在 LLM/Tool 前安全返回 `waiting_confirmation`。
  - 保留 Pending，不调用 `discard_pending_plan`，也不创建其他业务副作用。

### 正向 / 反向 / 边界

- 正向：`先别生成，我还要再想想`、`不要取消，只是先别确认` → 暂缓。
- 反向：`取消当前方案` → 仍走明确取消。
- 边界：`先别生成了，取消这个方案` → 取消优先；`先降到90分，暂时不要确认` → 执行 Patch 后等待确认。

## EVAL-045：章节别名 canonicalization

### 根因

章节 resolver 已能把 `第一章` 解析到真实 Curriculum Chapter，但 Generation Preview 在持久化 `PendingGeneration` 前没有调用统一 resolver，因此 Pending 保存了非 canonical 的 `scope_names=["第一章"]`。

### 修复

- `src/calculus_agent/agent/services/generation.py`
  - 在 `GenerationService.preview` 合并请求前调用 `resolve_deterministic_scope_labels`。
  - 仅在全部 scope 可确定解析时替换为 canonical display name。
  - 未知 scope 保持原值并由现有请求校验拒绝，禁止静默映射到其他章节。
  - canonical 输入保持不变；解析复用生产范围 resolver，因此不扩大真实组卷节点范围。

### 正向 / 反向 / 边界

- 正向：`第一章` → `第一章 函数与极限`。
- 反向：`第九章 不存在` 不被改写到其他章节，并返回 `scope_not_found`。
- 边界：canonical display name 输入保持不变；Pending Patch 中未修改 scope 时继承既有范围。

## 验证

- 新增/扩展：
  - `tests/teacher_agent/test_response_policy.py`
  - `tests/evaluation/reliability/test_business_fact_projection.py`
  - `tests/generation/test_generation_scope_canonicalization.py`
- 更新既有 canonical scope 断言：
  - `tests/generation/test_generation_merge_and_scope.py`
  - `tests/teacher_agent/test_teacher_agent_structured_generation.py`
- 离线修复回归：2/2 PASS
  - `evaluation/reliability/datasets/staging/dev_v3_2_proposal/eval045_eval107_repair_regression_offline.json`
- generation、teacher_agent、evals 广泛回归：全通过（仅原有 skip）。
- 真实 SiliconFlow post-fix regression：2/2 PASS
  - EVAL-045：Pending scope 为 `第一章 函数与极限`，题型2选择+3计算、5题50分保持。
  - EVAL-107：0 model calls、0 tool calls，Pending 保留，状态 `waiting_confirmation`。
  - 报告：`evaluation/artifacts/reports/reliability/runs/dev-v3.2-eval045-eval107-postfix-regression-api.json`
