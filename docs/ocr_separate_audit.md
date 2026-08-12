# OCR separate 模式改造 — 只读审计报告（第十九节）

> 本文件仅记录审计结果，未改动任何代码。实施需在你确认后进行。

## 1. OCR candidate 当前使用什么 ID

- 内存态：`QuestionCandidate`（ocr.py 的 dataclass）无持久 id，靠 `(section_key, original_number)` 标识。
- 落库态：`OcrImportDraft.id`（36 位 uuid 字符串），API 中暴露为 `question_id`
  （`database._draft_dict` 第 485 行：`"question_id": draft.id`）。
- 校验层 `QuestionPayload.question_id` 形如 `q_<hash>` / uuid，是 payload 级临时 id。

**结论**：OCR 候选的正式 ID = `OcrImportDraft.id`（API 叫 `question_id`）。

## 2. ocr_import_draft 主键

- `OcrImportDraft.id`（models.py:362）：`String(36)` 主键，`default=new_id`。
- 另含 `formal_question_id`（models.py:375）：`String(36), nullable, index`，指向真实 `Question.id`。

## 3. 正式 Question.question_id 创建时机

- 唯一入口：`publish_ocr_draft`（import_service.py:144）`Question(...)`，仅此处创建。
- 未发布时 `formal_question_id` 恒为 `None`；发布后回填（import_service.py:160）。
- revision 再发布（import_service.py:142）：`session.get(Question, draft.formal_question_id)`，
  存在则 update 原行，**不新建** → 复用正式 question_id。

## 4. 是否存在"未发布就创建正式 Question"

**否**。`formal_question_id` 仅在 publish 写入；未发布 draft 永远为 `None`。
确认 `publish_ocr_draft` 是正式 `Question` 的唯一创建入口（OCR 流程内）。

## 5. separate matcher 当前需要改的地方

`match_questions_and_solutions`（import_pipeline.py:174-234）：

- **删除 `sequence_fallback` 位置强行配对**（207-219 行）：
  当前 `题目 1,3 + 答案 1,2` 会 zip 成 `3→2`，违反第五节"题号不同禁止顺序配"。
- 保留 `unmatched` 分支（`needs_review / matched=False / match_method="unmatched"`）。
- 缺答案题当前仍被 append 进 `output`（作为普通 candidate），需明确分类。

`ImportDiagnostics`（import_pipeline.py:80-84）目前只含 `unmatched_solutions` + `ambiguous_keys`，
**缺"缺答案题"分类字段**。

## 6. sequence_fallback 造成错配的场景

条件：未匹配两侧数量相等 + 各自 key 全唯一 → 按位置 zip：

- `题目 1,3 + 答案 1,2` → `3→2`（禁）
- `题目 1,2 + 答案 2,3` → `1→2, 2→3`（题号全错位，禁）
- 任何"题号集合 ≠ 答案集合，但数量恰巧相等"的组合都会乱配。

本质：题号错位时不应依赖位置。

## 7. 待审核接口是否返回缺答案题

**是**。`list_questions`（database.py:242）返回 source 下**全部** draft，无过滤；
`app.py:338` 列表接口直接 `db.list_questions` 全返回。

且 `OcrImportDraft`（models.py:358-383）**没有** `matched` / `needs_review` 列，
匹配状态落库时只把 `review_note` 写进 Markdown 的 `## 审核备注` 文本，无可查询结构化字段。
→ 要可靠过滤，必须新增结构化列（见第 10 节）。

## 8. diagnostics 当前能否保存 缺答案 / 多余答案 / 无法确定

- 多余答案：`ImportDiagnostics.unmatched_solutions` ✅（仅在内存/API 返回，不落库；
  重新导入会再生，不丢）。
- 无法确定：`ImportDiagnostics.ambiguous_keys` ✅。
- 缺答案：❌ 当前无独立分类，仅 `match_method="unmatched"` + `review_note="answer_not_found..."`
  写进 markdown，无结构化字段。

**结论**：需为"缺答案"增加结构化表达（推荐加列或 diagnostics 字段）。

## 9. resplit 重新匹配真实调用链

`resplit.build_plan`
→ 用 `edited_markdown` 重算页文本
→ `DocumentLayout.from_dict`
→ separate 模式取 `question_pages + solution_pages` 全页
→ `import_document(pipeline_pages, layout)`（import_pipeline.py:258）
→ 重新 `extract_questions` + `extract_solutions` + `match_questions_and_solutions`（按最新题号重配）
→ `render_drafts` → `_build_draft_diff`（按 page+number+markdown 比对 keep/remove/add）
→ `apply_plan` 删 removed、建 added。

**关键点**：重新切题必然重跑 matcher，旧人工配对**不保留**——这正是第十一/十二节要求的行为
（按最新题号重配），前提是 matcher 不再做 `sequence_fallback` 错配。

## 10. 推荐最小修改文件列表

| 文件 | 改动 |
| --- | --- |
| `workbench/import_pipeline.py` | 删除 `sequence_fallback`；`ImportDiagnostics` 增 `missing_questions`；matcher 分类填充 |
| `models.py` | `OcrImportDraft` 增轻量列 `match_status`（matched/missing_answer/ambiguous）+ `match_method`/`review_note` 存储列（**新增列，非改名**） |
| `workbench/ocr.py` | `persist_rendered_draft` / `render_drafts` 把 `matched`/`match_method`/`review_note` 落到草稿 |
| `workbench/database.py` | `_draft_dict` 输出 `match_status`；`list_questions` 支持按 `match_status` 过滤；`add_question` 接收 match 字段 |
| `workbench/app.py` | separate 列表接口把 `matched` 进"待审核"，其余进"匹配异常"；import 完成返回配对摘要 |
| `workbench/resplit.py` | `plan_to_dict` 补 `missing_questions`（来自新 diagnostics 字段） |
| `tests/test_ocr_separate_resplit.py` | 修复 known fail + 新增"改题号重配"场景 |
| `tests/test_ocr_separate_matcher.py`（新增） | 覆盖第二十节 14 个场景 |

**不动**：inline 分支、`ocr.py` 切题正则、PaddleOCR、RAG/LLM、`QuestionProfile`、正式题库 UI、`publish_ocr_draft` 主体。

## schema 兼容性提醒

表结构由 `Base.metadata.create_all(engine)`（db.py:29）初始化，**无 Alembic 迁移**。
新增列不会自动应用到已有 `calculus_agent.db`，需对开发库执行一次幂等 `ALTER TABLE ocr_import_draft ADD COLUMN ...`。
我会用幂等 ALTER（且只加列、不改名、不删列）保证现有库可用，旧草稿 `match_status` 默认按 `review_note` 推导或置 `matched` 兜底。
