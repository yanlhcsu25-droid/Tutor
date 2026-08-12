"""一级大题切分（workbench/ocr.split_page_markdown）回归测试。

聚焦一个真实 bad case：OCR 把第 3 题题号识别成 ``$ \\*3$ 根据...``，
导致第 3 题漏识别、第 2 题的解析吞掉第 3、4 题。

这些测试只改“一级大题边界识别”，不触碰二级 (1)(2)(3) 拆分、
Markdown schema、answer/solution_content、QuestionBank、发布状态机、数据库。
"""

import pytest

from calculus_agent.workbench.ocr import (
    QUESTION_START_RE,
    _normalize_question_line,
    split_page_markdown,
)


# OCR 噪声把第 3 题题号包进 $…$ 且题号后无分隔符的真实 bad case 页面。
BADCASE_PAGE = """2. 计算下列极限
(1) 求 $\\lim_{x\\to 0}\\frac{\\sin x}{x}$
(2) 求 $\\lim_{x\\to 0}\\frac{1-\\cos x}{x^2}$
(3) 求 $\\lim_{x\\to 0}(1+x)^{1/x}$
(4) 求 $\\lim_{x\\to 0}\\left(\\frac{1+\\cos x}{2}\\right)^{1/x^2}= e^{-k}$

解析：
(1) 原式 = 1；(2) 原式 = 1/2；(3) 原式 = e；(4) 原式 = e^{-1/2}。

$ \\*3$ 根据函数极限的定义，证明极限存在的准则I′
准则I′ 如果对于任意给定的正数 ε，存在正整数 N……
解 由极限定义，对 ε>0……
注 此极限为重要极限之一。

4.利用极限存在准则证明：
(1) 证明 $\\lim_{n\\to\\infty}(1+1/n)^n=e$
(2) 证明 $\\lim_{n\\to\\infty}\\left(1+\\frac{1}{n}\\right)^{n+1}=e$
(5) 证明 $\\lim_{x\\to 0}\\frac{\\sin x}{x}=1$
解(1) 令 n→∞……
"""


def test_badcase_splits_into_three_questions():
    cands = split_page_markdown(BADCASE_PAGE)
    numbers = [c.original_number for c in cands]
    assert len(cands) == 3, f"预期 3 道一级大题，实际 {len(cands)}：{numbers}"
    assert numbers == ["2", "3", "4"]


def test_badcase_q2_solution_excludes_q3_q4():
    cands = {c.original_number: c for c in split_page_markdown(BADCASE_PAGE)}
    q2 = cands["2"]
    # 第 2 题的解析（solution_content）不应吞入第 3、4 题。
    assert "根据函数极限的定义" not in q2.analysis
    assert "利用极限存在准则证明" not in q2.analysis


def test_badcase_q3_content_and_solution():
    cands = {c.original_number: c for c in split_page_markdown(BADCASE_PAGE)}
    q3 = cands["3"]
    assert "根据函数极限的定义" in q3.body
    # 第 3 题的解析不应吞入第 4 题。
    assert "4.利用极限存在准则证明" not in q3.analysis


def test_badcase_q4_content():
    cands = {c.original_number: c for c in split_page_markdown(BADCASE_PAGE)}
    q4 = cands["4"]
    assert "利用极限存在准则证明" in q4.body


def test_history_normal_question_numbers():
    """历史正常 case：2./3./4. 不被容错逻辑回归。"""
    page = (
        "2. 计算下列极限\n(1) a\n(2) b\n\n"
        "3. 证明某定理\n证 略。\n\n"
        "4. 利用洛必达法则\n解 略。\n"
    )
    cands = split_page_markdown(page)
    assert [c.original_number for c in cands] == ["2", "3", "4"]


# ---- 一级大题题号前缀识别 ----

POSITIVE_PREFIXES = [
    ("3. 求极限", "3"),
    ("3．求极限", "3"),
    ("■ 3. 求极限", "3"),
    ("▣4. 求极限", "4"),
    ("$ \\*3$ 根据函数极限的定义，证明极限存在的准则I′", "3"),
]


@pytest.mark.parametrize(("raw", "expected_number"), POSITIVE_PREFIXES)
def test_question_number_prefix_recognized(raw, expected_number):
    normalized = _normalize_question_line(raw)
    match = QUESTION_START_RE.match(normalized)
    assert match is not None, f"行未被识别为一级大题起点：{raw!r} -> {normalized!r}"
    assert match.group(1).replace("．", ".") == expected_number


# ---- 负例：不应误识别为一级大题 ----

NEGATIVE_PREFIXES = [
    "(3) 求极限",          # 子题编号 (3)
    "-3.5",                # 负数/小数
    "当 x=3. 时计算",       # 普通正文里的数字
    "x^2",                 # 公式
    "① 求极限",            # 带圈数字
    "3 计算下列极限",       # 无噪声、无分隔符的普通文本
]


@pytest.mark.parametrize("raw", NEGATIVE_PREFIXES)
def test_no_false_positive_question_start(raw):
    normalized = _normalize_question_line(raw)
    # 既不应被改写，也不应被识别为一级大题起点。
    assert normalized == raw
    assert QUESTION_START_RE.match(normalized) is None
