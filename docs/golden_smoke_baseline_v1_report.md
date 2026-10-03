# Golden Smoke Baseline v1 运行与改进报告

## 1. 问题背景

此前评测容易停留在状态字符串和 Tool 名称层面，无法证明 Tutor Agent 是否真正保留教师约束、正确处理 preview/confirm、避免非法副作用，并让回复与持久化对象一致。Golden Smoke v1 因此选择 20 条 Development cases，覆盖 generation、pending、paper read/modify、teaching design、consultation、clarification 和 multi-turn。

本轮目标不是第一次获得高通过率，而是建立一份可复现、可追责、能暴露真实业务缺陷的正式 baseline。

## 2. 方案决策

- 使用人工审核并冻结的 20 条 Golden Smoke，不接触 Holdout。
- 锁定 `siliconflow / Qwen/Qwen3.5-35B-A3B`、temperature 0、Dataset SHA、代码 SHA、fixture 与 grader 版本。
- 保留 raw report、console log 和 provider provenance sidecar；不回写或美化原始结果。
- 自动 grader pass 只记为自动验收结果，不直接等同于人工确认的 Task Success。
- 失败按 failure class 修复，不针对单条自然语言 case 打补丁。

## 3. 个人动作与系统动作

### 人工完成

- 审核 20 条业务场景、expected、required tools 与副作用契约。
- 指定先冻结 baseline、保留原始报告，再按 failure taxonomy 推进修复。
- 明确优先目标：消除 false-success、非法副作用、read grounding 错误、约束漂移和 provider provenance 错误。

### Agent/代码完成

- 冻结 Golden 数据集及 Manifest。
- 修复 Tool trace 与最终报告的 JSON 序列化，增加逐 case 报告检查点。
- 执行真实模型 Smoke，保存完整 raw report、console log 和 provenance sidecar。
- 生成机器可读 baseline 索引：`evaluations/golden_smoke_baseline_v1.json`。

## 4. 交付状态

**阶段：内部技术验证 / benchmark baseline。**

本轮不是生产验收，也不能证明模型整体只有 30% 能力。它证明当前 Golden contract 已经能够识别约束丢失、状态漂移、非法副作用、回复与数据库不一致等业务问题。

本轮报告完整落盘，因此可以作为 Baseline v1。首次因序列化失败的运行仍作为 incident 保留，但不纳入正式基准比较。

## 5. 落地范围与运行配置

| 项目 | 值 |
|---|---|
| Cases | 20 Development Golden Smoke |
| Variant | `state-policy` |
| Model | `Qwen/Qwen3.5-35B-A3B` |
| 实际 Endpoint | SiliconFlow OpenAI-compatible API |
| Temperature | 0 |
| Git SHA | `aebba583bb2fd9a0a285409a1ec8e86ed78907ad` |
| Manifest Dataset SHA | `f0545f54b4c9703dc42533b38faf1fe04c3c3a03ead45569ef4228413d1d1c34` |
| YAML file SHA | `b093f1748e66c8c4381a444777a0e34bfad7f4cf8e80a3a06386edf6112f03af` |
| Raw report SHA256 | `ee34c3f33420360a1a0aa934740e1aad798ce82bd6afdcfdf72a37ceb09e3bbe` |
| Console log SHA256 | `3f2396481190469b519ad1d2bb79c5b9d0184bd79fdd10a8eab486856ed00a83` |

原始材料：

- `tests/evals/reports/golden-smoke-v1-rerun-20261003T124347Z.json`
- `tests/evals/reports/golden-smoke-v1-rerun-20261003T124347Z.log`
- `tests/evals/reports/golden-smoke-v1-rerun-20261003T124347Z.provenance.json`

## 6. 效果证据

### 6.1 原始结果

| 指标 | 结果 | 证据性质 |
|---|---:|---|
| Automated grader pass | 6/20（30%） | 实测自动验收结果 |
| Failed | 14/20 | 实测自动验收结果 |
| Model calls | 64/100 | 实测，未超过调用预算 |
| Recorded total tokens | 290,125 | 实测，覆盖 19/20 cases |
| Token coverage | 95% | 1 个超时 case 无完整 usage |
| Provider timeout | 1 次，120 秒 | 实测；`SMOKE-TEACH-02` |
| Illegal side effect | 1 条 | `SMOKE-CLARIFY-01` |
| Raw report | 4.2 MB，完整可解析 | 实测 |

