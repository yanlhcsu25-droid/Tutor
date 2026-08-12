from calculus_agent.workbench.markdown_schema import (
    fixed_template,
    normalize_selection_option_markdown,
    payload_from_markdown,
    render_preview,
)
from calculus_agent.workbench.models import extract_explicit_choice_answers


QUESTION_ID = "q_" + "1" * 32
SOURCE_ID = "src_" + "2" * 32


def _validate(markdown: str):
    return payload_from_markdown(
        markdown,
        question_id=QUESTION_ID,
        source_file_id=SOURCE_ID,
        ocr_markdown=markdown,
        source_bbox=None,
    )


def test_selection_answer_ignores_formula_letters_and_extracts_only_explicit_b():
    solution = (
        r"解析：$f(0^{-})=\lim_{x\to0^{-}}f(x)=-1$，"
        r"且 $\mathrm{e}^x$、$\frac{f(x)}{x}$ 存在，所以应选(B)."
    )
    markdown = fixed_template(
        "判断间断点类型",
        question_type="selection",
        page_number=1,
        original_number="3(2)",
        options={"A": "可去间断点", "B": "跳跃间断点", "C": "第二类间断点", "D": "连续点"},
        analysis=solution,
    )

    payload, validation = _validate(markdown)

    assert validation.valid
    assert payload is not None
    assert extract_explicit_choice_answers(solution) == {"B"}
    assert "E" not in extract_explicit_choice_answers(solution)
    assert "F" not in extract_explicit_choice_answers(solution)


def test_selection_analysis_without_explicit_answer_does_not_guess_formula_letters():
    markdown = fixed_template(
        "判断",
        question_type="selection",
        page_number=1,
        original_number="1",
        options={"A": "甲", "B": "乙"},
        analysis=r"由 $f(x)=\frac{\mathrm{e}^x-1}{x}$ 可知结论成立。",
    )
    payload, validation = _validate(markdown)
    assert validation.valid
    assert payload is not None


def test_selection_options_serialize_without_markdown_list_bullets():
    markdown = fixed_template(
        "选择正确结论",
        question_type="selection",
        page_number=1,
        original_number="1",
        options={"A": "$f(x)$ 等价", "B": "$f(x)$ 同阶", "C": "高阶", "D": "低阶"},
    )
    assert "\nA. $f(x)$ 等价  \nB. $f(x)$ 同阶" in markdown
    assert "\n- A." not in markdown
    html, _ = render_preview(markdown)
    assert "<ul>" not in html
    assert "<li>" not in html


def test_existing_edited_markdown_removes_only_option_bullets():
    old = """## 题目内容

题干保留 - 普通连字符

- A. 甲
- B. 乙

## 参考解答

- 人工编辑的解析列表保持不变

## 题型

selection
"""
    normalized = normalize_selection_option_markdown(old)
    assert "题干保留 - 普通连字符" in normalized
    assert "\nA. 甲  \nB. 乙  " in normalized
    assert "B. 乙  \n\n## 参考解答" in normalized
    assert "- 人工编辑的解析列表保持不变" in normalized
