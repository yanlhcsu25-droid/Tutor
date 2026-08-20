from calculus_agent.teaching_design.generation_adapter import (
    project_confirmed_design,
)
from calculus_agent.teaching_design.schemas import TeachingDesignRead
from calculus_agent.teaching_design.service import TeachingDesignService


def _confirmed_projection(*, knowledge_count: int, assessment_plan: dict | None = None):
    design = TeachingDesignRead(
        version_id="regression",
        design_key="regression",
        owner_key="teacher",
        source_conversation_id="regression",
        parent_version_id=None,
        version=1,
        status="confirmed",
        created_at="now",
        content={
            "title": "回归",
            "objective": "回归",
            "scope_names": ["第一章"],
            "knowledge_plan": [
                {"name": f"知识点{i}", "role": "required"}
                for i in range(knowledge_count)
            ],
            "assessment_plan": assessment_plan or {},
        },
    )
    return project_confirmed_design(design)


def test_question_count_never_comes_from_teaching_required_knowledge():
    for count in (2, 4, 8):
        projection = _confirmed_projection(knowledge_count=count)
        assert projection.payload["question_count"] == 10
        assert "required_knowledge_names" not in projection.payload
        assert "required_knowledge_coverage" not in projection.hard_constraints


def test_explicit_assessment_structure_and_coverage_are_hard():
    projection = _confirmed_projection(
        knowledge_count=4,
        assessment_plan={
            "question_count": 6,
            "assessment_required_knowledge": ["函数的极限"],
        },
    )
    assert projection.payload["question_count"] == 6
    assert "required_knowledge_coverage" in projection.hard_constraints
    assert projection.payload["required_knowledge_names"] == ["函数的极限"]

    projection = _confirmed_projection(
        knowledge_count=4,
        assessment_plan={
            "question_type_requirements": [
                {"question_type": "选择题", "count": 2},
                {"question_type": "填空题", "count": 3},
                {"question_type": "计算题", "count": 4},
            ],
        },
    )
    assert projection.payload["question_count"] == 9


def test_confirmed_design_compiles_hard_bounded_soft_and_advisory_constraints(session):
    service = TeachingDesignService(session)
    design = service.create(
        owner_key="local_teacher",
        conversation_id="compiler",
        content={
            "title": "第一到第三章期中复习",
            "objective": "完成阶段复习并测评。",
            "scope_names": ["第一章", "第二章", "第三章"],
            "knowledge_plan": [
                {
                    "name": "极限",
                    "role": "required",
                    "priority": 5,
                },
                {
                    "name": "导数",
                    "role": "optional",
                    "priority": 4,
                },
                {
                    "name": "函数基础",
                    "role": "prerequisite",
                    "priority": 2,
                },
            ],
            "assessment_plan": {
                "paper_type": "midterm",
                "total_score": 100,
                "duration_minutes": 90,
                "difficulty": "hard",
                "ability_weights": {
                    "concept_understanding": 25,
                    "calculation": 35,
                    "reasoning": 20,
                    "application": 20,
                },
                "coverage_strategy": "三章尽量均衡，第三章适当加强。",
                "question_design_ideas": ["兼顾概念与综合计算"],
            },
        },
        run_id="run-create",
        source_user_message="设计期中复习。",
    )
    design = service.confirm(
        design.version_id,
        conversation_id="compiler",
        run_id="run-confirm",
    )

    projection = project_confirmed_design(design)

    assert projection.unsupported_design_constraints == []
    assert set(projection.hard_constraints) >= {
        "scope",
        "total_score",
        "hard_duration_range",
    }
    assert "required_knowledge_coverage" not in projection.hard_constraints
    assert projection.payload["question_count"] == 10
    assert set(projection.bounded_constraints) >= {"difficulty_band"}
    assert set(projection.soft_objectives) >= {
        "knowledge_priority",
        "ability_profile",
    }
    assert set(projection.advisory_constraints) >= {
        "prerequisite_knowledge",
        "coverage_strategy",
        "question_design_ideas",
    }

    assert projection.payload["target_duration_min"] == 90
    assert projection.payload["duration_tolerance_min"] == 9
    assert "required_knowledge_names" not in projection.payload
    assert projection.payload["knowledge_preferences"] == ["极限", "导数"]
    assert projection.payload["knowledge_preferences"] == ["极限", "导数"]
    assert projection.payload["knowledge_priority_weights"] == {
        "极限": 5,
        "导数": 4,
    }
    assert projection.payload["ability_weights"]["calculation"] == 35


def test_unknown_ability_dimension_remains_an_explicit_blocker(session):
    service = TeachingDesignService(session)
    design = service.create(
        owner_key="local_teacher",
        conversation_id="unknown-ability",
        content={
            "title": "测试",
            "objective": "测试",
            "scope_names": ["第一章"],
            "assessment_plan": {
                "ability_weights": {
                    "memorization": 100,
                },
            },
        },
        run_id="run-create",
        source_user_message="测试",
    )
    design = service.confirm(
        design.version_id,
        conversation_id="unknown-ability",
        run_id="run-confirm",
    )

    projection = project_confirmed_design(design)

    assert projection.unsupported_design_constraints == [
        "ability_weight:memorization"
    ]
