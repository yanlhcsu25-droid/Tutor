import pytest

from calculus_agent.teaching_design.generation_adapter import (
    TeachingDesignGenerationError,
    project_confirmed_design,
)
from calculus_agent.teaching_design.service import (
    StaleTeachingDesignError,
    TeachingDesignService,
)
from calculus_agent.teaching_design.schemas import TeachingDesignContent


def _content(title: str = "第一到第三章期中复习") -> TeachingDesignContent:
    return TeachingDesignContent(
        title=title,
        objective="完成第一到第三章的期中复习与测评。",
        scope_names=["第一章", "第二章", "第三章"],
        knowledge_plan=[
            {
                "name": "极限",
                "role": "required",
                "priority": 4,
                "introduction": "复习极限的基本概念与计算。",
            }
        ],
        teaching_priorities=["三章核心知识尽量均衡覆盖"],
        assessment_plan={
            "paper_type": "midterm",
            "total_score": 100,
            "duration_minutes": 90,
            "difficulty": "hard",
            "ability_weights": {
                "concept_understanding": 25,
                "calculation": 40,
                "application": 35,
            },
            "question_design_ideas": ["兼顾概念辨析与综合计算"],
        },
        lecture_plan={
            "structure": ["知识框架", "重点讲解", "典型例题", "复习总结"],
        },
    )


def test_revision_is_immutable_and_traceable(session):
    service = TeachingDesignService(session)

    v1 = service.create(
        owner_key="local-teacher",
        conversation_id="conv-1",
        content=_content(),
        run_id="run-create",
        source_user_message="帮我设计第一到第三章的期中复习。",
    )

    v2 = service.revise(
        v1.version_id,
        {
            "teaching_priorities": [
                "第三章重点加强",
                "第一、二章保持核心覆盖",
            ]
        },
        conversation_id="conv-1",
        run_id="run-revise",
        source_user_message="第三章再重点一点。",
        change_reason="teacher_requested_more_chapter_3",
    )

    persisted_v1 = service.get(v1.version_id)

    assert v1.version == 1
    assert v2.version == 2
    assert v2.parent_version_id == v1.version_id
    assert v2.created_by_run_id == "run-revise"
    assert v2.source_user_message == "第三章再重点一点。"
    assert v2.change_reason == "teacher_requested_more_chapter_3"

    # v1 content is historical truth and was not overwritten.
    assert persisted_v1.content.teaching_priorities == [
        "三章核心知识尽量均衡覆盖"
    ]
    assert persisted_v1.status == "superseded"
    assert persisted_v1.superseded_by_version_id == v2.version_id


def test_confirmed_version_remains_effective_until_new_version_is_confirmed(session):
    service = TeachingDesignService(session)

    v1 = service.create(
        owner_key="local-teacher",
        conversation_id="conv-1",
        content=_content(),
        run_id="run-1",
        source_user_message="设计期中复习。",
    )
    v1 = service.confirm(
        v1.version_id,
        conversation_id="conv-1",
        run_id="run-confirm-1",
    )

    v2 = service.revise(
        v1.version_id,
        {"objective": "第三章作为重点，同时保持前两章核心覆盖。"},
        conversation_id="conv-1",
        run_id="run-2",
        source_user_message="第三章再重点一点。",
        change_reason="teacher_changed_emphasis",
    )

    active = service.get_active(
        owner_key="local-teacher",
        conversation_id="conv-1",
    )
    effective = service.get_effective_confirmed(design_key=v1.design_key)

    assert active is not None and active.version_id == v2.version_id
    assert active.status == "awaiting_confirmation"
    assert effective is not None and effective.version_id == v1.version_id
    assert effective.status == "confirmed"

    v2 = service.confirm(
        v2.version_id,
        conversation_id="conv-1",
        run_id="run-confirm-2",
    )

    assert v2.confirmed_by_run_id == "run-confirm-2"
    assert service.get(v1.version_id).status == "superseded"
    assert (
        service.get(v1.version_id).superseded_by_version_id
        == v2.version_id
    )
    assert (
        service.get_effective_confirmed(design_key=v1.design_key).version_id
        == v2.version_id
    )


