from calculus_agent.agent.schemas import GeneratePaperInput
from calculus_agent.agent.tools.paper_tools import (
    build_structured_generation_request,
)
from calculus_agent.models import CurriculumNode, KnowledgeNode, Textbook


def test_structured_generation_compiles_required_knowledge_and_profile_targets(session):
    textbook = Textbook(
        name="高等数学",
        edition="测试版",
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
        sort_order=1,
        review_status="approved",
    )
    session.add(chapter)
    session.flush()

    required = KnowledgeNode(
        curriculum_node_id=chapter.id,
        node_type="concept",
        name="极限",
        normalized_name="极限",
        review_status="approved",
    )
    optional = KnowledgeNode(
        curriculum_node_id=chapter.id,
        node_type="concept",
        name="连续性",
        normalized_name="连续性",
        review_status="approved",
    )
    session.add_all([required, optional])
    session.flush()

    request = GeneratePaperInput(
        paper_type="chapter_test",
        scope_names=["第一章"],
        total_score=100,
        difficulty_level="normal",
        required_knowledge_names=["极限"],
        knowledge_preferences=["极限", "连续性"],
        knowledge_priority_weights={
            "极限": 5,
            "连续性": 2,
        },
        target_duration_min=60,
        duration_tolerance_min=6,
        ability_weights={
            "concept_understanding": 40,
            "calculation": 30,
            "reasoning": 20,
            "application": 10,
        },
    )

    generation_request, warnings, errors, questions = (
        build_structured_generation_request(session, request)
    )

    assert errors == []
    assert questions == []
    assert generation_request is not None
    assert {
        quota.name: quota.count
        for quota in generation_request.blueprint.knowledge_quotas
    } == {"极限": 1}
    assert set(
        generation_request.blueprint.soft_knowledge_preferences
    ) == {"极限", "连续性"}

    constraints = generation_request.constraints
    assert constraints.target_duration_min == 60
    assert constraints.duration_tolerance_min == 6
    assert constraints.knowledge_priority_weights == {
        "极限": 5,
        "连续性": 2,
    }
    assert constraints.ability_weights["concept_understanding"] == 40
    assert required.id in constraints.preferred_knowledge_node_ids
    assert optional.id in constraints.preferred_knowledge_node_ids


def test_missing_assessment_required_knowledge_remains_a_generation_blocker(session):
    session.query(Textbook).update({Textbook.is_active: False})
    textbook = Textbook(name="高等数学", edition="缺失知识点", is_active=True)
    session.add(textbook)
    session.flush()
    chapter = CurriculumNode(
        textbook_id=textbook.id,
        parent_id=None,
        node_type="chapter",
        code="1",
        title="第一章",
        sort_order=1,
        review_status="approved",
    )
    session.add(chapter)
    session.flush()
    session.add(KnowledgeNode(
        curriculum_node_id=chapter.id,
        node_type="concept",
        name="极限",
        normalized_name="极限",
        review_status="approved",
    ))
    session.flush()

    _request, _warnings, errors, _questions = build_structured_generation_request(
        session,
        GeneratePaperInput(
            paper_type="chapter_test",
            scope_names=["第一章"],
            required_knowledge_names=["不存在的必考知识点"],
        ),
    )

    assert errors == ["knowledge_unknown"]
