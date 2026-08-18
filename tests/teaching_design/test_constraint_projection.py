from calculus_agent.teaching_design.generation_adapter import (
    project_confirmed_design,
)
from calculus_agent.teaching_design.service import TeachingDesignService


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
        "required_knowledge_coverage",
    }
    assert set(projection.bounded_constraints) >= {
        "difficulty_band",
        "estimated_duration",
    }
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
    assert projection.payload["required_knowledge_names"] == ["极限"]
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
