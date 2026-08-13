from calculus_agent.papers.latex_renderer import format_question_display, render_paper_latex
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
    question_position = result.index(r"\ExamQuestion{20}")
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


def test_teacher_needspace_is_not_based_on_student_answer_space():
    paper = _paper()
    paper.items[0].question_type = "calculation"
    paper.items[0].score = 13

    result = render_paper_latex(paper, teacher_version=True)

    assert r"\Needspace" not in result
    assert r"\ExamQuestion{13}" in result
    student = render_paper_latex(paper, teacher_version=False)
    assert r"\Needspace{8.8cm}" in student
    assert r"\vspace{6.6cm}" in student


def test_teacher_solution_prefixes_are_stripped_only_at_step_start():
    paper = _paper()
    paper.items[0].solution_steps = ["解析：移项得 x=3。", "解: 合并同类项。", "证明：结论成立。", "普通正文。"]

    result = render_paper_latex(paper, teacher_version=True)

    assert "\\item 移项得 x=3。" in result
    assert "\\item 合并同类项。" in result
    assert "\\item 结论成立。" in result
    assert "\\item 普通正文。" in result
    assert "\\item 解析：" not in result


def test_teacher_sections_are_continuously_numbered_in_rendered_output():
    paper = _paper()
    paper.items = [
        paper.items[0].model_copy(update={"question_type": question_type, "question_id": question_type})
        for question_type in ("选择题", "填空题", "计算题", "证明题")
    ]
    result = render_paper_latex(paper, teacher_version=True)

    titles = [r"\ExamSection{一}{选择题", r"\ExamSection{二}{填空题", r"\ExamSection{三}{计算题", r"\ExamSection{四}{证明题"]
    positions = [result.index(title) for title in titles]
    assert positions == sorted(positions)


def test_teacher_template_owns_visual_commands_and_normalizes_options():
    paper = _paper()
    paper.items[0].question_type = "选择题"
    paper.items[0].question_text = "选出正确答案。\nA. $x=1$\nB. $x=2$"

    result = render_paper_latex(paper, teacher_version=True)

    assert "%__PAPER_BODY__%" not in result
    assert r"\ExamSection{一}{选择题" in result
    assert r"\begin{ExamOptions}" in result
    assert r"\item $x=1$" in result
    assert r"\item A. $x=1$" not in result


def test_display_formatter_preserves_math_and_formats_only_explicit_subquestions():
    value = r"""在充分、必要条件中选择：
(1) 数列 $\left\{x_n\right\}$ 有界是收敛的____条件；
(2) $\lim_{x\to x_0}f(x)$ 存在是有界的____条件。
正文中的 (3) 不应被单独识别。"""

    result = format_question_display(value)

    assert "(1) 数列" in result
    assert "(2)" in result
    assert "§EXAM_BLANK§" in result
    assert r"$\left\{x_n\right\}$" in result
    assert "正文中的 (3) 不应被单独识别。" in result


def test_display_formatter_splits_inline_subquestions_after_delimiters_not_math():
    value = r"标题：(1) 条件 $f((1))$；(2) 结论 $g(x)$。证明中条件 (3) 保持原样。"

    result = format_question_display(value)

    assert "标题：" in result
    assert "(1) 条件" in result
    assert "(2) 结论" in result
    assert "条件 (3) 保持原样。" in result
    assert "$f((1))$" in result


def test_real_sufficient_necessary_question_keeps_four_subquestions_in_teacher_latex():
    paper = _paper()
    paper.items[0].question_type = "填空题"
    paper.items[0].question_text = r"""在“充分”“必要”和“充分必要”三者中选择一个正确的填入下列空格内：
(1)数列$\left\{\boldsymbol{x}_{n}\right\}$ 有界是数列$\left\{x_{n}\right\}$ 收敛的____条件，数列$\left\{\boldsymbol{x}_{n}\right\}$ 收敛是数列$\{x_{n}\}$ 有界的____条件；
(2)$f(\dot{\boldsymbol{x}})$ 在$\boldsymbol{x}_{0}$ 的某一去心邻域内有界是$\lim_{x\to x_0}f(x)$ 存在的____条件；
(3)$f(x)$ 在$x_{0}$ 的某一去心邻域内无界是$\lim_{x\to x_0}f(x)=\infty$ 的____条件；
(4)$f(x)$ 当$x\to x_{0}$ 时的右极限存在且相等是极限存在的____条件。"""

    result = render_paper_latex(paper, teacher_version=True)

    assert result.count(r"\ExamSubQuestion{") == 4
    assert result.count(r"\underline{\hspace{2cm}}") == 6  # 5 body blanks + template definition
    assert r"\boldsymbol{x}_{n}" in result
    assert r"\dot{\boldsymbol{x}}" in result
