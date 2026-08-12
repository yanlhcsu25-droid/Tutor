# OCR 审核 Markdown 空 Section 去除 + 发布结构化同步 — 验收报告

> 日期：2026-08-11
> 范围：仅调整 OCR 审核阶段 Markdown 展示 + publish 同步逻辑。未改动 OCR / splitter / resplit 核心。

## 一、OCR Markdown 最终保留哪些 section

`markdown_schema.fixed_template` 不再输出空的 `## 章节` / `## 知识点` / `## 难度`。
最终保留 section：

- `## 题目内容`
- `## 参考解答`
- `## 题型`
- `## 来源页码`
- `## 原始题号`
- `## 审核备注`（review_section）

`SECTION_ORDER` 已同步去除上述三个字段。前端 `OcrReviewDrawer` 用 `stripOcrMetaSections()` 在加载/保存/新建 revision 时再兜底剥离一次，确保任何历史 draft 的残留空 section 也不显示。

> 注意：从 Markdown 移除这些 section **不等于**题库不再显示知识点/难度——题库数据源是结构化的，与 Markdown 无关。

## 二、正式题库知识点的数据来源

`api.py` 经 `QuestionKnowledgeLink`（正式题 ↔ `knowledge_node_id`）反查 `KnowledgeNode.name` 得到名称列表，前端 `knowledge_name` 仅用于展示。
发布时 `publish_ocr_draft` 已补齐同步：从 `draft.knowledge_points_json`（knowledge_id 列表）建 `QuestionKnowledgeLink`，index 0 为 primary、其余为 secondary。

## 三、正式题库难度的数据来源

难度经最新一条 approved 的 `QuestionProfile.difficulty` 读取（1~5）。
发布时 `publish_ocr_draft` 新建一条 `profile_source="human"`（OCR 审核阶段教师人工设定难度，属画像来源维度，非业务流程来源）、status=approved 的 `QuestionProfile`，`difficulty` 取 `draft.difficulty_level`（缺失兜底 3）。

> 注：曾写入 `profile_source="ocr_publish"`，与 `QuestionProfileRead` 枚举（`auto`/`human`/`corrected`）冲突，导致 `GET /api/v1/questions/search` 经 `list_question_profiles` → `QuestionProfileRead` 抛 Pydantic `ValidationError` 而 500。已于 2026-08-11 修正为 `"human"`，并将库中 4 条历史 `ocr_publish` 记录回填为 `"human"`。

## 四、章节如何反推 / 展示

`derive_chapter_from_knowledge(session, knowledge_ids)`：取主知识点（`knowledge_ids[0]`）对应的 `KnowledgeNode.curriculum_node_id`，沿 `CurriculumNode` 父链向上拼接 `title`，形如 `导数与微分 / 隐函数及由参数方程所确定的函数的导数`。
`publish_ocr_draft` 的 `source_topic` 优先用反推结果，Markdown 中的 `## 章节` 仅作兜底。正式题库 `source_topic` 即章节来源。

## 五、publish 后 QuestionKnowledgeLink 是否正确

`tests/test_publish_ocr_draft_structured_sync.py::test_publish_syncs_knowledge_links_and_profile` 验证：
- `QuestionKnowledgeLink` 行数与 `knowledge_ids` 一致，且 primary/secondary 顺序正确；
- `QuestionProfile.difficulty == 2` 且 status=approved；
- `source_topic == "导数与微分 / 隐函数及由参数方程所确定的函数的导数"`。

无知识点时 `test_publish_without_knowledge_is_safe` 验证：不建 link，但 profile（difficulty 兜底 4）仍正常创建——无崩溃。

## 六、revision 是否能正常回填

`database.create_revision` 复制源题的 `knowledge_points_json` / `difficulty_level`（结构化字段），不从 Markdown 反解，因此回填稳定，不会因 Markdown 缺 section 而空白。
`OcrReviewDrawer.createRevision` 走 `stripOcrMetaSections` 仅清展示层，结构化卡片（知识点 Select + 难度 InputNumber）仍源自结构化数据。

## 七、修改文件

| 文件 | 改动 |
|------|------|
| `src/calculus_agent/workbench/markdown_schema.py` | `fixed_template` 去除空 `## 章节/知识点/难度`；`SECTION_ORDER` 同步 |
| `src/calculus_agent/ocr/import_service.py` | `publish_ocr_draft` 改为从结构化字段同步 link/profile；新增 `derive_chapter_from_knowledge` / `_sync_knowledge_links` / `_sync_publish_profile` |
| `web/src/components/OcrReviewDrawer.tsx` | 新增 `stripOcrMetaSections()` 兜底剥离加载/保存/revision 的空 meta section |
| `tests/test_workbench_metadata_integrity.py` | 断言 `## 难度/知识点/章节` count == 0 |
| `tests/test_publish_ocr_draft_structured_sync.py` | 新增：发布结构化同步 + 无知识点安全路径 |

## 八、测试结果

- 新增 + 完整性测试 `test_publish_ocr_draft_structured_sync.py` / `test_workbench_metadata_integrity.py`：**18 passed**
- 回归：`test_workbench_publish_freeze` / `test_selection_validation_and_markdown` / `test_ocr_subanswer_split` / `test_formal_question_edit` / `test_workbench_source_delete`：全绿
- 前端 `tsc --noEmit`：零错误

### 已知未处理项（与本任务无关，按约束未动）
`test_ocr_separate_resplit::test_question_number_change_reruns_matcher_and_keeps_diagnostics` 为**预存失败**（经 `git stash` 确认在基线无我改动时同样失败，属 resplit 诊断文案断言）。用户明确要求禁止改动 OCR/resplit 核心，故未修复，仅记录。
