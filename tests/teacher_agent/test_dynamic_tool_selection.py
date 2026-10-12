import json

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from calculus_agent.agent.agent import run_teacher_agent
from calculus_agent.agent.task_router import TaskType
from calculus_agent.agent.tool_registry import AgentExecutionContext, build_agent_tools
from calculus_agent.agent.toolkit import Toolkit
from calculus_agent.runtime.tool_exposure_policy import ToolExposureContext, ToolExposurePolicy
from calculus_agent.runtime.tool_selection import compare_tool_exposure, task_chain_tools


ALL_TOOLS = frozenset({
    "read_paper", "analyze_paper", "prepare_generation_plan",
    "prepare_reinforcement_plan", "confirm_generation", "preview_paper_changes",
    "confirm_paper_changes", "discard_pending_plan", "operate_paper_version",
    "retrieve_curriculum_candidates", "inspect_curriculum", "inspect_question_bank",
    "read_active_teaching_design", "create_teaching_design", "revise_teaching_design",
    "confirm_teaching_design", "discard_teaching_design",
    "search_teaching_design_history", "activate_teaching_design",
    "select_teaching_scope", "prepare_teaching_planning_draft",
})
ENVIRONMENT = (
    "retrieve_curriculum_candidates", "inspect_curriculum", "inspect_question_bank",
)


def _context(**changes):
    values = dict(
        tool_names=ALL_TOOLS,
        task_type=TaskType.INFORMATION_REQUEST,
        message="查看当前试卷有多少道题",
        has_current_paper=True,
        pending_generation=False,
        pending_paper_change=False,
        pending_replacement=False,
        legacy_teaching_design_active=False,
        design_tool_names=(),
        environment_tool_names=ENVIRONMENT,
        pending_teaching_design_action=None,
        teaching_design_artifact_requested=False,
        has_explicit_curriculum_scope=False,
        route_source="deterministic_state",
        route_confidence=1.0,
        clarification_needed=False,
    )
    values.update(changes)
    return ToolExposureContext(**values)


def test_read_only_current_paper_hides_write_tools():
    policy = ToolExposurePolicy(dynamic_enabled=True)
    names = policy.initial_tools(_context())
    assert names == ["read_paper"]
    assert not set(names) & {
        "preview_paper_changes", "confirm_paper_changes", "operate_paper_version",
        "prepare_generation_plan",
    }


def test_generation_keeps_environment_inspection_and_preview_chain():
    policy = ToolExposurePolicy(dynamic_enabled=True)
    names = policy.initial_tools(_context(
        task_type=TaskType.DIRECT_ACTION,
        message="第一章生成10题100分测试卷",
        has_current_paper=False,
    ))
    assert names == ["prepare_generation_plan", *ENVIRONMENT]
    assert {"inspect_curriculum", "inspect_question_bank", "prepare_generation_plan"} <= set(names)


def test_pending_generation_modify_and_confirm_surfaces_are_lifecycle_safe():
    policy = ToolExposurePolicy(dynamic_enabled=True)
    modified = policy.initial_tools(_context(
        task_type=TaskType.DIRECT_ACTION,
        message="总分改成120分",
        has_current_paper=False,
        pending_generation=True,
    ))
    confirmed = policy.initial_tools(_context(
        task_type=TaskType.DIRECT_ACTION,
        message="确认，就按这个生成",
        has_current_paper=False,
        pending_generation=True,
    ))
    assert modified == [
        "prepare_generation_plan", "confirm_generation", "discard_pending_plan",
    ]
    assert confirmed == ["confirm_generation"]


def test_paper_change_keeps_read_then_preview_without_confirm():
    policy = ToolExposurePolicy(dynamic_enabled=True)
    names = policy.initial_tools(_context(
        task_type=TaskType.DIRECT_ACTION,
        message="把第3题换成一道更难的题",
    ))
    assert names == ["read_paper", "preview_paper_changes"]
    assert "confirm_paper_changes" not in names


