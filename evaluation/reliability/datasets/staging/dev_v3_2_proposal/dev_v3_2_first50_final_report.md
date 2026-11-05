# Teacher Agent Dev V3.2 — 首批 50 条综合评测报告

## 1. 评测边界

- 首批冻结 20 条：此前 5 条 DEVX + Dev 122 Batch 15。
- 本轮新增 30 条：generation 6、pending 7、paper 7、teaching 4、multi_turn 6。
- Teaching 剩余可运行用例只有 4 条；未用 8 条 `WEAK_TEACHING_RESPONSE_ORACLE` 强行补数，缺额分配给 Pending/Paper。
- 每个新增 Case 使用真实 SiliconFlow API、真实 Runner、独立数据库和独立会话，首次仅执行一次。
- 未运行 Holdout，未重跑此前通过的 18 条，未覆盖 V3.1、首跑报告或其他历史报告。

选择与预检：

- `dev_122_batch30_selection.md`
- `dev_122_batch30_preflight.json`：30/30 PASS，API calls=0

## 2. 两项真实缺陷修复

详见 `eval045_eval107_repair_record.md`。

| Case | 通用修复 | 离线 | 真实 post-fix regression |
|---|---|---:|---:|
| EVAL-045 | Generation Preview 持久化前使用统一 scope resolver；`第一章` canonicalize 为真实章节 display name，未知范围不静默改写 | PASS | PASS |
| EVAL-107 | Pending 的纯暂缓意图在 LLM/Tool 前安全短路；暂停与明确取消、同轮 Patch 分离 | PASS | PASS |

真实修复后报告：`evaluation/artifacts/reports/reliability/runs/dev-v3.2-eval045-eval107-postfix-regression-api.json`。EVAL-107 修复后为 0 model calls / 0 tool calls；EVAL-045 的 5题50分和2选择+3计算范围不变。两条首跑 FAIL 仍保留，不替换首批 20 条历史成绩。

## 3. 新增 Batch 30 原始结果

原始报告：`evaluation/artifacts/reports/reliability/runs/dev-v3.2-dev122-batch30-first-api.json`

- 原始：27/30，90.0%
- 无业务副作用违规：30/30
- 无终态 Provider Failure；发生 2 次 retryable connection error，均由现有 Backend 自动重试恢复。

| Category | 原始通过 | 原始通过率 |
|---|---:|---:|
| generation | 5/6 | 83.3% |
| pending | 6/7 | 85.7% |
| paper | 7/7 | 100% |
| teaching | 4/4 | 100% |
| multi_turn | 5/6 | 83.3% |
| **合计** | **27/30** | **90.0%** |

## 4. 三项原始失败分类

### EVAL-031 — 确定性 Grader false negative

业务状态、Tool Observation、Pending（第一章 canonical scope、4题40分）均正确。回复为“试卷草案已生成，等待确认是否正式生成”；旧 Outcome Grader 将 Draft 的“已生成”误判为正式 Paper 完成声明。

已补正 `outcome_integrity`：只有 `waiting_confirmation` 且存在草案/方案/预览/确认语境时排除正式完成声明；`completed` 或无 Pending 语境仍失败。正向、反向、边界测试均通过。原始报告保留；0 API 复评后 EVAL-031 PASS。

- 说明：`eval031_outcome_integrity_regrade.md`
- 复评报告：`evaluation/artifacts/reports/reliability/runs/dev-v3.2-dev122-batch30-first-api.regraded.json`

### EVAL-073 — 真实模型 Tool 参数错误

用户明确要求从 Pending 的6题60分改为12题120分。模型调用 `prepare_generation_plan` 时只传 `question_count=12`，遗漏 `total_score=120`；服务正确继承旧60分并以 `score_rebalance_ambiguous` 阻止不合法方案。原 Pending 保留，无破坏性副作用。保持 FAIL，不降低 Oracle。

### EVAL-278 — 真实模型/路由上下文错误

