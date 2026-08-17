from datetime import UTC, datetime

from sqlalchemy import select

import calculus_agent.agent.tools.analysis_tools as analysis_tools
import calculus_agent.papers.workflow as workflow
from calculus_agent.agent.tools.analysis_tools import (
    confirm_adjust_paper,
    preview_adjust_paper,
)
from calculus_agent.agent.tools.version_tools import run_version_operation
from calculus_agent.agent.version_parser import VersionOperationIntent
from calculus_agent.models import (
    Paper,
    PaperBlueprintRecord,
    PaperItem,
    Question,
    QuestionDraft,
)
from calculus_agent.papers.addressing import resolve_section_item_from_items
from calculus_agent.schemas import ValidationReportRead


def _fake_validate_paper(session, paper_id: str) -> ValidationReportRead:
    """Keep this test focused on version/address semantics, not blueprint validation."""
    paper = session.get(Paper, paper_id)
    paper.status = "passed"
    paper.validation_status = "passed"
    session.flush()
    return ValidationReportRead(
        id=f"fake-{paper_id}",
        paper_id=paper_id,
        passed=True,
        violations=[],
        created_at=datetime.now(UTC),
    )


def _question(
    session,
    *,
    source_item_id: str,
    text: str,
    question_type: str,
) -> Question:
    draft = QuestionDraft(
        source_name="test",
        source_item_id=source_item_id,
        variant=1,
        subject="高等数学",
        question_type=question_type,
        question_text=text,
        normalized_fingerprint=f"fp-{source_item_id}",
        status="approved",
    )
    session.add(draft)
    session.flush()

    question = Question(
        draft_id=draft.id,
        question_text=text,
        question_type=question_type,
        verification_status="manual_verified",
        review_status="approved",
        is_active=True,
    )
    session.add(question)
    session.flush()
    return question


def _items(session, paper_id: str) -> list[PaperItem]:
    return list(
        session.scalars(
            select(PaperItem)
            .where(PaperItem.paper_id == paper_id)
            .order_by(PaperItem.position)
        )
    )


def _question_ids_by_text(session, items: list[PaperItem]) -> list[str]:
    return [
        session.get(Question, item.question_id).question_text
        for item in items
    ]


def _make_v1(session):
    blueprint = PaperBlueprintRecord(
        title="delete integration",
        blueprint_json={
            "title": "delete integration",
            "total_questions": 5,
            "total_score": 25,
            "question_type_counts": {
                "选择题": 4,
                "填空题": 1,
            },
        },
        status="used",
    )
    session.add(blueprint)
    session.flush()

    paper = Paper(
        blueprint_id=blueprint.id,
        root_paper_id=None,
        parent_version_id=None,
        version=1,
        status="passed",
        title="v1",
        total_score=25,
        validation_status="passed",
    )
    session.add(paper)
    session.flush()

    # Real create_paper() establishes this invariant.
    paper.root_paper_id = paper.id
    session.flush()

    specs = [
        ("A", "A", "选择题", 5),
        ("B", "B", "选择题", 5),
        ("C", "C", "选择题", 5),
        ("D", "D", "选择题", 5),
        ("F1", "F1", "填空题", 5),
    ]
    questions = {}

    for position, (sid, text, qtype, score) in enumerate(specs, 1):
        question = _question(
            session,
            source_item_id=sid,
            text=text,
            question_type=qtype,
        )
        questions[sid] = question
        session.add(
            PaperItem(
                paper_id=paper.id,
                question_id=question.id,
                section=qtype,
                position=position,
                score=score,
                locked=False,
            )
        )

    session.flush()
    return paper, questions


