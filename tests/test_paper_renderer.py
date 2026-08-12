from calculus_agent.papers.renderer import render_paper_pdf
from calculus_agent.schemas import PaperItemRead, PaperPreviewRead


def test_render_student_and_teacher_pdf():
    paper = PaperPreviewRead(
        title="八年级数学测试卷",
        total_score=10,
        feasible=True,
        constraints=[],
        items=[
            PaperItemRead(
                question_id="q1",
                question_text="若 x+2=5，求 x。",
                question_type="解答题",
                score=10,
                knowledge=["一元一次方程"],
                final_answer="x=3",
                solution_steps=["移项得 x=5-2=3。"],
            )
        ],
    )
    student = render_paper_pdf(paper, teacher_version=False)
    teacher = render_paper_pdf(paper, teacher_version=True)
    assert student.startswith(b"%PDF")
    assert teacher.startswith(b"%PDF")
    assert len(teacher) > len(student)
