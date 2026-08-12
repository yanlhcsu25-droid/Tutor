# profile_source 枚举语义修复 + 历史错误数据回填 — 验收报告

> 日期：2026-08-11
> 范围：仅修正 `profile_source` 枚举语义不一致与库中已有错误数据。未改动 OCR / splitter / resplit / 知识点 RAG·LLM / difficulty 取值逻辑 / question_id·revision 语义。

## 一、根因

`QuestionProfileRead.profile_source` 枚举仅允许 `auto` / `human` / `corrected`（见 `src/calculus_agent/schemas.py:312`）。
但 `publish_ocr_draft()` 经 `_sync_publish_profile()` 写入 `profile_source="ocr_publish"`（见 `import_service.py`，本轮已修正）。

`GET /api/v1/questions/search` → `search_questions` → `list_question_profiles(session, status="approved", source_name=None)` 对**全库** approved `QuestionProfile` 构造 `QuestionProfileRead`。
只要存在任何 `profile_source="ocr_publish"` 的行，Pydantic 校验即失败 → 整个题库接口 500。

## 二、修正语义（最小修复，不扩功能）

`import_service.py` 的 `_sync_publish_profile()`：

```python
# 改前
profile_source="ocr_publish",
# 改后
profile_source="human",   # OCR 审核阶段难度由教师人工设定并确认，属 human 来源
```

**未把 `"ocr_publish"` 加入枚举**——`profile_source` 语义是画像判断来源（auto/human/corrected），`ocr_publish` 是业务流程来源，不属于同一维度；如需记录「由 OCR 发布流程创建」，应使用独立 origin/audit 字段，本轮不实现。

## 三、历史错误数据处理

### 统计（只读）
`SELECT DISTINCT profile_source FROM question_profile` 分布：

- auto: 183
- corrected: 61
- human: 61（回填前）/ 65（回填后）
- **ocr_publish: 4**（全部 `approved`、difficulty=2、created_at 2026-08-11 11:43–11:46）

4 条 `ocr_publish` 记录：

| question_id | difficulty | status | 题型 |
|---|---|---|---|
| 43302d57-1dd3-4de3-a866-7da1117397aa | 2 | approved | calculation |
| d2c51a76-bee6-45bf-a811-bb9fb4802622 | 2 | approved | calculation |
| 647bc137-c197-44cf-8260-e467b7202845 | 2 | approved | calculation |
| 21eada00-5d49-4ba1-8712-717b3c47b913 | 2 | approved | calculation |

确认：4 题均存在于 `question` 表（review_status=approved），且每题**仅此一条** profile，无其它来源，回填安全。

### 备份
`calculus_agent.db.bak_20260811_2009_ocrpublish`（发布前完整库）。

### 回填
```sql
UPDATE question_profile SET profile_source='human' WHERE profile_source='ocr_publish';
-- 影响 4 行
```
未改动 `difficulty` / `profile_status` / `question_id` / `created_at` / 其它 profile 内容。
回填后 `DISTINCT profile_source` = `auto`(183) / `corrected`(61) / `human`(65)，无 `ocr_publish`。

## 四、验证结果

| # | 验收项 | 结果 |
|---|---|---|
| 1 | `GET /api/v1/questions/search?limit=50&source_name=ocr_import,ocr_doc` 返回 200（不再 500） | ✅ 实测 200，返回 50 题 |
| 2 | OCR 正式题正常加载 | ✅ 接口 200，OCR 题正常返回 |
| 3 | difficulty 正常显示 | ✅ 来自 `QuestionProfile.difficulty`（结构化） |
| 4 | knowledge points 正常显示 | ✅ `QuestionKnowledgeLink` 3218 行，经 `KnowledgeNode.name` 反查 |
| 5 | `QuestionProfileRead` 可解析全部返回 profile | ✅ 无 `ValidationError`；并独立复现旧 bug（`ocr_publish` → `ValidationError`）证因果 |
| 6 | 新发布 OCR 题 profile_source == "human" | ✅ `test_publish_syncs_knowledge_links_and_profile` / `test_publish_without_knowledge_is_safe` 断言 |
| 7 | revision 再发布不生成非法 profile_source | ✅ 新增 `test_revision_republish_overwrites_profile_with_human`：经 `formal_question_id` 分支覆盖重写为 human |
| 8 | `SELECT DISTINCT profile_source` 仅合法值 | ✅ `auto`/`corrected`/`human`，无异常值 |
| 9 | 相关 pytest 通过 | ✅ publish 同步 4 项 + profiling + 完整性 + 5 套回归全绿 |
| 10 | 完整 pytest 除已知 resplit 预存失败外无新增失败 | ✅ 唯一失败为预存 `test_ocr_separate_resplit::test_question_number_change_reruns_matcher_and_keeps_diagnostics`（与本次无关，禁改范围） |

## 五、修改文件

| 文件 | 改动 |
|---|---|
| `src/calculus_agent/ocr/import_service.py` | `_sync_publish_profile()` 的 `profile_source` 由 `"ocr_publish"` 改为 `"human"`（仅此一处写入点） |
| `tests/test_publish_ocr_draft_structured_sync.py` | 两处发布测试断言 `profile_source == "human"`；新增 `test_revision_republish_overwrites_profile_with_human` |
| `docs/ocr_markdown_acceptance.md` | 同步修正上一轮报告中已失效的 `ocr_publish` 描述 |

> 数据库 `calculus_agent.db`：回填 4 行（已备份）。`schemas.py` 枚举未改动。

## 六、已知未处理项（与本次无关，按约束未动）
`test_ocr_separate_resplit::test_question_number_change_reruns_matcher_and_keeps_diagnostics`：预存失败（resplit 诊断文案断言），用户禁止改动 OCR/resplit 核心，仅记录。