自动通过 cases：

- `SMOKE-PENDING-02`
- `SMOKE-PENDING-04`
- `SMOKE-READ-01`
- `SMOKE-MODIFY-01`
- `SMOKE-MODIFY-02`
- `SMOKE-MODIFY-03`

### 6.2 分类结果

| 类别 | 自动通过 |
|---|---:|
| Generation | 0/4 |
| Pending | 2/4 |
| Paper（Read + Modify） | 4/6 |
| Teaching | 0/3 |
| Ambiguity | 0/1 |
| Multi-turn | 0/2 |

### 6.3 需要人工复核的 grader 信号

自动结果中至少有几类不能直接解释为 Agent 业务失败：

- `GEN-01`、`GEN-04`、`MULTI-01` 因回复使用“已生成方案”被 false-success heuristic 标记，但回复明确描述的是待确认方案，不是持久化 Paper。应修 grader 语义边界。
- `GEN-01` 是初次创建方案，却被 constraint grader 报告“baseline generation state unavailable”。初次创建不应要求既有 baseline。
- `READ-02/03` 调用了确定性的 `analyze_paper` 并返回正确数据库事实；当前 Acceptance 强制要求 `read_paper`，而 `response_facts` 又无法识别“选择题 4 题”格式。需要先统一 Benchmark 的 authoritative-read contract。

因此 30% 是原始自动结果，不应被解读为人工确认后的整体 Task Success。

## 7. 责任边界

- 本轮只验证冻结的 Development Smoke，不代表 Holdout、生产流量或其他模型表现。
- 290,125 是 19/20 cases 的已记录 token 总量；超时请求缺少 usage，不能据此证明完整 token 总量一定低于 500,000。
- `SMOKE-TEACH-02` 包含 Provider timeout，不能仅归因为模型推理错误。
- Raw report 中 provider 为 `bailian`，原因是 SiliconFlow 复用了 `BailianChatBackend` 的静态标签；实际 endpoint 已由 sidecar 记录。原始报告不修改。
- 自动 grader 中存在已识别的 false-positive/contract mismatch，修复前不得用 30% 做项目质量结论。

## 8. 待补证据

1. `raw model args → request guard → normalized request → pending generation` 的逐阶段结构化 diff，目前报告只稳定保留了 Tool 边界后的参数。
2. Read 类任务对 `read_paper` 与 `analyze_paper` 的正式允许矩阵。
3. false-success classifier 对“生成方案”和“生成正式试卷”的区分测试。
4. TeachingDesign invalid arguments 的纠错成功率，以及超时 case 的非重试复验。
5. 修复后的 targeted regression 结果；在确定性问题清零前不应重跑全部 20 条。
6. Provider、adapter、protocol 三个维度的独立运行元数据。

## 9. 后续评审问题

- 哪些字段是教师显式锁定约束，哪些字段允许模板补全？
- 初次 preview、pending patch、confirm 三个阶段分别应比较哪一份基线？
- `analyze_paper` 是否可作为题数、总分和题型分布查询的 authoritative read？
- TeachingDesign 的 assessment plan 是必填业务内容，还是可省略的模型扩展内容？
- Provider timeout 是否单独标记为 infrastructure failure，而不进入模型 reasoning failure？
- 修复 targeted regressions 后，满足什么门槛才允许第二次完整 Golden Smoke？

# 改进计划

## P0：先消除不可接受的可靠性风险

### A. 模糊题号必须 fail closed

目标行为：

```text
“删掉第二题”
→ needs_clarification
→ no preview
→ no mutation
```

在调用 `preview_paper_changes` 前增加确定性 addressing gate：只有“全卷第 N 题”或“某题型第 N 题”等有命名空间的地址才能进入预览。将 `SMOKE-CLARIFY-01` 固化为 P0 regression。

### B. 最终试卷必须同时满足两层一致性

1. `sum(PaperItem.score) == Paper.total_score`
2. 持久化 Paper 必须满足 Pending 中所有 `teacher_explicit` 锁定约束

第二条是本轮 `MULTI-02` 暴露的核心：100 分试卷内部可以自洽，但仍违反教师明确要求的 60 分。