def test_pending_paper_change_confirmation_exposes_only_confirm():
    names = ToolExposurePolicy(dynamic_enabled=True).initial_tools(_context(
        task_type=TaskType.DIRECT_ACTION,
        message="确认，就按这个",
        pending_paper_change=True,
    ))
    assert names == ["confirm_paper_changes"]


def test_task_chain_keeps_later_generation_tools_available_to_future_rounds():
    policy = ToolExposurePolicy(dynamic_enabled=True)
    context = _context(
        task_type=TaskType.DIRECT_ACTION,
        message="第一章生成10题100分测试卷",
        has_current_paper=False,
    )
    policy.initial_tools(context)
    chain = task_chain_tools(policy.last_selection, context.tool_names)
    assert {
        "inspect_curriculum", "inspect_question_bank", "prepare_generation_plan",
        "confirm_generation", "discard_pending_plan",
    } <= set(chain)


def test_mixed_task_uses_bounded_fallback_without_confirmation_tools():
    policy = ToolExposurePolicy(dynamic_enabled=True)
    names = policy.initial_tools(_context(
        task_type=TaskType.DIRECT_ACTION,
        message="删除第2题并生成一套新的练习卷",
    ))
    assert policy.last_selection.fallback is True
    assert {"read_paper", "preview_paper_changes", "prepare_generation_plan"} <= set(names)
    assert "confirm_generation" not in names
    assert "confirm_paper_changes" not in names
    assert "create_teaching_design" not in names


def test_uncertain_route_keeps_phase1_safe_surface():
    context = _context(route_confidence=0.5, clarification_needed=True)
    phase1 = ToolExposurePolicy(dynamic_enabled=False).initial_tools(context)
    policy = ToolExposurePolicy(dynamic_enabled=True)
    assert policy.initial_tools(context) == phase1
    assert policy.last_selection.fallback is True


def test_feature_flag_defaults_off_and_preserves_phase1(monkeypatch):
    monkeypatch.delenv("AGENT_DYNAMIC_TOOL_SELECTION_ENABLED", raising=False)
    context = _context()
    assert ToolExposurePolicy().initial_tools(context) == ToolExposurePolicy(
        dynamic_enabled=False
    ).initial_tools(context)


def test_feature_flag_can_enable_selection(monkeypatch):
    monkeypatch.setenv("AGENT_DYNAMIC_TOOL_SELECTION_ENABLED", "true")
    assert ToolExposurePolicy().initial_tools(_context()) == ["read_paper"]


def test_model_call_to_unexposed_tool_is_still_rejected(session, monkeypatch):
    class UnauthorizedBackend:
        def complete(self, _messages, _tools):
            return {"message": {"tool_calls": [{
                "id": "unauthorized",
                "function": {"name": "create_teaching_design", "arguments": {}},
            }]}}

    monkeypatch.setenv("AGENT_DYNAMIC_TOOL_SELECTION_ENABLED", "true")
    result = run_teacher_agent(
        session,
        "第一章生成10题100分测试卷",
        conversation_id="dynamic-tool-unauthorized",
        backend=UnauthorizedBackend(),
    )
    assert result.status == "failed"
    assert result.blocking_errors == ["tool_not_exposed"]


def test_schema_comparison_does_not_change_selected_parameter_schema():
    with Session(create_engine("sqlite:///:memory:")) as session:
        tools = build_agent_tools(AgentExecutionContext(
            session=session,
            conversation_id=None,
            paper_id=None,
            version_id=None,
            state_store=None,
        ))
        toolkit = Toolkit(tools.values())
        policy = ToolExposurePolicy(dynamic_enabled=True)
        context = _context()
        phase1 = ToolExposurePolicy(dynamic_enabled=False).initial_tools(context)
        selected = policy.initial_tools(context)
        comparison = compare_tool_exposure(toolkit, phase1, policy.last_selection)

        assert comparison["schema_chars_reduction"] > 0
        assert toolkit.schema(selected[0])["function"]["parameters"] == tools[
            selected[0]
        ].definition()["function"]["parameters"]
        assert json.dumps(toolkit.schema(selected[0]), sort_keys=True) == json.dumps(
            tools[selected[0]].definition(), sort_keys=True
        )
