# Phase 2 · P0 Token Cost Profile

## 1. 范围与口径

本报告只分析历史文件 `tests/evals/reports/official-holdout-v3.2.1-69-second-20261006T045441Z.json`，未调用模型、未修改数据库、Runtime、Prompt、Tool Registry、Oracle 或 Grader。

- 原始报告 SHA256：`7f0ac6090e54cc856450859ca01f4fee04b91f59d8011096e469759c9f3a088b`
- 历史运行 Commit：`f8d9b013fe6b4658a0a507159f24b7ebcbd030d6`，`git_dirty=false`
- Provider / Model：`siliconflow` / `Qwen/Qwen3.5-35B-A3B`
- 配置：`state-policy`、temperature `0`、max tool rounds `8`、timeout `120s`
- Phase 1 基线：`746a94345e31ce3e92d6f804123766782ec19888` / `tool-runtime-optimized-v1.0.0`

历史报告早于 Phase 1 封存。因此其中的循环问题可用于定位优化机会，但不能代表 Phase 1 当前分支仍有相同消耗，也不能作为 Phase 2 同条件前测。

### 精确 Token 与 Context Proxy

- **精确 Token**：Provider usage 中的 `input_tokens`、`output_tokens`、`total_tokens`。163 个模型 Span 均有真实 usage。
- **Context Size Proxy**：Runtime 记录的字符数，包括 System Prompt、Tool Schema、Conversation、Workspace 和 Tool Observation。字符数没有除以 4，也没有被描述为真实 Token。
- 字符占比只能用于归因上下文体积，不能直接换算成费用或 Token 节省。

机器结果中的 `case_metrics` 按全部 69 个 Case ID 保存 Model Calls、Tool Calls、Provider Token、最终状态、Blocking Errors 和 Latency。

## 2. Token 消耗总览

| 指标 | 总量 | 每用例平均（69 条） | 发生模型调用的用例平均（48 条） | 每模型调用平均（163 次） |
|---|---:|---:|---:|---:|
| Model Calls | 163 | 2.36 | 3.40 | - |
| Tool Calls | 112 | 1.62 | 2.33 | - |
| Input Tokens | 774,001 | 11,217.41 | 16,125.02 | 4,748.47 |
| Output Tokens | 16,538 | 239.68 | 344.54 | 101.46 |
| Total Tokens | 790,539 | 11,457.09 | 16,469.56 | 4,849.93 |
| Latency | 1,734,168 ms | 25,132.87 ms | 35,079.96 ms | - |

- 69 条中 64 PASS；21 条零模型调用，48 条发生模型调用。
- 最终状态：12 `completed`、9 `waiting_confirmation`、47 `needs_clarification`、1 `failed`。
- Case Latency 可用，但不能从本报告将网络等待、Provider 重试和模型推理解耦。EVAL-095 的 555,183 ms 是最大值，报告没有 Provider Attempt 明细可解释该异常值。

### 模型调用次数分布

| Model Calls / Case | Case 数 |
|---:|---:|
| 0 | 21 |
| 1 | 4 |
| 2 | 10 |
| 3 | 11 |
| 4 | 10 |
| 5 | 12 |
| 6 | 1 |

调用次数和单次输入大小必须分开优化：前者减少整个请求，后者缩小每次请求。历史 Top Case 主要同时受到 5–6 次调用和约 4.7K/次输入影响。

## 3. Top 10 高成本用例

| Case | 类型 | Model / Tool Calls | Input | Output | Total | 最终状态 | Blocking Errors |
|---|---|---:|---:|---:|---:|---|---|
| EVAL-279 | multi_turn / generate_then_confirm | 6 / 4 | 29,885 | 432 | 30,317 | completed | - |
| EVAL-067 | generation / shortage_or_conflict | 5 / 3 | 26,301 | 857 | 27,158 | needs_clarification | insufficient_candidates, joint_constraints_infeasible |
| EVAL-055 | generation / shortage_or_conflict | 5 / 3 | 26,055 | 567 | 26,622 | needs_clarification | missing_question_count |
| EVAL-063 | generation / shortage_or_conflict | 5 / 3 | 25,729 | 436 | 26,165 | needs_clarification | insufficient_candidates, joint_constraints_infeasible |
| EVAL-061 | generation / shortage_or_conflict | 5 / 3 | 25,562 | 305 | 25,867 | needs_clarification | insufficient_candidates, type_supply_shortage |
| EVAL-054 | generation / shortage_or_conflict | 5 / 3 | 25,576 | 288 | 25,864 | needs_clarification | insufficient_candidates, type_supply_shortage |
| EVAL-053 | generation / shortage_or_conflict | 5 / 3 | 25,331 | 450 | 25,781 | needs_clarification | insufficient_candidates, no_eligible_candidates |
| EVAL-049 | generation / shortage_or_conflict | 5 / 3 | 25,284 | 452 | 25,736 | needs_clarification | insufficient_candidates, no_eligible_candidates |
| EVAL-056 | generation / shortage_or_conflict | 5 / 3 | 25,169 | 480 | 25,649 | needs_clarification | insufficient_candidates, no_eligible_candidates |
| EVAL-062 | generation / shortage_or_conflict | 5 / 3 | 25,194 | 386 | 25,580 | needs_clarification | insufficient_candidates, no_eligible_candidates |

