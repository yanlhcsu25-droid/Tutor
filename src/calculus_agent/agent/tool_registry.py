"""Shared Tool contracts, aggregation, and validated execution boundary."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Literal

from pydantic import BaseModel, ConfigDict, ValidationError
from sqlalchemy.orm import Session

from .conversation_state import DatabasePendingReplacementStore


class EmptyInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


@dataclass
class AgentExecutionContext:
    session: Session
    conversation_id: str | None
    paper_id: str | None
    version_id: str | None
    state_store: DatabasePendingReplacementStore | None
    expected_pending_generation_version: int | None = None
    owner_key: str = "local_teacher"
    run_id: str | None = None
    user_message: str = ""
    observed_evidence: dict[str, dict[str, Any]] = field(default_factory=dict)
    inspection_state: dict[str, Any] = field(default_factory=dict)
    inspection_call_count: int = 0


@dataclass
class ExecutedTool:
    payload: dict[str, Any]
    status: Literal[
        "completed",
        "needs_clarification",
        "waiting_confirmation",
        "failed",
    ]
    result_fields: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AgentTool:
    name: str
    description: str
    input_model: type[BaseModel]
    execute: Callable[[BaseModel], ExecutedTool]

    def definition(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.input_model.model_json_schema(),
            },
        }


def build_agent_tools(context: AgentExecutionContext) -> dict[str, AgentTool]:
    """Aggregate domain tool sets; domain implementations live elsewhere."""
    # Lazy imports avoid cycles because TeachingDesign/environment adapters
    # currently import AgentTool/ExecutedTool from this stable boundary.
    from .paper_tool_registry import build_paper_tools
    from .tool_adapters.teaching_environment import build_environment_inspection_tools
    from .tool_adapters.teaching_design import build_teaching_design_tools

    tools = dict(build_paper_tools(context))
    for tool in build_environment_inspection_tools(context):
        if tool.name in tools:
            raise ValueError(f"duplicate_tool_name:{tool.name}")
        tools[tool.name] = tool
    for tool in build_teaching_design_tools(context):
        if tool.name in tools:
            raise ValueError(f"duplicate_tool_name:{tool.name}")
        tools[tool.name] = tool
    return tools


def execute_tool(tool: AgentTool, arguments: Any) -> ExecutedTool:
    """Validate every model-provided argument before deterministic execution."""
    try:
        validated = tool.input_model.model_validate(arguments or {})
    except ValidationError as exc:
        return ExecutedTool(
            payload={
                "ok": False,
                "code": "invalid_tool_arguments",
                "message": "工具参数未通过校验。",
                "details": exc.errors(include_url=False),
            },
            status="failed",
            result_fields={"blocking_errors": ["invalid_tool_arguments"]},
        )
    return tool.execute(validated)
