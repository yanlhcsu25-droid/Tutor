# OCR separate 模式改造 — 实施验收报告（第二十一节）

> 配套审计：docs/ocr_separate_audit.md
> 仅改动 `solution_mode = "separate"` 流程；inline 模式完全不变。

## 1. OCR 候选 ID 与正式题目 ID 的最终关系

- OCR 候选 ID = `OcrImportDraft.id`（API 暴露为 `question_id`），是**临时候选/审核草稿**主键。
- 正式题目 ID = `Question.id`，仅在**发布**时由 `publish_ocr_draft` 创建，并回填到 `OcrImportDraft.formal_question_id`。
- 二者通过 `formal_question_id` 关联：**OCR 候选 ID ≠ 正式题目 ID**，业务语义明确分离。
- 本次未重命名任何 ID 字段（遵守"不大规模重构数据库"约束）。

## 2. 正式 question_id 在哪一步创建

唯一入口：`publish_ocr_draft`（import_service.py:144）`Question(...)`。
未发布草稿 `formal_question_id` 恒为 `None`；revision 再发布走 `session.get(Question, draft.formal_question_id)` 复用原行，不新建。

## 3. sequence_fallback 做了什么修改

**已彻底删除**（`import_pipeline.py` 原 207-219 行位置强行配对块）。
原逻辑在"未匹配两侧数量相等且各自 key 唯一"时按位置 `zip` 配对，导致
`题目1,3 + 答案1,2 → 3→2` 等错配。
现改为：未匹配题一律分类为 `missing_answer`（无对应答案）或 `ambiguous`（题号歧义），
全部 `needs_review=True`、**仍进入 OCR 待审核列表（带 ⚠ 提示）**，但**禁止发布**；绝不按位置配对。

## 4. "缺少答案"的题现在存在哪里

- 仍作为 `OcrImportDraft` 落库（与正常候选同表），`match_status = "missing_answer"`，
  `review_note = "answer_not_found（未找到与该题号对应的参考解答）"` 写进 `## 审核备注`。
- 不删除、不丢弃，保留在 OCR 待审核区供人工补全或补答案页后重配。

## 5. 为什么"缺答案/歧义"题仍然显示在 OCR 待审核里，却不会进正式题库

- **待审核列表返回全部候选**：`list_questions`（database.py:248）对 source 下所有 `OcrImportDraft`
  一并返回，不按 `match_status` 过滤。所以 `missing_answer` / `ambiguous` 草稿**就在主待审核列表里**，
  只是附带 `⚠ 缺少答案` / `⚠ 答案对应不确定` 徽标（前端按 `match_status` 渲染，数据已透出）。
- **正式题库完全不知道这些状态**：`match_status` 仅用于 OCR 审核界面提示，**不进 `publish_ocr_draft`、**
  不写 `Question`、不出现在正式题库任何接口。只有审核通过且 `match_status == "matched"` 的题发布后，
  才会产生正式 `Question`，正式题库里只有"正常正式题"。
- **禁止发布**：发布接口（`app.py:708`）拦截 `match_status in (missing_answer, ambiguous, unknown)`
  → 直接返回失败原因。用户点开缺答案题可"补参考解答 → 保存 → 状态恢复 matched → 继续知识点/难度 → 发布"。
- 因此流程是：OCR 审核阶段（正常 / 缺答案 / 对应不确定）→ 只有完整且审核通过 → 正式题库（只有正常正式题）。

## 6. 多余答案怎么保存

- 多余答案（无对应题目的答案块）进入 `ImportDiagnostics.unmatched_solutions`，
  经 `import` / `resplit` 接口返回给前端（section/题号/页码/答案正文）。
- 不落独立表，但**每次重新导入/重新切题会再生**，符合"底层仍保留、可补页后重配"的语义。

## 7. 无法确定配对怎么处理

- 同题号出现两份答案 / 两份题目 → key 进 `ImportDiagnostics.ambiguous_keys`，
  对应候选 `match_status = "ambiguous"`，`review_note = "题号重复或参考解答归属存在歧义，未自动匹配，请人工核对。"`，不进入正常待审核。

## 8. resplit 修改题号后如何重新匹配

链路不变：`build_plan` → 用 `edited_markdown` 重算 → `import_document` →
重新 `extract_questions` + `extract_solutions` + `match_questions_and_solutions`（按最新题号配）
→ `render_drafts` → `_build_draft_diff`（keep/remove/add）→ `apply_plan`。
旧人工配对**不保留**，按最新内容重配；因已删除 `sequence_fallback`，不会继承错误旧答案。
例：题目2 改 3 → 重新切后 `1→1`、`3→缺少答案`、答案2→`unmatched_solutions`。

## 9. 修改了哪些文件

| 文件 | 改动 |
| --- | --- |
| `workbench/import_pipeline.py` | 删除 `sequence_fallback`；`ImportDiagnostics` 增 `missing_questions`；matcher 分类 `matched/missing_answer/ambiguous` |
| `workbench/ocr.py` | `QuestionCandidate`/`RenderedDraft` 增 `match_status/match_method/review_note`；贯穿 `render_drafts`/`persist_rendered_draft` |
| `workbench/database.py` | `add_question` 接收并写入匹配字段；`_draft_dict` 透出 `match_status/match_method/review_note` |
| `models.py` | `OcrImportDraft` 增三列（`match_status`/`match_method`/`review_note`），仅 OCR 候选/待审核语义 |
| `db.py` | `create_schema` 幂等 ALTER 加三列；**历史回填**：`separate` 来源草稿 → `unknown`，inline/legacy → `matched`（禁止把未知历史 separate 草稿认定为已配对） |
| `workbench/app.py` | 发布拦截扩展为 `missing_answer/ambiguous/unknown`；import 端点补 `missing_questions` + `summary` |
| `workbench/resplit.py` | `plan_to_dict` 诊断补 `missing_questions` |
| `tests/test_ocr_separate_matcher.py`（新增） | 覆盖第二十节 14 场景 |
| `tests/test_ocr_separate_resplit.py` | 已知失败 `test_question_number_change_reruns_matcher_and_keeps_diagnostics` 已转绿 |

