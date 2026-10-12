# Phase 1 — Agent Runtime & Tool Reliability Optimization

## 阶段目标

冻结 Agent Runtime 与 Tool Calling 的可靠性改进，形成后续 Token Efficiency 与推理成本优化的可复现工程基线。本阶段不包含 Token 优化实现。

## 已完成能力

- Semantic Router 与确定性路由纠偏
- Agent Tool Loop、工具暴露边界与终止边界
- Working Memory 与跨轮题目指代，并按当前 Paper Version 校验题目焦点
- Pending Generation 多字段约束合并与暂停/确认语义保护
- Paper Preview → Confirm 双阶段操作及业务状态保护
- 工具调用去重、业务等价参数归一化与幂等处理
- 有界错误恢复、Provider 尝试可观测性与候选不足快速终止
- 只读查询结构化结果复用，避免重复读取和模型臆测
- 自动化回归及 Pending、只读查询、上下文与工具恢复的定向真实 API 验收

## Runtime 与 Tool Calling 改进

Runtime 在执行前使用确定性信号纠正高风险误路由，并仅向模型暴露当前边界需要的工具。变更类操作必须先生成绑定版本的 Preview，再由显式确认执行；成功的变更调用按 Paper/Version 作用域去重。Working Memory 只保存可由数据库、Pending 状态或工具结构化结果验证的题目焦点。候选不足等确定性业务失败会立即给出有限选项，不再进行无界重试。只读结果保留结构化事实，供最终响应直接复用。

## 评测与统计口径

以下结果来自不同时间、不同版本或不同执行方式，不能合并为一个统一成功率：

| 评测 | 结果 | 口径 |
|---|---:|---|
| Official Holdout V3.1 → V3.2.1 | 25/69 → 64/69 | 两次正式、冻结配置的历史运行；V3.2.1 无回归、5 条仍失败 |
| Dev117 首测 | 97/117 | 首次 Dev117 执行结果 |
| Dev117 已核验离线复评 | 104/117 | 对已保存结果的离线核验，不是重新调用模型取得的性能提升 |
| 阶段封存确定性回归 | 413 PASS、1 SKIP | `.venv/bin/pytest tests/teacher_agent tests/paper_change tests/generation -q`；因当前环境无 `uv`，使用项目虚拟环境中的同一 pytest；未调用付费模型 API |
| Pending 多字段定向验收 | 2/2 PASS | 定向真实 API 历史验收 |
| 只读查询定向验收 | 2/2 PASS | 定向真实 API 历史验收 |
| 上下文与工具恢复 | 3 条通过 | 此前定向业务验收 |

封存检查同时通过：`.venv/bin/ruff check src/calculus_agent` 与 `git diff --check`。

## 已知限制

- Official Holdout V3.2.1 仍有 5 条历史失败，涉及难度契约、上下文依赖判断、路由、末题地址和 Pending 保留。
- 真实 API 定向结果是历史验收证据，本次封存未重新调用模型。
- Runtime 中仍存在较大的 Tool Schema 与上下文开销；本阶段优先保证正确性和安全性，尚未优化 Token。
- 原始评测报告可能包含完整请求、响应和 Trace，因此保持为本地忽略文件，不纳入阶段提交。

## Phase 2 Token 优化历史参考基线

该基线仅用于历史参照，**不能直接视为 Phase 2 新分支上的同条件优化前测**；开始优化前应确认代码、数据集、Provider、模型和运行配置是否可比。

- 原始报告：`tests/evals/reports/official-holdout-v3.2.1-69-second-20261006T045441Z.json`（本地忽略，不提交）
- 报告时间：`2026-10-06T05:30:54.008906+00:00`
- 历史 Commit：`f8d9b013fe6b4658a0a507159f24b7ebcbd030d6`，报告记录 `git_dirty=false`
- 数据集：`tests/evals/staging/tutor_workflow_eval_300_v3_1/tutor_workflow_eval_300_v3_1.yaml`
- 数据集版本 / SHA256：`2f3c9c48936a` / `2f3c9c48936a8208a53f850194d15118be9601e380e1249726a42cc140e394ad`
- Provider / 模型：`siliconflow` / `Qwen/Qwen3.5-35B-A3B`
- Adapter / 协议：`BailianChatBackend` / `openai_compatible`
- 运行配置：`state-policy`；tools、persistent state、grounding guard、confirmation guard、recovery policy 均启用；temperature `0`；max tool rounds `8`；timeout `120s`；case delay `5s`；Provider 最多重试 `4` 次，指数退避基数 `5s`、上限 `60s`
- 统计口径：69 条 Official Holdout，64/69 PASS；163 次模型调用；输入 Token `774001`；输出 Token `16538`；总 Token `790539`；零模型调用用例 `21` 条。Token 来自 Provider usage，所有发生模型调用的用例均有实际 usage。

本次封存不调用模型重新评测。Phase 2 应从已冻结提交建立分支，并另行生成可比的优化前测。