9/10 是 Generation shortage/conflict。该聚类首先指向“调用次数/终止策略”，而不是仅缩短单次 Prompt。

## 4. 上下文组成

162/163 个模型调用满足 `context_breakdown` 各部分之和等于 `total_chars`。以下比例只使用这 162 个可核对调用：

| Context 部分 | 聚合字符数（全部调用） | 可核对调用字符占比 |
|---|---:|---:|
| Tool Schema | 1,598,196 | **69.15%** |
| System Prompt | 338,868 | 14.71% |
| Workspace | 176,228 | 7.60% |
| Tool Observation | 156,007 | 6.77% |
| Other | 40,272 | 1.75% |
| Conversation History | 388 | 0.02% |

EVAL-267 有 1 个调用的归因和 `total_chars` 相差 527 字符，未纳入比例分母。历史数据的 Conversation History 几乎为空，因此不能用该样本证明真实长会话的 History 成本可以忽略。

### 多轮增长

| Tool Round | Calls | 平均 Input Token | 平均 Schema chars | 平均 Workspace chars | 平均 Observation chars |
|---:|---:|---:|---:|---:|---:|
| 0 | 49 | 4,335.61 | 10,129.94 | 1,099.27 | 0 |
| 1 | 45 | 4,557.96 | 9,895.33 | 1,104.22 | 609.93 |
| 2 | 34 | 5,601.53 | 11,614.62 | 1,063.71 | 1,796.26 |
| 3 | 23 | 4,879.43 | 8,267.65 | 1,043.22 | 1,883.83 |
| 4 | 12 | 4,480.75 | 5,957.17 | 1,042.83 | 2,013.25 |

Observation 随 Tool Round 明显增长；Workspace 大致稳定。输入 Token 不与字符数严格线性变化，原因包括模型 tokenizer、不同 Schema 组合和其他消息内容。

Workspace 聚合中 `other_chars=150,248`，而明确标注的 `paper_chars=19,404`、`active_task_chars=2,852`、`generation_chars=2,720`、`memory_chars=1,004`。历史指标没有展开 Pending Generation 和 Working Memory 的全部字段，因此当前只能确认总 Workspace 规模，不能可靠地按字段给出 Token 占比。

## 5. Tool Schema 开销

历史调用已经存在部分动态暴露，并非每次都发送全部工具：

- 77/163 次调用暴露 8 个工具、13,612 chars。
- Generation：101 次模型调用，平均暴露 6.45 个工具、11,549 chars；实际调用过 `inspect_curriculum`、`inspect_question_bank`、`prepare_generation_plan`。
- Pending：16 次，平均 3 个工具、5,901 chars；实际只调用过 `discard_pending_plan`。
- Paper：9 次，平均 2.67 个工具、4,930 chars；实际调用过 `read_paper`、`preview_paper_changes`。
- Teaching：23 次，平均 3.39 个工具、6,887 chars。

Phase 1 当前 Registry 有 21 个工具，Schema 合计 33,335 chars。最大的单工具 Schema 是：

| Tool | Schema chars |
|---|---:|
| revise_teaching_design | 6,435 |
| create_teaching_design | 6,040 |
| prepare_generation_plan | 5,372 |
| preview_paper_changes | 4,201 |
| prepare_reinforcement_plan | 2,027 |
| prepare_teaching_planning_draft | 1,521 |
| read_paper | 1,390 |

机器报告给出了“当前全部 Registry → 历史任务实际使用工具集合”的理论场景。例如 Generation 的实际使用集合当前 Schema 为 6,999 chars，而全部 Registry 为 33,335 chars。这个差值**不是实际可节省量**：历史 Trace 没有记录每次暴露的工具名称，“实际调用过”也不等于“决策时只需要这些工具”，且当前 Schema 与历史运行版本不同。

## 6. Tool Observation 与 Tool Loop

### Observation

