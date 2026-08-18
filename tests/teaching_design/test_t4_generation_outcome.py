from calculus_agent.agent.conversation_state import (
    DatabasePendingReplacementStore,
)
from calculus_agent.agent.schemas import (
    GenerationPlanPatch,
    QuestionTypeRequirement,
)
from calculus_agent.agent.services.generation import GenerationService
from calculus_agent.models import (
    CurriculumNode,
    KnowledgeNode,
    Paper,
    Question,
    QuestionDraft,
    QuestionKnowledgeLink,
    QuestionProfile,
    Textbook,
)
from calculus_agent.teaching_design.service import TeachingDesignService


def _seed_bad_solution_question(session):
    textbook = Textbook(
        name="T4生成结果测试教材",
        edition="T4",
        is_active=True,
    )
    session.add(textbook)
    session.flush()

    chapter = CurriculumNode(
        textbook_id=textbook.id,
        parent_id=None,
        node_type="chapter",
        code="1",
        title="第一章",
        sort_order=100,
        review_status="approved",
    )
    session.add(chapter)
    session.flush()

    section = CurriculumNode(
        textbook_id=textbook.id,
        parent_id=chapter.id,
        node_type="section",
        code="1.1",
        title="1.1 极限",
        sort_order=101,
        review_status="approved",
    )
    session.add(section)
    session.flush()

    knowledge = KnowledgeNode(
        curriculum_node_id=section.id,
        node_type="concept",
        name="T4极限",
        normalized_name="t4极限",
        source_type="directory",
        confidence=1.0,
        review_status="approved",
    )
    session.add(knowledge)
    session.flush()

    draft = QuestionDraft(
        source_name="ocr_import",
        source_item_id="t4-bad-solution-1",
        variant=1,
        subject="高等数学",
        question_type="计算题",
        question_text="计算一个极限。",
        reference_answers_json=["1"],
        normalized_fingerprint="4" * 64,
        status="approved",
    )
    session.add(draft)
    session.flush()

    question = Question(
        draft_id=draft.id,
        curriculum_chapter_id=chapter.id,
        question_text=draft.question_text,
        question_type="计算题",
        final_answer="1",
        solution_json={},
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
    session.add(
        QuestionProfile(
            question_id=question.id,
            profile_version=1,
            difficulty=3,
            estimated_time_min=8,
            reasoning_depth=3,
            calculation_load=3,
            knowledge_depth=3,
            comprehensive_level=3,
            confidence=0.95,
            profile_source="human",
            profile_status="approved",
            reason="T4 generation outcome fixture",
        )
    )
    session.flush()


def _confirmed_design(session, conversation_id: str):
    service = TeachingDesignService(session)
    design = service.create(
        owner_key="local_teacher",
        conversation_id=conversation_id,
        content={
            "title": "第一章测试",
            "objective": "完成第一章阶段测评。",
            "scope_names": ["第一章"],
            "assessment_plan": {
                "paper_type": "chapter_test",
                "total_score": 10,
                "difficulty": "normal",
            },
        },
        run_id="run-create",
        source_user_message="创建第一章测试",
    )
    return service.confirm(
        design.version_id,
        conversation_id=conversation_id,
        run_id="run-confirm",
    )


def test_post_generation_validation_failure_is_not_generation_success(session):
    _seed_bad_solution_question(session)
    conversation_id = "t4-validation-outcome"
    design = _confirmed_design(session, conversation_id)
    store = DatabasePendingReplacementStore(session)

    service = GenerationService(
        session=session,
        store=store,
        conversation_id=conversation_id,
        teaching_design_version_id=design.version_id,
    )
    preview = service.preview(
        GenerationPlanPatch(
            paper_type="chapter_test",
            scope_names=["第一章"],
            total_score=10,
            difficulty_level="normal",
            question_type_requirements=[
                QuestionTypeRequirement(
                    question_type="计算题",
                    count=1,
                    score_each=10,
                    total_score=10,
                )
            ],
        )
    )
    assert preview.ok is True

    result = service.confirm()

    assert result.ok is False
    assert result.paper_id is not None
    assert result.validation_status == "failed"
    assert result.validation_report is not None
    assert "paper_validation_failed" in result.blocking_errors
    assert {
        item.code
        for item in result.validation_report.violations
    } >= {"SOLUTION_MISSING"}

    paper = session.get(Paper, str(result.paper_id))
    assert paper is not None
    assert paper.validation_status == "failed"
    assert paper.teaching_design_version_id == design.version_id
    assert store.get_generation(conversation_id) is not None
