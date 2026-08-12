from calculus_agent.papers.latex_renderer import render_paper_latex
from calculus_agent.schemas import PaperItemRead, PaperPreviewRead


def _paper() -> PaperPreviewRead:
    return PaperPreviewRead(
        title="八年级函数测试_卷",
        total_score=20,
        feasible=True,
        constraints=[],
        items=[
            PaperItemRead(
                question_id="q1",
                question_text=r"已知 $y=\frac{1}{2}x+3$，求增长率（占比 50%）。",
                question_type="解答题",
                score=20,
                knowledge=["一次函数"],
                final_answer=r"$\frac{1}{2}$",
                solution_steps=[r"由 $y=kx+b$ 可知 $k=\frac{1}{2}$。"],
            )
        ],
    )


def test_student_latex_escapes_prose_and_preserves_math():
    result = render_paper_latex(_paper(), teacher_version=False)
    assert result.startswith(r"\documentclass")
    assert r"测试\_卷" in result
    assert r"$y=\frac{1}{2}x+3$" in result
    assert r"50\%" in result
    assert "本卷附参考答案与解析" not in result
    assert "解答应写出文字说明、证明过程或演算步骤" in result
    assert r"\Needspace{11.2cm}" in result
    assert "姓名：" in result
    assert "满分：20 分" in result


def test_teacher_latex_contains_solution():
    result = render_paper_latex(_paper(), teacher_version=True)
    question_position = result.index(r"\question{1}")
    answer_position = result.index("答案：")
    assert answer_position > question_position
    assert "本卷附参考答案与解析" in result
    assert r"\clearpage" not in result
    assert "解析：" in result
    assert r"$\frac{1}{2}$" in result
    assert "知识点：一次函数" in result


def test_question_numbers_continue_across_dynamic_sections():
    paper = _paper()
    paper.items.insert(
        0,
        PaperItemRead(
            question_id="q0",
            question_text="选择正确答案。\nA. 甲\nB. 乙\nC. 丙\nD. 丁",
            question_type="选择题",
            score=5,
        ),
    )
    paper.total_score = 25

    result = render_paper_latex(paper, teacher_version=False)

    assert r"\section*{一、选择题" in result
    assert r"\section*{二、解答题" in result
    assert r"\question{1}{5}" in result
    assert r"\question{2}{20}" in result


def test_calculation_and_proof_get_subjective_answer_space():
    paper = _paper()
    paper.items[0].question_type = "calculation"
    calculation = render_paper_latex(paper, teacher_version=False)
    paper.items[0].question_type = "proof"
    proof = render_paper_latex(paper, teacher_version=False)

    assert "计算题" in calculation
    assert r"\vspace{9.0cm}" in calculation
    assert "证明题" in proof
    assert r"\vspace{11.0cm}" in proof


def test_html_entities_are_normalized_before_latex_rendering():
    paper = _paper()
    paper.items[0].solution_steps = [
        r"由 $g&apos;(x)=\frac{x}{1+x}&gt;0$ 可知结论成立。"
    ]

    result = render_paper_latex(paper, teacher_version=True)

    assert "&apos;" not in result
    assert "&prime;" not in result
    assert "&gt;" not in result
    assert r"$g'(x)=\frac{x}{1+x}>0$" in result
