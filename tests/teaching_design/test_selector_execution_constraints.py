from calculus_agent.agent.schemas import GenerationConstraints, PaperGenerationRequest
from calculus_agent.models import (
    KnowledgeNode,
    Question,
    QuestionDraft,
    QuestionKnowledgeLink,
    QuestionProfile,
)
from calculus_agent.papers.selector import compose_paper
from calculus_agent.schemas import PaperBlueprint


def _question_with_profile(
    session,
    *,
    number: int,
    knowledge: KnowledgeNode,
    time_min: int,
    calculation_load: int,
    reasoning_depth: int = 3,
    knowledge_depth: int = 3,
    comprehensive_level: int = 3,
):
    draft = QuestionDraft(
        source_name="ocr_import",
        source_item_id=f"t2-{number}",
        variant=1,
        subject="高等数学",
        question_type="计算题",
        question_text=f"测试题 {number}",
        reference_answers_json=[str(number)],
        normalized_fingerprint=str(number).zfill(64),
        status="approved",
    )
    session.add(draft)
    session.flush()
    question = Question(
        draft_id=draft.id,
        question_text=draft.question_text,
        question_type="计算题",
        final_answer=str(number),
        solution_json={"solution_steps": [f"解析 {number}"]},
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
            reasoning_depth=reasoning_depth,
            calculation_load=calculation_load,
            knowledge_depth=knowledge_depth,
            comprehensive_level=comprehensive_level,
            confidence=0.95,
            profile_source="human",
            profile_status="approved",
            reason="T2 selector fixture",
        )
    )
    session.flush()
    return question


def test_duration_is_a_real_cp_sat_range_constraint(session):
    knowledge = KnowledgeNode(
        node_type="concept",
        name="极限",
        normalized_name="极限",
        review_status="approved",
    )
    session.add(knowledge)
    session.flush()

    q5 = _question_with_profile(
        session,
        number=5,
        knowledge=knowledge,
        time_min=5,
        calculation_load=3,
    )
    q10 = _question_with_profile(
        session,
        number=10,
        knowledge=knowledge,
        time_min=10,
        calculation_load=3,
    )
    q20 = _question_with_profile(
        session,
        number=20,
        knowledge=knowledge,
        time_min=20,
        calculation_load=3,
    )

    request = PaperGenerationRequest(
        blueprint=PaperBlueprint(
            total_questions=2,
            total_score=20,
            question_type_counts={"计算题": 2},
            seed=42,
        ),
        constraints=GenerationConstraints(
            target_duration_min=15,
            duration_tolerance_min=0,
        ),
    )

    result = compose_paper(session, request)

    assert result.feasible is True
    assert {item.question_id for item in result.items} == {q5.id, q10.id}
    duration_check = next(
        item for item in result.constraints
        if item.name == "预计时长"
    )
    assert duration_check.actual == 15
    assert duration_check.satisfied is True
    assert q20.id not in {item.question_id for item in result.items}


def test_ability_weight_is_a_soft_cp_sat_objective(session):
    knowledge = KnowledgeNode(
        node_type="concept",
        name="导数",
        normalized_name="导数",
        review_status="approved",
    )
    session.add(knowledge)
    session.flush()

    low = _question_with_profile(
        session,
        number=101,
        knowledge=knowledge,
        time_min=8,
        calculation_load=1,
    )
    high = _question_with_profile(
        session,
        number=102,
        knowledge=knowledge,
        time_min=8,
        calculation_load=5,
    )

    request = PaperGenerationRequest(
        blueprint=PaperBlueprint(
            total_questions=1,
            total_score=10,
            question_type_counts={"计算题": 1},
            seed=42,
        ),
        constraints=GenerationConstraints(
            ability_weights={"calculation": 100},
        ),
    )

    result = compose_paper(session, request)

    assert result.feasible is True
    assert result.items[0].question_id == high.id
    assert result.items[0].calculation_load == 5
    assert result.items[0].question_id != low.id


def test_knowledge_priority_weight_prefers_higher_priority_knowledge(session):
    high_k = KnowledgeNode(
        node_type="concept",
        name="洛必达法则",
        normalized_name="洛必达法则",
        review_status="approved",
    )
    low_k = KnowledgeNode(
        node_type="concept",
        name="连续性",
        normalized_name="连续性",
        review_status="approved",
    )
    session.add_all([high_k, low_k])
    session.flush()

    high = _question_with_profile(
        session,
        number=201,
        knowledge=high_k,
        time_min=8,
        calculation_load=3,
    )
    _question_with_profile(
        session,
        number=202,
        knowledge=low_k,
        time_min=8,
        calculation_load=3,
    )

    request = PaperGenerationRequest(
        blueprint=PaperBlueprint(
            total_questions=1,
            total_score=10,
            question_type_counts={"计算题": 1},
            soft_knowledge_preferences=["洛必达法则", "连续性"],
            seed=42,
        ),
        constraints=GenerationConstraints(
            knowledge_priority_weights={
                "洛必达法则": 5,
                "连续性": 1,
            },
        ),
    )

    result = compose_paper(session, request)

    assert result.feasible is True
    assert result.items[0].question_id == high.id
