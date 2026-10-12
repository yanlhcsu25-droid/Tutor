# Phase 2 P0/P1 — Token Profiling & Dynamic Tool Schema Optimization

**状态：Experiment Validated / Not Production Enabled**

## 1. Executive Summary

Phase 2 P0/P1 完成了 Token 成本归因、工具 Schema 依赖审计、Dynamic Tool Selection 实现、Shadow Audit 和定向真实 Provider A/B 验证。实验开关默认关闭，未宣称生产环境全面上线，也未修改 Phase 1 稳定 Tag。

结论：Tool Schema 是当前最优先的输入上下文优化对象。P1 在历史 Trace 上的离线 Schema 字符投影从 `1,750,579` 降至 `1,017,515`，减少 `41.88%`；这只是字符投影，不是 Provider Token 降幅。三组人工核实业务成功的真实 A/B 场景输入 Token 从 `83,529` 降至 `77,985`，下降 `6.64%`，是三组筛选样本的定向结果，不是全部业务平均值。

## 2. Phase 1 与 Phase 2 版本

- 仓库：`https://github.com/yanlhcsu25-droid/Tutor`
- Phase 1 Tag：`tool-runtime-optimized-v1.0.0`
- Phase 1 Commit：`746a94345e31ce3e92d6f804123766782ec19888`
- Phase 2 分支：`feat/token-optimization-v1`
- Feature Flag：`AGENT_DYNAMIC_TOOL_SELECTION_ENABLED=false`
- 当前状态：实验验证完成，默认不启用

## 3. P0 Token 成本归因

历史 Official Holdout 共 69 条：

| 指标 | 数值 |
|---|---:|
| PASS | 64/69 |
| Model Calls | 163 |
| Tool Calls | 112 |
| Input Tokens | 774001 |
| Output Tokens | 16538 |
| Total Tokens | 790539 |
| 零模型调用 | 21 |

Context Size Proxy（字符数，不是 Provider Token）：

| 部分 | 字符占比 |
|---|---:|
| Tool Schema | 69.15% |
| System Prompt | 14.71% |
| Workspace | 7.60% |
| Tool Observation | 6.77% |
| Conversation History | 0.02% |
| Other | 1.75% |

因此 P0 优先选择 Tool Schema，而不是把字符占比直接换算为 Token 节省。

详情：[`phase2_p0_cost_profile.md`](../token_optimization/phase2_p0_cost_profile.md)。

## 4. Dynamic Tool Selection 架构

```text
TaskRouter → ToolExposurePolicy → Dynamic Selection → Toolkit.schemas()
```

P1 使用任务类型、原始指令、当前 Paper/Pending 生命周期和路由置信度裁剪模型可见工具集合，不新增 LLM Tool Router。支持：

- READ
- GENERATION
- PAPER_CHANGE
- Pending Generation / Paper Change Confirm、Cancel、Patch
- TEACHING_DESIGN
- MIXED / UNKNOWN 安全回退

裁剪只影响模型可见 Schema；模型调用仍经过原有 exposed-tool 检查、Pydantic 参数校验、ToolExecutor、Paper Version 校验、Pending 校验和 Preview → Confirm 边界。未改变业务数据库契约。

Feature Flag 关闭时保持 Phase 1 工具暴露行为。

## 5. Shadow Audit

历史 Shadow Audit：69 条历史用例、70 个 Turns、163 次模型调用。

| 指标 | 结果 |
|---|---:|
| Historical Required Tool Coverage | 100% |
| Unsafe Pruning | 0 |
| 平均暴露工具数 | 6.33 → 3.46 |
| 加权 Schema Chars | 1,750,579 → 1,017,515 |
| Schema 字符投影减少 | 41.88% |
| Fallback | 29/70 Turns；16/163 Model Calls |

**41.88% 是离线 Schema 字符节省投影，不是实际模型 Token 降幅。**

审计限制：历史 Trace 没有完整保存实际 Task Route 和每轮原始暴露工具集合。Shadow Audit 使用当前确定性/启发式 Router 重建路由，并使用保守多轮工具链检查历史成功调用覆盖。因此该结果不能替代同条件线上前测。

机器结果：[`phase2_p1_tool_schema_shadow_audit.json`](../token_optimization/reports/phase2_p1_tool_schema_shadow_audit.json)。

## 6. 真实模型 A/B 实验

原始实验文件保留在本地，不提交到公开仓库：

- JSON：`tests/evals/token_optimization/reports/phase2_p1_ab_20261012T065612Z_d2433c.json`
- Markdown：`tests/evals/token_optimization/reports/phase2_p1_ab_20261012T065612Z_d2433c.md`
- JSON SHA256：`8248b68520cdf1c74634f06a77f1b0c6b12871f7cac3084b2c33302885e2240a`
- Markdown SHA256：`87ab596c29147bbdd45343811bd7b5bcd880964b3fd0a8a174bcd7a2c604cb4a`

