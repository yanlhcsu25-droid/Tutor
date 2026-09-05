# Tutor 项目修缮报告

## 范围

本轮按“先恢复可合并性，再处理非阻塞优化”的原则修复已确认的运行时错误、CI 静态检查、开发环境可迁移性和缺失依赖，并补充端点回归测试。未进行前端框架扩展、模块大拆分或 Redis/Celery/Kubernetes 等非必要工程化。

## 已完成

### 1. 修复 API 运行时错误

- `src/calculus_agent/api.py`：补充 `uuid` 导入，修复 deprecated `POST /api/v1/agents/runs` 创建 legacy conversation ID 时的 `NameError`。
- `src/calculus_agent/api.py`：补充 `Paper` 导入，修复旧版 Blueprint 会话执行“恢复到版本 N”时的 `NameError`。
- 将 OCR 端点使用的 `File`、`UploadFile` 移到模块顶部，消除中段导入。
- 清理该模块确认未使用的导入和局部变量。

### 2. 增加端点回归测试

- `tests/test_teacher_agent_api.py`：新增 legacy Agent Run 回归，验证接口能够生成 `legacy-api-<uuid>` 会话并读取持久化 Run Trace。
- `tests/test_requirement_provider_routing.py`：新增指定 Paper 历史版本恢复回归，覆盖 `Paper` 查询和版本恢复路径。

### 3. 恢复 Ruff/CI 静态检查

- 修复或清理未使用导入、未使用变量、模块中段导入和兼容性导出等问题。
- `paper_change_service.py` 保留原排序 lambda 语义，并对 Ruff E731 做局部、显式豁免，避免为了格式规则重写稳定业务逻辑。
- 旧版紧凑测试 `test_phase2b3a_replacement.py` 对 E702/F841 做文件级局部豁免，避免产生数百行纯格式差异。
- 恢复 `papers.selector.EXCLUDED_PAPER_SOURCE_NAMES` 的兼容性 re-export，避免自动清理未使用导入后破坏外部模块导入契约。

### 4. 修复开发环境与依赖声明

- 删除并通过 `uv sync --locked --all-groups` 重建 `.venv`，修复项目移动后 pytest shebang 仍指向旧目录的问题。
- `pyproject.toml`/`uv.lock` 新增运行时依赖 `latex2mathml`，因为 Workbench Markdown 渲染模块直接导入它。
- 新增开发依赖 `psutil`，因为 `tests/minerU_test.py` 在测试收集阶段直接导入它。
- 该问题此前被旧虚拟环境中的非声明依赖掩盖，重建环境后才会稳定复现。

## 验证结果

- `uv run ruff check src tests`：通过。
- `uv run pytest -q`：通过；pytest 缓存记录 1043 个已收集测试节点，环境相关用例按既有条件跳过，无失败。
- API 与 Paper 相关定向回归：通过。
- `tests/evals/test_failure_injection.py`、`test_acceptance_grader.py`、`test_fixture_repair_a.py`：6 个测试通过。
- `web/npm run build`：通过。
- `git diff --check`：通过。

## 尚未处理的非阻塞项

1. 前端主 Bundle 仍约 1.21 MB，Vite 提示超过 500 KB；在没有首屏性能目标前不作为发布阻塞项，后续可按 Admin、OCR、题库和教材模块使用 `React.lazy` 拆包。
2. 前端尚无 Vitest/React Testing Library；建议未来优先覆盖 GenerationPlanCard 的 Pending 恢复、修改后重新校验和确认按钮状态。
3. `api.py`、`runtime/coordinator.py` 等模块仍较大；不建议仅按行数整体重构，应在新增功能时沿已存在领域边界渐进迁移。
4. `tests/evals/reports/latest.json` 已被 Git 跟踪，但 `.gitignore` 又忽略同类 JSON。本轮未直接修改 Git index；建议后续单独提交一次报告治理变更：原始报告转 CI artifact，只保留精简、可复现的 Markdown/JSON 摘要。
5. 本轮只执行确定性 failure-injection 测试，没有调用真实付费 LLM；Live Eval 应在模型凭据和预算明确后单独运行并保存版本元数据。

## 当前结论

本轮已清除已知的 API `NameError`、Ruff CI 红灯、失效虚拟环境和缺失依赖四类合并阻塞问题。后端全量测试、静态检查和前端构建均通过，当前代码已恢复到可继续评审的状态；后续优先级应转向独立盲测集和面试 Demo，而不是继续扩大功能范围。
