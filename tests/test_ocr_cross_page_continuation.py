"""跨页 continuation（续写 / 防误拼）专项测试。

覆盖三类真实跨页场景 + 一类「防误拼」护栏：
- Case 1：inline 答案跨页（上一题答案末段落在下一页页首）
- Case 2：inline 题目跨页（上一题题干子问落在下一页页首）
- Case 3：separate 答案跨页（答案页页首续写属于上一答案 block）
- Case 4：防页首误拼（页眉/章节标题等家具文本不得拼入上一题）

统一约定：
- inline / separate 的题目侧均经 split_pages_into_candidates（resplit、doc_pipeline、
  extract_questions 共用），答案侧经 extract_solutions。护栏在两处同源生效。
- split_pages_into_candidates 按大题号切分，每个大题产出单一 QuestionCandidate：
  题干进 body、解/解析内容进 analysis，answer 字段在本层为空（子问 2(1)/2(2) 等
  的进一步拆分由下游 split_candidate_subquestions 完成，不在本层）。
  因此本测试在大题号层级断言跨页续写归属（续写内容落在对应大题的 body/analysis）。
"""

from calculus_agent.workbench.import_pipeline import DocumentLayout, import_document
from calculus_agent.workbench.ocr import split_pages_into_candidates


# ── Case 1：inline 答案跨页 ──
# page1: Q2 题干 (1)-(10) + 解答 (1)-(9)
# page2: (10) 续写 + Q3

PAGE1_ANSWER_CONT = """2. 求下列函数的导数：
(1) x^2
(2) x^3
(3) x^4
(4) x^5
(5) x^6
(6) x^7
(7) x^8
(8) x^9
(9) x^10
(10) x^11
解
(1) 2x
(2) 3x^2
(3) 4x^3
(4) 5x^4
(5) 6x^5
(6) 7x^6
(7) 8x^7
(8) 9x^8
(9) 10x^9
"""

PAGE2_ANSWER_CONT = """(10) 11x^10

3. 求下列函数在给定点处的导数：
解 略。
"""


def test_inline_answer_continuation_across_pages():
    placed = split_pages_into_candidates([(1, PAGE1_ANSWER_CONT), (2, PAGE2_ANSWER_CONT)])
    by_num = {p.candidate.original_number: p.candidate for p in placed}

    # Q2 是单一大题候选；其答案（analysis）由跨页续写补齐第 (10) 子问
    assert "2" in by_num, "Q2 应存在"
    q2 = by_num["2"]
    assert "11x^10" in q2.analysis, "Q2 答案应包含跨页续写的 (10) 内容"

    # Q3 正常独立，且不被 Q2 答案污染
    assert "3" in by_num, "Q3 应独立存在"
    q3 = by_num["3"]
    assert "11x^10" not in (q3.body + q3.answer + q3.analysis), "Q3 不应包含 Q2 的答案续写"


# ── Case 2：inline 题目跨页 ──
# page1: Q6 题干 (1)-(4)
# page2: (5)-(10) 解（题干续写到下一页）

PAGE1_Q_CONT = """6. 求下列函数的导数：
(1) x^2
(2) x^3
(3) x^4
(4) x^5
"""

PAGE2_Q_CONT = """(5) x^6
(6) x^7
(7) x^8
(8) x^9
(9) x^10
(10) x^11
解
(1) 2x
(2) 3x^2
(3) 4x^3
(4) 5x^4
(5) 6x^5
(6) 7x^6
(7) 8x^7
(8) 9x^8
(9) 10x^9
(10) 11x^10
"""


def test_inline_question_continuation_across_pages():
    placed = split_pages_into_candidates([(1, PAGE1_Q_CONT), (2, PAGE2_Q_CONT)])
    by_num = {p.candidate.original_number: p.candidate for p in placed}

    # Q6 是单一大题候选；题干（body）跨页续写后子问应完整覆盖 (1)-(10)
    assert "6" in by_num, "Q6 应存在"
    q6 = by_num["6"]
    for sub in range(1, 11):
        assert f"({sub}) x^{sub + 1}" in q6.body, f"Q6 题干应包含第({sub})子问"
    # 页2 续写的 (10) 题干内容必须落在 Q6 题干（不丢、不串题）
    assert "x^11" in q6.body, "Q6 题干应包含跨页续写的 (10) 内容"
    # 页1 的 (1) 题干也应完整
    assert "x^2" in q6.body


