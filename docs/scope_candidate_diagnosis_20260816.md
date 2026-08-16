# Teacher Agent 组卷链路排查报告（2026-08-16）

排查对象：两个可能相关问题
- **问题 1**：Agent 不知道「第三章」是什么
- **问题 2**：题库候选充足，但仍返回 `insufficient_candidates`

排查原则：`Deterministic first, LLM second, Agent last`；最小修复优先级 ① scope/DB 查询 ② candidate filter ③ feasibility ④ 才考虑 Agent/Prompt。结论先给，**未执行任何业务代码修改**（按用户要求，确认根因后再改）。

---

## 1. Expected（期望）

- **问题 1**：教师 NL「给我出一套第三章的章节测试题」→ LLM 提取 scope 标签 `第三章` → 确定性 scope resolver 用 `curriculum_node.code='三'` 命中章节 → 下钻到该章全部 section → 映射 `KnowledgeNode.curriculum_node_id` → 得到稳定的 knowledge node id 集合 → 候选查询按这些 id JOIN。Agent 应**精确知道**第三章对应的知识范围，无需 LLM 猜教材目录。
- **问题 2**：当题库在「对应 scope + 题型 + 数量」上确有足量候选时，generation 应 `feasible=True`；`insufficient_candidates` 只在候选**真实不足**时才出现。
- **一致性预期**：generation 查询看到的候选集合，应 == 教师在题库页（按同一 scope 筛选）看到的题目集合。两路若发散，才说明有查询 Bug。

## 2. Actual（实际）

- **问题 1 — 非问题（代码层健康）**：`resolve_generation_scope(["第三章"])` 正确返回 **17 个 knowledge node id**（对应 `code='三'` 的「微分中值定理与导数的应用」及其小节）。端到端 `generate_paper_from_input(scope_names=["第三章"], paper_type="chapter_test")` → `ok=True`，候选 **39 题**（选择 20 / 填空 9 / 计算 4 / 证明 6）。**第三章解析正确，Agent 知道第三章是什么。** 现行症状在当前代码下不可复现——若用户曾观察到「Agent 不知道第三章」，应另查 LLM tool-call 参数透传或前端展示，而非 resolver。
- **问题 2 — 真实成立，但根因是数据覆盖缺口，不是代码 Bug**：
  - 全库当前唯一真实题源是 `ocr_import`，共 **207 题**（CMM-Math / built-in-demo 已在上一轮物理删除）。
  - `ocr_import` **只覆盖了第一~八章，且部分章节题型不齐；第九~十二章（多元函数微分法 / 重积分 / 曲线曲面积分 / 无穷级数）零道 ocr_import 题**。
  - 因此第九~十二章 generation 候选 = `0/0/0`，`insufficient_candidates` 是**真实正确**的失败，不是误报。
  - 关键：每一章 `gen_total == bank_all`（见 Evidence），generation 查询与题库页查询集合**完全一致，无任何发散** → 排除了「人工看到的集合 ≠ generation 查询集合」的假设。

## 3. Failure Boundary（失效边界）

- ❌ **不在** scope resolution：`blueprint_adapter.resolve_generation_scope`（line 154）按 `第([一二三四…0-9]+)章` 正则 → `_scope_number('三')='3'` → 匹配 `node.code=='三'` 的 chapter → 下钻 section → `KnowledgeNode.curriculum_node_id.in_(selected)`。无字符串直接表扫描，靠 code 而非 title（库内本就无 `title="第三章"` 节点，符合设计）。
- ❌ **不在** candidate filter：`selector._candidates`（line 76）scope JOIN 逻辑正确。`gen_total == bank_all` 证明没有 JOIN 误删行。
- ✅ **在数据覆盖层**：`question` / `question_knowledge_link` / `curriculum_node` 表本身缺少九~十二章的 `ocr_import` 行。这是物理数据缺口，不是查询发散。
- ✅ `insufficient_candidates` 触发点：`paper_tools.py:391`（`if not preview.feasible: blocking_errors=["insufficient_candidates", *unsatisfied]`）—— 正确业务逻辑，**不是误触发**。

## 4. Root Cause（根因 — 两现象是否同一根因）

**不是同一根因。**

