from calculus_agent.workbench.markdown_schema import parse_markdown
from calculus_agent.workbench.ocr import (
    PlacedCandidate,
    RawQuestion,
    _candidate_from_raw,
    render_drafts,
)


def _render(raw: str):
    candidate = _candidate_from_raw(RawQuestion("3", raw, "other"))
    assert candidate is not None
    return render_drafts(PlacedCandidate(1, candidate))


def test_two_choice_subquestions_keep_local_options_and_solutions():
    raw = """以下两题中给出了四个结论，从中选出一个正确的结论：
(1) 第一题题干 SENTINEL_Q1
(A) 第一题A SENTINEL_A1
(B) 第一题B
(C) 第一题C
(D) 第一题D
(2) 第二题题干 SENTINEL_Q2
(A) 第二题A SENTINEL_A2
(B) 第二题B
(C) 第二题C
(D) 第二题D
解：
(1) 第一题解析，应选(B)。
(2) 第二题解析，应选(C)。
"""
    drafts = _render(raw)
    assert [item.original_number for item in drafts] == ["3(1)", "3(2)"]
    first, second = [parse_markdown(item.markdown).sections for item in drafts]
    assert "SENTINEL_Q1" in first["题目内容"] and "SENTINEL_A1" in first["题目内容"]
    assert "SENTINEL_Q2" not in first["题目内容"] and "SENTINEL_A2" not in first["题目内容"]
    assert "SENTINEL_Q2" in second["题目内容"] and "SENTINEL_A2" in second["题目内容"]
    assert "SENTINEL_Q1" not in second["题目内容"] and "SENTINEL_A1" not in second["题目内容"]
    assert "第一题解析" in first["参考解答"] and "第二题解析" not in first["参考解答"]
    assert "第二题解析" in second["参考解答"] and "第一题解析" not in second["参考解答"]
    assert all(sections["题型"] == "selection" for sections in (first, second))


def test_internal_statement_numbers_with_one_option_group_remain_one_question():
    raw = """下列命题中选择正确组合：
(1) 命题甲
(2) 命题乙
(3) 命题丙
A. (1)(2)正确
B. (1)(3)正确
C. (2)(3)正确
D. 全部正确
"""
    drafts = _render(raw)
    assert [item.original_number for item in drafts] == ["3"]
    sections = parse_markdown(drafts[0].markdown).sections
    assert all(token in sections["题目内容"] for token in ("命题甲", "命题乙", "命题丙"))


def test_normal_choice_question_remains_single():
    drafts = _render("以下说法正确的是：\nA. 甲\nB. 乙\nC. 丙\nD. 丁")
    assert [item.original_number for item in drafts] == ["3"]


def test_mixed_ocr_option_formats_form_two_local_groups():
    raw = """从中选择正确结论：
(1) 第一题
(A) 甲
（B). 乙
(C) 丙
(D） 丁
(2) 第二题
(A) 子甲
（B). 子乙
(C) 子丙
(D） 子丁
"""
    drafts = _render(raw)
    assert [item.original_number for item in drafts] == ["3(1)", "3(2)"]
    assert "\nA. 甲  \n" in drafts[0].markdown and "\nD. 丁\n" in drafts[0].markdown
    assert "\nA. 子甲  \n" in drafts[1].markdown and "\nD. 子丁\n" in drafts[1].markdown


def test_missing_second_solution_keeps_both_questions_and_flags_review():
    raw = """选择正确结论：
(1) 第一题
A. 甲一
B. 乙一
C. 丙一
D. 丁一
(2) 第二题
A. 甲二
B. 乙二
C. 丙二
D. 丁二
解：
(1) 第一题答案。
"""
    drafts = _render(raw)
    assert [item.original_number for item in drafts] == ["3(1)", "3(2)"]
    first, second = [parse_markdown(item.markdown).sections for item in drafts]
    assert "第一题答案" in first["参考解答"]
    assert second["参考解答"] == ""
    assert first["审核备注"] and second["审核备注"]