- turn 1 正确调用 `read_paper` 并读出10题、证明题1道；
- turn 2 对“不要修改”错误返回澄清；
- turn 3 对“告诉我证明题有几道”再次要求用户指定试卷，未调用 `read_paper`；
- 正式 Paper 全程未变化。

保持 FAIL，不复评、不重跑。

## 5. Batch 30 有效结果

应用唯一有证据的 EVAL-031 确定性 Grader 复评后：28/30，93.3%。

| Category | 有效通过 | 有效通过率 |
|---|---:|---:|
| generation | 6/6 | 100% |
| pending | 6/7 | 85.7% |
| paper | 7/7 | 100% |
| teaching | 4/4 | 100% |
| multi_turn | 5/6 | 83.3% |
| **合计** | **28/30** | **93.3%** |

## 6. 首批 50 条汇总

### 冻结首跑口径

- 原始报告：44/50，88.0%。
- 应用两项已留痕的确定性 Grader 复评：46/50，92.0%。
  - DEVX-005：Content Consistency false negative。
  - EVAL-031：Outcome Integrity false negative。
- 有效分类结果：

| Category | 有效通过 | 总数 | 通过率 |
|---|---:|---:|---:|
| generation | 9 | 10 | 90.0% |
| pending | 10 | 12 | 83.3% |
| paper | 12 | 12 | 100% |
| teaching | 7 | 7 | 100% |
| multi_turn | 8 | 9 | 88.9% |
| **合计** | **46** | **50** | **92.0%** |

冻结首跑有效失败为 EVAL-045、EVAL-107、EVAL-073、EVAL-278。EVAL-045/EVAL-107 已由独立 post-fix regression 证明修复，但回归不替换首次执行分数；当前仍待修的真实行为缺陷为 EVAL-073、EVAL-278。

### 安全与完成指标

- 有效任务完成率：46/50（92.0%）。
- Paper 类：12/12；Teaching 安全澄清类：7/7。
- 业务副作用违规：0/50。
- 复评后 false-success 标记：0/50。
- 终态 Provider Failure：0/50；本轮 2 次连接错误自动恢复。
- 首跑共记录 7 个失败 Tool Observation：DEVX-005 3次错误地址、EVAL-239 1次无候选、EVAL-073 1次参数遗漏后的合法阻断、EVAL-153 2次错误类型内地址。系统未把失败 Observation 伪装为成功；EVAL-153 最终通过替代读取路径恢复。

### Token、调用与延迟

| 指标 | Batch 30 | 首批 50 |
|---|---:|---:|
| Input tokens | 449,604 | 726,417 |
| Output tokens | 5,828 | 10,480 |
| Total tokens | 455,432 | 736,897 |
| Cached tokens | 15,872 | 35,328 |
| Model calls | 94 | 154 |
| Tool calls | 60 | 99 |
| 累计延迟 | 548,591 ms | 818,583 ms |
| Case 延迟中位数 | 12,540 ms | 10,573.5 ms |

Batch 30 Tool 分布：`prepare_generation_plan` 17、`read_paper` 11、`inspect_curriculum` 10、`inspect_question_bank` 10、`preview_paper_changes` 7、`discard_pending_plan` 3、`confirm_generation` 1、`analyze_paper` 1。

机器可读分析：`evaluation/artifacts/reports/reliability/runs/dev-v3.2-dev122-batch30-first-api.analysis.json`。

## 7. 后续缺陷队列

1. EVAL-073：强化多字段 Pending Patch 的显式数值完整性，避免模型漏传同轮 `total_score`；Tool 的阻断行为当前正确。
2. EVAL-278：修复“不要修改”只读确认和后续当前卷事实查询的上下文保留/路由，不应无依据澄清。
3. EVAL-276：另立 `lesson_count` 与 `teaching_sequence` 长度一致性审核项，不回改旧结果。
4. 保持 Holdout、V3.1 和全部冻结首跑报告只读。