def test_stale_confirmation_is_rejected(session):
    service = TeachingDesignService(session)

    v1 = service.create(
        owner_key="local-teacher",
        conversation_id="conv-1",
        content=_content(),
        run_id="run-1",
        source_user_message="设计期中复习。",
    )
    v2 = service.revise(
        v1.version_id,
        {"objective": "新版目标"},
        conversation_id="conv-1",
        run_id="run-2",
        source_user_message="改一下。",
        change_reason="teacher_revision",
    )

    with pytest.raises(StaleTeachingDesignError):
        service.confirm(
            v1.version_id,
            conversation_id="conv-1",
            run_id="run-stale-confirm",
        )

    service.confirm(
        v2.version_id,
        conversation_id="conv-1",
        run_id="run-confirm",
    )


def test_recall_can_activate_historical_design_without_changing_status(session):
    service = TeachingDesignService(session)

    old = service.create(
        owner_key="local-teacher",
        conversation_id="conv-old",
        content=_content("第一章极限复习方案"),
        run_id="run-old",
        source_user_message="设计第一章复习。",
    )
    old = service.confirm(
        old.version_id,
        conversation_id="conv-old",
        run_id="run-old-confirm",
    )

    service.create(
        owner_key="local-teacher",
        conversation_id="conv-new",
        content=TeachingDesignContent(
            title="第三章导数复习方案",
            objective="复习第三章导数与微分的核心知识。",
            scope_names=["第三章"],
            knowledge_plan=[
                {
                    "name": "导数",
                    "role": "required",
                    "priority": 4,
                    "introduction": "复习导数的基本概念与计算。",
                }
            ],
            teaching_priorities=["重点掌握导数与微分"],
            assessment_plan={
                "paper_type": "chapter_test",
                "total_score": 100,
                "duration_minutes": 90,
                "difficulty": "normal",
            },
            lecture_plan={
                "structure": ["知识框架", "重点讲解", "典型例题", "复习总结"],
            },
        ),
        run_id="run-new",
        source_user_message="设计第三章复习。",
    )

    candidates = service.recall_candidates(
        owner_key="local-teacher",
        query="第一章",
    )
    assert [x.version_id for x in candidates] == [old.version_id]

    recalled = service.activate_historical_version(
        old.version_id,
        owner_key="local-teacher",
        conversation_id="conv-new",
        run_id="run-recall",
    )

    assert recalled.status == "confirmed"
    active = service.get_active(
        owner_key="local-teacher",
        conversation_id="conv-new",
    )
    assert active is not None
    assert active.version_id == old.version_id


def test_generation_projection_requires_confirmation_and_reports_gaps(session):
    service = TeachingDesignService(session)

    draft = service.create(
        owner_key="local-teacher",
        conversation_id="conv-1",
        content=_content(),
        run_id="run-1",
        source_user_message="设计期中复习。",
    )

    with pytest.raises(TeachingDesignGenerationError):
        project_confirmed_design(draft)

    confirmed = service.confirm(
        draft.version_id,
        conversation_id="conv-1",
        run_id="run-confirm",
    )
    projection = project_confirmed_design(confirmed)

    assert projection.teaching_design_version_id == confirmed.version_id
    assert projection.payload["paper_type"] == "midterm"
    assert projection.payload["scope_names"] == [
        "第一章",
        "第二章",
        "第三章",
    ]
    assert projection.payload["total_score"] == 100
    assert projection.payload["difficulty_level"] == "hard"
    assert projection.payload["target_duration_min"] == 90
    assert projection.payload["duration_tolerance_min"] == 9
    assert projection.payload["required_knowledge_names"] == ["极限"]
    assert projection.payload["ability_weights"] == {
        "concept_understanding": 25,
        "calculation": 40,
        "application": 35,
    }
    assert projection.unsupported_design_constraints == []
    assert "estimated_duration" in projection.bounded_constraints
    assert "ability_profile" in projection.soft_objectives
    assert "question_design_ideas" in projection.advisory_constraints
