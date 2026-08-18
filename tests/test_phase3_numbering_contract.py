from types import SimpleNamespace

import pytest

from calculus_agent.papers.latex_renderer import render_paper_latex
from calculus_agent.papers.workflow import BlueprintStateError, reorder_paper_items
from calculus_agent.models import Paper, PaperBlueprintRecord, PaperItem, Question, QuestionDraft


def _preview(items):
    return SimpleNamespace(
        title="编号测试",
        total_score=sum(item.score for item in items),
        items=items,
    )


def _item(qtype, text, score=5):
    return SimpleNamespace(
        question_type=qtype,
        question_text=text,
        score=score,
        final_answer=None,
        solution_steps=[],
        knowledge=[],
    )


def test_latex_student_numbers_restart_per_section():
    paper = _preview([
        _item("选择题", "A"),
        _item("选择题", "B"),
        _item("填空题", "C"),
        _item("填空题", "D"),
        _item("计算题", "E", 10),
    ])
    latex = render_paper_latex(paper, teacher_version=False)
    assert latex.count(r"\\question{1}") == 3
    assert latex.count(r"\\question{2}") == 2
    assert r"\\question{3}" not in latex


def test_latex_section_titles_follow_actual_appearance():
    paper = _preview([
        _item("填空题", "C"),
        _item("证明题", "P", 10),
    ])
    latex = render_paper_latex(paper, teacher_version=False)
    assert "一、填空题" in latex
    assert "二、证明题" in latex
    assert "三、填空题" not in latex
    assert "四、证明题" not in latex


def _question(session, sid, qtype):
    draft = QuestionDraft(
        source_name="reorder-test",
        source_item_id=sid,
        variant=1,
        subject="高等数学",
        question_type=qtype,
        question_text=sid,
        normalized_fingerprint=f"fp-{sid}",
        status="approved",
    )
    session.add(draft)
    session.flush()
    q = Question(
        draft_id=draft.id,
        question_text=sid,
        question_type=qtype,
        verification_status="manual_verified",
        review_status="approved",
        is_active=True,
    )
    session.add(q)
    session.flush()
    return q


def test_backend_reorder_rejects_cross_section_before_mutation(session):
    blueprint = PaperBlueprintRecord(title="reorder", blueprint_json={}, status="used")
    session.add(blueprint)
    session.flush()
    paper = Paper(
        blueprint_id=blueprint.id,
        version=1,
        status="draft",
        title="reorder",
        total_score=20,
        validation_status="pending",
    )
    session.add(paper)
    session.flush()
    paper.root_paper_id = paper.id

    questions = [
        _question(session, "s1", "选择题"),
        _question(session, "s2", "选择题"),
        _question(session, "f1", "填空题"),
        _question(session, "f2", "填空题"),
    ]
    sections = ["选择题", "选择题", "填空题", "填空题"]
    rows = []
    for position, (question, section) in enumerate(zip(questions, sections), 1):
        row = PaperItem(
            paper_id=paper.id,
            question_id=question.id,
            section=section,
            position=position,
            score=5,
            locked=False,
        )
        session.add(row)
        rows.append(row)
    session.flush()

    with pytest.raises(BlueprintStateError, match="不能跨题型"):
        reorder_paper_items(
            session,
            paper.id,
            [rows[0].id, rows[2].id, rows[1].id, rows[3].id],
        )
    assert [row.position for row in rows] == [1, 2, 3, 4]
