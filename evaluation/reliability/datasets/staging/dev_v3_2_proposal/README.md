# Teacher Agent Dev V3.2 — 离线修订候选包

> **状态：候选提案，不是已冻结的正式 Benchmark，也不是“142/142 已审核通过”。**
>
> 来源：用户提供的 `teacher-agent-eval-review-bundle.zip`（Dev 200 YAML 与部分核心代码）。

## 教师审核更新（当前有效合同）

- 有效候选保持 **Dev 122 + DEVX 20 = 142**。
- `DEVX-015` 已排除：并发 Pending 不符合教师实际使用习惯；新增拆分场景 `DEVX-021`。
- 142 条已完成静态合同审查：**129 条 approved/READY，13 条 pending/not-ready**；逐项证据见 `dataset_review_142.json`。
- `EVAL-149` 与 12 条 DEVX 仍保留明确的能力、Grader、Parser、故障测试或 Runner 阻塞，不得进入正式批准；详见 `remaining_review_items.json`。
- EVAL-217/218/219/221 与 DEVX-016 接入现有 Consultation Judge；只有 `RUN_LIVE_JUDGE=1` 才允许调用 Judge API。Judge 失败归类为评测基础设施失败。
- 本轮只做离线代码、Fixture、Schema 和测试验证；未调用真实 LLM、Smoke、Dev、Holdout 或 Langfuse，未冻结 Manifest。

## 已完成的可验证工作

| 项目 | 数量 | 说明 |
|---|---:|---|
| 原始 Dev | 200 | 保留原版不变 |
| 严格重复归档 | 78 | `turns + setup + expected + graders` 完全相同，不作为新能力重复计算 |
| 保留独立测试定义 | 122 | 200 - 78，保留原 Case ID 和原教师 `turns` |
| 修改了业务字段的原有定义 | 67 | 80 项字段级变更，详见日志 |
| 新增能力候选 | 20 | 有效 ID 为 `DEVX-001~014, 016~021`；无一条从冻结 Holdout 复制 |
| 当前候选定义合计 | 142 | 122+20；**不是全数 READY** |

### 改动类型（按字段变更次数）

- 45 次 `pending_generation.scope_names` 第一章范围补充。
- 12 次 Pending 初始 Fixture 从 `6题60分` 调整到 `10题100分`，使其与原指令中“仍然/不变/降到”及原 Oracle 一致；总共原先报告的 24 次来自重复用例。
- 3 条不同的“读当前卷指定题”修复了误写的 `needs_clarification` 及 `read_paper` 禁令；第 4 条原问题在重复归档中。
- 1 条不同的 Pending 确认用例增加 Paper 题量 6 和总分 60 的结果验证。
- 4 条读取相关用例增加或增强 `response_facts` 事实字段（对应字段变更详见 `change_log.jsonl`）；仍需检验正则覆盖率。

### 仍未修复的部分

当前完整 142 条审查中保留 13 个未就绪项，详见 `remaining_review_items.json`。
没有已知未决问题也不等于未经审核即可自动批准；未在本轮教师审核范围内的 Case 保持原状态。

历史主要未决类别如下；本轮相关 16 条已按教师意见更新：

- `WEAK_READ_RESPONSE_ORACLE`：单纯调用 `read_paper` 并不足以证明最终回复准确。
- `WEAK_TEACHING_RESPONSE_ORACLE`：Workflow Grader 不等同于教学质量 Judge。
- `INTERMEDIATE_TURN_ORACLE_WEAK`：多轮最终状态一致，仍可能隐藏错误的中间状态或副作用。
- `ADDRESS_SEMANTICS_NEEDS_REVIEW`：全卷题号是否默认可用于替换/预览，要以正式业务语义为准。
- `RESPONSE_FACTS_PARSER_VALIDATION`：正则无法覆盖所有合理的“题数/总分”自然语言回答。
- `READ_ITEM_CONTENT_GRADER_GAP`：仅验证读题工具调用，尚不能验证准确复述题干。

## 文件用途

- `dev_122_revised_candidate.yaml`：核心去重后修订集合；所有 Case 保留 `review.status=pending`。
- `excluded_duplicates_78.yaml`：被移出的 78 条原始定义，完整留档。
- `duplicate_map.csv`：重复 ID → 保留 ID 对应表。
- `dev_200_archive_with_review_flags.yaml`：按旧 200 个 ID 顺序保留的历史对照版。**不得把它作为去重后的评分分母。**
- `new_capability_20_candidate.yaml`：20 条有效新能力场景候选；已删除 DEVX-015 并新增 DEVX-021。
- `dev_v3_2_reviewed_142.yaml`：Dev 122 + DEVX 20 的单文件冻结输入快照。
- `dataset_review_142.json`：142 条分类、输入、预期、Fixture、Grader 对应关系的静态审查证据。
- `change_log.jsonl`：原用例每个业务字段更改的前后值、原因和证据等级。
- `remaining_review_items.json`：未解决旧用例的汇总。
- `offline_validation.json`：本地 YAML roundtrip、CaseLoader、去重、保留原 turns 等基本离线验证结果。
- `summary.json`：统计摘要。
- `MANIFEST.sha256`：文件内容 SHA-256。

## 新增20条的审查分层

新的 DEVX 用例主要覆盖版本恢复、连续确认幂等、目标指代、多轮只读、防写入、题型别名、跨 Pending 隔离、故障注入、知识点溯源、学生错题巩固、跨用户会话隔离等。

审核状态以每条 Case 的 `review.status` 和 `review.readiness` 为准。教师确认不等于能力已实现：尤其 DEVX-019 必须保持 `NEEDS_CAPABILITY`；未审核的历史 READY 候选也不会被自动改为 approved。

## 建议导入位置与不调用模型的静态检查

将整个包解压到仓库中的 `evaluation/reliability/datasets/staging/dev_v3_2_proposal/`，例如：

```bash
cd /Users/shengyue/Documents/Teacher_Agent/calculus_knowledge_agent
mkdir -p evaluation/reliability/datasets/staging/dev_v3_2_proposal
# 将本压缩包中的文件复制到上述目录
uv run python evaluation/reliability/datasets/staging/dev_v3_2_proposal/validate_offline.py
```

**本步不运行真实 Agent。** 通过离线验证、人工审核后，才挑选 `review.status` 真正获批并通过 Fixture 检查的 Case，运行现有 `evaluation/reliability/runner.py`。不能直接对候选集合全量实测并把通过率称为正式 Dev 成绩。

## 运行环境注意事项

- 本包只有部分业务代码，不是完整项目源码，也不包含真实 SQLite 题库。实际业务流程、API 接口和真实题目供给尚未验证。
- 参考模型服务为 SiliconFlow；API Key 留在本机环境变量，**不要上传、打包或提交**。
- 原始 Dev V3.1、官方 Holdout 和历史报告不得覆盖。修订提案需要新 Git 分支和独立评测版本。
