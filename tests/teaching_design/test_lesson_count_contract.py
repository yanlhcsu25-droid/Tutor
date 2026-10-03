from calculus_agent.agent.tool_adapters.teaching_design import CreateTeachingDesignInput
from calculus_agent.runtime.request_guards import _apply_explicit_opt_in_guards
from calculus_agent.teaching_design.schemas import TeachingDesignContent, TeachingDesignPatch
from calculus_agent.teaching_design.service import TeachingDesignService


def _content(lesson_count=2):
    return TeachingDesignContent(
        title="第一章复习",
        objective="巩固函数与极限",
        scope_names=["第一章 函数与极限"],
        lesson_count=lesson_count,
    )


def test_unrequested_invalid_assessment_scores_do_not_block_design_contract():
    guarded = _apply_explicit_opt_in_guards(
        tool_name="create_teaching_design",
        arguments={"content": {
            "title": "第一章复习",
            "objective": "巩固函数与极限",
            "scope_names": ["第一章 函数与极限"],
            "lesson_count": 2,
            "assessment_plan": {
                "total_score": 100,
                "question_count": 2,
                "question_type_requirements": [{
                    "question_type": "计算题",
                    "count": 2,
                    "score_each": 60,
                }],
            },
        }},
        message="给第一章设计一份两课时复习方案，先让我审核。",
    )

    validated = CreateTeachingDesignInput.model_validate(guarded)

    assert validated.content.lesson_count == 2
    assert validated.content.assessment_plan.question_type_requirements[0].score_each is None


def test_lesson_count_is_persisted_and_revisable(session):
    service = TeachingDesignService(session)
    created = service.create(
        owner_key="teacher",
        conversation_id="lesson-count",
        content=_content(2),
        run_id="run-create",
        source_user_message="安排两课时",
        ready_for_confirmation=True,
    )
    revised = service.revise(
        created.version_id,
        TeachingDesignPatch(lesson_count=3),
        conversation_id="lesson-count",
        run_id="run-revise",
        source_user_message="改成三课时",
        change_reason="teacher_request",
    )

    assert created.content.lesson_count == 2
    assert revised.content.lesson_count == 3
    assert service.get(revised.version_id).content.lesson_count == 3