- 模型可见 Observation 累计 156,007 chars，占可核对 Context 的 6.77%。
- 保存的 Tool Output JSON 中，体积累计最高的是 `inspect_question_bank`（36,900 chars）、`prepare_generation_plan`（34,784）、`inspect_curriculum`（21,882）和 `create_teaching_design`（14,431）。
- Tool Output JSON 长度与最终发送给模型的 Observation 文本可能不同，只能作为定位对象的 Proxy。

### 历史循环候选

| 模式 | 结果 | 解释 |
|---|---:|---|
| 同一 Turn 完全相同 Tool Call | 0 | 历史样本未发现 |
| Tool `ok=false` 后继续调用模型 | 0 | 23 个业务失败 Tool 均未在同 Turn 后继续模型调用 |
| Preview 成功后继续模型调用 | 3 | EVAL-141/142/143；后续合计 13,888 Input Token、165 Output Token |
| 中间模型输出无 Tool Call、随后被重新检查 | 30 | 涉及 21 个 Case；这些调用实际消耗 160,092 Input、4,279 Output Token |

30 次中间无 Tool 输出是“优化候选”，不是 160,092 Token 可全部删除的证明；部分调用可能承载恢复或最终回答判断。它们占历史 Input Token 的 20.68%，说明调用次数治理的量级可能高于微调 Prompt 文案。

Phase 1 已加入有界恢复、候选不足快速终止、Preview 终止边界、工具去重和结构化只读复用。上述问题属于历史证据；当前分支是否已消除及其 Token 效果仍需后续同条件离线/受控前测验证。本轮不重新调用模型。

Router 确定意图但仍分类、结构化结果后重复查询，无法仅凭该报告可靠识别。历史样本没有完全相同 Tool Call 重复，不能据此推断其他语义等价重复不存在。

## 7. Trace、重试与数据质量

- JSON 同时保存 `turns[].observability` 和 `actual.evaluated_turns[].observability`。按 Span ID 检出 163 份 Model Span 和 112 份 Tool Span 的重复投影；它们是存储副本，未重复计入执行次数。
- 163 个 Model Span 均有 Provider Token usage。
- 163 个 Model Span 均无 `provider_attempts`，所以失败重试次数及其 Token/Latency 归因是 `unavailable`，不能从零记录推断为零重试。
- 历史 Trace 仅记录暴露工具数量和总 Schema chars，未记录每次暴露工具名称。
- Conversation History 样本覆盖不足；Working Memory/Pending 字段拆分不足；一个 Context Breakdown 无法对账。
- Case Latency 可用，单独 Provider Attempt Latency 不可用。

## 8. 优化优先级与下一阶段建议

### P1 推荐顺序

1. **先优化 Tool Schema / Tool Exposure。** 它占 Context Size Proxy 的 69.15%，每个模型调用都会重复发送，并且已有 Task/Boundary 路由可作为确定性选择依据。先增加“每次暴露工具名称 + 每工具 Schema chars”观测，再做保守的 Dynamic Tool Selection A/B。
2. **并行验证调用次数治理。** 历史 30 次中间无 Tool 输出涉及 160,092 个真实 Input Token；先分类哪些已被 Phase 1 修复，再建立相同 Case 的调用次数前测，不能直接宣称这些 Token 可删除。
3. **第二优先优化 Tool Observation。** 它当前占 6.77%，并从 Round 1 的平均 610 chars 增至 Round 4 的 2,013 chars。优先对题库、生成计划和课程检查结果做有损风险低的字段级投影，保留业务错误码、版本、证据和候选边界。
4. **Working Memory 暂不先改。** 全部 Workspace 为 7.60%，但明确标注的 `memory_chars` 很小，绝大部分落在未分类 `other_chars`。应先补字段级观测，再决定压缩 Pending/Memory；不可为省 Token 删除版本、约束来源、Pending 状态或确认安全字段。
5. Provider Attempt 观测补齐后再评估失败重试成本。

### 明确结论

**应该先优化 Tool Schema。** 原因是它是当前可核对上下文中最大、重复频率最高且最容易由确定性 Task/Boundary 安全裁剪的部分（69.15% Context Size Proxy）。Tool Observation 排第二，因为它在多轮中持续增长；Working Memory 在历史报告中拆分不足，现阶段先压缩会带来状态与业务安全风险。

所有减少量目前都只是字符规模或历史候选调用的理论影响，尚无实际 Token 降低结论。

## 9. 复现

```bash
.venv/bin/python tests/evals/token_optimization/token_baseline_audit.py
.venv/bin/pytest tests/evals/token_optimization/test_token_baseline_audit.py -q
.venv/bin/ruff check tests/evals/token_optimization
```

输出：`tests/evals/token_optimization/reports/phase2_p0_cost_profile.json`。