def test_confirm_delete_creates_child_and_rebinds_section_number(
    session,
    monkeypatch,
):
    monkeypatch.setattr(
        analysis_tools,
        "validate_paper",
        _fake_validate_paper,
    )

    v1, questions = _make_v1(session)

    preview = preview_adjust_paper(
        session,
        paper_id=v1.id,
        remove_positions=[2],
        target_total_score=None,
    )

    assert preview.ok is True
    assert preview.plan is not None
    assert preview.plan.before_summary.score_total == 25
    assert preview.plan.after_summary.score_total == 20
    assert preview.plan.after_summary.question_count == 4
    assert [op.type for op in preview.plan.operations] == [
        "remove_question"
    ]

    confirmed = confirm_adjust_paper(
        session,
        plan_id=preview.plan.plan_id,
        paper_id=v1.id,
        current_version_id=v1.id,
    )

    assert confirmed.ok is True
    assert confirmed.new_version_id is not None
    assert confirmed.new_version_id != v1.id

    v2 = session.get(Paper, confirmed.new_version_id)

    assert v2.version == 2
    assert v2.parent_version_id == v1.id
    assert v2.root_paper_id == v1.id
    assert v2.total_score == 20

    # Old version remains immutable/restorable.
    assert _question_ids_by_text(
        session,
        _items(session, v1.id),
    ) == ["A", "B", "C", "D", "F1"]

    # New version removed B and globally reindexed positions.
    v2_items = _items(session, v2.id)

    assert _question_ids_by_text(
        session,
        v2_items,
    ) == ["A", "C", "D", "F1"]

    assert [item.position for item in v2_items] == [1, 2, 3, 4]

    # Critical CRUD invariant:
    # current "选择题第2题" is C, never stale/deleted B.
    choice_2 = resolve_section_item_from_items(
        v2_items,
        section_type="选择题",
        section_order=2,
    )

    assert choice_2 is not None
    assert choice_2.question_id == questions["C"].id
    assert choice_2.question_id != questions["B"].id


def test_undo_delete_restores_old_section_numbering(
    session,
    monkeypatch,
):
    monkeypatch.setattr(
        analysis_tools,
        "validate_paper",
        _fake_validate_paper,
    )
    monkeypatch.setattr(
        workflow,
        "validate_paper",
        _fake_validate_paper,
    )

    v1, questions = _make_v1(session)

    preview = preview_adjust_paper(
        session,
        paper_id=v1.id,
        remove_positions=[2],
        target_total_score=None,
    )
    assert preview.ok is True
    assert preview.plan is not None

    confirmed = confirm_adjust_paper(
        session,
        plan_id=preview.plan.plan_id,
        paper_id=v1.id,
        current_version_id=v1.id,
    )
    assert confirmed.ok is True
    v2_id = confirmed.new_version_id

    undone = run_version_operation(
        session,
        paper_id=v2_id,
        version_id=v2_id,
        intent=VersionOperationIntent(action="undo"),
    )

    assert undone.ok is True
    assert undone.current_version_id is not None
    assert undone.current_version_id != v1.id
    assert undone.current_version_id != v2_id

    restored_items = _items(
        session,
        undone.current_version_id,
    )

    assert _question_ids_by_text(
        session,
        restored_items,
    ) == ["A", "B", "C", "D", "F1"]

    choice_2 = resolve_section_item_from_items(
        restored_items,
        section_type="选择题",
        section_order=2,
    )

    assert choice_2 is not None
    assert choice_2.question_id == questions["B"].id


def test_restore_version_one_restores_old_section_numbering(
    session,
    monkeypatch,
):
    monkeypatch.setattr(
        analysis_tools,
        "validate_paper",
        _fake_validate_paper,
    )
    monkeypatch.setattr(
        workflow,
        "validate_paper",
        _fake_validate_paper,
    )

    v1, questions = _make_v1(session)

    preview = preview_adjust_paper(
        session,
        paper_id=v1.id,
        remove_positions=[2],
        target_total_score=None,
    )
    assert preview.ok is True
    assert preview.plan is not None

    confirmed = confirm_adjust_paper(
        session,
        plan_id=preview.plan.plan_id,
        paper_id=v1.id,
        current_version_id=v1.id,
    )
    assert confirmed.ok is True
    v2_id = confirmed.new_version_id

    restored = run_version_operation(
        session,
        paper_id=v2_id,
        version_id=v2_id,
        intent=VersionOperationIntent(
            action="restore",
            target_version=1,
        ),
    )

    assert restored.ok is True
    assert restored.current_version_id is not None

    restored_items = _items(
        session,
        restored.current_version_id,
    )

    assert _question_ids_by_text(
        session,
        restored_items,
    ) == ["A", "B", "C", "D", "F1"]

    choice_2 = resolve_section_item_from_items(
        restored_items,
        section_type="选择题",
        section_order=2,
    )

    assert choice_2 is not None
    assert choice_2.question_id == questions["B"].id
