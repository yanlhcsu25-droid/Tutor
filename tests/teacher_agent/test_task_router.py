import pytest
from pydantic import ValidationError

from calculus_agent.agent.task_router import (
    TaskRoute,
    TaskRouter,
    TaskType,
    RoutingState,
    decide_task,
    tool_surface_for,
)


def test_router_classifies_direct_action_generation_request():
    decision = decide_task("第三章出10题测试卷")

    assert decision.source == "deterministic_operation"
    assert decision.route.task_type == TaskType.DIRECT_ACTION
    assert decision.route.confidence >= 0.9
    assert decision.route.clarification_needed is False


def test_router_classifies_teaching_planning_request():
    decision = decide_task("学生极限一直学不好，帮我安排复习")

    assert decision.source == "router"
    assert decision.route.task_type == TaskType.TEACHING_PLANNING
    assert decision.route.clarification_needed is False


def test_router_classifies_information_request_not_knowledge_query():
    decision = decide_task("为什么洛必达法则不能随便用")

    assert decision.source == "router"
    assert decision.route.task_type == TaskType.INFORMATION_REQUEST
    assert decision.route.clarification_needed is False


def test_ambiguous_preparation_request_asks_clarification():
    decision = decide_task("帮我准备一下第三章")

    assert decision.source == "router"
    assert decision.route.task_type == TaskType.TEACHING_PLANNING
    assert decision.route.confidence < 0.7
    assert decision.route.clarification_needed is True
    assert "练习卷" in decision.route.clarification_question
    assert "复习方案" in decision.route.clarification_question


def test_pending_generation_state_overrides_teaching_words():
    decision = decide_task(
        "把选择题改成5道，第三章重点一点",
        state=RoutingState(pending_generation=True),
    )

    assert decision.source == "deterministic_state"
    assert decision.route.task_type == TaskType.DIRECT_ACTION
    assert decision.route.confidence == 1.0


def test_current_paper_operation_overrides_router():
    decision = decide_task(
        "换第三题",
        state=RoutingState(current_paper=True),
    )

    assert decision.source == "deterministic_state"
    assert decision.route.task_type == TaskType.DIRECT_ACTION
    assert decision.route.confidence == 1.0


def test_explicit_paper_operation_routes_to_direct_action_without_state():
    decision = decide_task("删除填空题第2题")

    assert decision.source == "deterministic_operation"
    assert decision.route.task_type == TaskType.DIRECT_ACTION


def test_model_route_is_used_only_without_deterministic_override():
    model_route = TaskRoute(
        task_type=TaskType.INFORMATION_REQUEST,
        confidence=0.99,
        reason="model classified as information",
    )

    overridden = decide_task(
        "确认生成",
        state=RoutingState(pending_generation=True),
        model_route=model_route,
    )
    assert overridden.source == "deterministic_state"
    assert overridden.route.task_type == TaskType.DIRECT_ACTION

    accepted = decide_task("极限的定义是什么", model_route=model_route)
    assert accepted.source == "router"
    assert accepted.route is model_route


@pytest.mark.parametrize(
    ("task_type", "expected_allowed", "unexpected_allowed"),
    [
        (
            TaskType.DIRECT_ACTION,
            {"prepare_generation_plan", "preview_paper_changes", "operate_paper_version"},
            {"inspect_curriculum", "create_teaching_design"},
        ),
        (
            TaskType.TEACHING_PLANNING,
            {"inspect_curriculum", "inspect_question_bank", "create_teaching_design"},
            {"prepare_generation_plan", "confirm_generation", "preview_paper_changes"},
        ),
        (
            TaskType.INFORMATION_REQUEST,
            {"inspect_curriculum", "inspect_question_bank"},
            {"prepare_generation_plan", "confirm_generation", "preview_paper_changes"},
        ),
    ],
)
def test_tool_surface_policy_mapping(task_type, expected_allowed, unexpected_allowed):
    surface = tool_surface_for(task_type)

    assert surface.task_type == task_type
    assert expected_allowed.issubset(set(surface.allowed_tools))
    assert unexpected_allowed.isdisjoint(set(surface.allowed_tools))


def test_teaching_planning_allows_create_teaching_design_after_environment_inspection():
    surface = tool_surface_for(TaskType.TEACHING_PLANNING)

    assert "create_teaching_design" in surface.allowed_tools
    assert "create_teaching_design" not in surface.reserved_tools


def test_router_schema_forbids_business_parameters_and_tool_names():
    with pytest.raises(ValidationError):
        TaskRoute.model_validate(
            {
                "task_type": "DIRECT_ACTION",
                "confidence": 0.9,
                "clarification_needed": False,
                "reason": "invalid extra business fields",
                "chapter": "第三章",
                "question_count": 10,
                "tool_name": "prepare_generation_plan",
            }
        )


def test_clarification_question_required_when_needed():
    with pytest.raises(ValidationError):
        TaskRoute(
            task_type=TaskType.TEACHING_PLANNING,
            confidence=0.55,
            clarification_needed=True,
            reason="ambiguous",
        )


def test_task_router_instance_decide_matches_convenience_wrapper():
    router = TaskRouter()

    assert router.decide("第三章题库有多少道题").route.task_type == TaskType.INFORMATION_REQUEST
    assert decide_task("第三章题库有多少道题").route.task_type == TaskType.INFORMATION_REQUEST