## 10. 新增了哪些测试

`tests/test_ocr_separate_matcher.py`：
1. 三题全配对
2. 1,3+1,2 → 1 配对/3 缺答案/答案2 多余，禁止 3→2
3. 1,2,3+1,3 → 2 缺答案，答案3 不配题2
4. 子题 3(1)(2)(3) + 答案 3(1)(3) → 3(2) 缺答案
5. 同题号两份答案 → ambiguous
6. 分类正确性（missing/extra/matched 集合）
7. 未发布无正式 id
8. 发布创建正式 id
9. revision 复用原 id
10. resplit 改题号重配
11. inline 行为不变

## 11. 当前 separate resplit 原失败是否已修复

**已修复**：`test_question_number_change_reruns_matcher_and_keeps_diagnostics`
此前因 `sequence_fallback` 把答案2 错配给题3（markdown 不含"未找到"）而失败；
删除 fallback 后题3 正确判为缺答案，断言通过。

## 12. 普通 OCR 是否完全没有被影响

**完全未受影响**：
- `import_document` 的 `inline` 分支直接返回 `split_pages_into_candidates(...)`，不经过 matcher；
- 所有新增字段均有默认值（`match_status="matched"`/`match_method="inline"`），inline 候选天然 `matched`；
- 未改任何切题正则、PaddleOCR、RAG/LLM、QuestionProfile、正式题库 UI、publish 主体。
- 完整 pytest 全绿，无新增失败。

## 范围说明（前端）

后端已完整提供：结构化 `match_status`、API `import_diagnostics.summary` / `missing_questions`、发布拦截。
**缺答案 / 歧义题就在主待审核列表内**，前端按 `match_status` 渲染 `⚠ 缺少答案` / `⚠ 答案对应不确定` 徽标即可，
无需把它们挪到独立折叠区。"匹配异常"折叠区可仅用于"多余答案（无对应题目）"这一类（数据经 `unmatched_solutions` 透出）。
如需补最小前端徽标可单独安排（不在本轮强制范围内）。

---

## 补充修正（2026-08-11 晚）—— 两处与业务规则对齐

评审后收敛两点，均未进入跨页改造，先稳住"题目答案分离"这一层基础。

### 修正 A：缺答案题仍显示在 OCR 待审核，只是不能发布

- 原报告措辞"缺答案 → 不进正常待审核，只放匹配异常折叠区"**与已定规则不符**。
- 实际代码 `list_questions` 本来就返回全部草稿（不过滤 `match_status`），缺答案题一直都在列表里；
  `_draft_dict` 也已透出 `match_status`。所以**无需删除式改动**，仅修正文档语义。
- 最终语义：
  - `matched` → 正常，可发布；
  - `missing_answer` → 主待审核列表内带 ⚠，可人工补答案后恢复 `matched` 再发布，**禁止直接发布**；
  - `ambiguous` → 主待审核列表内带 ⚠，需人工处理，**禁止直接发布**；
  - 正式题库完全不展示这些 OCR 匹配状态。

### 修正 B：历史草稿默认 `matched` 的风险

- 问题：上一轮迁移给 `match_status` 列设 `DEFAULT 'matched'`，对**历史 117 条旧草稿**一律置 `matched`，
  等于把"未知历史 separate 草稿"自动认定为"已配对成功"，与"历史数据不能为了迁移方便而猜状态"冲突。
- 修复（`db.py` 迁移块，仅在首次加列时执行一次、幂等）：加列后立即回填——
  - `separate` 来源的草稿 → `unknown`（状态未知，须重新切题/匹配后才得到 matched/missing_answer/ambiguous）；
  - inline / legacy（无 layout 或 `solution_mode='inline'`）普通 OCR → `matched`（本就不是题目答案分离匹配）。
- 发布拦截扩展：`app.py:708` 现拦截 `missing_answer / ambiguous / unknown`。
- **真实库核实**：当前 `calculus_agent.db` 的 117 条草稿中，0 条来自 `separate` 来源（58 条 legacy、59 条 inline），
  故历史回填后**仍全部为 `matched`，无需改动**；`create_schema` 幂等复跑无副作用。
  未来若出现 separate 历史草稿，会被正确置 `unknown` 并强制重配，不会误判为已配对。

### 新增/调整测试

`tests/test_ocr_separate_matcher.py` 追加 3 项：
1. `test_missing_answer_still_in_review_list` —— 1,3+1,2 场景，`list_questions` 仍返回 `3(missing_answer)`，不过滤；
2. `test_migration_backfills_separate_as_unknown` —— 模拟加列前 schema，验证 separate→unknown、inline/legacy→matched；
3. `test_unknown_status_blocks_publish` —— 经 `POST /api/publish` 端点验证 `unknown` 被拦截（重绑模块级 session factory 指向临时库）。

完整 pytest 全绿，无新增失败。
