# OCR 导入流程 真实 PDF 端到端 dry-run 验收报告

> 本轮：不改代码，用两类真实 PDF 验证「统一 OCR → parser → 跨页 → 子题拆分 → matcher → Draft」完整链路。
> 零 DB 污染：复用缓存 Markdown + 内存跑 parser/matcher/render_drafts；separate 跑 PPStructureV3（无 DB）。

---

## 一、测试文件

| 类型 | PDF / 缓存源 | 页数 | 来源 | 模式 |
|------|-------------|------|------|------|
| inline（教材习题） | `src_d8f8...`（习题2-2 函数的求导法则） | 5 | 真实高数教材，PPStructureV3 缓存 | inline |
| separate（套卷） | `output/pdf/示例-教师解析卷.pdf` | 2 | **合成样本**（八年级，项目内无真实高数 separate PDF） | separate |

> ⚠️ **样本限制**：项目内无真实高数 separate 型 PDF（记忆确认"真实库 0 条 separate"）。separate 路径仅能用合成样本验证（结构正确但仅 3 题、八年级）。

---

## 二、OCR 层检查（第三节）

### inline（src_d8f8，复用缓存）
1. **OCR 耗时**：缓存复用（此前已跑，本轮 0s）。PPStructureV3 整 PDF 一次。
2. **是否只初始化一次 PPStructureV3**：✅ 是。`parser = PPStructureV3()` 构造一次，`parser.predict(input=pdf)` 迭代所有页。
3. **每页 Markdown 路径**：`workbench_data/ocr_raw/src_d8f8.../page_0001.md` ~ `page_0005.md`。
4. **乱码**：无严重乱码。公式以 `$$...$$` LaTeX 保留。
5. **公式 OCR 错误**：**有**。page2 首行 `(10)...$$...3.求下列函数在给定点处的导数：$$` —— OCR 把 Q2(10) 答案与 Q3 题干合并进同一 `$$` 块；page2 Q6 子题标记乱序：`(1)\n(3)\n$$y=(2x+5)^4$$\n$$(3)y=e^{-3x^2}$$\n(2)\n(4)` —— 既有裸 `(3)` 又有 `$$(3)...$$`，(1)/(2) 无公式。
6. **页码/页眉/水印进 Markdown**：✅ **是**。page1 末尾"配套课程请加QQ群：754986907，关注微信公众号（研者荣耀）获取更多考研资源"，page2 末尾"关注微信公众号…"。**但未污染最终 Draft**（见下）。
7. **整页失败/空页**：无。

### separate（示例-教师解析卷，本轮 fresh OCR）
1. **OCR 耗时**：53.6s（2 页，含首次模型加载；模型已缓存至 `~/.paddlex/`）。
2. **是否只初始化一次 PPStructureV3**：✅ 是。
3. **Markdown 质量**：page1 选项 OCR 瑕疵——"A.-、二、三 B.-、二$ 、四C.一、三、四D.二、三、四"（"一"丢失、选项合并）。page2 答案"第1题/第2题/第3题"格式正确识别为文本。

> **重要架构发现**：工作台 `run_ocr_into_database` 的 inline 与 separate 用**不同 OCR 引擎**——inline=PPStructureV3（整 PDF 一次），separate=逐页轻量 PaddleOCR（`_run_lightweight_paddleocr_page` 子进程）。本轮 separate dry-run 用 PPStructureV3 替代（验证 parser/matcher 层，非 OCR 引擎）。**这与上轮"统一 OCR 已满足"结论有出入**——上轮结论针对 `doc_pipeline.parse_pdf_to_candidates`（非工作台路径），工作台真实路径并未统一 OCR。

---

## 三、inline 重点验收（第四节）

### 父题数量与最终 Draft
- **父候选**：9 个（Q1-Q9），`split_pages_into_candidates` 产出。
- **最终 Draft**：45 个（经 `split_candidate_subquestions` 拆分）。
- **跨页正确率**：2/2 父题级跨页正确（Case A + Case B）。

### Case A：答案跨页（page1→page2，Q2）✅
- Q2 题干 (1)-(10) + 解答 (1)-(9) 在 page1；page2 开头 `(10) 11x^10` 是 Q2 答案续写。
- **结果**：Q2.analysis 含 `(10)` + `cos t` 续写内容 ✅；Q3 不被污染 ✅。
- **子题拆分**：2(1)-2(10) 共 10 个，**每个子题答案一一对应**（2(1)→3x², 2(2)→15x²...2(10)→cos t 公式）✅。
- **未出现**"每个子题继承整段 Q2 解答"bad case ✅。