### C. Read grounding fail closed

- DB Tool 失败或未获得权威 observation 时，不允许 `completed`。
- 对题数、总分、题型分布直接比较 Tool observation/DB snapshot，不再依赖自然语言 regex 作为唯一 oracle。
- 明确 `read_paper` 与 `analyze_paper` 的允许范围；若两者都权威，Acceptance 使用 `must_include_any`，不强绑内部实现名称。

## P1：按 failure class 修业务链路

### 1. Generation constraint preservation

为同一次 prepare 保存四阶段 diff：

```text
raw_model_args
→ guarded_args
→ normalized_request
→ persisted_pending
```

每个显式约束记录 provenance、是否变化及变化原因。修复应落在统一 constraint contract，不针对 `8题80分` 等单句增加专用 regex。

Target regressions：`GEN-02`、`GEN-03`、`MULTI-01`、`MULTI-02`。

### 2. Pending Patch inheritance

建立字段级 patch 规则：

- 用户明确修改的字段才覆盖。
- 未提字段继承原 Pending。
- `scope_names`、`question_count`、`total_score` 和题型约束不得漂移。
- pause、cancel、confirm 分别进入不同确定性分支。

Target regressions：`PENDING-01`、`PENDING-03`、`MULTI-01`。

### 3. TeachingDesign

按以下顺序定位：

1. Tool 是否暴露并调用。
2. `lesson_count` 是否进入 Tool args 和 persisted content。
3. Schema validation 是否因非用户要求的 assessment 明细阻塞整个设计。
4. Tool 成功后 active design 与 final status 是否一致。
5. 回复是否与 persisted content 一致。

`TEACH-01` 的直接失败原因是 assessment 分项分数与 total score 不一致，模型重试后仍提交相同非法参数。保持 schema 校验，但应让恢复提示明确指出可重新计算或省略非必需 `score_each`。`TEACH-02` 先作为 timeout/infrastructure regression 单独处理。

### 4. Consultation routing

“暂时不要建教学设计，只讲教学重点”应直接进入 consultation，不应因缺少教材 scope 被强制澄清。增加 negation + consultation 的 deterministic routing regression。

## P1：修 grader，而不是迁就 grader

- false-success：区分“生成方案/预览”和“正式试卷已生成”。
- constraint grader：初次 preview 不要求 existing generation baseline。
- response facts：优先比较 structured Tool observation；如保留文本解析，支持“4题 / 4 题 / 4道题”。
- failure taxonomy：Provider timeout 归入 infrastructure/provider，不标记为 model reasoning。

这些修改必须有 grader regression，且不得反向降低业务断言强度。

## P2：修运行 provenance

将运行身份拆成：

```json
{
  "provider": "siliconflow",
  "backend_adapter": "BailianChatBackend",
  "protocol": "openai_compatible"
}
```

不要再由 adapter 类名推导 provider。原始 baseline 保持 `reported_provider=bailian`，通过 sidecar 解释，不做历史回写。

## 推荐执行顺序

1. P0 addressing side-effect gate。
2. P0 explicit constraint + final artifact validator。
3. Read authoritative contract 与 grader 修正。
4. Pending patch inheritance。
5. TeachingDesign validation/recovery。
6. Consultation routing。
7. Provider provenance 与 failure taxonomy。
8. 每一类只运行对应 regression cases。
9. 确定性 regression 全通过后，再申请完整重跑 20 条 Golden Smoke。

下一轮首要门槛不是总体通过率，而是：

- 0 个确认后的非法副作用；
- 0 个已确认的 false-success；
- Read grounding 全部正确；
- 教师显式约束不漂移；
- 回复与持久化 artifact 一致；
- Provider provenance 可核验。

## 升维迁移

- 原始事实：首次完整 Golden Smoke 自动通过 6/20，并暴露约束漂移、非法副作用和 grader false-positive。
- 高密度表达：通过冻结的 20-case 业务基准和 DB/副作用 oracle，将评测从 Tool-name 检查推进到 preview-confirm、约束继承、持久化一致性与 fail-closed 验证，并以 30% baseline 定位六类系统性缺陷。
- 升维动作：不追逐单次通过率，改用可复现证据链、failure taxonomy 和 targeted regression 驱动可靠性收敛。
