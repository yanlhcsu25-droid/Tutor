from calculus_agent.agent.schemas import GenerationConstraints
from calculus_agent.models import (
    KnowledgeNode,
    Question,
    QuestionDraft,
    QuestionKnowledgeLink,
    QuestionProfile,
)
from calculus_agent.papers.persistence import create_paper_draft
from calculus_agent.papers.selector import compose_paper
from calculus_agent.papers.workflow import validate_paper
from calculus_agent.schemas import PaperBlueprint


def _profiled_question(session, *, number: int, knowledge, time_min: int):
    draft = QuestionDraft(
        source_name="ocr_import",
        source_item_id=f"validation-{number}",
        variant=1,
        subject="高等数学",
        question_type="计算题",
        question_text=f"验证题 {number}",
        reference_answers_json=[str(number)],
        normalized_fingerprint=("9" + str(number)).zfill(64),
        status="approved",
    )
    session.add(draft)
    session.flush()
    question = Question(
        draft_id=draft.id,
        question_text=draft.question_text,
        question_type="计算题",
        final_answer=str(number),
        solution_json={"solution_steps": ["完整解析"]},
        verification_status="verified",
        review_status="approved",
    )
    session.add(question)
    session.flush()
    session.add(
        QuestionKnowledgeLink(
            question_id=question.id,
            knowledge_node_id=knowledge.id,
            relation_type="primary_concept",
        )
    )
    session.add(
        QuestionProfile(
            question_id=question.id,
            profile_version=1,
            difficulty=3,
            estimated_time_min=time_min,
            reasoning_depth=3,
            calculation_load=3,
            knowledge_depth=3,
            comprehensive_level=3,
            confidence=0.95,
            profile_source="human",
            profile_status="approved",
            reason="validation fixture",
        )
    )
    session.flush()
    return question


def test_persisted_generation_metadata_is_revalidated_for_duration(session):
    knowledge = KnowledgeNode(
        node_type="concept",
        name="极限",
        normalized_name="极限",
        review_status="approved",
    )
    session.add(knowledge)
    session.flush()

    _profiled_question(
        session,
        number=1,
        knowledge=knowledge,
        time_min=10,
    )
    _profiled_question(
        session,
        number=2,
        knowledge=knowledge,
        time_min=10,
    )

    blueprint = PaperBlueprint(
        total_questions=2,
        total_score=20,
        question_type_counts={"计算题": 2},
        seed=42,
    )
    constraints = GenerationConstraints(
        target_duration_min=20,
        duration_tolerance_min=0,
    )

    preview = compose_paper(
        session,
        __import__(
            "calculus_agent.agent.schemas",
            fromlist=["PaperGenerationRequest"],
        ).PaperGenerationRequest(
            blueprint=blueprint,
            constraints=constraints,
        ),
    )
    assert preview.feasible is True

    persisted = create_paper_draft(
        session,
        preview,
        blueprint,
        generation_constraints=constraints,
    )
    assert persisted.ok is True

    report = validate_paper(session, persisted.paper_id)

    assert report.passed is True
    assert not any(
        item.code.startswith("DURATION_")
        for item in report.violations
    )
