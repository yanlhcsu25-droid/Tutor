from calculus_agent.agent.task_router import TaskType
from calculus_agent.runtime.tool_exposure_policy import ToolExposureContext, ToolExposurePolicy


ALL_TOOLS = frozenset({
    "read_paper", "analyze_paper", "preview_paper_changes", "confirm_paper_changes",
    "discard_pending_plan", "operate_paper_version", "prepare_generation_plan",
    "confirm_generation", "prepare_reinforcement_plan", "create_teaching_design",
    "retrieve_curriculum_candidates", "select_teaching_scope", "prepare_teaching_planning_draft",
})


def _context(**changes):
    values = dict(
        tool_names=ALL_TOOLS, task_type=TaskType.DIRECT_ACTION, message="查看当前试卷",
        has_current_paper=False, pending_generation=False, pending_paper_change=False,
        pending_replacement=False, legacy_teaching_design_active=False,
        design_tool_names=(), environment_tool_names=(), pending_teaching_design_action=None,
        teaching_design_artifact_requested=False, has_explicit_curriculum_scope=False,
    )
    values.update(changes)
    return ToolExposureContext(**values)


def test_pending_generation_has_only_its_lifecycle_tools():
    names = ToolExposurePolicy().initial_tools(_context(pending_generation=True))
    assert names == ["prepare_generation_plan", "confirm_generation", "discard_pending_plan"]


def test_existing_paper_defaults_to_lifecycle_surface():
    names = ToolExposurePolicy().initial_tools(_context(has_current_paper=True))
    assert names == ["read_paper", "analyze_paper", "preview_paper_changes", "confirm_paper_changes", "discard_pending_plan", "operate_paper_version"]


def test_existing_paper_direct_generation_keeps_generation_surface():
    names = ToolExposurePolicy().initial_tools(_context(
        has_current_paper=True, message="再生成一张练习卷",
    ))
    assert names == ["read_paper", "analyze_paper", "prepare_generation_plan", "prepare_reinforcement_plan", "discard_pending_plan"]


def test_boundary_surfaces_are_policy_owned():
    policy = ToolExposurePolicy()
    context = _context(has_current_paper=True)
    assert policy.boundary_tools("grounding_read", context=context) == ["read_paper"]
    assert policy.boundary_tools("paper_change_preview", context=context) == ["preview_paper_changes"]
