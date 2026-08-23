"""Preparation of one model turn, independent from the runtime loop."""

from dataclasses import dataclass
from typing import Any

from calculus_agent.agent.context_metrics import measure_context


@dataclass(frozen=True)
class ModelTurnPreparation:
    active_skills: tuple[str, ...]
    context_metrics: Any
    span_input: dict[str, Any]


def prepare_model_turn(
    *,
    messages: list[dict],
    definitions: list[dict],
    serialized_context: str,
    recent_messages: list[dict[str, str]],
    context_builder: Any,
    dynamic_context: dict[str, Any],
    teaching_design_skill_active: bool,
    question_operation_skill_active: bool,
    teaching_design_skill_name: str,
    question_operation_skill_name: str,
    tool_round: int,
) -> ModelTurnPreparation:
    """Build observability input for a model call without executing it."""
    active_skills: list[str] = []
    if teaching_design_skill_active:
        active_skills.append(teaching_design_skill_name)
    if question_operation_skill_active:
        active_skills.append(question_operation_skill_name)
    metrics = measure_context(
        messages=messages,
        tool_definitions=definitions,
        serialized_context=serialized_context,
        conversation_history=recent_messages,
        workspace_context=context_builder.project_workspace(dynamic_context),
    )
    return ModelTurnPreparation(
        active_skills=tuple(active_skills),
        context_metrics=metrics,
        span_input={
            "n_messages": len(messages),
            "n_definitions": len(definitions),
            "active_skills": active_skills,
            "context_metrics": metrics.as_dict(),
            "tool_round": tool_round,
        },
    )