配置：SiliconFlow、`Qwen/Qwen3.5-35B-A3B`、A 关闭动态选择、B 开启动态选择、8 组独立 SQLite Fixture、Provider 真实 Token Usage。

### 全部 8 组

| 指标 | A：关闭 | B：开启 |
|---|---:|---:|
| Input Tokens | 136268 | 120568 |
| Output Tokens | 3879 | 3690 |
| Total Tokens | 140147 | 124258 |
| Model Calls | 29 | 28 |
| Tool Calls | 18 | 17 |
| Business PASS | 5/8 | 5/8 |
| Safety PASS | 7/8 | 7/8 |

全部样本 Input Token 变化为 `11.52%`，Total Token 变化为 `11.34%`。该汇总包含业务失败场景和不同执行路径；A08 的 B 组走确定性澄清、模型调用为 0，不能把全部节省归因于 Schema 裁剪。

### 原始脚本严格可比统计

原始实验脚本认定可比 4 组 A01、A03、A04、A07：

- Input：`93791 → 89494`
- 下降：`4.58%`

人工复核发现 A07 虽然原始验收标记 PASS，但请求同时包含当前试卷换题预览和新试卷生成方案预览，Trace 只证明完成前者。因此 A07 不宜作为完整多任务成功正例。未修改原始 Oracle 或评分结果。

### 三组已核实成功场景

| 场景 | A Input | B Input | 下降 |
|---|---:|---:|---:|
| A01 READ | 14602 | 13032 | 10.75% |
| A03 Pending Generation | 40811 | 37263 | 8.69% |
| A04 Paper Change | 28116 | 27690 | 1.52% |
| 合计 | 83529 | 77985 | **6.64%** |

`6.64%` 是事后筛选的三个已核实成功场景的定向实验结果，不是全体 8 组平均值，也不是生产收益承诺。

## 7. 业务正确性、安全性与失败场景

- 全部 A/B：Business PASS 为 `5/8 → 5/8`。
- Safety PASS 为 `7/8 → 7/8`。
- 未发现 B 组新增安全回归，但 A05 原有安全问题仍未解决。
- A02：两组均出现 `scope_not_found`。
- A05：两组均未正确处理候选不足，并出现非预期 Pending 修改。
- A06：两组均未调用预期的 `create_teaching_design`。
- A07：多业务请求完成性不足，原始验收口径过宽。
- A08：B 组走确定性澄清路径，与 A 组模型执行路径不同。

以上问题本轮只归档，不重新开启业务正确率专项修复。

## 8. 测试与复现

本次实际执行：

- 核心确定性回归：`425 PASS、1 SKIP`（426 collected）
- Feature Flag 开启定向回归：`89 PASS`
- P0/P1 离线审计测试：`2 PASS`
- Eval Grader 定向回归：`49 PASS`
- Provider 可观测性测试：`2 PASS`
- Ruff：PASS
- `git diff --check`：PASS

复现离线分析：

```bash
.venv/bin/python tests/evals/token_optimization/token_baseline_audit.py
.venv/bin/python tests/evals/token_optimization/tool_schema_shadow_audit.py
.venv/bin/pytest tests/teacher_agent/test_dynamic_tool_selection.py tests/teacher_agent/test_model_turn.py tests/evals/token_optimization -q
.venv/bin/ruff check src/calculus_agent tests/teacher_agent tests/evals/token_optimization
```

本次归档不重新运行真实模型 A/B，不运行完整 Dev117，不修改历史报告。

## 9. 最终工程结论

P1 已证明 Dynamic Tool Selection 在 READ、Pending Generation 和 Paper Change 三个核实成功场景中具有真实 Provider Input Token 改善，同时没有观察到 B 组新增安全回归。但实验规模小、场景经过筛选，且存在 A05/A06/A07/A08 限制，不能据此宣称所有业务稳定收益或生产全面启用。

因此当前正确状态是：**Experiment Validated / Not Production Enabled**。保留默认关闭开关，后续如继续优化，应先设计更严格的同条件 A/B 和多业务完成性判定，不在本归档中进入 P2 Context Optimization。

## 10. 简历可引用指标

- Tool Schema 离线字符投影减少 **41.88%**（69 条历史用例、163 次模型调用；字符投影，不是 Token 降幅）。
- 真实模型 A/B 中，三组已核实成功场景 Input Token 下降 **6.64%**（A01/A03/A04，定向筛选样本）。
- 未发现 B 组新增安全回归，但 A05 原有安全问题仍未解决。
