from sqlalchemy import select

import calculus_agent.papers.persistence as persistence
from calculus_agent.models import PaperBlueprintRecord
from calculus_agent.papers.persistence import create_paper_draft
from calculus_agent.schemas import (
    PaperBlueprint,
    PaperItemRead,
    PaperPreviewRead,
)
from calculus_agent.teaching_design.service import TeachingDesignService


def _confirmed_design(session):
    service = TeachingDesignService(session)
    design = service.create(
        owner_key="local_teacher",
        conversation_id="t4-savepoint",
        content={
            "title": "事务边界测试",
            "objective": "验证已确认教学设计不会被下游持久化失败回滚。",
            "scope_names": ["第一章"],
        },
        run_id="run-create",
        source_user_message="创建事务边界测试设计",
    )
    return service.confirm(
        design.version_id,
        conversation_id="t4-savepoint",
        run_id="run-confirm",
    )


def test_paper_persistence_failure_rolls_back_only_its_savepoint(
    session,
    monkeypatch,
):
    design = _confirmed_design(session)

    preview = PaperPreviewRead(
        title="事务边界测试卷",
        total_score=10,
        items=[
            PaperItemRead(
                question_id="not-reached-question",
                question_text="不会真正写入的题目",
                question_type="计算题",
                score=10,
            )
        ],
        constraints=[],
        feasible=True,
    )
    blueprint = PaperBlueprint(
        title="事务边界测试卷",
        total_questions=1,
        total_score=10,
        question_type_counts={"计算题": 1},
        seed=42,
    )

    def explode_paper_constructor(**_kwargs):
        raise RuntimeError("forced-paper-persistence-failure")

    monkeypatch.setattr(
        persistence,
        "Paper",
        explode_paper_constructor,
    )

    result = create_paper_draft(
        session,
        preview,
        blueprint,
    )

    assert result.ok is False
    assert result.blocking_errors[0] == "paper_persistence_failed"

    surviving = TeachingDesignService(session).get(design.version_id)
    assert surviving.status == "confirmed"

    assert session.scalar(
        select(PaperBlueprintRecord).where(
            PaperBlueprintRecord.title == "事务边界测试卷"
        )
    ) is None
