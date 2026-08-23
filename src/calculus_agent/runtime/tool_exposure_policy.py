"""Deterministic model-visible Tool surface policy."""

from dataclasses import dataclass
from typing import Iterable

from calculus_agent.agent.paper_tool_registry import PAPER_TOOL_NAMES
from calculus_agent.agent.task_router import TaskType, tool_surface_for


_TEACHING_DESIGN_TOOLS = frozenset({
    "read_active_teaching_design", "create_teaching_design", "revise_teaching_design",
    "confirm_teaching_design", "discard_teaching_design", "search_teaching_design_history",
    "activate_teaching_design",
})
_PENDING_GENERATION_TOOLS = ("prepare_generation_plan", "confirm_generation", "discard_pending_plan")
_PENDING_PAPER_CHANGE_TOOLS = ("read_paper", "analyze_paper", "preview_paper_changes", "confirm_paper_changes", "discard_pending_plan")
_EXISTING_PAPER_GENERATION_TOOLS = ("read_paper", "analyze_paper", "prepare_generation_plan", "prepare_reinforcement_plan", "discard_pending_plan")
_EXISTING_PAPER_LIFECYCLE_TOOLS = ("read_paper", "analyze_paper", "preview_paper_changes", "confirm_paper_changes", "discard_pending_plan", "operate_paper_version")


@dataclass(frozen=True)
class ToolExposureContext:
    tool_names: frozenset[str]
    task_type: TaskType
    message: str
    has_current_paper: bool
    pending_generation: bool
    pending_paper_change: bool
    pending_replacement: bool
    legacy_teaching_design_active: bool
    design_tool_names: tuple[str, ...]
    environment_tool_names: tuple[str, ...]
    pending_teaching_design_action: str | None
    teaching_design_artifact_requested: bool
    has_explicit_curriculum_scope: bool


class ToolExposurePolicy:
    """The single authority for which Tool names may be shown to the model."""

    @staticmethod
    def _available(names: Iterable[str], context: ToolExposureContext) -> list[str]:
        return list(dict.fromkeys(name for name in names if name in context.tool_names))

    def initial_tools(self, context: ToolExposureContext) -> list[str]:
        if not context.has_current_paper:
            names = ["prepare_generation_plan", *context.environment_tool_names, *context.design_tool_names]
            if context.task_type == TaskType.DIRECT_ACTION:
                names.extend((
                    "read_paper", "analyze_paper", "preview_paper_changes",
                    "operate_paper_version",
                ))
            if context.task_type in {TaskType.TEACHING_DESIGN, TaskType.TEACHING_PLANNING}:
                names.append("create_teaching_design")
            if context.pending_generation:
                names.extend(("confirm_generation", "discard_pending_plan"))
            if context.pending_replacement:
                names.append("discard_pending_plan")
        elif context.pending_replacement or context.pending_generation or context.pending_paper_change:
            names = list(PAPER_TOOL_NAMES)
        else:
            names = list(context.tool_names)

        names = [
            name for name in names
            if name not in _TEACHING_DESIGN_TOOLS
            or (context.legacy_teaching_design_active and name in context.design_tool_names)
            or (
                context.task_type in {TaskType.TEACHING_DESIGN, TaskType.TEACHING_PLANNING}
                and name == "create_teaching_design"
            )
        ]
        if context.pending_teaching_design_action is not None:
            allowed = {
                "confirm": {"confirm_teaching_design"}, "revise": {"revise_teaching_design"},
                "query": {"read_active_teaching_design"}, "cancel": {"discard_teaching_design"},
                "ambiguous": set(),
            }[context.pending_teaching_design_action]
            names = [name for name in names if name in allowed]

        strong_state = any((context.pending_generation, context.pending_paper_change,
                            context.pending_replacement, context.has_current_paper,
                            context.legacy_teaching_design_active))
        if (not strong_state and context.task_type == TaskType.TEACHING_DESIGN
                and (context.teaching_design_artifact_requested or ("设计" in context.message and "复习方案" in context.message))
                and context.has_explicit_curriculum_scope):
            names = [
                "inspect_curriculum", "inspect_question_bank",
                "create_teaching_design",
            ]
        elif not strong_state and context.task_type in {
            TaskType.TEACHING_DESIGN, TaskType.TEACHING_PLANNING,
            TaskType.INFORMATION_REQUEST,
        }:
            allowed = set(tool_surface_for(context.task_type).allowed_tools)
            names = [name for name in names if name in allowed]
            if context.task_type in {
                TaskType.TEACHING_DESIGN, TaskType.TEACHING_PLANNING,
            } and not context.has_explicit_curriculum_scope:
                names = ["retrieve_curriculum_candidates", "select_teaching_scope", *([] if context.teaching_design_artifact_requested else ["prepare_teaching_planning_draft"])]

        if context.pending_generation:
            names = list(_PENDING_GENERATION_TOOLS)
        elif context.pending_paper_change or context.pending_replacement:
            names = list(_PENDING_PAPER_CHANGE_TOOLS)
        elif context.has_current_paper and not context.legacy_teaching_design_active and set(names) != {"create_teaching_design"}:
            generation_words = ("出", "生成", "组卷", "测试卷", "练习卷", "测验", "考试")
            names = list(_EXISTING_PAPER_GENERATION_TOOLS if context.task_type == TaskType.DIRECT_ACTION and any(word in context.message for word in generation_words) else _EXISTING_PAPER_LIFECYCLE_TOOLS)
        return self._available(names, context)

    def is_teaching_design_tool(self, name: str) -> bool:
        return name in _TEACHING_DESIGN_TOOLS

    def teaching_scope_tools(self, context: ToolExposureContext) -> list[str]:
        names = list(tool_surface_for(TaskType.TEACHING_PLANNING).allowed_tools)
        if context.teaching_design_artifact_requested:
            names = [name for name in names if name != "prepare_teaching_planning_draft"]
        return self._available(names, context)

    def post_inspection_tools(self, context: ToolExposureContext) -> list[str]:
        names = ["prepare_generation_plan", *context.environment_tool_names, *context.design_tool_names]
        if context.task_type in {TaskType.TEACHING_DESIGN, TaskType.TEACHING_PLANNING}:
            names.append("create_teaching_design")
        if context.task_type in {
            TaskType.TEACHING_DESIGN, TaskType.TEACHING_PLANNING,
            TaskType.INFORMATION_REQUEST,
        }:
            allowed = set(tool_surface_for(context.task_type).allowed_tools)
            names = [name for name in names if name in allowed]
        return self._available(names, context)

    def boundary_tools(self, boundary: str, *, context: ToolExposureContext, post_inspection_names: Iterable[str] = ()) -> list[str]:
        names = {
            "grounding_read": ("read_paper",),
            "pending_paper_change": _PENDING_PAPER_CHANGE_TOOLS,
            "paper_change_preview": ("preview_paper_changes",),
            "generation_patch": ("prepare_generation_plan",),
            "post_inspection": tuple(post_inspection_names),
            "refreshed_teaching_scope": tuple(post_inspection_names),
        }[boundary]
        return self._available(names, context)
