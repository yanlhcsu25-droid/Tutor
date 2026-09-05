from calculus_agent.agent import (
    build_generation_request, build_paper_blueprint, parse_teacher_requirement,
    resolve_generation_scope,
)
from calculus_agent.agent.schemas import RequirementBlueprint, RequirementPreferences
from calculus_agent.models import CurriculumNode, KnowledgeNode


def test_chapter_requirement_uses_existing_default_structure():
    result = build_paper_blueprint(parse_teacher_requirement("帮我出一套第一章测试卷"))
    assert result.ok is True
    assert result.resolved_scope == ["第一章"]
    assert result.paper_blueprint.total_score == 100
    assert result.paper_blueprint.question_type_counts == {"选择题": 4, "填空题": 2, "计算题": 4}
    assert "scope_not_enforced_by_existing_paper_blueprint" in result.warnings


def test_explicit_total_count_resizes_default_structure_deterministically():
    result = build_paper_blueprint(RequirementBlueprint(
        paper_type="midterm", scope=["第一章", "第二章"],
        question_count=12, total_score=100,
    ))

    assert result.ok
    assert result.paper_blueprint.total_questions == 12
    assert result.paper_blueprint.total_score == 100
    assert result.paper_blueprint.question_type_counts == {
        "选择题": 5, "填空题": 3, "计算题": 4,
    }


def test_homework_uses_five_question_compatible_blueprint():
    result = build_paper_blueprint(parse_teacher_requirement("给第一节出一套课后练习"))
    assert result.ok is True
    assert result.paper_blueprint.total_questions == 5
    assert result.paper_blueprint.question_type_counts == {"计算题": 3, "证明题": 2}
    assert result.paper_blueprint.total_score == 50


def test_midterm_without_scope_is_blocked():
    result = build_paper_blueprint(parse_teacher_requirement("帮我出一套期中考试"))
    assert result.ok is False
    assert result.paper_blueprint is None
    assert result.blocking_errors == ["missing_exam_scope"]


def test_final_without_difficulty_ratio_is_blocked():
    result = build_paper_blueprint(parse_teacher_requirement("帮我出一套期末考试"))
    assert result.ok is False
    assert result.blocking_errors == ["missing_difficulty_ratio"]


def test_soft_preferences_do_not_change_hard_structure():
    requirement = parse_teacher_requirement("第一章测试，简单一点，多一点计算题")
    result = build_paper_blueprint(requirement)
    assert result.ok is True
    assert result.paper_blueprint.total_score == 100
    assert result.paper_blueprint.total_questions == 10
    assert "question_type_preference_is_soft" in result.warnings
    assert "difficulty_preference_approximated" in result.warnings


def test_final_with_ratio_can_build():
    requirement = RequirementBlueprint(
        paper_type="final",
        preferences=RequirementPreferences(difficulty_ratio={"easy": 60, "normal": 30, "hard": 10}),
    )
    result = build_paper_blueprint(requirement)
    assert result.ok is True
    assert result.paper_blueprint.total_score == 100


def test_generation_request_resolves_difficulty_constraints():
    requirement = parse_teacher_requirement("第一章测试，简单一点")
    result = build_paper_blueprint(requirement)
    request, warnings, errors = build_generation_request(requirement, result)
    assert not errors
    assert request.constraints.allowed_difficulty_levels == [1, 2, 3]
    assert request.constraints.preferred_difficulty_levels == [1, 2]
    assert request.constraints.fallback_difficulty_levels == [3]
    assert "scope_not_enforced_by_existing_paper_blueprint" not in warnings


def test_scope_resolution_accepts_chinese_curriculum_codes(session):
    chapter = CurriculumNode(id="chapter-one", node_type="chapter", code="一", title="函数", sort_order=1)
    session.add(chapter)
    session.add(KnowledgeNode(id="knowledge-one", node_type="concept", name="极限", normalized_name="极限", curriculum_node_id=chapter.id))
    session.flush()
    requirement = parse_teacher_requirement("第一章测试")
    built = build_paper_blueprint(requirement)
    request, _, _ = build_generation_request(requirement, built)
    resolved, errors = resolve_generation_scope(session, request)
    assert not errors
    assert resolved.constraints.scope_node_ids == ["knowledge-one"]
