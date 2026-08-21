# Tutor

> 面向教师的可验证教学与组卷 Agent。

Tutor 将教师的自然语言需求转为可追溯的教学设计、组卷方案与试卷修改操作。它不把 LLM 回复当作业务事实：所有创建、修改、确认和取消都必须由确定性 Tool 与数据库状态支持。

## 为什么是 Tutor

教育 Agent 的难点不是“生成一段建议”，而是让系统在真实工作流中可靠地推进状态：

```text
教师需求
  → Scope / 题库调查
  → Preview（可审阅方案）
  → Teacher Confirm
  → Domain 写入
  → Workspace / Trace 持久化
```

Tutor 的核心约束：

- **Tool / Domain state 是事实源**：模型不能编造题目、知识点、分数、ID 或已完成状态。
- **Preview / Confirm 生命周期**：组卷与试卷修改在教师确认前不会写入最终业务状态。
- **完成边界**：没有成功 Tool Observation 和持久化 Domain 对象，不能回复“已创建”或“已完成”。
- **可恢复状态**：Conversation Workspace、Pending state、Paper 版本与 TeachingDesign 都可从数据库恢复。
- **可观测与可回归**：每轮持久化 Run / Span Trace，并通过 Live Teacher Acceptance Eval 验证真实工作流。

## 已实现能力

### TeachingDesign

- 根据教师目标检索教材范围与题库供给；
- 创建、修改、确认、放弃可版本化 TeachingDesign；
- TeachingDesign 创建必须建立真实证据引用，不能仅由模型常识生成；
- 已持久化设计才能进入确认状态。

### Generation

- 自然语言生成试卷 Preview；
- 明确教师确认后才调用 `confirm_generation` 创建试卷；
- 按章节、题型、题量、总分和难度约束生成；
- 题库不足时返回结构化诊断与可执行调整建议；
- 成功生成后记录 ConversationGenerationHistory 与已选题目。

### Paper Operations

- 读取当前试卷、题型内题号定位；
- 换题、删题、改分等操作先生成 `preview_paper_changes`；
- 确认后才创建新 Paper 版本；
- 支持 Pending 取消、版本链与 Paper state grounding。

### Reliability & Observability

- 本地持久化 `TeacherAgentRunTrace` / `TeacherAgentSpan`；
- Trace payload 在存储边界 JSON-safe，Trace 失败不应成为业务事实；
- Tool Observation Projection 减少模型上下文中的运行时与审计字段；
- Context Stability Eval 观测上下文体积、工具轮次和延迟；
- Teacher Acceptance Eval 覆盖 TeachingDesign、Generation、Paper 修改、Pending Cancel、题库不足和 Tool failure。

## Architecture

```text
Teacher UI / API
       │
       ▼
Teacher Agent Runtime
       │
       ├── Task Router / Tool Surface
       ├── Tool Loop
       ├── Preview / Confirm Guards
       └── Run-Level Trace
       │
       ▼
Domain Tools
       ├── TeachingDesign
       ├── Generation
       ├── Paper Change
       ├── Curriculum / Question Bank Inspection
       └── Pending State
       │
       ▼
SQLite + SQLAlchemy
       ├── Paper / PaperItem version chain
       ├── TeachingDesign versions
       ├── Conversation Workspace / Working Memory
       ├── Pending actions
       └── Run / Span traces
```

## Quick Start

### Requirements

- Python 3.11–3.14
- [uv](https://docs.astral.sh/uv/)
- Node.js 18+ and pnpm（运行 Web UI 时需要）

```bash
git clone https://github.com/yanlhcsu25-droid/Tutor.git
cd Tutor
cp .env.example .env
./scripts/quickstart.sh
```

启动后：

- Web UI: `http://127.0.0.1:5173`
- API docs: `http://127.0.0.1:8000/docs`

后续启动：

```bash
./scripts/start.sh
```

> Python package 目前仍为 `calculus_agent`，以避免破坏已有数据库迁移与导入路径；产品名称为 **Tutor**。

## Model Configuration

基础 Paper 数据、版本操作和多数确定性校验不依赖 LLM。运行 Teacher Agent / Live Eval 时，在本地 `.env` 配置模型：

```env
SILICONFLOW_API_KEY=your_api_key
SILICONFLOW_BASE_URL=https://api.siliconflow.cn/v1
SILICONFLOW_AGENT_MODEL=zai-org/GLM-4.5V
SILICONFLOW_VL_MODEL=zai-org/GLM-4.5V
```

不要提交 `.env`、本地数据库、上传文件或 Trace 报告。

## Testing

### Unit and integration tests

```bash
uv run pytest -q
uv run ruff check src tests
```

### Teacher Acceptance Evaluation

该评测使用隔离数据库与确定性题库 Fixture，并调用真实配置的 LLM：

```bash
RUN_LIVE_LLM=1 uv run pytest -q tests/evals/test_teacher_acceptance.py -s
```

当前覆盖：

| Area | Scenarios |
| --- | --- |
| TeachingDesign | 自动 Scope 映射、范围调查、创建与待确认状态 |
| Generation | Preview、Confirm、成功生成、题库不足诊断 |
| Paper modification | Read → Preview → Confirm 边界、删除预览 |
| Pending | 取消待生成计划 |
| Reliability | Tool failure 不伪造成功、Trace 记录 |

评测报告写入 `tests/evals/reports/teacher_acceptance_v0.json`（本地文件，不提交）。

## Project Structure

```text
src/calculus_agent/
  runtime/              # Agent loop, lifecycle boundaries, context policy
  agent/                # Tool registry, state, tracing, adapters
  teaching_design/      # Versioned TeachingDesign domain
  papers/               # Paper persistence, selection, rendering, export
  application/          # Scope, environment and workflow application services
  questions/            # Question profile / review / eligibility

tests/
  teacher_agent/        # Deterministic runtime and lifecycle regressions
  evals/                # YAML-driven live acceptance evaluations
  curriculum_context/   # Curriculum context and migration checks
```

## Engineering Notes

Tutor is intentionally designed as a stateful business Agent rather than a prompt-only chatbot. Key implementation lessons include:

- LLM Observation 可序列化，不代表 Trace JSON Column 可序列化；Trace 持久化边界必须单独规范化。
- SQLAlchemy JSON flush 失败会污染 Session；可观测性不能破坏业务事务。
- “模型声称已完成”不是业务完成；完成状态必须由 Tool Observation 和 Domain persistence 支撑。
- Agent 评测应以 **Outcome > Workflow > Tool Path** 为准，内部 Tool 顺序只作为诊断证据。

## License

This repository is currently intended for portfolio, research, and development use. Add a license before public production distribution.
