# Teacher Agent V3.2 Dev117 — 13 条失败只读根因审计 v1

**依据**：用户上传的 `v3_2_dev117_failure_audit_evidence.json/.md`，共 13 条首次失败索引；另有 EVAL-045/107 的独立回归记录。

**冻结口径**：首次真实 API 97/117（82.9%）；已核验离线复评 104/117（88.9%）。独立回归不能改变首次成绩。

## 审计结论

- **7 条可确认为 Agent 行为与预期不符**（是行为层归因，尚未核对源代码实现路径）。
- **4 条需进一步契约或源码核对**：EVAL-186、124、184、278。
- **2 条已有独立 API PASS 回归**：EVAL-045、107；还需与当前仓库 Commit 关联。

**重大证据限制**：本次提取器将每条 `actual` 与 `turns` 序列化文本截断到约 12,030 字符，因此不能据此声称已逐字段比对了所有多轮 Trace 和完整数据库前后镜像。`expected` 与各 Grader 内容可读取，工具调用详情仅对未被截断的条目完整。

## 13 条 Case 汇总

| Case | 优先级 | 审计分类 | 可复核的首次偏差 |
|---|---|---|---|
| EVAL-152 | P0 | `AGENT_DEFECT` | 缺少明确替换目标，却读取试卷并创建“第1道选择题”替换 Pending；原 Paper 未确认修改 |
| EVAL-192 | P0 | `AGENT_DEFECT` | “刚才那题”缺少本轮可验证的指代，Agent 推定为选择题第1题并创建替换 Pending |
| EVAL-194 | P0 | `AGENT_DEFECT` | “删除一道计算题”仅指定题型，Agent 选择计算题第1题（全卷位置7），生成删除 Pending |
| EVAL-073 | P0 | `AGENT_DEFECT` | 用户指定12题120分；prepare_generation_plan 仅发送 question_count=12，遗漏 total_score=120，继承旧60分导致 score_rebalance_ambiguous |
| EVAL-078 | P0 | `AGENT_DEFECT` | 用户指定8题100分；prepare_generation_plan 仅发送 question_count=8，遗漏 total_score=100，继承旧60分并失败 |
| EVAL-186 | P1 | `UNRESOLVED` | read_paper 成功读取填空题，但最终状态 failed，错误 paper_change_preview_required；缺失预期 response_facts |
| EVAL-188 | P1 | `AGENT_DEFECT` | 4道选择题已读出，Agent 继续读选择题第5题，触发 question_address_not_found；答复正确为4但最终 failed |
| EVAL-124 | P1 | `UNRESOLVED` | Agent 用 read_paper(positions=[1]) 给出了正确的全卷第1题信息，Oracle 却要求 needs_clarification 且禁止 read_paper |
| EVAL-184 | P1 | `UNRESOLVED` | Agent 用 read_paper(positions=[10]) 给出了第10题题干，Oracle 要求 needs_clarification 且禁止 read_paper |
| EVAL-195 | P1 | `AGENT_DEFECT` | 用户仅说“调整这套卷”，未指定调整项；Agent 只汇总试卷信息并以 completed 结束，没有询问修改目标 |
| EVAL-278 | P1 | `UNRESOLVED` | 最终状态 needs_clarification，与 expected completed 不符；多轮 grader 指出后续轮未 read_paper，但长 turns 被截断，无法核实上下文是否已提供 |
| EVAL-045 | P2 | `ALREADY_FIXED` | 首次 scope_names 为“第一章”，不等于预期的“第一章 函数与极限”；独立 API 回归全部 Grader PASS |
| EVAL-107 | P2 | `ALREADY_FIXED` | 首次将“先别生成”误解为取消，调用 discard_pending_plan；独立 API 回归保持 waiting_confirmation 和原 Pending，全部 Grader PASS |

## 优先级建议

**A. P0 目标定位安全（152/192/194）**：未明确目标时不得按默认“第1题”生成 `pending_paper_change`。已有 Trace 表明主 Paper 未被确认修改，但错误 Pending 已持久化。重点排查目标解析、工具调用前校验和 Preview 准入规则。

**B. P0 Pending 多字段 Patch（073/078）**：`prepare_generation_plan` 显式传了新题数，但漏传新总分，旧总分 60 被继承并诱发 `score_rebalance_ambiguous`。先检查参数提取/工具调用是否完整，再检查服务端 merge；不能仅把工具的约束报错屏蔽掉。

**C. P1 只读查询（186/188）**：186 成功 `read_paper` 仍触发 `paper_change_preview_required`，怀疑后置状态门禁错误；188 先读到 4 道选择题，继续请求不存在的第5道，触发 `question_address_not_found` 并使最终状态 `failed`，虽然文字回答正确。两个 Case 的机制不同，应分别建回归。

