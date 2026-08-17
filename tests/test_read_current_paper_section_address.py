import pytest

from calculus_agent.agent.tools.read_tools import (
    ReadCurrentPaperInput,
    read_current_paper,
)
from calculus_agent.models import (
    Paper,
    PaperBlueprintRecord,
    PaperItem,
    Question,
    QuestionDraft,
)
from calculus_agent.papers.addressing import QuestionAddress


def _draft(source_item_id: str, text: str) -> QuestionDraft:
    return QuestionDraft(
        source_name="test",
        source_item_id=source_item_id,
        variant=1,
        subject="高等数学",
        question_type="计算题",
        question_text=text,
        normalized_fingerprint=f"fp-{source_item_id}",
        status="approved",
    )


def _question(
    draft: QuestionDraft,
    *,
    text: str,
    question_type: str,
) -> Question:
    return Question(
        draft_id=draft.id,
        question_text=text,
        question_type=question_type,
        verification_status="manual_verified",
        review_status="approved",
        is_active=True,
    )


def test_read_current_paper_by_section_address(session):
    blueprint = PaperBlueprintRecord(
        title="test blueprint",
        blueprint_json={},
        status="used",
    )
    session.add(blueprint)
    session.flush()

    paper = Paper(
        blueprint_id=blueprint.id,
        version=1,
        status="draft",
        title="test paper",
        total_score=30,
        validation_status="pending",
    )
    session.add(paper)
    session.flush()

    specs = [
        ("c1", "选择1", "选择题", 5),
        ("c2", "选择2", "选择题", 5),
        ("f1", "填空1", "填空题", 5),
        ("f2", "填空2", "填空题", 5),
        ("p1", "证明1", "证明题", 10),
    ]

    created = []

    for position, (sid, text, qtype, score) in enumerate(specs, 1):
        draft = _draft(sid, text)
        session.add(draft)
        session.flush()

        question = _question(
            draft,
            text=text,
            question_type=qtype,
        )
        session.add(question)
        session.flush()

        item = PaperItem(
            paper_id=paper.id,
            question_id=question.id,
            section=qtype,
            position=position,
            score=score,
            locked=False,
        )
        session.add(item)
        created.append((question, item))

    session.flush()

    result = read_current_paper(
        session,
        current_paper_version_id=paper.id,
        request=ReadCurrentPaperInput(
            addresses=[
                QuestionAddress(
                    section_type="填空题",
                    section_order=2,
                )
            ]
        ),
    )

    assert result.ok is True
    assert len(result.questions) == 1

    question = result.questions[0]

    assert question.content == "填空2"
    assert question.position == 4
    assert question.section_order == 2
    assert question.question_type == "填空题"
    assert question.item_id == created[3][1].id


def test_read_current_paper_keeps_legacy_position_compatibility(session):
    blueprint = PaperBlueprintRecord(
        title="test blueprint",
        blueprint_json={},
        status="used",
    )
    session.add(blueprint)
    session.flush()

    paper = Paper(
        blueprint_id=blueprint.id,
        version=1,
        status="draft",
        title="test paper",
        total_score=10,
        validation_status="pending",
    )
    session.add(paper)
    session.flush()

    draft = _draft("legacy", "旧位置读取")
    session.add(draft)
    session.flush()

    question = _question(
        draft,
        text="旧位置读取",
        question_type="计算题",
    )
    session.add(question)
    session.flush()

    session.add(
        PaperItem(
            paper_id=paper.id,
            question_id=question.id,
            section="计算题",
            position=1,
            score=10,
            locked=False,
        )
    )
    session.flush()

    result = read_current_paper(
        session,
        current_paper_version_id=paper.id,
        request=ReadCurrentPaperInput(
            positions=[1]
        ),
    )

    assert result.ok is True
    assert result.questions[0].content == "旧位置读取"
    assert result.questions[0].section_order == 1


def test_read_input_rejects_mixed_address_modes():
    with pytest.raises(ValueError):
        ReadCurrentPaperInput(
            positions=[1],
            addresses=[
                QuestionAddress(
                    section_type="填空题",
                    section_order=1,
                )
            ],
        )
