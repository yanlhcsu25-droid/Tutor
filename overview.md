# OCR 导入流程：统一边界 + 跨页续写 + 防误拼 —— 交付总览

## ① 当前真实调用链（修改前 → 修改后）

**修改前（已确认）：**
```
PDF → ocr/service.create_doc_ocr_task_async
    → doc_pipeline.parse_pdf_to_candidates
        → run_ppstructure (PPStructureV3, 每 PDF 一次) → 每页原始 Markdown
        → workbench/ocr.split_pages_into_candidates
            (inline: _run_inline_ppstructure_into_database → _raw_page_chunks → split_major_questions → preamble/chunks → pending 续拼)
    → QuestionCandidate → render_drafts → persist → OcrImportDraft
separate: import_document → extract_questions + extract_solutions → match_questions_and_solutions → render → persist
```

**修改后（结构不变，仅 3 处 preamble 拼接点插入护栏）：**
```
PDF → ocr/service.create_doc_ocr_task_async
    → doc_pipeline.parse_pdf_to_candidates
        → run_ppstructure (每 PDF 一次) → 每页原始 Markdown
        → workbench/ocr.split_pages_into_candidates
            (inline: ... pending 续拼 ← 加 _should_join_cross_page 护栏 @ ocr.py:522)
    → OcrImportDraft
separate: import_document → extract_questions + extract_solutions
            (extract_solutions 续拼 ← 加护栏 @ import_pipeline.py:160；pending=None 家具不入 unmatched @ :163)
            → match_questions_and_solutions → persist
resplit: resplit.apply_plan → import_document（仅重跑 parser，不调 run_ppstructure）
```
> 关键点：**统一 OCR 已满足** —— inline/separate 不建两套 OCR，OCR 层只产出原始 Markdown，题号/答案/跨页判定全在 parser 层。

## ② 修改文件逐说明
- `src/calculus_agent/workbench/ocr.py`
  - 新增 `_PAGE_FURNITURE_RE` / `_PAGE_FURNITURE_MARKERS` / `_is_page_furniture` / `_should_join_cross_page`。
  - 在 `split_pages_into_candidates` 的 pending 续拼点（line 522）应用护栏。
  - **修复 `\b` 词边界 bug**：中文章节标题分支 `第X章\w` → `第X章`（中文连续字符间 `\b` 不生效，导致「第八章…」「第三部分…」被误判为续写拼进上一题）。
- `src/calculus_agent/workbench/import_pipeline.py`
  - import 上述两函数；`extract_solutions` 两分支：pending 续拼加护栏（:160）、pending=None 时家具文本丢弃不入 unmatched（:163）。
- `tests/test_ocr_cross_page_continuation.py`（新增 5 用例）。

## ③ 跨页状态机 / 判断规则
- 函数级状态机：`_should_join_cross_page(pending_raw, preamble)`：
  1. preamble 为空 → 不拼。
  2. preamble 是家具（页眉/页脚/章节标题/出版信息）→ **丢弃，不污染上一题**（防误拼护栏）。
  3. 其余 → **默认续拼**（与既有"页首无新题号则续拼"语义一致），由后续 splitter 按 解/解析 边界自然截断。
- 当前 pending 处于 question 还是 answer 段落，由 `pending_raw` 是否含"解/解析/答案"隐式决定；本函数只拒绝明显家具，不依赖 LLM、不堆页面特判。
- inline 与 separate 跨页逻辑分处 `split_pages_into_candidates` 与 `extract_solutions`，互不耦合。

## ④ 三类 case 实际解析结果（实测）
- **Case 1 inline 答案跨页**：Q2 的 `analysis` 跨页补齐 `(10) 11x^10`；Q3 独立且不含 Q2 答案（不串题）。✅
- **Case 2 inline 题干跨页**：Q6 的 `body` 跨页补齐 (5)-(10) 子问，含 `x^11`；不丢、不串。✅
- **Case 3 separate 答案跨页**：Q16 的 `analysis` 跨页续写含「lim x_n = L」；Q17 不含 Q16 续写；二者均 `matched`。✅
- **Case 4 防误拼**：「第八章 多元函数微分学」「第三部分 习题详解」等家具不进上一题。✅

## ⑤ 测试结果
- 新增 `tests/test_ocr_cross_page_continuation.py`：**5 个全部通过**（Case1/2/3/4a/4b）。
- 完整 pytest：**255 passed**（仅 1 个无关 httpx 弃用警告，0 failed 0 error）。

## ⑥ 修改 Markdown 后重新切题，是否重新跑 PaddleOCR？
**不会。** 重新切题只重新运行 parser/import pipeline（`import_document` / `split_pages_into_candidates` / `extract_solutions` / `match_questions_and_solutions`），**不加载 PaddleOCR、`run_ppstructure` 仅在首次 OCR 生产原始 Markdown 时跑一次**，resplit 路径 (`resplit.apply_plan`) 同样只调 `import_document`。

## 约束遵守核对
- ✅ 未新建第二套 PaddleOCR；✅ 未破坏 `match_status`（matched/missing_answer/ambiguous/unknown 语义保留）；✅ 未修改真实 117 条数据；✅ 未用 LLM 解题号/跨页；✅ OCR 与 resplit 两阶段清晰；✅ 未重构前端。