- **问题 1**：代码层无 Bug，第三章解析本就正确（伪问题 / 历史残留或前端表现问题）。
- **问题 2**：真实根因是 **OCR 题源覆盖缺口**——`ocr_import` 仅覆盖第一~八章，九~十二章零题，部分早章题型不足。强行把两现象合并会误导修复方向。

## 5. Evidence（候选漏斗与数量）

全 12 章漏斗（generation 资格：approved + active + current + 非 EXCLUDED 源 + scope JOIN + canonical 题型；`bank_all` 为同 scope 下状态无关的题库可见数）：

| 章 | kn_ids | gen_total | 选择 | 填空 | 计算 | 证明 | 多选 | bank_all |
|---|---|---|---|---|---|---|---|---|
| 一 | 21 | 117 | 41 | 13 | 40 | 23 | 0 | 117 |
| 二 | 11 | 16 | 6 | 3 | 7 | 0 | 0 | 16 |
| 三 | 17 | 39 | 20 | 9 | 4 | 6 | 0 | 39 |
| 四 | 11 | 14 | 5 | 7 | 1 | 1 | 0 | 14 |
| 五 | 11 | 41 | 17 | 12 | 12 | 0 | 0 | 41 |
| 六 | 7 | 14 | 3 | 7 | 4 | 0 | 0 | 14 |
| 七 | 21 | 2 | 1 | 1 | 0 | 0 | 0 | 2 |
| 八 | 7 | 20 | 3 | 12 | 5 | 0 | 0 | 20 |
| 九 | 11 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| 十 | 6 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| 十一 | 8 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| 十二 | 5 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |

- 默认 `CHAPTER_TEST_TEMPLATE = 选择题×4, 填空题×2, 计算题×4`（共 10 题）。
- 第三章：17 kn_ids，39 候选 → 足以生成 → **问题 1 非问题**成立。
- 九~十二章：`gen_total == bank_all == 0` → OCR 零覆盖 → `insufficient_candidates` 真实。
- 二/四/六/七/八章：有覆盖但某题型不足（如第七章仅 1 选择/1 填空/0 计算）→ 同级缺口，只是程度较轻。
- 题源分布：仅 `ocr_import: 207`（已无 CMM/demo）。

## 6. Minimal Fix（最小修复 — 尚未执行）

修复优先级遵循「最小且对症」：

1. **不改** scope resolver 与 candidate query —— 它们本身正确（无 Bug 可修）。
2. **（可选·低风险的增量改进）** 在 `insufficient_candidates` 返回时附**结构化覆盖诊断**：逐题型给出 `required vs available`，让教师得到可行动反馈（「第三章证明题够，但计算题仅 4 题缺 0；第九章全部为 0，请补充题源」），而非一堵硬墙。feasibility 判定逻辑保持不变，仅增强报错信息。
   - 落点：`paper_tools.py` `_execute_generation_request` / `compose_paper` 中，从各题型候选计数计算 `coverage_gap` 摘要，并入 `blocking_errors`/`warnings`。
3. **（真正解决症状的修复）** 补录九~十二章（及薄弱章节）的 `ocr_import` 题源 —— 这是消除 `insufficient_candidates` 的根本手段。
4. **明确不做的**：改 Prompt、硬编码教材目录、人为扩大题库、把 LLM 拉进 scope 解析。

> 以上 1-3 在用户确认根因后再执行并跑回归。

## 7. Regression Tests（回归测试）

- **Case 1 — 第三章 Scope Resolution**：`resolve_generation_scope(["第三章"])` 返回 id 集合 == `code='三'` 章节子树 knowledge ids，无 `invalid_scope`/`ambiguous_scope` 错误。
- **Case 2 — 候选充足时生成成功**：`generate_paper_from_input(scope_names=["第三章"], paper_type="chapter_test")` → `ok=True` 且返回 10 题。
- **Case 3 — 候选真实不足时正确诊断**：构造某章（或复用真实第九章）真不足场景 → generation `feasible=False`，`blocking_errors` 含 `insufficient_candidates`，且覆盖诊断列出缺口题型与 `required/available` 计数。
- **Case 4 — Soft Difficulty 不阻塞**：题量充足但难度梯度不完美（`difficulty_progression_is_soft` warning）时，generation 仍成功（仅 warning，不进入 blocking）。

---

### 验证脚本
`scripts/diagnose_scope_candidates.py`（stdlib sqlite3，无重依赖）可随时复跑，输出上面的漏斗表。