**D. P1 题号语义契约（124/184）**：成功读取全卷 position 1 / 10，Oracle 却规定 `needs_clarification` 且禁止 `read_paper`。若产品约定 '第N题' 必须澄清，Agent 执行策略有错；若已有明确全卷序号且只读可查，Oracle 过严。**不得未经裁定修改 Oracle 或直接离线复评 PASS。**

**E. P1 缺少操作参数（195）**：用户仅说“调整这套卷”，Agent 返回试卷情况并给出 `completed`，没有询问要修改什么。应检验缺参数指令如何从信息查询与修改意图中区分。

**F. P1 多轮上下文（278）**：最终 `needs_clarification`，多轮 Grader 报告第 2 轮异常状态和第 3 轮未调用所需 `read_paper`。但上传的 `turns` 已在第一轮快照内截断，**尚不能认定是上下文丢失还是 Oracle 不合理**。

**G. P2 已有回归（045/107）**：045 首测章节字符串为“第一章”而非完整 canonical 名称；107 首测把暂停当取消而调用 `discard_pending_plan`。独立回归内两个 Case 的所有 Grader 均 PASS，但尚未核验此回归对应当前代码 SHA。

## 最小修复建议与验收

1. **先修安全决策，勿先改提示词凑通过**：统一确定性 TargetResolver，修改类动作要求可唯一解析；否则澄清，且 `pending_paper_change=None`。覆盖 152/192/194 三类输入。
2. **修显式覆盖字段的合并链路**：将一次用户消息里的 `question_count`、`total_score` 与 `scope_names` 作为原子 Patch 传入，验证请求快照与版本，并覆盖 073/078 的 12题120分和 8题100分。
3. **读写流程隔离**：只读查询不得依赖 `preview_paper_changes`；读取失败不得篡改主 Paper。对于数量查询使用已验证统计，不探测无效的第 5 项。
4. **产品/Oracle 决策**：文档化不带全卷前缀的“第N题”用于只读和修改时是否要求澄清，并成对增加对照用例；在决议前保留 124/184 原始 FAIL。
5. **补齐 278 完整多轮 Trace**：导出每轮 user/request、active paper、工具名/参数/结果、最终 status，避免整库状态挤占审计容量。
6. **固定回归 Provenance**：给 045/107 的回归报告补上 Git SHA、Fixture SHA 和 Runtime/Grader 版本，而不是把回归 PASS 回填首次测试。

## 核验门槛

本报告不是“已经完成源码级根因核查”的声明。本轮没有读取 `src/calculus_agent/` 的实际代码，也没有执行任何业务修改或模型 API 调用。确认源码级位置后再开展针对性修复。

## 可追溯的原始报告位置

- `EVAL-152`：`tests/evals/reports/dev-v3.2-remaining-batch06-first-api.json` → `/results/0`
- `EVAL-192`：`tests/evals/reports/dev-v3.2-remaining-batch07-first-api.json` → `/results/3`
- `EVAL-194`：`tests/evals/reports/dev-v3.2-remaining-batch07-first-api.json` → `/results/5`
- `EVAL-073`：`tests/evals/reports/dev-v3.2-dev122-batch30-first-api.json` → `/results/7`
- `EVAL-078`：`tests/evals/reports/dev-v3.2-remaining-batch04-first-api.json` → `/results/8`
- `EVAL-186`：`tests/evals/reports/dev-v3.2-remaining-batch06-first-api.json` → `/results/7`
- `EVAL-188`：`tests/evals/reports/dev-v3.2-remaining-batch06-first-api.json` → `/results/9`
- `EVAL-124`：`tests/evals/reports/dev-v3.2-remaining-batch05-first-api.json` → `/results/8`
- `EVAL-184`：`tests/evals/reports/dev-v3.2-remaining-batch06-first-api.json` → `/results/5`
- `EVAL-195`：`tests/evals/reports/dev-v3.2-remaining-batch07-first-api.json` → `/results/6`
- `EVAL-278`：`tests/evals/reports/dev-v3.2-dev122-batch30-first-api.json` → `/results/28`
- `EVAL-045`：`tests/evals/reports/dev-v3.2-dev122-batch15-first-api.json` → `/results/2`；回归：`tests/evals/reports/dev-v3.2-eval045-eval107-postfix-regression-api.json`
- `EVAL-107`：`tests/evals/reports/dev-v3.2-dev122-batch15-first-api.json` → `/results/4`；回归：`tests/evals/reports/dev-v3.2-eval045-eval107-postfix-regression-api.json`

**结构化结果**：`teacher_agent_v3_2_root_cause_audit_v1.json`