### Case B：题目跨页（page2→page3，Q6）✅ 父题级 / ✗ 子题级
- Q6 题干 (1)-(4) 在 page2；page3 开头 (5)-(10) 是 Q6 题干续写。
- **父题级**：Q6.body 含 (1)-(10) ✅；不产生无题号 candidate ✅；(5)-(10) 未拼到其他题 ✅。
- **子题级 ✗ BAD CASE**：仅拆出 8 个子题（6(3)-6(10)），**缺 6(1)/6(2)**；且 **6(3)-6(10) 全部继承相同整段 Q6 解答**（正是用户警告的 bad case）。

### Case C：页首家具 ✅
- page1 末尾"QQ群/微信公众号/获取更多"页脚、page2 末尾"微信公众号"页脚：**均未进入最终 Draft** ✅。
- `_PAGE_FURNITURE_RE` 未误删正常题目正文 ✅（Q1-Q9 题干完整）。

---

## 四、separate 重点验收（第五节）

### layout
- question_pages=[1], solution_pages=[2]（示例-教师解析卷 page1=题目, page2=参考答案与解析）。

### question side
- 得到 Q1/Q2/Q3 共 3 题 ✅（无漏题/重复）。
- Q1 选择题选项 OCR 有瑕疵但语义保留。

### solution side ✗ BAD CASE
- 答案页用"第1题/第2题/第3题"格式。
- `extract_solutions`→`split_major_questions` **不识别"第N题"格式**；答案中"1.因为…"列表项被**误判为题号"1"**。
- 结果：3 个答案全编号"1" → ambiguous。

### matcher 统计
| 指标 | 理想 | 实际 |
|------|------|------|
| matched | 3 | **0** |
| missing_answer | 0 | **2**（Q2, Q3）|
| ambiguous | 0 | **1**（Q1）|
| unmatched_solutions | 0 | **4** |

> 注：此 bad case 部分源于合成样本用"第N题"格式（真实套卷多用"1. C【解析】"格式，能被识别）。但"答案中 `1.` 列表项误判为题号"是通用风险。

---

## 五、最终 Draft 层检查（第六节）

### inline 父题→子题树
```
1  └─ 1
2  ├─ 2(1)  ans: y'=3x²          ✅
   ├─ 2(2)  ans: y'=15x²         ✅
   ├─ ...
   └─ 2(10) ans: cos t 公式(跨页) ✅
3  ├─ 3(1)  ├─ 3(2)  ├─ 3(3)
4  └─ 4
5  └─ 5    ✗ answer 留在 body, analysis 空
6  ├─ 6(3)  ans: 整段Q6解答 ✗
   ├─ 6(4)  ans: 整段Q6解答 ✗
   ├─ 6(5)  ans: 整段Q6解答 ✗
   ├─ ...
   └─ 6(10) ans: 整段Q6解答 ✗
   （缺 6(1), 6(2)）
7  ├─ 7(1)-7(10)  各自独立答案 ✅
8  ├─ 8(1)-8(10)  各自独立答案 ✅
9  └─ 9
```

### separate 最终 Draft
- Q1: match=ambiguous, 答案未正确配对
- Q2: match=missing_answer, 参考解答为空
- Q3: match=missing_answer, 参考解答为空

---

## 六、Bad Case 表（第七节）

| # | PDF | page | question | stage | expected | actual | category |
|---|-----|------|----------|-------|----------|--------|----------|
| 1 | inline src_d8f8 | 2 | Q6 | OCR | 子题标记清晰有序 | OCR 产生重复(3)/(4)标记 + 空(1)/(2) | **OCR** |
| 2 | inline src_d8f8 | 2 | Q6 | subquestion_split | 6(1)-6(10)各自独立答案 | 仅8子题(缺6(1)/6(2))，全部继承整段Q6解答 | **subquestion_split** |
| 3 | inline src_d8f8 | 2 | Q5 | answer_split | "解"后内容进 analysis | 答案留在 body，analysis 空 | **answer_split** |
| 4 | inline src_d8f8 | 2 | Q6(10) | OCR | (10)答案与Q3题干分离 | OCR 把(10)答案与"3.求下列函数"合并进同一`$$`块 | **OCR** |
| 5 | separate 示例 | 2 | A1-A3 | answer_split | "第N题"格式识别为答案编号 | 不识别；"1."列表项误判为题号 | **answer_split** |
| 6 | separate 示例 | 2 | A1-A3 | matcher | matched=3 | matched=0, ambiguous=1, missing=2, unmatched=4 | **matcher** |
| 7 | workbench | - | - | OCR | inline/separate 共用统一 OCR | inline=PPStructureV3, separate=轻量PaddleOCR（两套引擎） | **OCR** |

---

## 七、每个 bad case 属于哪一层（第八节）

