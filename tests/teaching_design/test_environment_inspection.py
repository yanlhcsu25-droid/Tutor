from sqlalchemy import select

from calculus_agent.application.teaching_environment import (
    InspectQuestionBankRequest,
    inspect_curriculum,
    inspect_question_bank,
)
from calculus_agent.models import (
    CurriculumNode,
    KnowledgeNode,
    Question,
    QuestionDraft,
    QuestionKnowledgeLink,
    QuestionProfile,
    Textbook,
)


def _chapter(session, textbook, ordinal: int):
    chapter = CurriculumNode(
        textbook_id=textbook.id,
        parent_id=None,
        node_type="chapter",
        code=str(ordinal),
        title=f"第{ordinal}章",
        sort_order=ordinal * 100,
        review_status="approved",
    )
    session.add(chapter)
    session.flush()

    section = CurriculumNode(
        textbook_id=textbook.id,
        parent_id=chapter.id,
        node_type="section",
        code=f"{ordinal}.1",
        title=f"{ordinal}.1 核心内容",
        sort_order=ordinal * 100 + 1,
        review_status="approved",
    )
    session.add(section)
    session.flush()

    knowledge = KnowledgeNode(
        curriculum_node_id=section.id,
        node_type="concept",
        name=f"知识点{ordinal}",
        normalized_name=f"知识点{ordinal}",
        source_type="directory",
        confidence=1.0,
        review_status="approved",
    )
    session.add(knowledge)
    session.flush()
    return chapter, section, knowledge


def _question(
    session,
    *,
    number: int,
    chapter,
    knowledge,
    question_type: str,
    source_name: str = "ocr_import",
    difficulty: int | None = 3,
):
    draft = QuestionDraft(
        source_name=source_name,
        source_item_id=f"env-{number}",
        variant=1,
        subject="高等数学",
        question_type=question_type,
        question_text=f"环境题 {number}",
        reference_answers_json=[str(number)],
        normalized_fingerprint=str(number).zfill(64),
        status="approved",
    )
    session.add(draft)
    session.flush()

    question = Question(
        draft_id=draft.id,
        curriculum_chapter_id=chapter.id,
        question_text=draft.question_text,
        question_type=question_type,
        final_answer=str(number),
        solution_json={"solution_steps": ["解析"]},
        verification_status="verified",
        review_status="approved",
        is_active=True,
        knowledge_match_status="current",
    )
    session.add(question)
    session.flush()

    session.add(
        QuestionKnowledgeLink(
            question_id=question.id,
            knowledge_node_id=knowledge.id,
            relation_type="primary_concept",
            confidence=1.0,
        )
    )
    if difficulty is not None:
        session.add(
            QuestionProfile(
                question_id=question.id,
                profile_version=1,
                difficulty=difficulty,
                estimated_time_min=5 + number % 5,
                reasoning_depth=3,
                calculation_load=3,
                knowledge_depth=3,
                comprehensive_level=3,
                confidence=0.95,
                profile_source="human",
                profile_status="approved",
                reason="environment fixture",
            )
        )
    session.flush()
    return question


def _fixture(session):
    textbook = Textbook(
        name="高等数学测试教材",
        edition="T3",
        is_active=True,
    )
    session.add(textbook)
    session.flush()

    c1, _s1, k1 = _chapter(session, textbook, 1)
    c2, _s2, k2 = _chapter(session, textbook, 2)

    _question(
        session,
        number=1,
        chapter=c1,
        knowledge=k1,
        question_type="选择题",
        difficulty=2,
    )
    _question(
        session,
        number=2,
        chapter=c1,
        knowledge=k1,
        question_type="计算题",
        difficulty=3,
    )
    _question(
        session,
        number=3,
        chapter=c1,
        knowledge=k1,
        question_type="证明题",
        difficulty=4,
    )
    _question(
        session,
        number=4,
        chapter=c2,
        knowledge=k2,
        question_type="计算题",
        difficulty=None,
    )

    # This row is visible to maintenance flows but must not inflate
    # teacher-facing paper supply.
    _question(
        session,
        number=99,
        chapter=c2,
        knowledge=k2,
        question_type="计算题",
        source_name="CMM-Math",
        difficulty=5,
    )
    return c1, c2


def test_curriculum_inspection_resolves_active_scope_and_returns_traceable_evidence(session):
    _fixture(session)

    result = inspect_curriculum(
        session,
        scope_names=["第一章", "第二章"],
        run_id="run-env",
    )

    assert result.ok is True
    assert result.active_textbook_name == "高等数学测试教材"
    assert len(result.chapters) == 2
    assert result.chapters[0].section_titles
    assert result.chapters[0].knowledge_point_count >= 1
    assert result.evidence_ref is not None
    assert result.evidence_ref.kind == "curriculum_scope"
    assert result.evidence_ref.observed_by_run_id == "run-env"
    assert result.evidence_ref.ref_id.startswith("curriculum_scope:")


def test_question_bank_aggregate_uses_same_teacher_facing_supply_universe(session):
    _fixture(session)

    result = inspect_question_bank(
        session,
        InspectQuestionBankRequest(
            scope_names=["第一章", "第二章"],
            detail_level="aggregate",
        ),
        run_id="run-supply",
    )

    assert result.ok is True
    assert result.total_questions == 4
    assert result.profiled_questions == 3
    assert result.profile_coverage_ratio == 0.75

    by_id = {
        item.chapter_id: item
        for item in result.chapters
    }
    active_chapters = {
        item.code: item.id
        for item in session.query(CurriculumNode).filter(
            CurriculumNode.node_type == "chapter"
        ).all()
    }
    first = by_id[active_chapters["1"]]
    second = by_id[active_chapters["2"]]

    assert first.total_questions == 3
    assert first.question_type_counts == {
        "计算题": 1,
        "证明题": 1,
        "选择题": 1,
    }
    assert second.total_questions == 1
    assert second.profiled_questions == 0

    assert result.evidence_ref is not None
    assert result.evidence_ref.kind == "question_bank_aggregate"
    assert result.evidence_ref.observed_by_run_id == "run-supply"


def test_question_bank_detail_is_bounded_to_requested_chapter(session):
    _fixture(session)

    result = inspect_question_bank(
        session,
        InspectQuestionBankRequest(
            scope_names=["第一章", "第二章"],
            detail_level="chapter_detail",
            chapter_name="第二章",
        ),
        run_id="run-detail",
    )

    assert result.ok is True
    assert len(result.chapters) == 1
    expected = session.scalar(
        select(CurriculumNode).where(
            CurriculumNode.node_type == "chapter",
            CurriculumNode.code == "2",
        )
    )
    assert expected is not None
    assert result.chapters[0].chapter_id == expected.id
    assert result.chapters[0].total_questions == 1
    assert result.chapters[0].top_knowledge_supply[0].total_questions == 1
    assert result.evidence_ref.kind == "question_bank_detail"
