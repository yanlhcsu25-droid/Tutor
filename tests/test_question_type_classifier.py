import pytest

from calculus_agent.workbench.ocr import PlacedCandidate, render_drafts, split_page_markdown
from calculus_agent.workbench.question_type_classifier import infer_question_type


@pytest.mark.parametrize(
    ("content", "options", "expected"),
    [
        (
            "在“充分”“必要”和“充分必要”三者中选择一个正确的填入下列空格内",
            {}, "fill_blank",
        ),
        ("已知函数在 x=0 连续，则 a=", {}, "fill_blank"),
        (
            "以下两题中给出了四个结论，从中选出一个正确的结论",
            {"A": "甲", "B": "乙", "C": "丙", "D": "丁"}, "selection",
        ),
        ("计算下列极限：(1) 第一式 (2) 第二式", {}, "calculation"),
        ("根据函数极限的定义，证明极限存在的准则Ⅰ", {}, "proof"),
        ("求函数的间断点并判断其类型", {}, "calculation"),
        ("判断下列陈述的正误，并说明理由", {}, "subjective"),
        ("试举出具有以下性质的函数的例子", {}, "subjective"),
        ("二者相比，哪一个是高阶无穷小", {}, "subjective"),
    ],
)
def test_classification_cases(content, options, expected):
    result = infer_question_type(content, options)
    assert result.question_type == expected
    assert result.needs_review is False
    assert result.reason


def test_unknown_requires_review_and_never_returns_other():
    result = infer_question_type("函数在定义域内具有某种性质。")
    assert result.question_type == "unknown"
    assert result.needs_review is True


def test_ocr_option_formats_are_normalized():
    page = """1. 以下说法正确的是
(A) 甲
（B). 乙
(C) 丙
(D） 丁
"""
    candidate = split_page_markdown(page)[0]
    assert candidate.options == {"A": "甲", "B": "乙", "C": "丙", "D": "丁"}
    assert candidate.question_type == "selection"


def test_calculation_subquestions_keep_business_type():
    page = """1. 计算下列极限：
(1) 第一式
(2) 第二式
"""
    candidate = split_page_markdown(page)[0]
    assert candidate.question_type == "calculation"
    rendered = render_drafts(PlacedCandidate(1, candidate))
    assert len(rendered) == 2
    assert all("## 题型\n\ncalculation" in item.markdown for item in rendered)


def test_dependent_subquestions_do_not_emit_composite_type():
    candidate = split_page_markdown(
        "1. 计算下列各式：\n(1) 第一式\n(2) 利用(1)的结论计算第二式"
    )[0]
    assert candidate.question_type == "calculation"