1. **Q6 子题标记重复/乱序** → **OCR 层**（PPStructureV3 产出重复 `(3)` 标记）。parser 无法修复 OCR 产出。
2. **Q6 子题全继承整段答案** → **subquestion_split 层**。`_split_answer_for_subquestions` 检测到重复编号→返回 `(None,True)`→`_split_independent_candidates` 回退把完整父题 analysis 赋给所有子题。`needs_review=True`+review_note 已设，但 `match_status` 仍为 `matched`（未反映问题）。
3. **Q5 答案留在 body** → **answer_split 层**。`_candidate_from_raw` 的"解"边界检测未识别"法线方程.解"（"解"紧跟句号无换行）。
4. **Q6(10) 答案与 Q3 题干合并** → **OCR 层**（`$$` 块内合并）。
5. **"第N题"格式不识别** → **answer_split 层**。`split_major_questions` 正则不匹配"第N题"。
6. **"1."列表项误判为题号** → **matcher 层**。`split_major_questions` 把答案中的"1.因为…"误识别为题号"1"。
7. **工作台两套 OCR 引擎** → **OCR 层**（架构层面，非本轮引入）。

---

## 八、是否建议修改代码（第九节）

**建议修改**，但本轮不执行（遵循"先不动代码"）。最小改动点：

### 改动点 1（subquestion_split，最高优先级）
**问题**：`_split_independent_candidates` 第 857 行，`analysis_segments is None` 时回退 `candidate.analysis`（完整父题答案）→ 所有子题继承整段答案。
**最小改法**：当 `_split_answer_for_subquestions` 返回 `(None, True)`（不可靠）时，子题 analysis 应留空（`""`）而非继承完整父题答案；保留 `needs_review=True` + review_note。这样避免答案复制污染，人工复核时仍能看到父题完整答案（在父题 Draft 上）。
**影响**：Q6 的 8 个子题 analysis 从"整段重复"变为"空+needs_review"，更安全。

### 改动点 2（subquestion_split，中优先级）
**问题**：Q6 缺 6(1)/6(2)——body 中 (1)/(2) 标记后无文本（OCR 丢公式）→ `sub_items` 跳过空文本。
**最小改法**：对被跳过的空子题，保留编号占位（body="（OCR 未识别题干）"），`needs_review=True`，避免子题静默丢失。

### 改动点 3（answer_split，中优先级）
**问题**：Q5 "法线方程.解" 的"解"未触发 body/answer 分割。
**最小改法**：在 `_candidate_from_raw` 的"解"边界正则中，兼容"句号/分号后紧跟解"的情况（如 `\.解` / `；解`）。

### 改动点 4（matcher/answer_split，低优先级）
**问题**：答案中"1.因为…"列表项被误判为题号。
**最小改法**：`split_major_questions` 对 solution 页的题号匹配增加上下文约束（如题号后须跟答案/解析标记"答案：/解："而非普通正文）。

### 改动点 5（OCR 架构，低优先级，大改）
**问题**：工作台 inline/separate 用两套 OCR 引擎。
**最小改法**：separate 路径也改用 PPStructureV3（整 PDF 一次），统一 OCR 层；`restore_vector_blanks`（填空题横线恢复）作为后处理保留。此改动较大，需评估 separate 模式的填空题横线需求。

### 不建议改
- OCR 层的公式合并/标记乱序（改动点 1/4）：OCR 模型局限，应通过 parser 容错解决，不改 OCR 模型。
- `_PAGE_FURNITURE_RE`：本轮验证未误删正文，无需改。

---

## 九、最终结论

**当前 255 passed 的实现能否真正处理这两类真实高数 PDF？**

### inline（真实高数）—— **基本可用，有 2 个明确 bad case**
- ✅ 跨页续写（答案跨页 + 题目跨页）父题级正确。
- ✅ 页首家具防误拼有效，未误删正文。
- ✅ Q2/Q7/Q8 子题拆分 + 答案一一对应正确。
- ✗ Q6 子题全继承整段答案（subquestion_split 回退缺陷）。
- ✗ Q5 答案留在 body（answer_split 边界检测缺陷）。
- **结论**：跨页与防误拼护栏真实生效；子题拆分在 OCR 标记清晰时正确，OCR 乱序时回退策略有缺陷（改动点 1 可修）。

### separate（合成样本）—— **不可用，matcher 全错**
- ✗ "第N题"答案格式不识别 + "1."列表项误判 → matched=0。
- **结论**：separate 路径在标准"1. C【解析】"格式下可能正常（未验证真实样本），但"第N题"格式 + 答案内列表项是真实风险。**需真实高数 separate PDF 复验**。

### 统一 OCR —— **工作台路径未统一**
- inline=PPStructureV3（整 PDF 一次），separate=轻量 PaddleOCR（逐页子进程），两套引擎。
- 上轮"统一 OCR 已满足"结论仅对 `doc_pipeline.parse_pdf_to_candidates`（非工作台路径）成立，工作台真实路径未统一。

### 是否重新跑 OCR？
重切题（resplit）只重跑 `import_document`（parser 层），不重载 PaddleOCR/PPStructureV3 ✅（本轮 inline dry-run 复用缓存验证）。
