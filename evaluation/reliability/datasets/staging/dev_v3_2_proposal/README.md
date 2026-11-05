# Teacher Agent Dev V3.2 — 离线修订候选包

> **状态：候选提案，不是已冻结的正式 Benchmark，也不是“142/142 已审核通过”。**
>
> 来源：用户提供的 `teacher-agent-eval-review-bundle.zip`（Dev 200 YAML 与部分核心代码）。没有完整真实题库数据库，也没有运行模型或正式 Agent。

## 已完成的可验证工作

| 项目 | 数量 | 说明 |
|---|---:|---|
| 原始 Dev | 200 | 保留原版不变 |
| 严格重复归档 | 78 | `turns + setup + expected + graders` 完全相同，不作为新能力重复计算 |
| 保留独立测试定义 | 122 | 200 - 78，保留原 Case ID 和原教师 `turns` |
| 修改了业务字段的原有定义 | 67 | 80 项字段级变更，详见日志 |
| 新增能力候选 | 20 | 独立 `DEVX-001~020`；无一条从冻结 Holdout 复制 |
| 当前候选定义合计 | 142 | 122+20；**不是全数 READY** |

### 改动类型（按字段变更次数）

- 45 次 `pending_generation.scope_names` 第一章范围补充。
- 12 次 Pending 初始 Fixture 从 `6题60分` 调整到 `10题100分`，使其与原指令中“仍然/不变/降到”及原 Oracle 一致；总共原先报告的 24 次来自重复用例。
- 3 条不同的“读当前卷指定题”修复了误写的 `needs_clarification` 及 `read_paper` 禁令；第 4 条原问题在重复归档中。
- 1 条不同的 Pending 确认用例增加 Paper 题量 6 和总分 60 的结果验证。
- 4 条读取相关用例增加或增强 `response_facts` 事实字段（对应字段变更详见 `change_log.jsonl`）；仍需检验正则覆盖率。

### 仍未修复的部分

当前 `dev_122_revised_candidate.yaml` 中，83 条没有 `open_issues`，39 条仍有一项或多项标注。
**83 条没有已知未决问题 ≠ 83 条正式审核通过。** 它们尚未在真实数据库及模型 Runner 下执行。

主要未决：

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
- `new_capability_20_candidate.yaml`：20 条新能力场景候选，包含依赖和实施条件；与既有 Dev 编号不重合。
- `change_log.jsonl`：原用例每个业务字段更改的前后值、原因和证据等级。
- `remaining_review_items.json`：未解决旧用例的汇总。
- `offline_validation.json`：本地 YAML roundtrip、CaseLoader、去重、保留原 turns 等基本离线验证结果。
- `summary.json`：统计摘要。
- `MANIFEST.sha256`：文件内容 SHA-256。

## 新增20条的审查分层

新的 DEVX 用例主要覆盖版本恢复、连续确认幂等、目标指代、多轮只读、防写入、题型别名、跨 Pending 隔离、故障注入、知识点溯源、学生错题巩固、跨用户会话隔离等。

有 **6 条**标记 `NEEDS_VALIDATION`，可优先在用户完整仓库中验证 Fixture + Grader 的适配性；其他 **14 条**已明确记录 `NEEDS_FIXTURE`、`NEEDS_GRADER_EXTENSION`、`NEEDS_SEMANTIC_REVIEW` 等前置阻塞。所有新增 20 条仍为 `review.status=pending`，不能直接纳入正式通过率。

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