# ── Case 3：separate 答案跨页 ──
# question page1: 16, 17
# solution page2: 16. 解：第一部分
# solution page3: 续写（属于 16）+ 17. 解：

PAGE_Q_SEP = """16. 证明某数列收敛
17. 证明另一数列收敛
"""

PAGE_S1_SEP = """16. 解：设 x_n 由递推定义，首先证明有界。
"""

PAGE_S2_SEP = """是 x_n² < M，故 x_n 收敛，所以 lim x_n = L。

17. 解：令 y_n = 1/n，显然趋于 0，证毕。
"""


def test_separate_answer_continuation_across_pages():
    layout = DocumentLayout("separate", [1], [2, 3])
    result = import_document(
        [(1, PAGE_Q_SEP), (2, PAGE_S1_SEP), (3, PAGE_S2_SEP)], layout
    )
    by_num = {c.candidate.original_number: c.candidate for c in result.candidates}

    assert "16" in by_num and "17" in by_num
    q16 = by_num["16"]
    q17 = by_num["17"]

    # Q16 答案应包含跨页续写的「所以 lim x_n = L」
    assert "lim x_n = L" in q16.analysis, "Q16 答案应包含跨页续写内容"
    # Q17 答案不得包含 Q16 的续写内容（不串题）
    assert "x_n²" not in q17.analysis, "Q17 答案不应包含 Q16 的续写内容"
    assert "lim x_n = L" not in q17.analysis, "Q17 答案不应包含 Q16 的续写内容"
    # 两者均应正常配对
    assert q16.match_status == "matched"
    assert q17.match_status == "matched"


# ── Case 4a：inline / 题目侧 防页首误拼 ──
# page1: Q1 完整（含答案）
# page2: 章节标题（家具）+ Q2

PAGE1_Q4 = """1. 求 f(x) = x^2 的导数。
解：f'(x) = 2x。
"""

PAGE2_Q4 = """第八章 多元函数微分学

2. 求 g(x) = x^3 的导数。
解：g'(x) = 3x^2。
"""


def test_inline_preamble_furniture_not_joined():
    placed = split_pages_into_candidates([(1, PAGE1_Q4), (2, PAGE2_Q4)])
    by_num = {p.candidate.original_number: p.candidate for p in placed}

    assert "1" in by_num and "2" in by_num
    q1 = by_num["1"]
    # 章节标题「第八章 多元函数微分学」不得进入 Q1（防页首误拼）
    assert "第八章" not in q1.body
    assert "第八章" not in q1.answer
    assert "第八章" not in q1.analysis
    # Q1 答案（analysis）保持原样，answer 字段本层为空
    assert q1.analysis == "f'(x) = 2x。"
    assert q1.answer == ""
    # Q2 正常独立
    assert "3x^2" in by_num["2"].analysis


# ── Case 4b：separate 答案侧 防页首误拼 ──
# question page1: 1, 2
# solution page2: 1. 解：甲
# solution page3: 章节标题（家具）+ 2. 解：乙

PAGE_Q4B = """1. 问题一
2. 问题二
"""

PAGE_S1_4B = """1. 解：甲
"""

PAGE_S2_4B = """第三部分 习题详解

2. 解：乙
"""


def test_separate_solution_preamble_furniture_not_joined():
    layout = DocumentLayout("separate", [1], [2, 3])
    result = import_document(
        [(1, PAGE_Q4B), (2, PAGE_S1_4B), (3, PAGE_S2_4B)], layout
    )
    by_num = {c.candidate.original_number: c.candidate for c in result.candidates}

    assert "1" in by_num and "2" in by_num
    # 页3 页首「第三部分 习题详解」是家具，不得拼入 Q1 答案
    assert "习题详解" not in by_num["1"].analysis
    assert by_num["1"].analysis == "甲"
    # Q2 答案保持原样
    assert by_num["2"].analysis == "乙"
